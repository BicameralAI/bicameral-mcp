# Doctrine: Deterministic Governance

**Status**: Active · **Last reviewed**: 2026-06-26

This document declares the governance doctrine for bicameral-mcp: how
process rules are authored, consumed, and enforced.

## Core principle

Governance rules are deterministic, auditable, and machine-enforceable.
Rules that cannot be verified by CI or code are documented as
"skill-text orchestration, not enforced" in the
[compliance stance matrix](compliance-stance-matrix.md).

## Layers

1. **Shared process (bic-logic)**: Owned upstream in `bicameral-factory`;
   consumed here by pin/hash. The one mandatory layer. See
   [BOUNDARY.md](BOUNDARY.md).

2. **Local process**: Per-operator tooling (registered siblings). Never
   committed; leak-guarded by `scripts/validate_governance_boundary.py`.

3. **MCP authority boundary**: MCP owns agent workflow UX; bot daemon owns
   ledger, governance, and storage authority. Defined in ADR-0001 (accepted).

## Enforcement

- **Pre-commit + CI**: `validate_governance_boundary.py` enforces sibling
  leak rules and governance-directory allowlist.
- **Approval gates**: Destructive/privacy-sensitive operations require
  explicit single-use human approval (`approval_gate.py`, `erasure_gate.py`).
- **Bot boundary**: MCP cannot write to ledger, modify governance state,
  or bypass daemon authority. All mutations route through ToolRequest.

## What is NOT enforced

- qor-logic `build_manifest` / `HumanOversight` / `OverrideFriction`
  gates exist only as skill-text; not vendored into this repo as code.
- Host-level UX confirmation (e.g., override friction) is externalized
  to the MCP host application and not server-enforceable.
- These non-enforced items are documented honestly in the compliance
  stance matrix rather than claimed as enforced.

## Related

- [BOUNDARY.md](BOUNDARY.md) — governance boundary contract
- [compliance-stance-matrix.md](compliance-stance-matrix.md) — compliance posture
- `docs/adr/0001-agent-tool-surface.md` — MCP/bot authority boundary
- `SECURITY.md` — trust boundary and threat model
