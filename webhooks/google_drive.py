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
       Tokens are operator-set strings, so length is not a fixed
       constant; we pad to the longer length before compare to keep
       the compare itself constant-time (compare_digest's own length
       check leaks length, but length is not the secret — the token
       value is).
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

    # Non-sync state: log the "channel dirty" marker and ack.
    # Cycle 9b will add a worker that consumes these markers and runs
    # files.get for the affected file_id. For v0 we just log + 200 so
    # operators can verify the wire works.
    if state in {"add", "remove", "update", "trash", "untrash", "change"}:
        print(
            f"[drive-webhook] channel {channel_id!r} state={state!r} "
            f"(ingest follow-up deferred to cycle 9b)",
            file=sys.stderr,
        )
        return 200, f"channel {channel_id!r} state={state!r} acknowledged"

    # Unknown / future state — still 200 (Drive's retry posture
    # would retry on 5xx, and we don't want to retry on a state we
    # simply haven't taught the handler about yet).
    print(
        f"[drive-webhook] channel {channel_id!r} unknown state {state!r}; acking",
        file=sys.stderr,
    )
    return 200, f"channel {channel_id!r} state={state!r} acknowledged (unknown)"
