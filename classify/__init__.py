"""Decision-level classifier (#77).

Pure-function heuristic classifier mapping decision descriptions/sources to
L1 / L2 / L3 levels. No IO, no LLM, no network — deterministic.
"""

from classify.heuristic import classify

__all__ = ["classify"]
