---
name: qor-remediate
description: Process-level remediation for repeated failures, gate violations, and regression patterns. Absorbs qor-course-correct.
user-invocable: true
category: sdlc
requires: []
enhances_with:
  - codex-plugin: adversarial review of proposed remediation
  - agent-teams: parallel specialist consultation for cross-cutting fixes
gate_reads:
  - .qor/gates/<session_id>/substantiate.json (if present)
  - docs/PROCESS_SHADOW_GENOME.md
gate_writes: .qor/gates/<session_id>/remediate.json
phase: remediate
tone_aware: false
---
# /qor-remediate — Process Remediation

<skill>
  <trigger>/qor-remediate</trigger>
  <phase>remediate (post-substantiate or threshold-auto-triggered)</phase>
  <persona>Governor</persona>
  <output>Remediation proposal + shadow-process events addressed</output>
</skill>

## Purpose

Handle **process-level** failures distinct from code failures:

- Repeated gate overrides
- Threshold breach in Process Shadow Genome (severity sum ≥ 10)
- Regression pattern detected across multiple implement/substantiate cycles
- Capability shortfalls compounding (Codex absent, Agent Teams unavailable)

For **code-level** failures (runtime errors, test failures, broken behavior), use `/qor-debug` instead.

## Execution Protocol

### Step 0: Chain position (Phase 8 wiring)

This skill is **cross-cutting** — invoked when the Process Shadow Genome threshold breaches or by explicit user request. It reads `docs/PROCESS_SHADOW_GENOME.md` directly rather than a prior gate artifact.

### Step 1 — Read context

Invoke `qor/scripts/remediate_read_context.py` via its library interface:

```python
from qor.scripts import remediate_read_context as rrc
groups = rrc.load_unaddressed_groups()
```

- Reads both `docs/PROCESS_SHADOW_GENOME.md` (LOCAL) and `docs/PROCESS_SHADOW_GENOME_UPSTREAM.md` via `shadow_process.read_all_events()`.
- Filters `addressed=false`.
- Groups by `(event_type, skill, session_id)`.
- Empty-log case returns `{}`.
- Read latest substantiate gate artifact if present (external to helper).

### Step 2 — Pattern match

Invoke `qor/scripts/remediate_pattern_match.py`:

```python
import remediate_pattern_match as rpm
classifications = rpm.classify(groups)
```

Categorizes each group into one pattern (priority-ordered, highest wins):

- **Aged-high-severity**: sev >= 3 event unaddressed > 90 days (auto-escalated via `qor/scripts/check_shadow_threshold.py`).
- **Hallucination**: unverified mechanism naming caught at substantiate.
- **Regression**: implement-substantiate cycle producing worse state than prior cycle.
- **Gate-loop**: >=2 `gate_override` events in same group (plan-audit, audit-implement). Root cause likely plan-quality or audit-calibration.
- **Capability-shortfall aggregation**: >=3 `capability_shortfall` events in one session.

### Step 3 — Propose process change

Invoke `qor/scripts/remediate_propose.py`:

```python
import remediate_propose as rp
proposals = [rp.propose(c) for c in classifications]
```

Each proposal maps pattern to proposal_kind (`skill | agent | gate | doctrine`) with a skeleton `proposal_text`. Not a code change — a **skill / agent / gate / doctrine** change.

Examples:
- Add a grounding check to `qor-plan` Step 2b for a specific mechanism class
- Tighten `qor-audit` rubric to catch a specific violation class
- Add a reference file under a skill's `references/` capturing a recurring anti-pattern
- Escalate a severity level in the shadow rubric
- Retire a skill that consistently produces low-quality output

### Step 4 — Mark events addressed_pending (Phase 36 B19 wiring)

Invoke `qor/scripts/remediate_mark_addressed.py` with the **pending-stage** API:

```python
from qor.scripts import remediate_mark_addressed as rma
flipped, missing = rma.mark_addressed_pending(
    proposal["addressed_event_ids"], session_id=sid
)
```

- Flips `addressed_pending: true` only. `addressed` stays `false`; `addressed_ts` stays `null`; `addressed_reason` stays `null`.
- Routes write-back to each event's origin file (LOCAL or UPSTREAM) via `shadow_process.id_source_map()` + `write_events_per_source`.
- Returns `(flipped_count, missing_ids)`. `missing_ids` is the list of IDs not found in either log — surfaced per SG-032 instead of silently dropped.
- The `addressed: true` flip is deferred to Step 6 (invoked from `/qor-audit`) and requires a PASS audit with the `reviews-remediate:<path>` operator signal. This two-stage contract codifies "remediation is advisory until reviewed."

### Step 5 — Emit remediate gate artifact

Invoke `qor/scripts/remediate_emit_gate.py`:

```python
import remediate_emit_gate as reg
path = reg.emit(proposal, session_id=sid)
```

Writes `.qor/gates/<session_id>/remediate.json` with the proposal plus a `ts` field for downstream audit.

### Step 6 — Review-pass flip (invoked from `/qor-audit`, Phase 36 B19)

This step is NOT called by `/qor-remediate`. It is executed by `/qor-audit` when the operator invokes audit with the `reviews-remediate:<path>` skill arg and the audit reaches a PASS verdict. For completeness the contract is:

```python
from qor.scripts import remediate_mark_addressed as rma
flipped, missing = rma.mark_addressed(
    proposal["addressed_event_ids"],
    session_id=sid,
    review_pass_artifact_path=".qor/gates/<sid>/audit.json",
    remediate_gate_path=".qor/gates/<sid>/remediate.json",
)
```

`mark_addressed` verifies the audit artifact's `phase == "audit"`, `verdict == "PASS"`, and `reviews_remediate_gate == remediate_gate_path` before flipping. On any verification failure it raises `ReviewAttestationError` without mutating events. This closes the advisory-until-reviewed loop: Step 4 writes pending state; Step 6 (via operator-explicit audit) completes the flip to `addressed: true`.

See `qor/references/doctrine-governance-enforcement.md` §10.1 "Two-stage remediation flip."

## Constraints

- **NEVER** propose code changes — that is `/qor-debug`'s domain
- **NEVER** mark events addressed without a concrete remediation paired to each
- **ALWAYS** cite shadow events by `id` in the remediation proposal
- **ALWAYS** write the gate artifact; remediation that doesn't pass downstream audit is advisory-only until reviewed
- **ALWAYS** check `missing_ids` from `mark_addressed`; non-empty indicates a stale or typo'd event ID and warrants operator attention

## Tested behaviors

See `tests/test_remediate.py` (18 tests): context loading + filtering + grouping, empty-state handling, pattern classification for all 5 patterns, proposal shape + kind mapping + id preservation, mark-addressed flip + origin-file routing + missing-id surfacing, gate artifact write path + payload roundtrip.
