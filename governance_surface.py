"""Response formatters for MCP governance inbox and contradiction resolution.

MCP is a thin client. Governance authority, contradiction materialization,
and canonical event submission are bot-owned. This module renders daemon
governance responses faithfully: inbox listings, finding inspection, and
resolution outcomes (including unauthorized rejections).

Deduplication of ContradictionReport IDs on inbox listing is MCP-local
rendering hygiene — the daemon is the authority for finding state.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent


def format_governance_inbox(response: dict[str, Any]) -> TextContent:
    """Render daemon governance.inbox.list response as deduplicated inbox items.

    Deterministically deduplicates findings by report_id so the same
    ContradictionReport never appears twice in a single inbox listing.
    """
    findings = response.get("findings", [])

    seen_ids: set[str] = set()
    deduplicated: list[dict[str, Any]] = []
    for finding in findings:
        report_id = finding.get("report_id", "")
        if report_id and report_id in seen_ids:
            continue
        if report_id:
            seen_ids.add(report_id)

        item: dict[str, Any] = {
            "report_id": report_id,
            "status": finding.get("status"),
            "reason_code": finding.get("reason_code"),
            "affected_refs": finding.get("affected_refs", []),
            "evidence_refs": finding.get("evidence_refs", []),
            "allowed_actions": finding.get("allowed_actions", []),
        }
        if finding.get("summary"):
            item["summary"] = finding["summary"]
        deduplicated.append(item)

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "findings": deduplicated,
        "total": len(deduplicated),
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_governance_inspect(response: dict[str, Any]) -> TextContent:
    """Render daemon governance.inspect response for a single finding."""
    finding = response.get("finding", {})

    rendered: dict[str, Any] = {
        "report_id": finding.get("report_id"),
        "status": finding.get("status"),
        "reason_code": finding.get("reason_code"),
        "affected_refs": finding.get("affected_refs", []),
        "evidence_refs": finding.get("evidence_refs", []),
        "allowed_actions": finding.get("allowed_actions", []),
    }
    if finding.get("summary"):
        rendered["summary"] = finding["summary"]
    if finding.get("detail"):
        rendered["detail"] = finding["detail"]
    if finding.get("created_at"):
        rendered["created_at"] = finding["created_at"]

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "finding": rendered,
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def format_governance_resolve(response: dict[str, Any]) -> TextContent:
    """Render daemon governance.resolve_contradiction response.

    Handles both successful resolution and unauthorized rejection.
    The daemon is the authority for authorization; MCP renders the
    outcome faithfully including error_code for unauthorized actors.
    """
    result = response.get("result", {})

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "report_id": result.get("report_id"),
        "action": result.get("action"),
        "accepted": result.get("accepted"),
    }
    if result.get("message"):
        mcp_output["message"] = result["message"]
    if response.get("error_code"):
        mcp_output["error_code"] = response["error_code"]
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))
