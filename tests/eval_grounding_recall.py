#!/usr/bin/env python3
"""M2 grounding-recall eval — measures caller-LLM bind precision/recall (#280 PR-2).

Drives the bicameral-bind skill against a synthetic fixture (≥ 21
decisions across same-name-different-module / similar-intent /
cross-language cases), captures what each judgment bound vs. ground
truth, and emits precision / recall / abort-rate.

Three measurement axes (the split matters for diagnosis):
  - Precision = correct / (correct + wrong_symbol + wrong_file)
                of the bindings the agent committed, what fraction were right
  - Recall    = correct / total_rows
                of the ground-truth bindings, how many the agent got right
                (aborts and wrong bindings BOTH count against)
  - Abort rate = aborted / total_rows
                 first-class signal because the bind-skill makes 'abort on
                 weak evidence' an explicit contract — high abort rate
                 means the skill is too conservative

Usage:
    .venv/bin/python tests/eval_grounding_recall.py
        --model claude-haiku-4-5-20251001
        --gate-mode warn
        -o test-results/m2-grounding-recall.json

Flags:
    --case-filter        only run cases of this type (debug)
    --case-id            run a single case by id (debug)
    --model              override BICAMERAL_GROUNDING_EVAL_MODEL
    --min-recall         gate threshold (default 0.80, per #280)
    --min-precision      gate threshold (default 0.85, per #280)
    --max-abort-rate     gate threshold (default 0.30 — agent too cautious)
    --gate-mode          'warn' (advisory, default) | 'hard' (exit non-zero on miss)
    -o / --output        write JSON report
    --verbose            print per-case rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# tests/ has no __init__.py; import siblings via dir on sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "eval"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "grounding_recall"))

from _bind_judge import BindJudgment, fixture_exists, run_bind_judgment  # type: ignore[import-not-found]  # noqa: E402, I001
from _gate import gate_exit_code, is_inconclusive  # type: ignore[import-not-found]  # noqa: E402, I001
from dataset import ALL_CASES, GENERATOR_VERSION, GroundingCase, cases_by_type  # type: ignore[import-not-found]  # noqa: E402, I001

FIXTURE_REPO = REPO_ROOT / "tests" / "fixtures" / "grounding_recall" / "repo"


# ── Outcome classification ──────────────────────────────────────────────────


def _classify(case: GroundingCase, judgment: BindJudgment) -> str:
    """Map (case, judgment) → outcome category for metrics."""
    if judgment.aborted:
        return "aborted"
    if judgment.bound_file == case.intended_file and judgment.bound_symbol == case.intended_symbol:
        return "correct"
    if judgment.bound_file == case.intended_file:
        return "wrong_symbol"
    return "wrong_file"


def classify_failure_mode(row: dict[str, Any]) -> str:
    """Map a row dict (the per-case payload from ``_per_case_row``) to a
    PM-readable failure-mode category for cross-functional design discussion
    (#280, Jin's PR-#288 followup). Pure post-hoc classifier — no agent-side
    change. Each row falls into exactly one category:

      - ``correct``                   — agent got it right, no action
      - ``wrong_module``              — same-name disambiguation failed
      - ``wrong_intent``              — similar-intent miss; picked the wrong
                                        plausible symbol
      - ``cross_language_confusion``  — Python ↔ TypeScript runtime mistake
      - ``wrong_symbol_in_right_file``— right module, wrong symbol within
      - ``hallucinated_symbol``       — agent named a non-existent symbol;
                                        handler reject path caught it
      - ``span_mismatch``             — caller-supplied lines didn't overlap
                                        the resolved symbol; handler caught it
      - ``aborted_correctly``         — agent aborted on a case whose expected
                                        outcome IS abort (behavioral decisions
                                        — only meaningful once §B fixture lands)
      - ``aborted_incorrectly``       — agent aborted but the case has a
                                        bindable answer
      - ``eval_error``                — infra (API timeout / network); not an
                                        agent decision

    Categories drive the PM-actionable next steps documented in the plan.
    """
    outcome = row.get("outcome")
    if outcome == "correct":
        return "correct"
    if outcome == "eval_error":
        return "eval_error"

    if outcome == "aborted":
        # `expected_outcome` is reserved for §B (ungroundable behavioral cases);
        # default-`bind` rows treat any abort as incorrect for now.
        if row.get("expected_outcome") == "abort":
            return "aborted_correctly"
        return "aborted_incorrectly"

    error_msg = str(row.get("error_msg") or "")
    if "span mismatch" in error_msg.lower() and "#280" in error_msg:
        return "span_mismatch"
    if "not found" in error_msg.lower() and "#280" in error_msg:
        return "hallucinated_symbol"

    if outcome == "wrong_symbol":
        return "wrong_symbol_in_right_file"

    case_type = row.get("case_type")
    if outcome == "wrong_file":
        if case_type == "same_name_different_module":
            return "wrong_module"
        if case_type == "similar_intent":
            return "wrong_intent"
        if case_type == "cross_language":
            return "cross_language_confusion"

    # Catch-all: shouldn't happen given the outcome enum, but keep deterministic.
    return "uncategorized"


# Documented next-step per category — keep in sync with the plan's taxonomy
# table. Used by the renderer's "Failure modes" section.
FAILURE_MODE_NEXT_STEPS: dict[str, str] = {
    "correct": "—",
    "wrong_module": "tighten case-A decision text to name the module/scope",
    "wrong_intent": "improve the bind skill prompt's 'abort on weak evidence' rule",
    "cross_language_confusion": "make decision text mention runtime explicitly OR add language detection",
    "wrong_symbol_in_right_file": "agent reached the right module — sub-region disambiguation gap",
    "hallucinated_symbol": "handler failsafe is doing its job; LLM degraded — consider model bump",
    "span_mismatch": "handler failsafe caught hallucinated lines; LLM degraded — consider model bump",
    "aborted_correctly": "expected — behavioral decisions correctly route to PM review, not engineering",
    "aborted_incorrectly": "bind skill prompt is too cautious; loosen the abort rule",
    "eval_error": "infra (API timeout / network); not an agent issue",
    "uncategorized": "unexpected outcome — investigate manually",
}


# ── Report shape ────────────────────────────────────────────────────────────


def _per_case_row(case: GroundingCase, judgment: BindJudgment, outcome: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "case_type": case.case_type,
        "intended_file": case.intended_file,
        "intended_symbol": case.intended_symbol,
        "bound_file": judgment.bound_file,
        "bound_symbol": judgment.bound_symbol,
        "outcome": outcome,
        "aborted": judgment.aborted,
        "abort_reason": judgment.abort_reason,
        "reasoning": judgment.reasoning,
        "turns": judgment.turns,
        "tokens_in": judgment.tokens_in,
        "tokens_out": judgment.tokens_out,
    }
    row["failure_mode"] = classify_failure_mode(row)
    return row


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    by_outcome: dict[str, int] = defaultdict(int)
    by_type_total: dict[str, int] = defaultdict(int)
    by_type_correct: dict[str, int] = defaultdict(int)
    tokens_in = 0
    tokens_out = 0
    turns_sum = 0

    for r in rows:
        by_outcome[r["outcome"]] += 1
        by_type_total[r["case_type"]] += 1
        if r["outcome"] == "correct":
            by_type_correct[r["case_type"]] += 1
        tokens_in += r["tokens_in"]
        tokens_out += r["tokens_out"]
        turns_sum += r["turns"]

    correct = by_outcome["correct"]
    wrong_symbol = by_outcome["wrong_symbol"]
    wrong_file = by_outcome["wrong_file"]
    aborted = by_outcome["aborted"]

    submitted = correct + wrong_symbol + wrong_file
    precision = correct / submitted if submitted > 0 else 0.0
    recall = correct / total if total > 0 else 0.0
    abort_rate = aborted / total if total > 0 else 0.0

    per_type = {
        t: {
            "total": by_type_total[t],
            "correct": by_type_correct[t],
            "recall": (by_type_correct[t] / by_type_total[t]) if by_type_total[t] else 0.0,
        }
        for t in sorted(by_type_total)
    }

    return {
        "total_cases": total,
        "outcomes": dict(by_outcome),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "abort_rate": round(abort_rate, 4),
        "per_case_type": per_type,
        "tokens_in_total": tokens_in,
        "tokens_out_total": tokens_out,
        "avg_turns": round(turns_sum / total, 2) if total else 0.0,
    }


# ── Runner ──────────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cases: list[GroundingCase] = list(ALL_CASES)
    if args.case_filter:
        cases = cases_by_type(args.case_filter)
        if not cases:
            print(f"no cases match --case-filter {args.case_filter!r}", file=sys.stderr)
            return {}, 2
    if args.case_id:
        cases = [c for c in cases if c.case_id == args.case_id]
        if not cases:
            print(f"no case matches --case-id {args.case_id!r}", file=sys.stderr)
            return {}, 2

    rows: list[dict[str, Any]] = []
    for case in cases:
        if args.skip_missing_fixtures and not fixture_exists(
            case_id=case.case_id,
            decision_description=case.description,
            repo_root=FIXTURE_REPO,
            model=args.model,
        ):
            if args.verbose:
                print(f"SKIP {case.case_id}: no cached fixture and --skip-missing-fixtures")
            continue
        try:
            judgment = run_bind_judgment(
                case_id=case.case_id,
                decision_description=case.description,
                repo_root=FIXTURE_REPO,
                model=args.model,
            )
        except Exception as exc:
            # Per-case failure (typically: API timeout / network — see retry
            # loop in _bind_judge._call_messages_api). Record as eval_error
            # outcome and continue; do NOT fail the whole run on one case.
            # The aggregate gate check below catches the case where so many
            # cases erred that recall fell below the gate.
            print(f"ERROR on {case.case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            error_row = {
                "case_id": case.case_id,
                "case_type": case.case_type,
                "intended_file": case.intended_file,
                "intended_symbol": case.intended_symbol,
                "bound_file": None,
                "bound_symbol": None,
                "outcome": "eval_error",
                "aborted": False,
                "abort_reason": None,
                "reasoning": "",
                "error_msg": f"{type(exc).__name__}: {exc}",
                "turns": 0,
                "tokens_in": 0,
                "tokens_out": 0,
            }
            error_row["failure_mode"] = classify_failure_mode(error_row)
            rows.append(error_row)
            continue

        outcome = _classify(case, judgment)
        row = _per_case_row(case, judgment, outcome)
        rows.append(row)
        if args.verbose:
            print(
                f"  {case.case_id:<35}  {outcome:<14}  "
                f"bound=({judgment.bound_file or '—'}::{judgment.bound_symbol or '—'})"
            )

    summary = _aggregate(rows)
    summary["generator_version"] = GENERATOR_VERSION
    summary["model"] = args.model or "default"
    summary["gate_mode"] = args.gate_mode

    # Gate enforcement
    breaches: list[str] = []
    if summary["recall"] < args.min_recall:
        breaches.append(f"recall {summary['recall']:.3f} < {args.min_recall}")
    if summary["precision"] < args.min_precision:
        breaches.append(f"precision {summary['precision']:.3f} < {args.min_precision}")
    if summary["abort_rate"] > args.max_abort_rate:
        breaches.append(f"abort_rate {summary['abort_rate']:.3f} > {args.max_abort_rate}")
    summary["gate_breaches"] = breaches

    # Catastrophic floor (#537): hard-fails CI in any mode — but ONLY on a
    # genuine grounding collapse, never on an eval that couldn't run. A high
    # eval_error rate (e.g. missing API key / network on a CI re-run) zeroes
    # recall without any grounding signal; that is inconclusive, not
    # catastrophic, so we abstain and warn. A real collapse shows as low recall
    # with the eval actually running (a low error rate).
    total_cases = summary["total_cases"]
    eval_errors = sum(1 for r in rows if r.get("outcome") == "eval_error")
    error_rate = (eval_errors / total_cases) if total_cases else 0.0
    summary["eval_error_rate"] = error_rate
    inconclusive = is_inconclusive(eval_errors, total_cases)

    catastrophic: list[str] = []
    if not inconclusive and summary["recall"] < args.catastrophic_recall:
        catastrophic.append(
            f"recall {summary['recall']:.3f} < catastrophic floor {args.catastrophic_recall}"
        )
    summary["catastrophic_breaches"] = catastrophic

    report = {"summary": summary, "rows": rows}

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.output}")

    print()
    print(f"M2 grounding-recall eval — {summary['total_cases']} cases")
    print(f"  precision  : {summary['precision']:.3f}  (gate ≥ {args.min_precision})")
    print(f"  recall     : {summary['recall']:.3f}  (gate ≥ {args.min_recall})")
    print(f"  abort_rate : {summary['abort_rate']:.3f}  (gate ≤ {args.max_abort_rate})")
    print(f"  avg_turns  : {summary['avg_turns']}")
    print(f"  tokens     : {summary['tokens_in_total']} in / {summary['tokens_out_total']} out")
    if breaches:
        mode_note = "advisory (warn)" if args.gate_mode != "hard" else "gate"
        print(f"  ⚠ {mode_note} breaches: {'; '.join(breaches)}")
    else:
        print("  ✓ all gates pass")
    if catastrophic:
        print(f"  ✗ CATASTROPHIC floor breached (hard-fails any mode): {'; '.join(catastrophic)}")
    if inconclusive:
        print(
            f"  ⓘ inconclusive: {eval_errors}/{total_cases} cases errored "
            f"(rate {error_rate:.2f}) — catastrophic floor abstained "
            "(eval could not run; not a grounding collapse)"
        )

    return report, gate_exit_code(
        quality_breaches=breaches,
        catastrophic_breaches=catastrophic,
        gate_mode=args.gate_mode,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    p.add_argument(
        "--case-filter", choices=("same_name_different_module", "similar_intent", "cross_language")
    )
    p.add_argument("--case-id", help="run a single case by id (debug)")
    p.add_argument("--model", help="override BICAMERAL_GROUNDING_EVAL_MODEL")
    p.add_argument("--min-recall", type=float, default=0.80)
    p.add_argument("--min-precision", type=float, default=0.85)
    p.add_argument("--max-abort-rate", type=float, default=0.30)
    p.add_argument(
        "--catastrophic-recall",
        type=float,
        default=0.50,
        help="Hard floor (#537): recall below this hard-fails CI in any gate mode "
        "(a collapsed grounding path, not LLM variance). Default 0.50.",
    )
    p.add_argument("--gate-mode", choices=("warn", "hard"), default="warn")
    p.add_argument(
        "--skip-missing-fixtures",
        action="store_true",
        help="skip cases without a cached fixture (no API call) — useful for offline runs",
    )
    p.add_argument("-o", "--output", help="write JSON report to this path")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    _, exit_code = asyncio.run(run(args))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
