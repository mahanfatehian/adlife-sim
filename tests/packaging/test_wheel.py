"""The wheel is the canonical distribution, verified from a clean room.

A wheel that works in the development checkout proves nothing: these tests build the
real wheel, assert it physically carries every packaged resource the product needs, and
then hand it to :mod:`scripts.smoke_release`, which installs it into a temporary
virtual environment outside the checkout and drives the whole workflow offline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from adlife.core.domain.city import CityPackV2, parse_city_pack_json
from adlife.core.domain.city_catalog import parse_city_catalog_json

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--no-sources", "--out-dir", str(out_dir)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_OFFLINE": "1"},
    )
    wheels = sorted(out_dir.glob("adlife_sim-*.whl"))
    assert wheels, "uv build produced no wheel"
    return wheels[-1]


def test_wheel_carries_package_yaml_resources(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
    for resource in (
        "adlife/resources/demo/adlife.yaml",
        "adlife/resources/demo/population.yaml",
        "adlife/resources/demo/campaigns/demo-phone.yaml",
        "adlife/resources/demo/campaigns/demo-billboard.yaml",
    ):
        assert resource in names, resource


def test_wheel_carries_report_template_and_stylesheets(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
    assert "adlife/reporting/templates/report.html.j2" in names
    assert "adlife/reporting/static/report.css" in names
    assert "adlife/tui/styles.tcss" in names
    assert "adlife/reporting/templates/spatial-report.html.j2" in names
    assert "adlife/reporting/static/spatial-report.css" in names


def test_wheel_carries_offline_city_viewer_and_demo_pack(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        html = wheel.read("adlife/city/static/index.html").decode("utf-8")
        style = wheel.read("adlife/city/static/app.css").decode("utf-8")
        script = wheel.read("adlife/city/static/app.js").decode("utf-8")
    for resource in (
        "adlife/city/demo_city.json",
        "adlife/city/static/index.html",
        "adlife/city/static/app.css",
        "adlife/city/static/app.js",
    ):
        assert resource in names, resource
    assert 'id="saved-run-label" hidden' in html
    assert 'id="metrics-panel"' in html
    assert 'id="metrics-overall-notice-rate-receipt"' in html
    assert 'id="response-panel"' in html
    assert 'id="response-state-panel"' in html
    assert "NOT STATE AT THE SCRUBBED MINUTE" in html
    assert "PURCHASE INTENTION IS NOT PURCHASE PROBABILITY OR SALES" in html
    assert ".response-panel" in style
    assert ".response-state-panel" in style
    assert 'byId("saved-run-label").textContent =' in script
    assert 'fetchJson("/api/spatial-metrics")' in script
    assert 'fetchJson("/api/response-summary")' in script
    assert "fetchJson(`/api/response-events?minute=${next}`)" in script
    assert (
        "fetchJson(`/api/response-state?agent_id=${encodeURIComponent(state.selected)}`)" in script
    )
    assert 'page.state_scope !== "final-end-of-run-not-scrubbed-minute"' in script
    assert "innerHTML" not in script
    assert "SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}" in script


def test_wheel_carries_verified_fictional_city_catalog(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        catalog_bytes = wheel.read("adlife/city/catalog.json")
        pack_bytes = wheel.read("adlife/city/catalog/fictional-grid-v2.json")
    assert "adlife/city/catalog.json" in names
    assert "adlife/city/catalog/fictional-grid-v2.json" in names
    catalog = parse_city_catalog_json(catalog_bytes)
    pack = parse_city_pack_json(pack_bytes)
    assert isinstance(pack, CityPackV2)
    assert len(catalog.entries) == 1
    entry = catalog.entries[0]
    assert entry.city_id == pack.city_id == "fictional-grid-v2"
    assert entry.qualification == "fictional-fixture"
    assert entry.pack_sha256 == pack.fingerprint


def test_wheel_smoke_installs_and_runs_offline(built_wheel: Path, tmp_path: Path) -> None:
    report = tmp_path / "smoke-result.txt"
    shadow = tmp_path / "shadow-checkout"
    (shadow / "adlife").mkdir(parents=True)
    (shadow / "adlife" / "__init__.py").write_text(
        'raise RuntimeError("shadow checkout package was imported")\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "smoke_release.py"),
            "--reuse-locked-dependencies",
            str(built_wheel),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=tmp_path,
        env={
            **os.environ,
            "UV_OFFLINE": "1",
            # A clean-wheel smoke must not silently import the checkout even when
            # the caller supplies the most adversarial plausible Python path.
            "PYTHONPATH": str(shadow),
        },
    )
    report.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, report.read_text(encoding="utf-8")
    assert "smoke ok" in completed.stdout
    assert "schema-v6 response API/UI verified without network" in completed.stdout


def test_exact_installed_wheel_spatial_report_is_offline_and_no_clobber(built_wheel, tmp_path):
    import json

    from scripts import smoke_release
    from tests.integration.test_city_spatial_study import definition, make_runs
    from tests.unit.city.test_city_analysis import _artifact_bytes

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    python, uv = smoke_release._create_interpreter(tmp_path)
    smoke_release._expose_locked_dependencies(python)
    smoke_release._install_wheel(python, uv, built_wheel, no_deps=True)
    guard = smoke_release._install_network_guard(python, cwd=scratch)
    probe = smoke_release._run(
        [
            str(python),
            "-I",
            "-c",
            "import pathlib, sysconfig, adlife; "
            "assert pathlib.Path(adlife.__file__).resolve().is_relative_to("
            "pathlib.Path(sysconfig.get_path('purelib')).resolve()); print('exact wheel')",
        ],
        cwd=scratch,
    )
    assert probe.stdout.strip() == "exact wheel"
    make_runs(scratch, response=True)
    from adlife.core.domain.serialization import canonical_json

    study = scratch / "study.json"
    study.write_text(canonical_json(definition(response=True)), encoding="utf-8")
    before = _artifact_bytes(scratch / "city-runs")
    adlife = python.parent / ("adlife.exe" if sys.platform == "win32" else "adlife")
    command = [str(adlife), "--format", "json", "city-report", ".", "study.json"]
    completed = smoke_release._run(command, cwd=scratch)
    receipt = json.loads(completed.stdout)
    assert receipt["report_path"] == "city-reports/repeated-study.html"
    assert len(receipt) == 9 and completed.stderr == ""
    report = scratch / receipt["report_path"]
    original = report.read_bytes()
    assert b"<style>" in original and b"<script" not in original
    assert b"Content-Security-Policy" in original
    conflict = subprocess.run(
        command,
        cwd=scratch,
        capture_output=True,
        text=True,
        env=smoke_release._subprocess_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=60,
    )
    assert conflict.returncode == 3
    assert json.loads(conflict.stdout)["error"]["message"] == "report destination already exists"
    assert report.read_bytes() == original
    assert _artifact_bytes(scratch / "city-runs") == before
    assert guard.with_name(smoke_release._NETWORK_GUARD_LOG).is_file()
