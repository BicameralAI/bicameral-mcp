# Integrations — Settings Inventory

**Living document.** Updated as each integration phase ships. Treats
"settings" as the full operator-facing capability surface, not just
on/off + auth credentials.

Per the project directive (memory entry
`feedback_integrations_settings_inventory`): "integration config is
NOT as simple as on/off and login permissions." Each integration's
inventory captures:

- **Auth** — credentials, scopes, storage location
- **Discovery** — how the operator enumerates available resources
  once authenticated
- **Selection** — which specific resources are watched
- **Filtering** — declarative criteria per resource (keywords,
  reactions, labels, time windows, user lists)
- **Content evaluation hook** — pluggable script for filtering
  beyond declarative criteria
- **Active vs passive toggle** — per source AND per resource
- **Rate limit / quota** — operator overrides for default caps

A field is **SHIPPED** when it's plumbed end-to-end (config →
runtime → tests). Other states: **PARTIAL** (config present but
runtime ignores it, or vice versa), **TBD** (planned, not started),
**N/A** (not applicable to this source).

The future UI settings surface (parent tracker #337, parallel track)
consumes this inventory as its spec.

---

## Cross-cutting requirements

| Requirement | State | Notes |
|---|---|---|
| Per-source secrets storage | **SHIPPED** | `secrets_store` (OS keyring + `BICAMERAL_KEYRING_DISABLE` dict fallback). Source 0a/0b in #418/#419. |
| Per-source DLQ | **SHIPPED** | `~/.bicameral/dlq/<source_id>.jsonl` + mode-0600 raw sidecar. Phase 0a #422. |
| Per-source audit events | **SHIPPED** | `SOURCE_INGEST_ATTEMPT` / `_ACCEPTED` / `_REFUSED` / `_AUTH_GRANTED` / `_REVOKED`. Phase 0b #423. |
| `ingest_mode="passive"` | **SHIPPED** | Soft-gate WARN+DLQ+continue. Phase 0a. |
| Per-source rate limits | **PARTIAL** | Process-wide token bucket exists (`context.py:ingest_rate_limit_*`). NOT per-source. **TBD**: per-source override. |
| Per-source max-payload-bytes | **PARTIAL** | Global `ingest_max_bytes`. Per-source override **TBD**. |
| Content-evaluation hook | **TBD** | Pluggable callable for declarative-filter escape hatch. No source supports it yet. Design open. |
| Discovery primitive | **TBD** | `bicameral-mcp source-list <source>` CLI to enumerate available resources for the operator. No source supports it yet. Foundational gap. |
| Active-vs-passive per resource | **TBD** | Currently source-level only (the *type* is the toggle). Per-resource toggle (e.g. "channel X active-only, channel Y polling") not designed yet. |
| Webhook receivers | **TBD** | Push model alternative to polling. Needs public HTTP surface. Tracker design pending. |

---

## Source inventories

### 1. `granola` — meeting-transcript pull (legacy, pre-#337)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | `api_key_env` config entry → `os.environ[<name>]` (pre-`secrets_store` pattern; migration **TBD**) |
| Discovery | **N/A** | API exposes all transcripts the key can see; no enumeration step |
| Selection | **N/A** | No per-transcript opt-in; all incoming transcripts ingest |
| Filtering | **TBD** | No keyword/participant/duration filter |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Passive-only | No active URL-based path; legacy |
| Rate / quota | **PARTIAL** | Inherits global ingest gates |

### 2. `local_directory` — file drop watching (#344)

| Field | State | Detail |
|---|---|---|
| Auth | **N/A** | Filesystem |
| Discovery | **TBD** | No `list-directories` enumerator; operator must already know the path |
| Selection | **SHIPPED** | `path:` config entry — one directory per source-config entry |
| Filtering | **SHIPPED** | `extensions: [...]` whitelist (`.md/.txt/.json` default), `max_file_bytes` cap |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Passive-only | — |
| Rate / quota | **SHIPPED** | `max_file_bytes` per file |
| Operator label override | **SHIPPED** | `source_type_label` |

### 3. `google_drive` — Docs ingest (Phases 5a / 5b / 5c)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | OAuth bot-app flow via `bicameral-mcp source-auth google_drive` → `secrets_store`. Scope: `documents.readonly` + `drive.metadata.readonly` |
| Auth refresh | **SHIPPED** | Automatic via `load_credentials()` refresh_token path |
| Discovery — folders | **TBD** | No CLI to list folders the operator has access to. Operator must paste folder ID from Drive URL bar. **Critical UX gap.** |
| Discovery — docs in folder | **SHIPPED** (runtime) | Adapter enumerates via `files.list` filtered to Docs MIME, modifiedTime > watermark |
| Selection — active | URL paste | Operator pastes Docs URL, adapter fetches via Docs API |
| Selection — passive | **SHIPPED** | `folder_id:` config entry, one folder per source-config entry |
| Filtering | **PARTIAL** | MIME-type filter is hard-coded (Docs only). No keyword/title/author filter |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Both | Active = URL paste, Passive = folder_id polling |
| Rate / quota | **PARTIAL** | Pagination capped at 2000 docs/cycle. No operator override |
| Sub-folder recursion | **TBD** (deliberately) | Phase 5c documented as non-recursive |

### 4. `linear` — issue ingest (Phases 1a / 1b)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | Personal API key (`lin_...`) or OAuth token via `secrets_store` |
| Discovery — teams | **TBD** | No CLI to list teams the API key can see |
| Discovery — projects | **TBD** | — |
| Selection — active | URL paste | Operator pastes Linear issue URL |
| Selection — passive | **PARTIAL** | `team_keys: [...]` filter optional. No project-level filter. No "watch all teams" explicit setting (omitting filter implies it) |
| Filtering — state | **SHIPPED** | Hard-coded to `completedAt` > watermark (state="Done" or workflow-state-completed) |
| Filtering — labels | **TBD** | No label-include / label-exclude |
| Filtering — assignees | **TBD** | — |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Both | URL active + polling passive |
| Webhook receiver | **TBD** | Phase 1c |
| Rate / quota | **PARTIAL** | Pagination capped at 500 issues/cycle |

### 5. `notion` — page ingest (Phases 2a / 2b)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | Internal-integration token via `secrets_store` |
| Discovery — databases | **TBD** | No CLI to list databases shared with the integration |
| Discovery — pages | **TBD** | Page-level enumeration would be `databases/{id}/query` — adapter does this at runtime, no operator-facing enumerator |
| Selection — active | URL paste | Operator pastes Notion page URL |
| Selection — passive | **SHIPPED** | `database_id:` config entry, one database per source-config entry |
| Filtering — page properties | **TBD** | No tag/property-value filter (e.g. only pages with `decision-source` tag) |
| Filtering — last_edited_by | **TBD** | — |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Both | URL active + database polling passive |
| Webhook receiver | **N/A** | Notion has no webhook product. Polling is the only option. |
| Sub-page recursion | **TBD** | Currently single page (active) or top-level pages in database (passive) |
| Rate / quota | **PARTIAL** | Pagination capped at 2000 pages/cycle |

### 6. `github` — PR / issue / commit ingest (Phases 3 / 3b)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | PAT (`repo` scope) OR GitHub App install token via `secrets_store` |
| Discovery — repos | **TBD** | No CLI to list repos the token has access to (operator can use `gh repo list` outside Bicameral) |
| Selection — active | URL paste | PR / issue / commit URL |
| Selection — passive | **SHIPPED** | `repos: ["owner/repo", ...]` config entry. Multiple repos per source-config entry |
| Filtering — PR state | **SHIPPED** | Hard-coded: state=closed + merged_at not null |
| Filtering — labels | **TBD** | No label include/exclude |
| Filtering — paths | **TBD** | No file-path-touched filter |
| Filtering — authors | **TBD** | — |
| Content eval hook | **TBD** | — |
| Webhook receiver | **TBD** | Phase 3c. GitHub has a mature webhook product (Events API) — strong candidate for first webhook implementation. |
| Active/passive toggle | Both | URL active + repos polling passive |
| Rate / quota | **PARTIAL** | Pagination capped at 1000 records/cycle. Per-source rate-limit window **TBD** |

### 7. `slack` — channel / thread ingest (Phases 4a / 4b)

| Field | State | Detail |
|---|---|---|
| Auth | **SHIPPED** | Bot token (`xoxb-...`) via `secrets_store`. Required scopes: `channels:history`, `groups:history`, `users:read` (latter implicit for `users.info`) |
| Discovery — channels | **TBD** | **Critical gap** per user feedback. Bot token enables `conversations.list` but no CLI / UI exposes it yet. Operator must look up channel IDs in Slack's UI |
| Discovery — users | **PARTIAL** | `users.info` called at runtime for participant resolution; no explicit operator-facing user enumeration |
| Selection — active | URL paste | Operator pastes thread URL |
| Selection — passive | **PARTIAL** | `channels: [...]` config entry with hand-typed channel IDs. No UI for select-from-discovered-list |
| Filtering — keywords | **TBD** | No "only ingest messages containing X" filter |
| Filtering — reactions | **TBD** | Originally proposed (`:decision:` emoji as opt-in marker per #337 v1 sketch). Not implemented |
| Filtering — user include/exclude | **TBD** | — |
| Filtering — message subtypes | **SHIPPED** | Hard-coded exclusion list (bot_message, channel_topic/join/leave, etc.) |
| Filtering — reply skipping | **SHIPPED** (passive only) | Polling skips replies; active fetches full thread |
| Content eval hook | **TBD** | — |
| Active/passive toggle | Both | URL active + channel polling passive |
| Webhook receiver | **TBD** | Phase 4c. Slack Events API. Bigger commit (public HTTP surface) |
| DM policy | **SHIPPED** | Channel-only enforced at URL parser (active) + ID-prefix filter (passive) |
| Rate / quota | **PARTIAL** | Pagination capped at 2000 messages/cycle |

---

## Patterns + gaps

### Patterns shipped

- **Two-track auth**: OS keyring via `secrets_store` for first-party flows; legacy `api_key_env` env-var indirection for Granola (predates `secrets_store`).
- **Two-mode ingest** per source: active (URL paste) + passive (poller). Single token serves both.
- **Per-resource watermarks**: GitHub (per repo), Slack (per channel) — others use single-cursor.
- **Failure isolation**: per-item fetch failures skipped, watermark still advances. Per-resource listing failures skipped, watermark unchanged.

### Most-requested gaps (rolling up "TBD" frequency)

1. **Discovery CLI primitive** — `bicameral-mcp source-list <source>` to enumerate channels/teams/repos/folders/databases. Currently the operator must look up IDs outside Bicameral. *Affects: every source.*
2. **Resource-level filters** — keyword, label, reaction, author, path filters per watched resource. Currently the only filter dimension that ships is "which resource(s)." *Affects: every source.*
3. **Content evaluation hook** — pluggable callable for ops that need filtering beyond declarative criteria. Design open. *Affects: every source.*
4. **Per-source rate / quota overrides** — global ingest gates apply uniformly; no per-source budget. *Affects: every source.*
5. **Webhook receivers** — push model alternative to polling. Mature on GitHub/Slack/Linear; absent from Notion. *Affects: 3 of 7 sources.*
6. **Per-resource active/passive toggle** — currently the source TYPE is the toggle. Operator can't say "channel X active-only, channel Y polling." *Affects: every source.*

### Sequencing recommendation

Foundational work that unblocks multiple sources should land before any one webhook receiver:

- **`source-list` CLI** (one cycle, hits all sources) — unlocks "operator picks from discovered list" UI flow.
- **Per-resource filter schema + runtime hook** (one cycle, generic across sources) — unlocks every "TBD: filter" cell above.

After the foundations, webhook receivers go one source at a time:

- **GitHub webhooks** first (best-documented receiver, lowest auth complexity once the public surface exists).
- **Slack Events API** second (well-known but signature verification + URL verification handshake adds steps).
- **Linear webhooks** third (smaller payload surface; same shape).
- Notion stays polling-only (no webhook product).
