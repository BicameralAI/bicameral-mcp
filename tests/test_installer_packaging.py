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
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
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


# --- #292: sigstore-bundle packaging contract ------------------------------


def _shared_data_members(wheel: Path) -> set[str]:
    """Return wheel members under the hatch `shared-data` install prefix
    (`<dist>.data/data/share/bicameral-mcp/...`)."""
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    return {n for n in names if "share/bicameral-mcp/" in n}


def test_wheel_bundles_manifests_and_sigstore_bundles(built_wheel: Path):
    """#292: the wheel must ship BOTH manifests AND their `.sigstore`
    bundles via hatch `shared-data` (4 files total) so install-time
    verification can re-engage. A local-dev build emits zero-byte
    placeholder bundles — they are still present as wheel members.
    """
    members = _shared_data_members(built_wheel)
    required_suffixes = [
        "share/bicameral-mcp/hooks-manifest.json",
        "share/bicameral-mcp/hooks-manifest.json.sigstore",
        "share/bicameral-mcp/skills-manifest.toml",
        "share/bicameral-mcp/skills-manifest.toml.sigstore",
    ]
    for suffix in required_suffixes:
        assert any(m.endswith(suffix) for m in members), (
            f"wheel missing shared-data member ending in {suffix!r}; "
            f"present share/ members: {sorted(members)}"
        )


def test_wheel_manifests_are_non_empty(built_wheel: Path):
    """The manifests themselves must carry content (the build hook ran).
    The `.sigstore` bundles MAY be zero-byte placeholders on a local-dev
    build, so only the manifests are size-checked here."""
    members = _shared_data_members(built_wheel)
    with zipfile.ZipFile(built_wheel) as zf:
        for suffix in (
            "share/bicameral-mcp/hooks-manifest.json",
            "share/bicameral-mcp/skills-manifest.toml",
        ):
            member = next(m for m in members if m.endswith(suffix))
            data = zf.read(member)
            assert len(data) > 0, f"{member} is empty; build hook did not generate the manifest"
