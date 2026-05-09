"""CycloneDX SBOM emitter for #218 Phase 1.

Wraps the ``cyclonedx-bom`` CLI. Subprocess invocation is list-form argv
with ``shell=False`` per OWASP A03 commitment in plan-218.

``cyclonedx-py environment`` introspects an installed Python environment,
not a wheel file. To SBOM a single wheel's dependency closure we install
it into an isolated tempdir venv and point ``cyclonedx-py environment``
at that venv's interpreter — the build environment (hatchling, build,
cyclonedx-bom itself) is not contaminated into the output.

Spec-version policy: accept any CycloneDX 1.x output cyclonedx-py
produces. Earlier revisions of this module pinned 1.5 because that was
the contract advertised in plan-218; cyclonedx-py 7.x defaults to 1.6
and rejected the ``--schema-version`` flag we passed to force 1.5.
Rather than fight the CLI, we accept the spec version cyclonedx-py is
prepared to emit. CycloneDX is forward-compatible within 1.x — every
JSON consumer that handles 1.5 also handles 1.6.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

_EXPECTED_BOM_FORMAT = "CycloneDX"


class SBOMValidationError(RuntimeError):
    """Raised when ``cyclonedx-bom`` succeeds but the doc shape is wrong."""


def _venv_python(venv_path: Path) -> Path:
    # POSIX layout; Windows would be Scripts/python.exe but the publish
    # workflow runs ubuntu-latest exclusively.
    return venv_path / "bin" / "python"


def _venv_cyclonedx(venv_path: Path) -> Path:
    return venv_path / "bin" / "cyclonedx-py"


def emit_sbom(wheel_path: Path, output_path: Path) -> Path:
    """Install ``wheel_path`` into a temp venv and emit a CycloneDX SBOM.

    Subprocess discipline: list-form argv, ``shell=False`` (default).
    Raises ``subprocess.CalledProcessError`` on cyclonedx-bom failure
    (with stderr surfaced); raises ``SBOMValidationError`` when the
    emitted doc's bomFormat or specVersion shape is invalid.
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
        cmd = [str(cdx), "environment", "--output-file", str(output_path), str(py)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            # Surface stderr so future CLI-flag drift is diagnosable from
            # publish workflow logs without re-running with verbose mode.
            print(
                f"sbom_emit: cyclonedx-py failed (exit {exc.returncode}): {exc.stderr}",
                file=sys.stderr,
            )
            if output_path.exists():
                output_path.unlink()
            raise

    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    if parsed.get("bomFormat") != _EXPECTED_BOM_FORMAT:
        output_path.unlink()
        raise SBOMValidationError(
            f"bomFormat {parsed.get('bomFormat')!r} != {_EXPECTED_BOM_FORMAT!r}"
        )
    spec_version = parsed.get("specVersion", "")
    if not spec_version.startswith("1."):
        output_path.unlink()
        raise SBOMValidationError(
            f"specVersion {spec_version!r} not in CycloneDX 1.x — refusing to publish"
        )
    return output_path


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit CycloneDX SBOM")
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
