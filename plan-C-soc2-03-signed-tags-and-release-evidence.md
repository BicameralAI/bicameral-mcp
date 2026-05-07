# Plan C: SOC2-03 — cosign-signed release tags + per-release evidence procedure (#218 sub-task)

**change_class**: feature

**doc_tier**: minimal

**high_risk_target**: false

**terms_introduced**:
- term: release_evidence
  home: docs/RELEASE_EVIDENCE_PROCEDURE.md (new) — operator-readable per-release evidence-collection workflow

**boundaries**:
- limitations:
  - Tag signing uses cosign keyless (Sigstore Fulcio + Rekor) tied to the BicameralAI/bicameral-mcp GitHub OIDC identity. NOT GPG-keypair-based — keeps the trust root consistent with #237's keyless wheel/manifest signing. Operators with GPG-only audit requirements will need follow-up work to add a parallel GPG path.
  - Evidence collection is scripted into a markdown scaffold (`release/evidence_collect.py`); operators must run the script manually post-release and fill in narrative sections (rationale, exceptions, deviations). Fully automatic SOC2 evidence emission would require deeper CI/PR-history integration (out of scope here).
  - The "evidence" produced satisfies the SOC2 Type II Trust Service Criterion CC8.1 (change management) at the per-release granularity. Per-PR or per-deploy granularity not in scope.
- non_goals:
  - GPG-signed tags (`git tag -s`) — would require maintainer-side key infra inconsistent with the cosign keyless story.
  - Full SOC2 evidence automation (no human-in-loop). The procedure deliberately requires the operator to attest exceptions and deviations.
  - SBOM/wheel/manifest signing (already shipped in #237).
  - Per-PR audit-log emission (operator-side audit-log story for individual PR merges is a separate concern).
- exclusions:
  - Other epic #218 sub-tasks (OWASP-03 lockfile, OWASP-05 RECOMMENDED_VERSION, LLM-06 skills manifest).
  - Modifying the existing #237 build job — this plan ADDS a sibling step in the same workflow.

## Open Questions

None at plan time. Trust root + scope locked: cosign keyless via Sigstore Fulcio (matches #237); two new deliverables (tag-SHA signature + evidence procedure doc + helper script) attached to GitHub Release.

## Phase 1: Sign the release tag's commit SHA + author evidence procedure

### Affected Files

- `tests/test_release_evidence_collect.py` (new) — functionality tests for `release.evidence_collect.collect_evidence(...)`: gh-CLI subprocess shape, markdown rendering of PR list + CI runs + reviewers
- `release/evidence_collect.py` (new) — small CLI that runs `gh pr list --state merged --base main --search 'merged:<from-tag>..<to-tag>'`, `gh run list`, `gh pr view --json reviews`; assembles a markdown evidence scaffold; emits to stdout or `--output <path>`
- `docs/RELEASE_EVIDENCE_PROCEDURE.md` (new) — operator-readable per-release evidence procedure: pre-release checklist, post-release evidence-collection steps, attestation template, retention policy
- `.github/workflows/publish.yml` — extend the existing `build` job (#237's cosign-installed pipeline) with one additional step: cosign sign-blob the release tag's commit SHA; attach the `.sig` and `.crt` to the GitHub Release alongside the existing artifacts

### Changes

#### Tag commit-SHA signing (cosign keyless)

Append to the publish workflow's `build` job, AFTER the existing manifest/SBOM signing steps and BEFORE the `Attach signed artifacts to GitHub Release` step:

```yaml
      - name: Cosign keyless sign release tag commit
        run: |
          # Capture the commit SHA the release tag points at.
          TAG_COMMIT=$(git rev-list -n 1 "${{ github.event.release.tag_name }}")
          echo "$TAG_COMMIT" > dist/release-tag-commit.txt
          cosign sign-blob --yes \
            --output-signature dist/release-tag-commit.txt.sig \
            --output-certificate dist/release-tag-commit.txt.crt \
            dist/release-tag-commit.txt
```

Then add `dist/release-tag-commit.txt`, `.sig`, and `.crt` to the existing `gh release upload` line. The verifier (operator-side, manual) runs:

```bash
cosign verify-blob \
  --certificate-identity-regexp "^https://github.com/BicameralAI/bicameral-mcp/" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --signature release-tag-commit.txt.sig \
  --certificate release-tag-commit.txt.crt \
  release-tag-commit.txt
```

That command is documented in `docs/RELEASE_EVIDENCE_PROCEDURE.md`.

#### Evidence-collection helper

`release/evidence_collect.py` — single-file CLI:

```python
def collect_evidence(from_tag: str, to_tag: str) -> str:
    """Run gh-CLI subprocess calls to gather per-release evidence; return
    a markdown report. Pure: no I/O writes; caller writes to disk.

    Subprocess discipline: list-form argv, shell=False (OWASP A03).
    """
    prs = _gh_pr_list_merged_between(from_tag, to_tag)
    ci_runs = _gh_run_list_in_window(from_tag, to_tag)
    reviews = [_gh_pr_view_reviews(pr["number"]) for pr in prs]
    return _render_markdown(prs, ci_runs, reviews, from_tag, to_tag)
```

The helper functions invoke `subprocess.run(["gh", ...], check=True, capture_output=True)` and parse the JSON. Each is <20 LOC. The renderer assembles the markdown sections (PR table, CI table, reviewer list) and includes pointers to the cosign-signed wheel + SBOM + tag-commit signature attached to the GitHub Release.

CLI invocation:

```bash
python -m release.evidence_collect \
  --from-tag v0.13.7 \
  --to-tag v0.13.8 \
  --output dist/release-evidence-v0.13.8.md
```

Output is markdown; operator reviews, fills narrative sections, archives to `docs/release-evidence/v<version>.md` (folder created on first use; not committed by default — operators may publish to a SOC2 evidence repository instead).

#### Per-release evidence procedure doc

`docs/RELEASE_EVIDENCE_PROCEDURE.md` — operator-readable workflow:

1. **Pre-release checklist** — verify CI green on all merged PRs; verify all PRs in window have ≥1 approving review; verify no force-push to main since previous release tag.
2. **Release-tag creation** — `git tag -a v<version> -m "..."; git push --tags; gh release create v<version> --generate-notes`.
3. **Post-release verification** — confirm publish workflow ran successfully; confirm signed artifacts attached to GitHub Release (wheel signature, hooks-manifest signature, SBOM attestation, tag-commit signature).
4. **Evidence collection** — run `python -m release.evidence_collect --from-tag <prev> --to-tag <new> --output dist/release-evidence-v<new>.md`; review the scaffold; fill in narrative sections (rationale for any exceptions, deviations from policy, closed-issue traceability).
5. **Attestation template** — operator's signed statement (text template) attesting that all PRs in window had ≥1 approving review, no force-pushes to main, all CI checks green at merge, no policy exceptions undisclosed.
6. **Retention policy** — evidence files retained ≥7 years per typical SOC2 audit window. Storage location is operator-chosen (in-repo `docs/release-evidence/`, separate evidence repo, or document-management system).
7. **Verification commands** — concrete `cosign verify-blob` commands for each artifact type, for auditor-side independent verification.

### Unit Tests

- `tests/test_release_evidence_collect.py::test_collect_evidence_renders_markdown_with_pr_table` — invokes `collect_evidence(from_tag="v0.13.7", to_tag="v0.13.8")` with `subprocess.run` mocked to return a fixture JSON of merged PRs; asserts the returned markdown contains a table row per PR with title, number, and merge timestamp

- `tests/test_release_evidence_collect.py::test_collect_evidence_renders_markdown_with_ci_runs` — invokes with `gh run list` mocked; asserts the returned markdown contains a table row per CI run with workflow name, conclusion, and run URL

- `tests/test_release_evidence_collect.py::test_collect_evidence_renders_markdown_with_reviewer_attribution` — invokes with `gh pr view` per-PR mocked to return reviewer JSON; asserts the rendered markdown carries a reviewer-attribution section listing PRs that had ≥1 approving review

- `tests/test_release_evidence_collect.py::test_collect_evidence_uses_list_form_argv_with_no_shell_true` — invokes the helper with a stub that captures the `subprocess.run` arguments; asserts every call is list-form argv and `shell` is False or absent (OWASP A03 commitment)

- `tests/test_release_evidence_collect.py::test_collect_evidence_raises_on_subprocess_failure` — invokes with `subprocess.run` raising `CalledProcessError`; asserts the helper propagates the exception (no silent empty-evidence fallback)

- `tests/test_release_evidence_collect.py::test_render_markdown_omits_empty_sections_with_explicit_note` — invokes the renderer directly with empty PR list / empty CI list; asserts the rendered markdown carries explicit "No PRs in window" / "No CI runs in window" notes (not silent omission, which would be misleading evidence)

## CI Commands

- `python -m pytest tests/test_release_evidence_collect.py -v` — runs the new tests
- `python -m pytest -v` — full regression
- `ruff check .` + `ruff format --check .` — lint + format gates
- `mypy release/evidence_collect.py` — type gate on the new module
- `python -m release.evidence_collect --from-tag v0.13.7 --to-tag v0.13.8 --output /tmp/test-evidence.md` — local smoke test (requires `gh` CLI authenticated)
