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
_DEFAULT_RENDER_ATTRIBUTION_MODE = "redacted"  # #209: flipped from "full"; positional-cue regex refined

_BYPASS_TRACKING_MODES = frozenset({"enabled", "disabled"})
_DEFAULT_BYPASS_TRACKING_MODE = "enabled"

# #216 LLM-02: payload-size guardrail. 1 MiB cap is loose enough for
# any legitimate transcript / decision dump but bounds DoS shape
# (single oversized payload can't blow out the ledger writer).
_DEFAULT_INGEST_MAX_BYTES = 1024 * 1024  # 1 MiB
_INGEST_MAX_BYTES_MIN = 1024  # 1 KiB; below this is meaningless / config error
_INGEST_MAX_BYTES_MAX = 64 * 1024 * 1024  # 64 MiB; above this is operator footgun

# #216 LLM-08: token-bucket rate limit per session_id.
# Default 10-token burst with 1 token/sec refill: an agent can fire 10
# ingests instantly, then sustain 1/sec. Tuned for single-user
# developer-tool workflow shape; stricter sliding-window enforcement
# is a team-server activation concern (revisit then).
_DEFAULT_INGEST_RATE_LIMIT_BURST = 10
_DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC = 1.0
_INGEST_RATE_LIMIT_BURST_MIN = 1
_INGEST_RATE_LIMIT_BURST_MAX = 1000
_INGEST_RATE_LIMIT_REFILL_MIN = 0.01
_INGEST_RATE_LIMIT_REFILL_MAX = 100.0


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


def _resolve_agent_identity(repo_path: str) -> str:
    """Resolve a stable, opaque agent-identity for ``BicameralContext.session_id``.

    Returns a 16-char hex hash derived from ``git config user.email`` salted
    with the per-install ``preflight_telemetry`` salt. Single-developer
    installs get one stable identifier across server restarts; team-server
    installs get one identifier per developer (per-developer rate-limit
    bucket isolation). Cross-install correlation broken by the per-install
    salt; cross-session-within-one-install correlation preserved (useful
    for ledger-side attribution).

    Falls back to the process-wide ``_SESSION_ID`` UUID when git config
    is unreadable (no git, no user.email, subprocess failure). Falls back
    to ``_SESSION_ID`` also when the salt file is unreadable / unwriteable
    (filesystem-locked or test/CI sandbox).

    **Side effect**: ``_get_or_create_salt()`` creates ``~/.bicameral/salt``
    on first call across the entire bicameral-mcp install (not just this
    handler). The first invocation of ``_resolve_agent_identity`` may
    therefore initialize the salt file ahead of any preflight-telemetry
    call. This is acceptable — the salt file is per-install state that
    needs to exist regardless of which subsystem first reads it; both
    consumers see the same value via the file's idempotent creation
    semantics (race-safe via ``os.O_EXCL`` per the existing implementation
    at ``preflight_telemetry.py:97-118``).

    #231 v1 option (α): email-derived identity. Option (β) per-MCP-session
    granularity is deferred until team-server protocol activation surfaces
    a per-conversation session-id from the agent host.
    """
    try:
        from events.writer import _get_git_email
        from preflight_telemetry import _get_or_create_salt
    except Exception:
        return _SESSION_ID
    try:
        email = _get_git_email(repo_path)
    except Exception:
        return _SESSION_ID
    if email == "unknown" or not email:
        return _SESSION_ID
    try:
        salt = _get_or_create_salt()
    except Exception:
        return _SESSION_ID
    import hashlib

    return hashlib.sha256(salt + email.encode("utf-8")).hexdigest()[:16]


def _read_ingest_max_bytes(repo_path: str) -> int:
    """Resolve ``ingest_max_bytes`` from ``.bicameral/config.yaml``.

    Default 1 MiB. Clamped to ``[_INGEST_MAX_BYTES_MIN, _INGEST_MAX_BYTES_MAX]``;
    out-of-range values (negative, non-integer, beyond clamp) fall back
    to the default with no silent acceptance. Read by
    ``handlers.ingest._check_payload_size``.
    """
    config_path = Path(repo_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return _DEFAULT_INGEST_MAX_BYTES
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        val = config.get("ingest_max_bytes", _DEFAULT_INGEST_MAX_BYTES)
    except Exception:
        return _DEFAULT_INGEST_MAX_BYTES
    if not isinstance(val, int) or isinstance(val, bool):
        return _DEFAULT_INGEST_MAX_BYTES
    if val < _INGEST_MAX_BYTES_MIN or val > _INGEST_MAX_BYTES_MAX:
        return _DEFAULT_INGEST_MAX_BYTES
    return val


def _read_ingest_rate_limit_burst(repo_path: str) -> int:
    """Resolve ``ingest_rate_limit_burst`` from ``.bicameral/config.yaml``.

    Default 10. Clamped to ``[_INGEST_RATE_LIMIT_BURST_MIN,
    _INGEST_RATE_LIMIT_BURST_MAX]``. Out-of-range / non-int / malformed
    yaml all fall back to default (no silent acceptance).
    """
    config_path = Path(repo_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return _DEFAULT_INGEST_RATE_LIMIT_BURST
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        val = config.get("ingest_rate_limit_burst", _DEFAULT_INGEST_RATE_LIMIT_BURST)
    except Exception:
        return _DEFAULT_INGEST_RATE_LIMIT_BURST
    if not isinstance(val, int) or isinstance(val, bool):
        return _DEFAULT_INGEST_RATE_LIMIT_BURST
    if val < _INGEST_RATE_LIMIT_BURST_MIN or val > _INGEST_RATE_LIMIT_BURST_MAX:
        return _DEFAULT_INGEST_RATE_LIMIT_BURST
    return val


def _read_ingest_rate_limit_refill_per_sec(repo_path: str) -> float:
    """Resolve ``ingest_rate_limit_refill_per_sec`` from ``.bicameral/config.yaml``.

    Default 1.0. Clamped to ``[_INGEST_RATE_LIMIT_REFILL_MIN,
    _INGEST_RATE_LIMIT_REFILL_MAX]``. ``0.0`` would lock the bucket
    forever after first burst — treated as malformed and falls back
    to default. Out-of-range / non-numeric / malformed yaml all fall
    back to default.
    """
    config_path = Path(repo_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        val = config.get(
            "ingest_rate_limit_refill_per_sec",
            _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC,
        )
    except Exception:
        return _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    val_f = float(val)
    # NaN evades both `< MIN` and `> MAX` (every comparison with NaN is
    # False), so an operator who writes ``refill: .nan`` would land NaN
    # in the bucket and lock ingest forever (`min(burst, x + dt*nan) =
    # nan`, and ``nan >= 1.0`` is False — bucket is permanently empty).
    # ``math.isfinite()`` rejects NaN + inf in one check.
    import math

    if not math.isfinite(val_f):
        return _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    if val_f < _INGEST_RATE_LIMIT_REFILL_MIN or val_f > _INGEST_RATE_LIMIT_REFILL_MAX:
        return _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    return val_f


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
    # v0.7.0 + #231 v1: agent-identity field — populated by `from_env()` via
    # `_resolve_agent_identity(repo_path)` to a per-developer salted email-hash
    # (16-char hex). Same developer across server restarts gets the same
    # identifier (useful for ledger-side attribution); different developers on
    # the same install get different identifiers (per-developer rate-limit
    # bucket isolation in `handlers/ingest.py:_RATE_LIMIT_REGISTRY`). Falls
    # back to the process-wide ``_SESSION_ID`` UUID when git config is
    # unreadable. Field default factory still returns ``_SESSION_ID`` for
    # tests that construct ``BicameralContext`` directly without going through
    # ``from_env`` — that path keeps the v0.7.0 single-UUID-per-process
    # semantic. Option (β) per-MCP-session granularity is the v2 upgrade
    # path, gated on team-server protocol activation; documented in plan-231.
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
    # #216 LLM-02: max serialized-JSON byte size for an inbound ingest
    # payload. 1 MiB default. Clamped to [1 KiB, 64 MiB]. Enforced by
    # ``handlers.ingest._check_payload_size`` before payload normalization;
    # over-cap payloads raise ``_IngestRefused`` and are translated to a
    # structured TextContent error at the MCP boundary.
    ingest_max_bytes: int = _DEFAULT_INGEST_MAX_BYTES
    # #216 LLM-08: per-session token-bucket rate limit. ``burst`` is the
    # initial / max-cap token count; ``refill_per_sec`` is the lazy refill
    # rate. Enforced by ``handlers.ingest._check_rate_limit`` after the
    # size-check passes. ``BICAMERAL_INGEST_RATE_LIMIT_DISABLE=1`` env
    # bypasses the gate entirely (local debugging knob).
    ingest_rate_limit_burst: int = _DEFAULT_INGEST_RATE_LIMIT_BURST
    ingest_rate_limit_refill_per_sec: float = _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
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
        ingest_max_bytes = _read_ingest_max_bytes(repo_path)
        ingest_rate_limit_burst = _read_ingest_rate_limit_burst(repo_path)
        ingest_rate_limit_refill_per_sec = _read_ingest_rate_limit_refill_per_sec(repo_path)
        # #231: per-developer agent identity (salted email-hash); falls back
        # to _SESSION_ID UUID on git/salt failure.
        session_id = _resolve_agent_identity(repo_path)

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
            session_id=session_id,
            signer_email_fallback=signer_email_fallback,
            render_source_attribution=render_source_attribution,
            preflight_bypass_tracking=preflight_bypass_tracking,
            ingest_max_bytes=ingest_max_bytes,
            ingest_rate_limit_burst=ingest_rate_limit_burst,
            ingest_rate_limit_refill_per_sec=ingest_rate_limit_refill_per_sec,
        )
