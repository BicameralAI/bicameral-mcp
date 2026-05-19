"""reset CLI subcommand — non-interactive ledger reset (#410).

Thin wrapper over ``handlers.reset.handle_reset`` for the case where the
agent (or operator) needs a non-interactive escape hatch:

* the MCP ``bicameral_reset`` tool isn't reachable in the running
  session (tool-surface pinning bug — see project memory note
  ``project_mcp_tool_schema_pinned_at_startup``), or
* the MCP server itself can't start because ``init_schema``/``migrate``
  is crashing on a corrupted on-disk ledger.

The interactive ``setup_wizard.run_reset_wizard`` still handles the
human-driven case (no ``--confirm`` flag).

Filesystem-only fallback: when ``--confirm --wipe-mode=full`` is given
and ``BicameralContext.from_env()``/``ledger.connect()`` raises (the
exact scenario described in #410), we drop down to a direct
``shutil.rmtree`` on the resolved ``.bicameral/`` directory. The whole
point of full-wipe is that the DB is unreadable; requiring a working
DB connection to recover from that would be circular.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path


def run_noninteractive_reset(
    *,
    wipe_mode: str,
    replay_from_events: bool,
) -> int:
    """Execute a confirmed reset and emit a JSON result to stdout.

    Returns 0 on success (including the filesystem-only fallback), 1 on
    failures the operator must act on.
    """
    try:
        return asyncio.run(_run(wipe_mode=wipe_mode, replay_from_events=replay_from_events))
    except KeyboardInterrupt:
        sys.stderr.write("reset: aborted by user\n")
        return 1


async def _run(*, wipe_mode: str, replay_from_events: bool) -> int:
    try:
        from context import BicameralContext
        from handlers.reset import handle_reset

        ctx = BicameralContext.from_env()
        response = await handle_reset(
            ctx,
            replay=True,
            confirm=True,
            wipe_mode=wipe_mode,
            replay_from_events=replay_from_events,
        )
        sys.stdout.write(json.dumps(response.model_dump(), default=str, indent=2) + "\n")
        return 0 if response.wiped else 1
    except Exception as exc:  # noqa: BLE001 — recovery-path fallback by design
        # The recovery tool itself failed to bring up a working ledger.
        # For wipe_mode='full' this is exactly the situation the
        # filesystem-only fallback is designed for: the DB is unreadable,
        # so we delete .bicameral/ at the filesystem level.
        if wipe_mode != "full":
            sys.stderr.write(
                f"reset: ledger connect failed ({type(exc).__name__}: {exc}).\n"
                "       ledger-mode wipe requires a working connection.\n"
                "       Re-run with `--wipe-mode=full` for a filesystem-only wipe.\n"
            )
            return 1
        return _fallback_full_wipe(connect_error=exc)


def _fallback_full_wipe(*, connect_error: BaseException) -> int:
    """Filesystem-only ``.bicameral/`` wipe when the DB can't even connect.

    Resolves the directory from ``SURREAL_URL`` (or the embedded default)
    and ``shutil.rmtree``s it. Emits a JSON result mirroring the shape
    of ``ResetResponse`` so callers can parse it the same way.
    """
    from ledger.adapter import _default_db_url

    ledger_url = os.environ.get("SURREAL_URL", _default_db_url())
    bicameral_dir = _bicameral_dir_for_url(ledger_url)

    if not bicameral_dir:
        sys.stderr.write(
            f"reset: ledger URL {ledger_url!r} has no on-disk directory to wipe.\n"
            f"       Original connect error: {type(connect_error).__name__}: {connect_error}\n"
        )
        return 1

    try:
        shutil.rmtree(bicameral_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 — last-resort fallback
        sys.stderr.write(
            f"reset: filesystem wipe of {bicameral_dir!r} failed: {type(exc).__name__}: {exc}\n"
        )
        return 1

    sys.stdout.write(
        json.dumps(
            {
                "wiped": True,
                "wipe_mode": "full",
                "ledger_url": ledger_url,
                "bicameral_dir": bicameral_dir,
                "fallback": "filesystem_only",
                "connect_error": f"{type(connect_error).__name__}: {connect_error}",
                "next_action": (
                    f"Full wipe complete (filesystem fallback). {bicameral_dir!r} "
                    "deleted. The next bicameral tool call will reinitialise the "
                    "schema from scratch."
                ),
            },
            indent=2,
        )
        + "\n"
    )
    return 0


def _bicameral_dir_for_url(url: str) -> str:
    """Return the on-disk dir to filesystem-wipe in the recovery fallback.

    Resolution order (#410):
        1. ``BICAMERAL_DATA_PATH`` override (tests / pre-#368 installs).
        2. When no explicit ``SURREAL_URL`` is set, route through the
           locator — the URL came from the locator default, so the
           canonical state dir is the locator's project dir.
        3. Otherwise (explicit ``SURREAL_URL``, or locator can't
           resolve), derive from the URL via ``Path(db).parent`` — the
           user has signalled where their ledger lives and we operate
           on that dir.

    The locator branch matters because this fallback runs when the
    adapter can't even connect; the locator itself only needs
    ``REPO_PATH`` + git common-dir, so it's still available.
    """
    if dp := os.environ.get("BICAMERAL_DATA_PATH"):
        return str(Path(dp) / ".bicameral")

    if "SURREAL_URL" not in os.environ:
        try:
            from ledger_locator import ProjectIdResolutionError, project_dir_for

            return str(project_dir_for())
        except ProjectIdResolutionError:
            pass

    if not url.startswith("surrealkv://"):
        return ""
    db_path = url[len("surrealkv://") :]
    if not db_path:
        return ""
    return str(Path(db_path).expanduser().parent)
