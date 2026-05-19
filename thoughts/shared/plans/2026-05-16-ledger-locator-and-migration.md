# Plan: Ledger Locator + state migration to `~/.bicameral/projects/<id>/` (#368)

**change_class**: feature

**doc_tier**: system

**terms_introduced**:
- term: ledger-locator
  home: docs/architecture/ledger-locator.md
- term: project-id
  home: docs/architecture/ledger-locator.md
- term: state-migration
  home: docs/architecture/ledger-locator.md

**boundaries**:
- limitations:
  - Single-user, single-machine layout. No multi-user shared-runner support.
  - `git` must be on `$PATH` (the locator keys off `git rev-parse --git-common-dir`).
- non_goals:
  - Per-branch DBs (rejected in the #368 RFC; same reasoning).
  - Daemon-based ownership of the ledger file — tracked separately in the sibling daemon plan (`2026-05-16-bicameral-daemon.md` for #372).
  - Auto-rebuild of `code-graph.db` after migration. The existing `python -m code_locator index <repo>` flow remains the (re)builder.
- exclusions:
  - CI / Docker / shared-runner auto-detection (operators set env vars explicitly).
  - `--ledger-location=repo` opt-out flag.
  - Any change to what gets stored in either file.

## Why this ships first

The locator decides *where* both the ledger and the code-graph live. The daemon plan binds to whatever path the locator resolves. If the daemon ships first, it binds to the legacy `<repo>/.bicameral/` path and the locator change becomes a destructive layout migration on a running daemon — strictly harder. Sequencing locator → daemon means the daemon plan can assume the layout is already settled and existing users have already migrated.

Target release: **v0.16.0** (locator + migration). The daemon plan targets **v0.16.0** as well, but cuts a separate PR against `dev` that lands after this one.

## Audit history

| Revision | Audit | Verdict | Gate artifact |
|---|---|---|---|
| 1 (original) | 2026-05-17 (solo) | **VETO** — V1 `specification-drift` (None default crashes `resolve_paths`), V2 `infrastructure-mismatch` (single-consumer model misses 15 read sites), V3 `infrastructure-mismatch` (cited `setup_wizard.py:366` actually `:472`) | `.qor/gates/2026-05-17T0605-a24ab8/audit.json` |
| 2 | 2026-05-17 (solo) | **PASS** — V1/V2/V3 cleared; advisories on `origin.txt` term + `gc` jargon left open | `.qor/gates/2026-05-17T0605-a24ab8/audit.json` (R2 PASS overwrote R1) |
| 3 | pending | — | (audit skipped — superseded by R4) |
| 4 | 2026-05-18 (solo) | **VETO** — V1 `infrastructure-mismatch` (`_edit_config_interactive` actual name `run_config_wizard`), V2 `infrastructure-mismatch` (context.py reader lines stale + bogus `412-429`), V3 `coverage-gap` (missing solo-short-circuit + `run_config_wizard` editor tests) | `.qor/gates/2026-05-18T2334-r4audit/audit.json` |
| 4-bis (this revision) | pending | — | — |

**R1 V1 + V2 resolution**: Phase 2 now adopts V1(b) + V2(b) combined — `load_config()` substitutes the locator-resolved path on construction; `resolve_paths()` is None-safe as a belt-and-braces guard for direct-construction callers. Four new unit tests (`test_code_locator_config_none_safe.py`) pin the contract.

**R1 V3 resolution**: `setup_wizard.py:366` → `setup_wizard.py:472`.

**R3 scope expansion — worktree completeness**: R2's PASS satisfied the literal #368 acceptance criterion (ledger + code-graph share across worktrees), but missed three file classes that #368's value proposition (*"Multiple working trees of one project share... same decisions, same symbol graph"*) actually requires:
- `.bicameral/local/bm25_index.pkl` — derived from `code-graph.db`, must travel with it. One consumer at `code_locator_runtime.py:277`.
- `.bicameral/local/watermark` — `events/materializer.py:24` reads `local_dir/"watermark"`. Per-worktree today → peers' events re-replayed N times → duplicate `input_span` rows under multi-worktree usage.
- `.bicameral/pending-transcripts/` + `processed-transcripts/` — `events/transcript_queue.py:18-27` declares the layout under `<repo>/.bicameral/`. Per-worktree today → transcript ingested in worktree A is invisible to worktree B's drain loop.
- `.bicameral/config.yaml` — committed to git, but worktrees on different branches diverge silently. **R4 supersedes R3's read-fix here** (see R4 expansion below). R3 had proposed `resolve_config_path()` to read from primary worktree; R4 deletes that approach.

These are added to Phase 2 (locator API + consumer wiring) and Phase 3 (migrate-state file inventory).

**R4 expansion — config split + git-native worktree handling**: R3's `resolve_config_path()` was a filesystem-topology inference ("look in primary worktree's directory") that special-cased `git worktree add` and silently misclassified submodules, bare-repo deployments, sparse checkouts, dev containers, Codespaces, and ephemeral CI runners. Captured in detail at the [Topology Problem Notion page](https://www.notion.so/3642a51619c481149836ef883ae62489). The R3 choice was deliberate per its own text, but the audit was pending and the choice was challenged this session. The R4 amendments:

1. **DELETE `resolve_config_path()` from the locator module** (decision:ew9rgegdlblexsraesss — ratified 2026-05-18). Runtime readers in `context.py` revert to direct `Path(repo_path) / ".bicameral" / "config.yaml"` access (the v0.15.x baseline). Wizard onboarding detection uses `git show HEAD:.bicameral/config.yaml`. Divergence guard uses `git show <default-branch>:.bicameral/config.yaml`. No filesystem-topology inference anywhere — concept ports cleanly across all 9 deployment shapes the Topology Problem catalogs.
2. **Config split** (decision:5nr66wvmapjpt58rrji8): team-identity keys (`mode`, `team.backend`, `team.folder_id`/`remote_root`, `ingest_max_bytes`, optional `signer_email_fallback_min_strength`) stay at `<repo>/.bicameral/config.yaml` (git-committed). Per-operator keys (`telemetry`, `channel`, `guided`, `signer_email_fallback`, `render_source_attribution`, `team.author`, `team.role`, rate-limit knobs, query timeouts) move to `~/.bicameral/projects/<id>/operator.yaml` (per-machine). `setup_wizard._write_collaboration_config` splits into two atomic writes (temp-file + rename); `context.py` readers route per-key.
3. **Explicit VCS contract** (decision:6c20xahdyxk3suzav4pj): surface a structured `ProjectIdResolutionError` from `ledger_locator/_project_id.py::common_dir_for` when `git rev-parse --git-common-dir` fails. Error message names the assumption: `"bicameral currently supports git only; non-git VCSes are not yet implemented"`. Forces future port to jj/sapling to be a deliberate amendment.
4. **Reuse `_resolve_authoritative_ref()`** (decision:ogdfx014sqgc6fi6ky1a): wizard's default-branch divergence guard delegates to `setup_wizard.py:325::_resolve_authoritative_ref()`. Do not reinvent the `origin/HEAD → user-prompt` fallback ladder.
5. **Defer ephemeral environments** (decision:e3xz4c4ji4x7lm3lvq4k): R4 only adds a one-line notice in `migrate-state` CLI post-flight summary naming the issue. Full ephemeral-environment support (BICAMERAL_STATE_ROOT, --ephemeral=no-op, rebuild-from-team CLI) is a subsequent patch (v0.16.1 / v0.17).

**R4 specification-drift retraction**: R3 line 127 said "the asymmetry [between worktree-local writes and primary-worktree reads] is deliberate." That sentence is **retracted** by R4 — there is no asymmetry under R4 because there is no primary-worktree resolution. The "deliberate" framing was a one-line shortcut described as a tradeoff; R4 picks a layer where the question doesn't arise.

## Open questions

1. **`bicameral-mcp update` migration trigger.** v0.16 changes the canonical state layout. Anyone running `bicameral-mcp update` from v0.15.x must have their existing `<repo>/.bicameral/ledger.db` and `<repo>/.bicameral/local/code-graph.db` moved before the new binary tries to open them at the new path. **Resolution**: the `update` flow (an existing skill — see `.claude/skills/bicameral-update/`) runs `bicameral-mcp migrate-state --auto` after package upgrade and before the next MCP boot. `--auto` is non-interactive, idempotent, and archives-on-collision (defined below). Operators who upgrade outside the skill see a one-time first-boot warning surfacing the same command.

2. **What happens to legacy `<repo>/.bicameral/` after migration.** The `ledger.db`, `local/code-graph.db{,-shm,-wal}`, derived state (bm25, watermark, transcript queues), and per-operator config (R4 adds `operator.yaml` to the home-side) all move out; the rest of `<repo>/.bicameral/` (the trimmed `config.yaml` with only team-identity keys, events/, hooks/) stays where it is — it's repo-scoped state, not user-local cache. The migration deletes only the moved files, leaves `<repo>/.bicameral/local/` if empty, removes it if it's empty.

3. **Project-id collision.** `sha256(git rev-parse --git-common-dir)[:16]` is collision-resistant in practice but not by guarantee. **Resolution**: on first `resolve_ledger_url()` for a project, write `~/.bicameral/projects/<id>/origin.txt` containing the absolute git-common-dir path. On subsequent resolves, read that file and refuse to proceed if it disagrees (configurable via `BICAMERAL_LOCATOR_ALLOW_COLLISION=1` — surfaces the offending path in the error).

4. **Memory-mode (`SURREAL_URL=memory://`) interaction.** Memory mode skips the locator entirely for the ledger URL (env-var wins). For the code-graph file there is no memory equivalent; tests must point `CODE_LOCATOR_SQLITE_DB` at `tmp_path` explicitly. The locator never silently writes test artifacts into `~/.bicameral/projects/` — tests that omit both env vars get a temp-dir locator fixture from `conftest.py`.

5. **(R4 resolved 2026-05-18)** ~~How does the wizard detect that team mode is already configured when a teammate clones the repo?~~ → `git show HEAD:.bicameral/config.yaml`. Works uniformly across all 9 deployment shapes (worktree, submodule, bare-repo, sparse checkout, devcontainer, Codespaces, CI runner, `--separate-git-dir`, non-git VCS error). See decision:ew9rgegdlblexsraesss.

6. **(R4 resolved 2026-05-18)** ~~Should the git-only VCS assumption be implicit (accidental success on misclassified VCSes) or explicit?~~ → Explicit. Structured error from `ledger_locator/_project_id.py::common_dir_for`. See decision:6c20xahdyxk3suzav4pj.

7. **(R4 resolved 2026-05-18)** ~~How does the wizard pick the default branch for the divergence guard?~~ → Reuse `_resolve_authoritative_ref()` at `setup_wizard.py:325`. See decision:ogdfx014sqgc6fi6ky1a.

8. **(R4 deferred 2026-05-18)** Codespaces / devcontainer / CI persistence: `~/.bicameral/projects/<id>/` evaporates at container teardown. **R4 placeholder**: one-line notice in `migrate-state` post-flight. **Full scope** (BICAMERAL_STATE_ROOT, --ephemeral=no-op, rebuild-from-team CLI, ephemeral-signal detection) is a subsequent patch (v0.16.1 / v0.17). See decision:e3xz4c4ji4x7lm3lvq4k.

## Phase 1: `ledger_locator/` module

The deterministic resolver. No I/O until you call a `resolve_*` function. No network, no LLM, no git mutations.

### Affected files

- `ledger_locator/__init__.py` (new) — public API: `resolve_ledger_url()`, `resolve_code_graph_path()`, `project_id_for(repo_path)`, `project_dir_for(repo_path)`.
- `ledger_locator/_project_id.py` (new) — wrap `git rev-parse --git-common-dir`, hash, return 16-char hex.
- `ledger_locator/_origin_guard.py` (new) — read/write/verify `<project_dir>/origin.txt`.

### Unit tests (write first)

- `tests/test_ledger_locator.py::test_default_resolves_under_home_bicameral_projects` — in a real git repo (`tmp_path` + `git init`), `resolve_ledger_url()` returns `surrealkv://<home>/.bicameral/projects/<16hex>/ledger.db`; `resolve_code_graph_path()` returns the sibling `code-graph.db`.
- `tests/test_ledger_locator.py::test_resolves_derived_state_paths_under_project_dir` — same fixture; assert `resolve_bm25_index_path()`, `resolve_watermark_path()`, `resolve_pending_transcripts_dir()`, `resolve_processed_transcripts_dir()` all share the same `<16hex>` parent dir as `resolve_code_graph_path()` (one project, one bag of state).
- `tests/test_ledger_locator.py::test_resolve_operator_config_path` (**R4 replacement** for the dropped `test_resolve_config_path_uses_primary_worktree`) — assert `resolve_operator_config_path()` returns `project_dir_for(repo) / "operator.yaml"` (per-operator config under the project state dir). No worktree-divergence test — config.yaml is now read as a normal tracked file via `<cwd>/.bicameral/config.yaml`; per-worktree divergence is expected/handled by git, not the locator.
- `tests/test_ledger_locator.py::test_env_override_wins_for_ledger_only` — `SURREAL_URL=memory://`; `resolve_ledger_url()` returns `memory://` and `resolve_code_graph_path()` still returns the on-disk default. Each env var is independent.
- `tests/test_ledger_locator.py::test_env_override_wins_for_code_graph_only` — `CODE_LOCATOR_SQLITE_DB=/tmp/x.db`; `resolve_code_graph_path()` returns `/tmp/x.db`; `resolve_ledger_url()` returns the default.
- `tests/test_ledger_locator.py::test_two_worktrees_resolve_to_same_id` — create a primary git repo + `git worktree add` a second working tree; assert `project_id_for(primary) == project_id_for(secondary)`.
- `tests/test_ledger_locator.py::test_separate_clones_resolve_to_different_ids` — two `git init` repos in disjoint dirs produce different project IDs (the inputs to the hash differ).
- `tests/test_ledger_locator.py::test_non_git_directory_raises_actionable_error` — `resolve_ledger_url()` from a non-git directory raises `ProjectIdResolutionError` whose message names the missing `.git/` and points at the env-var override.
- `tests/test_ledger_locator_origin_guard.py::test_first_resolve_writes_origin_txt` — `resolve_ledger_url()` from a fresh project; assert `<project_dir>/origin.txt` exists and contains the absolute common-dir path.
- `tests/test_ledger_locator_origin_guard.py::test_collision_with_different_origin_raises` — manually write a project dir's `origin.txt` pointing at `/somewhere/else`; `resolve_ledger_url()` raises `ProjectIdCollisionError` whose message includes both the on-disk origin and the current one.
- `tests/test_ledger_locator_origin_guard.py::test_collision_override_logs_and_proceeds` — same setup, `BICAMERAL_LOCATOR_ALLOW_COLLISION=1`; the call succeeds and emits a single WARN with both paths in the message.

### Implementation files

- `ledger_locator/_project_id.py`:
  - `def common_dir_for(repo_path: Path) -> Path`: `subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo_path)`; raises `ProjectIdResolutionError` on non-zero exit. **R4 — explicit VCS contract** (decision:6c20xahdyxk3suzav4pj): error message names the assumption verbatim: `"bicameral currently supports git only; non-git VCSes are not yet implemented. To use bicameral with this repo, run from inside a git working tree."` Force future ports to jj/sapling/fossil to be a deliberate locator amendment rather than an accidental success on a misclassified VCS.
  - `def project_id_for(repo_path: Path) -> str`: `hashlib.sha256(str(common_dir_for(repo_path).resolve()).encode()).hexdigest()[:16]`.

- `ledger_locator/_origin_guard.py`:
  - `def assert_origin(project_dir: Path, common_dir: Path) -> None`: on first call, writes `origin.txt`. On subsequent calls, compares and raises `ProjectIdCollisionError` unless `BICAMERAL_LOCATOR_ALLOW_COLLISION=1`.

- `ledger_locator/__init__.py`:
  - `STATE_ROOT = Path.home() / ".bicameral" / "projects"`.
  - `def project_dir_for(repo_path: Path | None = None) -> Path`: defaults `repo_path` to `Path.cwd()`; calls the helpers above.
  - `def resolve_ledger_url(repo_path: Path | None = None) -> str`: env `SURREAL_URL` wins; else `f"surrealkv://{project_dir_for(repo_path) / 'ledger.db'}"`.
  - `def resolve_code_graph_path(repo_path: Path | None = None) -> Path`: env `CODE_LOCATOR_SQLITE_DB` wins; else `project_dir_for(repo_path) / "code-graph.db"`.
  - **R3 additions** (worktree-shared derived state, project-scoped):
    - `def resolve_bm25_index_path(repo_path: Path | None = None) -> Path`: `project_dir_for(repo_path) / "bm25_index.pkl"`. Sibling to the code-graph SQLite — they are a unit.
    - `def resolve_watermark_path(repo_path: Path | None = None) -> Path`: `project_dir_for(repo_path) / "watermark"`. Replaces `events/materializer.py`'s `local_dir / "watermark"`.
    - `def resolve_pending_transcripts_dir(repo_path: Path | None = None) -> Path`: `project_dir_for(repo_path) / "pending-transcripts"`. Replaces `events/transcript_queue.py:_pending_root`.
    - `def resolve_processed_transcripts_dir(repo_path: Path | None = None) -> Path`: `project_dir_for(repo_path) / "processed-transcripts"`. Sibling.
  - **R4 addition** (per-operator config, project-scoped, per-machine):
    - `def resolve_operator_config_path(repo_path: Path | None = None) -> Path`: `project_dir_for(repo_path) / "operator.yaml"`. Per-operator settings live under the project state dir; shared across worktrees on the same machine; not committed to git. **Decision**: decision:5nr66wvmapjpt58rrji8.
  - **R4 retraction** (was R3 addition): `resolve_config_path()` is **NOT** added. R3 had proposed `Path(common_dir_for(repo_path)).parent / ".bicameral" / "config.yaml"` for primary-worktree convergence; R4 deletes this approach entirely. `<repo>/.bicameral/config.yaml` is read as a normal tracked file by callers via `Path(repo_path) / ".bicameral" / "config.yaml"` (no locator function). Per the [Topology Problem](https://www.notion.so/3642a51619c481149836ef883ae62489) analysis — filesystem-topology inference special-cases `git worktree add` and silently misclassifies 8 other deployment shapes (submodules, bare-repo, Codespaces, etc.). Decision: decision:ew9rgegdlblexsraesss; superseded R3 design at decision:6z39wrjpmmg9vhm8i6t4 (rejected).

## Phase 2: Delegate every call site to the locator

Today the path is computed inline in four places. After this phase, the locator is the only source.

### Affected files

- `ledger/adapter.py:158-161` (`_default_db_url`) — delegate to `ledger_locator.resolve_ledger_url()`. Remove the local `Path.home() / ".bicameral" / "ledger.db"` literal.
- `code_locator_runtime.py:48` — `os.environ.setdefault("CODE_LOCATOR_SQLITE_DB", str(ledger_locator.resolve_code_graph_path()))`. The runtime stops computing a path itself.
- `code_locator_runtime.py:277` (`bm25_path = Path(config.sqlite_db).parent / "bm25_index.pkl"`) — delegate to `ledger_locator.resolve_bm25_index_path()`. Removes the implicit "bm25 lives next to sqlite_db" coupling; both paths come from the locator independently.
- `code_locator/config.py:17` — drop the `~/.bicameral/code-graph.db` literal default; the field type becomes `str | None` with default `None`. **`load_config()` (line 70) is responsible for substituting the locator-resolved path before construction returns**; see Implementation files below. This means every existing reader of `config.sqlite_db` (enumerated below) sees a resolved path without needing to change.
- `code_locator/config.py:36-39` (`resolve_paths`) — None-safe wrap. When `sqlite_db is None`, fall through to `ledger_locator.resolve_code_graph_path()` before `Path(...).expanduser()`. Belt-and-braces against any caller that builds `CodeLocatorConfig(...)` directly instead of through `load_config()`.
- `events/materializer.py:24` (`self._watermark_path = local_dir / "watermark"`) — replace with `self._watermark_path = ledger_locator.resolve_watermark_path()`. Drop the `local_dir` parameter from `EventMaterializer.__init__`; callers stop computing watermark paths.
- `events/transcript_queue.py:18-27` (`PENDING_DIR`, `_pending_root`, `_processed_root`) — replace `Path(repo_path) / ".bicameral" / PENDING_DIR` with `ledger_locator.resolve_pending_transcripts_dir(repo_path)`. Same for processed.
- `scripts/hooks/transcript_archive.py` (SessionEnd hook, runs out-of-process) — replace the `<cwd>/.bicameral/pending-transcripts/` literal with `ledger_locator.resolve_pending_transcripts_dir(Path.cwd())`. The hook is already installed as a console script (`pyproject.toml:80`), so importing `ledger_locator` is safe.
- `context.py` — **10 readers** that hardcode `Path(repo_path) / ".bicameral" / "config.yaml"` (R4 V2 correction: R3 cited "9 readers" with stale line numbers and a bogus `412-429` claim — that range is a `BicameralContext` dataclass field block, not a reader). Actual readers at current `dev` HEAD: `_read_yaml_string_field:66`, `_read_signer_email_fallback:86`, `_read_render_source_attribution:99`, `_read_ingest_max_bytes:164`, `_read_ingest_rate_limit_burst:189`, `_read_ingest_rate_limit_refill_per_sec:213`, `_read_query_timeout_seconds:252` (generic helper), `_read_query_timeout_read_seconds:295` (wraps generic), `_read_query_timeout_drift_seconds:307` (wraps generic), `_read_guided_mode:319`. **R4 supersedes R3 here**: instead of delegating to `ledger_locator.resolve_config_path()` (R3 design), each reader is routed per-key based on R4 config split (decision:5nr66wvmapjpt58rrji8). Team-identity keys stay direct on `<repo>/.bicameral/config.yaml`. Per-operator keys (`signer_email_fallback`, `render_source_attribution`, `guided`, rate-limit knobs, query timeouts) read from `ledger_locator.resolve_operator_config_path(repo_path)`. Per-key routing table is the single source of truth — encoded in a new `context._CONFIG_KEY_ROUTING` constant (key → enum {`team`, `operator`}). Wizard, migrate-state, and context.py all consume the same table.
- `setup_wizard.py:472` — stop writing `SURREAL_URL` for the default case (locator decides). *(Audit V3: corrected from earlier `setup_wizard.py:366` which was lifted from the #368 issue body and predated the current file.)*
- `setup_wizard.py:473` — stop writing `CODE_LOCATOR_SQLITE_DB` for the default case (locator decides).
- `setup_wizard.py:1528` (`_write_collaboration_config`) — **R4 split**: function now writes BOTH `<repo>/.bicameral/config.yaml` (team-identity keys via atomic temp-file + rename) AND `~/.bicameral/projects/<id>/operator.yaml` (per-operator keys via atomic temp-file + rename). If either temp-file write fails, both temps are unlinked and the function raises (no torn state). The two files are written in a defined order: operator.yaml first (no git impact), config.yaml second (commit-able). Decision: decision:5nr66wvmapjpt58rrji8.
- `setup_wizard.py:1697` (existing `_detect_linked_worktree` warning) — **R4 trailer**: in addition to the existing hooks-per-worktree note, append a one-line summary after every successful wizard run: `"team config → .bicameral/config.yaml (commit this) | your settings → ~/.bicameral/projects/<id>/operator.yaml (private)"`. Cheap fix for the silent-leak-to-git class of bugs where operators forget what's committed.
- `setup_wizard.run_setup` — **R4 onboarding detection**: before prompting for `_select_collaboration_mode`, call `subprocess.run(["git", "show", "HEAD:.bicameral/config.yaml"], ...)`. If returncode 0 and `mode == "team"`, print `"Detected team config: backend=<...>, folder=<...> ✓ (auto-joining)"` and skip the team-backend wizard. Decision: decision:ew9rgegdlblexsraesss.
- `setup_wizard.run_setup` — **R4 divergence guard**: when `git show HEAD:.bicameral/config.yaml` returns no config but `git show <default-branch>:.bicameral/config.yaml` (via `_resolve_authoritative_ref()`) does, warn: `"Your branch doesn't have .bicameral/config.yaml, but <default-branch> does. Merge first to inherit team config, or continue with fresh setup? [y/N]"`. Decision: decision:ogdfx014sqgc6fi6ky1a — reuses existing `_resolve_authoritative_ref()` ladder.
- `setup_wizard.py:1817` (`run_config_wizard`) — **R4 two-pane editor** (R4 V1 correction: R4-initial misnamed this `_edit_config_interactive`; actual function name at the cited line is `run_config_wizard`). The existing single-pane editor splits into a tagged single-pane (each row carries `[team]` or `[your machine]` prefix) so operators can SEE which choices affect teammates. Editor reads from both files via the per-key routing table; writes back to the appropriate file with the same atomic temp+rename discipline as `_write_collaboration_config`.

**`config.sqlite_db` reader inventory** (grep-verified against current `dev` HEAD; included so the auditor doesn't need to re-walk):

| File | Line | Read |
|---|---|---|
| `adapters/code_locator.py` | 128 | `SymbolDB(config.sqlite_db)` |
| `handlers/link_commit.py` | 567 | `_get_meta(cg_config.sqlite_db, "head_commit")` |
| `code_locator_runtime.py` | 223, 228, 229, 236, 237, 239, 241, 260, 261, 265, 275, 276, 277 | 13 reads, indexing + meta + symbol-count paths |

None of these read sites need to change: they all obtain `config` via `load_config()` (the existing factory pattern), and `load_config()` now substitutes the locator-resolved path before returning. The `resolve_paths` None-safety in `code_locator/config.py:36-39` covers any future direct-construction caller.

### Unit tests (write first)

- `tests/test_ledger_adapter_uses_locator.py::test_default_url_comes_from_locator` — no env vars set; `SurrealDBLedgerAdapter()._url` equals `ledger_locator.resolve_ledger_url()`. (Asserts on the resolver's output rather than a hard-coded path so the test survives layout changes.)
- `tests/test_code_locator_runtime_uses_locator.py::test_runtime_sets_env_from_locator` — call the runtime's init helper in a `tmp_path` git repo with `CODE_LOCATOR_SQLITE_DB` unset; assert `os.environ["CODE_LOCATOR_SQLITE_DB"]` equals `str(ledger_locator.resolve_code_graph_path(...))` for that repo.
- `tests/test_code_locator_runtime_uses_locator.py::test_runtime_respects_existing_env` — `CODE_LOCATOR_SQLITE_DB=/tmp/x.db` pre-set; runtime init leaves it untouched (the locator's own env-priority logic already preserves it).
- `tests/test_setup_wizard_omits_state_env_vars.py::test_build_config_no_surreal_url` — `_build_config(...)` returned dict's `env` does NOT contain `SURREAL_URL`.
- `tests/test_setup_wizard_omits_state_env_vars.py::test_build_config_no_code_locator_sqlite_db` — same dict does NOT contain `CODE_LOCATOR_SQLITE_DB`.
- `tests/test_code_locator_config_none_safe.py::test_load_config_substitutes_locator_path_when_unset` — clear `CODE_LOCATOR_SQLITE_DB` from env; call `load_config()` in a `tmp_path` git repo; assert `config.sqlite_db == str(ledger_locator.resolve_code_graph_path())`. Catches V2-class regressions where a future change removes the `load_config`-level substitution.
- `tests/test_code_locator_config_none_safe.py::test_resolve_paths_handles_none_sqlite_db` — construct `CodeLocatorConfig(sqlite_db=None)` directly (bypass `load_config()`); call `.resolve_paths()`; assert `config.sqlite_db` is a non-None string ending in `code-graph.db`. Catches V1-class regressions where a future caller direct-constructs the dataclass and the None-safe wrap is missing.
- `tests/test_code_locator_config_none_safe.py::test_env_var_still_wins_over_locator` — set `CODE_LOCATOR_SQLITE_DB=/tmp/explicit.db`; call `load_config()`; assert `config.sqlite_db == "/tmp/explicit.db"`. Catches the regression where the locator substitution starts overriding an explicit env override.
- `tests/test_code_locator_config_none_safe.py::test_load_config_resolves_before_consumers_run` — in a `tmp_path` git repo with `CODE_LOCATOR_SQLITE_DB` unset, call `load_config()` and instantiate a `SymbolDB(config.sqlite_db)` against the returned path; assert the underlying SQLite file is created at the locator-resolved location (not at the literal string `"None"`). Direct check that the order-of-init concern V2 raised is resolved.
- `tests/test_two_worktrees_share_state.py::test_decision_visible_across_worktrees` — `git init` repo + `git worktree add`; ingest a decision from worktree A; instantiate a fresh adapter against worktree B; `get_all_decisions()` returns the row. Same physical `ledger.db`. Gated by `pytest.mark.requires_git` (the runner needs a real `git` binary).
- `tests/test_two_worktrees_share_state.py::test_watermark_shared_across_worktrees` — `git init` + `git worktree add`; instantiate `EventMaterializer` against worktree A; replay peer events; instantiate a second `EventMaterializer` from worktree B; assert `_read_offsets()` returns the offsets written by worktree A's replay (proves both worktrees read the same physical `watermark` file under the project dir). Without this fix, B would return `{}` and re-replay everything.
- `tests/test_two_worktrees_share_state.py::test_pending_transcript_visible_across_worktrees` — `git init` + `git worktree add`; `write_pending(worktree_a, session_id="s1", transcript_path=...)`; assert `list_pending_fifo(worktree_b)` returns the file (single project-scoped queue, not per-worktree).
- `tests/test_two_worktrees_share_state.py::test_bm25_index_shared_across_worktrees` — `git init` + `git worktree add`; assert `ledger_locator.resolve_bm25_index_path(worktree_a) == ledger_locator.resolve_bm25_index_path(worktree_b)`. (Functional check; we don't need to actually build a BM25 index for the test — the path-equality contract is what mattered.)
- **R4 drops** `tests/test_config_path_resolution.py` from R3 (worktree-convergence at the locator layer is no longer the design). **R4 adds** the following Phase 2 tests instead:
- `tests/test_config_split.py::test_team_identity_keys_persist_to_config_yaml` — call `_write_collaboration_config(data_path, mode="team", team_backend={...}, telemetry=False)`; assert `<data_path>/.bicameral/config.yaml` contains `mode`, `team.backend`, `team.folder_id` but NOT `telemetry`, `channel`, `guided`. (Per-operator keys absent from the committed file.)
- `tests/test_config_split.py::test_operator_keys_persist_to_operator_yaml` — same call; assert `~/.bicameral/projects/<id>/operator.yaml` contains `telemetry`, `channel`, `guided`, `signer_email_fallback`, `render_source_attribution`, but NOT `mode` or `team.*`.
- `tests/test_config_split.py::test_atomic_two_file_write_failure_unlinks_both_temps` — monkeypatch `Path.replace` to raise on the second file; call `_write_collaboration_config`; assert neither destination file exists, no `*.tmp` artifacts remain in either directory, and the function raised.
- `tests/test_config_split.py::test_routing_table_covers_every_key` — assert every key produced by `_write_collaboration_config` is present in `context._CONFIG_KEY_ROUTING` with a non-None value of `team` or `operator`. Catches drift where a new key is added to the writer but not the routing table.
- `tests/test_config_split.py::test_context_reads_route_per_key` — write `mode: team` to a fake config.yaml and `guided: true` to a fake operator.yaml; assert `_read_guided_mode(repo)` returns True (read from operator.yaml) and a representative team-mode reader returns `"team"` (read from config.yaml).
- `tests/test_setup_wizard_git_native.py::test_onboarding_skips_team_prompts_when_head_has_team_config` — `git init` repo + commit `.bicameral/config.yaml` with `mode: team` and team-backend block. `monkeypatch` the wizard's questionary prompts to raise (would block on prompt). Call `run_setup(repo_path)`; assert it succeeds without raising and the team-prompt path is skipped (proves `git show HEAD:` detection short-circuits the wizard).
- `tests/test_setup_wizard_git_native.py::test_onboarding_skips_solo_prompts_when_head_has_solo_config` (**R4 V3 addition**) — symmetric to the team test: `git init` repo + commit `.bicameral/config.yaml` with `mode: solo`. Monkeypatch `_select_collaboration_mode` to raise. Call `run_setup(repo_path)`; assert it succeeds and `_select_collaboration_mode` was not invoked (proves solo-mode short-circuit branch fires).
- `tests/test_setup_wizard_git_native.py::test_divergence_guard_warns_on_branch_without_config` — `git init` repo with `main` having committed `.bicameral/config.yaml`; `git checkout -b feature-x` and remove `.bicameral/config.yaml`. From the feature branch, call the wizard's divergence guard helper; assert the warning message names the default branch (`main`) and offers the merge-first option. Decision: decision:ogdfx014sqgc6fi6ky1a.
- `tests/test_run_config_wizard.py::test_editor_reads_from_both_files` (**R4 V3 addition**) — pre-populate `<repo>/.bicameral/config.yaml` with `mode: team` and `~/.bicameral/projects/<id>/operator.yaml` with `guided: true, channel: stable`. Monkeypatch `questionary` to capture the prompt scaffolding instead of prompting. Invoke `run_config_wizard()` and assert the captured scaffolding includes BOTH `mode` (team-side) and `guided` (operator-side) as editable rows, each tagged with the correct `[team]` / `[your machine]` prefix.
- `tests/test_run_config_wizard.py::test_editor_writes_to_routed_file` (**R4 V3 addition**) — same fixture; monkeypatch `questionary` to return simulated edits: change `mode: team` → `solo` AND `guided: true` → `false`. Invoke `run_config_wizard()`; assert `<repo>/.bicameral/config.yaml` now contains `mode: solo` (and is otherwise unchanged) AND `~/.bicameral/projects/<id>/operator.yaml` now contains `guided: false` (and is otherwise unchanged). Catches the regression where the editor writes operator-side edits to config.yaml (or vice versa).
- `tests/test_ledger_locator_vcs_contract.py::test_non_git_directory_raises_with_vcs_message` — `resolve_ledger_url()` from a non-git tmp_path; assert the raised `ProjectIdResolutionError`'s message contains the verbatim string `"bicameral currently supports git only; non-git VCSes are not yet implemented"`. Decision: decision:6c20xahdyxk3suzav4pj.

### Implementation files

- `ledger/adapter.py`:
  - `def _default_db_url() -> str: return ledger_locator.resolve_ledger_url()`. The helper stays as a single line so future tests / mocks can monkey-patch it.
- `code_locator_runtime.py`:
  - The init function calls `os.environ.setdefault("CODE_LOCATOR_SQLITE_DB", str(ledger_locator.resolve_code_graph_path()))` instead of computing the path inline.
- `code_locator/config.py`:
  - Change field declaration `sqlite_db: str = "~/.bicameral/code-graph.db"` to `sqlite_db: str | None = None`. Remove the hard-coded path literal.
  - Replace `resolve_paths()` body with the None-safe form:
    ```python
    def resolve_paths(self) -> CodeLocatorConfig:
        if self.sqlite_db is None:
            from ledger_locator import resolve_code_graph_path
            self.sqlite_db = str(resolve_code_graph_path())
        else:
            self.sqlite_db = str(Path(self.sqlite_db).expanduser())
        return self
    ```
  - `load_config()` is unchanged in body — it already terminates with `.resolve_paths()`. The None default + None-safe `resolve_paths` is what closes the V1/V2 gap: every consumer that goes through `load_config()` sees a resolved string; every consumer that direct-constructs the dataclass and calls `.resolve_paths()` also sees one.
  - The pre-existing `CODE_LOCATOR_SQLITE_DB` env-var override path in `load_config()` (lines 56-68) is unchanged — when set, it lands in `config_data["sqlite_db"]` as a string and bypasses the locator fallback. Tests that pre-set the env var continue to work.
- `setup_wizard.py::_build_config`:
  - Delete the two lines that write `SURREAL_URL` (now at line 472, was misquoted as 366 in the pre-audit draft) and `CODE_LOCATOR_SQLITE_DB` (line 473). No replacement — the locator picks both up at runtime from the agent process's env (which is empty for both vars by default).

- `setup_wizard.py::_write_collaboration_config` (R4 split — decision:5nr66wvmapjpt58rrji8):
  - Function signature unchanged. Body splits the input into team-identity and per-operator buckets via the `context._CONFIG_KEY_ROUTING` table.
  - Two-file atomic write pattern: write each file to `<dest>.tmp` first, then `os.replace(<dest>.tmp, <dest>)` for atomic rename. If the second `replace` fails, the first file's temp gets unlinked (cleanup) — but the first file has already been successfully renamed, so the writer attempts to restore the previous content via backup. Simpler alternative: always write operator.yaml first (no git side-effect on failure), then config.yaml; if config.yaml fails, operator.yaml is left in its new state but the function raises. Operator can rerun. Document the failure mode in the function docstring.
  - The function prints both file paths on success: `"team config → {config_path} (commit this)"` and `"your settings → {operator_path} (private)"`.

- `context.py`:
  - New module-level constant `_CONFIG_KEY_ROUTING: dict[str, Literal["team", "operator"]]`. Lists every key the wizard writes, partitioning team-identity vs per-operator. Single source of truth for migrate-state, wizard, and readers.
  - Each of the 9 existing reader helpers (`_read_signer_email_fallback`, `_read_render_source_attribution`, `_read_ingest_max_bytes`, `_read_ingest_rate_limit_burst`, `_read_ingest_rate_limit_refill_per_sec`, `_read_query_timeout_*`, `_read_guided_mode`, generic `_read_yaml_string_field`) consults `_CONFIG_KEY_ROUTING[key]` to pick the file: `Path(repo_path) / ".bicameral" / "config.yaml"` for team-identity keys, `ledger_locator.resolve_operator_config_path(repo_path)` for per-operator keys.
  - Existing fallback semantics preserved per reader (missing file → default; malformed YAML → line-oriented fallback).

- `setup_wizard.py::run_setup` (R4 git-native onboarding detection — decision:ew9rgegdlblexsraesss):
  - Before calling `_select_collaboration_mode()`, run `git show HEAD:.bicameral/config.yaml` via `subprocess.run([...], cwd=repo_path, capture_output=True, text=True)`.
  - On returncode 0 + parseable YAML + `mode == "team"`: print `"Detected team config: backend={team.backend}, folder={team.folder_id or team.remote_root} ✓ (auto-joining)"` and skip the entire `_select_team_backend` flow. `team_backend = parsed["team"]` is reused verbatim.
  - On returncode 0 + `mode == "solo"`: similar short-circuit — `collab_mode = "solo"`; skip prompt.
  - Otherwise (returncode != 0 / unparseable / no mode): fall through to existing `_select_collaboration_mode()` prompt.
  - Divergence guard (decision:ogdfx014sqgc6fi6ky1a): when HEAD lacks the file, also run `git show {default_branch}:.bicameral/config.yaml` where `default_branch = _resolve_authoritative_ref(repo_path)[0]`. If that returncode is 0, prompt: `"Your branch doesn't have .bicameral/config.yaml, but {default_branch} does. Merge first to inherit team config, or continue with fresh setup? [y/N]"`. On `n`/empty (default `N`), exit non-zero with the merge-first hint.

## Phase 3: `migrate-state` CLI + auto-trigger on `bicameral update`

The bytes have to move before the new binary tries to open them. `migrate-state` is a one-shot, idempotent, manifest-resumable CLI that walks the current repo and relocates *every project-scoped state file* from `<repo>/.bicameral/...` into the locator-resolved project dir. Archives on collision; cleans up empty source directories after success.

**Source inventory** (per R3 — the full project-scoped state, not just ledger + code-graph):

| Legacy path (under `<repo>/.bicameral/`) | Destination (under `~/.bicameral/projects/<id>/`) | Why it moves |
|---|---|---|
| `ledger.db` (or legacy user-global `~/.bicameral/ledger.db`) | `ledger.db` | The decision store. |
| `local/code-graph.db` (+ `-shm`, `-wal`) | `code-graph.db` (+ siblings) | Symbol index. |
| `local/bm25_index.pkl` | `bm25_index.pkl` | BM25 index over the same symbols. |
| `local/watermark` | `watermark` | Per-peer event-replay offsets. |
| `pending-transcripts/*.jsonl` | `pending-transcripts/*.jsonl` | SessionEnd-hook queue; per-worktree today, project-scoped after. |
| `processed-transcripts/*.jsonl` | `processed-transcripts/*.jsonl` | Sibling to pending. |

**R4 addition** — partitioning existing `<repo>/.bicameral/config.yaml`:

| Legacy key (in `<repo>/.bicameral/config.yaml`) | R4 destination | Routing class |
|---|---|---|
| `mode` | stays | `team` |
| `team.backend`, `team.folder_id`, `team.remote_root` | stays | `team` |
| `ingest_max_bytes` | stays | `team` |
| `telemetry`, `channel`, `guided` | move to `~/.bicameral/projects/<id>/operator.yaml` | `operator` |
| `signer_email_fallback`, `render_source_attribution` | move | `operator` |
| `team.author`, `team.role` | move | `operator` |
| `ingest_rate_limit_burst`, `ingest_rate_limit_refill_per_sec` | move | `operator` |
| `query_timeout_read_seconds`, `query_timeout_drift_seconds` | move | `operator` |

The partition is driven by `context._CONFIG_KEY_ROUTING` (defined in Phase 2 Implementation files). Unknown keys (added by future versions) stay in `config.yaml` with a warning logged — forward-compat for unrecognized keys defers their classification to a later release rather than dropping them.

**Files that do NOT move** (and why, so the operator's post-flight summary can name them by intent rather than oversight):
| Path | Why we keep it where it is |
|---|---|
| `.bicameral/config.yaml` (trimmed, R4) | Per-clone identity, committed to git. Read directly via `Path(repo) / ".bicameral" / "config.yaml"` (no locator function). Worktree-divergence is treated like any other tracked file — git handles it. Decision: decision:ew9rgegdlblexsraesss. |
| `.bicameral/events/*.jsonl` | Currently committed to git (will change under #373). Leaving them in working tree until #373 lands; until then, worktrees on different branches will see different event-file content — accepted as a transitional limitation, called out in the migrate-state post-flight summary. |

**R4 post-flight notice** (decision:e3xz4c4ji4x7lm3lvq4k): on every `migrate-state` success, append the following line to the summary block, regardless of detected environment: `"Note: ~/.bicameral/projects/ persists per home directory. If you run bicameral inside an ephemeral container (Codespaces, devcontainer, CI), state will be lost at teardown. Full ephemeral-environment support tracked separately under v0.16.1/v0.17."` Single line; bounded scope; doesn't depend on environment detection (which is the deferred feature).

### Affected files

- `cli/migrate_state.py` (new) — argparse-driven CLI: `--repo PATH` (defaults to cwd), `--auto` (non-interactive), `--dry-run`, `--archive-dir PATH`.
- `server.py:1447-1497` (`_register_subparsers`) — register `migrate-state` (and an alias `migrate-ledger` so the issue's verbiage works).
- `server.py:1500-1545` (`_dispatch`) — route to `cli.migrate_state.main`.
- `.claude/skills/bicameral-update/SKILL.md` — append a post-upgrade step: run `bicameral-mcp migrate-state --auto` before reporting "update complete".

### Unit tests (write first)

- `tests/test_migrate_state.py::test_moves_ledger_and_code_graph_in_one_pass` — `tmp_path` git repo; pre-create `<repo>/.bicameral/ledger.db` (1KB of random bytes) and `<repo>/.bicameral/local/code-graph.db{,-shm,-wal}` (mock files); call `migrate_state.main(["--repo", str(repo), "--auto"])`; assert (a) all four files now exist at the locator-resolved project dir, (b) byte-for-byte identical to the originals, (c) the originals are gone, (d) the empty `<repo>/.bicameral/local/` was removed.
- `tests/test_migrate_state.py::test_idempotent_second_run_is_noop` — run migrate twice; second invocation reports "nothing to migrate" with exit 0 and does not touch the destination files (compare mtime before/after).
- `tests/test_migrate_state.py::test_collision_archives_destination` — pre-populate both source and destination ledger.db with different content; migrate; assert the destination file got moved to `<archive_dir>/ledger.db.<iso8601>.bak` before the source replaced it; the source then no longer exists at the source path.
- `tests/test_migrate_state.py::test_dry_run_writes_nothing` — populate sources; `--dry-run`; assert source files unchanged, destination dir does not exist, stdout enumerates the planned operations.
- `tests/test_migrate_state.py::test_missing_source_skips_silently` — repo with no `<repo>/.bicameral/ledger.db`; migrate exits 0; stdout says "nothing to migrate"; destination dir is not created.
- `tests/test_migrate_state.py::test_partial_state_migrates_what_exists` — only `ledger.db` exists locally (no code-graph); migrate moves the ledger, skips the code-graph silently, exits 0.
- `tests/test_migrate_state.py::test_auto_flag_skips_prompts` — populate sources; `--auto`; `monkeypatch` `input` to raise (would block on prompt); migration still succeeds (proves the non-interactive path).
- `tests/test_migrate_state.py::test_archive_dir_defaults_under_home` — collision case without `--archive-dir`; archive lands under `~/.bicameral/archive/<project-id>/`.
- `tests/test_migrate_state.py::test_moves_bm25_watermark_and_transcript_queues` — pre-create `local/bm25_index.pkl`, `local/watermark`, `pending-transcripts/sess1.jsonl`, `pending-transcripts/sess2.jsonl`, `processed-transcripts/sess0.jsonl`; run `--auto`; assert all five files land at the locator-resolved project dir with byte-identical content; the source files are gone; the empty `<repo>/.bicameral/pending-transcripts/` is removed; the empty `<repo>/.bicameral/processed-transcripts/` is removed.
- `tests/test_migrate_state.py::test_legacy_user_global_ledger_moves_on_first_project` — pre-create `~/.bicameral/ledger.db` (no `<repo>/.bicameral/ledger.db`); run `--auto` from a `tmp_path` git repo; assert the user-global file moved into `~/.bicameral/projects/<id>/ledger.db`; `~/.bicameral/ledger.db` no longer exists.
- `tests/test_migrate_state.py::test_legacy_user_global_ledger_already_claimed_is_noop` — pre-create `~/.bicameral/projects/<other-id>/ledger.db` (some other project already claimed it; `~/.bicameral/ledger.db` is gone); run `--auto` from a fresh repo; assert no error, no move attempt, exit 0.

### Implementation files

- `cli/migrate_state.py`:
  - `_FILE_SOURCES = [".bicameral/ledger.db", ".bicameral/local/code-graph.db", ".bicameral/local/code-graph.db-shm", ".bicameral/local/code-graph.db-wal", ".bicameral/local/bm25_index.pkl", ".bicameral/local/watermark"]`.
  - `_DIR_SOURCES = [".bicameral/pending-transcripts", ".bicameral/processed-transcripts"]` — directory contents move into the project dir's same-named subdir; the directory itself is removed if empty after move.
  - `_LEGACY_USER_GLOBAL = Path.home() / ".bicameral" / "ledger.db"` — the v0.15.x user-global ledger (was unscoped). The first project's `migrate-state` invocation claims it; subsequent invocations skip it (already moved or never existed).
  - `def _planned_moves(repo: Path) -> list[tuple[Path, Path]]`: pair each present source with the locator-resolved destination via `ledger_locator.project_dir_for(repo)`. Walks files in `_DIR_SOURCES` directories one-by-one.
  - `def _archive_existing(dest: Path, archive_dir: Path) -> Path | None`: if dest exists and bytes differ from source, mv to `<archive_dir>/<dest.name>.<iso8601>.bak`; return the archive path.
  - `def _execute(plan, archive_dir, dry_run) -> int`: iterate, archive-on-collision, `shutil.move`; clean up the empty `<repo>/.bicameral/local/` and `<repo>/.bicameral/pending-transcripts/` directories after success.
  - `main(argv)`: argparse → plan → confirm (unless `--auto`) → execute. Exit 0 on success or no-op.

- `server.py`:
  - In `_register_subparsers`: `migrate = subparsers.add_parser("migrate-state", help="move ledger.db + code-graph.db to ~/.bicameral/projects/<id>/ (v0.16 layout)"); migrate.add_argument(...); subparsers.add_parser("migrate-ledger", help="alias for migrate-state (#368)")`.
  - In `_dispatch`: both subcommands route to `cli.migrate_state.main`.

- `.claude/skills/bicameral-update/SKILL.md`:
  - After the existing "pipx/uv install --upgrade" step, append: "Run `bicameral-mcp migrate-state --auto`. Surface the migration summary to the operator. If the binary exits non-zero, surface the stderr verbatim and abort the update flow."

## Phase 4: `gc` CLI for orphaned project dirs

After a project is deleted or relocated, its `~/.bicameral/projects/<id>/` is orphaned. The garbage collector lists orphans (their `origin.txt` no longer resolves to an existing directory) and prompts before deleting.

### Affected files

- `cli/gc.py` (new) — argparse-driven CLI: default = list; `--delete` performs deletion after a per-item prompt.
- `server.py:1447-1497` — register `gc` subparser.
- `server.py:1500-1545` — route.

### Unit tests (write first)

- `tests/test_gc.py::test_lists_orphans_and_keeps_live_projects` — seed `~/.bicameral/projects/<id_live>/origin.txt` pointing at an existing `tmp_path` git dir, and `<id_orphan>/origin.txt` pointing at a deleted path; `gc.main([])` exits 0; stdout lists exactly `<id_orphan>` and does NOT list `<id_live>`.
- `tests/test_gc.py::test_delete_prompts_per_item_and_removes_confirmed_ones` — two orphans; patched `input` returns `y` for the first and `n` for the second; `--delete`; first project dir is gone, second is intact.
- `tests/test_gc.py::test_delete_with_yes_flag_skips_prompts` — two orphans; `--delete --yes`; both project dirs removed; `input` never called (patched to raise).
- `tests/test_gc.py::test_skips_unreadable_origin_txt_with_warn` — write `origin.txt` as zero bytes; `gc.main([])` logs WARN naming the file, classifies as orphan, exits 0. (Empty origin = unrecoverable; the operator chooses whether `--delete` it.)

### Implementation files

- `cli/gc.py`:
  - `def _scan(state_root: Path) -> list[tuple[str, Path, str]]`: yields `(project_id, project_dir, status)` for every direct child of `state_root`. `status` is `"live"` / `"orphan"` / `"unreadable"`.
  - `def _is_live(origin_path: Path) -> bool`: read `origin.txt`, return True iff the named path exists and is a directory.
  - `main(argv)`: argparse; list-mode renders a table; `--delete` iterates the orphans and prompts (unless `--yes`); `shutil.rmtree` on confirm.

## CI Commands

- `python -m pytest tests/test_ledger_locator.py tests/test_ledger_locator_origin_guard.py tests/test_ledger_locator_vcs_contract.py -v` — Phase 1: locator module + origin guard + R4 VCS contract.
- `python -m pytest tests/test_ledger_adapter_uses_locator.py tests/test_code_locator_runtime_uses_locator.py tests/test_code_locator_config_none_safe.py tests/test_setup_wizard_omits_state_env_vars.py tests/test_two_worktrees_share_state.py tests/test_config_split.py tests/test_setup_wizard_git_native.py -v` — Phase 2: call-site delegation + worktree regression (ledger + code-graph + bm25 + watermark + pending-transcripts) + R4 config split + R4 git-native wizard. *(R3's `test_config_path_resolution.py` is intentionally absent — R4 retracts the primary-worktree resolution design it tested.)*
- `python -m pytest tests/test_migrate_state.py -v` — Phase 3: migration CLI.
- `python -m pytest tests/test_gc.py -v` — Phase 4: GC CLI.
- `ruff check ledger_locator cli/migrate_state.py cli/gc.py ledger/adapter.py code_locator_runtime.py code_locator/config.py events/materializer.py events/transcript_queue.py scripts/hooks/transcript_archive.py context.py setup_wizard.py` — lint scope matches the diff.
- `mypy ledger_locator cli/migrate_state.py cli/gc.py events/materializer.py events/transcript_queue.py context.py` — type-check new + modified modules (R4 adds context.py routing table to typing scope).
- `python -m pytest tests/ -v` — full suite; the locator changes touch the default DB path so every existing test that constructs an adapter must still pass.
