"""EventMaterializer — replays JSONL event logs into the local ledger (v0.4.20).

One file per contributor: ``.bicameral/events/{email}.jsonl``. Watermark
is a JSON ``{email: byte_offset}`` map at ``.bicameral/local/watermark``.
Replay resumes from the stored offset per author.

Auto-migrates legacy ``{email}/*.json`` layout (v0.4.13 – v0.4.19) on
first startup, then deletes the old files. DB-level ``canonical_id``
UNIQUE makes any re-replay safe.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class EventMaterializer:
    def __init__(self, events_dir: Path, local_dir: Path) -> None:
        self._events_dir = events_dir
        self._watermark_path = local_dir / "watermark"
        local_dir.mkdir(parents=True, exist_ok=True)

    def _read_offsets(self) -> dict[str, int]:
        if not self._watermark_path.exists():
            return {}
        raw = self._watermark_path.read_text(encoding="utf-8").strip()
        try:
            data = json.loads(raw) if raw else {}
            return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError, TypeError):
            # Legacy timestamp-string watermark (≤v0.4.19) — discard; DB dedup covers re-replay.
            return {}

    def _migrate_legacy(self) -> None:
        """Consolidate legacy ``{email}/*.json`` → ``{email}.jsonl``, once."""
        if not self._events_dir.exists():
            return
        for d in sorted(self._events_dir.iterdir()):
            if not d.is_dir():
                continue
            legacy = sorted(d.glob("*.json"), key=lambda f: f.name)
            if not legacy:
                continue
            out_path = self._events_dir / f"{d.name}.jsonl"
            with open(out_path, "ab") as out:
                for f in legacy:
                    try:
                        env = json.loads(f.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        continue
                    env.pop("event_id", None)
                    out.write((json.dumps(env, separators=(",", ":"), default=str) + "\n").encode())
                    f.unlink()
            try:
                d.rmdir()
            except OSError:
                pass
            logger.info("[migrate] %d legacy events → %s.jsonl", len(legacy), d.name)

    async def replay_new_events(self, inner_adapter) -> int:
        if not self._events_dir.exists():
            return 0
        self._migrate_legacy()

        offsets = self._read_offsets()
        new_offsets = dict(offsets)
        replayed = 0

        for path in sorted(self._events_dir.glob("*.jsonl")):
            author = path.stem
            start = offsets.get(author, 0)
            size = path.stat().st_size
            if size < start:  # file shrank (history rewrite) — re-read
                start = 0
            if size == start:
                continue
            with open(path, "rb") as f:
                f.seek(start)
                for raw in f:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    etype, payload = event.get("event_type", ""), event.get("payload", {})
                    if etype == "ingest.completed":
                        await inner_adapter.ingest_payload(payload)
                        replayed += 1
                    elif etype == "link_commit.completed":
                        await inner_adapter.ingest_commit(
                            payload.get("commit_hash", ""), payload.get("repo_path", ""),
                        )
                        replayed += 1
                new_offsets[author] = f.tell()

        if new_offsets != offsets:
            self._watermark_path.write_text(json.dumps(new_offsets) + "\n", encoding="utf-8")
        if replayed:
            logger.info("[materializer] replayed %d events", replayed)
        return replayed
