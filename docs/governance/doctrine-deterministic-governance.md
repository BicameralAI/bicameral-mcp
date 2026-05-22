# Doctrine: Deterministic Governance Boundaries (#205)

## The hard rule

> **Prompt/skill-level instruction is suggestive but never qualifies as governance. Governance requires deterministic gates.**

This applies to every privacy / security / compliance default in the bicameral-mcp surface. Skill text in `skills/**/SKILL.md` is *orchestration glue* — it tells the agent **how** to use the tools we ship. It is **never the only gate** that enforces a default behavior the project commits to.

## Why this matters

Several recently-shipped skills implement privacy / security defaults via SKILL.md instructions to the agent — *"extract only the keys, by default"*, *"redact branch names"*, *"never include verbatim ledger entries"*. A `AskUserQuestion` toggle is a deterministic gate (the user's answer becomes a boolean the handler reads). But the *default behavior* depends on the agent following the markdown faithfully. A jailbroken agent, a model regression, or a prompt-injection upstream can bypass instruction-only defaults silently. The leak surface is invisible until a downstream incident.

For bicameral-mcp to support customers in regulated environments — any team using the team-server JSONL substrate over git, any ingest of compliance-sensitive transcripts/PRDs/Slack threads — the privacy and security defaults need to be enforced **at server-side and config-load boundaries**, not at agent-instruction time.

## Suggestive vs governance

| Class | Property | Example | Sufficient for governance? |
|---|---|---|---|
| **Suggestive** | The agent CHOOSES whether to comply | SKILL.md: *"By default, redact branch names from output."* — agent may or may not follow | ❌ No |
| **Governance** | The system ENFORCES regardless of agent compliance | `handlers/ingest.py:_filter_redacted_branches()` strips branch names BEFORE the payload reaches the model | ✅ Yes |

The suggestive instruction can still exist — it's useful agent-side guidance for graceful degradation, observability, and consistent UX. But it cannot be the *only* enforcement mechanism for a privacy or security commitment.

## Worked examples

### ✅ Good: env-flag gate

`release/manifest_verify.py` verifies the bundled `hooks-manifest.json` signature (keyless cosign / sigstore) and cross-checks the SHA-256 of every hook command before the installer writes it. It is **fail-closed**: a `SignatureError` aborts the install regardless of what skill text says or what the agent attempts. The one bypass — `BICAMERAL_HOOKS_VERIFY_DISABLE=1` — is an env-var decision read server-side, and even that is audited (it writes a severity-3 `verification_bypassed` ledger event). The gate is the env check on the server, not agent instruction.

### ✅ Good: API-key indirection in config

`events/sources/granola.py::_build_default_client()` reads the API key from `os.environ[<api_key_env>]` based on the config's `api_key_env` field. The config holds the env-var *name*, not the secret. This is a deterministic gate: even if the skill said "feel free to inline the key in config", the handler still reads from env.

### ✅ Good: ingest filter

`handlers/ingest.py` runs sensitive-data detection against every ingest payload. The skill `skills/bicameral-ingest/SKILL.md` advises the agent on how to avoid triggering refusals, but the refusal itself is server-side: PII/secret/PHI/PAN patterns return `_IngestRefused` regardless of what the agent thinks should happen.

### ❌ Bad: SKILL.md-only default

A hypothetical `skills/bicameral-foo/SKILL.md` says:

> By default, the `foo` tool extracts only the public keys and discards values.

…with no `handlers/foo.py` filter performing the extraction. The agent receives the full payload + the instruction to "extract only keys". A jailbroken agent that ignores the instruction leaks values. Not governance.

The fix: the server-side filter strips values before constructing the agent-visible payload. The skill text then describes the contract, but the gate is the filter.

## The `governance-gates.yaml` registry

This file at the repo root declares the project's deterministic gates so the lint at `scripts/lint_skill_governance.py` can match SKILL.md text against registered gates.

Schema (minimal Phase 1 shape):

```yaml
gates:
  - skill: bicameral-ingest          # skill folder name under skills/
    instruction_pattern: "extract only the keys"
    backing_gate: handlers/ingest.py::_extract_keys_only
    gate_kind: server                # one of: env | config | server | schema
```

Future phases of #205 may extend this with severity, evidence path, last-verified date, etc. Phase 1 ships the minimal shape so the lint has something to match against.

## What the lint enforces

`scripts/lint_skill_governance.py` scans `skills/**/SKILL.md` for instruction patterns that claim a default behavior (`"by default"`, `"redact"`, `"extract only"`, etc.). For each matched claim, it checks `governance-gates.yaml` for a corresponding entry. Findings — claims without a registered backing gate — are reported as advisory in Phase 1.

Phase 4 of #205 will wire the lint into CI as a hard gate. Until then, the lint runs locally + can be invoked manually; reviewers should consult its output on PRs that add new default claims.

## What this doctrine does NOT change

- **Skill text is still required.** Removing all SKILL.md guidance is not the fix; agents need orchestration help to use tools well. The fix is ensuring every claimed default has a backing gate.
- **Existing skills are not retroactively broken.** Phase 3 of #205 (a future cycle) sweeps existing skills against the new lint and files per-skill remediation issues. Phase 1 does not block CI on legacy findings.
- **Suggestive instructions don't go away.** UX improvement guidance, formatting conventions, "how the agent should phrase X" — all suggestive, all still belong in SKILL.md. The doctrine narrows to *privacy / security / compliance defaults*: those need gates.

## References

- `docs/governance/compliance-stance-matrix.md` — the project's stance on every applicable standard.
- `scripts/lint_skill_governance.py` — the static lint.
- `governance-gates.yaml` — the registry the lint reads.
- Issue #205 — the originating doctrine + roadmap to CI enforcement.
- `docs/policies/install-trust-model.md` — example of a related deterministic gate pattern (action SHA-pinning).
