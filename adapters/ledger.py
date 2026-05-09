"""Ledger adapter — SurrealDB decision ledger via embedded Python SDK.

Uses SurrealDBLedgerAdapter backed by embedded SurrealDB (Python SDK v1.x).
- Default URL: surrealkv://~/.bicameral/ledger.db (persistent)
- Override via SURREAL_URL env var (e.g. memory:// for tests, ws://host:port for server)

In team mode (.bicameral/config.yaml: mode: team), wraps the adapter with
TeamWriteAdapter for dual-write (event file + DB) and event materialization.

The adapter is a singleton per process — one connection, reused across tool calls.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Singleton for the real adapter (one connection per process)
_real_ledger_instance = None


def _read_team_config(repo_path: str) -> dict:
    """Read .bicameral/config.yaml as a parsed dict.

    Returns ``{"mode": "solo"}`` when the file is absent or unparseable.
    Checks BICAMERAL_DATA_PATH first so history stored in a private parent
    repo is discovered even when REPO_PATH points to a public submodule.
    """
    data_path = os.getenv("BICAMERAL_DATA_PATH", repo_path)
    config_path = Path(data_path) / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return {"mode": "solo"}
    try:
        import yaml

        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {"mode": "solo"}
    except Exception:
        # yaml not installed or bad file — fall back to mode-only parse
        try:
            for line in config_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("mode:"):
                    return {"mode": line.split(":", 1)[1].strip().strip("\"'")}
        except OSError:
            pass
    return {"mode": "solo"}


def get_ledger():
    """Return the ledger adapter (singleton).

    Returns SurrealDBLedgerAdapter in solo mode, or TeamWriteAdapter in team mode.
    """
    global _real_ledger_instance

    if _real_ledger_instance is None:
        from ledger.adapter import SurrealDBLedgerAdapter

        inner = SurrealDBLedgerAdapter(
            url=os.getenv("SURREAL_URL", None),
        )

        repo_path = os.getenv("REPO_PATH", ".")
        cfg = _read_team_config(repo_path)
        mode = cfg.get("mode", "solo")

        if mode == "team":
            from events.backends import get_backend
            from events.materializer import EventMaterializer
            from events.team_adapter import TeamWriteAdapter
            from events.writer import EventFileWriter, _get_git_email

            # BICAMERAL_DATA_PATH redirects all history (events + local state)
            # to a separate directory — typically a private parent repo when
            # REPO_PATH points to a public submodule.
            data_path = os.getenv("BICAMERAL_DATA_PATH", repo_path)
            bicameral_dir = Path(data_path) / ".bicameral"
            events_dir = bicameral_dir / "events"
            local_dir = bicameral_dir / "local"

            author = _get_git_email(repo_path)
            writer = EventFileWriter(events_dir, author)
            materializer = EventMaterializer(events_dir, local_dir)

            cfg.setdefault("team", {})["author"] = author
            try:
                backend = get_backend(cfg)
            except Exception as exc:
                logger.warning(
                    "[ledger] team backend init failed (%s) — continuing local-only", exc
                )
                backend = None

            _real_ledger_instance = TeamWriteAdapter(inner, writer, materializer, backend=backend)
            backend_kind = (cfg.get("team") or {}).get("backend") or "local-only"
            logger.info(
                "[ledger] team mode — events at %s (author: %s, backend: %s)",
                events_dir,
                author,
                backend_kind,
            )
        else:
            _real_ledger_instance = inner

    return _real_ledger_instance


def reset_ledger_singleton() -> None:
    """Reset the singleton — used in tests to get a fresh adapter instance."""
    global _real_ledger_instance
    _real_ledger_instance = None


def get_drift_analyzer():
    """Return the drift analyzer (Layer 1 hash-only by default).

    Swap this factory return to use SemanticDriftAnalyzer (L2+L3)
    or CodeGenomeDriftAnalyzer when ready.
    """
    from ledger.drift import HashDriftAnalyzer

    return HashDriftAnalyzer()
