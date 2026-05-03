"""Team-server configuration loader — YAML in, pydantic-validated out.

Strict schema: missing required fields raise ValueError (caller surfaces
the message to the operator at startup). v1.1 adds heuristic trigger
rules per workspace + per-channel/database overrides.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from team_server.extraction.heuristic_classifier import TriggerRules

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("BICAMERAL_CONFIG_PATH", "/etc/bicameral-team-server/config.yml")
)


class WorkspaceConfig(BaseModel):
    team_id: str = Field(..., description="Slack team ID (e.g., T01ABCDEF)")
    channels: list[str] = Field(default_factory=list)


class HeuristicGlobalRules(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    keyword_negatives: list[str] = Field(default_factory=list)
    min_word_count: int = 0
    boost_reactions: list[str] = Field(default_factory=list)
    boost_threshold: int = 1
    thread_tail_position_threshold: int | None = None
    enabled: bool = True
    learned_denylist: list[str] = Field(default_factory=list)


class HeuristicScopedOverride(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    keyword_negatives: list[str] = Field(default_factory=list)
    min_word_count: int | None = None
    enabled: bool = True


class SlackHeuristics(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    global_rules: HeuristicGlobalRules = Field(default_factory=HeuristicGlobalRules, alias="global")
    channels: dict[str, HeuristicScopedOverride] = Field(default_factory=dict)


class NotionHeuristics(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    global_rules: HeuristicGlobalRules = Field(default_factory=HeuristicGlobalRules, alias="global")
    databases: dict[str, HeuristicScopedOverride] = Field(default_factory=dict)


class SlackConfig(BaseModel):
    workspaces: list[WorkspaceConfig] = Field(default_factory=list)
    heuristics: SlackHeuristics = Field(default_factory=SlackHeuristics)


class NotionConfig(BaseModel):
    token: str | None = None
    heuristics: NotionHeuristics = Field(default_factory=NotionHeuristics)


class CorpusLearnerConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = 86400
    top_n: int = 50


class TeamServerConfig(BaseModel):
    slack: SlackConfig = Field(default_factory=SlackConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)
    corpus_learner: CorpusLearnerConfig = Field(default_factory=CorpusLearnerConfig)


class RulesDisabled:
    """Sentinel returned by resolve_rules_* when a channel/db is opted out."""


def load_channel_allowlist(path: Path) -> TeamServerConfig:
    return load_rules_from_config(path)


def load_rules_from_config(path: str | Path) -> TeamServerConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        return TeamServerConfig(**raw)
    except ValidationError as exc:
        msg_parts = [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        ]
        raise ValueError(f"team-server config invalid: {'; '.join(msg_parts)}") from exc


def _build_rules(
    base: HeuristicGlobalRules,
    override: HeuristicScopedOverride | None,
    learned: tuple[str, ...] = (),
) -> TriggerRules:
    return TriggerRules(
        keywords=tuple([*base.keywords, *(override.keywords if override else [])]),
        keyword_negatives=tuple(
            [
                *base.keyword_negatives,
                *(override.keyword_negatives if override else []),
            ]
        ),
        min_word_count=(
            override.min_word_count
            if override and override.min_word_count is not None
            else base.min_word_count
        ),
        boost_reactions=tuple(base.boost_reactions),
        boost_threshold=base.boost_threshold,
        thread_tail_position_threshold=base.thread_tail_position_threshold,
        learned_keywords=learned,
    )


def resolve_rules_for_slack(
    config: TeamServerConfig,
    channel_id: str,
    learned: tuple[str, ...] = (),
) -> TriggerRules | RulesDisabled:
    base = config.slack.heuristics.global_rules
    override = config.slack.heuristics.channels.get(channel_id)
    if not base.enabled or (override and not override.enabled):
        return RulesDisabled()
    return _build_rules(base, override, learned)


def resolve_rules_for_notion(
    config: TeamServerConfig,
    db_id: str,
    learned: tuple[str, ...] = (),
) -> TriggerRules | RulesDisabled:
    base = config.notion.heuristics.global_rules
    override = config.notion.heuristics.databases.get(db_id)
    if not base.enabled or (override and not override.enabled):
        return RulesDisabled()
    return _build_rules(base, override, learned)
