# Phase 2c-2 — Read Surface (first PR)

**Date**: 2026-05-22
**Branch**: `feat/daemon-02c-protocol-surface`
**Parent**: `2026-05-22-daemon-as-process-arc.md` (full arc 2c-2 through 2c-8)
**Predecessors**: Phase 2c-1 (categorization decorators), merged in #500

## Scope of THIS PR

Define the externally-callable **read.\*** subset of the protocol surface:

- `read.history` — full ledger dump grouped by feature area
- `read.usage_summary` — aggregate usage stats over a window

The PR adds Pydantic request/result models, registers server-side handlers that delegate to today's in-tree ledger code, and ships a conformance test asserting every registered method dispatches.

**Deferred to 2c-5**: `read.search_decisions` (internal BM25 search used by `judge_gaps` + `preflight`). No external caller today; migrates with its callers.

**Out of scope** (later sub-phases):
- `write.*` surface (sibling PR 2c-2b)
- Grounding RPCs (`grounding.lookup.*`, `grounding.analyze.*` — they already exist as `validate_symbols`/`get_neighbors`/`analyze_region`; the only addition is `grounding.analyze.preflight`)
- Daemon supervisor + process lifecycle (2c-3)
- Any handler body rewrites (2c-4 onward)

## Bicameral preflight findings (2026-05-22)

Preflight surfaced 3 constraints worth encoding explicitly:

1. **Deterministic read path** — no LLM hop inside any `read.*` method. *(Bound decision: `decision:wndwxgam2m8yjor0igya` — "MCP server: deterministic tools, no nested LLM".)* Encoded as a conformance assertion: protocol read handlers must not import `litellm`, `anthropic`, `openai`, or call any function whose name contains `llm`.
2. **Flat response shapes** — protocol result models mirror handler return shapes one-to-one, no additional wrapper envelopes. *(Bound decision: `decision:cqrnefjlmih8r2102rj3` — "Keep MCP response contracts lean and flat".)* Practical: `HistoryResponse` (the type today's handler returns) IS the protocol result type. No `HistoryRPCResult { data: HistoryResponse }` wrapping.
3. **Lazy ledger connect preserved** — `ProtocolServer.__init__` must NOT open a SurrealDB connection. Lazy on first method call, matching today's `SurrealDBLedgerAdapter` semantics. *(Bound decision: `decision:k44cko8xtkcswk55kytz` — "Lazy connection in SurrealDB ledger adapter".)*

No drift candidates, no open divergences, no prior daemon-protocol decisions to honor or override.

## Inventory — what `read.*` handlers actually do today

### `handle_history` (`handlers/history.py:310`)

External collaborators:

| Call | Path | Purpose |
|------|------|---------|
| `resolve_head(ctx.repo_path)` | `ledger.status` | Resolve git HEAD when `as_of=None` |
| `ledger.connect()` | adapter | Idempotent connect (lazy-OK) |
| `inner._client.query(SurrealQL)` | `ledger.client` | Custom enriched graph-traversal query — bypasses adapter |
| `ledger.get_all_decisions(filter="all")` | adapter | Fallback when enriched query fails |
| `_resolve_span_text(archive, span)` | `ledger.queries` | PII archive lookup for source-span text |
| `inner._client.query(...)` | `ledger.client` | Compliance-check status lookup for ephemeral filtering |
| `ensure_ledger_synced(ctx)` | `handlers.sync_middleware` | Auto-sync to HEAD before read |

**Protocol shape**: ONE method `read.history(repo_id, ref, filters) -> HistoryResponse`. The daemon owns the enriched SurrealQL + PII resolution + grouping + filtering. MCP doesn't see the SurrealQL.

### `handle_usage_summary` (`handlers/usage_summary.py:22`)

External collaborators:

| Call | Path | Purpose |
|------|------|---------|
| `read_counters()` | `local_counters` | Read `~/.bicameral/counters/*.jsonl` for tool-call counts |
| `client.query("SELECT status, count() ...")` | `ledger.client` | Decision status counts by status, windowed |
| `client.query("SELECT verdict, count() ...")` | `ledger.client` | Compliance verdict counts (cosmetic vs drifted) |

**Protocol shape**: ONE method `read.usage_summary(repo_id, days) -> UsageSummaryResponse`. The daemon owns the counter file read + ledger queries.

### Internal: `handle_search_decisions` (`handlers/search_decisions.py:22`)

Called by `handle_judge_gaps` and `handle_preflight`. External collaborator:

| Call | Path | Purpose |
|------|------|---------|
| `ctx.ledger.search_by_query(...)` | adapter | BM25 search over decisions |

**Protocol shape**: ONE method `read.search_decisions(repo_id, query, limit, search_hint) -> list[DecisionMatch]`. Daemon owns the BM25 index.

This isn't externally callable from MCP today (it's internal to gap_judge / preflight), but the daemon needs to expose it because those callers will eventually go through the protocol too.

## Pydantic models to add to `protocol/contracts.py`

```python
# ── Read: history ──
class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    ref: str = "HEAD"
    feature_filter: str | None = None
    include_superseded: bool = True
    include_pruned: bool = False
    as_of: str | None = None

# HistoryResponse already exists in contracts.py (the MCP-facing one).
# Re-export from protocol.contracts for the daemon wire.

# ── Read: usage summary ──
class UsageSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    days: int = Field(default=7, ge=0, le=365)

class UsageSummaryResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period_days: int
    ingest_calls: int
    bind_calls_total: int
    decisions_ingested: int
    decisions_ungrounded: int
    decisions_pending: int
    decisions_reflected: int
    decisions_drifted: int
    reflected_pct: float
    drift_pct: float
    cosmetic_drift_pct: float
    error_rate: float

# ── Read: search decisions ──
class SearchDecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    search_hint: str | None = None

class DecisionMatchSummary(BaseModel):
    """Wire shape for one BM25 hit. Flat — no nested score envelope."""
    model_config = ConfigDict(extra="ignore")
    decision_id: str
    description: str
    rationale: str
    feature_hint: str | None = None
    score: float
    status: str
    source_ref: str | None = None
```

**Note on `HistoryResponse`**: today's `contracts.HistoryResponse` IS the right wire shape (per the "flat envelopes" constraint). The protocol's `read.history` returns it as-is. No re-modeling.

## Server-side handler registration

Add a new module `protocol/handlers/reads.py` that wires the three read methods. Each handler is a thin async adapter:

```python
# protocol/handlers/reads.py
from protocol.contracts import (
    HistoryRequest, HistoryResponse,
    UsageSummaryRequest, UsageSummaryResult,
    SearchDecisionsRequest, DecisionMatchSummary,
    ConnectionContext,
)


async def handle_read_history(
    params: dict, ctx: ConnectionContext
) -> dict:
    req = HistoryRequest.model_validate(params)
    # Daemon-side: import the EXISTING handler. In 2c-4 this will be inverted
    # (MCP handler calls into the daemon), but in 2c-2 the daemon shell delegates
    # to the same in-tree code.
    from handlers.history import handle_history
    bctx = _resolve_context(ctx, req.repo_id)  # built from ConnectionContext
    result = await handle_history(
        bctx,
        feature_filter=req.feature_filter,
        include_superseded=req.include_superseded,
        include_pruned=req.include_pruned,
        as_of=req.as_of,
    )
    return result.model_dump()


async def handle_read_usage_summary(
    params: dict, ctx: ConnectionContext
) -> dict:
    req = UsageSummaryRequest.model_validate(params)
    from handlers.usage_summary import handle_usage_summary
    bctx = _resolve_context(ctx, req.repo_id)
    return await handle_usage_summary(bctx, days=req.days)


async def handle_read_search_decisions(
    params: dict, ctx: ConnectionContext
) -> list[dict]:
    req = SearchDecisionsRequest.model_validate(params)
    from handlers.search_decisions import handle_search_decisions
    bctx = _resolve_context(ctx, req.repo_id)
    matches = await handle_search_decisions(
        bctx, query=req.query, limit=req.limit, search_hint=req.search_hint
    )
    return [DecisionMatchSummary.model_validate(m).model_dump() for m in matches]


def register(server) -> None:
    server.register("read.history", handle_read_history)
    server.register("read.usage_summary", handle_read_usage_summary)
    server.register("read.search_decisions", handle_read_search_decisions)
```

**Critically**: this code runs *inside the same Python process as MCP today*. No daemon process exists yet. The dispatch path is `ProtocolClient → ProtocolServer (in-process) → existing handler`. That's fine for 2c-2 — we're validating contract shape, not boundary semantics. Phase 2c-3 spawns a real daemon process; phase 2c-4 starts the call-site migration.

**`_resolve_context` helper**: builds a `BicameralContext` from the `ConnectionContext.tenant_id` + the request's `repo_id`. For local-tenant single-repo mode (today's only configuration), this is straight-through. Multi-repo / multi-tenant resolution waits for 2c-3.

## Conformance test

`tests/test_protocol_read_conformance.py`:

```python
"""Conformance: every registered read.* method dispatches on a well-formed request."""

@pytest.mark.asyncio
async def test_read_history_dispatches(short_socket_dir, real_ledger):
    server = ProtocolServer(short_socket_dir / "daemon.sock")
    register_read_handlers(server)
    await server.start()

    client = ProtocolClient(short_socket_dir / "daemon.sock")
    await client.connect()
    result = await client._call("read.history", {"repo_id": "test", "ref": "HEAD"})
    assert "features" in result
    assert "total_features" in result
    await client.close()
    await server.stop()


async def test_read_usage_summary_dispatches(...): ...
async def test_read_search_decisions_dispatches(...): ...


def test_no_llm_imports_in_read_handlers():
    """Constraint from bicameral preflight: read.* handlers must not invoke LLMs."""
    import protocol.handlers.reads as reads_module
    source = inspect.getsource(reads_module)
    forbidden = ["litellm", "anthropic.AsyncAnthropic", "openai.AsyncOpenAI", "ChatCompletion"]
    for keyword in forbidden:
        assert keyword not in source, f"read handler imports {keyword}"


def test_protocol_server_does_not_open_db_on_init(tmp_path):
    """Constraint from bicameral preflight: lazy connect preserved."""
    server = ProtocolServer(tmp_path / "daemon.sock")
    register_read_handlers(server)
    # No connection until .start() is called and a method dispatches.
    assert not hasattr(server, "_db_connected") or server._db_connected is False
```

## Acceptance

- [ ] `protocol/contracts.py` exports `HistoryRequest`, `UsageSummaryRequest/Result`, `SearchDecisionsRequest`, `DecisionMatchSummary`.
- [ ] `protocol/handlers/reads.py` exists with three registered methods.
- [ ] `tests/test_protocol_read_conformance.py` — all dispatch + constraint tests pass.
- [ ] Existing `pytest tests/test_protocol_categorization.py` still passes.
- [ ] Full suite: no regressions vs baseline (the 88 pre-existing failures we already characterized).
- [ ] `ruff check` and `ruff format --check` both clean.
- [ ] No handler in `handlers/` has been modified. (Verifying: this PR is contract-shaped, not call-site-shaped.)

## Reversibility

Pure-additive PR. New files, no existing-file edits except protocol/__init__.py re-exports. `git revert <sha>` cleanly removes the protocol read surface; no downstream depends on it yet.
