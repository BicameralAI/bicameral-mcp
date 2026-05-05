"""Bridge: team-server team_event payload → IngestPayload-compatible dict.

The team-server emits events with shape:
  {source_type, source_ref, content_hash, extraction: {decisions, ...}}

The materializer's inner_adapter.ingest_payload expects shape:
  {source, decisions: [{description, source_excerpt, ...}], repo,
   commit_hash, ...}

This module's two pure functions (is_team_server_payload +
bridge_team_server_payload) handle the recognition and shape mapping.
"""

from __future__ import annotations

_TEAM_SERVER_SOURCE_NORMALIZATION = {
    "slack": "slack",
    "notion_database_row": "notion",
}


def is_team_server_payload(payload: dict) -> bool:
    """True iff the payload has the team-server event shape."""
    return (
        isinstance(payload, dict)
        and "source_type" in payload
        and isinstance(payload.get("extraction"), dict)
    )


def bridge_team_server_payload(payload: dict) -> dict:
    """Map team-server's payload shape to an IngestPayload-compatible dict.
    Decisions land as source='slack'|'notion' with empty repo/commit_hash
    (Slack/Notion-sourced decisions don't reference code)."""
    source_type = payload.get("source_type", "")
    source = _TEAM_SERVER_SOURCE_NORMALIZATION.get(source_type, source_type)
    extraction = payload.get("extraction") or {}
    raw_decisions = extraction.get("decisions") or []
    decisions: list[dict] = []
    for d in raw_decisions:
        if isinstance(d, dict):
            decisions.append(
                {
                    "description": d.get("summary", ""),
                    "source_excerpt": d.get("context_snippet", ""),
                }
            )
        elif isinstance(d, str):
            # interim-claude-v1 placeholder shape (paragraph-split strings)
            decisions.append({"description": d, "source_excerpt": d})
    return {
        "source": source,
        "repo": "",
        "commit_hash": "",
        "decisions": decisions,
        "title": payload.get("source_ref", ""),
    }
