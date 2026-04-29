"""Unit tests for handlers/_match_shaping.py.

The helper is pure value transformation factored out of handle_search_decisions.
Behavior preservation is locked by tests/test_phase2_ledger.py at the integration
layer; these tests cover the helper in isolation.
"""

from __future__ import annotations

from contracts import DecisionMatch
from handlers._match_shaping import _raw_to_decision_match


def _minimum_row(**overrides) -> dict:
    """Smallest viable raw row — only the absolutely required keys."""
    base = {"decision_id": "d:1", "description": "hello"}
    base.update(overrides)
    return base


def test_raw_to_decision_match_minimum_fields() -> None:
    out = _raw_to_decision_match(_minimum_row())
    assert isinstance(out, DecisionMatch)
    assert out.decision_id == "d:1"
    assert out.description == "hello"
    assert out.code_regions == []
    assert out.confidence == 0.5  # default
    assert out.status == "ungrounded"  # no regions and no explicit status


def test_raw_to_decision_match_with_code_regions() -> None:
    row = _minimum_row(
        status="reflected",
        code_regions=[
            {"file_path": "a.py", "symbol": "fn1", "lines": [1, 10], "purpose": "p1"},
            {"file_path": "b.py", "symbol": "fn2", "lines": [20, 30]},
        ],
    )
    out = _raw_to_decision_match(row)
    assert len(out.code_regions) == 2
    assert out.code_regions[0].file_path == "a.py"
    assert out.code_regions[0].symbol == "fn1"
    assert out.code_regions[0].lines == (1, 10)
    assert out.code_regions[0].purpose == "p1"
    assert out.code_regions[1].purpose == ""  # default when missing


def test_status_inferred_when_missing_no_regions() -> None:
    out = _raw_to_decision_match(_minimum_row())
    assert out.status == "ungrounded"


def test_status_inferred_when_missing_with_regions() -> None:
    row = _minimum_row(
        code_regions=[{"file_path": "x.py", "symbol": "f", "lines": [1, 2]}],
    )
    out = _raw_to_decision_match(row)
    assert out.status == "pending"


def test_status_passes_through_when_known() -> None:
    for known in ("reflected", "drifted", "pending", "ungrounded"):
        out = _raw_to_decision_match(_minimum_row(status=known))
        assert out.status == known, f"failed for {known}"


def test_status_unknown_value_falls_through_to_inference() -> None:
    """Non-canonical status string → fall through to region-based inference."""
    out = _raw_to_decision_match(_minimum_row(status="garbage"))
    assert out.status == "ungrounded"


def test_signoff_object_preserved() -> None:
    row = _minimum_row(signoff={"state": "ratified", "signer": "alice"})
    out = _raw_to_decision_match(row)
    assert out.signoff_state == "ratified"
    assert out.signoff == {"state": "ratified", "signer": "alice"}


def test_signoff_none_handled() -> None:
    out = _raw_to_decision_match(_minimum_row(signoff=None))
    assert out.signoff_state is None
    assert out.signoff is None


def test_optional_fields_default_when_missing() -> None:
    out = _raw_to_decision_match(_minimum_row())
    assert out.source_ref == ""
    assert out.drift_evidence == ""
    assert out.related_constraints == []
    assert out.source_excerpt == ""
    assert out.meeting_date == ""
