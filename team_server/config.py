"""Team-server configuration loader — YAML in, pydantic-validated out.

Strict schema: missing required fields raise ValueError (caller surfaces
the message to the operator at startup).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("BICAMERAL_CONFIG_PATH", "/etc/bicameral-team-server/config.yml")
)


class WorkspaceConfig(BaseModel):
    team_id: str = Field(..., description="Slack team ID (e.g., T01ABCDEF)")
    channels: list[str] = Field(default_factory=list)


class SlackConfig(BaseModel):
    workspaces: list[WorkspaceConfig] = Field(default_factory=list)


class TeamServerConfig(BaseModel):
    slack: SlackConfig = Field(default_factory=SlackConfig)


def load_channel_allowlist(path: Path) -> TeamServerConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return TeamServerConfig(**raw)
    except ValidationError as exc:
        # Re-raise as ValueError per plan contract; surface field errors.
        msg_parts = [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ValueError(
            f"team-server config invalid: {'; '.join(msg_parts)}"
        ) from exc
