# Research Brief — Team-server tier v1: existing compatible components

**Date**: 2026-05-14
**Analyst**: The Qor-logic Analyst
**Target**: `BicameralAI/bicameral-mcp` repo, all components that are currently team-mode-compatible or plausibly extend to a future "team-server tier v1"
**Scope**: pre-design survey ahead of the team-server runtime reactivation cycle queued by the operator on 2026-05-14. **No implementation in this brief — fact-finding only.**

---

## Executive summary

A v0-conformant team-mode substrate ships on `dev` HEAD today. The
event-log substrate (`events/writer.py`, `events/materializer.py`,
`events/team_adapter.py`) plus the BackendAdapter ABC
(`events/backends/__init__.py` with LocalFolder + GoogleDrive
implementations from #279 Phase 2) cover **the wire format, the
per-author file isolation, the replay-determinism contract, the
dual-write atomic semantic, and the CLI integration**
(`cli/sync_and_brief_cli.py`). #242 permanently removed the
self-hosted HTTP runtime; no replacement exists. The gaps for a
team-server tier v1 are **above the substrate**, not in it: auth
(#215 Track 2), HTTP transport surface, per-peer rate-limit / health
metrics, multi-author conflict resolution beyond canonical_id
first-write-wins, and an MCP-tool surface for team membership /
audit.

**Single most important finding**: the BackendAdapter contract +
canonical_id dedup + per-author JSONL isolation is **sufficient as
the wire substrate**. A team-server tier v1 should be additive on
top, not a replacement for it. Designing the tier as a wrapper that
sits between the MCP transport and the BackendAdapter avoids
re-litigating #242.

---

## Findings

### 1. Event-log substrate (the v0 wire format)

| Component | File:line | Verified behavior |
|---|---|---|
| `EventEnvelope` schema | `events/writer.py:72-79` | `schema_version: int`, `event_type: str`, `author: str`, `timestamp: datetime`, `payload: dict`. Per-author JSONL at `.bicameral/events/{email}.jsonl` (writer.py:132). |
| Atomic multi-byte writes | `events/writer.py:28-69` | Cross-platform advisory flock (POSIX `fcntl` / Windows `msvcrt`). Locks per-file; safe across concurrent processes on the same user account. |
| Signer-email redaction | `events/writer.py:100-123` | `_resolve_signer_email()` honors `signer_email_fallback` config (`redact|local-part-only|full`); applied at envelope-write time, not later. |
| Materializer projection | `events/materializer.py:1-203` | Byte-offset watermarks at `.bicameral/local/watermark` (line 24); legacy `{email}/*.json` → `.jsonl` migration on first start (lines 38-62); shrink detection resets to byte 0 (lines 77-78). |
| Cross-author event types | `events/materializer.py:89-195` | `ingest.completed`, `link_commit.completed`, `decision_ratified.completed`, `decision_superseded.completed`, `compliance_check.completed`. |
| Transcript queue | `events/transcript_queue.py:18, 30-66` | Pending FIFO at `.bicameral/pending-transcripts/{session_id}.jsonl`; drained by preflight Step 3.5; archived to `.bicameral/processed-transcripts/` post-correction. |
| Dual-write adapter | `events/team_adapter.py:20-289` | `TeamWriteAdapter` composes any inner ledger adapter; event-write first, DB-write second; dirty-flag batching defers `backend.push_events()` to post-handler `flush_to_backend()` (lines 51-61). |

**Verified against blueprint** (`docs/v0-architecture-current.md`): all of the above match the doc's v0 contract; no drift.

### 2. BackendAdapter foundation (#279 Phase 2 substrate)

| Surface | File:line | Verified behavior |
|---|---|---|
| ABC contract | `events/backends/__init__.py:20-50` | Four methods: `push_events(local_path, remote_name)`, `pull_events(local_dir, since_token) → str`, `lock(remote_name) → AsyncContextManager`, `list_peers() → AsyncIterator[str]`. |
| Factory | `events/backends/__init__.py:53-71` | `get_backend(config) → BackendAdapter | None`; reads `team.backend`, `team.author`, backend-specific keys. |
| LocalFolderAdapter | `events/backends/local_folder.py:1-75` | SHA256 hash-skip on both push and pull (lines 42-46, 48-58); copies every peer's file except caller's own. In-process `asyncio.Lock` per `remote_name` (lines 60-70). |
| GoogleDriveAdapter | `events/backends/google_drive.py:1-80+` | OAuth (RFC 8252 installed-app) with bundled non-secret credentials (lines 41-49); scope `drive.file` only (line 37); token cache at `~/.bicameral/google-drive-token.json` mode 0600 (line 38); MD5 etag matching for skip (lines 69-78). |

**Verified against blueprint** (`docs/policies/sources-config.md` § "Team backend"): config shape matches; failure-mode table matches; adapter-implementation roster matches. No drift.

### 3. Multi-author / multi-peer mechanics

| Mechanic | File:line | Verified behavior |
|---|---|---|
| Author identity | `events/writer.py:82-97` | `_get_git_email()` resolves from `git config user.email`; per-developer file ownership flows from this. |
| Canonical_id dedup | `ledger/schema.py:137,165`; `events/materializer.py:99-195` | UUIDv5 from `(description, source_type, source_ref)` is a DB-level UNIQUE index. First-write-wins. Cross-author replay idempotent. |
| Content-address region keys | `events/materializer.py:104-107, 171-177` | `find_decision_by_canonical_id()` resolves peer-event-side canonical IDs to local row IDs; compliance checks use `(repo, file_path, symbol_name, content_hash)` instead of line numbers. |
| Watermark advancement | `events/materializer.py:196-202` | Per-author byte-offset watermarks; advanced only on successful replay. Legacy timestamp watermarks (≤v0.4.19) detected and discarded — DB-level canonical_id dedup covers re-replay. |
| `team:` config block | `docs/policies/sources-config.md:62-99` | `backend` (`local_folder|google_drive`), `author` (required), `remote_root` or `folder_id` per backend. Missing `team.author` → stderr warning + skip. |
| Order of operations | `cli/sync_and_brief_cli.py:68-101` | Pull peer events → pull sources → ingest → push local author file → synthesize brief. Backend resolution in `get_backend(cfg)` (lines 104-121). |

### 4. Identity & rate-limit isolation hooks

| Hook | File:line | Verified behavior |
|---|---|---|
| `_resolve_agent_identity` | `context.py:100-147` | 16-char hex of SHA256(salt + git-email); per-install salt at `~/.bicameral/salt`; stable per-developer across server restarts; fallback to process-wide `_SESSION_ID` UUID on git/salt failure. |
| Salt creation | `preflight_telemetry._get_or_create_salt` (cited by `context.py:116-124`) | Race-safe via `os.O_EXCL`. Documented side-effect: first call from any subsystem materializes the salt file. |
| Per-session token bucket | `handlers/ingest.py:39-49`; `context.py:39-49` | Default 10-token burst, 1 token/sec refill. Per-session-id key. Aggregate enforcement (sliding-window cross-session) **deferred to team-server activation** per `handlers/ingest.py:6-17`. |
| `BICAMERAL_INGEST_RATE_LIMIT_DISABLE` | `handlers/ingest.py:368` (referenced) | Env override mirroring the #224 `BICAMERAL_QUERY_TIMEOUT_DISABLE` precedent. |

### 5. CLI surfaces that touch team mode

| CLI | File:line | Verified behavior |
|---|---|---|
| `sync-and-brief` | `cli/sync_and_brief_cli.py:68-101` | Entry point; orchestrates pull → ingest → push → brief synthesis. Hook wrapper exits 0 even on sync failure (lines 43-56). |
| `_resolve_team_backend` | `cli/sync_and_brief_cli.py:104-121` | Returns `None` for solo mode; warns + returns None if `team.backend` set but `team.author` empty. |
| `_team_sync_pull` / `_team_sync_push` | `cli/sync_and_brief_cli.py` (per `tests/test_sync_and_brief_team_mode.py:97-175`) | Failure-isolated wrappers; backend errors logged to stderr but never propagate to the CLI exit code. |
| Ledger export/import | `cli/ledger_export.py`, `cli/ledger_import.py` (per `git log` 2026-05-13) | JSONL transport pair from #252 Layer 4; useful for offline catch-up / disaster recovery. Not yet wired into team-mode flow but compatible with the JSONL substrate. |

### 6. Negative space — #242 removals (confirmed absent)

The Explore-agent survey confirmed via Glob + Grep:

- `team_server/` directory is empty (only `__pycache__` artifacts remain).
- No `events/team_server_bridge.py`, `events/team_server_consumer.py`, `events/team_server_pull.py`.
- No `deploy/Dockerfile.team-server`, `deploy/team-server.docker-compose.yml`.
- No `tests/test_team_server_*.py`.
- Grep for `team_server_bridge|team_server_consumer|team_server_pull` returns zero results across the main tree (excluding cache).
- No HTTP server framework imports (no FastAPI / Flask / Starlette) in the main codebase.

**Hygiene note**: `.mypy_cache/3.11/events/team_server_bridge.*` and `events/__pycache__/team_server_bridge.cpython-313.pyc` artifacts still exist but are inert. **Cosmetic only** — no functional blocker. A future `chore(cleanup)` pass could nuke them.

### 7. Anchor docs (sections / headers only)

| Doc | Relevant sections |
|---|---|
| `docs/team-mode-setup.md` | Backends; Create vs Join; OAuth (what happens / what we see); Drive scope; Trust dependency; Setup flows; Verifying replication; Permissions/revocation; Privacy posture; Local-folder backend; Troubleshooting |
| `docs/policies/sources-config.md` | Shape; API key handling; Watermarks; Adding a new adapter; Future-source roadmap; Team backend (#279 Phase 2): Config shape, Failure modes, Adapter implementations |
| `docs/policies/threat-model-and-trust-boundary.md` (just merged in #324) | Scope statement; In/out-of-scope deployments; MCP stdio surface; Team-mode posture (v0, post-#242); Track 2 deferral |
| `docs/policies/host-trust-model.md` | Server-side guarantees; Externalized assumptions; Cross-ref to threat-model doc |
| `docs/v0-architecture-current.md` | Knowledge graph schema; Status state machine; Protocol flows; MCP server architecture; Event-sourced ledger; Reconciliation notes |

### 8. Open issues referenced in code comments / docstrings

| Issue | File:line | Context |
|---|---|---|
| #279 Phase 1 | `cli/sync_and_brief_cli.py:1` | Pull-based session-magic CLI entry-point doc |
| #279 Phase 2 | `cli/sync_and_brief_cli.py:68, 88` | Team-backend pull before source / push after ingest |
| #279 Phase 2 | `cli/brief_renderer.py` | Team-sync section in brief output |
| #296 | `audit_log.py` comment | Recoverable schema-skip / init-deferred path |
| #296 | `handlers/reset.py` | `--replay-from-events` flag |
| #296 | `ledger/schema.py` v17 migration | Yields-edge integrity cleanup |
| #231 Phase 1 | `context.py:100-147` | Email-derived agent identity |
| #231 Phase 2 | `handlers/ingest.py` (rate-limit registry) | Per-developer bucket isolation |
| #215 Track 1 | `docs/policies/threat-model-and-trust-boundary.md:1-9` | Trust-boundary scope statement (Track 2 deferred) |
| #242 | Git commit `ab2d45b` | Removal of self-hosted server runtime |

### 9. Gaps & blank spots (what tier v1 will need)

| Gap | Evidence of absence |
|---|---|
| **HTTP server endpoint surface** | No FastAPI / Flask / Starlette imports; `team_server/` directory empty; #242 explicitly removed the previous shape. |
| **Auth shim (#215 Track 2)** | `docs/policies/threat-model-and-trust-boundary.md:7-9, 31-32` deferral; no `authn|authz|bearer|jwt|oauth` imports in the MCP-transport handler layer. |
| **Multi-author write coordination** | Per-author file separation + canonical_id UNIQUE is the entire coordination story. No leases, no quorum, no CRDTs beyond git's per-line mergeability. |
| **Backend health / liveness probes** | `BackendAdapter` ABC at `events/backends/__init__.py:20-50` has no `health()` / `ping()` / `status()` method. |
| **Conflict resolution** | Canonical_id dedup is first-write-wins (`events/materializer.py:108-113`). No merge strategy; fail-soft skip is the resolution. |
| **Per-peer bandwidth metering** | Pull/push ops are fire-and-forget; no quota, rate-limit, retry budget per peer. |
| **Per-backend observability** | LocalFolder / GoogleDrive have no metrics hooks; only stderr / `cli-errors.log` logging. |
| **Team-governance MCP tools** | No tools for "who is in the team", "kick a peer", "audit who wrote what". Decision-level governance (`#231` rate-limit) exists; team-level coordination is missing. |
| **Source-pull dedup across peers** | If multiple peers pull from the same Granola / Drive account, redundant API calls + duplicated ingest. No leader-election. |

---

## Blueprint alignment check

| Blueprint claim | Actual finding | Status |
|---|---|---|
| Team-mode uses pull-based event-log adapters (per #242 v0 commitment) | LocalFolderAdapter + GoogleDriveAdapter implement the ABC; `cli/sync_and_brief_cli.py` orchestrates pull → ingest → push | **MATCH** |
| Per-author JSONL is the wire substrate (`docs/v0-architecture-current.md`) | `events/writer.py:132` writes `.bicameral/events/{email}.jsonl`; materializer reads same | **MATCH** |
| First-write-wins via content-addressed keys (`docs/v0-architecture-current.md:40`) | Canonical_id UNIQUE index at `ledger/schema.py:137,165`; materializer dedup at `events/materializer.py:99-195` | **MATCH** |
| MCP transport boundary is OS user account; team-mode does not elevate it (`docs/policies/threat-model-and-trust-boundary.md`) | No auth on MCP stdio; team-mode is filesystem-ACL bound on the shared backend | **MATCH** |
| Old self-hosted runtime is permanently removed (#242) | Confirmed: `team_server/` empty; no `team_server_*` imports; no HTTP framework imports | **MATCH** |
| Per-developer rate-limit isolation via agent-identity hash (#231) | `context.py:100-147` ships the resolver; `handlers/ingest.py` registry keyed by session_id | **MATCH** |
| Replay determinism for team-mode (#296) | Canonical_id + content-hash region keys make cross-author replay deterministic | **MATCH** |
| Auth shim ships in Track 2 of #215, gated on team-mode evolution | Track 1 doc landed 2026-05-14 (#324); Track 2 plan does not exist yet | **MATCH (deferred-by-design)** |

**No drift detected.** The architecture-as-coded matches the architecture-as-documented at the v0 boundary.

---

## Recommendations

In order of dependency. The first three are unblockable without a design decision; the rest follow.

### R1. **Define the tier-v1 boundary line.** (1 cycle, `/qor-ideate` or `/qor-plan`)

The single most important design question is: **where does the tier-v1 transport surface live?** Three plausible answers:

1. **Inside the MCP server.** A new "team-mode-aware" handler that uses `BackendAdapter` plus an auth check. Cleanest fit with existing code; bounds the surface area; constrains the auth shim's API to the MCP envelope shape (`#215` Track 2's first design option).
2. **Beside the MCP server**, as a separate "broker" process per developer that the MCP server talks to over a local IPC channel. Reintroduces a daemon (which #242 warned about) but isolates team-mode auth from the MCP transport. Probably wrong shape.
3. **Above the BackendAdapter**, as a new BackendAdapter subclass that speaks to a hosted bicameral-team-server over HTTP. Cleanest separation but requires a new HTTP server runtime (which #242 *also* warned against — the warning was about *self-hosted Slack/Notion OAuth workers*, not about all HTTP servers).

Option 1 is most consistent with the v0 doctrine. Option 3 is needed if hosted multi-team deployments become a goal (Stage 2 of the business model per `visual-plans/bicameral-business-model.html`).

**Recommendation**: Run `/qor-ideate` to pick between Option 1 and Option 3 before any implementation work begins. Option 2 is excluded.

### R2. **Track 2 of #215 — design the auth shim.** (1 plan cycle, no implementation)

Whatever the tier-v1 boundary line, the auth shim is what elevates the trust boundary from "OS user account" to "per-developer authenticated principal." Three design options were enumerated in `docs/policies/threat-model-and-trust-boundary.md`:

- Per-developer JWT signing keys carried in the MCP envelope.
- mTLS over a stdio-tunneling transport for hosted deployments.
- Operator-side OS-keychain-backed credentials with a server-side verification handshake.

**Recommendation**: Track 2 is the next `/qor-auto-dev-1` cycle's plan + audit phase. No code in that cycle — design only.

### R3. **Decide the multi-author conflict-resolution semantic.** (1 plan cycle)

First-write-wins via canonical_id is correct for *idempotency* (re-replaying the same event is a noop) but punts on *divergence* (two peers write the "same" decision with different rationales). The current behavior is fail-soft skip. Tier v1 needs to choose:

- Accept lossy skip (current) — risk: silent loss of conflicting peer intent.
- Surface conflicts to the human via a new MCP tool — preserves info but adds UX surface.
- Merge via a CRDT-shaped rule (lexicographic peer-ID, latest-wins, etc.) — surfaces nothing but biases the resolution.

**Recommendation**: Pair this with R1; the conflict semantic depends on the transport surface.

### R4. **Add `BackendAdapter.health()` / `BackendAdapter.list_peers()` improvements.** (1 small cycle)

Operational observability gap. `list_peers()` exists but is wired only through `pull_events()`; a probe-only path (no side-effects) would let the SessionStart hook show "team backend reachable: yes (3 peers)" at session start (similar to the #224 timeout-posture hook).

**Recommendation**: Defer to after R1, since the shape depends on whether the team-server tier intercepts these calls.

### R5. **Clean up #242 cache artifacts.** (chore, 1 commit)

Inert `__pycache__` / `.mypy_cache` entries for the removed `team_server_*` modules. Cosmetic; bundle into the next infrastructure PR.

### R6. **Defer source-pull leader-election + per-peer quotas until activation drives the need.** (no cycle until evidence)

These are real gaps but unmeasured. Adding leader-election before evidence shows redundant Granola/Drive pulls would be YAGNI. Track the gap; revisit when an operator reports it.

---

## Updated knowledge

The following should be added or reinforced in the repo's knowledge base:

1. **Substrate vs tier**: `docs/v0-architecture-current.md` documents the substrate (event log + materializer + BackendAdapter) very well, but it stops at v0. A future `docs/v1-team-server-tier.md` should sit alongside it once R1 picks a design. **For now**: do *not* write that doc; this brief is the placeholder.

2. **Negative space is intentional, not omission**: the absence of HTTP transport, auth shim, conflict-resolver, etc. is *by design* per #242. Future contributors who see "no HTTP server" and assume it was lost should be redirected to this brief + `docs/policies/threat-model-and-trust-boundary.md`.

3. **The BackendAdapter is the right substrate abstraction.** No drift between the ABC and its two implementations. A third (e.g., S3, Slack-channel-as-event-log) drops in without touching consumers.

4. **The decision to put per-author files on a shared backend is what makes the substrate trust-portable.** Each operator authenticates to the backend with their own identity; the MCP server does not need to know about peer identities at all. Anything that would make the MCP server *need* to authenticate peers is a tier-v1 concern, not a substrate concern.

5. **`canonical_id` is the keystone.** Any tier-v1 design that breaks the `(description, source_type, source_ref)` → UUIDv5 derivation breaks the cross-author replay determinism guarantee. Track it as a substrate invariant.

---

## Refs

- Brief: this file
- Substrate doc: [`docs/v0-architecture-current.md`](v0-architecture-current.md)
- Trust boundary: [`docs/policies/threat-model-and-trust-boundary.md`](policies/threat-model-and-trust-boundary.md)
- Team-mode setup: [`docs/team-mode-setup.md`](team-mode-setup.md)
- Backend config: [`docs/policies/sources-config.md`](policies/sources-config.md)
- Issues: [#196](https://github.com/BicameralAI/bicameral-mcp/issues/196), [#215](https://github.com/BicameralAI/bicameral-mcp/issues/215), [#231](https://github.com/BicameralAI/bicameral-mcp/issues/231), [#242](https://github.com/BicameralAI/bicameral-mcp/issues/242), [#279](https://github.com/BicameralAI/bicameral-mcp/issues/279), [#296](https://github.com/BicameralAI/bicameral-mcp/issues/296), [#324](https://github.com/BicameralAI/bicameral-mcp/pull/324) (Track 1 merged)

---

_Research complete. Findings are advisory — implementation decisions remain with the Governor._
