"""Request-scoped snapshot pinning CodeGraph and Ledger to the same git ref."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Generated once per server process — all tool calls in the same session share it.
_SESSION_ID: str = str(uuid.uuid4())


_GUIDED_MODE_TRUTHY = frozenset({"1", "true", "yes", "on"})
_GUIDED_MODE_FALSY = frozenset({"0", "false", "no", "off", ""})

_SIGNER_FALLBACK_MODES = frozenset({"redact", "local-part-only", "full"})
_DEFAULT_SIGNER_FALLBACK_MODE = "local-part-only"

_RENDER_ATTRIBUTION_MODES = frozenset({"full", "redacted", "hidden"})
# v1 default is `full` (legacy verbatim) for backward-compat with the e2e
# harness's agent-parsing of source_refs. The current `redacted` regex is
# overbroad — it replaces all `[A-Z][a-z]+` patterns including meaningful
# tokens like "Sprint", "Linear", "Slack", which strips the source_ref of
# agent-parseable structure. Default flips to `redacted` once the regex
# is refined to match only true name/date patterns. Tracked separately;
# config field already exposes the privacy-positive options for opt-in.
_DEFAULT_RENDER_ATTRIBUTION_MODE = "full"

_BYPASS_TRACKING_MODES = frozenset({"enabled", "disabled"})
_DEFAULT_BYPASS_TRACKING_MODE = "enabled"


def _read_yaml_string_field(repo_path: str, key: str, valid: frozenset[str], default: str) -> str:
    """Generic reader for a `.bicameral/config.yaml` string field with a
    fixed valid-set and fail-soft default. Returns the raw config value
    if it's in the valid set; falls back to default on missing file,
    malformed yaml, missing key, or invalid value."""
    config_path = Path(repo_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return default
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        val = config.get(key, default)
        if val in valid:
            return val
    except Exception:
        pass
    return default


def _read_signer_email_fallback(repo_path: str) -> str:
    """Resolve `signer_email_fallback` from `.bicameral/config.yaml`.

    Default: ``"local-part-only"`` (privacy-positive). Modes: ``redact``,
    ``local-part-only``, ``full``."""
    return _read_yaml_string_field(
        repo_path,
        "signer_email_fallback",
        _SIGNER_FALLBACK_MODES,
        _DEFAULT_SIGNER_FALLBACK_MODE,
    )


def _read_render_source_attribution(repo_path: str) -> str:
    """Resolve `render_source_attribution` from `.bicameral/config.yaml`.

    Default: ``"redacted"`` (privacy-positive — replaces names + dates
    with placeholders, preserves structural shape). Modes: ``full``,
    ``redacted``, ``hidden``. Read by ``handlers.preflight._apply_attribution_policy``
    to filter ``DecisionMatch.source_ref`` before it returns to the agent."""
    return _read_yaml_string_field(
        repo_path,
        "render_source_attribution",
        _RENDER_ATTRIBUTION_MODES,
        _DEFAULT_RENDER_ATTRIBUTION_MODE,
    )


def _read_preflight_bypass_tracking(repo_path: str) -> str:
    """Resolve `preflight_bypass_tracking` from `.bicameral/config.yaml`.

    Default: ``"enabled"`` (backward-compat with pre-#200 behavior; lift
    candidate for a later deprecation cycle). Modes: ``enabled``,
    ``disabled``. When disabled, ``handlers.record_bypass.handle_record_bypass``
    short-circuits before the JSONL write to ``~/.bicameral/preflight_events.jsonl``."""
    return _read_yaml_string_field(
        repo_path,
        "preflight_bypass_tracking",
        _BYPASS_TRACKING_MODES,
        _DEFAULT_BYPASS_TRACKING_MODE,
    )


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
    # CodeGenome adapter + config (#59). ``codegenome`` is a
    # ``CodeGenomeAdapter`` instance from ``adapters/codegenome.py``;
    # ``codegenome_config`` is a ``CodeGenomeConfig`` carrying the feature
    # flags. Both are populated by ``from_env()``; tests construct them
    # explicitly. Defaults are ``None`` so older test contexts that
    # haven't been updated keep working — handlers null-check both.
    codegenome: object | None = None
    codegenome_config: object | None = None
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
    # v0.7.0: server-session UUID — same for all tool calls in one server process.
    # Used to tag proposed/ratified signoff objects with their originating session.
    session_id: str = field(default_factory=lambda: _SESSION_ID)
    # #200 Phase 2: signer-email fallback policy. Read at server start from
    # `.bicameral/config.yaml: signer_email_fallback`. Applied by
    # `events.writer._resolve_signer_email` to raw git user.email values
    # before they land in the ledger. Privacy-positive default
    # (`local-part-only`) preserves attribution prefix without leaking a
    # directly-mailable address. Modes: `redact`, `local-part-only`, `full`.
    signer_email_fallback: str = _DEFAULT_SIGNER_FALLBACK_MODE
    # #200 Phase 3: render_source_attribution gates how DecisionMatch.source_ref
    # lines render to the agent. Default `redacted` strips name + date patterns
    # while preserving structural shape. Modes: `full` (legacy verbatim),
    # `redacted` (default), `hidden` (blank source_ref entirely).
    render_source_attribution: str = _DEFAULT_RENDER_ATTRIBUTION_MODE
    # #200 Phase 3: preflight_bypass_tracking gates the JSONL write to
    # ~/.bicameral/preflight_events.jsonl. Default `enabled` matches pre-#200
    # behavior; `disabled` makes record_bypass a no-op (returns recorded=False
    # with reason="tracking_disabled"). Operator privacy choice; deterministic
    # at config-load time.
    preflight_bypass_tracking: str = _DEFAULT_BYPASS_TRACKING_MODE
    # v0.4.8: mutable cache for within-call sync dedup. Frozen-dataclass-safe
    # because the reference stays pinned; only the dict's contents mutate.
    # Keys: ``last_sync_sha`` (str). Cleared by any handler that mutates
    # repo-state expectations before chaining downstream tools.
    # #200 Phase 2: also stores the session-scoped `seen_ingest_warning`
    # flag (set by `bicameral-ingest` Step 0.6 after the first pre-ingest
    # leak warning is shown; gates re-display on subsequent ingests in the
    # same session). Read via `seen_ingest_warning` property; set via
    # `set_seen_ingest_warning(bool)`.
    _sync_state: dict = field(default_factory=dict)

    @property
    def seen_ingest_warning(self) -> bool:
        """True if the pre-ingest leak warning has been shown this session."""
        return bool(self._sync_state.get("seen_ingest_warning", False))

    def set_seen_ingest_warning(self, value: bool) -> None:
        """Set the session-scoped flag. Frozen-dataclass-safe — mutates
        the `_sync_state` dict's contents, not the dataclass field."""
        self._sync_state["seen_ingest_warning"] = bool(value)

    @classmethod
    def from_env(cls) -> BicameralContext:
        from adapters.code_locator import get_code_locator
        from adapters.codegenome import get_codegenome
        from adapters.ledger import get_drift_analyzer, get_ledger
        from code_locator_runtime import (
            detect_authoritative_ref,
            get_repo_index_state,
            resolve_ref_sha,
        )
        from codegenome.config import CodeGenomeConfig

        repo_path = os.getenv("REPO_PATH", ".")
        state = get_repo_index_state(repo_path)
        authoritative_ref = detect_authoritative_ref(repo_path)
        authoritative_sha = resolve_ref_sha(repo_path, authoritative_ref) or ""
        guided_mode = _read_guided_mode(repo_path)
        signer_email_fallback = _read_signer_email_fallback(repo_path)
        render_source_attribution = _read_render_source_attribution(repo_path)
        preflight_bypass_tracking = _read_preflight_bypass_tracking(repo_path)

        return cls(
            repo_path=repo_path,
            head_sha=state.head_commit,
            ledger=get_ledger(),
            code_graph=get_code_locator(),
            drift_analyzer=get_drift_analyzer(),
            codegenome=get_codegenome(),
            codegenome_config=CodeGenomeConfig.from_env(),
            authoritative_ref=authoritative_ref,
            authoritative_sha=authoritative_sha,
            guided_mode=guided_mode,
            signer_email_fallback=signer_email_fallback,
            render_source_attribution=render_source_attribution,
            preflight_bypass_tracking=preflight_bypass_tracking,
        )
