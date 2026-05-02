"""Phase 1 — heuristic classifier behavior."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from team_server.extraction.heuristic_classifier import (
    ClassificationResult, TriggerRules, classify, derive_classifier_version,
)


def test_keyword_match_yields_positive_with_matched_triggers():
    rules = TriggerRules(keywords=("decided", "agreed"))
    result = classify({"text": "we decided to use REST"}, {}, rules)
    assert result.is_positive is True
    assert "decided" in result.matched_triggers


def test_no_keyword_match_yields_negative():
    rules = TriggerRules(keywords=("decided",))
    result = classify({"text": "lunch?"}, {}, rules)
    assert result.is_positive is False
    assert result.matched_triggers == ()


def test_keyword_negative_overrides_positive():
    rules = TriggerRules(
        keywords=("decided",),
        keyword_negatives=("haha just kidding",),
    )
    result = classify(
        {"text": "we decided haha just kidding"}, {}, rules,
    )
    assert result.is_positive is False
    assert result.matched_triggers == ()


def test_min_word_count_floor_rejects_short_messages():
    rules = TriggerRules(keywords=("decided",), min_word_count=5)
    result = classify({"text": "we decided"}, {}, rules)
    assert result.is_positive is False


def test_reaction_boost_flips_negative_to_positive():
    rules = TriggerRules(
        keywords=("zzz",),
        boost_reactions=("white_check_mark",),
        boost_threshold=2,
    )
    context = {"reactions": [{"name": "white_check_mark", "count": 3}]}
    result = classify({"text": "lgtm"}, context, rules)
    assert result.is_positive is True
    assert ":white_check_mark:×3" in result.matched_triggers


def test_thread_position_booster_for_thread_tail():
    rules = TriggerRules(thread_tail_position_threshold=3)
    result = classify(
        {"text": "ok"}, {"thread_position": 5}, rules,
    )
    assert result.is_positive is True
    assert "thread-tail" in result.matched_triggers


def test_classification_is_deterministic_for_same_input():
    rules = TriggerRules(keywords=("approved",))
    msg = {"text": "approved by tech lead"}
    ctx = {}
    a = classify(msg, ctx, rules)
    b = classify(msg, ctx, rules)
    assert a == b


def test_classifier_version_changes_when_rules_change():
    a = derive_classifier_version(TriggerRules(keywords=("a",)))
    b = derive_classifier_version(TriggerRules(keywords=("a", "b")))
    assert a != b


def test_unicode_and_emoji_in_text_does_not_crash():
    rules = TriggerRules(keywords=("decided",))
    result = classify(
        {"text": "we déçidéd 🚀 to ship — résumé later"}, {}, rules,
    )
    assert isinstance(result, ClassificationResult)
