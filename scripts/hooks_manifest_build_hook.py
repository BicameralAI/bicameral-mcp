"""Hatch build hook for bicameral-mcp wheel-bundled manifests.

Generates BOTH the hooks-manifest (#218 LLM-11) and skills-manifest
(#218 LLM-06) at wheel-build time:

- ``share/bicameral-mcp/hooks-manifest.json`` from ``release.hooks_source``
- ``share/bicameral-mcp/skills-manifest.toml`` from ``release.skills_source``

Hatch's ``[tool.hatch.build.targets.wheel.hooks.custom]`` registers a
SINGLE plugin class per module path; both manifest generations run in
one ``initialize`` call. Generated manifests are bundled into the
wheel via the ``[tool.hatch.build.targets.wheel.shared-data]`` table
in pyproject.toml.

At install time, ``setup_wizard._bundled_manifest_paths()`` (hooks) and
``setup_wizard._bundled_skills_manifest_paths()`` (skills) discover the
respective files under ``sys.prefix/share/bicameral-mcp/`` and the
matching verifier cross-checks SHA-256 entries before any
host-config-write or skill-copy.

#292 — sigstore bundle handling:

- **Release build**: the publish workflow generates each manifest and
  signs it with ``cosign sign-blob --new-bundle-format --bundle`` BEFORE
  ``python -m build``. When this hook runs it sees a ``<manifest>`` and a
  pre-signed ``<manifest>.sigstore`` already present and SKIPS manifest
  regeneration — regenerating would change the manifest bytes and void
  the signature. The ``shared-data`` table then bundles both files.
- **Local-dev build**: no OIDC token, so no signing. This hook generates
  the manifest and writes an EMPTY placeholder ``<manifest>.sigstore`` so
  the ``shared-data`` table resolves. ``setup_wizard._bundled_*`` treats
  a zero-byte bundle as absent → verification defers, as it did before
  #292.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ManifestsBuildHook(BuildHookInterface):
    PLUGIN_NAME = "manifests"

    def initialize(self, version: str, build_data: dict) -> None:
        # Make project root importable so release.* modules resolve.
        root = Path(self.root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        self._generate_hooks_manifest(root)
        self._generate_skills_manifest(root)

    @staticmethod
    def _has_signed_bundle(manifest: Path) -> bool:
        """True when a NON-EMPTY ``<manifest>.sigstore`` exists next to
        ``manifest`` — i.e. the publish workflow pre-signed it. A zero-byte
        placeholder (local-dev) counts as absent so the manifest still
        regenerates on a plain local build."""
        bundle = Path(str(manifest) + ".sigstore")
        return bundle.exists() and bundle.stat().st_size > 0

    @staticmethod
    def _ensure_placeholder_bundle(manifest: Path) -> None:
        """Write an empty placeholder ``<manifest>.sigstore`` when none
        exists, so the hatch ``shared-data`` entry resolves on a local-dev
        build. The placeholder is never signed and never verifies; it only
        satisfies the packager. A real bundle (release build) is left
        untouched."""
        bundle = Path(str(manifest) + ".sigstore")
        if not bundle.exists():
            bundle.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_bytes(b"")

    def _generate_hooks_manifest(self, root: Path) -> None:
        out = root / "share" / "bicameral-mcp" / "hooks-manifest.json"
        if self._has_signed_bundle(out):
            # Pre-signed by the publish workflow — regenerating would void
            # the signature. Leave both manifest and bundle as-is.
            return
        from release import hooks_manifest_generator, hooks_source

        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = hooks_manifest_generator.generate_manifest(hooks_source)
        hooks_manifest_generator.write_manifest(manifest, out)
        self._ensure_placeholder_bundle(out)

    def _generate_skills_manifest(self, root: Path) -> None:
        out = root / "share" / "bicameral-mcp" / "skills-manifest.toml"
        if self._has_signed_bundle(out):
            # Pre-signed by the publish workflow — regenerating would void
            # the signature. Leave both manifest and bundle as-is.
            return
        from release import skills_manifest_generator, skills_source

        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = skills_manifest_generator.generate_manifest(skills_source.walk_skills())
        skills_manifest_generator.write_manifest(manifest, out)
        self._ensure_placeholder_bundle(out)
