# Shadow Genome

Recorded failure modes for the QorLogic chain. Each entry captures a
verdict-rejecting pattern so future planning avoids it.

---

## Failure Entry #1

**Date**: 2026-04-28T01:06:38Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `c31802d7...`
**Failure Mode**: HALLUCINATION (V1/V2 — residual `{{verify}}` tags)

### What Failed
`plan-codegenome-phase-1-2.md` shipped to `/qor-audit` with two
unresolved `{{verify: ...}}` tags (lines 19, 143).

### Why It Failed
The qor-plan grounding doctrine (Step 2b) declares: "Residual
`{{verify: ...}}` tags in a plan block its submission." Both tags had
contextually legitimate purposes:
- Line 19: documenting a deferred decision (release-eng version pin)
- Line 143: pairing a verifiable assertion with the test that verifies it

But the doctrine is binary — *any* residual `{{verify}}` blocks. The
governor used the tags as informal annotations rather than resolving or
removing them before submission.

### Pattern to Avoid
`{{verify: ...}}` is a *working-file* annotation, not a *submission-grade*
artifact. Before submission to audit:
- If the claim is *deferred* to another decision-maker (e.g. release-eng),
  rewrite as plain prose stating the deferral and its owner.
- If the claim is *self-resolving* via a planned test or check, delete
  the tag — the test or check is the verification.
- If the claim is genuinely uncertain and cannot be deferred or
  self-resolved, the plan is not yet ready for audit; resolve before
  submission.

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md remediation #1 (pin `0.11.0`
placeholder + plain prose deferral) and #2 (delete in-plan tag — let test
stand). Re-submission for `/qor-audit` follows.

---

## Failure Entry #2

**Date**: 2026-04-28T01:06:38Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `c31802d7...`
**Failure Mode**: ORPHAN / SCOPE_CREEP (V3 — `SubjectIdentityModel`)

### What Failed
Phase 1 of `plan-codegenome-phase-1-2.md` proposed four Pydantic models
in `codegenome/contracts.py`. The upstream issue #59 mandates only three:
`SubjectCandidateModel`, `EvidenceRecordModel`, `EvidencePacketModel`.
The fourth, `SubjectIdentityModel`, is not in the issue, has no caller in
the plan, and is not covered by any test.

### Why It Failed
The user's anti-goal Q2=B authorized exactly one Phase-3 foundation
artifact: the `subject_version` table (so the schema migration fires
once, not twice). `SubjectIdentityModel` does not fall under that
exception — it is an unrelated stub for a future MCP-boundary surface
that #59 does not deliver.

### Pattern to Avoid
**Symmetry is not a justification.** "All four dataclasses get Pydantic
mirrors" is an aesthetic argument, not a YAGNI-compliant one. When an
issue lists three deliverables, deliver three. Future phases that need
the fourth mirror can add it under their own justification, with their
own caller, in their own PR. Audit checks issue-mandate ∩ caller-
existence; symmetry-driven extras fail both.

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md remediation #3: remove
`SubjectIdentityModel` from the Phase 1 deliverables list in
`plan-codegenome-phase-1-2.md` so the implementation phase does not
write the unjustified mirror. Re-submission for `/qor-audit` follows.

---

## Failure Entry #3 (Phase 3 plan, #60)

**Date**: 2026-04-28T03:18:53Z
**Verdict ID**: AUDIT_REPORT.md @ chain hash `7fad1059...`
**Failure Mode**: ORPHAN / MACRO-ARCHITECTURE (V1, V2, V3 — coupled
build-path incompleteness)

### What Failed
`plan-codegenome-phase-3.md`'s auto-resolve recipe inside
`evaluate_continuity_for_drift` (line 203):

> "On ≥0.75: writes `subject_version`, `identity_supersedes`, calls
> `update_binds_to_region`, returns `ContinuityResolution`..."

The recipe enumerates three terminal writes but omits four prerequisite
writes that are required to make the terminal writes valid:

- The new `subject_identity` row that `identity_supersedes(old, new)`
  references.
- The new `code_region` row that `update_binds_to_region(...,
  new_region_id)` references.
- The `has_version` edge that connects `code_subject` to the newly
  written `subject_version` row (otherwise the row is unreachable).
- The `compute_identity_with_neighbors` call that produces the new
  identity values used by both the new `subject_identity` row and the
  new `subject_version` row.

### Why It Failed
The plan was written from the issue body's bullet list ("write
subject_version / write identity_supersedes / update binds_to") and
treated those bullets as the *complete* sequence rather than as the
*terminal* sequence. Each terminal write has a graph-theoretic
prerequisite (the target row must exist before a RELATE can reference
it) that was implicit in the issue but not enumerated in the plan.

### Pattern to Avoid
When a plan describes ledger writes that involve RELATE statements,
enumerate every prerequisite upsert by name. Treat "writes X" as a
single bullet only if X is a node, never if X is an edge — edges
require both endpoints to exist. A plan that says "write
identity_supersedes" must also say where the OUT endpoint comes from.
The audit pass that catches this is *macro-architecture: build path is
intentional* — same checkbox, different scale (data flow rather than
module flow).

### Remediation Attempted
Plan to be edited per AUDIT_REPORT.md required remediations `#1`, `#2`,
and `#3`: extend `evaluate_continuity_for_drift` description with the
7-step sequence (compute_identity → upsert_code_region →
upsert_subject_identity → write_subject_version → relate_has_version
→ write_identity_supersedes → update_binds_to_region); add the
missing `relate_has_version` query + adapter wrapper to the plan;
update integration-test fixture-setup descriptions to verify the
prerequisite rows. Re-submission for `/qor-audit` follows.

---

## Failure Entry #3

**Date:** 2026-04-28
**Phase:** AUDIT (Phase 4 / Issue #61)
**Persona:** Judge

### What Failed

`plan-codegenome-phase-4.md` received VETO with five blocking findings.
The Governor's plan invoked **non-existent infrastructure** (CHANGEFEED
on `compliance_check`, `extract_calls` API on `symbol_extractor`),
introduced a **dead enum value** (`pre_classification_hint` in the
`semantic_status` ASSERT with no writer), used a **wrong language
identifier** (`csharp` vs `c_sharp`), and the M3 benchmark corpus
**did not honour the multi-language scope** chosen at planning time
(Q2=B): no uncertain-band fixtures for non-Python; Java + C# got zero
fixtures of any kind.

### Why It Failed

**Root cause:** the plan was written from architectural intuition
without grounding the API references and schema claims against the
actual code. Every one of F1–F4 collapsed under direct file read:

- F1 was contradicted by `ledger/schema.py:186` (no CHANGEFEED on
  `compliance_check`).
- F3 was contradicted by `code_locator/indexing/symbol_extractor.py:64`
  (`c_sharp`, not `csharp`).
- F4 was contradicted by the public-function listing of
  `symbol_extractor.py` (only `extract_symbols*` — no `extract_calls`).

The plan trusted memory of how the code "ought to" work rather than
re-reading. When the plan was forwarded to `/qor-audit` without that
ground-check pass, the audit caught the gap — but the cost was a full
plan-revision cycle.

F2 (dead enum) and F5 (test corpus scope mismatch) are different in
kind: they're **internal inconsistencies** within the plan itself.
F2 lists a value the plan never writes; F5 promises multi-language
coverage in the deliverables but only delivers Python coverage in the
fixture inventory. These are catchable by re-reading the plan against
itself before submission.

### Pattern to Avoid

**SG-PLAN-GROUNDING-DRIFT.** When writing a plan that references an
existing API (function, schema field, language identifier, table
property), the Governor must:

1. Open the referenced file.
2. Verify the symbol exists and matches the spelling used in the plan.
3. If the plan asserts a property of the schema/code (e.g. "table X has
   CHANGEFEED Y"), grep for the property and confirm.

Plans that skip this step ship invented infrastructure that the audit
must catch. Each invention is a V1 (orphan) or V2 (broken contract)
violation. The grounding cost (~5 minutes of greps) is far less than
a re-plan cycle (~hours of rewrite + re-audit).

**SG-PLAN-INTERNAL-INCONSISTENCY.** When a plan picks a scope
(multi-language, additive-only schema, etc.) it must be honoured in
EVERY section that references that scope:

- Affected-files lists.
- Test plan.
- Fixture inventory.
- Razor pre-check.
- Risk table.

A scope that lives only in the §Open-Questions or §Composition-Principles
sections but degrades silently in §Test-Plan or §Phase-N is the same
class of failure as F5. Internal consistency is a precondition for
submission to `/qor-audit`.

### Remediation Attempted

VETO issued. Governor must revise the plan addressing F1–F5 and
resubmit for `/qor-audit`. Recommended remediation paths are listed
in each finding's "Required remediation" section of the audit report.

The five non-blocking observations (O1–O5) should also be addressed
in the revision pass for plan hygiene, but do not on their own block
re-audit PASS.

### Auto-counter on resubmission

When the revised plan is submitted, the Judge will specifically
ground-check every API reference and schema claim against the
codebase before issuing PASS. The grounding sweep is non-optional
for L2 plans that touch schema or extend an existing module API.

---

## Failure Entry #5

**Date**: 2026-04-29
**Phase**: GATE / qor-audit (v1 of #44 plan, commit `b15c9ef`)
**Pattern**: SG-PLAN-GROUNDING-DRIFT (instance #2 in this session)

### What happened

Plan `plan-codegenome-llm-drift-judge.md` (v1) instructed the implementer to modify `pilot/mcp/skills/bicameral-sync/SKILL.md` and added a unit test (`test_pilot_skill_md_matches_skills_skill_md`) that diffed two copies of SKILL.md across `skills/` and `pilot/mcp/skills/`. The plan author (this session) inherited the claim from `CLAUDE.md` ("`pilot/mcp/skills/` is the **single canonical location**") without empirically verifying it.

Reality on `dev` HEAD (`200dbd5`):

```
$ ls pilot/
ls: cannot access 'pilot/': No such file or directory
```

The directory does not exist. The plan was unimplementable as written.

### Detection

Audit Step 3 — orphan detection pass — flagged `pilot/mcp/skills/bicameral-sync/SKILL.md` as a build-path orphan. Backwalking to the plan revealed it was a directive, not a typo; a literal `ls` confirmed the directory's absence.

### Mitigation

1. v2 of the plan (commit `d846a4a`) removed the directive, removed the matching test, and added a rationale note identifying CLAUDE.md's reference as stale.
2. Plan author should `ls` every directory it proposes to modify before issuing the plan, not trust `CLAUDE.md` verbatim for filesystem layout.
3. Auditor's orphan detection should run on every plan, not just code-bearing ones.

### Cross-references

- **Instance #1**: `DEV_CYCLE.md` §9 (PR #93) absorbed the same `pilot/mcp/skills/` reference into a "skill file rule (project-specific, mandatory)" callout. Same root cause; landed undetected because PR #93 was a docs PR with no orphan check.
- **Followup workstream**: `docs:claude-md-cleanup` issue (to be filed) — fixes `CLAUDE.md` itself so future plans don't keep inheriting the stale assertion.

### Pattern signature

```
SG-PLAN-GROUNDING-DRIFT
  Trigger:        plan author trusts a documented assertion about
                  filesystem state without empirical verification.
  Failure mode:   plan instructs work on files that don't exist;
                  unit test references nonexistent path; orphan
                  detection catches it at audit (best case) or
                  implementation runtime (worst case).
  Countermeasure: every directory cited in a plan's "affected
                  files" section must be `ls`-confirmed before
                  the plan is submitted for audit. Add a Step 2b
                  Grounding Protocol clause if not already present.
```

---

## Failure Entry #6

**Date**: 2026-05-02T22:00:00Z
**Verdict ID**: research-brief-priority-c-selective-ingest-2026-05-02.md (deleted) — operator-rejected during dialogue
**Failure Mode**: INVARIANT_FROM_IMPLEMENTATION (Hallucination-class; SG-1 family)

### What Failed

`/qor-research` for v0 Priority C (selective source ingest) read the current
`bicameral.ingest` code surface (`handlers/ingest.py:217`), observed that
the server accepts pre-extracted text and has no source-fetcher / OAuth /
API-client code, and elevated this **v0 implementation state** to a
**product principle**:

> "Architecture invariant: bicameral-mcp does not fetch source content;
> the agent fetches via host's tools. Any future 'source connector'
> proposal should be VETO'd at audit unless it explicitly bypasses this
> invariant for a documented reason."

The brief recommended an entire framing reversal of the user's stated
priority — from "build selective ingest for sources" to "build a
curation/quality-gate UX over what the agent already fetches" — based on
this invented invariant. The brief was about to be filed as advisory
input to the follow-on `/qor-plan` and the invariant about to be saved
as a project memory entry.

### Why It Failed

The Sales Enablement & Positioning Playbook (operator-supplied during
post-research dialogue) explicitly positions Bicameral as the
**destination** of a `Decision Sources → Bicameral.LEDGER` arrow in
its ecosystem-fit diagram. Decision continuity at multi-developer,
multi-agent scale is **Value Pillar #1**. The agent-fetches-only model
fragments the ledger across sessions: Dev A's Cursor session, Dev B's
Claude Code session, and Dev C's Claude Desktop session each produce
independent reads of the same Slack thread, with independent extractions.
Two devs preflighting the same code path against the same conversational
source can get different drift verdicts.

The product principle is **decision continuity at scale**. The v0 code's
agent-fetches-only pattern is a solo-developer simplification, not a
load-bearing invariant. Treating the simplification as a principle
would have shipped a plan whose executive summary directly contradicts
the product positioning the team is selling against.

### Pattern to Avoid

**Distinguish "what the code does today" from "what the product principle
is."** A v0 simplification is evidence of a design choice at a moment in
time — not evidence of the load-bearing rule. Authoritative product
principles live in:

- `docs/CONCEPT.md` (project DNA)
- `docs/ARCHITECTURE_PLAN.md` (interface contracts + risk grade)
- Sales Enablement & Positioning Playbook (operator-curated, off-repo)
- Founder/maintainer dialogue when the artifacts are silent or
  contradictory

Code-state observations may *suggest* an invariant, but the invariant must
be checked against authoritative sources before being ascribed product
weight. When code and product positioning diverge, the code is the
v0-state, not the contract.

### Detection Heuristic

Before writing the phrase "architecture invariant" or "product principle"
or "by design" in a research brief, ask:

1. Is this claim grounded in a non-code authoritative source? (CONCEPT.md,
   ARCHITECTURE_PLAN.md, positioning doc, founder dialogue.)
2. If only grounded in code, am I sure the code reflects the product
   principle and not just a v0 simplification?
3. Could this claim, if elevated to a project memory, contradict the
   product's market positioning if the team scales?

A "no" or "unsure" on any of these means the claim is unproven. **Anything
unproven is only theater.** Quote it as observation, not as principle.

### Remediation

- Research brief deleted (no archival; the failure mode is more useful
  preserved here than the false brief is in the docs tree).
- Project memory entry "bicameral does not fetch source content" was
  about to be saved; intercepted before write.
- Operator-supplied playbook treated as primary substrate for the
  re-research that follows.
- Doctrine "anything unproven is only theater" saved as project memory
  feedback for future research/audit phases.


### Addendum to Entry #6 (2026-05-02T22:30:00Z)

The pattern catalogued above is **symmetric**: it applies as much to project doctrine documents as to source code. After the v1 brief failure, dialogue with the operator revealed CONCEPT.md anti-goals were also being read too generously — specifically *"No remote DB, no managed backend"* was treated as "no server-side components at organizational scale," which conflicts with multi-org sync requirements implied by the playbook.

The operator parsed the anti-goal literally: the load-bearing keyword is **"managed"**, not "backend." A managed backend is one that requires human ops (DBA tasks, on-call, capacity planning, manual migration) — i.e., a SaaS the customer pays an ops tax for. A **self-managing** backend (self-hosted, schema-migrating itself, deterministic, no on-call surface) is fully compatible. Sentry self-hosted, Supabase self-host, embedded-SurrealDB-already-in-repo are the precedents.

### Pattern to Avoid (extension)

When parsing project doctrine documents (CONCEPT.md anti-goals, ARCHITECTURE_PLAN.md interface contracts, positioning playbooks), identify the **load-bearing keyword** in each clause and read the rest as gloss on that keyword. Do NOT generalize the clause beyond what the keyword warrants:

- *"No managed backend"* — load-bearing word: **managed**. Allows server-side that's self-managing.
- *"No cloud, no network calls in the deterministic core"* — load-bearing words: **deterministic core**. Allows network calls outside the deterministic core (e.g., source ingest workers, telemetry).
- *"Not an LLM-powered ledger"* — load-bearing words: **ledger**. Allows LLMs as callers, classifiers, and orchestrators around the ledger.

When the operator's product positioning implies a feature that seems to violate an anti-goal, do not assume the anti-goal blocks the feature — first parse the keyword and see whether the feature actually trips it.

### Detection Heuristic (extension)

Before declaring "this anti-goal forbids X," ask:
1. What is the load-bearing keyword in the anti-goal clause?
2. Does X trip that specific keyword, or just the broader gloss around it?
3. Is there an industry precedent (self-hosted Sentry, Supabase OSS, etc.) where a system honors this anti-goal-keyword while still implementing X?

If 2 says "just the gloss" or 3 surfaces a precedent, X is not blocked — it's compatible with the anti-goal under literal-keyword parsing.

---

## Failure Entry #7

**Date**: 2026-05-02T06:55:00Z
**Session**: `2026-05-02T0625-8ea4cc`
**Skill that produced the artifact**: `/qor-plan` (`plan-priority-c-team-server-notion-v1.md`)
**Skill that detected**: `/qor-audit`
**Verdict**: VETO (`infrastructure-mismatch`)

### Pattern Observed: PARALLEL_STRUCTURE_ASSUMED

The plan extended a v0 codebase by repeatedly assuming the v0 had implemented patterns *symmetric* with the v1 ambition. In four places:

1. The plan referenced a `schema-version row` that was never added to v0's schema (`SCHEMA_VERSION` is an in-code constant only).
2. The plan changed `_MIGRATIONS`'s type signature from tuple-of-stmts to dict-of-callables without acknowledging the corresponding `ensure_schema` dispatch loop change — assuming the dispatch was already callable-shaped.
3. The plan said "extend the existing `lifespan` to spawn a Notion-worker task" — assuming a Slack-worker task was already registered. It was not. The Slack worker shipped in v0 Phase 3 has no production caller and is invoked only by tests.
4. The plan referenced `_resolve_extractor()` and `DEFAULT_CONFIG_PATH` in a code sketch — assuming Slack precedents existed. They did not.

The common signature: "the plan generalizes from a Slack-shaped pattern that the plan author *imagined* the v0 had built, rather than the pattern the v0 actually built." This is a class of plan-text drift specifically tied to writing v1 plans against v0 codebases without grep-verifying every named symbol.

### Root Cause

The Governor was treating the v0 plan document (`plan-priority-c-team-server-slack-v0.md`) as the ground truth for v0 state, rather than the v0 *code*. The v0 plan promised a worker-task lifecycle pattern in §Phase 3; the v0 code shipped the worker function but never wired it. The Governor read the plan, not the code. The audit caught it because Step 2 verified state against the code itself.

### Pattern to Avoid

When writing a v1 plan that extends a landed v0:

1. Do NOT cite a v0 symbol in a v1 plan without `grep`-verifying it exists in the current code tree. The audit's Infrastructure Alignment Pass enforces this; the plan should pre-empt it.
2. Do NOT use phrasing like "extend the existing X" without identifying the exact file/line where X is registered. If you cannot point to a registration site, X may not exist — and "extend" becomes "establish."
3. Do NOT change a type signature of landed code without an explicit Affected-Files entry naming every dispatch / consumption site that must change.
4. Do NOT write code sketches with helper-function references (`_helper()`, `CONST`) unless the helper / constant is either declared in Affected Files or already exists at a cited path.

### Detection Heuristic

For every Affected-Files line in a v1 plan that says MUTATE:
1. Read the file. Confirm the cited symbol exists.
2. Confirm the cited type signature matches reality.
3. If the mutation is type-changing, list every consumption site of the changed type and add it as a sub-bullet to the Affected-Files entry.

For every code sketch in §Changes:
1. Every imported symbol must trace to either an Affected-Files entry OR a current-tree path.
2. Every `_helper()` call must be either local (defined within the same sketch) or declared.
3. Every constant reference (`UPPERCASE_CONST`) must be either local or declared in Affected Files.

### Project Memory Implication

This pattern is the natural consequence of treating a previous-phase plan document as evidence about current state. Plans drift from code as soon as the implement phase ends. **Only the code is ground truth for the next plan's state-of-the-world claims.** Every plan referencing prior-phase symbols should grep-verify those symbols against current HEAD before submission.

The remediation pattern is uniform: the plan amendment must replace each unsupported claim with either (a) a citation to current code, or (b) an explicit Affected-Files entry establishing the missing infrastructure.

### Addendum to Entry #7 (2026-05-02T07:25:00Z)

The amended plan that followed Entry #7 (audit round 2 of `plan-priority-c-team-server-notion-v1.md`) closed all four original findings successfully but introduced a sibling failure under the same root cause: `slack_runner.run_slack_iteration` called `decrypt_token(ws["oauth_token_encrypted"])` with one argument, where the actual signature is `decrypt_token(ciphertext: bytes, key: bytes) -> str`.

The pattern surfaced in Entry #7 was *missing/undeclared symbols*. The amendment correctly closed that pattern by either declaring or grounding every symbol — but the round-2 sketch invoked an *existing, declared* symbol with the wrong call shape. The verification heuristic in Entry #7 ("for every cited symbol... confirm the cited type signature matches reality") was correct in principle but underspecified in practice: it covered `MUTATE` Affected-Files entries but not the in-line code sketches in §Changes blocks.

### Pattern to Avoid (extension)

Extending Entry #7's heuristic — for every code sketch in §Changes:

1. **Existence check**: every `from X import Y` traces to a real module + symbol. (Original Entry #7 contract.)
2. **Signature check**: every call to `Y(...)` matches `Y`'s actual signature: arity, positional-vs-keyword discipline, and argument types. The audit's Infrastructure Alignment Pass should `inspect.signature(Y)` against the call shape. (New extension.)
3. **Type-boundary check**: when a value crosses a persistence boundary (DB column type ↔ in-memory Python type), the conversion must be explicit in the sketch. Specifically: any `str` field stored from a `bytes` source must be encoded back at the read site (e.g. `ws["x"].encode("utf-8")`); any `bytes` field stored from a `str` source must be decoded at the read site. (New extension.)
4. **Helper-symmetry check**: if a write-side path (e.g. `team_server/auth/router.py`'s OAuth callback) uses `helper_a` + `helper_b` to perform the encode + persist combination, the read-side path must use the symmetric `helper_b_inverse` + `helper_a_inverse` chain — not a single helper missing one argument. The existing precedent in the repo IS the contract.

### Detection Heuristic (extension)

For every code sketch with an external function call:

1. Read the function's actual definition. Confirm arity matches.
2. Confirm argument types match. If a literal or named variable in the sketch is the wrong type for the function, name the conversion explicitly in the sketch.
3. Find the symmetric existing precedent in the repo (e.g. the encrypt-side for a decrypt call). If the precedent exists, model the sketch after it.

Adding these to the round-3 amendment closes the documented residual.

