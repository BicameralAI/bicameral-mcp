"""Functionality tests for team_server Phase 1 - Notion property serializer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _page(properties: dict) -> dict:
    return {"properties": properties}


def _block(rich_text_plain: str, btype: str = "paragraph") -> dict:
    return {
        "type": btype,
        btype: {"rich_text": [{"plain_text": rich_text_plain}]},
    }


def test_serialize_row_emits_title_then_properties_then_body():
    from team_server.extraction.notion_serializer import serialize_row

    page = _page(
        {
            "Name": {"type": "title", "title": [{"plain_text": "Decision: REST"}]},
            "Status": {"type": "select", "select": {"name": "Approved"}},
            "Owner": {"type": "rich_text", "rich_text": [{"plain_text": "Jin"}]},
        }
    )
    blocks = [_block("Body line 1"), _block("Body line 2")]
    result = serialize_row(page, blocks)
    lines = result.split("\n")
    assert lines[0] == "Decision: REST"
    assert "Owner: Jin" in lines[1:3]
    assert "Status: Approved" in lines[1:3]
    blank_idx = lines.index("")
    body = "\n".join(lines[blank_idx + 1 :])
    assert "Body line 1" in body
    assert "Body line 2" in body


def test_serialize_row_handles_typed_properties():
    from team_server.extraction.notion_serializer import serialize_row

    page = _page(
        {
            "Title": {"type": "title", "title": [{"plain_text": "T"}]},
            "Sel": {"type": "select", "select": {"name": "A"}},
            "Multi": {"type": "multi_select", "multi_select": [{"name": "x"}, {"name": "y"}]},
            "When": {"type": "date", "date": {"start": "2026-05-02", "end": None}},
            "Body": {"type": "rich_text", "rich_text": [{"plain_text": "hello"}]},
            "Done": {"type": "checkbox", "checkbox": True},
            "N": {"type": "number", "number": 42},
            "U": {"type": "url", "url": "https://example.com"},
            "Ppl": {"type": "people", "people": [{"id": "u1"}, {"id": "u2"}]},
        }
    )
    result = serialize_row(page, [])
    assert "Sel: A" in result
    assert "Multi: x, y" in result
    assert "When: 2026-05-02" in result
    assert "Body: hello" in result
    assert "Done: true" in result
    assert "N: 42" in result
    assert "U: https://example.com" in result
    assert "Ppl: u1, u2" in result


def test_serialize_row_is_byte_stable_across_calls():
    from team_server.extraction.notion_serializer import serialize_row

    page = _page(
        {
            "Name": {"type": "title", "title": [{"plain_text": "X"}]},
            "Z": {"type": "select", "select": {"name": "z1"}},
            "A": {"type": "select", "select": {"name": "a1"}},
        }
    )
    blocks = [_block("body")]
    a = serialize_row(page, blocks)
    b = serialize_row(page, blocks)
    assert a == b
