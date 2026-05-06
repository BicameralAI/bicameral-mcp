"""Hatch build hook: generate ``share/bicameral-mcp/hooks-manifest.json``
at wheel-build time from ``release.hooks_source`` (#218 Phase 1).

The generated manifest is then bundled into the wheel via the
``[tool.hatch.build.targets.wheel.shared-data]`` table in pyproject.toml.
At install time, ``setup_wizard._bundled_manifest_paths()`` discovers
the file under ``sys.prefix/share/bicameral-mcp/`` and the verifier
cross-checks SHA-256 entries before any host-config write.

Cosign signature emission lives in the publish workflow (separate
from the wheel build); the .sig and .crt are attached to the GitHub
Release rather than bundled into the wheel.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class HooksManifestBuildHook(BuildHookInterface):
    PLUGIN_NAME = "hooks-manifest"

    def initialize(self, version: str, build_data: dict) -> None:
        # Make project root importable so `release.hooks_source` resolves.
        root = Path(self.root)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from release import hooks_manifest_generator, hooks_source

        out = root / "share" / "bicameral-mcp" / "hooks-manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = hooks_manifest_generator.generate_manifest(hooks_source)
        hooks_manifest_generator.write_manifest(manifest, out)
