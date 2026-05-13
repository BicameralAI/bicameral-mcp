"""#87 Phase 5 — dedup decision telemetry.

Pin the contract for ``preflight_dedup_decision`` events emitted on the
two dedup outcomes that matter for production attribution:

1. ``invalidated_by_revision_bump`` — M7a/M7c signal: a same-(topic,
   file_paths) call missed the cache because ``ledger_revision``
   advanced. Confirms the new key shape (Phase 4) is doing useful work.
2. ``bypassed_revision_unknown`` — safety signal: revision lookup
   failed and the handler bypassed dedup entirely per Kevin's amendment.

Other dedup outcomes (hit, first-call, topic-changed, file_paths-shift)
are intentionally NOT emitted — keeping the telemetry signal-to-noise
ratio tight on the metric Kevin asked for at signoff. File-paths-shift
miss attribution can be backfilled from ``write_preflight_event`` rows
later if needed (the file_paths_hash field already lives there).

Sociable where it matters: we touch the real telemetry write path
(``preflight_telemetry.write_dedup_event``) but mock ``_append`` so we
don't write to the real ~/.bicameral/ JSONL file in tests. The seam is
the file-handle layer, not the public API.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.preflight import _dedup_miss_was_revision_bump


# ── _dedup_miss_was_revision_bump classification ─────────────────────


def _ctx(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(_sync_state=state if state is not None else {})


def test_classifier_returns_false_when_no_prior_entry():
    """First call → no prefix in cache → not a revision bump."""
    ctx = _ctx()
    assert _dedup_miss_was_revision_bump(ctx, "topic words", ["a.py"], "rev-1") is False


def test_classifier_returns_true_when_only_revision_differs():
    """M7a/c shape — same topic + same paths + different rev within TTL."""
    ctx = _ctx()
    # Seed cache with prior call at rev-1.
    from handlers.preflight import _check_dedup

    _check_dedup(ctx, "stripe webhook", ["payments/stripe.py"], "rev-1")
    # Now classify a miss at rev-2 with same prefix.
    assert (
        _dedup_miss_was_revision_bump(ctx, "stripe webhook", ["payments/stripe.py"], "rev-2")
        is True
    )


def test_classifier_returns_false_when_file_paths_differ():
    """M7b shape — same topic + different paths is a prefix change, not a
    revision bump. We don't count this as the invalidation signal."""
    ctx = _ctx()
    from handlers.preflight import _check_dedup

    _check_dedup(ctx, "refactor handler", ["auth/login.py"], "rev-1")
    assert (
        _dedup_miss_was_revision_bump(ctx, "refactor handler", ["billing/subs.py"], "rev-1")
        is False
    )


def test_classifier_returns_false_when_topic_differs():
    """Entirely different topic → no prefix match."""
    ctx = _ctx()
    from handlers.preflight import _check_dedup

    _check_dedup(ctx, "stripe webhook", ["a.py"], "rev-1")
    assert _dedup_miss_was_revision_bump(ctx, "auth jwt", ["a.py"], "rev-1") is False


def test_classifier_ignores_entries_outside_ttl(monkeypatch):
    """Stale entries (older than _DEDUP_TTL_SECONDS) don't count as
    prior — the cache would've expired them anyway."""
    import handlers.preflight as pf
    import time

    ctx = _ctx()
    # Seed cache, then rewind its timestamp past the TTL.
    pf._check_dedup(ctx, "stripe webhook", ["a.py"], "rev-1")
    topics = ctx._sync_state["preflight_topics"]
    for k in list(topics.keys()):
        topics[k] = time.time() - (pf._DEDUP_TTL_SECONDS + 60)

    assert _dedup_miss_was_revision_bump(ctx, "stripe webhook", ["a.py"], "rev-2") is False


def test_classifier_returns_false_for_identical_keys():
    """If the current call's key matches an existing entry exactly, it
    would have been a cache HIT — not a miss — so the classifier
    correctly returns False (would never be invoked in production)."""
    ctx = _ctx()
    from handlers.preflight import _check_dedup

    _check_dedup(ctx, "stripe webhook", ["a.py"], "rev-1")
    assert _dedup_miss_was_revision_bump(ctx, "stripe webhook", ["a.py"], "rev-1") is False


def test_classifier_returns_false_when_sync_state_missing():
    """Defensive — ctx without _sync_state can't have a prior entry."""
    ctx = SimpleNamespace()
    assert _dedup_miss_was_revision_bump(ctx, "topic", ["a.py"], "rev-1") is False


# ── End-to-end telemetry emission ────────────────────────────────────


def _wire_common_mocks(monkeypatch):
    """Mock the bits handle_preflight needs except for the seam under
    test (write_dedup_event), which the test inspects."""
    import handlers.preflight as pf
    import handlers.sync_middleware as sm
    import ledger.queries as lq

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
    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)


def _ctx_for_handler(sync_state: dict) -> SimpleNamespace:
    ledger = MagicMock()
    ledger.get_decisions_for_files = AsyncMock(return_value=[])
    inner = MagicMock()
    inner._client = MagicMock()
    ledger._inner = inner
    return SimpleNamespace(
        ledger=ledger,
        guided_mode=False,
        _sync_state=sync_state,
    )


def test_telemetry_bypass_emits_bypassed_revision_unknown(monkeypatch):
    """Revision lookup → None → handler emits one
    preflight_dedup_decision event with reason=bypassed_revision_unknown."""
    import handlers.preflight as pf
    import ledger.queries as lq

    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value=None))
    _wire_common_mocks(monkeypatch)

    captured: list[tuple[str, str, str | None]] = []

    def _capture(reason, session_id, preflight_id=None):
        captured.append((reason, session_id, preflight_id))

    monkeypatch.setattr(pf, "write_dedup_event", _capture)

    ctx = _ctx_for_handler({})
    asyncio.run(pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["a.py"]))

    bypass_events = [e for e in captured if e[0] == "bypassed_revision_unknown"]
    assert len(bypass_events) == 1, (
        f"expected exactly one bypassed_revision_unknown event, got {captured!r}"
    )


def test_telemetry_revision_bump_emits_invalidated_by_revision_bump(monkeypatch):
    """Two calls: same topic + same paths, revision changes between them.
    Second call must emit one preflight_dedup_decision event with
    reason=invalidated_by_revision_bump (the M7a/c signal)."""
    import handlers.preflight as pf
    import ledger.queries as lq

    # Different revision per call — first "rev-1", second "rev-2".
    revisions = iter(["rev-1", "rev-2"])
    monkeypatch.setattr(
        lq,
        "get_ledger_revision",
        AsyncMock(side_effect=lambda *_a, **_kw: next(revisions)),
    )
    _wire_common_mocks(monkeypatch)

    captured: list[tuple[str, str, str | None]] = []

    def _capture(reason, session_id, preflight_id=None):
        captured.append((reason, session_id, preflight_id))

    monkeypatch.setattr(pf, "write_dedup_event", _capture)

    sync_state: dict = {}
    ctx1 = _ctx_for_handler(sync_state)
    asyncio.run(pf.handle_preflight(ctx=ctx1, topic="stripe webhook", file_paths=["a.py"]))
    ctx2 = _ctx_for_handler(sync_state)  # reuses sync_state — same session
    asyncio.run(pf.handle_preflight(ctx=ctx2, topic="stripe webhook", file_paths=["a.py"]))

    bump_events = [e for e in captured if e[0] == "invalidated_by_revision_bump"]
    assert len(bump_events) == 1, (
        f"expected exactly one invalidated_by_revision_bump event, got {captured!r}"
    )


def test_telemetry_first_call_emits_nothing(monkeypatch):
    """A fresh-session first call (no prior entry, revision known) is
    not a revision-bump invalidation — no telemetry emitted."""
    import handlers.preflight as pf
    import ledger.queries as lq

    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value="stable-rev-1"))
    _wire_common_mocks(monkeypatch)

    captured: list[tuple[str, str, str | None]] = []

    def _capture(reason, session_id, preflight_id=None):
        captured.append((reason, session_id, preflight_id))

    monkeypatch.setattr(pf, "write_dedup_event", _capture)

    ctx = _ctx_for_handler({})
    asyncio.run(pf.handle_preflight(ctx=ctx, topic="stripe webhook", file_paths=["a.py"]))

    assert captured == [], f"first call must not emit dedup events, got {captured!r}"


def test_telemetry_cache_hit_emits_nothing(monkeypatch):
    """A cache HIT (same key) returns recently_checked but is NOT
    instrumented in Phase 5 — only invalidations matter for the
    'is the new key doing useful work' question."""
    import handlers.preflight as pf
    import ledger.queries as lq

    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value="stable-rev-1"))
    _wire_common_mocks(monkeypatch)

    captured: list[tuple[str, str, str | None]] = []

    def _capture(reason, session_id, preflight_id=None):
        captured.append((reason, session_id, preflight_id))

    monkeypatch.setattr(pf, "write_dedup_event", _capture)

    sync_state: dict = {}
    asyncio.run(
        pf.handle_preflight(
            ctx=_ctx_for_handler(sync_state),
            topic="stripe webhook",
            file_paths=["a.py"],
        )
    )
    response = asyncio.run(
        pf.handle_preflight(
            ctx=_ctx_for_handler(sync_state),
            topic="stripe webhook",
            file_paths=["a.py"],
        )
    )
    # Confirm we got the hit (sanity).
    assert response.reason == "recently_checked"
    # No telemetry events should have fired across both calls.
    assert captured == [], f"cache hit must not emit dedup events, got {captured!r}"


def test_telemetry_file_paths_shift_does_not_emit_revision_bump(monkeypatch):
    """M7b — same topic + different file_paths. The dedup correctly
    invalidates via the file_paths component of the key, but Phase 5
    telemetry intentionally does NOT count this as a revision bump
    (it's a different signal class)."""
    import handlers.preflight as pf
    import ledger.queries as lq

    monkeypatch.setattr(lq, "get_ledger_revision", AsyncMock(return_value="stable-rev-1"))
    _wire_common_mocks(monkeypatch)

    captured: list[tuple[str, str, str | None]] = []

    def _capture(reason, session_id, preflight_id=None):
        captured.append((reason, session_id, preflight_id))

    monkeypatch.setattr(pf, "write_dedup_event", _capture)

    sync_state: dict = {}
    asyncio.run(
        pf.handle_preflight(
            ctx=_ctx_for_handler(sync_state),
            topic="refactor handler",
            file_paths=["auth/login.py"],
        )
    )
    asyncio.run(
        pf.handle_preflight(
            ctx=_ctx_for_handler(sync_state),
            topic="refactor handler",
            file_paths=["billing/subs.py"],
        )
    )

    bump_events = [e for e in captured if e[0] == "invalidated_by_revision_bump"]
    assert bump_events == [], (
        f"file_paths shift must not be classified as revision bump, got {captured!r}"
    )


# ── write_dedup_event direct contract ────────────────────────────────


def test_write_dedup_event_noops_when_telemetry_disabled(monkeypatch, tmp_path):
    """The shared no-op-when-disabled contract every telemetry writer
    honors."""
    import preflight_telemetry as pt

    monkeypatch.setenv("BICAMERAL_TELEMETRY", "0")
    appended: list[dict] = []

    def _spy_append(path, record):
        appended.append(record)

    monkeypatch.setattr(pt, "_append", _spy_append)
    pt.write_dedup_event(reason="invalidated_by_revision_bump", session_id="s")
    assert appended == []


def test_write_dedup_event_writes_record_when_telemetry_enabled(monkeypatch, tmp_path):
    """When enabled, write one row with the expected shape."""
    import preflight_telemetry as pt

    monkeypatch.setenv("BICAMERAL_TELEMETRY", "preflight")
    monkeypatch.setenv("HOME", str(tmp_path))

    appended: list[dict] = []

    def _spy_append(path, record):
        appended.append(record)

    monkeypatch.setattr(pt, "_append", _spy_append)

    pt.write_dedup_event(
        reason="invalidated_by_revision_bump",
        session_id="session-xyz",
        preflight_id="pf-123",
    )

    assert len(appended) == 1
    rec = appended[0]
    assert rec["event_type"] == "preflight_dedup_decision"
    assert rec["reason"] == "invalidated_by_revision_bump"
    assert rec["session_id"] == "session-xyz"
    assert rec["preflight_id"] == "pf-123"
    assert "ts" in rec


def test_write_dedup_event_omits_preflight_id_when_unset(monkeypatch, tmp_path):
    """Empty / None preflight_id is not included in the record (cleaner
    schema for events from sessions without telemetry preflight ids)."""
    import preflight_telemetry as pt

    monkeypatch.setenv("BICAMERAL_TELEMETRY", "preflight")
    monkeypatch.setenv("HOME", str(tmp_path))

    appended: list[dict] = []

    def _spy_append(path, record):
        appended.append(record)

    monkeypatch.setattr(pt, "_append", _spy_append)

    pt.write_dedup_event(
        reason="bypassed_revision_unknown",
        session_id="session-abc",
    )

    assert len(appended) == 1
    assert "preflight_id" not in appended[0]
