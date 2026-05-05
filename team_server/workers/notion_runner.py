"""Notion worker runner - single-workspace internal-integration shape.

The internal-integration auth model gives one token per Notion
workspace; v1 ships single-workspace, so run_notion_iteration is a
thin wrapper over poll_once. Exists for symmetry with slack_runner
(both expose a zero-extra-arg work_fn for the lifespan to register).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ledger.client import LedgerClient
from team_server.workers import notion_worker

Extractor = Callable[[str], Awaitable[dict]]


async def run_notion_iteration(db_client: LedgerClient, token: str, extractor: Extractor) -> None:
    await notion_worker.poll_once(db_client, token, extractor)
