---
name: qor-help
description: >-
  Conversational catalog and SDLC navigator. Use when: (1) Need a command overview, (2) Stuck mid-workflow and want a recommendation based on current session state, or (3) Have a specific question about which skill applies.
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic

    path: qor/skills/meta/qor-help
phase: help
tone_aware: false
gate_reads: ""
gate_writes: ""
---
# /qor-help — Conversational catalog and SDLC navigator

<skill>
  <trigger>/qor-help</trigger>
  <phase>ANY</phase>
  <output>Catalog + ASCII SDLC flow chart, or a state-aware recommendation, or a routed answer to a free-form question</output>
</skill>

## Intro — How to use /qor-help

Three modes:

- **bare**: `/qor-help` — show the full command catalog, the ASCII SDLC flow chart, and these usage instructions.
- **stuck mode**: `/qor-help --stuck` — diagnose where the current session is in the SDLC by reading `.qor/gates/<sid>/*.json`, then recommend the next skill with one-paragraph rationale.
- **question mode**: `/qor-help -- "<your question>"` — answer a free-form question by routing against the catalog and (when relevant) the current session state. The LLM running this skill is the routing engine; the catalog is the source of truth.

All three modes are read-only. /qor-help never invokes another skill — it recommends; you invoke.

## SDLC Flow

```
                           [ /qor-status ]   <- diagnose at any time
                                  |
                  +---------------+---------------+
                  |                               |
                  v                               v
             (new work)                      (--stuck?)
                  |                               |
                  v                               v
       /qor-research              read .qor/gates/<sid>/*.json
                  |                infer phase, recommend next skill
                  v
       /qor-plan ----> /qor-audit ----> /qor-implement ----> /qor-substantiate
                          |  ^                   |
                          |  | VETO              v
                          v  |                /qor-debug   (regression)
                        amend                    |
                          |                      v
                          +--> /qor-refactor   /qor-organize
                          +--> /qor-remediate (3+ same-signature VETOs)

Bundles: /qor-deep-audit, /qor-onboard-codebase, /qor-process-review-cycle
```

## Catalog

### SDLC Chain (research -> plan -> audit -> implement -> substantiate -> validate)

| Command | Purpose | Typical When |
|---|---|---|
| `/qor-research` | Investigate before planning. Surface conventions, deps, prior art. | Beginning of any non-trivial work; chain start. |
| `/qor-plan` | Author plan-*.md with phases, tests, open questions. | After research; before audit. |
| `/qor-audit` | Adversarial PASS/VETO review. Each VETO ground names its required next action per `qor/references/doctrine-audit-report-language.md`: Razor -> `/qor-refactor`; Orphan or Macro-arch -> `/qor-organize`; Plan-text -> Governor amends plan, re-runs `/qor-audit`; Process-level -> `/qor-remediate`; Code-logic defect -> `/qor-debug`. | Before any L2/L3 risk implementation. |
| `/qor-implement` | Execute work under KISS constraints after PASS. | After `/qor-audit` PASS. |
| `/qor-refactor` | File-internal logic shape (Section 4). | When audit/implement detects bloat. |
| `/qor-debug` | Root-cause diagnosis (cross-cutting). | Regression / hallucination / degradation. |
| `/qor-substantiate` | Seal session, record Merkle evidence. | End of completed work session. |
| `/qor-validate` | Verify chain integrity + criteria. | Before delivery; on repeat failure -> `/qor-remediate`. |
| `/qor-remediate` | Process-level fix (NOT code). Absorbed the retired qor-course-correct skill. | Process Shadow Genome severity-sum threshold breach; `repeated_veto_pattern` event fired by `qor/scripts/veto_pattern.py` (>=2 consecutive sealed phases each needing >1 audit pass); capability-shortfall cascade. |

### Memory & Meta

| Command | Purpose | Typical When |
|---|---|---|
| `/qor-status` | Diagnose lifecycle stage + next action. | Any time you need current state. |
| `/qor-tone` | Set communication tier for the session (technical / standard / plain). | When rendering audience changes mid-session. |
| `/qor-document` | Author / update governance docs. | Before delivery; doc rot detected. |
| `/qor-organize` | Project-level structure (directory topology). | When audit flags Orphan/Macro; reorganization needed. |
| `/qor-bootstrap` | Initialize Qor-logic DNA for a **new workspace**. | First-time setup. NOT for new features. |
| `/qor-help` | This command. | When uncertain. |
| `/qor-repo-audit` | Repo-level audit (separate from per-feature audits). | Before release; audit rotation. |
| `/qor-repo-release` | Release ceremony + tagging. | After validate PASS; when shipping. |
| `/qor-repo-scaffold` | Create new repo from template. | First-time repo creation. |
| `/qor-ab-run` | Run persona-vs-stance A/B measurement via parallel Task subagents. | Before Phase 39b persona sweep; when empirical evidence is needed for Identity Activation rewrites on stance-critical skills. |

### Governance

| Command | Purpose | Typical When |
|---|---|---|
| `/qor-shadow-process` | Append a process-failure event to PROCESS_SHADOW_GENOME. | Auto-invoked by override paths; rarely called manually. |
| `/qor-governance-compliance` | Verify workspace governance compliance (root hygiene, sensitive file checks, structure integrity). | Periodic governance audit; before release. |

### Migrated qore-* skills

These skills migrated from the qore-* prefix during the SSoT consolidation; names retain the "qor-" prefix per Phase 7 rename.

| Command | Purpose | Typical When |
|---|---|---|
| `/qor-docs-technical-writing` | Author or revise technical writing under doctrine. | When documentation needs structured authoring. |
| `/qor-meta-log-decision` | Append a structured decision record. | Major decision points; ADR-style logging. |
| `/qor-meta-track-shadow` | Track shadow-genome-style failure patterns at the meta level. | When patterns of failure emerge across cycles. |

### Workflow Bundles (multi-skill orchestration)

| Bundle | Composition | Typical When |
|---|---|---|
| `/qor-deep-audit` | recon (3 phases) + remediate (3 phases) — large, decomposed | Pre-release readiness, absorbing a codebase, comprehensive tech-debt inventory. |
| `/qor-deep-audit-recon` | research subagents -> synthesize RESEARCH_BRIEF -> 3x verification | Investigation only; ends at brief. |
| `/qor-deep-audit-remediate` | plan -> implement -> validate (consumes RESEARCH_BRIEF) | Action half; after recon brief is approved. |
| `/qor-onboard-codebase` | research -> organize -> audit -> plan (4 phases, 3 checkpoints) | Inheriting / merging an external codebase. |
| `/qor-process-review-cycle` | shadow-sweep -> remediate -> audit (3 phases, 2 checkpoints) | Periodic process health check (weekly/monthly/post-incident). |

Bundles honor `qor/gates/workflow-bundles.md` (checkpoints, budgets, decomposition) and `qor/references/doctrine-token-efficiency.md`.

## Mode: --stuck

State-aware recommendation. Read-only.

Protocol:

1. Resolve session_id via `qor.scripts.session.current()` — the canonical helper that reads `.qor/session/current` AND validates the content against `SESSION_ID_PATTERN` (Phase 23 LOW-2 fix). If `current()` returns `None` (no marker, stale beyond TTL, or invalid format), recommend `/qor-status` and stop. Do NOT bypass the validator with naive `Path('.qor/session/current').read_text()` — naive reads fail open on corrupted markers and pass unsanitized text to the glob in step 2 (OWASP A03; per `qor/references/doctrine-owasp-governance.md` §A03 skill-prose worked example).
2. Glob `.qor/gates/<session_id>/*.json` and read each artifact.
3. Determine the highest-rank artifact present. Rank order: `research` < `plan` < `audit` < `implement` < `substantiate`.
4. If `audit.json` present, read its `verdict` field (PASS or VETO) and `findings_categories`.
5. Map state to recommendation:
   - No artifacts -> `/qor-research` (or `/qor-plan` if research was done out-of-gate; ask the operator).
   - `plan.json` only -> `/qor-audit`.
   - `audit.json` verdict=PASS -> `/qor-implement`.
   - `audit.json` verdict=VETO -> Governor amends the plan per `findings_categories`. Cite the required next action from `qor/references/doctrine-audit-report-language.md` per category.
   - `implement.json` present -> `/qor-substantiate`.
   - `substantiate.json` present -> session sealed; recommend a fresh `/qor-plan` or `/qor-research` for the next phase.
6. Emit a one-paragraph recommendation: state observed, recommended skill, rationale, the exact slash-command to invoke.

No file writes. Routing is read-only inference.

## Mode: -- "question"

Free-form routing. Read-only.

Protocol:

1. Capture the operator's question text (everything after `-- `).
2. Optionally perform the --stuck state-read for context.
3. Use the catalog above plus current state to identify the 1-3 most relevant skills.
4. Answer with: which skill(s) apply, why, the exact slash-command to invoke, what the operator should expect to see.
5. If the question maps to no skill (e.g., asking about external tooling), say so plainly and suggest the closest fit.

The LLM running this skill is the routing engine. The catalog is the source of truth — never invent skills. When the question is ambiguous, ask one clarifying question before recommending.

## Constraints

- **NEVER** execute other skills from within qor-help (recommend, never invoke)
- **NEVER** write files
- **ALWAYS** recommend `/qor-status` when the operator's state is unclear and --stuck cannot resolve it (e.g., no `.qor/` directory)
- **ALWAYS** cite the catalog as the source of truth for question-mode answers; do not invent skills

## Success Criteria

- [ ] Bare invocation displays intro + ASCII SDLC flow chart + catalog
- [ ] --stuck mode reads gate artifacts and emits a recommendation
- [ ] question mode routes the question to 1-3 catalog skills with rationale
- [ ] No skill execution from within /qor-help

## Integration with S.H.I.E.L.D.

This skill implements:

- **Lifecycle Navigation**: Entry point for discovering available commands and routing intent.
- **State-aware Recommendation**: --stuck mode reads `.qor/gates/<sid>/*.json` to infer SDLC position.
- **Question Routing**: question mode answers free-form operator questions against the catalog.
- **Read-only Governor Persona**: routing guidance without execution.
