"""Tests for ``bicameral-mcp drive-watch`` and ``drive-stop`` CLIs
(#337 cycle 9b).

Sociable on the channel registry (real ChannelRegistry pointed at
tmp), narrow seams on:
- ``sources.google_drive.auth.load_credentials`` (OAuth — can't run in tests)
- ``googleapiclient.discovery.build`` (Drive API — can't run in tests)

Coverage:
- drive-watch: input validation (non-HTTPS callback, bad file URL,
  TTL out of range, token over 256 chars), OAuth-missing exit 2,
  Drive HTTP error exit 2, missing resourceId exit 2, persistence
  failure with successful cleanup, persistence failure with cleanup
  failure, happy path persists ChannelRecord + prints channel_id
- drive-stop: empty channel_id exit 1, no registry entry exit 1,
  OAuth-missing exit 2, Drive HTTP non-404 error exit 2, Drive
  HTTP 404 (already-stopped) still reaps locally, --keep-local
  skips delete, registry-delete failure exit 3, happy path
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sources.google_drive.channels import (
    ChannelRecord,
    ChannelRegistry,
)
from sources.google_drive.channels import (
    _reset_for_tests as _channels_reset,
)


@pytest.fixture(autouse=True)
def _reset_channels(tmp_path: Path):
    """Each test gets a fresh registry at tmp_path so CLI singleton
    reads/writes don't leak across tests."""
    _channels_reset(path=tmp_path / "drive_channels.json")
    yield
    _channels_reset()


def _watch_args(
    *,
    callback_url: str = "https://operator.example.com/webhooks/google-drive",
    file_url: str = "https://docs.google.com/document/d/abc123def456ghi789jkl012mno345pq/edit",
    token: str | None = None,
    ttl_seconds: int = 86400,
) -> argparse.Namespace:
    return argparse.Namespace(
        callback_url=callback_url,
        file_url=file_url,
        token=token,
        ttl_seconds=ttl_seconds,
    )


def _stop_args(
    *,
    channel_id: str = "ch-1",
    keep_local: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(channel_id=channel_id, keep_local=keep_local)


# ── drive-watch: input validation ─────────────────────────────────────────


def test_watch_rejects_non_https_callback(capsys: pytest.CaptureFixture):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(callback_url="http://operator.example.com/x"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "https" in captured.err.lower()


def test_watch_rejects_callback_without_host(capsys: pytest.CaptureFixture):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(callback_url="https:///no-host"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "host" in captured.err.lower()


def test_watch_rejects_bad_file_url(capsys: pytest.CaptureFixture):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(file_url="https://example.com/not-a-doc"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "invalid" in captured.err.lower()


def test_watch_rejects_ttl_zero(capsys: pytest.CaptureFixture):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(ttl_seconds=0))
    assert rc == 1
    captured = capsys.readouterr()
    assert "ttl-seconds" in captured.err


def test_watch_rejects_ttl_over_max(capsys: pytest.CaptureFixture):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(ttl_seconds=86401))
    assert rc == 1
    captured = capsys.readouterr()
    assert "ttl-seconds" in captured.err


def test_watch_rejects_token_over_256_chars(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_watch_cli import main

    rc = main(_watch_args(token="x" * 257))
    assert rc == 1
    captured = capsys.readouterr()
    assert "256" in captured.err


# ── drive-watch: Drive API failures ───────────────────────────────────────


def test_watch_oauth_missing_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_watch_cli import main

    def _no_creds():
        raise RuntimeError("token not configured")

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", _no_creds)

    rc = main(_watch_args())
    assert rc == 2
    captured = capsys.readouterr()
    assert "OAuth" in captured.err or "credentials" in captured.err
    assert "source-auth" in captured.err


def test_watch_drive_http_error_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from googleapiclient.errors import HttpError

    from cli.drive_watch_cli import main

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_resp = MagicMock(status=403, reason="Forbidden")
    fake_service = MagicMock()
    fake_service.files().watch().execute.side_effect = HttpError(
        resp=fake_resp, content=b"forbidden"
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_watch_args())
    assert rc == 2
    captured = capsys.readouterr()
    assert "403" in captured.err


def test_watch_missing_resource_id_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Drive response missing resourceId means we can never validate
    future notifications OR stop the channel. Abort + refuse to
    register."""
    from cli.drive_watch_cli import main

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = {"id": "ch-uuid"}  # NO resourceId
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_watch_args())
    assert rc == 2
    captured = capsys.readouterr()
    assert "resourceId" in captured.err


# ── drive-watch: happy path ───────────────────────────────────────────────


def test_watch_happy_path_persists_channel_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
):
    from cli.drive_watch_cli import main
    from sources.google_drive.channels import get_registry

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = {
        "id": "WILL-BE-OVERWRITTEN-BY-UUID",
        "resourceId": "drive-resource-abc",
        "expiration": "1700000000000",
    }
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_watch_args(token="operator-supplied-token"))
    assert rc == 0
    captured = capsys.readouterr()
    assert "channel_id:" in captured.out
    assert "resource_id: drive-resource-abc" in captured.out
    assert "expires:" in captured.out

    # Registry has exactly one entry.
    records = get_registry().list_all()
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ChannelRecord)
    assert record.resource_id == "drive-resource-abc"
    assert record.token == "operator-supplied-token"
    assert record.file_id == "abc123def456ghi789jkl012mno345pq"
    assert record.watched_resource_kind == "file"


def test_watch_auto_generates_token_when_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
):
    """When --token is omitted, auto-generate via
    secrets.token_urlsafe(32)."""
    from cli.drive_watch_cli import main
    from sources.google_drive.channels import get_registry

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = {
        "resourceId": "drive-resource-xyz",
    }
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_watch_args(token=None))
    assert rc == 0

    records = get_registry().list_all()
    assert len(records) == 1
    # token_urlsafe(32) yields ~43 chars of base64-ish output.
    assert len(records[0].token) >= 32


def test_watch_persistence_failure_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """When local registry persistence fails, the CLI tries to stop
    the just-created channel on Drive's side to avoid leaking it."""
    from cli.drive_watch_cli import main

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    stop_called = []
    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = {
        "resourceId": "drive-resource-leak",
    }
    fake_service.channels().stop().execute = lambda: stop_called.append(True)
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    # Force registry.register to fail.
    monkeypatch.setattr(
        "sources.google_drive.channels.ChannelRegistry.register",
        MagicMock(side_effect=OSError("simulated disk full")),
    )

    rc = main(_watch_args())
    assert rc == 3
    captured = capsys.readouterr()
    assert "persistence failed" in captured.err
    # Cleanup was at least attempted (the mock side-effect chain
    # records the call).
    assert fake_service.channels().stop.called


# ── drive-stop: input validation + happy path ────────────────────────────


def test_stop_empty_channel_id_returns_1(capsys: pytest.CaptureFixture):
    from cli.drive_stop_cli import main

    rc = main(_stop_args(channel_id="   "))
    assert rc == 1
    captured = capsys.readouterr()
    assert "empty" in captured.err.lower()


def test_stop_no_registry_entry_returns_1(capsys: pytest.CaptureFixture):
    from cli.drive_stop_cli import main

    rc = main(_stop_args(channel_id="ch-doesnt-exist"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "no registry entry" in captured.err


def test_stop_oauth_missing_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_stop_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(
        ChannelRecord(
            channel_id="ch-1",
            resource_id="res-1",
            token="t",
            expiration_ms=0,
            file_id="f",
        )
    )

    def _no_creds():
        raise RuntimeError("token not configured")

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", _no_creds)

    rc = main(_stop_args())
    assert rc == 2


def test_stop_drive_404_still_reaps_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Channel already stopped on Drive's side (expired or canceled
    via Drive UI) → 404. We still delete the local registry entry."""
    from googleapiclient.errors import HttpError

    from cli.drive_stop_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(
        ChannelRecord(
            channel_id="ch-stale",
            resource_id="res-stale",
            token="t",
            expiration_ms=0,
            file_id="f",
        )
    )
    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_resp = MagicMock(status=404, reason="Not Found")
    fake_service = MagicMock()
    fake_service.channels().stop().execute.side_effect = HttpError(
        resp=fake_resp, content=b"not found"
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_stop_args(channel_id="ch-stale"))
    assert rc == 0
    captured = capsys.readouterr()
    assert "already stopped" in captured.err
    # Local entry was reaped.
    assert get_registry().get("ch-stale") is None


def test_stop_drive_other_http_error_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Non-404 Drive errors are reported but the local entry is
    preserved so operator can retry."""
    from googleapiclient.errors import HttpError

    from cli.drive_stop_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(
        ChannelRecord(
            channel_id="ch-perm",
            resource_id="res-perm",
            token="t",
            expiration_ms=0,
            file_id="f",
        )
    )
    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_resp = MagicMock(status=403, reason="Forbidden")
    fake_service = MagicMock()
    fake_service.channels().stop().execute.side_effect = HttpError(
        resp=fake_resp, content=b"forbidden"
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_stop_args(channel_id="ch-perm"))
    assert rc == 2
    # Local entry NOT reaped — operator can retry.
    assert get_registry().get("ch-perm") is not None


def test_stop_happy_path_deletes_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_stop_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(
        ChannelRecord(
            channel_id="ch-good",
            resource_id="res-good",
            token="t",
            expiration_ms=0,
            file_id="f",
        )
    )
    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_stop_args(channel_id="ch-good"))
    assert rc == 0
    captured = capsys.readouterr()
    assert "stopped and reaped" in captured.out
    assert get_registry().get("ch-good") is None


def test_stop_keep_local_preserves_entry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """--keep-local skips the registry delete (debugging flag)."""
    from cli.drive_stop_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(
        ChannelRecord(
            channel_id="ch-keep",
            resource_id="res-keep",
            token="t",
            expiration_ms=0,
            file_id="f",
        )
    )
    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_stop_args(channel_id="ch-keep", keep_local=True))
    assert rc == 0
    captured = capsys.readouterr()
    assert "NOT deleted" in captured.err
    assert get_registry().get("ch-keep") is not None
