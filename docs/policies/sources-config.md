# `.bicameral/config.yaml` — `sources:` schema (#279 Phase 1)

The `sources:` top-level key configures pull-based meeting-ingestion adapters used by `bicameral-mcp sync-and-brief`.

## Shape

```yaml
sources:
  - type: granola
    api_key_env: GRANOLA_API_KEY
    # base_url: https://api.granola.ai  # optional override
```

Each entry is a dict. Required fields per type:

| `type` | Required keys | Optional keys |
|---|---|---|
| `granola` | `api_key_env` | `base_url` |
| `local_directory` | `path` | `extensions`, `source_type_label`, `max_file_bytes` |

## API key handling (rationale)

**The config file holds the env-var name, not the key.** This is deliberate:

1. `.bicameral/config.yaml` is project-local and operators sometimes commit it accidentally.
2. The actual API key in `os.environ` lives in the operator's shell or secret manager — outside the repo by construction.
3. Tooling that does secret scanning (TruffleHog, etc.) looks for keys, not env-var names; the `api_key_env` indirection passes secret-scan CI cleanly.

If the env var is unset or empty when `sync-and-brief` runs, the adapter raises `MissingApiKeyError` and the CLI prints a friendly message + the env-var name. The session-start hook still exits 0.

## Watermarks

Per-source watermarks live at `~/.bicameral/source-watermarks/<source-name>.json` — outside the repo, in the user's home directory:

```json
{
  "last_synced_at": "2026-05-14T10:00:00Z",
  "written_at": "2026-05-14T10:01:23.456789+00:00"
}
```

The watermark only advances on **two-phase commit**: the source pulls items, the CLI ingests them, and only after every ingest succeeds does the adapter persist the new watermark. If ingest fails, the watermark stays put so the next run re-receives the un-ingested items.

## `local_directory` source (#344)

Captures decisions made outside the IDE — planning sessions, brainstorms, design docs, meeting notes — by watching a configured local directory. Drop a file into the directory; the next `bicameral-mcp sync-and-brief` ingests it.

```yaml
sources:
  - type: local_directory
    path: ~/.bicameral/captured-notes
    # extensions: [.md, .txt, .json]    # defaults shown
    # source_type_label: planning        # default; override e.g. design-doc
    # max_file_bytes: 1048576            # 1 MiB default; oversized files skipped
```

### Behavior

- **Non-recursive.** Only files directly inside `path` are considered. Subdirectories and their contents are ignored. Hidden files (`.`-prefixed) are ignored.
- **Extension-filtered.** Default extensions are `.md`, `.txt`, `.json`. Override via `extensions`. Matching is case-insensitive on the file suffix.
- **Watermark-driven.** Each pull returns only files whose mtime is strictly greater than the last confirmed watermark. The watermark stores the maximum mtime seen, as an ISO 8601 string, in `~/.bicameral/source-watermarks/local_directory.json`.
- **Two-phase commit.** The watermark only advances after the CLI confirms every ingest succeeded — failed ingest = watermark stays put = next run retries the same files.
- **Size-capped.** Files larger than `max_file_bytes` (default 1 MiB, matching the ingest payload-size cap) are skipped with a stderr warning; their mtime is **not** added to the watermark-candidate set, so a future run after the file shrinks will pick them up.
- **No file mutation.** The adapter never deletes, moves, or modifies files in the source directory. Operators manage file lifecycle (manual archive, `rm`, etc.).
- **No symlink-following inside the directory.** The top-level `path` may itself be a symlink to a directory (common for Dropbox / Drive mirror dirs), but symlinked files inside the directory are read like regular files (no recursion through them).

### Workflow example

A planning workflow that emits decisions:

1. Operator runs a Superpower brainstorm session that outputs to `~/.bicameral/captured-notes/2026-05-14-auth-design.md`.
2. Operator runs `bicameral-mcp sync-and-brief`.
3. The adapter sees the new file, emits an ingest payload with `source_type: "planning"` (or operator-set label), the full file content as `span.text`, and the file path as `source_ref`.
4. The decision lands in the ledger.
5. Watermark advances to the file's mtime; future runs skip it unless edited.

In-place editing the file advances its mtime → next run re-ingests it. To avoid re-ingestion, `cp` to a new filename rather than editing in place.

### What this does NOT do

- No watch-mode / daemon. Operators run the CLI on demand.
- No content-type-aware parsing. A markdown file becomes one ingest payload with the full content as `span.text`; bicameral doesn't try to segment by H1, parse frontmatter, or detect speakers.
- No remote source support. For meeting transcripts pulled from a SaaS API, see `granola` and future adapters.

## Adding a new adapter

To add a source adapter (Drive, Slack, local-folder, etc.):

1. Create `events/sources/<name>.py` implementing the `SourceAdapter` protocol from `events/sources/__init__.py`.
2. Register it in `events/sources/__init__.py::ADAPTERS`.
3. Add unit tests in `tests/test_sources_<name>_unit.py` following the pattern in `tests/test_sources_granola_unit.py`.
4. Update this doc with the new `type` and its required/optional config keys.

## Future-source roadmap

Per the #279 issue scope:

- **Granola** (this phase) — shipped.
- **Drive folder reader** — P2 follow-up. Read meeting transcripts from a Google Drive folder.
- **Slack pull** — P2 follow-up. Pull from a Slack channel (not a webhook).
- **Local meeting-notes paths** — shipped via `local_directory` adapter (#344). Watches a configured local directory for new files; emits one ingest payload per file.
- **Calendar invites, email webhooks** — explicitly deferred per #279 ("Push-only sources are deferred").

## Team backend (#279 Phase 2)

`bicameral-mcp sync-and-brief` can optionally sync the shared per-author event log via a `BackendAdapter` configured under the `team:` top-level key.

When configured:
1. **Before source pull**: `backend.pull_events()` copies every peer's `<email>.jsonl` into the local `.bicameral/events/` cache. The materializer picks them up alongside the operator's own events.
2. **After source ingest succeeds**: `backend.push_events()` uploads each local `<email>.jsonl` to the shared backend. The backend's sha-match skip keeps the second invocation a noop until the file content changes.

Failures during pull/push are logged to stderr + `~/.bicameral/cli-errors.log` but do NOT block the brief — sync-and-brief continues with the local-only path. The hook wrapper's `exit 0` framing makes this completely invisible to SessionStart users on a network outage.

### Config shape

```yaml
team:
  backend: local_folder       # or: google_drive
  author: alice@example.com   # required; the operator's email
  remote_root: /shared/events # local_folder only
  # folder_id: 1abc...        # google_drive only
```

If `team.backend` is set but `team.author` is empty or missing, the CLI logs a warning and skips team sync — preventing the partial-config case where the adapter can't determine which file belongs to the operator.

### Failure modes

| Scenario | Behavior |
|---|---|
| `team:` absent from config | Solo mode. No backend constructed. |
| `team.backend` set, `team.author` empty | Warning to stderr; team sync skipped; CLI continues local-only. |
| Backend `pull_events` raises | Logged; continues with current local events_dir state. |
| Backend `push_events` raises for one file | Logged; other files still pushed. |
| Source ingest raises | Watermark NOT advanced (Phase 1 invariant); push still runs for unrelated files. |

### Adapter implementations

- **`local_folder`** — shared filesystem path (NFS, Dropbox, syncthing, etc.). Useful as an integration-test backend and as a fallback for orgs that already have a synced folder. Sha-match skip on upload.
- **`google_drive`** — Google Drive folder. Requires OAuth credentials per the standard `google-auth` flow.

To add a new backend, see `events/backends/__init__.py` for the `BackendAdapter` ABC.
