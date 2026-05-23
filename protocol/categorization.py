"""Read/write categorization decorators for the universal protocol.

Phase 2c-1 (#daemon-extraction parent plan §Phase 2c-1). Every externally
callable handler in ``handlers/`` is tagged with exactly one decorator from
this module. The decorator attaches two attributes on the wrapped function:

- ``__bicameral_protocol_method__`` — the wire method name (e.g. ``"write.ingest"``)
- ``__bicameral_protocol_category__`` — one of ``Category`` enum values

The decorators are pure metadata: they do not change call behavior at
runtime. Phase 2c-2 reads this metadata to register handlers against the
daemon's JSON-RPC dispatcher and to route reads vs writes onto separate
connection pools.

Six categories partition the surface:

============================  ================================================
Prefix                        Intent
============================  ================================================
``read.``                     Ledger reads (no state mutation, no I/O)
``write.``                    Ledger writes (decisions, sources, bindings)
``grounding.lookup.``         Deterministic code-locator primitives
``grounding.analyze.``        Drift / region analysis (L1-L3)
``system.``                   Daemon lifecycle + meta (attach, reset, …)
``egress.``                   Outbound delivery to humans (Slack/email/…)
============================  ================================================

``egress.*`` is structurally distinct from ``write.*`` — it pushes
NotificationEvents to channel adapters, not rows to the ledger. Conflating
them at the wire level would force adapter authors to reason about which
``write.X`` mutates state vs which one fans out a notification. Added in
Phase 2c-2d after the ``egress.deliver`` allowlist entry from 2c-2c.

The mismatch between a decorator and its declared method name (e.g.
``@read_tool("write.foo")``) raises ``ProtocolMethodNameError`` at decoration
time — invariants are enforced at import, not at first call.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])

READ_PREFIX = "read."
WRITE_PREFIX = "write."
GROUNDING_LOOKUP_PREFIX = "grounding.lookup."
GROUNDING_ANALYZE_PREFIX = "grounding.analyze."
SYSTEM_PREFIX = "system."
EGRESS_PREFIX = "egress."


class Category(StrEnum):
    READ = "read"
    WRITE = "write"
    GROUNDING_LOOKUP = "grounding.lookup"
    GROUNDING_ANALYZE = "grounding.analyze"
    SYSTEM = "system"
    EGRESS = "egress"


_PREFIX_BY_CATEGORY: dict[Category, str] = {
    Category.READ: READ_PREFIX,
    Category.WRITE: WRITE_PREFIX,
    Category.GROUNDING_LOOKUP: GROUNDING_LOOKUP_PREFIX,
    Category.GROUNDING_ANALYZE: GROUNDING_ANALYZE_PREFIX,
    Category.SYSTEM: SYSTEM_PREFIX,
    Category.EGRESS: EGRESS_PREFIX,
}


METHOD_ATTR = "__bicameral_protocol_method__"
CATEGORY_ATTR = "__bicameral_protocol_category__"


class ProtocolMethodNameError(ValueError):
    """Raised when a decorator's category doesn't match the method-name prefix."""


def _tag(category: Category, method: str) -> Callable[[F], F]:
    prefix = _PREFIX_BY_CATEGORY[category]
    if not method.startswith(prefix):
        raise ProtocolMethodNameError(
            f"@{category.name.lower()}_tool expects a '{prefix}*' method name, got '{method}'"
        )

    def _decorator(fn: F) -> F:
        existing = getattr(fn, METHOD_ATTR, None)
        if existing is not None:
            raise ProtocolMethodNameError(
                f"{fn.__qualname__} is already tagged as '{existing}'; "
                f"each handler must have exactly one categorization decorator"
            )
        setattr(fn, METHOD_ATTR, method)
        setattr(fn, CATEGORY_ATTR, category)
        return fn

    return _decorator


def read_tool(method: str) -> Callable[[F], F]:
    return _tag(Category.READ, method)


def write_tool(method: str) -> Callable[[F], F]:
    return _tag(Category.WRITE, method)


def grounding_lookup(method: str) -> Callable[[F], F]:
    return _tag(Category.GROUNDING_LOOKUP, method)


def grounding_analyze(method: str) -> Callable[[F], F]:
    return _tag(Category.GROUNDING_ANALYZE, method)


def system_tool(method: str) -> Callable[[F], F]:
    return _tag(Category.SYSTEM, method)


def egress_tool(method: str) -> Callable[[F], F]:
    return _tag(Category.EGRESS, method)


def is_categorized(fn: object) -> bool:
    return hasattr(fn, METHOD_ATTR) and hasattr(fn, CATEGORY_ATTR)


def get_method(fn: object) -> str | None:
    value = getattr(fn, METHOD_ATTR, None)
    return value if isinstance(value, str) else None


def get_category(fn: object) -> Category | None:
    value = getattr(fn, CATEGORY_ATTR, None)
    return value if isinstance(value, Category) else None


__all__ = [
    "CATEGORY_ATTR",
    "EGRESS_PREFIX",
    "GROUNDING_ANALYZE_PREFIX",
    "GROUNDING_LOOKUP_PREFIX",
    "METHOD_ATTR",
    "READ_PREFIX",
    "SYSTEM_PREFIX",
    "WRITE_PREFIX",
    "Category",
    "ProtocolMethodNameError",
    "egress_tool",
    "get_category",
    "get_method",
    "grounding_analyze",
    "grounding_lookup",
    "is_categorized",
    "read_tool",
    "system_tool",
    "write_tool",
]
