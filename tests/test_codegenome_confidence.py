"""Phase 1 unit tests — codegenome.confidence helpers."""

from __future__ import annotations

import math

import pytest

from codegenome.confidence import noisy_or, weighted_average


# ── noisy_or ────────────────────────────────────────────────────────────────


def test_noisy_or_two_independent_70_percent_signals():
    assert round(noisy_or([0.7, 0.7]), 2) == 0.91


def test_noisy_or_empty_returns_zero():
    assert noisy_or([]) == 0.0


def test_noisy_or_single_value_returns_value():
    assert noisy_or([0.42]) == pytest.approx(0.42)


def test_noisy_or_clamps_negative_to_zero():
    result = noisy_or([-0.5, 0.5])
    assert 0.0 <= result <= 1.0
    assert result == pytest.approx(0.5)


def test_noisy_or_clamps_above_one_to_one():
    assert noisy_or([1.5]) == pytest.approx(1.0)
    assert noisy_or([1.5, 0.3]) == pytest.approx(1.0)


def test_noisy_or_three_signals_matches_formula():
    a, b, c = 0.4, 0.5, 0.6
    expected = 1.0 - (1 - a) * (1 - b) * (1 - c)
    assert noisy_or([a, b, c]) == pytest.approx(expected)


def test_noisy_or_all_zero_returns_zero():
    assert noisy_or([0.0, 0.0, 0.0]) == 0.0


# ── weighted_average ────────────────────────────────────────────────────────


def test_weighted_average_basic():
    assert weighted_average({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}) == pytest.approx(0.5)


def test_weighted_average_unequal_weights():
    assert weighted_average({"a": 1.0, "b": 0.0}, {"a": 0.75, "b": 0.25}) == pytest.approx(0.75)


def test_weighted_average_missing_weight_drops_signal():
    assert weighted_average({"a": 1.0, "ignored": 5.0}, {"a": 1.0}) == pytest.approx(1.0)


def test_weighted_average_empty_weights_returns_zero():
    assert weighted_average({"a": 1.0}, {}) == 0.0


def test_weighted_average_empty_signals_returns_zero():
    assert weighted_average({}, {"a": 1.0}) == 0.0


def test_weighted_average_total_weight_zero_returns_zero():
    result = weighted_average({"a": 1.0}, {"a": 0.0})
    assert result == 0.0
    assert not math.isnan(result)
