"""Phase 2c-2c invariant: every wire method name on ``ProtocolClient`` and
``daemon.runtime.Runtime`` lives under a categorized prefix.

The categorized prefixes — ``read.``, ``write.``, ``grounding.lookup.``,
``grounding.analyze.``, ``system.`` — were locked in Phase 2c-1
(``protocol/categorization.py``). This test prevents the regression where
new methods get added with un-categorized names like ``ingest.foo`` or
``grounding.bar``.

``egress.deliver`` is on the explicit allowlist because the categorization
surface has no ``egress.*`` slot yet; expanding it is its own design
decision (see PR #503's follow-up discussion). Adding entries to the
allowlist should require explaining why the new prefix shouldn't be a
category — keep the list short.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from protocol import client as client_module
from protocol.categorization import (
    EGRESS_PREFIX,
    GROUNDING_ANALYZE_PREFIX,
    GROUNDING_LOOKUP_PREFIX,
    READ_PREFIX,
    SYSTEM_PREFIX,
    WRITE_PREFIX,
)

# Method names that intentionally live outside the six categorized prefixes.
# Each entry needs a one-line rationale; keep this list small.
WIRE_METHOD_ALLOWLIST: frozenset[str] = frozenset()


_CATEGORIZED_PREFIXES = (
    READ_PREFIX,
    WRITE_PREFIX,
    GROUNDING_LOOKUP_PREFIX,
    GROUNDING_ANALYZE_PREFIX,
    SYSTEM_PREFIX,
    EGRESS_PREFIX,
)


def _extract_method_name_strings(source_path: Path) -> set[str]:
    """Return every string literal passed as the first positional argument to
    a ``self._call(...)`` or ``self._server.register(...)`` call in ``source_path``.

    AST-based so we don't accidentally match docstrings, comments, or string
    literals used for other purposes (event_type strings, log messages, etc.).
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    found: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match `<receiver>._call(name, ...)` and `<receiver>.register(name, ...)`.
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"_call", "register"}:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def _is_categorized_or_allowlisted(method: str) -> bool:
    if method in WIRE_METHOD_ALLOWLIST:
        return True
    return any(method.startswith(p) for p in _CATEGORIZED_PREFIXES)


def test_protocol_client_facade_uses_categorized_names() -> None:
    """Every method-name string in ``protocol/client.py`` matches the
    categorized prefixes (or is on the explicit allowlist)."""
    client_path = Path(inspect.getfile(client_module))
    methods = _extract_method_name_strings(client_path)

    # Smoke: the facade has at least the read + write surfaces wired.
    assert "write.ingest" in methods, "write.ingest facade missing"
    assert any(m.startswith(GROUNDING_LOOKUP_PREFIX) for m in methods), (
        "no grounding.lookup.* methods on facade"
    )

    uncategorized = sorted(m for m in methods if not _is_categorized_or_allowlisted(m))
    assert not uncategorized, (
        "ProtocolClient method-name strings not matching a categorized prefix "
        "(or the explicit allowlist):\n  " + "\n  ".join(uncategorized)
    )


def test_daemon_runtime_registrations_use_categorized_names() -> None:
    """Every server-side ``register(...)`` call in ``daemon/runtime.py``
    matches the categorized prefixes (or allowlist)."""
    import daemon.runtime as runtime_module

    runtime_path = Path(inspect.getfile(runtime_module))
    methods = _extract_method_name_strings(runtime_path)

    assert "write.ingest" in methods or "write.link_commit" in methods, (
        "daemon runtime no longer registers write.* — check the renames"
    )

    uncategorized = sorted(m for m in methods if not _is_categorized_or_allowlisted(m))
    assert not uncategorized, (
        "daemon.runtime registers method names not matching a categorized prefix "
        "(or the explicit allowlist):\n  " + "\n  ".join(uncategorized)
    )


def test_allowlist_entries_are_actually_used() -> None:
    """Catch dead allowlist entries — every name on the allowlist must
    actually appear somewhere in the codebase. A removed/renamed method
    that left a stale allowlist entry is exactly the rot we want to prevent.
    """
    import daemon.runtime as runtime_module

    client_methods = _extract_method_name_strings(Path(inspect.getfile(client_module)))
    runtime_methods = _extract_method_name_strings(Path(inspect.getfile(runtime_module)))
    all_used = client_methods | runtime_methods

    stale = sorted(name for name in WIRE_METHOD_ALLOWLIST if name not in all_used)
    assert not stale, (
        "Stale WIRE_METHOD_ALLOWLIST entries (no longer referenced anywhere "
        "in protocol/client.py or daemon/runtime.py):\n  " + "\n  ".join(stale)
    )
