"""Tests for the Slack Events API webhook handler.

Coverage:
- verify_signature: missing timestamp/signature/secret, stale timestamp,
  non-integer timestamp, malformed signature prefix, mismatch, success,
  constant-time comparison pin, body-byte sensitivity
- handle: missing signature → 401, invalid JSON → 400, url_verification
  echoes challenge, event_callback message → ingest, bot/topic messages
  → ignored, dedup by event_id, hard-gate refusal → 422
- Server route: Slack URL-verification handshake returns
  application/json with the challenge bytes echoed verbatim
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from webhooks.slack import WebhookVerificationError, handle, verify_signature


@pytest.fixture(autouse=True)
def _disable_keyring_and_reset_dedup(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests as _secrets_reset
    from webhooks.dedup import _reset_for_tests as _dedup_reset

    _secrets_reset()
    _dedup_reset()
    yield
    _secrets_reset()
    _dedup_reset()


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    """Compute the v0 signature Slack would send for ``body`` at ``timestamp``."""
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    return "v0=" + hmac.new(secret.encode("utf-8"), msg=base, digestmod=hashlib.sha256).hexdigest()


# ── verify_signature ────────────────────────────────────────────────────────


def test_verify_signature_success():
    body = b'{"ok":true}'
    secret = "s3cr3t"
    ts = str(int(time.time()))
    sig = _sign(secret, ts, body)
    verify_signature(
        body=body,
        timestamp_header=ts,
        signature_header=sig,
        secret=secret,
    )  # does not raise


def test_verify_signature_missing_timestamp_raises():
    with pytest.raises(WebhookVerificationError, match="Timestamp"):
        verify_signature(
            body=b"x",
            timestamp_header=None,
            signature_header="v0=abc",
            secret="s",
        )


def test_verify_signature_missing_signature_raises():
    with pytest.raises(WebhookVerificationError, match="Signature"):
        verify_signature(
            body=b"x",
            timestamp_header="1700000000",
            signature_header=None,
            secret="s",
        )


def test_verify_signature_missing_secret_raises():
    with pytest.raises(WebhookVerificationError, match="webhook_secret"):
        verify_signature(
            body=b"x",
            timestamp_header="1700000000",
            signature_header="v0=abc",
            secret="",
        )


def test_verify_signature_stale_timestamp_rejected():
    """Replay defense: anything more than 5 minutes off is rejected
    BEFORE the HMAC is even computed — protects against constant-time
    probing against very old timestamps."""
    secret = "s"
    body = b"x"
    stale_ts = "1700000000"  # 2023; definitely > 5 min from "now"
    sig = _sign(secret, stale_ts, body)
    with pytest.raises(WebhookVerificationError, match="stale"):
        verify_signature(
            body=body,
            timestamp_header=stale_ts,
            signature_header=sig,
            secret=secret,
            now=time.time(),  # use real current time
        )


def test_verify_signature_future_timestamp_rejected():
    """Defense in depth: a clock-skew attacker submitting a timestamp
    far in the future is also rejected (same 5-min gate)."""
    secret = "s"
    body = b"x"
    future_ts = str(int(time.time()) + 3600)  # 1 hour ahead
    sig = _sign(secret, future_ts, body)
    with pytest.raises(WebhookVerificationError, match="stale"):
        verify_signature(
            body=body,
            timestamp_header=future_ts,
            signature_header=sig,
            secret=secret,
        )


def test_verify_signature_non_integer_timestamp_rejected():
    with pytest.raises(WebhookVerificationError, match="decimal"):
        verify_signature(
            body=b"x",
            timestamp_header="not-a-number",
            signature_header="v0=abc",
            secret="s",
        )


def test_verify_signature_malformed_prefix_rejected():
    """A future Slack version might ship v1 / v2 — for now we reject
    anything not v0=. Documented intentional friction."""
    ts = str(int(time.time()))
    with pytest.raises(WebhookVerificationError, match="malformed"):
        verify_signature(
            body=b"x",
            timestamp_header=ts,
            signature_header="v1=" + "0" * 64,
            secret="s",
        )


def test_verify_signature_mismatch_rejected():
    secret = "right"
    body = b"x"
    ts = str(int(time.time()))
    sig = _sign(secret, ts, body)
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(
            body=body,
            timestamp_header=ts,
            signature_header=sig,
            secret="wrong",
        )


def test_verify_signature_uses_compare_digest():
    import hmac as _hmac

    secret = "s"
    body = b"x"
    ts = str(int(time.time()))
    sig = _sign(secret, ts, body)
    with patch.object(_hmac, "compare_digest", wraps=_hmac.compare_digest) as spy:
        verify_signature(
            body=body,
            timestamp_header=ts,
            signature_header=sig,
            secret=secret,
        )
    assert spy.called


def test_verify_signature_body_byte_sensitive():
    """Even a single trailing space in the body invalidates the signature."""
    secret = "s"
    body = b'{"a":1}'
    ts = str(int(time.time()))
    sig = _sign(secret, ts, body)
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(
            body=b'{"a":1} ',  # one extra byte
            timestamp_header=ts,
            signature_header=sig,
            secret=secret,
        )


def test_verify_signature_includes_timestamp_in_basestring():
    """Slack's basestring is ``v0:{ts}:{body}`` — different ts must
    produce different signature even with the same body. Pins against
    accidentally signing only the body."""
    secret = "s"
    body = b"x"
    ts_a = str(int(time.time()))
    ts_b = str(int(time.time()) + 1)
    sig_a = _sign(secret, ts_a, body)
    # Same signature with the wrong timestamp must fail.
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(
            body=body,
            timestamp_header=ts_b,
            signature_header=sig_a,
            secret=secret,
        )


# ── handle: dispatch ────────────────────────────────────────────────────────


def _fresh_ts() -> str:
    return str(int(time.time()))


def test_handle_url_verification_echoes_challenge():
    """The handshake response must echo the challenge field verbatim."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode("utf-8")
    ts = _fresh_ts()
    status, response, content_type = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 200
    assert content_type == "application/json"
    parsed = json.loads(response)
    assert parsed["challenge"] == "abc123"


def test_handle_url_verification_missing_challenge_rejected():
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps({"type": "url_verification"}).encode("utf-8")
    ts = _fresh_ts()
    status, _, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 400


def test_handle_missing_signature_returns_401():
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    status, _, _ = handle(
        body=b"{}",
        timestamp_header=_fresh_ts(),
        signature_header=None,
    )
    assert status == 401


def test_handle_invalid_json_returns_400():
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = b"not json"
    ts = _fresh_ts()
    status, _, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 400


def test_handle_message_event_ingests():
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev01",
            "event": {
                "type": "message",
                "channel": "C01A",
                "ts": "1700000000.000001",
                "user": "U1",
                "text": "decided to ship",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()

    async def _fake_ingest(*args, **kwargs):
        return None

    with (
        patch("handlers.ingest.handle_ingest", new=_fake_ingest),
        patch("context.BicameralContext.from_env", return_value=MagicMock()),
    ):
        status, msg, _ = handle(
            body=body,
            timestamp_header=ts,
            signature_header=_sign("s", ts, body),
        )
    assert status == 200
    assert "ingested" in msg


def test_handle_thread_reply_ignored():
    """thread_ts present and different from ts → reply, skip (matches the
    polling adapter's behavior)."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev02",
            "event": {
                "type": "message",
                "channel": "C01A",
                "ts": "1700000002.000001",
                "user": "U1",
                "text": "reply text",
                "thread_ts": "1700000001.000001",  # different from ts → reply
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    status, msg, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 200
    assert "thread reply" in msg


def test_handle_bot_message_subtype_ignored():
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev03",
            "event": {
                "type": "message",
                "channel": "C01A",
                "ts": "1700000000.000001",
                "user": "USLACKBOT",
                "text": "channel topic was set",
                "subtype": "channel_topic",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    status, msg, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 200
    assert "decision-bearing" in msg


def test_handle_dedup_on_event_id():
    """Slack retries failed deliveries — second arrival with same
    event_id must not re-ingest."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-dup",
            "event": {
                "type": "message",
                "channel": "C01A",
                "ts": "1700000000.000001",
                "user": "U1",
                "text": "decided",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    sig = _sign("s", ts, body)

    async def _fake_ingest(*args, **kwargs):
        return None

    with (
        patch("handlers.ingest.handle_ingest", new=_fake_ingest),
        patch("context.BicameralContext.from_env", return_value=MagicMock()),
    ):
        status1, _, _ = handle(body=body, timestamp_header=ts, signature_header=sig)
        status2, msg2, _ = handle(body=body, timestamp_header=ts, signature_header=sig)
    assert status1 == 200
    assert status2 == 200
    assert "duplicate" in msg2


def test_handle_hard_gate_refusal_returns_422():
    """_IngestRefused → 422 not 500. Slack retries 5xx 3× over 1 hour;
    we don't want to re-process payloads with secret/PHI in them."""
    from handlers.ingest import _IngestRefused
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-secret",
            "event": {
                "type": "message",
                "channel": "C01A",
                "ts": "1700000000.000001",
                "user": "U1",
                "text": "AKIAIOSFODNN7EXAMPLE",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()

    async def _refuse(*args, **kwargs):
        raise _IngestRefused("sensitive_data:secret")

    with (
        patch("handlers.ingest.handle_ingest", new=_refuse),
        patch("context.BicameralContext.from_env", return_value=MagicMock()),
    ):
        status, msg, _ = handle(
            body=body,
            timestamp_header=ts,
            signature_header=_sign("s", ts, body),
        )
    assert status == 422
    assert "refused" in msg.lower()


def test_handle_event_callback_without_event_id_rejected():
    """B1 review finding: event_callback envelope missing event_id is
    rejected with 400. Absence would skip the dedup cache entirely,
    enabling unbounded replay within the 5-min timestamp window."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            # NO event_id
            "event": {"type": "message", "channel": "C01A", "ts": "1.0", "user": "U", "text": "x"},
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    status, msg, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 400
    assert "event_id" in msg


def test_handle_dm_channel_type_rejected():
    """H2 review finding: channel_type 'im' (DM) ignored per the
    channel-only policy that already applies to the polling adapter."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-dm",
            "event": {
                "type": "message",
                "channel": "C01A",
                "channel_type": "im",
                "ts": "1.0",
                "user": "U",
                "text": "private chat",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    status, msg, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 200
    assert "DM" in msg


def test_handle_d_prefix_channel_id_rejected():
    """H2 review finding: defense-in-depth — even if channel_type is
    missing or wrong, a D-prefix channel ID is rejected."""
    from secrets_store import put_secret

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-dprefix",
            "event": {
                "type": "message",
                "channel": "D01XYZ",  # D-prefix → DM
                "ts": "1.0",
                "user": "U",
                "text": "x",
            },
        }
    ).encode("utf-8")
    ts = _fresh_ts()
    status, msg, _ = handle(
        body=body,
        timestamp_header=ts,
        signature_header=_sign("s", ts, body),
    )
    assert status == 200
    assert "DM" in msg


def test_handle_single_message_normalize_produces_non_empty_decisions():
    """M2 review finding: regression pin against future adapter
    refactors that would make single-message normalize produce empty
    decisions (which would land a junk row in the ledger)."""
    from secrets_store import put_secret
    from sources.slack.adapter import normalize_thread_to_payload

    put_secret(source_id="slack", key="webhook_secret", value="s")

    event = {
        "type": "message",
        "channel": "C01A",
        "ts": "1700000000.000001",
        "user": "U1",
        "text": "real decision text",
    }
    # Direct call to the normalize function as the webhook uses it.
    payload = normalize_thread_to_payload(
        [event],
        channel="C01A",
        thread_url="slack://C01A/1700000000.000001",
    )
    assert len(payload.get("decisions") or []) >= 1
    assert payload["decisions"][0]["description"] == "real decision text"


def test_verify_signature_rejects_timestamp_with_leading_plus():
    """M1 review finding: strict digit-only timestamp parse rejects
    `+1700000000` and similar non-canonical forms before int()."""
    secret = "s"
    body = b"x"
    bad_ts = "+1700000000"  # int() would accept, isdigit() rejects
    sig = _sign(secret, bad_ts, body)
    with pytest.raises(WebhookVerificationError, match="strict decimal"):
        verify_signature(
            body=body,
            timestamp_header=bad_ts,
            signature_header=sig,
            secret=secret,
        )


def test_verify_signature_rejects_timestamp_with_whitespace():
    """M1 corollary: leading/trailing whitespace rejected."""
    secret = "s"
    body = b"x"
    bad_ts = " 1700000000 "
    sig = _sign(secret, bad_ts.strip(), body)
    with pytest.raises(WebhookVerificationError, match="strict decimal"):
        verify_signature(
            body=body,
            timestamp_header=bad_ts,
            signature_header=sig,
            secret=secret,
        )


def test_handle_dedup_only_after_verification():
    """Bogus signed payloads must NOT pollute the dedup cache —
    same defense as GitHub."""
    from secrets_store import put_secret
    from webhooks.dedup import get_dedup_cache

    put_secret(source_id="slack", key="webhook_secret", value="real")
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-attack",
            "event": {"type": "message", "channel": "C01A", "ts": "1.0", "user": "U", "text": "x"},
        }
    ).encode("utf-8")
    ts = _fresh_ts()

    # Attacker signs with wrong secret.
    bogus_sig = _sign("attacker", ts, body)
    handle(body=body, timestamp_header=ts, signature_header=bogus_sig)
    assert get_dedup_cache().is_duplicate("slack", "Ev-attack") is False


# ── Server route (HTTP layer) ───────────────────────────────────────────────


def test_server_routes_slack_handshake_as_json():
    """End-to-end through the asyncio server: URL verification returns
    application/json with the challenge bytes."""
    from secrets_store import put_secret
    from webhooks import server as ws

    put_secret(source_id="slack", key="webhook_secret", value="s")
    body = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode("utf-8")
    ts = _fresh_ts()
    sig = _sign("s", ts, body)
    raw = (
        b"POST /webhooks/slack HTTP/1.1\r\n"
        + f"X-Slack-Request-Timestamp: {ts}\r\n".encode("ascii")
        + f"X-Slack-Signature: {sig}\r\n".encode("ascii")
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )

    class _Buf:
        def __init__(self, data):
            self._in = io.BytesIO(data)
            self.out = bytearray()

        async def readline(self):
            return self._in.readline()

        async def readexactly(self, n):
            chunk = self._in.read(n)
            if len(chunk) < n:
                raise asyncio.IncompleteReadError(chunk, n)
            return chunk

        def write(self, d):
            self.out.extend(d)

        async def drain(self):
            return None

        def close(self):
            pass

        async def wait_closed(self):
            return None

    ws._request_semaphore = None  # reset
    buf = _Buf(raw)
    asyncio.run(ws.handle_client(buf, buf))

    out = bytes(buf.out)
    assert b"200 OK" in out
    assert b"Content-Type: application/json" in out
    # Body bytes contain the challenge JSON.
    body_idx = out.index(b"\r\n\r\n") + 4
    body_bytes = out[body_idx:]
    parsed = json.loads(body_bytes)
    assert parsed["challenge"] == "xyz"
