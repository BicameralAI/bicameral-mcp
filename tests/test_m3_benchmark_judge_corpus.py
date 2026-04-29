"""Issue #44 — judge-corpus contract tests.

Validates the shape of ``expected_judge`` entries on uncertain-band
M3 benchmark cases. Pure data validation — does NOT call an LLM, does
NOT touch SurrealDB, does NOT hit the network.

The ``expected_judge`` field is the human-authored ground truth for
the operator QC pass at substantiation: each uncertain case declares
the (verdict, semantic_status) pair the LLM judge SHOULD produce. The
operator compares actual LLM output against these labels.

Per the plan's D5 rule: when ``verdict == "not_relevant"``, axis 2
(cosmetic-vs-semantic) does not apply, so ``semantic_status`` must
be ``None``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m3_benchmark"))
from cases import CASES  # noqa: E402

_VALID_VERDICTS = {"compliant", "drifted", "not_relevant"}
_VALID_SEMANTIC_STATUSES = {"semantically_preserved", "semantic_change", None}


def _uncertain_cases() -> list[dict]:
    """All M3 cases with deterministic verdict ``uncertain``."""
    return [c for c in CASES if c.get("expected") == "uncertain"]


def test_every_uncertain_case_has_expected_judge() -> None:
    """Issue #44 acceptance: every uncertain case carries a ground-truth
    judge label so the operator QC pass at substantiation has a
    reference to compare LLM output against."""
    uncertain = _uncertain_cases()
    assert uncertain, "M3 corpus has no uncertain cases — plan baseline broken"
    missing = [c["id"] for c in uncertain if "expected_judge" not in c]
    assert not missing, (
        f"uncertain cases without expected_judge: {missing}"
    )
    for case in uncertain:
        assert isinstance(case["expected_judge"], dict), (
            f"{case['id']} expected_judge must be dict, got "
            f"{type(case['expected_judge']).__name__}"
        )


def test_expected_judge_verdict_is_valid_enum() -> None:
    """``verdict`` must be one of the three values accepted by
    ``ComplianceVerdict.verdict`` in contracts.py."""
    for case in _uncertain_cases():
        verdict = case["expected_judge"].get("verdict")
        assert verdict in _VALID_VERDICTS, (
            f"{case['id']} verdict={verdict!r} not in {_VALID_VERDICTS}"
        )


def test_expected_judge_semantic_status_is_valid_enum() -> None:
    """``semantic_status`` must be one of the Phase 4 additive values
    or ``None``."""
    for case in _uncertain_cases():
        status = case["expected_judge"].get("semantic_status")
        assert status in _VALID_SEMANTIC_STATUSES, (
            f"{case['id']} semantic_status={status!r} not in "
            f"{_VALID_SEMANTIC_STATUSES}"
        )


def test_not_relevant_verdict_implies_semantic_status_none() -> None:
    """Plan D5 step 1: ``not_relevant`` is decided on axis 1
    (compliance) regardless of axis 2. When the LLM judge says
    ``not_relevant``, axis 2 doesn't apply — semantic_status must
    be ``None``."""
    for case in _uncertain_cases():
        judge = case["expected_judge"]
        if judge.get("verdict") == "not_relevant":
            assert judge.get("semantic_status") is None, (
                f"{case['id']} verdict=not_relevant but "
                f"semantic_status={judge.get('semantic_status')!r}"
            )
