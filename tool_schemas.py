"""MCP-facing tool schemas for canonical ToolRequest commands."""

from __future__ import annotations

from mcp.types import Tool


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": {
            **properties,
            "actor_id": {"type": "string", "description": "Optional actor override."},
            "session_id": {"type": "string", "description": "Optional MCP session id."},
            "workspace": {"type": "string", "description": "Workspace root for policy scope."},
            "policy_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional policy scopes.",
            },
        },
        "required": required or [],
    }


def _review_schema() -> dict:
    return _schema(
        {
            "target_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        ["target_id"],
    )


SUPPORTED_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="bicameral.ingest",
        description="Submit local source/session evidence or decision candidates to the bot daemon.",
        inputSchema=_schema(
            {
                "source_uri": {"type": "string"},
                "source_type": {"type": "string"},
                "label": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "level": {"type": "string"},
                "snapshot_content": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
            ["source_uri", "source_type", "title", "description"],
        ),
    ),
    Tool(
        name="bicameral.preflight",
        description="Surface relevant decisions before implementation.",
        inputSchema=_schema(
            {
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "diff_context": {"type": "string"},
                "branch": {"type": "string"},
                "checkpoint_hint": {
                    "type": "string",
                    "description": (
                        "Optional session checkpoint hint forwarded to the daemon. "
                        "Values such as pre_work, mid_session, or pre_write are "
                        "metadata only and do not change MCP authority or behavior."
                    ),
                },
            }
        ),
    ),
    Tool(
        name="bicameral.bind",
        description="Propose binding evidence for a decision or candidate.",
        inputSchema=_schema(
            {
                "decision_or_candidate_id": {"type": "string"},
                "bindings": {"type": "array", "items": {"type": "object"}},
                "commit_sha": {"type": "string"},
                "ref_name": {"type": "string"},
            },
            ["decision_or_candidate_id", "bindings"],
        ),
    ),
    Tool(
        name="bicameral.binding.inspect",
        description="Inspect existing binding evidence through the bot daemon.",
        inputSchema=_schema(
            {
                "decision_or_candidate_id": {"type": "string"},
                "commit_sha": {"type": "string"},
            },
            ["decision_or_candidate_id"],
        ),
    ),
    Tool(
        name="bicameral.evidence.refresh",
        description="Request daemon-owned evidence currentness refresh for a tracked Decision.",
        inputSchema=_schema(
            {
                "decision_id": {
                    "type": "string",
                    "description": "Decision id whose evidence currentness should be refreshed.",
                },
            },
            ["decision_id"],
        ),
    ),
    Tool(
        name="bicameral.review.accept_candidate",
        description="Accept a decision candidate through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.reject_candidate",
        description="Reject a decision candidate through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.approve_signoff",
        description="Approve signoff on a promoted decision through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.reject_signoff",
        description="Reject signoff on a promoted decision through bot governance.",
        inputSchema=_review_schema(),
    ),
    Tool(
        name="bicameral.review.resolve_compliance",
        description="Resolve compliance state for a decision through bot governance.",
        inputSchema=_schema(
            {
                "target_id": {"type": "string"},
                "compliance_verdict": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["target_id", "compliance_verdict"],
        ),
    ),
    Tool(
        name="bicameral.history",
        description="Read replayed/materialized decision state from the bot daemon.",
        inputSchema=_schema(
            {
                "decision_id": {"type": "string"},
                "include_events": {"type": "boolean"},
                "include_bindings": {"type": "boolean"},
                "since": {"type": "string"},
            }
        ),
    ),
    Tool(
        name="bicameral.search",
        description="Search daemon-owned decision, candidate, and binding state.",
        inputSchema=_schema(
            {
                "query": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["decisions", "candidates", "bindings", "all"],
                },
                "filters": {"type": "object"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
    ),
    Tool(
        name="bicameral.request_correction.approve",
        description=(
            "Grant single-use approval for a scoped correction request. "
            "The user must confirm the specific packet item, excerpt, diff, "
            "or correction request text before submission is allowed. "
            "Approval is consumed on the next successful request_correction call."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "Packet item ID the correction targets.",
                },
                "excerpt": {
                    "type": "string",
                    "description": "Excerpt text the correction targets.",
                },
                "diff": {
                    "type": "string",
                    "description": "Diff content the correction targets.",
                },
                "correction_request": {
                    "type": "string",
                    "description": "The correction request text to approve.",
                },
            },
        ),
    ),
    Tool(
        name="bicameral.request_correction",
        description=(
            "Submit a correction request to the bot daemon. Requires prior "
            "single-use approval via bicameral.request_correction.approve "
            "scoped to the same packet item, excerpt, diff, or correction "
            "request. Submission is rejected locally without approval."
        ),
        inputSchema=_schema(
            {
                "packet_id": {
                    "type": "string",
                    "description": "Packet item ID the correction targets.",
                },
                "excerpt": {
                    "type": "string",
                    "description": "Excerpt text the correction targets.",
                },
                "diff": {
                    "type": "string",
                    "description": "Diff content the correction targets.",
                },
                "correction_request": {
                    "type": "string",
                    "description": "The correction request text to submit.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason or context for the correction.",
                },
            },
        ),
    ),
)


def tool_for_name(name: str) -> Tool | None:
    return next((tool for tool in SUPPORTED_TOOLS if tool.name == name), None)
