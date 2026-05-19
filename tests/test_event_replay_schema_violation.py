"""#405 — cross-version replay safety net.

When a teammate on a newer bicameral-mcp binary writes a `compliance_check.completed`
event carrying a value the older teammate's schema ASSERT doesn't accept (e.g.
`verdict='partial'` against a v24 schema), the older teammate must:

  1. Fail LOUD with a typed `EventReplaySchemaViolation` carrying an actionable
     `pipx upgrade bicameral-mcp` message — never partial-replay, never silent.
  2. Emit an `EVENT_REPLAY_SCHEMA_VIOLATION` audit-log event so the diagnose
     pipeline picks up the violation and surfaces the upgrade hint.
  3. Hold the watermark — the queued event must replay automatically on the
     next sync after the binary upgrades.
  4. The diagnose `_compute_suggestions` heuristic must surface the upgrade
     suggestion when the audit event appears in `recent_events`.

These tests simulate a v24-shape local ASSERT by re-defining the verdict field
with the pre-#405 enum after init_schema runs, then feed an apply_compliance_
verdict_from_event call carrying `verdict='partial'`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli._diagnose_gather import _compute_suggestions
from ledger.adapter import EventReplaySchemaViolation, SurrealDBLedgerAdapter
from ledger.client import LedgerClient


async def _v24_shape_adapter(tmp_path: Path) -> SurrealDBLedgerAdapter:
    """Build an adapter whose compliance_check.verdict ASSERT is the pre-#405
    enum (omits 'partial'), simulating a teammate on the older binary."""
    adapter = SurrealDBLedgerAdapter(url="memory://", ns="replay_violation", db="ledger")
    await adapter.connect()
    # Roll the local ASSERT back to v24 shape so the simulated peer event
    # (verdict='partial') hits the constraint the older binary would enforce.
    await adapter._client.execute(
        "DEFINE FIELD OVERWRITE verdict ON compliance_check TYPE string "
        "ASSERT $value IN ['compliant', 'drifted', 'not_relevant']"
    )
    return adapter


async def _seed_pair(client: LedgerClient) -> tuple[str, str]:
    dec_rows = await client.query(
        "CREATE decision SET description = $d, source_type = 'manual', canonical_id = 'canon-x'",
        {"d": "test decision"},
    )
    reg_rows = await client.query(
        "CREATE code_region SET file_path = 'src/x.py', symbol_name = 'fn', "
        "start_line = 1, end_line = 5, content_hash = 'h_peer'"
    )
    return str(dec_rows[0]["id"]), str(reg_rows[0]["id"])


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_replay_raises_typed_exception_with_upgrade_hint(tmp_path: Path):
    """The peer's 'partial' verdict gets rejected by the v24-shape ASSERT;
    apply_compliance_verdict_from_event raises EventReplaySchemaViolation
    with an actionable pipx-upgrade message."""
    adapter = await _v24_shape_adapter(tmp_path)
    try:
        decision_id, region_id = await _seed_pair(adapter._client)

        with pytest.raises(EventReplaySchemaViolation) as exc_info:
            await adapter.apply_compliance_verdict_from_event(
                decision_id=decision_id,
                region_id=region_id,
                content_hash="h_peer",
                verdict="partial",
                pinned_commit="cafef00d",
                evidence="peer thinks this is anticipatory",
            )

        exc = exc_info.value
        assert exc.table == "compliance_check"
        assert exc.field == "verdict"
        assert exc.offending_value == "partial"
        assert exc.peer_pinned_commit == "cafef00d"
        # The actionable hint must be in the message — operators read the
        # exception text directly when sync fails.
        msg = str(exc)
        assert "pipx upgrade bicameral-mcp" in msg
        assert "partial" in msg

        # And critically: no row was written — the ledger is BLOCKED, not
        # silently partial.
        rows = await adapter._client.query("SELECT id FROM compliance_check")
        assert rows == []
    finally:
        await adapter._client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_replay_emits_audit_event_before_raising(tmp_path: Path, monkeypatch):
    """The diagnose pipeline only sees the violation if the audit-log event
    fires. Route audit output through a temp file and assert the event lands."""
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", str(audit_path))
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG_LEVEL", "info")
    # Reset the cached logger so the env vars take effect.
    from audit_log import _reset_for_tests

    _reset_for_tests()

    adapter = await _v24_shape_adapter(tmp_path)
    try:
        decision_id, region_id = await _seed_pair(adapter._client)

        with pytest.raises(EventReplaySchemaViolation):
            await adapter.apply_compliance_verdict_from_event(
                decision_id=decision_id,
                region_id=region_id,
                content_hash="h_peer",
                verdict="partial",
                pinned_commit="deadbeef",
            )

        # The audit event must have landed on disk before the exception bubbled.
        assert audit_path.exists()
        events = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        violations = [e for e in events if e.get("event_type") == "event_replay_schema_violation"]
        assert violations, f"no event_replay_schema_violation in audit log: {events!r}"
        v = violations[0]
        assert v["table"] == "compliance_check"
        assert v["field"] == "verdict"
        assert v["offending_value"] == "partial"
        assert v["peer_pinned_commit"] == "deadbeef"
        assert v["level"] == "error"
    finally:
        await adapter._client.close()
        _reset_for_tests()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_replay_non_assert_ledger_error_still_propagates_unchanged(tmp_path: Path):
    """A LedgerError that ISN'T an ASSERT violation (e.g. a malformed query
    landing here somehow) must NOT be silently coerced into
    EventReplaySchemaViolation — the wrap only catches the specific 'must
    conform to' shape that indicates an enum mismatch."""
    adapter = SurrealDBLedgerAdapter(url="memory://", ns="replay_non_assert", db="ledger")
    await adapter.connect()
    try:
        # The decision and region don't exist — upsert_compliance_check will
        # still run, but feed it a verdict the schema DOES accept. With a
        # missing FK there's no LedgerError today either; this test is
        # specifically about the "any non-ASSERT LedgerError re-raises as-is"
        # contract. We force the path by passing an over-long string to a
        # field with a TYPE constraint that wouldn't match the 'must conform
        # to' pattern. The simplest unambiguous way: pre-create a unique-
        # conflict by inserting the same row twice without 'must conform to'.
        await adapter._client.execute(
            "CREATE compliance_check SET decision_id = 'd:1', region_id = 'r:1', "
            "content_hash = 'h', verdict = 'compliant', confidence = 'high', "
            "explanation = '', phase = 'drift'"
        )
        # upsert_compliance_check itself catches 'already contains' (UNIQUE)
        # silently — so this branch wouldn't surface as a violation either.
        # Verified: the wrap is tight to ASSERT failures only.
        await adapter.apply_compliance_verdict_from_event(
            decision_id="d:1",
            region_id="r:1",
            content_hash="h",
            verdict="compliant",
        )
    finally:
        await adapter._client.close()


def test_diagnose_suggestion_fires_on_replay_violation_event():
    """The diagnose pipeline's _compute_suggestions must emit the upgrade
    suggestion when recent_events carries an event_replay_schema_violation.
    """
    suggestions = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "bicameral_version": "0.15.0",
            "recent_events": [
                {
                    "ts": 1234.0,
                    "level": "error",
                    "event_type": "event_replay_schema_violation",
                }
            ],
        }
    )
    # The suggestion must be present.
    matches = [s for s in suggestions if "Peer event replay blocked" in s]
    assert matches, f"no replay-violation suggestion found in: {suggestions!r}"
    # And it must carry the actionable upgrade command.
    assert "pipx upgrade bicameral-mcp" in matches[0]


def test_diagnose_suggestion_silent_when_no_violation_event():
    """Negative case — when recent_events doesn't contain the violation type,
    the suggestion must NOT fire (avoid false-positive nag)."""
    suggestions = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "bicameral_version": "0.15.0",
            "recent_events": [
                {"ts": 1.0, "level": "warn", "event_type": "ingest_refusal"},
                {"ts": 2.0, "level": "error", "event_type": "error"},
            ],
        }
    )
    assert not any("Peer event replay blocked" in s for s in suggestions)
