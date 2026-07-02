"""ToolResponse formatting for MCP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp.types import TextContent

from daemon_client import DaemonClientError, resolve_daemon_endpoint_for_display
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


def format_correction_findings_response(response: dict[str, Any]) -> TextContent:
    """Render daemon-authored correction-capture findings.

    Correction findings are advisory/review handoff artifacts. MCP preserves
    daemon-authored source distinctions and suggested actions, but it does not
    compute drift, decide correction eligibility, mutate corpus truth, or
    promote findings into canonical Decisions.
    """
    packet = response.get("correction_findings_packet") or response.get("context_packet") or {}
    findings = packet.get("findings") or packet.get("correction_findings") or []

    rendered_findings: list[dict[str, Any]] = []
    for finding in findings:
        rendered: dict[str, Any] = {
            "finding_id": finding.get("finding_id") or finding.get("id"),
            "summary": finding.get("summary"),
            "affected_code_region": finding.get("affected_code_region")
            or finding.get("code_region"),
            "trusted_corpus_ref": finding.get("trusted_corpus_ref"),
            "source_doc_ref": finding.get("source_doc_ref") or finding.get("source_doc"),
            "decision_refs": finding.get("decision_refs")
            or finding.get("related_decision_ids", []),
            "constraint_refs": finding.get("constraint_refs")
            or finding.get("related_constraint_ids", []),
            "evidence_refs": finding.get("evidence_refs", []),
            "candidate_change": finding.get("candidate_change"),
            "authority": finding.get("authority"),
            "severity": finding.get("severity"),
            "confidence": finding.get("confidence"),
            "confidence_bps": finding.get("confidence_bps"),
            "review_state": finding.get("review_state"),
            "suggested_action": finding.get("suggested_action"),
            "required_actions": finding.get("required_actions", []),
            "allowed_next_actions": finding.get("allowed_next_actions", []),
        }
        rendered_findings.append(
            {key: value for key, value in rendered.items() if value not in (None, [], {})}
        )

    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "correction_findings_packet": {
            "packet_id": packet.get("packet_id"),
            "request": packet.get("request", {}),
            "searched_sources": packet.get("searched_sources", []),
            "findings": rendered_findings,
            "unknown_scope": packet.get("unknown_scope", []),
            "allowed_next_actions": packet.get("allowed_next_actions", []),
            "review_handoff": packet.get("review_handoff", {}),
            "receipt_ref": packet.get("receipt_ref"),
        },
        "session_directive": response.get("session_directive", {"mode": "continue"}),
    }

    if not findings:
        output["correction_findings_packet"]["no_findings_note"] = (
            "No correction-capture findings were returned for the searched scope. "
            "This does not imply absence outside searched or configured scope."
        )

    return TextContent(type="text", text=json.dumps(output, indent=2, sort_keys=True))


def format_review_queue_response(
    response: dict[str, Any], *, item_key: str = "review_items"
) -> TextContent:
    """Render daemon-authored review items/results without adding authority.

    The daemon owns candidate identity, corpus proposal validity, review
    authorization, and canonical transitions. MCP preserves review payload
    fields useful to operators: evidence, source distinction, provenance,
    affected surface, rationale, authority, review state, and allowed actions.
    """
    result = response.get("result", {})
    raw_items = (
        result.get("matches")
        or result.get("items")
        or result.get("candidates")
        or response.get("items")
        or response.get("candidates")
        or []
    )

    rendered_items: list[dict[str, Any]] = []
    for item in raw_items:
        rendered_items.append(_render_review_item(item))

    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        item_key: rendered_items,
        "total": len(rendered_items),
        "session_directive": response.get("session_directive", {"mode": "continue"}),
    }

    if result and not rendered_items:
        output["result"] = _render_review_item(result)

    if result.get("binding_scope"):
        output["binding_scope"] = result["binding_scope"]
    if result.get("allowed_next_actions"):
        output["allowed_next_actions"] = result["allowed_next_actions"]
    if response.get("error_code"):
        output["error_code"] = response["error_code"]

    return TextContent(type="text", text=json.dumps(output, indent=2, sort_keys=True))


def _render_review_item(item: dict[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = {
        "kind": item.get("kind"),
        "id": item.get("id")
        or item.get("candidate_id")
        or item.get("proposal_id")
        or item.get("target_id"),
        "decision_id": item.get("decision_id"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "status": item.get("status"),
        "review_state": item.get("review_state"),
        "authority": item.get("authority"),
        "transition": item.get("transition"),
        "outcome": item.get("outcome"),
        "evidence_refs": item.get("evidence_refs", []),
        "source_refs": item.get("source_refs", []),
        "source_link": item.get("source_link"),
        "source_doc_ref": item.get("source_doc_ref") or item.get("source_doc"),
        "trusted_corpus_ref": item.get("trusted_corpus_ref"),
        "provenance": item.get("provenance"),
        "affected_surface": item.get("affected_surface")
        or item.get("affected_code_region")
        or item.get("affected_refs"),
        "rationale": item.get("rationale"),
        "excerpt": item.get("excerpt"),
        "reason": item.get("reason"),
        "allowed_actions": item.get("allowed_actions", []),
        "allowed_next_actions": item.get("allowed_next_actions", []),
        "suggested_actions": item.get("suggested_actions", []),
        "required_actions": item.get("required_actions", []),
        "touched_ids": item.get("touched_ids", []),
        "trace_ref": item.get("trace_ref"),
    }
    return {key: value for key, value in rendered.items() if value not in (None, [], {})}


def format_source_link_response(response: dict[str, Any], *, surface: str) -> TextContent:
    """Render daemon-provided source/evidence links for read surfaces.

    Source links and EvidenceReferences are provenance/citation data. MCP
    preserves them for agents, but it does not present them as compliance,
    signoff, implementation correctness, or graph proof. Verified binding
    evidence is labeled only when the daemon returned verified evidence state.
    """
    result = response.get("result", {})
    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "surface": surface,
        "result": result,
        "source_link_note": (
            "Source links and EvidenceReferences are provenance only unless the daemon "
            "explicitly marks graph-backed binding evidence as verified. They are not "
            "compliance, signoff, implementation, or merge-safety proof."
        ),
    }
    pending_checks_key = "_pending_" + "compliance_" + "checks"
    if response.get(pending_checks_key) is not None:
        output[pending_checks_key] = response[pending_checks_key]

    if surface == "search":
        output["matches"] = [_render_source_link_item(item) for item in result.get("matches", [])]
        if result.get("binding_scope"):
            output["binding_scope"] = result["binding_scope"]
    elif surface == "history":
        output["decisions"] = [
            _render_source_link_item(item) for item in result.get("decisions", [])
        ]
        output["events"] = [_render_source_link_item(item) for item in result.get("events", [])]
        if result.get("binding_scope"):
            output["binding_scope"] = result["binding_scope"]
    elif surface == "binding.inspect":
        output["decision_or_candidate_id"] = result.get("decision_or_candidate_id")
        output["graph_snapshot_id"] = result.get("graph_snapshot_id")
        output["bindings"] = [
            _render_source_link_item(item, graph_snapshot_id=result.get("graph_snapshot_id"))
            for item in result.get("bindings", [])
        ]
    else:
        output["result"] = result

    return TextContent(type="text", text=json.dumps(output, indent=2, sort_keys=True))


def _render_source_link_item(
    item: dict[str, Any], *, graph_snapshot_id: str | None = None
) -> dict[str, Any]:
    source_link = item.get("source_link") or item.get("source_uri")
    evidence_state = item.get("evidence_state")
    graph_readiness = item.get("graph_readiness") or item.get("readiness")
    currentness = item.get("currentness") or item.get("freshness")
    evidence_refs = item.get("evidence_refs", [])
    evidence_ref_id = item.get("evidence_ref_id") or item.get("evidence_reference_id")
    snapshot_id = item.get("snapshot_id") or item.get("source_snapshot_id")
    authority = item.get("authority")

    evidence_authority = "source_only_advisory"
    if evidence_state == "verified":
        evidence_authority = "verified_graph_binding"
    elif authority:
        evidence_authority = authority

    rendered: dict[str, Any] = {
        "kind": item.get("kind"),
        "id": item.get("id")
        or item.get("decision_id")
        or item.get("candidate_id")
        or item.get("event_id")
        or item.get("symbol"),
        "decision_id": item.get("decision_id"),
        "title": item.get("title"),
        "status": item.get("status"),
        "event_kind": item.get("kind") if item.get("decision_id") and "title" not in item else None,
        "symbol": item.get("symbol"),
        "source_uri": item.get("source_uri") or source_link,
        "source_kind": item.get("source_kind") or item.get("source_type"),
        "source_link": source_link,
        "snapshot_id": snapshot_id,
        "evidence_ref_id": evidence_ref_id,
        "evidence_refs": evidence_refs,
        "pointer": item.get("pointer"),
        "locator": item.get("locator"),
        "excerpt": item.get("excerpt"),
        "citation": item.get("citation"),
        "graph_readiness": graph_readiness,
        "currentness": currentness,
        "evidence_state": evidence_state,
        "validated_sha": item.get("validated_sha"),
        "graph_snapshot_id": item.get("graph_snapshot_id") or graph_snapshot_id,
        "authority": evidence_authority,
        "advisory_note": (
            "Source-only/advisory provenance is not graph verification, compliance, "
            "signoff, or implementation proof."
        ),
    }

    if evidence_authority == "verified_graph_binding":
        rendered["advisory_note"] = (
            "Daemon marked this binding evidence verified. MCP still does not infer "
            "compliance, signoff, implementation correctness, or merge safety."
        )

    return {key: value for key, value in rendered.items() if value not in (None, [], {})}


def format_recall_inspect_evidence(response: dict[str, Any]) -> TextContent:
    """Render daemon-authored evidence detail from a RecallPacket match.

    This is a read-only evidence inspection. MCP preserves daemon-authored
    evidence refs, source links, freshness, readiness, and searched/unknown
    scope without adding compliance, signoff, or verification claims.
    """
    result = response.get("result", {})
    evidence = result.get("evidence") or result.get("evidence_detail") or {}
    searched_scope = result.get("searched_scope") or evidence.get("searched_scope", [])
    unknown_scope = result.get("unknown_scope") or evidence.get("unknown_scope", [])

    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "packet_id": result.get("packet_id"),
        "match_id": result.get("match_id"),
        "evidence": evidence,
        "searched_scope": searched_scope,
        "unknown_scope": unknown_scope,
        "scope_note": (
            "Evidence shown is limited to the searched scope. "
            "Unknown scope is not searched and may contain additional evidence."
        ),
    }
    return TextContent(type="text", text=json.dumps(output, indent=2, sort_keys=True))


def format_recall_expand_scope(response: dict[str, Any]) -> TextContent:
    """Render a daemon-authored expanded RecallPacket.

    The daemon widens the searched scope and returns updated matches.
    MCP preserves searched/unknown scope, matches, and allowed next
    actions without adding compliance, signoff, or verification claims.
    """
    recall = response.get("recall_packet", response.get("result", {}))
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

    output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "packet_id": recall.get("packet_id"),
        "searched_scope": searched_scope,
        "unknown_scope": unknown_scope,
        "matches": rendered_matches,
    }

    if not matches:
        scope_desc = ", ".join(searched_scope) if searched_scope else "expanded scope"
        output["no_match_note"] = (
            f"Expanded lookup found no relevant items within: {scope_desc}. "
            "This does not imply absence outside searched scope."
        )

    if allowed_next_actions:
        output["allowed_next_actions"] = allowed_next_actions

    output["scope_note"] = (
        "Scope was expanded by the daemon. Unknown scope after expansion "
        "is surfaced explicitly and has not been searched."
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
    endpoint = resolve_daemon_endpoint_for_display()

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

    if details.get("deferred"):
        recovery["deferred"] = True
        operator_action = (
            "This command is deferred in the current alpha daemon. "
            "It may become available in a future daemon release."
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
