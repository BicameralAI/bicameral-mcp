"""Regression guards for retiring MCP source/Drive runtime authority (#623)."""

from pathlib import Path

FORBIDDEN_RUNTIME_PATHS = {
    "cli/sync_and_brief_cli.py",
    "cli/drive_renew_cli.py",
    "sources",
    "events/sources",
    "webhooks",
}

FORBIDDEN_RUNTIME_TOKENS = {
    "handle_ingest(",
    "sources.google_drive",
    "sources.github",
    "events.sources",
    "webhooks.google_drive",
    "webhooks.github",
    "drive_renew_cli",
    "drive_watch_cli",
    "sync_and_brief_cli",
}

PRODUCTION_PYTHON_FILES = {
    path for path in Path(".").glob("*.py") if path.name not in {"setup.py"}
} | set(Path("scripts").glob("*.py"))


def test_source_acquisition_runtime_paths_are_absent():
    for forbidden in FORBIDDEN_RUNTIME_PATHS:
        assert not Path(forbidden).exists(), f"MCP source runtime path reintroduced: {forbidden}"


def test_packaged_surface_cannot_include_source_runtime():
    text = Path("pyproject.toml").read_text()

    for forbidden in [
        "cli/",
        "sources/",
        "events/",
        "webhooks/",
        "google_drive",
        "drive_renew",
        "sync_and_brief",
    ]:
        assert forbidden not in text


def test_production_python_does_not_call_source_ingest_runtime():
    findings: list[str] = []
    for path in sorted(PRODUCTION_PYTHON_FILES):
        text = path.read_text()
        for token in FORBIDDEN_RUNTIME_TOKENS:
            if token in text:
                findings.append(f"{path}:{token}")

    assert findings == []


def test_mcp_keeps_only_local_toolrequest_ingest_mapping():
    text = Path("tool_request.py").read_text()

    assert '"bicameral.ingest": "ingest.submit_local"' in text
    assert "ExternalIngestEnvelope" not in text
    assert "external-ingest" not in text
    assert "ingest.submit_managed" not in text
