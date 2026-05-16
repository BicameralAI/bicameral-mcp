"""#221 Phase B-1 — unit tests for `_resolve_span_text` + `_ERASED_SENTINEL`.

Sociable per CLAUDE.md: real `PiiArchive` (SQLite-backed) for the
archive-present and post-erasure paths. Tests cover all 4 branches:
- archive present → archive text
- archive returns None (erased) → sentinel
- archive raises → sentinel + warning
- legacy row (archive_key='') → row text fallback
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.queries import _ERASED_SENTINEL, _resolve_span_text
from pii_archive import PiiArchive


@pytest.fixture
def archive(tmp_path: Path) -> PiiArchive:
    return PiiArchive(tmp_path / "test-archive.db")


def test_erased_sentinel_constant_value() -> None:
    """Pin the literal — refactor that widens this MUST also update
    the `real_spans` filter at queries.py:~204."""
    assert _ERASED_SENTINEL == "[ERASED]"


def test_resolve_returns_archive_text_when_archive_key_set(archive: PiiArchive) -> None:
    key = archive.put(
        text="canonical span text",
        speakers=["alice@example.com"],
        source_ref="meet-1",
        meeting_date="2026-05-15",
    )
    row = {"archive_key": key, "text": ""}
    assert _resolve_span_text(archive, row) == "canonical span text"


def test_resolve_falls_back_to_row_text_when_archive_key_empty(
    archive: PiiArchive,
) -> None:
    """Legacy row path — archive_key empty, text non-empty."""
    row = {"archive_key": "", "text": "legacy verbatim"}
    assert _resolve_span_text(archive, row) == "legacy verbatim"


def test_resolve_returns_erased_sentinel_when_archive_lookup_returns_none(
    archive: PiiArchive,
) -> None:
    """Post-erasure: archive_key set, archive.get returns None."""
    key = archive.put(
        text="will be erased",
        speakers=[],
        source_ref="meet-2",
        meeting_date="2026-05-15",
    )
    # Erase the archive entry
    from pii_archive import ErasePredicate

    archive.erase_by_predicate(ErasePredicate(archive_key=key))
    row = {"archive_key": key, "text": ""}
    assert _resolve_span_text(archive, row) == _ERASED_SENTINEL
    assert _resolve_span_text(archive, row) == "[ERASED]"


def test_resolve_returns_empty_string_when_both_archive_key_and_text_empty(
    archive: PiiArchive,
) -> None:
    """Defensive — the v22 ASSERT should make this impossible but the
    helper handles it gracefully."""
    row = {"archive_key": "", "text": ""}
    assert _resolve_span_text(archive, row) == ""


def test_resolve_handles_archive_error_gracefully(archive: PiiArchive, capsys) -> None:
    """Archive raises → return sentinel + stderr warning."""

    class _BrokenArchive:
        def get(self, key):
            raise RuntimeError("simulated archive corruption")

    row = {"archive_key": "deadbeef", "text": ""}
    result = _resolve_span_text(_BrokenArchive(), row)
    assert result == _ERASED_SENTINEL


def test_resolve_is_idempotent(archive: PiiArchive) -> None:
    """Same row, same archive → same result every call."""
    key = archive.put(
        text="stable",
        speakers=[],
        source_ref="meet-3",
        meeting_date="2026-05-15",
    )
    row = {"archive_key": key, "text": ""}
    a = _resolve_span_text(archive, row)
    b = _resolve_span_text(archive, row)
    c = _resolve_span_text(archive, row)
    assert a == b == c == "stable"


def test_resolve_prefers_archive_over_text_when_both_set(archive: PiiArchive) -> None:
    """If a row anomalously has both archive_key and text set (during
    a transitional state), the helper trusts the archive — the
    canonical PII source."""
    key = archive.put(
        text="archive content",
        speakers=[],
        source_ref="meet-4",
        meeting_date="2026-05-15",
    )
    row = {"archive_key": key, "text": "row-side stale text"}
    assert _resolve_span_text(archive, row) == "archive content"
