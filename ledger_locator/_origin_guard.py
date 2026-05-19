"""Origin guard — refuses to use a project dir keyed off a different common-dir.

The 16-char project id is sha256-derived; collisions in practice are
vanishingly unlikely but not zero. The guard records the common-dir on
first use and refuses to proceed when a later call presents a different
one. Operators can bypass via `BICAMERAL_LOCATOR_ALLOW_COLLISION=1`,
which downgrades the refusal to a logged WARN.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ALLOW_COLLISION_ENV = "BICAMERAL_LOCATOR_ALLOW_COLLISION"


class ProjectIdCollisionError(RuntimeError):
    """Two distinct projects hashed to the same 16-char id."""


def assert_origin(project_dir: Path, common_dir: Path) -> None:
    """Record or verify `<project_dir>/origin.txt`.

    First call writes the common-dir path; subsequent calls compare and
    raise on mismatch (or WARN + proceed when the override env is set).
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    origin_file = project_dir / "origin.txt"
    expected = str(common_dir)

    if not origin_file.exists():
        origin_file.write_text(expected + "\n")
        return

    recorded = origin_file.read_text().strip()
    if recorded == expected:
        return

    msg = (
        f"project-id collision detected at {project_dir}: "
        f"recorded origin {recorded!r}, current origin {expected!r}. "
        f"Set {_ALLOW_COLLISION_ENV}=1 to proceed anyway."
    )
    if os.environ.get(_ALLOW_COLLISION_ENV) == "1":
        logger.warning(msg)
        return
    raise ProjectIdCollisionError(msg)
