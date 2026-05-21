"""bicameral-mcp brief — the operator-facing Project Pulse command (#437 Phase 2).

Builds the shared :class:`~pulse.summary.ProjectPulseSummary` from the ledger
and renders it — either as concise plain text (``render_pulse_text``) or as
structured JSON (``--json``, ``summary.to_dict()``).

``brief`` is **read-only**. ``build_project_pulse`` issues only ``get_*``
ledger queries; this command adds no write. It is the CLI surface of #437's
"one shared backend object, rendered three ways" design — ``brief`` and
``sync-and-brief`` render the same object through the same renderer.

Filters:

* ``--since`` bounds the recency window — an ISO date, ``today`` /
  ``yesterday``, or ``Nd`` (N days). An unparseable value exits 2.
* ``--feature`` filters to decisions whose ``feature_hint`` matches.
* ``--recent-limit`` / ``--max-decisions`` caps the recently-learned list.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pulse import SinceParseError, build_project_pulse, render_pulse_text


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Emit the summary as structured JSON instead of plain text.",
    )
    subparser.add_argument(
        "--since",
        default=None,
        metavar="WHEN",
        help=(
            "Bound the recency window: an ISO date (2026-05-20), 'today', "
            "'yesterday', or 'Nd' (N days, e.g. 7d)."
        ),
    )
    subparser.add_argument(
        "--feature",
        default=None,
        metavar="NAME",
        help="Filter to decisions whose feature_hint matches NAME.",
    )
    subparser.add_argument(
        "--recent-limit",
        "--max-decisions",
        dest="recent_limit",
        type=int,
        default=8,
        metavar="N",
        help="Cap on the recently-learned list (default: 8).",
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 on success, 2 on an unparseable ``--since`` value, 1 on any
    other unexpected failure (e.g. a total ledger failure).
    """
    try:
        return asyncio.run(_run(args))
    except SinceParseError as exc:
        print(f"[brief] invalid --since: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — operator-visible CLI; never re-raise
        print(f"[brief] unexpected error: {exc}", file=sys.stderr)
        return 1


async def _run(args: argparse.Namespace) -> int:
    """Build the Project Pulse summary and render it to stdout."""
    from context import BicameralContext

    ctx = BicameralContext.from_env()
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    summary = await build_project_pulse(
        ledger,
        recent_limit=max(args.recent_limit, 0),
        since=args.since,
        feature=args.feature,
    )

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        print(render_pulse_text(summary))
    return 0
