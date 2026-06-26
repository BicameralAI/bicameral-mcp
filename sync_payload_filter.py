"""Scope-filter and token-budget for _pending_compliance_checks (#504).

The daemon may attach _pending_compliance_checks to any ToolResponse when a
HEAD-move catch-up produces pending compliance work.  Without filtering, the
full array is serialized into the outer tool response regardless of caller
scope or payload size — producing huge irrelevant responses that bury the
actual tool result and train agents to dismiss the compliance surface.

This module applies three layers before the response reaches MCP formatters:

1. **Scope filter** — when the caller supplies ``file_paths`` (preflight,
   lookup, etc.), keep only checks whose ``file_path`` shares a top-level
   directory with at least one caller path.
2. **Zero-overlap short-circuit** — when the caller's ``file_paths`` have
   no overlap with any check's ``file_path``, attach nothing (no checks,
   no guidance).
3. **Token budget** — if the scoped checks exceed the configured char
   budget, replace the array with a truncation digest and emit
   ``_sync_guidance`` pointing the agent at ``bicameral.history``.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_BUDGET_CHARS = 16_000


def _top_level_dir(path: str) -> str | None:
    """Extract the first path component (top-level directory/package)."""
    stripped = path.lstrip("/")
    if not stripped:
        return None
    return stripped.split("/")[0]


def _caller_top_dirs(file_paths: list[str]) -> set[str]:
    """Unique top-level directories from caller file_paths."""
    dirs: set[str] = set()
    for fp in file_paths:
        d = _top_level_dir(fp)
        if d:
            dirs.add(d)
    return dirs


def _check_file_path(check: dict[str, Any]) -> str | None:
    """Extract file_path from a compliance check dict.

    Supports both top-level ``file_path`` and nested ``code_region.file_path``.
    """
    fp = check.get("file_path")
    if fp:
        return fp
    region = check.get("code_region")
    if isinstance(region, dict):
        return region.get("file_path")
    return None


def scope_filter_checks(
    checks: list[dict[str, Any]],
    caller_file_paths: list[str],
) -> list[dict[str, Any]]:
    """Keep only checks whose file_path shares a top-level dir with the caller."""
    caller_dirs = _caller_top_dirs(caller_file_paths)
    if not caller_dirs:
        return checks

    result: list[dict[str, Any]] = []
    for check in checks:
        fp = _check_file_path(check)
        if fp is None:
            result.append(check)
            continue
        check_dir = _top_level_dir(fp)
        if check_dir and check_dir in caller_dirs:
            result.append(check)
    return result


def apply_budget(
    checks: list[dict[str, Any]],
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> list[dict[str, Any]] | dict[str, Any]:
    """If serialized checks exceed the budget, return a truncation digest."""
    serialized = json.dumps(checks)
    if len(serialized) <= budget_chars:
        return checks

    kept: list[dict[str, Any]] = []
    running_size = 2  # opening/closing brackets
    for check in checks:
        entry = json.dumps(check)
        cost = len(entry) + (2 if kept else 0)  # comma + space
        if running_size + cost > budget_chars:
            break
        kept.append(check)
        running_size += cost

    return {
        "truncated": True,
        "total": len(checks),
        "kept": len(kept),
        "budget_chars": budget_chars,
        "hint": "Call bicameral.history to enumerate full compliance state.",
        "items": kept,
    }


def filter_pending_checks(
    response: dict[str, Any],
    caller_file_paths: list[str] | None,
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> None:
    """Filter and budget _pending_compliance_checks in a daemon response.

    Mutates ``response`` in place.  When the caller's file_paths have zero
    overlap with check file_paths, removes the compliance keys entirely.
    When within scope and below budget the payload is left byte-identical.
    """
    checks = response.get("_pending_compliance_checks")
    if not checks or not isinstance(checks, list):
        return

    if caller_file_paths is not None and len(caller_file_paths) > 0:
        scoped = scope_filter_checks(checks, caller_file_paths)

        if len(scoped) == 0:
            response.pop("_pending_compliance_checks", None)
            response.pop("_pending_flow_id", None)
            response.pop("_sync_guidance", None)
            return

        budgeted = apply_budget(scoped, budget_chars=budget_chars)
        response["_pending_compliance_checks"] = budgeted

        if isinstance(budgeted, dict) and budgeted.get("truncated"):
            response["_sync_guidance"] = (
                f"{budgeted['total']} pending compliance check(s), "
                f"{budgeted['kept']} shown within budget. "
                "Call bicameral.history to enumerate full compliance state."
            )
    else:
        budgeted = apply_budget(checks, budget_chars=budget_chars)
        response["_pending_compliance_checks"] = budgeted

        if isinstance(budgeted, dict) and budgeted.get("truncated"):
            response["_sync_guidance"] = (
                f"{budgeted['total']} pending compliance check(s), "
                f"{budgeted['kept']} shown within budget. "
                "Call bicameral.history to enumerate full compliance state."
            )
