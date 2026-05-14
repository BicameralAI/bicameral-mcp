"""Unit tests for the Granola source adapter (#279 Phase 1).

Mocks the HTTP layer via ``GranolaClient`` substitution; no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from events.sources.granola import (
    GranolaAdapter,
    GranolaClient,
    MissingApiKeyError,
    _transform,
)


class _FakeClient:
    """Records calls and returns canned transcript items."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items if items is not None else []
        self.calls: list[dict | None] = []

    def list_transcripts(self, *, since: str | None = None) -> list[dict]:
        self.calls.append({"since": since})
        return list(self._items)


# ── api-key handling ──────────────────────────────────────────────────────


def test_granola_adapter_reads_api_key_from_env_not_config(
    monkeypatch, tmp_path: Path
) -> None:
    """The api_key env var must be set; the config holds only the env name.
    Without the env, the adapter raises MissingApiKeyError before any HTTP call."""
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    adapter = GranolaAdapter()  # no injected client → forces default-client path
    with pytest.raises(MissingApiKeyError, match="GRANOLA_API_KEY"):
        adapter.pull(
            watermark_dir=tmp_path,
            config={"type": "granola", "api_key_env": "GRANOLA_API_KEY"},
        )


def test_granola_adapter_default_env_name_when_config_omits_api_key_env(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    adapter = GranolaAdapter()
    with pytest.raises(MissingApiKeyError, match="GRANOLA_API_KEY"):
        adapter.pull(watermark_dir=tmp_path, config={"type": "granola"})


# ── watermark behavior ────────────────────────────────────────────────────


def test_granola_watermark_path_is_inside_watermark_dir(tmp_path: Path) -> None:
    """The watermark file must live inside the supplied watermark_dir;
    no path-escape via config injection."""
    client = _FakeClient(
        items=[
            {
                "id": "t1",
                "ended_at": "2026-05-14T10:00:00Z",
                "transcript_text": "hello",
            }
        ]
    )
    adapter = GranolaAdapter(client=client)
    adapter.pull(watermark_dir=tmp_path, config={})
    adapter.confirm_watermark()
    expected = tmp_path / "granola.json"
    assert expected.exists()
    # Path is strictly inside tmp_path
    assert expected.resolve().is_relative_to(tmp_path.resolve())


def test_granola_pull_no_new_items_does_not_update_watermark(
    tmp_path: Path,
) -> None:
    """Empty pull → watermark file is NOT created, even after confirm."""
    client = _FakeClient(items=[])
    adapter = GranolaAdapter(client=client)
    payloads = adapter.pull(watermark_dir=tmp_path, config={})
    adapter.confirm_watermark()
    assert payloads == []
    assert not (tmp_path / "granola.json").exists()


def test_granola_pull_passes_existing_watermark_as_since(tmp_path: Path) -> None:
    """A prior watermark is forwarded as ``since`` on the next pull."""
    (tmp_path / "granola.json").write_text(
        json.dumps({"last_synced_at": "2026-05-10T00:00:00Z"})
    )
    client = _FakeClient(items=[])
    adapter = GranolaAdapter(client=client)
    adapter.pull(watermark_dir=tmp_path, config={})
    assert client.calls == [{"since": "2026-05-10T00:00:00Z"}]


def test_granola_pull_advances_watermark_to_max_ended_at(tmp_path: Path) -> None:
    """When several items arrive with different ended_at, the persisted
    watermark equals the maximum (latest)."""
    client = _FakeClient(
        items=[
            {"id": "a", "ended_at": "2026-05-10T08:00:00Z", "transcript_text": "x"},
            {"id": "b", "ended_at": "2026-05-12T09:00:00Z", "transcript_text": "y"},
            {"id": "c", "ended_at": "2026-05-11T07:30:00Z", "transcript_text": "z"},
        ]
    )
    adapter = GranolaAdapter(client=client)
    adapter.pull(watermark_dir=tmp_path, config={})
    # Pre-confirm: file does NOT exist (two-phase commit)
    assert not (tmp_path / "granola.json").exists()
    adapter.confirm_watermark()
    saved = json.loads((tmp_path / "granola.json").read_text())
    assert saved["last_synced_at"] == "2026-05-12T09:00:00Z"


def test_granola_confirm_is_idempotent(tmp_path: Path) -> None:
    """Calling confirm_watermark twice after a single pull writes only once
    semantically (no stale advance)."""
    client = _FakeClient(
        items=[{"id": "x", "ended_at": "2026-05-14T01:00:00Z", "transcript_text": "t"}]
    )
    adapter = GranolaAdapter(client=client)
    adapter.pull(watermark_dir=tmp_path, config={})
    adapter.confirm_watermark()
    first = (tmp_path / "granola.json").read_text()
    adapter.confirm_watermark()  # no-op
    assert (tmp_path / "granola.json").read_text() == first


def test_granola_pull_then_skip_confirm_leaves_watermark_unchanged(
    tmp_path: Path,
) -> None:
    """The two-phase-commit guarantee: pull without confirm does NOT
    advance the watermark, so a subsequent pull re-receives the same items."""
    (tmp_path / "granola.json").write_text(
        json.dumps({"last_synced_at": "2026-05-01T00:00:00Z"})
    )
    client = _FakeClient(
        items=[{"id": "x", "ended_at": "2026-05-14T01:00:00Z", "transcript_text": "t"}]
    )
    adapter = GranolaAdapter(client=client)
    adapter.pull(watermark_dir=tmp_path, config={})
    # Operator decides NOT to confirm (ingest failed). Watermark unchanged.
    saved = json.loads((tmp_path / "granola.json").read_text())
    assert saved["last_synced_at"] == "2026-05-01T00:00:00Z"


# ── payload transform ─────────────────────────────────────────────────────


def test_transform_maps_granola_fields_into_ingest_payload_shape() -> None:
    item = {
        "id": "txn-abc",
        "ended_at": "2026-05-14T10:00:00Z",
        "title": "Sprint planning",
        "transcript_text": "Jin: let's ship Phase 1.\nKim: agreed.",
        "participants": [{"name": "Jin"}, {"name": "Kim"}],
    }
    payload = _transform(item)
    assert payload["query"] == "Sprint planning"
    assert len(payload["mappings"]) == 1
    span = payload["mappings"][0]["span"]
    assert span["source_type"] == "transcript"
    assert span["source_ref"] == "txn-abc"
    assert span["text"] == "Jin: let's ship Phase 1.\nKim: agreed."
    assert span["speaker"] == "Jin"
    assert span["meeting_date"] == "2026-05-14"
    assert span["span_id"] == "granola-txn-abc"


def test_transform_tolerates_missing_optional_fields() -> None:
    """Granola may return items lacking title or participants; transform
    must produce a valid payload (no exceptions, sensible defaults)."""
    item = {"id": "txn-min", "transcript_text": "some text"}
    payload = _transform(item)
    assert payload["query"]  # non-empty fallback
    assert payload["mappings"][0]["span"]["speaker"] == ""
    assert payload["mappings"][0]["span"]["text"] == "some text"


def test_transform_extracts_speaker_from_plain_string_participant() -> None:
    """Tolerate participants encoded as bare strings instead of dicts."""
    item = {"id": "x", "transcript_text": "t", "participants": ["Jin"]}
    payload = _transform(item)
    assert payload["mappings"][0]["span"]["speaker"] == "Jin"


# ── full happy path ───────────────────────────────────────────────────────


def test_granola_pull_returns_one_payload_per_item(tmp_path: Path) -> None:
    client = _FakeClient(
        items=[
            {"id": "a", "ended_at": "2026-05-12T01:00:00Z", "transcript_text": "AAA"},
            {"id": "b", "ended_at": "2026-05-12T02:00:00Z", "transcript_text": "BBB"},
        ]
    )
    adapter = GranolaAdapter(client=client)
    payloads = adapter.pull(watermark_dir=tmp_path, config={})
    assert len(payloads) == 2
    assert payloads[0]["mappings"][0]["span"]["text"] == "AAA"
    assert payloads[1]["mappings"][0]["span"]["text"] == "BBB"
