"""Handler for bicameral.update — check for and apply recommended updates.

Two release channels:
  - ``stable``  → tracks ``RECOMMENDED_VERSION`` on the ``main`` branch.
  - ``nightly`` → tracks ``RECOMMENDED_NIGHTLY_VERSION`` on the ``dev`` branch,
    updated by ``.github/workflows/publish-nightly.yml`` after each successful
    PyPI dev-release publish.

Channel is read from ``.bicameral/config.yaml`` (``channel: stable|nightly``),
defaulting to ``stable``. Testers opt into nightly by editing the config or
re-running the wizard; the wizard writes ``channel: stable`` on a fresh install.

Version comparison uses ``packaging.version.Version`` (PEP 440), so dev
releases (``0.14.7.dev202605151430``) compare correctly against final
releases (``0.14.6``, ``0.14.7``). The previous ``int(x) for x in v.split('.')``
parser crashed on ``.devN`` suffixes and silently downgraded nightly users.

Update check is cached at ``~/.bicameral/update-check.json`` with a 1-hour
TTL, keyed by channel.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

_RECOMMENDED_VERSION_URLS: dict[str, str] = {
    "stable": "https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/RECOMMENDED_VERSION",
    "nightly": "https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/dev/RECOMMENDED_NIGHTLY_VERSION",
}
_VALID_CHANNELS = frozenset(_RECOMMENDED_VERSION_URLS)
_DEFAULT_CHANNEL = "stable"

_CACHE_PATH = os.path.expanduser("~/.bicameral/update-check.json")
_CACHE_TTL_SECONDS = 3600  # 1 hour


def _resolve_install_command(target: str) -> list[str]:
    """Resolve the installer command for ``target`` (e.g. ``"bicameral-mcp==1.2.3"``).

    Order is deterministic and PATH-driven (no env heuristics):
      1. ``uv tool install --force <target>`` — preferred. uv ships as a
         single static binary, has no Python prerequisite, and ``uv tool``
         is the canonical CLI-app installer in the uv ecosystem.
      2. ``pipx install <target> --force`` — fallback when uv is absent.
         Manages its own venv and handles externally-managed-environment
         restrictions on macOS.
      3. ``<sys.executable> -m pip install --quiet <target>`` — last-resort
         path for venv/dev installs where neither uv nor pipx is on PATH.

    (#199)
    """
    if shutil.which("uv"):
        return ["uv", "tool", "install", "--force", target]
    if shutil.which("pipx"):
        return ["pipx", "install", target, "--force"]
    return [sys.executable, "-m", "pip", "install", target, "--quiet"]


def _load_cache() -> dict:
    """Load the per-channel cache. Migrates legacy flat shape on read."""
    try:
        with open(_CACHE_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Legacy shape was {"recommended_version": ..., "fetched_at": ...}.
        # Promote it under the "stable" key so existing caches keep working.
        if "recommended_version" in data and "fetched_at" in data:
            return {"stable": data}
        return data
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _normalize_channel(channel: str | None) -> str:
    if channel and channel in _VALID_CHANNELS:
        return channel
    return _DEFAULT_CHANNEL


def _read_channel(repo_path: str) -> str:
    """Resolve the release channel from ``.bicameral/config.yaml``.

    Mirrors the regex-fallback pattern used by ``_read_guided_from_config`` to
    avoid hard-importing yaml in this module. Defaults to ``stable`` on any
    missing file, parse error, or unrecognized value.
    """
    if not repo_path:
        return _DEFAULT_CHANNEL
    try:
        config_path = Path(repo_path) / ".bicameral" / "config.yaml"
        if not config_path.exists():
            return _DEFAULT_CHANNEL
        text = config_path.read_text()
        m = re.search(r"^channel:\s*(\w+)", text, re.MULTILINE)
        if m:
            return _normalize_channel(m.group(1))
    except Exception:
        pass
    return _DEFAULT_CHANNEL


def fetch_recommended_version(channel: str = _DEFAULT_CHANNEL) -> str | None:
    """Public alias for ``_fetch_recommended_version`` (#252 Layer 3 cross-layer call).

    Used by ``cli/diagnose.py`` to compute the recommended-version-mismatch
    suggestion heuristic. Same semantics + 1-hour cache; this is the
    cross-layer-clean entry point.
    """
    return _fetch_recommended_version(channel)


def _fetch_recommended_version(channel: str = _DEFAULT_CHANNEL) -> str | None:
    """Fetch the recommended version for ``channel`` from GitHub with a 1-hour cache."""
    channel = _normalize_channel(channel)
    cache = _load_cache()
    now = time.time()
    bucket = cache.get(channel) if isinstance(cache.get(channel), dict) else {}

    if bucket.get("fetched_at", 0) + _CACHE_TTL_SECONDS > now:
        return bucket.get("recommended_version")

    url = _RECOMMENDED_VERSION_URLS[channel]
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            version = resp.read().decode().strip()
        cache[channel] = {"recommended_version": version, "fetched_at": now}
        _save_cache(cache)
        return version
    except Exception as exc:
        logger.debug("[update] version check failed for channel=%s: %s", channel, exc)
        # Return stale cache value rather than nothing
        return bucket.get("recommended_version")


def _parse_version(v: str) -> Version:
    """PEP 440 version parse. Falls back to ``Version('0')`` on malformed input.

    Using ``packaging.version.Version`` ensures ``0.14.7.dev202605151430``
    correctly orders between ``0.14.6`` (less) and ``0.14.7`` (less, since
    .devN sorts before the final release of the same number). The previous
    tuple-of-ints parser crashed on the ``.devN`` suffix and returned ``(0,)``,
    which made every nightly tester look like they were running v0.
    """
    try:
        return Version(v.strip().lstrip("v"))
    except (InvalidVersion, Exception):
        return Version("0")


def get_update_notice(current_version: str, repo_path: str = "") -> dict | None:
    """Return an _update block if a recommended update is available, else None.

    Channel is read from ``<repo_path>/.bicameral/config.yaml``. With no
    repo_path (e.g. early server bootstrap), behaves as if channel=stable.
    """
    channel = _read_channel(repo_path)
    recommended = _fetch_recommended_version(channel)
    if not recommended:
        return None
    if _parse_version(recommended) <= _parse_version(current_version):
        return None
    return {
        "channel": channel,
        "recommended_version": recommended,
        "current_version": current_version,
        "action_required": (
            f"Ask the user: 'bicameral-mcp v{recommended} is available "
            f"(you are on v{current_version}, channel={channel}) — upgrade now? (yes/no)'. "
            'If yes, call bicameral.update {"action": "apply"}.'
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
    import os
    import tempfile

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
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


def _read_guided_from_config(repo_path: str) -> bool:
    """Return the guided: flag from .bicameral/config.yaml, defaulting to False."""
    try:
        import re

        config_path = Path(repo_path) / ".bicameral" / "config.yaml"
        if not config_path.exists():
            return False
        text = config_path.read_text()
        m = re.search(r"^guided:\s*(true|false)", text, re.MULTILINE)
        return m.group(1) == "true" if m else False
    except Exception:
        return False


def _reinstall_skills(repo_path: str) -> int:
    """Re-copy skill SKILL.md files and hooks from the newly-installed package.

    Runs in a fresh subprocess so the newly-installed setup_wizard is used —
    the current process has the old version cached in sys.modules.
    """
    try:
        guided = _read_guided_from_config(repo_path)
        script = (
            "from setup_wizard import _install_skills, _install_claude_hooks, _install_git_post_commit_hook; "
            "from pathlib import Path; "
            f"rp = Path(r'{repo_path}'); "
            f"n = _install_skills(rp); "
            f"_install_claude_hooks(rp); "
            + ("_install_git_post_commit_hook(rp); " if guided else "")
            + "print(n)"
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


async def handle_update(
    action: str,
    current_version: str,
    repo_path: str = "",
    *,
    preflight_id: str | None = None,
) -> dict:
    """Handle bicameral.update tool calls.

    The keyword-only ``preflight_id`` is plumbed onto every return dict for
    parity with the pydantic-model handlers (#65). This is intentionally a
    smaller blast radius than refactoring update.py to a pydantic response.
    """
    # Best-effort engagement telemetry — emit once at entry.
    try:
        from preflight_telemetry import telemetry_enabled, write_engagement

        if telemetry_enabled():
            write_engagement(
                session_id="unknown",  # update.py is not session-scoped
                tool="bicameral.update",
                decision_id=None,
                preflight_id=preflight_id,
                file_paths=None,
            )
    except Exception:
        pass

    channel = _read_channel(repo_path)

    if action == "check":
        recommended = _fetch_recommended_version(channel)
        if not recommended:
            return {
                "status": "unknown",
                "channel": channel,
                "current_version": current_version,
                "message": f"Could not reach version endpoint for channel={channel}.",
                "preflight_id": preflight_id,
            }
        if _parse_version(recommended) <= _parse_version(current_version):
            return {
                "status": "up_to_date",
                "channel": channel,
                "current_version": current_version,
                "recommended_version": recommended,
                "preflight_id": preflight_id,
            }
        return {
            "status": "update_available",
            "channel": channel,
            "current_version": current_version,
            "recommended_version": recommended,
            "preflight_id": preflight_id,
        }

    if action == "apply":
        recommended = _fetch_recommended_version(channel)
        if not recommended:
            return {
                "status": "error",
                "channel": channel,
                "message": f"Could not determine recommended version for channel={channel}.",
                "preflight_id": preflight_id,
            }

        if _parse_version(recommended) <= _parse_version(current_version):
            return {
                "status": "already_up_to_date",
                "channel": channel,
                "current_version": current_version,
                "recommended_version": recommended,
                "preflight_id": preflight_id,
            }

        target = f"bicameral-mcp=={recommended}"
        try:
            cmd = _resolve_install_command(target)
            result = subprocess.run(
                cmd,
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
                migration_result = (
                    _apply_pending_migration(repo_path) if repo_path else {"migrated": False}
                )
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
                        "channel": channel,
                        "from_version": current_version,
                        "to_version": recommended,
                        "skills_updated": skills_updated,
                        "migration_applied": True,
                        "migration_replay_plan": replay_plan,
                        "message": (
                            f"Upgraded to v{recommended}.{skills_note}{replay_note}"
                            f" Restart the MCP server to use the new version."
                        ),
                        "preflight_id": preflight_id,
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
                    "channel": channel,
                    "from_version": current_version,
                    "to_version": recommended,
                    "skills_updated": skills_updated,
                    "migration_applied": False,
                    "message": (
                        f"Upgraded to v{recommended}.{skills_note} "
                        f"Restart the MCP server to use the new version.{migration_warning}"
                    ),
                    "preflight_id": preflight_id,
                }
            else:
                return {
                    "status": "error",
                    "message": f"{cmd[0]} install failed: {result.stderr.strip()}",
                    "preflight_id": preflight_id,
                }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"{cmd[0]} install timed out after 120s.",
                "preflight_id": preflight_id,
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
                "preflight_id": preflight_id,
            }

    return {
        "status": "error",
        "message": f"Unknown action '{action}'. Use 'check' or 'apply'.",
        "preflight_id": preflight_id,
    }
