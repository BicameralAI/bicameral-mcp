"""Clone-on-demand helper for OSS corpus parity tests (#399 Stage B).

Used by ``tests/test_tags_extractor_parity.py`` to materialize a small,
reproducible slice of real OSS Go/Rust code on first use, against which
the walker ⊆ substrate parity contract can be measured.

Why clone-on-demand (vs. vendoring or env-controlled pre-clone):

- The #399 issue body explicitly recommends it ("avoid vendoring license
  headers; vendor only when the parity gate needs a stable hash for
  caching" — Stage B doesn't need a stable hash).
- Matches the #367 Phoenix smoke-test pattern (one-off
  ``git clone --depth 1 phoenixframework/phoenix``) — Stage B promotes
  the same shape into the CI gate.
- No new env-var contract, no workflow yaml changes. Stage B PR diff
  stays focused on test code.

Failure model — **fail loud, never silently skip**:

- A gate that vanishes when GitHub blips is worse than one that fails
  honestly. Per Jin's standing principle (memory: feedback_engineering_style),
  fail-loud beats silent-fallback. If git or the network is unavailable,
  ``pytest.fail`` with a clear message; CI re-runs are one click.
- Pinned tags only (no branches, no HEAD) so the corpus is reproducible
  run-to-run.

Bandwidth budget — ``--depth=1 --filter=blob:none --sparse-checkout``:

- ``--depth=1``: shallow clone, history not fetched.
- ``--filter=blob:none``: defer blob fetches; only the blobs reachable
  from the sparse-checkout set materialize.
- ``--sparse-checkout``: working tree only materializes the requested
  subtree.

Net effect on a corpus like ``kubernetes/kubernetes@v1.30.0`` →
``staging/src/k8s.io/api/core/v1/``: transfer drops from ~600MB (full
clone) to a few MB (sparse blobs only). On Hugo's ``hugolib/``: ~2MB.
A typical session pays one-time ~10-30s per source.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class OssSource:
    """A pinned source slice within an OSS repo.

    Attributes
    ----------
    repo : str
        ``owner/name`` on github.com (e.g. ``kubernetes/kubernetes``).
    ref : str
        A git tag for reproducibility. Branches MUST NOT be used —
        the parity gate's corpus must be deterministic run-to-run.
    sparse_path : str
        Directory within the repo to materialize via sparse-checkout.
        Relative to the repo root. Files outside this path are not
        fetched (saves bandwidth on large monorepos like k8s).
    """

    repo: str
    ref: str
    sparse_path: str

    def cache_dirname(self) -> str:
        """Filesystem-safe identifier for the sparse clone directory."""
        return f"{self.repo.replace('/', '_')}_{self.ref}"


def sparse_clone(source: OssSource, dest: Path, *, timeout: int = 180) -> None:
    """Clone ``source.repo`` at ``source.ref`` into ``dest``, materializing
    only files under ``source.sparse_path``.

    Performs ``git clone --depth=1 --filter=blob:none --no-checkout``
    followed by ``git sparse-checkout init --cone`` +
    ``set <sparse_path>`` + ``git checkout``. The two-phase clone is
    required because ``--filter=blob:none`` is only honored when no
    initial checkout happens (else git fetches all blobs to populate the
    working tree).

    Fails loudly via ``pytest.fail`` on:
        - Missing ``git`` binary
        - Network timeout (default 180s)
        - Non-zero git exit (e.g. tag does not exist upstream)

    The CalledProcessError stderr is preserved verbatim in the failure
    message so debugging starts from the actual git output.
    """
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{source.repo}.git"

    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--filter=blob:none",
                "--no-checkout",
                "--branch",
                source.ref,
                url,
                str(dest),
            ],
            check=True,
            timeout=timeout,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "sparse-checkout", "init", "--cone"],
            check=True,
            timeout=30,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "sparse-checkout", "set", source.sparse_path],
            check=True,
            timeout=30,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout"],
            check=True,
            timeout=timeout,
            capture_output=True,
        )
    except FileNotFoundError as e:
        pytest.fail(
            f"git binary not found ({e}); the corpus parity gate requires "
            f"a working git installation on PATH."
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"sparse clone of {source.repo}@{source.ref} timed out after "
            f"{timeout}s. The parity gate requires network access to "
            f"github.com; check connectivity and re-run."
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "(no stderr)"
        pytest.fail(
            f"sparse clone of {source.repo}@{source.ref} failed "
            f"(git exit {e.returncode}):\n{stderr}\n\n"
            f"The parity gate requires network access to github.com and "
            f"the pinned tag must exist upstream."
        )


def discover_files(
    clone_dir: Path,
    source: OssSource,
    *,
    extension: str,
    exclude_globs: tuple[str, ...] = (),
    max_files: int | None = None,
) -> list[Path]:
    """Walk ``clone_dir / source.sparse_path`` for files matching
    ``*.{extension}``, skip paths matching any ``exclude_globs``, and
    return up to ``max_files`` sorted absolute paths.

    Sort-then-cap is deliberate: deterministic file selection so the
    parity gate scope doesn't drift between runs. Adding/removing files
    in the upstream repo *can* still shift which files fall under the
    cap — that's expected and surfaces as a corpus update in the next
    PR that bumps the pinned tag.

    Fails loudly via ``pytest.fail`` if zero files match — that means
    either the sparse_path is wrong or upstream emptied the directory.
    """
    root = clone_dir / source.sparse_path
    if not root.exists():
        pytest.fail(
            f"sparse-checkout did not materialize {root}. Either "
            f"{source.sparse_path!r} is the wrong path in {source.repo}@"
            f"{source.ref}, or upstream renamed/removed the directory."
        )

    candidates = sorted(root.rglob(f"*.{extension}"))
    out: list[Path] = []
    for p in candidates:
        rel = p.relative_to(root)
        if any(rel.match(g) for g in exclude_globs):
            continue
        out.append(p)
        if max_files is not None and len(out) >= max_files:
            break

    if not out:
        pytest.fail(
            f"No *.{extension} files discovered under {root} "
            f"(after exclude_globs={exclude_globs}). The corpus has no "
            f"signal — either the sparse_path is wrong or every file was "
            f"excluded."
        )
    return out
