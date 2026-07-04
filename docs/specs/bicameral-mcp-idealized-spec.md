# Bicameral MCP Idealized Spec

**Status:** draft for MCP refactor  
**Date:** 2026-06-04  
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
- MCP prompts for generic Bicameral workflows that are tightly coupled to MCP tools.
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
- Repo-local skills, contribution policy, factory instructions, or repo-specific workflow guidance.
- Bot daemon setup, lifecycle, diagnostics, reset, update, migration, usage, or feedback telemetry.

If an old MCP capability is not implemented in bot yet, MCP still removes the old owner implementation. The plan must leave a pointer to commit `0827444c80d45fe3474f68002166e1fc35708eda` so maintainers can recover behavior during bot implementation without keeping obsolete authority in MCP.

## Tool Surface

MCP exposes tools that map to the bot `ToolCommand` registry from issue #103:

| MCP tool | ToolRequest command | Notes |
|---|---|---|
| `bicameral.ingest` | `ingest.submit_local` | Local actor submits source/session evidence or candidate drafts. |
| `bicameral.capture_context` | `ingest.submit_local` | Submits MCP session/tool/command/code-hint context as bot-owned Source/SourceSnapshot/EvidenceReference-compatible local ingest input. |
| `bicameral.preflight` | `preflight.run` | Constraint Lookup/readiness surface: reads relevant decisions and graph-scoped evidence via daemon without implying a governed work gate. |
| `bicameral.context` | `lookup.query` | Requests daemon-authored relevance-time ContextPacket/RecallPacket output for agent and developer workflows. |
| `bicameral.correction_findings` | `lookup.query` | Requests daemon-authored correction-capture findings for PR/agent workflows without MCP-local drift or corpus mutation. |
| `bicameral.lookup` | `lookup.query` | Queries daemon-authored RecallPacket output without MCP-local relevance or authority computation. |
| `bicameral.bind` | `binding.create` | Proposes binding evidence; daemon owns validation and materialization. |
| `bicameral.binding.inspect` | `binding.inspect` | Inspects bindings/evidence through daemon and renders source links/EvidenceReferences without compliance inference. |
| `bicameral.evidence.refresh` | `evidence.refresh` | Requests daemon-owned evidence currentness refresh for a tracked Decision. |
| `bicameral.review.candidates` | `search.query` | Lists daemon-owned decision candidates for review while preserving evidence/source/provenance/rationale fields. |
| `bicameral.review.corpus_proposals` | `lookup.query` | Lists daemon-authored corpus-change proposals/correction findings without MCP-local corpus mutation. |
| `bicameral.review.accept_candidate` | `review.accept_candidate` | Review command, not direct ledger mutation. |
| `bicameral.review.reject_candidate` | `review.reject_candidate` | Review command, not direct ledger mutation. |
| `bicameral.review.promote_candidate` | `recall.promote_decision_candidate` | Requests daemon-governed candidate promotion/routing from a RecallPacket reference. |
| `bicameral.review.request_corpus_change` | `recall.request_correction` | Requests daemon-governed corpus correction/change review from selected RecallPacket items. |
| `bicameral.review.approve_signoff` | `review.approve_signoff` | Review command gated by bot policy. |
| `bicameral.review.reject_signoff` | `review.reject_signoff` | Review command gated by bot policy. |
| `bicameral.review.resolve_compliance` | `review.resolve_compliance` | Review command gated by bot policy and evidence state. |
| `bicameral.review.contradictions` | `governance.inbox.list` | Lists contradiction findings for review via the daemon governance inbox. |
| `bicameral.review.triage_contradiction` | `governance.resolve_contradiction` | Submits contradiction triage updates; daemon owns authorization and state transition. |
| `bicameral.governance.inbox` | `governance.inbox.list` | Lists active governance inbox items. |
| `bicameral.governance.inspect` | `governance.inspect` | Inspects a daemon-authored governance finding. |
| `bicameral.governance.resolve` | `governance.resolve_contradiction` | Resolves, acknowledges, dismisses, or routes a contradiction through daemon governance. |
| `bicameral.history` | `history.list` | Reads replayed/materialized state and renders daemon-provided source links/EvidenceReferences. |
| `bicameral.search` | `search.query` | Searches daemon-owned state and renders daemon-provided source links/EvidenceReferences. |
| `bicameral.request_correction` | `correction.request` | Submits an explicitly approved correction request to the daemon-owned correction path. |

`bicameral.dashboard` is deferred unless bot exposes a local dashboard command or stable URL discovery endpoint. MCP may later add it as convenience only; it must not host the dashboard.

Legacy tools that do not map to the finalized command registry are removed from the core server surface. This includes `bicameral.link_commit`, `bicameral.reset`, `bicameral.judge_gaps`, `bicameral.ratify`, `bicameral.remove_decision`, `bicameral.remove_source`, `bicameral.resolve_collision`, `bicameral.skill_begin`, `bicameral.skill_end`, `bicameral.feedback`, `bicameral.usage_summary`, `bicameral.diagnose`, `validate_symbols`, and `get_neighbors`.

Future versions may reintroduce some behavior only after bot defines a canonical `ToolCommand`, protocol schema, and daemon dispatch path for it.

Removal and erasure behavior is not a thin-client concern. Old MCP `remove_decision` and `remove_source` implementations are deleted. Bot/dashboard work must decide the user-facing removal policy and expose canonical commands before MCP can call it again.

History, search, candidate review, signoff review, and compliance review are not separate MCP subsystems. They are existing bot-owned read/write concepts reached through `ToolRequest` command routing.

## Product Terminology Boundary

MCP-facing language must distinguish lookup, correction capture, grounding,
compliance review, and work gating:

- **Constraint Lookup** retrieves relevant daemon-authored Decisions,
  source links, evidence references, and readiness labels before or during work.
  `bicameral.preflight` currently belongs here even though its bot command name
  remains `preflight.run`.
- **Constraint Correction Capture** collects proposed corpus changes or drift
  findings for daemon-owned review. MCP may request and render these artifacts;
  it does not promote them into canonical truth.
- **Code Grounding** inspects daemon-owned binding and graph evidence. MCP may
  forward hints and render typed evidence states only.
- **Code Compliance** is daemon-owned review state. MCP must not infer
  compliance from lookup, binding, source links, or no-match output.
- **Governed Work Gate** means policy-controlled block/route/interrupt behavior.
  Ordinary `bicameral.preflight`, `bicameral.lookup`, or context packet output
  must not be described as a gate unless the daemon explicitly returns a gate
  decision through a future command.

## Prompts And Skills Boundary

MCP may expose MCP prompts for generic Bicameral workflows that mainly guide the caller to use MCP tools, such as constraint lookup, binding, local ingest, history, and search. These prompts are versioned with the MCP package and must call only supported ToolRequest-backed tools.

MCP does not own repo-local skills. Repo skills are reserved for repo/team behavior: when to run Bicameral in that repository, which ADRs or context files to read, contribution policy, factory attestation, and workflows that span beyond Bicameral MCP.

Bot owns per-user setup and lifecycle. MCP prompts must not install the daemon, migrate state, reset state, run diagnostics, write telemetry, or preserve old setup wizard behavior.

To avoid version desync, MCP performs a daemon capability handshake at startup. The handshake must cover bot daemon version, ToolRequest protocol version, supported command registry, and unsupported commands with reasons. MCP must refuse to start when the daemon's ToolRequest protocol version is unsupported. Once protocol compatibility is established, individual commands may still return daemon capability errors when that command is not implemented.

Repo-local skills, when present, should declare the Bicameral/MCP contract version and required commands they expect.

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
- Perform a startup daemon capability handshake and fail fast on unsupported ToolRequest protocol versions.
- Preserve bot command names exactly.
- Validate only local transport shape before dispatch; bot remains the schema and governance authority.
- Return daemon `ToolResponse` status, result payload, governance result, and request id.
- Represent daemon rejections as successful MCP tool responses with rejected status when the daemon processed the request.
- Represent daemon unavailability, handshake failure, and invalid local MCP arguments as MCP errors.

MCP must not:

- Convert daemon rejection into local acceptance.
- Start against an incompatible daemon protocol and defer the protocol mismatch to individual tool calls.
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

MCP remains a caller/tool surface in all modes. Hosted or local daemon-side reasoning belongs behind bot/cloud ADRs, not inside the MCP refactor spec.

## External Ingest And Egress

`ExternalIngestEnvelope` is separate from `ToolRequest`. MCP is a local tool surface under local actor/session authority, not an external source integration. MCP must not accept arbitrary external-integration payloads and route them as `ToolRequest` authority.

External source adapters, Drive/webhook runtimes, and integration placement are covered by bot gateway/integration ownership. MCP deletes old source adapter code and keeps only local `ingest.submit_local` request mapping.

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
