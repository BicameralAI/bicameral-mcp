# Research Brief — Notion Webhooks

**Date**: 2026-05-20
**Analyst**: The Qor-logic Analyst (research mode)
**Target**: Notion webhooks API — design contract for cycle 8 webhook receiver in the `#337` integrations track
**Scope**:
- Wire protocol: subscription verification handshake, signature scheme, event envelope, retry behavior
- Authentication / verification model — the `verification_token` doubles as the HMAC secret
- Event coverage: what fires, what gets aggregated, what's available for ingest
- Operational implications: out-of-band token-paste step in operator setup, dedup keys, attempt-number semantics
- Threat model relative to GitHub / Slack / Linear / Drive
- Drift against existing Notion code in the tree (`sources/notion/poller.py:4` carries a stale comment claiming Notion has no webhooks)

---

## Executive Summary

Notion's webhook contract is HMAC-signed (closer to GitHub's posture than Drive's), but the secret-provisioning flow is unique: **Notion mints the secret and sends it to us via a one-time verification POST; the operator must then paste that token back into Notion's UI to activate the subscription.** The same token serves as both the proof-of-reachability handshake artifact AND the long-term HMAC-SHA256 secret for every subsequent event.

This places cycle 8 in design tension with the cycles 5-7 pattern in two ways:
1. The receiver must distinguish a "verification" POST from an "event" POST on a route that does not yet have an authentication context — there's no HMAC to verify when the secret hasn't been received yet.
2. The operator setup flow is two-sided: we must surface the freshly received `verification_token` to the operator (via CLI, dashboard, or log) so they can copy-paste it into Notion's UI to complete the handshake.

Beyond that quirk, the rest of the protocol is well-shaped: `X-Notion-Signature: sha256=<hex>` HMAC over body, rich event envelope (`id`, `timestamp`, `subscription_id`, `attempt_number`, `entity`, `data`), at-most-once delivery with up to 8 retries over ~24h, content-update aggregation reducing event spam.

No drift against `docs/ARCHITECTURE_PLAN.md` (the plan does not yet cover Notion webhooks). **One drift detected in the existing code**: `sources/notion/poller.py:4-5` claims "Notion has no webhook story for shared workspaces" — Notion shipped webhooks in 2024 GA. The comment needs to be updated as part of cycle 8.

---

## Findings

### 1. Subscription Verification Handshake

#### 1a. Setup flow (operator-side)

Per Notion's docs verbatim:
> Notion sends a one-time POST request to your webhook URL. The body of the request contains a `verification_token`, which proves that Notion can successfully reach your endpoint.

Then:
> Paste the `verification_token` value into the form and click **Verify subscription.**

Implications:
- We MUST receive the POST and persist the `verification_token` BEFORE the operator can complete the Notion-side step.
- The operator-side step is out-of-band (browser UI on Notion's site). We have no way to programmatically complete it.
- We MUST surface the `verification_token` to the operator. Three viable surfaces: (a) print to stderr, (b) write to a known log file, (c) expose via a CLI `bicameral-mcp notion-pending-verifications` command. Recommend (a) AND (c) — print at receive time AND make it queryable, in case operator missed the stderr line.

#### 1b. Wire shape of the verification POST

```json
{ "verification_token": "secret_REDACTED_PLACEHOLDER_VALUE_FROM_NOTION_DOCS" }
```

The verification POST itself has NO `X-Notion-Signature` header — there is no HMAC to verify because no secret has been established yet. Distinguishing verification from event delivery must be done by inspecting the body for the `verification_token` key. This is a structural marker, not an authentication primitive — but at this stage in the lifecycle there is nothing to authenticate against.

**Threat-model implication:** An attacker who knows our webhook URL can POST `{"verification_token": "attacker-chosen-string"}` to us. We will receive it and log/surface it. The damage is bounded: the operator must independently paste THAT token into Notion's UI for it to take effect on Notion's side; Notion's UI accepts the token Notion sent (which is the legitimate one), not what we stored. So at worst we surface garbage to the operator. **The verification POST is opportunistically attackable but does not lead to compromised state** because Notion is the source of truth for "did the operator paste back the value WE sent." Mitigation: only display the most recently received verification_token (overwrite the registry slot on each verification POST), and label the surface clearly: "Pending verification — paste this exact value into Notion if you initiated a subscription."

### 2. Event Signature Verification

#### 2a. Header & scheme

`X-Notion-Signature: sha256=<hex>` where `<hex>` is the lowercase hex HMAC-SHA256 of the raw request body, using the `verification_token` (the same value from the handshake) as the HMAC key.

Notion's docs verbatim:
> Every webhook request from Notion includes an `X-Notion-Signature` header, which contains an HMAC-SHA256 hash of the request body, signed with your `verification_token`.

Notable basestring properties:
- Body bytes only — NO timestamp prefix (unlike Slack's `v0:{ts}:{body}` scheme).
- NO `v0=`-style version prefix on the signature value (unlike Slack).
- `sha256=` literal prefix on the signature value (parallel to GitHub's `X-Hub-Signature-256: sha256=<hex>` posture).

This is functionally identical to GitHub's signature scheme. The `webhooks/github.py:verify_signature` implementation is a direct template, with two trivial changes: header name and missing `X-GitHub-Delivery` (Notion uses body-side `id` instead).

#### 2b. Replay defense

Notion does NOT send a timestamp header. Documented body fields usable for replay defense:

- `id` (UUID per event) — canonical dedup key. Documented as unique per event.
- `timestamp` (ISO 8601) — useful for staleness gating IF operator wants to enforce a window, but Notion does not promise prompt delivery: events "should be delivered within 5 minutes" and aggregation can add additional delay. A tight staleness gate (e.g. 5 min like Slack) would conflict with aggregated `page.content_updated` deliveries. Recommend NOT gating on timestamp — rely on `id` dedup.
- `attempt_number` (1-8) — Notion's documented retry counter. NOT a dedup primitive (multiple retries of the same logical event will share `id` but have different `attempt_number`). Useful as a soft signal for "Notion is retrying — our previous response wasn't a 200." Log it; don't gate on it.

### 3. Event Envelope

Documented top-level fields on every event:

| Field | Type | Purpose |
|---|---|---|
| `id` | UUID | Unique event identifier — dedup key |
| `timestamp` | ISO 8601 | Event-occurrence time (per Notion's clock) |
| `workspace_id` | UUID | Source workspace |
| `subscription_id` | UUID | The subscription this event belongs to (multi-subscription operators can route on this) |
| `integration_id` | UUID | The integration that owns the subscription |
| `type` | string | Event type, dotted: `page.content_updated`, `comment.created`, etc. |
| `authors` | array | Person/bot/agent who performed the action |
| `accessible_by` | array | Users/bots with access to the entity |
| `attempt_number` | int 1-8 | Retry counter |
| `entity` | object `{id, type}` | The resource the event is about |
| `data` | object | Event-type-specific payload |

### 4. Event Types

Confirmed supported (per the Notion docs):

- Page lifecycle: `page.created`, `page.deleted`, `page.locked`, `page.moved`, `page.properties_updated`, `page.undeleted`, `page.unlocked`
- Page content: `page.content_updated` ← **aggregated** (high-frequency events batched within a short window into a single delivery)
- Database lifecycle: `database.created`, `database.deleted`, `database.moved`, `database.undeleted`
- Data source (newer concept, post 2025-09-03 API version): `data_source.created`, `data_source.deleted`, `data_source.moved`, `data_source.undeleted`, `data_source.schema_updated`, `data_source.content_updated`
- Comments: `comment.created`, `comment.updated`, `comment.deleted`

Deprecated / removed: `database.schema_updated` (post-2022-06-28 API version). Cycle 8 should NOT subscribe to this.

### 5. Retry Policy

Per Notion's docs verbatim:
> Events should be delivered within 5 minutes of their occurrences. Most should be delivered within a minute.

> at-most-once event delivery [...] up to 8 times [...] using exponential backoff, with the final retry attempt occurr[ing] approximately 24 hours after the initial event trigger.

Notable:
- Notion does NOT specify which status codes trigger retry vs final-fail. Safe assumption: any non-2xx triggers retry (different from Drive's "5xx only" posture).
- "at-most-once" is best-effort — combined with retry, the same `id` CAN arrive multiple times if our previous response was non-2xx OR if Notion's tracking lost the previous attempt.
- Dedup window MUST cover the 24h retry envelope, same as the GitHub adjustment we made post-cycle-5 review.

### 6. Aggregation Behavior

Per Notion's docs verbatim:
> For high-frequency events like `page.content_updated`, Notion batches changes that occur within a short time window into a single webhook event.

Implication: a single `page.content_updated` delivery may represent MANY edits. The handler cannot infer "1 webhook = 1 logical change." For decision-bearing ingest purposes this is fine — we fetch the canonical page content via the active-fetch adapter on each delivery, and the page content IS the ground truth regardless of edit count.

### 7. Access Permissions Gate

Per Notion's docs:
> Make sure the connection has access to the object that triggered the event. For example, if a new page is created inside a private page your connection doesn't have access to, the event won't be triggered.

So the integration's existing `internal_integration_secret` (used by `sources/notion/client.py`) controls which pages are eligible to generate webhooks. The webhook subscription is scoped to the integration; if the integration doesn't have access to a page, no webhook fires for that page. This is the right posture and matches our preferred operator model (operator explicitly shares pages with the integration; bicameral never sees more than is shared).

### 8. HTTPS Requirement

Per Notion's docs verbatim:
> Enter your public **Webhook URL** — this is the public endpoint where you want Notion to send events. It must be a secure (SSL) and publicly available endpoint. Endpoints in localhost are not reachable.

Same posture as Drive — operator MUST front us with TLS termination. Cycle-5 server listens on plain HTTP and requires `--allow-public` for non-loopback bind.

---

## Blueprint Alignment

| Blueprint Claim | Actual Finding | Status |
|---|---|---|
| `ARCHITECTURE_PLAN.md` does not mention Notion webhooks (Phase 2/2b shipped active + polling only) | Confirmed via grep | NO DRIFT (new design space) |
| `sources/notion/poller.py:4-5` comment: "Notion has no webhook story for shared workspaces, so polling is the only viable passive path." | Notion shipped webhooks (2024 GA); they DO work for shared workspaces; the comment is stale | **DRIFT** — code comment, must update during cycle 8 |
| Cycle 5/6/7 hardening (slow-loris, body cap, smuggling defenses) inherited | Cycle-5 `webhooks/server.py` applies to every route equally | MATCH |
| Cycle 6/7/9 lesson: adversarial review required before push | This brief recommends same gate for cycle 8 | MATCH |
| Cycle 5-7 webhook signing patterns generalize to Notion | Notion's HMAC scheme = GitHub's pattern with header rename — direct template | MATCH (large code reuse opportunity) |
| Memory snapshot `integrations_337_landed.md` says Notion active-ingest is live | Confirmed; no webhook scaffolding present | NO DRIFT |

---

## Recommendations

### Priority 1 (must do before any cycle 8 code lands)

1. **Pick the verification surface strategy.** Three options:
   - (A) **Stderr + CLI query** — print `verification_token` to stderr at receive time, plus `bicameral-mcp notion-pending-verifications` CLI command lists unverified subscriptions. **Recommended** for v0: cheap, no UI dependency, scriptable.
   - (B) **Dashboard sidecar** — surface in the existing dashboard server (`dashboard/server.py`). Cleaner UX but adds a coupling.
   - (C) **Email / Slack notification** — overkill for v0.

2. **Add a subscription registry.** Persists `(subscription_id, verification_token, integration_id, workspace_id, verified_at)`. Two viable stores: SurrealDB table (cycle 9 chose JSON file, but Notion needs query-by-subscription_id which is a more first-class need than channel-id lookup) or extend `secrets_store` with a typed namespace. **Recommended**: secrets_store with `source_id="notion"`, `key=f"subscription:{subscription_id}"` (mirrors the existing `webhook_secret` pattern from cycles 5-7, leverages OS keyring for the token storage — the verification_token IS effectively a secret).

3. **Adopt GitHub's signature implementation as the template.** `webhooks/github.py:verify_signature` requires only two changes: header name (`X-Notion-Signature` instead of `X-Hub-Signature-256`) and the absence of a delivery-id header (use body `id` instead). No new HMAC code to write.

### Priority 2 (cycle 8 in-scope)

4. **Add `/webhooks/notion` route** in `webhooks/server.py` per the cycle-5/6/7 pattern.
5. **Verification handler:** detect `verification_token` in body; if present, persist via `secrets_store`, log + queue for CLI display; return 200 with empty body. Do NOT echo the token back in our response (Notion's UI is the only place it's needed; echoing it back to an arbitrary POST would be a leak primitive).
6. **Event handler:**
   - Pull `subscription_id` from body, look up `verification_token` in `secrets_store`.
   - HMAC-verify `X-Notion-Signature` against the registered token.
   - Dedup on body-field `id` using the existing `webhooks/dedup` LRU (24h TTL covers Notion's retry envelope).
   - Route on `type`: `page.*` and `data_source.content_updated` → enqueue page fetch via existing `sources/notion/adapter.py`; `comment.*` → fetch comment content; everything else → 200 ack + log.
7. **Update stale poller comment:** `sources/notion/poller.py:4-5` should now read approximately: "Notion shipped webhooks in 2024 — passive ingest can be either webhook-driven (`webhooks/notion.py`) or poll-driven (this module). Operators choose per-subscription based on whether they can host an HTTPS endpoint." Land this in the same PR.
8. **Adversarial `code-reviewer` pass before push** — per cycle 6/7/9 precedent. Reviewer should specifically scrutinize:
   - The verification-POST surface: can an attacker who can reach the endpoint clobber a legitimate pending verification by sending their own `{"verification_token": "..."}` POST?
   - The subscription-id-as-lookup-key — what happens if `body.subscription_id` is missing, malformed, or points to a subscription we don't know about? (Notion can resend events after we delete the local subscription record — the 24h retry envelope outlasts most local-state recovery scenarios.)
   - The `attempt_number > 1` case — is there a useful audit signal we're dropping by treating retries as identical to first-attempts?
   - HMAC implementation parity with `webhooks/github.py` — any divergence is a regression risk.

### Priority 3 (deferred / cycle 8b)

9. **Subscription discovery via Notion's API.** Notion exposes `GET /v1/webhooks` (or equivalent) — operator-friendly to list "what am I subscribed to from bicameral's perspective." Defer to cycle 8b when CLI primitives are written for both Drive and Notion.
10. **Per-event-type enrichment.** `page.content_updated` aggregates; cycle 8b can use the `data.updated_blocks` array (if it exists; verify against an actual delivery) to fetch only changed blocks, reducing API quota burn for large pages.

---

## Threat Model Comparison

| Aspect | GitHub | Slack | Linear | Drive | Notion |
|---|---|---|---|---|---|
| Body HMAC | ✅ sha256 | ✅ sha256 over `v0:ts:body` | ✅ sha256 | ❌ no body | ✅ sha256 |
| Timestamp gate | ❌ | ✅ 5min | ✅ 60s | n/a | ❌ |
| Delivery-id dedup | ✅ X-GitHub-Delivery | ✅ event_id | ✅ Linear-Delivery | n/a (resource_id) | ✅ body `id` |
| Secret provisioning | operator-set | operator-set | operator-set | operator-set | **Notion-minted, out-of-band paste-back** |
| Replay window | 24h (retry envelope) | 5min | 60s + 30min retries | n/a | 24h (retry envelope) |
| Body trust model | trust signed body | trust signed body | trust signed body | trust nothing (empty) | trust signed body |

Notion's posture is in the same security tier as GitHub. The unique attack surface is the verification handshake — but as analyzed in §1b, the attack is bounded (operator must independently paste the value back to Notion).

---

## Provenance

- Primary source: `https://developers.notion.com/reference/webhooks` (fetched 2026-05-20)
- Secondary: `https://developers.notion.com/reference/webhooks-events-delivery` (fetched 2026-05-20)
- Internal: `sources/notion/adapter.py:1-30` (existing page-fetch + URL-parse), `sources/notion/poller.py:1-15` (stale-comment drift), `sources/notion/client.py` (REST client), `webhooks/github.py:verify_signature` (signature implementation template), `webhooks/dedup.py` (24h LRU usable as-is)
- Gate artifact: **NOT EMITTED.** `qor/` Python package is not installed in this worktree (same shortfall as Entries #53 and #54). Brief stands on its content per established precedent.

---

_Research complete. Findings are advisory — implementation decisions for cycle 8 remain with the Governor._
