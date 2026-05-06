# Plan: #205 — e2e compliance research brief

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: compliance stance matrix
  home: docs/research-brief-compliance-audit-2026-05-06.md
- term: boundary statement (compliance)
  home: docs/research-brief-compliance-audit-2026-05-06.md
- term: deterministic gate (governance)
  home: docs/research-brief-compliance-audit-2026-05-06.md

**boundaries**:
- limitations: This deliverable is a research brief — operator-consumed prose, not code. There are no unit tests because the surface under audit is documentation; the audit pass evaluates brief completeness against #205's stated matrix shape. Anything here can be wrong; the brief is a snapshot of risk-as-of-2026-05-06, not a guarantee.
- non_goals: Implement any compliance standard fully. Do not author `lint_skill_governance.py` (#205 Phase 1 deliverable, follow-up). Do not write `doctrine-deterministic-governance.md` (#205 Phase 1 deliverable, follow-up). Do not migrate any existing skill from instruction-only defaults to deterministic gates (#205 Phase 3 retroactive sweep, follow-up).
- exclusions: Team-server / Slack ingest gets a one-paragraph boundary statement only — partly inert per #161; full audit deferred until activation. The e2e test harness (`tests/e2e/`) and CI workflows (`.github/workflows/`) get a one-line scope-out reference; CI/build-system threat models are a different shape than runtime threat models and bundling dilutes both. NIST CSF 2.0, NIST SSDF, FIPS 140-3, ISO 27001/27701, HIPAA, PCI DSS, CCPA/CPRA, BIPA/state-specific all get one-paragraph boundary statements rather than full-depth walks.

## Open Questions

All resolved during /qor-plan dialogue 2026-05-06:

- **Output shape**: research brief (no code, no test surface). Per option B.
- **Standards depth**: tiered — full depth on GDPR, SOC 2, OWASP Top 10, OWASP LLM Top 10, NIST AI RMF, EU AI Act; boundary statement on HIPAA, PCI DSS, NIST CSF 2.0, NIST SSDF, FIPS 140-3, ISO 27001/27701, CCPA/CPRA, BIPA. Per option 2.
- **Surface scope**: full user-facing runtime + agent-instruction surface; team-server gets boundary statement; e2e harness + CI workflows excluded. Per option δ.
- **Artifact home**: `docs/` flat (matches existing `docs/preflight-failure-scenarios.md` precedent). Per option ii.
- **Test surface**: none. Research-brief content is operator-consumed prose; the audit pass + operator review are the verification mechanism. Plan declares zero unit tests with rationale rather than authoring presence-only tests that the doctrine-test-functionality carve-out would forbid (the brief is not LLM-consumed agent-instruction markdown; it is operator-facing prose).
- **Issue-filing trigger**: file gap-remediation issues during Phase 3 against the dev branch immediately, before brief finalization. Operator can re-prioritize / close / re-scope after the fact; lower friction than a holding queue inside the brief.

## Phase 1: Surface inventory

### Affected Files

- `docs/research-brief-compliance-audit-2026-05-06.md` — **new**: opens with metadata header (date, scope, methodology) + § "Surface inventory" section listing every component in scope per option δ.

### Changes

Author `docs/research-brief-compliance-audit-2026-05-06.md` (**new**) § Surface inventory. For each component below, capture: (a) location (file path + key entrypoints), (b) what it does, (c) data it touches (input shape + persistent state shape), (d) external surfaces (network, filesystem, env, MCP tool boundary), (e) trust boundary (operator-controlled vs agent-controlled vs external).

Components in scope:

1. **MCP server + tool dispatch** — `server.py`, `handlers/` directory. The 13-tool MCP surface is the agent's only programmatic access path.
2. **Ledger persistence** — `ledger/adapter.py`, `ledger/queries.py`, `ledger/schema.py`; SurrealDB embedded via `surrealdb>=2.0.0`; storage at `surrealkv://~/.bicameral/ledger.db`.
3. **Code locator** — `code_locator/` directory, sqlite symbol DB, tree-sitter symbol extraction.
4. **Ingest pipeline** — `handlers/ingest.py`, `events/writer.py`, signer-email policy in `context.py`.
5. **Preflight pipeline** — `handlers/preflight.py`, source-attribution policy gate, bypass-tracking gate.
6. **Telemetry** — `scripts/hooks/`, `BICAMERAL_TELEMETRY` env flag, `~/.bicameral/preflight_events.jsonl` JSONL substrate.
7. **Install / upgrade path** — `setup_wizard.py`, `handlers/update.py`, the new uv > pipx > pip resolve chain (#199), the SessionEnd hook installer.
8. **Skills (agent-instruction surface)** — `skills/**/SKILL.md`. The novel risk class #205 calls out — instruction-level defaults that a jailbroken or prompt-injected agent can bypass.
9. **Team-server boundary statement** — one paragraph: parked / partly inert per #161 + #160; full audit deferred until activation. Captures the dependency without committing to depth.
10. **CI/e2e scope-out** — one line: out of scope for this brief; tracked separately.

### Unit Tests

None. Phase 1 delivers prose; the audit pass evaluates inventory completeness against the 10-component scope agreed in dialogue. Rationale: doctrine-test-functionality forbids presence-only tests; the brief is operator-consumed, not LLM-consumed; there is no programmatic invariant to assert.

## Phase 2: Per-standard walk

### Affected Files

- `docs/research-brief-compliance-audit-2026-05-06.md` (**new** — created in Phase 1, extended here) — extends with § "Per-standard walk" containing 6 full-depth subsections + 8 boundary-statement subsections + § "Boundary-statement standards summary table."

### Changes

For each **full-depth standard**, produce a subsection with three blocks:

- **What the standard requires**: 3–8 bullets capturing the core obligations relevant to bicameral-mcp's surface (NOT a full standard summary — the obligations that intersect what bicameral-mcp actually does).
- **What bicameral-mcp does today**: per-component (from Phase 1's inventory) statement of current behavior; cite file paths + line ranges where the deterministic-gate side of #205 is honored.
- **Gaps**: numbered list of every place the current behavior falls short of, or is silent on, the standard's requirements. Each gap has a stable ID (e.g. `GDPR-01`, `OWASP-LLM-04`) so Phase 3 can cross-link issues back.

Full-depth standards (in this order; the order is the rough commercial-relevance gradient):

- **GDPR** (Regulation (EU) 2016/679). Focus: lawful basis for telemetry, data minimization in ledger entries, right-to-erasure semantics for the append-only Merkle chain (the canonical hard problem), cross-border transfer in team mode, signer-email PII handling (#200 Phase 2 wiring is partial coverage).
- **SOC 2 Type II readiness** (AICPA TSP §100). Focus: security (the Trust Services Criterion most binding on a B2B MCP tool); availability (single-process MCP server limits); processing integrity (Merkle chain + classifier_version freeze per #162); confidentiality (the render-source-attribution gate from #200 Phase 3 is direct evidence); privacy (overlap with GDPR walk).
- **OWASP Top 10 (2021/2025)**. Already partially gated by `/qor-audit` Step 3. This walk catalogs what's covered vs uncovered; specific focus on A03 Injection (subprocess form, ingested-content paths), A04 Insecure Design (the agent-instruction-as-default class), A05 Misconfiguration (env-var defaults, telemetry opt-in vs opt-out), A08 Software/Data Integrity (the supply chain — wheel build, dependency surface, the new `uv tool install` path from #199).
- **OWASP LLM Top 10 (v1.1, 2024)**. The single most-novel surface for bicameral-mcp. Focus: LLM01 Prompt Injection (every ingested transcript, every PR body, every commit message is potential injection content; preflight currently displays redacted source_ref but downstream agent reasoning still consumes it); LLM02 Insecure Output Handling (decisions surfaced to the agent shape downstream code-edit decisions — a manipulated decision IS an output-handling defect); LLM03 Training Data Poisoning (n/a — bicameral-mcp doesn't train); LLM04 Model DoS (preflight cap of ≤4 questions, ingest size limits — what are they?); LLM06 Sensitive Information Disclosure (ledger contents in the agent's context — the novel surface); LLM07 Insecure Plugin Design (MCP tool surface — what's the principle of least authority story?); LLM08 Excessive Agency (the agent calls preflight/ingest/resolve_collision; what gates exist beyond "agent decides to call"?); LLM09 Overreliance (the gap between deterministic-gate and instruction-default that #205 codifies); LLM10 Model Theft (n/a — bicameral-mcp doesn't host a model).
- **NIST AI RMF 1.0** (NIST AI 100-1). Already partially referenced in qor-logic doctrine. Focus: GOVERN (the deterministic-gate doctrine of #205 is direct evidence); MAP (the impact-assessment block in plan schemas is partial); MEASURE (the gap — bicameral-mcp has no production-time MEASURE function for the AI risks; current measurement is plan-time gates); MANAGE (override-friction at qor-logic skill level is referenced; bicameral-mcp itself has nothing analogous).
- **EU AI Act** (Regulation (EU) 2024/1689). Focus: risk classification (limited-risk most likely, but team-server transcript ingest could pull in HR-decision data → high-risk); transparency obligations (Art. 13/50 — what does the agent disclose about itself when it surfaces decisions?); human oversight (Art. 14 — the AskUserQuestion gates from #175 are direct evidence; preflight bypass-tracking from #200 Phase 3 is partial); risk-management system (Art. 9 — the Pause/Audit/Implement/Substantiate cycle from qor-logic invocations is partial coverage when bicameral-mcp is operated under qor-logic, but bicameral-mcp standalone has no such cycle).

For each **boundary-statement standard**, one paragraph in `## Boundary-statement standards`:

- **Apply?** Yes / No / Conditional (e.g. "yes if customer ingests PHI").
- **Stance.** What bicameral-mcp commits to (e.g. "no PHI processing").
- **Gate.** What deterministic mechanism enforces the stance (e.g. "no PHI-detection regex today; gap GAP-HIPAA-01").

Boundary-statement standards: **NIST CSF 2.0**, **NIST SSDF (SP 800-218)**, **FIPS 140-3**, **HIPAA**, **PCI DSS**, **ISO 27001 / 27701**, **CCPA / CPRA**, **BIPA / state-specific**.

A summary table at end of Phase 2 captures all 14 standards (6 + 8) with columns: standard | apply? | stance | deterministic gate (now) | gap IDs.

### Unit Tests

None. Same rationale as Phase 1.

## Phase 3: Gap synthesis + remediation triage

### Affected Files

- `docs/research-brief-compliance-audit-2026-05-06.md` (**new** — created in Phase 1, extended here) — extends with § "Gap synthesis" + § "Remediation triage" + § "Filed issues" cross-link table.
- (External) GitHub issues filed against `BicameralAI/bicameral-mcp` for each gap, labeled `governance`, `compliance`, plus the per-standard label tag.

### Changes

Collect every numbered gap from Phase 2 into a flat table:

| ID | Standards | Component | One-line description | Severity | Likelihood | Priority | Remediation type |
|---|---|---|---|---|---|---|---|

- **Severity**: P0 (compliance-blocking — customer can't adopt) / P1 (audit-finding-class — surfaces in SOC 2 Type II) / P2 (best-practice / posture-improving) / P3 (deferred / out-of-scope-confirm).
- **Likelihood**: H (default code path) / M (uncommon code path) / L (only under prompt injection or jailbreak).
- **Priority**: derived as Severity × Likelihood; the brief picks one of the standard 4-quadrant rubrics.
- **Remediation type**: deterministic-gate (matches #205 doctrine — server-side filter, config knob, MCP tool wrapper) / boundary-statement (no behavior change, only documented stance) / instruction-only (kept as instruction with explicit acknowledgement that the doctrine permits it under named exceptions, e.g. UX-only hints with no privacy/security impact) / scope-defer (out of scope; track for revisit).

Deduplicate gaps that surface under multiple standards (e.g. an audit-log retention gap will hit GDPR + SOC 2 + NIST AI RMF MEASURE).

For each P0 / P1 gap, file a GitHub issue against `BicameralAI/bicameral-mcp`. Issue title: `[compliance:<standard>] <one-line description> (gap <ID>)`. Body: cite the brief location, name the deterministic gate that needs to ship, link to any prior PRs that touched the area. Apply labels: `governance`, `compliance`, the `P0`/`P1` priority label, and the per-standard tag.

A summary table at end of Phase 3 cross-links every gap ID → filed issue number.

### Unit Tests

None. Phase 3 produces operator-reviewable triage; the audit pass evaluates the gap-table for completeness (every gap from Phase 2 appears, every P0/P1 has an issue), and the operator confirms or amends priority calls during review.

## CI Commands

- `python scripts/lint_plan_grounding.py plan-205-compliance-research-brief.md` — validates that backticked paths in this plan resolve on the working tree (existing files) or are exempted via `**new**` markers (new file).
- `gh issue list --repo BicameralAI/bicameral-mcp --label compliance --state open` — Phase 3 verification: every gap-ID flagged P0/P1 has a corresponding open issue.
- `wc -l docs/research-brief-compliance-audit-2026-05-06.md` — sanity check the brief is non-trivial (expected on the order of 600–1500 lines).

There is no test-suite invocation because no code ships in this PR. The audit pass is the verification gate; operator review of the brief is the final acceptance.
