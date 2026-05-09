# Google OAuth verification submission — Bicameral MCP

This is the operator-facing checklist + ready-to-paste text for getting
Bicameral's Drive OAuth client verified by Google. Run through this once
per project lifetime. Until verification clears, users see a "Google
hasn't verified this app" interstitial — they can click through (advanced
→ "go to Bicameral (unsafe)") but it scares non-technical users.

## Prerequisites (already done)

- [x] GCP project `bicameral-mcp` created
- [x] Linked to billing account `01647A-138E06-EE9A8B`
- [x] Drive API enabled

## Web-console steps

### 1. OAuth consent screen

URL: https://console.cloud.google.com/apis/credentials/consent?project=bicameral-mcp

| Field | Value |
|---|---|
| User Type | **External** |
| App name | `Bicameral` |
| User support email | `support@bicameral-ai.com` (or `jin@bicameral-ai.com` while support@ isn't set up) |
| App logo | Upload `assets/bicameral-icon-120.png` (must be 120×120 PNG, under 1 MB). If we don't have one yet, leave blank — Google won't block submission, but verified apps look more legitimate with a logo |
| Application home page | `https://bicameral-ai.com` |
| Application privacy policy | `https://bicameral-ai.com/privacy` |
| Application terms of service | `https://bicameral-ai.com/terms` |
| Authorized domains | `bicameral-ai.com` |
| Developer contact email | `jin@bicameral-ai.com` |

### 2. Scopes

Add **only** this one scope:

- `https://www.googleapis.com/auth/drive.file` — "View and manage Google Drive files and folders that you have opened or created with this app."

> Critical: do NOT add `drive`, `drive.readonly`, `drive.metadata`, or any
> other Drive scope. `drive.file` is non-sensitive — it skips the "restricted
> scope" review path and is much faster to verify (days vs weeks).

### 3. Test users (during dev / before verification)

Add yourself + any teammates running the unverified flow:

- `jin@bicameral-ai.com`
- (anyone else from the bicameral-ai.com domain)

Once verified, this list is unused — anyone with a Google account can
authenticate.

### 4. OAuth client (Desktop app)

URL: https://console.cloud.google.com/apis/credentials?project=bicameral-mcp

- Click **Create Credentials → OAuth client ID**.
- Application type: **Desktop app**.
- Name: `Bicameral CLI` (Google-internal label, not user-visible).
- Click Create. Download the JSON.

Open the JSON. Copy `client_id` and `client_secret` into
`events/backends/google_drive.py` — replace these constants:

```python
_BUNDLED_CLIENT_ID = "REPLACE_WITH_BICAMERAL_DRIVE_OAUTH_CLIENT_ID.apps.googleusercontent.com"
_BUNDLED_CLIENT_SECRET = "REPLACE_WITH_BICAMERAL_DRIVE_OAUTH_CLIENT_SECRET"
```

Commit. Push. Cut a release.

### 5. Verification submission

Required because we're publishing externally and want the consent screen
without the unverified-app warning. URL:
https://console.cloud.google.com/apis/credentials/consent?project=bicameral-mcp
→ click **Publish App** → submission form opens.

#### Verification justification — paste this

**App functionality**

> Bicameral is an open-source MCP (Model Context Protocol) server for AI
> coding assistants. It maintains a local decision ledger that maps
> meeting decisions to code regions. In team mode, the CLI uses Google
> Drive as a pull-only replication substrate so teammates' decision logs
> sync between machines without operating a central server.

**Why each scope is needed**

> `drive.file` — Bicameral creates one append-only JSONL event log per
> teammate (`<email>.jsonl`) inside a single shared folder the team
> creates. Each user's CLI reads peer files (created by other Bicameral
> instances within the same folder) and writes their own. Bicameral never
> needs access to the user's other Drive files, so the narrow `drive.file`
> scope is sufficient.

**Demo video script (2-3 minutes; record once)**

1. Open Bicameral landing page (`https://bicameral-ai.com`).
2. Show terminal: `bicameral-mcp setup`.
3. When wizard asks "How do you want to set up the shared ledger?", select
   "Create a new shared ledger".
4. Cut to the colored security disclosure the wizard prints — pause on
   screen for 3 seconds so reviewers see it.
5. Browser opens, OAuth consent screen appears. Click Allow.
6. Cut back to terminal — wizard prints folder ID, instructions to share
   with teammates.
7. Open Drive in another tab — show the new `bicameral-<repo>-ledger`
   folder. Show the empty folder.
8. In the terminal, ingest a small decision (e.g. `bicameral-mcp ingest
   --text "We decided to ship pull-only sync"`). Show that it succeeded.
9. Cut to Drive — refresh the folder. Show the new `<email>.jsonl` file
   (one event line). Open it briefly to show the JSON event structure.
10. Cut to the privacy policy page in the browser — pause on the section
    that explains what Bicameral can and cannot see.

Upload the unlisted YouTube video link to the verification form.

**Privacy policy must include**

> Bicameral collects no personal data through the Google Drive
> integration. The Drive OAuth flow grants Bicameral the `drive.file`
> scope, which permits the application to read and write only files it
> creates within the shared folder you provision. Bicameral does not
> upload, transmit, or store user data on any Bicameral-operated server;
> all decision data is replicated peer-to-peer through your team's own
> Google Drive folder. As the OAuth application owner, Bicameral receives
> aggregate API usage analytics from Google (e.g. request counts) and
> per-user OAuth consent records (which Google accounts authenticated
> against the Bicameral app). We do not link this telemetry to any
> identifying information beyond the Google account that authenticated
> and we do not share it with third parties.

(Adapt to fit the rest of the bicameral-ai.com privacy policy structure.)

#### Submit

Click **Submit for verification**. Google replies in 1-2 days for
non-sensitive `drive.file` scope; sometimes longer if they ask follow-up
questions. Reply promptly — slow operator response is the #1 reason
verification stalls.

## After verification

- Replace the placeholder constants in `events/backends/google_drive.py`
  with the published `client_id` + `client_secret` (if not already done in
  step 4).
- Update `tests/test_backends_google_drive_unit.py::test_bundled_client_config_raises_when_placeholders_present`
  — it'll auto-skip once placeholders are gone, but you can also rewrite
  it to assert the published config dict is well-formed.
- Remove the "Provision an OAuth client" section from
  `docs/team-mode-setup.md` (already done — that section was removed when
  we pivoted from operator-supplied to bundled client).
- Cut a release; users get the 1-click flow with no unverified-app
  warning.

## If verification gets denied

Most common reasons + fixes:

| Reason | Fix |
|---|---|
| Privacy policy doesn't mention Google scopes | Add the boilerplate paragraph above |
| Demo video doesn't show the OAuth flow | Re-record with the consent screen visible |
| Application home page doesn't link to OAuth-using product | Make sure the landing page mentions the team-mode feature |
| Trademark concern with "Bicameral" | Provide trademark documentation if challenged (we own the domain) |
