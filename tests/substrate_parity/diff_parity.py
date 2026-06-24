#!/usr/bin/env python3
"""Diff substrate parity logs and gate on capture-critical hook coverage.

Consumes the JSONL logs produced by ``parity_hook.py`` under each substrate
(interactive / headless `claude -p` / cron) and answers the #611 question: does
the same workload fire the same hook events everywhere?

Gate semantics (the part #148 portability hangs on):
- ``CAPTURE_CRITICAL`` events are the ones #610 capture leans on. If any of them
  is observed in the *reference* substrate but missing from another tested
  substrate, that substrate cannot carry #610 capture — the tool exits non-zero.
- ``--strict`` additionally fails on ANY event-coverage divergence, not just the
  capture-critical ones.

This converts "we assume SessionEnd fires under `claude -p`" into a checked fact
before any portability claim is made. Per GH #611.
"""

from __future__ import annotations

import argparse
import json
import sys

# Events #610 Phase-1 capture depends on. SessionEnd drains the transcript
# queue; PreCompact can drop assistant text before that drain; PostToolUse /
# Stop / SubagentStop bound the turn. If any of these does not fire in a
# substrate, capture silently loses data there.
CAPTURE_CRITICAL = ("SessionEnd", "PreCompact", "Stop", "PostToolUse")

# The reference substrate every other substrate is compared against.
DEFAULT_REFERENCE = "interactive"


def _load_log(path: str) -> dict[str, int]:
    """Return {event_name: count} for one substrate's JSONL log."""
    counts: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                event = rec.get("event", "unknown")
                counts[event] = counts.get(event, 0) + 1
    except OSError as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
    return counts


def _parse_log_args(raw: list[str]) -> dict[str, str]:
    """Parse ``substrate=path`` / ``substrate:path`` pairs into a mapping."""
    out: dict[str, str] = {}
    for item in raw:
        sep = "=" if "=" in item else (":" if ":" in item else None)
        if sep is None:
            raise SystemExit(f"--log expects substrate=path, got: {item!r}")
        substrate, path = item.split(sep, 1)
        out[substrate.strip()] = path.strip()
    return out


def build_report(logs: dict[str, dict[str, int]], reference: str) -> dict:
    """Compute the event x substrate matrix plus divergence findings."""
    substrates = sorted(logs)
    all_events = sorted({e for counts in logs.values() for e in counts})

    matrix = {event: {sub: logs[sub].get(event, 0) for sub in substrates} for event in all_events}

    ref_events = set(logs.get(reference, {}))
    missing_critical: list[dict] = []
    divergences: list[dict] = []

    for sub in substrates:
        if sub == reference:
            continue
        sub_events = set(logs[sub])
        for event in sorted(ref_events - sub_events):
            finding = {"event": event, "present_in": reference, "missing_in": sub}
            divergences.append(finding)
            if event in CAPTURE_CRITICAL:
                missing_critical.append(finding)

    return {
        "reference": reference,
        "substrates": substrates,
        "events": all_events,
        "matrix": matrix,
        "divergences": divergences,
        "missing_critical": missing_critical,
        "capture_critical": list(CAPTURE_CRITICAL),
    }


def render(report: dict) -> str:
    subs = report["substrates"]
    width = max([len("event")] + [len(e) for e in report["events"]] + [4])
    lines = ["", "Substrate parity matrix (event firing counts)", ""]
    header = "  " + "event".ljust(width) + "  " + "  ".join(s.rjust(10) for s in subs)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for event in report["events"]:
        crit = " *" if event in report["capture_critical"] else "  "
        row = (
            "  "
            + event.ljust(width)
            + "  "
            + "  ".join(str(report["matrix"][event][s]).rjust(10) for s in subs)
        )
        lines.append(row + crit)
    lines.append("")
    lines.append("  (* = capture-critical for #610)")
    lines.append(f"  reference substrate: {report['reference']}")
    if report["missing_critical"]:
        lines.append("")
        lines.append("  CAPTURE-CRITICAL GAPS (portability-blocking):")
        for f in report["missing_critical"]:
            lines.append(
                f"    - {f['event']} fires in {f['present_in']} but NOT in {f['missing_in']}"
            )
    elif report["divergences"]:
        lines.append("")
        lines.append("  Non-critical divergences:")
        for f in report["divergences"]:
            lines.append(
                f"    - {f['event']} fires in {f['present_in']} but NOT in {f['missing_in']}"
            )
    else:
        lines.append("")
        lines.append("  No divergences: all substrates fired the reference's event set.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diff substrate parity logs (GH #611).")
    ap.add_argument(
        "--log",
        action="append",
        default=[],
        metavar="SUBSTRATE=PATH",
        help="a substrate label and its parity JSONL log (repeatable)",
    )
    ap.add_argument("--reference", default=DEFAULT_REFERENCE, help="reference substrate")
    ap.add_argument(
        "--strict", action="store_true", help="fail on ANY divergence, not just capture-critical"
    )
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    if len(args.log) < 2:
        ap.error("need at least two --log substrate=path entries to diff")

    paths = _parse_log_args(args.log)
    logs = {sub: _load_log(path) for sub, path in paths.items()}
    if args.reference not in logs:
        ap.error(f"reference {args.reference!r} not among substrates {sorted(logs)}")

    report = build_report(logs, args.reference)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))

    if report["missing_critical"]:
        return 2
    if args.strict and report["divergences"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
