# Bicameral Skill Constants

Single source of truth for all tuning parameters referenced by agent skill files.
When a value changes here, propagate it to the inline reference in each skill listed
under "Used by".

---

## Correction Capture

| Constant | Value | Used by | Notes |
|---|---|---|---|
| `IN_SESSION_SCAN_TURNS` | `~10` | capture-corrections (in-session), preflight step 3.5 | User turns scanned per preflight code verb |
| `MANUAL_SCAN_TURNS` | `20` | capture-corrections (manual) | Turns scanned when invoked by hand |
| `DEDUP_TOP_K` | `3` | capture-corrections Step C | Results fetched from bicameral.search for dedup |
| `DEDUP_MIN_CONFIDENCE` | `0.4` | capture-corrections Step C | BM25 floor; treat presence-in-results as match |
| `BATCH_CONFIRM_THRESHOLD` | `5` | capture-corrections steps 6-7 | ≤ threshold → show full list; > threshold → show first N with overflow note |

## Preflight

| Constant | Value | Used by | Notes |
|---|---|---|---|
| `MAX_QUESTIONS_PER_CALL` | `4` | preflight step 4 | Hard cap on stop-and-ask questions per preflight invocation |
| `MAX_QUESTIONS_PER_CATEGORY` | `1` | preflight step 4, Stop-and-Ask Contract | One question per finding category (drift / divergence / uningested / open / ungrounded) |

## Ingest

| Constant | Value | Used by | Notes |
|---|---|---|---|
| `CHUNK_H1_THRESHOLD` | `3` | ingest chunking heuristic | ≥ N H1 headings → consider chunking the document |
| `CHUNK_H2_THRESHOLD` | `5` | ingest chunking heuristic | ≥ N H2 headings → consider chunking the document |
| `CHUNK_SPEAKER_TURNS` | `5` | ingest chunking heuristic | ≥ N distinct speaker turns → consider chunking the transcript |
| `CHUNK_THEME_COUNT` | `3` | ingest chunking heuristic | ≥ N distinct topical themes on first-pass read → consider chunking |
| `FEATURE_GROUP_WORD_OVERLAP` | `2` | ingest feature grouping | Minimum significant content words shared to fuzzy-match an existing group |
| `MAX_STOP_AND_ASK_PER_INGEST` | `3` | ingest step 6 | Hard cap on clarifying questions per ingest call |
| `INGEST_CONFIRM_THRESHOLD` | `5` | ingest ratify UX | ≤ threshold → show full list; > threshold → default to "all / exclude" prompt |

## Judge Gaps

| Constant | Value | Used by | Notes |
|---|---|---|---|
| `GAP_INLINE_THRESHOLD` | `3` | judge-gaps rendering | ≤ threshold → render each gap inline; > threshold → batch the overflow |

---

## Server-owned constants (not set here)

These live in Python source and are intentionally excluded from skill files to
avoid coupling:

| Constant | Location | Notes |
|---|---|---|
| `recently_checked` TTL | `handlers/preflight.py` | Per-session dedup window for repeated preflight calls on the same topic |
| Topic validation rules | `handlers/preflight.py` | Server-side; skills say "the handler validates" without specifying the rule |
