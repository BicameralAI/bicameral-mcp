"""Real-ledger seeder for cost/latency baselines (issue #88, path 3).

Translates a synthetic ``HistoryResponse``-shaped dict (from
``_synthetic_ledger.generate_ledger``) into real SurrealDB writes via
``adapter.ingest_payload`` — the production ingestion path. This makes
the seeded ledger reflect what a real ledger looks like to the preflight
handler: same node types, same edges, same query patterns.

Uses the synthetic-repo fallback added in v0.10.7+ — when ``repo`` doesn't
resolve to a directory on disk, content_hash is left empty and decisions
are created as ungrounded. We then update statuses directly to match the
synthetic generator's distribution (70% reflected, 20% drifted, 5% each
pending/ungrounded), bypassing ``derive_status`` since there's no real
file content to hash.

Seeding cost: ~20ms per decision via ``ingest_payload``. At N=1000 (3000
decisions in 3 decisions-per-feature) the seed phase takes ~60s. Acceptable
for advisory CI; cached at the test level (one seed per N per session).
"""

from __future__ import annotations

from typing import Any


def _build_mapping(feature_id: str, synthetic_decision: dict) -> dict:
    """Translate one synthetic decision dict into an ``ingest_payload`` mapping."""
    description = synthetic_decision["summary"]
    fulfillments = synthetic_decision.get("fulfillments") or []

    code_regions: list[dict] = []
    if fulfillments:
        f = fulfillments[0]
        code_regions = [
            {
                "file_path": f["file_path"],
                "symbol": f.get("symbol") or f["file_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0],
                "type": "function",
                "start_line": f.get("start_line", 1),
                "end_line": f.get("end_line", 50),
                "purpose": description[:120],
            }
        ]

    return {
        "span": {
            "text": description,
            "source_type": "transcript",
            "source_ref": f"synthetic-{feature_id}",
            "speakers": [],
            "meeting_date": "",
        },
        "intent": description,
        "feature_group": feature_id,
        "code_regions": code_regions,
    }


async def seed_ledger_from_synthetic(adapter: Any, synthetic: dict) -> int:
    """Seed ``adapter`` with all decisions from a synthetic ledger dict.

    Returns the number of decisions created. The adapter must be connected
    (caller's responsibility). After this call, the ledger contains
    ``len(synthetic.features) * decisions_per_feature`` decisions plus
    associated input_span / code_region nodes and yields/binds_to edges.

    Status overrides: ``ingest_payload`` defaults decisions to ``ungrounded``
    (no code_regions) or ``pending`` (with code_regions, but content_hash
    empty because synthetic-repo fallback). We then call
    ``update_decision_status`` per decision to match the synthetic
    generator's intended status (reflected / drifted / pending /
    ungrounded), so the seeded ledger's status distribution matches what
    the generator produced.
    """
    # Build all mappings + parallel list of (description, target_status) for status post-fix.
    mappings: list[dict] = []
    desired_statuses: list[tuple[str, str]] = []

    for feature in synthetic["features"]:
        feature_id = feature["id"]
        for decision in feature["decisions"]:
            mappings.append(_build_mapping(feature_id, decision))
            desired_statuses.append((decision["summary"], decision["status"]))

    if not mappings:
        return 0

    payload = {
        "query": "synthetic baseline seed",
        "repo": "synthetic-baseline-test-repo",  # not on disk → synthetic fallback
        "commit_hash": "synthetic-baseline",
        "analyzed_at": "2026-04-29T00:00:00Z",
        "mappings": mappings,
    }
    response = await adapter.ingest_payload(payload)

    # ingest_payload returns a dict (when called on the inner adapter directly)
    # or an IngestResponse model — handle both shapes.
    created = (
        response.get("created_decisions")
        if isinstance(response, dict)
        else getattr(response, "created_decisions", [])
    )
    if not created:
        return 0

    # Match created decisions back to synthetic intended statuses by description
    # (description is unique per mapping in the synthetic generator).
    desc_to_status = {desc: status for desc, status in desired_statuses}

    from ledger.queries import update_decision_status

    inner = getattr(adapter, "_inner", adapter)
    client = inner._client

    for created_decision in created:
        if isinstance(created_decision, dict):
            decision_id = created_decision.get("decision_id") or created_decision.get("id", "")
            description = created_decision.get("description", "")
        else:
            decision_id = getattr(created_decision, "decision_id", "")
            description = getattr(created_decision, "description", "")

        target_status = desc_to_status.get(description)
        if not decision_id or not target_status:
            continue

        # Always update — explicit override even when current status happens to match.
        await update_decision_status(client, str(decision_id), target_status)

    return len(created)
