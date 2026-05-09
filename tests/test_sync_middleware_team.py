"""Phase 1 tests: ensure_team_synced + flush_team_writes middleware (#277)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import handlers.sync_middleware as middleware


class StubBackend:
    def __init__(self) -> None:
        self.pull_calls = 0

    async def push_events(self, local_path: Path, remote_name: str) -> None:
        pass

    async def pull_events(self, local_dir: Path, since_token):
        self.pull_calls += 1
        return ""

    @asynccontextmanager
    async def lock(self, remote_name: str):
        yield

    async def list_peers(self):
        if False:
            yield  # pragma: no cover


class StubInner:
    pass


class StubMaterializer:
    def __init__(self) -> None:
        self.replay_calls = 0

    async def replay_new_events(self, inner) -> int:
        self.replay_calls += 1
        return 0


class StubLedger:
    def __init__(self, backend, materializer) -> None:
        self._backend = backend
        self._inner = StubInner()
        self._writer = SimpleNamespace(events_dir=Path("/tmp/never-touched"), path=Path("/tmp/x.jsonl"))
        self._materializer = materializer
        self.flush_count = 0
        self._raise_on_flush = False

    async def flush_to_backend(self) -> None:
        if self._raise_on_flush:
            raise RuntimeError("simulated network error")
        self.flush_count += 1


def _ctx(ledger, repo_path: str = "/tmp/repo") -> SimpleNamespace:
    return SimpleNamespace(ledger=ledger, repo_path=repo_path)


@pytest.fixture(autouse=True)
def _clear_team_pull_cache():
    middleware._LAST_TEAM_PULL_AT.clear()
    yield
    middleware._LAST_TEAM_PULL_AT.clear()


@pytest.mark.asyncio
async def test_ensure_team_synced_ttl_cache(monkeypatch) -> None:
    backend = StubBackend()
    mat = StubMaterializer()
    ledger = StubLedger(backend, mat)
    ctx = _ctx(ledger, repo_path="/repo-A")

    fake_now = [1000.0]
    monkeypatch.setattr(middleware.time, "monotonic", lambda: fake_now[0])

    await middleware.ensure_team_synced(ctx)
    assert backend.pull_calls == 1

    fake_now[0] = 1015.0  # within 30 s TTL → no-op
    await middleware.ensure_team_synced(ctx)
    assert backend.pull_calls == 1

    fake_now[0] = 1100.0  # past TTL → pull again
    await middleware.ensure_team_synced(ctx)
    assert backend.pull_calls == 2


@pytest.mark.asyncio
async def test_ensure_team_synced_no_backend_is_noop() -> None:
    ledger = StubLedger(backend=None, materializer=StubMaterializer())
    ctx = _ctx(ledger)
    await middleware.ensure_team_synced(ctx)  # must not raise


@pytest.mark.asyncio
async def test_ensure_team_synced_no_ledger_is_noop() -> None:
    ctx = SimpleNamespace(repo_path="/x")  # no ledger attr
    await middleware.ensure_team_synced(ctx)


@pytest.mark.asyncio
async def test_flush_team_writes_swallows_backend_errors() -> None:
    ledger = StubLedger(StubBackend(), StubMaterializer())
    ledger._raise_on_flush = True
    ctx = _ctx(ledger)
    await middleware.flush_team_writes(ctx)  # must not raise


@pytest.mark.asyncio
async def test_flush_team_writes_calls_through_when_present() -> None:
    ledger = StubLedger(StubBackend(), StubMaterializer())
    ctx = _ctx(ledger)
    await middleware.flush_team_writes(ctx)
    assert ledger.flush_count == 1
