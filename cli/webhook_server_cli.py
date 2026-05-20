"""CLI: ``bicameral-mcp webhook-server`` (#337 cycle 5).

Starts the asyncio webhook receiver. Blocks until SIGINT / SIGTERM.

Operator workflow:

    # 1. Configure GitHub webhook secret via secrets_store
    python -c "from secrets_store import put_secret; \\
               put_secret(source_id='github', key='webhook_secret', value='<secret>')"

    # 2. Start the receiver
    bicameral-mcp webhook-server --port 8765

    # 3. In GitHub repo settings → Webhooks: point at https://<your-host>/webhooks/github
    #    with the SAME secret. Operator MUST put TLS in front (ngrok, Cloudflare,
    #    reverse proxy) — the server itself listens HTTP-only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default 127.0.0.1, loopback only). "
        "Non-loopback hosts require --allow-public.",
    )
    subparser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="bind port (default 8765).",
    )
    subparser.add_argument(
        "--allow-public",
        action="store_true",
        help="acknowledge that --host is non-loopback and that operator has "
        "terminated TLS at a reverse proxy / tunnel in front of this server. "
        "The receiver itself listens on plain HTTP.",
    )


def main(args: argparse.Namespace) -> int:
    from webhooks.server import serve

    try:
        asyncio.run(serve(host=args.host, port=args.port, allow_public=args.allow_public))
    except KeyboardInterrupt:
        print("[webhook-server] interrupted; shutting down.", file=sys.stderr)
        return 0
    except RuntimeError as exc:
        # Refused public bind without --allow-public is intentional and
        # gets a 2 exit code (operator config error).
        print(f"[webhook-server] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[webhook-server] fatal: {exc}", file=sys.stderr)
        return 1
    return 0
