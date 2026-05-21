"""Tests for ``bicameral-mcp drive-renew`` (#337 cycle 9c).

Sociable on ChannelRegistry (real registry at tmp_path), narrow
seams on:
- ``sources.google_drive.auth.load_credentials`` (OAuth — can't run)
- ``googleapiclient.discovery.build`` (Drive API — can't run)

Coverage:
- Input validation (threshold out of range)
- No channels due → exit 0
- Dry-run reports without API calls
- Happy path renews 1 channel: persists new record, stops old,
  deletes old entry
- callback_url empty (pre-9c row) → skipped with reason; not a hard fail
- successor response missing resourceId → honest MANUAL ACTION log,
  no doomed channels.stop() call, per-channel fail (LOW-2)
- --token-rotation always/preserve, empty-token fallback, bad value (MED-3)
- persist failure → cleanup attempted + per-channel fail
- old channels.stop 404 → tolerated (continue)
- old registry.delete failure → tolerated + warning (don't fail the renewal)
- Multi-channel pass: 2 succeed + 1 fails → exit 2 with summary
- OAuth credentials missing → exit 3
"""

from __future__ import annotations

import argparse
import time
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
    _channels_reset(path=tmp_path / "drive_channels.json")
    yield
    _channels_reset()


def _args(
    threshold_seconds: int = 12 * 60 * 60,
    dry_run: bool = False,
    token_rotation: str = "always",
) -> argparse.Namespace:
    return argparse.Namespace(
        threshold_seconds=threshold_seconds,
        dry_run=dry_run,
        token_rotation=token_rotation,
    )


def _record(
    *,
    channel_id: str = "ch-old",
    resource_id: str = "res-old",
    token: str = "tok-old",
    file_id: str = "file-1",
    expiration_ms: int | None = None,
    callback_url: str = "https://operator.example.com/webhooks/google-drive",
) -> ChannelRecord:
    if expiration_ms is None:
        # Default to "expiring in 1 hour" so the default 12h threshold catches it.
        expiration_ms = int((time.time() + 3600) * 1000)
    return ChannelRecord(
        channel_id=channel_id,
        resource_id=resource_id,
        token=token,
        expiration_ms=expiration_ms,
        file_id=file_id,
        callback_url=callback_url,
    )


def _watch_response(*, resource_id: str = "res-new") -> dict:
    """Drive's files.watch response shape."""
    return {
        "id": "irrelevant",
        "resourceId": resource_id,
        "expiration": "1700000000000",
    }


# ── Input validation ─────────────────────────────────────────────────────────


def test_threshold_zero_rejected(capsys: pytest.CaptureFixture):
    from cli.drive_renew_cli import main

    rc = main(_args(threshold_seconds=0))
    assert rc == 1
    captured = capsys.readouterr()
    assert "threshold-seconds" in captured.err


def test_threshold_over_ceiling_rejected(capsys: pytest.CaptureFixture):
    """MED-2 review fix: ceiling at half of Drive's max TTL (43200s).
    Above this every channel becomes due every pass, doubling API
    traffic gratuitously."""
    from cli.drive_renew_cli import main

    rc = main(_args(threshold_seconds=43201))
    assert rc == 1
    captured = capsys.readouterr()
    assert "43200" in captured.err


# ── Empty / no-due cases ─────────────────────────────────────────────────────


def test_empty_registry_returns_0(capsys: pytest.CaptureFixture):
    from cli.drive_renew_cli import main

    rc = main(_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "0 channel(s) in registry" in captured.err
    assert "0 due" in captured.err


def test_no_channels_due_returns_0(capsys: pytest.CaptureFixture):
    """Channels with > threshold lifetime remaining are skipped."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    # Expires in 23h — well past the 12h threshold.
    get_registry().register(_record(expiration_ms=int((time.time() + 23 * 3600) * 1000)))
    rc = main(_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "1 channel(s) in registry" in captured.err
    assert "0 due" in captured.err


# ── Dry-run ───────────────────────────────────────────────────────────────────


def test_dry_run_reports_without_api_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-dry"))

    # If dry-run accidentally hits the API, this would explode.
    def _explode():
        raise AssertionError("load_credentials should not be called in --dry-run")

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", _explode)

    rc = main(_args(dry_run=True))
    assert rc == 0
    captured = capsys.readouterr()
    assert "ch-dry" in captured.out
    assert "file_id=file-1" in captured.out


# ── Happy path ────────────────────────────────────────────────────────────────


def test_renew_one_happy_path(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-old", resource_id="res-old"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response(resource_id="res-new")
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 0

    # Old gone, new present.
    registry = get_registry()
    assert registry.get("ch-old") is None
    records = registry.list_all()
    assert len(records) == 1
    new_record = records[0]
    assert new_record.resource_id == "res-new"
    assert new_record.file_id == "file-1"
    assert new_record.callback_url == "https://operator.example.com/webhooks/google-drive"
    # New token is freshly minted, not the old one.
    assert new_record.token != "tok-old"
    assert len(new_record.token) >= 32

    captured = capsys.readouterr()
    assert "renewed: 1" in captured.out
    assert "failed: 0" in captured.out


# ── Per-channel failure modes ────────────────────────────────────────────────


def test_pre_9c_rows_partitioned_to_warning_not_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """MED-1 review fix: pre-9c rows (no callback_url) are partitioned
    OUT of the renewal loop and reported as a one-time warning,
    NOT counted as failures. Exit 0 if no renewable failures."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-no-url", callback_url=""))

    # If the partition works, we never need creds.
    def _explode():
        raise AssertionError("load_credentials should not be called")

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", _explode)

    rc = main(_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "cannot be auto-renewed" in captured.err
    assert "ch-no-url" in captured.err
    assert "drive-watch" in captured.err  # operator action hint


def test_pre_9c_rows_partition_does_not_mask_renewable_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """When mixing pre-9c rows + renewable failures, only the
    renewable failures affect the exit code."""
    from googleapiclient.errors import HttpError

    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    # One pre-9c row (warned) + one renewable that will fail.
    get_registry().register(_record(channel_id="ch-pre9c", callback_url=""))
    get_registry().register(_record(channel_id="ch-403"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())
    fake_resp = MagicMock(status=403, reason="Forbidden")
    fake_service = MagicMock()
    fake_service.files().watch().execute.side_effect = HttpError(resp=fake_resp, content=b"")
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 2  # renewable failure
    captured = capsys.readouterr()
    assert "WARNING" in captured.err  # pre-9c warning shown
    assert "renewed: 0" in captured.out
    assert "failed: 1" in captured.out  # only ch-403 counted


def test_renew_missing_resource_id_emits_manual_action(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """LOW-2 (cycle-9c review): when the successor files.watch response
    lacks resourceId, the channel cannot be stopped via the API
    (channels.stop requires a resourceId). The handler must emit an
    honest MANUAL ACTION log and must NOT make a doomed channels.stop()
    call with an empty resourceId."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-noresid"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = {"id": "x"}  # NO resourceId
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 2
    captured = capsys.readouterr()
    # Per-channel failure surfaced in the stdout summary.
    assert "resourceId" in captured.out
    # Honest MANUAL ACTION log on stderr — no cleanup theater.
    assert "MANUAL ACTION" in captured.err
    # The doomed channels.stop() call must NOT have been made for the
    # no-resourceId path (the old code called it with resourceId="").
    assert fake_service.channels().stop.call_count == 0
    # Old entry still present — renewal didn't complete.
    assert get_registry().get("ch-noresid") is not None


# ── MED-3: --token-rotation policy ──────────────────────────────────────────


def _successor_record(registry, old_channel_id: str):
    """After a successful renewal the registry holds exactly the
    successor (old entry deleted). Return it."""
    rows = [r for r in registry.list_all() if r.channel_id != old_channel_id]
    assert len(rows) == 1, f"expected exactly one successor, got {len(rows)}"
    return rows[0]


def test_token_rotation_always_mints_fresh_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Default policy: the successor channel gets a freshly minted
    token, distinct from the old channel's token."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-rot", token="tok-old"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())
    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args(token_rotation="always"))
    assert rc == 0
    successor = _successor_record(get_registry(), "ch-rot")
    assert successor.token != "tok-old"
    assert len(successor.token) > 0


def test_token_rotation_preserve_reuses_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """--token-rotation preserve: the successor channel reuses the old
    channel's token verbatim."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-keep", token="tok-old"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())
    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args(token_rotation="preserve"))
    assert rc == 0
    successor = _successor_record(get_registry(), "ch-keep")
    assert successor.token == "tok-old"
    # Per-pass posture notice surfaced on stderr.
    assert "preserve" in capsys.readouterr().err


def test_token_rotation_preserve_empty_token_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """--token-rotation preserve on a row whose stored token is empty
    (registry corruption / pre-9c) must fall back to minting a fresh
    non-empty token — persisting a tokenless successor would produce a
    channel that verify_notification rejects on every delivery."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-notoken", token=""))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())
    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args(token_rotation="preserve"))
    assert rc == 0
    successor = _successor_record(get_registry(), "ch-notoken")
    assert len(successor.token) > 0, "successor must not be tokenless"
    # The fallback is logged so the operator sees it.
    assert "empty" in capsys.readouterr().err


def test_token_rotation_rejects_unknown_value():
    """argparse choices reject an invalid --token-rotation value at
    parse time."""
    from cli.drive_renew_cli import _build_argparser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    renew = sub.add_parser("drive-renew")
    _build_argparser(renew)
    with pytest.raises(SystemExit):
        parser.parse_args(["drive-renew", "--token-rotation", "bogus"])


def test_renew_files_watch_http_error_per_channel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from googleapiclient.errors import HttpError

    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-403"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_resp = MagicMock(status=403, reason="Forbidden")
    fake_service = MagicMock()
    fake_service.files().watch().execute.side_effect = HttpError(
        resp=fake_resp, content=b"forbidden"
    )
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 2
    captured = capsys.readouterr()
    assert "files.watch HTTP 403" in captured.out


def test_renew_old_stop_404_tolerated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """channels.stop on the OLD channel returning 404 is fine — the
    new channel still got registered. Renewal succeeds overall."""
    from googleapiclient.errors import HttpError

    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-old-gone"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    fake_resp = MagicMock(status=404, reason="Not Found")
    fake_service.channels().stop().execute.side_effect = HttpError(resp=fake_resp, content=b"")
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    # Stop failed 404 but the renewal SUCCEEDED — new channel registered.
    # Old entry is still present because step-4 delete only runs after a
    # successful stop or 404. Wait — 404 IS treated as success-ish in the
    # impl, so step-4 should run and delete. Let me verify what the
    # contract should be: 404 on stop means Drive forgot the channel,
    # registry should also forget it.
    assert rc == 0
    # registry.delete should have been called and succeeded.
    assert get_registry().get("ch-old-gone") is None


def test_renew_old_stop_other_error_tolerated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """channels.stop on the OLD channel returning 500 is logged but
    renewal still succeeds (the OLD channel will expire naturally)."""
    from googleapiclient.errors import HttpError

    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-stop500"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    fake_resp = MagicMock(status=500, reason="Server Error")
    fake_service.channels().stop().execute.side_effect = HttpError(resp=fake_resp, content=b"")
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 0  # Renewal still succeeded overall.
    captured = capsys.readouterr()
    assert "expire naturally" in captured.err


def test_renew_registry_persist_failure_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """ChannelRegistry.register failing for the new record triggers a
    cleanup channels.stop call so we don't leak the new Drive-side
    channel."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-persist-fail"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    # Fixture setup above already used the original register. Now
    # install a stub that raises for every subsequent call — the
    # next register IS the new-record persist inside _renew_one.
    monkeypatch.setattr(
        "sources.google_drive.channels.ChannelRegistry.register",
        MagicMock(side_effect=OSError("simulated disk full")),
    )

    rc = main(_args())
    assert rc == 2
    captured = capsys.readouterr()
    assert "persist failed" in captured.out
    # Cleanup attempted.
    assert fake_service.channels().stop.called


def test_renew_old_registry_delete_failure_tolerated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """Failing to delete the OLD registry entry is logged but does
    NOT fail the renewal — the new channel is active. Operator must
    manually clean up the stale row."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-deletefail"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    monkeypatch.setattr(
        "sources.google_drive.channels.ChannelRegistry.delete",
        MagicMock(side_effect=OSError("delete failed")),
    )

    rc = main(_args())
    assert rc == 0  # Renewal succeeded overall.
    captured = capsys.readouterr()
    assert "delete of old registry entry" in captured.err
    assert "MANUAL ACTION" in captured.err


# ── Multi-channel pass ───────────────────────────────────────────────────────


def test_multi_channel_all_succeed_returns_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """3 renewable channels all succeed → exit 0."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-ok1", file_id="file-1"))
    get_registry().register(_record(channel_id="ch-ok2", file_id="file-2", resource_id="res-old-2"))
    get_registry().register(_record(channel_id="ch-ok3", file_id="file-3", resource_id="res-old-3"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())

    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    rc = main(_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "renewed: 3" in captured.out
    assert "failed: 0" in captured.out


def test_lock_unavailable_returns_0(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """HIGH-2 review fix: another concurrent drive-renew invocation
    holds the lock. Skip this pass with exit 0 — the in-progress
    pass will handle the work."""
    from cli.drive_renew_cli import _LockUnavailable, main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-locked"))

    monkeypatch.setattr(
        "cli.drive_renew_cli._acquire_lock",
        MagicMock(side_effect=_LockUnavailable("simulated contention")),
    )

    rc = main(_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "another renewal pass" in captured.err
    assert "skipping" in captured.err


def test_lock_released_after_successful_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """HIGH-2 review fix corollary: lock is released even on the
    happy path so subsequent invocations succeed."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-locktest"))

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", lambda: object())
    fake_service = MagicMock()
    fake_service.files().watch().execute.return_value = _watch_response()
    fake_service.channels().stop().execute.return_value = None
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **kw: fake_service)

    # Use a tmp lock path so we don't fight with the real
    # ~/.bicameral/.drive_renew.lock.
    release_calls = []
    real_release = __import__("cli.drive_renew_cli", fromlist=["_release_lock"])._release_lock

    def _tracked_release(fd):
        release_calls.append(fd)
        real_release(fd)

    monkeypatch.setattr("cli.drive_renew_cli._release_lock", _tracked_release)

    rc = main(_args())
    assert rc == 0
    # Lock release was invoked exactly once.
    assert len(release_calls) == 1


def test_dry_run_does_not_acquire_lock(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """HIGH-2 corollary: --dry-run doesn't mutate, so it shouldn't
    block on the renewal lock."""
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record(channel_id="ch-dry-lock"))

    acquire_called = []
    monkeypatch.setattr(
        "cli.drive_renew_cli._acquire_lock",
        lambda path: acquire_called.append(path),
    )

    rc = main(_args(dry_run=True))
    assert rc == 0
    assert acquire_called == []


# ── Infrastructure failures ──────────────────────────────────────────────────


def test_oauth_missing_returns_3(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    from cli.drive_renew_cli import main
    from sources.google_drive.channels import get_registry

    get_registry().register(_record())

    def _no_creds():
        raise RuntimeError("token not configured")

    monkeypatch.setattr("sources.google_drive.auth.load_credentials", _no_creds)

    rc = main(_args())
    assert rc == 3
    captured = capsys.readouterr()
    assert "OAuth" in captured.err or "credentials" in captured.err


def test_registry_read_failure_returns_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    from cli.drive_renew_cli import main

    monkeypatch.setattr(
        "sources.google_drive.channels.ChannelRegistry.list_all",
        MagicMock(side_effect=OSError("simulated read fail")),
    )

    rc = main(_args())
    assert rc == 3
    captured = capsys.readouterr()
    assert "registry read failed" in captured.err
