"""Canonical-extraction cache.

For a given (source_type, source_ref, content_hash) tuple, returns the
extraction result deterministically: cache hit returns persisted output,
cache miss invokes compute_fn and persists. Multi-dev convergence: any
peer hitting the same triple sees the same canonical extraction.

Per audit Advisory #3 + #72 lesson: the underlying field is FLEXIBLE
TYPE object (declared in `team_server/schema.py`) so nested decision
dicts persist intact.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ledger.client import LedgerClient

ComputeFn = Callable[[], Awaitable[dict]]


async def get_or_compute(
    client: LedgerClient,
    source_type: str,
    source_ref: str,
    content_hash: str,
    compute_fn: ComputeFn,
    model_version: str,
) -> dict:
    cached = await client.query(
        "SELECT canonical_extraction FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr "
        "AND content_hash = $ch LIMIT 1",
        {"st": source_type, "sr": source_ref, "ch": content_hash},
    )
    if cached:
        return cached[0]["canonical_extraction"]
    extraction = await compute_fn()
    await client.query(
        "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
        "content_hash: $ch, canonical_extraction: $ext, model_version: $mv }",
        {"st": source_type, "sr": source_ref, "ch": content_hash,
         "ext": extraction, "mv": model_version},
    )
    return extraction
