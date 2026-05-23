"""Phase 2c-1 invariants: every externally-callable handler is tagged with
exactly one categorization decorator and the declared protocol method name
matches the decorator's prefix.

These checks live at the module level so they fire at test-collection time —
a new handler that forgets a decorator fails CI before any test runs.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable

import pytest

import handlers
from protocol.categorization import (
    GROUNDING_ANALYZE_PREFIX,
    GROUNDING_LOOKUP_PREFIX,
    READ_PREFIX,
    SYSTEM_PREFIX,
    WRITE_PREFIX,
    Category,
    ProtocolMethodNameError,
    get_category,
    get_method,
    grounding_analyze,
    grounding_lookup,
    is_categorized,
    read_tool,
    system_tool,
    write_tool,
)

# Internal-use-only handlers: queried by other handlers, never reached by
# external callers, so they are not part of the protocol surface and stay
# undecorated. Keep this list short; new entries need a one-line rationale.
INTERNAL_HANDLER_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Internal queries called by gap_judge, preflight, brief flow:
        "handlers.search_decisions.handle_search_decisions",
        # Internal helpers for drift detection used by preflight + scan_branch:
        "handlers.detect_drift.handle_detect_drift",
        # Internal status projector used by ratify + resolve_compliance:
        "handlers.decision_status.handle_decision_status",
    }
)


def _iter_handler_functions() -> list[tuple[str, Callable[..., object]]]:
    """Yield every ``handle_*`` callable across ``handlers/*.py``.

    Walks the ``handlers`` package, imports each submodule, and pulls out
    module-level coroutine functions whose name starts with ``handle_``.
    Skips ``__init__`` and any module whose name leads with ``_``.
    """
    found: list[tuple[str, Callable[..., object]]] = []
    for module_info in pkgutil.iter_modules(handlers.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"handlers.{module_info.name}")
        for attr_name, value in inspect.getmembers(module):
            if not attr_name.startswith("handle_"):
                continue
            if not inspect.iscoroutinefunction(value):
                continue
            if value.__module__ != module.__name__:
                continue  # re-export, not the canonical home
            qualname = f"{value.__module__}.{value.__name__}"
            found.append((qualname, value))
    return found


_PREFIX_BY_CATEGORY: dict[Category, str] = {
    Category.READ: READ_PREFIX,
    Category.WRITE: WRITE_PREFIX,
    Category.GROUNDING_LOOKUP: GROUNDING_LOOKUP_PREFIX,
    Category.GROUNDING_ANALYZE: GROUNDING_ANALYZE_PREFIX,
    Category.SYSTEM: SYSTEM_PREFIX,
}


def test_every_external_handler_is_decorated() -> None:
    """No externally-callable handler is missing a categorization decorator.

    Internal helpers may be undecorated, but they must be on the allowlist —
    otherwise we can't tell "intentionally internal" from "accidentally forgot
    to decorate".
    """
    missing: list[str] = []
    for qualname, fn in _iter_handler_functions():
        if is_categorized(fn):
            continue
        if qualname in INTERNAL_HANDLER_ALLOWLIST:
            continue
        missing.append(qualname)
    assert not missing, (
        "Handlers without a categorization decorator (and not on the internal "
        "allowlist):\n  " + "\n  ".join(missing)
    )


def test_decorator_prefix_matches_method_name() -> None:
    """A handler tagged ``@read_tool`` must declare a ``"read.*"`` method."""
    mismatches: list[str] = []
    for qualname, fn in _iter_handler_functions():
        if not is_categorized(fn):
            continue
        category = get_category(fn)
        method = get_method(fn)
        assert category is not None and method is not None
        expected_prefix = _PREFIX_BY_CATEGORY[category]
        if not method.startswith(expected_prefix):
            mismatches.append(f"{qualname}: '{method}' missing prefix '{expected_prefix}'")
    assert not mismatches, "Decorator/method prefix mismatch:\n  " + "\n  ".join(mismatches)


def test_protocol_methods_are_unique() -> None:
    """Two handlers cannot register the same wire method — that's an ambiguous
    dispatch."""
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for qualname, fn in _iter_handler_functions():
        if not is_categorized(fn):
            continue
        method = get_method(fn)
        assert method is not None
        if method in seen:
            duplicates.append(f"{method}: {seen[method]} and {qualname}")
        else:
            seen[method] = qualname
    assert not duplicates, "Duplicate protocol methods:\n  " + "\n  ".join(duplicates)


def test_internal_allowlist_entries_exist() -> None:
    """Catch dead entries: every name on the allowlist must actually resolve
    to an undecorated handler today. Renames or deletions should prune the
    allowlist in the same PR."""
    found_qualnames = {qualname for qualname, _ in _iter_handler_functions()}
    stale = [name for name in INTERNAL_HANDLER_ALLOWLIST if name not in found_qualnames]
    assert not stale, "Stale internal-handler allowlist entries:\n  " + "\n  ".join(stale)


def test_internal_allowlist_handlers_are_actually_undecorated() -> None:
    """Catch contradictions: a name on the internal allowlist that ALSO carries
    a decorator means a reviewer probably copied a line. Force them to choose."""
    contradictions: list[str] = []
    for qualname, fn in _iter_handler_functions():
        if qualname in INTERNAL_HANDLER_ALLOWLIST and is_categorized(fn):
            contradictions.append(f"{qualname} is on the internal allowlist but is decorated")
    assert not contradictions, "Allowlist/decorator contradiction:\n  " + "\n  ".join(
        contradictions
    )


def test_decorator_mismatch_raises() -> None:
    """The decorator itself rejects a category/method mismatch at import time."""
    with pytest.raises(ProtocolMethodNameError):

        @read_tool("write.foo")  # type: ignore[arg-type]
        async def _bad() -> None: ...


def test_double_decoration_raises() -> None:
    """Stacking two decorators on one handler is an error — pick one category."""
    with pytest.raises(ProtocolMethodNameError):

        @write_tool("write.a")
        @read_tool("read.a")
        async def _doubled() -> None: ...


def test_all_five_categories_smoke() -> None:
    """Sanity: each decorator accepts its own prefix without raising."""

    @read_tool("read.x")
    async def _r() -> None: ...

    @write_tool("write.x")
    async def _w() -> None: ...

    @grounding_lookup("grounding.lookup.x")
    async def _gl() -> None: ...

    @grounding_analyze("grounding.analyze.x")
    async def _ga() -> None: ...

    @system_tool("system.x")
    async def _s() -> None: ...

    assert get_category(_r) == Category.READ
    assert get_category(_w) == Category.WRITE
    assert get_category(_gl) == Category.GROUNDING_LOOKUP
    assert get_category(_ga) == Category.GROUNDING_ANALYZE
    assert get_category(_s) == Category.SYSTEM
