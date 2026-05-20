"""GitHub polling source adapter (#337 Phase 3b).

Pull-based: enumerates merged PRs in configured repos since the last
watermark via the REST `pulls?state=closed&sort=updated&since=...`
endpoint, fetches each through Phase 3's active-ingest adapter for
PR + reviews + comments normalization, returns ingest-ready payloads.

Config schema::

    sources:
      - type: github
        repos: ["owner/repo", "owner/other-repo"]
        source_type_label: pr-review  # optional

Auth: ``secrets_store source_id="github", key="api_key"`` (PAT with
``repo`` scope OR GitHub App installation token).

Watermark: per-repo. ``<watermark_dir>/github.json`` stores a dict
keyed by ``owner/repo`` → ``last_updated_at`` so a slow repo doesn't
re-pull every PR after a fast repo advances. Corrupt / missing → epoch.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WATERMARK_FILENAME = "github.json"


class GitHubPollingAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "github"

    def __init__(self) -> None:
        self._pending_watermarks: dict[str, str] = {}
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / _WATERMARK_FILENAME

        repos_raw = config.get("repos") or []
        repos = [str(r) for r in repos_raw if "/" in str(r)]
        if not repos:
            print(
                "[github] at least one 'owner/repo' entry is required in source.repos; skipping.",
                file=sys.stderr,
            )
            self._pending_watermarks = {}
            return []

        all_watermarks = _read_watermarks(self._watermark_path)

        try:
            from secrets_store import get_secret

            api_key = get_secret(source_id="github", key="api_key")
            if not api_key:
                print(
                    "[github] api_key not configured (secrets_store source_id=github, "
                    "key=api_key); skipping.",
                    file=sys.stderr,
                )
                self._pending_watermarks = {}
                return []
        except Exception as exc:  # noqa: BLE001
            print(f"[github] secret lookup failed: {exc}", file=sys.stderr)
            self._pending_watermarks = {}
            return []

        try:
            from sources.github.adapter import GitHubAdapter
        except ImportError as exc:
            print(f"[github] adapter import failed: {exc}", file=sys.stderr)
            self._pending_watermarks = {}
            return []

        active = GitHubAdapter()
        payloads: list[dict] = []
        new_watermarks: dict[str, str] = dict(all_watermarks)

        for owner_repo in repos:
            try:
                owner, repo = owner_repo.split("/", 1)
            except ValueError:
                print(
                    f"[github] malformed repo entry {owner_repo!r}; expected 'owner/repo'.",
                    file=sys.stderr,
                )
                continue
            last_updated = all_watermarks.get(owner_repo)
            try:
                from sources.github.poller import list_merged_pulls_since

                pulls = list_merged_pulls_since(
                    api_key=api_key,
                    owner=owner,
                    repo=repo,
                    updated_after=last_updated,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[github] {owner_repo} pull listing failed: {exc}",
                    file=sys.stderr,
                )
                continue

            highest_updated = last_updated or ""
            for pr in pulls:
                url = pr.get("html_url") or ""
                updated_at = pr.get("updated_at") or ""
                if not url:
                    continue
                try:
                    payload = active.fetch_active(url)
                except Exception as exc:  # noqa: BLE001
                    pr_number = pr.get("number", "?")
                    print(
                        f"[github] {owner_repo}#{pr_number} fetch failed (skipped): {exc}",
                        file=sys.stderr,
                    )
                    continue
                label = config.get("source_type_label")
                if label:
                    payload = {**payload, "source": str(label)}
                payloads.append(payload)
                if updated_at > highest_updated:
                    highest_updated = updated_at

            if highest_updated:
                new_watermarks[owner_repo] = highest_updated

        # Stage the per-repo watermark advance.
        self._pending_watermarks = new_watermarks
        return payloads

    def confirm_watermark(self) -> None:
        if self._watermark_path is None or not self._pending_watermarks:
            return
        try:
            self._watermark_path.write_text(
                json.dumps(self._pending_watermarks),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[github] watermark persistence failed (will re-pull next run): %s",
                exc,
            )
        self._pending_watermarks = {}


def _read_watermarks(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[github] watermark file corrupt at %s, starting from epoch: %s",
            path,
            exc,
        )
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
