"""Local-directory source adapter — captures decisions dropped as files (#344).

Watches a configured local directory for new files (filtered by extension),
emits one ingest payload per file, watermarks by latest mtime. Two-phase
commit parity with the Granola precedent: ``pull()`` stages a pending
watermark; ``confirm_watermark()`` persists it only after the caller
confirms the ingest batch succeeded.

Closes #344 (planning/brainstorming workflows weren't being auto-captured
because SessionEnd hooks only fire during IDE sessions). Partial step on
#337's broader multi-source capture pipeline — adds a third adapter to the
``ADAPTERS`` registry after Granola.

Design constraints (from plan-344 + audit advisories A1-A3):

- Non-recursive ``iterdir()``. Subdirs ignored, hidden files (``.``-prefix)
  ignored, glob-style patterns not supported. Top-level ``source.path``
  may itself be a symlink to a directory (common for Dropbox / Drive
  mirror dirs); inner-content symlinks are ignored.
- File-size cap mirrors ``context.py:_DEFAULT_INGEST_MAX_BYTES`` (1 MiB).
  Oversized files are skipped with a stderr warning; their mtime is NOT
  added to the watermark-candidate set so they remain seen-but-not-
  ingested next run.
- Watermark stores the max ISO 8601 mtime seen. Edge case: in-place file
  edit advances mtime → re-ingest (documented as expected; operator
  workaround is ``cp`` over in-place edit).
- Graceful empty-return on config errors (missing path, not a directory,
  unreadable). Mirrors the ``_run_source`` "never raise to caller"
  discipline at ``cli/sync_and_brief_cli.py:171-216``.
- Corrupt watermark file (per A3) is treated the same as missing → log
  + start from epoch. Mirrors ``granola.py:140-148``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_EXTENSIONS: tuple[str, ...] = (".md", ".txt", ".json")
_DEFAULT_SOURCE_TYPE_LABEL = "planning"
_DEFAULT_MAX_FILE_BYTES = 1024 * 1024  # 1 MiB; mirrors context._DEFAULT_INGEST_MAX_BYTES


class LocalDirectoryAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "local_directory"

    def __init__(self) -> None:
        self._pending_watermark: str | None = None
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        """Pull new files since the last confirmed watermark.

        Returns a list of ingest-ready payloads. Empty list when:
        - ``source.path`` is missing / not a directory / not readable
          (logged to stderr; never raised — matches ``_run_source``'s
          "never raise to caller" framing)
        - directory contains no qualifying files newer than the watermark

        Per A2 audit advisory: gracefully empty-return rather than
        raising a dedicated error class. The CLI catches any unexpected
        raise via its broad ``except Exception`` but we should never
        reach that path under normal config-error conditions.
        """
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / f"{self.name}.json"

        source_path_raw = config.get("path") or ""
        source_path = self._resolve_path(source_path_raw)
        if source_path is None:
            self._pending_watermark = None
            return []

        extensions = self._coerce_extensions(config.get("extensions"))
        source_type_label = str(config.get("source_type_label") or _DEFAULT_SOURCE_TYPE_LABEL)
        max_bytes = int(config.get("max_file_bytes") or _DEFAULT_MAX_FILE_BYTES)

        last_synced = _read_watermark(self._watermark_path)

        payloads: list[dict] = []
        mtime_candidates: list[str] = []
        for child in sorted(source_path.iterdir()):
            if not _eligible(child, extensions):
                continue
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=UTC).isoformat()
            except OSError as exc:
                logger.warning("[local_directory] stat failed for %s: %s", child, exc)
                continue
            if last_synced is not None and mtime <= last_synced:
                continue
            payload = self._read_and_transform(
                child,
                mtime=mtime,
                source_type_label=source_type_label,
                max_bytes=max_bytes,
            )
            if payload is None:
                # Oversized / unreadable: skip without adding mtime to
                # the watermark candidate set so a future run retries.
                continue
            payloads.append(payload)
            mtime_candidates.append(mtime)

        if mtime_candidates:
            self._pending_watermark = max(mtime_candidates)
        else:
            self._pending_watermark = None
        return payloads

    def confirm_watermark(self) -> None:
        """Persist the pending watermark. No-op if the last pull returned
        no items or if pull() was never called."""
        if self._pending_watermark is None or self._watermark_path is None:
            return
        _write_watermark(self._watermark_path, self._pending_watermark)
        self._pending_watermark = None

    # ── private helpers ───────────────────────────────────────────────

    def _resolve_path(self, raw: str) -> Path | None:
        """Expand ``~``, resolve to absolute, verify it's a readable
        directory. Per audit A1: top-level symlink to a directory is
        accepted (common for cross-tool mirror dirs).
        """
        if not raw:
            print(
                "[local_directory] config missing 'path'; skipping.",
                file=sys.stderr,
            )
            return None
        try:
            resolved = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            print(
                f"[local_directory] could not resolve path {raw!r}: {exc}",
                file=sys.stderr,
            )
            return None
        if not resolved.exists():
            print(
                f"[local_directory] path does not exist: {resolved}",
                file=sys.stderr,
            )
            return None
        if not resolved.is_dir():
            print(
                f"[local_directory] path is not a directory: {resolved}",
                file=sys.stderr,
            )
            return None
        return resolved

    def _coerce_extensions(self, raw: object) -> tuple[str, ...]:
        """Normalize the extensions config: lowercase, dot-prefixed,
        de-duplicated. Falls back to defaults on bad input."""
        if not raw:
            return _DEFAULT_EXTENSIONS
        if not isinstance(raw, (list, tuple)):
            return _DEFAULT_EXTENSIONS
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            ext = item.lower().strip()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in out:
                out.append(ext)
        return tuple(out) if out else _DEFAULT_EXTENSIONS

    def _read_and_transform(
        self,
        path: Path,
        *,
        mtime: str,
        source_type_label: str,
        max_bytes: int,
    ) -> dict | None:
        """Read the file and emit an ingest payload. Returns None on
        oversized / unreadable so the caller can skip the watermark
        advance for that file."""
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("[local_directory] stat failed for %s: %s", path, exc)
            return None
        if size > max_bytes:
            print(
                f"[local_directory] skipping oversized file ({size} bytes > {max_bytes}): {path}",
                file=sys.stderr,
            )
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(
                f"[local_directory] could not read {path}: {exc}",
                file=sys.stderr,
            )
            return None
        return _transform(path, content, mtime=mtime, source_type_label=source_type_label)


def _eligible(child: Path, extensions: tuple[str, ...]) -> bool:
    """File qualifies if: regular file (or symlink to one), not hidden,
    extension matches the allow-list."""
    if child.name.startswith("."):
        return False
    if not child.is_file():
        return False
    return child.suffix.lower() in extensions


def _read_watermark(path: Path) -> str | None:
    """Read prior watermark or None. Corrupt / missing file → None
    (mirrors granola._read_watermark error semantics per A3)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = data.get("last_synced_at")
        return str(result) if result else None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[local_directory] watermark unreadable at %s: %s", path, exc)
        return None


def _write_watermark(path: Path, last_synced_at: str) -> None:
    payload = {"last_synced_at": last_synced_at, "written_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def _path_token(path: Path) -> str:
    """Stable opaque token for the span_id derived from the file path.

    Sha256 first 16 chars of the absolute path. Avoids embedding the
    full filesystem path in the span_id (which would leak the operator's
    home-dir layout into the ledger), while still being deterministic so
    re-ingesting the same file gives the same span_id."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def _transform(path: Path, content: str, *, mtime: str, source_type_label: str) -> dict:
    """Map a local-directory file to a bicameral ingest payload.

    The full file content is emitted as ``span.text``. The operator
    decides the granularity by choosing what to drop in the directory.
    Smarter segmentation (markdown sections, frontmatter parsing) is
    explicitly out of scope per plan-344 non-goals.
    """
    stem = path.stem
    token = _path_token(path)
    meeting_date = mtime[:10] if mtime else ""
    return {
        "query": stem or token,
        "repo": "",
        "commit_hash": "",
        "analyzed_at": mtime,
        "mappings": [
            {
                "span": {
                    "span_id": f"local-{token}",
                    "source_type": source_type_label,
                    "text": content,
                    "speaker": "",
                    "source_ref": str(path),
                    "meeting_date": meeting_date,
                },
                "intent": stem or token,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }
