"""Regression guard: the MCP-local CodeGenome governance side door stays retired.

mcp#624 / RFQ 10 (bicameral-bot#221, ADR-0024). The fat-server side door turned
CodeGenome *confidence signals* into *durable authority*:

  * `codegenome/drift_service.py::_write_auto_resolution()` wrote a
    `compliance_check` row `verdict="compliant"` when `confidence >= 0.80`;
  * `codegenome/continuity_service.py::_persist_resolved_match()` flipped a
    decision's `binds_to` region from an approximate match score
    (`update_binds_to_region`);
  * `handlers/link_commit.py` orchestrated both via `_run_drift_classification_pass`
    and `_run_continuity_pass`.

The thin-client migration deleted that entire surface: the thin client only
marshals ToolRequests to the bot daemon, so CodeGenome→authority materialization
can only happen behind the governed bot front doors (`binding.create` verified-only,
`review.resolve_compliance`). This test pins that boundary so the side door cannot
silently return — e.g. by a future re-vendoring of `codegenome/` or `handlers/`
into the thin-client package.

Per ADR-0024, CodeGenome-derived values are signal/identity inputs: they may rank,
warn, propose, and explain, but must never cross an authority gate. CodeGenome the
substrate is *not* killed here — it lives in the bot; this test only forbids the
MCP-local authority coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tool_request import MCP_TOOL_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Thin-client package modules (the only Python that ships in the wheel).
_SOURCE_MODULES = (
    "authority.py",
    "daemon_client.py",
    "prompts.py",
    "responses.py",
    "server.py",
    "tool_request.py",
    "tool_schemas.py",
    "version.py",
)

# Directories that carried the fat-server governance/authority surface. Their
# reappearance in the thin-client package is the regression #624 guards against.
_FORBIDDEN_DIRS = (
    "codegenome",
    "handlers",
    "ledger",
    "adapters",
    "governance",
)

# Symbols that *were* the side door. None may appear in thin-client source.
_SIDE_DOOR_SYMBOLS = (
    "compliance_check",
    "binds_to",
    "update_binds_to_region",
    "_write_auto_resolution",
    "_persist_resolved_match",
    "_run_drift_classification_pass",
    "_run_continuity_pass",
    "drift_service",
    "continuity_service",
)


def test_fat_server_authority_directories_absent():
    for name in _FORBIDDEN_DIRS:
        assert not (_REPO_ROOT / name).exists(), (
            f"{name}/ reappeared in the thin client — the CodeGenome/authority "
            f"side door (#624) must stay retired"
        )


def test_no_side_door_symbols_in_thin_client_source():
    offenders: dict[str, list[str]] = {}
    for module in _SOURCE_MODULES:
        text = (_REPO_ROOT / module).read_text(encoding="utf-8")
        hits = [sym for sym in _SIDE_DOOR_SYMBOLS if sym in text]
        if hits:
            offenders[module] = hits
    assert not offenders, f"side-door symbols leaked into thin-client source: {offenders}"


def test_thin_client_imports_no_codegenome_or_authority_modules():
    forbidden_roots = set(_FORBIDDEN_DIRS) | {"codegenome"}
    for module in _SOURCE_MODULES:
        tree = ast.parse((_REPO_ROOT / module).read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert roots.isdisjoint(forbidden_roots), (
            f"{module} imports a retired authority module: {roots & forbidden_roots}"
        )


def test_link_commit_side_door_orchestrator_not_exposed():
    # `link_commit` was the side-door entrypoint; it must not be a tool command.
    assert "bicameral.link_commit" not in MCP_TOOL_COMMANDS
    assert "link_commit" not in MCP_TOOL_COMMANDS.values()


def test_binding_and_compliance_only_route_through_governed_bot_commands():
    # The only way to touch binding / compliance state from MCP is via the
    # governed bot front doors — both go to the daemon, neither is MCP-local.
    assert MCP_TOOL_COMMANDS["bicameral.bind"] == "binding.create"
    assert MCP_TOOL_COMMANDS["bicameral.review.resolve_compliance"] == "review.resolve_compliance"


def test_thin_client_has_no_local_persistence_capability():
    # A side door needs somewhere to write. The thin client must not import any
    # database / persistence client; all state lives in the bot.
    persistence_roots = {"sqlite3", "surrealdb", "psycopg", "psycopg2", "sqlalchemy"}
    for module in _SOURCE_MODULES:
        tree = ast.parse((_REPO_ROOT / module).read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert roots.isdisjoint(persistence_roots), (
            f"{module} imports a persistence client {roots & persistence_roots} — "
            f"the thin client must hold no durable store (#624)"
        )
