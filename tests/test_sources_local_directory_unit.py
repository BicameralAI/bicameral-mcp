"""Sociable unit tests for the LocalDirectorySourceAdapter (#344).

Real filesystem via ``tmp_path``; no mocks. The adapter has no
network/auth boundary so 100% sociable is correct (vs. Granola's
``_FakeClient`` for HTTP).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from events.sources import ADAPTERS, SourceAdapter
from events.sources.local_directory import (
    _DEFAULT_MAX_FILE_BYTES,
    LocalDirectoryAdapter,
)

# ── helpers ──────────────────────────────────────────────────────────


def _make_source_dir(tmp_path: Path, name: str = "captured-notes") -> Path:
    p = tmp_path / name
    p.mkdir()
    return p


def _write_file(parent: Path, filename: str, content: str = "sample brainstorm content") -> Path:
    f = parent / filename
    f.write_text(content, encoding="utf-8")
    return f


def _set_mtime(path: Path, iso: str) -> None:
    """Force a file's mtime to a specific ISO 8601 instant."""
    from datetime import datetime

    ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    os.utime(path, (ts, ts))


# ── registry + protocol conformance ───────────────────────────────────


def test_local_directory_registered_in_adapters() -> None:
    """The new adapter must be on the registry for the CLI to find it."""
    assert "local_directory" in ADAPTERS
    assert ADAPTERS["local_directory"] is LocalDirectoryAdapter


def test_local_directory_satisfies_source_adapter_protocol() -> None:
    adapter = LocalDirectoryAdapter()
    assert isinstance(adapter, SourceAdapter)


# ── config error handling ────────────────────────────────────────────


def test_pull_returns_empty_when_path_missing_from_config(tmp_path: Path, capsys) -> None:
    """No ``source.path`` → graceful empty list + stderr warning."""
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path, config={"type": "local_directory"})
    assert payloads == []
    err = capsys.readouterr().err
    assert "missing 'path'" in err


def test_pull_returns_empty_when_directory_does_not_exist(tmp_path: Path, capsys) -> None:
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(
        watermark_dir=tmp_path,
        config={"path": str(tmp_path / "nonexistent")},
    )
    assert payloads == []
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_pull_returns_empty_when_path_is_a_file(tmp_path: Path, capsys) -> None:
    """``source.path`` pointing at a file → graceful skip."""
    f = tmp_path / "not-a-dir.txt"
    f.write_text("file content")
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path, config={"path": str(f)})
    assert payloads == []
    err = capsys.readouterr().err
    assert "not a directory" in err


# ── happy-path: pull behavior ─────────────────────────────────────────


def test_pull_ingests_all_files_on_first_run(tmp_path: Path) -> None:
    """No prior watermark → every qualifying file is emitted."""
    src = _make_source_dir(tmp_path)
    _write_file(src, "note1.md")
    _write_file(src, "note2.md")
    _write_file(src, "note3.md")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(
        watermark_dir=tmp_path / "wm",
        config={"path": str(src)},
    )
    assert len(payloads) == 3
    queries = {p["query"] for p in payloads}
    assert queries == {"note1", "note2", "note3"}


def test_pull_only_returns_files_newer_than_watermark(tmp_path: Path) -> None:
    """A prior watermark filters to only newer-mtime files."""
    src = _make_source_dir(tmp_path)
    old_a = _write_file(src, "old-a.md")
    old_b = _write_file(src, "old-b.md")
    new_c = _write_file(src, "new-c.md")
    _set_mtime(old_a, "2026-05-10T00:00:00+00:00")
    _set_mtime(old_b, "2026-05-10T00:00:01+00:00")
    _set_mtime(new_c, "2026-05-14T00:00:00+00:00")

    wm = tmp_path / "wm"
    wm.mkdir()
    (wm / "local_directory.json").write_text(
        json.dumps({"last_synced_at": "2026-05-12T00:00:00+00:00"}),
        encoding="utf-8",
    )

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=wm, config={"path": str(src)})
    assert len(payloads) == 1
    assert payloads[0]["query"] == "new-c"


# ── extension filtering ──────────────────────────────────────────────


def test_pull_skips_unknown_extensions(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    _write_file(src, "note.md")
    _write_file(src, "note.txt")
    _write_file(src, "tool.exe", "binary-ish")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    queries = {p["query"] for p in payloads}
    assert queries == {"note"}  # both note.md and note.txt → same stem "note"
    # Verify .exe is not present:
    refs = {p["mappings"][0]["span"]["source_ref"] for p in payloads}
    assert not any(r.endswith(".exe") for r in refs)


def test_pull_respects_custom_extensions_config(tmp_path: Path) -> None:
    """Operator can narrow extensions via config."""
    src = _make_source_dir(tmp_path)
    _write_file(src, "md.md")
    _write_file(src, "txt.txt")
    _write_file(src, "json.json")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(
        watermark_dir=tmp_path / "wm",
        config={"path": str(src), "extensions": [".txt"]},
    )
    assert len(payloads) == 1
    assert payloads[0]["query"] == "txt"


# ── filesystem containment ───────────────────────────────────────────


def test_pull_ignores_hidden_files(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    _write_file(src, ".hidden.md")
    _write_file(src, "visible.md")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    assert len(payloads) == 1
    assert payloads[0]["query"] == "visible"


def test_pull_ignores_subdirectories(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    _write_file(src, "top.md")
    sub = src / "subdir"
    sub.mkdir()
    _write_file(sub, "nested.md")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    assert len(payloads) == 1
    assert payloads[0]["query"] == "top"


# ── size cap ─────────────────────────────────────────────────────────


def test_pull_skips_oversized_files(tmp_path: Path, capsys) -> None:
    """Files > _DEFAULT_MAX_FILE_BYTES are skipped + warned + mtime
    NOT added to watermark candidates (so a future run could retry)."""
    src = _make_source_dir(tmp_path)
    big = src / "huge.md"
    big.write_bytes(b"x" * (_DEFAULT_MAX_FILE_BYTES + 1))
    small = _write_file(src, "tiny.md", "ok")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})

    assert len(payloads) == 1
    assert payloads[0]["query"] == "tiny"

    err = capsys.readouterr().err
    assert "skipping oversized" in err

    # Watermark advance must only reflect the small file's mtime.
    adapter.confirm_watermark()
    wm_file = tmp_path / "wm" / "local_directory.json"
    assert wm_file.exists()
    wm = json.loads(wm_file.read_text(encoding="utf-8"))
    small_mtime = small.stat().st_mtime
    big_mtime = big.stat().st_mtime
    from datetime import UTC, datetime

    big_iso = datetime.fromtimestamp(big_mtime, tz=UTC).isoformat()
    small_iso = datetime.fromtimestamp(small_mtime, tz=UTC).isoformat()
    # Watermark must equal the small file's iso, never the oversized big one
    assert wm["last_synced_at"] == small_iso
    # And we explicitly do NOT want the watermark to be at-or-past big_iso
    # unless small happens to be newer (which is the test's actual contract)
    if big_iso > small_iso:
        assert wm["last_synced_at"] < big_iso


# ── watermark advancement ───────────────────────────────────────────


def test_confirm_advances_watermark_to_max_mtime(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    a = _write_file(src, "a.md")
    b = _write_file(src, "b.md")
    c = _write_file(src, "c.md")
    _set_mtime(a, "2026-05-10T00:00:00+00:00")
    _set_mtime(b, "2026-05-11T00:00:00+00:00")
    _set_mtime(c, "2026-05-14T00:00:00+00:00")

    adapter = LocalDirectoryAdapter()
    adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    adapter.confirm_watermark()

    wm_file = tmp_path / "wm" / "local_directory.json"
    assert wm_file.exists()
    wm = json.loads(wm_file.read_text(encoding="utf-8"))
    assert wm["last_synced_at"] == "2026-05-14T00:00:00+00:00"


def test_pull_no_new_items_does_not_create_watermark_file(tmp_path: Path) -> None:
    """Empty pull → confirm is a no-op; watermark file is NOT created."""
    src = _make_source_dir(tmp_path)
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    adapter.confirm_watermark()
    assert payloads == []
    assert not (tmp_path / "wm" / "local_directory.json").exists()


# ── source_type_label override ───────────────────────────────────────


def test_pull_respects_source_type_label_override(tmp_path: Path) -> None:
    """Operator-set ``source_type_label`` flows through to the emitted span."""
    src = _make_source_dir(tmp_path)
    _write_file(src, "note.md")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(
        watermark_dir=tmp_path / "wm",
        config={"path": str(src), "source_type_label": "design-doc"},
    )
    assert len(payloads) == 1
    assert payloads[0]["mappings"][0]["span"]["source_type"] == "design-doc"


def test_pull_default_source_type_label_is_planning(tmp_path: Path) -> None:
    src = _make_source_dir(tmp_path)
    _write_file(src, "note.md")
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    assert payloads[0]["mappings"][0]["span"]["source_type"] == "planning"


# ── corrupt watermark (A3 advisory) ──────────────────────────────────


def test_pull_treats_corrupt_watermark_as_missing(tmp_path: Path) -> None:
    """Per audit A3: corrupt watermark file → log + start from epoch
    (not crash, not skip). Mirrors Granola's _read_watermark semantics."""
    src = _make_source_dir(tmp_path)
    _write_file(src, "fresh.md")

    wm = tmp_path / "wm"
    wm.mkdir()
    (wm / "local_directory.json").write_text("{ this is not valid json", encoding="utf-8")

    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=wm, config={"path": str(src)})
    # Treated as no-watermark → file is emitted
    assert len(payloads) == 1
    assert payloads[0]["query"] == "fresh"


# ── span_id stability ────────────────────────────────────────────────


def test_pull_emits_stable_span_id_for_same_file_path(tmp_path: Path) -> None:
    """Re-ingesting the same file path produces the same span_id
    (deterministic via sha256(absolute_path)[:16])."""
    src = _make_source_dir(tmp_path)
    f = _write_file(src, "note.md")

    adapter1 = LocalDirectoryAdapter()
    payloads1 = adapter1.pull(watermark_dir=tmp_path / "wm1", config={"path": str(src)})

    # Touch file to advance mtime — but span_id should still be path-derived
    _set_mtime(f, "2026-05-15T00:00:00+00:00")

    adapter2 = LocalDirectoryAdapter()
    payloads2 = adapter2.pull(watermark_dir=tmp_path / "wm2", config={"path": str(src)})

    assert (
        payloads1[0]["mappings"][0]["span"]["span_id"]
        == payloads2[0]["mappings"][0]["span"]["span_id"]
    )
    assert payloads1[0]["mappings"][0]["span"]["span_id"].startswith("local-")


# ── parametrize-style: span content matches file content ─────────────


@pytest.mark.parametrize(
    "filename,content",
    [
        ("simple.md", "Decision: use SurrealDB for the ledger."),
        ("multi-line.txt", "line 1\nline 2\nline 3\n"),
        ("with-emoji.md", "We agreed (✓) on the auth flow."),
    ],
)
def test_pull_preserves_file_content_verbatim(tmp_path: Path, filename: str, content: str) -> None:
    src = _make_source_dir(tmp_path)
    _write_file(src, filename, content)
    adapter = LocalDirectoryAdapter()
    payloads = adapter.pull(watermark_dir=tmp_path / "wm", config={"path": str(src)})
    assert payloads[0]["mappings"][0]["span"]["text"] == content
