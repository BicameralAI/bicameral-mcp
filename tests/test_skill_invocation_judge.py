"""Sociable unit tests for the Step-0 outcome classifier (#306 Part B).

The runner's pass/fail is downstream of the API call, but the
2x2 truth-table that converts ``(should_invoke, invoked) → outcome``
is pure logic. Test it standalone so a refactor of the truth table
fails fast here without burning an API call.

Per CLAUDE.md's sociable testing rule: no MagicMock, no API. The
classifier is a pure function on bool inputs; the test is table-driven.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from _skill_invocation_judge import (  # noqa: E402
    ALL_OUTCOMES,
    OUTCOME_INVOKED_CORRECTLY,
    OUTCOME_INVOKED_UNNECESSARILY,
    OUTCOME_PROCEEDED_WITHOUT_FETCH,
    OUTCOME_SKIPPED_SHOULD_HAVE,
    InvocationJudgment,
    classify_outcome,
)


@pytest.mark.parametrize(
    "should_invoke, invoked, expected",
    [
        (True, True, OUTCOME_INVOKED_CORRECTLY),
        (True, False, OUTCOME_SKIPPED_SHOULD_HAVE),
        (False, True, OUTCOME_INVOKED_UNNECESSARILY),
        (False, False, OUTCOME_PROCEEDED_WITHOUT_FETCH),
    ],
)
def test_classify_outcome_truth_table(should_invoke, invoked, expected):
    """All four cells of the 2x2 classification matrix."""
    assert classify_outcome(should_invoke=should_invoke, invoked=invoked) == expected


def test_all_outcomes_partition_is_complete():
    """The ALL_OUTCOMES tuple must cover every classification — if a
    refactor adds a fifth outcome, this fails so the summary renderer
    (Part C) gets updated alongside."""
    expected = {
        classify_outcome(should_invoke=si, invoked=i) for si in (True, False) for i in (True, False)
    }
    assert set(ALL_OUTCOMES) == expected
    assert len(ALL_OUTCOMES) == 4, "Outcome space must remain four cells"


def test_invocation_judgment_dataclass_immutable():
    """``InvocationJudgment`` is frozen — cached fixtures + summary
    renderer rely on hashability and accidental mutation safety. If a
    refactor unfreezes the dataclass, this fails so the cache contract
    is preserved."""
    j = InvocationJudgment(
        case_id="X1",
        invoked_history=True,
        submitted=True,
        reasoning="r",
        turns=2,
        tokens_in=10,
        tokens_out=20,
    )
    with pytest.raises(
        (AttributeError, Exception)
    ):  # FrozenInstanceError subclass of AttributeError
        j.invoked_history = False  # type: ignore[misc]


def test_classify_outcome_pure_no_side_effects():
    """Same inputs → same outputs across N calls. Guards against an
    accidental import-time singleton or memoization regression."""
    for _ in range(3):
        assert classify_outcome(should_invoke=True, invoked=True) == OUTCOME_INVOKED_CORRECTLY
        assert (
            classify_outcome(should_invoke=False, invoked=False) == OUTCOME_PROCEEDED_WITHOUT_FETCH
        )
