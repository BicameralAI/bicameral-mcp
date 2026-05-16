"""#221 Phase B-1 — load-bearing user-visible erasure propagation tests.

These are the tests the round-1 audit explicitly required. They pin
that after `archive.erase_by_predicate(...)`, the history-enriched
surface returns `[ERASED]` for the affected spans rather than stale
plaintext.

Sociable per CLAUDE.md: real `LedgerClient` over `memory://`, real
`PiiArchive` (SQLite-backed tmp_path), real `_fetch_all_decisions_enriched`
path. No mocks on the helper or the storage layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import _ERASED_SENTINEL, _resolve_span_text
from pii_archive import ErasePredicate, PiiArchive


async def _seed_decision(adapter: SurrealDBLedgerAdapter, *, text: str) -> str:
    """Seed one input_span + decision via the real ingest path; returns
    the decision_id. The archive (if attached) receives the verbatim
    text via the Phase B-1 cutover."""
    payload = {
        "query": "test",
        "repo": "test-repo",
        "analyzed_at": "2026-05-15T00:00:00+00:00",
        "mappings": [
            {
                "span": {
                    "span_id": "test-span",
                    "source_type": "manual",
                    "text": text,
                    "speakers": ["alice@example.com"],
                    "source_ref": "test-ref",
                    "meeting_date": "2026-05-15",
                },
                "intent": "test decision",
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }
    await adapter.ingest_payload(payload)
    rows = await adapter._client.query(
        "SELECT type::string(id) AS id FROM decision WHERE source_ref = 'test-ref' LIMIT 1"
    )
    return str(rows[0]["id"]) if rows else ""


@pytest.fixture
async def adapter_with_archive(tmp_path: Path) -> SurrealDBLedgerAdapter:
    """Real adapter, real archive, fresh memory:// ledger."""
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    adapter._pii_archive = PiiArchive(tmp_path / "pii.db")
    return adapter


@pytest.mark.asyncio
async def test_resolve_returns_erased_sentinel_after_archive_erase(
    adapter_with_archive: SurrealDBLedgerAdapter,
) -> None:
    """Load-bearing user-visible check: post-erasure, helper returns sentinel."""
    adapter = adapter_with_archive
    decision_id = await _seed_decision(adapter, text="ratified by alice@example.com on 2026-05-15")
    assert decision_id

    # Query the input_span row to get its archive_key
    rows = await adapter._client.query(
        """
        SELECT text, archive_key FROM input_span WHERE source_ref = 'test-ref'
        """
    )
    assert rows
    span_row = rows[0]
    archive_key = span_row.get("archive_key") or ""
    assert archive_key, "Phase B-1 cutover: ingest must populate archive_key"
    assert span_row.get("text") == "", (
        "Phase B-1 cutover: input_span.text must be empty for new rows"
    )

    # Helper returns archive text pre-erasure
    pre_erasure = _resolve_span_text(adapter._pii_archive, span_row)
    assert pre_erasure == "ratified by alice@example.com on 2026-05-15"

    # Erase the archive entry
    count = adapter._pii_archive.erase_by_predicate(ErasePredicate(archive_key=archive_key))
    assert count == 1

    # Helper now returns the sentinel
    post_erasure = _resolve_span_text(adapter._pii_archive, span_row)
    assert post_erasure == _ERASED_SENTINEL
    assert post_erasure == "[ERASED]"


@pytest.mark.asyncio
async def test_get_all_decisions_filters_erased_sentinel_from_source_excerpt(
    adapter_with_archive: SurrealDBLedgerAdapter,
) -> None:
    """After erasure, the agent-visible `source_excerpt` field is empty,
    NOT the sentinel literal. The sentinel is observable via the helper
    + audit telemetry but is filtered out of agent-facing rendering."""
    from ledger.queries import get_all_decisions

    adapter = adapter_with_archive
    decision_id = await _seed_decision(adapter, text="some sensitive content")

    # Pre-erasure: agent-visible source_excerpt has the archive text
    decisions_before = await get_all_decisions(adapter._client, archive=adapter._pii_archive)
    target_before = [d for d in decisions_before if d.get("decision_id") == decision_id]
    assert target_before
    assert target_before[0].get("source_excerpt") == "some sensitive content"

    # Erase
    rows = await adapter._client.query(
        "SELECT archive_key FROM input_span WHERE source_ref = 'test-ref'"
    )
    adapter._pii_archive.erase_by_predicate(ErasePredicate(archive_key=rows[0]["archive_key"]))

    # Post-erasure: source_excerpt is empty (filter excludes the sentinel)
    decisions_after = await get_all_decisions(adapter._client, archive=adapter._pii_archive)
    target_after = [d for d in decisions_after if d.get("decision_id") == decision_id]
    assert target_after
    assert target_after[0].get("source_excerpt") == ""


@pytest.mark.asyncio
async def test_legacy_row_with_no_archive_key_still_renders_normally(
    adapter_with_archive: SurrealDBLedgerAdapter,
) -> None:
    """Backward-compat: a row inserted in the legacy shape (text!='',
    archive_key='') is unaffected by Phase B-1. The helper returns
    `row['text']` for these."""
    adapter = adapter_with_archive

    # Insert a legacy-shape row directly (bypass the ingest path)
    await adapter._client.execute(
        """
        CREATE input_span SET
            text = 'legacy verbatim content',
            source_type = 'manual',
            source_ref = 'legacy-ref',
            speakers = []
        """
    )
    rows = await adapter._client.query(
        "SELECT text, archive_key FROM input_span WHERE source_ref = 'legacy-ref'"
    )
    assert rows
    legacy_row = rows[0]
    assert legacy_row.get("text") == "legacy verbatim content"
    assert legacy_row.get("archive_key") == ""

    # Helper returns the legacy text (NOT the sentinel)
    result = _resolve_span_text(adapter._pii_archive, legacy_row)
    assert result == "legacy verbatim content"


@pytest.mark.asyncio
async def test_ingest_writes_text_to_archive_and_empty_to_input_span(
    adapter_with_archive: SurrealDBLedgerAdapter,
) -> None:
    """End-to-end: ingest writes the verbatim text to the PiiArchive,
    leaves input_span.text empty, and sets archive_key. The v22 ASSERT
    permits this because archive_key is non-empty."""
    adapter = adapter_with_archive
    text = "Decision: use the auth proposal as drafted."
    await _seed_decision(adapter, text=text)

    rows = await adapter._client.query(
        "SELECT text, archive_key FROM input_span WHERE source_ref = 'test-ref'"
    )
    assert rows
    row = rows[0]
    assert row.get("text") == ""
    assert row.get("archive_key") != ""

    # Archive holds the verbatim
    entry = adapter._pii_archive.get(row["archive_key"])
    assert entry is not None
    assert entry.text == text
