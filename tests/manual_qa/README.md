# Manual QA — Slack OAuth E2E (PR #153)

Covers the two unchecked manual items in the PR description:

1. `docker-compose -f deploy/team-server.docker-compose.yml up` → `/health` returns OK
2. Slack OAuth round-trip in a dev workspace; encrypted token persists

The CI path is in `.github/workflows/slack-oauth-manual-qa.yml`. It is
`workflow_dispatch`-only and gated by the `recording-approval` GitHub
environment, so it never runs without a maintainer clicking Approve.

## One-time setup (before first CI run)

Set these as **repository secrets** (or environment secrets on
`recording-approval`):

| Secret | What it is |
|---|---|
| `SLACK_CLIENT_ID` | OAuth app client ID from your dev Slack app |
| `SLACK_CLIENT_SECRET` | OAuth app client secret |
| `SLACK_STORAGE_STATE_B64` | base64 of a Playwright `storage_state.json` for a logged-in test Slack user (capture steps below) |

The Fernet key for token-at-rest encryption is generated fresh each run —
no secret needed.

### Capturing `SLACK_STORAGE_STATE_B64`

Slack rejects automated logins, so the test reuses a saved session.

```bash
pip install playwright && playwright install chromium
python -c '
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=False)
    ctx = b.new_context()
    p = ctx.new_page()
    p.goto("https://slack.com/signin")
    input("Log in to your test workspace, then press Enter...")
    ctx.storage_state(path="slack-state.json")
    b.close()
'
base64 -i slack-state.json | pbcopy   # paste as SLACK_STORAGE_STATE_B64
```

Use a **dedicated test workspace and user** — not your real one. Slack
sessions in `storage_state.json` grant full account access to anyone
who has the file.

### `recording-approval` environment

This environment already exists for `v0-user-flow-e2e.yml` and has
required-reviewer rules attached. The new workflow reuses it; no
additional setup needed.

## Local run

```bash
# 1. Generate a Fernet key and start the stack
export BICAMERAL_TEAM_SERVER_SECRET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export SLACK_CLIENT_ID=...
export SLACK_CLIENT_SECRET=...

# 2. Start a tunnel (separate terminal); copy the trycloudflare.com URL
cloudflared tunnel --url http://localhost:8765

# 3. Tell the team-server about the public URL, then start it
export SLACK_REDIRECT_URI="https://<random>.trycloudflare.com/oauth/slack/callback"
docker compose -f deploy/team-server.docker-compose.yml \
  -f tests/manual_qa/docker-compose.override.yml up -d

# 4. Run the tests
export MANUAL_QA_PUBLIC_URL="https://<random>.trycloudflare.com"
export SLACK_STORAGE_STATE_PATH="$PWD/slack-state.json"
pip install pytest playwright httpx && playwright install chromium
pytest tests/manual_qa/ -v -s
```

Videos land in `pytest`'s tmp dir; the test prints the path.
