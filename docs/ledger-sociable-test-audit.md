# Sociable test coverage audit — `ledger/queries.py`

**Issue #357 sub-task 1 — Phase A deliverable.**

- Total functions in `ledger/queries.py`: **67**
- Functions issuing raw SurrealQL: **57**

Coverage breakdown (SurrealQL-bearing functions only):

| Category | Count | Risk |
|---|---|---|
| **Direct sociable** (has at least one test using `memory://` or real adapter) | 24 | safe |
| **Solitary trap** (tests exist but ALL use `Mock`/`Fake` — #309-class) | 8 | **HIGH** |
| **Indirect sociable** (no direct test, but caller has sociable handler test) | 25 | low |
| **Uncovered** (no direct test and no indirect coverage detected) | 0 | medium |


## Full table

| Function | Line | SQL | # refs | sociable | category | callers |
|---|---|---|---|---|---|---|
| `_execute_idempotent_edge` | 30 | yes | 0 | 0 | indirect | queries.py |
| `get_sync_state` | 49 | yes | 1 | 1 | direct | adapter.py |
| `upsert_sync_state` | 58 | yes | 0 | 0 | indirect | adapter.py |
| `get_source_cursor` | 72 | yes | 0 | 0 | indirect | adapter.py |
| `upsert_source_cursor` | 94 | yes | 1 | 1 | direct | ingest.py, adapter.py, team_adapter.py |
| `get_all_decisions` | 146 | yes | 8 | 6 | direct | decision_status.py, history.py, adapter.py |
| `search_by_bm25` | 214 | yes | 0 | 0 | indirect | adapter.py |
| `lookup_vocab_cache` | 270 | yes | 0 | 0 | indirect | adapter.py |
| `upsert_vocab_cache` | 304 | yes | 0 | 0 | indirect | adapter.py |
| `get_decisions_for_file` | 329 | yes | 1 | 1 | direct | detect_drift.py, adapter.py |
| `has_decisions_for_files` | 434 | yes | 0 | 0 | indirect | preflight.py |
| `get_decisions_for_files` | 450 | yes | 3 | 0 | **TRAP** | preflight.py, adapter.py |
| `get_undocumented_symbols` | 558 | yes | 0 | 0 | indirect | detect_drift.py, adapter.py |
| `upsert_decision` | 578 | yes | 4 | 4 | direct | adapter.py |
| `upsert_symbol` | 679 | yes | 0 | 0 | indirect | adapter.py |
| `upsert_code_region` | 707 | yes | 3 | 3 | direct | adapter.py |
| `create_code_region` | 749 | yes | 0 | 0 | indirect | adapter.py |
| `upsert_compliance_check` | 787 | yes | 3 | 2 | direct | resolve_compliance.py, adapter.py |
| `promote_ephemeral_verdict` | 845 | yes | 1 | 1 | direct | resolve_compliance.py, adapter.py |
| `decision_exists` | 868 | yes | 4 | 0 | **TRAP** | remove_decision.py, ratify.py, bind.py (+3) |
| `get_decisions_for_span` | 874 | yes | 1 | 0 | **TRAP** | remove_source.py |
| `input_span_exists` | 890 | yes | 2 | 0 | **TRAP** | remove_source.py |
| `get_input_span_row` | 896 | yes | 1 | 0 | **TRAP** | remove_source.py |
| `get_decision_level` | 909 | yes | 1 | 1 | direct | bind.py, adapter.py |
| `get_decision_source` | 928 | yes | 0 | 0 | indirect | bind.py, resolve_compliance.py, adapter.py |
| `region_exists` | 947 | yes | 0 | 0 | indirect | resolve_compliance.py |
| `get_region_descriptor` | 953 | yes | 0 | 0 | indirect | resolve_compliance.py |
| `find_code_region_by_content` | 982 | yes | 0 | 0 | indirect | materializer.py |
| `get_compliance_verdict` | 1010 | yes | 2 | 2 | direct | adapter.py, queries.py, status.py |
| `relate_yields` | 1031 | no | 0 | 0 | — | — |
| `relate_binds_to` | 1043 | no | 3 | 3 | — | — |
| `relate_locates` | 1059 | no | 0 | 0 | — | — |
| `upsert_input_span` | 1073 | yes | 0 | 0 | indirect | adapter.py |
| `update_decision_status` | 1116 | yes | 6 | 3 | direct | remove_decision.py, remove_source.py, resolve_collision.py (+2) |
| `get_ledger_revision` | 1128 | yes | 4 | 2 | direct | preflight.py |
| `get_canonical_id` | 1188 | yes | 2 | 2 | direct | resolve_compliance.py, team_adapter.py |
| `find_decision_by_canonical_id` | 1202 | yes | 2 | 2 | direct | materializer.py |
| `update_decision_level` | 1235 | yes | 3 | 3 | direct | — |
| `update_region_hash` | 1271 | yes | 1 | 1 | direct | resolve_compliance.py, adapter.py |
| `get_regions_for_files` | 1284 | yes | 0 | 0 | indirect | adapter.py |
| `get_regions_without_hash` | 1308 | yes | 0 | 0 | indirect | adapter.py |
| `get_regions_with_ephemeral_verdicts` | 1328 | yes | 0 | 0 | indirect | adapter.py |
| `get_pending_decisions_with_regions` | 1360 | yes | 0 | 0 | indirect | adapter.py |
| `delete_binds_to_edge` | 1392 | yes | 0 | 0 | indirect | resolve_compliance.py |
| `get_proposed_decisions_with_bindings` | 1410 | yes | 0 | 0 | indirect | adapter.py |
| `set_decision_pruned` | 1438 | yes | 0 | 0 | indirect | adapter.py |
| `has_prior_compliant_verdict` | 1454 | yes | 2 | 2 | direct | adapter.py, queries.py |
| `project_decision_status` | 1482 | yes | 8 | 4 | direct | remove_decision.py, remove_source.py, ratify.py (+3) |
| `get_grounding_breakdown` | 1590 | yes | 1 | 1 | direct | — |
| `_normalize_decisions` | 1637 | no | 0 | 0 | — | — |
| `relate_supersedes` | 1657 | no | 0 | 0 | — | — |
| `relate_context_for` | 1673 | yes | 0 | 0 | indirect | resolve_collision.py |
| `get_input_span_id` | 1704 | yes | 0 | 0 | indirect | ingest.py |
| `search_context_pending_by_text` | 1719 | yes | 0 | 0 | indirect | ingest.py |
| `get_collision_pending_decisions` | 1755 | yes | 2 | 0 | **TRAP** | preflight.py |
| `get_context_for_ready_decisions` | 1779 | yes | 2 | 0 | **TRAP** | preflight.py |
| `_validated_record_id` | 1830 | no | 0 | 0 | — | — |
| `upsert_code_subject` | 1844 | yes | 1 | 1 | direct | adapter.py |
| `upsert_subject_identity` | 1889 | yes | 1 | 1 | direct | adapter.py |
| `relate_has_identity` | 1964 | no | 1 | 1 | — | — |
| `link_decision_to_subject` | 1980 | no | 1 | 1 | — | — |
| `get_region_metadata` | 2013 | yes | 1 | 0 | **TRAP** | link_commit.py, adapter.py |
| `update_binds_to_region` | 2052 | yes | 1 | 1 | direct | adapter.py |
| `write_identity_supersedes` | 2127 | no | 1 | 1 | — | — |
| `write_subject_version` | 2150 | yes | 1 | 1 | direct | adapter.py |
| `relate_has_version` | 2219 | no | 1 | 1 | — | — |
| `find_subject_identities_for_decision` | 2239 | yes | 2 | 1 | direct | adapter.py |

## Solitary trap rows — fix first (#309-class risk)

- `get_decisions_for_files` (line 450)
  - solitary tests: `tests/test_preflight_dedup_telemetry.py`, `tests/test_preflight_dedup_v2.py`, `tests/test_v055_region_anchored_preflight.py`
  - prod callers: handlers/preflight.py, ledger/adapter.py
- `decision_exists` (line 868)
  - solitary tests: `tests/test_dogfood_label_propagation.py`, `tests/test_preflight_id_plumbing.py`, `tests/test_remove_decision.py`, `tests/test_remove_source.py`
  - prod callers: handlers/remove_decision.py, handlers/ratify.py, handlers/bind.py
- `get_decisions_for_span` (line 874)
  - solitary tests: `tests/test_remove_source.py`
  - prod callers: handlers/remove_source.py
- `input_span_exists` (line 890)
  - solitary tests: `tests/test_dogfood_label_propagation.py`, `tests/test_remove_source.py`
  - prod callers: handlers/remove_source.py
- `get_input_span_row` (line 896)
  - solitary tests: `tests/test_remove_source.py`
  - prod callers: handlers/remove_source.py
- `get_collision_pending_decisions` (line 1755)
  - solitary tests: `tests/test_preflight_dedup_telemetry.py`, `tests/test_preflight_dedup_v2.py`
  - prod callers: handlers/preflight.py
- `get_context_for_ready_decisions` (line 1779)
  - solitary tests: `tests/test_preflight_dedup_telemetry.py`, `tests/test_preflight_dedup_v2.py`
  - prod callers: handlers/preflight.py
- `get_region_metadata` (line 2013)
  - solitary tests: `tests/test_codegenome_phase4_link_commit.py`
  - prod callers: handlers/link_commit.py, ledger/adapter.py

## Uncovered rows — investigate

_None._

## Indirect-only rows — low priority

- `_execute_idempotent_edge` (line 30) — exercised via: ledger/queries.py
- `upsert_sync_state` (line 58) — exercised via: ledger/adapter.py
- `get_source_cursor` (line 72) — exercised via: ledger/adapter.py
- `search_by_bm25` (line 214) — exercised via: ledger/adapter.py
- `lookup_vocab_cache` (line 270) — exercised via: ledger/adapter.py
- `upsert_vocab_cache` (line 304) — exercised via: ledger/adapter.py
- `has_decisions_for_files` (line 434) — exercised via: handlers/preflight.py
- `get_undocumented_symbols` (line 558) — exercised via: handlers/detect_drift.py, ledger/adapter.py
- `upsert_symbol` (line 679) — exercised via: ledger/adapter.py
- `create_code_region` (line 749) — exercised via: ledger/adapter.py
- `get_decision_source` (line 928) — exercised via: handlers/bind.py, handlers/resolve_compliance.py, ledger/adapter.py
- `region_exists` (line 947) — exercised via: handlers/resolve_compliance.py
- `get_region_descriptor` (line 953) — exercised via: handlers/resolve_compliance.py
- `find_code_region_by_content` (line 982) — exercised via: events/materializer.py
- `upsert_input_span` (line 1073) — exercised via: ledger/adapter.py
- `get_regions_for_files` (line 1284) — exercised via: ledger/adapter.py
- `get_regions_without_hash` (line 1308) — exercised via: ledger/adapter.py
- `get_regions_with_ephemeral_verdicts` (line 1328) — exercised via: ledger/adapter.py
- `get_pending_decisions_with_regions` (line 1360) — exercised via: ledger/adapter.py
- `delete_binds_to_edge` (line 1392) — exercised via: handlers/resolve_compliance.py
- `get_proposed_decisions_with_bindings` (line 1410) — exercised via: ledger/adapter.py
- `set_decision_pruned` (line 1438) — exercised via: ledger/adapter.py
- `relate_context_for` (line 1673) — exercised via: handlers/resolve_collision.py
- `get_input_span_id` (line 1704) — exercised via: handlers/ingest.py
- `search_context_pending_by_text` (line 1719) — exercised via: handlers/ingest.py
