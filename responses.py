"""ToolResponse formatting for MCP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp.types import TextContent

from daemon_client import DaemonClientError, resolve_daemon_endpoint
from version import TOOLREQUEST_PROTOCOL_VERSION

PREFLIGHT_STAGES = ("capture", "projection", "lookup", "enforcement")

# Static operator guidance per typed handshake failure. MCP stays fail-fast and
# informational only: it never starts, installs, upgrades, migrates, or repairs
# the daemon, and never falls back to legacy MCP-owned handlers (mcp#583).
RECOVERY_GUIDANCE: dict[str, dict[str, Any]] = {
    "daemon_unavailable": {
        "category": "setup",
        "retryable": True,
        "operator_action": ("Start or install the Bicameral bot daemon, then retry."),
    },
    "daemon_protocol_mismatch": {
        "category": "setup",
        "retryable": False,
        "operator_action": (
            "Upgrade bicameral-mcp and bicameral-bot/daemon to matching tags, then retry."
        ),
    },
    "daemon_capability_error": {
        "category": "capability",
        "retryable": False,
        "operator_action": (
            "Use a supported command, or upgrade to a daemon tag that advertises this capability."
        ),
    },
    "daemon_error": {
        "category": "setup",
        "retryable": False,
        "operator_action": ("Inspect the bicameral-bot daemon logs, then retry."),
    },
}


def format_tool_response(response: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(response, indent=2, sort_keys=True))


def format_recall_packet(response: dict[str, Any]) -> TextContent:
    """Render a daemon-authored RecallPacket without strengthening claims.

    The RecallPacket is a daemon-owned evidence lookup result.  MCP renders it
    faithfully: searched scope, unknown scope, matches (with evidence refs and
    freshness/readiness labels), and allowed next actions.

    Rendering rules (mcp#638):
    - No-match output states the lookup found no relevant items *only within
      the searched scope* — it never infers no-conflict, compliance, safety,
      or global completeness from narrow scope.
    - Unknown scope is never hidden or summarized away.
    - Stale / source_only / candidate labels remain visible.
    - Expand-scope affordances are forwarded when present.
    """
    recall: dict[str, Any] = response.get("recall_packet", {})

    searched_scope = recall.get("searched_scope", [])
    unknown_scope = recall.get("unknown_scope", [])
    matches = recall.get("matches", [])
    allowed_next_actions = recall.get("allowed_next_actions", [])

    rendered_matches: list[dict[str, Any]] = []
    for match in matches:
        rendered: dict[str, Any] = {
            "kind": match.get("kind"),
            "id": match.get("id"),
            "title": match.get("title"),
        }
        if match.get("evidence_refs"):
            rendered["evidence_refs"] = match["evidence_refs"]
        if match.get("freshness"):
            rendered["freshness"] = match["freshness"]
        if match.get("readiness"):
            rendered["readiness"] = match["readiness"]
        if match.get("source_link"):
            rendered["source_link"] = match["source_link"]
        if match.get("excerpt"):
            rendered["excerpt"] = match["excerpt"]
        rendered_matches.append(rendered)

    no_match_note: str | None = None
    if not matches:
        scope_desc = ", ".join(searched_scope) if searched_scope else "requested scope"
        no_match_note = (
            f"Lookup found no relevant items within searched scope: {scope_desc}. "
            "This does not imply absence outside searched scope."
        )

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "searched_scope": searched_scope,
        "unknown_scope": unknown_scope,
        "matches": rendered_matches,
    }

    if no_match_note:
        mcp_output["no_match_note"] = no_match_note

    if allowed_next_actions:
        mcp_output["allowed_next_actions"] = allowed_next_actions

    expand_scope = recall.get("expand_scope")
    if expand_scope:
        mcp_output["expand_scope"] = expand_scope

    mcp_output["responded_at"] = response.get("responded_at", _now())

    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_preflight_no_fire(*, files: list[str], request_id: str) -> TextContent:
    """Render a preflight no-fire decision for un-ingested file scope.

    Returned when the coverage guard determines that none of the requested
    files have ledger/code_region bindings.  All stages are reported as
    ``skipped`` with a clear reason so the agent understands why the full
    preflight pipeline was not executed.
    """
    stages: dict[str, Any] = {}
    for stage_name in PREFLIGHT_STAGES:
        stages[stage_name] = {
            "status": "skipped",
            "reason": "no_coverage",
        }

    mcp_output: dict[str, Any] = {
        "status": "no_fire",
        "request_id": request_id,
        "reason": "coverage_guard",
        "detail": (
            "All requested files have zero ledger/code_region coverage. "
            "Preflight skipped — no binding evidence exists for this scope."
        ),
        "stages": stages,
        "session_directive": {"mode": "continue"},
        "guarded_files": files,
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_preflight_response(response: dict[str, Any]) -> TextContent:
    """Render a preflight daemon response with explicit staged section.

    Extracts the ``staged`` key added by bot#323 and surfaces each stage
    status at the top level of the MCP output.  Stages missing from the
    daemon payload are rendered as ``unsupported``.  ``enforcement.status``
    of ``not_configured`` is never promoted to warn/pause/block behavior.
    ``session_directive`` is forwarded as-is from the daemon.
    """
    staged: dict[str, Any] = response.get("staged", {})
    stages: dict[str, Any] = {}

    for stage_name in PREFLIGHT_STAGES:
        stage_data = staged.get(stage_name)
        if stage_data is None:
            stages[stage_name] = {"status": "unsupported"}
        else:
            stages[stage_name] = stage_data

    enforcement = stages.get("enforcement", {})
    if enforcement.get("status") == "not_configured":
        enforcement["behavior"] = "none"

    session_directive = staged.get("session_directive", {"mode": "continue"})

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "stages": stages,
        "session_directive": session_directive,
        "result": {key: value for key, value in response.items() if key != "staged"},
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_lookup_response(response: dict[str, Any]) -> TextContent:
    """Render a daemon lookup response with explicit state surfaces.

    Surfaces the daemon-authored RecallPacket fields: searched sources,
    corpus version, matches, unknown scope, and allowed next actions.
    When the daemon returns a deferred or unsupported state, the response
    renders that state explicitly rather than hiding it.
    """
    status = response.get("status", "ok")
    recall_packet = response.get("recall_packet", {})

    mcp_output: dict[str, Any] = {
        "status": status,
        "request_id": response.get("request_id"),
        "recall_packet": {
            "searched_sources": recall_packet.get("searched_sources", []),
            "corpus_version": recall_packet.get("corpus_version"),
            "matches": recall_packet.get("matches", []),
            "unknown_scope": recall_packet.get("unknown_scope", []),
            "allowed_next_actions": recall_packet.get("allowed_next_actions", []),
        },
        "session_directive": response.get("session_directive", {"mode": "continue"}),
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_context_packet_response(response: dict[str, Any]) -> TextContent:
    """Render a daemon-authored relevance-time context packet.

    The packet is a lookup/read artifact. MCP preserves daemon-authored source
    distinction, freshness/readiness, rationale, risk, confidence, and required
    actions when present, but it does not compute relevance, infer completeness,
    or convert no-match/partial coverage into safety or compliance claims.
    """
    packet = response.get("context_packet") or response.get("recall_packet") or {}
    matches = packet.get("matches", [])
    searched_sources = packet.get("searched_sources") or packet.get("searched_scope", [])
    unknown_scope = packet.get("unknown_scope", [])

    rendered_matches: list[dict[str, Any]] = []
    for match in matches:
        rendered: dict[str, Any] = {
            "match_id": match.get("match_id") or match.get("id"),
            "kind": match.get("kind"),
            "title": match.get("title"),
            "summary": match.get("summary"),
            "authority": match.get("authority"),
            "evidence_refs": match.get("evidence_refs", []),
            "relevance_reasons": match.get("relevance_reasons", []),
            "freshness_state": match.get("freshness_state") or match.get("freshness"),
            "review_state": match.get("review_state") or match.get("readiness"),
            "risk": match.get("risk"),
            "confidence": match.get("confidence"),
            "rationale": match.get("rationale"),
            "required_actions": match.get("required_actions", []),
        }
        rendered_matches.append(
            {key: value for key, value in rendered.items() if value not in (None, [], {})}
        )

    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "context_packet": {
            "packet_id": packet.get("packet_id"),
            "request": packet.get("request", {}),
            "corpus": packet.get("corpus", {}),
            "searched_sources": searched_sources,
            "matches": rendered_matches,
            "unknown_scope": unknown_scope,
            "allowed_next_actions": packet.get("allowed_next_actions", []),
            "receipt_ref": packet.get("receipt_ref"),
        },
        "session_directive": response.get("session_directive", {"mode": "continue"}),
    }

    if not matches:
        output["context_packet"]["no_match_note"] = (
            "No relevant items were returned for the searched sources. "
            "This does not imply absence outside searched or configured scope."
        )

    return TextContent(type="text", text=json.dumps(output, indent=2, sort_keys=True))


def format_correction_response(response: dict[str, Any]) -> TextContent:
    """Render a daemon correction response.

    Surfaces the correction outcome exactly as returned by the daemon.
    MCP does not own the correction lifecycle — it only renders the result.
    """
    result = response.get("result", {})
    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "correction_id": result.get("correction_id"),
        "packet_id": result.get("packet_id"),
        "accepted": result.get("accepted"),
        "message": result.get("message"),
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def error_text(code: str, message: str) -> TextContent:
    payload = {
        "status": "error",
        "message": message,
        "error_code": code,
        "responded_at": _now(),
    }
    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))


def build_recovery_payload(
    *,
    error_code: str,
    requested_tool: str | None = None,
    requested_command: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a typed daemon handshake failure to a structured recovery payload.

    The payload is informational only. It surfaces a stable ``error_code``,
    the protocol versions involved, the daemon endpoint, the requested tool /
    ToolRequest command, and a concise ``operator_action``. When the daemon URL
    is set via an env override, the override is reported and called out in the
    action text so misconfiguration is obvious.
    """
    details = details or {}
    guidance = RECOVERY_GUIDANCE.get(error_code, RECOVERY_GUIDANCE["daemon_error"])
    endpoint = resolve_daemon_endpoint()

    operator_action = guidance["operator_action"]
    recovery: dict[str, Any] = {
        "error_code": error_code,
        "category": guidance["category"],
        "retryable": guidance["retryable"],
        "mcp_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
        "daemon_protocol_version": details.get("daemon_protocol_version"),
        "daemon_endpoint": details.get("daemon_endpoint") or endpoint.url,
        "requested_tool": requested_tool,
        "requested_command": requested_command,
    }

    if endpoint.override_env_var is not None:
        recovery["daemon_url_override"] = {
            "env_var": endpoint.override_env_var,
            "value": endpoint.override_value,
        }
        operator_action = (
            f"{operator_action} A custom daemon URL is set via "
            f"{endpoint.override_env_var} ({endpoint.override_value}); unset or "
            "correct it if the daemon is running elsewhere."
        )

    recovery["operator_action"] = operator_action
    return recovery


def recovery_error_text(
    exc: DaemonClientError,
    *,
    requested_tool: str | None = None,
    requested_command: str | None = None,
) -> TextContent:
    """Render a daemon handshake failure as a typed MCP error with recovery info."""
    recovery = build_recovery_payload(
        error_code=exc.code,
        requested_tool=requested_tool,
        requested_command=requested_command,
        details=exc.details,
    )
    payload = {
        "status": "error",
        "message": str(exc),
        "error_code": exc.code,
        "recovery": recovery,
        "responded_at": _now(),
    }
    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
