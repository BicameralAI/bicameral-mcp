"""Tests for #337 foundations cycle 2 — universal FilterSpec + evaluator."""

from __future__ import annotations

import pytest

from filters import FilterSpec, evaluate_universal, merge_specs

# ── No-filter default ───────────────────────────────────────────────────────


def test_empty_spec_passes_everything():
    spec = FilterSpec()
    assert evaluate_universal(
        {"text": "anything", "author": "anyone", "timestamp": "2026-01-01T00:00:00Z"}, spec
    )
    # Even empty/missing candidate fields pass when no filters set.
    assert evaluate_universal({}, spec)


# ── keyword_include ─────────────────────────────────────────────────────────


def test_keyword_include_or_match():
    spec = FilterSpec(keyword_include=["decided", "agreed"])
    assert evaluate_universal({"text": "We decided to ship"}, spec)
    assert evaluate_universal({"text": "We agreed on Tuesday"}, spec)
    assert not evaluate_universal({"text": "We discussed it"}, spec)


def test_keyword_include_case_insensitive():
    spec = FilterSpec(keyword_include=["Decided"])
    assert evaluate_universal({"text": "DECIDED to do it"}, spec)
    assert evaluate_universal({"text": "decided"}, spec)


# ── keyword_exclude ─────────────────────────────────────────────────────────


def test_keyword_exclude_rejects_match():
    spec = FilterSpec(keyword_exclude=["spam", "test"])
    assert evaluate_universal({"text": "real decision"}, spec)
    assert not evaluate_universal({"text": "this is spam"}, spec)
    assert not evaluate_universal({"text": "TEST run"}, spec)


def test_include_and_exclude_compose():
    """Include + exclude both must pass."""
    spec = FilterSpec(keyword_include=["decided"], keyword_exclude=["draft"])
    assert evaluate_universal({"text": "decided to ship"}, spec)
    assert not evaluate_universal({"text": "decided draft"}, spec)
    assert not evaluate_universal({"text": "shipping it"}, spec)  # missing include


# ── author_include / author_exclude ─────────────────────────────────────────


def test_author_include_exact_match():
    spec = FilterSpec(author_include=["alice@example.com", "bob@example.com"])
    assert evaluate_universal({"text": "x", "author": "alice@example.com"}, spec)
    assert not evaluate_universal({"text": "x", "author": "carol@example.com"}, spec)
    # Empty author rejected when include is set.
    assert not evaluate_universal({"text": "x", "author": ""}, spec)


def test_author_exclude_rejects():
    spec = FilterSpec(author_exclude=["bot@example.com"])
    assert evaluate_universal({"text": "x", "author": "alice@example.com"}, spec)
    assert not evaluate_universal({"text": "x", "author": "bot@example.com"}, spec)


# ── time_window ─────────────────────────────────────────────────────────────


def test_time_window_after():
    spec = FilterSpec(time_window_after="2026-01-01T00:00:00Z")
    assert evaluate_universal({"text": "x", "timestamp": "2026-06-01T00:00:00Z"}, spec)
    assert not evaluate_universal({"text": "x", "timestamp": "2025-12-31T00:00:00Z"}, spec)
    # Equal-to-boundary rejected (strict after).
    assert not evaluate_universal({"text": "x", "timestamp": "2026-01-01T00:00:00Z"}, spec)
    # Unknown timestamp rejected when filter is set.
    assert not evaluate_universal({"text": "x", "timestamp": ""}, spec)


def test_time_window_before():
    spec = FilterSpec(time_window_before="2026-12-31T23:59:59Z")
    assert evaluate_universal({"text": "x", "timestamp": "2026-06-01T00:00:00Z"}, spec)
    assert not evaluate_universal({"text": "x", "timestamp": "2027-01-01T00:00:00Z"}, spec)


def test_time_window_range():
    spec = FilterSpec(
        time_window_after="2026-01-01T00:00:00Z", time_window_before="2026-12-31T23:59:59Z"
    )
    assert evaluate_universal({"text": "x", "timestamp": "2026-06-01T00:00:00Z"}, spec)
    assert not evaluate_universal({"text": "x", "timestamp": "2025-12-31T00:00:00Z"}, spec)
    assert not evaluate_universal({"text": "x", "timestamp": "2027-01-01T00:00:00Z"}, spec)


# ── Validation ──────────────────────────────────────────────────────────────


def test_unknown_field_rejected_by_pydantic():
    """Typo'd config field fails loud — that's the point of `extra = forbid`."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FilterSpec(keywords_include=["typo"])  # missing 's' on plural


def test_extensions_dict_is_opaque():
    """The universal evaluator ignores `extensions`; future per-source
    evaluators consume it. Setting it shouldn't affect universal behavior."""
    spec = FilterSpec(extensions={"slack_reactions": [":decision:"]})
    assert evaluate_universal({"text": "anything", "author": "x", "timestamp": "x"}, spec)


# ── merge_specs ─────────────────────────────────────────────────────────────


def test_merge_resource_overrides_source():
    src = FilterSpec(keyword_include=["src-kw"])
    res = FilterSpec(keyword_include=["res-kw"])
    merged = merge_specs(src, res)
    # Resource-level override wins.
    assert merged.keyword_include == ["res-kw"]


def test_merge_empty_resource_inherits_source():
    src = FilterSpec(keyword_include=["src-kw"], time_window_after="2026-01-01T00:00:00Z")
    res = FilterSpec()  # all defaults — inherit
    merged = merge_specs(src, res)
    assert merged.keyword_include == ["src-kw"]
    assert merged.time_window_after == "2026-01-01T00:00:00Z"


def test_merge_partial_resource_overrides_only_set_fields():
    src = FilterSpec(keyword_include=["src-kw"], author_include=["src-author"])
    res = FilterSpec(keyword_include=["res-kw"])  # author not set → inherit
    merged = merge_specs(src, res)
    assert merged.keyword_include == ["res-kw"]
    assert merged.author_include == ["src-author"]


def test_merge_extensions_shallow_merge():
    src = FilterSpec(extensions={"a": 1, "b": 2})
    res = FilterSpec(extensions={"b": 99, "c": 3})
    merged = merge_specs(src, res)
    assert merged.extensions == {"a": 1, "b": 99, "c": 3}


def test_merge_none_resource_returns_source_unchanged():
    src = FilterSpec(keyword_include=["x"])
    assert merge_specs(src, None) is src
