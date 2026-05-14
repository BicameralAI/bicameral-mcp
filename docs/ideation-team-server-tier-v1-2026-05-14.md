# Ideation — Team-server tier v1: transport boundary + auth shim

**Date**: 2026-05-14
**Phase**: `/qor-ideate` (governed ideation readiness)
**Analyst**: The Qor-logic Analyst (ideation mode)
**Upstream**: `docs/research-brief-team-server-tier-v1-2026-05-14.md` (META_LEDGER #48)
**Issues**: [#215](https://github.com/BicameralAI/bicameral-mcp/issues/215) (P0), [#196](https://github.com/BicameralAI/bicameral-mcp/issues/196)

---

## Section 1 — Spark Record

**Observation**: The v0 team-mode substrate (event-log + BackendAdapter + canonical_id dedup) ships today and works for file-based sync. But code-grounded decisions — the dominant ingest path — still flow through git replication only. The team-server tier above the substrate is entirely absent: no HTTP transport, no auth, no conflict resolution beyond first-write-wins. This caps the product at single-developer or git-replicated setups and blocks the Stage 2 (Hosted-Repo) business model.

**Initial question**: Where should the tier-v1 transport surface live — inside the MCP server as a handler, or above the BackendAdapter as an HTTP-speaking subclass? The answer determines the shape of everything downstream: auth shim, conflict resolution, observability, team governance tools.

**Why now**: #215 is P0 — the trust-boundary gap shows up immediately in any B2B compliance review. Track 1 (doc the boundary) shipped via PR #324. Track 2 (auth shim design) is gated on this ideation's R1 decision. Separately, #196's acceptance criteria require `POST /events` on the team-server and a push adapter — both need R1 settled first. Strategic charge: Stage 2 of the business model (`visual-plans/bicameral-business-model.html`) requires hosted multi-team deployments; the substrate is ready but the tier is not.

---

## Section 2 — Problem Frame

**Affected actors**:
1. **Multi-developer teams** — cannot sync code-grounded decisions without git replication; no hosted alternative exists
2. **B2B compliance reviewers** — see no auth on the MCP transport; SOC 2 CC1.0/CC6.0 gap surfaces immediately in Type II audit
3. **Operators on shared machines** — OS-user-account trust boundary is insufficient; team-mode activation without auth shim exposes the gap
4. **Product/business** — Stage 2 (Hosted-Repo) value prop requires team-server tier; currently blocked

**Failure mode**: Without a tier-v1 transport surface, team-mode activation (#161) would expose an unauthenticated network surface. Code-grounded decisions remain git-only, capping the team-server at a read-side sidecar for chat/doc sources. B2B deals that require SOC 2 Type II evidence cannot close because the trust boundary doc (Track 1) declares team-mode out of scope — which is the correct short-term answer but blocks revenue.

**Cost of failure**: Blocks Stage 2 business model entirely. Every B2B compliance review requires manual explanation of scope limitations. Multi-developer teams stay on git replication with no hosted path forward.

---

## Section 3 — Transformation Statement

Multi-developer teams move from git-replication-only decision sync to an authenticated, transport-bounded team-server tier — without breaking the v0 substrate invariants (canonical_id dedup, per-author JSONL isolation, BackendAdapter contract) or reintroducing the self-hosted daemon problems that #242 removed.

---

## Section 4 — Assumption Ledger

| # | Statement | Category | Confidence | Impact if wrong | Validation method | Blocking? |
|---|-----------|----------|------------|-----------------|-------------------|-----------|
| A1 | The BackendAdapter contract is sufficient as the wire substrate; tier v1 is additive, not a replacement | technical | high | high — would require substrate redesign | Research brief §1-2 verified MATCH on all 8 alignment checks | yes |
| A2 | Option 2 (beside-MCP broker process) is the wrong shape per #242 lessons | technical | high | medium — could revisit if daemon isolation benefits emerge | #242 post-mortem; research brief R1 exclusion rationale | no |
| A3 | `canonical_id` UUIDv5 derivation is a substrate invariant that tier v1 must not break | technical | high | high — breaks cross-author replay determinism | `ledger/schema.py:137,165` UNIQUE constraint | yes |
| A4 | Stage 2 (Hosted-Repo) requires Option 3 (BackendAdapter-over-HTTP) eventually | market | medium | medium — if all deployments remain local-folder/Drive, Option 1 suffices | Business model visual-plan; customer discovery | no |
| A5 | Auth shim design (Track 2 of #215) depends on R1 selection | technical | high | low — auth shim shape is similar regardless, but integration point differs | Research brief R2 dependency chain | yes |
| A6 | First-write-wins via canonical_id is acceptable as the v1 conflict resolution semantic | workflow | medium | medium — silent loss of conflicting peer intent | Research brief R3; needs operator confirmation | **⚠️ OPERATOR INPUT NEEDED** |

---

## Section 5 — Scope Boundary Record

**Non-goals**:
1. Branch/commit/version-control awareness in team_event (#196 explicitly out-of-scope)
2. Slack/Notion ingest path changes (already shipped in #181)
3. Auth/RBAC beyond what's needed for per-developer identity verification (Hosted-Repo tier concern)
4. Source-pull leader-election or per-peer quotas (YAGNI gate per R6)
5. Full HTTP server runtime reintroduction (the #242 warning applies to self-hosted OAuth workers, not to all HTTP surfaces)

**Limitations**:
1. v1 must coexist with the existing git-replication path — no breaking change to solo-mode operators
2. Auth shim is design-only in this cycle (Track 2 of #215) — no implementation
3. BackendAdapter ABC contract is frozen; tier v1 is additive

**Exclusions**:
1. CRDT-based conflict resolution (too complex for v1; first-write-wins is the starting semantic)
2. Multi-tenant hosted infrastructure (Stage 3 concern)
3. Re-architecting `TeamWriteAdapter`'s wrapper boundary beyond what's needed for the push path

**Forbidden interpretations**:
1. "Team-server" does NOT mean a self-hosted daemon process per #242's removal — it means an authenticated transport layer
2. "Tier v1" does NOT mean replacing the substrate — the event-log + BackendAdapter + canonical_id layer is v0 and stays

---

## Section 6 — Concept Brief

**Concept name**: `team-server-tier-v1`

Tier v1 adds an authenticated transport surface above the v0 substrate to enable code-grounded decision sync without git replication. The v0 substrate (event-log, BackendAdapter, canonical_id dedup, per-author JSONL) is preserved as-is. The tier adds: (1) a transport endpoint for decision push/pull, (2) per-developer authentication, (3) failure-isolated coexistence with git replication. This unblocks #215 Track 2 (auth shim) and #196 (code-grounded decisions to team-server), and is a precondition for Stage 2 (Hosted-Repo) of the business model.

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

**Risk grade**: **L3** — security-relevant (auth shim touches trust boundary) + production-traffic potential (team-server handles real decision data).

**Evidence required at audit time**:
1. Updated threat model (`docs/policies/threat-model-and-trust-boundary.md`) reflecting the new transport surface
2. Auth protocol specification (Track 2 of #215)
3. Failure isolation test coverage: team-server unreachable does NOT break local `bicameral.ingest`
4. Coexistence correctness: no double-ingest under option (a); no data loss under option (c)
5. `canonical_id` invariant preserved across all team-mode paths

**Escalation triggers**:
1. Any design that requires breaking the `canonical_id` UUIDv5 derivation
2. Any design that makes the MCP server depend on team-server availability for local operations
3. Auth shim complexity exceeding a single-cycle plan budget

---

## Section 9 — Failure Remediation Plan

| Failure class | Detection signal | Containment action | Return phase |
|---------------|-----------------|-------------------|--------------|
| Auth shim design is too complex for one plan cycle | `/qor-audit` VETO on complexity grounds | Decompose into Track 2a (minimal viable auth) + Track 2b (full RBAC) | plan |
| Team-server push breaks local ingest path | e2e test failure: `bicameral.ingest` errors when team-server unreachable | Revert push adapter; restore git-only path | implement |
| `canonical_id` invariant broken by new transport | Duplicate decisions in ledger after team-mode sync | Halt team-mode activation; fix dedup logic | debug |
| Coexistence mode causes double-ingest | Duplicate entries detected during cross-author replay | Switch from additive (a) to primary-with-fallback (b) | implement |
| #242-style daemon problems resurface (Option 3 only) | Operator complaints about process management / resource leaks | Fall back to Option 1 scope; defer Option 3 | research |

---

## Section 10 — Readiness Scoring

**Readiness status**: `ready`

Both operator decisions are resolved:
- R1: Option 1 (MCP local server + remote JSONL via BackendAdapter)
- Coexistence: (c) full migration to BackendAdapter for team-sync repos

**Recommended next phase**: `/qor-plan` — scope: extend BackendAdapter pipeline to handle code-grounded decision push for #196, and design auth shim within MCP envelope for #215 Track 2.

---

## Delegation

Per `qor/gates/delegation-table.md`:
- Current status: `ready` (R1 + coexistence decisions resolved 2026-05-14)
- Route: `/qor-plan` → `/qor-audit` → `/qor-implement`

---

_Ideation complete. R1 decided: MCP local + remote JSONL via BackendAdapter (no team server). Ready for `/qor-plan`._
