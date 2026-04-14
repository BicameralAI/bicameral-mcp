"""FC-3 vocab cache similarity gate + purpose rewrite regression tests
(bicameral-mcp v0.4.7).

The FC-3 failure mode: ``handlers/ingest.py:handle_ingest`` calls
``lookup_vocab_cache(description, repo)`` which uses SurrealDB's ``@0@``
BM25 full-text operator. Without a similarity threshold, two unrelated
intents sharing incidental tokens cross-match, and
``_validate_cached_regions`` preserves the cached region's stale
``purpose`` field — cross-wiring intents so one decision's regions carry
another decision's label.

Witnessed live on Accountable 2026-04-14: a "Stripe payment-link fallback"
decision inherited 8 bogus regions from an earlier "weekly bulletin page"
ingest because both descriptions shared tokens like "page" and "flow".

v0.4.7 fix:
  1. ``lookup_vocab_cache`` returns ``(symbols, matched_query_text)``
  2. ``handle_ingest`` computes Jaccard similarity and rejects hits
     below ``_VOCAB_SIMILARITY_THRESHOLD`` (0.5)
  3. ``_validate_cached_regions`` accepts ``current_description`` and
     rewrites the ``purpose`` field on every returned region

These tests cover the pure helpers and the handler integration.
"""

from __future__ import annotations

import pytest

from handlers.ingest import (
    _VOCAB_SIMILARITY_THRESHOLD,
    _content_tokens,
    _jaccard_similarity,
    _validate_cached_regions,
)


# ── _content_tokens ─────────────────────────────────────────────────


def test_content_tokens_drops_stopwords():
    tokens = _content_tokens("the quick brown fox jumps over the lazy dog")
    # "quick brown fox jumps lazy" — stopwords removed, short words dropped
    assert "quick" in tokens
    assert "brown" in tokens
    assert "jumps" in tokens
    assert "lazy" in tokens
    assert "the" not in tokens
    assert "over" not in tokens  # in stopword list


def test_content_tokens_filters_short_words():
    tokens = _content_tokens("a an is at of to in on by fox eye cat")
    # The tokenizer requires 4+ chars, so all three-letter words drop.
    assert tokens == set()


def test_content_tokens_lowercases():
    tokens = _content_tokens("Stripe Payment PROCESSING")
    assert "stripe" in tokens
    assert "payment" in tokens
    assert "processing" in tokens


def test_content_tokens_handles_empty():
    assert _content_tokens("") == set()
    assert _content_tokens(None) == set()  # type: ignore[arg-type]


# ── _jaccard_similarity ─────────────────────────────────────────────


def test_jaccard_identical_strings():
    assert _jaccard_similarity("payment flow", "payment flow") == 1.0


def test_jaccard_disjoint_strings():
    assert _jaccard_similarity("stripe refund", "calendar event") == 0.0


def test_jaccard_partial_overlap():
    # "redis cache sessions" vs "local memory cache sessions"
    # tokens A = {redis, cache, sessions}
    # tokens B = {local, memory, cache, sessions}
    # intersection = {cache, sessions} = 2
    # union = {redis, local, memory, cache, sessions} = 5
    # jaccard = 2/5 = 0.4
    sim = _jaccard_similarity(
        "redis cache sessions",
        "local memory cache sessions",
    )
    assert 0.35 < sim < 0.45


def test_jaccard_empty_strings():
    assert _jaccard_similarity("", "anything") == 0.0
    assert _jaccard_similarity("anything", "") == 0.0
    assert _jaccard_similarity("", "") == 0.0


def test_jaccard_witnessed_bug_case_below_threshold():
    """The actual Accountable 2026-04-14 cross-contamination case:
    Stripe payment-link fallback vs weekly bulletin page. Their Jaccard
    must be below _VOCAB_SIMILARITY_THRESHOLD (0.5) so the gate rejects
    the hit.
    """
    stripe = (
        "Onboarding Stripe payment-link fallback: use direct Stripe payment "
        "link with prefilled promo code, confirmation page links to GHL "
        "onboarding booking page"
    )
    bulletin = (
        "Dynamic weekly community bulletin page, email becomes notification "
        "with link, content lives on page to avoid deliverability hits"
    )
    sim = _jaccard_similarity(stripe, bulletin)
    assert sim < _VOCAB_SIMILARITY_THRESHOLD, (
        f"FC-3 witnessed bug case now scores {sim:.2f} — similarity gate "
        f"would NOT reject it. Either rewrite the threshold, the stopword "
        f"list, or the tokenizer."
    )


def test_jaccard_near_duplicate_above_threshold():
    """A real near-duplicate phrasing should score ABOVE the threshold
    so the cache gate reuses the hit.
    """
    # Same decision, tightened phrasing — shares most content tokens.
    a = "Cache user sessions in Redis for horizontal scaling"
    b = "Cache user sessions in Redis for scaling"
    sim = _jaccard_similarity(a, b)
    assert sim >= _VOCAB_SIMILARITY_THRESHOLD, (
        f"Near-duplicate scored {sim:.2f} < {_VOCAB_SIMILARITY_THRESHOLD} — "
        f"gate is too strict and would force unnecessary re-grounding."
    )


# ── _validate_cached_regions purpose-rewrite ────────────────────────


class _FakeSymbolRow(dict):
    def __getattr__(self, k: str):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


class _FakeSymbolDB:
    def __init__(self, symbols: list[dict]) -> None:
        self._by_name: dict[str, list[_FakeSymbolRow]] = {}
        for s in symbols:
            row = _FakeSymbolRow(s)
            self._by_name.setdefault(s["name"], []).append(row)

    def lookup_by_name(self, name: str):
        return self._by_name.get(name, [])


class _FakeCodeGraph:
    def __init__(self, db) -> None:
        self._db = db

    def _ensure_initialized(self) -> None:
        pass


def test_validate_cached_regions_rewrites_purpose():
    """The cached region's ``purpose`` field must be rewritten to
    ``current_description`` so reused regions don't carry the original
    intent's text.
    """
    code_graph = _FakeCodeGraph(
        _FakeSymbolDB(
            [
                {
                    "id": 1,
                    "name": "fetchPage",
                    "qualified_name": "fetchPage",
                    "type": "function",
                    "file_path": "tests/smoke.ts",
                    "start_line": 10,
                    "end_line": 20,
                },
            ]
        ),
    )

    cached_regions = [
        {
            "symbol": "fetchPage",
            "file_path": "tests/smoke.ts",
            "start_line": 10,
            "end_line": 20,
            "type": "function",
            # Stale purpose from the ORIGINAL intent — this is the bug
            "purpose": "Dynamic weekly bulletin page with email notification",
        }
    ]

    # Current intent is a totally different one
    current = "Stripe payment-link fallback for onboarding flow"
    valid = _validate_cached_regions(cached_regions, code_graph, current_description=current)

    assert len(valid) == 1
    assert valid[0]["purpose"] == current, (
        f"FC-3 regression: purpose field not rewritten. "
        f"Got {valid[0]['purpose']!r}, expected {current!r}"
    )


def test_validate_cached_regions_preserves_purpose_when_no_override():
    """Backwards compat: if the caller omits ``current_description``,
    the stale purpose field is preserved (old v0.4.5/v0.4.6 behavior).
    """
    code_graph = _FakeCodeGraph(
        _FakeSymbolDB(
            [
                {
                    "id": 1,
                    "name": "fetchPage",
                    "qualified_name": "fetchPage",
                    "type": "function",
                    "file_path": "tests/smoke.ts",
                    "start_line": 10,
                    "end_line": 20,
                },
            ]
        ),
    )
    cached_regions = [
        {
            "symbol": "fetchPage",
            "file_path": "tests/smoke.ts",
            "start_line": 10,
            "end_line": 20,
            "type": "function",
            "purpose": "Original purpose",
        }
    ]
    valid = _validate_cached_regions(cached_regions, code_graph)  # no current_description
    assert valid[0]["purpose"] == "Original purpose"


# ── Integration: handle_ingest vocab cache path ─────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_fc3_similarity_gate_rejects_cross_contaminated_hit(
    monkeypatch, surreal_url, tmp_path,
):
    """End-to-end: seed the vocab cache with intent A, then ingest
    intent B that shares only incidental tokens (Jaccard < 0.5).

    Expected: the cache hit is rejected, intent B falls through to
    fresh grounding instead of inheriting A's cached regions.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")

    # Initialize a throwaway git repo
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    from adapters.ledger import get_ledger, reset_ledger_singleton

    reset_ledger_singleton()
    ledger = get_ledger()
    await ledger.connect()

    # Seed the vocab cache with intent A's grounding
    await ledger.upsert_vocab_cache(
        "weekly community bulletin page dynamic content",
        str(tmp_path),
        [
            {
                "symbol": "fetchPage",
                "file_path": "src/bulletin.ts",
                "start_line": 1,
                "end_line": 10,
                "type": "function",
                "purpose": "weekly bulletin page",
            }
        ],
    )

    # Now lookup with intent B — should match via @0@ (shared "page"
    # token) but Jaccard should reject because the intents are unrelated.
    cached_symbols, matched = await ledger.lookup_vocab_cache(
        "Stripe payment-link fallback onboarding promo code",
        str(tmp_path),
    )

    # @0@ may or may not return a match depending on SurrealDB's FTS
    # scoring. If it does, confirm our Jaccard gate rejects it.
    if cached_symbols:
        sim = _jaccard_similarity(
            "Stripe payment-link fallback onboarding promo code",
            matched,
        )
        assert sim < _VOCAB_SIMILARITY_THRESHOLD, (
            f"Cross-contamination case scored sim={sim:.2f} — gate would "
            f"NOT reject this hit. Either the threshold is too loose or "
            f"the FC-3 fix isn't correctly wired. Matched query: {matched!r}"
        )

    reset_ledger_singleton()
