"""``FilterSpec`` — typed config schema for universal ingest filters.

Defaults are all "no-filter" (empty list / empty string) so a source-
config entry without a ``filters:`` block is a true pass-through.
Validation: pydantic ensures lists are lists, strings are strings, and
unknown fields under ``extensions`` are preserved (opaque) for the
source-specific evaluators that consume them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FilterSpec(BaseModel):
    """Universal filter primitives applied uniformly across all sources.

    Each polling adapter:
    1. Reads its config entry's ``filters:`` block into a ``FilterSpec``.
    2. For each candidate item, normalizes to ``{text, author, timestamp}``.
    3. Calls ``evaluate_universal(candidate, spec)``.
    4. Skips items where the evaluator returns False; watermark still
       advances past them.
    """

    keyword_include: list[str] = Field(
        default_factory=list,
        description=(
            "Case-insensitive substring match. Item passes if its text "
            "contains AT LEAST ONE entry. Empty list disables this filter."
        ),
    )
    keyword_exclude: list[str] = Field(
        default_factory=list,
        description=(
            "Case-insensitive substring match. Item rejected if its text "
            "contains ANY entry. Empty list disables this filter."
        ),
    )
    author_include: list[str] = Field(
        default_factory=list,
        description=(
            "Exact-match allowlist on the candidate's author identifier "
            "(email / login / user_id per source). Empty list disables."
        ),
    )
    author_exclude: list[str] = Field(
        default_factory=list,
        description="Exact-match blocklist on author. Empty list disables.",
    )
    time_window_after: str = Field(
        default="",
        description=(
            "ISO 8601 timestamp. Item must have a timestamp strictly "
            "AFTER this. Empty string disables. Lexicographic comparison "
            "(safe for ISO 8601 + UTC)."
        ),
    )
    time_window_before: str = Field(
        default="",
        description=(
            "ISO 8601 timestamp. Item must have a timestamp strictly "
            "BEFORE this. Empty string disables."
        ),
    )
    eval_hook: str = Field(
        default="",
        description=(
            "Dotted import path to an operator-defined callable that returns "
            "True for candidates that should pass the filter. Shape: "
            "``'module.submodule:function_name'``. The callable receives "
            "the same normalized candidate dict the universal evaluator "
            "sees (``{text, author, timestamp}``) and must return a bool. "
            "Hook runs AFTER the universal primitives — cheap declarative "
            "filters cull first, expensive operator logic only sees survivors. "
            "Operator's hook code runs in the same Python process; we don't "
            "sandbox (operator owns their Bicameral install). Resolution "
            "errors, non-bool returns, and exceptions all reject the "
            "candidate conservatively and log to stderr."
        ),
    )
    extensions: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Reserved for source-specific extension dimensions (Slack "
            "reactions, GitHub paths, Linear labels, Notion tags). The "
            "universal evaluator ignores this dict; per-source extension "
            "evaluators consume it. Schema-free by design — each source "
            "documents its own keys."
        ),
    )

    # Reject unknown top-level fields so a typo in config (e.g.
    # `keywords_include` vs `keyword_include`) fails loud instead of
    # silently disabling the filter.
    model_config = ConfigDict(extra="forbid")
