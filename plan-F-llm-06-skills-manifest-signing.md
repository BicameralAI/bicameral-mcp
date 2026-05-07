# Plan F: LLM-06 / #214 — signed skills/MANIFEST.toml (#218 final sub-task)

**change_class**: feature

**doc_tier**: standard

**high_risk_target**: false

**terms_introduced**:
- term: skills_manifest
  home: release/skills_manifest_generator.py (new) + skills/MANIFEST.toml (generated at wheel-build time)
- term: skills_verify_disable
  home: BICAMERAL_SKILLS_VERIFY_DISABLE env var (mirrors BICAMERAL_HOOKS_VERIFY_DISABLE from #218 LLM-11)

**boundaries**:
- limitations:
  - Mirror of #218 LLM-11 (hooks-manifest) for the skills-content surface. Same trust root (cosign keyless via Sigstore Fulcio), same build-hook generation pattern, same install-time verification + bypass posture.
  - **Cosign verification activation timing inheritance from #237 LLM-11** (round-1 audit substrate observation 2): full cosign keyless verification depends on the same deferred sigstore-python wiring stub at `release/manifest_verify.py:_sigstore_verify`. When that stub is replaced with a real `Verifier.production()` call against `sigstore.models.Bundle`, BOTH LLM-11 (hooks) and LLM-06 (skills) verification activate together. The deferred sigstore-python wiring is itself a separate #218 follow-up.
  - Per-file SHA-256 covers `SKILL.md` files in each `skills/<skill-name>/` directory. Adjacent `*.yaml` files (skill metadata) get the same coverage if present.
  - **LLM-06 framing per the research brief** (round-1 audit substrate observation 1): the brief at line 416 frames LLM-06 as "design constraint, not runtime defect" because wheel-bundled skills are already covered by SOC2-03 (signed releases) + OWASP-01 (SBOM). #214's title "scope-narrowed P1 — gates future remote-skill-loading" frames the substrate as enabling a future scope. Plan F's deliverable removes the LLM-06 design-constraint gate, enabling future remote-skill-loading to ship without re-litigating the signing question. Secondary benefit: per-file SHA-256 verification ALSO catches post-install in-place tampering of the installed package directory (a runtime defense beyond what wheel signing provides). PR description should make this framing explicit.
- non_goals:
  - Implementing the sigstore-python verifier (deferred follow-up; same stub pattern as #237 LLM-11).
  - Wheel-level signing (option (b) in #214 issue body — issue explicitly prefers (a) per-file hash manifest because it catches in-place modification of installed package directories, not just upload-time tampering).
  - Cross-host skill-channel verification (skills shipping from sources OTHER than the wheel — e.g., remote-skill-loading marketplace — is the future scope flagged in epic #218's LLM-06 entry; Plan F covers wheel-bundled skills only).
- exclusions:
  - Other epic #218 sub-tasks: all closed (LLM-11 + OWASP-01 + SOC2-03 + OWASP-03 + OWASP-05 from #237/#241/#248).
  - Modifying `setup_wizard.py`'s skill installation logic beyond the verify-call insertion point. The existing copy semantics are preserved.

**Implementer notes** (substrate observations from round-1 audit):
- **TOML emitter approach**: plan recommends manual emission to avoid `tomli_w` dependency. Acceptance bound: if manual emitter exceeds ~30 LOC at implement time, swap to `tomli_w` (the dependency is small and YAGNI-favors avoiding overengineered string handling for TOML escapes). Implementer judgment call.

## Open Questions

None at plan time. #214 issue body locks path (a) (per-file hash manifest, cosign-signed). Trust root + verifier substrate inherited from #237 LLM-11.

## Phase 1: Build-side — generate + bundle + sign skills manifest

### Affected Files

- `tests/test_skills_manifest_generator.py` (new) — functionality tests for the manifest generator: walk skills/, compute per-file SHA-256, emit deterministic TOML
- `release/skills_manifest_generator.py` (new) — pure-function deterministic TOML manifest writer; SHA-256 over each `SKILL.md` + adjacent `*.yaml` bytes; sorted-key serialization
- `release/skills_source.py` (new) — single source of truth: walks `skills/` at build time and yields per-skill `(skill_name, files)` tuples for the generator to consume
- `scripts/hooks_manifest_build_hook.py` — **modified** (not new file): add `class SkillsManifestBuildHook(BuildHookInterface)` alongside the existing `HooksManifestBuildHook`. Hatch's `[tool.hatch.build.targets.wheel.hooks.custom]` registers ONE plugin module path; both `BuildHookInterface` subclasses defined in that module are auto-discovered and fire at wheel-build time. (Round-1 audit finding 1: the original Plan F's separate `scripts/skills_manifest_build_hook.py` file paired with a `[[tool.hatch.build.targets.wheel.hooks.custom]]` array-of-tables addition was invalid TOML AND a hatch plugin model mismatch. Fixed via Path A — collapse into the existing module.)
- `pyproject.toml` — augment `[tool.hatch.build.targets.wheel.shared-data]` with one new entry mapping `share/bicameral-mcp/skills-manifest.toml` (table-augmentation, not table-redefinition). The existing single-table `[tool.hatch.build.targets.wheel.hooks.custom]` registration is unchanged — auto-discovery picks up the new `SkillsManifestBuildHook` class added to the registered module.
- `.github/workflows/publish.yml` — add cosign keyless sign-blob step for `skills/MANIFEST.toml`; attach `.sig` + `.crt` to GitHub Release; update PyPI strip step

### Changes

#### `release/skills_manifest_generator.py`

Pure-function TOML emitter. Input: list of `(skill_name, file_path, file_bytes)` tuples. Output: deterministic TOML dict with the shape:

```toml
manifest_version = 1

[skills.bicameral-ingest]
"SKILL.md" = "<sha256-hex>"

[skills.bicameral-preflight]
"SKILL.md" = "<sha256-hex>"
"agent-instructions.yaml" = "<sha256-hex>"
```

Each skill is a sorted section; each file within is a sorted key. Manifest version `1` is a forward-extensibility marker; future schema changes bump the version.

Stdlib-only implementation: `tomllib` (Python 3.11+) for parsing in tests; `tomli_w` if available else manual emission for write. Manual emission is preferred to avoid the external `tomli_w` dependency — TOML output is structurally simple (sorted dict-of-dict-of-strings).

#### `release/skills_source.py`

```python
SKILLS_ROOT = Path(__file__).parent.parent / "skills"

def walk_skills() -> Iterator[tuple[str, Path, bytes]]:
    """Yield (skill_name, file_path, file_bytes) for every SKILL.md and
    adjacent *.yaml under skills/<skill>/."""
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        for fp in sorted(skill_dir.glob("*.md")):
            yield skill_dir.name, fp, fp.read_bytes()
        for fp in sorted(skill_dir.glob("*.yaml")):
            yield skill_dir.name, fp, fp.read_bytes()
```

#### `scripts/hooks_manifest_build_hook.py` (modified — add second class)

Hatch auto-discovers ALL `BuildHookInterface` subclasses defined in the registered plugin module. The existing `HooksManifestBuildHook` class stays unchanged; add `SkillsManifestBuildHook` alongside it in the same file:

```python
# Existing class (#237):
class HooksManifestBuildHook(BuildHookInterface):
    PLUGIN_NAME = "hooks-manifest"

    def initialize(self, version, build_data):
        # ... existing body unchanged
        ...


# NEW class added in #218 LLM-06:
class SkillsManifestBuildHook(BuildHookInterface):
    PLUGIN_NAME = "skills-manifest"

    def initialize(self, version, build_data):
        root = Path(self.root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from release import skills_manifest_generator, skills_source

        out = root / "share" / "bicameral-mcp" / "skills-manifest.toml"
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = skills_manifest_generator.generate_manifest(skills_source.walk_skills())
        skills_manifest_generator.write_manifest(manifest, out)
```

Both classes fire when hatch loads the registered module. No additional pyproject hooks.custom registration is required.

#### pyproject.toml extensions

The existing `[tool.hatch.build.targets.wheel.hooks.custom]` registration at `pyproject.toml:89` stays unchanged. Only the `[tool.hatch.build.targets.wheel.shared-data]` table is augmented:

```toml
# Existing (unchanged):
[tool.hatch.build.targets.wheel.hooks.custom]
path = "scripts/hooks_manifest_build_hook.py"

# Augmented (one new entry, table-augmentation not table-redefinition):
[tool.hatch.build.targets.wheel.shared-data]
"share/bicameral-mcp/hooks-manifest.json" = "share/bicameral-mcp/hooks-manifest.json"
"share/bicameral-mcp/skills-manifest.toml" = "share/bicameral-mcp/skills-manifest.toml"  # NEW
```

#### publish.yml extensions

After the existing hooks-manifest signing step, mirror the pattern for the skills manifest:

```yaml
      - name: Cosign keyless sign skills-manifest
        run: |
          cosign sign-blob --yes \
            --output-signature dist/share/bicameral-mcp/skills-manifest.toml.sig \
            --output-certificate dist/share/bicameral-mcp/skills-manifest.toml.crt \
            dist/share/bicameral-mcp/skills-manifest.toml
```

Add `skills-manifest.toml` + `.sig` + `.crt` to the `gh release upload` line.

### Unit Tests

- `tests/test_skills_manifest_generator.py::test_generate_manifest_includes_all_skill_md_files` — invokes `generate_manifest` against a stub skills tree fixture (2-3 skills with `SKILL.md` + a yaml); asserts the returned dict has one section per skill_name and one entry per file with matching SHA-256
- `tests/test_skills_manifest_generator.py::test_sha256_matches_file_bytes` — invokes against fixture with known content; asserts the manifest hash equals `hashlib.sha256(content).hexdigest()`
- `tests/test_skills_manifest_generator.py::test_manifest_serialization_is_deterministic` — invokes `write_manifest` twice with equivalent inputs in different iteration order; asserts byte-identical TOML output (sorted skills + sorted files-within-skill)
- `tests/test_skills_manifest_generator.py::test_manifest_omits_non_md_non_yaml_files` — fixture includes a `.txt` file in a skill dir; asserts the manifest does NOT include it (only `*.md` and `*.yaml` are signed)
- `tests/test_skills_manifest_generator.py::test_walk_skills_yields_skill_directories_only` — points `walk_skills` at a fixture tree containing both directories and stray files at the root; asserts only directories are walked (file-at-root is silently skipped)

## Phase 2: Verify-side — verify before copy

### Affected Files

- `tests/test_setup_wizard_skills_verify.py` (new) — functionality tests covering the verify path: positive (signed manifest matches), negative (sha256 mismatch), missing manifest, env-bypass with severity-3 ledger event, fail-closed without env var
- `release/skills_verify.py` (new) — sigstore-python verifier (mirrors `release/manifest_verify.py` from #237); `verify_skills_manifest` + `verify_skills_or_bypass` helpers; module-level `_VERIFIER_HOOK` for future activation
- `setup_wizard.py` — wire `_verify_intended_skills_writes()` helper into `_install_skills` (mirrors `_verify_intended_writes` from #237); each call site adds 1 LOC

### Changes

#### `release/skills_verify.py`

Mirror of `release/manifest_verify.py` from #237. Same shape: stub `_sigstore_verify` raising "deferred follow-up", real cross-check logic against expected per-file SHA-256, bypass-aware `verify_skills_or_bypass` helper.

The `expected_skills` argument to `verify_skills_manifest` is `dict[str, dict[str, str]]` — `{skill_name: {filename: sha256}}`. Cross-check: every entry the installer is about to copy must match the manifest.

#### `setup_wizard.py` modifications

```python
def _install_skills(repo_path: Path) -> int:
    """Copy skill definitions into .claude/skills/ in the target repo."""
    _verify_intended_skills_writes()  # NEW (1 LOC; same shape as _verify_intended_writes)
    skills_src = Path(__file__).parent / "skills"
    # ... rest unchanged
```

New helper `_verify_intended_skills_writes()`:
- Calls `_bundled_skills_manifest_paths()` (mirrors `_bundled_manifest_paths()`)
- If None (no bundled artifacts → dev install), returns
- Else: build `expected_skills` from the source `skills/` tree by walking + hashing each `SKILL.md` + `*.yaml`
- Calls `release.skills_verify.verify_skills_or_bypass(...)` with the manifest paths + expected dict
- On bypass: severity-3 `verification_bypassed` ledger event with `manifest_kind: "skills"` field for disambiguation

### Unit Tests

7 functional tests mirroring the #237 LLM-11 test suite:
- `test_verify_skills_manifest_returns_none_for_valid_signed_manifest`
- `test_verify_skills_manifest_raises_signature_error_when_sig_invalid`
- `test_verify_skills_manifest_raises_when_file_sha256_mismatches`
- `test_verify_skills_manifest_raises_when_manifest_file_missing`
- `test_verifier_hook_is_swappable_at_module_level`
- `test_install_skills_proceeds_with_bypass_event_when_env_var_set`
- `test_install_skills_raises_signature_error_when_env_var_unset`

## Phase 3: Documentation + research-brief closure

### Affected Files

- `docs/policies/host-trust-model.md` — extend the "Server-side guarantees" table with a row for "Skills manifest signature verification" (when bundled)
- `docs/research-brief-compliance-audit-2026-05-06.md` — mark LLM-06 entry closed by `release/skills_manifest_generator.py` + `release/skills_verify.py` + `docs/policies/host-trust-model.md` row

### Unit Tests

- `tests/test_compliance_policy_docs.py::test_host_trust_model_includes_skills_manifest_row` (extend existing test file) — verify the host-trust-model.md "Server-side guarantees" table includes the new row

## CI Commands

- `python -m pytest tests/test_skills_manifest_generator.py tests/test_setup_wizard_skills_verify.py -v` — runs the new functionality tests
- `python -m pytest -v` — full regression
- `ruff check .` + `ruff format --check .` — lint + format gates
- `mypy release/skills_manifest_generator.py release/skills_verify.py release/skills_source.py` — type gate
- `python -m build --wheel --outdir /tmp/test-build` — wheel build smoke test; verify `bicameral_mcp-*.data/data/share/bicameral-mcp/skills-manifest.toml` lands in the wheel
