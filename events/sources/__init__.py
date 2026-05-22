"""Source adapters for pull-based meeting ingestion (#279 Phase 1).

Each adapter implements ``pull(watermark_dir, config) -> list[dict]`` and
returns ingest-ready payload dicts for items new since the last watermark.
The caller (`cli/sync_and_brief_cli.py`) is responsible for sending the
payloads through ``handle_ingest`` and then calling
``adapter.confirm_watermark(...)`` so the watermark only advances after
a successful ingest (two-phase commit).

Watermark files live in ``~/.bicameral/source-watermarks/<source-name>.json``
— outside the repo, in the user home, to keep them out of git.

API keys are NEVER stored in the config file. Adapters read keys from
``os.environ`` via a config entry like ``{type: granola, api_key_env: GRANOLA_API_KEY}``.

Registry: ``ADAPTERS`` maps the config ``type`` string to the adapter class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .github import GitHubPollingAdapter
from .google_drive import GoogleDriveFolderAdapter
from .granola import GranolaAdapter, MissingApiKeyError
from .local_directory import LocalDirectoryAdapter


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol every source adapter implements.

    ``name`` is the lookup key into ``ADAPTERS`` and the basename of the
    watermark file written under ``watermark_dir``.
    """

    name: str

    def pull(self, *, watermark_dir, config: dict) -> list[dict]:  # pragma: no cover - protocol
        ...

    def confirm_watermark(self) -> None:  # pragma: no cover - protocol
        ...


ADAPTERS: dict[str, type] = {
    "granola": GranolaAdapter,
    "local_directory": LocalDirectoryAdapter,
    "google_drive": GoogleDriveFolderAdapter,
    "github": GitHubPollingAdapter,
}


__all__ = [
    "ADAPTERS",
    "GitHubPollingAdapter",
    "GoogleDriveFolderAdapter",
    "GranolaAdapter",
    "LocalDirectoryAdapter",
    "MissingApiKeyError",
    "SourceAdapter",
]
