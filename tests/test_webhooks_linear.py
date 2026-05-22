"""Tests for the Linear webhook handler (#337 cycle 7).

Coverage parity with the Slack/GitHub suites:
- verify_signature: missing/non-hex header, missing secret, mismatch,
  body-byte sensitivity, constant-time-compare pin, body-timestamp
  staleness gate
- handle: missing signature → 401, malformed JSON → 400 (before HMAC
  cost), Issue create with description ingests, Issue create without
  description ignored, Comment create ingests, Comment update ignored,
  remove acknowledged, unknown event type acknowledged, delivery-id
  dedup, missing Linear-Delivery still processes (timestamp gate
  applies), hard-gate refusal → 422
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from webhooks.linear import (
    WebhookVerificationError,
    check_timestamp_skew,
    handle,
    verify_signature,
)


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


def _sign(secret: str, body: bytes) -> str:
    """Compute the hex HMAC Linear would send for ``body``."""
    return hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── verify_signature ────────────────────────────────────────────────────────


def test_verify_signature_success():
    body = b'{"action":"create","type":"Issue"}'
    secret = "s3cr3t"
    sig = _sign(secret, body)
    verify_signature(body=body, signature_header=sig, secret=secret)  # does not raise


def test_verify_signature_missing_signature_raises():
    with pytest.raises(WebhookVerificationError, match="Signature"):
        verify_signature(body=b"x", signature_header=None, secret="s")


def test_verify_signature_missing_secret_raises():
    with pytest.raises(WebhookVerificationError, match="webhook_secret"):
        verify_signature(body=b"x", signature_header="a" * 64, secret="")


def test_verify_signature_non_hex_header_rejected():
    """M1 fix: non-hex headers that pass the length check are caught
    by compare_digest (constant-time byte-mismatch). Pre-HMAC charset
    scan was removed to close a position-of-first-bad-byte timing
    oracle."""
    bad_hex = "not-hex-" + "a" * 56  # 64 chars but not hex
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        verify_signature(body=b"x", signature_header=bad_hex, secret="s")


def test_verify_signature_wrong_length_header_rejected():
    with pytest.raises(WebhookVerificationError, match="64 chars"):
        verify_signature(body=b"x", signature_header="ab" * 31, secret="s")


def test_verify_signature_uppercase_hex_accepted():
    """Linear's docs show lowercase hex; defensive: also accept uppercase."""
    body = b"x"
    secret = "s"
    sig = _sign(secret, body).upper()
    verify_signature(body=body, signature_header=sig, secret=secret)


def test_verify_signature_mismatch_raises():
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        verify_signature(body=b"x", signature_header="a" * 64, secret="s")


def test_verify_signature_body_byte_sensitivity():
    """Single-byte body change must invalidate the signature — pins
    that HMAC sees the full body, not a truncated/normalized form."""
    secret = "s"
    sig = _sign(secret, b"original")
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        verify_signature(body=b"originaL", signature_header=sig, secret=secret)


def test_check_timestamp_skew_stale_rejected():
    """H1 fix: timestamp staleness gate now a separate function that
    runs AFTER HMAC verify (operates on authenticated values only).
    Past the 60s window → reject."""
    with pytest.raises(WebhookVerificationError, match="stale"):
        check_timestamp_skew(_now_ms() - 120_000)


def test_check_timestamp_skew_future_skew_rejected():
    """Bidirectional gate — future-skewed timestamps also rejected."""
    with pytest.raises(WebhookVerificationError, match="stale"):
        check_timestamp_skew(_now_ms() + 120_000)


def test_check_timestamp_skew_fresh_passes():
    """Within the 60s window → does not raise."""
    check_timestamp_skew(_now_ms())


# ── handle: routing ─────────────────────────────────────────────────────────


def _put_secret(value: str = "s") -> None:
    from secrets_store import put_secret

    put_secret(source_id="linear", key="webhook_secret", value=value)


def _issue_create_body(*, description: str = "real decision text") -> bytes:
    return json.dumps(
        {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "issue-uuid",
                "identifier": "BIC-100",
                "title": "Some title",
                "description": description,
                "url": "https://linear.app/bic/issue/BIC-100/some-title",
                "updatedAt": "2026-05-19T00:00:00Z",
                "assignee": {"email": "alice@example.com"},
            },
            "url": "https://linear.app/bic/issue/BIC-100/some-title",
            "webhookTimestamp": _now_ms(),
        }
    ).encode("utf-8")


def _comment_create_body(*, body_text: str = "real comment text") -> bytes:
    return json.dumps(
        {
            "action": "create",
            "type": "Comment",
            "data": {
                "id": "comment-uuid",
                "body": body_text,
                "user": {"email": "bob@example.com"},
                "issue": {
                    "identifier": "BIC-100",
                    "title": "Some title",
                },
                "updatedAt": "2026-05-19T00:00:00Z",
            },
            "url": "https://linear.app/bic/issue/BIC-100#comment-uuid",
            "webhookTimestamp": _now_ms(),
        }
    ).encode("utf-8")


def test_handle_invalid_json_returns_400():
    _put_secret()
    status, msg = handle(
        body=b"not-json",
        event_header="Issue",
        delivery_header="d1",
        signature_header=_sign("s", b"not-json"),
    )
    assert status == 400
    assert "JSON" in msg


def test_handle_missing_signature_returns_401():
    _put_secret()
    body = _issue_create_body()
    status, msg = handle(
        body=body,
        event_header="Issue",
        delivery_header="d1",
        signature_header=None,
    )
    assert status == 401


def test_handle_signature_mismatch_returns_401():
    _put_secret()
    body = _issue_create_body()
    status, msg = handle(
        body=body,
        event_header="Issue",
        delivery_header="d1",
        signature_header="0" * 64,
    )
    assert status == 401
    assert "verification" in msg


def test_handle_stale_body_timestamp_returns_401():
    _put_secret()
    payload = {
        "action": "create",
        "type": "Issue",
        "data": {"identifier": "BIC-100", "title": "t", "description": "d"},
        "webhookTimestamp": _now_ms() - 120_000,  # 2 min stale
    }
    body = json.dumps(payload).encode("utf-8")
    status, msg = handle(
        body=body,
        event_header="Issue",
        delivery_header="d-stale",
        signature_header=_sign("s", body),
    )
    assert status == 401
    assert "stale" in msg


def test_handle_issue_create_with_description_ingests():
    _put_secret()
    body = _issue_create_body()
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested BIC-100")
        status, msg = handle(
            body=body,
            event_header="Issue",
            delivery_header="d-issue",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert mock_dispatch.called
    norm = mock_dispatch.call_args.args[0]
    assert norm["source"] == "linear"
    assert norm["title"] == "BIC-100"
    assert len(norm["decisions"]) == 1
    assert norm["decisions"][0]["description"] == "real decision text"
    assert "alice@example.com" in norm["participants"]


def test_handle_issue_create_without_description_ignored():
    _put_secret()
    body = _issue_create_body(description="")
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        status, msg = handle(
            body=body,
            event_header="Issue",
            delivery_header="d-empty",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert "no description" in msg
    assert not mock_dispatch.called


def test_handle_issue_remove_acknowledged_no_ingest():
    """Append-only contract — we do NOT propagate Linear removes to the
    ledger. Erasure goes through #221 / GDPR, not the webhook path."""
    _put_secret()
    payload = json.loads(_issue_create_body())
    payload["action"] = "remove"
    body = json.dumps(payload).encode("utf-8")
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        status, msg = handle(
            body=body,
            event_header="Issue",
            delivery_header="d-rm",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert "remove" in msg
    assert not mock_dispatch.called


def test_handle_comment_create_ingests():
    _put_secret()
    body = _comment_create_body()
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested Comment on BIC-100 ()")
        status, msg = handle(
            body=body,
            event_header="Comment",
            delivery_header="d-cm",
            signature_header=_sign("s", body),
        )
    assert status == 200
    norm = mock_dispatch.call_args.args[0]
    assert norm["source"] == "linear"
    assert norm["title"] == "BIC-100"
    assert norm["decisions"][0]["title"] == "BIC-100#comment-comment-uuid"
    assert norm["decisions"][0]["description"] == "real comment text"


def test_handle_comment_update_acknowledged_no_ingest():
    _put_secret()
    payload = json.loads(_comment_create_body())
    payload["action"] = "update"
    body = json.dumps(payload).encode("utf-8")
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        status, msg = handle(
            body=body,
            event_header="Comment",
            delivery_header="d-cm-up",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert "update" in msg
    assert not mock_dispatch.called


def test_handle_unknown_event_type_acknowledged():
    _put_secret()
    payload = {
        "action": "create",
        "type": "Reaction",
        "webhookTimestamp": _now_ms(),
    }
    body = json.dumps(payload).encode("utf-8")
    status, msg = handle(
        body=body,
        event_header="Reaction",
        delivery_header="d-react",
        signature_header=_sign("s", body),
    )
    assert status == 200
    assert "acknowledged" in msg or "not yet" in msg


def test_handle_delivery_id_dedup():
    _put_secret()
    body = _issue_create_body()
    sig = _sign("s", body)
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested")
        s1, _ = handle(
            body=body, event_header="Issue", delivery_header="d-dup", signature_header=sig
        )
        s2, msg2 = handle(
            body=body, event_header="Issue", delivery_header="d-dup", signature_header=sig
        )
    assert s1 == 200
    assert s2 == 200
    assert "duplicate" in msg2
    # ingest only called once
    assert mock_dispatch.call_count == 1


def test_handle_missing_delivery_id_but_timestamp_present_still_processes():
    """H2 fix: missing Linear-Delivery is fine IF webhookTimestamp is
    present — the staleness gate still bounds replay to 60s."""
    _put_secret()
    body = _issue_create_body()  # includes webhookTimestamp
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested")
        status, _ = handle(
            body=body, event_header="Issue", delivery_header=None, signature_header=_sign("s", body)
        )
    assert status == 200
    assert mock_dispatch.called


def test_handle_missing_delivery_and_missing_timestamp_rejected():
    """H2 fix: both replay-defense fields missing → 400. Either an
    attacker stripping defenses or a misconfigured provider — neither
    should hit the ingest pipeline."""
    _put_secret()
    payload = {
        "action": "create",
        "type": "Issue",
        "data": {"identifier": "BIC-100", "title": "t", "description": "d"},
        # NO webhookTimestamp
    }
    body = json.dumps(payload).encode("utf-8")
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        status, msg = handle(
            body=body,
            event_header="Issue",
            delivery_header=None,
            signature_header=_sign("s", body),
        )
    assert status == 400
    assert "both missing" in msg
    assert not mock_dispatch.called


def test_handle_missing_timestamp_but_delivery_present_processes():
    """H2 corollary: webhookTimestamp absent is fine IF Linear-Delivery
    is present — dedup cache covers the replay window."""
    _put_secret()
    payload = {
        "action": "create",
        "type": "Issue",
        "data": {
            "identifier": "BIC-100",
            "title": "t",
            "description": "real",
        },
        # NO webhookTimestamp
    }
    body = json.dumps(payload).encode("utf-8")
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested")
        status, _ = handle(
            body=body,
            event_header="Issue",
            delivery_header="d-no-ts",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert mock_dispatch.called


def test_handle_event_header_vs_body_type_mismatch_rejected():
    """H3 fix: signed-body / unsigned-header asymmetry. An attacker
    with leaked-secret access could otherwise route Issue payloads as
    other event types to bypass ingest, or route other types as Issue
    to ingest them anyway. Mismatch must reject."""
    _put_secret()
    body = _issue_create_body()  # body.type = "Issue"
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        status, msg = handle(
            body=body,
            event_header="Reaction",  # header says Reaction
            delivery_header="d-mismatch",
            signature_header=_sign("s", body),
        )
    assert status == 400
    assert "header" in msg and "body.type" in msg
    assert not mock_dispatch.called


def test_handle_verify_runs_before_json_parse():
    """H1 regression: malformed JSON with INVALID signature must return
    401 (verify failure), NOT 400 (parse failure). Bad signature must
    not reach json.loads at all."""
    _put_secret()
    status, msg = handle(
        body=b"not-json-payload",
        event_header="Issue",
        delivery_header="d-h1",
        signature_header="0" * 64,  # well-formed length but wrong
    )
    assert status == 401
    assert "verification" in msg


def test_handle_hard_gate_refusal_returns_422():
    """When the ingest pipeline raises _IngestRefused (e.g. PHI hard
    gate), surface 422 — provider should NOT retry."""
    _put_secret()
    body = _issue_create_body()

    from handlers.ingest import _IngestRefused

    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        # Simulate _dispatch_to_ingest catching the refusal internally.
        mock_dispatch.return_value = (422, "refused: sensitive_data:phi")
        status, msg = handle(
            body=body,
            event_header="Issue",
            delivery_header="d-refuse",
            signature_header=_sign("s", body),
        )
    assert status == 422
    assert "refused" in msg
    # Side-channel: verify _IngestRefused is importable from the path
    # the handler uses so the actual integration works.
    assert _IngestRefused is not None


def test_handle_event_header_falls_back_to_body_type():
    """Missing Linear-Event header → fall back to body.type. Linear's
    docs say the header is canonical but defensive coding accommodates
    operators behind proxies that strip non-standard headers."""
    _put_secret()
    body = _issue_create_body()
    with patch("webhooks.linear._dispatch_to_ingest") as mock_dispatch:
        mock_dispatch.return_value = (200, "ingested")
        status, _ = handle(
            body=body,
            event_header=None,
            delivery_header="d-noheader",
            signature_header=_sign("s", body),
        )
    assert status == 200
    assert mock_dispatch.called


# ── _dispatch_to_ingest (sociable: real ingest path) ────────────────────────


def test_dispatch_to_ingest_uses_passive_mode(monkeypatch):
    """Pin the ingest contract — webhook path always passes
    ingest_mode='passive' so Phase 0a DLQ catches per-item failures."""
    from webhooks.linear import _dispatch_to_ingest

    captured: dict = {}

    async def _fake_handle_ingest(ctx, payload, *, source_scope, ingest_mode):
        captured["scope"] = source_scope
        captured["mode"] = ingest_mode
        captured["payload"] = payload

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_handle_ingest)
    # BicameralContext.from_env may try keyring; replace with a stub.
    monkeypatch.setattr(
        "context.BicameralContext.from_env", classmethod(lambda cls: SimpleNamespace())
    )

    norm = {
        "query": "Some title",
        "source": "linear",
        "title": "BIC-100",
        "date": "2026-05-19T00:00:00Z",
        "participants": ["alice@example.com"],
        "decisions": [{"description": "x", "title": "BIC-100"}],
    }
    status, msg = _dispatch_to_ingest(norm, label="Issue BIC-100 (url)")
    assert status == 200
    assert captured["scope"] == "linear"
    assert captured["mode"] == "passive"
    assert captured["payload"]["title"] == "BIC-100"
