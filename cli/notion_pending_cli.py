"""CLI: ``bicameral-mcp notion-pending`` — operator-facing tool to
retrieve pending Notion ``verification_token`` values.

After ``webhooks/notion.py`` receives a verification handshake POST,
the token is persisted in ``secrets_store`` under a fingerprint-keyed
slot and the fingerprint (not the full token) is logged to stderr.
The operator runs this command to retrieve the full token by
fingerprint, then pastes it into Notion's UI to complete the
subscription handshake.

Usage::

    bicameral-mcp notion-pending              # list all pending entries
    bicameral-mcp notion-pending <fingerprint>  # print full token

The list form prints fingerprint + received-at age, NOT the token,
so it's safe to run in shared terminals. The retrieve form prints
the full token to stdout — operator is responsible for not leaving
that scrolled-back in a shared terminal.

This is the user-facing half of cycle-8's F3 review fix: the token
never appears in stderr / log pipelines; operators retrieve it via
this explicit command instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

# Same prefix the webhook handler uses. Source of truth lives in
# ``webhooks.notion``; keep these in sync.
_PENDING_PREFIX = "pending_"
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")
# Cap the listing output to keep the CLI usable when the registry
# has been spammed by attacker-driven verification POSTs. Matches
# the webhook handler's ``_MAX_PENDING_ENTRIES`` cap (review LOW-2)
# so a healthy registry never hits the truncation footer.
_LIST_MAX_ROWS = 100


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "fingerprint",
        nargs="?",
        default=None,
        help=(
            "16-char hex fingerprint logged by the webhook receiver when "
            "Notion's verification POST arrived. Omit to list all pending "
            "entries (without revealing tokens)."
        ),
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 on success, 1 on no-matching-entry, 2 on
    invalid-fingerprint, 3 on secrets_store error.
    """
    try:
        from secrets_store import get_secret, list_keys
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-pending] secrets_store unavailable: {exc}", file=sys.stderr)
        return 3

    if args.fingerprint is None:
        return _list_pending(list_keys, get_secret)

    fingerprint = args.fingerprint.strip().lower()
    if not _FINGERPRINT_RE.match(fingerprint):
        print(
            f"[notion-pending] fingerprint must be 16 lowercase hex chars; got "
            f"{args.fingerprint!r}",
            file=sys.stderr,
        )
        return 2

    return _retrieve_token(fingerprint, get_secret)


def _list_pending(list_keys_fn, get_secret_fn) -> int:
    """Print fingerprint + age for every pending entry. Never
    prints token contents."""
    try:
        keys = list_keys_fn(source_id="notion")
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-pending] list_keys failed: {exc}", file=sys.stderr)
        return 3

    pending_keys = sorted([k for k in keys if k.startswith(_PENDING_PREFIX)])
    if not pending_keys:
        print("No pending Notion verification entries.")
        return 0

    total = len(pending_keys)
    truncated = total > _LIST_MAX_ROWS
    visible = pending_keys[:_LIST_MAX_ROWS]

    now = int(time.time())
    print(f"{'FINGERPRINT':<18}{'AGE':<14}STATUS")
    for key in visible:
        fingerprint = key[len(_PENDING_PREFIX) :]
        raw = get_secret_fn(source_id="notion", key=key)
        if not raw:
            print(f"{fingerprint:<18}{'?':<14}(value missing)")
            continue
        try:
            entry = json.loads(raw)
            received_at = int(entry.get("received_at") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            print(f"{fingerprint:<18}{'?':<14}(malformed entry)")
            continue
        age_s = now - received_at
        age = _format_age(age_s)
        # 24h TTL on adoption (webhooks.notion._PENDING_TTL_SECONDS).
        status = "stale" if age_s > 86400 else "active"
        print(f"{fingerprint:<18}{age:<14}{status}")
    if truncated:
        print(
            f"... and {total - _LIST_MAX_ROWS} more (showing first {_LIST_MAX_ROWS}). "
            "Registry may be spammed; consider rotating the receiver URL."
        )
    return 0


def _retrieve_token(fingerprint: str, get_secret_fn) -> int:
    """Print the full token for one fingerprint to stdout. Operator
    pastes this into Notion's UI."""
    key = f"{_PENDING_PREFIX}{fingerprint}"
    try:
        raw = get_secret_fn(source_id="notion", key=key)
    except Exception as exc:  # noqa: BLE001
        print(f"[notion-pending] get_secret failed: {exc}", file=sys.stderr)
        return 3

    if not raw:
        print(
            f"[notion-pending] no pending entry for fingerprint {fingerprint!r}",
            file=sys.stderr,
        )
        return 1

    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"[notion-pending] entry for {fingerprint!r} is malformed: {exc}",
            file=sys.stderr,
        )
        return 3

    token = entry.get("token")
    if not isinstance(token, str) or not token:
        print(
            f"[notion-pending] entry for {fingerprint!r} has no token field",
            file=sys.stderr,
        )
        return 3

    # Full token to stdout, not stderr — operator-driven retrieval
    # is the safe surface (cycle 8 review F3). No trailing newline
    # so the operator can pipe directly into a clipboard tool
    # (``xclip``, ``pbcopy``, etc.) without spurious whitespace.
    sys.stdout.write(token)
    sys.stdout.flush()
    return 0


def _format_age(seconds: int) -> str:
    """Render an age-in-seconds as a compact human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    return f"{days}d"
