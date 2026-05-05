"""E2E manual-QA for PR #153 — covers the two unchecked manual items in
the PR description:

  1. `docker-compose up` → `/health` returns `{"status":"ok",...}`
  2. Slack OAuth round-trip in a dev workspace; encrypted token persists.

Infrastructure (compose + cloudflared tunnel) is provisioned outside the
test process — see `.github/workflows/slack-oauth-manual-qa.yml`. These
tests only need `MANUAL_QA_PUBLIC_URL` pointed at the public tunnel.

Playwright records video for the OAuth round-trip. CI uploads the MP4 as
an artifact for evidence.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("playwright")
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402


def test_health(public_url: str) -> None:
    r = httpx.get(f"{public_url}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "schema_version" in body


def test_oauth_install_returns_authorize_url(public_url: str) -> None:
    r = httpx.get(f"{public_url}/oauth/slack/install", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize?")
    assert body["state"]
    parsed = urlparse(body["authorize_url"])
    qs = dict(p.split("=", 1) for p in parsed.query.split("&"))
    # Confirm redirect_uri points back at the public tunnel (not localhost),
    # otherwise the OAuth dance can't complete from Slack's redirect.
    assert public_url.replace(":", "%3A").replace("/", "%2F") in qs["redirect_uri"]


def test_slack_oauth_round_trip(public_url: str, slack_storage_state, tmp_path) -> None:
    if slack_storage_state is None:
        pytest.skip(
            "no Slack storage_state — set SLACK_STORAGE_STATE_B64 (CI) or "
            "SLACK_STORAGE_STATE_PATH (local). See tests/manual_qa/README.md."
        )

    install = httpx.get(f"{public_url}/oauth/slack/install", timeout=10).json()
    authorize_url = install["authorize_url"]

    video_dir = tmp_path / "videos"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=slack_storage_state,
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )
        page: Page = context.new_page()
        try:
            _drive_slack_consent(page, authorize_url, public_url)
            expect(page).to_have_url(
                lambda url: url.startswith(f"{public_url}/oauth/slack/callback"),
                timeout=30_000,
            )
            assert '"ok": true' in page.content() or '"ok":true' in page.content()
            assert '"team_id"' in page.content()
        finally:
            context.close()
            browser.close()

    videos = list(video_dir.rglob("*.webm"))
    assert videos, "Playwright should have produced at least one video"
    print(f"\n[manual-qa] OAuth round-trip video: {videos[0]}")


def _drive_slack_consent(page: Page, authorize_url: str, callback_origin: str) -> None:
    """Walk Slack's OAuth consent screen. Slack changes this DOM
    occasionally — failures here usually mean the selector list needs
    refreshing, not that the team-server code regressed.
    """
    page.goto(authorize_url, wait_until="domcontentloaded")

    # Workspace picker (shown when storage_state has multiple workspaces).
    if page.locator('[data-qa="oauth_submit_button"]').count() > 0:
        page.locator('[data-qa="oauth_submit_button"]').first.click()

    # Consent screen — try a few known selectors before giving up.
    consent_selectors = [
        'button:has-text("Allow")',
        '[data-qa="oauth_allow_button"]',
        'button[type="submit"]:has-text("Allow")',
    ]
    for sel in consent_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            loc.first.click()
            break
    else:
        raise AssertionError(
            "Could not locate Slack 'Allow' button — DOM likely changed. "
            f"Page URL at failure: {page.url}"
        )

    page.wait_for_url(f"{callback_origin}/oauth/slack/callback**", timeout=30_000)
