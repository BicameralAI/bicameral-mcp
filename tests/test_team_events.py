"""Tests for Phase 1: event-sourced collaboration.

Covers: EventFileWriter, EventMaterializer, TeamWriteAdapter, config detection.
All tests use SURREAL_URL=memory:// and temp directories.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# Ensure in-memory SurrealDB for all tests in this module
os.environ.setdefault("SURREAL_URL", "memory://")


# ── EventFileWriter ──────────────────────────────────────────────────────


class TestEventFileWriter:
    def test_write_creates_file(self, tmp_path):
        from events.writer import EventFileWriter

        events_dir = tmp_path / "events"
        writer = EventFileWriter(events_dir, "jin@example.com")

        path = writer.write("ingest.completed", {"repo": "test"})

        assert path.exists()
        assert path.parent == events_dir / "jin@example.com"
        assert path.suffix == ".json"

    def test_write_correct_json_structure(self, tmp_path):
        from events.writer import EventFileWriter

        writer = EventFileWriter(tmp_path / "events", "jin@example.com")
        path = writer.write("ingest.completed", {"repo": "test", "mappings": []})

        event = json.loads(path.read_text())
        assert event["schema_version"] == 1
        assert event["event_type"] == "ingest.completed"
        assert event["author"] == "jin@example.com"
        assert event["payload"]["repo"] == "test"
        assert "event_id" in event
        assert "timestamp" in event

    def test_write_filename_format(self, tmp_path):
        from events.writer import EventFileWriter

        writer = EventFileWriter(tmp_path / "events", "test@co.com")
        path = writer.write("link_commit.completed", {})

        # Filename: 20260410T180000Z-a1b2c3d4.json
        name = path.stem
        parts = name.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[0].endswith("Z")  # ISO timestamp
        assert len(parts[1]) == 8       # short UUID

    def test_write_no_tmp_files_left(self, tmp_path):
        from events.writer import EventFileWriter

        writer = EventFileWriter(tmp_path / "events", "test@co.com")
        writer.write("ingest.completed", {})

        tmp_files = list((tmp_path / "events" / "test@co.com").glob("*.tmp"))
        assert tmp_files == []

    def test_write_multiple_events_unique_filenames(self, tmp_path):
        from events.writer import EventFileWriter

        writer = EventFileWriter(tmp_path / "events", "test@co.com")
        paths = [writer.write("ingest.completed", {"i": i}) for i in range(5)]

        assert len(set(paths)) == 5

    def test_creates_user_directory(self, tmp_path):
        from events.writer import EventFileWriter

        events_dir = tmp_path / "events"
        assert not events_dir.exists()

        EventFileWriter(events_dir, "new@user.com")
        assert (events_dir / "new@user.com").is_dir()


# ── EventMaterializer ───────────────────────────────────────────────────


class TestEventMaterializer:
    def _write_event_file(self, events_dir: Path, author: str, ts: str, event_type: str, payload: dict):
        """Helper to create a manual event file."""
        user_dir = events_dir / author
        user_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts}-deadbeef.json"
        event = {
            "schema_version": 1,
            "event_id": f"{ts}-deadbeef",
            "event_type": event_type,
            "author": author,
            "timestamp": "2026-04-10T18:00:00+00:00",
            "payload": payload,
        }
        (user_dir / filename).write_text(json.dumps(event), encoding="utf-8")

    def test_no_events_returns_zero(self, tmp_path):
        import asyncio
        from events.materializer import EventMaterializer

        mat = EventMaterializer(tmp_path / "events", tmp_path / "local")

        class FakeAdapter:
            pass

        result = asyncio.run(mat.replay_new_events(FakeAdapter()))
        assert result == 0

    def test_replay_processes_new_events(self, tmp_path):
        import asyncio
        from events.materializer import EventMaterializer

        events_dir = tmp_path / "events"
        self._write_event_file(
            events_dir, "a@test.com", "20260410T180000Z",
            "ingest.completed",
            {"repo": "test", "mappings": []},
        )

        mat = EventMaterializer(events_dir, tmp_path / "local")
        calls = []

        class MockAdapter:
            async def ingest_payload(self, payload):
                calls.append(("ingest", payload))
                return {}

        result = asyncio.run(mat.replay_new_events(MockAdapter()))
        assert result == 1
        assert len(calls) == 1
        assert calls[0][0] == "ingest"

    def test_watermark_prevents_replay(self, tmp_path):
        import asyncio
        from events.materializer import EventMaterializer

        events_dir = tmp_path / "events"
        self._write_event_file(
            events_dir, "a@test.com", "20260410T180000Z",
            "ingest.completed", {"repo": "test", "mappings": []},
        )

        mat = EventMaterializer(events_dir, tmp_path / "local")
        calls = []

        class MockAdapter:
            async def ingest_payload(self, payload):
                calls.append(payload)
                return {}

        # First replay
        asyncio.run(mat.replay_new_events(MockAdapter()))
        assert len(calls) == 1

        # Second replay — watermark should prevent re-processing
        calls.clear()
        asyncio.run(mat.replay_new_events(MockAdapter()))
        assert len(calls) == 0

    def test_replays_in_chronological_order(self, tmp_path):
        import asyncio
        from events.materializer import EventMaterializer

        events_dir = tmp_path / "events"
        # Write events from two users, interleaved timestamps
        self._write_event_file(
            events_dir, "b@test.com", "20260410T190000Z",
            "ingest.completed", {"order": 2},
        )
        self._write_event_file(
            events_dir, "a@test.com", "20260410T180000Z",
            "ingest.completed", {"order": 1},
        )
        self._write_event_file(
            events_dir, "a@test.com", "20260410T200000Z",
            "ingest.completed", {"order": 3},
        )

        mat = EventMaterializer(events_dir, tmp_path / "local")
        orders = []

        class MockAdapter:
            async def ingest_payload(self, payload):
                orders.append(payload["order"])
                return {}

        asyncio.run(mat.replay_new_events(MockAdapter()))
        assert orders == [1, 2, 3]

    def test_link_commit_event_replay(self, tmp_path):
        import asyncio
        from events.materializer import EventMaterializer

        events_dir = tmp_path / "events"
        self._write_event_file(
            events_dir, "a@test.com", "20260410T180000Z",
            "link_commit.completed",
            {"commit_hash": "abc123", "repo_path": "/tmp/repo"},
        )

        mat = EventMaterializer(events_dir, tmp_path / "local")
        calls = []

        class MockAdapter:
            async def ingest_commit(self, commit_hash, repo_path):
                calls.append((commit_hash, repo_path))
                return {}

        asyncio.run(mat.replay_new_events(MockAdapter()))
        assert calls == [("abc123", "/tmp/repo")]

    def test_extract_timestamp(self):
        from events.materializer import EventMaterializer

        assert EventMaterializer._extract_timestamp("20260410T180000Z-deadbeef.json") == "20260410T180000Z"
        assert EventMaterializer._extract_timestamp("20260410T183022Z-a1b2c3d4.json") == "20260410T183022Z"


# ── TeamWriteAdapter ─────────────────────────────────────────────────────


class TestTeamWriteAdapter:
    @pytest.fixture
    def team_setup(self, tmp_path):
        from events.writer import EventFileWriter
        from events.materializer import EventMaterializer
        from events.team_adapter import TeamWriteAdapter

        events_dir = tmp_path / "events"
        local_dir = tmp_path / "local"

        writer = EventFileWriter(events_dir, "test@co.com")
        materializer = EventMaterializer(events_dir, local_dir)

        calls = {}

        class MockInner:
            async def connect(self):
                calls["connect"] = True

            async def ingest_payload(self, payload):
                calls.setdefault("ingest_payload", []).append(payload)
                return {"ingested": True}

            async def ingest_commit(self, commit_hash, repo_path, drift_analyzer=None):
                calls.setdefault("ingest_commit", []).append((commit_hash, repo_path))
                return {"synced": True}

            async def get_all_decisions(self, filter="all"):
                return [{"id": "test"}]

            async def search_by_query(self, query, max_results=10, min_confidence=0.5):
                return []

            async def get_decisions_for_file(self, file_path):
                return []

            async def get_undocumented_symbols(self, file_path):
                return []

            async def get_source_cursor(self, repo, source_type, source_scope="default"):
                return None

            async def upsert_source_cursor(self, **kwargs):
                return {}

        inner = MockInner()
        adapter = TeamWriteAdapter(inner, writer, materializer)
        return adapter, writer, calls

    def test_ingest_writes_event_and_delegates(self, team_setup):
        import asyncio

        adapter, writer, calls = team_setup
        payload = {"repo": "test", "mappings": []}

        result = asyncio.run(adapter.ingest_payload(payload))

        # Event file was written
        event_files = list(writer.events_dir.glob("*/*.json"))
        assert len(event_files) == 1

        event = json.loads(event_files[0].read_text())
        assert event["event_type"] == "ingest.completed"
        assert event["payload"]["repo"] == "test"

        # Inner adapter was called
        assert calls["ingest_payload"] == [payload]
        assert result == {"ingested": True}

    def test_ingest_commit_writes_event_and_delegates(self, team_setup):
        import asyncio

        adapter, writer, calls = team_setup

        result = asyncio.run(adapter.ingest_commit("abc123", "/tmp/repo"))

        event_files = list(writer.events_dir.glob("*/*.json"))
        assert len(event_files) == 1

        event = json.loads(event_files[0].read_text())
        assert event["event_type"] == "link_commit.completed"
        assert event["payload"]["commit_hash"] == "abc123"

        assert calls["ingest_commit"] == [("abc123", "/tmp/repo")]
        assert result == {"synced": True}

    def test_read_methods_delegate(self, team_setup):
        import asyncio

        adapter, _, _ = team_setup

        decisions = asyncio.run(adapter.get_all_decisions())
        assert decisions == [{"id": "test"}]

    def test_no_event_for_source_cursor(self, team_setup):
        import asyncio

        adapter, writer, _ = team_setup

        asyncio.run(adapter.upsert_source_cursor(
            repo="test", source_type="transcript",
        ))

        event_files = list(writer.events_dir.glob("*/*.json"))
        assert len(event_files) == 0


# ── Config Detection ─────────────────────────────────────────────────────


class TestConfigDetection:
    def test_solo_when_no_config(self, tmp_path):
        from adapters.ledger import _read_collaboration_mode

        assert _read_collaboration_mode(str(tmp_path)) == "solo"

    def test_solo_when_explicit(self, tmp_path):
        from adapters.ledger import _read_collaboration_mode

        config = tmp_path / ".bicameral" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("mode: solo\n")

        assert _read_collaboration_mode(str(tmp_path)) == "solo"

    def test_team_when_configured(self, tmp_path):
        from adapters.ledger import _read_collaboration_mode

        config = tmp_path / ".bicameral" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("mode: team\n")

        assert _read_collaboration_mode(str(tmp_path)) == "team"

    def test_team_with_quotes(self, tmp_path):
        from adapters.ledger import _read_collaboration_mode

        config = tmp_path / ".bicameral" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text('mode: "team"\n')

        assert _read_collaboration_mode(str(tmp_path)) == "team"
