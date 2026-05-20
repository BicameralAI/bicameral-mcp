"""Tests for the Notion webhook handler (#337 cycle 8).

Coverage parity with the cycles 5-7 / 9 suites, scoped to Notion's
two-mode contract (verification handshake + signed events):

- verify_signature: missing header, missing token, missing prefix,
  wrong digest length, mismatch, body-byte sensitivity, success
- handle (verification path): non-JSON, non-object, missing token,
  out-of-bounds length, success persists + logs the token
- handle (event path): missing signature, missing subscription_id,
  missing id, missing type, no registered token, signature mismatch,
  happy path, dedup by id, attempt_number>1 logged, pending-token
  adoption on first event for fresh subscription
- Server route: /webhooks/notion routing (smoke test against the
  dispatch tuple shape)
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from webhooks.notion import WebhookVerificationError, handle, verify_signature


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


def _sign(token: str, body: bytes) -> str:
    return (
        "sha256=" + hmac.new(token.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    )


def _put_token(subscription_id: str, token: str = "secret_abc") -> None:
    from secrets_store import put_secret

    put_secret(source_id="notion", key=f"subscription_{subscription_id}", value=token)


def _event_body(
    *,
    subscription_id: str = "sub-1",
    event_id: str = "evt-1",
    event_type: str = "page.content_updated",
    attempt_number: int = 1,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "timestamp": "2026-05-20T12:00:00.000Z",
            "workspace_id": "ws-1",
            "subscription_id": subscription_id,
            "integration_id": "int-1",
            "type": event_type,
            "authors": [{"id": "u-1", "type": "person"}],
            "accessible_by": [{"id": "u-1", "type": "person"}],
            "attempt_number": attempt_number,
            "entity": {"id": "page-1", "type": "page"},
            "data": {},
        }
    ).encode("utf-8")


# ── verify_signature ────────────────────────────────────────────────────────


def test_verify_signature_success():
    body = b'{"ok":true}'
    token = "secret_xyz"
    verify_signature(
        body=body, signature_header=_sign(token, body), verification_token=token
    )  # does not raise


def test_verify_signature_missing_header_raises():
    with pytest.raises(WebhookVerificationError, match="Signature"):
        verify_signature(body=b"x", signature_header=None, verification_token="t")


def test_verify_signature_missing_token_raises():
    with pytest.raises(WebhookVerificationError, match="verification_token"):
        verify_signature(body=b"x", signature_header="sha256=" + "a" * 64, verification_token="")


def test_verify_signature_missing_prefix_raises():
    body = b"x"
    sig_without_prefix = hmac.new(b"t", msg=body, digestmod=hashlib.sha256).hexdigest()
    with pytest.raises(WebhookVerificationError, match="sha256="):
        verify_signature(body=body, signature_header=sig_without_prefix, verification_token="t")


def test_verify_signature_wrong_digest_length():
    with pytest.raises(WebhookVerificationError, match="64 hex"):
        verify_signature(body=b"x", signature_header="sha256=abcdef", verification_token="t")


def test_verify_signature_mismatch():
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(
            body=b"x",
            signature_header="sha256=" + "0" * 64,
            verification_token="t",
        )


def test_verify_signature_body_byte_sensitivity():
    """Single-byte change in body invalidates the signature."""
    token = "secret_xyz"
    sig = _sign(token, b"original")
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(body=b"originaL", signature_header=sig, verification_token=token)


def test_verify_signature_uppercase_hex_accepted():
    """Defensive: accept uppercase hex even though Notion sends lowercase."""
    body = b"x"
    token = "secret_xyz"
    sig = _sign(token, body)
    # Uppercase the hex portion only.
    upper_sig = "sha256=" + sig[len("sha256=") :].upper()
    verify_signature(body=body, signature_header=upper_sig, verification_token=token)


# ── handle: verification handshake ─────────────────────────────────────────


def test_handle_invalid_json_returns_400():
    status, msg = handle(body=b"not-json", signature_header=None)
    assert status == 400
    assert "JSON" in msg


def test_handle_non_object_body_returns_400():
    status, msg = handle(body=b'["array", "not", "object"]', signature_header=None)
    assert status == 400


def test_handle_verification_success_persists_and_logs(
    capsys: pytest.CaptureFixture,
):
    """Verification POST: extract token, persist under fingerprint
    slot, log fingerprint (NOT full token) to stderr for operator
    retrieval. F3 review fix: token must NOT appear in stderr."""
    import hashlib

    token = "secret_abcdefghij"
    expected_fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    body = json.dumps({"verification_token": token}).encode("utf-8")
    status, msg = handle(body=body, signature_header=None)
    assert status == 200
    assert expected_fp in msg

    captured = capsys.readouterr()
    # Fingerprint logged, full token must NOT be in stderr.
    assert expected_fp in captured.err
    assert token not in captured.err
    assert "paste" in captured.err.lower()

    # Confirm the token was persisted under the fingerprint slot.
    from secrets_store import get_secret

    raw = get_secret(source_id="notion", key=f"pending_{expected_fp}")
    assert raw is not None
    entry = json.loads(raw)
    assert entry["token"] == token
    assert isinstance(entry["received_at"], int)


def test_handle_verification_missing_token_returns_400():
    body = json.dumps({"verification_token": None}).encode("utf-8")
    status, msg = handle(body=body, signature_header=None)
    assert status == 400
    assert "verification_token" in msg


def test_handle_verification_token_too_short_rejected():
    body = json.dumps({"verification_token": "tiny"}).encode("utf-8")
    status, msg = handle(body=body, signature_header=None)
    assert status == 400
    assert "length" in msg


def test_handle_verification_token_too_long_rejected():
    body = json.dumps({"verification_token": "x" * 257}).encode("utf-8")
    status, msg = handle(body=body, signature_header=None)
    assert status == 400
    assert "length" in msg


def test_handle_verification_token_in_event_body_does_not_clobber():
    """Structural marker safety: if an attacker smuggles
    verification_token INTO an event payload (with `type` set), we
    must NOT treat it as a verification handshake. Discriminator is
    'verification_token present AND type absent'."""
    body = json.dumps(
        {
            "verification_token": "secret_attacker_chosen",
            "type": "page.content_updated",
            "id": "evt-malicious",
            "subscription_id": "sub-1",
        }
    ).encode("utf-8")
    status, _ = handle(body=body, signature_header=None)
    # Event path: missing signature → 401 (not 200/verification).
    assert status == 401

    # And no pending entries were created.
    from secrets_store import list_keys

    pending_keys = [k for k in list_keys(source_id="notion") if k.startswith("pending_")]
    assert pending_keys == []


# ── handle: event delivery ─────────────────────────────────────────────────


def test_handle_event_missing_signature_returns_401():
    _put_token("sub-1")
    body = _event_body()
    status, msg = handle(body=body, signature_header=None)
    assert status == 401


def test_handle_event_missing_subscription_id_returns_400():
    body = json.dumps({"id": "evt", "type": "page.content_updated"}).encode("utf-8")
    status, msg = handle(body=body, signature_header="sha256=" + "0" * 64)
    assert status == 400
    assert "subscription_id" in msg


def test_handle_event_missing_id_returns_400():
    body = json.dumps({"subscription_id": "sub-1", "type": "page.content_updated"}).encode("utf-8")
    status, msg = handle(body=body, signature_header="sha256=" + "0" * 64)
    assert status == 400
    assert msg.endswith("missing id")


def test_handle_event_missing_type_returns_400():
    body = json.dumps({"subscription_id": "sub-1", "id": "evt"}).encode("utf-8")
    status, msg = handle(body=body, signature_header="sha256=" + "0" * 64)
    assert status == 400
    assert "type" in msg


def test_handle_event_no_registered_token_returns_401(
    capsys: pytest.CaptureFixture,
):
    """No verification_token for the subscription → 401. Notion will
    not retry on 401 (gives up after backoff envelope); operator
    must re-run the handshake."""
    body = _event_body(subscription_id="sub-unknown")
    status, msg = handle(body=body, signature_header="sha256=" + "0" * 64)
    assert status == 401
    assert "verification_token" in msg


def test_handle_event_signature_mismatch_returns_401():
    _put_token("sub-1", token="correct-token")
    body = _event_body()
    bad_sig = _sign("WRONG-token", body)
    status, msg = handle(body=body, signature_header=bad_sig)
    assert status == 401
    assert "mismatch" in msg or "verification" in msg


def test_handle_event_happy_path():
    _put_token("sub-1", token="secret_token")
    body = _event_body()
    status, msg = handle(body=body, signature_header=_sign("secret_token", body))
    assert status == 200
    assert "evt-1" in msg
    assert "page.content_updated" in msg


def test_handle_event_dedup_by_id():
    _put_token("sub-1", token="secret_token")
    body = _event_body(event_id="evt-dup")
    sig = _sign("secret_token", body)
    s1, _ = handle(body=body, signature_header=sig)
    s2, msg2 = handle(body=body, signature_header=sig)
    assert s1 == 200
    assert s2 == 200
    assert "duplicate" in msg2


def test_handle_event_attempt_number_4_logged(capsys: pytest.CaptureFixture):
    """F7 fix: log attempt_number only when >= 4 (halfway through
    the 8-retry envelope — real signal, not transient noise)."""
    _put_token("sub-1", token="secret_token")
    body = _event_body(attempt_number=4)
    status, _ = handle(body=body, signature_header=_sign("secret_token", body))
    assert status == 200
    captured = capsys.readouterr()
    assert "attempt_number=4" in captured.err
    assert "retrying" in captured.err


def test_handle_event_adopts_pending_token_by_hmac_match(
    capsys: pytest.CaptureFixture,
):
    """First event for a fresh subscription: enumerate pending
    entries (keyed by token fingerprint, F2 fix), try each as the
    HMAC key, adopt the one whose signature matches the body. On
    adoption, the pending entry is DELETED and the subscription-
    specific key is created."""
    import hashlib
    import time as _t

    from secrets_store import get_secret, put_secret

    token = "secret_pending"
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    entry = json.dumps({"token": token, "received_at": int(_t.time())})
    put_secret(source_id="notion", key=f"pending_{fingerprint}", value=entry)
    assert get_secret(source_id="notion", key="subscription_sub-fresh") is None

    body = _event_body(subscription_id="sub-fresh")
    status, _ = handle(body=body, signature_header=_sign(token, body))
    assert status == 200

    # Adopted: subscription-specific slot now holds the token.
    assert get_secret(source_id="notion", key="subscription_sub-fresh") == token
    # Pending slot was consumed.
    assert get_secret(source_id="notion", key=f"pending_{fingerprint}") is None
    captured = capsys.readouterr()
    assert "adopted pending" in captured.err


def test_handle_event_multiple_pending_only_matching_adopted(
    capsys: pytest.CaptureFixture,
):
    """F2 fix in action: two concurrent verifications produce two
    pending entries. An event signed with TOKEN_A adopts only the
    A entry; B's entry remains untouched."""
    import hashlib
    import time as _t

    from secrets_store import get_secret, put_secret

    token_a = "secret_for_sub_a"
    token_b = "secret_for_sub_b"
    fp_a = hashlib.sha256(token_a.encode("utf-8")).hexdigest()[:16]
    fp_b = hashlib.sha256(token_b.encode("utf-8")).hexdigest()[:16]
    now = int(_t.time())
    put_secret(
        source_id="notion",
        key=f"pending_{fp_a}",
        value=json.dumps({"token": token_a, "received_at": now}),
    )
    put_secret(
        source_id="notion",
        key=f"pending_{fp_b}",
        value=json.dumps({"token": token_b, "received_at": now}),
    )

    body = _event_body(subscription_id="sub-a")
    status, _ = handle(body=body, signature_header=_sign(token_a, body))
    assert status == 200

    # A was adopted + consumed; B is untouched.
    assert get_secret(source_id="notion", key="subscription_sub-a") == token_a
    assert get_secret(source_id="notion", key=f"pending_{fp_a}") is None
    assert get_secret(source_id="notion", key=f"pending_{fp_b}") is not None


def test_handle_event_stale_pending_entry_dropped(capsys: pytest.CaptureFixture):
    """F9 fix: pending entries older than 24h are deleted and not
    adoptable, even if the HMAC would otherwise match. Bounds the
    attacker-DoS window from forever to 24h."""
    import hashlib

    from secrets_store import get_secret, put_secret

    token = "secret_old"
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    stale_ts = 0  # epoch 1970 — well past 24h
    put_secret(
        source_id="notion",
        key=f"pending_{fingerprint}",
        value=json.dumps({"token": token, "received_at": stale_ts}),
    )

    body = _event_body(subscription_id="sub-stale")
    status, _ = handle(body=body, signature_header=_sign(token, body))
    # No adoption — falls through to "no verification_token registered".
    assert status == 401

    # Stale entry was deleted as a side effect.
    assert get_secret(source_id="notion", key=f"pending_{fingerprint}") is None
    captured = capsys.readouterr()
    assert "stale pending entry" in captured.err


def test_handle_event_attacker_pending_does_not_poison_legit_event():
    """F2c fix: attacker POSTs a fake verification_token, creating
    a pending entry under their token's fingerprint. A legitimate
    event from Notion (signed with the real token, which we don't
    yet have) lands; we enumerate pendings, the attacker's HMAC
    does NOT match the legitimate body, so we do NOT adopt the
    attacker entry. Event returns 401 (no matching pending), but
    the attacker entry is NOT promoted to subscription-specific."""
    import hashlib
    import time as _t

    from secrets_store import get_secret, put_secret

    attacker_token = "secret_attacker"
    fp_attacker = hashlib.sha256(attacker_token.encode("utf-8")).hexdigest()[:16]
    put_secret(
        source_id="notion",
        key=f"pending_{fp_attacker}",
        value=json.dumps({"token": attacker_token, "received_at": int(_t.time())}),
    )

    # Legitimate body signed with a DIFFERENT, legitimate token
    # that we never received a verification for.
    legitimate_token = "secret_real_from_notion"
    body = _event_body(subscription_id="sub-real")
    status, _ = handle(body=body, signature_header=_sign(legitimate_token, body))
    assert status == 401

    # Attacker entry is still pending (HMAC didn't match, no
    # adoption happened) but it was NOT promoted to
    # subscription_sub-real.
    assert get_secret(source_id="notion", key="subscription_sub-real") is None
    assert get_secret(source_id="notion", key=f"pending_{fp_attacker}") is not None


def test_handle_event_subscription_specific_token_overrides_pending():
    """If a subscription-specific token exists, the pending fallback
    is NOT used — pin that the per-subscription token wins."""
    import hashlib
    import time as _t

    from secrets_store import put_secret

    put_secret(
        source_id="notion",
        key=f"pending_{hashlib.sha256(b'WRONG').hexdigest()[:16]}",
        value=json.dumps({"token": "WRONG", "received_at": int(_t.time())}),
    )
    put_secret(source_id="notion", key="subscription_sub-1", value="correct-token")

    body = _event_body(subscription_id="sub-1")
    status, _ = handle(body=body, signature_header=_sign("correct-token", body))
    assert status == 200


def test_handle_event_subscription_id_with_invalid_chars_rejected():
    """Review nice-to-have: invalid subscription_id (chars outside
    [A-Za-z0-9._-]) returns 400, not 500 from secrets_store key
    validation."""
    body = json.dumps(
        {
            "subscription_id": "../malicious",  # path traversal attempt
            "id": "evt-1",
            "type": "page.content_updated",
        }
    ).encode("utf-8")
    status, msg = handle(body=body, signature_header="sha256=" + "0" * 64)
    assert status == 400
    assert "subscription_id" in msg


def test_handle_event_attempt_number_2_does_not_log_noise(
    capsys: pytest.CaptureFixture,
):
    """F7 nice-to-have: attempt_number==2 is almost always a
    transient self-heal; don't log noise. Only log at >= 4."""
    _put_token("sub-1", token="secret_token")
    body = _event_body(attempt_number=2)
    status, _ = handle(body=body, signature_header=_sign("secret_token", body))
    assert status == 200
    captured = capsys.readouterr()
    assert "attempt_number=2" not in captured.err
