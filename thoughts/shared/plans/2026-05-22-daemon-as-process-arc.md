# Daemon-as-Process — Multi-PR Arc

**Date**: 2026-05-22
**Owner**: Jin
**Parent plan**: `~/github/bicameral/thoughts/shared/plans/2026-05-21-daemon-extraction-and-universal-ingest-egress.md`
**Predecessor**: Phase 2c-1 (read/write tool categorization, merged in #500)

## Architectural commitment

**The daemon is a real process. Always.** `bicameral-daemon` is not a library MCP imports; it's a binary MCP launches and talks to over a Unix domain socket (HTTPS later for hosted mode). This locks the testing strategy, the failure modes, and the deployment story — no in-process shortcut.

Consequences:

- MCP handlers stop calling `from ledger.queries import X` directly. Every ledger/governance/dashboard operation becomes an async RPC through `ProtocolClient`.
- `bicameral-mcp` installs `bicameral-daemon` as a peer with its own CLI entrypoint, not as an importable Python package.
- Tests that verify the daemon boundary spin up a real daemon subprocess. No fake in-memory ProtocolServer dressed up as integration.
- Single-writer property (#301-class races) becomes real — the daemon owns the SurrealDB connection, no concurrent MCP processes can collide.

## Test strategy (per Fowler advisory, 2026-05-22)

**Three-tier pyramid, explicitly labeled:**

1. **Handler logic — sociable in-process tests** (today's pattern, unchanged). Real `SurrealDBLedgerAdapter` over `memory://`, real Pydantic models, real query results. Tests handler decision-making, not the IPC boundary. ~95% of the suite stays this shape.

2. **Boundary tests — per-test logical daemon** (new). Each test owns its socket path and ledger DB file. A pytest fixture hides the process lifecycle: starts with subprocess-per-test, swappable to a pooled-warmed-daemon later when cold-start cost actually bites. Tests:
   - Wire serialization (Pydantic round-trips through JSON)
   - Connection lifecycle (daemon-killed-mid-call, reconnect, refused-connection)
   - Concurrency (single-writer serialization under contended writes)
   - **Volume target**: tens of tests, not hundreds.

3. **Contract tests — in-process ProtocolServer** (separate file, separate label). Verifies *shape* of the contract: every registered method, every request/result model. Cheap, fast, no subprocess. Lives in the protocol package's own conformance suite. Does NOT crowd out tier 2.

**Anti-patterns explicitly rejected:**

- Session-scoped shared daemon → state bleeds, debugging hell, isolation traded for unmeasured perf.
- In-memory ProtocolServer pretending to be a boundary test → false confidence.
- Pre-optimization (pooling, async warm-up, etc.) before measuring subprocess startup cost.

## Sub-phase breakdown

Each sub-phase is one PR. Each PR is independently shippable — MCP keeps working whether or not a given operation has been migrated yet (until the cleanup phase). Branches off `dev`.

### 2c-2 — Protocol surface expansion (this branch)
**Branch**: `feat/daemon-02c-protocol-surface`
**Size**: ~1 week. Reads first; writes follow in a sibling PR (2c-2b) if scope demands.

Define every read/write/grounding/system operation today's 19 handlers depend on. Each gets:
- Pydantic request + result model in `protocol/contracts.py`
- Method-name slot in the categorized namespace (`read.X`, `write.Y`, etc.)
- Server-side handler in `protocol/server.py` (or a new `protocol/handlers/` submodule) that validates the request, calls today's in-tree ledger code, and serializes the response
- Conformance test entry — every registered method passes a smoke dispatch

**Critically, no call sites change in 2c-2.** MCP handlers still do `from ledger.queries import X` directly. The protocol surface becomes real; nobody uses it yet. This isolates "is the contract complete?" from "does the rewire work?"

Scope split: start with **reads** (`read.history`, `read.usage_summary`, and the internal queries reads decompose into — `decision_exists`, `project_decision_status`, `get_canonical_id`, etc.). Reads are idempotent, no ledger mutation, lowest risk. Writes follow in 2c-2b once the pattern is validated.

**Acceptance**:
- Inventory doc lists every operation handlers call.
- Read surface has 100% coverage in the conformance suite.
- `protocol/contracts.py` exports all read models.
- Full test suite still passes (no regressions; no call sites changed).
- ~300-600 LOC + tests, single PR.

### 2c-2b — Write surface expansion (sibling PR)
**Branch**: `feat/daemon-02c-protocol-surface-writes`

Same pattern, write operations. Decisions inserted, sources removed, regions updated, bindings created, governance verdicts written, audit log emitted. Conformance covers all of them.

**Acceptance**: write surface 100% in conformance suite. Still no call sites changed.

### 2c-3 — Daemon supervisor + process lifecycle
**Branch**: `feat/daemon-03-supervisor`
**Size**: ~3-4 days.

`bicameral-mcp daemon {start,stop,restart,status,uninstall}` actually launches a supervised daemon process running `ProtocolServer` with the registered handlers. The daemon:
- Spawns `surreal start` (or the embedded SurrealDB instance)
- Listens on a UDS socket
- Publishes socket path to `~/.bicameral/daemon.json`
- Health-check endpoint
- Restart-on-crash supervision
- macOS LaunchAgent install via `setup_wizard.py`

**MCP does not use the daemon yet.** Daemon runs in parallel, idle. This isolates lifecycle bugs from call-site bugs.

**Acceptance**: `bicameral-mcp daemon start` succeeds. Client can connect to the socket and call `system.version`. Daemon survives SIGHUP / config reload. Crash → automatic restart within 5s. No effect on today's MCP behavior.

### 2c-4 — First call-site migration: `read.history` (load-bearing prototype)
**Branch**: `feat/daemon-04-history-via-daemon`
**Size**: ~2 days.

Pick **one** read handler. Rewrite its body to use `ProtocolClient` instead of direct imports. Build the **per-test daemon fixture**: `tmp_path` for the ledger DB, ephemeral socket path, subprocess started in the fixture, torn down after the test. Validate end-to-end:
- Latency in single-call mode (cold + warm)
- Reconnect-on-daemon-crash semantics
- Test ergonomics (do tests feel painful? what would make them better?)
- Error semantics (daemon returns malformed response → what does the handler see?)

**This is the load-bearing PR.** Everything that comes after assumes the test fixture works. If the prototype reveals the fixture needs to be different (pool, alternate transport, anything), redesign here before bulk migration.

**Acceptance**: `handle_history` does not import `ledger.status` directly. Existing `test_history_*` tests pass via the new fixture. New boundary tests cover wire+lifecycle+reconnect for at least one read operation.

### 2c-5 — Migrate remaining reads
**Branch**: `feat/daemon-05-reads-via-daemon`
**Size**: ~3 days.

`read.usage_summary`, `read.preflight` (via `grounding.analyze.preflight` — which internally is a composite of reads + grounding analyze calls), and the internal read helpers. Each rewritten one at a time, tests updated to use the fixture.

### 2c-6 — Migrate writes in safety order
**Branch**: `feat/daemon-06-writes-via-daemon`
**Size**: ~1 week.

Order matters. Safest first:
1. **Telemetry-only writes** (no ledger mutation): `write.feedback`, `write.skill_begin`, `write.skill_end`. Sends events, no rows.
2. **Append-only ledger writes**: `write.ingest`, `write.link_commit`. Adds rows, never deletes.
3. **Mutation writes**: `write.ratify`, `write.resolve_compliance`, `write.resolve_collision`, `write.judge_gaps`. Updates existing rows. Concurrency matters here — boundary test must cover contended writes through the daemon's single-writer queue.
4. **Destructive writes**: `write.remove_decision`, `write.remove_source`. Deletes. Boundary test for "two callers race to remove the same decision."

Probably split into 2-3 PRs along these layers.

### 2c-7 — Grounding + dashboard via daemon
**Branch**: `feat/daemon-07-grounding-and-dashboard`
**Size**: ~3-5 days.

- `grounding.lookup.bind`, `grounding.analyze.preflight`, the batch analyzers.
- Dashboard server moves out of MCP into the daemon: the daemon hosts HTTP+SSE; MCP no longer starts a dashboard server. `system.dashboard` becomes "return the daemon's dashboard URL" rather than "spawn a server."

### 2c-8 — Cleanup + invariant assertions
**Branch**: `feat/daemon-08-cleanup`
**Size**: ~2 days.

- Delete unused ledger imports from `handlers/`.
- Delete the in-tree dashboard server (now daemon-owned).
- Add CI assertion: `git grep "from ledger\." handlers/ | wc -l` returns 0.
- Add CI assertion: `git grep "from dashboard\." handlers/ | wc -l` returns 0.
- Add CI assertion: `git grep "from audit_log import" handlers/ | wc -l` returns 0.
- Update CLAUDE.md to reflect the new boundary.

After 2c-8 lands, **Phase 3** (the actual cross-repo split) becomes mechanical: every file under `daemon/` and `protocol/server-side handlers/` moves to the private `BicameralAI/bicameral-daemon` repo via `git filter-repo` preserving history. `bicameral-protocol` extracts as its own public package. Issues transfer per the parent plan's mapping table.

## Cross-cutting design decisions

**Connection lifecycle.** Each MCP server process opens one `ProtocolClient` connection at startup, attaches the tenant, holds it for the process lifetime. Reconnect-on-failure with exponential backoff (200ms → 2s, max 5 retries). Failed reconnect surfaces as a typed `DaemonUnreachableError` that handlers translate to a user-facing "daemon offline — run `bicameral-mcp daemon start`" message.

**Request shape.** Every RPC carries `(repo_id, ref)` because the daemon is multi-repo-aware. `tenant_id` lives on the connection (from `system.attach`), not in every payload. This matches the Phase 2b decision baked into `ConnectionContext`.

**Backpressure.** Single-writer means writes serialize. A long-running ingest can block other writes. The daemon's writer pool needs at least a "writes queue depth" diagnostic surface for the dashboard. Out of scope for 2c-2 through 2c-6; tracked separately.

**Error semantics.** Three error tiers:
1. **Transport** — connection dropped, malformed frame. `ProtocolError` raised by client.
2. **Protocol** — unknown method, type-validation failure. Server returns JSON-RPC error response; client raises `ProtocolError` with the structured `code` + `message`.
3. **Domain** — ledger constraint violation, governance veto, validation rejection. Server returns a success result with a domain-error envelope (e.g. `IngestResult(status="refused", reason="size_limit_exceeded")`). Handlers translate to today's MCP-level error responses unchanged.

**Migration safety.** Between 2c-4 and 2c-8, MCP has *some* handlers going through the daemon and *some* going direct. That's fine because:
- The daemon and the MCP process both connect to the same SurrealDB (just via different paths).
- Reads through either route see the same data.
- Writes through either route end up in the same DB. The single-writer guarantee only takes effect once every writer is going through the daemon — until then, we accept that two MCP processes can still race on direct writes. We don't lose ground.

The cleanup phase (2c-8) is where the single-writer property becomes real.

## Reversibility

Through 2c-7: every PR is independently revertable. Reverting a call-site migration restores the direct import. Reverting the supervisor PR removes the `daemon start` command. The protocol surface (2c-2 / 2c-2b) is additive — nothing depends on it until 2c-4.

After 2c-8: still revertable, but multi-PR revert. The CI assertions in 2c-8 are the point of no return for the in-tree direct-import path.

After Phase 3 (repo split): not revertable without remerging the private repo. By then we should have hosted-mode in production and the question is moot.

## Acceptance for the whole arc

Once 2c-8 lands:
- `git grep "from ledger\." handlers/` returns empty
- `git grep "from dashboard\." handlers/` returns empty
- `git grep "from audit_log import" handlers/` returns empty
- All MCP handler tests pass
- All boundary tests pass against a real daemon subprocess
- `bicameral-mcp daemon start` is the only path to a running ledger
- The dashboard is daemon-owned
- Phase 3 (repo split) is a `git filter-repo` operation, not an architectural rewrite
