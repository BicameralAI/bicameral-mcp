"""Jira Cloud webhook handler (#337 Phase B).

Jira's webhook contract (admin-UI registration path — Jira Settings →
System → WebHooks, with a shared secret):

- POST request with a JSON body.
- ``X-Hub-Signature`` header: ``sha256=<hex>`` HMAC-SHA256(secret, raw_body).
  **Note the header name is** ``X-Hub-Signature`` — *not* GitHub's
  ``X-Hub-Signature-256``. Same algorithm, different header name
  (Jira follows the WebSub ``method=signature`` convention).
- ``X-Atlassian-Webhook-Identifier`` header: an identifier unique within a
  Jira Cloud tenant and stable across retries — the idempotency key for
  duplicate-delivery filtering.
- ``webhookEvent`` body field: the event id (``jira:issue_created`` etc.).

The webhook secret (the third Jira ``secrets_store`` key alongside Phase A's
``api_email`` / ``api_token``) is referred to as the ``webhook_secret``. It
is read via ``secrets_store.get_secret(source_id="jira", key="webhook_secret")``,
is NEVER logged, and never appears in an exception or HTTP-response message.

Verification order (mirrors ``webhooks/github.py``):
1. Resolve the webhook secret. A receiver with no configured secret cannot
   verify anything, so it fails CLOSED — missing secret → 500 with a
   setup-guidance message that carries no secret value.
2. Verify the HMAC signature over the **exact raw request bytes** — never a
   re-serialized body (re-encoding changes whitespace / key order and breaks
   the digest). Missing / malformed / mismatched → 401 (Jira does not retry
   a 401).
3. Require ``X-Atlassian-Webhook-Identifier``. Missing/empty → 400 (Jira does
   not retry a 400; an empty delivery id would also let a captured payload
   bypass dedup).
4. Dedup on the delivery identifier — single bucket (the identifier is
   globally unique per tenant, so no per-channel partition is needed).
   Dedup runs AFTER verification so an unverified delivery cannot poison the
   cache.
5. JSON-parse the body (verification already confirmed the bytes), dispatch
   on ``webhookEvent``.

Self-contained payload: unlike the GitHub / Drive receivers, a Jira
issue/comment webhook carries the full ``issue`` object inline (the same
shape the REST API returns with no expand params). Phase B normalizes that
inline object via Phase A's ``sources.jira.adapter.normalize_issue_to_payload``
(a module-level function — the ``JiraAdapter`` class calls it internally on
the active path) — there is no network round-trip and no SSRF surface.

Mark-after-ack (the cycle-9d Drive precedent): the delivery is marked seen
only once an acked outcome (a 200 or a 422) has been decided. A 500 path is
left UNMARKED so Jira's retry re-dispatches the delivery.

Event handling (Phase B):
- ``jira:issue_created`` / ``jira:issue_updated`` / ``comment_created`` /
  ``comment_updated`` → normalize the inline ``issue`` object and ingest.
- ``jira:issue_deleted`` / ``comment_deleted`` / every other ``webhookEvent``
  → 200 acknowledged, no ingest (append-only contract — deletes are not
  propagated to the ledger).

Status-code contract (``docs/vendor/jira/webhooks.md`` §4):
- 200 — ack (success / dedup hit / ignored event / deterministic ingest
  failure). Jira will not retry a 2xx.
- 401 — signature verification failed. Jira does not retry a 401.
- 400 — missing/empty ``X-Atlassian-Webhook-Identifier`` or a body that is
  not JSON. Jira does not retry a 400.
- 422 — hard-gate refusal (``_IngestRefused`` — PHI/secret/PAN). Jira does
  not retry a 422.
- 500 — transient receiver failure (secret lookup error, transient ingest
  failure). Jira WILL retry, which is desired.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys


class WebhookVerificationError(Exception):
    """Raised when signature verification fails. Caller maps to HTTP 401."""


def verify_signature(*, body: bytes, signature_header: str | None, secret: str) -> None:
    """Verify the Jira HMAC-SHA256 signature against ``body``.

    Jira signs with the ``X-Hub-Signature`` header, value ``sha256=<hex>``,
    HMAC-SHA256 over the raw request body keyed by the webhook secret.

    Raises ``WebhookVerificationError`` on any failure mode (missing
    header, malformed format, missing secret, digest mismatch). The
    exception message is operator-facing only — it never carries the
    secret value, and the HTTP layer responds with a generic 401.

    Comparison uses ``hmac.compare_digest`` for constant-time equality —
    no timing oracle on the secret.
    """
    if not signature_header:
        raise WebhookVerificationError("missing X-Hub-Signature header")
    if not signature_header.isascii():
        # Defense-in-depth (mirrors webhooks/google_drive.py's ASCII gate):
        # the HTTP layer already rejects non-ASCII headers, but a non-ASCII
        # provided-hex would make hmac.compare_digest raise TypeError rather
        # than failing closed as a WebhookVerificationError. Reject here so a
        # non-HTTP caller (a future CLI replay tool) still fails closed → 401.
        raise WebhookVerificationError("non-ASCII X-Hub-Signature header")
    if not signature_header.startswith("sha256="):
        raise WebhookVerificationError(
            f"signature header malformed: expected 'sha256=<hex>', got {signature_header[:32]!r}"
        )
    provided_hex = signature_header[len("sha256=") :]
    if not secret:
        # Defensive: operator-config bug. Refuse rather than silently
        # accept anything signed with an empty key.
        raise WebhookVerificationError("no webhook_secret configured for this source")
    expected_hex = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    # Both sides hex-encoded — same length, ASCII-safe for compare_digest.
    if not hmac.compare_digest(provided_hex.lower(), expected_hex):
        raise WebhookVerificationError("signature mismatch")


# Events that carry a decision-bearing inline ``issue`` object and trigger
# ingest. Comment events also carry the inline ``issue`` (the comment lives
# on the issue's comment page) — Phase B normalizes the same inline object
# for all four; comment-event-specific shaping is a Phase C refinement.
_INGEST_EVENTS = frozenset(
    {
        "jira:issue_created",
        "jira:issue_updated",
        "comment_created",
        "comment_updated",
    }
)


def _resolve_secret() -> str:
    """Default webhook-secret resolver — reads from ``secrets_store``.

    Returns the secret string ("" when unset). Kept as a module function
    so ``handle`` can inject a test stub without monkeypatching the
    ``secrets_store`` module at import time.
    """
    from secrets_store import get_secret

    return get_secret(source_id="jira", key="webhook_secret") or ""


def handle(
    *,
    body: bytes,
    signature_header: str | None,
    delivery_identifier: str | None,
    secret_resolver=None,
) -> tuple[int, str]:
    """Process one Jira webhook delivery.

    Returns ``(http_status, log_message)``. The HTTP server lifts the
    status into the response and prints the log message.

    ``secret_resolver`` is an injectable zero-arg callable returning the
    webhook secret string; it defaults to :func:`_resolve_secret`
    (``secrets_store``). Tests pass a stub to avoid touching the keyring.

    The function is sync; the HTTP server calls it via
    ``asyncio.to_thread`` so the ingest path does not block the event
    loop — same pattern as the peer receivers.
    """
    resolver = secret_resolver if secret_resolver is not None else _resolve_secret
    try:
        secret = resolver() or ""
    except Exception as exc:  # noqa: BLE001 — never break the HTTP layer
        # Note: the secret value itself never reaches this branch — only
        # the lookup-failure exception, which carries no secret.
        print(f"[jira-webhook] secret lookup failed: {exc}", file=sys.stderr)
        return 500, f"secret lookup failed: {exc}"

    if not secret:
        # Fail closed: a receiver with no configured secret cannot verify
        # any delivery. Surface setup guidance — never a secret value.
        print(
            "[jira-webhook] no webhook_secret configured for source 'jira'; "
            "refusing to accept unverifiable deliveries. Configure it via "
            "secrets_store source_id='jira' key='webhook_secret' to match the "
            "secret set in Jira's webhook configuration.",
            file=sys.stderr,
        )
        return 500, "webhook_secret not configured for source 'jira'"

    # Verify the signature over the exact raw bytes BEFORE any json.loads.
    try:
        verify_signature(body=body, signature_header=signature_header, secret=secret)
    except WebhookVerificationError as exc:
        # Verification failure logs detail server-side, returns generic 401.
        print(f"[jira-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}"

    # Jira always sends X-Atlassian-Webhook-Identifier. Absence is a
    # provider bug or an attack trying to bypass dedup — accepting an
    # empty identifier would let a captured signed payload replay
    # infinitely under the dedup radar.
    if not delivery_identifier:
        print(
            "[jira-webhook] missing X-Atlassian-Webhook-Identifier header "
            "(replay-defense gate); rejecting.",
            file=sys.stderr,
        )
        return 400, "missing X-Atlassian-Webhook-Identifier"

    # Dedup AFTER verification so an attacker cannot poison the cache with
    # unverified delivery identifiers. Single bucket — the identifier is
    # globally unique per tenant, no per-channel partition.
    from webhooks.dedup import get_dedup_cache

    cache = get_dedup_cache()
    if cache.is_duplicate("jira", delivery_identifier):
        print(
            f"[jira-webhook] duplicate delivery {delivery_identifier!r} ignored",
            file=sys.stderr,
        )
        return 200, "duplicate"

    # Parse the JSON body. Verification already confirmed the bytes are
    # what Jira signed, but the bytes might still not be JSON-decodable.
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[jira-webhook] body not JSON-decodable: {exc}", file=sys.stderr)
        # 400, not marked seen — a malformed body is deterministic, but a
        # mark here is harmless either way; consistency with mark-after-ack
        # keeps the rule simple: only 200/422 mark.
        return 400, f"body not JSON: {exc}"

    if not isinstance(payload, dict):
        return 400, "body not a JSON object"

    event = ""
    raw_event = payload.get("webhookEvent")
    if isinstance(raw_event, str):
        event = raw_event.strip()

    if event in _INGEST_EVENTS:
        result = _ingest_issue(payload, event, delivery_identifier)
    else:
        # jira:issue_deleted / comment_deleted / any other event →
        # acknowledged, no ingest (append-only contract).
        print(
            f"[jira-webhook] delivery {delivery_identifier!r} event "
            f"{event!r} acknowledged (no ingest)",
            file=sys.stderr,
        )
        result = (200, f"event={event!r} acknowledged (no ingest)")

    # Mark-after-ack: mark the delivery seen only once an acked outcome
    # (200 or 422 — Jira will not retry either) is decided. A 500 path is
    # left unmarked so Jira's retry re-dispatches the delivery.
    if result[0] in (200, 422):
        cache.mark_seen("jira", delivery_identifier)
    return result


def _ingest_issue(payload: dict, event: str, delivery_identifier: str) -> tuple[int, str]:
    """Normalize the webhook payload's inline ``issue`` object and ingest.

    The Jira issue/comment webhook carries the full ``issue`` object inline
    (REST shape, no expand), so Phase B normalizes it directly — no network
    round-trip. ``comment_*`` events carry the same inline ``issue`` (the
    comment lives on the issue's comment page).

    Failure posture:
    - missing/empty ``issue`` or its ``key`` → 200 acknowledged, no ingest
      (a malformed payload — not worth a retry).
    - ``_IngestRefused`` (hard gate: PHI/secret/PAN) → 422 (Jira does not
      retry; the gate's own audit-log emit is the operator-visibility
      surface — the payload content is not echoed).
    - any other ``Exception`` (transient ledger error, etc.) → 500 so Jira
      retries; the operator's environment recovers if the failure was
      transient.
    """
    issue = payload.get("issue")
    if not isinstance(issue, dict) or not issue:
        print(
            f"[jira-webhook] delivery {delivery_identifier!r} event {event!r} "
            "missing inline 'issue' object; acking without ingest",
            file=sys.stderr,
        )
        return 200, f"event={event!r} acknowledged (no issue object)"

    issue_key = issue.get("key")
    if not isinstance(issue_key, str) or not issue_key.strip():
        print(
            f"[jira-webhook] delivery {delivery_identifier!r} event {event!r} "
            "issue payload missing 'key'; acking without ingest",
            file=sys.stderr,
        )
        return 200, f"event={event!r} acknowledged (issue missing key)"
    issue_key = issue_key.strip()

    try:
        # Phase A ships ``normalize_issue_to_payload`` as a module-level
        # function in ``sources/jira/adapter.py`` (the ``JiraAdapter``
        # class calls it internally). Phase B reuses it directly on the
        # webhook's inline ``issue`` object — no network, no ``fetch_active``.
        from sources.jira.adapter import normalize_issue_to_payload

        ingest_payload = normalize_issue_to_payload(issue, issue_key)
    except Exception as exc:  # noqa: BLE001
        # Normalization is pure (no network); a failure here is a payload-
        # shape surprise. Treat as transient — operator visibility via the
        # retry storm beats silent loss.
        print(
            f"[jira-webhook] payload normalization failed for {issue_key!r} "
            f"(delivery={delivery_identifier!r}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 500, f"normalization failed for {issue_key!r}; Jira will retry"

    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(ctx, ingest_payload, source_scope="jira", ingest_mode="passive")

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        # Hard-gate refusal (secret / PHI / PAN). Don't echo the payload
        # content into the HTTP response — only the reason category. The
        # gate's own audit-log emit captured the detail. 422 → Jira does
        # not retry, so the offending content does not linger in retry logs.
        print(
            f"[jira-webhook] hard-gate refusal for {issue_key!r} "
            f"(delivery={delivery_identifier!r}): {exc.reason}",
            file=sys.stderr,
        )
        return 422, f"refused: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        # Generic post-normalize ingest failure (ledger error, etc.).
        # 500 so Jira retries — a transient failure self-heals.
        print(
            f"[jira-webhook] ingest failed for {issue_key!r} "
            f"(delivery={delivery_identifier!r}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 500, f"ingest failed for {issue_key!r}; Jira will retry"

    return 200, f"event={event!r} ingested {issue_key}"
