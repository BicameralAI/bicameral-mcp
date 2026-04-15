"""Request-scoped snapshot pinning CodeGraph and Ledger to the same git ref."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_GUIDED_MODE_TRUTHY = frozenset({"1", "true", "yes", "on"})
_GUIDED_MODE_FALSY = frozenset({"0", "false", "no", "off", ""})


def _read_guided_mode(repo_path: str) -> bool:
    """Resolve guided-mode flag for this MCP call.

    Precedence:
      1. ``BICAMERAL_GUIDED_MODE`` env var (truthy / falsy) — one-off override
      2. ``guided: true/false`` in ``<repo>/.bicameral/config.yaml`` — durable
         setting chosen at ``bicameral setup`` time
      3. Default: ``False`` (normal mode — action hints still fire, but as
         non-blocking advisories)
    """
    env_val = os.getenv("BICAMERAL_GUIDED_MODE", "").strip().lower()
    if env_val in _GUIDED_MODE_TRUTHY:
        return True
    if env_val in _GUIDED_MODE_FALSY and env_val != "":
        return False

    config_path = Path(repo_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return False
    try:
        import yaml
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return bool(config.get("guided", False))
    except Exception:
        # yaml missing or bad file — fall back to line-oriented parse so we
        # don't silently lose the setting when the yaml dep isn't installed.
        try:
            for line in config_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("guided:"):
                    val = stripped.split(":", 1)[1].strip().strip("\"'").lower()
                    return val in _GUIDED_MODE_TRUTHY
        except OSError:
            pass
    return False


@dataclass(frozen=True)
class BicameralContext:
    """Created once per MCP tool call. All services see the same commit."""

    repo_path: str
    head_sha: str
    ledger: object
    code_graph: object
    drift_analyzer: object
    authoritative_ref: str = "main"
    authoritative_sha: str = ""
    # v0.4.10: guided mode dials up the intensity of ``action_hints`` emitted
    # by search and brief handlers. In normal mode (``guided_mode=False``)
    # hints still fire when findings exist but are advisory
    # (``blocking=False``). In guided mode they become blocking — the skill
    # contract forbids write operations until each is addressed. Durable
    # setting lives in ``.bicameral/config.yaml`` (chosen at setup time);
    # env var ``BICAMERAL_GUIDED_MODE`` is a one-off override.
    guided_mode: bool = False
    # v0.4.8: mutable cache for within-call sync dedup. Frozen-dataclass-safe
    # because the reference stays pinned; only the dict's contents mutate.
    # Keys: ``last_sync_sha`` (str). Cleared by any handler that mutates
    # repo-state expectations before chaining downstream tools.
    _sync_state: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> BicameralContext:
        from adapters.code_locator import get_code_locator
        from adapters.ledger import get_drift_analyzer, get_ledger
        from code_locator_runtime import detect_authoritative_ref, get_repo_index_state, resolve_ref_sha

        repo_path = os.getenv("REPO_PATH", ".")
        state = get_repo_index_state(repo_path)
        authoritative_ref = detect_authoritative_ref(repo_path)
        authoritative_sha = resolve_ref_sha(repo_path, authoritative_ref) or ""
        guided_mode = _read_guided_mode(repo_path)

        return cls(
            repo_path=repo_path,
            head_sha=state.head_commit,
            ledger=get_ledger(),
            code_graph=get_code_locator(),
            drift_analyzer=get_drift_analyzer(),
            authoritative_ref=authoritative_ref,
            authoritative_sha=authoritative_sha,
            guided_mode=guided_mode,
        )
