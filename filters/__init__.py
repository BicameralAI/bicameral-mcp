"""Universal filter primitives for ingest sources (#337 foundations cycle 2).

Shared filter vocabulary across all source adapters. Each polling
adapter consumes a ``FilterSpec`` from its config entry, normalizes each
candidate item to a common shape (``{text, author, timestamp}``), and
passes the pair through :func:`evaluate_universal` before deciding
whether to include the item in the ingest batch. Items that fail the
filter are skipped; the watermark still advances past them.

Universal primitives (work for every source):
- ``keyword_include`` — case-insensitive OR-match over the candidate's
  text. Empty list disables the filter.
- ``keyword_exclude`` — case-insensitive NOT-ANY match.
- ``author_include`` / ``author_exclude`` — exact match on the author
  identifier (email, login, user_id — whatever the source emits).
- ``time_window_after`` / ``time_window_before`` — ISO 8601 lexicographic
  comparison on the candidate's timestamp.

Source-specific extensions (Slack reactions, GitHub paths, Linear
labels, Notion tags) are deferred to a follow-up cycle. The
``FilterSpec.extensions`` dict is reserved for that work — currently
opaque to the universal evaluator.

Per-resource overrides: a source-config entry may carry a top-level
``filters:`` block (applies to every resource) and individual resources
may carry their own ``filters:`` block (merged on top of the source-level
one via :func:`merge_specs`). Per-resource filters override source-level
filters for the same field; non-overridden fields inherit.
"""

from filters.evaluator import (
    evaluate_filters,
    evaluate_universal,
    merge_specs,
    run_eval_hook,
)
from filters.spec import FilterSpec

__all__ = [
    "FilterSpec",
    "evaluate_filters",
    "evaluate_universal",
    "merge_specs",
    "run_eval_hook",
]
