"""Phase 1 tests: TeamWriteAdapter ↔ BackendAdapter wiring (#277)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from events.materializer import EventMaterializer
from events.team_adapter import TeamWriteAdapter
from events.writer import EventFileWriter
from ledger.adapter import SurrealDBLedgerAdapter


class FakeBackend:
    """In-memory backend stub. Records calls so tests can assert on them."""

    def __init__(self, prepopulate: dict[str, bytes] | None = None) -> None:
        self._files = dict(prepopulate or {})
        self.push_calls: list[tuple[Path, str]] = []
        self.pull_calls = 0

    async def push_events(self, local_path: Path, remote_name: str) -> None:
        self.push_calls.append((local_path, remote_name))
        self._files[remote_name] = local_path.read_bytes()

    async def pull_events(self, local_dir: Path, since_token):
        self.pull_calls += 1
        local_dir.mkdir(parents=True, exist_ok=True)
        for name, body in self._files.items():
            (local_dir / name).write_bytes(body)
        return ""

    @asynccontextmanager
    async def lock(self, remote_name: str):
        yield

    async def list_peers(self):
        for name in self._files:
            if name.endswith(".jsonl"):
                yield name[: -len(".jsonl")]


def _payload(intent: str, source_ref: str) -> dict:
    return {
        "query": intent,
        "repo": "test-repo",
        "commit_hash": "deadbeef00000000000000000000000000000000",
        "analyzed_at": "2026-04-29T12:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": f"span-{source_ref}",
                    "source_type": "transcript",
                    "text": intent,
                    "speaker": "Tester",
                    "source_ref": source_ref,
                },
                "intent": intent,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }


def _build(
    events_dir: Path, local_dir: Path, author: str, backend
) -> tuple[TeamWriteAdapter, SurrealDBLedgerAdapter]:
    inner = SurrealDBLedgerAdapter(url="memory://")
    writer = EventFileWriter(events_dir, author)
    materializer = EventMaterializer(events_dir, watermark_override=local_dir / "watermark")
    return TeamWriteAdapter(inner, writer, materializer, backend=backend), inner


@pytest.mark.asyncio
async def test_connect_pulls_then_replays(tmp_path: Path) -> None:
    """Backend pull populates events_dir; then materializer applies them."""
    # Pre-stage a peer event file in the BACKEND (not in events_dir).
    # connect() should pull it down then replay → inner adapter sees the ingest.
    peer_payload = _payload("peer-intent", "peer-1")
    import json

    peer_event = (
        json.dumps(
            {
                "schema_version": 2,
                "event_type": "ingest.completed",
                "author": "peer@x.com",
                "timestamp": "2026-05-08T00:00:00Z",
                "payload": peer_payload,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    backend = FakeBackend(prepopulate={"peer@x.com.jsonl": peer_event.encode()})

    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"
    adapter, inner = _build(events_dir, local_dir, "alice@x.com", backend)

    await adapter.connect()

    assert backend.pull_calls == 1
    decisions = await inner.get_all_decisions()
    intents = [d.get("description", "") for d in decisions]
    assert any("peer-intent" in i for i in intents), intents


@pytest.mark.asyncio
async def test_write_marks_dirty_then_flush_pushes(tmp_path: Path) -> None:
    backend = FakeBackend()
    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"
    adapter, _ = _build(events_dir, local_dir, "alice@x.com", backend)

    await adapter.connect()
    await adapter.ingest_payload(_payload("alice-intent", "src-1"))

    # First flush: pushes alice's file.
    await adapter.flush_to_backend()
    assert len(backend.push_calls) == 1
    pushed_path, pushed_name = backend.push_calls[0]
    assert pushed_name == "alice@x.com.jsonl"
    assert pushed_path.name == "alice@x.com.jsonl"

    # Second flush with no intervening writes: no-op.
    await adapter.flush_to_backend()
    assert len(backend.push_calls) == 1


@pytest.mark.asyncio
async def test_no_backend_means_no_push_no_pull(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"
    adapter, _ = _build(events_dir, local_dir, "alice@x.com", backend=None)

    await adapter.connect()
    await adapter.ingest_payload(_payload("solo-intent", "src-1"))
    await adapter.flush_to_backend()  # must not raise
