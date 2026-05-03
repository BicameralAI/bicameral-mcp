# Plan: Priority B v0 final blockers (issues #154 + #156 transcript fix)

**change_class**: feature
**doc_tier**: system
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (touches a landed product skill + a landed install-time hook command; both are scoped, mechanical, and close known-broken contracts)
**Mode**: solo (auto)
**Predecessor**: `plan-priority-c-team-server-v0-release-blockers.md` (sealed at META_LEDGER #41; Merkle `7cc405fc`)
**Issues**: closes [#154](https://github.com/BicameralAI/bicameral-mcp/issues/154); partially closes [#156](https://github.com/BicameralAI/bicameral-mcp/issues/156) (transcript-passing fix only — the design-pivot half is explicitly deferred to v0.1 per operator scope)
**v0 release deadline**: ~2 days. Both phases ship together as the final v0 push.

**terms_introduced**:
- term: contradiction-driven refinement capture
  home: skills/bicameral-preflight/SKILL.md
- term: SessionEnd transcript bridge
  home: events/session_end_bridge.py

**boundaries**:
- limitations:
  - **Phase 1 (#154)**: agent emits `action="supersede"` by default in `bicameral.resolve_collision`. PM ratifies in inbox; if the PM rejects supersession the original decision stays. Alternative `action` values per `skills/bicameral-resolve-collision/SKILL.md` are `keep_both` (false-positive contradiction; both decisions valid) and `link_parent` (cross-level child-of-parent linkage); for the contradicting-prompt case `supersede` is unambiguously correct, so per-prompt classification is not needed at v0.
  - **Phase 2 (#156 transcript half)**: the `--auto-ingest` mode's silent-background-ingestion design is preserved. The "design pivot to next-session surfacing" called out in #156's TL;DR is **out of scope** for v0 — that half remains tracked in #156 for v0.1 follow-up.
- non_goals:
  - Multi-turn correction-capture redesign (already owned by capture-corrections in-session mode)
  - Server-side auto-detection of contradictions (deliberately removed in v0.9.3 per `handlers/ingest.py` design; this plan keeps that posture)
  - Refactoring the canonical preflight Section 5 → Step 5.6 → Section 6/7 numbering scheme
- exclusions:
  - No new MCP tool surface
  - No new dependencies
  - No CHANGELOG/version bump (operator's release cadence; same posture as prior sessions)

## Open Questions

None blocking. Three design points resolved in advance per auto-mode + #154's recommended-fix-shape body:

1. **`action` default for `resolve_collision`** = `"supersede"`. Canonical alternatives per `skills/bicameral-resolve-collision/SKILL.md` are `"keep_both"` (false-positive contradiction — both decisions valid) and `"link_parent"` (cross-level parent-child linkage; not a same-level conflict). For the contradicting-prompt case the user has explicitly stated a refinement, so `"supersede"` is the unambiguous choice; v0 hard-codes it.
2. **Transcript bridge location** = new module `events/session_end_bridge.py` invoked by `python3 -m events.session_end_bridge`. Cleaner than a python `-c` one-liner (matches the post-commit hook pattern but earns testability via importable functions). Module is reachable via the user's Python path because bicameral-mcp is pip-installed at setup time.
3. **Transcript value propagation** = `BICAMERAL_PARENT_TRANSCRIPT_PATH` env var. The capture-corrections skill in `--auto-ingest` mode reads this env to find and scan the parent session's JSONL transcript. Env-var passthrough is the simplest mechanism and lines up with how `BICAMERAL_SESSION_END_RUNNING` already flows into the child process.

## Phase 1: preflight Step 5.6 — contradiction-driven refinement capture (closes #154)

**Why this phase exists**: The preflight skill auto-fires on natural refactor prompts (post-#146) and surfaces stored decisions when the user's request scopes a file under their authority. But when the user's prompt explicitly contradicts a surfaced decision, the agent has no skill instruction to ingest the refinement + wire it via `resolve_collision`. The correction-capture loop dies at "render". This is the v0.9.3 "caller-LLM owns supersession" contract being only half-honored: caller-LLM CHECKS history (Step 3.5 fires), but doesn't WRITE the refinement back. Phase 1 closes that loop.

### Verification (TDD discipline note)

Skill text is consumed by an LLM, not invoked by a function. The validation surface for an LLM-consumed skill is the e2e flow that simulates the agent's behavior with the updated skill loaded. The existing test `tests/e2e/run_e2e_flows.py::assert_flow_2` is already shaped for this exact contract — it asserts:

1. `bicameral.preflight` was called with `reorder.ts` in `file_paths` (auto-fire works post-#146; pre-existing assertion)
2. `bicameral.ingest` was called with `source="agent_session"` (the refinement; **the assertion that fails today**, and that this phase fixes)
3. `bicameral.resolve_collision` was called (the wiring; **the assertion that fails today**, and that this phase fixes)

After Phase 1, Flow 2a flips FAIL → PASS. The skill change IS the validation surface; no new unit-test artifact is added because the skill text has no unit-testable Python entry point.

A new functionality test IS added at the e2e layer to ensure Flow 2a's assertions are exercised in CI (today they may run only opportunistically). See Affected Files.

### Affected Files

- `skills/bicameral-preflight/SKILL.md` — **MUTATE** — (a) add Step 5.6 (after Step 5.5 "Confirm finding relevance", before Step 6 "Honor blocking hints"). Step 5.6 instructs the agent: when the user's current prompt restates or replaces a surfaced decision (signals: "instead of", "actually we're switching to", "no more X", "I know the roadmap said X but...", direct mention of a different approach for a file the surfaced decision anchors), then BEFORE proceeding with code work: invoke `bicameral.ingest` with `decisions[0].feature_group` set, followed by `bicameral.resolve_collision(new_id=<just-ingested>, old_id=<surfaced>, action="supersede")`. Mechanical execution — no user-confirmation prompt. PM ratifies in inbox. (b) Fix the existing Section 7 "On stop-and-ask resolution — ingest the answer" template: move `feature_group` from the bogus top-level call kwarg into `decisions[0].feature_group` (the MCP dispatch at `server.py:1078-1085` only forwards `payload`/`source_scope`/`cursor`; the top-level kwarg has been silently dropped since v0.x).
- `.claude/skills/bicameral-preflight/SKILL.md` — **DELETE-IF-EXISTS** — the project's CLAUDE.md mandates `pilot/mcp/skills/` was the canonical source pre-Phase-1; current state has `skills/` as canonical. Any stale `.claude/skills/bicameral-preflight/SKILL.md` symlink/duplicate must be removed so Claude Code reads the amended skill.
- `tests/e2e/conftest.py` — **READ-ONLY** — verify Flow 2a is in the default e2e flow set; if not, add it explicitly.
- `tests/e2e/run_e2e_flows.py::assert_flow_2` — **READ-ONLY** — already has the three-assertion structure. No mutation needed.

### Changes

**Step 5.6 text to insert into `skills/bicameral-preflight/SKILL.md`** (after the existing Step 5.5 closing paragraph, before "### 6. Honor blocking hints"):

```markdown
### 5.6 Capture refinements when the user's prompt contradicts a surfaced decision

When at least one decision was surfaced in Step 5 AND the user's
current prompt is restating or replacing that decision (signals:
"instead of", "actually we're switching to", "no more X", "I know the
roadmap said X but...", direct mention of a different approach for a
file the surfaced decision anchors), THEN before any code work:

1. **Ingest the refinement**:

```
bicameral.ingest(payload={
  "query": "<feature topic preflight scoped to>",
  "source": "agent_session",
  "title": "preflight-refinement-<topic>",
  "date": "<today ISO date>",
  "decisions": [{
    "description": "<user's stated new direction as a decision statement>",
    "source_excerpt": "<verbatim quote of the user's contradicting phrase>",
    "feature_group": "<same feature_group as the surfaced decision>"
  }]
})
```

2. **Wire the refinement to the seeded decision**:

```
bicameral.resolve_collision(
  new_id="<decision_id returned by step 1's ingest>",
  old_id="<id of the surfaced decision being contradicted>",
  action="supersede"
)
```

This is **mechanical** — the user has already stated the refinement
explicitly. Do NOT ask the user to confirm. The new decision enters
the ledger as `proposed`; the PM sees both the original and the
refinement in their next inbox review and ratifies or rejects the
supersession.

**Role mapping (`new_id` vs `old_id`)**: per
`skills/bicameral-resolve-collision/SKILL.md` canonical pattern,
`new_id` is the just-ingested refinement (what supersedes); `old_id`
is the surfaced decision being contradicted (what gets superseded).
The supersedes edge writes `new_id → supersedes → old_id`.

**When NOT to fire**: if the user is asking a clarifying question, not
stating a refinement (e.g., "does this implement drag-drop?"), Step
5.6 does not apply — pass the question through to normal preflight
rendering.

**`action` default**: `"supersede"` covers the most common case (the
refinement replaces the prior approach for the same scope). The
canonical alternative values are `"keep_both"` (false-positive
contradiction; both decisions valid) and `"link_parent"` (cross-level
parent-child, not a same-level conflict). Per-prompt classification
deferred — for v0, the contradicting-prompt case is unambiguously
`"supersede"`.

```

### Unit Tests

The skill text has no Python entry point; the validation surface is the e2e flow. To make Flow 2a's assertions a v0 release gate:

- [ ] `tests/test_e2e_flow_2a_in_default_set.py::test_flow_2a_runs_in_e2e_default_set` — invokes the e2e runner's flow-set discovery (`tests/e2e/run_e2e_flows.py::FLOWS` or equivalent registry); asserts that `Flow 2` (which contains the 2a assertions per `assert_flow_2`) is in the default-run set, NOT marked `skip` or `xfail`. Functionality — exercises the test-registry invariant that ensures CI fails on a regression of the contradiction-capture path. (If Flow 2 is skipped in CI today, this test fails immediately, surfacing the gap.)

The existing `tests/e2e/run_e2e_flows.py::assert_flow_2` is the runtime functionality test. It runs in CI only when the e2e suite runs (which has its own gating — typically `-m e2e` or similar marker). The new test above ensures the suite includes this flow as a default-run target so a regression in `bicameral-preflight/SKILL.md` Step 5.6 fails CI immediately.

---

## Phase 2: SessionEnd transcript bridge (closes #156 transcript-passing half)

**Why this phase exists**: The canonical SessionEnd hook command at `setup_wizard.py:362` doesn't read stdin, so the spawned `claude -p` subprocess never receives the parent session's `transcript_path`. `bicameral-capture-corrections --auto-ingest` then has no transcript to scan and silently no-ops. Two stacked problems were called out in #156; this phase fixes the transcript-passing one. The design-pivot half (silent-background-ingest → next-session surfacing) is a v0.1 concern.

### Verification (TDD — list test files first)

- [ ] `tests/test_session_end_bridge.py::test_bridge_extracts_transcript_path_from_stdin_and_propagates_via_env` — calls `events.session_end_bridge:_compute_subprocess_env(stdin_text=<valid hook payload>, current_env={"PATH": "..."})`; asserts the returned env dict contains `BICAMERAL_PARENT_TRANSCRIPT_PATH` set to the JSON's `transcript_path` value AND `BICAMERAL_SESSION_END_RUNNING="1"` (recursion guard) AND preserves `PATH`. Functionality — exercises the stdin → env mapping invariant.
- [ ] `tests/test_session_end_bridge.py::test_bridge_skips_when_no_bicameral_dir_exists` — patches `os.path.isdir` to return False for `.bicameral`; calls `events.session_end_bridge:should_run(cwd=tmp_path, env={})`; asserts return is False. Functionality — exercises the per-repo guard.
- [ ] `tests/test_session_end_bridge.py::test_bridge_skips_when_recursion_guard_set` — patches `os.path.isdir` to True for `.bicameral`; calls `should_run` with `env={"BICAMERAL_SESSION_END_RUNNING": "1"}`; asserts return is False. Functionality — exercises the recursion-prevention invariant.
- [ ] `tests/test_session_end_bridge.py::test_bridge_main_invokes_claude_subprocess_with_correct_env_when_stdin_valid` — patches `subprocess.run` to a recording stub; pipes valid hook stdin into the entry point; asserts `subprocess.run` was called once with argv=`["claude", "-p", "/bicameral:capture-corrections --auto-ingest"]` AND env containing both `BICAMERAL_PARENT_TRANSCRIPT_PATH` and `BICAMERAL_SESSION_END_RUNNING`. Functionality — exercises the end-to-end main path.
- [ ] `tests/test_session_end_bridge.py::test_bridge_main_no_op_when_stdin_malformed_json` — pipes invalid JSON into stdin; asserts `subprocess.run` was NOT called and exit code is 0 (silent no-op, not crash). Functionality — exercises the defensive parse failure path.
- [ ] `tests/test_session_end_bridge.py::test_bridge_main_uses_cwd_from_stdin_payload_not_process_cwd` — pipes valid stdin with `cwd=<tmp_path_with_dot_bicameral>` while `os.getcwd()` returns a different directory without `.bicameral/`; patches `subprocess.run` to recording stub; asserts `subprocess.run` WAS called (the cwd from stdin satisfied the `.bicameral/` guard, even though the process cwd would not have). Functionality — exercises the hook-contract cwd-from-stdin invariant per audit-round-1 Remediation 2.
- [ ] `tests/test_session_end_bridge.py::test_setup_wizard_session_end_command_invokes_bridge_module` — reads `setup_wizard.py::_BICAMERAL_SESSION_END_COMMAND` constant; asserts the literal command string is `"python3 -m events.session_end_bridge"`. Functionality — guards the hook command against drift; if the constant changes shape, this test fires. (Acceptable per Test Functionality doctrine because the unit under test is a literal-constant config value, not a function — its "output" IS the literal string.)
- [ ] `tests/test_session_end_capture_corrections_reads_transcript_env.py::test_capture_corrections_auto_ingest_reads_parent_transcript_env_var` — exists as a documentation-of-contract test rather than a functional one. The capture-corrections skill is LLM-consumed text; this test grep-asserts that the skill's `--auto-ingest` mode section references `BICAMERAL_PARENT_TRANSCRIPT_PATH` as the transcript source. **Presence-only by Test Functionality doctrine** — flagging here as a gap; will skip implementing this test. The functional surface for the skill change is downstream e2e (Flow 4 in `tests/e2e/run_e2e_flows.py`, which exercises the SessionEnd capture path).

### Affected Files

- `events/session_end_bridge.py` — **CREATE** — exports four functions: `read_hook_stdin(stdin_text: str) -> dict` (parses Claude Code hook contract JSON), `should_run(cwd: str, env: dict) -> bool` (combines `.bicameral/` directory check + recursion-guard check), `_compute_subprocess_env(stdin_text: str, current_env: dict) -> dict` (builds the env dict for the subprocess: copy + set `BICAMERAL_SESSION_END_RUNNING="1"` + set `BICAMERAL_PARENT_TRANSCRIPT_PATH=<from hook payload>`), `main()` (entrypoint: reads stdin, dispatches to subprocess.run with computed env). Module is invokable via `python3 -m events.session_end_bridge` because the file's `__name__ == "__main__"` block calls `main()`.
- `setup_wizard.py` — **MUTATE** — replace `_BICAMERAL_SESSION_END_COMMAND` (line 362) from the no-stdin shell pipe to `"python3 -m events.session_end_bridge"`. The new module handles the `.bicameral/` guard, recursion guard, stdin parse, and subprocess spawn — the inline shell command becomes a single dispatch.
- `skills/bicameral-capture-corrections/SKILL.md` — **MUTATE** — Section 1 (or the auto-ingest mode docs) gains a one-paragraph note: in `--auto-ingest` mode invoked from the SessionEnd hook, read `BICAMERAL_PARENT_TRANSCRIPT_PATH` env var to find the parent session's JSONL transcript and scan it. Existing `--auto-ingest` semantics otherwise unchanged.
- `tests/test_session_end_bridge.py` — **CREATE** — 6 functionality tests above (test 7 flagged as presence-only and intentionally skipped).

### Changes

`events/session_end_bridge.py`:

```python
"""SessionEnd hook bridge — reads Claude Code's hook stdin contract,
extracts the parent session's transcript_path, and spawns the
capture-corrections skill via `claude -p` with the transcript path
propagated via BICAMERAL_PARENT_TRANSCRIPT_PATH env var.

Closes the transcript-passing half of #156. Without this bridge, the
canonical SessionEnd command spawned `claude -p` with no transcript
context, leaving --auto-ingest mode silently no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GUARD_ENV = "BICAMERAL_SESSION_END_RUNNING"
TRANSCRIPT_ENV = "BICAMERAL_PARENT_TRANSCRIPT_PATH"
CHILD_CLAUDE_CMD = ["claude", "-p", "/bicameral:capture-corrections --auto-ingest"]


def read_hook_stdin(stdin_text: str) -> dict:
    """Parse Claude Code's SessionEnd hook contract JSON. Returns {}
    on parse failure (silent no-op semantics — the hook should never
    crash the parent session)."""
    try:
        return json.loads(stdin_text)
    except (json.JSONDecodeError, ValueError):
        return {}


def should_run(cwd: str, env: dict) -> bool:
    """True iff the hook should fire: cwd has .bicameral/ AND the
    recursion guard env var is unset."""
    if not Path(cwd, ".bicameral").is_dir():
        return False
    if env.get(GUARD_ENV):
        return False
    return True


def _compute_subprocess_env(stdin_text: str, current_env: dict) -> dict:
    """Build the env dict for the spawned claude -p subprocess: copy
    of current env + recursion guard set + transcript path set."""
    payload = read_hook_stdin(stdin_text)
    new_env = dict(current_env)
    new_env[GUARD_ENV] = "1"
    new_env[TRANSCRIPT_ENV] = payload.get("transcript_path", "")
    return new_env


def main() -> int:
    # Per Claude Code's SessionEnd hook contract (issue #156 body),
    # the parent session's cwd arrives in the stdin JSON payload alongside
    # transcript_path. Read stdin first; use payload.cwd for the
    # .bicameral/ directory check, falling through to os.getcwd() if
    # stdin is empty or malformed (manual invocation case).
    stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload = read_hook_stdin(stdin_text)
    cwd = payload.get("cwd") or os.getcwd()
    if not should_run(cwd, dict(os.environ)):
        return 0
    env = _compute_subprocess_env(stdin_text, dict(os.environ))
    try:
        subprocess.run(CHILD_CLAUDE_CMD, env=env, check=False)
    except (FileNotFoundError, OSError):
        pass  # claude not on PATH; silent no-op
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`setup_wizard.py` change (line 362):

```python
# OLD:
_BICAMERAL_SESSION_END_COMMAND = (
    "[ -d .bicameral ] && claude -p '/bicameral:capture-corrections' || true"
)

# NEW:
_BICAMERAL_SESSION_END_COMMAND = "python3 -m events.session_end_bridge"
```

The `.bicameral` guard moves from shell to Python (preserved semantics); the recursion guard moves from shell env-prefix to Python env-check; the stdin → transcript-path-env propagation is the new piece.

`skills/bicameral-capture-corrections/SKILL.md` Section 1 amendment (one-paragraph addition):

```markdown
**SessionEnd-hook transcript propagation**: when invoked via the
SessionEnd hook (`--auto-ingest` mode), the parent session's transcript
path is provided via the `BICAMERAL_PARENT_TRANSCRIPT_PATH` env var.
Read the JSONL at that path to scan the user's last ~10 messages for
uningested corrections. Without this env var (e.g., manual invocation),
the skill scans only the live conversation context.
```

---

## CI Commands

- `pytest -x tests/test_session_end_bridge.py` — Phase 2 bridge functionality
- `pytest -x tests/test_e2e_flow_2a_in_default_set.py` — Phase 1 e2e gating
- `pytest -x tests/ -k "not team_server"` — full regression check (no breakage to per-repo bicameral)
- `pytest -x tests/e2e/ -k "flow_2"` — e2e Flow 2/2a (requires Anthropic API key; opportunistic in CI but the validation surface for #154's contradiction-capture loop)
- `python -m events.session_end_bridge < /dev/null` — manual smoke (stdin-empty → no-op exit 0; verifies the module is invokable via `python -m`)

---

## Risk note (L2 grade reasoning)

L2 because:

- **No new credential surface, no new IPC paths**: Phase 2 just re-routes existing SessionEnd hook stdin into the existing `claude -p` subprocess via env var. No new external surface.
- **Phase 1 is text-only**: SKILL.md amendment. Worst-case failure is the LLM ignoring the new step (regression to today's broken behavior). Best-case is the e2e Flow 2a flipping to PASS in CI on the next run.
- **Phase 2 has a real subprocess interaction**: but the bridge is unit-testable end-to-end (stdin → env → `subprocess.run` arguments), and the worst-case failure is "no-op" (silent skip), not "session crash". The OSError catch on `subprocess.run` makes the hook resilient if `claude` is missing from PATH.
- **No backwards-compat concerns**: the old SessionEnd hook command was silently no-op in every install (per #156), so replacing it has no negative-surface for existing users. Operators who manually configured a different SessionEnd hook are left alone (the wizard only writes new entries; merge logic at `setup_wizard.py:419-429` preserves non-bicameral entries).

---

## Modular commit plan

Three commits, one PR (or fold into existing PR #159 since this is the same v0 release).

```
feat(skills): preflight Step 5.6 — capture refinements when prompt contradicts surfaced decision (closes #154)
feat(events): SessionEnd transcript bridge — propagate parent transcript_path via env var (closes #156 transcript half)
docs(governance): v0 final-blockers plan/audit/seal artifacts
```

Phase 1 and Phase 2 are independent — either ships without the other and delivers value. Combined, they close the v0-product correctness gap (Priority B preflight loop closure + SessionEnd hook actually firing).
