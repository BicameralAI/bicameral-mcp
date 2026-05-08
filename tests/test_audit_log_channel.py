"""Functional tests for the audit_log channel resolution + level filter (#227)."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path

import pytest

import audit_log
from audit_log import (
    _LEVEL_RANK,
    AuditEventType,
    _resolve_channel,
    _resolve_min_level_rank,
    emit,
)


@pytest.fixture(autouse=True)
def _reset_audit_log_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


def test_resolve_channel_default_is_stderr():
    assert _resolve_channel() == ("stderr", "")


def test_resolve_channel_explicit_stderr_string(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", "stderr")
    assert _resolve_channel() == ("stderr", "")


def test_resolve_channel_disabled_string(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", "disabled")
    assert _resolve_channel() == ("disabled", "")


def test_resolve_channel_path_string(monkeypatch, tmp_path):
    target = str(tmp_path / "audit.log")
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", target)
    assert _resolve_channel() == ("file", target)


def test_emit_disabled_channel_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", "disabled")
    audit_log._reset_for_tests()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.SERVER_START, message="boot")
    assert buf.getvalue() == ""
    assert not list(tmp_path.iterdir())


def test_emit_file_channel_writes_to_file(monkeypatch, tmp_path):
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", str(target))
    audit_log._reset_for_tests()
    emit(AuditEventType.SERVER_START, message="boot", version="0.13.3")
    contents = target.read_text(encoding="utf-8").strip()
    parsed = json.loads(contents)
    assert parsed["event_type"] == "server_start"
    assert parsed["version"] == "0.13.3"


def test_emit_file_channel_unwriteable_falls_back_to_stderr_with_warning(monkeypatch, tmp_path):
    bad_path = str(tmp_path / "no-such-dir" / "audit.jsonl")
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", bad_path)
    audit_log._reset_for_tests()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.SERVER_START, message="boot")
    output = buf.getvalue()
    # Two stderr lines: the unwriteable warning, then the actual record.
    lines = [ln for ln in output.splitlines() if ln.strip()]
    assert any("file path unwriteable" in ln for ln in lines)
    assert any('"event_type": "server_start"' in ln for ln in lines)
    assert not Path(bad_path).exists()


def test_resolve_min_level_rank_default_is_info():
    if "BICAMERAL_AUDIT_LOG_LEVEL" in os.environ:
        del os.environ["BICAMERAL_AUDIT_LOG_LEVEL"]
    audit_log._reset_for_tests()
    assert _resolve_min_level_rank() == _LEVEL_RANK["info"]
