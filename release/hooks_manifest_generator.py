"""Deterministic hook-manifest generator for #218 Phase 1.

Walks a module exposing a ``BICAMERAL_HOOKS`` list-of-dicts (each entry
``{event_type, command}``) and emits ``hooks-manifest.json`` carrying
the SHA-256 of each command. Output bytes are deterministic so cosign
signatures bind exactly to the rendered file.

Subprocess discipline: this module performs no subprocess work; pure
file I/O. Per OWASP A03 commitment in plan-218.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

_MANIFEST_VERSION = 1


def generate_manifest(module: Any) -> dict[str, Any]:
    """Build the manifest dict from ``module.BICAMERAL_HOOKS``.

    Raises ``AttributeError`` if the module does not declare the contract.
    Each entry contributes ``{event_type, command, sha256}``. Output is
    sorted by ``event_type`` lexicographically.
    """
    hooks_in = module.BICAMERAL_HOOKS  # AttributeError surfaces explicitly
    entries: list[dict[str, str]] = []
    for hook in hooks_in:
        cmd: str = hook["command"]
        entries.append(
            {
                "event_type": hook["event_type"],
                "command": cmd,
                "sha256": hashlib.sha256(cmd.encode("utf-8")).hexdigest(),
            }
        )
    entries.sort(key=lambda h: h["event_type"])
    return {"manifest_version": _MANIFEST_VERSION, "hooks": entries}


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write the manifest to ``output_path`` with sorted keys + fixed indent.

    Bytes are deterministic across runs given the same input dict.
    """
    output_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate hooks-manifest.json")
    parser.add_argument("output", type=Path, help="Output JSON path")
    parser.add_argument(
        "--source-module",
        default="release.hooks_source",
        help="Module exposing BICAMERAL_HOOKS (default: release.hooks_source)",
    )
    args = parser.parse_args(argv)
    module = importlib.import_module(args.source_module)
    manifest = generate_manifest(module)
    write_manifest(manifest, args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
