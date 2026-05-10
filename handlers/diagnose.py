"""Handler for /bicameral_diagnose MCP tool.

Read-only structural diagnostic. Mirrors the ``bicameral-mcp diagnose``
CLI but exposed as an MCP tool so agents can call it from any
tool-error envelope.

Critical property: this handler MUST work even when ``adapter.connect()``
crashes inside ``init_schema``/``migrate``. It opens a raw
``LedgerClient`` directly and calls ``gather_diagnosis_raw``, which
reads tables defensively (missing tables and SELECT failures are
treated as "unavailable" not propagated as exceptions). That's the
whole point — when the ledger is broken, the agent needs a tool that
still answers "what's wrong?"

Repair is a deliberate CLI operation (``bicameral-mcp diagnose
--repair``), never an in-session agent action. This tool is
intentionally read-only.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from contracts import DiagnoseResponse

RecoveryPath = Literal["clean", "fixable", "reset_rebuild", "reset_destructive"]

logger = logging.getLogger(__name__)


def _resolve_ledger_url(ctx) -> str:
    """Pick the ledger URL from the same source ``adapter.connect()`` does.

    Order: ``ctx.ledger._url`` (if connected), then ``SURREAL_URL`` env,
    then the embedded default. Mirrors ``SurrealDBLedgerAdapter.__init__``.
    """
    ledger = getattr(ctx, "ledger", None)
    inner = getattr(ledger, "_inner", ledger) if ledger is not None else None
    for obj in (ledger, inner):
        if obj is None:
            continue
        url = getattr(obj, "_url", None)
        if url:
            return str(url)

    from ledger.adapter import _default_db_url

    return os.environ.get("SURREAL_URL", _default_db_url())


async def handle_diagnose(ctx) -> DiagnoseResponse:
    """Probe the ledger read-only and return a structural diagnosis.

    Always opens its own raw ``LedgerClient`` rather than reusing the
    adapter's client — the adapter may be in a partially-connected
    state, and going through ``adapter._ensure_connected`` would re-run
    the migration that's already failing.
    """
    from cli._diagnose_gather import gather_diagnosis_raw
    from ledger.client import LedgerClient

    ledger_url = _resolve_ledger_url(ctx)

    client = LedgerClient(url=ledger_url)
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001 — operator needs the failure context
        logger.warning("[diagnose] raw connect failed for %s: %s", ledger_url, exc)
        return DiagnoseResponse(
            ledger_url=ledger_url,
            connect_error=f"{type(exc).__name__}: {exc}",
            recovery_path="reset_destructive",
            diagnosis=None,
            next_action=(
                "Raw client could not connect to the ledger URL. The DB file "
                "may be missing, locked, or unreadable. Inspect the path, then "
                "run `bicameral-mcp reset --confirm` to reinitialise. If the "
                "directory contains team-mode events under .bicameral/events/, "
                "use `bicameral_reset(replay_from_events=True, confirm=True)` "
                "to rebuild from the substrate after wipe."
            ),
        )

    try:
        diagnosis = await gather_diagnosis_raw(client, ledger_url)
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001 — close is best-effort
            pass

    recovery_path, next_action = _classify_recovery(diagnosis)
    return DiagnoseResponse(
        ledger_url=ledger_url,
        connect_error="",
        recovery_path=recovery_path,
        diagnosis=diagnosis.__dict__,  # Diagnosis is a frozen dataclass
        next_action=next_action,
    )


def _classify_recovery(diagnosis) -> tuple[RecoveryPath, str]:
    """Translate the raw diagnosis into one of four recovery paths.

    - ``clean``: schema_recorded == schema_expected and no flagged drift.
    - ``fixable``: schema_recorded < schema_expected — `migrate` will
      catch up on next normal connect.
    - ``reset_rebuild``: ledger is past the binary's schema OR the
      diagnose surfaces stale-row warnings; .bicameral/events/ is
      present, so reset+replay recovers without data loss.
    - ``reset_destructive``: same condition as reset_rebuild but no
      events on disk → user must accept data loss.

    The classification is deliberately conservative — anything weird
    routes to the operator with a clear next_action, not an automated
    fix.
    """
    rec = diagnosis.schema_version_recorded
    exp = diagnosis.schema_version_expected
    has_events = _events_present(diagnosis.ledger_url)

    if rec is not None and rec > exp:
        path: RecoveryPath = "reset_rebuild" if has_events else "reset_destructive"
        return path, (
            f"Ledger schema v{rec} is newer than this binary (v{exp}). "
            f"Upgrade `bicameral-mcp` to a version that understands v{rec}, "
            f"or run `bicameral_reset(replay_from_events={has_events}, confirm=True)`."
        )

    if rec is not None and rec < exp:
        return "fixable", (
            f"Ledger schema v{rec} is behind binary v{exp}. The next normal "
            f"connect will run pending migrations. If connect is failing, "
            f"the cleanup migration may need a re-run — `bicameral-mcp diagnose --repair`."
        )

    if rec is None:
        return "clean", (
            "Schema version not yet recorded — likely a fresh install. "
            "Any tool call will initialise the ledger."
        )

    # rec == exp — confirm the table counts look sane
    table_counts = diagnosis.table_counts or {}
    if not table_counts:
        return "fixable", (
            "Schema version matches but no tables visible. "
            "Connect may have stopped mid-init; re-run a tool call to retry."
        )

    return "clean", (f"Ledger is at expected schema v{exp}. No remediation needed.")


def _events_present(ledger_url: str) -> bool:
    """Best-effort check for ``.bicameral/events/*.jsonl``."""
    if not ledger_url.startswith("surrealkv://"):
        return False
    from pathlib import Path

    db_path = Path(ledger_url.removeprefix("surrealkv://"))
    events_dir = db_path.parent / "events"
    if not events_dir.exists():
        return False
    return any(events_dir.glob("*.jsonl"))
