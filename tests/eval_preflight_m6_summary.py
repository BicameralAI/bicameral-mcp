#!/usr/bin/env python3
"""Render M6 preflight retrieval recall eval JSON as a GitHub Actions
step-summary markdown block (#58 Phase A).

Reads the JSON written by ``tests/eval_preflight_m6_recall.py -o <path>``
and prints a markdown table to stdout; the workflow step appends stdout
to ``$GITHUB_STEP_SUMMARY`` so the metrics show up on the GitHub Actions
run page without needing to download the artifact.

Fail-quiet: missing JSON, parse errors, and missing keys degrade to a
one-line note rather than failing the step. The eval is warn-only at
the CI hook initially; this renderer never gates merge.

Usage:
    python tests/eval_preflight_m6_summary.py test-results/m6-preflight-recall.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _safe_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _emoji_for(value: float | None, gate: float) -> str:
    if value is None:
        return "—"
    if value >= gate:
        return "✅"
    if value >= gate - 0.15:
        return "⚠️"
    return "❌"


def render(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("rows") or []

    out: list[str] = []
    out.append("## M6 preflight retrieval recall (#58)")
    out.append("")

    total = summary.get("total_cases", 0)
    if total == 0:
        out.append("> No cases ran. Check the M6 step log — fixture filter, env, or runner error.")
        return "\n".join(out)

    recall = summary.get("recall")
    fire_rate = summary.get("fire_rate")
    gate_mode = summary.get("gate_mode", "warn")
    breaches = summary.get("gate_breaches") or []

    out.append("| Metric | Value | Gate | |")
    out.append("|---|---|---|---|")
    out.append(
        f"| **Overall recall** | {_safe_pct(recall)} | ≥ 70.0% | {_emoji_for(recall, 0.70)} |"
    )
    out.append(
        f"| **Fire rate** | {_safe_pct(fire_rate)} | ≥ 60.0% | {_emoji_for(fire_rate, 0.60)} |"
    )
    out.append("")

    outcomes = summary.get("outcomes") or {}
    out.append(f"**Outcome breakdown** (total {total}, gate-mode `{gate_mode}`):")
    out.append("")
    out.append("| Outcome | Count | Share |")
    out.append("|---|---|---|")
    for label in ("surfaced", "missed", "error"):
        count = outcomes.get(label, 0)
        share = f"{(count / total) * 100:.1f}%" if total > 0 else "—"
        out.append(f"| {label} | {count} | {share} |")
    out.append("")

    per_mode = summary.get("per_miss_mode") or {}
    if per_mode:
        out.append("**Per miss-mode:**")
        out.append("")
        out.append("| Miss mode | Total | Surfaced | Recall | Gate (50%) |")
        out.append("|---|---|---|---|---|")
        for mode in sorted(per_mode):
            stats = per_mode[mode]
            out.append(
                f"| `{mode}` | {stats.get('total', 0)} | {stats.get('surfaced', 0)} | "
                f"{_safe_pct(stats.get('recall'))} | "
                f"{_emoji_for(stats.get('recall'), 0.50)} |"
            )
        out.append("")

    if breaches:
        gate_note = (
            "(hard — failing CI)" if gate_mode == "hard" else "(warn-only — does not fail CI)"
        )
        out.append(f"⚠ **Gate breaches** {gate_note}:")
        for b in breaches:
            out.append(f"- {b}")
        out.append("")

    # Missed-case detail (helps PMs see WHICH cases the runtime is missing)
    misses = [r for r in rows if r.get("outcome") == "missed"]
    if misses:
        out.append(f"<details><summary>{len(misses)} missed cases (click to expand)</summary>")
        out.append("")
        out.append("| Case | Mode | Topic | Why it should have surfaced |")
        out.append("|---|---|---|---|")
        for r in misses[:25]:
            topic = (r.get("topic") or "").replace("\n", " ").replace("|", "·")
            if len(topic) > 80:
                topic = topic[:77] + "…"
            descr = (r.get("intended_description") or "").replace("\n", " ").replace("|", "·")
            if len(descr) > 100:
                descr = descr[:97] + "…"
            out.append(
                f"| `{r.get('case_id', '?')}` | {r.get('miss_mode', '?')} | {topic} | {descr} |"
            )
        if len(misses) > 25:
            out.append("")
            out.append(f"_…and {len(misses) - 25} more (see artifact)._")
        out.append("")
        out.append("</details>")

    errors = [r for r in rows if r.get("outcome") == "error"]
    if errors:
        out.append("")
        out.append(f"_⚠ {len(errors)} infra error(s) (seeder failures, not agent misses)._")

    return "\n".join(out)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: eval_preflight_m6_summary.py <path-to-m6-json>", file=sys.stderr)
        print("## M6 preflight retrieval recall\n\n_renderer error: missing input arg_")
        return 0

    path = Path(sys.argv[1])
    if not path.exists():
        print("## M6 preflight retrieval recall")
        print()
        print(f"_eval JSON not found at `{path}` — eval step likely errored or skipped._")
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("## M6 preflight retrieval recall")
        print()
        print(f"_could not parse `{path}`: {exc}_")
        return 0

    print(render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
