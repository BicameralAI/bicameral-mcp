# Install + update trust model

**Status**: active
**Closes gaps**: OWASP-03 + OWASP-05 per `docs/research-brief-compliance-audit-2026-05-06.md` § 2.3
**Doctrine**: #205 deterministic-governance hard rule

This document declares bicameral-mcp's supply-chain trust model in two parts: what's trusted at **install time** (OWASP-03) and what's trusted at **update time** (OWASP-05). Together they describe the active stance, the residual risks, and the future-activation paths for stricter signing.

## Install-time trust model (OWASP-03)

### Active stance

bicameral-mcp ships **no `requirements.lock` or `uv.lock`** in the repository. The active install-authority is the operator's chosen tool:

1. `uv tool install bicameral-mcp` (preferred; matches the README quickstart)
2. `pipx install bicameral-mcp` (fallback)
3. `pip install bicameral-mcp` (last-resort, e.g. dev venv)

Each tool manages its own per-tool venv and resolves the dependency tree at install time against the floor constraints declared in `pyproject.toml`. The resolved venv is locked-at-install-time within that tool's bookkeeping, not in this repo.

### What this means in practice

- **Install authority**: uv / pipx / pip is the canonical authority for the resolved tree on the operator's machine. The repo declares floor constraints; the install tool resolves transitively against PyPI.
- **No reproducible-by-default install**: two operators running `uv tool install bicameral-mcp` weeks apart may receive different resolved transitive trees because PyPI evolves and uv's resolver picks the highest compatible version.
- **No central lockfile to drift against**: there is no shipped `requirements.lock` to keep current with `pyproject.toml` floors, so no maintenance burden of lockfile-vs-spec drift.

### What an operator wanting reproducible installs does

For pinned, reproducible installs:

```bash
uv tool install bicameral-mcp==0.13.8
# or
pipx install bicameral-mcp==0.13.8
```

Pinning the wheel version is the first step. The transitive tree is still uv/pipx-resolver-determined at install time. For org-wide reproducibility, operators may capture the resolved tree post-install via `uv pip freeze` and check it into their own infrastructure repo as an attestation of the install state.

### What would change for a hosted deployment

A future hosted tier (per `docs/sla.md`'s deferred Hosted-tier section) shipping bicameral-mcp as a service would pin the resolved tree and ship a `requirements.lock` as the deployment artifact. The active **operator-run-only** model (per `docs/sla.md`) does not require this. If `bicameral.cloud` ever ships, this section gets a parallel "Hosted tier (active)" subsection with the locked dependency tree.

### OWASP A06 cross-reference

The active model trusts PyPI as the canonical source for floor-constraint resolution. Operators concerned about supply-chain compromise of upstream dependencies should consult OWASP A06 (Vulnerable & Outdated Components) and run their own SBOM-based dependency audits. The CycloneDX 1.5 SBOM shipped with each release (per #218 OWASP-01) is the substrate for this audit.

## Update-time trust model (OWASP-05)

### Active stance

The `bicameral.update` tool fetches the recommended version from:

```
https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION
```

via plain HTTPS (TLS-only). **No cosign signature verification is performed on the fetched content in v1.** The fetched version string is cached at `~/.bicameral/update-check.json` with a 1-hour TTL.

### What's trusted

- **TLS to `raw.githubusercontent.com`**: provides transport-level integrity. The content was not tampered with in transit, given GitHub's TLS cert chain is trusted by the operator's system trust store.
- **GitHub's organizational access controls** on `BicameralAI/bicameral-mcp`: only authorized maintainers can modify `main`. The branch-protection rules + maintainer credential controls are the active source-trust mechanism.
- **1-hour cache + best-effort fallback to stale cache** on transient network failure: limited availability resilience.

### What's NOT trusted (yet)

**Content-level signature on `RECOMMENDED_VERSION`.** A compromised maintainer credential or a GitHub-side authorization breach would be undetected at update-fetch time. The active mitigation is operator-side: operators do **not** auto-apply updates. The agent surfaces the recommended version and asks the user before invoking `bicameral.update apply`. The user makes the final call.

### Operator escape hatch (v1)

Operators wanting stricter trust on the update path can:

- **Pin install** (`uv tool install bicameral-mcp==<exact-version>`) and decline auto-updates entirely.
- **Manually verify the wheel signature** via `cosign verify-blob` against the GitHub Release's wheel signature artifacts (per #218 LLM-11) before applying any recommended update. The verification command is documented in `docs/RELEASE_EVIDENCE_PROCEDURE.md`.

### Future activation

When the deferred sigstore-python verifier wiring lands (currently stubbed at `release/manifest_verify.py:_sigstore_verify` with explicit "deferred follow-up" framing), the `RECOMMENDED_VERSION` content can be cosign-signed at maintainer commit time and verified in `handlers/update.py:_fetch_recommended_version`. Activation requirements:

1. **sigstore-python integration** in `release/manifest_verify.py` — replaces the current stub with a real `Verifier.production()` call against `sigstore.models.Bundle`. This is itself a deferred #218 follow-up.
2. **A separate signing workflow** (`.github/workflows/sign-recommended-version.yml`) triggered on push to `main` when `RECOMMENDED_VERSION` changes. Cosigns the content; commits/uploads `.sig` + `.crt` to a stable URL accessible at update time.
3. **Verifier in `handlers/update.py`** — fetches `.sig` + `.crt` alongside the version content; refuses the version on signature mismatch (with the same `BICAMERAL_HOOKS_VERIFY_DISABLE`-style bypass posture from #218 LLM-11).

When all three land, this section flips to "Active" and the current "Active stance" downgrades to "Legacy stance (pre-signing)".

### OWASP A08 cross-reference

The update path is a software-supply-chain integrity surface (OWASP A08 Software & Data Integrity). The active model's residual risk is documented above; the future-activation path is the closure direction. Until that lands, the operator-side gating (no auto-apply; explicit user confirmation per update) is the active mitigation.

## Cross-references

- `docs/policies/host-trust-model.md` — server-side guarantees + host-side surfaces (MCP-01)
- `docs/policies/acceptable-use.md` — intended purpose + prohibited uses (NIST AI RMF + EU AI Act)
- `docs/sla.md` — availability stance (operator-run-only) — drives the install-time deployment model
- `docs/RELEASE_EVIDENCE_PROCEDURE.md` — per-release evidence procedure (SOC2-03) — operator-readable verification commands for signed artifacts
- `release/manifest_verify.py:_sigstore_verify` — current sigstore stub; future-activation hook for OWASP-05 closure
- `handlers/update.py:_fetch_recommended_version` — current TLS-only fetch
- Research brief: § 2.3 OWASP-03 + OWASP-05; § 5 deployment-trigger column
- Doctrine: #205 (deterministic-governance hard rule)
