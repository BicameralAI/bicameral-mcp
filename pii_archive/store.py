"""SQLite-backed PiiArchive — operator-erasable PII storage substrate (#221 Phase A).

This module is the **foundation** for GDPR Art. 17 right-to-erasure. It is
**not wired into the ingest path in this cycle** — Phase B does that.
Phase A ships only the primitive plus the additive ``input_span.archive_key``
schema slot.

Operational design:

- Single SQLite file at ``~/.bicameral/pii-archive.db`` (or
  ``BICAMERAL_PII_ARCHIVE_PATH`` env override). Operator-erasable by ``rm``.
- ``put()`` is idempotent on dedup; same (text, source_ref, meeting_date)
  always yields the same key (sha256-derived).
- ``erase_by_predicate()`` runs inside a single ``BEGIN IMMEDIATE`` /
  ``COMMIT`` transaction so mid-operation crash leaves the archive
  coherent at the pre-crash state.
- ``PiiArchiveError`` is raised fail-fast on unwritable backing store.

This module **does not** auto-redact or auto-detect PII. It is the
storage substrate; the upstream caller (Phase B's ingest wiring) is
responsible for routing the right data into it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .contracts import ArchiveEntry, ErasePredicate, PiiArchiveError

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS pii_span (
    key TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    speakers TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    meeting_date TEXT NOT NULL DEFAULT '',
    decision_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pii_span_source_ref ON pii_span(source_ref);
"""


def _derive_key(text: str, source_ref: str, meeting_date: str) -> str:
    """Deterministic content-addressable key.

    Matches the dedup semantic of ``input_span``'s UNIQUE index
    ``(source_type, source_ref, text)`` (with ``source_type`` and
    ``meeting_date`` swapped — Phase B's wiring decides which composite
    is canonical). Identical inputs yield identical keys across processes
    and machines.
    """
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(source_ref.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(meeting_date.encode("utf-8"))
    return digest.hexdigest()


class PiiArchive:
    """Per-operator, operator-erasable PII storage substrate."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            # ``:memory:`` and tmp paths both flow through here; the
            # ``check_same_thread=False`` is safe because callers are
            # responsible for serializing access (Phase B's ingest path
            # is async-single-threaded; the CLI shipping in Phase C
            # takes an exclusive transaction).
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.executescript(_INIT_SQL)
            self._commit()
        except sqlite3.Error as exc:
            raise PiiArchiveError(
                f"PiiArchive could not open or initialize {self.path}: {exc}"
            ) from exc

    def _commit(self) -> None:
        """Indirection over ``self._conn.commit()`` so the crash-injection
        test can patch this method (sqlite3.Connection.commit is a
        read-only C slot and resists ``patch.object``)."""
        self._conn.commit()

    def put(
        self,
        *,
        text: str,
        speakers: list[str],
        source_ref: str = "",
        meeting_date: str = "",
        decision_id: str | None = None,
    ) -> str:
        """Insert a PII span and return its archive key.

        Idempotent on dedup — calling with the same (text, source_ref,
        meeting_date) returns the existing key without raising and
        without modifying the existing row.
        """
        key = _derive_key(text, source_ref, meeting_date)
        try:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO pii_span
                    (key, text, speakers, source_ref, meeting_date,
                     decision_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    text,
                    json.dumps(speakers),
                    source_ref,
                    meeting_date,
                    decision_id,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._commit()
        except sqlite3.Error as exc:
            raise PiiArchiveError(f"PiiArchive.put failed for key {key[:16]}…: {exc}") from exc
        return key

    def get(self, key: str) -> ArchiveEntry | None:
        """Return the entry for ``key``, or ``None`` if not present
        (including post-erasure)."""
        try:
            row = self._conn.execute(
                """
                SELECT key, text, speakers, source_ref, meeting_date,
                       decision_id, created_at
                FROM pii_span WHERE key = ?
                """,
                (key,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise PiiArchiveError(f"PiiArchive.get failed: {exc}") from exc
        if row is None:
            return None
        return ArchiveEntry(
            key=row[0],
            text=row[1],
            speakers=json.loads(row[2]),
            source_ref=row[3],
            meeting_date=row[4],
            decision_id=row[5],
            created_at=row[6],
        )

    def erase_by_predicate(self, predicate: ErasePredicate) -> int:
        """Erase matching rows inside a single transaction. Returns the
        count of rows deleted.

        Precedence when multiple predicate fields are set: ``archive_key``
        wins, then ``speaker_match``, then ``source_ref_match``. Callers
        should set exactly one for clarity.

        Transactional: ``BEGIN IMMEDIATE`` + ``COMMIT``. Mid-operation
        crash rolls back via SQLite atomicity.
        """
        if (
            predicate.archive_key is None
            and predicate.speaker_match is None
            and predicate.source_ref_match is None
        ):
            return 0

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            if predicate.archive_key is not None:
                cur = self._conn.execute(
                    "DELETE FROM pii_span WHERE key = ?",
                    (predicate.archive_key,),
                )
            elif predicate.speaker_match is not None:
                # JSON-substring match — sqlite has no native JSON-array
                # contains, but the stored value is a JSON array of strings,
                # so substring works for non-pathological speaker names.
                cur = self._conn.execute(
                    "DELETE FROM pii_span WHERE speakers LIKE ?",
                    (f"%{predicate.speaker_match}%",),
                )
            else:
                assert predicate.source_ref_match is not None
                cur = self._conn.execute(
                    "DELETE FROM pii_span WHERE source_ref LIKE ?",
                    (f"%{predicate.source_ref_match}%",),
                )
            count = cur.rowcount
            self._commit()
            return count
        except sqlite3.Error as exc:
            # Rollback is implicit on connection error; explicit safety.
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
            raise PiiArchiveError(f"PiiArchive.erase_by_predicate failed: {exc}") from exc

    def iter_keys(self) -> Iterator[str]:
        """Yield every archive key currently stored. Useful for migration
        tooling and operator-side inventory."""
        try:
            for row in self._conn.execute("SELECT key FROM pii_span"):
                yield row[0]
        except sqlite3.Error as exc:
            raise PiiArchiveError(f"PiiArchive.iter_keys failed: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]
