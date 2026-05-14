"""Optional dogfood label propagation for event payloads (#278 Phase 4).

When ``BICAMERAL_DOGFOOD_LABEL`` is set to a non-empty value at MCP server
start, every event emitted by the Phase 1–3 surfaces
(``decision_removed.completed``, ``source_removed.completed``,
``admin_query.executed``) carries an extra ``dogfood_label`` field with
that value. Operators slice the event log by label to count whether the
design-partner success criteria from #278 Phase 4 were met:

  - "A PM finds a wrong decision and removes it via the dashboard,
     without escalating to the operator."
  - "An operator runs a SurrealQL query to investigate a stale ledger
     entry without leaving the dashboard."

The label is purely additive and opt-in. The env unset / empty produces
no field, preserving the pre-Phase-4 payload shape exactly.
"""

from __future__ import annotations

import os
from typing import Any


def maybe_dogfood_label(payload: dict[str, Any]) -> dict[str, Any]:
    """Add ``dogfood_label`` to ``payload`` iff
    ``BICAMERAL_DOGFOOD_LABEL`` is set to a non-empty value.

    Mutates and returns the same dict. Empty-string env values are
    treated as unset (noise, not signal — the env var is opt-in).
    """
    label = (os.environ.get("BICAMERAL_DOGFOOD_LABEL") or "").strip()
    if label:
        payload["dogfood_label"] = label
    return payload
