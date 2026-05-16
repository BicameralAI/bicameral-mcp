"""Typed contracts for the PiiArchive primitive (#221 Phase A)."""

from __future__ import annotations

from dataclasses import dataclass


class PiiArchiveError(RuntimeError):
    """Raised when the PII archive cannot be opened, initialized, or written.

    Fail-fast: surfaced at the operator boundary, not silently swallowed.
    Phase A's only discipline (per the plan's Security & Audit section).
    """


@dataclass(frozen=True)
class ArchiveEntry:
    """One PII span stored in the archive."""

    key: str
    text: str
    speakers: list[str]
    source_ref: str
    meeting_date: str
    decision_id: str | None
    created_at: str  # ISO 8601 UTC


@dataclass(frozen=True)
class ErasePredicate:
    """Selector for ``PiiArchive.erase_by_predicate``.

    Exactly one of ``speaker_match`` (substring), ``source_ref_match``
    (substring), or ``archive_key`` (exact) is honored per call. If more
    than one is set, ``archive_key`` wins, then ``speaker_match``, then
    ``source_ref_match`` — but callers should set only one for clarity.

    Notably absent: ``text_match``. The discipline (per plan-221
    F2 / decision-log): the predicate does NOT scan the ``text`` field
    to find subjects, because that would mean reading PII to find which
    PII to erase, defeating the segregation discipline.
    """

    speaker_match: str | None = None
    source_ref_match: str | None = None
    archive_key: str | None = None
