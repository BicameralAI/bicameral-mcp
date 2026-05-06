"""CycloneDX 1.5 SBOM emitter for #218 Phase 1.

Wraps the ``cyclonedx-bom`` CLI. Subprocess invocation is list-form argv
with ``shell=False`` per OWASP A03 commitment in plan-218. The wrapper
validates the emitted document is CycloneDX 1.5 before returning; on
failure (subprocess error OR wrong spec version) the output is removed
so callers cannot mistake a stale file for a fresh build.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_EXPECTED_SPEC_VERSION = "1.5"
_EXPECTED_BOM_FORMAT = "CycloneDX"


class SBOMValidationError(RuntimeError):
    """Raised when ``cyclonedx-bom`` succeeds but emits a non-1.5 doc."""


def emit_sbom(wheel_path: Path, output_path: Path) -> Path:
    """Run ``cyclonedx-bom`` against ``wheel_path``, write to ``output_path``.

    Subprocess discipline: list-form argv, ``shell=False`` (default).
    Raises ``subprocess.CalledProcessError`` on cyclonedx-bom failure;
    raises ``SBOMValidationError`` when the emitted doc is not CycloneDX 1.5.
    """
    cmd = ["cyclonedx-py", "environment", "-o", str(output_path), str(wheel_path)]
    # ``cyclonedx-bom`` (PyPI package) installs the ``cyclonedx-py`` CLI.
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        if output_path.exists():
            output_path.unlink()
        raise

    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    if parsed.get("bomFormat") != _EXPECTED_BOM_FORMAT:
        output_path.unlink()
        raise SBOMValidationError(
            f"bomFormat {parsed.get('bomFormat')!r} != {_EXPECTED_BOM_FORMAT!r}"
        )
    if parsed.get("specVersion") != _EXPECTED_SPEC_VERSION:
        output_path.unlink()
        raise SBOMValidationError(
            f"specVersion {parsed.get('specVersion')!r} != {_EXPECTED_SPEC_VERSION!r}"
        )
    return output_path


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit CycloneDX 1.5 SBOM")
    parser.add_argument("wheel", type=Path, help="Path to built wheel")
    parser.add_argument("output", type=Path, help="Output SBOM JSON path")
    args = parser.parse_args(argv)
    try:
        result = emit_sbom(args.wheel, args.output)
    except (subprocess.CalledProcessError, SBOMValidationError) as exc:
        print(f"sbom_emit: failed: {exc}", file=sys.stderr)
        return 1
    print(str(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
