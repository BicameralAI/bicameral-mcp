"""Stage 2 LLM extractor — real Anthropic SDK call.

Called only on heuristic-positive messages. Returns a structured dict:
{"decisions": [{"summary": str, "context_snippet": str}], ...}.

Failure modes:
- ANTHROPIC_API_KEY unset: raises MissingAnthropicKeyError (fail-loud).
- HTTP 429: retries with exponential backoff (max 3 attempts).
- HTTP 5xx / network errors: fail-soft, returns
  {"decisions": [], "error": <message>}.
- Unparseable model output: same fail-soft path.
- Non-text content blocks (ToolUseBlock etc.): fail-soft.

Also exports INTERIM_MODEL_VERSION (carried for backwards compat with
v1.0 cache rows that pre-date this real-extractor implementation).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Optional

INTERIM_MODEL_VERSION = "interim-claude-v1"

DEFAULT_MODEL = "claude-haiku-4-5"
PROMPT_TEMPLATE = """You extract DECISIONS from a single chat or document
message. Return STRICT JSON of the shape:
{{"decisions": [{{"summary": "...", "context_snippet": "..."}}]}}

A "decision" is a commitment, choice, or ratification of a course of
action. Casual chatter, questions, and stale-context messages produce
[]. Multiple decisions in one message produce multiple objects.

The pre-classifier matched these triggers: {triggers}.
Use them only as context; do not require them in the output.

Message:
\"\"\"{text}\"\"\""""

PROMPT_TEMPLATE_HASH = hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:8]


class MissingAnthropicKeyError(RuntimeError):
    """Raised at extract-time when ANTHROPIC_API_KEY is not set."""


def _extractor_version() -> str:
    model = os.environ.get("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", DEFAULT_MODEL)
    return f"{model}-extract-{PROMPT_TEMPLATE_HASH}"


def _success(decisions: list, version: str, triggers: list[str]) -> dict:
    return {
        "decisions": decisions,
        "extractor_version": version,
        "matched_triggers": triggers,
    }


def _fail_soft(error: str, version: str, triggers: list[str]) -> dict:
    return {
        "decisions": [],
        "error": error,
        "extractor_version": version,
        "matched_triggers": triggers,
    }


async def _one_attempt(client, model: str, prompt: str) -> tuple[str, object]:
    """Returns ("ok", decisions_list) | ("retry", None) | ("error", str_message).
    'retry' means caller should sleep+retry (429 case). 'error' is terminal."""
    from anthropic import APIError, APIStatusError

    try:
        resp = await client.messages.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIStatusError as exc:
        if exc.status_code == 429:
            return ("retry", None)
        return ("error", f"{exc.status_code}: {str(exc)[:200]}")
    except APIError as exc:
        return ("error", str(exc)[:200])
    try:
        content = resp.content[0].text if resp.content else ""
    except (AttributeError, IndexError) as exc:
        # Non-text content block (ToolUseBlock, ImageBlock, etc.) — fail-soft
        return ("error", f"non-text-content: {exc}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return ("error", f"parse-failure: {exc}")
    return ("ok", parsed.get("decisions", []))


async def extract(text: str, matched_triggers: list[str]) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingAnthropicKeyError(
            "ANTHROPIC_API_KEY env var is required for Stage 2 LLM extraction"
        )
    from anthropic import AsyncAnthropic

    model = os.environ.get("BICAMERAL_TEAM_SERVER_EXTRACT_MODEL", DEFAULT_MODEL)
    version = _extractor_version()
    client = AsyncAnthropic(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(triggers=matched_triggers, text=text)

    last_error = "unknown"
    for attempt in range(3):
        status, payload = await _one_attempt(client, model, prompt)
        if status == "ok":
            return _success(payload, version, matched_triggers)
        if status == "retry" and attempt < 2:
            await asyncio.sleep(2 ** attempt)
            continue
        last_error = str(payload) if payload else "rate-limit-exhausted"
        break
    return _fail_soft(last_error, version, matched_triggers)
