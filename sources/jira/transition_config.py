"""Jira status-transition config loader (#337 Jira Phase C).

Phase C makes a Jira issue's transition into a "done-like" terminal status a
decision-bearing event. Which statuses count is **operator config**, declared
per project in ``.bicameral/config.yaml``:

    jira:
      status_transitions:
        PROJ: ["Done", "Released"]
        OPS:  ["Closed"]

- ``jira.status_transitions`` is a map of **project key -> list of terminal
  status display names**.
- The map keys ARE the **project allowlist**: a project absent from the map
  gets no transition-ingest at all.
- Status-name and project-key matching is **case-insensitive** (casefolded),
  so ``"done"`` and ``"Done"`` — and ``proj`` vs ``PROJ`` — match.
- Absent or malformed config means transition-ingest is simply **off**
  (`load_terminal_statuses` returns an empty map). It is never an error: a
  config mistake must not crash the webhook receiver and must never be read
  as "ingest every transition".

The config file is resolved relative to ``REPO_PATH`` (default ``.``) — the
same resolution ``context.py`` uses — so the server's CWD does not matter.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _config_path(repo_path: str | None) -> Path:
    """Resolve ``<repo>/.bicameral/config.yaml`` the way ``context.py`` does."""
    base = repo_path if repo_path else os.getenv("REPO_PATH", ".")
    return Path(base) / ".bicameral" / "config.yaml"


def load_terminal_statuses(repo_path: str | None = None) -> dict[str, frozenset[str]]:
    """Load the per-project terminal-status map — fail-closed.

    Returns ``{casefolded_project_key: frozenset(casefolded status names)}``.
    Any absent or malformed config yields an empty map (transition-ingest
    off). This function **never raises** — a config error must not break
    webhook delivery.

    Loaded fresh on each call (no caching) so an operator's edit to
    ``config.yaml`` takes effect with no restart; the v0 webhook volume
    makes a per-delivery YAML read a non-issue.
    """
    path = _config_path(repo_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # File absent / unreadable — transition-ingest is simply off.
        return {}

    try:
        import yaml

        data = yaml.safe_load(raw)
    except Exception as exc:  # noqa: BLE001 — any parse failure fails closed
        logger.warning("[jira-transition-config] %s not parseable as YAML: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        return {}
    jira = data.get("jira")
    if not isinstance(jira, dict):
        return {}
    transitions = jira.get("status_transitions")
    if transitions is None:
        return {}
    if not isinstance(transitions, dict):
        logger.warning(
            "[jira-transition-config] jira.status_transitions is %s, expected a "
            "mapping of project-key -> status list; ignoring",
            type(transitions).__name__,
        )
        return {}

    out: dict[str, frozenset[str]] = {}
    for project, statuses in transitions.items():
        if not isinstance(project, str) or not project.strip():
            logger.warning("[jira-transition-config] skipping non-string project key %r", project)
            continue
        if not isinstance(statuses, list):
            logger.warning(
                "[jira-transition-config] skipping project %r: statuses is %s, expected a list",
                project,
                type(statuses).__name__,
            )
            continue
        names = frozenset(
            s.strip().casefold() for s in statuses if isinstance(s, str) and s.strip()
        )
        if names:
            out[project.strip().casefold()] = names
    return out
