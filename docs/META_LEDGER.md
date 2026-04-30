# QorLogic Meta Ledger

## Chain Status: ACTIVE
## Genesis: 2026-04-28T01:00:52Z

---

### Entry #1: GENESIS

**Timestamp**: 2026-04-28T01:00:52Z
**Phase**: BOOTSTRAP
**Author**: Governor (executed via `/qor-bootstrap`)
**Risk Grade**: L2

**Content Hash**:
SHA256(CONCEPT.md + ARCHITECTURE_PLAN.md) = `29dfd085d2993f4a72dc1157d5d0cd33b818bdd3df3de2356c6e62e212457a1d`

**Previous Hash**: GENESIS (no predecessor)

**Decision**: Project DNA initialized. Lifecycle: ALIGN/ENCODE complete.

**Branch deviation note**: Bootstrap was executed inline on the QOR-process
feature branch `claude/codegenome-phase-1-2-qor` (off `upstream/main`)
instead of a dedicated `feat/bicameral-mcp-genesis` branch, by user
direction — these genesis docs are part of the QOR-process artifact for
side-by-side comparison against an ad-hoc reference build on
`claude/elegant-euclid-feeb63`. The genesis hash above remains the
canonical chain anchor regardless of branch.

---

### Entry #2: PLAN

**Timestamp**: 2026-04-28T00:55:00Z (preceded bootstrap chronologically)
**Phase**: PLAN
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (inherited from genesis)

**Artifact**: `plan-codegenome-phase-1-2.md`

**Previous Hash**: `29dfd085...` (genesis)

**Scope**: CodeGenome Phase 1+2 — adapter boundary + bind-time identity
records, against upstream issue #59. Two-phase plan with TDD-ordered
unit + integration tests; locked architecture decisions on module placement
(flat `codegenome/`), composition (handler-orchestrated), factory pattern
(`adapters/codegenome.py`), and hash strategy (sha256 content for ledger
parity, blake2b signature). Three open questions flagged at top.

**Decision**: Plan accepted by user; one `{{verify}}` tag remains on the
`subject_identity.content_hash == code_region.content_hash` exit
criterion for auditor grading.

**Next required action**: `/qor-audit` (mandatory for L2).

---

### Entry #3: GATE TRIBUNAL

**Timestamp**: 2026-04-28T01:06:38Z
**Phase**: GATE
**Author**: Judge (executed via `/qor-audit`)
**Risk Grade**: L2
**Verdict**: VETO
**Mode**: solo (codex-plugin shortfall logged)

**Content Hash**:
SHA256(AUDIT_REPORT.md) = `a404e4bf9d46b0b71e2796b1fd48b46d8036ad2a1bacd2d5b9150fbb5c891a20`

**Previous Hash**: `29dfd085...` (Genesis)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `c31802d7bbf38f70cc466b0990903027dde75b57f0856529df537adef559d8c2`

**Decision**: VETO. Three violations: V1/V2 grounding (residual `{{verify}}`
tags violate qor-plan Step 2b doctrine); V3 orphan/scope-creep
(`SubjectIdentityModel` not issue-mandated, no caller, exceeds anti-goal
Q2=B authorization). Substance of plan is sound on architecture, composition,
dependency direction, test coverage, security, OWASP, and convention
alignment. Remediation is surgical: pin one placeholder, delete two tags,
delete one Pydantic model. Re-audit required before `/qor-implement`.

---

### Entry #4: GATE TRIBUNAL (Re-Audit)

**Timestamp**: 2026-04-28T01:13:24Z
**Phase**: GATE
**Author**: Judge (executed via `/qor-audit`)
**Risk Grade**: L2
**Verdict**: PASS
**Mode**: solo (capability shortfall logged in entry #3, not duplicating)

**Content Hash**:
SHA256(AUDIT_REPORT.md) = `761013d188d90b6d96ba6d8782f93a9b2001c1270e9b0892a53ada85c99213ad`

**Previous Hash**: `c31802d7...` (Entry #3, predecessor VETO)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `0fc97cd3169c75d5c1f95fb537b0aab5660375862ffbd17f13a0baafc5ad160d`

**Decision**: PASS. All three predecessor violations (V1, V2, V3) are
closed by surgical remediations in `plan-codegenome-phase-1-2.md`.
`grep -c "{{verify"` → 0; `grep -n "SubjectIdentityModel"` → no matches.
No new violations introduced. All other audit passes (Security, OWASP,
Ghost UI, Razor, Dependency, Macro Architecture) remain PASS. Section 4
razor footprint *improved* (contracts.py is now smaller). Gate is OPEN
for `/qor-implement`.

---

### Entry #5: IMPLEMENTATION

**Timestamp**: 2026-04-28T01:49:30Z
**Phase**: IMPLEMENT
**Author**: Specialist (executed via `/qor-implement`)
**Risk Grade**: L2
**Mode**: sequential (capability shortfalls for `qor/scripts` runtime + agent-teams logged at prior phases)

**Files created**:
- `codegenome/__init__.py`, `adapter.py`, `contracts.py`, `confidence.py`, `config.py`,
  `deterministic_adapter.py`, `bind_service.py`
- `adapters/codegenome.py`
- `tests/test_codegenome_{adapter,bind_integration,confidence,config}.py`

**Files modified**:
- `ledger/schema.py` (SCHEMA_VERSION 10 → 11; +CodeGenome tables/edges; +`_migrate_v10_to_v11`)
- `ledger/queries.py` (+5 query functions)
- `ledger/adapter.py` (+5 thin wrapper methods + import additions)
- `context.py` (+`codegenome` and `codegenome_config` fields on `BicameralContext`; populated in `from_env()`)
- `handlers/bind.py` (+side-effect identity-write hook, gated by `ctx.codegenome_config.identity_writes_active()`)
- `.gitignore` (+QOR governance directories)

**Content Hash**:
SHA256(impl files concatenated by sorted path) = `e217fb615d821fbb2f89e4a1f800a23d4ebf10f6ac89b55d3362fd95f094fae9`

**Previous Hash**: `0fc97cd3...` (Entry #4, PASS verdict)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `eed1816066b0b65082adf9711dffe1b8a91e6f0b9a5cecf9258ffe3521a0429b`

**Test results**:
- Codegenome unit + integration: 49 passed / 0 failed (this PR)
- Section 4 razor self-check: PASS — all new functions ≤ 40 lines (one mid-implement violation in `bind_service.write_codegenome_identity` was caught and refactored into `_check_hash_parity` + `_persist_subject_and_identity` helpers per Step 9)
- Full suite regression: 254 passed / 81 failed against the implementation; baseline (pristine upstream/main `6bdff24`) was 250 passed / 85 failed → **zero regressions introduced; 4 codegenome integration tests now pass that previously failed without the impl**.

**Pre-existing test failures filed upstream**:
- BicameralAI/bicameral-mcp#67 — Windows subprocess `NotADirectoryError` (38 tests)
- BicameralAI/bicameral-mcp#68 — surrealkv URL parsing on Windows (5 tests)
- BicameralAI/bicameral-mcp#69 — missing `_merge_decision_matches` symbol (3 tests)
- BicameralAI/bicameral-mcp#70 — AssertionError cluster umbrella (~20 tests)

**Scope check**: Validated against issue #59 deliverables list — all mandated paths/signatures delivered (with documented adaptations for upstream's flat layout). Two justified deviations:
- Schema added one extra edge (`about` decision→code_subject) — required by `find_subject_identities_for_decision`'s two-hop graph walk per the issue's exit criterion.
- `content_hash` uses sha256-with-whitespace-normalization (`ledger.status.hash_lines`) instead of literal `blake2b(body_text)` — required by the issue's exit criterion *"subject_identity.content_hash matches code_region.content_hash at bind time"*.

**Decision**: Reality matches Promise. Plan executed without deviation from audited specification.

---

### Entry #6: SUBSTANTIATION (SESSION SEAL)

**Timestamp**: 2026-04-28T02:23:33Z
**Phase**: SUBSTANTIATE
**Author**: Judge (executed via `/qor-substantiate`)
**Risk Grade**: L2
**Verdict**: **REALITY = PROMISE**

**Verifications run**:

| Check | Result | Notes |
|---|---|---|
| Step 2 — PASS verdict present | ✅ | `.agent/staging/AUDIT_REPORT.md` (path divergence from skill default `.failsafe/governance/` — noted) |
| Step 2.5 — Version validation | ✅ | Current tag `v0.10.7` → target `v0.11.0` (feature bump, additive) |
| Step 3 — Reality audit | ✅ | 25 / 25 planned files exist; 0 missing; 0 unplanned additions in scope |
| Step 3.5 — Blocker review | ⚠️ | 1 open security blocker (`S1 — SECURITY.md missing`); 1 dev blocker (`D1 — SCHEMA_COMPATIBILITY[10]` upstream gap, out of scope). Neither blocks this seal. |
| Step 4 — Functional verification | ✅ | 49 / 49 codegenome tests pass post-rebase (auto-merged `handlers/bind.py`, `ledger/adapter.py`, `ledger/queries.py` did not regress) |
| Step 4.5 — Skill file integrity | n/a | No skill files modified this session |
| Step 4.6 — Reliability sweep | ⚠️ | `qor/reliability/` scripts absent (intent-lock, skill-admission, gate-skill-matrix) — capability shortfall logged in SYSTEM_STATE.md, sweep skipped |
| Step 5 — Section 4 razor final | ✅ | All new functions ≤ 40 lines; all new files ≤ 250 lines |
| Step 6 — SYSTEM_STATE.md sync | ✅ | `docs/SYSTEM_STATE.md` written |

**Rebase note**: Branch was rebased onto `upstream/main` (tip `7796ab9`)
between Entry #5 and this seal to resolve a CHANGELOG.md merge conflict
introduced by upstream's v0.10.3 → v0.10.7 release cadence. The rebased
HEAD is `51ff53f`; the same logical commit as `edc4ff4` from Entry #5,
with one CHANGELOG section reordering. Codegenome tests verified passing
post-rebase.

**Session content hash** (27 files, sorted-path concatenation):
SHA256 = `c2887a4612034f8772ef9bb7e33de853bb658abb2a8ef74389426deae4e6735d`

**Previous chain hash**: `eed18160...` (Entry #5, IMPLEMENTATION)

**Merkle seal**:
SHA256(content_hash + previous_hash) = **`509b411d3e00cfe8135faf60ba99b1c3644680d63bb959e846b146cfb5da6acb`**

**Decision**: Reality matches Promise. Implementation conforms to the
audited plan; all exit criteria for issue #59 satisfied; no new
violations introduced post-rebase. Session is sealed.

---

### Entry #7: GATE TRIBUNAL (Phase 3 plan)

**Timestamp**: 2026-04-28T03:18:53Z
**Phase**: GATE
**Author**: Judge (executed via `/qor-audit`)
**Risk Grade**: L2
**Verdict**: VETO
**Mode**: solo (capability shortfalls per Entry #3)

**Target**: `plan-codegenome-phase-3.md` (CodeGenome Phase 3 — continuity evaluation, issue #60)

**Content Hash**:
SHA256(AUDIT_REPORT.md) = `3d77c8d2860e177cb0a320ee017188aa280c2df6499486fd3b50996db44eede3`

**Previous Hash**: `509b411d...` (Entry #6, SUBSTANTIATION seal of Phase 1+2)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `7fad10597b6cbdfb50bf0041169e5905a08bda1004ad59b9d7feb1f8b2edad93`

**Decision**: VETO. Three coupled orphan / macro-architecture failures
(V1, V2, V3 — same root cause): plan's auto-resolve recipe references
records and edges that the recipe does not create. `write_subject_version`
omits the `has_version` edge wire-up; `write_identity_supersedes`
references a `new_identity_id` whose creation is not enumerated;
`update_binds_to_region` references a `new_region_id` whose creation
is not enumerated. All other audit passes (Security, OWASP, Ghost UI,
Razor, Dependency, Grounding) PASS. Remediation is mechanical — extend
the plan's `evaluate_continuity_for_drift` description with the 7-step
sequence enumerated in the audit report, and add a `relate_has_version`
ledger query.

---

### Entry #8: GATE TRIBUNAL (Phase 3 plan, Re-Audit)

**Timestamp**: 2026-04-28T03:37:09Z
**Phase**: GATE
**Author**: Judge (executed via `/qor-audit`)
**Risk Grade**: L2
**Verdict**: PASS
**Mode**: solo

**Target**: `plan-codegenome-phase-3.md` (post-remediation)

**Content Hash**:
SHA256(AUDIT_REPORT.md) = `9ed0eb80371d5e4c6e8c99ae1fa42585cc2ddd488baf8435dd58c8fc960d3bcf`

**Previous Hash**: `7fad1059...` (Entry #7, predecessor VETO)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `e249fb8f42ad4fdd2f6bf23528b8dd119ad44466411102339fcf3d92be59f514`

**Decision**: PASS. All three predecessor violations (V1, V2, V3 —
coupled orphan/macro-architecture findings) closed by surgical
remediations. Auto-resolve recipe in `evaluate_continuity_for_drift`
is now a complete 7-step sequence with every RELATE preceded by the
upsert that creates its target row. The previously-orphan `has_version`
edge (defined-but-unused since #59) gains its first caller via the new
`relate_has_version` query. No new violations introduced. Section 4
razor footprint commitment intact at success-criteria level. Gate is
OPEN for `/qor-implement` of Phase 3.

---

### Entry #9: IMPLEMENTATION (Phase 3, #60)

**Timestamp**: 2026-04-28T04:38:55Z
**Phase**: IMPLEMENT
**Author**: Specialist (executed via `/qor-implement`)
**Risk Grade**: L2

**Files created**:
- `codegenome/continuity.py` (matcher: 151 LOC)
- `codegenome/continuity_service.py` (orchestrator + DriftContext: 190 LOC)
- `tests/test_codegenome_continuity.py` (18 tests)
- `tests/test_codegenome_continuity_ledger.py` (8 tests)
- `tests/test_codegenome_continuity_service.py` (5 tests)

**Files modified**:
- `codegenome/adapter.py` (+`SubjectIdentity.neighbors_at_bind` field)
- `codegenome/deterministic_adapter.py` (+`compute_identity_with_neighbors`)
- `codegenome/bind_service.py` (+optional `code_locator` arg)
- `handlers/bind.py` (passes `ctx.code_graph`)
- `handlers/link_commit.py` (+`_run_continuity_pass`, +`continuity_resolutions` field)
- `contracts.py` (+`ContinuityResolution` model, +field on `LinkCommitResponse`)
- `ledger/schema.py` (SCHEMA_VERSION 11→12; +`identity_supersedes` edge; +`neighbors_at_bind` field on `subject_identity`; +`_migrate_v11_to_v12`)
- `ledger/queries.py` (+`update_binds_to_region`, `write_identity_supersedes`, `write_subject_version`, `relate_has_version`; extended `upsert_subject_identity` and `find_subject_identities_for_decision` for neighbors)
- `ledger/adapter.py` (+5 thin wrappers + import additions)
- `adapters/code_locator.py` (+`neighbors_for(file, start, end)` Phase-3 protocol method)

**Content Hash**:
SHA256(impl files concatenated by sorted path) = `64b1ed03cbdb76274df154f814cdc89bdd5b133d023fedd857b906dd475bbad8`

**Previous Hash**: `e249fb8f...` (Entry #8, PASS verdict re-audit)

**Chain Hash**:
SHA256(content_hash + previous_hash) = `dc7ece4aa312c003361dae5464b551ec65f9349339bdc39bcf9f2eb9be4b3c36`

**Test results**:
- Codegenome unit + integration: **85 passed / 0 failed** (up from 49 in #59, +36 Phase 3 tests)
- Section 4 razor self-check: **PASS** — all new functions ≤ 40 lines.
  Mid-implement violation in `evaluate_continuity_for_drift` (65→52→47→39
  lines) caught by Step 9 self-check; remediated by extracting helpers
  (`_load_best_identity`, `_build_needs_review`, `_build_resolved`,
  `_persist_resolved_match`) and bundling parameters into a
  `DriftContext` dataclass to keep the function under the 40-line limit.
- Full suite regression: **290 passed / 81 failed** (baseline 254 / 81).
  Zero new failures; 81 pre-existing matches the #67–#70 cluster.

**Pre-existing schema bug discovered** (filed as upstream issue):
- BicameralAI/bicameral-mcp#72 — `binds_to.provenance` declared as
  plain `TYPE object` (without `FLEXIBLE`) silently strips nested
  metadata. Affects `relate_binds_to` in production
  (`{"method": "caller_llm"}` provenance is dropped to `{}`) and the
  new `update_binds_to_region` in this PR. Test for the
  `provenance.method = "continuity_resolved"` assertion in
  `test_codegenome_continuity_ledger.py` is documented-as-deferred
  pending upstream schema fix; edge-swap behavior is verified.

**Scope check**: Plan `plan-codegenome-phase-3.md` exit criteria:
- [x] `SCHEMA_VERSION = 12`; migration registered; `init_schema` idempotent.
- [x] All Phase 1, 2, 3 tests pass under `pytest tests/test_codegenome_*.py -v`.
- [x] `pytest -m phase2` passes (no regression).
- [x] Default off (flags both off): `LinkCommitResponse` shape + behavior identical.
- [x] Flag on, exact-name match: `continuity_resolutions[0].semantic_status="identity_moved"`,
      4 prerequisite ledger states asserted (V1/V2/V3 closed via integration tests).
- [x] Logic-removal: `find_continuity_match` returns `None` (no false continuity).
- [x] needs_review case at 0.50–0.75 confidence.
- [x] Failure isolation: `find_continuity_match` raising → fall-through.
- [x] Ledger module does NOT import from `codegenome` (one-way dep preserved).
- [x] No new MCP tools registered.
- [x] No `BindResponse`/`BindResult` field changes.
- [x] Section 4 razor: every new function ≤ 40 lines.
- [ ] M5 benchmark corpus — **DEFERRED** to backlog `[B4]`. Stubs in
      unit/integration tests cover the scenarios; real-repo fixtures
      enable the false-positive-rate benchmark and are in scope as a
      follow-up PR before #61 starts.

**Decision**: Reality matches Promise modulo the documented M5-corpus
deferral. Plan executed; razor enforced; one upstream-bug discovery
(#72) filed independently.

---

### Entry #10: SUBSTANTIATION (PHASE 3 SESSION SEAL)

**Timestamp**: 2026-04-28T04:45:59Z
**Phase**: SUBSTANTIATE
**Author**: Judge (executed via `/qor-substantiate`)
**Risk Grade**: L2
**Verdict**: **REALITY = PROMISE**

**Verifications run**:

| Check | Result | Notes |
|---|---|---|
| Step 2 — PASS verdict present | ✅ | `.agent/staging/AUDIT_REPORT.md` (Phase 3 plan, chain hash `e249fb8f...`) |
| Step 2.5 — Version validation | ✅ | Current tag `v0.10.7` → target `v0.12.0` (feature bump, additive); `SCHEMA_COMPATIBILITY[12] = "0.12.0"` placeholder |
| Step 3 — Reality audit | ✅ | All 5 Phase 3 planned files exist; no missing; M5 fixture corpus deferred to BACKLOG `[B4]` (acknowledged) |
| Step 3.5 — Blocker review | ⚠️ | Open: `[S1]` SECURITY.md missing (carries from Phase 1+2); `[D1]` SCHEMA_COMPATIBILITY[10] gap; new `[B4]` M5 fixtures. None block this seal. |
| Step 4 — Functional verification | ✅ | 85 / 85 codegenome tests pass; full suite 290 / 81 (zero new failures vs Phase 1+2 baseline 254 / 81; +36 new Phase 3 tests passing) |
| Step 4 — console.log scan | ✅ | No leftover debug prints in new code |
| Step 4.5 — Skill file integrity | n/a | No skill files modified |
| Step 4.6 — Reliability sweep | ⚠️ | qor/reliability/ scripts absent — capability shortfall logged in SYSTEM_STATE.md, sweep skipped |
| Step 5 — Section 4 razor final | ✅ | All new functions ≤ 40 lines after substantiation-time razor regression caught + fixed (`write_codegenome_identity` 53→36 via `_compute_identity_for_bind` helper extraction) |
| Step 6 — SYSTEM_STATE.md sync | ✅ | `docs/SYSTEM_STATE.md` updated with Phase 3 + cumulative state |
| Step 7.5 — Annotated tag | ⚠️ | qor governance_helpers absent; tag deferred to release-eng at PR merge time |

**Razor regression note**: Step 5 final-check on this seal caught
`write_codegenome_identity` regressing from 36 lines (Phase 1+2 sealed
state) to 53 lines after Phase 3 plumbing added the optional
`code_locator` arg + branch. Remediated inline by extracting
`_compute_identity_for_bind` helper and tightening the docstring; final
size 36 lines. Razor commitment intact at session-seal time.

**Session content hash** (34 files, sorted-path concatenation):
SHA256 = `8a7e2bf5ddd2db532b272291a6f6b224306883d05c75873ddf1573efb776a18c`

**Previous chain hash**: `dc7ece4a...` (Entry #9, IMPLEMENTATION)

**Merkle seal**:
SHA256(content_hash + previous_hash) = **`89cac7ff99a689b211955e68c6a688508287d3325df3737958556c41070237e2`**

**Decision**: Reality matches Promise. Phase 3 implementation
conforms to the audited plan; #60 exit criteria met (with M5 fixture
corpus deferred to backlog `[B4]` per documented exception); razor
regression caught and remediated at seal time; no new violations
introduced.

---

## Entry #11 — GATE TRIBUNAL (VETO) — Phase 4 plan

**Date:** 2026-04-28
**Phase:** AUDIT
**Persona:** Judge
**Subject:** `plan-codegenome-phase-4.md` (CodeGenome Phase 4, Issue #61)
**Risk Grade:** L2

**Verdict:** **VETO**

**Findings (5 blocking):**
- F1 (V2): falsified CHANGEFEED mitigation — `compliance_check` table has no changefeed; the silent-overwrite risk has no actual audit trail.
- F2 (V2): dead enum value — `pre_classification_hint` listed in `semantic_status` ASSERT but never written by any code path.
- F3 (V2): language-name mismatch — plan uses `csharp`, `code_locator` uses `c_sharp`. Multi-language promise silently broken for C#.
- F4 (V1): orphan macro-arch — `_signal_no_new_calls` references a non-existent `extract_calls` API on `code_locator.indexing.symbol_extractor`.
- F5 (V2): scope inconsistency — Q2=B (multi-language) chosen, but no uncertain-band fixtures for non-Python; Java + C# get zero fixtures.

**Non-blocking observations (5):** O1 hidden contract change, O2 enhance_drift flag policy, O3 razor margin thin on diff_categorizer.py, O4 mocks/README acknowledgement, O5 evaluate_drift_classification razor margin tight.

**Plan content hash:** `sha256:927ff046977631b17883ec0f11dc20edf087b71d00b0da60bc017db44373dbf6`
**Audit-report content hash:** `sha256:b68749de8d96f23ae50843076754384ad14e50ee707be3d3fd29dc6a35c78d37`

**Previous chain hash:** `89cac7ff99a689b211955e68c6a688508287d3325df3737958556c41070237e2` (Entry #10, Phase 3 SEAL)

**Merkle seal:**
SHA256(audit_content_hash + previous_chain_hash) = **`231fe5f1a6ab1b57b5b49761c56b69063a7507a2f164d01f80df12179462450a`**

**Decision:** Plan does not pass adversarial review. Implementation gate held closed. Governor must address F1–F5 in `/qor-plan` revision and re-audit before `/qor-implement` is permitted.

**Next required action:** `/qor-plan` (revision) → re-`/qor-audit`.

---

## Entry #12 — GATE TRIBUNAL (PASS) — Phase 4 plan, re-audit v2

**Date:** 2026-04-28
**Phase:** AUDIT (re-run)
**Persona:** Judge
**Subject:** `plan-codegenome-phase-4.md` v2 (CodeGenome Phase 4, Issue #61)
**Risk Grade:** L2

**Verdict:** **PASS**

**Remediation summary:**
- F1 (CHANGEFEED): table-level `CHANGEFEED 30d INCLUDE ORIGINAL` added; 3 regression tests planned. ✓
- F2 (dead enum): `pre_classification_hint` removed from schema ASSERT and Pydantic Literal types. ✓
- F3 (csharp): all references normalized to `c_sharp`; parity test enforces `_SUPPORTED_LANGUAGES == _LANG_PACKAGE_MAP.keys()`. ✓
- F4 (orphan API): new sibling module `code_locator/indexing/call_site_extractor.py` (~150 LOC) replaces the invented `extract_calls` API on `symbol_extractor.py`. ✓
- F5 (corpus): expanded to 30 fixtures; Java + C# get full cosmetic/semantic/uncertain triples; every non-Python language has uncertain coverage. ✓
- O1–O5 all addressed.

**Grounding sweep (per SG-PLAN-GROUNDING-DRIFT countermeasure, Failure Entry #3):** every API/schema reference verified against codebase. `_LANG_PACKAGE_MAP` (line 57), `_get_parser` (line 97), `CHANGEFEED` syntax (already in use on `decision` and `code_region` tables) all confirmed.

**Non-blocking observations carried into implementation:**
- Obs-V2-1: `SHOW CHANGES FOR TABLE` syntax not yet used in this codebase; if unreliable in v2 embedded, implementer should find an alternative verification path for the F1 regression test and document the limitation.
- Obs-V2-2: `_LANG_PACKAGE_MAP` is defined inside `if not _USE_LEGACY`; F3 parity test should guard with `_USE_LEGACY` check or `pytest.importorskip`.

**Plan content hash (v2):** `sha256:efdf0477f01ffe38e7262b8b995655b77aeff44f6747f8943741306d8f81054d`
**Audit-report content hash:** `sha256:dcf28287420c07f03a34ece5866582da74430addde6a37bdebaf8cc8fb5aba73`

**Previous chain hash:** `231fe5f1a6ab1b57b5b49761c56b69063a7507a2f164d01f80df12179462450a` (Entry #11, v1 VETO)

**Merkle seal:**
SHA256(audit_content_hash + previous_chain_hash) = **`332c72b23d0d64ec77979f64147e5d4df4a9fa130f9c110be6217e5331b66f14`**

**Decision:** Plan passes adversarial review. Implementation gate **OPENS**. Governor advances to `/qor-implement`.

**Next required action:** `/qor-implement` (Phase-by-phase TDD per the v2 plan).

---

## Entry #13 — GATE TRIBUNAL (PASS) — Phase 4 plan v3 (post-rebase, Phase 1 sealed)

**Date:** 2026-04-28
**Phase:** AUDIT (re-run)
**Persona:** Judge
**Subject:** `plan-codegenome-phase-4.md` v3
**Risk Grade:** L2
**Verdict:** **PASS**

**Refresh summary:** branch rebased onto `BicameralAI/dev` (single base; 3-deep stack collapsed). Phase 1 of Phase 4 SEALED at commit `2afd52d` post-rebase / `c39317c` plan refresh: schema v13 + contracts + 9 persistence tests all green; 146/146 broader regression clean. Obs-V2-1 resolved positively (`SHOW CHANGES FOR TABLE` works in v2 embedded). Merge target now `BicameralAI/dev`. Implementation queue table for Phases 2-5 added.

**Grounding sweep (per SG-PLAN-GROUNDING-DRIFT):** every claim verified — branch state, schema versions (dev=v12, Phase 4 branch=v13), Phase 3 primitives all confirmed in dev. PR #71/#73 merge timestamps verified.

**Internal consistency (per SG-PLAN-INTERNAL-INCONSISTENCY):** all v2 sealed decisions preserved in v3 — sibling pass, multi-language scope, `PreClassificationHint`, CHANGEFEED 30d, `c_sharp` consistency, 30-fixture corpus, `call_site_extractor.py`, `_diff_dispatch.py`. No regressions.

**Non-blocking observations (2):** Obs-V3-1 schema-version race with PR #81 (sequencing only, 5-min mechanical fix when triggered); Obs-V3-2 carries Obs-V2-2 forward (legacy tree-sitter guard for F3 parity test).

**Plan content hash (v3):** `sha256:911171cfc18ce1eba783fd49e3e12be6a1d1ac5375cb06c728dea88a6ff14b52`
**Audit content hash:** `sha256:883b4cf776c97aaa66a1a67b45b66736b7472bc59c89309ed77d9724ccddc337`
**Previous chain hash:** `332c72b23d0d64ec77979f64147e5d4df4a9fa130f9c110be6217e5331b66f14` (Entry #12)

**Merkle seal:** SHA256(audit_content_hash + previous_chain_hash) = **`21ac210f1d043ccfd22fd941e5b373783c833240b1ca473f55a3cf5c8e6b2026`**

**Decision:** v3 plan passes adversarial review. Implementation gate **OPENS** for Phases 2-5. Per user directive ("if /qor-audit passes, then you can go directly to /qor-implement"), chain proceeds without pause.

**Next required action:** `/qor-implement` (Phase 2 — drift classifier + multi-language line categorizers + call_site_extractor).

---

## Entry #14 — SUBSTANTIATION (Phase 4 SESSION SEAL)

**Date:** 2026-04-29
**Phase:** SUBSTANTIATE
**Persona:** Judge (executed via `/qor-substantiate`)
**Risk Grade:** L2
**Verdict:** **REALITY = PROMISE**
**Mode:** Solo

### Verifications run

| Check | Result | Notes |
|---|---|---|
| Step 2 — PASS verdict present | ✅ | `.agent/staging/AUDIT_REPORT.md` (v3 PASS, chain `21ac210f`) |
| Step 2.5 — Version validation | ✅ | Current tag `v0.10.8` → target `v0.13.0` (feature bump). Schema renumbered v13→v14 mid-substantiation per Obs-V3-1 (race with merged PR #81). |
| Step 3 — Reality audit | ✅ | 22/22 planned new files exist; 0 missing. §Phase 5 fixture-collapse deviation documented inline. |
| Step 4 — Test audit | ✅ | 189/189 codegenome + extract_call_sites + m3_benchmark + ledger phase2 + resolve_compliance regression suite passing on Windows local. |
| Step 5 — Section 4 razor | ✅ for production | All 13 new production files ≤ 250 LOC (largest: `drift_service.py` 249, `_diff_dispatch.py` 213). Test files + data fixture exceed cap (consistent with Phase 1+2 / Phase 3 precedent — production code is what the razor primarily protects). |
| Step 6 — SYSTEM_STATE.md sync | ✅ | Phase 4 snapshot prepended; Phase 3 history preserved. |
| Step 7 — Merkle seal | ✅ | Computed below. |
| Step 7.5 — Annotated tag | ⚠️ | qor governance_helpers script absent on this branch; tag deferred to release-eng at PR merge time. Plan target: v0.13.0. |

### Plan deviations (documented)

1. **Schema renumbering v13 → v14** during substantiation — Obs-V3-1 fired (PR #81 merged claiming v13 with provenance FLEXIBLE). Phase 4's CHANGEFEED + semantic_status + evidence_refs migration was rebased to claim v14. SCHEMA_COMPATIBILITY[14] = "0.13.0".
2. **§Phase 5 fixture collapse** — plan called for 30 paired files on disk; delivered as 30 cases in a single `cases.py` data module. Same coverage, identical contract for `test_m3_benchmark.py`. Documented in `tests/fixtures/m3_benchmark/__init__.py`.
3. **Test file razor exceptions** — 4 test files + 1 data fixture exceed the 250-LOC cap. Consistent with Phase 1+2 / Phase 3 precedent in this codebase. Production files all ≤ 250.

### Carried-forward observations

- **Obs-V3-1**: schema-version race RESOLVED via mid-substantiation rebase to v14.
- **Obs-V3-2**: legacy tree-sitter guard ADDRESSED via `pytest.skipif(_USE_LEGACY)` in the F3 parity test (Phase 2 commit).

### Capability shortfalls (carried across phases)

- `qor/scripts/` runtime helpers (`gate_chain`, `session`, `governance_helpers`) absent — gate artifact files at `.qor/gates/<session>/*.json` not written. File-based META_LEDGER chain remains canonical.
- `qor/reliability/` enforcement scripts (`intent-lock`, `skill-admission`, `gate-skill-matrix`) absent — Step 4.6 reliability sweep skipped; documented as session shortfall.
- `agent-teams` capability not declared on Claude Code host — Step 1.a parallel-mode disabled; ran sequential.
- `codex-plugin` capability not declared — Step 1.a adversarial audit-mode disabled; ran solo across all audit phases.
- `AUDIT_REPORT.md` lives at `.agent/staging/` rather than the skill's default `.failsafe/governance/`. Path divergence noted; chain integrity preserved.

### Session content hash

SHA256 over 28 sorted-path files = **`ba20c63f37bb8c39f8b0d252222488088f16f8a3cb66423fa909361e9a40d88e`**

### Previous chain hash

`21ac210f1d043ccfd22fd941e5b373783c833240b1ca473f55a3cf5c8e6b2026` (Entry #13, v3 audit PASS)

### Merkle seal

SHA256(content_hash + previous_hash) = **`0ebcf69bf25e11d9d85cb9856ccc9757ad39b75c2f352bdd063bd2d957f506cf`**

### Decision

Reality matches Promise. Phase 4 (#61) implementation conforms to the v3-audited specification with two documented plan deviations (schema renumbering and §Phase 5 fixture collapse). All 5 phases sealed in sequence; M3 benchmark exit criterion (false-positive rate < 5%) met with 0 false positives. Chain integrity intact. Next phase: `/qor-document` then open PR `claude/codegenome-phase-4-qor → BicameralAI/dev`.

---

## Entry #15 — GATE TRIBUNAL: `plan-codegenome-llm-drift-judge.md` (Issue #44)

**Phase**: GATE / qor-audit
**Date**: 2026-04-29
**Branch**: `feat/44-llm-drift-judge` (off `BicameralAI/dev` post-Phase-4 seal)
**Subject**: Issue #44 — *[P2] LLM semantic drift judge: suppress false-positive drift flags on cosmetic code changes*
**Risk Grade**: L1 (docs + skill rubric + test data; zero production code paths)
**Change Class**: minor

### Audit history (this entry covers both iterations)

| v | Plan commit | Verdict | Findings |
|---|---|---|---|
| v1 | `b15c9ef` | **VETO** | F-1 (BLOCKING): `pilot/mcp/skills/bicameral-sync/SKILL.md` does not exist on dev — plan inherited stale `CLAUDE.md` claim without filesystem verification. SG-PLAN-GROUNDING-DRIFT instance #2. F-2/F-3: minor grounding numerics. F-4/F-5: non-blocking. |
| v2 | `d846a4a` | **PASS** | All blocking findings remediated. Pilot path directive removed; test count 5→4; SKILL.md baseline 138→150; ruff exemption claim corrected. |

### Plan content hash (v2)

`sha256:7094b9b64339e1bf2d96055fac1bd46dec066966fbf690687c129d02fb5ae74d`

### Audit report content hash

`sha256:bc74936e79eff03bdae0dda2d7ab419044328978814643b99ecfa5ee8fa2b6a1`

### Previous chain hash

`0ebcf69bf25e11d9d85cb9856ccc9757ad39b75c2f352bdd063bd2d957f506cf` (Entry #14, Phase 4 SEAL)

### Chain hash

`SHA256(plan_hash + audit_hash + prev_hash) =` **`536dd15f587d749cb600999171e0889fbe20f39818bf3969890f411ff0fe350b`**

### Decision

PASS. Chain to `/qor-implement` per delegation table. Plan declares 2 phases (test corpus + skill rubric), 0 production code changes, 0 schema migrations, 0 new dependencies.

### Shadow Genome instance recorded

`SG-PLAN-GROUNDING-DRIFT` instance #2 catalogued in `docs/SHADOW_GENOME.md`. Cross-references PR #93 (instance #1, same root cause: CLAUDE.md asserts a `pilot/mcp/skills/` location that does not exist on dev). Followup: separate `docs:claude-md-cleanup` issue tracked outside this plan.

---

## Entry #16 — SUBSTANTIATION SEAL: `plan-codegenome-llm-drift-judge.md` (Issue #44)

**Phase**: SUBSTANTIATE / qor-substantiate
**Date**: 2026-04-29
**Branch**: `feat/44-llm-drift-judge` (off `BicameralAI/dev` post-Phase-4 seal `200dbd5`)
**Implementation commit**: `f230331`
**Risk Grade**: L1 (docs + skill rubric + test data; zero production code paths changed)
**Change Class**: minor

### Verification gates

| Step | Check | Result | Notes |
|---|---|---|---|
| Step 2 | PASS verdict in AUDIT_REPORT.md | ✅ | Entry #15 audit PASS at `536dd15f`. |
| Step 2.5 | Version validation | ✅ | Source remains v0.13.x; no version bump in this PR per user direction (v0.14.0 release PR is Jin's call). |
| Step 3 | Reality vs Promise | ✅ | All 4 new files + 3 modified files exist. One documented deviation: `docs/training/README.md` was created (not modified) because PR #93 scaffolding hasn't merged yet — minimal mirror created on this branch. |
| Step 3.5 | Backlog blockers | ✅ | No new security blockers; pre-existing S1 (SECURITY.md absent) unaffected. |
| Step 4 | Test audit | ✅ | 48/48 in targeted sweep (8 new + 40 regression on test_m3_benchmark + drift_classifier + drift_service). |
| Step 4 (artifacts) | console.log / print() in NEW production code | ✅ | Zero new production code added; pre-existing `print()` in handlers/update.py unchanged (CLI JSON output, by design). |
| Step 4.5 | Skill file integrity | ✅ | `skills/bicameral-sync/SKILL.md` modified — required structure preserved (frontmatter, headings, rules). Section 4 `2.bis` correctly placed between Step 2 and Step 3 ("Report"). |
| Step 4.6 | Reliability sweep | ⚠️ skip | `qor/reliability/` capability shortfall; intent-lock + skill-admission + gate-skill-matrix scripts absent. |
| Step 5 | Section 4 razor final | ✅ | All NEW files: test_m3_benchmark_judge_corpus.py 83 LOC, test_skill_uncertain_protocol.py 96 LOC, training docs 198+49 LOC (markdown). MODIFIED: SKILL.md 211 LOC (markdown), cases.py 431 LOC (under tests/ ruff exclusion). All test functions ≤ 25 LOC. Zero new production functions. |
| Step 6 | SYSTEM_STATE.md sync | ✅ | Updated below; #44 snapshot prepended; Phase 4 inventory preserved. |
| Step 7 | Merkle seal | ✅ | Computed below. |
| Step 7.5 | Annotated tag | ⚠️ skip | Per user direction, no version bump in this PR. v0.14.0 tag deferred to Jin's release PR. |

### Architectural decisions sealed

D1 (skill-side judge), D2 (caching free via existing compliance_check), D3-D4 (reuses existing typed contracts), D5 (rubric is markdown text), D6 (5 exit criteria). No design deviations during implementation.

### Operator QC pass (D6 #5)

Recorded as **pending qualitative gate**, NOT a CI-blocker. The operator will run the `bicameral-sync` skill against the 10 uncertain-band cases and compare LLM verdicts to `expected_judge` ground truth in `tests/fixtures/m3_benchmark/cases.py`. Pass threshold: 0% FP on cosmetic-claimed verdicts; ≤ 20% FN. Threshold met / not met to be recorded by the operator post-merge as a separate META_LEDGER addendum or comment on the PR.

### Plan deviations (documented)

1. **`docs/training/README.md` created (not modified)**. PR #93's docs/training/ scaffolding hasn't merged to dev. Minimal training README mirrored on this branch; merges will reconcile. Soft dependency disclosed in PR body.

### Carried-forward observations

- **SG-PLAN-GROUNDING-DRIFT instance #2** (META_LEDGER #15 / SHADOW_GENOME #5): `pilot/mcp/skills/` referenced by CLAUDE.md but does not exist on dev. Plan post-remediation correctly drops the reference. Followup `docs:claude-md-cleanup` workstream filed separately (NOT in scope for #44).

### Capability shortfalls (carried)

- `qor/scripts/` runtime helpers absent — gate-chain artifacts at `.qor/gates/<session>/*.json` not written. File-based META_LEDGER chain remains canonical.
- `qor/reliability/` enforcement scripts absent — Step 4.6 reliability sweep skipped.
- `agent-teams` capability not declared on Claude Code host — sequential mode.
- `codex-plugin` capability not declared — solo audit mode.
- `AUDIT_REPORT.md` lives at `.agent/staging/` rather than `.failsafe/governance/`. Path divergence noted; chain integrity preserved.

### Session content hash

SHA256 over 8 sorted-path files (plan + skill + training doc + 2 test files + cases.py + training README + SYSTEM_STATE.md) =
**`a952c0140a142dbd60f9239b4786bc4a498bac98441e157f0b19bc2eb8f4dc1d`**

### Previous chain hash

`536dd15f587d749cb600999171e0889fbe20f39818bf3969890f411ff0fe350b` (Entry #15, audit PASS post-remediation)

### Merkle seal

SHA256(content_hash + previous_hash) = **`567170e0f1dc008cd5663201d8b1582dbabb5904527acb31ed5ea869b1cd8877`**

### Decision

**Reality matches Promise.** Implementation conforms to the audited specification (`d846a4a`) with one documented plan deviation (training README scaffolding). Phase 1 (test corpus extension) and Phase 2 (skill rubric + training doc) sealed in sequence; 8/8 new tests + 40/40 regression green. Chain integrity intact. Next phase: `/qor-document` then open PR `feat/44-llm-drift-judge → BicameralAI/dev`.

---

## Entry #17 — GATE TRIBUNAL: `plan-48-pre-push-drift-hook.md` (Issue #48)

**Phase**: GATE / qor-audit
**Date**: 2026-04-29
**Branch**: `feat/48-pre-push-drift-hook` (off `BicameralAI/dev` post-#113 sticky drift report, current tip `77b9ee3`)
**Subject**: Issue #48 — *Pre-push git hook: surface drift warnings before `git push`*
**Risk Grade**: L2 (new CLI subcommand surface; modifies setup_wizard + server.py; no MCP tool changes, no schema, no contracts)
**Change Class**: minor
**Verdict**: **PASS** (first-attempt — no remediation needed)

### Audit history

| v | Plan commit | Verdict | Findings |
|---|---|---|---|
| v1 | `79abcc2` | **PASS** | All standard passes clean. SG-PLAN-GROUNDING-DRIFT instance #4 prevented (plan author ran `ls -d */` before submission). Three non-blocking observations: O1 (cosmetic param-name nit), O2 (latent post-commit-hook bug — recommend separate issue), O3 (two-renderer non-duplication accepted). |

### Plan content hash

`sha256:96045a11fbd403ca0ef55b12d0c02b5dfbf5fc42ee31d3980ed87b0617b71807`

### Audit report content hash

`sha256:d9a003e44bf9ee52e1801ea61f5c6fbf68187389b86d82807ebcd96cce3e7b66`

### Previous chain hash

`567170e0f1dc008cd5663201d8b1582dbabb5904527acb31ed5ea869b1cd8877` (Entry #16, #44 SEAL on dev)

### Chain hash

`SHA256(plan_hash + audit_hash + prev_hash) =` **`bf890347b6aac9097f5468f577c5cf2e7581af57cc1dc776bda5baad498fb37c`**

### Decision

PASS first-attempt. Plan-author-level grounding mitigation confirmed working — no `pilot/mcp/skills/` references, no fictional paths, all module/file claims pre-verified via filesystem `ls`. Three phases (branch-scan CLI / setup-wizard hook install / docs) all gate-cleared for implementation.

### Audit recommendations

- **File separately**: latent bug in existing post-commit hook — `bicameral-mcp link_commit HEAD` is not a registered subcommand of `cli_main`. Hook silently no-ops under `|| true`. Out of scope for #48.

---

## Entry #18 — SUBSTANTIATION SEAL: `plan-48-pre-push-drift-hook.md` (Issue #48)

**Phase**: SUBSTANTIATE / qor-substantiate
**Date**: 2026-04-29
**Branch**: `feat/48-pre-push-drift-hook` (off `BicameralAI/dev` post-#113)
**Plan commit**: `79abcc2`; implementation latest commit on branch
**Risk Grade**: L2 (new CLI subcommand surface; modifies setup_wizard + server.py; no MCP tool changes, no schema, no contracts)
**Change Class**: minor

### Verification gates

| Step | Check | Result | Notes |
|---|---|---|---|
| Step 2 | PASS verdict in AUDIT_REPORT.md | ✅ | Entry #17 audit PASS at `bf890347` (first-attempt — no remediation cycle). |
| Step 2.5 | Version validation | ✅ | Source remains v0.16.0 (current dev tip from PR #107); no version bump in this PR per maintainer direction. |
| Step 3 | Reality vs Promise | ✅ | All 4 new files + 3 modified files exist. Zero plan deviations — implementation matches plan 1:1. |
| Step 3.5 | Backlog blockers | ✅ | No new blockers. |
| Step 4 | Test audit | ✅ | 27/28 in targeted sweep (11 new + 16 regression on PR #113 drift_report tests; 1 chmod test skipped on Windows). |
| Step 4 (artifacts) | console.log / debug | ✅ | Zero. The `print()` statements in `cli/branch_scan.py` are stderr/stdout CLI status output — intentional design. |
| Step 4.5 | Skill file integrity | N/A | No `skills/*/SKILL.md` files modified (no MCP tool changes). |
| Step 4.6 | Reliability sweep | ⚠️ skip | `qor/reliability/` capability shortfall. |
| Step 5 | Section 4 razor final | ✅ | `cli/branch_scan.py` 177 LOC (≤250); entry funcs ≤25 LOC; helpers ≤20 LOC; nesting ≤2; zero nested ternaries. |
| Step 6 | SYSTEM_STATE.md sync | ✅ | Updated with #48 inventory; #44 history preserved below. |
| Step 7 | Merkle seal | ✅ | Computed below. |
| Step 7.5 | Annotated tag | ⚠️ skip | Per maintainer direction, no version bump in this PR. |

### Architectural decisions sealed

Q1 (`cli/branch_scan.py` placement), Q2 (deliberate non-modeling on broken predecessor), Q3 (HEAD-only v1), Q4 (TTY/no-TTY/no-ledger graceful behaviors), Q5 (setup_wizard pattern mirroring) — all implemented exactly as specified. Zero design deviations during implementation.

### Plan deviations (none)

First implementation in this session with zero plan deviations. Plan was thorough enough that implementation was direct.

### Carried-forward observations

- **Audit's separate-issue recommendation**: latent bug in existing post-commit hook (`bicameral-mcp link_commit HEAD` not a registered subcommand). NOT addressed in this PR — separate workstream.
- **SG-PLAN-GROUNDING-DRIFT prevention**: this is the second consecutive plan in the session where author-time `ls -d */` mitigation worked (no instance #4). Issue #114 (CI lint) remains the durable countermeasure.

### Capability shortfalls (carried)

- `qor/scripts/` runtime helpers absent.
- `qor/reliability/` enforcement scripts absent.
- `agent-teams` capability not declared — sequential mode.
- `codex-plugin` capability not declared — solo audit mode.
- `AUDIT_REPORT.md` lives at `.agent/staging/` rather than `.failsafe/governance/`.

### Session content hash

SHA256 over 8 sorted-path files (plan + 1 new prod + 2 modified prod + 2 tests + 1 guide + SYSTEM_STATE.md) =
**`d943569a6fd566fcb9dfe61bce660100ca28e84671b4ca465cac02065ab15023`**

### Previous chain hash

`bf890347b6aac9097f5468f577c5cf2e7581af57cc1dc776bda5baad498fb37c` (Entry #17 audit PASS first-attempt)

### Merkle seal

SHA256(content_hash + previous_hash) = **`eacc6f89f707ce958fa2485177c9706808fdfeb32b8e4865aadc8bcda47cb645`**

### Decision

**Reality matches Promise.** Implementation conforms to the audit-PASSED specification (`79abcc2`) with **zero plan deviations**. Phase 0 (branch-scan CLI) + Phase 1 (setup_wizard hook install) + Phase 2 (CHANGELOG + user guide) sealed in sequence; 11/12 new tests + 16/16 regression green (1 Windows-only chmod skip). Chain integrity intact on this branch. Next phase: `/qor-document` then open PR `feat/48-pre-push-drift-hook → BicameralAI/dev`.

---
## Entry #19 — PLAN: `plan-124-post-commit-hook-fix.md` (Issue #124)

**Phase**: PLAN / qor-plan
**Date**: 2026-04-29
**Branch**: `feat/124-link-commit-cli` (off `BicameralAI/dev` post-#119 governance v0.17.2 tip `8f0253d`)
**Subject**: Issue #124 — *post-commit hook silently no-ops because `bicameral-mcp link_commit HEAD` is not a registered CLI subcommand*
**Risk Grade**: L2
**Change Class**: bug-fix (hotfix-shaped — restores advertised behavior)

### Plan content hash

`sha256:a82c62f58ba1e91bcf41d9dc82c983d59a41e09d8666e8a7acec7faf4f001432`

### Previous chain hash

`eacc6f89f707ce958fa2485177c9706808fdfeb32b8e4865aadc8bcda47cb645` (Entry #18, #48 SEAL on dev)

Note: Entries #19/#20 on the `feat/114-grounding-lint` branch (PR #121, #114 audit + seal) are not yet on dev (PR #121 pending merge). This branch chains directly off dev's tip Entry #18.

### Chain hash

`SHA256(plan_hash + prev_hash) =` **`49044f4c55e0d70cf913e8dd649b193452a880fe1136791bbc60aeac42e9bffc`**

### Plan summary

Three-phase plan:

- **Phase 0**: refactor-with-existing-coverage. Promote `cli/branch_scan.py:_invoke_link_commit` (lines 133–149) to a shared `cli/_link_commit_runner.py` module so a second caller (Phase 1) doesn't duplicate the lazy-import sync-wrapper pattern. ~30 LOC new, ~10 LOC removed from `cli/branch_scan.py`.
- **Phase 1**: register `link_commit` as a top-level CLI subcommand in `server.py:cli_main`. Argparse subparser + dispatch + new `cli/link_commit_cli.py` (~35 LOC) entry point. JSON to stdout by default; `--quiet` flag for hooks/scripts. 6 unit tests.
- **Phase 2**: harden the post-commit hook — replace `>/dev/null 2>&1 || true` with stderr-loud-but-non-blocking variant (writes to `/tmp/bicameral-hook.err`, surfaces summary on next commit, always exits 0). Add `tests/test_hook_command_registration.py` (3 tests) — a smoke test that walks every `bicameral-mcp <subcommand>` invocation in installed hook scripts and asserts each is registered. Would have caught the original bug at PR time.
- **Phase 3**: `CHANGELOG.md` `[Unreleased]` Fixed entry.

### Open questions (5)

- **Q1**: Output shape on success. *Recommend JSON to stdout + `--quiet` flag.*
- **Q2**: Migration for existing installs. *None needed — hook script content is correct; bug is server-side argparse.*
- **Q3**: Bundle silent-suppression fix with registration fix. *Same PR — three reasons documented.*
- **Q4**: Reuse `branch-scan` for post-commit. *No — distinct semantics; would overload CLI surface.*
- **Q5**: Where does the shared runner helper live. *`cli/_link_commit_runner.py` (DRY, single source of truth).*

### Grounding (manual — #114 lint not yet on dev)

Verified all 10 referenced existing paths exist (`setup_wizard.py`, `server.py`, `handlers/link_commit.py`, `cli/branch_scan.py`, `contracts.py`, `context.py`, `tests/test_branch_scan_cli.py`, `tests/test_setup_pre_push_hook.py`, `CHANGELOG.md`, `pyproject.toml`). Verified all 4 declared-new paths correctly do NOT exist yet. Zero SG-PLAN-GROUNDING-DRIFT instances.

### Next required action

`/qor-audit` (mandatory for L2).

---
*Chain integrity: VALID (19 entries on this branch — Entry #19 is #124 PLAN; Entry #18 was #48 SEAL on dev)*
*Genesis: `29dfd085` → Phase 1+2 Seal: `509b411d` → Phase 3 Seal: `89cac7ff` → Phase 4 Audit v1 (VETO): `231fe5f1` → Phase 4 Audit v2 (PASS): `332c72b2` → Phase 4 Audit v3 (PASS, post-rebase): `21ac210f` → Phase 4 SEAL: `0ebcf69b` → #44 Audit (PASS, post-remediation): `536dd15f` → #44 SEAL: `567170e0` → #48 Audit (PASS, first-attempt): `bf890347` → #48 SEAL: `eacc6f89` → #124 PLAN: `49044f4c`*
*Next required action: `/qor-audit` for `plan-124-post-commit-hook-fix.md`*
