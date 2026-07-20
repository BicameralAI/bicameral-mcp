# Release Manifest Adoption: Bicameral MCP

Status: descriptor CI implemented
Related Factory blueprint: https://github.com/BicameralAI/bicameral-factory/pull/290

## Purpose

Defines MCP's role as a thin ToolRequest consumer in the incremental release-manifest evidence model.

## MCP-owned release inputs

For an eligible MCP build, provenance and MCP-facing interface fingerprint cover packaged MCP artifact digest, ToolRequest/ToolResponse and capability compatibility range, host/runtime configuration affecting the actual MCP-to-Bot path, and product-distributed hook/package version when it participates in the journey. MCP must not become a Factory runtime dependency, canonical-state writer, release approver, or deployment controller.

## Journey closure

Integration adapter/config + Bot integration ingress + Bot MCP ToolRequest interface + MCP artifact/protocol.

A receipt is reusable only when the complete declared closure and topology-profile version match. An MCP or Integrations change therefore does not force a Bot-to-Cloud browser rerun unless it changes a declared dependency in that distinct closure.

## Delivery gates

1. MCP CI emits artifact provenance and deterministic Bot-facing contract fingerprint.
2. Release assembly validates MCP against exact pinned Bot interface.
3. Real-process Integration-to-Bot-to-MCP validation records the pinned closure.
4. Protocol drift fails visibly without fixture or legacy-success fallback.

## Authority boundaries

- MCP remains a thin client and renderer of Bot-owned results.
- Receipts and manifests are operational records, not ToolRequest authority or Decision lifecycle state.
- Factory doctrine remains development-time only, never an MCP product/runtime dependency.

## Non-goals

No new MCP lifecycle/persistence state or hosted-dashboard implementation. Descriptor CI is component provenance, not terminal journey evidence.
