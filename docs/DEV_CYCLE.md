# Development Cycle

**Audience**: contributors, release managers (Jin), and anyone shipping a change
to `BicameralAI/bicameral-mcp`. This document is the contract — if you are about
to open a branch, write a PR, cut a release, or close an issue, follow what is
written here. Deviations require a META_LEDGER entry explaining why.

**Repo topology** (as of v0.13.0, post-Phase-4):

```text
contributor fork (e.g. Knapp-Kevin/bicameral-mcp)
         │  feature branches live here
         ▼
BicameralAI/bicameral-mcp
   ├── dev      ← integration branch; CI green, code complete, NOT shipped
   └── main     ← shipped; tagged; users pull from here
```

Two branches, one direction of flow: **feature → dev → main**. Nothing else
merges to `main` except `dev` (and the rare hotfix — see §10).

---

## 0. Workflow Feature Release Cycle

**Audience**: anyone proposing a new agentic workflow capability — a new
skill, a new lifecycle hook, a new auto-fire trigger, a new dashboard
surface. Distinct from §6 (engineering version release): §6 covers how a
finished change reaches users; **§0 covers how a workflow idea becomes a
finished change worth releasing.**

**Why this exists separately**: most of our P0 misses (#146 preflight
auto-fire, #147 SessionEnd capture-corrections, the e2e harness churn
across 2026-04 → 2026-05) trace back to the same root cause — we shipped
the implementation BEFORE we wrote down what success looks like and
BEFORE we had any way to observe whether it actually worked in the wild.
The fix is to put validation in front of implementation, not behind it.

### The cycle

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  1.      │  │  2.      │  │  3.      │  │  4.      │  │  5.      │  │  6.      │
│ Friction │─▶│Candidate │─▶│  Test    │─▶│Functional│─▶│Telemetry │─▶│Optimized │
│ capture  │  │ workflow │  │ harness  │  │ solution │  │collection│  │ solution │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
                                  ▲                            │
                                  │   ◀─── feedback loop ──────┘
                                  │   (telemetry surfaces gaps the harness should have caught)
```

**Anti-pattern (the trap we keep falling into)**: jump from step 1
directly to step 4. Build the skill. Ship it. Discover the harness can't
observe the auto-fire and telemetry surfaces nothing. Now you're
retrofitting phases 2/3/5 onto a thing already in production — every
iteration loses fidelity because the spec and the implementation are
entangled. (See: every revision history of `tests/e2e/run_e2e_flows.py`.)

### Phases

#### 1. Friction capture

Observable evidence that a real user / agent / contributor stubbed their
toe on something that should "just work." Symptoms, not fixes.

Examples:
- Slack thread from a design partner showing `claude -p '/bicameral:sync'`
  exiting silently (#124).
- Dashboard footage of a mid-session constraint orphaning as a parallel
  decision instead of linking to its parent.
- An e2e harness flow that fails for a reason no one can immediately
  explain.

Captured as a GitHub issue with `friction` or `desync:*` label, in the
repo where the friction was observed. Body answers: *what was the user
trying to do, what happened instead, what would "right" look like.*

**Out of scope at this stage**: solution shape, file paths, schema
changes. Don't pre-commit to an implementation in the friction note.

#### 2. Candidate workflow

A short prose spec of what the new workflow should look like end-to-end,
written from the user/agent perspective, NOT the implementation
perspective. Lives in a source-of-truth issue (e.g.
`BicameralAI/bicameral#108` for the v0 user flow spec).

Format:
- **Trigger**: what does the user do or say to enter this workflow?
- **Sequence**: numbered list of agent-observable steps — tool calls,
  hook fires, status transitions. Reference the spec; do NOT inline
  implementation details (file paths, function names, schema columns).
- **Success outcome**: what visible state proves the workflow worked?
  Status flip, ledger row, dashboard panel, ratification record.
- **Failure modes**: what should the user see when each step fails, and
  what's the recovery path?

The spec is the contract for phases 3–6. If the spec is wrong, the
harness validates the wrong thing and the implementation chases the
wrong target.

#### 3. Test harness

A real e2e test that exercises the spec from step 2 against a real
claude session (not mocks). For bicameral-mcp this lives at
`tests/e2e/run_e2e_flows.py`.

**Required before any implementation work begins.** The harness fails on
day one — that's the point. A failing harness with a clear assertion
message is the spec made executable.

Harness rules:
- Assert on the spec's success outcome, not the implementation path.
  ("After commit, decision X is in `pending` state" is good. "Agent
  called `link_commit` then `resolve_compliance` in that order" is
  brittle and couples the test to the substrate.)
- Use natural prompts — never name the tool the agent is supposed to
  auto-fire. Naming the tool defeats the trigger that IS the product.
- When success isn't observable in stream-json (e.g. a SessionEnd
  subprocess writes to the ledger out-of-band), validate via post-hoc
  ledger query. Document the indirection in the asserter docstring.
- When a flow fails: distinguish test-harness bug from product gap. If
  the asserter is wrong about the spec, fix the asserter (no GitHub
  issue needed). If the spec says X happens and X doesn't happen, that's
  a product gap — open or update an issue, leave the harness asserting
  the spec, mark the failure as expected until the implementation lands.

#### 4. Functional solution

Implementation pass that makes the harness pass. Optimize for spec
correctness — not performance, not polish. Skill description, tool
contract, lifecycle hooks all in scope.

Done when:
- Harness PASSes against the unmodified natural prompt from step 3.
- A real user can complete the flow end-to-end without hitting any of
  the friction from step 1.
- Implementation is documented at the level needed for phase 5 telemetry
  to know what to count.

#### 5. Telemetry collection

Instrument the new workflow with PostHog events /
`bicameral.skill_begin/end` calls / structured logs that answer: *is
this actually being used, by whom, and does it work in their hands?*

Telemetry contract is part of the spec, not an afterthought. Each step
in the candidate workflow (phase 2) should map to a telemetry event the
dashboard can query.

Wire telemetry BEFORE merging the implementation PR. A workflow you
can't observe in production is a workflow that's never validated in
production.

#### 6. Optimized solution

Iterate based on what telemetry shows:
- Drop-off after step N → step N is unclear or broken in real
  conditions. Could be a description fix or a substrate change.
- Auto-fire rate &lt;X% → trigger discipline is losing the priority race;
  restate the skill description, change the trigger phrasing, or move to
  a deterministic hook.
- Compliance verdict mix unexpected → either the rubric is wrong or the
  user is using the workflow differently than the spec assumed.

Optimization changes route through the same cycle: telemetry-observed
friction → updated workflow spec → updated harness → new functional
pass → new telemetry. Don't optimize without re-passing the harness.

### Audit trail

Every workflow feature gets a short META_LEDGER entry at each phase
boundary:

```
2026-05-01  workflow:bicameral-capture-corrections  phase=3→4
  harness PR: BicameralAI/bicameral-mcp#147 (SKIP→SETUP)
  spec: BicameralAI/bicameral#108 § Flow 4
  next: implementation PR + telemetry wiring
```

This makes it possible to look at any open workflow feature and
immediately see which phase it's in, what's blocking the next phase, and
where the spec lives. It's also the first place to look when a feature
ships and silently regresses — phase boundaries are where the harness
should pass before/after the change.

---

## 1. Lifecycle map

```
┌──────────┐   ┌────────┐   ┌──────────┐   ┌─────┐   ┌─────────────┐   ┌──────┐   ┌────────┐
│  Issue   │──▶│ Branch │──▶│ Feature  │──▶│ dev │──▶│  Release PR │──▶│ main │──▶│  Tag   │
│ (#nnn)   │   │ named  │   │   PR     │   │     │   │  (dev→main) │   │      │   │ vX.Y.Z │
│          │   │ /<n>-x │   │ → dev    │   │     │   │             │   │      │   │        │
└──────────┘   └────────┘   └──────────┘   └─────┘   └─────────────┘   └──────┘   └────────┘
     │              │            │           │              │             │           │
     │              │       Closes #nnn      │              │             │      GitHub
     │              │       on squash        │       Bumps version,       │      Release
     │              │            │           │       CHANGELOG flip,      │      published
     │              │            ▼           │       milestone close      │           │
     │              │      CI must pass      │              │             │           ▼
     │              │      QOR seal in       │              ▼             │    Help/training
     │              │      META_LEDGER       │      Squash-merge          │    docs published
     │              │                        │      OR merge commit       │
     ▼              ▼                        ▼                            ▼
 Milestone:    Branch name:                Issue auto-closed,    User-facing release;
 vX.Y.Z        <issue#>-<short-slug>       milestone open        upstream consumers
                                           ("pending release")    pull from main
```

**One rule of thumb**: any work that touches user-visible behavior must traverse
every box in that diagram. No back-doors to `main`.

---

## 2. Issues

### 2.1 Creating

- **Title**: imperative, scoped. `feat(codegenome): semantic drift evaluation in resolve_compliance`,
  not "add drift evaluation". **Do not** prefix with `[P0]`/`[P1]`/`[P2]` — use
  the priority labels in §2.1.1 instead.
- **Required labels** (apply at least one of each mandatory axis):
  - **Type** (mandatory): `feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `perf`, `security`.
  - **Surface** (mandatory): `tool`, `skill`, `ledger`, `code-locator`, `codegenome`, `infra`, `docs-only`.
  - **Priority** (mandatory after triage): see §2.1.1 below.
  - **State** (optional): see §2.1.2 below.
- **Milestone**: attach to the next-up release (`v0.14.0`). If you don't know
  which release it lands in, attach to `vNext-triage` and let Jin re-assign.
- **Body template** (see `.github/ISSUE_TEMPLATE/`):
  - **Why**: one paragraph. The product decision this serves.
  - **What**: the smallest change that satisfies "Why".
  - **Out of scope**: explicit exclusions. Stops scope creep at PR-review time.
  - **Acceptance**: bullet list of testable conditions. CI green is implied; add
    behavioural checks ("`link_commit` returns `auto_resolved_count` ≥ 0").

> **Risk** (`risk:L1` / `risk:L2` / `risk:L3`) lives on **PRs**, not issues —
> see §4.4. Risk is a property of the change being made, knowable only after
> design. Issues carry priority (urgency); PRs carry risk (review tier).

#### 2.1.1 Priority labels (one per issue, mandatory after triage)

Exactly one priority label per triaged issue. Untriaged issues carry `triage`
(see §2.1.2) until a maintainer assigns priority.

| Label | Color | Meaning |
|---|---|---|
| `P0` | red | Critical — drop everything. Production down, data loss, security regression, ledger corruption. **Triggers an immediate response, even off-hours.** |
| `P1` | orange | High — ship this milestone. User-impacting bug or committed feature with a deadline. |
| `P2` | yellow | Medium — next milestone or two. The default for routine new feature work and non-urgent bugs. |
| `P3` | grey | Low — eventually. Nice-to-have, polish, non-load-bearing improvements. |

**Calibration heuristics**:

- *"If this stays open for the next two months, will any user be unhappy?"*
  → No: `P3`. Yes: at least `P2`.
- *"Is there a workaround that's acceptable for the next milestone?"*
  → Yes: `P2` or lower. No: at least `P1`.
- *"Is anyone losing data, money, or trust right now?"*
  → Yes: `P0`. No: not `P0`.

**P0 is rare.** If we have more than two open `P0` issues at any time, something
is wrong with our triage discipline — `P0` should mean *"the team stops other
work"*. Promoting too many issues to `P0` dilutes the signal.

#### 2.1.2 State labels (optional, orthogonal to priority)

| Label | Color | Meaning |
|---|---|---|
| `triage` | light grey | Needs assessment; no priority assigned yet. Default for newly-filed issues. |
| `blocked` | dark grey | Temporarily blocked by another issue or external dependency. Always include a comment naming the blocker. |
| `parked` | purple | Known issue, deferred indefinitely (external blocker, strategic pause, cost > benefit at current scale). Not abandoned, but not on a roadmap. **Only maintainers apply `parked`.** |

State labels are mostly orthogonal to priority — with one exception:

- **`triage` and `blocked` coexist with priority.** A `P1 + blocked` issue is
  high-priority work waiting on a dependency; a `triage` issue gets a priority
  label as soon as a maintainer assesses it.
- **`parked` supersedes priority.** Don't apply both. A parked issue is, by
  definition, not on the priority axis — it's deferred indefinitely. Adding
  `P3` to a `parked` issue is redundant and clutters the label list. If a
  parked issue ever becomes actionable, drop `parked` and assign a real
  priority at that moment.

**Never close a `parked` issue** — keep it open as a known-deferred record
so future filers find it.

The existing `merged-to-dev` label (post-merge status, not pre-merge state)
remains separate from this axis. See §6.8.

### 2.2 Closure

`Closes #X` in a PR body **fires when that PR's HEAD merges into its BASE**, not
when work reaches `main`. PRs target `dev`, so issues close at the dev-merge.

Why we keep auto-close on dev: closure tracks "the work is in code", milestones
track "the work is shipped". Two signals, two artifacts.

### 2.3 Reopening

If a hotfix or follow-up reveals the dev work was wrong, **reopen the original
issue** rather than filing a new one — keeps history threaded. Add a comment
linking the regression's hotfix PR.

---

## 3. Branches

### 3.1 Naming

`<issue#>-<short-slug>` from a fork.

```
Knapp-Kevin/codegenome-phase-4-qor    ← acceptable (descriptive slug)
Knapp-Kevin/61-drift-classifier       ← preferred (issue-numbered)
Knapp-Kevin/main                      ← never push feature work to fork's main
Knapp-Kevin/dev                       ← does not exist (BicameralAI/dev is canonical)
```

A fork's `dev` branch is **not** maintained. The integration branch is exactly
one place: `BicameralAI/dev`.

### 3.2 Branching off

Always branch off `BicameralAI/dev`, never `main`. `dev` is what other in-flight
work has integrated against; `main` is a moving snapshot of the last release.

```bash
git fetch BicameralAI dev
git checkout -b 61-drift-classifier BicameralAI/dev
```

### 3.3 Stacking

Stacked PRs (PR B depends on PR A's branch) are tolerated for short windows
(< 48 h). Rebase the stack onto `dev` the moment the bottom PR merges. Long
stacks compound merge-conflict risk and review fatigue.

---

## 4. Pull Requests

### 4.1 Targeting

**All feature/fix PRs target `dev`.** The release PR (and only the release PR)
targets `main`. CI workflows enforce both: `pull_request: branches: [main, dev]`.

#### 4.1.1 Flow labels (mandatory)

Every PR carries exactly one `flow:` label so contributors and reviewers can
tell at a glance which lane it's in. The label mirrors the target branch but
disambiguates the two cases that share `main`:

| Label | Color | Target | Meaning |
|---|---|---|---|
| `flow:feature` | green | `dev` | Standard feature/fix going through the integration branch. The default. |
| `flow:release` | blue | `main` | Periodic `dev → main` release PR opened by the release manager. Carries no new code — only the integrated `dev` HEAD. |
| `flow:hotfix` | red | `main` | Emergency fix bypassing `dev`. Sets the §10 sync-back-to-dev clock. |

Why labels in addition to the base branch:

- `gh pr list --base main` returns *both* release PRs and hotfix PRs — different
  processes, different review tiers, different urgencies. The label
  disambiguates.
- Filters like `gh pr list --label flow:hotfix --state closed` give a clean
  audit trail of every emergency bypass over time. We want that visible.
- Dependabot auto-applies `flow:feature` via `.github/dependabot.yml`; nothing
  arrives without a flow label.

Reviewers can refuse to review a PR that has no `flow:` label — the contract
is "label first, review second."

**Distinct from the post-merge `merged-to-dev` label.** That one tracks
*status* ("this work has landed on dev but not yet on main"). The `flow:`
labels track *intent* (which lane the PR is in). Both can coexist on a single
PR after merge if Jin uses `merged-to-dev` to surface his release queue.

### 4.2 Title

`<type>(<surface>): <imperative summary>` — the same shape as the issue title.
The squash commit message inherits this; loose PR titles produce ugly history.

### 4.3 Body — required sections

```markdown
## Summary
1–3 bullets, user-facing outcome.

## Linked issues
Closes #61
Refs #60 (depends on continuity matcher landed there)

## Plan / Audit / Seal
- Plan: docs/Planning/plan-codegenome-phase-4.md (v3, content hash sha256:911171cf…)
- Audit: META_LEDGER Entry #13, chain hash 21ac210f… — verdict PASS
- Seal:  META_LEDGER Entry #14, chain hash 0ebcf69b…

## Test plan
- [ ] `pytest tests/test_codegenome_drift_classifier.py -q` (32/32)
- [ ] `pytest tests/test_m3_benchmark.py -q` (5/5)
- [ ] regression: `pytest -q` (189/189)
```

The Plan/Audit/Seal section is **mandatory for any PR > 100 LOC or risk:L2+**.
Smaller PRs may use `Plan: trivial; risk:L1`.

### 4.4 Reviewers

- Code-owner from `CODEOWNERS` is auto-requested.
- **Risk:L3 PRs**: require a second reviewer + a security-pass note in the
  description.
- **Risk:L2 PRs**: one reviewer.
- **Risk:L1 PRs** (typo, comment fixes, dep bumps from Dependabot with green
  CI): owner self-merge after CI is green.

### 4.5 CI gates

Two-tier model: a fast set on every PR-to-`dev`, a deeper set on the release
PR (`dev` → `main`). The asymmetry is deliberate — see §4.5.3.

#### 4.5.1 Tier 1 — PR → `dev` (fast, blocks every PR)

The bar is *"this won't break dev for everyone else."* Target wall-clock: under
5 minutes. Red on any of these blocks merge.

| Gate | Workflow / tool | Why |
|---|---|---|
| **Lint** | `ruff` + `black --check` | Catches style drift, dead imports, unused vars before review |
| **Type check** | `mypy` (or `pyright`) | Type errors surface at runtime via Pydantic boundaries; keep them at PR-time |
| **Unit + integration tests (Linux)** | `test-mcp-regression.yml` (existing) | Core regression suite |
| **Unit + integration tests (Windows)** | matrix on `test-mcp-regression.yml` | Three of the last four bugs (#67, #68, #74) were Windows-only — manual verification is not a strategy |
| **Schema persistence smoke** | `test-schema-persistence.yml` (existing) | Schema bugs are silent killers; cheap to run |
| **Module import smoke** | `python -c "import server, telemetry, consent, ..."` | Catches missing modules / circular imports in seconds |
| **Secret scan** | `gitleaks` or `trufflehog`, fail-on-find | API keys, tokens, credentials in code or test fixtures |
| **`pip check`** | one-liner job | Detects broken dependency tree on the PR's `pip install -e .[test]` |
| **`merged-to-dev` label automation** | post-merge GitHub Action | Auto-applies the label on merge; resolves the manual labeling problem from the PR-A audit |

#### 4.5.2 Tier 2 — Release PR (`dev` → `main`)

The bar is *"this is releasable to users."* Inherits all Tier 1 gates plus the
following. Can run 10–20 minutes; runs less often (one release PR at a time).

| Gate | Workflow / tool | Why |
|---|---|---|
| **All Tier 1 gates** | — | Inherits dev's bar |
| **Full regression including slow markers** | `pytest -m "not bench"` | Tier 1 may exclude `alpha_flow`, `desync_scenarios`; the release run includes them |
| **Preflight eval — blocking** | `preflight-eval.yml` (currently advisory) | Currently advisory on every PR; should block release if drift precision regresses |
| **Schema migration validation against persistent DB with seed data** | bespoke job | Beyond the smoke — apply migration on a `v_(N-1)` seed, assert no row loss + roundtrip works |
| **Performance regression** | bespoke job | Drift detection p50, ingest throughput, search latency. Fail if > 15% regression vs `main`'s last successful run |
| **Security scan** | `bandit`, `pip-audit`, GitHub Dependency Review | Required before any user touches the binary |
| **CHANGELOG enforcement** | bespoke job | Reject release PR if `CHANGELOG.md` does not move `## Unreleased` content under a new `## [vX.Y.Z]` block |
| **Version monotonicity** | bespoke job | Version in `pyproject.toml` must be `>` current `main` tag |
| **MCP protocol live smoke** | bespoke job | Spawn server, call each tool over stdio, assert response shape. Catches handler-registration / Pydantic-boundary issues unit tests miss |
| **Issue auto-close on merge** | post-merge action | `Closes #N` fires on merge into the PR's base; on release PR merge to `main`, also strip the `merged-to-dev` label from issues whose fix is now shipped |

#### 4.5.3 Why the split

The asymmetry isn't arbitrary — it's about **failure cost vs velocity**:

| Concern | dev gate | main gate |
|---|---|---|
| Style / type errors | Block dev (cheap to fix at PR time) | Inherited |
| Windows breakage | Block dev (recent bug history mandates) | Inherited |
| Eval regression | Advisory on dev (don't slow feature work for noise) | **Block main** (release quality) |
| Performance regression | Don't run (too slow per PR) | **Block main** |
| CHANGELOG / version | Don't enforce (dev work is in-flight) | **Block main** |
| Security scan | Don't run per PR (slow, noisy) | **Block main** |
| MCP protocol live smoke | Don't run (requires server boot) | **Block main** |

#### 4.5.4 Implementation phases (current state vs target)

A dev-cycle gate is only as strong as its branch-protection rule. Adding the
workflow file is half the job; the other half is requiring it via the GitHub
"Require status checks to pass before merging" setting on `dev` and `main`.

**Phase 1 — biggest impact, low risk** (open as one chore PR):

1. Add Windows test job to `test-mcp-regression.yml` matrix
   (`runs-on: [ubuntu-latest, windows-latest]`).
2. Add `lint-and-typecheck.yml` (ruff + mypy) running on all PRs.
3. Add `secret-scan.yml` (gitleaks) on all PRs.
4. Add the `merged-to-dev` auto-labeller as a post-merge action on `dev`.
5. Update `dev` branch-protection to require: lint, typecheck, regression
   (Linux + Windows), schema persistence, secret scan.

**Phase 2 — release-quality gates**:

6. Convert `preflight-eval.yml` from advisory to blocking on `main`-bound PRs
   only (use `if: github.base_ref == 'main'`).
7. New `release-gates.yml` running only on `main`-bound PRs: CHANGELOG diff,
   version monotonicity, MCP live smoke.
8. Add `bandit` + `pip-audit` to `release-gates`.
9. Performance baseline harness — capture drift detection p50 and search
   latency; compare against `main`'s last successful run.
10. Update `main` branch-protection to require all Tier 1 + Tier 2 checks.

**Phase 3 — nice to have**:

11. Auto-close `merged-to-dev` issues when `dev` → `main` forward-merges.
12. Sticky PR-comment bot for preflight-eval results (covered by issue #49).

Until Phase 1 ships, the documented Tier 1 list is **aspirational** — only
`test-mcp-regression`, `test-schema-persistence`, and `preflight-eval`
(advisory) actually run today. Reviewers should treat the rest as their own
responsibility (run lint locally, verify on Windows, etc.) until the gates
land.

Red CI blocks merge. Don't ask reviewers to look at red PRs.

### 4.6 Review feedback discipline

CodeRabbit, Devin, and human reviewers all leave comments. The author's job:

- **Address** every actionable comment with a commit or a reply justifying
  decline.
- **Resolve** the conversation thread only after addressing.
- **Never** push `--force` on a PR with active review threads — comments lose
  their line anchors. Use `--force-with-lease` only after a `git fetch`, and
  call it out in a PR comment so reviewers re-fetch.

---

## 5. Merging to `dev`

### 5.1 Strategy

**Squash merging is disabled at the repo level** (`allow_squash_merge: false`)
so the wrong choice is unavailable, not just discouraged. The reason this
matters at all — beyond style preference — is that squash collapses
multi-commit PRs into opaque blobs that cannot be cleanly cherry-picked into
the §10.5 triage lane. See §10.5.0 "Why this lane exists" for the full
rationale. Two options remain:

| Merge style | When to use | Rationale |
|---|---|---|
| **Rebase and merge** *(default — covers ~all PRs)* | Single-commit PRs; multi-commit features; any PR a maintainer might backport to `triage-from-dev`; any PR with a `Triage-Cc:` trailer (see §10.5); Dependabot bumps | Preserves atomic commits as individually-cherry-pickable SHAs on `dev`. For single-commit PRs, this is the literal squash equivalent (one commit on `dev`) without the opaque-blob failure mode. GitHub's docs explicitly warn that squashing long-running branches "makes merge conflicts more likely … you'll have to resolve the same conflicts repeatedly." |
| **Merge commit (`--no-ff`)** | Multi-commit features whose grouping matters historically (e.g. coordinated multi-handler refactor); any PR you may want to revert atomically with `git revert -m 1` | Preserves both individual commits *and* the merge boundary. Use sparingly — `dev` log gets noisy fast. |

**Author obligation, not just merger obligation.** If you write a PR that may be
triage-eligible, write atomic commits — one logical change per commit, each
individually buildable, each with a meaningful subject line. The Linux kernel's
atomic-commit discipline ([Linus on commit messages](https://yarchive.net/comp/linux/commit_messages.html))
exists precisely so cherry-pick is mechanical, not interpretive. Reviewers may
ask you to reorganize. WIP messages like `wip`, `fix typo`, `address review`
should be squashed locally with `git rebase -i` *before* the PR is merged —
since repo-level squash is off, the rebase-and-merge button will preserve them
verbatim otherwise.

### 5.2 Pre-merge checklist (for the merger)

- [ ] CI green
- [ ] All review threads resolved
- [ ] Milestone attached on the PR (== same milestone as the issue)
- [ ] Plan / Audit / Seal references exist for non-trivial PRs
- [ ] CHANGELOG `## Unreleased` updated (or PR explicitly states "no user-visible change")

### 5.3 Post-merge

- Issue auto-closes (via `Closes #X`).
- Milestone progress bar advances.
- Branch may be deleted (GitHub default).
- If the work shipped a new tool / new tool field / changed default, the matching
  `pilot/mcp/skills/<tool>/SKILL.md` **must** be in the same PR — for
  rebase-and-merge, in the same atomic commit; for merge-commit, in one of the
  commits being merged. Project rule from `CLAUDE.md`. Reviewers reject
  silently-mismatched skill contracts.

---

## 6. Release cycle

### 6.1 Cadence

- **Minor releases** (`v0.X.0`): roughly every 2–3 weeks, when the milestone is
  full and `dev` is stable.
- **Patch releases** (`v0.X.Y`): as needed for bug fixes that can't wait.
- **Major release** (`v1.0.0`): scheduled; not driven by milestone fill.

Jin owns the call on "is `dev` ready to ship". Heuristic: milestone closed-issue
count covers the headline features, and CI on `dev` HEAD has been green for ≥ 24 h.

### 6.2 Version selection

Semver applies:

- **PATCH** — bug fix only, no public-API change, no schema migration.
- **MINOR** — new tool / new tool field / new schema migration that is **additive**
  with a registered `_migrate_vN_to_vN+1` and bumped `SCHEMA_COMPATIBILITY` map.
- **MAJOR** — breaking change to a tool's request/response shape, or a destructive
  schema migration, or a CLI flag rename.

If the change is borderline, round **up**. Schema-migrating PRs are never PATCH.

### 6.3 The release PR (`dev` → `main`)

Jin opens this PR. It targets `main`, base = `main`, head = `dev`.

**Title**: `release: v0.13.0`

**Body**:

```markdown
## Release v0.13.0

### Headline
One sentence the README and Twitter post can both quote.

### Included issues
Closes milestone v0.13.0
- #61 — CodeGenome Phase 4 (semantic drift evaluation)
- #75 — <…>
- …

### Schema
- Migrates ledger v13 → v14 (additive: CHANGEFEED on compliance_check,
  semantic_status, evidence_refs)

### Breaking changes
None. (or: list each.)

### Documentation
- CHANGELOG.md — v0.13.0 section
- skills/bicameral-sync/SKILL.md — Phase 3+4 callout updated
- README.md — bumped feature list (if applicable)
- New: docs/DEV_CYCLE.md
```

### 6.4 Pre-release checklist

Jin runs through this before merging the release PR. Items marked **CI** are
enforced by the Tier 2 gates in §4.5.2 once Phase 2 lands; until then they are
manual.

- [ ] **CHANGELOG flip** — move `## Unreleased` content under `## [v0.13.0] - 2026-04-29`.
      Add a fresh empty `## Unreleased` block at the top. **(CI: CHANGELOG enforcement)**
- [ ] **Version bump** — update `pyproject.toml` / `__init__.py` / wherever the
      canonical version lives. **(CI: version monotonicity)**
- [ ] **`SCHEMA_COMPATIBILITY` map** — confirm the new schema version maps to the
      new release version (e.g. `14: "0.13.0"`). **(CI: schema migration validation)**
- [ ] **Skill files** — every changed skill is committed in `pilot/mcp/skills/`,
      not just in `.claude/skills/`.
- [ ] **Help / training docs** (see §8) — published for any feature on the
      "user-touching" list.
- [ ] **Demo readiness** — at least one demo script (§11) covers each headline
      feature.
- [ ] **CI on `dev` HEAD** — green for ≥ 24 h. **(CI: full regression incl. slow markers)**
- [ ] **Preflight eval** — blocking gate, no regression vs `main`'s baseline.
      **(CI: preflight-eval blocking on `main`-bound)**
- [ ] **Performance** — drift detection p50, ingest throughput, search latency
      within ±15 % of `main`'s last successful run. **(CI: performance regression)**
- [ ] **Security scan** — `bandit` + `pip-audit` + GitHub Dependency Review
      clean. **(CI: security scan)**
- [ ] **MCP protocol live smoke** — server boots, every registered tool returns
      a shape-conformant response over stdio. **(CI: MCP protocol live smoke)**
- [ ] **Milestone** — every issue under it is closed.

### 6.5 Merging the release PR

**Strategy**: **merge-commit**, not squash. `main` is meant to preserve the
release boundary in history; a merge commit ("`Merge dev into main for
v0.13.0`") gives `git log main` a clean release-by-release walk.

```bash
git checkout main
git pull
git merge --no-ff dev -m "release: v0.13.0"
git push
```

GitHub's UI "Create a merge commit" button does the same.

### 6.6 Tagging

Immediately after the merge:

```bash
git tag -a v0.13.0 -m "Release v0.13.0 — CodeGenome Phase 4 (semantic drift)"
git push --tags
```

Tag format: `vMAJOR.MINOR.PATCH`. Annotated, never lightweight. The annotation
body is the headline sentence from the release PR.

### 6.7 GitHub Release

Create a Release object on GitHub from the tag (`gh release create v0.13.0` or
the UI):

**Title**: `v0.13.0 — CodeGenome Phase 4 (semantic drift)`

**Body**: copy/paste the CHANGELOG section for this version, then append:

```markdown
---

## Documentation
- [Migration notes](https://…/docs/migrations/v0.13.md) — schema v13 → v14
- [User guide for semantic drift evaluation](https://…/docs/guides/semantic-drift.md)
- [Demo: cosmetic-vs-semantic auto-resolve](https://…/docs/demos/04-drift-classifier.md)

## Verification
Merkle seal: 0ebcf69b…
META_LEDGER entries: #11 (VETO), #12 (PASS), #13 (PASS post-rebase), #14 (seal)
```

**Attachments**: none for now (we ship via PyPI/source). When we ship binaries,
attach platform builds here.

### 6.8 Post-release

- Close the milestone.
- Open the next milestone (`v0.14.0`).
- Announce: README badge bump, project README "Latest" line, optional Slack /
  Discord drop. Use the headline sentence verbatim.

---

## 7. CHANGELOG.md conventions

We follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely.

**Top of file at all times**:

```markdown
## [Unreleased]

### Added
- (work in flight that's already merged to dev)

### Changed
### Fixed
### Schema
### Security
```

When Jin cuts a release, he replaces `[Unreleased]` with the version + date,
then prepends a fresh empty `[Unreleased]` block.

**Section ordering** (preserve even when empty — drop a section only at release
flip): `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Schema`,
`Security`.

**One bullet per logical change**, not per file. User-facing language. Internal
governance details (chain hashes, verdicts) stay out of CHANGELOG; they live in
META_LEDGER.

---

## 8. Documentation requirements per release

Some features ship with code only. Some ship with code **plus** mandatory docs.
Use this matrix:

| Feature class | User-touching? | Docs required |
|---|---|---|
| New MCP tool | yes | `pilot/mcp/skills/<tool>/SKILL.md` + entry in `README.md#tools` |
| New tool field / new status value | yes | Update every skill that renders the field |
| New schema migration | indirect | `docs/migrations/vN.md` — what changes, automatic or manual |
| New caller-facing helper (e.g. `ensure_ledger_synced`) | yes | `docs/guides/<feature>.md` user guide |
| New deterministic primitive (e.g. continuity matcher) | yes | demo script in `docs/demos/` |
| Bug fix without behavior change | no | CHANGELOG entry only |
| Internal refactor | no | CHANGELOG entry only ("Changed: …") |
| Performance improvement | no, unless > 2× | CHANGELOG entry; `> 2×` adds a `docs/perf/` note |
| Security fix | yes | CHANGELOG `### Security` entry + `SECURITY.md` advisory if disclosed |

**Help docs go in**: `docs/guides/<feature>.md`. Structure:

```markdown
# <Feature> — User Guide

## What it does
One paragraph.

## When you'd use it
Bulleted scenarios.

## Quickstart
Smallest end-to-end example.

## Reference
Tool name, request shape, response shape, error modes.

## See also
Links to related guides + demo script.
```

**Training docs** (longer-form, multi-step walkthroughs intended to teach a
concept, not just document a tool) go in `docs/training/<topic>.md`. These are
optional unless the feature introduces a concept the user must internalize
(example: "what does `pending` vs `reflected` mean?" — that's training, not
reference).

---

## 9. Skill file rule (project-specific, mandatory)

From `CLAUDE.md`:

> Any change to an MCP tool's behavior — new fields in a response, new status
> values, changed defaults, new tool calls, deprecated params — **must ship
> with a matching update to the relevant `pilot/mcp/skills/*/SKILL.md`** in the
> same commit.

This is enforced at review time. `pilot/mcp/skills/` is canonical;
`.claude/skills/bicameral-*/SKILL.md` copies are stale and slated for deletion.

---

## 10. Hotfix path (main → main → dev)

When `main` has a bug that can't wait for the next release:

```
                                    ┌──── tag v0.13.1 ────┐
main ─────●─────────────────────────●─────────────────────●─────▶
           \                       /                       \
            └── hotfix/0.13.1 ────┘                         │
                                                            │ merge or
                                                            │ cherry-pick
                                                            ▼
dev  ─────────────────────────────────────────────────────●─────▶
```

1. Branch from `main` (not `dev`): `hotfix/0.13.1-<slug>`.
2. Smallest possible diff. No tangential cleanup.
3. PR targets `main`. Reviewer approves; CI green.
4. Merge to `main`, tag `v0.13.1`, GitHub Release.
5. **Immediately** sync to `dev`: either merge `main` into `dev` or cherry-pick
   the hotfix commit. Resolve conflicts. Push. Don't let `dev` and `main`
   diverge in opposite directions for more than an hour.

Hotfixes never carry feature work — feature work goes through the normal
feature → dev → release cycle.

### 10.5 Triage lane (`dev` → `triage-from-dev` → `main`)

`triage-from-dev` is a long-lived **curated stable lane** that ships a *subset*
of `dev` to `main` between full releases. It exists for changes that should
reach users faster than the next minor release allows, but that aren't
emergency hotfixes (which use §10's path).

#### 10.5.0 Why this lane exists

The triage lane plus the §5.1 rebase-and-merge default (with squash disabled
at the repo level) together **allow for parallel development of feature work
on `dev` and selective incorporation into production based on live feedback**.

That goal decomposes into three constraints the existing two-branch flow
(feature → dev → main) cannot satisfy on its own:

- **Fast iteration on `dev` shouldn't gate user-visible delivery on `main`.**
  Without a triage lane, every minor-release cycle is "ship the whole
  integrated batch or wait." A bug fix that's ready in week one of a six-week
  release cycle waits five weeks for a milestone full of unrelated work to
  close. The triage lane lets ready-and-eligible work reach users on its own
  cadence.
- **Live feedback should steer what reaches `main`, not just what reaches
  `dev`.** When telemetry / a customer report / a security finding marks a
  specific change as important, the maintainer needs to be able to ship that
  change *without* shipping everything ahead of it on `dev`. Cherry-picking a
  selected subset (under §10.5.1's eligibility rule) is that mechanism.
- **The merge style on `dev` must preserve cherry-pickability.** Squash
  collapses a multi-commit PR into one opaque blob — fine for `dev`'s log,
  fatal for backport. Rebase-and-merge keeps each commit as an individually
  addressable SHA, which is the unit the §10.5.3 cherry-pick mechanic operates
  on. §5.1's "squash disabled at the repo level" exists to make this
  guarantee structural rather than aspirational.

Together these rules let the project hold two timelines: a fast-iteration
trunk where features can land in pieces and the team can change its mind, and
a slower curated trunk where users see only what's been deemed ready for
broad delivery. Neither trunk forces the other's cadence.

```
dev ────●────●────●────●────●────●─────▶
            \         \    \
             cherry-pick -x  (selected commits only)
              \         \    \
               ▼         ▼    ▼
triage-from-dev ●────────●────●─────▶ ──── release PR ────▶ main
```

**Direction is one-way.** Cherry-picks flow `dev → triage-from-dev` only. Never
develop on `triage-from-dev` directly; never cherry-pick `triage-from-dev →
dev`. (Bugs introduced *only* on the triage lane get fixed on `dev` first, then
re-cherry-picked.)

#### 10.5.1 Eligibility — what gets triaged

Modeled after the Linux kernel's `stable` tree rules
([kernel.org stable rules](https://docs.kernel.org/process/stable-kernel-rules.html)).
A commit is triage-eligible if **all** of:

- It is small and self-contained (rough guideline: ≤ 100 lines of context-diff,
  one logical change).
- It is **obviously correct and tested** — the kernel's exact phrasing.
- It fixes one of: a real user-facing bug, a security regression, a build break
  on a supported platform, a data-loss/corruption bug, or a documented
  cross-platform quirk. Or it is a small additive feature whose risk surface is
  isolated (e.g. a new optional MCP tool field with a default).
- It does not depend on `dev`-only refactors that haven't shipped to `main`. If
  it does, the prerequisites must be triage-eligible too, and they all
  cherry-pick as a coherent batch.

**Not triage-eligible** by default: schema-migrating changes, breaking
public-API changes, multi-PR feature epics, "v1 patches" (the catch-all
`triage-from-dev` PR title uses for work explicitly held for the next major).

When in doubt, the change waits for the next `dev → main` release.

#### 10.5.2 Author trailer — `Triage-Cc:`

If you (the author) believe a commit belongs on the triage lane, add a trailer:

```
Triage-Cc: triage-from-dev
```

For commits that fix an earlier commit (kernel-style), also add:

```
Fixes: <abbrev-sha> ("<subject of fixed commit>")
```

The release manager finds candidates with:

```bash
git log --grep='^Triage-Cc:' origin/dev ^origin/triage-from-dev
```

Trailers are advisory — the release manager makes the final call — but they
make the candidate set legible without re-reading every commit message.

#### 10.5.3 Cherry-pick mechanics

Always use `cherry-pick -x` so the resulting commit message records its
provenance (`(cherry picked from commit <dev-sha>)`):

```bash
git checkout triage-from-dev
git fetch origin
git cherry-pick -x <dev-sha>
# resolve conflicts narrowly — do NOT pull in unrelated dev refactors
git push origin triage-from-dev
```

When a cherry-pick conflicts, classify the conflict before resolving:

- **Missing-prerequisite conflict** — the dev commit calls a function /
  references a schema field / depends on a contract that does not exist on
  `triage-from-dev` and is not introduced by this same commit. **Stop.** Either
  pick the prerequisite first (if it is itself triage-eligible per §10.5.1) or
  hold the change for the next full `dev → main` release.
- **Diverged-surface conflict** — the change's *target file* has been
  refactored on dev's path between triage's branch point and the cherry-pick
  source, but every symbol / schema field / contract the cherry-picked commit
  *actually depends on* either already exists on triage or is additively
  introduced in this same commit. **Adaptable** — see below.

##### Adaptation clause

A diverged-surface conflict may be resolved by manually adapting the conflict
hunks to triage's surrounding code, provided **all** of the following hold:

1. The cherry-pick's *intent* (the conceptual change — e.g. "route through
   new adapter method", "add replay case for new event type") is preserved.
   The semantic effect on triage matches the semantic effect on dev from any
   external caller's POV.
2. No new logic is *invented* — every line in the resolution either comes
   from the cherry-picked commit, exists on triage already, or is the
   minimal mechanical glue to bridge the two (e.g. renaming a local variable
   to match triage's existing identifier).
3. Each adapted hunk is annotated:
   - In the **commit message** under an `Adaptation:` trailer:
     `Adaptation: handlers/ratify.py — rewrote against pre-#65 inline impl`
   - In the **code itself**, where the adapted block isn't trivially obvious,
     with `# triage-adapt: <one-line reason>` immediately above the block.

If you find yourself writing a hunk that doesn't satisfy (2) — i.e. you're
inventing logic to bridge the gap — the conflict is in fact a missing-
prerequisite conflict in disguise. Stop and reclassify.

The release manager reviews adapted commits with extra scrutiny at the
§10.5.4 release PR; adapted commits should be a small fraction of any
triage release, and a triage cycle that's mostly adaptations is a signal
that the lane has drifted too far from `dev`.

Resolving conflicts by inventing replacement code that does not satisfy the
adaptation clause above is forbidden — the cherry-pick must remain a faithful
subset of `dev`, modulo legitimate adaptation to a diverged surface.

The fact that `triage-from-dev` already carries some commits with **different
SHAs than dev** (e.g. v0.14.0 telemetry, RFC #98) is sunk cost from the lane's
pre-§10.5 era. Going forward every cherry-pick uses `-x` and the audit trail
re-converges. Do **not** rewrite history on `triage-from-dev` to fix the
divergence — it is a published branch.

#### 10.5.4 Release PR (`triage-from-dev` → `main`)

The triage release PR follows §6 with two adjustments:

- **Title**: `release: v0.X.Y (triage)` — the patch version bumps; minor stays
  pinned to whatever `main` last tagged from a full `dev → main` release.
- **Flow label**: `flow:release` (same as a full release).
- **Body** lists each cherry-picked commit with its source `dev-sha` and the
  issue/PR it traces back to.

After the triage release tags on `main`, sync `main` back to `dev` per §10
(merge or cherry-pick — the next-release CHANGELOG flip absorbs the patch).

---

## 11. Roles

| Role | Owner | Responsibilities |
|---|---|---|
| **Contributor** | anyone | Open issues, branch off `dev`, open PRs to `dev`, address review feedback, keep skill files in sync. |
| **Reviewer** | code-owners | Block on red CI, Razor violations, missing skill updates, missing Plan/Audit/Seal references on non-trivial PRs. |
| **Release manager** | Jin | Decide release cadence, open release PR, run pre-release checklist, merge to `main`, tag, publish GitHub Release, manage milestones. |
| **Doc steward** | rotating | Verify the §8 matrix is satisfied before each release. |
| **Governance steward** | QOR-chain owner | Verify META_LEDGER chain integrity at each release seal. |

Single-maintainer fallback: if Jin is offline, the release waits. We do not
unilaterally promote `dev` → `main`.

---

## 12. Demo scripts

Every shipped feature should have at least one runnable demo that takes a
viewer from "I don't know what this does" to "I see the value" in under 5
minutes. Demos live in `docs/demos/<NN>-<slug>.md` and follow the same template:

```markdown
# Demo NN: <Title>

**Audience**: <e.g. "first-time evaluator">
**Time**: <≤ 5 min>
**Prereqs**: <repo cloned, deps installed, MCP server running>

## What you'll see
1-paragraph spoiler.

## Setup
Copy-pasteable shell block.

## Walkthrough
Numbered steps, each with the exact tool call / command and the expected
output (truncated where it makes sense).

## What just happened
Plain-English read of the result. Tie it back to the user-value claim.

## Next
Pointer to the user guide and related demos.
```

Below: four demo scripts that cover the project's headline functionality. Each
one should be authored as a standalone file and kept in sync with the matching
skill / tool.

### Demo 01 — First decision bind, search, drift detect

**Path**: `docs/demos/01-first-bind.md`
**Audience**: "I just installed bicameral-mcp; what's the loop?"

**Storyline**:

1. `bicameral.bind` a decision: *"all monetary calculations use `Decimal`,
   never `float`"*. Show that the tool returns a region-id and a content hash.
2. `bicameral.search_decisions` for the keyword `"monetary"`. Show the just-bound
   decision returns at the top.
3. Edit the bound region: change `Decimal` to `float` in the linked file.
4. `bicameral.detect_drift`. Show that the region surfaces with status
   `drifted`.
5. Restore the file. Re-run. Status flips back to `reflected`.

**Value claim**: "Your decisions are now first-class artifacts — searchable,
hash-anchored, and drift-detected without you running anything by hand."

### Demo 02 — Commit-sync loop (post-commit hook → resolve_compliance)

**Path**: `docs/demos/02-commit-sync.md`
**Audience**: "How does this play with my actual git workflow?"

**Storyline**:

1. Show the post-commit hook installed (`.git/hooks/post-commit`) calling
   `bicameral-mcp link_commit HEAD`.
2. Edit a bound region. `git commit`.
3. Show the hook output: `bicameral: new commit detected`.
4. Show `_pending_compliance_checks` injected into the next tool response.
5. Walk through the `bicameral-sync` skill: read region → reason → batched
   `resolve_compliance(verdicts=[...])`.
6. Show the final ledger state: N reflected, N drifted, 0 pending.

**Value claim**: "Compliance is computed automatically on every commit, not
quarterly by a human auditor."

### Demo 03 — Continuity matcher: function rename auto-redirect (Phase 3)

**Path**: `docs/demos/03-continuity-rename.md`
**Audience**: "What happens when I refactor?"

**Storyline**:

1. Bind a decision to a function `calculate_tax_v1`.
2. Rename the function to `compute_tax`. Move it to a different file. Commit.
3. Naïvely: the binding would orphan and the decision would go `ungrounded`.
4. With `BICAMERAL_CODEGENOME_ENHANCE_DRIFT=1`: `link_commit` runs the
   continuity matcher pre-pass.
5. Show the response's `continuity_resolutions` list:
   `semantic_status: identity_renamed`, the binding redirected, no manual
   action needed.

**Value claim**: "Refactoring no longer breaks your decision graph. The matcher
recognises moved or renamed code and updates bindings automatically."

### Demo 04 — Cosmetic-vs-semantic drift classifier (Phase 4)

**Path**: `docs/demos/04-drift-classifier.md`
**Audience**: "Why does this not flag every whitespace change as drift?"

**Storyline**:

1. Bind a decision to a function. Capture the baseline ledger state.
2. **Cosmetic change**: re-format the docstring; re-order imports. Commit.
   Run `link_commit`. Show `auto_resolved_count: 1`, status flips to
   `compliant` with `semantic_status: semantically_preserved`. Zero LLM calls.
3. **Semantic change**: change the threshold inside the function from 100
   to 50. Commit. Run `link_commit`. Show the region appears in
   `pending_compliance_checks` with a `pre_classification` hint
   (`verdict: uncertain`, signals breakdown).
4. Walk through the LLM-side reasoning the `bicameral-sync` skill applies to
   issue the `drifted` verdict.
5. Show the M3 benchmark: 30 cases × 7 languages, 0% false-positive rate on
   the cosmetic-only set.

**Value claim**: "The classifier handles the easy 80% deterministically, leaves
only genuinely ambiguous cases for the LLM, and never costs you a token on a
docstring tweak."

### Authoring rules for new demos

- Run the demo end-to-end on a fresh clone before committing it. Demos that
  drift become anti-marketing.
- If the demo depends on a feature flag (`BICAMERAL_CODEGENOME_ENHANCE_DRIFT`,
  etc.), say so in **Prereqs**.
- If the demo records output, store the recording in `docs/demos/recordings/`
  next to the script. Keep recordings under 30 MB.
- Update the demo whenever the underlying tool's response shape changes —
  this is enforced under §9 (skill rule).

---

## 13. When in doubt

- **"Does this need a release PR?"** — If `main`'s SHA would change, yes.
- **"Should I close this issue?"** — `Closes #X` in the PR body, then yes
  (auto on dev-merge).
- **"Should I bump the version?"** — Only Jin bumps the version, only at
  release time.
- **"Can I commit a skill change separately from the tool change?"** — No.
  Same commit, same PR.
- **"Should I write a guide for this?"** — Use the §8 matrix. If the row says
  "yes", yes.
- **"Is this a hotfix or a feature?"** — Hotfix is for a regression on `main`
  that broke a user. Everything else is a feature.

---

**Owner**: Jin (release manager) + repo maintainers.
**Last reviewed**: 2026-04-29.
**Change protocol**: amendments require a META_LEDGER entry + a PR labeled
`docs:dev-cycle`.
