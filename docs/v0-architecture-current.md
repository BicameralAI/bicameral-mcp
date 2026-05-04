# v0 Architecture — current state

**Audience**: contributors, reviewers, and anyone who needs to know what bicameral-mcp's v0 surface actually does on `dev` HEAD today.

**Source-of-truth contract**: this document is derived from code. When code changes, this doc must change in lockstep — same discipline as DEV_CYCLE.md §9 ("tool changes require skill changes"). Numeric claims (node/edge/flow counts, emitted event names) are checkable against the cited files; if you find drift, file an issue with the `docs-only` surface label.

**What this doc supersedes**: an earlier `v0 Architecture` page in Notion (mirrored locally at `.failsafe/governance/v0 Architecture 3512a51619c481ac97fcd66251c094d9.md`). The Notion page captured original design intent before several material decisions were made (event substrate moved to JSONL-in-git; `SPEC.bql` deferred indefinitely; CodeGenome v1 schema landed early). Where the Notion page disagrees with this doc, this doc wins.

---

## 1. Knowledge graph schema

Canonical source: `ledger/schema.py`.

**v0 core nodes** (the ones the architecture is about):

| Node table | Purpose |
|---|---|
| `input_span` | Source text — transcript, document, slack thread, manual entry |
| `decision` | Extracted decision; carries status (derived), signoff, governance metadata |
| `code_region` | A pinned region in source — `(file_path, start_line, end_line, content_hash)` |
| `compliance_check` | Verdict node: `compliant` / `drifted` / `not_relevant` for a `(decision, code_region, commit)` triple |
| `code_subject` | Stable identity for a code unit across renames/moves |
| `source_cursor` | Per-source ingest progress marker |

**v0 core edges**:

| Edge | In → Out |
|---|---|
| `yields` | `input_span` → `decision` |
| `binds_to` | `decision` → `code_region` |
| `supersedes` | `decision` → `decision` |
| `context_for` | `input_span` → `decision` |
| `about` | `decision` → `code_subject` |

**Operational tables** (necessary infrastructure, not part of the knowledge graph proper): `symbol`, `vocab_cache`, `ledger_sync`, `graph_proposal`, `schema_meta`. These support indexing, caching, sync state, and migration tracking.

**v1 CodeGenome schema** (intentionally held back from production releases per #165, but already present in `ledger/schema.py` on `dev`): `subject_identity`, `subject_version`, edges `has_identity`, `has_version`, `identity_supersedes`, `locates`, `depends_on`. Treat these as v1; they don't load-bear v0 behavior.

**Inserts are idempotent** via content-addressed keys. **First-write-wins** is invariant.

## 2. Status state machine

`DecisionStatus` is **never stored** — re-derived on every read from graph state.

Canonical type: `contracts.py` (see `DecisionStatus = Literal["reflected", "drifted", "pending", "ungrounded"]`).

| Status | Condition |
|---|---|
| `ungrounded` | No `code_region` bound |
| `drifted` | At least one bound region has a `drifted` verdict |
| `pending` | At least one bound region lacks a cached verdict |
| `reflected` | All bound regions have `compliant` verdicts |

**Signoff is orthogonal** — a separate axis. Signoff values include `proposed`, `ratified`, `rejected`, `collision_pending`, `context_pending`, `superseded`. Ratifying a decision does not change its compliance status; the two axes are independent.

`superseded` is a **signoff state**, not a status. Once frozen, a decision can never transition out of `superseded` — that is invariant.

**Ephemeral isolation**: compliance checks created on unmerged branches carry `is_ephemeral: true` and are excluded from status projection on `main` until promoted. Promotion happens via `resolve_compliance` once the branch lands.

## 3. Protocol flows (MCP tools)

23 tools exposed by `server.py`. The v0 architecture's "core" subset:

**Decision lifecycle**:
- `bicameral.ingest` — normalize decisions, write ledger, sync HEAD, surface pending compliance checks
- `bicameral.bind` — resolve symbol, write `code_region` + `binds_to` edge
- `bicameral.preflight` — mandatory HEAD sync, query drift matches and pending collisions, gate agent output (zero output if not fired)
- `bicameral.ratify` — human sign-off; re-derives status but leaves compliance unchanged
- `bicameral.resolve_collision` — supersede / keep_both / link_parent; clears `collision_pending` signoff
- `bicameral.resolve_compliance` — verdict writer for a single region (`compliant` / `drifted` / `not_relevant`)
- `bicameral.link_commit` — sync a commit's effects into the ledger (called by post-commit hook)
- `bicameral.reset` — destructive: wipe and replay (emergency only)

**Telemetry / governance / observability** (the "FAT" the original v0 page acknowledged but didn't enumerate):
- `bicameral.skill_begin` / `bicameral.skill_end` — skill execution lifecycle
- `bicameral.feedback` — agent feedback events
- `bicameral.usage_summary` — aggregate metrics
- `bicameral.history` / `bicameral.dashboard` — read-only views
- `bicameral.judge_gaps` — surface ingestion gaps for caller-LLM evaluation
- `bicameral.update` — install upgrade
- `bicameral.list_unclassified_decisions` / `bicameral.set_decision_level` / `bicameral.evaluate_governance` — decision-tier classification surface

**Code locator** (deterministic primitives, no LLM in path):
- `validate_symbols`, `search_code`, `get_neighbors`, `extract_symbols`

There is **no internal/external split** at the MCP boundary — every tool is callable from any client. Discipline lives in the skill layer (`skills/*/SKILL.md`), not in the tool surface.

## 4. MCP server architecture

Each tool call constructs a `BicameralContext` (`context.py`) with a pinned HEAD SHA. Handlers cannot observe a moving HEAD within a single call.

- **Code Graph** — SQLite + tree-sitter (`code_locator/`). Symbol resolution, file→region lookup, BM25 + RRF fusion retrieval, 1-hop import-edge graph traversal. No LLM in the indexing or retrieval path.
- **Ledger** — SurrealDB embedded (`ledger/`). All persistent reads and writes. Schema migrations registered in `SCHEMA_COMPATIBILITY` map; `_migrate_vN_to_vN+1` chain validated against persistent seed data in CI.
- **Event log** — JSONL files at `.bicameral/events/{git-email}/` (`events/`). See §5.

## 5. Event-sourced ledger

All v0 ledger writes that affect cross-team-shareable state emit a JSONL event line. Events live in `.bicameral/events/{git-email}/<date>.jsonl`, are committed to git, and form a CRDT-mergeable stream across team members.

**This differs from the original Notion v0 page**, which described events as graph nodes (`LedgerEvent` + `event_targets` edge). The implementation chose JSONL-in-git because the spec's stated goal — *"team sync becomes tractable. Event streams from two engineers can be CRDT-merged"* — is only deliverable if the events live in a shared, ordered, version-controlled store. A graph node only inside one engineer's local SurrealDB doesn't team-sync. JSONL-in-git does.

**Events emitted today** (canonical source: `events/team_adapter.py`):

| Event type | Trigger |
|---|---|
| `ingest.completed` | After `ingest` writes a decision |
| `bind_decision.completed` | After `bind` writes a `binds_to` edge |
| `link_commit.completed` | After `link_commit` advances HEAD and re-evaluates regions |
| `decision_ratified.completed` | After `ratify` flips signoff |
| `decision_superseded.completed` | After `resolve_collision` writes a `supersedes` edge |

**Naming convention**: `<domain>.completed`. The original Notion page used `decision_X` past-tense names (e.g. `decision_ingested`); the code emits `<domain>.completed`. The code names are what wire-level integrators must match.

**Known gap (tracked separately)**: `compliance_checked` is named in the Notion page but is **not currently emitted**. When `resolve_compliance` flips a region from `pending` to `reflected` / `drifted`, that transition is not in the team-sync stream. Filed as a follow-up under the team-mode-correctness umbrella; see #178's open-issues survey for context. Until fixed, teammate replays infer compliance state from `link_commit.completed` effects rather than from explicit verdict events.

**Invariant**: the JSONL log is **append-only**. The `events/writer.py` API cannot rewrite or delete; replay is the only way to rebuild state.

## 6. What is core, what is fat

The original Notion page promised *"150 lines of declarative spec (`SPEC.bql`)"* as the kernel and called everything else fat. **`SPEC.bql` was never authored** — it would require a custom DSL + parser + checker that were not engineered. The de-facto spec source-of-truth is:

- **Schema**: `ledger/schema.py`
- **Status logic**: `handlers/preflight.py` (status derivation) + `contracts.py` (type definitions)
- **Tool surface**: `server.py`
- **Event vocabulary**: `events/team_adapter.py`
- **Invariants**: enforced by tests in `tests/`

**v0 invariants** (what the system promises, not where they're written):
1. **First-write-wins** on content-addressed keys — duplicate ingests are no-ops
2. **Status is derived, never stored** — derived on read from graph state
3. **Superseded signoff is frozen** — no transitions out of `superseded`
4. **`not_relevant` prunes** — when a binding is `not_relevant`, it's treated as if it doesn't exist for status purposes
5. **Ephemeral isolation** — branch-scoped compliance is excluded from `main` status projection until promoted
6. **Event log is append-only** — JSONL files in `.bicameral/events/` cannot be rewritten

These invariants are present in the codebase. Each one has at least one test that breaks if the invariant breaks. A future `SPEC.bql` would formalize them; today, the test suite is the formal contract.

**Honest fat list** (present in code, not load-bearing for invariants):
- Sync cache, dashboard notifications, speaker backfill, topic derivation heuristics, source-cursor replay bookkeeping, error message formatting, telemetry instrumentation
- The 11 telemetry/governance tools listed in §3 above

These can be removed without breaking any v0 invariant. They exist because they make the product usable, not because the architecture demands them.

---

## Reconciliation notes (drift from the original Notion v0 page)

| Notion page claim | Reality |
|---|---|
| *"Source of truth: `SPEC.bql` at repo root"* | `SPEC.bql` does not exist. Source of truth is the code (see §6). |
| *"7 node types, 6 edge types"* | 13 tables, 10 edges in `ledger/schema.py`. v0 core is 6+5; the rest is operational infrastructure or v1 CodeGenome. |
| *"8 flows; 5 visible, 3 internal"* | 23 MCP tools, no internal/external split. Discipline lives in skills, not at the tool boundary. |
| *"`LedgerEvent` graph node + `event_targets` edge"* | Events live in JSONL files at `.bicameral/events/{git-email}/`. Choice was deliberate to enable git-committed CRDT-mergeable team sync. |
| *"Events emitted: `decision_ingested`, `decision_bound`, `compliance_checked`, `decision_ratified`, `decision_superseded`"* | Code emits `ingest.completed`, `bind_decision.completed`, `link_commit.completed`, `decision_ratified.completed`, `decision_superseded.completed`. `compliance_checked` is missing — known gap. `link_commit.completed` is extra. Naming convention shifted to `<domain>.completed`. |
| *"150 lines of declarative spec vs ~2,500 lines of Python"* | Schema declarations in `ledger/schema.py` are ~400 lines. Python supporting them is much larger. The "150 lines" promise was tied to `SPEC.bql`, which doesn't exist. |
