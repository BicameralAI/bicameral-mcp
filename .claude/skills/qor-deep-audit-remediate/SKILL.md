---
name: qor-deep-audit-remediate
type: workflow-bundle
description: >-
  Plan + implement + validate the gaps catalogued in a RESEARCH_BRIEF.md (typically produced by /qor-deep-audit-recon). Sprint-by-sprint execution with per-sprint checkpoints. Use after recon when the gap inventory is verified and you are ready to act.
autonomy: autonomous
tone_aware: false
phases: [plan, implement, validate]
checkpoints: [after-plan, after-each-sprint, after-implement]
budget:
  max_phases: 3
  abort_on_token_threshold: 0.7
  max_iterations_per_phase: 1
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/meta/qor-deep-audit-remediate
phase: meta
gate_reads: ""
gate_writes: ""
---

# /qor-deep-audit-remediate — Plan + Implement + Validate

<skill>
  <trigger>/qor-deep-audit-remediate</trigger>
  <phase>meta (workflow bundle, remediate half)</phase>
  <persona>Governor</persona>
  <output>plan-remediation-*.md + per-sprint commits + validated gap closure</output>
</skill>

## Purpose

The action half of `/qor-deep-audit`. Consumes a `RESEARCH_BRIEF.md` (from `/qor-deep-audit-recon`) and executes remediation sprint-by-sprint. Each sprint checkpoints with the operator before the next begins.

## When to use

- A `RESEARCH_BRIEF.md` exists and is verified
- Operator has decided to act on the catalogued gaps
- Sprint capacity is available (this bundle does not silently extend)

## Required input

`RESEARCH_BRIEF.md` (path passed as `--brief` arg, or default `docs/RESEARCH_BRIEF.md`). Bundle ABORTS if absent — recon phase must complete first.

## Phases

### Phase 4: REMEDIATION PLANNING — `/qor-plan`

Generate `plan-remediation-*.md`. Order: GA-blockers → severity DESC → effort ASC. Group into 3-5 day sprints. Mark prerequisites.

**Per delegation-table**: invoke `/qor-plan` explicitly; do NOT inline planning.

**CHECKPOINT — after-plan**: Plan ready, gate audit pending. Prompt: continue to `/qor-audit` of the plan / revise / stop.

### Phase 5: IMPLEMENTATION — `/qor-implement` (sprint by sprint)

For each sprint:
1. `/qor-implement` the sprint's items (TDD: tests first)
2. `/qor-substantiate` the sprint
3. Mark each gap RESOLVED in `RESEARCH_BRIEF.md` with file:line evidence

**CHECKPOINT — after-each-sprint**: Surface what was completed + what remains. Prompt: continue to next sprint / pause / stop.

### Phase 6: VALIDATION — `/qor-substantiate` + `/qor-debug`

Per `qor-deep-audit` §Phase 6: build verification, UI verification, security review, gap closure check, regression check.

**CHECKPOINT — after-implement**: Validation report ready. Prompt: ship / extend with more sprints / stop.

## Constraints

- **NEVER** start without a verified `RESEARCH_BRIEF.md`
- **NEVER** silently extend beyond planned sprints
- **NEVER** mark a gap RESOLVED without file:line evidence + passing test
- **ALWAYS** delegate to `/qor-plan`, `/qor-implement`, `/qor-substantiate`, `/qor-debug` by name
- **ALWAYS** checkpoint after every sprint
- **ALWAYS** abort with resume marker on budget breach
- **TDD**: tests before implementation per fix

## Success Criteria

- [ ] Remediation plan authored by `/qor-plan`
- [ ] Plan passed `/qor-audit`
- [ ] Each sprint executed + substantiated + checkpointed
- [ ] Every `GAP-*` either RESOLVED with evidence or DEFERRED with rationale
- [ ] Validation phase complete
- [ ] No skipped checkpoints

## Delegation

Per `qor/gates/delegation-table.md`:

- **Validation FAIL with single defect** → `/qor-implement` with scoped fix
- **Repeat sprint failures (3+)** → `/qor-remediate` (process-level concern)
- **Regression detected during sprint** → `/qor-debug` (root cause); pause sprint until resolved
- **Bundle complete + PASS** → `/qor-repo-release` if release-gating, else session complete

See `qor/gates/workflow-bundles.md` for bundle protocol details.
