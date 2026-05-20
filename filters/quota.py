"""Per-source quota helpers (#337 foundations cycle 4).

Two operator-tunable knobs per source-config entry:

- ``max_items_per_pull`` — caps the number of ingest payloads the
  adapter produces in a single ``pull()`` call. Items beyond the cap
  are left for the next pull; watermark stops at the last processed
  item so we don't skip them.
- ``max_payload_bytes`` — per-payload size cap that overrides the
  global ``BicameralContext.ingest_max_bytes`` for this source. Used
  by source-level config to express "Slack messages should be bounded
  tighter than GitHub PR bodies."

Both default to 0 (no cap). Adapters read via :func:`get_max_items`
/ :func:`get_max_bytes` so the parsing + validation logic stays in
one place.

This module is intentionally NOT pydantic-modelled — the values are
simple ints with a "0 disables" sentinel, and there's no per-resource
override semantic to merge. A more elaborate schema can be added
later if the surface grows.
"""

from __future__ import annotations

import sys


def get_max_items(config: dict) -> int:
    """Return ``max_items_per_pull`` from ``config``, or 0 if unset/invalid.

    Negative / non-int / malformed values log to stderr and fall through
    to 0 (no cap). The polling adapter doesn't fail on config errors.
    """
    raw = config.get("max_items_per_pull")
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        print(
            f"[quota] max_items_per_pull must be int (got {type(raw).__name__}); "
            "ignored (no cap applied).",
            file=sys.stderr,
        )
        return 0
    if value < 0:
        print(
            f"[quota] max_items_per_pull must be >= 0 (got {value}); ignored (no cap applied).",
            file=sys.stderr,
        )
        return 0
    return value


def get_max_bytes(config: dict) -> int:
    """Return ``max_payload_bytes`` from ``config``, or 0 if unset/invalid.

    Same validation discipline as :func:`get_max_items`. 0 means "use
    the global ``ingest_max_bytes`` from BicameralContext."
    """
    raw = config.get("max_payload_bytes")
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        print(
            f"[quota] max_payload_bytes must be int (got {type(raw).__name__}); "
            "ignored (using global cap).",
            file=sys.stderr,
        )
        return 0
    if value < 0:
        print(
            f"[quota] max_payload_bytes must be >= 0 (got {value}); ignored (using global cap).",
            file=sys.stderr,
        )
        return 0
    return value


def payload_within_cap(payload: dict, max_bytes: int) -> bool:
    """Return True if ``payload``'s serialized size is within ``max_bytes``.

    ``max_bytes == 0`` short-circuits to True (no cap applied here;
    global cap in ``handle_ingest`` still gates).
    """
    if max_bytes <= 0:
        return True
    import json

    try:
        size = len(json.dumps(payload, default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001 — malformed payload caught by handle_ingest's gate later
        return True
    return size <= max_bytes
