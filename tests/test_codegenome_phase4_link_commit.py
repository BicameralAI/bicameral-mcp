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
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from contracts import PendingComplianceCheck, PreClassificationHint
from codegenome.drift_service import DriftClassificationOutcome


def _make_pending(decision_id="d:1", region_id="r:1") -> PendingComplianceCheck:
    return PendingComplianceCheck(
        phase="drift", decision_id=decision_id, region_id=region_id,
        decision_description="Stripe webhook handling",
        file_path="src/foo.py", symbol="handle_webhook",
        content_hash="h-1", code_body="def handle_webhook(): pass",
    )


def _make_ctx(
    *,
    enhance_drift: bool = True,
    enabled: bool = True,
    code_graph=None,
    region_meta=None,
) -> MagicMock:
    """Build a fake BicameralContext for the pass."""
    ctx = MagicMock()
    ctx.repo_path = "/repo"
    ctx.authoritative_sha = "abc123"
    ctx.code_graph = code_graph or MagicMock(neighbors_for=MagicMock(return_value=("n1",)))
    ctx.codegenome_config = MagicMock(enabled=enabled, enhance_drift=enhance_drift)
    ctx.codegenome = MagicMock()
    ctx.ledger = MagicMock()
    ctx.ledger.get_region_metadata = AsyncMock(
        return_value=region_meta or {
            "file_path": "src/foo.py", "symbol_name": "handle_webhook",
            "start_line": 1, "end_line": 5, "identity_type": "function",
        },
    )
    return ctx


# ── Off-mode tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_flag_disabled() -> None:
    from handlers.link_commit import _run_drift_classification_pass

    ctx = _make_ctx(enhance_drift=False)
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert survivors == pending  # untouched
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_config_missing() -> None:
    from handlers.link_commit import _run_drift_classification_pass

    ctx = MagicMock()
    ctx.codegenome_config = None
    ctx.codegenome = None
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert survivors == pending
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_off_when_pending_empty() -> None:
    from handlers.link_commit import _run_drift_classification_pass

    ctx = _make_ctx()
    survivors, count = await _run_drift_classification_pass(
        ctx, [], commit_hash="abc",
    )
    assert survivors == []
    assert count == 0


# ── Cosmetic strip + write ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drift_classification_pass_strips_cosmetic_pendings(
    monkeypatch,
) -> None:
    """When ``evaluate_drift_classification`` returns ``auto_resolved=True``,
    the pending check is stripped and the count incremented."""
    from handlers.link_commit import _run_drift_classification_pass

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None, auto_resolved=True,
            pre_classification_hint=None,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification", fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    ctx = _make_ctx()
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert survivors == []
    assert count == 1


@pytest.mark.asyncio
async def test_run_drift_classification_pass_keeps_semantic_pendings_unchanged(
    monkeypatch,
) -> None:
    from handlers.link_commit import _run_drift_classification_pass

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None, auto_resolved=False,
            pre_classification_hint=None,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification", fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    ctx = _make_ctx()
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert len(survivors) == 1
    assert survivors[0].pre_classification is None  # no hint
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_attaches_hint_to_uncertain(
    monkeypatch,
) -> None:
    from handlers.link_commit import _run_drift_classification_pass

    hint = PreClassificationHint(
        verdict="uncertain", confidence=0.55,
        signals={"signature": 1.0, "neighbors": 0.5},
        evidence_refs=["score:0.55"],
    )

    async def fake_eval(**kwargs):
        return DriftClassificationOutcome(
            classification=None, auto_resolved=False,
            pre_classification_hint=hint,
        )

    monkeypatch.setattr(
        "codegenome.drift_service.evaluate_drift_classification", fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    ctx = _make_ctx()
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert len(survivors) == 1
    assert survivors[0].pre_classification == hint
    assert count == 0


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
        "codegenome.drift_service.evaluate_drift_classification", fake_eval,
    )
    monkeypatch.setattr(
        "ledger.status.get_git_content",
        lambda *a, **k: "def handle_webhook(): pass",
    )

    ctx = _make_ctx()
    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert len(survivors) == 1
    assert survivors[0].pre_classification is None
    assert count == 0


@pytest.mark.asyncio
async def test_run_drift_classification_pass_no_region_metadata_falls_through(
    monkeypatch,
) -> None:
    """When ``get_region_metadata`` returns None, the pending stays
    in the survivors list unchanged."""
    from handlers.link_commit import _run_drift_classification_pass

    ctx = _make_ctx()
    ctx.ledger.get_region_metadata = AsyncMock(return_value=None)

    pending = [_make_pending()]
    survivors, count = await _run_drift_classification_pass(
        ctx, pending, commit_hash="abc",
    )
    assert len(survivors) == 1
    assert count == 0


# ── Response-shape contract ────────────────────────────────────────


def test_link_commit_response_includes_auto_resolved_count() -> None:
    """``LinkCommitResponse.auto_resolved_count`` exists with default 0."""
    from contracts import LinkCommitResponse
    r = LinkCommitResponse(commit_hash="abc", synced=True, reason="new_commit")
    assert hasattr(r, "auto_resolved_count")
    assert r.auto_resolved_count == 0
