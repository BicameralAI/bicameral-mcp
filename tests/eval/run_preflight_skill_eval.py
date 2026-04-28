"""Pytest runner for preflight skill-layer dataset (phase 2 — LLM-in-the-loop).

Each row describes a synthetic ledger + topic. The harness:

- Loads rows from preflight_skill_dataset.jsonl
- For each row, invokes the bicameral-preflight skill's Step 1 (relevance
  judgment) via the Anthropic Messages API
- Asserts that the LLM's chosen feature groups match `expect_relevant` and
  exclude `expect_strict_irrelevant`

Caching: responses are cached under tests/eval/fixtures/skill_judge/, keyed
on (model, SKILL.md SHA, dataset row SHA). Cache hits cost nothing; cache
misses require ANTHROPIC_API_KEY. Re-record by setting
BICAMERAL_PREFLIGHT_EVAL_RECORD=1.

Skip behavior: rows skip cleanly when neither cache hit nor API key are
available — so the suite stays runnable on forks and forks-of-forks.

This is the empirical recall measurement for the catalog's skill-layer
miss/false-fire rows (M1-M4, FF1, FF3 in the catalog). A failure here is
real signal: the LLM did not recover the failure mode the row models.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Sibling-module import (matches tests/eval_decision_relevance.py convention).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _skill_judge import (  # noqa: E402  (sibling module)
    DEFAULT_MODEL,
    fixture_exists,
    judge_relevance,
)


DATASET = Path(__file__).parent / "preflight_skill_dataset.jsonl"

REQUIRED_KEYS = {"id", "axis", "title", "topic", "ledger", "expect_relevant"}
ALLOWED_AXES = {"miss", "false_fire", "correct"}


def _load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def _validate_row(row: dict) -> None:
    missing = REQUIRED_KEYS - row.keys()
    if missing:
        raise AssertionError(f"row {row.get('id')!r} missing keys: {missing}")
    if row["axis"] not in ALLOWED_AXES:
        raise AssertionError(f"row {row['id']}: axis {row['axis']!r} not in {ALLOWED_AXES}")
    if not isinstance(row["ledger"].get("features"), list):
        raise AssertionError(f"row {row['id']}: ledger.features must be a list")


def _params() -> list:
    return [pytest.param(r, id=r["id"]) for r in _load_rows()]


@pytest.fixture(scope="session")
def _eval_model() -> str:
    return os.getenv("BICAMERAL_PREFLIGHT_EVAL_MODEL", DEFAULT_MODEL)


@pytest.mark.parametrize("row", _params())
def test_preflight_skill_relevance(row, _eval_model):
    _validate_row(row)

    has_cache = fixture_exists(topic=row["topic"], ledger=row["ledger"], model=_eval_model)
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if not has_cache and not has_key:
        pytest.skip(
            "no cached fixture and no ANTHROPIC_API_KEY — re-record locally with "
            "BICAMERAL_PREFLIGHT_EVAL_RECORD=1 and commit the fixture, or set the "
            "API key in CI"
        )

    judgment = judge_relevance(
        topic=row["topic"],
        ledger=row["ledger"],
        model=_eval_model,
    )

    chosen = set(judgment.get("relevant_features") or [])
    expect_rel = set(row["expect_relevant"])
    expect_irrel = set(row.get("expect_strict_irrelevant") or [])

    missing_required = expect_rel - chosen
    incorrect_picks = chosen & expect_irrel

    assert not missing_required, (
        f"{row['id']}: skill missed required feature group(s) {sorted(missing_required)}. "
        f"Chose: {sorted(chosen)}. Reasoning: {judgment.get('reasoning', '')!r}"
    )
    assert not incorrect_picks, (
        f"{row['id']}: skill drilled into irrelevant feature group(s) {sorted(incorrect_picks)}. "
        f"Chose: {sorted(chosen)}. Reasoning: {judgment.get('reasoning', '')!r}"
    )


def test_skill_dataset_schema_valid():
    for row in _load_rows():
        _validate_row(row)
