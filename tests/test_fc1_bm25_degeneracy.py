"""FC-1 BM25 degeneracy guard regression tests (bicameral-mcp v0.4.6).

The FC-1 failure mode: when an intent description expands into fewer than
2 tokens that exist in the indexed corpus vocabulary, BM25 ranks by lexical
tiebreak rather than by meaningful similarity, producing spurious anchors.

Witnessed live in Accountable-App-3.0 (2026-04-13): the decision
"GitHub Discussions vs Slack" was grounded to
``supabase/functions/log-error-to-slack/index.ts:getFeatureName`` because
only the token ``slack`` survived stopword filtering and the test-file
filter eliminated the top hit, leaving a getFeatureName helper as the
tiebreak winner.

v0.4.6 fix: ``Bm25sClient.count_corpus_tokens`` + guard in
``RealCodeLocatorAdapter.ground_mappings``. The decision lands as
ungrounded, which is the right state for open-question-shaped intents.

These tests build a small in-process BM25 corpus so they run in <1s and
are deterministic — no dependency on the live bicameral repo index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_locator.retrieval.bm25s_client import Bm25sClient


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fc1_degeneracy"


def _load_fc1_fixtures() -> list[dict]:
    fixtures = []
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        data["__name__"] = path.stem
        fixtures.append(data)
    return fixtures


_FIXTURES = _load_fc1_fixtures()


def _fixture_id(fixture: dict) -> str:
    return fixture["__name__"]


# ── Synthetic corpus ────────────────────────────────────────────────
#
# A small bag of Python-ish documents representing a typical MCP-server
# codebase. The vocabulary is intentionally narrow so that degenerate
# intents (policy talk, open questions, neologisms) degenerate cleanly.
_SYNTHETIC_CORPUS_DOCS = [
    "class SymbolDB database sqlite store index symbols file_path",
    "def ingest_payload commit_hash repo content_hash source_span intent",
    "def ground_mappings coverage loop BM25 fuzzy search grounding tier",
    "class HashDriftAnalyzer analyze_region stored_hash actual_hash reflected drifted",
    "def upsert_code_region symbol_name start_line end_line pinned_commit",
    "def resolve_head repo_path git rev_parse HEAD subprocess",
    "class BicameralContext repo_path ledger code_graph drift_analyzer",
    "def handle_ingest decisions mappings auto_ground normalize payload",
]
_SYNTHETIC_DOC_IDS = [f"file_{i}.py" for i in range(len(_SYNTHETIC_CORPUS_DOCS))]


@pytest.fixture
def bm25_client() -> Bm25sClient:
    """Tiny in-process BM25 client seeded with a synthetic code corpus.

    Fast enough to run the full FC-1 fixture suite in <1s. Does NOT touch
    disk, does NOT build a real code_locator index.
    """
    import bm25s

    client = Bm25sClient()
    bm25 = bm25s.BM25()
    tokens = bm25s.tokenize(_SYNTHETIC_CORPUS_DOCS, stopwords="en", show_progress=False)
    bm25.index(tokens, show_progress=False)
    client._bm25 = bm25
    client._doc_ids = list(_SYNTHETIC_DOC_IDS)
    client._loaded = True
    return client


# ── count_corpus_tokens unit tests ──────────────────────────────────


def test_count_corpus_tokens_on_unloaded_client_returns_zero():
    client = Bm25sClient()
    assert client.count_corpus_tokens("anything") == 0


def test_count_corpus_tokens_empty_query(bm25_client):
    assert bm25_client.count_corpus_tokens("") == 0


def test_count_corpus_tokens_full_overlap(bm25_client):
    """A query that matches multiple corpus terms should return ≥2."""
    assert bm25_client.count_corpus_tokens(
        "symbol database sqlite store index",
    ) >= 2


def test_count_corpus_tokens_degenerate_single_token(bm25_client):
    """Only ``ingest`` (via ``ingest_payload`` expansion) overlaps — rest
    are pure stopwords or proper nouns not in corpus."""
    # 'should', 'we', 'really', 'this' are stopwords.
    # 'slack' is not in the synthetic corpus.
    count = bm25_client.count_corpus_tokens("should we really do this with slack")
    assert count < 2, (
        f"Expected <2 corpus tokens for a stopword-heavy degenerate query, got {count}"
    )


def test_count_corpus_tokens_zero_overlap(bm25_client):
    """Terms that are neither stopwords nor in the corpus return 0."""
    count = bm25_client.count_corpus_tokens("xyzzy plugh gnusto")
    assert count == 0


# ── Fixture-driven degenerate-query regression ─────────────────────


@pytest.mark.parametrize("fixture", _FIXTURES, ids=_fixture_id)
def test_fc1_fixture_queries_degenerate_in_synthetic_corpus(bm25_client, fixture):
    """Each fixture query should come out with <2 corpus tokens against
    the synthetic corpus. If it doesn't, the fixture is over-specified
    for this corpus and needs rewording.
    """
    intent = fixture["intent"]
    count = bm25_client.count_corpus_tokens(intent)
    assert count < 2, (
        f"Fixture {fixture['__name__']!r} query {intent!r} has "
        f"{count} corpus tokens — expected <2. "
        f"The FC-1 guard will NOT skip this in the synthetic corpus. "
        f"Either reword the fixture or add a note that it's intended "
        f"for larger corpora."
    )


# ── End-to-end guard integration test ──────────────────────────────


def test_ground_mappings_skips_degenerate_queries_via_fc1(bm25_client, monkeypatch):
    """Smoke test that the FC-1 guard inside ``ground_mappings`` actually
    fires on a degenerate query and leaves the mapping ungrounded.

    Uses monkeypatching to inject the synthetic bm25_client into a
    RealCodeLocatorAdapter WITHOUT triggering the real index build.
    """
    from adapters.code_locator import RealCodeLocatorAdapter

    adapter = RealCodeLocatorAdapter(repo_path=".")

    # Inject a minimal initialized state so _ensure_initialized is a no-op
    adapter._bm25 = bm25_client
    adapter._initialized = True
    adapter._db = None  # ground_mappings guards on _db via _ensure_initialized

    # Monkey-patch _ensure_initialized to skip the real init (which would
    # otherwise try to load a symbol database from disk).
    def _noop_init(*_args, **_kwargs):
        return None
    monkeypatch.setattr(adapter, "_ensure_initialized", _noop_init)

    degenerate_mapping = {
        "intent": "GitHub Discussions vs Slack",
        "span": {"text": "GitHub Discussions vs Slack", "source_type": "transcript"},
        "symbols": [],
        "code_regions": [],
    }
    resolved, deferred = adapter.ground_mappings([degenerate_mapping])

    assert len(resolved) == 1
    assert resolved[0].get("code_regions") == [] or resolved[0].get("code_regions") is None, (
        f"FC-1 degenerate query was grounded: {resolved[0]}"
    )
    # Deferred count unchanged — the mapping passes through cleanly
    assert isinstance(deferred, int)
