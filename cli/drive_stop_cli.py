"""CLI: ``bicameral-mcp drive-stop <channel_id>`` — cancel a Google
Drive Push Notification channel.

Wraps Drive's ``channels.stop`` API call:
- Looks up the channel in the local registry (we need the
  ``resource_id`` Drive returned at ``files.watch`` time — it's
  required for ``channels.stop`` and not recoverable any other way).
- Calls ``channels.stop`` with ``{id, resourceId}``.
- Deletes the local registry entry on success.

Returns 204 from Drive on success. If the channel is already
expired or stopped on Drive's side, Drive returns 404 — the CLI
still deletes the local registry entry (stale-on-Drive shouldn't
strand a stale local row).
"""

from __future__ import annotations

import argparse
import sys


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args."""
    subparser.add_argument(
        "channel_id",
        help=(
            "Channel UUID created by `bicameral-mcp drive-watch`. The "
            "registry holds the matching resource_id needed for the "
            "channels.stop call."
        ),
    )
    subparser.add_argument(
        "--keep-local",
        action="store_true",
        help=(
            "Don't delete the local registry entry even on successful "
            "channels.stop. Useful for debugging; not recommended in "
            "normal operation."
        ),
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 on success (incl. Drive-side already-stopped 404),
    1 on registry miss, 2 on Drive API failure (other than 404),
    3 on local cleanup failure.
    """
    channel_id = args.channel_id.strip()
    if not channel_id:
        print("[drive-stop] channel_id is empty", file=sys.stderr)
        return 1

    try:
        from sources.google_drive.channels import get_registry

        registry = get_registry()
        record = registry.get(channel_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-stop] registry read failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3

    if record is None:
        print(
            f"[drive-stop] no registry entry for channel_id={channel_id!r}. "
            "Either the channel was never created locally, or it was "
            "already stopped + reaped. Nothing to do.",
            file=sys.stderr,
        )
        return 1

    try:
        from sources.google_drive.auth import load_credentials

        creds = load_credentials()
    except RuntimeError as exc:
        print(
            f"[drive-stop] OAuth credentials unavailable: {exc}\n"
            "Run `bicameral-mcp source-auth google_drive` first.",
            file=sys.stderr,
        )
        return 2

    try:
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        service.channels().stop(body={"id": channel_id, "resourceId": record.resource_id}).execute()
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else None
        if status == 404:
            # Already stopped on Drive's side (expired or
            # operator-canceled via the Drive UI). Local registry
            # row is stale; still delete it.
            print(
                f"[drive-stop] channel {channel_id!r} already stopped on "
                "Drive's side (HTTP 404); reaping local registry entry.",
                file=sys.stderr,
            )
        else:
            print(
                f"[drive-stop] Drive channels.stop failed: HTTP {status}: {exc}",
                file=sys.stderr,
            )
            return 2
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-stop] Drive channels.stop raised: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    # ── Local cleanup ────────────────────────────────────────────
    if args.keep_local:
        print(
            f"[drive-stop] --keep-local: registry entry for {channel_id!r} NOT deleted.",
            file=sys.stderr,
        )
        return 0

    try:
        registry.delete(channel_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-stop] Drive channel stopped but local registry delete "
            f"failed: {type(exc).__name__}: {exc}\n"
            f"MANUAL ACTION: remove the entry for channel_id={channel_id!r} "
            "from ~/.bicameral/drive_channels.json.",
            file=sys.stderr,
        )
        return 3

    print(f"channel_id: {channel_id} stopped and reaped")
    return 0
