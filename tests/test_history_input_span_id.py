"""Phase A of #278 Phase 2 — HistorySource carries input_span_id.

Pins:
  1. HistorySource has an optional `input_span_id` field that defaults to None
     and serializes when set.
  2. `_row_to_history_decision` (handlers/history.py) propagates the id when
     present in the SurrealDB row, AND tolerates legacy rows that don't carry
     it (back-compat for pre-Phase-2 ingest).
"""

from __future__ import annotations

import pytest

from contracts import HistorySource


def test_history_source_has_optional_input_span_id_default_none() -> None:
    """Default-construction omits input_span_id; the field is None."""
    src = HistorySource(
        source_ref="sprint-14",
        source_type="manual",
        date="2026-05-14",
        quote="some excerpt",
    )
    assert src.input_span_id is None


def test_history_source_round_trips_input_span_id_via_model_dump() -> None:
    """When set, input_span_id round-trips through .model_dump()."""
    src = HistorySource(
        source_ref="sprint-14",
        source_type="manual",
        date="2026-05-14",
        quote="some excerpt",
        input_span_id="input_span:abc123",
    )
    dumped = src.model_dump()
    assert dumped["input_span_id"] == "input_span:abc123"


def test_row_to_history_decision_populates_input_span_id_when_present() -> None:
    """Feeding a SurrealDB row whose _source_spans entry carries an `id`
    yields a HistorySource with that id propagated.

    Invokes `_row_to_history_decision` directly with a fixture dict shaped
    like a single decision row joined with its source spans.
    """
    from handlers.history import _row_to_history_decision

    row = {
        "decision_id": "decision:abc",
        "description": "test decision",
        "status": "ungrounded",
        "_source_spans": [
            {
                "id": "input_span:span001",
                "text": "verbatim excerpt",
                "source_ref": "meeting-001",
                "source_type": "transcript",
                "meeting_date": "2026-05-14",
                "speakers": ["Jin"],
            }
        ],
    }
    dec = _row_to_history_decision(row, feature_id="test-feature")
    assert len(dec.sources) == 1
    assert dec.sources[0].input_span_id == "input_span:span001"
    assert dec.sources[0].quote == "verbatim excerpt"


def test_row_to_history_decision_tolerates_missing_input_span_id() -> None:
    """Legacy rows (pre-Phase-2 ingest) won't carry `id` in their span dicts.
    The hydration code must produce HistorySource with input_span_id=None
    rather than raising or coercing a falsy id to a string."""
    from handlers.history import _row_to_history_decision

    row = {
        "decision_id": "decision:legacy",
        "description": "legacy decision",
        "status": "ungrounded",
        "_source_spans": [
            {
                # No "id" key — legacy data shape
                "text": "legacy excerpt",
                "source_ref": "old-meeting",
                "source_type": "transcript",
                "meeting_date": "2026-01-01",
                "speakers": [],
            }
        ],
    }
    dec = _row_to_history_decision(row, feature_id="test-feature")
    assert len(dec.sources) == 1
    assert dec.sources[0].input_span_id is None
    assert dec.sources[0].quote == "legacy excerpt"


def test_row_to_history_decision_tolerates_empty_string_id() -> None:
    """A row where `id` is an empty string (SurrealDB null-coalesce artifact)
    must produce input_span_id=None, not the empty string. Pins the
    `str(span_id_raw) if span_id_raw else None` guard."""
    from handlers.history import _row_to_history_decision

    row = {
        "decision_id": "decision:edge",
        "description": "edge case",
        "status": "ungrounded",
        "_source_spans": [
            {
                "id": "",  # falsy — should become None, not ""
                "text": "edge excerpt",
                "source_ref": "edge-meeting",
                "source_type": "manual",
                "meeting_date": "2026-05-14",
                "speakers": [],
            }
        ],
    }
    dec = _row_to_history_decision(row, feature_id="test-feature")
    assert len(dec.sources) == 1
    assert dec.sources[0].input_span_id is None
