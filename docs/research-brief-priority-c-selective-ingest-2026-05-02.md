# Research Brief — Priority C: selective source ingest (re-research v2)

**Date**: 2026-05-02 (replaces v1, which was rejected for `INVARIANT_FROM_IMPLEMENTATION` — see `docs/SHADOW_GENOME.md` Failure Entry #6)
**Analyst**: The QorLogic Analyst (executed via `/qor-research`)
**Target**: v0 Priority C — selective source ingest (GitHub / Notion / Slack) at multi-dev / multi-agent / multi-host scale
**Substrate**: operator-supplied Sales Enablement & Positioning Playbook + `docs/CONCEPT.md` + `docs/ARCHITECTURE_PLAN.md` + repo source code, with **"unproven is theater"** filter active throughout
**Constraint**: Claude (Code + Desktop) only at v0 (Priority D constraint)

---

## Executive Summary

Priority C scope, after dialogue: **Slack-first source ingest, via a self-managing team-server, with CocoIndex (#136) memoization for canonical extraction**. Multi-dev decision continuity (Playbook Pillar #1) requires extraction convergence in addition to the storage convergence the existing `events/team_adapter.py` JSONL-via-git pattern already provides.

The repo already implements **storage-layer** convergence: `TeamWriteAdapter` dual-writes per-author JSONL files (git-merged), `EventMaterializer` replays peer events with watermark, `canonical_id` UNIQUE coalesces at DB level. The gap is **extraction-layer** divergence — same Slack thread, different agents, different extractions. The team-server closes this by owning the canonical extraction (CocoIndex memoization) and exposing it to per-dev local ledgers.

Local-first per CONCEPT.md is honored under literal-keyword parsing: the anti-goal *"No managed backend"* blocks vendor SaaS and human-ops-tax architectures, not self-managing customer-self-hosted backends. Sentry self-hosted, Supabase OSS, the existing embedded-SurrealDB philosophy are precedents.

Source priority Slack → Notion → GitHub-via-skill, by **disorder-to-info ratio** (operator-resolved): Slack has no structure and no useful AI-dev-environment connector for decision extraction; Notion is structured and has connectors; GitHub is organically in the SDLC and resolves to a skill/hook nudge (agent consults git) rather than team-server ingest.

---

## Findings

### F1 — Event-sourced multi-dev consistency exists today

[`events/team_adapter.py`](events/team_adapter.py) `TeamWriteAdapter` wraps `SurrealDBLedgerAdapter` via composition. On every write: (1) emit an event file via `EventFileWriter`, (2) delegate to the inner adapter. Reads pass through directly.

[`events/writer.py:1-12`](events/writer.py): *"Each contributor owns a single file: `.bicameral/events/{email}.jsonl`. Events are appended one per line. Git merges are additive (both sides only append)."*

[`events/materializer.py:1-9`](events/materializer.py): *"Replays JSONL event logs into the local ledger… One file per contributor… Watermark is a JSON `{email: byte_offset}` map at `.bicameral/local/watermark`. Replay resumes from the stored offset per author."*

[`tests/test_team_event_replay.py`](tests/test_team_event_replay.py) exercises this end-to-end: Dev A writes events, Dev B materializes them into Dev B's local ledger, ledgers converge.

The pattern is **event-sourced with git as sync mechanism**. Local-first is preserved per CONCEPT.md anti-goals. **MATCH** with playbook Pillar #1 (Decision Continuity) at the storage layer.

### F2 — Event log is per-author; canonical_id at the DB level coalesces

`.bicameral/events/{email}.jsonl` is per-contributor. Setup via [`setup_wizard.py:197-209`](setup_wizard.py): *"In team mode, local DBs go under `.bicameral/local/` (gitignored) so they don't leak into the tracked events directory."*

So team mode tracks events in repo (`.bicameral/events/`) and gitignores per-dev DB (`.bicameral/local/`). Devs share events, materialize into per-dev DBs.

Dedup at the DB level via `canonical_id` UNIQUE index ([`events/writer.py:11`](events/writer.py): *"Dedup now relies on the DB-level `canonical_id` UNIQUE index instead of filesystem collisions."*).

### F3 — CONCEPT.md anti-goals parsed literally — load-bearing keywords are `managed` and `deterministic core`

> *"**local-first** — runs entirely in-process via embedded SurrealDB; no cloud, no network calls in the deterministic core."*
>
> Anti-Goals:
> - *"Not a cloud service. No remote DB, no managed backend; the ledger lives next to the repo it tracks."*
> - *"Not an LLM-powered ledger. The deterministic core does not invoke any model."*

Operator-resolved during dialogue (recorded as `docs/SHADOW_GENOME.md` Failure Entry #6 addendum): these anti-goals must be parsed by their **load-bearing keyword**, not generalized. The keywords:

- *"No managed backend"* — keyword: **managed**. A self-managing, customer-self-hosted, schema-migrating-itself, no-on-call backend is **compatible**. The anti-goal blocks vendor SaaS and human-ops-tax architectures, not server-side components per se. (Sentry self-hosted, Supabase OSS, embedded-SurrealDB precedents.)
- *"No cloud, no network calls in the deterministic core"* — keyword: **deterministic core**. Network calls outside the deterministic core (source ingest workers, telemetry) are not blocked.
- *"Not an LLM-powered ledger"* — keyword: **ledger**. LLMs as callers/classifiers/orchestrators around the ledger are not blocked.

So a self-managing team-server that holds Slack credentials, runs CocoIndex memoization for canonical extraction, and exposes results to per-dev local ledgers honors all three anti-goals under literal parsing. The team-server is the natural Priority C anchor.

### F4 — Real Priority C gap: extraction-layer divergence

Today's flow:
1. Dev A agent reads Slack thread X via host's Slack MCP connector
2. Dev A agent extracts 3 decisions
3. `bicameral.ingest` writes 3 decision rows + emits 3 events to `.bicameral/events/dev_a@org.com.jsonl`
4. Dev B agent reads the same Slack thread X (later, separate session)
5. Dev B agent extracts 5 decisions (richer pass; or fewer; or different framing of the same ideas)
6. `bicameral.ingest` writes — `canonical_id` UNIQUE may collide on overlap, dropping or last-write-winning the duplicates

The DB has SOMETHING for the thread, but it's not **canonical extraction** — it's "whichever agent's read happened to land first/last." Two devs preflight the same code path against the same Slack source and could see different decision sets if their extractions diverged on edge cases.

This breaks Playbook Pillar #1 *"preserves the chain between a human decision and the code that implements it"* at multi-dev scale. The chain only preserves if the decision set is canonical, not just deduplicated.

### F5 — `source_type` schema supports playbook source list with no change

[`contracts.py:815`](contracts.py): `Literal["transcript", "slack", "document", "agent_session", "manual"]`. [`handlers/history.py:30-36`](handlers/history.py) normalizes `notion → document`.

Schema is source-agnostic. The playbook's source list (PRDs, ADRs, Slack, transcripts, Jira/Linear, PR discussions, code comments, design docs, verbal agreements, agent sessions) all map to existing `source_type` values. **MATCH** — no schema change required for Priority C as such.

### F6 — Issue #136 CocoIndex is the architectural lever for deterministic extraction

[Issue #136](https://github.com/BicameralAI/bicameral-mcp/issues/136): *"v1 Architecture §6: implement CocoIndex execution layer for Layer A pre-classifier and Layer B identity capture."* Per the operator's earlier framing this session, #136 has strategic dimension (founder relationship + publicity) plus architectural impact (memoization for the pre-classifier + identity capture).

Memoization on Layer A pre-classifier means: *"this Slack thread, processed by the v0.X pre-classifier, deterministically yields THIS decision set."* If Dev A's session pre-classifies the thread, the result is cached. Dev B's session pulls the cache instead of re-classifying — same input → same output across devs. **This is the convergence mechanism for extraction-layer determinism.**

#136 is currently labeled in the open-issues list with no priority tag, but operator has flagged it strategically. Priority C threading through #136 is plausibly the architecturally clean path. Confirming this requires #136 design dialogue with founder; not yet done.

### F7 — Existing curation surface is the `bicameral-ingest` SKILL's permissive trigger

[`skills/bicameral-ingest/SKILL.md`](skills/bicameral-ingest/SKILL.md) frontmatter: *"AUTO-TRIGGER on ANY of these: (1) user pastes or mentions a transcript, meeting notes, Slack thread, PRD, spec, or design doc … (4) user answers a gap or open question … When in doubt, ingest — a false trigger that captures zero decisions is cheaper than missing a real decision."*

This is solo-developer-tuned: prefer over-ingestion to under-ingestion. At enterprise multi-dev scale, the failure modes invert — over-ingestion creates noise across the team that's hard to selectively reject because it's deduplicated/replayed across all devs' DBs.

### F8 — No source-fetcher / OAuth / API-client code exists today

`grep -rn "oauth|api_key|client_secret|GITHUB_TOKEN|SLACK_TOKEN|NOTION_API"` over `*.py` returns no matches outside test eval-judge code (which uses `ANTHROPIC_API_KEY` for an unrelated LLM-judge surface).

This is **a current observation, not an architectural invariant** (per `docs/SHADOW_GENOME.md` Failure Entry #6). However, the local-first principle in F3 makes the simplest path forward continue to lean on host-supplied connectors for fetch authority, with bicameral owning extraction determinism rather than fetch credentials.

---

## Blueprint Alignment

| Playbook claim | Repo finding | Status |
|---|---|---|
| Decision-to-code continuity at multi-dev scale (Pillar #1) | `TeamWriteAdapter` + git-merged JSONL events + `EventMaterializer` watermark exists | **MATCH at storage layer** |
| Same decision-set across devs from same source | Extraction is per-agent; canonical_id dedup hides drift | **GAP — Priority C target** |
| Local-first decision ledger | CONCEPT.md ratifies "no cloud, no managed backend"; team mode preserves it | MATCH |
| Multi-source ingest (Slack, Notion, GitHub, etc.) | `source_type` Literal already covers; `notion → document` normalization present | MATCH |
| Deterministic core; LLMs are callers, never truth-bearers | Honored in current code; #136 CocoIndex would extend deterministic substrate to extraction | MATCH (+ extension path via #136) |
| Bicameral amplifies existing tools, never replaces | Source fetching delegated to host MCP connectors; bicameral never duplicates GitHub/Slack/Notion's own surface | MATCH |
| Bicameral never blocks, only exposes/escalates (Pillar #5) | Today's permissive ingest never blocks; gates would also be exposure-only ("warn before ingest" not "refuse to ingest") | MATCH constraint for any Priority C gate design |

---

## Recommendations (priority-ordered for follow-on `/qor-plan`, all theater-flagged where unproven)

1. **[P0] Anchor Priority C on a self-managing team-server, Slack-first** — not a curation gate, not source-plumbing-via-agent. The team-server holds Slack credentials, runs source workers, hosts the canonical-extraction substrate, and syncs to per-dev local ledgers. Customer self-hosts; no human ops surface. Compatible with CONCEPT.md anti-goals under literal-keyword parsing (F3).
2. **[P0] Bundle CocoIndex (#136) into v0 team-server, conditional on feasibility** — operator-confirmed in scope ("good idea if we can manage it"). Layer A pre-classifier + Layer B identity capture as memoized transforms = the deterministic-extraction substrate that closes the multi-dev convergence gap (F4). The plan should structure CocoIndex integration as a discrete phase that can slip independently if calendar/founder-coordination blocks it; v0 ships without if needed, with extraction determinism deferred to an interim cache.
3. **[P0] Interim canonical-extraction cache (fallback if CocoIndex slips)** — team-server-side keyed table `(source_type, source_ref) → canonical_extraction_json`. Subsequent agent ingests of the same source-event pull the cache instead of re-extracting. Provides convergence without CocoIndex; ships independently if #136 is blocked. *Unproven: whether this composes cleanly with `TeamWriteAdapter`'s JSONL event log; design dialogue at `/qor-plan` time.*
4. **[P1] Slack auth + channel-selection UX** — workspace-level OAuth in the team-server; admin selects which channels are ingested; allow-list semantics. Honors Pillar #5 (Human Authority) and Pillar #6 (amplifies existing tools — Slack remains the system of record). Specific UX shape (web admin? CLI? config file?) is `/qor-plan` dialogue surface.
5. **[P1] Sync mechanism between team-server and per-dev local ledgers** — extension of the existing `events/team_adapter.py` JSONL pattern: team-server writes events the same way an authoring dev would, devs' materializers replay them. Treats the team-server as a peer in the existing event-sourcing model. *Unproven: whether the team-server's per-author identity (single bot? per-source bot?) plays cleanly with the per-author JSONL convention.*
6. **[P2] Notion-second deferred to v1** — same team-server architecture; lower urgency per disorder-to-info ratio (Notion is already structured).
7. **[P2] GitHub via skill enforcement, not team-server** — agent-consult-git nudge via `UserPromptSubmit` hook (similar shape to PR #151's preflight hook). Separate small plan; not in Priority C scope.
8. **[Defer] Vendor SaaS, human-ops-tax architectures** — these would violate the literal "managed" keyword. If the product needs paid-hosting offerings later, that's a separate strategic decision, not a v0 Priority C move.
9. **[Defer] Per-source MCP tools** (`bicameral.ingest_slack`, etc.) — breaks the 13-tool capability-not-source norm. Source-specific behavior belongs in the team-server worker layer or extraction rubric, not in MCP tool-surface.

---

## Theater audit (anything in this brief not grounded in cited source)

Per the "unproven is theater" doctrine, the following claims in this brief are **interpretations beyond direct citation** and should be treated as observation, not principle:

- **"CocoIndex (#136) memoization closes the extraction convergence gap"** — partial interpretation. #136's body cites Layer A pre-classifier and Layer B identity capture being "useful as memoized transforms." Operator confirmed during dialogue that CocoIndex helps with visibility and is in v0 scope conditional on feasibility. Whether the *specific mechanism* (memoization keyed on source-event identity, deterministic across devs) matches the operator/founder's design intent for extraction-layer convergence still needs verification at `/qor-plan` time.
- **"Multi-dev preflight on the same code path could see different decision sets"** — plausible failure mode derivable from F2+F4, not constructed as a repro test. Treated as design risk, not demonstrated bug.
- **"Self-managing team-server is compatible with CONCEPT.md anti-goals under literal-keyword parsing"** — operator-resolved during dialogue (recorded as SHADOW_GENOME Entry #6 addendum). Should be re-pressure-tested at `/qor-audit` time when the planning cycle goes through governance gates.
- **All Recommendations** — design proposals, not demonstrated mechanisms. The next `/qor-plan` is where these get pressure-tested or replaced. Specifically the team-server's deployment shape, sync-with-events-via-git pattern, Slack-auth UX surface, and CocoIndex feasibility are all dialogue surfaces, not closed answers.

---

## Updated Knowledge — for SHADOW_GENOME / project memory

- (Already saved) `docs/SHADOW_GENOME.md` Failure Entry #6: `INVARIANT_FROM_IMPLEMENTATION` documenting the v1 brief's framing error.
- (Already saved) Project memory: `unproven_is_theater.md` doctrine.
- (Already saved) Project memory: `bicameral_product_positioning.md` capturing playbook key claims as research substrate.

This brief introduces no new architectural invariant. The earlier "bicameral does not fetch source content" claim is **explicitly retired** here; the repo simply has not implemented source fetching yet, and design intent for v1+ is not pinned.

---

## CI Commands

None. Research is documentation; validation is operator read-through and audit pressure-test. No tests; no schema changes.

---

_Research complete. Findings are advisory — implementation decisions remain with the Governor. Followup `/qor-plan` should explicitly engage operator on the #136 dependency before drafting._
