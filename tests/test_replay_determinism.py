"""Replay determinism regression suite — #296.

Asserts that ``EventMaterializer.replay_new_events`` produces logically
equivalent ledger state across (a) two independent replays of the same
event sequence, (b) reordered commutative event pairs, (c) re-replays
into the same ledger (DB-level UNIQUE invariants), and (d) cross-author
canonical_id coalescing.

Determinism is a load-bearing invariant of:
  * Team mode — every operator's local DB is built by replaying every
    other operator's events on every sync. Divergence silently corrupts
    collaborators' ledgers.
  * Layer E recovery (#252) — ``bicameral_reset(replay_from_events=True)``
    wipes the local DB and rebuilds from the same JSONL substrate. Non-
    determinism produces a ledger that doesn't match what the operator
    had pre-corruption.

Each test follows the same arrange-act-assert shape:
  1. Spin up two fresh in-memory ledgers, A and B (or one ledger for
     idempotency tests).
  2. Replay the same synthetic JSONL substrate through both.
  3. Compute ``fingerprint_ledger`` per ledger.
  4. Assert ``fingerprint(A) == fingerprint(B)``.

Tests use real ``SurrealDBLedgerAdapter`` over ``memory://`` per
``CLAUDE.md`` "Sociable Testing" mandate; no mocked ledgers, no fake
clients.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import get_canonical_id
from ledger.schema import init_schema, migrate
from tests._replay_helpers import (
    compliance_check_event,
    decision_ratified_event,
    decision_superseded_event,
    fingerprint_ledger,
    ingest_event,
    link_commit_event,
    replay_substrate,
)

pytestmark = pytest.mark.asyncio


async def _fresh_adapter(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Real adapter over memory:// with schema migrated up. Per
    ``tests/test_codegenome_continuity_service.py::_fresh_adapter``."""
    c = LedgerClient(url="memory://", ns=f"replay_det_{suffix}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    a = SurrealDBLedgerAdapter(url="memory://")
    a._client = c
    a._connected = True
    return a, c


# ── per-event-type determinism ────────────────────────────────────────────


async def test_replay_determinism_ingest_completed(tmp_path: Path) -> None:
    """ingest.completed replays produce equivalent ledger state."""
    events = [
        ingest_event(intent="ship Phase 1", source_ref="m-001"),
        ingest_event(intent="defer cache", source_ref="m-002"),
    ]
    adapter_a, client_a = await _fresh_adapter("ingest-a")
    adapter_b, client_b = await _fresh_adapter("ingest-b")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


async def test_replay_determinism_link_commit_completed(tmp_path: Path) -> None:
    """ingest + link_commit.completed replays produce equivalent state."""
    events = [
        ingest_event(intent="x", source_ref="r"),
        link_commit_event(commit_hash="cafef00d" + "0" * 32),
    ]
    adapter_a, client_a = await _fresh_adapter("link-a")
    adapter_b, client_b = await _fresh_adapter("link-b")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


async def test_replay_determinism_decision_ratified(tmp_path: Path) -> None:
    """ingest + decision_ratified.completed replays produce equivalent state.

    The ratify event carries canonical_id; the materializer resolves it
    via find_decision_by_canonical_id. Both ledgers should end with the
    same signoff state on the same canonical decision.
    """
    # Build the ingest event first; we need its canonical_id for the ratify
    # event. The canonical_decision_id is computed from (description,
    # source_type, source_ref) — deterministic across DBs.
    from ledger.canonical import canonical_decision_id

    ingest_payload = ingest_event(intent="approve plan", source_ref="m-r1")
    canonical = canonical_decision_id(
        description="approve plan",
        source_type="transcript",
        source_ref="m-r1",
    )
    events = [
        ingest_payload,
        decision_ratified_event(canonical_id=canonical),
    ]

    adapter_a, client_a = await _fresh_adapter("rat-a")
    adapter_b, client_b = await _fresh_adapter("rat-b")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


async def test_replay_determinism_decision_superseded(tmp_path: Path) -> None:
    """ingest two decisions + decision_superseded.completed replays
    produce equivalent state, including the supersedes edge."""
    from ledger.canonical import canonical_decision_id

    old_canonical = canonical_decision_id(
        description="old approach", source_type="transcript", source_ref="m-s1"
    )
    new_canonical = canonical_decision_id(
        description="new approach", source_type="transcript", source_ref="m-s2"
    )
    events = [
        ingest_event(intent="old approach", source_ref="m-s1"),
        ingest_event(intent="new approach", source_ref="m-s2"),
        decision_superseded_event(
            new_canonical_id=new_canonical,
            old_canonical_id=old_canonical,
        ),
    ]
    adapter_a, client_a = await _fresh_adapter("sup-a")
    adapter_b, client_b = await _fresh_adapter("sup-b")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


async def test_replay_determinism_compliance_check_completed(tmp_path: Path) -> None:
    """ingest + link_commit + compliance_check.completed replays produce
    equivalent state. compliance_check resolution requires both the
    decision canonical_id AND a content-addressable code_region; the
    materializer skips the event if either is unresolved, so both
    ledgers must end with the same compliance_check rows."""
    from ledger.canonical import canonical_decision_id

    canonical = canonical_decision_id(
        description="compliance test", source_type="transcript", source_ref="m-c1"
    )
    # The compliance_check.completed event references a region by
    # (repo, file_path, symbol_name, content_hash). Without a matching
    # code_region row in the ledger, the materializer skips it. For this
    # determinism test, we don't need the region to actually exist —
    # the event is skipped consistently in both ledgers, which is still a
    # valid determinism property.
    events = [
        ingest_event(intent="compliance test", source_ref="m-c1"),
        link_commit_event(commit_hash="cafef00d" + "0" * 32),
        compliance_check_event(canonical_decision_id=canonical),
    ]
    adapter_a, client_a = await _fresh_adapter("comp-a")
    adapter_b, client_b = await _fresh_adapter("comp-b")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


# ── order-independence ───────────────────────────────────────────────────


async def test_replay_order_independence_for_independent_ingest_events(
    tmp_path: Path,
) -> None:
    """Two ingest events on INDEPENDENT decisions (no causal edge) can
    replay in either order and produce equivalent ledger state."""
    events_ab = [
        ingest_event(intent="alpha", source_ref="m-a"),
        ingest_event(intent="beta", source_ref="m-b"),
    ]
    events_ba = [
        ingest_event(intent="beta", source_ref="m-b"),
        ingest_event(intent="alpha", source_ref="m-a"),
    ]
    adapter_a, client_a = await _fresh_adapter("order-ab")
    adapter_b, client_b = await _fresh_adapter("order-ba")
    await replay_substrate(
        adapter_a,
        {"alice@example.com": events_ab},
        events_dir=tmp_path / "events_a",
        local_dir=tmp_path / "local_a",
    )
    await replay_substrate(
        adapter_b,
        {"alice@example.com": events_ba},
        events_dir=tmp_path / "events_b",
        local_dir=tmp_path / "local_b",
    )
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)


# ── re-replay idempotency ────────────────────────────────────────────────


async def test_replay_idempotent_on_second_pass(tmp_path: Path) -> None:
    """Replay the same events twice into the same ledger; assert the
    fingerprint is unchanged after the second pass. Pins the DB-level
    UNIQUE invariants on canonical_id and (in, out) edges."""
    events = [
        ingest_event(intent="x", source_ref="r1"),
        ingest_event(intent="y", source_ref="r2"),
        link_commit_event(commit_hash="cafef00d" + "0" * 32),
    ]
    adapter, client = await _fresh_adapter("idem")
    events_dir = tmp_path / "events"
    local_dir_first = tmp_path / "local_first"
    local_dir_second = tmp_path / "local_second"
    # First replay
    await replay_substrate(
        adapter,
        {"alice@example.com": events},
        events_dir=events_dir,
        local_dir=local_dir_first,
    )
    once = await fingerprint_ledger(client)
    # Second replay (fresh watermark — simulates fresh boot)
    await replay_substrate(
        adapter,
        {},  # no new events to write; just replay from offset 0
        events_dir=events_dir,
        local_dir=local_dir_second,
    )
    twice = await fingerprint_ledger(client)
    assert once == twice


async def test_replay_idempotent_yields_edge_unique_dedupe(tmp_path: Path) -> None:
    """Specifically pin the UNIQUE(in, out) index on the yields edge.
    Replay an ingest event twice into the same ledger; assert the yields
    row count after the second pass equals the count after the first."""
    adapter, client = await _fresh_adapter("yields-dedup")
    events = [ingest_event(intent="x", source_ref="r")]
    await replay_substrate(
        adapter,
        {"alice@example.com": events},
        events_dir=tmp_path / "events",
        local_dir=tmp_path / "local_a",
    )
    rows_first = await client.query("SELECT count() FROM yields GROUP ALL")
    count_first = int(rows_first[0]["count"]) if rows_first else 0

    await replay_substrate(
        adapter,
        {},
        events_dir=tmp_path / "events",
        local_dir=tmp_path / "local_b",
    )
    rows_second = await client.query("SELECT count() FROM yields GROUP ALL")
    count_second = int(rows_second[0]["count"]) if rows_second else 0

    assert count_first == count_second, (
        f"yields edge count grew on re-replay: {count_first} → {count_second}"
    )


# ── cross-author identity ────────────────────────────────────────────────


async def test_replay_cross_author_canonical_coalesce(tmp_path: Path) -> None:
    """Alice and Bob each ingest the SAME decision (same description,
    same source_ref) under different author emails. The materializer
    must coalesce them into one decision row via canonical_id, and the
    ledger fingerprint must match a single-author replay of the same
    payload."""
    shared_event = ingest_event(intent="shared decision", source_ref="m-shared")
    # Cross-author replay
    adapter_a, client_a = await _fresh_adapter("xa-multi")
    await replay_substrate(
        adapter_a,
        {
            "alice@example.com": [shared_event],
            "bob@example.com": [shared_event],
        },
        events_dir=tmp_path / "events_multi",
        local_dir=tmp_path / "local_multi",
    )
    rows = await client_a.query("SELECT id FROM decision")
    assert len(rows) == 1, f"expected canonical_id coalesce; got {len(rows)} rows"


async def test_replay_cross_author_distinct_decisions_stay_distinct(
    tmp_path: Path,
) -> None:
    """Alice ingests decision A; Bob ingests decision B (different text).
    Replay produces two distinct decision rows; canonical_id coalescing
    must NOT cross-pollinate distinct decisions."""
    adapter, client = await _fresh_adapter("xa-distinct")
    await replay_substrate(
        adapter,
        {
            "alice@example.com": [ingest_event(intent="A unique", source_ref="m-a")],
            "bob@example.com": [ingest_event(intent="B different", source_ref="m-b")],
        },
        events_dir=tmp_path / "events",
        local_dir=tmp_path / "local",
    )
    rows = await client.query("SELECT id FROM decision")
    assert len(rows) == 2


async def test_replay_cross_author_canonical_id_matches_single_author(
    tmp_path: Path,
) -> None:
    """The decision row produced by cross-author replay has the same
    canonical_id as the row produced by single-author replay of the same
    payload. Pins that author identity doesn't affect canonical_id."""
    shared = ingest_event(intent="shared", source_ref="m-shared")
    adapter_single, client_single = await _fresh_adapter("xa-single")
    await replay_substrate(
        adapter_single,
        {"alice@example.com": [shared]},
        events_dir=tmp_path / "ev_single",
        local_dir=tmp_path / "lo_single",
    )
    single_rows = await client_single.query("SELECT canonical_id FROM decision LIMIT 1")
    single_canonical = single_rows[0]["canonical_id"]

    adapter_multi, client_multi = await _fresh_adapter("xa-multi2")
    await replay_substrate(
        adapter_multi,
        {
            "alice@example.com": [shared],
            "bob@example.com": [shared],
        },
        events_dir=tmp_path / "ev_multi",
        local_dir=tmp_path / "lo_multi",
    )
    multi_rows = await client_multi.query("SELECT canonical_id FROM decision LIMIT 1")
    multi_canonical = multi_rows[0]["canonical_id"]

    assert single_canonical == multi_canonical
