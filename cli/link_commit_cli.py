"""link_commit CLI subcommand entry point (#124).

Wraps the shared ``cli._link_commit_runner.invoke_link_commit`` for
human-driven invocation. JSON-to-stdout by default; ``--quiet`` for
hook scripts that pipe to /dev/null.

Always exits 0 — the post-commit hook depends on this so commits are
never blocked. Hook-side loudness (stderr) is handled in the installed
shell script, not here.
"""

from __future__ import annotations

import json

from cli._link_commit_runner import invoke_link_commit


def main(commit_hash: str = "HEAD", *, quiet: bool = False) -> int:
    """Run link_commit against ``commit_hash`` (default HEAD).

    Returns 0 on success, on no-ledger graceful skip, and on
    handler-exception graceful skip — the runner already collapses
    those cases to ``None``. Print JSON to stdout unless ``quiet``.
    """
    response = invoke_link_commit(commit_hash)
    if response is None:
        return 0
    if not quiet:
        print(json.dumps(response.model_dump(), default=str, indent=2))
    return 0
