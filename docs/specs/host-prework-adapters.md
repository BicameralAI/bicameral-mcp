# Host Pre-work Adapters (MCP-distributed)

Status: implemented (mcp#734, parent RFQ mcp#733).

This spec defines the package-owned host adapters that let a coding host run
`bicameral.preflight` once, automatically, at a genuine pre-work boundary. The
adapters are shipped and owned by `bicameral-mcp`. They are product automation,
not repo-local skills, and they have no runtime dependency on the Bicameral
Factory.

## Ownership boundary

- **MCP owns** the user-distributed workflow automation: the packaged adapters,
  their host install/status/update/disable/uninstall lifecycle, consent, and the
  bounded pre-work invocation of `bicameral.preflight`.
- **The daemon (`bicameral-bot`) owns** all canonical state: Decisions,
  candidates, signoff, evidence, compliance, protocol/authorization semantics.
  Adapters forward bounded context and read the daemon's response; they never
  write canonical state and never infer global consistency.
- **Repo-local skills** (when to run Bicameral in a repo, which ADRs to read,
  contribution policy, factory attestation) remain outside MCP and are not owned
  or distributed by this package.
- **The Bicameral Factory** is development/governance tooling. It is never
  installed, imported, fetched, or read at product runtime by these adapters.

## Supported hosts and mechanisms

Each host uses its own documented, host-native hook mechanism. Evidence is
host-specific; support for one host never implies support for another.

| Host        | Mechanism (official)                              | Config file                                             |
| ----------- | ------------------------------------------------- | ------------------------------------------------------- |
| Claude Code | Claude Code hooks — `SessionStart` command hook   | `~/.claude/settings.json`                               |
| Codex CLI   | Codex lifecycle hooks — `SessionStart` command hook | `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) |

Both mechanisms deliver a JSON event on stdin at hook execution time. The
adapters read only an allowlisted view of that event (`session_id`, `source`,
`cwd`) and treat `source == startup` as the sole pre-work boundary; `resume`,
`compact`, and `clear` continue an existing session and do not fire.

If a host lacks a production pre-work mechanism, the capability probe reports
`supported == false` and `install` fails visibly instead of simulating support.

## Lifecycle

CLI: `bicameral-mcp adapters <status|install|update|disable|uninstall> [--host HOST] [--json]`.

- **install** — requires explicit `--consent`. Writes an MCP-managed
  `SessionStart` command hook into the host's native config, preserving any
  existing user hooks. Records consent and adapter metadata under
  `<host home>/bicameral-mcp/`.
- **status / inspect** — reports state (`not_installed` / `enabled` /
  `disabled`), mechanism, config path, whether the managed hook is present,
  capability support, consent, and contract version.
- **update** — rewrites the managed hook to the current runner invocation and
  contract version without duplicating entries.
- **disable** — removes the managed hook entry but retains consent metadata.
- **uninstall** — removes the managed hook entry, consent record, and dedup
  markers. Never touches unrelated host config.

Managed hook entries are identified by a stable token plus the host id, so the
adapter only ever edits or removes its own entries.

Host configuration is treated as external user data. A missing config may be
created, but an existing config must be a readable JSON object. Unreadable,
malformed, or non-object input fails closed with the original bytes unchanged.
Valid mutations first retain the exact prior bytes at
`<config>.bicameral-backup`, write the complete replacement to a temporary file
beside the config, and atomically replace the config. New configs have no
backup because no prior user data exists.

## Pre-work invocation contract

At a pre-work boundary the runner (`bicameral-mcp prework-run --host HOST`, which
the hook invokes) does, in order:

1. Parse the host event into an allowlisted view.
2. Skip unless the adapter is enabled and the boundary is genuine pre-work.
3. Deduplicate on `correlation_id = "<host>:<session_id>"`: invoke
   `bicameral.preflight` **exactly once** per task boundary.
4. Perform a daemon protocol/capability handshake.
5. Invoke `preflight.run` with bounded context and correlation/idempotency
   metadata.

On any of: host mechanism absent, daemon unavailable, protocol mismatch, command
unadvertised, or dispatch error — the runner emits a **visible, typed fallback**
message pointing at explicit/manual `bicameral.preflight`, does **not** claim
preflight ran, and does **not** consume the once-per-boundary marker (so a retry
after fixing the daemon is possible). The runner always exits 0 so a missing or
failing daemon never blocks the host session.

## Bounded context and privacy

Forwarded to the daemon (allowlist only):

- task boundary (e.g. `session_start`)
- workspace root path
- current git branch (when resolvable)
- changed file paths, symbol names, bounded diff summary (when the host provides
  them)
- `checkpoint_hint = pre_work`, `correlation_id`, `idempotency_key`

Never read or forwarded: raw transcripts (`transcript_path` is never read),
secrets/API keys/tokens, unrelated tool output, environment, prompts/messages,
and any background telemetry.

Hook receipts and dedup markers under `<host home>/bicameral-mcp/` are
operational witnesses only, not canonical product state.

## Non-goals

These adapters never:

- render, choose, promote, or confirm candidates;
- run mid-session or before a write (pre-work boundary only);
- depend on Factory skills, content, or runtime;
- make compliance, safety, no-conflict, or merge-readiness claims.
