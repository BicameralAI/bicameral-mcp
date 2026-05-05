"""Heuristic classifier — pure function over (message, context, rules).

Stage 1 of the extraction pipeline. Decides whether a message is decision-
relevant. Deterministic by construction (no LLM, no temperature). Rules
are operator-configured at the workspace level + channel/database
overrides; merged at classification time by `pipeline.merge_rules`.
Option-c learned terms merge in via the same path; learned-keywords
field of rules is appended to the operator-configured keywords.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationResult:
    is_positive: bool
    matched_triggers: tuple[str, ...]
    classifier_version: str


@dataclass(frozen=True)
class TriggerRules:
    keywords: tuple[str, ...] = ()
    keyword_negatives: tuple[str, ...] = ()
    min_word_count: int = 0
    boost_reactions: tuple[str, ...] = ()
    boost_threshold: int = 1
    thread_tail_position_threshold: int | None = None
    learned_keywords: tuple[str, ...] = ()


def derive_classifier_version(rules: TriggerRules) -> str:
    """Stable hash of the rule set; changes invalidate cache downstream."""
    payload = json.dumps(
        {
            "keywords": sorted(rules.keywords),
            "keyword_negatives": sorted(rules.keyword_negatives),
            "min_word_count": rules.min_word_count,
            "boost_reactions": sorted(rules.boost_reactions),
            "boost_threshold": rules.boost_threshold,
            "thread_tail_position_threshold": rules.thread_tail_position_threshold,
            "learned_keywords": sorted(rules.learned_keywords),
            "engine": "heuristic-v1",
        },
        sort_keys=True,
    ).encode("utf-8")
    return f"heuristic-v1+{hashlib.sha256(payload).hexdigest()[:12]}"


_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def _has_negative(text_lc: str, negatives: tuple[str, ...]) -> bool:
    return any(n.lower() in text_lc for n in negatives)


def _match_keywords(text_lc: str, keywords: tuple[str, ...]) -> list[str]:
    return [kw for kw in keywords if kw.lower() in text_lc]


def _reaction_triggers(reactions: list, boost_set: set, threshold: int) -> list[str]:
    out = []
    for r in reactions:
        name = r.get("name", "")
        count = int(r.get("count", 0))
        if name in boost_set and count >= threshold:
            out.append(f":{name}:×{count}")
    return out


def classify(
    message: dict,
    context: dict,
    rules: TriggerRules,
) -> ClassificationResult:
    text = (message.get("text", "") or "").lower()
    cv = derive_classifier_version(rules)

    # Negative-list short-circuit.
    if _has_negative(text, rules.keyword_negatives):
        return ClassificationResult(False, (), cv)

    word_count = len(_WORD_RE.findall(text))
    text_matches = _match_keywords(text, (*rules.keywords, *rules.learned_keywords))
    reaction_matches = _reaction_triggers(
        context.get("reactions") or [],
        set(rules.boost_reactions),
        rules.boost_threshold,
    )
    thread_match: list[str] = []
    if rules.thread_tail_position_threshold is not None:
        if context.get("thread_position", 0) >= rules.thread_tail_position_threshold:
            thread_match.append("thread-tail")

    has_text = bool(text_matches) and word_count >= rules.min_word_count
    has_context = bool(reaction_matches) or bool(thread_match)
    is_positive = has_text or has_context

    matched = tuple(text_matches) + tuple(reaction_matches) + tuple(thread_match)
    return ClassificationResult(is_positive, matched, cv)
