# Jira Integration — Operator Setup

How to connect a **Jira Cloud** site to Bicameral so that Jira issues,
comments, and status transitions flow into the decision ledger.

Bicameral ingests Jira two ways, and you can use either or both:

- **Active** — paste a Jira issue URL into `bicameral.ingest` and it is
  fetched on demand.
- **Passive** — register a webhook in Jira; issue/comment events are
  ingested as they happen, with no polling.

This guide covers the **admin-UI webhook** path (you register the webhook
once in Jira's own settings). It is the recommended setup: one-time, no
OAuth, no token-renewal job. The OAuth / dynamic-webhook variant is out of
scope for v0.

> Jira **Cloud** only. Jira Server / Data Center is not supported.

---

## Prerequisites

- A Jira Cloud site (`https://<your-tenant>.atlassian.net`).
- A **Jira site admin** account — required to register the webhook
  (Step 4). Active ingest alone (Steps 1–3) does not need admin.
- Bicameral's webhook receiver reachable from the internet over **HTTPS**
  (see the port note in Step 4). Jira Cloud only delivers to TLS endpoints.

---

## Step 1 — Create an Atlassian API token

Active ingest authenticates with HTTP Basic auth — your account email plus
an API token (not your password).

1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. **Create API token**, give it a label (e.g. `bicameral-mcp`), and copy
   the value. You cannot retrieve it again later.

The token's account needs **Browse projects** permission on every project
you want to ingest from.

---

## Step 2 — Store the credentials

Bicameral keeps all source credentials in the OS keyring via
`secrets_store` — never in `.bicameral/config.yaml`. Jira uses three keys
under `source_id="jira"`. Store each one (paste your own values):

```bash
python -c "from secrets_store import put_secret; put_secret(source_id='jira', key='api_email', value='you@example.com')"
python -c "from secrets_store import put_secret; put_secret(source_id='jira', key='api_token', value='<your-api-token>')"
```

The third key — `webhook_secret` — is the shared HMAC secret for the
passive webhook; you will generate and store it in Step 3.

| Key | Used by | Purpose |
|---|---|---|
| `api_email` | active ingest | Basic-auth account email |
| `api_token` | active ingest | Basic-auth API token (Step 1) |
| `webhook_secret` | passive webhook | HMAC-SHA256 shared secret (Step 3) |

If you only want active ingest, you can stop after this step.

---

## Step 3 — Generate and store the webhook secret

The passive webhook is verified with an HMAC-SHA256 signature. Pick a
strong random secret — you will paste the *same* value into Bicameral here
and into Jira in Step 4.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # generate one
python -c "from secrets_store import put_secret; put_secret(source_id='jira', key='webhook_secret', value='<the-secret>')"
```

The webhook receiver **fails closed**: with no `webhook_secret` stored it
refuses every delivery (HTTP 500 with setup guidance) rather than accepting
unverifiable traffic. Keep the secret — Jira does not let you retrieve it
again after the webhook is saved (Step 4); if lost, re-create the webhook.

---

## Step 4 — Register the webhook in Jira

As a Jira site admin: **Jira Settings → System → WebHooks → Create a
WebHook**.

| Field | Value |
|---|---|
| **Name** | e.g. `Bicameral ingest` |
| **URL** | your Bicameral receiver, path **`/webhooks/jira`** — e.g. `https://bicameral.example.com/webhooks/jira` |
| **Secret** | the exact `webhook_secret` value from Step 3 (this enables `X-Hub-Signature` HMAC signing) |
| **JQL filter** | optional — scopes which issues fire the webhook, e.g. `project in (PROJ, OPS)`. Leave empty to watch everything the webhook can see. |
| **Events** | enable: **Issue created**, **Issue updated**, **Comment created**, **Comment updated** |

**Callback port restriction.** Jira Cloud only delivers to these ports:
`443`, `1880-1890`, `4044`, `6017`, `7990`, `8060`, `8080`, `8085`,
`8089`, `8090`, `8443`, `8444`, `8900`, `9900`, `9420`, `9520`.
**Port 80 is not allowed** — front the receiver with TLS on `443` (or one
of the others).

> Jira signs deliveries with the **`X-Hub-Signature`** header (note: *not*
> GitHub's `X-Hub-Signature-256` — same algorithm, different header name).
> You do not configure the header name anywhere; this is just so the
> behaviour is not a surprise if you inspect deliveries.

Save. Jira does not show the secret again after saving.

---

## Step 5 — (Optional) Status-transition ingest

By default the webhook ingests issue descriptions and comments. You can
*also* have a Jira issue's transition into a "done-like" status recorded as
a decision — e.g. when `PROJ-123` moves to `Done`.

This is **opt-in per project** in `.bicameral/config.yaml`:

```yaml
jira:
  status_transitions:
    PROJ: ["Done", "Released"]
    OPS:  ["Closed"]
```

- A map of **project key → list of terminal status names**.
- The map keys are the **allowlist**: a project not listed gets no
  transition ingest.
- Status names and project keys are matched **case-insensitively**.
- **Fail-closed**: if this section is absent or malformed, transition
  ingest is simply off — issue/comment ingest is unaffected.

---

## Verify it works

- **Active** — paste a Jira issue URL
  (`https://<tenant>.atlassian.net/browse/PROJ-123`) into `bicameral.ingest`.
  The decision should appear in the ledger / dashboard.
- **Passive** — edit an issue or add a comment in an in-scope project, then
  check the dashboard. Jira's WebHooks admin page also shows recent
  delivery attempts and their response codes (a `200` means accepted).

---

## Revoking access

- **Stop passive ingest** — delete the webhook in Jira's WebHooks admin
  page. (The receiver also rejects deliveries once the secrets no longer
  match.)
- **Remove stored credentials** —
  `python -c "from secrets_store import delete_secret; delete_secret(source_id='jira', key='webhook_secret')"`
  (repeat for `api_token` / `api_email`).
- **Revoke the API token** at
  <https://id.atlassian.com/manage-profile/security/api-tokens>.
