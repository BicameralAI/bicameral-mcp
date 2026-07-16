"""Bounded pre-work context for MCP-distributed host adapters.

A :class:`PreworkContext` is the *only* thing an adapter is allowed to forward
to the local daemon when a host pre-work checkpoint fires. It is a strict
allowlist: task boundary, workspace, branch, and optional file/symbol/diff
hints. Raw session transcripts, secrets, unrelated tool output, environment,
and background telemetry are never carried here.

This is MCP product automation. It is not a Decision lifecycle state and it is
not a compliance, safety, or merge-readiness signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Host event fields the adapters deliberately never read or forward. Kept as an
#: explicit, testable denylist so the privacy boundary is auditable rather than
#: implied by the allowlist alone.
FORBIDDEN_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "transcript_path",
        "transcript",
        "messages",
        "conversation",
        "prompt",
        "user_prompt",
        "tool_output",
        "tool_outputs",
        "command_output",
        "stdout",
        "stderr",
        "env",
        "environment",
        "secrets",
        "api_key",
        "token",
        "telemetry",
    }
)


@dataclass(frozen=True)
class PreworkContext:
    """Bounded work context sent with a single pre-work preflight invocation."""

    host: str
    #: Stable per-task-boundary id used for deduplication/idempotency. One
    #: pre-work invocation is made per correlation_id.
    correlation_id: str
    #: The genuine task boundary that triggered this context, e.g.
    #: ``session_start``. Never a mid-session or pre-write trigger.
    task_boundary: str
    workspace: str | None = None
    branch: str | None = None
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    diff_summary: str | None = None
    #: Records that no raw transcript was read from the host event. Operational
    #: evidence only.
    transcript_included: bool = False

    def to_preflight_arguments(self) -> dict[str, Any]:
        """Build bounded ``bicameral.preflight`` command params from context.

        ``checkpoint_hint`` is always ``pre_work``: these adapters exist only for
        the pre-work boundary. Only allowlisted, bounded fields are included;
        correlation/idempotency metadata travels in the request authority's
        ``audit_metadata`` (see :meth:`audit_metadata`), not in the command
        params, so the daemon command contract is unchanged.
        """
        arguments: dict[str, Any] = {"checkpoint_hint": "pre_work"}
        if self.files:
            arguments["files"] = list(self.files)
        if self.symbols:
            arguments["symbols"] = list(self.symbols)
        if self.diff_summary:
            arguments["diff_context"] = self.diff_summary
        if self.branch:
            arguments["branch"] = self.branch
        return arguments

    def audit_metadata(self) -> dict[str, Any]:
        """Correlation/idempotency metadata for the request authority context.

        The correlation id doubles as the idempotency key: one automatic
        pre-work invocation is made per task boundary.
        """
        return {
            "correlation_id": self.correlation_id,
            "idempotency_key": self.correlation_id,
            "checkpoint": self.task_boundary,
            "automation": "mcp_host_prework_adapter",
        }

    def describe(self) -> str:
        """One-line human summary of exactly what will be sent."""
        parts = [f"task_boundary={self.task_boundary}"]
        if self.workspace:
            parts.append(f"workspace={self.workspace}")
        if self.branch:
            parts.append(f"branch={self.branch}")
        if self.files:
            parts.append(f"files={len(self.files)}")
        if self.symbols:
            parts.append(f"symbols={len(self.symbols)}")
        if self.diff_summary:
            parts.append("diff_summary=present")
        return ", ".join(parts)


@dataclass(frozen=True)
class BoundedContextDescriptor:
    """Human-readable description of the bounded context an adapter may send.

    Rendered during consent so the operator gives *informed* consent about the
    exact category of data that can leave the host for the local daemon.
    """

    sent_fields: tuple[str, ...] = (
        "task boundary (e.g. session_start)",
        "workspace root path",
        "current git branch (when resolvable)",
        "changed file paths (when the host provides them)",
        "symbol names (when the host provides them)",
        "a bounded diff summary (when the host provides one)",
    )
    never_sent_fields: tuple[str, ...] = tuple(sorted(FORBIDDEN_EVENT_FIELDS))

    def render(self) -> str:
        lines = ["Bounded context that MAY be sent to the local Bicameral daemon:"]
        lines += [f"  + {item}" for item in self.sent_fields]
        lines.append("Never sent (raw transcripts, secrets, unrelated output, telemetry):")
        lines += [f"  - {item}" for item in self.never_sent_fields]
        return "\n".join(lines)


def assert_no_forbidden_fields(payload: dict[str, Any]) -> list[str]:
    """Return the forbidden keys present in *payload* (empty when clean).

    Used by the context builders to guarantee the allowlist is honored even if a
    host event carries transcript/secret-like fields: those keys are never read
    into a :class:`PreworkContext`, and this makes that guarantee testable.
    """
    return sorted(key for key in payload if key in FORBIDDEN_EVENT_FIELDS)
