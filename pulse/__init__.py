"""Project Pulse — the shared backend summary object behind GitHub #437.

This package ships the structured, read-only ``ProjectPulseSummary`` that the
render surfaces (CLI ``bicameral-mcp brief``, the dashboard Project Pulse
view, and future Slack/email channels) all consume. Phase 1 built the data
object + ``build_project_pulse``; Phase 2 adds ``render_pulse_text`` — the
shared plain-text renderer used by ``brief`` and ``sync-and-brief``.

See ``pulse/summary.py`` for the dataclasses and ``build_project_pulse``, and
``pulse/render.py`` for ``render_pulse_text``.
"""

from __future__ import annotations

from pulse.render import render_pulse_text
from pulse.summary import (
    Health,
    LearnedItem,
    NeedsAttentionItem,
    ProjectPulseSummary,
    SinceParseError,
    build_project_pulse,
)

__all__ = [
    "Health",
    "LearnedItem",
    "NeedsAttentionItem",
    "ProjectPulseSummary",
    "SinceParseError",
    "build_project_pulse",
    "render_pulse_text",
]
