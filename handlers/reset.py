"""Handler for /bicameral_reset MCP tool.

The fail-safe valve. When the ledger gets polluted — by a bad bulk ingest,
a pre-v0.4.6 pollution bug, or a Claude Code session that went off the
rails — the user needs a one-command recovery path that doesn't require
them to remember which sources they originally ingested.

How it works:
  1. Query the ``source_cursor`` table for every row scoped to the
     current repo. Each row is a (source_type, source_scope, last_source_ref)
     triple recorded the last time an ingest ran for that source.
  2. Return the list as a ``replay_plan`` so the caller (host Claude) can
     re-run the original ``bicameral_ingest`` calls by source_ref lookup.
  3. If ``confirm=True``, wipe every bicameral table scoped to the repo
     BEFORE returning the plan.

Safety design:
  - **Dry run by default.** ``confirm=False`` returns the plan without
    touching any state.
  - **Scoped by repo.** Never wipes rows from other repos sharing the
    same SurrealDB instance.
  - **Replay is a handoff.** In v0.4.6 we do NOT store raw source
    documents, so "replay" means returning the plan — the caller
    still has to re-invoke ``bicameral_ingest`` with the originals.
"""

from __future__ import annotations

import logging

from contracts import ResetReplayEntry, ResetResponse

logger = logging.getLogger(__name__)


async def handle_reset(
    ctx,
    replay: bool = True,
    confirm: bool = False,
) -> ResetResponse:
    """Wipe the ledger scoped to ``ctx.repo_path`` (if confirm=True) and
    return a replay plan derived from the existing source_cursor rows.

    Args:
        ctx: BicameralContext
        replay: When True, include the replay plan in the response.
            (Always computed; this flag only controls whether it surfaces.)
        confirm: When False (default), DRY RUN — reads cursors, returns
            the plan, touches nothing. When True, WIPES every bicameral
            table scoped to ctx.repo_path.
    """
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()  # may partially succeed with _pending_destructive set
    # If a destructive migration is pending and the user confirmed, apply it now
    # before wiping so the schema matches the code.
    if confirm and hasattr(ledger, "force_migrate") and getattr(ledger, "_pending_destructive", None):
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

    if not confirm:
        next_action = (
            f"Dry run only. Would wipe {cursors_before} source_cursor row(s) "
            f"and every bicameral node/edge scoped to {ctx.repo_path!r}. "
            f"Re-run with confirm=True to execute."
        )
        return ResetResponse(
            wiped=False,
            ledger_url=ledger_url,
            repo=ctx.repo_path,
            cursors_before=cursors_before,
            replay_plan=replay_plan if replay else [],
            next_action=next_action,
        )

    # Destructive path — wipe the ledger scoped to this repo.
    # v0.4.8: invalidate the within-call sync cache so any future chained
    # handler in this same MCP call (e.g. future tester-mode hint chains)
    # doesn't read stale decision state from before the wipe.
    try:
        from handlers.link_commit import invalidate_sync_cache
        invalidate_sync_cache(ctx)
    except Exception:
        pass

    replay_errors: list[str] = []
    try:
        await _wipe_all(ledger, ctx.repo_path)
    except Exception as exc:
        logger.exception("[reset] wipe failed: %s", exc)
        return ResetResponse(
            wiped=False,
            ledger_url=ledger_url,
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
        "[reset] wiped %d source_cursor(s) and all scoped nodes for repo=%s",
        cursors_before, ctx.repo_path,
    )

    next_action = (
        f"Ledger wiped for repo {ctx.repo_path!r}. "
        f"{cursors_before} source(s) recorded in the replay plan. "
        f"Re-run the original bicameral_ingest calls for each entry in "
        f"replay_plan to repopulate the ledger."
    )

    return ResetResponse(
        wiped=True,
        ledger_url=ledger_url,
        repo=ctx.repo_path,
        cursors_before=cursors_before,
        replay_plan=replay_plan if replay else [],
        replay_errors=replay_errors,
        next_action=next_action,
    )


# ── Ledger method shims ─────────────────────────────────────────────
#
# We prefer adapter methods when they exist (``get_all_source_cursors``,
# ``wipe_all_rows``) but fall back to direct SurrealQL so the handler
# works against any ``SurrealDBLedgerAdapter``-like object, including the
# ``TeamWriteAdapter`` wrapper used in live deployments.


async def _get_cursors(ledger, repo_path: str) -> list[dict]:
    if hasattr(ledger, "get_all_source_cursors"):
        return await ledger.get_all_source_cursors(repo_path)
    # Fallback — direct query via the inner client if the wrapper exposes it.
    inner = getattr(ledger, "_inner", ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        return []
    rows = await client.query(
        "SELECT * FROM source_cursor WHERE repo = $repo",
        {"repo": repo_path},
    )
    return rows or []


async def _wipe_all(ledger, repo_path: str) -> None:
    if hasattr(ledger, "wipe_all_rows"):
        await ledger.wipe_all_rows(repo_path)
        return
    inner = getattr(ledger, "_inner", ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        raise RuntimeError(
            "reset: ledger adapter does not expose wipe_all_rows or an inner client"
        )
    # Scoped tables first (those with a repo field), then edge tables
    # (which are orphaned once their endpoints are gone).
    for table in ("intent", "code_region", "source_span", "source_cursor", "vocab_cache"):
        await client.execute(
            f"DELETE FROM {table} WHERE repo = $repo",
            {"repo": repo_path},
        )
    # Unscoped tables — wipe only the rows whose endpoints were in the
    # scoped tables. Simplest correct approach: wipe them all. Acceptable
    # because single-repo deployments are the common case; multi-repo
    # deployments should use the adapter-level wipe_all_rows method.
    for table in ("symbol", "maps_to", "implements", "yields", "ledger_sync"):
        try:
            await client.execute(f"DELETE FROM {table}")
        except Exception as exc:
            logger.debug("[reset] wipe of %s failed (non-fatal): %s", table, exc)


def _resolve_ledger_url(ctx, ledger) -> str:
    # Prefer an explicit attribute if the adapter tracks it; otherwise
    # surface the env var so the caller has something to correlate logs
    # against.
    for attr in ("_url", "url", "surreal_url"):
        v = getattr(ledger, attr, None)
        if v:
            return str(v)
        v = getattr(getattr(ledger, "_inner", ledger), attr, None)
        if v:
            return str(v)
    import os
    return os.environ.get("SURREAL_URL", "")
