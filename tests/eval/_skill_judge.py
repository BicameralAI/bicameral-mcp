"""Headless skill-judgment driver for the bicameral-preflight skill.

Runs Step 1 of `skills/bicameral-preflight/SKILL.md` (read full ledger →
identify relevant feature groups) against the Anthropic Messages API and
returns a structured judgment. Used by phase 2 of the preflight failure-
mode harness to measure recall on vocabulary-mismatch / ungrounded /
false-positive scenarios.

Modeled on `tests/_extract_headless.py`. Same auth path (x-api-key),
same tool-use approach for structured output, same fixture-cache
keyed on SHA(model | skill_sha | input_sha) so a SKILL.md edit or a
dataset change invalidates prior runs automatically.

Environment:
    ANTHROPIC_API_KEY                       required for live calls
    BICAMERAL_PREFLIGHT_EVAL_MODEL          default "claude-sonnet-4-6"
    BICAMERAL_PREFLIGHT_EVAL_RECORD=1       force-bypass cache, re-record
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD_PATH = REPO_ROOT / "skills" / "bicameral-preflight" / "SKILL.md"
CACHE_DIR = Path(__file__).resolve().parent / "fixtures" / "skill_judge"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT_S = 60.0


JUDGMENT_TOOL = {
    "name": "submit_relevance_judgment",
    "description": (
        "Submit which feature groups in the provided ledger are relevant to "
        "the current implementation task. Must be called exactly once. "
        "If no feature group is relevant, call with an empty array."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant_features": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names of feature groups (exactly as they appear in the "
                    "ledger's 'feature' field) that are relevant to the topic. "
                    "Empty array if none."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "One short sentence per chosen feature explaining the link. "
                    "Empty string if no features chosen."
                ),
            },
        },
        "required": ["relevant_features", "reasoning"],
    },
}


SYSTEM_PROMPT_TEMPLATE = """\
You are the relevance-judgment stage of the bicameral-preflight skill.
Apply the rules below from SKILL.md verbatim, then call the
`submit_relevance_judgment` tool exactly once with the chosen feature
groups.

Rules to apply (from SKILL.md, Step 1 — Read the full decision ledger):

{skill_excerpt}

Reminder: identify feature groups whose decisions describe behavior the
current task will touch or depend on. When in doubt, exclude. Output
must use the `submit_relevance_judgment` tool — do not respond in plain
text.
"""


USER_PROMPT_TEMPLATE = """\
Implementation task topic: {topic}

bicameral.history() returned the following ledger:

<ledger>
{ledger_json}
</ledger>

Identify which feature groups are relevant. Call `submit_relevance_judgment` now.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_skill_md() -> str:
    if not SKILL_MD_PATH.exists():
        raise FileNotFoundError(f"SKILL.md not found at {SKILL_MD_PATH}")
    return SKILL_MD_PATH.read_text(encoding="utf-8")


_STEP_HEADER_RE = re.compile(r"^###\s+\d+\.\s+", re.MULTILINE)


def _extract_step1_excerpt(skill_md: str) -> str:
    """Isolate Step 1 (Read the full decision ledger) up to the next Step header.

    Falls back to the entire "## Steps" section if Step 1 cannot be located.
    """
    steps_idx = skill_md.find("## Steps")
    body = skill_md[steps_idx:] if steps_idx != -1 else skill_md

    step1_re = re.compile(r"^###\s+1\.\s+", re.MULTILINE)
    step1_match = step1_re.search(body)
    if not step1_match:
        return body.strip()

    next_header = _STEP_HEADER_RE.search(body, step1_match.end())
    end = next_header.start() if next_header else len(body)
    return body[step1_match.start() : end].strip()


def _cache_path(model: str, skill_sha: str, input_sha: str) -> Path:
    key = f"{model}|{skill_sha}|{input_sha}"
    return CACHE_DIR / f"{_sha(key)}.json"


def _call_messages_api(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
) -> dict:
    """POST to Anthropic Messages API with forced tool-use. Returns the parsed
    `submit_relevance_judgment` input dict."""
    headers = {
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [JUDGMENT_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_relevance_judgment"},
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text[:500]}")
        data = resp.json()

    stop_reason = data.get("stop_reason", "")
    content = data.get("content") or []
    tool_use = next((b for b in content if b.get("type") == "tool_use"), None)
    if tool_use is None:
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        raise RuntimeError(
            f"Anthropic response missing tool_use block "
            f"(stop_reason={stop_reason!r}, text={'|'.join(text_parts)[:300]!r})"
        )
    if stop_reason == "max_tokens":
        raise RuntimeError(f"Anthropic response hit max_tokens={MAX_OUTPUT_TOKENS}")
    judgment = tool_use.get("input")
    if not isinstance(judgment, dict):
        raise RuntimeError(f"tool_use input is not a dict: {judgment!r}")
    judgment.setdefault("relevant_features", [])
    judgment.setdefault("reasoning", "")
    return judgment


def judge_relevance(
    *,
    topic: str,
    ledger: dict,
    model: str | None = None,
    api_key: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Run Step 1 of the current bicameral-preflight SKILL.md against a
    synthetic ledger + topic. Returns the LLM's relevance judgment.

    Result shape: ``{"relevant_features": list[str], "reasoning": str}``.

    Caching: response is cached to ``tests/eval/fixtures/skill_judge/`` keyed
    on (model, SKILL.md SHA, input SHA). Any edit to SKILL.md or the
    topic/ledger pair invalidates the cache for that combination.

    Set ``BICAMERAL_PREFLIGHT_EVAL_RECORD=1`` to bypass the cache and force
    a live API call (re-records the fixture).
    """
    chosen_model = model or os.getenv("BICAMERAL_PREFLIGHT_EVAL_MODEL", DEFAULT_MODEL)
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)

    canonical_input = json.dumps(
        {"topic": topic, "ledger": ledger},
        sort_keys=True,
        ensure_ascii=False,
    )
    input_sha = _sha(canonical_input)

    cache_file = _cache_path(chosen_model, skill_sha, input_sha)
    force_record = os.getenv("BICAMERAL_PREFLIGHT_EVAL_RECORD", "").strip() in {"1", "true", "yes"}

    if use_cache and not force_record and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    chosen_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not chosen_key.strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY missing and no cached fixture exists for "
            f"(model={chosen_model}, skill_sha={skill_sha[:8]}, input_sha={input_sha[:8]}). "
            "Either set the API key to record a new fixture, or check that "
            "the dataset and SKILL.md match the committed cache."
        )

    skill_excerpt = _extract_step1_excerpt(skill_md)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(skill_excerpt=skill_excerpt)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        topic=topic,
        ledger_json=json.dumps(ledger, indent=2, ensure_ascii=False),
    )

    judgment = _call_messages_api(
        model=chosen_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        api_key=chosen_key,
    )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(judgment, indent=2, ensure_ascii=False), encoding="utf-8")
    return judgment


def fixture_exists(*, topic: str, ledger: dict, model: str | None = None) -> bool:
    """Check whether a cached fixture exists for these inputs. Useful for
    tests that need to skip when neither cache nor API key is available."""
    chosen_model = model or os.getenv("BICAMERAL_PREFLIGHT_EVAL_MODEL", DEFAULT_MODEL)
    skill_md = _load_skill_md()
    skill_sha = _sha(skill_md)
    canonical_input = json.dumps(
        {"topic": topic, "ledger": ledger},
        sort_keys=True,
        ensure_ascii=False,
    )
    input_sha = _sha(canonical_input)
    return _cache_path(chosen_model, skill_sha, input_sha).exists()
