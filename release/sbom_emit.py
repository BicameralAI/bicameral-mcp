"""CycloneDX 1.5 SBOM emitter for #218 Phase 1.

Wraps the ``cyclonedx-bom`` CLI. Subprocess invocation is list-form argv
with ``shell=False`` per OWASP A03 commitment in plan-218. The wrapper
validates the emitted document is CycloneDX 1.5 before returning; on
failure (subprocess error OR wrong spec version) the output is removed
so callers cannot mistake a stale file for a fresh build.

``cyclonedx-py environment`` introspects an installed Python environment,
not a wheel file. To SBOM a single wheel's dependency closure we install
it into an isolated tempdir venv and point ``cyclonedx-py environment``
at that venv's interpreter — the build environment (hatchling, build,
cyclonedx-bom itself) is not contaminated into the output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

_EXPECTED_SPEC_VERSION = "1.5"
_EXPECTED_BOM_FORMAT = "CycloneDX"


class SBOMValidationError(RuntimeError):
    """Raised when ``cyclonedx-bom`` succeeds but emits a non-1.5 doc."""


def _venv_python(venv_path: Path) -> Path:
    # POSIX layout; Windows would be Scripts/python.exe but the publish
    # workflow runs ubuntu-latest exclusively.
    return venv_path / "bin" / "python"


def _venv_cyclonedx(venv_path: Path) -> Path:
    return venv_path / "bin" / "cyclonedx-py"


def emit_sbom(wheel_path: Path, output_path: Path) -> Path:
    """Install ``wheel_path`` into a temp venv and emit a CycloneDX 1.5 SBOM.

    Subprocess discipline: list-form argv, ``shell=False`` (default).
    Raises ``subprocess.CalledProcessError`` on cyclonedx-bom failure;
    raises ``SBOMValidationError`` when the emitted doc is not CycloneDX 1.5.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bicameral-sbom-") as tmp:
        venv_path = Path(tmp) / "venv"
        venv.create(venv_path, with_pip=True, clear=True, symlinks=True)
        py = _venv_python(venv_path)
        # Install wheel + cyclonedx-bom (which installs cyclonedx-py CLI) into
        # the isolated venv. Quiet pip; failures still raise via check=True.
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", str(wheel_path), "cyclonedx-bom"],
            check=True,
        )
        cdx = _venv_cyclonedx(venv_path)
        # Pin schema-version to 1.5 — cyclonedx-py 7.x defaults to 1.6, but the
        # contract advertised in plan-218 + this module is "CycloneDX 1.5".
        # Bumping to 1.6 is a deliberate spec migration, not a side effect of a
        # cyclonedx-py upgrade.
        cmd = [
            str(cdx),
            "environment",
            "--schema-version",
            _EXPECTED_SPEC_VERSION,
            "--output-file",
            str(output_path),
            str(py),
        ]
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
