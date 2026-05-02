---
name: qor-process-review-cycle
type: workflow-bundle
description: >-
  Periodic process health check: sweep the Process Shadow Genome, propose remediations for accumulated events, audit the proposed remediation. Use weekly/monthly or after a long stretch of work to keep process drift in check.
phases: [shadow-sweep, remediate, audit]
tone_aware: false
checkpoints: [after-shadow-sweep, after-remediate]
budget:
  max_phases: 3
  abort_on_token_threshold: 0.7
  max_iterations_per_phase: 1
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ-Labs-LLC/Qor-logic
    path: qor/skills/governance/qor-process-review-cycle
phase: meta
gate_reads: ""
gate_writes: ""
---

# /qor-process-review-cycle — Periodic Process Health Check (Workflow Bundle)

<skill>
  <trigger>/qor-process-review-cycle</trigger>
  <phase>meta (workflow bundle, governance)</phase>
  <persona>Governor</persona>
  <output>process review summary + addressed events + audit verdict on remediation plan</output>
</skill>

## Purpose

Process drift accumulates: gate overrides, capability shortfalls, repeated regressions. Without periodic review, the Process Shadow Genome grows but its events never get acted on. This bundle is the periodic act-on-it: sweep → propose → audit.

## When to use

- Scheduled (weekly cron, monthly review)
- After a long sprint when override events have accumulated
- Before a release when process trustworthiness matters
- After an incident as part of postmortem

NOT for code defects — those route to `/qor-debug`. This is for PROCESS issues (the way we work, not the code we write).

## Bundle protocol

Governed by `qor/gates/workflow-bundles.md`.

### Phase 1 — Shadow Sweep (`qor/scripts/check_shadow_threshold.py`)

Run the threshold checker. This applies stale expiry (sev 1-2 only after 90 days), emits aged-high-severity self-escalations (idempotent), and computes total unaddressed severity.

If the threshold is breached, `.qor/remediate-pending` exists with aggregated event ids. If not, the sweep summary still shows recent activity.

```bash
python -m qor.scripts.check_shadow_threshold
```

**CHECKPOINT — after-shadow-sweep**: Surface (a) total unaddressed severity, (b) breached/under threshold, (c) event-type distribution, (d) whether `.qor/remediate-pending` exists. Prompt: continue to remediate / scope-down (focus on subset) / stop with summary.

### Phase 2 — Remediate (`/qor-remediate`)

Per `/qor-remediate`:
- Read `docs/PROCESS_SHADOW_GENOME.md`
- Cluster events by type, skill, session
- Propose process changes (skill / agent / gate / doctrine adjustments) — NOT code changes
- Mark events `addressed=true` with `addressed_reason="remediated"` for those the proposal addresses (use `qor/scripts/create_shadow_issue.py --mark-resolved --events <ids>` for direct resolution without GitHub issue)
- Write a remediate gate artifact

**CHECKPOINT — after-remediate**: Surface remediation proposal + addressed event count. Prompt: continue to audit-of-proposal / revise / stop.

### Phase 3 — Audit the Remediation (`/qor-audit`)

Adversarial audit of the remediation proposal. Process changes deserve the same scrutiny as code plans:
- Are the proposed changes actually addressing the root cause?
- Do they pass Section 4 razor (no over-engineering)?
- Are they orthogonal to existing governance, or are they reinventing existing skills?
- Is there a ghost handler — a proposed change with no implementation path?

Audit verdict is binding. PASS → operator can implement. VETO → revise per audit's mandated next actions.

## Constraints

- **NEVER** mark events addressed without a paired remediation
- **NEVER** propose code changes — that's `/qor-debug`'s domain (delegation-table)
- **NEVER** skip the audit phase — process changes need adversarial review too
- **ALWAYS** cite shadow event ids in the remediation proposal
- **ALWAYS** delegate to constituent skills (`check_shadow_threshold.py`, `/qor-remediate`, `/qor-audit`) by name

## Success Criteria

- [ ] Shadow sweep completed; threshold status known
- [ ] Both checkpoints surfaced
- [ ] Remediation proposal authored citing event ids
- [ ] Events addressed in proposal flipped via `--mark-resolved` (or via issue creation if external tracking is preferred)
- [ ] Audit verdict on proposal recorded
- [ ] No process change made silently

## Delegation

Per `qor/gates/delegation-table.md`:

- **Threshold not breached + no events to act on** → bundle exits cleanly; suggest fresh schedule
- **Audit PASS on remediation** → operator implements (out of bundle scope)
- **Audit VETO on remediation** → revise proposal; do NOT silently apply
- **Repeat process review failures** (3+ cycles producing same kind of override) → escalate to architectural review, not another remediation cycle

See `qor/gates/workflow-bundles.md` for bundle protocol.

## Scheduling

Operator-driven. Recommended cadence:
- Weekly: lightweight sweep (Phase 1 + checkpoint, often early-exit)
- Monthly: full cycle (all 3 phases)
- After incident: full cycle as part of postmortem

Document scheduling per project in your repo's CI/cron config; bundle does not self-schedule.
