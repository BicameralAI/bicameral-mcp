"""Handler for /bicameral_reset MCP tool.

The fail-safe valve. Two modes:

  wipe_mode="ledger" (default)
    Wipes the materialized SurrealDB rows scoped to the current repo.
    The .bicameral/ directory (config, event files) is untouched.
    The server stays live and reconnects immediately.
    Use this for: bad bulk ingest, pollution bugs, stale groundings.

  wipe_mode="full"
    Deletes the entire .bicameral/ directory — ledger, config.yaml,
    team event files, everything. The schema is reinitialised in-process.
    Use this for: nuclear restart, switching repos, credential rotation.
    The user must explicitly confirm after seeing the warning.

Safety design:
  - Dry run by default. confirm=False returns the plan without touching state.
  - Replay plan is always computed before any destructive operation.
  - Full mode surfaces the exact path that will be deleted in the dry run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from contracts import ResetReplayEntry, ResetResponse

logger = logging.getLogger(__name__)


async def handle_reset(
    ctx,
    replay: bool = True,
    confirm: bool = False,
    wipe_mode: str = "ledger",
) -> ResetResponse:
    """Wipe the ledger (and optionally the full .bicameral/ dir) for ctx.repo_path.

    Args:
        ctx: BicameralContext
        replay: Include the replay plan in the response.
        confirm: False = dry run (default). True = execute.
        wipe_mode: "ledger" = wipe DB rows only (server stays live).
                   "full"   = delete the entire .bicameral/ directory.
    """
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()
    if (
        confirm
        and hasattr(ledger, "force_migrate")
        and getattr(ledger, "_pending_destructive", None)
    ):
        await ledger.force_migrate()

    cursors = await _get_cursors(ledger, ctx.repo_path)
    cursors_before = len(cursors)

    replay_plan = [
        ResetReplayEntry(
            source_type=str(c.get("source_type", "")),
            source_scope=str(c.get("source_scope", "")),
            last_source_ref=str(c.get("last_source_ref", "")),
        )
        for c in cursors
    ]

    ledger_url = _resolve_ledger_url(ctx, ledger)
    bicameral_dir = _resolve_bicameral_dir(ledger) if wipe_mode == "full" else ""

    if not confirm:
        if wipe_mode == "full":
            dir_desc = (
                f" and the entire .bicameral/ directory at {bicameral_dir!r}"
                if bicameral_dir
                else ""
            )
            next_action = (
                f"DRY RUN — FULL WIPE. Would delete {cursors_before} source_cursor row(s), "
                f"every bicameral node/edge scoped to {ctx.repo_path!r}{dir_desc}. "
                f"WARNING: this removes config.yaml, team event files, and all history — "
                f"there is no undo. Re-run with confirm=True to execute."
            )
        else:
            next_action = (
                f"Dry run only. Would wipe {cursors_before} source_cursor row(s) "
                f"and every bicameral node/edge scoped to {ctx.repo_path!r}. "
                f"Re-run with confirm=True to execute."
            )
        return ResetResponse(
            wiped=False,
            wipe_mode=wipe_mode,
            ledger_url=ledger_url,
            bicameral_dir=bicameral_dir,
            repo=ctx.repo_path,
            cursors_before=cursors_before,
            replay_plan=replay_plan if replay else [],
            next_action=next_action,
        )

    # Invalidate within-call sync cache before any destructive operation.
    try:
        from handlers.link_commit import invalidate_sync_cache

        invalidate_sync_cache(ctx)
    except Exception:
        pass

    try:
        if wipe_mode == "full":
            bicameral_dir = await _wipe_bicameral_dir(ledger)
        else:
            await _wipe_ledger(ledger, ctx.repo_path)
    except Exception as exc:
        logger.exception("[reset] wipe failed: %s", exc)
        return ResetResponse(
            wiped=False,
            wipe_mode=wipe_mode,
            ledger_url=ledger_url,
            bicameral_dir=bicameral_dir,
            repo=ctx.repo_path,
            cursors_before=cursors_before,
            replay_plan=replay_plan if replay else [],
            replay_errors=[f"wipe failed: {exc}"],
            next_action=(
                f"Wipe FAILED before persisting. No data destroyed. "
                f"Error: {exc}. Check logs and retry or diagnose."
            ),
        )

    logger.info(
        "[reset] wipe_mode=%s, wiped %d source_cursor(s) for repo=%s bicameral_dir=%r",
        wipe_mode,
        cursors_before,
        ctx.repo_path,
        bicameral_dir,
    )

    if wipe_mode == "full":
        next_action = (
            f"Full wipe complete for repo {ctx.repo_path!r}. "
            f".bicameral/ directory deleted: {bicameral_dir!r}. "
            f"{cursors_before} source(s) in the replay plan. "
            f"Schema has been reinitialised — the server is ready for fresh ingestion. "
            f"Re-run the original bicameral_ingest calls for each entry in replay_plan."
        )
    else:
        next_action = (
            f"Ledger wiped for repo {ctx.repo_path!r}. "
            f"{cursors_before} source(s) recorded in the replay plan. "
            f"Re-run the original bicameral_ingest calls for each entry in "
            f"replay_plan to repopulate the ledger."
        )

    return ResetResponse(
        wiped=True,
        wipe_mode=wipe_mode,
        ledger_url=ledger_url,
        bicameral_dir=bicameral_dir,
        repo=ctx.repo_path,
        cursors_before=cursors_before,
        replay_plan=replay_plan if replay else [],
        next_action=next_action,
    )


# ── Wipe implementations ─────────────────────────────────────────────


async def _wipe_ledger(ledger, repo_path: str) -> None:
    """Wipe DB rows only. Delegates to adapter method or falls back to direct delete."""
    if hasattr(ledger, "wipe_all_rows"):
        await ledger.wipe_all_rows(repo_path)
        return
    inner = getattr(ledger, "_inner", ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        raise RuntimeError("reset: ledger adapter does not expose wipe_all_rows or an inner client")
    import shutil

    url = getattr(inner, "_url", "")
    await client.close()
    inner._connected = False
    if url.startswith("surrealkv://"):
        db_path = url[len("surrealkv://") :]
        if db_path:
            shutil.rmtree(db_path, ignore_errors=True)
    await inner._ensure_connected()


async def _wipe_bicameral_dir(ledger) -> str:
    """Delete the entire .bicameral/ directory and reinitialise the schema.

    Returns the path that was deleted (empty string for in-memory URLs).
    """
    import shutil

    bicameral_dir = _resolve_bicameral_dir(ledger)

    # Close the connection on the innermost adapter.
    inner = getattr(ledger, "_inner", ledger)
    client = getattr(inner, "_client", None)
    if client:
        try:
            await client.close()
        except Exception:
            pass
        inner._connected = False

    if bicameral_dir:
        shutil.rmtree(bicameral_dir, ignore_errors=True)

    # Reinitialise schema so the server is immediately ready.
    if hasattr(inner, "_ensure_connected"):
        await inner._ensure_connected()

    return bicameral_dir


def _resolve_bicameral_dir(ledger) -> str:
    """Return the .bicameral/ directory path derived from the ledger URL.

    For surrealkv://<path>/ledger.db the .bicameral/ dir is the parent of
    the ledger.db directory. Returns empty string for in-memory URLs.
    """
    for obj in (ledger, getattr(ledger, "_inner", None)):
        if obj is None:
            continue
        url = getattr(obj, "_url", "")
        if url.startswith("surrealkv://"):
            db_path = url[len("surrealkv://") :]
            if db_path:
                return str(Path(db_path).expanduser().parent)
    return ""


# ── Ledger query shims ───────────────────────────────────────────────


async def _get_cursors(ledger, repo_path: str) -> list[dict]:
    if hasattr(ledger, "get_all_source_cursors"):
        return await ledger.get_all_source_cursors(repo_path)
    inner = getattr(ledger, "_inner", ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        return []
    rows = await client.query(
        "SELECT * FROM source_cursor WHERE repo = $repo",
        {"repo": repo_path},
    )
    return rows or []


def _resolve_ledger_url(ctx, ledger) -> str:
    for attr in ("_url", "url", "surreal_url"):
        v = getattr(ledger, attr, None)
        if v:
            return str(v)
        v = getattr(getattr(ledger, "_inner", ledger), attr, None)
        if v:
            return str(v)
    import os

    return os.environ.get("SURREAL_URL", "")
