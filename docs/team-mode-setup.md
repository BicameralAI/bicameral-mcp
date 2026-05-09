# Team mode setup

Team mode replicates your decision ledger across teammates' machines so a
PM can ingest a PRD and your engineers see those decisions in their next
`bicameral.preflight` call. Same pull-only, append-only event-log model as
solo mode — just with a shared remote substrate the wizard provisions for
you.

This page walks you through setup end-to-end, covering both backends, the
OAuth client provisioning step, the security posture, and verification.

## Backends

| Backend | When to use | What you provision |
|---|---|---|
| `google_drive` | Default. Anyone with a Google account, no shared filesystem required. | A Google Drive folder + an OAuth client (3 minutes, one-time per machine). |
| `local_folder` | Advanced. Your team already has a shared filesystem (NFS, Dropbox, syncthing). | A directory path everyone has mounted. |

Both backends carry the same wire shape: one append-only `<author-email>.jsonl`
file per teammate, written-by-author / read-by-everyone, deduplicated at
the database layer via canonical IDs. There is no central server.

## Create vs Join

Team-mode setup branches into two flows:

- **Create** — you're the first teammate. The wizard creates a Drive folder
  on your account, prints the folder ID, and tells you the literal text to
  send your teammates. You become the *founding member* (recorded in
  `config.yaml`); the Drive folder ACL is governed by your Google account.
- **Join** — a teammate has already created the shared folder. They send
  you the folder ID; you paste it; the wizard verifies your access (404 →
  not shared yet, read-only → ask for Editor) and confirms how you'll
  appear in the ledger before persisting.

For LocalFolder there is no Create vs Join distinction — filesystem ACLs
on the shared directory determine who's in the team.

## OAuth — what happens, what we see

Bicameral ships with its own Google OAuth client (the same pattern `gh`,
`gcloud`, and `cursor` use). When you run `bicameral-mcp setup` and pick
Create or Join, the wizard prints a colored security disclosure **before**
opening your browser, then triggers the standard Google consent flow on
`localhost`. Click Allow once; you're done.

### What flows where

- **Decision data (transcripts, payloads)** flows your-CLI ↔ Google
  directly. Bicameral the company does NOT receive copies. No Bicameral
  server is in the loop.
- **Your OAuth token** lives at `~/.bicameral/google-drive-token.json`,
  mode 0600, on your machine.

### What the `drive.file` scope means for the rest of your Drive

The Bicameral CLI on your machine can only touch files it creates in the
team folder. Your other Drive content (other folders, Google Docs, shared
files) is invisible to the CLI — Google enforces this server-side. This
is the protection the `drive.file` scope is designed to give.

### What Bicameral the company CAN see (as the OAuth app publisher)

As the publisher of the OAuth client, we receive limited telemetry from
Google's OAuth dashboard:

- **Aggregate API request counts.** Not contents. ("5,000 Drive API
  calls last week from this OAuth client.")
- **OAuth consent records.** Which Google accounts authenticated against
  the Bicameral app, and when.

We do NOT receive: file contents, file names, folder names, folder IDs,
team membership, or who is collaborating with whom on what.

### The trust dependency you're accepting

The OAuth flow itself can't leak file contents to us — your token stays
on your machine, and Drive API calls bypass our infrastructure entirely.
The realistic threat is *"what if Bicameral pushed a malicious CLI
update that read transcripts and POSTed them to bicameral-ai.com?"* —
the same trust dependency you accept with any OAuth tool you install
(`gh`, `gcloud`, Notion, Slack desktop, Cursor, etc.). The mitigation is
that Bicameral is open source: any exfiltration code would be visible in
the diff. Source:
[github.com/BicameralAI/bicameral-mcp](https://github.com/BicameralAI/bicameral-mcp).

## Run setup — Create flow

```
$ bicameral-mcp setup
  …
? Collaboration mode: Team — decisions shared via git (append-only event files)
? How do you want to set up the shared ledger?
  ❯ Create a new shared ledger (you become the founding member)
    Join an existing shared ledger (paste a folder ID from a teammate)
    Use a shared filesystem instead (NFS, Dropbox, syncthing) — advanced

  [browser opens for Google OAuth, you grant Drive access]

  Created shared ledger folder: bicameral-myrepo-ledger
  Folder ID: 1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd
  URL: https://drive.google.com/drive/folders/1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd

  Send this to your teammates so they can Join:
    "Share this folder with my teammate as Editor, then run `bicameral
     setup` and paste this folder ID: 1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd"
```

After Create completes, share the Drive folder with each teammate as
**Editor** in the Drive UI — the wizard does not auto-share, because your
Google account governs the ACL.

## Run setup — Join flow

```
$ bicameral-mcp setup
  …
? How do you want to set up the shared ledger?
    Create a new shared ledger
  ❯ Join an existing shared ledger (paste a folder ID from a teammate)
    Use a shared filesystem (advanced)

? Paste the shared ledger folder ID (or full Drive URL) from your teammate:
  1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd

  [browser opens for Google OAuth]
  [verify_access checks the folder is shared and writable]

? You'll appear in the team ledger as `alice`. Continue? [y/N]
  y
```

The identity confirmation defaults to **No**. If the resolved signer
(governed by `signer_email_fallback` in `config.yaml`) doesn't match how
you want to appear in the team ledger, decline, edit your config, and
re-run.

## Verifying replication

1. On machine A: `bicameral.ingest` a small decision (e.g. one line of
   meeting notes).
2. Wait up to 30 seconds (the in-process pull TTL).
3. On machine B: run any tool (`bicameral.history` is convenient). The
   decision should appear.

If it doesn't:

- Check `tail -F ~/.bicameral/local/bicameral.log` on B for `[gdrive]` or
  `[sync_middleware]` warnings.
- Confirm B's account has Editor on the Drive folder.
- The 30 s TTL is a per-process cache; restarting the MCP server clears it.

## Permissions and revocation

| Action | Effect |
|---|---|
| Founding member shares folder as Editor | Teammate can Create + read peer files. |
| Founding member shares folder as Reader | Join wizard fails with `ReadOnlyAccessError` (won't persist config). |
| Founding member revokes a teammate's access | Their next `push_events` call silently fails (DEBUG-logged); their existing `<email>.jsonl` in the folder remains until manually deleted. Their local DB still has every decision they ever ingested or pulled — event logs are append-only and durable. |

## Privacy posture

- **Token cache.** `~/.bicameral/google-drive-token.json`, mode 0600 on
  POSIX. Contains a refresh token; treat it like an SSH key.
- **OAuth scope.** `https://www.googleapis.com/auth/drive.file` only —
  Bicameral can read/write only files it created or that you opened
  through Bicameral. Other Drive content is invisible.
- **Author identity in the ledger.** Each event's `author` field is your
  resolved signer (governed by `signer_email_fallback` in `config.yaml`):
  - `redact` — `<REDACTED>`, no attribution.
  - `local-part-only` (default) — the part before `@`. Privacy-positive.
  - `full` — verbatim email. Opt-in.
- **What's in the JSONL.** Decision payloads, canonical IDs, signoffs,
  region descriptors. No source code is uploaded.

## Local-folder backend

Single prompt, no OAuth. Pick a path everyone has mounted (NFS,
Dropbox, syncthing, etc.):

```
? How do you want to set up the shared ledger?
  ❯ Use a shared filesystem (advanced)

? Path to the shared folder (must exist on every teammate's machine):
  /Volumes/team-share/bicameral-myrepo
```

Filesystem ACLs determine team membership. Same per-author JSONL layout,
same TTL-cached pull on tool dispatch.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Join wizard exits with `Drive folder ... not found` | Folder ID typo OR founding member hasn't shared yet | Confirm ID; ask founding member to share as Editor. |
| Join wizard exits with `read-only for this account` | You were granted Viewer, not Editor | Ask founding member to upgrade your role in the Drive UI. |
| `OAuthClientNotProvisionedError` on first run | You're running a dev build before the bundled OAuth client was published — file an issue. | Wait for the next release, or build from a tag with the published client. |
| "Google hasn't verified this app" warning | Bicameral's OAuth app verification is still pending with Google. | Click "Advanced" → "Go to Bicameral (unsafe)" — the app is published; verification badge clears once Google completes review. |
| Peer events don't appear after 30 s | Pull cache TTL OR backend silently failing | Check `~/.bicameral/local/bicameral.log` for `[sync_middleware] team pull failed` lines. |
| OAuth refresh fails | Cached refresh token expired/revoked | Delete `~/.bicameral/google-drive-token.json` and re-run setup. |
