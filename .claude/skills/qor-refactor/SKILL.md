---
name: qor-refactor
description: >-
  KISS Refactor and Simplification Pass that flattens logic, deconstructs bloat, and verifies structural integrity. Use when: (1) Code violates Section 4 Simplicity Razor, (2) Functions exceed 40 lines or files exceed 250 lines, (3) Nesting depth exceeds 3 levels, or (4) General code cleanup needed.
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/sdlc/qor-refactor
phase: implement
tone_aware: false
gate_reads: audit
gate_writes: implement
permitted_tools: [Read, Grep, Glob, Bash, Edit, Write]
permitted_subagents: []
model_compatibility: [claude-opus-4-7]
min_model_capability: opus
---
# /qor-refactor - KISS Simplification Pass

<skill>
  <trigger>/qor-refactor</trigger>
  <phase>implement (maintenance)</phase>
  <persona>Specialist</persona>
  <output>Refactored code, updated SYSTEM_STATE.md, ledger entry</output>
</skill>

## Purpose

Mandatory pass to flatten logic, deconstruct bloat, and verify structural integrity. Applies both micro-level (function) and macro-level (file/module) KISS principles.

## Execution Protocol

### Step 0: Gate Check (advisory — Phase 8 wiring)

Verify prior-phase artifact exists and is well-formed before proceeding.

```python
from qor.scripts import gate_chain, session

sid = session.get_or_create()
result = gate_chain.check_prior_artifact("implement", session_id=sid)
if not result.found:
    # Prompt user to override; on confirm:
    gate_chain.emit_gate_override(
        current_phase="implement",
        prior_phase_name="audit",
        reason="user override: audit.json not found",
        session_id=sid,
    )
elif not result.valid:
    gate_chain.emit_gate_override(
        current_phase="implement",
        prior_phase_name="audit",
        reason=f"user override: {result.errors}",
        session_id=sid,
    )
```

Override is permitted (advisory gate) but logged as severity-1 `gate_override` event in the Process Shadow Genome.

**Phase 54 wiring**: when `gate_chain.emit_gate_override` raises `OverrideFrictionRequired`, prompt the operator for a written justification (>=50 chars) and re-call `emit_gate_override` with `justification=<text>`. Per `qor/references/doctrine-ai-rmf.md` §MANAGE-1.1 + `qor/references/doctrine-eu-ai-act.md` Art. 14.

### Step 1: Identity Activation
You are now operating as **The Qor-logic Specialist** in refactoring mode.

### Step 1.a — Capability check (agent-teams parallel mode, Phase 8 wiring)

```python
import qor_platform as qplat
import shadow_process

if qplat.is_available("agent-teams"):
    # Fan out specialist tracks (frontend/backend/infra) in parallel via TeamCreate;
    # synthesize results in this skill.
    mode = "teams"
else:
    state = qplat.current() or {}
    if state.get("detected", {}).get("host") == "claude-code":
        # claude-code host but agent-teams not declared -> log capability_shortfall
        shadow_process.append_event({
            "ts": shadow_process.now_iso(), "skill": "qor-implement", "session_id": sid,
            "event_type": "capability_shortfall", "severity": 2,
            "details": {"capability": "agent-teams"},
            "addressed": False, "issue_url": None, "addressed_ts": None,
            "addressed_reason": None, "source_entry_id": None,
        })
    mode = "sequential"
```

Contract for `teams` mode (reserved for future harness wiring): `TeamCreate(<spec>) -> [{track, deliverable}, ...]`. Skill synthesizes the track outputs into a single artifact.

### Step 2: Environment Scan

```
Glob: [target path]
Read: [each file in scope]
```

Identify violations of Section 4 Simplicity Razor:
- Functions > 40 lines
- Files > 250 lines
- Nesting > 3 levels
- Nested ternaries
- Generic variable names

### Step 3: Scope Determination

**Single-File** (default): One file micro-refactor
**Multi-File**: Directory/module macro-refactor

---

## Single-File Micro-Refactor

### Step 3a: Function Decomposition

For each function exceeding 40 lines, split into cohesive sub-functions.
Reference examples: `references/qor-refactor-examples.md`.

### Step 3b: Logic Flattening

Replace deep nesting with early returns.
Reference examples: `references/qor-refactor-examples.md`.

### Step 3c: Ternary Elimination

Replace nested ternaries with explicit control flow.
Reference examples: `references/qor-refactor-examples.md`.

### Step 3d: Variable Renaming

Audit and replace generic identifiers.
Reference examples: `references/qor-refactor-examples.md`.

### Step 3e: Cleanup

- Remove all `console.log` artifacts
- Remove commented-out code
- Remove unrequested config options
- Remove empty catch blocks
- Remove unused imports

---

## Multi-File Macro-Refactor

### Step 4a: Orphan Detection

```
Read: [entry point - main.tsx, index.ts]
Trace: Import chains to all files in scope
```

Flag any file not reachable from entry point. Template:
`references/qor-refactor-examples.md`.

**For orphans**: Remove or wire into build path

### Step 4b: File Splitting

For files exceeding 250 lines, split into cohesive modules.
Reference example: `references/qor-refactor-examples.md`.

### Step 4c: God Object Elimination

Identify and split "God Objects" (classes/modules doing too much).
Reference example: `references/qor-refactor-examples.md`.

### Step 4d: Dependency Audit

```
Read: package.json
```

For each dependency:
1. Is it actually imported/used?
2. Can vanilla JS/TS replace it in < 10 lines?

Template: `references/qor-refactor-examples.md`.

### Step 4e: Macro-Level Structure Check

Audit module boundaries and architecture flow:

- Verify directories align to domains (no mixed responsibilities).
- Check for cyclic imports between modules; break cycles by extracting shared interfaces.
- Enforce dependency direction (UI -> domain -> data). No reverse imports.
- Consolidate duplicated domain logic into a single module.
- Centralize cross-cutting concerns (logging, auth, config) to avoid scattering.
- Identify config/flags sprawl; consolidate or document ownership.

If any violation is found, refactor to restore clear boundaries before proceeding.

---

## Post-Refactor Verification

### Step 5: Compliance Check

Template: `references/qor-refactor-examples.md`.

All must pass before completion.

### Step 6: Update System State

```
Edit: docs/SYSTEM_STATE.md
```

Template: `references/qor-refactor-examples.md`.

### Step 7: Update Ledger

```
Edit: docs/META_LEDGER.md
```

Template: `references/qor-refactor-examples.md`.

### Step 8: Handoff

Template: `references/qor-refactor-examples.md`.

### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/implement.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/implement.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "implement", human_oversight=ai_provenance.HumanOversight.ABSENT
)
gate_chain.write_gate_artifact(
    phase="implement", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/implement.schema.json`; the helper validates before write. Per Phase 54: refactor calls `ai_provenance.build_manifest` to embed AI provenance.

## Delegation

Per `qor/gates/delegation-table.md`:

- **Refactor complete** → `/qor-audit` (re-audit) when invoked from outside the SDLC chain, or `/qor-substantiate` when invoked from a substantiate-driven Section 4 cleanup.
- **File-internal refactor surfaces project-level structural issues** (e.g., a refactor reveals two modules that should be one, or a directory boundary that's wrong) → escalate to `/qor-organize`. Refactor owns file-internal logic shape; project topology belongs to organize.

## Constraints

- **NEVER** change behavior during refactor (only structure)
- **NEVER** skip orphan detection in multi-file mode
- **NEVER** leave any Section 4 violation after refactor
- **NEVER** push refactored code to CI without running CI-equivalent commands locally first
- **NEVER** push individual fix commits — batch all refactoring into one commit
- **NEVER** force-push to shared branches without GR-2 coordination protocol
- **NEVER** leave secrets in code — rotate, rewrite history, then gitignore (GR-1)
- **ALWAYS** update SYSTEM_STATE.md with new tree
- **ALWAYS** update ledger with refactor hash
- **ALWAYS** verify tests still pass after refactor
- **ALWAYS** run local CI mirror (lint + test with CI flags) before pushing refactored code
- **ALWAYS** batch CI fixes into a single push

## Success Criteria

Refactor succeeds when:

- [ ] All Section 4 violations resolved (functions <=40, files <=250, nesting <=3)
- [ ] No nested ternaries remain
- [ ] No orphan files detected (all connected to build path)
- [ ] All tests pass after refactor
- [ ] Behavior unchanged (only structure modified)
- [ ] SYSTEM_STATE.md updated with new file tree
- [ ] META_LEDGER.md updated with refactor hash

## Integration with S.H.I.E.L.D.

This skill implements:

- **Section 4 Razor Enforcement**: Mandatory simplification pass for violations
- **Structural Integrity**: Ensures no orphans or broken imports after changes
- **Hash Chain Continuation**: Records refactoring in META_LEDGER
- **Specialist Persona**: Precision structural changes without behavior modification
