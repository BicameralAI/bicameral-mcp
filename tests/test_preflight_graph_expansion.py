"""Tests for the 1-hop code-graph expansion in region-anchored preflight (#173).

Two layers:

1. **Adapter unit** — ``RealCodeLocatorAdapter.expand_file_paths_via_graph``
   returns the union of input file paths plus 1-hop neighbor files,
   bounded by ``max_neighbors_per_result``. Exercised against an
   in-memory ``SymbolDB`` with hand-inserted symbols and edges so the
   test doesn't depend on a real codebase index.

2. **Handler integration** — ``_region_anchored_preflight`` in
   ``handlers/preflight.py`` calls the expander, surfaces decisions
   bound to expanded paths with ``confidence=0.7``, and tags
   ``"graph"`` on ``sources_chained``. The structural
   distance scenario: a decision is bound to ``app/src/lib/git/reorder.ts``;
   the caller passes ``["app/src/ui/multi-commit-operation/reorder.tsx"]``
   (a graph neighbor); the decision still surfaces.
"""

from __future__ import annotations

import pytest

from adapters.code_locator import RealCodeLocatorAdapter
from adapters.ledger import reset_ledger_singleton
from code_locator.config import CodeLocatorConfig
from code_locator.indexing.sqlite_store import SymbolDB, SymbolRecord
from context import BicameralContext
from handlers.bind import handle_bind
from handlers.ingest import handle_ingest
from handlers.preflight import handle_preflight


def _build_ingest_payload(description: str) -> dict:
    """Internal-format ingest payload that produces a single ratified mapping.

    Mirrors the shape used by ``test_alpha_contract::_ingest_payload`` with
    ``with_region=False`` + ``signoff=True`` so the test ingest produces an
    ungrounded decision ready to bind in the next step.
    """
    return {
        "query": description,
        "repo": "graph-expand-test-repo",
        "mappings": [
            {
                "intent": description,
                "span": {
                    "source_type": "transcript",
                    "text": description,
                    "source_ref": "graph-expand-test",
                    "speakers": ["test@example.com"],
                    "meeting_date": "2026-05-04",
                },
                "symbols": [],
                "code_regions": [],
                "signoff": {
                    "state": "ratified",
                    "signer": "test@example.com",
                    "ratified_at": "2026-05-04T00:00:00Z",
                    "session_id": None,
                },
            }
        ],
    }


def _stub_adapter_with(db: SymbolDB, max_neighbors: int = 10) -> RealCodeLocatorAdapter:
    """Build a RealCodeLocatorAdapter wired to a hand-built SymbolDB.

    Bypasses the ``_ensure_initialized`` index-presence check so we don't
    have to point at a real codebase. Sets ``_initialized=True`` and
    populates ``_db`` + ``_config`` directly — the only attributes
    ``expand_file_paths_via_graph`` reads.
    """
    adapter = RealCodeLocatorAdapter(repo_path=".")
    adapter._db = db
    adapter._config = CodeLocatorConfig(max_neighbors_per_result=max_neighbors)
    adapter._initialized = True
    return adapter


def _build_synthetic_db(tmp_path) -> SymbolDB:
    """Two files, one edge: ``reorder.tsx`` imports a symbol from ``reorder.ts``."""
    db = SymbolDB(str(tmp_path / "sym.db"))
    db.init_db()
    db.insert_symbols_batch(
        [
            # symbol id 1 — git-layer (where the decision is bound)
            SymbolRecord(
                name="reorder",
                qualified_name="reorder",
                type="function",
                file_path="app/src/lib/git/reorder.ts",
                start_line=10,
                end_line=80,
                signature="export function reorder(...)",
                parent_qualified_name="",
            ),
            # symbol id 2 — UI layer (caller's chosen file)
            SymbolRecord(
                name="Reorder",
                qualified_name="Reorder",
                type="class",
                file_path="app/src/ui/multi-commit-operation/reorder.tsx",
                start_line=4,
                end_line=27,
                signature="export class Reorder ...",
                parent_qualified_name="",
            ),
        ]
    )
    # The UI symbol invokes / imports the git-layer symbol → bidirectional edge.
    db.insert_edges_batch([(2, 1, "imports")])
    return db


# ── Adapter unit tests ──────────────────────────────────────────────────


def test_expander_finds_1_hop_neighbor_file(tmp_path):
    """Passing the UI file alone returns it + the git-layer neighbor."""
    db = _build_synthetic_db(tmp_path)
    adapter = _stub_adapter_with(db)
    expanded, added = adapter.expand_file_paths_via_graph(
        ["app/src/ui/multi-commit-operation/reorder.tsx"], hops=1
    )
    assert "app/src/ui/multi-commit-operation/reorder.tsx" in expanded
    assert "app/src/lib/git/reorder.ts" in expanded
    assert added == ["app/src/lib/git/reorder.ts"]


def test_expander_preserves_input_paths_when_no_neighbors(tmp_path):
    """A file with indexed symbols but no edges yields no expansion."""
    db = SymbolDB(str(tmp_path / "lonely.db"))
    db.init_db()
    db.insert_symbols_batch(
        [
            SymbolRecord(
                name="standalone",
                qualified_name="standalone",
                type="function",
                file_path="app/src/lonely.ts",
                start_line=1,
                end_line=10,
                signature="",
                parent_qualified_name="",
            )
        ]
    )
    adapter = _stub_adapter_with(db)
    expanded, added = adapter.expand_file_paths_via_graph(["app/src/lonely.ts"], hops=1)
    assert expanded == ["app/src/lonely.ts"]
    assert added == []


def test_expander_handles_empty_input():
    db = SymbolDB(":memory:")
    db.init_db()
    adapter = _stub_adapter_with(db)
    expanded, added = adapter.expand_file_paths_via_graph([], hops=1)
    assert expanded == []
    assert added == []


def test_expander_handles_unindexed_file(tmp_path):
    """A file with NO symbols in the index contributes nothing — no crash."""
    db = _build_synthetic_db(tmp_path)
    adapter = _stub_adapter_with(db)
    expanded, added = adapter.expand_file_paths_via_graph(["app/src/never-indexed.ts"], hops=1)
    assert expanded == ["app/src/never-indexed.ts"]
    assert added == []


def test_expander_caps_hub_file_explosion(tmp_path):
    """A hub file with many neighbors does not blow up the result set.

    Per-symbol cap = ``max_neighbors_per_result``; global cap scales with
    input size. With one input file and ``max_neighbors=2``, expansion
    should add at most 2 paths.
    """
    db = SymbolDB(str(tmp_path / "hub.db"))
    db.init_db()
    # 1 hub symbol + 5 neighbor symbols, each in a different file.
    records = [
        SymbolRecord("hub", "hub", "function", "hub.ts", 1, 5, "", ""),
    ]
    for i in range(5):
        records.append(
            SymbolRecord(
                f"neigh_{i}",
                f"neigh_{i}",
                "function",
                f"neigh_{i}.ts",
                1,
                3,
                "",
                "",
            )
        )
    db.insert_symbols_batch(records)
    # Hub imports each of the 5 neighbors. (Use ``imports`` not ``invokes``
    # because the expander now filters to import edges only — see
    # ``test_expander_filters_to_imports_only`` and #64.)
    db.insert_edges_batch([(1, i + 2, "imports") for i in range(5)])

    adapter = _stub_adapter_with(db, max_neighbors=2)
    expanded, added = adapter.expand_file_paths_via_graph(["hub.ts"], hops=1)
    # Per-symbol cap caps the per-symbol neighbor walk at 2, so even though 5
    # neighbors exist, expansion adds at most 2.
    assert len(added) <= 2
    assert len(added) > 0, "imports-edges hub should produce some expansion"
    assert "hub.ts" in expanded


def test_expander_filters_to_imports_only(tmp_path):
    """Per #64: only ``imports`` edges expand; ``invokes`` / ``inherits`` /
    ``contains`` are symbol-level edges that over-broaden the file-level
    expansion. A neighbor reachable only via a non-import edge must NOT
    appear in the expanded set.
    """
    db = SymbolDB(str(tmp_path / "edge_filter.db"))
    db.init_db()
    db.insert_symbols_batch(
        [
            SymbolRecord("caller", "caller", "function", "caller.ts", 1, 5, "", ""),
            SymbolRecord("import_target", "import_target", "function", "imp.ts", 1, 5, "", ""),
            SymbolRecord("invoke_target", "invoke_target", "function", "inv.ts", 1, 5, "", ""),
            SymbolRecord("inherit_target", "inherit_target", "class", "inh.ts", 1, 5, "", ""),
        ]
    )
    db.insert_edges_batch(
        [
            (1, 2, "imports"),  # caller → imp.ts (should expand)
            (1, 3, "invokes"),  # caller → inv.ts (should NOT expand)
            (1, 4, "inherits"),  # caller → inh.ts (should NOT expand)
        ]
    )
    adapter = _stub_adapter_with(db)
    _, added = adapter.expand_file_paths_via_graph(["caller.ts"], hops=1)
    assert added == ["imp.ts"], f"only imports-edged neighbors should expand; got: {added}"


def test_expander_falls_back_when_uninitialized():
    """If the symbol index isn't available, returns inputs unchanged."""
    adapter = RealCodeLocatorAdapter(repo_path=".")
    # _initialized stays False; calling _ensure_initialized would raise
    # because there's no index. The expander must catch that and fall back.
    expanded, added = adapter.expand_file_paths_via_graph(["a.ts", "b.ts"], hops=1)
    assert expanded == ["a.ts", "b.ts"]
    assert added == []


# ── Handler integration test ────────────────────────────────────────────


class _FakeCodeGraph:
    """Minimal code_graph wrapper for handle_preflight: overrides
    ``expand_file_paths_via_graph`` with a hard-coded expansion, forwards
    every other attribute to the real adapter (so ``resolve_symbols`` etc.
    still work for the surrounding ingest/bind calls). Lets us prove the
    handler wiring (sources_chained tag, expansion-provenance confidence)
    without depending on a real symbol index in the test environment.
    """

    def __init__(self, real, *, expansion_for_tsx: list[str]) -> None:
        self._real = real
        self._expansion = expansion_for_tsx
        self.calls: list[list[str]] = []

    def expand_file_paths_via_graph(
        self,
        file_paths: list[str],
        hops: int = 1,
    ) -> tuple[list[str], list[str]]:
        self.calls.append(list(file_paths))
        added = [p for p in self._expansion if p not in file_paths]
        return list(file_paths) + added, added

    def __getattr__(self, name: str):
        # Forward unknown attribute access to the real adapter so other
        # handlers (ingest's resolve_symbols, etc.) keep working.
        return getattr(self._real, name)


@pytest.fixture
def integration_env(monkeypatch, tmp_path):
    """In-memory ledger + git-initialized repo + repo-rooted ctx; same shape
    as ``test_alpha_contract::alpha_env`` pared down to what graph-expansion
    needs. Requires git init because ``ensure_ledger_synced`` walks HEAD.
    """
    import subprocess

    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "graph-expand-repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True)
    # Seed the two files the tests bind / preflight against. handle_bind
    # verifies the file exists at HEAD so we have to materialize them.
    git_layer = repo_root / "app" / "src" / "lib" / "git"
    git_layer.mkdir(parents=True)
    (git_layer / "reorder.ts").write_text(
        "// stub for graph-expansion test\nexport function reorder() { return 0 }\n"
    )
    ui_layer = repo_root / "app" / "src" / "ui" / "multi-commit-operation"
    ui_layer.mkdir(parents=True)
    (ui_layer / "reorder.tsx").write_text(
        "// stub for graph-expansion test\nexport class Reorder {}\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
        cwd=repo_root,
        check=True,
    )

    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    ctx = BicameralContext.from_env()
    yield ctx
    reset_ledger_singleton()


@pytest.mark.asyncio
async def test_preflight_surfaces_via_graph_expansion(integration_env, monkeypatch):
    """Caller passes a UI-layer file; the decision is bound to a git-layer
    file 1 hop away; preflight surfaces it via expansion with
    ``confidence=0.7`` and tags ``sources_chained`` accordingly.
    """
    import dataclasses

    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "1")
    # Stub code_graph: when caller passes the UI file, expansion adds the
    # git-layer file (where the bind lives). BicameralContext is a frozen
    # dataclass; clone with dataclasses.replace to swap in the fake.
    base = BicameralContext.from_env()
    ctx = dataclasses.replace(
        base,
        code_graph=_FakeCodeGraph(
            base.code_graph,
            expansion_for_tsx=["app/src/lib/git/reorder.ts"],
        ),
    )

    ingest_resp = await handle_ingest(
        ctx,
        _build_ingest_payload("Drag-to-reorder commits via the git-layer reorder helper."),
    )
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]
    bind_resp = await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "app/src/lib/git/reorder.ts",
                "symbol_name": "reorder",
                "start_line": 10,
                "end_line": 80,
            }
        ],
    )
    assert bind_resp.bindings[0].error is None

    pf_resp = await handle_preflight(
        ctx,
        topic="refactor the reorder UI to use a text-editor flow",
        file_paths=["app/src/ui/multi-commit-operation/reorder.tsx"],
    )

    # The bound decision must surface even though caller passed the UI file.
    decision_ids = [d.decision_id for d in pf_resp.decisions]
    assert decision_id in decision_ids, (
        f"bound decision {decision_id} must surface via 1-hop expansion; "
        f"got: {decision_ids}; sources={pf_resp.sources_chained}"
    )

    # And it should be marked as expansion-provenance, not direct.
    # `decisions` on PreflightResponse is BriefDecision (no confidence field);
    # the confidence lives on the underlying DecisionMatch via the region
    # lookup. The signal we can assert end-to-end is sources_chained.
    assert "region" in pf_resp.sources_chained
    assert "graph" in pf_resp.sources_chained, (
        f"expected 'graph' in sources_chained when graph "
        f"expansion produced extra hits; got: {pf_resp.sources_chained}"
    )


@pytest.mark.asyncio
async def test_preflight_does_not_tag_expanded_when_direct_pin_alone(integration_env, monkeypatch):
    """When caller passes the bound file directly, expansion may add neighbors
    but the decision is reached via a direct pin — `sources_chained` should
    contain `region` but NOT `graph` (the existing decision
    is direct, not expanded).
    """
    import dataclasses

    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "1")
    # Expander returns no extra paths when the caller already passed the
    # bound file directly (simulates a clean discovery).
    base = BicameralContext.from_env()
    ctx = dataclasses.replace(
        base,
        code_graph=_FakeCodeGraph(base.code_graph, expansion_for_tsx=[]),
    )

    ingest_resp = await handle_ingest(ctx, _build_ingest_payload("Direct-pin baseline."))
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]
    await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "app/src/lib/git/reorder.ts",
                "symbol_name": "reorder",
                "start_line": 10,
                "end_line": 80,
            }
        ],
    )

    pf_resp = await handle_preflight(
        ctx,
        topic="edit reorder",
        file_paths=["app/src/lib/git/reorder.ts"],
    )

    decision_ids = [d.decision_id for d in pf_resp.decisions]
    assert decision_id in decision_ids
    assert "region" in pf_resp.sources_chained
    assert "graph" not in pf_resp.sources_chained, (
        f"direct pin alone must not tag 'graph'; got: {pf_resp.sources_chained}"
    )
