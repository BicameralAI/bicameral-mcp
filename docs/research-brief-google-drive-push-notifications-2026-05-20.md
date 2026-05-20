# Research Brief — Google Drive Push Notifications

**Date**: 2026-05-20
**Analyst**: The Qor-logic Analyst (research mode)
**Target**: Google Drive API v3 Push Notifications (channels.watch / changes.watch / files.watch) — design contract for cycle 9 webhook receiver in the `#337` integrations track
**Scope**:
- Wire protocol: request shape, headers Google sends, body content, status-code retry behavior
- Authentication / verification model and how it differs from HMAC-signed providers (GitHub, Slack, Linear)
- Channel lifecycle: creation, expiration windows, renewal, cancellation
- Operational implications: notification-as-trigger vs notification-as-payload, follow-up API calls
- Threat model: what an attacker can do given the limits of Drive's verification model

---

## Executive Summary

Google Drive's Push Notifications model is fundamentally different from the HMAC-signed webhooks that GitHub, Slack, and Linear use. **Drive does not sign requests.** The only verification primitives are: HTTPS callback URL (mandatory, CA-signed cert), an operator-set opaque `token` string echoed in `X-Goog-Channel-Token`, and an opaque `X-Goog-Resource-Id` that must match the channel-creation registration. The body is empty for `files`/`changes` watches; the notification is a trigger, not a payload — the handler must call `changes.list` (with a pre-saved `startPageToken`) to learn what actually changed.

The threat-model gap relative to the prior three cycles is real and load-bearing for cycle 9's design: token equality is a strictly weaker security primitive than HMAC. The cycle 9 receiver MUST treat the token as a privacy boundary (rotate, never log raw, alert on shape divergence) and MUST treat every accepted notification as advisory until cross-validated against `changes.list`. There is no "trust the body" path — the body is empty.

No drift detected against `docs/ARCHITECTURE_PLAN.md` (the plan does not yet cover Drive webhooks). Two scope additions are proposed for cycle 9: a channel-lifecycle store (channel_id ↔ resource_id ↔ token ↔ expiration) and a `startPageToken` watermark store; both are absent from the existing Drive code.

---

## Findings

### 1. Wire Protocol

#### 1a. Watch Request — what we send Google

`POST https://www.googleapis.com/drive/v3/changes/watch` (account-wide changes)
or `POST https://www.googleapis.com/drive/v3/files/{fileId}/watch` (single file).

Required body fields:
- `id` — UUID, ≤64 chars, unique per channel within our project
- `type` — must be the literal string `"web_hook"`
- `address` — HTTPS URL with valid CA-signed SSL cert. Self-signed, untrusted-CA, revoked, or mismatched-CN certs cause Google to refuse delivery.

Optional fields we should use:
- `token` — operator-set arbitrary string, ≤256 chars; echoed in `X-Goog-Channel-Token` on every notification. **This is the only authenticity signal we get.** Google's docs explicitly note this should not contain sensitive data (no OAuth tokens, no API keys) because it lives in plaintext in logs and HTTP intermediaries.
- `expiration` — Unix-ms timestamp. Max: 86,400,000 ms (1 day) for `files`, 604,800,000 ms (7 days) for `changes`. Default if omitted: 3600 seconds.

Source: `https://developers.google.com/drive/api/guides/push` § "Create notification channels" / "Required properties" / "Optional properties".

#### 1b. Notification — what Google sends our callback

Headers on every notification POST:

| Header | Always Present | Contents |
|---|---|---|
| `X-Goog-Channel-Id` | yes | The UUID we sent in `id` |
| `X-Goog-Resource-Id` | yes | Opaque ID identifying the *watched resource* (not the changed item) |
| `X-Goog-Resource-State` | yes | `sync` (first message), `add`, `remove`, `update`, `trash`, `untrash`, or `change` |
| `X-Goog-Resource-URI` | yes | API-version-specific resource URI |
| `X-Goog-Message-Number` | yes | Integer; `1` for the sync message, increases (non-sequentially) thereafter |
| `X-Goog-Channel-Token` | only if we set `token` | The token we registered |
| `X-Goog-Channel-Expiration` | only if we set `expiration` | Human-readable date/time |
| `X-Goog-Changed` | only on update events | Comma-separated subset of `content,parents,children,permissions` |

Body: **empty** for `files` and `changes` watches. For `changes`, a small JSON envelope `{"kind": "drive#changes"}` is sent, but it carries no per-change detail.

Source: same doc, "Headers" table + "Sync message" + "Understand Google Drive API notification events".

#### 1c. Retry / status semantics

From the Google docs verbatim:

> If your service uses Google's API client library and returns `500`, `502`, `503`, or `504`, the Google Drive API retries with exponential backoff. Every other return status code is considered to be a message failure.

Notably **un**documented (drift risk):
- Number of retry attempts
- Total retry window
- Backoff intervals

Implication: any non-5xx response (including our `422` hard-gate refusal pattern from GitHub/Slack/Linear) is final. There is no Drive-side replay of a `422` like we get from GitHub.

### 2. Authentication & Verification Model

The Drive notification model has **no cryptographic verification primitive**. From the docs verbatim, the `token` is used to:

> verify that each incoming message is for a channel that your application created—to ensure that the notification is not being spoofed—or to route the message.

Verification therefore reduces to:
1. HTTPS termination at our reverse proxy / tunnel (the cycle-5 server listens on plain HTTP and requires `--allow-public` for non-loopback bind; Drive callback URL MUST be HTTPS, so operator deployment MUST front us with TLS).
2. Constant-time comparison of `X-Goog-Channel-Token` against the token we stored at channels.watch time, keyed by `X-Goog-Channel-Id`.
3. Cross-check that `X-Goog-Resource-Id` matches the resource-id Google returned in the channels.watch response (Google returns this in the response body — we MUST persist it at creation time; there is no way to recover it from the notification alone).

Things that are explicitly NOT provided:
- HMAC of body (body is empty — nothing to sign)
- Signature header
- Replay defense (no timestamp gate, no monotonic counter gate — `X-Goog-Message-Number` is non-sequential per the docs, so we cannot use it as a window check)
- Domain ownership verification — historical Drive API v2 required the callback domain be verified via Search Console; **this requirement was removed for v3** and is not documented as a current requirement. The only domain-level gate now is the SSL certificate's CN matching the callback hostname.

Source: same doc; corroborated by absence of "verify" / "domain ownership" / "Search Console" language in the v3 push guide; Drive API v2 push docs (now deprecated) had a "verifying ownership" section that is gone.

### 3. Channel Lifecycle

#### 3a. Creation
`channels.watch` returns a Channel resource containing the same fields we sent plus a **`resourceId`** (the value Google will put in `X-Goog-Resource-Id` on notifications). We MUST persist `(channel_id, resource_id, token, expiration, watched_resource_kind, watched_resource_id)` at creation time. The `resource_id` cannot be recovered later — losing it means we cannot validate notifications OR stop the channel.

#### 3b. Expiration
Channels expire automatically at `expiration`. No grace period. Google does NOT notify us when a channel expires — it simply stops sending notifications. The operator must renew before expiration by issuing a fresh `channels.watch` for the same resource with a NEW `id` (the old one is dead) and then stopping the old channel.

Implication: cycle 9 needs a renewal job. A background task that walks the channel registry, finds channels expiring in <24h, creates a successor, and stops the predecessor. If the operator's process dies and stays dead past expiration, ingest silently halts. We need a sentinel.

#### 3c. Cancellation
`POST https://www.googleapis.com/drive/v3/channels/stop` with body `{"id": <channel_id>, "resourceId": <resource_id>}`. Only the OAuth principal that created the channel can stop it (or any principal under the same service-account credentials). Returns 204 on success.

### 4. Notification-as-Trigger vs Notification-as-Payload

This is the largest design departure from cycles 5-7.

For `changes.watch`, the notification body is empty and the headers tell us only "something happened on the watched resource." To learn WHAT happened, the handler must:

1. Load the `startPageToken` we saved at channel creation time (or the last token from the previous changes.list pull).
2. Call `changes.list(pageToken=<saved>)` — this returns the list of changes since that token.
3. Iterate the changes; for each change with `kind == "drive#change"` and a `fileId` matching our watched files, fetch the doc via the existing `sources/google_drive/adapter.py` path.
4. Update the watermark to the `newStartPageToken` returned by the final `changes.list` page.

`getStartPageToken` is canonical here: from the docs, the page token "doesn't expire" and "is the starting page token for listing future changes" — meaning we cannot defer obtaining it until the first notification arrives. We MUST call `getStartPageToken` BEFORE `channels.watch` and persist the result; otherwise the first notification has no anchor and we lose any changes that occurred between channel creation and the first list-call.

Source: `https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/getStartPageToken`.

### 5. Sync Message

The first notification after channel creation has `X-Goog-Resource-State: sync` and `X-Goog-Message-Number: 1`. Per the docs verbatim: **"safe to ignore."** Operationally we should still return 200 (so Google marks the channel as confirmed-reachable) but skip ingest. This is parity with Slack's URL-verification handshake.

### 6. Threat Model

What an attacker who can reach our public callback URL can do:

| Attack | Mitigation |
|---|---|
| Spoof a notification with a random `X-Goog-Channel-Id` | Channel-id lookup fails → 401 |
| Spoof with a known `X-Goog-Channel-Id` + wrong token | Token compare_digest fails → 401 |
| Spoof with leaked token (insider, log scrape, or MITM-without-TLS) | Token equality holds → 200; attacker can force us to call `changes.list`, burning API quota; cannot inject decisions because `changes.list` is server-of-record |
| Replay a captured legitimate notification | Token + channel-id + resource-id all match → 200; we re-call `changes.list`; if no new changes since the saved page token, we ingest nothing; if there ARE new changes, we ingest them once (the page token advances) — replay is bounded by the page-token watermark |
| DoS by flooding callback | Cycle-5 hardening (50 concurrent, 60s total budget, body cap) still applies; attacker burns more of their resources than ours |
| Force token-comparison timing oracle | `hmac.compare_digest` between equal-length strings is constant-time; tokens are operator-set and we control length |

What an attacker who has stolen the OAuth refresh token can do: everything we can do, plus they can `channels.stop` our channels and silently terminate ingest. Mitigation is out of scope for the webhook handler — token security is `secrets_store`'s problem.

**Severity assessment vs HMAC providers:** Drive's model is one notch weaker because the verification primitive is operator-supplied rather than provider-supplied. With GitHub/Slack/Linear, the secret is provisioned at provider-config time and never appears in our outbound traffic; with Drive, the token is sent BY us TO Google at channels.watch time and then sent BY Google TO us on every notification — every hop is a potential leak surface. The pragmatic implication is that tokens should be high-entropy (256 bits, base64-encoded) and rotated on every channel renewal (cheap because we already issue new channels at renewal time).

### 7. Comparison Against Existing Drive Code

`sources/google_drive/auth.py` (Phase 5b, #427) and `sources/google_drive/folder.py` (Phase 5c) define the OAuth handshake and folder-polling primitives. Scopes already configured: `documents.readonly` + `drive.metadata.readonly`. **`changes.watch` and `changes.list` require `drive` or `drive.readonly` scope, not the narrower `drive.metadata.readonly`** — see `https://developers.google.com/identity/protocols/oauth2/scopes#drive`. Cycle 9 must either expand the scope (forcing every existing operator to re-consent) or use the per-file `files.watch` path which works with the existing scope. Trade-off:

- `changes.watch` (account-wide): one channel covers everything, but needs scope expansion
- `files.watch` (per-file): no scope expansion, but operators with N watched docs need N channels — multiplies our channel-registry size and renewal job complexity

Existing `sources/google_drive/adapter.py` and `folder.py` use `googleapiclient.discovery.build("drive", "v3", credentials=creds, cache_discovery=False)`. Cycle 9 should use the same builder, NOT a hand-rolled HTTP client — keeps quota/retry/auth handling consistent with the active-fetch path.

---

## Blueprint Alignment

| Blueprint Claim | Actual Finding | Status |
|---|---|---|
| `ARCHITECTURE_PLAN.md` does not mention Drive webhooks (Phase 5b/5c shipped polling-only) | Confirmed via grep | NO DRIFT (new design space) |
| `sources/google_drive/__init__.py` scope set includes `drive.metadata.readonly` | Drive's `changes.watch` needs `drive` or `drive.readonly` — broader than metadata-only | **GAP** — not drift against the plan, but cycle 9 design must choose `files.watch` OR scope expansion |
| Memory snapshot `integrations_337_landed.md` says Drive active-ingest is live | Confirmed; no webhook scaffolding present | NO DRIFT |
| Cycle 5/6/7 hardening (slow-loris, body cap, smuggling defenses) inherited | The cycle-5 `webhooks/server.py` applies to every route equally | MATCH — Drive route inherits all cycle-5 defenses |
| Cycle 6/7 lesson: adversarial review required before push | This brief recommends the same gate for cycle 9 | MATCH |

---

## Recommendations

### Priority 1 (must do before any cycle 9 code lands)

1. **Pick the channel strategy.** `files.watch` (no scope change, complexity in renewal job) vs `changes.watch` (scope expansion, single channel). Recommend: **`files.watch` for v0**, defer `changes.watch` to a later cycle. Reasoning: scope expansion forces every existing operator to re-consent — that's a UX cliff. The per-file complexity is bounded (operators with >50 watched docs are unusual in our user base per the inventory doc).
2. **Add a channel registry** (`channel_id`, `resource_id`, `token`, `expiration_ms`, `file_id`, `start_page_token` if `changes.watch`). Two viable stores: SurrealDB table or a JSON file in `~/.bicameral/`. Recommend SurrealDB table — gives us atomicity for the "create new, persist, stop old" renewal sequence.
3. **Add a renewal job** running every 6 hours (Drive `files.watch` max TTL is 24h, so 6h cadence gives us 4x headroom). On wake, enumerate channels expiring in <12h, issue a successor with a fresh token, persist, then stop the predecessor.

### Priority 2 (cycle 9 in-scope but can be follow-up)

4. **Add the `/webhooks/google-drive` route** to `webhooks/server.py` per the cycle-5/6/7 pattern. Handler returns `(status, body)` tuple; route dispatches via `asyncio.to_thread`.
5. **Verify primitive:** `_verify_notification` checks (a) `X-Goog-Channel-Id` known, (b) constant-time compare on `X-Goog-Channel-Token`, (c) `X-Goog-Resource-Id` matches the stored resource_id for that channel.
6. **Sync-message handler:** if `X-Goog-Resource-State == "sync"` and `X-Goog-Message-Number == "1"`, return 200 + ignore (per Drive's docs).
7. **Change handler:** for non-sync states, mark the channel "dirty," return 200 immediately, and process the actual `files.get`/`changes.list` follow-up on a worker (do NOT do it synchronously — Drive's retry posture penalizes >timeout responses).
8. **Adversarial review by `code-reviewer`** before push, per cycle 6/7 precedent. The reviewer should specifically be asked to scrutinize: (a) token comparison constant-time correctness, (b) channel-id lookup TOCTOU between verify and dispatch, (c) what happens when the registry doesn't contain a channel-id Google has sent (legitimate edge case: operator restored state from backup that pre-dates the channel creation), (d) what happens when `X-Goog-Resource-Id` matches a different channel than `X-Goog-Channel-Id` claims (signed-body/unsigned-header analogue from the Linear H3 finding).

### Priority 3 (deferred)

9. **`changes.watch` (account-wide) implementation** — when we're ready to ask operators for scope re-consent, this collapses N-channel complexity to 1 and gives us reaction-time on every Drive event the operator can see.
10. **Per-channel token rotation cadence** — rotate on every renewal (every 6h) for cheap defense-in-depth.

---

## Updated Knowledge

`docs/SHADOW_GENOME.md` is intentionally NOT updated by this brief — Shadow Genome captures failure modes, not external API knowledge. The brief itself is the canonical reference; cycle 9 implementation MUST link to this file in its PR description and skill (per `CLAUDE.md` "Tool Changes Require Skill Changes" if a new MCP-visible primitive is added).

`docs/integrations-settings-inventory.md` should be updated to add the Drive Webhook row when cycle 9 lands (deferred to that cycle's PR per the cycle-2/3/4 housekeeping pattern).

No `qor/references/` doctrine update needed — the cycle-5/6/7 webhook-receiver pattern (verify → dedup → dispatch) generalizes, and the Drive-specific divergence (token equality instead of HMAC, follow-up `changes.list` instead of body parse) is local to the cycle 9 handler.

---

## Provenance

- Primary source: `https://developers.google.com/drive/api/guides/push` (fetched 2026-05-20)
- Secondary: `https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/getStartPageToken` (fetched 2026-05-20)
- Internal: `sources/google_drive/auth.py:38-49` (existing OAuth scope set), `sources/google_drive/folder.py:34-66` (existing Drive API discovery client pattern), `webhooks/server.py:191-217` (cycle-5/6/7 route pattern), `webhooks/linear.py` (cycle 7 H3 cross-validation pattern, applicable to Drive resource-id/channel-id pair)
- Gate artifact: **NOT EMITTED.** `qor/` Python package is not installed in this worktree (same shortfall noted in `docs/META_LEDGER.md` Entry #53). Skill Step 8.5 (`gate_chain.write_gate_artifact`) cannot run; the brief stands on its content per the Entry #53 precedent.

---

_Research complete. Findings are advisory — implementation decisions for cycle 9 remain with the Governor._
