"""Notion DB row -> text input for the canonical extractor.

Deterministic serialization: title line, then sorted-by-key property
lines, then a blank line, then the body block plain-text. Byte-stable
output is the gating invariant for content_hash stability across polls.
"""

from __future__ import annotations


def _rich_text_plain(rich_text: list[dict]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _serialize_property(prop: dict) -> str:
    ptype = prop.get("type")
    if ptype == "title":
        return _rich_text_plain(prop.get("title", []))
    if ptype == "rich_text":
        return _rich_text_plain(prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if ptype == "multi_select":
        return ", ".join(opt.get("name", "") for opt in prop.get("multi_select", []))
    if ptype == "date":
        d = prop.get("date")
        if not d:
            return ""
        start = d.get("start", "")
        end = d.get("end")
        return f"{start}..{end}" if end else start
    if ptype == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    if ptype == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if ptype == "url":
        return prop.get("url") or ""
    if ptype == "people":
        return ", ".join(p.get("id", "") for p in prop.get("people", []))
    return f"<unknown:{ptype}>"


def _block_plain_text(block: dict) -> str:
    btype = block.get("type", "")
    body = block.get(btype) or {}
    return _rich_text_plain(body.get("rich_text", []))


def serialize_row(page: dict, blocks: list[dict]) -> str:
    properties = page.get("properties", {})
    title = ""
    prop_lines: list[str] = []
    for key in sorted(properties):
        prop = properties[key]
        value = _serialize_property(prop)
        if prop.get("type") == "title":
            title = value
        else:
            prop_lines.append(f"{key}: {value}")
    body_lines = [_block_plain_text(b) for b in blocks]
    body_text = "\n".join(line for line in body_lines if line)
    return "\n".join([title, *prop_lines, "", body_text])
