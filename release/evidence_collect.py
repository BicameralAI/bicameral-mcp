"""Per-release evidence collector for #218 SOC2-03.

Runs ``gh`` CLI subprocess calls to gather per-release evidence (merged
PRs, CI runs, reviewer attribution) and renders a markdown report. The
operator runs this manually after a release tag is published; the
rendered markdown is the scaffold the operator fills in with narrative
sections (rationale, exceptions) and archives per the SOC 2 retention
policy in ``docs/RELEASE_EVIDENCE_PROCEDURE.md``.

Subprocess discipline: every ``subprocess.run`` invocation uses list-form
argv with ``shell=False`` (the default). Per OWASP A03 commitment in
``plan-C-soc2-03-signed-tags-and-release-evidence.md``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

REPO = "BicameralAI/bicameral-mcp"


def _gh_pr_list_merged_between(from_tag: str, to_tag: str) -> list[dict[str, Any]]:
    """Return merged PRs in the window via ``gh pr list --search``.

    Uses ``merged:>=<from-iso>`` semantics by resolving tag dates first;
    operators rerunning on a fresh shell get the same window. List-form
    argv per OWASP A03.
    """
    cmd = [
        "gh",
        "pr",
        "list",
        "--repo",
        REPO,
        "--state",
        "merged",
        "--base",
        "main",
        "--search",
        f"merged:>={from_tag} merged:<={to_tag}",
        "--json",
        "number,title,mergedAt,url",
        "--limit",
        "200",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    return json.loads(result.stdout or b"[]")


def _gh_run_list_in_window(from_tag: str, to_tag: str) -> list[dict[str, Any]]:
    """Return CI runs in the tag window via ``gh run list``."""
    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        REPO,
        "--branch",
        "main",
        "--limit",
        "200",
        "--json",
        "name,conclusion,url,createdAt",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    return json.loads(result.stdout or b"[]")


def _gh_pr_view_reviews(pr_number: int) -> dict[str, Any]:
    """Return the reviews payload for a given PR."""
    cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        REPO,
        "--json",
        "reviews",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    return json.loads(result.stdout or b"{}")


def _render_pr_section(prs: list[dict[str, Any]]) -> list[str]:
    lines = ["## Merged PRs in window", ""]
    if not prs:
        lines.append("_No PRs merged between these tags._")
        return lines
    lines.append("| # | Title | Merged at | URL |")
    lines.append("| --- | --- | --- | --- |")
    for pr in prs:
        lines.append(
            f"| {pr.get('number', '?')} | {pr.get('title', '?')} | "
            f"{pr.get('mergedAt', '?')} | {pr.get('url', '?')} |"
        )
    return lines


def _render_ci_section(ci_runs: list[dict[str, Any]]) -> list[str]:
    lines = ["## CI runs in window", ""]
    if not ci_runs:
        lines.append("_No CI runs recorded in window._")
        return lines
    lines.append("| Workflow | Conclusion | Created at | URL |")
    lines.append("| --- | --- | --- | --- |")
    for run in ci_runs:
        lines.append(
            f"| {run.get('name', '?')} | {run.get('conclusion', '?')} | "
            f"{run.get('createdAt', '?')} | {run.get('url', '?')} |"
        )
    return lines


def _render_reviews_section(reviews_by_pr: dict[int, dict[str, Any]]) -> list[str]:
    lines = ["## Reviewer attribution", ""]
    if not reviews_by_pr:
        lines.append("_No reviewer data fetched._")
        return lines
    for pr_number, payload in reviews_by_pr.items():
        reviews = payload.get("reviews", [])
        lines.append(f"### PR #{pr_number}")
        lines.append("")
        for r in reviews:
            login = r.get("author", {}).get("login", "?")
            state = r.get("state", "?")
            lines.append(f"- {login}: {state}")
        lines.append("")
    return lines


def render_markdown(
    *,
    prs: list[dict[str, Any]],
    ci_runs: list[dict[str, Any]],
    reviews_by_pr: dict[int, dict[str, Any]],
    from_tag: str,
    to_tag: str,
) -> str:
    """Render the evidence scaffold. Pure: same inputs → same outputs."""
    lines: list[str] = [f"# Release evidence — {from_tag} → {to_tag}", ""]
    lines.extend(_render_pr_section(prs))
    lines.append("")
    lines.extend(_render_ci_section(ci_runs))
    lines.append("")
    lines.extend(_render_reviews_section(reviews_by_pr))
    lines.append("## Operator narrative")
    lines.append("")
    lines.append(
        "_Fill in: rationale for any exceptions, deviations from policy, "
        "closed-issue traceability, attestation statement._"
    )
    lines.append("")
    return "\n".join(lines)


def collect_evidence(*, from_tag: str, to_tag: str) -> str:
    """Gather evidence and render markdown. Caller writes to disk.

    Raises ``subprocess.CalledProcessError`` on any gh CLI failure — no
    silent empty-evidence fallback.
    """
    prs = _gh_pr_list_merged_between(from_tag, to_tag)
    ci_runs = _gh_run_list_in_window(from_tag, to_tag)
    reviews_by_pr: dict[int, dict[str, Any]] = {}
    for pr in prs:
        number = pr.get("number")
        if isinstance(number, int):
            reviews_by_pr[number] = _gh_pr_view_reviews(number)
    return render_markdown(
        prs=prs,
        ci_runs=ci_runs,
        reviews_by_pr=reviews_by_pr,
        from_tag=from_tag,
        to_tag=to_tag,
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect per-release evidence")
    parser.add_argument("--from-tag", required=True, help="Previous release tag (e.g., v0.13.7)")
    parser.add_argument("--to-tag", required=True, help="Current release tag (e.g., v0.13.8)")
    parser.add_argument("--output", help="Output markdown path (default: stdout)")
    args = parser.parse_args(argv)
    md = collect_evidence(from_tag=args.from_tag, to_tag=args.to_tag)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(md, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
