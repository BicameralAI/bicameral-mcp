# Shadow Genome

Recorded failure modes for the QorLogic chain. Each entry captures a
verdict-rejecting pattern so future planning avoids it.

---

## Failure Entry #1

**Date**: 2026-04-28T01:06:38Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `c31802d7...`
**Failure Mode**: HALLUCINATION (V1/V2 — residual `{{verify}}` tags)

### What Failed
`plan-codegenome-phase-1-2.md` shipped to `/qor-audit` with two
unresolved `{{verify: ...}}` tags (lines 19, 143).

### Why It Failed
The qor-plan grounding doctrine (Step 2b) declares: "Residual
`{{verify: ...}}` tags in a plan block its submission." Both tags had
contextually legitimate purposes:
- Line 19: documenting a deferred decision (release-eng version pin)
- Line 143: pairing a verifiable assertion with the test that verifies it

But the doctrine is binary — *any* residual `{{verify}}` blocks. The
governor used the tags as informal annotations rather than resolving or
removing them before submission.

### Pattern to Avoid
`{{verify: ...}}` is a *working-file* annotation, not a *submission-grade*
artifact. Before submission to audit:
- If the claim is *deferred* to another decision-maker (e.g. release-eng),
  rewrite as plain prose stating the deferral and its owner.
- If the claim is *self-resolving* via a planned test or check, delete
  the tag — the test or check is the verification.
- If the claim is genuinely uncertain and cannot be deferred or
  self-resolved, the plan is not yet ready for audit; resolve before
  submission.

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md remediation #1 (pin `0.11.0`
placeholder + plain prose deferral) and #2 (delete in-plan tag — let test
stand). Re-submission for `/qor-audit` follows.

---

## Failure Entry #2

**Date**: 2026-04-28T01:06:38Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `c31802d7...`
**Failure Mode**: ORPHAN / SCOPE_CREEP (V3 — `SubjectIdentityModel`)

### What Failed
Phase 1 of `plan-codegenome-phase-1-2.md` proposed four Pydantic models
in `codegenome/contracts.py`. The upstream issue #59 mandates only three:
`SubjectCandidateModel`, `EvidenceRecordModel`, `EvidencePacketModel`.
The fourth, `SubjectIdentityModel`, is not in the issue, has no caller in
the plan, and is not covered by any test.

### Why It Failed
The user's anti-goal Q2=B authorized exactly one Phase-3 foundation
artifact: the `subject_version` table (so the schema migration fires
once, not twice). `SubjectIdentityModel` does not fall under that
exception — it is an unrelated stub for a future MCP-boundary surface
that #59 does not deliver.

### Pattern to Avoid
**Symmetry is not a justification.** "All four dataclasses get Pydantic
mirrors" is an aesthetic argument, not a YAGNI-compliant one. When an
issue lists three deliverables, deliver three. Future phases that need
the fourth mirror can add it under their own justification, with their
own caller, in their own PR. Audit checks issue-mandate ∩ caller-
existence; symmetry-driven extras fail both.

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md remediation #3: remove
`SubjectIdentityModel` from the Phase 1 deliverables list in
`plan-codegenome-phase-1-2.md` so the implementation phase does not
write the unjustified mirror. Re-submission for `/qor-audit` follows.

---

## Failure Entry #3 (Phase 3 plan, #60)

**Date**: 2026-04-28T03:18:53Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `7fad1059...`
**Failure Mode**: ORPHAN / MACRO-ARCHITECTURE (V1, V2, V3 — coupled
build-path incompleteness)

### What Failed
`plan-codegenome-phase-3.md`'s auto-resolve recipe inside
`evaluate_continuity_for_drift` (line 203):

> "On ≥0.75: writes `subject_version`, `identity_supersedes`, calls
> `update_binds_to_region`, returns `ContinuityResolution`..."

The recipe enumerates three terminal writes but omits four prerequisite
writes that are required to make the terminal writes valid:

- The new `subject_identity` row that `identity_supersedes(old, new)`
  references.
- The new `code_region` row that `update_binds_to_region(...,
  new_region_id)` references.
- The `has_version` edge that connects `code_subject` to the newly
  written `subject_version` row (otherwise the row is unreachable).
- The `compute_identity_with_neighbors` call that produces the new
  identity values used by both the new `subject_identity` row and the
  new `subject_version` row.

### Why It Failed
The plan was written from the issue body's bullet list ("write
subject_version / write identity_supersedes / update binds_to") and
treated those bullets as the *complete* sequence rather than as the
*terminal* sequence. Each terminal write has a graph-theoretic
prerequisite (the target row must exist before a RELATE can reference
it) that was implicit in the issue but not enumerated in the plan.

### Pattern to Avoid
When a plan describes ledger writes that involve RELATE statements,
enumerate every prerequisite upsert by name. Treat "writes X" as a
single bullet only if X is a node, never if X is an edge — edges
require both endpoints to exist. A plan that says "write
identity_supersedes" must also say where the OUT endpoint comes from.
The audit pass that catches this is *macro-architecture: build path is
intentional* — same checkbox, different scale (data flow rather than
module flow).

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md required remediations `#1`, `#2`,
and `#3`: extend `evaluate_continuity_for_drift` description with the
7-step sequence (compute_identity → upsert_code_region →
upsert_subject_identity → write_subject_version → relate_has_version
→ write_identity_supersedes → update_binds_to_region); add the
missing `relate_has_version` query + adapter wrapper to the plan;
update integration-test fixture-setup descriptions to verify the
prerequisite rows. Re-submission for `/qor-audit` follows.

---
