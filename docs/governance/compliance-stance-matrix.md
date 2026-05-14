# Compliance Stance Matrix (#205)

Project-wide stance on every privacy / security / compliance standard that could plausibly apply to bicameral-mcp's footprint. For each row: **Standard**, **Applies?**, **Project stance**, **Gate** that enforces the stance.

A "we don't process X" stance is fine if a deterministic gate enforces it. See `docs/governance/doctrine-deterministic-governance.md` for the rule: skill-text instructions are suggestive but never qualify as governance.

This matrix is reviewed annually; the cadence is wired into `/qor-compliance-review` (deferred to #205 Phase 4).

## Matrix

| Standard | Applies? | Project stance | Gate |
|---|---|---|---|
| **GDPR** (EU 2016/679) | Yes (any EU end user or operator) | Process minimum personal data. Operator's git email is the only PII routinely persisted; `signer_email_fallback` policy lets the operator opt to local-part-only or full-redact. Right-to-erasure via ingest filtering + storage segregation, NOT tombstone/rehash on the append-only chain. | `context.py:_resolve_signer_email()` env-driven mode; `handlers/ingest.py` PII detect-and-refuse; #221 (deferred) for full erasure procedure |
| **CCPA / CPRA** (California) | Yes (any California end user) | Same data-minimization commitments as GDPR. No selling of personal data; no behavioral advertising. | Inherits GDPR gates above |
| **SOC 2 Type I → Type II** | Target (B2B sales gate) | Pursuing Type I evidence collection in 2026; Type II ramp after first Type I report. Five trust principles: security, availability, processing integrity, confidentiality, privacy. | Multi-issue track — see `#215` (transport trust boundary), `#292` (supply-chain sigstore), `#227` (structured audit log) |
| **NIST CSF 2.0** | Yes (overall alignment) | Align with the six CSF functions: Govern, Identify, Protect, Detect, Respond, Recover. Govern function tracked in `/qor-` skills + this doctrine. | This doctrine + audit gates |
| **NIST AI RMF (AI 100-1)** | Yes (we ship an LLM-tool surface) | Already partially referenced in `qor/references/doctrine-ai-rmf.md` (qor-logic package). MAP / MEASURE / MANAGE / GOVERN functions: `/qor-audit` runs plan-time MAP; runtime MEASURE deferred to follow-up | `/qor-audit` Step 1c (impact assessment) + `governance` plan field |
| **NIST SSDF (SP 800-218)** | Yes (we ship developer tooling) | Secure SDLC alignment: protect software, produce well-secured software, respond to vulnerabilities. | `/qor-audit` Step 3 OWASP pass; supply-chain via #292 |
| **FIPS 140-3** | Partial (cryptographic surfaces only) | Ledger Merkle hashing uses SHA-256 (FIPS-approved). Sigstore signing uses Ed25519 (Suite B). No other cryptographic operations. | `ledger/adapter.py` SHA-256 declaration; `release/manifest_verify.py` sigstore verification |
| **OWASP Top 10 (2021/2025)** | Yes | A01 Broken Access Control (admin panel env flag + origin lock); A03 Injection (SQL via parameterized queries, no shell exec); A04 Insecure Design (audit gates, confirm-first destructive ops); A05 Misconfig (defaults safe); A06 Vulnerable Components (SHA-pin GitHub actions per #272); A08 Software Integrity (sigstore-signed manifests). | `/qor-audit` Step 3; #272 (action pinning); #292 (sigstore) |
| **OWASP LLM Top 10** | Yes (we ship an LLM-tool surface) | LLM01 Prompt Injection (ingest canary scanner #212; brief renderer fence-isolation #278 Phase 1); LLM02 Insecure Output Handling (escape user-supplied text in dashboard via `.textContent`); LLM06 Sensitive Info Disclosure (PII/PHI/PAN detect-and-refuse #213); LLM10 Model DoS (rate limit on ingest). | `handlers/ingest.py` canary + sensitive-pattern detectors; #278 fence isolation; ingest rate limit |
| **OWASP ASVS Level 2** | Target (production deployment minimum) | Verification requirements for the dashboard HTTP surface, ingest path, ledger queries. Pursuit deferred to a focused cycle; intermediate gains land via OWASP Top 10 + LLM Top 10 work. | Future cycle |
| **EU AI Act** | Partial — limited-risk classification likely | The bicameral-mcp surface is an AI-orchestration tool; we are NOT a high-risk system per Annex III. Customer's downstream use may be high-risk; `/qor-plan`'s `high_risk_target` flag declares per-plan. | `qor/references/doctrine-eu-ai-act.md` (qor-logic package); `/qor-plan` Step 1c |
| **HIPAA** | **No** | bicameral-mcp does NOT process Protected Health Information. Ingest pipeline refuses payloads matching PHI patterns (medical record numbers, common PHI field names). Operators must not ingest clinical content; use HIPAA-compliant tooling for medical data. | `handlers/sensitive_patterns.py` PHI patterns; `_IngestRefused` with `sensitive_data:phi` reason |
| **PCI DSS** | **No** | bicameral-mcp does NOT process cardholder data. Ingest pipeline refuses Luhn-valid 13-19 digit sequences not preceded by ID-class labels. Operators must use PCI-compliant systems for cardholder data. | `handlers/sensitive_patterns.py` PAN patterns; `_IngestRefused` with `sensitive_data:pan` reason |
| **ISO 27001 / 27701** | Future (enterprise sales gate) | Long-tail enterprise readiness; track as future work. Aspects already covered by SOC 2 work overlap heavily. | Future cycle |
| **Illinois BIPA** | Minimal exposure | We don't process biometric data. Operators must not ingest biometric identifiers. | Inherits PII detect-and-refuse |
| **Texas CUPRA / state-specific** | Minimal exposure | Aligned with GDPR/CCPA stance; revisit if specific obligations land. | Inherits GDPR gates |

## How to add a new standard

1. Add a row to this matrix with the four columns filled.
2. If "Applies? = Yes" and there's a "we don't process X" stance, the **Gate** column MUST point to deterministic enforcement (e.g., a detect-and-refuse pattern in `handlers/`). Skill-text-only enforcement is NOT acceptable per `docs/governance/doctrine-deterministic-governance.md`.
3. If "Applies? = Yes" and we do process X, document the data minimization or scope-reduction gate(s).
4. Update `governance-gates.yaml` if the new row introduces a new gate kind.
5. File specific compliance work (Type II evidence, DPIA, etc.) as separate issues that reference this matrix.

## Audit history

| Date | Reviewer | Notes |
|---|---|---|
| 2026-05-14 | initial author (Knapp-Kevin, AI-assisted) | First draft. Issue #205 Phase 1. |
