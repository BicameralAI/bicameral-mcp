"""v0.4.12 — bicameral.preflight regression tests.

Covers:

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
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contracts import (
    BriefDecision,
    BriefDivergence,
    BriefGap,
    BriefResponse,
    CodeRegionSummary,
    DecisionMatch,
    LinkCommitResponse,
    PreflightResponse,
    SearchDecisionsResponse,
)
from handlers.preflight import (
    _check_dedup,
    _content_tokens,
    _dedup_key_for,
    _has_actionable_signal_in_brief,
    _has_actionable_signal_in_search,
    _validate_topic,
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


def test_actionable_signal_in_brief_divergence():
    brief = SimpleNamespace(
        divergences=[SimpleNamespace()],
        gaps=[],
    )
    assert _has_actionable_signal_in_brief(brief) is True


def test_actionable_signal_in_brief_open_question_gap():
    brief = SimpleNamespace(
        divergences=[],
        gaps=[SimpleNamespace(hint="open-question phrasing")],
    )
    assert _has_actionable_signal_in_brief(brief) is True


def test_actionable_signal_in_brief_plain():
    brief = SimpleNamespace(
        divergences=[],
        gaps=[SimpleNamespace(hint="missing acceptance criteria")],
    )
    assert _has_actionable_signal_in_brief(brief) is False


# ── Handler tests with mocked search/brief ──────────────────────────


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


def _empty_brief() -> BriefResponse:
    return BriefResponse(
        topic="test",
        as_of="2026-04-14T00:00:00Z",
        ref="HEAD",
    )


def _brief_with(
    divergences: list[BriefDivergence] | None = None,
    open_qs: list[str] | None = None,
    drift_candidates: list[BriefDecision] | None = None,
) -> BriefResponse:
    return BriefResponse(
        topic="test",
        as_of="2026-04-14T00:00:00Z",
        ref="HEAD",
        divergences=divergences or [],
        gaps=[
            BriefGap(description=q, hint="open-question phrasing")
            for q in (open_qs or [])
        ],
        drift_candidates=drift_candidates or [],
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
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=_empty_brief()),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is False
    assert r.reason == "no_actionable_signal"


@pytest.mark.asyncio
async def test_guided_mode_fires_on_plain_matches():
    """Standard intensity: guided mode fires whenever there are matches,
    even without actionable signal."""
    ctx = _ctx(guided=True)
    search = _search_response_with([
        _match("intent:1", status="reflected"),
    ])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=_empty_brief()),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is True
    assert r.reason == "fired"
    assert len(r.decisions) >= 1


@pytest.mark.asyncio
async def test_normal_mode_fires_on_drifted_match():
    """Even in normal mode, a drifted match is enough to fire."""
    ctx = _ctx(guided=False)
    search = _search_response_with([
        _match("intent:1", status="drifted"),
    ])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=_empty_brief()),
    ):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert r.fired is True
    assert r.reason == "fired"
    assert len(r.drift_candidates) >= 1


@pytest.mark.asyncio
async def test_brief_chain_fires_when_search_has_drift():
    """Q1=B: brief chain fires when search returns drift, regardless of mode."""
    ctx = _ctx(guided=False)
    search = _search_response_with([_match("intent:1", status="drifted")])
    brief = _brief_with(open_qs=["Should we use Redis or local memory?"])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=brief),
    ) as mock_brief:
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert mock_brief.called
    assert "brief" in r.sources_chained
    assert r.fired is True
    assert len(r.open_questions) == 1


@pytest.mark.asyncio
async def test_brief_chain_skipped_when_search_has_only_plain_matches_in_normal_mode():
    """Q1=B inverse: brief chain does NOT fire on plain matches in
    normal mode (would be wasted work; the gate would still produce
    fired=false)."""
    ctx = _ctx(guided=False)
    search = _search_response_with([_match("intent:1", status="reflected")])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=_empty_brief()),
    ) as mock_brief:
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert not mock_brief.called
    assert r.fired is False


@pytest.mark.asyncio
async def test_brief_chain_fires_in_guided_mode_even_on_plain_matches():
    """Q1=B: in guided mode, brief chains even on plain matches because
    the user opted into the loud experience."""
    ctx = _ctx(guided=True)
    search = _search_response_with([_match("intent:1", status="reflected")])
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch(
        "handlers.preflight.handle_brief",
        new=AsyncMock(return_value=_empty_brief()),
    ) as mock_brief:
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    assert mock_brief.called
    assert "brief" in r.sources_chained


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


@pytest.mark.asyncio
async def test_brief_failure_continues_with_search_only():
    """Robustness: if brief throws, preflight falls back to search-only
    rendering instead of failing the whole call."""
    ctx = _ctx(guided=False)
    search = _search_response_with([_match("intent:1", status="drifted")])
    async def _boom(*a, **kw):
        raise RuntimeError("brief crashed")
    with patch(
        "handlers.preflight.handle_search_decisions",
        new=AsyncMock(return_value=search),
    ), patch("handlers.preflight.handle_brief", side_effect=_boom):
        r = await handle_preflight(ctx, topic="Stripe webhook payment")
    # Should still fire because search alone has actionable signal
    assert r.fired is True
    assert r.reason == "fired"
    assert "search" in r.sources_chained
    # Brief in sources_chained means it was attempted; on failure it
    # should NOT be in sources_chained
    assert "brief" not in r.sources_chained
