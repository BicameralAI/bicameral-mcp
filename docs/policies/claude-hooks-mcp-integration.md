# Claude Code hooks → bicameral MCP context integration (#224 Phase C-pre)

When the agent on the other end of the MCP transport is **Claude Code**,
we leverage Claude Code hooks (``PreToolUse``, ``SessionStart``) to
fetch *relative context* from the bicameral MCP at gate-time and
surface it to the model.

This is **additive** over the deterministic server-side gates
documented elsewhere. It is not a substitute. Per the #205
doctrine, governance is enforced by deterministic code; the hooks
add context, not authority.

## Hooks in this repo

| Hook | Fires | Effect |
|---|---|---|
| ``.claude/hooks/session_start_timeout_posture.py`` | Once per Claude Code session | Prints a one-line brief to stderr summarizing current ledger-query timeout config + recent timeout-event counts |
| ``.claude/hooks/pre_tool_use_timeout_context.py`` | Before bicameral tool calls (configure via ``.claude/settings.json``) | Prints a warning to stderr only when the ring buffer shows recent (<10 min) timeouts, so the model has evidence to back off or pick ``timeout_class="drift"`` |

Both hooks **always exit 0**. They never block tool execution.
``stderr`` is the surfacing channel because Claude Code routes hook
stderr back to the model as a context fragment.

## Wiring the hooks

Edit ``.claude/settings.json`` to register the hooks. Example shape
(operator-specific; not committed by default):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/session_start_timeout_posture.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "mcp__bicameral__bicameral_.*",
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/pre_tool_use_timeout_context.py"
          }
        ]
      }
    ]
  }
}
```

## Design constraints

1. **Exit 0 unconditionally.** A hook crash must never block the
   session or the tool call. Each script wraps every external
   call in ``try / except``.
2. **Quiet when there's nothing to say.** The pre-tool-use hook
   only emits when recent timeouts exist. A clean session prints
   nothing — no model-context noise.
3. **No PII / secret routing.** The brief emits counts + numeric
   budgets + the env-disable boolean. No SQL fragments, no
   decision IDs, no operator email — the hook is observability,
   not exfiltration.
4. **MCP unreachable → graceful degradation.** If the bicameral
   package isn't importable from where the hook runs, the
   session-start hook prints a single warning and exits 0. The
   pre-tool-use hook exits 0 silently.
5. **Cross-platform.** Hooks are Python scripts because the
   bicameral operator base spans Windows and POSIX. ``python``
   on PATH is the only requirement.

## Wire format with the MCP

The hooks read two surfaces:

1. **Local config** via ``context._read_query_timeout_*_seconds`` —
   so the brief always shows the actually-resolved budget after
   fail-closed parsing, not the unverified raw config value.
2. **In-process ring buffer** via
   ``ledger.timeout_telemetry.recent_timeout_counts`` — counts of
   ``LedgerTimeoutError`` events emitted in the configured window.

The buffer is **process-local**. Each Claude Code session running
in the bicameral checkout sees its own process; restarting the
MCP server resets the buffer. This matches the session-start
surfacing semantic — operators want "what's happened in this
session" not "what's happened in history."

The same ring-buffer state is also surfaced via the
``bicameral_preflight`` MCP response (``recent_timeout_count`` field).
That's the *other* path the hook architecture supports: a future
hook variant could call into the MCP transport directly rather
than importing the Python package, useful for clients running the
MCP over network. The current scripts use the local import path
because it's simpler and the operator install is local-only.

## Adding a new hook for a different gate

When adding a new gate elsewhere in the codebase (rate-limit,
fail-closed config, schema check), follow this pattern:

1. **Implement the deterministic server-side gate first.** That
   is the floor. The hook is additive.
2. **Add the ring-buffer / counter surface** alongside the gate
   so the hook has data to fetch.
3. **Register the gate** in ``governance-gates.yaml``.
4. **Add a Python hook script** at ``.claude/hooks/<gate>_*.py``
   that reads the surface and emits stderr context. Exit 0 always.
5. **Document the wiring** in this file under a new section so
   operators can register the hook in ``.claude/settings.json``
   if they want it.
6. **Test the hook script as a subprocess** in
   ``tests/test_claude_hooks_*.py`` — sociable, real script,
   real env, real buffer state.

Per the
[feedback-claude-hooks-for-mcp-context memory](../../memory/feedback_claude_hooks_for_mcp_context.md),
this is the default pattern for new gates in this codebase.
