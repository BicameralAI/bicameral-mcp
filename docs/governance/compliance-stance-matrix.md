# Compliance Stance Matrix

**Status**: Active · **Last reviewed**: 2026-06-26
**System**: bicameral-mcp (thin MCP client for bicameral-bot daemon)
**Deployment scope**: Single-operator local install (OS user = trust boundary)

This matrix declares the project's compliance posture for each framework.
Claims are grounded in cited evidence; nothing is asserted as enforced unless
code or CI proves it. See `docs/COMPLIANCE_AUDIT_2026-06-03.md` for the
full audit.

---

## Framework Posture

| Framework | Scope / Tier | Posture | Evidence | Notes |
|---|---|---|---|---|
| SOC 2 (CC1-CC9) | System description: single-operator local install | **Scoped** | `SECURITY.md:17`; `docs/policies/threat-model-and-trust-boundary.md` | CC6.1 satisfied by scope reduction (no app-layer auth); see Access Control below |
| SOC 2 (Availability) | Local single-tenant | **Met** | `docs/sla.md`; `docs/policies/ledger-export.md` | Scoped no-SLA stance |
| SOC 2 (Confidentiality C1.x) | PII disposal | **Partial** | `pii_archive/store.py`; `erasure_gate.py`; this matrix | MCP ships erase-subject approval gate; daemon owns archive; see PII Disposal below |
| SOC 2 (Privacy P3.x) | Third-party PII | **Partial** | See Lawful Basis below | Consent covers telemetry only; third-party PII documented as legitimate-interest |
| OWASP Top 10 (Web+LLM) | All controls | **Met/Partial** | `docs/COMPLIANCE_AUDIT_2026-06-03.md` §4a | SurrealQL injection remediation tracked separately (#549 sibling) |
| NIST AI RMF | GOVERN/MAP/MEASURE/MANAGE | **Partial** | `GOVERNANCE_INDEX.md`; ADRs; this matrix | MANAGE-1.1 human-oversight: see Human Oversight below |
| NIST SSDF | PO/PS/PW/RV | **Met** | Supply chain, signed releases, SBOM | Action SHA-pinning tracked separately |
| GDPR | Art.5-50 | **Partial** | See PII Disposal, Lawful Basis below | Limited to in-repo scope; operator-side obligations documented |
| EU AI Act | Limited-risk (Art.50 binds) | **Met (limited-risk)** | No Annex III high-risk use; Art.50 transparency via provenance | Arts.9/10/12/14/15 voluntary alignment |

---

## Access Control — CC6.1 Scope Statement

**Load-bearing scope reduction**: bicameral-mcp is a single-operator local
install. The trust boundary is the OS user account. There is no app-layer
authentication or authorization because the deployment model does not require
it.

- **Single-operator mode** (current, in audit scope): CC6.1 is satisfied by
  the OS-user trust boundary. No network listener; stdio transport only.
  Evidence: `SECURITY.md:17`, `docs/policies/host-trust-model.md`.

- **Team/hosted mode** (Track 2, NOT in audit scope): requires an auth shim
  before deployment enters scope. **Status: NOT SHIPPED.** No team/hosted
  access control is enforced. Do not claim otherwise.

- **Track 2 auth shim status**: Implementation deferred. The smallest viable
  shim (bearer-token validation on the daemon HTTP surface) is defined but
  not shipped. Team-mode activation MUST NOT proceed without this gate.
  Tracked as future work; no timeline commitment.

---

## PII Disposal — GDPR Art.17 / SOC 2 C1.x

**MCP-side posture** (this repo):

- `bicameral.privacy.erase_subject.approve` — single-use approval gate for
  erasure requests. Fail-closed: no fallback, no inline storage on failure.
- `bicameral.privacy.erase_subject` — routes to daemon `privacy.erase_subject`
  command after approval is consumed.
- MCP does not own PII storage; it routes to the daemon's `PiiArchive`.
- `authority.py` captures only OS username and hostname for audit metadata;
  no email addresses are collected at the MCP layer.

**Daemon-side posture** (bicameral-bot, not this repo):

- `PiiArchive` storage-segregation design: `decision.speakers` / `source_ref`
  routed to erasable archive (Phase B-2).
- `archive.put()` failure: fail-closed (must not fall back to inline storage).
- Erase-subject CLI: wired to `PiiArchive.erase_by_predicate`.
- Removal event snapshots: `source_ref` / `speakers` scrubbed/pseudonymized.
- Team-mode event author: `_resolve_signer_email` applied.
- Legacy-row backfill: required before full Art.17 compliance.

**Residual**: Full erasure depends on daemon-side Phase B-2 completion.
MCP provides the approval surface and routing; daemon owns the storage.

---

## Lawful Basis — GDPR Art.6/7 / SOC 2 Privacy P3.x

### Third-party PII ingest

**Lawful basis**: Operator-controller legitimate interest (Art.6(1)(f)).

The operator who installs bicameral-mcp is the data controller. When the
operator ingests third-party PII (e.g., decision participant names from
integrations, GitHub commit-author emails), the lawful basis is the
operator's legitimate interest in maintaining an accurate engineering
decision record.

**Controller-side obligations** (operator responsibility):

1. **Notice**: The operator must provide appropriate notice to data subjects
   whose PII is ingested, per Art.13/14. This is a controller obligation,
   not a processor obligation. bicameral-mcp does not provide notice on
   behalf of the operator.

2. **Minimization** (Art.5(1)(c)): The system minimizes PII at ingestion
   where architecture permits:
   - MCP `authority.py`: captures OS username only, not email.
   - Daemon-side: `_resolve_signer_email` minimizes to username where
     full email is not required.
   - Integration emails (e.g., GitHub commit authors): captured only when
     the source adapter requires attribution. Minimization is a daemon-side
     responsibility; MCP does not see raw integration data.

3. **Consent**: No separate consent gate exists for third-party PII ingest.
   The lawful basis is legitimate interest, not consent. If the operator's
   jurisdiction requires consent (e.g., for special categories), the
   operator must obtain it independently.

### Telemetry

Outbound telemetry consent is gated by `consent.py` (first-boot notice,
opt-out). Telemetry payloads are string-stripped of PII before transmission.
PostHog region and cross-border basis: documented in the operator-facing
deployment guide. SCCs apply where PostHog processes in non-EEA regions.

---

## Human Oversight — NIST AI RMF MANAGE-1.1 / EU AI Act Art.14

**Honest posture**: Skill-text orchestration with MCP approval gates.
Not fully vendored/enforced as CI-verifiable gates.

**What IS enforced in this repo** (code-backed):

- `approval_gate.py` — single-use scoped approval for `request_correction`
  submissions. Human must approve before any correction is submitted.
- `erasure_gate.py` — single-use scoped approval for PII erasure.
  Fail-closed; no fallback.
- ADR-0001 (accepted) — agent tool surface boundary: MCP owns agent UX,
  bot owns governance. Agent cannot bypass bot authority.
- Destructive-action scaffolding: `remove_decision`, `reset` handlers
  require explicit invocation (no autonomous execution).

**What is NOT enforced in this repo** (skill-text only):

- `build_manifest` / `HumanOversight` / `OverrideFriction` gates exist
  only in non-vendored qor-logic skill markdown. No `.py` implementation
  in this repo. By the project's own doctrine, this is unenforced.
- Override-friction: externalized to MCP host UX; not server-enforceable.

**Stance**: The compliance-stance claims for NIST AI RMF MANAGE-1.1 and
EU AI Act Art.14 are limited to the code-backed gates listed above. The
non-vendored skill-text orchestration is acknowledged as non-enforced.
Vendoring qor-logic scripts into the repo would upgrade this stance but
is not required for the current limited-risk classification.

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-06-26 | Initial matrix: CC6.1 scope, PII disposal, lawful basis, human oversight | Compliance remediation PR (umbrella #549) |
