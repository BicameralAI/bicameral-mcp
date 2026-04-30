# System State — post-#124-substantiation snapshot

**Generated**: 2026-04-29
**HEAD**: `7c210b4` (Issue #124 implementation; seal pending commit)
**Branch**: `feat/124-link-commit-cli` (off `BicameralAI/dev` post-#119 governance v0.17.2)
**Tracked PR**: will target `BicameralAI/dev` (Issue #124); aggregate `dev → main` PR is downstream
**Genesis hash**: `29dfd085...`
**#124 seal**: Entry #23 — `950f362cb700da5a4db85c545f6b55bb725502a5744bfbb2c2eb3a9c9728661a`
**#114 seal** (other in-flight branch): Entry #20 — `a19a04de...` (PR #121 pending merge)
**#48 seal** (last-on-dev): Entry #18 — `eacc6f89...`

## #124 (post-commit hook bug fix — link_commit CLI subcommand) implementation — 8 files, ~398 LOC delta, 9 new tests, 20/21 targeted regression

| Phase | Files | New tests | Notes |
|---|---|---|---|
| 0a — Decompose `cli_main` | 1 modified | 0 | `server.py:cli_main` 92 → 15 LOC; new `_register_subparsers` (16 LOC) + `_dispatch` (29 LOC). Pure refactor under existing coverage. |
| 0 — Promote `_invoke_link_commit` | 1 new + 1 modified | 0 | `cli/_link_commit_runner.py` (38 LOC, shared sync wrapper). Pure refactor. |
| 1 — Register `link_commit` subcommand | 1 new prod + 1 modified + 1 new test | 6 | `cli/link_commit_cli.py` (29 LOC); JSON-to-stdout + `--quiet` flag; always exit 0. |
| 2 — Hook hardening | 1 modified + 1 new test | 3 | `${HOME}/.bicameral/hook-errors.log` capture + stderr-loud + always exit 0. Smoke test asserts every hook subcommand is registered + dispatched. |
| 3 — Documentation | 1 modified | 0 | `CHANGELOG.md` `[Unreleased]` Fixed entry. |

### Files in scope

**New** (5):
- `cli/_link_commit_runner.py` (38 LOC) — shared sync wrapper around `handle_link_commit`; lazy-imports SurrealDB-touching modules; collapses no-ledger and handler-exception cases to `None` for graceful skip.
- `cli/link_commit_cli.py` (29 LOC) — `link_commit` CLI entry point.
- `tests/test_link_commit_cli.py` (95 LOC, 6 tests).
- `tests/test_hook_command_registration.py` (78 LOC, 3 tests). **Original #124 bug class is now caught at PR time.**
- `plan-124-post-commit-hook-fix.md` (477 LOC, plan committed at `44c6568`).

**Modified** (4):
- `server.py` — Phase 0a decomposition (cli_main 92 → 15 + new helpers) + Phase 1 link_commit subparser/dispatch + `from typing import Any`. Net –19 LOC.
- `cli/branch_scan.py` — Phase 0 refactor (delegates to `_link_commit_runner`). Net –19 LOC.
- `setup_wizard.py` — Phase 2 hook hardening. Net +4 LOC.
- `CHANGELOG.md` — `[Unreleased]` Fixed entry.

### Plan deviations (none structural)

Implementation matches v2 plan (`44c6568`) 1:1. Mid-Phase-2 hook-message fix ("bicameral-mcp post-commit" → "Bicameral post-commit") was a self-test discovery — the smoke-test regex caught a false-positive subcommand match in the loud-failure echo string. Plan didn't pin the exact message wording, so it's a refinement, not a deviation.

### Architectural decisions retained from plan (Q1–Q5)

- **Q1**: JSON to stdout default + `--quiet` flag.
- **Q2**: No migration needed — existing Guided-mode hooks start working automatically.
- **Q3**: Bundled silent-suppression + registration fix in same PR (smoke-test interdependence).
- **Q4**: Separate subcommand (not reusing `branch-scan`) — distinct semantics.
- **Q5**: Promoted `_invoke_link_commit` to shared module — DRY at 2 callers.

### Audit findings remediated (v1 → v2 → IMPL)

- **F-1 (BLOCKING — Section 4 razor)**: `cli_main` 92 → 120 LOC was 3x over cap. **Closed**: Phase 0a decomposed before Phase 1 added the subcommand. All three resulting functions razor-compliant.
- **F-2 (NON-BLOCKING — OWASP A01/A05)**: `/tmp/bicameral-hook.err` predictable-path symlink risk. **Closed**: replaced with `${HOME}/.bicameral/hook-errors.log`.
- **F-3 (NON-BLOCKING — completeness)**: `>` truncation semantics not stated. **Closed**: explicit paragraph added.

### Capability shortfalls (carried)

- `qor/scripts/`, `qor/reliability/` absent — gate-chain artifacts not written; reliability sweep skipped.
- `agent-teams`, `codex-plugin` not declared — sequential + solo modes.
- #114 grounding lint not yet on dev (PR #121 pending) — author-time `ls -d */` discipline.
- Step 7.5 version-bump-and-tag skipped — ships in next aggregate release PR.

### Test state (post-implementation)

- 20 passed, 1 skipped (Windows chmod from #48).
- 9 new (6 link_commit_cli + 3 hook-command-registration) all green.
- 11 regression (7 branch_scan_cli + 4 setup_pre_push_hook) all green.
- All test functions ≤ 18 LOC. Largest file 95 LOC.
- ruff check + format + mypy: clean.

### Razor self-check

| Function | LOC | Cap | Headroom |
|---|---|---|---|
| `server.cli_main` | 15 | 40 | 25 |
| `server._register_subparsers` | 16 | 40 | 24 (≈ 8 more subcommands) |
| `server._dispatch` | 29 | 40 | 11 (≈ 3 more if/branches before refactor) |
| `cli._link_commit_runner.invoke_link_commit` | 22 | 40 | 18 |
| `cli.link_commit_cli.main` | 13 | 40 | 27 |
| `cli.branch_scan._compute_drift` | 9 | 40 | 31 (was 14 pre-Phase-0) |

### Workflow security review

- Hook writes to `${HOME}/.bicameral/hook-errors.log` — user-owned, no shared-system race, no `/tmp/` symlink-attack vector.
- No shell interpolation of user-controlled input.
- `exit 0` invariant preserved — failed sync never blocks user's commit.
- `[ -d .bicameral ]` guard preserved — no-op when ledger directory absent.
- File mode `0o755` on installed hook (#48 pattern unchanged).

---

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

---

## #135 triage substantiation — addendum (2026-04-30)

**Branch**: `triage/135-dashboard-tooltip-scope-cut` (off `BicameralAI/dev`)
**Tracked PR**: will target `BicameralAI/dev` (issue `BicameralAI/bicameral-mcp#135`)
**Seal**: Entry #26 — `efd0304b2f0e0b3ca28aa4620c2b8ea2eda5ab9e2828ca852ab9f3c5adda6eb5`

### Scope (deliberately narrow — scope-cut from #135's original L2 proposal)

| Surface | File | Δ LOC | Notes |
|---|---|---|---|
| Repo | `pilot/mcp/assets/dashboard.html` | +5/-1 | `renderStateCell()` ternary → if/else if; new `pending` branch with tooltip text *"Pending compliance — run /bicameral-sync in your Claude Code session to resolve."* |
| Repo | `pilot/mcp/skills/bicameral-dashboard/SKILL.md` | +1/-0 | One bullet under **Notes** documenting the tooltip nudge contract |
| External | `BicameralAI/bicameral-mcp#135` | — | `gh issue close` with scope-cut comment, post-merge |
| External | `BicameralAI/bicameral#108` body | — | Flow 3 out-of-session paragraph + Flow 1 step 3 wording fix, post-merge |

### Architectural decision recorded

`bicameral-mcp#135`'s original P0 proposal called for a `--auto-resolve-trivial`
flag on `link_commit` to close the post-commit drift→resolution loop without a
caller LLM. Design enumeration produced 7 options (hash-equality, AST-equality,
CodeGenome-classifier, Hosted GitHub App, pure-notification, tiered, defer).
All require either an LLM in the deterministic core (violating the "selection
over generation" guardrail) or trivial-cases enumeration with non-zero
false-positive risk.

**Cut**: accept the architectural limit. Post-commit hook stays sync-only.
Resolution path = dashboard tooltip on `status === 'pending'` rows → user
runs `/bicameral-sync` in their Claude Code session. No code is auto-resolved.

### Section 4 razor (post-change)

| Function | LOC | Cap | Status |
|---|---|---|---|
| `renderStateCell` | 19 | 40 | OK (was 13; +6 for if/else if) |

`dashboard.html` is 786 LOC (HTML+CSS+JS bundle, delta-only evaluated per
audit precedent).

### Plan deviations

Zero structural. Implementation matches Entry #24 audit blueprint 1:1.

### Test verification

- 0 new automated tests (acknowledged advisory per Entry #24 audit;
  `dashboard.html` has no existing automated test infrastructure).
- Mitigation: PR description includes manual verification step (composed
  in `/qor-document`).
- No console.log artifacts introduced.
- Section 4 razor: clean.

### Capability shortfalls (carried, no regression vs Entry #23)

1. `qor/scripts/` runtime helpers absent — gate-chain artifacts not written.
2. `tools/reliability/` validators absent — Steps 4.6–4.8 skipped.
3. `agent-teams` capability not declared — sequential.
4. `codex-plugin` capability not declared — solo audit/seal.
5. Step 5.5 `intent_lock` capture skipped (no `qor.reliability.intent_lock`).

### Outstanding (carried into next phase)

- `bicameral-mcp#125` scope should be widened — 7 skills under
  `pilot/mcp/.claude/skills/` are absent from canonical `pilot/mcp/skills/`
  location claimed by `pilot/mcp/CLAUDE.md`.
- `bicameral#108` Flow 1 step 3 spec drift: doc claimed
  `IngestResponse.supersession_candidates` exists when it does not;
  collision detection lives caller-side via `bicameral-context-sentry`
  skill and surfaces via `bicameral.preflight.unresolved_collisions`.
  Spec-text correction is a `/qor-document`-phase external `gh` action.
