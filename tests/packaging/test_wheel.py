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


def test_wheel_carries_offline_city_viewer_and_demo_pack(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        html = wheel.read("adlife/city/static/index.html").decode("utf-8")
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
    assert 'byId("saved-run-label").textContent =' in script
    assert 'fetchJson("/api/spatial-metrics")' in script
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
        env={**os.environ, "UV_OFFLINE": "1"},
    )
    report.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, report.read_text(encoding="utf-8")
    assert "smoke ok" in completed.stdout
