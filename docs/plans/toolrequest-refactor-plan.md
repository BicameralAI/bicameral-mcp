# ToolRequest Refactor Plan For Bicameral MCP

**Status:** draft implementation plan  
**Date:** 2026-06-04  
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

## Grilled Bot RFQ Shape

This MCP refactor depends on a reduced bot RFQ set. These RFQs are tracked in `bicameral-bot`; MCP must not preserve old implementations while waiting for them.

1. **Wire `ToolRequest` commands into the local daemon read/write path** (`@jinhongkuan`).
   - Covers runtime command dispatch, policy-shaped routing, `history.list`, `search.query`, candidate review, signoff review, compliance review, and retirement/mapping of old `ratify` and collision handlers.
2. **Wire local `ingest.submit_local` through `ToolRequest`** (`@Knapp-Kevin`, review by `@jinhongkuan`).
   - Covers local MCP-triggered source/session evidence, candidate creation, and refusal semantics.
   - Does not create a new source-adapter RFQ; external ingest remains under existing Gateway/integration ownership.
3. **Complete bot-owned binding evidence** (`@silongtan`).
   - Covers `binding.create`, `binding.inspect`, snapshot mismatch handling, validation tokens, Rust symbol resolver integration, and materialized `BindingEvidence`.
4. **Complete grounded preflight and compliance readiness** (`@silongtan`).
   - Covers `preflight.run`, graph readiness, relevant decisions with binding/evidence states, compliance state visibility, and unknown/stale/unsupported warnings.
5. **Dashboard and removal UX** (`@jinhongkuan`).
   - Covers Ledger View gaps plus remove-source/remove-decision/erasure product behavior.
   - Old MCP removal handlers are deleted until bot exposes canonical commands.
6. **MCP package retirement boundary** (`@jinhongkuan`, review by `@Knapp-Kevin` and `@silongtan`).
   - Covers setup, CLI wrappers, MCP prompts versus repo skills, diagnose/update/usage/feedback telemetry, release/install behavior, and migration notes.
7. **Strip MCP to the ToolRequest thin client** (`@jinhongkuan`, review by `@silongtan` and `@Knapp-Kevin`).
   - Covers final deletion, request mapping, daemon client, schema tests, package cleanup, and the pre-refactor pointer.

No standalone RFQ is created for event-store substrates, team sync, ledger materialization, history/search, review commands, collision resolution, external source adapters, dashboard-only migration, CLI-only migration, skills-only migration, or telemetry-only migration. Those areas are either already covered by existing bot ADR/code or folded into the RFQs above.

## Phase 0 - Freeze And Mark The Cutover

1. Create a branch from MCP `dev`.
2. Record pre-refactor pointer in release notes and migration docs:
   - `0827444c80d45fe3474f68002166e1fc35708eda`.
3. Add a top-level deprecation notice in docs for removed direct MCP behavior.
4. Confirm package/version strategy for the breaking release.
5. Inventory public tools and classify each as keep, rename, remove, future bot command, or bot/dashboard product decision.

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
- `prompts.py`: MCP prompts for generic Bicameral workflows that call supported ToolRequest-backed tools.

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
- Prompt-guided workflows check daemon capabilities before recommending or dispatching tool calls.
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

Keep or introduce MCP prompts for generic Bicameral workflows when they are thin recipes over the supported tools:

- preflight workflow;
- binding workflow;
- local ingest workflow;
- history/search workflow.

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
- `bicameral.remove_decision` and `bicameral.remove_source` stay absent because removal/erasure is a bot dashboard product decision, not a transport fallback.
- `bicameral.resolve_collision` stays absent unless bot introduces a canonical review command beyond existing candidate/signoff/compliance review commands.

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

External source adapters and team sync code are deleted from MCP even if bot/integration parity is incomplete. Existing bot Gateway/integration ownership governs those gaps; MCP keeps the pre-refactor pointer for recovery.

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
- Move setup, CLI wrappers, slash-command skills, diagnose/update/usage/feedback telemetry, and release/install behavior into the MCP package retirement boundary. Keep only registration and smoke-test behavior that proves MCP can reach the bot daemon.
- Reclassify old slash-command skills:
  - Move generic Bicameral tool workflows into MCP prompts versioned with the MCP package.
  - Leave repo/team behavior as repo-local skills outside MCP, such as repository trigger rules, ADR/context loading, contribution policy, and factory attestation.
  - Delete skills that preserve old setup, diagnose, reset, update, telemetry, direct ledger writes, direct binding writes, or obsolete tool names.

Configuration should include only:

- bot daemon endpoint or discovery mode;
- bot daemon version, ToolRequest protocol version, and supported command registry from capability handshake;
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
- Local `ingest.submit_local` execution behind the daemon.
- Complete binding evidence creation/inspection behind the daemon.
- Grounded preflight/compliance readiness behind the daemon.
- Dashboard removal/erasure UX and canonical removal commands, if any.
- Final reconciliation between bot `ReviewCommandKind` and `ToolCommand` variants.
- Bot dashboard URL discovery command, if `bicameral.dashboard` returns later.
- Bot graph validation endpoints for any richer binding inspection behavior.
- Bot-owned install/onboard command that MCP setup can call or detect.

Until those land, MCP returns clear daemon capability errors or omits unsupported tools.

## PR Slicing

Recommended sequence:

1. Thin `ToolRequest` client spine with tests.
2. Server surface replacement and tool list contraction.
3. Obsolete code deletion and import-boundary tests.
4. Setup/install simplification.
5. Docs, release notes, and package cleanup.

Do not mix new bot runtime implementation into the MCP refactor PR. Bot dispatch belongs in `bicameral-bot`.
