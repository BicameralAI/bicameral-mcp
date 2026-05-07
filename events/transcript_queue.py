"""Pending-transcripts queue (#156).

The SessionEnd hook copies the parent session's transcript to
``<repo>/.bicameral/pending-transcripts/<session_id>.jsonl``. The next
session's preflight Step 3.5 reads the queue FIFO, surfaces corrections
as ask-findings, then archives processed files to
``<repo>/.bicameral/processed-transcripts/``.

This module is the single source of truth for queue layout. Future
team-server config may override retention and merge policy by reading
this module's defaults.
"""

from __future__ import annotations

from pathlib import Path

PENDING_DIR = "pending-transcripts"
PROCESSED_DIR = "processed-transcripts"


def _pending_root(repo_path: str) -> Path:
    return Path(repo_path) / ".bicameral" / PENDING_DIR


def _processed_root(repo_path: str) -> Path:
    return Path(repo_path) / ".bicameral" / PROCESSED_DIR


def write_pending(repo_path: str, session_id: str, transcript_path: str) -> Path | None:
    """Copy `transcript_path` to the pending-transcripts queue.

    Returns the queue path on success, None on fail-soft (transcript
    missing, repo lacks .bicameral/, write fails)."""
    src = Path(transcript_path)
    if not src.is_file():
        return None
    bicameral = Path(repo_path) / ".bicameral"
    if not bicameral.is_dir():
        return None
    pending = _pending_root(repo_path)
    pending.mkdir(parents=True, exist_ok=True)
    dst = pending / f"{session_id}.jsonl"
    dst.write_bytes(src.read_bytes())
    return dst


def list_pending_fifo(repo_path: str) -> list[Path]:
    """Return pending transcript files ordered oldest-first by mtime.
    Used by Step 0 of capture-corrections (Phase 2) to drain the queue."""
    pending = _pending_root(repo_path)
    if not pending.is_dir():
        return []
    return sorted(pending.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def archive_processed(repo_path: str, pending_path: Path) -> Path:
    """Move `pending_path` from pending/ to processed/. Idempotent — if
    the destination already exists (re-replay), overwrites."""
    archive = _processed_root(repo_path)
    archive.mkdir(parents=True, exist_ok=True)
    dst = archive / pending_path.name
    if dst.exists():
        dst.unlink()
    pending_path.rename(dst)
    return dst
