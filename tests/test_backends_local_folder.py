"""Phase 1 unit tests for events.backends.local_folder.LocalFolderAdapter (#277)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from events.backends.local_folder import LocalFolderAdapter


def _populate(p: Path, body: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)


@pytest.mark.asyncio
async def test_push_uploads_when_remote_missing(tmp_path: Path) -> None:
    local = tmp_path / "local" / "alice@x.com.jsonl"
    remote = tmp_path / "remote"
    remote.mkdir()
    _populate(local, b'{"event_type":"ingest.completed"}\n')

    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    await adapter.push_events(local, "alice@x.com.jsonl")

    assert (remote / "alice@x.com.jsonl").read_bytes() == local.read_bytes()


@pytest.mark.asyncio
async def test_push_skips_when_remote_hash_matches(tmp_path: Path) -> None:
    local = tmp_path / "alice@x.com.jsonl"
    remote = tmp_path / "remote"
    remote.mkdir()
    _populate(local, b'{"event_type":"ingest.completed"}\n')

    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    await adapter.push_events(local, "alice@x.com.jsonl")
    first_mtime = (remote / "alice@x.com.jsonl").stat().st_mtime_ns

    # Bump local mtime but keep contents identical → push must be a no-op.
    import os
    os.utime(local, ns=(first_mtime + 1_000_000_000, first_mtime + 1_000_000_000))
    await adapter.push_events(local, "alice@x.com.jsonl")

    assert (remote / "alice@x.com.jsonl").stat().st_mtime_ns == first_mtime


@pytest.mark.asyncio
async def test_pull_copies_peer_files_only(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    _populate(remote / "alice@x.com.jsonl", b'{"e":"alice-remote"}\n')
    _populate(remote / "bob@x.com.jsonl", b'{"e":"bob-remote"}\n')
    _populate(local_dir / "alice@x.com.jsonl", b'{"e":"alice-local"}\n')

    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    await adapter.pull_events(local_dir, since_token=None)

    assert (local_dir / "alice@x.com.jsonl").read_bytes() == b'{"e":"alice-local"}\n'
    assert (local_dir / "bob@x.com.jsonl").read_bytes() == b'{"e":"bob-remote"}\n'


@pytest.mark.asyncio
async def test_pull_skips_unchanged(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    _populate(remote / "bob@x.com.jsonl", b'{"e":"bob-1"}\n')

    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    await adapter.pull_events(local_dir, since_token=None)
    first_mtime = (local_dir / "bob@x.com.jsonl").stat().st_mtime_ns

    await adapter.pull_events(local_dir, since_token=None)

    assert (local_dir / "bob@x.com.jsonl").stat().st_mtime_ns == first_mtime


@pytest.mark.asyncio
async def test_list_peers_yields_email_stems(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _populate(remote / "alice@x.com.jsonl", b"")
    _populate(remote / "bob@x.com.jsonl", b"")
    _populate(remote / "carol@x.com.jsonl", b"")
    _populate(remote / "noise.txt", b"ignore")

    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    peers = sorted([name async for name in adapter.list_peers()])

    assert peers == ["alice@x.com", "bob@x.com", "carol@x.com"]


@pytest.mark.asyncio
async def test_lock_serializes_concurrent_acquirers(tmp_path: Path) -> None:
    """Second acquirer waits until the first releases."""
    remote = tmp_path / "remote"
    remote.mkdir()
    adapter = LocalFolderAdapter(remote_root=remote, author="alice@x.com")
    order: list[str] = []

    async def hold(name: str, hold_ms: int) -> None:
        async with adapter.lock("alice@x.com.jsonl"):
            order.append(f"{name}-acquired")
            await asyncio.sleep(hold_ms / 1000)
            order.append(f"{name}-released")

    await asyncio.gather(hold("first", 100), hold("second", 0))
    # First's release MUST appear before second's acquire (strict serialization).
    assert order.index("first-released") < order.index("second-acquired")
