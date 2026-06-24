# Recorded bot ToolResponse contract fixtures

One JSON file per canonical ToolRequest command (the values of
`tool_request.MCP_TOOL_COMMANDS`). Each file is a representative
`ToolResponse` payload as the bicameral-bot daemon returns it over
`POST /v2/tool-requests`.

These fixtures are the **deterministic replay corpus** for mcp#555: they let
the thin client's response renderers (`responses.format_tool_response`,
`responses.format_preflight_response`) be validated against realistic daemon
output with no live daemon and no LLM in the loop.

Grounding: the typed-state vocabulary (`source_only`, `graph_grounded`,
`stale`, `ambiguous`, `not_indexed`, `snapshot_mismatch`, `unsupported`,
`not_found`, deferred compliance, unsupported binding search scope) follows
`bicameral-bot/docs/specs/bot-mcp-data-flow-runtime-architecture.md`
(the merged #255 V1 runtime / Ledger View consolidation).

When the daemon contract changes, update these fixtures in the same PR — they
are the contract surface, not throwaway test data.
