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
    CLAUDE_CODE_OAUTH_TOKEN  required — sent as Authorization: Bearer <token>
    M1_EVAL_MODEL            default "claude-haiku-4-5-20251001"
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

# The canonical bicameral-ingest skill lives inside the MCP submodule
# (tracked at pilot/mcp/.claude/skills/bicameral-ingest/SKILL.md). We
# resolve it relative to this file so CI and local dev agree without any
# env-var dance. Phase 5 skill-spec A/B branches edit this exact file.
MCP_ROOT = Path(__file__).resolve().parents[1]  # pilot/mcp
SKILL_MD_PATH = MCP_ROOT / ".claude" / "skills" / "bicameral-ingest" / "SKILL.md"
CACHE_DIR = Path(__file__).resolve().parent / ".extract-cache"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_OUTPUT_TOKENS = 4096
REQUEST_TIMEOUT_S = 120.0

SYSTEM_PROMPT_TEMPLATE = """\
You are the extraction stage of the Bicameral ingest skill. Apply the rules
below literally. Return STRICT JSON matching this exact shape and nothing else:

{{"decisions": [{{"description": "..."}}], "action_items": [{{"text": "...", "owner": null}}]}}

Rules to apply (from SKILL.md, Step 1 — Extract candidate decisions):

{skill_excerpt}

Hard constraints:
- Output ONLY the JSON object. No prose, no markdown fences, no commentary.
- `decisions` contains implementation-relevant decisions (architectural, API
  contract, data model, technology choice, behavioral requirement, action
  item with code implications). One object per decision. `description` must
  be a single self-contained sentence.
- `action_items` contains tasks with explicit owners. `owner` may be null
  if no owner was named.
- If nothing qualifies, return {{"decisions": [], "action_items": []}}.
"""

USER_PROMPT_TEMPLATE = """\
Transcript source_ref: {source_ref}

<transcript>
{transcript}
</transcript>

Extract the decisions now. Return only the JSON object.
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

    headers = list(_STEP_HEADER_RE.finditer(body))
    if not headers:
        return body.strip()

    first = headers[0]
    second_start = headers[1].start() if len(headers) >= 2 else len(body)
    return body[first.start():second_start].strip()


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
    oauth_token: str,
) -> str:
    """POST to Anthropic Messages API. Returns the concatenated text content.

    On HTTP error, raises RuntimeError with the response body included so the
    CI log shows the exact Anthropic error (e.g. ``invalid_api_key`` vs
    ``unauthorized_scope``). We never log the token itself — only its length
    prefix, which is enough to distinguish sk-ant-api03 (API key) from
    sk-ant-oat01 (OAuth token) without leaking material.
    """
    token_kind = oauth_token[:12] if oauth_token else "(empty)"
    headers = {
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
        "authorization": f"Bearer {oauth_token}",
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
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Anthropic API error {resp.status_code}: {resp.text[:500]} "
                f"(model={model}, token_prefix={token_kind}..., "
                f"token_len={len(oauth_token)})"
            )
        data = resp.json()

    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text:
        raise RuntimeError(f"Anthropic API returned no text content: {data}")
    return text


def extract_from_current_skill(
    transcript: str,
    *,
    source_ref: str = "",
    skill_md_path: Path | None = None,
    model: str | None = None,
    oauth_token: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Run Step 1 of the current bicameral-ingest SKILL.md on a transcript.

    Returns a payload shaped for handlers/ingest.py's natural-format path:
        {"decisions": [{"description": str}], "action_items": [{"text": str, "owner": str|None}]}

    The response is cached to tests/.extract-cache/ keyed on
    (model, SKILL.md SHA, transcript SHA). Any edit to SKILL.md invalidates
    prior cache entries for that branch.

    Auth: uses `oauth_token` (or CLAUDE_CODE_OAUTH_TOKEN env var) sent as
    `Authorization: Bearer <token>`. In CI the token is provided as a
    repo-level GitHub secret and exported into the step env.
    """
    skill_md_path = skill_md_path or SKILL_MD_PATH
    skill_md = _load_skill_md(skill_md_path)
    skill_sha = _sha(skill_md)
    transcript_sha = _sha(transcript)
    chosen_model = model or os.getenv("M1_EVAL_MODEL", DEFAULT_MODEL)

    cache_file = _cache_path(skill_sha, transcript_sha, chosen_model)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    chosen_oauth = oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not chosen_oauth.strip():
        raise RuntimeError(
            "CLAUDE_CODE_OAUTH_TOKEN is missing or empty — the env var "
            "resolved to '' which means the GitHub secret reference did "
            "not expand. Check that the secret exists at the repository "
            "(or org with this repo in allowed-repositories) and that the "
            "workflow step's `env:` block maps it correctly."
        )

    skill_excerpt = _extract_step1_excerpt(skill_md)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(skill_excerpt=skill_excerpt)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        source_ref=source_ref or "(unspecified)",
        transcript=transcript,
    )

    body = _call_messages_api(
        model=chosen_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        oauth_token=chosen_oauth,
    )
    parsed = _parse_response_json(body)

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    return parsed
