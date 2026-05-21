"""Idempotency tests for the manifest build hook (#292).

``scripts/hooks_manifest_build_hook.py`` runs at wheel-build time. #292
makes it:

1. SKIP manifest regeneration when a non-empty pre-signed ``<manifest>.sigstore``
   bundle already exists (regenerating would change the bytes and void the
   signature the publish workflow produced before ``python -m build``).
2. GENERATE the manifest when no signed bundle is present, AND write an
   empty placeholder ``<manifest>.sigstore`` so the hatch ``shared-data``
   table resolves on a local-dev (unsigned) build.

The hook subclasses ``hatchling``'s ``BuildHookInterface``. ``hatchling``
is a build-time-only dependency and is not installed in the test
environment, so a minimal stub module is registered before import — this
is a genuine external boundary (the hatch build framework), and the
idempotency logic under test never touches it. The real
``release.hooks_manifest_generator`` / ``release.skills_manifest_generator``
collaborators ARE run (sociable).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_hatchling_stub() -> None:
    """Register a minimal ``hatchling.builders.hooks.plugin.interface``
    stub if hatchling is not installed, so the build-hook module imports.
    No-op when the real hatchling is present."""
    try:
        import hatchling.builders.hooks.plugin.interface  # noqa: F401

        return
    except ImportError:
        pass

    class _StubBuildHookInterface:
        """Stand-in for hatchling's BuildHookInterface. The build-hook only
        uses ``self.root`` and overrides ``initialize`` — both supplied by
        the test subclass / fixture, never by this base."""

    for name in (
        "hatchling",
        "hatchling.builders",
        "hatchling.builders.hooks",
        "hatchling.builders.hooks.plugin",
        "hatchling.builders.hooks.plugin.interface",
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules[
        "hatchling.builders.hooks.plugin.interface"
    ].BuildHookInterface = _StubBuildHookInterface


_ensure_hatchling_stub()
build_hook = importlib.import_module("scripts.hooks_manifest_build_hook")


@pytest.fixture
def hook(tmp_path: Path):
    """A ManifestsBuildHook whose `root` points at an isolated tmp tree.

    `BuildHookInterface.__init__` is bypassed (it expects hatch wiring);
    the hook only reads `self.root`, which we set directly."""
    instance = build_hook.ManifestsBuildHook.__new__(build_hook.ManifestsBuildHook)
    instance.__dict__["root"] = str(tmp_path)
    return instance


# --- _has_signed_bundle / _ensure_placeholder_bundle (static helpers) -------


def test_has_signed_bundle_false_when_bundle_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "hooks-manifest.json"
    manifest.write_text("{}")
    assert build_hook.ManifestsBuildHook._has_signed_bundle(manifest) is False


def test_has_signed_bundle_false_when_bundle_empty(tmp_path: Path) -> None:
    """A zero-byte placeholder is NOT a signed bundle."""
    manifest = tmp_path / "hooks-manifest.json"
    manifest.write_text("{}")
    (tmp_path / "hooks-manifest.json.sigstore").write_bytes(b"")
    assert build_hook.ManifestsBuildHook._has_signed_bundle(manifest) is False


def test_has_signed_bundle_true_when_bundle_non_empty(tmp_path: Path) -> None:
    manifest = tmp_path / "hooks-manifest.json"
    manifest.write_text("{}")
    (tmp_path / "hooks-manifest.json.sigstore").write_bytes(b"REAL-BUNDLE")
    assert build_hook.ManifestsBuildHook._has_signed_bundle(manifest) is True


def test_ensure_placeholder_bundle_creates_empty_file(tmp_path: Path) -> None:
    manifest = tmp_path / "share" / "bicameral-mcp" / "hooks-manifest.json"
    build_hook.ManifestsBuildHook._ensure_placeholder_bundle(manifest)
    bundle = Path(str(manifest) + ".sigstore")
    assert bundle.exists()
    assert bundle.stat().st_size == 0


def test_ensure_placeholder_bundle_leaves_real_bundle_untouched(tmp_path: Path) -> None:
    """A pre-existing (real) bundle is never overwritten by the placeholder
    writer — that would destroy the signature."""
    manifest = tmp_path / "hooks-manifest.json"
    bundle = tmp_path / "hooks-manifest.json.sigstore"
    bundle.write_bytes(b"REAL-SIGNED-BUNDLE")
    build_hook.ManifestsBuildHook._ensure_placeholder_bundle(manifest)
    assert bundle.read_bytes() == b"REAL-SIGNED-BUNDLE"


# --- _generate_hooks_manifest (skip-vs-regenerate + placeholder) ------------


def test_generate_hooks_manifest_creates_manifest_and_placeholder(hook, tmp_path: Path) -> None:
    """Local-dev path: no pre-signed bundle → the real generator runs and
    a zero-byte placeholder `.sigstore` is written."""
    hook._generate_hooks_manifest(tmp_path)
    manifest = tmp_path / "share" / "bicameral-mcp" / "hooks-manifest.json"
    bundle = tmp_path / "share" / "bicameral-mcp" / "hooks-manifest.json.sigstore"
    assert manifest.exists() and manifest.stat().st_size > 0
    assert bundle.exists() and bundle.stat().st_size == 0


def test_generate_hooks_manifest_skips_when_signed_bundle_present(hook, tmp_path: Path) -> None:
    """Release path: a pre-signed bundle is present → the hook leaves the
    manifest bytes UNTOUCHED (regenerating would void the signature)."""
    share = tmp_path / "share" / "bicameral-mcp"
    share.mkdir(parents=True)
    manifest = share / "hooks-manifest.json"
    bundle = share / "hooks-manifest.json.sigstore"
    sentinel = '{"manifest_version": 1, "hooks": [], "_presigned": true}'
    manifest.write_text(sentinel, encoding="utf-8")
    bundle.write_bytes(b"REAL-SIGNED-BUNDLE")

    hook._generate_hooks_manifest(tmp_path)

    assert manifest.read_text(encoding="utf-8") == sentinel, (
        "build hook regenerated a pre-signed manifest — signature voided"
    )
    assert bundle.read_bytes() == b"REAL-SIGNED-BUNDLE"


def test_generate_hooks_manifest_regenerates_when_bundle_zero_byte(hook, tmp_path: Path) -> None:
    """A zero-byte placeholder bundle does NOT count as pre-signed: a
    re-run regenerates the manifest (a stale local-dev manifest must
    refresh)."""
    share = tmp_path / "share" / "bicameral-mcp"
    share.mkdir(parents=True)
    manifest = share / "hooks-manifest.json"
    bundle = share / "hooks-manifest.json.sigstore"
    manifest.write_text("STALE", encoding="utf-8")
    bundle.write_bytes(b"")

    hook._generate_hooks_manifest(tmp_path)

    assert manifest.read_text(encoding="utf-8") != "STALE"
    assert "manifest_version" in manifest.read_text(encoding="utf-8")


# --- _generate_skills_manifest (mirror) ------------------------------------


def test_generate_skills_manifest_creates_manifest_and_placeholder(hook, tmp_path: Path) -> None:
    hook._generate_skills_manifest(tmp_path)
    manifest = tmp_path / "share" / "bicameral-mcp" / "skills-manifest.toml"
    bundle = tmp_path / "share" / "bicameral-mcp" / "skills-manifest.toml.sigstore"
    assert manifest.exists() and manifest.stat().st_size > 0
    assert bundle.exists() and bundle.stat().st_size == 0


def test_generate_skills_manifest_skips_when_signed_bundle_present(hook, tmp_path: Path) -> None:
    share = tmp_path / "share" / "bicameral-mcp"
    share.mkdir(parents=True)
    manifest = share / "skills-manifest.toml"
    bundle = share / "skills-manifest.toml.sigstore"
    sentinel = "manifest_version = 1\n# presigned\n"
    manifest.write_text(sentinel, encoding="utf-8")
    bundle.write_bytes(b"REAL-SIGNED-BUNDLE")

    hook._generate_skills_manifest(tmp_path)

    assert manifest.read_text(encoding="utf-8") == sentinel, (
        "build hook regenerated a pre-signed skills-manifest — signature voided"
    )
    assert bundle.read_bytes() == b"REAL-SIGNED-BUNDLE"
