"""Tests for Jira webhook status-transition heuristics (#337 Jira Phase C).

Two layers:

* **Solitary** unit tests for the two pure helpers — ``_status_transitions``
  (the changelog parser) and ``load_terminal_statuses`` (the config loader).
  Solitary is correct here: both are pure functions / a pure file read, with
  no collaborator we ship to the agent (CLAUDE.md "solitary is correct for
  pure helpers").
* **Sociable** receiver tests that drive the real ``handle`` — real
  ``verify_signature``, real ``DeliveryDedupCache``, real
  ``normalize_issue_to_payload``, real ``_append_status_transition_decisions``,
  and the real ``load_terminal_statuses`` reading a real temp ``config.yaml``.
  The only seam is ``handle_ingest`` — the genuine ledger boundary — exactly
  as the Phase B harness (``test_webhooks_jira.py``) documents and does; the
  observable Phase C behaviour is the *payload* that reaches ``handle_ingest``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from sources.jira.transition_config import load_terminal_statuses
from webhooks.jira import _status_transitions, handle

_SECRET = "jira-webhook-shared-secret"


@pytest.fixture(autouse=True)
def _reset_dedup():
    """Reset the process-local dedup singleton between tests."""
    from webhooks.dedup import _reset_for_tests as _dedup_reset

    _dedup_reset()
    yield
    _dedup_reset()


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()


# ── Solitary: _status_transitions (the changelog parser) ────────────────────


def test_status_transitions_extracts_status_item():
    changelog = {
        "id": "1001",
        "items": [
            {"field": "assignee", "fromString": "Ada", "toString": "Grace"},
            {"field": "status", "fromString": "In Progress", "toString": "Done"},
        ],
    }
    assert _status_transitions(changelog) == [("In Progress", "Done")]


def test_status_transitions_preserves_item_order():
    changelog = {
        "items": [
            {"field": "status", "fromString": "Open", "toString": "In Review"},
            {"field": "status", "fromString": "In Review", "toString": "Done"},
        ]
    }
    assert _status_transitions(changelog) == [("Open", "In Review"), ("In Review", "Done")]


def test_status_transitions_missing_fromstring_yields_empty_str():
    changelog = {"items": [{"field": "status", "toString": "Done"}]}
    assert _status_transitions(changelog) == [("", "Done")]


@pytest.mark.parametrize(
    "changelog",
    [None, "not-a-dict", 42, {}, {"items": "not-a-list"}, {"items": None}],
)
def test_status_transitions_malformed_changelog_yields_empty(changelog):
    """A malformed/absent changelog yields no transition — never raises."""
    assert _status_transitions(changelog) == []


def test_status_transitions_skips_non_status_and_non_dict_items():
    changelog = {"items": [{"field": "priority", "toString": "High"}, "junk", 7, None]}
    assert _status_transitions(changelog) == []


# ── Solitary: load_terminal_statuses (the config loader) ────────────────────


def _write_config(tmp_path, text: str):
    cfg_dir = tmp_path / ".bicameral"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(text, encoding="utf-8")


def test_load_terminal_statuses_valid_config(tmp_path):
    _write_config(
        tmp_path,
        "jira:\n  status_transitions:\n    PROJ: [Done, Released]\n    OPS: [Closed]\n",
    )
    result = load_terminal_statuses(repo_path=str(tmp_path))
    assert result == {"proj": frozenset({"done", "released"}), "ops": frozenset({"closed"})}


def test_load_terminal_statuses_casefolds_keys_and_statuses(tmp_path):
    """Operator YAML casing must never silently miss — keys + statuses casefold."""
    _write_config(tmp_path, "jira:\n  status_transitions:\n    Proj: ['DONE']\n")
    result = load_terminal_statuses(repo_path=str(tmp_path))
    assert result == {"proj": frozenset({"done"})}


def test_load_terminal_statuses_missing_file_returns_empty(tmp_path):
    assert load_terminal_statuses(repo_path=str(tmp_path)) == {}


@pytest.mark.parametrize(
    "text",
    [
        "jira: not-a-mapping",  # jira not a dict
        "jira:\n  status_transitions: not-a-mapping",  # transitions not a dict
        "not-a-mapping-at-all",  # top level not a dict
        "{ broken: [unclosed",  # not valid YAML
        "telemetry: true",  # no jira section
    ],
)
def test_load_terminal_statuses_malformed_config_fails_closed(tmp_path, text):
    """Every malformed shape fails closed to {} — never crashes, never
    over-ingests."""
    _write_config(tmp_path, text)
    assert load_terminal_statuses(repo_path=str(tmp_path)) == {}


def test_load_terminal_statuses_skips_bad_entry_keeps_good(tmp_path):
    """A project whose value is not a list is skipped; valid siblings survive."""
    _write_config(
        tmp_path,
        "jira:\n  status_transitions:\n    GOOD: [Done]\n    BAD: 123\n",
    )
    assert load_terminal_statuses(repo_path=str(tmp_path)) == {"good": frozenset({"done"})}


# ── Sociable: handle() → transition decision in the ingest payload ──────────


def _issue_updated_body(*, issue_key="PROJ-123", to_status="Done", from_status="In Progress"):
    """A signed-ready jira:issue_updated body with a changelog status item."""
    payload = {
        "timestamp": 1716285600000,
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": issue_key,
            "fields": {
                "summary": "Ship the Jira integration",
                "updated": "2026-05-21T12:00:00.000+0000",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Body."}]}
                    ],
                },
            },
        },
        "changelog": {
            "items": [{"field": "status", "fromString": from_status, "toString": to_status}]
        },
    }
    return json.dumps(payload).encode("utf-8")


def _capture_handle(monkeypatch, tmp_path, config_text: str) -> dict:
    """Wire a real temp config + a capturing handle_ingest seam.

    Returns a dict that ``["payload"]`` is filled into once handle_ingest is
    reached. ``handle_ingest`` is the documented genuine ledger boundary —
    seamed exactly as the Phase B receiver tests seam it.
    """
    _write_config(tmp_path, config_text)
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    captured: dict = {}

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        captured["payload"] = payload

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)
    monkeypatch.setattr(
        "context.BicameralContext.from_env", classmethod(lambda cls: SimpleNamespace())
    )
    return captured


def _transition_decisions(payload: dict) -> list[dict]:
    return [d for d in payload["decisions"] if d["title"].endswith("#status-Done")]


def test_handle_configured_transition_appends_decision(monkeypatch, tmp_path):
    """A status transition into a configured terminal status appends one
    transition decision — alongside the Phase B description decision."""
    captured = _capture_handle(
        monkeypatch, tmp_path, "jira:\n  status_transitions:\n    PROJ: [Done]\n"
    )
    body = _issue_updated_body()
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-c1",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    payload = captured["payload"]
    transition = _transition_decisions(payload)
    assert len(transition) == 1
    assert transition[0]["title"] == "PROJ-123#status-Done"
    assert "transitioned: In Progress -> Done" in transition[0]["description"]
    # Phase B's description decision is still present — Phase C is additive.
    assert any(d["title"] == "PROJ-123" for d in payload["decisions"])


def test_handle_non_terminal_transition_appends_nothing(monkeypatch, tmp_path):
    """A transition into a status NOT in the configured terminal set adds no
    transition decision (Phase B description decision only)."""
    captured = _capture_handle(
        monkeypatch, tmp_path, "jira:\n  status_transitions:\n    PROJ: [Released]\n"
    )
    body = _issue_updated_body(to_status="Done")
    handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-c2",
        secret_resolver=lambda: _SECRET,
    )
    assert _transition_decisions(captured["payload"]) == []


def test_handle_unconfigured_project_appends_nothing(monkeypatch, tmp_path):
    """An issue whose project key is not in the allowlist map gets no
    transition decision — the map keys are the allowlist."""
    captured = _capture_handle(
        monkeypatch, tmp_path, "jira:\n  status_transitions:\n    OTHER: [Done]\n"
    )
    body = _issue_updated_body(issue_key="PROJ-7")
    handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-c3",
        secret_resolver=lambda: _SECRET,
    )
    assert _transition_decisions(captured["payload"]) == []


def test_handle_no_config_is_pure_phase_b(monkeypatch, tmp_path):
    """With no jira config at all, behaviour is exactly Phase B — the issue
    still ingests (description decision), no transition decision."""
    captured = _capture_handle(monkeypatch, tmp_path, "telemetry: true\n")
    body = _issue_updated_body()
    status, _ = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-c4",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    assert _transition_decisions(captured["payload"]) == []
    assert any(d["title"] == "PROJ-123" for d in captured["payload"]["decisions"])
