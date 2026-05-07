# Plan D: bundle compliance stance declarations (#220 + #225 + #226)

**change_class**: feature

**doc_tier**: minimal

**high_risk_target**: false

**terms_introduced**:
- term: host_trust_model
  home: docs/policies/host-trust-model.md (new) — MCP host UX dependency declaration
- term: acceptable_use
  home: docs/policies/acceptable-use.md (new) — prohibited-use declaration covering NIST AI RMF MAP-3.1 + EU AI Act Annex III
- term: sla_stance
  home: docs/sla.md (new) — operator-run-only availability stance declaration

**boundaries**:
- limitations:
  - These are compliance stance declarations, not enforced gates. Operators reading the documents can still violate the declared boundaries; the value is operator-readable scope and auditor-readable attestation.
  - The acceptable-use document folds NIST AI RMF MAP-3.1 + EU AI Act Annex III into one artifact (they're structurally equivalent: "intended purpose / prohibited uses"). Future regulatory frameworks with different shape may need separate artifacts.
  - The SLA document declares the operator-run-only stance as the active commitment. If a hosted tier ships, the document needs a parallel "Hosted tier" section with concrete uptime/MTTR/support targets.
- non_goals:
  - Server-side enforcement of any of these stances (out-of-band gating is the #217 epic's surface).
  - Audit-evidence collection (that's #218 sub-task SOC2-03 / Plan C — a separate workstream).
  - Per-deployment-tier stance variation (single-stance-per-document; if needs change with hosted tier, future PR adds tier sections).
- exclusions:
  - Modifying handler behavior, ingest gates, preflight policy, or any code path. Pure-doc PR.
  - Deferred related issues: #215 (SOC2-01 declare MCP trust boundary) — overlap exists with #226 sla.md; this plan keeps the SLA scope narrow and lets #215 ship its own boundary document.

## Open Questions

None at plan time. Three issues all map to specific compliance-framework gaps with explicit acceptance criteria. Bundling is justified because they share `docs/policies/` directory + a single README cross-reference section.

## Phase 1: Author the three policy documents + README cross-reference

### Affected Files

- `docs/policies/host-trust-model.md` (new) — MCP host UX dependency declaration; enumerates the host surfaces bicameral-mcp's design assumes (tool-call visibility, denial path, stdio surfacing, mid-call intervention) and notes none are server-enforceable; per-host operator-checklist entries for Claude Code / Cursor / Codex
- `docs/policies/acceptable-use.md` (new) — prohibited-use declaration: not-substitute-for-HR-legal-medical-financial decisions; do-not-ingest PHI / cardholder data / regulated classes; do-not-deploy on shared multi-tenant filesystem without auth shim; cross-reference to deferred deployment-profile matrix
- `docs/sla.md` (new) — availability stance: operator-run-only is the active commitment; no uptime / MTTR / support targets at this stage; activation requirements for a future hosted tier; "what changes when a hosted tier ships" section with the parallel-stance template
- `README.md` — new "Compliance posture" or "Policies" section (4-6 lines) linking to the three policy docs + the existing `docs/research-brief-compliance-audit-2026-05-06.md`
- `docs/research-brief-compliance-audit-2026-05-06.md` — mark gaps MCP-01 (#220), NIST-RMF-01 + AI-ACT-02 (#225), SOC2-02 (#226) as "closed by `docs/policies/<file>.md`" in their respective gap sections; doctrine-pointer update only, no analysis change

### Changes

#### `docs/policies/host-trust-model.md`

Sections:
1. **Why this document exists** — short paragraph framing: bicameral-mcp's design assumes specific host UX surfaces; those surfaces are external to the server.
2. **Server-side guarantees** — list the deterministic gates the SERVER enforces regardless of host (size limit, rate limit, canary scan, sensitive-data scan, signed hooks-manifest verification when bundled). Operator-readable.
3. **Host-side surfaces this design assumes** — enumerated:
   - Operator sees every tool-call request before approval
   - Operator can deny tool-call execution
   - Operator sees server stdout / TextContent responses
   - Operator can intervene mid-call (cancel)
   - Destructive actions (the `bicameral.reset` path) surface via host's confirmation UI
4. **None of (3) is server-enforceable** — explicit declaration; the next paragraph documents what an "auto-approving host" silently bypasses.
5. **Per-host operator checklist** — Claude Code / Cursor / Codex / generic-host: short bullet for each verifying the (3) assumptions hold (or not). Operators choosing a host consult this.
6. **What's covered by the #217 epic** — pointer to the per-tool authority gradation work that adds an out-of-band confirmation primitive (independent of host UX).
7. **Cross-references** — research brief § 1.1 + § 2.4 gap MCP-01; doctrine #205.

#### `docs/policies/acceptable-use.md`

Sections:
1. **Intended purpose** — bicameral-mcp is a developer-tool MCP server for code-decision tracking and drift detection. Limited-risk AI per EU AI Act Annex III categorization (decision-support for software engineering; not high-risk).
2. **Prohibited uses** — explicit non-exhaustive list:
   - Do not use bicameral-mcp's decisions as a substitute for HR, legal, medical, or financial decision-making.
   - Do not ingest Protected Health Information (PHI), cardholder data (PAN), or other regulated-data classes. Server-side detect-and-refuse on `bicameral.ingest` is in place (#213) but operators must understand the boundary.
   - Do not deploy on a shared multi-tenant filesystem without an auth shim. The current substrate assumes single-tenant operator control.
   - Do not use bicameral-mcp's outputs to make automated decisions affecting people without human-in-the-loop review.
3. **Cross-framework mapping** — short table mapping each prohibited use to the framework that drives it (NIST AI RMF MAP-3.1, EU AI Act Annex III categories, HIPAA, PCI DSS, SOC 2 multi-tenancy boundary).
4. **Deferred deployment-profile matrix** — pointer to the brief's Codex-2 review item (deferred follow-up) once that lands; the matrix will declare per-deployment-tier acceptable-use deltas.
5. **Cross-references** — research brief § 2.5 NIST-RMF-01 + § 2.6 AI-ACT-02; doctrine #205.

#### `docs/sla.md`

Sections:
1. **Active commitment** — operator-run-only. Bicameral-mcp is installed and operated by the operator on their own infrastructure; no hosted offering. Therefore: no declared uptime target, no MTTR target, no support response time.
2. **What this means in practice** — operator chooses install platform (CLI host, IDE, OS); operator runs upgrades; operator monitors process health; operator handles incidents. Bicameral-mcp's CI guarantees the WHEEL works; the server's runtime availability is the operator's domain.
3. **Activation requirements for a future hosted tier** — what would have to be declared if `bicameral.cloud` (or any hosted variant) ships:
   - Target uptime percentage
   - MTTR target
   - Support response time target
   - Incident notification SLA
   - Security incident-disclosure SLA
   - Data residency commitments
4. **What changes when a hosted tier ships** — this section becomes a "Hosted tier (active)" section with concrete numbers; the operator-run section moves to "Self-hosted (always available)" with no SLA.
5. **Cross-references** — research brief § 2.2 SOC2-02 + § 5 deployment-trigger column; related: #215 (SOC2-01 trust boundary); doctrine #205.

#### README.md addition

Insert a new section (placement: after the existing project description, before installation):

```markdown
## Compliance posture

bicameral-mcp's compliance stance is documented in three policy files:

- [`docs/policies/host-trust-model.md`](docs/policies/host-trust-model.md) — MCP host UX dependency declaration (closes OWASP LLM-07 / MCP-01 gap)
- [`docs/policies/acceptable-use.md`](docs/policies/acceptable-use.md) — intended purpose + prohibited uses (NIST AI RMF MAP-3.1 + EU AI Act Annex III)
- [`docs/sla.md`](docs/sla.md) — availability stance (operator-run-only; no hosted SLA)

The full compliance audit is at [`docs/research-brief-compliance-audit-2026-05-06.md`](docs/research-brief-compliance-audit-2026-05-06.md).
```

#### `docs/research-brief-compliance-audit-2026-05-06.md` updates

Find the gap entries for MCP-01, NIST-RMF-01, AI-ACT-02, SOC2-02 and append a single line each:

```markdown
- **Status (2026-05-06)**: Closed by `docs/policies/host-trust-model.md`.
```

(or the appropriate doc path for each gap). No analysis change; pure pointer update.

### Unit Tests

This is a pure-doc PR. The acceptance question for unit tests ("if the unit's behavior were silently broken but the artifact still existed, would this test fail?") cannot be satisfied by traditional functional tests against markdown content — markdown HAS no behavior, only content.

The functional tests for this PR are therefore CONTENT-CONTRACT tests: they invoke the docs-rendering substrate (in this repo, that's just file existence + content presence) and assert on the structural commitments the policy docs make. Per `qor/references/doctrine-test-functionality.md`, content tests are functional when they verify the unit's content delivers the contract; presence-only is when they ONLY verify existence.

- `tests/test_compliance_policy_docs.py::test_host_trust_model_declares_required_sections` (new) — opens `docs/policies/host-trust-model.md`; asserts the rendered markdown contains the section headings: "Server-side guarantees", "Host-side surfaces this design assumes", "Per-host operator checklist". The unit's contract is the section-shape commitment to operators and auditors; if a future edit silently drops one of these sections, the policy declaration loses its load-bearing structure.

- `tests/test_compliance_policy_docs.py::test_acceptable_use_lists_required_prohibited_categories` (new) — opens `docs/policies/acceptable-use.md`; asserts the markdown carries lines for: HR/legal/medical/financial substitution prohibition, PHI/PAN/regulated-data prohibition, multi-tenant deployment prohibition, automated-decisions-without-HITL prohibition. Doctrine: each of these is a specific compliance-framework requirement; their absence is a coverage gap.

- `tests/test_compliance_policy_docs.py::test_sla_declares_operator_run_only_stance_and_hosted_activation` (new) — opens `docs/sla.md`; asserts the document contains both an "Active commitment: operator-run-only" declaration AND an "Activation requirements for a future hosted tier" section. The first locks the current stance; the second locks the upgrade path so a future hosted tier doesn't ship without the SLA section being filled in.

- `tests/test_compliance_policy_docs.py::test_readme_compliance_section_links_all_three_policies` (new) — opens `README.md`; asserts the rendered content contains links to all three policy files (`docs/policies/host-trust-model.md`, `docs/policies/acceptable-use.md`, `docs/sla.md`) AND a link to the compliance audit brief. Doctrine: cross-reference discoverability is the load-bearing operator-facing surface; a missing link silently breaks the discovery path.

- `tests/test_compliance_policy_docs.py::test_research_brief_marks_closed_gaps` (new) — opens `docs/research-brief-compliance-audit-2026-05-06.md`; asserts the gap entries for MCP-01, NIST-RMF-01, AI-ACT-02, SOC2-02 each carry a "Status (2026-05-06): Closed by `docs/policies/<file>.md`" line. Locks the bidirectional cross-reference between the gap analysis and the closure documents.

Each test invokes the file-read primitive AND asserts on specific content commitments — not just file existence. The acceptance question "if the doc were silently rewritten to remove a load-bearing section but the file still existed, would the test fail?" is YES for every test. Per the test-functionality doctrine, content-contract tests are functional when they verify specific commitments the doc was authored to deliver.

## CI Commands

- `python -m pytest tests/test_compliance_policy_docs.py -v` — runs the new content-contract tests
- `python -m pytest -v` — full regression (no code changes; verifies no regression on the doctrine-test gate)
- `ruff check .` + `ruff format --check .` — lint + format gates (no Python changes; should be no-op)
- Manual smoke: open the rendered markdown in a Markdown previewer and verify cross-links resolve
