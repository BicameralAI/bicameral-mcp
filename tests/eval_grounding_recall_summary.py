#!/usr/bin/env python3
"""Render M2 grounding-recall eval JSON as a GitHub Actions step-summary
markdown block (#280 PR-3 — replaces the dropped dashboard panel).

Reads the JSON written by ``tests/eval_grounding_recall.py -o <path>``
and prints a markdown table to stdout; the workflow step appends stdout
to ``$GITHUB_STEP_SUMMARY`` so the metrics show up on the GitHub Actions
run page without needing to download the artifact.

Fail-quiet: missing JSON, parse errors, and missing keys all degrade to
a one-line note rather than failing the step. The eval itself is
warn-only at PR-2's CI hook; this renderer never gates merge.

Usage:
    python tests/eval_grounding_recall_summary.py test-results/m2-grounding-recall.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _safe_pct(numerator: float | None, denominator: float | None) -> str:
    """Format ``numerator/denominator`` as a percent, or ``—`` when undefined."""
    if numerator is None or denominator is None or denominator == 0:
        return "—"
    return f"{(numerator / denominator) * 100:.1f}%"


def _safe_pct_value(value: float | None) -> str:
    """Format an already-computed precision/recall (0.0-1.0) as a percent."""
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _emoji_for(precision: float | None, gate: float = 0.85) -> str:
    if precision is None:
        return "—"
    if precision >= gate:
        return "✅"
    if precision >= gate - 0.15:
        return "⚠️"
    return "❌"


def render(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("rows") or []

    out: list[str] = []
    out.append("## M2 grounding precision (caller-LLM bind eval, #280)")
    out.append("")

    total = summary.get("total_cases", 0)
    if total == 0:
        out.append(
            "> No cases ran. (API key missing, fixture filtered to zero, "
            "or the eval step errored — check the M2 step log.)"
        )
        return "\n".join(out)

    precision = summary.get("precision")
    recall = summary.get("recall")
    abort_rate = summary.get("abort_rate")
    breaches = summary.get("gate_breaches") or []
    model = summary.get("model", "default")
    gate_mode = summary.get("gate_mode", "warn")

    out.append("| Metric | Value | Gate | |")
    out.append("|---|---|---|---|")
    out.append(
        f"| **Precision** | {_safe_pct_value(precision)} | ≥ 85.0% | "
        f"{_emoji_for(precision, 0.85)} |"
    )
    out.append(f"| **Recall** | {_safe_pct_value(recall)} | ≥ 80.0% | {_emoji_for(recall, 0.80)} |")
    out.append(
        f"| **Abort rate** | {_safe_pct_value(abort_rate)} | ≤ 30.0% | "
        f"{'✅' if abort_rate is not None and abort_rate <= 0.30 else '⚠️'} |"
    )
    out.append("")

    outcomes = summary.get("outcomes") or {}
    out.append(f"**Outcome breakdown** (total {total}, model `{model}`, gate-mode `{gate_mode}`):")
    out.append("")
    out.append("| Outcome | Count | Share |")
    out.append("|---|---|---|")
    for label in ("correct", "wrong_symbol", "wrong_file", "aborted"):
        count = outcomes.get(label, 0)
        out.append(f"| {label} | {count} | {_safe_pct(count, total)} |")
    out.append("")

    per_type = summary.get("per_case_type") or {}
    if per_type:
        out.append("**Per case type:**")
        out.append("")
        out.append("| Case type | Total | Correct | Recall |")
        out.append("|---|---|---|---|")
        for case_type in sorted(per_type):
            stats = per_type[case_type]
            out.append(
                f"| {case_type} | {stats.get('total', 0)} | "
                f"{stats.get('correct', 0)} | "
                f"{_safe_pct_value(stats.get('recall'))} |"
            )
        out.append("")

    if breaches:
        out.append(f"⚠ **Gate breaches** (warn-only — does not fail CI): {'; '.join(breaches)}")
        out.append("")

    misses = [r for r in rows if r.get("outcome") not in ("correct", None)]
    if misses:
        out.append(f"<details><summary>{len(misses)} missed cases (click to expand)</summary>")
        out.append("")
        out.append("| Case | Type | Outcome | Bound |")
        out.append("|---|---|---|---|")
        for r in misses[:25]:  # cap so the summary stays readable
            bound = (
                f"`{r.get('bound_file') or '—'}::{r.get('bound_symbol') or '—'}`"
                if not r.get("aborted")
                else "_aborted_"
            )
            out.append(
                f"| {r.get('case_id', '?')} | {r.get('case_type', '?')} | "
                f"{r.get('outcome', '?')} | {bound} |"
            )
        if len(misses) > 25:
            out.append("")
            out.append(f"_…and {len(misses) - 25} more (see artifact)._")
        out.append("")
        out.append("</details>")

    out.append(
        f"\n_Tokens: {summary.get('tokens_in_total', 0)} in / "
        f"{summary.get('tokens_out_total', 0)} out · "
        f"avg turns {summary.get('avg_turns', 0)}_"
    )
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: eval_grounding_recall_summary.py <path-to-m2-json>", file=sys.stderr)
        # Fail quiet on the GitHub-summary path (the workflow's continue-on-error
        # already shields us); print a minimal stub so the section isn't blank.
        print("## M2 grounding precision\n\n_renderer error: missing input arg_")
        return 0

    path = Path(sys.argv[1])
    if not path.exists():
        print("## M2 grounding precision")
        print()
        print(f"_eval JSON not found at `{path}` — eval step likely errored or skipped._")
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("## M2 grounding precision")
        print()
        print(f"_could not parse `{path}`: {exc}_")
        return 0

    print(render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
