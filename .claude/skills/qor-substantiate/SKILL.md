---
name: qor-substantiate
description: >-
  S.H.I.E.L.D. Substantiation and Session Seal that verifies implementation against blueprint and cryptographically seals the session. Use when: (1) Implementation is complete, (2) Ready to verify Reality matches Promise, (3) Need to seal session with Merkle hash, or (4) Preparing to hand off completed work.
metadata:
  category: governance
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/governance/qor-substantiate
phase: substantiate
tone_aware: false
autonomy: interactive
gate_reads: implement
gate_writes: substantiate
permitted_tools: [Read, Grep, Glob, Bash, Edit, Write]
permitted_subagents: []
model_compatibility: [claude-opus-4-7]
min_model_capability: opus
---
# /qor-substantiate - Session Seal

<skill>
  <trigger>/qor-substantiate</trigger>
  <phase>substantiate</phase>
  <persona>Judge</persona>
  <output>Updated META_LEDGER.md with final seal, SYSTEM_STATE.md snapshot</output>
</skill>

## Purpose

The final phase of the S.H.I.E.L.D. lifecycle. Verify that implementation matches the encoded blueprint (Reality = Promise), then cryptographically seal the session.

## Execution Protocol

### Step 0: Gate Check (advisory — Phase 8 wiring)

Verify prior-phase artifact exists and is well-formed before proceeding.

```python
from qor.scripts import gate_chain, session

sid = session.get_or_create()
result = gate_chain.check_prior_artifact("substantiate", session_id=sid)
if not result.found:
    # Prompt user to override; on confirm:
    gate_chain.emit_gate_override(
        current_phase="substantiate",
        prior_phase_name="implement",
        reason="user override: implement.json not found",
        session_id=sid,
    )
elif not result.valid:
    gate_chain.emit_gate_override(
        current_phase="substantiate",
        prior_phase_name="implement",
        reason=f"user override: {result.errors}",
        session_id=sid,
    )
```

Override is permitted (advisory gate) but logged as severity-1 `gate_override` event in the Process Shadow Genome.

**Phase 54 wiring**: when `gate_chain.emit_gate_override` raises `OverrideFrictionRequired`, prompt the operator for a written justification (>=50 chars) and re-call `emit_gate_override` with `justification=<text>`. Per `qor/references/doctrine-ai-rmf.md` §MANAGE-1.1 + `qor/references/doctrine-eu-ai-act.md` Art. 14.

### Step 1: Identity Activation
You are now operating as **The Qor-logic Judge** in substantiation mode.

Your role is to prove, not to improve. Verify what was built matches what was promised.

### Step 2: State Verification

```
Read: docs/META_LEDGER.md
Read: docs/ARCHITECTURE_PLAN.md
Read: .agent/staging/AUDIT_REPORT.md
```

**INTERDICTION**: If no PASS verdict exists:

<!-- qor:fail-fast-only reason="PASS verdict is produced by /qor-audit, not by qor-logic seed; cannot auto-heal" -->
Abort with "Cannot substantiate without PASS verdict. Run /qor-audit first."

**INTERDICTION**: If no implementation exists:

<!-- qor:fail-fast-only reason="implementation is /qor-implement's output, not scaffold; cannot auto-heal" -->
Abort with "No implementation found. Run /qor-implement first."

### Step 2.5: Version Validation (MANDATORY)

**Verify version consistency** between plan and current state:

```bash
git tag --sort=-v:refname | head -1
```

```
Read: Plan file (docs/Planning/plan-*.md or docs/ARCHITECTURE_PLAN.md)
Extract: Target Version from plan header
```

<!-- qor:fail-fast-only reason="version-state checks are logic gates, not scaffold; recovery requires operator correction" -->
**INTERDICTION**: If Target Version ≤ Current Tag → ABORT (version already shipped).
**INTERDICTION**: If governance files reference wrong version → PAUSE (fix before sealing).

Log: "Version validated: v[current] → v[target] (change type: [hotfix|feature|breaking])"

### Step 3: Reality Audit

Compare implementation against blueprint:

```
Read: All files in src/
Compare: Against docs/ARCHITECTURE_PLAN.md file tree
```

Template: `references/qor-substantiate-templates.md`.

**Findings**:
- **MISSING**: Planned but not created -> FAIL
- **UNPLANNED**: Created but not in blueprint -> WARNING (document in ledger)
- **EXISTS**: Matches -> PASS

### Step 3.5: Blocker Verification

Read `docs/BACKLOG.md`. Warn if open Security Blockers or related Development Blockers exist.

### Step 4: Functional Verification

#### Test Audit
```
Glob: tests/**/*.test.{ts,tsx,js}
Read: Test files
```

Template: `references/qor-substantiate-templates.md`.

**Presence-only seal gate**: substantiation refuses to seal if any test added in the current phase is presence-only for the unit it claims to verify. Operator runs the acceptance question — "If the unit's behavior were silently broken but the artifact still existed, would this test fail?" — against each new test in the phase. Any "no" answer ABORTs seal. The operator amends the test to invoke the unit and assert against its output, then re-runs `/qor-substantiate`. Per `qor/references/doctrine-test-functionality.md` and SG-035 ("doctrine-content test unanchored").

#### Visual Silence Verification (if frontend)
```
Grep: "color:" in src/**/*.{css,tsx}
Grep: "background:" in src/**/*.{css,tsx}
```

Check for violations:
Template: `references/qor-substantiate-templates.md`.

#### Console.log Artifacts
```
Grep: "console.log" in src/**/*
```

Template: `references/qor-substantiate-templates.md`.

### Step 4.5: Skill File Integrity Check

If any skill files (`.claude/commands/qor-*.md`) were modified during this session:

1. List modified skill files from git diff
2. For each modified skill:
   - Verify it still has required sections: `<skill>` block, `## Execution Protocol`, `### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/substantiate.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/substantiate.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "substantiate",
    human_oversight=(
        ai_provenance.HumanOversight.PASS if payload.get("verdict") == "PASS"
        else ai_provenance.HumanOversight.VETO
    ),
)
gate_chain.write_gate_artifact(
    phase="substantiate", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/substantiate.schema.json`; the helper validates before write. Per Phase 54: substantiate calls `ai_provenance.build_manifest` with the seal verdict mapped to `HumanOversight`; closes EU AI Act Art. 14 oversight-signal surface.

After writing, rotate the session so the next `/qor-plan` starts with a clean gate directory (Phase 30 wiring; closes the session-carry-over gap that let Phase 28/29 share a single session dir):

```python
import session
new_sid = session.rotate()
print(f"Session sealed. New session: {new_sid}. Prior artifacts preserved at .qor/gates/{sid}/")
```

## Constraints`, `## Next Step`
   - Verify the `## Next Step` section references valid successor skills
   - Log in ledger: "Skill file [name] modified — structure verified"

If any skill is missing required sections after modification:

```
PAUSE
Report: "Skill [name] missing required section: [section]. Fix before sealing."
```

### Step 4.6: Reliability Sweep (Phase 17 wiring)

Three reliability enforcement gates run sequentially. Each is an interdiction: non-zero exit aborts substantiation.

```bash
# Resolve session_id via the canonical helper (reads .qor/session/current AND
# validates against SESSION_ID_PATTERN per Phase 23 LOW-2 / Phase 50 doctrine)
SESSION_ID=$(python -c "from qor.scripts.session import current; print(current() or 'default')")

# Re-verify the intent lock captured at /qor-implement Step 5.5.
# Fails if plan, audit, or HEAD commit drifted since capture.
python -m qor.reliability.intent_lock verify --session "$SESSION_ID" || ABORT

# Verify current skill is registered and frontmatter is well-formed.
python -m qor.reliability.skill_admission qor-substantiate || ABORT

# Verify all /qor-* handoff references across skills resolve to real skills.
python -m qor.reliability.gate_skill_matrix || ABORT
```

Any ABORT leaves the session unsealed. Operator must resolve the drift (re-audit, re-admit, or fix broken handoff) and re-run substantiation.

### Step 4.6.5: Secret-scanning gate (Phase 56 wiring)

Pre-seal scan over staged content. ABORTs on any detected secret. Closes OWASP LLM Top 10 LLM06 (Sensitive Information Disclosure) and NIST AI 600-1 §2.10. Drives the previously dormant `has_hardcoded_secrets` Cedar attribute (rule on books since Phase 23).

```bash
python -m qor.scripts.secret_scanner --staged --out dist/secrets.findings.json || ABORT
```

ABORT semantics on non-zero exit: operator must remediate detected secrets (remove from staging, redact, or add to allowlist when literal-match false-positive) before re-running substantiation. Findings JSON written to `dist/secrets.findings.json` in gitleaks v8 schema for downstream tool compatibility.

### Step 4.7: Documentation Integrity Check (Phase 28 wiring)

Read the plan artifact and run doc-integrity checks against the declared tier. ABORTs on any `ValueError` per `qor/references/doctrine-documentation-integrity.md` (no silent override; `legacy` tier bypasses all checks).

```python
from qor.scripts import doc_integrity, gate_chain

plan_artifact = gate_chain.read_phase_artifact("plan", session_id=sid)
# plan_slug derived from plan_path filename stem (e.g., plan-qor-phase28-<slug>.md)
plan_artifact["plan_slug"] = derive_slug_from_plan_path(plan_artifact["plan_path"])
doc_integrity.run_all_checks_from_plan(plan_artifact, repo_root=".", strict=True)  # Phase 32 wiring: D/E strict-mode
```

Any raised `ValueError` ABORTs substantiation. Operator fixes (update glossary / adjust declared terms / raise tier) and re-runs. No retry-with-waiver path.

### Step 5: Section 4 Razor Final Check

Template: `references/qor-substantiate-templates.md`.

### Step 6: Sync System State

Map the final physical tree:

```
Glob: src/**/*
Glob: tests/**/*
Glob: docs/**/*
```

Create/Update `docs/SYSTEM_STATE.md`:

Template: `references/qor-substantiate-templates.md`.

### Step 6.5: Documentation Currency Check (Phase 31 wiring)

Verify that doc-affecting phase changes also updated the system-tier docs (`docs/architecture.md`, `docs/lifecycle.md`, `docs/operations.md`, `docs/policies.md`). Heuristic lives in `doc_integrity_strict.check_documentation_currency` and returns a warning list.

```python
from qor.scripts import gate_chain
from qor.scripts.doc_integrity_strict import check_documentation_currency

implement = gate_chain.read_phase_artifact("implement", session_id=sid)
plan_artifact = gate_chain.read_phase_artifact("plan", session_id=sid)
warnings = check_documentation_currency(
    implement, repo_root=".", plan_payload=plan_artifact,  # Phase 33 wiring: release-doc rule
)
if warnings:
    print("WARNING: Documentation currency check:")
    for w in warnings: print(f"  {w}")
    # Phase 31 semantics: WARN + continue. Future phase may upgrade to BLOCK.
```

Phase 33 addition: when `plan_artifact.change_class` is `feature` or `breaking`, the check also requires README.md and CHANGELOG.md in `files_touched`. Hotfix is exempt. Operator judgment applies: if warnings are spurious (e.g., touched file genuinely doesn't affect doc-worthy concepts), continue seal. If legitimate (e.g., new doctrine added without updating lifecycle.md, or feature shipped without release-doc authoring), PAUSE, amend, re-run seal.

**Phase 49 addition: README badge currency (release-class only, ABORT semantics)**. When `plan_artifact.change_class` is `feature` or `breaking`, run `qor.scripts.badge_currency` and ABORT seal on mismatch. Hotfix is exempt (matches the existing `_RELEASE_CLASSES` semantics). The check parses README literal-count badges (Tests, Ledger, Skills, Agents, Doctrines) and asserts them against current truth.

```bash
if [[ "${CHANGE_CLASS}" == "feature" || "${CHANGE_CLASS}" == "breaking" ]]; then
  python -m qor.scripts.badge_currency \
    --repo-root . \
    --ledger docs/META_LEDGER.md \
    || { echo "ABORT: README badge currency mismatch — update Tests/Ledger/Skills/Agents/Doctrines counts to match truth before re-running /qor-substantiate"; exit 1; }
fi
```

The exit-1 ABORT is what distinguishes Phase 49's enforcement from Phase 31's WARN-only currency check above. Operator must update the README badges in the implement pass (or here, before re-running substantiate). Locked by `tests/test_readme_badge_currency.py` and `tests/test_substantiate_badge_currency_wiring.py` per `qor/references/doctrine-governance-enforcement.md` §"Badge currency".

### Step 7: Final Merkle Seal

Calculate session seal:

Reference implementation: `.claude/commands/scripts/calculate-session-seal.py`.

Update `docs/META_LEDGER.md`:

Template: `references/qor-substantiate-templates.md`.

### Step 7.4: SSDF tag emission (Phase 52 wiring)

Closes G-1 from `docs/compliance-re-evaluation-2026-04-29.md`. Computes NIST SSDF practice tags for the SESSION SEAL entry body BEFORE the content_hash is computed in Step 7. Forward-only: Phase 52+ entries get tags; Phase ≤ 51 entries grandfathered (immutable Merkle chain forbids retroactive edit).

```bash
# Compute SSDF tag line via pure-Python module (no python -c shell-variable
# interpolation; SG-Phase47-A countermeasure). $CHANGE_CLASS is consumed only
# as an argv argument and argparse-validated against {feature, breaking, hotfix}.
CHANGE_CLASS=$(python -c "from qor.scripts.governance_helpers import parse_change_class, current_phase_plan_path; print(parse_change_class(current_phase_plan_path()))")
SSDF_LINE=$(python -m qor.scripts.ssdf_tagger --change-class "$CHANGE_CLASS" --base-ref origin/main --repo-root .)
```

Operator pastes `$SSDF_LINE` into the SESSION SEAL entry body before Step 7 computes content_hash. Per `qor/references/doctrine-nist-ssdf-alignment.md` §"Phase 52 wiring (forward-only emission)".

### Step 7.5: Version bump (Phase 13 wiring; Phase 33 split)

Bump `pyproject.toml` only. Tag creation was moved to Step 9.5.5 after the seal commit (Phase 33 — prevents the historical off-by-one seal-tag timing bug where tags pointed at the pre-seal HEAD).

```python
# Phase 13 wiring: version bump. Tag creation deferred to Step 9.5.5 (Phase 33).
from qor.scripts import governance_helpers as gh

plan_path = gh.current_phase_plan_path()              # V-5: lexicographic suffix
phase_num, slug = gh.derive_phase_metadata(plan_path) # W-3: derive before use
change_class = gh.parse_change_class(plan_path)       # V-2: bold-form enforced
new_version = gh.bump_version(change_class)           # V-6 + W-4: tag-collision + downgrade interdiction
```

### Step 7.6: Stamp CHANGELOG (Phase 27 wiring)

After the version bump in Step 7.5 produces `new_version` and the seal date is known, stamp `CHANGELOG.md` in place:

```python
from qor.scripts.changelog_stamp import apply_stamp
from datetime import datetime, timezone

apply_stamp(
    path="CHANGELOG.md",
    version=new_version,
    date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
)
```

`apply_stamp` raises `ValueError` on missing `## [Unreleased]`, empty Unreleased (no bullets), or collision with an existing `[new_version]` section. On any raise, PAUSE with the operator message; do NOT silently ship an unstamped CHANGELOG. Per `qor/references/doctrine-changelog.md`, every release gets a dated section; the Unreleased convention is populated during `/qor-implement` and mechanically renamed on seal.

### Step 7.7: Post-seal verification (Phase 47 wiring)

Closes SG-AdjacentState-A (Phase 46's sixth instance: substantiate sealed at v0.33.0 without writing META_LEDGER entries; intent-lock and the Step 4.6 reliability gates did not catch it because they run before the seal entry is written). Runs *after* Step 7 (Final Merkle Seal) has appended the SESSION SEAL entry to `docs/META_LEDGER.md`. Verifies the entry exists for this phase and the latest chain hash is internally consistent.

```bash
PLAN_PATH=$(python -c "from qor.scripts.governance_helpers import current_phase_plan_path; print(current_phase_plan_path())")

python -m qor.reliability.seal_entry_check --ledger docs/META_LEDGER.md --plan "$PLAN_PATH" || ABORT
```

The `python -c` source is hardcoded — no shell variable is interpolated into the Python literal — so the OWASP A03 injection vector is closed by construction. `current_phase_plan_path()` (`qor/scripts/governance_helpers.py:57-67`) reads the git branch name and globs `docs/plan-qor-phase{NN}*.md`. The helper resolves phase number from the plan path internally and reads the latest entry's chain hash directly from the ledger; no caller-supplied Merkle seal expectation. Argv-form invocation throughout. ABORT on non-zero exit leaves the session unsealed; operator amends the ledger (or re-runs Step 7) and re-runs `/qor-substantiate`.

### Step 7.8: Gate-chain completeness check (Phase 52 wiring)

Closes the skill-protocol bypass surface. Walks SESSION SEAL entries with phase >= 52 in `docs/META_LEDGER.md`; for each, asserts `.qor/gates/<sid>/{plan,audit,implement,substantiate}.json` all exist. ABORTs seal on any gap. Phase ≤ 51 entries grandfathered.

```bash
QOR_SKILL_ACTIVE=substantiate python -m qor.reliability.gate_chain_completeness \
  --repo-root . \
  --phase-min 52 \
  || { echo "ABORT: gate-chain completeness violated; bypass would be invisible to ledger math"; exit 1; }
```

Argv-form invocation; no shell-variable interpolation into `python -c` literals (SG-Phase47-A countermeasure). The check runs after Step 7.7's seal-entry-check confirms ledger integrity, ensuring this phase's own session has all four gate artifacts before the seal commit lands.

### Step 8: Cleanup Staging

Clear: `.agent/staging/` (transient working directory).

Preserve only the final AUDIT_REPORT.md (or archive it).

### Step 8.5: Dist Recompile (Phase 30 wiring)

Rebuild variant outputs so seal cannot complete with dist drift against source skills. Closes the gap where Phase 28/29 operators manually ran `python -m qor.scripts.dist_compile` between implementation and seal.

```bash
python -m qor.scripts.dist_compile
```

On non-zero exit, ABORT substantiation; operator must resolve compile errors and re-run seal.

### Step 9: Final Report

Template: `references/qor-substantiate-templates.md`.

### Step 9.5: Stage Artifacts (for user commit)

  **Stage All Artifacts**:
  ```bash
  git add CHANGELOG.md
  git add docs/CONCEPT.md
  git add docs/ARCHITECTURE_PLAN.md
  git add docs/META_LEDGER.md
  git add docs/SYSTEM_STATE.md
  git add docs/BACKLOG.md
  git add src/
  ```

  **Next Steps**: Review the staged files and then commit and push when ready.

  Example commit message:
  ```
  seal: [plan-slug] - Session substantiated
  Merkle seal: [chain-hash]
  Verdict: PASS
  Files: [file-count]
  ```

REPORT: "Session committed and pushed to [current-branch]"

### Step 9.5.5: Annotated seal-tag creation (Phase 33 wiring)

The seal commit now exists (created in Step 9.5). Capture its SHA via `git rev-parse HEAD` and tag it. Moving tag creation to this step closes the historical timing bug where `create_seal_tag` ran at Step 7.5 with HEAD still pointing at the pre-seal commit, producing off-by-one tags across v0.19.0–v0.22.0.

```python
import subprocess
from qor.scripts import governance_helpers as gh

commit_sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    capture_output=True, text=True, check=True,
).stdout.strip()

tag = gh.create_seal_tag(
    new_version, merkle_seal, ledger_entry_num, phase_num, change_class,
    commit=commit_sha,
)
```

`commit` is a required argument — there is no HEAD-default fallback. Calling `create_seal_tag` without it raises `TypeError`. Verified by `tests/test_seal_tag_timing.py::test_create_seal_tag_raises_without_commit`.

### Step 9.6: Push/Merge Options (Phase 13 — 4-option menu)

Prompt user with four options (never offer continuation menus when work is sealable; the next decision is push/merge, not "what next phase"):

1. **Push only** — `git push origin <branch>`.
2. **Push + open PR** — `gh pr create` (description must cite plan file, ledger entry `#<n>`, and Merkle seal hash per `doctrine-governance-enforcement.md` §6).
3. **Merge to main locally (dry-run first)** — `git merge --no-commit --no-ff <branch>`; on conflict, abort and prompt operator.
4. **Hold local** — no push/merge this session.

Annotated tag was already created in Step 9.5.5; do not re-offer. Push `--tags` alongside the branch push.

Template: `references/qor-substantiate-templates.md`.

## Failure Scenarios

### If Reality != Promise:

Template: `references/qor-substantiate-templates.md`.

## Constraints

- **NEVER** seal a session with Reality != Promise
- **NEVER** skip any verification step
- **NEVER** seal with Section 4 violations present
- **NEVER** seal with version mismatch (Target ≤ Current Tag)
- **ALWAYS** validate version before sealing
- **ALWAYS** update SYSTEM_STATE.md before sealing
- **ALWAYS** calculate proper chain hash
- **ALWAYS** document any unplanned files in ledger
- **ALWAYS** verify chain integrity before sealing
- **ALWAYS** call `governance_helpers.bump_version` at Step 7.5 and `governance_helpers.create_seal_tag` at Step 9.5.5 (after the seal commit); never author tags manually and never tag at Step 7.5 (SG-Phase33-A: tagging before commit targets the pre-seal HEAD, producing off-by-one tags).
- **ALWAYS** run `python -m qor.scripts.dist_compile` at Step 8.5 so variant outputs are rebuilt on seal; prevents dist drift.
- **ALWAYS** call `session.rotate()` at Step Z after writing `substantiate.json`; prior session directory preserved for archaeology.

## Success Criteria

Substantiation succeeds when:

- [ ] PASS verdict exists in AUDIT_REPORT.md
- [ ] Version validated (Target > Current Tag)
- [ ] Reality matches Promise (all planned files exist, no missing)
- [ ] Open security blockers reviewed
- [ ] Test audit completed
- [ ] Section 4 Razor final check passed
- [ ] SYSTEM_STATE.md synced with actual file tree
- [ ] Merkle seal calculated and recorded in META_LEDGER
- [ ] Session committed and pushed
- [ ] Merge/PR/tag options presented to user

## Integration with S.H.I.E.L.D.

This skill implements:

- **Session Seal**: Cryptographic proof that Reality matches Promise
- **Version Gate**: Prevents sealing with stale or mismatched versions
- **Reality Audit**: File-by-file comparison against blueprint
- **Hash Chain Finalization**: Calculates session seal for META_LEDGER
