"""v0.4.18 — bicameral.doctor regression tests.

Covers the auto-detect composition tool that replaces bicameral.drift
as the user-facing "check for drift" entry point. Three scopes:

  - ``scope="file"``  — file_path given, delegates to handle_detect_drift
  - ``scope="branch"`` — no file, runs scan_branch + ledger summary
  - ``scope="empty"`` — no file AND empty ledger AND empty range

Also includes:
  - Hint merging across sub-scans (dedup by kind, union refs,
    ``blocking`` promoted if any input is blocking)
  - Server-level guard that bicameral.drift is fully removed from
    the tool list
  - Regression that ``handle_detect_drift`` is still importable as
    an internal helper (doctor depends on it for file scope)
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from contracts import (
    ActionHint,
    DetectDriftResponse,
    DoctorLedgerSummary,
    DoctorResponse,
    ScanBranchResponse,
)
from handlers.detect_drift import handle_detect_drift
from handlers.doctor import _compose_action_hints, handle_doctor
from handlers.ingest import handle_ingest


# ── Layer 1: pure composition logic ─────────────────────────────────


def test_compose_action_hints_dedups_by_kind():
    """Same kind from two sub-scans collapses to one hint; refs
    unioned and stable; blocking promoted on any-true input."""
    h_file = ActionHint(
        kind="review_drift", message="advisory", blocking=False, refs=["i1"],
    )
    h_branch = ActionHint(
        kind="review_drift", message="stronger", blocking=True, refs=["i2", "i1"],
    )
    merged = _compose_action_hints([h_file], [h_branch])
    assert len(merged) == 1
    out = merged[0]
    assert out.kind == "review_drift"
    assert out.blocking is True
    assert out.refs == ["i1", "i2"]


def test_compose_action_hints_distinct_kinds_both_pass_through():
    """Two different kinds both survive."""
    h1 = ActionHint(kind="review_drift", message="a", blocking=False, refs=[])
    h2 = ActionHint(kind="ground_decision", message="b", blocking=False, refs=[])
    merged = _compose_action_hints([h1], [h2])
    assert len(merged) == 2
    kinds = sorted(m.kind for m in merged)
    assert kinds == ["ground_decision", "review_drift"]


def test_compose_action_hints_empty_inputs():
    """No hints in → no hints out."""
    assert _compose_action_hints([], []) == []


def test_doctor_response_empty_scope_shape():
    """scope=empty response has all sub-fields None."""
    resp = DoctorResponse(scope="empty")
    assert resp.file_scan is None
    assert resp.branch_scan is None
    assert resp.ledger_summary is None
    assert resp.action_hints == []


# ── Layer 2: logic tests with stubbed sub-handlers ─────────────────


class _FakeCtx:
    """Minimal ctx for handler logic tests — carries a ledger object
    that the sub-handlers stub around."""
    def __init__(self, decisions: list[dict] | None = None, repo_path: str = "/tmp/fake"):
        self.repo_path = repo_path
        self.guided_mode = False
        self.ledger = _FakeLedger(decisions or [])


class _FakeLedger:
    def __init__(self, decisions: list[dict]):
        self._decisions = decisions

    async def get_all_decisions(self, filter: str = "all") -> list[dict]:
        return list(self._decisions)


@pytest.mark.asyncio
async def test_doctor_empty_scope_when_everything_empty(monkeypatch):
    """No file, empty branch scan, empty ledger → scope="empty"."""
    ctx = _FakeCtx(decisions=[])

    # Stub scan_branch to return an empty response
    async def _empty_scan(ctx, **kwargs):
        return ScanBranchResponse(
            base_ref="main", head_ref="HEAD",
            sweep_scope="range_diff", range_size=0,
            source="HEAD", decisions=[], files_changed=[],
            drifted_count=0, pending_count=0, ungrounded_count=0, reflected_count=0,
            undocumented_symbols=[], action_hints=[],
        )
    monkeypatch.setattr("handlers.doctor.handle_scan_branch", _empty_scan)

    resp = await handle_doctor(ctx)
    assert resp.scope == "empty"
    assert resp.branch_scan is None
    assert resp.ledger_summary is None


@pytest.mark.asyncio
async def test_doctor_branch_scope_populates_ledger_summary(monkeypatch):
    """Branch scope: scan_branch returns something, ledger has decisions,
    doctor composes both into DoctorResponse."""
    # Ledger-wide state: 5 decisions across statuses
    ctx = _FakeCtx(decisions=[
        {"status": "drifted"},
        {"status": "drifted"},
        {"status": "reflected"},
        {"status": "pending"},
        {"status": "ungrounded"},
    ])

    branch = ScanBranchResponse(
        base_ref="main", head_ref="HEAD",
        sweep_scope="range_diff", range_size=3,
        source="HEAD", decisions=[], files_changed=["a.py", "b.py", "c.py"],
        drifted_count=1, pending_count=0, ungrounded_count=0, reflected_count=0,
        undocumented_symbols=[],
        action_hints=[
            ActionHint(
                kind="review_drift", message="advisory", blocking=False, refs=["i1"],
            ),
        ],
    )

    async def _stub_scan(ctx, **kwargs):
        return branch
    monkeypatch.setattr("handlers.doctor.handle_scan_branch", _stub_scan)

    resp = await handle_doctor(ctx)
    assert resp.scope == "branch"
    assert resp.branch_scan is branch
    assert resp.ledger_summary is not None
    assert resp.ledger_summary.total == 5
    assert resp.ledger_summary.drifted == 2
    assert resp.ledger_summary.reflected == 1
    assert resp.ledger_summary.pending == 1
    assert resp.ledger_summary.ungrounded == 1
    # Hints merged through from the sub-scan
    assert len(resp.action_hints) == 1
    assert resp.action_hints[0].kind == "review_drift"


@pytest.mark.asyncio
async def test_doctor_file_scope_delegates_to_detect_drift(monkeypatch):
    """When file_path is given, doctor runs handle_detect_drift and
    wraps the response under scope=file. No branch scan, no ledger
    summary on this path."""
    ctx = _FakeCtx(decisions=[{"status": "drifted"}])

    # Stub detect_drift
    from contracts import LinkCommitResponse
    fake_drift = DetectDriftResponse(
        file_path="pricing.py",
        sync_status=LinkCommitResponse(
            commit_hash="abc", synced=False, reason="already_synced",
        ),
        source="HEAD",
        decisions=[],
        drifted_count=0,
        pending_count=0,
        undocumented_symbols=[],
    )

    async def _stub_drift(ctx, file_path, use_working_tree):
        assert file_path == "pricing.py"
        return fake_drift
    monkeypatch.setattr("handlers.doctor.handle_detect_drift", _stub_drift)

    resp = await handle_doctor(ctx, file_path="pricing.py")
    assert resp.scope == "file"
    assert resp.file_scan is fake_drift
    assert resp.branch_scan is None
    assert resp.ledger_summary is None  # file scope skips ledger summary


@pytest.mark.asyncio
async def test_doctor_ledger_summary_failure_is_non_fatal(monkeypatch):
    """If get_all_decisions raises, _build_ledger_summary returns a
    zero-count summary and doctor still completes."""
    ctx = _FakeCtx()

    async def _boom(filter="all"):
        raise RuntimeError("ledger unreachable")
    ctx.ledger.get_all_decisions = _boom  # type: ignore[method-assign]

    branch = ScanBranchResponse(
        base_ref="main", head_ref="HEAD",
        sweep_scope="range_diff", range_size=1,
        source="HEAD", decisions=[], files_changed=["x.py"],
        drifted_count=0, pending_count=0, ungrounded_count=0, reflected_count=0,
        undocumented_symbols=[],
    )
    async def _stub_scan(ctx, **kwargs):
        return branch
    monkeypatch.setattr("handlers.doctor.handle_scan_branch", _stub_scan)

    resp = await handle_doctor(ctx)
    assert resp.scope == "branch"
    assert resp.ledger_summary.total == 0


# ── Layer 3: server-level guards ────────────────────────────────────


@pytest.mark.asyncio
async def test_bicameral_drift_is_removed_from_tool_list():
    """Hard-removal regression: v0.4.18 drops bicameral.drift from
    list_tools(). Failing this test means the removal regressed."""
    from server import list_tools
    tools = await list_tools()
    names = {t.name for t in tools}
    assert "bicameral.drift" not in names, (
        "bicameral.drift was supposed to be removed in v0.4.18 but is "
        "still registered"
    )
    assert "bicameral.doctor" in names
    assert "bicameral.scan_branch" in names


def test_handle_detect_drift_still_importable_as_internal_helper():
    """handle_detect_drift is no longer exposed as an MCP tool, but
    it IS the internal implementation of doctor's file-scope path.
    Deleting it would break doctor; this test locks that in."""
    from handlers.detect_drift import handle_detect_drift
    assert callable(handle_detect_drift)
    # Also the pure helper that both drift and scan_branch use
    from handlers.detect_drift import raw_decisions_to_drift_entries
    entries, counts = raw_decisions_to_drift_entries([])
    assert entries == []
    assert counts["drifted"] == 0


# ── Layer 4: integration — real ledger + real git repo ─────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed_repo(repo_root: Path, body: str) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@e.com")
    _git(repo_root, "config", "user.name", "t")
    (repo_root / "pricing.py").write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", ".")
    _git(
        repo_root,
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "seed",
    )


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "repo"
    _seed_repo(
        repo_root,
        """
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0
        """,
    )
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


def _payload(repo: str, description: str) -> dict:
    return {
        "query": description,
        "repo": repo,
        "mappings": [
            {
                "span": {
                    "span_id": "v0418-doctor-0",
                    "source_type": "transcript",
                    "text": description,
                    "source_ref": "v0418-doctor-test",
                    "meeting_date": "2026-04-15",
                },
                "intent": description,
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                        "purpose": "pricing rule",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_doctor_file_scope_end_to_end(_isolated_ledger):
    """Integration: ingest a decision, call doctor with file_path,
    verify the file_scan surfaces the decision."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    await handle_ingest(ctx, _payload(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
    ))

    resp = await handle_doctor(ctx, file_path="pricing.py")
    assert resp.scope == "file"
    assert resp.file_scan is not None
    assert resp.file_scan.file_path == "pricing.py"
    assert len(resp.file_scan.decisions) >= 1
    # Ledger summary is skipped on file scope
    assert resp.ledger_summary is None
    assert resp.branch_scan is None


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_doctor_branch_scope_end_to_end(_isolated_ledger):
    """Integration: ingest a decision, modify the file + commit,
    call doctor with no args, verify branch_scan surfaces the
    changed file and ledger_summary carries repo-wide counts."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    base_sha = _git(_isolated_ledger, "rev-parse", "HEAD")

    await handle_ingest(ctx, _payload(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
    ))

    # Commit a change so the branch range isn't empty
    (_isolated_ledger / "pricing.py").write_text(
        "def calculate_discount(order_total):\n"
        "    if order_total >= 100:\n"
        "        return order_total * 0.15\n"
        "    return 0\n"
    )
    _git(_isolated_ledger, "add", "pricing.py")
    _git(
        _isolated_ledger,
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "bump",
    )

    resp = await handle_doctor(ctx, base_ref=base_sha, head_ref="HEAD")
    assert resp.scope == "branch"
    assert resp.branch_scan is not None
    assert "pricing.py" in resp.branch_scan.files_changed
    assert resp.ledger_summary is not None
    assert resp.ledger_summary.total >= 1
    assert resp.file_scan is None
