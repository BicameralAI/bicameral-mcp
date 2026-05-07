"""Deterministic skills-manifest generator for #218 LLM-06.

Consumes a sequence of ``(skill_name, filename, file_bytes)`` tuples
(typically from ``release.skills_source.walk_skills``) and emits a
``skills/MANIFEST.toml`` carrying the SHA-256 of each signed-content
file. Output bytes are deterministic so cosign signatures bind exactly
to the rendered file.

TOML emission is manual (stdlib-only). Acceptance bound from plan
audit: if the emitter exceeds ~30 LOC, swap to ``tomli_w``. Current
emitter is well under that ceiling because the schema is simple
(version int + dict-of-dict-of-strings).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_MANIFEST_VERSION = 1


def generate_manifest(entries: Iterable[tuple[str, str, bytes]]) -> dict[str, Any]:
    """Build the manifest dict from ``(skill_name, filename, file_bytes)``
    entries. Each entry contributes one ``skills.<skill_name>.<filename>``
    SHA-256 hex value.
    """
    skills: dict[str, dict[str, str]] = {}
    for skill_name, filename, file_bytes in entries:
        digest = hashlib.sha256(file_bytes).hexdigest()
        skills.setdefault(skill_name, {})[filename] = digest
    return {"manifest_version": _MANIFEST_VERSION, "skills": skills}


def _emit_toml(manifest: dict[str, Any]) -> str:
    """Serialize the manifest to deterministic TOML text.

    Layout:
        manifest_version = 1

        [skills.<skill_name>]
        "<filename>" = "<sha256-hex>"

    Skills lex-sorted; filenames within each skill lex-sorted. Filenames
    are quoted (TOML keys with non-bare-key chars require quoting).
    """
    lines: list[str] = [f"manifest_version = {manifest['manifest_version']}", ""]
    skills = manifest.get("skills", {})
    for skill_name in sorted(skills):
        lines.append(f"[skills.{skill_name}]")
        for filename in sorted(skills[skill_name]):
            digest = skills[skill_name][filename]
            lines.append(f'"{filename}" = "{digest}"')
        lines.append("")
    return "\n".join(lines)


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    """Write the manifest as deterministic TOML to ``output_path``."""
    output_path.write_text(_emit_toml(manifest), encoding="utf-8")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate skills/MANIFEST.toml")
    parser.add_argument("output", type=Path, help="Output TOML path")
    parser.add_argument(
        "--skills-source",
        default="release.skills_source",
        help="Module exposing walk_skills() (default: release.skills_source)",
    )
    args = parser.parse_args(argv)
    module = importlib.import_module(args.skills_source)
    manifest = generate_manifest(module.walk_skills())
    write_manifest(manifest, args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
