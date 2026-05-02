# Qor-logic Audit Templates

## Audit Report Template

```markdown
# AUDIT REPORT

**Tribunal Date**: [ISO 8601]
**Target**: [project/component name]
**Risk Grade**: [L1 / L2 / L3]
**Auditor**: The Qor-logic Judge

---

## VERDICT: [PASS / VETO]

---

### Executive Summary

[One paragraph explaining the verdict]

### Audit Results

#### Security Pass
**Result**: [PASS / FAIL]
[Specific findings]

#### Ghost UI Pass
**Result**: [PASS / FAIL]
[Specific findings]

#### Section 4 Razor Pass
**Result**: [PASS / FAIL]
[Specific findings]

#### Dependency Pass
**Result**: [PASS / FAIL]
[Specific findings]

#### Orphan Pass
**Result**: [PASS / FAIL]
[Specific findings]

#### Macro-Level Architecture Pass
**Result**: [PASS / FAIL]
[Specific findings]

### Violations Found

| ID  | Category | Location    | Description    |
| --- | -------- | ----------- | -------------- |
| V1  | [type]   | [file/line] | [what's wrong] |

### Per-ground directives (if VETO)

Each VETO ground carries exactly one `**Required next action:**` line. See
`qor/references/doctrine-audit-report-language.md` for the canonical
ground-class -> skill mapping. The five ground classes MUST use the exact
headers below when a ground applies.

#### Section 4 Razor

[Specific findings]

**Required next action:** `/qor-refactor`

#### Orphan file / Macro-arch breach

[Specific findings]

**Required next action:** `/qor-organize`

#### Plan-text

[Specific findings (A08 safe_load, dependency, missing tests, ghost feature, wording)]

**Required next action:** Governor: amend plan text, re-run `/qor-audit`

#### Process-level

[Specific findings (repeated VETO, SG threshold, capability shortfall)]

**Required next action:** `/qor-remediate`

#### Code-logic defect

[Specific findings (regression, hallucination, behavioral break)]

**Required next action:** `/qor-debug`

## Process Pattern Advisory

<!-- qor:veto-pattern-advisory -->

Populated from `qor/scripts/veto_pattern.py check()` at Step 7 of the audit
protocol. When the detector returns `PatternResult(detected=True, ...)`,
this section names the two sealed phases involved and recommends
`/qor-remediate`. When `detected=False`, the section reads:

> No repeated-VETO pattern detected in the last 2 sealed phases.

The advisory is non-blocking. It never itself constitutes a new VETO
ground; current-audit VETOs stand on their own merits.

### Verdict Hash

SHA256(this_report) = [hash]

---
_This verdict is binding._
```

## Ledger Entry Template (GATE TRIBUNAL)

```markdown
### Entry #[N]: GATE TRIBUNAL

**Timestamp**: [ISO 8601]
**Phase**: GATE
**Author**: Judge
**Risk Grade**: [L1/L2/L3]
**Verdict**: [PASS / VETO]

**Content Hash**:
SHA256(AUDIT_REPORT.md) = [hash]

**Previous Hash**: [from entry N-1]

**Chain Hash**:
SHA256(content_hash + previous_hash) = [calculated]

**Decision**: [Brief summary of verdict and reason]
```

## Shadow Genome Entry Template (VETO only)

```markdown
## Failure Entry #[N]

**Date**: [ISO 8601]
**Verdict ID**: [from audit report]
**Failure Mode**: [COMPLEXITY_VIOLATION / SECURITY_STUB / GHOST_PATH / HALLUCINATION / ORPHAN]

### What Failed
[Component or pattern that was rejected]

### Why It Failed
[Specific violation details]

### Pattern to Avoid
[Generalized lesson for future work]

### Remediation Attempted
[Was it fixed? How?]
```

## Final Report Template

```markdown
## Tribunal Complete

**Verdict**: [PASS / VETO]
**Risk Grade**: [L1/L2/L3]
**Report Location**: .agent/staging/AUDIT_REPORT.md

### If PASS
Gate cleared. The Specialist may proceed with `/qor-implement`.

### If VETO
Implementation blocked. Address violations and re-submit for audit.
Required actions logged in AUDIT_REPORT.md.
Failure mode recorded in SHADOW_GENOME.md.

---
_Gate [OPEN / LOCKED]. Proceed accordingly._
```

## Step Z payload shape (Phase 29 wiring)

`/qor-audit` Step Z writes the audit gate artifact at `.qor/gates/<session>/audit.json` via `gate_chain.write_gate_artifact`. Schema: `qor/gates/schema/audit.schema.json`.

Required fields (per schema `required`): `phase` (always `"audit"`, injected by helper), `ts` (ISO-8601 UTC with seconds), `session_id`, `target` (plan file path audited), `verdict` (enum `"PASS"` / `"VETO"`).

Optional fields: `violations` (array of objects, reserved for future structured per-ground capture), `risk_grade` (enum `"L1"` / `"L2"` / `"L3"`), `report_path` (path to the corresponding AUDIT_REPORT.md).

No schema change was introduced in Phase 29; the shape was pre-existing and the skill simply began writing it.

## Documentation Drift insertion marker (Phase 31 wiring)

Every AUDIT_REPORT.md produced by `/qor-audit` MUST carry the canonical marker `<!-- qor:drift-section -->` at the point where `doc_integrity.render_drift_section` output is spliced in. If the helper returns the empty string (clean), the marker stays in place and the section header "## Documentation Drift" is rendered with `(clean)` as body. If the helper returns drift markdown, the section replaces the marker line with the returned text.

```markdown
<!-- qor:drift-section -->
```

Lint: `tests/test_audit_drift_auto_invoked.py::test_audit_template_has_drift_marker` asserts the marker is present in this template.

## Findings Categories slot (Phase 37 B20b wiring)

Every AUDIT_REPORT.md produced with `verdict: VETO` MUST populate a `findings_categories` field in the emitted audit gate artifact drawn from the closed 12-value enum (see `qor/gates/schema/audit.schema.json`). Unmapped findings raise `UnmappedCategoryError` at gate emission per `qor/scripts/findings_signature.py`. The audit report markdown does not need a visible categories section; the structured gate artifact is the canonical record.

Lint: `tests/test_audit_gate_emits_findings_categories.py` asserts schema enforcement and skill-prose references.
