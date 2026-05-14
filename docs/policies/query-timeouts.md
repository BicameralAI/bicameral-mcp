# Ledger query timeouts (#224)

Every ledger query is bounded by a wallclock timeout. Queries that
exceed their budget raise ``LedgerTimeoutError`` (a subclass of
``LedgerError``) rather than hanging the agent indefinitely.

## Default budgets

| Class | Default | Range (clamped) | Where it's used |
|---|---|---|---|
| ``read`` | 5.0 s | 0.5 – 120 s | Point queries, shallow SELECTs (the default for every ledger call) |
| ``drift`` | 30.0 s | 1 – 600 s | Heavy graph-traversal queries (currently: ``handlers/history.py::_fetch_all_decisions_enriched``) |

The two classes are deliberate — see [the audit decision below](#why-only-two-classes).

## Configuration

Set under ``.bicameral/config.yaml`` (operator-supplied, project-local):

```yaml
query_timeout_read_seconds: 5
query_timeout_drift_seconds: 30
```

### Fail-closed behavior

Bad config never produces an unbounded query.

| Config value | Resolved budget |
|---|---|
| Missing key | Default |
| ``"fast"`` / any string | Default |
| ``True`` / ``False`` | Default |
| ``-1``, ``0``, NaN, Inf | Default |
| ``0.01`` (below min) | Clamped to MIN |
| ``9999`` (above max) | Clamped to MAX |
| Valid numeric in range | Used as-is |

Out-of-range values are **clamped** rather than substituted with the default so
operator intent ("I want a long-but-bounded budget") is preserved. Truly
malformed values (NaN, negative, non-numeric) fall back to the documented default —
those aren't operator intent; they're config errors.

## Env override (debugging)

Set ``BICAMERAL_QUERY_TIMEOUT_DISABLE=1`` to skip the wrap entirely.
Use for:

- Intentional data export / recovery operations that legitimately run long.
- Local debugging when a slow query is what you're trying to understand.

The flag matches the precedent set by ``BICAMERAL_INGEST_RATE_LIMIT_DISABLE``.
It is **read fresh on every query**, so test fixtures can toggle it via
``monkeypatch.setenv`` without restarting the process.

## Error shape

When a query exceeds its budget, ``LedgerTimeoutError`` carries:

| Attribute | Description |
|---|---|
| ``sql_prefix`` | First 200 chars of the SQL (truncated for log safety) |
| ``timeout_class`` | ``"read"`` or ``"drift"`` |
| ``elapsed_seconds`` | Actual wallclock at the point of cancellation |
| ``budget_seconds`` | Configured budget that was exceeded |

Existing ``except LedgerError`` handlers catch this transparently —
``LedgerTimeoutError`` is a subclass. Code that needs to distinguish
timeout from other ledger errors can match the subclass directly.

## Telemetry

Each timeout fire appends one entry to a process-local ring buffer in
``ledger/timeout_telemetry.py``:

- Capacity: 1000 entries (older drop automatically).
- Surfaced via the ``recent_timeout_count`` field on
  ``PreflightResponse`` so a Claude Code hook can read the recent
  count without a SurrealDB roundtrip.
- Reset on process restart — per-session granularity matches the
  session-start hook surfacing.

To completely disable telemetry, set ``BICAMERAL_TELEMETRY`` to a CSV
that excludes the relevant scope. The ring buffer itself has no PII;
``sql_prefix`` is capped at 200 chars but a sufficiently long table
name + WHERE clause could leak a column name or ID prefix. Trade off
observability vs. zero-info-leak by disabling the scope.

## Why only two classes

The initial design considered per-call override knobs and a third
"slow-but-legitimate" class. We deferred both:

- **Per-call override knob.** Adding a third public parameter to
  ``LedgerClient.query`` for an unmeasured need is YAGNI. If
  ``drift`` (30s) proves insufficient, a future cycle adds a
  ``timeout_seconds: float | None = None`` kwarg that bypasses the
  class lookup — forward-compat preserved.
- **Third class.** The drift-class call site is currently a single
  one — the enriched-fetch full-tree query in
  ``handlers/history.py``. The other workflows that initially
  looked drift-shaped (preflight, sync_middleware, link_commit)
  turned out to chain many individually-fast queries; each
  individual query stays inside ``read`` budget. Adding a third
  class without a concrete site needing it would be premature.

## Deterministic gate vs. agent hooks (#205 doctrine)

The ``asyncio.wait_for`` wrap in
``ledger/client.py::LedgerClient._run_with_timeout`` is the
**deterministic server-side gate**. It fires identically regardless
of which MCP client is on the other end — generic MCP clients,
custom integrations, Claude Code. That gate is the truth.

For **Claude-as-agent specifically**, the timeout posture is
surfaced via Claude Code hooks in ``.claude/hooks/`` that read the
ring buffer + config and emit context to stderr where Claude Code
routes it back to the model. The hooks are **advisory only** —
they exit 0 always, never block, and the deterministic wrap still
fires whether they ran or not. See
[claude-hooks-mcp-integration.md](claude-hooks-mcp-integration.md)
for the hook design.

## Governance

The wrap is registered in ``governance-gates.yaml`` as the backing
gate for any SKILL.md text that claims a "queries time out" default.
The skill-governance lint will fail to find a backing gate for that
claim only if the gate entry is removed; the wrap itself is
unconditional.
