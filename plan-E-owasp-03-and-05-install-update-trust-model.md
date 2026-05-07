# Plan E: install + update trust model declaration (#218 OWASP-03 + OWASP-05)

**change_class**: feature

**doc_tier**: minimal

**high_risk_target**: false

**terms_introduced**:
- term: install_trust_model
  home: docs/policies/install-trust-model.md (new) — operator-readable declaration of what's trusted at install time (uv/pipx authority) and update time (TLS + org-trust on RECOMMENDED_VERSION)

**boundaries**:
- limitations:
  - This PR ships the trust-model DOCUMENTATION, not cosign verification of the RECOMMENDED_VERSION fetch. Cosign-signing the RECOMMENDED_VERSION file content is the future-activation path declared in the doc; the v1 deliverable is the explicit-posture statement (option (b) in the epic body: "document trust-on-first-use posture explicitly"). Activation depends on the sigstore-python verifier wiring that is itself a deferred #218 follow-up (see `release/manifest_verify.py:_sigstore_verify` stub).
  - The "no shipped lockfile" stance is the active install-authority declaration. Operators wanting reproducible installs use `uv tool install bicameral-mcp==<exact-version>` or `pipx install bicameral-mcp==<exact-version>`; uv and pipx each manage their own per-tool venv and lock at install time. This is the supported posture for v1; a hosted deployment tier would change this calculus.
- non_goals:
  - Implementing cosign verification of RECOMMENDED_VERSION at update time (deferred; requires sigstore-python verifier substrate from a separate #218 follow-up).
  - Shipping a `requirements.lock` or `uv.lock` file in the repo. The active stance is "operator-managed install-time resolution"; declaring this stance closes OWASP-03 per the epic body's option ("posture-improving; deployment trigger is hosted").
  - Modifying `handlers/update.py` runtime behavior. The doc declares the current trust model honestly; no code change is in scope.
- exclusions:
  - LLM-06 / #214 (skills/MANIFEST.toml) — separate sub-task; mirrors LLM-11's hooks-manifest signing surface. Larger; will land as its own PR.
  - Other epic #218 sub-tasks already closed (LLM-11 + OWASP-01 from #237; SOC2-03 from #241).

## Open Questions

None at plan time. Both sub-tasks have explicit option-paths in the epic body; the chosen paths are option (b) for OWASP-05 ("document trust-on-first-use posture explicitly") and the equivalent posture-declaration path for OWASP-03 ("floor-only stance declared explicitly with uv.lock / pipx-managed authority documented"). Both reduce to a single trust-model document with two sections.

## Phase 1: Author the install-trust-model doc + cross-references

### Affected Files

- `docs/policies/install-trust-model.md` (new) — two-section operator-readable trust-model doc covering install-time (OWASP-03) and update-time (OWASP-05) supply-chain posture; declares what's trusted now, what would have to change to activate cosign verification on the update path, and what operators with stricter audit requirements should do
- `README.md` — extend the existing "Compliance posture" section with a fourth bullet linking `docs/policies/install-trust-model.md`
- `docs/research-brief-compliance-audit-2026-05-06.md` — mark OWASP-03 and OWASP-05 entries closed by the new doc (matches the bidirectional-cross-reference pattern Plan D established)
- `tests/test_install_trust_model_doc.py` (new) — content-contract tests verifying both load-bearing sections (install-time + update-time) declare the required posture commitments

### Changes

#### `docs/policies/install-trust-model.md`

Two-section structure:

**§ Install-time (OWASP-03)**:

1. **Active stance** — bicameral-mcp ships no `requirements.lock` or `uv.lock` in the repo. The active install-authority is the operator's chosen tool: `uv tool install` (preferred), `pipx install` (fallback), or `pip install` (last-resort). Each manages its own per-tool venv and resolves dependencies at install time.
2. **What this means in practice** — operator chooses the install tool; uv/pipx/pip resolves the dependency tree against the floor constraints in `pyproject.toml`; the resolved venv is locked-at-install-time within that tool's bookkeeping.
3. **What an operator wanting reproducible installs does** — `uv tool install bicameral-mcp==<exact-version>` or `pipx install bicameral-mcp==<exact-version>` pins the wheel version; the resolved transitive dependency tree is then determined by uv/pipx's resolver against PyPI at install time. For org-wide reproducibility, operators may capture the resolved tree via `uv pip freeze` post-install and check it into their own repo as an attestation of the install state.
4. **What would change for hosted deployment** — a hosted tier shipping bicameral-mcp as a service (per `docs/sla.md`'s deferred Hosted-tier section) would pin the resolved tree and ship a `requirements.lock` as the deployment artifact. The active operator-run-only model does not require this.
5. **OWASP A06 cross-reference** — the active model trusts PyPI as the canonical source for floor-constraint resolution; operators concerned about supply-chain compromise of upstream dependencies should consult OWASP A06 (Vulnerable & Outdated Components) and run their own SBOM-based dependency audits. The CycloneDX 1.5 SBOM shipped with each release (per #237 OWASP-01) is the substrate for this.

**§ Update-time (OWASP-05)**:

1. **Active stance** — the `RECOMMENDED_VERSION` file at `https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION` is fetched via plain HTTPS (TLS-only). No cosign signature verification is performed on the fetched content in v1.
2. **What's trusted** — TLS to `raw.githubusercontent.com` provides transport-level integrity (the content was not tampered with in transit, given GitHub's TLS cert chain is trusted); GitHub's organizational access controls on `BicameralAI/bicameral-mcp` provide source-trust (only authorized maintainers can modify `main`). The 1-hour cache at `~/.bicameral/update-check.json` provides limited availability resilience (best-effort fallback to stale cache on transient network failure).
3. **What's NOT trusted (yet)** — content-level signature on RECOMMENDED_VERSION. A compromised maintainer credential or a GitHub-side authorization breach would be undetected at update-fetch time. The active mitigation is operator-side: operators do not auto-apply updates; the agent surfaces the recommended version and asks the user before invoking `bicameral.update apply`.
4. **Future activation** — when the deferred sigstore-python verifier wiring lands (currently stubbed at `release/manifest_verify.py:_sigstore_verify`), the `RECOMMENDED_VERSION` content can be cosign-signed at maintainer commit time and verified in `handlers/update.py:_fetch_recommended_version`. Activation requirements:
   - sigstore-python integration in `release/manifest_verify.py` (replaces the stub)
   - A separate workflow (`.github/workflows/sign-recommended-version.yml`) triggered on push to `main` when RECOMMENDED_VERSION changes; cosign-signs the content and commits/uploads the `.sig` + `.crt` to a stable URL
   - Verifier in `handlers/update.py` fetches `.sig` + `.crt` alongside the version content; refuses the version on signature mismatch (with the same `BICAMERAL_HOOKS_VERIFY_DISABLE`-style bypass posture from #237 LLM-11)
5. **Operator escape hatch (v1)** — operators wanting stricter trust on the update path can:
   - Pin install (`uv tool install bicameral-mcp==<exact>`) and disable auto-updates
   - Manually verify the wheel signature via `cosign verify-blob` against the GitHub Release's wheel signature artifacts (per #237 LLM-11 + `docs/RELEASE_EVIDENCE_PROCEDURE.md`) before applying any recommended update
6. **OWASP A08 cross-reference** — the update path is a software-supply-chain integrity surface (OWASP A08 Software & Data Integrity). The active model's residual risk is documented; the future-activation path is the closure direction.

#### README.md addition

Extend the existing "Compliance posture" section (added by Plan D #240). After the third bullet (`docs/sla.md`), insert:

```markdown
- [`docs/policies/install-trust-model.md`](docs/policies/install-trust-model.md) — install + update supply-chain trust model (closes OWASP-03 + OWASP-05)
```

#### Research brief closures

Find the OWASP-03 and OWASP-05 entries and append closure pointers (matches Plan D pattern):

```markdown
- **Status (2026-05-07)**: Closed by `docs/policies/install-trust-model.md`.
```

### Unit Tests

- `tests/test_install_trust_model_doc.py::test_doc_declares_install_time_section` (new) — opens `docs/policies/install-trust-model.md`; asserts the rendered markdown contains both `## Install-time` (or equivalent OWASP-03 heading) AND substrings declaring the "no shipped lockfile" stance, "uv tool install" / "pipx install" mention, and the "pin install via ==<exact-version>" reproducible-install path.

- `tests/test_install_trust_model_doc.py::test_doc_declares_update_time_section` (new) — asserts the rendered markdown contains the OWASP-05 heading + substrings declaring the TLS-only active stance, the future-activation path (sigstore-python deferred), and the operator escape hatch (pin install / disable auto-updates).

- `tests/test_install_trust_model_doc.py::test_doc_cross_references_related_policies` (new) — asserts the doc cross-references SBOM (OWASP-01 from #237), `docs/RELEASE_EVIDENCE_PROCEDURE.md` (SOC2-03 from #241), and `docs/sla.md` (the operator-run-only deployment model that justifies the install posture).

- `tests/test_install_trust_model_doc.py::test_readme_compliance_section_links_install_trust_model` (new) — opens `README.md`; asserts the existing "Compliance posture" section now lists `docs/policies/install-trust-model.md` alongside the three Plan D-shipped policy docs.

- `tests/test_install_trust_model_doc.py::test_research_brief_marks_owasp_03_and_owasp_05_closed` (new) — opens the research brief; asserts both OWASP-03 and OWASP-05 entries carry "Status (2026-05-07): Closed by `docs/policies/install-trust-model.md`" lines.

Each test invokes the file-read primitive (the unit IS the doc content) and asserts on specific commitments; functional under the doctrine-interpretation locked in Plan D's audit (`<substring> in <file_text>` is presence-only ONLY when the substring is a proxy for a SEPARATE unit; here the doc IS the unit).

## CI Commands

- `python -m pytest tests/test_install_trust_model_doc.py -v` — runs the new content-contract tests
- `python -m pytest -v` — full regression (no code changes; verifies no regression on the doctrine-test gate)
- `ruff check .` + `ruff format --check .` — lint + format gates (no Python file changes; should be no-op)
- Manual smoke: open the rendered markdown in a Markdown previewer; verify the trust-model commitments read coherently and the cross-links resolve
