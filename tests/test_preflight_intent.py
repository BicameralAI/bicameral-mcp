"""Functionality tests for scripts.hooks.preflight_intent."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.hooks.preflight_intent import (  # noqa: E402
    IMPLEMENTATION_VERBS,
    INDIRECT_INTENT_PHRASES,
    READ_ONLY_PATTERNS,
    SKIP_PATTERNS,
    should_fire_preflight,
    suppress_capture_reminder,
)


def test_fires_on_implementation_verbs():
    """Every canonical verb in a natural sentence must fire the classifier."""
    for verb in IMPLEMENTATION_VERBS:
        prompt = f"Please {verb} the rate limiter for me."
        assert should_fire_preflight(prompt), f"verb {verb!r} did not fire"


def test_skips_on_doc_only_prompts():
    """Skip patterns must suppress the fire even when verbs are present."""
    skip_prompts = (
        "fix the typo in the README",
        "bump lodash to 4.17.21",
        "how does the rate limiter work?",
    )
    for prompt in skip_prompts:
        assert not should_fire_preflight(prompt), f"skip-prompt {prompt!r} fired"


def test_fires_on_indirect_intent():
    """Asking HOW to implement is intent — must fire."""
    indirect = (
        "how should I implement the retry logic?",
        "how do I build the payment flow?",
        "what's the best way to add idempotency keys?",
    )
    for prompt in indirect:
        assert should_fire_preflight(prompt), f"indirect prompt {prompt!r} did not fire"


def test_data_is_loadable():
    """The shared verb list must be importable, non-empty, and well-typed."""
    assert isinstance(IMPLEMENTATION_VERBS, frozenset)
    assert len(IMPLEMENTATION_VERBS) >= 28
    assert all(isinstance(v, str) and v for v in IMPLEMENTATION_VERBS)
    assert isinstance(INDIRECT_INTENT_PHRASES, tuple)
    assert all(isinstance(p, str) and p for p in INDIRECT_INTENT_PHRASES)
    assert isinstance(SKIP_PATTERNS, tuple)


def test_natural_contradiction_prompt():
    """The literal Flow 2 prompt from issue #146 must fire."""
    prompt = (
        "I know the roadmap said drag-and-drop to reorder commits, "
        "but actually we're switching to a text-editor approach. "
        "Please update cherry-pick.ts and reorder.ts."
    )
    assert should_fire_preflight(prompt)


def test_empty_prompt_does_not_fire():
    assert not should_fire_preflight("")
    assert not should_fire_preflight("   \n\t")


# ── #170 — capture-reminder suppression gate ─────────────────────────────


def test_suppress_on_read_only_prompts():
    """Read-only / informational prompts with no implementation verb suppress."""
    read_only = (
        "explain how the reorder flow works",
        "how does the session cache invalidate?",
        "review the auth middleware",
        "summarize the drag-to-reorder decision",
        "walk me through the rate limiter",
        "what is the current retry policy?",
    )
    for prompt in read_only:
        assert suppress_capture_reminder(prompt), f"{prompt!r} should suppress"


def test_no_suppress_on_implementation_verb():
    """ANY implementation verb fires (never suppress) — incl. the #175 smuggled-
    refinement case. R1: also assert a NON-'add' verb to lock the regex, not the
    literal token 'add'."""
    must_fire = (
        # the exact audit VETO example — compatible verb + smuggled structural refinement
        "add tests for the reorder flow, and expose it as a programmatic API",
        # R1 — different implementation verb, same smuggled-refinement shape
        "refactor the reorder flow and expose it as a programmatic API",
        "update cherry-pick.ts to use buttons instead of drag",
    )
    for prompt in must_fire:
        assert not suppress_capture_reminder(prompt), f"{prompt!r} must fire (not suppress)"


def test_no_suppress_acceptance_b_compatible_write():
    """Documented #170 trade-off: 'add tests for X' carries an impl verb ('add'),
    so it is NOT suppressed — acceptance case (b) is intentionally not met to
    preserve the #175 no-data-loss invariant."""
    assert not suppress_capture_reminder("add tests for drag-to-reorder")


def test_no_suppress_contradiction_prompt():
    """R3 — the Flow-2 contradiction prompt must never be suppressed."""
    prompt = (
        "I know the roadmap said drag-and-drop to reorder commits, "
        "but actually we're switching to a text-editor approach. "
        "Please update cherry-pick.ts and reorder.ts."
    )
    assert not suppress_capture_reminder(prompt)


def test_suppress_empty_and_data_loadable():
    assert not suppress_capture_reminder("")
    assert not suppress_capture_reminder("   \n\t")
    assert isinstance(READ_ONLY_PATTERNS, tuple) and len(READ_ONLY_PATTERNS) >= 8
