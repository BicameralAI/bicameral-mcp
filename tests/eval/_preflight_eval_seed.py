"""Sociable ledger seeding for the preflight eval harness (#357 Phase B).

This module replaces the AsyncMock/MagicMock scaffolding in
run_preflight_eval.py with real SurrealDB seeding over memory://. The
eval harness used to monkeypatch ledger.queries.get_ledger_revision
with an AsyncMock (line 198 in the pre-#357 version) — exactly the
pattern that hid #309's coalesce parse error from every Phase 4 + 5
test. With this module wired in, the bypass class is no longer
expressible: every ledger call in the eval harness runs real SurrealQL.

Modeled on tests/test_codegenome_continuity_service.py::_fresh_adapter,
the canonical sociable seeding pattern named in CLAUDE.md.
"""

from __future__ import annotations

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import relate_binds_to, relate_context_for, upsert_code_region
from ledger.schema import init_schema, migrate


async def make_real_ledger(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Build a fresh memory:// SurrealDB ledger with schema migrated.

    Each parametrized eval test gets its own namespace so rows don't
    bleed across tests. `suffix` should be unique per test invocation
    (the dataset row id is a good choice).
    """
    client = LedgerClient(url="memory://", ns=f"preflight_eval_{suffix}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


async def seed_decision_pinned_to_file(
    client: LedgerClient,
    *,
    description: str,
    status: str,
    file_path: str,
    symbol: str = "test_symbol",
    signoff: dict | None = None,
) -> str:
    """Seed a decision + code_region + binds_to edge. Returns decision_id.

    Used for dataset rows under `region_decisions` and
    `region_decisions_pinned_to[file_path]` — the entries the handler
    finds via region-anchored retrieval (`get_decisions_for_files`).
    """
    params: dict = {"d": description, "s": status}
    signoff_clause = ""
    if signoff is not None:
        signoff_clause = ", signoff=$so"
        params["so"] = signoff
    rows = await client.query(
        "CREATE decision SET description=$d, status=$s, "
        f"source_type='test', source_ref='eval'{signoff_clause}",
        params,
    )
    decision_id = str(rows[0]["id"])

    region_id = await upsert_code_region(
        client,
        file_path=file_path,
        symbol_name=symbol,
        start_line=1,
        end_line=10,
        repo="test",
        content_hash="h_test",
    )
    await relate_binds_to(client, decision_id, region_id)
    return decision_id


async def seed_decision_with_signoff(
    client: LedgerClient,
    *,
    description: str,
    status: str,
    signoff: dict,
) -> str:
    """Seed a decision with explicit signoff state. No region binding.

    Used for dataset rows under `collision_pending` — the HITL queries
    (`get_collision_pending_decisions`) read decision rows directly via
    `WHERE signoff.state = 'collision_pending'`, not via region traversal,
    so no binds_to edge is needed.
    """
    rows = await client.query(
        "CREATE decision SET description=$d, status=$s, signoff=$so, "
        "source_type='test', source_ref='eval'",
        {"d": description, "s": status, "so": signoff},
    )
    return str(rows[0]["id"])


async def seed_context_pending_ready(
    client: LedgerClient,
    *,
    description: str,
    status: str,
    signoff: dict,
) -> str:
    """Seed a decision with signoff.state='context_pending' AND a
    confirmed context_for edge.

    `get_context_for_ready_decisions` filters on
    `signoff.state = 'context_pending'` AND requires `count(<-context_for
    [WHERE state = 'confirmed']) > 0`. Both conditions must hold for the
    handler to surface the row in the `context_pending_ready` field.
    """
    # The dataset uses signoff.state="context_pending_ready" colloquially
    # but the production filter is 'context_pending'. Force the canonical
    # value so the real query matches.
    canonical_signoff = {**signoff, "state": "context_pending"}
    decision_id = await seed_decision_with_signoff(
        client, description=description, status=status, signoff=canonical_signoff,
    )
    span_rows = await client.query(
        "CREATE input_span SET text='eval_seed', source_type='test', "
        "source_ref='eval', speakers=[], meeting_date=''"
    )
    span_id = str(span_rows[0]["id"])
    await relate_context_for(client, span_id, decision_id, state="confirmed")
    return decision_id


async def reset_for_next_call(client: LedgerClient) -> None:
    """Wipe all decision-graph rows AND advance the revision counter.

    For multi-call dataset rows (M7a/b/c), this is invoked between calls
    so the second call sees the new setup's state and the ledger_revision
    component of the dedup key naturally differs.

    The DEFINE EVENT on `decision` only bumps `bicameral_meta.decision_revision`
    on CREATE/UPDATE — not DELETE. In production every state change is an
    UPDATE that bumps the counter; the wipe-and-reseed pattern here is a
    test shortcut that needs a manual bump to match. The handler observes
    only the counter value, so this is faithful to the production effect.
    """
    await client.execute("DELETE decision")
    await client.execute("DELETE code_region")
    await client.execute("DELETE input_span")
    await client.execute("DELETE binds_to")
    await client.execute("DELETE context_for")
    await client.execute("DELETE yields")
    await client.execute(
        "UPDATE bicameral_meta SET decision_revision = decision_revision + 1"
    )


async def apply_setup_to_ledger(
    client: LedgerClient,
    setup: dict,
) -> None:
    """Seed every decision/HITL row described in `setup`.

    Mirrors the pre-#357 _apply_setup mock-build logic but writes real
    rows. Accepts the same setup dict shape so the dataset file
    (preflight_dataset.jsonl) does not change.
    """
    for d in setup.get("region_decisions", []) or []:
        await seed_decision_pinned_to_file(
            client,
            description=d["description"],
            status=d.get("status", "reflected"),
            file_path=d.get("file_path", "test.py"),
            symbol=d.get("symbol", "test_symbol"),
            signoff=d.get("signoff"),
        )

    pinned = setup.get("region_decisions_pinned_to") or {}
    for fp, decisions in pinned.items():
        for d in decisions:
            await seed_decision_pinned_to_file(
                client,
                description=d["description"],
                status=d.get("status", "reflected"),
                file_path=fp,
                symbol=d.get("symbol", "test_symbol"),
                signoff=d.get("signoff"),
            )

    for d in setup.get("collision_pending", []) or []:
        signoff = d.get("signoff") or {"state": "collision_pending"}
        # Force canonical state — dataset rows occasionally omit it
        signoff = {**signoff, "state": "collision_pending"}
        await seed_decision_with_signoff(
            client,
            description=d["description"],
            status=d.get("status", "pending"),
            signoff=signoff,
        )

    for d in setup.get("context_pending_ready", []) or []:
        signoff = d.get("signoff") or {}
        await seed_context_pending_ready(
            client,
            description=d["description"],
            status=d.get("status", "pending"),
            signoff=signoff,
        )
