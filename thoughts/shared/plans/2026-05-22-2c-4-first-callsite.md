# Phase 2c-4 — First call-site migration (Fowler load-bearing PR)

**Date**: 2026-05-22
**Branch**: `feat/daemon-02c-4-first-callsite-migration`
**Parent**: `2026-05-22-daemon-as-process-arc.md`
**Predecessor**: Phase 2c-3 (daemon supervisor, PR #508)

## Why this PR is the load-bearing one

Through 2c-2d the protocol surface was real but no MCP handler used it.
Through 2c-3 the daemon ran as a real process but MCP didn't talk to it.
**This PR is the first where MCP actually calls the daemon over IPC for a
real operation.** Everything that follows (2c-5 through 2c-8) is replication
of the pattern this PR establishes.

Per the [Fowler advisory of 2026-05-22](2026-05-22-daemon-as-process-arc.md#test-strategy-per-fowler-advisory-2026-05-22):

> Start with subprocess-per-test, write your first ten boundary tests, see
> what hurts, and pool only if pooling is actually the bottleneck. You'll
> learn ten times more from the friction of the first ten honest tests
> than from any amount of upfront design.

If the pattern this PR commits to feels wrong in practice, it has to be
ripped out before 2c-5 onward. So scope it small, name the decisions
clearly, and reserve the right to re-design.

## Scope of THIS PR

**One handler migrated**: `handle_history` (per the original arc plan and
@jinhongkuan's call). More representative of the real complexity — enriched
SurrealQL traversal, PII archive resolution, fallback paths. If this
migrates cleanly, the rest will be mechanical.

**Not in this PR**: write migrations (2c-6). Grounding/dashboard (2c-7).

## Architectural choices (all documented for review)

### 1. The `_impl` split

Today `handlers/history.py::handle_history` does everything.
After migration:

```python
# Public API — what MCP / server.py call_tool() invokes
@read_tool("read.history")
async def handle_history(ctx, days: int = 7) -> dict:
    return await ctx.daemon.history(days=days)

# Core logic — what the daemon's read.history handler invokes
async def _handle_history_impl(ctx, days: int = 7) -> dict:
    # ... today's body, unchanged ...
```

**Why a same-file split, not a separate module:**
- Smallest delta — easier to review.
- Phase 3 (repo split) will move the impl into `bicameral-daemon` and
  leave the facade in `bicameral-mcp`. Pre-staging that move now would
  add code churn for a transition we can do mechanically later.

**Loop avoidance**: `protocol/handlers/reads.py::handle_read_history`
must call `_handle_history_impl` (NOT `handle_history`), or
MCP-side `handle_history` would recurse: handler → daemon →
handler → daemon → … Test asserts the impl is what the protocol calls.

### 2. `_DaemonProxy` on `BicameralContext`

```python
class _DaemonProxy:
    """Lazy-connect ProtocolClient bound to a single BicameralContext.

    The connection is opened on first RPC call (NOT in ``from_env()``),
    so existing sync construction paths don't need to become async.
    Subsequent calls reuse the same connection.
    """
    def __init__(
        self,
        descriptor_path: Path | None = None,
        tenant_id: str = LOCAL_TENANT_ID,
    ) -> None:
        self._descriptor_path = descriptor_path or default_descriptor_path()
        self._tenant_id = tenant_id
        self._client: ProtocolClient | None = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> ProtocolClient:
        async with self._lock:
            if self._client is None:
                self._client = ProtocolClient(
                    socket_path=self._resolve_socket(),
                    tenant_id=self._tenant_id,
                )
                await self._client.connect()
            return self._client

    async def history(self, *, days: int = 7, repo_id: str = "local") -> dict:
        client = await self._ensure_connected()
        result = await client._call(
            "read.history",
            {"repo_id": repo_id, "days": days},
        )
        return result

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.close()
                self._client = None
```

`BicameralContext.from_env()` constructs the proxy (cheap, no I/O). First
RPC call opens the connection. `close()` is best-effort cleanup; the
MCP process exit handles the socket release.

### 3. Daemon-not-running error semantics — mode-aware

The proxy is **mode-aware** from day one. It checks for config files in
this order:

1. `~/.bicameral/auth.json` — **hosted-mode** marker (Phase 5). If present,
   the proxy raises `NotImplementedError("hosted mode found in
   ~/.bicameral/auth.json but not yet wired — see Phase 5 plan")`. This
   is the seam future hosted-mode code plugs into without re-architecting.
2. `~/.bicameral/daemon.json` — **local-mode** descriptor. The proxy
   opens a UDS connection. This is the only path 2c-4 actually implements.
3. Neither — `DaemonUnreachableError` with wizard-pointing message:
   ```
   DaemonUnreachableError: can't reach the bicameral daemon.
     Run `bicameral-mcp setup` to configure (local or hosted mode),
     or `bicameral-mcp daemon start` if you've already set up local mode.
   ```

`server.py::_call_tool_impl` catches this and translates to a structured
MCP error response so the agent sees a clear actionable message rather
than a generic exception traceback.

**Not in this PR**:
- Auto-spawn the daemon if local-mode descriptor missing. The setup
  wizard (separate PR, likely 2c-3e) will install LaunchAgent / Windows
  Service / systemd-user for auto-start, which solves the per-session
  friction without conflating "MCP can't reach daemon" with "MCP
  silently bootstraps daemon". Auto-spawn was the expedient answer to
  the wrong problem.
- Hosted-mode HTTPS client. The stub `NotImplementedError` is enough to
  prove the architecture is mode-aware; Phase 5 fills in the body.

### 4. Reconnect-on-daemon-crash

If the daemon dies between calls (e.g. the user runs `bicameral-mcp
daemon stop`), the next `_ensure_connected()` finds the existing client
in a half-closed state. The boundary test must specify the contract.

**Decision for this PR**: on detected disconnect, the proxy clears
`self._client` and reconnects on the same call. ONE retry. If the
retry also fails, raise `DaemonUnreachableError`. This is the simplest
honest behavior — covers the "daemon restarted mid-session" case without
exponential backoff complexity.

Boundary test for this: spawn → call → kill the daemon process →
respawn → call again → expect success.

### 5. Test pyramid impact

Per Fowler: 80%+ of tests should NOT touch the daemon. Today's
`tests/test_history*.py` files (if any) move to calling
`_handle_history_impl` directly with a real `memory://` ledger —
that's the sociable path, no daemon.

**New tests** (boundary tier, per-test daemon fixture):
- `handle_history` through a real daemon returns the same shape
  the impl returns.
- `DaemonUnreachableError` on no-descriptor.
- Reconnect-on-daemon-restart succeeds.
- Latency observation (informational, not assertional).

## Per-test daemon fixture

Reusable across this PR and every future boundary test:

```python
# tests/conftest.py addition (or tests/_daemon_fixture.py)

@pytest.fixture
async def daemon_subprocess(short_state_dir):
    """Per-test daemon: spawn a real subprocess, tear down on teardown.

    Each test owns its socket + descriptor + daemon. No shared state
    between tests. Per Fowler's per-test-logical-daemon guidance.
    """
    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"
    descriptor = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        yield descriptor
    finally:
        stop(descriptor_path=descriptor_path)
```

**No pooling in this PR.** ~8s per test is the cost (measured in 2c-3).
For ~4 boundary tests in 2c-4 that's ~32s — annoying but not blocking.
The fixture body is the only thing that changes when we eventually pool.

## Files touched

| File | Change |
|---|---|
| `handlers/history.py` | Split into facade + `_handle_history_impl` |
| `protocol/handlers/reads.py` | Update to call `_handle_history_impl` |
| `context.py` | Add `daemon: _DaemonProxy` attribute on `BicameralContext`; construct in `from_env()` |
| `daemon/proxy.py` | NEW — the `_DaemonProxy` class + `DaemonUnreachableError` |
| `tests/conftest.py` | Add `daemon_subprocess` fixture (or new `tests/_daemon_fixture.py` if conftest gets messy) |
| `tests/test_protocol_history_boundary.py` | NEW — boundary tests through real daemon |
| `tests/test_history.py` (if it exists) | Migrate to `_handle_history_impl` |

## Acceptance

- [ ] `handle_history` body is `return await ctx.daemon.history(...)` (no other imports from `local_counters`, `ledger.client`, etc. in the facade).
- [ ] `_handle_history_impl` retains today's exact behavior.
- [ ] Protocol's `read.history` handler delegates to `_impl`, not the facade.
- [ ] `DaemonUnreachableError` raised with actionable message when daemon not running.
- [ ] Boundary tests pass against per-test daemon fixture.
- [ ] Existing sociable tests still pass against `_impl`.
- [ ] Full protocol+daemon suite green; no regressions in handlers tests.

## Reversibility

- The facade/impl split is mechanical to revert (rename `_impl` back to `handle_history`, delete the facade).
- `_DaemonProxy` is additive — `from_env()` constructs it but no one is forced to use it; reverting leaves it as dead code.
- The fixture is in `conftest.py`; new fixture, removable by deletion.

If the PR shows the per-test daemon cost is unbearable for 2c-5+, we
revisit pooling here before fanning out.

## Resolved (2026-05-22 with @jinhongkuan)

**Setup-wizard-first model.** The user-facing entry point is
`bicameral-mcp setup` (existing wizard, to be extended in a separate
PR). It asks the user which mode they want and walks them through:

- **Local mode**: run `daemon start`, prompt to install LaunchAgent /
  Windows Service / systemd-user for auto-start across reboots.
- **Hosted mode** (Phase 5): browser-based OAuth, write `auth.json`
  with endpoint + token.

Explicit commands (`bicameral-mcp daemon start`, `bicameral-mcp auth
login`) stay available as escape hatches for advanced users.

This PR (2c-4) doesn't extend the wizard — it just makes sure the
architecture is mode-aware so the wizard has somewhere to plug in. The
proxy reads `auth.json` first (hosted, stubbed), then `daemon.json`
(local, implemented). Error message points at the wizard as the
recommended next step.

The wizard extension itself is its own PR (likely 2c-3e), to be sized
once 2c-4 lands and proves the architectural seams hold.
