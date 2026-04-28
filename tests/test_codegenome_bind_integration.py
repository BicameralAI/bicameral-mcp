"""Phase 2 integration tests — bind-time identity write path (#59 exit criteria)."""

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


async def _fresh_client(db_suffix):
    c = LedgerClient(url="memory://", ns=f"cg_bind_{db_suffix}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_decision(client, description, level="L2"):
    """Seed a decision; defaults to ``decision_level="L2"`` so the
    codegenome write path runs (L1 is intentionally exempted by
    ``handlers/bind.py`` per the spec-governance proposal §4.2)."""
    if level is None:
        rows = await client.query(
            "CREATE decision SET description = $d, source_type = 'manual', status = 'ungrounded'",
            {"d": description},
        )
    else:
        rows = await client.query(
            "CREATE decision SET description = $d, source_type = 'manual', "
            "status = 'ungrounded', decision_level = $l",
            {"d": description, "l": level},
        )
    return str(rows[0]["id"])


class _CtxWithCodegenome:
    def __init__(self, ledger, *, write_identity_records):
        self.ledger = ledger
        self.repo_path = "/tmp/test-repo"
        self.authoritative_sha = "HEAD"
        self.codegenome = DeterministicCodeGenomeAdapter(repo_path=self.repo_path)
        self.codegenome_config = CodeGenomeConfig(
            enabled=write_identity_records,
            write_identity_records=write_identity_records,
        )


def _stub_bind_dependencies(content_hash="abc123"):
    stack = ExitStack()
    stack.enter_context(patch("ledger.adapter.compute_content_hash", return_value=content_hash))
    stack.enter_context(patch("ledger.status.get_git_content", return_value="def foo():\n    return 1\n"))
    stack.enter_context(patch("ledger.status.hash_lines", return_value=content_hash))
    return stack


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_with_flag_off_writes_no_identity():
    """#59 exit criterion: bind behavior unchanged when write_identity_records=False."""
    client = await _fresh_client("flag_off")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Use BM25 for search")
        ctx = _CtxWithCodegenome(adapter, write_identity_records=False)

        with _stub_bind_dependencies(content_hash="hash_off"):
            resp = await handle_bind(ctx, bindings=[{
                "decision_id": decision_id,
                "file_path": "server.py",
                "symbol_name": "handle_search",
                "start_line": 10,
                "end_line": 30,
            }])

        assert len(resp.bindings) == 1
        assert resp.bindings[0].error is None

        identities = await adapter.find_subject_identities_for_decision(decision_id)
        assert identities == []
        subjects = await client.query("SELECT id FROM code_subject")
        assert subjects == [] or subjects is None
        ids = await client.query("SELECT id FROM subject_identity")
        assert ids == [] or ids is None
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_with_flag_on_writes_identity_and_links_decision():
    """#59 exit criteria: subject_identity created, queryable, content_hash matches region."""
    client = await _fresh_client("flag_on")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Rate-limit checkout")
        ctx = _CtxWithCodegenome(adapter, write_identity_records=True)

        fixed_hash = "deadbeefcafe1234"
        with _stub_bind_dependencies(content_hash=fixed_hash):
            resp = await handle_bind(ctx, bindings=[{
                "decision_id": decision_id,
                "file_path": "checkout/rate_limit.py",
                "symbol_name": "enforce_checkout_rate_limit",
                "start_line": 24,
                "end_line": 67,
            }])

        assert len(resp.bindings) == 1
        bind_result = resp.bindings[0]
        assert bind_result.error is None
        assert bind_result.content_hash == fixed_hash

        identities = await adapter.find_subject_identities_for_decision(decision_id)
        assert len(identities) >= 1
        identity = identities[0]
        assert identity["identity_type"] == "deterministic_location_v1"
        assert identity["model_version"] == "deterministic-location-v1"
        assert identity["address"].startswith("cg:")
        assert identity["content_hash"] == bind_result.content_hash == fixed_hash

        subjects = await client.query("SELECT canonical_name, kind FROM code_subject")
        assert subjects, "code_subject row missing"
        assert any(s.get("canonical_name") == "enforce_checkout_rate_limit" for s in subjects)
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bind_twice_is_idempotent_for_codegenome():
    """Repeat bind for same (decision, span) → exactly one row in each codegenome table."""
    client = await _fresh_client("idem")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "Auth middleware")
        ctx = _CtxWithCodegenome(adapter, write_identity_records=True)

        binding = {
            "decision_id": decision_id,
            "file_path": "auth/mw.py",
            "symbol_name": "require_auth",
            "start_line": 5,
            "end_line": 25,
        }

        with _stub_bind_dependencies(content_hash="h1"):
            await handle_bind(ctx, bindings=[binding])
            await handle_bind(ctx, bindings=[binding])

        identities = await adapter.find_subject_identities_for_decision(decision_id)
        assert len(identities) == 1

        subjects = await client.query("SELECT id FROM code_subject")
        assert len(subjects or []) == 1

        ids = await client.query("SELECT id FROM subject_identity")
        assert len(ids or []) == 1
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_codegenome_failure_does_not_change_bind_response():
    """Side-effect-only contract: identity-write exceptions are caught; bind contract intact."""
    client = await _fresh_client("err")
    try:
        adapter = SurrealDBLedgerAdapter(url="memory://")
        adapter._client = client
        adapter._connected = True

        decision_id = await _seed_decision(client, "x")
        ctx = _CtxWithCodegenome(adapter, write_identity_records=True)

        with patch.object(
            ctx.codegenome, "compute_identity",
            side_effect=RuntimeError("simulated codegenome failure"),
        ), _stub_bind_dependencies(content_hash="h2"):
            resp = await handle_bind(ctx, bindings=[{
                "decision_id": decision_id,
                "file_path": "a.py",
                "symbol_name": "f",
                "start_line": 1,
                "end_line": 5,
            }])

        assert len(resp.bindings) == 1
        assert resp.bindings[0].error is None
        identities = await adapter.find_subject_identities_for_decision(decision_id)
        assert identities == []
    finally:
        await client.close()
