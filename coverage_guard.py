"""Preflight coverage guard — fast-path elimination for un-ingested files.

When all file paths in a preflight request have zero ledger/code_region coverage,
the full preflight pipeline (capture → projection → lookup → enforcement) is
unnecessary noise.  This module queries the daemon's lookup.query command for a
lightweight coverage probe and returns a structured no-fire decision when the
scope is entirely un-ingested.

Design invariants:
- Fails OPEN: any error, ambiguity, or partial coverage → proceed with full preflight.
- Does not remove or replace existing semantic relevance logic.
- Thin client: daemon owns the coverage determination via lookup.query.
- Only activates when ``files`` are explicitly provided.
"""

from __future__ import annotations

from typing import Any

from daemon_client import DaemonClient, DaemonClientError


async def check_coverage(
    *,
    client: DaemonClient,
    files: list[str],
    supported_commands: tuple[str, ...],
    arguments: dict[str, Any] | None = None,
) -> bool:
    """Return True if *all* files have zero ledger/code_region coverage.

    Returns False (meaning "has coverage, proceed with preflight") when:
    - lookup.query is not advertised by the daemon
    - The daemon returns any matches for the given files
    - Any file is NOT in the unknown_scope list
    - The daemon request fails for any reason

    This is a narrow fast-path: it only short-circuits when the daemon
    explicitly confirms that none of the requested files are covered.
    """
    if "lookup.query" not in supported_commands:
        return False

    from authority import build_authority_context
    from tool_request import build_tool_request

    # Use the original tool arguments for authority context so workspace,
    # actor_id, session_id etc. are preserved from the caller.
    authority_args = dict(arguments) if arguments else {}
    authority_args["files"] = files

    tool_request = build_tool_request(
        command_name="lookup.query",
        params={"files": files},
        authority=build_authority_context("bicameral.preflight", authority_args),
    )

    try:
        response = await client.send_tool_request(tool_request)
    except DaemonClientError:
        # Fail open: if we can't check coverage, proceed with full preflight.
        return False

    if response.get("status") != "ok":
        return False

    recall_packet = response.get("recall_packet", {})
    matches = recall_packet.get("matches", [])
    unknown_scope = recall_packet.get("unknown_scope", [])

    # Coverage exists if there are any matches.
    if matches:
        return False

    # All requested files must appear in unknown_scope for a definitive no-coverage.
    unknown_set = set(unknown_scope)
    if not all(f in unknown_set for f in files):
        return False

    return True
