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


# Mirrors the FAILURE_MODE_NEXT_STEPS dict in eval_grounding_recall.py.
# Kept in sync manually — both files must update if the taxonomy changes.
# Renderer doesn't import from the runner because the runner pulls in
# fixtures that the renderer doesn't need (keep the renderer dependency-free).
_FAILURE_MODE_HINTS: dict[str, str] = {
    "wrong_module": "tighten case-A decision text to name the module/scope",
    "wrong_intent": "improve bind skill prompt's 'abort on weak evidence'",
    "cross_language_confusion": "make decision text mention runtime explicitly",
    "wrong_symbol_in_right_file": "right module — sub-region disambiguation gap",
    "hallucinated_symbol": "handler failsafe firing; LLM degraded — model bump?",
    "span_mismatch": "handler failsafe firing; LLM degraded — model bump?",
    "aborted_correctly": "behavioral decisions correctly route to PM review",
    "aborted_incorrectly": "bind skill is too cautious — loosen abort rule",
    "eval_error": "infra (API timeout / network) — not an agent issue",
    "uncategorized": "unexpected outcome — investigate manually",
}


def _render_failure_modes(rows: list[dict[str, Any]]) -> list[str]:
    """Render Jin's failure-mode enumeration (#280 PR #292).

    Groups misses by ``failure_mode`` (deterministic classifier in
    ``tests/eval_grounding_recall.py:classify_failure_mode``), surfaces the
    top 3 categories with up to 2 example cases each. PM-readable.

    Pure layout function — no surprises if every case is `correct`
    (returns nothing). Categories are kept in plan-readable order: misses
    first (sorted by count), eval_error last.
    """
    misses = [r for r in rows if r.get("failure_mode") not in (None, "correct")]
    if not misses:
        return []

    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in misses:
        mode = str(row.get("failure_mode") or "uncategorized")
        by_mode.setdefault(mode, []).append(row)

    # Sort: eval_error always last (infra noise), rest by descending count.
    def _sort_key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int]:
        mode, rows_in = item
        is_infra = 1 if mode == "eval_error" else 0
        return (is_infra, -len(rows_in))

    ranked = sorted(by_mode.items(), key=_sort_key)
    top = ranked[:3]

    out: list[str] = []
    out.append("**Failure modes** (top categories — PM-actionable):")
    out.append("")
    out.append("| Category | Count | Suggested next step | Example |")
    out.append("|---|---|---|---|")
    for mode, mode_rows in top:
        hint = _FAILURE_MODE_HINTS.get(mode, "—")
        # Up to 2 examples per category. Each example: case_id + 1-line
        # decision-text excerpt (truncated) + agent reasoning if present.
        examples: list[str] = []
        for r in mode_rows[:2]:
            case_id = r.get("case_id", "?")
            reasoning = (r.get("reasoning") or "").strip()
            abort_reason = (r.get("abort_reason") or "").strip()
            error_msg = (r.get("error_msg") or "").strip()
            tail = reasoning or abort_reason or error_msg or "(no reasoning captured)"
            tail = tail.replace("\n", " ").replace("|", "·")
            if len(tail) > 110:
                tail = tail[:107] + "…"
            examples.append(f"`{case_id}` — {tail}")
        examples_md = "<br>".join(examples) if examples else "—"
        out.append(f"| `{mode}` | {len(mode_rows)} | {hint} | {examples_md} |")
    if len(ranked) > 3:
        out.append("")
        out.append(f"_…and {len(ranked) - 3} more category(ies); see the per-case list below._")
    out.append("")
    return out


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
    for label in ("correct", "wrong_symbol", "wrong_file", "aborted", "eval_error"):
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

    out.extend(_render_failure_modes(rows))

    misses = [r for r in rows if r.get("outcome") not in ("correct", None)]
    if misses:
        out.append(f"<details><summary>{len(misses)} missed cases (click to expand)</summary>")
        out.append("")
        out.append("| Case | Type | Outcome | Bound / Reason |")
        out.append("|---|---|---|---|")
        for r in misses[:25]:  # cap so the summary stays readable
            outcome = r.get("outcome", "?")
            if outcome == "eval_error":
                detail = f"_error: `{r.get('error_msg') or 'unknown'}`_"
            elif r.get("aborted"):
                detail = f"_aborted: {r.get('abort_reason') or 'no reason given'}_"
            else:
                detail = f"`{r.get('bound_file') or '—'}::{r.get('bound_symbol') or '—'}`"
            out.append(
                f"| {r.get('case_id', '?')} | {r.get('case_type', '?')} | {outcome} | {detail} |"
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
