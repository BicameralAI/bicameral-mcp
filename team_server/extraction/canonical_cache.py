"""Canonical-extraction cache (upsert-shaped).

For a given (source_type, source_ref), holds the latest canonical
extraction. content_hash tracks the input that produced it; an inbound
content_hash that matches the stored value is a no-op (returns
changed=False). A different hash triggers re-extraction and replaces
the row in place. team_event log preserves edit history.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ledger.client import LedgerClient

ComputeFn = Callable[[], Awaitable[dict]]


async def upsert_canonical_extraction(
    client: LedgerClient,
    source_type: str,
    source_ref: str,
    content_hash: str,
    compute_fn: ComputeFn,
    model_version: str,
) -> tuple[dict, bool]:
    """Upsert canonical extraction. Returns (extraction, changed).

    changed=True when the row was created OR the content_hash differed
    from the stored value (i.e. an event-worthy change). changed=False
    on cache hit with identical content_hash (idempotent re-poll).
    """
    rows = await client.query(
        "SELECT content_hash, canonical_extraction FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr LIMIT 1",
        {"st": source_type, "sr": source_ref},
    )
    if rows and rows[0]["content_hash"] == content_hash:
        return rows[0]["canonical_extraction"], False
    extraction = await compute_fn()
    if rows:
        await client.query(
            "UPDATE extraction_cache SET content_hash = $ch, "
            "canonical_extraction = $ext, model_version = $mv "
            "WHERE source_type = $st AND source_ref = $sr",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "ext": extraction, "mv": model_version},
        )
    else:
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
            "content_hash: $ch, canonical_extraction: $ext, model_version: $mv }",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "ext": extraction, "mv": model_version},
        )
    return extraction, True
