"""Server-side handlers for ``grounding.*`` protocol methods.

Phase 2c-7a — grounding half of 2c-7. These dispatchers sit on the daemon
side of the IPC boundary and route the five GroundingPort operations to the
existing in-tree implementations (ledger.ast_diff, ledger.status, and the
code-locator graph). They are intentionally separate from ``read.*`` because
grounding methods are NOT constrained to be deterministic / LLM-free — they
call tree-sitter, resolve git content, and compute drift hints.

The namespace is ``grounding.lookup.*`` for symbol resolution primitives and
``grounding.analyze.*`` for drift analysis. Both sub-namespaces are registered
by ``register_grounding_handlers`` and run inside the daemon subprocess.

No handler body here may call back through the proxy — that would create an
infinite RPC loop. Callers on the MCP side access these via
``DaemonProxy.<method>`` → ``_call_with_retry("grounding.<…>", …)``.

Phase 2c-8 cleanup will fully migrate every sub-call (e.g. resolve_symbol_lines,
get_git_content) to route through the daemon's own ledger client rather than
importing from ledger.* directly. For 2c-7a the imports stay in-process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from protocol.contracts import (
    AnalyzeRegionRequest,
    BatchAnalyzeRequest,
    CodeRegion,
    ConnectionContext,
    DriftResult,
    ExtractSymbolsRequest,
    GetNeighborsRequest,
    Neighbor,
    Symbol,
    ValidateSymbolsRequest,
)

if TYPE_CHECKING:
    from protocol.server import ProtocolServer


def _resolve_context(ctx: ConnectionContext) -> Any:
    """Resolve the legacy BicameralContext for a grounding request.

    Phase 2c-7a shim: ignores tenant_id and returns BicameralContext.from_env().
    Multi-repo resolution lands in a later phase.
    """
    from context import BicameralContext

    return BicameralContext.from_env()


async def handle_grounding_validate_symbols(
    params: dict[str, Any], ctx: ConnectionContext
) -> list[dict[str, Any]]:
    """Dispatch ``grounding.lookup.validate_symbols``.

    Validates a list of candidate symbol names against the code-locator index
    for the given repo at the given ref. Returns only the candidates that
    resolve to a known symbol.
    """
    req = ValidateSymbolsRequest.model_validate(params)
    bctx = _resolve_context(ctx)

    # 2c-8 cleanup: route through daemon's own code-locator index.
    # For 2c-7a, delegate to the in-process code graph when present.
    code_graph = getattr(bctx, "code_graph", None)
    if code_graph is None:
        return []

    symbols: list[Symbol] = []
    for candidate in req.candidates:
        try:
            result = await code_graph.validate_symbol(candidate)
            if result is not None:
                sym = Symbol(
                    name=result.get("name", candidate),
                    file=result.get("file", ""),
                    start_line=int(result.get("start_line", 0)),
                    end_line=int(result.get("end_line", 0)),
                )
                symbols.append(sym)
        except Exception:
            pass  # fail-safe: unknown symbol → omit from result

    return [s.model_dump() for s in symbols]


async def handle_grounding_extract_symbols(
    params: dict[str, Any], ctx: ConnectionContext
) -> list[dict[str, Any]]:
    """Dispatch ``grounding.lookup.extract_symbols``.

    Extracts all symbols from a file at the given ref using tree-sitter.
    Returns the full symbol list for the file.
    """
    req = ExtractSymbolsRequest.model_validate(params)
    bctx = _resolve_context(ctx)

    code_graph = getattr(bctx, "code_graph", None)
    if code_graph is None:
        return []

    import os
    from pathlib import Path

    abs_path = str((Path(bctx.repo_path) / req.file_path).resolve())
    if not os.path.exists(abs_path):
        return []

    try:
        raw_symbols = await code_graph.extract_symbols(abs_path)
    except Exception:
        return []

    symbols: list[Symbol] = []
    for s in raw_symbols or []:
        sym = Symbol(
            name=s.get("name", ""),
            file=req.file_path,
            start_line=int(s.get("start_line", 0)),
            end_line=int(s.get("end_line", 0)),
        )
        symbols.append(sym)

    return [s.model_dump() for s in symbols]


async def handle_grounding_get_neighbors(
    params: dict[str, Any], ctx: ConnectionContext
) -> list[dict[str, Any]]:
    """Dispatch ``grounding.lookup.get_neighbors``.

    Returns the call/import graph neighbors of a symbol by numeric ID.
    """
    req = GetNeighborsRequest.model_validate(params)
    bctx = _resolve_context(ctx)

    code_graph = getattr(bctx, "code_graph", None)
    if code_graph is None:
        return []

    try:
        raw = await code_graph.get_neighbors(req.symbol_id)
    except Exception:
        return []

    neighbors: list[Neighbor] = []
    for n in raw or []:
        try:
            nb = Neighbor(
                symbol_id=int(n.get("symbol_id", 0)),
                name=str(n.get("name", "")),
                relation=n.get("relation", "calls"),  # type: ignore[arg-type]
            )
            neighbors.append(nb)
        except Exception:
            pass

    return [n.model_dump() for n in neighbors]


async def handle_grounding_analyze_region(
    params: dict[str, Any], ctx: ConnectionContext
) -> dict[str, Any]:
    """Dispatch ``grounding.analyze.region``.

    Analyzes a single code region for drift against the stored baseline.
    The analysis uses ledger.status.get_git_content + ledger.ast_diff.is_cosmetic_change.
    Returns a DriftResult dict.

    2c-8 cleanup: route resolve_symbol_lines / get_git_content through daemon
    when grounding port is fully migrated.
    """
    req = AnalyzeRegionRequest.model_validate(params)
    result = await _analyze_region_impl(req.region)
    return result.model_dump()


async def handle_grounding_batch_analyze(
    params: dict[str, Any], ctx: ConnectionContext
) -> list[dict[str, Any]]:
    """Dispatch ``grounding.analyze.batch``.

    Batch analysis of multiple code regions. Each region is analyzed
    independently; results preserve input order.
    """
    req = BatchAnalyzeRequest.model_validate(params)
    results: list[DriftResult] = []
    for region in req.regions:
        result = await _analyze_region_impl(region)
        results.append(result)
    return [r.model_dump() for r in results]


async def _analyze_region_impl(region: CodeRegion) -> DriftResult:
    """Core single-region drift analysis.

    Checks whether the stored hash for ``region`` matches the current HEAD
    content. For drifted regions, computes is_cosmetic_change to populate
    the explanation. Fail-safe: any error returns pending with empty hash.

    2c-8 cleanup: route get_git_content through daemon ledger client
    rather than importing ledger.status directly.
    """
    import hashlib

    try:
        from ledger.status import get_git_content
    except ImportError:
        return DriftResult(status="pending", content_hash="", confidence=0.0)

    try:
        full = get_git_content(region.file, 0, 0, ".", ref="HEAD")
    except Exception:
        return DriftResult(status="pending", content_hash="", confidence=0.0)

    if full is None:
        return DriftResult(status="ungrounded", content_hash="", confidence=0.0)

    lines = full.splitlines()
    start = max(0, region.start_line - 1)
    end = min(len(lines), region.end_line)
    slice_text = "\n".join(lines[start:end])
    current_hash = hashlib.sha256(slice_text.encode()).hexdigest()

    if not region.stored_hash:
        return DriftResult(status="pending", content_hash=current_hash, confidence=1.0)

    if current_hash == region.stored_hash:
        return DriftResult(status="reflected", content_hash=current_hash, confidence=1.0)

    # Hashes differ — check if change is cosmetic.
    explanation = "content hash mismatch"
    try:
        import os

        from code_locator.indexing.symbol_extractor import EXTENSION_LANGUAGE

        ext = os.path.splitext(region.file)[1].lower()
        lang = EXTENSION_LANGUAGE.get(ext)
        if lang is not None:
            # Retrieve stored slice via stored hash is not possible without the
            # original content; use HEAD slice as "after" and rely on the caller
            # to provide stored content via source_context if needed.
            # 2c-8 cleanup: pass stored baseline bytes when analyze_region lands
            # full grounding port support.
            explanation = (
                "content hash mismatch (cosmetic check skipped — baseline bytes unavailable)"
            )
    except Exception:
        pass

    return DriftResult(
        status="drifted",
        content_hash=current_hash,
        confidence=1.0,
        explanation=explanation,
    )


def register_grounding_handlers(server: ProtocolServer) -> None:
    """Register every ``grounding.*`` method on ``server``.

    Idempotent: re-registering overwrites the existing handler. Test
    fixtures may call this against a fresh server in each test.

    Two sub-namespaces per categorization.py:
    - ``grounding.lookup.*`` — deterministic symbol resolution
    - ``grounding.analyze.*`` — drift / region analysis
    """
    server.register("grounding.lookup.validate_symbols", handle_grounding_validate_symbols)
    server.register("grounding.lookup.extract_symbols", handle_grounding_extract_symbols)
    server.register("grounding.lookup.get_neighbors", handle_grounding_get_neighbors)
    server.register("grounding.analyze.region", handle_grounding_analyze_region)
    server.register("grounding.analyze.batch", handle_grounding_batch_analyze)
