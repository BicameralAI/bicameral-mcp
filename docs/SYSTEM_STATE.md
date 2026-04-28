# System State — post-Phase-3-substantiation snapshot

**Generated**: 2026-04-28
**HEAD**: `d10f0ca` + razor-fix amendment (Phase 3 sealed)
**Branch**: `claude/codegenome-phase-3-qor`
**Tracked PR**: stacked on PR #71; #60 PR pending
**Genesis hash**: `29dfd085...`

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
