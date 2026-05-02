---
name: qor-validate
description: >-
  Merkle Chain Validator that recalculates and verifies cryptographic integrity of the project's Meta Ledger. Use when: (1) Verifying chain integrity before handoff, (2) Detecting tampering or corruption, (3) Auditing decision history, or (4) Validating after manual ledger edits.
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/governance/qor-validate
phase: validate
tone_aware: false
autonomy: interactive
gate_reads: substantiate
gate_writes: validate
permitted_tools: [Read, Grep, Glob, Bash]
permitted_subagents: []
model_compatibility: [claude-opus-4-7, claude-sonnet-4-6]
min_model_capability: sonnet
---
# /qor-validate - Merkle Chain Validator

<skill>
  <trigger>/qor-validate</trigger>
  <phase>ANY</phase>
  <persona>Judge</persona>
  <output>Chain validity report with entry-by-entry verification</output>
</skill>

## Purpose

Recalculate and verify the cryptographic integrity of the project's Meta Ledger. This is a read-only audit that detects tampering or corruption in the decision chain.

## Execution Protocol

### Step 0: Gate Check (advisory — Phase 8 wiring)

Verify prior-phase artifact exists and is well-formed before proceeding.

```python
from qor.scripts import gate_chain, session

sid = session.get_or_create()
result = gate_chain.check_prior_artifact("validate", session_id=sid)
if not result.found:
    # Prompt user to override; on confirm:
    gate_chain.emit_gate_override(
        current_phase="validate",
        prior_phase_name="substantiate",
        reason="user override: substantiate.json not found",
        session_id=sid,
    )
elif not result.valid:
    gate_chain.emit_gate_override(
        current_phase="validate",
        prior_phase_name="substantiate",
        reason=f"user override: {result.errors}",
        session_id=sid,
    )
```

Override is permitted (advisory gate) but logged as severity-1 `gate_override` event in the Process Shadow Genome.

**Phase 54 wiring**: when `gate_chain.emit_gate_override` raises `OverrideFrictionRequired`, prompt the operator for a written justification (>=50 chars) and re-call `emit_gate_override` with `justification=<text>`. Per `qor/references/doctrine-ai-rmf.md` §MANAGE-1.1 + `qor/references/doctrine-eu-ai-act.md` Art. 14.

### Step 1: Identity Activation
You are now operating as **The Qor-logic Judge** in validation mode.

### Step 2: Load Ledger

```
Read: docs/META_LEDGER.md
```

**INTERDICTION**: If ledger does not exist:

<!-- qor:recovery-prompt -->
Ask the user: "docs/META_LEDGER.md not found. Should I correct it by running 'qor-logic seed' or pause? [Y/n]"

- On Y or empty: run `qor-logic seed` (idempotent), then continue.
- On N: abort with "Run `qor-logic seed` to create the governance scaffold, then re-run this skill."

### Step 3: Parse Entries

Extract all ledger entries:

Reference implementation: `qor/scripts/ledger_hash.py` — exposes `ENTRY_RE`, `CONTENT_HASH_RE`, `PREV_HASH_RE`, `CHAIN_HASH_RE` and the `verify()` entrypoint. CLI: `qor-logic verify-ledger docs/META_LEDGER.md`.

### Step 4: Verify Chain

Reference implementation: `qor/scripts/ledger_hash.py` — exposes `ENTRY_RE`, `CONTENT_HASH_RE`, `PREV_HASH_RE`, `CHAIN_HASH_RE` and the `verify()` entrypoint. CLI: `qor-logic verify-ledger docs/META_LEDGER.md`.

### Step 5: Generate Report

#### If Chain Valid:

Templates: `references/qor-validate-reports.md`.

#### If Chain Broken:

Templates: `references/qor-validate-reports.md`.

### Step 6: Reference Document Verification (Optional)

If chain is valid, optionally verify referenced documents still exist:

```
Glob: docs/CONCEPT.md
Glob: docs/ARCHITECTURE_PLAN.md
Glob: .agent/staging/AUDIT_REPORT.md
```

Template: `references/qor-validate-reports.md`.

### Step 7: Content Hash Verification (Deep Audit)

For thorough validation, recalculate content hashes:

Reference implementation: `qor/scripts/ledger_hash.py` — exposes `ENTRY_RE`, `CONTENT_HASH_RE`, `PREV_HASH_RE`, `CHAIN_HASH_RE` and the `verify()` entrypoint. CLI: `qor-logic verify-ledger docs/META_LEDGER.md`.

Template: `references/qor-validate-reports.md`.

## Final Report Summary

Template: `references/qor-validate-reports.md`.

### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/validate.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/validate.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "validate",
    human_oversight=(
        ai_provenance.HumanOversight.PASS if payload.get("overall") == "PASS"
        else ai_provenance.HumanOversight.VETO
    ),
)
gate_chain.write_gate_artifact(
    phase="validate", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/validate.schema.json`; the helper validates before write. Per Phase 54: validate calls `ai_provenance.build_manifest` with the verdict mapped to `HumanOversight`; closes EU AI Act Art. 14 oversight-signal surface.

## Delegation

Per `qor/gates/delegation-table.md`:

- **Validation complete + PASS** → `/qor-repo-release` or session is sealed (depending on workflow).
- **Validation FAIL with single defect** → return to `/qor-implement` with scoped fix.
- **Repeat validation failure across cycles** (3+ same root cause) → `/qor-remediate`. Do NOT keep re-implementing — the issue is process-level, not code-level.

## Constraints

- **NEVER** modify any files during validation
- **NEVER** skip any entry in the chain
- **ALWAYS** report exact break location if chain is broken
- **ALWAYS** lock dataset if chain is compromised
- **ALWAYS** provide remediation guidance for broken chains

## Success Criteria

Validation succeeds when:

- [ ] All ledger entries parsed and verified
- [ ] Chain hashes recalculated and compared
- [ ] Break location identified (if chain is broken)
- [ ] Referenced documents verified to exist
- [ ] Validation report generated with entry-by-entry results
- [ ] Chain status reported (VALID or BROKEN at entry N)

## Integration with S.H.I.E.L.D.

This skill implements:

- **Merkle Chain Verification**: Cryptographic integrity audit of decision history
- **Read-Only Audit**: Never modifies files, only reports findings
- **Tamper Detection**: Identifies unauthorized changes to ledger entries
- **Pre-Delivery Gate**: Validates chain before handoff or release
