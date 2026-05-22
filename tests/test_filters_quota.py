"""Tests for #337 foundations cycle 4 — per-source quota helpers + adapter wiring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from filters import get_max_bytes, get_max_items, payload_within_cap


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


# ── helpers: get_max_items ──────────────────────────────────────────────────


def test_get_max_items_default_zero():
    assert get_max_items({}) == 0
    assert get_max_items({"max_items_per_pull": None}) == 0
    assert get_max_items({"max_items_per_pull": ""}) == 0


def test_get_max_items_int_passthrough():
    assert get_max_items({"max_items_per_pull": 50}) == 50
    assert get_max_items({"max_items_per_pull": "100"}) == 100  # YAML int-as-string


def test_get_max_items_invalid_falls_through(capsys):
    assert get_max_items({"max_items_per_pull": "not-a-number"}) == 0
    assert "must be int" in capsys.readouterr().err


def test_get_max_items_negative_rejected(capsys):
    assert get_max_items({"max_items_per_pull": -5}) == 0
    assert "must be >= 0" in capsys.readouterr().err


# ── helpers: get_max_bytes ──────────────────────────────────────────────────


def test_get_max_bytes_default_zero():
    assert get_max_bytes({}) == 0


def test_get_max_bytes_int_passthrough():
    assert get_max_bytes({"max_payload_bytes": 65536}) == 65536


def test_get_max_bytes_invalid_falls_through(capsys):
    assert get_max_bytes({"max_payload_bytes": "huge"}) == 0
    assert "must be int" in capsys.readouterr().err


# ── helpers: payload_within_cap ─────────────────────────────────────────────


def test_payload_within_cap_zero_disables():
    """max_bytes=0 means 'no cap' — every payload passes."""
    big = {"x": "y" * 10000}
    assert payload_within_cap(big, 0)


def test_payload_within_cap_under_passes():
    small = {"x": "y"}
    assert payload_within_cap(small, 1024)


def test_payload_within_cap_over_rejects():
    big = {"x": "y" * 1000}
    assert not payload_within_cap(big, 100)


def test_payload_within_cap_handles_malformed():
    """Non-JSON-serializable payload short-circuits to True (no cap applied
    at this layer; handle_ingest's malformed_payload gate catches it later)."""

    class _NonSerializable:
        def __repr__(self):
            raise RuntimeError("nope")

    assert payload_within_cap({"x": _NonSerializable()}, 100) is True


# ── per-adapter wiring ──────────────────────────────────────────────────────


def test_github_caps_across_repos(tmp_path):
    """Cap is global across repos — once reached, no further repos are pulled."""
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_t")

    fake_a = [
        {
            "number": i,
            "html_url": f"https://github.com/foo/bar/pull/{i}",
            "updated_at": f"2026-05-{i:02d}T00:00:00Z",
            "merged_at": f"2026-05-{i:02d}T01:00:00Z",
        }
        for i in range(1, 4)
    ]
    fake_b = [
        {
            "number": 100,
            "html_url": "https://github.com/foo/baz/pull/100",
            "updated_at": "2026-06-01T00:00:00Z",
            "merged_at": "2026-06-01T01:00:00Z",
        }
    ]

    def _list(*, api_key, owner, repo, updated_after=None):
        return {"bar": fake_a, "baz": fake_b}[repo]

    def _fetch(self, url):
        return {"query": "x", "decisions": [], "participants": []}

    with (
        patch("sources.github.poller.list_merged_pulls_since", side_effect=_list),
        patch("sources.github.adapter.GitHubAdapter.fetch_active", new=_fetch),
    ):
        adapter = GitHubPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "repos": ["foo/bar", "foo/baz"],
                "max_items_per_pull": 2,
            },
        )

    # Cap of 2 — should stop after processing 2 PRs from foo/bar; foo/baz untouched.
    assert len(result) == 2
    assert "foo/bar" in adapter._pending_watermarks
    assert "foo/baz" not in adapter._pending_watermarks


def test_slack_caps_across_channels(tmp_path):
    from events.sources.slack import SlackPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-t")

    msgs_a = [{"ts": f"170000000{i}.000000", "user": "U1", "text": f"msg {i}"} for i in range(1, 4)]
    msgs_b = [{"ts": "1700000099.000000", "user": "U2", "text": "later channel"}]

    def _list(*, token, channel, oldest=None):
        return {"C01A": msgs_a, "C01B": msgs_b}[channel]

    with (
        patch("sources.slack.poller.list_new_messages", side_effect=_list),
        patch("sources.slack.client.get_user_info", return_value={}),
    ):
        adapter = SlackPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "channels": ["C01A", "C01B"],
                "max_items_per_pull": 2,
            },
        )

    assert len(result) == 2
    # C01A's watermark advanced to second-msg ts; C01B never touched.
    assert "C01A" in adapter._pending_watermarks
    assert "C01B" not in adapter._pending_watermarks


def test_google_drive_caps(tmp_path):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    fake_docs = [
        {"id": f"d{i}", "name": f"D{i}", "modifiedTime": f"2026-05-{i:02d}T00:00:00Z"}
        for i in range(1, 6)
    ]

    def _fetch(self, url):
        return {"query": "x", "decisions": [], "participants": []}

    with (
        patch("sources.google_drive.auth.load_credentials", return_value=MagicMock()),
        patch("sources.google_drive.folder.list_docs_in_folder", return_value=fake_docs),
        patch("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", new=_fetch),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"folder_id": "f1", "max_items_per_pull": 2},
        )

    assert len(result) == 2
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"
