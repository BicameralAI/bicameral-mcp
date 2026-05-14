"""Granola source adapter — pulls recent meeting transcripts (#279 Phase 1).

Watermark-driven: each ``pull()`` returns only transcripts whose
``ended_at`` is strictly after the last confirmed watermark. The
watermark only advances when the caller invokes
``confirm_watermark(highest_ended_at)`` after a successful ingest —
two-phase commit so a failed ingest does not lose the un-ingested items.

API key is read from ``os.environ[config["api_key_env"]]`` at pull time;
the config file holds only the env-var name.

HTTP transport uses stdlib ``urllib.request`` (no new dependency). The
``GranolaClient`` indirection exists so tests can mock the HTTP layer
without spinning up a real Granola endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class MissingApiKeyError(RuntimeError):
    """Raised when the configured api_key_env is not set in os.environ."""


class GranolaClient:
    """Thin HTTP wrapper around the Granola transcripts endpoint.

    Used through dependency injection so tests can substitute a fake.
    """

    DEFAULT_BASE_URL = "https://api.granola.ai"

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")

    def list_transcripts(self, *, since: str | None = None) -> list[dict]:
        """GET the transcripts listing. Returns the parsed JSON ``items``
        list, or an empty list when the response carries no items.

        ``since`` is forwarded as an ISO8601 query parameter when set.
        """
        params: dict[str, str] = {}
        if since:
            params["since"] = since
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = f"{self._base_url}/v1/transcripts{qs}"
        req = urllib.request.Request(  # nosec — operator-configured URL
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec — same
            body = resp.read().decode("utf-8")
        data = json.loads(body) if body else {}
        if isinstance(data, dict):
            return list(data.get("items") or [])
        return []


class GranolaAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "granola"

    def __init__(self, *, client: GranolaClient | None = None) -> None:
        self._client = client
        # Pending watermark advance — set by pull(), committed by confirm_watermark().
        self._pending_watermark: str | None = None
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        """Pull new transcripts since the last confirmed watermark.

        Returns a list of ingest-ready payloads (shape matches the
        ``mappings[]`` structure that ``handle_ingest`` consumes).
        """
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        watermark_path = watermark_dir / f"{self.name}.json"
        self._watermark_path = watermark_path

        last_synced = _read_watermark(watermark_path)

        client = self._client or _build_default_client(config)

        items = client.list_transcripts(since=last_synced)
        if not items:
            self._pending_watermark = None
            return []

        payloads = [_transform(item) for item in items if item]
        # Compute the maximum ended_at; only items that have it count.
        ended_at_values = [item.get("ended_at") for item in items if item and item.get("ended_at")]
        if ended_at_values:
            self._pending_watermark = max(ended_at_values)
        else:
            self._pending_watermark = datetime.now(UTC).isoformat()
        return payloads

    def confirm_watermark(self) -> None:
        """Persist the pending watermark. No-op if the last pull returned
        no items or if pull() was never called."""
        if self._pending_watermark is None or self._watermark_path is None:
            return
        _write_watermark(self._watermark_path, self._pending_watermark)
        self._pending_watermark = None


# ── helpers ────────────────────────────────────────────────────────────────


def _build_default_client(config: dict) -> GranolaClient:
    api_key_env = config.get("api_key_env") or "GRANOLA_API_KEY"
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise MissingApiKeyError(
            f"Granola adapter: env var {api_key_env!r} is unset or empty. "
            f"Set it before running sync-and-brief, or change "
            f"sources[].api_key_env in .bicameral/config.yaml."
        )
    return GranolaClient(api_key=api_key, base_url=config.get("base_url"))


def _read_watermark(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("last_synced_at")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[granola] watermark unreadable at %s: %s", path, exc)
        return None


def _write_watermark(path: Path, last_synced_at: str) -> None:
    payload = {"last_synced_at": last_synced_at, "written_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def _transform(item: dict) -> dict:
    """Map a Granola transcript dict to a bicameral ingest payload.

    Granola's transcript shape (per public docs at time of writing):
      ``{id, ended_at, transcript_text, title, participants: [{name,...}], ...}``

    Bicameral ingest expects ``{query, repo, mappings: [{span, intent, ...}]}``.
    For session-magic pull-and-brief, we set ``query`` to the transcript
    title (or id), and emit one mapping per transcript with the full text
    as the span.
    """
    transcript_id = str(item.get("id") or "")
    text = str(item.get("transcript_text") or "")
    title = str(item.get("title") or "") or transcript_id
    ended_at = str(item.get("ended_at") or "")
    participants = item.get("participants") or []
    speaker = ""
    if participants and isinstance(participants, list):
        first = participants[0]
        if isinstance(first, dict):
            speaker = str(first.get("name") or "")
        elif isinstance(first, str):
            speaker = first
    return {
        "query": title or transcript_id or "granola transcript",
        "repo": "",
        "commit_hash": "",
        "analyzed_at": ended_at or datetime.now(UTC).isoformat(),
        "mappings": [
            {
                "span": {
                    "span_id": f"granola-{transcript_id}",
                    "source_type": "transcript",
                    "text": text,
                    "speaker": speaker,
                    "source_ref": transcript_id,
                    "meeting_date": ended_at[:10] if ended_at else "",
                },
                "intent": title or transcript_id,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }
