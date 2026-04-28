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
