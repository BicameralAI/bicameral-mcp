# ADR-0001: Agent Tool Surface

**Date:** 2026-05-27  
**Status:** accepted  
**Level:** L1

## Problem

Coding agents need a direct local tool surface for preflight, ingestion, and review actions. Putting source-specific adapters or canonical storage logic in MCP would conflate agent UX with system authority.

## Decision

MCP owns local agent workflow tools: ingest, preflight, bind, review command emission, review-state query, and explanation. It calls into `bicameral-bot` and uses `bicameral-bot/protocol/` contracts.

## Non-Goals

MCP does not own external source adapters, storage adapters, local daemon governance, or cloud code graph infrastructure.

## Consequences

Agent UX can evolve independently while the bot remains the governance boundary.
