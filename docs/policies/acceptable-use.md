# Acceptable use — intended purpose & prohibited uses

**Status**: active
**Closes gaps**: NIST-RMF-01 (NIST AI RMF MAP-3.1 / GOVERN-3.1), AI-ACT-02 (EU AI Act Annex III) per `docs/research-brief-compliance-audit-2026-05-06.md` § 2.5, § 2.6
**Doctrine**: #205 deterministic-governance hard rule

## Intended purpose

bicameral-mcp is a developer-tool MCP server for code-decision tracking and drift detection. Its intended purpose:

- Capture engineering decisions made in conversations (meetings, chat, design docs) and bind them to code artifacts via deterministic search + agent-LLM verification.
- Surface drift between captured decisions and the current code state when files change.
- Provide a deterministic gate (preflight) that brings prior decisions into agent context before code-implementation prompts.

bicameral-mcp is **limited-risk AI** per EU AI Act Annex III categorization: a decision-support system for software engineering. It is **not high-risk AI** under Annex III (not employment, education, essential services, law enforcement, biometric identification, etc.).

## Prohibited uses

Do NOT use bicameral-mcp for the following purposes. This list is non-exhaustive; operators are responsible for evaluating their own deployment context against intended-purpose boundaries.

### 1. Substitution for HR, legal, medical, or financial decision-making

bicameral-mcp's outputs are decision-support artifacts for software engineering — **not** automated decisions affecting people's employment, legal standing, medical diagnosis, financial creditworthiness, or any similar regulated domain. Limited-risk AI boundary statement per EU AI Act and NIST AI RMF MAP-3.1.

### 2. Ingestion of regulated data classes

Do not ingest content containing:

- **Protected Health Information (PHI)** — patient identifiers, medical record numbers, clinical narratives. Server-side detect-and-refuse is in place (#213 / HIPAA-01 fold), but operators must understand the boundary: if PHI reaches `bicameral.ingest`, the server refuses; do NOT route PHI through bicameral-mcp at all.
- **Cardholder data (PAN)** — primary account numbers, CVV, full magnetic stripe data. Server-side Luhn-validating detect-and-refuse is in place (#213 / PCI-01 fold), but the same posture applies: do NOT route PAN through bicameral-mcp.
- **Other regulated-data classes** — student records (FERPA), financial records subject to GLBA, EU personal data subject to GDPR Art. 9 special-category processing, etc. If your deployment context regulates the data, bicameral-mcp is not the channel for it.

### 3. Multi-tenant deployment without an auth shim

Do not deploy bicameral-mcp on a shared multi-tenant filesystem (e.g., shared dev VM, shared CI runner with multiple operators) without an authentication shim. The current substrate assumes single-tenant operator control:

- The local SurrealDB ledger (`~/.bicameral/ledger.db` or `surrealkv://`) is filesystem-permission-gated only.
- The MCP transport (stdio) does not enforce caller identity — any process that can spawn the server can issue tool calls.
- Cross-tenant data leakage on a shared install is the operator's responsibility to prevent.

The team-server activation track addresses this (cross-developer correlation needs server-side auth); until then, single-tenant deployment is the supported posture.

### 4. Automated decisions affecting people without human-in-the-loop review

Do not use bicameral-mcp's outputs to drive automated decisions about people without an explicit human-in-the-loop (HITL) review step. The preflight gate is a context-surfacing primitive; it is not a decision-making oracle. Outputs are advisory; operators reviewing the surfaced context are the deciders.

## Cross-framework mapping

| Prohibited use | Driving framework | Specific reference |
|---|---|---|
| HR/legal/medical/financial substitution | NIST AI RMF MAP-3.1; EU AI Act Annex III | Limited-risk AI boundary statement |
| PHI ingestion | HIPAA Privacy Rule § 164.502 | Boundary statement #213 |
| PAN ingestion | PCI DSS 4.0 Req 3, 4 | Boundary statement #213 |
| GDPR special-category data | GDPR Art. 9 | Operator-side scope decision |
| Multi-tenant deployment without auth | SOC 2 CC6.1 logical access controls | Multi-tenancy boundary (deferred to team-server activation) |
| Automated decisions without HITL | NIST AI RMF MEASURE-2.1; EU AI Act Annex III safeguards | Decision-support-not-decision-making boundary |

## Deferred deployment-profile matrix

A per-deployment-tier acceptable-use matrix (single-developer / team / hosted) is a deferred follow-up per the brief's Codex-2 review item #2. When that matrix lands, this document gets a "Deployment-tier deltas" section linking each tier's specific acceptable-use scoping. Until then, the prohibited-uses list above is the universal scope across all current deployment tiers.

## Cross-references

- Research brief: § 2.5 NIST-RMF-01, § 2.6 AI-ACT-02
- Boundary statements: #213 (LLM-04 + HIPAA-01 + PCI-01 fold), #226 / `docs/sla.md` (deployment trust boundary)
- Doctrine: #205 (deterministic-governance hard rule)
