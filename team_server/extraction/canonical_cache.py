"""Canonical-extraction cache (upsert-shaped, two-axis identity).

For a given (source_type, source_ref), holds the latest canonical
extraction. Cache identity is the tuple (content_hash, classifier_version):
both must match for a cache hit. Either differing triggers re-extraction
and replaces the row in place. team_event log preserves edit history.

classifier_version captures the rule-set hash of the heuristic Stage 1
that gated the LLM call; rules change ⇒ classifier_version changes ⇒
all rows look stale ⇒ next poll re-runs the pipeline. This is the
mechanism that makes operator config edits and corpus-learner updates
take effect without manual cache invalidation.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ledger.client import LedgerClient

ComputeFn = Callable[[], Awaitable[dict]]


async def upsert_canonical_extraction(
    client: LedgerClient,
    *,
    source_type: str,
    source_ref: str,
    content_hash: str,
    classifier_version: str,
    compute_fn: ComputeFn,
    model_version: str,
) -> tuple[dict, bool]:
    """Upsert canonical extraction. Returns (extraction, changed).

    changed=True when the row was created OR either content_hash OR
    classifier_version differs from the stored values. changed=False
    only on cache hit where BOTH match.
    """
    rows = await client.query(
        "SELECT content_hash, classifier_version, canonical_extraction "
        "FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr LIMIT 1",
        {"st": source_type, "sr": source_ref},
    )
    if (rows
            and rows[0]["content_hash"] == content_hash
            and rows[0]["classifier_version"] == classifier_version):
        return rows[0]["canonical_extraction"], False
    extraction = await compute_fn()
    if rows:
        await client.query(
            "UPDATE extraction_cache SET content_hash = $ch, "
            "classifier_version = $cv, canonical_extraction = $ext, "
            "model_version = $mv "
            "WHERE source_type = $st AND source_ref = $sr",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "cv": classifier_version, "ext": extraction, "mv": model_version},
        )
    else:
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
            "content_hash: $ch, classifier_version: $cv, "
            "canonical_extraction: $ext, model_version: $mv }",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "cv": classifier_version, "ext": extraction, "mv": model_version},
        )
    return extraction, True
