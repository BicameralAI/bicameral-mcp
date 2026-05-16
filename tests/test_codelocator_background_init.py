"""Background-init lifecycle on ``RealCodeLocatorAdapter`` (#380).

Pre-#380: ``server.py:serve_stdio`` did ``await get_code_locator().initialize()``
inline before opening the MCP stdio transport. On a 150MB+ symbol-index DB
the cold path took ~45s, blowing past Claude Code's 30s ``initialize``
JSON-RPC timeout. The fix moves init off the handshake path — kicked off
as a background asyncio Task, with a threading.Lock making
``_ensure_initialized`` safe to call concurrently from the background
Task AND from worker threads spawned by ``asyncio.to_thread(
ctx.code_graph.<method>, ...)``.

These tests pin the contract:

1. ``initialize_in_background`` returns immediately (doesn't block on the
   slow init body).
2. A concurrent sync ``_ensure_initialized`` call (e.g., from a worker
   thread) blocks on the lock until the background init finishes —
   honoring the "first tool call eats the latency" trade.
3. ``wait_until_ready`` re-raises a background-init failure to its
   async caller (fail-loud contract from #243 phase-2 signoff Q3,
   relocated from boot to first call).
4. After a failed background init, ``_ensure_initialized`` is free to
   retry (the lock is released on exception, the task slot is reused).
5. Concurrent ``initialize_in_background`` calls produce exactly one
   Task (idempotent against re-entry from server.py startup paths).

Solitary by design — patching ``_ensure_initialized`` is the right
seam because the alternative (a real symbol index that takes a
controllable amount of time) is fragile and slow. The lock + Task
glue is what's under test; the init body is replaced with a
deterministic sleep/flag/raise.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from adapters.code_locator import RealCodeLocatorAdapter


def _fresh_adapter() -> RealCodeLocatorAdapter:
    """Avoid the module-level singleton cache so each test gets a clean state."""
    return RealCodeLocatorAdapter(repo_path=".")


@pytest.mark.asyncio
async def test_initialize_in_background_returns_immediately() -> None:
    """Scheduling init must not block the event loop on the slow body."""
    adapter = _fresh_adapter()
    ready = threading.Event()
    release = threading.Event()

    def slow_init(self: RealCodeLocatorAdapter) -> None:
        # Signal we entered, then wait until the test releases us. Mirrors a
        # real cold-init that takes seconds.
        ready.set()
        release.wait(timeout=5)
        self._initialized = True

    # Monkey-patch via direct method substitution on the instance.
    adapter._run_init_body = slow_init.__get__(adapter, RealCodeLocatorAdapter)

    t0 = time.monotonic()
    adapter.initialize_in_background()
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2, f"initialize_in_background must return immediately; took {elapsed:.3f}s"
    assert adapter._init_task is not None, "background Task must be stored on the adapter"
    assert not adapter._init_task.done(), "background Task must still be running"

    # Wait for the executor thread to actually enter the slow body before
    # releasing. Use ``asyncio.to_thread`` so the event loop stays free to
    # actually schedule the Task we just created.
    entered = await asyncio.to_thread(ready.wait, 2)
    assert entered, "background Task didn't reach the init body"
    release.set()
    await adapter._init_task
    assert adapter._initialized is True


@pytest.mark.asyncio
async def test_sync_caller_blocks_on_background_init_via_lock() -> None:
    """First tool-call thread blocks on the lock until background init lands."""
    adapter = _fresh_adapter()
    init_entered = threading.Event()
    release = threading.Event()
    call_log: list[str] = []

    def slow_init(self: RealCodeLocatorAdapter) -> None:
        call_log.append("init-start")
        init_entered.set()
        release.wait(timeout=5)
        call_log.append("init-end")
        self._initialized = True

    adapter._run_init_body = slow_init.__get__(adapter, RealCodeLocatorAdapter)

    # Kick off background init; wait for it to actually enter the body so
    # the next call genuinely contends for the lock. ``asyncio.to_thread``
    # keeps the event loop free to schedule the Task we just created.
    adapter.initialize_in_background()
    entered = await asyncio.to_thread(init_entered.wait, 2)
    assert entered

    # Simulate a tool-handler worker thread reaching the adapter via
    # ``asyncio.to_thread(adapter._ensure_initialized)``. Without the lock
    # this would race the background init body.
    second_call_finished = threading.Event()

    def second_caller() -> None:
        adapter._ensure_initialized()
        call_log.append("second-call-return")
        second_call_finished.set()

    t = threading.Thread(target=second_caller, daemon=True)
    t.start()
    # Give the second caller a beat to try to acquire the lock.
    await asyncio.sleep(0.1)
    assert not second_call_finished.is_set(), (
        "second caller returned before background init finished — lock not held"
    )

    release.set()
    await adapter._init_task
    finished = await asyncio.to_thread(second_call_finished.wait, 2)
    assert finished

    # The slow init ran exactly once, and the second caller observed the
    # post-init state without re-running the body.
    assert call_log == ["init-start", "init-end", "second-call-return"]


@pytest.mark.asyncio
async def test_wait_until_ready_reraises_background_init_failure() -> None:
    """Fail-loud contract from #243 phase-2 (relocated to first-call time)."""
    adapter = _fresh_adapter()

    def boom(self: RealCodeLocatorAdapter) -> None:
        raise RuntimeError(
            "Code locator index is empty. Run: python -m code_locator index <repo_path>"
        )

    adapter._run_init_body = boom.__get__(adapter, RealCodeLocatorAdapter)

    adapter.initialize_in_background()
    with pytest.raises(RuntimeError, match="Code locator index is empty"):
        await adapter.wait_until_ready()


@pytest.mark.asyncio
async def test_failed_background_init_allows_retry() -> None:
    """After a failed init, the next call may try again — the slot isn't poisoned."""
    adapter = _fresh_adapter()
    attempts = {"n": 0}

    def flaky_init(self: RealCodeLocatorAdapter) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        self._initialized = True

    adapter._run_init_body = flaky_init.__get__(adapter, RealCodeLocatorAdapter)

    adapter.initialize_in_background()
    with pytest.raises(RuntimeError, match="transient"):
        await adapter.wait_until_ready()
    assert adapter._initialized is False

    # Second kickoff schedules a fresh Task; the previous one is done.
    adapter.initialize_in_background()
    await adapter.wait_until_ready()
    assert adapter._initialized is True
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_initialize_in_background_is_idempotent_while_running() -> None:
    """Repeated kickoffs while a Task is in flight reuse the existing Task."""
    adapter = _fresh_adapter()
    release = threading.Event()

    def slow_init(self: RealCodeLocatorAdapter) -> None:
        release.wait(timeout=5)
        self._initialized = True

    adapter._run_init_body = slow_init.__get__(adapter, RealCodeLocatorAdapter)

    adapter.initialize_in_background()
    first_task = adapter._init_task
    adapter.initialize_in_background()
    adapter.initialize_in_background()
    assert adapter._init_task is first_task, (
        "subsequent initialize_in_background calls must not replace the in-flight Task"
    )

    release.set()
    await first_task
    assert adapter._initialized is True

    # Post-success kickoff is a no-op — Task slot stays as-is.
    adapter.initialize_in_background()
    assert adapter._init_task is first_task
