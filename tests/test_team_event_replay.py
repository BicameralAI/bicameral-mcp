"""Round-trip tests for the team event log replay path (#97).

For each decision-status event type:
    1. Setup team mode: inner adapter (memory://) wrapped in TeamWriteAdapter
    2. Mutate state via the adapter (writes JSONL + DB)
    3. Spin up a fresh adapter pointing at the same JSONL log but a fresh
       memory:// inner DB and a fresh watermark
    4. Connect — triggers materializer replay from offset 0
    5. Assert the fresh DB ends up in the same end-state

Covers the new event vocabulary added in this PR:
    - decision_ratified.completed
    - decision_superseded.completed

Plus regression coverage for the pre-existing emit/replay surface:
    - ingest.completed (decision row + signoff round-trip)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from events.materializer import EventMaterializer
from events.team_adapter import TeamWriteAdapter
from events.writer import EventFileWriter
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import find_decision_by_canonical_id, get_canonical_id


def _build_team_adapter(
    events_dir: Path,
    local_dir: Path,
    author: str = "tester@example.com",
) -> tuple[TeamWriteAdapter, SurrealDBLedgerAdapter]:
    """Wire up an in-memory inner adapter + JSONL event log + materializer."""
    inner = SurrealDBLedgerAdapter(url="memory://")
    writer = EventFileWriter(events_dir, author)
    materializer = EventMaterializer(events_dir, local_dir)
    return TeamWriteAdapter(inner, writer, materializer), inner


def _payload(intent: str, source_ref: str) -> dict:
    """Minimal single-decision payload for ingest_payload."""
    return {
        "query": intent,
        "repo": "test-repo",
        "commit_hash": "deadbeef00000000000000000000000000000000",
        "analyzed_at": "2026-04-29T12:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": f"span-{source_ref}",
                    "source_type": "transcript",
                    "text": intent,
                    "speaker": "Tester",
                    "source_ref": source_ref,
                },
                "intent": intent,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }


@pytest.mark.asyncio
async def test_ratify_event_roundtrip(tmp_path: Path) -> None:
    """A ratify on the live adapter replays into a fresh adapter's DB.

    Cross-DB lookup goes through canonical_id since SurrealDB-generated
    decision ids are per-DB.
    """
    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()

    res = await team_a.ingest_payload(_payload("ratify-roundtrip", "rt-mtg"))
    decision_id_a = res["created_decisions"][0]["decision_id"]
    canonical = await get_canonical_id(inner_a._client, decision_id_a)
    assert canonical, "canonical_id not stamped on decision row"

    signoff = {
        "state": "ratified",
        "signer": "tester",
        "note": "round-trip",
        "ratified_at": "2026-04-29T13:00:00Z",
    }
    await team_a.apply_ratify(decision_id_a, signoff)

    rows = await inner_a._client.query(f"SELECT signoff FROM {decision_id_a} LIMIT 1")
    assert rows and rows[0]["signoff"]["state"] == "ratified"

    # Fresh adapter, same JSONL log, fresh watermark — replay from 0.
    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
    await team_b.connect()

    decision_id_b = await find_decision_by_canonical_id(inner_b._client, canonical)
    assert decision_id_b, "ingest event did not replay (no row for canonical_id)"
    rows_b = await inner_b._client.query(f"SELECT signoff FROM {decision_id_b} LIMIT 1")
    replayed_signoff = rows_b[0].get("signoff") or {}
    assert replayed_signoff.get("state") == "ratified", (
        f"decision_ratified.completed event did not replay; got signoff={replayed_signoff!r}"
    )


@pytest.mark.asyncio
async def test_supersede_event_roundtrip(tmp_path: Path) -> None:
    """A supersede on the live adapter replays edge + frozen signoff."""
    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()

    r_old = await team_a.ingest_payload(_payload("old-decision", "old-mtg"))
    r_new = await team_a.ingest_payload(_payload("new-decision", "new-mtg"))
    old_id_a = r_old["created_decisions"][0]["decision_id"]
    new_id_a = r_new["created_decisions"][0]["decision_id"]
    old_canonical = await get_canonical_id(inner_a._client, old_id_a)
    new_canonical = await get_canonical_id(inner_a._client, new_id_a)
    assert old_canonical and new_canonical

    await team_a.apply_supersede(
        new_id=new_id_a,
        old_id=old_id_a,
        signer="tester",
        signoff_note="superseding for round-trip",
        superseded_at="2026-04-29T13:00:00Z",
        session_id="test-session",
    )

    rows = await inner_a._client.query(f"SELECT signoff FROM {old_id_a} LIMIT 1")
    assert rows and rows[0]["signoff"]["state"] == "superseded"

    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
    await team_b.connect()

    old_id_b = await find_decision_by_canonical_id(inner_b._client, old_canonical)
    new_id_b = await find_decision_by_canonical_id(inner_b._client, new_canonical)
    assert old_id_b and new_id_b, "ingest events did not replay (canonical lookup failed)"

    rows_b = await inner_b._client.query(f"SELECT signoff FROM {old_id_b} LIMIT 1")
    replayed = rows_b[0].get("signoff") or {}
    assert replayed.get("state") == "superseded", (
        f"decision_superseded.completed event did not replay; got signoff={replayed!r}"
    )
    assert replayed.get("superseded_by") == new_id_b

    edge_rows = await inner_b._client.query(
        f"SELECT id FROM supersedes WHERE in = {new_id_b} AND out = {old_id_b} LIMIT 1"
    )
    assert edge_rows, "supersedes edge did not replay"


@pytest.mark.asyncio
async def test_ingest_event_roundtrip_regression(tmp_path: Path) -> None:
    """Pre-existing ingest.completed emit/replay still works.

    This is the regression guard for the existing event vocabulary —
    ensures the new emit calls did not perturb the established path.
    """
    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, _ = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()

    res = await team_a.ingest_payload(_payload("regression-intent", "reg-mtg"))
    decision_id_a = res["created_decisions"][0]["decision_id"]
    canonical = await get_canonical_id(team_a._inner._client, decision_id_a)

    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
    await team_b.connect()

    decision_id_b = await find_decision_by_canonical_id(inner_b._client, canonical)
    assert decision_id_b, "ingest.completed regression — canonical lookup failed"
    rows = await inner_b._client.query(f"SELECT description FROM {decision_id_b} LIMIT 1")
    assert rows, "ingest.completed regression — decision row missing after replay"
    assert "regression-intent" in str(rows[0].get("description", ""))
