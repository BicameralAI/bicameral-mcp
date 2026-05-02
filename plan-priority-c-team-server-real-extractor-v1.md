# Plan: Priority C v1.1 — Real heuristic+LLM extractor (replaces interim paragraph-split placeholder)

**change_class**: feature
**doc_tier**: system
**Author**: Governor (executed via `/qor-plan`)
**Risk Grade**: L2 (replaces a placeholder; no new credential surface beyond an Anthropic API key; no IPC paths beyond what Phases 0.5+3 of v1.0 already established; cache-contract gets a column added but stays uniform-shaped across sources)
**Mode**: solo
**Predecessor**: `plan-priority-c-team-server-notion-v1.md` (sealed at META_LEDGER Entry #33; Merkle `dcb61910...`)
**Issue**: none filed

**terms_introduced**:
- term: heuristic classifier
  home: team_server/extraction/heuristic_classifier.py
- term: classification result
  home: team_server/extraction/heuristic_classifier.py
- term: extraction pipeline
  home: team_server/extraction/pipeline.py
- term: corpus learner
  home: team_server/extraction/corpus_learner.py
- term: classifier version
  home: team_server/schema.py
- term: trigger rules
  home: team_server/config.py

**boundaries**:
- limitations:
  - v1.1 ships **claude-haiku-4-5** as the Stage 2 default model. Sonnet/Opus selectable via env (`BICAMERAL_TEAM_SERVER_EXTRACT_MODEL`); no auto-tier-up.
  - Heuristic classifier is **regex/keyword based + reaction/length boosters**. No embedding-similarity classification (deferred to a CocoIndex unparking).
  - Corpus learner reads the **per-team-server local ledger's `decision` table**, not the originating-author per-dev ledgers. The team-server is its own peer; its corpus is what it observes through replay. Cross-deployment learning is not in scope.
  - Decision output schema is minimal: `{"summary": str, "context_snippet": str, "matched_triggers": [str]}`. Richer fields (level / rationale / subjects) are deferred to materializer alignment (separate plan).
  - Anthropic API key sourcing: env var `ANTHROPIC_API_KEY` only. If unset AND any positive classification reaches Stage 2, the team-server fails loud at startup (Phase 4 wiring).
- non_goals:
  - Multi-provider LLM support (OpenAI, etc.). Anthropic only.
  - Per-message confidence scoring as a tunable threshold in v1.1 (the `is_positive` boolean from heuristic Stage 1 is the gate).
  - LLM-driven heuristic-rule auto-generation. Operator authors rules; corpus learner only suggests learned terms (operator denylist takes precedence).
  - Replacing the canonical-extraction cache contract from v1.0 (still upsert per `(source_type, source_ref)`).
  - Materializer's `event_type='ingest.completed'` vs team-server's `event_type='ingest'` shape mismatch — pre-existing v0 gap, separate plan.
- exclusions:
  - No CocoIndex (#136) work — remains parked from the v0 plan's Phase 5.
  - No new MCP tool surface.
  - No deploy/Dockerfile changes beyond env-var documentation.

## Open Questions

Two flagged at top. Neither blocks Phase 0–4 implementation; Phase 5 (corpus learner) depends on resolution of OQ-1.

1. **OQ-1: Corpus source for the learner** — the team-server has its own SurrealDB; its `decision` table is populated only when peers materialize events back into the team-server's ledger via `/events` pull. But the team-server is not currently configured as a *consumer* of its own `/events` endpoint. Two interpretations:
   - **(a)** Corpus learner reads from the per-team-server local ledger directly (the same tables `slack_runner` and `notion_worker` write to). This requires the team-server to also run an `EventMaterializer` against its own event log; or skip materialization and read directly from `team_event` rows.
   - **(b)** Corpus learner reads from a remote source (e.g., the customer's git-tracked event log via `events/team_adapter.py`). More complex; out of scope for this plan.
   I plan against **(a)** with reading directly from `team_event` rows (no internal materializer). Operator may override.

2. **OQ-2: Materializer event_type mismatch** — `events/materializer.py:89` dispatches on `event_type == 'ingest.completed'`; team-server's `slack_worker` and `notion_worker` write `event_type='ingest'`. Per-dev `EventMaterializer` consuming team-server events would skip them entirely under current code. This is a pre-existing v0 gap; this plan does NOT fix it (separate plan). Flagged because the LLM extractor's output is dead weight in the materializer chain until OQ-2 is resolved. Operator may want to bundle the fix.

## Phase 0: Cache contract gets `classifier_version` column

**Why this phase exists**: Heuristic rules change over time (operator config edits, corpus-learned keywords). The current cache identity `(source_type, source_ref) + content_hash` does not invalidate when rules change — a cached "negative classification" outcome stays cached even after a rule change that would now classify the same text positively. Adding `classifier_version` to the cache row + upsert gate closes the staleness window without changing the source-side primary key shape.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_classifier_version.py::test_upsert_returns_changed_true_when_classifier_version_differs` — creates an extraction_cache row with `classifier_version='v1'`; calls `upsert_canonical_extraction(...)` with the same `(source_type, source_ref, content_hash)` but a new `classifier_version='v2'`; asserts return tuple is `(<new extraction>, True)` and the row's `classifier_version` field is now `'v2'`. Functionality — exercises the second-axis upsert gate.
- [ ] `tests/test_team_server_classifier_version.py::test_upsert_returns_changed_false_when_both_hash_and_version_match` — pre-seeds a row with content_hash and classifier_version; calls upsert with identical values for both; asserts `(<cached>, False)` and the inner compute_fn was not invoked. Functionality — exercises the no-op-when-fully-matched case.
- [ ] `tests/test_team_server_classifier_version.py::test_upsert_returns_changed_true_when_content_hash_differs_classifier_same` — exercises the existing v1.0 axis (content change) is preserved unchanged. Functionality — regression coverage that the new column did not break the v1.0 contract.
- [ ] `tests/test_team_server_schema_migration.py::test_v2_to_v3_migration_adds_classifier_version_column` — runs `ensure_schema` on a v2-shaped ledger (no `classifier_version` column); asserts post-migration that `INSERT extraction_cache CONTENT { ..., classifier_version: 'h-v1' }` succeeds and that pre-existing rows' `classifier_version` defaults to the literal string `legacy-pre-v3`. Functionality — exercises the migration's schema-add behavior.
- [ ] `tests/test_team_server_schema_migration.py::test_v2_to_v3_migration_is_idempotent` — runs ensure_schema twice; asserts no exception and that schema_version row reads 3. Functionality — exercises idempotency under the new migration.

### Affected Files

- `team_server/schema.py` — **MUTATE** — bump `SCHEMA_VERSION` to 3; add `_migrate_v2_to_v3` callable that adds `DEFINE FIELD classifier_version ON extraction_cache TYPE string DEFAULT 'legacy-pre-v3'` and updates pre-existing rows to set the default explicitly (since SurrealDB v2 `DEFAULT` only applies to subsequent CREATEs, not existing rows). Register `_migrate_v2_to_v3` in `_MIGRATIONS`.
- `team_server/extraction/canonical_cache.py` — **MUTATE** — extend `upsert_canonical_extraction` signature with a new required keyword-only argument `classifier_version: str`. Behavior: SELECT now also reads `classifier_version`; cache hit (`changed=False`) requires BOTH content_hash AND classifier_version match; otherwise the row is updated in place to the new content_hash + classifier_version + extraction.
- `team_server/workers/slack_worker.py` — **MUTATE** — pass through `classifier_version` from the pipeline result (Phase 4 wires this; for Phase 0 in isolation, slack_worker gets a hardcoded `classifier_version='legacy-pre-v3'` to keep tests passing — Phase 4 replaces with the real value).
- `team_server/workers/notion_worker.py` — **MUTATE** — same pattern as slack_worker.
- `tests/test_team_server_classifier_version.py` — **CREATE** — 3 functionality tests above.
- `tests/test_team_server_schema_migration.py` — **MUTATE** — add 2 functionality tests above.
- `tests/test_team_server_cache_upsert.py` — **MUTATE** — adapt the existing 4 tests to pass `classifier_version='legacy-pre-v3'` so they continue to pass under the new signature.
- `tests/test_team_server_slack_worker.py` — **MUTATE** — adapt the upsert-stub tests to the new tuple-return signature including classifier_version.

### Changes

`team_server/extraction/canonical_cache.py`:

```python
async def upsert_canonical_extraction(
    client: LedgerClient,
    *,
    source_type: str,
    source_ref: str,
    content_hash: str,
    classifier_version: str,   # NEW: second-axis cache identity
    compute_fn: ComputeFn,
    model_version: str,
) -> tuple[dict, bool]:
    rows = await client.query(
        "SELECT id, content_hash, classifier_version, canonical_extraction "
        "FROM extraction_cache "
        "WHERE source_type = $st AND source_ref = $sr LIMIT 1",
        {"st": source_type, "sr": source_ref},
    )
    if (rows
            and rows[0]["content_hash"] == content_hash
            and rows[0]["classifier_version"] == classifier_version):
        return rows[0]["canonical_extraction"], False
    extraction = await compute_fn()
    if rows:
        await client.query(
            "UPDATE extraction_cache SET content_hash = $ch, "
            "classifier_version = $cv, canonical_extraction = $ext, "
            "model_version = $mv "
            "WHERE source_type = $st AND source_ref = $sr",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "cv": classifier_version, "ext": extraction, "mv": model_version},
        )
    else:
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: $st, source_ref: $sr, "
            "content_hash: $ch, classifier_version: $cv, "
            "canonical_extraction: $ext, model_version: $mv }",
            {"st": source_type, "sr": source_ref, "ch": content_hash,
             "cv": classifier_version, "ext": extraction, "mv": model_version},
        )
    return extraction, True
```

`team_server/schema.py` migration block:

```python
SCHEMA_VERSION = 3

# _BASE_STMTS gains:
"DEFINE FIELD classifier_version ON extraction_cache TYPE string DEFAULT 'legacy-pre-v3'",

async def _migrate_v2_to_v3(client: LedgerClient) -> None:
    """Add classifier_version column with default for new rows; backfill
    existing rows so SELECT returns a defined value, not the SurrealDB
    'NONE' marker that would compare unequal to any real version string."""
    try:
        await client.query(
            "DEFINE FIELD classifier_version ON extraction_cache "
            "TYPE string DEFAULT 'legacy-pre-v3'"
        )
    except Exception as exc:  # noqa: BLE001
        if "already exists" not in str(exc).lower():
            raise
    await client.query(
        "UPDATE extraction_cache SET classifier_version = 'legacy-pre-v3' "
        "WHERE classifier_version IS NONE OR classifier_version = ''"
    )

_MIGRATIONS[3] = _migrate_v2_to_v3
```

---

## Phase 1: Heuristic classifier — pure function over (message, context, rules)

**Why this phase exists**: This is the deterministic Stage 1 that replaces the v0 paragraph-split placeholder for chatter rejection. It runs before any Anthropic API call. Operator-tunable per workspace (option a), per-channel/database overridable (option b), context-aware on Slack reactions and thread position (option d). Option c (corpus-learned terms) integrates here in Phase 5; the merge contract is established now.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_heuristic_classifier.py::test_keyword_match_yields_positive_with_matched_triggers` — feeds a message text containing the keyword "decided"; rules has `keywords=["decided", "agreed"]`; asserts the result is `ClassificationResult(is_positive=True, matched_triggers=["decided"], classifier_version=<expected hash>)`. Functionality — exercises the core keyword-match path.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_no_keyword_match_yields_negative` — message text contains none of the configured keywords; asserts `is_positive=False`, `matched_triggers=[]`. Functionality.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_keyword_negative_overrides_positive` — message contains both a positive keyword AND a negative keyword (e.g., "decided" + "haha just kidding"); rules has both lists; asserts `is_positive=False`. Functionality — exercises the negative-list filter.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_min_word_count_floor_rejects_short_messages` — 2-word message containing a positive keyword; `min_word_count=5`; asserts `is_positive=False`. Functionality — exercises the length floor.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_reaction_boost_flips_negative_to_positive` — message text has no keyword match; context has `reactions=[{"name": "white_check_mark", "count": 2}]`; rules has `boost_reactions=["white_check_mark"]` with `boost_threshold=1`; asserts `is_positive=True`, `matched_triggers=[":white_check_mark:×2"]`. Functionality — exercises the option-d context-aware booster.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_thread_position_booster_for_thread_tail` — message is at position N≥3 in a thread (i.e., thread tail where decisions usually crystallize); rules has `thread_tail_boost: {position_threshold: 3}`; otherwise-borderline message; asserts `is_positive=True` with `matched_triggers=["thread-tail"]`. Functionality — exercises the option-d thread-position signal.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_classification_is_deterministic_for_same_input` — runs `classify(message, context, rules)` twice with identical inputs; asserts byte-identical result tuples (including the same `classifier_version` string). Functionality — exercises the determinism invariant that the classifier's correctness depends on.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_classifier_version_changes_when_rules_change` — runs `classify` with two rule sets that differ in keyword list; asserts the two `classifier_version` strings are different. Functionality — exercises the rules→version derivation that gates cache invalidation.
- [ ] `tests/test_team_server_heuristic_classifier.py::test_unicode_and_emoji_in_text_does_not_crash` — feeds messages with mixed unicode + emoji; asserts the classifier returns a result without raising. Functionality — exercises the input-robustness invariant.

### Affected Files

- `team_server/extraction/heuristic_classifier.py` — **CREATE** — pure functions. Exports: `ClassificationResult` dataclass, `classify(message, context, rules) -> ClassificationResult`, `derive_classifier_version(rules) -> str`. No I/O, no DB.
- `tests/test_team_server_heuristic_classifier.py` — **CREATE** — 9 functionality tests above.

### Changes

`team_server/extraction/heuristic_classifier.py`:

```python
"""Heuristic classifier — pure function over (message, context, rules).

Stage 1 of the extraction pipeline. Decides whether a message is decision-
relevant. Deterministic by construction (no LLM, no temperature). Rules
are operator-configured at the workspace level + channel/database
overrides; merged at classification time by `pipeline.merge_rules`.
Option-c learned terms merge in via the same path; learned-keywords
field of rules is appended to the operator-configured keywords.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ClassificationResult:
    is_positive: bool
    matched_triggers: tuple[str, ...]
    classifier_version: str


@dataclass(frozen=True)
class TriggerRules:
    keywords: tuple[str, ...] = ()
    keyword_negatives: tuple[str, ...] = ()
    min_word_count: int = 0
    boost_reactions: tuple[str, ...] = ()
    boost_threshold: int = 1
    thread_tail_position_threshold: Optional[int] = None  # None = disabled
    learned_keywords: tuple[str, ...] = ()  # filled by Phase 5 corpus learner


def derive_classifier_version(rules: TriggerRules) -> str:
    """Stable hash of the rule set; changes ⇒ cache invalidation downstream."""
    payload = json.dumps({
        "keywords": sorted(rules.keywords),
        "keyword_negatives": sorted(rules.keyword_negatives),
        "min_word_count": rules.min_word_count,
        "boost_reactions": sorted(rules.boost_reactions),
        "boost_threshold": rules.boost_threshold,
        "thread_tail_position_threshold": rules.thread_tail_position_threshold,
        "learned_keywords": sorted(rules.learned_keywords),
        "engine": "heuristic-v1",
    }, sort_keys=True).encode("utf-8")
    return f"heuristic-v1+{hashlib.sha256(payload).hexdigest()[:12]}"


_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def classify(
    message: dict,
    context: dict,
    rules: TriggerRules,
) -> ClassificationResult:
    text = message.get("text", "") or ""
    text_lc = text.lower()
    matched: list[str] = []

    # Negative-list filter runs first; short-circuits to negative if any hit.
    if any(neg.lower() in text_lc for neg in rules.keyword_negatives):
        return ClassificationResult(False, (), derive_classifier_version(rules))

    # Length floor filter.
    word_count = len(_WORD_RE.findall(text))
    if word_count < rules.min_word_count:
        # Only return early-negative if no override booster could rescue.
        # Keep going to evaluate reactions/thread-tail; if nothing rescues, return.
        pass

    # Keyword match (operator-configured + corpus-learned).
    for kw in (*rules.keywords, *rules.learned_keywords):
        if kw.lower() in text_lc:
            matched.append(kw)

    # Reaction-count boost (option d).
    reactions = context.get("reactions") or []
    if rules.boost_reactions:
        boost_set = set(rules.boost_reactions)
        for r in reactions:
            name = r.get("name", "")
            count = int(r.get("count", 0))
            if name in boost_set and count >= rules.boost_threshold:
                matched.append(f":{name}:×{count}")

    # Thread-tail position boost (option d).
    if rules.thread_tail_position_threshold is not None:
        pos = context.get("thread_position", 0)
        if pos >= rules.thread_tail_position_threshold:
            matched.append("thread-tail")

    # Final gate: any matched trigger AND meets length floor (or has reaction/thread booster).
    has_text_trigger = any(
        not m.startswith(":") and m != "thread-tail" for m in matched
    )
    has_context_trigger = any(
        m.startswith(":") or m == "thread-tail" for m in matched
    )
    is_positive = (
        (has_text_trigger and word_count >= rules.min_word_count)
        or has_context_trigger
    )

    return ClassificationResult(
        is_positive=is_positive,
        matched_triggers=tuple(matched),
        classifier_version=derive_classifier_version(rules),
    )
```

---

## Phase 2: Trigger rules schema + per-source / per-channel merge

**Why this phase exists**: Phase 1's classifier accepts a `TriggerRules` dataclass. Phase 2 produces those rules from operator configuration. Slack rules + Notion rules sit at workspace level; per-channel and per-database overrides merge on top. Operator authors a single YAML; runtime computes the effective rules per message.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_rules.py::test_load_rules_from_yaml_returns_typed_rules` — writes a YAML config with `slack.heuristics.keywords: [decided]`; calls `load_rules_from_config(path).slack.global_rules.keywords`; asserts the returned tuple equals `("decided",)`. Functionality — exercises the YAML→pydantic→TriggerRules path.
- [ ] `tests/test_team_server_rules.py::test_resolve_rules_for_slack_channel_merges_global_with_channel_override` — config has `slack.heuristics.global.keywords=[a, b]` and `slack.heuristics.channels.C123.keywords=[c]`; calls `resolve_rules_for_slack(config, channel_id="C123")`; asserts the resulting rules has `keywords=("a", "b", "c")` (channel overrides additive). Functionality — exercises the merge order.
- [ ] `tests/test_team_server_rules.py::test_resolve_rules_for_slack_channel_with_disabled_returns_disabled_marker` — config has `slack.heuristics.channels.C-RANDOM.enabled: false`; calls `resolve_rules_for_slack(config, channel_id="C-RANDOM")`; asserts the resolver returns `RulesDisabled` sentinel. Functionality — exercises the channel-skip surface.
- [ ] `tests/test_team_server_rules.py::test_resolve_rules_for_notion_database_merges_global_with_database_override` — same shape as above for `notion.heuristics.databases.<db_id>`. Functionality.
- [ ] `tests/test_team_server_rules.py::test_invalid_yaml_keyword_negatives_pattern_raises_value_error` — YAML has a list-of-int where a list-of-str is required; asserts `ValueError` on load. Functionality — exercises the strict pydantic validation.

### Affected Files

- `team_server/config.py` — **MUTATE** — add `HeuristicGlobalRules`, `HeuristicChannelOverride`, `HeuristicDatabaseOverride` pydantic models nested under existing `SlackConfig` and a new `NotionConfig`. Add `load_rules_from_config(path) -> TeamServerRules`. Add `resolve_rules_for_slack(config, channel_id) -> TriggerRules | RulesDisabled` and `resolve_rules_for_notion(config, db_id) -> TriggerRules | RulesDisabled`.
- `tests/test_team_server_rules.py` — **CREATE** — 5 functionality tests above.

### Changes

`team_server/config.py` additions:

```python
class HeuristicGlobalRules(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    keyword_negatives: list[str] = Field(default_factory=list)
    min_word_count: int = 0
    boost_reactions: list[str] = Field(default_factory=list)
    boost_threshold: int = 1
    thread_tail_position_threshold: Optional[int] = None
    enabled: bool = True


class HeuristicChannelOverride(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    keyword_negatives: list[str] = Field(default_factory=list)
    min_word_count: Optional[int] = None
    enabled: bool = True


class SlackHeuristics(BaseModel):
    global_rules: HeuristicGlobalRules = Field(
        default_factory=HeuristicGlobalRules, alias="global"
    )
    channels: dict[str, HeuristicChannelOverride] = Field(default_factory=dict)


class NotionHeuristics(BaseModel):
    global_rules: HeuristicGlobalRules = Field(
        default_factory=HeuristicGlobalRules, alias="global"
    )
    databases: dict[str, HeuristicChannelOverride] = Field(default_factory=dict)


class SlackConfig(BaseModel):  # existing class, MUTATE
    workspaces: list[WorkspaceConfig] = Field(default_factory=list)
    heuristics: SlackHeuristics = Field(default_factory=SlackHeuristics)


class NotionConfig(BaseModel):
    token: Optional[str] = None
    heuristics: NotionHeuristics = Field(default_factory=NotionHeuristics)


class TeamServerConfig(BaseModel):  # existing class, MUTATE
    slack: SlackConfig = Field(default_factory=SlackConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)


class RulesDisabled:
    """Sentinel returned by resolve_rules_* when a channel/db is opted out."""


def resolve_rules_for_slack(
    config: TeamServerConfig, channel_id: str
) -> TriggerRules | RulesDisabled:
    base = config.slack.heuristics.global_rules
    override = config.slack.heuristics.channels.get(channel_id)
    if not base.enabled or (override and not override.enabled):
        return RulesDisabled()
    return TriggerRules(
        keywords=tuple([*base.keywords, *(override.keywords if override else [])]),
        keyword_negatives=tuple([*base.keyword_negatives,
                                 *(override.keyword_negatives if override else [])]),
        min_word_count=(override.min_word_count if override and override.min_word_count is not None
                        else base.min_word_count),
        boost_reactions=tuple(base.boost_reactions),
        boost_threshold=base.boost_threshold,
        thread_tail_position_threshold=base.thread_tail_position_threshold,
    )


# resolve_rules_for_notion follows identical shape with `databases` in place of `channels`.
```

---

## Phase 3: Real LLM extractor — Anthropic SDK (Stage 2)

**Why this phase exists**: Replaces `team_server/extraction/llm_extractor.py`'s paragraph-split placeholder with a real Anthropic call. Stage 2 only runs on heuristic-positive messages (Phase 4 wires this). Output schema is minimal-structured: `{"summary": str, "context_snippet": str}` per decision. Error handling: 429 backoff + retry; other errors fail-soft to `{"decisions": [], "error": "..."}` so the worker's per-iteration try/except catches gracefully without dropping the whole polling cycle.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_llm_extractor.py::test_extract_returns_structured_decisions_from_mocked_anthropic_response` — patches the Anthropic client to return a fixed JSON-formatted message content; calls `extract(text="we decided to use REST", matched_triggers=["decided"])`; asserts the returned dict is `{"decisions": [{"summary": "use REST", "context_snippet": "we decided to use REST"}], "extractor_version": "claude-haiku-4-5-extract-v1", "matched_triggers": ["decided"]}`. Functionality — exercises the structured-output parsing.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_passes_matched_triggers_into_prompt` — patches the Anthropic client to record the request body; calls `extract(text=..., matched_triggers=["decided", "agreed"])`; asserts the captured request's user message contains both triggers as context grounding. Functionality — exercises the prompt-assembly contract.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_retries_on_429_then_succeeds` — patches the client to return 429 once then 200 with valid content; asserts the final return is the parsed decisions, and the patched client was called exactly twice. Functionality — exercises the retry-on-rate-limit path.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_fails_soft_on_500_returns_error_field` — patches the client to return 500 persistently; asserts the return is `{"decisions": [], "error": "<truncated 500 message>", "extractor_version": "...", "matched_triggers": [...]}`. Functionality — exercises the fail-soft contract.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_returns_empty_decisions_when_model_emits_unparseable_content` — patches the client to return text that's not valid JSON; asserts the return is `{"decisions": [], "error": "parse-failure: ...", ...}`. Functionality — exercises malformed-output recovery.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_uses_env_overridden_model_when_set` — sets `BICAMERAL_TEAM_SERVER_EXTRACT_MODEL=claude-sonnet-4-6`; patches client; asserts the captured request's `model` field equals the env value. Functionality — exercises the model-selection knob.
- [ ] `tests/test_team_server_llm_extractor.py::test_extract_raises_loud_when_anthropic_api_key_unset` — clears `ANTHROPIC_API_KEY`; calls `extract(...)`; asserts `RuntimeError` with a message naming `ANTHROPIC_API_KEY`. Functionality — exercises the fail-loud-on-missing-credential contract.

### Affected Files

- `team_server/extraction/llm_extractor.py` — **MUTATE** — full replacement of the paragraph-split placeholder. New module exports: `extract(text: str, matched_triggers: list[str]) -> dict` async; `EXTRACTOR_VERSION` constant computed from `(model_name + prompt_template_hash)`; `MissingAnthropicKeyError`. Anthropic SDK imported lazily inside `extract` (matches the slack_sdk lazy-import pattern from Phase 0.5).
- `tests/test_team_server_llm_extractor.py` — **CREATE** — 7 functionality tests above.

### Changes

`team_server/extraction/llm_extractor.py` (full rewrite):

```python
"""Stage 2 LLM extractor — real Anthropic SDK call.

Called only on heuristic-positive messages. Returns a structured dict
shape: {"decisions": [{"summary": str, "context_snippet": str}], ...}.
Failure modes:
- ANTHROPIC_API_KEY unset: raises MissingAnthropicKeyError (fail-loud).
- HTTP 429: retries with exponential backoff (max 3 attempts).
- HTTP 5xx: fails soft, returns {"decisions": [], "error": <message>}.
- Unparseable model output: same fail-soft path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Optional

DEFAULT_MODEL = "claude-haiku-4-5"
PROMPT_TEMPLATE = """You extract DECISIONS from a single chat or document
message. Return STRICT JSON of the shape:
{"decisions": [{"summary": "...", "context_snippet": "..."}]}

A "decision" is a commitment, choice, or ratification of a course of
action. Casual chatter, questions, and stale-context messages produce
[]. Multiple decisions in one message produce multiple objects.

The pre-classifier already matched these triggers: {triggers}.
Use them only as context; do not require them in the output.

Message:
\"\"\"{text}\"\"\""""

EXTRACTOR_VERSION_TEMPLATE_HASH = hashlib.sha256(
    PROMPT_TEMPLATE.encode("utf-8")
).hexdigest()[:8]


class MissingAnthropicKeyError(RuntimeError):
    """Raised at extract-time when ANTHROPIC_API_KEY is not set."""


def _extractor_version() -> str:
    model = os.environ.get("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", DEFAULT_MODEL)
    return f"{model}-extract-{EXTRACTOR_VERSION_TEMPLATE_HASH}"


async def extract(text: str, matched_triggers: list[str]) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingAnthropicKeyError(
            "ANTHROPIC_API_KEY env var is required for Stage 2 LLM extraction"
        )
    # Lazy import to allow the package to import in environments where
    # anthropic is in requirements.txt but not installed in dev venv.
    from anthropic import AsyncAnthropic, APIError, APIStatusError

    model = os.environ.get("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", DEFAULT_MODEL)
    client = AsyncAnthropic(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(triggers=matched_triggers, text=text)
    extractor_version = _extractor_version()

    last_error: Optional[str] = None
    for attempt in range(3):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.content[0].text if resp.content else ""
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                return {
                    "decisions": [],
                    "error": f"parse-failure: {exc}",
                    "extractor_version": extractor_version,
                    "matched_triggers": matched_triggers,
                }
            return {
                "decisions": parsed.get("decisions", []),
                "extractor_version": extractor_version,
                "matched_triggers": matched_triggers,
            }
        except APIStatusError as exc:
            if exc.status_code == 429 and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            last_error = f"{exc.status_code}: {str(exc)[:200]}"
        except APIError as exc:
            last_error = str(exc)[:200]
            break

    return {
        "decisions": [],
        "error": last_error or "unknown",
        "extractor_version": extractor_version,
        "matched_triggers": matched_triggers,
    }
```

---

## Phase 4: Pipeline integration — Slack/Notion workers route through `extract_decision_pipeline`

**Why this phase exists**: Wires Phase 1 (classifier) + Phase 2 (rules) + Phase 3 (LLM extractor) into a single pipeline function the workers call. Replaces the existing direct `extractor(text)` call in `slack_worker._ingest_message` and `notion_worker._ingest_row`. The pipeline is the only thing that knows about the two-stage architecture; workers just see "text+context+rules in, extraction dict out."

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_pipeline.py::test_pipeline_short_circuits_on_negative_classification` — patches LLM extractor to a recording stub; feeds a message that the classifier rejects (no keyword match, no booster); asserts the LLM stub was NOT awaited and the pipeline output is `{"decisions": [], "classifier_version": "...", "matched_triggers": [], "extractor_version": null}`. Functionality — exercises the no-LLM-on-chatter contract.
- [ ] `tests/test_team_server_pipeline.py::test_pipeline_invokes_llm_on_positive_classification` — patches LLM extractor to return `{"decisions": [{"summary": "..."}], "extractor_version": "...", ...}`; feeds a positive-classified message; asserts the LLM stub was awaited exactly once with the matched triggers passed through; pipeline output merges classifier + extractor metadata. Functionality — exercises the Stage 1 → Stage 2 wiring.
- [ ] `tests/test_team_server_pipeline.py::test_pipeline_skips_when_rules_disabled` — channel/db with `enabled: false`; asserts the pipeline returns the `RulesDisabled` short-circuit shape (`{"decisions": [], "skipped": true, ...}`) without invoking either classifier or extractor. Functionality — exercises the channel-opt-out path.
- [ ] `tests/test_team_server_slack_worker.py::test_slack_worker_routes_through_pipeline_with_thread_context` — seeds a message with `thread_ts` and `reactions`; patches the pipeline to a recording stub; runs `slack_worker._ingest_message`; asserts the recorded pipeline call received `context={"reactions": [...], "thread_position": ..., ...}`. Functionality — exercises the worker→pipeline context handoff (Slack-side option-d wiring).
- [ ] `tests/test_team_server_notion_worker.py::test_notion_worker_routes_through_pipeline_with_edit_context` — analogous Notion-side test with `last_edited_by` / `edit_count` context. Functionality — exercises the option-d wiring on the Notion source.

### Affected Files

- `team_server/extraction/pipeline.py` — **CREATE** — exports `extract_decision_pipeline(*, text, message, context, rules_or_disabled, llm_extract_fn=None) -> dict`. Argument `llm_extract_fn` defaults to `team_server.extraction.llm_extractor.extract` and is a parameter for test stubbing. Returns a uniform output shape: `{"decisions": [...], "classifier_version": str, "matched_triggers": [...], "extractor_version": str|None, "skipped": bool}`.
- `team_server/workers/slack_worker.py` — **MUTATE** — `_ingest_message` builds the `context` dict (extracts `thread_ts`, `reply_count`, `reactions`, `subtype`, computes `thread_position`); calls `resolve_rules_for_slack(config, channel_id)`; calls `extract_decision_pipeline`; passes the result's `(content_hash, classifier_version)` into `upsert_canonical_extraction`.
- `team_server/workers/notion_worker.py` — **MUTATE** — `_ingest_row` builds the context dict (extracts `last_edited_by`, `edit_count` from page meta); calls `resolve_rules_for_notion(config, db_id)`; same pipeline call shape.
- `team_server/workers/slack_runner.py` — **MUTATE** — passes the resolved `TeamServerConfig` through to slack_worker so `_ingest_message` can resolve per-channel rules.
- `team_server/workers/notion_runner.py` — **MUTATE** — same pattern for notion_worker.
- `team_server/app.py` — **MUTATE** — lifespan loads `TeamServerConfig` from `DEFAULT_CONFIG_PATH` once at startup and passes it through `run_slack_iteration` / `run_notion_iteration`'s extra arg.
- `tests/test_team_server_pipeline.py` — **CREATE** — 3 functionality tests above.
- `tests/test_team_server_slack_worker.py` — **MUTATE** — add the thread-context-handoff test.
- `tests/test_team_server_notion_worker.py` — **MUTATE** — add the edit-context-handoff test.

### Changes

`team_server/extraction/pipeline.py`:

```python
"""Extraction pipeline — Stage 1 (heuristic classifier) → Stage 2 (LLM).

Single entry point for both Slack and Notion workers. Determines the
output shape regardless of source: {decisions, classifier_version,
matched_triggers, extractor_version, skipped}. extractor_version is
null when Stage 2 did not run (chatter or rules-disabled).
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, Union

from team_server.config import RulesDisabled
from team_server.extraction.heuristic_classifier import (
    TriggerRules, classify, derive_classifier_version
)

LLMExtractFn = Callable[[str, list[str]], Awaitable[dict]]


async def extract_decision_pipeline(
    *,
    text: str,
    message: dict,
    context: dict,
    rules_or_disabled: Union[TriggerRules, RulesDisabled],
    llm_extract_fn: Optional[LLMExtractFn] = None,
) -> dict:
    if isinstance(rules_or_disabled, RulesDisabled):
        return {
            "decisions": [],
            "classifier_version": "rules-disabled",
            "matched_triggers": [],
            "extractor_version": None,
            "skipped": True,
        }
    rules = rules_or_disabled
    cv = derive_classifier_version(rules)
    classification = classify({"text": text, **message}, context, rules)
    if not classification.is_positive:
        return {
            "decisions": [],
            "classifier_version": cv,
            "matched_triggers": list(classification.matched_triggers),
            "extractor_version": None,
            "skipped": False,
        }
    if llm_extract_fn is None:
        from team_server.extraction.llm_extractor import extract as llm_extract_fn  # noqa
    llm_result = await llm_extract_fn(text, list(classification.matched_triggers))
    return {
        "decisions": llm_result.get("decisions", []),
        "classifier_version": cv,
        "matched_triggers": list(classification.matched_triggers),
        "extractor_version": llm_result.get("extractor_version"),
        "error": llm_result.get("error"),
        "skipped": False,
    }
```

---

## Phase 5: Corpus learner — option-c feedback loop (ships independently)

**Why this phase exists**: Operator-configured keywords cover the obvious vocabulary; the long tail of team-specific phrasing emerges from observing actual decisions over time. Phase 5 reads the team-server's own `decision` table (per OQ-1 resolution: directly from local rows, not via remote pull), extracts top N-grams that appeared in messages preceding ratified decisions, and writes them to a new `learned_heuristic_terms` table. The merge-into-rules path is already established in Phase 1 (`TriggerRules.learned_keywords`); Phase 5 just populates it.

This phase is **slip-independent** — Phases 0–4 ship as a complete real-extractor system. Phase 5 enriches the rule set with corpus-learned terms; if it slips, the operator-configured keyword path covers v1.1's promise.

### Verification (TDD — list test files first)

- [ ] `tests/test_team_server_corpus_learner.py::test_learner_extracts_top_ngrams_from_ratified_decisions` — seeds the local ledger with 10 ratified decisions whose source messages contain a recurring phrase ("approved by tech lead"); calls `learn_corpus_terms(client, top_n=5)`; asserts the returned list contains "approved by tech lead" with support count 10. Functionality — exercises the n-gram extraction over a synthetic corpus.
- [ ] `tests/test_team_server_corpus_learner.py::test_learner_respects_denylist` — config has `slack.heuristics.global.learned_denylist=["approved by"]`; seeds same corpus; asserts the returned list does not contain any term matching the denylist. Functionality — exercises the operator-veto path.
- [ ] `tests/test_team_server_corpus_learner.py::test_learner_persists_results_to_learned_heuristic_terms_table` — runs the learner; asserts a SELECT against `learned_heuristic_terms` returns the expected rows with `term`, `support_count`, `learned_at`. Functionality — exercises the persistence contract.
- [ ] `tests/test_team_server_corpus_learner.py::test_learn_corpus_terms_is_deterministic_for_same_input` — runs the learner twice over the same fixture corpus; asserts byte-identical output. Functionality — exercises the determinism invariant (gates whether re-runs are no-ops or cause classifier-version churn).
- [ ] `tests/test_team_server_corpus_learner.py::test_resolve_rules_merges_learned_terms_into_keywords` — pre-populates `learned_heuristic_terms`; calls `resolve_rules_for_slack(config, channel_id)`; asserts the resulting `TriggerRules.learned_keywords` includes the persisted terms. Functionality — exercises the rules-merge integration.
- [ ] `tests/test_team_server_corpus_learner_lifecycle.py::test_lifespan_starts_corpus_learner_when_enabled` — config has `corpus_learner.enabled: true`; starts the app; patches `learn_corpus_terms` to a recording stub; advances the worker timer; asserts the stub was awaited at least once. Functionality — exercises the worker registration via the existing `worker_loop` helper.
- [ ] `tests/test_team_server_corpus_learner_lifecycle.py::test_lifespan_does_not_start_corpus_learner_when_disabled` — config has `corpus_learner.enabled: false` (default); asserts no `team-server-worker-corpus-learner` task is registered. Functionality — exercises the off-by-default invariant.

### Affected Files

- `team_server/extraction/corpus_learner.py` — **CREATE** — exports `learn_corpus_terms(client, *, top_n, denylist) -> list[dict]`; `persist_learned_terms(client, terms)`; `run_corpus_learner_iteration(client, config)` async wrapper for `worker_loop`.
- `team_server/schema.py` — **MUTATE** — bump `SCHEMA_VERSION` to 4; add `learned_heuristic_terms` table (`source_type`, `term`, `support_count`, `learned_at`, `version` index); register `_migrate_v3_to_v4`.
- `team_server/config.py` — **MUTATE** — add `CorpusLearnerConfig` model with `enabled: bool`, `interval_seconds: int = 86400`, `top_n: int = 50`, and `learned_denylist: list[str]` field on `HeuristicGlobalRules`. Update `resolve_rules_for_slack` / `resolve_rules_for_notion` to read from `learned_heuristic_terms` table and merge into `learned_keywords`.
- `team_server/app.py` — **MUTATE** — lifespan registers the corpus-learner task via `worker_loop` when `config.corpus_learner.enabled` is true.
- `tests/test_team_server_corpus_learner.py` — **CREATE** — 5 functionality tests.
- `tests/test_team_server_corpus_learner_lifecycle.py` — **CREATE** — 2 functionality tests.

### Changes

(Full implementation deferred to the implement phase. Core skeleton:)

```python
# team_server/extraction/corpus_learner.py
"""Corpus learner — reads ratified decisions, extracts recurring n-grams,
populates learned_heuristic_terms for the heuristic classifier to merge."""

from collections import Counter

from ledger.client import LedgerClient

NGRAM_MIN, NGRAM_MAX = 2, 4


async def learn_corpus_terms(
    client: LedgerClient, *, top_n: int = 50, denylist: list[str] = None,
) -> list[dict]:
    rows = await client.query(
        "SELECT description FROM decision WHERE status = 'ratified'"
    )
    counter: Counter[str] = Counter()
    for row in rows or []:
        text = (row.get("description") or "").lower()
        words = text.split()
        for n in range(NGRAM_MIN, NGRAM_MAX + 1):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                counter[gram] += 1
    denyset = {d.lower() for d in (denylist or [])}
    out = []
    for term, support in counter.most_common(top_n * 4):
        if term in denyset or any(d in term for d in denyset):
            continue
        out.append({"term": term, "support_count": support})
        if len(out) >= top_n:
            break
    return out
```

---

## CI Commands

- `pytest -x tests/test_team_server_classifier_version.py tests/test_team_server_schema_migration.py` — Phase 0 cache-contract evolution
- `pytest -x tests/test_team_server_heuristic_classifier.py` — Phase 1 classifier behavior
- `pytest -x tests/test_team_server_rules.py` — Phase 2 config rules + merge order
- `pytest -x tests/test_team_server_llm_extractor.py` — Phase 3 Anthropic SDK integration
- `pytest -x tests/test_team_server_pipeline.py tests/test_team_server_slack_worker.py tests/test_team_server_notion_worker.py` — Phase 4 pipeline + worker integration
- `pytest -x tests/test_team_server_corpus_learner.py tests/test_team_server_corpus_learner_lifecycle.py` — Phase 5 corpus learner (slip-independent)
- `pytest -x tests/test_team_server_*.py tests/test_materializer_team_server_pull.py` — full team-server suite
- `pytest -x tests/ -k "not team_server"` — regression check (no breakage to per-repo bicameral)

---

## Risk note (L2 grade reasoning)

L2 because:

- **No new credential lifecycle**: Anthropic API key is env-sourced; same operator-deployment-concern posture as the existing `BICAMERAL_TEAM_SERVER_SECRET_KEY` Fernet key. Fail-loud on missing key prevents silent skip.
- **No new IPC paths**: Pipeline is in-process; adds Anthropic API calls (already a network-permitted boundary outside the deterministic core per CONCEPT.md literal-keyword parsing).
- **Cache contract evolution is contained**: `classifier_version` adds one column; the upsert function gains one comparison axis; the v2→v3 migration is additive (no DROP/REDEFINE). Phase 0 tests cover the contract change end-to-end before any other phase lands.
- **Determinism and auditability preserved**: heuristic Stage 1 is deterministic; matched triggers are persisted in the cache row's extraction blob. Operator can answer "why was this surfaced?" with file:line precision.
- **CocoIndex unparking compatibility**: when CocoIndex (#136) eventually lands, it replaces Stage 1 (and possibly Stage 2) by becoming the deterministic memoized classifier+extractor. The pipeline's `llm_extract_fn` parameter and the rules-version cache axis both extend cleanly.

---

## Modular commit plan (Option-5 convention)

Six commits, one PR.

```
refactor(team-server): cache-contract gets classifier_version axis (Phase 0)
feat(team-server): heuristic classifier — pure deterministic Stage 1 (Phase 1)
feat(team-server): trigger rules schema + per-channel/db merge (Phase 2)
feat(team-server): real LLM extractor via Anthropic SDK (Phase 3)
feat(team-server): pipeline integration — workers route Stage 1 → Stage 2 (Phase 4)
feat(team-server): corpus learner — option-c feedback loop (Phase 5)
```

Phase 5 ships independently if it slips — Phases 0–4 deliver the real extractor with operator-configured + context-aware classification.
