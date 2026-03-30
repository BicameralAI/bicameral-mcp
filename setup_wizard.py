"""Interactive setup wizard for bicameral-mcp.

Guides the user through selecting a repo and coding agent, then installs
the MCP server config + skills.

Supports: Claude Code, Cursor

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
        "config_path": lambda repo: repo / ".mcp.json",
        "skills": True,
    },
    "cursor": {
        "name": "Cursor",
        "config_path": lambda repo: repo / ".cursor" / "mcp.json",
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
    return found


def _select_agents() -> list[str]:
    """Prompt user to select coding agents."""
    detected = _detect_agents()

    if detected:
        names = ", ".join(AGENTS[a]["name"] for a in detected)
        print(f"  Detected: {names}")
        answer = input("  Install for all detected? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return detected

    print("\n  Select coding agents to configure:")
    print("    1. Claude Code")
    print("    2. Cursor")
    print("    3. Both")
    choice = input("  Choice [1/2/3]: ").strip()

    if choice == "2":
        return ["cursor"]
    elif choice == "3":
        return ["claude", "cursor"]
    else:
        return ["claude"]


def _detect_runner() -> tuple[str, list[str]]:
    """Detect the best available Python package runner."""
    if shutil.which("uvx"):
        return ("uvx", ["bicameral-mcp"])
    if shutil.which("pipx"):
        return ("pipx", ["run", "bicameral-mcp"])
    python = "python3" if shutil.which("python3") else "python"
    return (python, ["-m", "bicameral_mcp"])


def _build_config(repo_path: Path) -> dict:
    """Build the MCP server config object."""
    command, args = _detect_runner()
    data_dir = repo_path / ".bicameral"
    data_dir.mkdir(parents=True, exist_ok=True)

    return {
        "command": command,
        "args": args,
        "env": {
            "REPO_PATH": str(repo_path),
            "SURREAL_URL": f"surrealkv://{data_dir / 'ledger.db'}",
            "CODE_LOCATOR_SQLITE_DB": str(data_dir / "code-graph.db"),
        },
    }


def _write_mcp_config(repo_path: Path, config_path: Path) -> None:
    """Write MCP server config to a JSON file."""
    config = _build_config(repo_path)

    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing.setdefault("mcpServers", {})["bicameral"] = config
    config_path.write_text(json.dumps(existing, indent=2) + "\n")


def _install_for_agent(agent_key: str, repo_path: Path) -> bool:
    """Install MCP config for a specific coding agent."""
    agent = AGENTS[agent_key]
    config_path = agent["config_path"](repo_path)

    # For Claude Code, try CLI first
    if agent_key == "claude" and shutil.which("claude"):
        config = _build_config(repo_path)
        config_json = json.dumps(config)
        # Remove existing, then add
        subprocess.run(
            ["claude", "mcp", "remove", "bicameral", "--scope", "local"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        result = subprocess.run(
            ["claude", "mcp", "add-json", "bicameral", "--scope", "local", config_json],
            capture_output=True, text=True, timeout=10, cwd=str(repo_path),
        )
        if result.returncode == 0:
            print(f"  {agent['name']}: installed via CLI")
            return True

    # Fallback / Cursor: write config file directly
    _write_mcp_config(repo_path, config_path)
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


def _ensure_gitignore(repo_path: Path) -> None:
    """Add .bicameral/ to .gitignore if not already there."""
    gitignore = repo_path / ".gitignore"
    entry = ".bicameral/"

    if gitignore.exists():
        content = gitignore.read_text()
        if entry in content:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# Bicameral MCP local data\n{entry}\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(f"# Bicameral MCP local data\n{entry}\n")

    print(f"  Added {entry} to .gitignore")


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
    if command not in ("uvx", "pipx"):
        print(f"\n  Note: using '{command} -m bicameral_mcp' as runner.")
        print("  Install a package runner for zero-install: pip install pipx")

    # Step 4: Prepare local data + gitignore
    _ensure_gitignore(repo_path)

    # Step 5: Install MCP config for each agent
    print()
    for agent_key in agents:
        _install_for_agent(agent_key, repo_path)

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
