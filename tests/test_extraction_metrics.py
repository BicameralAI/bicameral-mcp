"""Offline unit tests for _extraction_metrics.

Exercises the fuzzy matching, 1:1 assignment, and aggregate math with
synthetic extracted/fixture pairs. No network, no fixture files on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _extraction_metrics import (  # noqa: E402
    aggregate_extraction_metrics,
    compute_extraction_metrics,
)


def _f(descriptions: list[str]) -> dict:
    """Synthesize a fixture dict from bare description strings."""
    return {"decisions": [{"description": d} for d in descriptions]}


def _e(descriptions: list[str]) -> list[dict]:
    return [{"description": d} for d in descriptions]


def test_skipped_when_fixture_absent():
    out = compute_extraction_metrics(_e(["anything"]), None, matcher="rapidfuzz")
    assert out == {"skipped": True, "reason": "no fixture"}


def test_perfect_match_is_p1_r1_f1_1():
    fixture = _f(
        [
            "Add 12-second timeout to payment authorize calls",
            "Emit payment.timeout event via EventBus",
        ]
    )
    extracted = _e(
        [
            "Add 12-second timeout to payment authorize calls",
            "Emit payment.timeout event via EventBus",
        ]
    )
    out = compute_extraction_metrics(extracted, fixture, matcher="rapidfuzz")
    assert out["skipped"] is False
    assert out["true_positives"] == 2
    assert out["false_positives"] == 0
    assert out["false_negatives"] == 0
    assert out["precision"] == 1.0
    assert out["recall"] == 1.0
    assert out["f1"] == 1.0


def test_fuzzy_match_tolerates_word_order_and_synonyms():
    """token_set_ratio should match reasonable paraphrases above threshold."""
    fixture = _f(["Add 12-second timeout to payment authorize calls"])
    extracted = _e(["Payment authorize calls must use a 12-second timeout ceiling"])
    out = compute_extraction_metrics(extracted, fixture, matcher="rapidfuzz")
    assert out["true_positives"] == 1
    assert out["false_positives"] == 0
    assert out["false_negatives"] == 0


def test_low_similarity_is_false_positive_and_false_negative():
    fixture = _f(["Add 12-second timeout to payment authorize calls"])
    extracted = _e(["Use Redis Streams for dead letter queue"])
    out = compute_extraction_metrics(extracted, fixture, matcher="rapidfuzz")
    assert out["true_positives"] == 0
    assert out["false_positives"] == 1
    assert out["false_negatives"] == 1
    assert out["precision"] == 0.0
    assert out["recall"] == 0.0
    assert out["f1"] == 0.0


def test_partial_match_mixed_precision_and_recall():
    fixture = _f(
        [
            "Add timeout to authorize calls",
            "Emit timeout event via EventBus",
            "Drop garbage provider responses",
        ]
    )
    extracted = _e(
        [
            "Add timeout to authorize calls",  # TP
            "Drop garbage provider responses",  # TP
            "Use circuit breaker for rate limiting",  # FP
        ]
    )
    out = compute_extraction_metrics(extracted, fixture, matcher="rapidfuzz")
    assert out["true_positives"] == 2
    assert out["false_positives"] == 1
    assert out["false_negatives"] == 1  # emit timeout event
    assert 0.6 < out["precision"] < 0.7  # 2/3
    assert 0.6 < out["recall"] < 0.7  # 2/3


def test_one_to_one_matching_prevents_double_counting():
    """If two extracted items both look like one fixture item, only one wins."""
    fixture = _f(["Add 12-second timeout to payment authorize calls"])
    extracted = _e(
        [
            "Add 12-second timeout to payment authorize calls",
            "Add a 12-second timeout to authorize calls in payments",  # very similar
        ]
    )
    out = compute_extraction_metrics(extracted, fixture, matcher="rapidfuzz")
    assert out["true_positives"] == 1  # not 2
    assert out["false_positives"] == 1  # the second one doesn't match anything new
    assert out["false_negatives"] == 0


def test_aggregate_sums_across_scored_and_ignores_skipped():
    per_transcript = [
        {
            "skipped": False,
            "true_positives": 3,
            "false_positives": 1,
            "false_negatives": 2,
            "precision": 0.75,
            "recall": 0.6,
            "f1": 0.667,
        },
        {
            "skipped": False,
            "true_positives": 5,
            "false_positives": 0,
            "false_negatives": 1,
            "precision": 1.0,
            "recall": 0.833,
            "f1": 0.909,
        },
        {"skipped": True, "reason": "no fixture"},
    ]
    out = aggregate_extraction_metrics(per_transcript)
    assert out["skipped"] is False
    assert out["scored_transcripts"] == 2
    assert out["true_positives"] == 8
    assert out["false_positives"] == 1
    assert out["false_negatives"] == 3
    # precision = 8/9, recall = 8/11
    assert abs(out["precision"] - 8 / 9) < 1e-3
    assert abs(out["recall"] - 8 / 11) < 1e-3


def test_aggregate_all_skipped_returns_skipped():
    per_transcript = [
        {"skipped": True, "reason": "no fixture"},
        {"skipped": True, "reason": "no fixture"},
    ]
    out = aggregate_extraction_metrics(per_transcript)
    assert out["skipped"] is True


def test_empty_extraction_and_empty_fixture_gives_zero_not_error():
    out = compute_extraction_metrics([], _f([]), matcher="rapidfuzz")
    assert out["skipped"] is False
    assert out["true_positives"] == 0
    assert out["precision"] == 0.0
    assert out["recall"] == 0.0
    assert out["f1"] == 0.0


# ── LLM-as-judge path ──────────────────────────────────────────────


def test_pick_matcher_auto_picks_llm_when_key_present(monkeypatch):
    from _extraction_metrics import _pick_matcher

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-fake")
    assert _pick_matcher("auto") == "llm"


def test_pick_matcher_auto_falls_back_to_rapidfuzz(monkeypatch):
    from _extraction_metrics import _pick_matcher

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _pick_matcher("auto") == "rapidfuzz"


def test_pick_matcher_explicit_overrides_env(monkeypatch):
    from _extraction_metrics import _pick_matcher

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-fake")
    assert _pick_matcher("rapidfuzz") == "rapidfuzz"


def test_llm_match_caches_response(monkeypatch, tmp_path):
    """When a cache file exists for (model, actual_sha, expected_sha), no
    network call happens — proven by unsetting ANTHROPIC_API_KEY."""
    import _extraction_matcher as m

    monkeypatch.setattr(m, "MATCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(m, "DEFAULT_MATCHER_MODEL", "test-model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("M1_EVAL_MODEL", raising=False)

    actual = ["a one", "a two"]
    expected = ["e one", "e two"]
    actual_sha = m._list_sha(actual)
    expected_sha = m._list_sha(expected)
    cache_file = m._cache_path("test-model", actual_sha, expected_sha)

    cache_file.write_text(
        '{"model": "test-model", "n_actual": 2, "n_expected": 2, '
        '"pairs": [[0, 1], [1, 0]], "rationales": []}'
    )

    pairs = m.llm_match(actual, expected)
    assert pairs == [(0, 1), (1, 0)]


def test_llm_match_raises_without_api_key_on_cache_miss(monkeypatch, tmp_path):
    import _extraction_matcher as m

    monkeypatch.setattr(m, "MATCH_CACHE_DIR", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        m.llm_match(["a"], ["b"], use_cache=False)


def test_llm_match_parses_valid_response_into_pairs():
    """_parse_matches enforces 1:1, valid indices, and adds unmatched
    actuals as (idx, None)."""
    import _extraction_matcher as m

    tool_input = {
        "matches": [
            {"actual_index": 0, "expected_index": 1, "rationale": "same"},
            {"actual_index": 2, "expected_index": 0, "rationale": "same"},
            # invalid: out of range
            {"actual_index": 99, "expected_index": 0, "rationale": "bad"},
            # invalid: duplicate expected_index
            {"actual_index": 1, "expected_index": 1, "rationale": "dup"},
        ]
    }
    pairs = m._parse_matches(tool_input, n_actual=3, n_expected=2)
    # Sorted by actual_idx, with idx 1 unmatched
    assert pairs == [(0, 1), (1, None), (2, 0)]


def test_compute_extraction_metrics_dispatches_to_llm(monkeypatch):
    """When matcher='llm', compute_extraction_metrics calls llm_match
    instead of rapidfuzz. We stub llm_match so no network is needed."""
    import _extraction_matcher
    import _extraction_metrics

    actual = _e(["X", "Y", "Z"])
    fixture = _f(["P", "Q"])

    calls = []

    def fake_llm_match(a, e, **kwargs):
        calls.append((tuple(a), tuple(e)))
        # Y matches Q, Z matches P, X is unmatched
        return [(0, None), (1, 1), (2, 0)]

    monkeypatch.setattr(_extraction_matcher, "llm_match", fake_llm_match)

    out = _extraction_metrics.compute_extraction_metrics(actual, fixture, matcher="llm")

    assert calls == [(("X", "Y", "Z"), ("P", "Q"))]
    assert out["matcher"] == "llm"
    assert out["true_positives"] == 2
    assert out["false_positives"] == 1
    assert out["false_negatives"] == 0
    assert out["match_threshold"] is None  # threshold is rapidfuzz-only
