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


def _reinstall_skills(repo_path: str) -> int:
    """Re-copy skill SKILL.md files from the newly-installed package into the repo."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("setup_wizard")
        if spec is None or spec.origin is None:
            return 0
        from pathlib import Path
        skills_src = Path(spec.origin).parent / "skills"
        if not skills_src.exists():
            return 0
        skills_dst = Path(repo_path) / ".claude" / "skills"
        count = 0
        for skill_dir in sorted(skills_src.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            dst_dir = skills_dst / skill_dir.name
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / "SKILL.md").write_text(skill_md.read_text())
            count += 1

        # Re-install the git hook alongside skills on every upgrade
        try:
            from setup_wizard import _install_claude_hooks
            _install_claude_hooks(Path(repo_path))
        except Exception:
            pass

        return count
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
                return {
                    "status": "upgraded",
                    "from_version": current_version,
                    "to_version": recommended,
                    "skills_updated": skills_updated,
                    "message": (
                        f"Upgraded to v{recommended}.{skills_note} "
                        "Restart the MCP server to use the new version."
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
