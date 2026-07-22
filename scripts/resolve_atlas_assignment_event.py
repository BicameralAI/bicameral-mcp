#!/usr/bin/env python3
"""Resolve one exact product PR and its Release Unit paths from a status event."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PROPOSAL_PATH = re.compile(r"^release-units/[A-Za-z0-9][A-Za-z0-9._-]*\.json$")


def select_pull_request(pulls: list[dict[str, Any]], status_sha: str) -> dict[str, Any]:
    if not SHA.fullmatch(status_sha):
        raise ValueError("status event SHA must be a full lowercase commit SHA")
    matches = [
        pull
        for pull in pulls
        if pull.get("state") == "open"
        and isinstance(pull.get("head"), dict)
        and pull["head"].get("sha") == status_sha
        and isinstance(pull.get("base"), dict)
        and pull["base"].get("ref") == "dev"
    ]
    if len(matches) != 1:
        raise ValueError(
            "owner-approved status must resolve to exactly one open PR into dev; "
            f"found {len(matches)}"
        )
    pull = matches[0]
    number = pull.get("number")
    base_sha = pull["base"].get("sha")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("resolved pull request has no valid number")
    if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
        raise ValueError("resolved pull request has no full lowercase base SHA")
    return {"pull_request": number, "head_sha": status_sha, "base_sha": base_sha}


def release_unit_paths(files: list[dict[str, Any]]) -> list[str]:
    paths = sorted(
        file["filename"]
        for file in files
        if file.get("status") != "removed"
        and isinstance(file.get("filename"), str)
        and file["filename"].startswith("release-units/")
        and file["filename"].endswith(".json")
    )
    if len(paths) != len(set(paths)):
        raise ValueError("GitHub returned duplicate Release Unit proposal paths")
    if not 1 <= len(paths) <= 20:
        raise ValueError("assignment PR must contain 1-20 Release Unit proposals")
    unsafe = [path for path in paths if not PROPOSAL_PATH.fullmatch(path)]
    if unsafe:
        raise ValueError(f"unsafe Release Unit proposal paths: {unsafe}")
    return paths


class GitHubReader:
    def __init__(self, api_url: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(api_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GitHub API URL must be https://api.github.com")
        self.api_url = api_url.rstrip("/")
        self.token = token

    def get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bicameral-atlas-assignment-gate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 -- HTTPS GitHub host validated above
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError(f"GitHub API request failed with HTTP {error.code}") from error

    def pull_requests_for_commit(self, repository: str, sha: str) -> list[dict[str, Any]]:
        payload = self.get(f"/repos/{repository}/commits/{sha}/pulls?per_page=100")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("GitHub commit pull-request response is invalid")
        return payload

    def pull_request_files(self, repository: str, number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for page in range(1, 31):
            payload = self.get(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise ValueError("GitHub pull-request file response is invalid")
            files.extend(payload)
            if len(payload) < 100:
                return files
        raise ValueError("pull request exceeds the supported 3000-file bound")


def append_github_outputs(path: Path, values: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            rendered = (
                json.dumps(value, separators=(",", ":")) if isinstance(value, list) else str(value)
            )
            if "\n" in rendered or "\r" in rendered:
                raise ValueError(f"unsafe multiline GitHub output: {key}")
            output.write(f"{key}={rendered}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--status-sha", required=True)
    parser.add_argument("--api-url", default="https://api.github.com")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    if not REPOSITORY.fullmatch(args.repository):
        raise SystemExit("repository must be an owner/name pair")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    reader = GitHubReader(args.api_url, token)
    selected = select_pull_request(
        reader.pull_requests_for_commit(args.repository, args.status_sha),
        args.status_sha,
    )
    paths = release_unit_paths(
        reader.pull_request_files(args.repository, int(selected["pull_request"]))
    )
    append_github_outputs(
        args.github_output,
        {**selected, "proposal_paths_json": paths},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
