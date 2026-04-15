"""v0.4.13 — content-addressable canonical ID dedup tests.

Three layers:

  1. Pure-function tests for the canonicalization helpers
     (canonicalize_source_ref, canonicalize_text, canonical_intent_id).
     IO-free, fast.

  2. UPSERT idempotency tests via the real adapter — verifies that
     ingesting the same logical decision twice produces a single row
     even with formatting variance (whitespace, casing, source_ref
     format drift).

  3. Cross-writer dedup simulation — two "developers" (different
     description/source_ref formatting of the same logical event)
     ingesting into the same DB produces a single canonical row.
"""

from __future__ import annotations

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from ledger.canonical import (
    BICAMERAL_NAMESPACE,
    canonical_intent_id,
    canonical_json_bytes,
    canonical_source_span_id,
    canonicalize_source_ref,
    canonicalize_text,
)


# ── Source ref canonicalization ─────────────────────────────────────


def test_slack_with_hash_prefix():
    assert (
        canonicalize_source_ref("slack", "#payments:1726113809.330439")
        == "slack:payments:1726113809330439"
    )


def test_slack_with_dash_separator():
    assert (
        canonicalize_source_ref("slack", "payments-1726113809330439")
        == "slack:payments:1726113809330439"
    )


def test_slack_three_variants_collapse():
    a = canonicalize_source_ref("slack", "#payments:1726113809.330439")
    b = canonicalize_source_ref("slack", "payments-1726113809330439")
    c = canonicalize_source_ref("slack", "payments:1726113809.330439")
    assert a == b == c


def test_notion_strips_title_prefix():
    out = canonicalize_source_ref(
        "notion", "Page-Title-abc123def456abc123def456abc123ef45",
    )
    # 32-char hex extracted from the end
    assert out.startswith("notion:")
    assert len(out.split(":", 1)[1]) == 32


def test_github_normalizes_separators():
    a = canonicalize_source_ref("github", "issue/142")
    b = canonicalize_source_ref("github", "issue#142")
    assert a == b == "github:issue:142"


def test_transcript_canonicalizes_whitespace():
    assert (
        canonicalize_source_ref("transcript", "  meeting 2026 03 12  ")
        == "transcript:meeting_2026_03_12"
    )


def test_unknown_source_type_falls_through():
    out = canonicalize_source_ref("zoom", "Meeting ID 12345")
    assert out == "zoom:meeting_id_12345"


def test_empty_inputs():
    assert canonicalize_source_ref("", "") == ""
    assert canonicalize_source_ref("slack", "") == "slack:"


# ── Text canonicalization ──────────────────────────────────────────


def test_text_lowercases():
    assert canonicalize_text("Use Redis For Sessions") == "use redis for sessions"


def test_text_collapses_whitespace():
    assert canonicalize_text("  use   redis   for sessions  ") == "use redis for sessions"


def test_text_normalizes_curly_quotes():
    """Curly Unicode quotes → straight ASCII quotes."""
    assert canonicalize_text("Don\u2019t cache user data") == "don't cache user data"


def test_text_normalizes_em_dash():
    assert canonicalize_text("Use Redis \u2014 not local memory") == "use redis - not local memory"


def test_text_handles_nbsp():
    assert canonicalize_text("Use\u00a0Redis\u00a0for\u00a0sessions") == "use redis for sessions"


def test_text_empty():
    assert canonicalize_text("") == ""
    assert canonicalize_text(None) == ""  # type: ignore[arg-type]


# ── Canonical ID determinism ───────────────────────────────────────


def test_intent_id_is_deterministic():
    """Same input → same UUID, every call."""
    a = canonical_intent_id("Use Redis", "slack", "#payments:1726113809.330439")
    b = canonical_intent_id("Use Redis", "slack", "#payments:1726113809.330439")
    assert a == b


def test_intent_id_collapses_whitespace_variant():
    """Two writers with whitespace differences produce the same ID."""
    a = canonical_intent_id("Use Redis for sessions", "slack", "#payments:172611380.330439")
    b = canonical_intent_id("  Use Redis for sessions  ", "slack", "#payments:172611380.330439")
    assert a == b


def test_intent_id_collapses_source_ref_format_drift():
    """The killer case: same logical event, different source_ref formatting."""
    a = canonical_intent_id("Use Redis for sessions", "slack", "#payments:1726113809.330439")
    b = canonical_intent_id("Use Redis for sessions", "slack", "payments-1726113809330439")
    c = canonical_intent_id("Use Redis for sessions", "slack", "payments:1726113809.330439")
    assert a == b == c


def test_intent_id_collapses_unicode_punctuation():
    a = canonical_intent_id("Don't cache user data", "transcript", "meeting-1")
    b = canonical_intent_id("Don\u2019t cache user data", "transcript", "meeting-1")
    assert a == b


def test_intent_id_collapses_casing():
    a = canonical_intent_id("USE REDIS FOR SESSIONS", "slack", "payments:1")
    b = canonical_intent_id("use redis for sessions", "slack", "payments:1")
    assert a == b


def test_intent_id_distinguishes_real_differences():
    """Different decisions must produce different IDs."""
    a = canonical_intent_id("Use Redis for sessions", "slack", "payments:1")
    b = canonical_intent_id("Use Memcached for sessions", "slack", "payments:1")
    c = canonical_intent_id("Use Redis for sessions", "slack", "payments:2")  # different ref
    assert len({a, b, c}) == 3


def test_intent_id_is_valid_uuid_string():
    out = canonical_intent_id("Use Redis", "slack", "payments:1")
    # UUID v5 has version digit 5 in the third group: ........-....-5...-....-............
    parts = out.split("-")
    assert len(parts) == 5
    assert parts[2][0] == "5"


# ── JCS canonicalization ───────────────────────────────────────────


def test_jcs_sorts_keys():
    a = canonical_json_bytes({"b": 1, "a": 2})
    b = canonical_json_bytes({"a": 2, "b": 1})
    assert a == b


def test_jcs_no_whitespace():
    out = canonical_json_bytes({"a": 1, "b": 2})
    assert b" " not in out


# ── End-to-end UPSERT idempotency ──────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_upsert_intent_collapses_whitespace_variant(monkeypatch, surreal_url):
    """Two ingests with whitespace variance must produce a single
    intent row, dedupe via canonical_id."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload_a = {
        "query": "Use Redis for sessions",
        "repo": "test-repo",
        "mappings": [
            {
                "span": {
                    "span_id": "p1-0",
                    "source_type": "slack",
                    "text": "Use Redis for sessions",
                    "source_ref": "#payments:1726113809.330439",
                },
                "intent": "Use Redis for sessions",
                "symbols": [],
                "code_regions": [],
            }
        ],
    }
    payload_b = {
        **payload_a,
        "mappings": [
            {
                **payload_a["mappings"][0],
                # Whitespace + source_ref format variance
                "intent": "  Use Redis for sessions  ",
                "span": {
                    **payload_a["mappings"][0]["span"],
                    "text": "  Use Redis for sessions  ",
                    "source_ref": "payments-1726113809330439",
                },
            }
        ],
    }

    await ledger.ingest_payload(payload_a)
    await ledger.ingest_payload(payload_b)

    decisions = await ledger.get_all_decisions(filter="all")
    matching = [
        d for d in decisions
        if "redis" in d["description"].lower() and "session" in d["description"].lower()
    ]
    assert len(matching) == 1, (
        f"Two ingests of the same logical decision (whitespace + source_ref "
        f"format variance) should dedupe to 1 row via canonical_id, got "
        f"{len(matching)} rows: {[d['description'] for d in matching]}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_upsert_intent_distinguishes_real_differences(monkeypatch, surreal_url):
    """Different decisions on the same source produce different rows."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = {
        "query": "test",
        "repo": "test-repo",
        "mappings": [
            {
                "span": {
                    "span_id": "p1",
                    "source_type": "slack",
                    "text": "Use Redis",
                    "source_ref": "payments:1726113809330439",
                },
                "intent": "Use Redis for sessions",
                "symbols": [],
                "code_regions": [],
            },
            {
                "span": {
                    "span_id": "p2",
                    "source_type": "slack",
                    "text": "Webhooks retry with backoff",
                    "source_ref": "payments:1726113809330439",
                },
                "intent": "Retry failed webhooks with exponential backoff",
                "symbols": [],
                "code_regions": [],
            },
        ],
    }
    await ledger.ingest_payload(payload)

    decisions = await ledger.get_all_decisions(filter="all")
    assert len(decisions) == 2
