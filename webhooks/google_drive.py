"""Google Drive Push Notifications handler (#337 cycle 9).

Drive's webhook contract is FUNDAMENTALLY DIFFERENT from the other
three providers we receive from (GitHub, Slack, Linear):

- **No body signature.** The notification body is empty for ``files``
  watches and a tiny envelope (``{"kind": "drive#changes"}``) for
  ``changes`` watches — there is nothing for the provider to HMAC.
- **No replay-defense primitive from the provider side.** Google does
  not send a timestamp header we can stale-gate, and
  ``X-Goog-Message-Number`` is documented as non-sequential.
- **Notification is a TRIGGER, not a PAYLOAD.** The handler learns
  "something changed in this channel" — to learn WHAT changed, the
  caller must follow up with ``files.get`` or ``changes.list``.

Authenticity therefore reduces to a three-way match against the
:class:`ChannelRegistry` row we wrote at ``channels.watch`` time:

1. ``X-Goog-Channel-Id`` — known channel?
2. ``X-Goog-Channel-Token`` — constant-time equality with the stored
   token.
3. ``X-Goog-Resource-Id`` — equals the resource_id Google returned
   in the ``channels.watch`` response (NOT the channel_id). This is
   the "signed-body / unsigned-header" cross-check analog from cycle
   7 (Linear H3 finding): the channel-id and token alone could be
   replayed across resources if Google issued the same ``id`` for
   two different watches, but the resource-id binds the message to
   the specific watched file.

See ``docs/research-brief-google-drive-push-notifications-2026-05-20.md``
for the full threat-model write-up.

## Cycle 9 scope

v0 handles two paths:

- ``X-Goog-Resource-State == "sync"`` (the first message) → 200 +
  log; do NOT fetch anything. Per Drive's docs: "safe to ignore."
- Any other state (``add``, ``remove``, ``update``, ``trash``,
  ``untrash``, ``change``) → 200 + log a "channel dirty" marker.
  The actual ``files.get`` follow-up that consumes the dirty marker
  is deferred to cycle 9b.

Returning 200 fast (without waiting for the follow-up fetch) is
deliberate: Drive's retry posture penalizes slow responses, and the
``changes.list`` path is rate-limited per OAuth principal — a
synchronous fetch from inside the webhook handler would amplify
Drive-side traffic spikes into our own quota burn.
"""

from __future__ import annotations

import hmac
import sys


class WebhookVerificationError(Exception):
    """Raised when a notification fails the three-way match."""


def verify_notification(
    *,
    channel_id: str | None,
    channel_token: str | None,
    resource_id: str | None,
    registry=None,
) -> None:
    """Verify the three required Drive notification headers against
    the channel registry.

    Order:
    1. All three headers present (channel_id, channel_token,
       resource_id). Missing any → reject.
    2. Channel-id lookup. Unknown channel → reject.
    3. Constant-time token compare via :func:`hmac.compare_digest`.
       Tokens are operator-set strings of variable length;
       compare_digest's length-mismatch fast-path leaks length, but
       length is not the secret here (the token value is).
    4. Resource-id equality (constant-time). An attacker who has
       learned (channel_id, token) but not the matching resource_id
       still cannot pass — and we DO log a divergent resource_id
       loudly so operators see lateral-movement attempts.

    ``registry`` defaults to the process singleton from
    :mod:`sources.google_drive.channels`; tests may inject a stub.

    Raises:
        WebhookVerificationError: every failure mode.
    """
    if not channel_id:
        raise WebhookVerificationError("missing X-Goog-Channel-ID header")
    if not channel_token:
        # Empty / missing token. Note: a registry record with an
        # empty token would still fail this gate — operators MUST
        # provision tokens at channels.watch time. The Drive API
        # itself allows an empty token; we choose not to.
        raise WebhookVerificationError("missing X-Goog-Channel-Token header")
    if not resource_id:
        raise WebhookVerificationError("missing X-Goog-Resource-ID header")

    if registry is None:
        from sources.google_drive.channels import get_registry

        registry = get_registry()

    record = registry.get(channel_id)
    if record is None:
        raise WebhookVerificationError(f"unknown channel_id {channel_id!r} (not in registry)")

    if not record.token:
        # Registry row exists but has no token recorded. This is a
        # registry-corruption case (or a deliberately empty-token
        # channel, which we don't allow per the missing-token gate
        # above). Refuse to compare an empty string with anything.
        raise WebhookVerificationError(f"channel {channel_id!r} has no token registered")

    # Constant-time compare. compare_digest on str args works
    # provided both are ASCII; Drive's tokens are at most 256 chars
    # and the docs recommend "URL query parameter-style" content
    # which is ASCII-safe.
    if not hmac.compare_digest(channel_token, record.token):
        raise WebhookVerificationError(f"channel {channel_id!r} token mismatch")

    if not hmac.compare_digest(resource_id, record.resource_id):
        # Loud log: this is the canonical "someone is trying to
        # forge a notification using a known channel-id+token but
        # against the wrong resource" signal.
        print(
            f"[drive-webhook] channel {channel_id!r} resource_id mismatch: "
            f"got {resource_id!r}, expected {record.resource_id!r}",
            file=sys.stderr,
        )
        raise WebhookVerificationError(f"channel {channel_id!r} resource_id mismatch")


def handle(
    *,
    body: bytes,
    channel_id: str | None,
    channel_token: str | None,
    resource_id: str | None,
    resource_state: str | None,
    message_number: str | None,
    registry=None,
) -> tuple[int, str]:
    """Process one Drive notification delivery.

    Returns ``(http_status, response_body)``. Drive does not require a
    JSON response shape; plain text is fine.

    Drive retries on 500/502/503/504 only. Every other non-2xx is
    final. We use:
    - 401 for verification failures (no retry desired — the channel
      is broken; renewal will replace it).
    - 200 for sync messages and ack'd notifications (deferred ingest
      lands later via the dirty-channel marker).
    - 400 for the (rare) malformed-header case where the registry
      can't even be queried.

    ``body`` is accepted but not parsed — Drive's notification body
    is empty for ``files.watch`` and a no-op envelope for
    ``changes.watch``. We pin it as a parameter for symmetry with
    the other handlers and for future tracing of Drive-side body
    schema changes.
    """
    # Body intentionally unused in cycle 9 (see module docstring).
    # Naming it ``_body`` and dropping the parameter would diverge
    # from the cycle-5/6/7 server dispatch contract; keep the
    # parameter and acknowledge the discard.
    _ = body

    # LOW-2 review finding: ASCII gate on resource_state. The HTTP
    # layer (webhooks/server.py:134) already rejects non-ASCII
    # headers, so this is defense-in-depth for callers that bypass
    # the HTTP path (e.g. a future CLI replay tool).
    if resource_state is not None and not resource_state.isascii():
        return 400, "non-ASCII resource_state"

    try:
        verify_notification(
            channel_id=channel_id,
            channel_token=channel_token,
            resource_id=resource_id,
            registry=registry,
        )
    except WebhookVerificationError as exc:
        print(f"[drive-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}"

    # Cycle 9d: per-delivery dedup. Drive auto-retries on 5xx with the
    # same (channel_id, message_number); without dedup, a single logical
    # event can fire `_ingest_change` multiple times. The
    # `upsert_decision` / `upsert_input_span` canonical_id UUIDv5 upserts
    # in the ledger are the idempotency safety net for any retries that
    # slip past this gate, but suppressing the duplicate before
    # `_ingest_change` also avoids the wasted Drive API quota of a
    # second `files.get` for content we already ingested. Dedup is
    # placed AFTER verification so unverified deliveries cannot poison
    # the cache. Empty `message_number` (Drive does not guarantee the
    # header) fail-opens through `is_duplicate`'s existing empty-id
    # contract; canonical_id upsert remains the safety net.
    from webhooks.dedup import get_dedup_cache

    delivery_id = f"{channel_id}:{message_number}" if message_number else ""
    cache = get_dedup_cache()
    if cache.is_duplicate("google_drive", delivery_id):
        print(
            f"[drive-webhook] duplicate delivery {channel_id!r}:{message_number!r} ignored",
            file=sys.stderr,
        )
        return 200, "duplicate"
    cache.mark_seen("google_drive", delivery_id)

    state = (resource_state or "").strip().lower()

    # Sync message: Drive sends one of these immediately after
    # channels.watch to confirm the URL is reachable. Per Drive's
    # docs, "safe to ignore." We still 200 it so Google marks the
    # channel as healthy.
    if state == "sync":
        # Defense-in-depth: pin the contract that sync messages have
        # message_number == "1". A sync-state message with a higher
        # number would indicate provider-side bug or replay attempt.
        if message_number and message_number.strip() != "1":
            print(
                f"[drive-webhook] sync message with unexpected "
                f"message_number={message_number!r} (expected '1'); "
                f"still acking but recording for audit",
                file=sys.stderr,
            )
        print(
            f"[drive-webhook] sync ack for channel {channel_id!r}",
            file=sys.stderr,
        )
        return 200, f"sync acknowledged for channel {channel_id!r}"

    # Non-sync state: look up the file_id in the registry, fetch via
    # the active adapter, pipe through handle_ingest.
    # ``trash``/``remove`` are append-only-contract acks: we do NOT
    # propagate deletes to the ledger (operator uses #221 / GDPR path
    # for erasure, not this).
    if state in {"add", "update", "change", "untrash"}:
        # verify_notification rejected missing channel_id above, so by
        # this point channel_id is guaranteed non-empty. Assert it for
        # the type-checker (mypy can't propagate the implicit narrow).
        assert channel_id is not None
        return _ingest_change(channel_id, state, registry=registry)
    if state in {"remove", "trash"}:
        print(
            f"[drive-webhook] channel {channel_id!r} state={state!r} "
            f"acknowledged (append-only contract; no ingest)",
            file=sys.stderr,
        )
        return 200, f"channel {channel_id!r} state={state!r} acknowledged (no ingest)"

    # Unknown / future state — still 200 (Drive's retry posture
    # would retry on 5xx, and we don't want to retry on a state we
    # simply haven't taught the handler about yet).
    print(
        f"[drive-webhook] channel {channel_id!r} unknown state {state!r}; acking",
        file=sys.stderr,
    )
    return 200, f"channel {channel_id!r} state={state!r} acknowledged (unknown)"


def _ingest_change(channel_id: str, state: str, *, registry=None) -> tuple[int, str]:
    """Look up the channel's file_id, fetch via GoogleDriveAdapter, ingest.

    Failure posture (per cycle-9 docs + Drive's retry contract):
    Drive retries on 5xx ONLY (not on 4xx like Notion). We use:
    - 500 for transient fetch failures so Drive retries.
    - 200 for deterministic failures (file gone, permission revoked,
      hard-gate refusal) so Drive does not amplify.

    The cycle-9 review M3 finding about nested ``asyncio.run`` is
    inherited here — same pattern as Notion's ``_ingest_page``. A
    follow-up cycle makes ``handle()`` itself async to retire the
    pattern across all five handlers.
    """
    if registry is None:
        from sources.google_drive.channels import get_registry

        registry = get_registry()

    record = registry.get(channel_id)
    if record is None:
        # Verification passed (so we DID know this channel a moment
        # ago) but the registry was mutated between verify and
        # dispatch — race on operator-driven drive-stop. 200 ack
        # because Drive's retry won't help (the channel is gone).
        print(
            f"[drive-webhook] channel {channel_id!r} missing from registry "
            f"during ingest dispatch (raced with drive-stop?); acking",
            file=sys.stderr,
        )
        return 200, f"channel {channel_id!r} state={state!r} acknowledged (registry race)"

    file_id = record.file_id
    if not file_id:
        print(
            f"[drive-webhook] channel {channel_id!r} has no file_id; acking",
            file=sys.stderr,
        )
        return 200, f"channel {channel_id!r} state={state!r} acknowledged (no file_id)"

    try:
        from sources.google_drive.adapter import GoogleDriveAdapter

        adapter = GoogleDriveAdapter()
        # Build a canonical Drive URL from the file_id.
        # ``parse_gdrive_url`` accepts both docs.google.com and
        # drive.google.com forms; we use docs.google.com because
        # the adapter normalizes to the Docs API endpoint.
        page_url = f"https://docs.google.com/document/d/{file_id}/edit"
        ingest_payload = adapter.fetch_active(page_url)
    except RuntimeError as exc:
        # GoogleDriveAdapter wraps every API failure in RuntimeError
        # (sources/google_drive/adapter.py:154). Inspect ``__cause__``
        # to demote 4xx-not-429 to 200 (deterministic — file gone,
        # permission revoked, malformed request); 5xx + 429 + non-
        # HttpError causes default to 500 so Drive's 8-retry envelope
        # acts as backoff. Cycle 9b review M1 fix.
        cause = exc.__cause__
        http_status = getattr(getattr(cause, "resp", None), "status", None)
        is_deterministic = (
            isinstance(http_status, int) and 400 <= http_status < 500 and http_status != 429
        )
        print(
            f"[drive-webhook] file fetch failed for {file_id!r} "
            f"(channel={channel_id!r}, state={state!r}, http_status={http_status}, "
            f"deterministic={is_deterministic}): {exc}",
            file=sys.stderr,
        )
        if is_deterministic:
            return 200, (
                f"channel {channel_id!r} acknowledged (fetch http={http_status}, no retry)"
            )
        return 500, (f"channel {channel_id!r} fetch failed (transient); Drive will retry")
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-webhook] adapter raised non-RuntimeError for {file_id!r}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 500, (f"channel {channel_id!r} fetch failed (unclassified); Drive will retry")

    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(
                ctx, ingest_payload, source_scope="google_drive", ingest_mode="passive"
            )

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        # Hard-gate refusal (PHI/secret/PAN). 200 ack — Drive will
        # not retry on 200, and the gate's own audit-log emit is the
        # operator-visibility surface.
        print(
            f"[drive-webhook] hard-gate refusal for file {file_id!r} "
            f"(channel={channel_id!r}): {exc.reason}",
            file=sys.stderr,
        )
        return 200, f"channel {channel_id!r} acknowledged (refused: {exc.reason})"
    except Exception as exc:  # noqa: BLE001
        # Generic post-fetch ingest failure (ledger error, etc.).
        # 500 so Drive retries — operator's environment will recover
        # if the failure was transient.
        print(
            f"[drive-webhook] ingest failed for file {file_id!r} "
            f"(channel={channel_id!r}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 500, (f"channel {channel_id!r} ingest failed (transient); Drive will retry")

    return 200, (f"channel {channel_id!r} state={state!r} ingested file {file_id!r}")
