"""L1 exemption guard for codegenome identity writes (#59 + spec-governance).

Per Jin's spec-governance proposal §4.2 + Failure Mode #2: only decisions
explicitly tagged ``decision_level = "L2"`` should enter the codegenome
identity graph. L1 decisions (behavioral commitments) are intentionally
ungrounded at the identity layer; L3 is never tracked; ``None``
(unclassified) is treated as L3 by the tolerant policy.

Without this guard, binding an L1 decision pollutes the identity graph
with subsystem-level matches that erode Phase-3 continuity precision.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from codegenome.config import CodeGenomeConfig
from codegenome.deterministic_adapter import DeterministicCodeGenomeAdapter
from handlers.bind import handle_bind
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

# ── Fixtures ────────────────────────────────────────────────────────────────


async def _fresh_client(suffix):
    c = LedgerClient(url="memory://", ns=f"cg_l1_{suffix}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_decision(client, *, description, level=None):
    """Create a decision row, optionally with ``decision_level``."""
    if level is None:
        rows = await client.query(
            "CREATE decision SET description=$d, source_type='manual', status='ungrounded'",
            {"d": description},
        )
    else:
        rows = await client.query(
            "CREATE decision SET description=$d, source_type='manual', "
            "status='ungrounded', decision_level=$l",
            {"d": description, "l": level},
        )
    return str(rows[0]["id"])


class _CtxWithCodegenome:
    def __init__(self, ledger):
        self.ledger = ledger
        self.repo_path = "/tmp/test-repo"
        self.authoritative_sha = "HEAD"
        self.codegenome = DeterministicCodeGenomeAdapter(repo_path=self.repo_path)
        # Both flags ON — L1 guard is the only thing that should suppress writes.
        self.codegenome_config = CodeGenomeConfig(
            enabled=True,
            write_identity_records=True,
        )


def _stub_bind_dependencies(content_hash="abc123"):
    stack = ExitStack()
    stack.enter_context(patch("ledger.adapter.compute_content_hash", return_value=content_hash))
    stack.enter_context(patch("ledger.status.get_git_content", return_value="def f(): pass\n"))
    stack.enter_context(patch("ledger.status.hash_lines", return_value=content_hash))
    return stack


async def _count_codegenome_rows(client):
    """Return (#code_subject rows, #subject_identity rows, #about edges)."""
    cs = await client.query("SELECT count() AS n FROM code_subject GROUP ALL")
    si = await client.query("SELECT count() AS n FROM subject_identity GROUP ALL")
    ab = await client.query("SELECT count() AS n FROM about GROUP ALL")
    n = lambda rows: int((rows or [{}])[0].get("n", 0)) if rows else 0
    return n(cs), n(si), n(ab)


# ── L2 — identity must be written ───────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_l2_writes_identity():
    """Sanity: L2 decisions still flow through the codegenome write path."""
    client = await _fresh_client("l2")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True
        decision_id = await _seed_decision(client, description="Use SQLite WAL mode", level="L2")
        ctx = _CtxWithCodegenome(adapter)

        with _stub_bind_dependencies("h_l2"):
            resp = await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "ledger/client.py",
                        "symbol_name": "WALWriter",
                        "start_line": 10,
                        "end_line": 30,
                    }
                ],
            )
        assert resp.bindings[0].error is None

        cs, si, ab = await _count_codegenome_rows(client)
        assert cs == 1, "L2 bind must create exactly one code_subject"
        assert si == 1, "L2 bind must create exactly one subject_identity"
        assert ab == 1, "L2 bind must create exactly one decision→about→subject edge"
    finally:
        await client.close()


# ── L1 — exemption must suppress all codegenome writes ──────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_l1_skips_codegenome_writes():
    """L1 decisions (behavioral commitments) must NOT enter the identity graph.

    §4.2 of Jin's spec: L1 decisions are intentionally ungrounded at the
    identity layer. Failure Mode #2: without this guard, L1 fingerprints
    produce subsystem-level matches that pollute relocation tracking.
    """
    client = await _fresh_client("l1")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True
        decision_id = await _seed_decision(
            client,
            description="Users can pause subscription for 90 days",
            level="L1",
        )
        ctx = _CtxWithCodegenome(adapter)

        with _stub_bind_dependencies("h_l1"):
            resp = await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "subscriptions/pause.py",
                        "symbol_name": "pause_subscription",
                        "start_line": 1,
                        "end_line": 20,
                    }
                ],
            )
        # Bind itself succeeds (binds_to + code_region still written —
        # the bind contract is unchanged). Only the codegenome
        # side-effect is suppressed.
        assert resp.bindings[0].error is None

        cs, si, ab = await _count_codegenome_rows(client)
        assert cs == 0, "L1 bind must not create any code_subject row"
        assert si == 0, "L1 bind must not create any subject_identity row"
        assert ab == 0, "L1 bind must not create any decision→about→subject edge"
    finally:
        await client.close()


# ── L3 — tracker says never; identical to L1 at the codegenome layer ────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_l3_skips_codegenome_writes():
    """L3 decisions are never tracked at the identity layer."""
    client = await _fresh_client("l3")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True
        decision_id = await _seed_decision(
            client,
            description="Loop unroll factor 4 in hot path",
            level="L3",
        )
        ctx = _CtxWithCodegenome(adapter)

        with _stub_bind_dependencies("h_l3"):
            await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "vm/eval.py",
                        "symbol_name": "eval_loop",
                        "start_line": 100,
                        "end_line": 200,
                    }
                ],
            )

        cs, si, ab = await _count_codegenome_rows(client)
        assert (cs, si, ab) == (0, 0, 0)
    finally:
        await client.close()


# ── Tolerant default — NULL decision_level treated as L3 (skip) ─────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_unclassified_decision_level_skips_codegenome_writes():
    """Per Q2 tolerant policy: ``decision_level=NULL`` is treated as L3
    (skip the identity write). Existing pre-classification ingest
    payloads remain backward-compatible — classification can be added
    later without re-binding existing decisions.
    """
    client = await _fresh_client("unset")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True
        decision_id = await _seed_decision(
            client,
            description="legacy ungrouped decision",
            level=None,
        )
        ctx = _CtxWithCodegenome(adapter)

        with _stub_bind_dependencies("h_null"):
            await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "x.py",
                        "symbol_name": "x",
                        "start_line": 1,
                        "end_line": 5,
                    }
                ],
            )

        cs, si, ab = await _count_codegenome_rows(client)
        assert (cs, si, ab) == (0, 0, 0)
    finally:
        await client.close()


# ── Bind contract is unchanged regardless of level ──────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_response_shape_unchanged_for_l1():
    """The L1 exemption is a side-effect skip; ``BindResponse`` shape is
    identical to the L2 case (region_id, content_hash, pending_check).
    """
    client = await _fresh_client("shape")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True
        decision_id = await _seed_decision(
            client,
            description="Members can pause subscription",
            level="L1",
        )
        ctx = _CtxWithCodegenome(adapter)

        with _stub_bind_dependencies("h_shape"):
            resp = await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": decision_id,
                        "file_path": "src/x.py",
                        "symbol_name": "x",
                        "start_line": 1,
                        "end_line": 5,
                    }
                ],
            )

        bind = resp.bindings[0]
        assert bind.error is None
        assert bind.region_id  # binds_to + code_region still written
        assert bind.content_hash == "h_shape"
        # PendingComplianceCheck is emitted whenever content_hash is non-empty —
        # L1 doesn't change that side of the bind contract.
        assert bind.pending_compliance_check is not None
    finally:
        await client.close()
