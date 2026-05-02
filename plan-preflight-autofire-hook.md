# Plan: Preflight Auto-Fire Hook (#146)

**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (modifies agent behavior on every user prompt; misfire/missed-fire footprint analyzed below)
**Mode**: solo (codex-plugin declared unavailable)
**Predecessor chain**: META_LEDGER Entry #10 seal `186b045e`
**Issue**: [BicameralAI/bicameral-mcp#146](https://github.com/BicameralAI/bicameral-mcp/issues/146)
**Scope**: Resolve Flow 2 e2e failure — `bicameral.preflight` does not auto-fire on natural refactor prompts in headless `claude -p`. Solution: deterministic `UserPromptSubmit` hook injecting an authoritative reminder when implementation intent is detected. SKILL.md description has plateaued; hook adds the missing priority signal.

**Step 0 gate-check note**: No `research.json` artifact exists for this plan. The issue body is itself a research-grade analysis (failure mode narrowed across three e2e iterations, with explicit out-of-scope and acceptance criteria). Treating issue #146 as research substrate; gate override logged.

## Open Questions

None. Design fully closed during planning dialogue:
- Strategy: reminder injection (declarative, non-coercive)
- Detection: regex over verb list (deterministic)
- Form: separate Python script with shared verb-list data
- Skip-list: minimal in hook; LLM is second filter
- v0 verb-list configurability: fixed value (UI-configurable surface deferred post-v0)

---

## Phase 1: Intent classifier (data + pure function + unit tests)

### Verification (TDD)

Tests written **first**, before classifier impl, per Hickey razor.

- [ ] `tests/test_preflight_intent.py::test_fires_on_implementation_verbs` — every verb in the canonical list (>= 30) produces `should_fire_preflight(...) is True` when used in a natural sentence
- [ ] `tests/test_preflight_intent.py::test_skips_on_doc_only_prompts` — `"fix the typo in README"`, `"bump lodash to 4.17.21"`, `"how does the rate limiter work?"` all return `False`
- [ ] `tests/test_preflight_intent.py::test_fires_on_indirect_intent` — `"how should I implement the retry logic?"`, `"continue what we started yesterday on the email queue"` return `True`
- [ ] `tests/test_preflight_intent.py::test_data_is_loadable` — `IMPLEMENTATION_VERBS` and `SKIP_PATTERNS` import cleanly; both are non-empty; values are strings
- [ ] `tests/test_preflight_intent.py::test_natural_contradiction_prompt` — the exact prompt from `tests/e2e/prompts/flow-2-preflight.md` referenced in #146 (`"I know the roadmap said drag-and-drop to reorder commits, but actually we're switching to a text-editor approach…"`) returns `True`

### Affected Files

- `scripts/hooks/__init__.py` — **CREATE** — empty marker
- `scripts/hooks/preflight_intent.py` — **CREATE** — single source of truth for intent classification
- `tests/test_preflight_intent.py` — **CREATE** — unit tests above

### Changes

`scripts/hooks/preflight_intent.py` exports three names:

```python
"""Preflight intent classifier — single source of truth for the verb
list used by both the SKILL.md auto-fire description and the
UserPromptSubmit hook. Kept deterministic: no LLM, no network, no I/O
beyond a single string scan."""

from __future__ import annotations
import re

IMPLEMENTATION_VERBS: frozenset[str] = frozenset({
    "add", "build", "create", "implement", "modify", "refactor",
    "update", "fix", "change", "write", "edit", "move", "rename",
    "remove", "delete", "extract", "convert", "integrate", "deploy",
    "ship", "configure", "connect", "extend", "migrate", "wire",
    "hook up", "set up", "complete", "finish", "continue",
})

# Phrases that indicate code intent without a verb match. Lower-cased.
INDIRECT_INTENT_PHRASES: tuple[str, ...] = (
    "how should i implement",
    "how do i build",
    "how should i write",
    "what's the best way to add",
    "what's the cleanest way to refactor",
)

# Narrow skip-list: prompts the hook should NOT fire on even when a verb
# is present. The LLM is the second filter; keep this list small to
# avoid complecting the hook with the SKILL.md skip rules.
SKIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfix\b.*\btypo\b", re.IGNORECASE),
    re.compile(r"\bbump\b.*\b(?:to|from)\b.*\d+\.\d+", re.IGNORECASE),
    re.compile(r"\bhow does\b", re.IGNORECASE),  # purely descriptive
)

_VERB_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in IMPLEMENTATION_VERBS) + r")\b",
    re.IGNORECASE,
)


def should_fire_preflight(prompt: str) -> bool:
    """Return True iff prompt indicates code-implementation intent."""
    if not prompt or not prompt.strip():
        return False
    for skip in SKIP_PATTERNS:
        if skip.search(prompt):
            return False
    if _VERB_REGEX.search(prompt):
        return True
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in INDIRECT_INTENT_PHRASES)
```

Function fits Section 4 razor: 8 lines, depth 2, no nested ternaries.

---

## Phase 2: UserPromptSubmit hook entry point

### Verification (TDD)

- [ ] `tests/test_preflight_hook.py::test_emits_additional_context_on_match` — feeding a fire-worthy prompt to `scripts/hooks/preflight_reminder.py` via stdin produces a JSON response with `additionalContext` containing the literal string `"<system-reminder>"` and the directive `"call bicameral.preflight"`
- [ ] `tests/test_preflight_hook.py::test_emits_empty_on_no_match` — feeding a skip-worthy prompt produces an empty JSON response (`{}` or no `additionalContext` key)
- [ ] `tests/test_preflight_hook.py::test_handles_malformed_stdin` — non-JSON stdin returns exit 0 with empty response (never blocks the user)
- [ ] `tests/test_preflight_hook.py::test_idempotent_on_double_fire` — running the hook twice on the same prompt produces the same output (no state leak)

### Affected Files

- `scripts/hooks/preflight_reminder.py` — **CREATE** — UserPromptSubmit hook entry point
- `tests/test_preflight_hook.py` — **CREATE** — unit tests above

### Changes

`scripts/hooks/preflight_reminder.py`:

```python
"""UserPromptSubmit hook for Claude Code: when the user prompt indicates
code-implementation intent, inject a system-reminder that elevates
bicameral.preflight above the agent's default tool-selection priority.

Per Claude Code hook contract: read JSON from stdin, write JSON to
stdout. additionalContext is appended to the prompt before the LLM
sees it. Errors are swallowed silently (exit 0, empty response) so a
broken hook never blocks a user."""

from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.preflight_intent import should_fire_preflight

REMINDER_TEXT = (
    "<system-reminder>\n"
    "This prompt indicates code-implementation intent. Before invoking "
    "any file-inspection tool (Read, Grep, Bash, Glob), call "
    "`bicameral.preflight` to surface prior decisions, drifted regions, "
    "and open questions for the affected area. The skill produces zero "
    "output when nothing relevant is found, so the cost of firing is "
    "bounded. Auto-fire is the contract; do not skip.\n"
    "</system-reminder>"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = payload.get("prompt", "") if isinstance(payload, dict) else ""
    if should_fire_preflight(prompt):
        json.dump({"additionalContext": REMINDER_TEXT}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Function fits Section 4 razor: `main()` 9 lines, depth 2.

---

## Phase 3: Wire the hook into Claude Code + SKILL.md addendum

### Verification (TDD)

- [ ] `.claude/settings.json` parses as valid JSON
- [ ] `.claude/settings.json` `hooks.UserPromptSubmit[0].hooks[0].command` invokes `scripts/hooks/preflight_reminder.py`
- [ ] `python scripts/hooks/preflight_reminder.py < tests/fixtures/flow2_prompt.json` produces JSON with `additionalContext` containing the directive (manual smoke test, then encoded as a CI step)
- [ ] `skills/bicameral-preflight/SKILL.md` has a new short section under "When to fire" referencing the hook as the second-stage reinforcement, and pointing at `scripts/hooks/preflight_intent.py` as the canonical verb list source

### Affected Files

- `.claude/settings.json` — **MUTATE** — append `UserPromptSubmit` hook entry
- `skills/bicameral-preflight/SKILL.md` — **MUTATE** — add hook-reinforcement note
- `tests/fixtures/flow2_prompt.json` — **CREATE** — pinned fixture with the natural-contradiction prompt from #146 acceptance, used in Phase 2 tests + manual smoke test

### Changes

Append to `.claude/settings.json` `hooks` object:

```jsonc
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 scripts/hooks/preflight_reminder.py"
      }
    ]
  }
]
```

Append to `skills/bicameral-preflight/SKILL.md` under the "When to fire" section a single short subsection:

```markdown
### Hook reinforcement

The trigger described above is reinforced by a `UserPromptSubmit` hook
configured in `.claude/settings.json`. The hook reads the user prompt,
matches against the canonical verb list in
`scripts/hooks/preflight_intent.py`, and injects a `<system-reminder>`
elevating preflight's tool-selection priority. SKILL.md description
and hook intent classifier share the same verb list — edit
`preflight_intent.py` to evolve the trigger surface.
```

`tests/fixtures/flow2_prompt.json`:

```json
{
  "prompt": "I know the roadmap said drag-and-drop to reorder commits, but actually we're switching to a text-editor approach. Please update cherry-pick.ts and reorder.ts."
}
```

---

## CI commands

```bash
# Phase 1 + 2 unit tests (deterministic; must pass on every PR)
pytest -x tests/test_preflight_intent.py tests/test_preflight_hook.py

# Phase 3 settings.json validity
python -m json.tool .claude/settings.json > /dev/null

# Manual smoke test (encoded as CI step)
python3 scripts/hooks/preflight_reminder.py < tests/fixtures/flow2_prompt.json | python -c "import json,sys; r=json.load(sys.stdin); assert 'additionalContext' in r and 'bicameral.preflight' in r['additionalContext'], r; print('OK')"

# Authoritative integration test (lives on dev branch; cited for traceability)
# pytest tests/e2e/run_e2e_flows.py::test_flow_2  # runs in dev-branch CI
```

## Risk note (L2 grade reasoning)

- **Misfire footprint**: a false-positive hook fires preflight on a non-code prompt. Cost: one MCP round-trip; preflight is gated to silence on no-match. Bounded.
- **Missed-fire footprint**: a false-negative leaves us where we are today (Flow 2 fails). Equal to current baseline; no regression possible.
- **Per-prompt latency**: regex scan of a string. Sub-millisecond; below noise floor.
- **Failure isolation**: hook errors return exit 0 with empty response — never blocks the user. The handler is ergonomically defensive by design.

The L2 grade is from blast radius (every prompt) not from individual-action risk (small, bounded, reversible). This warrants `/qor-audit` review before implementation.
