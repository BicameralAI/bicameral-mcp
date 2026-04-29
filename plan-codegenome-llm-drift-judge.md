# Plan: CodeGenome LLM drift judge (Issue #44)

**Tracks**: BicameralAI/bicameral-mcp#44 — *[P2] LLM semantic drift judge:
suppress false-positive drift flags on cosmetic code changes*
**Targets**: v0.14.0
**Branch**: `feat/44-llm-drift-judge` (off `BicameralAI/dev` post-Phase-4 seal)
**Risk grade**: L1 — all changes are skill rubric + docs + test data; no
production code paths, no schema changes, no new tools, no new dependencies.
**Change class**: minor (additive skill rubric extension, documentation,
test corpus expansion)

---

## Open Questions

1. **Reasoning trace field on `ComplianceVerdict`?** — The judge could emit a
   short structured rationale separate from `explanation` (e.g.
   `{cosmetic_signals_seen: ["whitespace", "comment"], semantic_signals_seen: []}`).
   **Recommendation**: do NOT add a new field. Reuse `explanation` (already
   one-sentence) and `evidence_refs` (already list-of-strings) — adding
   fields couples the skill output to a server-side schema change for
   minimal value. Open question only because the user listed it explicitly;
   recommendation stands until reviewer says otherwise.

2. **Should the rubric distinguish "cosmetic but the decision is irrelevant"
   from "cosmetic and decision is met"?** — A cosmetic change to code that
   was always wrong-region for the decision should emit
   `verdict=not_relevant`, not `verdict=compliant + semantically_preserved`.
   **Recommendation**: state the disambiguation rule explicitly in the
   rubric: `not_relevant` is decided on axis 1 (compliance) regardless of
   axis 2 (cosmetic-vs-semantic). If `not_relevant`, leave `semantic_status`
   unset (None). Plan reflects this.

3. **Telemetry coupling** — Issue #44 acceptance criterion 3 is "`bicameral.usage_summary`
   shows `cosmetic_drift_pct` decreasing after this lands." That metric
   depends on the local-counters subsystem in PR #95 (#39 + #42), which
   is in flight but not merged. **Recommendation**: defer the telemetry
   wiring to a follow-up issue (`#44-followup-telemetry`) gated on PR #95
   landing first. Don't couple this plan's merge to PR #95's review tier.
   Plan does NOT include telemetry work.

---

## Background (grounding — verified against post-Phase-4 dev HEAD)

Phase 4 (#61, sealed at META_LEDGER #14, chain `0ebcf69b`) added:

- Deterministic 4-signal classifier in `codegenome/drift_classifier.py`
  (signature 0.30, neighbors 0.25, diff_lines 0.30, no_new_calls 0.15;
  thresholds ≥0.80 cosmetic, ≤0.30 semantic, [0.30, 0.80) uncertain).
- `_run_drift_classification_pass` in `handlers/link_commit.py` (line 236)
  fires after the continuity pass when `BICAMERAL_CODEGENOME_ENHANCE_DRIFT=1`.
  Cosmetic regions → `compliance_check` written immediately
  (`auto_resolved_count` reported). Uncertain regions → kept in pending
  with `pre_classification: PreClassificationHint` attached. Semantic
  regions → kept in pending with no hint.
- `PreClassificationHint` (contracts.py:157) carries
  `verdict ∈ {cosmetic, semantic, uncertain}`, `confidence: float`,
  `signals: dict[str, float]`, `evidence_refs: list[str]`.
- `ComplianceVerdict` (contracts.py:172) accepts optional
  `semantic_status: Literal["semantically_preserved", "semantic_change"] | None`
  and `evidence_refs: list[str]`. Server persists both to
  `compliance_check.semantic_status` / `.evidence_refs`.
- `ResolveComplianceAccepted` (contracts.py:214) echoes `semantic_status`
  back to the caller for round-trip confirmation.
- `compliance_check` table is `CHANGEFEED 30d INCLUDE ORIGINAL` (Phase 1
  of #61) so the audit trail is queryable via `SHOW CHANGES FOR TABLE`.
- M3 benchmark (`tests/fixtures/m3_benchmark/cases.py`, 391 lines, 30
  cases × 7 languages) classifies each case with a deterministic
  `expected: "cosmetic" | "semantic" | "uncertain"`. 10 cases are
  expected-uncertain (4 Python + 1 each for the other six languages).

**What's left for #44** (the original issue framing pre-dates Phase 4):

1. The deterministic classifier stops at "uncertain" and emits a hint;
   the **caller LLM** is supposed to convert that hint into a definitive
   `semantic_status` claim alongside its existing compliance verdict.
2. The current `bicameral-sync` skill (Step 2, lines 89–125 of
   `skills/bicameral-sync/SKILL.md`) treats `semantic_status` as
   *optional* and does not have a sub-protocol for the uncertain band.
   The skill rubric is the contract — without a protocol that
   exploits the hint, the LLM ignores it.
3. The `cosmetic_drift_pct` telemetry surface is unbuilt (Open Question 3).

This plan addresses (1) and (2). (3) is deferred.

---

## Architecture decisions

### D1. Pipeline location: skill-side (caller LLM), NOT server-side

`docs/CONCEPT.md` anti-goal: *"Not an LLM-powered ledger. The
deterministic core does not invoke any model. Compliance verdicts come
from caller LLMs and are cached."*

The skill-side path leverages the existing flow without coupling the
server to LLM availability:

```
link_commit ──► pre_classification: uncertain ──► PendingComplianceCheck
                                                          │
                                                          ▼
                                          bicameral-sync skill (LLM)
                                                          │
                                                  two-axis judgment
                                                          │
                                                          ▼
                                          resolve_compliance(verdicts=[
                                            {verdict, semantic_status, evidence_refs}
                                          ])
                                                          │
                                                          ▼
                                          compliance_check row (cached)
```

No new server code. The judge is purely a skill-rubric specification
that the caller LLM follows when reading `pre_classification`.

### D2. Caching: already free

`resolve_compliance` writes to `compliance_check` keyed by
`(decision_id, region_id, content_hash)`. Once written, the same hash
won't be re-asked — the next `link_commit` sees a cached verdict and
strips the region from pending. Phase 4 added `semantic_status` and
`evidence_refs` columns to that same row, so the LLM judge's
two-axis output is cached identically. **No additional cache layer
needed.**

### D3. Input contract for the judge

When the skill encounters a `PendingComplianceCheck` with
`pre_classification.verdict == "uncertain"`, the inputs to the
judge are:

| Source | Field | Purpose |
|---|---|---|
| `PendingComplianceCheck` | `decision_description` | What the decision claims |
| `PendingComplianceCheck` | `code_body` (capped 200 lines) | The current code |
| `PendingComplianceCheck` | `file_path`, `start_line`, `end_line` | Pointer for full re-read if `code_body` is truncated |
| `PreClassificationHint` | `confidence` ∈ [0.30, 0.80) | How close to the cosmetic line the classifier got |
| `PreClassificationHint` | `signals` (dict) | Per-signal contribution: `signature`, `neighbors`, `diff_lines`, `no_new_calls` |
| `PreClassificationHint` | `evidence_refs` | Audit-trail strings the classifier produced |

No new contract. The skill consumes existing typed fields.

### D4. Output contract for the judge

The skill emits one `ComplianceVerdict` per pending check via the
existing `resolve_compliance` API. **The two-axis judgment maps to
existing fields**:

| Field | Axis | Semantics |
|---|---|---|
| `verdict` | Compliance | `compliant` / `drifted` / `not_relevant` (existing) |
| `semantic_status` | Cosmetic-vs-semantic | `semantically_preserved` / `semantic_change` / `None` (Phase 4 additive) |
| `confidence` | Both | `high` / `medium` / `low` (existing) |
| `explanation` | Both | One-sentence rationale (existing) |
| `evidence_refs` | Both | Free-form audit strings, e.g. echo the hint's signals (Phase 4 additive) |

**No new field.** The judge's "reasoning trace" lives in `explanation` +
`evidence_refs`. The two-axis output uses the two existing fields.

### D5. Decision rule (the rubric — declarative, in SKILL.md)

When `pre_classification.verdict == "uncertain"`:

```
Step 1. Decide axis 1 (compliance) FIRST:
   - Is this region semantically about the decision at all?
     → No: verdict = "not_relevant"; semantic_status = None (server prunes binds_to).
     → Yes: continue to step 2.

Step 2. Decide axis 2 (cosmetic-vs-semantic):
   - Use pre_classification.signals as advisory evidence:
     - signature signal high (>0.8): function shape unchanged → leans cosmetic
     - neighbors signal high (>0.8): surrounding context unchanged → leans cosmetic
     - diff_lines signal high (>0.8): only comment/docstring/whitespace lines
       changed → leans cosmetic
     - no_new_calls signal == 1.0: no new callees introduced → leans cosmetic
   - Read the actual diff. Don't trust signals blindly — they're advisory.
   - If the change is structurally cosmetic AND the decision's intent is
     unaffected → semantic_status = "semantically_preserved", verdict = "compliant".
   - If the change is semantic (logic, threshold, branch, return shape changed)
     → semantic_status = "semantic_change", verdict = derived from axis 1
       (compliant if the new logic still meets the decision; drifted otherwise).

Step 3. Echo the hint's evidence_refs back in the verdict so the audit
   trail captures the deterministic→LLM hand-off.
```

This rubric is data — text in SKILL.md — not code. The LLM follows it
when reasoning; no Python implementation.

### D6. Exit criteria (acceptance gate)

Verifiable on the M3 benchmark corpus:

1. **No regression on cosmetic set.** All 10 expected-cosmetic cases
   continue to auto-resolve at the deterministic layer (≥0.80 score).
   Verified by `tests/test_m3_benchmark.py` (already exists).
2. **No regression on semantic set.** All 10 expected-semantic cases
   continue to score ≤0.30 at the deterministic layer. Verified by
   `tests/test_m3_benchmark.py` (already exists).
3. **Uncertain-band corpus expanded** with judge-expected outputs.
   Each of the 10 uncertain cases gains an `expected_judge` field
   declaring the human-correct
   `(verdict, semantic_status)` pair. Verified by structural test
   that asserts every uncertain case has the field — does NOT call
   an LLM.
4. **Skill rubric conformance.** A test parses
   `skills/bicameral-sync/SKILL.md`, asserts the new uncertain-band
   sub-protocol section exists, and asserts the section names both
   axes by their literal field names (`semantic_status`,
   `semantically_preserved`, `semantic_change`).
5. **Operator QC pass** (out-of-band, not CI). Operator runs the
   skill against the uncertain-band fixtures, compares LLM verdicts
   to the `expected_judge` values, records false-positive and
   false-negative counts. Pass threshold: 0% FP on cosmetic-claimed
   verdicts (the LLM never said "semantically_preserved" when the
   ground truth was "semantic_change"). FN tolerance: ≤ 20%.

Criteria 1–4 are CI-checkable. Criterion 5 is the qualitative gate
called out by Issue #44 ("M3 Drift Precision target < 10% false alarm
rate"). Operator pass is recorded in the META_LEDGER substantiation
entry for this plan.

---

## Phase 1: Test corpus extension (uncertain-band judge expectations)

TDD-light: tests written FIRST, confirm red (existing M3 corpus has
no `expected_judge` field), then add data, confirm green.

### Affected files

- `tests/test_m3_benchmark_judge_corpus.py` — **new**, ~80 LOC, 4 tests.
  Validates corpus shape only — does NOT call an LLM.
- `tests/fixtures/m3_benchmark/cases.py` — **modify**, add
  `expected_judge: {"verdict": str, "semantic_status": str | None}` field
  to each of the 10 uncertain cases. Existing cosmetic + semantic cases
  unchanged.

### Changes

1. New test file `tests/test_m3_benchmark_judge_corpus.py`:
   - `test_every_uncertain_case_has_expected_judge` — iterate `CASES`,
     assert every case with `expected == "uncertain"` has
     `expected_judge` key, and that key is a dict.
   - `test_expected_judge_verdict_is_valid_enum` — every
     `expected_judge["verdict"]` ∈ `{"compliant", "drifted", "not_relevant"}`.
   - `test_expected_judge_semantic_status_is_valid_enum` — every
     `expected_judge["semantic_status"]` ∈
     `{"semantically_preserved", "semantic_change", None}`.
   - `test_not_relevant_verdict_implies_semantic_status_none` — when
     `expected_judge["verdict"] == "not_relevant"`, the
     `semantic_status` is `None` (axis-2 doesn't apply for
     misretrieved regions, per D5 step 1).
2. `tests/fixtures/m3_benchmark/cases.py` — for each of the 10
   uncertain cases, hand-author the expected judge output. Example:
   ```python
   {
       "id": "py_09_uncertain_logic_inside_unchanged_signature",
       "language": "python", "expected": "uncertain",
       "expected_judge": {
           "verdict": "drifted",
           "semantic_status": "semantic_change",
       },
       "old": "...", "new": "...",
   },
   ```
   Authoring is human judgment — these are the ground-truth labels
   the operator QC pass measures against.

### Unit tests (Phase 1)

- `tests/test_m3_benchmark_judge_corpus.py` — 4 tests, all run in
  `pytest -q --no-header tests/test_m3_benchmark_judge_corpus.py`.
  Pure data validation; no SurrealDB, no LLM, no network. Runs in
  <100 ms.

---

## Phase 2: Skill rubric — uncertain-band sub-protocol

TDD-light: rubric-conformance test written FIRST, confirm red (no
sub-protocol section exists yet), then update the SKILL.md, confirm
green.

### Affected files

- `tests/test_skill_uncertain_protocol.py` — **new**, ~70 LOC, 5
  tests. Parses SKILL.md as text; asserts structural invariants.
- `skills/bicameral-sync/SKILL.md` — **modify**, add new subsection
  under existing Step 2 (the "Resolve every pending compliance check"
  section, currently at lines 41–125). Estimated +50 lines.
- `pilot/mcp/skills/bicameral-sync/SKILL.md` — **modify**, mirror the
  same change. Per `CLAUDE.md` rule: `pilot/mcp/skills/` is canonical;
  `skills/bicameral-sync/SKILL.md` is the version reviewers actually
  read in this repo. Both must change in the same commit.
- `docs/training/cosmetic-vs-semantic.md` — **new**, ~150 LOC. Concept
  training doc per `DEV_CYCLE.md` §8 (the matrix says training is
  required when a feature introduces a concept). Walks one Python
  cosmetic case + one Python uncertain case from M3 end-to-end.
- `docs/training/README.md` — **modify**, add row to the index table.

### Changes

1. New test file `tests/test_skill_uncertain_protocol.py`:
   - `test_skill_md_has_uncertain_band_subsection` — read
     `skills/bicameral-sync/SKILL.md`, assert it contains a heading
     matching `r"Uncertain-band sub-protocol"` (case-insensitive).
   - `test_uncertain_subsection_names_both_axes` — assert the
     subsection text contains all three terms: `semantic_status`,
     `semantically_preserved`, `semantic_change`.
   - `test_uncertain_subsection_describes_signal_use` — assert the
     subsection mentions all four signals by name: `signature`,
     `neighbors`, `diff_lines`, `no_new_calls`.
   - `test_uncertain_subsection_states_axis_1_first_rule` — assert
     the subsection contains text equivalent to "axis 1 first" (the
     `not_relevant` short-circuit per D5 step 1).
   - `test_pilot_skill_md_matches_skills_skill_md` — both copies of
     SKILL.md have identical body content (canonical-source rule
     from `CLAUDE.md`). Diff is whitespace-tolerant only.

2. `skills/bicameral-sync/SKILL.md` and `pilot/mcp/skills/bicameral-sync/SKILL.md`:
   Insert a new `### 2.bis Uncertain-band sub-protocol (Phase 4 / #44)`
   subsection between current Step 2 and Step 3 ("Report"). The
   subsection contents reproduce the D5 rubric verbatim (declarative,
   not code).

3. New training doc `docs/training/cosmetic-vs-semantic.md`. Sections
   per the `docs/training/README.md` template:
   - **Why this exists**: the deterministic vs LLM hand-off
   - **Prerequisites**: read DEV_CYCLE.md §2.1.2; read
     skills/bicameral-sync/SKILL.md
   - **The concept**: two-axis judgment, with worked Python example
     from M3 (one cosmetic, one uncertain → `semantic_change/drifted`)
   - **Worked example**: full skill flow for a `py_09_*` uncertain
     case
   - **Common pitfalls**: trusting the hint blindly; conflating
     `not_relevant` with `semantically_preserved`; forgetting to
     echo `evidence_refs`
   - **See also**: PR #91 (Phase 4 sealing), Issue #44, M3 benchmark
     fixtures

4. `docs/training/README.md`: add a row to the index:
   `| Cosmetic vs semantic drift | Active |`

### Unit tests (Phase 2)

- `tests/test_skill_uncertain_protocol.py` — 5 tests, all
  pure-text parsing. No SurrealDB, no LLM, no network. Runs
  in <100 ms.

---

## Test invocation (matches CI workflow)

The CI workflow `test-mcp-regression.yml` runs the full suite. The
two new test files are picked up automatically. To run only the
new tests during development:

```bash
SURREAL_URL=memory:// python -m pytest -q \
    tests/test_m3_benchmark_judge_corpus.py \
    tests/test_skill_uncertain_protocol.py
```

Lint:

```bash
ruff check tests/test_m3_benchmark_judge_corpus.py tests/test_skill_uncertain_protocol.py
black --check tests/test_m3_benchmark_judge_corpus.py tests/test_skill_uncertain_protocol.py
```

No mypy run — both new test files are pure Python with no typed
contracts.

---

## Section 4 razor pre-check

Estimated post-implementation file sizes:

| File | Estimate | Razor cap | OK? |
|---|---|---|---|
| `tests/test_m3_benchmark_judge_corpus.py` | ~80 LOC | 250 | yes |
| `tests/test_skill_uncertain_protocol.py` | ~70 LOC | 250 | yes |
| `tests/fixtures/m3_benchmark/cases.py` | 391 → ~430 LOC | 250 | **violates** |
| `skills/bicameral-sync/SKILL.md` | 138 → ~190 LOC | n/a (markdown) | n/a |
| `pilot/mcp/skills/bicameral-sync/SKILL.md` | mirror | n/a | n/a |
| `docs/training/cosmetic-vs-semantic.md` | ~150 LOC | n/a (markdown) | n/a |

**`cases.py` razor violation**: it's already at 391 LOC pre-Phase-1
(legacy from Phase 5 of #61). This plan adds ~40 LOC to it. The
`pyproject.toml` ruff config exempts `tests/fixtures/**`. Test
fixture data files are explicitly out of scope for the razor per the
shipped Phase 4 substantiation note ("Plan deviation: §Phase 5
collapsed 30 paired files to a single `cases.py` data module — same
coverage, far less file-system noise"). No remediation required.

Function-level razor: every new function is a test (`def test_*`),
all under 30 LOC. No production functions added.

---

## What this plan is NOT

- **Not a Phase 5 of #61.** This is a separate v0.14.0 issue (#44)
  consuming Phase 4's contracts. Branched off `dev` post-Phase-4
  seal.
- **Not server-side.** Per D1.
- **Not a new tool, contract, or schema migration.** Pure skill +
  data + docs.
- **Not telemetry.** Per Open Question 3, telemetry is deferred to
  a follow-up.
- **Not a replacement for the deterministic classifier.** The
  classifier still runs first; the LLM judge only acts on the
  uncertain band [0.30, 0.80) and the existing semantic-band
  pending checks (which is unchanged behavior).
