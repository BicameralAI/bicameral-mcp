"""HTTP webhook receiver for push-model ingest sources (#337 cycle 5).

Bicameral's first public HTTP surface. Webhook providers (GitHub,
Slack, Linear) POST event payloads to a configured URL on the
operator's host; the receiver:

1. Verifies the provider's HMAC signature against a per-source secret
   stored in ``secrets_store source_id="<source>", key="webhook_secret"``.
2. Dedups duplicate deliveries via the provider's delivery-id header.
3. Normalizes the event payload to an ``IngestPayload`` via the
   existing per-source active-ingest adapter where possible.
4. Calls ``handle_ingest(ingest_mode="passive")`` so the Phase 0a
   DLQ catches per-item failures.

The receiver runs as a separate asyncio HTTP server (distinct from
the dashboard sidecar) — operator chooses the bind port and is
responsible for the public-surface concerns (TLS termination,
reverse-proxy / tunnel, rate limit at the network layer, request
size cap, log redaction).

Threat-model boundary: this server trusts the signature gate. Any
request that fails verification is rejected with 401 and never
reaches the ingest pipeline. We do NOT trust the request body
beyond what the signature attests to.
"""

from webhooks.dedup import DeliveryDedupCache, get_dedup_cache

__all__ = ["DeliveryDedupCache", "get_dedup_cache"]
