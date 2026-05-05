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

Run a single Bash block to gather environment data. Output goes
straight into the issue body — do not surface raw output to the user.

```bash
{
  echo "## Environment"
  echo
  echo "- bicameral-mcp version: $(bicameral-mcp --version 2>/dev/null || pip show bicameral-mcp 2>/dev/null | awk '/^Version:/ {print $2}' || echo 'unknown')"
  echo "- Python: $(python3 --version 2>/dev/null || echo 'unknown')"
  echo "- OS: $(uname -srm 2>/dev/null || echo 'unknown')"
  echo "- IDE: ${CLAUDE_CODE_VERSION:+Claude Code $CLAUDE_CODE_VERSION}${CURSOR_TRACE_ID:+Cursor}${TERM_PROGRAM:+ ($TERM_PROGRAM)}"
  echo "- Shell: $SHELL"
  echo
  echo "## Repo state"
  echo
  echo '```'
  # Branch name and commit subjects often leak business context (initiative names,
  # vendor partners, unannounced features). Redact by default; print only the shape
  # of the state, not the content.
  COMMIT_COUNT=$(git -C "$(pwd)" log --oneline -3 2>/dev/null | wc -l | tr -d ' ')
  echo "branch: <REDACTED>"
  echo "$COMMIT_COUNT recent commit(s) (titles redacted)"
  echo '```'
  echo
  if [ -f .bicameral/config.yaml ]; then
    echo "## .bicameral/config.yaml"
    echo
    echo '```yaml'
    cat .bicameral/config.yaml
    echo '```'
  fi
} 2>/dev/null
```

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
- **Environment** + **Repo state** + **config.yaml**: from the Bash
  block above.

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

Then call `AskUserQuestion`:

```
AskUserQuestion({
  questions: [{
    question: "Open the prefilled GitHub issue?",
    header: "Open issue",
    multiSelect: false,
    options: [
      { label: "Yes, open it",
        description: "Browser opens to a GitHub draft — you review and submit there" },
      { label: "Edit the body first",
        description: "I want to revise the body in chat before opening" },
      { label: "Cancel",
        description: "Don't open anything; nothing leaves the machine" }
    ]
  }]
})
```

- **Yes** → proceed to Step 4.
- **Edit the body first** → ask the user what to change, regenerate
  the body, return to this step.
- **Cancel** → stop. Tell the user "Cancelled. Nothing was sent." and
  emit the `errored=False` skill_end with `error_class="user_cancelled"`.

---

## Step 4 — Open the prefilled GitHub issue

Build the URL. Use `python3 -c` to URL-encode safely (do NOT hand-roll
encoding — `?` `&` `#` in the body will break it).

```bash
TITLE='<title from Step 1>'
BODY='<assembled markdown from Step 3>'

URL=$(python3 -c '
import sys, urllib.parse
title, body = sys.argv[1], sys.argv[2]
q = urllib.parse.urlencode({
    "title": title,
    "body": body,
    "labels": "dev,bug",
})
print("https://github.com/BicameralAI/bicameral-mcp/issues/new?" + q)
' "$TITLE" "$BODY")

case "$(uname -s)" in
  Darwin)  open "$URL" ;;
  Linux)   xdg-open "$URL" >/dev/null 2>&1 || echo "$URL" ;;
  MINGW*|MSYS*|CYGWIN*) start "" "$URL" ;;
  *) echo "$URL" ;;
esac
```

If `open` / `xdg-open` is unavailable (CI, headless), print the URL
so the user can copy it.

---

## Step 5 — Tell the user

Short, factual confirmation. No emoji. Format:

```
Opened a prefilled GitHub issue on BicameralAI/bicameral-mcp with
the `dev` label. Review, edit if needed, and submit on the page.

If your browser didn't open, the URL is above.
```

**Do not** post the full body back to the user — they'll see it on
the GitHub page. Posting it again is noise.

---

## Privacy & safety rules

- **Never auto-submit.** The skill's whole contract is: assemble + open.
  The user reviews on GitHub and clicks Submit. Anything that leaves
  the machine leaves it through the user's hands.
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
  session_id=<uuid4>,
  rationale="<one-liner: what triggered the report>")
```

**At skill end**:
```
bicameral.skill_end(skill_name="bicameral-report-bug",
  session_id=<stored_id>,
  errored=<bool>,
  error_class="url_open_failed" if browser launch failed else None)
```
