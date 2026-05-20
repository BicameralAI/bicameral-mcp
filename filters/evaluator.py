"""Universal filter evaluator + spec-merging helper."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from filters.spec import FilterSpec


def evaluate_filters(candidate: dict, spec: FilterSpec) -> bool:
    """Combined filter gate: universal primitives + content-eval hook.

    Adapters call this once per candidate. Returns True iff the
    candidate passes both layers:

    1. :func:`evaluate_universal` — declarative primitives. Cheap.
    2. :func:`run_eval_hook` — operator-defined callable (only invoked
       when ``spec.eval_hook`` is non-empty). Expensive.

    The two-layer ordering keeps the operator hook on the hot path only
    for candidates that already survived the cheap declarative filter.
    """
    if not evaluate_universal(candidate, spec):
        return False
    if spec.eval_hook:
        return run_eval_hook(spec.eval_hook, candidate)
    return True


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


_HOOK_CACHE: dict[str, Callable[[dict], Any]] = {}
_FAILED_HOOKS: set[str] = set()


def run_eval_hook(hook_path: str, candidate: dict) -> bool:
    """Resolve ``hook_path`` (``"module.path:function_name"``) and run it.

    Returns False on any failure (malformed path, import error, callable
    not found, hook raises, non-bool return). Logs to stderr the first
    time each unique hook_path fails — subsequent items in the same
    process don't re-spam.

    Module + callable are cached after first successful resolution.
    Failed paths are remembered in ``_FAILED_HOOKS`` and short-circuit
    to False without re-attempting the import.

    Never raises. The operator's hook is treated as a best-effort filter,
    not a critical gate — filter failures should not kill the poll loop.
    """
    import importlib
    import sys

    fn = _HOOK_CACHE.get(hook_path)
    if fn is None:
        if hook_path in _FAILED_HOOKS:
            return False
        if ":" not in hook_path:
            print(
                f"[filters] eval_hook {hook_path!r} malformed (expected "
                "'module.path:function_name'); items will be rejected.",
                file=sys.stderr,
            )
            _FAILED_HOOKS.add(hook_path)
            return False
        module_path, _, attr = hook_path.partition(":")
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 — surface, never crash poller
            print(
                f"[filters] eval_hook {hook_path!r} import failed "
                f"({type(exc).__name__}: {exc}); items will be rejected.",
                file=sys.stderr,
            )
            _FAILED_HOOKS.add(hook_path)
            return False
        try:
            fn = getattr(mod, attr)
        except AttributeError:
            print(
                f"[filters] eval_hook {hook_path!r}: module imported but "
                f"has no attribute {attr!r}; items will be rejected.",
                file=sys.stderr,
            )
            _FAILED_HOOKS.add(hook_path)
            return False
        if not callable(fn):
            print(
                f"[filters] eval_hook {hook_path!r}: {attr!r} is not "
                "callable; items will be rejected.",
                file=sys.stderr,
            )
            _FAILED_HOOKS.add(hook_path)
            return False
        _HOOK_CACHE[hook_path] = fn

    try:
        result = fn(candidate)
    except Exception as exc:  # noqa: BLE001 — hook is operator code, treat as filter
        print(
            f"[filters] eval_hook {hook_path!r} raised on candidate "
            f"({type(exc).__name__}: {exc}); item rejected.",
            file=sys.stderr,
        )
        return False
    if not isinstance(result, bool):
        print(
            f"[filters] eval_hook {hook_path!r} returned non-bool "
            f"({type(result).__name__}); item rejected. Hook must return bool.",
            file=sys.stderr,
        )
        return False
    return result


def _reset_hook_caches_for_tests() -> None:
    """Test-only — clear the module-level resolution caches."""
    _HOOK_CACHE.clear()
    _FAILED_HOOKS.clear()


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
