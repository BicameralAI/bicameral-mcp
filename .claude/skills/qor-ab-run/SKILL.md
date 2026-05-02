---
name: qor-ab-run
description: A/B measurement tool. Orchestrates parallel Task-tool subagent dispatches to measure detection rate under persona-named vs stance-directive-only Identity Activation variants. Produces docs/phase39-ab-results.md. No external API dependency.
user-invocable: true
category: meta
phase: meta
autonomy: autonomous
tone_aware: false
gate_reads:
  - tests/fixtures/ab_corpus/MANIFEST.json
  - tests/fixtures/ab_corpus/variants/*.md
gate_writes:
  - docs/phase39-ab-results.md
  - .qor/gates/<session_id>/ab-run.json (direct pathlib-write; no schema, advisory)
---

# /qor-ab-run — Agent Team A/B measurement

<skill>
  <trigger>/qor-ab-run</trigger>
  <phase>meta (measurement utility; not part of SDLC chain)</phase>
  <output>docs/phase39-ab-results.md + .qor/gates/&lt;session_id&gt;/ab-run.json</output>
</skill>

## Purpose

Measures detection rate of seeded defects under two Identity Activation variants (persona-named vs stance-directive-only) for stance-critical skills. Produces evidence artifacts that Phase 39b Phase 2 persona sweep consumes to apply the R3 Identity Activation rewrite rule.

Zero external dependency: orchestration is parallel `Task`-tool subagent dispatch within Claude Code itself. Aligned with `qor/references/doctrine-context-discipline.md` §4 (subagent invocation rule: `subagent_type: "general"` by default).

## When to invoke

- Operator wants empirical evidence for persona-vs-stance Identity Activation effectiveness on `/qor-audit` or `/qor-substantiate` (the two stance-critical skills).
- Before running Phase 39b Phase 2 persona sweep for the R3 conditional rewrite gate.
- Cost: zero marginal — runs within existing Claude Code session. Wall-time ~2-5 min for 20 parallel subagent dispatches.

## Execution Protocol

### Step 0: Gate check

Verify corpus inputs exist:
- `tests/fixtures/ab_corpus/MANIFEST.json` (20 declared defects)
- `tests/fixtures/ab_corpus/variants/qor-audit.persona.md`
- `tests/fixtures/ab_corpus/variants/qor-audit.stance.md`
- `tests/fixtures/ab_corpus/variants/qor-substantiate.persona.md`
- `tests/fixtures/ab_corpus/variants/qor-substantiate.stance.md`
- Subagent prompt template at `qor/skills/meta/qor-ab-run/references/ab-subagent-prompt.md`

If any missing: ABORT with explicit file path. The Phase 39 v0.29.0 release shipped all corpus fixtures.

### Step 1: Load inputs

Read manifest, 4 variant files, prompt template, and 20 fixture files. Record the current session's model (from environment context — needed for O5 disclosure in results artifact).

### Step 2: Construct 20 subagent dispatches

For each `(skill, variant, replication)` combination — 2 skills × 2 variants × 5 replications = 20 combinations — build one subagent task:

- `subagent_type`: `"general"` (per doctrine §4; no persona-typed subagents without evidence)
- `description`: `"A/B trial: {skill}/{variant}/rep{N}"` (under 10 words)
- `prompt`: the `ab-subagent-prompt.md` template with `{VARIANT_IDENTITY_ACTIVATION_BLOCK}` replaced by the variant's Identity Activation content and `{FIXTURES_CONCATENATED}` replaced by all 20 fixture contents formatted per the template.

### Step 3: Dispatch in parallel

Issue ALL 20 `Task` tool calls in a single message for concurrent execution. Claude Code executes parallel tool calls in one response. Wait for all subagent results before proceeding.

### Step 4: Parse and aggregate

Each subagent returns a JSON object `{"trials": [{"defect_id": N, "findings_categories": [...]}, ...]}`. Collect results into a list of trial-batch records:

```python
from qor.scripts import ab_aggregator

trial_batches = [
    {"skill": "qor-audit", "variant": "persona", "replication": 1,
     "trials": ab_aggregator.parse_trial(subagent_response_text, defect_ids)},
    ...
]
aggregated = ab_aggregator.aggregate(trial_batches)
```

`aggregate` returns per-`(skill, variant)` mean + stddev detection rates and per-skill winner declarations.

### Step 5: Write results artifact

```python
from pathlib import Path
Path("docs/phase39-ab-results.md").write_text(
    ab_aggregator.render_markdown(aggregated, model=current_model),
    encoding="utf-8",
)
```

### Step 6: Write ab-run gate artifact (direct pathlib; no schema)

```python
import json, pathlib
from qor.scripts import session as _session

sid = _session.get_or_create()
gate_path = pathlib.Path(".qor/gates") / sid / "ab-run.json"
gate_path.parent.mkdir(parents=True, exist_ok=True)
gate_path.write_text(
    json.dumps({"trial_batches": trial_batches, "aggregated": aggregated,
                "model": current_model}, indent=2),
    encoding="utf-8",
)
```

The ab-run artifact is advisory (raw data + aggregate for audit trail) and is NOT schema-validated — there is no `ab-run.schema.json`. Direct pathlib write is intentional; `gate_chain.write_gate_artifact` is not appropriate here.

## Constraints

- **NEVER** use `subagent_type` other than `"general"` — doctrine §4.
- **NEVER** invoke external APIs (no `anthropic` SDK, no `ANTHROPIC_API_KEY`) — Claude Code's Task tool is the LLM invocation mechanism.
- **NEVER** construct subagent prompts from untrusted input — fixtures are seeded test data only; never accept arbitrary user input into a subagent prompt.
- **ALWAYS** record the main session's model identity in `docs/phase39-ab-results.md` — the A/B numbers are model-specific; readers must know whether the evidence describes Sonnet, Opus, or Haiku.
- **ALWAYS** dispatch all 20 Task calls in a single message for concurrent execution.
- **ALWAYS** treat malformed subagent responses as missed detections (not errors) — one bad subagent should not abort the entire run.

## Measurement scope (O1 disclosure from Pass 1 audit)

The subagent receives only the variant Identity Activation block + fixtures + the instruction template. It does NOT receive the full `/qor-audit` or `/qor-substantiate` SKILL.md body. This isolates the Identity Activation variable cleanly for the A/B comparison but means the measured detection rates are NOT directly comparable to real-skill performance (where full skill body provides Razor/OWASP/ghost-UI/etc. rubrics). Operators reading `docs/phase39-ab-results.md` should understand: the numbers compare persona-named vs stance-directive Identity Activation prose in isolation, not end-to-end skill behavior.

## Stddev expectations (O4 disclosure)

If the session's model is run at deterministic sampling, stddev across 5 runs may approach zero. Zero stddev does not invalidate the mean detection rate but reduces the evidential robustness — the 5-run replication is a precaution against non-determinism that may not fire. Report both mean and stddev; interpret deltas near the tie threshold (±5pp) with stddev context.

## Model inheritance (O5 disclosure)

Task subagents run on the same model the main Claude Code session uses. `docs/phase39-ab-results.md` records the model identity so subsequent readers can interpret the evidence appropriately.

## Next Step

After this skill produces `docs/phase39-ab-results.md`, invoke `/qor-plan` (or continue Phase 39b Phase 2 implement) to apply the results to the persona sweep. The R3 Identity Activation rewrite rule fires ONLY if the results declare `winner: "stance"` for a given skill.
