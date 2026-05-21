"""Tests for the Jira Cloud webhook handler (#337 Phase B).

Sociable: real ``handle``, real ``verify_signature``, the real
``DeliveryDedupCache`` singleton (reset per test), and the real
``JiraAdapter.normalize_issue_to_payload`` / ``flatten_adf``. The only
seams are the genuine boundaries — the ``handle_ingest`` ledger call and
the webhook-secret resolver — because running the real ledger / keyring
inside a unit test would couple the receiver test to external state we
don't need to exercise here.

Coverage (the 11 cases from the Phase B plan's Test plan):
1. verify_signature against Atlassian's documented test vector; tamper.
2. missing X-Hub-Signature → 401; bad signature → 401.
3. missing X-Atlassian-Webhook-Identifier → 400.
4. malformed JSON body (signature valid) → 400.
5. dedup: same identifier twice → second 200 "duplicate", ingest once.
6. jira:issue_created / jira:issue_updated → ingest, scope="jira",
   payload normalized (ADF description flattened).
7. comment_created → ingest (normalizes the inline issue).
8. jira:issue_deleted / unknown webhookEvent → 200, no ingest.
9. issue missing key → 200 acknowledged, no ingest.
10. _IngestRefused → 422; transient ingest Exception → 500; a 500 does
    NOT mark_seen (retry re-dispatches).
11. missing webhook secret → 500, message carries no secret value.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from webhooks.jira import WebhookVerificationError, handle, verify_signature

# Atlassian's documented HMAC test vector (docs/vendor/jira/webhooks.md §2).
_ATLASSIAN_SECRET = "It's a Secret to Everybody"
_ATLASSIAN_BODY = b"Hello World!"
_ATLASSIAN_EXPECTED = "sha256=a4771c39fbe90f317c7824e83ddef3caae9cb3d976c214ace1f2937e133263c9"

# A non-secret value used for the handle()-level tests.
_SECRET = "jira-webhook-shared-secret"


@pytest.fixture(autouse=True)
def _reset_dedup():
    """The dedup cache is a process-local singleton; reset it between
    tests so a delivery identifier from one test does not short-circuit
    another (mirrors tests/test_webhooks_google_drive.py)."""
    from webhooks.dedup import _reset_for_tests as _dedup_reset

    _dedup_reset()
    yield
    _dedup_reset()


def _sign(secret: str, body: bytes) -> str:
    """Build a Jira-style X-Hub-Signature header value for ``body``."""
    return (
        "sha256=" + hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    )


def _issue_payload(
    *,
    event: str = "jira:issue_updated",
    issue_key: str | None = "PROJ-123",
    summary: str = "Investigate flaky webhook test",
    description_text: str = "We decided to pin the retry budget at 5.",
) -> dict:
    """Build a Jira webhook payload with an inline ``issue`` object.

    ``description`` is ADF JSON (Jira v3 wire shape) so the real
    ``flatten_adf`` is exercised end-to-end by the ingest path.
    """
    issue: dict = {
        "id": "10042",
        "fields": {
            "summary": summary,
            "updated": "2026-05-21T12:00:00.000+0000",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description_text}],
                    }
                ],
            },
            "assignee": {"displayName": "Ada Lovelace"},
            "reporter": {"displayName": "Grace Hopper"},
        },
    }
    if issue_key is not None:
        issue["key"] = issue_key
    return {
        "timestamp": 1716285600000,
        "webhookEvent": event,
        "issue_event_type_name": "issue_generic",
        "issue": issue,
    }


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


# ── 1. verify_signature: Atlassian test vector ──────────────────────────────


def test_verify_signature_atlassian_test_vector_passes():
    """The verifier must accept Atlassian's documented test vector
    (docs/vendor/jira/webhooks.md §2) — the wire-protocol ground truth."""
    verify_signature(
        body=_ATLASSIAN_BODY,
        signature_header=_ATLASSIAN_EXPECTED,
        secret=_ATLASSIAN_SECRET,
    )  # does not raise


def test_verify_signature_tampered_body_raises():
    """Atlassian's vector signature against a tampered body must fail —
    pins the body-byte sensitivity of the HMAC."""
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(
            body=_ATLASSIAN_BODY + b" ",
            signature_header=_ATLASSIAN_EXPECTED,
            secret=_ATLASSIAN_SECRET,
        )


def test_verify_signature_uses_compare_digest():
    """Constant-time comparison is load-bearing — pin that the verifier
    routes through hmac.compare_digest, not ==."""
    import hmac as _hmac
    from unittest.mock import patch

    with patch.object(_hmac, "compare_digest", wraps=_hmac.compare_digest) as spy:
        verify_signature(
            body=_ATLASSIAN_BODY,
            signature_header=_ATLASSIAN_EXPECTED,
            secret=_ATLASSIAN_SECRET,
        )
    assert spy.called


def test_verify_signature_malformed_prefix_raises():
    """A header that doesn't start with 'sha256=' is rejected before any
    HMAC computation."""
    with pytest.raises(WebhookVerificationError, match="malformed"):
        verify_signature(body=b"x", signature_header="md5=" + "0" * 32, secret=_SECRET)


def test_verify_signature_empty_hex_raises():
    """The classic bypass attempt — the 'sha256=' prefix present but an empty
    hex payload — fails closed: 0 chars vs the 64-char digest is a mismatch,
    raising WebhookVerificationError (never silently accepted)."""
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        verify_signature(body=b"x", signature_header="sha256=", secret=_SECRET)


def test_verify_signature_non_ascii_header_raises_verification_error():
    """A non-ASCII signature header fails closed as WebhookVerificationError
    (→ 401), NOT a raw TypeError from hmac.compare_digest. Defense-in-depth
    for a non-HTTP caller bypassing the server's header ASCII gate."""
    with pytest.raises(WebhookVerificationError, match="non-ASCII"):
        verify_signature(body=b"x", signature_header="sha256=café", secret=_SECRET)


# ── 2. handle: signature failures → 401 ─────────────────────────────────────


def test_handle_missing_signature_returns_401():
    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=None,
        delivery_identifier="wh-1",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 401
    assert "verification" in msg


def test_handle_bad_signature_returns_401():
    """A valid-looking 'sha256=...' header signed with the wrong secret
    must not get past verification."""
    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=_sign("attacker-guess", body),
        delivery_identifier="wh-1",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 401
    assert "verification" in msg


# ── 3. handle: missing delivery identifier → 400 ────────────────────────────


def test_handle_missing_delivery_identifier_returns_400():
    """Jira always sends X-Atlassian-Webhook-Identifier; an empty value
    is rejected so a captured signed payload cannot bypass dedup."""
    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 400
    assert "X-Atlassian-Webhook-Identifier" in msg


# ── 4. handle: malformed JSON body (signature valid) → 400 ──────────────────


def test_handle_malformed_json_body_returns_400():
    """A body that verifies but is not JSON-decodable → 400. Verification
    runs over the raw bytes, so a valid signature over garbage still
    reaches the json.loads boundary."""
    body = b"not json at all"
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-1",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 400
    assert "JSON" in msg


# ── 5. handle: dedup ────────────────────────────────────────────────────────


def test_handle_duplicate_delivery_returns_200_ingest_once(monkeypatch):
    """The same X-Atlassian-Webhook-Identifier twice → the second is
    200 'duplicate' and handle_ingest is invoked exactly once."""
    ingest_calls: list[str] = []

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        ingest_calls.append(source_scope)

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(_issue_payload())
    sig = _sign(_SECRET, body)

    status1, msg1 = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-dup",
        secret_resolver=lambda: _SECRET,
    )
    status2, msg2 = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-dup",
        secret_resolver=lambda: _SECRET,
    )
    assert status1 == 200
    assert status2 == 200
    assert "ingested" in msg1
    assert msg2 == "duplicate"
    assert ingest_calls == ["jira"], f"ingest invoked {len(ingest_calls)} times; want 1"


# ── 6. handle: issue_created / issue_updated → ingest ───────────────────────


@pytest.mark.parametrize("event", ["jira:issue_created", "jira:issue_updated"])
def test_handle_issue_events_ingest_with_normalized_payload(monkeypatch, event):
    """jira:issue_created / jira:issue_updated → handle_ingest called with
    source_scope='jira', ingest_mode='passive', and a payload whose ADF
    description was flattened to plain text by the real adapter."""
    captured: dict = {}

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        captured["payload"] = payload
        captured["scope"] = source_scope
        captured["mode"] = ingest_mode

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(_issue_payload(event=event, description_text="Chose option B for the rollout."))
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier=f"wh-{event}",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    assert "ingested" in msg
    assert captured["scope"] == "jira"
    assert captured["mode"] == "passive"
    payload = captured["payload"]
    assert payload["source"] == "jira"
    assert payload["title"] == "PROJ-123"
    # The ADF description was flattened to plain text (real flatten_adf).
    assert payload["decisions"][0]["description"] == "Chose option B for the rollout."
    assert "Ada Lovelace" in payload["participants"]


# ── 7. handle: comment_created → ingest the inline issue ────────────────────


def test_handle_comment_created_ingests_inline_issue(monkeypatch):
    """comment_created carries the inline issue object too — Phase B
    normalizes that same inline issue."""
    captured: dict = {}

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        captured["payload"] = payload
        captured["scope"] = source_scope

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(
        _issue_payload(event="comment_created", description_text="Comment-event issue body.")
    )
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-comment",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    assert "ingested" in msg
    assert captured["scope"] == "jira"
    assert captured["payload"]["title"] == "PROJ-123"


# ── 8. handle: deletes / unknown events → 200, no ingest ────────────────────


@pytest.mark.parametrize(
    "event",
    ["jira:issue_deleted", "comment_deleted", "worklog_created", "sprint_started"],
)
def test_handle_non_ingest_events_acked_no_ingest(monkeypatch, event):
    """jira:issue_deleted, comment_deleted, and every other webhookEvent
    → 200 acknowledged with no ingest (append-only contract)."""
    ingest_calls: list[str] = []

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        ingest_calls.append(source_scope)

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)

    body = _body(_issue_payload(event=event))
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier=f"wh-{event}",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    assert "no ingest" in msg
    assert ingest_calls == [], f"event {event!r} wrongly triggered ingest"


# ── 9. handle: issue missing key → 200 acknowledged, no ingest ──────────────


def test_handle_issue_missing_key_acked_no_ingest(monkeypatch):
    """An ingest event whose inline issue has no 'key' → 200 acknowledged
    without ingest (malformed payload, not worth a retry)."""
    ingest_calls: list[str] = []

    async def _fake_ingest(ctx, payload, *, source_scope, ingest_mode):
        ingest_calls.append(source_scope)

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fake_ingest)

    body = _body(_issue_payload(event="jira:issue_updated", issue_key=None))
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-nokey",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 200
    assert "key" in msg
    assert ingest_calls == []


# ── 10. handle: _IngestRefused → 422; transient → 500; 500 not marked ───────


def test_handle_ingest_refused_returns_422(monkeypatch):
    """_IngestRefused (hard gate — PHI/secret/PAN) → 422 so Jira does not
    retry; the reason category surfaces but never the payload content."""
    from handlers.ingest import _IngestRefused

    async def _refuse(ctx, payload, *, source_scope, ingest_mode):
        raise _IngestRefused("sensitive_data:secret")

    monkeypatch.setattr("handlers.ingest.handle_ingest", _refuse)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-refused",
        secret_resolver=lambda: _SECRET,
    )
    assert status == 422
    assert "refused" in msg.lower()
    assert "secret" in msg


def test_handle_transient_ingest_failure_returns_500_and_does_not_mark(monkeypatch):
    """A transient ingest Exception → 500, and the delivery is NOT
    marked seen — Jira's retry of the same identifier must re-dispatch
    (mark-after-ack). Proven by handle_ingest being invoked on both
    back-to-back deliveries of the same identifier."""
    ingest_calls: list[int] = []

    async def _fail(ctx, payload, *, source_scope, ingest_mode):
        ingest_calls.append(1)
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr("handlers.ingest.handle_ingest", _fail)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(_issue_payload())
    sig = _sign(_SECRET, body)

    status1, _ = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-transient",
        secret_resolver=lambda: _SECRET,
    )
    status2, _ = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-transient",
        secret_resolver=lambda: _SECRET,
    )
    assert status1 == 500
    assert status2 == 500
    assert len(ingest_calls) == 2, (
        f"handle_ingest called {len(ingest_calls)} times; a 500 wrongly "
        "marked the delivery seen and the retry was dedup-suppressed"
    )


def test_handle_ingest_refused_422_marks_dedup(monkeypatch):
    """Mark-after-ack corollary: a 422 IS an acked outcome (Jira will not
    retry it), so the delivery is marked — a replay is dedup-suppressed.
    handle_ingest is invoked exactly once across two deliveries."""
    from handlers.ingest import _IngestRefused

    ingest_calls: list[int] = []

    async def _refuse(ctx, payload, *, source_scope, ingest_mode):
        ingest_calls.append(1)
        raise _IngestRefused("sensitive_data:phi")

    monkeypatch.setattr("handlers.ingest.handle_ingest", _refuse)
    from types import SimpleNamespace as _SN

    monkeypatch.setattr("context.BicameralContext.from_env", classmethod(lambda cls: _SN()))

    body = _body(_issue_payload())
    sig = _sign(_SECRET, body)

    status1, _ = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-refused-dup",
        secret_resolver=lambda: _SECRET,
    )
    status2, msg2 = handle(
        body=body,
        signature_header=sig,
        delivery_identifier="wh-refused-dup",
        secret_resolver=lambda: _SECRET,
    )
    assert status1 == 422
    assert status2 == 200
    assert msg2 == "duplicate"
    assert len(ingest_calls) == 1


# ── 11. handle: missing webhook secret → 500, no secret leak ────────────────


def test_handle_missing_secret_returns_500_no_secret_leak():
    """A receiver with no configured secret cannot verify anything — it
    fails closed with 500. The message must carry setup guidance only,
    never a secret value."""
    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-1",
        secret_resolver=lambda: "",
    )
    assert status == 500
    assert "webhook_secret" in msg
    # The message names the key, not any value — pin that no plausible
    # secret value leaked into the operator-facing message.
    assert _SECRET not in msg


def test_handle_secret_resolver_exception_returns_500():
    """If the secret resolver itself raises, the receiver returns 500
    (transient — Jira retries) and the exception is not a secret."""

    def _boom() -> str:
        raise RuntimeError("keyring backend unavailable")

    body = _body(_issue_payload())
    status, msg = handle(
        body=body,
        signature_header=_sign(_SECRET, body),
        delivery_identifier="wh-1",
        secret_resolver=_boom,
    )
    assert status == 500
    assert "secret lookup failed" in msg
    assert _SECRET not in msg
