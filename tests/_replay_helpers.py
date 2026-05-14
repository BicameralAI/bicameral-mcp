"""Test-only helpers for #296 replay-determinism regression suite.

Two pieces:

  * ``fingerprint_ledger(client)`` — content-addressable digest of the
    ledger's logical state. Excludes auto-gen record ids and timestamps
    so that two ledgers built by independent replays of the same event
    sequence produce the same fingerprint.

  * ``build_event_log(events, author_email)`` — serializes a list of
    event dicts to JSONL bytes matching the wire format
    ``EventMaterializer.replay_new_events`` expects.

Together these let a determinism test arrange-act-assert:

    events = [_ingest_event(...)]
    adapter_a, client_a = await _fresh_adapter("a")
    adapter_b, client_b = await _fresh_adapter("b")
    await replay_substrate(adapter_a, {"alice@x": events})
    await replay_substrate(adapter_b, {"alice@x": events})
    assert await fingerprint_ledger(client_a) == await fingerprint_ledger(client_b)

The fingerprint is content-only: same fingerprint means same logical
ledger state, not necessarily byte-for-byte SurrealKV state. Future
cycles can layer a stricter on-disk diff if real corruption surfaces
past content equivalence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from events.materializer import EventMaterializer
from events.writer import EventEnvelope

# Tables whose row content participates in fingerprint equivalence.
# Edge tables included because their (in, out) pairs are the structural truth.
LEDGER_TABLES_TO_FINGERPRINT: list[str] = [
    # Node tables
    "decision",
    "code_region",
    "input_span",
    "compliance_check",
    # Edge tables
    "yields",
    "binds_to",
    "supersedes",
    "locates",
    "context_for",
    "has_identity",
    "has_version",
    "depends_on",
    "about",
]


# Fields stripped from row dicts before hashing. These are wall-clock or
# storage-engine-assigned values that vary across replays even when the
# logical state is identical.
EXCLUDED_FIELDS: set[str] = {
    "id",
    "created_at",
    "updated_at",
    "ratified_at",
    "rejected_at",
    "superseded_at",
    "removed_at",
    "ingested_at",
    # session_id is per-run; not logical state
    "session_id",
    # source_commit_ref carries the runtime commit SHA; not logical
    "source_commit_ref",
}


async def fingerprint_ledger(client) -> str:
    """Compute a SHA-256 digest of the ledger's logical content.

    For each table in ``LEDGER_TABLES_TO_FINGERPRINT``:
      * ``SELECT *`` all rows;
      * for edge tables (rows carrying ``in`` + ``out``), resolve each
        endpoint to a content-addressable key (canonical_id for decisions,
        (repo, file_path, symbol_name, content_hash) for code_regions,
        (source_type, source_ref) for input_spans) so two ledgers whose
        per-DB record IDs differ but whose logical edges match produce
        the same fingerprint;
      * strip fields listed in ``EXCLUDED_FIELDS`` from each row;
      * sort rows by a stable per-row key;
      * serialize with ``json.dumps(..., sort_keys=True, separators=(',', ':'))``;
      * concatenate per-table digests with the table name as separator.

    Returns the hex digest of the final SHA-256.
    """
    # Build the record-id → content-key resolver cache once per fingerprint
    # so we don't re-query the same target row repeatedly for high-fan-in edges.
    resolver_cache: dict[str, str] = {}
    hasher = hashlib.sha256()
    for table in LEDGER_TABLES_TO_FINGERPRINT:
        try:
            rows = await client.query(f"SELECT * FROM {table}")
        except Exception:
            # Table may not exist in some schemas / migrations.
            rows = []
        normalized: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                normalized.append({"_value": str(row)})
                continue
            row_dict = dict(row)
            # Resolve edge endpoints to content keys.
            if "in" in row_dict and "out" in row_dict:
                row_dict["in"] = await _content_key(client, row_dict["in"], resolver_cache)
                row_dict["out"] = await _content_key(client, row_dict["out"], resolver_cache)
            normalized.append(_strip_row(row_dict))
        normalized.sort(key=_row_sort_key)
        table_bytes = (
            table.encode("utf-8")
            + b"|"
            + json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
            + b"\n"
        )
        hasher.update(table_bytes)
    return hasher.hexdigest()


def _strip_row(row: Any) -> dict:
    """Strip non-deterministic fields from a row, including nested ones
    in the signoff dict where per-DB record IDs leak in (e.g.
    ``signoff.superseded_by`` is a ``decision:<local-id>`` reference)."""
    if not isinstance(row, dict):
        return {"_value": str(row)}
    out: dict = {}
    for k, v in row.items():
        if k in EXCLUDED_FIELDS:
            continue
        if k == "signoff" and isinstance(v, dict):
            out[k] = _strip_signoff(v)
        else:
            out[k] = v
    return out


def _strip_signoff(signoff: dict) -> dict:
    """Strip per-DB record-id references from a nested signoff dict.

    ``superseded_by`` carries the new decision's local record id, which
    differs across DBs even when the logical state matches. The
    ``supersedes`` edge already carries the structural truth (resolved
    via canonical_id), so dropping this field from the signoff fingerprint
    does not lose information.
    """
    return {
        k: v
        for k, v in signoff.items()
        if k not in {"superseded_by", "session_id", "source_commit_ref"}
        and k not in EXCLUDED_FIELDS
    }


def _row_sort_key(row: dict) -> str:
    """Stable per-row sort key. canonical_id for decisions; (in, out) for
    edges (already content-resolved); otherwise the full row's JSON repr."""
    if "canonical_id" in row:
        return f"c:{row['canonical_id']}"
    if "in" in row and "out" in row:
        return f"e:{row['in']}>{row['out']}"
    return f"j:{json.dumps(row, sort_keys=True, default=str)}"


async def _content_key(client, record_id: Any, cache: dict[str, str]) -> str:
    """Resolve a SurrealDB record id (e.g. ``decision:abc``) to a
    content-addressable key (e.g. ``decision:<canonical_id>``) so two
    ledgers with different per-DB record IDs but identical logical state
    fingerprint identically.

    Per-edge-table strategy:
      * decision → canonical_id (deterministic UUIDv5 across DBs)
      * code_region → (repo, file_path, symbol_name, content_hash)
      * input_span → (source_type, source_ref) — input_span has no
        cross-DB canonical id today, but (source_type, source_ref) is
        the closest content key.
      * other tables → return the raw record_id (best effort).
    """
    key = str(record_id)
    if key in cache:
        return cache[key]
    table = key.split(":", 1)[0] if ":" in key else "unknown"
    resolved: str
    try:
        if table == "decision":
            rows = await client.query(f"SELECT canonical_id FROM {key} LIMIT 1")
            resolved = (
                f"decision:{rows[0]['canonical_id']}"
                if rows and rows[0].get("canonical_id")
                else f"decision:?{key}"
            )
        elif table == "code_region":
            rows = await client.query(
                f"SELECT repo, file_path, symbol_name, content_hash FROM {key} LIMIT 1"
            )
            if rows:
                r = rows[0]
                resolved = (
                    f"code_region:{r.get('repo', '')}|{r.get('file_path', '')}|"
                    f"{r.get('symbol_name', '')}|{r.get('content_hash', '')}"
                )
            else:
                resolved = f"code_region:?{key}"
        elif table == "input_span":
            rows = await client.query(f"SELECT source_type, source_ref FROM {key} LIMIT 1")
            if rows:
                r = rows[0]
                resolved = f"input_span:{r.get('source_type', '')}|{r.get('source_ref', '')}"
            else:
                resolved = f"input_span:?{key}"
        else:
            resolved = key
    except Exception:
        resolved = f"?{key}"
    cache[key] = resolved
    return resolved


def build_event_log(events: list[dict], author_email: str) -> bytes:
    """Serialize a list of event dicts to JSONL bytes.

    Each input dict must carry at least ``event_type`` and ``payload``
    keys. Output is one JSON line per event, matching the format
    ``EventFileWriter.write`` produces and ``EventMaterializer.replay_new_events``
    consumes.
    """
    out = bytearray()
    for ev in events:
        env = EventEnvelope(
            event_type=str(ev.get("event_type", "")),
            author=author_email,
            payload=dict(ev.get("payload", {})),
        )
        line = json.dumps(env.model_dump(), separators=(",", ":"), default=str) + "\n"
        out.extend(line.encode("utf-8"))
    return bytes(out)


async def replay_substrate(
    adapter,
    author_to_events: dict[str, list[dict]],
    *,
    events_dir: Path,
    local_dir: Path,
) -> int:
    """Write per-author JSONL files into ``events_dir`` and replay them
    into ``adapter``.

    Returns the number of events the materializer replayed.
    """
    events_dir.mkdir(parents=True, exist_ok=True)
    local_dir.mkdir(parents=True, exist_ok=True)
    for author, events in author_to_events.items():
        path = events_dir / f"{author}.jsonl"
        with open(path, "ab") as f:
            f.write(build_event_log(events, author))
    materializer = EventMaterializer(events_dir, local_dir)
    return await materializer.replay_new_events(adapter)


# ── canonical event builders ───────────────────────────────────────────────


def ingest_event(
    *,
    intent: str,
    source_ref: str,
    speaker: str = "Tester",
    commit_hash: str = "deadbeef00000000000000000000000000000000",
) -> dict:
    """Construct an ``ingest.completed`` event with a single-decision payload.

    The materializer dispatches ``ingest.completed`` to
    ``inner_adapter.ingest_payload(payload)``, so the payload shape must
    match what ``handle_ingest``'s code path consumes.
    """
    return {
        "event_type": "ingest.completed",
        "payload": {
            "query": intent,
            "repo": "test-repo",
            "commit_hash": commit_hash,
            "analyzed_at": "2026-05-14T00:00:00Z",
            "mappings": [
                {
                    "span": {
                        "span_id": f"span-{source_ref}",
                        "source_type": "transcript",
                        "text": intent,
                        "speaker": speaker,
                        "source_ref": source_ref,
                    },
                    "intent": intent,
                    "symbols": [],
                    "code_regions": [],
                    "dependency_edges": [],
                }
            ],
        },
    }


def link_commit_event(commit_hash: str, repo_path: str = "test-repo") -> dict:
    return {
        "event_type": "link_commit.completed",
        "payload": {"commit_hash": commit_hash, "repo_path": repo_path},
    }


def decision_ratified_event(canonical_id: str, signer: str = "tester") -> dict:
    return {
        "event_type": "decision_ratified.completed",
        "payload": {
            "canonical_id": canonical_id,
            "decision_id": "decision:placeholder",  # ignored by materializer; resolved via canonical
            "signoff": {
                "state": "ratified",
                "signer": signer,
                "ratified_at": "2026-05-14T01:00:00Z",
            },
        },
    }


def decision_superseded_event(
    new_canonical_id: str,
    old_canonical_id: str,
    signer: str = "tester",
) -> dict:
    return {
        "event_type": "decision_superseded.completed",
        "payload": {
            "new_canonical_id": new_canonical_id,
            "old_canonical_id": old_canonical_id,
            "signer": signer,
            "signoff_note": "test supersede",
            "superseded_at": "2026-05-14T02:00:00Z",
        },
    }


def compliance_check_event(
    canonical_decision_id: str,
    *,
    region_repo: str = "test-repo",
    region_file: str = "module.py",
    region_symbol: str = "fn",
    region_content_hash: str = "0" * 64,
    verdict: str = "compliant",
) -> dict:
    return {
        "event_type": "compliance_check.completed",
        "payload": {
            "canonical_decision_id": canonical_decision_id,
            "region": {
                "repo": region_repo,
                "file_path": region_file,
                "symbol_name": region_symbol,
                "content_hash": region_content_hash,
            },
            "verdict": verdict,
            "pinned_commit": "cafef00d" + "0" * 32,
            "evidence": "test evidence",
        },
    }
