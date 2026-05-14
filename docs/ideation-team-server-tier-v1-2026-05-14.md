# Ideation — Team-server tier v1: transport boundary + auth shim

**Date**: 2026-05-14
**Phase**: `/qor-ideate` (governed ideation readiness)
**Analyst**: The Qor-logic Analyst (ideation mode)
**Upstream**: `docs/research-brief-team-server-tier-v1-2026-05-14.md` (META_LEDGER #48)
**Issues**: [#215](https://github.com/BicameralAI/bicameral-mcp/issues/215) (P0), [#196](https://github.com/BicameralAI/bicameral-mcp/issues/196)

---

## Section 1 — Spark Record

**Observation**: The v0 team-mode substrate (event-log + BackendAdapter + canonical_id dedup) ships today and works for file-based sync. But code-grounded decisions — the dominant ingest path — still flow through git replication only. The BackendAdapter pipeline is not yet wired to carry code-grounded decisions to a shared remote backend, and no per-developer authentication exists. This caps the product at single-developer or git-replicated setups and blocks the Stage 2 (Hosted-Repo) business model.

**Initial question**: Where should the tier-v1 transport surface live — inside the MCP server as a handler, or above the BackendAdapter as an HTTP-speaking subclass? The answer determines the shape of everything downstream: auth shim, conflict resolution, observability, team governance tools.

**R1 answer (decided)**: Inside the MCP server (Option 1). Each developer runs their own MCP server locally; team sync happens through the BackendAdapter contract. No separate server process. See Section 7.

**Why now**: #215 is P0 — the trust-boundary gap shows up immediately in any B2B compliance review. Track 1 (doc the boundary) shipped via PR #324. Track 2 (auth shim design) is gated on this ideation's R1 decision. Separately, #196 identified the code-grounded decision sync gap — its original acceptance criteria (`POST /events`, `TeamServerPushAdapter`) predate the R1 decision and require re-scoping to align with the BackendAdapter-mediated architecture (see Section 7, Architectural implication). Strategic charge: Stage 2 of the business model (`visual-plans/bicameral-business-model.html`) requires hosted multi-team deployments; the substrate is ready but the tier is not.

---

## Section 2 — Problem Frame

**Affected actors**:
1. **Multi-developer teams** — cannot sync code-grounded decisions without git replication; no hosted alternative exists
2. **B2B compliance reviewers** — see no auth on the MCP transport; SOC 2 CC1.0/CC6.0 gap surfaces immediately in Type II audit
3. **Operators on shared machines** — OS-user-account trust boundary is insufficient; team-mode activation without auth shim exposes the gap
4. **Product/business** — Stage 2 (Hosted-Repo) value prop requires team-mode beyond git replication; currently blocked

**Failure mode**: Without extending the BackendAdapter pipeline to carry code-grounded decisions, team-mode remains a read-side sidecar for chat/doc sources. Without per-developer authentication on the BackendAdapter transport, team-mode activation (#161) exposes a trust-boundary gap. B2B deals that require SOC 2 Type II evidence cannot close because the trust boundary doc (Track 1) declares team-mode out of scope — which is the correct short-term answer but blocks revenue.

**Cost of failure**: Blocks Stage 2 business model entirely. Every B2B compliance review requires manual explanation of scope limitations. Multi-developer teams stay on git replication with no hosted path forward.

---

## Section 3 — Transformation Statement

Multi-developer teams move from git-replication-only decision sync to an authenticated, BackendAdapter-mediated team-mode — without breaking the v0 substrate invariants (canonical_id dedup, per-author JSONL isolation, BackendAdapter contract) or reintroducing the self-hosted daemon problems that #242 removed.

---

## Section 4 — Assumption Ledger

| # | Statement | Category | Confidence | Impact if wrong | Validation method | Blocking? |
|---|-----------|----------|------------|-----------------|-------------------|-----------|
| A1 | The BackendAdapter contract is sufficient as the wire substrate; tier v1 is additive, not a replacement | technical | high | high — would require substrate redesign | Research brief §1-2 verified MATCH on all 8 alignment checks | yes |
| A2 | Option 2 (beside-MCP broker process) is the wrong shape per #242 lessons | technical | high | medium — could revisit if daemon isolation benefits emerge | #242 post-mortem; research brief R1 exclusion rationale | no |
| A3 | `canonical_id` UUIDv5 derivation is a substrate invariant that tier v1 must not break | technical | high | high — breaks cross-author replay determinism | `ledger/schema.py:137,165` UNIQUE constraint | yes |
| A4 | Stage 2 (Hosted-Repo) may eventually require additional BackendAdapter subclasses (e.g., S3, Supabase) beyond LocalFolder/GoogleDrive. No separate HTTP server process is needed — the R1 decision establishes that future transport surfaces are BackendAdapter implementations, not server runtimes. Architectural intent is preserved for future iterations even though no server-side transport is on the current roadmap. | market | medium | low — if all deployments remain local-folder/Drive, current adapters suffice; new adapters are additive | Business model visual-plan; customer discovery; R1 decision rationale | no |
| A5 | Auth shim design (Track 2 of #215) depends on R1 selection | technical | high | low — auth shim shape is similar regardless, but integration point differs | Research brief R2 dependency chain | yes |
| A6 | First-write-wins via canonical_id is acceptable as the v1 conflict resolution semantic | workflow | medium | medium — silent loss of conflicting peer intent | Research brief R3; awaiting operator decision (posted to @jinhongkuan on PR #325) | **⚠️ OPERATOR INPUT NEEDED** |

---

## Section 5 — Scope Boundary Record

**Non-goals**:
1. Branch/commit/version-control awareness in team_event (#196 explicitly out-of-scope)
2. Slack/Notion ingest path changes (already shipped in #181)
3. Auth/RBAC beyond what's needed for per-developer identity verification (Hosted-Repo tier concern)
4. Source-pull leader-election or per-peer quotas (YAGNI gate per R6)
5. Separate server process of any kind — per R1 decision, no HTTP server runtime, no broker daemon. Future transport surfaces are BackendAdapter subclasses, not server runtimes. The #242 warning is fully respected.

**Limitations**:
1. v1 must coexist with the existing git-replication path — no breaking change to solo-mode operators
2. Auth shim is design-only in this cycle (Track 2 of #215) — no implementation
3. BackendAdapter ABC contract is frozen; tier v1 is additive

**Exclusions**:
1. CRDT-based conflict resolution (too complex for v1; first-write-wins is the starting semantic)
2. Multi-tenant hosted infrastructure (Stage 3 concern)
3. Re-architecting `TeamWriteAdapter`'s wrapper boundary beyond what's needed for the push path

**Forbidden interpretations**:
1. "Team-server" does NOT mean a self-hosted daemon process per #242's removal, and per R1 does NOT mean a separate server process of any kind — it means authenticated team-mode sync via the BackendAdapter contract
2. "Tier v1" does NOT mean replacing the substrate — the event-log + BackendAdapter + canonical_id layer is v0 and stays
3. "No team server now" does NOT mean the architecture can't evolve — future BackendAdapter subclasses (S3, Supabase, HTTP-backed storage) are the intended extension point, not server runtimes. Architectural intent for future iterations is preserved.

**#196 re-scoping note**: Issue #196's original acceptance criteria (`POST /events`, `TeamServerPushAdapter`, `BICAMERAL_TEAM_SERVER_URL`) predate the R1 decision. The *problem* #196 identifies — code-grounded decisions don't sync without git replication — remains valid and is the primary deliverable for `/qor-plan`. The *solution shape* must be updated to use the BackendAdapter pipeline (extend `TeamWriteAdapter` + `push_events()` to carry code-grounded decisions to the configured remote backend) rather than a `POST /events` endpoint.

---

## Section 6 — Concept Brief

**Concept name**: `team-server-tier-v1`

Tier v1 extends the existing BackendAdapter pipeline to enable code-grounded decision sync without git replication, and adds per-developer authentication to the MCP envelope. The v0 substrate (event-log, BackendAdapter, canonical_id dedup, per-author JSONL) is preserved as-is. The tier adds: (1) BackendAdapter-mediated push/pull for code-grounded decisions (extending the existing `TeamWriteAdapter` + `push_events()` pipeline), (2) per-developer authentication within the MCP envelope, (3) full migration from git replication to BackendAdapter for team-sync repos. This unblocks #215 Track 2 (auth shim) and #196 (code-grounded decisions via BackendAdapter), and is a precondition for Stage 2 (Hosted-Repo) of the business model.

---

## Section 7 — Options Matrix

### R1 — Transport Boundary Line (DECIDED)

| Option | Summary | Selected? | Rejection reason |
|--------|---------|-----------|------------------|
| **Option 1: In-MCP handler** | MCP local server remains the only process. Uses BackendAdapter for remote JSONL storage. No separate "team server" process. Most consistent with v0 doctrine. | **Yes** | — |
| **Option 2: Beside-MCP broker** | Separate broker process per developer; MCP server talks to it over local IPC. | **No** | Reintroduces daemon pattern that #242 warned about. Wrong shape — isolation benefit doesn't justify complexity. Excluded by research brief. |
| **Option 3: Above-BackendAdapter HTTP** | New BackendAdapter subclass that speaks to a hosted bicameral-team-server over HTTP. | **No** | There should be no "team server" — the architecture is MCP local server + JSONL stored remotely via BackendAdapter. A separate server process is the wrong shape. |

**Decision by**: @jinhongkuan (2026-05-14)
**Rationale**: "there shouldnt be a 'team server' — 1. Option 1 2. mcp local server + jsonl stored remotely should be the final setup"

**Architectural implication**: The tier-v1 model is *not* a client-server architecture. Each developer runs their own MCP server locally. Team sync happens through the BackendAdapter contract — JSONL files stored on a shared remote backend (LocalFolder, GoogleDrive, or future adapters). The BackendAdapter is the team transport layer; no HTTP server runtime is needed.

### Coexistence with git replication (#196) (DECIDED — follows from R1)

Since R1 selects "MCP local + JSONL stored remotely" as the final architecture, the coexistence question resolves naturally:

| Option | Summary | Selected? | Rejection reason |
|--------|---------|-----------|------------------|
| **(a) Additive** | Write JSONL AND push to team-server; consumer dedups | **No** | No team-server exists; moot. |
| **(b) Primary with fallback** | Team-server primary; JSONL when unreachable | **No** | No team-server exists; moot. |
| **(c) Full migration to BackendAdapter** | JSONL written locally + pushed to remote backend via BackendAdapter. Git replication retired for repos using `team.backend`. | **Yes** | — |

**Implication**: The BackendAdapter `push_events()` / `pull_events()` contract *is* the team sync mechanism. Code-grounded decisions flow through the same JSONL substrate, stored remotely via the configured backend. No separate push adapter needed — the existing `TeamWriteAdapter` + BackendAdapter pipeline handles it.

---

## Section 8 — Governance Profile

**Risk grade**: **L3** — security-relevant (auth shim touches trust boundary) + production-traffic potential (BackendAdapter pipeline handles real decision data in team-mode).

**Evidence required at audit time**:
1. Updated threat model (`docs/policies/threat-model-and-trust-boundary.md`) reflecting the BackendAdapter-mediated team-mode transport
2. Auth protocol specification (Track 2 of #215)
3. Failure isolation test coverage: remote backend unreachable does NOT break local `bicameral.ingest`
4. Coexistence correctness: no data loss under option (c) (full migration to BackendAdapter)
5. `canonical_id` invariant preserved across all team-mode paths

**Escalation triggers**:
1. Any design that requires breaking the `canonical_id` UUIDv5 derivation
2. Any design that makes the MCP server depend on remote backend availability for local operations
3. Auth shim complexity exceeding a single-cycle plan budget

---

## Section 9 — Failure Remediation Plan

| Failure class | Detection signal | Containment action | Return phase |
|---------------|-----------------|-------------------|--------------|
| Auth shim design is too complex for one plan cycle | `/qor-audit` VETO on complexity grounds | Decompose into Track 2a (minimal viable auth) + Track 2b (full RBAC) | plan |
| BackendAdapter push breaks local ingest path | e2e test failure: `bicameral.ingest` errors when remote backend unreachable | Revert push path changes; restore git-only fallback | implement |
| `canonical_id` invariant broken by new push path | Duplicate decisions in ledger after team-mode sync | Halt team-mode activation; fix dedup logic | debug |
| Full migration causes data loss | Decision events missing after git replication retired for a team-sync repo | Re-enable git replication as fallback; investigate BackendAdapter push/pull gap | implement |
| Future BackendAdapter subclass introduces complexity beyond v1 scope | New adapter (S3, HTTP-backed) requires changes to the ABC contract | Freeze ABC; implement as a wrapper adapter that composes with existing ABC | research |

---

## Section 9a — Known Limitations of the R1 Architecture

The R1 decision (MCP local + BackendAdapter file-share, no server process) trades operational complexity for simplicity. The following are inherent architectural constraints — not bugs, but boundaries that `/qor-plan` should acknowledge and that future iterations may address through new BackendAdapter subclasses.

### Sync & Latency

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L1 | **Poll-only, no push notifications** — peers see changes only on next `pull_events()` call (via `sync-and-brief` CLI or hook). Latency = polling interval, not network round-trip. | Decisions ingested at 2:01 may not be visible to peers until next poll (e.g. 2:15). | Acceptable for v1. Could add filesystem watchers (inotify/FSEvents) or backend-specific webhooks (Google Drive push notifications) in future adapters. |
| L2 | **No partial sync** — `pull_events()` copies every peer's entire author file (hash-skip optimization avoids redundant transfers, but the granularity is per-file, not per-event). | Can't pull only events related to a specific module or decision area. All-or-nothing per author. | Acceptable for v1 event-log sizes. Partitioning by time window or topic is a future adapter concern. |

### Consistency & Conflicts

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L3 | **No write-time coordination** — two developers can write conflicting decisions simultaneously. No distributed lock. Coordination is `canonical_id` first-write-wins evaluated at *replay* time (materializer), not write time. | Silent divergence window between write and replay. | Acceptable for v1; `canonical_id` UNIQUE constraint catches it at replay. Surface-to-human tool (A6 option 2) would close the UX gap. |
| L4 | **Conflict resolution is lossy** — when two peers write the "same" decision (same `canonical_id`) with different rationales, the second writer's intent is silently dropped during replay. No merge, no notification. | Risk of silent loss of conflicting peer intent. | A6 decision (posted to @jinhongkuan) will determine v1 semantic. Options: accept lossy skip, surface conflicts, or deterministic merge rule. |
| L5 | **No global event ordering across authors** — each author's events are ordered within their own JSONL file (append-only), but no global ordering. Events from different authors interleave in whatever order the materializer encounters them. | Causal ordering across authors is not guaranteed. | Acceptable for v1: decisions are independently meaningful. Causal ordering (vector clocks, Lamport timestamps) is a future concern. |

### Identity & Access

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L6 | **Identity is self-asserted** — author identity comes from `git config user.email` (`events/writer.py:82-97`). A developer can change their git email and appear as a different author. No server-side verification. | Impersonation possible; audit trail unreliable for compliance. | Auth shim (Track 2 of #215) is designed to close this gap. Under file-share-only, there's nothing to verify against. |
| L7 | **No access control at the transport layer** — if you can read/write to the shared folder or Google Drive folder, you have full access. No per-developer read/write restrictions, no role-based access. | All team members are equal peers; no admin/read-only roles. Revocation requires revoking filesystem/Drive permissions. | Acceptable for v1 (small trusted teams). RBAC is a future BackendAdapter concern. |

### Observability & Operations

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L8 | **No transport-layer audit trail** — push/pull operations are fire-and-forget. No log of who pushed when, no receipt confirmation, no integrity verification at the transport layer. | Event-log has author fields, but the transport doesn't verify them. | Add push/pull event logging to BackendAdapter ABC in a future version. |
| L9 | **No health or presence signals** — no way to know who is actively working. `list_peers()` shows who has *ever* pushed files, not who is online. No backend health probe (`BackendAdapter` has no `health()` method). | No team awareness; no "is the backend reachable?" check at session start. | Research brief R4 recommends `BackendAdapter.health()`. Defer to post-R1. |
| L10 | **No metrics** — no instrumentation for sync frequency, sync latency, sync failures, data volume. Only stderr logging via `cli-errors.log`. | Operational blind spot; failures are silent unless the developer checks stderr. | Add structured telemetry hooks to BackendAdapter pipeline. |

### Scalability

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L11 | **File-per-author ceiling** — `pull_events()` iterates over every peer's file on every pull. | O(N) in team size per pull. Works for small teams; increasingly expensive as team grows. No sharding or partitioning. | Acceptable for v1 team sizes (< 20). Future adapters could shard by time window or topic. |
| L12 | **No delta sync** — `push_events()` copies the entire author file (SHA256 hash-skip avoids redundant copies, but the hash computation itself scales with file size). | As event logs grow, hash computation becomes a bottleneck. No incremental append-only transport. | Content-addressed chunking or byte-offset watermarks on the remote side could enable delta sync. |

### Backend-Specific

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L13 | **LocalFolderAdapter** — requires shared filesystem access (NFS, SMB, mounted volume). | Introduces filesystem-specific reliability concerns: NFS stale file handles, SMB locking semantics, latency over WAN. | Acceptable for co-located teams. Remote teams should prefer GoogleDriveAdapter or future cloud adapters. |
| L14 | **GoogleDriveAdapter** — OAuth token management, Google API rate limits (300 queries/min default), 15GB free tier ceiling, eventual consistency. | MD5 etag matching can miss rapid successive writes. Token refresh failures break sync silently. | Acceptable for v1. S3/Supabase adapters would avoid Drive-specific constraints. |

### Schema & Versioning

| # | Limitation | Impact | Mitigation path |
|---|-----------|--------|-----------------|
| L15 | **No version negotiation** — if one developer updates their MCP server (new event types, new fields), their events may not be understood by peers running older versions. `schema_version` field in `EventEnvelope` exists but there's no enforcement that a peer can process a newer version. | Forward-compatibility not guaranteed. | Acceptable for v1 (teams typically coordinate upgrades). Version negotiation is a future adapter concern. |

### Prioritization for `/qor-plan`

The limitations most likely to bite first in practice:
1. **L4 (lossy conflicts)** + **L6 (self-asserted identity)** — these are the A6 and #215 Track 2 priorities already identified
2. **L9 (no health signal)** — easy win; research brief R4 recommends `BackendAdapter.health()`
3. **L1 (polling latency)** — acceptable for v1 but will surface as a UX complaint in active multi-developer sessions

The remaining limitations (L2, L3, L5, L7, L8, L10-L15) are acceptable trade-offs for v1 team sizes and usage patterns.

---

## Section 10 — Readiness Scoring

**Readiness status**: `ready` (with one open non-blocking assumption)

Resolved operator decisions:
- R1: Option 1 (MCP local server + remote JSONL via BackendAdapter) — no team server now; architectural intent for future BackendAdapter subclasses preserved
- Coexistence: (c) full migration to BackendAdapter for team-sync repos

Open non-blocking assumption:
- A6: First-write-wins conflict resolution semantic — awaiting operator decision from @jinhongkuan (does not block `/qor-plan`; plan can proceed with first-write-wins as the default and surface-conflicts-to-human as the alternative)

**Recommended next phase**: `/qor-plan` — scope: extend BackendAdapter pipeline to handle code-grounded decision push for #196, and design auth shim within MCP envelope for #215 Track 2.

---

## Delegation

Per `qor/gates/delegation-table.md`:
- Current status: `ready` (R1 + coexistence decisions resolved 2026-05-14)
- Route: `/qor-plan` → `/qor-audit` → `/qor-implement`

---

_Ideation complete. R1 decided: MCP local + remote JSONL via BackendAdapter (no team server). Ready for `/qor-plan`._
