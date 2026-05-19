"""`bicameral-mcp gc` — reclaim project dirs whose origin no longer resolves.

Each `~/.bicameral/projects/<id>/origin.txt` names the absolute git
common-dir path the locator hashed at first resolve. When the named
path no longer exists (project deleted, relocated, or rebased), the
project dir is orphaned. `gc` lists orphans by default and deletes
them under `--delete` after a per-item prompt (or `--yes` to skip).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Literal

Status = Literal["live", "orphan", "unreadable"]


def _scan(state_root: Path) -> list[tuple[str, Path, Status, str | None]]:
    """Yield (project_id, project_dir, status, origin_text) for every
    immediate subdirectory of ``state_root``.

    - ``live`` — origin.txt points at an existing directory.
    - ``orphan`` — origin.txt parses but the named path is gone.
    - ``unreadable`` — origin.txt missing, empty, or not a regular file.
    """
    out: list[tuple[str, Path, Status, str | None]] = []
    if not state_root.is_dir():
        return out
    for child in sorted(state_root.iterdir()):
        if not child.is_dir():
            continue
        origin = child / "origin.txt"
        if not origin.is_file():
            out.append((child.name, child, "unreadable", None))
            continue
        try:
            text = origin.read_text(encoding="utf-8").strip()
        except OSError:
            out.append((child.name, child, "unreadable", None))
            continue
        if not text:
            out.append((child.name, child, "unreadable", None))
            continue
        if Path(text).is_dir():
            out.append((child.name, child, "live", text))
        else:
            out.append((child.name, child, "orphan", text))
    return out


def _print_table(rows: list[tuple[str, Path, Status, str | None]]) -> None:
    if not rows:
        print("  (no projects under ~/.bicameral/projects/)")
        return
    print(f"  {'STATUS':<10} {'PROJECT-ID':<18} ORIGIN")
    for _project_id, _path, status, origin_text in rows:
        # Pad the project id column with the first 16 chars of the dir name.
        print(f"  {status:<10} {_path.name:<18} {origin_text or '(unreadable)'}")


def _confirm_delete(project_dir: Path, status: Status, origin: str | None) -> bool:
    """Per-item prompt. Empty / `y` / `yes` confirms; anything else declines."""
    try:
        response = input(
            f"  Delete {project_dir.name} ({status} — origin={origin or 'unreadable'})? [y/N]: "
        ).strip().lower()
    except (EOFError, OSError):
        return False
    return response in ("y", "yes")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bicameral-mcp gc",
        description="List or delete orphan project dirs under ~/.bicameral/projects/ (#368).",
    )
    p.add_argument(
        "--delete",
        action="store_true",
        help="prompt to delete each orphan (and unreadable) project dir",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="under --delete: skip per-item prompts and delete every orphan/unreadable dir",
    )
    p.add_argument(
        "--state-root",
        default=None,
        metavar="PATH",
        help="override ~/.bicameral/projects/ (test fixture knob)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if args.state_root is not None:
        state_root = Path(args.state_root).expanduser().resolve()
    else:
        try:
            from ledger_locator import STATE_ROOT

            state_root = STATE_ROOT
        except ImportError as exc:  # pragma: no cover
            print(f"  ERROR: ledger_locator unavailable: {exc}")
            return 2

    rows = _scan(state_root)
    if not args.delete:
        _print_table(rows)
        return 0

    to_delete = [r for r in rows if r[2] in ("orphan", "unreadable")]
    if not to_delete:
        print("  No orphan project dirs.")
        return 0
    for _project_id, path, status, origin in to_delete:
        if args.yes or _confirm_delete(path, status, origin):
            shutil.rmtree(path, ignore_errors=False)
            print(f"  Removed {path}")
    return 0
