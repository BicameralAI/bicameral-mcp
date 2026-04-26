"""Interactive setup wizard for bicameral-mcp.

Guides the user through selecting a repo and coding agent, then installs
the MCP server config + skills.

Supports: Claude Code, Cursor, Codex

Usage: bicameral-mcp setup
       bicameral-mcp setup /path/to/repo
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

AGENTS = {
    "claude": {
        "name": "Claude Code",
        "config_format": "json",
        "config_path": lambda repo: repo / ".mcp.json",
        "skills": True,
    },
    "cursor": {
        "name": "Cursor",
        "config_format": "json",
        "config_path": lambda repo: repo / ".cursor" / "mcp.json",
        "skills": False,
    },
    "codex": {
        "name": "Codex",
        "config_format": "toml",
        "config_path": lambda repo: repo / ".codex" / "config.toml",
        "skills": False,
    },
}


def _detect_history_path(repo_path: Path, hint: str | None = None) -> Path:
    """Optionally select a separate directory for Bicameral history storage.

    Returns repo_path if the user skips (default).  The separate path is
    useful when the code repo is public but history should live in a private
    parent repo.
    """
    if hint:
        p = Path(hint).resolve()
        if p.is_dir():
            return p
        print(f"  History path not found: {p}")

    if not _is_interactive():
        return repo_path

    raw = input(
        f"\n  History storage path (default: same as repo — press Enter to skip):\n  > "
    ).strip()
    if not raw:
        return repo_path

    p = Path(raw).expanduser().resolve()
    if p.is_dir():
        return p
    print(f"  Not a directory: {p} — falling back to repo path")
    return repo_path


def _detect_repo(hint: str | None = None) -> Path:
    """Detect or prompt for the repo path."""
    if hint:
        p = Path(hint).resolve()
        if p.is_dir():
            return p
        print(f"  Path not found: {p}")

    cwd = Path.cwd()
    git_root = _find_git_root(cwd)

    # Non-interactive: use detected git root or cwd
    if not _is_interactive():
        if git_root:
            return git_root
        return cwd

    if git_root:
        answer = input(f"\n  Detected git repo: {git_root}\n  Use this? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return git_root

    while True:
        raw = input("\n  Enter the path to your repo: ").strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
        print(f"  Not a directory: {p}")


def _find_git_root(start: Path) -> Path | None:
    """Walk up from start to find .git directory."""
    current = start
    for _ in range(20):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _detect_agents() -> list[str]:
    """Auto-detect which coding agents are available."""
    found = []
    if shutil.which("claude"):
        found.append("claude")
    if shutil.which("cursor"):
        found.append("cursor")
    if shutil.which("codex"):
        found.append("codex")
    return found


def _is_interactive() -> bool:
    """Check if stdin is a terminal (not piped)."""
    import sys
    return sys.stdin.isatty()


def _select_agents() -> list[str]:
    """Prompt user to select coding agents."""
    import questionary

    detected = _detect_agents()

    # Non-interactive: auto-install for all detected (or claude as default)
    if not _is_interactive():
        if detected:
            names = ", ".join(AGENTS[a]["name"] for a in detected)
            print(f"  Auto-detected: {names}")
            return detected
        return ["claude"]

    all_keys = list(AGENTS.keys())
    choices = [
        questionary.Choice(
            title=f"{AGENTS[k]['name']}{' (detected)' if k in detected else ''}",
            value=k,
            checked=k in detected or (not detected and k == "claude"),
        )
        for k in all_keys
    ]

    selected = questionary.checkbox(
        "Select coding agents to configure:",
        choices=choices,
    ).ask()

    if not selected:
        return detected or ["claude"]
    return selected


def _detect_runner() -> tuple[str, list[str]]:
    """Detect the best available runner for bicameral-mcp.

    Preference order:
      1. bicameral-mcp binary on PATH — uses the actual installed environment,
         so local subpackages (dashboard/, etc.) and editable installs work.
      2. python3 -m bicameral_mcp — fallback for source checkouts / venvs.

    pipx run is intentionally NOT used: it downloads a fresh ephemeral copy
    from PyPI on every server start, which misses local-only modules and can
    run a different version than what the user installed.
    """
    if shutil.which("bicameral-mcp"):
        return ("bicameral-mcp", [])
    python = "python3" if shutil.which("python3") else "python"
    return (python, ["-m", "bicameral_mcp"])


def _build_config(
    repo_path: Path,
    data_path: Path | None = None,
    mode: str = "solo",
    telemetry: bool = False,
) -> dict:
    """Build the MCP server config object.

    data_path: where .bicameral/ history lives.  Defaults to repo_path.
    Keeping data_path separate lets history live in a private parent repo
    while REPO_PATH points to the public code repo.

    In team mode, local DBs go under .bicameral/local/ (gitignored)
    so they don't leak into the tracked events directory.
    """
    command, args = _detect_runner()
    data_root = (data_path or repo_path) / ".bicameral"
    data_root.mkdir(parents=True, exist_ok=True)

    if mode == "team":
        local_dir = data_root / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
    else:
        local_dir = data_root

    env: dict[str, str] = {
        "REPO_PATH": str(repo_path),
        "SURREAL_URL": f"surrealkv://{local_dir / 'ledger.db'}",
        "CODE_LOCATOR_SQLITE_DB": str(local_dir / "code-graph.db"),
        "BICAMERAL_TELEMETRY": "1" if telemetry else "0",
    }
    if data_path is not None and data_path.resolve() != repo_path.resolve():
        # History lives in a separate private repo — tell the adapter where
        # to read/write events and config.
        env["BICAMERAL_DATA_PATH"] = str(data_path)

    return {"command": command, "args": args, "env": env}


def _write_json_config(
    repo_path: Path,
    config_path: Path,
    data_path: Path | None = None,
    mode: str = "solo",
    telemetry: bool = False,
) -> None:
    """Write MCP server config to a JSON file (Claude Code / Cursor)."""
    config = _build_config(repo_path, data_path=data_path, mode=mode, telemetry=telemetry)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.setdefault("mcpServers", {})["bicameral"] = config
    config_path.write_text(json.dumps(existing, indent=2) + "\n")


def _write_toml_config(
    repo_path: Path,
    config_path: Path,
    data_path: Path | None = None,
    mode: str = "solo",
    telemetry: bool = False,
) -> None:
    """Write MCP server config to a TOML file (Codex)."""
    config = _build_config(repo_path, data_path=data_path, mode=mode, telemetry=telemetry)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the [mcp_servers.bicameral] TOML section
    lines = []

    # Read existing content, strip old bicameral section if present
    if config_path.exists():
        existing = config_path.read_text()
        in_bicameral = False
        for line in existing.splitlines():
            if line.strip() == "[mcp_servers.bicameral]":
                in_bicameral = True
                continue
            if in_bicameral and line.startswith("["):
                in_bicameral = False
            if not in_bicameral:
                lines.append(line)
    # Remove trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()

    # Append the bicameral section
    if lines:
        lines.append("")
    lines.append("[mcp_servers.bicameral]")
    lines.append(f'command = "{config["command"]}"')

    args_str = ", ".join(f'"{a}"' for a in config["args"])
    lines.append(f"args = [{args_str}]")

    env_parts = ", ".join(f'"{k}" = "{v}"' for k, v in config["env"].items())
    lines.append(f"env = {{ {env_parts} }}")
    lines.append("")

    config_path.write_text("\n".join(lines) + "\n")


def _install_for_agent(
    agent_key: str,
    repo_path: Path,
    data_path: Path | None = None,
    mode: str = "solo",
    telemetry: bool = False,
) -> bool:
    """Install MCP config for a specific coding agent."""
    agent = AGENTS[agent_key]
    config_path = agent["config_path"](repo_path)

    # For Claude Code, try CLI first
    if agent_key == "claude" and shutil.which("claude"):
        config = _build_config(repo_path, data_path=data_path, mode=mode, telemetry=telemetry)
        config_json = json.dumps(config)
        subprocess.run(
            ["claude", "mcp", "remove", "bicameral", "--scope", "project"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        result = subprocess.run(
            ["claude", "mcp", "add-json", "bicameral", "--scope", "project", config_json],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if result.returncode == 0:
            print(f"  {agent['name']}: installed via CLI")
            return True

    # For Codex, try CLI first
    if agent_key == "codex" and shutil.which("codex"):
        config = _build_config(repo_path, data_path=data_path, mode=mode, telemetry=telemetry)
        env_args = []
        for k, v in config["env"].items():
            env_args.extend(["--env", f"{k}={v}"])
        result = subprocess.run(
            ["codex", "mcp", "add", "bicameral"] + env_args + ["--"] + [config["command"]] + config["args"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if result.returncode == 0:
            print(f"  {agent['name']}: installed via CLI")
            return True

    # Fallback: write config file directly
    if agent.get("config_format") == "toml":
        _write_toml_config(repo_path, config_path, data_path=data_path, mode=mode, telemetry=telemetry)
    else:
        _write_json_config(repo_path, config_path, data_path=data_path, mode=mode, telemetry=telemetry)

    print(f"  {agent['name']}: wrote {config_path}")
    return True


_BICAMERAL_SESSION_END_COMMAND = (
    "[ -d .bicameral ] && claude -p '/bicameral:capture-corrections' || true"
)


def _install_claude_hooks(repo_path: Path) -> bool:
    """Merge bicameral hooks into the project-level .claude/settings.json.

    Installs one hook:
    - SessionEnd: runs bicameral-capture-corrections to catch uningested
      mid-session corrections (only fires when .bicameral/ exists).

    Idempotent — safe to call on every setup run. Returns True if a new
    entry was written, False if already present.
    """
    settings_path = repo_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    hooks = existing.setdefault("hooks", {})
    wrote_anything = False

    # ── SessionEnd — capture uningested corrections ──────────────────
    session_end: list = hooks.setdefault("SessionEnd", [])
    session_end_present = any(
        "bicameral" in h.get("command", "")
        for entry in session_end
        for h in entry.get("hooks", [])
    )
    if not session_end_present:
        session_end.append({
            "hooks": [{
                "type": "command",
                "command": _BICAMERAL_SESSION_END_COMMAND,
            }]
        })
        wrote_anything = True

    if wrote_anything:
        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    return wrote_anything


def _install_skills(repo_path: Path) -> int:
    """Copy skill definitions into .claude/skills/ in the target repo."""
    skills_src = Path(__file__).parent / "skills"
    if not skills_src.exists():
        return 0

    skills_dst = repo_path / ".claude" / "skills"
    installed = 0

    for skill_dir in sorted(skills_src.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        dst_dir = skills_dst / skill_dir.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / "SKILL.md").write_text(skill_md.read_text())
        installed += 1

    return installed


def _select_collaboration_mode() -> str:
    """Prompt user for solo or team collaboration mode."""
    import questionary

    if not _is_interactive():
        return "team"

    result = questionary.select(
        "Collaboration mode:",
        choices=[
            questionary.Choice("Team  — decisions shared via git (append-only event files)", value="team"),
            questionary.Choice("Solo  — decisions stored locally", value="solo"),
        ],
        default="team",
    ).ask()

    return result if result is not None else "team"


def _select_guided_mode() -> bool:
    """Prompt user for guided-mode intensity."""
    import questionary

    if not _is_interactive():
        return True

    result = questionary.select(
        "Interaction intensity:",
        choices=[
            questionary.Choice("Guided  — bicameral stops you when it detects discrepancies", value=True),
            questionary.Choice("Normal  — bicameral flags discrepancies as advisory hints", value=False),
        ],
        default=True,
    ).ask()

    return result if result is not None else True


def _select_telemetry() -> bool:
    """Prompt user for anonymous telemetry consent.

    Shows the exact event schema before asking. Defaults to Yes (opt-in).
    """
    import questionary

    print()
    print("  Anonymous telemetry — exact payload that would be sent:")
    print()
    print('    {"tool": "bicameral.ingest", "version": "0.5.3",')
    print('     "duration_ms": 412, "errored": false,')
    print('     "diagnostic": {"grounded_count": 3, "ungrounded_count": 1}}')
    print()
    print("    No code. No decision text. No file paths. No personal data.")
    print("    Change anytime: BICAMERAL_TELEMETRY=0")
    print()

    if not _is_interactive():
        return True

    result = questionary.select(
        "Enable anonymous telemetry?",
        choices=[
            questionary.Choice("Yes  — share anonymous usage stats to improve Bicameral", value=True),
            questionary.Choice("No   — keep telemetry off", value=False),
        ],
        default=True,
    ).ask()

    return result if result is not None else True


def _write_collaboration_config(
    data_path: Path,
    mode: str,
    guided: bool = False,
    telemetry: bool = False,
) -> None:
    """Write .bicameral/config.yaml with collaboration mode, guided-mode, and telemetry flags."""
    config_path = data_path / ".bicameral" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "# Bicameral configuration\n"
        f"mode: {mode}\n"
        f"guided: {'true' if guided else 'false'}\n"
        f"telemetry: {'true' if telemetry else 'false'}\n",
        encoding="utf-8",
    )
    print(f"  Collaboration: {mode} mode")
    print(f"  Guided mode: {'on — blocking hints' if guided else 'off — advisory hints'}")
    print(f"  Telemetry: {'on — anonymous usage stats' if telemetry else 'off'}")


def _patch_gitignore(path: Path, entries: list[str], comment: str) -> None:
    """Idempotently write bicameral entries into a .gitignore file.

    Removes any pre-existing bicameral block first to avoid stale entries
    when upgrading from solo→team or when data_path changes.
    """
    if path.exists():
        content = path.read_text()
        lines = content.splitlines()
        cleaned: list[str] = []
        skip_next_blank = False
        for line in lines:
            stripped = line.strip()
            if stripped in (".bicameral/", ".bicameral/local/"):
                skip_next_blank = True
                continue
            if stripped.startswith("# Bicameral MCP"):
                skip_next_blank = True
                continue
            if skip_next_blank and stripped == "":
                skip_next_blank = False
                continue
            skip_next_blank = False
            cleaned.append(line)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        content = "\n".join(cleaned)
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"\n{comment}\n"
        for entry in entries:
            content += f"{entry}\n"
        path.write_text(content)
    else:
        path.write_text(f"{comment}\n" + "".join(f"{e}\n" for e in entries))


def _ensure_gitignore(
    data_path: Path,
    mode: str = "solo",
    repo_path: Path | None = None,
) -> None:
    """Configure .gitignore entries for the selected collaboration mode.

    data_path: where .bicameral/ history lives (events + local state).
    repo_path: the code repo being analyzed.  When it differs from data_path
               (history stored in a private parent), the public repo's
               .gitignore gets a blanket `.bicameral/` rule so stale local
               state from a prior setup can never slip into the public repo.

    Modes applied to data_path/.gitignore:
      team: `.bicameral/local/`  (events/ committed, local/ ignored)
      solo: `.bicameral/`        (whole dir ignored)
    """
    if mode == "team":
        data_entries = [".bicameral/local/"]
        data_comment = "# Bicameral MCP local data (team mode — events/ is committed)"
    else:
        data_entries = [".bicameral/"]
        data_comment = "# Bicameral MCP local data"

    _patch_gitignore(data_path / ".gitignore", data_entries, data_comment)
    print(f"  Updated {data_path}/.gitignore for {mode} mode")

    # When history lives in a separate repo, also guard the public code repo.
    if repo_path is not None and repo_path.resolve() != data_path.resolve():
        _patch_gitignore(
            repo_path / ".gitignore",
            [".bicameral/"],
            "# Bicameral MCP local data (history stored in parent repo)",
        )
        print(f"  Updated {repo_path}/.gitignore — .bicameral/ fully ignored (history in parent)")


def run_setup(repo_hint: str | None = None, history_hint: str | None = None) -> int:
    """Run the interactive setup wizard."""
    print()
    print("  ┌─────────────────────────────────────────┐")
    print("  │  Bicameral MCP — Setup                   │")
    print("  │  Decision ledger for your codebase        │")
    print("  └─────────────────────────────────────────┘")
    print()

    # Step 1: Select repo
    repo_path = _detect_repo(repo_hint)
    print(f"\n  Repo: {repo_path}")

    # Step 1b: Optionally separate history storage (e.g. private parent repo)
    data_path = _detect_history_path(repo_path, history_hint)
    if data_path != repo_path:
        print(f"  History: {data_path}")

    # Step 2: Select coding agents
    print()
    agents = _select_agents()

    # Step 3: Runner check
    command, _ = _detect_runner()
    if command not in ("bicameral-mcp",):
        print(f"\n  Note: bicameral-mcp binary not found on PATH.")
        print(f"  Using '{command} -m bicameral_mcp' as runner.")
        print("  Install for a cleaner setup: pip install bicameral-mcp")

    # Step 4: Collaboration mode + guided intensity + telemetry + gitignore
    collab_mode = _select_collaboration_mode()
    guided = _select_guided_mode()
    telemetry = _select_telemetry()
    _write_collaboration_config(data_path, collab_mode, guided=guided, telemetry=telemetry)
    _ensure_gitignore(data_path, mode=collab_mode, repo_path=repo_path)

    if collab_mode == "team":
        events_dir = data_path / ".bicameral" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)

    # Step 5: Install MCP config for each agent
    print()
    for agent_key in agents:
        _install_for_agent(agent_key, repo_path, data_path=data_path, mode=collab_mode, telemetry=telemetry)

    # Step 6: Install skills + hooks (Claude Code only)
    if "claude" in agents:
        num_skills = _install_skills(repo_path)
        if num_skills:
            print(f"  Claude Code: installed {num_skills} slash commands")
        if _install_claude_hooks(repo_path):
            print("  Claude Code: installed session-end hook → capture-corrections")

    # Summary
    agent_names = ", ".join(AGENTS[a]["name"] for a in agents)
    print(f"\n  Done! Bicameral MCP configured for: {agent_names}")
    print(f"  Repo: {repo_path}")
    if data_path != repo_path:
        print(f"  History: {data_path}")
    print()

    if "claude" in agents:
        print("  Claude Code slash commands:")
        print("    /bicameral:ingest     — ingest a transcript, Slack thread, or PRD")
        print("    /bicameral:preflight  — pre-flight: surface decisions before coding")
        print("    /bicameral:history    — list all tracked decisions by feature area")
        print("    /bicameral:dashboard  — open live decision dashboard in browser")
        print("    /bicameral:reset      — nuke and replay the ledger (emergency)")
        print()

    print("  Or just ask naturally:")
    print('    "What decisions have been made about authentication?"')
    print('    "Check this file for drifted decisions"')
    print()

    return 0
