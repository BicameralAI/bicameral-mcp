"""CLI: ``bicameral-mcp drive-watch <file_url>`` — start a Google
Drive Push Notification subscription for a single file.

Operator-driven entry point for the cycle-9 webhook receiver:
calls Drive's ``files.watch`` to create a channel pointing at the
operator's public webhook URL, then persists the channel metadata
in the local registry so the receiver's three-way-match auth has
something to look up.

Usage::

    bicameral-mcp drive-watch \\
        --callback-url https://operator.example.com/webhooks/google-drive \\
        --file-url https://docs.google.com/document/d/<file_id>/edit \\
        [--token <opaque-string>] \\
        [--ttl-seconds 86400]

The ``--token`` flag is the only auth primitive Drive provides
(echoed in ``X-Goog-Channel-Token`` on every notification). Omit
it to auto-generate via ``secrets.token_urlsafe(32)``.

Token-handling posture (review L10): unlike Notion's
``verification_token`` (which is a long-term HMAC secret and is
fingerprint-only in logs), Drive channel tokens are per-channel,
short-lived (≤86400s), echoed back in cleartext on every
notification, and operator-chosen rather than provider-generated.
The token is therefore stored in the channel registry (0o600 file
+ 0o700 dir on POSIX, per cycle 9 MED-1 fix) and is NOT printed
to stdout/stderr — but the threat model is one notch lower than
the Notion token's. Future cycles that "harmonize" the CLI logs
across providers should NOT strip the token write to the
registry; the receiver's three-way-match needs it.

The ``--ttl-seconds`` flag caps at 86400 (Drive's documented max
for ``files.watch``). Default is the max; renewal job (cycle 9c)
will issue successors before expiry.

The CLI prints the created channel_id (operator passes it to
``drive-stop`` later if they want to cancel) and the expiration
timestamp.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
import uuid
from urllib.parse import urlparse

_MAX_TTL_SECONDS = 86400  # Drive's documented max for files.watch
_TOKEN_DEFAULT_BYTES = 32


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "--callback-url",
        required=True,
        help=(
            "Public HTTPS URL operator's reverse proxy forwards to the "
            "webhook receiver. Must be HTTPS (Drive rejects plain HTTP)."
        ),
    )
    subparser.add_argument(
        "--file-url",
        required=True,
        help=(
            "Google Docs URL to watch. Same shape the active-fetch adapter "
            "accepts: docs.google.com/document/d/<id>/... or "
            "drive.google.com/file/d/<id>/...."
        ),
    )
    subparser.add_argument(
        "--token",
        default=None,
        help=(
            "Opaque token echoed in X-Goog-Channel-Token on each "
            "notification. Auto-generated via secrets.token_urlsafe(32) "
            "if omitted. Maximum 256 chars per Drive's docs."
        ),
    )
    subparser.add_argument(
        "--ttl-seconds",
        type=int,
        default=_MAX_TTL_SECONDS,
        help=(
            "Channel lifetime in seconds. Drive's documented max for "
            "files.watch is 86400 (1 day); the CLI clamps to this value. "
            "Renewal (cycle 9c) issues successors before expiry."
        ),
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 on success, 1 on input validation failure, 2 on auth /
    Drive API failure, 3 on persistence failure.
    """
    # ── Input validation ────────────────────────────────────────
    parsed_callback = urlparse(args.callback_url)
    if parsed_callback.scheme != "https":
        print(
            f"[drive-watch] --callback-url must be https://; got scheme "
            f"{parsed_callback.scheme!r}. Drive rejects non-HTTPS endpoints.",
            file=sys.stderr,
        )
        return 1
    if not parsed_callback.netloc:
        print("[drive-watch] --callback-url has no host", file=sys.stderr)
        return 1

    try:
        from sources.google_drive.adapter import parse_gdrive_url

        file_id = parse_gdrive_url(args.file_url)
    except ValueError as exc:
        print(f"[drive-watch] --file-url invalid: {exc}", file=sys.stderr)
        return 1

    ttl = args.ttl_seconds
    if ttl <= 0 or ttl > _MAX_TTL_SECONDS:
        print(
            f"[drive-watch] --ttl-seconds must be in (0, {_MAX_TTL_SECONDS}]; got {ttl}",
            file=sys.stderr,
        )
        return 1

    token = args.token if args.token else secrets.token_urlsafe(_TOKEN_DEFAULT_BYTES)
    if len(token) > 256:
        print(
            f"[drive-watch] --token must be ≤256 chars (Drive limit); got {len(token)}",
            file=sys.stderr,
        )
        return 1

    channel_id = str(uuid.uuid4())
    expiration_ms = int((time.time() + ttl) * 1000)

    # ── Drive API call ──────────────────────────────────────────
    try:
        from sources.google_drive.auth import load_credentials

        creds = load_credentials()
    except RuntimeError as exc:
        print(
            f"[drive-watch] OAuth credentials unavailable: {exc}\n"
            "Run `bicameral-mcp source-auth google_drive` first.",
            file=sys.stderr,
        )
        return 2

    try:
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
        from googleapiclient.errors import HttpError  # type: ignore[import-not-found]

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        request_body = {
            "id": channel_id,
            "type": "web_hook",
            "address": args.callback_url,
            "token": token,
            "expiration": str(expiration_ms),
        }
        response = service.files().watch(fileId=file_id, body=request_body).execute()
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else "?"
        print(
            f"[drive-watch] Drive files.watch failed: HTTP {status}: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            f"[drive-watch] Drive files.watch raised: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    resource_id = response.get("resourceId") or ""
    if not resource_id:
        # Without resource_id we cannot validate future notifications
        # OR stop the channel. Abort and refuse to register a
        # half-dead channel.
        print(
            "[drive-watch] Drive response missing resourceId — cannot "
            "register a channel that can never be validated or stopped. "
            f"Raw response: {response!r}",
            file=sys.stderr,
        )
        return 2

    # ── Registry persistence ────────────────────────────────────
    try:
        from sources.google_drive.channels import ChannelRecord, get_registry

        record = ChannelRecord(
            channel_id=channel_id,
            resource_id=resource_id,
            token=token,
            expiration_ms=expiration_ms,
            file_id=file_id,
            watched_resource_kind="file",
            created_at_ms=int(time.time() * 1000),
        )
        get_registry().register(record)
    except Exception as exc:  # noqa: BLE001
        # Channel exists on Drive's side but we couldn't persist
        # locally. Best-effort cleanup: stop the channel so we don't
        # leak it. If stop itself fails, report both errors.
        print(
            f"[drive-watch] channel registry persistence failed: "
            f"{type(exc).__name__}: {exc}\n"
            "Attempting to stop the newly-created channel to avoid leak...",
            file=sys.stderr,
        )
        try:
            service.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()
            # L5 review fix: confirm successful cleanup so the
            # operator isn't left ambiguous about leak status.
            print(
                "[drive-watch] cleanup channels.stop succeeded; no Drive-side leak.",
                file=sys.stderr,
            )
        except Exception as cleanup_exc:  # noqa: BLE001
            print(
                f"[drive-watch] cleanup channels.stop also failed: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}\n"
                f"MANUAL ACTION: stop channel id={channel_id!r} "
                f"resourceId={resource_id!r} via the Drive API console.",
                file=sys.stderr,
            )
        return 3

    # ── Success report ──────────────────────────────────────────
    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiration_ms / 1000))
    print(f"channel_id: {channel_id}")
    print(f"file_id: {file_id}")
    print(f"resource_id: {resource_id}")
    print(f"expires: {expires_iso} ({ttl}s from now)")
    # Token to stderr, not stdout — operator may pipe stdout to a
    # config file; keeping the token off stdout reduces accidental
    # exposure. The token also lives in the registry; operator can
    # retrieve it from there if needed.
    print(
        f"[drive-watch] channel registered. Token written to local "
        f"registry; do NOT log it. Use `bicameral-mcp drive-stop "
        f"{channel_id}` to cancel.",
        file=sys.stderr,
    )
    return 0
