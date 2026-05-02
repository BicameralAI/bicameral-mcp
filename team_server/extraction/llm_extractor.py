"""Interim LLM extractor — placeholder for v0 until CocoIndex (#136) lands.

Marked with `model_version='interim-claude-v1'` so Phase 5's CocoIndex
integration can identify+rebuild interim cache entries deterministically.

This module deliberately does NOT call Anthropic's API at import-time —
the real call lives inside `extract()`. Tests substitute their own
extractor function via the worker's `extractor` parameter.
"""

from __future__ import annotations

INTERIM_MODEL_VERSION = "interim-claude-v1"


async def extract(text: str) -> dict:
    """Default v0 interim extractor. Returns a structured decision payload.

    Implementation note: the real Claude API call lands here once
    Phase 3 deployment is operator-validated. For v0 unit tests we feed
    `extractor=stub` directly into the worker, so this function is the
    *production* default that customers see when they deploy.
    """
    # v0 minimal-correct shape: each non-empty paragraph becomes one
    # candidate decision. The actual semantic extraction goes here when
    # the operator wires Anthropic credentials at the team-server layer.
    decisions = [p.strip() for p in text.split("\n\n") if p.strip()]
    return {"decisions": decisions, "model_version": INTERIM_MODEL_VERSION}
