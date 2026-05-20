"""Tests for #337 Phase 5c — Google Drive folder polling adapter.

The Drive `files.list` call is mocked at the boundary (googleapiclient's
service-builder return value). Watermark persistence runs unmocked over
``tmp_path``. The active-ingest fetch path (``GoogleDriveAdapter.fetch_active``)
is also mocked at the seam since it needs a real OAuth token + network.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


# ── folder.list_docs_in_folder ──────────────────────────────────────────────


def _fake_list_resp(*docs, next_page=None):
    return {
        "files": list(docs),
        **({"nextPageToken": next_page} if next_page else {}),
    }


def test_list_docs_returns_files_sorted_by_modified_time():
    from sources.google_drive.folder import list_docs_in_folder

    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = _fake_list_resp(
        {"id": "a", "name": "A", "modifiedTime": "2026-05-01T00:00:00Z"},
        {"id": "b", "name": "B", "modifiedTime": "2026-05-02T00:00:00Z"},
    )
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = list_docs_in_folder(MagicMock(), "folder1")
    assert [d["id"] for d in result] == ["a", "b"]


def test_list_docs_paginates_until_done():
    from sources.google_drive.folder import list_docs_in_folder

    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.side_effect = [
        _fake_list_resp(
            {"id": "a", "name": "A", "modifiedTime": "2026-05-01T00:00:00Z"},
            next_page="cursor1",
        ),
        _fake_list_resp(
            {"id": "b", "name": "B", "modifiedTime": "2026-05-02T00:00:00Z"},
        ),
    ]
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = list_docs_in_folder(MagicMock(), "folder1")
    assert len(result) == 2


def test_list_docs_applies_modified_after_filter():
    """The q expression must include `modifiedTime > '<watermark>'` when set."""
    from sources.google_drive.folder import list_docs_in_folder

    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = _fake_list_resp()
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        list_docs_in_folder(MagicMock(), "folder1", modified_after="2026-05-01T00:00:00Z")

    # Inspect the kwargs passed to .list().
    list_call = fake_service.files.return_value.list.call_args
    assert "modifiedTime > '2026-05-01T00:00:00Z'" in list_call.kwargs["q"]


def test_list_docs_filters_to_google_docs_mime():
    from sources.google_drive.folder import list_docs_in_folder

    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = _fake_list_resp()
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        list_docs_in_folder(MagicMock(), "folder1")

    q = fake_service.files.return_value.list.call_args.kwargs["q"]
    assert "mimeType = 'application/vnd.google-apps.document'" in q
    assert "trashed = false" in q


def test_list_docs_raises_on_api_error():
    from googleapiclient.errors import HttpError

    from sources.google_drive.folder import list_docs_in_folder

    err = HttpError(MagicMock(status=403), b"forbidden")
    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.side_effect = err
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        with pytest.raises(RuntimeError, match="folder list failed"):
            list_docs_in_folder(MagicMock(), "folder1")


# ── GoogleDriveFolderAdapter.pull ───────────────────────────────────────────


def test_pull_returns_payloads_for_new_docs(tmp_path):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    fake_docs = [
        {"id": "doc1", "name": "Doc 1", "modifiedTime": "2026-05-01T00:00:00Z"},
        {"id": "doc2", "name": "Doc 2", "modifiedTime": "2026-05-02T00:00:00Z"},
    ]
    fake_payload = {"source": "google_drive", "decisions": [], "title": "x"}

    with (
        patch(
            "sources.google_drive.auth.load_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sources.google_drive.folder.list_docs_in_folder",
            return_value=fake_docs,
        ),
        patch(
            "sources.google_drive.adapter.GoogleDriveAdapter.fetch_active",
            return_value=fake_payload,
        ),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})

    assert len(result) == 2
    # Pending watermark = highest modifiedTime seen.
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_returns_empty_when_folder_id_missing(tmp_path, capsys):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    adapter = GoogleDriveFolderAdapter()
    result = adapter.pull(watermark_dir=tmp_path, config={})
    assert result == []
    err = capsys.readouterr().err
    assert "folder_id is required" in err


def test_pull_returns_empty_when_no_new_docs(tmp_path):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    with (
        patch(
            "sources.google_drive.auth.load_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sources.google_drive.folder.list_docs_in_folder",
            return_value=[],
        ),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})
    assert result == []
    assert adapter._pending_watermark is None


def test_pull_skips_individual_fetch_failures(tmp_path, capsys):
    """One bad doc shouldn't kill the whole pull."""
    from events.sources.google_drive import GoogleDriveFolderAdapter

    fake_docs = [
        {"id": "doc1", "name": "Doc 1", "modifiedTime": "2026-05-01T00:00:00Z"},
        {"id": "doc2", "name": "Doc 2", "modifiedTime": "2026-05-02T00:00:00Z"},
    ]
    call_count = {"n": 0}

    def _flaky_fetch(self, url):
        call_count["n"] += 1
        if "doc1" in url:
            raise RuntimeError("transient API blip")
        return {"source": "google_drive", "decisions": [], "title": "doc2"}

    with (
        patch(
            "sources.google_drive.auth.load_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sources.google_drive.folder.list_docs_in_folder",
            return_value=fake_docs,
        ),
        patch(
            "sources.google_drive.adapter.GoogleDriveAdapter.fetch_active",
            new=_flaky_fetch,
        ),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})

    # doc1 skipped, doc2 succeeded.
    assert len(result) == 1
    err = capsys.readouterr().err
    assert "doc1" in err
    # Watermark advances to doc2's mtime even though doc1 was skipped —
    # documented trade-off: skipping individual items doesn't strand the
    # whole folder behind one bad doc.
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_returns_empty_on_oauth_failure(tmp_path, capsys):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    with patch(
        "sources.google_drive.auth.load_credentials",
        side_effect=RuntimeError("token not configured"),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})

    assert result == []
    err = capsys.readouterr().err
    assert "folder enumeration failed" in err


def test_pull_passes_last_watermark_to_list(tmp_path):
    """Watermark on disk should be passed as modified_after to the API."""
    from events.sources.google_drive import GoogleDriveFolderAdapter

    wm_path = tmp_path / "google_drive.json"
    wm_path.write_text(json.dumps({"last_modified": "2026-05-01T00:00:00Z"}))

    captured = {}

    def _capture(creds, folder_id, *, modified_after=None):
        captured["modified_after"] = modified_after
        return []

    with (
        patch(
            "sources.google_drive.auth.load_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sources.google_drive.folder.list_docs_in_folder",
            side_effect=_capture,
        ),
    ):
        adapter = GoogleDriveFolderAdapter()
        adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})

    assert captured["modified_after"] == "2026-05-01T00:00:00Z"


def test_pull_starts_from_epoch_on_corrupt_watermark(tmp_path, capsys):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    wm_path = tmp_path / "google_drive.json"
    wm_path.write_text("not-valid-json{")

    captured = {}

    def _capture(creds, folder_id, *, modified_after=None):
        captured["modified_after"] = modified_after
        return []

    with (
        patch(
            "sources.google_drive.auth.load_credentials",
            return_value=MagicMock(),
        ),
        patch(
            "sources.google_drive.folder.list_docs_in_folder",
            side_effect=_capture,
        ),
    ):
        adapter = GoogleDriveFolderAdapter()
        adapter.pull(watermark_dir=tmp_path, config={"folder_id": "folder1"})

    assert captured["modified_after"] is None


# ── confirm_watermark ───────────────────────────────────────────────────────


def test_confirm_watermark_persists_to_disk(tmp_path):
    from events.sources.google_drive import GoogleDriveFolderAdapter

    adapter = GoogleDriveFolderAdapter()
    adapter._watermark_path = tmp_path / "google_drive.json"
    adapter._pending_watermark = "2026-05-19T12:00:00Z"
    adapter.confirm_watermark()

    data = json.loads((tmp_path / "google_drive.json").read_text())
    assert data["last_modified"] == "2026-05-19T12:00:00Z"
    # After confirm, pending is cleared so a re-call is a no-op.
    assert adapter._pending_watermark is None


def test_confirm_watermark_is_noop_when_pending_is_none(tmp_path):
    """Confirm without a prior pull (or after an empty pull) should not
    create a watermark file or crash."""
    from events.sources.google_drive import GoogleDriveFolderAdapter

    adapter = GoogleDriveFolderAdapter()
    adapter._watermark_path = tmp_path / "google_drive.json"
    adapter._pending_watermark = None
    adapter.confirm_watermark()
    assert not (tmp_path / "google_drive.json").exists()


# ── Registry integration ────────────────────────────────────────────────────


def test_registered_as_google_drive_in_ADAPTERS():
    from events.sources import ADAPTERS
    from events.sources.google_drive import GoogleDriveFolderAdapter

    assert ADAPTERS["google_drive"] is GoogleDriveFolderAdapter
