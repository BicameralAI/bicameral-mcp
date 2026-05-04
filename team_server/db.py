"""DB factory for the team-server.

Wraps `ledger.client.LedgerClient` with team-server-specific defaults.
The team-server uses its own `ns/db` pair so its rows never collide with
a per-repo bicameral ledger that might share the same backing surrealkv
file (e.g., development setups).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ledger.client import LedgerClient

DEFAULT_URL = "memory://"
DEFAULT_NS = "bicameral_team"
DEFAULT_DB = "team_server"


@dataclass
class TeamServerDB:
    """Thin holder around `LedgerClient` so app.state can carry one object."""

    client: LedgerClient

    @classmethod
    def from_env(cls) -> TeamServerDB:
        url = os.environ.get("BICAMERAL_TEAM_SERVER_SURREAL_URL", DEFAULT_URL)
        return cls(client=LedgerClient(url=url, ns=DEFAULT_NS, db=DEFAULT_DB))

    async def connect(self) -> None:
        await self.client.connect()

    async def close(self) -> None:
        await self.client.close()


def build_client() -> LedgerClient:
    """Test/CLI helper — returns a configured but not-yet-connected client."""
    return TeamServerDB.from_env().client
