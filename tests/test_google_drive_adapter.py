"""Tests for the Google Drive / Docs source adapter (#337 Phase 5).

URL parse + document-walker + normalization run unmocked.
The Google API service builder is patched via the adapter's
_fetch_document seam — googleapiclient imports defer until first call,
so tests don't need google-auth installed.
"""

from __future__ import annotations

import pytest

from sources.google_drive.adapter import (
    GoogleDriveAdapter,
    extract_document_text,
    normalize_document_to_payload,
    parse_gdrive_url,
)

# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected_id",
    [
        (
            "https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/edit",
            "1abcDEF234ghiJKL567mnoPQR890stuVWX",
        ),
        (
            "https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/edit?usp=sharing",
            "1abcDEF234ghiJKL567mnoPQR890stuVWX",
        ),
        (
            "https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX",
            "1abcDEF234ghiJKL567mnoPQR890stuVWX",
        ),
        (
            "https://drive.google.com/file/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/view",
            "1abcDEF234ghiJKL567mnoPQR890stuVWX",
        ),
    ],
)
def test_parse_gdrive_url_accepts_valid(url, expected_id):
    assert parse_gdrive_url(url) == expected_id


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://docs.google.com/spreadsheets/d/1abc",  # spreadsheets not supported in Phase 5
        "https://docs.google.com/document/d/short",  # ID too short
        "https://docs.google.com/document/",
        "",
    ],
)
def test_parse_gdrive_url_rejects_invalid(url):
    with pytest.raises(ValueError):
        parse_gdrive_url(url)


# ── Document text extraction ────────────────────────────────────────────────


def _para(text: str, style: str = "NORMAL_TEXT") -> dict:
    return {
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [{"textRun": {"content": text}}],
        }
    }


def test_extract_plain_paragraphs():
    doc = {"body": {"content": [_para("First line.\n"), _para("Second line.\n")]}}
    out = extract_document_text(doc)
    assert "First line." in out
    assert "Second line." in out


def test_extract_decorates_headings():
    doc = {
        "body": {
            "content": [
                _para("Title", style="HEADING_1"),
                _para("Subsection", style="HEADING_2"),
                _para("Sub-subsection", style="HEADING_3"),
                _para("Normal text", style="NORMAL_TEXT"),
            ]
        }
    }
    out = extract_document_text(doc)
    assert "# Title" in out
    assert "## Subsection" in out
    assert "### Sub-subsection" in out
    assert "Normal text" in out
    assert "# Normal text" not in out  # un-styled paragraphs are NOT decorated


def test_extract_flattens_tables():
    doc = {
        "body": {
            "content": [
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"content": [_para("Cell A1")]},
                                    {"content": [_para("Cell A2")]},
                                ]
                            },
                            {
                                "tableCells": [
                                    {"content": [_para("Cell B1")]},
                                    {"content": [_para("Cell B2")]},
                                ]
                            },
                        ]
                    }
                }
            ]
        }
    }
    out = extract_document_text(doc)
    assert "Cell A1" in out
    assert "Cell B2" in out


def test_extract_skips_section_break_and_toc():
    doc = {
        "body": {
            "content": [
                {"sectionBreak": {}},
                _para("Real content"),
                {"tableOfContents": {"content": []}},
            ]
        }
    }
    out = extract_document_text(doc)
    assert out == "Real content"


def test_extract_handles_empty_paragraphs():
    """Empty paragraphs (e.g. blank lines) shouldn't pollute the output."""
    doc = {
        "body": {
            "content": [
                _para(""),
                _para("Content"),
                _para("   "),
            ]
        }
    }
    out = extract_document_text(doc)
    assert out == "Content"


# ── Normalization ───────────────────────────────────────────────────────────


def test_normalize_full_shape():
    doc = {
        "title": "Architecture Decision Record",
        "body": {"content": [_para("Context.\n"), _para("Decision.\n")]},
    }
    payload = normalize_document_to_payload(doc, "doc-id-123")
    assert payload["source"] == "google_drive"
    assert payload["title"] == "Architecture Decision Record"
    assert payload["query"] == "Architecture Decision Record"
    assert len(payload["decisions"]) == 1
    assert "Context." in payload["decisions"][0]["description"]


def test_normalize_empty_doc_drops_decision():
    doc = {"title": "Empty", "body": {"content": []}}
    payload = normalize_document_to_payload(doc, "doc-id")
    assert payload["decisions"] == []


def test_normalize_falls_back_to_doc_id_when_no_title():
    doc = {"body": {"content": [_para("x")]}}
    payload = normalize_document_to_payload(doc, "doc-id-xyz")
    assert payload["title"] == "doc-id-xyz"


# ── Adapter integration ─────────────────────────────────────────────────────


def test_adapter_can_handle_url():
    a = GoogleDriveAdapter()
    assert a.can_handle_url(
        "https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/edit"
    )
    assert not a.can_handle_url("https://github.com/foo/bar")


def test_adapter_fetch_active_round_trip(monkeypatch):
    """Override _fetch_document to skip the network entirely — exercises
    the parse + normalize path end-to-end."""
    doc_response = {
        "title": "T",
        "body": {"content": [_para("Decision body")]},
    }
    a = GoogleDriveAdapter()
    monkeypatch.setattr(a, "_fetch_document", lambda doc_id: doc_response)

    result = a.fetch_active(
        "https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/edit"
    )
    assert result["source"] == "google_drive"
    assert result["title"] == "T"
    assert "Decision body" in result["decisions"][0]["description"]


def test_adapter_raises_when_token_missing(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()

    a = GoogleDriveAdapter()
    with pytest.raises(RuntimeError, match="OAuth token not configured"):
        a.fetch_active("https://docs.google.com/document/d/1abcDEF234ghiJKL567mnoPQR890stuVWX/edit")
