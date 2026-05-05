"""Extraction pipeline — Stage 1 (heuristic classifier) → Stage 2 (LLM).

Single entry point for both Slack and Notion workers. Determines the
output shape regardless of source: {decisions, classifier_version,
matched_triggers, extractor_version, skipped}. extractor_version is
None when Stage 2 did not run (chatter or rules-disabled).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from team_server.config import RulesDisabled
from team_server.extraction.heuristic_classifier import (
    TriggerRules,
    classify,
    derive_classifier_version,
)

LLMExtractFn = Callable[[str, list[str]], Awaitable[dict]]


async def extract_decision_pipeline(
    *,
    text: str,
    message: dict,
    context: dict,
    rules_or_disabled: TriggerRules | RulesDisabled,
    llm_extract_fn: LLMExtractFn | None = None,
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
    classification = classify({**message, "text": text}, context, rules)
    if not classification.is_positive:
        return {
            "decisions": [],
            "classifier_version": cv,
            "matched_triggers": list(classification.matched_triggers),
            "extractor_version": None,
            "skipped": False,
        }
    if llm_extract_fn is None:
        from team_server.extraction.llm_extractor import extract as default_extract

        llm_extract_fn = default_extract
    llm_result = await llm_extract_fn(text, list(classification.matched_triggers))
    return {
        "decisions": llm_result.get("decisions", []),
        "classifier_version": cv,
        "matched_triggers": list(classification.matched_triggers),
        "extractor_version": llm_result.get("extractor_version"),
        "error": llm_result.get("error"),
        "skipped": False,
    }
