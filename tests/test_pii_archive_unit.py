"""#221 Phase A — sociable unit tests for the PiiArchive primitive.

Sociable per CLAUDE.md: real SQLite in-memory + tmp_path. The only
non-real seam is ``monkeypatch.setattr`` for the crash-injection test,
which is a narrow seam for an otherwise unreachable failure mode
(CLAUDE.md rule 4 allowance).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pii_archive import (
    ArchiveEntry,
    ErasePredicate,
    PiiArchive,
    PiiArchiveError,
)


def _archive(path: str = ":memory:") -> PiiArchive:
    return PiiArchive(path)


def _put_sample(archive: PiiArchive, **kwargs) -> str:
    defaults = {
        "text": "Alice said we should ratify the auth proposal",
        "speakers": ["alice@example.com"],
        "source_ref": "meeting-2026-05-14",
        "meeting_date": "2026-05-14",
        "decision_id": None,
    }
    defaults.update(kwargs)
    return archive.put(**defaults)


# ── determinism + dedup ─────────────────────────────────────────────


def test_put_returns_deterministic_key() -> None:
    """Same (text, source_ref, meeting_date) → same key across calls."""
    a = _archive()
    k1 = _put_sample(a)
    k2 = _put_sample(a)
    assert k1 == k2


def test_put_with_distinct_inputs_returns_distinct_keys() -> None:
    a = _archive()
    k1 = _put_sample(a, text="text one")
    k2 = _put_sample(a, text="text two")
    k3 = _put_sample(a, text="text one", meeting_date="2026-05-15")
    assert len({k1, k2, k3}) == 3


def test_put_is_idempotent_on_dedup() -> None:
    """Calling put twice with same payload returns same key; row count is 1."""
    a = _archive()
    k = _put_sample(a)
    assert k == _put_sample(a)
    # Verify only one row exists for this key
    keys = list(a.iter_keys())
    assert keys == [k]


# ── get round-trip ──────────────────────────────────────────────────


def test_get_round_trip() -> None:
    a = _archive()
    k = _put_sample(a)
    entry = a.get(k)
    assert entry is not None
    assert isinstance(entry, ArchiveEntry)
    assert entry.key == k
    assert entry.text == "Alice said we should ratify the auth proposal"
    assert entry.speakers == ["alice@example.com"]
    assert entry.source_ref == "meeting-2026-05-14"
    assert entry.meeting_date == "2026-05-14"


def test_get_missing_key_returns_none() -> None:
    a = _archive()
    assert a.get("0" * 64) is None


# ── erase by predicate ──────────────────────────────────────────────


def test_erase_by_speaker_substring() -> None:
    a = _archive()
    _put_sample(a, text="alice says X", speakers=["alice@example.com"])
    _put_sample(a, text="bob says Y", speakers=["bob@example.com"])
    _put_sample(a, text="alice says Z", speakers=["alice@example.com"])
    count = a.erase_by_predicate(ErasePredicate(speaker_match="alice@"))
    assert count == 2
    # Non-matching survivor
    remaining = list(a.iter_keys())
    assert len(remaining) == 1
    survivor = a.get(remaining[0])
    assert survivor is not None
    assert survivor.speakers == ["bob@example.com"]


def test_erase_by_source_ref_substring() -> None:
    a = _archive()
    _put_sample(a, text="t1", source_ref="meeting-2026-05-14")
    _put_sample(a, text="t2", source_ref="meeting-2026-05-15")
    _put_sample(a, text="t3", source_ref="slack-thread-abc")
    count = a.erase_by_predicate(ErasePredicate(source_ref_match="meeting-"))
    assert count == 2
    remaining = list(a.iter_keys())
    assert len(remaining) == 1
    survivor = a.get(remaining[0])
    assert survivor is not None
    assert survivor.source_ref == "slack-thread-abc"


def test_erase_by_archive_key_exact() -> None:
    a = _archive()
    k1 = _put_sample(a, text="t1")
    k2 = _put_sample(a, text="t2")
    count = a.erase_by_predicate(ErasePredicate(archive_key=k1))
    assert count == 1
    assert a.get(k1) is None
    assert a.get(k2) is not None


def test_erase_predicate_no_match_returns_zero() -> None:
    a = _archive()
    _put_sample(a, text="t1")
    count = a.erase_by_predicate(ErasePredicate(speaker_match="not-a-real-handle"))
    assert count == 0
    assert len(list(a.iter_keys())) == 1


def test_erase_predicate_empty_returns_zero() -> None:
    """Empty predicate (no fields set) is a noop, returns 0."""
    a = _archive()
    _put_sample(a)
    count = a.erase_by_predicate(ErasePredicate())
    assert count == 0
    assert len(list(a.iter_keys())) == 1


# ── transactional crash safety ───────────────────────────────────────


def test_erase_transactional_on_crash(tmp_path: Path) -> None:
    """Mid-erasure crash leaves the archive coherent at pre-crash state.

    Narrow seam: patch ``commit`` to raise so the DELETE statement runs
    inside the txn but the commit fails. Per SQLite atomicity, the
    rollback discards the deletion. Verifies the fail-closed promise
    of the plan's Phase A discipline #1.
    """
    db = tmp_path / "archive.db"
    a = PiiArchive(db)
    k = _put_sample(a, text="surviving content")
    a.close()

    # Reopen and inject the crash. We patch ``PiiArchive._commit`` rather
    # than the underlying ``sqlite3.Connection.commit`` because the
    # connection's commit slot is a read-only C attribute resistant to
    # ``patch.object``. ``_commit`` exists exactly to make this seam
    # patchable for the fail-closed test (see CLAUDE.md "narrow seam"
    # rule 4).
    a2 = PiiArchive(db)

    def _raise(*_a, **_kw):
        raise sqlite3.OperationalError("simulated mid-txn failure")

    with patch.object(a2, "_commit", side_effect=_raise):
        with pytest.raises(PiiArchiveError):
            a2.erase_by_predicate(ErasePredicate(archive_key=k))

    a2.close()

    # Reopen fresh: row must still be there.
    a3 = PiiArchive(db)
    entry = a3.get(k)
    assert entry is not None, "Row was deleted despite simulated commit failure"
    assert entry.text == "surviving content"
    a3.close()


# ── unwritable backing store ────────────────────────────────────────


def test_archive_unwritable_raises_pii_archive_error(tmp_path: Path) -> None:
    """Unwritable path → fail-fast at constructor, not silent allow."""
    # Point at a path inside a file (not a directory) — SQLite will reject.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("blocker", encoding="utf-8")
    bad_path = blocker / "archive.db"

    with pytest.raises(PiiArchiveError) as excinfo:
        PiiArchive(bad_path)
    assert "could not open" in str(excinfo.value).lower()


# ── iter_keys ───────────────────────────────────────────────────────


def test_iter_keys_yields_every_stored_key() -> None:
    a = _archive()
    k1 = _put_sample(a, text="t1")
    k2 = _put_sample(a, text="t2")
    k3 = _put_sample(a, text="t3")
    assert set(a.iter_keys()) == {k1, k2, k3}


def test_iter_keys_empty_when_nothing_stored() -> None:
    a = _archive()
    assert list(a.iter_keys()) == []


# ── close ───────────────────────────────────────────────────────────


def test_close_releases_connection(tmp_path: Path) -> None:
    db = tmp_path / "archive.db"
    a = PiiArchive(db)
    _put_sample(a)
    a.close()
    # Reopen succeeds (file is not locked).
    a2 = PiiArchive(db)
    assert len(list(a2.iter_keys())) == 1
    a2.close()
