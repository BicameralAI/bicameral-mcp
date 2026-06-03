# Bicameral MCP Idealized Spec

**Status:** draft for MCP refactor  
**Date:** 2026-06-03  
**Target repo:** `BicameralAI/bicameral-mcp`  
**Target branch:** `dev`  
**Contract source:** `BicameralAI/bicameral-bot` `origin/dev` commit `6fe40b9` (`Merge PR #107: feat(protocol): canonical ToolRequest/ToolResponse local tool API (#103)`)  
**Pre-refactor MCP pointer:** `BicameralAI/bicameral-mcp` `dev` commit `0827444c80d45fe3474f68002166e1fc35708eda`

## Purpose

`bicameral-mcp` is the coding agent's MCP transport surface for Bicameral. It exposes agent-friendly tools, builds canonical `ToolRequest` objects, submits them to the local `bicameral-bot` daemon, and returns daemon `ToolResponse` results in MCP-compatible content.

MCP is not a daemon, ledger, graph authority, dashboard backend, integration host, cloud advisory service, or protocol owner. It is a thin local client surface over the bot-owned protocol and runtime authority.

## Architectural Contract

The finalized local tool contract is:

```text
MCP tool call
  -> ToolRequest(command + AuthorityContext)
  -> local bot daemon validation and governance policy
  -> ToolResponse(status + result + governance_result)
  -> event store adapter materialization, if accepted
  -> replayed/materialized Decision Ledger state
```

MCP must not preserve the old v0.2 direct payload compatibility. Old direct MCP handlers are implementation history, not a compatibility contract.

## Owned By MCP

MCP owns only the agent-facing transport layer:

- MCP server startup and stdio transport.
- MCP `list_tools` metadata.
- Argument normalization from MCP-friendly tool schemas into bot-owned `ToolRequest` commands.
- `AuthorityContext` assembly for MCP sessions.
- Daemon connection discovery and health/error reporting.
- MCP response formatting from daemon `ToolResponse`.
- Client-side idempotency and request correlation fields when needed.
- Optional non-authoritative helper caches for transport speed only.
- Package/install affordances that register the MCP server with supported agent clients.

## Not Owned By MCP

The refactor must remove or stop using MCP implementations that own these responsibilities:

- Event store adapter internals.
- Canonical audit log or event materialization.
- Direct `.bicameral/decisions`, `.bicameral/events`, `.bicameral/sources`, or local ledger writes.
- SurrealDB ledger schema, migrations, replay, or row mutation.
- Local graph indexing, symbol validation authority, graph persistence, and graph readiness.
- Direct binding materialization or compliance resolution.
- Dashboard/review UI backend.
- External source integration runtime.
- Hosted/team conflict advisory or organization-scale graph analysis.
- LLM reasoning as authority.
- Egress/write-back, notification, sync, or annotation contracts.

If an old MCP capability is not implemented in bot yet, MCP still removes the old owner implementation. The plan must leave a pointer to commit `0827444c80d45fe3474f68002166e1fc35708eda` so maintainers can recover behavior during bot implementation without keeping obsolete authority in MCP.

## Tool Surface

MCP exposes tools that map to the bot `ToolCommand` registry from issue #103:

| MCP tool | ToolRequest command | Notes |
|---|---|---|
| `bicameral.ingest` | `ingest.submit_local` | Local actor submits source/session evidence or candidate drafts. |
| `bicameral.preflight` | `preflight.run` | Reads relevant decisions and graph-scoped evidence via daemon. |
| `bicameral.bind` | `binding.create` | Proposes binding evidence; daemon owns validation and materialization. |
| `bicameral.binding.inspect` | `binding.inspect` | Inspects bindings/evidence through daemon. |
| `bicameral.review.accept_candidate` | `review.accept_candidate` | Review command, not direct ledger mutation. |
| `bicameral.review.reject_candidate` | `review.reject_candidate` | Review command, not direct ledger mutation. |
| `bicameral.review.approve_signoff` | `review.approve_signoff` | Review command gated by bot policy. |
| `bicameral.review.reject_signoff` | `review.reject_signoff` | Review command gated by bot policy. |
| `bicameral.review.resolve_compliance` | `review.resolve_compliance` | Review command gated by bot policy and evidence state. |
| `bicameral.history` | `history.list` | Read replayed/materialized state. |
| `bicameral.search` | `search.query` | Search daemon-owned state. |

`bicameral.dashboard` is deferred unless bot exposes a local dashboard command or stable URL discovery endpoint. MCP may later add it as convenience only; it must not host the dashboard.

Legacy tools that do not map to the finalized command registry are removed from the core server surface. This includes `bicameral.link_commit`, `bicameral.reset`, `bicameral.judge_gaps`, `bicameral.ratify`, `bicameral.remove_decision`, `bicameral.remove_source`, `bicameral.resolve_collision`, `bicameral.skill_begin`, `bicameral.skill_end`, `bicameral.feedback`, `bicameral.usage_summary`, `bicameral.diagnose`, `validate_symbols`, and `get_neighbors`.

Future versions may reintroduce some behavior only after bot defines a canonical `ToolCommand`, protocol schema, and daemon dispatch path for it.

## AuthorityContext

Every MCP-generated `ToolRequest` includes:

- `actor_id`: local user, agent session id, or daemon-authenticated equivalent.
- `auth_method`: `mcp_session`.
- `session_id`: MCP/client session id when available.
- `workspace`: repo/workspace root as understood by the daemon.
- `policy_scope`: caller-supplied or derived policy scope tags.
- `audit_metadata`: at least `surface=mcp`, MCP tool name, client name/version when known, and MCP package version.

MCP may derive missing context from environment, daemon handshake, or explicit tool arguments. If required authority context cannot be established, MCP returns a transport error before sending a malformed authority-bearing request.

## Request And Response Rules

MCP must:

- Generate `request_id` and `issued_at` for every call unless the daemon supplies a safer request wrapper.
- Preserve bot command names exactly.
- Validate only local transport shape before dispatch; bot remains the schema and governance authority.
- Return daemon `ToolResponse` status, result payload, governance result, and request id.
- Represent daemon rejections as successful MCP tool responses with rejected status when the daemon processed the request.
- Represent daemon unavailability, handshake failure, and invalid local MCP arguments as MCP errors.

MCP must not:

- Convert daemon rejection into local acceptance.
- Mutate local Bicameral state after a daemon rejection.
- Retry non-idempotent commands without an explicit request id/idempotency contract.
- Hide graph evidence states such as `unknown_stale`, `unknown_not_indexed`, `ambiguous`, `unsupported`, or `approximate_candidate`.
- Convert caller LLM hints into verified evidence.

## Graph And Binding Behavior

Graph and binding evidence are daemon-owned. MCP may pass:

- file paths;
- symbols;
- diff context;
- commit/ref hints;
- binding hints;
- validation tokens returned by the daemon;
- caller rationale.

MCP must receive and display:

- `graph_snapshot_id`;
- `validated_sha`;
- `validated_ref`;
- `GraphEvidenceState`;
- typed snapshot mismatch errors;
- graph readiness warnings.

Only daemon-verified graph evidence can be materialized as `BindingEvidence`. MCP never falls back to fuzzy local grounding after the daemon rejects or cannot verify a graph claim.

## LLM Reasoning Placement

Coding agents using MCP may have LLM capacity. Their outputs are caller hints, not authority.

MCP can submit candidate drafts, excerpts, binding hints, level hints, compliance concerns, and rationale through `ToolRequest`. The daemon validates deterministic facts where possible, normalizes policy inputs, gates review commands, and materializes only accepted events.

The product should support several reasoning provider modes without changing the MCP authority boundary:

- `disabled`: daemon accepts clean projections and uses deterministic fallback behavior only.
- `caller`: MCP/coding agents submit model-derived hints and rationale through `ToolRequest`.
- `local_endpoint`: users configure an OpenAI-compatible local endpoint such as Ollama, LM Studio, vLLM, or a llama.cpp wrapper.
- `local_cli`: users configure a command that accepts typed JSON on stdin and returns typed JSON on stdout.
- `hosted_bicameral`: signed-in users use Bicameral-hosted reasoning for managed extraction, summaries, and advisory analysis.

MCP remains a caller/tool surface in all modes. Hosted or local daemon-side reasoning belongs behind the bot/cloud provider boundary, not inside MCP.

## Hosted Reasoning And Memoization

Hosted Bicameral reasoning should be positioned as managed reasoning consistency, not model authority. The value proposition is:

> Consistent, cached, versioned AI interpretation of the same evidence across a team, while final authority stays in governance.

Hosted reasoning can improve team ergonomics and become a paid capability because it provides:

- zero local model setup;
- shared team-wide model and prompt policy;
- Bicameral-tuned extraction, span selection, level-hint, binding-suggestion, and conflict-summary tasks;
- background integration ingest when no coding agent is active;
- admin controls, usage limits, audit metadata, and predictable fallback behavior;
- centralized upgrades to model routing and prompts.

The hosted service should also memoize reasoning artifacts per tenant. For the same tenant, workspace, source snapshot, reasoning task, normalized input, model policy version, prompt version, schema version, and redaction policy version, the service returns the existing advisory artifact instead of asking the model again.

This does not make the underlying model deterministic. It makes Bicameral's pipeline replay deterministic for already-seen inputs, reducing duplicate inferred decisions and model cost.

Canonical cache key fields:

```text
tenant_id
workspace_id
reasoning_task
source_snapshot_hash
input_object_hash
model_policy_version
prompt_version
schema_version
redaction_policy_version
```

Reasoning artifacts are immutable advisory provenance records. Prompt/model/schema changes create a new artifact version; they do not silently rewrite prior review history. A new artifact is created only when the source snapshot, task, normalized input, model policy, prompt, schema, redaction policy, or explicit reanalysis request changes.

The dashboard can expose this as a model configuration tab:

- hosted Bicameral sign-in and workspace selection;
- local OpenAI-compatible endpoint configuration;
- local CLI adapter configuration;
- per-task toggles for candidate extraction, evidence spans, level hints, binding suggestions, conflict summaries, and review summaries;
- usage/quota and cache-hit visibility for hosted mode.

## External Ingest And Egress

`ExternalIngestEnvelope` is separate from `ToolRequest`. MCP is a local tool surface under local actor/session authority, not an external source integration. MCP must not accept arbitrary external-integration payloads and route them as `ToolRequest` authority.

`Egress` is future outbound write-back/notification/sync/annotation. MCP does not implement egress.

## Ideal Repository Shape

After refactor, the MCP repository should be small:

```text
bicameral_mcp/
  server.py              # MCP server, list_tools, call_tool
  tool_schemas.py        # MCP input schemas for supported tools
  tool_request.py        # ToolRequest construction helpers
  authority.py           # MCP AuthorityContext assembly
  daemon_client.py       # local bot daemon client
  responses.py           # ToolResponse -> MCP response formatting
  errors.py              # daemon/transport error mapping
  version.py
tests/
  test_tool_schemas.py
  test_tool_request_mapping.py
  test_authority_context.py
  test_daemon_client.py
  test_response_formatting.py
docs/
  specs/
  plans/
```

The repo should not contain production `ledger/`, `code_locator/`, `codegenome/`, `dashboard/`, `integrations/`, `governance/`, `sources/`, `events/`, `daemon/`, `dlq/`, `pulse/`, or source-system adapter implementations after the refactor, except for temporary migration notes or tests that assert they have been removed.

## Compatibility Policy

Compatibility target: bot `ToolRequest`/`ToolResponse` protocol on `dev`, not historical MCP direct payloads.

Breaking old MCP clients is acceptable and intended for this refactor. The release notes must state:

- old v0.2 direct MCP payloads are superseded;
- direct ledger/graph/dashboard behavior moved behind the bot daemon boundary;
- previous implementation can be inspected at MCP commit `0827444c80d45fe3474f68002166e1fc35708eda`;
- missing bot-backed behavior is intentionally unavailable rather than emulated in MCP.
