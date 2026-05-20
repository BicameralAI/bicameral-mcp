"""Tests for the Google Drive Push Notifications handler (#337 cycle 9).

(File-mode posture pin lives below alongside the other registry tests.)

Coverage parity with the GitHub/Slack/Linear suites, scoped to Drive's
weaker (non-HMAC) auth model:

- verify_notification: missing each of the three required headers,
  unknown channel-id, token-empty-in-registry, token mismatch,
  resource-id mismatch (with stderr log assertion), happy path
- handle: 401 on verification failure, 200 + log on sync message,
  200 + dirty-marker log on each non-sync state, 200 + unknown-state
  log on a state we haven't taught the handler about
- ChannelRegistry: register / get / delete / list_all, JSON
  round-trip, atomic-write torn-read pin, malformed-row recovery,
  empty / missing channel_id rejection
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from sources.google_drive.channels import (
    ChannelRecord,
    ChannelRegistry,
)
from sources.google_drive.channels import (
    _reset_for_tests as _channels_reset,
)
from webhooks.google_drive import (
    WebhookVerificationError,
    handle,
    verify_notification,
)


@pytest.fixture
def reg(tmp_path: Path) -> ChannelRegistry:
    """Fresh, file-backed registry pointed at a tmp path."""
    return ChannelRegistry(path=tmp_path / "drive_channels.json")


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    # Cycle 9b: stub fetch_active by default so tests that don't
    # explicitly care about the ingest path (most pre-cycle-9b
    # tests, written when the handler was ack-only) don't fail
    # when the default ingest-trigger states (update/change/etc.)
    # try to fetch ``docs.google.com/document/d/file-1/edit`` — not
    # a real Drive URL. Individual tests that exercise the
    # fetch/ingest failure paths override this stub.
    def _default_fetch(self, url):
        return {
            "query": "stub-title",
            "source": "google_drive",
            "title": "stub-title",
            "date": "",
            "participants": [],
            "decisions": [{"description": "stub", "title": "stub-title"}],
        }

    monkeypatch.setattr(
        "sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _default_fetch
    )

    async def _default_ingest(ctx, payload, *, source_scope, ingest_mode):
        pass

    monkeypatch.setattr("handlers.ingest.handle_ingest", _default_ingest)

    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    yield
    _channels_reset()


def _record(
    *,
    channel_id: str = "ch-1",
    resource_id: str = "res-1",
    token: str = "tok-1",
    file_id: str = "file-1",
    expiration_ms: int = 1_700_000_000_000,
) -> ChannelRecord:
    return ChannelRecord(
        channel_id=channel_id,
        resource_id=resource_id,
        token=token,
        file_id=file_id,
        expiration_ms=expiration_ms,
    )


# ── ChannelRegistry ─────────────────────────────────────────────────────────


def test_registry_register_and_get(reg: ChannelRegistry):
    rec = _record()
    reg.register(rec)
    got = reg.get("ch-1")
    assert got == rec


def test_registry_get_missing_returns_none(reg: ChannelRegistry):
    assert reg.get("nope") is None


def test_registry_get_empty_channel_id_returns_none(reg: ChannelRegistry):
    """Defensive: empty channel_id is a programming error; treat as miss."""
    assert reg.get("") is None


def test_registry_register_overwrites_same_id(reg: ChannelRegistry):
    """Renewal flow (future cycle) reuses channel_id semantics — last
    write wins. Pin this so a future caller doesn't accidentally
    double-register."""
    reg.register(_record(token="old"))
    reg.register(_record(token="new"))
    got = reg.get("ch-1")
    assert got is not None
    assert got.token == "new"


def test_registry_register_rejects_empty_channel_id(reg: ChannelRegistry):
    with pytest.raises(ValueError, match="channel_id"):
        reg.register(_record(channel_id=""))


def test_registry_register_rejects_empty_resource_id(reg: ChannelRegistry):
    with pytest.raises(ValueError, match="resource_id"):
        reg.register(_record(resource_id=""))


def test_registry_delete_existing_returns_true(reg: ChannelRegistry):
    reg.register(_record())
    assert reg.delete("ch-1") is True
    assert reg.get("ch-1") is None


def test_registry_delete_missing_returns_false(reg: ChannelRegistry):
    assert reg.delete("nope") is False


def test_registry_list_all(reg: ChannelRegistry):
    reg.register(_record(channel_id="ch-1"))
    reg.register(_record(channel_id="ch-2", resource_id="res-2"))
    all_records = reg.list_all()
    assert len(all_records) == 2
    assert {r.channel_id for r in all_records} == {"ch-1", "ch-2"}


def test_registry_persists_across_instances(tmp_path: Path):
    """A new ChannelRegistry pointed at the same path sees prior writes
    — pins the JSON round-trip without going through the in-memory cache."""
    path = tmp_path / "drive_channels.json"
    reg1 = ChannelRegistry(path=path)
    reg1.register(_record())
    reg2 = ChannelRegistry(path=path)
    assert reg2.get("ch-1") == _record()


def test_registry_missing_file_is_empty(tmp_path: Path):
    """No file yet → empty registry, no error."""
    reg = ChannelRegistry(path=tmp_path / "drive_channels.json")
    assert reg.get("ch-1") is None
    assert reg.list_all() == []


def test_registry_corrupt_file_treated_as_empty(tmp_path: Path):
    """Corrupt JSON is salvaged as an empty registry — operator can
    replay channels.watch to repopulate. We do NOT auto-delete the
    corrupt file (footgun); leave it for human inspection."""
    path = tmp_path / "drive_channels.json"
    path.write_text("{not-json")
    reg = ChannelRegistry(path=path)
    assert reg.list_all() == []
    # File is still there for the operator to inspect.
    assert path.exists()


def test_registry_skips_malformed_rows(tmp_path: Path):
    """One bad row doesn't take the whole registry down."""
    path = tmp_path / "drive_channels.json"
    payload = {
        "good": asdict(_record(channel_id="good")),
        "bad": {"channel_id": "bad"},  # missing required fields
        "alsobad": "not-a-dict",
    }
    path.write_text(json.dumps(payload))
    reg = ChannelRegistry(path=path)
    rows = reg.list_all()
    assert len(rows) == 1
    assert rows[0].channel_id == "good"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits not enforced on Windows")
def test_registry_file_mode_is_0o600_after_register(tmp_path: Path):
    """MED-1 review finding: registry file must be 0o600 post-register
    so other local users can't read (channel_id, token, resource_id)
    triples and forge notifications under Drive's three-way-match
    auth model. Parent dir must be 0o700."""
    path = tmp_path / "drive_channels.json"
    reg = ChannelRegistry(path=path)
    reg.register(_record())
    file_mode = stat.S_IMODE(os.stat(path).st_mode)
    dir_mode = stat.S_IMODE(os.stat(path.parent).st_mode)
    assert file_mode == 0o600, f"expected 0o600, got {oct(file_mode)}"
    assert dir_mode == 0o700, f"expected 0o700, got {oct(dir_mode)}"


def test_registry_atomic_write_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pin the atomic-write invariant: if the write fails mid-flight,
    the on-disk file is either the previous version or unchanged —
    never a half-written truncated file."""
    path = tmp_path / "drive_channels.json"
    reg = ChannelRegistry(path=path)
    reg.register(_record(channel_id="ch-original"))
    original_content = path.read_text()

    # Force the os.replace step to raise.
    from sources.google_drive import channels as _channels_mod

    def _boom(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(_channels_mod.os, "replace", _boom)

    with pytest.raises(OSError, match="disk full"):
        reg.register(_record(channel_id="ch-new"))

    # File on disk is still the original version.
    assert path.read_text() == original_content
    # No stray .tmp files in the directory.
    tmp_files = list(path.parent.glob(".drive_channels.*.tmp"))
    assert tmp_files == []


# ── verify_notification ─────────────────────────────────────────────────────


def test_verify_missing_channel_id_rejected(reg: ChannelRegistry):
    with pytest.raises(WebhookVerificationError, match="Channel-ID"):
        verify_notification(
            channel_id=None,
            channel_token="t",
            resource_id="r",
            registry=reg,
        )


def test_verify_missing_token_rejected(reg: ChannelRegistry):
    with pytest.raises(WebhookVerificationError, match="Channel-Token"):
        verify_notification(
            channel_id="ch-1",
            channel_token=None,
            resource_id="r",
            registry=reg,
        )


def test_verify_missing_resource_id_rejected(reg: ChannelRegistry):
    with pytest.raises(WebhookVerificationError, match="Resource-ID"):
        verify_notification(
            channel_id="ch-1",
            channel_token="t",
            resource_id=None,
            registry=reg,
        )


def test_verify_empty_string_channel_id_rejected(reg: ChannelRegistry):
    """LOW-1 review finding: the HTTP layer strips header values, so
    ``X-Goog-Channel-Id: <whitespace>`` arrives as ``""``. The
    ``if not channel_id`` guard at handler:92 catches this AND
    ``ChannelRegistry.get("")`` short-circuits to None. Pin both layers."""
    with pytest.raises(WebhookVerificationError, match="Channel-ID"):
        verify_notification(
            channel_id="",
            channel_token="t",
            resource_id="r",
            registry=reg,
        )


def test_handle_non_ascii_resource_state_returns_400(reg: ChannelRegistry):
    """LOW-2 review finding: ASCII gate on resource_state. The HTTP
    layer already enforces this at server.py:134, but the handler is
    self-defending too in case it's invoked from non-HTTP callers."""
    reg.register(_record())
    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="SYNCͅ",  # combining iota subscript
        message_number="1",
        registry=reg,
    )
    assert status == 400
    assert "non-ASCII" in msg


def test_verify_unknown_channel_rejected(reg: ChannelRegistry):
    with pytest.raises(WebhookVerificationError, match="unknown channel_id"):
        verify_notification(
            channel_id="ch-1",
            channel_token="t",
            resource_id="r",
            registry=reg,
        )


def test_verify_empty_registered_token_rejected(reg: ChannelRegistry):
    """Registry-corruption case: row exists but token is empty. The
    register() entrypoint doesn't strictly forbid empty token (Drive
    technically allows it), but the verify path refuses to compare
    against empty string — empty token compared with anything is a
    spoofing risk."""
    reg.register(_record(token=""))
    with pytest.raises(WebhookVerificationError, match="no token registered"):
        verify_notification(
            channel_id="ch-1",
            channel_token="anything",
            resource_id="res-1",
            registry=reg,
        )


def test_verify_token_mismatch_rejected(reg: ChannelRegistry):
    reg.register(_record(token="correct-token"))
    with pytest.raises(WebhookVerificationError, match="token mismatch"):
        verify_notification(
            channel_id="ch-1",
            channel_token="WRONG-token",
            resource_id="res-1",
            registry=reg,
        )


def test_verify_resource_id_mismatch_rejected_and_logged(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture
):
    """The 'signed-payload / unsigned-header' analog from Linear H3:
    an attacker who has learned (channel_id, token) still cannot
    succeed if they can't supply the matching resource_id. Loud-log
    on this path is the canonical lateral-movement signal."""
    reg.register(_record(resource_id="res-correct", token="t"))
    with pytest.raises(WebhookVerificationError, match="resource_id mismatch"):
        verify_notification(
            channel_id="ch-1",
            channel_token="t",
            resource_id="res-WRONG",
            registry=reg,
        )
    captured = capsys.readouterr()
    assert "resource_id mismatch" in captured.err
    assert "res-WRONG" in captured.err
    assert "res-correct" in captured.err


def test_verify_happy_path(reg: ChannelRegistry):
    reg.register(_record())
    verify_notification(
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        registry=reg,
    )  # does not raise


def test_verify_falls_back_to_singleton_when_no_registry_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The handler is called by _dispatch_google_drive without an
    explicit registry — pin that the singleton path works."""
    from sources.google_drive import channels as _ch

    # Point the singleton at our tmp file.
    _ch._reset_for_tests(path=tmp_path / "drive_channels.json")
    _ch.get_registry().register(_record())

    verify_notification(
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
    )  # does not raise


# ── handle ──────────────────────────────────────────────────────────────────


def test_handle_verification_failure_returns_401(reg: ChannelRegistry):
    """Verify failure → 401, NOT 5xx. We do NOT want Drive's retry
    machinery to keep delivering to a broken channel."""
    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="t",
        resource_id="r",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 401
    assert "verification" in msg


def test_handle_sync_message_returns_200_no_ingest(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture
):
    """Drive's first message after channels.watch is a sync. Per
    Drive docs: 'safe to ignore.' We 200 it so Google marks the
    channel as healthy."""
    reg.register(_record())
    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="sync",
        message_number="1",
        registry=reg,
    )
    assert status == 200
    assert "sync" in msg.lower()
    captured = capsys.readouterr()
    assert "sync ack" in captured.err


def test_handle_sync_message_with_unexpected_message_number_still_acks(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture
):
    """Defense-in-depth: a sync-state with message_number != '1' is
    a provider-side bug or replay attempt. Still 200 (don't let Drive
    retry forever) but loud-log."""
    reg.register(_record())
    status, _ = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="sync",
        message_number="9999",
        registry=reg,
    )
    assert status == 200
    captured = capsys.readouterr()
    assert "unexpected" in captured.err
    assert "9999" in captured.err


@pytest.mark.parametrize("state", ["remove", "trash"])
def test_handle_delete_states_acked_no_ingest(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, state: str, monkeypatch
):
    """Cycle 9b: ``remove`` and ``trash`` are append-only-contract
    acks — we do NOT propagate deletes to the ledger."""
    reg.register(_record())
    # Defensive: monkeypatch the adapter so a regression that
    # accidentally routes deletes to ingest is loud, not silent.
    fetch_called = []

    def _fake_fetch(self, url):
        fetch_called.append(url)
        return {}

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fake_fetch)

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state=state,
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "no ingest" in msg or "append-only" in msg
    assert fetch_called == []


@pytest.mark.parametrize("state", ["add", "update", "change", "untrash"])
def test_handle_ingest_states_fetch_and_pipe(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, state: str, monkeypatch
):
    """Cycle 9b: ``add``/``update``/``change``/``untrash`` trigger
    fetch via GoogleDriveAdapter + handle_ingest in passive mode."""
    reg.register(_record(file_id="abc123def456ghi789jkl012mno345pq"))
    captured_data: dict = {}

    def _fake_fetch(self, url):
        captured_data["fetch_url"] = url
        return {
            "query": "doc-title",
            "source": "google_drive",
            "title": "doc-title",
            "date": "",
            "participants": [],
            "decisions": [{"description": "doc body", "title": "doc-title"}],
        }

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        captured_data["scope"] = source_scope
        captured_data["mode"] = ingest_mode

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fake_fetch)
    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state=state,
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "ingested" in msg
    assert captured_data["scope"] == "google_drive"
    assert captured_data["mode"] == "passive"
    # URL is built from file_id in the registry record.
    assert "abc123def456ghi789jkl012mno345pq" in captured_data["fetch_url"]


def test_handle_ingest_state_fetch_4xx_returns_200_deterministic(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, monkeypatch
):
    """M1 review fix: 4xx-not-429 wrapped in RuntimeError → 200
    (file deleted / permission revoked → no point retrying)."""
    reg.register(_record())

    from unittest.mock import MagicMock

    from googleapiclient.errors import HttpError

    def _fetch_404(self, url):
        resp = MagicMock(status=404, reason="Not Found")
        cause = HttpError(resp=resp, content=b"not found")
        raise RuntimeError("Google Docs API call failed: 404") from cause

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fetch_404)

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "no retry" in msg or "http=404" in msg
    err = capsys.readouterr().err
    assert "deterministic=True" in err
    assert "http_status=404" in err


def test_handle_ingest_state_fetch_5xx_returns_500_transient(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, monkeypatch
):
    """M1 review fix corollary: 5xx wrapped in RuntimeError → 500
    so Drive's 8-retry envelope kicks in."""
    reg.register(_record())

    from unittest.mock import MagicMock

    from googleapiclient.errors import HttpError

    def _fetch_503(self, url):
        resp = MagicMock(status=503, reason="Service Unavailable")
        cause = HttpError(resp=resp, content=b"")
        raise RuntimeError("Google Docs API call failed: 503") from cause

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fetch_503)

    status, _ = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 500
    err = capsys.readouterr().err
    assert "deterministic=False" in err


def test_handle_ingest_state_fetch_429_returns_500_transient(reg: ChannelRegistry, monkeypatch):
    """M1 review fix corollary: 429 (rate limit) is 4xx but NOT
    deterministic — retry envelope is the right backpressure."""
    reg.register(_record())

    from unittest.mock import MagicMock

    from googleapiclient.errors import HttpError

    def _fetch_429(self, url):
        resp = MagicMock(status=429, reason="Too Many Requests")
        cause = HttpError(resp=resp, content=b"")
        raise RuntimeError("Google Docs API call failed: 429") from cause

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fetch_429)

    status, _ = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 500


def test_handle_ingest_state_fetch_failure_returns_500_for_retry(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, monkeypatch
):
    """RuntimeError from the adapter → 500 so Drive's 8-retry
    envelope kicks in. Without distinguishing 4xx from 5xx (the
    adapter doesn't expose status), conservative default is
    transient."""
    reg.register(_record())

    def _broken_fetch(self, url):
        raise RuntimeError("simulated Drive API failure")

    monkeypatch.setattr(
        "sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _broken_fetch
    )

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 500
    assert "transient" in msg or "retry" in msg
    err = capsys.readouterr().err
    assert "simulated Drive API failure" in err


def test_handle_ingest_state_hard_gate_refusal_returns_200(reg: ChannelRegistry, monkeypatch):
    """Hard-gate refusal → 200 (NOT 500) so Drive doesn't retry the
    refused payload 8 times."""
    reg.register(_record())

    def _fake_fetch(self, url):
        return {
            "query": "x",
            "source": "google_drive",
            "title": "x",
            "date": "",
            "participants": [],
            "decisions": [{"description": "x", "title": "x"}],
        }

    from handlers.ingest import _IngestRefused

    async def _refuse(ctx, payload, *, source_scope, ingest_mode):
        raise _IngestRefused(reason="sensitive_data:phi")

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fake_fetch)
    monkeypatch.setattr("handlers.ingest.handle_ingest", _refuse)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "refused" in msg
    assert "phi" in msg


def test_handle_ingest_state_post_fetch_ingest_failure_returns_500(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, monkeypatch
):
    """Generic ingest failure post-fetch → 500 (transient default).
    Operator's environment will recover if the failure was
    transient; if not, Drive's 24h retry envelope is enough time
    to investigate."""
    reg.register(_record())

    def _fake_fetch(self, url):
        return {
            "query": "x",
            "source": "google_drive",
            "title": "x",
            "date": "",
            "participants": [],
            "decisions": [{"description": "x", "title": "x"}],
        }

    async def _fail(ctx, payload, *, source_scope, ingest_mode):
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", _fake_fetch)
    monkeypatch.setattr("handlers.ingest.handle_ingest", _fail)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 500
    assert "transient" in msg
    err = capsys.readouterr().err
    assert "simulated ledger failure" in err


def test_handle_ingest_state_registry_race_acked(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, monkeypatch
):
    """Verify passed (registry had the channel) but by the time
    _ingest_change runs, the entry is gone (operator ran drive-stop
    mid-notification). 200 ack — Drive's retry won't help."""
    reg.register(_record())
    # Verify call captures the record; subsequent registry.get
    # in _ingest_change returns None to simulate the race.
    original_get = reg.get
    call_count = [0]

    def _flaky_get(channel_id):
        call_count[0] += 1
        if call_count[0] == 1:
            return original_get(channel_id)  # verify sees the record
        return None  # _ingest_change sees the race

    monkeypatch.setattr(reg, "get", _flaky_get)

    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "registry race" in msg or "acknowledged" in msg
    err = capsys.readouterr().err
    assert "drive-stop" in err or "race" in err


def test_handle_unknown_state_returns_200_with_unknown_marker(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture
):
    """Future-proofing: Drive may add new resource_state values. We
    200 them rather than 5xx (which would cause Drive to retry the
    same unknowable state forever) and log so operators see drift."""
    reg.register(_record())
    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="future-state-not-yet-known",
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "unknown" in msg
    captured = capsys.readouterr()
    assert "unknown state" in captured.err
    assert "future-state-not-yet-known" in captured.err


def test_handle_case_insensitive_state(reg: ChannelRegistry):
    """Drive's docs show lowercase states but the parser is defensive:
    a header that arrives uppercase still routes correctly."""
    reg.register(_record())
    status, msg = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="UPDATE",
        message_number="42",
        registry=reg,
    )
    assert status == 200
    assert "update" in msg


def test_handle_body_is_ignored(reg: ChannelRegistry):
    """Drive's notification body is empty for files.watch. The handler
    pins that we don't depend on body contents — a non-empty body
    should NOT change behavior."""
    reg.register(_record())
    status1, _ = handle(
        body=b"",
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    status2, _ = handle(
        body=b'{"unexpected": "body"}',
        channel_id="ch-1",
        channel_token="tok-1",
        resource_id="res-1",
        resource_state="update",
        message_number="42",
        registry=reg,
    )
    assert status1 == status2 == 200
