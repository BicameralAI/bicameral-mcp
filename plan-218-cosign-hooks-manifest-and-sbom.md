# Plan: cosign-keyless signing for hooks-manifest + SBOM emission (#218 Phase 1)

**change_class**: feature

**doc_tier**: standard

**high_risk_target**: false

**terms_introduced**:
- term: hooks_manifest
  home: setup_wizard.py / docs/research-brief-compliance-audit-2026-05-06.md § 2.4
- term: verification_bypassed
  home: ledger event (severity-3) — writer in setup_wizard hook-verify path
- term: verifier_hook
  home: setup_wizard.py module-level extension surface (`_VERIFIER_HOOK`)

**boundaries**:
- limitations:
  - Sigstore Fulcio + Rekor must be reachable at install time for verification to succeed without bypass.
  - `BICAMERAL_HOOKS_VERIFY_DISABLE=1` is the documented escape hatch; ledger-logged but not blocked.
  - Verifier covers Claude hooks (`_install_claude_hooks`) and git hooks (`_install_git_post_commit_hook`, `_install_git_pre_push_hook`) only — surfaces that write shell commands executed at host-fire time.
- non_goals:
  - Air-gap install support (offline keypair fallback is a future #218 sub-task — would land as `verifier_hook` swap).
  - Key rotation tooling (keyless = no long-lived keys; rotation happens at OIDC-identity layer outside this PR).
  - Skills manifest signing (LLM-06 / #214 — separate sub-task).
  - Permission-allowlist signing (out-of-scope; deferred until risk re-eval).
  - SBOM verification at install time (this PR emits the SBOM and Rekor attestation; operator-side `cosign verify-attestation` is documented but not wired into install path).
- exclusions:
  - Other epic #218 sub-tasks (OWASP-03 lockfile, OWASP-05 RECOMMENDED_VERSION signing, SOC2-03 signed-tag procedure, LLM-06 skills manifest).
  - Pre-existing canary / sensitive-data / rate-limit gates in `handlers/ingest.py` (out of this surface area).

## Open Questions

None at plan time. Three design dialogue decisions locked: scope=ii (LLM-11 + OWASP-01), trust-root=α (cosign keyless via Fulcio + Rekor), bypass=B (env override + severity-3 ledger event).

## Phase 1: Build-side — release pipeline emits SBOM + signed hooks-manifest

### Affected Files

- `tests/test_hooks_manifest_generator.py` (new) — functionality tests for manifest derivation, determinism, sha256 computation
- `tests/test_release_artifacts_sbom.py` (new) — functionality tests for CycloneDX-1.5 SBOM shape produced by the build script
- `release/hooks_manifest_generator.py` (new) — derives `hooks-manifest.json` from the source-of-truth in `setup_wizard.py` (Claude hooks + git hook contents); pure-function, deterministic
- `release/sbom_emit.py` (new) — wraps `cyclonedx-bom` invocation; emits `bicameral-mcp-<version>.sbom.json` to `dist/` for cosign-attest
- `.github/workflows/publish.yml` — add SBOM generation, manifest generation, cosign keyless sign, attach signed artifacts to GitHub Release
- `pyproject.toml` — add `cyclonedx-bom` to build-time `[project.optional-dependencies] dev`

### Changes

**`release/hooks_manifest_generator.py`** — new module:
- `generate_manifest(setup_wizard_module) -> dict` — walks the hook-defining functions in `setup_wizard.py` (`_install_claude_hooks`, `_install_git_post_commit_hook`, `_install_git_pre_push_hook`); for each, captures `{event_type, command, sha256_of_command}` triples
- `write_manifest(manifest_dict, output_path: Path) -> None` — writes deterministic JSON (sorted keys, fixed indent)
- Deterministic ordering: hooks sorted by `event_type` lexicographically. SHA-256 over the literal command bytes (no whitespace normalization — exact-match contract).

**`release/sbom_emit.py`** — new module:
- `emit_sbom(wheel_path: Path, output_path: Path) -> Path` — invokes `cyclonedx-bom` against the built wheel via `subprocess.run([...], check=True)` (list-form argv, no `shell=True`); returns output path; raises on non-zero exit
- Validates output has top-level `bomFormat == "CycloneDX"` and `specVersion == "1.5"` before returning (defensive)
- **Subprocess discipline**: all `subprocess` invocations in `release/sbom_emit.py` and `release/hooks_manifest_generator.py` use list-form argv with `shell=False` (the default). CI-pipeline-controlled inputs are still subjected to argv-form discipline as a defense-in-depth commitment per OWASP A03.

**`.github/workflows/publish.yml`** — extend the `build` job:
- After `python -m build`: install `cyclonedx-bom` + `cosign` (via `sigstore/cosign-installer@v3`)
- Run `python -m release.hooks_manifest_generator dist/hooks-manifest.json`
- Run `python -m release.sbom_emit dist/`
- Run `cosign sign-blob --yes dist/hooks-manifest.json --output-signature dist/hooks-manifest.json.sig --output-certificate dist/hooks-manifest.json.crt` (keyless; `id-token: write` permission already present)
- Run `cosign attest-blob --yes --predicate dist/bicameral-mcp-*.sbom.json --type cyclonedx dist/bicameral-mcp-*.whl --output-attestation dist/bicameral-mcp.sbom.intoto.jsonl`
- Add new `release-artifacts` job that uploads the signed artifacts to the GitHub Release (separate from PyPI publish)
- Embed `hooks-manifest.json`, `.sig`, `.crt` into the wheel via `[tool.hatch.build.targets.wheel.shared-data]` so verifiers can find them at install time

### Unit Tests

- `tests/test_hooks_manifest_generator.py::test_generate_manifest_emits_entry_per_hook_function` — invokes `generate_manifest(<stub setup_wizard with two known hooks>)`, asserts the returned dict has exactly two entries with matching `event_type` keys and the expected `command` content
- `tests/test_hooks_manifest_generator.py::test_sha256_matches_command_bytes` — invokes `generate_manifest` with a known command string, asserts the manifest's sha256 entry equals `hashlib.sha256(command.encode()).hexdigest()`
- `tests/test_hooks_manifest_generator.py::test_manifest_serialization_is_deterministic` — invokes `write_manifest` twice with equivalent dicts in different insertion order, asserts byte-identical output (sorted keys)
- `tests/test_hooks_manifest_generator.py::test_manifest_orders_entries_by_event_type` — invokes `generate_manifest` with hooks in non-alphabetical declaration order, asserts the JSON output's hook list is alphabetically ordered
- `tests/test_release_artifacts_sbom.py::test_emit_sbom_returns_path_to_valid_cyclonedx_15_document` — invokes `emit_sbom` against a fixture wheel, parses output JSON, asserts `bomFormat == "CycloneDX"` and `specVersion == "1.5"` and at least one component entry exists
- `tests/test_release_artifacts_sbom.py::test_emit_sbom_raises_on_subprocess_failure` — invokes `emit_sbom` with an unreadable wheel path, asserts `subprocess.CalledProcessError` (or wrapping) is raised; no silent empty-SBOM fallback

## Phase 2: Verify-side — setup_wizard verifies hooks-manifest before writing

### Affected Files

- `tests/test_setup_wizard_hook_verify.py` (new) — functionality tests for the verify path (positive, tampered-sig, command-mismatch, missing-manifest, env-bypass-with-ledger-event)
- `setup_wizard/manifest_verify.py` (new) — sigstore-python verification entry point + per-hook command/sha256 cross-check
- `setup_wizard.py` — wire a new helper `_verify_hooks_or_bypass(...)` into `_install_claude_hooks` (and the two git-hook installers) before any write; honor `BICAMERAL_HOOKS_VERIFY_DISABLE=1`
- `pyproject.toml` — add `sigstore>=3.0` to runtime dependencies
- `docs/SYSTEM_STATE.md` — append a new "Hook Manifest Verification" section documenting: (i) the `BICAMERAL_HOOKS_VERIFY_DISABLE=1` env var, what the bypass costs (loss of supply-chain integrity guarantee for that install), and the audit-trail it leaves (`verification_bypassed` ledger event); (ii) the operator command for offline-verifying SBOM Rekor attestation (`cosign verify-attestation --type cyclonedx --certificate-identity ... <wheel>`)

### Changes

**`setup_wizard/manifest_verify.py`** — new module:
- `class SignatureError(Exception)` — raised on any verify failure (bad sig, missing artifact, command mismatch)
- `verify_hooks_manifest(manifest_path: Path, sig_path: Path, cert_path: Path, expected_hooks: dict) -> None` — calls `sigstore-python` to verify keyless signature against Fulcio cert chain; loads the verified manifest; cross-checks each entry in `expected_hooks` against the manifest by `event_type` + `sha256`; raises `SignatureError` on any mismatch
- `_VERIFIER_HOOK: Callable[[Path, Path, Path], None]` — module-level function pointer, default `_sigstore_verify`; tests can monkeypatch for offline-mode coverage; future #218 sub-task can swap for offline-keypair verifier

**`setup_wizard.py`** — extract the verify+bypass logic into a single helper, invoke from each installer:
- New helper `_verify_hooks_or_bypass(manifest_path: Path, sig_path: Path, cert_path: Path, expected_hooks: dict) -> None`:
  - Calls `manifest_verify.verify_hooks_manifest(...)`
  - On `SignatureError`: checks `os.environ.get("BICAMERAL_HOOKS_VERIFY_DISABLE") == "1"`. If yes: writes a `verification_bypassed` ledger event (severity-3, fields `{ts, manifest_path, reason, manifest_sha256}`) via `EventFileWriter.write("verification_bypassed", payload)` and returns. If no: re-raises.
- Each installer (`_install_claude_hooks`, `_install_git_post_commit_hook`, `_install_git_pre_push_hook`) gets exactly 3 new LOC: build the `expected_hooks` dict mirroring what the installer intends to write, then `_verify_hooks_or_bypass(...)`, then proceed with the existing write logic.
- **Razor commitment**: the modification adds at most 3 LOC to each installer call site. `_install_claude_hooks` is currently ~121 LOC (pre-existing overage from before this PR); reducing that overage is out of scope and is explicitly handed off to `/qor-refactor` as a separate workstream.

**Ledger event contract**: the `verification_bypassed` event is written via `EventFileWriter.write(event_type='verification_bypassed', payload={...})`. The `event_type` field on `EventEnvelope` (`events/writer.py:76`) is free-form `str` — no schema modification required, no edit to `events/writer.py` needed.

### Unit Tests

- `tests/test_setup_wizard_hook_verify.py::test_verify_hooks_manifest_returns_none_for_valid_signed_manifest` — invokes `verify_hooks_manifest` with a fixture-signed manifest + matching expected hooks (monkeypatched `_VERIFIER_HOOK` returning success), asserts no exception raised
- `tests/test_setup_wizard_hook_verify.py::test_verify_hooks_manifest_raises_signature_error_when_sig_invalid` — invokes `verify_hooks_manifest` with monkeypatched `_VERIFIER_HOOK` raising sigstore-equivalent error, asserts `SignatureError` propagates
- `tests/test_setup_wizard_hook_verify.py::test_verify_hooks_manifest_raises_when_command_sha256_mismatches` — invokes `verify_hooks_manifest` with valid signature but `expected_hooks` containing a sha256 different from manifest's, asserts `SignatureError`
- `tests/test_setup_wizard_hook_verify.py::test_verify_hooks_manifest_raises_when_manifest_file_missing` — invokes with non-existent `manifest_path`, asserts `SignatureError`
- `tests/test_setup_wizard_hook_verify.py::test_install_claude_hooks_proceeds_with_bypass_event_when_env_var_set` — monkeypatches `_VERIFIER_HOOK` to raise; sets `BICAMERAL_HOOKS_VERIFY_DISABLE=1` via monkeypatch; invokes `_install_claude_hooks(repo_path)`; asserts (a) the function returns success, (b) `events.writer` received a `verification_bypassed` event with severity-3 + a `manifest_sha256` field
- `tests/test_setup_wizard_hook_verify.py::test_install_claude_hooks_raises_signature_error_when_env_var_unset` — same setup as above without the env var; asserts `SignatureError` propagates and no hook file was written (verify with a `tmp_path` fixture and `(tmp_path / ".claude" / "settings.json").exists() == False`)
- `tests/test_setup_wizard_hook_verify.py::test_verifier_hook_is_swappable_at_module_level` — assigns a sentinel function to `manifest_verify._VERIFIER_HOOK`; invokes `verify_hooks_manifest`; asserts the sentinel was called (the function-pointer extension surface contract)

## CI Commands

- `python -m pytest tests/test_hooks_manifest_generator.py tests/test_release_artifacts_sbom.py tests/test_setup_wizard_hook_verify.py -v` — runs the new functionality tests
- `python -m pytest -v` — full regression (138+ ingest tests, 24+ context tests, plus new 14 above)
- `ruff check .` — lint gate
- `ruff format --check .` — format gate
- `mypy setup_wizard.py setup_wizard/ release/` — type gate on the new modules
- `python -m release.hooks_manifest_generator /tmp/test-manifest.json && cat /tmp/test-manifest.json | python -m json.tool` — local smoke test for manifest generator deterministic output
- `python -m release.sbom_emit dist/` (after `python -m build`) — local smoke test for SBOM generator
