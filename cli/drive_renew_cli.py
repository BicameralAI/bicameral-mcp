"""CLI: ``bicameral-mcp drive-renew`` — renew Drive Push Notification
channels approaching expiration.

Drive's documented max TTL for ``files.watch`` is 86400 seconds (1
day). Channels expire SILENTLY — Google does not send a "your
channel is dying" notification — so the operator must run a
renewal cadence or ingest stops at the 24h mark.

## Dedup behavior (cycle 9c review HIGH-1)

During the renewal window (between step 2 / persist-new and step 3
/ stop-old) BOTH the old and new channels are active on Drive's
side. Drive will deliver the same change notification to both
channels with DIFFERENT ``X-Goog-Channel-Id`` headers. The
:mod:`webhooks.google_drive` handler does NOT currently consult
:mod:`webhooks.dedup` (unlike the other four providers' handlers)
— so a change inside the renewal window will fire ``handle_ingest``
TWICE for the same file_id. Suppression of the duplicate falls to
the ledger's content-hash idempotency (`#216`), which is a
weaker contract than per-delivery dedup.

This is a pre-existing cycle-9b gap that renewal aggravates.
Cycle 9d adds proper dedup keyed on ``(channel_id,
message_number)``. Until then, operators running this CLI should
expect a small rate of duplicate-ledger-row attempts during
renewal windows (suppressed by content-hash idempotency, visible
in the audit log).

This CLI does one pass over the channel registry. For each channel
expiring within the threshold (default: 43200s = 12h), it issues a
successor:

1. Call ``files.watch`` with a new UUID channel_id, the same
   callback_url, a fresh token, and a fresh 24h expiration.
2. Persist the new ``ChannelRecord``.
3. Call ``channels.stop`` on the old channel.
4. Delete the old registry entry.

Failure modes are isolated per-channel: if one renewal fails, the
pass continues with the rest. Exit code reflects whether ANY
renewals failed.

Recommended cadence: run every 6h via cron / systemd timer / Task
Scheduler. The 12h threshold gives 6h headroom for cron skew + the
renewal itself.

Usage::

    bicameral-mcp drive-renew                      # default: renew <12h
    bicameral-mcp drive-renew --threshold-seconds 28800  # renew <8h
    bicameral-mcp drive-renew --dry-run            # report what would happen

Token rotation: every renewal issues a fresh token via
``secrets.token_urlsafe(32)``. Cycle-8 review F2 lesson applied —
limiting the operational window of any one token to ~24h.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
import uuid

_DEFAULT_THRESHOLD_SECONDS = 12 * 60 * 60  # 12h: half of Drive's max TTL
_MAX_TTL_SECONDS = 86400  # Drive's documented max for files.watch
# MED-2 review fix: ceiling threshold at half of max TTL. Above
# this, EVERY channel becomes "due" each pass (no channel can have
# more than 86400s remaining), doubling Drive API call volume
# gratuitously and tripling the HIGH-2 race window.
_THRESHOLD_CEILING_SECONDS = _MAX_TTL_SECONDS // 2
_TOKEN_BYTES = 32
# HIGH-2 review fix: exclusive file lock so two concurrent CLI
# invocations (cron + manual) cannot both run a pass and leak a
# new channel into Drive without persisting it locally.
_LOCK_PATH = None  # resolved lazily inside main() to honor $HOME mocks in tests


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args."""
    subparser.add_argument(
        "--threshold-seconds",
        type=int,
        default=_DEFAULT_THRESHOLD_SECONDS,
        help=(
            "Renew channels expiring in less than this many seconds. "
            f"Default {_DEFAULT_THRESHOLD_SECONDS}s (12h) — half of "
            f"Drive's max TTL. Capped at {_THRESHOLD_CEILING_SECONDS}s. "
            "Lower values renew sooner (more API traffic); higher "
            "values risk missed renewals if the cron cadence is loose."
        ),
    )
    subparser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be renewed without making any API calls.",
    )


class _LockUnavailable(Exception):
    """Raised when the exclusive renewal lock is already held."""


def _acquire_lock(path):
    """Acquire an exclusive non-blocking lock on ``path``. Returns a
    file descriptor that the caller must keep open for the lock's
    duration; closing it releases the lock.

    Cross-platform: ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on
    Windows. Both are advisory in this codebase (no other process
    holds them) — the lock just prevents concurrent ``drive-renew``
    invocations from racing.

    Raises:
        _LockUnavailable: another process holds the lock.
    """
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode so we don't truncate; create if missing.
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        os.close(fd)
        raise _LockUnavailable(str(exc)) from exc
    return fd


def _release_lock(fd) -> None:
    """Release the lock obtained via :func:`_acquire_lock`."""
    import os

    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 if all renewals succeeded (or no renewals were due),
    1 on input validation failure, 2 if ANY individual renewal
    failed (the rest still attempted), 3 on infrastructure failure
    (OAuth, registry read).
    """
    threshold = args.threshold_seconds
    if threshold <= 0 or threshold > _THRESHOLD_CEILING_SECONDS:
        print(
            f"[drive-renew] --threshold-seconds must be in "
            f"(0, {_THRESHOLD_CEILING_SECONDS}]; got {threshold}. "
            "Values above half of Drive's max TTL renew the entire "
            "registry every pass.",
            file=sys.stderr,
        )
        return 1

    # HIGH-2 review fix: acquire exclusive lock to prevent concurrent
    # passes from racing on `files.watch` and leaking channels.
    # Skipped for --dry-run (no API mutations, no race).
    from pathlib import Path

    lock_path = Path.home() / ".bicameral" / ".drive_renew.lock"
    lock_fd = None
    if not args.dry_run:
        try:
            lock_fd = _acquire_lock(lock_path)
        except _LockUnavailable:
            print(
                "[drive-renew] another renewal pass is already in progress "
                f"(lock at {lock_path}); skipping.",
                file=sys.stderr,
            )
            return 0

    try:
        try:
            from sources.google_drive.channels import get_registry

            registry = get_registry()
            records = registry.list_all()
        except Exception as exc:  # noqa: BLE001
            print(
                f"[drive-renew] registry read failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 3
        return _run_pass(args, records, registry)
    finally:
        if lock_fd is not None:
            _release_lock(lock_fd)


def _run_pass(args, records, registry) -> int:
    """Inner main body — separated from ``main`` so the lock can be
    released in a finally block regardless of early returns."""
    threshold = args.threshold_seconds

    now_ms = int(time.time() * 1000)
    threshold_ms = threshold * 1000
    due_all = [r for r in records if r.expiration_ms - now_ms < threshold_ms]

    # MED-1 review fix: partition pre-9c rows (no callback_url) out of
    # the renewal loop. They cannot be auto-renewed and would
    # otherwise pollute the failure summary on every pass.
    due_renewable = [r for r in due_all if r.callback_url]
    due_unrenewable = [r for r in due_all if not r.callback_url]

    print(
        f"[drive-renew] {len(records)} channel(s) in registry, "
        f"{len(due_all)} due for renewal (threshold={threshold}s)",
        file=sys.stderr,
    )
    if due_unrenewable:
        print(
            f"[drive-renew] WARNING: {len(due_unrenewable)} channel(s) "
            "cannot be auto-renewed (pre-cycle-9c rows; missing callback_url):",
            file=sys.stderr,
        )
        for r in due_unrenewable:
            remaining_s = max(0, (r.expiration_ms - now_ms) // 1000)
            print(
                f"  channel_id={r.channel_id!r} file_id={r.file_id!r} expires_in={remaining_s}s",
                file=sys.stderr,
            )
        print(
            "[drive-renew]   re-run `bicameral-mcp drive-watch "
            "--callback-url ... --file-url ...` to re-register under "
            "the cycle-9c schema.",
            file=sys.stderr,
        )

    if not due_renewable:
        return 0

    if args.dry_run:
        for record in due_renewable:
            remaining_s = max(0, (record.expiration_ms - now_ms) // 1000)
            print(
                f"channel_id={record.channel_id} file_id={record.file_id} "
                f"expires_in={remaining_s}s callback_url={record.callback_url!r}"
            )
        return 0

    # Single OAuth fetch for the whole pass — cheaper than re-loading
    # per-channel, and we want a uniform credential state across the
    # renewal batch.
    try:
        from sources.google_drive.auth import load_credentials

        creds = load_credentials()
    except RuntimeError as exc:
        print(
            f"[drive-renew] OAuth credentials unavailable: {exc}\n"
            "Run `bicameral-mcp source-auth google_drive` first.",
            file=sys.stderr,
        )
        return 3

    try:
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-renew] Drive service build failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    for record in due_renewable:
        result = _renew_one(record, service, registry)
        if result is None:
            succeeded.append(record.channel_id)
        else:
            failed.append((record.channel_id, result))

    # Operator-facing summary on stdout (parsable by cron logs);
    # detail on stderr (already logged per failure).
    print(f"renewed: {len(succeeded)}")
    print(f"failed: {len(failed)}")
    for old_id, reason in failed:
        print(f"  {old_id}: {reason}")

    return 0 if not failed else 2


def _renew_one(record, service, registry) -> str | None:
    """Issue a successor channel for ``record``, persist, stop old,
    delete old registry entry. Returns ``None`` on full success or a
    short failure reason string.

    Failure modes are local to this function and reported via the
    return value so the outer loop continues with the next record.
    """
    # Defensive: `_run_pass` already partitions empty-callback_url
    # rows out, but pin the invariant here so a future caller that
    # invokes `_renew_one` directly doesn't silently misbehave.
    if not record.callback_url:
        return "callback_url empty (cycle-9b row pre-dating cycle 9c)"

    try:
        from googleapiclient.errors import HttpError  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return f"googleapiclient import failed: {exc}"

    new_channel_id = str(uuid.uuid4())
    new_token = secrets.token_urlsafe(_TOKEN_BYTES)
    new_expiration_ms = int((time.time() + _MAX_TTL_SECONDS) * 1000)

    # ── Step 1: files.watch on the same file_id with the successor
    #            channel parameters.
    try:
        request_body = {
            "id": new_channel_id,
            "type": "web_hook",
            "address": record.callback_url,
            "token": new_token,
            "expiration": str(new_expiration_ms),
        }
        response = service.files().watch(fileId=record.file_id, body=request_body).execute()
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else "?"
        print(
            f"[drive-renew] files.watch failed for channel "
            f"{record.channel_id!r} file_id={record.file_id!r}: "
            f"HTTP {status}: {exc}",
            file=sys.stderr,
        )
        return f"files.watch HTTP {status}"
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-renew] files.watch raised for channel "
            f"{record.channel_id!r}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return f"files.watch raised {type(exc).__name__}"

    new_resource_id = response.get("resourceId") or ""
    if not new_resource_id:
        # Successor channel was created on Drive's side but we can't
        # validate or stop it. Best-effort cleanup so we don't leak.
        print(
            f"[drive-renew] successor channel for {record.channel_id!r} "
            "has no resourceId in the response; attempting cleanup",
            file=sys.stderr,
        )
        try:
            service.channels().stop(body={"id": new_channel_id, "resourceId": ""}).execute()
        except Exception:  # noqa: BLE001
            pass
        return "successor response missing resourceId"

    # ── Step 2: persist new ChannelRecord.
    try:
        from sources.google_drive.channels import ChannelRecord

        new_record = ChannelRecord(
            channel_id=new_channel_id,
            resource_id=new_resource_id,
            token=new_token,
            expiration_ms=new_expiration_ms,
            file_id=record.file_id,
            watched_resource_kind=record.watched_resource_kind,
            callback_url=record.callback_url,
            created_at_ms=int(time.time() * 1000),
        )
        registry.register(new_record)
    except Exception as exc:  # noqa: BLE001
        # New channel exists on Drive's side; we couldn't persist it
        # locally. Stop the orphan to avoid leak.
        print(
            f"[drive-renew] persist failed for successor of {record.channel_id!r}: "
            f"{type(exc).__name__}: {exc}\n"
            f"Attempting to stop orphan channel id={new_channel_id!r}...",
            file=sys.stderr,
        )
        try:
            service.channels().stop(
                body={"id": new_channel_id, "resourceId": new_resource_id}
            ).execute()
        except Exception as cleanup_exc:  # noqa: BLE001
            print(
                f"[drive-renew] orphan cleanup also failed: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}\n"
                f"MANUAL ACTION: stop channel id={new_channel_id!r} "
                f"resourceId={new_resource_id!r} via the Drive API console.",
                file=sys.stderr,
            )
        return f"persist failed: {type(exc).__name__}"

    # ── Step 3: stop the OLD channel. Failure here is non-fatal — the
    #            old channel will expire naturally within the threshold
    #            window. Best-effort, log loudly.
    try:
        service.channels().stop(
            body={"id": record.channel_id, "resourceId": record.resource_id}
        ).execute()
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else "?"
        if status == 404:
            # Already stopped on Drive's side (raced with operator
            # drive-stop or expired between our list and now). Fine.
            pass
        else:
            print(
                f"[drive-renew] channels.stop for old {record.channel_id!r} "
                f"failed: HTTP {status}: {exc}. Old channel will expire "
                "naturally; no operator action required.",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-renew] channels.stop for old {record.channel_id!r} "
            f"raised: {type(exc).__name__}: {exc}. Old channel will expire "
            "naturally; no operator action required.",
            file=sys.stderr,
        )

    # ── Step 4: delete the OLD registry entry. Failure here is
    #            non-fatal but visible — leaves a stale row that the
    #            next renewal pass will trip over (expiration already
    #            past, so `_renew_one` would try to renew a dead
    #            channel and produce a 4xx). Log so operator can
    #            manually delete.
    try:
        registry.delete(record.channel_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-renew] delete of old registry entry "
            f"{record.channel_id!r} failed: {type(exc).__name__}: {exc}. "
            "MANUAL ACTION: remove the entry from "
            "~/.bicameral/drive_channels.json.",
            file=sys.stderr,
        )
        # Don't fail the renewal — the new channel IS persisted and
        # active. The stale old row is operator-cleanable.

    print(
        f"[drive-renew] renewed channel {record.channel_id!r} → "
        f"{new_channel_id!r} (file_id={record.file_id!r})",
        file=sys.stderr,
    )
    return None
