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
- **Local meeting-notes paths** — P2 follow-up. Watch a local directory for new transcript files.
- **Calendar invites, email webhooks** — explicitly deferred per #279 ("Push-only sources are deferred").
