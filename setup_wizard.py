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
    detected = _detect_agents()

    # Non-interactive: auto-install for all detected (or claude as default)
    if not _is_interactive():
        if detected:
            names = ", ".join(AGENTS[a]["name"] for a in detected)
            print(f"  Auto-detected: {names}")
            return detected
        return ["claude"]

    if detected:
        names = ", ".join(AGENTS[a]["name"] for a in detected)
        print(f"  Detected: {names}")
        answer = input("  Install for all detected? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return detected

    all_keys = list(AGENTS.keys())
    print("\n  Select coding agents to configure:")
    for i, key in enumerate(all_keys, 1):
        print(f"    {i}. {AGENTS[key]['name']}")
    print(f"    {len(all_keys) + 1}. All")
    choice = input(f"  Choice [1-{len(all_keys) + 1}]: ").strip()

    try:
        idx = int(choice) - 1
        if idx == len(all_keys):
            return all_keys
        if 0 <= idx < len(all_keys):
            return [all_keys[idx]]
    except ValueError:
        pass
    return ["claude"]


def _detect_runner() -> tuple[str, list[str]]:
    """Detect the best available Python package runner.

    uvx is intentionally not used — bicameral-mcp bundles ML dependencies
    (sentence-transformers, cocoindex) that are too large for ephemeral runs.
    pipx install gives a persistent, upgradeable install.
    """
    if shutil.which("pipx"):
        return ("pipx", ["run", "bicameral-mcp"])
    python = "python3" if shutil.which("python3") else "python"
    return (python, ["-m", "bicameral_mcp"])


def _build_config(repo_path: Path, mode: str = "solo") -> dict:
    """Build the MCP server config object.

    In team mode, local DBs go under .bicameral/local/ (gitignored)
    so they don't leak into the tracked events directory.
    """
    command, args = _detect_runner()
    data_dir = repo_path / ".bicameral"
    data_dir.mkdir(parents=True, exist_ok=True)

    if mode == "team":
        local_dir = data_dir / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
    else:
        local_dir = data_dir

    return {
        "command": command,
        "args": args,
        "env": {
            "REPO_PATH": str(repo_path),
            "SURREAL_URL": f"surrealkv://{local_dir / 'ledger.db'}",
            "CODE_LOCATOR_SQLITE_DB": str(local_dir / "code-graph.db"),
        },
    }


def _write_json_config(repo_path: Path, config_path: Path, mode: str = "solo") -> None:
    """Write MCP server config to a JSON file (Claude Code / Cursor)."""
    config = _build_config(repo_path, mode=mode)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.setdefault("mcpServers", {})["bicameral"] = config
    config_path.write_text(json.dumps(existing, indent=2) + "\n")


def _write_toml_config(repo_path: Path, config_path: Path, mode: str = "solo") -> None:
    """Write MCP server config to a TOML file (Codex)."""
    config = _build_config(repo_path, mode=mode)
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


def _install_for_agent(agent_key: str, repo_path: Path, mode: str = "solo") -> bool:
    """Install MCP config for a specific coding agent."""
    agent = AGENTS[agent_key]
    config_path = agent["config_path"](repo_path)

    # For Claude Code, try CLI first
    if agent_key == "claude" and shutil.which("claude"):
        config = _build_config(repo_path, mode=mode)
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
        config = _build_config(repo_path, mode=mode)
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
        _write_toml_config(repo_path, config_path, mode=mode)
    else:
        _write_json_config(repo_path, config_path, mode=mode)

    print(f"  {agent['name']}: wrote {config_path}")
    return True


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
    if not _is_interactive():
        return "solo"

    print("\n  Collaboration mode:")
    print("    1. Solo  — decisions stored locally (default)")
    print("    2. Team  — decisions shared via git (append-only event files)")
    choice = input("  Choice [1/2]: ").strip()

    if choice == "2":
        return "team"
    return "solo"


def _select_guided_mode() -> bool:
    """Prompt user for guided-mode intensity.

    Guided mode makes bicameral stop the agent when it detects
    discrepancies (drifted decisions, divergences, open questions).
    Normal mode still surfaces those as advisory hints, but doesn't
    block writes.
    """
    if not _is_interactive():
        return False

    print("\n  Interaction intensity:")
    print("    1. Normal  — bicameral flags discrepancies as advisory hints (default)")
    print("    2. Guided  — bicameral stops you when it detects discrepancies")
    choice = input("  Choice [1/2]: ").strip()

    return choice == "2"


def _write_collaboration_config(
    repo_path: Path,
    mode: str,
    guided: bool = False,
) -> None:
    """Write .bicameral/config.yaml with the collaboration mode and
    guided-mode flag.
    """
    config_path = repo_path / ".bicameral" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "# Bicameral configuration\n"
        f"mode: {mode}\n"
        f"guided: {'true' if guided else 'false'}\n",
        encoding="utf-8",
    )
    print(f"  Collaboration: {mode} mode")
    print(f"  Guided mode: {'on — blocking hints' if guided else 'off — advisory hints'}")


def _ensure_gitignore(repo_path: Path, mode: str = "solo") -> None:
    """Configure .gitignore for the selected collaboration mode.

    Solo mode: ignore all of .bicameral/
    Team mode: ignore only .bicameral/local/ (events/ and config.yaml are committed)
    """
    gitignore = repo_path / ".gitignore"

    if mode == "team":
        entries = [".bicameral/local/"]
        comment = "# Bicameral MCP local data (team mode — events/ is committed)"
    else:
        entries = [".bicameral/"]
        comment = "# Bicameral MCP local data"

    if gitignore.exists():
        content = gitignore.read_text()
        lines = content.splitlines()

        # Remove any existing bicameral entries to avoid conflicts
        cleaned = []
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

        # Remove trailing blank lines
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()

        content = "\n".join(cleaned)
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"\n{comment}\n"
        for entry in entries:
            content += f"{entry}\n"
        gitignore.write_text(content)
    else:
        lines = [comment]
        lines.extend(entries)
        gitignore.write_text("\n".join(lines) + "\n")

    print(f"  Updated .gitignore for {mode} mode")


def run_setup(repo_hint: str | None = None) -> int:
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

    # Step 2: Select coding agents
    print()
    agents = _select_agents()

    # Step 3: Runner check
    command, _ = _detect_runner()
    if command not in ("pipx",):
        print(f"\n  Note: using '{command} -m bicameral_mcp' as runner.")
        print("  Install a package runner for zero-install: pip install pipx")

    # Step 4: Collaboration mode + guided intensity + gitignore
    collab_mode = _select_collaboration_mode()
    guided = _select_guided_mode()
    _write_collaboration_config(repo_path, collab_mode, guided=guided)
    _ensure_gitignore(repo_path, mode=collab_mode)

    if collab_mode == "team":
        events_dir = repo_path / ".bicameral" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)

    # Step 5: Install MCP config for each agent
    print()
    for agent_key in agents:
        _install_for_agent(agent_key, repo_path, mode=collab_mode)

    # Step 6: Install skills (Claude Code only)
    if "claude" in agents:
        num_skills = _install_skills(repo_path)
        if num_skills:
            print(f"  Claude Code: installed {num_skills} slash commands")

    # Summary
    agent_names = ", ".join(AGENTS[a]["name"] for a in agents)
    print(f"\n  Done! Bicameral MCP configured for: {agent_names}")
    print(f"  Repo: {repo_path}")
    print()

    if "claude" in agents:
        print("  Claude Code slash commands:")
        print("    /bicameral:ingest  — ingest a meeting transcript or PRD")
        print("    /bicameral:search  — pre-flight: check prior decisions")
        print("    /bicameral:drift   — check a file for drifted decisions")
        print("    /bicameral:status  — implementation status dashboard")
        print()

    print("  Or just ask naturally:")
    print('    "What decisions have been made about authentication?"')
    print('    "Check this file for drifted decisions"')
    print()

    return 0
