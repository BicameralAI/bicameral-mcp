"""Single source of truth for skill content (#218 LLM-06).

Walks the ``skills/`` directory at wheel-build time. Yields one tuple
per signed-content file (``SKILL.md`` and ``*.yaml``). Files outside
those categories are skipped. Stray files at ``skills/`` root are
silently skipped — only directories represent skills.

Consumed by ``release.skills_manifest_generator.generate_manifest`` and
by ``setup_wizard._verify_intended_skills_writes`` to derive the
expected per-file SHA-256 dict at install time.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

DEFAULT_SKILLS_ROOT = Path(__file__).parent.parent / "skills"

_SIGNED_GLOBS = ("*.md", "*.yaml")


def walk_skills(root: Path | None = None) -> Iterator[tuple[str, str, bytes]]:
    """Yield ``(skill_name, filename, file_bytes)`` for every signed
    content file under each skill directory.

    Iteration order is deterministic: skills lex-sorted, files within
    each skill lex-sorted. Stray files at the root are skipped.
    """
    skills_root = root if root is not None else DEFAULT_SKILLS_ROOT
    if not skills_root.exists():
        return
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        for pattern in _SIGNED_GLOBS:
            for fp in sorted(skill_dir.glob(pattern)):
                yield skill_dir.name, fp.name, fp.read_bytes()
