# Plan: Remote event-log adapter (issue #277)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: BackendAdapter
  home: events/backends/__init__.py
- term: LocalFolderAdapter
  home: events/backends/local_folder.py
- term: GoogleDriveAdapter
  home: events/backends/google_drive.py
- term: remote_root
  home: docs/team-mode-setup.md
- term: Create (shared ledger)
  home: docs/team-mode-setup.md
- term: Join (shared ledger)
  home: docs/team-mode-setup.md
- term: founding member
  home: docs/team-mode-setup.md
- term: shared ledger
  home: docs/team-mode-setup.md

**boundaries**:
- limitations:
  - Pull-only sync. No webhook receivers, no daemons, no background polling.
  - One remote folder per repo. Multi-tenant orchestration is out of scope.
  - Conflict resolution is by ordering (timestamp) + DB-level `canonical_id` UNIQUE; no merge UI.
  - Sync cadence is on-invocation only (with a short TTL cache to avoid hammering remote on every tool call).
  - GoogleDriveAdapter targets a single shared Drive folder; no Shared Drive (Team Drive) special-casing in v0.
- non_goals:
  - S3, Dropbox, OneDrive adapters. Interface is designed for them; bodies are P1.
  - Encryption at rest beyond what the backend provides.
  - Real-time push or change notifications.
- exclusions:
  - Mutating peer event files. Each author writes ONLY to `<my-email>.jsonl`. Pull is read-only with respect to peer files; we replace local copies with remote contents.

## Open Questions

1. **Sync TTL on `pull_events`** — proposal: 30 s in-process cache keyed on `(remote_root, repo_path)`. Rationale: a session with a tight tool-call loop shouldn't make 20 Drive API calls per minute, but a multi-minute coding session should pick up peer events. Alternative is `pull_events` always runs and the LocalFolderAdapter is fast enough that it doesn't matter — but Drive is not.
2. **`push_events` cadence** — proposal: push the author's own JSONL file once per tool-call lifecycle (at end of `TeamWriteAdapter._ensure_ready` if any writes happened in this process, OR explicitly at end of `handle_*` for write tools). Per-event push is rejected (Drive rate limits + adds latency to every write).
3. **Drive OAuth redirect** — proposal: localhost loopback (`http://localhost:<random-port>/callback`) following the standard `google-auth-oauthlib.flow.InstalledAppFlow.run_local_server()` pattern. No public callback URL needed.
4. **Lock semantics** — proposal: `lock(remote_path)` returns a context manager that's a no-op for LocalFolderAdapter when `remote_path == events_dir/{my-email}.jsonl` (same-author writes serialize via the existing fcntl lock in `events/writer.py`); GoogleDriveAdapter uses a sentinel file (`<my-email>.lock`) with last-writer-wins semantics. Caller is expected to retry-on-conflict, not block. For v0, no caller actually invokes `lock()` — it exists in the protocol so future writers (e.g., a second machine for the same author) can opt in.

## Phase 1: BackendAdapter protocol + LocalFolderAdapter + wire into TeamWriteAdapter

### Affected Files

- `events/backends/__init__.py` (new) — `BackendAdapter` ABC; module-level factory `get_backend(config) -> BackendAdapter | None`
- `events/backends/local_folder.py` (new) — `LocalFolderAdapter` implementing the protocol against a shared filesystem path
- `events/team_adapter.py` — accept optional `backend: BackendAdapter | None`; in `connect()` call `backend.pull_events()` BEFORE `materializer.replay_new_events()`; after every `_writer.write(...)` set a "dirty" flag; expose an `async def flush_to_backend()` that uploads the author's file when dirty
- `events/materializer.py` — no change to the body; the contract that "files in `events_dir` are the source of truth" is preserved
- `adapters/ledger.py` — read `team.backend` from `.bicameral/config.yaml`; if present, construct the corresponding `BackendAdapter` and pass into `TeamWriteAdapter`
- `handlers/sync_middleware.py` — add `async def ensure_team_synced(ctx)` (TTL-cached pull); call it from the same dispatch site as `ensure_ledger_synced`; call `flush_to_backend()` after handler completion for write tools
- `server.py` — wire `ensure_team_synced` and the post-handler flush at the dispatch site (mirror existing `ensure_ledger_synced` placement)

### Changes

**`events/backends/__init__.py`** — small ABC, four operations, no JSONL knowledge:

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import AsyncIterator

class BackendAdapter(ABC):
    """Move per-author event files between local cache and a remote root.

    Knows nothing about JSONL contents — pure file transport. The remote
    root is a flat directory of <author-email>.jsonl files (one per peer)
    plus optional <author-email>.lock sentinel files.
    """

    @abstractmethod
    async def push_events(self, local_path: Path, remote_name: str) -> None:
        """Upload local_path to <remote_root>/<remote_name>. Idempotent
        (skip when remote hash matches local)."""

    @abstractmethod
    async def pull_events(self, local_dir: Path, since_token: str | None) -> str:
        """Download every <peer>.jsonl in remote_root into local_dir,
        skipping the caller's own file. Returns an opaque token the caller
        passes back next time to enable since-cursor optimization (backends
        free to ignore and return ""). Idempotent."""

    @abstractmethod
    def lock(self, remote_name: str) -> AbstractAsyncContextManager[None]:
        """Best-effort write lock. Caller must handle races on its own."""

    @abstractmethod
    async def list_peers(self) -> AsyncIterator[str]:
        """Yield <author-email> for every peer file in remote_root."""

def get_backend(config: dict) -> "BackendAdapter | None":
    backend_kind = (config.get("team") or {}).get("backend")
    if backend_kind == "local_folder":
        from .local_folder import LocalFolderAdapter
        return LocalFolderAdapter(remote_root=Path(config["team"]["remote_root"]),
                                  author=config["team"]["author"])
    if backend_kind == "google_drive":
        from .google_drive import GoogleDriveAdapter
        return GoogleDriveAdapter(folder_id=config["team"]["folder_id"],
                                  author=config["team"]["author"])
    return None
```

**`events/backends/local_folder.py`** — minimal real implementation:

- `push_events(local_path, remote_name)`: compute `sha256(local_path.read_bytes())`; if `(remote_root / remote_name).exists()` and its hash matches, return; else `shutil.copy2`.
- `pull_events(local_dir, since_token)`: iterate `remote_root.glob("*.jsonl")`; for each `peer.jsonl != f"{author}.jsonl"`, hash-compare with `local_dir / peer.jsonl`, copy when different. Returns `""` (no since-token in v0).
- `lock(remote_name)`: returns an async context manager that opens `remote_root / f"{remote_name}.lock"` with fcntl exclusive lock (POSIX) / msvcrt (Windows); no-op if remote_root is not on a filesystem that supports flock (degrade gracefully).
- `list_peers()`: yield `path.stem` for each `*.jsonl` in remote_root.

**`events/team_adapter.py`** — accept backend, drive pull/push lifecycle:

```python
def __init__(self, inner, writer, materializer, backend=None):
    self._inner = inner
    self._writer = writer
    self._materializer = materializer
    self._backend = backend
    self._dirty = False
    self._ready = False

async def connect(self):
    await self._inner.connect()
    if self._backend is not None:
        await self._backend.pull_events(
            self._writer.events_dir, since_token=None)
    replayed = await self._materializer.replay_new_events(self._inner)
    ...
    self._ready = True

# After every self._writer.write(...) in existing methods, set:
#   self._dirty = True

async def flush_to_backend(self):
    """Push the author's JSONL to remote if any write happened since last flush."""
    if self._backend is None or not self._dirty:
        return
    await self._backend.push_events(
        self._writer.path, remote_name=self._writer.path.name)
    self._dirty = False
```

**`adapters/ledger.py`** — refactor `_read_collaboration_mode` and extend the team-mode branch:

Refactor: replace the existing `_read_collaboration_mode(repo_path) -> str` with `_read_team_config(repo_path) -> dict` that returns the full parsed config (or `{"mode": "solo"}` when no config). Update its single existing caller (`get_ledger`) to `cfg = _read_team_config(repo_path); mode = cfg.get("mode", "solo")`. The existing string-only contract is internal — no external callers grep across the tree (verified).

```python
def _read_team_config(repo_path: str) -> dict:
    """Read .bicameral/config.yaml as a dict. Returns {"mode": "solo"} if absent."""
    data_path = os.getenv("BICAMERAL_DATA_PATH", repo_path)
    config_path = Path(data_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return {"mode": "solo"}
    try:
        import yaml
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {"mode": "solo"}
    except Exception:
        return {"mode": "solo"}

# In get_ledger():
if mode == "team":
    ...
    cfg = _read_team_config(repo_path)
    cfg.setdefault("team", {})["author"] = author
    from events.backends import get_backend
    backend = get_backend(cfg)
    _real_ledger_instance = TeamWriteAdapter(inner, writer, materializer, backend=backend)
```

**`handlers/sync_middleware.py`** — add team-sync companion to `ensure_ledger_synced`:

```python
_LAST_TEAM_PULL_AT: dict[str, float] = {}  # repo_path -> monotonic ts
_TEAM_PULL_TTL_S = 30.0

async def ensure_team_synced(ctx) -> None:
    """Pull peer events from the team backend, TTL-cached per repo."""
    ledger = getattr(ctx, "ledger", None)
    if ledger is None or not hasattr(ledger, "_backend") or ledger._backend is None:
        return
    repo = getattr(ctx, "repo_path", "") or "."
    now = time.monotonic()
    last = _LAST_TEAM_PULL_AT.get(repo, 0.0)
    if now - last < _TEAM_PULL_TTL_S:
        return
    try:
        await ledger._backend.pull_events(
            ledger._writer.events_dir, since_token=None)
        await ledger._materializer.replay_new_events(ledger._inner)
        _LAST_TEAM_PULL_AT[repo] = now
    except Exception as exc:
        logger.debug("[sync_middleware] team pull failed: %s", exc)

async def flush_team_writes(ctx) -> None:
    ledger = getattr(ctx, "ledger", None)
    if ledger is not None and hasattr(ledger, "flush_to_backend"):
        try:
            await ledger.flush_to_backend()
        except Exception as exc:
            logger.debug("[sync_middleware] team flush failed: %s", exc)
```

**`server.py`** — at the dispatch site that already calls `ensure_ledger_synced(ctx)`, add `await ensure_team_synced(ctx)` immediately after; in the `finally` of the dispatch body, add `await flush_team_writes(ctx)`. (Exact line numbers determined during implementation; mirror the existing pattern.)

### Unit Tests

- `tests/test_backends_local_folder.py` (new):
  - `test_push_uploads_when_remote_missing` — write local file, push, assert remote bytes match.
  - `test_push_skips_when_remote_hash_matches` — push twice, assert second push does NOT modify remote mtime (or assert via spy that `shutil.copy2` was called once).
  - `test_pull_copies_peer_files_only` — populate remote with `mine.jsonl` and `peer.jsonl`; pull as `mine`; assert `peer.jsonl` arrived, `mine.jsonl` was NOT overwritten.
  - `test_pull_skips_unchanged` — second pull with no remote change is a no-op (file mtime unchanged).
  - `test_list_peers_yields_email_stems` — populate remote with three `*.jsonl`; assert `list_peers()` yields the three stems.
  - `test_lock_serializes_concurrent_acquirers` — two coroutines acquire same `lock(name)`; assert ordering is preserved (second waits for first).

- `tests/test_team_adapter_with_backend.py` (new):
  - `test_connect_pulls_then_replays` — fake backend yields a peer event file; `await adapter.connect()`; assert the inner adapter saw the peer's `ingest_payload(...)` call.
  - `test_write_marks_dirty_then_flush_pushes` — call `ingest_payload`; assert `flush_to_backend()` invokes `backend.push_events(self._writer.path, ...)`; second flush without writes is a no-op.
  - `test_no_backend_means_no_push_no_pull` — `backend=None`; full mutation cycle works; no errors raised.

- `tests/test_team_round_trip_local_folder.py` (new — integration):
  - `test_two_authors_round_trip` — spin up two `TeamWriteAdapter` instances pointing at the SAME `remote_root` but different `events_dir` and different author emails; author A ingests a decision, calls `flush_to_backend()`; author B calls `connect()` (which triggers pull + replay); assert author B's inner adapter has the decision.
  - `test_pull_idempotent_across_invocations` — same scenario, run B's `ensure_team_synced` cycle three times; assert only the FIRST pull caused a replay.

- `tests/test_sync_middleware_team.py` (new):
  - `test_ensure_team_synced_ttl_cache` — first call hits backend; second call within 30 s does not; third call after TTL hits again. Use a stub clock.
  - `test_ensure_team_synced_no_backend_is_noop` — solo-mode ledger; `ensure_team_synced` returns without error and without calling anything.
  - `test_flush_team_writes_swallows_backend_errors` — backend.push raises; `flush_team_writes` does not propagate (logs at DEBUG).

## Phase 2: GoogleDriveAdapter + OAuth flow

### Affected Files

- `events/backends/google_drive.py` (new) — `GoogleDriveAdapter` implementing `BackendAdapter` against the Drive Files API
- `requirements.txt` — add `google-auth-oauthlib>=1.2`, `google-api-python-client>=2.100`
- `pyproject.toml` — same additions under `[project.dependencies]` (or whichever group `requirements.txt` mirrors)

#### OAuth client provisioning (security alignment)

The Bicameral repo is public. We do NOT bundle a default OAuth client_id/client_secret in source. Two configuration paths, in priority order:

1. **Operator-supplied via env** — `BICAMERAL_GDRIVE_CLIENT_ID` and `BICAMERAL_GDRIVE_CLIENT_SECRET`. Highest priority. Suitable for CI / scripted setup.
2. **Operator-supplied via file** — `~/.bicameral/google-drive-client.json` (the JSON client config exported from Google Cloud Console for an "OAuth client ID" of type "Desktop app"). The setup wizard documents how to obtain this file in `docs/team-mode-setup.md` (3 minutes: create GCP project → enable Drive API → create OAuth consent screen → download credentials JSON).

If neither is present, `GoogleDriveAdapter._credentials()` raises `MissingOAuthClientError` with the exact remediation text. There is NO bundled default — the operator must explicitly provision a client. This aligns with the existing `signer_email_fallback` policy in `events/writer.py` (privacy-positive defaults; explicit opt-in for anything that emits identity to a remote system).

### Changes

**`events/backends/google_drive.py`**:

- Constructor: `(folder_id: str, author: str, token_path: Path = ~/.bicameral/google-drive-token.json)`.
- `_credentials()` — load cached token from `token_path`, refresh on expiry. If no cached token, resolve OAuth client per the provisioning rules above (env first, then `~/.bicameral/google-drive-client.json`, else raise `MissingOAuthClientError`); then `InstalledAppFlow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/drive.file"]).run_local_server(port=0)`. The narrowest-possible scope (`drive.file`) limits Bicameral's access to files it created/opened — the operator's other Drive content stays invisible.
- `_files_service()` — cached `googleapiclient.discovery.build("drive", "v3", credentials=...)`.
- `push_events(local_path, remote_name)`:
  - Query: `files().list(q=f"'{folder_id}' in parents and name='{remote_name}'", fields="files(id, md5Checksum)")`
  - If found and `md5Checksum == md5(local_path.read_bytes())`: return (no-op).
  - If found but hash differs: `files().update(fileId=..., media_body=MediaFileUpload(local_path))`.
  - If not found: `files().create(body={"name": remote_name, "parents": [folder_id]}, media_body=MediaFileUpload(local_path))`.
- `pull_events(local_dir, since_token)`:
  - List with optional `q=f"... and modifiedTime > '{since_token}'"` when token is present.
  - For each remote file whose name is `*.jsonl` and `!= f"{author}.jsonl"`: download via `files().get_media(fileId=...).execute()`; write to `local_dir / name` only if md5 differs from local.
  - Return the most recent `modifiedTime` seen as the new since_token.
- `lock(remote_name)`: async context manager that `create`s `<remote_name>.lock` (best-effort; if create fails because file exists, retry-on-conflict semantics — caller decides). Releases by `delete()` on exit.
- `list_peers()`: list folder, yield `name.removesuffix(".jsonl")` for `*.jsonl` files.

### Unit Tests

- `tests/test_backends_google_drive_unit.py` (new — uses `unittest.mock` to stub the Drive client; no network):
  - `test_push_skips_when_md5_matches` — stub `files().list()` to return `[{"id": "x", "md5Checksum": EXPECTED}]`; push; assert `files().update` and `files().create` were never called.
  - `test_push_updates_when_md5_differs` — stub list to return mismatching md5; assert `files().update` called once with the right `fileId`.
  - `test_push_creates_when_remote_missing` — stub list to return `[]`; assert `files().create` called once.
  - `test_pull_writes_only_changed_peer_files` — stub list to return three peer files (one matches local md5, two differ); assert only two downloads occurred and own-file was skipped.
  - `test_pull_returns_max_modified_time_as_token` — stub list with three files of different `modifiedTime`; assert returned token equals the max.
  - `test_lock_create_then_delete` — assert lock entry creates `<name>.lock`, releases by deleting it; raised exception inside the `async with` still triggers delete.

- `tests/test_backends_google_drive_integration.py` (new — `pytest.mark.integration`, gated on `BICAMERAL_GDRIVE_TEST_FOLDER` + `BICAMERAL_GDRIVE_TEST_TOKEN` env vars; skipped in CI by default):
  - `test_round_trip_against_real_folder` — push a 5-line JSONL; pull it back from a different `local_dir`; assert byte-identical.

## Phase 3: Setup wizard prompt + docs

### Affected Files

- `setup_wizard.py` — extend the existing team-mode branch with a Create-vs-Join wizard for shared ledgers. Adds `_select_team_backend`, `_create_shared_ledger_drive`, `_join_shared_ledger_drive`, and `_select_local_folder_backend` helpers. Writes `team.backend`, `team.folder_id` (or `team.remote_root`), `team.role` (`founding_member` | `member`) to `.bicameral/config.yaml`.
- `docs/team-mode-setup.md` (new) — operator-facing how-to (Create vs Join, OAuth client provisioning, security model)
- `README.md` — under "What `setup` writes", add a bullet describing the new `team.backend` / `team.folder_id` / `team.role` keys

### UX flow (the contract)

After the operator selects "Team" in `_select_collaboration_mode`, the wizard branches:

```
Team mode selected
    │
    ▼
"How do you want to set up the shared ledger?"
    ├── Create new shared ledger    → Create branch
    ├── Join existing shared ledger → Join branch
    └── Use a shared filesystem instead (advanced) → LocalFolder branch
```

#### Create branch (founding member)

1. "Where will the shared ledger live?" → currently only `Google Drive` (single-option list, future-proof).
2. **OAuth client check** — verify `BICAMERAL_GDRIVE_CLIENT_ID/SECRET` env OR `~/.bicameral/google-drive-client.json` is present. If not, surface the 3-minute GCP setup blurb with link to `docs/team-mode-setup.md` §"Provision an OAuth client" and exit (operator re-runs setup after).
3. **OAuth flow** — `GoogleDriveAdapter._credentials()` runs the local-loopback flow with scope `drive.file`. Token cached at `~/.bicameral/google-drive-token.json` (mode 0600).
4. **Folder creation** — call Drive API `files().create(body={"name": f"bicameral-{repo_basename}-ledger", "mimeType": "application/vnd.google-apps.folder"})`. Capture the new folder ID.
5. **Sharing instructions** — print the folder URL (`https://drive.google.com/drive/folders/<id>`) and the literal text the founding member should send teammates: "Share this folder with your teammates as Editor. They run `bicameral setup`, choose Join, and paste the folder ID: `<id>`." Do NOT auto-share — the founding member's Google account governs ACL.
6. **Persist** — write to `.bicameral/config.yaml`:
   ```yaml
   mode: team
   team:
     backend: google_drive
     folder_id: <id>
     role: founding_member
   ```

#### Join branch (subsequent member)

1. "Paste the shared ledger folder ID (your teammate sent this to you):" → `prompt_text`. Accept either a raw ID or a full Drive URL (extract the ID via regex).
2. **OAuth client check** — same as Create.
3. **OAuth flow** — same as Create.
4. **Verify access** — call `files().get(fileId=folder_id, fields="id,name,capabilities")`. If 404, error with "Folder not found — check the ID, or ask the founding member to share it with your Google account." If `capabilities.canEdit == False`, error with "You have read-only access — ask the founding member to grant Editor." Both are blocking; do not persist on failure.
5. **Identity check** — read git `user.email` for the repo. Print: "You'll appear in the team ledger as `<resolved-signer>` (per your `signer_email_fallback: <mode>` policy in events/writer.py). Continue? [y/N]". Default no — operator must affirm. This makes the privacy posture an explicit decision at join time rather than a silent ledger emission.
6. **Persist**:
   ```yaml
   mode: team
   team:
     backend: google_drive
     folder_id: <id>
     role: member
   ```

#### LocalFolder branch (advanced)

Single prompt: "Path to a shared folder mounted on every teammate's machine (NFS, Dropbox, syncthing, ...):". Validate the path exists and is writable. Persist:
```yaml
mode: team
team:
  backend: local_folder
  remote_root: <path>
  role: member
```

(No Create/Join distinction here — file-system ACLs handle it; whoever can write to the folder is in the team.)

### Changes

```python
def _select_team_backend(repo_path: str) -> dict:
    """Top-level Create vs Join vs LocalFolder dispatch. Returns team config dict."""
    intent = prompt_choice(
        "How do you want to set up the shared ledger?",
        choices=[
            ("create",       "Create a new shared ledger (you become the founding member)"),
            ("join",         "Join an existing shared ledger (paste a folder ID from a teammate)"),
            ("local_folder", "Use a shared filesystem instead (NFS, Dropbox, syncthing) — advanced"),
        ],
        default="create",
    )
    if intent == "create":
        return _create_shared_ledger_drive(repo_path)
    if intent == "join":
        return _join_shared_ledger_drive(repo_path)
    return _select_local_folder_backend()

def _create_shared_ledger_drive(repo_path: str) -> dict:
    _check_oauth_client_or_exit()
    from events.backends.google_drive import GoogleDriveAdapter
    adapter = GoogleDriveAdapter(folder_id=None, author=_resolved_signer(repo_path))
    adapter._credentials()  # runs OAuth, caches token
    repo_name = Path(repo_path).resolve().name
    folder_id = adapter.create_folder(name=f"bicameral-{repo_name}-ledger")
    print(_share_instructions(folder_id))  # prints the URL + paste-this-to-teammates text
    return {"backend": "google_drive", "folder_id": folder_id, "role": "founding_member"}

def _join_shared_ledger_drive(repo_path: str) -> dict:
    raw = prompt_text("Paste the shared ledger folder ID (or full Drive URL):")
    folder_id = _extract_folder_id(raw)  # accepts either form
    _check_oauth_client_or_exit()
    from events.backends.google_drive import GoogleDriveAdapter
    adapter = GoogleDriveAdapter(folder_id=folder_id, author=_resolved_signer(repo_path))
    adapter._credentials()
    adapter.verify_access()  # raises FolderNotFoundError or ReadOnlyAccessError
    signer = _resolved_signer(repo_path)
    if not prompt_yes_no(
        f"You'll appear in the team ledger as `{signer}`. Continue?", default=False
    ):
        sys.exit("Aborted — adjust signer_email_fallback in your existing config and re-run.")
    return {"backend": "google_drive", "folder_id": folder_id, "role": "member"}

def _select_local_folder_backend() -> dict:
    path = prompt_text("Path to the shared folder (must exist on every teammate's machine):")
    p = Path(path).expanduser().resolve()
    if not p.exists() or not os.access(p, os.W_OK):
        sys.exit(f"Path not writable: {p}")
    return {"backend": "local_folder", "remote_root": str(p), "role": "member"}
```

`GoogleDriveAdapter` gains two helpers (added to Phase 2 scope as a follow-on):
- `create_folder(name) -> str` — returns the new folder ID.
- `verify_access() -> None` — raises `FolderNotFoundError` (404) or `ReadOnlyAccessError` (`canEdit == False`).

### Documentation (`docs/team-mode-setup.md`)

Sections in order:

1. **What is team mode** — solo vs team in one paragraph; what "shared ledger" means.
2. **Create vs Join** — table: when each applies; what the founding member is responsible for (folder ACL, OAuth client provisioning).
3. **Provision an OAuth client (3 minutes)** — step-by-step GCP screenshots-equivalent prose: create project, enable Drive API, create OAuth consent screen (External, test users), create credentials (Desktop app), download JSON, save as `~/.bicameral/google-drive-client.json` OR export as env vars. Why we don't bundle: "Bicameral is open source; bundling our own OAuth client lets anyone publish a 'Bicameral' consent screen and harvest scopes."
4. **Run setup — Create flow** — terminal transcript walkthrough.
5. **Run setup — Join flow** — terminal transcript walkthrough; emphasize the identity confirmation step.
6. **Verifying replication** — operator A ingests a decision; operator B runs `bicameral.history` within 60 s; should appear.
7. **Permissions and revocation** — Drive Editor required to write; revoking Editor stops new pushes immediately, but past events remain in peers' local DBs (event log is append-only).
8. **Privacy posture** — explain `signer_email_fallback`, what the JSONL author field carries, where the OAuth token lives (`~/.bicameral/google-drive-token.json`, mode 0600).
9. **Local-folder backend** — when to use it (all-on-NFS shops); single section because the wizard is one prompt.
10. **Troubleshooting** — common failures: 404 on Join (sharing not propagated), OAuth refresh failures (token deleted; re-run setup), folder ID format mismatch.

### Unit Tests

- `tests/test_setup_wizard_team_backend.py` (new):
  - `test_create_branch_persists_founding_member_role` — stub `_check_oauth_client_or_exit`, stub `GoogleDriveAdapter._credentials` and `.create_folder` to return `"abc123"`; drive wizard with `intent="create"`; assert config.yaml has `team.backend: google_drive`, `team.folder_id: abc123`, `team.role: founding_member`.
  - `test_join_branch_verifies_access_before_persist` — stub `verify_access` to raise `FolderNotFoundError`; assert wizard exits non-zero AND no config.yaml is written.
  - `test_join_branch_extracts_folder_id_from_url` — pass `https://drive.google.com/drive/folders/xyz789?usp=sharing` to the prompt stub; assert persisted `folder_id == "xyz789"`.
  - `test_join_branch_aborts_on_identity_decline` — stub identity confirmation prompt to return False; assert SystemExit and no config write.
  - `test_local_folder_branch_rejects_unwritable_path` — pass a path the test process cannot write to (e.g. `/`); assert SystemExit with the path in the message.
  - `test_oauth_client_missing_blocks_create_and_join` — env unset, no `~/.bicameral/google-drive-client.json`; both Create and Join branches surface the remediation message and exit cleanly (no partial config write).

- `tests/test_google_drive_adapter_helpers.py` (new — Phase 3 additions to the Phase 2 adapter):
  - `test_create_folder_returns_id` — stub `files().create()` to return `{"id": "new123"}`; assert `create_folder("bicameral-foo-ledger") == "new123"`.
  - `test_verify_access_raises_on_404` — stub `files().get()` to raise `HttpError(404)`; assert `FolderNotFoundError` raised with the folder ID in the message.
  - `test_verify_access_raises_on_read_only` — stub to return `{"capabilities": {"canEdit": False}}`; assert `ReadOnlyAccessError`.
  - `test_verify_access_passes_when_can_edit` — stub to return `{"capabilities": {"canEdit": True}}`; assert no exception.

- (No new tests for `docs/team-mode-setup.md` or `README.md` — content review only.)

## CI Commands

- `cd pilot/mcp && pytest tests/test_backends_local_folder.py tests/test_team_adapter_with_backend.py tests/test_team_round_trip_local_folder.py tests/test_sync_middleware_team.py -v` — Phase 1 unit + integration (no network)
- `cd pilot/mcp && pytest tests/test_backends_google_drive_unit.py -v` — Phase 2 unit (mocked Drive client)
- `cd pilot/mcp && pytest tests/test_backends_google_drive_integration.py -v -m integration` — Phase 2 integration (gated on env vars; skipped in CI by default)
- `cd pilot/mcp && pytest tests/test_setup_wizard_team_backend.py tests/test_google_drive_adapter_helpers.py -v` — Phase 3 wizard + adapter helpers
- `cd pilot/mcp && ruff check events/ adapters/ handlers/sync_middleware.py setup_wizard.py` — lint matches CI
- `cd pilot/mcp && pytest tests/ -k 'team or events or sync_middleware'` — regression sweep across the touched surface
