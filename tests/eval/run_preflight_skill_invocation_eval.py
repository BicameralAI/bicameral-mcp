"""Pytest runner for the Step-0 preflight invocation dataset (#306 Part B).

Each row asks: given a topic + handler-empty preflight result, does the
caller LLM elect to call ``bicameral.history()`` before submitting? The
harness:

- Loads rows from preflight_skill_invocation_dataset.jsonl
- For each row, drives a multi-turn tool-use loop via
  ``_skill_invocation_judge.run_invocation_judgment`` — exposes
  ``bicameral_history`` and ``submit_decision_to_proceed`` as tool defs
- Classifies the outcome against the row's ``should_invoke_history``
  ground truth using the 2x2 truth table:

    | should_invoke | invoked | outcome                            |
    |---------------|---------|------------------------------------|
    | True          | True    | invoked_history_correctly          |
    | True          | False   | skipped_history_should_have   ← MISS|
    | False         | True    | invoked_history_unnecessarily ← FP  |
    | False         | False   | proceeded_without_fetch            |

A failing test is a "miss" (FN) or an "over-fetch" (FP) — both are
signal. The aggregate JSON written to test-results/preflight-skill-
invocation.json feeds the Part C step-summary renderer.

Caching: responses cached under tests/eval/fixtures/skill_invocation_judge/
keyed on (model, SKILL.md SHA, dataset row SHA). Cache hits cost nothing;
cache misses require ANTHROPIC_API_KEY. Re-record by setting
BICAMERAL_PREFLIGHT_INVOCATION_EVAL_RECORD=1.

Skip behavior: rows skip cleanly when neither cache hit nor API key are
available — so the suite stays runnable on forks.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Sibling-module import (matches run_preflight_skill_eval.py convention).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _skill_invocation_judge import (  # noqa: E402  (sibling module)
    DEFAULT_MODEL,
    classify_outcome,
    fixture_exists,
    run_invocation_judgment,
)

DATASET = Path(__file__).parent / "preflight_skill_invocation_dataset.jsonl"

REQUIRED_KEYS = {"id", "topic", "handler_result", "seeded_decisions", "should_invoke_history"}


def _load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def _validate_row(row: dict) -> None:
    missing = REQUIRED_KEYS - row.keys()
    if missing:
        raise AssertionError(f"row {row.get('id')!r} missing keys: {missing}")
    if not isinstance(row["seeded_decisions"], list):
        raise AssertionError(f"row {row['id']}: seeded_decisions must be a list")
    if not isinstance(row["should_invoke_history"], bool):
        raise AssertionError(f"row {row['id']}: should_invoke_history must be bool")
    if row["handler_result"] != {"fired": False}:
        raise AssertionError(
            f"row {row['id']}: handler_result must be {{'fired': False}} — this axis tests "
            f"the handler-empty path only; saw {row['handler_result']!r}"
        )


def _params() -> list:
    return [pytest.param(r, id=r["id"]) for r in _load_rows()]


@pytest.fixture(scope="session")
def _eval_model() -> str:
    return os.getenv("BICAMERAL_PREFLIGHT_INVOCATION_EVAL_MODEL", DEFAULT_MODEL)


@pytest.mark.parametrize("row", _params())
def test_preflight_skill_invocation(row, _eval_model):
    _validate_row(row)

    has_cache = fixture_exists(
        topic=row["topic"], seeded_decisions=row["seeded_decisions"], model=_eval_model
    )
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not has_cache and not has_key:
        pytest.skip(
            "no cached fixture and no ANTHROPIC_API_KEY — re-record locally with "
            "BICAMERAL_PREFLIGHT_INVOCATION_EVAL_RECORD=1 and commit the fixture, "
            "or set the API key in CI"
        )

    judgment = run_invocation_judgment(
        case_id=row["id"],
        topic=row["topic"],
        seeded_decisions=row["seeded_decisions"],
        model=_eval_model,
    )

    outcome = classify_outcome(
        should_invoke=row["should_invoke_history"], invoked=judgment.invoked_history
    )

    # Test passes iff the LLM's invocation decision matches ground truth.
    # The outcome string is rendered in the assertion message either way so
    # the summary renderer (Part C) can scrape pytest output if the JSON
    # artifact is missing.
    assert outcome in {"invoked_history_correctly", "proceeded_without_fetch"}, (
        f"{row['id']}: outcome={outcome} "
        f"(should_invoke_history={row['should_invoke_history']}, "
        f"invoked_history={judgment.invoked_history}, "
        f"submitted={judgment.submitted}). "
        f"Reasoning: {judgment.reasoning!r}"
    )


def test_invocation_dataset_schema_valid():
    rows = _load_rows()
    for row in rows:
        _validate_row(row)
    # Sanity: the issue spec requires 8/7 balance — drift here is a real
    # signal that someone changed the dataset without updating ground
    # truth counts. Soft assertion lets a future row addition pass once
    # the spec is intentionally updated.
    should_invoke_count = sum(1 for r in rows if r["should_invoke_history"])
    should_skip_count = sum(1 for r in rows if not r["should_invoke_history"])
    assert should_invoke_count >= 8, (
        f"should_invoke count dropped below #306 minimum (8): got {should_invoke_count}"
    )
    assert should_skip_count >= 7, (
        f"should_skip count dropped below #306 minimum (7): got {should_skip_count}"
    )
