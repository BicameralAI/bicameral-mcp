---
name: qor-repo-scaffold
description: >-
  /qor-repo-scaffold - Generate Governance Scaffold
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/meta/qor-repo-scaffold
phase: implement
tone_aware: false
gate_reads: ""
gate_writes: implement
---
# /qor-repo-scaffold - Generate Governance Scaffold

<skill>
  <trigger>/qor-repo-scaffold</trigger>
  <phase>implement</phase>
  <output>Created community files from templates</output>
</skill>

## Purpose

Generate missing repository governance files. Uses templates with variable substitution for project-specific values.

## Execution Protocol

### Step 1: Detect Project Context

```bash
# Project name
cat package.json | jq -r '.name' 2>/dev/null || basename $(pwd)

# License type
head -1 LICENSE 2>/dev/null | grep -oE '(MIT|Apache|GPL|BSD|ISC)' || echo "MIT"

# Maintainer email
git config user.email || cat package.json | jq -r '.author.email' 2>/dev/null

# Current year
date +%Y
```

Store as:
- `{{PROJECT_NAME}}`
- `{{LICENSE_TYPE}}`
- `{{MAINTAINER_EMAIL}}`
- `{{YEAR}}`

### Step 2: Run Audit First

```
Execute: /qor-repo-audit (internal)
Capture: List of MISSING files
```

IF no missing files:
- REPORT: "Repository already meets Gold Standard"
- DONE

### Step 3: Generate Missing Files

For each file marked MISSING, load template and substitute variables:

| Missing File | Template |
|--------------|----------|
| CODE_OF_CONDUCT.md | Contributor Covenant |
| CONTRIBUTING.md | Standard guide |
| SECURITY.md | Security policy |
| GOVERNANCE.md | Project governance |
| .github/ISSUE_TEMPLATE/*.yml | Issue templates |
| .github/PULL_REQUEST_TEMPLATE.md | PR template |

### Step 4: Stage Files

```bash
git add [created files]
```

### Step 5: Report

```markdown
## Scaffold Complete

**Project**: {{PROJECT_NAME}}
**License**: {{LICENSE_TYPE}}

### Files Created

| File | Path | Status |
|------|------|--------|
| [name] | [path] | Created & Staged |

### Next Steps

1. Review staged files: `git status`
2. Customize content as needed
3. Commit: `git commit -m "docs: add community governance files"`
4. Verify: `/qor-repo-audit`
```

### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/implement.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/implement.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "implement", human_oversight=ai_provenance.HumanOversight.ABSENT
)
gate_chain.write_gate_artifact(
    phase="implement", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/implement.schema.json`; the helper validates before write. Per Phase 54: repo-scaffold calls `ai_provenance.build_manifest` to embed AI provenance.

## Constraints

- **NEVER** overwrite existing files
- **NEVER** auto-commit (stage only, user owns final review)
- **ALWAYS** run /qor-repo-audit first to identify gaps
- **ALWAYS** use template substitution for project-specific values
- **ALWAYS** make template substitution idempotent

## Success Criteria

Scaffold succeeds when:

- [ ] Project context detected (name, license, email, year)
- [ ] /qor-repo-audit run to identify missing files
- [ ] All missing files generated from templates
- [ ] Template variables substituted correctly
- [ ] Files staged (not committed)
- [ ] Report generated listing created files

## Integration with S.H.I.E.L.D.

This skill implements:

- **Gold Standard Remediation**: Generates files identified by /qor-repo-audit
- **Template-Driven Generation**: Consistent community files across projects
- **Bootstrap Support**: Called silently by /qor-bootstrap for new repositories
- **Specialist Persona**: Precision file generation with variable substitution
