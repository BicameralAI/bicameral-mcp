# System State — post-substantiation snapshot

**Last updated**: 2026-05-01 (v0 process cleanup seal — Merkle `186b045e`)
**Original seal**: 2026-04-28 (codegenome Phase 1+2 seal — Merkle `509b411d`)
**HEAD**: `169722f` + uncommitted v0 process cleanup
**Branch**: `claude/peaceful-bell-12b5e8` (off `dev`)
**Tracked PR**: [BicameralAI/bicameral-mcp#71](https://github.com/BicameralAI/bicameral-mcp/pull/71)
**Genesis hash**: `29dfd085...`

## v0 process cleanup session (2026-05-01)

Files added by this session:
- `SECURITY.md` (102 lines) — closes BACKLOG S1
- `docs/PROCESS_SHADOW_GENOME.md` — runtime-readable JSONL log; 3 events written, 3 addressed
- `.qor/platform.json` — capability state (`agent-teams=true`, `codex-plugin=false`)
- `.qor/gates/2026-05-02T0052-2d49b8/{plan,audit,implement,substantiate}.json`
- `.claude/skills/qor-*/` (29 governance skills) + `.claude/agents/` (5 agents) — installed via `qor-logic install --host claude --scope repo`
- `plan-v0-process-cleanup.md`, `.agent/staging/AUDIT_REPORT.md`

Files modified by this session:
- `docs/BACKLOG.md` — S1 ticked
- `docs/SYSTEM_STATE.md` — capability shortfalls 1-5 annotated `Resolved 2026-05-01`
- `docs/META_LEDGER.md` — Entries #7 (PLAN), #8 (GATE PASS), #9 (IMPLEMENT), #10 (SUBSTANTIATE seal)
- `.gitignore` — `qor-logic seed` appended `# qor:seed` block

Files deleted by this session:
- `.claude/skills/bicameral-*/` (15 stale duplicate dirs; canonical at `skills/bicameral-*/` untouched)

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

These were logged at each phase but not actioned (out of scope for #59).
Resolution annotations added 2026-05-01 by `plan-v0-process-cleanup.md`.

1. `qor/scripts/` runtime helpers (`gate_chain`, `session`, `shadow_process`,
   `governance_helpers`, `qor_audit_runtime`) absent — gate-chain artifacts
   at `.qor/gates/<session_id>/<phase>.json` were not written. Skill
   protocols treat these as advisory wiring; the file-based META_LEDGER
   chain is the canonical record.
   **Resolved 2026-05-01** — pip upgrade to `qor-logic 0.42.0` provides
   the runtime; gate artifacts now written to `.qor/gates/<sid>/*.json`.
2. `qor/reliability/` enforcement scripts (`intent-lock`, `skill-admission`,
   `gate-skill-matrix`) absent — Step 4.6 reliability sweep skipped.
   **Resolved 2026-05-01** — `qor-logic 0.42.0` ships `intent_lock.py`,
   `skill_admission.py`, `gate_skill_matrix.py` (verified at
   `qor/reliability/`); intent lock captured at start of `/qor-implement`
   for session `2026-05-02T0052-2d49b8`.
3. `agent-teams` capability not declared on Claude Code host — Step 1.a
   parallel-mode disabled; ran sequential.
   **Resolved 2026-05-01** — declared `true` via
   `python -m qor.scripts.qor_platform set agent-teams true`; persisted
   in `.qor/platform.json`.
4. `codex-plugin` capability not declared — Step 1.a adversarial
   audit-mode disabled; ran solo.
   **Resolved 2026-05-01** — declared `false` via
   `python -m qor.scripts.qor_platform set codex-plugin false`. Genuine
   unavailability; declaration stops the recurring shortfall log.
5. `AUDIT_REPORT.md` lives at `.agent/staging/` rather than the skill's
   default `.failsafe/governance/`. Path divergence noted; chain
   integrity preserved.
   **Resolved 2026-05-01** — `.agent/staging/AUDIT_REPORT.md` is the
   canonical default in `qor-logic 0.42.0` skills (verified across
   `qor-audit`, `qor-substantiate`, `qor-validate` SKILL.md files); the
   prior path was the divergence, not this one.

## Outstanding upstream issues filed

- [BicameralAI/bicameral-mcp#67](https://github.com/BicameralAI/bicameral-mcp/issues/67) — Windows subprocess `NotADirectoryError` (38 tests)
- [BicameralAI/bicameral-mcp#68](https://github.com/BicameralAI/bicameral-mcp/issues/68) — surrealkv URL parsing on Windows (5 tests)
- [BicameralAI/bicameral-mcp#69](https://github.com/BicameralAI/bicameral-mcp/issues/69) — missing `_merge_decision_matches` symbol (3 tests)
- [BicameralAI/bicameral-mcp#70](https://github.com/BicameralAI/bicameral-mcp/issues/70) — AssertionError cluster umbrella (~20 tests)
- [MythologIQ-Labs-LLC/Qor-logic#18](https://github.com/MythologIQ-Labs-LLC/Qor-logic/issues/18) — convention proposal: commit-trailer attribution for QorLogic SDLC work
