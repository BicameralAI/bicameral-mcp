"""Narrative Markdown renderer for daemon-authored feature area briefs.

Renders the structured ``brief`` payload returned by the daemon's
``brief.render`` command as a chronological, plain-language Markdown
document a PM or new developer can read without Bicameral training.

Rendering rules:
- MCP faithfully renders daemon-authored data; it never infers compliance,
  safety, or completeness beyond what the daemon states.
- Unknown scope and limitations are always disclosed.
- Supersession chains and drift evidence are rendered as-is from graph edges.
- Empty sections are omitted rather than rendered with "none" placeholders.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent


def format_brief_narrative(response: dict[str, Any]) -> TextContent:
    """Render a daemon brief.render response as Markdown narrative."""
    brief: dict[str, Any] = response.get("brief", {})

    if not brief:
        return _empty_brief_response(response)

    parts: list[str] = []

    _render_heading(brief, parts)
    _render_timeline(brief.get("entries", []), parts)
    _render_open_items(brief.get("open_items", []), parts)
    _render_graph(brief.get("graph_edges", []), parts)
    _render_footer(brief, parts)

    return TextContent(type="text", text="\n".join(parts))


def _empty_brief_response(response: dict[str, Any]) -> TextContent:
    """Handle a response with no brief payload."""
    payload: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "note": "Daemon returned no brief data for the requested scope.",
    }
    unknown_scope = response.get("unknown_scope", [])
    if unknown_scope:
        payload["unknown_scope"] = unknown_scope
    limitations = response.get("limitations", [])
    if limitations:
        payload["limitations"] = limitations
    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))


def _render_heading(brief: dict[str, Any], parts: list[str]) -> None:
    topic = brief.get("topic", "Unknown")
    parts.append(f"# {topic} — Decision Context Brief")
    parts.append("")

    stats = brief.get("stats", {})
    generated_at = brief.get("generated_at", "")
    date_part = generated_at[:10] if generated_at else "unknown date"

    stat_fragments: list[str] = [f"Generated {date_part}"]

    total = stats.get("total_decisions")
    if total is not None:
        stat_fragments.append(f"{total} decisions")
    active = stats.get("active")
    if active is not None:
        stat_fragments.append(f"{active} active")
    drifted = stats.get("drifted")
    if drifted is not None and drifted > 0:
        stat_fragments.append(f"{drifted} drifted")
    superseded = stats.get("superseded")
    if superseded is not None and superseded > 0:
        stat_fragments.append(f"{superseded} superseded")
    pending = stats.get("pending_ratification")
    if pending is not None and pending > 0:
        stat_fragments.append(f"{pending} pending ratification")

    parts.append(" · ".join(stat_fragments))
    parts.append("")


def _render_timeline(entries: list[dict[str, Any]], parts: list[str]) -> None:
    if not entries:
        return

    parts.append("## Timeline")
    parts.append("")

    for i, entry in enumerate(entries, 1):
        date = entry.get("date", "unknown")
        actor = entry.get("actor", "")
        title = entry.get("title", "")
        decision_id = entry.get("decision_id", "")

        actor_clause = f"{actor} decided: " if actor else ""
        id_suffix = f" ({decision_id})" if decision_id else ""
        parts.append(f'{i}. **[{date}]** {actor_clause}"{title}"{id_suffix}')

        source = entry.get("source")
        if source:
            label = source.get("label", "")
            link = source.get("link", "")
            if label and link:
                parts.append(f"   - Source: {label} ({link})")
            elif label:
                parts.append(f"   - Source: {label}")

        status = entry.get("status", "")
        freshness = entry.get("freshness", "")
        signoff = entry.get("signoff")

        status_parts: list[str] = []
        if status:
            status_text = status.replace("_", " ").title()
            if signoff:
                signoff_date = signoff.get("date", "")
                signer = signoff.get("signer", "")
                if signoff_date and signer:
                    status_text += f" ({signoff_date}, signed by {signer})"
                elif signer:
                    status_text += f" (signed by {signer})"
            status_parts.append(status_text)
        if freshness and freshness != status:
            status_parts.append(f"**{freshness.title()}**")

        superseded_by = entry.get("superseded_by")
        if superseded_by:
            status_parts.append(f"superseded by {superseded_by}")

        if status_parts:
            parts.append(f"   - Status: {' · '.join(status_parts)}")

        bindings = entry.get("bindings", [])
        if bindings:
            binding_strs = []
            for b in bindings:
                symbol = b.get("symbol", "")
                lines = b.get("lines", "")
                if symbol and lines:
                    binding_strs.append(f"`{symbol}` (lines {lines})")
                elif symbol:
                    binding_strs.append(f"`{symbol}`")
            if binding_strs:
                parts.append(f"   - Code: {', '.join(binding_strs)}")

        excerpt = entry.get("excerpt")
        if excerpt:
            parts.append(f"   - *{excerpt}*")

        parts.append("")


def _render_open_items(open_items: list[dict[str, Any]], parts: list[str]) -> None:
    if not open_items:
        return

    parts.append("## Open Items")
    parts.append("")

    for item in open_items:
        kind = item.get("kind", "").replace("_", " ").title()
        decision_id = item.get("decision_id", "")
        title = item.get("title", "")
        detail = item.get("detail", "")

        line = f"- **{kind}:** {decision_id}"
        if title:
            line += f' "{title}"'
        if detail:
            line += f" — {detail}"
        parts.append(line)

    parts.append("")


def _render_graph(graph_edges: list[dict[str, Any]], parts: list[str]) -> None:
    if not graph_edges:
        return

    parts.append("## Decision Graph")
    parts.append("")

    for edge in graph_edges:
        source = edge.get("source", "?")
        relation = edge.get("relation", "?")
        target = edge.get("target", "")
        ref = edge.get("ref", "")
        arrow_label = relation.replace("_", " ")

        if target:
            parts.append(f"- {source} —{arrow_label}→ {target}")
        elif ref:
            parts.append(f"- {source} —{arrow_label}→ {ref}")

    parts.append("")


def _render_footer(brief: dict[str, Any], parts: list[str]) -> None:
    unknown_scope = brief.get("unknown_scope", [])
    limitations = brief.get("limitations", [])

    if not unknown_scope and not limitations:
        return

    parts.append("---")

    footer_notes: list[str] = []
    if unknown_scope:
        footer_notes.append(f"Unknown scope: {', '.join(unknown_scope)}")
    if limitations:
        footer_notes.append(f"Limitations: {'; '.join(limitations)}")

    parts.append(f"*{'. '.join(footer_notes)}.*")
    parts.append("")
