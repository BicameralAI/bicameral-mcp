# ADR-0002: MCP Emits Review Commands, Not Writes

**Date:** 2026-05-27  
**Status:** proposed  
**Level:** L1

## Problem

Agents can act quickly and confidently. If MCP tools write canonical state directly, agent mistakes become durable cognitive debt.

## Decision

MCP emits `ReviewCommand`, `SourceEvidence`, `DecisionCandidate`, `BindingHint`, or `BindingEvidence` objects. The local bot validates commands, applies governance policy, and materializes accepted events through storage adapters.

## Consequences

Agent-generated claims remain reviewable and auditable. Weak evidence routes to HITL.
