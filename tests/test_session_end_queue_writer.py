"""Behavioral tests for #156 SessionEnd queue writer + archive helper.

Covers:
  - ``scripts/hooks/session_end_queue_writer.py`` (SessionEnd hook entrypoint)
  - ``events/transcript_queue.py`` (queue layout module)
  - ``scripts/hooks/transcript_archive.py`` (CLI helper used by Phase 2 SKILL.md)

Per ``doctrine-test-functionality``: every test invokes the unit and asserts
on observable output / state. No presence-only assertions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRITER = REPO_ROOT / "scripts" / "hooks" / "session_end_queue_writer.py"
ARCHIVER = REPO_ROOT / "scripts" / "hooks" / "transcript_archive.py"


def _run_writer(stdin_payload: str | bytes, cwd: Path) -> subprocess.CompletedProcess[str]:
    if isinstance(stdin_payload, str):
        stdin_payload = stdin_payload.encode("utf-8")
    return subprocess.run(
        [sys.executable, str(WRITER)],
        input=stdin_payload,
        cwd=str(cwd),
        capture_output=True,
        timeout=15,
    )


def _make_repo(tmp_path: Path, with_bicameral: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # #368 Phase 2B-ii: transcript_queue delegates to the locator which
    # requires a git repo. Init one so the locator can resolve the
    # project-scoped pending-transcripts dir.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if with_bicameral:
        (repo / ".bicameral").mkdir()
    return repo


def _make_transcript(tmp_path: Path, content: str = '{"type":"user","text":"hi"}\n') -> Path:
    src = tmp_path / "transcript.jsonl"
    src.write_text(content, encoding="utf-8")
    return src


def test_writer_copies_transcript_to_pending_dir(tmp_path: Path) -> None:
    from events.transcript_queue import _pending_root

    repo = _make_repo(tmp_path)
    src = _make_transcript(tmp_path, content='{"a":1}\n{"b":2}\n')
    payload = json.dumps({"session_id": "abc", "transcript_path": str(src), "cwd": str(repo)})

    result = _run_writer(payload, cwd=repo)

    assert result.returncode == 0
    dst = _pending_root(str(repo)) / "abc.jsonl"
    assert dst.is_file()
    assert dst.read_bytes() == src.read_bytes()


def test_writer_no_op_when_no_bicameral_dir(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_bicameral=False)
    src = _make_transcript(tmp_path)
    payload = json.dumps({"session_id": "x", "transcript_path": str(src), "cwd": str(repo)})

    result = _run_writer(payload, cwd=repo)

    assert result.returncode == 0
    assert not (repo / ".bicameral").exists()


def test_writer_no_op_when_transcript_missing(tmp_path: Path) -> None:
    from events.transcript_queue import _pending_root

    repo = _make_repo(tmp_path)
    payload = json.dumps(
        {"session_id": "x", "transcript_path": str(tmp_path / "nope.jsonl"), "cwd": str(repo)}
    )

    result = _run_writer(payload, cwd=repo)

    assert result.returncode == 0
    pending = _pending_root(str(repo))
    assert not pending.exists() or list(pending.iterdir()) == []


def test_writer_handles_malformed_stdin(tmp_path: Path) -> None:
    from events.transcript_queue import _pending_root

    repo = _make_repo(tmp_path)

    result = _run_writer("not json at all", cwd=repo)

    assert result.returncode == 0
    pending = _pending_root(str(repo))
    assert not pending.exists() or list(pending.iterdir()) == []


def test_writer_uses_uuid_when_session_id_missing(tmp_path: Path) -> None:
    from events.transcript_queue import _pending_root

    repo = _make_repo(tmp_path)
    src = _make_transcript(tmp_path)
    payload = json.dumps({"transcript_path": str(src), "cwd": str(repo)})

    result = _run_writer(payload, cwd=repo)

    assert result.returncode == 0
    pending = _pending_root(str(repo))
    files = list(pending.iterdir())
    assert len(files) == 1
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
    assert uuid_re.match(files[0].name), f"got filename {files[0].name!r}"


def test_list_pending_fifo_orders_by_mtime(tmp_path: Path) -> None:
    from events.transcript_queue import _pending_root, list_pending_fifo

    repo = _make_repo(tmp_path)
    pending = _pending_root(str(repo))
    pending.mkdir(parents=True)
    names = ["c.jsonl", "a.jsonl", "b.jsonl"]
    base = time.time() - 100
    for i, name in enumerate(names):
        p = pending / name
        p.write_text(f"file {name}", encoding="utf-8")
        os.utime(p, (base + i, base + i))

    result = list_pending_fifo(str(repo))

    assert [p.name for p in result] == names


def test_archive_processed_moves_pending_to_processed(tmp_path: Path) -> None:
    from events.transcript_queue import archive_processed, write_pending

    repo = _make_repo(tmp_path)
    src = _make_transcript(tmp_path, content="payload-A")
    pending_path = write_pending(str(repo), "sess1", str(src))
    assert pending_path is not None and pending_path.is_file()

    dst = archive_processed(str(repo), pending_path)

    assert not pending_path.exists()
    assert dst.is_file()
    assert dst.name == "sess1.jsonl"
    assert dst.read_text(encoding="utf-8") == "payload-A"
    from events.transcript_queue import _processed_root

    assert dst.parent == _processed_root(str(repo))


def test_archive_processed_idempotent_on_replay(tmp_path: Path) -> None:
    from events.transcript_queue import archive_processed, write_pending

    repo = _make_repo(tmp_path)

    src1 = _make_transcript(tmp_path, content="first")
    pending1 = write_pending(str(repo), "sess-replay", str(src1))
    assert pending1 is not None
    archive_processed(str(repo), pending1)

    src2 = tmp_path / "transcript2.jsonl"
    src2.write_text("second", encoding="utf-8")
    pending2 = write_pending(str(repo), "sess-replay", str(src2))
    assert pending2 is not None
    dst = archive_processed(str(repo), pending2)

    assert dst.read_text(encoding="utf-8") == "second"
    assert not pending2.exists()


def test_transcript_archive_invokes_archive_processed(tmp_path: Path) -> None:
    from events.transcript_queue import write_pending

    repo = _make_repo(tmp_path)
    src = _make_transcript(tmp_path, content="archived-content")
    pending = write_pending(str(repo), "sess-cli", str(src))
    assert pending is not None and pending.is_file()

    happy = subprocess.run(
        [sys.executable, str(ARCHIVER), "sess-cli.jsonl"],
        cwd=str(repo),
        capture_output=True,
        timeout=15,
    )

    from events.transcript_queue import _processed_root

    assert happy.returncode == 0, happy.stderr.decode("utf-8", errors="replace")
    assert not pending.exists()
    archived = _processed_root(str(repo)) / "sess-cli.jsonl"
    assert archived.is_file()
    assert archived.read_text(encoding="utf-8") == "archived-content"

    fs_before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file())
    unsafe = subprocess.run(
        [sys.executable, str(ARCHIVER), "../etc/passwd"],
        cwd=str(repo),
        capture_output=True,
        timeout=15,
    )
    fs_after = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file())

    assert unsafe.returncode == 2
    assert fs_before == fs_after
