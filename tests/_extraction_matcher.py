"""LLM-as-judge matcher for the M1 extraction-precision metric.

Given two lists of decision descriptions — ``actual`` (from the current
skill's Haiku extraction) and ``expected`` (from the committed Opus
ground-truth fixture) — returns a 1:1 matching identifying which actuals
correspond to which expecteds, even when the wording diverges.

This replaces the previous rapidfuzz ``token_set_ratio`` matcher, which
was too brittle for paraphrase-equivalent decisions. Concrete example
from run 24370616582 that rapidfuzz missed:

    Haiku:  "Implement a 12-second timeout ceiling on payment provider
             authorize calls; if exceeded, return requires_more status"
    Opus:   "Wrap the authorize call in PaymentProviderService with a
             12-second timeout ceiling and return requires_more status"

Both describe the same decision — same constraint, same status code,
same retry semantics — but token_set_ratio scored them below 70%.

Design:
- One Haiku 4.5 call per transcript, not per pair (N+M tokens, not N*M).
- Structured via tool use so there's no free-text JSON parsing.
- Response is cached to tests/.match-cache/ keyed on
  (model, sha(actual), sha(expected)). Cache invalidates automatically
  whenever either list changes — e.g., when a Phase 5 branch edits
  SKILL.md and Haiku produces a different extraction.
- Offline tests use the rapidfuzz fallback in _extraction_metrics.py
  by passing matcher="rapidfuzz" explicitly, so no network is needed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MATCHER_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 4096
REQUEST_TIMEOUT_S = 120.0

MATCH_CACHE_DIR = Path(__file__).resolve().parent / ".match-cache"

MATCHER_TOOL = {
    "name": "submit_matching",
    "description": (
        "Submit the 1:1 matching between actual (extracted) decisions and "
        "expected (ground-truth) decisions. Two decisions match when they "
        "describe the same implementation choice — same constraint, same "
        "scope, same behavior — even if the exact wording differs. Each "
        "actual index and each expected index may appear at most once. "
        "Omit any actuals or expecteds that have no semantic equivalent "
        "on the other side."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "description": "Pairs of (actual_index, expected_index). Both 0-based.",
                "items": {
                    "type": "object",
                    "properties": {
                        "actual_index": {"type": "integer", "minimum": 0},
                        "expected_index": {"type": "integer", "minimum": 0},
                        "rationale": {
                            "type": "string",
                            "description": "One short sentence explaining why this pair matches. For audit logs.",
                        },
                    },
                    "required": ["actual_index", "expected_index", "rationale"],
                },
            }
        },
        "required": ["matches"],
    },
}

SYSTEM_PROMPT = """\
You are the matching judge for a decision-extraction evaluation. You will be
given two numbered lists of implementation decisions extracted from a single
meeting transcript:

  ACTUAL   — what the current skill extracted (may be noisy, paraphrased,
             over-split, or incomplete)
  EXPECTED — the committed ground-truth reference

Your job is to pair each actual decision with its semantic equivalent in
the expected list, if one exists. Two decisions **match** when they describe
the same implementation choice — same constraint, same scope, same
behavior — even if the wording is different.

Matching rules:
- **1:1**: each actual index and each expected index may appear at most
  once. If the actual side duplicates a decision (e.g., two paraphrases of
  the same thing), only one of them can claim the expected match.
- **Semantic equivalence**: favor meaning over wording. "12-second timeout
  ceiling on authorize" matches "wrap authorize with 12s timeout". But
  "add retry logic" and "add rate limiter" do not match — different
  behaviors.
- **When in doubt, leave unmatched**. A false match is worse than a false
  miss for this metric.

Call `submit_matching` with the list of matches and a short rationale for
each pair. Every other actual or expected is unmatched by omission.
"""

USER_PROMPT_TEMPLATE = """\
ACTUAL (current skill extraction, 0-indexed):
{actual_block}

EXPECTED (ground-truth reference, 0-indexed):
{expected_block}

Match them now and call `submit_matching`.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _list_sha(items: list[str]) -> str:
    # Order matters for cache key — same-content-different-order lists
    # should map to different cache entries so we don't silently reuse a
    # stale matching when the runner reshuffles.
    return _sha("\n".join(items))


def _cache_path(model: str, actual_sha: str, expected_sha: str) -> Path:
    key = f"{model}|{actual_sha}|{expected_sha}"
    return MATCH_CACHE_DIR / f"{_sha(key)}.json"


def _format_list(items: list[str]) -> str:
    return "\n".join(f"  [{i}] {text}" for i, text in enumerate(items))


def _parse_matches(
    tool_input: dict,
    n_actual: int,
    n_expected: int,
) -> list[tuple[int, int | None]]:
    """Convert the tool's matches list into (actual_idx, expected_idx|None) pairs.

    Enforces 1:1 matching and valid indices. Any actual not matched by the
    model becomes (idx, None). Invalid pairs are dropped with a warning.
    """
    raw_matches = tool_input.get("matches", []) or []
    seen_actual: set[int] = set()
    seen_expected: set[int] = set()
    paired: list[tuple[int, int | None]] = []

    for m in raw_matches:
        ai = m.get("actual_index")
        ei = m.get("expected_index")
        if not isinstance(ai, int) or not isinstance(ei, int):
            continue
        if not (0 <= ai < n_actual) or not (0 <= ei < n_expected):
            continue
        if ai in seen_actual or ei in seen_expected:
            continue
        seen_actual.add(ai)
        seen_expected.add(ei)
        paired.append((ai, ei))

    for i in range(n_actual):
        if i not in seen_actual:
            paired.append((i, None))

    paired.sort(key=lambda p: p[0])
    return paired


def _call_matcher_api(
    actual: list[str],
    expected: list[str],
    *,
    model: str,
    api_key: str,
) -> dict:
    """POST to Anthropic Messages API with tool-use forced. Returns the
    parsed tool_use input dict.

    Raises RuntimeError with a diagnostic body on HTTP errors so the CI
    log is informative rather than opaque.
    """
    headers = {
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    user_prompt = USER_PROMPT_TEMPLATE.format(
        actual_block=_format_list(actual),
        expected_block=_format_list(expected),
    )
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [MATCHER_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_matching"},
    }

    key_prefix = api_key[:12] if api_key else "(empty)"
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Matcher API error {resp.status_code}: {resp.text[:500]} "
                f"(model={model}, key_prefix={key_prefix}..., key_len={len(api_key)})"
            )
        data = resp.json()

    stop_reason = data.get("stop_reason", "")
    content = data.get("content") or []
    tool_use = next((b for b in content if b.get("type") == "tool_use"), None)
    if tool_use is None:
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        raise RuntimeError(
            f"Matcher response missing tool_use block "
            f"(stop_reason={stop_reason!r}, text={'|'.join(text_parts)[:300]!r})"
        )
    if stop_reason == "max_tokens":
        raise RuntimeError(
            f"Matcher response hit max_tokens={MAX_OUTPUT_TOKENS} — lists "
            f"are too long. Consider chunking or raising MAX_OUTPUT_TOKENS."
        )
    result = tool_use.get("input")
    if not isinstance(result, dict):
        raise RuntimeError(f"Matcher tool_use.input is not a dict: {result!r}")
    return result


def llm_match(
    actual: list[str],
    expected: list[str],
    *,
    model: str | None = None,
    api_key: str | None = None,
    use_cache: bool = True,
) -> list[tuple[int, int | None]]:
    """Return a 1:1 matching of actual→expected indices via Haiku tool use.

    Entries in the returned list are ``(actual_idx, expected_idx | None)``.
    Unmatched actuals are included with ``None`` as the second element.
    Unmatched expecteds are *not* listed — the caller derives them from
    ``expected_idx`` values that never appear.
    """
    if not actual:
        return []
    if not expected:
        return [(i, None) for i in range(len(actual))]

    chosen_model = model or os.getenv("M1_EVAL_MODEL", DEFAULT_MATCHER_MODEL)
    actual_sha = _list_sha(actual)
    expected_sha = _list_sha(expected)
    cache_file = _cache_path(chosen_model, actual_sha, expected_sha)

    if use_cache and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return [tuple(p) for p in cached["pairs"]]

    chosen_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not chosen_key.strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required for LLM-as-judge matching. "
            "Set the env var, or pass matcher='rapidfuzz' explicitly."
        )

    tool_input = _call_matcher_api(actual, expected, model=chosen_model, api_key=chosen_key)
    pairs = _parse_matches(tool_input, n_actual=len(actual), n_expected=len(expected))

    if use_cache:
        MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "model": chosen_model,
                    "n_actual": len(actual),
                    "n_expected": len(expected),
                    "pairs": [list(p) for p in pairs],
                    "rationales": tool_input.get("matches", []),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return pairs
