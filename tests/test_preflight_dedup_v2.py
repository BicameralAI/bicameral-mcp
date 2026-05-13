"""Unit-tests for the broadened preflight dedup cache key (#87 Phase 4).

Pin the contract for ``_dedup_key_for`` / ``_check_dedup`` / the bypass
branch in ``handle_preflight`` so future refactors that drop the
file_paths or ledger_revision component fail loudly. Companion to the
M7a/b/c row-driven coverage in ``tests/eval/run_preflight_eval.py`` —
those exercise the end-to-end behavior; these pin the helper shape and
the bypass semantics that Kevin's signoff specifically called out
(issue #87 — "correctness over saving a preflight call").

Mostly solitary (the unit under test is a pure key-formatter and a
small dict-cache check). The integration test for the revision-lookup
bypass uses a real ``handle_preflight`` call against a SimpleNamespace
ctx; the underlying ``get_ledger_revision`` is the only seam.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.preflight import (
    _check_dedup,
    _dedup_key_for,
    _normalize_file_paths_for_key,
)

# ── _normalize_file_paths_for_key ─────────────────────────────────────


def test_normalize_file_paths_empty_collapses_to_empty_string():
    assert _normalize_file_paths_for_key(None) == ""
    assert _normalize_file_paths_for_key([]) == ""
    assert _normalize_file_paths_for_key(["", None, ""]) == ""  # type: ignore[list-item]


def test_normalize_file_paths_is_order_insensitive():
    a = _normalize_file_paths_for_key(["b/two.py", "a/one.py"])
    b = _normalize_file_paths_for_key(["a/one.py", "b/two.py"])
    assert a == b


def test_normalize_file_paths_is_case_insensitive():
    a = _normalize_file_paths_for_key(["Auth/JWT.py"])
    b = _normalize_file_paths_for_key(["auth/jwt.py"])
    assert a == b


def test_normalize_file_paths_dedupes_repeats():
    out = _normalize_file_paths_for_key(["x.py", "x.py", "x.py"])
    assert out == "x.py"


def test_normalize_file_paths_strips_whitespace():
    out = _normalize_file_paths_for_key(["  auth/jwt.py  "])
    assert out == "auth/jwt.py"


# ── _dedup_key_for ───────────────────────────────────────────────────


def test_dedup_key_includes_all_three_components_separated_by_double_pipe():
    key = _dedup_key_for("Stripe webhook", ["payments/stripe.py"], "rev-1")
    parts = key.split("||")
    assert len(parts) == 3
    assert "stripe" in parts[0] and "webhook" in parts[0]
    assert parts[1] == "payments/stripe.py"
    assert parts[2] == "rev-1"


def test_dedup_key_topic_phrasing_collapses():
    """Legacy v0.4.12 behavior preserved: 'Stripe webhook' and
    'webhook Stripe' produce the same topic component."""
    a = _dedup_key_for("Stripe webhook", ["x.py"], "r")
    b = _dedup_key_for("webhook Stripe", ["x.py"], "r")
    assert a == b


def test_dedup_key_differs_when_file_paths_differ():
    """M7b — same topic + different file_paths → different keys."""
    a = _dedup_key_for("refactor handler", ["auth/login.py"], "r")
    b = _dedup_key_for("refactor handler", ["billing/subs.py"], "r")
    assert a != b


def test_dedup_key_differs_when_revision_differs():
    """M7a/c — same topic+paths + different revisions → different keys."""
    a = _dedup_key_for("webhook idempotency", ["payments/stripe.py"], "rev-1")
    b = _dedup_key_for("webhook idempotency", ["payments/stripe.py"], "rev-2")
    assert a != b


def test_dedup_key_stable_when_all_three_match():
    """Same inputs in any order produce the same key — the cache must hit."""
    a = _dedup_key_for("auth jwt", ["a.py", "b.py"], "r")
    b = _dedup_key_for("jwt auth", ["b.py", "a.py"], "r")
    assert a == b


# ── _check_dedup ─────────────────────────────────────────────────────


def _ctx_with_sync_state(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(_sync_state=state if state is not None else {})


def test_check_dedup_first_call_misses_caches_marks_seen():
    ctx = _ctx_with_sync_state()
    hit = _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-1")
    assert hit is False
    # The entry must be recorded so a second call within TTL hits.
    assert ctx._sync_state["preflight_topics"]


def test_check_dedup_second_identical_call_hits_within_ttl():
    ctx = _ctx_with_sync_state()
    _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-1")
    hit = _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-1")
    assert hit is True


def test_check_dedup_misses_when_revision_changes():
    """M7a/c — second call with bumped revision must re-evaluate."""
    ctx = _ctx_with_sync_state()
    _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-1")
    hit = _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-2")
    assert hit is False


def test_check_dedup_misses_when_file_paths_change():
    """M7b — second call with different file_paths must re-evaluate."""
    ctx = _ctx_with_sync_state()
    _check_dedup(ctx, "refactor handler", ["auth/login.py"], "rev-1")
    hit = _check_dedup(ctx, "refactor handler", ["billing/subs.py"], "rev-1")
    assert hit is False


def test_check_dedup_handles_ctx_without_sync_state():
    """Defensive — ctx with no _sync_state never dedups (legacy contract)."""
    ctx = SimpleNamespace()
    hit = _check_dedup(ctx, "topic words", ["x.py"], "rev-1")
    assert hit is False


def test_check_dedup_does_not_dedup_when_topic_too_short():
    """Legacy behavior — _content_tokens returns <2 tokens → no dedup
    (the topic was already going to fire silently in the handler)."""
    ctx = _ctx_with_sync_state()
    hit = _check_dedup(ctx, "x", ["a.py"], "rev-1")
    assert hit is False


# ── Bypass path: ledger_revision=None ────────────────────────────────


def test_handle_preflight_bypasses_dedup_when_revision_lookup_fails(monkeypatch):
    """Kevin's amendment (#87 B2 signoff): when ``get_ledger_revision``
    returns None, ``handle_preflight`` MUST skip the dedup check entirely
    rather than degrade to a partial key. Verified end-to-end: two
    successive same-topic calls both reach the post-dedup region/HITL
    lookup, neither returns ``recently_checked``.
    """
    import handlers.preflight as pf
    import ledger.queries as lq

    # Force revision lookup to fail (simulates transient SurrealDB error).
    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value=None))
    monkeypatch.setattr(
        lq,
        "get_collision_pending_decisions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        lq,
        "get_context_for_ready_decisions",
        AsyncMock(return_value=[]),
    )
    import handlers.sync_middleware as sm

    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)

    ledger = MagicMock()
    ledger.get_decisions_for_files = AsyncMock(return_value=[])
    inner = MagicMock()
    inner._client = MagicMock()
    ledger._inner = inner

    sync_state: dict = {}
    ctx = SimpleNamespace(
        ledger=ledger,
        guided_mode=False,
        _sync_state=sync_state,
    )

    # Two consecutive same-topic calls — without bypass, the second
    # would be silenced. With bypass, both proceed to real evaluation.
    r1 = asyncio.run(
        pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["payments/stripe.py"])
    )
    r2 = asyncio.run(
        pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["payments/stripe.py"])
    )

    assert r1.reason != "recently_checked", (
        f"first call should not dedup-hit (clean cache), got reason={r1.reason!r}"
    )
    assert r2.reason != "recently_checked", (
        "BYPASS broken: second call returned recently_checked despite revision "
        f"lookup returning None (got reason={r2.reason!r}). Kevin's amendment "
        "requires bypass over partial-key degrade."
    )
    # Cache must NOT have been populated either — the bypass branch
    # short-circuits before _check_dedup is invoked.
    assert sync_state.get("preflight_topics", {}) == {}, (
        f"bypass branch must not write to the cache; got {sync_state.get('preflight_topics')!r}"
    )


def test_handle_preflight_dedups_when_revision_lookup_succeeds(monkeypatch):
    """Mirror of the bypass test: when revision lookup returns a value,
    same-input second call hits the cache (legacy dedup behavior with
    the broadened key)."""
    import handlers.preflight as pf
    import ledger.queries as lq

    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value="stable-rev-1"))
    monkeypatch.setattr(
        lq,
        "get_collision_pending_decisions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        lq,
        "get_context_for_ready_decisions",
        AsyncMock(return_value=[]),
    )
    import handlers.sync_middleware as sm

    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)

    ledger = MagicMock()
    ledger.get_decisions_for_files = AsyncMock(return_value=[])
    inner = MagicMock()
    inner._client = MagicMock()
    ledger._inner = inner

    sync_state: dict = {}
    ctx = SimpleNamespace(
        ledger=ledger,
        guided_mode=False,
        _sync_state=sync_state,
    )

    r1 = asyncio.run(
        pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["payments/stripe.py"])
    )
    r2 = asyncio.run(
        pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["payments/stripe.py"])
    )

    assert r1.reason != "recently_checked"
    assert r2.reason == "recently_checked", (
        f"expected dedup hit on identical-input second call, got reason={r2.reason!r}"
    )
