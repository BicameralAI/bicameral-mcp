"""scripts/lint_skill_governance.py — static lint for skill governance (#205 Phase 1).

Scans every ``skills/<name>/SKILL.md`` for sentence-level patterns that
claim a default privacy / security behavior (``"by default"``,
``"redacted by default"``, ``"extract only"``, etc.). For each matched
claim, checks ``governance-gates.yaml`` for a corresponding registered
gate entry. Findings — claims without a registered backing gate — are
reported as advisory in Phase 1.

Phase 1 contract: the lint exits 1 if findings are present (so a future
CI workflow can opt to enforce); but is NOT wired into CI yet (that's
Phase 4 of #205). Operators invoke it locally:

    python scripts/lint_skill_governance.py --skill-dir skills/ \\
        --registry governance-gates.yaml

See ``docs/governance/doctrine-deterministic-governance.md`` for the rule
this lint enforces.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

# Sentence-level patterns that signal a default behavior claim. Case-
# insensitive, line-based scan. Extend this list as new patterns surface
# during the retroactive sweep (#205 Phase 3).
_DEFAULT_CLAIM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bby\s+default\b[^.\n]*", re.IGNORECASE),
    re.compile(r"\bredact(?:ed)?\s+by\s+default\b[^.\n]*", re.IGNORECASE),
    re.compile(r"\bextract(?:s|ed)?\s+only\b[^.\n]*", re.IGNORECASE),
    re.compile(r"\bnever\s+include\b[^.\n]*", re.IGNORECASE),
    re.compile(r"\bdefault(?:s|ed)?\s+to\b[^.\n]*", re.IGNORECASE),
]


@dataclass(frozen=True)
class Finding:
    """One unregistered default-claim in a SKILL.md."""

    skill: str  # folder name (e.g. "bicameral-ingest")
    line: int  # 1-indexed line number in the SKILL.md
    claim: str  # the matched sentence/phrase, stripped
    suggestion: str  # operator-facing remediation hint


def main(argv: list[str] | None = None) -> int:
    """Argparse entry. Returns 0 if no findings; 1 otherwise."""
    parser = argparse.ArgumentParser(
        description="Lint SKILL.md files for default-behavior claims that "
        "lack a registered deterministic gate (#205 Phase 1).",
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=Path("skills"),
        help="Root of the skill tree (default: skills/).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("governance-gates.yaml"),
        help="Path to the governance-gates registry (default: governance-gates.yaml).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a markdown report.",
    )
    args = parser.parse_args(argv)

    registry = _load_registry(args.registry)
    findings = _scan_skill_tree(args.skill_dir, registry)

    if args.json:
        import json as _json

        report = [
            {
                "skill": f.skill,
                "line": f.line,
                "claim": f.claim,
                "suggestion": f.suggestion,
            }
            for f in findings
        ]
        print(_json.dumps(report, indent=2))
    else:
        print(format_report(findings))

    return 0 if not findings else 1


def _load_registry(path: Path) -> dict[str, list[dict]]:
    """Load governance-gates.yaml via ``yaml.safe_load``.

    SafeLoader required (per OWASP A08 + `context.py:63` precedent — never
    use ``yaml.load`` on operator-authored config).
    Returns ``{skill_name: [gate_entry, ...]}``; absent file → empty.
    """
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    gates: dict[str, list[dict]] = {}
    for entry in raw.get("gates") or []:
        if not isinstance(entry, dict):
            continue
        skill = str(entry.get("skill") or "").strip()
        if not skill:
            continue
        gates.setdefault(skill, []).append(entry)
    return gates


def _scan_skill_tree(
    skill_dir: Path,
    registry: dict[str, list[dict]],
) -> list[Finding]:
    """Walk every SKILL.md under ``skill_dir`` and accumulate findings."""
    findings: list[Finding] = []
    if not skill_dir.exists():
        return findings
    for skill_path in sorted(skill_dir.iterdir()):
        if not skill_path.is_dir():
            continue
        md_path = skill_path / "SKILL.md"
        if not md_path.exists():
            continue
        findings.extend(_lint_skill(md_path, registry.get(skill_path.name, [])))
    return findings


def _lint_skill(skill_md: Path, gates: list[dict]) -> list[Finding]:
    """Lint one SKILL.md against its registered gates."""
    text = skill_md.read_text(encoding="utf-8")
    skill_name = skill_md.parent.name
    findings: list[Finding] = []
    for line_no, claim in _extract_default_claims(text):
        if _match_registered_gate(claim, gates) is not None:
            continue
        findings.append(
            Finding(
                skill=skill_name,
                line=line_no,
                claim=claim.strip(),
                suggestion=(
                    f"Either revise the SKILL.md text to drop the default "
                    f"claim, or add a gate entry under skill: {skill_name} in "
                    f"governance-gates.yaml pointing to the deterministic "
                    f"enforcement code."
                ),
            )
        )
    return findings


def _extract_default_claims(text: str) -> list[tuple[int, str]]:
    """Return (line_number, matched_sentence) tuples for every default-claim
    pattern hit in the text. 1-indexed line numbers."""
    out: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in _DEFAULT_CLAIM_PATTERNS:
            for match in pattern.finditer(line):
                out.append((line_no, match.group(0)))
    return out


def _match_registered_gate(claim: str, gates: list[dict]) -> dict | None:
    """For each gate entry, fuzzy-match its ``instruction_pattern`` field
    against ``claim`` (substring match, case-insensitive). Returns the
    first matching gate or ``None``."""
    claim_lower = claim.lower()
    for gate in gates:
        pattern = str(gate.get("instruction_pattern") or "").strip().lower()
        if pattern and pattern in claim_lower:
            return gate
    return None


def format_report(findings: list[Finding]) -> str:
    """Render findings as a markdown report. Empty input → friendly OK message."""
    if not findings:
        return "✅ governance-gates lint: no unregistered default claims found.\n"
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.skill, []).append(f)
    lines: list[str] = [
        f"# Governance-gates lint — {len(findings)} finding(s)",
        "",
        "Per `docs/governance/doctrine-deterministic-governance.md`: "
        "skill-text claims of default behavior must have a deterministic "
        "backing gate registered in `governance-gates.yaml`.",
        "",
    ]
    for skill in sorted(grouped):
        lines.append(f"## `{skill}`")
        lines.append("")
        lines.append("| Line | Claim | Suggestion |")
        lines.append("|---|---|---|")
        for f in grouped[skill]:
            claim_short = f.claim if len(f.claim) <= 80 else f.claim[:77] + "…"
            # Escape pipe-chars in the suggestion so they don't break the
            # markdown table column boundary. Done outside the f-string to
            # satisfy py3.11 (no backslashes inside f-string expressions).
            suggestion_escaped = f.suggestion.replace("|", "\\|")
            lines.append(f"| {f.line} | `{claim_short}` | {suggestion_escaped} |")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
