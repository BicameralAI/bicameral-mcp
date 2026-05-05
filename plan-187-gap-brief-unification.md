# Plan: unify gap-judge findings into `IngestResponse.brief`

**change_class**: breaking

**doc_tier**: standard

**terms_introduced**:
- term: `BriefEnvelope`
  home: contracts.py

**boundaries**:
- limitations: gap-judge findings are still computed by the caller-LLM via the existing rubric in `bicameral-judge-gaps`. The brief just bundles the rubric input so callers render a single section instead of two.
- non_goals: reintroducing standalone `bicameral.brief` or `bicameral.judge_gaps` MCP tools — both stay as server-side auto-fires inside `ingest`. No new server-side LLM call.
- exclusions: backwards-compat shim for `judgment_payload` / `judgment_payloads`. Removed cleanly per Simple Made Easy; no parallel-field carry.

## Open Questions

None. All design choices resolved in `/qor-plan` dialogue + audit-cycle amendment:
- Q2 response shape → `BriefEnvelope` model with `decisions`, `drift_candidates`, `divergences`, `gaps`, `rubric`, `action_hints`, `suggested_questions`. Mirrors `PreflightResponse`'s brief surface (plus `rubric` to carry the GapRubric reference that previously lived on `judgment_payload.rubric`); one model, two consumers.
- Q3 `judgment_payload` disposition → removed entirely. The existing `# kept for backward compat` comment is only a few minors of compat anyway; cleaner to remove than carry.
- Audit-F2 resolution → rubric metadata carried on `BriefEnvelope.rubric: GapRubric | None`, allowing the existing `tests/test_v0416_gap_judge.py` assertions on `rubric.categories` to migrate cleanly to `response.brief.rubric.categories`. No information loss vs the legacy `judgment_payload.rubric` field.

## Phase 1: Introduce `BriefEnvelope` + populate on `IngestResponse`

### Affected Files

- `tests/test_ingest_brief_unification.py` — new tests for the unified brief contract (4 tests)
- `tests/test_v0416_gap_judge.py` — migrate 5+ existing assertions from `response.judgment_payload[s].*` to `response.brief.gaps[*]` / `response.brief.rubric.*`. Specifically: `test_ingest_chain_attaches_judgment_payload` (line 368-386) currently asserts `response.judgment_payload is not None`, `len(response.judgment_payload.decisions) >= 1`, `len(response.judgment_payload.rubric.categories) == 5` — migrated to `response.brief is not None`, `len(response.brief.gaps) >= 1`, `len(response.brief.rubric.categories) == 5`. The other 4 references in the file follow the same shape; rename in-place
- `contracts.py` — add `BriefEnvelope` class with `rubric: GapRubric | None = None` field (carries the rubric reference that previously lived on `judgment_payload.rubric`); add `brief: BriefEnvelope | None` to `IngestResponse`; remove `judgment_payload` and `judgment_payloads` fields
- `handlers/ingest.py` — populate `brief` from the assembled gap-judge findings + drift hints; remove the `judgment_payload[s]` assignments at the response site
- `handlers/action_hints.py` — add `merge_drift_and_gap_hints(drift_hints, gap_hints) -> list[ActionHint]` helper that produces the unified action-hint list for the brief
- `handlers/gap_judge.py` — docstring text update at line 23 (replace `IngestResponse.judgment_payload` reference with `IngestResponse.brief.gaps` / `IngestResponse.brief.rubric`)
- **Out of scope but flagged**: `server.py:1119` contains `text=json.dumps({"judgment_payload": None, "topic": arguments["topic"]})` — this is the standalone `bicameral.judge_gaps` MCP tool's response shape, NOT `IngestResponse`. Same field name, different parent type, different contract. Implementer leaves this untouched. The `grep -rn "judgment_payload"` CI sanity check at the bottom of this plan will surface it; the implementer recognizes it as a non-cascading collision and skips

### Changes

**`contracts.py`** — new model just below `BriefDivergence`:

```python
class BriefEnvelope(BaseModel):
    """Unified brief envelope returned by ingest (and shared shape with PreflightResponse).

    The caller renders this single section instead of stitching together
    separate `judgment_payload` + flat fields. Server-side population of
    `gaps` removes the previously-fragile dual-render contract where agents
    silently dropped Step 6 of the bicameral-ingest skill.

    `rubric` carries the GapRubric reference that previously lived on
    `judgment_payload.rubric` (5 fixed categories per v0.4.19; structurally
    locked by Literal typing on GapRubricCategory.key). `None` when no
    gap-judge findings are present.
    """
    divergences: list[BriefDivergence] = []
    drift_candidates: list[BriefDecision] = []
    decisions: list[BriefDecision] = []
    gaps: list[BriefGap] = []
    rubric: GapRubric | None = None
    action_hints: list[ActionHint] = []
    suggested_questions: list[str] = []
```

`IngestResponse` modified:

```python
class IngestResponse(BaseModel):
    ingested: bool
    repo: str
    query: str
    source_refs: list[str]
    stats: IngestStats
    created_decisions: list[CreatedDecision] = []
    pending_grounding_decisions: list[dict] = []
    context_for_candidates: list[ContextForCandidate] = []
    source_cursor: SourceCursorSummary | None = None
    brief: BriefEnvelope | None = None  # new — see #187
    sync_status: LinkCommitResponse | None = None
    # judgment_payload + judgment_payloads removed per #187
```

**`handlers/ingest.py`** — replace the existing `judgment_payload` / `judgment_payloads` assembly (lines ~324-338, 403-404 in current code) with a `BriefEnvelope` build site:

```python
# After gap-judge auto-chain produces judgment_payloads:
gaps = []
for jp in judgment_payloads:
    gaps.extend(jp.findings)  # flatten across feature-group topics

# Drift candidates / divergences / decisions come from the existing
# brief-style assembly already running in handle_ingest. Action hints
# are merged from drift + gap sources via the new helper.
action_hints = merge_drift_and_gap_hints(
    drift_hints=drift_action_hints,  # existing local var
    gap_hints=[h for jp in judgment_payloads for h in jp.action_hints],
)

brief = BriefEnvelope(
    divergences=divergences,
    drift_candidates=drift_candidates,
    decisions=brief_decisions,
    gaps=gaps,
    action_hints=action_hints,
    suggested_questions=suggested_questions,
) if (divergences or drift_candidates or brief_decisions or gaps) else None
```

`brief` is `None` when no signal — preserves the "silent on no signal" contract from preflight.

**`handlers/action_hints.py`** — pure merge helper:

```python
def merge_drift_and_gap_hints(
    drift_hints: list[ActionHint],
    gap_hints: list[ActionHint],
) -> list[ActionHint]:
    """Combine drift and gap action hints into one list. Preserves order
    (drift first, gaps after). Deduplicates by hint identity (`kind`, `text`)."""
    seen = set()
    merged = []
    for h in (*drift_hints, *gap_hints):
        key = (h.kind, h.text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(h)
    return merged
```

### Unit Tests

- `tests/test_ingest_brief_unification.py::test_ingest_response_brief_populated_when_gaps_judged` — invoke `handle_ingest` with a payload that triggers gap-judge auto-fire (a feature_group present); assert `response.brief` is not None AND `response.brief.gaps` has at least one `BriefGap` entry. Confirms server-side population of the unified shape.

- `tests/test_ingest_brief_unification.py::test_ingest_response_brief_action_hints_merge_drift_and_gaps` — invoke `handle_ingest` with a payload that produces both drift candidates AND gap-judge findings; assert `response.brief.action_hints` contains hints from BOTH sources, with drift hints first and gaps after, no duplicates. Confirms the merge contract.

- `tests/test_ingest_brief_unification.py::test_ingest_response_brief_is_none_when_no_signal` — invoke `handle_ingest` with a payload that produces zero divergences, drift candidates, decisions, or gaps; assert `response.brief is None`. Confirms the silent-on-no-signal invariant.

- `tests/test_ingest_brief_unification.py::test_ingest_response_has_no_judgment_payload_field` — invoke `handle_ingest`, get the response, assert that `model_dump(exclude_none=True)` for the response does NOT contain `judgment_payload` or `judgment_payloads` keys. Confirms the field removal — fails if a future change re-introduces the legacy fields.

## Phase 2: Update `bicameral-ingest` skill to render unified brief

### Affected Files

- `skills/bicameral-ingest/SKILL.md` — replace the dual-step (Step 5 brief + Step 6 gap-judge) with a single-step unified render
- `skills/bicameral-judge-gaps/SKILL.md` — update trigger-line reference (per §Changes below)

### Changes

**`skills/bicameral-ingest/SKILL.md`** — current Steps 5 + 6 replaced with one Step 5:

```markdown
### Step 5. Render the unified brief

When `IngestResponse.brief` is non-null, render it as a single block.
Sections in order:

1. **Divergences** — `brief.divergences[]`. Surface each conflicting decision pair.
2. **Drift candidates** — `brief.drift_candidates[]`. Surface drifted regions.
3. **Decisions** — `brief.decisions[]`. Surface tracked decisions for context.
4. **Gaps** — `brief.gaps[]`. Apply the v0.4.16 rubric (5 categories) inline; show category + finding for each gap.
5. **Action hints** — `brief.action_hints[]`. Surface in guided-mode-aware tone.
6. **Suggested questions** — `brief.suggested_questions[]`. Bulleted list at the bottom.

When `brief` is null, do not render anything for the brief — silent-on-no-signal preserves the existing contract.

The `judgment_payload` field that previously carried gap-judge data has been
removed; gap findings are now in `brief.gaps[]`.
```

**`skills/bicameral-judge-gaps/SKILL.md`** — note that the rubric is invoked via the brief context, not as a standalone step. Remove the "fired automatically when an ingest response carries a `judgment_payload`" trigger line; replace with "fired automatically when an ingest response carries `brief.gaps`."

### Unit Tests

None. Phase 2 is text-only edits to two skill markdown files; skills are consumed by an LLM at runtime, not pytest-invocable. A static skill-vs-schema drift check (e.g. parsing the skill prose for field names and checking against `BriefEnvelope.model_json_schema()`) would be presence-only per `doctrine-test-functionality` (no unit invocation; the assertion is `<substring> in <derived-data>`-shaped). If skill-schema drift detection is wanted, structure it as a CI lint script outside pytest — separate concern, not in this plan.

## Phase 3: Stale `/bicameral:report-bug` → `/bicameral-report-bug` rename

### Affected Files

- `skills/bicameral-report-bug/SKILL.md` — 4 line edits

### Changes

Replace `/bicameral:report-bug` → `/bicameral-report-bug` in 4 locations on `skills/bicameral-report-bug/SKILL.md`:
- Line 3 (description trigger phrase)
- Line 6 (header `# /bicameral:report-bug`)
- Line 8 (trigger declaration)
- Line 142 (trailer `_Reported via .../bicameral:report-bug._`)

This is the single residual occurrence of the colon namespace form across `skills/`. The convention rename was completed in #178 except for this skill, which post-dated #178's sweep. After this Phase, no `/bicameral:` references remain in any tracked source file (per `grep -rn "/bicameral:" --include="*.md" --include="*.py" .` — 0 hits, excluding `CHANGELOG.md` archival entries and `.claude/worktrees/` clutter).

### Unit Tests

None — text rename in one skill file. The convention is enforced by the wider rename done in #178; this is mop-up. A presence-only "no `/bicameral:` in skills/" guard would violate `doctrine-test-functionality` (asserts string absence rather than unit behavior); skipped.

## CI Commands

- `pytest tests/test_ingest_brief_unification.py tests/test_v0416_gap_judge.py -v` — validates Phase 1's new tests AND the migrated assertions in the existing v0416 gap-judge tests
- `pytest tests/ -v --no-cov` — full regression sweep (catches any test that asserts on the removed `judgment_payload` field outside the explicitly-migrated set)
- `mypy .` — type-check (catches consumers reading `.judgment_payload` after removal)
- `ruff check . && ruff format --check .` — lint + format
- `grep -rn "judgment_payload" --include="*.py" .` — final sanity check. Expected hits after migration: `server.py:1119` only (the standalone judge_gaps tool's response shape — non-cascading collision, intentionally untouched per Phase 1 §Affected Files). Any other hits indicate a missed consumer
