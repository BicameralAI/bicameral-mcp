"""Pre-work runner: the command a host hook invokes at a session boundary.

Responsibilities, in order:

1. Parse the host event into an allowlisted view (no transcript/secrets).
2. Fire only at a genuine pre-work boundary (new session), never mid-session.
3. Deduplicate: invoke ``bicameral.preflight`` exactly once per task boundary.
4. Perform a daemon capability/protocol handshake before invoking.
5. On any failure, unsupported capability, daemon unavailability, or protocol
   mismatch, surface a visible message pointing at explicit/manual preflight and
   never claim preflight ran.

The runner never renders, chooses, promotes, or confirms candidates and never
writes canonical Decision state. That authority is the daemon's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from authority import build_authority_context
from daemon_client import (
    DaemonCapabilityError,
    DaemonClient,
    DaemonClientError,
    DaemonProtocolError,
)
from tool_request import build_tool_request
from version import TOOLREQUEST_PROTOCOL_VERSION

from .base import HostAdapter
from .context import PreworkContext
from .registry import get_adapter
from .state import AdapterState

PREFLIGHT_COMMAND = "preflight.run"

MANUAL_FALLBACK = (
    "Automatic pre-work preflight did not run. Run bicameral.preflight "
    "explicitly (or the 'preflight' MCP prompt) to get constraint/readiness "
    "context before you start."
)


class PreworkOutcome(StrEnum):
    INVOKED = "invoked"
    SKIPPED_NOT_PREWORK = "skipped_not_prework"
    SKIPPED_ALREADY_FIRED = "skipped_already_fired"
    SKIPPED_DISABLED = "skipped_disabled"
    FALLBACK_DAEMON_UNAVAILABLE = "fallback_daemon_unavailable"
    FALLBACK_PROTOCOL_MISMATCH = "fallback_protocol_mismatch"
    FALLBACK_CAPABILITY_UNSUPPORTED = "fallback_capability_unsupported"
    FALLBACK_ERROR = "fallback_error"


_FALLBACK_OUTCOMES = frozenset(
    {
        PreworkOutcome.FALLBACK_DAEMON_UNAVAILABLE,
        PreworkOutcome.FALLBACK_PROTOCOL_MISMATCH,
        PreworkOutcome.FALLBACK_CAPABILITY_UNSUPPORTED,
        PreworkOutcome.FALLBACK_ERROR,
    }
)


@dataclass(frozen=True)
class PreworkResult:
    outcome: PreworkOutcome
    host: str
    message: str
    correlation_id: str | None = None
    preflight_invoked: bool = False
    forwarded_context: str | None = None
    daemon_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_fallback(self) -> bool:
        return self.outcome in _FALLBACK_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "host": self.host,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "preflight_invoked": self.preflight_invoked,
            "forwarded_context": self.forwarded_context,
            "daemon_status": self.daemon_status,
            "details": self.details,
        }


class _ClientLike(Protocol):
    async def capabilities(self) -> dict[str, Any]: ...

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]: ...


def _default_client_factory() -> _ClientLike:
    return DaemonClient.from_env()


async def run_prework(
    host_id: str,
    payload: dict[str, Any],
    *,
    home: Path | None = None,
    client_factory: Any = None,
) -> PreworkResult:
    adapter = get_adapter(host_id, home=home)
    event = adapter.parse_event(payload)

    status = adapter.status()
    if status.state is not AdapterState.INSTALLED_ENABLED:
        return PreworkResult(
            outcome=PreworkOutcome.SKIPPED_DISABLED,
            host=host_id,
            message=(
                f"{adapter.display_name} pre-work adapter is not enabled; "
                "skipping automatic preflight."
            ),
        )

    if not adapter.is_prework_boundary(event):
        return PreworkResult(
            outcome=PreworkOutcome.SKIPPED_NOT_PREWORK,
            host=host_id,
            message=(
                f"Event source {event.source!r} is not a pre-work boundary; "
                "no automatic preflight (pre-work only)."
            ),
        )

    context = adapter.build_context(event)
    correlation_id = context.correlation_id
    store = adapter._store()
    if store.has_fired(correlation_id):
        return PreworkResult(
            outcome=PreworkOutcome.SKIPPED_ALREADY_FIRED,
            host=host_id,
            correlation_id=correlation_id,
            message=(
                "Pre-work preflight already ran for this task boundary "
                f"({correlation_id}); skipping to keep exactly-once semantics."
            ),
        )

    factory = client_factory or _default_client_factory
    client = factory()

    handshake = await _handshake(client)
    if handshake is not None:
        return PreworkResult(
            outcome=handshake[0],
            host=host_id,
            correlation_id=correlation_id,
            message=f"{handshake[1]} {MANUAL_FALLBACK}",
            details=handshake[2],
        )

    try:
        response = await _invoke_preflight(client, adapter, context)
    except DaemonCapabilityError as exc:
        return PreworkResult(
            outcome=PreworkOutcome.FALLBACK_CAPABILITY_UNSUPPORTED,
            host=host_id,
            correlation_id=correlation_id,
            message=(f"Daemon does not support the preflight command ({exc}). {MANUAL_FALLBACK}"),
        )
    except DaemonProtocolError as exc:
        return PreworkResult(
            outcome=PreworkOutcome.FALLBACK_PROTOCOL_MISMATCH,
            host=host_id,
            correlation_id=correlation_id,
            message=f"Daemon protocol mismatch ({exc}). {MANUAL_FALLBACK}",
        )
    except DaemonClientError as exc:
        return PreworkResult(
            outcome=PreworkOutcome.FALLBACK_DAEMON_UNAVAILABLE,
            host=host_id,
            correlation_id=correlation_id,
            message=f"Daemon unavailable ({exc}). {MANUAL_FALLBACK}",
        )

    daemon_status = str(response.get("status")) if isinstance(response, dict) else None
    store.mark_fired(
        correlation_id,
        {
            "task_boundary": context.task_boundary,
            "daemon_status": daemon_status,
            "forwarded_context": context.describe(),
        },
    )
    return PreworkResult(
        outcome=PreworkOutcome.INVOKED,
        host=host_id,
        correlation_id=correlation_id,
        preflight_invoked=True,
        forwarded_context=context.describe(),
        daemon_status=daemon_status,
        message=(
            f"Invoked bicameral.preflight once for {adapter.display_name} "
            f"pre-work boundary with bounded context ({context.describe()})."
        ),
    )


async def _handshake(
    client: _ClientLike,
) -> tuple[PreworkOutcome, str, dict[str, Any]] | None:
    """Return ``None`` when compatible, else a fallback ``(outcome, msg, details)``."""
    try:
        capabilities = await client.capabilities()
    except DaemonClientError as exc:
        return (
            PreworkOutcome.FALLBACK_DAEMON_UNAVAILABLE,
            f"Daemon unavailable during capability handshake ({exc}).",
            {},
        )
    protocol_version = capabilities.get("toolrequest_protocol_version") or capabilities.get(
        "protocol_version"
    )
    if protocol_version != TOOLREQUEST_PROTOCOL_VERSION:
        return (
            PreworkOutcome.FALLBACK_PROTOCOL_MISMATCH,
            (
                "Daemon ToolRequest protocol "
                f"{protocol_version!r} is incompatible with MCP "
                f"{TOOLREQUEST_PROTOCOL_VERSION!r}."
            ),
            {
                "daemon_protocol_version": protocol_version,
                "mcp_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            },
        )
    supported = tuple(capabilities.get("supported_commands", []))
    deferred = tuple(capabilities.get("deferred_commands", []))
    if PREFLIGHT_COMMAND in deferred or PREFLIGHT_COMMAND not in supported:
        return (
            PreworkOutcome.FALLBACK_CAPABILITY_UNSUPPORTED,
            f"Daemon does not advertise the {PREFLIGHT_COMMAND} command.",
            {"supported_commands": list(supported), "deferred_commands": list(deferred)},
        )
    return None


async def _invoke_preflight(
    client: _ClientLike,
    adapter: HostAdapter,
    context: PreworkContext,
) -> dict[str, Any]:
    params = context.to_preflight_arguments()
    authority = build_authority_context(
        "bicameral.preflight",
        {"workspace": context.workspace} if context.workspace else {},
    )
    # Correlation/idempotency metadata rides in the audit metadata so the daemon
    # can join the operational witness without changing the command contract.
    audit_metadata = authority.get("audit_metadata")
    if isinstance(audit_metadata, dict):
        audit_metadata.update(context.audit_metadata())
    tool_request = build_tool_request(
        command_name=PREFLIGHT_COMMAND,
        params=params,
        authority=authority,
    )
    return await client.send_tool_request(tool_request)
