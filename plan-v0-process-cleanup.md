# Plan: v0 Process Cleanup

**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L1 (governance/docs only; no code paths touched)
**Mode**: solo (codex-plugin not declared; agent-teams pending Phase 4)
**Predecessor chain**: docs/META_LEDGER.md Entry #6 seal `509b411d`
**Scope**: cleanup-only — v0 feature priorities are out of scope for this plan.

## Open Questions

None. All design choices were closed during the planning dialogue:
- Skill placement: `.claude/skills/` (host-correct default)
- Backfill: 3 live shortfalls into JSONL log; narrative `SHADOW_GENOME.md` retained
- SECURITY.md: standard scope (versions + reporting + threat model + SLA)
- Capabilities: `agent-teams=true`, `codex-plugin=false`

---

## Phase 1: Skill placement correction

### Verification (TDD)

- [ ] `ls .claude/skills/ | grep -c "^bicameral-"` → `0`
- [ ] `ls .claude/skills/ | grep -c "^qor-"` → `≥ 25`
- [ ] `ls skills/ | grep -c "^bicameral-"` → unchanged (canonical MCP skills untouched)
- [ ] `git status --short skills/` → no MCP skill changes
- [ ] `git status --short` shows expected `.claude/skills/qor-*/` untracked + `.gitignore` modified

### Affected Files

- `.claude/skills/qor-*/` — already written by `qor-logic install --host claude --scope repo` (no `--target` override). 35+ governance skill dirs.
- `.claude/agents/*.md` — already written. 5 agent files.
- `.claude/.qorlogic-installed.json` — already written. Install manifest.
- `.qor/gates/`, `.qor/session/`, `.agent/staging/` — already created by `qor-logic seed`.
- `.gitignore` — already modified by seed (appended `# qor:seed` block with `.qor/session/`).
- `.claude/skills/bicameral-*/` — **DELETE** — 16 stale duplicate dirs per [CLAUDE.md](CLAUDE.md): "stale duplicates and should be deleted." Canonical lives at `skills/bicameral-*/`.

### Changes

```bash
rm -rf .claude/skills/bicameral-*/
```

### Status

The install correction was executed during this planning dialogue (the wrong-path install at repo root was reverted; the right-path install completed). The stale-duplicate deletion above is the only residual action.

---

## Phase 2: PROCESS_SHADOW_GENOME.md initialization

### Verification (TDD)

- [ ] `python -m qor.scripts.check_shadow_threshold` reports 3 events (not "no events in log")
- [ ] `python -c "from qor.scripts import shadow_process; print(len(shadow_process.read_events()))"` → `3`
- [ ] All three events parse as valid JSON; no malformed-line warnings emitted

### Affected Files

- `docs/PROCESS_SHADOW_GENOME.md` — **CREATE** — runtime-readable JSONL log (per `qor/workdir.py:40-42` canonical path)
- `docs/SHADOW_GENOME.md` — **UNCHANGED** — narrative failure-mode log retained as separate artifact

### Changes

Create `docs/PROCESS_SHADOW_GENOME.md` with prose header + JSONL section. Three initial events backfilling live process drift:

| Event id | event_type | skill | severity | summary | source |
|---|---|---|---|---|---|
| shadow-001 | `capability_shortfall` | `qor-audit` | 2 | agent-teams capability undeclared on host | docs/SYSTEM_STATE.md#capability-shortfalls |
| shadow-002 | `capability_shortfall` | `qor-audit` | 2 | codex-plugin capability undeclared (forces solo-mode audits) | docs/SYSTEM_STATE.md#capability-shortfalls |
| shadow-003 | `governance_gap` | `qor-repo-scaffold` | 3 | SECURITY.md missing in repo root | docs/BACKLOG.md#S1 |

Authored via `qor.scripts.shadow_process.write_events([...])`; field shape conforms to whatever the helper writes (do not hand-craft schema). Prose header explains the relationship to the narrative `SHADOW_GENOME.md`:

```
# Process Shadow Genome

Runtime-readable JSONL log of unaddressed process drift. Read by
qor.scripts.check_shadow_threshold and qor.scripts.shadow_process.

For narrative failure-mode entries (HALLUCINATION, ORPHAN/SCOPE_CREEP),
see SHADOW_GENOME.md — separate artifact, human-readable, not parsed by
the runtime.
```

---

## Phase 3: SECURITY.md (closes shadow-003)

### Verification (TDD)

- [ ] `SECURITY.md` exists at repo root
- [ ] File has the four expected H2 sections: `## Supported Versions`, `## Reporting a Vulnerability`, `## Threat Model Summary`, `## Response SLA`
- [ ] `python -m qor.scripts.shadow_process read | grep shadow-003 | grep -q '"addressed":true'` after the mark-resolved step
- [ ] `docs/BACKLOG.md` S1 entry ticked or moved to a Resolved section

### Affected Files

- `SECURITY.md` — **CREATE** — repo root
- `docs/PROCESS_SHADOW_GENOME.md` — **MUTATE** — flip `shadow-003.addressed = true`, set `addressed_reason = "SECURITY.md authored"`
- `docs/BACKLOG.md` — **MUTATE** — tick S1, append resolution date

### Changes

Author `SECURITY.md` with sections:

1. **Supported Versions** — table: v0.11.x (current), v0.10.x (security-fix only), older (unsupported).
2. **Reporting a Vulnerability** — GitHub private vulnerability reporting (preferred) + maintainer email fallback. State: do NOT open public issues for security findings.
3. **Threat Model Summary** — Stores: SurrealDB embedded ledger (decisions, code symbol index), local file paths. Does NOT store: secrets, credentials, end-user PII, third-party API tokens. Trust boundary: MCP server runs locally; assumes the host (Claude Code, etc.) is trusted.
4. **Response SLA** — 7-day acknowledgement, 30-day fix-or-coordinated-disclosure.

Then mark shadow-003 addressed:

```bash
python -m qor.scripts.create_shadow_issue --mark-resolved --events shadow-003
```

Tick BACKLOG.md S1.

---

## Phase 4: Capability declarations (closes shadow-001 + shadow-002)

### Verification (TDD)

- [ ] `.qor/platform.json` exists
- [ ] `python -m qor.scripts.qor_platform check agent-teams` exits 0
- [ ] `python -m qor.scripts.qor_platform check codex-plugin` exits non-zero (declared unavailable)
- [ ] `python -m qor.scripts.qor_platform get` shows both capabilities present
- [ ] `python -m qor.scripts.check_shadow_threshold` reports 0 unaddressed events

### Affected Files

- `.qor/platform.json` — **CREATE** via `qor_platform set` (atomic write per script's docstring)
- `docs/PROCESS_SHADOW_GENOME.md` — **MUTATE** — flip shadow-001 and shadow-002 to addressed

### Changes

```bash
python -m qor.scripts.qor_platform set agent-teams true
python -m qor.scripts.qor_platform set codex-plugin false
python -m qor.scripts.create_shadow_issue --mark-resolved --events shadow-001,shadow-002
```

Honest declarations: agent-teams genuinely available (Claude Code Agent tool); codex-plugin genuinely unavailable in this host configuration.

---

## Phase 5: SYSTEM_STATE.md sync

### Verification (TDD)

- [ ] `grep -c "Resolved 2026-05-01" docs/SYSTEM_STATE.md` → `5` (one per shortfall)
- [ ] No capability-shortfall numbered entries remain without a Resolved annotation
- [ ] Section header on capability shortfalls retained (preserves history)

### Affected Files

- `docs/SYSTEM_STATE.md` — **MUTATE** — annotate the five entries in the "Capability shortfalls observed during this session" section (lines 70-87)

### Changes

For each of the five entries, append a "Resolved" line with date and resolution:

| # | Original entry | Resolution annotation |
|---|---|---|
| 4a | `qor/scripts/` runtime helpers absent | Resolved 2026-05-01 — pip upgrade to `qor-logic 0.42.0` |
| 4b | `qor/reliability/` enforcement scripts absent | Resolved 2026-05-01 — present in `qor-logic 0.42.0` (`intent_lock`, `skill_admission`, `gate_skill_matrix`) |
| 4c | `agent-teams` undeclared | Resolved 2026-05-01 — declared `true` via `qor_platform set` |
| 4d | `codex-plugin` undeclared | Resolved 2026-05-01 — declared `false` via `qor_platform set` (genuine unavailability) |
| 4e | `AUDIT_REPORT.md` path divergence | Resolved 2026-05-01 — `.agent/staging/` is the canonical default in `qor-logic 0.42.0` skills |

Do not delete entries; the historical record is part of META_LEDGER chain integrity.

---

## CI commands

Validation commands to run end-to-end after Phase 5:

```bash
# Test suite — must remain unchanged (no code paths touched in this plan)
pytest -x tests/

# Shadow log healthy — 3 events, all addressed
python -m qor.scripts.check_shadow_threshold

# Capability state correct
python -m qor.scripts.qor_platform get

# Gate chain integrity (advisory)
python -m qor.scripts.gate_chain --verify
```

Pytest baseline: 254 passed / 81 environmental failures pre-plan (per META_LEDGER Entry #5). This plan changes no code; the same 254/81 split must hold post-implementation.
