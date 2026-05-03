"""Slack ingest worker — polls allowlisted channels, runs the
extraction pipeline (heuristic Stage 1 → optional LLM Stage 2), writes
peer-authored team_event per change.

Idempotent: same Slack message ts with unchanged content + classifier
version yields no new team_event row.

When `config` is None, falls back to the legacy `extractor(text)` path
for backwards compat with v1.0 callers (channel_allowlist test suite,
direct poll_once test invocations). When `config` is provided, the
pipeline runs with rules resolved per channel.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterable

from ledger.client import LedgerClient
from team_server.config import (
    RulesDisabled,
    TeamServerConfig,
    resolve_rules_for_slack,
)
from team_server.extraction.canonical_cache import upsert_canonical_extraction
from team_server.extraction.heuristic_classifier import derive_classifier_version
from team_server.extraction.llm_extractor import INTERIM_MODEL_VERSION
from team_server.extraction.pipeline import extract_decision_pipeline
from team_server.sync.peer_writer import write_team_event

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]
LLMExtractFn = Callable[[str, list], Awaitable[dict]]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_ref_for_message(channel: str, ts: str) -> str:
    return f"{channel}/{ts}"


def _slack_context(message: dict, position: int) -> dict:
    return {
        "reactions": message.get("reactions") or [],
        "thread_position": position,
        "thread_ts": message.get("thread_ts"),
        "subtype": message.get("subtype"),
    }


async def poll_once(
    db_client: LedgerClient,
    slack_client,
    workspace_team_id: str,
    channels: Iterable[str],
    extractor: Extractor,
    *,
    config: TeamServerConfig | None = None,
    llm_extract_fn: LLMExtractFn | None = None,
) -> None:
    """One polling pass over allowlisted channels."""
    for channel in channels:
        history = slack_client.conversations_history(channel=channel)
        if not history.get("ok", False):
            logger.warning("[slack-worker] history failed for %s", channel)
            continue
        messages = history.get("messages", [])
        for position, message in enumerate(messages):
            await _ingest_message(
                db_client,
                workspace_team_id,
                channel,
                message,
                extractor,
                position=position,
                config=config,
                llm_extract_fn=llm_extract_fn,
            )


def _resolve_classifier_version(
    config: TeamServerConfig | None,
    channel: str,
) -> tuple[str, object]:
    if config is None:
        return "legacy-pre-v3", None
    rules_or_disabled = resolve_rules_for_slack(config, channel)
    if isinstance(rules_or_disabled, RulesDisabled):
        return "rules-disabled", rules_or_disabled
    return derive_classifier_version(rules_or_disabled), rules_or_disabled


async def _ingest_message(
    db_client: LedgerClient,
    workspace_team_id: str,
    channel: str,
    message: dict,
    extractor: Extractor,
    *,
    position: int,
    config: TeamServerConfig | None,
    llm_extract_fn: LLMExtractFn | None,
) -> None:
    text = message.get("text", "")
    ts = message.get("ts", "")
    source_ref = _source_ref_for_message(channel, ts)
    content_hash = _content_hash(text)
    classifier_version, rules_or_disabled = _resolve_classifier_version(config, channel)

    async def compute():
        if rules_or_disabled is None:
            return await extractor(text)
        return await extract_decision_pipeline(
            text=text,
            message=message,
            context=_slack_context(message, position),
            rules_or_disabled=rules_or_disabled,
            llm_extract_fn=llm_extract_fn,
        )

    extraction, changed = await upsert_canonical_extraction(
        db_client,
        source_type="slack",
        source_ref=source_ref,
        content_hash=content_hash,
        classifier_version=classifier_version,
        compute_fn=compute,
        model_version=INTERIM_MODEL_VERSION,
    )
    if not changed:
        return
    await write_team_event(
        db_client,
        workspace_team_id=workspace_team_id,
        event_type="ingest",
        payload={
            "source_type": "slack",
            "source_ref": source_ref,
            "content_hash": content_hash,
            "extraction": extraction,
        },
    )
