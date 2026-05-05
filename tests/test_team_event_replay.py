"""Round-trip tests for the team event log replay path (#97, #190).

For each decision-status event type:
    1. Setup team mode: inner adapter (memory://) wrapped in TeamWriteAdapter
    2. Mutate state via the adapter (writes JSONL + DB)
    3. Spin up a fresh adapter pointing at the same JSONL log but a fresh
       memory:// inner DB and a fresh watermark
    4. Connect — triggers materializer replay from offset 0
    5. Assert the fresh DB ends up in the same end-state

Covers:
    - decision_ratified.completed
    - decision_superseded.completed
    - compliance_check.completed (#190 — closes the §5 gap in
      docs/v0-architecture-current.md)

Plus regression coverage for the pre-existing emit/replay surface:
    - ingest.completed (decision row + signoff round-trip)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import ComplianceVerdict
from events.materializer import EventMaterializer
from events.team_adapter import TeamWriteAdapter
from events.writer import EventFileWriter
from handlers.resolve_compliance import handle_resolve_compliance
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


# ── #190: compliance_check.completed event coverage ────────────────────────


class _StubCtx:
    """Minimal context for handle_resolve_compliance.

    Mirrors the production BicameralContext shape that the handler reaches
    into: ``ledger`` (the team adapter — exposes ``emit_compliance_check_event``
    and an ``_inner._client`` with the SurrealDB connection).
    """

    def __init__(self, ledger: TeamWriteAdapter) -> None:
        self.ledger = ledger


async def _seed_decision_and_region(
    inner: SurrealDBLedgerAdapter,
    *,
    description: str,
    canonical_id: str,
    repo: str,
    file_path: str,
    symbol_name: str,
    start_line: int = 10,
    end_line: int = 30,
    content_hash: str = "h-initial",
) -> tuple[str, str]:
    """Seed one decision (with stamped canonical_id) + one region.

    Bypasses the ingest pipeline so the test stays focused on the verdict
    emit/replay surface — ingest already has its own round-trip coverage
    earlier in this file.
    """
    client = inner._client
    d_rows = await client.query(
        "CREATE decision SET description = $d, source_type = 'manual', "
        "canonical_id = $cid",
        {"d": description, "cid": canonical_id},
    )
    decision_id = str(d_rows[0]["id"])
    r_rows = await client.query(
        "CREATE code_region SET file_path = $fp, symbol_name = $s, "
        "start_line = $sl, end_line = $el, repo = $repo, content_hash = $h",
        {
            "fp": file_path,
            "s": symbol_name,
            "sl": start_line,
            "el": end_line,
            "repo": repo,
            "h": content_hash,
        },
    )
    region_id = str(r_rows[0]["id"])
    return decision_id, region_id


@pytest.mark.asyncio
async def test_compliance_check_event_emitted_to_jsonl(tmp_path: Path) -> None:
    """resolve_compliance writes a compliance_check.completed event line.

    Closes #190: the §5 gap was that the verdict mutated DB state without
    appearing in the team-sync stream. This asserts the event is now in
    the JSONL log with the cross-author payload (canonical_decision_id +
    region_descriptor + verdict fields).
    """
    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"

    team, inner = _build_team_adapter(events_dir, local_dir, author="alice@example.com")
    await team.connect()

    decision_id, region_id = await _seed_decision_and_region(
        inner,
        description="emit-test decision",
        canonical_id="canon-emit-test",
        repo="emit-repo",
        file_path="src/emit.py",
        symbol_name="emit_target",
    )

    verdict = ComplianceVerdict(
        decision_id=decision_id,
        region_id=region_id,
        content_hash="h-initial",
        verdict="compliant",
        confidence="high",
        explanation="implementation matches decision",
    )

    resp = await handle_resolve_compliance(
        _StubCtx(team),
        phase="drift",
        verdicts=[verdict],
        commit_hash="cafe1234",
    )
    assert len(resp.accepted) == 1, resp

    log_path = events_dir / "alice@example.com.jsonl"
    assert log_path.exists(), "team JSONL log not created"
    lines = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    compliance_events = [e for e in lines if e["event_type"] == "compliance_check.completed"]
    assert len(compliance_events) == 1, (
        f"expected exactly one compliance_check.completed event, got {compliance_events!r}"
    )

    payload = compliance_events[0]["payload"]
    assert payload["canonical_decision_id"] == "canon-emit-test"
    assert payload["verdict"] == "compliant"
    assert payload["confidence"] == "high"
    assert payload["content_hash"] == "h-initial"
    assert payload["phase"] == "drift"
    assert payload["commit_hash"] == "cafe1234"
    assert payload["pruned"] is False
    descriptor = payload["region_descriptor"]
    assert descriptor["repo"] == "emit-repo"
    assert descriptor["file_path"] == "src/emit.py"
    assert descriptor["symbol_name"] == "emit_target"
    assert descriptor["start_line"] == 10
    assert descriptor["end_line"] == 30


@pytest.mark.asyncio
async def test_compliance_check_event_roundtrip(tmp_path: Path) -> None:
    """A peer's verdict replays into a fresh DB by canonical decision +
    region descriptor.

    Both teammates seed the same decision (matching canonical_id) and the
    same region (matching descriptor). Alice resolves the verdict; Bob's
    fresh adapter materializes it on connect and ends up with the same
    compliance_check row.
    """
    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a, author="alice@example.com")
    await team_a.connect()

    decision_id_a, region_id_a = await _seed_decision_and_region(
        inner_a,
        description="roundtrip decision",
        canonical_id="canon-roundtrip",
        repo="rt-repo",
        file_path="src/round.py",
        symbol_name="rt_target",
        content_hash="h-rt",
    )

    verdict = ComplianceVerdict(
        decision_id=decision_id_a,
        region_id=region_id_a,
        content_hash="h-rt",
        verdict="drifted",
        confidence="medium",
        explanation="signature changed",
    )
    resp = await handle_resolve_compliance(
        _StubCtx(team_a),
        phase="drift",
        verdicts=[verdict],
        commit_hash="beef5678",
    )
    assert len(resp.accepted) == 1

    # Bob — fresh DB, fresh watermark, same JSONL log.
    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b, author="bob@example.com")

    # Connect Bob's inner adapter explicitly so we can seed the
    # decision + region BEFORE the materializer's first replay.
    await inner_b.connect()

    # Bob has the same decision + region locally (e.g. from a prior
    # ingest replay or his own tooling) but no compliance row yet.
    await _seed_decision_and_region(
        inner_b,
        description="roundtrip decision",
        canonical_id="canon-roundtrip",
        repo="rt-repo",
        file_path="src/round.py",
        symbol_name="rt_target",
        content_hash="h-rt",
    )

    # Triggers replay from offset 0 — Alice's verdict event lands here.
    await team_b.connect()

    decision_id_b = await find_decision_by_canonical_id(inner_b._client, "canon-roundtrip")
    assert decision_id_b, "decision did not resolve on Bob's side"
    rows = await inner_b._client.query(
        "SELECT verdict, confidence, explanation, phase, commit_hash, pruned "
        "FROM compliance_check WHERE decision_id = $d",
        {"d": decision_id_b},
    )
    assert rows, "compliance_check.completed did not replay — verdict row missing"
    assert rows[0]["verdict"] == "drifted"
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["phase"] == "drift"
    assert rows[0]["commit_hash"] == "beef5678"
    assert rows[0]["pruned"] is False


@pytest.mark.asyncio
async def test_compliance_check_replay_skips_when_region_missing(tmp_path: Path) -> None:
    """If the receiver's local region has not been materialized yet, the
    replay logs a warning and skips rather than crashing.

    This mirrors how decision_ratified handles missing canonical_ids and
    keeps the materializer monotonic (skipped events stay readable on the
    next replay if the region appears later).
    """
    events_dir = tmp_path / "events"
    local_dir_a = tmp_path / "local_a"

    team_a, inner_a = _build_team_adapter(events_dir, local_dir_a, author="alice@example.com")
    await team_a.connect()

    decision_id_a, region_id_a = await _seed_decision_and_region(
        inner_a,
        description="skip decision",
        canonical_id="canon-skip",
        repo="skip-repo",
        file_path="src/skip.py",
        symbol_name="skip_target",
        content_hash="h-skip",
    )

    await handle_resolve_compliance(
        _StubCtx(team_a),
        phase="drift",
        verdicts=[
            ComplianceVerdict(
                decision_id=decision_id_a,
                region_id=region_id_a,
                content_hash="h-skip",
                verdict="compliant",
                confidence="high",
                explanation="ok",
            )
        ],
    )

    # Bob — fresh DB, no decision and no region seeded.
    local_dir_b = tmp_path / "local_b"
    team_b, inner_b = _build_team_adapter(events_dir, local_dir_b, author="bob@example.com")
    await team_b.connect()

    rows = await inner_b._client.query("SELECT id FROM compliance_check")
    assert rows == [], "expected zero compliance rows when region is unresolved"
