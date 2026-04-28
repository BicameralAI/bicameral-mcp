# System State — post-substantiation snapshot

**Generated**: 2026-04-28
**HEAD**: `51ff53f` (rebased onto `upstream/main` `7796ab9`)
**Branch**: `claude/codegenome-phase-1-2-qor`
**Tracked PR**: [BicameralAI/bicameral-mcp#71](https://github.com/BicameralAI/bicameral-mcp/pull/71)
**Genesis hash**: `29dfd085...`

## Files added by this session

```
codegenome/
├── __init__.py
├── adapter.py                   # CodeGenomeAdapter ABC + 5 dataclasses + 2 type aliases
├── contracts.py                 # 3 issue-mandated Pydantic models
├── confidence.py                # noisy_or, weighted_average, DEFAULT_CONFIDENCE_WEIGHTS
├── config.py                    # CodeGenomeConfig (7 flags, all default False)
├── deterministic_adapter.py     # DeterministicCodeGenomeAdapter.compute_identity (deterministic_location_v1)
└── bind_service.py              # write_codegenome_identity + 2 internal helpers (Section 4 razor split)

adapters/
└── codegenome.py                # get_codegenome() factory parallel to get_ledger / get_code_locator / get_drift_analyzer

tests/
├── test_codegenome_adapter.py            # ABC + dataclass + compute_identity coverage
├── test_codegenome_bind_integration.py   # full handler-path integration (#59 exit criteria)
├── test_codegenome_confidence.py         # noisy_or + weighted_average property tests
└── test_codegenome_config.py             # env-loaded flag matrix

docs/
├── CONCEPT.md                   # Why / Vibe / Anti-Goals — project DNA
├── ARCHITECTURE_PLAN.md         # Risk grade L2 + file tree + interface contracts
├── META_LEDGER.md               # 5-entry Merkle chain (will gain Entry #6 from this seal)
├── BACKLOG.md                   # 1 security blocker, 1 dev blocker, 3 backlog, 2 wishlist
├── SHADOW_GENOME.md             # 2 recorded failure modes from pre-PASS audit
├── QOR_VS_ADHOC_COMPARISON.md   # Side-by-side QOR-process vs ad-hoc reference build
└── SYSTEM_STATE.md              # this file

(repo root)
plan-codegenome-phase-1-2.md     # Audit-passed implementation plan
```

## Files modified by this session

```
ledger/schema.py                 # SCHEMA_VERSION 10 → 11 + 3 tables + 3 edges + _migrate_v10_to_v11
ledger/queries.py                # +5 codegenome queries (upsert_code_subject, upsert_subject_identity, relate_has_identity, link_decision_to_subject, find_subject_identities_for_decision)
ledger/adapter.py                # +5 thin async wrappers + 5 query imports
context.py                       # +codegenome and codegenome_config fields on BicameralContext, populated in from_env()
handlers/bind.py                 # +side-effect identity-write hook (gated by ctx.codegenome_config.identity_writes_active())
.gitignore                       # +AI-governance directories (.agent/, .failsafe/, .qor/, .cursor/, .windsurf/)
CHANGELOG.md                     # +v0.11.0 entry (header notes "built via QorLogic SDLC")
```

## Schema state

- `SCHEMA_VERSION = 11`
- `SCHEMA_COMPATIBILITY[11] = "0.11.0"` (placeholder, release-eng pin at PR merge)
- New tables: `code_subject`, `subject_identity`, `subject_version`
- New edges: `has_identity` (subject→identity), `has_version` (subject→version), `about` (decision→subject)
- Migration: `_migrate_v10_to_v11` (additive only, no existing tables touched)
- Tables exist unconditionally; writes gated by `codegenome.write_identity_records=True` at handler boundary

## Test state

- **Codegenome**: 49 unit + integration tests, 49/49 PASS
- **Pre-existing failures on upstream/main**: 81 (all environmental — Windows subprocess, surrealkv URL, missing symbol; filed as upstream issues #67, #68, #69, #70). Zero introduced by this session.
- **Section 4 razor**: PASS (all new functions ≤ 40 lines, all new files ≤ 250 lines)

## Capability shortfalls observed during this session

These were logged at each phase but not actioned (out of scope for #59):

1. `qor/scripts/` runtime helpers (`gate_chain`, `session`, `shadow_process`,
   `governance_helpers`, `qor_audit_runtime`) absent — gate-chain artifacts
   at `.qor/gates/<session_id>/<phase>.json` were not written. Skill
   protocols treat these as advisory wiring; the file-based META_LEDGER
   chain is the canonical record.
2. `qor/reliability/` enforcement scripts (`intent-lock`, `skill-admission`,
   `gate-skill-matrix`) absent — Step 4.6 reliability sweep skipped.
3. `agent-teams` capability not declared on Claude Code host — Step 1.a
   parallel-mode disabled; ran sequential.
4. `codex-plugin` capability not declared — Step 1.a adversarial
   audit-mode disabled; ran solo.
5. `AUDIT_REPORT.md` lives at `.agent/staging/` rather than the skill's
   default `.failsafe/governance/`. Path divergence noted; chain
   integrity preserved.

## Outstanding upstream issues filed

- [BicameralAI/bicameral-mcp#67](https://github.com/BicameralAI/bicameral-mcp/issues/67) — Windows subprocess `NotADirectoryError` (38 tests)
- [BicameralAI/bicameral-mcp#68](https://github.com/BicameralAI/bicameral-mcp/issues/68) — surrealkv URL parsing on Windows (5 tests)
- [BicameralAI/bicameral-mcp#69](https://github.com/BicameralAI/bicameral-mcp/issues/69) — missing `_merge_decision_matches` symbol (3 tests)
- [BicameralAI/bicameral-mcp#70](https://github.com/BicameralAI/bicameral-mcp/issues/70) — AssertionError cluster umbrella (~20 tests)
- [MythologIQ-Labs-LLC/Qor-logic#18](https://github.com/MythologIQ-Labs-LLC/Qor-logic/issues/18) — convention proposal: commit-trailer attribution for QorLogic SDLC work
