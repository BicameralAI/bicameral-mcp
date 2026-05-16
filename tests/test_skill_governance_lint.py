"""Unit tests for scripts/lint_skill_governance.py — #205 Phase 1.

Sociable per CLAUDE.md: the lint is exercised through its real module
surface, against real fixture SKILL.md files at
``tests/fixtures/skill_lint/``. No mocked file IO; no monkey-patched
parsers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

# Make ``scripts/`` importable for the test runner.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import lint_skill_governance as lint  # noqa: E402

FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "skill_lint"


# ── _extract_default_claims ───────────────────────────────────────────────


def test_extract_default_claims_finds_by_default_pattern() -> None:
    text = "By default, the agent extracts only keys."
    claims = lint._extract_default_claims(text)
    assert len(claims) >= 1
    assert any("by default" in c.lower() for _, c in claims)


def test_extract_default_claims_case_insensitive() -> None:
    text = "BY DEFAULT, x is redacted."
    claims = lint._extract_default_claims(text)
    assert len(claims) >= 1
    assert "BY DEFAULT" in claims[0][1].upper()


def test_extract_default_claims_ignores_unrelated_text() -> None:
    """Plain mention of the word 'default' without a claim should not fire.

    The patterns key on \"by default\" / \"defaults to\" / \"extract only\" / etc.,
    not on the bare word 'default'."""
    text = "The default port is 8080. The system is robust."
    claims = lint._extract_default_claims(text)
    # No "by default" / "default to" / "extract only" / "redact" / "never include"
    assert claims == []


def test_extract_default_claims_finds_redact_pattern() -> None:
    text = "Branch names are redacted by default."
    claims = lint._extract_default_claims(text)
    assert len(claims) >= 1


def test_extract_default_claims_finds_extract_only_pattern() -> None:
    text = "The handler extracts only the keys."
    claims = lint._extract_default_claims(text)
    assert any("extract" in c.lower() and "only" in c.lower() for _, c in claims)


def test_extract_default_claims_reports_1_indexed_line_numbers() -> None:
    text = "\nLine 2 has no claim.\nBy default, line 3 has a claim.\n"
    claims = lint._extract_default_claims(text)
    assert len(claims) == 1
    line_no, _ = claims[0]
    assert line_no == 3


# ── _match_registered_gate ────────────────────────────────────────────────


def test_match_registered_gate_finds_exact_substring() -> None:
    """Registry patterns are matched as case-insensitive substrings, so
    the registered ``instruction_pattern`` must appear verbatim in the
    claim text. Operators choose patterns that hit common variations of
    the SKILL.md text — typically the noun phrase rather than the verb
    form, which is more stable across rewrites."""
    gate = {
        "skill": "fixture",
        "instruction_pattern": "only the keys",  # noun phrase, substring of claim
        "backing_gate": "handlers/fixture.py::_extract_keys_only",
        "gate_kind": "server",
    }
    claim = "By default, the agent extracts only the keys and discards values."
    assert lint._match_registered_gate(claim, [gate]) is gate


def test_match_registered_gate_case_insensitive() -> None:
    gate = {
        "instruction_pattern": "REDACTED BY DEFAULT",
    }
    claim = "Branch names are redacted by default."
    assert lint._match_registered_gate(claim, [gate]) is gate


def test_match_registered_gate_returns_none_for_unregistered() -> None:
    gate = {"instruction_pattern": "something else entirely"}
    claim = "By default, the agent extracts only the keys."
    assert lint._match_registered_gate(claim, [gate]) is None


def test_match_registered_gate_returns_none_for_empty_pattern() -> None:
    """An empty/missing pattern field must not silently match every claim."""
    gate = {"instruction_pattern": ""}
    claim = "By default, x is y."
    assert lint._match_registered_gate(claim, [gate]) is None


# ── _lint_skill against fixtures ──────────────────────────────────────────


def test_lint_skill_clean_fixture_produces_no_findings() -> None:
    findings = lint._lint_skill(FIXTURES / "clean_skill" / "SKILL.md", [])
    assert findings == []


def test_lint_skill_flagged_fixture_produces_findings() -> None:
    findings = lint._lint_skill(FIXTURES / "flagged_skill" / "SKILL.md", [])
    assert len(findings) >= 2
    skills = {f.skill for f in findings}
    assert skills == {"flagged_skill"}


def test_lint_skill_registered_fixture_with_matching_gate_is_clean() -> None:
    """When the registry has a gate matching the SKILL.md's default claim,
    the lint emits no finding for that claim."""
    gate = {
        "skill": "registered_skill",
        "instruction_pattern": "extracts only the public keys",
        "backing_gate": "handlers/fixture.py::_extract_keys_only",
        "gate_kind": "server",
    }
    findings = lint._lint_skill(FIXTURES / "registered_skill" / "SKILL.md", [gate])
    assert findings == []


def test_lint_skill_registered_fixture_without_matching_gate_still_flags() -> None:
    """Negative case: registered_skill's SKILL.md WITHOUT the matching gate
    in the registry — lint flags it."""
    findings = lint._lint_skill(FIXTURES / "registered_skill" / "SKILL.md", [])
    assert len(findings) >= 1


# ── _load_registry ────────────────────────────────────────────────────────


def test_load_registry_returns_empty_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.yaml"
    assert lint._load_registry(missing) == {}


def test_load_registry_groups_entries_by_skill(tmp_path: Path) -> None:
    yaml_path = tmp_path / "gates.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "gates": [
                    {
                        "skill": "skill-a",
                        "instruction_pattern": "p1",
                        "backing_gate": "h.py::f",
                        "gate_kind": "server",
                    },
                    {
                        "skill": "skill-a",
                        "instruction_pattern": "p2",
                        "backing_gate": "h.py::g",
                        "gate_kind": "server",
                    },
                    {
                        "skill": "skill-b",
                        "instruction_pattern": "p3",
                        "backing_gate": "h.py::h",
                        "gate_kind": "env",
                    },
                ]
            }
        )
    )
    reg = lint._load_registry(yaml_path)
    assert set(reg.keys()) == {"skill-a", "skill-b"}
    assert len(reg["skill-a"]) == 2
    assert len(reg["skill-b"]) == 1


def test_load_registry_uses_safe_load_for_owasp_a08(tmp_path: Path) -> None:
    """The lint must use yaml.safe_load — feeding a Python-object-tag YAML
    would otherwise execute arbitrary code. SafeLoader rejects such tags
    by raising ConstructorError; the lint either propagates or filters
    to empty. Either way, no arbitrary code executes."""
    yaml_path = tmp_path / "evil.yaml"
    # !!python/object/apply tags would deserialize-and-call under
    # yaml.load() (unsafe). yaml.safe_load() rejects them.
    yaml_path.write_text(
        "gates:\n"
        "  - skill: x\n"
        "    instruction_pattern: !!python/object/apply:os.system ['echo p0wned']\n"
        "    backing_gate: y\n"
        "    gate_kind: env\n"
    )
    with pytest.raises(yaml.YAMLError):
        lint._load_registry(yaml_path)


# ── main() integration ───────────────────────────────────────────────────


def test_main_exits_0_when_skill_dir_has_no_skills(tmp_path: Path) -> None:
    """Empty skill dir → no SKILL.md files → no findings → exit 0."""
    empty_skills = tmp_path / "skills"
    empty_skills.mkdir()
    empty_registry = tmp_path / "gates.yaml"
    empty_registry.write_text("gates: []\n")
    rc = lint.main(["--skill-dir", str(empty_skills), "--registry", str(empty_registry)])
    assert rc == 0


def test_main_exits_1_when_findings_present_in_flagged_fixture(tmp_path: Path, capsys) -> None:
    """Wire main() against the flagged fixture; expect exit 1 + report."""
    # Build a tmp skill tree containing just the flagged fixture, copied.
    skills = tmp_path / "skills"
    skills.mkdir()
    target = skills / "flagged_skill"
    target.mkdir()
    src = FIXTURES / "flagged_skill" / "SKILL.md"
    (target / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    reg = tmp_path / "gates.yaml"
    reg.write_text("gates: []\n")
    rc = lint.main(["--skill-dir", str(skills), "--registry", str(reg)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "flagged_skill" in captured.out


def test_main_json_mode_emits_valid_json(tmp_path: Path, capsys) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    target = skills / "flagged_skill"
    target.mkdir()
    (target / "SKILL.md").write_text(
        (FIXTURES / "flagged_skill" / "SKILL.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    reg = tmp_path / "gates.yaml"
    reg.write_text("gates: []\n")
    rc = lint.main(["--skill-dir", str(skills), "--registry", str(reg), "--json"])
    assert rc == 1
    captured = capsys.readouterr()
    decoded = json.loads(captured.out)
    assert isinstance(decoded, list)
    assert len(decoded) >= 2
    for f in decoded:
        assert {"skill", "line", "claim", "suggestion"} <= set(f.keys())


# ── format_report ────────────────────────────────────────────────────────


def test_format_report_returns_friendly_ok_on_empty() -> None:
    report = lint.format_report([])
    assert "no unregistered default claims found" in report


def test_format_report_renders_markdown_table_for_findings() -> None:
    finding = lint.Finding(
        skill="x",
        line=42,
        claim="By default, x is y.",
        suggestion="Add a gate entry.",
    )
    report = lint.format_report([finding])
    assert "# Governance-gates lint" in report
    assert "## `x`" in report
    assert "42" in report
    assert "By default, x is y." in report
    assert "Add a gate entry" in report
