"""Phase 3 end-to-end tests — full MCP pipeline with artifact output.

Structured around the 5 SDLC failure modes Bicameral solves. Each test uses
the REAL code locator to discover symbols in the repo, builds payloads
from those real results, ingests them via the real ledger, and dumps
the actual SurrealDB graph state as artifacts.

No fake file paths. No mock data. The knowledge graph you see in the
report is exactly what SurrealDB contains after running real tools
against the real codebase.

Run: SURREAL_URL=memory:// REPO_PATH=. pytest tests/test_phase3_integration.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from adapters.code_locator import get_code_locator
from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.decision_status import handle_decision_status
from handlers.detect_drift import handle_detect_drift
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit
from handlers.search_decisions import handle_search_decisions

# ── Fixtures ─────────────────────────────────────────────────────────

RESULTS_DIR = Path(__file__).parent.parent / "test-results" / "e2e"


@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    """Fresh in-memory ledger for every test."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(Path(__file__).resolve().parents[1]))
    reset_ledger_singleton()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    reset_ledger_singleton()


@pytest.fixture
def ctx():
    """Build BicameralContext from test env vars."""
    return BicameralContext.from_env()


def _dump(name: str, data: dict | list) -> None:
    """Write JSON artifact for qualitative review."""
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, default=str))


async def _dump_graph(label: str) -> dict:
    """Dump raw SurrealDB graph state and write as artifact."""
    ledger = get_ledger()
    inner = getattr(ledger, "_inner", ledger)
    await inner._ensure_connected()
    client = inner._client

    decisions = await client.query("SELECT * FROM decision")
    symbols = await client.query("SELECT * FROM symbol")
    regions = await client.query("SELECT * FROM code_region")
    binds_to = await client.query("SELECT * FROM binds_to")
    locates = await client.query("SELECT * FROM locates")
    depends_on = await client.query("SELECT * FROM depends_on")
    cursors = await client.query("SELECT * FROM source_cursor")

    graph = {
        "label": label,
        "nodes": {
            "decisions": decisions,
            "symbols": symbols,
            "code_regions": regions,
            "source_cursors": cursors,
        },
        "edges": {
            "binds_to": binds_to,
            "locates": locates,
            "depends_on": depends_on,
        },
        "counts": {
            "intents": len(decisions),  # alias for old test assertions
            "decisions": len(decisions),
            "symbols": len(symbols),
            "code_regions": len(regions),
            "binds_to": len(binds_to),
            "locates": len(locates),
            "depends_on": len(depends_on),
        },
    }
    _dump(f"graph_{label}", graph)
    return graph


def _response_dict(response) -> dict:
    """Convert a Pydantic response to a JSON-serializable dict."""
    return json.loads(response.model_dump_json())


# ── Real code locator helpers ────────────────────────────────────────


def _locate_hits(adapter, query_str: str, limit: int = 2) -> list[dict]:
    """Resolve a bag-of-words query to {file_path, symbol_name, line_number}
    hits for test payload construction.

    Tokenizes the query, fuzzy-validates each token against the symbol index,
    and looks up the matched symbol rows in SymbolDB. Replaces the legacy
    search_code helper — no BM25/RRF needed.
    """
    tokens = [t for t in query_str.split() if len(t) >= 2]
    if not tokens:
        return []

    validated = adapter.validate_symbols(tokens)
    if not validated:
        return []

    adapter._ensure_initialized()
    db = adapter._db

    hits: list[dict] = []
    for v in validated:
        sid = v.get("symbol_id")
        if sid is None:
            continue
        row = db.lookup_by_id(sid)
        if row is None:
            continue
        hits.append(
            {
                "file_path": row["file_path"],
                "symbol_name": row["name"],
                "line_number": row["start_line"],
                "score": v.get("match_score", 0) / 100.0,
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _build_payload_from_real_code(
    adapter,
    query: str,
    search_queries: list[str],
    intents: list[dict],
    repo: str,
    source_type: str = "transcript",
    source_ref: str = "meeting-001",
    commit_hash: str = "e2e-real",
) -> dict:
    """Build an ingest payload using REAL code locator results.

    Each intent in `intents` has:
      - text: the spoken/written decision
      - intent: the extracted intent string
      - search: symbol-ish query to resolve to a real code region (or None for ungrounded)
      - speaker: who said it
    """
    mappings = []
    for i, item in enumerate(intents):
        code_regions = []
        symbols = []

        if item.get("search"):
            hits = _locate_hits(adapter, item["search"], limit=2)
            for hit in hits:
                fp = hit.get("file_path", "")
                sym = hit.get("symbol_name", "")
                line = hit.get("line_number", 1)
                if fp:
                    code_regions.append(
                        {
                            "file_path": fp,
                            "symbol": sym or fp.split("/")[-1],
                            "type": "function",
                            "start_line": line,
                            "end_line": line + 20,
                            "purpose": f"Located from search terms: {item['search']!r}",
                        }
                    )
                    if sym:
                        symbols.append(sym)

        mappings.append(
            {
                "span": {
                    "span_id": f"e2e-{i}",
                    "source_type": source_type,
                    "text": item["text"],
                    "speaker": item.get("speaker", ""),
                    "source_ref": source_ref,
                },
                "intent": item["intent"],
                "symbols": symbols,
                "code_regions": code_regions,
                "dependency_edges": [],
            }
        )

    return {
        "query": query,
        "repo": repo,
        "commit_hash": commit_hash,
        "analyzed_at": "2026-03-20T10:00:00Z",
        "mappings": mappings,
    }


# ══════════════════════════════════════════════════════════════════════
# SDLC 1: CONSTRAINT_LOST
# "A known technical limit surfaces mid-sprint instead of at design time"
# Tool: bicameral.search — pre-flight before coding
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_constraint_lost__search_surfaces_prior_decisions(ctx):
    """After ingesting decisions grounded to real code, searching for
    related work surfaces the constraint BEFORE a developer starts coding."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    payload = _build_payload_from_real_code(
        adapter,
        query="Sprint 14 — ledger + search improvements",
        search_queries=["ledger ingest", "search decisions"],
        intents=[
            {
                "text": "Ledger ingestion must be idempotent — re-ingesting the same transcript should be a no-op",
                "intent": "Idempotent ledger ingestion for transcripts",
                "search": "ingest_payload ledger",
                "speaker": "Jin",
            },
            {
                "text": "Search should auto-sync to HEAD before returning results",
                "intent": "Auto-sync ledger to HEAD before search",
                "search": "search_decisions link_commit",
                "speaker": "Silong",
            },
            {
                "text": "We need optimistic locking for concurrent writes — two agents ingesting at once can corrupt the graph",
                "intent": "Optimistic locking for concurrent ledger writes",
                "search": None,  # ungrounded — not yet built
                "speaker": "Jin",
            },
        ],
        repo=repo,
        source_ref="sprint-14-planning-2026-03-20",
    )

    ingest_result = await handle_ingest(ctx, payload)
    assert ingest_result.ingested
    _dump("01_constraint_lost_ingest", _response_dict(ingest_result))

    # Developer starts working on "idempotent ingestion" — search surfaces the prior decision
    search_result = await handle_search_decisions(
        ctx,
        query="idempotent ingest ledger",
        min_confidence=0.1,
    )
    _dump("01_constraint_lost_search", _response_dict(search_result))

    descriptions = [m.description.lower() for m in search_result.matches]
    assert any("idempotent" in d or "ingest" in d for d in descriptions), (
        f"Search did not surface prior constraint. Got: {descriptions}"
    )

    graph = await _dump_graph("01_constraint_lost")
    assert graph["counts"]["intents"] >= 2


# ══════════════════════════════════════════════════════════════════════
# SDLC 2: CONTEXT_SCATTERED
# "The 'why' behind a decision is split across Slack, Notion, and memory"
# Tool: bicameral.ingest — normalizes intent from multiple sources
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_context_scattered__ingest_unifies_sources(ctx):
    """Ingesting from transcript and PRD produces a unified graph
    where decisions from both sources are queryable together."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    # Source 1: meeting transcript about server architecture
    transcript_payload = _build_payload_from_real_code(
        adapter,
        query="Architecture sync — MCP server design",
        search_queries=["server MCP tool"],
        intents=[
            {
                "text": "The MCP server should expose deterministic tools — no nested LLM calls",
                "intent": "MCP server: deterministic tools, no nested LLM",
                "search": "server tool dispatch",
                "speaker": "Jin",
            },
        ],
        repo=repo,
        source_type="transcript",
        source_ref="arch-sync-2026-03-24",
    )

    # Source 2: PRD about code locator requirements
    prd_payload = _build_payload_from_real_code(
        adapter,
        query="Code locator PRD — symbol index requirements",
        search_queries=["validate_symbols symbol"],
        intents=[
            {
                "text": "Symbol resolution must be deterministic — tree-sitter extraction with fuzzy validation, no LLM in the indexing path",
                "intent": "Deterministic symbol resolution via tree-sitter + fuzzy validation",
                "search": "validate_symbols symbol index",
                "speaker": "",
            },
            {
                "text": "Every file path returned by search must exist on disk — zero hallucination tolerance",
                "intent": "Anti-hallucination: all search results must reference real files",
                "search": "validate_symbols symbol",
                "speaker": "",
            },
        ],
        repo=repo,
        source_type="prd",
        source_ref="prd-code-locator-v1",
    )

    r1 = await handle_ingest(ctx, transcript_payload, source_scope="meetings", cursor="2026-03-24")
    r2 = await handle_ingest(ctx, prd_payload, source_scope="prds", cursor="prd-cl-v1")
    _dump("02_context_scattered_ingest_transcript", _response_dict(r1))
    _dump("02_context_scattered_ingest_prd", _response_dict(r2))

    assert r1.ingested and r2.ingested
    assert r1.source_cursor.source_scope == "meetings"
    assert r2.source_cursor.source_scope == "prds"

    # Unified query across both sources
    status = await handle_decision_status(ctx, filter="all")
    _dump("02_context_scattered_unified_status", _response_dict(status))

    source_types = {d.source_type for d in status.decisions}
    assert "transcript" in source_types, "Missing transcript source"
    assert "prd" in source_types, "Missing PRD source"

    graph = await _dump_graph("02_context_scattered")
    assert graph["counts"]["intents"] >= 3


# ══════════════════════════════════════════════════════════════════════
# SDLC 3: DECISION_UNDOCUMENTED
# "A verbal 'let's do X' never lands in a ticket or ADR"
# Tool: bicameral.status — tracks decided vs built, surfaces ungrounded
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_decision_undocumented__status_surfaces_ungrounded(ctx):
    """Ungrounded intents (no code region mapped) are explicitly surfaced
    so verbal decisions don't silently disappear."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    payload = _build_payload_from_real_code(
        adapter,
        query="Sprint planning — mixed grounded and ungrounded",
        search_queries=[],
        intents=[
            {
                "text": "The decision ledger schema should support graph traversal for blast radius queries",
                "intent": "Graph traversal for blast radius in decision ledger",
                "search": "schema ledger surreal",
                "speaker": "Jin",
            },
            {
                "text": "Zylorphian kvetnar should harmonize the plornax bifurcation",
                "intent": "Zylorphian kvetnar plornax bifurcation",
                "search": None,  # ungrounded — gibberish, no matching code
                "speaker": "Silong",
            },
        ],
        repo=repo,
        source_ref="sprint-planning-2026-03-22",
    )

    ingest_result = await handle_ingest(ctx, payload)
    _dump("03_undocumented_ingest", _response_dict(ingest_result))

    assert ingest_result.stats.ungrounded >= 1
    assert any(
        "zylorphian" in (u.get("description", "") or u.get("decision_id", "")).lower()
        for u in ingest_result.pending_grounding_decisions
    ), (
        f"Expected 'Zylorphian' in pending_grounding_decisions, got: {ingest_result.pending_grounding_decisions}"
    )

    status = await handle_decision_status(ctx, filter="all")
    _dump("03_undocumented_status_all", _response_dict(status))

    ungrounded = [d for d in status.decisions if d.status == "ungrounded"]
    assert len(ungrounded) >= 1
    assert status.summary.get("ungrounded", 0) == len(ungrounded)
    total = sum(status.summary.values())
    assert total == len(status.decisions)

    graph = await _dump_graph("03_undocumented")


# ══════════════════════════════════════════════════════════════════════
# SDLC 4: REPEATED_EXPLANATION
# "Same context tax paid twice — once to design, once to engineering"
# Tool: search + code locator — retrieves full decision provenance
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_repeated_explanation__search_returns_full_provenance(ctx):
    """When a developer searches, they get the full provenance chain:
    who decided it, when, what real code it maps to."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    # Ingest decisions about the code locator's internals
    payload = _build_payload_from_real_code(
        adapter,
        query="Code locator design decisions",
        search_queries=[],
        intents=[
            {
                "text": "Symbol extraction must use tree-sitter for deterministic parsing across 7 languages",
                "intent": "Tree-sitter for multi-language symbol extraction",
                "search": "symbol_extractor tree_sitter extract",
                "speaker": "Silong",
            },
            {
                "text": "The index must auto-refresh when HEAD changes — stale index is worse than no index",
                "intent": "Auto-refresh code index on HEAD mismatch",
                "search": "ensure_index_matches_repo rebuild",
                "speaker": "Jin",
            },
        ],
        repo=repo,
        source_ref="design-review-2026-03-25",
    )
    await handle_ingest(ctx, payload)

    # Developer asks about symbol extraction — gets full provenance
    search_result = await handle_search_decisions(
        ctx,
        query="tree-sitter symbol extraction parsing",
        min_confidence=0.1,
    )
    _dump("04_repeated_explanation_search", _response_dict(search_result))

    for match in search_result.matches:
        assert match.source_ref, f"Match '{match.description}' missing source_ref"
        assert match.confidence > 0
        if match.status != "ungrounded":
            assert len(match.code_regions) > 0, (
                f"Grounded match '{match.description}' has no code_regions"
            )

    graph = await _dump_graph("04_repeated_explanation")


# ══════════════════════════════════════════════════════════════════════
# SDLC 5: TRIBAL_KNOWLEDGE
# "Only one person knows why the system works the way it does"
# Tool: bicameral.drift — surfaces institutional memory tied to code
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_tribal_knowledge__drift_surfaces_decisions_for_file(ctx):
    """When reviewing a file, drift detection surfaces every decision
    that touches it — using real file paths from the code locator."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    # Find a real file that the code locator knows about
    hits = _locate_hits(adapter, "contracts response pydantic", limit=10)
    target_file = None
    for hit in hits:
        fp = hit.get("file_path", "")
        if fp and (Path(repo) / fp).exists():
            target_file = fp
            break

    if not target_file:
        pytest.skip("Code locator found no real files for drift test")

    # Ingest a decision that maps to this real file
    payload = _build_payload_from_real_code(
        adapter,
        query=f"Decisions touching {target_file}",
        search_queries=[],
        intents=[
            {
                "text": f"The contracts in {target_file} must stay lean and agent-consumable — no nested objects deeper than 2 levels",
                "intent": "Keep MCP response contracts lean and flat for agent consumption",
                "search": target_file.replace("/", " ").replace(".py", ""),
                "speaker": "Jin",
            },
        ],
        repo=repo,
        source_ref="design-review-2026-03-26",
    )
    await handle_ingest(ctx, payload)

    # Drift check on the real file
    drift_result = await handle_detect_drift(ctx, target_file)
    _dump("05_tribal_knowledge_drift", _response_dict(drift_result))

    assert drift_result.file_path == target_file

    graph = await _dump_graph("05_tribal_knowledge")


# ══════════════════════════════════════════════════════════════════════
# INTEGRATION: Full lifecycle + graph integrity
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_full_lifecycle_graph_integrity(ctx):
    """Ingest -> link_commit -> status -> search -> drift — verify the graph
    is internally consistent at each step. All code references are real."""
    repo = os.environ["REPO_PATH"]
    adapter = get_code_locator()

    # Step 1: Ingest with real code references
    payload = _build_payload_from_real_code(
        adapter,
        query="Full lifecycle test — ledger + code locator",
        search_queries=[],
        intents=[
            {
                "text": "BM25 search must return ranked results with provenance tags",
                "intent": "BM25 search with provenance-tagged results",
                "search": "bm25 search ranked",
                "speaker": "Silong",
            },
            {
                "text": "The ledger adapter must lazy-connect on first use",
                "intent": "Lazy connection in SurrealDB ledger adapter",
                "search": "ledger adapter connect surreal",
                "speaker": "Jin",
            },
            {
                "text": "Zylorphian kvetnar should harmonize the plornax bifurcation across teams",
                "intent": "Zylorphian kvetnar plornax bifurcation",
                "search": None,  # ungrounded — gibberish, no matching code
                "speaker": "Jin",
            },
        ],
        repo=repo,
        source_ref="lifecycle-test",
    )

    r_ingest = await handle_ingest(ctx, payload)
    assert r_ingest.ingested
    assert r_ingest.stats.intents_created >= 2
    _dump("06_lifecycle_01_ingest", _response_dict(r_ingest))

    # Step 2: Link commit
    r_link = await handle_link_commit(ctx, "HEAD")
    assert r_link.commit_hash != ""
    _dump("06_lifecycle_02_link_commit", _response_dict(r_link))

    # Step 3: Status
    r_status = await handle_decision_status(ctx, filter="all")
    total = sum(r_status.summary.values())
    assert total == len(r_status.decisions)
    _dump("06_lifecycle_03_status", _response_dict(r_status))

    # Step 4: Search
    r_search = await handle_search_decisions(
        ctx, query="BM25 search provenance", min_confidence=0.1
    )
    assert len(r_search.matches) >= 1
    _dump("06_lifecycle_04_search", _response_dict(r_search))

    # Step 5: Drift on a real file from the ingest
    drift_file = None
    for d in r_status.decisions:
        for region in d.code_regions:
            if region.file_path and (Path(repo) / region.file_path).exists():
                drift_file = region.file_path
                break
        if drift_file:
            break

    if drift_file:
        r_drift = await handle_detect_drift(ctx, drift_file)
        _dump("06_lifecycle_05_drift", _response_dict(r_drift))

    # Step 6: Full graph dump
    graph = await _dump_graph("06_lifecycle_final")

    # Graph integrity (v4: decision→binds_to→code_region)
    decision_ids = {i.get("id") for i in graph["nodes"]["decisions"]}
    region_ids = {r.get("id") for r in graph["nodes"]["code_regions"]}
    for edge in graph["edges"]["binds_to"]:
        assert edge.get("in") in decision_ids, f"Dangling binds_to.in: {edge.get('in')}"
        assert edge.get("out") in region_ids, f"Dangling binds_to.out: {edge.get('out')}"

    # At least one ungrounded
    statuses = {d.status for d in r_status.decisions}
    assert "ungrounded" in statuses
    assert r_ingest.stats.regions_linked >= 2


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_adapters_are_real():
    """Confirm no mock adapters are in the chain."""
    code_locator = get_code_locator()
    ledger = get_ledger()

    assert "Mock" not in type(code_locator).__name__
    assert "Mock" not in type(ledger).__name__
