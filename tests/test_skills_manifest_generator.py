"""Functionality tests for `release.skills_manifest_generator` (#218 LLM-06).

Locks the deterministic skills-manifest contract:
- One section per skill_name; one entry per (SKILL.md / *.yaml) file
- SHA-256 over file bytes, hex-encoded
- Sorted-key TOML serialization (skills lex-sorted; files within skill lex-sorted)
- Non-md/non-yaml files skipped (only signed-content categories)
- walk_skills yields directories only (file-at-root silently skipped)
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from release import skills_manifest_generator as smg


def _entries(*items: tuple[str, str, bytes]) -> list[tuple[str, str, bytes]]:
    """Build a (skill_name, filename, content_bytes) sequence for tests."""
    return list(items)


def test_generate_manifest_includes_all_skill_md_files() -> None:
    entries = _entries(
        ("bicameral-ingest", "SKILL.md", b"ingest skill body"),
        ("bicameral-preflight", "SKILL.md", b"preflight skill body"),
        ("bicameral-preflight", "agent-instructions.yaml", b"yaml: contents"),
    )
    manifest = smg.generate_manifest(entries)
    assert "skills" in manifest
    assert set(manifest["skills"].keys()) == {"bicameral-ingest", "bicameral-preflight"}
    assert "SKILL.md" in manifest["skills"]["bicameral-ingest"]
    assert "agent-instructions.yaml" in manifest["skills"]["bicameral-preflight"]


def test_sha256_matches_file_bytes() -> None:
    body = b"deterministic skill body"
    entries = _entries(("solo-skill", "SKILL.md", body))
    manifest = smg.generate_manifest(entries)
    expected = hashlib.sha256(body).hexdigest()
    assert manifest["skills"]["solo-skill"]["SKILL.md"] == expected


def test_manifest_serialization_is_deterministic(tmp_path: Path) -> None:
    """Two equivalent inputs in different iteration order produce
    byte-identical TOML output."""
    entries_a = _entries(
        ("bicameral-ingest", "SKILL.md", b"a"),
        ("bicameral-config", "SKILL.md", b"b"),
    )
    entries_b = _entries(
        ("bicameral-config", "SKILL.md", b"b"),
        ("bicameral-ingest", "SKILL.md", b"a"),
    )
    out_a = tmp_path / "manifest_a.toml"
    out_b = tmp_path / "manifest_b.toml"
    smg.write_manifest(smg.generate_manifest(entries_a), out_a)
    smg.write_manifest(smg.generate_manifest(entries_b), out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_manifest_omits_non_md_non_yaml_files(tmp_path: Path) -> None:
    """A *.txt file in a skill dir must NOT appear in the manifest —
    only *.md and *.yaml are signed content categories.
    Note: walk_skills enforces this filter; generate_manifest assumes
    the caller pre-filters. Test the walker directly."""
    skills_root = tmp_path / "skills"
    (skills_root / "bicameral-ingest").mkdir(parents=True)
    (skills_root / "bicameral-ingest" / "SKILL.md").write_bytes(b"signed content")
    (skills_root / "bicameral-ingest" / "notes.txt").write_bytes(b"unsigned content")
    (skills_root / "bicameral-ingest" / "config.yaml").write_bytes(b"yaml content")

    from release.skills_source import walk_skills

    walked = list(walk_skills(skills_root))
    filenames = [name for _, name, _ in walked]
    assert "SKILL.md" in filenames
    assert "config.yaml" in filenames
    assert "notes.txt" not in filenames


def test_walk_skills_yields_skill_directories_only(tmp_path: Path) -> None:
    """A stray file at skills/ root is silently skipped (only directories
    are walked)."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    (skills_root / "stray.md").write_bytes(b"file at root, not in a skill dir")
    (skills_root / "bicameral-ingest").mkdir()
    (skills_root / "bicameral-ingest" / "SKILL.md").write_bytes(b"in-skill content")

    from release.skills_source import walk_skills

    walked = list(walk_skills(skills_root))
    skill_names = {sn for sn, _, _ in walked}
    assert skill_names == {"bicameral-ingest"}


def test_manifest_round_trip_via_tomllib(tmp_path: Path) -> None:
    """Written TOML parses back to the same logical structure (defends
    against escaping bugs in the manual emitter)."""
    entries = _entries(
        ("bicameral-ingest", "SKILL.md", b"body-a"),
        ("bicameral-history", "SKILL.md", b"body-b"),
    )
    out = tmp_path / "m.toml"
    smg.write_manifest(smg.generate_manifest(entries), out)
    parsed = tomllib.loads(out.read_text(encoding="utf-8"))
    assert parsed["manifest_version"] == 1
    assert parsed["skills"]["bicameral-ingest"]["SKILL.md"] == hashlib.sha256(b"body-a").hexdigest()
    assert (
        parsed["skills"]["bicameral-history"]["SKILL.md"] == hashlib.sha256(b"body-b").hexdigest()
    )


def test_generate_manifest_orders_skills_lexicographically(tmp_path: Path) -> None:
    """Skill names appear in the rendered TOML in lex-sorted order regardless
    of input ordering."""
    entries = _entries(
        ("zeta-skill", "SKILL.md", b"z"),
        ("alpha-skill", "SKILL.md", b"a"),
        ("mid-skill", "SKILL.md", b"m"),
    )
    out = tmp_path / "m.toml"
    smg.write_manifest(smg.generate_manifest(entries), out)
    text = out.read_text(encoding="utf-8")
    # Find positions of each skill section header
    pos_alpha = text.find("[skills.alpha-skill]")
    pos_mid = text.find("[skills.mid-skill]")
    pos_zeta = text.find("[skills.zeta-skill]")
    assert pos_alpha < pos_mid < pos_zeta
