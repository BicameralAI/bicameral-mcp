---
name: bicameral-report-bug
description: File a bug against bicameral-mcp with auto-bundled context. Fires on "/bicameral-report-bug", "file a bicameral bug", "report this bicameral bug", "bicameral broke", "bicameral isn't working", "something's wrong with bicameral", "this is a bicameral bug". Bundles environment + recent calls + error trace into a prefilled GitHub issue URL on BicameralAI/bicameral-mcp with the `dev` label, and opens it in the browser. Skill DOES NOT submit — the user reviews and clicks submit on GitHub.
---

# /bicameral-report-bug — File a bug with context

**Trigger**: `/bicameral-report-bug`, or natural-language phrasing like
"file a bicameral bug", "this bicameral thing broke", "report this issue".

The skill collects environment + recent tool activity + the suspected
failure, formats them as a GitHub issue body, and opens a prefilled
issue URL on the dev repo. The user lands on the GitHub new-issue form
with everything filled in — they review, edit if needed, and click
Submit. Nothing leaves the machine until the user clicks Submit.

**Repo target**: `BicameralAI/bicameral-mcp` (the dev-facing MCP repo,
not the parent `bicameral` monorepo).
**Default labels**: `dev`, `bug`.

---

## Step 1 — Confirm intent + collect a one-line description

Use `AskUserQuestion` to grab a one-line title and a short description.
Default-select the most plausible title based on the recent error or
tool activity in the current session.

```
AskUserQuestion({
  questions: [
    {
      question: "One-line summary of what went wrong?",
      header: "Title",
      multiSelect: false,
      options: [
        { label: "<best guess from recent error/activity>",
          description: "Suggested from the last failed tool call" },
        { label: "Other (write my own)",
          description: "I'll type a different title" }
      ]
    },
    {
      question: "What were you trying to do?",
      header: "Intent",
      multiSelect: false,
      options: [
        { label: "Ingest decisions from a transcript" },
        { label: "Run preflight before implementing" },
        { label: "Check drift / scan branch" },
        { label: "Resolve compliance / sync after commit" },
        { label: "Other (skip this question)" }
      ]
    }
  ]
})
```

If the user picks "Other" for the title, prompt freely for a one-liner.

---

## Step 2 — Collect diagnostic context

Assemble the diagnostic context from the current session — **do not
shell out**. Use what the agent already knows plus the `Read` tool for
files the user wants to include.

Sources (in order of preference, all read-only / non-bash):

- **bicameral-mcp version**: any recent `bicameral.*` tool response
  surfaces it; otherwise `Read` `pyproject.toml` and pull the
  `version = "..."` line. If unknown, write `unknown`.
- **IDE / harness**: derive from the running environment context the
  agent already has (e.g. "Claude Code", "Cursor"). If unknown,
  write `unknown`.
- **OS**: derive from the platform the agent already knows (e.g.
  `darwin`, `linux`). If unknown, write `unknown`.
- **Repo state**: do **not** read branch names or commit subjects —
  they leak business context. Record only the shape:
  `branch: <REDACTED>` and `recent commits: titles redacted`.
- **`.bicameral/config.yaml`**: use `Read` on `.bicameral/config.yaml`
  if it exists. **Extract ONLY the top-level key structure by default** —
  every YAML key whose line doesn't start with whitespace, one per
  line, sorted alphabetically. Do NOT include values, nested keys,
  comments, or any other content. Default-shape rationale: top-level
  keys are sufficient diagnostic signal for *"is this bug in the
  config loader?"* questions while leaking zero workspace IDs, tokens,
  allowlists, or environment-specific settings. If the operator's bug
  genuinely needs the verbatim contents (e.g. a YAML parser regression),
  Step 3.5's transparency preview offers an explicit opt-in toggle —
  see "Step 3.5 — Transparency preview" below. If `Read` errors (file
  missing), skip the section entirely.

Then assemble in your head (do NOT print to user yet):

- **Title**: from Step 1
- **Intent**: from Step 1
- **Symptom**: the most recent error / unexpected behavior the agent
  observed in this conversation. Quote tool error output verbatim if
  available — that's the highest-signal piece. If no error, describe
  the unexpected output.
- **Reproduction**: the last 3-5 bicameral tool calls in this session,
  in order. Just the call signatures (tool name + parameter *names*) —
  **redact the parameter VALUES by default**. Replace `query="…"`,
  `feature_filter="…"`, `topic="…"`, `intent="…"`, `description="…"`,
  `text="…"`, `excerpt="…"`, `title="…"`, etc. with `<REDACTED>` or
  short placeholders (`<feature_area_a>`, `<feature_area_b>`). The
  diagnostic signal is which tool was called with which parameter
  *names*, not the verbatim payload — payloads almost always leak
  business context (feature names, vendor names, internal codenames).
  Also redact obvious secrets (`ANTHROPIC_API_KEY=…`, bearer tokens).
- **Environment** + **Repo state** + **config.yaml**: assembled as
  above, with the same markdown shape:

  ```markdown
  ## Environment

  - bicameral-mcp version: <version or "unknown">
  - IDE: <IDE or "unknown">
  - OS: <os or "unknown">

  ## Repo state

  ```
  branch: <REDACTED>
  recent commits: titles redacted
  ```

  ## .bicameral/config.yaml   ← only if Read succeeded

  ```
  <sorted top-level key list, one per line, no values>
  ```
  *(values redacted by default — opt in via Step 3.5 transparency preview to include verbatim)*
  ```

---

## Step 3 — Build the issue body

Format as markdown:

```markdown
**Intent**: <from Step 1>

## Symptom

<recent error verbatim, or one-paragraph description>

## Reproduction (recent calls)

1. `bicameral.<tool>(...)`
2. `bicameral.<tool>(...)`
3. ...

<environment + repo state + config blocks from Step 2>

---
_Reported via `/bicameral-report-bug`._
```

If the assembled body exceeds **6500 characters**, drop sections in
this order until it fits:
1. The git log (keep just the branch name)
2. The config.yaml block
3. Any tool-call argument bodies (keep just tool names)

GitHub's URL prefill caps around 8KB, and we want headroom for the
URL-encoded title.

---

## Step 3.5 — Transparency preview (consent gate)

Before opening the browser, show the user exactly what will be in the
issue body and what was redacted. Modeled on `setup_wizard._select_telemetry()`
(the wizard's "exact payload that would be sent" pattern) — concrete
preview, explicit non-collection list, single yes/no.

Print this block verbatim, filling in `<...>` placeholders:

```
About to open a prefilled GitHub issue on BicameralAI/bicameral-mcp.
Here's exactly what goes in the body — review it before anything leaves
your machine.

  Title:  <title from Step 1>
  Labels: dev, bug

  ── Body preview ─────────────────────────────────────────────
  <full assembled markdown from Step 3, indented two spaces>
  ─────────────────────────────────────────────────────────────

Auto-redacted in this body:
  - Tool-call argument *values* in the Reproduction section (query, feature_filter,
    topic, intent, description, text, excerpt, title — parameter names kept,
    values replaced with <REDACTED>)
  - Branch name and commit subject lines in the Repo state block
  - .bicameral/config.yaml: keys only by default (workspace IDs, tokens,
    allowlists, env-specific values stripped — toggle below to include verbatim
    if the bug requires inspecting config values)
  - API keys, bearer tokens, secrets, passwords (regex-matched)
  - <N> redaction(s) applied   ← print actual count, or "none detected"

Never included by this skill:
  - File contents, code snippets you didn't paste
  - Decision text or ledger entries
  - Environment variables (only the IDE name + version)
  - Personal data, email, repo URL

Nothing has been sent yet. The browser will open a GitHub *draft* —
you can edit anything in the body, delete sections, or close the tab
to abandon. The issue is only filed when you click Submit on GitHub.
```

> **Telemetry note**: this skill emits `skill_begin` / `skill_end` events on every invocation (skill name only, no body content). Set `BICAMERAL_TELEMETRY=0` to opt out before invoking.

Then call `AskUserQuestion`:

```
AskUserQuestion({
  questions: [{
    question: "Open the prefilled GitHub issue?",
    header: "Open issue",
    multiSelect: false,
    options: [
      { label: "Yes, open it (config.yaml: keys only)",
        description: "Default — config.yaml top-level keys included, values redacted" },
      { label: "Yes, but include config.yaml verbatim",
        description: "Use only when the bug requires inspecting config values (e.g. YAML parser regression). Values still pass the secret-redaction regex but workspace IDs / allowlists / env settings are exposed." },
      { label: "Edit the body first",
        description: "I want to revise the body in chat before opening" },
      { label: "Cancel",
        description: "Don't open anything; nothing leaves the machine" }
    ]
  }]
})
```

- **Yes, open it (config.yaml: keys only)** → proceed to Step 4 with the keys-only body as already previewed.
- **Yes, but include config.yaml verbatim** → regenerate the body, replacing the keys-only block in the `## .bicameral/config.yaml` section with the verbatim ```yaml <contents> ``` shape. Re-run the Auto-redacted summary on the new body (the secret-redaction regex still applies — defense-in-depth, not a substitute for the keys-only default). Re-display the transparency preview with the verbatim contents and re-ask the open-issue question one more time so the operator sees what's actually being shipped before clicking through. Then proceed to Step 4 once the operator confirms the verbatim shape.
- **Edit the body first** → ask the user what to change, regenerate
  the body, return to this step.
- **Cancel** → stop. Tell the user "Cancelled. Nothing was sent." and
  emit the `errored=False` skill_end with `error_class="user_cancelled"`.

---

## Step 4 — Output the prefilled GitHub issue URL

Build the URL inline — no shell, no `python3 -c`. URL-encode the
title and body using standard percent-encoding (RFC 3986) and assemble:

```
https://github.com/BicameralAI/bicameral-mcp/issues/new?title=<URL-encoded title>&body=<URL-encoded body>&labels=dev,bug
```

Encoding requirements:
- Encode every `?`, `&`, `#`, `=`, space (→ `%20` or `+`), newline
  (`%0A`), and any non-ASCII characters in both title and body. Triple
  backticks and other markdown survive encoding fine.
- Keep the URL under **8000 characters** total. If it exceeds that,
  loop back to Step 3 and drop sections per the truncation order
  (git log → config.yaml → tool-call argument bodies).

Then print the full URL on its own line in chat — the user clicks it
to land on the prefilled GitHub draft. Do **not** attempt to launch a
browser yourself; the user opens it. Format:

```
Open this prefilled GitHub issue (review on the page, then click Submit):

<full URL>
```

---

## Step 5 — Tell the user

Short, factual confirmation. No emoji. Format:

```
Posted a prefilled GitHub issue link on BicameralAI/bicameral-mcp
with the `dev` label. Click the URL above, review the page, edit if
needed, and click Submit.
```

**Do not** post the full body back to the user — they'll see it on
the GitHub page. Posting it again is noise.

---

## Privacy & safety rules

- **Never auto-submit.** The skill's whole contract is: assemble + hand
  the user a URL. The user opens it, reviews on GitHub, and clicks
  Submit. Anything that leaves the machine leaves it through the
  user's hands.
- **Redact obvious secrets** before placing them in the body: anything
  matching `(api[_-]?key|token|secret|password|bearer)\s*[=:]\s*\S+`
  → replace value with `***REDACTED***`.
- **Redact business context by default**, even though it isn't a "secret":
  - Tool-call argument values in the Reproduction section. Preserve parameter
    names (so the maintainer sees which fields were used), but replace values
    with `<REDACTED>` or `<feature_area_a>` placeholders.
  - The current branch name and recent commit subject lines. These routinely
    name initiatives, vendor partners, and unannounced features. Print only
    the shape (`branch: <REDACTED>`, `N recent commit(s) (titles redacted)`).
  - If the user explicitly wants the verbose context for a specific bug, they
    can paste it back in during the "Edit the body first" branch of Step 3.5.
    Default off — leak less, ask more.
- **Do not include file contents** unless the user explicitly pastes
  them in their description. Recent tool calls and error traces are OK
  — file dumps are not.
- **Do not include the full ledger.** If the bug needs ledger context,
  the maintainer can ask in the issue thread.

---

## When NOT to fire

- The user is asking how to use bicameral, not reporting a bug → answer
  the question directly.
- The user is reporting a code-level drift / spec gap inside their own
  project → that's `bicameral-ingest` or `bicameral-doctor`, not a
  bicameral bug.
- The user wants to file an issue against a different project (their
  own repo, GitHub Actions, etc.) → don't fire; this skill only files
  against `BicameralAI/bicameral-mcp`.

---

## Telemetry

**At skill start**:
```
bicameral.skill_begin(skill_name="bicameral-report-bug",
  session_id=<uuid4>)
```

**At skill end**:
```
bicameral.skill_end(skill_name="bicameral-report-bug",
  session_id=<stored_id>,
  errored=<bool>,
  error_class="user_cancelled" if user cancelled at preview else None)
```
