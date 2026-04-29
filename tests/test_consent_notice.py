"""Tests for consent.py (issue #39): marker, notice, telemetry_allowed."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _reload_consent():
    import importlib
    import consent
    importlib.reload(consent)
    return consent


# ── telemetry_allowed() — gating behavior ──────────────────────────────


def test_telemetry_allowed_no_marker_default_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No marker: default-on (preserves upgrade-path behavior)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    consent = _reload_consent()
    assert consent.telemetry_allowed() is True


def test_telemetry_allowed_env_off_overrides_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env BICAMERAL_TELEMETRY=0 wins even when marker says enabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "0")
    consent = _reload_consent()
    consent.write_consent(telemetry=True, via="wizard")
    assert consent.telemetry_allowed() is False


def test_telemetry_allowed_marker_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker 'disabled' suppresses relay even without env var."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    consent = _reload_consent()
    consent.write_consent(telemetry=False, via="wizard")
    assert consent.telemetry_allowed() is False


def test_telemetry_allowed_marker_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    consent = _reload_consent()
    consent.write_consent(telemetry=True, via="wizard")
    assert consent.telemetry_allowed() is True


# ── write_consent() — file shape + permissions ─────────────────────────


def test_write_consent_records_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    consent = _reload_consent()
    consent.write_consent(telemetry=True, via="wizard")

    marker = tmp_path / ".bicameral" / "consent.json"
    assert marker.exists()
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["telemetry"] == "enabled"
    assert record["acknowledged_via"] == "wizard"
    assert record["policy_version"] == consent.POLICY_VERSION
    assert "acknowledged_at" in record


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
def test_write_consent_mode_0o600_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    consent = _reload_consent()
    consent.write_consent(telemetry=True, via="wizard")
    marker = tmp_path / ".bicameral" / "consent.json"
    assert (marker.stat().st_mode & 0o777) == 0o600


# ── notify_if_first_run() — non-blocking notice ────────────────────────


def test_notice_emitted_on_first_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_SKIP_CONSENT_NOTICE", raising=False)
    consent = _reload_consent()

    mcp_send = MagicMock()
    consent.notify_if_first_run(send_mcp_notification=mcp_send)

    captured = capsys.readouterr()
    assert "Bicameral collects" in captured.err
    mcp_send.assert_called_once()
    assert mcp_send.call_args.args[0] == "info"

    marker = consent.read_consent()
    assert marker is not None
    assert marker["acknowledged_via"] == "first_boot_notice"


def test_notice_suppressed_after_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_SKIP_CONSENT_NOTICE", raising=False)
    consent = _reload_consent()
    consent.write_consent(telemetry=True, via="wizard")

    capsys.readouterr()  # reset
    consent.notify_if_first_run()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_notice_re_emitted_on_policy_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_SKIP_CONSENT_NOTICE", raising=False)
    consent = _reload_consent()

    # Simulate a stale marker (older policy version).
    (tmp_path / ".bicameral").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".bicameral" / "consent.json").write_text(
        json.dumps({"telemetry": "enabled", "policy_version": 0, "acknowledged_at": "x", "acknowledged_via": "wizard"}),
        encoding="utf-8",
    )

    consent.notify_if_first_run()
    captured = capsys.readouterr()
    assert "Bicameral collects" in captured.err
    new_marker = consent.read_consent()
    assert new_marker["policy_version"] == consent.POLICY_VERSION


def test_notice_skipped_when_env_var_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_SKIP_CONSENT_NOTICE", "1")
    consent = _reload_consent()

    consent.notify_if_first_run()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert consent.read_consent() is None


def test_notice_swallows_marker_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If marker write fails, notify_if_first_run still completes silently."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_SKIP_CONSENT_NOTICE", raising=False)
    consent = _reload_consent()
    monkeypatch.setattr(consent, "write_consent", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    # Must not raise.
    consent.notify_if_first_run()


def test_telemetry_send_event_blocked_when_consent_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """telemetry.send_event suppresses relay when consent says disabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    consent = _reload_consent()
    consent.write_consent(telemetry=False, via="wizard")

    import importlib
    import telemetry
    importlib.reload(telemetry)

    # Patch the network path; if relay was attempted, this would be called.
    sent = []
    monkeypatch.setattr(telemetry, "_send_bg", lambda payload: sent.append(payload))
    telemetry.send_event("0.13.3", skill="bicameral-ingest", duration_ms=100)
    # Counter should still increment locally.
    import local_counters
    importlib.reload(local_counters)
    # Relay was NOT called (consent denied).
    assert sent == []
