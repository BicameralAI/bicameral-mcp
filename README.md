<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

# Bicameral MCP

### The agent-facing MCP tool surface for the local Bicameral daemon

[![SOC 2 Type II: in progress](https://img.shields.io/badge/SOC_2_Type_II-in_progress-f5a623)](#trust--compliance)
[![Visibility: public](https://img.shields.io/badge/Visibility-public-2ea44f)](#)
[![Protocol: ToolRequest v2](https://img.shields.io/badge/Protocol-ToolRequest_v2-8957e5)](#current-contract)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](#development)

</div>

`bicameral-mcp` is the MCP transport client for the local Bicameral daemon. It exposes agent-friendly tools, maps them into canonical `ToolRequest` envelopes, sends those requests to the daemon, and returns daemon-authored `ToolResponse` payloads.

MCP is not the daemon, Decision Ledger, code graph, dashboard, integration runtime, setup wizard, telemetry sink, or canonical governance authority. Edge surfaces propose; the daemon decides what becomes canonical.

## Current contract

```text
MCP tool call
  -> ToolRequest(command + AuthorityContext)
  -> local daemon validation and product policy
  -> ToolResponse(status + result + governance_result)
```

MCP performs a daemon capability handshake at startup. It refuses to start when the daemon's ToolRequest protocol version is unsupported. Individual commands may still return typed capability errors when the connected daemon does not advertise a requested feature.

## Configuration

Set the daemon endpoint:

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

Print locally known tool names without contacting the daemon:

```bash
bicameral-mcp tools
```

## Supported tools

MCP exposes only ToolRequest-backed product tools. Availability is filtered by the connected daemon's capability report.

| MCP tool | Daemon command |
|---|---|
| `bicameral.ingest` | `ingest.submit_local` |
| `bicameral.capture_context` | `ingest.submit_local` |
| `bicameral.preflight` | `preflight.run` |
| `bicameral.context` | `lookup.query` |
| `bicameral.correction_findings` | `lookup.query` |
| `bicameral.lookup` | `lookup.query` |
| `bicameral.bind` | `binding.create` |
| `bicameral.binding.inspect` | `binding.inspect` |
| `bicameral.evidence.refresh` | `evidence.refresh` |
| `bicameral.review.candidates` | `search.query` |
| `bicameral.review.corpus_proposals` | `lookup.query` |
| `bicameral.review.accept_candidate` | `review.accept_candidate` |
| `bicameral.review.reject_candidate` | `review.reject_candidate` |
| `bicameral.review.promote_candidate` | `recall.promote_decision_candidate` |
| `bicameral.review.request_corpus_change` | `recall.request_correction` |
| `bicameral.review.approve_signoff` | `review.approve_signoff` |
| `bicameral.review.reject_signoff` | `review.reject_signoff` |
| `bicameral.review.resolve_compliance` | `review.resolve_compliance` |
| `bicameral.history` | `history.list` |
| `bicameral.search` | `search.query` |
| `bicameral.review.contradictions` | `governance.inbox.list` |
| `bicameral.review.triage_contradiction` | `governance.resolve_contradiction` |
| `bicameral.governance.inbox` | `governance.inbox.list` |
| `bicameral.governance.inspect` | `governance.inspect` |
| `bicameral.governance.resolve` | `governance.resolve_contradiction` |
| `bicameral.recall.inspect_evidence` | `recall.inspect_evidence` |
| `bicameral.recall.expand_scope` | `recall.expand_scope` |
| `bicameral.request_correction` | `correction.request` |

Candidate promotion is a daemon-owned two-phase flow. An initial
`bicameral.review.promote_candidate` call may return `confirmation_required`
with daemon-authored candidate, outcome, authority, and expiry metadata. MCP
renders that state without claiming a canonical transition occurred. After an
explicit human confirmation action in the host, MCP resubmits the daemon-issued
confirmation object to `recall.promote_decision_candidate` and renders the
daemon result. MCP never mints, validates, stores, consumes, or self-confirms a
challenge.

## Product terminology

`bicameral.preflight` retrieves daemon-authored constraint and readiness context. MCP does not turn lookup output into a compliance decision, signoff, merge-safety claim, or canonical product state.

Use these terms consistently:

- **Constraint Lookup:** retrieve relevant Decisions, source links, evidence references, and readiness labels.
- **Constraint Correction Capture:** submit or review proposed corpus corrections.
- **Code Grounding:** inspect daemon-owned binding and graph evidence.
- **Code Compliance:** review daemon-owned compliance state when that capability is available.
- **Governed Work Gate:** policy-controlled blocking or routing behavior, not implied by ordinary lookup output.

## Failure behavior

MCP stays fail-fast on daemon capability handshake failures. It never installs, upgrades, migrates, or repairs the daemon, and it never falls back to legacy MCP-owned handlers.

Common typed failures include:

| Error | Meaning |
|---|---|
| `daemon_unavailable` | The configured local daemon cannot be reached. |
| `daemon_protocol_mismatch` | MCP and daemon ToolRequest protocol versions are incompatible. |
| `daemon_capability_error` | The daemon does not advertise the requested command or reports it as deferred. |

Remediation such as installation, upgrade, startup, or migration is daemon- or installer-owned.

## Prompts and host adapters

MCP may expose prompts and reviewed customer-product host adapters for workflows over supported tools. Customer adapters are product functionality and remain separate from contributor development tooling.

MCP customer installations do not require private development repositories, internal build controls, or contributor-only evidence.

## Host Pre-work Adapters

MCP ships optional, package-owned adapters that let a coding host run
`bicameral.preflight` once at a genuine pre-work boundary (a new session), so
constraint/readiness context is available before you start. These adapters are
**product automation distributed and owned by `bicameral-mcp`** — they are not
repo-local skills and they never install, import, fetch, or read the Bicameral
Factory. The Factory is development/governance tooling and is not a runtime
dependency of this package.

Supported hosts, each using its own documented, host-native mechanism:

| Host        | Mechanism                                                     | Config file                     |
| ----------- | ------------------------------------------------------------- | ------------------------------- |
| Claude Code | Claude Code hooks — `SessionStart` command hook               | `~/.claude/settings.json`       |
| Codex CLI   | Codex lifecycle hooks — `SessionStart` command hook           | `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) |

For a clean Codex home, install creates an empty `config.toml` when absent so
Codex discovers its user hook layer. It never changes an existing `config.toml`
and never bypasses Codex's host-owned hook trust.

If a host does not provide a production pre-work mechanism, that host criterion
fails visibly rather than simulating support.

Manage adapters through the `bicameral-mcp` CLI:

```bash
bicameral-mcp adapters status                        # inspect all hosts
bicameral-mcp adapters install --host claude --consent
bicameral-mcp adapters update  --host codex
bicameral-mcp adapters disable --host claude
bicameral-mcp adapters uninstall --host codex
bicameral-mcp adapters verify-host --host codex --receipt host-receipt.json --json
```

Behavioral guarantees:

- **Consent-gated.** Installing/enabling requires explicit `--consent`. Without
  it, the CLI prints exactly which bounded context could be sent and makes no
  changes.
- **Pre-work only, exactly once.** The hook fires only at a new-session boundary
  (`source == startup`), never mid-session or before a write, and invokes
  `bicameral.preflight` exactly once per task boundary (idempotent by
  correlation id).
- **Bounded context / privacy.** Only task boundary, workspace, branch, and
  optional file/symbol/diff hints are forwarded. Raw transcripts, secrets,
  unrelated tool output, environment, and telemetry are never read or sent. The
  adapter never reads the host's `transcript_path`.
- **Capability-checked with visible manual fallback.** Before invoking, the
  adapter performs a daemon protocol/capability handshake. If the host lacks the
  mechanism, the daemon is unavailable, the protocol mismatches, or the command
  is unadvertised, it emits a visible message pointing at explicit/manual
  `bicameral.preflight` and never claims preflight ran.
- **Independent host evidence.** Claude Code and Codex are configured and probed
  separately; enabling or a positive probe for one host never implies the other.
  External real-PTY evidence is accepted only with independent host-subtree
  provenance and exactly one packaged-hook process; direct invocation does not
  qualify. Factory may produce that external evidence but is never a runtime
  dependency.
- **Exact package provenance.** Lifecycle receipts record the package version,
  absolute runner executable, exact invocation, and resolution source. Status
  fails closed when the contract, package, executable, or managed command no
  longer matches.
- **No candidate workflow, no compliance claims.** Adapters never render,
  choose, promote, or confirm candidates, never write canonical Decision state,
  and make no compliance, safety, no-conflict, or merge-readiness claims. That
  authority belongs to the daemon.

Hook receipts and dedup markers under the host's MCP state directory
(`<host home>/bicameral-mcp/`) are operational witnesses only, not canonical
product state.

See [`docs/specs/host-prework-adapters.md`](docs/specs/host-prework-adapters.md)
for the full contract.

## Development

Contributor setup, internal review controls, and release procedures are documented in `CONTRIBUTING.md`, `AGENTS.md`, and `.github/`. Those development artifacts are excluded from customer packages.

Run the product checks:

```bash
python -m pytest tests/ -q
python -m build
```

## Repository boundary

`bicameral-mcp` is the agent-facing product tool surface. It is not the source-specific integration repository, local daemon runtime, hosted code graph, or canonical Decision store.

## Trust & compliance

BicameralAI is pursuing SOC 2 Type II and ISO 27001. Framework-level status is available through the Vanta Trust Center. Detailed reports and evidence are provided under appropriate access controls and are not embedded in this package.

## Security

Report security issues to **security@bicameral-ai.com**. Do not open a public issue for a suspected vulnerability. See `SECURITY.md` for the coordinated-disclosure policy and safe-harbor terms.

## License

Bicameral MCP is licensed under the Business Source License 1.1. See `LICENSE` for the complete terms.
