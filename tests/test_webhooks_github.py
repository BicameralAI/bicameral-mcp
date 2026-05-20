"""Tests for the GitHub webhook handler.

Coverage:
- verify_signature: missing header, malformed prefix, missing secret,
  signature mismatch (including timing-attack pin), success path
- handle: missing signature → 401, invalid JSON body → 400, ping → 200,
  pull_request closed-merged → ingest, pull_request closed-not-merged → ignore,
  other events → 200 + ignored, duplicate delivery → 200 + duplicate
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from webhooks.github import WebhookVerificationError, handle, verify_signature


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
    return (
        "sha256=" + hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    )


# ── verify_signature ────────────────────────────────────────────────────────


def test_verify_signature_success():
    body = b'{"key":"value"}'
    secret = "hunter2"
    sig = _sign(secret, body)
    verify_signature(body=body, signature_header=sig, secret=secret)  # does not raise


def test_verify_signature_missing_header_raises():
    with pytest.raises(WebhookVerificationError, match="missing"):
        verify_signature(body=b"x", signature_header=None, secret="hunter2")


def test_verify_signature_empty_header_raises():
    with pytest.raises(WebhookVerificationError, match="missing"):
        verify_signature(body=b"x", signature_header="", secret="hunter2")


def test_verify_signature_malformed_prefix_raises():
    """A signature header that doesn't start with 'sha256=' must be rejected
    BEFORE any HMAC computation — protects against confused-deputy attacks
    if a future GitHub version ships a different algorithm header."""
    with pytest.raises(WebhookVerificationError, match="malformed"):
        verify_signature(
            body=b"x",
            signature_header="md5=" + "0" * 32,
            secret="hunter2",
        )


def test_verify_signature_missing_secret_raises():
    """If secrets_store has no webhook_secret, refuse rather than silently
    accept a payload signed with empty key."""
    body = b"x"
    # Compute sig with empty secret to make sure we'd reject even if attacker
    # could produce it.
    sig = _sign("", body)
    with pytest.raises(WebhookVerificationError, match="no webhook_secret"):
        verify_signature(body=body, signature_header=sig, secret="")


def test_verify_signature_mismatch_raises():
    body = b'{"key":"value"}'
    sig = _sign("right-secret", body)
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(body=body, signature_header=sig, secret="wrong-secret")


def test_verify_signature_uses_compare_digest():
    """Constant-time comparison is critical — pin that the implementation
    uses hmac.compare_digest rather than ==."""
    import hmac as _hmac

    body = b"x"
    secret = "s"
    sig = _sign(secret, body)
    with patch.object(_hmac, "compare_digest", wraps=_hmac.compare_digest) as spy:
        verify_signature(body=body, signature_header=sig, secret=secret)
    assert spy.called


def test_verify_signature_body_byte_sensitive():
    """Adding even one byte to the body must invalidate the signature."""
    secret = "s"
    sig = _sign(secret, b'{"a":1}')
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(body=b'{"a":1} ', signature_header=sig, secret=secret)


# ── handle: top-level dispatch ──────────────────────────────────────────────


def test_handle_missing_signature_returns_401():
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    status, msg = handle(
        event="ping",
        delivery_id="d1",
        body=b"{}",
        signature_header=None,
    )
    assert status == 401
    assert "verification" in msg


def test_handle_no_secret_configured_returns_401():
    """No webhook_secret in secrets_store — refuse cleanly."""
    body = b'{"zen":"Speak like a human."}'
    status, msg = handle(
        event="ping",
        delivery_id="d1",
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 401


def test_handle_ping_event_returns_200():
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = b'{"zen":"Speak like a human."}'
    status, msg = handle(
        event="ping",
        delivery_id="d1",
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 200
    assert "ping" in msg


def test_handle_invalid_json_returns_400():
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = b"not json at all"
    status, msg = handle(
        event="pull_request",
        delivery_id="d1",
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 400


def test_handle_duplicate_delivery_returns_200():
    """A repeat delivery (same UUID) ack'd as 200 so provider stops retrying."""
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = b'{"zen":"x"}'
    sig = _sign("s", body)
    # First delivery: processed.
    status1, _ = handle(event="ping", delivery_id="dup-1", body=body, signature_header=sig)
    # Second delivery with same ID: dedup ack.
    status2, msg2 = handle(event="ping", delivery_id="dup-1", body=body, signature_header=sig)
    assert status1 == 200
    assert status2 == 200
    assert "duplicate" in msg2


def test_handle_pull_request_closed_not_merged_ignored():
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = json.dumps(
        {"action": "closed", "pull_request": {"merged": False, "html_url": "x"}}
    ).encode("utf-8")
    status, msg = handle(
        event="pull_request",
        delivery_id="d1",
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 200
    assert "ignored" in msg


def test_handle_pull_request_merged_triggers_ingest():
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    pr_url = "https://github.com/foo/bar/pull/1"
    body = json.dumps(
        {
            "action": "closed",
            "pull_request": {"merged": True, "html_url": pr_url},
        }
    ).encode("utf-8")

    # Patch the active-ingest path + the ingest handler so the test
    # never hits the network or the ledger.
    fake_payload = {"source": "github", "decisions": [], "title": "x"}

    async def _fake_ingest(*args, **kwargs):
        return None

    with (
        patch(
            "sources.github.adapter.GitHubAdapter.fetch_active",
            return_value=fake_payload,
        ),
        patch("handlers.ingest.handle_ingest", new=_fake_ingest),
        patch("context.BicameralContext.from_env", return_value=MagicMock()),
    ):
        status, msg = handle(
            event="pull_request",
            delivery_id="d1",
            body=body,
            signature_header=_sign("s", body),
        )

    assert status == 200
    assert "ingested" in msg


def test_handle_unknown_event_returns_200_ignored():
    """A weird event type doesn't trip a 4xx — provider would disable
    the webhook on repeated 4xx. We log and 200."""
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = b"{}"
    status, msg = handle(
        event="repository_dispatch",
        delivery_id="d1",
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 200
    assert "ignored" in msg


def test_handle_signature_with_wrong_secret_returns_401():
    """An attacker who doesn't know the secret can't get past verification
    even with a valid-looking 'sha256=...' header."""
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="real")
    body = b'{"zen":"x"}'
    bogus = _sign("attacker-guess", body)
    status, _ = handle(event="ping", delivery_id="d1", body=body, signature_header=bogus)
    assert status == 401


def test_handle_empty_delivery_id_returns_400():
    """H1: GitHub always sends X-GitHub-Delivery. Absence is rejected
    so an attacker can't bypass dedup by omitting the header."""
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    body = b'{"zen":"x"}'
    status, msg = handle(
        event="ping",
        delivery_id="",  # empty — should reject
        body=body,
        signature_header=_sign("s", body),
    )
    assert status == 400
    assert "X-GitHub-Delivery" in msg


def test_handle_hard_gate_refusal_returns_422_not_500():
    """H2: _IngestRefused must map to 422 (permanent) not 500 (retry).
    GitHub retries 5xx; we do NOT want to re-process a payload that
    contains a secret/PHI/PAN, or to log the offending content N times.
    """
    from secrets_store import put_secret

    put_secret(source_id="github", key="webhook_secret", value="s")
    pr_url = "https://github.com/foo/bar/pull/1"
    body = json.dumps(
        {"action": "closed", "pull_request": {"merged": True, "html_url": pr_url}}
    ).encode("utf-8")

    fake_payload = {"source": "github", "decisions": [], "title": "x"}

    # Make handle_ingest raise _IngestRefused (hard-gate path).
    from handlers.ingest import _IngestRefused

    async def _refuse(*args, **kwargs):
        raise _IngestRefused("sensitive_data:secret")

    with (
        patch("sources.github.adapter.GitHubAdapter.fetch_active", return_value=fake_payload),
        patch("handlers.ingest.handle_ingest", new=_refuse),
        patch("context.BicameralContext.from_env", return_value=MagicMock()),
    ):
        status, msg = handle(
            event="pull_request",
            delivery_id="d-permanent-fail",
            body=body,
            signature_header=_sign("s", body),
        )

    assert status == 422, f"expected 422 (don't retry), got {status}"
    assert "refused" in msg.lower()
    # The reason category surfaces — but never the payload content.
    assert "secret" in msg


def test_handle_dedup_only_after_verification():
    """An unverified delivery must NOT pollute the dedup cache. Otherwise
    an attacker could spam delivery IDs and prevent legitimate deliveries
    from processing."""
    from secrets_store import put_secret
    from webhooks.dedup import get_dedup_cache

    put_secret(source_id="github", key="webhook_secret", value="real")
    body = b'{"zen":"x"}'

    # 1. Attacker sends bogus delivery with a delivery ID.
    bogus = _sign("attacker", body)
    handle(event="ping", delivery_id="d-attack", body=body, signature_header=bogus)

    # 2. The dedup cache must NOT have learned "d-attack".
    assert get_dedup_cache().is_duplicate("github", "d-attack") is False

    # 3. A legit delivery with the same ID still processes.
    legit = _sign("real", body)
    status, msg = handle(event="ping", delivery_id="d-attack", body=body, signature_header=legit)
    assert status == 200
    assert "ping" in msg  # not "duplicate"
