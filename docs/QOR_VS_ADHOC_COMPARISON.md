# QOR-process vs ad-hoc — quality comparison for CodeGenome Phase 1+2 (#59)

Two builds of the same upstream issue (`BicameralAI/bicameral-mcp#59`)
were executed with the same model and the same task brief, differing
only in *workflow*:

| Branch | Base | Process |
|---|---|---|
| `claude/elegant-euclid-feeb63` | MythologIQ fork `362b53c` (schema v8) | ad-hoc — single Specialist pass, no audit gate |
| `claude/codegenome-phase-1-2-qor` | upstream/main `6bdff24` (schema v10) | `/qor-bootstrap` → `/qor-plan` → `/qor-audit` (VETO → remediate → PASS) → `/qor-implement` |

The branches deliver functionally identical features (49 codegenome
tests pass on both), but differ in five substantive quality dimensions.
Because the branches are also off different bases, a fair comparison
needs to separate **base-driven wins** (which would evaporate on rebase)
from **process-driven wins** (which persist after rebase).

## Hypothetical rebase deltas

If the ad-hoc branch were rebased onto `upstream/main`, the following
mechanical adjustments would be required. None demand process
discipline — any contributor would catch them within minutes of
running `git rebase`:

| Mechanical fix | Effort | Outcome |
|---|---|---|
| `SCHEMA_VERSION` 9 → 11 | trivial | resolved by rebase |
| Rename `_migrate_v8_to_v9` → `_migrate_v10_to_v11` | trivial | resolved by rebase |
| `SCHEMA_COMPATIBILITY` entry adjusted to `11: "0.11.0"` | trivial | resolved by rebase |

These are all base-driven. They would not appear in a comparison if
both builds were on the same upstream commit.

## What process discipline actually catches

For each row in the original branch comparison, attribution between
"base-driven" and "process-driven":

| Row | Base-driven? | Process-driven? | Persists after rebase? |
|---|---|---|---|
| Schema version + compat map | base | — | ✅ resolved by rebase |
| `adapters/codegenome.py` factory | — | **process** | ❌ still missing — ad-hoc never noticed the upstream-convention break |
| Pydantic models in `contracts.py` (`SubjectIdentityModel`) | — | **process (V3 audit catch)** | ❌ still 4 models — no audit ran |
| `bind_service.py` razor (88-line function) | — | **process (Step 9 self-check)** | ❌ still 88-line — no self-check ran |
| Implementation LOC | mostly equal | — | ~equal |
| Test LOC (623 vs 527) | — | **process (V3 removed `SubjectIdentityModel` test)** | ❌ still 623 |
| Genesis / plan / audit artifacts | — | **process** | ❌ still none |
| 4 upstream issues filed (#67–#70) | — | **process** | ❌ still none |
| Audit-recorded violation history | — | **process** | ❌ still none |

## Estimated quality comparison if both builds were on upstream/main

| Dimension | Rebased ad-hoc (estimated) | QOR-process |
|---|---|---|
| Tests pass | ~50/50 (the `SubjectIdentityModel` test still included) | 49/49 (the model and its test removed) |
| Section 4 razor | ❌ violation in `bind_service.write_codegenome_identity` (88 lines) | ✅ clean (split into 24 + 33 + 33 lines) |
| Convention alignment | ❌ no `adapters/codegenome.py` factory | ✅ factory matches `get_ledger` / `get_code_locator` / `get_drift_analyzer` |
| Scope discipline | ❌ 1 unused Pydantic class (`SubjectIdentityModel`) | ✅ exactly the issue-mandated set |
| Process auditability | ❌ none | ✅ full Merkle chain (5 ledger entries) |
| Pre-existing test failures observed | ❌ unflagged | ✅ 4 upstream issues filed (#67–#70) |
| Reviewer-visible defects on PR submit | **3** (factory missing, scope creep, razor violation) | **0** |

## Why these defects survived the ad-hoc inner loop

Each of the three defects has a specific reason it would not be
caught by careful single-pass implementation:

### 1. Missing `adapters/codegenome.py` factory

The factory pattern is enforced by *upstream convention*, not by any
test or runtime check. The ad-hoc build instantiated
`DeterministicCodeGenomeAdapter` directly inside
`BicameralContext.from_env()`. Functional tests pass; the
architectural-symmetry break is invisible at runtime.

A reviewer reading the diff might catch it, but only if they happen to
look at the existing `adapters/` factories and notice the asymmetry. An
audit pass that explicitly checks "does this file follow the project's
established factory pattern?" catches it deterministically.

### 2. `SubjectIdentityModel` (V3 — orphan / scope creep)

The issue body lists three Pydantic models as Phase 1 deliverables.
The four-dataclass set in `codegenome/adapter.py`
(`SubjectCandidate`, `SubjectIdentity`, `EvidenceRecord`,
`EvidencePacket`) suggests a fourth Pydantic mirror by *symmetric
inference*. Without an adversarial audit pass that explicitly checks
issue-mandate ∩ caller-existence, that inference looks like a
deliverable, not a violation.

The model has no caller in #59, no test exercises it, and its tests
pass trivially because Pydantic validates an empty model. It would
ship as dead code.

### 3. 88-line `write_codegenome_identity` (Section 4 razor)

The function reads as a single coherent operation: compute identity,
check hash parity, persist five records. Without an automated post-
implement line-count check, no inner-loop signal flags it. A
reviewer might catch it on close reading, but might not — the function
is procedural and uniform, not branchy.

The QOR workflow's Step 9 runs `ast.walk` over every new file at
implementation completion and flags any function over 40 lines. That
single deterministic check turned a possible-shipped-defect into a
30-second refactor (`_check_hash_parity` + `_persist_subject_and_identity`
extracted; `write_codegenome_identity` reduced to 33 lines of pure
orchestration).

## Verdict

**Four of the five branch-level quality differences are process-driven,
not base-driven.** Only the schema-version delta would evaporate on
rebase. The other four — factory pattern, scope discipline, razor
compliance, and auditability — persist. They are exactly the
defects that careful single-pass implementation does not reliably
catch, because each requires a specific check that no inner-loop test
performs.

The QOR process did not make the implementation *correct*. The ad-hoc
build was already ~90% correct (same architecture, same test coverage,
same identity model). What the QOR process did was make the
remaining ~10% of defects *detectable* — and it did so before the PR
left the contributor's machine, when remediation cost was minutes
rather than review-cycle days.

That is the actual value the workflow delivers, and it is the price
the workflow overhead is paying for.

## Methodology and reproducibility

- Both branches were built by the same model (Claude Opus 4.7) with
  the same upstream issue brief.
- The ad-hoc branch was built first and committed as a frozen
  reference (`f451700`) before the QOR branch was started, to prevent
  cross-contamination from the audit findings.
- Both branches have all codegenome tests passing locally.
- The QOR branch additionally has the genesis / plan / audit / ledger
  / shadow-genome artifacts in `docs/` and the project root.
- This document was authored after both builds completed, working
  from the actual `git diff` of the two branches and the audit history
  in `docs/META_LEDGER.md` and `docs/SHADOW_GENOME.md`.
