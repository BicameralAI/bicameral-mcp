"""Slack source adapter (#337 Phase 4a — active ingest).

Public API: ``SlackAdapter`` and ``parse_slack_url``.

Auth: bot token persisted via ``secrets_store`` under
``source_id="slack"``, key ``"api_key"``. Operator creates a Slack app
at api.slack.com, grants ``channels:history`` + ``groups:history``
scopes (and ``mpim:history`` / ``im:history`` only if intentionally
ingesting DMs — Bicameral's policy is channel-only by default per
#337's "no DMs" rule).
"""

from sources.slack.adapter import SlackAdapter, parse_slack_url

__all__ = ["SlackAdapter", "parse_slack_url"]
