"""Project Pulse — the shared backend summary object behind GitHub #437.

This package ships the structured, read-only ``ProjectPulseSummary`` that
future render surfaces (CLI ``bicameral-mcp brief``, the dashboard Project
Pulse view, and future Slack/email channels) all consume. Phase 1 builds
*only* the data object and its builder — no renderer, no CLI command, no
dashboard view.

See ``pulse/summary.py`` for the dataclasses and ``build_project_pulse``.
"""

from __future__ import annotations

from pulse.summary import (
    Health,
    LearnedItem,
    NeedsAttentionItem,
    ProjectPulseSummary,
    build_project_pulse,
)

__all__ = [
    "Health",
    "LearnedItem",
    "NeedsAttentionItem",
    "ProjectPulseSummary",
    "build_project_pulse",
]
