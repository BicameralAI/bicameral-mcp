"""Functionality tests for scripts.hooks.preflight_intent."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.hooks.preflight_intent import (  # noqa: E402
    IMPL_INTENT_SLASH_COMMANDS,
    IMPLEMENTATION_VERBS,
    INDIRECT_INTENT_PHRASES,
    SKIP_PATTERNS,
    SURFACE_EMPTY,
    SURFACE_FREE_TEXT,
    SURFACE_SLASH_BARE,
    SURFACE_SLASH_WITH_TEXT,
    SURFACE_SLASH_WITH_URL,
    classify_prompt,
    should_fire_preflight,
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


# ── #402: slash-command surface forms ─────────────────────────────────


def test_qor_plan_with_issue_url_fires():
    """The exact failing invocation from #402 must fire.

    Hypothesis 1 from the issue: the verb-regex classifier missed
    ``/qor-plan <issue-url>`` because ``plan`` is not in the verb list
    and the implementation-intent verb is encoded in the slash-command
    name rather than the prompt body. With the IMPL_INTENT_SLASH_COMMANDS
    short-circuit, this now fires.
    """
    prompt = "/qor-plan https://github.com/BicameralAI/bicameral-daemon/issues/1"
    result = classify_prompt(prompt)
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_URL
    assert result.slash_command == "qor-plan"


def test_qor_plan_with_plain_english_prompt_fires():
    """Regression coverage from #402 acceptance: plain-English /qor-plan
    must also fire, not just URL-arg form."""
    prompt = "/qor-plan add a Stripe webhook handler for payment_intent.succeeded"
    result = classify_prompt(prompt)
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_TEXT


def test_qor_implement_with_url_fires():
    """``/qor-implement`` already fired pre-#402 via the ``implement`` verb;
    regression coverage so we don't trade one trigger gap for another."""
    prompt = "/qor-implement https://github.com/BicameralAI/bicameral-mcp/issues/123"
    result = classify_prompt(prompt)
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_URL


def test_qor_implement_with_plain_prompt_fires():
    prompt = "/qor-implement wire up the new endpoint to the frontend"
    result = classify_prompt(prompt)
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_TEXT


def test_all_impl_intent_slash_commands_fire_with_url():
    """Every command in IMPL_INTENT_SLASH_COMMANDS must fire when invoked
    with a URL-only argument. Locks the contract so the Tier-1 (hook) and
    Tier-2 (skill description) gates stay aligned."""
    url = "https://github.com/BicameralAI/bicameral-mcp/issues/1"
    for command in IMPL_INTENT_SLASH_COMMANDS:
        prompt = f"/{command} {url}"
        result = classify_prompt(prompt)
        assert result.fire is True, f"impl-intent /{command} did not fire"
        assert result.prompt_surface_form == SURFACE_SLASH_WITH_URL


def test_bare_slash_command_in_impl_set_fires():
    """``/qor-auto-dev-1`` with no argument still implies implementation
    intent — the operator is launching the full dev cycle."""
    result = classify_prompt("/qor-auto-dev-1")
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_BARE
    assert result.slash_command == "qor-auto-dev-1"


def test_non_impl_slash_commands_do_not_fire():
    """Read-only / informational slash-commands must not fire. Guards
    against the opposite regression — accidentally treating every slash-
    command as implementation intent."""
    for command in ("qor-status", "qor-help", "qor-audit", "qor-validate"):
        prompt = f"/{command}"
        result = classify_prompt(prompt)
        assert result.fire is False, f"/{command} should not fire"
        assert result.prompt_surface_form == SURFACE_SLASH_BARE


def test_non_impl_slash_command_with_url_does_not_fire():
    """A URL alone after a non-impl command must not fire — the URL has
    no implementation verbs and the command is not in IMPL_INTENT."""
    prompt = "/qor-status https://github.com/BicameralAI/bicameral-mcp/issues/1"
    result = classify_prompt(prompt)
    assert result.fire is False
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_URL


def test_free_text_surface_form_classification():
    """Free-text prompts get the free_text surface form."""
    result = classify_prompt("please refactor the rate limiter")
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_FREE_TEXT
    assert result.slash_command is None


def test_empty_prompt_classification():
    """Empty prompts get a dedicated surface-form label for telemetry."""
    result = classify_prompt("")
    assert result.fire is False
    assert result.prompt_surface_form == SURFACE_EMPTY


def test_classify_prompt_and_should_fire_preflight_agree():
    """The backward-compat wrapper must mirror :func:`classify_prompt`'s
    fire bit for every input — no semantic drift between the two APIs."""
    fixtures = (
        "/qor-plan https://github.com/foo/bar/issues/1",
        "/qor-implement add stripe webhook",
        "/qor-status",
        "please refactor the rate limiter",
        "fix the typo in README",
        "",
        "how should I implement the retry logic?",
    )
    for prompt in fixtures:
        assert classify_prompt(prompt).fire == should_fire_preflight(prompt), (
            f"divergence on prompt {prompt!r}"
        )


def test_slash_command_names_lowercased():
    """Command name in ClassifyResult is lowercased so callers can
    membership-test against the canonical set without case dancing."""
    result = classify_prompt("/QOR-PLAN https://github.com/foo/bar/issues/1")
    assert result.slash_command == "qor-plan"
    assert result.fire is True


def test_unknown_slash_command_falls_through_to_verb_check():
    """A slash-command not in IMPL_INTENT falls through to the free-text
    verb classifier on the full prompt — so ``/some-future-cmd add X``
    still fires via the ``add`` verb match."""
    result = classify_prompt("/some-future-cmd add the new webhook handler")
    assert result.fire is True
    assert result.prompt_surface_form == SURFACE_SLASH_WITH_TEXT
    assert result.slash_command == "some-future-cmd"
