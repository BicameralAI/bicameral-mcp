"""Tests for #337 Phase 4a — Slack active-ingest adapter."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _mock_response(body):
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = 200

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,channel,thread_ts",
    [
        (
            "https://acme.slack.com/archives/C12345ABC/p1700000000123456",
            "C12345ABC",
            "1700000000.123456",
        ),
        (
            "https://acme.slack.com/archives/C12345ABC/p1700000000999999?thread_ts=1699999999.000001",
            "C12345ABC",
            "1699999999.000001",
        ),
        (
            "https://acme.slack.com/archives/c12345abc/p1700000000123456",
            "C12345ABC",
            "1700000000.123456",
        ),
    ],
)
def test_parse_slack_url_accepts_valid(url, channel, thread_ts):
    from sources.slack.adapter import parse_slack_url

    parsed = parse_slack_url(url)
    assert parsed.channel == channel
    assert parsed.thread_ts == thread_ts


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://acme.slack.com/messages/D12345ABC/p1700000000123456",  # DM
        "https://acme.slack.com/archives/",
        "https://acme.slack.com/archives/C12345/p12345",  # ts too short
        "",
    ],
)
def test_parse_slack_url_rejects_invalid(url):
    from sources.slack.adapter import parse_slack_url

    with pytest.raises(ValueError):
        parse_slack_url(url)


# ── Normalization ───────────────────────────────────────────────────────────


def test_normalize_thread_filters_bot_and_join_messages():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [
        {"ts": "1700000000.000001", "user": "U1", "text": "Real decision"},
        {
            "ts": "1700000001.000001",
            "user": "USLACKBOT",
            "text": "set channel topic",
            "subtype": "channel_topic",
        },
        {
            "ts": "1700000002.000001",
            "user": "U2",
            "text": "bot autoresponse",
            "subtype": "bot_message",
        },
        {"ts": "1700000003.000001", "user": "U2", "text": "Counter-point"},
    ]
    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000000001",
    )
    assert payload["source"] == "slack"
    assert len(payload["decisions"]) == 2  # the two human messages
    assert "Real decision" in payload["decisions"][0]["description"]
    assert "Counter-point" in payload["decisions"][1]["description"]


def test_normalize_thread_dedups_participants():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [
        {"ts": "1700000000.000001", "user": "U1", "text": "first"},
        {"ts": "1700000001.000001", "user": "U1", "text": "second from same user"},
        {"ts": "1700000002.000001", "user": "U2", "text": "third"},
    ]
    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000000001",
    )
    assert payload["participants"] == ["U1", "U2"]


def test_normalize_thread_resolves_user_to_email_when_resolver_supplied():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [
        {"ts": "1700000000.000001", "user": "U1", "text": "decision"},
    ]
    resolver = MagicMock(
        return_value={
            "real_name": "Alice",
            "profile": {"email": "alice@example.com"},
        }
    )
    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000000001",
        user_resolver=resolver,
    )
    assert payload["participants"] == ["alice@example.com"]


def test_normalize_thread_falls_back_to_name_when_no_email():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [{"ts": "1700000000.000001", "user": "U1", "text": "x"}]
    resolver = MagicMock(return_value={"real_name": "Alice", "profile": {}})
    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000000001",
        user_resolver=resolver,
    )
    assert payload["participants"] == ["Alice"]


def test_normalize_thread_falls_back_to_user_id_on_resolver_failure():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [{"ts": "1700000000.000001", "user": "U1", "text": "x"}]

    def _failing(_):
        raise RuntimeError("transient")

    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000000001",
        user_resolver=_failing,
    )
    assert payload["participants"] == ["U1"]


def test_normalize_thread_date_uses_root_ts_iso():
    from sources.slack.adapter import normalize_thread_to_payload

    messages = [{"ts": "1700000000.123456", "user": "U1", "text": "x"}]
    payload = normalize_thread_to_payload(
        messages,
        channel="C12345ABC",
        thread_url="https://acme.slack.com/archives/C12345ABC/p1700000000123456",
    )
    # 1700000000 epoch == 2023-11-14T22:13:20Z. Just assert the ISO shape +
    # that the date field is non-empty.
    assert payload["date"].startswith("2023-")


# ── Client + Adapter integration ────────────────────────────────────────────


def test_client_raises_on_ok_false():
    from sources.slack.client import SlackAPIError, _get

    with patch(
        "urllib.request.urlopen",
        return_value=_mock_response({"ok": False, "error": "invalid_auth"}),
    ):
        with pytest.raises(SlackAPIError) as exc_info:
            _get(token="bad", method="conversations.replies")
    assert exc_info.value.slack_error == "invalid_auth"


def test_client_get_thread_replies_paginates():
    from sources.slack.client import get_thread_replies

    page1 = {
        "ok": True,
        "messages": [{"ts": "1700000000.000001", "user": "U1", "text": "a"}],
        "response_metadata": {"next_cursor": "cursor-1"},
    }
    page2 = {
        "ok": True,
        "messages": [{"ts": "1700000001.000001", "user": "U1", "text": "b"}],
        "response_metadata": {"next_cursor": ""},
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[_mock_response(page1), _mock_response(page2)],
    ):
        msgs = get_thread_replies(token="xoxb-t", channel="C1", thread_ts="1700000000.000001")
    assert len(msgs) == 2


def test_adapter_can_handle_url():
    from sources.slack.adapter import SlackAdapter

    a = SlackAdapter()
    assert a.can_handle_url("https://acme.slack.com/archives/C12345ABC/p1700000000123456")
    assert not a.can_handle_url("https://github.com/foo/bar")
    assert not a.can_handle_url("https://acme.slack.com/messages/D12345/p1700000000123456")


def test_adapter_fetch_active_round_trip(monkeypatch):
    from secrets_store import put_secret
    from sources.slack.adapter import SlackAdapter

    put_secret(source_id="slack", key="api_key", value="xoxb-test")

    thread_replies = {
        "ok": True,
        "messages": [
            {"ts": "1700000000.000001", "user": "U1", "text": "Decision A"},
            {"ts": "1700000001.000001", "user": "U2", "text": "Counter B"},
        ],
        "response_metadata": {"next_cursor": ""},
    }
    user_u1 = {
        "ok": True,
        "user": {"real_name": "Alice", "profile": {"email": "alice@example.com"}},
    }
    user_u2 = {
        "ok": True,
        "user": {"real_name": "Bob", "profile": {"email": "bob@example.com"}},
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_response(thread_replies),
            _mock_response(user_u1),
            _mock_response(user_u2),
        ],
    ):
        adapter = SlackAdapter()
        result = adapter.fetch_active("https://acme.slack.com/archives/C12345ABC/p1700000000000001")

    assert result["source"] == "slack"
    assert len(result["decisions"]) == 2
    assert result["participants"] == ["alice@example.com", "bob@example.com"]


def test_adapter_raises_when_token_missing():
    from sources.slack.adapter import SlackAdapter

    a = SlackAdapter()
    with pytest.raises(RuntimeError, match="bot token not configured"):
        a.fetch_active("https://acme.slack.com/archives/C12345ABC/p1700000000000001")


def test_adapter_raises_on_empty_thread(monkeypatch):
    from secrets_store import put_secret
    from sources.slack.adapter import SlackAdapter

    put_secret(source_id="slack", key="api_key", value="xoxb-test")

    with patch(
        "urllib.request.urlopen",
        return_value=_mock_response(
            {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}
        ),
    ):
        adapter = SlackAdapter()
        with pytest.raises(RuntimeError, match="no messages"):
            adapter.fetch_active("https://acme.slack.com/archives/C12345ABC/p1700000000000001")
