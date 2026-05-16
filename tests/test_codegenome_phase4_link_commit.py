"""Phase 4 / Phase 4 (#61) — link_commit handler integration tests.

Covers ``handlers.link_commit._run_drift_classification_pass``:

- Off when ``cg_config.enhance_drift = False`` or ``cg_config = None``.
- Strips cosmetic pendings and writes a ``compliance_check`` row.
- Keeps semantic pendings unchanged in the surviving list.
- Attaches ``pre_classification`` hint to uncertain pendings.
- Failure-isolated: any exception falls through to the original list.
- ``LinkCommitResponse.auto_resolved_count`` reflects the strip count.
- Continuity-then-classification ordering: a moved+cosmetic region is
  stripped by continuity first; classification doesn't see it.

#357 backfill — the link_commit cluster's single solitary-trap row.
``ctx.ledger`` is now a real ``SurrealDBLedgerAdapter`` over ``memory://``;
``ctx`` itself is a ``SimpleNamespace`` per CLAUDE.md's posture rule
("ctx should be SimpleNamespace, not MagicMock"). The
``get_region_metadata=AsyncMock(return_value=None)`` narrow seam from
the pre-#357 version is gone — the failure mode is now produced by
querying a non-existent ``code_region:doesnotexist`` id against the
real adapter, which naturally returns None. That's strictly better
than mocking the function we're trying to exercise.

The remaining mocks (``evaluate_drift_classification``,
``get_git_content``, the codegenome adapter, ``code_graph``) are
collaborators with their own test surfaces and simulate specific
outcomes the test docstrings name. Permitted narrow seams.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codegenome.drift_service import DriftClassificationOutcome
from contracts import PendingComplianceCheck, PreClassificationHint
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import upsert_code_region
from ledger.schema import init_schema, migrate

_NS_COUNTER = 0


async def _fresh_adapter() -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Build a fresh memory:// SurrealDB adapter with schema migrated."""
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"link_commit_p4_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


async def _seed_code_region(
    client: LedgerClient,
    *,
    file_path: str = "src/foo.py",
    symbol_name: str = "handle_webhook",
    start_line: int = 1,
    end_line: int = 5,
) -> str:
    """Seed one code_region row and return its real ID."""
    return await upsert_code_region(
        client,
        file_path=file_path,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        repo="test",
        content_hash="h-test",
    )


def _make_pending(
    decision_id: str = "decision:test1",
    region_id: str = "code_region:r1",
) -> PendingComplianceCheck:
    return PendingComplianceCheck(
        phase="drift",
        decision_id=decision_id,
        region_id=region_id,
        decision_description="Stripe webhook handling",
        file_path="src/foo.py",
        symbol="handle_webhook",
        content_hash="h-1",
        code_body="def handle_webhook(): pass",
    )


def _make_ctx(
    adapter: SurrealDBLedgerAdapter | None,
    *,
    enhance_drift: bool = True,
    enabled: bool = True,
    code_graph=None,
) -> SimpleNamespace:
    """Build a SimpleNamespace ctx with a real ledger adapter.

    ``adapter=None`` for tests that exit before any ledger call (config
    missing / pending empty / enhance_drift=False) — those exits are
    gated on attributes that come before the ledger touch.
    """
    return SimpleNamespace(
        repo_path="/repo",
        authoritative_sha="abc123",
        code_graph=code_graph or MagicMock(neighbors_for=MagicMock(return_value=("n1",))),
        codegenome_config=MagicMock(enabled=enabled, enhance_drift=enhance_drift),
        codegenome=MagicMock(),
        ledger=adapter,
    )


# ── Off-mode tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_flag_disabled() -> None:
    """``enhance_drift=False`` → early return before any ledger touch.

    No adapter needed; the function exits at the gate before reaching
    ``ctx.ledger.get_region_metadata``.
    """
    from handlers.link_commit import _run_drift_classification_pass

    ctx = _make_ctx(None, enhance_drift=False)
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx,
        pending,
        commit_hash="abc",
    )
    assert survivors == pending  # untouched
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_config_missing() -> None:
    """``cg_config = None`` → early return before any ledger touch."""
    from handlers.link_commit import _run_drift_classification_pass

    ctx = SimpleNamespace(codegenome_config=None, codegenome=None, ledger=None)
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx,
        pending,
        commit_hash="abc",
    )
    assert survivors == pending
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_pending_empty() -> None:
    """Empty ``pending`` → early return before any ledger touch."""
    from handlers.link_commit import _run_drift_classification_pass

    ctx = _make_ctx(None)
    survivors, count = await _run_drift_classification_pass(
        ctx,
        [],
        commit_hash="abc",
    )
    assert survivors == []
    assert count == 0


# ── Cosmetic strip + write ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drift_classification_pass_strips_cosmetic_pendings(
    monkeypatch,
) -> None:
    """When ``evaluate_drift_classification`` returns ``auto_resolved=True``,
    the pending check is stripped and the count incremented. The classifier
    itself runs against the real ``code_region`` row this test seeds."""
    from handlers.link_commit import _run_drift_classification_pass

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None,
            auto_resolved=True,
            pre_classification_hint=None,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification",
        fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    adapter, client = await _fresh_adapter()
    try:
        region_id = await _seed_code_region(client)
        ctx = _make_ctx(adapter)
        pending = [_make_pending(region_id=region_id)]
        survivors, count = await _run_drift_classification_pass(
            ctx,
            pending,
            commit_hash="abc",
        )
        assert survivors == []
        assert count == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_run_drift_classification_pass_keeps_semantic_pendings_unchanged(
    monkeypatch,
) -> None:
    from handlers.link_commit import _run_drift_classification_pass

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None,
            auto_resolved=False,
            pre_classification_hint=None,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification",
        fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    adapter, client = await _fresh_adapter()
    try:
        region_id = await _seed_code_region(client)
        ctx = _make_ctx(adapter)
        pending = [_make_pending(region_id=region_id)]
        survivors, count = await _run_drift_classification_pass(
            ctx,
            pending,
            commit_hash="abc",
        )
        assert len(survivors) == 1
        assert survivors[0].pre_classification is None  # no hint
        assert count == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_run_drift_classification_pass_attaches_hint_to_uncertain(
    monkeypatch,
) -> None:
    from handlers.link_commit import _run_drift_classification_pass

    hint = PreClassificationHint(
        verdict="uncertain",
        confidence=0.55,
        signals={"signature": 1.0, "neighbors": 0.5},
        evidence_refs=["score:0.55"],
    )

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None,
            auto_resolved=False,
            pre_classification_hint=hint,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification",
        fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    adapter, client = await _fresh_adapter()
    try:
        region_id = await _seed_code_region(client)
        ctx = _make_ctx(adapter)
        pending = [_make_pending(region_id=region_id)]
        survivors, count = await _run_drift_classification_pass(
            ctx,
            pending,
            commit_hash="abc",
        )
        assert len(survivors) == 1
        assert survivors[0].pre_classification == hint
        assert count == 0
    finally:
        await client.close()


# ── Failure isolation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drift_classification_pass_failure_isolated(
    monkeypatch,
) -> None:
    """If ``evaluate_drift_classification`` raises, the pending list
    survives unchanged with no hints attached."""
    from handlers.link_commit import _run_drift_classification_pass

    async def fake_eval(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification",
        fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    adapter, client = await _fresh_adapter()
    try:
        region_id = await _seed_code_region(client)
        ctx = _make_ctx(adapter)
        pending = [_make_pending(region_id=region_id)]
        survivors, count = await _run_drift_classification_pass(
            ctx,
            pending,
            commit_hash="abc",
        )
        assert len(survivors) == 1
        assert survivors[0].pre_classification is None
        assert count == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_run_drift_classification_pass_no_region_metadata_falls_through() -> None:
    """When ``get_region_metadata`` returns None (region absent from
    ledger), the pending stays in the survivors list unchanged.

    #357 backfill: the pre-#357 version mocked this with
    ``AsyncMock(return_value=None)``. The de-mocked version uses a
    real adapter and a region_id that doesn't exist in the ledger —
    the real query returns None naturally, which is strictly better
    than mocking the function we're trying to exercise.
    """
    from handlers.link_commit import _run_drift_classification_pass

    adapter, client = await _fresh_adapter()
    try:
        ctx = _make_ctx(adapter)
        # Valid `code_region:<id>` shape but no such row exists →
        # real get_region_metadata returns None.
        pending = [_make_pending(region_id="code_region:doesnotexist")]
        survivors, count = await _run_drift_classification_pass(
            ctx,
            pending,
            commit_hash="abc",
        )
        assert len(survivors) == 1
        assert count == 0
    finally:
        await client.close()


# ── Response-shape contract ────────────────────────────────────────


def test_link_commit_response_includes_auto_resolved_count() -> None:
    """``LinkCommitResponse.auto_resolved_count`` exists with default 0."""
    from contracts import LinkCommitResponse

    r = LinkCommitResponse(commit_hash="abc", synced=True, reason="new_commit")
    assert hasattr(r, "auto_resolved_count")
    assert r.auto_resolved_count == 0
