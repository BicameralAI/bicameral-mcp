"""MCP-assisted workspace binding proposal flow (mcp#702, bicameral-bot#731).

The MCP thin client may *propose* that the local bicameral-bot daemon bind an
already-registered project to the current local folder for code grounding and
preflight. It never binds silently and never persists a ``LocalWorkspaceBinding``
itself: it detects a candidate workspace root, asks the operator to confirm, and
on explicit confirmation dispatches the daemon-owned ``workspace.bind``
``ToolRequest``. The daemon validates and materializes the binding.

Authority boundary (bicameral-bot#731):

* The MCP cwd / workspace root is **candidate path evidence only**, never
  project identity. Identity comes from explicit registration (``project_id``).
* MCP builds a transient ``WorkspaceBindingProposal``; it does not create
  ``BindingEvidence`` or write any durable lifecycle state.
* The daemon owns validation, safety policy, and materialization.

The request/response/typed-error wire shapes mirror the contract in
``bicameral-bot`` at ``crates/bicameral-api/src/workspace_binding.rs`` and
``protocol/schemas/v2/workspace-*.json``.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from typing import Any

from mcp.types import TextContent

# Minimum daemon workspace-binding capability version MCP requires by default.
# Older daemons that do not advertise this fail closed with the typed
# ``daemon_capability_mismatch`` error rather than binding silently.
DEFAULT_REQUIRED_DAEMON_CAPABILITY = 1

_VALID_EXPECTED_STATES = frozenset(
    {
        "local_workspace_unbound",
        "local_workspace_bound",
        "local_workspace_repair_required",
    }
)

# Typed daemon error kind -> (operator-facing outcome label, actionable guidance).
# Every kind fails closed with concrete fallback guidance; MCP never coerces a
# typed error into success.
_ERROR_GUIDANCE: dict[str, tuple[str, str]] = {
    "already_bound": (
        "already_bound",
        "This project already has a local workspace binding. No action needed.",
    ),
    "confirmation_missing": (
        "confirmation_missing",
        "Re-invoke bicameral.workspace.bind with confirmed=true to bind this folder.",
    ),
    "unsafe_path": (
        "rejected",
        "The daemon rejected the candidate folder as unsafe. "
        "Choose a project source folder (not a system directory) and retry.",
    ),
    "wrong_project": (
        "rejected",
        "The proposal targets a different project than the daemon's session scope. "
        "Select the registered project for this folder and retry.",
    ),
    "unregistered_project": (
        "not_registered",
        "The target project is not registered/paired with the local daemon. "
        "Register or pair the project first, then retry the bind.",
    ),
    "daemon_capability_mismatch": (
        "unsupported",
        "The local bicameral-bot daemon is too old to support workspace binding. "
        "Upgrade the daemon to a build that advertises workspace.bind, then retry.",
    ),
    "repair_required": (
        "repair_required",
        "The existing workspace binding is broken and must be repaired before binding again.",
    ),
}


def detect_workspace_root(arguments: dict[str, Any]) -> str:
    """Resolve the candidate workspace root using existing MCP repo patterns.

    Precedence: explicit ``candidate_path`` / ``workspace`` argument, then the
    ``BICAMERAL_WORKSPACE`` / ``REPO_PATH`` env vars, then the process cwd. The
    result is candidate *path* evidence only — never project identity.
    """
    base = (
        arguments.get("candidate_path")
        or arguments.get("workspace")
        or os.environ.get("BICAMERAL_WORKSPACE")
        or os.environ.get("REPO_PATH")
        or os.getcwd()
    )
    toplevel = _git_toplevel(base)
    return os.path.abspath(toplevel or base)


def _git_toplevel(path: str) -> str:
    """Return the git top-level for *path*, or ``""`` when unavailable."""
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=path,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""


def build_binding_proposal(arguments: dict[str, Any]) -> dict[str, Any]:
    """Build a transient ``WorkspaceBindingProposal`` from MCP arguments.

    Raises ``ValueError`` when the required ``project_id`` identity is missing —
    the candidate folder is never used as a substitute for project identity.
    """
    project_id = arguments.get("project_id")
    if not project_id or not str(project_id).strip():
        raise ValueError(
            "project_id is required: MCP proposes a binding for an already-registered "
            "project and never derives project identity from the current folder."
        )

    candidate_path = detect_workspace_root(arguments)

    display: dict[str, Any] = {
        "display_name": arguments.get("display_name") or str(project_id),
    }
    if arguments.get("project_slug"):
        display["project_slug"] = str(arguments["project_slug"])
    candidate_label = arguments.get("candidate_label") or os.path.basename(
        candidate_path.rstrip(os.sep)
    )
    if candidate_label:
        display["candidate_label"] = candidate_label

    reason = (
        arguments.get("reason")
        or "The MCP session working directory matches the candidate folder for this project."
    )
    confidence = _clamp_confidence(arguments.get("confidence"))

    return {
        "project_id": str(project_id),
        "display": display,
        "candidate_path": candidate_path,
        "source_surface": "mcp",
        "reason": reason,
        "confidence": confidence,
    }


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value) if value is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    return max(0.0, min(1.0, confidence))


def build_workspace_bind_command_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Assemble ``workspace.bind`` command params (a ``WorkspaceBindRequest``)."""
    command_args: dict[str, Any] = {
        "proposal": build_binding_proposal(arguments),
        "confirmed": bool(arguments.get("confirmed", False)),
    }

    required_capability = arguments.get("required_daemon_capability")
    if required_capability is None:
        required_capability = DEFAULT_REQUIRED_DAEMON_CAPABILITY
    command_args["required_daemon_capability"] = int(required_capability)

    expected_state = arguments.get("expected_current_state")
    if expected_state in _VALID_EXPECTED_STATES:
        command_args["expected_current_state"] = expected_state

    return command_args


def format_confirmation_prompt(proposal: dict[str, Any]) -> TextContent:
    """Render the explicit confirmation prompt (no daemon dispatch happens).

    Taking no action cancels the flow; MCP never binds without confirmation.
    """
    display_name = proposal["display"]["display_name"]
    payload = {
        "status": "confirmation_required",
        "action": "workspace.bind",
        "prompt": (f"Bind this folder to project {display_name} for local code grounding?"),
        "project_id": proposal["project_id"],
        "candidate_path": proposal["candidate_path"],
        "proposal": proposal,
        "next_step": (
            "Re-invoke bicameral.workspace.bind with confirmed=true to bind, "
            "or take no action to cancel."
        ),
        "responded_at": _now(),
    }
    return _json_content(payload)


def format_workspace_bind_response(response: dict[str, Any]) -> TextContent:
    """Render a daemon ``workspace.bind`` outcome faithfully.

    The daemon carries the outcome in ``response["result"]``: a
    ``WorkspaceBindResponse`` on success or a ``WorkspaceBindErrorResponse``
    (distinguished by its ``error`` field) on a typed failure. MCP renders each
    of success, already-bound, rejected, unsupported, repair-required, and
    not-registered without strengthening or coercing the daemon's verdict.
    """
    result = response.get("result")
    if not isinstance(result, dict):
        result = {}

    if "error" in result:
        return _format_bind_error(result)
    return _format_bind_success(result)


def _format_bind_success(result: dict[str, Any]) -> TextContent:
    payload = {
        "status": "ok",
        "outcome": "bound",
        "project_id": result.get("project_id"),
        "state": result.get("state"),
        "display": result.get("display"),
        "message": result.get("message") or "Workspace binding materialized.",
        "responded_at": _now(),
    }
    return _json_content({key: value for key, value in payload.items() if value is not None})


def _format_bind_error(error: dict[str, Any]) -> TextContent:
    kind = str(error.get("error", "daemon_error"))
    outcome, guidance = _ERROR_GUIDANCE.get(
        kind, ("error", "Inspect the bicameral-bot daemon logs, then retry.")
    )
    payload = {
        "status": "error",
        "outcome": outcome,
        "error_kind": kind,
        "project_id": error.get("project_id"),
        "state": error.get("state"),
        "message": error.get("message"),
        "retry_after_repair": error.get("retry_after_repair"),
        "operator_action": guidance,
        "responded_at": _now(),
    }
    return _json_content({key: value for key, value in payload.items() if value is not None})


def _json_content(payload: dict[str, Any]) -> TextContent:
    import json

    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
