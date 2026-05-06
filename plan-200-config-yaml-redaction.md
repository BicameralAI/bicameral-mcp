# Plan: bicameral-report-bug — `.bicameral/config.yaml` keys-only by default (#200 A4) (example)

**change_class**: feature

**doc_tier**: minimal

## Open Questions

None. Scope and design are framed by #200's A4 finding and the user's directive: "transparency + accuracy + minimum data shared." PR #201 (just merged) closed A1 (python3 portability) and A6 (browser-open). Partial coverage of A7 (rationale-field drop). This plan addresses only the remaining A4 surface (`.bicameral/config.yaml` verbatim leak). (example)

## Phase 1: Default config.yaml inclusion to keys-only with explicit verbatim toggle

The current Step 2 instruction (post-#201) says: *"`.bicameral/config.yaml`: use `Read` on `.bicameral/config.yaml` if it exists. If `Read` errors (file missing), skip the section."* The Step 3 body assembly then dumps `<contents>` verbatim into the issue body. This violates the "minimum data shared" directive — config.yaml routinely contains team-server tokens, workspace IDs, allowlists, and environment-specific settings whose values aren't needed for diagnosing most bugs but whose presence is. (example)

Phase 1 changes Step 2's instruction so the default included shape is the YAML's *key structure* (top-level keys, no values), with an explicit opt-in toggle exposed in Step 3.5's transparency preview that lets the user elect to include the verbatim contents when the bug genuinely needs them (e.g., a parser bug in config loading).

### Affected Files

- `skills/bicameral-report-bug/SKILL.md` — three edits, all on the existing skill markdown:
  1. **Step 2 §"`.bicameral/config.yaml`"** instruction: replace the "use Read on .bicameral/config.yaml" line with the keys-only extraction rule (Read the file, parse the YAML or scan top-level non-indented keys, emit only the key list as a sorted bulleted block). (example)
  2. **Step 3 body-assembly template**: replace the verbatim ```yaml <contents> ``` block in the assembled markdown with the keys-only block. Add a sentinel-line `(values redacted by default — opt in via Step 3.5 to include verbatim)`.
  3. **Step 3.5 transparency preview**: add the `.bicameral/config.yaml` toggle to the operator-facing question. The toggle defaults to "keys only" and the operator can flip it to "include verbatim" if the bug needs the values. Update the redaction-summary block to print which mode was chosen (keys-only / verbatim) so the operator sees the choice in the preview. (example)

### Changes

**Step 2 §config.yaml** — replace the existing line with:

```markdown
- **`.bicameral/config.yaml`**: use `Read` on `.bicameral/config.yaml` if it exists. Extract ONLY the top-level key structure by default — every top-level YAML key (lines that don't start with whitespace), one per line, sorted alphabetically. Do NOT include values, nested keys, comments, or any other content. The keys-only shape is sufficient diagnostic signal for *"is this bug in the config loader?"* questions while leaking zero workspace IDs, tokens, or environment-specific settings. If the operator's bug genuinely needs the verbatim contents (e.g. a YAML parser regression), Step 3.5's transparency preview offers an explicit opt-in toggle. If `Read` errors (file missing), skip the section entirely. (example)
```

**Step 3 body-assembly template** — replace the existing ```yaml <contents> ``` block with the keys-only shape:

```markdown
  ## .bicameral/config.yaml   ← only if Read succeeded

  ```
  <sorted top-level key list, one per line, no values>
  ```
  *(values redacted by default — opt in via Step 3.5 transparency preview to include verbatim)*
  ```
```

**Step 3.5 transparency preview** — extend the operator-facing question to include a config.yaml verbosity toggle. The exact `AskUserQuestion` shape:

```python
AskUserQuestion({
  questions: [{
    question: "Open the prefilled GitHub issue?",
    header: "Open issue",
    multiSelect: false,
    options: [
      { label: "Yes, open it (config.yaml: keys only)",
        description: "Default — config.yaml top-level keys included, values redacted" },
      { label: "Yes, but include config.yaml verbatim",
        description: "Use only when the bug requires inspecting config values; values still pass the secret-redaction regex but workspace IDs / tokens / allowlists are exposed" },
      { label: "Edit the body first",
        description: "I want to revise the body in chat before opening" },
      { label: "Cancel",
        description: "Don't open anything; nothing leaves the machine" }
    ]
  }]
})
```

The redaction-summary block printed before the question is updated to include the explicit current choice:

```
Auto-redacted in this body:
  - ...existing redactions...
  - .bicameral/config.yaml: keys only by default (toggle below to include verbatim)
```

When the operator picks "Yes, but include config.yaml verbatim", regenerate the body with the verbatim ```yaml <contents> ``` block (the existing pre-#200 shape, kept available for the opt-in path). Re-display the preview with verbatim contents and re-ask the open-issue question one more time so the operator sees what's actually being shipped before clicking through. The secret-redaction regex (`(api[_-]?key|token|secret|password|bearer)\s*[=:]\s*\S+`) still runs on verbatim contents — this is defense-in-depth, not a substitute for the keys-only default.

### Unit Tests

None for this phase. Per `doctrine-test-functionality` and the established precedent across plan-156 PR A Phase 2, plan-156b Phase 1, plan-187 Phase 2, plan-197 Phase 1: skill markdown is LLM-consumed agent-instruction, not pytest-invocable. The functional validation is the operator running `/bicameral-report-bug` against a real bug; correctness of the keys-only extraction, the toggle UX, and the regenerate-on-opt-in flow is observed at the runtime preview, not in unit tests.

A static lint asserting the new instruction text contains the keys-only phrase would be presence-only by construction (it doesn't validate the LLM's behavior on the new instruction). Per the doctrine, presence-only assertions don't satisfy the test functionality bar.

## CI Commands

- `python scripts/lint_plan_grounding.py plan-200-config-yaml-redaction.md` — runs the plan-grounding lint shipped in PR #121 against this plan to catch any backtick-wrapped path tokens that don't resolve on the working tree.
- `grep -n "<contents>" skills/bicameral-report-bug/SKILL.md` — sanity check that the verbatim-by-default `<contents>` placeholder is gone from the default body assembly. Expected after Phase 1: matches only inside the documented opt-in path (Step 3.5's "include verbatim" branch).
- `grep -nE "config\.yaml.*verbatim|keys only" skills/bicameral-report-bug/SKILL.md` — sanity check the new instruction text and toggle copy are both present. Expected: ≥3 matches (Step 2 instruction, Step 3 template note, Step 3.5 toggle).
