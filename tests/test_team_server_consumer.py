"""Phase 1.5 — periodic team-server event consumer."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _team_server_event(seq: int, source_ref: str, decisions=None) -> dict:
    return {
        "sequence": seq,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "slack",
            "source_ref": source_ref,
            "content_hash": "h",
            "extraction": {
                "decisions": decisions
                if decisions is not None
                else [
                    {"summary": "use REST", "context_snippet": "we decided to use REST"},
                ],
            },
        },
    }


class _RecordingAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    async def ingest_payload(self, payload, ctx=None):
        self.calls.append(payload)
        return {}


@pytest.mark.asyncio
async def test_consumer_pulls_events_and_invokes_ingest_payload(monkeypatch, tmp_path):
    from events import team_server_consumer

    async def fake_pull(team_server_url, watermark_path, *, timeout=10.0):
        return [_team_server_event(1, "C1/1.0")]

    monkeypatch.setattr(team_server_consumer, "pull_team_server_events", fake_pull)
    adapter = _RecordingAdapter()
    n = await team_server_consumer.consume_team_server_events_once(
        team_server_url="http://team:8765",
        watermark_path=tmp_path / "wm",
        inner_adapter=adapter,
    )
    assert n == 1
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["source"] == "slack"
    assert adapter.calls[0]["decisions"][0]["description"] == "use REST"


@pytest.mark.asyncio
async def test_consumer_skips_events_with_empty_decisions(monkeypatch, tmp_path):
    from events import team_server_consumer

    async def fake_pull(team_server_url, watermark_path, *, timeout=10.0):
        return [_team_server_event(1, "C1/1.0", decisions=[])]

    monkeypatch.setattr(team_server_consumer, "pull_team_server_events", fake_pull)
    adapter = _RecordingAdapter()
    n = await team_server_consumer.consume_team_server_events_once(
        team_server_url="http://team:8765",
        watermark_path=tmp_path / "wm",
        inner_adapter=adapter,
    )
    assert n == 0
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_consumer_handles_pull_failure_gracefully(monkeypatch, tmp_path):
    from events import team_server_consumer

    async def fake_pull(team_server_url, watermark_path, *, timeout=10.0):
        return []  # pull failure semantics

    monkeypatch.setattr(team_server_consumer, "pull_team_server_events", fake_pull)
    adapter = _RecordingAdapter()
    n = await team_server_consumer.consume_team_server_events_once(
        team_server_url="http://team:8765",
        watermark_path=tmp_path / "wm",
        inner_adapter=adapter,
    )
    assert n == 0
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_consumer_advances_pull_watermark_via_returned_events(monkeypatch, tmp_path):
    """The pull_team_server_events function manages its own watermark
    file; the consumer doesn't break that. After one consume call, the
    next pull's `since` parameter equals the max sequence seen."""
    from events import team_server_consumer

    seen_since: list[int] = []

    async def fake_pull(team_server_url, watermark_path, *, timeout=10.0):
        # Mimic real pull_team_server_events behavior: advance watermark
        # based on max sequence in returned events.
        prior = 0
        if Path(watermark_path).exists():
            try:
                prior = int(Path(watermark_path).read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                prior = 0
        seen_since.append(prior)
        if prior == 0:
            events = [
                _team_server_event(1, "C/1"),
                _team_server_event(2, "C/2"),
                _team_server_event(3, "C/3"),
            ]
            Path(watermark_path).parent.mkdir(parents=True, exist_ok=True)
            Path(watermark_path).write_text("3", encoding="utf-8")
            return events
        return []

    monkeypatch.setattr(team_server_consumer, "pull_team_server_events", fake_pull)
    adapter = _RecordingAdapter()
    wm = tmp_path / "wm"
    await team_server_consumer.consume_team_server_events_once(
        "http://team:8765",
        wm,
        adapter,
    )
    await team_server_consumer.consume_team_server_events_once(
        "http://team:8765",
        wm,
        adapter,
    )
    assert seen_since == [0, 3]


@pytest.mark.asyncio
async def test_start_consumer_loop_registers_task_when_url_set(monkeypatch, tmp_path):
    from events import team_server_consumer

    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_URL", "http://team:8765")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_PULL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("BICAMERAL_DATA_PATH", str(tmp_path))
    adapter = _RecordingAdapter()
    task = team_server_consumer.start_team_server_consumer_if_configured(adapter)
    try:
        assert task is not None
        assert task.get_name() == "bicameral-team-server-consumer"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_consumer_loop_returns_none_when_url_unset(monkeypatch):
    from events import team_server_consumer

    monkeypatch.delenv("BICAMERAL_TEAM_SERVER_URL", raising=False)
    adapter = _RecordingAdapter()
    task = team_server_consumer.start_team_server_consumer_if_configured(adapter)
    assert task is None


@pytest.mark.asyncio
async def test_consumer_unwraps_team_write_adapter_does_not_echo_to_jsonl(monkeypatch, tmp_path):
    """The load-bearing test from audit-round-2 Finding A: when
    start_team_server_consumer_if_configured is passed a real
    TeamWriteAdapter, the consumer must call _inner.ingest_payload
    (NOT the wrapper) so no synthetic 'ingest.completed' echo is
    written to per-dev JSONL files."""
    from events import team_server_consumer

    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_URL", "http://team:8765")
    # Use 0-second interval so the loop fires immediately on schedule
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_PULL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BICAMERAL_DATA_PATH", str(tmp_path))

    inner = _RecordingAdapter()

    class _RecordingWriter:
        def __init__(self):
            self.calls: list[tuple] = []

        def write(self, event_type: str, payload: dict) -> None:
            self.calls.append((event_type, payload))

    class _StubMaterializer:
        async def replay_new_events(self, _inner_adapter):
            return 0

    writer = _RecordingWriter()

    # Stub the pull to return one team-server event so consume has work
    async def fake_pull(team_server_url, watermark_path, *, timeout=10.0):
        return [_team_server_event(1, "C/T")]

    monkeypatch.setattr(team_server_consumer, "pull_team_server_events", fake_pull)

    # Construct a real TeamWriteAdapter with the recording writer
    from events.team_adapter import TeamWriteAdapter

    team_adapter = TeamWriteAdapter(
        inner=inner,
        writer=writer,
        materializer=_StubMaterializer(),
    )

    task = team_server_consumer.start_team_server_consumer_if_configured(team_adapter)
    try:
        # Yield to let _loop fire once
        for _ in range(20):
            await asyncio.sleep(0.05)
            if inner.calls:
                break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # (a) Inner adapter received the ingest call
    assert len(inner.calls) >= 1
    assert inner.calls[0]["source"] == "slack"
    # (b) Writer was NEVER invoked — the unwrap bypassed the wrapper's side effect
    assert writer.calls == []
