"""Behavioral tests for the two-tier eval gate policy (#537).

The helper is a pure decision function with no shipped collaborators, so a
solitary test that invokes it directly and asserts the returned exit code is
the correct shape (per the project's "solitary is correct for pure helpers"
exception). Each case asserts the observable output (exit code) for a given
(quality_breaches, catastrophic_breaches, gate_mode) input — not presence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))

from _gate import gate_exit_code, is_inconclusive  # noqa: E402


def test_clean_passes_in_warn_mode():
    assert gate_exit_code(quality_breaches=[], catastrophic_breaches=[], gate_mode="warn") == 0


def test_clean_passes_in_hard_mode():
    assert gate_exit_code(quality_breaches=[], catastrophic_breaches=[], gate_mode="hard") == 0


def test_quality_breach_is_advisory_in_warn_mode():
    # The #537 fix: a quality-threshold breach (LLM variance) must NOT fail CI
    # in warn mode.
    assert (
        gate_exit_code(
            quality_breaches=["recall 0.783 < 0.8"],
            catastrophic_breaches=[],
            gate_mode="warn",
        )
        == 0
    )


def test_quality_breach_still_hard_fails_in_hard_mode():
    # Legacy opt-in contract is preserved for callers that pass --gate-mode hard.
    assert (
        gate_exit_code(
            quality_breaches=["recall 0.783 < 0.8"],
            catastrophic_breaches=[],
            gate_mode="hard",
        )
        == 1
    )


def test_catastrophic_breach_hard_fails_in_warn_mode():
    # The catastrophic floor fires even in warn mode — a collapsed metric is a
    # genuine break, not variance.
    assert (
        gate_exit_code(
            quality_breaches=["recall 0.40 < 0.8"],
            catastrophic_breaches=["recall 0.40 < catastrophic floor 0.50"],
            gate_mode="warn",
        )
        == 1
    )


def test_catastrophic_breach_hard_fails_in_hard_mode():
    assert (
        gate_exit_code(
            quality_breaches=[],
            catastrophic_breaches=["recall 0.10 < catastrophic floor 0.50"],
            gate_mode="hard",
        )
        == 1
    )


def test_all_cases_errored_is_inconclusive_not_catastrophic():
    # The #536 re-run scored recall 0.000 because every case errored (no API key
    # on rerun). That must read as inconclusive, not a grounding collapse.
    assert is_inconclusive(error_count=23, total=23) is True


def test_no_cases_run_is_inconclusive():
    assert is_inconclusive(error_count=0, total=0) is True


def test_low_error_rate_is_conclusive():
    # A real collapse: the eval ran (only 1/23 errored) but recall is genuinely
    # low — the catastrophic floor should be allowed to fire.
    assert is_inconclusive(error_count=1, total=23) is False


def test_error_rate_at_threshold_is_inconclusive():
    # Exactly half errored — at/above the default 0.5 ceiling → abstain.
    assert is_inconclusive(error_count=5, total=10) is True


def test_error_rate_just_below_threshold_is_conclusive():
    assert is_inconclusive(error_count=4, total=10) is False
