"""Clean-room release smoke: install a built wheel, drive the whole workflow offline.

The wheel is the canonical distribution, so release verification never happens inside
the development checkout. This script creates a temporary virtual environment, installs
exactly the wheel it was handed, changes to a scratch directory outside the checkout,
and runs the documented workflow — including the verified packaged city catalog,
validated fictional place and spatial-campaign inputs, a schema-v4 spatial opportunity
run and its replay — validating the JSON output contract along the way. Every temporary
artifact is cleaned up on success and on failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import site
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Never


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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


def _refuse(label: str, detail: str) -> Never:
    print(f"{label}: {detail}", file=sys.stderr)
    raise SystemExit(1)


def _expect_value(document: dict[str, object], key: str, expected: object, label: str) -> None:
    actual = document.get(key)
    if type(actual) is not type(expected) or actual != expected:
        _refuse(label, f"expected {key}={expected!r}, got {actual!r}")


def _write_fictional_place_set(path: Path, *, city_sha256: str) -> None:
    """Create the clean-room fixture without reading from the source checkout."""
    provenance = {"method": "operator-authored-fictional"}
    document = {
        "schema_version": 1,
        "city_id": "fictional-grid-v2",
        "city_sha256": city_sha256,
        "name": "Fictional clean-room smoke places",
        "places": [
            {
                "place_id": "home-west",
                "kind": "home",
                "node_id": "west-north",
                "label": "Fictional west home",
                "provenance": provenance,
            },
            {
                "place_id": "home-east",
                "kind": "home",
                "node_id": "east-north",
                "label": "Fictional east home",
                "provenance": provenance,
            },
            {
                "place_id": "work-center",
                "kind": "workplace",
                "node_id": "center-center",
                "label": "Fictional center workplace",
                "provenance": provenance,
            },
            {
                "place_id": "leisure-south",
                "kind": "leisure",
                "node_id": "center-south",
                "label": "Fictional south leisure venue",
                "provenance": provenance,
            },
        ],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_fictional_spatial_campaign(path: Path, *, city_sha256: str) -> None:
    """Create one explicit two-channel validation fixture for the packaged grid."""
    document = {
        "schema_version": 1,
        "scenario_id": "clean-room-spatial",
        "name": "Fictional clean-room spatial campaign",
        "days": 1,
        "city_id": "fictional-grid-v2",
        "city_sha256": city_sha256,
        "campaigns": [
            {
                "campaign_id": "fictional-launch",
                "name": "Fictional product launch",
                "creative_sha256": sha256(b"fictional clean-room creative").hexdigest(),
            }
        ],
        "placements": [
            {
                "placement_id": "north-road-billboard",
                "campaign_id": "fictional-launch",
                "channel": "roadside-billboard",
                "active_windows": [{"start_minute": 360, "end_minute": 1_140}],
                "frequency_cap_per_agent_per_day": 3,
                "road_id": "north-west",
                "travel_direction": "forward",
                "road_fraction": 0.5,
                "longitude": 0.02,
                "latitude": 0.06,
                "side": "right",
                "orientation_degrees": 180.0,
                "max_view_distance_meters": 120.0,
            },
            {
                "placement_id": "fictional-mobile-feed",
                "campaign_id": "fictional-launch",
                "channel": "mobile-feed",
                "active_windows": [{"start_minute": 360, "end_minute": 1_140}],
                "frequency_cap_per_agent_per_day": 2,
                "opportunity_model": "keyed-activity-minute-v1",
                "eligible_activities": ["home", "commute", "work", "leisure"],
                "opportunity_probability_per_minute": 0.05,
            },
        ],
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


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


def _install_wheel(python: Path, uv: str | None, wheel: Path, *, no_deps: bool = False) -> None:
    dependency_option = ["--no-deps"] if no_deps else []
    if uv is not None:
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--quiet",
                *dependency_option,
                str(wheel),
            ]
        )
    else:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                *dependency_option,
                str(wheel),
            ]
        )


def _expose_locked_dependencies(python: Path) -> None:
    """Expose this locked environment's dependencies to an isolated wheel venv.

    This is solely for the repository's network-free pytest smoke. The wheel is still
    installed into a fresh environment and is not imported from the source checkout.
    Release CI deliberately omits this mode and resolves a genuinely clean environment.
    """
    completed = _run([str(python), "-c", "import site; print(site.getsitepackages()[0])"])
    child_site = Path(completed.stdout.strip()).resolve()
    environment_root = python.parent.parent.resolve()
    if not child_site.is_relative_to(environment_root):
        raise RuntimeError("child site-packages is outside the smoke environment")
    if not child_site.is_dir():
        raise RuntimeError("child site-packages does not exist")

    host_sites = sorted(
        {
            path
            for value in site.getsitepackages()
            if (path := Path(value).resolve()).is_dir() and path != child_site
        },
        key=str,
    )
    if not host_sites:
        raise RuntimeError("locked host dependencies are unavailable")
    dependency_paths = "".join(f"{path}\n" for path in host_sites)
    (child_site / "adlife-smoke-locked-dependencies.pth").write_text(
        dependency_paths, encoding="utf-8"
    )


def smoke(wheel: Path, *, reuse_locked_dependencies: bool = False) -> None:
    """Verify the wheel, then remove all temporary artifacts before returning."""
    workspace = Path(tempfile.mkdtemp(prefix="adlife-smoke-"))
    try:
        scratch = workspace / "scratch"
        scratch.mkdir(parents=True)
        python, uv = _create_interpreter(workspace)
        adlife = python.parent / ("adlife.exe" if sys.platform == "win32" else "adlife")

        if reuse_locked_dependencies:
            _expose_locked_dependencies(python)
        _install_wheel(python, uv, wheel, no_deps=reuse_locked_dependencies)
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

        places_path = scratch / "fictional-grid-v2-places.json"
        _write_fictional_place_set(places_path, city_sha256=catalog_sha256)
        places = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-places",
                    "validate",
                    str(places_path),
                    "--city-id",
                    "fictional-grid-v2",
                    "--agents",
                    "2",
                    "--days",
                    "1",
                    "--seed",
                    "42",
                ],
                cwd=scratch,
            ),
            "city-places validate",
        )
        _expect_value(places, "valid", True, "city-places validate")
        _expect_value(places, "city_id", "fictional-grid-v2", "city-places validate")
        place_set_sha256 = places.get("place_set_sha256")
        if not isinstance(place_set_sha256, str) or len(place_set_sha256) != 64:
            _refuse("city-places validate", "place_set_sha256 is not a SHA-256 digest")

        spatial_path = scratch / "fictional-grid-v2-spatial-campaign.json"
        _write_fictional_spatial_campaign(spatial_path, city_sha256=catalog_sha256)
        spatial = _expect_json(
            _run(
                [
                    str(adlife),
                    "--format",
                    "json",
                    "city-campaign",
                    "validate",
                    str(spatial_path),
                    "--city-id",
                    "fictional-grid-v2",
                ],
                cwd=scratch,
            ),
            "city-campaign validate",
        )
        _expect_value(spatial, "valid", True, "city-campaign validate")
        _expect_value(
            spatial,
            "scenario_id",
            "clean-room-spatial",
            "city-campaign validate",
        )
        _expect_value(spatial, "city_sha256", catalog_sha256, "city-campaign validate")
        _expect_value(spatial, "campaign_count", 1, "city-campaign validate")
        _expect_value(spatial, "placement_count", 2, "city-campaign validate")
        _expect_value(spatial, "billboard_count", 1, "city-campaign validate")
        _expect_value(spatial, "phone_count", 1, "city-campaign validate")
        scenario_sha256 = spatial.get("scenario_sha256")
        if not isinstance(scenario_sha256, str) or len(scenario_sha256) != 64:
            _refuse("city-campaign validate", "scenario_sha256 is not a SHA-256 digest")
        binding_error = spatial.get("max_billboard_binding_error_meters")
        if type(binding_error) is not float or not 0 <= binding_error <= 1:
            _refuse("city-campaign validate", "billboard binding error is outside 0..1 m")

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
                    "--places",
                    str(places_path),
                    "--spatial-campaign",
                    str(spatial_path),
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
        _expect_value(city_run, "run_schema_version", 5, "city-run --city-id")
        _expect_value(city_run, "city_id", "fictional-grid-v2", "city-run --city-id")
        _expect_value(city_run, "city_sha256", catalog_sha256, "city-run --city-id")
        _expect_value(
            city_run,
            "place_set_sha256",
            place_set_sha256,
            "city-run --city-id",
        )
        assignments_sha256 = city_run.get("place_assignments_sha256")
        if not isinstance(assignments_sha256, str) or len(assignments_sha256) != 64:
            _refuse("city-run --city-id", "place_assignments_sha256 is not a SHA-256 digest")
        _expect_value(
            city_run,
            "scenario_sha256",
            scenario_sha256,
            "city-run --city-id",
        )
        for key in ("opportunity_stream_sha256", "opportunity_summary_sha256"):
            value = city_run.get(key)
            if not isinstance(value, str) or len(value) != 64:
                _refuse("city-run --city-id", f"{key} is not a SHA-256 digest")
        opportunity_bytes = city_run.get("opportunity_stream_bytes")
        if type(opportunity_bytes) is not int or opportunity_bytes <= 0:
            _refuse("city-run --city-id", "opportunity stream is empty or has invalid size")
        opportunity_count = city_run.get("opportunity_count")
        opportunity_counts = city_run.get("opportunity_counts")
        if type(opportunity_count) is not int or opportunity_count <= 0:
            _refuse("city-run --city-id", "opportunity stream contains no opportunities")
        if not isinstance(opportunity_counts, dict) or (
            opportunity_counts.get("opportunity_count") != opportunity_count
        ):
            _refuse("city-run --city-id", "opportunity summary does not match the stream")
        _expect_value(
            city_run,
            "opportunity_claim_scope",
            "synthetic-opportunity-not-impression",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_model_id",
            "spatial-attention-v1",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_claim_scope",
            "synthetic-attention-not-observed-behavior",
            "city-run --city-id",
        )
        _expect_value(
            city_run,
            "attention_notice_probability",
            0.5,
            "city-run --city-id",
        )
        for key in ("attention_stream_sha256", "attention_summary_sha256"):
            value = city_run.get(key)
            if not isinstance(value, str) or len(value) != 64:
                _refuse("city-run --city-id", f"{key} is not a SHA-256 digest")
        attention_bytes = city_run.get("attention_stream_bytes")
        if type(attention_bytes) is not int or attention_bytes <= 0:
            _refuse("city-run --city-id", "attention stream is empty or has invalid size")
        impression_count = city_run.get("impression_count")
        noticed_count = city_run.get("noticed_count")
        attention_counts = city_run.get("attention_counts")
        if impression_count != opportunity_count:
            _refuse("city-run --city-id", "impressions do not match opportunities")
        if (
            type(noticed_count) is not int
            or type(impression_count) is not int
            or not 0 <= noticed_count <= impression_count
        ):
            _refuse("city-run --city-id", "attention funnel counts are invalid")
        if not isinstance(attention_counts, dict) or (
            attention_counts.get("impression_count") != impression_count
            or attention_counts.get("noticed_count") != noticed_count
        ):
            _refuse("city-run --city-id", "attention summary does not match the stream")
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
        for key in (
            "city_sha256",
            "place_set_sha256",
            "place_assignments_sha256",
            "trace_sha256",
            "scenario_sha256",
            "opportunity_stream_sha256",
            "opportunity_summary_sha256",
            "opportunity_stream_bytes",
            "opportunity_count",
            "opportunity_counts",
            "opportunity_claim_scope",
            "attention_model_id",
            "attention_claim_scope",
            "attention_notice_probability",
            "attention_stream_sha256",
            "attention_summary_sha256",
            "attention_stream_bytes",
            "impression_count",
            "noticed_count",
            "attention_counts",
            "frame_count",
            "position_count",
        ):
            _expect_value(replay, key, city_run.get(key), "city-replay")
        _ = doctor_document  # parsed: the JSON contract held
        print(
            "smoke ok: "
            f"report at {html.stat().st_size} bytes; "
            "catalog fictional-grid-v2 spatial run and replay verified"
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse-locked-dependencies",
        action="store_true",
        help=(
            "Reuse dependencies from the invoking locked environment for a network-free "
            "test; the wheel itself is still installed into a fresh environment."
        ),
    )
    parser.add_argument("wheel", type=Path, help="Path to the built wheel to verify.")
    arguments = parser.parse_args()
    if not arguments.wheel.is_file():
        print(f"wheel not found: {arguments.wheel}", file=sys.stderr)
        raise SystemExit(2)
    smoke(
        arguments.wheel.resolve(),
        reuse_locked_dependencies=arguments.reuse_locked_dependencies,
    )


if __name__ == "__main__":
    main()
