# Plan: emit `compliance_check.completed` event + cross-author replay

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: `compliance_check.completed`
  home: docs/v0-architecture-current.md (§5 emitted-events table)

**boundaries**:
- limitations: cross-author replay matches regions by `(repo, file_path, symbol_name, content_hash)` — content-addressable, survives line-number shifts. Position-based matching is rejected (line numbers shift across replays).
- non_goals: backfilling historical compliance events for prior `link_commit.completed` replays. Going-forward only.
- exclusions: renaming the ledger `compliance_check` table or any other event types; SPEC.bql formalism; new server-side LLM call.

## Open Questions

None. All design choices resolved in `/qor-plan` dialogue:
- Q1 region descriptor → content-addressable `(repo, file_path, symbol_name, content_hash)`, not position-addressable.

## Phase 1: Emit `compliance_check.completed` from `handle_resolve_compliance`

### Affected Files

- `tests/test_team_event_replay.py` — new tests for emission contract (3 tests)
- `events/team_adapter.py` — new `apply_resolve_compliance` method that writes the JSONL event
- `handlers/resolve_compliance.py` — call `apply_resolve_compliance` after `upsert_compliance_check` succeeds, when the underlying ledger is the team adapter

### Changes

**`events/team_adapter.py`** — add an emit method alongside the existing `apply_ratify` / `apply_supersede`:

```python
async def apply_resolve_compliance(
    self,
    *,
    canonical_decision_id: str,
    repo: str,
    file_path: str,
    symbol_name: str,
    content_hash: str,
    verdict: Literal["compliant", "drifted", "not_relevant"],
    pinned_commit: str,
    evidence: str = "",
) -> None:
    """Emit `compliance_check.completed` to the team-sync JSONL stream.

    Fires once per accepted `ComplianceVerdict`. Receiver re-applies
    the verdict via the materializer's content-addressable lookup.
    Idempotent on receiver side — `upsert_compliance_check` already
    first-write-wins on `(decision_id, region_id, content_hash)`.
    """
    payload = {
        "canonical_decision_id": canonical_decision_id,
        "region": {
            "repo": repo,
            "file_path": file_path,
            "symbol_name": symbol_name,
            "content_hash": content_hash,
        },
        "verdict": verdict,
        "pinned_commit": pinned_commit,
        "evidence": evidence,
    }
    self._writer.write("compliance_check.completed", payload)
```

**`handlers/resolve_compliance.py`** — after each successful `upsert_compliance_check` call (line 156-168 in current code), invoke the emit when the adapter is the team adapter. Resolution: pass `canonical_decision_id` looked up via the existing `get_canonical_id` helper in `ledger/queries.py`; pass `repo`, `file_path`, `symbol_name`, and `content_hash` from the `code_region` row that was just verdicted.

The team-adapter check follows the existing pattern from `apply_ratify` / `apply_supersede` — `hasattr(ledger, "_team_adapter")` or equivalent attribute set during construction.

### Unit Tests

- `tests/test_team_event_replay.py::test_resolve_compliance_emits_one_event_per_verdict` — invoke `handle_resolve_compliance` with a team adapter and 2 accepted verdicts; assert exactly 2 `compliance_check.completed` events appear in the JSONL writer's captured output. Confirms one-event-per-verdict invariant.

- `tests/test_team_event_replay.py::test_resolve_compliance_no_emit_in_single_mode` — invoke `handle_resolve_compliance` with the non-team `SurrealDBLedgerAdapter`; assert no JSONL events written. Confirms emission is gated on team mode.

- `tests/test_team_event_replay.py::test_compliance_event_payload_is_content_addressable` — invoke `handle_resolve_compliance`, capture the emitted event, assert the `region` block contains `content_hash` and does NOT contain `start_line` / `end_line`. Confirms Q1 design choice (line numbers excluded; receiver matches by content hash).

## Phase 2: Replay branch in `events/materializer.py`

### Affected Files

- `tests/test_team_event_replay.py` — new tests for replay contract (3 tests, extending Phase 1 file)
- `events/materializer.py` — new `compliance_check.completed` dispatch case in the `EventMaterializer.replay_new_events` switch
- `ledger/queries.py` — new helper `find_code_region_by_content` resolving `(repo, file_path, symbol_name, content_hash)` to a local `code_region` id (for the receiver-side lookup)
- `ledger/adapter.py` — new public method `apply_compliance_verdict_from_event` that takes the resolved `decision_id`, `region_id`, `verdict`, `pinned_commit`, `evidence` and calls the existing `upsert_compliance_check` path

### Changes

**`ledger/queries.py`** — pure-data lookup function:

```python
async def find_code_region_by_content(
    client,
    *,
    repo: str,
    file_path: str,
    symbol_name: str,
    content_hash: str,
) -> str | None:
    """Resolve a code_region by content-addressable key. Returns the local
    region id, or None if no matching row exists yet on this DB."""
```

**`events/materializer.py`** — dispatch branch matching the existing `decision_ratified.completed` shape:

```python
elif etype == "compliance_check.completed":
    from ledger.queries import find_decision_by_canonical_id, find_code_region_by_content

    local_decision_id = await find_decision_by_canonical_id(
        inner_adapter._client,
        payload.get("canonical_decision_id", ""),
    )
    if local_decision_id is None:
        logger.warning(
            "[materializer] skipping compliance_check.completed — "
            "canonical_decision_id %r not found locally",
            payload.get("canonical_decision_id"),
        )
        continue
    region = payload.get("region") or {}
    local_region_id = await find_code_region_by_content(
        inner_adapter._client,
        repo=region.get("repo", ""),
        file_path=region.get("file_path", ""),
        symbol_name=region.get("symbol_name", ""),
        content_hash=region.get("content_hash", ""),
    )
    if local_region_id is None:
        logger.warning(
            "[materializer] skipping compliance_check.completed — "
            "region (%s::%s @ %s) not yet materialized locally",
            region.get("file_path"),
            region.get("symbol_name"),
            region.get("content_hash", "")[:8],
        )
        continue
    await inner_adapter.apply_compliance_verdict_from_event(
        decision_id=local_decision_id,
        region_id=local_region_id,
        verdict=payload.get("verdict", ""),
        pinned_commit=payload.get("pinned_commit", ""),
        evidence=payload.get("evidence", ""),
    )
    replayed += 1
```

**`ledger/adapter.py::SurrealDBLedgerAdapter`** — `apply_compliance_verdict_from_event` thin wrapper that delegates to the existing `upsert_compliance_check` query; surfaces it as a public method so the materializer doesn't reach into private query helpers.

### Unit Tests

- `tests/test_team_event_replay.py::test_compliance_event_replay_writes_compliance_check_row` — full round-trip: ingest + bind + resolve_compliance on adapter A produces a JSONL event; replay into adapter B's fresh memory:// DB; assert B has a `compliance_check` row with the matching verdict AND `project_decision_status` returns the same status as A's. Confirms the receiver-side state matches sender-side state via the event substrate (the §5 team-sync invariant).

- `tests/test_team_event_replay.py::test_compliance_event_replay_warns_when_region_missing_locally` — emit a `compliance_check.completed` event referencing a region whose content_hash doesn't exist on the receiver's DB; replay; capture log output; assert one `WARNING` line is emitted naming the file_path/symbol_name AND no compliance_check row was written on the receiver. Confirms the documented fail-soft contract.

- `tests/test_team_event_replay.py::test_compliance_event_replay_idempotent_on_duplicate` — replay the same `compliance_check.completed` event twice into receiver B's DB; assert only one `compliance_check` row exists after both replays AND the second replay completes without error. Confirms first-write-wins idempotency on `(decision_id, region_id, content_hash)` survives replay.

## Phase 3: Documentation update

### Affected Files

- `docs/v0-architecture-current.md` — update §5 emitted-events table to include `compliance_check.completed`; update the "Known gap" prose at line 115 to past-tense; remove the broken `#178` cross-reference; update the Reconciliation table at the bottom

### Changes

**§5 emitted-events table** — add row:

```markdown
| `compliance_check.completed` | After `resolve_compliance` writes a verdict |
```

**§5 prose at line 115** — replace the "Known gap (tracked separately)" paragraph:

> ~~**Known gap (tracked separately)**: `compliance_checked` is named in the Notion page but is **not currently emitted**. … Until fixed, teammate replays infer compliance state from `link_commit.completed` effects rather than from explicit verdict events.~~
>
> All v0 verdict transitions emit explicit events. Receiver-side replay resolves regions content-addressably (`(repo, file_path, symbol_name, content_hash)`); regions not yet materialized locally produce a logged warning rather than a silent inconsistency. See #190 for the design.

**Reconciliation notes table at the bottom** — remove the row claiming `compliance_checked` is missing (now resolved).

### Unit Tests

None — pure markdown content; reviewed by humans on PR. The acceptance check is that the §5 events table matches the actual emitter set in `events/team_adapter.py`, which Phase 1's tests already lock.

## CI Commands

- `pytest tests/test_team_event_replay.py -v` — validates emission + replay contract (6 tests)
- `pytest tests/ -v --no-cov` — full regression sweep
- `mypy .` — type-check
- `ruff check . && ruff format --check .` — lint + format
