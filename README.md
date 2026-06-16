# Bicameral MCP

`bicameral-mcp` is the MCP transport client for the local `bicameral-bot`
daemon. It exposes agent-friendly tools, maps them into canonical
`ToolRequest` envelopes, sends those requests to the daemon, and returns daemon
`ToolResponse` payloads.

MCP is not the Bicameral daemon, Decision Ledger, code graph, dashboard,
integration runtime, setup wizard, telemetry sink, or governance engine.

## Current Contract

The cutover target is the bot-owned ToolRequest protocol:

```text
MCP tool call
  -> ToolRequest(command + AuthorityContext)
  -> bicameral-bot daemon validation and governance policy
  -> ToolResponse(status + result + governance_result)
```

MCP performs a daemon capability handshake at startup. It refuses to start when
the daemon's ToolRequest protocol version is unsupported. After protocol
compatibility is established, individual commands may still return daemon
capability errors while bot parity is being implemented.

## Configuration

Set the bot daemon endpoint with:

```bash
export BICAMERAL_DAEMON_URL=http://127.0.0.1:37373
```

Optional context:

```bash
export BICAMERAL_ACTOR_ID="$(whoami)"
export BICAMERAL_WORKSPACE="$PWD"
export BICAMERAL_POLICY_SCOPE=default
```

Run the MCP server:

```bash
bicameral-mcp
```

Print supported tool names without contacting the daemon:

```bash
bicameral-mcp tools
```

## Supported Tools

MCP exposes only ToolRequest-backed tools:

| MCP tool | Bot command |
|---|---|
| `bicameral.ingest` | `ingest.submit_local` |
| `bicameral.preflight` | `preflight.run` |
| `bicameral.bind` | `binding.create` |
| `bicameral.binding.inspect` | `binding.inspect` |
| `bicameral.review.accept_candidate` | `review.accept_candidate` |
| `bicameral.review.reject_candidate` | `review.reject_candidate` |
| `bicameral.review.approve_signoff` | `review.approve_signoff` |
| `bicameral.review.reject_signoff` | `review.reject_signoff` |
| `bicameral.review.resolve_compliance` | `review.resolve_compliance` |
| `bicameral.history` | `history.list` |
| `bicameral.search` | `search.query` |

## Troubleshooting: Daemon Handshake Failures

MCP stays fail-fast on daemon capability handshake failures. It never starts,
installs, upgrades, migrates, or repairs the daemon, and never falls back to
legacy MCP-owned handlers. Instead, a failed tool call returns a typed MCP
error with an informational `recovery` payload:

```json
{
  "status": "error",
  "error_code": "daemon_protocol_mismatch",
  "recovery": {
    "error_code": "daemon_protocol_mismatch",
    "category": "setup",
    "retryable": false,
    "mcp_protocol_version": "v2",
    "daemon_protocol_version": "v1",
    "daemon_endpoint": "http://127.0.0.1:37373",
    "requested_tool": "bicameral.preflight",
    "requested_command": "preflight.run",
    "operator_action": "Upgrade bicameral-mcp and bicameral-bot/daemon to matching tags, then retry."
  }
}
```

| `error_code` | Meaning | What to do |
|---|---|---|
| `daemon_unavailable` | MCP cannot reach the configured/local bot daemon. | Start or install the Bicameral bot daemon, then retry. |
| `daemon_protocol_mismatch` | Daemon is reachable but its ToolRequest protocol version is incompatible. | Upgrade `bicameral-mcp` and `bicameral-bot`/daemon to matching tags, then retry. |
| `daemon_capability_error` | Daemon answered capabilities but the requested command is unadvertised or deferred. | Use a supported command, or upgrade to a daemon tag that advertises the capability. |
| Wrong daemon URL | Connection target is misconfigured via an env override. | Unset or correct `BICAMERAL_DAEMON_URL` / `BICAMERAL_BOT_DAEMON_URL`, then retry. |

When a daemon URL env override is set, the recovery payload adds a
`daemon_url_override` field and calls out the env var in `operator_action`, so a
wrong URL is easy to spot even though it shares the `daemon_unavailable`
transport path.

Remediation such as installing, upgrading, starting, or migrating the daemon is
bot-owned or CLI-owned, not MCP-owned.

## Prompts And Skills

MCP may expose MCP prompts for generic Bicameral workflows over supported tools,
such as preflight, binding, ingest, history, and search.

Repo-local skills are outside MCP. Keep repo/team behavior in repo skills:
when to run Bicameral, which ADRs to read, contribution policy, factory
attestation, and workflows that span beyond Bicameral MCP.

## Retired From MCP

The v0.2 direct MCP payload surface is not preserved. Removed or unsupported
legacy behavior includes:

- `link_commit`
- `ratify`
- `resolve_collision`
- `remove_decision`
- `remove_source`
- `validate_symbols`
- `get_neighbors`
- setup wizard, reset, update, diagnose, usage, feedback, and telemetry
- dashboard hosting
- local ledger/event/graph/source/integration runtimes

Missing bot-backed behavior is intentionally unavailable in MCP rather than
emulated locally.

Previous implementation history can be inspected at:

```text
0827444c80d45fe3474f68002166e1fc35708eda
```

## Development

Focused cutover checks:

```bash
python -m pytest tests/test_toolrequest_thin_client.py -q
python -m build
```

## Repository boundary

`bicameral-mcp` is the agent-facing tool surface. It exposes local Bicameral actions to coding agents: ingest, preflight, bind, review-command emission, and local run loops.

It is not the source-specific integration repo, the local bot runtime, or the hosted code graph. See `docs/adr/0001-mcp-repository-boundary.md`.
