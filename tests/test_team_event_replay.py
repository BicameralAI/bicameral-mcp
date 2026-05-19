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
    - compliance_check.completed (#190 — emission + replay + idempotency)

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


# ── #190: compliance_check.completed event ──────────────────────────


async def _seed_code_region(
    client,
    *,
    file_path: str = "src/billing/calc.py",
    symbol_name: str = "calc_total",
    content_hash: str = "sha256:beef0001",
    repo: str = "test-repo",
) -> str:
    """Seed a code_region row directly via SQL. Ingest events don't carry
    region data, so cross-author replay tests need both peers to have
    matching regions (same content_hash) materialized via a separate path
    (e.g. local bind call, repo scan)."""
    rows = await client.query(
        "CREATE code_region SET file_path = $fp, symbol_name = $s, "
        "start_line = 10, end_line = 30, content_hash = $h, repo = $r",
        {"fp": file_path, "s": symbol_name, "h": content_hash, "r": repo},
    )
    return str(rows[0]["id"])


async def _resolve_compliance(
    ledger,
    *,
    decision_id: str,
    region_id: str,
    content_hash: str,
    verdict: str,
    repo: str = "test-repo",
):
    """Helper: invoke handle_resolve_compliance with one verdict."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    ctx = BicameralContext(
        repo_path=repo,
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=ledger,
        code_graph=None,
        drift_analyzer=None,
    )
    v = ComplianceVerdict(
        decision_id=decision_id,
        region_id=region_id,
        content_hash=content_hash,
        verdict=verdict,
        confidence=0.95,
        explanation="test",
    )
    return await handle_resolve_compliance(
        ctx, phase="drift", verdicts=[v], commit_hash=ctx.head_sha
    )


def _read_jsonl_events(events_dir: Path) -> list[dict]:
    """Return all events written to the JSONL log, in order."""
    import json

    out: list[dict] = []
    for p in sorted(events_dir.rglob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


@pytest.mark.asyncio
async def test_resolve_compliance_emits_one_event_per_verdict(tmp_path: Path) -> None:
    """Each accepted ComplianceVerdict produces exactly one
    compliance_check.completed event in the JSONL stream."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"

    team, inner = _build_team_adapter(events_dir, local_dir)
    await team.connect()

    res = await team.ingest_payload(_payload("compliance-emit", "ce-mtg"))
    decision_id = res["created_decisions"][0]["decision_id"]
    region_id = await _seed_code_region(inner._client, content_hash="sha256:beef0001")

    # Build two verdicts on the same region, varying content_hash so the
    # UNIQUE index doesn't collapse them. Each verdict is independently emitted.
    verdicts = [
        ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="sha256:beef0001",
            verdict="compliant",
            confidence="high",
            explanation="v1",
        ),
        ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="sha256:beef0002",
            verdict="drifted",
            confidence="high",
            explanation="v2",
        ),
    ]
    ctx = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=team,
        code_graph=None,
        drift_analyzer=None,
    )
    await handle_resolve_compliance(ctx, phase="drift", verdicts=verdicts, commit_hash=ctx.head_sha)

    events = _read_jsonl_events(events_dir)
    compliance_events = [e for e in events if e.get("event_type") == "compliance_check.completed"]
    assert len(compliance_events) == 2, (
        f"expected 2 compliance_check.completed events, got {len(compliance_events)}: "
        f"{[e.get('event_type') for e in events]}"
    )


@pytest.mark.asyncio
async def test_resolve_compliance_no_emit_in_single_mode(tmp_path: Path) -> None:
    """Single-mode (non-team adapter) writes verdicts to the local DB but
    emits no JSONL events."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"

    # Use the inner adapter directly — NO TeamWriteAdapter wrapping.
    inner = SurrealDBLedgerAdapter(url="memory://")
    await inner.connect()

    res = await inner.ingest_payload(_payload("single-mode", "sm-mtg"))
    decision_id = res["created_decisions"][0]["decision_id"]
    region_id = await _seed_code_region(inner._client, content_hash="sha256:beef0001")

    ctx = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=inner,
        code_graph=None,
        drift_analyzer=None,
    )
    v = ComplianceVerdict(
        decision_id=decision_id,
        region_id=region_id,
        content_hash="sha256:beef0001",
        verdict="compliant",
        confidence="high",
        explanation="single-mode",
    )
    await handle_resolve_compliance(ctx, phase="drift", verdicts=[v], commit_hash=ctx.head_sha)

    # The events_dir should not exist (no writer was wired up) OR be empty.
    if events_dir.exists():
        events = _read_jsonl_events(events_dir)
        assert events == [], f"single-mode should emit zero events, got {events}"
    # Otherwise: no events_dir was created, which is also "no emit".


@pytest.mark.asyncio
async def test_compliance_event_payload_is_content_addressable(tmp_path: Path) -> None:
    """The emitted event's region descriptor uses content_hash, NOT
    start_line/end_line. Confirms Q1 design choice (line numbers excluded;
    receiver matches by content hash for cross-author replay stability)."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"

    team, inner = _build_team_adapter(events_dir, local_dir)
    await team.connect()

    res = await team.ingest_payload(_payload("payload-shape", "ps-mtg"))
    decision_id = res["created_decisions"][0]["decision_id"]
    region_id = await _seed_code_region(inner._client, content_hash="sha256:beef0001")

    ctx = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=team,
        code_graph=None,
        drift_analyzer=None,
    )
    v = ComplianceVerdict(
        decision_id=decision_id,
        region_id=region_id,
        content_hash="sha256:beef0001",
        verdict="compliant",
        confidence="high",
        explanation="payload check",
    )
    await handle_resolve_compliance(ctx, phase="drift", verdicts=[v], commit_hash=ctx.head_sha)

    events = _read_jsonl_events(events_dir)
    compliance_events = [e for e in events if e.get("event_type") == "compliance_check.completed"]
    assert len(compliance_events) == 1
    region = compliance_events[0]["payload"].get("region", {})
    assert "content_hash" in region, f"region descriptor must include content_hash; got {region}"
    assert region["content_hash"] == "sha256:beef0001"
    assert "start_line" not in region, (
        "region descriptor must NOT include start_line — line numbers shift on replay"
    )
    assert "end_line" not in region, (
        "region descriptor must NOT include end_line — line numbers shift on replay"
    )


@pytest.mark.asyncio
async def test_compliance_event_replay_writes_compliance_check_row(tmp_path: Path) -> None:
    """Full round-trip: resolve_compliance on adapter A emits an event;
    fresh adapter B replays it from the same JSONL into a fresh DB; B has
    a compliance_check row matching A's verdict."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()

    res = await team_a.ingest_payload(_payload("replay-roundtrip", "rr-mtg"))
    decision_id_a = res["created_decisions"][0]["decision_id"]
    canonical = await get_canonical_id(inner_a._client, decision_id_a)
    region_id_a = await _seed_code_region(inner_a._client, content_hash="sha256:beef0001")

    ctx_a = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=team_a,
        code_graph=None,
        drift_analyzer=None,
    )
    # #405 — never-compliant region must use 'partial', not 'drifted'.
    # The replay path is verdict-agnostic; the label change is incidental.
    v = ComplianceVerdict(
        decision_id=decision_id_a,
        region_id=region_id_a,
        content_hash="sha256:beef0001",
        verdict="partial",
        confidence="high",
        explanation="anticipatory binding — not yet implemented",
    )
    await handle_resolve_compliance(ctx_a, phase="drift", verdicts=[v], commit_hash=ctx_a.head_sha)

    # Fresh adapter B. Seed B's code_region with the same content_hash A used
    # (production: B has it via local bind / repo scan; here we inline-seed so
    # the cross-author lookup succeeds). Then drive replay.
    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
    await team_b.connect()
    await _seed_code_region(inner_b._client, content_hash="sha256:beef0001")
    # Re-replay so the compliance event finds the now-seeded region.
    watermark_b = local_dir_b / "watermark"
    if watermark_b.exists():
        watermark_b.unlink()
    await team_b._materializer.replay_new_events(team_b._inner)

    decision_id_b = await find_decision_by_canonical_id(inner_b._client, canonical)
    assert decision_id_b, "ingest event did not replay (no row for canonical_id)"
    rows = await inner_b._client.query(
        "SELECT verdict FROM compliance_check WHERE decision_id = $did LIMIT 1",
        {"did": decision_id_b},
    )
    assert rows, "compliance_check.completed did not replay — no compliance_check row on B"
    assert rows[0].get("verdict") == "partial", (
        f"replayed verdict should be 'partial'; got {rows[0].get('verdict')!r}"
    )


@pytest.mark.asyncio
async def test_compliance_event_replay_warns_when_region_missing_locally(
    tmp_path: Path, caplog
) -> None:
    """Replay of a compliance event whose region descriptor doesn't match
    any local code_region produces a warning AND does not write a row."""
    import logging

    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()
    res = await team_a.ingest_payload(_payload("missing-region", "mr-mtg"))
    decision_id_a = res["created_decisions"][0]["decision_id"]
    # Seed A's region with a content_hash B will NOT have. The event's
    # region descriptor is derived from THIS region (not from the verdict),
    # so receiver B looking up by this content_hash will miss.
    region_id_a = await _seed_code_region(inner_a._client, content_hash="sha256:ONLY_ON_SENDER")

    ctx_a = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=team_a,
        code_graph=None,
        drift_analyzer=None,
    )
    v = ComplianceVerdict(
        decision_id=decision_id_a,
        region_id=region_id_a,
        content_hash="sha256:ONLY_ON_SENDER",
        verdict="compliant",
        confidence="high",
        explanation="phantom",
    )
    await handle_resolve_compliance(ctx_a, phase="drift", verdicts=[v], commit_hash=ctx_a.head_sha)

    # Receiver B replays. B's code_region has a DIFFERENT content_hash —
    # production scenario where B has the file at a different state. The
    # event's region descriptor (carrying sha256:ONLY_ON_SENDER) won't match
    # any local region on B. Warning expected; no compliance_check row.
    local_dir_b = tmp_path / "local_b"
    with caplog.at_level(logging.WARNING, logger="events.materializer"):
        team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
        await team_b.connect()
        await _seed_code_region(inner_b._client, content_hash="sha256:beef0001")
        # Re-replay against the now-seeded region — should still miss.
        watermark_b = local_dir_b / "watermark"
        if watermark_b.exists():
            watermark_b.unlink()
        await team_b._materializer.replay_new_events(team_b._inner)

    decision_id_b = await find_decision_by_canonical_id(
        inner_b._client, await get_canonical_id(inner_a._client, decision_id_a)
    )
    assert decision_id_b, "ingest replay should have written the decision row"
    rows = await inner_b._client.query(
        "SELECT verdict FROM compliance_check WHERE decision_id = $did",
        {"did": decision_id_b},
    )
    assert rows == [], f"no compliance_check row should be written when region misses; got {rows}"
    assert any(
        "compliance_check.completed" in rec.message and "not yet materialized" in rec.message
        for rec in caplog.records
    ), f"expected a 'not yet materialized' WARNING; got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_compliance_event_replay_idempotent_on_duplicate(tmp_path: Path) -> None:
    """Replaying the same compliance_check.completed event twice does not
    produce duplicate rows. Locked by the existing UNIQUE index on
    (decision_id, region_id, content_hash) at ledger/schema.py:228."""
    from context import BicameralContext
    from contracts import ComplianceVerdict
    from handlers.resolve_compliance import handle_resolve_compliance

    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a)
    await team_a.connect()
    res = await team_a.ingest_payload(_payload("idempotent", "id-mtg"))
    decision_id_a = res["created_decisions"][0]["decision_id"]
    canonical = await get_canonical_id(inner_a._client, decision_id_a)
    region_id_a = await _seed_code_region(inner_a._client, content_hash="sha256:beef0001")

    ctx_a = BicameralContext(
        repo_path="test-repo",
        head_sha="cafef00d00000000000000000000000000000000",
        ledger=team_a,
        code_graph=None,
        drift_analyzer=None,
    )
    v = ComplianceVerdict(
        decision_id=decision_id_a,
        region_id=region_id_a,
        content_hash="sha256:beef0001",
        verdict="compliant",
        confidence="high",
        explanation="idempotent",
    )
    await handle_resolve_compliance(ctx_a, phase="drift", verdicts=[v], commit_hash=ctx_a.head_sha)

    # First replay into B. Seed matching region first.
    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b)
    await team_b.connect()
    await _seed_code_region(inner_b._client, content_hash="sha256:beef0001")
    watermark_b = local_dir_b / "watermark"
    if watermark_b.exists():
        watermark_b.unlink()
    await team_b._materializer.replay_new_events(team_b._inner)

    # Second replay: reset the watermark to 0 and reconnect; same events get
    # processed again. The UNIQUE index should make the second upsert a no-op.
    watermark_b = local_dir_b / "watermark"
    if watermark_b.exists():
        watermark_b.unlink()
    await team_b._materializer.replay_new_events(team_b._inner)

    decision_id_b = await find_decision_by_canonical_id(inner_b._client, canonical)
    rows = await inner_b._client.query(
        "SELECT verdict FROM compliance_check WHERE decision_id = $did",
        {"did": decision_id_b},
    )
    assert len(rows) == 1, (
        f"after duplicate replay, exactly one compliance_check row should exist; got {len(rows)}"
    )
