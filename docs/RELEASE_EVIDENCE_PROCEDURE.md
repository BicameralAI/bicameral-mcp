# Per-release evidence procedure (#218 SOC2-03)

**Status**: active
**Closes gap**: SOC2-03 per `docs/research-brief-compliance-audit-2026-05-06.md`
**Doctrine**: SOC 2 Type II Trust Service Criterion CC8.1 (change management)

This document is the operator-facing workflow for collecting and archiving per-release evidence. Run it for every published GitHub Release.

## Pre-release checklist

Before creating a new release tag:

- [ ] All PRs merged into the release branch (`dev` for sub-releases; `main` for stable releases) have at least one approving review
- [ ] All CI checks were green at merge time for every PR in the window (no checks-skipped merges)
- [ ] No force-pushes to the protected branch (`main`) since the previous release tag
- [ ] CHANGELOG / release notes draft is ready
- [ ] Open security blockers reviewed in `docs/BACKLOG.md`

## Release-tag creation

```bash
# 1. Tag locally with annotation
git tag -a v<VERSION> -m "Release v<VERSION>"

# 2. Push tag
git push origin v<VERSION>

# 3. Create GitHub Release (triggers publish workflow)
gh release create v<VERSION> --generate-notes
```

The publish workflow (`.github/workflows/publish.yml`) automatically:

- Generates `hooks-manifest.json` + `skills-manifest.toml` into `share/bicameral-mcp/`
- Cosign keyless-signs both manifests with `cosign sign-blob --new-bundle-format --bundle`, emitting single-file `.sigstore` bundles (LLM-11, LLM-06 / #218; bundle format / #292)
- Builds the wheel — the manifest build hook detects the pre-signed `.sigstore` bundles and skips regeneration, and hatch `shared-data` bundles each manifest + its `.sigstore` into the wheel (#292)
- Generates the CycloneDX 1.5 SBOM and Rekor attestation (OWASP-01 / #218)
- Cosign keyless-signs the release tag's commit SHA (SOC2-03 / #218 — this document's surface)
- Attaches all signed artifacts to the GitHub Release
- Publishes the wheel + sdist to PyPI

> **#292 ordering invariant**: manifest generation + `cosign sign-blob` run **before** `python -m build`. The build hook is idempotent — a pre-signed `.sigstore` makes it skip regeneration so the signed bytes survive into the wheel. `tests/test_publish_workflow_lint.py` enforces this ordering.

## Post-release verification

After the publish workflow completes:

- [ ] Confirm the workflow succeeded in GitHub Actions
- [ ] Confirm the GitHub Release page lists all expected signed artifacts:
  - `bicameral_mcp-<version>-py3-none-any.whl` (the wheel)
  - `hooks-manifest.json` + `hooks-manifest.json.sigstore`
  - `skills-manifest.toml` + `skills-manifest.toml.sigstore`
  - `bicameral-mcp.sbom.json` + `bicameral-mcp.sbom.intoto.jsonl`
  - `release-tag-commit.txt` + `release-tag-commit.txt.sig` + `release-tag-commit.txt.crt`
- [ ] Confirm the wheel + sdist are visible on https://pypi.org/project/bicameral-mcp/

## Evidence collection

Run the helper script to assemble the per-release evidence scaffold:

```bash
python -m release.evidence_collect \
  --from-tag v<PREVIOUS> \
  --to-tag v<CURRENT> \
  --output dist/release-evidence-v<CURRENT>.md
```

Requires the `gh` CLI to be authenticated against `BicameralAI/bicameral-mcp`.

The script:

- Lists merged PRs in the tag window via `gh pr list --state merged --search "merged:>=<PREV> merged:<=<CURRENT>"`
- Lists CI runs against `main` in the same window via `gh run list`
- For each PR, fetches reviewer attribution via `gh pr view <PR> --json reviews`
- Renders a markdown scaffold with three sections (Merged PRs, CI runs, Reviewer attribution) and an Operator narrative section to fill in

Empty sections produce explicit "_No PRs merged between these tags_" / "_No CI runs recorded in window_" notes — never silent omission.

## Operator narrative (fill in)

After the scaffold is generated, fill in the **Operator narrative** section with:

- **Rationale** for any exceptions (PRs merged without an approving review, CI checks skipped, force-pushes since previous release)
- **Deviations from policy** (any pre-release checklist item that wasn't satisfied)
- **Closed-issue traceability** (which `#<issue>` items this release closes; which deferred follow-ups remain open)
- **Attestation statement** — operator's signed statement, e.g.:

  > I, <operator name>, attest that all PRs merged between v<PREV> and v<CURRENT> had at least one approving review, all CI checks were green at merge time, no force-pushes occurred to `main` in this window, and no policy exceptions are undisclosed in this evidence file. Signed: <date> <operator>

## Retention policy

Evidence files MUST be retained for the duration of the SOC 2 audit window, typically **at least 7 years**. Storage is operator-chosen:

- In-repo `docs/release-evidence/v<version>.md` (committed alongside the release tag) — simplest, lowest-friction
- Separate evidence repository (private GitHub repo, Drive folder, document-management system) — for organizations with formal evidence-collection workflows
- Both — recommended for orgs running formal SOC 2 audits

This document does NOT mandate a specific storage location; the operator's compliance team chooses based on organizational policy.

## Verification commands (for auditors)

These commands verify that signed artifacts are bound to the published release. Run from any machine with `cosign` installed; no special access required (cosign keyless verification reads from the public Sigstore Fulcio + Rekor log).

### Verify the tag-commit signature

```bash
cosign verify-blob \
  --certificate-identity-regexp "^https://github.com/BicameralAI/bicameral-mcp/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --signature release-tag-commit.txt.sig \
  --certificate release-tag-commit.txt.crt \
  release-tag-commit.txt
```

The `release-tag-commit.txt` file contains the commit SHA the release tag pointed at. Successful verification proves the GitHub Actions workflow signed this exact commit SHA at release-publish time.

### Verify the hooks-manifest signature

The manifests are signed as single-file Sigstore `.sigstore` bundles
(#292). Verify with the `--bundle` form:

```bash
cosign verify-blob \
  --new-bundle-format \
  --certificate-identity-regexp "^https://github.com/BicameralAI/bicameral-mcp/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --bundle hooks-manifest.json.sigstore \
  hooks-manifest.json
```

The same command verifies `skills-manifest.toml` against
`skills-manifest.toml.sigstore`.

### Verify the SBOM Rekor attestation

```bash
cosign verify-attestation \
  --type cyclonedx \
  --certificate-identity-regexp "^https://github.com/BicameralAI/bicameral-mcp/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  bicameral_mcp-<version>-py3-none-any.whl
```

The `bicameral-mcp.sbom.json` is the SBOM document; the `.intoto.jsonl` is the Rekor in-toto attestation that binds the SBOM to the wheel.

## Verify install-time signature (#292)

Since #292, the wheel ships each manifest **and** its `.sigstore` bundle
(`hooks-manifest.json.sigstore`, `skills-manifest.toml.sigstore`) via
hatch `shared-data`. At install time `bicameral-mcp setup` re-engages
signature verification end-to-end.

### What `bicameral-mcp setup` does at the signature layer

Before writing any host hook config or copying any skill file, the wizard:

1. Calls `setup_wizard._bundled_manifest_paths()` /
   `_bundled_skills_manifest_paths()`, which locate the manifest + its
   `.sigstore` bundle under `<sys.prefix>/share/bicameral-mcp/`. A
   **zero-byte** `.sigstore` (the local-dev placeholder a non-release
   build emits) is treated as absent → the helper returns `None` and
   verification defers.
2. When a real (non-empty) bundle is present, `release.manifest_verify` /
   `release.skills_verify` load it with `sigstore.models.Bundle.from_json`
   and verify the manifest via
   `sigstore.verify.Verifier.production().verify_artifact()` under a
   composite identity policy
   (`GitHubWorkflowRepository("BicameralAI/bicameral-mcp")` **and**
   `GitHubWorkflowName("Publish to PyPI")`).
3. The verified manifest's per-entry SHA-256 values are cross-checked
   against the exact bytes the installer is about to write/copy. Any
   mismatch → `SignatureError` and the install aborts (fail-closed).

### Confirm the verifier actually ran (no bypass)

A clean install emits **no** `verification_bypassed` event. To confirm:

```bash
# After `bicameral-mcp setup`, inspect the ledger event stream for the repo.
# A severity-3 verification_bypassed event means the signature check was
# skipped via the emergency bypass env var — investigate before trusting
# the install.
grep -r '"event_type": "verification_bypassed"' ~/.bicameral/ 2>/dev/null || \
  echo "no verification_bypassed events — verifier ran clean"
```

### Emergency bypass

If verification fails on a known-good release (e.g. a transient Sigstore
outage) the install can be forced through with:

- `BICAMERAL_HOOKS_VERIFY_DISABLE=1` — bypass the hooks-manifest verifier
- `BICAMERAL_SKILLS_VERIFY_DISABLE=1` — bypass the skills-manifest verifier

Each bypass swallows the `SignatureError` **and writes a severity-3
`verification_bypassed` ledger event** (with `manifest_kind: "skills"`
for the skills surface) so the bypass is auditable. Use only when the
failure is understood; the bypass is fail-open by design.

### Manually verify the wheel's bundled `.sigstore`

To verify a wheel's bundled bundle directly, extract it from the wheel
(`shared-data` lands under `<dist>.data/data/share/bicameral-mcp/`) and
run `cosign verify-blob --new-bundle-format --bundle ...` as shown in the
"Verify the hooks-manifest signature" section above.

### Release-time acceptance step (v0.14.5+)

Because keyless cosign signing requires a GitHub Actions OIDC token, the
end-to-end install-time path cannot be exercised before a real signed
release exists. For the first release that ships #292, record this
manual acceptance step in the release evidence:

- [ ] A fresh `pipx install bicameral-mcp==<version>` followed by
  `bicameral-mcp setup` completes **without** a `SignatureError` and
  **without** a `verification_bypassed` event.

## Cross-references

- `.github/workflows/publish.yml` — the release pipeline that emits all signed artifacts
- `release/evidence_collect.py` — the evidence-collection helper script
- `docs/policies/host-trust-model.md` — server-side guarantees enforced regardless of host
- `docs/policies/acceptable-use.md` — intended purpose + prohibited uses
- `docs/sla.md` — availability stance (operator-run-only)
- `docs/research-brief-compliance-audit-2026-05-06.md` § 2.2 SOC2-03
- Doctrine: #205 (deterministic-governance hard rule)
