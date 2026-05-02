---
name: qor-plan
description: >-
  Planning protocol following Rich Hickey's "Simple Made Easy" principles for creating implementation plans. Use when: (1) Designing complex features, (2) Planning multi-phase implementations, (3) Architecting new components, or (4) Any work requiring systematic planning before implementation.
metadata:
  category: development
  author: MythologIQ
  source:
    repository: https://github.com/MythologIQ/Qor-logic
    path: qor/skills/sdlc/qor-plan
phase: plan
tone_aware: false
gate_reads: research
gate_writes: plan
permitted_tools: [Read, Grep, Glob, Bash, Edit, Write]
permitted_subagents: []
model_compatibility: [claude-opus-4-7]
min_model_capability: opus
---
# /qor-plan - Simple Made Easy Planning

<skill>
  <trigger>/qor-plan</trigger>
  <phase>plan</phase>
  <persona>Governor</persona>
  <output>plan-*.md file with incremental phases and unit test descriptions</output>
</skill>

## Purpose

Create implementation plans following Rich Hickey's "Simple Made Easy" principles. This skill focuses on objective simplicity over subjective ease, avoiding complecting, and favoring composable, declarative, value-oriented designs.

## Core Principles

### Choose SIMPLE over EASY

Strive for un-braided, composable designs that minimize incidental complexity. Judge a tool, abstraction, or pattern by long-term properties: clarity, changeability, and robustness. "Easy to start" is not sufficient.

### Detect Complecting

Whenever you join concerns (state & time, data & behavior, configuration & code...), pause and seek an alternative that keeps them independent. Favor composition (placing things side-by-side) over interleaving.

### Prefer Values, Resist State

Immutable data is default. Mutable state must be narrowly scoped, well-named, and justified.

### Assess by Artifacts

Judge designs by what they produce: clarity, changeability, and robustness. Measure decisions by how much braid they remove, not how quickly they compile.

### Declarative > Imperative

Describe WHAT, not HOW. Lean on data, configuration, queries, and rule systems where possible.

### Polymorphism a la Carte

Separate data definitions, behavior specifications, and their connections. Avoid inheritance hierarchies that entangle unrelated facets.

### Guard-rails Are Not Simplicity

Tests, static checks, and refactors are valuable, but cannot compensate for complex design. Seek to remove complexity first.

## Execution Protocol

### Step 0: Gate Check (advisory — Phase 8 wiring)

Verify prior-phase artifact exists and is well-formed before proceeding.

```python
from qor.scripts import gate_chain, session

sid = session.get_or_create()
result = gate_chain.check_prior_artifact("plan", session_id=sid)
if not result.found:
    # Prompt user to override; on confirm:
    gate_chain.emit_gate_override(
        current_phase="plan",
        prior_phase_name="research",
        reason="user override: research.json not found",
        session_id=sid,
    )
elif not result.valid:
    gate_chain.emit_gate_override(
        current_phase="plan",
        prior_phase_name="research",
        reason=f"user override: {result.errors}",
        session_id=sid,
    )
```

Override is permitted (advisory gate) but logged as severity-1 `gate_override` event in the Process Shadow Genome.

**Phase 54 wiring**: when `gate_chain.emit_gate_override` raises `OverrideFrictionRequired` (the per-session override count has reached the threshold per `qor.scripts.override_friction` defaults), prompt the operator for a written justification (>=50 chars) and re-call `emit_gate_override` with `justification=<text>`. Per `qor/references/doctrine-ai-rmf.md` §MANAGE-1.1 + `qor/references/doctrine-eu-ai-act.md` Art. 14.

### Step 0.2: Install drift check (Phase 32 wiring)

Detect whether the operator's locally installed skills differ from the repo source. Non-blocking WARN; operator decides whether to reinstall before proceeding.

```bash
python -m qor.scripts.install_drift_check --host claude --scope repo || \
  echo "WARNING: Local skill install differs from repo source. Consider: qor-logic install --host claude --scope repo"
```

Fix on drift: `qor-logic install --host <host> --scope <scope>` regenerates the installed copy from source. See `qor/references/doctrine-governance-enforcement.md` §8 Install Currency for the full contract.

### Step 0.3: Model-pinning lint (Phase 55 wiring)

Verify the operator's running model meets each scoped skill's declared `min_model_capability`. Non-blocking WARN; operator decides whether to swap models before proceeding.

```bash
python -m qor.scripts.model_pinning_lint --repo-root . || true
```

Reads `QOR_MODEL_FAMILY` env (set by harness when available) and walks `qor/skills/**/SKILL.md` for `model_compatibility` + `min_model_capability` frontmatter. Emits stderr warnings for any skill whose declared minimum exceeds the current model's tier (`haiku < sonnet < opus`). Per `qor/references/doctrine-ai-rmf.md` §MANAGE-3.1.

### Step 0.5: Phase branch creation (Phase 13 wiring)

See `qor/skills/sdlc/qor-plan/references/step-extensions.md` for the full protocol.

### Step 1: Collaborative Design Dialogue

Before writing any plan, understand the design through conversation:

1. **Check project context first** — read existing files, docs, recent commits
2. **Ask questions one at a time** — prefer multiple choice when possible
3. **Focus on understanding**: purpose, constraints, success criteria, anti-goals
4. **Propose 2-3 approaches** with trade-offs before settling on design
5. **Present design in sections** (200-300 words) — validate each before proceeding
6. **YAGNI ruthlessly** — challenge every proposed feature: "Is this essential for v1?"

Only proceed to write the plan file after the user has validated the design direction.

### Step 1.a — Capability check (agent-teams parallel mode, Phase 8 wiring)

See `qor/skills/sdlc/qor-plan/references/step-extensions.md` for the full protocol.

### Step 1b: Documentation-integrity dialogue (Phase 28 wiring)

Before writing the plan, elicit `doc_tier`, `terms_introduced`, and `boundaries` from the operator per `qor/references/doctrine-documentation-integrity.md`. Full dialogue protocol: `qor/skills/sdlc/qor-plan/references/step-extensions.md` §Step 1b. Summary:

1. Ask tier: `minimal | standard | system | legacy` (multiple choice).
2. For standard/system: ask for newly introduced terms with `home:` paths.
3. For standard/system: ask for `limitations`, `non_goals`, `exclusions`.
4. For legacy: require a `doc_tier_rationale` (schema enforces; emit severity-2 `degradation` shadow event via `doc_integrity.emit_legacy_tier_event`).

Warn (do not block) if `doc_tier` is omitted (default `standard`) or if `system` is declared with no `terms_introduced`.

### Step 1c: Impact assessment dialogue (Phase 54 wiring)

Triggered when the operator declares `high_risk_target: true` in plan top-matter. The flag asserts that the downstream system being supported by this Qor-logic invocation is itself a high-risk AI system per EU AI Act Annex III; per `qor/references/doctrine-eu-ai-act.md`, this triggers an Art. 9 risk-management contract that the plan must fulfil via the `impact_assessment` block.

When `high_risk_target: true` is set, the plan MUST declare an `impact_assessment` object with the following five sub-fields (schema-enforced via `qor/gates/schema/plan.schema.json`):

- **purpose** — what the downstream system is designed to do, expressed in operator-facing prose.
- **affected_stakeholders** — who is affected by the system's decisions (users, third parties, operators, regulators).
- **identified_risks** — the operationally-relevant risks per AI RMF MAP-3.1 / EU AI Act Art. 9.
- **mitigations** — concrete controls that reduce the identified risks.
- **residual_risks** — risks remaining after mitigation that the operator accepts and surfaces to deployers.

Skipped silently when `high_risk_target` is false or omitted (default false). Maps to AI RMF MAP-3.1 / MAP-5.1 per `qor/references/doctrine-ai-rmf.md`.

### Step 2: Research Existing Code

Use existing code as foundation for plan. Identify existing abstractions, naming conventions, test structure, and integration points.

### Step 2b: Grounding Protocol (MANDATORY)

See `qor/references/doctrine-shadow-genome-countermeasures.md` for the full Grounding Protocol and Shadow Genome countermeasure inventory. Residual `{{verify: ...}}` tags in a plan block its submission.

### Step 2c: Cycle-count escalation check (Phase 37 wiring)

Before authoring a new plan, check whether the session already has three consecutive same-signature VETO audits. If so, the legal next action is `/qor-remediate`, not another plan iteration.

```python
from qor.scripts import cycle_count_escalator as cce

rec = cce.check(sid)
if rec:
    # Surface to operator: "Session has N consecutive VETOs on signature <sig>.
    # Recommended skill: /qor-remediate. Proceed with plan anyway, or escalate?"
    # On escalate: exit qor-plan and invoke /qor-remediate.
    # On decline: record the override and continue.
    if operator_declines:
        from qor.scripts import orchestration_override
        orchestration_override.record(
            session_id=sid,
            skill="qor-plan",
            recommended_skill=rec.suggested_skill,
            reason="operator elected to continue planning",
        )
        # Next cycle_count_escalator.check in this session will see the
        # suppression marker and skip.
```

The override event is unioned with `gate_override` in the gate-loop classifier (see `qor/scripts/remediate_pattern_match.py`), so repeated overrides still eventually escalate.

See `qor/references/doctrine-governance-enforcement.md` §10.4 "Cycle-count escalation" + §10.5 "Operator override and re-prompt suppression."

### Step 3: Create Plan File

Create plan markdown file with specific requirements:

#### Plan Structure

```markdown
# Plan: [feature/component name]

**change_class**: hotfix | feature | breaking

**doc_tier**: minimal | standard | system | legacy

[if doc_tier is standard/system and the plan introduces terms]
**terms_introduced**:
- term: TermName
  home: path/to/home.md

[if doc_tier is standard/system]
**boundaries**:
- limitations: [...]
- non_goals: [...]
- exclusions: [...]

[if doc_tier is legacy]
**doc_tier_rationale**: one-line justification (logged to shadow genome)

## Open Questions

[List any open questions or edge cases requiring clarification]

## Phase 1: [Phase Name]

### Affected Files

- [file path 1] - [concise change summary]
- [file path 2] - [concise change summary]

### Changes

[Specific code changes, minimal prose]

### Unit Tests

- [test file path] - [what it tests, why important]

## CI Commands

- `<command 1>` — <what it validates>
- `<command 2>` — <what it validates>
```

**Phase 38 B22 contract**: every plan from Phase 38 forward carries a `## CI Commands` section listing the commands the operator runs locally to validate the plan before substantiate. The same list appears in the plan gate artifact's `ci_commands` field (required by `qor/gates/schema/plan.schema.json`). Pre-Phase-38 plans are grandfathered at the test layer; no retroactive migration.

#### Plan Requirements

- **Specific code changes** - Describe concisely with minimal surrounding prose
- **Incremental phases** - 2-3 logical phases that stack on each other
- **Well-typed interfaces** - Self-documenting, self-consistent with surrounding code
- **Unit test descriptions** - Grouped with relevant phases
- **Affected files summary** - At top of each phase

### Step 4: Avoid Common Pitfalls

**Do NOT include:**

- Exploration steps (grep for X, consult docs)
- Backwards compatibility concerns
- Feature gating or release plans
- Concluding errata (future considerations, next steps)
- Tests that only assert presence (file existence, `<substring> in <file_text>`, function-defined) without invoking the unit and verifying its output. Per `qor/references/doctrine-test-functionality.md`.

**DO include:**

- Complex logic unit test descriptions
- Open questions flagged at TOP of plan
- Refactoring required for clean abstractions

### Step 5: Review Plan

Before finalizing, ensure:

- [ ] Plan is precise and consistent with itself
- [ ] Follows "Simple Made Easy" principles
- [ ] Open questions are clearly flagged
- [ ] No backwards compatibility concerns
- [ ] No concluding errata sections
- [ ] Each unit test description names the behavior it confirms (the unit's output for a given input), not the artifact it expects to find. Per `qor/references/doctrine-test-functionality.md`.

## Success Criteria

A reader unfamiliar with code should be able to:

- Locate a part without untangling others
- Understand the change without reading surrounding code
- Replace a part without breaking other parts
- See the complete scope of work

### Step Z: Write Gate Artifact (Phase 11D wiring)

Persist the structured gate artifact at `.qor/gates/<session_id>/plan.json` so downstream phases can read it via `gate_chain.check_prior_artifact`.

```python
from qor.scripts import gate_chain, shadow_process, ai_provenance

# Build payload conforming to qor/gates/schema/plan.schema.json
payload = {
    "ts": shadow_process.now_iso(),
    # ... phase-specific required fields (see schema)
}
manifest = ai_provenance.build_manifest(
    "plan", human_oversight=ai_provenance.HumanOversight.ABSENT
)
gate_chain.write_gate_artifact(
    phase="plan", payload=payload, session_id=sid, ai_provenance=manifest,
)
```

Schema lives at `qor/gates/schema/plan.schema.json`; the helper validates before write. Plans declaring `doc_tier: legacy` without `doc_tier_rationale` fail schema validation (enforced by the `if-then` rule added in Phase 28); operator must either add rationale or raise the tier. Per Phase 54: every gate-writing skill calls `ai_provenance.build_manifest` and passes the result through `ai_provenance=manifest`; closes EU AI Act Art. 13/50 transparency surface.

## Delegation

Per `qor/gates/delegation-table.md`:

- **Plan complete** → `/qor-audit` (next phase).
- **Plan needs architectural restructuring** (changing where things live, splitting modules across boundaries) → `/qor-organize` (project topology is its domain). The plan should reference the organize step explicitly, not embed restructuring instructions.
- **Re-research needed** for an open question that emerges during planning → return to `/qor-research`.

## Constraints

- **NEVER** worry about backwards compatibility (prefer streamlined, clean codebase)
- **NEVER** add concluding errata (future considerations belong in next plan)
- **NEVER** include exploration steps (do research before writing plan)
- **NEVER** skip the collaborative dialogue — do not jump straight to writing a plan file
- **ALWAYS** ask questions one at a time (prefer multiple choice)
- **ALWAYS** flag open questions at TOP of plan
- **ALWAYS** list unit test files FIRST in each phase, before implementation files (TDD enforcement)
- **ALWAYS** prioritize SIMPLE over EASY
- **ALWAYS** note CI commands needed to validate the plan (clippy/lint/test flags matching CI)
- **ALWAYS** declare `doc_tier` in plan top-matter (warn and default to `standard` if omitted; per `doctrine-documentation-integrity.md`)
- **ALWAYS** list terms introduced by the plan under `terms_introduced` when tier is standard or system

## Integration with Qor-logic

This skill implements:

- **Simple Made Easy**: Objective simplicity over subjective ease
- **Complecting Detection**: Identifies and removes braided concerns
- **Value-Oriented Design**: Prefers immutable data and composable abstractions
- **Incremental Planning**: Phased approach with clear deliverables

---

**Remember**: Simple is not easy. Dialogue before design, design before plan, plan before code.
