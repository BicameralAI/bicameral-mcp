<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

# Bicameral MCP

### The agent-facing MCP tool surface for the local Bicameral daemon

<p align="center">
  <strong>
    ⚡ <a href="#configuration">Quick Start</a> ·
    🧰 <a href="#supported-tools">Tools</a> ·
    🔌 <a href="#current-contract">Contract</a> ·
    🏛️ <a href="#governed-by-the-bicameral-factory">Governance</a> ·
    🛡️ <a href="#trust--compliance">Trust</a>
  </strong>
</p>

[![Governance: bicameral-factory](https://img.shields.io/badge/Governance-bicameral--factory-1f6feb)](https://github.com/BicameralAI/bicameral-factory)
[![SOC 2 Type II: in progress](https://img.shields.io/badge/SOC_2_Type_II-in_progress-f5a623)](#trust--compliance)
[![Visibility: public](https://img.shields.io/badge/Visibility-public-2ea44f)](#)
[![Protocol: ToolRequest v2](https://img.shields.io/badge/Protocol-ToolRequest_v2-8957e5)](#current-contract)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](#development)

</div>

`bicameral-mcp` is the MCP transport client for the local `bicameral-bot` daemon. It exposes agent-friendly tools, maps them into canonical `ToolRequest` envelopes, sends those requests to the daemon, and returns daemon `ToolResponse` payloads.

MCP is not the Bicameral daemon, Decision Ledger, code graph, dashboard, integration runtime, setup wizard, telemetry sink, or governance engine. Edge surfaces propose; the daemon decides what becomes canonical.

## Table of Contents

- [Current Contract](#current-contract)
- [Configuration](#configuration)
- [Supported Tools](#supported-tools)
- [Product Terminology](#product-terminology)
- [Troubleshooting: Daemon Handshake Failures](#troubleshooting-daemon-handshake-failures)
- [Prompts And Skills](#prompts-and-skills)
- [Retired From MCP](#retired-from-mcp)
- [Development](#development)
- [Repository boundary](#repository-boundary)
- [Governed by the Bicameral Factory](#governed-by-the-bicameral-factory)
- [Trust & Compliance](#trust--compliance)
- [Security](#security)

---

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

## Product Terminology

`bicameral.preflight` is the MCP surface for constraint lookup and readiness
context before or during implementation. It maps to the bot-owned
`preflight.run` command for historical protocol compatibility, but MCP does not
turn lookup output into a governed work gate, compliance decision, signoff, or
merge-safety claim.

Use these terms consistently:

- Constraint Lookup: retrieve relevant daemon-authored Decisions, source links,
  evidence references, and readiness labels.
- Constraint Correction Capture: submit or review proposed corpus corrections.
- Code Grounding: inspect daemon-owned binding and graph evidence.
- Code Compliance: review daemon-owned compliance state when the daemon exposes
  that capability.
- Governed Work Gate: future policy-controlled blocking/routing behavior; not
  implied by ordinary lookup or preflight output.

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
such as constraint lookup, binding, ingest, history, search, and brief.

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

## Governed by the Bicameral Factory

This repository is part of the BicameralAI organization and is governed by the [Bicameral Factory](https://github.com/BicameralAI/bicameral-factory), the org's governance control plane. The factory aggregates each repo's declared governance facts (classification, visibility, required checks) into an org-wide governance dashboard. Governance is evidence-first: status is read from hard evidence such as CI runs, file presence, and branch protection, never inferred.

## Trust & Compliance

BicameralAI is pursuing SOC 2 Type II and ISO 27001. Framework-level status and evidence are available through our [Vanta Trust Center](https://app.vanta.com/bicameral-ai.com/trust/g4wnw551zp5l8jr88ig70); access to detailed reports is provided under a non-disclosure agreement. This summary is framework-level by design; detailed control and test posture is maintained internally and is not published here.

## Security

Please report security issues to **security@bicameral.ai**. Do not open a public issue for a suspected vulnerability. See [`SECURITY.md`](SECURITY.md) for the full policy, coordinated-disclosure timeline, and safe-harbor terms.
