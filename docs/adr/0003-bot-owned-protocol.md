# ADR-0003: Use Bot-Owned Protocol Contracts

**Date:** 2026-05-27  
**Status:** proposed  
**Level:** L1

## Problem

The standalone protocol repo has been recombined into `bicameral-bot/protocol/`. MCP still needs stable contracts, but should not own schema authority.

## Decision

MCP consumes protocol contracts from `bicameral-bot/protocol/` and treats them as the compatibility boundary for tool input/output.

## Non-Goals

This ADR does not make MCP a submodule of the bot.

## Consequences

Schema drift is reduced because contracts are reviewed with the local authority boundary that enforces them.
