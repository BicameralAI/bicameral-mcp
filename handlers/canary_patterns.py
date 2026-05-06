"""Prompt-injection canary catalog + detector for `bicameral.ingest` content.

Reference: `qor.scripts.prompt_injection_canaries` (qor-logic ships a
parallel catalog for governance markdown; bicameral-mcp ships its own
runtime equivalent on user content per #205 doctrine — deterministic
gates, not skill-text instructions). v1 is regex-only; the
``_canary_detect`` module-level pointer is the v2 hook for a
classifier-backed detector.

Pattern shapes are deliberately tight to bound false positives:
each pattern requires the full structural adjacency of trigger words
(e.g. "ignore" + "previous-class-word" + "instruction-class-word"),
not just the presence of any individual word.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

_CANARY_CATALOG_VERSION = "v1"
"""Bump alongside classifier_version on catalog change.

Operators reading refusal `detail` strings can attribute hits to a
specific catalog generation; pattern_id semantics are stable only
within a given catalog version."""


class CanaryHit(NamedTuple):
    """One match of a canary pattern against scanned content.

    `category` is one of: override-instruction, role-impersonation,
    exfiltration-directive, jailbreak-marker.

    `pattern_id` is the 0-indexed position in `_PATTERNS` for this
    catalog version. Stable across releases until the catalog is
    re-ordered (which bumps `_CANARY_CATALOG_VERSION`).

    `match_excerpt` is the first 64 chars of the matched substring.
    Bounded so the refusal `detail` field stays small regardless of
    payload size.
    """

    category: str
    pattern_id: int
    match_excerpt: str


_EXCERPT_MAX = 64

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override-instruction",
        re.compile(
            r"(?i)\b(?:ignore|disregard|override|forget)\s+"
            r"(?:(?:previous|all|prior|earlier|above|preceding|the)\s+){1,3}"
            r"(?:instructions?|directives?|rules?|prompts?|messages?)\b"
        ),
    ),
    (
        "role-impersonation",
        re.compile(
            r"(?i)\byou\s+are\s+(?:now\s+|actually\s+)?(?:a\s+|an\s+|in\s+)?"
            r"(?:DAN|jailbreak|developer\s+mode|admin|system|root|"
            r"free|uncensored|unrestricted)\b"
        ),
    ),
    (
        "exfiltration-directive",
        re.compile(
            r"(?i)\b(?:reveal|dump|show|leak|output|print|display|repeat)\s+"
            r"(?:your|the)\s+(?:system\s+|original\s+)?"
            r"(?:prompt|instructions?|directives?|rules?|context)\b"
        ),
    ),
    (
        "jailbreak-marker",
        re.compile(
            r"(?:\[INST\]|<\|im_start\|>|<\|im_end\|>|"
            r"###\s*Instruction:|###\s*System:|<\|fim_)"
        ),
    ),
)


def detect_canaries(content: str) -> list[CanaryHit]:
    """Run every catalog pattern against `content`; return all hits.

    Empty list = clean. Caller decides whether to refuse on any hit
    (current `handlers.ingest._check_canary` refuses on first hit and
    reports total count in the refusal detail).

    Excerpts are truncated to `_EXCERPT_MAX` chars so the refusal
    `detail` field stays bounded regardless of payload size.
    """
    hits: list[CanaryHit] = []
    for idx, (category, pattern) in enumerate(_PATTERNS):
        for match in pattern.finditer(content):
            hits.append(
                CanaryHit(
                    category=category,
                    pattern_id=idx,
                    match_excerpt=match.group(0)[:_EXCERPT_MAX],
                )
            )
    return hits


_canary_detect: Callable[[str], list[CanaryHit]] = detect_canaries
"""v2 extension surface: replace this module-level pointer with a
classifier-backed implementation when one ships. Single-line swap;
no interface refactor needed. `handlers.ingest._check_canary` always
goes through this pointer (locked by `test_check_canary_invokes_function_pointer_not_direct_detect`).
"""
