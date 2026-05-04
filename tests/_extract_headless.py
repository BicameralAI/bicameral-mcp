"""Headless extraction driver for the bicameral-ingest skill.

Runs Step 1 of `.claude/skills/bicameral-ingest/SKILL.md` (decision extraction)
against the Anthropic Messages API and returns a natural-format payload
shaped for `handle_ingest`. Phase 5 skill-spec A/B branches simply edit
SKILL.md and the runner picks the change up automatically — the cache is
keyed on the SKILL.md SHA so any edit invalidates prior runs.

This module is intentionally dependency-free beyond httpx (already in the
pilot/mcp venv) — no anthropic SDK, no litellm. The prompt-caching header
is applied so that re-runs within the same branch only pay for transcript
tokens after the first call.

Environment:
    ANTHROPIC_API_KEY   required — sent as the x-api-key header
    M1_EVAL_MODEL       default "claude-haiku-4-5-20251001"

Note: we tried Claude Code OAuth tokens (sk-ant-oat01...) via
Authorization: Bearer but Anthropic's public Messages API rejects
them with "OAuth authentication is currently not supported" (401).
Standard API keys (sk-ant-api03...) authenticate via x-api-key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

# The canonical bicameral-ingest skill lives at
# .claude/skills/bicameral-ingest/SKILL.md. We resolve it relative to
# this file so CI and local dev agree without any env-var dance. Phase 5
# skill-spec A/B branches edit this exact file.
MCP_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD_PATH = MCP_ROOT / ".claude" / "skills" / "bicameral-ingest" / "SKILL.md"
CACHE_DIR = Path(__file__).resolve().parent / ".extract-cache"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 8192
REQUEST_TIMEOUT_S = 120.0

# ── Tool-use schema ────────────────────────────────────────────────
# We use Anthropic tool use to force structured output. The model is
# required to call this tool (via tool_choice) and the response is a
# pre-parsed Python dict — no JSON string parsing on the hot path,
# no unescaped-quote failures, no markdown-fence drift.
EXTRACTION_TOOL = {
    "name": "submit_extraction",
    "description": (
        "Submit the decisions and action items extracted from the transcript. "
        "Must be called exactly once. If nothing qualifies, call with empty arrays."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": (
                    "Implementation-relevant decisions from the transcript. "
                    "One object per decision. Include architectural choices, "
                    "API contracts, data model decisions, technology choices, "
                    "and behavioral requirements."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "A single self-contained sentence describing the decision.",
                        }
                    },
                    "required": ["description"],
                },
            },
            "action_items": {
                "type": "array",
                "description": "Action items with code implications. Owner may be null if not named.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "owner": {"type": ["string", "null"]},
                    },
                    "required": ["text"],
                },
            },
        },
        "required": ["decisions", "action_items"],
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
You are the extraction stage of the Bicameral ingest skill. Apply the rules
below literally, then call the `submit_extraction` tool exactly once with
the extracted decisions and action items.

Rules to apply (from SKILL.md, Step 1 — Extract candidate decisions):

{skill_excerpt}

Reminder: call `submit_extraction` exactly once. Decisions must be
implementation-relevant per the rules above. When in doubt, exclude.
"""

USER_PROMPT_TEMPLATE = """\
Transcript source_ref: {source_ref}

<transcript>
{transcript}
</transcript>

Extract the decisions now and call `submit_extraction`.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_skill_md(skill_md_path: Path) -> str:
    if not skill_md_path.exists():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md_path}")
    return skill_md_path.read_text(encoding="utf-8")


_STEP_HEADER_RE = re.compile(r"^###\s+\d+\.\s+", re.MULTILINE)


def _extract_step1_excerpt(skill_md: str) -> str:
    """Isolate Step 1 ("Extract candidate decisions") including Include/Exclude
    lists, up to the next Step header.

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


def _cache_path(skill_sha: str, transcript_sha: str, model: str) -> Path:
    key = f"{model}|{skill_sha}|{transcript_sha}"
    return CACHE_DIR / f"{_sha(key)}.json"


def _parse_response_json(body: str) -> dict:
    """Parse a model response, tolerating leading/trailing whitespace and
    accidental markdown fences. Raises ValueError with context on failure."""
    text = body.strip()
    if text.startswith("```"):
        # Strip a leading fence (```json or ```) and matching trailing fence.
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"extractor did not return valid JSON: {exc}\nbody={body[:500]}")
    if not isinstance(parsed, dict):
        raise ValueError(f"extractor returned non-object JSON: {type(parsed).__name__}")
    parsed.setdefault("decisions", [])
    parsed.setdefault("action_items", [])
    return parsed


def _call_messages_api(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
) -> dict:
    """POST to Anthropic Messages API with forced tool-use.

    Returns the `input` dict from the model's `submit_extraction` tool call,
    which is a pre-parsed structured object — no JSON string parsing needed.

    On HTTP error, raises RuntimeError with the response body included so the
    CI log shows the exact Anthropic error. We never log the key itself —
    only its length and first 12 chars.
    """
    key_prefix = api_key[:12] if api_key else "(empty)"
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
            # Cache the skill prompt across transcripts within one branch.
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [EXTRACTION_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_extraction"},
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Anthropic API error {resp.status_code}: {resp.text[:500]} "
                f"(model={model}, key_prefix={key_prefix}..., "
                f"key_len={len(api_key)})"
            )
        data = resp.json()

    stop_reason = data.get("stop_reason", "")
    content = data.get("content") or []
    tool_use = next((b for b in content if b.get("type") == "tool_use"), None)
    if tool_use is None:
        # The model responded with text instead of calling the tool — this
        # can happen if tool_choice gets ignored. Emit what we got so the
        # CI log is diagnostic rather than opaque.
        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        raise RuntimeError(
            f"Anthropic response missing tool_use block "
            f"(stop_reason={stop_reason!r}, text={'|'.join(text_parts)[:300]!r})"
        )

    if stop_reason == "max_tokens":
        raise RuntimeError(
            f"Anthropic response hit max_tokens={MAX_OUTPUT_TOKENS} — "
            f"bump MAX_OUTPUT_TOKENS or break the transcript into chunks"
        )

    extracted = tool_use.get("input")
    if not isinstance(extracted, dict):
        raise RuntimeError(f"tool_use input is not a dict: {extracted!r}")
    # Defensive defaults — tool schema requires these but belt-and-braces.
    extracted.setdefault("decisions", [])
    extracted.setdefault("action_items", [])
    return extracted


def extract_from_current_skill(
    transcript: str,
    *,
    source_ref: str = "",
    skill_md_path: Path | None = None,
    model: str | None = None,
    api_key: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Run Step 1 of the current bicameral-ingest SKILL.md on a transcript.

    Returns a payload shaped for handlers/ingest.py's natural-format path:
        {"decisions": [{"description": str}], "action_items": [{"text": str, "owner": str|None}]}

    The response is cached to tests/.extract-cache/ keyed on
    (model, SKILL.md SHA, transcript SHA). Any edit to SKILL.md invalidates
    prior cache entries for that branch.

    Auth: uses `api_key` (or ANTHROPIC_API_KEY env var) sent as the
    `x-api-key` header. In CI the key is provided as a GitHub environment
    secret in the `ci-test` environment and exported into the step env.
    """
    skill_md_path = skill_md_path or SKILL_MD_PATH
    skill_md = _load_skill_md(skill_md_path)
    skill_sha = _sha(skill_md)
    transcript_sha = _sha(transcript)
    chosen_model = model or os.getenv("M1_EVAL_MODEL", DEFAULT_MODEL)

    cache_file = _cache_path(skill_sha, transcript_sha, chosen_model)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    chosen_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not chosen_key.strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is missing or empty — the env var resolved "
            "to '' which means the GitHub secret reference did not expand. "
            "Check that the secret exists in the `ci-test` environment "
            "(Settings → Environments → ci-test → Environment secrets) "
            "and that the workflow job declares `environment: ci-test`."
        )

    skill_excerpt = _extract_step1_excerpt(skill_md)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(skill_excerpt=skill_excerpt)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        source_ref=source_ref or "(unspecified)",
        transcript=transcript,
    )

    parsed = _call_messages_api(
        model=chosen_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        api_key=chosen_key,
    )

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    return parsed
