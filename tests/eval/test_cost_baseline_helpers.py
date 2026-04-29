"""Unit tests for the cost-baseline helpers (Stage 1 of issue #88).

Pinned coverage for:
- Synthetic ledger generator: determinism, shape, scaling, status distribution
- Token counter: basic call, JSON-serialized payloads, monotonicity
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _baseline_io import (  # noqa: E402  (sibling module)
    LATENCY_NOISE_FLOOR_MS,
    TOKEN_NOISE_FLOOR,
    find_baseline,
    regression_check,
    upsert_baseline,
)
from _synthetic_ledger import (  # noqa: E402  (sibling module)
    GENERATOR_VERSION,
    generate_ledger,
)
from _token_count import count_tokens, count_tokens_json  # noqa: E402


# ── Generator: determinism ──────────────────────────────────────────────


def test_generator_is_deterministic_for_same_seed():
    a = generate_ledger(n_features=10, seed=42)
    b = generate_ledger(n_features=10, seed=42)
    assert a == b


def test_generator_diverges_for_different_seeds():
    a = generate_ledger(n_features=10, seed=42)
    b = generate_ledger(n_features=10, seed=43)
    assert a != b


# ── Generator: shape matches HistoryResponse contract ──────────────────


def test_generator_top_level_shape():
    ledger = generate_ledger(n_features=10)
    assert set(ledger.keys()) >= {
        "features", "truncated", "total_features", "as_of", "sync_metrics",
        "_generator_version",
    }
    assert ledger["total_features"] == 10
    assert len(ledger["features"]) == 10
    assert ledger["_generator_version"] == GENERATOR_VERSION


def test_generator_feature_shape():
    ledger = generate_ledger(n_features=5, decisions_per_feature=3)
    for feature in ledger["features"]:
        assert {"id", "name", "decisions"} <= feature.keys()
        assert len(feature["decisions"]) == 3


def test_generator_decision_shape():
    ledger = generate_ledger(n_features=3, decisions_per_feature=2)
    for feature in ledger["features"]:
        for decision in feature["decisions"]:
            assert {"id", "summary", "featureId", "status", "sources"} <= decision.keys()
            assert decision["status"] in {"reflected", "drifted", "pending", "ungrounded"}
            assert decision["featureId"] == feature["id"]
            assert isinstance(decision["sources"], list)
            assert isinstance(decision["fulfillments"], list)


def test_drifted_decision_has_drift_evidence_and_fulfillment():
    ledger = generate_ledger(n_features=200, seed=42)
    drifted = [
        d
        for f in ledger["features"]
        for d in f["decisions"]
        if d["status"] == "drifted"
    ]
    assert drifted, "expected at least one drifted decision at N=200"
    for d in drifted:
        assert d["drift_evidence"], "drifted decisions must carry drift_evidence"
        assert d["fulfillments"], "drifted decisions must carry a fulfillment"


def test_ungrounded_decision_has_no_fulfillment():
    ledger = generate_ledger(n_features=200, seed=42)
    ungrounded = [
        d
        for f in ledger["features"]
        for d in f["decisions"]
        if d["status"] == "ungrounded"
    ]
    assert ungrounded, "expected at least one ungrounded decision at N=200"
    for d in ungrounded:
        assert d["fulfillments"] == []


# ── Generator: scaling ──────────────────────────────────────────────────


@pytest.mark.parametrize("n", [0, 1, 10, 100, 1000])
def test_generator_scales(n):
    ledger = generate_ledger(n_features=n)
    assert ledger["total_features"] == n
    assert len(ledger["features"]) == n


# ── Generator: status distribution ─────────────────────────────────────


def test_status_distribution_within_tolerance():
    """At a moderately large sample the distribution lands near targets."""
    ledger = generate_ledger(n_features=200, decisions_per_feature=3, seed=42)
    statuses = [d["status"] for f in ledger["features"] for d in f["decisions"]]
    n = len(statuses)
    reflected_pct = statuses.count("reflected") / n
    drifted_pct = statuses.count("drifted") / n
    # Targets: 70% reflected, 20% drifted. Tolerance ±10pts at N=600 decisions.
    assert 0.6 < reflected_pct < 0.8, f"reflected={reflected_pct:.2f}"
    assert 0.1 < drifted_pct < 0.3, f"drifted={drifted_pct:.2f}"


# ── Generator: input validation ────────────────────────────────────────


def test_generator_rejects_negative_n():
    with pytest.raises(ValueError):
        generate_ledger(n_features=-1)


def test_generator_rejects_negative_decisions_per_feature():
    with pytest.raises(ValueError):
        generate_ledger(n_features=1, decisions_per_feature=-1)


# ── Token counter ──────────────────────────────────────────────────────


def test_count_tokens_empty_string():
    assert count_tokens("") == 0


def test_count_tokens_returns_positive_for_nonempty():
    assert count_tokens("hello world") > 0


def test_count_tokens_monotonic_in_text_length():
    short = count_tokens("hello")
    long = count_tokens("hello world this is a longer sentence")
    assert long > short


def test_count_tokens_json_matches_direct_serialize():
    payload = {"foo": "bar", "n": 42}
    direct = count_tokens('{"foo": "bar", "n": 42}')
    via_json = count_tokens_json(payload)
    assert direct == via_json


def test_count_tokens_json_on_synthetic_ledger():
    """Sanity: a non-trivial ledger has a non-trivial token count."""
    ledger = generate_ledger(n_features=10)
    tokens = count_tokens_json(ledger)
    assert tokens > 100, f"N=10 ledger tokenized to suspiciously few tokens: {tokens}"


# ── Regression rule ────────────────────────────────────────────────────


def test_regression_passes_on_unchanged_value():
    assert regression_check(field="tokens", current=100, baseline=100, noise_floor=10) is None


def test_regression_passes_on_improvement():
    """Asymmetric — only flags increases, never decreases."""
    assert regression_check(field="tokens", current=70, baseline=100, noise_floor=10) is None


def test_regression_passes_below_noise_floor():
    """5-token delta on a 100-token baseline is below the 10-token floor."""
    assert regression_check(field="tokens", current=105, baseline=100, noise_floor=10) is None


def test_regression_passes_just_under_relative_threshold():
    """120 / 100 = +20.0% — exactly at the 20% threshold, not above."""
    msg = regression_check(field="tokens", current=120, baseline=100, noise_floor=10)
    assert msg is None


def test_regression_fails_above_threshold():
    msg = regression_check(field="tokens", current=130, baseline=100, noise_floor=10)
    assert msg is not None
    assert "+30.0%" in msg
    assert "exceeded threshold" in msg
    assert "BICAMERAL_EVAL_RECORD_BASELINE" in msg


def test_regression_uses_latency_noise_floor():
    """0.4ms delta on 0.08ms baseline: relative is 500% but absolute is below floor → pass."""
    msg = regression_check(
        field="p50_ms",
        current=0.48,
        baseline=0.08,
        noise_floor=LATENCY_NOISE_FLOOR_MS,
    )
    assert msg is None


def test_regression_fails_above_latency_floor_and_threshold():
    """0.6ms delta on 0.08ms baseline: above 0.5ms floor + 750% relative → fail."""
    msg = regression_check(
        field="p50_ms",
        current=0.68,
        baseline=0.08,
        noise_floor=LATENCY_NOISE_FLOOR_MS,
    )
    assert msg is not None


def test_regression_handles_zero_baseline():
    """Edge case: baseline 0 → no relative check, only floor matters."""
    msg = regression_check(field="tokens", current=20, baseline=0, noise_floor=10)
    assert msg is not None  # delta 20 ≥ floor 10
    msg2 = regression_check(field="tokens", current=5, baseline=0, noise_floor=10)
    assert msg2 is None  # delta 5 < floor 10


# ── Baseline file IO ───────────────────────────────────────────────────


def test_find_baseline_matches_metric_and_platform():
    rows = [
        {"metric": "C1", "recorded_on": "darwin", "n_features": 10, "tokens": 100},
        {"metric": "C1", "recorded_on": "linux", "n_features": 10, "tokens": 105},
    ]
    found = find_baseline(rows, metric="C1", recorded_on="darwin", n_features=10)
    assert found is not None
    assert found["tokens"] == 100


def test_find_baseline_returns_none_when_missing():
    rows = [{"metric": "C1", "recorded_on": "darwin", "n_features": 10, "tokens": 100}]
    assert find_baseline(rows, metric="C1", recorded_on="windows", n_features=10) is None
    assert find_baseline(rows, metric="C2", recorded_on="darwin") is None


def test_upsert_replaces_existing_row():
    rows = [{"metric": "C1", "recorded_on": "darwin", "n_features": 10, "tokens": 100}]
    new = {"metric": "C1", "recorded_on": "darwin", "n_features": 10, "tokens": 200}
    out = upsert_baseline(rows, new)
    assert len(out) == 1
    assert out[0]["tokens"] == 200


def test_upsert_appends_when_not_found():
    rows = [{"metric": "C1", "recorded_on": "darwin", "n_features": 10, "tokens": 100}]
    new = {"metric": "C1", "recorded_on": "linux", "n_features": 10, "tokens": 105}
    out = upsert_baseline(rows, new)
    assert len(out) == 2
