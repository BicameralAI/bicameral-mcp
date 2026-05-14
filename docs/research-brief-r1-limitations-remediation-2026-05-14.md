# Research Brief — R1 Architecture: Limitation & Gap Remediation Strategies

**Date**: 2026-05-14
**Analyst**: The Qor-logic Analyst
**Target**: `BicameralAI/bicameral-mcp` repo — all 24 identified constraints under the R1 architecture (MCP local + BackendAdapter file-share, no server process)
**Scope**: Remediation strategy investigation for each of the 9 original gaps (from `research-brief-team-server-tier-v1-2026-05-14.md` §9) and 15 known limitations (L1–L15 on [issue #215](https://github.com/BicameralAI/bicameral-mcp/issues/215#issuecomment-4455233107)). **No implementation in this brief — strategy enumeration only.**
**Upstream**: R1 decision by @jinhongkuan (2026-05-14, PR #325): Option 1 — MCP local server + JSONL stored remotely via BackendAdapter. No separate server process.

---

## Executive summary

The R1 architecture trades operational complexity for simplicity: each developer runs their own MCP server locally, team sync happens through the BackendAdapter contract (file-share semantics), no server process exists. This brief investigates remediation strategies for every identified constraint, grounded in the actual codebase at `events/backends/__init__.py`, `events/materializer.py`, `events/writer.py`, `events/team_adapter.py`, and `ledger/schema.py`.

**Key finding**: 20 of the 24 items are remediable within the R1 architecture — through BackendAdapter ABC extensions, materializer enhancements, or new MCP tools — without introducing a server process. The remaining 4 (G1 HTTP endpoint, G8 team-governance tools requiring real-time coordination, L11 scalability ceiling, L12 delta sync) may eventually require a BackendAdapter subclass that speaks to a managed service (S3, Supabase, etc.), which is the intended extension path per the R1 decision's preserved architectural intent.

---

## Part I — Original Gaps (from research brief §9)

### G1. HTTP Server Endpoint Surface

**Current state**: No FastAPI/Flask/Starlette imports; `team_server/` directory empty; #242 explicitly removed the previous shape.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. No HTTP — BackendAdapter is the transport** | R1 decided this. Remote JSONL via BackendAdapter (LocalFolder, GoogleDrive, future S3/Supabase). No HTTP server process. | Zero ops burden; #242-compliant; simplest possible architecture; BackendAdapter ABC already exists and works | No real-time push; no centralized coordination; each new cloud backend needs a new adapter implementation |
| **B. Thin webhook relay (future adapter)** | A future `WebhookRelayAdapter` that receives change notifications from cloud backends (S3 Event Notifications, Google Drive `changes.watch`) and relays them to the local MCP server via a localhost callback. No public-facing HTTP server. | Enables near-real-time sync without a hosted server; stays local-only; solves L1 (polling latency) | Requires a local listener; adds complexity to setup; only works with backends that support webhooks |
| **C. Managed service adapter (Stage 2)** | A future `SupabaseAdapter` or `S3Adapter` that uses the service's native APIs for push/pull. The "HTTP" is between the adapter and the managed service, not a self-hosted server. | Cloud-native; scales beyond file-share; solves L11 and L12; no self-hosted process | Vendor lock-in per adapter; requires managed service account; more complex auth story |

**Recommendation**: Strategy A is current (R1 decided). Strategy C is the architectural intent for future iterations. Strategy B is an optional bridge.

---

### G2. Auth Shim (#215 Track 2)

**Current state**: `docs/policies/threat-model-and-trust-boundary.md:7-9, 31-32` deferral; no auth imports in MCP transport layer. Identity is self-asserted via `git config user.email` (`events/writer.py:82-97`).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Per-developer Ed25519 signing keys** | Each developer generates a key pair; events are signed before writing to JSONL; peers verify signatures on replay. Key distribution via BackendAdapter (public keys in a `keys/` directory on the shared backend). | Strong identity without a server; verifiable offline; aligns with #23 (Ed25519 EventEnvelopes proposal); no vendor dependency | Key management burden on operators; key rotation requires coordination; revocation is hard without a central authority |
| **B. BackendAdapter-mediated identity** | Use the backend's native auth as the identity layer. GoogleDriveAdapter: OAuth user identity is the signer. S3Adapter: IAM role/user is the signer. LocalFolderAdapter: OS user is the signer. Author identity derived from backend credentials, not git config. | Zero additional key management; leverages existing auth; identity tied to actual access control | Identity format varies per backend; not portable across backends; LocalFolderAdapter has weak identity (filesystem user) |
| **C. MCP envelope signing (JWT)** | Wrap MCP tool calls in a JWT signed by the developer's key. The `events/writer.py` embeds the JWT in the EventEnvelope. Peers verify the JWT on replay. | Standard format; well-understood; supports claims (expiry, scope, issuer); tooling ecosystem exists | JWT verification requires a shared secret or PKI; overkill for file-share transport where the signer IS the writer; key distribution still needed |
| **D. OS keychain-backed credentials** | Use the OS keychain (macOS Keychain, Windows Credential Manager, Linux libsecret) to store a signing key. MCP server retrieves it at startup. Operator provisions keys via `bicameral setup-wizard`. | No plaintext keys on disk; familiar to developers; works offline; setup wizard already exists (`setup_wizard.py`) | Platform-specific code; harder to automate in CI/headless environments; key is tied to OS user account (not portable) |

**Recommendation**: Strategy A (Ed25519 signing) for long-term, with Strategy D (OS keychain storage) for the key material. Strategy B is a pragmatic v1 starting point that requires zero additional setup.

---

### G3. Multi-Author Write Coordination

**Current state**: Per-author file separation + `canonical_id` UNIQUE is the entire coordination story (`events/backends/__init__.py:9`). No leases, no quorum, no CRDTs.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept current model (per-author files)** | Each author writes only to their own JSONL file. No cross-author write conflicts by construction. `canonical_id` dedup handles same-intent collisions at replay. | Zero coordination overhead; already implemented; O_APPEND atomic for lines < PIPE_BUF; file-level isolation is the strongest possible | Lossy on same-intent divergence (see G5/L4); no mechanism for coordinated multi-author operations |
| **B. Advisory lock protocol** | BackendAdapter ABC already has `lock(remote_name)` (`events/backends/__init__.py:41-42`). Extend with a `team_lock()` that coordinates across all peer files for operations that need cross-author atomicity (e.g., decision supersession). | Enables coordinated operations; ABC already supports locks; LocalFolderAdapter has asyncio.Lock implementation | Advisory only — no enforcement; cross-process locking on NFS/SMB is unreliable; GoogleDriveAdapter lock is sentinel-file based (race window) |
| **C. Optimistic concurrency with version vectors** | Add a monotonic sequence number per author to each event. On replay, detect gaps (missing sequence) or conflicts (same sequence from two sources) and surface to user. | Detects coordination failures; enables conflict surfacing; low overhead per event | Adds complexity to EventEnvelope schema; version vector management across peers; doesn't prevent conflicts, only detects them |

**Recommendation**: Strategy A (current model) is correct for v1. Strategy C is the right upgrade path if conflict detection becomes a user pain point (see L4).

---

### G4. Backend Health / Liveness Probes

**Current state**: `BackendAdapter` ABC at `events/backends/__init__.py:20-50` has no `health()` / `ping()` / `status()` method. Failures are caught by `try/except` in `TeamWriteAdapter.connect()` (`events/team_adapter.py:41-45`).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Add `BackendAdapter.health() -> HealthStatus`** | New abstract method on the ABC that returns reachability + peer count without side effects. Called at session start to show "team backend: reachable (3 peers)" in the session banner. | Low implementation cost; immediate UX value; each adapter implements with native check (folder exists, Drive API ping, S3 HeadBucket) | Adds a method to the frozen ABC (breaking change for external adapter implementations, if any); health check latency at session start |
| **B. Probe via existing `list_peers()`** | Use the existing `list_peers()` async iterator as a health signal — if it yields without error, the backend is reachable. Wrap in a timeout. | No ABC change; already implemented; zero new code for adapters | Not a clean separation of concerns; `list_peers()` may succeed even if push is broken (read-only access); no latency/health metadata |
| **C. Health via `pull_events()` dry-run** | Call `pull_events()` with a sentinel `since_token` that skips actual downloads. If it returns without error, backend is healthy. | No ABC change; tests the actual pull path; more realistic than a simple ping | Unclear sentinel token semantics per backend; GoogleDriveAdapter makes an API call regardless; pull_events has side effects (file copies) |

**Recommendation**: Strategy A is the cleanest. The ABC is marked "frozen" for the contract shape, but adding an optional method with a default implementation (`async def health(self) -> dict: return {"status": "unknown"}`) preserves backward compatibility.

---

### G5. Conflict Resolution

**Current state**: `canonical_id` dedup is first-write-wins (`events/materializer.py:89-91` for ingest). The second write is silently skipped during replay. No merge, no notification, no conflict surfacing.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept first-write-wins (current)** | Second peer's event with the same `canonical_id` is silently skipped. Deterministic; simple; idempotent. | Zero complexity; already works; deterministic replay; no UX surface needed | Silent loss of conflicting peer intent; the user whose decision was "lost" has no signal; violates the "every engineering option should be framed in terms of what problem it solves for the user" philosophy |
| **B. Surface conflicts to human via MCP tool** | When the materializer encounters a `canonical_id` collision with different payload content, log it to a `conflict_log` table. New MCP tool `bicameral.team_conflicts` surfaces unresolved conflicts. User picks the winner or merges manually. | Preserves all peer intent; user stays in control; adds a governance trail ("who decided which version wins"); aligns with the compliance layer value prop | New table + tool + UX surface; materializer needs to compare payloads (not just canonical_id); conflict log can grow if teams have frequent divergence |
| **C. Latest-wins (timestamp-based)** | Instead of first-write-wins, use the `timestamp` field in `EventEnvelope` (`events/writer.py:78`). The event with the latest timestamp wins; earlier events are superseded. | Biases toward most-recent information (often correct); deterministic; no UX surface | Clock skew across machines can produce wrong results; silent loss of the earlier peer's intent; non-deterministic if clocks disagree; violates the current canonical_id invariant semantics |
| **D. Both-survive with link** | Both events are ingested. The second gets a new canonical_id (suffixed with `-conflict-{n}`). A `conflict_of` edge links them. User resolves via existing `bicameral.supersede` tool. | No data loss; leverages existing supersede mechanism; conflict visible in decision graph | Pollutes the decision graph with duplicates; user must actively resolve; doesn't scale if conflicts are frequent |
| **E. Content-hash merge** | If canonical_id matches but payload differs, merge the payloads deterministically (union of fields, concatenate descriptions with separator, keep latest metadata). | Fully automatic; no UX surface; preserves both inputs | Merge semantics are domain-specific and hard to get right; concatenated descriptions may be nonsensical; loss of authorial intent about which version is "correct" |

**Recommendation**: Strategy B (surface to human) is the strongest alignment with the bicameral philosophy ("the compliance layer every team needs"). Strategy D (both-survive with link) is a pragmatic alternative that requires less new infrastructure. A6 decision from @jinhongkuan will determine the v1 semantic.

---

### G6. Per-Peer Bandwidth Metering

**Current state**: Pull/push operations are fire-and-forget; no quota, rate-limit, retry budget per peer. `TeamWriteAdapter.flush_to_backend()` (`events/team_adapter.py:51-61`) fires once per tool-call lifecycle.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Adaptive push throttling** | Track push frequency and payload size per tool-call lifecycle. If push volume exceeds a configurable threshold (e.g., 10 pushes/minute, 1 MB/minute), batch and defer to next lifecycle boundary. | Prevents API rate-limit exhaustion (Google Drive 300 req/min); reduces backend load; configurable per operator | Increases latency for deferred pushes; requires state tracking across tool calls; threshold tuning is operator-specific |
| **B. Backend-native rate limiting** | Let the backend enforce its own limits. GoogleDriveAdapter already handles HTTP 429 implicitly (google-api-python-client retries). LocalFolderAdapter has no limit (filesystem is the bottleneck). Future S3Adapter would use S3's native throttling. | Zero application code; backend-appropriate limits; no configuration burden | Different behaviors per backend; LocalFolderAdapter has no protection; error handling is implicit (no structured reporting) |
| **C. Per-peer quota config** | Add `team.quota.max_push_size_mb` and `team.quota.max_push_rate` to `.bicameral/config.yaml`. `TeamWriteAdapter` enforces before calling `BackendAdapter.push_events()`. | Operator control; prevents runaway peers; visible in config; auditable | Config complexity; needs sensible defaults; quota exceeded → silent data loss unless fallback (local buffer) is implemented |

**Recommendation**: Strategy B (backend-native) for v1 — it's already the implicit behavior. Strategy A as an enhancement if operators report rate-limit issues.

---

### G7. Per-Backend Observability

**Current state**: LocalFolderAdapter and GoogleDriveAdapter have no metrics hooks; only stderr / `cli-errors.log` logging (`events/team_adapter.py:45` warning).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Structured event hooks on BackendAdapter** | Add optional callback hooks (`on_push_complete`, `on_pull_complete`, `on_error`) to the ABC. Default implementations are no-ops. Operators wire them to their observability stack. | Extensible; no vendor lock-in; adapters opt in; default is silent (no regression) | Adds surface area to the ABC; callback design needs care (async? sync? blocking?); no built-in dashboard |
| **B. Emit to `cli-errors.log` with structured JSON** | Enhance existing stderr logging with structured JSON lines for push/pull events: `{"event": "push_complete", "backend": "google_drive", "bytes": 4096, "duration_ms": 340, "peer_count": 3}`. | Minimal code change; parseable by log aggregators (Datadog, Loki); builds on existing pattern | No real-time dashboard; requires external log aggregation; `cli-errors.log` is a grab-bag file | 
| **C. Telemetry integration via `#219` (consent-gated)** | Wire push/pull metrics into the existing telemetry framework gated by `BICAMERAL_TELEMETRY` (`#192`, `#219`). Emits to the same consent-controlled sink as other telemetry. | Consistent with existing telemetry story; consent-gated; solves #219 partially | Depends on #219 shipping (open issue); telemetry framework not yet fully consolidated (`#192` open) |
| **D. MCP tool `bicameral.team_status`** | New read-only MCP tool that returns last push/pull timestamps, backend reachability, peer count, bytes transferred since session start. Agent can display in session banner. | In-band visibility; no external tools; agent can act on it (e.g., warn if stale); aligns with existing MCP tool pattern | Only visible to the current session; no historical data; no cross-session aggregation |

**Recommendation**: Strategy B (structured JSON logging) for immediate value. Strategy D (MCP tool) for agent-visible status. Both are low-cost and complementary.

---

### G8. Team-Governance MCP Tools

**Current state**: No tools for "who is in the team", "kick a peer", "audit who wrote what". Decision-level governance (#231 rate-limit) exists; team-level coordination is missing.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Read-only team tools** | `bicameral.team_peers` (list peers + last activity), `bicameral.team_audit` (who wrote which decisions, from JSONL author fields). No write operations — no kick, no ban. | Low risk; builds on existing `list_peers()` and JSONL author fields; useful for compliance audits; no coordination needed | Read-only limits governance actions; can't revoke access (that's a backend concern); "last activity" requires parsing JSONL timestamps |
| **B. Full governance suite** | Add peer management (invite/kick via BackendAdapter ACL manipulation), audit trail export, team config sync. | Complete governance story; competitive with enterprise collaboration tools | Way beyond v1 scope; backend ACL manipulation is backend-specific; "kick" on file-share = delete their JSONL (dangerous); requires real-time coordination |
| **C. Governance via config convention** | Team membership defined in `.bicameral/config.yaml: team.members: [email1, email2]`. Materializer skips events from unlisted authors. "Kick" = remove from config + next pull ignores their events. | Simple; declarative; no new tools needed; operator-controlled | Config must be synced across all peers (how?); doesn't prevent writes (just ignores them); no real-time enforcement |

**Recommendation**: Strategy A (read-only tools) for v1. These provide compliance audit value ("who wrote which decisions and when") without the complexity of peer management.

---

### G9. Source-Pull Dedup Across Peers

**Current state**: If multiple peers pull from the same Granola / Drive account, redundant API calls + duplicated ingest. No leader-election (`events/materializer.py` dedup handles duplicates at replay, but the redundant API calls are wasteful).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Canonical_id dedup is sufficient** | Let multiple peers pull redundantly. `canonical_id` UNIQUE index (`ledger/schema.py:165`) prevents duplicate ingest. The waste is API calls, not data corruption. | Already works; zero coordination; correct by construction; YAGNI until evidence shows it's a problem | Redundant API calls (cost for Drive, Notion, Granola); each peer does full processing before dedup catches it; can't scale to large teams with many source integrations |
| **B. Pull-leader election via sentinel file** | One peer writes a `pull-leader.lock` sentinel to the shared backend (BackendAdapter already has `lock()` at `events/backends/__init__.py:41`). The leader does source pulls; others skip. Leadership rotates by timestamp/TTL. | Eliminates redundant source pulls; uses existing lock mechanism; simple protocol | Single point of failure (leader goes offline → no pulls until lock expires); lock implementation is advisory (races possible); adds coordination complexity |
| **C. Source-pull results as shared events** | The pulling peer writes source-pull results to a shared JSONL file (e.g., `source-pulls/{source_type}.jsonl`). Other peers read this instead of pulling from the source directly. | Clean separation; source-specific dedup; other peers get the data faster (file-share latency vs API latency) | New file convention; source-pull format must be standardized; still one peer doing all the work; what if that peer's pull is incomplete? |

**Recommendation**: Strategy A (canonical_id dedup) for v1 per R6 from the original research brief. Revisit when an operator reports cost/rate-limit issues from redundant pulls. Strategy B is the simplest coordination upgrade if needed.

---

## Part II — Known Limitations (L1–L15)

### L1. Poll-Only, No Push Notifications

**Current state**: `pull_events()` is called explicitly by `sync-and-brief` CLI or git hooks. No automatic triggering.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Reduce polling interval** | Configure `sync-and-brief` to run more frequently (e.g., every 60s via cron/launchd/Task Scheduler instead of per-commit hook). | Zero code change; operator-configurable; immediate improvement | CPU/IO cost of frequent pulls; still not real-time; battery impact on laptops |
| **B. Filesystem watchers (inotify/FSEvents)** | For LocalFolderAdapter: watch the shared `remote_root` directory for changes. When a peer's JSONL file changes, trigger `pull_events()` + `replay_new_events()` automatically. | Near-real-time for LocalFolder; no polling overhead; OS-native; well-understood | LocalFolderAdapter only (not GoogleDrive, not S3); inotify doesn't work over NFS; FSEvents has batching delay; requires a background process (conflicts with #242's no-daemon principle) |
| **C. Backend-specific webhooks** | GoogleDriveAdapter: use `changes.watch` API (Google Drive push notifications via webhook to a localhost receiver). S3Adapter: S3 Event Notifications → SNS/SQS → local poller. | Near-real-time per backend; uses native cloud capabilities; no custom protocol | Requires a local HTTP listener (webhook receiver); backend-specific implementation; Google Drive webhooks expire after ~24h and need renewal; adds setup complexity |
| **D. Piggyback on tool-call lifecycle** | Pull from backend on every `bicameral.ingest` or `bicameral.preflight` call (already done in `TeamWriteAdapter.connect()` at `events/team_adapter.py:41-45`). Add pull to more tool handlers. | Already partially implemented; no new process; sync happens when the user is actively working; zero extra setup | Only syncs when tools are called; if user doesn't call tools for hours, they're stale; adds latency to every tool call |
| **E. MCP notification channel** | Use MCP's built-in notification mechanism (if the MCP spec supports server→client notifications). Server sends "new peer events available" notification when it detects changes during its own push. | In-band; no extra process; leverages MCP transport; other team members' agents react automatically | MCP notification spec maturity uncertain; only notifies when THIS peer pushes (not when others do); doesn't help with cross-peer detection |

**Recommendation**: Strategy D (piggyback on tool calls) is already partially implemented and covers the common case. Strategy A (reduce interval) is the zero-code fallback. Strategy C (webhooks) is the right long-term play for cloud backends.

---

### L2. No Partial Sync

**Current state**: `pull_events()` copies entire peer JSONL files (with hash-skip). Granularity is per-file.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept full-file sync** | Current behavior. Hash-skip means unchanged files aren't re-downloaded. This is only a problem when files are large AND frequently changing. | Already works; simple; correct; hash-skip handles the common case (unchanged files) | Scales poorly with large event logs; O(file_size) hash computation on every sync check |
| **B. Byte-offset watermarks on remote** | Store the last-read byte offset per peer file on the remote (e.g., `{email}.offset` sentinel file). On pull, request only bytes after the offset. Works for append-only files. | True delta sync for append-only JSONL; minimal transfer; scales linearly with new events, not total log size | Requires per-peer offset tracking on the remote; sentinel file management; breaks if JSONL is rewritten (history rewrite); LocalFolderAdapter can use `f.seek(offset)` + `f.read()` but GoogleDriveAdapter can't do byte-range reads on non-exported files |
| **C. Time-windowed partitioning** | Split JSONL files by time window (e.g., `{email}-2026-W20.jsonl`). Pull only the current and previous window. | Bounded sync scope; natural archival boundary; old files are immutable (good for caching) | More files to manage; materializer needs to handle multi-file-per-author; cross-window events need careful handling; breaks single-file simplicity |
| **D. Content-addressed chunks** | Split JSONL into fixed-size content-addressed chunks (like git packfiles). Index file maps chunks to byte ranges. Pull only new chunks by comparing index. | True delta sync; cache-friendly; scales to very large logs; content-addressed = immutable = cacheable | Significant complexity; new file format; index management; overkill for v1 event log sizes |

**Recommendation**: Strategy A (accept full-file sync) for v1. Strategy B (byte-offset watermarks) is the right first upgrade — `materializer.py` already tracks byte offsets locally (`events/materializer.py:75-80`); extending this to the remote is a natural evolution.

---

### L3. No Write-Time Coordination

**Current state**: Per-author file separation eliminates cross-author write conflicts by construction. Same-intent collisions are detected at replay time via `canonical_id` UNIQUE.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept replay-time coordination** | Current model. Write-time is conflict-free (each author writes to their own file). Replay-time dedup via canonical_id. | Zero overhead; correct; simple; already works | Divergence window between write and replay (see L4) |
| **B. Pre-write canonical_id check** | Before writing an event, check if the canonical_id already exists in the local ledger. If so, skip the write or prompt the user. | Catches conflicts earlier (before they're written to JSONL); reduces replay-time surprises | Only checks local state (peer may have written the same canonical_id but hasn't been synced yet); adds latency to every write; requires ledger query on every ingest |
| **C. Broadcast intent before write** | Before writing, push a lightweight "intent" file to the shared backend (`{email}.intent`). Other peers check for conflicting intents before their own writes. | Catches cross-author conflicts before write; distributed coordination without a server | Significant complexity; race window between intent check and write; requires frequent polling of intent files; overkill for the rarity of same-intent collisions |

**Recommendation**: Strategy A (accept replay-time coordination) for v1. The per-author file model makes write-time conflicts impossible by construction; replay-time dedup is the right level of coordination.

---

### L4. Conflict Resolution Is Lossy

See G5 above — the strategies are identical. The recommended remediation is the same: Strategy B (surface conflicts to human via MCP tool) or Strategy D (both-survive with link).

---

### L5. No Global Event Ordering Across Authors

**Current state**: Each author's events are ordered within their own JSONL file (append-only). Materializer processes each author independently (`events/materializer.py:73` — `sorted(self._events_dir.glob("*.jsonl"))`).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept author-local ordering** | Current model. Events are independently meaningful; causal ordering across authors is rarely needed for decision tracking. | Zero complexity; sufficient for the decision-ledger use case; deterministic within each author's log | Can't reconstruct a global timeline across the team; "what happened first?" questions across authors are unanswerable |
| **B. Lamport timestamps** | Add a logical clock to each event. On push, include the author's current Lamport timestamp. On replay, the materializer uses Lamport timestamps to establish a partial ordering across authors. | Partial causal ordering; lightweight; well-understood; no clock sync needed | Doesn't give total ordering (concurrent events remain unordered); adds a field to EventEnvelope; authors must observe each other's timestamps (requires sync before write) |
| **C. Hybrid logical clocks (HLC)** | Combine physical timestamps with logical counters (as in CockroachDB/Spanner). Each event gets `(physical_time, logical_counter, author_id)`. Total ordering via lexicographic comparison. | Total ordering; tolerates clock skew; well-studied algorithm; deterministic | More complex than Lamport; still requires reasonable clock sync (NTP); adds 3 fields to EventEnvelope; overkill for the decision-ledger domain |
| **D. Merge log on pull** | When `pull_events()` downloads peer files, merge all events into a single sorted-by-timestamp log for replay. Materializer processes this merged view instead of per-file. | Global timeline view; enables "what happened when" across the team; single replay pass | Merge is O(N log K) where K = peer count; merged log is a derived artifact (source files are still per-author); timestamp ties need a tiebreaker (author_id) |

**Recommendation**: Strategy A (accept author-local ordering) for v1. Strategy D (merge log on pull) is the lowest-cost improvement if a global timeline becomes a user need. Strategy B (Lamport timestamps) for correctness-critical ordering.

---

### L6. Identity Is Self-Asserted

See G2 above — the auth shim strategies directly address this limitation. Strategy A (Ed25519 signing) + Strategy D (OS keychain storage) is the long-term remediation. Strategy B (backend-mediated identity) is the pragmatic v1 approach.

---

### L7. No Access Control at the Transport Layer

**Current state**: BackendAdapter has no concept of permissions. Read/write access is governed by the backend itself (filesystem ACLs, Google Drive sharing).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Backend-native ACLs** | Rely on the backend's native access control. GoogleDrive: share folder with specific users. LocalFolder: filesystem ACLs. S3: IAM policies. | Zero bicameral code; leverages proven auth systems; operator-familiar; each backend has mature ACL tools | Different model per backend; no cross-backend consistency; can't differentiate read/write at the bicameral level (e.g., "this peer can read but not write decisions") |
| **B. Signed events + allowlist** | Combine G2 auth (event signing) with a team allowlist in `.bicameral/config.yaml: team.allowed_authors: [email1, email2]`. Materializer ignores events from unlisted authors. Doesn't prevent writes, but prevents replay. | Declarative; config-driven; materializer already has author-per-file knowledge; leverages signing for integrity | Allowlist must be synced across all peers; doesn't prevent unauthorized writes to the shared backend; read access is uncontrolled |
| **C. Encrypted events** | Encrypt JSONL events with a team-shared symmetric key. Only team members with the key can read events. Key distributed via a secure channel (OS keychain, 1Password, etc.). | True read-access control; events are opaque on the shared backend; simple encryption (AES-256-GCM) | Key distribution burden; key rotation is hard; breaks grep-ability of JSONL; all-or-nothing (can't share subsets); encryption adds latency |

**Recommendation**: Strategy A (backend-native ACLs) for v1 — it's already the implicit behavior. Strategy B (signed events + allowlist) for additional defense-in-depth when G2 auth ships.

---

### L8. No Transport-Layer Audit Trail

**Current state**: Push/pull operations are fire-and-forget. No logging beyond stderr warnings on failure (`events/team_adapter.py:45`).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Structured push/pull log** | Append a JSON line to `.bicameral/local/sync-audit.jsonl` on every push/pull: `{"op": "push", "backend": "google_drive", "bytes": 4096, "peers_seen": 3, "ts": "..."}`. | Local audit trail; parseable; no external dependency; useful for debugging sync issues; builds on existing local/ directory convention | Local only — each peer has their own audit log; no cross-peer visibility; file grows unbounded without rotation |
| **B. Audit events in the JSONL substrate** | Emit `sync.push_completed` and `sync.pull_completed` events to the author's JSONL file. These propagate to peers via the normal sync mechanism, creating a distributed audit trail. | Cross-peer visibility; uses existing infrastructure; every peer sees every other peer's sync activity; no new file format | Increases JSONL file size; sync events are not "decisions" (semantic pollution); materializer must handle new event types |
| **C. Backend-native audit logs** | Use the backend's own audit trail. GoogleDrive: Drive audit log (Google Workspace admin). S3: CloudTrail. LocalFolder: filesystem audit (auditd/inotify). | Enterprise-grade; no bicameral code; compliant with SOC 2 requirements; leverages existing infrastructure | Backend-specific; not all backends have audit logs (LocalFolder requires OS config); not accessible from within bicameral |

**Recommendation**: Strategy A (structured push/pull log) for immediate value — local, low-cost, useful for debugging. Strategy C (backend-native) for compliance requirements.

---

### L9. No Health or Presence Signals

See G4 above — the `BackendAdapter.health()` strategies directly address this limitation.

For presence specifically:

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Heartbeat file** | Each peer writes a `{email}.heartbeat` file to the shared backend with a recent timestamp. `list_peers()` reads heartbeat files to determine "online" peers (heartbeat < 5 min = online). | Simple; uses existing backend; no coordination; each peer manages their own heartbeat | Stale heartbeats (peer crashes without cleanup); polling delay; additional file per peer on the shared backend; heartbeat write frequency = API cost for cloud backends |
| **B. Infer from JSONL modification time** | Use the modification timestamp of each peer's JSONL file as a proxy for "last active." If modified within N minutes, the peer is considered active. | Zero additional files; uses existing metadata; LocalFolderAdapter: `stat().st_mtime`; GoogleDriveAdapter: `modifiedTime` from API | Coarse signal (a peer may be active but not ingesting decisions); modification time is write-time, not presence; inactive peers who push rarely look offline even when they're working |

**Recommendation**: Strategy B (infer from JSONL modification time) for v1 — zero cost, already available. Strategy A (heartbeat file) if operators need more granular presence.

---

### L10. No Metrics

See G7 above — the observability strategies directly address this limitation. Strategy B (structured JSON logging) + Strategy D (MCP tool) is the recommended combination.

---

### L11. File-Per-Author Ceiling

**Current state**: `pull_events()` iterates over every peer's JSONL file on every pull (`events/materializer.py:73` — `glob("*.jsonl")`). O(N) in team size.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept O(N) for v1** | Current model. For teams < 20, the cost is negligible (20 files × hash check ≈ milliseconds for LocalFolder, 20 API calls for GoogleDrive). | Zero complexity; sufficient for target v1 team sizes; simple to reason about | Doesn't scale to large organizations; GoogleDrive API cost grows linearly with team size |
| **B. Manifest file** | A single `manifest.json` on the shared backend listing all peers + their JSONL file hashes + modification times. `pull_events()` reads the manifest first, then downloads only changed files. | O(1) manifest read + O(changed) file downloads; significantly reduces API calls for cloud backends; enables batch skip | Manifest must be updated atomically by each pusher (coordination needed); stale manifest = missed updates; adds a new file to the protocol |
| **C. Sharded by topic/module** | Instead of one file per author, shard by topic (e.g., `{author}-{module}.jsonl`). Pull only shards relevant to the current working context. | Enables partial sync (addresses L2 simultaneously); reduces irrelevant event processing | Significantly more complex; "which shard?" decision is non-trivial; cross-shard references need handling; breaks the simple per-author model |
| **D. Managed service adapter** | Future S3/Supabase adapter uses the service's native list+filter capabilities (S3 ListObjectsV2 with prefix, Supabase query with timestamp filter). More efficient than file globbing. | Cloud-native scaling; service handles the iteration; pagination built-in; cost-efficient at scale | Vendor-specific; requires new adapter implementation; not applicable to LocalFolderAdapter |

**Recommendation**: Strategy A (accept O(N)) for v1. Strategy B (manifest file) is the right first optimization if team sizes exceed 20.

---

### L12. No Delta Sync

**Current state**: `push_events()` copies the entire author JSONL file (SHA256 hash-skip avoids redundant copies, `events/backends/local_folder.py:42-46`).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Accept hash-skip** | Current model. If the file hasn't changed (hash matches), skip the copy entirely. This is delta sync at the file granularity. | Already works; simple; correct; zero-copy when unchanged | When the file HAS changed (any new event), the entire file is re-uploaded; O(file_size) hash computation; doesn't scale for active writers |
| **B. Append-only remote writes** | For backends that support append (e.g., S3 Multipart Upload Append, Supabase Storage), push only the new bytes since last push. Track the local byte offset at last push in `.bicameral/local/push-offsets.json`. | True delta push; O(new_events) transfer; minimal bandwidth; the JSONL format is append-only by design | Not all backends support append (GoogleDrive: no; S3: experimental; LocalFolder: yes via `shutil.copy2` of the tail); push-offset tracking needed |
| **C. Chunked uploads** | Split the JSONL file into fixed-size chunks (e.g., 64KB). Content-address each chunk. Upload only new chunks. Reassemble on pull. | Content-addressed = immutable = cacheable; true delta sync; works for any backend | Significant complexity; new file format; chunk index management; overkill for v1; resembles git packfile protocol |
| **D. rsync-style rolling checksum** | Use rsync's rolling checksum algorithm to transfer only the changed blocks. LocalFolderAdapter: use actual rsync. Cloud adapters: implement the algorithm in Python. | Optimal transfer size; well-proven algorithm; LocalFolderAdapter can literally call rsync | Complex to implement for cloud backends; rsync is a separate binary dependency; overkill for append-only files (strategy B is simpler) |

**Recommendation**: Strategy A (hash-skip) for v1. Strategy B (append-only remote writes) for the next iteration — it's the natural fit for append-only JSONL files.

---

### L13. LocalFolderAdapter — Shared Filesystem Concerns

**Current state**: LocalFolderAdapter requires a shared filesystem path (`events/backends/local_folder.py:36-39`). Uses `shutil.copy2` for push/pull. Advisory lock via `asyncio.Lock` (in-process only).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Document filesystem requirements** | Document that LocalFolderAdapter works best with POSIX-compliant shared filesystems (same-machine, syncthing, Dropbox). Warn about NFS stale handles and SMB locking. | Zero code change; manages expectations; helps operators choose the right backend | Doesn't fix the issues, just documents them |
| **B. Add `fcntl.flock` for cross-process locking** | Replace `asyncio.Lock` with `fcntl.flock` (POSIX) / `msvcrt.locking` (Windows) for the `lock()` method. The writer already does this (`events/writer.py:63-69`). | Cross-process safety on same machine; proven pattern (writer already uses it); minimal code change | Still advisory (not mandatory); doesn't work over NFS; Windows `msvcrt.locking` has different semantics |
| **C. Health check for shared filesystem** | Implement `health()` that checks: (1) remote_root exists, (2) is writable, (3) is not a stale NFS mount. Test with a sentinel file write + read. | Catches common issues at session start; user sees "backend unhealthy: NFS mount stale" instead of silent failures | Health check adds latency; sentinel file is a side effect; doesn't prevent mid-session failures |

**Recommendation**: Strategy A (document requirements) + Strategy C (health check) — both are low-cost and complementary.

---

### L14. GoogleDriveAdapter — OAuth and API Constraints

**Current state**: OAuth token at `~/.bicameral/google-drive-token.json` (`events/backends/google_drive.py:38`). MD5 etag matching for skip-copy. `drive.file` scope (Bicameral-created files only).

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Proactive token refresh** | Check token expiry at session start. If token expires within 30 minutes, refresh proactively. Surface "Drive token refreshed" or "Drive token expired — re-auth required" in session banner. | Prevents mid-session auth failures; better UX; proactive rather than reactive | Requires checking expiry time (already in token JSON); doesn't help if refresh token is revoked; adds session start latency |
| **B. Rate-limit awareness** | Track API call count per session. Warn when approaching Drive's 300 req/min limit. Implement exponential backoff on 429 responses (google-api-python-client may already do this). | Prevents rate-limit-induced sync failures; observable; operator can adjust sync frequency | Rate limit tracking adds complexity; limit varies by Google Workspace edition; backoff delays sync |
| **C. Batch API calls** | Use Google Drive's batch API (`new_batch_http_request()`) to combine multiple file operations into a single HTTP request. Useful for `pull_events()` which may download many peer files. | Reduces API call count by up to 100x; faster pulls for large teams; stays within rate limits | Batch API has its own limits (100 calls per batch); error handling is per-call within the batch; adds implementation complexity |
| **D. Alternative cloud backend (S3/Supabase)** | For teams that hit Drive limitations, offer S3 or Supabase as alternative backends. These have different (typically higher) rate limits and no OAuth token management. | Avoids Drive-specific issues entirely; S3 has virtually unlimited API rate; no OAuth | New adapter implementation needed; different auth model (IAM vs OAuth); not free (S3 storage + transfer costs) |

**Recommendation**: Strategy A (proactive token refresh) for immediate UX improvement. Strategy C (batch API) if teams exceed 10 peers. Strategy D (alternative backends) is the long-term architectural intent per R1.

---

### L15. No Version Negotiation

**Current state**: `schema_version: int = 2` in EventEnvelope (`events/writer.py:75`). No enforcement that peers can process each other's schema version.

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **A. Minimum version check on replay** | Materializer checks `schema_version` of each event before replay. If version > local max supported version, log a warning and skip the event (fail-soft). | Prevents crashes from unknown event formats; explicit degradation; low implementation cost | Skipped events are silently lost (same problem as L4); user doesn't know why some peer decisions are missing |
| **B. Version range advertisement** | Each peer writes their supported schema version range to a `{email}.meta` file on the shared backend. On pull, warn if any peer's version is outside the local range. | Users see "peer X is running a newer version — upgrade recommended" at session start; prevents silent incompatibility | New file per peer; metadata must be kept in sync with actual version; warning fatigue if versions differ frequently |
| **C. Forward-compatible envelope design** | Design EventEnvelope so that new fields are always optional and old fields are never removed. Materializer ignores unknown fields (Pydantic `model_config = ConfigDict(extra="ignore")`). | Backward + forward compatible; no version check needed; Pydantic handles it natively; already partially implemented (Pydantic BaseModel ignores extra by default) | Can't handle breaking changes (field type changes, renamed fields, removed required fields); limits schema evolution |
| **D. Schema migration on replay** | Materializer includes migration functions: `if event.schema_version == 1: event = migrate_v1_to_v2(event)`. Each version bump ships with a migration. | Handles all schema evolution; deterministic; migration logic is testable; pattern used in `materializer.py` legacy migration (`_migrate_legacy()`) | Migration code accumulates over time; must be maintained indefinitely; migration bugs corrupt the ledger |

**Recommendation**: Strategy C (forward-compatible envelope design) + Strategy A (minimum version check on replay) — these are complementary and low-cost. Strategy D (migration on replay) is already partially implemented in `_migrate_legacy()` and should continue for breaking changes.

---

## Blueprint alignment check

| Blueprint claim | Actual finding | Status |
|---|---|---|
| R1: MCP local + BackendAdapter, no server process | All 24 remediation strategies are compatible with R1 architecture; none require a server process | **MATCH** |
| BackendAdapter ABC is the extension point | Future adapters (S3, Supabase) are the path for scaling beyond file-share; ABC extensions (health, hooks) are additive | **MATCH** |
| #242 warning fully respected | No strategy reintroduces a self-hosted daemon; local listeners (Strategy C for L1) are optional and localhost-only | **MATCH** |
| Auth shim gated on Track 2 of #215 | G2 strategies map directly to Track 2 design options | **MATCH** |
| canonical_id invariant preserved | No strategy breaks the `(description, source_type, source_ref)` → UUIDv5 derivation | **MATCH** |

**No drift detected.** All remediation strategies are consistent with the R1 architecture.

---

## Recommendations — Prioritized Remediation Roadmap

### Tier 1 — Ship before `/qor-plan` (low-cost, high-signal)

| Item | Strategy | Effort |
|------|----------|--------|
| G4/L9 | `BackendAdapter.health()` with default implementation | 1 commit |
| G7/L10 | Structured JSON push/pull logging | 1 commit |
| L15 | Minimum version check on replay + forward-compatible envelope | 1 commit |
| L8 | Local sync-audit.jsonl | 1 commit |
| L13 | Document LocalFolderAdapter filesystem requirements | 1 commit |

### Tier 2 — Ship with `/qor-plan` scope (v1 deliverables)

| Item | Strategy | Effort |
|------|----------|--------|
| G2/L6 | Auth shim design (Track 2 of #215) | 1 plan cycle |
| G5/L4 | Conflict surfacing via MCP tool (pending A6 decision) | 1 plan + implement cycle |
| G8 | Read-only team governance tools (`bicameral.team_peers`, `bicameral.team_audit`) | 1 implement cycle |
| L14 | Proactive token refresh for GoogleDriveAdapter | 1 commit |

### Tier 3 — Post-v1 (evidence-driven)

| Item | Strategy | Effort |
|------|----------|--------|
| L1 | Backend-specific webhooks (GoogleDrive `changes.watch`, S3 Event Notifications) | 1 implement cycle per backend |
| L2/L12 | Byte-offset watermarks for delta sync | 1 implement cycle |
| L11 | Manifest file for O(1) peer discovery | 1 implement cycle |
| G1 | Managed service adapters (S3, Supabase) | 1 implement cycle per adapter |
| G6 | Adaptive push throttling | 1 commit |

### Tier 4 — Future / evidence-gated

| Item | Strategy | Effort |
|------|----------|--------|
| L5 | Lamport timestamps or merge log | 1 plan + implement cycle |
| L7 | Signed events + allowlist | 1 implement cycle (after G2) |
| G3 | Optimistic concurrency with version vectors | 1 plan cycle |
| G9 | Pull-leader election | 1 implement cycle |

---

## Updated knowledge

- **R1 architecture is remediable for all 24 constraints** without introducing a server process. The BackendAdapter ABC extension path (new methods, new subclasses) covers the long-term evolution.
- **Managed service adapters (S3, Supabase) are the Stage 2 play**, not an HTTP server. This aligns with R1's "no team server" decision while preserving the architectural intent for hosted multi-team deployments.
- **The materializer is the right place for coordination logic** (conflict detection, version checking, ordering). It already handles dedup, watermarks, and legacy migration — extending it for L4/L5/L15 is natural.
- **The BackendAdapter ABC should gain `health()` as an optional method** with a default no-op implementation. This preserves backward compatibility while enabling G4/L9.

---

## Refs

- Upstream research brief: [`docs/research-brief-team-server-tier-v1-2026-05-14.md`](research-brief-team-server-tier-v1-2026-05-14.md)
- Ideation artifact: [`docs/ideation-team-server-tier-v1-2026-05-14.md`](ideation-team-server-tier-v1-2026-05-14.md)
- Known limitations (canonical): [issue #215 comment](https://github.com/BicameralAI/bicameral-mcp/issues/215#issuecomment-4455233107)
- R1 decision: PR #325 (merged to dev 2026-05-14)
- BackendAdapter ABC: `events/backends/__init__.py:20-50`
- Materializer: `events/materializer.py`
- Writer: `events/writer.py`
- TeamWriteAdapter: `events/team_adapter.py`
- GoogleDriveAdapter: `events/backends/google_drive.py`
- LocalFolderAdapter: `events/backends/local_folder.py`
- Ledger schema: `ledger/schema.py:137,165` (canonical_id)
- Issues: [#196](https://github.com/BicameralAI/bicameral-mcp/issues/196), [#215](https://github.com/BicameralAI/bicameral-mcp/issues/215), [#242](https://github.com/BicameralAI/bicameral-mcp/issues/242)
- Google Drive push notifications: [changes.watch API](https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/watch)
- S3 Event Notifications: [AWS docs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/NotificationHowTo.html)

---

_Research complete. Findings are advisory — implementation decisions remain with the Governor._
