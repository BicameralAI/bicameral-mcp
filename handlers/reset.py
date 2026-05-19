"""Handler for /bicameral_reset MCP tool.

The fail-safe valve. Two wipe modes plus an optional replay path.

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

  replay_from_events=True (#296 Layer E)
    After a successful ``wipe_mode="ledger"`` wipe, the watermark is
    reset and ``EventMaterializer.replay_new_events`` rebuilds the
    local DB from ``.bicameral/events/<author>.jsonl``. The event log
    is the canonical record (committed to git in team mode) — replay
    is recovery, not destruction. Combined with ``wipe_mode="full"``
    is rejected because full-wipe deletes the very events we'd replay.

Safety design:
  - Dry run by default. confirm=False returns the plan without touching state.
  - Replay plan is always computed before any destructive operation.
  - Full mode surfaces the exact path that will be deleted in the dry run.
  - replay_from_events surfaces the on-disk event count in the dry run.
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
    replay_from_events: bool = False,
) -> ResetResponse:
    """Wipe the ledger (and optionally the full .bicameral/ dir) for ctx.repo_path.

    Args:
        ctx: BicameralContext
        replay: Include the replay plan in the response.
        confirm: False = dry run (default). True = execute.
        wipe_mode: "ledger" = wipe DB rows only (server stays live).
                   "full"   = delete the entire .bicameral/ directory.
        replay_from_events: After wipe (only when wipe_mode="ledger"),
            replay every event in .bicameral/events/*.jsonl through the
            ingest path to recover decisions deterministically. Mutually
            exclusive with wipe_mode="full" — that mode deletes the
            substrate we'd replay from.
    """
    if replay_from_events and wipe_mode == "full":
        return ResetResponse(
            wiped=False,
            wipe_mode=wipe_mode,
            ledger_url=_resolve_ledger_url(ctx, ctx.ledger),
            bicameral_dir="",
            repo=ctx.repo_path,
            cursors_before=0,
            replay_plan=[],
            replay_errors=[
                "replay_from_events is incompatible with wipe_mode='full' "
                "(full wipe deletes .bicameral/events which is the replay source)"
            ],
            next_action=(
                "Pick one: wipe_mode='ledger' + replay_from_events=True for "
                "recovery from a corrupted ledger, OR wipe_mode='full' for a "
                "nuclear restart that intentionally drops everything."
            ),
        )

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
    bicameral_dir = _resolve_bicameral_dir(ledger, ctx.repo_path) if wipe_mode == "full" else ""

    events_on_disk = _count_events_on_disk(ctx.repo_path) if replay_from_events else 0

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
        elif replay_from_events:
            next_action = (
                f"DRY RUN — LEDGER WIPE + REBUILD. Would wipe {cursors_before} "
                f"source_cursor row(s), every bicameral node/edge scoped to "
                f"{ctx.repo_path!r}, then reset the watermark and replay "
                f"{events_on_disk} event(s) from .bicameral/events/*.jsonl through "
                f"the ingest path. The event log is the canonical record — replay "
                f"recovers decisions deterministically. "
                f"Re-run with confirm=True to execute."
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
            events_replayed=events_on_disk if replay_from_events else 0,
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
            bicameral_dir = await _wipe_bicameral_dir(ledger, ctx.repo_path)
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

    events_replayed = 0
    replay_errors: list[str] = []
    if replay_from_events and wipe_mode != "full":
        try:
            events_replayed = await _replay_events_into_ledger(ctx.repo_path, ledger)
        except Exception as exc:  # noqa: BLE001 — surface failure but keep wipe done
            logger.exception("[reset] replay_from_events failed: %s", exc)
            replay_errors.append(f"replay_from_events failed: {exc}")

    if wipe_mode == "full":
        next_action = (
            f"Full wipe complete for repo {ctx.repo_path!r}. "
            f".bicameral/ directory deleted: {bicameral_dir!r}. "
            f"{cursors_before} source(s) in the replay plan. "
            f"Schema has been reinitialised — the server is ready for fresh ingestion. "
            f"Re-run the original bicameral_ingest calls for each entry in replay_plan."
        )
    elif replay_from_events:
        if replay_errors:
            next_action = (
                f"Ledger wiped for repo {ctx.repo_path!r}. Replay FAILED — see "
                f"replay_errors. The wipe succeeded; the event substrate is intact. "
                f"Re-run with confirm=True after addressing the replay error, or "
                f"fall back to manual ingest using replay_plan."
            )
        else:
            next_action = (
                f"Ledger wiped and rebuilt from events for repo {ctx.repo_path!r}. "
                f"{events_replayed} event(s) replayed through the ingest path. "
                f"Verify with `bicameral_diagnose` or `bicameral_history`."
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
        replay_errors=replay_errors,
        events_replayed=events_replayed,
        next_action=next_action,
    )


# ── Wipe implementations ─────────────────────────────────────────────


def _resolve_events_dir(repo_path: str | None = None) -> Path | None:
    """Return the repo-scoped events directory, or None when no
    on-disk substrate exists.

    Events are the canonical SOURCE for the ledger and live in the
    REPO (committed to git pre-#373, pushed to the configured remote
    backend post-#373). They are NOT user-local state — that bucket
    is the locator's domain (ledger.db, code-graph, bm25, watermark,
    transcript queues, operator.yaml). Conflating the two was the
    root cause of the pre-#410 silent zero-replay regression: the
    old resolver inverted the locator-resolved ledger URL to derive
    the events dir, which only worked while ledger and events shared
    a parent. Once R4 moved the ledger out from under the repo, the
    inverse silently pointed at an empty path.

    Forward-resolution (mirrors the production read site in
    ``cli/sync_and_brief_cli.py``):
        1. ``BICAMERAL_DATA_PATH`` override → ``<data>/.bicameral/events``
           (test escape hatch).
        2. ``<repo_path>/.bicameral/events`` — the canonical
           committed/synced location.
    """
    import os as _os

    data_path = _os.environ.get("BICAMERAL_DATA_PATH")
    if data_path:
        events_dir = Path(data_path) / ".bicameral" / "events"
    elif repo_path:
        events_dir = Path(repo_path) / ".bicameral" / "events"
    else:
        events_dir = Path.cwd() / ".bicameral" / "events"
    return events_dir if events_dir.exists() else None


def _count_events_on_disk(repo_path: str | None = None) -> int:
    """Tally non-empty lines across every ``<author>.jsonl`` under events/.

    Best-effort: returns 0 when the directory is missing or any file
    read fails. Used only to surface a count in the dry-run; not
    load-bearing for the replay itself.
    """
    events_dir = _resolve_events_dir(repo_path)
    if events_dir is None:
        return 0
    total = 0
    for path in events_dir.glob("*.jsonl"):
        try:
            with open(path, "rb") as f:
                for line in f:
                    if line.strip():
                        total += 1
        except OSError:
            continue
    return total


async def _replay_events_into_ledger(repo_path: str | None, ledger) -> int:
    """Reset the materializer watermark and replay every event back
    through the ingest path. Returns the count of events the
    materializer applied.

    Raises ``FileNotFoundError`` when the events substrate cannot be
    located — caller surfaces it via ``replay_errors`` so a missing
    events dir produces a loud failure rather than a passing-looking
    zero-replay response (this was the actual symptom of the pre-fix
    resolver bug; #410).

    Uses the same ``EventMaterializer`` instance team mode uses, so
    replay-vs-live divergence is impossible by construction. Determinism
    is tracked separately under issue #296.
    """
    import os as _os

    inner = getattr(ledger, "_inner", ledger)
    events_dir = _resolve_events_dir(repo_path)
    if events_dir is None:
        expected = (
            Path(repo_path) / ".bicameral" / "events" if repo_path else "<repo>/.bicameral/events"
        )
        raise FileNotFoundError(
            f"events dir not found at {expected!s}. The replay source is "
            "repo-local: pre-#373 these JSONLs are committed to git, "
            "post-#373 they are pulled from the configured remote backend "
            "via `bicameral-mcp sync-and-brief`. Run sync-and-brief first "
            "if you're on a fresh clone with no local cache yet."
        )

    # Reset the watermark so every event replays from offset 0. The
    # materializer's offset map is `{author: byte_offset}`; writing an
    # empty object is the canonical "start over" signal. Mirrored seams:
    # tests using BICAMERAL_DATA_PATH route the watermark to a sibling
    # of events under .bicameral/local/ (no git context required);
    # production routes through the locator's project dir.
    data_path = _os.environ.get("BICAMERAL_DATA_PATH")
    watermark_override: Path | None = None
    if data_path:
        watermark_override = Path(data_path) / ".bicameral" / "local" / "watermark"
        watermark_override.parent.mkdir(parents=True, exist_ok=True)
        watermark_override.write_text("{}\n", encoding="utf-8")
    else:
        from ledger_locator import resolve_watermark_path

        resolve_watermark_path(Path(repo_path) if repo_path else None).write_text(
            "{}\n", encoding="utf-8"
        )

    from events.materializer import EventMaterializer

    materializer = EventMaterializer(
        events_dir,
        repo_path=Path(repo_path) if repo_path else None,
        watermark_override=watermark_override,
    )
    return await materializer.replay_new_events(inner)


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


async def _wipe_bicameral_dir(ledger, repo_path: str | None = None) -> str:
    """Delete the entire .bicameral/ directory and reinitialise the schema.

    Returns the path that was deleted (empty string for in-memory URLs).
    """
    import shutil

    bicameral_dir = _resolve_bicameral_dir(ledger, repo_path)

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


def _resolve_bicameral_dir(ledger, repo_path: str | None = None) -> str:
    """Return the on-disk dir to delete in a full-wipe, or "" for in-memory.

    Resolution order (#410 / #368):
        1. ``BICAMERAL_DATA_PATH`` override (tests / pre-#368 installs)
           → ``<data>/.bicameral`` symmetrically with
           ``adapters/ledger.py:_real_ledger_instance``.
        2. When no explicit ``SURREAL_URL`` is set, the URL came from
           the locator default → return the locator's project dir.
        3. Otherwise derive from the adapter URL — the operator
           pointed ``SURREAL_URL`` at a specific location and we
           operate on that dir.
    """
    import os as _os

    if _os.environ.get("BICAMERAL_DATA_PATH"):
        data_path = _os.environ["BICAMERAL_DATA_PATH"]
        return str(Path(data_path) / ".bicameral")

    if "SURREAL_URL" not in _os.environ:
        try:
            from ledger_locator import ProjectIdResolutionError, project_dir_for

            return str(project_dir_for(Path(repo_path) if repo_path else None))
        except ProjectIdResolutionError:
            pass

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
