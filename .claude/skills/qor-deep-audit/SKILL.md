---
name: qor-deep-audit
type: workflow-bundle
description: >-
  Full-cycle production gap audit. Decomposes into qor-deep-audit-recon (Phases 1-3, ends at RESEARCH_BRIEF.md) and qor-deep-audit-remediate (Phases 4-6, consumes the brief). Use when: (1) Preparing for GA/release, (2) Absorbing external codebases, (3) Investigating incomplete or hallucinated features, (4) Comprehensive tech debt inventory.
autonomy: autonomous
tone_aware: false
phases: [recon, synthesis, verification, plan, implement, validate]
checkpoints:
  - after-recon
  - after-synthesis
  - after-verification
  - after-plan
  - after-implement
budget:
  max_phases: 6
  abort_on_token_threshold: 0.7
  max_iterations_per_phase: 3
decomposes_into:
  - qor-deep-audit-recon
  - qor-deep-audit-remediate
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/meta/qor-deep-audit
phase: meta
gate_reads: ""
gate_writes: ""
---

# /qor-deep-audit — Full-Cycle Production Gap Audit (Workflow Bundle)

<skill>
  <trigger>/qor-deep-audit</trigger>
  <phase>meta (workflow bundle)</phase>
  <persona>Governor</persona>
  <output>RESEARCH_BRIEF.md + remediation plan + implementation + validation</output>
</skill>

## Decomposition (recommended)

This bundle is **large** (6 phases, multi-iteration). For controlled execution, prefer invoking the two sub-bundles separately with a hard checkpoint between them:

1. **`/qor-deep-audit-recon`** — Phases 1-3 (recon + synthesis + verification rounds). Output: `RESEARCH_BRIEF.md`. Operator reviews before proceeding.
2. **`/qor-deep-audit-remediate`** — Phases 4-6 (plan + implement + validate). Consumes `RESEARCH_BRIEF.md` as input.

Invoking `/qor-deep-audit` directly runs both sub-bundles in sequence with checkpoints; use this only when you've reviewed scope upfront and trust the recon→remediate transition.

## Token-efficiency

Per `qor/references/doctrine-token-efficiency.md`, this bundle:
- Delegates each investigation vector to a subagent (offloads token cost from main context)
- References findings by file:line, never inlines source code
- Summarizes at each checkpoint in <100 words
- Aborts and writes resume marker when context approaches `budget.abort_on_token_threshold`

## Bundle protocol

This bundle is governed by `qor/gates/workflow-bundles.md`. Read it before invoking.

### Phase 1: RECONNAISSANCE (parallel exploration via subagents)

Launch parallel investigation agents — one per subsystem. Each operates in its own context window (subagent), returns findings to main context as a structured summary.

| Agent | Scope | Output |
|---|---|---|
| Backend | Code inventory, IPC routes, tests, dead code | findings with file:line |
| Frontend | Components, routes, IPC calls, accessibility | findings with file:line |
| Config/Docs | Tauri config, governance docs, build config | doc/code mismatches |
| SDK/IPC | SDK methods, route alignment, type safety | orphaned routes, auth gaps |
| External deps | Pending integrations | absorption surface map |

**CHECKPOINT — after-recon**: Summarize raw findings count + risk distribution. Prompt: continue / branch to `/qor-organize` if structural issues dominate / stop.

### Phase 2: SYNTHESIS (gap categorization)

Compile findings into `RESEARCH_BRIEF.md`:
- Executive Summary (gap count, risk grade, top-5 critical)
- Gap Categories (backend, frontend, SDK, security, config, docs, integrations)
- Gap IDs (`GAP-{CATEGORY}-{NN}`)
- Summary Matrix (id, category, severity, effort, blocks-GA)
- Sprint Plan (ordered remediation)

**CHECKPOINT — after-synthesis**: Surface the Summary Matrix. Prompt: continue / scope-down (drop categories) / stop.

### Phase 3: ITERATIVE VERIFICATION (max 3 rounds, per budget)

- **Round 1**: Verify CRITICAL/HIGH gaps with file:line evidence. Mark CONFIRMED / REFUTED / PARTIALLY CONFIRMED.
- **Round 2**: Architecture-level analysis of largest gaps; security audit; hallucination scan.
- **Round 3**: Blast radius per remediation; identify hidden dependencies; validate sprint ordering.

After each round, update `RESEARCH_BRIEF.md`. Re-score severity/effort.

**CHECKPOINT — after-verification**: Final brief ready. Prompt: proceed to remediate / stop here (recon-only).

If the operator stopped after recon, the recon sub-bundle is complete. The remaining phases are the remediate sub-bundle.

### Phase 4: REMEDIATION PLANNING — `/qor-plan`

Generate `plan-remediation-*.md`. Order by GA-blockers → severity DESC → effort ASC. Group into 3-5 day sprints. Mark prerequisites.

Per delegation-table: this phase invokes `/qor-plan` explicitly; do NOT inline planning logic.

**CHECKPOINT — after-plan**: Plan ready, audit pending. Prompt: continue to `/qor-audit` of the plan / revise plan / stop.

### Phase 5: IMPLEMENTATION — `/qor-implement` (sprint by sprint)

Sprint 1: quick wins + critical fixes (XS items). Sprints 2-N: medium items in dependency order. Final sprint: large items.

Per item: write tests first → implement → `/qor-substantiate` → mark gap RESOLVED in brief.

**CHECKPOINT — after-implement**: All sprints complete. Prompt: continue to validation / extend with another sprint / stop.

### Phase 6: VALIDATION — `/qor-substantiate` + `/qor-debug`

- Build verification (tests pass)
- UI verification (golden path + edge cases)
- Security review (re-check remediated security gaps)
- Gap closure verification (each `GAP-*` actually resolved, not just claimed)
- Regression check

## Constraints

- **NEVER** skip checkpoints — bundles MUST surface progress between phases
- **NEVER** skip verification rounds in Phase 3 — the whole point is catching incorrect claims
- **NEVER** claim a gap resolved without evidence (file:line + passing test)
- **ALWAYS** delegate to constituent skills by name (per `qor/gates/delegation-table.md`)
- **ALWAYS** abort gracefully on budget breach with a resume marker (per `qor/gates/workflow-bundles.md`)
- **ALWAYS** update `RESEARCH_BRIEF.md` as the single source of truth
- **MINIMUM** 3 verification rounds before implementation begins
- **TDD**: tests before implementation per fix

## Anti-Patterns to Avoid

| Anti-Pattern | Prevention |
|---|---|
| Claim-without-evidence | Read the files, count manually |
| Grep-only analysis | Read full files; alternative macros |
| Underestimating blast radius | Always check who consumes the thing you're changing |
| Two-way when it's three-way | Check all version sources |
| Assuming bundler exists | Check for build config FIRST |
| Feature-gate when you should substitute | One runtime per concern |
| Continuing past context budget | Honor `budget.abort_on_token_threshold` |

## Success Criteria

- [ ] All subsystems explored
- [ ] `RESEARCH_BRIEF.md` created with categorized, ID'd gaps
- [ ] 3+ verification rounds completed
- [ ] Sprint plan executed (or scoped intentionally)
- [ ] Every `GAP-*` either RESOLVED with evidence or DEFERRED with rationale
- [ ] All checkpoints surfaced (not silently skipped)
- [ ] No budget breaches without resume marker
