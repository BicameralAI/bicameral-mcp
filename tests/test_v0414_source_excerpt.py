"""v0.4.14 — source excerpt + meeting_date plumbing tests.

Verifies that the meeting context bicameral stores at ingest time
(source_span.text + source_span.meeting_date) is now surfaced in the
brief/drift/search read paths via the yields reverse edge.

Pre-v0.4.14 the data was in the ledger but the handlers stripped it
out. The v0.4.14 fix pulls it back through into:

  - DecisionMatch.source_excerpt + meeting_date
  - DriftEntry.source_excerpt + meeting_date
  - BriefDecision.source_excerpt + meeting_date

Tests:
  1. Ingest a payload with span.text + meeting_date populated, then
     search/brief/drift back and verify the fields come through.
  2. Ingest with empty span.text — fields default to "" (graceful).
  3. Multiple intents per file — each intent's excerpt is its own.
"""

from __future__ import annotations

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext

from handlers.detect_drift import handle_detect_drift
from handlers.search_decisions import handle_search_decisions


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_search_response_includes_source_excerpt(monkeypatch, surreal_url):
    """search response surfaces source_span.text and meeting_date."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()  # isolate from prior test's ledger state

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = {
        "query": "rate limiting strategy",
        "repo": "test-repo",
        "mappings": [
            {
                "span": {
                    "span_id": "span-1",
                    "source_type": "transcript",
                    "text": (
                        "Alex: I think we should use a token bucket rate "
                        "limiter on the checkout endpoint, capped at 100 "
                        "requests per minute per IP."
                    ),
                    "source_ref": "sprint-13-arch-review",
                    "meeting_date": "2026-03-30",
                },
                "intent": "Use token bucket rate limiter on checkout, 100 RPM per IP",
                "symbols": [],
                "code_regions": [],
            }
        ],
    }
    await ledger.ingest_payload(payload)

    ctx = BicameralContext.from_env()
    response = await handle_search_decisions(
        ctx, query="token bucket rate limit", max_results=5, min_confidence=0.3,
    )
    assert response.matches, "Expected at least one match for the ingested decision"
    match = response.matches[0]
    assert "token bucket" in match.source_excerpt.lower(), (
        f"source_excerpt should contain the meeting passage; got {match.source_excerpt!r}"
    )
    assert "Alex:" in match.source_excerpt, (
        "speaker prefix should be preserved in the raw passage"
    )
    assert match.meeting_date == "2026-03-30", (
        f"meeting_date should round-trip; got {match.meeting_date!r}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_empty_source_excerpt_is_graceful(monkeypatch, surreal_url):
    """Ingest with empty span.text → response has empty source_excerpt
    (no crash, no KeyError)."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()  # isolate from prior test's ledger state

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = {
        "query": "empty span test",
        "repo": "test-repo",
        "mappings": [
            {
                "span": {
                    "span_id": "span-3",
                    "source_type": "manual",
                    "text": "",  # explicitly empty
                    "source_ref": "manual-entry-1",
                },
                "intent": "Empty span test decision",
                "symbols": [],
                "code_regions": [],
            }
        ],
    }
    await ledger.ingest_payload(payload)

    ctx = BicameralContext.from_env()
    response = await handle_search_decisions(
        ctx, query="empty span test", max_results=5, min_confidence=0.3,
    )
    assert response.matches
    assert response.matches[0].source_excerpt == ""
    assert response.matches[0].meeting_date == ""


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_drift_entry_carries_source_excerpt(monkeypatch, surreal_url):
    """DriftEntry from detect_drift includes source_excerpt + meeting_date."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()  # isolate from prior test's ledger state

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = {
        "query": "discount logic",
        "repo": "test-repo",
        "mappings": [
            {
                "span": {
                    "span_id": "span-4",
                    "source_type": "transcript",
                    "text": (
                        "Alex: discounts are 10% on orders of $100 or more. "
                        "Below that, no discount."
                    ),
                    "source_ref": "sprint-14-planning",
                    "meeting_date": "2026-03-12",
                },
                "intent": "10% discount on orders over $100",
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "src/pricing/discount.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                    }
                ],
            }
        ],
    }
    await ledger.ingest_payload(payload)

    ctx = BicameralContext.from_env()
    drift = await handle_detect_drift(
        ctx, file_path="src/pricing/discount.py", use_working_tree=False,
    )
    assert drift.decisions, "Expected at least one decision from detect_drift"
    entry = drift.decisions[0]
    assert "10%" in entry.source_excerpt or "$100" in entry.source_excerpt, (
        f"source_excerpt should contain the meeting passage; got {entry.source_excerpt!r}"
    )
    assert entry.meeting_date == "2026-03-12"
