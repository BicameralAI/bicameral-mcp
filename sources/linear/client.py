"""Thin Linear GraphQL client (#420 Phase 1a).

Uses stdlib ``urllib.request`` rather than adding ``httpx`` or
``requests`` as deps — matches the precedent set by ``handlers/update.py``.
Single endpoint (``https://api.linear.app/graphql``) so the surface
stays tiny; if the adapter family grows to need streaming, retries-with-
backoff, or multipart upload, swap to ``httpx`` then.

Threat-model notes:
- API key never appears in URL strings (Authorization header only).
- Response size capped at 4 MiB (Linear issues with megabytes of
  comments are a misuse signal; refuse rather than blow out memory).
- Network timeout fixed at 15s — long enough for slow Linear regions,
  short enough that an operator-driven active fetch doesn't appear
  hung.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_API_URL = "https://api.linear.app/graphql"
_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 4 MiB


class LinearAPIError(RuntimeError):
    """Raised for any non-recoverable Linear API failure.

    Subclassed off RuntimeError so the SourceAdapter protocol contract
    (raises RuntimeError on network/auth failure) is satisfied. Carries
    ``status_code`` when the failure is HTTP-shaped; ``None`` for
    network-level failures (DNS, timeout, connection reset).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def query(*, api_key: str, document: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against Linear's endpoint.

    Returns the ``data`` field of the response on success. Raises
    ``LinearAPIError`` on any failure — including HTTP non-2xx,
    GraphQL ``errors`` array present, or response exceeding the
    size cap.

    The function is intentionally synchronous (urllib). Callers running
    in an async context should wrap via ``asyncio.to_thread``.
    """
    body = json.dumps({"query": document, "variables": variables or {}}).encode("utf-8")
    headers = {
        "Authorization": api_key,  # Linear accepts the personal API key directly.
        "Content-Type": "application/json",
        "User-Agent": "bicameral-mcp/source-linear",
    }
    req = urllib.request.Request(_API_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            # Read up to cap + 1 byte; if we get the +1 the response is over-cap.
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise LinearAPIError(
                    f"Linear response exceeded {_MAX_RESPONSE_BYTES} bytes",
                    status_code=resp.status,
                )
    except urllib.error.HTTPError as exc:
        raise LinearAPIError(
            f"Linear API HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LinearAPIError(f"Linear API network error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LinearAPIError(f"Linear API returned non-JSON: {exc}") from exc

    if "errors" in parsed and parsed["errors"]:
        # GraphQL-level errors arrive as HTTP 200 with an ``errors`` array.
        first = parsed["errors"][0]
        msg = first.get("message", "unknown GraphQL error")
        raise LinearAPIError(f"Linear GraphQL error: {msg}")

    return parsed.get("data") or {}


_LIST_TEAMS_QUERY = """
query ListTeams {
  teams(first: 100) {
    nodes { id key name }
  }
}
"""


def list_teams(*, api_key: str) -> list[dict]:
    """Enumerate teams the API key has access to.

    Returns dicts shaped ``{"id", "key", "name"}``. The ``key`` is the
    short team prefix (e.g. ``"BIC"``) used in ``team_keys`` config.
    Capped at 100 teams per query (Linear's default page size) — enough
    for any sensible workspace.
    """
    data = query(api_key=api_key, document=_LIST_TEAMS_QUERY)
    teams = (data.get("teams") or {}).get("nodes") or []
    return [
        {"id": t.get("id") or "", "key": t.get("key") or "", "name": t.get("name") or ""}
        for t in teams
    ]
