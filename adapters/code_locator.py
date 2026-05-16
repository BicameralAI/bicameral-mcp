"""Code locator adapter — MCP-native code locator backed by real index.

Exposes validate_symbols, get_neighbors, extract_symbols, and resolve_symbols
as direct methods. The server no longer performs keyword or vector code
search — callers resolve code regions themselves and hand paths/symbols
to the server via bind and preflight.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

from code_locator_runtime import (
    ensure_index_matches_repo,
    ensure_runtime_env,
)

logger = logging.getLogger(__name__)


# #243 Piece B — singleton cache. Pre-fix, ``get_code_locator()`` returned
# a fresh adapter per call, which (combined with lazy ``_ensure_initialized``)
# meant the FIRST tool call after server boot paid the index-build cost AND
# could race the index check on multiple concurrent tool calls.
#
# Cache keyed by ``REPO_PATH`` so multi-repo correctness is preserved (any
# test that swaps REPO_PATH mid-process still gets a fresh adapter for the
# new path). Single global was rejected at signoff (Q1 of #243 phase-2 spec)
# for exactly this reason.
_INSTANCE_CACHE: dict[str, RealCodeLocatorAdapter] = {}


def get_code_locator() -> RealCodeLocatorAdapter:
    """Return the code locator adapter backed by a real indexed repo.

    Singleton-by-REPO_PATH (#243): subsequent calls with the same
    ``REPO_PATH`` return the same adapter instance, so the server-startup
    init path sets up the index once and every subsequent tool dispatch
    reuses the warm instance. Eager init lives in ``initialize()``;
    callers that don't await ``initialize()`` still get lazy init via
    ``_ensure_initialized()`` on first use (test contexts).
    """
    repo_path = str(Path(os.getenv("REPO_PATH", ".")).resolve())
    cached = _INSTANCE_CACHE.get(repo_path)
    if cached is not None:
        return cached
    instance = RealCodeLocatorAdapter(repo_path=repo_path)
    _INSTANCE_CACHE[repo_path] = instance
    return instance


def reset_code_locator_cache() -> None:
    """Test-only hook: drop all cached adapter instances.

    Production paths never call this — the cache lives for the lifetime
    of the process. Tests that swap ``REPO_PATH`` between assertions can
    use this to force a fresh adapter on the next ``get_code_locator()``
    call. Mirrors the ``adapters.ledger.reset_ledger_singleton`` pattern.
    """
    _INSTANCE_CACHE.clear()


class RealCodeLocatorAdapter:
    """MCP-native code locator — exposes deterministic primitives only.

    validate_symbols() → fuzzy-match candidates against symbol index
    get_neighbors()    → 1-hop structural graph traversal
    extract_symbols()  → tree-sitter symbol extraction (no index needed)
    resolve_symbols()  → symbol name → code region lookup for ingest
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._repo_path = str(Path(repo_path).resolve())
        self._initialized = False
        self._validate_tool = None
        self._neighbors_tool = None
        # #380 — lock makes ``_ensure_initialized`` safe to call concurrently
        # from the background-init asyncio Task (running in the default
        # executor thread pool) AND from worker threads spawned by tool
        # handlers via ``asyncio.to_thread(ctx.code_graph.validate_symbols, ...)``.
        # First holder runs init; everyone else blocks until it finishes,
        # then sees ``self._initialized = True`` and returns immediately.
        self._init_lock = threading.Lock()
        # #380 — handle to the background init Task (set by
        # ``initialize_in_background``). ``wait_until_ready`` awaits it
        # without re-running init; failure is re-raised to the caller so
        # the fail-loud contract from #243 is preserved (relocated from
        # boot-time to first-tool-call-time).
        self._init_task: asyncio.Task | None = None

    def _ensure_initialized(self) -> None:
        """Lazy init of SymbolDB, config, and tool instances.

        Thread-safe (#380): the body is serialized via ``self._init_lock``
        so a sync caller on a worker thread (from ``asyncio.to_thread``)
        will block on the background init Task instead of racing it.
        Whichever thread acquires the lock first runs the init body; the
        loser sees ``self._initialized`` True and returns.
        """
        if self._initialized:
            return

        with self._init_lock:
            # Re-check after lock acquire — another thread may have
            # finished init while we were waiting for the lock.
            if self._initialized:
                return
            self._run_init_body()

    def _run_init_body(self) -> None:
        """Actual init work — extracted so tests can monkey-patch this
        without bypassing the lock/state contract in ``_ensure_initialized``.
        """
        ensure_runtime_env()
        from code_locator.config import load_config
        from code_locator.indexing.sqlite_store import SymbolDB
        from code_locator.tools.get_neighbors import GetNeighborsTool
        from code_locator.tools.validate_symbols import ValidateSymbolsTool

        config = load_config()
        ensure_index_matches_repo(self._repo_path, config)

        db = SymbolDB(config.sqlite_db)
        if db.symbol_count() == 0:
            db.close()
            raise RuntimeError(
                "Code locator index is empty. Run: python -m code_locator index <repo_path>"
            )

        self._db = db
        self._config = config
        self._validate_tool = ValidateSymbolsTool(db, config)
        self._neighbors_tool = GetNeighborsTool(db, config)
        self._initialized = True

    async def initialize(self) -> None:
        """Async wrapper around ``_ensure_initialized()`` — awaits the
        sync init in a thread-pool executor so the event loop stays
        responsive.

        Pre-#380 this was the only path ``server.py:serve_stdio`` used,
        and it was awaited inline before opening the MCP stdio
        transport — every cold boot paid the index-load cost (sqlite-vec
        + tree-sitter + BM25 pickle, ~45s on a 150MB graph) BEFORE the
        ``initialize`` JSON-RPC reply could land, which exceeded Claude
        Code's 30s MCP startup timeout on real-world repos.

        Post-#380 the startup hook calls ``initialize_in_background()``
        instead, which schedules the same work but returns immediately.
        This method remains for tests + lazy-init paths that genuinely
        want a synchronous wait.
        """
        if self._initialized:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_initialized)

    def initialize_in_background(self) -> None:
        """Schedule index init as a background asyncio Task and return
        immediately (#380).

        Called from ``server.py:serve_stdio`` AFTER the dashboard sidecar
        starts but BEFORE ``stdio_server()`` opens the MCP transport, so
        the JSON-RPC ``initialize`` reply lands inside Claude Code's 30s
        startup timeout regardless of how large the symbol index is.

        Concurrency model (#380):
        - The Task runs ``_ensure_initialized`` in the default executor
          (thread pool), so the event loop stays free to serve protocol
          traffic.
        - Tool handlers reach the index via ``asyncio.to_thread(
          ctx.code_graph.validate_symbols, ...)`` — those worker threads
          call ``_ensure_initialized`` synchronously, which lock-blocks
          on the background Task. First tool call (typically preflight)
          eats the latency that boot used to.
        - If the background Task raises, the exception is captured by
          the Task; a ``done_callback`` logs the bare error to stderr so
          operators see the failure even before any tool call lands.
          The next ``_ensure_initialized`` call re-runs init and raises
          the same error to its caller — preserving #243's fail-loud
          contract (relocated, not removed).

        Idempotent: subsequent calls before the Task completes are
        no-ops; calls after a successful init are no-ops; calls after a
        failed init schedule a fresh retry attempt.
        """
        if self._initialized:
            return
        if self._init_task is not None and not self._init_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is in a sync context (e.g., tests
            # that import the adapter without running an event loop).
            # Fall back to a synchronous init so the caller's next
            # public-method call works.
            self._ensure_initialized()
            return

        async def _run_in_executor() -> None:
            # ``loop.run_in_executor`` returns a Future, not a coroutine, so
            # we wrap it so ``loop.create_task`` (which requires a coroutine)
            # accepts it. The awaited Future propagates the executor-thread
            # exception back into the Task, which the done_callback then
            # logs to stderr.
            await loop.run_in_executor(None, self._ensure_initialized)

        self._init_task = loop.create_task(_run_in_executor())

        def _log_failure(task: asyncio.Task) -> None:
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                # Match the boot-time stderr format the operator was
                # already trained to look for. Tool calls will surface
                # the same error to the client when they hit the lock.
                print(
                    f"[code_locator] background init FAILED (#380) — first tool "
                    f"call will surface this to the client: {exc}\n"
                    "Run: python -m code_locator index <repo_path>",
                    file=sys.stderr,
                )

        self._init_task.add_done_callback(_log_failure)

    async def wait_until_ready(self) -> None:
        """Await pending background init, raising on failure (#380).

        Optional explicit gate for callers that want to *await* readiness
        from an async context rather than rely on sync ``_ensure_initialized``
        blocking inside ``asyncio.to_thread``. Re-raises the original
        ``RuntimeError`` so a code-locator tool dispatcher can return a
        structured error response to the MCP client.
        """
        if self._initialized:
            return
        if self._init_task is not None:
            # Awaiting a done Task re-raises its exception; awaiting a
            # pending Task waits for completion first. Either way the
            # caller gets fail-loud semantics.
            await self._init_task
            return
        await self.initialize()

    def validate_symbols(self, candidates: list[str]) -> list[dict]:
        """Fuzzy-match candidate symbol names against the codebase index."""
        self._ensure_initialized()
        results = self._validate_tool.execute({"candidates": candidates})
        return [r.model_dump() for r in results]

    def _validate_with_threshold(self, candidates: list[str], threshold: int) -> list[dict]:
        """Fuzzy-match with a custom threshold (for coverage loop broadening)."""
        self._ensure_initialized()
        original = self._validate_tool.config.fuzzy_threshold
        try:
            self._validate_tool.config.fuzzy_threshold = threshold
            results = self._validate_tool.execute({"candidates": candidates})
            return [r.model_dump() for r in results]
        finally:
            self._validate_tool.config.fuzzy_threshold = original

    def get_neighbors(self, symbol_id: int) -> list[dict]:
        """1-hop structural graph traversal around a symbol."""
        self._ensure_initialized()
        results = self._neighbors_tool.execute({"symbol_id": symbol_id})
        return [r.model_dump() for r in results]

    # Hard cap on the number of caller-supplied seeds we expand. Mirrors the
    # contract documented in #64: ≤10 input seeds × ≤max_neighbors_per_result
    # neighbors per seed, so the worst-case response is still bounded even
    # when the caller passes a large file_paths list. Tightens the cost
    # envelope vs the per-config-only cap. Tunable via the PR's #64 lineage
    # if telemetry shows we're losing recall.
    _MAX_EXPANSION_SEEDS = 10

    def expand_file_paths_via_graph(
        self,
        file_paths: list[str],
        hops: int = 1,
    ) -> tuple[list[str], list[str]]:
        """Expand caller-supplied file paths to include 1-hop *import* graph
        neighbors.

        For each input file, look up its indexed symbols, fetch each
        symbol's 1-hop ego graph filtered to **import edges only**, and
        collect the file paths those neighbor symbols live in. The expanded
        set is the union of inputs and neighbor files.

        **Why imports only** (per #64): import is a *file-level* structural
        dependency edge ("module A's contract is referenced by module B"),
        which matches the granularity of the region-anchored decision
        lookup. ``invokes`` / ``inherits`` / ``contains`` are *symbol-level*
        edges that broaden the expansion to "any file whose symbols are
        used by my file's symbols," which over-fires for the recall
        contract this method backs. If telemetry surfaces real-world
        contradictions that imports-only misses, widen the filter then —
        not preemptively.

        Returns ``(expanded, added)`` where ``expanded`` is the deduped
        union (preserving caller order for inputs, then appending
        newly-discovered neighbor files) and ``added`` is the list of file
        paths NOT in the original input — the caller uses this to mark
        expanded matches with lower confidence than direct pins.

        Bounds (mirrors #64's spec):
          - At most ``_MAX_EXPANSION_SEEDS`` (=10) input seeds are walked.
          - For each seed, at most ``max_neighbors_per_result`` symbols are
            walked; for each symbol, at most ``max_neighbors_per_result``
            neighbors are inspected.
          - Global cap on the added set is the product so the worst-case
            response is still bounded for hub seeds.
        Falls back gracefully (returns input unchanged + empty added list)
        on any exception or if the symbol index is unavailable.

        Used by ``handlers/preflight.py::_region_anchored_preflight`` to
        lift the strict ``WHERE file_path IN $fps`` recall ceiling so the
        contradiction-capture loop fires even when the caller picks a
        structurally-near-but-not-exact file. See issue #173 (and the
        superseded #64 for the imports-only design rationale).
        """
        if not file_paths or hops < 1:
            return list(file_paths), []
        try:
            self._ensure_initialized()
        except Exception:
            return list(file_paths), []

        per_symbol_cap = self._config.max_neighbors_per_result
        # Cap total NEW paths added by expansion. With ≤10 seeds and
        # ≤per_symbol_cap neighbors each, the worst case is bounded.
        global_cap = max(per_symbol_cap, per_symbol_cap * self._MAX_EXPANSION_SEEDS)

        # Cap the number of input seeds we expand from. Caller can still pass
        # more file_paths to the underlying ledger lookup — we just don't
        # blow up the graph walk.
        seeds = [fp for fp in file_paths if fp][: self._MAX_EXPANSION_SEEDS]

        original_set = {fp for fp in file_paths if fp}
        added_paths: list[str] = []
        added_set: set[str] = set()

        for fp in seeds:
            try:
                symbols = self._db.lookup_by_file(fp) or []
            except Exception:
                continue
            for sym in symbols[:per_symbol_cap]:
                if len(added_paths) >= global_cap:
                    break
                sym_id = sym["id"]
                try:
                    neighbors = self._db.get_ego_graph(sym_id, hops=hops) or []
                except Exception:
                    continue
                for n in neighbors[:per_symbol_cap]:
                    if len(added_paths) >= global_cap:
                        break
                    if (n.get("edge_type") or "") != "imports":
                        continue
                    nfp = (n.get("file_path") or "").strip()
                    if not nfp or nfp in original_set or nfp in added_set:
                        continue
                    added_set.add(nfp)
                    added_paths.append(nfp)
            if len(added_paths) >= global_cap:
                break

        # Preserve caller order for the input prefix; append newly-added in
        # discovery order.
        expanded: list[str] = []
        for fp in file_paths:
            if fp and fp not in expanded:
                expanded.append(fp)
        expanded.extend(added_paths)
        return expanded, added_paths

    def neighbors_for(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> tuple[str, ...]:
        """Return 1-hop neighbor symbol addresses for a code span.

        Phase 3 (#60) protocol: resolve the symbol at ``(file, start, end)``
        via the existing symbol index, fetch its 1-hop neighbors, return
        their addresses (``"<file>::<symbol_name>"``) as a sorted tuple.
        Returns ``()`` when no symbol resolves to the span — matcher
        gracefully degrades on the Jaccard signal.
        """
        self._ensure_initialized()
        try:
            sym_id = self._resolve_symbol_id_for_span(file_path, start_line, end_line)
            if sym_id is None:
                return ()
            neighbors = self._neighbors_tool.execute({"symbol_id": sym_id})
        except Exception:
            return ()
        addresses = sorted(
            f"{getattr(n, 'file_path', '')}::{getattr(n, 'symbol_name', '') or getattr(n, 'name', '')}"
            for n in neighbors
        )
        return tuple(addresses)

    def _resolve_symbol_id_for_span(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> int | None:
        """Look up the symbol_id whose span contains the given line range.

        Uses the already-initialized ``self._db`` (set up in
        ``_ensure_initialized``) via ``lookup_by_file``, then picks the
        smallest enclosing symbol (most specific match). Returns
        ``None`` if no symbol's span covers the requested range —
        caller treats this as "no neighbors known" and the matcher's
        Jaccard signal contributes zero.

        PR #73 review history:
        - Earlier draft opened a fresh ``SymbolDB(...)`` per call,
          leaking SQLite handles (CodeRabbit MAJOR adapters/code_locator.py:136).
        - It also referenced ``config.sqlite_db_path``, which doesn't
          exist on ``CodeLocatorConfig`` — the real attribute is
          ``sqlite_db``. The ``AttributeError`` was silently swallowed
          by ``neighbors_for``'s broad ``except``, so the method
          always returned ``()`` and the continuity Jaccard signal
          was permanently zero in production (Devin CRITICAL).
        Both fixed by reusing ``self._db``.
        """
        rows = self._db.lookup_by_file(file_path)
        best_id: int | None = None
        best_span: int = 1 << 30
        for r in rows:
            r_start, r_end = r["start_line"], r["end_line"]
            if r_start <= start_line and r_end >= end_line:
                span = r_end - r_start
                if span < best_span:
                    best_span, best_id = span, r["id"]
        return best_id

    async def extract_symbols(self, file_path: str) -> list[dict]:
        """Extract symbols from a file via tree-sitter (no LLM)."""
        from code_locator.indexing.symbol_extractor import extract_symbols

        abs_path = str(Path(file_path).resolve())
        records = extract_symbols(abs_path, self._repo_path)

        symbols = []
        for rec in records:
            sym_type = rec.type
            if sym_type not in ("function", "class", "module", "file"):
                sym_type = "function"
            symbols.append(
                {
                    "name": rec.qualified_name or rec.name,
                    "type": sym_type,
                    "start_line": rec.start_line,
                    "end_line": rec.end_line,
                }
            )
        return symbols

    def resolve_symbols(self, payload: dict) -> dict:
        """For each mapping with symbols[] but no code_regions, look up symbol
        names in the code graph and populate code_regions."""
        mappings = payload.get("mappings")
        if not mappings:
            return payload

        needs_resolution = any(m.get("symbols") and not m.get("code_regions") for m in mappings)
        if not needs_resolution:
            return payload

        try:
            self._ensure_initialized()
            db = self._db
        except Exception as exc:
            logger.warning("[resolve_symbols] cannot open symbol DB: %s", exc)
            return payload

        resolved_mappings = []
        for mapping in mappings:
            symbol_names = mapping.get("symbols") or []
            code_regions = mapping.get("code_regions") or []

            if symbol_names and not code_regions:
                for name in symbol_names:
                    try:
                        rows = db.lookup_by_name(name)
                    except Exception as exc:
                        logger.warning(
                            "[resolve_symbols] lookup_by_name failed for '%s': %s", name, exc
                        )
                        rows = []
                    for row in rows:
                        code_regions.append(
                            {
                                "symbol": row["qualified_name"] or row["name"],
                                "file_path": row["file_path"],
                                "start_line": row["start_line"],
                                "end_line": row["end_line"],
                                "type": row["type"],
                                "purpose": mapping.get("intent", ""),
                            }
                        )
                if code_regions:
                    mapping = {**mapping, "code_regions": code_regions}
                else:
                    logger.debug(
                        "[resolve_symbols] no symbols found in index for: %s", symbol_names
                    )

            resolved_mappings.append(mapping)

        return {**payload, "mappings": resolved_mappings}
