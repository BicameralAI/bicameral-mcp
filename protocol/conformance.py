"""Conformance harness skeleton.

Phase 3 publishes the full test suite as part of ``bicameral-protocol``;
Phase 1 ships only the entry surface so adapters can declare conformance
without depending on the eventual import path. The fleshed-out suite ships
in the per-adapter implementation plans (Linear, Notion, Slack, …).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConformanceReport:
    passed: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


async def run_suite(_adapter: Any) -> ConformanceReport:
    """Placeholder until Phase 3 lands the published conformance suite.

    Returns an empty report so callers wiring up CI today get a no-op
    baseline. Phase 3 replaces the body; the signature is the public
    contract.
    """
    return ConformanceReport()
