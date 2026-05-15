# RFC: State Partition — Ledger Locator

_Drafted 2026-05-15. Target: v0.15.0._

---

## Why

Today the SurrealDB ledger file lives under `<repo>/.bicameral/ledger.db`, written into `.mcp.json` by `setup_wizard.py:366`. That choice predates two facts that are now load-bearing:

1. **Events are the source of truth; the ledger is a projection.** Per `docs/v0-architecture-current.md` §5, `.bicameral/events/{email}.jsonl` is the committed, CRDT-mergeable, append-only log. The ledger is a derived cache, rebuildable from events. Treating a projection as if it were source-of-truth state forces it to live next to the repo's other source-of-truth files — which is a category error.
2. **Git worktrees are a first-class workflow for our users.** Multiple working trees of one project share the same object database, the same refs, the same authoritative branch, the same decisions — but currently land on divergent `<repo>/.bicameral/ledger.db` files, one per working tree. Decisions ingested in worktree A are invisible to preflight in worktree B. This contradicts CONCEPT.md's invariant that decisions are project-scoped.

The fix is to move the projection out of the working tree and anchor it at the **project** (not the working tree). Events stay where they are.

This RFC introduces a single primitive — the **Ledger Locator** — that owns this resolution and is the only code path that decides where the ledger lives.

## What

### The Ledger Locator primitive

A new module `ledger_locator/` exporting one function:

```python
def resolve_ledger_url() -> str:
    """Return the SurrealDB URL for this invocation's ledger.

    Resolution order:
      1. SURREAL_URL env var (explicit override; wins unconditionally).
      2. Default: surrealkv://~/.bicameral/projects/<project-id>/ledger.db
         where <project-id> = sha256(git rev-parse --git-common-dir)[:16].

    The Ledger Locator is deterministic, has no LLM in path, and is the
    single source of truth for ledger location. It replaces the ad-hoc
    fallback in ledger/adapter.py:_default_db_url and the per-repo
    SURREAL_URL written by setup_wizard.py:_build_config.
    """
```

Mirrors `code_locator/` in shape: deterministic primitive, no LLM, one function.

### Supported environments

Locked scope. The Ledger Locator supports exactly two environments:

| Environment | Resolution |
|---|---|
| **Local dev** | Default → `~/.bicameral/projects/<project-id>/ledger.db` |
| **Memory test** | `SURREAL_URL=memory://` set explicitly |

**Explicitly out of scope** — these are not supported and have no auto-detection:

- CI runners (GitHub Actions, GitLab CI, Jenkins, BuildKite, etc.)
- Docker / docker-compose dev environments
- Multi-user shared runners
- Any `--ledger-location=repo` opt-out flag

Users in any unsupported environment must set `SURREAL_URL` explicitly. Behavior without an explicit `SURREAL_URL` outside the two supported environments is undefined.

**Rationale**: every supported-environment heuristic we add is a footgun and a maintenance burden. `SURREAL_URL` already covers the escape hatch — adding `BICAMERAL_LEDGER_LOCATION`, CI auto-detect, or `--repo-local` flags multiplies the surface that has to stay correct as the system evolves. Simplicity wins.

### Project identity

`<project-id>` is derived from `git rev-parse --git-common-dir`:

```
project_id = sha256(canonical_path).hexdigest()[:16]
```

Properties:

- **Same from every worktree of one repo.** Worktrees share `--git-common-dir`. No special-casing needed at the locator layer.
- **Differs across separate clones on the same machine.** Each clone has its own gitdir path. Two clones of the same upstream repo get separate ledgers — correct default; forks and parallel experiments stay isolated.
- **Doesn't survive `rm -rf <repo>` + re-clone.** A new clone has a new gitdir path → new project-id → fresh ledger. Re-import happens by replaying events from git (team mode) or by starting fresh (solo mode).

### Branch isolation stays logical

Per the ingest decision ratified 2026-05-15, **branch isolation continues to live on the `ephemeral=True/False` flag on `compliance_check` rows**, not on the storage layout. One ledger per project; branch metadata is a per-row property of one table.

Per-branch DBs were considered and explicitly rejected because they:

- Invalidate the content-hash invariant (scenarios E3, E4, E13 in `ephemeral-authoritative.md` — fast-forward, squash, rebase verdicts survive because the hash is unchanged; per-branch DBs throw the verdict away)
- Fragment project-scoped decisions across multiple DBs (or force them to be replicated, which breaks first-write-wins idempotence)
- Replace promotion-on-merge from a single flag flip into a cross-DB row-copy with dedup
- Break preflight's cross-branch read pattern (decisions ingested on `main` need to be visible from `feature-x` preflights)

The ephemeral flag is the right primitive. The Ledger Locator change does not touch it.

### Filesystem layout

```
~/.bicameral/
├── projects/
│   ├── <project-id>/
│   │   ├── ledger.db          ← SurrealKV file
│   │   └── code-graph.db      ← SQLite symbol index
│   └── <another-project-id>/
│       └── ...
└── (future) gc.log            ← orphan-reclaim audit trail
```

Per-project subdirs hold both the SurrealDB ledger and the SQLite code-graph index. These two were already co-located under `.bicameral/local/` — that grouping is preserved at the new home.

### Migration

One-shot CLI: `bicameral-mcp migrate-ledger`.

Detects the legacy `<repo>/.bicameral/ledger.db` (and `code-graph.db`), computes the project-id for the current working tree, and:

1. Creates `~/.bicameral/projects/<project-id>/` if not present.
2. If the destination is empty: moves the legacy files in place.
3. If the destination already exists (e.g. the user ran migration in worktree A first, now running in worktree B of the same project): leaves the destination untouched, archives the legacy files to `<repo>/.bicameral/legacy-<timestamp>/`, and prints a warning. First-write-wins on content-addressed keys means the destination is authoritative.
4. Rewrites `.mcp.json` to drop the per-repo `SURREAL_URL` (locator default takes over) or to point at the new location if running with `BICAMERAL_DATA_PATH` overrides.

Idempotent — running twice from the same worktree is a no-op.

### Orphan reclaim

A `bicameral-mcp gc` command (or quiet startup check) iterates `~/.bicameral/projects/*/`, resolves each project-id against any reachable gitdir on the user's machine, and offers to reclaim subdirs whose gitdir no longer exists. Interactive only; never auto-deletes.

### CONCEPT.md update

The anti-goal currently reads:

> Not a cloud service. No remote DB, no managed backend; **the ledger lives next to the repo it tracks.**

The bolded clause was always trying to say "not in the cloud" but reads as "in the working tree." It needs to become:

> Not a cloud service. No remote DB, no managed backend; **the ledger runs on your machine, never in a remote service.** Truth lives in the repo (committed events at `.bicameral/events/`); the user-local cache (`~/.bicameral/projects/<id>/`) is rebuildable from those events.

This is a wording change, not a principle change. The principle is still "local-first, no network, no cloud." The shift is recognizing that "local" means "on this user's machine" — not "in this working tree."

## Out of scope

- Cross-machine ledger sync (team mode already handles this via committed events).
- Per-branch DBs (rejected above).
- CI / Docker / shared-runner support (users set `SURREAL_URL` explicitly).
- Any change to `code_locator/` beyond co-locating its DB file.
- Changing what gets stored in the ledger; this RFC is purely about *where* the file lives.
- Networked / remote ledger backends.

## Acceptance

A v0.15.0 release that:

- [ ] `ledger_locator/` module exists, exports `resolve_ledger_url()`, has unit tests covering: env override wins, default path is keyed off `git rev-parse --git-common-dir`, same path resolves from every worktree of one repo, different path across separate clones, `memory://` env passes through unchanged.
- [ ] `ledger/adapter.py:_default_db_url` and `setup_wizard.py:_build_config` delegate to `ledger_locator.resolve_ledger_url()`. No other call site bypasses the locator.
- [ ] `setup_wizard.py` no longer writes a per-repo `SURREAL_URL` to `.mcp.json` for the default case; the locator handles it. The wizard still writes explicit overrides when the user sets them.
- [ ] `bicameral-mcp migrate-ledger` CLI exists, is idempotent, archives legacy files when the destination already exists, and emits a clear summary.
- [ ] `bicameral-mcp gc` CLI exists, lists orphan project dirs, prompts before deleting.
- [ ] CONCEPT.md anti-goal wording updated per above.
- [ ] `docs/state-partition-rfc.md` (this doc) cross-referenced from CONCEPT.md and v0-architecture-current.md.
- [ ] Regression test: two worktrees of one repo against one ledger; a decision ingested in worktree A is surfaced by preflight in worktree B.

## v0.14.7 stopgap

Before this RFC lands, ship a minimal v0.14.7 patch that unblocks worktree users on the *current* architecture:

- Linked-worktree detection message in `setup_wizard.py` (when `_resolve_git_hooks_dir` resolves through a `gitdir:` pointer, print a one-line "linked worktree detected — re-run setup from each worktree" notice).
- `origin/HEAD` probe + prompt to set `BICAMERAL_AUTHORITATIVE_REF` when the remote default branch isn't auto-detectable.

The stopgap does not change ledger location. It only makes the existing per-worktree setup *intentional and visible*, so the worktree user surveying the wizard output understands the model before v0.15.0 lands.

---

_Source decisions captured in the ledger 2026-05-15 (ingest flow `6dee7ffd`)._
