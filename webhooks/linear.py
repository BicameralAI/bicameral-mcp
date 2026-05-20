"""Linear webhook handler (#337 cycle 7).

Linear's webhook contract:

- Header ``Linear-Signature``: HMAC-SHA256 of the raw request body, hex-encoded.
  No ``v0=`` style prefix; the header value IS the hex digest.
- Header ``Linear-Delivery``: per-delivery UUID. Used for retry dedup.
- Header ``Linear-Event``: high-level event class (``Issue``, ``Comment``,
  ``Project``, ``Reaction``, etc.). Routing key.
- Body: JSON envelope with ``{action, type, data, url, createdAt,
  webhookId, webhookTimestamp}``. ``action`` ∈ {``create``, ``update``,
  ``remove``}; ``data`` is the entity payload (not the GraphQL shape used
  by the active-fetch adapter, but contains the same load-bearing fields:
  ``id``, ``identifier``, ``title``, ``description`` for Issue;
  ``id``, ``body``, ``user``, ``issue`` for Comment).

## Replay defense

Linear's docs recommend two layers:

1. ``webhookTimestamp`` in the body (epoch ms). Reject if more than
   60 seconds stale — tighter than Slack's 5-min window because Linear
   doesn't expose this as a header and operators can't easily tune.
2. ``Linear-Delivery`` dedup cache (24h TTL, shared with GitHub / Slack).

Order: timestamp gate first (constant-time-attack mitigation, same
reasoning as Slack), HMAC second, dedup third.

## Event coverage (cycle 7 minimum)

- ``Issue`` create/update with non-empty description → ingest as decision.
- ``Comment`` create with non-empty body → ingest as decision.
- ``Issue`` remove / ``Comment`` remove → 200 ack, no ingest.
- Anything else (``Project``, ``Reaction``, ``Cycle``, ...) → 200 ack,
  documented as not-yet-supported.

Webhook does NOT round-trip to the Linear API; the body fields are
sufficient for v0. A future enhancement can fetch the full issue shape
when the webhook event lacks decision-bearing context (e.g. issue
update where ``data`` only carries the changed fields).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

# Linear: 60-second skew tolerance. Tighter than Slack (5 min) because
# Linear's payload-side timestamp is not operator-tunable and Linear's
# infrastructure is internal-network-tight; a 60s gate still tolerates
# normal clock skew without leaving a wide replay window.
_MAX_TIMESTAMP_SKEW_SECONDS = 60


class WebhookVerificationError(Exception):
    """Raised when signature OR timestamp verification fails."""


def verify_signature(
    *,
    body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """Verify Linear's webhook signature against raw body bytes.

    Order:
    1. Missing signature header or secret → reject.
    2. Length sanity check (constant-time-safe: same path for all
       wrong lengths).
    3. HMAC-SHA256(secret, body) compared constant-time against the
       header value (hex). ``compare_digest`` itself rejects non-hex
       characters via byte-mismatch — no separate charset check
       (M1 review finding: pre-HMAC charset scan adds a position-of-
       first-bad-byte timing oracle for zero functional benefit).

    Timestamp staleness gate is now a SEPARATE function
    (:func:`check_timestamp_skew`) that runs AFTER signature verify,
    so we never parse / extract from unauthenticated bytes.

    Raises:
        WebhookVerificationError: every failure mode.
    """
    if not signature_header:
        raise WebhookVerificationError("missing Linear-Signature header")
    if not secret:
        raise WebhookVerificationError("no webhook_secret configured for this source")
    if len(signature_header) != 64:
        raise WebhookVerificationError(
            f"Linear-Signature not 64 chars: got {len(signature_header)}"
        )
    expected_hex = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature_header.lower(), expected_hex):
        raise WebhookVerificationError("signature mismatch")


def check_timestamp_skew(body_timestamp_ms: int, *, now: float | None = None) -> None:
    """Reject if ``body_timestamp_ms`` is more than 60s away from now.

    Runs AFTER :func:`verify_signature` per H1 review finding —
    operates on authenticated values only.

    Raises:
        WebhookVerificationError: when |now - ts| > 60s in either
            direction.
    """
    current_ms = int((time.time() if now is None else now) * 1000)
    if abs(current_ms - body_timestamp_ms) > _MAX_TIMESTAMP_SKEW_SECONDS * 1000:
        raise WebhookVerificationError(
            f"webhookTimestamp stale: |now - {body_timestamp_ms}ms| > "
            f"{_MAX_TIMESTAMP_SKEW_SECONDS}s"
        )


def handle(
    *,
    body: bytes,
    event_header: str | None,
    delivery_header: str | None,
    signature_header: str | None,
) -> tuple[int, str]:
    """Process one Linear webhook delivery.

    Returns ``(http_status, response_body)`` — Linear doesn't require
    a JSON response shape, so plain text is fine for every code path.
    """
    try:
        from secrets_store import get_secret

        secret = get_secret(source_id="linear", key="webhook_secret") or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[linear-webhook] secret lookup failed: {exc}", file=sys.stderr)
        return 500, f"secret lookup failed: {exc}"

    # H1 review finding: verify HMAC against raw bytes BEFORE any
    # JSON parsing. Slack and GitHub do the same — Linear no longer
    # diverges. Unauthenticated bytes never reach json.loads.
    try:
        verify_signature(
            body=body,
            signature_header=signature_header,
            secret=secret,
        )
    except WebhookVerificationError as exc:
        print(f"[linear-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}"

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[linear-webhook] body not JSON-decodable: {exc}", file=sys.stderr)
        return 400, f"body not JSON: {exc}"

    raw_ts = payload.get("webhookTimestamp")
    body_timestamp_ms: int | None = None
    if isinstance(raw_ts, int):
        body_timestamp_ms = raw_ts
    elif isinstance(raw_ts, str) and all(c in "0123456789" for c in raw_ts) and raw_ts:
        # Strict ASCII-decimal: avoids str.isdigit() accepting Arabic-
        # Indic / other Unicode digits that int() then parses.
        body_timestamp_ms = int(raw_ts)

    # H2 review finding: closing both replay channels in one go.
    # Require at least ONE of Linear-Delivery (dedup) OR
    # webhookTimestamp (staleness gate) to be present. Linear has
    # emitted Linear-Delivery on every webhook since v1 (2020) — there
    # is no "older config" in the wild that omits it. Both-missing is
    # either a provider bug or an attacker stripping replay defenses.
    if not delivery_header and body_timestamp_ms is None:
        print(
            "[linear-webhook] Linear-Delivery AND webhookTimestamp both missing; rejecting "
            "(no replay defense possible)",
            file=sys.stderr,
        )
        return 400, "Linear-Delivery and webhookTimestamp both missing; cannot dedup"

    # Timestamp staleness gate runs AFTER signature verify (H1 fix).
    if body_timestamp_ms is not None:
        try:
            check_timestamp_skew(body_timestamp_ms)
        except WebhookVerificationError as exc:
            print(f"[linear-webhook] verification failed: {exc}", file=sys.stderr)
            return 401, f"verification failed: {exc}"

    # Replay defense layer 2: delivery-id dedup. Linear retries failed
    # deliveries up to 5× over ~30 min; the dedup window covers that.
    if delivery_header:
        from webhooks.dedup import get_dedup_cache

        cache = get_dedup_cache()
        if cache.is_duplicate("linear", delivery_header):
            print(
                f"[linear-webhook] duplicate Linear-Delivery {delivery_header!r} ignored",
                file=sys.stderr,
            )
            return 200, "duplicate"
        cache.mark_seen("linear", delivery_header)

    action = payload.get("action") or ""
    # H3 review finding: header vs body.type cross-check. The signature
    # covers the body but NOT the header — an attacker with a leaked
    # secret could otherwise route a malicious Issue body as "Reaction"
    # (free 200-ack-no-ingest, payload staging) or route a Reaction
    # body as "Issue" (ingest as if it were an Issue). Requiring
    # equality when both are present closes both directions.
    header_type = (event_header or "").strip()
    body_type = (payload.get("type") or "").strip()
    if header_type and body_type and header_type != body_type:
        print(
            f"[linear-webhook] Linear-Event header={header_type!r} != body.type={body_type!r}; "
            "rejecting (signed-body vs unsigned-header mismatch)",
            file=sys.stderr,
        )
        return 400, f"Linear-Event header={header_type!r} != body.type={body_type!r}"
    event_type = header_type or body_type

    if event_type == "Issue":
        return _ingest_issue(payload, action)
    if event_type == "Comment":
        return _ingest_comment(payload, action)

    return 200, f"event type={event_type!r} acknowledged (not yet ingested)"


def _ingest_issue(payload: dict, action: str) -> tuple[int, str]:
    """Normalize an Issue create/update event and ingest as a decision."""
    if action == "remove":
        # We don't propagate deletes to the ledger — the append-only
        # contract means past decisions persist by design. Operators
        # who need erasure use the #221 / GDPR path, not this.
        return 200, "Issue remove acknowledged (no ledger mutation)"

    data = payload.get("data") or {}
    identifier = (data.get("identifier") or "").strip()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not identifier or not title:
        return 200, "Issue missing identifier/title; ignored"
    if not description:
        # Title-only Issue updates land here on first create. We don't
        # want to seed the ledger with empty-description rows, so skip.
        return 200, f"Issue {identifier} has no description; ignored"

    issue_url = data.get("url") or payload.get("url") or ""

    # Build the payload in the same shape as the active adapter so
    # downstream handle_ingest behavior is identical for active vs
    # webhook ingest.
    norm = {
        "query": title,
        "source": "linear",
        "title": identifier,
        "date": data.get("updatedAt") or data.get("createdAt") or "",
        "participants": _participants_from_data(data),
        "decisions": [{"description": description, "title": identifier}],
    }

    return _dispatch_to_ingest(norm, label=f"Issue {identifier} ({issue_url})")


def _ingest_comment(payload: dict, action: str) -> tuple[int, str]:
    """Normalize a Comment create event and ingest as a decision."""
    if action != "create":
        # Edits and removes — same reasoning as Issue remove.
        return 200, f"Comment {action} acknowledged (no ledger mutation)"

    data = payload.get("data") or {}
    body_text = (data.get("body") or "").strip()
    if not body_text:
        return 200, "Comment body empty; ignored"

    comment_id = data.get("id") or ""
    issue = data.get("issue") or {}
    identifier = (issue.get("identifier") or "").strip()
    if not identifier:
        return 200, "Comment missing issue.identifier; ignored"

    decision_title = f"{identifier}#comment-{comment_id}" if comment_id else identifier
    norm = {
        "query": (issue.get("title") or identifier),
        "source": "linear",
        "title": identifier,
        "date": data.get("updatedAt") or data.get("createdAt") or "",
        "participants": _participants_from_data(data),
        "decisions": [{"description": body_text, "title": decision_title}],
    }

    return _dispatch_to_ingest(norm, label=f"Comment on {identifier} ({payload.get('url') or ''})")


def _participants_from_data(data: dict) -> list[str]:
    """Best-effort participant extraction from a webhook body.

    Linear's webhook payloads are shallower than the GraphQL response
    — they include the actor (``user`` for Comment, sometimes
    ``assignee`` on Issue) but not the full thread. We collect what's
    present; the active-fetch path remains the authoritative source
    when full participant context is required.
    """
    out: list[str] = []
    seen: set[str] = set()
    for field in ("user", "assignee", "creator"):
        person = data.get(field)
        if not isinstance(person, dict):
            continue
        email = (person.get("email") or "").strip()
        if email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _dispatch_to_ingest(norm: dict, *, label: str) -> tuple[int, str]:
    """Run handle_ingest with the normalized payload."""
    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(ctx, norm, source_scope="linear", ingest_mode="passive")

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        print(
            f"[linear-webhook] hard-gate refusal for {label}: {exc.reason}",
            file=sys.stderr,
        )
        return 422, f"refused: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        print(f"[linear-webhook] ingest failed for {label}: {exc}", file=sys.stderr)
        return 500, f"ingest failed: {exc}"

    return 200, f"ingested {label}"
