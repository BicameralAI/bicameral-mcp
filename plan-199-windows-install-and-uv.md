# Plan: #199 — Windows banner UnicodeEncodeError fix + uv-first installer

**change_class**: hotfix

**doc_tier**: standard

**terms_introduced**:
- term: uv tool install
  home: README.md
- term: install resolve chain
  home: handlers/update.py

**boundaries**:
- limitations: Windows console UTF-8 reconfiguration depends on Python ≥ 3.7's `sys.stdout.reconfigure`; CPython on supported Python (≥ 3.10 per pyproject) always provides this. No support for legacy interpreters.
- non_goals: do not add `uv` as a Python dependency; do not vendor a uv install script; do not change the existing pipx-bootstrap "Don't have pipx?" copy in README.
- exclusions: not addressing the original `ModuleNotFoundError` symptom — verified non-reproducible against current HEAD (wheel build + clean-venv install + `import setup_wizard` resolves cleanly). Issue #199 is repurposed in place to track the real Windows crash.

## Open Questions

All resolved during /qor-plan dialogue (2026-05-06):
- **uv detection**: PATH-only via `shutil.which("uv")`. uv is not added to `pyproject.toml`.
- **handlers/update.py:283-285 comment**: expanded in place to spec the three-path resolve order; not replaced.
- **README ordering**: uv block above pipx block; existing pipx-bootstrap section unchanged.
- **change_class**: hotfix (banner crash dominates; uv path is additive).
- **Banner fix shape**: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` guarded for win32, called once from a single helper invoked at the top of `run_setup`, `run_config_wizard`, `run_reset_wizard`.

## Phase 1: Windows banner UTF-8 reconfigure

### Affected Files

- `tests/test_setup_wizard_windows_encoding.py` — **new** functionality test for the encoding helper
- `setup_wizard.py` — add `_ensure_utf8_stdout()` helper; call it at the top of the three banner-printing entry points

### Changes

Add module-level helper near the existing `_build_session_end_command` and other shared helpers:

```python
def _ensure_utf8_stdout(platform: str | None = None) -> None:
    """On Windows, reconfigure stdout/stderr to UTF-8 so the banner
    box-drawing characters at the top of run_setup / run_config_wizard /
    run_reset_wizard don't crash under cp1252.

    No-op on POSIX. The `platform` arg is for test isolation; production
    callers pass None and the helper reads sys.platform.
    """
    target = platform if platform is not None else sys.platform
    if target != "win32":
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
```

Insert one call at the top of each of:
- `run_setup` (setup_wizard.py:1041, before the `print()` at 1042)
- `run_config_wizard` (setup_wizard.py:1156, before the `print()` at 1156)
- `run_reset_wizard` (setup_wizard.py:1297, before the `print()` at 1297)

Each call site: `_ensure_utf8_stdout()`.

### Unit Tests

- `tests/test_setup_wizard_windows_encoding.py`:
  - `test_ensure_utf8_stdout_reconfigures_on_win32` — replace `sys.stdout` with a `_FakeStream` exposing `.reconfigure(encoding, errors)` that records the kwargs; call `setup_wizard._ensure_utf8_stdout(platform="win32")`; assert the recorded encoding is `"utf-8"` and errors mode is `"replace"`. Functionality test — invokes the unit and asserts on observable side-effect.
  - `test_ensure_utf8_stdout_noop_on_posix` — same `_FakeStream` setup; call with `platform="linux"` and `platform="darwin"`; assert reconfigure was NOT invoked. Functionality test — invokes the unit and verifies negative behavior.
  - `test_ensure_utf8_stdout_silent_when_reconfigure_missing` — `_FakeStream` without a `.reconfigure` attribute; call with `platform="win32"`; assert no exception is raised. Functionality test.
  - `test_ensure_utf8_stdout_silent_on_oserror` — `_FakeStream.reconfigure` raises `OSError`; call with `platform="win32"`; assert no exception escapes. Functionality test.
  - `test_run_setup_banner_does_not_crash_under_cp1252` — replace `sys.stdout` with a `_FakeStream` whose `.write` raises `UnicodeEncodeError` for any non-ASCII byte BEFORE `_ensure_utf8_stdout` runs but accepts UTF-8 after; mock `_detect_repo` to short-circuit `run_setup` after the banner; invoke `setup_wizard.run_setup(...)`; assert it returns without `UnicodeEncodeError`. Functionality test — proves the helper actually unblocks the failing call site, not just that it exists.

## Phase 2: uv-first resolve chain in handlers/update.py

### Affected Files

- `tests/test_update_resolve_chain.py` — **new** functionality test for the resolve-chain selection
- `handlers/update.py` — extend the resolver at lines 281-291 to a three-path order; expand the existing comment to document the new spec

### Changes

Replace the current pipx/pip if-else at handlers/update.py:286-291 with a deterministic three-path resolver. Lift the resolver into a small named helper so it's unit-testable without invoking `subprocess.run`:

```python
def _resolve_install_command(target: str) -> list[str]:
    """Resolve the installer command for `target` (e.g. "bicameral-mcp==1.2.3").

    Order is deterministic and PATH-driven (no environment heuristics):
      1. `uv tool install --force <target>` — preferred. uv ships as a
         single static binary, has no Python prerequisite, and `uv tool`
         is the canonical CLI-app installer in the uv ecosystem.
      2. `pipx install --force <target>` — fallback when uv is absent.
         Manages its own venv and handles externally-managed-environment
         restrictions on macOS.
      3. `<sys.executable> -m pip install --quiet <target>` — last-resort
         path for venv/dev installs where neither uv nor pipx is present.
    """
    if shutil.which("uv"):
        return ["uv", "tool", "install", "--force", target]
    if shutil.which("pipx"):
        return ["pipx", "install", target, "--force"]
    return [sys.executable, "-m", "pip", "install", target, "--quiet"]
```

Replace the inline `if shutil.which(...) ... else ...` block at handlers/update.py:286-291 with `cmd = _resolve_install_command(target)`. The `import shutil` at line 286 stays (used by the helper); `subprocess.run(cmd, ...)` at line 292 is unchanged.

### Unit Tests

- `tests/test_update_resolve_chain.py`:
  - `test_resolve_uv_when_uv_on_path` — `monkeypatch.setattr("handlers.update.shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else "/usr/bin/pipx")`; call `_resolve_install_command("bicameral-mcp==1.2.3")`; assert returned cmd is `["uv", "tool", "install", "--force", "bicameral-mcp==1.2.3"]`. Asserts the full cmd list, not just `cmd[0]`.
  - `test_resolve_pipx_when_uv_missing` — `which` returns None for "uv" and a path for "pipx"; assert returned cmd is `["pipx", "install", "bicameral-mcp==1.2.3", "--force"]`.
  - `test_resolve_pip_when_uv_and_pipx_missing` — `which` returns None for both; assert returned cmd starts with `[sys.executable, "-m", "pip", "install"]` and contains `"bicameral-mcp==1.2.3"` and `"--quiet"`.
  - `test_resolve_uv_wins_over_pipx_when_both_present` — both `which` lookups return paths; assert `cmd[0] == "uv"` (proves priority, not just availability).

## Phase 3: surface alignment — README, skill text, issue repurpose

### Affected Files

- `README.md` — add uv install block above the existing pipx Quickstart block
- `skills/bicameral-update/SKILL.md` — extend Step 3 description to reflect three-path resolve order; description field already partially updated this session
- `CHANGELOG.md` — Unreleased / Fixed entry for the Windows crash; Unreleased / Added entry for uv install path
- (manual operator action) `gh issue edit 199` — retitle and rewrite body

### Changes

**README.md** — at the Quickstart section (around line 85), prepend a uv block. Resulting structure:

```markdown
## Quickstart

The fastest path is uv. If you don't have uv yet, the official installer is one line:

​```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
​```

Then:

​```bash
uv tool install bicameral-mcp
bicameral-mcp setup
​```

Prefer pipx? That works too:

​```bash
pipx install bicameral-mcp
bicameral-mcp setup
​```
```

The existing "Don't have pipx?" subsection at line 98 is preserved unchanged.

**skills/bicameral-update/SKILL.md** — Step 3 already says "Reinstall the package at `=={recommended_version}` — `pipx install --force` when pipx is on PATH (the canonical install), otherwise `pip install` as a fallback for venv/dev installs". Replace with: "Reinstall the package at `=={recommended_version}`. The server resolves the installer in priority order: `uv tool install --force` if `uv` is on PATH, else `pipx install --force` if `pipx` is on PATH, else `pip install` as a venv/dev fallback. The chosen path is reported in error messages." Update the description front-matter `pipx preferred / pip fallback` → `uv preferred, pipx fallback, pip last-resort`.

**CHANGELOG.md** — under `[Unreleased]`:
- `### Fixed` — `Windows: bicameral-mcp setup / config / reset crashed with UnicodeEncodeError on the banner box-drawing chars under cp1252 — banner-printing entry points now reconfigure stdout/stderr to UTF-8 on win32. (#199)`
- `### Added` — `Installer resolve chain now prefers uv tool install over pipx; pip remains the last-resort fallback. README adds a uv quickstart block as the recommended install path. (#199)`

**gh issue edit 199** (operator runs from PR description, not automated) — retitle to `Install: bicameral-mcp setup / config / reset crashes on Windows (UnicodeEncodeError on banner)` and replace body with a short note recording the not-reproducible original symptom plus the actual root cause.

### Unit Tests

No new tests for Phase 3 — README copy and skill markdown are LLM-consumed governance text per `qor/references/doctrine-test-functionality.md`'s carve-out; CHANGELOG entries are operator-authored prose. Phase 1 + Phase 2 functionality tests cover the deterministic surface.

## CI Commands

- `pytest tests/test_setup_wizard_windows_encoding.py -v` — validates Phase 1 banner helper behavior + run_setup integration.
- `pytest tests/test_update_resolve_chain.py -v` — validates Phase 2 resolve-chain priority.
- `pytest tests/test_setup_wizard.py tests/test_setup_wizard_session_end_os_detection.py -v` — regression guard on existing setup_wizard tests (helper insertion must not break them).
- `pytest tests/ -k "update" -v` — regression guard on existing update-handler tests.
- `python -m build --wheel --outdir /tmp/wheel-check` then `python -m zipfile -l /tmp/wheel-check/*.whl | grep setup_wizard` — confirms wheel still bundles `setup_wizard.py` (regression guard against the original #199 symptom re-emerging if packaging shifts).
- `ruff check setup_wizard.py handlers/update.py` — lint passes on touched files.
