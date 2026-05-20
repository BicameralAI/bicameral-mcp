"""CLI: ``bicameral-mcp source-auth <source>`` — run the OAuth handshake (Phase 5b).

Currently dispatches to:
    google_drive → sources.google_drive.auth.run_oauth_handshake()

Future sources (Linear PAT-OAuth, Notion OAuth, GitHub App install,
Slack OAuth) will register here when their auth flows ship.
"""

from __future__ import annotations

import argparse
import sys

_KNOWN_SOURCES = ("google_drive",)


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "source",
        choices=list(_KNOWN_SOURCES),
        help=(
            "Source to authenticate. Currently only google_drive — the Linear "
            "/ Notion / GitHub adapters use static API keys stored via "
            "`secrets_store.put_secret`."
        ),
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Returns 0 on successful handshake, 1 on flow abort, 2 on bundled-client
    misconfiguration, 3 on unexpected exception. Non-zero exits print a
    one-line operator-facing hint to stderr.
    """
    if args.source == "google_drive":
        return _run_google_drive()
    # argparse choices=... blocks unknown sources before we get here, but
    # belt-and-suspenders in case the choices list and the dispatch table
    # drift.
    print(f"[source-auth] unknown source: {args.source}", file=sys.stderr)
    return 2


def _run_google_drive() -> int:
    from sources.google_drive.auth import OAuthFlowAbortedError, run_oauth_handshake

    print("[source-auth] starting Google Drive OAuth handshake...")
    print("[source-auth] your browser should open shortly. Sign in and consent.")
    try:
        run_oauth_handshake()
    except OAuthFlowAbortedError as exc:
        print(f"[source-auth] flow aborted: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(
            f"[source-auth] missing OAuth dependency ({exc}). "
            "Reinstall bicameral-mcp to pick up `google-auth-oauthlib`.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        # OAuthClientNotProvisionedError from events.backends.google_drive lands here.
        print(f"[source-auth] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI must never re-raise to user
        print(
            f"[source-auth] unexpected error ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 3
    print(
        "[source-auth] success — token stored in OS keyring under "
        "service `bicameral-mcp::google_drive`, key `oauth_token`. "
        "The Google Drive ingest adapter is now ready."
    )
    return 0
