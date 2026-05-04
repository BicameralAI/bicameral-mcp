"""Corpus learner — extracts recurring n-grams from team_event payloads
whose extraction.decisions is non-empty (per OQ-1 resolution: read from
team-server's own ledger, not the per-repo decision table). Output
populates learned_heuristic_terms for the heuristic classifier to merge.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from ledger.client import LedgerClient

logger = logging.getLogger(__name__)

NGRAM_MIN, NGRAM_MAX = 2, 4


async def learn_corpus_terms(
    client: LedgerClient,
    *,
    source_type: str = "slack",
    top_n: int = 50,
    denylist: Optional[list[str]] = None,
) -> list[dict]:
    """Read team_event rows whose payload yielded decisions, extract
    top n-grams from the source content. Returns list of {term, support_count}."""
    rows = await client.query(
        "SELECT payload FROM team_event WHERE event_type = 'ingest'"
    )
    counter: Counter = Counter()
    for row in rows or []:
        payload = row.get("payload") or {}
        if (payload.get("source_type") or "").split("_")[0] != source_type.split("_")[0]:
            continue
        extraction = payload.get("extraction") or {}
        decisions = extraction.get("decisions") or []
        if not decisions:
            continue
        for d in decisions:
            text = (d.get("summary", "") + " " + d.get("context_snippet", "")).lower()
            words = text.split()
            for n in range(NGRAM_MIN, NGRAM_MAX + 1):
                for i in range(len(words) - n + 1):
                    counter[" ".join(words[i:i + n])] += 1
    deny = {d.lower() for d in (denylist or [])}
    out: list[dict] = []
    for term, support in counter.most_common(top_n * 4):
        if term in deny or any(d in term for d in deny):
            continue
        out.append({"term": term, "support_count": support})
        if len(out) >= top_n:
            break
    return out


async def persist_learned_terms(
    client: LedgerClient, source_type: str, terms: list[dict],
) -> None:
    """UPSERT-shaped: existing rows for (source_type, term) get their
    support_count and learned_at updated; new terms inserted."""
    for entry in terms:
        existing = await client.query(
            "SELECT id FROM learned_heuristic_terms "
            "WHERE source_type = $st AND term = $t LIMIT 1",
            {"st": source_type, "t": entry["term"]},
        )
        if existing:
            await client.query(
                "UPDATE learned_heuristic_terms "
                "SET support_count = $sc, learned_at = time::now() "
                "WHERE source_type = $st AND term = $t",
                {"st": source_type, "t": entry["term"],
                 "sc": entry["support_count"]},
            )
        else:
            await client.query(
                "CREATE learned_heuristic_terms CONTENT { "
                "source_type: $st, term: $t, support_count: $sc }",
                {"st": source_type, "t": entry["term"],
                 "sc": entry["support_count"]},
            )


async def load_learned_terms(
    client: LedgerClient, source_type: str,
) -> tuple[str, ...]:
    rows = await client.query(
        "SELECT term FROM learned_heuristic_terms "
        "WHERE source_type = $st ORDER BY support_count DESC",
        {"st": source_type},
    )
    return tuple(r["term"] for r in rows or [])


async def run_corpus_learner_iteration(
    client: LedgerClient, config, *, source_type: str = "slack",
) -> None:
    """Single learner iteration. Pulls denylist from the matching
    heuristic-global rules; persists results."""
    deny: list[str] = []
    if source_type == "slack":
        deny = config.slack.heuristics.global_rules.learned_denylist
    elif source_type == "notion":
        deny = config.notion.heuristics.global_rules.learned_denylist
    terms = await learn_corpus_terms(
        client, source_type=source_type,
        top_n=config.corpus_learner.top_n, denylist=deny,
    )
    await persist_learned_terms(client, source_type, terms)
    logger.info(
        "[corpus-learner] source=%s persisted %d terms", source_type, len(terms),
    )
