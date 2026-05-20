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
def _reset_singleton():
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


@pytest.mark.parametrize(
    "state",
    ["add", "remove", "update", "trash", "untrash", "change"],
)
def test_handle_known_states_return_200_with_dirty_marker(
    reg: ChannelRegistry, capsys: pytest.CaptureFixture, state: str
):
    reg.register(_record())
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
    assert state in msg
    captured = capsys.readouterr()
    assert "deferred to cycle 9b" in captured.err
    assert state in captured.err


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
