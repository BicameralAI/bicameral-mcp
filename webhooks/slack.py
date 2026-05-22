"""Slack Events API webhook handler (#337 cycle 6).

Slack's webhook contract differs from GitHub's:
- Signature header: ``X-Slack-Signature`` of form ``v0=<hex>``
- Timestamp header: ``X-Slack-Request-Timestamp`` (epoch seconds, str)
- Signature payload: ``v0:{timestamp}:{raw_body}`` (NOT just the body)
- Replay defense: timestamp-based, reject if > 5 minutes stale

The timestamped signature is a stronger replay defense than GitHub's
delivery-UUID dedup: an attacker who captures a signed payload can
only replay it within the 5-minute window. We still dedup on the event
ID for ``event_callback`` requests (Slack retries failed deliveries up
to 3× over 1 hour; same envelope can arrive twice within the 5-min
window).

URL verification handshake: when an operator first registers the
webhook URL in Slack's app config, Slack POSTs ``{"type":"url_verification",
"challenge":"<random>"}``. We must echo ``{"challenge":"<random>"}``
within 3 seconds. The challenge response is the ONLY non-trivial 200
payload we emit — everything else is a status line.

Event handling (cycle 6 minimum):
- ``url_verification`` → 200 with ``{"challenge": <echo>}`` body
- ``event_callback`` with ``event.type == "message"`` (and not a bot
  subtype) → normalize via Phase 4a thread-fetch, ingest passively
- Any other ``event_callback`` subtype → 200 + "ignored"
- ``app_uninstalled`` / ``tokens_revoked`` → 200, log, no action
  (operator-side problem, our auth token is now dead anyway)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

# Slack: reject signed requests where the timestamp is more than this many
# seconds stale (per Slack docs § Verifying requests). 5 minutes is the
# canonical value — long enough for clock skew between Slack and operator,
# tight enough to bound replay window.
_MAX_TIMESTAMP_SKEW_SECONDS = 5 * 60


class WebhookVerificationError(Exception):
    """Raised when signature OR timestamp verification fails."""


def verify_signature(
    *,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str,
    now: float | None = None,
) -> None:
    """Verify Slack's v0 signing scheme + reject stale timestamps.

    Order:
    1. Missing timestamp / signature header → reject (no replay window
       to verify against).
    2. Timestamp parses as int.
    3. Timestamp not stale (|now - ts| <= 5 minutes). Done BEFORE HMAC so
       a constant-time-attacker can't probe the secret against an
       arbitrarily-old timestamp.
    4. Signature header has ``v0=`` prefix.
    5. HMAC-SHA256(secret, ``v0:{timestamp}:{body}``).
    6. Constant-time hex compare via ``hmac.compare_digest``.

    ``now`` is injectable for tests; defaults to ``time.time()``.

    Raises:
        WebhookVerificationError: every failure mode. Operator-facing
            detail is in the exception message; HTTP layer responds 401.
    """
    if not timestamp_header:
        raise WebhookVerificationError("missing X-Slack-Request-Timestamp header")
    if not signature_header:
        raise WebhookVerificationError("missing X-Slack-Signature header")
    if not secret:
        raise WebhookVerificationError("no webhook_secret configured for this source")
    # M1: strict digit-only parse — rejects whitespace, +, -, leading zeros
    # via the digit check before int() so future code that relies on the
    # parsed int form sees only canonical-shaped input.
    if not timestamp_header.isdigit():
        raise WebhookVerificationError(
            f"X-Slack-Request-Timestamp not strict decimal: {timestamp_header[:32]!r}"
        )
    ts = int(timestamp_header)
    current = time.time() if now is None else now
    if abs(current - ts) > _MAX_TIMESTAMP_SKEW_SECONDS:
        raise WebhookVerificationError(
            f"timestamp stale: |now - {ts}| > {_MAX_TIMESTAMP_SKEW_SECONDS}s"
        )
    if not signature_header.startswith("v0="):
        raise WebhookVerificationError(
            f"signature header malformed: expected 'v0=<hex>', got {signature_header[:32]!r}"
        )
    provided_hex = signature_header[len("v0=") :]
    # Slack's signature basestring concatenates the version tag, the
    # timestamp, and the raw request body, separated by colons.
    basestring = b"v0:" + timestamp_header.encode("ascii") + b":" + body
    expected_hex = hmac.new(
        secret.encode("utf-8"), msg=basestring, digestmod=hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided_hex, expected_hex):
        raise WebhookVerificationError("signature mismatch")


def handle(
    *,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
) -> tuple[int, str, str]:
    """Process one Slack Events API delivery.

    Returns ``(http_status, response_body, content_type)`` — content type
    is explicit (H1 review finding) so the server doesn't have to guess
    by inspecting the body. Most responses are ``text/plain``; URL
    verification handshake is ``application/json``.

    Slack expects the URL-verification response (the challenge echo)
    within 3 seconds of the POST.
    """
    _TEXT = "text/plain; charset=utf-8"
    _JSON = "application/json"

    try:
        from secrets_store import get_secret

        secret = get_secret(source_id="slack", key="webhook_secret") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[slack-webhook] secret lookup failed: {exc}", file=sys.stderr)
        return 500, f"secret lookup failed: {exc}", _TEXT

    try:
        verify_signature(
            body=body,
            timestamp_header=timestamp_header,
            signature_header=signature_header,
            secret=secret,
        )
    except WebhookVerificationError as exc:
        print(f"[slack-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}", _TEXT

    # Body parse only AFTER verification.
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[slack-webhook] body not JSON-decodable: {exc}", file=sys.stderr)
        return 400, f"body not JSON: {exc}", _TEXT

    envelope_type = payload.get("type") or ""

    # URL verification handshake: Slack expects {"challenge": <echo>}.
    # Confirmed harmless to replay within the 5-min window — challenge is
    # operator-chosen by Slack at app-config time and surfaced in their
    # admin UI. Replays just yield the same 200 echo.
    if envelope_type == "url_verification":
        challenge = payload.get("challenge") or ""
        if not challenge or not isinstance(challenge, str):
            return 400, "url_verification missing 'challenge' field", _TEXT
        # Echo back as JSON. Byte-exact: Slack literally byte-matches the
        # challenge against the response body.
        return 200, json.dumps({"challenge": challenge}), _JSON

    if envelope_type == "event_callback":
        # B1: Slack always sends event_id on event_callback. Absence is
        # either a provider bug or an attacker stripping the field to
        # bypass dedup — reject explicitly.
        # TODO(multi-tenant): when multi-workspace support lands, namespace
        # the dedup key as ("slack", team_id, event_id) — M3 from review.
        event_id = payload.get("event_id")
        if not event_id or not isinstance(event_id, str):
            print(
                "[slack-webhook] event_callback missing event_id (replay-defense gate); rejecting.",
                file=sys.stderr,
            )
            return 400, "event_callback missing event_id", _TEXT

        # Replay defense layer 2: Slack retries failed deliveries up to 3×
        # over 1 hour. Combined with the timestamp gate (5-min window),
        # any retry within the window must come through dedup.
        from webhooks.dedup import get_dedup_cache

        cache = get_dedup_cache()
        if cache.is_duplicate("slack", event_id):
            print(
                f"[slack-webhook] duplicate event_id {event_id!r} ignored",
                file=sys.stderr,
            )
            return 200, "duplicate", _TEXT
        cache.mark_seen("slack", event_id)

        event = payload.get("event") or {}
        event_type = event.get("type") or ""

        if event_type == "message":
            status, msg = _ingest_message(event)
            return status, msg, _TEXT

        return 200, f"event.type={event_type!r} ignored", _TEXT

    # tokens_revoked / app_uninstalled / etc. — Slack-side state changes
    # we acknowledge but don't ingest.
    return 200, f"envelope type={envelope_type!r} acknowledged", _TEXT


def _ingest_message(event: dict) -> tuple[int, str]:
    """Normalize a Slack message event + push through handle_ingest."""
    # Drop messages that aren't decision-bearing (bot, channel-meta, etc.)
    # by reusing the Phase 4a filter so active + passive + webhook paths
    # share the same definition.
    try:
        from sources.slack.adapter import _is_decision_bearing, normalize_thread_to_payload
    except ImportError as exc:
        print(f"[slack-webhook] adapter import failed: {exc}", file=sys.stderr)
        return 500, "adapter import failed"

    # H2: channel-only policy parity with the polling adapter. Reject DMs
    # (channel_type 'im'/'mpim') AND D-prefix channel IDs. Private channels
    # ('group' channel_type, G-prefix IDs) remain permitted — operator is
    # responsible for which channels the bot is invited to.
    channel_type = event.get("channel_type") or ""
    if channel_type in {"im", "mpim"}:
        return 200, "DM channel type; ignored (channel-only policy)"

    channel = event.get("channel") or ""
    msg_ts = event.get("ts") or ""
    if not channel or not msg_ts:
        return 200, "message missing channel/ts; ignored"
    if channel.upper().startswith("D"):
        return 200, "DM channel ID; ignored (channel-only policy)"

    if not _is_decision_bearing(event):
        return 200, "message not decision-bearing; ignored"

    # Skip thread replies (Slack semantics: thread_ts present and != ts);
    # mirrors the polling adapter's behavior.
    thread_ts = event.get("thread_ts") or ""
    if thread_ts and thread_ts != msg_ts:
        return 200, "thread reply; ignored (top-level + roots only)"

    # M2: single-message list is intentional. normalize_thread_to_payload
    # treats messages[0] as the thread root for title/date derivation;
    # all decision-bearing messages in the list become decisions. For our
    # one-message webhook path that's exactly the right shape.
    payload = normalize_thread_to_payload(
        [event],
        channel=channel,
        thread_url=f"slack://{channel}/{msg_ts}",
    )

    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(ctx, payload, source_scope="slack", ingest_mode="passive")

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        # Same H2 fix as GitHub: hard-gate refusals → 422, no retry.
        print(
            f"[slack-webhook] hard-gate refusal for {channel}#{msg_ts}: {exc.reason}",
            file=sys.stderr,
        )
        return 422, f"refused: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        print(
            f"[slack-webhook] ingest failed for {channel}#{msg_ts}: {exc}",
            file=sys.stderr,
        )
        return 500, f"ingest failed: {exc}"

    return 200, f"ingested {channel}#{msg_ts}"
