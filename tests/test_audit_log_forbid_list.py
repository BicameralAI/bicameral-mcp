"""Functional tests for the audit_log forbid-list discipline (#227).

Per ``qor/references/doctrine-test-functionality.md``: each test invokes
the unit under test (``_strip_forbidden`` or ``emit``) and asserts on the
returned value or observable side-effect (captured stderr, parsed JSON).
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr

import pytest

import audit_log
from audit_log import _FORBIDDEN_FIELDS, AuditEventType, _strip_forbidden, emit


@pytest.fixture(autouse=True)
def _reset_audit_log_state(monkeypatch):
    """Force fresh logger resolution per test; default stderr channel."""
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


def test_strip_forbidden_removes_decision_text():
    cleaned, stripped = _strip_forbidden({"a": 1, "decision_text": "secret"})
    assert cleaned == {"a": 1}
    assert stripped == ["decision_text"]


def test_strip_forbidden_removes_all_listed_keys_in_one_pass():
    payload = {key: f"v_{key}" for key in _FORBIDDEN_FIELDS}
    payload["safe_field"] = 42
    cleaned, stripped = _strip_forbidden(payload)
    assert cleaned == {"safe_field": 42}
    assert set(stripped) == set(_FORBIDDEN_FIELDS)


def test_strip_forbidden_does_not_mutate_input():
    original = {"a": 1, "decision_text": "secret"}
    snapshot = dict(original)
    _strip_forbidden(original)
    assert original == snapshot


def test_strip_forbidden_returns_empty_stripped_on_clean_input():
    cleaned, stripped = _strip_forbidden({"a": 1, "b": 2})
    assert cleaned == {"a": 1, "b": 2}
    assert stripped == []


def test_forbidden_fields_includes_canonical_secret_carriers():
    expected = {
        "decision_text",
        "file_paths",
        "transcript",
        "arguments",
        "payload",
        "content",
        "text",
        "body",
        "output",
        "result_text",
    }
    assert expected.issubset(_FORBIDDEN_FIELDS)


def test_emit_strips_forbidden_and_surfaces_redaction_field():
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.TOOL_INVOCATION, decision_text="leak", a=1)
    line = buf.getvalue().strip()
    record = json.loads(line)
    assert record["a"] == 1
    assert "decision_text" not in record
    assert record["forbidden_keys_stripped"] == ["decision_text"]
