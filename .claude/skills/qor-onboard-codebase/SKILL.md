---
name: qor-onboard-codebase
type: workflow-bundle
description: >-
  Absorb an external codebase into governed scope. Investigates structure, normalizes layout, audits architecture, produces an integration plan. Use when (1) inheriting an unfamiliar project, (2) merging an external repo into a governed monorepo, (3) onboarding a contractor's codebase.
phases: [research, organize, audit, plan]
tone_aware: false
checkpoints: [after-research, after-organize, after-audit]
budget:
  max_phases: 4
  abort_on_token_threshold: 0.7
  max_iterations_per_phase: 1
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/meta/qor-onboard-codebase
phase: meta
gate_reads: ""
gate_writes: ""
---

# /qor-onboard-codebase — External Codebase Onboarding (Workflow Bundle)

<skill>
  <trigger>/qor-onboard-codebase</trigger>
  <phase>meta (workflow bundle)</phase>
  <persona>Governor</persona>
  <output>RESEARCH_BRIEF.md + reorganization proposal + audit report + integration plan</output>
</skill>

## Purpose

Take an unfamiliar codebase from "we just got this" to "we have a governed plan to integrate it". Four phases, three operator checkpoints. Each phase delegates to its single-purpose skill — bundle does no analysis itself, only orchestration.

## When to use

- Inheriting an external repo (acquisition, contractor handoff, open-source absorption)
- Merging a sibling project into a governed monorepo
- Evaluating an unknown codebase before deciding whether to integrate it

## Bundle protocol

Governed by `qor/gates/workflow-bundles.md`. Honors `qor/references/doctrine-token-efficiency.md` — investigation phase delegates to subagents.

### Phase 1 — Research (`/qor-research`)

Investigate the foreign codebase. Per `/qor-research`:
- Surface the project's actual conventions (test runner, build system, doc layout)
- Map module boundaries
- Identify external dependencies + integration surfaces
- Note any structural concerns that should later route to `/qor-organize` (per delegation-table)

Output: `RESEARCH_BRIEF.md` (or `docs/onboard-<name>-research.md`) with file:line citations.

**CHECKPOINT — after-research**: Summarize total surface area + risk items + open questions in <100 words. Prompt: continue / scope-down (drop subsystems) / stop with resume marker.

### Phase 2 — Organize (`/qor-organize`)

Normalize the codebase's layout to project-host conventions. Per `/qor-organize`:
- Detect project archetype
- Propose directory restructuring matching host conventions
- Identify governance directories that must be excluded (`.agent/`, `.claude/`, `.qor/`, `.failsafe/`)
- Document the proposal as `FILE_INDEX.md` audit trail

Bundle does NOT execute reorganization automatically. Operator approves the proposal.

**CHECKPOINT — after-organize**: Surface proposed structure diff. Prompt: apply / revise / skip-organize-step / stop.

### Phase 3 — Audit (`/qor-audit`)

Adversarial audit of the codebase against host governance standards. Per `/qor-audit`:
- Security pass (placeholder auth, hardcoded creds, bypassed checks)
- Section 4 Razor pass (function/file size, nesting, ternaries) — VETO routes to `/qor-refactor`
- Macro-level pass (cyclic deps, mixed domains, layering reversal) — VETO routes to `/qor-organize`
- Orphan detection — VETO routes to `/qor-organize`
- Dependency audit (hallucinated/unjustified deps)

Audit verdict (PASS or VETO with mandated remediation skills) feeds Phase 4.

**CHECKPOINT — after-audit**: Surface verdict + violation count + mandated remediation skills. Prompt: continue to plan / abandon onboard / branch to remediation directly.

### Phase 4 — Plan (`/qor-plan`)

Integration plan. Per `/qor-plan`:
- If audit PASS: plan integration steps (host wiring, test harness alignment, dep-tree merge)
- If audit VETO: plan addresses each violation by invoking the mandated remediation skill (`/qor-refactor`, `/qor-organize`, etc.) BEFORE integration. No bundle re-implements those skills' logic.

Output: `plan-onboard-<name>.md` with phased integration sprints.

## Constraints

- **NEVER** skip checkpoints — operator must surface before each phase
- **NEVER** inline analysis the constituent skill owns — delegate by name
- **NEVER** auto-execute the organize proposal (Phase 2) — operator approves
- **ALWAYS** delegate to constituent skills via their `/qor-*` triggers (per `qor/gates/delegation-table.md`)
- **ALWAYS** abort gracefully on budget breach with resume marker
- **ALWAYS** preserve external repo's existing governance (don't overwrite their `.failsafe/` etc.)

## Anti-Patterns

| Anti-Pattern | Prevention |
|---|---|
| Bundle does its own audit | Always invoke `/qor-audit`; never inline |
| Skip organize when refactor would be the same | Different scopes — organize=topology, refactor=logic |
| Apply reorganization without operator approval | Phase 2 emits proposal; checkpoint blocks auto-apply |
| Merge external repo without audit verdict | Phase 3 is mandatory; PASS or remediated VETO required |

## Success Criteria

- [ ] All 3 checkpoints surfaced
- [ ] `RESEARCH_BRIEF.md` (or onboard-research) authored with citations
- [ ] Reorganization proposal authored + operator decision logged
- [ ] Audit verdict captured with mandated remediation skills named
- [ ] Integration plan authored that delegates to mandated remediation BEFORE integration steps
- [ ] No bundle phase reinvents a constituent skill's process

## Delegation

Per `qor/gates/delegation-table.md`:

- **Audit PASS** → continue to integration plan
- **Audit VETO Razor** → `/qor-refactor` (plan must call this before integration)
- **Audit VETO Orphan/Macro** → `/qor-organize` (plan must call this before integration)
- **Repeat onboard failure across attempts** → `/qor-remediate` (process-level concern)

See `qor/gates/workflow-bundles.md` for bundle protocol.
