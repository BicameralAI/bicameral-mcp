# Alex Alignment Delivery

This file maps Alex-aligned agent and operator workflow requirements to implementation issues in `bicameral-mcp`.

MCP exposes Bicameral context and review actions to agents and developer workflows. Core authority remains in `bicameral-bot`. Stable contracts remain in `bicameral-sdk`.

## MCP Delivery Issues

| Workflow Surface | Issue | Status | Notes |
|---|---|---|---|
| Pre-flight constraint context | #613 | Planned | Agents and developer tools can request relevant constraints before implementation begins. |
| Decision review and contradiction triage actions | #614 | Planned | Operators and authorized workflows can review candidates and triage findings without bypassing core authority. |

## Boundary

MCP owns:

- Tool and resource surfaces
- Agent-facing pre-flight context access
- Operator-facing review actions
- Contradiction triage actions
- Payload presentation through SDK contracts

MCP does not own:

- Canonical decision authority
- Constraint graph storage
- Customer-specific approval chains
- Customer-specific dashboards
- Connector ingest policy
