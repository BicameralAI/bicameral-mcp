"""LocalFolderAdapter — BackendAdapter backed by a shared filesystem path (#277).

Useful as an integration-test backend and as a fallback for orgs that already
share a synced folder (NFS, Dropbox, syncthing). Same wire shape as
GoogleDriveAdapter so the rest of the system is backend-agnostic.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalFolderAdapter:
    """Move per-author event files via a shared filesystem directory.

    Each author writes only to ``<remote_root>/<my-email>.jsonl``. Pull
    copies every peer's file (everything except the caller's own) into the
    caller's local events_dir; copies are skipped when sha256 matches.
    """

    def __init__(self, remote_root: Path, author: str) -> None:
        self._remote_root = Path(remote_root)
        self._author = author
        self._remote_root.mkdir(parents=True, exist_ok=True)
        self._lock_locks: dict[str, asyncio.Lock] = {}

    async def push_events(self, local_path: Path, remote_name: str) -> None:
        target = self._remote_root / remote_name
        if target.exists() and _sha256_file(target) == _sha256_file(local_path):
            return
        shutil.copy2(local_path, target)

    async def pull_events(self, local_dir: Path, since_token: str | None) -> str:
        local_dir.mkdir(parents=True, exist_ok=True)
        own_name = f"{self._author}.jsonl"
        for remote_path in self._remote_root.glob("*.jsonl"):
            if remote_path.name == own_name:
                continue
            local_path = local_dir / remote_path.name
            if local_path.exists() and _sha256_file(local_path) == _sha256_file(remote_path):
                continue
            shutil.copy2(remote_path, local_path)
        return ""

    @asynccontextmanager
    async def lock(self, remote_name: str):
        """Best-effort serialization within this process via asyncio.Lock.

        Cross-process locking via fcntl/msvcrt is intentionally out of v0
        scope — same-author cross-machine writes are an edge case (the
        per-author file model already serializes the common case).
        """
        lock = self._lock_locks.setdefault(remote_name, asyncio.Lock())
        async with lock:
            yield

    async def list_peers(self) -> AsyncIterator[str]:
        for path in sorted(self._remote_root.glob("*.jsonl")):
            yield path.stem
