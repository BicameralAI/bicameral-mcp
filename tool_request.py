"""ToolRequest construction helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

MCP_TOOL_COMMANDS: dict[str, str] = {
    "bicameral.ingest": "ingest.submit_local",
    "bicameral.preflight": "preflight.run",
    "bicameral.lookup": "lookup.query",
    "bicameral.request_correction": "correction.request",
    "bicameral.bind": "binding.create",
    "bicameral.binding.inspect": "binding.inspect",
    "bicameral.evidence.refresh": "evidence.refresh",
    "bicameral.review.accept_candidate": "review.accept_candidate",
    "bicameral.review.reject_candidate": "review.reject_candidate",
    "bicameral.review.approve_signoff": "review.approve_signoff",
    "bicameral.review.reject_signoff": "review.reject_signoff",
    "bicameral.review.resolve_compliance": "review.resolve_compliance",
    "bicameral.history": "history.list",
    "bicameral.search": "search.query",
}

# Tools that are locally gated and never dispatched to the daemon.
LOCAL_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "bicameral.request_correction.approve",
    }
)

SUPPORTED_COMMANDS = tuple(MCP_TOOL_COMMANDS.values())


def build_tool_request(
    *,
    command_name: str,
    params: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request_id": str(uuid4()),
        "command": {"name": command_name, "params": _command_params(command_name, params)},
        "authority": authority,
        "issued_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _command_params(command_name: str, params: dict[str, Any]) -> dict[str, Any]:
    control_keys = {
        "actor_id",
        "session_id",
        "workspace",
        "policy_scope",
    }
    cleaned = {key: value for key, value in params.items() if key not in control_keys}

    if command_name == "ingest.submit_local":
        return _ingest_params(cleaned)
    if command_name == "preflight.run":
        return _only(cleaned, "files", "symbols", "diff_context", "branch", "checkpoint_hint")
    if command_name == "binding.create":
        return _only(cleaned, "decision_or_candidate_id", "bindings", "commit_sha", "ref_name")
    if command_name == "binding.inspect":
        return _only(cleaned, "decision_or_candidate_id", "commit_sha")
    if command_name == "evidence.refresh":
        return _only(cleaned, "decision_id")
    if command_name in {
        "review.accept_candidate",
        "review.reject_candidate",
        "review.approve_signoff",
        "review.reject_signoff",
    }:
        return _only(cleaned, "target_id", "reason")
    if command_name == "review.resolve_compliance":
        return _only(cleaned, "target_id", "compliance_verdict", "reason")
    if command_name == "history.list":
        return _only(cleaned, "decision_id", "include_events", "include_bindings", "since")
    if command_name == "search.query":
        return _only(cleaned, "query", "scope", "filters", "limit")
    if command_name == "lookup.query":
        return _only(cleaned, "files", "symbols", "scope", "include_context")
    if command_name == "correction.request":
        return _only(cleaned, "packet_id", "excerpt", "diff", "correction_request", "reason")
    return cleaned


VALID_DECISION_LEVELS: frozenset[str] = frozenset({"L1", "L2", "L3"})


def _ingest_params(cleaned: dict[str, Any]) -> dict[str, Any]:
    """Shape ingest.submit_local params with decision_level classification signal.

    When the caller provides ``decision_level``, it is forwarded as-is.
    When omitted, ``pending_classification`` is injected so the daemon
    knows to apply heuristic classification rather than silently storing
    the decision as unclassified (which codegenome/bind treats as
    tolerant L3).
    """
    result = _only(
        cleaned,
        "source_uri",
        "source_type",
        "label",
        "title",
        "description",
        "level",
        "decision_level",
        "snapshot_content",
        "evidence",
    )
    if "decision_level" not in result:
        result["pending_classification"] = True
    return result


def _only(values: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: values[key] for key in keys if key in values}
