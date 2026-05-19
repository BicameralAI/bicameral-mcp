#!/usr/bin/env python3
"""Render M_skill_preflight eval JSONs as a GitHub Actions step-summary
markdown block (#306 Part C).

Reads two JSON reports — Step-1 (per-axis relevance recall) from
``tests/eval_preflight_skill_step1.py -o ...`` and Step-0 (history-
invocation rate) from ``tests/eval_preflight_skill_invocation.py
-o ...`` — and prints one combined markdown block to stdout. The CI
step appends stdout to ``$GITHUB_STEP_SUMMARY`` so reviewers can read
the per-axis recall + the 2x2 invocation matrix without downloading
the artifact.

Fail-quiet: missing JSON, parse errors, and missing keys degrade to a
one-line note rather than failing the step. The eval is warn-only at
the CI hook initially (matches #288 M2 warn→hard pattern); this
renderer never gates merge.

Mirrors ``tests/eval_grounding_recall_summary.py`` (#285) and
``tests/eval_preflight_m6_summary.py`` (#304).

Usage:
    python tests/eval_preflight_skill_summary.py
        --step1 test-results/skill-step1.json
        --step0 test-results/skill-step0.json

Both flags are optional; whichever JSON is missing renders a
"not run" note in its section. At least one of the two must exist or
the script prints a single explanatory line and exits zero.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _safe_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _emoji_for(value: float | None, gate: float, *, higher_is_better: bool = True) -> str:
    if value is None:
        return "—"
    if higher_is_better:
        if value >= gate:
            return "✅"
        if value >= gate - 0.15:
            return "⚠️"
        return "❌"
    else:
        if value <= gate:
            return "✅"
        if value <= gate + 0.15:
            return "⚠️"
        return "❌"


def _load(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def render_step1(payload: dict[str, Any] | None) -> list[str]:
    out: list[str] = ["### Step-1 — Per-axis relevance recall (Part A)", ""]
    if payload is None:
        out.append("> Not run (or JSON missing). See workflow log.")
        return out

    summary = payload.get("summary") or {}
    total = summary.get("total", 0)
    if total == 0:
        out.append("> No cases ran.")
        return out

    overall = summary.get("recall")
    out.append("| Metric | Value | Gate | |")
    out.append("|---|---|---|---|")
    gate = float(payload.get("min_recall") or 0.70)
    out.append(
        f"| **Overall recall** | {_safe_pct(overall)} (n={summary.get('hits', 0) + summary.get('misses', 0)}/{total}) "
        f"| ≥ {gate * 100:.0f}% | {_emoji_for(overall, gate)} |"
    )
    out.append(
        f"| Hits / Misses / Skips / Errors | "
        f"{summary.get('hits', 0)} / {summary.get('misses', 0)} / "
        f"{summary.get('skips', 0)} / {summary.get('errors', 0)} | | |"
    )
    out.append("")

    per_axis = summary.get("per_axis") or {}
    if per_axis:
        out.append(
            "**Per axis** (M1 = vocab_mismatch · M4 = ungrounded · FF1 = false_fire · D = direct_match):"
        )
        out.append("")
        out.append("| Axis | Total | Hits | Misses | Recall | Gate |")
        out.append("|---|---|---|---|---|---|")
        for axis_key in sorted(per_axis):
            stats = per_axis[axis_key]
            out.append(
                f"| `{axis_key}` | {stats.get('total', 0)} | {stats.get('hit', 0)} | "
                f"{stats.get('miss', 0)} | {_safe_pct(stats.get('recall'))} | "
                f"{_emoji_for(stats.get('recall'), gate)} |"
            )
        out.append("")

    breaches = summary.get("gate_breaches") or []
    if breaches:
        gate_note = "(hard — failing CI)" if payload.get("gate_mode") == "hard" else "(warn-only)"
        out.append(f"⚠ **Gate breaches** {gate_note}:")
        for b in breaches:
            out.append(f"- {b}")
        out.append("")

    return out


def render_step0(payload: dict[str, Any] | None) -> list[str]:
    out: list[str] = ["### Step-0 — History-invocation rate (Part B)", ""]
    if payload is None:
        out.append("> Not run (or JSON missing). See workflow log.")
        return out

    summary = payload.get("summary") or {}
    total = summary.get("total", 0)
    if total == 0:
        out.append("> No cases ran.")
        return out

    recall = summary.get("recall")
    precision = summary.get("precision")
    fp_rate = summary.get("fp_rate")
    gate_recall = float(payload.get("min_recall") or 0.50)
    gate_fp = float(payload.get("max_fp_rate") or 0.30)

    counts = summary.get("counts") or {}
    tp = summary.get("tp", 0)
    fn = summary.get("fn", 0)
    fp = summary.get("fp", 0)
    tn = summary.get("tn", 0)

    out.append("Does the agent invoke `bicameral.history()` when the handler returns empty?")
    out.append("")
    out.append("| Metric | Value | Gate | |")
    out.append("|---|---|---|---|")
    out.append(
        f"| **Recall** (should-invoke) | {_safe_pct(recall)} (TP={tp} / (TP+FN)={tp + fn}) "
        f"| ≥ {gate_recall * 100:.0f}% | {_emoji_for(recall, gate_recall)} |"
    )
    out.append(f"| **Precision** (TP / (TP+FP)) | {_safe_pct(precision)} | — | |")
    out.append(
        f"| **FP rate** (over-fetch) | {_safe_pct(fp_rate)} (FP={fp} / (FP+TN)={fp + tn}) "
        f"| ≤ {gate_fp * 100:.0f}% | {_emoji_for(fp_rate, gate_fp, higher_is_better=False)} |"
    )
    out.append("")

    out.append("**Confusion matrix:**")
    out.append("")
    out.append("|  | invoked = True | invoked = False |")
    out.append("|---|---|---|")
    out.append(
        f"| should-invoke = True | TP = {tp} (`invoked_history_correctly`) | FN = {fn} (`skipped_history_should_have`) |"
    )
    out.append(
        f"| should-invoke = False | FP = {fp} (`invoked_history_unnecessarily`) | TN = {tn} (`proceeded_without_fetch`) |"
    )
    out.append("")

    skip_count = counts.get("skip", 0)
    error_count = counts.get("error", 0)
    if skip_count or error_count:
        out.append(f"_skips={skip_count} · errors={error_count}_")
        out.append("")

    breaches = summary.get("gate_breaches") or []
    if breaches:
        gate_note = "(hard — failing CI)" if payload.get("gate_mode") == "hard" else "(warn-only)"
        out.append(f"⚠ **Gate breaches** {gate_note}:")
        for b in breaches:
            out.append(f"- {b}")
        out.append("")

    # Surface FN cases (the most important failure mode — the agent silently
    # drops a relevant decision because it didn't fetch history). FP cases
    # are over-eager but cheaper, so don't enumerate them here.
    rows = payload.get("rows") or []
    fns = [r for r in rows if r.get("outcome") == "skipped_history_should_have"]
    if fns:
        out.append("**Missed should-invoke cases (FN):**")
        out.append("")
        for fn_row in fns:
            out.append(f"- `{fn_row.get('id')}` — reasoning: {fn_row.get('reasoning', '')[:200]!r}")
        out.append("")

    return out


def render(*, step1: dict[str, Any] | None, step0: dict[str, Any] | None) -> str:
    out: list[str] = ["## M_skill_preflight — preflight skill-layer eval (#306)", ""]
    if step1 is None and step0 is None:
        out.append(
            "> Neither Step-1 nor Step-0 JSON found. See workflow log for the CLI invocation."
        )
        return "\n".join(out)
    out.extend(render_step1(step1))
    out.extend(render_step0(step0))
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1", type=Path, help="Path to Step-1 JSON")
    parser.add_argument("--step0", type=Path, help="Path to Step-0 JSON")
    # Positional fallback so the workflow can call this script with a single
    # path the way it calls eval_preflight_m6_summary.py.
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="(Deprecated) positional JSON paths — auto-routed by 'step'",
    )
    args = parser.parse_args()

    step1_payload = _load(args.step1)
    step0_payload = _load(args.step0)

    for p in args.paths or []:
        loaded = _load(p)
        if loaded is None:
            continue
        step_tag = loaded.get("step")
        if step_tag == "step1_relevance" and step1_payload is None:
            step1_payload = loaded
        elif step_tag == "step0_invocation" and step0_payload is None:
            step0_payload = loaded

    print(render(step1=step1_payload, step0=step0_payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
