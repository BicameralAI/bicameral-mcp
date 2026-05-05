# Plan: SessionEnd hook → next-session pending-transcripts queue (#156)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: pending-transcripts queue
  home: events/transcript_queue.py
- term: processed-transcripts archive
  home: events/transcript_queue.py

**boundaries**:
- limitations: Queue drain happens only at preflight Step 3.5 (per Q2 dialogue resolution). Read-only follow-up sessions that never call preflight leave pending files in place; corrections surface on the next implementation session. If telemetry post-deploy shows >10% of pending files going >7 days unsurfaced, upgrade to a hybrid Step-3.5 + SessionStart-fallback approach in a separate PR.
- non_goals: Adding a SessionStart hook in v1; replacing the existing `bicameral-capture-corrections` rubric (the pending-transcripts queue feeds the existing rubric, doesn't replace it); auto-deleting processed-transcripts (kept forever for audit; future team-server config may override).
- exclusions: PR #152's substrate fixes (re-entrancy guard, `--auto-ingest` flag drift, `.bicameral/` bootstrap) are independent and stay landed. This plan REPLACES the canonical SessionEnd hook command but does NOT undo PR #152's other fixes.

## Open Questions

None. All design choices resolved in `/qor-plan` dialogue:
- Q1 scope phasing → two-phase, two-PR (this plan covers PR A; PR B writes its own plan).
- Q2 drain trigger → preflight Step 3.5 only (no SessionStart fallback in v1).
- Q3 multi-transcript merge → FIFO single-pass; preserve session_id provenance.
- Q4 processed retention → keep forever in `.bicameral/processed-transcripts/`. Forward-compat note: retention and merge policy live in a single module (`events/transcript_queue.py`) so a future team-server config can override per-install.

## Phase 1: Queue write (SessionEnd hook → `.bicameral/pending-transcripts/`)

### Affected Files

- `tests/test_session_end_queue_writer.py` — new behavioral tests (9 tests)
- `scripts/hooks/session_end_queue_writer.py` — new Python hook script (replaces the `claude -p` SessionEnd command). Cross-platform; matches the existing `scripts/hooks/preflight_reminder.py` pattern (path-style invocation + `sys.path.insert` bootstrap to import sibling packages from repo root)
- `scripts/hooks/transcript_archive.py` — new tiny CLI helper that takes a pending-transcript basename as argv and routes archival through `events.transcript_queue.archive_processed`. Used by Phase 2's Step 0 instead of raw `mv`, preserving idempotency + cross-platform semantics. Path-style, basename-only argv (no path-traversal surface)
- `events/transcript_queue.py` — new module with pure helpers (`write_pending(repo_path, session_id, transcript_path)`, `list_pending_fifo(repo_path)`, `archive_processed(repo_path, pending_path)`). Single source of truth for queue layout — Phase 2's Step 0 read path reads from the same module
- `setup_wizard.py` — `_build_session_end_command` rewrites to invoke `python3 scripts/hooks/session_end_queue_writer.py` (path-style, consistent with the other three existing hooks in `.claude/settings.json`) instead of `claude -p '/bicameral-capture-corrections --auto-ingest'`. The legacy `--auto-ingest` flag is removed; `BICAMERAL_SESSION_END_RUNNING` re-entrancy guard preserved
- `.claude/settings.json` — SessionEnd hook command updated to match the new shape (dogfood install)

### Changes

**`events/transcript_queue.py`** (new, ~50 LOC):

```python
"""Pending-transcripts queue (#156).

The SessionEnd hook copies the parent session's transcript to
``<repo>/.bicameral/pending-transcripts/<session_id>.jsonl``. The next
session's preflight Step 3.5 reads the queue FIFO, surfaces corrections
as ask-findings, then archives processed files to
``<repo>/.bicameral/processed-transcripts/``.

This module is the single source of truth for queue layout. Future
team-server config may override retention and merge policy by reading
this module's defaults.
"""
from __future__ import annotations

from pathlib import Path

PENDING_DIR = "pending-transcripts"
PROCESSED_DIR = "processed-transcripts"


def _pending_root(repo_path: str) -> Path:
    return Path(repo_path) / ".bicameral" / PENDING_DIR


def _processed_root(repo_path: str) -> Path:
    return Path(repo_path) / ".bicameral" / PROCESSED_DIR


def write_pending(repo_path: str, session_id: str, transcript_path: str) -> Path | None:
    """Copy `transcript_path` to the pending-transcripts queue.

    Returns the queue path on success, None on fail-soft (transcript
    missing, repo lacks .bicameral/, write fails)."""
    src = Path(transcript_path)
    if not src.is_file():
        return None
    bicameral = Path(repo_path) / ".bicameral"
    if not bicameral.is_dir():
        return None
    pending = _pending_root(repo_path)
    pending.mkdir(parents=True, exist_ok=True)
    dst = pending / f"{session_id}.jsonl"
    dst.write_bytes(src.read_bytes())
    return dst


def list_pending_fifo(repo_path: str) -> list[Path]:
    """Return pending transcript files ordered oldest-first by mtime.
    Used by Step 0 of capture-corrections (Phase 2) to drain the queue."""
    pending = _pending_root(repo_path)
    if not pending.is_dir():
        return []
    return sorted(pending.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def archive_processed(repo_path: str, pending_path: Path) -> Path:
    """Move `pending_path` from pending/ to processed/. Idempotent — if
    the destination already exists (re-replay), overwrites."""
    archive = _processed_root(repo_path)
    archive.mkdir(parents=True, exist_ok=True)
    dst = archive / pending_path.name
    if dst.exists():
        dst.unlink()
    pending_path.rename(dst)
    return dst
```

**`scripts/hooks/session_end_queue_writer.py`** (new, ~35 LOC):

```python
"""SessionEnd hook — copy the parent session's transcript to the queue.

Receives a JSON envelope on stdin from Claude Code's SessionEnd hook
contract: ``{"session_id": "...", "transcript_path": "...", "cwd": "...", ...}``.

Replaces the broken ``claude -p '/bicameral-capture-corrections --auto-ingest'``
canonical command (#156). Pure shell-style behavior: no claude subprocess,
no MCP config, no auth. Errors swallowed (exit 0) so a broken hook never
blocks a user from ending their session.

Invocation: ``python3 scripts/hooks/session_end_queue_writer.py`` from the
repo root (path-style, consistent with the other three hooks in
``.claude/settings.json``). The ``sys.path`` bootstrap below mirrors the
shape used by ``scripts/hooks/preflight_reminder.py:41-42`` so the
``from events.transcript_queue import write_pending`` import resolves
when invoked path-style.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Bootstrap: add repo root to sys.path so ``events`` (a top-level package
# at the repo root) resolves under path-style invocation. ``__file__`` is
# scripts/hooks/<name>.py; .parent.parent.parent is the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from events.transcript_queue import write_pending  # noqa: E402


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = str(payload.get("session_id") or uuid.uuid4())
    transcript_path = str(payload.get("transcript_path", "")).strip()
    cwd = str(payload.get("cwd", "")).strip()
    if not transcript_path or not cwd:
        return 0
    try:
        write_pending(cwd, session_id, transcript_path)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**`scripts/hooks/transcript_archive.py`** (new, ~25 LOC):

```python
"""CLI helper — archive one pending transcript via the queue module.

Used by ``bicameral-capture-corrections`` Step 0 (Phase 2 of #156) instead
of raw shell ``mv``. Routes archival through
``events.transcript_queue.archive_processed`` so:
  - idempotent re-replay semantics are preserved (overwrite if dst exists);
  - cross-platform behavior is uniform (Windows ``mv`` differs from POSIX);
  - a future team-server config that overrides retention/merge policy
    via the queue module Just Works without re-editing the SKILL.md.

Argv contract: a single basename (e.g. ``abc-1234.jsonl``). Resolves
``<cwd>/.bicameral/pending-transcripts/<basename>``. Basename-only is
deliberate: it's the constrained shape Step 0 actually needs and removes
the path-traversal surface that a full-path argv would expose. Exit
non-zero on missing file or unsafe basename so the caller can surface
the failure; this is NOT a fail-soft hook (unlike the SessionEnd writer)
because Step 0 wants to know if archival failed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from events.transcript_queue import _pending_root, archive_processed  # noqa: E402

_BASENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.jsonl$")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not _BASENAME_RE.match(argv[1]):
        print("usage: transcript_archive.py <basename>.jsonl", file=sys.stderr)
        return 2
    pending = _pending_root(".") / argv[1]
    if not pending.is_file():
        print(f"not found: {pending}", file=sys.stderr)
        return 1
    archive_processed(".", pending)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

**`setup_wizard.py`** — replace `_build_session_end_command`:

```python
def _build_session_end_command(mcp_config_path: pathlib.Path | None = None) -> str:
    """Canonical SessionEnd hook command (#156 pivot).

    Replaces the `claude -p` invocation from earlier versions, which
    couldn't access the parent session's transcript. The new shape
    pipes Claude Code's stdin envelope into a Python script that
    extracts `transcript_path` and copies it to the pending-transcripts
    queue. Next session's preflight Step 3.5 drains the queue.

    `mcp_config_path` retained for signature stability with consumers
    that pass it through; the new hook doesn't use it (no claude
    subprocess to configure).
    """
    return (
        '[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && '
        "BICAMERAL_SESSION_END_RUNNING=1 "
        "python3 scripts/hooks/session_end_queue_writer.py || true"
    )
```

**`.claude/settings.json`** — SessionEnd hook block updated to match.

### Unit Tests

- `tests/test_session_end_queue_writer.py::test_writer_copies_transcript_to_pending_dir` — feed stdin JSON `{"session_id": "abc", "transcript_path": <tmp_file>, "cwd": <tmp_repo>}`; assert `<tmp_repo>/.bicameral/pending-transcripts/abc.jsonl` exists with byte-exact contents from the source transcript. Confirms the queue-write happy path.
- `tests/test_session_end_queue_writer.py::test_writer_no_op_when_no_bicameral_dir` — feed stdin with a `cwd` that doesn't contain a `.bicameral/` directory; assert script exits 0 AND no pending file is created. Confirms fail-soft on uninitialized repo.
- `tests/test_session_end_queue_writer.py::test_writer_no_op_when_transcript_missing` — feed stdin with a non-existent `transcript_path`; assert exit 0 AND no pending file. Confirms fail-soft on missing transcript.
- `tests/test_session_end_queue_writer.py::test_writer_handles_malformed_stdin` — feed stdin with invalid JSON; assert exit 0 AND no pending file. Confirms fail-soft on protocol drift.
- `tests/test_session_end_queue_writer.py::test_writer_uses_uuid_when_session_id_missing` — feed stdin without `session_id` key but with valid `transcript_path` + `cwd`; assert a pending file is created with a UUID-shaped filename (regex match). Confirms data preservation under partial-payload conditions.
- `tests/test_session_end_queue_writer.py::test_list_pending_fifo_orders_by_mtime` — create three pending files with explicit mtimes (`os.utime`); assert `list_pending_fifo` returns them oldest-first. Confirms FIFO ordering invariant for Phase 2's drain.
- `tests/test_session_end_queue_writer.py::test_archive_processed_moves_pending_to_processed` — create a pending file via `write_pending`; call `archive_processed`; assert pending file no longer exists AND identical-named file exists in `processed-transcripts/` with same byte content. Confirms archival behavior.
- `tests/test_session_end_queue_writer.py::test_archive_processed_idempotent_on_replay` — call `archive_processed` twice on the same path (creating the pending file fresh between calls); assert second call succeeds without error AND the processed file content matches the second source. Confirms idempotency for re-replay scenarios.
- `tests/test_session_end_queue_writer.py::test_transcript_archive_invokes_archive_processed` — create a pending file via `write_pending` in a tmp repo, `chdir` into it, invoke `scripts/hooks/transcript_archive.py <basename>.jsonl` as a subprocess; assert exit 0, pending file gone, and an identically-named file present in `processed-transcripts/` with byte-identical content. Then invoke the helper a second time with a basename containing path-traversal (`../etc/passwd`); assert exit 2 (usage error) AND no filesystem mutation. Confirms the helper routes through `archive_processed` for the happy path AND rejects unsafe basenames at the argv boundary.

## Phase 2: capture-corrections Step 0 + SKILL.md narrative alignment

### Affected Files

- `skills/bicameral-capture-corrections/SKILL.md` — (a) add Step 0 (read pending queue, process FIFO, archive each); (b) update the deployment-shape block (line ~267 in current file: the canonical `SessionEnd` hook command rendered for end-users) from `claude -p '/bicameral-capture-corrections --auto-ingest'` to `python3 scripts/hooks/session_end_queue_writer.py`; (c) update narrative `--auto-ingest` references at lines ~185, ~218, ~227 to describe the queue-write path; (d) revise the `invocation_mode="auto_ingest"` telemetry tag at line ~50 — the SessionEnd hook no longer invokes capture-corrections directly, so the `auto_ingest` mode is not used by the SessionEnd hook itself; the mode tag is now reachable only from the next-session preflight Step 3.5 / Step 0 drain path. Per CLAUDE.md "Tool Changes Require Skill Changes (Mandatory)": these are not optional — the SKILL.md is the contract, and shipping a tool change without the matching skill update is incomplete.

### Changes

**`skills/bicameral-capture-corrections/SKILL.md`** — insert new Step 0 before existing Steps A/B/C:

```markdown
### Step 0. Drain the pending-transcripts queue (#156)

Before scanning the current session's transcript, check `<repo>/.bicameral/pending-transcripts/`. Each `*.jsonl` file there is a transcript from a prior session whose corrections never surfaced (the SessionEnd hook deferred them to next-session triage rather than running an empty `claude -p` subprocess).

For each pending file, in mtime-order (oldest first):

1. Read the file (it's a Claude Code transcript JSONL — same shape as the current session's, just from a prior run).
2. Apply Steps A/B/C below to the file's user turns. Treat each correction-marker hit as a candidate for ingest, just like the in-session path.
3. After processing, archive the file by invoking the queue module via the dedicated helper:

```bash
python3 scripts/hooks/transcript_archive.py <basename>.jsonl
```

`<basename>.jsonl` is the filename only (e.g. `abc-1234.jsonl`), not the full path. The helper resolves it to `<repo>/.bicameral/pending-transcripts/<basename>` itself, calls `events.transcript_queue.archive_processed`, and ensures idempotent overwrite + cross-platform behavior. Exit code 0 on success, 2 on usage error (unsafe basename), 1 on missing file.

Do NOT use raw `mv` shell — it bypasses the queue module's idempotent overwrite semantic and breaks on Windows.

If `<repo>/.bicameral/pending-transcripts/` doesn't exist or is empty, skip Step 0 entirely.

The processed-transcripts folder is kept for audit; v1 has no automatic cleanup. A future team-server config may override retention.

**Why this Step 0 exists**: prior to #156, the canonical `SessionEnd` hook ran `claude -p '/bicameral-capture-corrections --auto-ingest'` which spawned an empty subprocess that couldn't see the parent transcript — corrections silently failed to surface. The new shape defers transcript handling to the next session, where the agent + user are present with full ledger context to confirm or dismiss each correction (matches the in-session path's UX).
```

### Unit Tests

None for Phase 2. The Step 0 text is agent-instructions consumed by the LLM at runtime, not pytest-invocable. Per `doctrine-test-functionality` (and the precedent set by plan-installer-skills-remediation Phase 3 + plan-187 Phase 2): a static skill-vs-schema drift check would be presence-only and is therefore a separate CI lint concern, not a unit test in this plan. The functions Step 0 invokes (`list_pending_fifo`, `archive_processed`) and the CLI helper Step 0 calls (`scripts/hooks/transcript_archive.py`) are all unit-tested in Phase 1.

## Phase 3: Drift cleanup (test alignment + dead-parameter removal + doc currency)

Phase 3 closes the cascade of references to the prior canonical `claude -p` SessionEnd command across tests, the e2e harness, deployment docs, and a known-stale skill copy. None of these are speculative — every entry was confirmed by grep + the Phase 1/2 audit's debug residual sweep. This phase is mandatory because (a) Phase 1 broke pinned drift tests that will fail CI without it, and (b) leaving the `mcp_config_path` plumbing alive in `materialize_settings_with_hooks` after the new hook ignores the value would be exactly the kind of dead braid `Simple Made Easy` enjoins us to remove.

### Affected Files

- `tests/test_session_end_hook_drift.py` — update `CANONICAL_COMMAND` to the new path-style queue-writer shape; delete the two tests that pin removed mechanisms (`test_settings_json_session_end_passes_auto_ingest_flag` at lines ~47–50; `test_build_session_end_command_with_mcp_config_inserts_flags` at lines ~71–87); add one new test that asserts the deployed command points at `scripts/hooks/session_end_queue_writer.py` and contains no `--auto-ingest` flag. Keep the re-entrancy guard test (lines ~40–44) and the canonical-equality tests (lines ~53–68) — both work after the constant update.
- `tests/test_setup_wizard.py` — replace `test_session_end_command_uses_hyphen_slash_command` (lines ~75–81) with a new test that asserts `_BICAMERAL_SESSION_END_COMMAND` contains `scripts/hooks/session_end_queue_writer.py` and does NOT contain `/bicameral-capture-corrections`. The original test guarded against an issue-#177 hyphen-vs-colon regression on a slash command that the new hook no longer invokes; the regression class is moot for this hook, so the test's purpose pivots to "the new canonical shape is deployed."
- `tests/e2e/_harness_setup.py` — remove the `mcp_config_path` argument from the `_build_session_end_command(...)` call site (line ~95) and from the `materialize_settings_with_hooks` parameter (line ~53; verify by grep that no other caller relies on it before deleting). Update the harness module docstring (lines ~60–83) to describe the queue-write mechanism: the new SessionEnd hook writes a JSONL into `<desktop_repo_path>/.bicameral/pending-transcripts/`, ledger redirection now happens in the next flow's `claude -p` invocation (which already inherits `--mcp-config` via the per-flow runner), and the SessionEnd hook itself no longer needs MCP config.
- `setup_wizard.py` — update the inline comment at line ~444 that describes the SessionEnd hook as running `bicameral-capture-corrections`; replace with one line describing the queue-write shape.
- `README.md` — update the SessionEnd hook description at line ~197 (user-facing doc) to describe the new shape and the next-session drain.
- `tests/e2e/run_e2e_flows.py` — update the Flow 4 advisory text at lines ~1167–1179 that references plan-156 as future work; replace with a one-line note that #156 has landed and that out-of-band correction capture now flows through the next-session queue drain. No assertion changes needed in this PR — the in-flow assertions remain valid; cross-flow ledger assertion via the queue is deferred to PR B (preflight Step 3.5 integration).
- `.claude/skills/bicameral-capture-corrections/SKILL.md` — delete the file. CLAUDE.md states this directory is a stale duplicate that "should be deleted"; canonical lives at `skills/bicameral-capture-corrections/SKILL.md`. Removing now closes one drift surface and aligns with the project's stated intent.

### Changes

**`tests/test_session_end_hook_drift.py`** — replace `CANONICAL_COMMAND`:

```python
CANONICAL_COMMAND = (
    '[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && '
    "BICAMERAL_SESSION_END_RUNNING=1 "
    "python3 scripts/hooks/session_end_queue_writer.py || true"
)
```

Delete `test_settings_json_session_end_passes_auto_ingest_flag` (the flag is gone). Delete `test_build_session_end_command_with_mcp_config_inserts_flags` (the parameter is documented as ignored; the test asserts a removed mechanism). Add one new test:

```python
def test_settings_json_session_end_invokes_queue_writer():
    settings = json.loads(SETTINGS_PATH.read_text())
    cmd = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert "scripts/hooks/session_end_queue_writer.py" in cmd
    assert "--auto-ingest" not in cmd
    assert "claude -p" not in cmd
```

**`tests/test_setup_wizard.py`** — replace the slash-command form test:

```python
def test_session_end_command_invokes_queue_writer():
    cmd = setup_wizard._BICAMERAL_SESSION_END_COMMAND
    assert "scripts/hooks/session_end_queue_writer.py" in cmd
    assert "/bicameral-capture-corrections" not in cmd
```

**`tests/e2e/_harness_setup.py`** — call-site simplification at line ~95:

```python
session_end_command = _build_session_end_command()
```

And drop `mcp_config_path` from `materialize_settings_with_hooks`'s parameter list at line ~53 (and from `setup_all`'s call to it at line ~236; verify by grep that those are the only sites). Rewrite the docstring paragraph at lines ~60–83 from the OLD `claude -p` rationale to the NEW queue-write rationale (one paragraph; describe that ledger redirection now flows through the next-flow `claude -p` invocation, not the SessionEnd hook).

**`setup_wizard.py`** comment at line ~444 — replace the SessionEnd-hook-runs-capture-corrections one-liner with a one-liner about the queue-write shape.

**`README.md`** at line ~197 — update the user-facing description of the SessionEnd hook.

**`tests/e2e/run_e2e_flows.py`** at lines ~1167–1179 — replace the Flow 4 advisory's "deferred to plan-156" wording with "landed in plan-156 (PR A); cross-flow ledger assertion via queue drain deferred to PR B."

**`.claude/skills/bicameral-capture-corrections/SKILL.md`** — delete the file (`git rm`).

### Unit Tests

- `tests/test_session_end_hook_drift.py::test_settings_json_session_end_invokes_queue_writer` (new) — load `.claude/settings.json`, extract the SessionEnd hook command, assert `scripts/hooks/session_end_queue_writer.py` is present AND `--auto-ingest` / `claude -p` are absent. Confirms the deployed canonical command is the new shape, which is the assertion that previously took the form `--auto-ingest in cmd` against the old shape.
- `tests/test_setup_wizard.py::test_session_end_command_invokes_queue_writer` (replacement for the deleted hyphen-form test) — read `setup_wizard._BICAMERAL_SESSION_END_COMMAND`, assert `scripts/hooks/session_end_queue_writer.py` is present AND `/bicameral-capture-corrections` is absent. Confirms the constant resolves to the new shape; pivot of the prior issue-#177 regression guard.

The remaining changes in Phase 3 (`_harness_setup.py` docstring + parameter removal, `setup_wizard.py:444` comment, `README.md` description, `run_e2e_flows.py` advisory, `.claude/skills` duplicate deletion) are doc / dead-code / file-deletion changes with no functional behavior to unit-test. The harness's behavior under the new shape is observed through the existing e2e flow runner; the test_session_end_hook_drift.py constant equality test (kept from current file at lines ~53–68) covers `_build_session_end_command`'s no-args call site that the harness now uses.

## CI Commands

- `pytest tests/test_session_end_queue_writer.py -v` — validates Phase 1's queue-writer + helpers + archive CLI (9 tests)
- `pytest tests/test_session_end_hook_drift.py tests/test_setup_wizard.py -v` — validates Phase 3's drift-test reshape and setup_wizard test pivot
- `pytest tests/ -v --no-cov` — full regression sweep (catches any consumer of the legacy `claude -p` SessionEnd command across the repo)
- `mypy events/transcript_queue.py scripts/hooks/session_end_queue_writer.py scripts/hooks/transcript_archive.py setup_wizard.py` — type-check on touched files
- `ruff check . && ruff format --check .` — lint + format
- `grep -rn "claude -p .*capture-corrections" --include="*.py" --include="*.md" --include="*.json" .` — sanity check that the legacy hook command is fully replaced. Expected hits after Phase 1: 0 in `setup_wizard.py` and `.claude/settings.json`; the only remaining references should be in `CHANGELOG.md` (archival) or in PR-specific plan/audit docs at the repo root
- `python3 scripts/hooks/session_end_queue_writer.py < /dev/null` — smoke-check that the writer is invocable path-style from the repo root and exits 0 on empty stdin (fail-soft contract)
