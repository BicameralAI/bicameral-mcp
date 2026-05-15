"""Preflight intent classifier.

Single source of truth for the verb list used by the bicameral-preflight
SKILL.md description and the UserPromptSubmit hook. Deterministic: no
LLM, no network, no I/O beyond a string scan.
"""

from __future__ import annotations

import re

IMPLEMENTATION_VERBS: frozenset[str] = frozenset(
    {
        "add",
        "build",
        "create",
        "implement",
        "modify",
        "refactor",
        "update",
        "fix",
        "change",
        "write",
        "edit",
        "move",
        "rename",
        "remove",
        "delete",
        "extract",
        "convert",
        "integrate",
        "deploy",
        "ship",
        "configure",
        "connect",
        "extend",
        "migrate",
        "wire",
        "hook up",
        "set up",
        "complete",
        "finish",
        "continue",
    }
)

INDIRECT_INTENT_PHRASES: tuple[str, ...] = (
    "how should i implement",
    "how do i build",
    "how should i write",
    "what's the best way to add",
    "what's the cleanest way to refactor",
)

SKIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfix\b.*\btypo\b", re.IGNORECASE),
    re.compile(r"\bbump\b.*\b(?:to|from)\b.*\d+\.\d+", re.IGNORECASE),
    re.compile(r"\bhow does\b", re.IGNORECASE),
    # #343 — suppress preflight for clearly non-decision-related work.
    re.compile(r"\b(?:lint|format|prettier|eslint|ruff)\b", re.IGNORECASE),
    re.compile(r"\breadme\b", re.IGNORECASE),
    re.compile(
        r"\b(?:fix|update|add|edit|configure)\b.*\b(?:ci|github.actions?|workflow)\b"
        r"|\b(?:ci|github.actions?|workflow)\b.*\b(?:fix|update|add|edit|configure)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btest(?:s|ing)?\b.*\b(?:fix|add|update|write)\b", re.IGNORECASE),
    re.compile(r"\b(?:fix|add|update|write)\b.*\btest(?:s|ing)?\b", re.IGNORECASE),
    re.compile(r"\b(?:changelog|release.notes?)\b", re.IGNORECASE),
    re.compile(r"\b(?:docker|dockerfile|compose)\b", re.IGNORECASE),
)

_VERB_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in IMPLEMENTATION_VERBS) + r")\b",
    re.IGNORECASE,
)


def should_fire_preflight(prompt: str) -> bool:
    """Return True iff prompt indicates code-implementation intent."""
    if not prompt or not prompt.strip():
        return False
    for skip in SKIP_PATTERNS:
        if skip.search(prompt):
            return False
    if _VERB_REGEX.search(prompt):
        return True
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in INDIRECT_INTENT_PHRASES)
