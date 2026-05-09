"""Phase 1 integration: two-author round-trip via LocalFolderAdapter (#277)."""

from __future__ import annotations

from pathlib import Path

import pytest

from events.backends.local_folder import LocalFolderAdapter
from events.materializer import EventMaterializer
from events.team_adapter import TeamWriteAdapter
from events.writer import EventFileWriter
from ledger.adapter import SurrealDBLedgerAdapter


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


def _build(events_dir: Path, local_dir: Path, author: str, remote_root: Path) -> TeamWriteAdapter:
    inner = SurrealDBLedgerAdapter(url="memory://")
    writer = EventFileWriter(events_dir, author)
    materializer = EventMaterializer(events_dir, local_dir)
    backend = LocalFolderAdapter(remote_root=remote_root, author=author)
    return TeamWriteAdapter(inner, writer, materializer, backend=backend)


@pytest.mark.asyncio
async def test_two_authors_round_trip(tmp_path: Path) -> None:
    """A ingests → flushes → B connects → B's inner DB has A's decision."""
    remote_root = tmp_path / "remote"
    remote_root.mkdir()

    a = _build(
        events_dir=tmp_path / "a-events",
        local_dir=tmp_path / "a-local",
        author="alice@x.com",
        remote_root=remote_root,
    )
    b = _build(
        events_dir=tmp_path / "b-events",
        local_dir=tmp_path / "b-local",
        author="bob@x.com",
        remote_root=remote_root,
    )

    await a.connect()
    await a.ingest_payload(_payload("alice-shared-intent", "src-A"))
    await a.flush_to_backend()

    await b.connect()
    decisions_b = await b._inner.get_all_decisions()
    descriptions = [d.get("description", "") for d in decisions_b]
    assert any("alice-shared-intent" in d for d in descriptions), descriptions


@pytest.mark.asyncio
async def test_pull_idempotent_across_invocations(tmp_path: Path) -> None:
    """Re-pulling without remote change is a no-op (file mtime stable)."""
    remote_root = tmp_path / "remote"
    remote_root.mkdir()

    a = _build(
        events_dir=tmp_path / "a-events",
        local_dir=tmp_path / "a-local",
        author="alice@x.com",
        remote_root=remote_root,
    )
    b = _build(
        events_dir=tmp_path / "b-events",
        local_dir=tmp_path / "b-local",
        author="bob@x.com",
        remote_root=remote_root,
    )

    await a.connect()
    await a.ingest_payload(_payload("alice-once", "src-A"))
    await a.flush_to_backend()

    await b.connect()
    pulled_path = (tmp_path / "b-events") / "alice@x.com.jsonl"
    first_mtime = pulled_path.stat().st_mtime_ns

    # Second pull on B with no remote change → mtime unchanged.
    await b._backend.pull_events(b._writer.events_dir, since_token=None)
    assert pulled_path.stat().st_mtime_ns == first_mtime
