"""Handler for /bicameral_usage_summary MCP tool (issue #42).

Aggregate operational readout — converts raw ledger state into actionable
percentages over a configurable window. Privacy-preserving: returns only
counts and floats. No event rows, no session IDs, no user content.

Pairs with local_counters.py (#39) for tool-call counts; pulls
decision-state metrics directly from the SurrealDB ledger.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from local_counters import read_counters

logger = logging.getLogger(__name__)


async def handle_usage_summary(ctx, days: int = 7) -> dict:
    """Aggregate usage stats over the last `days` days.

    Returns the schema specified in #42:
        period_days, ingest_calls, bind_calls_total, decisions_ingested,
        decisions_ungrounded, decisions_pending, decisions_reflected,
        decisions_drifted, reflected_pct, drift_pct, cosmetic_drift_pct,
        error_rate.
    """
    period_days = max(0, int(days))
    base = {
        "period_days": period_days,
        "ingest_calls": 0,
        "bind_calls_total": 0,
        "decisions_ingested": 0,
        "decisions_ungrounded": 0,
        "decisions_pending": 0,
        "decisions_reflected": 0,
        "decisions_drifted": 0,
        "reflected_pct": 0.0,
        "drift_pct": 0.0,
        "cosmetic_drift_pct": 0.0,
        "error_rate": 0.0,
    }

    # ── Tool-call counts (local-only, from #39's counters.jsonl) ──
    counters = read_counters()
    base["ingest_calls"] = int(counters.get("bicameral-ingest", 0))
    base["bind_calls_total"] = int(counters.get("bicameral-bind", 0))

    # ── Decision state counts (from ledger) ──
    if period_days == 0:
        return base

    try:
        ledger = ctx.ledger
        cutoff = (datetime.now(UTC) - timedelta(days=period_days)).isoformat()
        client = getattr(getattr(ledger, "_inner", ledger), "_client", None)
        if client is None:
            return base

        rows = await client.query(
            "SELECT status, count() AS n FROM decision "
            f"WHERE created_at > <datetime>'{cutoff}' GROUP BY status"
        )
        status_counts: dict[str, int] = {}
        for r in rows or []:
            s = r.get("status")
            n = int(r.get("n", 0))
            if isinstance(s, str):
                status_counts[s] = n

        base["decisions_ungrounded"] = status_counts.get("ungrounded", 0)
        base["decisions_pending"] = status_counts.get("pending", 0)
        base["decisions_reflected"] = status_counts.get("reflected", 0)
        base["decisions_drifted"] = status_counts.get("drifted", 0)
        base["decisions_ingested"] = sum(status_counts.values())

        grounded = base["decisions_reflected"] + base["decisions_drifted"]
        if grounded > 0:
            base["reflected_pct"] = round(base["decisions_reflected"] / grounded, 4)
            base["drift_pct"] = round(base["decisions_drifted"] / grounded, 4)

        # Cosmetic drift: count compliance_check verdicts of cosmetic_autopass
        # over total drift verdicts in the window.
        try:
            cc_rows = await client.query(
                "SELECT verdict, count() AS n FROM compliance_check "
                f"WHERE checked_at > <datetime>'{cutoff}' "
                "AND verdict IN ['drifted', 'cosmetic_autopass'] GROUP BY verdict"
            )
            cc_counts = {r.get("verdict"): int(r.get("n", 0)) for r in (cc_rows or [])}
            cosmetic = cc_counts.get("cosmetic_autopass", 0)
            drift_total = cosmetic + cc_counts.get("drifted", 0)
            if drift_total > 0:
                base["cosmetic_drift_pct"] = round(cosmetic / drift_total, 4)
        except Exception as exc:
            logger.debug("[usage_summary] cosmetic_drift query failed: %s", exc)
    except Exception as exc:
        logger.debug("[usage_summary] aggregate query failed: %s", exc)

    return base
