---
name: qor-deep-audit-recon
type: workflow-bundle
description: >-
  Reconnaissance + synthesis + verification rounds for a production gap audit. Output is a verified RESEARCH_BRIEF.md ready for remediation planning. Use as the first half of /qor-deep-audit when you want a hard checkpoint between investigation and action.
autonomy: autonomous
tone_aware: false
phases: [recon, synthesis, verification]
checkpoints: [after-recon, after-synthesis, after-verification]
budget:
  max_phases: 3
  abort_on_token_threshold: 0.7
  max_iterations_per_phase: 3
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/meta/qor-deep-audit-recon
phase: meta
gate_reads: ""
gate_writes: ""
---

# /qor-deep-audit-recon — Recon + Synthesis + Verification

<skill>
  <trigger>/qor-deep-audit-recon</trigger>
  <phase>meta (workflow bundle, recon half)</phase>
  <persona>Governor</persona>
  <output>RESEARCH_BRIEF.md (verified gap inventory, ready for remediation)</output>
</skill>

## Purpose

The investigation half of `/qor-deep-audit`. Identifies and verifies production gaps without proposing or executing fixes. Output is a single `RESEARCH_BRIEF.md` that the operator reviews before deciding to invoke `/qor-deep-audit-remediate`.

## When to use

- You want to scope a production gap without committing to remediation
- You need an external review of the gap inventory before action
- You want to absorb an external codebase and understand its true state first

## Phases

### Phase 1: RECONNAISSANCE

Launch parallel subagent investigations per the matrix in `qor/skills/meta/qor-deep-audit/SKILL.md` §Phase 1. Each subagent operates in its own context window; main context receives a structured summary with file:line citations.

**CHECKPOINT — after-recon**: Summarize raw findings count + risk distribution. Prompt: continue / branch (`/qor-organize` if structural issues dominate) / stop with resume marker.

### Phase 2: SYNTHESIS

Compile findings into `RESEARCH_BRIEF.md` per the schema in `qor-deep-audit` §Phase 2 (Executive Summary, Categories, Gap IDs, Summary Matrix, Sprint Plan).

**CHECKPOINT — after-synthesis**: Surface the Summary Matrix. Prompt: continue / scope-down / stop.

### Phase 3: VERIFICATION (max 3 rounds, per budget)

Three rounds of progressive verification per `qor-deep-audit` §Phase 3. After each round update `RESEARCH_BRIEF.md` with CONFIRMED/REFUTED/PARTIAL marks.

**CHECKPOINT — after-verification**: Final brief ready. Prompt: invoke `/qor-deep-audit-remediate` now / hand off the brief / stop.

## Constraints

- **NEVER** propose or execute fixes — that's `/qor-deep-audit-remediate`
- **NEVER** skip verification rounds
- **ALWAYS** delegate investigation to subagents (preserves main context budget)
- **ALWAYS** cite file:line for every claim
- **ALWAYS** surface checkpoints
- **MINIMUM** 3 verification rounds before completion

## Success Criteria

- [ ] All subsystems explored via subagents
- [ ] `RESEARCH_BRIEF.md` exists with categorized, ID'd, verified gaps
- [ ] All 3 checkpoints surfaced
- [ ] Each gap has CONFIRMED/REFUTED/PARTIAL status
- [ ] No budget breaches without resume marker

## Delegation

Per `qor/gates/delegation-table.md`:

- **Brief complete + ready to act** → `/qor-deep-audit-remediate`
- **Structural issues dominate findings** → `/qor-organize` first, then re-recon
- **Out-of-scope findings surface** → halt and escalate to user; do NOT silently scope-creep

See `qor/gates/workflow-bundles.md` for bundle protocol details.
