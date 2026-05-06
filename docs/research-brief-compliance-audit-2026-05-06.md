# Compliance & Security Audit — Bicameral MCP Research Brief

**Date**: 2026-05-06
**Repo HEAD**: `86860e9` (post-#199 merge, pre-this-PR)
**Server version**: 0.13.3
**Scope**: full user-facing runtime + agent-instruction surface (per `plan-205` option δ)
**Out of scope**: e2e harness (`tests/e2e/`), CI workflows (`.github/workflows/`), team-server full audit (boundary-statement only — per #161 inert state)
**Authoring methodology**: code-grounded walk (read actual files at HEAD; cite paths and line ranges); standards interpreted against the 2024–2026 published versions; gaps stated as observable behavior vs. requirement, not as opinion.

This document is **operator-consumed prose**, not LLM-consumed agent-instruction. It contains gap findings (`<STANDARD>-<NN>`) that map to filed remediation issues in the final § "Filed issues."

---

## § 1. Surface inventory

The 10 components in scope. For each: location, what it does, data it touches, external surfaces, trust boundary.

### 1.1 MCP server + tool dispatch

- **Location**: `server.py` (~1500 lines), `handlers/*.py` (24 handler modules).
- **What it does**: exposes 13 MCP tools to the connected agent (Claude Code, Cursor, Codex). Each tool dispatches into `handlers/<tool>.py`. The MCP boundary is the only programmatic surface the agent can reach.
- **Data touched**: tool arguments (agent-supplied JSON), repo-relative paths, ledger payloads, transcript text on `bicameral.ingest`, decision IDs, file paths on `bicameral.preflight`.
- **External surfaces**: stdio (MCP transport), filesystem (repo path, `~/.bicameral/`), embedded SurrealDB process, sqlite (code locator), git subprocess.
- **Trust boundary**: MCP transport is local stdio; the *agent* is operator-installed but executes model-generated tool calls. Inputs to handlers are **agent-controlled**, not operator-controlled. There is **no authentication, authorization, or rate-limiting** on the MCP boundary — the server trusts whoever can talk to its stdio.

### 1.2 Ledger persistence

- **Location**: `ledger/adapter.py`, `ledger/queries.py`, `ledger/schema.py`, `ledger/client.py`, `ledger/canonical.py`, `ledger/drift.py`, `ledger/status.py`, `ledger/ast_diff.py`.
- **What it does**: append-mostly decision ledger backed by embedded SurrealDB v2.x. Decisions, signoffs, code-region bindings, compliance check rows. SHA-256 Merkle hashing for chain integrity.
- **Data touched**: decision descriptions (free-form text from ingested transcripts), signer email (subject to `signer_email_fallback` policy from #200 Phase 2), classifier_version, decision_level, source_ref strings, file paths, region anchors, ratification state. Persisted indefinitely.
- **External surfaces**: filesystem (`surrealkv://~/.bicameral/ledger.db` by default; configurable via `SURREAL_URL`).
- **Trust boundary**: storage is **operator-local** unless team-mode is engaged. Append semantics make right-to-erasure non-trivial (see § 2.1.4).

### 1.3 Code locator

- **Location**: `code_locator/` directory: `config.py`, `models.py`, `indexing/` (tree-sitter symbol extractors per language), `tools/` (validate_symbols, get_neighbors).
- **What it does**: language-agnostic symbol DB built via tree-sitter, persisted to sqlite. Supports import/invokes/inherits/contains edges. Powers preflight's region-anchored lookup and the 1-hop graph expansion shipped in #173.
- **Data touched**: source-tree symbol names, file paths, line ranges, AST shapes. No persistent text content beyond symbol identifiers.
- **External surfaces**: filesystem (sqlite DB in `.bicameral/code_index.db`), tree-sitter shared libraries.
- **Trust boundary**: indexes whatever git tree the operator points it at. Pure read-side; no exfiltration path.

### 1.4 Ingest pipeline

- **Location**: `handlers/ingest.py` (canonical entry), `events/writer.py` (file-mode JSONL writer), `events/materializer.py` (event → ledger row), `context.py:54-94` (signer-email + render-attribution + bypass-tracking config readers).
- **What it does**: accepts a transcript / PR body / Slack thread / commit message / arbitrary text via `bicameral.ingest`; classifies into decision candidates; writes events to either local JSONL or directly to ledger; signs each event with `git config user.email` (subject to `signer_email_fallback`).
- **Data touched**: **arbitrary user-supplied text** (the highest-risk surface in the system). Signer email (PII). Source attribution string (potentially containing names, dates, system identifiers). Topic strings. File paths.
- **External surfaces**: filesystem (event JSONL files, ledger DB, optional team-server append).
- **Trust boundary**: **agent-controlled** content arrives via the MCP `bicameral.ingest` call. The agent is operating on operator-provided content (transcripts, PR bodies) but the agent itself is a model whose decisions can be steered by the content it's reading. Prompt injection in the source content can manipulate what the agent ingests, what it tags, and how it describes it.

### 1.5 Preflight pipeline

- **Location**: `handlers/preflight.py` (region-anchored lookup, attribution policy, bypass-tracking gate), `handlers/record_bypass.py` (telemetry write-side, gated on `preflight_bypass_tracking` config from #200 Phase 3 and `BICAMERAL_PREFLIGHT_TELEMETRY` env).
- **What it does**: surfaces existing decisions relevant to the agent's current intent BEFORE the agent acts; collects bypass justifications; writes telemetry events to `~/.bicameral/preflight_events.jsonl` (local-only, opt-in).
- **Data touched**: decision IDs surfaced to agent; redaction policy applied to `source_ref` (per #200 Phase 3); bypass reason strings (free-form agent text).
- **External surfaces**: filesystem (preflight telemetry JSONL).
- **Trust boundary**: preflight output flows BACK INTO the agent's reasoning context — a manipulated decision (e.g. injected via a poisoned ingested transcript) shapes downstream code edits.

### 1.6 Telemetry (two distinct paths)

- **Location**:
  - `telemetry.py` — anonymous usage relay to PostHog via Cloudflare Worker. Strict allowlist of fields (skill name, duration, version, numeric/boolean diagnostics). NEVER collects: decision text, file paths, repo names, queries, code, or meeting/PRD/Slack content.
  - `preflight_telemetry.py` — local-only failure-mode capture to `~/.bicameral/preflight_events.jsonl` and `engagements.jsonl`. Per-install salted SHA-256 hashes for `topic` and `file_paths`; `surfaced_ids` written raw (documented S1 invariant — opaque ledger IDs needed for triage join).
  - `consent.py` — first-boot notice + persisted consent marker at `~/.bicameral/consent.json`; `BICAMERAL_TELEMETRY=0` env override.
- **Data touched (anonymous relay)**: `distinct_id` random UUID at `~/.bicameral/device_id`; skill name strings; durations.
- **Data touched (local preflight telemetry)**: hashed topic + hashed file paths (default); raw under `BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1`. Raw `surfaced_ids` (opaque ledger UUIDs).
- **External surfaces**: HTTPS to Cloudflare Worker (anonymous relay only); filesystem (consent marker + local JSONL).
- **Trust boundary**: anonymous relay is **outbound-network**; opt-out is env-or-marker. Local preflight telemetry never leaves the machine but writes to operator's `$HOME`.

### 1.7 Install / upgrade path

- **Location**: `setup_wizard.py` (interactive setup, hook installer, OS-aware SessionEnd shape from #200 Phase 1, UTF-8 stdout reconfig from #199), `handlers/update.py` (version check + apply, uv > pipx > pip resolve chain from #199), `scripts/hooks/*.py` (post-commit sync reminder, preflight reminder, SessionEnd queue writer).
- **What it does**: bootstraps repo-local `.bicameral/` config, installs Claude Code hooks in `.claude/settings.json`, installs git post-commit + optional pre-push hooks. `update.py` hits `RECOMMENDED_VERSION` URL (raw GitHub) for upgrade discovery.
- **Data touched**: repo path, `~/.bicameral/update-check.json` (1h TTL cache), package-version strings.
- **External surfaces**: HTTPS to `raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION`; subprocess (`uv tool install` / `pipx install` / `pip install`).
- **Trust boundary**: installer runs as the operator. Subprocess install commands use list-form argv (per #199 implementation). The version-check URL is fixed and HTTPS — no MITM-resistant pinning beyond TLS.

### 1.8 Skills (the agent-instruction surface)

- **Location**: `skills/**/SKILL.md` — 14 skill files defining how the agent should invoke MCP tools, render output, redact data, and prompt the user. Plus `skills/CLAUDE.md`, `skills/CONSTANTS.md`, `skills/bicameral-output-formats/SKILL.md` (shared output rendering rules).
- **What it does**: provides the agent with prose instructions on when/how to call MCP tools and how to format results. Includes user-facing prompts (`AskUserQuestion` from #175), telemetry-transparency notes, and (the #205 doctrine concern) **default-behavior instructions** that say "by default, redact X" / "by default, extract only keys" / "never include verbatim Y."
- **Data touched**: indirect — skills shape how the agent processes ingested content, but the skill files themselves are operator-installed markdown.
- **External surfaces**: read by the agent at session boot.
- **Trust boundary**: **the novel #205 attack class.** A jailbroken agent, a model regression, or a prompt-injected upstream content payload can ignore SKILL.md instructions silently. The defenses must live at server-side / config-load boundaries, not in skill text.

### 1.9 Team-server boundary statement

Team-server plumbing exists in code (`events/team_adapter.py`, `events/team_server_bridge.py`, `events/team_server_consumer.py`, `events/team_server_pull.py`, plus the parked `team_server/` plan at `plan-priority-c-team-server-slack-v0.md`). Slack ingest is plumbed but **inert** because the `channel_allowlist` table is defined and queried but never populated (#161 — merged-to-dev, awaiting activation). The materializer dispatches on `event_type='ingest.completed'` but team-server emits `'ingest'` (#160 — merged-to-dev). Consequence: Slack content is not currently ingested, but the code path exists and would activate when both blockers are addressed.

**Posture for this brief**: full audit deferred until activation. Documented gaps recorded as `TEAM-NN` IDs in § 4 will not block this brief; they are waiting for the activation PR to consume them.

### 1.10 CI/e2e scope-out

`tests/e2e/` and `.github/workflows/` are out of scope for this brief. The e2e harness ingests real-transcript fixtures and runs Claude Code in headless mode — that's a real attack surface (prompt injection in CI, supply-chain considerations) but a different threat-model shape than runtime. Bundling dilutes both. Tracked for separate audit.

---

## § 2. Per-standard walk (full depth)

For each full-depth standard: **(a) what it requires (relevant intersections only)**, **(b) what bicameral-mcp does today**, **(c) gaps with stable IDs**.

### § 2.1 GDPR (Regulation (EU) 2016/679)

#### (a) What GDPR requires (intersections relevant to bicameral-mcp)

1. **Lawful basis** for any processing of personal data (Art. 6). Telemetry collection requires a basis (consent, legitimate interest, etc.).
2. **Data minimization** (Art. 5(1)(c)) — collect only what's necessary.
3. **Storage limitation** (Art. 5(1)(e)) — retention only as long as necessary.
4. **Right to erasure** (Art. 17) — data subjects can request deletion; the controller must comply absent overriding interests.
5. **Right of access** (Art. 15) — data subjects can obtain their personal data.
6. **Cross-border transfer** (Chapter V) — transfers outside EU/EEA require adequacy or safeguards.
7. **Records of processing activities** (Art. 30) — controllers ≥250 employees (or processing posing risk) maintain records.
8. **Data breach notification** (Art. 33–34) — 72-hour notification of breaches to supervisory authority.

#### (b) What bicameral-mcp does today

- **Lawful basis**: `consent.py` implements explicit telemetry consent with opt-out (`BICAMERAL_TELEMETRY=0`) and a first-boot notice. The notice text discloses categories collected. Consent marker at `~/.bicameral/consent.json` with policy version. ✓
- **Data minimization (anonymous relay)**: `telemetry.py` enforces a strict allowlist client-side: skill name, version, duration, numeric/boolean diagnostics; explicitly forbids decision text, file paths, repo names, code content. Schema invariants enforced at the Cloudflare Worker relay. ✓
- **Data minimization (preflight telemetry)**: `preflight_telemetry.py` defaults to hashed topic + hashed file_paths via per-install salt; raw mode is opt-in. `surfaced_ids` are written raw but are opaque UUIDs. ✓ Strong.
- **Data minimization (signer email)**: #200 Phase 2 signer-email fallback policy (`local-part-only` default) strips the domain from `git config user.email` before ledger write. ✓
- **Storage limitation**: preflight telemetry rotates files at 50 MB or 30-day mtime, keeping last 5. ✓ Anonymous relay retention is set on the PostHog side — not declared in this repo.
- **Right to erasure**: ledger is append-mostly with SHA-256 Merkle chain; deleting a row breaks the chain. **No documented right-to-erasure procedure.** Gap.
- **Right of access**: no API or CLI for a data subject to retrieve their personal data from a bicameral-mcp install. Gap.
- **Cross-border transfer**: anonymous relay routes to PostHog (likely EU-or-US depending on PostHog tenant); the Cloudflare Worker fronting is global. **No declared transfer mechanism**. Gap.
- **Records of processing**: not maintained in repo.
- **Breach notification**: no documented incident-response procedure.

#### (c) Gaps

- **GDPR-01** [P1] — **Right-to-erasure procedure undefined for ledger entries.** Append-mostly Merkle chain conflicts with Art. 17. Remediation: document a tombstone-and-rebuild procedure (mark erased rows, recompute the chain from a designated seal, document the data-loss boundary), OR scope the ledger as "no personal data" by deterministic gate (PII-detect-and-refuse on ingest), OR explicitly exempt under Art. 17(3) overriding-legitimate-interests claim.
- **GDPR-02** [P1] — **No data-subject access endpoint.** A self-hosted operator can't honor an Art. 15 request without a CLI/MCP tool that emits all rows containing a given email or identifier. Remediation: `bicameral-mcp data-subject-access --email <addr>` CLI that emits matching ledger rows + telemetry-events file, with a documented procedure.
- **GDPR-03** [P2] — **Cross-border transfer documentation gap for anonymous relay.** Cloudflare Worker is global; PostHog tenant location undeclared. Remediation: declare the data flow in `docs/`, identify the PostHog tenant region, declare adequacy basis (likely SCCs / EU tenant choice).
- **GDPR-04** [P2] — **No documented retention boundary for anonymous relay data.** Client-side controls send-side; server-side retention is implicit. Remediation: document PostHog retention setting in the `consent.py` policy text.
- **GDPR-05** [P1] — **Signer-email fallback default leaks local-part.** `local-part-only` mode emits `kevin` from `kevin@example.com` — that's a pseudonym, but a recoverable one in many orgs. Remediation: change default to `redact`, OR document why `local-part-only` is the better tradeoff (audit traceability vs. PII), OR add a per-team config knob with `redact` recommended for ≥10-person teams.
- **GDPR-06** [P3] — **No Art. 30 records of processing template.** Operator-side gap for self-hosted deployments crossing the headcount/risk threshold. Remediation: ship a `docs/gdpr-records-of-processing.md` template.
- **GDPR-07** [P3] — **No incident-response runbook.** Remediation: ship `docs/incident-response.md` with the 72-hour Art. 33 timeline and operator decision tree.

---

### § 2.2 SOC 2 Type II readiness (AICPA TSP §100)

#### (a) What SOC 2 requires (Trust Services Criteria — focus on Security + Privacy + Confidentiality)

1. **Security (CC)** — protect against unauthorized access. Logical/physical access controls, system monitoring, change management.
2. **Availability (A)** — system available for operation per commitment. Capacity planning, environmental safeguards, recovery.
3. **Processing integrity (PI)** — system processing complete, valid, accurate, timely, and authorized.
4. **Confidentiality (C)** — information designated as confidential protected.
5. **Privacy (P)** — personal information handled per the entity's notice and applicable criteria. Overlaps GDPR.

#### (b) What bicameral-mcp does today

- **CC: Logical access** — none on the MCP boundary. The MCP server trusts stdio. SurrealDB in `surrealkv://` mode is a local file with OS-level perms only.
- **CC: System monitoring** — `consent.py`, `telemetry.py` provide telemetry; no SIEM-grade logging. Preflight telemetry is local-only.
- **CC: Change management** — qor-logic gate chain (when bicameral-mcp is operated under qor-logic) provides plan→audit→implement→substantiate trail. Per-PR CI provides regression testing. Merkle chain provides cryptographic change-trail for the ledger.
- **A: Availability** — single-process MCP server; no HA story; no documented MTTR.
- **PI: Processing integrity** — strong. Merkle chain over the ledger; classifier_version freeze pending #162; `binds_to` provenance; deterministic resolve_compliance.
- **PI: Render-source-attribution** — #200 Phase 3 deterministic gate filters source_ref before agent consumption (currently `full` default with `redacted`/`hidden` opt-in pending regex refinement per #209).
- **C: Confidentiality** — telemetry minimization (above) is direct evidence; preflight render-attribution gate is direct evidence.
- **P: Privacy** — covered by § 2.1 GDPR.

#### (c) Gaps

- **SOC2-01** [P0] — **No authentication/authorization on the MCP boundary.** The MCP transport is stdio; the server trusts whatever process is on the other end. For a single-tenant local install this is acceptable; for any team-shared deployment (team-server activation, hosted bicameral-mcp) this is a CC1.0/CC6.0 gap. Remediation: declare the trust boundary explicitly (single-user local install only) AND/OR design an authentication shim for shared deployments.
- **SOC2-02** [P1] — **No availability commitment / MTTR target.** Acceptable for a developer tool; problematic for any "we run this for you" pricing tier. Remediation: declare in `docs/sla.md` whether bicameral-mcp is operator-run-only (no SLA) or has any hosted commitment.
- **SOC2-03** [P1] — **No documented change-control evidence trail for the package itself.** PRs are reviewed but not signed; releases are not signed. Remediation: gpg-sign release tags; document the per-release evidence-collection procedure (PR list, CI runs, code review attribution).
- **SOC2-04** [P2] — **Backup / disaster-recovery procedure for the ledger undefined.** A SurrealKV file at `~/.bicameral/ledger.db` is the operator's responsibility but the project ships no backup guidance. Remediation: ship `docs/backup-and-recovery.md`.
- **SOC2-05** [P2] — **classifier_version freeze (#162) gap.** Decisions ratified under classifier_version=N can be re-classified under classifier_version=N+1, breaking PI invariants. Remediation: ship #162.
- **SOC2-06** [P1] — **System monitoring gaps for self-hosted operators.** No structured-log emission; debugging requires `BICAMERAL_DEBUG=1` and stderr scraping. Remediation: emit structured JSON logs to stderr by default (or to a configurable path); document log retention guidance.

---

### § 2.3 OWASP Top 10 (2021)

#### (a) Categories with highest intersection

1. **A01 Broken Access Control** — bypassed access controls.
2. **A02 Cryptographic Failures** — sensitive data not protected.
3. **A03 Injection** — untrusted input reaches an interpreter.
4. **A04 Insecure Design** — design flaws (default-fail-open, missing security controls by design).
5. **A05 Security Misconfiguration** — defaults that leak, debug surfaces in prod.
6. **A06 Vulnerable and Outdated Components** — supply chain.
7. **A07 Identification and Authentication Failures** — weak/broken auth.
8. **A08 Software and Data Integrity Failures** — unsafe deserialization, unsigned updates, CI/CD without integrity.
9. **A09 Security Logging and Monitoring Failures** — auditing gaps.
10. **A10 Server-Side Request Forgery** — unvalidated outbound URLs.

#### (b) What bicameral-mcp does today

- **A01**: no access-control surface; see SOC2-01.
- **A02**: SHA-256 for Merkle chain (FIPS-approved). TLS for outbound HTTPS. No sensitive data is encrypted at rest in the local SurrealKV file (see GDPR-01 / OWASP-02 below).
- **A03**: subprocess invocations use list-form argv (verified at #199 audit and across handlers); no `shell=True` in prod paths; SurrealQL queries are parameter-bound through the SurrealDB Python SDK. Tree-sitter handles untrusted source; sqlite uses parameterized queries.
- **A04**: insecure-design surface flagged by #205 doctrine — instruction-only defaults in skill text. The whole #205 issue exists because of this.
- **A05**: telemetry default-on with policy-version notice (consent.py). `BICAMERAL_TELEMETRY=0` opt-out documented. `BICAMERAL_PREFLIGHT_TELEMETRY` is **opt-in**. `BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1` is **explicitly opt-in for raw-mode**.
- **A06**: `pyproject.toml` pins `>=` floors but not exact versions. `surrealdb>=2.0.0`, `tree-sitter>=0.23`, etc. No `requirements.txt` lock. No SBOM emitted.
- **A07**: no auth — see SOC2-01.
- **A08**: wheel build via Hatchling; no signed wheels; no SBOM in release artifacts. The `_resolve_install_command` in `handlers/update.py` (#199) calls subprocess with the user-installed `uv`/`pipx`/`pip` — supply-chain confidence is delegated to those tools' security models.
- **A09**: see SOC2-06.
- **A10**: outbound HTTPS only to `raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION` (fixed) and the Cloudflare Worker telemetry relay (fixed). No user-supplied outbound URL surface.

#### (c) Gaps

- **OWASP-01** [P1] — **No SBOM in release artifacts.** A06 (Vulnerable and Outdated Components) requires per-release component visibility for downstream consumers. Remediation: emit `bicameral-mcp-<version>.sbom.json` in the release pipeline (CycloneDX or SPDX).
- **OWASP-02** [P2] — **Ledger at rest is unencrypted.** SurrealKV file at `~/.bicameral/ledger.db` has OS-perm protection only. Acceptable for `$HOME`-scope local install; problematic for shared filesystems or team deployments. Remediation: document the at-rest threat model; for team-server activation (currently parked), declare encryption-at-rest requirement.
- **OWASP-03** [P1] — **No exact-pin lockfile** for downstream installs. `>=` floors leave install-time component drift. Remediation: ship `requirements.lock` (`pip freeze` of a known-good install) and document its use; OR declare the floor-only stance as deliberate with the operator's pipx/uv-managed env as the authority.
- **OWASP-04** [P0] — **Insecure Design (A04) — instruction-only defaults.** This is the entire #205 doctrine surface. Skills like `bicameral-report-bug`, `bicameral-ingest`, `bicameral-preflight` have user-facing privacy commitments ("by default, redact branch names", "extract keys only", "render attribution as redacted") that depend on the agent following SKILL.md text. A jailbroken agent or a prompt-injected upstream can bypass silently. Remediation: #205 Phase 1 + Phase 3 — codify `doctrine-deterministic-governance.md`, ship `lint_skill_governance.py`, retroactively migrate all instruction-only defaults to deterministic gates.
- **OWASP-05** [P2] — **Update-check URL not pinned beyond TLS.** `raw.githubusercontent.com` resolves whatever GitHub serves. A compromise of the BicameralAI/bicameral-mcp `main` branch would push a malicious recommended version that the next `bicameral.update` call would install. Remediation: cosign-sign the `RECOMMENDED_VERSION` content, verify signature in `handlers/update.py`. OR document the trust-on-first-use posture explicitly.
- **OWASP-06** [P2] — **No structured audit log.** A09 (Logging) gap. Remediation: same as SOC2-06.

---

### § 2.4 OWASP LLM Top 10 (v1.1, 2024)

This is **the highest-novelty surface for bicameral-mcp.** The system is an LLM agent's tool surface, ingesting LLM-generated and human-mixed content, surfacing decisions back into LLM reasoning. Most categories apply directly.

#### (a) Categories

- **LLM01 Prompt Injection** — direct or indirect manipulation of the LLM via untrusted content.
- **LLM02 Insecure Output Handling** — downstream systems trust LLM output without validation.
- **LLM03 Training Data Poisoning** — N/A (bicameral-mcp does not train).
- **LLM04 Model Denial of Service** — resource exhaustion via crafted inputs.
- **LLM05 Supply Chain Vulnerabilities** — model + plugin + data-source chain.
- **LLM06 Sensitive Information Disclosure** — model exposes secrets / PII.
- **LLM07 Insecure Plugin Design** — tool-surface design flaws.
- **LLM08 Excessive Agency** — agent does more than it should.
- **LLM09 Overreliance** — humans trust LLM output uncritically.
- **LLM10 Model Theft** — N/A (bicameral-mcp does not host a model).

#### (b) What bicameral-mcp does today

- **LLM01 Prompt Injection**:
  - Every `bicameral.ingest` call accepts untrusted content (transcripts, PR bodies, Slack threads). Content goes through classifier_version-tagged classification, then into the ledger. No pre-classification injection-canary scan.
  - `bicameral.preflight` returns `source_ref` strings to the agent; the #200 Phase 3 gate (`full` default; `redacted`/`hidden` opt-in pending #209 regex refinement) filters at render time.
  - **No deterministic prompt-injection canary detection** anywhere in the ingest path.
- **LLM02 Insecure Output Handling**:
  - Decisions surfaced to the agent shape downstream code edits. A manipulated decision (poisoned ingestion) IS an output-handling defect.
  - Agent-facing rendering is governed by skills (`skills/bicameral-output-formats/SKILL.md`) — instruction-only.
- **LLM04 Model DoS**:
  - **No documented size limit on `bicameral.ingest` payload.** A 10MB transcript would be classified, persisted, and re-rendered.
  - Preflight has a soft cap of ≤4 questions per turn (verified in skills); the `_filter_flow_plan` helper (#156 PR B) caps cross-flow drains.
  - SurrealDB embedded queries don't have explicit timeouts in handler paths.
- **LLM05 Supply Chain**:
  - Models the agent runs are operator-provided (Claude Code, Cursor, Codex). bicameral-mcp doesn't pin or attest models.
  - MCP transport pinned to stdio (no remote MCP endpoint).
  - Skills are operator-installed via `setup_wizard.py` — a malicious source repo could ship modified `skills/**/SKILL.md`.
- **LLM06 Sensitive Information Disclosure**:
  - The ledger holds whatever was ingested. If the operator ingested a transcript containing API keys, those keys live in the SurrealKV file. No detect-and-refuse on ingest. No periodic redaction sweep.
  - **`telemetry.py`'s allowlist** (skill name, duration, version, numeric diagnostics ONLY) is direct evidence of LLM06 control on the outbound surface.
  - **`signer_email_fallback`** (#200 Phase 2) controls one specific PII surface.
- **LLM07 Insecure Plugin Design**:
  - The MCP tool surface (13 tools) has no per-tool authority gradation. `bicameral.reset` (destructive, wipes data) requires a `confirm=True` parameter that the agent supplies. There's no operator approval for destructive tools — the agent can call any tool any time.
  - `bicameral.ingest` has no rate limit.
- **LLM08 Excessive Agency**:
  - `AskUserQuestion` from #175 is a deterministic gate that pulls operator into the loop for `supersede` / `keep_both` / `unrelated` calls.
  - Preflight `record_bypass` is a deterministic gate: bypasses are logged with reason text, gated by `preflight_bypass_tracking` (#200 Phase 3). Operator can review.
  - Ingest, ratify, link_commit, set_decision_level fire without human-in-loop today.
- **LLM09 Overreliance**:
  - The whole skill-text-as-default surface is LLM09 in disguise: the operator trusts that the model will follow SKILL.md. #205 codifies this.
- **LLM10 Model Theft**: N/A.

#### (c) Gaps

- **LLM-01** [P0] — **No prompt-injection canary scan on `bicameral.ingest` content.** A poisoned transcript can plant a "decision" the agent later acts on. Remediation: ship a server-side canary regex/heuristic check in `handlers/ingest.py` that flags content matching known-injection patterns (override-instruction, role-impersonation, exfiltration-request shapes); on hit, refuse-or-quarantine with operator notification. Track in `qor.scripts.prompt_injection_canaries` (qor-logic ships this for governance markdown; bicameral-mcp needs the runtime equivalent on user content).
- **LLM-02** [P1] — **No size limit on `bicameral.ingest`.** Remediation: add a `max_bytes` config knob at `.bicameral/config.yaml: ingest_max_bytes` with a 1 MiB default; refuse-with-reason on excess. Ship a deterministic gate at the ingest handler entry.
- **LLM-03** [P1] — **No SurrealDB query timeout.** Embedded queries can run unbounded. Remediation: wrap SurrealDB calls with a per-query timeout (5s default for read paths; 30s for full-tree drift detection).
- **LLM-04** [P0] — **No PII / secret detect-and-refuse on ingest.** Ingested content lands in the ledger as-is. Remediation: ship a server-side scan in `handlers/ingest.py` for common secret shapes (API key prefixes, AWS/GCP/Azure access keys, `.pem`-shaped private-key blocks, JWT shapes); on hit, refuse-with-reason and emit a low-noise warning. Track regex catalog separately from the bicameral-report-bug skill's existing redactor (which redacts AT REPORT TIME, not at ingest time).
- **LLM-05** [P1] — **No per-tool authority gradation on MCP boundary.** Destructive tools (`bicameral.reset`, `bicameral.ingest` with overwrite semantics) are equally callable as read tools. Remediation: declare an authority class on each MCP tool (`read` / `write` / `destructive`); `destructive` calls require operator confirmation via `AskUserQuestion`-style flow before the handler dispatches. Server-side enforced.
- **LLM-06** [P0] — **Skill-installed-by-malicious-source-repo supply-chain risk.** `setup_wizard.py` copies skills from the repo's `skills/` directory into `.claude/skills/`; a typosquatted fork or compromised mirror could ship modified SKILL.md. Remediation: pin skills via cosign-signed manifests; verify signatures in `setup_wizard._install_skills`.
- **LLM-07** [P1] — **`source_ref` redaction default is `full` (verbatim) per #209.** This is a known issue tracked separately. Remediation: ship #209 (refine regex + flip default to `redacted`).
- **LLM-08** [P2] — **`bicameral.ingest` has no rate limit.** A runaway agent can flood the ledger. Remediation: token-bucket rate limit per session_id; declare server-side enforcement.
- **LLM-09** [P1] — **`ratify`, `link_commit`, `set_decision_level` fire without human-in-loop on agent-initiated calls.** These are state-changing decisions. Remediation: declare each tool's HITL requirement deterministically; `AskUserQuestion`-gate the destructive ones.

---

### § 2.5 NIST AI RMF 1.0 (NIST AI 100-1)

#### (a) Function-level intersections

1. **GOVERN** — culture, accountability, policies. Roles defined, procedures documented.
2. **MAP** — context establishment, risk assessment.
3. **MEASURE** — analyze, assess, benchmark, and monitor risks.
4. **MANAGE** — risk response prioritization, resource allocation.

#### (b) What bicameral-mcp does today

- **GOVERN-1.1** (legal/regulatory requirements understood): `qor/references/doctrine-eu-ai-act.md` (in qor-logic, referenced not co-located). #205 codifies the deterministic-governance hard rule.
- **GOVERN-1.4** (decisions rationale documented): qor-logic gate chain produces plan/audit/implement/substantiate artifacts. Bicameral-mcp's own ledger is decision-rationale infrastructure for the operator's downstream code work, not for bicameral-mcp itself.
- **MAP-1.1** (intended purpose declared): `README.md` Quickstart + `docs/CONCEPT.md` (if present) declare scope.
- **MAP-3.1** (context, intended purpose, prohibited uses): partial — README declares purpose; prohibited uses not documented.
- **MAP-5.1** (impact assessment): qor-logic plan schema's `impact_assessment` block exists; bicameral-mcp itself doesn't author plans against itself.
- **MEASURE-1.1** (test/evaluate at scale): `tests/e2e/` flow harness plus `tests/eval/` preflight evaluation dataset. Real-world eval coverage tracked in #66, #89.
- **MEASURE-2.1** (track residual risks during operations): no production-side telemetry of AI-risk indicators (only usage telemetry).
- **MANAGE-1.1** (risk prioritization): override-friction in qor-logic (gate-override events emit shadow-genome logs with severity classification). Bicameral-mcp standalone has no analog.

#### (c) Gaps

- **NIST-RMF-01** [P1] — **No declared "prohibited uses."** MAP-3.1 / GOVERN-3.1. Remediation: add a "Prohibited uses" section to `README.md` and (if shipping) a `policies/acceptable-use.md`. Examples: do not ingest content the operator hasn't authorized to ingest; do not use as a substitute for HR/legal/medical/financial decision-making (limited-risk-AI boundary statement).
- **NIST-RMF-02** [P1] — **No production MEASURE function.** Plan-time evaluation exists (`tests/eval/`) but production deployments emit no AI-risk indicators (e.g. fraction of preflights bypassed, fraction of ingests classifier-rejected, drift incidence). Remediation: extend `preflight_telemetry.py` schema with AI-risk diagnostic counters; surface in the dashboard.
- **NIST-RMF-03** [P2] — **No documented MANAGE / risk-response procedure for operators.** When something goes wrong (corrupted ledger, jailbroken agent, prompt-injection incident), there's no operator runbook. Remediation: ship `docs/incident-response.md` (overlaps GDPR-07).
- **NIST-RMF-04** [P2] — **GOVERN-1.4 evidence trail relies on qor-logic.** Standalone bicameral-mcp operations don't produce per-change evidence. Remediation: declare in `docs/` that production change-control evidence requires qor-logic operation, OR ship a minimal in-tree change-trail (commit + CI + ledger merkle).

---

### § 2.6 EU AI Act (Regulation (EU) 2024/1689)

#### (a) What it requires (intersections)

1. **Risk-tier classification** — prohibited / high-risk / limited-risk / minimal-risk / GPAI.
2. **Limited-risk transparency obligations** (Art. 50) — disclose AI nature when interacting with humans.
3. **Risk-management system** (Art. 9, high-risk only) — continuous, iterative.
4. **Data and data governance** (Art. 10, high-risk only) — training/validation/test sets relevant + representative.
5. **Technical documentation** (Art. 11, high-risk only).
6. **Record-keeping** (Art. 12, high-risk only).
7. **Transparency** (Art. 13, high-risk only) — instructions for use, intended purpose.
8. **Human oversight** (Art. 14, high-risk only).
9. **Accuracy, robustness, cybersecurity** (Art. 15, high-risk only).

#### (b) What bicameral-mcp does today

- **Risk classification**: bicameral-mcp itself is a **developer-tool MCP server**, not a high-risk AI system per Annex III. It's most plausibly **limited risk** (an AI system intended to interact with natural persons). When operating under qor-logic with `high_risk_target: true`, the *downstream* system being supported may be high-risk; that triggers qor-logic's Art. 9 contract (`impact_assessment` block).
- **Art. 50 transparency**: no end-user-facing disclosure surface; bicameral-mcp talks to the AGENT, the agent talks to the operator. Operator already knows they're using AI.
- **Art. 14 human oversight**: `AskUserQuestion` flows from #175; preflight bypass-tracking from #200 Phase 3 (deterministic gate that records every bypass).
- **Cybersecurity (Art. 15 if applicable)**: covered by SOC 2 + OWASP walks above.

#### (c) Gaps

- **AI-ACT-01** [P2] — **Risk-tier classification not declared in repo.** Remediation: add a "EU AI Act stance" section to `README.md` declaring "limited risk; not intended for high-risk Annex III uses; high-risk-target operations supported only via qor-logic plan-time impact_assessment." Maps to Art. 50 transparency obligation by giving operators a one-line stance to cite.
- **AI-ACT-02** [P2] — **No prohibited-use declaration matching Annex III boundaries.** Same remediation as NIST-RMF-01.
- **AI-ACT-03** [P3] — **Art. 9 risk-management system** is qor-logic-resident, not bicameral-mcp-resident. If the bicameral-mcp server is ever operated for high-risk use, the standalone path lacks the cycle. Remediation: cross-reference qor-logic operation requirement in the limited-risk stance from AI-ACT-01.

---

## § 3. Boundary-statement standards

For each: **apply?** / **stance** / **deterministic gate (now)** / **gap IDs**.

### NIST CSF 2.0

- **Apply?** Conditional — applies if the operator's organization adopts CSF as its security framework.
- **Stance**: bicameral-mcp's controls map onto CSF Identify / Protect / Detect / Respond / Recover. Most overlap is captured under SOC 2 + OWASP walks.
- **Gate**: indirect — every gap from § 2.2 + § 2.3 maps to a CSF control.
- **Gap IDs**: covered by SOC2-* and OWASP-* IDs.

### NIST SSDF (NIST SP 800-218)

- **Apply?** Yes — bicameral-mcp is software ships and customers integrate; SSDF is the federal procurement-track baseline.
- **Stance**: PR-based code review; CI testing; signed commits via Co-Authored-By trailers; no signed releases yet.
- **Gate**: code review (manual), CI (`.github/workflows/`), Merkle ledger (operator-side).
- **Gap IDs**: SSDF-01 [P1] no signed release artifacts (overlaps OWASP-01 SBOM, SOC2-03 release evidence). SSDF-02 [P2] no documented threat model in repo.

### FIPS 140-3

- **Apply?** Conditional — applies if the operator deploys to FIPS-required environments (federal, defense, regulated finance).
- **Stance**: bicameral-mcp uses SHA-256 for the Merkle chain (FIPS-approved). TLS via Python's `ssl` module which links to OpenSSL (system-provided FIPS posture). No custom cryptography in-tree.
- **Gate**: stdlib `hashlib.sha256` (FIPS-approved primitive); SSL via `ssl` module (operator's OS posture).
- **Gap IDs**: FIPS-01 [P3] no documented FIPS-compliance stance in repo. Remediation: short paragraph in `README.md` or `docs/` declaring "uses FIPS-approved primitives only; FIPS posture inherited from OS-provided OpenSSL."

### HIPAA

- **Apply?** Conditional — only if customer ingests Protected Health Information.
- **Stance**: **bicameral-mcp does not process PHI.** This is a deliberate boundary.
- **Gate**: today, **no PHI-detect-and-refuse** at the ingest boundary. The boundary is enforced only by operator discipline.
- **Gap IDs**: HIPAA-01 [P1] gap matches LLM-04 (PII / secret detect-and-refuse). Remediation: extend the LLM-04 detector with PHI shapes (medical record numbers, patient identifiers, common PHI field names). Document the no-PHI stance in `README.md`.

### PCI DSS

- **Apply?** Conditional — only if customer ingests cardholder data.
- **Stance**: **bicameral-mcp does not process cardholder data.** Deliberate boundary.
- **Gate**: same as HIPAA-01 — no detect-and-refuse today.
- **Gap IDs**: PCI-01 [P2] gap matches LLM-04 / HIPAA-01. Add PAN-shape detection (Luhn-valid 13–19 digit sequences) to the unified detector.

### ISO 27001 / 27701

- **Apply?** Conditional — long-tail enterprise sales gate.
- **Stance**: SOC 2 evidence is convertible to ISO 27001 evidence; no current ISO certification path active.
- **Gate**: indirect — every SOC 2 control maps to an ISO 27001 Annex A control.
- **Gap IDs**: ISO-01 [P3] no ISO control-mapping document in repo. Future work tied to enterprise sales activation.

### CCPA / CPRA (California)

- **Apply?** Yes if customer is California-resident or processes California-resident data; bicameral-mcp itself collects telemetry from anywhere.
- **Stance**: parallels GDPR (data minimization, opt-out, access).
- **Gate**: same as GDPR.
- **Gap IDs**: GDPR-01 through GDPR-07 cover the same surface; no CCPA-specific gates needed.

### BIPA / state-specific (Illinois, Texas, etc.)

- **Apply?** No — bicameral-mcp does not process biometric or biometric-adjacent data.
- **Stance**: deliberate scope-out.
- **Gate**: no biometric ingestion path exists.
- **Gap IDs**: none.

---

## § 4. Standards summary table

| Standard | Apply? | Stance | Deterministic gate (now) | Gap IDs |
|---|---|---|---|---|
| GDPR | Yes | Operator-side controller; bicameral-mcp helps with data minimization by default | telemetry.py allowlist; preflight_telemetry hashing; signer_email_fallback; consent.py | GDPR-01..07 |
| SOC 2 | Yes (B2B sales) | Local-install posture; team deployments need extra controls | qor-logic gate chain; Merkle ledger; CI regression suite | SOC2-01..06 |
| OWASP Top 10 | Yes | Mostly clean except A04 (instruction-only defaults) and A06 (no SBOM) | list-form subprocess; parameter-bound queries; HTTPS-only outbound | OWASP-01..06 |
| OWASP LLM Top 10 | Yes — high novelty | Highest concentration of unmitigated risk | render-source-attribution gate (#200 P3); preflight HITL (#175); record_bypass tracking | LLM-01..09 |
| NIST AI RMF | Yes | Plan-time gates strong; production-time MEASURE absent | qor-logic plan/audit/implement/substantiate | NIST-RMF-01..04 |
| EU AI Act | Yes — limited risk | Limited-risk classification undeclared in repo | qor-logic Art. 9 contract for high-risk-target operations | AI-ACT-01..03 |
| NIST CSF 2.0 | Conditional | Maps onto SOC 2 + OWASP | (covered) | (covered above) |
| NIST SSDF | Yes | PR review + CI; no signed releases | code review, CI | SSDF-01..02 |
| FIPS 140-3 | Conditional | FIPS-approved primitives only | hashlib.sha256, system OpenSSL | FIPS-01 |
| HIPAA | Conditional | No PHI processing | (no detect-and-refuse today) | HIPAA-01 |
| PCI DSS | Conditional | No cardholder data | (no detect-and-refuse today) | PCI-01 |
| ISO 27001 / 27701 | Conditional | Convertible from SOC 2 | (overlap) | ISO-01 |
| CCPA / CPRA | Yes | Parallels GDPR | (covered) | (GDPR-* coverage) |
| BIPA / state-specific | No | No biometric ingestion path | structural scope-out | (none) |

---

## § 5. Gap synthesis

Flat table of all gaps with severity × likelihood → priority.

Severity: **P0** compliance-blocking / **P1** audit-finding-class / **P2** posture-improving / **P3** deferred-or-stub.
Likelihood: **H** default code path / **M** uncommon path / **L** only under jailbreak or injection.
Priority: derived; ordered top-to-bottom by P0→P3 then H→L within tier.
Type: **DG** deterministic-gate / **BS** boundary-statement / **DOC** documentation / **SD** scope-defer.

| ID | Standards | Component | Description (one-line) | Sev | Like | Priority | Type |
|---|---|---|---|---|---|---|---|
| OWASP-04 | OWASP A04, AI RMF GOVERN | 1.8 Skills | Instruction-only defaults — entire #205 doctrine surface | P0 | H | P0/H | DG |
| LLM-01 | OWASP-LLM-01 | 1.4 Ingest | No prompt-injection canary scan on `bicameral.ingest` content | P0 | H | P0/H | DG |
| LLM-04 | OWASP-LLM-06, HIPAA, PCI | 1.4 Ingest | No PII/secret detect-and-refuse on ingest | P0 | H | P0/H | DG |
| LLM-06 | OWASP-LLM-05 | 1.7 Install | Skill-install supply-chain — unsigned skills/ payload | P0 | M | P0/M | DG |
| SOC2-01 | SOC 2 CC1, CC6 | 1.1 MCP boundary | No authentication/authorization on MCP transport | P0 | H | P0/H | DOC + DG |
| GDPR-01 | GDPR Art. 17 | 1.2 Ledger | Right-to-erasure procedure undefined for append-mostly Merkle ledger | P1 | M | P1/M | DOC + DG |
| GDPR-02 | GDPR Art. 15 | 1.2 Ledger | No data-subject access endpoint | P1 | M | P1/M | DG |
| GDPR-05 | GDPR Art. 5(1)(c) | 1.4 Ingest | Signer-email default leaks local-part | P1 | H | P1/H | DG |
| LLM-02 | OWASP-LLM-04 | 1.4 Ingest | No size limit on `bicameral.ingest` | P1 | H | P1/H | DG |
| LLM-03 | OWASP-LLM-04 | 1.2 Ledger | No SurrealDB query timeout | P1 | M | P1/M | DG |
| LLM-05 | OWASP-LLM-07 | 1.1 MCP boundary | No per-tool authority gradation on MCP boundary | P1 | M | P1/M | DG |
| LLM-07 | OWASP-LLM-02 | 1.5 Preflight | `render_source_attribution` default is verbatim (#209) | P1 | H | P1/H | DG |
| LLM-09 | OWASP-LLM-08 | 1.1 MCP boundary | `ratify`, `link_commit`, `set_decision_level` fire without HITL | P1 | M | P1/M | DG |
| OWASP-01 | OWASP A06, SSDF | 1.7 Install | No SBOM in release artifacts | P1 | H | P1/H | DG |
| OWASP-03 | OWASP A06 | 1.7 Install | No exact-pin lockfile | P1 | M | P1/M | DOC |
| OWASP-05 | OWASP A08 | 1.7 Install | Update-check URL not pinned beyond TLS | P1 | M | P1/M | DG + DOC |
| SOC2-02 | SOC 2 A | (cross) | No availability commitment / MTTR | P1 | M | P1/M | DOC |
| SOC2-03 | SOC 2 CC, SSDF | 1.7 Install | No signed releases / change-control evidence | P1 | H | P1/H | DG |
| SOC2-06 | SOC 2 CC, OWASP A09 | (cross) | System-monitoring gaps for self-hosted operators | P1 | H | P1/H | DG |
| SSDF-01 | SSDF | 1.7 Install | No signed release artifacts (overlap with OWASP-01, SOC2-03) | P1 | H | P1/H | DG |
| HIPAA-01 | HIPAA, OWASP-LLM-06 | 1.4 Ingest | No PHI detect-and-refuse (folds into LLM-04) | P1 | M | P1/M | DG (folds) |
| NIST-RMF-01 | NIST AI RMF MAP-3.1 | (cross) | No "prohibited uses" declaration | P1 | M | P1/M | DOC |
| NIST-RMF-02 | NIST AI RMF MEASURE | 1.6 Telemetry | No production MEASURE / AI-risk telemetry | P1 | H | P1/H | DG |
| GDPR-03 | GDPR Ch. V | 1.6 Telemetry | Cross-border transfer documentation gap | P2 | M | P2/M | DOC |
| GDPR-04 | GDPR Art. 5(1)(e) | 1.6 Telemetry | No declared retention boundary for anonymous relay | P2 | M | P2/M | DOC |
| LLM-08 | OWASP-LLM-04 | 1.4 Ingest | No rate limit on `bicameral.ingest` | P2 | M | P2/M | DG |
| OWASP-02 | OWASP A02 | 1.2 Ledger | Ledger at rest unencrypted | P2 | L | P2/L | DOC |
| OWASP-06 | OWASP A09 | (cross) | No structured audit log (overlap SOC2-06) | P2 | H | P2/H | DG (folds) |
| PCI-01 | PCI DSS | 1.4 Ingest | No PAN detect-and-refuse (folds into LLM-04) | P2 | L | P2/L | DG (folds) |
| AI-ACT-01 | EU AI Act Art. 50 | (cross) | Risk-tier classification not declared | P2 | M | P2/M | DOC |
| AI-ACT-02 | EU AI Act Annex III | (cross) | No prohibited-use declaration (folds into NIST-RMF-01) | P2 | M | P2/M | DOC (folds) |
| SOC2-04 | SOC 2 A | (cross) | Backup/DR procedure for ledger undefined | P2 | M | P2/M | DOC |
| SOC2-05 | SOC 2 PI | 1.2 Ledger | classifier_version freeze (#162) gap | P2 | M | P2/M | DG (existing) |
| NIST-RMF-03 | NIST AI RMF MANAGE | (cross) | No documented MANAGE / incident-response runbook | P2 | M | P2/M | DOC |
| NIST-RMF-04 | NIST AI RMF GOVERN | (cross) | GOVERN-1.4 evidence trail relies on qor-logic | P2 | M | P2/M | DOC |
| SSDF-02 | SSDF | (cross) | No documented threat model in repo | P2 | M | P2/M | DOC |
| AI-ACT-03 | EU AI Act Art. 9 | (cross) | Art. 9 risk-management is qor-logic-resident | P3 | L | P3/L | DOC |
| GDPR-06 | GDPR Art. 30 | (cross) | No Records of Processing template | P3 | L | P3/L | DOC |
| GDPR-07 | GDPR Art. 33 | (cross) | No incident-response runbook (overlap NIST-RMF-03) | P3 | L | P3/L | DOC (folds) |
| FIPS-01 | FIPS 140-3 | (cross) | No documented FIPS stance | P3 | L | P3/L | DOC |
| ISO-01 | ISO 27001/27701 | (cross) | No ISO control-mapping doc | P3 | L | P3/L | DOC |

**Gap counts**: 5 P0, 18 P1, 13 P2, 5 P3. Total **41 gap IDs**, of which 7 are explicit folds (HIPAA-01 → LLM-04, OWASP-06 → SOC2-06, etc.).

---

## § 6. Remediation triage

Issue-filing strategy:

- **P0 gaps** — file individual issues immediately, label `compliance` + `governance` + `P0`, assign per-standard tag. These are commercial-blockers.
- **P1 gaps** — file individual issues immediately, label `compliance` + per-standard + `P1`. Folds (e.g. HIPAA-01 into LLM-04) get a single combined issue with cross-references.
- **P2 gaps** — file as one **rollup issue** "compliance audit P2 backlog" with a checklist; individual gap IDs in the body. Reduces issue-tracker noise; operator can split later if they earn separate work.
- **P3 gaps** — same rollup pattern as P2, separate issue.

Two rollups + one issue per P0/P1 (after folding) = manageable triage queue.

---

## § 7. Filed issues

(Populated by the issue-filing step. Each row maps a gap ID to a filed `BicameralAI/bicameral-mcp` issue.)

| Gap ID(s) | Issue # | Title (short) |
|---|---|---|
| OWASP-04 | (#205 already exists — this gap IS issue #205) | doctrine: deterministic privacy/security boundaries |
| LLM-01 | TBD | LLM01 prompt-injection canary scan on bicameral.ingest |
| LLM-04 + HIPAA-01 + PCI-01 + (fold) | TBD | LLM06 PII/secret/PHI/PAN detect-and-refuse on ingest |
| LLM-06 | TBD | LLM05 supply chain — sign skills/ payload |
| SOC2-01 | TBD | SOC2 CC1/CC6 — declare MCP trust boundary + auth shim plan |
| GDPR-01 | TBD | GDPR Art. 17 — right-to-erasure procedure for Merkle ledger |
| GDPR-02 | TBD | GDPR Art. 15 — data-subject-access CLI |
| GDPR-05 | TBD | GDPR Art. 5(1)(c) — signer-email default review |
| LLM-02 | TBD | LLM04 — ingest payload size limit |
| LLM-03 | TBD | LLM04 — SurrealDB query timeout |
| LLM-05 | TBD | LLM07 — per-tool authority gradation |
| LLM-07 | (#209 already exists) | refine render_source_attribution regex + flip default |
| LLM-09 | TBD | LLM08 — ratify/link_commit/set_decision_level HITL |
| OWASP-01 + SSDF-01 | TBD | OWASP A06 / SSDF — SBOM in release artifacts |
| OWASP-03 | TBD | OWASP A06 — exact-pin lockfile or stance declaration |
| OWASP-05 | TBD | OWASP A08 — sign or trust-on-first-use the RECOMMENDED_VERSION URL |
| SOC2-02 | TBD | SOC2 A — declare availability stance |
| SOC2-03 | TBD | SOC2 CC + SSDF — signed releases + change-control evidence |
| SOC2-06 + OWASP-06 | TBD | SOC2 CC + OWASP A09 — structured audit log emission |
| NIST-RMF-01 + AI-ACT-02 | TBD | NIST AI RMF MAP-3.1 + EU AI Act — prohibited-uses declaration |
| NIST-RMF-02 | TBD | NIST AI RMF MEASURE — production AI-risk telemetry |
| (P2 rollup) | TBD | compliance audit P2 backlog (13 IDs) |
| (P3 rollup) | TBD | compliance audit P3 backlog (5 IDs) |

---

## Appendix — references and method notes

- **Standards versions referenced**: GDPR (Reg. 2016/679, in force); SOC 2 TSP §100 (AICPA, 2017 + 2022 update); OWASP Top 10 2021; OWASP LLM Top 10 v1.1 (2024); NIST AI RMF 1.0 (NIST AI 100-1, 2023); EU AI Act (Reg. 2024/1689); NIST CSF 2.0 (2024); NIST SSDF (SP 800-218, 2022); FIPS 140-3 (NIST, 2019); HIPAA Security Rule (45 CFR 164); PCI DSS v4.0; ISO 27001:2022 / 27701:2019; CCPA / CPRA; BIPA (740 ILCS 14).
- **What this brief is not**: a substitute for legal counsel. Operators in regulated environments need their own counsel review. Specific certifications (SOC 2 Type II audit, HIPAA BAA, PCI assessment) require an external auditor.
- **Cross-references**:
  - #199 (Windows banner + uv installer) — recently shipped; touches install-path threat surface.
  - #200 Phase 2 (signer-email fallback) + Phase 3 (render-source-attribution + bypass-tracking) — recently shipped; provides several deterministic gates referenced above.
  - #205 (this brief's parent) — codifies the deterministic-governance hard rule.
  - #209 — `render_source_attribution: redacted` regex refinement; covers LLM-07.
  - #161 + #160 — team-server activation blockers; brief defers full team-server audit until activation.
  - #162 — classifier_version freeze; covers SOC2-05.
  - #65 — preflight failure-feedback telemetry; touches NIST-RMF-02 surface.
  - #148 — implicit-design-decision capture; expands ingest surface scope (revisit at activation).
- **Method notes**: walk performed against `86860e9` (post-#199 merge). All file-path citations verified with `Read` against current HEAD before authoring. No external network sources consulted. The brief is reproducible: every claim should be verifiable by re-reading the cited file at the cited line range.
