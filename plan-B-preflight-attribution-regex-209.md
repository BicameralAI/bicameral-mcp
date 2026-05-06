# Plan B: refine `render_source_attribution` redaction regex + flip default (#209)

**change_class**: feature

**doc_tier**: minimal

**high_risk_target**: false

**terms_introduced**: none (the refined plan introduces no new term-as-object; it replaces existing private regexes with more precise siblings)

**boundaries**:
- limitations:
  - The refined regex is a heuristic, not a formal grammar over source_ref shapes. It targets the documented shape `"<context-words> · <Name>, <date>"` produced by the e2e harness ingest path; ingests with non-standard structures may still over- or under-redact. Platform/tool tokens (Sprint, Linear, GitHub, etc.) survive redaction by design — the positional-cue patterns require explicit cues (`· `, `, `, `Speaker:`, `From:`) to fire, which context tokens never follow.
- non_goals:
  - Cryptographic name-hash (would defeat operator-readable redaction).
  - Per-locale name detection (e.g., non-Latin scripts).
  - Reverse-mapping a redacted source_ref back to its original (intentional one-way transform).
- exclusions:
  - The `hidden` mode (already correct — blank string).
  - The `full` mode (passes through verbatim — no transformation).

## Open Questions

None at plan time. The issue body is explicit about which tokens to preserve and which to redact; the e2e Flow 3 acceptance bound is concrete.

## Phase 1: Refine regex + curated allowlist + flip default

### Affected Files

- `tests/test_preflight_attribution_redaction.py` (new) — functionality tests covering the refined regex against real source_ref shapes (the example from the issue body, plus 4–6 representative real-world strings); locks the preserved-vs-redacted boundaries
- `handlers/preflight.py` —
  - Replace `_NAME_PATTERN = re.compile(r"\b[A-Z][a-z]+\b")` with four positional-cue patterns (after `· `, after `, ` adjacent to a date, after `Speaker:` prefix, after `From:` prefix)
  - Refine `_apply_attribution_policy` redacted branch to apply the new patterns in sequence
- `context.py` — flip `_DEFAULT_RENDER_ATTRIBUTION_MODE = "full"` → `"redacted"` (line 28)
- `setup_wizard.py` — line 1005: change `"render_source_attribution: full\n"` → `"render_source_attribution: redacted\n"` for fresh installs

### Changes

#### Refined regex strategy

Replace the broad `_NAME_PATTERN` with three positional-cue patterns:

```python
# Names appearing after `· ` (decorative bullet separator before attribution)
_NAME_AFTER_BULLET = re.compile(r"(?<=· )[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*")

# Names appearing after `, ` (comma-separator before name+date)
_NAME_AFTER_COMMA = re.compile(r"(?<=, )[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?=,?\s+\d{4}-\d{2}-\d{2})")

# Names in `Speaker: Name` or `From: Name` prefix shape
_SPEAKER_NAME = re.compile(r"(?<=^Speaker:\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", re.MULTILINE)
_FROM_NAME = re.compile(r"(?<=^From:\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", re.MULTILINE)
```

These match the documented attribution shapes without consuming context-word capitalizations like "Sprint 14" or "Linear board."

#### Why no platform-token allowlist

A curated frozenset of platform/tool names ("Sprint", "Linear", "GitHub", etc.) was considered as a defense-in-depth post-redaction restoration step. **Rejected** for this PR per round-1 audit finding 1 (`specification-drift`):

- The four positional-cue patterns above require explicit cues to fire (`· `, `, ` adjacent to a date, `^Speaker:\s`, `^From:\s`). Context tokens like "Sprint", "Linear", "GitHub" appearing in `<context-words>` position never follow these cues, so the patterns never over-match them by construction.
- Adding a post-redaction restoration loop adds fragile post-hoc string replacement to handle a case the positional patterns don't trigger — complexity without provable value.
- The test suite directly verifies this: `test_redacted_mode_preserves_platform_tokens`, `test_redacted_mode_preserves_capitalized_context_words` invoke `_apply_attribution_policy` with platform tokens in context-word position and assert passthrough. If the positional patterns ever over-match in practice, those tests fail and the regex gets refined — not a band-aid allowlist added.

If a future ingest source produces a shape where platform tokens DO follow positional cues (e.g., `"From: Sprint Bot"`), the right fix is to refine the positional regex or add a per-token negative lookahead — not a global allowlist that fights the regex.

Actual implementation order in `_apply_attribution_policy`'s `redacted` branch:

```python
stripped = _DATE_PATTERN.sub("<DATE_REDACTED>", m.source_ref)
redacted = _NAME_AFTER_BULLET.sub("<NAME_REDACTED>", stripped)
redacted = _NAME_AFTER_COMMA.sub("<NAME_REDACTED>", redacted)
redacted = _SPEAKER_NAME.sub("<NAME_REDACTED>", redacted)
redacted = _FROM_NAME.sub("<NAME_REDACTED>", redacted)
new_source_ref = redacted
```

Date pattern unchanged (`\b\d{4}-\d{2}-\d{2}\b` is correct).

#### Flip default

```python
# context.py
_DEFAULT_RENDER_ATTRIBUTION_MODE = "redacted"  # was "full"
```

```python
# setup_wizard.py:1005
"render_source_attribution: redacted\n"  # was "full"
```

The deterministic gate is in place; flipping the default is the privacy-positive move that #200 audit finding A4 directed.

### Unit Tests

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_redacts_name_after_bullet_separator` — invokes `_apply_attribution_policy([DecisionMatch(source_ref="Sprint 14 architecture review · Ian, 2026-03-12")], "redacted")`; asserts result `source_ref == "Sprint 14 architecture review · <NAME_REDACTED>, <DATE_REDACTED>"`. The "Sprint" platform token survives; "Ian" is redacted.

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_preserves_platform_tokens` — invokes against `"Linear board issue #143 · Bob"`; asserts "Linear" preserved, "Bob" redacted.

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_redacts_speaker_prefix` — invokes against `"Speaker: Alice Bobson"` (multi-line, `re.MULTILINE` shape); asserts redaction.

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_redacts_from_prefix` — invokes against `"From: Charlie\nBody text"`; asserts "Charlie" redacted but "Body" preserved (capitalization at start-of-line not after `From:` shouldn't trigger).

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_preserves_capitalized_context_words` — invokes against `"GitHub PR #229 review notes · Eve, 2026-04-10"`; asserts "GitHub", "PR", "review", "notes" preserved (none follow positional cues for name); "Eve" redacted; date redacted.

- `tests/test_preflight_attribution_redaction.py::test_redacted_mode_handles_no_attribution_shape` — invokes against a source_ref with no positional cues like `"Decision context: implement feature X"`; asserts no transformation (full passthrough since no positional cue triggered).

- `tests/test_preflight_attribution_redaction.py::test_default_render_attribution_mode_is_redacted` — imports `context._DEFAULT_RENDER_ATTRIBUTION_MODE`; asserts equality with `"redacted"`. (Locks the default-flip contract; without the test, a future revert could silently regress.)

- `tests/test_preflight_attribution_redaction.py::test_setup_wizard_fresh_install_writes_redacted_default` — invokes `setup_wizard._write_collaboration_config(tmp_path, mode="standard", guided=False, telemetry=False)` (or whichever signature it currently has — adjust kwargs to match). After the function returns, reads the resulting `.bicameral/config.yaml` file under `tmp_path` and asserts the rendered YAML content contains the substring `"render_source_attribution: redacted"` AND does NOT contain `"render_source_attribution: full"`. The unit under test is `_write_collaboration_config`'s YAML rendering — the assertion is on the rendered file content, not on the source file's literal. Acceptance question: if `_write_collaboration_config` were silently rewritten to emit `full`, would this test fail? YES — it reads the generated artifact, not the source.

- `tests/test_preflight_attribution_redaction.py::test_full_mode_unchanged` — invokes `_apply_attribution_policy([DecisionMatch(source_ref="Sprint 14 review · Ian, 2026-03-12")], "full")`; asserts the source_ref passes through verbatim.

- `tests/test_preflight_attribution_redaction.py::test_hidden_mode_blanks_source_ref` — invokes with `mode="hidden"`; asserts `source_ref == ""`.

## CI Commands

- `python -m pytest tests/test_preflight_attribution_redaction.py -v` — runs the new tests
- `python -m pytest tests/test_preflight* -v` — broader preflight regression
- `python -m pytest -v` — full regression (verifies the default-flip doesn't break any test that depended on `"full"` semantics implicitly)
- `ruff check .` — lint gate
- `ruff format --check .` — format gate
- e2e Flow 3 acceptance: post-merge to dev, the `v0 user flow e2e` workflow runs `mcp_layer + agentic_layer` paths against the new default; passing Flow 3 confirms agent reasoning isn't broken by `redacted` becoming the default
