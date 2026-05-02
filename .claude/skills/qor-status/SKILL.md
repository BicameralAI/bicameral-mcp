---
name: qor-status
description: >-
  /qor-status - Lifecycle Diagnostic
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/memory/qor-status
phase: memory
tone_aware: true
gate_reads: ""
gate_writes: ""
---
# /qor-status - Quick Lifecycle Check

<!-- qor:tone-aware-section -->
## Output rendering by tone

This skill reads `.qorlogic/config.json` for the `tone` field at runtime (or honors an active `/qor-tone` session override). Resolution order is session override > config > `technical` default (canonical lookup described in `qor/references/doctrine-communication-tiers.md` under "How skills read the tone value").

### technical

Render with full jargon. Include SG IDs where load-bearing (e.g., "SG-038: plan self-consistency violated at entry #54"), OWASP tags where applicable, explicit `file:line` citations, Merkle chain hashes, and exact verdict labels. Include the short Merkle seal prefix (e.g., `Merkle seal: 68772fd3...`). Tables acceptable.

### standard

Complete sentences, no SG IDs unless the operator asked for one. Use governance terms (verdict, gate, chain) with a one-line inline gloss on first use. Keep file paths and command names but drop hash values from the body. Tables acceptable when they aid clarity.

### plain

No technical jargon. No governance tags or pattern IDs. No long identifier strings in the body. Short declarative sentences: "Your workspace is set up. Everything looks ready." Explicit next-step commands only. Tables discouraged; use bullet points. If a long identifier is essential, include it as a footnote.
<!-- /qor:tone-aware-section -->

<skill>
  <trigger>/qor-status</trigger>
  <phase>ANY</phase>
  <output>Compact status with stage and next action</output>
</skill>

## Purpose

Quick, low-token diagnostic of project health. Read-minimal by design.

## Execution (Decision Tree)

### Step 1: Existence Checks Only

```
Glob: docs/META_LEDGER.md     -> has_ledger
Glob: docs/ARCHITECTURE_PLAN.md -> has_plan
Glob: .agent/staging/AUDIT_REPORT.md -> has_audit
```

**Do NOT read file contents yet.**

### Step 2: Determine State

```
IF !has_ledger:
  STATE = "UNINITIALIZED"
  NEXT = "/qor-bootstrap (first-time workspace setup only)"
  DONE (output immediately)

IF !has_plan:
  STATE = "ALIGN/ENCODE"
  NEXT = "Create ARCHITECTURE_PLAN.md OR use /qor-plan for new feature"
  DONE

IF !has_audit:
  # Only now read first 15 lines for Risk Grade
  Read: docs/ARCHITECTURE_PLAN.md (limit: 15)
  Extract: "Risk Grade: L[1-3]"

  IF L2 or L3:
    STATE = "GATED"
    NEXT = "/qor-audit"
  ELSE:
    STATE = "READY"
    NEXT = "/qor-implement"
  DONE

IF has_audit:
  # Read first 10 lines for verdict
  Read: .agent/staging/AUDIT_REPORT.md (limit: 10)

  IF contains "PASS":
    STATE = "IMPLEMENTING"
    NEXT = "Continue work, /qor-plan for new feature, or /qor-substantiate when done"
  ELSE IF contains "VETO":
    STATE = "BLOCKED"
    NEXT = "Address audit findings, re-run /qor-audit"
  DONE
```

### Important: New Feature vs New Workspace

| Scenario | Command | Description |
|----------|---------|-------------|
| **New workspace** (no META_LEDGER) | `/qor-bootstrap` | Initialize DNA, CONCEPT, ARCHITECTURE_PLAN, META_LEDGER |
| **New feature** (workspace exists) | `/qor-plan` | Create plan-*.md with phases, affected files, tests |
| **Resume work** (audit exists) | Continue or `/qor-status` | Check current state and next action |

**Note**: `/qor-bootstrap` is for **workspace initialization only**. For planning new features in an existing workspace, use `/qor-plan`.

### Step 3: Chain Spot-Check (Optional)

Only if user requests integrity check:
```
Read: docs/META_LEDGER.md (last 30 lines only via offset)
Check: Last entry has SHA256 hash format
Report: "Chain: OK" or "Chain: Verify with /qor-validate"
```

Full verification deferred to `/qor-validate`.

### Step 4: Output

```markdown
## Status: [STATE]

| Check | Result |
|-------|--------|
| Ledger | [exists/missing] |
| Plan | [exists/missing] |
| Audit | [PASS/VETO/pending] |
| Chain | [OK/unverified] |

**Next**: [NEXT]
```

## Token Budget

- Skill load: ~1.5KB
- Max additional reads: <3KB
- Target total: <5KB context impact

## Execution Protocol

### Step 1: Existence Checks

Check for key governance files without reading content:

```
Glob: docs/META_LEDGER.md
Glob: docs/ARCHITECTURE_PLAN.md
Glob: .agent/staging/AUDIT_REPORT.md
```

### Step 2: Determine State

Apply decision tree (see above) to determine STATE and NEXT action.

### Step 3: Output Status

Report compact status table with state, checks, and next action.

## Constraints

- **NEVER** load persona files (identity is implicit)
- **NEVER** read entire files when partial suffices
- **NEVER** enumerate src/**/*
- **ALWAYS** use existence checks before content reads
- **ALWAYS** stop at first determination (short-circuit)

## Success Criteria

Status check succeeds when:

- [ ] All existence checks performed
- [ ] State correctly determined from decision tree
- [ ] Next action identified and reported
- [ ] Total context impact under 5KB

## Integration with S.H.I.E.L.D.

This skill implements:

- **Lifecycle Diagnostic**: Quick state assessment for any phase
- **Low-Token Design**: Minimal reads, existence checks first
- **Decision Tree Routing**: Directs to correct next skill based on state
- **Governor Persona**: Lightweight oversight without full audit overhead
