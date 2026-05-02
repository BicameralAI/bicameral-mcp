---
name: qor-governance-compliance
metadata:
  category: governance
description: Enforce FailSafe physical isolation and environment compliance constraints across repository structure, platform limits, and security hygiene.
creator: MythologIQ Labs, LLC
license: Proprietary (FailSafe Project)
phase: governance
tone_aware: false
gate_reads: ""
gate_writes: ""
---
# Compliance Skill

## Enforce Physical Isolation and Environment-Specific Constraints

**Skill Name:** qor-governance-compliance
**Version:** 1.0
**Purpose:** Verify that all repository changes adhere to Physical Isolation rules and Environment Compliance requirements.

---

## Usage

```bash
# Full compliance audit
/qor-governance-compliance audit

# Check physical isolation only
/qor-governance-compliance isolation

# Check platform constraints only
/qor-governance-compliance constraints
```

---

## What This Skill Does

This skill enforces the "FailSafe Golden Rules" for repository integrity:

1.  **Physical Isolation**: Ensures app code stays in `FailSafe/` and workspace governance stays at the root.
2.  **Environment Compliance**: Verifies that workflows, agents, and skills meet the technical requirements of Antigravity, VSCode, and Claude (e.g., Antigravity's 250-char description limit).
3.  **Security Hygiene**: Audits the safety of Marketplace tokens and sensitive files.
4.  **Structure Integrity**: Validates that all directories match the locked structure in `.qor/workspace.json`.

---

## Skill Instructions

When this skill is invoked:

### 1. Perform Structural Audit

Verify that the "Isolation Boundary" is intact:

- **Forbidden at Root**: Ensure `src/`, `extension/`, `build/`, `targets/` are NOT at the root level.
- **Mandatory in FailSafe/**: Ensure the extension project and platform source directories exist in `FailSafe/`.
- **Root Hygiene**: Check that the root only contains `.agent/`, `.claude/`, `.qor/`, `.failsafe/`, `docs/`, and essential config files.

### 2. Perform Constraint Audit

Check all workflows in `FailSafe/` for platform-specific violations:

- **Antigravity**: Check all `.md` files in `FailSafe/Antigravity/` for `description` lengths > 250 characters.
- **VSCode**: Check that `FailSafe/VSCode/` uses the flat `prompts/` structure and `.prompt.md` extensions.
- **Claude**: Check for XML skill tags if required.

### 3. Perform Security Audit

- Verify `.claude/.vsce-token` and `.claude/.ovsx-token` exist and are gitignored.
- Check `.qor/workspace.json` for the `sensitiveFiles` entry.
- Ensure no tokens are leaked in `docs/` or `README.md`.

### 4. Report Findings

Generate a structured report:

- ✅ **PASS**: Requirement met.
- ⚠️ **WARNING**: Non-breaking deviation or recommendation.
- ❌ **FAIL**: Critical violation that blocks deployment or breaks isolation.

---

## Validation Protocols

### Physical Isolation (Protocol-A)

- **Rule**: `FAILSAFE_ISOLATION_BOUNDARY` must be 100% consistent.
- **Check**: `ls FailSafe/extension` -> If False: ❌ FAIL.
- **Check**: `ls extension/` -> If True: ❌ FAIL.

### Antigravity Description (Protocol-B)

- **Rule**: `GEMINI_DESC_LIMIT` = 250.
- **Action**: Invoke `FailSafe/build/validate.ps1` to perform character counts.

### VSCode Flatness (Protocol-C)

- **Rule**: `VSCODE_PROMPT_PATH` = `.github/prompts/`.
- **Check**: `ls FailSafe/VSCode/Genesis` -> If True: ❌ FAIL (Modules must be flattened to `prompts/`).

---

## Automated Enforcement

The skill should periodically run the automated check script:
`G:\MythologIQ\FailSafe\.agent\skills\compliance\scripts\verify-compliance.ps1`

---

## Success Criteria

1.  ✅ **Zero Isolation Leaks**: App code never drifts back to the root.
2.  ✅ **100% Metadata Compliance**: No workflow ever exceeds platform character limits.
3.  ✅ **Credential Safety**: Marketplace tokens are never committed.
4.  ✅ **Deployment Readiness**: The `FailSafe/` container is always ready to be packaged.

---

_This skill is the "Guardian of the Architecture" and must be consulted before every /qor-substantiate call._
