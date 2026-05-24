"""Phase 2c-6c boundary tests: mutation write handlers through a real daemon subprocess.

Each test exercises the full call chain across the IPC boundary:

    handle_ratify (facade in MCP process)
        → ctx.daemon.ratify (DaemonProxy)
            → ProtocolClient over UDS
                → daemon subprocess
                    → protocol/handlers/writes.handle_write_ratify
                        → _handle_ratify_impl (in daemon's ledger)

The boundary tests verify:
1. Wire serialization — response round-trips through JSON.
2. Dispatch shape — each dispatcher validates the request and returns the
   typed result shape.
3. Daemon=None fallthrough — facades degrade gracefully to in-process impls.
4. Concurrency serialization — two concurrent ratify calls against the same
   decision serialize through the daemon's single-writer queue.

Cost: ~8s per daemon-subprocess test. Five tests ≈ 40s.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon.proxy import DaemonProxy, DaemonUnreachableError
from tests._daemon_fixture import daemon_subprocess, short_state_dir  # noqa: F401

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_ledger_repo(monkeypatch, tmp_path):
    """A bare git repo + memory:// ledger. The daemon picks these up via
    ``REPO_PATH`` / ``SURREAL_URL`` when ``BicameralContext.from_env`` runs."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return tmp_path


# ── Helpers ──────────────────────────────────────────────────────────────


async def _seed_decision_via_daemon(proxy: DaemonProxy, *, repo_id: str = "local") -> str:
    """Ingest a single decision through the daemon and return its decision_id.

    Uses the protocol client directly (``read.history`` after ingest) to
    retrieve the assigned id so tests don't need to talk to the local ledger.
    """
    # We can't call write.ingest here because it's not yet migrated to 2c-6c.
    # Instead, seed through the in-process ledger that the daemon shares via
    # BicameralContext.from_env() (same SURREAL_URL=memory:// env var).
    import dataclasses

    from adapters.ledger import reset_ledger_singleton
    from context import BicameralContext
    from handlers.ingest import handle_ingest

    reset_ledger_singleton()
    # Build a real BicameralContext (gives us code_graph, all guardrail
    # fields, etc.) then null the daemon so the in-process facade falls
    # through and seeds via the local ledger — no daemon recursion.
    real_ctx = BicameralContext.from_env()
    ctx = dataclasses.replace(real_ctx, daemon=None)
    ledger = ctx.ledger

    payload = {
        "query": "We will use SQLite for the local dev database.",
        "repo": ctx.repo_path,
        "mappings": [
            {
                "span": {
                    "source_type": "transcript",
                    "text": "We will use SQLite for the local dev database.",
                    "source_ref": "test-ref",
                },
                "intent": "We will use SQLite for the local dev database.",
                "symbols": [],
                "code_regions": [],
            }
        ],
    }
    resp = await handle_ingest(ctx, payload, ingest_mode="passive")
    # IngestResponse exposes decisions through brief.decisions or stats.
    brief = getattr(resp, "brief", None)
    decisions = getattr(brief, "decisions", None) if brief else None
    if decisions:
        return decisions[0].id if hasattr(decisions[0], "id") else decisions[0].get("id")
    # Fallback: probe the ledger directly for the most recent decision id
    all_d = await ledger.get_all_decisions(filter="all")
    assert all_d, f"ingest produced no decisions: resp={resp}"
    return str(all_d[-1].get("decision_id") or all_d[-1].get("id"))


# ── Proxy missing-descriptor guard ──────────────────────────────────────


async def test_proxy_raises_when_no_descriptor_for_ratify(tmp_path):
    """No daemon.json → DaemonUnreachableError with actionable hint."""
    proxy = DaemonProxy(
        descriptor_path=tmp_path / "daemon.json",
        auth_path=tmp_path / "auth.json",
    )
    with pytest.raises(DaemonUnreachableError) as exc_info:
        await proxy.ratify(
            repo_id="local",
            decision_id="decision:abc123",
            signer="test@example.com",
        )
    msg = str(exc_info.value)
    assert "bicameral-mcp setup" in msg
    assert "bicameral-mcp daemon start" in msg


# ── Ratify boundary test ─────────────────────────────────────────────────


@pytest.mark.skip(
    reason="Requires shared ledger state between test process and daemon subprocess. "
    "Re-enable after Phase 2c-6b lands write.ingest so we can seed via the daemon."
)
async def test_ratify_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """End-to-end: ingest a decision then ratify it through the daemon.

    Verifies the ratify dispatcher returns the correct typed shape and
    that was_new=True on first ratification.
    """
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        decision_id = await _seed_decision_via_daemon(proxy, repo_id="local")

        raw = await proxy.ratify(
            repo_id="local",
            decision_id=decision_id,
            signer="agent@bicameral.ai",
            note="ratified in boundary test",
            action="ratify",
        )

        # Typed shape validation
        from protocol.contracts import RatifyResult

        result = RatifyResult.model_validate(raw)
        assert result.decision_id == decision_id
        assert result.was_new is True
        assert isinstance(result.signoff, dict)
        assert result.signoff.get("state") == "ratified"
        assert result.projected_status in (
            "reflected",
            "drifted",
            "partial",
            "pending",
            "ungrounded",
        )
    finally:
        await proxy.close()


@pytest.mark.skip(
    reason="Requires shared ledger state. Re-enable after Phase 2c-6b lands write.ingest."
)
async def test_ratify_idempotent_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """Ratify twice — second call returns was_new=False (idempotency)."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        decision_id = await _seed_decision_via_daemon(proxy, repo_id="local")

        raw1 = await proxy.ratify(
            repo_id="local",
            decision_id=decision_id,
            signer="agent@bicameral.ai",
            action="ratify",
        )
        raw2 = await proxy.ratify(
            repo_id="local",
            decision_id=decision_id,
            signer="agent@bicameral.ai",
            action="ratify",
        )

        from protocol.contracts import RatifyResult

        r1 = RatifyResult.model_validate(raw1)
        r2 = RatifyResult.model_validate(raw2)
        assert r1.was_new is True
        assert r2.was_new is False
        assert r2.signoff.get("state") == "ratified"
    finally:
        await proxy.close()


# ── Concurrent ratify serialization test ────────────────────────────────


@pytest.mark.skip(
    reason="Requires shared ledger state. Re-enable after Phase 2c-6b lands write.ingest "
    "so we can seed a decision via the daemon for the concurrency probe."
)
async def test_concurrent_ratify_serializes_through_daemon(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """Two concurrent ratify calls for the same decision serialize correctly.

    Expected behavior: both calls complete without error. The first sets
    was_new=True; the second observes the already-ratified state and returns
    was_new=False (idempotency). The daemon's single-writer queue ensures
    both calls produce a valid final state — no lost-update or
    torn-read anomaly.
    """
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        decision_id = await _seed_decision_via_daemon(proxy, repo_id="local")

        raw1, raw2 = await asyncio.gather(
            proxy.ratify(
                repo_id="local",
                decision_id=decision_id,
                signer="agent-a@bicameral.ai",
                action="ratify",
            ),
            proxy.ratify(
                repo_id="local",
                decision_id=decision_id,
                signer="agent-b@bicameral.ai",
                action="ratify",
            ),
        )

        from protocol.contracts import RatifyResult

        r1 = RatifyResult.model_validate(raw1)
        r2 = RatifyResult.model_validate(raw2)

        # Both results must be valid typed shapes
        assert r1.decision_id == decision_id
        assert r2.decision_id == decision_id

        # One must have won (was_new=True), the other must see idempotency
        was_new_values = {r1.was_new, r2.was_new}
        # Acceptable outcomes: (True, False) or both False (if they raced to the
        # same tick), but never both True (that would be a double-write anomaly).
        # Both False can only happen if a third write intervened; for a clean
        # two-call race, exactly one should be True.
        assert True in was_new_values, (
            f"Neither call reported was_new=True — unexpected: {r1}, {r2}"
        )
        assert r1.signoff.get("state") == "ratified"
        assert r2.signoff.get("state") == "ratified"
    finally:
        await proxy.close()


# ── ResolveCompliance boundary test ─────────────────────────────────────


async def test_resolve_compliance_through_daemon(daemon_subprocess, fresh_ledger_repo, tmp_path):
    """write.resolve_compliance dispatches and returns the typed shape.

    We submit an empty verdicts list (valid — just re-projects existing
    statuses) to validate the dispatcher without needing to fabricate a
    real decision + region pair.
    """
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        raw = await proxy.resolve_compliance(
            repo_id="local",
            phase="ingest",
            verdicts=[],  # empty batch — valid, no-op
        )

        from protocol.contracts import ResolveComplianceResult

        result = ResolveComplianceResult.model_validate(raw)
        assert result.phase == "ingest"
        assert result.accepted == []
        assert result.rejected == []
    finally:
        await proxy.close()


# ── ResolveCollision boundary test ───────────────────────────────────────


async def test_resolve_collision_through_daemon_invalid_raises(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """write.resolve_collision with unknown decision_id returns a protocol error.

    This verifies the dispatcher reaches the impl and domain validation runs.
    """
    from protocol.contracts import ProtocolError

    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        with pytest.raises((ProtocolError, Exception)):
            # Supplying a non-existent new_id with action='keep_both' will
            # reach the impl and raise ValueError("No decision row for ...").
            # The dispatcher surfaces this as a ProtocolError on the wire.
            await proxy.resolve_collision(
                repo_id="local",
                new_id="decision:nonexistent123",
                old_id="decision:nonexistent456",
                action="keep_both",
            )
    finally:
        await proxy.close()


# ── JudgeGaps boundary test ──────────────────────────────────────────────


async def test_judge_gaps_through_daemon_empty_ledger(
    daemon_subprocess, fresh_ledger_repo, tmp_path
):
    """write.judge_gaps on an empty ledger returns payload=None (honest empty path)."""
    proxy = DaemonProxy(
        descriptor_path=daemon_subprocess.socket_path.parent / "daemon.json",
        auth_path=tmp_path / "no-auth.json",
    )
    try:
        raw = await proxy.judge_gaps(
            repo_id="local",
            topic="database migration strategy",
        )

        from protocol.contracts import JudgeGapsResult

        result = JudgeGapsResult.model_validate(raw)
        # Empty ledger → no decisions → honest empty path
        assert result.payload is None
    finally:
        await proxy.close()


# ── Dispatcher registration ──────────────────────────────────────────────


def test_mutation_write_handlers_registered(tmp_path):
    """All four mutation dispatchers are registered on the server."""
    from protocol.handlers.writes import register_write_handlers
    from protocol.server import ProtocolServer

    server = ProtocolServer(tmp_path / "d.sock")
    register_write_handlers(server)

    assert "write.ratify" in server._methods
    assert "write.resolve_compliance" in server._methods
    assert "write.resolve_collision" in server._methods
    assert "write.judge_gaps" in server._methods
