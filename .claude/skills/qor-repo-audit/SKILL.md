---
name: qor-repo-audit
description: >-
  /qor-repo-audit - Repository Governance Audit
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/meta/qor-repo-audit
phase: audit
tone_aware: false
gate_reads: ""
gate_writes: audit
permitted_tools: [Read, Grep, Glob, Bash]
permitted_subagents: []
model_compatibility: [claude-opus-4-7]
min_model_capability: opus
---
# /qor-repo-audit - Repository Governance Audit

<skill>
  <trigger>/qor-repo-audit</trigger>
  <phase>audit</phase>
  <persona>Judge</persona>
  <output>Gap report with compliance score and GitHub API metrics</output>
</skill>

## Purpose

Audit repository against GitHub Gold Standard. Integrates with GitHub API for community profile score when available.

## Execution Protocol

### Step 0.6: Pre-audit lints (Phase 55 wiring — best-effort)

If a current-phase plan file is discoverable, run the pre-audit lints against it (WARN-only). qor-repo-audit primarily audits repo state, not plan files, so lints no-op when no plan is in scope.

```bash
PLAN_PATH=$(python -c "from qor.scripts.governance_helpers import current_phase_plan_path; print(current_phase_plan_path() or '')" 2>/dev/null || echo "")
if [ -n "$PLAN_PATH" ] && [ -f "$PLAN_PATH" ]; then
  python -m qor.scripts.plan_test_lint --plan "$PLAN_PATH" || true
  python -m qor.scripts.plan_grep_lint --plan "$PLAN_PATH" --repo-root . || true
fi
```

### Step 1: Local File Inventory

Check for presence and placement:

**Community Files** (7 required):
```
Glob: README.md          -> has_readme
Glob: LICENSE            -> has_license
Glob: CODE_OF_CONDUCT.md -> has_coc
Glob: CONTRIBUTING.md    -> has_contributing
Glob: SECURITY.md        -> has_security
Glob: GOVERNANCE.md      -> has_governance
Glob: CHANGELOG.md       -> has_changelog
```

**GitHub Templates** (5 required):
```
Glob: .github/ISSUE_TEMPLATE/bug_report.yml    -> has_bug_template
Glob: .github/ISSUE_TEMPLATE/feature_request.yml -> has_feature_template
Glob: .github/ISSUE_TEMPLATE/documentation.yml -> has_docs_template
Glob: .github/ISSUE_TEMPLATE/config.yml        -> has_template_config
Glob: .github/PULL_REQUEST_TEMPLATE.md         -> has_pr_template
```

**README Contract** (if README exists):
```
Read: README.md (limit: 100)
Check: Contains link to CONTRIBUTING.md
Check: Contains link to SECURITY.md
Check: Contains link to Roadmap or CHANGELOG.md
```

### Step 2: GitHub API Check (Optional)

```bash
gh api repos/{owner}/{repo}/community/profile --jq '.'
```

IF command succeeds:
- Extract `health_percentage` (target: 100%)
- Extract `files` object (what GitHub detects)

IF command fails:
- REPORT: "GitHub API unavailable - using local checks only"
- CONTINUE with local results

### Step 3: Calculate Scores

```
local_score = (present_files / 12) * 100
github_score = health_percentage (if available)
```

### Step 4: Generate Gap Report

```markdown
## Repository Gold Standard Audit

**Repository**: [detected from git remote or folder name]
**Audit Date**: [timestamp]

### Scores

| Source | Score | Target |
|--------|-------|--------|
| Local Files | [X]/12 ([Y]%) | 100% |
| GitHub API | [Z]% | 100% |

### Community Files

| File | Required | Present | Status |
|------|----------|---------|--------|
| README.md | Yes | [check] | [PASS/FAIL] |
...

### Missing Items (Priority Order)

1. [HIGH] [file] - [reason]
2. [MEDIUM] [file] - [reason]

### Remediation

Run `/qor-repo-scaffold` to auto-generate missing files.
```

### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/audit.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/audit.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "audit",
    human_oversight=(
        ai_provenance.HumanOversight.PASS if payload.get("verdict") == "PASS"
        else ai_provenance.HumanOversight.VETO
    ),
)
gate_chain.write_gate_artifact(
    phase="audit", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/audit.schema.json`; the helper validates before write. Per Phase 54: repo-audit calls `ai_provenance.build_manifest` with the operator's verdict.

## Constraints

- **NEVER** modify any repository files during audit
- **NEVER** fail if GitHub API is unavailable (use local fallback)
- **ALWAYS** report specific missing files with remediation path
- **ALWAYS** calculate both local and API scores when available

## Success Criteria

Repo audit succeeds when:

- [ ] All 12 community/template files checked for presence
- [ ] README contract verified (links to CONTRIBUTING, SECURITY)
- [ ] Scores calculated (local + GitHub API if available)
- [ ] Gap report generated with priority-ordered missing items
- [ ] Remediation path provided (points to /qor-repo-scaffold)

## Integration with S.H.I.E.L.D.

This skill implements:

- **Gold Standard Enforcement**: Verifies repository meets community governance baseline
- **Pre-Scaffold Gate**: Identifies what /qor-repo-scaffold needs to generate
- **Audit Pass 7**: Referenced by /qor-audit for repository-level compliance
- **GitHub API Integration**: Optional remote health check via `gh` CLI
