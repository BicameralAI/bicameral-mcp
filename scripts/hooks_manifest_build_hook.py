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

Cosign signature emission lives in the publish workflow (separate
from the wheel build); the .sig and .crt are attached to the GitHub
Release rather than bundled into the wheel.
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

    def _generate_hooks_manifest(self, root: Path) -> None:
        from release import hooks_manifest_generator, hooks_source

        out = root / "share" / "bicameral-mcp" / "hooks-manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = hooks_manifest_generator.generate_manifest(hooks_source)
        hooks_manifest_generator.write_manifest(manifest, out)

    def _generate_skills_manifest(self, root: Path) -> None:
        from release import skills_manifest_generator, skills_source

        out = root / "share" / "bicameral-mcp" / "skills-manifest.toml"
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = skills_manifest_generator.generate_manifest(skills_source.walk_skills())
        skills_manifest_generator.write_manifest(manifest, out)
