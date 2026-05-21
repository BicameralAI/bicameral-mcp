"""Workflow-lint guard for `.github/workflows/publish.yml` (#292).

The #292 supply-chain invariant: manifest generation + ``cosign sign-blob``
MUST run BEFORE ``python -m build``. The build hook is idempotent — it
respects a pre-signed ``.sigstore`` bundle and skips regeneration — so if
the build ran first it would emit an UNSIGNED manifest and the later sign
step would sign a file that never reaches the wheel. This test asserts the
step ordering in the YAML source so a reorder cannot land silently.

Also pins the two non-negotiable cosign details:
- ``cosign-installer`` is version-pinned via ``cosign-release`` (a 2.6.x
  release; ``--new-bundle-format`` needs cosign >= 2.4.0).
- both ``sign-blob`` invocations pass ``--new-bundle-format`` so cosign
  emits the Sigstore protobuf bundle ``sigstore-python`` can read.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISH_YML = REPO_ROOT / ".github" / "workflows" / "publish.yml"


def _yaml_text() -> str:
    return PUBLISH_YML.read_text(encoding="utf-8")


def test_publish_workflow_exists() -> None:
    assert PUBLISH_YML.exists(), f"missing workflow: {PUBLISH_YML}"


def _build_step_index(text: str) -> int:
    """Index of the actual `python -m build` STEP (the `run:` line), not a
    mention of it in a comment."""
    idx = text.find("run: python -m build")
    assert idx != -1, "publish.yml has no `run: python -m build` step"
    return idx


def test_cosign_sign_precedes_python_build() -> None:
    """The cosign sign-blob steps must appear BEFORE `python -m build` in
    the YAML source order (#292 workflow-ordering invariant)."""
    text = _yaml_text()
    first_sign = text.find("cosign sign-blob")
    build_idx = _build_step_index(text)

    assert first_sign != -1, "publish.yml has no `cosign sign-blob` step"
    assert first_sign < build_idx, (
        "cosign sign-blob must run before `python -m build`: signing after "
        "the build emits an unsigned manifest into the wheel"
    )


def test_both_manifests_signed_before_build() -> None:
    """Both the hooks-manifest and skills-manifest sign steps precede the
    build — not just the first one."""
    text = _yaml_text()
    build_idx = _build_step_index(text)
    hooks_sign = text.find("hooks-manifest.json.sigstore")
    skills_sign = text.find("skills-manifest.toml.sigstore")

    assert hooks_sign != -1 and hooks_sign < build_idx, (
        "hooks-manifest must be signed before `python -m build`"
    )
    assert skills_sign != -1 and skills_sign < build_idx, (
        "skills-manifest must be signed before `python -m build`"
    )


def test_cosign_installer_is_version_pinned() -> None:
    """`sigstore/cosign-installer` must pin a specific cosign release via
    `cosign-release` — `--new-bundle-format` requires cosign >= 2.4.0 and
    an unpinned installer could drift onto cosign 3.x where the flag is
    retired (#292 Open Question 1)."""
    text = _yaml_text()
    assert "sigstore/cosign-installer" in text, "publish.yml does not install cosign"
    assert "cosign-release:" in text, "cosign-installer must pin a version via `cosign-release:`"
    # The pin must be a 2.x release (>= 2.4.0 for --new-bundle-format).
    assert "cosign-release: 'v2." in text or 'cosign-release: "v2.' in text, (
        "cosign-release must pin a cosign 2.x version"
    )


def test_sign_blob_uses_new_bundle_format() -> None:
    """Every `cosign sign-blob` for a manifest must pass `--new-bundle-format`
    so the output is the Sigstore protobuf bundle `sigstore-python`'s
    `Bundle.from_json()` can read."""
    text = _yaml_text()
    # Count manifest sign-blob steps (those producing a `.sigstore` bundle).
    manifest_sign_steps = text.count("--bundle share/bicameral-mcp/")
    assert manifest_sign_steps == 2, (
        f"expected 2 manifest `cosign sign-blob --bundle` steps, found {manifest_sign_steps}"
    )
    new_format_count = text.count("--new-bundle-format")
    assert new_format_count >= 2, (
        f"both manifest sign-blob steps must pass `--new-bundle-format` (found {new_format_count})"
    )


def test_release_attaches_sigstore_bundles_not_sig_crt() -> None:
    """The GitHub Release attachment step ships the single-file `.sigstore`
    bundles; the legacy `.sig` + `.crt` manifest attachments are gone."""
    text = _yaml_text()
    assert "hooks-manifest.json.sigstore" in text
    assert "skills-manifest.toml.sigstore" in text
    assert "hooks-manifest.json.sig " not in text and "hooks-manifest.json.sig\n" not in text, (
        "legacy hooks-manifest .sig attachment still present"
    )
    assert "skills-manifest.toml.crt" not in text, (
        "legacy skills-manifest .crt attachment still present"
    )
