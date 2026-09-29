"""Clean-room release smoke: install a built wheel, drive the whole workflow offline.

The wheel is the canonical distribution, so release verification never happens inside
the development checkout. This script creates a temporary virtual environment, installs
exactly the wheel it was handed, changes to a scratch directory outside the checkout,
and runs the documented workflow — including the verified packaged city catalog, a
catalog-selected v2 mobility run and its replay — validating the JSON output contract
along the way. Every temporary artifact is cleaned up on success and on failure.
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
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
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


def _refuse(label: str, detail: str) -> None:
    print(f"{label}: {detail}", file=sys.stderr)
    raise SystemExit(1)


def _expect_value(document: dict[str, object], key: str, expected: object, label: str) -> None:
    actual = document.get(key)
    if type(actual) is not type(expected) or actual != expected:
        _refuse(label, f"expected {key}={expected!r}, got {actual!r}")


def _uv_executable() -> str | None:
    found = shutil.which("uv")
    return str(Path(found).resolve()) if found else None


def _create_interpreter(workspace: Path) -> tuple[Path, str | None]:
    """Create a venv with a supported interpreter; prefer uv when available.

    Reuse the executing Python when supported so CI tests its selected Python
    version and offline checks need no additional interpreter download. For an
    unsupported host interpreter, uv selects 3.13. uv-created venvs carry no pip,
    so installation goes through ``uv pip install --python`` in that case.
    """
    venv = workspace / "venv"
    uv = _uv_executable()
    if uv is not None:
        interpreter = sys.executable if (3, 11) <= sys.version_info[:2] < (3, 14) else "3.13"
        _run([uv, "venv", "--python", interpreter, str(venv)])
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


def smoke(wheel: Path) -> None:
    """Verify the wheel, then remove all temporary artifacts before returning."""
    workspace = Path(tempfile.mkdtemp(prefix="adlife-smoke-"))
    try:
        scratch = workspace / "scratch"
        scratch.mkdir(parents=True)
        python, uv = _create_interpreter(workspace)
        adlife = python.parent / ("adlife.exe" if sys.platform == "win32" else "adlife")

        _install_wheel(python, uv, wheel)
        _run([str(adlife), "--version"], cwd=scratch)
        doctor = _run([str(adlife), "--format", "json", "doctor", "--offline"], cwd=scratch)
        doctor_document = _expect_json(doctor, "doctor --offline")

        catalog_list = _expect_json(
            _run(
                [str(adlife), "--format", "json", "city-catalog", "list"],
                cwd=scratch,
            ),
            "city-catalog list",
        )
        cities = catalog_list.get("cities")
        if not isinstance(cities, list) or not any(
            isinstance(entry, dict) and entry.get("city_id") == "fictional-grid-v2"
            for entry in cities
        ):
            _refuse("city-catalog list", "fictional-grid-v2 was not listed")
        catalog_entry = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-catalog",
                    "show",
                    "fictional-grid-v2",
                ],
                cwd=scratch,
            ),
            "city-catalog show",
        )
        _expect_value(catalog_entry, "city_id", "fictional-grid-v2", "city-catalog show")
        _expect_value(catalog_entry, "qualification", "fictional-fixture", "city-catalog show")
        _expect_value(catalog_entry, "pack_schema_version", 2, "city-catalog show")
        catalog_sha256 = catalog_entry.get("pack_sha256")
        if not isinstance(catalog_sha256, str) or len(catalog_sha256) != 64:
            _refuse("city-catalog show", "pack_sha256 is not a SHA-256 digest")

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

        city_run = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-run",
                    "--city-id",
                    "fictional-grid-v2",
                    "--output-root",
                    "city-output",
                    "--run-id",
                    "catalog-smoke",
                    "--agents",
                    "2",
                    "--days",
                    "1",
                    "--seed",
                    "42",
                ],
                cwd=scratch,
            ),
            "city-run --city-id",
        )
        _expect_value(city_run, "run_id", "catalog-smoke", "city-run --city-id")
        _expect_value(city_run, "city_id", "fictional-grid-v2", "city-run --city-id")
        _expect_value(city_run, "city_sha256", catalog_sha256, "city-run --city-id")
        _expect_value(city_run, "frame_count", 1_440, "city-run --city-id")
        _expect_value(city_run, "position_count", 2_880, "city-run --city-id")

        replay = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-replay",
                    "city-output",
                    "catalog-smoke",
                ],
                cwd=scratch,
            ),
            "city-replay",
        )
        _expect_value(replay, "run_id", "catalog-smoke", "city-replay")
        _expect_value(replay, "identical", True, "city-replay")
        for key in ("city_sha256", "trace_sha256", "frame_count", "position_count"):
            _expect_value(replay, key, city_run.get(key), "city-replay")
        _ = doctor_document  # parsed: the JSON contract held
        print(
            "smoke ok: "
            f"report at {html.stat().st_size} bytes; "
            "catalog fictional-grid-v2 replay verified"
        )
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
