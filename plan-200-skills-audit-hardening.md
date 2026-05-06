# Plan: Skills audit hardening — capture-corrections + ingest + preflight (#200 audit follow-up)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: render_source_attribution
  home: .bicameral/config.yaml (example)
- term: preflight_bypass_tracking
  home: .bicameral/config.yaml (example)
- term: signer_email_fallback
  home: .bicameral/config.yaml (example)

**boundaries**:
- limitations: This plan adds three deterministic config knobs that gate runtime behavior at server-side / config-load time, plus per-ingest consent toggles. It does NOT introduce server-side filtering for `.bicameral/config.yaml` keys-only extraction (that's #205 Phase 3 territory — PR #204's instruction-level mechanism stands until then). The A7 telemetry transparency notes added in Phase 1 are skill-text-only and explicitly do not claim governance status — they're tracked as instruction-level under the doctrine deferred to #205. (example)
- non_goals: MCP-tool-wrapping for `transcript_archive.py` or `session_end_queue_writer.py` (#205 territory); server-side enforcement of redaction defaults via filter; CI lint for skill-governance patterns (#205 Phase 1); compliance-stance-matrix.md authoring (#205 Phase 1); retroactive lift of #204's mechanism to deterministic (#205 Phase 3).
- exclusions: PR #204's just-merged `.bicameral/config.yaml` keys-only-default mechanism is preserved as-is; this plan does NOT modify the bug-report skill's Step 2 / Step 3.5 default-behavior mechanism. Bug-report A7 transparency note added in Phase 1 sits alongside the existing consent gate without changing the governance shape. (example)

## Open Questions

None. All design choices resolved during dialogue:
- Q1 plan shape → single bundled plan, three phases, each phase ships as its own PR (consistent with plan-156's Phase 1/2/3 → PR A/B convention).
- Q2 determinism posture → max-deterministic-where-tractable: introduce three config fields (`render_source_attribution`, `preflight_bypass_tracking`, `signer_email_fallback`) that gate behavior at config-load time + setup-wizard-rendered OS-specific hook command + per-ingest `AskUserQuestion` consent toggle. Skip MCP-tool-wrapping (deferred to #205).
- Q3 doc tier → standard (introduces three config-schema terms that need glossary placement).

## Phase 1: Capture-corrections + report-bug A1/A7 hardening (PR A)

Closes A1 (Windows portability) for the SessionEnd hook command via deterministic OS detection in `setup_wizard.py`, and adds A7 telemetry transparency notes adjacent to consent gates in two skills.

### Affected Files

- `tests/test_setup_wizard_session_end_os_detection.py` **new** — new behavioral tests (4 tests) for the OS-aware SessionEnd hook command rendering.
- `setup_wizard.py` — `_build_session_end_command(mcp_config_path: pathlib.Path | None = None) -> str` gains an optional `platform: str | None = None` parameter (defaults to `sys.platform` when None). Branches the rendered command: POSIX systems (`linux`, `darwin`) get the existing `[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && BICAMERAL_SESSION_END_RUNNING=1 python3 scripts/hooks/session_end_queue_writer.py || true` shape; Windows (`win32`) gets a cmd.exe-compatible shape using `python` (no `3` suffix) and Windows-shell conditional construction. The OS detection happens at install time when the wizard writes `.claude/settings.json`, so the hook command in the user's settings is OS-correct from the moment it's written — no runtime detection.
- `skills/bicameral-capture-corrections/SKILL.md` — two edits:
  1. Lines 84 + 164 (the agent-invoked `python3 scripts/hooks/transcript_archive.py <basename>.jsonl` references): replace `python3` with `python`. Modern distros default `python` to Python 3; Windows MinGW only has `python`. This is the instruction-level fallback for the SKILL-invoked archive helper (the SessionEnd hook command at line 284 is now rendered by `setup_wizard` and is OS-correct from install).
  2. Lines 217–229 (SessionEnd batch consent prompt): add a one-line note immediately above the `AskUserQuestion` block: `> This skill emits skill_begin/skill_end telemetry counters (no content). Disable with BICAMERAL_TELEMETRY=0 to opt out.` Suggestive only — explicitly does not claim governance status per the doctrine deferred to #205.
- `skills/bicameral-report-bug/SKILL.md` — Step 3.5 transparency preview gains the same one-line telemetry note immediately above the consent `AskUserQuestion`. Mirrors the Phase 1 capture-corrections edit so the two skills present a consistent telemetry-disclosure shape.

### Changes

**`setup_wizard.py`** — extend `_build_session_end_command` signature and add a private helper `_session_end_command_for_platform(platform: str) -> str`:

```python
def _session_end_command_for_platform(platform: str) -> str:
    """Return the SessionEnd hook command for the given platform.

    Platform values: 'linux', 'darwin', 'win32', 'cygwin'. Anything else
    falls back to POSIX shape (most likely correct; falls back to broken-
    shell behavior if wrong, which is the same failure mode the prior
    hardcoded shape had on non-POSIX systems anyway).

    Windows shape uses `python` (Windows installers don't symlink
    `python3`) and cmd.exe conditional via `if exist .bicameral`. The
    `BICAMERAL_SESSION_END_RUNNING` re-entrancy guard is preserved
    cross-platform via `set` / `[ -z ]` on the respective shells.
    """
    if platform == "win32":
        return (
            'if exist .bicameral if not defined BICAMERAL_SESSION_END_RUNNING '
            "(set BICAMERAL_SESSION_END_RUNNING=1 && "
            "python scripts\\hooks\\session_end_queue_writer.py)"
        )
    return (
        '[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && '
        "BICAMERAL_SESSION_END_RUNNING=1 "
        "python scripts/hooks/session_end_queue_writer.py || true"
    )


def _build_session_end_command(
    mcp_config_path: pathlib.Path | None = None,
    platform: str | None = None,
) -> str:
    """Canonical SessionEnd hook command rendered for the target platform.

    [docstring carried forward from existing post-#156 implementation]
    """
    target = platform if platform is not None else sys.platform
    return _session_end_command_for_platform(target)
```

Note that POSIX shape switches `python3` → `python`. Modern Linux distros (Ubuntu 22.04+, Debian 12+, Fedora 38+, RHEL 9+) default `python` to Python 3; older systems retain a `python` symlink commonly. The existing `python3` literal was the historical safe choice but is now the failure mode on Windows MinGW. Switching to `python` cross-platform-from-POSIX-perspective is the lower-risk path.

**`skills/bicameral-capture-corrections/SKILL.md`** — replace `python3` with `python` at lines 84 and 164. Telemetry note insertion: above the SessionEnd batch `AskUserQuestion` (around line 217) add:

```markdown
> **Note**: this skill emits `skill_begin` / `skill_end` telemetry events with `g11_*` diagnostic counters (counts only, no content). Set `BICAMERAL_TELEMETRY=0` to opt out before invoking the skill.
```

**`skills/bicameral-report-bug/SKILL.md`** — same telemetry note inserted in Step 3.5 immediately above the `AskUserQuestion` block (around line 217 of the post-#204 file).

### Unit Tests

- `tests/test_setup_wizard_session_end_os_detection.py::test_session_end_command_posix_uses_python_not_python3` — call `_build_session_end_command(platform="linux")`; assert returned string contains `python scripts/hooks/session_end_queue_writer.py` AND does NOT contain `python3 scripts/hooks/`. Confirms the cross-platform `python` switch on POSIX path.
- `tests/test_setup_wizard_session_end_os_detection.py::test_session_end_command_darwin_matches_posix_shape` — call `_build_session_end_command(platform="darwin")`; assert the returned string matches the linux shape exactly. Confirms macOS uses POSIX rendering.
- `tests/test_setup_wizard_session_end_os_detection.py::test_session_end_command_win32_uses_cmd_exe_shape` — call `_build_session_end_command(platform="win32")`; assert returned string contains `if exist .bicameral`, `if not defined BICAMERAL_SESSION_END_RUNNING`, `set BICAMERAL_SESSION_END_RUNNING=1`, `python scripts\hooks\session_end_queue_writer.py`. Asserts ABSENCE of `[ -d`, `[ -z`, and the POSIX `&& ... || true` chaining. Confirms Windows shape is cmd.exe-compatible, not bash.
- `tests/test_setup_wizard_session_end_os_detection.py::test_session_end_command_no_platform_arg_uses_sys_platform` — call `_build_session_end_command()` (no platform arg); assert returned shape is consistent with `sys.platform` at test-run time (linux on CI). Confirms the default-platform fallback path.

## Phase 2: Ingest A4 — per-ingest verbatim toggle + signer-email config knob (PR B)

Adds a one-time-per-session `AskUserQuestion` warning before the first ingest of the session (mirrors PR #204's keys-only-default + opt-in pattern, applied to ingest payloads), plus a new `signer_email_fallback` config field with deterministic server-side enforcement.

### Affected Files

- `tests/test_signer_email_fallback.py` **new** — new behavioral tests (3 tests) for the three resolution modes (`redact`, `local-part-only`, `full`).
- `tests/test_context_ingest_warning_seen.py` **new** — new tests (2 tests) for the session-scoped `seen_ingest_warning` flag (set/get behavior).
- `context.py` — add `signer_email_fallback: Literal["redact", "local-part-only", "full"]` field to the `BicameralContext` model (or wherever the config-loaded model lives in `context.py`); read from `.bicameral/config.yaml` with default `"local-part-only"` (privacy-positive default — exposes the recipient's git identity prefix but not the email host, which is sufficient for "who proposed this" attribution without leaking a directly-mailable address). Also add a session-scoped `seen_ingest_warning: bool` (default False, set to True after first session warning). (example)
- `setup_wizard.py` — when writing fresh `.bicameral/config.yaml`, include `signer_email_fallback: local-part-only` as a default key. Existing installs without the key resolve via the model default at config-load. (example)
- `handlers/ingest.py` (or wherever `bicameral.ingest` resolves the signer) — honor `signer_email_fallback` before writing the `signer` field on a ratified record. The fallback chain at `skills/bicameral-ingest/SKILL.md:841` shifts from "git user email as final fallback" to "git user email transformed by `signer_email_fallback` as final fallback." For `redact`: write literal `<REDACTED>`. For `local-part-only`: write the part before `@`. For `full`: write the email verbatim (legacy behavior; opt-in only).
- `skills/bicameral-ingest/SKILL.md` — three edits:
  1. Add a new Step 0.6 ("Pre-ingest leak warning") near the top of the canonical-rubric section that fires `AskUserQuestion` once per session (gated on the `seen_ingest_warning` flag the server tracks). The warning surfaces: *"Ingested decisions persist verbatim source quotes (`source_excerpt`, `span.text`) to the local ledger. In team mode, these get committed to git via the JSONL event substrate. Continue?"* with options `Yes (don't ask again this session)` / `Yes (ask again next ingest)` / `Cancel this ingest`. Defense-in-depth: even with a "yes, don't ask again" the ingest payload still passes the existing secret-redaction regex (#204 mechanism).
  2. Update the signer-fallback chain documentation at line 841 to reference `signer_email_fallback` config field, with the three modes documented inline. The skill text reads the config field and applies it; the server-side enforcement is the deterministic gate.
  3. Add a one-line transparency note above the existing first `AskUserQuestion` (line 92): same shape as Phase 1's capture-corrections / report-bug notes.

### Changes

**`context.py`** — add `signer_email_fallback` to the config model with `Literal` type-narrowing:

```python
from typing import Literal

# inside BicameralContext (or equivalent config dataclass)
signer_email_fallback: Literal["redact", "local-part-only", "full"] = "local-part-only"
seen_ingest_warning: bool = False
```

The `seen_ingest_warning` flag is session-scoped (in-memory only, not persisted). Persistence across sessions would require ledger storage; v1 just re-warns once per fresh session, which is the right tradeoff.

**`setup_wizard.py`** — the YAML/JSON writer adds `signer_email_fallback: local-part-only` to the fresh-install config dict.

**`handlers/ingest.py`** — wrap the existing signer-resolution fallback chain. Pseudocode:

```python
def _resolve_signer(...) -> str:
    # ... existing chain returns either a non-fallback signer or a git email ...
    if isinstance(signer, GitEmailFallback):
        mode = ctx.signer_email_fallback
        if mode == "redact":
            return "<REDACTED>"
        if mode == "local-part-only":
            return signer.email.split("@", 1)[0]
        return signer.email  # mode == "full"
    return signer
```

The `GitEmailFallback` sentinel marks "this came from git config user.email"; existing callers that returned the email directly are refactored to return the sentinel and let `_resolve_signer` apply the policy.

**`skills/bicameral-ingest/SKILL.md`** — Step 0.6 insertion text:

```markdown
### Step 0.6 — Pre-ingest leak warning (#200 A4)

Before the first ingest of the current session, fire an `AskUserQuestion` warning the operator that ingested `source_excerpt` / `span.text` quotes persist to the ledger and (in team mode) commit to git. Skip this step if the server reports `seen_ingest_warning=True` for the current session.

The warning surfaces:

> **Heads up**: this ingest will record verbatim source quotes from your transcripts/PRDs/Slack threads to the bicameral ledger. In **team mode** (`.bicameral/config.yaml: mode: team`), these quotes are committed to git via the JSONL event substrate, making them visible to every collaborator with repo access. Continue?

Options:
- `Yes — proceed with this ingest, don't ask again this session`
- `Yes — proceed but warn me before each ingest`
- `Cancel this ingest`

When the operator picks the second option, the server's `seen_ingest_warning` flag is reset on the next ingest call so the warning fires again. When the first option is picked, the flag stays True for the rest of the session. Defense-in-depth: even with consent, the ingested payload still passes the existing secret-redaction regex (the same one PR #204 wired in Step 2 of `bicameral-report-bug`).
```

Lines 92, 316, 489, 506, 686, 786 (the six existing `AskUserQuestion` consent gates per the audit subagent's report) gain the same one-line telemetry note pattern from Phase 1.

### Unit Tests

- `tests/test_signer_email_fallback.py::test_redact_mode_returns_redacted_literal` — call the signer-resolution helper (the `_resolve_signer`-equivalent) with a `GitEmailFallback("user@example.com")` sentinel and `signer_email_fallback="redact"`; assert the return value is `"<REDACTED>"`. Confirms redact mode strips both local-part and host.
- `tests/test_signer_email_fallback.py::test_local_part_only_mode_strips_host` — same helper with `GitEmailFallback("user@example.com")` and mode `"local-part-only"`; assert return value is `"user"`. Confirms the privacy-positive default shape preserves attribution prefix without the directly-mailable host.
- `tests/test_signer_email_fallback.py::test_full_mode_returns_verbatim_email` — same helper with mode `"full"`; assert return value is `"user@example.com"` exactly. Confirms the legacy / explicit-opt-in path is preserved.
- `tests/test_context_ingest_warning_seen.py::test_seen_ingest_warning_default_is_false` — instantiate fresh `BicameralContext`; assert `seen_ingest_warning is False`. Confirms a fresh session always shows the warning at first ingest.
- `tests/test_context_ingest_warning_seen.py::test_seen_ingest_warning_set_to_true_persists_within_session` — instantiate context, set `seen_ingest_warning = True`, retrieve again; assert `True`. Confirms the in-memory flag honors set/get within a session (not persisted across sessions).

## Phase 3: Preflight render-attribution + bypass-tracking config knobs (PR C)

Two new config fields gate preflight's privacy-sensitive behaviors at config-load time. Skill text just reads the config and renders accordingly; the deterministic gate is the config field, not the skill instruction.

### Affected Files

- `tests/test_preflight_render_source_attribution.py` **new** — new behavioral tests (3 tests) for the three rendering modes (`full`, `redacted`, `hidden`) of source attribution lines in preflight surfacing.
- `tests/test_preflight_bypass_tracking.py` **new** — new tests (2 tests) for the `enabled` / `disabled` modes of `bicameral.record_bypass`.
- `context.py` — add `render_source_attribution: Literal["full", "redacted", "hidden"]` (default `"redacted"` — privacy-positive: shows `<NAME_REDACTED>`, `<DATE_REDACTED>` placeholders preserving the structural shape but stripping personal details) and `preflight_bypass_tracking: Literal["enabled", "disabled"]` (default `"enabled"` for backward-compat with the existing `~/.bicameral/preflight_events.jsonl` write behavior; lift to `"disabled"` default in a future deprecation cycle if telemetry shows low value).
- `setup_wizard.py` — fresh installs write both fields with their privacy-positive defaults to `.bicameral/config.yaml`. (example)
- `handlers/preflight.py` — `_region_anchored_preflight` (and any other path that returns surfaced decisions to the skill) gains a final transformation step: read `ctx.render_source_attribution`; for `"redacted"` mode replace `source_ref` patterns matching `\w+ \d{4}-\d{2}-\d{2}` (e.g. `Brian 2026-03-22`) with `<NAME_REDACTED> <DATE_REDACTED>`; for `"hidden"` mode strip the `source_ref` field entirely from each decision; for `"full"` mode return verbatim. The transformation is server-side: the agent gets pre-filtered output and renders it as instructed.
- `handlers/record_bypass.py` — gate the JSONL write on `ctx.preflight_bypass_tracking == "enabled"`. When disabled, the handler returns `{recorded: false, reason: "tracking disabled in config"}` without touching disk. Skill code that reads recent bypass events also no-ops cleanly when tracking is disabled (no events to read = no escalation drop, which matches the user's privacy choice).
- `skills/bicameral-preflight/SKILL.md` — three edits:
  1. Lines 287–323 (the surfacing template): replace the verbatim render instruction with a reference to `render_source_attribution` config field; the skill text says "render whatever the server returns in `source_ref` (server applies `render_source_attribution` policy)."
  2. Line 360 / 372 (bypass-event disk-write surface): add to the Rules section a line documenting `preflight_bypass_tracking` config field and what it gates.
  3. Lines 348–361, 388–398, 429–451 (three existing `AskUserQuestion` consent gates): add the same one-line telemetry transparency note pattern from Phase 1.

### Changes

**`context.py`** — extend the config model:

```python
render_source_attribution: Literal["full", "redacted", "hidden"] = "redacted"
preflight_bypass_tracking: Literal["enabled", "disabled"] = "enabled"
```

**`handlers/preflight.py`** — add a private helper `_apply_attribution_policy(decisions: list[Decision], mode: str) -> list[Decision]` that returns transformed decisions per the mode; call it as the final step before returning the preflight response. The transformation is pure (input → output) so it's straightforward to unit-test.

**`handlers/record_bypass.py`** — gate the existing write logic on the config flag.

**`skills/bicameral-preflight/SKILL.md`** — Step 4 / surfacing template line documenting the config-field-driven render is added inline. Skill instruction reads:

```markdown
**Source attribution rendering**: the `source_ref` field returned by `bicameral.preflight` is already pre-filtered server-side per the operator's `render_source_attribution` config setting (`full` | `redacted` | `hidden`, default `redacted`). Render whatever the server returned verbatim — no further redaction needed at the skill layer, and no inference of original values from the redacted form.
```

The bypass-tracking note added to the Rules section reads:

```markdown
**Bypass-tracking**: `bicameral.record_bypass` writes a JSONL event to `~/.bicameral/preflight_events.jsonl` ONLY when `preflight_bypass_tracking: enabled` in `.bicameral/config.yaml` (default). Set to `disabled` to fully opt out of preflight bypass-event persistence. (example)
```

### Unit Tests

- `tests/test_preflight_render_source_attribution.py::test_full_mode_passes_through_verbatim` — call `_apply_attribution_policy` with a list of two decisions whose `source_ref` is `"Brian 2026-03-22"` and `"Sprint 14 architecture review · Ian, 2026-03-12"`, mode `"full"`; assert the returned `source_ref` values are byte-identical to inputs. Confirms verbatim pass-through.
- `tests/test_preflight_render_source_attribution.py::test_redacted_mode_replaces_name_and_date_patterns` — same input, mode `"redacted"`; assert returned values are `"<NAME_REDACTED> <DATE_REDACTED>"` and `"<NAME_REDACTED> architecture review · <NAME_REDACTED>, <DATE_REDACTED>"` (or equivalent — exact patterns determined at implement time). Confirms structural-shape preservation with detail stripping.
- `tests/test_preflight_render_source_attribution.py::test_hidden_mode_strips_source_ref_field_entirely` — same input, mode `"hidden"`; assert returned decisions have NO `source_ref` field (or have it set to `None` / empty string per the chosen API shape). Confirms full hide.
- `tests/test_preflight_bypass_tracking.py::test_record_bypass_writes_event_when_enabled` — call `record_bypass(...)` with config setting `preflight_bypass_tracking="enabled"`; assert the JSONL file at `~/.bicameral/preflight_events.jsonl` (redirected to a tmp dir for test isolation) contains exactly one new event line. Confirms enabled-path actually writes.
- `tests/test_preflight_bypass_tracking.py::test_record_bypass_no_op_when_disabled` — same call with `preflight_bypass_tracking="disabled"`; assert (a) the return value's `recorded=False`, (b) the JSONL file is empty / unchanged. Confirms disabled-path takes no filesystem action.

## CI Commands

- `pytest tests/test_setup_wizard_session_end_os_detection.py tests/test_signer_email_fallback.py tests/test_context_ingest_warning_seen.py tests/test_preflight_render_source_attribution.py tests/test_preflight_bypass_tracking.py -v` — runs all 14 new tests across the three phases.
- `pytest tests/ -v --no-cov --ignore=tests/e2e` — full non-e2e regression sweep. No tests in this set should change behavior; sanity check.
- `python scripts/lint_plan_grounding.py plan-200-skills-audit-hardening.md` — runs PR #121's grounding lint against this plan. Self-test exit 0 expected.
- `ruff check setup_wizard.py context.py handlers/ingest.py handlers/preflight.py handlers/record_bypass.py && ruff format --check setup_wizard.py context.py handlers/ingest.py handlers/preflight.py handlers/record_bypass.py` — lint + format on touched files.
- `mypy setup_wizard.py context.py handlers/ingest.py handlers/preflight.py handlers/record_bypass.py` — type-check on touched files.
