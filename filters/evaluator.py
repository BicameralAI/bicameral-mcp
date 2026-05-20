"""Universal filter evaluator + spec-merging helper."""

from __future__ import annotations

from filters.spec import FilterSpec


def evaluate_universal(candidate: dict, spec: FilterSpec) -> bool:
    """Return True if ``candidate`` passes every universal primitive in ``spec``.

    ``candidate`` is a normalized dict with the keys the evaluator reads:
    - ``text`` (str): the body the keyword filters apply to. Adapters
      concatenate title + body + comments as appropriate before passing.
    - ``author`` (str): the identifier used by author_include/exclude.
      Source-specific — email for sources that surface it, login or
      user_id otherwise.
    - ``timestamp`` (str): ISO 8601 timestamp for the time-window filters.
      Empty string is treated as "unknown" — time-window filters that are
      configured will REJECT an unknown-timestamp candidate (conservative).

    Missing keys in ``candidate`` are treated as empty strings, which
    means the corresponding filter (if configured) will reject the
    candidate. Adapters are responsible for populating all three keys
    before calling; the conservative-reject behavior catches missed
    normalization at runtime.
    """
    text = (candidate.get("text") or "").lower()

    if spec.keyword_include:
        haystack_lower = text
        if not any(kw.lower() in haystack_lower for kw in spec.keyword_include):
            return False

    if spec.keyword_exclude:
        haystack_lower = text
        if any(kw.lower() in haystack_lower for kw in spec.keyword_exclude):
            return False

    author = candidate.get("author") or ""
    if spec.author_include and author not in spec.author_include:
        return False
    if spec.author_exclude and author in spec.author_exclude:
        return False

    ts = candidate.get("timestamp") or ""
    if spec.time_window_after:
        if not ts or ts <= spec.time_window_after:
            return False
    if spec.time_window_before:
        if not ts or ts >= spec.time_window_before:
            return False

    return True


def merge_specs(source_level: FilterSpec, resource_level: FilterSpec | None) -> FilterSpec:
    """Merge a resource-level spec on top of the source-level default.

    A resource-level field that was **explicitly set** in the YAML config
    overrides the source-level value, even when explicitly set to empty
    (e.g. ``author_exclude: []`` clears the source-level exclude list).
    A field that was **not present** in the resource-level YAML inherits
    the source-level value.

    The distinction is detected via pydantic's ``model_fields_set``,
    which carries which keys came from the input dict — independent of
    whether their value happens to equal the default.

    The ``extensions`` dict merges shallowly: resource-level keys
    override source-level keys with the same name. Unset extension keys
    at the resource level always inherit (no "clear" semantics for
    individual extension entries; that's up to per-source extension
    evaluators).
    """
    if resource_level is None:
        return source_level
    merged = source_level.model_dump()
    res_dump = resource_level.model_dump()
    explicit = resource_level.model_fields_set
    for field in (
        "keyword_include",
        "keyword_exclude",
        "author_include",
        "author_exclude",
        "time_window_after",
        "time_window_before",
    ):
        if field in explicit:
            merged[field] = res_dump[field]
    merged["extensions"] = {**source_level.extensions, **resource_level.extensions}
    return FilterSpec(**merged)
