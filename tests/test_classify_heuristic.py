"""Phase 1 (#77) — heuristic classifier regression tests.

Each fixture in ``tests/fixtures/ingest_level_classification/*.json`` is the
ground-truth spec for the classifier. The ``expected_level`` field is the
*ingest-level* outcome (after gate filters); the *classifier* itself maps
every input to one of L1/L2/L3 (gate filters drop or park them above this
layer). These tests assert on the classifier's L1/L2/L3 output directly.

Mapping when ``expected_level`` is null:
  - 03_strategy_not_l1 -> L3 (strategy tiebreaker: roadmap date, no behavior)
  - 04_l2_no_fork_drop  -> L2 (interface spec; classifier is L2, gate drops it)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from classify.heuristic import classify

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ingest_level_classification"


def _load(fixture_id: str) -> dict:
    """Load a fixture JSON file as UTF-8 (Windows-safe)."""
    for fp in FIXTURE_DIR.glob("*.json"):
        if fp.stem == fixture_id or fp.stem.startswith(fixture_id):
            with fp.open(encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(f"Fixture {fixture_id} not found in {FIXTURE_DIR}")


# ── One test per fixture (per-plan naming) ─────────────────────────────────


def test_01_l1_subscription_pause_classified_as_l1():
    data = _load("01_l1_subscription_pause")
    level, _rationale = classify(data["source"])
    assert level == "L1", (
        f"Expected L1 for fixture {data['id']!r}, got {level!r}. Source: {data['source'][:80]!r}"
    )


def test_02_l2_redis_sessions_classified_as_l2():
    data = _load("02_l2_redis_sessions")
    level, _rationale = classify(data["source"])
    assert level == "L2"


def test_03_strategy_not_l1_classified_as_l3():
    """Strategy tiebreaker: 'we will ship offline mode by Q3' -> L3.

    Fixture has expected_level=null because the gate filter drops strategy
    statements. The classifier itself emits L3 (strategy / roadmap intent
    without observable behavior).
    """
    data = _load("03_strategy_not_l1")
    level, rationale = classify(data["source"])
    assert level == "L3"
    assert "strategy" in rationale.lower() or "roadmap" in rationale.lower()


def test_04_l2_no_fork_drop_classified_as_l2():
    """Interface spec -> L2. Fixture has expected_level=null because the
    Gate-2 (fork-required) filter drops it, but the classifier still tags
    the technical content as L2."""
    data = _load("04_l2_no_fork_drop")
    level, _rationale = classify(data["source"])
    assert level == "L2"


def test_05_l2_driver_inferred_from_l1_classified_as_l2():
    data = _load("05_l2_driver_inferred_from_l1")
    level, _rationale = classify(data["source"])
    assert level == "L2"


def test_06_l1_offline_behavior_classified_as_l1():
    data = _load("06_l1_offline_behavior")
    level, _rationale = classify(data["source"])
    assert level == "L1"


def test_07_l2_no_driver_context_pending_classified_as_l2():
    data = _load("07_l2_no_driver_context_pending")
    level, _rationale = classify(data["source"])
    assert level == "L2"


# ── Behavioral / API contract tests ────────────────────────────────────────


def test_classify_falls_through_to_l3_when_no_signal():
    """Empty / generic input -> L3 with low-confidence rationale."""
    level, rationale = classify("")
    assert level == "L3"
    assert "low confidence" in rationale.lower()

    level2, rationale2 = classify("blah blah generic words")
    assert level2 == "L3"
    assert "low confidence" in rationale2.lower()


def test_classify_returns_rationale_for_audit():
    """Rationale string explains which signal matched. The bulk-classify
    CLI uses this for the dry-run proposal table."""
    level, rationale = classify("Members can pause their subscription for up to 90 days.")
    assert level == "L1"
    assert isinstance(rationale, str)
    assert rationale  # non-empty
    # Audit string mentions the level and either a role or a signal
    assert "L1" in rationale
    assert "Members" in rationale or "role" in rationale


def test_classify_pure_function():
    """Same input twice yields the same output. No IO, no network."""
    sample = "Users can export their data as CSV at any time."
    a = classify(sample)
    b = classify(sample)
    assert a == b

    # And with both args set
    c = classify("desc", "src body")
    d = classify("desc", "src body")
    assert c == d


def test_classify_returns_only_valid_levels():
    """Sanity: every fixture (and a synthetic empty input) yields a
    valid {L1, L2, L3} level."""
    valid = {"L1", "L2", "L3"}
    samples = [
        "",
        "blah",
        "We will ship offline mode by Q3.",
        "Members can pause their subscription.",
        "Use Redis for sessions.",
        "max key length 36 chars — Zoom SDK hard limit.",
    ]
    for s in samples:
        level, _ = classify(s)
        assert level in valid, f"unexpected level {level!r} for input {s!r}"
