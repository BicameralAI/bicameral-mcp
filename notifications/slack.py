"""SlackChannelAdapter — webhook-based Slack notification channel (#330).

Posts plain-text JSON to a Slack incoming webhook URL. The URL itself
is read from ``os.environ[config.webhook_url_env]`` at delivery time
(not construction); operators rotate the URL via env-var swap without
restarting bicameral.

Single env-var-only secret model — mirrors ``api_key_env`` pattern in
``events/sources/granola.py:128-137``. Config file holds only the
env-var NAME; the URL never lives in any committed-or-loggable file.

HTTP transport via stdlib ``urllib.request`` — no new dependency,
mirrors the ``GranolaClient`` indirection so tests can substitute a
fake client without spinning real HTTP.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from .contracts import ChannelDeliveryError, NotificationEvent

logger = logging.getLogger(__name__)


_DEFAULT_WEBHOOK_URL_ENV = "SLACK_WEBHOOK_URL"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class SlackClient:
    """Thin HTTP wrapper around a Slack incoming-webhook POST.

    The indirection exists so tests can inject a fake without
    spinning real HTTP. Production constructs the default in
    ``SlackChannelAdapter.deliver()``.
    """

    def __init__(
        self, *, webhook_url: str, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout_seconds

    def post(self, *, text: str) -> None:
        """POST a Slack plain-text message. Raises on HTTP error."""
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(  # nosec — operator-configured webhook URL
            self._webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec — same
            # Drain the body so the connection can close cleanly.
            resp.read()


class SlackChannelAdapter:
    """Slack channel — POSTs ``NotificationEvent`` to a webhook URL."""

    name = "slack"

    def __init__(
        self,
        *,
        config: dict | None = None,
        client: SlackClient | None = None,
    ) -> None:
        cfg = config or {}
        self._webhook_url_env = str(cfg.get("webhook_url_env") or _DEFAULT_WEBHOOK_URL_ENV)
        # Test seam — production constructs the default client lazily
        # inside ``deliver()`` so env-var rotation also affects URL.
        self._client = client

    async def deliver(self, event: NotificationEvent) -> None:
        url = os.environ.get(self._webhook_url_env, "").strip()
        if not url:
            raise ChannelDeliveryError(
                f"Slack adapter: env var {self._webhook_url_env!r} is unset or empty. "
                "Set it before delivery, or change channels[].webhook_url_env in "
                "~/.bicameral/notifications.yml."
            )

        text = _render(event)
        client = self._client or SlackClient(webhook_url=url)
        try:
            client.post(text=text)
        except urllib.error.URLError as exc:
            raise ChannelDeliveryError(f"Slack POST failed (URLError): {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ChannelDeliveryError(f"Slack POST failed: {exc}") from exc


def _render(event: NotificationEvent) -> str:
    """Plain-text Slack message body.

    Format:
        [bicameral][<event_type>] <feature_area>: <summary>

    Structural-fact-only per the #221 design directive — ``summary``
    is already 200-char capped by Phase 1's NotificationEvent
    invariant. Operator-supplied content in ``summary`` (e.g. the
    ``note`` parameter on ratify) flows through verbatim subject to
    that cap; operators are responsible for not putting PII into
    ``note`` (documented in ``docs/policies/notifications-config.md``).
    """
    fa = event.feature_area or "(no feature area)"
    return f"[bicameral][{event.event_type}] {fa}: {event.summary}"
