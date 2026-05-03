# Plan: Priority C v0 release-blockers (issues #160 + #161) — channel allowlist + materializer payload bridge

**change_class**: feature
**doc_tier**: system
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (touches landed v1.1 code; closes two known v0 functional gaps; no new credential surface)
**Mode**: solo (auto)
**Predecessor**: `plan-priority-c-team-server-real-extractor-v1.md` (sealed at META_LEDGER #36; Merkle `b3700366`)
**Issues**: closes [#160](https://github.com/BicameralAI/bicameral-mcp/issues/160), closes [#161](https://github.com/BicameralAI/bicameral-mcp/issues/161)
**v0 release deadline**: ~2 days. Both phases ship together.

**terms_introduced**:
- term: channel allowlist sync
  home: team_server/auth/allowlist_sync.py
- term: team-server payload bridge
  home: events/materializer.py

**boundaries**:
- limitations:
  - **Phase 1 (allowlist sync)**: startup-time only; YAML edits picked up on next restart, not hot-reloaded. Multi-workspace single-server still v1 concern; this plan reads `config.slack.workspaces[]` and matches by `team_id` against the OAuth-completed `workspace` table.
  - **Phase 2 (materializer bridge)**: maps team-server's `event_type='ingest'` payload shape into an `IngestPayload` (the existing handler input). Decisions land as `source='slack'|'notion'` with empty `repo`/`commit_hash`. Per-dev ledger handles them as ungrounded peer decisions. Subjects (code-region grounding) deferred — the team-server's text-extracted decisions don't reference code.
  - Materializer accepts BOTH `'ingest'` and `'ingest.completed'` going forward (broader is safer); team-server keeps emitting `'ingest'`.
- non_goals:
  - Hot-reload of YAML config without team-server restart
  - Slack `conversations.list` API discovery for channels (operator authors YAML)
  - Code-region grounding for Slack/Notion-sourced decisions (subjects=[] is correct for v0)
  - Multi-workspace per single team-server (still v1 per Priority C plan boundaries)
  - Touching `decision_ratified.completed` / `link_commit.completed` materializer dispatch (those still work; we only ADD `'ingest'` recognition)
- exclusions:
  - No CocoIndex (#136) work
  - No new MCP tool surface
  - No deploy/Dockerfile changes

## Open Questions

None blocking. Four design points resolved in advance per auto-mode (the fourth was added in response to audit round-1 VETO):

1. **Allowlist population strategy**: option (2) startup-time YAML→DB sync. Idempotent reconciliation on each lifespan startup. Picks up operator YAML edits on restart. Doesn't couple to the rarely-invoked OAuth callback path.
2. **Materializer event_type convention**: accept BOTH `'ingest'` and `'ingest.completed'`. Simpler than retrofitting team-server emission; keeps the `.completed` semantic for legacy callers that emit it.
3. **Decision schema for text-sourced decisions**: use the existing `IngestPayload` with `source='slack'|'notion'`, empty `repo`/`commit_hash`, `description` from extraction's `summary`, `source_excerpt` from `context_snippet`. Per-dev ledger handles ungrounded decisions naturally; nothing new to add to the schema.
4. **Pull→dispatch wiring** (audit round-1 finding): use direct adapter dispatch (Option A2 from the audit report), not the JSONL bridge (A1). Periodic task pulls events via `pull_team_server_events`, runs the team-server bridge, and invokes `inner_adapter.ingest_payload` directly. JSONL bypass is acceptable here because team-server events have their own canonical home (the team-server's SurrealDB + `/events` endpoint); re-rendering them as per-author JSONL files in each per-dev repo would be redundant mechanical work. Trade-off acknowledged: team-server events don't appear in `.bicameral/events/` for human inspection; they ARE in the per-dev local SurrealDB and the team-server's own ledger.

## Phase 1: Channel allowlist startup-time sync

**Why this phase exists**: Closes #161. The `channel_allowlist` table is queried by `slack_runner._channel_ids` per polling iteration but nothing populates it. Net effect after v1.0 Phase 0.5: Slack worker runs, decrypts tokens, calls `poll_once(channels=[])`. Zero ingestion.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_allowlist_sync.py::test_sync_inserts_channels_for_workspace_in_yaml` — pre-seeds a workspace row with `slack_team_id='T1'`; YAML config has `slack.workspaces=[{team_id: 'T1', channels: ['C-A', 'C-B']}]`; invokes `sync_channel_allowlist(client, config)`; asserts `channel_allowlist` rows exist for `(workspace_id_for_T1, 'C-A')` and `(workspace_id_for_T1, 'C-B')`. Functionality — exercises the YAML→DB write path.
- [ ] `tests/test_team_server_allowlist_sync.py::test_sync_is_idempotent` — runs sync twice with same input; asserts row count is unchanged after second invocation (UPSERT-shaped, not append). Functionality — exercises the idempotency invariant.
- [ ] `tests/test_team_server_allowlist_sync.py::test_sync_skips_workspaces_not_in_yaml` — pre-seeds two workspace rows (T1, T2); YAML mentions only T1; asserts T2 has no allowlist rows. Functionality — exercises the per-team_id match scope.
- [ ] `tests/test_team_server_allowlist_sync.py::test_sync_skips_workspaces_not_in_db` — YAML mentions T-MISSING; no matching workspace row; asserts no allowlist rows are created (no orphan `workspace_id`). Functionality — exercises the OAuth-must-have-completed precondition.
- [ ] `tests/test_team_server_allowlist_sync.py::test_sync_removes_channels_not_in_yaml` — pre-seeds T1 with allowlist [C-A, C-B]; YAML now lists only [C-A]; runs sync; asserts C-B row is deleted. Functionality — exercises the "operator removes a channel by editing YAML" workflow.
- [ ] `tests/test_team_server_allowlist_lifespan.py::test_lifespan_runs_allowlist_sync_at_startup` — config with one workspace + channels; pre-seeds workspace row; starts app; asserts post-lifespan that `channel_allowlist` is populated. Functionality — exercises the lifespan integration.
- [ ] `tests/test_team_server_slack_worker.py::test_slack_runner_picks_up_synced_allowlist_end_to_end` — full path: pre-seed workspace + run sync + run a slack-runner iteration with patched poll_once; assert poll_once received the synced channels. Functionality — exercises that the cached query in slack_runner sees synced rows.

### Affected Files

- `team_server/auth/allowlist_sync.py` — **CREATE** — exports `sync_channel_allowlist(client, config) -> None` async. For each `WorkspaceConfig` in `config.slack.workspaces`: SELECT workspace by `slack_team_id`; if no match, log INFO and skip (OAuth not yet completed for this team_id). If match: SELECT existing `channel_allowlist.channel_id` set; compute diff vs YAML's `channels`; INSERT new rows + DELETE removed rows. Idempotent.
- `team_server/app.py` — **MUTATE** — lifespan calls `await sync_channel_allowlist(db.client, config)` AFTER `ensure_schema` and AFTER config load, BEFORE worker registration. Failures log at WARN and continue (don't block startup if YAML is partial).
- `tests/test_team_server_allowlist_sync.py` — **CREATE** — 5 functionality tests above
- `tests/test_team_server_allowlist_lifespan.py` — **CREATE** — 1 functionality test above
- `tests/test_team_server_slack_worker.py` — **MUTATE** — add the end-to-end allowlist→runner test

### Changes

`team_server/auth/allowlist_sync.py`:

```python
"""Channel allowlist startup-time sync.

Reads config.slack.workspaces[] and reconciles channel_allowlist
against the workspace table. Per-team_id additive + subtractive sync
so operator YAML edits propagate on next restart. Workspaces in YAML
without a corresponding workspace-table row (no OAuth completed yet)
are logged and skipped — they get picked up on the next sync after
OAuth completes."""

from __future__ import annotations

import logging

from ledger.client import LedgerClient

from team_server.config import TeamServerConfig

logger = logging.getLogger(__name__)


async def sync_channel_allowlist(
    client: LedgerClient, config: TeamServerConfig,
) -> None:
    for workspace_cfg in config.slack.workspaces:
        await _sync_one_workspace(client, workspace_cfg.team_id, workspace_cfg.channels)


async def _sync_one_workspace(
    client: LedgerClient, team_id: str, yaml_channels: list[str],
) -> None:
    rows = await client.query(
        "SELECT id FROM workspace WHERE slack_team_id = $tid LIMIT 1",
        {"tid": team_id},
    )
    if not rows:
        logger.info(
            "[allowlist-sync] no workspace row for team_id=%s; "
            "skipping (OAuth not yet completed)", team_id,
        )
        return
    workspace_id = rows[0]["id"]
    existing_rows = await client.query(
        "SELECT channel_id FROM channel_allowlist WHERE workspace_id = $wid",
        {"wid": workspace_id},
    )
    existing = {r["channel_id"] for r in existing_rows or []}
    desired = set(yaml_channels)
    to_add = desired - existing
    to_remove = existing - desired
    for channel_id in to_add:
        await client.query(
            "CREATE channel_allowlist CONTENT { workspace_id: $wid, "
            "channel_id: $cid, channel_name: '' }",
            {"wid": workspace_id, "cid": channel_id},
        )
    for channel_id in to_remove:
        await client.query(
            "DELETE channel_allowlist WHERE workspace_id = $wid AND channel_id = $cid",
            {"wid": workspace_id, "cid": channel_id},
        )
    logger.info(
        "[allowlist-sync] team_id=%s: +%d -%d (now %d total)",
        team_id, len(to_add), len(to_remove), len(desired),
    )
```

`team_server/app.py` lifespan additions (insert after `await ensure_schema`):

```python
from team_server.auth.allowlist_sync import sync_channel_allowlist

# ... in lifespan, after ensure_schema + config load:
config = _load_config_or_default()
app.state.team_server_config = config
try:
    await sync_channel_allowlist(db.client, config)
except Exception:  # noqa: BLE001
    logger.exception("[team-server] channel_allowlist sync failed; continuing")
```

---

## Phase 1.5: Periodic team-server event consumer (closes audit round-1 finding)

**Why this phase exists**: Audit round-1 surfaced that `events/team_server_pull.py::pull_team_server_events` has zero production callers — the function exists but nothing pulls events into per-dev ledgers. Per-dev materializer iterates JSONL files; team-server events live in HTTP `/events` and would never reach the materializer's dispatch loop without this phase. The bridge in Phase 2 (formerly Phase 2 pre-amendment) is dead code without this wiring.

This phase establishes a periodic asyncio task in the per-dev MCP server's `serve_stdio` startup. The task pulls team-server events on a fixed interval, applies the team-server bridge (defined in Phase 2), and invokes `inner_adapter.ingest_payload` directly. This bypasses the JSONL representation — team-server events have their own canonical home in the team-server's SurrealDB; re-rendering as per-author JSONL would be redundant.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_consumer.py::test_consumer_pulls_events_and_invokes_ingest_payload` — patches `pull_team_server_events` to return one team-server-shaped event; patches `inner_adapter.ingest_payload` to a recording stub; invokes `consume_team_server_events_once(team_server_url, watermark_path, inner_adapter, llm_extract_fn=None)`; asserts the stub was awaited exactly once with a bridged `IngestPayload`-shaped dict. Functionality — exercises the pull→bridge→ingest path end-to-end.
- [ ] `tests/test_team_server_consumer.py::test_consumer_skips_events_with_empty_decisions` — pull returns one event with `extraction.decisions=[]` (chatter); asserts `ingest_payload` was NOT invoked. Functionality — exercises the chatter-skip behavior at consumer layer (mirrors materializer-side behavior in Phase 2).
- [ ] `tests/test_team_server_consumer.py::test_consumer_handles_pull_failure_gracefully` — patches `pull_team_server_events` to return `[]` (its failure-isolation contract); asserts `ingest_payload` NOT invoked AND no exception raised. Functionality — exercises the team-server-unavailable path.
- [ ] `tests/test_team_server_consumer.py::test_consumer_advances_pull_watermark_via_returned_events` — pull returns events with `sequence: [1, 2, 3]`; asserts the second consume call's pull invocation receives `since=3`. Functionality — exercises that `pull_team_server_events`'s own watermark is advanced (already covered by `test_materializer_persists_team_server_watermark_separately` for `pull_team_server_events` in isolation; this test verifies the consumer doesn't break that).
- [ ] `tests/test_team_server_consumer.py::test_start_consumer_loop_registers_task_when_url_set` — sets `BICAMERAL_TEAM_SERVER_URL=http://team:8765`; calls `start_team_server_consumer_if_configured(adapter)`; asserts the returned `asyncio.Task` is non-None and named `bicameral-team-server-consumer`. Functionality — exercises the env-gated startup wiring.
- [ ] `tests/test_team_server_consumer.py::test_start_consumer_loop_returns_none_when_url_unset` — clears `BICAMERAL_TEAM_SERVER_URL`; calls `start_team_server_consumer_if_configured(adapter)`; asserts the return is None. Functionality — exercises the off-by-default invariant.
- [ ] `tests/test_team_server_consumer.py::test_consumer_unwraps_team_write_adapter_does_not_echo_to_jsonl` — constructs a real `TeamWriteAdapter(inner=stub_inner_adapter, writer=recording_writer, materializer=stub_materializer)`; sets `BICAMERAL_TEAM_SERVER_URL` and patches `pull_team_server_events` to return one team-server event with non-empty extraction.decisions; invokes `start_team_server_consumer_if_configured(team_write_adapter)`; advances the asyncio loop one tick; asserts (a) `stub_inner_adapter.ingest_payload` was awaited (the unwrap routed correctly to inner), (b) `recording_writer.write` was NOT called (no echo to per-dev JSONL). Functionality — exercises the no-echo invariant that audit-round-2 Finding A surfaced.

### Affected Files

- `events/team_server_consumer.py` — **CREATE** — exports `consume_team_server_events_once(team_server_url, watermark_path, inner_adapter, llm_extract_fn=None)` async function that calls `pull_team_server_events`, filters team-server-shaped events via `is_team_server_payload`, bridges via `bridge_team_server_payload` (defined in Phase 2; this phase imports the bridge module created there), and invokes `inner_adapter.ingest_payload(bridged)` for each event with non-empty decisions. Also exports `start_team_server_consumer_if_configured(adapter, *, watermark_path=None) -> Optional[asyncio.Task]` that reads `BICAMERAL_TEAM_SERVER_URL` env, returns None if unset, otherwise spawns a forever-loop task that calls `consume_team_server_events_once` every `BICAMERAL_TEAM_SERVER_PULL_INTERVAL_SECONDS` (default 60).
- `server.py` — **MUTATE** — `serve_stdio` adds a call to `start_team_server_consumer_if_configured` parallel to the existing dashboard sidecar startup (line ~1330). Captured task is cancelled on shutdown via the same try/finally pattern used for dashboard.
- `tests/test_team_server_consumer.py` — **CREATE** — 6 functionality tests above.

### Changes

`events/team_server_consumer.py`:

```python
"""Periodic team-server event consumer.

Closes the pull→dispatch gap: pulls events from a team-server URL on
a fixed interval, bridges each event's payload to IngestPayload shape,
and invokes inner_adapter.ingest_payload directly. Bypasses JSONL —
team-server events have their own canonical home in the team-server's
SurrealDB; re-rendering as per-author JSONL files would be redundant.

Failure isolation: pull failures return [] (per pull_team_server_events
contract); per-event ingest failures are caught and logged so a single
malformed event doesn't kill the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from events.team_server_bridge import (
    bridge_team_server_payload, is_team_server_payload,
)
from events.team_server_pull import pull_team_server_events

logger = logging.getLogger(__name__)


async def consume_team_server_events_once(
    team_server_url: str,
    watermark_path: Path,
    inner_adapter,
    llm_extract_fn=None,  # reserved; team-server events are pre-extracted
) -> int:
    """Pull + dispatch one batch. Returns the count of events ingested."""
    events = await pull_team_server_events(
        team_server_url=team_server_url,
        watermark_path=watermark_path,
    )
    ingested = 0
    for event in events:
        payload = event.get("payload") or {}
        if not is_team_server_payload(payload):
            continue
        bridged = bridge_team_server_payload(payload)
        if not bridged.get("decisions"):
            continue  # chatter; skip ingest
        try:
            await inner_adapter.ingest_payload(bridged)
            ingested += 1
        except Exception:  # noqa: BLE001 — per-event isolation
            logger.exception("[team-server-consumer] ingest failed for %s",
                             payload.get("source_ref", "<unknown>"))
    return ingested


def start_team_server_consumer_if_configured(
    adapter, *, watermark_path: Optional[Path] = None,
) -> Optional[asyncio.Task]:
    """Spawn the consumer loop if BICAMERAL_TEAM_SERVER_URL is set.
    Returns the task (caller cancels on shutdown) or None when off.

    Defensive unwrap: TeamWriteAdapter (returned by get_ledger() in team
    mode) wraps SurrealDBLedgerAdapter and emits 'ingest.completed' via
    self._writer.write(...) BEFORE delegating ingest_payload. Consumer-
    driven ingest must use the inner adapter to bypass the writer; if
    we used the wrapper, every team-server event would echo into per-dev
    JSONL → git push → other devs replay → O(N²) cross-dev replay
    amplification per team-server event. Audit-round-2 Finding A.
    """
    url = os.environ.get("BICAMERAL_TEAM_SERVER_URL", "").strip()
    if not url:
        return None
    inner_adapter = getattr(adapter, "_inner", adapter)
    interval = int(os.environ.get("BICAMERAL_TEAM_SERVER_PULL_INTERVAL_SECONDS", "60"))
    if watermark_path is None:
        data_path = os.environ.get("BICAMERAL_DATA_PATH", os.environ.get("REPO_PATH", "."))
        watermark_path = Path(data_path) / ".bicameral" / "local" / "team_server_watermark"

    async def _loop():
        while True:
            try:
                ingested = await consume_team_server_events_once(
                    url, watermark_path, inner_adapter,
                )
                if ingested:
                    logger.info("[team-server-consumer] ingested %d events", ingested)
            except Exception:  # noqa: BLE001
                logger.exception("[team-server-consumer] iteration failed")
            await asyncio.sleep(interval)

    return asyncio.create_task(_loop(), name="bicameral-team-server-consumer")
```

`server.py::serve_stdio` extension (insert after dashboard startup, around line 1331):

```python
async def serve_stdio() -> None:
    dashboard_srv = get_dashboard_server()
    await dashboard_srv.start(ctx_factory=BicameralContext.from_env)

    # Team-server event consumer — opt-in via BICAMERAL_TEAM_SERVER_URL env.
    # Uses the per-repo ledger adapter as the ingest target.
    from adapters.ledger import get_ledger
    from events.team_server_consumer import start_team_server_consumer_if_configured

    team_consumer_task = start_team_server_consumer_if_configured(
        get_ledger(),
    )
    try:
        # ... existing stdio setup (consent + mcp.server.stdio.stdio_server) ...
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(...)
    finally:
        if team_consumer_task is not None:
            team_consumer_task.cancel()
            try:
                await team_consumer_task
            except asyncio.CancelledError:
                pass
```

The `get_ledger()` accessor is verified at `adapters/ledger.py:52` (singleton via `_real_ledger_instance`). The defensive unwrap inside `start_team_server_consumer_if_configured` (shown above as `inner_adapter = getattr(adapter, "_inner", adapter)`) is the load-bearing line: it picks `TeamWriteAdapter._inner` in team mode and falls through to the bare `SurrealDBLedgerAdapter` in solo mode. Without the unwrap, consumer-driven ingest would trigger the wrapper's `_writer.write("ingest.completed", ...)` side effect at `events/team_adapter.py:58`, echoing team-server events into per-dev JSONL files. The new test `test_consumer_unwraps_team_write_adapter_does_not_echo_to_jsonl` exercises this invariant by constructing a real `TeamWriteAdapter` with a recording `EventFileWriter` stub and asserting the writer's `write` method is not called.

---

## Phase 2: Materializer payload bridge for team-server events

**Why this phase exists**: Closes #160. The materializer at `events/materializer.py:89` dispatches on `event_type == 'ingest.completed'` but the team-server emits `event_type='ingest'`. The team-server's payload shape (`{source_type, source_ref, content_hash, extraction}`) doesn't match `IngestPayload` either. With Phase 1.5 wiring the consumer-side ingest, the materializer's bridge is for the secondary path: per-dev devs that pull team-server events into git-tracked JSONL files (out of scope for v0; future-compatible).

The Phase 2 module `events/team_server_bridge.py` is **shared** with Phase 1.5: both consume `is_team_server_payload` + `bridge_team_server_payload`. The bridge module is created in Phase 2 and imported by both Phase 1.5's consumer and Phase 2's materializer dispatch. (Phase 1.5 lands the consumer that imports from the bridge; Phase 2 lands the bridge module + the materializer's reciprocal dispatch case.)

### Verification (TDD — list test files first)

- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_dispatches_team_server_ingest_event` — seeds a JSONL event log line with `event_type='ingest'` and a team-server-shaped payload; runs `materialize_for_dev`; patches `inner_adapter.ingest_payload` to a recording stub; asserts the stub was awaited exactly once with an `IngestPayload`-shaped dict. Functionality — exercises the new dispatch case.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_bridges_slack_extraction_to_ingest_payload` — payload `{source_type: 'slack', source_ref: 'C1/123.0', content_hash: 'h', extraction: {decisions: [{summary: 'use REST', context_snippet: 'we decided to use REST'}], extractor_version: 'haiku-v1', matched_triggers: ['decided']}}`; asserts the bridged IngestPayload has `source='slack'`, `decisions=[{description: 'use REST', source_excerpt: 'we decided to use REST'}]`, `repo=''`, `commit_hash=''`. Functionality — exercises the team-server-shape → IngestPayload mapping.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_bridges_notion_extraction_with_correct_source_type` — identical to the slack test but `source_type='notion_database_row'`; asserts bridged IngestPayload has `source='notion'`. Functionality — exercises the source-type normalization (slack/notion_database_row → slack/notion).
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_skips_team_server_event_with_empty_decisions` — payload's `extraction.decisions=[]` (heuristic-negative classification); asserts `inner_adapter.ingest_payload` is NOT invoked AND `replayed` count is unchanged. Functionality — exercises the chatter-skip behavior (no decision to ingest).
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_still_handles_legacy_ingest_completed_event_type` — pre-existing v0 callers emit `event_type='ingest.completed'`; assert dispatch still routes correctly via the bridge. Functionality — regression coverage that `'ingest.completed'` path is preserved.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_skips_team_server_event_with_malformed_payload` — payload missing `extraction` key; asserts no exception, `inner_adapter.ingest_payload` is NOT invoked. Functionality — exercises defensive shape-checking.

### Affected Files

- `events/materializer.py` — **MUTATE** — add a new dispatch branch BEFORE the existing `'ingest.completed'` branch: `if etype in ("ingest", "ingest.completed") and _is_team_server_payload(payload):` route to `_bridge_team_server_payload(payload)` then `inner_adapter.ingest_payload(bridged)`. Existing `'ingest.completed'` handling for non-team-server payloads stays unchanged. Net effect: BOTH event types route through `ingest_payload`; team-server-shaped payloads get bridged first.
- `events/team_server_bridge.py` — **CREATE** — pure helpers: `is_team_server_payload(payload) -> bool` (heuristic: has `source_type` AND `extraction` keys); `bridge_team_server_payload(payload) -> dict` (returns IngestPayload-compatible dict). Source-type normalization: `'slack'` stays as `'slack'`; `'notion_database_row'` becomes `'notion'`.
- `tests/test_materializer_team_server_pull.py` — **MUTATE** — add 6 functionality tests above; existing 3 tests preserved.

### Changes

`events/team_server_bridge.py`:

```python
"""Bridge: team-server team_event payload → IngestPayload-compatible dict.

The team-server emits events with shape:
  {source_type, source_ref, content_hash, extraction: {decisions, ...}}

The materializer's inner_adapter.ingest_payload expects shape:
  {source, decisions: [{description, source_excerpt, ...}], repo, commit_hash, ...}

This module's two pure functions (is_team_server_payload + 
bridge_team_server_payload) handle the recognition and shape mapping.
"""

from __future__ import annotations


_TEAM_SERVER_SOURCE_NORMALIZATION = {
    "slack": "slack",
    "notion_database_row": "notion",
}


def is_team_server_payload(payload: dict) -> bool:
    """True iff the payload has the team-server event shape."""
    return (
        isinstance(payload, dict)
        and "source_type" in payload
        and isinstance(payload.get("extraction"), dict)
    )


def bridge_team_server_payload(payload: dict) -> dict:
    """Map team-server's payload shape to an IngestPayload-compatible dict.
    Decisions land as source='slack'|'notion' with empty repo/commit_hash
    (Slack/Notion-sourced decisions don't reference code)."""
    source_type = payload.get("source_type", "")
    source = _TEAM_SERVER_SOURCE_NORMALIZATION.get(source_type, source_type)
    extraction = payload.get("extraction") or {}
    raw_decisions = extraction.get("decisions") or []
    decisions = []
    for d in raw_decisions:
        if isinstance(d, dict):
            decisions.append({
                "description": d.get("summary", ""),
                "source_excerpt": d.get("context_snippet", ""),
            })
        elif isinstance(d, str):
            # interim-claude-v1 placeholder shape (paragraph-split strings)
            decisions.append({"description": d, "source_excerpt": d})
    return {
        "source": source,
        "repo": "",
        "commit_hash": "",
        "decisions": decisions,
        "title": payload.get("source_ref", ""),
    }
```

`events/materializer.py` dispatch addition (insert before the existing `'ingest.completed'` branch):

```python
from events.team_server_bridge import (
    bridge_team_server_payload, is_team_server_payload,
)

# ... in materialize_for_dev's event-replay loop:
if etype in ("ingest", "ingest.completed") and is_team_server_payload(payload):
    bridged = bridge_team_server_payload(payload)
    if bridged.get("decisions"):
        await inner_adapter.ingest_payload(bridged)
        replayed += 1
elif etype == "ingest.completed":
    await inner_adapter.ingest_payload(payload)
    replayed += 1
elif etype == "link_commit.completed":
    # ... unchanged ...
```

---

## CI Commands

- `pytest -x tests/test_team_server_allowlist_sync.py tests/test_team_server_allowlist_lifespan.py` — Phase 1 functionality
- `pytest -x tests/test_team_server_slack_worker.py` — Phase 1 end-to-end allowlist → worker
- `pytest -x tests/test_team_server_consumer.py` — Phase 1.5 consumer end-to-end
- `pytest -x tests/test_materializer_team_server_pull.py` — Phase 2 bridge + dispatch
- `pytest -x tests/test_team_server_*.py tests/test_materializer_team_server_pull.py` — full team-server + materializer regression
- `pytest -x tests/ -k "not team_server"` — non-team-server regression check

---

## Risk note (L2 grade reasoning)

L2 because:

- **No new credential lifecycle**: allowlist sync reads from existing YAML + workspace table; both already present
- **Bridge is purely additive**: existing `'ingest.completed'` dispatch path is preserved; the team-server branch is conditional on a payload-shape predicate
- **Deletion semantics in allowlist sync**: removing channels from YAML deletes rows. Operator should know this — document in the implement commit message. Mitigation: log INFO with `+N -N` summary so the operator sees the diff applied
- **Empty `repo`/`commit_hash` in bridged IngestPayload**: per-dev `ingest_payload` handler may emit "ungrounded decision" warnings. v0-acceptable; v1.next can introduce a proper text-sourced-decision ingest path

---

## Modular commit plan

Three commits, one PR (or fold into existing PR #159 since this is the same v0 release).

```
feat(team-server): channel_allowlist startup-time YAML sync (closes #161)
feat(team-server): periodic team-server event consumer + payload bridge (closes #160 first half)
feat(team-server): materializer dispatch case for legacy JSONL replay path (closes #160 second half)
```

Phase 1 closes the allowlist gap regardless of consumer state. Phase 1.5 (commit 2) closes the load-bearing v0 gap (events flow from team-server → per-dev ledger). Phase 2 (commit 3) adds the materializer's reciprocal dispatch case for any future flow that writes team-server events to git-tracked JSONL — defensive, not load-bearing for v0.

The audit round-1 finding identified that without Phase 1.5, the v0 ingest pipeline ships plumbed-but-inert. Phase 1.5 is the load-bearing piece; Phase 2 is supporting infrastructure that becomes useful when the JSONL flow is wired in v1.next (if at all).
