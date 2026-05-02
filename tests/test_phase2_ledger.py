"""Phase 2 regression tests — real SurrealDB decision ledger.

Status: FAILING until SurrealDBLedgerAdapter is implemented in adapters/ledger.py.
Expected failure: NotImplementedError from get_ledger() when USE_REAL_LEDGER=1.

Once SurrealDBLedgerAdapter is wired and SurrealDB is running:
  1. All tests in this file should pass
  2. Phase 0 must still pass

Run: pytest tests/ -v  (no config needed — these tests set their own env vars)
Requires: docker compose up surrealdb -d && python scripts/init_ledger.py

Contract: thoughts/shared/plans/2026-03-21-decision-ledger-integration.md
          thoughts/shared/plans/2026-03-27-decision-ledger-mcp-tech-spec.md
"""

from __future__ import annotations

import pytest

from adapters.ledger import get_ledger
from context import BicameralContext
from handlers.decision_status import handle_decision_status
from handlers.detect_drift import handle_detect_drift
from handlers.link_commit import handle_link_commit
from handlers.search_decisions import handle_search_decisions


def _ctx():
    """Build BicameralContext from current env."""
    return BicameralContext.from_env()


# ── Adapter availability ──────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_real_ledger_adapter_instantiates(monkeypatch, surreal_url):
    """Fails with NotImplementedError until SurrealDBLedgerAdapter is implemented."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    adapter = get_ledger()  # raises NotImplementedError until Phase 2 done
    assert adapter is not None


# ── Ingestion idempotency ─────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_payload_creates_intent_node(monkeypatch, surreal_url, minimal_payload):
    """Ingesting a payload must create a queryable intent node."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    await ledger.ingest_payload(minimal_payload)
    decisions = await ledger.get_all_decisions(filter="all")
    descs = [d["description"] for d in decisions]
    assert any("test decision for ledger ingestion" in d for d in descs), (
        f"Ingested intent not found in get_all_decisions(). Got: {descs}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_is_idempotent(monkeypatch, surreal_url, minimal_payload):
    """Ingesting the same payload twice must not duplicate intent nodes."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    await ledger.ingest_payload(minimal_payload)
    await ledger.ingest_payload(minimal_payload)

    decisions = await ledger.get_all_decisions(filter="all")
    matching = [d for d in decisions if d["description"] == "test decision for ledger ingestion"]
    assert len(matching) == 1, (
        f"Expected 1 node after 2 ingestions of same payload, got {len(matching)}"
    )


# ── BM25 search ───────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bm25_search_finds_ingested_intent(monkeypatch, surreal_url):
    """After ingesting an intent, BM25 search for its keywords must return it."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    desc = "exponential backoff retry on webhook failure"
    await ledger.ingest_payload(
        {
            "query": desc,
            "repo": "test-repo",
            "commit_hash": "bm25test",
            "analyzed_at": "2026-03-27T12:00:00Z",
            "mappings": [
                {
                    "span": {
                        "span_id": "bm25-0",
                        "source_type": "transcript",
                        "text": desc,
                        "speaker": "",
                        "source_ref": "",
                    },
                    "intent": desc,
                    "symbols": ["WebhookDispatcher.send"],
                    "code_regions": [
                        {
                            "file_path": "webhooks/dispatcher.py",
                            "symbol": "WebhookDispatcher.send",
                            "type": "function",
                            "start_line": 134,
                            "end_line": 180,
                            "purpose": "dispatch",
                        }
                    ],
                    "dependency_edges": [],
                }
            ],
        }
    )

    results = await ledger.search_by_query(
        "retry webhook backoff", max_results=10, min_confidence=0.1
    )
    assert len(results) > 0, "BM25 returned no results for recently ingested intent"
    descs = [r["description"] for r in results]
    assert any(
        "webhook" in d.lower() or "retry" in d.lower() or "backoff" in d.lower() for d in descs
    ), f"Relevant intent not surfaced by BM25. Got: {descs}"


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_bm25_min_confidence_filters_results(monkeypatch, surreal_url):
    """Results with confidence below threshold must not appear."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    results = await ledger.search_by_query("payment retry", max_results=10, min_confidence=0.8)
    for r in results:
        assert r.get("confidence", 0) >= 0.8, (
            f"Result {r['description']!r} has confidence={r.get('confidence')} below threshold=0.8"
        )


# ── Reverse traversal: file → decisions ──────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_file_reverse_traversal_finds_decision(monkeypatch, surreal_url):
    """get_decisions_for_file must walk backwards through the graph from file → intent."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    file_path = "payments/processor.py"
    desc = "optimistic locking for cart updates"
    await ledger.ingest_payload(
        {
            "query": desc,
            "repo": "test-repo",
            "commit_hash": "reversetest",
            "analyzed_at": "2026-03-27T12:00:00Z",
            "mappings": [
                {
                    "span": {
                        "span_id": "rev-0",
                        "source_type": "transcript",
                        "text": desc,
                        "speaker": "",
                        "source_ref": "",
                    },
                    "intent": desc,
                    "symbols": ["CartService.updateItem"],
                    "code_regions": [
                        {
                            "file_path": file_path,
                            "symbol": "CartService.updateItem",
                            "type": "function",
                            "start_line": 87,
                            "end_line": 120,
                            "purpose": "cart update",
                        }
                    ],
                    "dependency_edges": [],
                }
            ],
        }
    )

    decisions = await ledger.get_decisions_for_file(file_path)
    assert len(decisions) > 0, f"No decisions found for {file_path!r} via reverse traversal"
    assert any(d["description"] == desc for d in decisions), (
        f"Expected {desc!r} via reverse traversal on {file_path}, got: {[d['description'] for d in decisions]}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_unknown_file_returns_empty(monkeypatch, surreal_url):
    """Querying a file that was never ingested must return []."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    results = await ledger.get_decisions_for_file("path/never/ingested/file.py")
    assert results == [], f"Expected [], got {results}"


# ── link_commit idempotency ───────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_link_commit_idempotent(monkeypatch, surreal_url):
    """Calling link_commit twice for the same hash must fast-path on second call."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ctx = _ctx()
    r1 = await handle_link_commit(ctx, "HEAD")
    r2 = await handle_link_commit(ctx, "HEAD")

    assert r1.commit_hash == r2.commit_hash
    assert r2.reason == "already_synced", (
        f"Second link_commit(HEAD) must return 'already_synced', got {r2.reason!r}"
    )
    assert r2.synced is True
    # v0.4.8 dedup guard: cached response forwards the first sync's real
    # counters (B23) so downstream ``sync_status`` consumers don't see
    # synthetic zeros. Idempotency is asserted via ``reason`` + counter
    # equality with r1, not by zeros.
    assert r2.regions_updated == r1.regions_updated
    assert r2.decisions_reflected == r1.decisions_reflected
    assert r2.decisions_drifted == r1.decisions_drifted


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_link_commit_updates_sync_cursor(monkeypatch, surreal_url):
    """After link_commit(hash), a second call for same hash must be already_synced."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ctx = _ctx()
    test_hash = "cafebabe" + "0" * 32
    r1 = await handle_link_commit(ctx, test_hash)
    r2 = await handle_link_commit(ctx, test_hash)
    assert r2.reason == "already_synced", f"Expected 'already_synced', got {r2.reason!r}"


# ── decision_status via real graph ────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_decision_status_reflects_ingested_data(monkeypatch, surreal_url, minimal_payload):
    """decision_status must return decisions that exist in the real graph."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()
    await ledger.ingest_payload(minimal_payload)

    ctx = _ctx()
    result = await handle_decision_status(ctx, filter="all")
    descs = [d.description for d in result.decisions]
    assert any("test decision for ledger ingestion" in d for d in descs), (
        f"Ingested decision not found in decision_status. Got: {descs}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ungrounded_intent_has_correct_status(monkeypatch, surreal_url):
    """An intent with no code_regions must appear in filter='ungrounded'.

    Note: handle_decision_status() triggers lazy re-grounding via link_commit,
    so the decision text must be gibberish that no BM25 hit can anchor —
    otherwise the regrounder attaches it to a random symbol and the row
    exits the ungrounded filter. This is a quirk of exercising the lazy
    regrounder against a real code index, not a production concern.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    desc = "zzqx qqzzyy nonsensetoken glarbflumph deliberate-gibberish wlrdpfnz"
    await ledger.ingest_payload(
        {
            "query": desc,
            "repo": "test-repo",
            "commit_hash": "unground01",
            "analyzed_at": "2026-03-27T12:00:00Z",
            "mappings": [
                {
                    "span": {
                        "span_id": "ug-0",
                        "source_type": "transcript",
                        "text": desc,
                        "speaker": "",
                        "source_ref": "",
                    },
                    "intent": desc,
                    "symbols": [],
                    "code_regions": [],
                    "dependency_edges": [],
                }
            ],
        }
    )

    # Query the ledger directly — handle_decision_status auto-syncs via
    # link_commit which triggers _reground_ungrounded, potentially changing
    # the status before we can assert on it.
    ungrounded = await ledger.get_all_decisions(filter="ungrounded")
    descs = [d.get("description", "") for d in ungrounded]
    assert any(desc in d for d in descs), f"Expected {desc!r} in ungrounded filter. Got: {descs}"


# ── detect_drift with real reverse traversal ──────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_detect_drift_returns_decisions_for_ingested_file(monkeypatch, surreal_url):
    """detect_drift must surface decisions mapped to symbols in the given file."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    file_path = "services/checkout.py"
    desc = "rate limit checkout endpoint"
    await ledger.ingest_payload(
        {
            "query": desc,
            "repo": "test-repo",
            "commit_hash": "drift001",
            "analyzed_at": "2026-03-27T12:00:00Z",
            "mappings": [
                {
                    "span": {
                        "span_id": "d-0",
                        "source_type": "transcript",
                        "text": desc,
                        "speaker": "",
                        "source_ref": "mtg-001",
                    },
                    "intent": desc,
                    "symbols": ["CheckoutService.process"],
                    "code_regions": [
                        {
                            "file_path": file_path,
                            "symbol": "CheckoutService.process",
                            "type": "function",
                            "start_line": 45,
                            "end_line": 90,
                            "purpose": "checkout",
                        }
                    ],
                    "dependency_edges": [],
                }
            ],
        }
    )

    ctx = _ctx()
    result = await handle_detect_drift(ctx, file_path)
    assert len(result.decisions) > 0, (
        f"detect_drift returned no decisions for {file_path!r} after ingesting a decision that maps to it"
    )
    assert any("rate limit" in d.description.lower() for d in result.decisions)


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_source_cursor_upserts_after_ingest(monkeypatch, surreal_url, minimal_payload):
    """Source cursor rows track upstream ingest progress independently of source_ref provenance."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    from handlers.ingest import handle_ingest

    ctx = _ctx()
    result = await handle_ingest(
        ctx, minimal_payload, source_scope="slack:C123", cursor="1743210021.123"
    )

    assert result.source_cursor is not None
    assert result.source_cursor.repo == "test-repo"
    assert result.source_cursor.source_type == "transcript"
    assert result.source_cursor.source_scope == "slack:C123"
    assert result.source_cursor.cursor == "1743210021.123"
    assert result.source_cursor.last_source_ref == "test-meeting-001"


# ── M1 decision-relevance instrumentation ────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_stats_populates_grounded_fields(
    caplog, monkeypatch, surreal_url, minimal_payload
):
    """handle_ingest must populate stats.grounded + stats.grounded_pct and
    emit a [ingest] complete log line. This is the M1 instrumentation gate."""
    import logging

    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    from handlers.ingest import handle_ingest

    ctx = _ctx()
    with caplog.at_level(logging.INFO, logger="handlers.ingest"):
        result = await handle_ingest(ctx, minimal_payload)

    stats = result.stats
    assert stats.intents_created >= 1
    assert stats.grounded + stats.ungrounded == stats.intents_created
    if stats.intents_created > 0:
        expected_pct = stats.grounded / stats.intents_created
        assert abs(stats.grounded_pct - expected_pct) < 1e-9

    assert any("[ingest] complete:" in m for m in caplog.messages), (
        f"expected '[ingest] complete:' log line, got: {caplog.messages}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_get_grounding_breakdown_groups_by_source_ref(
    monkeypatch, surreal_url, minimal_payload
):
    """get_grounding_breakdown must return one row per distinct source_ref
    with grounded/ungrounded/total/grounded_pct buckets."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    from handlers.ingest import handle_ingest
    from ledger.queries import get_grounding_breakdown

    ctx = _ctx()
    await handle_ingest(ctx, minimal_payload)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()
    # TeamWriteAdapter wraps SurrealDBLedgerAdapter as _inner; unwrap to reach _client
    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    rows = await get_grounding_breakdown(client)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    row = next((r for r in rows if r["source_ref"] == "test-meeting-001"), None)
    assert row is not None, f"expected test-meeting-001 row, got {rows}"
    assert row["total"] == row["grounded"] + row["ungrounded"]
    assert 0.0 <= row["grounded_pct"] <= 1.0
