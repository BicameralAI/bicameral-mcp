"""Handler for bicameral.update — check for and apply recommended updates.

Recommended version is controlled via a RECOMMENDED_VERSION file in the repo
root. This is intentionally separate from the PyPI latest release — not every
release needs to be pushed to testers.

Update check is cached at ~/.bicameral/update-check.json with a 1-hour TTL to
avoid latency on every tool call.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_RECOMMENDED_VERSION_URL = (
    "https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION"
)
_CACHE_PATH = os.path.expanduser("~/.bicameral/update-check.json")
_CACHE_TTL_SECONDS = 3600  # 1 hour


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_recommended_version() -> Optional[str]:
    """Fetch RECOMMENDED_VERSION from GitHub with a 1-hour cache."""
    cache = _load_cache()
    now = time.time()

    if cache.get("fetched_at", 0) + _CACHE_TTL_SECONDS > now:
        return cache.get("recommended_version")

    try:
        with urllib.request.urlopen(_RECOMMENDED_VERSION_URL, timeout=3) as resp:
            version = resp.read().decode().strip()
        _save_cache({"recommended_version": version, "fetched_at": now})
        return version
    except Exception as exc:
        logger.debug("[update] version check failed: %s", exc)
        # Return stale cache value rather than nothing
        return cache.get("recommended_version")


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def get_update_notice(current_version: str) -> dict | None:
    """Return an _update block if a recommended update is available, else None."""
    recommended = _fetch_recommended_version()
    if not recommended:
        return None
    if _parse_version(recommended) <= _parse_version(current_version):
        return None
    return {
        "recommended_version": recommended,
        "current_version": current_version,
        "action_required": (
            f"Ask the user: 'bicameral-mcp v{recommended} is available "
            f"(you are on v{current_version}) — upgrade now? (yes/no)'. "
            "If yes, call bicameral.update {\"action\": \"apply\"}."
        ),
    }


_MIGRATION_SCRIPT = """
import asyncio, json, sys

async def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        from ledger.adapter import SurrealDBLedgerAdapter
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        if not getattr(adapter, "_pending_destructive", None):
            print(json.dumps({"migrated": False}))
            return
        from handlers.reset import _get_cursors, _wipe_all
        cursors = await _get_cursors(adapter, repo)
        replay_plan = [
            {
                "source_type": str(c.get("source_type", "")),
                "source_scope": str(c.get("source_scope", "")),
                "last_source_ref": str(c.get("last_source_ref", "")),
            }
            for c in cursors
        ]
        await adapter.force_migrate()
        await _wipe_all(adapter, repo)
        print(json.dumps({"migrated": True, "cursors_wiped": len(cursors), "replay_plan": replay_plan}))
    except Exception as exc:
        print(json.dumps({"migrated": False, "error": str(exc)}))

asyncio.run(main())
"""


def _apply_pending_migration(repo_path: str) -> dict:
    """Run in a subprocess using the newly-installed binary.

    Connects to the ledger, detects whether a destructive migration is
    pending, and if so applies it (schema DDL + data wipe) and returns
    the replay plan so the caller can surface it to the agent.

    Returns a dict with keys:
      migrated: bool
      cursors_wiped: int          (only when migrated=True)
      replay_plan: list[dict]     (only when migrated=True)
      error: str                  (only on failure)
    """
    import tempfile, os
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(_MIGRATION_SCRIPT)
            tmp = f.name
        result = subprocess.run(
            [sys.executable, tmp, repo_path or "."],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        logger.debug("[update] migration subprocess failed: %s", result.stderr.strip())
        return {"migrated": False, "error": result.stderr.strip() or "unknown error"}
    except Exception as exc:
        logger.debug("[update] migration subprocess error: %s", exc)
        return {"migrated": False, "error": str(exc)}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _reinstall_skills(repo_path: str) -> int:
    """Re-copy skill SKILL.md files and hooks from the newly-installed package.

    Runs in a fresh subprocess so the newly-installed setup_wizard is used —
    the current process has the old version cached in sys.modules.
    """
    try:
        script = (
            "from setup_wizard import _install_skills, _install_claude_hooks; "
            "from pathlib import Path; "
            f"n = _install_skills(Path(r'{repo_path}')); "
            f"_install_claude_hooks(Path(r'{repo_path}')); "
            "print(n)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return int(result.stdout.strip() or "0")
        logger.debug("[update] skill reinstall subprocess failed: %s", result.stderr.strip())
        return 0
    except Exception as exc:
        logger.debug("[update] skill reinstall failed: %s", exc)
        return 0


async def handle_update(action: str, current_version: str, repo_path: str = "") -> dict:
    """Handle bicameral.update tool calls."""
    if action == "check":
        recommended = _fetch_recommended_version()
        if not recommended:
            return {
                "status": "unknown",
                "current_version": current_version,
                "message": "Could not reach version endpoint.",
            }
        if _parse_version(recommended) <= _parse_version(current_version):
            return {
                "status": "up_to_date",
                "current_version": current_version,
                "recommended_version": recommended,
            }
        return {
            "status": "update_available",
            "current_version": current_version,
            "recommended_version": recommended,
        }

    if action == "apply":
        recommended = _fetch_recommended_version()
        if not recommended:
            return {"status": "error", "message": "Could not determine recommended version."}

        if _parse_version(recommended) <= _parse_version(current_version):
            return {
                "status": "already_up_to_date",
                "current_version": current_version,
                "recommended_version": recommended,
            }

        target = f"bicameral-mcp=={recommended}"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", target, "--quiet"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                # Bust the cache so the next check reflects the new version
                _save_cache({})
                skills_updated = _reinstall_skills(repo_path) if repo_path else 0
                skills_note = (
                    f" Updated {skills_updated} skill(s) in .claude/skills/."
                    if skills_updated
                    else ""
                )

                # Auto-apply any pending destructive migration using the new binary.
                migration_result = _apply_pending_migration(repo_path) if repo_path else {"migrated": False}
                if migration_result.get("migrated"):
                    cursors_wiped = migration_result.get("cursors_wiped", 0)
                    replay_plan = migration_result.get("replay_plan", [])
                    replay_note = (
                        f" Schema migration applied automatically — {cursors_wiped} source(s) cleared."
                        f" Re-ingest each entry in migration_replay_plan to restore the ledger."
                        if cursors_wiped
                        else " Schema migration applied automatically — ledger was empty, nothing to replay."
                    )
                    return {
                        "status": "upgraded",
                        "from_version": current_version,
                        "to_version": recommended,
                        "skills_updated": skills_updated,
                        "migration_applied": True,
                        "migration_replay_plan": replay_plan,
                        "message": (
                            f"Upgraded to v{recommended}.{skills_note}{replay_note}"
                            f" Restart the MCP server to use the new version."
                        ),
                    }

                migration_error = migration_result.get("error")
                migration_warning = (
                    f"\n\n⚠️  Auto-migration failed ({migration_error}) — "
                    "if the server fails to start, call bicameral.reset(confirm=True) to apply manually."
                    if migration_error
                    else ""
                )
                return {
                    "status": "upgraded",
                    "from_version": current_version,
                    "to_version": recommended,
                    "skills_updated": skills_updated,
                    "migration_applied": False,
                    "message": (
                        f"Upgraded to v{recommended}.{skills_note} "
                        f"Restart the MCP server to use the new version.{migration_warning}"
                    ),
                }
            else:
                return {
                    "status": "error",
                    "message": f"pip install failed: {result.stderr.strip()}",
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "pip install timed out after 120s."}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    return {"status": "error", "message": f"Unknown action '{action}'. Use 'check' or 'apply'."}
