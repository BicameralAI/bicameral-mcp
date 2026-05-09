"""Pluggable event-log transport backends (#277).

Each backend moves per-author JSONL files between local cache and a
shared remote root. Backends know nothing about JSONL contents — pure
file transport. The remote root is a flat namespace of
``<author-email>.jsonl`` files (one per peer) plus optional
``<author-email>.lock`` sentinels.

Pull-only sync model: no daemons, no webhooks, no background polling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path


class BackendAdapter(ABC):
    """Move per-author event files between local cache and a remote root."""

    @abstractmethod
    async def push_events(self, local_path: Path, remote_name: str) -> None:
        """Upload ``local_path`` to ``<remote_root>/<remote_name>``.

        Idempotent: skip when remote hash matches local.
        """

    @abstractmethod
    async def pull_events(self, local_dir: Path, since_token: str | None) -> str:
        """Download every peer's JSONL into ``local_dir``.

        Skips the caller's own file (their local copy is authoritative).
        Returns an opaque token the caller passes back next time to enable
        since-cursor optimization (backends free to ignore and return "").
        Idempotent.
        """

    @abstractmethod
    def lock(self, remote_name: str) -> AbstractAsyncContextManager[None]:
        """Best-effort write lock. Caller handles races on its own."""

    @abstractmethod
    async def list_peers(self) -> AsyncIterator[str]:
        """Yield ``<author-email>`` for every peer file in remote_root."""


def get_backend(config: dict) -> BackendAdapter | None:
    """Construct the configured backend, or None when not in use.

    Reads ``team.backend`` from the parsed config dict; supported values:
    ``local_folder``, ``google_drive``. Anything else (including absent)
    returns ``None`` — team mode then behaves as today (local-only events).
    """
    team = config.get("team") or {}
    kind = team.get("backend")
    author = team.get("author", "")
    if kind == "local_folder":
        from .local_folder import LocalFolderAdapter

        return LocalFolderAdapter(remote_root=Path(team["remote_root"]), author=author)
    if kind == "google_drive":
        from .google_drive import GoogleDriveAdapter

        return GoogleDriveAdapter(folder_id=team["folder_id"], author=author)
    return None
