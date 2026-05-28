# ADR-0001: MCP Repository Boundary in the Open-Core Split

Status: accepted

Date: 2026-05-27

## Context

Bicameral is splitting into public local bot, public protocol, public integrations, public MCP tools, and private cloud/oracle. `bicameral-mcp` already exists as the agent-facing local tool surface.

## Decision

`bicameral-mcp` owns the agent-facing MCP server and commands that let Claude/Codex/other agents interact with Bicameral:

- ingest candidates from agent sessions
- run local preflight
- request local binding/grounding
- emit review commands
- query review state
- call into `bicameral-bot` local runtime and `bicameral-protocol` objects

It does not own:

- source-specific Jira/Linear/Slack/etc. integrations (`bicameral-integrations`)
- canonical storage writes (`bicameral-bot` storage adapters)
- organization-scale code graph or conflict oracle (`bicameral-cloud`)
- protocol schema authority (`bicameral-protocol`)

## Invariant

MCP tools emit protocol-shaped commands and evidence. They do not bypass governance policy or create canonical authority directly.
