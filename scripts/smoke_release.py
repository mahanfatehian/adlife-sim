"""Clean-room release smoke: install a built wheel, drive the whole workflow offline.

The wheel is the canonical distribution, so release verification never happens inside
the development checkout. This script creates a temporary virtual environment, installs
exactly the wheel it was handed, changes to a scratch directory outside the checkout,
and runs the documented five-command workflow — version, offline doctor, study
initialisation, a small deterministic rules run, and the self-contained report —
validating the JSON output contract along the way. Every temporary artifact is cleaned
up on success and on failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = completed.stdout + completed.stderr
        print(f"FAILED ({completed.returncode}): {' '.join(command)}", file=sys.stderr)
        print(detail, file=sys.stderr)
        raise SystemExit(1)
    return completed


def _expect_json(completed: subprocess.CompletedProcess[str], label: str) -> dict[str, object]:
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        print(f"{label}: stdout was not a JSON document: {error}", file=sys.stderr)
        print(completed.stdout[:2000], file=sys.stderr)
        raise SystemExit(1) from error
    if not isinstance(document, dict):
        print(f"{label}: expected a JSON object on stdout", file=sys.stderr)
        raise SystemExit(1)
    return document


def _uv_executable() -> str | None:
    found = shutil.which("uv")
    return str(Path(found).resolve()) if found else None


def _create_interpreter(workspace: Path) -> tuple[Path, str | None]:
    """Create a venv with a supported interpreter; prefer uv when available.

    The package requires Python >=3.11,<3.14, so the interpreter is pinned to 3.13
    (the newest supported minor) rather than whatever ``python`` happens to be on
    PATH. uv-created venvs carry no pip, so installation goes through
    ``uv pip install --python`` in that case.
    """
    venv = workspace / "venv"
    uv = _uv_executable()
    if uv is not None:
        _run([uv, "venv", "--python", "3.13", str(venv)])
        scripts = "Scripts" if sys.platform == "win32" else "bin"
        return (
            venv / scripts / "python.exe" if sys.platform == "win32" else venv / scripts / "python",
            uv,
        )
    _run([sys.executable, "-m", "venv", str(venv)])
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    return (
        venv / scripts / "python.exe" if sys.platform == "win32" else venv / scripts / "python",
        None,
    )


def _install_wheel(python: Path, uv: str | None, wheel: Path) -> None:
    if uv is not None:
        _run([uv, "pip", "install", "--python", str(python), "--quiet", str(wheel)])
    else:
        _run([str(python), "-m", "pip", "install", "--quiet", str(wheel)])


def smoke(wheel: Path) -> Path:
    workspace = Path(tempfile.mkdtemp(prefix="adlife-smoke-"))
    try:
        scratch = workspace / "scratch"
        scratch.mkdir(parents=True)
        python, uv = _create_interpreter(workspace)
        adlife = python.parent / ("adlife.exe" if sys.platform == "win32" else "adlife")

        _install_wheel(python, uv, wheel)
        _run([str(adlife), "--version"])
        doctor = _run([str(adlife), "--format", "json", "doctor", "--offline"])
        doctor_document = _expect_json(doctor, "doctor --offline")

        _run([str(adlife), "init", "smoke-study"], cwd=scratch)
        run = _run(
            [
                str(adlife),
                "--format",
                "json",
                "run",
                "smoke-study",
                "--campaign",
                "campaigns/demo-phone.yaml",
                "--run-id",
                "smoke",
                "--mode",
                "rules",
                "--days",
                "1",
                "--population-size",
                "2",
                "--headless",
            ],
            cwd=scratch,
        )
        run_document = _expect_json(run, "run")
        status = str(run_document.get("status", run_document.get("state", "")))
        if "complete" not in status:
            print(f"run document does not record completion: {run_document}", file=sys.stderr)
            raise SystemExit(1)

        report = _run(
            [str(adlife), "--format", "json", "report", "smoke-study", "smoke"],
            cwd=scratch,
        )
        _expect_json(report, "report")

        html = scratch / "smoke-study" / "reports" / "smoke.html"
        if not html.is_file() or html.stat().st_size < 10_000:
            print(f"expected a substantive self-contained report at {html}", file=sys.stderr)
            raise SystemExit(1)
        _ = doctor_document  # parsed: the JSON contract held
        print(f"smoke ok: report at {html.stat().st_size} bytes")
        return scratch
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Path to the built wheel to verify.")
    arguments = parser.parse_args()
    if not arguments.wheel.is_file():
        print(f"wheel not found: {arguments.wheel}", file=sys.stderr)
        raise SystemExit(2)
    smoke(arguments.wheel.resolve())


if __name__ == "__main__":
    main()
