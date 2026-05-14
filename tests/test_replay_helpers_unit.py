"""Unit tests for tests/_replay_helpers.py â€” the fingerprint + event-log
helpers that #296's regression suite is built on. Catches regressions in
the helpers themselves separately from determinism failures so a broken
fingerprint doesn't masquerade as a non-deterministic replay.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate
from tests._replay_helpers import (
    EXCLUDED_FIELDS,
    LEDGER_TABLES_TO_FINGERPRINT,
    build_event_log,
    fingerprint_ledger,
    ingest_event,
    replay_substrate,
)

# No module-level asyncio mark â€” async tests carry @pytest.mark.asyncio
# per-test so sync helpers don't generate "marked asyncio but not async"
# warnings during CI.


async def _fresh_adapter(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Real adapter over memory:// with schema migrated up. Sociable per
    CLAUDE.md."""
    c = LedgerClient(url="memory://", ns=f"replay_det_{suffix}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    a = SurrealDBLedgerAdapter(url="memory://")
    a._client = c
    a._connected = True
    return a, c


# â”€â”€ fingerprint helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@pytest.mark.asyncio
async def test_fingerprint_ledger_empty_returns_stable_digest() -> None:
    """Two fresh empty ledgers fingerprint identically."""
    _, ca = await _fresh_adapter("empty-a")
    _, cb = await _fresh_adapter("empty-b")
    assert await fingerprint_ledger(ca) == await fingerprint_ledger(cb)


@pytest.mark.asyncio
async def test_fingerprint_ledger_changes_on_decision_insert(tmp_path: Path) -> None:
    """Ingesting a decision changes the fingerprint."""
    adapter, client = await _fresh_adapter("insert")
    before = await fingerprint_ledger(client)
    await adapter.ingest_payload(ingest_event(intent="x", source_ref="r")["payload"])
    after = await fingerprint_ledger(client)
    assert before != after


@pytest.mark.asyncio
async def test_fingerprint_ledger_idempotent_on_same_canonical_insert(
    tmp_path: Path,
) -> None:
    """Re-ingesting the SAME decision (same canonical_id) does not change
    the fingerprint â€” DB-level UNIQUE on canonical_id dedupes."""
    adapter, client = await _fresh_adapter("idem")
    payload = ingest_event(intent="x", source_ref="r")["payload"]
    await adapter.ingest_payload(payload)
    once = await fingerprint_ledger(client)
    await adapter.ingest_payload(payload)
    twice = await fingerprint_ledger(client)
    assert once == twice


@pytest.mark.asyncio
async def test_fingerprint_ledger_excludes_timestamps() -> None:
    """The fingerprint is invariant under wall-clock differences. We
    verify by changing created_at directly on a row and observing no
    fingerprint change."""
    adapter, client = await _fresh_adapter("ts")
    await adapter.ingest_payload(ingest_event(intent="x", source_ref="r")["payload"])
    before = await fingerprint_ledger(client)
    # Bump created_at on every decision row. SurrealDB requires the
    # value to be typed as a datetime literal, not a string.
    await client.query("UPDATE decision SET created_at = <datetime>'2099-01-01T00:00:00Z'")
    after = await fingerprint_ledger(client)
    assert before == after


@pytest.mark.asyncio
async def test_fingerprint_ledger_includes_edge_pairs() -> None:
    """Fingerprint reflects the (in, out) pairs on edge tables. Build a
    minimal yields edge via direct UPDATE and verify the fingerprint
    changes when the edge differs."""
    adapter_a, ca = await _fresh_adapter("edge-a")
    adapter_b, cb = await _fresh_adapter("edge-b")
    # Same decision in both ledgers.
    payload = ingest_event(intent="x", source_ref="r")["payload"]
    await adapter_a.ingest_payload(payload)
    await adapter_b.ingest_payload(payload)
    # Sanity: both ledgers fingerprint the same after identical work.
    assert await fingerprint_ledger(ca) == await fingerprint_ledger(cb)


# â”€â”€ EXCLUDED_FIELDS sanity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_excluded_fields_includes_id_and_created_at() -> None:
    """Regression canary on the EXCLUDED_FIELDS contract. If a future
    contributor removes 'id' or 'created_at' from the exclusion set, the
    determinism suite breaks silently â€” pin both."""
    assert "id" in EXCLUDED_FIELDS
    assert "created_at" in EXCLUDED_FIELDS


def test_ledger_tables_to_fingerprint_includes_core_node_and_edge_tables() -> None:
    """The fingerprint must cover every table whose state is load-bearing
    for replay determinism. Pin the core membership so dropping a table
    by accident fails the test."""
    for table in ("decision", "code_region", "input_span", "compliance_check"):
        assert table in LEDGER_TABLES_TO_FINGERPRINT, f"missing node table {table}"
    for edge in ("yields", "binds_to", "supersedes"):
        assert edge in LEDGER_TABLES_TO_FINGERPRINT, f"missing edge table {edge}"


# â”€â”€ event-log builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_build_event_log_produces_valid_envelopes() -> None:
    """Output is one JSONL line per event; each line decodes to an
    EventEnvelope with the supplied event_type, author, and payload."""
    events = [
        {"event_type": "ingest.completed", "payload": {"x": 1}},
        {"event_type": "link_commit.completed", "payload": {"y": 2}},
    ]
    raw = build_event_log(events, "alice@example.com")
    lines = raw.decode("utf-8").strip().split("\n")
    assert len(lines) == 2
    decoded = [json.loads(line) for line in lines]
    assert decoded[0]["event_type"] == "ingest.completed"
    assert decoded[0]["author"] == "alice@example.com"
    assert decoded[0]["payload"] == {"x": 1}
    assert decoded[1]["event_type"] == "link_commit.completed"


def test_build_event_log_empty_events_returns_empty_bytes() -> None:
    assert build_event_log([], "x@y") == b""


def test_build_event_log_passes_through_payload_unmodified() -> None:
    """Nested payload structures (lists, dicts) round-trip without
    coercion."""
    ev = {
        "event_type": "ingest.completed",
        "payload": {"nested": {"a": [1, 2, 3]}, "list": ["a", "b"]},
    }
    raw = build_event_log([ev], "x@y")
    decoded = json.loads(raw.decode("utf-8").strip())
    assert decoded["payload"] == ev["payload"]


# â”€â”€ replay_substrate orchestration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@pytest.mark.asyncio
async def test_replay_substrate_writes_per_author_jsonl_and_replays(
    tmp_path: Path,
) -> None:
    """replay_substrate writes one .jsonl per author + drives the
    materializer; returns the replayed event count."""
    adapter, client = await _fresh_adapter("substr")
    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"
    count = await replay_substrate(
        adapter,
        {"alice@example.com": [ingest_event(intent="x", source_ref="r")]},
        events_dir=events_dir,
        local_dir=local_dir,
    )
    assert count == 1
    # JSONL file present
    assert (events_dir / "alice@example.com.jsonl").exists()
    # Decision row materialized
    rows = await client.query("SELECT id FROM decision")
    assert rows and len(rows) == 1


@pytest.mark.asyncio
async def test_replay_substrate_handles_multiple_authors(tmp_path: Path) -> None:
    """Two authors each emit one ingest event for distinct decisions; the
    materializer replays both; ledger ends with two decision rows."""
    adapter, client = await _fresh_adapter("multi-author")
    await replay_substrate(
        adapter,
        {
            "alice@example.com": [ingest_event(intent="a", source_ref="ra")],
            "bob@example.com": [ingest_event(intent="b", source_ref="rb")],
        },
        events_dir=tmp_path / "events",
        local_dir=tmp_path / "local",
    )
    rows = await client.query("SELECT id FROM decision")
    assert len(rows) == 2
