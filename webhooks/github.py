"""GitHub webhook handler (#337 cycle 5).

GitHub's webhook contract:
- POST request with JSON body
- ``X-GitHub-Event`` header: event type (``pull_request``, ``issue_comment``, ``ping``, etc.)
- ``X-GitHub-Delivery`` header: per-delivery UUID for dedup
- ``X-Hub-Signature-256`` header: ``sha256=<hex>`` HMAC-SHA256(secret, raw_body)

Verification order:
1. Header presence — missing ``X-Hub-Signature-256`` → 401.
2. Constant-time comparison via ``hmac.compare_digest`` — prevents
   timing-oracle attacks on the secret.
3. Body parse only AFTER signature verifies — never trust unverified
   input for anything more than HMAC input.

Replay defense: GitHub doesn't ship a timestamp header, but
``X-GitHub-Delivery`` is unique per delivery. We dedup on that.
Network-level replay (attacker captures the signed payload and resends
within the dedup TTL) is blocked by the dedup cache; replay beyond the
TTL falls back to TLS as the protection (operator MUST terminate
webhooks behind HTTPS — documented in the inventory).

Event handling (cycle 5 minimum):
- ``pull_request.closed`` with ``pull_request.merged == true`` →
  fetch via Phase 3 active-ingest adapter, ingest passively.
- ``ping`` → 200 (GitHub's "test webhook" event).
- Other events → 200 + "ignored" log (so GitHub doesn't disable the
  webhook for repeated 4xx responses).

The handler never raises into the HTTP layer. Internal failures are
caught and translated to operator-actionable log lines + 500 response
(GitHub will retry on 5xx, which is correct — transient failures
shouldn't lose events).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys


class WebhookVerificationError(Exception):
    """Raised when signature verification fails. Caller maps to HTTP 401."""


def verify_signature(*, body: bytes, signature_header: str | None, secret: str) -> None:
    """Verify the GitHub HMAC-SHA256 signature against ``body``.

    Raises ``WebhookVerificationError`` on any failure mode (missing
    header, malformed format, mismatch). The exception message is
    operator-facing only — it's never returned to the requester
    (the HTTP layer responds with a generic 401).

    Comparison uses ``hmac.compare_digest`` for constant-time equality.
    """
    if not signature_header:
        raise WebhookVerificationError("missing X-Hub-Signature-256 header")
    if not signature_header.startswith("sha256="):
        raise WebhookVerificationError(
            f"signature header malformed: expected 'sha256=<hex>', got {signature_header[:32]!r}"
        )
    provided_hex = signature_header[len("sha256=") :]
    if not secret:
        # Defensive: operator-config bug. Refuse rather than silently
        # accept anything.
        raise WebhookVerificationError("no webhook_secret configured for this source")
    expected_hex = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    # Both sides hex-encoded — same length, ASCII-safe for compare_digest.
    if not hmac.compare_digest(provided_hex, expected_hex):
        raise WebhookVerificationError("signature mismatch")


def handle(
    *,
    event: str,
    delivery_id: str,
    body: bytes,
    signature_header: str | None,
) -> tuple[int, str]:
    """Process one GitHub webhook delivery.

    Returns ``(http_status, log_message)``. The HTTP server lifts the
    status into the response and prints the log message.

    Workflow:
    1. Resolve the webhook_secret from secrets_store.
    2. Verify signature against raw body bytes.
    3. Dedup on (source, delivery_id).
    4. Dispatch event type to the right ingest path.
    5. Catch internal failures, translate to 500 so GitHub retries.

    The function is sync; the HTTP server calls it via
    ``asyncio.to_thread`` so we don't block the event loop on the
    ingest path (which may hit the ledger).
    """
    try:
        from secrets_store import get_secret

        secret = get_secret(source_id="github", key="webhook_secret") or ""
    except Exception as exc:  # noqa: BLE001 — never break the HTTP layer
        print(f"[github-webhook] secret lookup failed: {exc}", file=sys.stderr)
        return 500, f"secret lookup failed: {exc}"

    try:
        verify_signature(body=body, signature_header=signature_header, secret=secret)
    except WebhookVerificationError as exc:
        # Verification failure logs detail server-side, returns generic 401.
        print(f"[github-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}"

    # H1: GitHub always sends X-GitHub-Delivery. Absence is either a
    # provider bug or an attack trying to bypass dedup. Reject explicitly
    # — accepting empty delivery_ids would let an attacker who somehow
    # acquires a valid signed payload (captured pre-TLS, or replayed)
    # replay it infinitely under the dedup radar.
    if not delivery_id:
        print(
            "[github-webhook] missing X-GitHub-Delivery header (replay-defense gate); rejecting.",
            file=sys.stderr,
        )
        return 400, "missing X-GitHub-Delivery"

    # Dedup AFTER verification so an attacker can't poison the cache
    # with unverified delivery_ids.
    from webhooks.dedup import get_dedup_cache

    cache = get_dedup_cache()
    if cache.is_duplicate("github", delivery_id):
        # GitHub treats 200 as ack; we return 200 so the provider stops
        # retrying. The dup is logged for operator visibility.
        print(
            f"[github-webhook] duplicate delivery {delivery_id!r} ignored",
            file=sys.stderr,
        )
        return 200, "duplicate"
    cache.mark_seen("github", delivery_id)

    # Parse JSON body. Verification already confirmed the bytes are
    # what GitHub signed, but the bytes might still not be JSON-decodable
    # (e.g. operator misconfigured GitHub to send something weird).
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[github-webhook] body not JSON-decodable: {exc}", file=sys.stderr)
        return 400, f"body not JSON: {exc}"

    if event == "ping":
        return 200, "ping ack"

    if event == "pull_request":
        action = payload.get("action") or ""
        pr = payload.get("pull_request") or {}
        if action == "closed" and pr.get("merged") is True:
            url = pr.get("html_url") or ""
            if not url:
                return 200, "pull_request closed-merged but no html_url; ignored"
            return _ingest_via_active_path(url)
        return 200, f"pull_request action={action!r} ignored"

    # Any other event: 200 + "ignored" so GitHub doesn't disable the webhook.
    return 200, f"event={event!r} ignored"


def _ingest_via_active_path(url: str) -> tuple[int, str]:
    """Fetch + ingest using the Phase 3 active adapter.

    Failures here return 500 so GitHub retries (transient API errors
    should not silently drop the event).
    """
    try:
        from sources.github.adapter import GitHubAdapter
    except ImportError as exc:
        print(f"[github-webhook] adapter import failed: {exc}", file=sys.stderr)
        return 500, "adapter import failed"

    try:
        payload = GitHubAdapter().fetch_active(url)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[github-webhook] fetch_active failed for {url!r}: {exc}",
            file=sys.stderr,
        )
        return 500, f"fetch_active failed: {exc}"

    # H2: distinguish hard-gate refusals (secret/PHI/PAN) from transient
    # failures. Hard-gate refusals must NOT trigger GitHub's retry loop —
    # the payload would re-trigger the same refusal AND linger in retry
    # logs N times, each carrying the offending content. Return 422 to
    # tell GitHub this is permanent: don't retry.
    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(ctx, payload, source_scope="github", ingest_mode="passive")

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        # Hard-gate refusal (secret / PHI / PAN / malformed). Don't echo
        # the payload or even the reason detail into the HTTP response —
        # provider may surface it in their UI. Operator audit-log already
        # captured the detail via the standard ingest-refusal emit.
        print(
            f"[github-webhook] hard-gate refusal for {url!r}: {exc.reason}",
            file=sys.stderr,
        )
        return 422, f"refused: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        print(
            f"[github-webhook] ingest failed for {url!r}: {exc}",
            file=sys.stderr,
        )
        return 500, f"ingest failed: {exc}"

    return 200, f"ingested {url}"
