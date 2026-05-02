# qor-plan Step Extensions

Extracted prose for Steps 0.5 and 1.a of `qor/skills/sdlc/qor-plan/SKILL.md` (Phase 16 refactor — keeps SKILL.md under Section 4 Razor).

## Step 0.5: Phase branch creation (Phase 13 wiring)

Before authoring a plan, cut a per-phase branch. Dirty tree blocks checkout; operator chooses stash / commit / abandon.

```python
# Phase 13 wiring: dirty-tree check + per-phase branch
import subprocess
from qor.scripts import governance_helpers as gh

result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
if result.stdout.strip():
    raise gh.InterdictionError(
        "Working tree dirty; operator must choose stash/commit/abandon before plan branch"
    )

phase_num, slug = gh.derive_phase_metadata(plan_path)  # raises on letter-suffix legacy plans
subprocess.run(["git", "checkout", "-b", f"phase/{phase_num:02d}-{slug}"], check=True)
```

Plan header MUST declare `**change_class**: hotfix | feature | breaking` (bold — V-2). Doctrine test `test_plans_declare_change_class` enforces.

## Step 1b — Documentation-integrity dialogue (Phase 28 wiring)

Before authoring the plan body, elicit doc-integrity declarations per `qor/references/doctrine-documentation-integrity.md`. Dialogue script (one question at a time):

1. **Tier**: "What `doc_tier` applies to this plan?"
   - `minimal` -- README only
   - `standard` -- README + glossary
   - `system` -- full topology (architecture + lifecycle + operations + policies)
   - `legacy` -- bypass, requires rationale

2. **Default + warnings**:
   - If operator skips the question: default to `standard` and warn.
   - If operator picks `system` without any terms_introduced: warn ("system tier typically introduces concepts; continue?").

3. **Terms** (only for standard/system): "Does this plan introduce any new terms (domain concepts, acronyms, canonical names)? If yes, list them with their canonical home file."

4. **Boundaries** (only for standard/system):
   - "What are this feature's limitations?" (things it cannot do)
   - "What are its non-goals?" (things it chooses not to do)
   - "Any exclusions?" (unsupported scenarios)

5. **Legacy rationale** (only for legacy): "Declare rationale for bypass; will be logged to shadow genome as severity-2 `degradation` event (kind=`doc_tier_legacy_declared`)."

6. **Emission**: when tier is `legacy`, call the helper before Step Z:

```python
from qor.scripts import doc_integrity
doc_integrity.emit_legacy_tier_event(
    session_id=sid,
    rationale=operator_rationale,
)
```

7. **Schema enforcement**: plans declaring `doc_tier: legacy` without `doc_tier_rationale` fail schema validation at Step Z (`plan.schema.json` carries an `if-then` rule). No runtime bypass path exists.

## Step 1.a — Capability check (agent-teams parallel mode, Phase 8 wiring)

```python
import qor_platform as qplat
import shadow_process

if qplat.is_available("agent-teams"):
    # Fan out specialist tracks (frontend/backend/infra) in parallel via TeamCreate;
    # synthesize results in this skill.
    mode = "teams"
else:
    state = qplat.current() or {}
    if state.get("detected", {}).get("host") == "claude-code":
        # claude-code host but agent-teams not declared -> log capability_shortfall
        shadow_process.append_event({
            "ts": shadow_process.now_iso(), "skill": "qor-plan", "session_id": sid,
            "event_type": "capability_shortfall", "severity": 2,
            "details": {"capability": "agent-teams"},
            "addressed": False, "issue_url": None, "addressed_ts": None,
            "addressed_reason": None, "source_entry_id": None,
        })
    mode = "sequential"
```

Contract for `teams` mode (reserved for future harness wiring): `TeamCreate(<spec>) -> [{track, deliverable}, ...]`. Skill synthesizes the track outputs into a single artifact.
