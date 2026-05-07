# Compliance & Security Audit — Bicameral MCP Research Brief

**Date**: 2026-05-06
**Repo HEAD**: `86860e9` (post-#199 merge, pre-this-PR)
**Server version**: 0.13.3
**Scope**: full user-facing runtime + agent-instruction surface (per `plan-205` option δ)
**Out of scope**: e2e harness (`tests/e2e/`), CI workflows (`.github/workflows/`), team-server full audit (boundary-statement only — per #161 inert state)
**Authoring methodology**: code-grounded walk (read actual files at HEAD; cite paths and line ranges); standards interpreted against the 2024–2026 published versions; gaps stated as observable behavior vs. requirement, not as opinion.

This document is **operator-consumed prose**, not LLM-consumed agent-instruction. It contains gap findings (`<STANDARD>-<NN>`) that map to filed remediation issues in the final § "Filed issues."

---

> **Codex reviewer note:** Overall, this is a strong code-grounded brief: the component inventory is concrete, the gap IDs are stable, and the repeated distinction between instruction-only guidance and deterministic gates is exactly the right central thesis. My comments below are mainly about audit defensibility: tighten product-vs-operator obligations, avoid overstating local-only risks as hosted/team risks, and make sure every high-priority finding has a reproducible evidence pointer.

## Review-history inputs

The Codex, Kilo, and Gemini sections below are raw review inputs preserved for audit trail. Some comments describe earlier unresolved states; the authoritative resolution is the **Reviewer disposition pass** immediately after them.

## Codex review comments

1. **Deployment-mode severity needs one more axis.** Several findings are clearly severe for a hosted, team-server, or shared deployment, but less severe for a single-user local stdio MCP process. SOC2-01 is the clearest example: "no auth on local stdio" is mostly a boundary statement, while "no auth on shared/team service" is audit-blocking. Consider adding a `Deployment` or `Trigger` column to the gap table so P0 findings remain defensible.

2. **The ingest path is correctly identified as the highest-risk write path.** I would make that wording explicit in section 1.4. The risk is not only that prompt-injected content influences the model; it can become durable ledger state and later flow back into preflight. That durable-feedback-loop framing makes LLM-01 and LLM-04 much easier to justify as P0.

3. **Instruction-only defaults are the strongest thesis in the brief.** Section 1.8 and OWASP-04 would benefit from one concrete before/after example: an instruction-only promise such as "redact branch names" versus a deterministic handler/config gate that redacts before returning data. That will help non-agent-systems readers understand why SKILL.md text cannot be treated as a control.

4. **Product obligations and operator obligations should be separated.** GDPR, HIPAA, PCI, ISO, and CCPA rows mix product controls, customer deployment triggers, and sales-readiness requirements. Consider changing "Apply?" to "Applicability trigger" and adding a short controller/processor note: for local/self-hosted installs, the operator likely controls most ledger content; Bicameral may separately control anonymous product telemetry depending on relay retention and tenant configuration.

5. **The GDPR access/erasure remediation should search more than email.** A data-subject access CLI that only searches signer email will miss the free-form transcript content that creates the risk. Include local-part, full email, source_ref, topic, decision description, file paths, and any future user/session identifiers.

6. **Detection controls should be framed as guardrails, not perfect classifiers.** LLM-01 and LLM-04 are good issues, but prompt-injection canaries and secret/PII regexes will have false positives and misses. The remediation should include quarantine, override, test fixtures, and measurement of refused/overridden/missed cases.

7. **The EU AI Act stance should be softened until counsel confirms classification.** "Limited risk" may be directionally right for an integrated AI-agent workflow, but bicameral-mcp alone may be better described as an AI-adjacent developer-tool component. Obligations likely attach to the integrated AI system and deployment context.

8. **The team-server boundary note has a table mismatch.** Section 1.9 says `TEAM-NN` gaps are recorded in section 4, but I do not see `TEAM-*` rows in the standards summary or gap synthesis. Either add a deferred team-server mini-table or change the sentence to say those gaps are intentionally not enumerated here.

9. **Evidence pointers need to match the methodology claim.** The appendix says file-path citations and line ranges were verified, but most findings currently cite components rather than exact lines. Add a small evidence appendix for P0/P1 gaps with `path:line` pointers, or adjust the methodology wording to avoid promising line-level citations.

10. **Issue filing can be grouped around foundation work.** The triage strategy is sensible, but a few P1s belong to the same implementation epic. "Ingest boundary guardrails" could cover payload size, prompt-injection quarantine, secret/PII/PHI/PAN detection, and rate limiting with separate acceptance criteria.

## Kilo review comments

1. **TEAM-NN cross-reference is a dangling promise.** Section 1.9 (line 113) states "Documented gaps recorded as `TEAM-NN` IDs in § 4 will not block this brief." Section 4 (the standards summary table, lines 419–434) contains zero `TEAM-*` rows. Either add a deferred mini-table (even a single-row `TEAM-00 [deferred]` placeholder) or rewrite § 1.9 to say team-server gaps are intentionally not enumerated. The Codex reviewer flagged this (comment #8) but it remains unfixed — it's an audit-defensibility gap in the brief itself.

2. **LLM-06 (P0/M) is overstated for the current deployment model.** The threat is "a typosquatted fork or compromised mirror could ship modified SKILL.md." But the operator already cloned or `pip install`-ed the repo — they've already trusted that supply chain. Skill files are co-located with the server code, not loaded from a separate channel. This is better classified as P1/M, or the description should be narrowed to the specific scenario where skill content diverges from server code (e.g., a future marketplace or remote-skill-loading feature that doesn't exist today).

3. **`bicameral.reset` `confirm=True` is agent-supplied, not operator-supplied.** Section 2.4(b) under LLM-07 notes this briefly: "requires a `confirm=True` parameter that the agent supplies." This deserves its own gap or at minimum a stronger callout in LLM-05. The "confirm" is a parameter the model fills in — it is not a human-in-the-loop gate. Calling it "requires confirmation" in any security context is misleading. Recommend adding a sentence in LLM-05 remediation explicitly stating: `confirm=True` is not a security gate; it is a prompt to the agent. A deterministic HITL gate would require an out-of-band operator action (e.g., stdin ack, interactive prompt to the operator's terminal).

4. **GDPR-05 (P1/H) severity is inflated for local-only single-user context.** The local-part of `git config user.email` is "leaked" into the operator's own local SurrealKV file on the operator's own machine. The data subject and the operator are the same person. This is P2 in a single-user local install and P1 only in a team-server context. Recommend a deployment-mode qualifier (aligning with Codex comment #1) or downgrading to P2 with a note that it upgrades to P1 when team-server activates.

5. **The ingest→ledger→preflight→agent feedback loop deserves an explicit risk-amplification callout.** Section 1.5 (line 80) identifies this individually ("a manipulated decision shapes downstream code edits") and Codex comment #2 flags the durable-feedback-loop framing. But neither section calls out the compounding nature: a single poisoned ingest creates a ledger entry that is subsequently surfaced by every future preflight for that topic/region, potentially influencing dozens of code edits before detection. This is a force-multiplier, not just a single-hop risk. Recommend adding a "Risk amplification" paragraph in § 1.4 or § 2.4 explicitly quantifying the blast radius.

6. **OWASP-03 (lockfile) P1/M is likely P2.** For a tool distributed via `uv tool install` / `pipx install` / `pip install`, the consuming package manager already resolves and locks dependencies at install time. The `>=` floors in `pyproject.toml` are standard Python packaging practice. The operator's environment (uv, pipx) is the lock authority, not this repo. Unless there's a specific known incompatibility, this is a posture note (P2/DOC), not a P1 gate.

7. **Ephemeral data surfaces are unaddressed.** The brief covers persistent storage (SurrealKV, JSONL, sqlite) but doesn't mention ephemeral surfaces: Python tempfile usage during ingest, OS swap/page file containing ledger contents, SurrealDB WAL segments before compaction, and crash dumps. For GDPR right-to-erasure (GDPR-01) and HIPAA/PCI boundary statements, these ephemeral copies are in scope. Recommend adding a brief subsection in § 1.2 or § 2.1 noting ephemeral-data posture.

8. **Consent versioning and re-consent mechanism is unclear.** `consent.py` is cited as storing a "policy version" at `~/.bicameral/consent.json`, but the brief doesn't state whether a policy-version bump triggers re-consent on next boot. If the telemetry allowlist changes (e.g., a new field is added to `telemetry.py`), does the existing consent marker still cover it? This affects GDPR Art. 7 (conditions for consent) and should be stated explicitly, either in § 2.1(b) or as a minor gap.

9. **`setup_wizard.py` modifies `.claude/settings.json` — cross-tool config surface unexamined.** Section 1.7 describes the wizard installing hooks in `.claude/settings.json`, but the security implications of a package modifying another tool's configuration file aren't analyzed. A compromised bicameral-mcp install could inject arbitrary Claude Code hooks. This is a supply-chain vector closely related to LLM-06 but distinct (it targets the agent host, not skill content). Recommend adding a note in § 1.7's trust boundary.

10. **Gap count validation passes.** I independently counted: 5 P0, 18 P1, 13 P2, 5 P3 = 41 total. Fold count of 7 is consistent with the table notation. The math is correct.

11. **SurrealDB version pinning is an unmentioned supply-chain surface.** `pyproject.toml` specifies `surrealdb>=2.0.0` — SurrealDB is the persistence layer holding the entire ledger. A breaking or malicious SurrealDB release (unlikely but nonzero) could compromise Merkle chain integrity. This folds into the broader OWASP-03 / supply-chain discussion but deserves a one-line callout given the criticality of the persistence layer.

12. **Merkle rebuild witness for GDPR-01 remediation is underspecified.** The proposed tombstone-and-rebuild procedure for right-to-erasure is architecturally sound, but who witnesses or attests the rebuild? If the operator runs the rebuild locally, there's no independent attestation that the tombstoned rows were actually excluded. For audit defensibility, the rebuild should emit a signed rebuild manifest (input seal hash, output seal hash, tombstone list, timestamp). This is a detail for the GDPR-01 remediation issue, not the brief itself, but worth noting so it isn't lost.

13. **Section 2.4(a) lists LLM03 and LLM10 as N/A but omits discussion of why LLM02 is sparse.** LLM02 (Insecure Output Handling) maps primarily to the preflight→agent path, but the brief doesn't enumerate it as a standalone gap — it's folded into LLM-07 (source_ref redaction) and the broader OWASP-04 instruction-only surface. A reader tracking OWASP LLM categories by number will wonder where LLM02's explicit treatment went. Recommend adding a one-line "LLM02: addressed by LLM-07 and OWASP-04; no separate gap ID" mapping note.

## Gemini CLI review comments

1. **GDPR-01 (Right to erasure vs Merkle chain):** The proposed tombstone-and-rebuild procedure could be operationally heavy and risks breaking downstream references. An alternative approach to support Art. 17 without rebuilding the chain is to use "crypto-shredding": store sensitive fields encrypted with a per-row or per-subject key, and include only the ciphertext in the Merkle hash. Erasure then simply requires deleting the key, rendering the data unrecoverable while leaving the cryptographic chain intact.

2. **LLM-01 & LLM-04 (Canary scans and Detect-and-Refuse):** While regexes and heuristic shapes are low-latency, they are notoriously brittle against novel prompt injection techniques. Consider specifying that the server-side scan should ideally be extensible to support a local, small-parameter classifier model specifically tuned for prompt injection detection, rather than relying purely on regex catalogs.

3. **LLM-03 (SurrealDB Query Timeout):** Ensure that the 5s/30s timeouts proposed are surfaced as configurable parameters in `.bicameral/config.yaml`. For operators with slower local machines or exceptionally large ledgers, hardcoded timeouts may lead to spurious failures that block legitimate workflows.

## Codex second-pass comments after Kilo/Gemini

1. **Add a reviewer-disposition pass before filing the remaining issues.** The three reviews now include several severity changes and scope corrections, especially for SOC2-01, LLM-06, GDPR-05, and OWASP-03. Before creating the remaining P1/P2 issues, add a short disposition table: `Comment`, `Decision`, `Reason`, `Gap IDs changed`, `Issue impact`. Otherwise the tracker may preserve stale P0/P1 rankings after the review consensus has moved.

2. **Create deployment profiles, not only deployment qualifiers.** The most useful next artifact is probably a compact matrix with `local single-user`, `team-server/shared`, and `hosted/managed` columns. Each gap can then carry the highest applicable severity per profile. This avoids repeatedly debating whether a finding is "really" P0 when the answer is profile-dependent.

3. **Define consent revocation semantics separately from opt-out and re-consent.** Kilo covered re-consent on policy-version changes, but the brief should also state what happens when an operator revokes telemetry consent: stop future sends only, delete local `device_id`, delete/rotate local consent marker, or request deletion from the relay/PostHog side. That matters for GDPR/CCPA access and deletion narratives.

4. **Turn deterministic gates into testable control requirements.** For each server-side gate proposed by #205/LLM-01/LLM-04/LLM-05/LLM-09, add acceptance criteria that include positive tests, negative tests, bypass/override tests, and telemetry/audit evidence. "Implemented a gate" will not be enough for audit; the control has to fail closed and produce reviewable evidence.

5. **Add configuration precedence and fail-closed behavior to the threat model.** The brief names several env vars and config knobs, but does not define precedence or safe behavior when config is missing, malformed, or contradictory. A small `config security model` section would reduce ambiguity around defaults like telemetry, raw preflight telemetry, signer email fallback, render attribution, and future ingest guardrails.

6. **Account for MCP host/tool-approval semantics as an external dependency.** Claude Code, Cursor, and Codex may differ in how they display tool calls, confirmations, stdio servers, and destructive actions. The server should not rely on host UX for security, but the brief should list MCP host behavior as a dependency that can change the effective risk posture.

## Reviewer disposition pass (2026-05-06)

Per Codex-2 #1: a single disposition table reconciling all four review layers, applied before downstream P1 issue-filing so the tracker carries the post-review consensus rather than stale per-layer rankings. Decisions: **apply** (folded into this PR) / **defer** (tracked for follow-up) / **note** (acknowledged, no action).

| Source | # | One-line | Decision | Reason / Gap-ID changes |
|---|---|---|---|---|
| Codex-1 | 1 | Add deployment-mode severity column | **applied** (1d82658) | New `Deployment trigger` column on § 5 table |
| Codex-1 | 2 | Ingest durable-feedback-loop framing | **applied** (1d82658) | § 1.4 "Risk amplification" paragraph |
| Codex-1 | 3 | Worked instruction-only vs deterministic example | **applied** (1d82658) | § 1.8 `bicameral-report-bug` worked example |
| Codex-1 | 4 | Product vs operator obligations split | **defer** | Substantive standards-table restructure; tracked for follow-up |
| Codex-1 | 5 | GDPR-02 search broader than email | **applied** (this commit) | GDPR-02 remediation extended; spec carried into eventual filed issue |
| Codex-1 | 6 | Detection controls are guardrails not classifiers | **applied** (this commit) | LLM-01 + LLM-04 remediation framing extended; comments to issues #212 + #213 |
| Codex-1 | 7 | EU AI Act classification softening | **applied** (1d82658) | § 2.6(b) + AI-ACT-01 reworded |
| Codex-1 | 8 | Team-server table mismatch | **applied** (1d82658) | § 1.9 rewritten |
| Codex-1 | 9 | Evidence pointers | **partial** (softened 1d82658) | Method-notes claim softened; full evidence appendix deferred |
| Codex-1 | 10 | Issue grouping around foundation work | **applied** (this commit) | § 6 triage updated to flag "ingest boundary guardrails" epic for the deferred P1 batch |
| Kilo | 1 | TEAM-NN dangling promise | **applied** (1d82658) | (same as Codex-1 #8) |
| Kilo | 2 | LLM-06 P0/M overstated | **applied** (this commit) | LLM-06 downgraded P0→P1, scope narrowed to remote-skill-loading future. Issue #214 relabeled + body updated. |
| Kilo | 3 | `bicameral.reset confirm=True` is agent-supplied, not HITL | **applied** (this commit) | § 2.4 LLM-05 + LLM-09 explicit callout; agent-supplied confirm parameters are not security gates |
| Kilo | 4 | GDPR-05 P1/H inflated for local-only | **applied** (1d82658) | Deployment trigger column captures team/hosted P1 vs local single-user P2 |
| Kilo | 5 | Force-multiplier framing for ingest→ledger→preflight | **applied** (1d82658) | (same as Codex-1 #2) |
| Kilo | 6 | OWASP-03 P1/M is likely P2 | **applied** (1d82658) | Deployment trigger column captures hosted P1 vs local P2 |
| Kilo | 7 | Ephemeral data surfaces | **applied** (this commit) | New gap **GDPR-08** (Python tempfiles, OS swap, SurrealDB WAL, crash dumps) |
| Kilo | 8 | Consent versioning + re-consent | **applied** (this commit) | New gap **GDPR-09** (covers Codex-2 #3 — consent revocation semantics) |
| Kilo | 9 | `setup_wizard.py` → `.claude/settings.json` cross-tool surface | **applied** (this commit) | New gap **LLM-11** (cross-tool config-file surface as supply-chain vector distinct from skill content) |
| Kilo | 10 | Gap count validation passes | **note** | No edit |
| Kilo | 11 | SurrealDB version pinning supply-chain callout | **applied** (this commit) | One-line note in OWASP-03 + § 1.2 trust boundary |
| Kilo | 12 | Merkle rebuild witness for GDPR-01 | **applied** (this commit) | One-line note in GDPR-01 remediation; full spec in eventual filed issue |
| Kilo | 13 | LLM02 mapping note | **applied** (this commit) | One-line in § 2.4(a) clarifying LLM02 is folded into LLM-07 + OWASP-04 |
| Gemini | 1 | GDPR-01 crypto-shredding alternative | **applied** (this commit) | Listed as alternative in GDPR-01 remediation |
| Gemini | 2 | LLM-01/LLM-04 extensible classifier | **applied** (this commit) | Remediation prose updated to allow regex catalog OR small classifier; comments on #212, #213 |
| Gemini | 3 | LLM-03 timeouts configurable | **applied** (this commit) | LLM-03 remediation prose calls out `.bicameral/config.yaml` knobs |
| Codex-2 | 1 | Reviewer-disposition pass | **applied** (this section) | This table |
| Codex-2 | 2 | Three-column deployment-profile matrix | **defer** | Single-column trigger from Codex-1 #1 is the compromise; full matrix tracked for follow-up |
| Codex-2 | 3 | Consent revocation semantics | **applied** (this commit) | Folded into new gap GDPR-09 |
| Codex-2 | 4 | Deterministic gates as testable control requirements | **applied** (this commit) | New § 6.2 "Control-acceptance criteria template" applies to every DG-typed gap; will land on issue bodies via comments |
| Codex-2 | 5 | Configuration precedence + fail-closed behavior | **applied** (this commit) | New short § 1.11 "Configuration precedence + fail-closed model" subsection |
| Codex-2 | 6 | MCP host UX as external dependency | **applied** (this commit) | § 1.1 trust boundary extended; new gap **MCP-01** |

**Net new gap IDs introduced in this pass**: CFG-01, GDPR-08, GDPR-09, LLM-11, MCP-01. Existing reclassification: LLM-06 P0/M → P1/M (narrowed scope). Updated counts in § 5 below.

**Filed issues affected**:
- **#214 (LLM-06)**: relabel `P0` → `P1`, update title + body to reflect narrowed scope.
- **#212 (LLM-01)** + **#213 (LLM-04)**: comment threads added per Codex-1 #6 + Gemini #2 + Codex-2 #4.

## § 1. Surface inventory

The 10 components in scope. For each: location, what it does, data it touches, external surfaces, trust boundary.

### 1.1 MCP server + tool dispatch

- **Location**: `server.py` (~1500 lines), `handlers/*.py` (24 handler modules).
- **What it does**: exposes 13 MCP tools to the connected agent (Claude Code, Cursor, Codex). Each tool dispatches into `handlers/<tool>.py`. The MCP boundary is the only programmatic surface the agent can reach.
- **Data touched**: tool arguments (agent-supplied JSON), repo-relative paths, ledger payloads, transcript text on `bicameral.ingest`, decision IDs, file paths on `bicameral.preflight`.
- **External surfaces**: stdio (MCP transport), filesystem (repo path, `~/.bicameral/`), embedded SurrealDB process, sqlite (code locator), git subprocess.
- **Trust boundary**: MCP transport is local stdio; the *agent* is operator-installed but executes model-generated tool calls. Inputs to handlers are **agent-controlled**, not operator-controlled. There is **no authentication, authorization, or rate-limiting** on the MCP boundary — the server trusts whoever can talk to its stdio.
- **MCP host UX is an external dependency, not a security gate** (Codex-2 #6 / new gap MCP-01): Claude Code, Cursor, Codex, and other MCP hosts differ in how they display tool calls, ask for confirmations, present stdio-server output, and surface destructive actions to the operator. The bicameral-mcp server **must not** rely on host UX for security — a host that auto-approves tool calls (or fails to surface them) silently bypasses any "the operator will see this" assumption. Track host-UX behavior as a per-host risk factor; document the assumption set in `docs/` for any deployment claim that depends on host-side confirmation surfaces. **Status (2026-05-06)**: Closed by `docs/policies/host-trust-model.md`.

### 1.2 Ledger persistence

- **Location**: `ledger/adapter.py`, `ledger/queries.py`, `ledger/schema.py`, `ledger/client.py`, `ledger/canonical.py`, `ledger/drift.py`, `ledger/status.py`, `ledger/ast_diff.py`.
- **What it does**: append-mostly decision ledger backed by embedded SurrealDB v2.x. Decisions, signoffs, code-region bindings, compliance check rows. SHA-256 Merkle hashing for chain integrity.
- **Data touched**: decision descriptions (free-form text from ingested transcripts), signer email (subject to `signer_email_fallback` policy from #200 Phase 2), classifier_version, decision_level, source_ref strings, file paths, region anchors, ratification state. Persisted indefinitely.
- **External surfaces**: filesystem (`surrealkv://~/.bicameral/ledger.db` by default; configurable via `SURREAL_URL`).
- **Trust boundary**: storage is **operator-local** unless team-mode is engaged. Append semantics make right-to-erasure non-trivial (see § 2.1.4). `pyproject.toml` pins `surrealdb>=2.0.0` (floor only); a malicious or breaking SurrealDB release would be installed automatically by the upgrade path. SurrealDB version pinning is a non-trivial supply-chain surface given the persistence layer holds the entire ledger including the Merkle chain (folds into OWASP-03; called out separately because the criticality is higher than a generic dependency).

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
- **Risk amplification (durable-feedback-loop)**: ingest is the system's only **durable write surface for free-form text**. Content classified into decisions does not stay at the ingest boundary — it lands in the ledger, and every subsequent `bicameral.preflight` call for the same topic / region / file path surfaces those decisions back to the agent as authoritative context. A single poisoned ingest is therefore a **force-multiplier**: one tampered transcript can shape dozens of downstream code edits over weeks or months before detection, and is silently re-inserted into the agent's reasoning context every time the affected scope is touched. This is the core defensibility argument for treating LLM-01 (canary scan) and LLM-04 (PII/secret detect-and-refuse) as P0 — they are not single-hop ingest validators; they are gates on the long-tail propagation surface.

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
- **Cross-tool config-file surface** (new gap LLM-11 from Kilo #9): `setup_wizard._install_hooks` modifies `.claude/settings.json` (Claude Code's config file, not bicameral-mcp's) and the analogous Cursor/Codex config files. A compromised bicameral-mcp install can therefore inject arbitrary Claude Code hooks — a supply-chain vector that is **distinct from the LLM-06 skill-content surface**: LLM-06 is about modified SKILL.md, LLM-11 is about modified host-config that runs operator-side commands at hook-fire-time. The two need separate gates (signed hook payloads vs signed skill manifests).

### 1.8 Skills (the agent-instruction surface)

- **Location**: `skills/**/SKILL.md` — 14 skill files defining how the agent should invoke MCP tools, render output, redact data, and prompt the user. Plus `skills/CLAUDE.md`, `skills/CONSTANTS.md`, `skills/bicameral-output-formats/SKILL.md` (shared output rendering rules).
- **What it does**: provides the agent with prose instructions on when/how to call MCP tools and how to format results. Includes user-facing prompts (`AskUserQuestion` from #175), telemetry-transparency notes, and (the #205 doctrine concern) **default-behavior instructions** that say "by default, redact X" / "by default, extract only keys" / "never include verbatim Y."
- **Data touched**: indirect — skills shape how the agent processes ingested content, but the skill files themselves are operator-installed markdown.
- **External surfaces**: read by the agent at session boot.
- **Trust boundary**: **the novel #205 attack class.** A jailbroken agent, a model regression, or a prompt-injected upstream content payload can ignore SKILL.md instructions silently. The defenses must live at server-side / config-load boundaries, not in skill text.
- **Instruction-only vs. deterministic gate (worked example)**: the `bicameral-report-bug` skill, before #200 Phase 2 + #204 landed, said in markdown: *"By default, extract only configuration keys from `.bicameral/config.yaml`; never include the values verbatim unless the operator explicitly opts in."* That is **instruction-only**: an agent that ignores the line — for any reason, including upstream prompt injection in the bug-report content itself — would emit the file verbatim including secrets. The replacement deterministic gate added in #204 is `events/writer._resolve_signer_email(email, mode)` plus the `signer_email_fallback` config reader: the *server* applies the policy at config-load time, the skill text only references the gate's existence. The skill markdown can now drift, the model can ignore it, the prompt-injection can override it — and the policy still holds because the keys-only extraction happens in Python, not in agent reasoning. Every privacy / security default in the brief should follow this shape.

### 1.9 Team-server boundary statement

Team-server plumbing exists in code (`events/team_adapter.py`, `events/team_server_bridge.py`, `events/team_server_consumer.py`, `events/team_server_pull.py`, plus the parked `team_server/` plan at `plan-priority-c-team-server-slack-v0.md`). Slack ingest is plumbed but **inert** because the `channel_allowlist` table is defined and queried but never populated (#161 — merged-to-dev, awaiting activation). The materializer dispatches on `event_type='ingest.completed'` but team-server emits `'ingest'` (#160 — merged-to-dev). Consequence: Slack content is not currently ingested, but the code path exists and would activate when both blockers are addressed.

**Posture for this brief**: full audit deferred until activation. Team-server gaps are **intentionally not enumerated** in § 4 / § 5 of this brief; they will be authored by the activation PR (the one that closes #161 + #160) so the audit reflects the actual activated topology rather than guesses. The activation PR is the right place to introduce `TEAM-NN` gap IDs.

### 1.10 CI/e2e scope-out

`tests/e2e/` and `.github/workflows/` are out of scope for this brief. The e2e harness ingests real-transcript fixtures and runs Claude Code in headless mode — that's a real attack surface (prompt injection in CI, supply-chain considerations) but a different threat-model shape than runtime. Bundling dilutes both. Tracked for separate audit.

### 1.11 Configuration precedence + fail-closed model (per Codex-2 #5)

The brief names several env vars and config knobs across components: `BICAMERAL_TELEMETRY`, `BICAMERAL_PREFLIGHT_TELEMETRY`, `BICAMERAL_PREFLIGHT_TELEMETRY_RAW`, `BICAMERAL_GUIDED_MODE`, `BICAMERAL_SKIP_CONSENT_NOTICE`, plus `.bicameral/config.yaml` fields `signer_email_fallback`, `render_source_attribution`, `preflight_bypass_tracking`, `guided`. The brief does **not** today define a single precedence rule or a fail-closed default for missing/malformed config. That is an audit-defensibility gap on its own.

**Proposed precedence model** (apply uniformly across all knobs):

1. **Explicit env-var override** (one-off operator action) wins.
2. **`.bicameral/config.yaml` field** (durable per-repo setting) is read when env is unset.
3. **Hardcoded default in `context.py`** — privacy-positive defaults (e.g. `signer_email_fallback="local-part-only"`, `preflight_bypass_tracking="enabled"`) when both env and config are silent.

**Fail-closed behavior**:
- Missing config file: use defaults. Log at INFO. Do not refuse to start.
- **Malformed config file** (yaml parse error, invalid value not in the field's `valid` set): use defaults, log at WARN, **do not silently accept the malformed value as opt-in**. This matters for privacy knobs where "default-on" is the safe answer (`render_source_attribution`) and for telemetry knobs where "default-off when uncertain" is safe (`BICAMERAL_PREFLIGHT_TELEMETRY` is already opt-in by design).
- **Contradictory config** (env says one thing, config says another): env wins per the precedence rule above; log at INFO with the chosen value.

This subsection is **descriptive of the intended model**; the actual code in `context.py` partially implements it but doesn't centralize the policy. New gap **CFG-01** [P2] tracks the gap between intent and implementation.

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

- **GDPR-01** [P1] — **Right-to-erasure procedure undefined for ledger entries.** Append-mostly Merkle chain conflicts with Art. 17. Three candidate remediations (operator picks one based on data-controller stance):
  - **(i) Tombstone-and-rebuild**: mark erased rows, recompute the chain from a designated seal, emit a signed rebuild-manifest (input seal hash, output seal hash, tombstone list, timestamp — per Kilo #12, the manifest is what gives the rebuild audit defensibility) and document the data-loss boundary.
  - **(ii) Crypto-shredding** (per Gemini #1): store sensitive fields encrypted with a per-row or per-subject key; the Merkle hash covers only ciphertext. Erasure becomes "delete the key" — chain stays intact, ciphertext is unrecoverable, and there's no rebuild step to attest. This is operationally lighter than tombstone-and-rebuild.
  - **(iii) Scope-out**: gate the ledger as "no personal data" via the LLM-04 PII-detect-and-refuse, OR explicitly exempt under Art. 17(3) overriding-legitimate-interests. Lowest engineering cost but the strongest claim to defend.
- **GDPR-02** [P1] — **No data-subject access endpoint.** A self-hosted operator can't honor an Art. 15 request without a CLI/MCP tool that emits all rows containing a given email or identifier. Remediation: `bicameral-mcp data-subject-access --identifier <value>` CLI that searches **the full identifier surface** (per Codex-1 #5) — not only `signer_email`, but also free-form `decision.description`, `source_ref`, `topic`, file-path strings, and any future user/session identifiers — and emits matching ledger rows + matching `~/.bicameral/preflight_events.jsonl` entries + matching `~/.bicameral/engagements.jsonl` entries. Email-only search would miss the bulk of the identifier-bearing surface, which lives in transcript content, not in audit metadata.
- **GDPR-03** [P2] — **Cross-border transfer documentation gap for anonymous relay.** Cloudflare Worker is global; PostHog tenant location undeclared. Remediation: declare the data flow in `docs/`, identify the PostHog tenant region, declare adequacy basis (likely SCCs / EU tenant choice).
- **GDPR-04** [P2] — **No documented retention boundary for anonymous relay data.** Client-side controls send-side; server-side retention is implicit. Remediation: document PostHog retention setting in the `consent.py` policy text.
- **GDPR-05** [P1] — **Signer-email fallback default leaks local-part.** `local-part-only` mode emits `kevin` from `kevin@example.com` — that's a pseudonym, but a recoverable one in many orgs. Remediation: change default to `redact`, OR document why `local-part-only` is the better tradeoff (audit traceability vs. PII), OR add a per-team config knob with `redact` recommended for ≥10-person teams.
- **GDPR-06** [P3] — **No Art. 30 records of processing template.** Operator-side gap for self-hosted deployments crossing the headcount/risk threshold. Remediation: ship a `docs/gdpr-records-of-processing.md` template.
- **GDPR-07** [P3] — **No incident-response runbook.** Remediation: ship `docs/incident-response.md` with the 72-hour Art. 33 timeline and operator decision tree.
- **GDPR-08** [P2] — **Ephemeral-data surfaces unaddressed** (per Kilo #7). The brief covers persistent storage (SurrealKV, JSONL, sqlite) but doesn't account for ephemeral copies that may contain ledger contents: Python tempfile usage during ingest, OS swap / page file under memory pressure, SurrealDB WAL segments before compaction, crash dumps. For Art. 17 right-to-erasure and the HIPAA / PCI boundary statements, these copies are in scope. Remediation: document ephemeral-data posture (`docs/ephemeral-data.md`) — what bicameral-mcp writes to tempfiles, what gets cleaned up on graceful shutdown, what survives crash; declare which OS-level mitigations (encrypted swap, secure tmpfs) the operator is responsible for.
- **GDPR-09** [P2] — **Consent versioning + revocation semantics undefined** (per Kilo #8 + Codex-2 #3). `consent.py` stores a `policy_version` at `~/.bicameral/consent.json`, but the brief doesn't state: (a) does a policy-version bump trigger re-consent on next boot if the telemetry allowlist changed, or does the existing marker still cover it (Art. 7 conditions for consent); (b) what happens when an operator revokes consent — stop future sends only, delete local `device_id`, delete/rotate the consent marker, request deletion from the relay/PostHog side. Remediation: define both flows explicitly in `consent.py` (re-consent fires on `POLICY_VERSION` bump if allowlist materially changed; revocation deletes `device_id` + marker locally and triggers a relay-side delete request if implementable).

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
- **SOC2-02** [P1] — **No availability commitment / MTTR target.** Acceptable for a developer tool; problematic for any "we run this for you" pricing tier. Remediation: declare in `docs/sla.md` whether bicameral-mcp is operator-run-only (no SLA) or has any hosted commitment. **Status (2026-05-06)**: Closed by `docs/sla.md`.
- **SOC2-03** [P1] — **No documented change-control evidence trail for the package itself.** PRs are reviewed but not signed; releases are not signed. Remediation: gpg-sign release tags; document the per-release evidence-collection procedure (PR list, CI runs, code review attribution). **Status (2026-05-07)**: Closed by `docs/RELEASE_EVIDENCE_PROCEDURE.md` (cosign-keyless tag-commit signing wired into publish.yml + operator-side `release.evidence_collect` helper).
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
- **LLM02 Insecure Output Handling** — folded into LLM-07 (`render_source_attribution` redaction) and the broader OWASP-04 instruction-only surface; no separate gap ID. Reader tracking by number: LLM02's preflight→agent path is the primary surface, governed by gaps LLM-07 + OWASP-04.
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
  - The MCP tool surface (13 tools) has no per-tool authority gradation. `bicameral.reset` (destructive, wipes data) takes a `confirm=True` parameter — but **the agent fills in that parameter**. `confirm=True` is a prompt to the agent's reasoning, **not a security gate**. There's no out-of-band operator approval for destructive tools (no stdin ack, no interactive terminal prompt) — the agent can call any tool, including destructive ones, at any time. Calling this "requires confirmation" in a security context would be misleading.
  - `bicameral.ingest` has no rate limit.
- **LLM08 Excessive Agency**:
  - `AskUserQuestion` from #175 is a deterministic gate that pulls operator into the loop for `supersede` / `keep_both` / `unrelated` calls.
  - Preflight `record_bypass` is a deterministic gate: bypasses are logged with reason text, gated by `preflight_bypass_tracking` (#200 Phase 3). Operator can review.
  - Ingest, ratify, link_commit, set_decision_level fire without human-in-loop today.
- **LLM09 Overreliance**:
  - The whole skill-text-as-default surface is LLM09 in disguise: the operator trusts that the model will follow SKILL.md. #205 codifies this.
- **LLM10 Model Theft**: N/A.

#### (c) Gaps

- **LLM-01** [P0] — **No prompt-injection canary scan on `bicameral.ingest` content.** A poisoned transcript can plant a "decision" the agent later acts on. Remediation: ship a server-side check in `handlers/ingest.py` that flags content matching known-injection patterns (override-instruction, role-impersonation, exfiltration-request shapes). The detector should be **extensible**: ship the regex catalog as v0, but allow operators to plug in a small local classifier model (per Gemini #2) tuned for prompt-injection detection — regexes will miss novel injections. Frame as a **guardrail, not a perfect classifier** (Codex-1 #6): on hit, **quarantine** rather than silently refuse, give the operator an override path, ship test fixtures (positive + negative + bypass cases), and emit measurement counters (refused / overridden / suspected-missed) into preflight telemetry. Track regex catalog versioned alongside classifier_version. Reference: `qor.scripts.prompt_injection_canaries` (qor-logic ships this for governance markdown; bicameral-mcp needs the runtime equivalent on user content).
- **LLM-02** [P1] — **No size limit on `bicameral.ingest`.** Remediation: add a `max_bytes` config knob at `.bicameral/config.yaml: ingest_max_bytes` with a 1 MiB default; refuse-with-reason on excess. Ship a deterministic gate at the ingest handler entry.
- **LLM-03** [P1] — **No SurrealDB query timeout.** Embedded queries can run unbounded. Remediation: wrap SurrealDB calls with a per-query timeout (5s default for read paths; 30s for full-tree drift detection). **Surface both as `.bicameral/config.yaml` knobs** (per Gemini #3) — operators with slower local machines or large ledgers will hit hardcoded timeouts as spurious failures otherwise.
- **LLM-04** [P0] — **No PII / secret detect-and-refuse on ingest.** Ingested content lands in the ledger as-is. Remediation: ship a server-side scan in `handlers/ingest.py` for common secret shapes (API key prefixes, AWS/GCP/Azure access keys, `.pem`-shaped private-key blocks, JWT shapes). Same shape as LLM-01: **extensible** (regex catalog v0; small classifier model as v1 per Gemini #2), framed as a **guardrail not a perfect classifier** (per Codex-1 #6) with quarantine + operator override + test fixtures + measurement counters. Track regex catalog separately from the `bicameral-report-bug` skill's existing redactor (which redacts at report-generation time, not at ingest time).
- **LLM-05** [P1] — **No per-tool authority gradation on MCP boundary.** Destructive tools (`bicameral.reset`, `bicameral.ingest` with overwrite semantics) are equally callable as read tools, and **agent-supplied `confirm=True` parameters do not constitute a security gate** (per Kilo #3): `confirm=True` is a prompt to the agent's reasoning, not an operator action. Remediation: declare an authority class on each MCP tool (`read` / `write` / `destructive`); `destructive` calls require **out-of-band operator confirmation** before the handler dispatches — the host `AskUserQuestion` flow when available, falling back to an interactive terminal prompt or stdin ack so a host that auto-approves still triggers a real operator action. Server-side enforced.
- **LLM-06** [P1 — narrowed scope] — **Skill content drift between server release and a future remote-skill-loading channel.** Original P0 framing was overstated (per Kilo #2): in the current install model, the operator already trusted the supply chain at `pip install` / `uv tool install` / `pipx install` time, and skills ship co-located with server code in the same wheel — there's no separate channel to compromise without compromising the wheel itself, which is covered by SOC2-03 (signed releases) and OWASP-01 (SBOM). The scenario where LLM-06 has independent value is a **future remote-skill-loading or marketplace feature** (none today): when skills could be pulled from a registry distinct from the server wheel, signing the skill payload separately becomes load-bearing. Remediation when that scope opens: cosign-signed `skills/MANIFEST.toml`, per-file SHA-256 verification at copy time. Until then, the gate is "don't ship remote skill-loading without first activating signed manifests" — a design constraint, not a runtime defect.
- **LLM-07** [P1] — **`source_ref` redaction default is `full` (verbatim) per #209.** This is a known issue tracked separately. Remediation: ship #209 (refine regex + flip default to `redacted`).
- **LLM-08** [P2] — **`bicameral.ingest` has no rate limit.** A runaway agent can flood the ledger. Remediation: token-bucket rate limit per session_id; declare server-side enforcement.
- **LLM-09** [P1] — **`ratify`, `link_commit`, `set_decision_level` fire without human-in-loop on agent-initiated calls.** These are state-changing decisions. Remediation: declare each tool's HITL requirement deterministically; gate the destructive ones with **out-of-band operator confirmation** (same shape as LLM-05 — `AskUserQuestion` when host supports it, terminal prompt fallback otherwise). Agent-supplied `confirm`-style parameters are not security gates here either.
- **LLM-11** [P0/M, all deployments] — **Cross-tool config-file modification surface** (per Kilo #9). `setup_wizard._install_hooks` modifies `.claude/settings.json` (Claude Code's host-config) and the analogous Cursor / Codex configs. A compromised bicameral-mcp install can therefore inject arbitrary Claude Code hook commands that fire as the operator at hook-trigger-time (PreToolUse, PostToolUse, SessionEnd). This is **distinct from LLM-06** (skill content): LLM-06 is text the agent reads, LLM-11 is shell commands the host runs. A signed-skills-manifest gate doesn't cover this. Remediation: ship a signed `hooks-manifest.json` separately (cosign-signed at release); `setup_wizard` verifies the manifest before writing to `.claude/settings.json`. The manifest is the second supply-chain leg distinct from skills + wheel.

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

- **NIST-RMF-01** [P1] — **No declared "prohibited uses."** MAP-3.1 / GOVERN-3.1. Remediation: add a "Prohibited uses" section to `README.md` and (if shipping) a `policies/acceptable-use.md`. Examples: do not ingest content the operator hasn't authorized to ingest; do not use as a substitute for HR/legal/medical/financial decision-making (limited-risk-AI boundary statement). **Status (2026-05-06)**: Closed by `docs/policies/acceptable-use.md`.
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

- **Risk classification**: this brief does **not** make a unilateral classification claim. bicameral-mcp standalone is most accurately described as an **AI-adjacent developer-tool component** (an MCP server that surfaces decisions to an agent host); EU AI Act risk-tier classification properly attaches to the **integrated AI system + the operator's deployment context**, not to a component in isolation. A naive "limited risk" claim is directionally plausible for most bicameral-mcp deployments but is premature without legal counsel review and without knowing the integrated-system shape. When operating under qor-logic with `high_risk_target: true`, the *downstream* system being supported may be high-risk; that triggers qor-logic's Art. 9 contract (`impact_assessment` block) and the bicameral-mcp surface inherits whatever obligations the integrated-system classification imposes.
- **Art. 50 transparency**: no end-user-facing disclosure surface; bicameral-mcp talks to the AGENT, the agent talks to the operator. Operator already knows they're using AI.
- **Art. 14 human oversight**: `AskUserQuestion` flows from #175; preflight bypass-tracking from #200 Phase 3 (deterministic gate that records every bypass).
- **Cybersecurity (Art. 15 if applicable)**: covered by SOC 2 + OWASP walks above.

#### (c) Gaps

- **AI-ACT-01** [P2] — **No risk-tier-classification stance declared in repo.** Remediation: add a "EU AI Act stance" section to `README.md` stating that bicameral-mcp is an AI-adjacent developer-tool component, that risk-tier classification properly attaches to the integrated AI system + deployment context, and that operators in regulated environments should obtain counsel review before claiming any specific tier on the integrated system's behalf. Cite Art. 50 transparency as the obligation that motivates the disclosure. Avoid making a unilateral "limited risk" claim — that determination is the integrator's, not the component's.
- **AI-ACT-02** [P2] — **No prohibited-use declaration matching Annex III boundaries.** Same remediation as NIST-RMF-01. **Status (2026-05-06)**: Closed by `docs/policies/acceptable-use.md`.
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
| GDPR | Yes | Operator-side controller; bicameral-mcp helps with data minimization by default | telemetry.py allowlist; preflight_telemetry hashing; signer_email_fallback; consent.py | GDPR-01..09 |
| SOC 2 | Yes (B2B sales) | Local-install posture; team deployments need extra controls | qor-logic gate chain; Merkle ledger; CI regression suite | SOC2-01..06 |
| OWASP Top 10 | Yes | Mostly clean except A04 (instruction-only defaults), A05 (config fail-closed model), and A06 (no SBOM) | list-form subprocess; parameter-bound queries; HTTPS-only outbound | OWASP-01..06, CFG-01 |
| OWASP LLM Top 10 | Yes — high novelty | Highest concentration of unmitigated risk | render-source-attribution gate (#200 P3); preflight HITL (#175); record_bypass tracking | LLM-01..11, MCP-01 |
| NIST AI RMF | Yes | Plan-time gates strong; production-time MEASURE absent | qor-logic plan/audit/implement/substantiate | NIST-RMF-01..04 |
| EU AI Act | Yes — limited risk | Limited-risk classification undeclared in repo | qor-logic Art. 9 contract for high-risk-target operations | AI-ACT-01..03 |
| NIST CSF 2.0 | Conditional | Maps onto SOC 2 + OWASP | (covered) | (covered above) |
| NIST SSDF | Yes | PR review + CI; no signed releases | code review, CI | SSDF-01..02 |
| FIPS 140-3 | Conditional | FIPS-approved primitives only | hashlib.sha256, system OpenSSL | FIPS-01 |
| HIPAA | Conditional | No PHI processing | (no detect-and-refuse today) | HIPAA-01 |
| PCI DSS | Conditional | No cardholder data | (no detect-and-refuse today) | PCI-01 |
| ISO 27001 / 27701 | Conditional | Convertible from SOC 2 | (overlap) | ISO-01 |
| CCPA / CPRA | Yes | Parallels GDPR | (covered) | (GDPR-* coverage, including GDPR-08/09) |
| BIPA / state-specific | No | No biometric ingestion path | structural scope-out | (none) |

---

## § 5. Gap synthesis

Flat table of all gaps with severity × likelihood → priority.

Severity: **P0** compliance-blocking / **P1** audit-finding-class / **P2** posture-improving / **P3** deferred-or-stub.
Likelihood: **H** default code path / **M** uncommon path / **L** only under jailbreak or injection.
Deployment trigger: **all** applies to every shape / **local-OK** applies but local stdio default is acceptable in practice / **team/hosted** only matters when team-server activates or in a hosted product / **pre-team** must be addressed before team-mode activation. Severity is stated for the highest-applicable deployment shape; readers using the brief for a narrower shape may downgrade per the trigger column.
Priority: derived; ordered top-to-bottom by P0→P3 then H→L within tier.
Type: **DG** deterministic-gate / **BS** boundary-statement / **DOC** documentation / **SD** scope-defer.

| ID | Standards | Component | Description (one-line) | Sev | Like | Deployment trigger | Type |
|---|---|---|---|---|---|---|---|
| OWASP-04 | OWASP A04, AI RMF GOVERN | 1.8 Skills | Instruction-only defaults — entire #205 doctrine surface | P0 | H | all | DG |
| LLM-01 | OWASP-LLM-01 | 1.4 Ingest | No prompt-injection canary scan on `bicameral.ingest` content | P0 | H | all | DG |
| LLM-04 | OWASP-LLM-06, HIPAA, PCI | 1.4 Ingest | No PII/secret detect-and-refuse on ingest | P0 | H | all | DG |
| LLM-06 | OWASP-LLM-05 | 1.7 Install | Skill content drift between server release and future remote-skill-loading channel (scope-narrowed per Kilo #2) | P1 | M | future-feature | DG (design constraint) |
| LLM-11 | OWASP-LLM-05 | 1.7 Install | Cross-tool config-file modification surface (`setup_wizard` writes to `.claude/settings.json`) | P0 | M | all | DG |
| MCP-01 | OWASP-LLM-07 | 1.1 MCP boundary | MCP host UX is external dependency, not security gate (host may auto-approve tool calls) | P1 | M | all | DOC + DG |
| CFG-01 | OWASP A05 | (cross) | No centralized configuration precedence + fail-closed model | P2 | M | all | DG (centralization) |
| GDPR-08 | GDPR Art. 5(1)(c), Art. 17 | 1.2 Ledger | Ephemeral-data surfaces (tempfiles, swap, WAL, crash dumps) unaddressed | P2 | M | all | DOC |
| GDPR-09 | GDPR Art. 7 | 1.6 Telemetry | Consent versioning + revocation semantics undefined | P2 | M | all | DG + DOC |
| SOC2-01 | SOC 2 CC1, CC6 | 1.1 MCP boundary | No authentication/authorization on MCP transport | P0 | H | pre-team / hosted (P3 boundary-statement for local-only) | DOC + DG |
| GDPR-01 | GDPR Art. 17 | 1.2 Ledger | Right-to-erasure procedure undefined for append-mostly Merkle ledger | P1 | M | all | DOC + DG |
| GDPR-02 | GDPR Art. 15 | 1.2 Ledger | No data-subject access endpoint | P1 | M | all | DG |
| GDPR-05 | GDPR Art. 5(1)(c) | 1.4 Ingest | Signer-email default leaks local-part | P1 | H | team/hosted (P2 for local single-user) | DG |
| LLM-02 | OWASP-LLM-04 | 1.4 Ingest | No size limit on `bicameral.ingest` | P1 | H | all | DG |
| LLM-03 | OWASP-LLM-04 | 1.2 Ledger | No SurrealDB query timeout | P1 | M | all | DG |
| LLM-05 | OWASP-LLM-07 | 1.1 MCP boundary | No per-tool authority gradation on MCP boundary | P1 | M | all | DG |
| LLM-07 | OWASP-LLM-02 | 1.5 Preflight | `render_source_attribution` default is verbatim (#209) | P1 | H | all | DG |
| LLM-09 | OWASP-LLM-08 | 1.1 MCP boundary | `ratify`, `link_commit`, `set_decision_level` fire without HITL | P1 | M | all | DG |
| OWASP-01 | OWASP A06, SSDF | 1.7 Install | No SBOM in release artifacts | P1 | H | all | DG |
| OWASP-03 | OWASP A06 | 1.7 Install | No exact-pin lockfile | P1 | M | hosted (P2 for local — uv/pipx provides install-time lock) | DOC |
| OWASP-05 | OWASP A08 | 1.7 Install | Update-check URL not pinned beyond TLS | P1 | M | all | DG + DOC |
| SOC2-02 | SOC 2 A | (cross) | No availability commitment / MTTR | P1 | M | hosted | DOC |
| SOC2-03 | SOC 2 CC, SSDF | 1.7 Install | No signed releases / change-control evidence | P1 | H | all | DG |
| SOC2-06 | SOC 2 CC, OWASP A09 | (cross) | System-monitoring gaps for self-hosted operators | P1 | H | all | DG |
| SSDF-01 | SSDF | 1.7 Install | No signed release artifacts (overlap with OWASP-01, SOC2-03) | P1 | H | all | DG (folds) |
| HIPAA-01 | HIPAA, OWASP-LLM-06 | 1.4 Ingest | No PHI detect-and-refuse (folds into LLM-04) | P1 | M | all | DG (folds) |
| NIST-RMF-01 | NIST AI RMF MAP-3.1 | (cross) | No "prohibited uses" declaration | P1 | M | all | DOC |
| NIST-RMF-02 | NIST AI RMF MEASURE | 1.6 Telemetry | No production MEASURE / AI-risk telemetry | P1 | H | all | DG |
| GDPR-03 | GDPR Ch. V | 1.6 Telemetry | Cross-border transfer documentation gap | P2 | M | all | DOC |
| GDPR-04 | GDPR Art. 5(1)(e) | 1.6 Telemetry | No declared retention boundary for anonymous relay | P2 | M | all | DOC |
| LLM-08 | OWASP-LLM-04 | 1.4 Ingest | No rate limit on `bicameral.ingest` | P2 | M | all | DG |
| OWASP-02 | OWASP A02 | 1.2 Ledger | Ledger at rest unencrypted | P2 | L | team/hosted | DOC |
| OWASP-06 | OWASP A09 | (cross) | No structured audit log (overlap SOC2-06) | P2 | H | all | DG (folds) |
| PCI-01 | PCI DSS | 1.4 Ingest | No PAN detect-and-refuse (folds into LLM-04) | P2 | L | all | DG (folds) |
| AI-ACT-01 | EU AI Act Art. 50 | (cross) | No risk-tier-classification stance declared | P2 | M | all | DOC |
| AI-ACT-02 | EU AI Act Annex III | (cross) | No prohibited-use declaration (folds into NIST-RMF-01) | P2 | M | all | DOC (folds) |
| SOC2-04 | SOC 2 A | (cross) | Backup/DR procedure for ledger undefined | P2 | M | all | DOC |
| SOC2-05 | SOC 2 PI | 1.2 Ledger | classifier_version freeze (#162) gap | P2 | M | all | DG (existing) |
| NIST-RMF-03 | NIST AI RMF MANAGE | (cross) | No documented MANAGE / incident-response runbook | P2 | M | all | DOC |
| NIST-RMF-04 | NIST AI RMF GOVERN | (cross) | GOVERN-1.4 evidence trail relies on qor-logic | P2 | M | all | DOC |
| SSDF-02 | SSDF | (cross) | No documented threat model in repo | P2 | M | all | DOC |
| AI-ACT-03 | EU AI Act Art. 9 | (cross) | Art. 9 risk-management is qor-logic-resident | P3 | L | all | DOC |
| GDPR-06 | GDPR Art. 30 | (cross) | No Records of Processing template | P3 | L | team/hosted | DOC |
| GDPR-07 | GDPR Art. 33 | (cross) | No incident-response runbook (overlap NIST-RMF-03) | P3 | L | all | DOC (folds) |
| FIPS-01 | FIPS 140-3 | (cross) | No documented FIPS stance | P3 | L | all | DOC |
| ISO-01 | ISO 27001/27701 | (cross) | No ISO control-mapping doc | P3 | L | hosted | DOC |

**Gap counts (post-disposition)**: 5 P0 (was 5; LLM-06 downgraded to P1; LLM-11 added as P0), 20 P1 (was 18; LLM-06 added scope-narrowed; MCP-01 added), 16 P2 (was 13; +CFG-01, GDPR-08, GDPR-09), 5 P3 unchanged. Total **46 gap IDs** (up from 41), of which 7 are explicit folds.

---

## § 6. Remediation triage

Issue-filing strategy:

- **P0 gaps** — file individual issues immediately, label `compliance` + `governance` + `P0`, assign per-standard tag. These are commercial-blockers.
- **P1 gaps** — file individual issues immediately, label `compliance` + per-standard + `P1`. Folds (e.g. HIPAA-01 into LLM-04) get a single combined issue with cross-references.
- **P2 gaps** — file as one **rollup issue** "compliance audit P2 backlog" with a checklist; individual gap IDs in the body. Reduces issue-tracker noise; operator can split later if they earn separate work.
- **P3 gaps** — same rollup pattern as P2, separate issue.

Two rollups + one issue per P0/P1 (after folding) = manageable triage queue.

### § 6.1 Epic grouping for the deferred P1 batch (per Codex-1 #10)

Several P1 gaps share an implementation epic. Filing them as separate issues without an epic header invites duplicate work and inconsistent design. Recommended epic grouping for the deferred P1 filing:

- **Ingest boundary guardrails epic** — covers LLM-02 (size limit), LLM-08 (rate limit), LLM-04 (PII/secret/PHI/PAN — already filed as #213), LLM-01 (prompt-injection canary — already filed as #212). One design surface (`handlers/ingest.py` middleware), one set of acceptance criteria, four sub-tasks.
- **Per-tool authority gradation epic** — covers LLM-05 (per-tool authority class), LLM-09 (HITL on ratify/link_commit/set_decision_level). Both want the same out-of-band-confirmation primitive; building it twice is wasteful.
- **Supply-chain and release-integrity epic** — covers OWASP-01 (SBOM), OWASP-03 (lockfile or floor-only stance), SOC2-03 (signed releases), SSDF-01 (signed artifacts), OWASP-05 (RECOMMENDED_VERSION pinning), LLM-11 (signed hooks-manifest), LLM-06 (signed skills-manifest, scope-narrowed). One release-integrity design, multiple consumers.
- **Telemetry & consent epic** — covers GDPR-04 (retention), GDPR-09 (versioning + revocation), NIST-RMF-02 (production MEASURE).

The remaining P1s (MCP-01, GDPR-01, GDPR-02, GDPR-05, LLM-03, LLM-07, NIST-RMF-01, SOC2-02, SOC2-06) are individually scoped and don't fold cleanly into an epic.

### § 6.2 Control-acceptance criteria template (per Codex-2 #4)

Every gap typed `DG` (deterministic gate) ships with the same six-section acceptance template — "implemented a gate" is not enough for audit; the control has to fail closed and produce reviewable evidence:

1. **Positive test** — gate triggers correctly on the in-scope condition (e.g. canary regex hits, secret pattern matches, destructive-tool call without operator confirmation).
2. **Negative test** — gate does not trigger on out-of-scope content (false-positive bound).
3. **Bypass / override test** — operator-supplied override path (where defined) actually bypasses, and the bypass event is logged with reason text.
4. **Fail-closed test** — when the gate's config is malformed or the underlying detector is unavailable, the system refuses-or-defaults rather than silently passing through.
5. **Telemetry / audit evidence** — the gate's decisions emit into `~/.bicameral/preflight_events.jsonl` (or an equivalent local audit JSONL) with refused / overridden / suspected-missed counters.
6. **Documentation pointer** — operator-readable doc explains the gate, its config knobs, and the override procedure.

Issue bodies for every DG gap should adopt this template as their acceptance criteria. Without all six, the implementation isn't audit-ready even if the gate technically exists.

---

## § 7. Filed issues

Initial P0 gaps were filed individually (4 new issues + #205 covering OWASP-04). Post-disposition added **LLM-11** as a new P0 that still needs filing or folding into the supply-chain signing epic. P1 individual issues + P2/P3 rollups remain deferred pending operator review of this brief; tracking entries below remain `TBD` until the operator's call.

| Gap ID(s) | Issue # | Title (short) |
|---|---|---|
| OWASP-04 | #205 (already exists — this gap IS issue #205) | doctrine: deterministic privacy/security boundaries |
| LLM-01 | #212 | LLM01 prompt-injection canary scan on bicameral.ingest |
| LLM-04 + HIPAA-01 + PCI-01 (fold) | #213 | LLM06 PII/secret/PHI/PAN detect-and-refuse on ingest |
| LLM-06 | #214 | LLM05 supply chain — sign skills/ payload (scope-narrowed P1) |
| LLM-11 | folded into epic #218 | signed hook/config manifest for host-config writes (P0 sub-task of supply-chain epic) |
| MCP-01 | #220 | LLM07 — MCP host UX is not a security gate |
| SOC2-01 | #215 | SOC2 CC1/CC6 — declare MCP trust boundary + auth shim plan |
| GDPR-01 | #221 | GDPR Art. 17 — right-to-erasure procedure for Merkle ledger |
| GDPR-02 | #222 | GDPR Art. 15 — data-subject-access CLI |
| GDPR-05 | #223 | GDPR Art. 5(1)(c) — signer-email default review |
| LLM-02 | folded into epic #216 | LLM04 — ingest payload size limit (sub-task of ingest-boundary-guardrails epic) |
| LLM-03 | #224 | LLM04 — SurrealDB query timeout |
| LLM-05 | folded into epic #217 | LLM07 — per-tool authority gradation (sub-task of authority-gradation epic) |
| LLM-07 | #209 (already exists) | refine render_source_attribution regex + flip default |
| LLM-08 | folded into epic #216 | ingest rate limit (P2; sub-task of ingest-boundary-guardrails epic) |
| LLM-09 | folded into epic #217 | LLM08 — ratify/link_commit/set_decision_level HITL (sub-task of authority-gradation epic) |
| OWASP-01 + SSDF-01 | folded into epic #218 | OWASP A06 / SSDF — SBOM in release artifacts (sub-task of supply-chain epic) |
| OWASP-03 | folded into epic #218 | OWASP A06 — exact-pin lockfile or stance declaration |
| OWASP-05 | folded into epic #218 | OWASP A08 — sign or trust-on-first-use the RECOMMENDED_VERSION URL |
| SOC2-02 | #226 | SOC2 A — declare availability stance |
| SOC2-03 | folded into epic #218 | SOC2 CC + SSDF — signed releases + change-control evidence |
| SOC2-06 + OWASP-06 | #227 | SOC2 CC + OWASP A09 — structured audit log emission |
| NIST-RMF-01 + AI-ACT-02 | #225 | NIST AI RMF MAP-3.1 + EU AI Act — prohibited-uses declaration |
| GDPR-04 | folded into epic #219 | GDPR Art. 5(1)(e) — anonymous-relay retention (sub-task of telemetry-and-consent epic) |
| GDPR-09 | folded into epic #219 | GDPR Art. 7 — consent versioning + revocation (sub-task of telemetry-and-consent epic) |
| NIST-RMF-02 | folded into epic #219 | NIST AI RMF MEASURE — production AI-risk telemetry (sub-task of telemetry-and-consent epic) |
| (P2 rollup incl. CFG-01, GDPR-08) | TBD | compliance audit P2 backlog (16 IDs) |
| (P3 rollup) | TBD | compliance audit P3 backlog (5 IDs) |

**Epic trackers** (filed 2026-05-06):
- **#216** Ingest boundary guardrails (LLM-01 / LLM-02 / LLM-04 / LLM-08; sub-issues #212, #213)
- **#217** Per-tool authority gradation (LLM-05, LLM-09)
- **#218** Supply-chain & release-integrity (OWASP-01 / OWASP-03 / OWASP-05 / SOC2-03 / SSDF-01 / LLM-06 / LLM-11; sub-issue #214)
- **#219** Telemetry & consent (GDPR-04, GDPR-09, NIST-RMF-02)

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
- **Method notes**: walk performed against `86860e9` (post-#199 merge). File-path citations were verified with `Read` against current HEAD before authoring; **most findings cite components and module locations rather than exact `path:line` evidence pointers**. A line-level evidence appendix would strengthen audit defensibility but is deferred — the brief's findings are reproducible via grep + file inspection at the cited components, but readers seeking line-level provenance should re-walk against current HEAD rather than relying on this brief alone. No external network sources consulted.
