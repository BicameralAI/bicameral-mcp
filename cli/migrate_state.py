"""`bicameral-mcp migrate-state` — relocate project-scoped state under #368.

Moves every project-scoped state file from `<repo>/.bicameral/` (and the
v0.15.x user-global `~/.bicameral/ledger.db`) into the locator-resolved
project dir at `~/.bicameral/projects/<id>/`. Idempotent, archives on
collision, cleans up empty source directories after success.

Also partitions a pre-R4 `<repo>/.bicameral/config.yaml` by routing each
key to either the (trimmed) team config or the new `operator.yaml` under
the project dir per `context._CONFIG_KEY_ROUTING`. Unknown keys stay in
config.yaml with a warning logged — forward-compat for future versions.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

# Single files that move from <repo>/.bicameral/<src> → <project_dir>/<basename(src)>.
_FILE_SOURCES: tuple[str, ...] = (
    ".bicameral/ledger.db",
    ".bicameral/local/code-graph.db",
    ".bicameral/local/code-graph.db-shm",
    ".bicameral/local/code-graph.db-wal",
    ".bicameral/local/bm25_index.pkl",
    ".bicameral/local/watermark",
)

# Directories whose contents move file-by-file into <project_dir>/<dir-name>/.
# After all files migrate, the source directory is removed if empty.
_DIR_SOURCES: tuple[str, ...] = (
    ".bicameral/pending-transcripts",
    ".bicameral/processed-transcripts",
)

_LEGACY_USER_GLOBAL_LEDGER = Path.home() / ".bicameral" / "ledger.db"


def _iso8601_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _files_byte_equal(a: Path, b: Path) -> bool:
    """Compare two file contents byte-for-byte. Returns False when sizes
    differ or either side is unreadable (treat as "collision unsafe")."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _archive(dest: Path, archive_dir: Path) -> Path:
    """Move ``dest`` to ``<archive_dir>/<dest.name>.<iso8601>.bak`` and
    return the archive path. Caller ensures ``dest`` exists."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{dest.name}.{_iso8601_stamp()}.bak"
    shutil.move(str(dest), str(archive_path))
    return archive_path


def _plan_file_moves(repo: Path, project_dir: Path) -> list[tuple[Path, Path]]:
    """Plan the (src, dest) pairs for single files (not dir contents)."""
    plan: list[tuple[Path, Path]] = []
    for rel in _FILE_SOURCES:
        src = repo / rel
        if src.exists() and src.is_file():
            plan.append((src, project_dir / src.name))
    return plan


def _plan_dir_contents_moves(repo: Path, project_dir: Path) -> list[tuple[Path, Path]]:
    """Plan (src_file, dest_file) pairs for every file inside the
    pending/processed-transcripts source dirs."""
    plan: list[tuple[Path, Path]] = []
    for rel in _DIR_SOURCES:
        src_dir = repo / rel
        if not src_dir.is_dir():
            continue
        dest_dir = project_dir / src_dir.name
        for child in src_dir.iterdir():
            if child.is_file():
                plan.append((child, dest_dir / child.name))
    return plan


def _maybe_legacy_user_global_move(project_dir: Path) -> tuple[Path, Path] | None:
    """If ``~/.bicameral/ledger.db`` exists and the locator-resolved
    ledger doesn't yet, plan a one-shot relocation. Returns None when
    nothing to migrate (file absent, or some other project already
    claimed it via a fresh `~/.bicameral/projects/<id>/ledger.db`).
    """
    src = _LEGACY_USER_GLOBAL_LEDGER
    if not src.is_file():
        return None
    dest = project_dir / "ledger.db"
    if dest.exists():
        # First project to migrate already claimed it — leave the source
        # alone (it may also have been already deleted in a prior run).
        return None
    return (src, dest)


def _partition_config_yaml(
    repo: Path, project_dir: Path, *, dry_run: bool
) -> tuple[bool, list[str]]:
    """R4 partition of a pre-split `.bicameral/config.yaml`.

    Splits each key into either the trimmed config.yaml (team-identity)
    or operator.yaml (per-operator) per `context._CONFIG_KEY_ROUTING`.
    Returns ``(did_something, warnings)``. No-ops if the file is missing
    or already split (no operator-routed keys present).
    """
    config_path = repo / ".bicameral" / "config.yaml"
    if not config_path.is_file():
        return False, []

    try:
        import yaml
    except ImportError:
        return False, ["yaml unavailable — leaving config.yaml untouched"]

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — fail-soft
        return False, [f"config.yaml unparseable ({exc}) — leaving untouched"]
    if not isinstance(loaded, dict):
        return False, ["config.yaml top-level is not a mapping — leaving untouched"]

    from context import _CONFIG_KEY_ROUTING

    team_out: dict = {}
    operator_out: dict = {}
    warnings: list[str] = []

    def _route(flat_key: str) -> str | None:
        return _CONFIG_KEY_ROUTING.get(flat_key)

    # Top-level scalar keys
    for key, value in loaded.items():
        if key == "team" and isinstance(value, dict):
            continue  # nested handling below
        routing = _route(key)
        if routing == "operator":
            operator_out[key] = value
        elif routing == "team":
            team_out[key] = value
        else:
            warnings.append(f"unknown key in config.yaml — left in team file: {key!r}")
            team_out[key] = value

    # `team.*` nested keys split per their own routing.
    nested = loaded.get("team")
    if isinstance(nested, dict):
        team_block: dict = {}
        op_team_block: dict = {}
        for sub_key, sub_value in nested.items():
            routing = _route(f"team.{sub_key}")
            if routing == "operator":
                op_team_block[sub_key] = sub_value
            elif routing == "team":
                team_block[sub_key] = sub_value
            else:
                warnings.append(
                    f"unknown nested key in config.yaml — left in team file: team.{sub_key!r}"
                )
                team_block[sub_key] = sub_value
        if team_block:
            team_out["team"] = team_block
        if op_team_block:
            operator_out["team"] = op_team_block

    has_operator_payload = bool(operator_out)
    did_something = has_operator_payload  # nothing to do if every key is already team-side

    if dry_run:
        return did_something, warnings

    if has_operator_payload:
        operator_path = project_dir / "operator.yaml"
        operator_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with any pre-existing operator.yaml so a partial prior
        # migration doesn't clobber operator-only keys the wizard wrote.
        if operator_path.exists():
            try:
                existing = yaml.safe_load(operator_path.read_text(encoding="utf-8")) or {}
                if isinstance(existing, dict):
                    # Existing values WIN — they're the operator's chosen state.
                    merged = {**operator_out, **existing}
                    operator_out = merged
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"existing operator.yaml unreadable ({exc}); overwriting")
        operator_path.write_text(yaml.safe_dump(operator_out, sort_keys=False), encoding="utf-8")

    # Trimmed team file: rewrite even when nothing moved out, so subsequent
    # runs see a consistent shape. Skip the write entirely if the team body
    # would be identical to the on-disk contents (idempotency).
    new_team_text = yaml.safe_dump(team_out, sort_keys=False) if team_out else ""
    cur_text = config_path.read_text(encoding="utf-8")
    if new_team_text != cur_text and has_operator_payload:
        config_path.write_text(new_team_text, encoding="utf-8")

    return did_something, warnings


_LEGACY_SURREAL_SCHEME = "surrealkv://"


def _is_repo_local_path(path_str: str, repo: Path) -> bool:
    """Return True when ``path_str`` resolves inside ``<repo>/.bicameral/``.

    Used to identify legacy in-repo overrides written by setup_wizard
    before #368 shipped. Resolves both sides so symlinked repos and
    relative paths behave the same way. Unresolvable paths (e.g.,
    malformed) return False — preserve, don't clobber.
    """
    if not path_str:
        return False
    try:
        candidate = Path(path_str).expanduser().resolve(strict=False)
        legacy_root = (repo / ".bicameral").resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        candidate.relative_to(legacy_root)
    except ValueError:
        return False
    return True


def _is_legacy_repo_local_surreal_url(url: str, repo: Path) -> bool:
    """Return True iff ``url`` is a ``surrealkv://`` URL pointing into
    ``<repo>/.bicameral/``. ``memory://`` and any URL pointing outside
    the repo are legitimate operator overrides — preserve them.
    """
    if not url.startswith(_LEGACY_SURREAL_SCHEME):
        return False
    path_part = url[len(_LEGACY_SURREAL_SCHEME) :]
    return _is_repo_local_path(path_part, repo)


def _rewrite_mcp_json(repo: Path, *, dry_run: bool) -> tuple[bool, list[str]]:
    """Drop legacy in-repo ``SURREAL_URL`` / ``CODE_LOCATOR_SQLITE_DB``
    overrides from ``<repo>/.mcp.json`` so the locator can resolve the
    canonical paths at next startup (#494).

    Only the ``mcpServers.bicameral.env`` block is touched, and only
    when the value resolves into ``<repo>/.bicameral/``. Anything else
    (other servers, other env keys, ``memory://``, paths outside the
    repo) is preserved untouched.

    Returns ``(did_rewrite, warnings)``. No-op when the file is absent,
    unparseable, or already clean.
    """
    config_path = repo / ".mcp.json"
    if not config_path.is_file():
        return False, []

    raw = config_path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, [f".mcp.json unparseable ({exc}) — leaving untouched"]
    if not isinstance(loaded, dict):
        return False, [".mcp.json top-level is not an object — leaving untouched"]

    servers = loaded.get("mcpServers")
    if not isinstance(servers, dict):
        return False, []
    bicameral = servers.get("bicameral")
    if not isinstance(bicameral, dict):
        return False, []
    env = bicameral.get("env")
    if not isinstance(env, dict):
        return False, []

    removed_keys: list[str] = []

    surreal_url = env.get("SURREAL_URL")
    if isinstance(surreal_url, str) and _is_legacy_repo_local_surreal_url(surreal_url, repo):
        del env["SURREAL_URL"]
        removed_keys.append("SURREAL_URL")

    code_locator_db = env.get("CODE_LOCATOR_SQLITE_DB")
    if isinstance(code_locator_db, str) and _is_repo_local_path(code_locator_db, repo):
        del env["CODE_LOCATOR_SQLITE_DB"]
        removed_keys.append("CODE_LOCATOR_SQLITE_DB")

    if not removed_keys:
        return False, []

    if not dry_run:
        config_path.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")

    return True, [f"dropped legacy env keys from .mcp.json: {', '.join(removed_keys)}"]


def _execute_plan(
    plan: list[tuple[Path, Path]],
    archive_dir: Path,
    *,
    dry_run: bool,
) -> list[tuple[str, Path, Path, Path | None]]:
    """Move each (src, dest) pair. Archive collisions when bytes differ.
    Returns a list of (action, src, dest, archive_path) records for
    summary printing."""
    log: list[tuple[str, Path, Path, Path | None]] = []
    for src, dest in plan:
        if not src.exists():
            continue
        if dest.exists():
            if _files_byte_equal(src, dest):
                if not dry_run:
                    src.unlink()
                log.append(("dedup", src, dest, None))
                continue
            archive_path: Path | None = None
            if not dry_run:
                archive_path = _archive(dest, archive_dir)
            log.append(("archive+move", src, dest, archive_path))
        else:
            log.append(("move", src, dest, None))
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    return log


def _cleanup_empty_source_dirs(repo: Path, *, dry_run: bool) -> list[Path]:
    """Remove `<repo>/.bicameral/{local,pending-transcripts,processed-transcripts}`
    if they're empty after the migration. Returns the list of removed dirs."""
    removed: list[Path] = []
    for rel in (".bicameral/local", *_DIR_SOURCES):
        candidate = repo / rel
        if candidate.is_dir() and not any(candidate.iterdir()):
            removed.append(candidate)
            if not dry_run:
                candidate.rmdir()
    return removed


def _print_summary(
    log: list[tuple[str, Path, Path, Path | None]],
    empty_dirs_removed: list[Path],
    config_did_split: bool,
    config_warnings: list[str],
    mcp_json_did_rewrite: bool,
    mcp_json_warnings: list[str],
    *,
    dry_run: bool,
) -> None:
    if (
        not log
        and not empty_dirs_removed
        and not config_did_split
        and not mcp_json_did_rewrite
        and not mcp_json_warnings
        and not config_warnings
    ):
        print("  Nothing to migrate.")
        return
    prefix = "[dry-run] " if dry_run else ""
    for action, src, dest, archive_path in log:
        if action == "dedup":
            print(f"  {prefix}skip (identical bytes) — removed {src} → kept {dest}")
        elif action == "archive+move":
            print(f"  {prefix}archive {dest} → {archive_path}")
            print(f"  {prefix}move {src} → {dest}")
        else:
            print(f"  {prefix}move {src} → {dest}")
    for d in empty_dirs_removed:
        print(f"  {prefix}rmdir {d}")
    if config_did_split:
        print(f"  {prefix}split config.yaml → team-identity + operator.yaml")
    for w in config_warnings:
        print(f"  WARN: {w}")
    for w in mcp_json_warnings:
        # Both informational ("dropped …") and failure ("unparseable") flow through
        # the same channel — the message itself carries enough context.
        print(f"  {prefix}{w}" if w.startswith("dropped") else f"  WARN: {w}")
    # R4 deferred-ephemeral notice (decision:e3xz4c4ji4x7lm3lvq4k).
    print(
        "  Note: ~/.bicameral/projects/ persists per home directory. If you run "
        "bicameral inside an ephemeral container (Codespaces, devcontainer, CI), "
        "state will be lost at teardown. Full ephemeral-environment support is "
        "tracked separately under v0.16.1/v0.17."
    )


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bicameral-mcp migrate-state",
        description="Move project-scoped state into ~/.bicameral/projects/<id>/ (#368).",
    )
    p.add_argument("--repo", default=None, metavar="PATH", help="repo path (default: cwd)")
    p.add_argument("--auto", action="store_true", help="non-interactive — skip the confirm prompt")
    p.add_argument("--dry-run", action="store_true", help="plan only; write nothing")
    p.add_argument(
        "--archive-dir",
        default=None,
        metavar="PATH",
        help="collisions land here (default: ~/.bicameral/archive/<project-id>/)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else Path.cwd()

    try:
        from ledger_locator import (
            ProjectIdResolutionError,
            project_dir_for,
            project_id_for,
        )
    except ImportError as exc:  # pragma: no cover
        print(f"  ERROR: ledger_locator unavailable: {exc}")
        return 2

    try:
        project_id = project_id_for(repo)
        project_dir = project_dir_for(repo)
    except ProjectIdResolutionError as exc:
        print(f"  ERROR: {exc}")
        return 2

    archive_dir = (
        Path(args.archive_dir).expanduser().resolve()
        if args.archive_dir
        else Path.home() / ".bicameral" / "archive" / project_id
    )

    plan = _plan_file_moves(repo, project_dir)
    plan += _plan_dir_contents_moves(repo, project_dir)
    legacy = _maybe_legacy_user_global_move(project_dir)
    if legacy is not None:
        plan.append(legacy)

    if plan:
        project_dir.mkdir(parents=True, exist_ok=True)

    if not args.auto and not args.dry_run and plan:
        # Best-effort confirmation. Stdin not a tty → fall through.
        try:
            response = (
                input(f"  Migrate {len(plan)} files into {project_dir}? [y/N]: ").strip().lower()
            )
        except (EOFError, OSError):
            response = "y"
        if response not in ("y", "yes"):
            print("  Aborted (run with --auto to skip the prompt).")
            return 1

    log = _execute_plan(plan, archive_dir, dry_run=args.dry_run)
    config_did_split, config_warnings = _partition_config_yaml(
        repo, project_dir, dry_run=args.dry_run
    )
    mcp_json_did_rewrite, mcp_json_warnings = _rewrite_mcp_json(repo, dry_run=args.dry_run)
    empty_dirs_removed = _cleanup_empty_source_dirs(repo, dry_run=args.dry_run)

    _print_summary(
        log,
        empty_dirs_removed,
        config_did_split,
        config_warnings,
        mcp_json_did_rewrite,
        mcp_json_warnings,
        dry_run=args.dry_run,
    )
    return 0
