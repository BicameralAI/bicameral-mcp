# Plan: CI lint for unstructured references in plan files and PR bodies (Issue #114)

**Tracks**: BicameralAI/bicameral-mcp#114 — *CI lint for unstructured references in plan files and PR bodies*
**Targets**: v0.18.x (Jin's call at release-PR time)
**Branch**: `feat/114-grounding-lint` (off `BicameralAI/dev`, current tip `2e9a842` — post-#117 pre-push hook)
**Risk grade**: L1 — pure checker scripts + advisory CI workflow; no production code paths, no schema migrations, no MCP tool changes, no contract changes.
**Change class**: minor (additive lint scripts + new CI step + new advisory workflow + DEV_CYCLE.md docs).

---

## Open Questions

These are decisions worth flagging for audit; the plan proposes provisional answers.

### Q1. Pre-commit hook + CI, or CI only?

Issue body asks for both. Pre-commit requires `.pre-commit-config.yaml` infrastructure that does not currently exist in the repo (verified via `ls .pre-commit-config.yaml` → missing). Bootstrapping the pre-commit framework is its own concern with its own quirks (per-file vs per-commit, hook installation flow, contributor onboarding burden).

**Recommend CI-only for v1.** A pre-commit hook is a small follow-up issue once the CI checkers prove themselves. The CI run is the canonical gate; pre-commit is just earlier feedback for the same checks.

### Q2. Check A — what's a "registered top-level package"?

Two options:

- **Static list** of known packages (`adapters/`, `cli/`, `code_locator/`, `codegenome/`, `dashboard/`, `events/`, `governance/`, `handlers/`, `ledger/`) — drifts when packages are added.
- **Dynamic discovery** via `ls -d */` filtered by `__init__.py` presence — adapts automatically.

**Recommend dynamic discovery** — every new top-level package gets `__init__.py`, so the lint stays current without manual maintenance.

### Q3. Check B — block or warn?

Issue body says "warn (not block) when bare `#NUMBER` mentions appear in prose without one of those wrappings."

**Recommend warn (advisory check, not failing).** Bare `#NUMBER` mentions are sometimes legitimate (e.g., a release-notes paragraph that names every closed issue without `Closes` keywords because they were already closed). Hard-blocking creates churn; warning surfaces the smell without forcing action.

### Q4. What counts as a "linked-issue keyword"?

Standardised set, case-insensitive: `Closes`, `Fixes`, `Resolves` (GitHub auto-close), plus `Refs`, `Refs PR`, `Related to`, `Related`, `See` (advisory linking). Configurable via the script's argparse, hard-coded list for v1.

### Q5. Where do the scripts live?

- **Check A** (used both locally as a dev utility AND from CI): `scripts/lint_plan_grounding.py` — `scripts/` already exists for dev utilities (currently `sim_accountable.py`).
- **Check B** (CI-only, reads PR-body from GitHub Actions context): `.github/scripts/lint_pr_body_refs.py` — `.github/scripts/` already exists from PR #113 (`post_drift_comment.py`).

**Recommend the asymmetry**: dev-utility script in `scripts/`; CI-only script in `.github/scripts/`. Mirrors the existing convention.

### Q6. Does Check A interact with audit's grounding pass?

The `/qor-audit` skill already runs grounding manually. Does Check A duplicate that work?

**No** — they overlap but don't compete. CI lint is a fast pre-audit check (no SurrealDB, no LLM); audit's grounding is deeper (verifies API references, contract shapes, function signatures). Check A catches the easy 80% of SG-PLAN-GROUNDING-DRIFT instances earlier, freeing audit attention for harder cases.

---

## Background (grounding — verified against `dev` HEAD `2e9a842`)

- Top-level packages: `adapters/`, `assets/`, `classify/`, `cli/`, `code_locator/`, `codegenome/`, `dashboard/`, `docs/`, `events/`, `governance/`, `handlers/`, `ledger/`, `scripts/`, `skills/`, `tests/`, `thoughts/`. Verified via `ls -d */`. (Avoids SG-PLAN-GROUNDING-DRIFT instance #5.)
- `.github/workflows/`: `drift-report.yml`, `label-merged-to-dev.yml`, `lint-and-typecheck.yml`, `preflight-eval.yml`, `publish.yml`, `secret-scan.yml`, `test-mcp-regression.yml`, `test-schema-persistence.yml`. Lint workflow runs `ruff check .` + `ruff format --check .` + `mypy .` on PRs to `main` and `dev`.
- No `.pre-commit-config.yaml` exists.
- `scripts/` exists at repo root with `sim_accountable.py` and `CLAUDE.md`.
- `.github/scripts/post_drift_comment.py` (from PR #113) is the precedent for CI-only Python helpers — stdlib-only, no new runtime deps.
- `cli/` is for user-facing console tools (`classify`, `branch_scan`, `drift_report`); not the right home for a lint script.

---

## Phase 0: Check A — plan-grounding lint

TDD-light: tests written FIRST, confirm red, then implement, confirm green.

### Affected files

- `tests/test_lint_plan_grounding.py` — **new**, ~120 LOC, 8 tests covering path detection, exemption rules, suggested-correction output.
- `scripts/lint_plan_grounding.py` — **new**, ~140 LOC. Standalone Python script (no project imports) that walks plan files, classifies tokens, emits diagnostics.

### Public interface

```python
# scripts/lint_plan_grounding.py

def lint_plan_file(path: pathlib.Path, repo_root: pathlib.Path) -> list[Diagnostic]:
    """Walk a plan-*.md file, find filesystem-shaped path tokens, verify
    each against the working tree (or the documented "new" exemption).

    Returns a list of Diagnostic records. Empty list = clean.
    Pure function: no IO except reading the plan file and stat-ing
    candidate paths. No git, no network."""


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Walks `plan-*.md` and `docs/Planning/plan-*.md`,
    runs lint_plan_file on each, prints diagnostics, returns 0 if
    clean, 1 if any plan has unresolved paths."""
```

### Diagnostic shape

```python
@dataclasses.dataclass(frozen=True)
class Diagnostic:
    path: pathlib.Path     # plan file
    line: int              # 1-indexed line in plan
    token: str             # the candidate path that didn't resolve
    suggestion: str | None # nearest-match guess from the registered packages
```

### Output format

```
plan-foo.md:42: 'bicameral/drift_report.py' does not exist
                did you mean 'cli/drift_report.py'? (registered packages: cli/, codegenome/, handlers/, ...)
```

### Token detection rules

A token is a lint candidate when ALL hold:

1. Wrapped in backticks (` `…` `) or inside a fenced code block.
2. Contains at least one `/` (filesystem-shape).
3. Ends in a known extension (`.py`, `.yaml`, `.yml`, `.md`, `.json`, `.toml`, `.sh`, `.ts`, `.tsx`) OR has no extension AND matches `r"^[a-z_]+/$"` (package directory).
4. NOT preceded by an explicit "new" / "**new**" / "(new)" marker on the same Markdown bullet line.

Token NOT a candidate when:
- Inside a `<!-- ... -->` HTML comment.
- Inside an indented quote block (`>` prefix).
- Followed by `(planned)` / `(future)` / `(v2)`.

### Unit tests (Phase 0)

- `tests/test_lint_plan_grounding.py`:
  - `test_clean_plan_emits_no_diagnostics` — synthetic plan with only existing paths → empty list.
  - `test_nonexistent_path_emits_diagnostic` — synthetic plan referencing `bicameral/foo.py` (nonexistent) → 1 diagnostic with line + token.
  - `test_new_marker_exempts_path` — plan with `**new**` marker on the same line → no diagnostic.
  - `test_planned_suffix_exempts_path` — plan with `(planned)` suffix → no diagnostic.
  - `test_html_comment_skipped` — path inside `<!-- ... -->` block → no diagnostic.
  - `test_suggestion_for_misspelled_package` — `bicameral/drift_report.py` → suggests `cli/drift_report.py`.
  - `test_main_exits_zero_when_all_clean` — `main()` against a clean fixture set → returncode 0.
  - `test_main_exits_one_when_diagnostics` — `main()` against a fixture with one bad path → returncode 1.

### Function-level razor

- `lint_plan_file` ≤ 30 LOC (orchestrator).
- `main()` ≤ 25 LOC.
- Helpers: `_extract_path_tokens(text)` ≤ 25 LOC, `_is_exempt(token, line)` ≤ 20 LOC, `_resolve_or_suggest(token, repo_root)` ≤ 25 LOC.

---

## Phase 1: Check B — PR-body refs lint

TDD-light: tests written FIRST, confirm red, then implement, confirm green.

### Affected files

- `tests/test_lint_pr_body_refs.py` — **new**, ~90 LOC, 5 tests covering keyword recognition, bare-mention warnings, edge cases.
- `.github/scripts/lint_pr_body_refs.py` — **new**, ~100 LOC. Stdlib-only checker that consumes a PR body via `--body` (path) or stdin, emits warnings for bare `#NUMBER` mentions.

### Public interface

```python
# .github/scripts/lint_pr_body_refs.py

def lint_pr_body(body: str) -> list[Warning]:
    """Walk a PR body's lines. For each `#NUMBER` token, classify as:
      - structured (under 'Linked issues' header OR preceded by a recognised keyword)
      - bare (warning emitted)
    Returns warnings as Warning records. Pure function, no IO."""


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Reads --body file (or stdin if no flag), runs
    lint_pr_body, prints warnings to stderr. Always returns 0
    (advisory check; never blocks merge). The CI workflow can
    inspect printed warnings via `gh pr review` or comment posting
    if desired in v2."""
```

### Recognised keywords (case-insensitive)

`Closes`, `Closed`, `Fixes`, `Fixed`, `Resolves`, `Resolved`, `Refs`, `Refs PR`, `Related to`, `Related`, `See`.

### Linked-issues section detection

A section is detected when a Markdown heading (`#`/`##`/`###`) matches `r"^\s*#{1,6}\s+linked\s+issues?\s*$"` (case-insensitive). Tokens within that section's body (until the next heading) are exempt from bare-mention warnings.

### Output format

```
warning: bare '#108' on line 12 — wrap with 'Closes #108' / 'Refs #108', or move to a 'Linked issues' section
```

### Unit tests (Phase 1)

- `tests/test_lint_pr_body_refs.py`:
  - `test_closes_keyword_recognised` — body with `Closes #42` → no warnings.
  - `test_refs_keyword_recognised` — body with `Refs #42` → no warnings.
  - `test_bare_mention_in_prose_warns` — body with `Phase 1 (#42):` → 1 warning.
  - `test_linked_issues_section_exempts_bare_mentions` — body with `## Linked issues\n\n- #42` (bare under the section) → no warnings.
  - `test_main_always_returns_zero` — even with warnings, exit code 0 (advisory).

### Function-level razor

- `lint_pr_body` ≤ 30 LOC.
- `main()` ≤ 20 LOC.
- Helpers: `_classify_token(line, ctx)` ≤ 20 LOC, `_is_in_linked_issues_section(line, prev_headings)` ≤ 15 LOC.

---

## Phase 2: CI integration

TDD-light: this phase has no new tests — it's CI plumbing. Phase 0 and 1 tests prove the checkers work; Phase 2 just wires them in.

### Affected files

- `.github/workflows/lint-and-typecheck.yml` — **modify**, +6 LOC. Add a step running `python scripts/lint_plan_grounding.py` after the existing `mypy` step.
- `.github/workflows/pr-body-refs-lint.yml` — **new**, ~30 LOC. Advisory workflow that runs Check B on PR open/edit.

### `lint-and-typecheck.yml` modification

```yaml
      - name: Plan-grounding lint (#114 Check A)
        run: python scripts/lint_plan_grounding.py
```

This blocks merge on plan-grounding violations (consistent with `ruff check`'s blocking semantics).

### New `pr-body-refs-lint.yml` workflow

```yaml
name: PR body refs lint

on:
  pull_request:
    types: [opened, edited, reopened]

permissions:
  pull-requests: read

jobs:
  lint:
    runs-on: ubuntu-latest
    continue-on-error: true   # advisory; never blocks merge
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - name: Run lint
        env:
          PR_BODY: ${{ github.event.pull_request.body }}
        run: |
          echo "$PR_BODY" > /tmp/pr-body.md
          python .github/scripts/lint_pr_body_refs.py --body /tmp/pr-body.md
```

`continue-on-error: true` is intentional — Check B is advisory.

---

## Phase 3: Documentation

TDD-light: this phase has no tests — it's pure documentation.

### Affected files

- `docs/DEV_CYCLE.md` — **modify**, +~30 LOC across two sections.
- `CHANGELOG.md` — **modify**, `[Unreleased]` entry under Added.

### `DEV_CYCLE.md` updates

Section §2.1 (issue creation, grounding-protocol references) — add:

```markdown
> **CI grounding lint (Issue #114)**: every plan committed to this repo
> runs through `scripts/lint_plan_grounding.py` in the lint workflow.
> The lint rejects plan files that reference filesystem paths not
> present on the working tree, unless the path is marked **new** /
> **(planned)** on its bullet. Author-time `ls -d */` is faster
> feedback; CI is the durable gate.
```

Section §4.3 (PR body required sections) — add:

```markdown
> **PR-body issue references**: every PR body that mentions `#NUMBER`
> tokens should wrap them with `Closes`/`Fixes`/`Resolves` (full
> closure) or `Refs` (related/partial) keywords, OR place them under
> a `## Linked issues` heading. The advisory workflow
> `pr-body-refs-lint.yml` warns (does not block) when bare mentions
> appear in prose. Issue #114.
```

### CHANGELOG entry

```markdown
## [Unreleased]

### Added

- **CI grounding lint for plan files and PR bodies (#114).** Two new
  checkers: `scripts/lint_plan_grounding.py` (filesystem-path
  references in `plan-*.md`, blocks merge on unresolved paths) and
  `.github/scripts/lint_pr_body_refs.py` (bare `#NUMBER` mentions in
  PR bodies, advisory only). Plan-grounding check folds into the
  existing `lint-and-typecheck.yml` workflow; PR-body check runs as a
  new advisory workflow `pr-body-refs-lint.yml`. Closes the
  SG-PLAN-GROUNDING-DRIFT loop after three instances this session.
```

---

## Test invocation

```bash
# Phase 0 + 1
python -m pytest -q tests/test_lint_plan_grounding.py tests/test_lint_pr_body_refs.py

# Run the linters manually (dev workflow)
python scripts/lint_plan_grounding.py
echo "Closes #42" | python .github/scripts/lint_pr_body_refs.py --body /dev/stdin

# CI gates these run on every PR (lint-and-typecheck.yml + pr-body-refs-lint.yml)
ruff check scripts/lint_plan_grounding.py .github/scripts/lint_pr_body_refs.py tests/test_lint_*.py
ruff format --check scripts/lint_plan_grounding.py .github/scripts/lint_pr_body_refs.py tests/test_lint_*.py
mypy scripts/lint_plan_grounding.py
```

---

## Section 4 razor pre-check

| File | Estimate | Razor cap | OK? |
|---|---|---|---|
| `scripts/lint_plan_grounding.py` | ~140 LOC | ≤250 | yes |
| `.github/scripts/lint_pr_body_refs.py` | ~100 LOC | ≤250 | yes |
| `tests/test_lint_plan_grounding.py` | ~120 LOC | ≤250 | yes |
| `tests/test_lint_pr_body_refs.py` | ~90 LOC | ≤250 | yes |
| `pr-body-refs-lint.yml` | ~30 LOC | n/a (YAML) | n/a |

Function-level: every new function ≤ 30 LOC entry / ≤ 25 LOC helpers / nesting ≤ 3 / no nested ternaries.

---

## Exit criteria

1. **Phase 0 GREEN**: 8/8 plan-grounding tests pass; `ruff check` + `format --check` + `mypy` clean.
2. **Phase 1 GREEN**: 5/5 PR-body-refs tests pass; ruff/format clean.
3. **Phase 2 wired**: lint-and-typecheck.yml runs the plan-grounding step on this PR; pr-body-refs-lint.yml workflow registered with GitHub Actions and runs on this PR.
4. **Phase 3 documented**: DEV_CYCLE.md §2.1 and §4.3 carry the lint references; CHANGELOG `[Unreleased]` entry committed.
5. **Self-test on this very PR**: the plan-grounding check runs against `plan-114-grounding-lint.md` itself and emits zero diagnostics. The PR-body lint runs against this PR's body and emits zero warnings (PR description will be authored with proper `## Linked issues` block).

---

## What this plan is NOT

- Not a pre-commit hook (deferred to a follow-up issue if CI proves the checkers).
- Not an auto-fix tool — surfaces violations, doesn't rewrite plans or PR bodies.
- Not an API/contract grounding lint — Check A only verifies filesystem paths. API/contract verification stays with `/qor-audit`'s deeper grounding pass.
- Not a hard-block on PR-body warnings — Check B is advisory by design.
