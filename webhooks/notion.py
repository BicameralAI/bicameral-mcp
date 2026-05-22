"""Notion webhook handler (#337 cycle 8).

Notion's webhook contract:

- Header ``X-Notion-Signature: sha256=<hex>`` — HMAC-SHA256 of the
  raw request body, keyed by the ``verification_token``. Same
  pattern as GitHub's ``X-Hub-Signature-256``.
- Body envelope (post-handshake events): ``{id, timestamp,
  workspace_id, subscription_id, integration_id, type, authors,
  accessible_by, attempt_number, entity, data}``. ``id`` is the
  canonical dedup key.
- Verification handshake (one-time, at subscription setup):
  Notion POSTs ``{"verification_token": "<token>"}`` with NO
  signature header. We extract the token, persist it via
  ``secrets_store``, and surface it to stderr so the operator can
  paste it into Notion's UI to activate the subscription.

The same ``verification_token`` doubles as the long-term HMAC
secret for every subsequent event delivery on that subscription.

## Replay defense

Notion does NOT send a timestamp header. Body-side ``timestamp``
exists but staleness-gating would conflict with aggregated
``page.content_updated`` deliveries (Notion batches edits within a
short window per their docs). We rely on body-side ``id`` for
dedup via the shared :mod:`webhooks.dedup` LRU. The 24h TTL on
that cache (raised post-cycle-5 review) covers Notion's full
8-retry / 24h delivery envelope.

## Event coverage (cycle 8 minimum)

- ``page.created``, ``page.content_updated``, ``page.properties_updated``,
  ``page.deleted``, ``page.locked``, ``page.unlocked``, ``page.moved``,
  ``page.undeleted``
- ``data_source.content_updated``, ``data_source.schema_updated``,
  ``data_source.created``, ``data_source.deleted``, etc.
- ``database.*`` (lifecycle only — ``database.schema_updated`` was
  deprecated post-2022-06-28; we don't subscribe)
- ``comment.created``, ``comment.updated``, ``comment.deleted``

v0 ack-only on the event-dispatch path: cycle 8b adds the actual
``handle_ingest`` invocations once we've validated the wire on
real-operator deliveries. Returning 200 + a logged "dirty" marker
is the same posture as cycle 9's Drive handler.

See ``docs/research-brief-notion-webhooks-2026-05-20.md`` for the
full design rationale.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import time


class WebhookVerificationError(Exception):
    """Raised when signature verification fails."""


# Cycle 8 review F2 fix: pending tokens are now keyed by token
# FINGERPRINT (sha256 prefix), not by a single shared
# ``pending_verification_token`` slot. This prevents:
#   - F2a: concurrent verifications clobbering each other's tokens
#   - F2b: cross-binding when multiple first-events race
#   - F2c: attacker DoS via fake-verification POSTs poisoning the
#     single pending slot
# On first event for a subscription, we enumerate pending entries
# and adopt the one whose HMAC matches the signature on the
# incoming body. ``secrets_store.list_keys`` (source_id="notion")
# gives us the enumeration primitive.
#
# Each pending entry stores ``{"token": "...", "received_at":
# <epoch>}`` as JSON. Adoption is rejected for entries older than
# 24h (F9 fix: bounds the attacker-DoS window).

_PENDING_PREFIX = "pending_"
_SUBSCRIPTION_PREFIX = "subscription_"
_PENDING_TTL_SECONDS = 24 * 60 * 60
# LOW-2 review fix: cap the number of pending entries to bound the
# memory cost of attacker-driven fake-verification POSTs. At ~120
# bytes per JSON entry, 100 entries ≈ 12 KiB — well within the
# dict-fallback memory budget. New verifications past the cap are
# rejected with 429 (operator can investigate via
# ``bicameral-mcp notion-pending``).
_MAX_PENDING_ENTRIES = 100
# secrets_store keys must match [A-Za-z0-9._-]+; subscription_id
# from Notion is a UUID but we validate defensively in case the
# body is attacker-crafted.
_SUBSCRIPTION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _secret_key(subscription_id: str) -> str:
    """Build the keyring key for a verified subscription's token."""
    return f"{_SUBSCRIPTION_PREFIX}{subscription_id}"


def _fingerprint(token: str) -> str:
    """16-hex-char prefix of sha256(token). Collision-resistant for
    the small N of pending tokens at any one operator's site."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _pending_key(fingerprint: str) -> str:
    return f"{_PENDING_PREFIX}{fingerprint}"


def verify_signature(
    *,
    body: bytes,
    signature_header: str | None,
    verification_token: str,
) -> None:
    """Verify the X-Notion-Signature header against the body.

    Direct adaptation of :func:`webhooks.github.verify_signature` —
    the schemes are functionally identical apart from the header
    name (``X-Notion-Signature`` vs ``X-Hub-Signature-256``).

    Order:
    1. Missing signature header or verification_token → reject.
    2. Header must start with ``sha256=`` literal.
    3. Hex digest after the prefix must be exactly 64 chars.
    4. HMAC-SHA256(verification_token, body) compared constant-time
       against the digest via :func:`hmac.compare_digest`.

    Raises:
        WebhookVerificationError: every failure mode.
    """
    if not signature_header:
        raise WebhookVerificationError("missing X-Notion-Signature header")
    if not verification_token:
        raise WebhookVerificationError("no verification_token registered for this subscription")
    if not signature_header.startswith("sha256="):
        raise WebhookVerificationError(
            f"X-Notion-Signature missing 'sha256=' prefix: {signature_header[:32]!r}"
        )
    provided_hex = signature_header[len("sha256=") :]
    if len(provided_hex) != 64:
        raise WebhookVerificationError(
            f"X-Notion-Signature digest not 64 hex chars: got {len(provided_hex)}"
        )
    expected_hex = hmac.new(
        verification_token.encode("utf-8"), msg=body, digestmod=hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided_hex.lower(), expected_hex):
        raise WebhookVerificationError("signature mismatch")


def handle(
    *,
    body: bytes,
    signature_header: str | None,
) -> tuple[int, str]:
    """Process one Notion webhook delivery.

    Two structurally distinct payload shapes can arrive on this
    route:

    1. **Verification handshake** (one-time per subscription, no
       signature header): body is ``{"verification_token":
       "secret_..."}``. We persist the token via ``secrets_store``
       and log it to stderr so the operator can complete the
       handshake in Notion's UI.
    2. **Event delivery** (recurring): full envelope with
       ``subscription_id``, ``id``, ``type``, etc. We HMAC-verify
       against the registered verification_token for that
       subscription, dedup on ``id``, and ack.

    Returns ``(http_status, response_body)``. Notion does not
    require a JSON response shape; plain text suffices.
    """
    # Body parse comes BEFORE verify because we need to inspect the
    # body shape to distinguish verification from event. This is
    # the same posture Drive uses (no body signature on the
    # verification path), and it's safer than the cycle-7 Linear
    # pattern that verified first — Notion's verification body
    # legitimately has no signature, so a verify-first design would
    # require special-casing the missing header anyway.
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-webhook] body not JSON-decodable: {exc}", file=sys.stderr)
        return 400, f"body not JSON: {exc}"

    if not isinstance(payload, dict):
        return 400, "body not a JSON object"

    # ── Verification handshake ───────────────────────────────────
    if "verification_token" in payload and "type" not in payload:
        # Distinguish by structural marker: verification payloads
        # have exactly one key (verification_token); event payloads
        # always have type + id + subscription_id. We additionally
        # require ``type`` to be ABSENT (rather than just check
        # verification_token presence) so an attacker can't smuggle
        # a verification_token into a normal event payload to trick
        # us into clobbering the registered token.
        return _handle_verification(payload)

    # ── Event delivery ───────────────────────────────────────────
    return _handle_event(body, payload, signature_header)


def _handle_verification(payload: dict) -> tuple[int, str]:
    """Handle Notion's one-time subscription verification POST.

    Notion POSTs ``{"verification_token": "secret_..."}`` to our
    callback URL. We persist the token under a fingerprint-keyed
    pending slot and surface the fingerprint (NOT the full token)
    to stderr. The full token is retrievable via
    ``secrets_store.get_secret(source_id="notion",
    key=f"pending_<fingerprint>")`` — cycle 8b's CLI tool reads it
    and shows the operator the value to paste back into Notion's UI.

    Each verification gets its own pending slot (keyed by token
    fingerprint), so concurrent verifications don't clobber each
    other (cycle 8 review F2 fix).

    Threat-model note: an attacker who can reach this endpoint can
    POST a fake ``{"verification_token": ...}`` to create a junk
    pending entry. The damage is bounded — the entry is keyed by
    the attacker's own token fingerprint, so it can NOT clobber a
    legitimate pending token (different fingerprints → different
    slots). Worst case is keyring bloat, capped by the 24h TTL on
    adoption (review F9 fix).
    """
    token = payload.get("verification_token")
    if not token or not isinstance(token, str):
        return 400, "verification_token missing or non-string"

    # Length sanity — Notion's documented format is "secret_<base64-ish>"
    # at ~50 chars. Reject anything obviously off so we don't store
    # garbage from random attackers.
    if len(token) < 10 or len(token) > 256:
        print(
            f"[notion-webhook] verification_token length {len(token)} outside "
            "[10, 256] — rejecting",
            file=sys.stderr,
        )
        return 400, "verification_token length out of bounds"

    fingerprint = _fingerprint(token)
    entry = json.dumps({"token": token, "received_at": int(time.time())})

    try:
        from secrets_store import list_keys, put_secret

        # LOW-2 review fix: bound the pending-entries set. Idempotent
        # re-receipt of the same fingerprint (Notion retrying the
        # verification POST itself, or operator-side retry) is
        # ALLOWED — it overwrites the existing entry and doesn't
        # count against the cap. Only NEW fingerprints past the cap
        # are rejected.
        existing = [k for k in list_keys(source_id="notion") if k.startswith(_PENDING_PREFIX)]
        is_new = _pending_key(fingerprint) not in existing
        if is_new and len(existing) >= _MAX_PENDING_ENTRIES:
            print(
                f"[notion-webhook] pending-entries cap reached "
                f"({len(existing)} >= {_MAX_PENDING_ENTRIES}); rejecting new "
                f"fingerprint={fingerprint!r}. Use `bicameral-mcp notion-pending` "
                "to inspect and clean up stale entries.",
                file=sys.stderr,
            )
            return 429, f"pending-entries cap reached ({_MAX_PENDING_ENTRIES})"

        put_secret(source_id="notion", key=_pending_key(fingerprint), value=entry)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[notion-webhook] failed to persist verification entry: {exc}",
            file=sys.stderr,
        )
        return 500, f"failed to persist verification entry: {exc}"

    # F3 fix: log the fingerprint, not the full token. Operator
    # retrieves the full token via `bicameral-mcp notion-pending
    # <fingerprint>` (cycle 8b CLI). Until that CLI ships, operators
    # can read the keyring entry directly via the OS keyring tool of
    # their choice — same surface they already use for OAuth tokens.
    print(
        f"[notion-webhook] verification handshake received "
        f"(fingerprint={fingerprint!r}). Retrieve the full token from "
        f"secrets_store source_id='notion' key='{_pending_key(fingerprint)}' "
        "and paste it into Notion's webhook verification form.",
        file=sys.stderr,
    )

    return 200, f"verification received (fingerprint={fingerprint})"


def _try_adopt_pending(
    body: bytes, signature_header: str | None, subscription_id: str
) -> str | None:
    """Try each pending token; on HMAC match, adopt it for this
    subscription and return the token. Returns None if no pending
    token matches.

    Stale entries (older than 24h per F9 fix) are skipped AND
    deleted in the same pass.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return None
    provided_hex = signature_header[len("sha256=") :].lower()
    if len(provided_hex) != 64:
        return None

    try:
        from secrets_store import delete_secret, get_secret, list_keys, put_secret
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-webhook] secrets_store import failed: {exc}", file=sys.stderr)
        return None

    keys = list_keys(source_id="notion")
    pending_keys = [k for k in keys if k.startswith(_PENDING_PREFIX)]
    now = int(time.time())

    for key in pending_keys:
        raw = get_secret(source_id="notion", key=key)
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:  # noqa: BLE001
            # Malformed entry — delete it and keep going.
            try:
                delete_secret(source_id="notion", key=key)
            except Exception:  # noqa: BLE001
                pass
            continue
        token = entry.get("token")
        received_at = entry.get("received_at")
        if not isinstance(token, str) or not isinstance(received_at, (int, float)):
            try:
                delete_secret(source_id="notion", key=key)
            except Exception:  # noqa: BLE001
                pass
            continue
        # F9 fix: reject + clean up stale pending entries.
        if now - received_at > _PENDING_TTL_SECONDS:
            print(
                f"[notion-webhook] dropping stale pending entry {key!r} "
                f"(age {now - int(received_at)}s > {_PENDING_TTL_SECONDS}s)",
                file=sys.stderr,
            )
            try:
                delete_secret(source_id="notion", key=key)
            except Exception:  # noqa: BLE001
                pass
            continue

        expected_hex = hmac.new(
            token.encode("utf-8"), msg=body, digestmod=hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(provided_hex, expected_hex):
            # Match — adopt under the subscription-specific key and
            # delete the pending entry. This is the moment the
            # subscription becomes "verified" from our side.
            try:
                put_secret(
                    source_id="notion",
                    key=_secret_key(subscription_id),
                    value=token,
                )
                delete_secret(source_id="notion", key=key)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[notion-webhook] failed to adopt pending entry {key!r}: {exc}",
                    file=sys.stderr,
                )
                return None
            print(
                f"[notion-webhook] adopted pending verification (fingerprint="
                f"{key[len(_PENDING_PREFIX) :]!r}) for subscription_id="
                f"{subscription_id!r}",
                file=sys.stderr,
            )
            return token
    return None


def _handle_event(body: bytes, payload: dict, signature_header: str | None) -> tuple[int, str]:
    """Handle a post-handshake event delivery."""
    subscription_id = payload.get("subscription_id")
    if not subscription_id or not isinstance(subscription_id, str):
        return 400, "event missing subscription_id"
    # Validate subscription_id shape: secrets_store keys must match
    # [A-Za-z0-9._-]+, and an attacker-supplied value with other
    # characters would otherwise surface as a 500 from
    # `_validate_identifier` (review nice-to-have: return 400
    # instead).
    if not _SUBSCRIPTION_ID_RE.match(subscription_id):
        return 400, "subscription_id has invalid characters"

    event_id = payload.get("id")
    if not event_id or not isinstance(event_id, str):
        return 400, "event missing id"

    event_type = (payload.get("type") or "").strip()
    if not event_type:
        return 400, "event missing type"

    # Look up the verification_token for this subscription. On the
    # FIRST event delivery for a freshly verified subscription, the
    # subscription-specific key won't exist yet — enumerate the
    # pending entries (one per verification handshake we've
    # received) and try to adopt one whose HMAC matches the
    # incoming signature. Fingerprint-keyed pending entries
    # (review F2 fix) prevent concurrent verifications from
    # clobbering each other.
    try:
        from secrets_store import get_secret

        token = get_secret(source_id="notion", key=_secret_key(subscription_id))
        if not token:
            token = _try_adopt_pending(body, signature_header, subscription_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-webhook] secret lookup failed: {exc}", file=sys.stderr)
        return 500, f"secret lookup failed: {exc}"

    if not token:
        print(
            f"[notion-webhook] no verification_token for subscription_id="
            f"{subscription_id!r}; rejecting",
            file=sys.stderr,
        )
        return 401, f"no verification_token registered for subscription {subscription_id!r}"

    try:
        verify_signature(body=body, signature_header=signature_header, verification_token=token)
    except WebhookVerificationError as exc:
        print(f"[notion-webhook] verification failed: {exc}", file=sys.stderr)
        return 401, f"verification failed: {exc}"

    # Dedup on event id. Notion's 8-retry / 24h envelope is fully
    # covered by the shared 24h dedup TTL (raised post-cycle-5).
    from webhooks.dedup import get_dedup_cache

    cache = get_dedup_cache()
    if cache.is_duplicate("notion", event_id):
        print(
            f"[notion-webhook] duplicate event_id {event_id!r} ignored",
            file=sys.stderr,
        )
        return 200, "duplicate"
    cache.mark_seen("notion", event_id)

    attempt_number = payload.get("attempt_number")
    # Review F7 nice-to-have: only log attempt_number when it's
    # actually actionable. attempt_number==2 is almost always a
    # transient that self-healed; attempt_number>=4 (halfway
    # through the envelope) is a real signal worth surfacing.
    if isinstance(attempt_number, int) and attempt_number >= 4:
        print(
            f"[notion-webhook] event_id={event_id!r} attempt_number="
            f"{attempt_number} (Notion retrying — investigate prior 200 misses)",
            file=sys.stderr,
        )

    # Cycle 8b: actually ingest decision-bearing events. The
    # webhook is the trigger; the canonical content still flows
    # through ``sources/notion/adapter.py:fetch_active`` so the
    # passive and active paths land identical payloads.
    #
    # ``page.*`` events with a non-deleted entity → fetch + ingest.
    # ``page.deleted`` → ack only (append-only contract, same
    # posture as Linear's Issue/Comment remove path).
    # ``comment.*``, ``data_source.*``, ``database.*`` → ack only;
    # later cycles add comment fetching, schema-change handling.
    if event_type in {"page.created", "page.content_updated", "page.properties_updated"}:
        entity = payload.get("entity") or {}
        page_id = entity.get("id")
        if not page_id or not isinstance(page_id, str):
            print(
                f"[notion-webhook] event_id={event_id!r} type={event_type!r} "
                f"missing entity.id; skipping ingest",
                file=sys.stderr,
            )
            return 200, f"event id={event_id!r} type={event_type!r} acknowledged (no entity.id)"
        return _ingest_page(event_id, event_type, page_id)

    # Acknowledged, no ingest. Cycle 8c-or-later will add comment
    # fetching (needs a new Notion API path beyond the existing
    # adapter) and decide whether schema / data_source events get
    # any treatment.
    print(
        f"[notion-webhook] event_id={event_id!r} type={event_type!r} "
        f"subscription_id={subscription_id!r} (no ingest for this event type)",
        file=sys.stderr,
    )
    return 200, f"event id={event_id!r} type={event_type!r} acknowledged"


def _ingest_page(event_id: str, event_type: str, page_id: str) -> tuple[int, str]:
    """Fetch a Notion page via the active adapter and pipe through
    ``handle_ingest`` in passive mode.

    Mirrors the GitHub/Slack/Linear webhook → handle_ingest path:
    ``asyncio.run()`` inside the to_thread worker. Cycle 9 review
    M3 flagged this nested-loop pattern as a latent reliability
    issue (per-request loop create/teardown overhead); a follow-up
    cycle will revisit by making handle() itself async. Until
    then, the pattern matches the rest of the chain.

    Failure posture: ALL failures (fetch error, refusal, generic
    ingest error) return 200 to Notion. Notion retries on every
    non-2xx (not just 5xx like Drive), and we do NOT want 8 retries
    of a payload that's already failed for a deterministic reason.
    Operator visibility is via stderr + the gate's own audit-log
    emit. This deviates from cycles 5/6/7 (which return 422 on
    refusal) — the deviation is documented in the
    research-brief-notion §5 retry-policy analysis.
    """
    try:
        from sources.notion.adapter import NotionAdapter
        from sources.notion.client import NotionAPIError

        adapter = NotionAdapter()
        # Build a canonical Notion URL from the page_id. The
        # adapter accepts both dashed and undashed UUIDs; the
        # webhook payload provides the undashed form.
        page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
        ingest_payload = adapter.fetch_active(page_url)
    except NotionAPIError as exc:
        # MED-1 fix: split transient from deterministic fetch
        # failures. 5xx + 429 are exactly the cases where Notion's
        # 8-retry / 24h envelope is the right backpressure — return
        # 500 so the provider retries and the operator sees the
        # storm in their network metrics. 4xx-not-429 (page gone,
        # permission revoked, malformed page_id) is genuinely
        # deterministic; 200-ack with stderr so we don't amplify.
        status_code = exc.status_code
        is_transient = status_code is None or status_code == 429 or status_code >= 500
        print(
            f"[notion-webhook] page fetch failed for {page_id!r} "
            f"(event_id={event_id!r}, http_status={status_code}, "
            f"transient={is_transient}): {exc}",
            file=sys.stderr,
        )
        if is_transient:
            return 500, (
                f"event id={event_id!r} fetch failed (transient http={status_code}); "
                "Notion will retry"
            )
        return 200, f"event id={event_id!r} type={event_type!r} acknowledged (fetch failed)"
    except Exception as exc:  # noqa: BLE001
        # Non-NotionAPIError exceptions (e.g. malformed URL parse,
        # adapter import failure, transport-layer bugs we haven't
        # classified). Treat as transient by default — operator
        # visibility via the retry storm is more useful than silent
        # loss.
        print(
            f"[notion-webhook] page fetch raised non-API exception for {page_id!r} "
            f"(event_id={event_id!r}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 500, f"event id={event_id!r} fetch failed (unclassified); Notion will retry"

    try:
        import asyncio

        from context import BicameralContext
        from handlers.ingest import _IngestRefused, handle_ingest

        ctx = BicameralContext.from_env()

        async def _ingest() -> None:
            await handle_ingest(ctx, ingest_payload, source_scope="notion", ingest_mode="passive")

        asyncio.run(_ingest())
    except _IngestRefused as exc:
        print(
            f"[notion-webhook] hard-gate refusal for page {page_id!r} "
            f"(event_id={event_id!r}): {exc.reason}",
            file=sys.stderr,
        )
        return 200, f"event id={event_id!r} acknowledged (refused: {exc.reason})"
    except Exception as exc:  # noqa: BLE001
        print(
            f"[notion-webhook] ingest failed for page {page_id!r} (event_id={event_id!r}): {exc}",
            file=sys.stderr,
        )
        return 200, f"event id={event_id!r} acknowledged (ingest failed)"

    return 200, f"event id={event_id!r} type={event_type!r} ingested page {page_id!r}"
