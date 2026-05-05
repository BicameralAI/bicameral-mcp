# Plan: Priority C v1 — Notion ingest (database rows, internal integration, upsert cache)

**change_class**: feature
**doc_tier**: system
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (extends a landed L3 service; new credential surface is a static integration token, not OAuth tokens; no new IPC paths beyond what Phase 4 already established; cache-contract migration touches landed Slack code)
**Mode**: solo
**Predecessor**: `plan-priority-c-team-server-slack-v0.md` (v0, Phases 1–4 landed; Phase 5 CocoIndex parked pending feasibility re-research per operator decision 2026-05-02)
**Issue**: none filed yet — operator may want to file before merge

**terms_introduced**:
- term: notion database row
  home: team_server/workers/notion_worker.py
- term: source_watermark
  home: team_server/schema.py
- term: upsert canonical-extraction
  home: team_server/extraction/canonical_cache.py
- term: notion property serializer
  home: team_server/extraction/notion_serializer.py

**boundaries**:
- limitations:
  - v1 ingests **Notion database rows only** — freeform pages and comment threads are out of scope.
  - v1 supports a **single Notion workspace** per team-server install (matches the v0 single-workspace Slack constraint; multi-workspace is a future concern).
  - Auth is **internal-integration token only** — no public-OAuth router, no callback URL, no client secret. Public-OAuth integrations are explicitly out of scope and remain a v2 concern gated on a vendor-hosted offering existing.
  - The allow-list is **derived from `databases.list`**, not stored. Operator's act of sharing a database with the integration in Notion's UI *is* the allow-list signal. No `notion_database_allowlist` table.
  - Notion API calls run inside the team-server worker only; the per-dev local ledger never talks to Notion.
- non_goals:
  - Multi-workspace Notion (one team-server, many Notion workspaces)
  - Webhook-driven ingest (polling only at v1; Notion's webhook surface is connection-trigger, not change-feed, and would not avoid polling anyway)
  - Notion writeback (team-server posting comments/pages back into Notion)
  - Replacing or modifying CocoIndex parking (Phase 5 of v0 plan stays parked)
  - Touching the `bicameral.ingest` MCP tool surface — same posture as v0
  - Refactoring the Slack worker to a generic `Source` abstraction class — parallel-implementation in v1; abstract only when a third real source arrives
- exclusions:
  - No deploy/Dockerfile changes beyond pinning a `notion-client`-equivalent dep (we use raw `httpx` — no new SDK)
  - No new MCP tools — symmetric to v0

## Open Questions

None blocking. Five design points resolved (two during dialogue, three during audit-driven amendment):

1. **Unit of ingest** — Notion *database row*, `source_ref = '{db_id}/{page_id}'`. Freeform pages and comments deferred. Rationale: Notion's structured surface is where the disorder-to-info ratio is best, and the title+properties give strong signal even without an LLM extractor. Operator-resolved.
2. **Edit semantics** — cache becomes upsert per `(source_type, source_ref)`; `content_hash` becomes a tracked column, not part of the unique index. Slack worker migrates to the new contract. `team_event` log retains full edit history; cache holds the latest snapshot. Operator-resolved as a uniform contract for both sources.
3. **Schema version observability** (audit Remediation 1) — added a `schema_version` table that `ensure_schema` UPSERTs on every successful migration. Versioning becomes data, not folklore. The idempotency test reads from the table.
4. **Worker-task lifecycle pattern** (audit Remediation 3) — added a new Phase 0.5 that establishes `asyncio.create_task` registration in `lifespan` and **wires Slack as the canonical reference implementation**. This closes the v0 dormant-Slack-worker gap (the v0 plan claimed an active Slack ingest worker; the v0 code shipped the function with no production caller). Phase 3 then "extends the now-existing pattern with a Notion task" rather than inventing it.
5. **Dispatch loop migration** (audit Remediation 2) — `_MIGRATIONS` type signature changes from `dict[int, tuple[str, ...]]` to `dict[int, Callable[[LedgerClient], Awaitable[None]]]`. The `ensure_schema` dispatch loop is mutated in lockstep; the change is now declared in Affected Files.

---

## Phase 0: Cache contract migration — `(source_type, source_ref)` upsert + Slack worker adaptation

**Why this phase exists**: Notion edits are normal where Slack edits were exceptional. Rather than complecting source-type into the cache contract (one-row-per-content-hash for Slack, latest-snapshot for Notion), both sources share a single upsert-keyed-on-source_ref contract. This phase lands the contract change before any Notion code so Slack invariants are validated against the new shape under the existing Phase 1–4 test surface.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_cache_upsert.py::test_upsert_returns_extraction_and_changed_true_on_first_write` — invokes `upsert_canonical_extraction(client, source_type='slack', source_ref='C1/1.0', content_hash='h1', compute_fn=stub_returning({'decisions':['x']}), model_version='interim-claude-v1')` against a fresh `memory://` ledger; asserts the returned tuple is `({'decisions':['x']}, True)`. Functionality — exercises the new return contract.
- [ ] `tests/test_team_server_cache_upsert.py::test_upsert_returns_changed_false_on_same_hash` — calls upsert twice with identical args; asserts second call returns `(<cached>, False)` and `compute_fn` was invoked exactly once. Functionality — exercises the no-op-on-same-hash invariant.
- [ ] `tests/test_team_server_cache_upsert.py::test_upsert_replaces_extraction_on_hash_change` — calls upsert with `content_hash='h1'`, then with same `source_ref` and `content_hash='h2'`; asserts second call returns `(<new>, True)`, the cache row count for the key is exactly 1, and `canonical_extraction` reflects the second compute. Functionality — exercises the in-place replacement invariant.
- [ ] `tests/test_team_server_cache_upsert.py::test_upsert_unique_index_is_source_type_and_ref_only` — after migration, attempts `CREATE extraction_cache CONTENT { source_type:'slack', source_ref:'C1/1.0', content_hash:'h1', ... }` followed by an identical `CREATE` differing only in `content_hash`; asserts the second `CREATE` fails. Functionality — exercises the index-shape invariant.
- [ ] `tests/test_team_server_schema_migration.py::test_v1_to_v2_migration_drops_old_index_and_defines_new` — seeds a v1-shaped ledger with one duplicate-by-source_ref pair (different content_hash), invokes `ensure_schema(client)`, asserts `idx_extraction_cache_key` exists with fields `source_type, source_ref` only and that exactly one row remains for the duplicated key (the one with the latest `created_at`). Functionality — exercises the migration's dedup-then-redefine path.
- [ ] `tests/test_team_server_schema_migration.py::test_v1_to_v2_migration_is_idempotent` — runs `ensure_schema` twice on a fresh ledger; asserts no exception on the second call AND that the `(source_type, source_ref)` UNIQUE index still rejects a duplicate `CREATE` after the second pass (i.e. the migration didn't redefine the index in a way that broke uniqueness). Functionality — exercises observable post-migration behavior, not a stored marker.
- [ ] `tests/test_team_server_schema_migration.py::test_schema_version_row_records_current_version_after_migrations_apply` — invokes `ensure_schema(client)` on a fresh ledger; queries `SELECT version FROM schema_version LIMIT 1`; asserts the returned row's `version` field equals `SCHEMA_VERSION` (2). Then invokes `ensure_schema` again and asserts the table still has exactly one row with `version = 2` (UPSERT, not INSERT). Functionality — exercises the schema_version-as-data invariant introduced by audit Remediation 1.
- [ ] `tests/test_team_server_schema_migration.py::test_ensure_schema_dispatches_callable_migrations` — registers a synthetic `_MIGRATIONS = {2: stub_migration}` where `stub_migration` is a recording async callable; invokes `ensure_schema`; asserts `stub_migration` was awaited exactly once with the `LedgerClient` instance as its sole argument. Functionality — exercises the new callable-dispatch contract from audit Remediation 2.
- [ ] `tests/test_team_server_slack_worker.py::test_slack_worker_writes_team_event_only_on_changed_returns` — patches the worker's call-site so `upsert_canonical_extraction` returns `(<extraction>, False)`; asserts no `team_event` row is written. Then patches it to return `(<extraction>, True)`; asserts exactly one `team_event` row is written. Functionality — exercises the Slack worker's adaptation to the new tuple-return contract (replaces the existing `cache_existed_before` branch).

### Affected Files

- `team_server/schema.py` — **MUTATE** — bump `SCHEMA_VERSION` from 1 to 2; add `_migrate_v1_to_v2` callable (DROP `idx_extraction_cache_key`, dedup `extraction_cache` rows by max(`created_at`) per `(source_type, source_ref)`, REDEFINE `idx_extraction_cache_key ON extraction_cache FIELDS source_type, source_ref UNIQUE`); add `source_watermark` table; add `schema_version` table (single-row, UPSERT-written after migrations apply — closes audit Remediation 1); change `_MIGRATIONS` type signature from `dict[int, tuple[str, ...]]` to `dict[int, Callable[[LedgerClient], Awaitable[None]]]` and **update `ensure_schema`'s migration dispatch loop** from `for stmt in _MIGRATIONS[version]: await client.query(stmt)` to `await _MIGRATIONS[version](client)` (closes audit Remediation 2).
- `team_server/extraction/canonical_cache.py` — **MUTATE** — replace `get_or_compute(...)->dict` with `upsert_canonical_extraction(...)->tuple[dict, bool]`. Behavior: SELECT by `(source_type, source_ref)`; if row exists and `content_hash` matches stored, return `(stored.canonical_extraction, False)`; else compute via `compute_fn`, UPSERT (UPDATE if row exists, CREATE if not), return `(extraction, True)`. Old function name is gone — no compatibility shim.
- `team_server/workers/slack_worker.py` — **MUTATE** — replace the `cache_existed_before` SELECT-then-call pattern with a single `upsert_canonical_extraction(...)` call; gate the `write_team_event` on the returned `changed` bool. Removes `_cache_row_exists` helper (now dead).
- `tests/test_team_server_cache_upsert.py` — **CREATE** — 4 functionality tests above.
- `tests/test_team_server_schema_migration.py` — **CREATE** — 2 functionality tests above.
- `tests/test_team_server_slack_worker.py` — **MUTATE** — adapt the existing tests to the new tuple return; add the no-event-on-unchanged + event-on-changed pair above.

### Changes

`team_server/extraction/canonical_cache.py` becomes:

```python
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
        "SELECT id, content_hash, canonical_extraction FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr LIMIT 1",
        {"st": source_type, "sr": source_ref},
    )
    if rows and rows[0]["content_hash"] == content_hash:
        return rows[0]["canonical_extraction"], False
    extraction = await compute_fn()
    if rows:
        await client.query(
            "UPDATE $id SET content_hash = $ch, canonical_extraction = $ext, "
            "model_version = $mv",
            {"id": rows[0]["id"], "ch": content_hash, "ext": extraction, "mv": model_version},
        )
    else:
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
            "content_hash: $ch, canonical_extraction: $ext, model_version: $mv }",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "ext": extraction, "mv": model_version},
        )
    return extraction, True
```

`team_server/schema.py` migration block:

```python
from typing import Awaitable, Callable

SCHEMA_VERSION = 2

_BASE_STMTS: tuple[str, ...] = (
    # ... existing tables (workspace, channel_allowlist, extraction_cache, team_event) ...

    # source_watermark — generic per-source, per-resource watermark.
    # Used by polled sources (Notion v1; future polled sources reuse).
    "DEFINE TABLE source_watermark SCHEMAFULL",
    "DEFINE FIELD source_type ON source_watermark TYPE string",
    "DEFINE FIELD resource_id ON source_watermark TYPE string",
    "DEFINE FIELD last_seen   ON source_watermark TYPE string DEFAULT ''",  # ISO-8601 or opaque cursor
    "DEFINE FIELD updated_at  ON source_watermark TYPE datetime DEFAULT time::now()",
    "DEFINE INDEX idx_source_watermark_key ON source_watermark FIELDS source_type, resource_id UNIQUE",

    # schema_version — single-row table holding the current SCHEMA_VERSION.
    # UPSERT-written by ensure_schema after migrations apply. Versioning is
    # data, not folklore (audit Remediation 1).
    "DEFINE TABLE schema_version SCHEMAFULL",
    "DEFINE FIELD version    ON schema_version TYPE int",
    "DEFINE FIELD updated_at ON schema_version TYPE datetime DEFAULT time::now()",
)


async def _migrate_v1_to_v2(client: "LedgerClient") -> None:
    """Drop the v1 (source_type, source_ref, content_hash) UNIQUE index;
    dedup duplicates by max(created_at); redefine the index on
    (source_type, source_ref) UNIQUE. Idempotent: REMOVE INDEX is a
    no-op if the index doesn't exist; the dedup pass deletes nothing
    when no duplicates exist.
    """
    await client.query("REMOVE INDEX idx_extraction_cache_key ON extraction_cache")
    # Per-key dedup: select all rows, group in Python (avoids reliance on
    # SurrealDB v2 GROUP BY+HAVING semantics in embedded mode — see
    # CLAUDE.md "Known v2 quirks"). Keep the row with max(created_at) per
    # (source_type, source_ref) tuple; delete the rest.
    rows = await client.query(
        "SELECT id, source_type, source_ref, created_at FROM extraction_cache"
    )
    survivors: dict[tuple[str, str], dict] = {}
    for row in rows or []:
        key = (row["source_type"], row["source_ref"])
        prior = survivors.get(key)
        if prior is None or row["created_at"] > prior["created_at"]:
            survivors[key] = row
    survivor_ids = {r["id"] for r in survivors.values()}
    for row in rows or []:
        if row["id"] not in survivor_ids:
            await client.query("DELETE $id", {"id": row["id"]})
    await client.query(
        "DEFINE INDEX idx_extraction_cache_key ON extraction_cache "
        "FIELDS source_type, source_ref UNIQUE"
    )


_MIGRATIONS: dict[int, Callable[["LedgerClient"], Awaitable[None]]] = {
    2: _migrate_v1_to_v2,
}


async def ensure_schema(client: "LedgerClient") -> None:
    """Apply base schema (idempotent), run forward migrations, record version."""
    for stmt in _BASE_STMTS:
        try:
            await client.query(stmt)
        except Exception as exc:
            if "already exists" in str(exc).lower():
                continue
            raise
    for version in sorted(_MIGRATIONS):
        await _MIGRATIONS[version](client)  # callable dispatch (Remediation 2)
    # Record the post-migration version. UPSERT MERGE keeps the table
    # at one row regardless of how many times ensure_schema runs.
    await client.query(
        "DELETE schema_version; "
        "CREATE schema_version CONTENT { version: $v }",
        {"v": SCHEMA_VERSION},
    )
    logger.info("[team-server] schema ensured at version %s", SCHEMA_VERSION)
```

The dedup pass is rewritten as a SELECT-then-Python-group-by to avoid relying on SurrealDB v2 embedded `GROUP BY ... HAVING` semantics, which the project's `CLAUDE.md` flags as quirky. Functionality is unchanged.

`team_server/workers/slack_worker.py` — `_ingest_message` becomes:

```python
async def _ingest_message(
    db_client: LedgerClient,
    workspace_team_id: str,
    channel: str,
    message: dict,
    extractor: Extractor,
) -> None:
    text = message.get("text", "")
    ts = message.get("ts", "")
    source_ref = _source_ref_for_message(channel, ts)
    content_hash = _content_hash(text)
    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type="slack",
        source_ref=source_ref,
        content_hash=content_hash,
        compute_fn=lambda: extractor(text),
        model_version=INTERIM_MODEL_VERSION,
    )
    if not changed:
        return
    await write_team_event(
        db_client,
        workspace_team_id=workspace_team_id,
        event_type="ingest",
        payload={
            "source_type": "slack",
            "source_ref": source_ref,
            "content_hash": content_hash,
            "extraction": extraction,
        },
    )
```

The `_cache_row_exists` helper is deleted.

---

## Phase 0.5: Worker-task lifecycle pattern + Slack reference wiring

**Why this phase exists**: Audit Remediation 3. The v0 plan claimed an active Slack ingest worker; the v0 code shipped `slack_worker.poll_once` with zero production callers. `team_server/app.py:22-32` registers no `asyncio.create_task` for any worker. This phase establishes the worker-task lifecycle pattern uniformly and wires Slack as the canonical reference implementation **before** Notion comes along to extend the pattern. Closes the v0 gap.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_worker_lifecycle.py::test_lifespan_starts_slack_worker_when_workspaces_exist` — seeds the `workspace` table with one row; starts the app via `lifespan`; patches `slack_worker.poll_once` to a recording stub; advances the worker's interval timer once; asserts the stub was awaited at least once with the seeded workspace's `team_id` propagated through the wrapper. Functionality — exercises the workspace-iteration→poll wiring.
- [ ] `tests/test_team_server_worker_lifecycle.py::test_lifespan_does_not_invoke_slack_poll_when_workspaces_empty` — leaves `workspace` table empty; starts the app via `lifespan`; patches `slack_worker.poll_once` to a recording stub; advances the worker timer once; asserts the registered Slack task IS spawned (lifespan registers it unconditionally) but `slack_worker.poll_once` was NOT invoked (the workspace SELECT returned no rows so no fan-out happened). Functionality — exercises the empty-workspace branch's no-op behavior.
- [ ] `tests/test_team_server_worker_lifecycle.py::test_lifespan_cancels_slack_worker_task_on_shutdown` — seeds a workspace; starts then cleanly stops the app; asserts the Slack-worker task's state is `done()` and not pending after shutdown completes. Functionality — exercises the cancellation invariant.
- [ ] `tests/test_team_server_worker_lifecycle.py::test_slack_worker_loop_continues_after_single_iteration_raises` — seeds a workspace; patches `poll_once` to raise on the first call and succeed on the second; advances the timer twice; asserts `poll_once` was awaited at least twice. Functionality — exercises the single-iteration-failure-doesn't-kill-loop invariant.
- [ ] `tests/test_team_server_worker_lifecycle.py::test_slack_worker_iterates_all_workspaces_per_poll` — seeds two workspace rows with different `team_id` and decrypted-token-fixture values; patches the slack_client factory to a recording stub; one polling pass; asserts the stub was constructed exactly twice (one per workspace) with the per-workspace token. Functionality — exercises the multi-workspace fan-out invariant within a single polling cycle (forward-compat for v1 multi-workspace; v0 still ships single-workspace via the table having one row).
- [ ] `tests/test_team_server_worker_lifecycle.py::test_slack_worker_skips_workspace_on_decrypt_failure` — seeds two workspace rows; patches the token decryption to raise on the first and succeed on the second; one polling pass; asserts the second workspace's `slack_client` factory was still invoked (failure isolation). Functionality — exercises the per-workspace failure-isolation invariant.
- [ ] `tests/test_team_server_worker_lifecycle.py::test_slack_runner_decrypts_workspace_token_with_loaded_key` — sets `BICAMERAL_TEAM_SERVER_SECRET_KEY` to a real `Fernet.generate_key().decode()`; uses `encrypt_token("xoxb-test-token", key).decode("utf-8")` to seed a single workspace row's `oauth_token_encrypted`; patches `AsyncWebClient.__init__` to a recording stub; runs one `run_slack_iteration` pass; asserts the recording stub received `token="xoxb-test-token"` (the round-trip encrypt → store-as-string → read-back-as-bytes → decrypt succeeded with the loaded key). Functionality — closes the blind spot identified by audit round 2 Finding A: the existing tests patched the slack_client factory but never exercised the actual `decrypt_token(bytes, key)` call shape.

### Affected Files

- `team_server/workers/runner.py` — **CREATE** — `worker_loop(name, interval_seconds, work_fn)` async helper that wraps a single work-fn callable in a forever-loop with try/except + `asyncio.sleep`. Returns the registered `asyncio.Task` so `lifespan` can cancel it cleanly. This is the *one* place worker-task lifecycle is expressed; Slack and Notion both call into it.
- `team_server/workers/slack_runner.py` — **CREATE** — `run_slack_iteration(db_client)` async function that: (1) selects all rows from `workspace` table; (2) per workspace, decrypts the OAuth token via `team_server.auth.encryption`; (3) reads the `channel_allowlist` for that workspace; (4) constructs a `slack_client` via `slack_sdk.web.async_client.AsyncWebClient(token=decrypted)`; (5) calls `slack_worker.poll_once(db_client, slack_client, workspace_team_id, channels, extractor)`; (6) catches per-workspace exceptions so one bad token does not stop iteration over the rest. Replaces what was implicit in v0.
- `team_server/app.py` — **MUTATE** — extend `lifespan` to: (1) construct the interim extractor via direct import (no helper indirection — closes audit Remediation 4); (2) start one Slack worker task via `worker_loop("slack", interval, lambda: run_slack_iteration(db_client))`; (3) on shutdown, cancel the task and `await` it under `CancelledError` swallow.
- `team_server/auth/encryption.py` — **READ-ONLY DEPENDENCY** — referenced by `slack_runner.py` for token decryption; no change.
- `tests/test_team_server_worker_lifecycle.py` — **CREATE** — 6 functionality tests above.
- `tests/test_team_server_app.py` — **MUTATE** — adapt the v0 `test_app_shutdown_releases_db` to also assert the Slack-worker task has been cancelled before DB close.

### Changes

`team_server/workers/runner.py`:

```python
"""Generic worker-task lifecycle helper.

worker_loop wraps a callable in a forever-loop with per-iteration error
isolation and a fixed sleep interval. Returns the asyncio.Task so the
caller (typically the FastAPI lifespan context manager) can cancel it
on shutdown. One location for the loop pattern; Slack and Notion both
delegate here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

WorkFn = Callable[[], Awaitable[None]]


def worker_loop(name: str, interval_seconds: int, work_fn: WorkFn) -> asyncio.Task:
    async def _loop() -> None:
        while True:
            try:
                await work_fn()
            except Exception:  # noqa: BLE001 — single-iteration isolation
                logger.exception("[team-server] worker=%s iteration failed", name)
            await asyncio.sleep(interval_seconds)
    return asyncio.create_task(_loop(), name=f"team-server-worker-{name}")
```

`team_server/workers/slack_runner.py`:

```python
"""Slack worker runner — workspace iteration + per-workspace fan-out.

Single iteration: read all workspaces, decrypt each token, construct a
Slack client per workspace, read the channel allowlist, delegate one
polling pass to slack_worker.poll_once. Per-workspace exceptions are
caught so a single bad token does not break iteration over the rest.

Encryption contract (mirrors team_server/auth/router.py:60-72): the
Fernet key is loaded once per iteration via load_key_from_env; the
oauth_token_encrypted field stores the urlsafe-base64 string output
of Fernet(key).encrypt(...).decode("utf-8"), so decrypting requires
encoding the string back to bytes before passing to decrypt_token.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from slack_sdk.web.async_client import AsyncWebClient

from ledger.client import LedgerClient
from team_server.auth.encryption import decrypt_token, load_key_from_env
from team_server.workers.slack_worker import poll_once

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]


async def run_slack_iteration(
    db_client: LedgerClient, extractor: Extractor
) -> None:
    key = load_key_from_env()  # Fernet key (bytes) — load once per iteration
    workspaces = await db_client.query(
        "SELECT id, slack_team_id, oauth_token_encrypted FROM workspace"
    )
    for ws in workspaces or []:
        try:
            ciphertext = ws["oauth_token_encrypted"].encode("utf-8")
            token = decrypt_token(ciphertext, key)
            channels = await _channel_ids(db_client, ws["id"])
            slack_client = AsyncWebClient(token=token)
            await poll_once(
                db_client=db_client,
                slack_client=slack_client,
                workspace_team_id=ws["slack_team_id"],
                channels=channels,
                extractor=extractor,
            )
        except Exception:  # noqa: BLE001 — per-workspace isolation
            logger.exception(
                "[team-server] slack workspace=%s iteration failed",
                ws.get("slack_team_id", "<unknown>"),
            )


async def _channel_ids(client: LedgerClient, workspace_id: str) -> list[str]:
    rows = await client.query(
        "SELECT channel_id FROM channel_allowlist WHERE workspace_id = $wid",
        {"wid": workspace_id},
    )
    return [r["channel_id"] for r in rows or []]
```

`team_server/app.py` lifespan extension:

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from team_server.db import TeamServerDB
from team_server.extraction.llm_extractor import extract as _interim_extractor
from team_server.schema import SCHEMA_VERSION, ensure_schema
from team_server.workers.runner import worker_loop
from team_server.workers.slack_runner import run_slack_iteration

logger = logging.getLogger(__name__)

SLACK_POLL_INTERVAL_SECONDS = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = TeamServerDB.from_env()
    await db.connect()
    await ensure_schema(db.client)
    app.state.db = db

    slack_task = worker_loop(
        name="slack",
        interval_seconds=SLACK_POLL_INTERVAL_SECONDS,
        work_fn=lambda: run_slack_iteration(db.client, _interim_extractor),
    )
    logger.info("[team-server] started; schema_version=%s; slack worker registered", SCHEMA_VERSION)
    try:
        yield
    finally:
        slack_task.cancel()
        try:
            await slack_task
        except asyncio.CancelledError:
            pass
        await db.close()
        logger.info("[team-server] shut down")
```

The Phase 0.5 lifespan registers exactly one Slack task. Phase 3 will add a second task for Notion via the same `worker_loop` helper — symmetrically.

---

## Phase 1: Notion auth + content fetch primitives

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_notion_client.py::test_load_token_prefers_env_over_config` — sets `NOTION_TOKEN=env_value`, also writes a config file with `notion.token=config_value`; invokes `notion_client.load_token(config_path)`; asserts return value is `'env_value'`. Functionality — exercises precedence rule.
- [ ] `tests/test_team_server_notion_client.py::test_load_token_falls_back_to_config_when_env_unset` — clears env, writes config with `notion.token=config_value`; asserts return value is `'config_value'`. Functionality — exercises the fallback path.
- [ ] `tests/test_team_server_notion_client.py::test_load_token_raises_when_neither_set` — clears env, writes empty config; asserts `notion_client.load_token` raises `NotionAuthError`. Functionality — exercises the missing-token failure.
- [ ] `tests/test_team_server_notion_client.py::test_list_databases_returns_only_databases_filter` — uses `httpx.MockTransport` to return a Notion `search` response with mixed `object: page` and `object: database` entries; asserts `notion_client.list_databases(token)` returns only the database entries with `(id, title)` tuples. Functionality — exercises the `filter: { property: 'object', value: 'database' }` invariant on the search call.
- [ ] `tests/test_team_server_notion_client.py::test_query_database_passes_last_edited_time_filter_when_watermark_given` — uses `httpx.MockTransport`; asserts the outbound request body to `/v1/databases/{db_id}/query` includes `filter: { timestamp: 'last_edited_time', last_edited_time: { after: '<watermark>' } }` when watermark is non-empty, and omits the filter when watermark is empty/None. Functionality — exercises the watermark-to-filter wiring.
- [ ] `tests/test_team_server_notion_client.py::test_fetch_page_blocks_paginates_until_has_more_false` — `MockTransport` returns 3 pages with `has_more: true, next_cursor: ...` for the first 2 and `has_more: false` for the third; asserts `notion_client.fetch_page_blocks(token, page_id)` returns the union of all blocks across pages. Functionality — exercises pagination.
- [ ] `tests/test_team_server_notion_client.py::test_notion_version_header_is_pinned` — asserts every request made by the client carries `Notion-Version: 2022-06-28` (the pinned version). Functionality — exercises the version-pinning invariant.
- [ ] `tests/test_team_server_notion_serializer.py::test_serialize_row_emits_title_then_properties_then_body` — feeds a synthetic Notion DB row + body blocks; asserts the serialized text begins with the title line, followed by `key: value` property lines (sorted by property key for determinism), followed by a blank line, followed by the body block plain-text. Functionality — exercises the deterministic serialization order.
- [ ] `tests/test_team_server_notion_serializer.py::test_serialize_row_handles_typed_properties` — feeds rows with `select`, `multi_select`, `date`, `rich_text`, `checkbox`, `number`, `url`, and `people` properties; asserts each is serialized to a deterministic string form (option name(s); ISO date; concatenated rich_text plain-text; `true`/`false`; numeric repr; URL string; comma-joined user-IDs). Functionality — exercises each typed-property branch.
- [ ] `tests/test_team_server_notion_serializer.py::test_serialize_row_is_byte_stable_across_calls` — invokes `serialize_row` twice with the same row+blocks input; asserts byte-identical output. Functionality — exercises the determinism invariant that gates content_hash stability.

### Affected Files

- `team_server/auth/notion_client.py` — **CREATE** — pure async functions over `httpx.AsyncClient`. Exports: `load_token(config_path) -> str`, `NotionAuthError`, `list_databases(token) -> list[tuple[str, str]]`, `query_database(token, db_id, watermark: str|None) -> AsyncIterator[dict]`, `fetch_page_blocks(token, page_id) -> list[dict]`. No app state; no DB.
- `team_server/extraction/notion_serializer.py` — **CREATE** — pure functions. Exports: `serialize_row(page: dict, blocks: list[dict]) -> str`. Property-type dispatch via a small dict-of-callables; unknown property types serialize as `<unknown:type>` to keep determinism without crashing.
- `team_server/config.py` — **MUTATE** (existing) — add `NotionConfig` dataclass with `token: Optional[str]` field; loaded from YAML's `notion:` section. Token resolution (env vs config) lives in `notion_client.load_token`, not in config — config returns the YAML value verbatim.
- `team_server/requirements.txt` — **MUTATE** — no new deps; `httpx` is already required by Phase 1 of v0. Pin `Notion-Version: 2022-06-28` as a constant in `notion_client.py`, not as a dep.
- `tests/test_team_server_notion_client.py` — **CREATE** — 7 functionality tests above.
- `tests/test_team_server_notion_serializer.py` — **CREATE** — 3 functionality tests above.

### Changes

`team_server/auth/notion_client.py` skeleton:

```python
"""Notion API client — internal-integration auth, no OAuth.

Pure async functions over httpx. Token resolution: NOTION_TOKEN env
preferred; falls back to YAML config's `notion.token`; raises
NotionAuthError if neither is set. Notion-Version header is pinned to
2022-06-28 (the stable version this code is tested against).
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Optional

import httpx
import yaml

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAuthError(RuntimeError):
    """Raised when no Notion integration token can be resolved."""


def load_token(config_path: Optional[str] = None) -> str:
    env = os.environ.get("NOTION_TOKEN")
    if env:
        return env
    if config_path and os.path.exists(config_path):
        with open(config_path) as fh:
            cfg = yaml.safe_load(fh) or {}
        token = (cfg.get("notion") or {}).get("token")
        if token:
            return token
    raise NotionAuthError("NOTION_TOKEN not set and notion.token absent in config")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


async def list_databases(token: str) -> list[tuple[str, str]]:
    """Return [(db_id, title), ...] for every database the integration has been shared with."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{NOTION_API_BASE}/search",
            headers=_headers(token),
            json={"filter": {"property": "object", "value": "database"}},
        )
    resp.raise_for_status()
    out = []
    for entry in resp.json().get("results", []):
        title_parts = entry.get("title") or []
        title = "".join(p.get("plain_text", "") for p in title_parts) or "(untitled)"
        out.append((entry["id"], title))
    return out


async def query_database(
    token: str, db_id: str, watermark: Optional[str]
) -> AsyncIterator[dict]:
    """Yield page rows from a database, optionally filtered by last_edited_time > watermark.
    Sorted by last_edited_time ascending so watermark advancement is monotonic."""
    body: dict = {
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
    }
    if watermark:
        body["filter"] = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"after": watermark},
        }
    cursor: Optional[str] = None
    async with httpx.AsyncClient() as client:
        while True:
            req_body = {**body, **({"start_cursor": cursor} if cursor else {})}
            resp = await client.post(
                f"{NOTION_API_BASE}/databases/{db_id}/query",
                headers=_headers(token),
                json=req_body,
            )
            resp.raise_for_status()
            payload = resp.json()
            for row in payload.get("results", []):
                yield row
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")


async def fetch_page_blocks(token: str, page_id: str) -> list[dict]:
    """Return the flat list of top-level blocks for a page (paginated)."""
    out: list[dict] = []
    cursor: Optional[str] = None
    async with httpx.AsyncClient() as client:
        while True:
            params = {"start_cursor": cursor} if cursor else {}
            resp = await client.get(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(token),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            out.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return out
            cursor = payload.get("next_cursor")
```

`team_server/extraction/notion_serializer.py` skeleton:

```python
"""Notion DB row → text input for the canonical extractor.

Deterministic serialization: title line, then sorted-by-key property
lines, then a blank line, then the body block plain-text. Byte-stable
output is the gating invariant for content_hash stability across polls.
"""

from __future__ import annotations

from typing import Callable


def _rich_text_plain(rich_text: list[dict]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _serialize_property(prop: dict) -> str:
    ptype = prop.get("type")
    if ptype == "title":
        return _rich_text_plain(prop.get("title", []))
    if ptype == "rich_text":
        return _rich_text_plain(prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if ptype == "multi_select":
        return ", ".join(opt.get("name", "") for opt in prop.get("multi_select", []))
    if ptype == "date":
        d = prop.get("date")
        if not d:
            return ""
        start = d.get("start", "")
        end = d.get("end")
        return f"{start}..{end}" if end else start
    if ptype == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    if ptype == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if ptype == "url":
        return prop.get("url") or ""
    if ptype == "people":
        return ", ".join(p.get("id", "") for p in prop.get("people", []))
    return f"<unknown:{ptype}>"


def _block_plain_text(block: dict) -> str:
    btype = block.get("type", "")
    body = block.get(btype) or {}
    return _rich_text_plain(body.get("rich_text", []))


def serialize_row(page: dict, blocks: list[dict]) -> str:
    properties = page.get("properties", {})
    title = ""
    prop_lines: list[str] = []
    for key in sorted(properties):
        prop = properties[key]
        value = _serialize_property(prop)
        if prop.get("type") == "title":
            title = value
        else:
            prop_lines.append(f"{key}: {value}")
    body_lines = [_block_plain_text(b) for b in blocks]
    body_text = "\n".join(line for line in body_lines if line)
    return "\n".join([title, *prop_lines, "", body_text])
```

---

## Phase 2: Notion ingest worker — polling, watermark, peer-author event

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_iterates_databases_from_list_databases` — patches `notion_client.list_databases` to return `[('db1', 'D1'), ('db2', 'D2')]` and `query_database` to yield empty per call; asserts `query_database` was invoked exactly twice with `db_id` values `'db1'` and `'db2'`. Functionality — exercises the no-allowlist-table-derive-from-list_databases invariant.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_writes_event_on_first_seen_row` — mocks `query_database` to yield one row with `id='page1'`, `last_edited_time='2026-05-02T10:00:00Z'`, with a title property; asserts a `team_event` row exists with `payload.source_type='notion_database_row'`, `payload.source_ref='db1/page1'`, `payload.author_email='team-server@notion.bicameral'`, `payload.event_type='ingest'`. Functionality — exercises the new-row → event path.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_is_idempotent_on_unchanged_row` — runs `poll_once` twice with the same mocked row and same content; asserts exactly one `team_event` row exists after the second pass. Functionality — exercises the upsert-changed=False idempotency guarantee under Notion polling.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_writes_new_event_on_edited_row` — runs `poll_once`, then mutates the mocked row's title; runs again; asserts exactly two `team_event` rows exist for the same `(db_id, page_id)` pair, with the second event's `payload.extraction` reflecting the edited title. Functionality — exercises the edit → new event invariant under upsert.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_advances_watermark_to_max_last_edited_time_seen` — yields rows with `last_edited_time` `'2026-05-02T10:00:00Z'` and `'2026-05-02T11:00:00Z'`; after `poll_once`, asserts the `source_watermark` row for `(source_type='notion', resource_id='db1')` has `last_seen='2026-05-02T11:00:00Z'`. Functionality — exercises monotonic watermark advancement.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_passes_stored_watermark_to_query_database_on_subsequent_pass` — pre-seeds `source_watermark` with `last_seen='2026-05-02T09:00:00Z'`; asserts the recorded `query_database` call's `watermark` arg equals `'2026-05-02T09:00:00Z'`. Functionality — exercises the watermark → filter wiring.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_does_not_advance_watermark_when_query_raises` — patches `query_database` to raise `httpx.HTTPError` mid-iteration after one row was yielded; asserts the watermark moved to that one row's `last_edited_time` (not past it), so the next poll re-attempts the rest. Functionality — exercises partial-failure recovery.
- [ ] `tests/test_team_server_notion_worker.py::test_poll_once_skips_database_on_404_logs_and_continues` — mocks `query_database` for `db1` to raise `httpx.HTTPStatusError` 404; for `db2` yields rows normally; asserts events for `db2` are written, no events for `db1`, and the worker did not crash. Functionality — exercises the per-database failure-isolation invariant.
- [ ] `tests/test_team_server_notion_worker.py::test_content_hash_uses_serialized_row_not_raw_page_dict` — ingests a row, then re-runs with the same row but a re-ordered `properties` dict (Python dict ordering doesn't affect serialization but the test guards against it ever doing so); asserts changed=False on the second call (no new event). Functionality — exercises the stability of the content_hash through the deterministic serializer.

### Affected Files

- `team_server/workers/notion_worker.py` — **CREATE** — exports `poll_once(db_client, token, extractor) -> None` mirroring the Slack worker's shape but per-database. Uses `notion_client.list_databases` for discovery, `query_database` per database with stored watermark, `fetch_page_blocks` per row, `notion_serializer.serialize_row` for the extraction input, `upsert_canonical_extraction` for the cache, `write_team_event` for the peer-authored event. Watermark read/write helpers live in this module (small, source-specific) — generalize only when a third source needs them.
- `tests/test_team_server_notion_worker.py` — **CREATE** — 9 functionality tests above.

### Changes

`team_server/workers/notion_worker.py` skeleton:

```python
"""Notion ingest worker — polls allowlist-via-share databases, runs
canonical extraction, writes a peer-authored team_event per change.

Idempotent: same (db_id, page_id) with unchanged content yields no new
event. Per-database watermark is advanced monotonically as rows are
ingested; partial failures stop watermark advancement at the last
successfully-ingested row so the next poll resumes correctly.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

import httpx

from ledger.client import LedgerClient

from team_server.auth import notion_client as nc
from team_server.extraction.canonical_cache import upsert_canonical_extraction
from team_server.extraction.llm_extractor import INTERIM_MODEL_VERSION
from team_server.extraction.notion_serializer import serialize_row
from team_server.sync.peer_writer import write_team_event

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]
SOURCE_TYPE = "notion_database_row"
PEER_AUTHOR_EMAIL = "team-server@notion.bicameral"


async def poll_once(
    db_client: LedgerClient,
    token: str,
    extractor: Extractor,
) -> None:
    databases = await nc.list_databases(token)
    for db_id, _title in databases:
        await _poll_database(db_client, token, db_id, extractor)


async def _poll_database(
    db_client: LedgerClient, token: str, db_id: str, extractor: Extractor
) -> None:
    watermark = await _load_watermark(db_client, db_id)
    last_advanced = watermark
    try:
        async for row in nc.query_database(token, db_id, watermark):
            await _ingest_row(db_client, token, db_id, row, extractor)
            last_advanced = row.get("last_edited_time", last_advanced)
    except httpx.HTTPError as exc:
        logger.warning("[notion-worker] db=%s aborted mid-iteration: %s", db_id, exc)
    finally:
        if last_advanced != watermark:
            await _store_watermark(db_client, db_id, last_advanced)


async def _ingest_row(
    db_client: LedgerClient,
    token: str,
    db_id: str,
    row: dict,
    extractor: Extractor,
) -> None:
    page_id = row["id"]
    blocks = await nc.fetch_page_blocks(token, page_id)
    text = serialize_row(row, blocks)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source_ref = f"{db_id}/{page_id}"
    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type=SOURCE_TYPE,
        source_ref=source_ref,
        content_hash=content_hash,
        compute_fn=lambda: extractor(text),
        model_version=INTERIM_MODEL_VERSION,
    )
    if not changed:
        return
    await write_team_event(
        db_client,
        workspace_team_id=PEER_AUTHOR_EMAIL,
        event_type="ingest",
        payload={
            "source_type": SOURCE_TYPE,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "extraction": extraction,
        },
    )


async def _load_watermark(client: LedgerClient, db_id: str) -> str:
    rows = await client.query(
        "SELECT last_seen FROM source_watermark "
        "WHERE source_type = 'notion' AND resource_id = $rid LIMIT 1",
        {"rid": db_id},
    )
    return rows[0]["last_seen"] if rows else ""


async def _store_watermark(client: LedgerClient, db_id: str, value: str) -> None:
    await client.query(
        "UPSERT source_watermark MERGE { source_type: 'notion', resource_id: $rid, "
        "last_seen: $v, updated_at: time::now() } "
        "WHERE source_type = 'notion' AND resource_id = $rid",
        {"rid": db_id, "v": value},
    )
```

The `write_team_event` call passes `PEER_AUTHOR_EMAIL` as the `workspace_team_id` arg — the field is named after Slack's shape but the underlying `team_event` row stores it under `author_email` (per `team_server/schema.py:53`). If the field name proves load-bearing for downstream consumers, rename in a follow-up; the v0 plan called the field `author_email` already, so this is a no-op.

---

## Phase 3: Notion worker registration — extend the Phase 0.5 worker-task pattern

**Why this phase exists**: Phase 0.5 established the `worker_loop` lifecycle helper and wired Slack as the canonical reference. Phase 3 adds the *second* registered worker (Notion) via the same helper — symmetric structure, no new lifecycle pattern. Notion is opt-in: registration is gated on `notion_client.load_token` succeeding (env or config); when no token resolves, the team-server logs once at INFO and continues without Notion ingest.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_notion_lifecycle.py::test_app_starts_notion_worker_when_token_env_set` — sets `NOTION_TOKEN=fake-token`; patches `notion_runner.run_notion_iteration` to a recording stub; starts the app via the `lifespan` context manager; advances the worker's interval timer once; asserts the stub was awaited at least once. Functionality — exercises the env-gated startup wiring.
- [ ] `tests/test_team_server_notion_lifecycle.py::test_app_does_not_start_notion_worker_when_token_unset` — clears `NOTION_TOKEN` and `BICAMERAL_CONFIG_PATH`; starts the app; asserts the `lifespan`-managed task set contains the Slack task but no task with `name='team-server-worker-notion'`. Functionality — exercises the off-by-default invariant.
- [ ] `tests/test_team_server_notion_lifecycle.py::test_notion_worker_task_is_cancelled_on_shutdown` — sets the token; starts then cleanly stops the app; asserts the registered Notion-worker task's state is `done()` and not pending after shutdown returns. Functionality — exercises the lifecycle invariant under shutdown.
- [ ] `tests/test_team_server_notion_lifecycle.py::test_notion_worker_loop_continues_after_single_iteration_raises` — sets the token; patches `run_notion_iteration` to raise on the first call and succeed on the second; advances the timer twice; asserts the patched stub was awaited at least twice. Functionality — exercises the resilience invariant (delegated to `worker_loop`'s try/except, so this test confirms the helper's contract is honored when a second consumer registers).

### Affected Files

- `team_server/workers/notion_runner.py` — **CREATE** — `run_notion_iteration(db_client, token, extractor)` async function that delegates to `notion_worker.poll_once(db_client, token, extractor)` (no per-workspace iteration — internal-integration auth means a single token covers a single workspace; the wrapper exists for symmetry with `slack_runner.run_slack_iteration` and to give the lifespan a single zero-arg `work_fn` to pass to `worker_loop`).
- `team_server/app.py` — **MUTATE** — after the Phase 0.5 Slack task registration, attempt `notion_client.load_token(config_path=DEFAULT_CONFIG_PATH)` inside a try/except; on success, register a Notion task via `worker_loop("notion", NOTION_POLL_INTERVAL_SECONDS, lambda: run_notion_iteration(db.client, token, _interim_extractor))`; on `NotionAuthError`, log INFO and continue. On shutdown, cancel and await both tasks (extending the Phase 0.5 cancellation pattern with the new task).
- `team_server/config.py` — **MUTATE** — add module-level `DEFAULT_CONFIG_PATH = Path(os.environ.get("BICAMERAL_CONFIG_PATH", "/etc/bicameral-team-server/config.yml"))`. Closes audit Remediation 4 (concrete declaration replacing the v1-pre-amendment placeholder).
- `tests/test_team_server_notion_lifecycle.py` — **CREATE** — 4 functionality tests above.

### Changes

`team_server/workers/notion_runner.py`:

```python
"""Notion worker runner — single-workspace internal-integration shape.

The internal-integration auth model gives one token per Notion
workspace; v1 ships single-workspace, so run_notion_iteration is a
thin wrapper over poll_once. Exists for symmetry with slack_runner
(both expose a zero-extra-arg work_fn for the lifespan to register).
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ledger.client import LedgerClient

from team_server.workers import notion_worker

Extractor = Callable[[str], Awaitable[dict]]


async def run_notion_iteration(
    db_client: LedgerClient, token: str, extractor: Extractor
) -> None:
    await notion_worker.poll_once(db_client, token, extractor)
```

`team_server/app.py` lifespan extension (added after the Phase 0.5 Slack registration):

```python
import os
from team_server.auth import notion_client as nc
from team_server.config import DEFAULT_CONFIG_PATH
from team_server.workers.notion_runner import run_notion_iteration

NOTION_POLL_INTERVAL_SECONDS = int(os.environ.get("NOTION_POLL_INTERVAL_SECONDS", "60"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = TeamServerDB.from_env()
    await db.connect()
    await ensure_schema(db.client)
    app.state.db = db

    tasks: list[asyncio.Task] = []

    # Phase 0.5: Slack worker (always registered)
    tasks.append(worker_loop(
        name="slack",
        interval_seconds=SLACK_POLL_INTERVAL_SECONDS,
        work_fn=lambda: run_slack_iteration(db.client, _interim_extractor),
    ))

    # Phase 3: Notion worker (opt-in, registered only if token resolves)
    try:
        notion_token = nc.load_token(config_path=str(DEFAULT_CONFIG_PATH))
        tasks.append(worker_loop(
            name="notion",
            interval_seconds=NOTION_POLL_INTERVAL_SECONDS,
            work_fn=lambda: run_notion_iteration(db.client, notion_token, _interim_extractor),
        ))
        logger.info("[team-server] notion worker registered")
    except nc.NotionAuthError:
        logger.info("[team-server] notion ingest disabled (no token)")

    logger.info("[team-server] started; schema_version=%s; %d worker(s)", SCHEMA_VERSION, len(tasks))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await db.close()
        logger.info("[team-server] shut down")
```

`team_server/config.py` augmentation (one-line addition):

```python
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(os.environ.get("BICAMERAL_CONFIG_PATH", "/etc/bicameral-team-server/config.yml"))
```

---

## CI Commands

- `pytest -x tests/test_team_server_cache_upsert.py tests/test_team_server_schema_migration.py` — Phase 0 contract migration validation (includes the schema_version + callable-dispatch tests added per audit Remediations 1+2)
- `pytest -x tests/test_team_server_slack_worker.py` — Phase 0 regression check that the Slack worker's adaptation to `upsert_canonical_extraction` did not break landed v0 behavior
- `pytest -x tests/test_team_server_worker_lifecycle.py` — Phase 0.5 worker-task lifecycle pattern + Slack reference wiring (added per audit Remediation 3)
- `pytest -x tests/test_team_server_app.py` — Phase 0.5 lifespan regression check (cancellation invariant under the new task set)
- `pytest -x tests/test_team_server_notion_client.py tests/test_team_server_notion_serializer.py` — Phase 1 client + serializer functionality
- `pytest -x tests/test_team_server_notion_worker.py` — Phase 2 ingest behavior
- `pytest -x tests/test_team_server_notion_lifecycle.py` — Phase 3 Notion task registration
- `pytest -x tests/test_team_server_*.py tests/test_materializer_team_server_pull.py` — full team-server suite, validates Phase 4 materializer still consumes both source types correctly through `/events`
- `pytest -x tests/ -k "not team_server"` — existing-suite regression check (no breakage to per-repo bicameral)
- `docker-compose -f deploy/team-server.docker-compose.yml config > /dev/null` — deploy-artifact validation (no Dockerfile changes expected, but config drift would break v0)

---

## Risk note (L2 grade reasoning)

L2 (not L3) because:

- **No new credential lifecycle**: Notion internal-integration tokens don't expire and don't rotate. Encryption-at-rest of the YAML config is the operator's deployment concern — same posture as any other long-lived API key. No OAuth-state CSRF surface, no callback redirect to validate.
- **No new IPC paths**: Notion events flow through the same `team_event` table and the same `/events` API that Phase 4 already exposes. The per-dev materializer treats `notion_database_row` as just another `source_type` string; failure-isolation invariants from Phase 4 still apply.
- **The cache-contract migration is the load-bearing risk**: Phase 0's schema v1→v2 touches landed code. Mitigation: dedup pass before index swap; idempotent migration; full Slack-worker regression run in the CI command list above. The Phase 0 tests cover `(test_v1_to_v2_migration_drops_old_index_and_defines_new, test_upsert_unique_index_is_source_type_and_ref_only, test_slack_worker_writes_team_event_only_on_changed_returns)` end-to-end before Notion code lands.
- **Determinism invariant**: `serialize_row` byte-stability is what makes the content_hash useful. The serializer test suite includes an explicit `test_serialize_row_is_byte_stable_across_calls`. If a property type lands in production that hits the `<unknown:type>` branch, the operator sees a noisy property line but determinism holds — better than a serializer crash.

---

## Modular commit plan (Option-5 convention)

Five commits, one PR.

```
refactor(team-server): cache-contract migration to upsert-per-source_ref + schema_version table (Phase 0)
feat(team-server): worker-task lifecycle pattern + Slack reference wiring (Phase 0.5)
feat(team-server): Notion API client + property serializer (Phase 1)
feat(team-server): Notion ingest worker + per-database watermark (Phase 2)
feat(team-server): Notion task registration on lifespan (Phase 3)
```

Phase 0 ships even if Phases 0.5+ slip — the contract is uniform improvement on its own, and Slack-worker regression coverage validates it independently. Phase 0.5 ships even if Phases 1–3 slip — it closes the v0 dormant-Slack-worker gap as a standalone fix and the worker-task pattern is a generic improvement. Phases 1–3 cannot ship without Phase 0.5; Phase 0.5 cannot ship without Phase 0.
