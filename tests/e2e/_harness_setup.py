"""Shared test-harness setup helpers.

Used by:
  - tests/e2e/run_e2e_flows.py (headless ``claude -p`` assertion test)
  - tests/e2e/record_demo_interactive.sh (interactive tmux-driven recording)

Both code paths must produce IDENTICAL artifacts (materialized MCP config,
materialized claude settings with hooks, bootstrapped ``.bicameral/``) so the
agent sees the same hook substrate and same MCP config regardless of which
entry point invoked it. This module is the single source of truth for that
materialization — no inline duplication in either consumer.

A CLI entry point exists so shell scripts can invoke the same logic as the
Python harness without re-implementing it inline. See ``__main__``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys


def materialize_mcp_config(
    template: pathlib.Path,
    out_dir: pathlib.Path,
    desktop_repo_path: str,
    ledger_dir: pathlib.Path,
) -> pathlib.Path:
    """Read the MCP config template, substitute env-var placeholders, write
    a runtime copy to ``<out_dir>/bicameral.mcp.materialized.json``.

    The template uses ``${DESKTOP_REPO_PATH}`` and ``${LEDGER_DIR}`` so the
    same template works locally (any clone path) and in CI (the workflow's
    clone path). Claude Code's MCP spawn behaviour for env replacement vs
    merge is implementation-defined; passing REPO_PATH explicitly via the
    config avoids that ambiguity.
    """
    raw = template.read_text(encoding="utf-8")
    materialized = raw.replace("${DESKTOP_REPO_PATH}", desktop_repo_path).replace(
        "${LEDGER_DIR}", str(ledger_dir)
    )
    out = out_dir / "bicameral.mcp.materialized.json"
    out.write_text(materialized, encoding="utf-8")
    return out


def materialize_settings_with_hooks(
    out_dir: pathlib.Path,
    mcp_config_path: pathlib.Path,
    mcp_root: pathlib.Path,
) -> pathlib.Path:
    """Write a project-style ``settings.json`` carrying the three hooks
    bicameral's setup-wizard installs in real projects. The PostToolUse and
    UserPromptSubmit commands are byte-exact strings imported from
    ``setup_wizard`` — single source of truth, no drift.

    The SessionEnd command is built via ``setup_wizard._build_session_end_command``
    with ``mcp_config_path`` set. Production end-users have ``bicameral``
    registered in their default Claude Code MCP config so the spawned
    subprocess inherits it without an explicit flag; test harnesses
    override ``SURREAL_URL`` via the materialized MCP config to point at
    a test-results ledger, so we MUST pass that config explicitly to the
    subprocess or its ``capture-corrections`` writes land in the user's
    default ledger and post-hoc validators find zero rows.

    Hooks installed:
      - PostToolUse/Bash: bicameral-sync listens for "new commit detected"
        output to auto-fire ``link_commit``.
      - SessionEnd: spawns a subprocess running
        ``/bicameral:capture-corrections --auto-ingest`` (with the test
        MCP config) to scan the just-ended session for uningested
        mid-session corrections.
      - UserPromptSubmit: deterministic verb-list classifier injects a
        <system-reminder> elevating bicameral.preflight above the agent's
        default tool-selection priority on code-implementation prompts.
    """
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from setup_wizard import (  # noqa: E402
        _BICAMERAL_POST_COMMIT_COMMAND,
        _BICAMERAL_PREFLIGHT_REMINDER_COMMAND,
        _build_session_end_command,
    )

    session_end_command = _build_session_end_command(mcp_config_path=str(mcp_config_path))

    settings = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": _BICAMERAL_POST_COMMIT_COMMAND}],
                }
            ],
            "SessionEnd": [
                {
                    "hooks": [{"type": "command", "command": session_end_command}],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {"type": "command", "command": _BICAMERAL_PREFLIGHT_REMINDER_COMMAND}
                    ],
                }
            ],
        }
    }
    out = out_dir / "claude-settings-with-hook.json"
    out.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return out


def clean_ledger(ledger_dir: pathlib.Path) -> None:
    """Wipe the persistent ledger between harness runs.

    State must persist across the 5 sequential claude sessions within a run
    (so the PM in flow 5 sees decisions from flows 1/2/4), but must NOT leak
    across runs (so each run is reproducible and CI is deterministic).
    """
    if ledger_dir.exists():
        shutil.rmtree(ledger_dir, ignore_errors=True)


def reset_desktop_repo(desktop_repo_path: str) -> None:
    """Reset desktop-clone to its pinned HEAD between runs. Flow 3 makes a
    real commit; without a reset, the second-onwards run starts from a
    polluted base.
    """
    repo = pathlib.Path(desktop_repo_path)
    if not (repo / ".git").exists():
        return
    for args in (("git", "reset", "--hard", "FETCH_HEAD"), ("git", "reset", "--hard", "HEAD")):
        try:
            subprocess.run(args, cwd=repo, check=True, capture_output=True, timeout=20)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue


def bootstrap_bicameral_dir(desktop_repo_path: str, mcp_root: pathlib.Path) -> None:
    """Create a minimal ``.bicameral/`` inside ``desktop_repo_path`` so the
    SessionEnd hook's ``[ -d .bicameral ]`` guard passes when the parent
    claude session exits. Without this, the hook short-circuits silently
    and Flow 4's path-X-(b) ledger validation has nothing to observe.

    Reuses ``setup_wizard._write_collaboration_config`` to write the same
    minimal ``config.yaml`` (mode=solo, guided=false, telemetry=false) a
    fresh end-user install would produce — single source of truth.

    Wiped + recreated each run so flows do not inherit cross-run state.
    """
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from setup_wizard import _write_collaboration_config  # noqa: E402

    bicameral_dir = pathlib.Path(desktop_repo_path) / ".bicameral"
    if bicameral_dir.exists():
        shutil.rmtree(bicameral_dir, ignore_errors=True)
    _write_collaboration_config(
        data_path=pathlib.Path(desktop_repo_path),
        mode="solo",
        guided=False,
        telemetry=False,
    )


def setup_all(
    desktop_repo_path: str,
    results_dir: pathlib.Path,
    mcp_config_template: pathlib.Path,
    mcp_root: pathlib.Path,
    clean: bool = True,
) -> dict[str, pathlib.Path]:
    """Run every setup step in the canonical order. Returns the resulting
    artifact paths so consumers can wire them through to the agent invocation.

    When ``clean=True`` (default), wipes the ledger and resets the desktop
    repo first. The harness uses this; the recording script uses it too —
    state must persist across flows within a run, but not across runs.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    ledger_dir = results_dir / "ledger.db"
    if clean:
        clean_ledger(ledger_dir)
        reset_desktop_repo(desktop_repo_path)
    bootstrap_bicameral_dir(desktop_repo_path, mcp_root)
    mcp_config_path = materialize_mcp_config(
        mcp_config_template, results_dir, desktop_repo_path, ledger_dir
    )
    settings_path = materialize_settings_with_hooks(results_dir, mcp_config_path, mcp_root)
    return {"mcp_config": mcp_config_path, "settings": settings_path, "ledger": ledger_dir}


def main() -> int:
    """CLI entrypoint for shell consumers (record_demo_interactive.sh).

    Prints the resulting artifact paths as ``<key>\\t<path>`` lines on
    stdout so the shell can parse them with ``awk`` or ``cut`` if it
    needs to thread them through to subsequent commands.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--desktop-repo-path", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--mcp-config-template", required=True)
    p.add_argument("--mcp-root", required=True)
    p.add_argument(
        "--no-clean",
        action="store_true",
        help="skip ledger wipe + desktop-clone reset (default: wipe + reset)",
    )
    args = p.parse_args()

    paths = setup_all(
        desktop_repo_path=args.desktop_repo_path,
        results_dir=pathlib.Path(args.results_dir),
        mcp_config_template=pathlib.Path(args.mcp_config_template),
        mcp_root=pathlib.Path(args.mcp_root),
        clean=not args.no_clean,
    )
    for key, path in paths.items():
        print(f"{key}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
