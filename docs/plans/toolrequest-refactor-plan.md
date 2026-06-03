# ToolRequest Refactor Plan For Bicameral MCP

**Status:** draft implementation plan  
**Date:** 2026-06-03  
**Target repo:** `BicameralAI/bicameral-mcp`  
**Target branch:** `dev`  
**Finalized bot contract:** `BicameralAI/bicameral-bot` `origin/dev` commit `6fe40b9` (`Merge PR #107: feat(protocol): canonical ToolRequest/ToolResponse local tool API (#103)`)  
**Pre-refactor MCP implementation pointer:** `BicameralAI/bicameral-mcp` `dev` commit `0827444c80d45fe3474f68002166e1fc35708eda`

## Refactor Goal

Turn `bicameral-mcp` into a thin MCP client for the local `bicameral-bot` daemon.

The refactor deletes MCP-owned daemon, ledger, graph, dashboard, integration, governance, and source persistence code. If bot does not yet implement a corresponding capability, MCP does not keep the obsolete implementation as a fallback. The old behavior remains recoverable from commit `0827444c80d45fe3474f68002166e1fc35708eda`.

## Source Contracts To Follow

- `bicameral-bot` issue #103: canonical `ToolRequest` local tool API.
- `bicameral-bot` commit `6fe40b9`.
- `bicameral-bot/protocol/README.md`.
- `bicameral-bot/protocol/schemas/v2/tool-request.schema.json`.
- `bicameral-bot/protocol/schemas/v2/tool-response.schema.json`.
- `bicameral-bot/crates/bicameral-api/src/tool_request.rs`.
- ADR-0002: MCP maps to `ToolRequest`; no direct persistence authority.
- ADR-0007: governance flow is substrate-neutral and bot-owned.
- ADR-0010: graph evidence is snapshot-scoped and daemon-owned.
- ADR-0014: caller LLM output is hints/rationale, not authority.

## Phase 0 - Freeze And Mark The Cutover

1. Create a branch from MCP `dev`.
2. Record pre-refactor pointer in release notes and migration docs:
   - `0827444c80d45fe3474f68002166e1fc35708eda`.
3. Add a top-level deprecation notice in docs for removed direct MCP behavior.
4. Confirm package/version strategy for the breaking release.
5. Inventory public tools and classify each as keep, rename, remove, or future bot command.

Expected output:

- Migration note says old v0.2 direct payload compatibility is not preserved.
- Docs point to the old commit for missing removed behavior.

## Phase 1 - Add The Thin ToolRequest Spine

Implement the new MCP core without deleting old code yet:

- `daemon_client.py`: local bot daemon discovery, request submission, timeout handling, health errors.
- `tool_request.py`: request id, issued_at, command construction, schema-aligned payload helpers.
- `authority.py`: `AuthorityContext` assembly with `auth_method=mcp_session`.
- `responses.py`: `ToolResponse` to MCP `TextContent`/structured response formatting.
- `tool_schemas.py`: MCP-facing input schemas for the supported command registry.

Supported command registry:

- `ingest.submit_local`
- `preflight.run`
- `binding.create`
- `binding.inspect`
- `review.accept_candidate`
- `review.reject_candidate`
- `review.approve_signoff`
- `review.reject_signoff`
- `review.resolve_compliance`
- `history.list`
- `search.query`

Tests:

- MCP arguments map to exact bot command names.
- Every request includes `request_id`, `issued_at`, and `AuthorityContext`.
- `audit_metadata.surface == "mcp"` and includes the MCP tool name.
- Daemon rejection is returned as daemon rejection, not hidden as local success.
- Daemon unavailable returns a transport/setup error with operator action.

## Phase 2 - Replace Server Tool Surface

Rewrite `server.py` around the new spine.

Keep or introduce MCP tools:

- `bicameral.ingest`
- `bicameral.preflight`
- `bicameral.bind`
- `bicameral.binding.inspect`
- `bicameral.review.accept_candidate`
- `bicameral.review.reject_candidate`
- `bicameral.review.approve_signoff`
- `bicameral.review.reject_signoff`
- `bicameral.review.resolve_compliance`
- `bicameral.history`
- `bicameral.search`

Remove from core MCP list:

- `bicameral.link_commit`
- `bicameral.update`
- `bicameral.reset`
- `bicameral.judge_gaps`
- `bicameral.resolve_compliance` as a direct write implementation
- `bicameral.ratify`
- `bicameral.remove_decision`
- `bicameral.remove_source`
- `bicameral.resolve_collision`
- `bicameral.dashboard`
- `bicameral.skill_begin`
- `bicameral.skill_end`
- `bicameral.feedback`
- `bicameral.usage_summary`
- `bicameral.diagnose`
- `validate_symbols`
- `get_neighbors`

Notes:

- `bicameral.review.resolve_compliance` replaces the old direct `bicameral.resolve_compliance`.
- `bicameral.review.approve_signoff` / `review.reject_signoff` replace old `bicameral.ratify` semantics.
- `bicameral.dashboard` stays absent until bot exposes a stable dashboard command or URL discovery endpoint.
- `validate_symbols` and `get_neighbors` stay absent because graph evidence belongs to bot.

Tests:

- `list_tools` returns only supported tools.
- Removed tool names fail with unknown tool.
- Supported tools dispatch one daemon request each.
- No supported handler imports `ledger`, `code_locator`, `codegenome`, `dashboard`, or old write handlers.

## Phase 3 - Delete Obsolete Authority Implementations

Remove production code directories that are no longer MCP-owned:

- `ledger/`
- `code_locator/`
- `codegenome/`
- `dashboard/`
- `daemon/`
- `integrations/`
- `governance/`
- `sources/`
- `events/`
- `dlq/`
- `pulse/`
- `notifications/`
- `pii_archive/`
- `secrets_store/`
- source/webhook integration runtimes.

Remove old handler modules after `server.py` no longer imports them:

- `handlers/link_commit.py`
- `handlers/ingest.py` if it owns ingest validation/materialization instead of request mapping.
- `handlers/bind.py` if it owns binding materialization instead of request mapping.
- `handlers/preflight.py` if it reads local ledger/graph directly.
- `handlers/history.py` if it reads local ledger directly.
- `handlers/ratify.py`
- `handlers/remove_decision.py`
- `handlers/remove_source.py`
- `handlers/resolve_collision.py`
- `handlers/resolve_compliance.py`
- `handlers/reset.py`
- `handlers/dashboard.py`
- `handlers/gap_judge.py`
- `handlers/diagnose.py`
- `handlers/sync_middleware.py`

Keep only handler-like modules that perform MCP-to-ToolRequest mapping, or replace them with declarative mapping helpers.

Tests:

- Import smoke test proves deleted modules are not required.
- Static test rejects imports from removed authority packages in production MCP code.
- Wheel/sdist build excludes deleted directories.

## Phase 4 - Simplify State, Config, And Install

Update install/setup behavior so MCP registers itself as a client of bot:

- Remove setup writes for `.bicameral/ledger.db`, `.bicameral/events`, local SurrealDB state, code indexes, dashboard assets, and ledger replay hooks.
- Keep only MCP server registration and any client-local config needed to locate the bot daemon.
- Require or guide installation of `bicameral-bot` local daemon when missing.
- Replace direct smoke tests with daemon connectivity and ToolRequest round trip smoke tests.

Configuration should include only:

- bot daemon endpoint or discovery mode;
- MCP actor/session identity derivation;
- workspace root selection;
- timeout/retry settings;
- optional logging/diagnostic verbosity.

## Phase 5 - Align Protocol Tests With Bot Fixtures

Vendor no protocol authority into MCP. Instead:

1. Add tests that load bot v2 schema fixtures from a pinned path, package artifact, or test fixture copy with source commit noted.
2. Validate MCP-generated requests against `tool-request.schema.json`.
3. Validate daemon-shaped mocked responses against `tool-response.schema.json`.
4. Include negative tests for forbidden external ingest authority paths only as compatibility checks against bot fixtures, not as MCP-owned ingest validation.

Minimum fixture coverage:

- `mcp-to-tool-request-ingest.json`
- `mcp-to-tool-request-preflight.json`
- `mcp-to-tool-request-review.json`
- invalid external ingest review command
- invalid external ingest authority injection
- invalid external ingest compliance resolution

## Phase 6 - Documentation And Release Notes

Update:

- `README.md`
- `CHANGELOG.md`
- `SECURITY.md` if it names old local state or trust boundaries.
- setup docs and slash-command docs.
- MCP tool reference.

Docs must state:

- `bicameral-mcp` is a local MCP client for `bicameral-bot`.
- Bot owns protocol, governance, event materialization, graph evidence, dashboard, and integrations.
- Old direct payload compatibility is broken by design.
- Removed behavior can be inspected at `0827444c80d45fe3474f68002166e1fc35708eda`.
- Missing bot-backed behavior is intentionally unavailable in MCP until bot exposes it through `ToolRequest`.
- Hosted reasoning, local OpenAI-compatible endpoints, and local CLI adapters are provider choices behind the bot/cloud reasoning boundary, not MCP-owned authority.
- Hosted reasoning should be described as managed reasoning consistency: shared prompt/model policy, versioned advisory artifacts, audit metadata, and per-tenant memoization for repeated source snapshots.
- Memoization reduces duplicate inferred decisions and hosted model cost, but does not make LLM output canonical truth.

## Phase 6A - Reasoning Provider ADR Follow-Ups

Open or update ADRs outside this MCP repo before implementing hosted/local reasoning configuration:

- `bicameral-bot`: configurable reasoning providers, deterministic fallback requirements, model configuration dashboard surface, local OpenAI-compatible endpoint support, local CLI adapter support, and advisory-only reasoning artifacts.
- `bicameral-bot`: reasoning provider output must enter governance as candidates, hints, rationale, summaries, or advisory artifacts; it cannot bypass `ToolRequest`, graph validation, review command gating, or event-store materialization.
- `bicameral-cloud`: hosted reasoning service ownership for model routing, auth, tenant/workspace scoping, quotas, billing, redaction policy, telemetry, audit metadata, and cache/memoization semantics.
- `bicameral-cloud`: per-tenant reasoning artifact memoization keyed by tenant, workspace, source snapshot hash, normalized input hash, task, model policy version, prompt version, schema version, and redaction policy version.
- `bicameral-cloud`: immutable artifact/version policy so prompt/model/schema changes create new advisory artifacts rather than rewriting prior reasoning history.
- `bicameral-cloud`: hosted model availability must degrade cleanly to caller, local endpoint, local CLI, or disabled modes.

## Phase 7 - Validation Gate

Run:

- unit tests for request mapping, authority, daemon client, and response formatting;
- schema validation tests;
- import boundary tests;
- package build;
- MCP smoke test against mocked daemon;
- MCP smoke test against a real local bot daemon when available.

Acceptance criteria:

- No production import path references removed authority modules.
- `list_tools` exposes only the supported ToolRequest-backed surface.
- Every supported tool dispatches exactly one canonical `ToolRequest`.
- MCP never writes Bicameral ledger/event/graph/dashboard state directly.
- README and release notes name the breaking contract change.

## Open Bot Follow-Ups

These are not reasons to keep old MCP implementations:

- Runtime `ToolRequest` dispatch wiring in `bicameral-runtime`.
- Final reconciliation between bot `ReviewCommandKind` and `ToolCommand` variants.
- Bot dashboard URL discovery command, if `bicameral.dashboard` returns later.
- Bot graph validation endpoints for any richer binding inspection behavior.
- Bot-owned install/onboard command that MCP setup can call or detect.
- Bot model configuration UI for hosted Bicameral, local OpenAI-compatible endpoints, and local CLI adapters.
- Cloud hosted reasoning service with per-tenant memoized `ReasoningArtifact` storage and quota/cost controls.

Until those land, MCP returns clear daemon capability errors or omits unsupported tools.

## PR Slicing

Recommended sequence:

1. Thin `ToolRequest` client spine with tests.
2. Server surface replacement and tool list contraction.
3. Obsolete code deletion and import-boundary tests.
4. Setup/install simplification.
5. Docs, release notes, and package cleanup.

Do not mix new bot runtime implementation into the MCP refactor PR. Bot dispatch belongs in `bicameral-bot`.
