# System State — post-#48-substantiation snapshot

**Generated**: 2026-04-29
**HEAD**: latest (Issue #48 sealed)
**Branch**: `feat/48-pre-push-drift-hook` (off `BicameralAI/dev` post-#113, current dev tip `77b9ee3`)
**Tracked PR**: will target `BicameralAI/dev` (Issue #48); aggregate `dev → main` PR is downstream
**Genesis hash**: `29dfd085...`
**#48 seal**: see Entry #18 (computed during this substantiation)

## #48 (pre-push drift hook + branch-scan CLI) implementation — 7 files, ~609 LOC, 11 new tests, 27/28 targeted regression

| Phase | Files | New tests | Notes |
|---|---|---|---|
| 0 — branch-scan CLI subcommand | 1 new prod + 1 new test + 1 modified | 7 | `cli/branch_scan.py` 177 LOC, server.py +14 LOC |
| 1 — setup_wizard pre-push hook | 1 modified + 1 new test | 5 (1 chmod skipped on Windows) | setup_wizard.py +50 LOC, --with-push-hook flag |
| 2 — Documentation | 2 modified/new | 0 | CHANGELOG [Unreleased] + 129-LOC user guide |

### Files in scope

**New** (4):
- `cli/branch_scan.py` (177 LOC) — terminal-output drift renderer + main() CLI
- `tests/test_branch_scan_cli.py` (144 LOC, 7 tests)
- `tests/test_setup_pre_push_hook.py` (92 LOC, 5 tests)
- `docs/guides/pre-push-drift-hook.md` (129 LOC) — user guide
- `plan-48-pre-push-drift-hook.md` (366 LOC) — plan, committed at `79abcc2`

**Modified** (3):
- `server.py` (+14 LOC, branch-scan subparser + --with-push-hook flag)
- `setup_wizard.py` (+50 LOC, _GIT_PRE_PUSH_HOOK + _install_git_pre_push_hook + run_setup kwarg + step 7b)
- `CHANGELOG.md` (Unreleased entry under Added)

### Plan deviations (none)

Implementation matches plan 1:1. All design decisions Q1–Q5 implemented exactly as specified.

### Architectural decisions retained from plan

- **Q1**: `cli/branch_scan.py` placement (mirrors `cli/classify.py` and `cli/drift_report.py` patterns).
- **Q2**: Deliberate non-modeling on possibly-broken post-commit-hook predecessor — `branch-scan` registered properly via `cli_main` subparser.
- **Q3**: HEAD-only v1 (no multi-commit-range walk); v2 tracked as future enhancement.
- **Q4**: TTY/no-TTY/no-ledger graceful behaviors — all three branches implemented per spec.
- **Q5**: setup_wizard pattern mirrors `_install_git_post_commit_hook` exactly (idempotent install, append-on-existing).

### Capability shortfalls (carried across phases)

- `qor/scripts/` runtime helpers absent — gate-chain artifacts not written.
- `qor/reliability/` enforcement scripts absent — Step 4.6 reliability sweep skipped.
- `agent-teams` capability not declared — sequential mode.
- `codex-plugin` capability not declared — solo audit mode.
- v1 audit was first plan in session where SG-PLAN-GROUNDING-DRIFT prevention worked at *author-time* rather than audit-time. Issue #114 (CI lint enforcement) remains the durable countermeasure.

### Test state (post-implementation)

- Targeted sweep: 27/28 (11 new + 16 regression on PR #113's drift_report tests; 1 chmod test skipped on Windows non-POSIX).
- All test functions ≤ 25 LOC.
- All test files ≤ 144 LOC.
- ruff check + format: clean.
- mypy on `cli/branch_scan.py`: no issues.
- End-to-end smoke confirmed: `python -m server branch-scan` → graceful skip → exit 0 (no ledger configured locally).

### Workflow security review

- Hook reads `/dev/tty` for the prompt; input matched against fixed regex (`[yY]|[yY][eE][sS]`); no shell expansion of user-controlled input.
- Hook calls `bicameral-mcp branch-scan` from `PATH` — same trust model as the existing post-commit hook.
- No `pull_request_target` triggers introduced.
- File mode `0o755` (executable, world-readable). No secrets in hook content.
- Behavior: hook short-circuits (`exit 0`) when no `.bicameral/` directory in repo.

### Audit's separate-issue recommendation (NOT addressed in this PR)

Latent bug in existing post-commit hook: `bicameral-mcp link_commit HEAD` is not a registered subcommand of `cli_main`. The `|| true` swallows the argparse error. Recommended title: *"post-commit hook command bicameral-mcp link_commit HEAD not a registered CLI subcommand — hook silently no-ops"*. Out of scope for #48; tracked separately.

---

# System State — post-#44-substantiation snapshot

**Generated**: 2026-04-29
**HEAD**: `f230331` (#44 implementation sealed)
**Branch**: `feat/44-llm-drift-judge` (off `BicameralAI/dev` post-Phase-4 seal `200dbd5`)
**Tracked PR**: will target `BicameralAI/dev` (Issue #44); aggregate `dev → main` PR is downstream
**Genesis hash**: `29dfd085...`
**#44 seal**: see Entry #16 (computed during this substantiation)

## #44 (LLM drift judge) implementation — 7 files, ~549 LOC, 8 new tests, 40/40 targeted regression

| Phase | Files | New tests | Commit |
|---|---|---|---|
| 1 — M3 benchmark `expected_judge` ground-truth labels | 1 new + 1 modified | 4 | `f230331` |
| 2 — bicameral-sync §2.bis Uncertain-band sub-protocol + training doc | 1 new test + 1 modified skill + 2 new docs | 4 | `f230331` |

### Files in scope

**New** (5):
- `tests/test_m3_benchmark_judge_corpus.py` (83 LOC, 4 tests)
- `tests/test_skill_uncertain_protocol.py` (96 LOC, 4 tests)
- `docs/training/cosmetic-vs-semantic.md` (198 LOC, training doc)
- `docs/training/README.md` (49 LOC, training index — soft-deps on PR #93)
- `plan-codegenome-llm-drift-judge.md` (417 LOC, plan; committed at `b15c9ef`/`d846a4a`)

**Modified** (3):
- `tests/fixtures/m3_benchmark/cases.py` (391 → 431 LOC, expected_judge added to 10 uncertain cases)
- `skills/bicameral-sync/SKILL.md` (150 → 211 LOC, §2.bis Uncertain-band sub-protocol)
- `CHANGELOG.md` ([Unreleased] entry under Added)

### Plan deviations (documented)

1. **`docs/training/README.md` created on this branch** rather than modified — the PR #93 docs scaffolding hasn't merged to dev yet, so the training/ directory was empty on the fork-point. Created a minimal version that mirrors PR #93's intended structure; merges will reconcile via standard merge when one or both PRs land.

### Architectural decisions retained from plan (D1-D6)

- **D1**: skill-side judge (caller LLM), not server-side. Preserves docs/CONCEPT.md anti-goal "Not an LLM-powered ledger".
- **D2**: caching via existing `compliance_check` writes (Phase 4 added `semantic_status` + `evidence_refs`).
- **D3-D4**: reuses existing typed contracts (`PreClassificationHint`, `ComplianceVerdict`); no new fields.
- **D5**: rubric is data (markdown text in SKILL.md §2.bis), not code.
- **D6**: 5 exit criteria, 4 CI-checkable + 1 operator QC pass (qualitative gate).

### Capability shortfalls (carried across phases)

- `qor/scripts/` runtime helpers absent — gate-chain artifacts not written.
- `qor/reliability/` enforcement scripts absent — Step 4.6 reliability sweep skipped.
- `agent-teams` capability not declared — sequential mode.
- `codex-plugin` capability not declared — solo audit mode.
- Audit found `pilot/mcp/skills/` referenced by CLAUDE.md but does not exist on dev (SG-PLAN-GROUNDING-DRIFT instance #2 — META_LEDGER #15, SHADOW_GENOME #5). Plan post-remediation correctly drops the reference; followup workstream `docs:claude-md-cleanup` filed separately.

### Test state (post-implementation)

- Targeted sweep: 40/40 (8 new + 32 regression on test_m3_benchmark.py + test_codegenome_drift_classifier.py + test_codegenome_drift_service.py).
- All test functions ≤ 25 LOC.
- All test files ≤ 96 LOC.
- `cases.py` 431 LOC under tests/ ruff exclusion (pyproject.toml `exclude = ["tests", ...]`).

---

## Phase 4 (#61) implementation — 27 files, ~2515 LOC, 73 new tests, 189/189 regression

| Phase | Files | New tests | Commit |
|---|---|---|---|
| 1 — Schema v14 + contracts | 3 modified, 1 new test | 9 | `066a209` |
| 2 — Drift classifier + 7-lang categorizers + call_site_extractor | 12 new + 2 new tests | 35 | `7a79dc5` |
| 3 — Drift classification service | 2 new | 8 | `3a0fc8c` |
| 4 — Handler integration (link_commit + resolve_compliance) | 2 modified + 2 new tests | 14 | `6bbc687` |
| 5 — M3 benchmark corpus (30 cases × 7 languages) | 3 new | 7 | `09f30a8` |

Schema renumbered v13 → v14 during /qor-substantiate per Obs-V3-1: PR #81 (provenance FLEXIBLE) merged claiming v13 first; this Phase 4 migration shifted to v14 (compliance_check CHANGEFEED + semantic_status + evidence_refs). Plan deviation: §Phase 5 collapsed 30 paired files to a single ``cases.py`` data module — same coverage, far less file-system noise; documented in `tests/fixtures/m3_benchmark/__init__.py`.

---

## Phase 3 (#60) seal preserved below



## Files added across the project DNA chain (Phases 1-2-3)

```text
codegenome/
├── __init__.py
├── adapter.py                   # CodeGenomeAdapter ABC + 5 dataclasses
│                                # + neighbors_at_bind on SubjectIdentity (Phase 3)
├── contracts.py                 # 3 issue-mandated Pydantic models
├── confidence.py                # noisy_or, weighted_average, DEFAULT_CONFIDENCE_WEIGHTS
├── config.py                    # CodeGenomeConfig (7 flags, all default False)
├── deterministic_adapter.py     # DeterministicCodeGenomeAdapter (Phase 1+2 + Phase 3 neighbor variant)
├── bind_service.py              # write_codegenome_identity + 3 helpers (Section 4 razor split)
├── continuity.py                # Phase 3 matcher (deterministic v1 weights)
└── continuity_service.py        # Phase 3 7-step orchestrator + DriftContext

adapters/
├── codegenome.py                # get_codegenome() factory
└── code_locator.py              # +neighbors_for(file, start, end) Phase 3 protocol

tests/
├── test_codegenome_adapter.py            # ABC + dataclass + compute_identity[_with_neighbors]
├── test_codegenome_bind_integration.py   # bind path; #59 exit criteria
├── test_codegenome_confidence.py         # noisy_or + weighted_average
├── test_codegenome_config.py             # env-flag matrix
├── test_codegenome_continuity.py         # matcher (18 tests)
├── test_codegenome_continuity_ledger.py  # 4 ledger queries (8 tests)
└── test_codegenome_continuity_service.py # 7-step orchestrator (5 tests)

docs/
├── CONCEPT.md                   # project DNA Why/Vibe/Anti-Goals
├── ARCHITECTURE_PLAN.md         # L2 risk grade + flat layout map
├── META_LEDGER.md               # 9-entry chain (about to gain Entry #10 from this seal)
├── BACKLOG.md                   # +B4: M5 fixture corpus (deferred Phase 3 sub-deliverable)
├── SHADOW_GENOME.md             # 3 recorded failure modes from prior audits
├── QOR_VS_ADHOC_COMPARISON.md   # Phase 1+2 process comparison artifact
└── SYSTEM_STATE.md              # this file

(repo root)
plan-codegenome-phase-1-2.md     # PASS audit, sealed at 509b411d
plan-codegenome-phase-3.md       # PASS audit, sealing now
```

## Files modified across phases

```text
ledger/schema.py                 # 10 → 11 → 12; +6 tables, +5 edges, +3 migrations
ledger/queries.py                # +9 codegenome queries, _validated_record_id helper
ledger/adapter.py                # +9 thin async wrappers + import additions
context.py                       # +codegenome / codegenome_config on BicameralContext
handlers/bind.py                 # +codegenome hook (Phase 1+2; passes code_locator in Phase 3)
handlers/link_commit.py          # +_run_continuity_pass (Phase 3)
contracts.py                     # +ContinuityResolution + LinkCommitResponse field (Phase 3)
.gitignore                       # +AI-governance directories
CHANGELOG.md                     # v0.11.0 entry; v0.12.0 entry to follow at PR-merge time
```

## Schema state (final)

- `SCHEMA_VERSION = 12`
- `SCHEMA_COMPATIBILITY[11] = "0.11.0"`, `SCHEMA_COMPATIBILITY[12] = "0.12.0"`
  (placeholders; release-eng pins at PR merge)
- New tables (Phase 1+2): `code_subject`, `subject_identity`, `subject_version`
- New edges (Phase 1+2): `has_identity`, `has_version`, `about`
- New edge (Phase 3): `identity_supersedes`
- Subject_identity gained `neighbors_at_bind` field in v12 (additive; Phase-1+2 rows have `NULL`)
- Migrations: `_migrate_v10_to_v11`, `_migrate_v11_to_v12` (additive only, no destructive)
- All writes gated at handler boundary by feature flags (`enabled` + `write_identity_records`
  for Phase 1+2; `enabled` + `enhance_drift` for Phase 3)

## Test state (final)

- **Codegenome**: 85 unit + integration tests; 85 passing.
- **Pre-existing failures on upstream/main**: 81 (filed as #67, #68, #69, #70).
  Zero introduced by this session across both #59 and #60.
- **Section 4 razor**: PASS; mid-implement violations caught twice
  (`write_codegenome_identity` in #59, `evaluate_continuity_for_drift` and
  `write_codegenome_identity` regrowth in #60) and remediated by extracting
  helpers + bundling args into dataclass.
- **Razor regression after Phase 3 plumbing**: caught at substantiation
  Step 5; remediated by extracting `_compute_identity_for_bind` helper
  and tightening `write_codegenome_identity` docstring.

## Capability shortfalls (carried across all phases)

1. `qor/scripts/` runtime helpers absent — gate-chain artifacts at
   `.qor/gates/<session_id>/<phase>.json` not written. File-based
   META_LEDGER chain is the canonical record.
2. `qor/reliability/` enforcement scripts absent — Step 4.6 sweep
   skipped (intent-lock, skill-admission, gate-skill-matrix).
3. `agent-teams` capability not declared — sequential mode.
4. `codex-plugin` capability not declared — solo audit mode.

## Outstanding upstream issues filed across this session

- BicameralAI/bicameral-mcp#67 — Windows subprocess `NotADirectoryError` (38 tests)
- BicameralAI/bicameral-mcp#68 — surrealkv URL parsing on Windows (5 tests)
- BicameralAI/bicameral-mcp#69 — missing `_merge_decision_matches` (3 tests)
- BicameralAI/bicameral-mcp#70 — AssertionError cluster umbrella (~20 tests)
- BicameralAI/bicameral-mcp#72 — `binds_to.provenance` schema needs FLEXIBLE keyword
- MythologIQ-Labs-LLC/Qor-logic#18 — convention proposal: commit-trailer attribution
