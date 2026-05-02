# Plan: Priority C v0 — Self-managing team-server, Slack-first, CocoIndex-conditional

**change_class**: feature
**doc_tier**: system
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L3 (new self-hosted service; new credential surface; new IPC path between team-server and per-dev local ledgers; multi-dev consistency invariant load-bearing for product positioning)
**Mode**: solo (codex-plugin declared unavailable)
**Predecessor**: `docs/research-brief-priority-c-selective-ingest-2026-05-02.md` (research v3); `docs/SHADOW_GENOME.md` Failure Entry #6 + addendum (literal-keyword parsing of CONCEPT.md anti-goals)
**Issue**: no GitHub issue yet — operator may want to file one before merge

**terms_introduced**:
- term: team-server
  home: docs/ARCHITECTURE_PLAN.md (to be amended in Phase 5)
- term: canonical-extraction cache
  home: team_server/extraction/canonical_cache.py
- term: peer-author event identity
  home: team_server/sync/peer_writer.py
- term: workspace allow-list (Slack)
  home: team_server/auth/slack_workspace.py
- term: self-managing backend
  home: docs/CONCEPT.md (to be amended with literal-keyword clarification)

**boundaries**:
- limitations:
  - v0 ships **Slack only**. Notion is v1; GitHub is post-v1 via skill nudge (separate plan).
  - v0 ships **single-workspace** Slack ingest. Multi-workspace (one team-server, many Slack workspaces) is a v1 concern.
  - Team-server is **self-hosted only**; no vendor SaaS surface.
  - **No human ops surface** — schema migration is automatic; restart is idempotent; no DBAs required.
- non_goals:
  - Vendor-hosted SaaS offering ("you sign up at bicameral.com")
  - Multi-region / HA deployment patterns (single instance is the v0 deployment shape)
  - Replacing the existing per-repo embedded SurrealDB ledger
  - Fixing #74 / #72 / other unrelated bugs
  - Touching the `bicameral.ingest` MCP tool surface — the team-server consumes it, doesn't replace it
- exclusions:
  - No changes to `docs/ARCHITECTURE_PLAN.md` substantive architecture beyond adding the team-server section
  - No new MCP tools at v0 — agent talks to bicameral-mcp; bicameral-mcp talks to team-server only via its existing event log consumption
  - No web admin UI in v0 — config is via YAML files in the team-server's local data dir

## Open Questions

None blocking. Four resolved during dialogue:
1. **Deployment shape** — docker-compose with a Python (FastAPI/uvicorn) service. Lowest ops surface; runs on any host with Docker. Customer alternative: `pip install bicameral-team-server && python -m bicameral_team_server` for non-Docker installs.
2. **Sync identity** — team-server authors events under `team-server@<workspace>.bicameral` (single bot per workspace). Per-channel identities is over-engineered for v0.
3. **Slack auth UX** — OAuth web flow on first start (browser redirect to admin's machine); channel allow-list in `team-server-config.yml`. Web admin UI deferred.
4. **CocoIndex (#136) feasibility** — Phase 5 of this plan; structured as discrete deferrable phase. If founder coordination / calendar blocks, ship v0 without; Phase 3's canonical-extraction cache provides extraction determinism in the interim.

---

## Phase 1: Team-server scaffold + self-managing schema

### Verification (TDD)

- [ ] `tests/test_team_server_app.py::test_app_starts_and_serves_health` — invokes `team_server.app:create_app()`; uses `httpx.AsyncClient`; asserts `GET /health` returns `200` with body `{"status": "ok", "schema_version": <int>}`. Functionality, not presence — exercises the actual FastAPI app.
- [ ] `tests/test_team_server_app.py::test_schema_migrates_from_empty_ledger` — invokes `team_server.schema:ensure_schema(client)` against a fresh `memory://` SurrealDB; queries `INFO FOR DB`; asserts the team-server's tables (`workspace`, `channel_allowlist`, `extraction_cache`, `team_event`) are all present. Functionality — invokes the migration, asserts on observed state.
- [ ] `tests/test_team_server_app.py::test_schema_migration_is_idempotent` — runs `ensure_schema` twice; asserts no exception and table count unchanged. Functionality — exercises idempotency invariant.
- [ ] `tests/test_team_server_app.py::test_app_shutdown_releases_db` — starts app via `lifespan` context manager; tears it down; asserts the SurrealDB client `is_connected` is False after teardown. Functionality — exercises the lifecycle invariant.
- [ ] `tests/test_team_server_deploy.py::test_docker_compose_yaml_validates` — invokes `docker-compose -f deploy/team-server.docker-compose.yml config` via `subprocess.run`; asserts exit 0 and stdout contains the `bicameral-team-server` service. Functionality — exercises the deploy artifact's parser-validity.

### Affected Files

- `team_server/__init__.py` — **CREATE** — package marker; export `create_app`
- `team_server/app.py` — **CREATE** — FastAPI app factory; lifespan context manager; `/health` endpoint
- `team_server/schema.py` — **CREATE** — `ensure_schema(client)` function; migrations dispatch table; v0-schema definitions for `workspace`, `channel_allowlist`, `extraction_cache`, `team_event`
- `team_server/db.py` — **CREATE** — `LedgerClient`-mirroring async SurrealDB wrapper (delegates to `ledger.client.LedgerClient` if pattern matches; otherwise minimal local wrapper)
- `deploy/team-server.docker-compose.yml` — **CREATE** — single-service compose; SurrealDB embedded in the container; volume for persistent data
- `deploy/Dockerfile.team-server` — **CREATE** — Python 3.11 base; pip-install the new `team_server` package; expose port 8765
- `team_server/requirements.txt` — **CREATE** — explicit dep pinning: `fastapi`, `uvicorn`, `surrealdb`, `httpx`, `pydantic`
- `tests/test_team_server_app.py` — **CREATE** — 4 functionality tests above
- `tests/test_team_server_deploy.py` — **CREATE** — 1 functionality test above
- `pyproject.toml` — **MUTATE** — add `team_server` package to setup; add optional-extras `[team-server]` for the requirements

### Changes

`team_server/app.py` exports an app factory:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .db import TeamServerDB
from .schema import ensure_schema

@asynccontextmanager
async def lifespan(app: FastAPI):
    db = TeamServerDB.from_env()
    await db.connect()
    await ensure_schema(db.client)
    app.state.db = db
    yield
    await db.close()

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health():
        version = await app.state.db.client.query("RETURN $schema_version", {"schema_version": 1})
        return {"status": "ok", "schema_version": 1}

    return app
```

`team_server/schema.py` follows the `ledger/schema.py` pattern: a `_BASE_STMTS` list of `DEFINE` statements, an `ensure_schema()` function that runs them idempotently, a `_MIGRATIONS` dispatch table for future versions. v0 schema:

- `workspace` (id, name, slack_team_id, oauth_token_encrypted, created_at)
- `channel_allowlist` (id, workspace_id, channel_id, channel_name, added_at)
- `extraction_cache` (id, source_type, source_ref, content_hash, canonical_extraction, model_version, created_at) — keyed unique on `(source_type, source_ref, content_hash)`
- `team_event` (id, author_email, event_type, payload, sequence, created_at) — append-only

`deploy/team-server.docker-compose.yml`: single service `bicameral-team-server`, volume `team-server-data:/data`, env `TEAM_SERVER_PORT=8765`, healthcheck pointing at `/health`.

---

## Phase 2: Slack OAuth + workspace allow-list config

### Verification (TDD)

- [ ] `tests/test_team_server_slack_oauth.py::test_oauth_redirect_url_contains_required_params` — invokes `team_server.auth.slack_oauth:build_authorize_url(client_id, redirect_uri, state)`; asserts URL contains `client_id`, `redirect_uri`, `state`, and the `channels:history,channels:read,groups:history,groups:read` scope set required for ingest. Functionality — invokes URL builder, asserts on output.
- [ ] `tests/test_team_server_slack_oauth.py::test_callback_exchanges_code_for_token` — mocks Slack's OAuth `oauth.v2.access` endpoint via `httpx_mock`; invokes `slack_oauth:exchange_code(code, client_id, client_secret, redirect_uri)`; asserts the function returns the parsed token + team_id and the request body contained `code` and `redirect_uri`. Functionality.
- [ ] `tests/test_team_server_slack_oauth.py::test_callback_persists_workspace_with_encrypted_token` — invokes the FastAPI test client with a mocked OAuth callback; queries the `workspace` table; asserts the row exists, `slack_team_id` matches, and `oauth_token_encrypted` is **not equal** to the cleartext token (i.e., encryption actually happened). Functionality.
- [ ] `tests/test_team_server_slack_oauth.py::test_callback_rejects_invalid_state` — mocks callback with mismatched `state`; asserts 400 response and no row inserted. Functionality — exercises CSRF defense.
- [ ] `tests/test_team_server_channel_allowlist.py::test_config_yaml_loads_channel_allowlist` — writes a fixture `team-server-config.yml` with `slack: {workspaces: [{team_id: T123, channels: [C1, C2]}]}`; invokes `team_server.config:load_channel_allowlist(path)`; asserts the returned dict matches expected shape. Functionality.
- [ ] `tests/test_team_server_channel_allowlist.py::test_config_yaml_rejects_missing_workspace_id` — writes a fixture with channels but no team_id; asserts `load_channel_allowlist` raises `ValueError` with a descriptive message. Functionality — exercises the schema-validation failure path.

### Affected Files

- `team_server/auth/__init__.py` — **CREATE** — package marker
- `team_server/auth/slack_oauth.py` — **CREATE** — `build_authorize_url`, `exchange_code`, callback handler
- `team_server/auth/encryption.py` — **CREATE** — Fernet-based at-rest encryption for OAuth tokens; key from env `BICAMERAL_TEAM_SERVER_SECRET_KEY`
- `team_server/config.py` — **CREATE** — `load_channel_allowlist(path: Path) -> dict`; YAML parser with strict schema validation
- `team_server/app.py` — **MUTATE** — register `/oauth/slack/callback` route; `/oauth/slack/install` route returning the authorize URL
- `team_server/schema.py` — **MUTATE** — `workspace` table already declared in Phase 1; this phase fills its rows
- `team_server/requirements.txt` — **MUTATE** — add `cryptography` (Fernet), `pyyaml`, `pydantic[email]`
- `tests/test_team_server_slack_oauth.py` — **CREATE** — 4 tests above
- `tests/test_team_server_channel_allowlist.py` — **CREATE** — 2 tests above

### Changes

`team_server/auth/slack_oauth.py`:

```python
SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
REQUIRED_SCOPES = ["channels:history", "channels:read", "groups:history", "groups:read"]

def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": ",".join(REQUIRED_SCOPES),
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"

async def exchange_code(code, client_id, client_secret, redirect_uri) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(SLACK_TOKEN_URL, data={
            "code": code, "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri,
        })
    payload = resp.json()
    if not payload.get("ok"):
        raise SlackOAuthError(payload.get("error", "unknown"))
    return payload
```

`team_server/auth/encryption.py`:

```python
from cryptography.fernet import Fernet

def encrypt_token(plaintext: str, key: bytes) -> bytes:
    return Fernet(key).encrypt(plaintext.encode("utf-8"))

def decrypt_token(ciphertext: bytes, key: bytes) -> str:
    return Fernet(key).decrypt(ciphertext).decode("utf-8")
```

`team_server/config.py`: pydantic model `WorkspaceConfig(team_id: str, channels: list[str])`; top-level `Config(slack: SlackConfig)`. `load_channel_allowlist` parses YAML, validates via pydantic, raises `ValueError` on schema failures.

---

## Phase 3: Slack ingest worker + canonical-extraction cache (interim)

### Verification (TDD)

- [ ] `tests/test_team_server_slack_worker.py::test_worker_polls_allowlisted_channels_only` — mocks `slack_sdk.WebClient.conversations_history`; invokes `team_server.workers.slack_worker:poll_once(workspace_id, db)`; asserts the mock was called with channel IDs from the allow-list and NOT with channels outside the list. Functionality — exercises the allow-list filter.
- [ ] `tests/test_team_server_slack_worker.py::test_worker_writes_team_event_for_each_message` — feeds the worker 3 mocked Slack messages; asserts 3 rows in `team_event` after `poll_once` returns; asserts each row's `author_email` is `team-server@<team_id>.bicameral` and `event_type == "ingest"`. Functionality.
- [ ] `tests/test_team_server_slack_worker.py::test_worker_dedups_via_message_ts` — feeds the same Slack message twice (same `ts`); asserts only one `team_event` row after both invocations. Functionality — exercises the idempotency invariant.
- [ ] `tests/test_team_server_canonical_cache.py::test_cache_hit_returns_existing_extraction` — pre-populates `extraction_cache` with one row; invokes `team_server.extraction.canonical_cache:get_or_compute(source_type, source_ref, content_hash, compute_fn)`; asserts `compute_fn` was NOT called and the cached extraction was returned. Functionality.
- [ ] `tests/test_team_server_canonical_cache.py::test_cache_miss_invokes_compute_and_persists` — empty cache; invokes `get_or_compute` with a `compute_fn` that returns `{"decisions": [...]}`; asserts the function was called once, the result was persisted, AND a subsequent call with same key returns from cache without re-invoking. Functionality — exercises the cache-fill path.
- [ ] `tests/test_team_server_canonical_cache.py::test_cache_keys_on_content_hash_changes` — invokes with same `(source_type, source_ref)` but different `content_hash`; asserts both rows persist (i.e., a Slack message edit produces a new cache row). Functionality.

### Affected Files

- `team_server/workers/__init__.py` — **CREATE** — package marker
- `team_server/workers/slack_worker.py` — **CREATE** — async polling worker; reads allowlist; pulls messages; calls extraction; writes events
- `team_server/extraction/__init__.py` — **CREATE** — package marker
- `team_server/extraction/canonical_cache.py` — **CREATE** — `get_or_compute(source_type, source_ref, content_hash, compute_fn) -> dict` + persistence
- `team_server/extraction/llm_extractor.py` — **CREATE** — interim LLM-based extraction (Claude API call) used as the v0 `compute_fn`; deterministic only via cache hit, not via the model itself
- `team_server/sync/__init__.py` — **CREATE** — package marker
- `team_server/sync/peer_writer.py` — **CREATE** — writes a row into `team_event` shaped to match the `events/writer.py` JSONL event contract; `author_email` is `team-server@<team_id>.bicameral`
- `team_server/app.py` — **MUTATE** — start the worker as a background task in the lifespan context
- `team_server/requirements.txt` — **MUTATE** — add `slack_sdk`, `anthropic`
- `tests/test_team_server_slack_worker.py` — **CREATE** — 3 functionality tests above
- `tests/test_team_server_canonical_cache.py` — **CREATE** — 3 functionality tests above

### Changes

`team_server/extraction/canonical_cache.py`:

```python
async def get_or_compute(
    db, source_type: str, source_ref: str, content_hash: str,
    compute_fn,
) -> dict:
    """Return canonical extraction for (source_type, source_ref, content_hash).
    Cache hit: returns persisted extraction without invoking compute_fn.
    Cache miss: invokes compute_fn, persists result, returns it.
    Idempotent on the (source_type, source_ref, content_hash) tuple."""
    cached = await db.client.query(
        "SELECT canonical_extraction FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr AND content_hash = $ch LIMIT 1",
        {"st": source_type, "sr": source_ref, "ch": content_hash},
    )
    if cached:
        return cached[0]["canonical_extraction"]
    extraction = await compute_fn()
    await db.client.query(
        "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
        "content_hash: $ch, canonical_extraction: $ext, model_version: $mv }",
        {"st": source_type, "sr": source_ref, "ch": content_hash,
         "ext": extraction, "mv": "interim-claude-v1"},
    )
    return extraction
```

The `interim-claude-v1` `model_version` is a tombstone label so Phase 5 (CocoIndex) can rebuild cache entries marked interim if the operator wants extraction determinism enforcement.

`team_server/workers/slack_worker.py`: `poll_once(workspace_id, db)` is the unit of work; a background task calls it on a 30s interval. Polling rather than Events API for v0 because Events API requires a public callback URL (not all self-host setups have one).

---

## Phase 4: Per-dev consumption — HTTP event publishing + materializer extension

### Verification (TDD)

- [ ] `tests/test_team_server_events_api.py::test_get_events_returns_team_events_in_sequence_order` — pre-populates `team_event` with 5 rows of varying sequence numbers; invokes `GET /events?since=0&limit=10`; asserts response body has 5 events ordered by `sequence` ascending. Functionality.
- [ ] `tests/test_team_server_events_api.py::test_get_events_paginates_via_since_cursor` — pre-populates 100 rows; calls `/events?since=50&limit=10`; asserts response has rows 51..60 only. Functionality — exercises the pagination contract.
- [ ] `tests/test_team_server_events_api.py::test_get_events_returns_empty_when_no_new_events` — calls `/events?since=999999`; asserts empty array, not error. Functionality.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_pulls_from_team_server_url` — extends `events.materializer.EventMaterializer` with optional `team_server_url`; mocks the `/events` endpoint; invokes `materializer.replay()`; asserts the mocked endpoint was called and events were materialized into the local SurrealDB. Functionality.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_persists_team_server_watermark_separately` — invokes replay twice; asserts the second invocation passes `since=<watermark>` derived from the first; watermark is stored at `.bicameral/local/team_server_watermark`. Functionality — exercises the cursor-persistence invariant.
- [ ] `tests/test_materializer_team_server_pull.py::test_materializer_handles_team_server_unavailable_gracefully` — mocks `/events` to return 503; invokes replay; asserts no exception raised, log contains warning, materializer continues with git-based event sources. Functionality — exercises the failure-isolation invariant (per CONCEPT.md "no network calls in deterministic core" — team-server pull is OUTSIDE the deterministic core, so failure must not cascade).

### Affected Files

- `team_server/api/__init__.py` — **CREATE** — package marker
- `team_server/api/events.py` — **CREATE** — `GET /events?since=<int>&limit=<int>` endpoint reading from `team_event`
- `team_server/app.py` — **MUTATE** — register the events router
- `events/materializer.py` — **MUTATE** — extend `EventMaterializer.__init__` with optional `team_server_url: str | None = None`; in `replay()`, pull `/events?since=<watermark>` after exhausting git-based sources
- `events/team_server_watermark.py` — **CREATE** — small helper for read/write of `.bicameral/local/team_server_watermark` (parallel to existing per-author watermark file)
- `tests/test_team_server_events_api.py` — **CREATE** — 3 functionality tests above
- `tests/test_materializer_team_server_pull.py` — **CREATE** — 3 functionality tests above

### Changes

`team_server/api/events.py`:

```python
from fastapi import APIRouter, Depends, Query

router = APIRouter()

@router.get("/events")
async def get_events(
    since: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db = Depends(get_db),
) -> list[dict]:
    rows = await db.client.query(
        "SELECT * FROM team_event WHERE sequence > $since "
        "ORDER BY sequence ASC LIMIT $limit",
        {"since": since, "limit": limit},
    )
    return rows
```

`events/materializer.py` extension:

```python
class EventMaterializer:
    def __init__(self, events_dir, local_dir, team_server_url: str | None = None):
        # ... existing init ...
        self._team_server_url = team_server_url

    async def replay(self) -> None:
        # ... existing git-based replay ...
        if self._team_server_url:
            await self._replay_team_server()

    async def _replay_team_server(self) -> None:
        watermark_path = self._local_dir / "team_server_watermark"
        since = int(watermark_path.read_text()) if watermark_path.exists() else 0
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._team_server_url}/events",
                    params={"since": since, "limit": 1000},
                    timeout=10,
                )
            events = resp.json()
            for event in events:
                await self._apply_event(event)
            if events:
                watermark_path.write_text(str(events[-1]["sequence"]))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("team-server pull failed: %s", exc)
```

---

## Phase 5: CocoIndex integration (conditional on #136 feasibility)

### Verification (TDD)

- [ ] `tests/test_team_server_cocoindex_extractor.py::test_cocoindex_extractor_is_deterministic_across_invocations` — invokes `team_server.extraction.cocoindex_adapter:CocoIndexExtractor.extract(message_text)` twice with the same input; asserts byte-identical output (including ordering). Functionality — exercises the determinism invariant that's the entire point of using CocoIndex.
- [ ] `tests/test_team_server_cocoindex_extractor.py::test_cocoindex_extractor_replaces_canonical_cache_when_enabled` — feeds the worker a message; with `BICAMERAL_TEAM_SERVER_USE_COCOINDEX=1`, asserts `extraction_cache.model_version == "cocoindex-v1"` (not `interim-claude-v1`). Functionality — exercises the wiring decision.
- [ ] `tests/test_team_server_cocoindex_extractor.py::test_cocoindex_disabled_by_default_falls_back_to_interim` — `BICAMERAL_TEAM_SERVER_USE_COCOINDEX` unset; asserts the worker uses `llm_extractor` and persists `model_version="interim-claude-v1"`. Functionality — exercises the fallback path.

### Affected Files

- `team_server/extraction/cocoindex_adapter.py` — **CREATE** — wraps the CocoIndex Python API; exposes `CocoIndexExtractor.extract(text) -> dict`
- `team_server/extraction/llm_extractor.py` — **MUTATE** — gate behind env var; default branch (env unset) returns interim Claude path
- `team_server/workers/slack_worker.py` — **MUTATE** — select extractor at startup based on env var
- `team_server/requirements.txt` — **MUTATE** — add `cocoindex` (version pin TBD by founder coordination at install time)
- `tests/test_team_server_cocoindex_extractor.py` — **CREATE** — 3 functionality tests above
- `docs/CONCEPT.md` — **AMEND** — add a paragraph clarifying that "no managed backend" parses as "no human-ops-tax architecture," not "no backend"; cite SHADOW_GENOME Entry #6 addendum
- `docs/ARCHITECTURE_PLAN.md` — **AMEND** — add `## Team-server architecture` section describing the v0 deployment shape, sync model, and CocoIndex integration

### Changes

`team_server/extraction/cocoindex_adapter.py`:

```python
import cocoindex

class CocoIndexExtractor:
    """Deterministic extraction via CocoIndex memoized transforms.
    Layer A pre-classifier + Layer B identity capture per #136."""

    def __init__(self, model_version: str = "cocoindex-v1"):
        self.model_version = model_version
        self._flow = cocoindex.flow.from_layers([
            # Layer A: pre-classifier (deterministic memoized)
            cocoindex.transforms.PreClassifier(),
            # Layer B: identity capture (deterministic memoized)
            cocoindex.transforms.IdentityCapture(),
        ])

    def extract(self, text: str) -> dict:
        result = self._flow.run({"text": text})
        return {
            "decisions": result["decisions"],
            "model_version": self.model_version,
        }
```

The exact `cocoindex` API surface is **subject to founder coordination** at integration time. If the actual API differs, the adapter shape stays the same; only internals change. **This is the primary feasibility risk** — Phase 5 ships only if the API is available and stable.

If `BICAMERAL_TEAM_SERVER_USE_COCOINDEX` is unset (default), the worker keeps using `llm_extractor`. v0 ships extraction-deterministic-via-cache (Phase 3) regardless of whether Phase 5 lands.

`docs/CONCEPT.md` amendment text (insert after the existing Anti-Goals list):

```markdown
### Anti-Goal Parsing

The anti-goals above must be read by their load-bearing keyword,
not generalized. "Not a cloud service" means no vendor-hosted SaaS;
"No managed backend" means no architecture that requires customers to
pay an ops tax (DBAs, on-call, manual schema migration). Self-hosted,
self-managing backend components — that the customer deploys without
human ops surface — are compatible. See `docs/SHADOW_GENOME.md`
Failure Entry #6 + addendum for the rationale.
```

---

## CI Commands

```bash
# Per-phase functionality tests (run incrementally during implement)
pytest -x tests/test_team_server_app.py tests/test_team_server_deploy.py
pytest -x tests/test_team_server_slack_oauth.py tests/test_team_server_channel_allowlist.py
pytest -x tests/test_team_server_slack_worker.py tests/test_team_server_canonical_cache.py
pytest -x tests/test_team_server_events_api.py tests/test_materializer_team_server_pull.py
pytest -x tests/test_team_server_cocoindex_extractor.py  # Phase 5 only

# Combined suite for this plan
pytest -x tests/test_team_server_*.py tests/test_materializer_team_server_pull.py

# Deployment artifact validation
docker-compose -f deploy/team-server.docker-compose.yml config > /dev/null

# Existing-suite regression check (no breakage to per-repo bicameral)
pytest -x tests/ -k "not team_server"

# Multi-dev convergence smoke test (manual; encoded as CI step in v1)
# Two simulated devs, one team-server-published canonical decision,
# both ledgers converge — implemented in Phase 4 tests
```

---

## Risk note (L3 grade reasoning)

L3 is warranted because:

- **New attack surface**: team-server holds Slack OAuth tokens + ingests private channel content. Token encryption (Fernet, Phase 2), CSRF defense on the OAuth callback (state parameter, Phase 2), and at-rest encryption of the SurrealDB volume (deployment concern, addressed in `deploy/team-server.docker-compose.yml`) are all required.
- **New IPC path**: per-dev materializers pull from team-server `/events`. Failure-isolation invariant (Phase 4 test #6) prevents team-server outage from cascading into per-dev preflight failures.
- **Multi-dev consistency invariant**: if the team-server's canonical extraction is wrong, every dev sees the same wrong decision. Tradeoff: extraction cache (Phase 3) is auditable post-hoc; CocoIndex (Phase 5) is deterministic-by-construction. Phase 5 hardens the invariant.
- **CONCEPT.md amendment**: Phase 5 amends project DNA. This is governance-grade and warrants `/qor-audit` scrutiny on the wording of the anti-goal-parsing clarification.

---

## Modular commit plan (Option-5 convention; per #149 rebase-merge proposal)

Five commits per phase, one PR. If the team has not yet adopted rebase-merge (per #149), the squash will collapse them — implementer notes the granularity in the PR body for review-time benefit.

```
chore(team-server): scaffold + self-managing schema (Phase 1)
feat(team-server): Slack OAuth + workspace allow-list (Phase 2)
feat(team-server): Slack ingest worker + canonical-extraction cache (Phase 3)
feat(team-server): HTTP /events API + materializer extension (Phase 4)
feat(team-server): CocoIndex integration (Phase 5, conditional on #136)
```

If Phase 5 slips on feasibility, the PR ships Phases 1-4 and a follow-on PR adds Phase 5 once #136 lands.
