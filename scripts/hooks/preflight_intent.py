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


# #170 — read-only / informational intent. Used by the post-preflight capture
# reminder gate to suppress the Step 5.6 nudge on prompts that cannot be
# implementing a refinement. Deliberately NARROW and applied only AFTER the
# implementation-verb check (see suppress_capture_reminder): any prompt with a
# write verb is never suppressed, so a refinement smuggled under a compatible
# verb ("add tests ... and expose X as a programmatic API") always fires the
# reminder. This preserves the #175 invariant that contradiction judgment is
# never decided by a lexical scan over a write-intent prompt.
READ_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bexplain\b", re.IGNORECASE),
    re.compile(r"\bhow (?:does|do|did|can|would|is|are)\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is| are| does| do)\b", re.IGNORECASE),
    re.compile(r"\bwhy (?:does|do|did|is|are)\b", re.IGNORECASE),
    re.compile(r"\bdescribe\b", re.IGNORECASE),
    re.compile(r"\bsummar(?:ize|ise|y)\b", re.IGNORECASE),
    re.compile(r"\breview\b", re.IGNORECASE),
    re.compile(r"\bshow me\b", re.IGNORECASE),
    re.compile(r"\bwalk me through\b", re.IGNORECASE),
    re.compile(r"\btell me about\b", re.IGNORECASE),
    re.compile(r"\bwhere (?:is|are|does|do)\b", re.IGNORECASE),
    re.compile(r"\bunderstand\b", re.IGNORECASE),
    re.compile(r"\blook at\b", re.IGNORECASE),
)


def suppress_capture_reminder(prompt: str) -> bool:
    """Return True iff the post-preflight capture reminder should be SUPPRESSED.

    #170: the reminder (Step 5.6 of bicameral-preflight) is otherwise injected on
    every preflight call that surfaces >=1 decision, asking the user to
    disambiguate a possible refinement. That is spam on read-only / informational
    prompts where no refinement exists.

    Recall-biased gate (operator-selected "read-only-only" suppression, preserving
    the #175 no-data-loss invariant):
      1. empty / whitespace prompt        -> False (fire; uncertain)
      2. ANY implementation verb present  -> False (fire; could be implementing a
         refinement, smuggled or not — never suppress a write-intent prompt)
      3. otherwise, suppress iff a read-only / informational signal is present.

    Because step 2 runs before step 3, the read-only patterns only ever evaluate
    verb-free prompts — so their loose matching can only suppress prompts that
    cannot be performing an edit this turn. NOTE: this intentionally does NOT
    reuse should_fire_preflight, whose SKIP_PATTERNS (e.g. ``add ... test``) would
    misclassify write prompts as non-firing and reopen the #175 hole.
    """
    if not prompt or not prompt.strip():
        return False
    if _VERB_REGEX.search(prompt):
        return False
    return any(pat.search(prompt) for pat in READ_ONLY_PATTERNS)
