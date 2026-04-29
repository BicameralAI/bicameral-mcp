"""v0.4.12 — bicameral.preflight regression tests.

Covers (HISTORICAL — see status note below):

  1. Pure-function tests for the helpers (_validate_topic, _content_tokens,
     _has_actionable_signal_in_search, _check_dedup) — synchronous, IO-free.

  2. Handler tests with mocked search/brief responses — cover every
     fired/not-fired path:
       - topic_too_generic (validation failure)
       - recently_checked (dedup hit)
       - preflight_disabled (env var mute)
       - no_matches (empty search response)
       - no_actionable_signal (normal mode + plain matches only)
       - fired (normal mode + drift)
       - fired (guided mode + plain matches)

  3. Mode interaction: normal mode SHOULD NOT fire on plain matches;
     guided mode SHOULD fire on the same plain matches.

  4. Brief chain: triggers when search has actionable signal, OR when
     guided_mode=True. Doesn't fire otherwise.

Status (issue #69): ``_has_actionable_signal_in_search`` was removed in
commit 12f25eb ("v0.10.0 — hierarchical dashboard, history-based
preflight, per-section ingest"). The preflight refactor dropped BM25
topic search; preflight now reads ``bicameral.history()`` and uses LLM
reasoning to identify relevant feature groups. The "actionable signal"
predicate this file tested no longer exists as a discrete unit.

Three of the helpers (``_validate_topic``, ``_dedup_key_for``,
``_check_dedup``) still exist on the new code path, so a future port
could salvage the validation/dedup tests here. The handler-level mock
tests are tied to the old BM25 pipeline and would need full rewrites.

The file is kept for git archaeology and skipped at collection time so
it doesn't break ``pytest`` runs.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Tests cover preflight contracts removed in 12f25eb (v0.10.0 — "
    "BM25 topic search dropped; _has_actionable_signal_in_search and "
    "the BM25-based handler pipeline were removed). Kept for archaeology; "
    "validation/dedup helper tests could be ported if needed. "
    "See issue #69.",
    allow_module_level=True,
)

# Imports below intentionally retained but unreachable — they document the
# original test file's surface area for future port-forward work.
import time  # noqa: E402, F401
from types import SimpleNamespace  # noqa: E402, F401
from unittest.mock import AsyncMock, patch  # noqa: E402, F401

from contracts import (  # noqa: E402, F401
    BriefDecision,
    BriefDivergence,
    BriefGap,
    CodeRegionSummary,
    DecisionMatch,
    LinkCommitResponse,
    PreflightResponse,
    SearchDecisionsResponse,
)
from handlers.preflight import (  # noqa: E402, F401
    handle_preflight,
)


# ── Pure helpers ────────────────────────────────────────────────────


def test_validate_topic_accepts_real_topic():
    assert _validate_topic("Stripe webhook payment_intent succeeded")
    assert _validate_topic("rate limiting middleware sliding window")
    assert _validate_topic("Google Calendar OAuth callback flow")


def test_validate_topic_rejects_too_short():
    assert not _validate_topic("")
    assert not _validate_topic("foo")
    assert not _validate_topic("ab")


def test_validate_topic_rejects_single_token():
    assert not _validate_topic("webhook")
    assert not _validate_topic("authorization")


def test_validate_topic_rejects_generic_catchall():
    assert not _validate_topic("project")
    assert not _validate_topic("code")
    assert not _validate_topic("everything")
    assert not _validate_topic("system")


def test_validate_topic_strips_implementation_verbs():
    """Implementation verbs are in the stopword set so they don't
    count toward the 2-content-token requirement."""
    # "implement webhook" — only 'webhook' is content. Should fail.
    assert not _validate_topic("implement webhook")
    # "implement Stripe webhook" — 2 content tokens. Should pass.
    assert _validate_topic("implement Stripe webhook")


def test_dedup_key_normalizes_word_order():
    """'Stripe webhook' and 'webhook stripe' should dedup as same topic."""
    assert _dedup_key_for("Stripe webhook payment") == _dedup_key_for(
        "payment webhook Stripe"
    )


def test_check_dedup_marks_then_hits():
    ctx = SimpleNamespace(_sync_state={})
    # First call — no entry, should not dedup, should mark
    assert _check_dedup(ctx, "Stripe webhook payment") is False
    # Second call — entry is fresh, should dedup
    assert _check_dedup(ctx, "Stripe webhook payment") is True
    # Different topic — should not dedup
    assert _check_dedup(ctx, "rate limiting middleware") is False


def test_check_dedup_expires_after_ttl(monkeypatch):
    ctx = SimpleNamespace(_sync_state={})
    now = [1000.0]

    def fake_time():
        return now[0]

    monkeypatch.setattr("handlers.preflight.time.time", fake_time)
    _check_dedup(ctx, "Stripe webhook payment")
    # Same instant — dedup hits
    assert _check_dedup(ctx, "Stripe webhook payment") is True
    # 6 minutes later — TTL expired (300s), should NOT dedup
    now[0] += 360
    assert _check_dedup(ctx, "Stripe webhook payment") is False


def test_actionable_signal_in_search_drifted():
    matches = [
        SimpleNamespace(status="reflected"),
        SimpleNamespace(status="drifted"),
    ]
    assert _has_actionable_signal_in_search(matches) is True


def test_actionable_signal_in_search_ungrounded():
    matches = [SimpleNamespace(status="ungrounded")]
    assert _has_actionable_signal_in_search(matches) is True


def test_actionable_signal_in_search_only_reflected():
    matches = [SimpleNamespace(status="reflected"), SimpleNamespace(status="reflected")]
    assert _has_actionable_signal_in_search(matches) is False


# ── Handler tests with mocked search ────────────────────────────────


def _ctx(guided: bool = False, sync_state: dict | None = None):
    return SimpleNamespace(
        guided_mode=guided,
        _sync_state=sync_state if sync_state is not None else {},
        repo_path="/tmp/test-repo",
    )


def _empty_search_response() -> SearchDecisionsResponse:
    return SearchDecisionsResponse(
        query="test",
        sync_status=LinkCommitResponse(
            commit_hash="abc", synced=True, reason="new_commit",
        ),
        matches=[],
        ungrounded_count=0,
        suggested_review=[],
    )


def _search_response_with(matches: list[DecisionMatch]) -> SearchDecisionsResponse:
    return SearchDecisionsResponse(
        query="test",
        sync_status=LinkCommitResponse(
            commit_hash="abc", synced=True, reason="new_commit",
        ),
        matches=matches,
        ungrounded_count=sum(1 for m in matches if m.status == "ungrounded"),
        suggested_review=[m.decision_id for m in matches if m.status in ("drifted", "pending")],
    )


def _match(intent_id: str, status: str = "reflected", file_path: str = "src/foo.ts") -> DecisionMatch:
    return DecisionMatch(
        decision_id=intent_id,
        description=f"decision {intent_id}",
        status=status,  # type: ignore[arg-type]
        confidence=0.9,
        source_ref="test-ref",
        code_regions=[
            CodeRegionSummary(
                file_path=file_path, symbol="foo", lines=(1, 10), purpose="",
            )
        ],
    )



@pytest.mark.asyncio
async def test_topic_too_generic_returns_silent_skip():
    ctx = _ctx()
    r = await handle_preflight(ctx, topic="project")
    assert r.fired is False
    assert r.reason == "topic_too_generic"
    assert r.decisions == []


@pytest.mark.asyncio
async def test_dedup_hit_returns_silent_skip():
    ctx = _ctx()
    # Mock search to make sure it's NOT called on the second invocation
    with patch("handlers.preflight.handle_search_decisions") as mock_search:
        mock_search.return_value = _empty_search_response()
        r1 = await handle_preflight(ctx, topic="Stripe webhook payment")
        assert mock_search.call_count == 1
        r2 = await handle_preflight(ctx, topic="Stripe webhook payment")
        # Second call hit dedup — search should NOT have been called again
        assert mock_search.call_count == 1
        assert r2.fired is False
        assert r2.reason == "recently_checked"


@pytest.mark.asyncio
async def test_env_mute_returns_silent_skip(monkeypatch):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_MUTE", "1")
    ctx = _ctx()
    r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is False
    assert r.reason == "preflight_disabled"


@pytest.mark.asyncio
async def test_no_matches_returns_silent_skip():
    ctx = _ctx()
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=_empty_search_response()),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is False
    assert r.reason == "no_matches"
    assert "search" in r.sources_chained


@pytest.mark.asyncio
async def test_normal_mode_silent_on_plain_matches_only():
    """Q1=B + 'less intense than standard': normal mode is silent when
    the only matches are reflected with no drift, no divergences, no
    open questions."""
    ctx = _ctx(guided=False)
    search = _search_response_with([
        _match("intent:1", status="reflected"),
        _match("intent:2", status="reflected"),
    ])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is False
    assert r.reason == "no_actionable_signal"


@pytest.mark.asyncio
async def test_guided_mode_fires_on_plain_matches():
    """Standard intensity: guided mode fires whenever there are matches."""
    ctx = _ctx(guided=True)
    search = _search_response_with([_match("intent:1", status="reflected")])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is True
    assert r.reason == "fired"
    assert len(r.decisions) >= 1


@pytest.mark.asyncio
async def test_normal_mode_fires_on_drifted_match():
    """Even in normal mode, a drifted match is enough to fire."""
    ctx = _ctx(guided=False)
    search = _search_response_with([_match("intent:1", status="drifted")])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is True
    assert r.reason == "fired"
    assert len(r.drift_candidates) >= 1


@pytest.mark.asyncio
async def test_search_failure_fails_open():
    """Robustness: if search throws, preflight returns fired=false
    silently — never blocks on bicameral being unavailable."""
    ctx = _ctx()
    async def _boom(*a, **kw):
        raise RuntimeError("ledger down")
    with patch("handlers.preflight.handle_search_decisions", side_effect=_boom):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is False
    assert r.reason == "no_matches"


