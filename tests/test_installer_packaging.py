"""Wheel-packaging contract: ship the skill source tree.

Catches regressions where pyproject.toml drops `skills/` from the wheel.
The bug was silent: pre-fix, the wheel built cleanly with zero skill members
because `packages = ["."]` does not bundle a directory without `__init__.py`,
and the `artifacts` directive proved insufficient on its own.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_SRC = REPO_ROOT / "skills"


def _expected_skill_members() -> list[str]:
    """Enumerate every skills/<name>/SKILL.md present in the source tree.

    Built dynamically so the assertion stays correct as skills are added or
    removed; pre-fix this would fail because the wheel had zero skill members.
    """
    members: list[str] = []
    for skill_dir in sorted(SKILLS_SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            members.append(f"skills/{skill_dir.name}/SKILL.md")
    return members


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("wheel-out")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=str(REPO_ROOT), check=True, capture_output=True,
    )
    wheels = list(out_dir.glob("bicameral_mcp-*.whl"))
    assert len(wheels) == 1, f"expected 1 wheel, got {wheels}"
    return wheels[0]


def test_wheel_bundles_all_skill_sources(built_wheel: Path):
    expected = _expected_skill_members()
    assert expected, "no SKILL.md files found in skills/; nothing to assert"
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    missing = [m for m in expected if m not in names]
    assert not missing, (
        f"wheel missing {len(missing)} skill member(s): {missing[:5]}; "
        "ensure pyproject.toml force-includes skills/"
    )


def test_wheel_skill_md_is_non_empty(built_wheel: Path):
    expected = _expected_skill_members()
    sample = expected[0]
    with zipfile.ZipFile(built_wheel) as zf:
        with zf.open(sample) as f:
            content = f.read().decode("utf-8")
    assert "name:" in content, f"{sample} has no `name:` frontmatter; possible truncation"
    assert len(content) > 100, f"{sample} suspiciously small ({len(content)} bytes)"
