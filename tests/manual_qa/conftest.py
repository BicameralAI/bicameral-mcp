"""Manual-QA fixtures: public URL into a running team-server.

The harness expects the team-server stack and a public tunnel to be
running already (set up by `.github/workflows/slack-oauth-manual-qa.yml`
in CI, or by `tests/manual_qa/README.md` locally). The tests just need
the public base URL.

Slack auth state is loaded from `SLACK_STORAGE_STATE_B64` (preferred in
CI) or `SLACK_STORAGE_STATE_PATH` (local). See README for capture steps.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def public_url() -> str:
    url = os.environ.get("MANUAL_QA_PUBLIC_URL", "").rstrip("/")
    if not url:
        pytest.skip("MANUAL_QA_PUBLIC_URL not set; run via workflow or README")
    return url


@pytest.fixture(scope="session")
def slack_storage_state(tmp_path_factory) -> str | None:
    b64 = os.environ.get("SLACK_STORAGE_STATE_B64", "").strip()
    if b64:
        path = tmp_path_factory.mktemp("slack-state") / "state.json"
        path.write_bytes(base64.b64decode(b64))
        return str(path)
    path_env = os.environ.get("SLACK_STORAGE_STATE_PATH", "").strip()
    if path_env and Path(path_env).is_file():
        json.loads(Path(path_env).read_text())  # validates it's parseable
        return path_env
    return None
