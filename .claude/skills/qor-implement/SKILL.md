---
name: qor-implement
description: >-
  Specialist Implementation Pass that translates gated blueprint into reality using Section 4 Simplicity Razor and TDD-Light methodology. Use when: (1) Implementing after PASS verdict from /qor-audit, (2) Building features from approved architecture plans, or (3) Creating code under KISS constraints.
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/sdlc/qor-implement
phase: implement
tone_aware: false
autonomy: interactive
gate_reads: audit
gate_writes: implement
permitted_tools: [Read, Grep, Glob, Bash, Edit, Write]
permitted_subagents: []
model_compatibility: [claude-opus-4-7]
min_model_capability: opus
---
# /qor-implement - Implementation Pass

<skill>
  <trigger>/qor-implement</trigger>
  <phase>implement</phase>
  <persona>Specialist</persona>
  <output>Source code in src/, tests in tests/</output>
</skill>

## Purpose

Translate the gated blueprint into maintainable reality using strict Section 4 Simplicity Razor constraints and TDD-Light methodology.

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

You are now operating as **The Qor-logic Specialist**.

Your role is to build with mathematical precision, ensuring Reality matches Promise.

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

### Step 2: Gate Verification
 
```
Read: .agent/staging/AUDIT_REPORT.md
```

**INTERDICTION**: If verdict is NOT "PASS":

<!-- qor:fail-fast-only reason="audit verdict is /qor-audit's output, not scaffold; cannot auto-heal" -->
Abort with "Gate locked. Tribunal audit required. Run /qor-audit first."

**INTERDICTION**: If AUDIT_REPORT.md does not exist:

<!-- qor:fail-fast-only reason="AUDIT_REPORT.md is /qor-audit's output, not scaffold; cannot auto-heal" -->
Abort with "No audit record found. Run /qor-audit to unlock implementation."

### Step 3: Blueprint Alignment

```
Read: docs/ARCHITECTURE_PLAN.md
Read: docs/CONCEPT.md
```

Extract:

- File tree (what to create)
- Interface contracts (how it should work)
- Risk grade (level of caution required)

### Step 4: Build Path Trace

Before creating ANY file:

```
Read: [entry point - main.tsx, index.ts, package.json]
```

Verify the target file will be connected to the build path.

**If orphan detected**:

```
STOP
Report: "Target file would be orphaned (not in build path).
Verify import chain or update blueprint."
```

### Step 5: TDD-Light

**Before writing any core logic**, create a minimal failing test.
Template: `references/qor-implement-patterns.md`.

**Constraint**: Define exactly ONE success condition that proves Reality matches Promise.

**Test functionality, not presence**: the failing test MUST invoke the unit under test (function call, CLI subprocess, helper render, parser pass) and assert against its output. Tests that only check artifact existence (`assert path.exists()`, `assert <substring> in <file_text>`, `assert hasattr(...)`) do not satisfy TDD-Light. Acceptance question: "If the unit's behavior were silently broken but the artifact still existed, would this test fail?" If no, the test is presence-only and must be rewritten before implementation begins. Per `qor/references/doctrine-test-functionality.md`.

### Step 5.5: Intent Lock Capture (Phase 17 wiring)

Capture a fingerprint of the implementer's intent (plan + PASS audit + HEAD commit) before writing any implementation code. Interdicts drift during implementation.

```bash
PLAN_PATH=$(python -c "from qor.scripts.governance_helpers import current_phase_plan_path; print(current_phase_plan_path())")
# Resolve session_id via the canonical helper (reads .qor/session/current AND
# validates against SESSION_ID_PATTERN per Phase 23 LOW-2 / Phase 50 doctrine)
SESSION_ID=$(python -c "from qor.scripts.session import current; print(current() or 'default')")
python -m qor.reliability.intent_lock capture \
  --session "$SESSION_ID" \
  --plan "$PLAN_PATH" \
  --audit .agent/staging/AUDIT_REPORT.md
```

On non-zero exit, ABORT implementation and report the intent-lock reason (audit not PASS, missing plan, etc.). Lock is re-verified in `/qor-substantiate` Step 4.6.

### Step 6: Precision Build

Apply the Section 4 Razor to EVERY function and file.
Checklist: `references/qor-implement-patterns.md`.

#### Code Patterns

Reference code patterns:
`references/qor-implement-patterns.md`.

### Step 7: Visual Silence (Frontend)

For UI examples, see:
`references/qor-implement-patterns.md`.

### Step 8: Post-Build Cleanup

Final pass checklist:
`references/qor-implement-patterns.md`.

### Step 9: Complexity Self-Check

Before declaring completion:

```
For each file modified/created:
  - Count function lines
  - Count nesting levels
  - Check for nested ternaries
  - Verify naming conventions
  - For every newly-added test in this file, confirm the test body invokes the unit under test (function/method/CLI) and the assertion compares against the call's return value or observable side-effect. Bare `assert <substring> in <file_text>` for the unit's behavior is a presence-only test; flag and rewrite as a functionality test before declaring completion. Per `qor/references/doctrine-test-functionality.md`.
```

If ANY violation found:

```
PAUSE
Report: "Section 4 violation detected. Running self-refactor before completion."
Apply: Automatic splitting/flattening
```

### Step 10: Handoff

Template:
`references/qor-implement-patterns.md`.

### Step 10.5: Mark Blockers Complete

If implementation addressed any blockers in BACKLOG.md:

```
Read: docs/BACKLOG.md
Edit: docs/BACKLOG.md
```

For each addressed blocker, change `- [ ] [ID]` to `- [x] [ID] (v[version] - Complete)`.

### Step 11: Update Ledger

Edit `docs/META_LEDGER.md` — add IMPLEMENTATION entry with files modified, content hash, chain hash.

Template: `references/qor-implement-patterns.md`.

### Step 12.5: Implementation Staging

**Verify Reality = Blueprint**:
1. Read docs/ARCHITECTURE_PLAN.md file tree
2. Glob src/** and tests/**
3. Compare: every planned file exists
4. Verify: no unplanned orphans in src/

IF verification FAILS:
```
ABORT: "Implementation incomplete"
REPORT: Missing/unexpected files
DO NOT STAGE
```

IF verification PASSES:

**Auto-Stage**:
```bash
git add src/**
git add tests/**
git add docs/META_LEDGER.md
git add docs/BACKLOG.md
```

**CHANGELOG Check** (if user-facing changes):
IF CHANGELOG.md not updated AND changes are user-facing:
- WARN: "Consider updating CHANGELOG.md for user-facing changes"

REPORT: "Implementation verified. X files staged. Ready for commit."

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

Schema lives at `qor/gates/schema/implement.schema.json`; the helper validates before write. Per Phase 54: every gate-writing skill calls `ai_provenance.build_manifest` and passes the result through `ai_provenance=manifest`; closes EU AI Act Art. 13/50 transparency surface.

## Delegation

Per `qor/gates/delegation-table.md`:

- **Implementation complete** → `/qor-substantiate` (next phase).
- **Mid-implement Razor bloat detected** (a function or file is growing past Section 4 limits during implementation) → pause and `/qor-refactor`. Do NOT inline a refactor process; refactor owns the file-internal logic shape.
- **Regression / hallucination / degradation detected** during implement → `/qor-debug` for root-cause analysis; do NOT keep iterating on the same code without diagnosing why prior iterations regressed.

## Constraints

- **NEVER** implement without PASS verdict
- **NEVER** exceed Section 4 limits - split/refactor instead
- **NEVER** skip TDD-Light for logic functions
- **NEVER** leave console.log in code
- **NEVER** create files not in blueprint without Governor approval
- **NEVER** add dependencies without proving necessity
- **ALWAYS** verify build path before creating files
- **ALWAYS** mark addressed blockers as complete in BACKLOG.md
- **ALWAYS** handoff to Judge for substantiation
- **ALWAYS** update ledger with implementation hash

## Success Criteria

Implementation succeeds when:

- [ ] AUDIT_REPORT.md shows PASS verdict
- [ ] All files from ARCHITECTURE_PLAN.md created
- [ ] All files connected to build path (no orphans)
- [ ] Section 4 Razor applied to all functions (<=40 lines)
- [ ] Section 4 Razor applied to all files (<=250 lines)
- [ ] Nesting depth <=3 levels for all code
- [ ] No nested ternaries in any code
- [ ] TDD-Light tests written for all logic functions
- [ ] No console.log statements in production code
- [ ] META_LEDGER.md updated with implementation hash
- [ ] Handoff to Judge for substantiation

## Integration with Qor-logic

This skill implements:

- **Precision Build**: Mathematical precision matching Reality to Promise
- **Section 4 Razor**: Strict simplicity constraints on all code
- **TDD-Light**: Test-driven development for logic functions
- **Build Path Verification**: Ensures no orphan files created
- **Hash Chain Continuation**: Updates META_LEDGER with cryptographic linkage

---

**Remember**: Reality must match Promise. Never compromise simplicity for speed.
