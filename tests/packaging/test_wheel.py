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

from adlife.city.workbench_input import CreativeTemplateCatalog
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
    compact_script = "".join(script.split())
    for resource in (
        "adlife/city/demo_city.json",
        "adlife/city/static/index.html",
        "adlife/city/static/app.css",
        "adlife/city/static/app.js",
    ):
        assert resource in names, resource
    assert 'id="saved-run-label" hidden' in html
    assert '<meta name="adlife-api-base" content="/api">' in html
    assert 'id="metrics-panel"' in html
    assert 'id="metrics-overall-notice-rate-receipt"' in html
    assert 'id="response-panel"' in html
    assert 'id="response-state-panel"' in html
    assert "Final state &middot; not the selected minute" in html
    assert "purchase intention is not purchase probability or sales" in html
    assert ".response-panel" in style
    assert ".response-state-panel" in style
    assert 'byId("saved-run-label").textContent =' in script
    assert "function validatedApiBase()" in script
    assert "function apiPath(relative)" in script
    assert 'fetchJson("/spatial-metrics")' in script
    assert 'fetchJson("/response-summary")' in script
    assert 'endpoint: "/response-events"' in script
    assert 'fetchJson(evidencePageUrl("response",next,offsets.response),' in compact_script
    assert "`${endpoint}?minute=${minute}&offset=${offset}&limit=${EVIDENCE_PAGE_SIZE}`" in script
    assert (
        "fetchJson(`/response-state?agent_id=${encodeURIComponent(selectedAgent)}`,"
        in compact_script
    )
    assert "fetchJson(timelineBatchUrl(" in compact_script
    assert "/api/spatial-metrics" not in script
    assert "/api/response-summary" not in script
    assert "/api/response-events" not in script
    assert "/api/response-state" not in script
    assert 'page.state_scope !== "final-end-of-run-not-scrubbed-minute"' in script
    assert "innerHTML" not in script
    assert "SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}" in script


def test_wheel_carries_exact_bounded_cognition_inspector_assets(built_wheel: Path) -> None:
    static = ROOT / "src" / "adlife" / "city" / "static"
    members = {
        "adlife/city/static/app-cognition-v2.js": static / "app-cognition-v2.js",
        "adlife/city/static/app-cognition-v2.css": static / "app-cognition-v2.css",
    }
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        packaged = {name: wheel.read(name) for name in members if name in names}
        script = wheel.read("adlife/city/static/app.js").decode("utf-8")
    assert set(members) <= names
    for name, source in members.items():
        assert packaged[name] == source.read_bytes(), name
    helper = packaged["adlife/city/static/app-cognition-v2.js"].decode("utf-8")
    style = packaged["adlife/city/static/app-cognition-v2.css"].decode("utf-8")
    assert 'Object.defineProperty(window, "AdLifeBoundedCognition"' in helper
    for exported in (
        "validateSummary,",
        "validateRecords,",
        "indexTerminalRecords,",
        "presentationForAppliedResponse,",
    ):
        assert exported in helper
    for forbidden in ("innerHTML", "fetch(", "localStorage", "http://", "https://"):
        assert forbidden not in helper
    assert ".bounded-cognition-panel" in style
    assert "url(" not in style
    assert "function boundedCognitionHelper()" in script
    assert 'const boundedResponseModelId = "spatial-response-v2";' in script


def test_wheel_carries_verified_fictional_city_catalog(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        catalog_bytes = wheel.read("adlife/city/catalog.json")
    assert "adlife/city/catalog.json" in names
    catalog = parse_city_catalog_json(catalog_bytes)
    assert [entry.city_id for entry in catalog.entries] == [
        "fictional-grid-v2",
        "fictional-harbor-loop-v2",
        "fictional-new-district-v2",
        "fictional-old-town-v2",
        "fictional-river-junction-v2",
    ]
    with zipfile.ZipFile(built_wheel) as wheel:
        for entry in catalog.entries:
            resource = f"adlife/city/catalog/{entry.resource_name}"
            assert resource in names
            pack = parse_city_pack_json(wheel.read(resource))
            assert isinstance(pack, CityPackV2)
            assert entry.city_id == pack.city_id
            assert entry.qualification == "fictional-fixture"
            assert entry.pack_sha256 == pack.fingerprint


def test_exact_installed_wheel_loads_fictional_personas_offline_outside_checkout(
    built_wheel: Path, tmp_path: Path
) -> None:
    import json

    from scripts import smoke_release

    with zipfile.ZipFile(built_wheel) as wheel:
        assert "adlife/city/persona_templates.json" in wheel.namelist()
        payload = json.loads(wheel.read("adlife/city/persona_templates.json"))
    assert [item["persona_id"] for item in payload["templates"]] == [
        "persona-001",
        "persona-002",
        "persona-003",
        "persona-004",
        "persona-005",
        "persona-006",
        "persona-007",
        "persona-008",
        "persona-009",
        "persona-010",
    ]
    scratch = tmp_path / "persona-clean-room"
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
            "import json, pathlib, sysconfig, adlife; "
            "from importlib.resources import files; "
            "from adlife.city.workbench_personas import load_persona_template_catalog; "
            "from adlife.city.workbench_input import WorkbenchPersonaDraftV2; "
            "assert pathlib.Path(adlife.__file__).resolve().is_relative_to("
            "pathlib.Path(sysconfig.get_path('purelib')).resolve()); "
            "assert files('adlife.city').joinpath('persona_templates.json').is_file(); "
            "catalog=load_persona_template_catalog(); "
            "assert all(isinstance(p, WorkbenchPersonaDraftV2) and p.fictional is True "
            "for p in catalog.templates); "
            "print(json.dumps([p.persona_id for p in catalog.templates]))",
        ],
        cwd=scratch,
    )
    assert json.loads(probe.stdout) == [
        "persona-001",
        "persona-002",
        "persona-003",
        "persona-004",
        "persona-005",
        "persona-006",
        "persona-007",
        "persona-008",
        "persona-009",
        "persona-010",
    ]
    assert guard.with_name(smoke_release._NETWORK_GUARD_LOG).is_file()


def test_wheel_carries_and_instantiates_local_workbench_outside_checkout(
    built_wheel: Path, tmp_path: Path
) -> None:
    import json

    from scripts import smoke_release

    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
        creative_bytes = wheel.read("adlife/city/creative_templates.json")
        icon = wheel.read("adlife/city/static/workbench-icon.svg").decode("utf-8")
        guide_sources = {
            name: wheel.read(name).decode("utf-8")
            for name in names
            if name.startswith("adlife/city/static/guide/") and name.endswith((".js", ".css"))
        }
    # The workbench server serves only the guided pages; the old technical UI is not packaged.
    for removed in (
        "adlife/city/static/workbench.html",
        "adlife/city/static/workbench.css",
        "adlife/city/static/workbench.js",
        "adlife/city/static/workbench-city-packs.js",
        "adlife/city/static/workbench-personas.js",
        "adlife/city/static/workbench-personas.css",
        "adlife/city/static/workbench-providers.js",
        "adlife/city/static/workbench-providers.css",
    ):
        assert removed not in names, removed
    for resource in (
        "adlife/city/creative_templates.json",
        # The guide and the report use the icon.
        "adlife/city/static/workbench-icon.svg",
        # The separate `adlife city` and `adlife city-view` viewers keep their dashboard.
        "adlife/city/static/index.html",
        "adlife/city/static/app.js",
        "adlife/city/static/app.css",
        "adlife/city/static/app-cognition-v2.js",
        "adlife/city/static/app-cognition-v2.css",
        # The guided presenter view: catalogs, page templates, assets and the run report.
        "adlife/i18n/catalogs/en.json",
        "adlife/i18n/catalogs/de.json",
        "adlife/i18n/catalogs/fa.json",
        "adlife/city/templates/guide/base.html.j2",
        "adlife/city/templates/guide/error.html.j2",
        "adlife/city/templates/guide/start.html.j2",
        "adlife/city/templates/guide/new.html.j2",
        "adlife/city/templates/guide/job.html.j2",
        "adlife/city/templates/guide/story.html.j2",
        "adlife/city/static/guide/guide.css",
        "adlife/city/static/guide/guide-setup.css",
        "adlife/city/static/guide/guide-story.css",
        "adlife/city/static/guide/guide-core.js",
        "adlife/city/static/guide/guide-i18n.js",
        "adlife/city/static/guide/guide-connection.js",
        "adlife/city/static/guide/guide-start.js",
        "adlife/city/static/guide/guide-setup.js",
        "adlife/city/static/guide/guide-progress.js",
        "adlife/city/static/guide/guide-story.js",
        "adlife/city/static/guide/guide-map.js",
        "adlife/reporting/templates/run-report.html.j2",
        "adlife/reporting/static/run-report.css",
    ):
        assert resource in names, resource
    catalog = CreativeTemplateCatalog.model_validate_json(creative_bytes)
    assert [item.template_id for item in catalog.templates] == [
        "fictional-bike-share-v1",
        "fictional-book-box-v1",
        "fictional-coffee-pass-v1",
        "fictional-crispy-meal-v1",
        "fictional-device-launch-v1",
        "fictional-fitness-app-v1",
        "fictional-home-delivery-v1",
        "fictional-music-stream-v1",
    ]
    assert icon.startswith("<svg") and "<script" not in icon.lower()
    assert {name.rsplit("/", 1)[1] for name in guide_sources} >= {
        "guide.css",
        "guide-core.js",
        "guide-start.js",
        "guide-story.js",
    }
    for name, source in guide_sources.items():
        assert "http://" not in source and "https://" not in source, name
        assert "@import" not in source.lower(), name
        for forbidden in (
            "innerHTML",
            "outerHTML",
            "insertAdjacentHTML",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "serviceWorker",
            "eval(",
            "new Function",
        ):
            assert forbidden not in source, (name, forbidden)

    scratch = tmp_path / "workbench-clean-room"
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
            "import json, pathlib, sys, sysconfig; "
            "import adlife; "
            "from fastapi.testclient import TestClient; "
            "from adlife.city.workbench_personas import PersonaTemplateCatalog; "
            "from adlife.city.workbench_workspace import prepare_workbench_workspace; "
            "from adlife.city.workbench_web import create_city_workbench_app; "
            "package=pathlib.Path(adlife.__file__).resolve(); "
            "assert package.is_relative_to(pathlib.Path(sysconfig.get_path('purelib')).resolve()); "
            "workspace=prepare_workbench_workspace(pathlib.Path(sys.argv[1])); "
            "app=create_city_workbench_app(workspace, csrf_token='installed-wheel-token'); "
            "paths=sorted({route.path for route in app.routes if hasattr(route, 'path')});\n"
            "with TestClient(app, base_url='http://127.0.0.1:8765') as client:\n"
            "    response=client.get('/api/persona-templates')\n"
            "    assert response.status_code == 200\n"
            "    catalog=PersonaTemplateCatalog.model_validate_json(response.content)\n"
            "    assert [p.persona_id for p in catalog.templates] == "
            "[f'persona-{index:03d}' for index in range(1, 11)]\n"
            "    assert all(p.fictional is True for p in catalog.templates)\n"
            "    root=client.get('/', follow_redirects=False)\n"
            "    assert root.status_code == 307 and root.headers['location'] == '/guide'\n"
            "    assert \"script-src 'self'\" in root.headers['content-security-policy']\n"
            "    for removed in ['/assets/workbench.js', '/assets/workbench.css', "
            "'/static/app.js', '/runs/any-run', '/runs/any-run/reuse']:\n"
            "        assert client.get(removed).status_code == 404\n"
            "    for name, media in [('guide-core.js', 'text/javascript'), "
            "('guide-start.js', 'text/javascript'), ('guide.css', 'text/css')]:\n"
            "        asset=client.get('/guide/assets/'+name)\n"
            "        assert asset.status_code == 200 and len(asset.content) > 100\n"
            "        assert asset.headers['content-type'].startswith(media)\n"
            "        assert asset.headers['cache-control'] == 'no-store'\n"
            "        assert asset.headers['x-content-type-options'] == 'nosniff'\n"
            "        assert \"script-src 'self'\" in asset.headers['content-security-policy']\n"
            "    page=client.get('/guide?lang=fa')\n"
            "    assert page.status_code == 200\n"
            '    assert \'<html lang="fa" dir="rtl">\' in page.text\n'
            "    assert page.headers['content-type'] == 'text/html; charset=utf-8'\n"
            "    catalog_asset=client.get('/guide/assets/i18n-fa.json')\n"
            "    assert catalog_asset.status_code == 200\n"
            "    assert catalog_asset.headers['content-type'] == 'application/json'\n"
            "    assert json.loads(catalog_asset.content)['direction'] == 'rtl'\n"
            "print(json.dumps(paths))",
            str(scratch / "workspace"),
        ],
        cwd=scratch,
    )
    assert set(json.loads(probe.stdout)) == {
        "/",
        "/api/catalog/cities",
        "/api/catalog/cities/upload",
        "/api/catalog/cities/{city_id}",
        "/api/creative-templates",
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/jobs/{job_id}/cancel",
        "/api/persona-templates",
        "/api/providers",
        "/api/providers/origins",
        "/api/providers/{profile_id}/revisions/{revision_id}/bounded-check",
        "/api/providers/{profile_id}/revisions/{revision_id}/check",
        "/api/providers/{profile_id}/revisions/{revision_id}/credential",
        "/api/runs",
        "/api/runs/{run_id}/cognition-records",
        "/api/runs/{run_id}/cognition-summary",
        "/api/runs/{run_id}/story",
        "/api/runs/{run_id}/workbench-input",
        "/api/scenarios/validate",
        "/api/workbench",
        "/assets/workbench-icon.svg",
        "/guide",
        "/guide/new",
        "/guide/jobs/{job_id}",
        "/guide/runs/{run_id}",
        "/guide/runs/{run_id}/report",
        "/guide/runs/{run_id}/report/download",
        "/guide/assets/{asset_name}",
    }
    assert guard.with_name(smoke_release._NETWORK_GUARD_LOG).is_file()


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
    assert (
        "schema-v6 response API/UI, schema-v7 compatibility and "
        "schema-v8 persona lifecycle plus schema-v9 rules/mock explanations and "
        "a local compatible provider verified under the network guard" in completed.stdout
    )


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
    conflict = smoke_release._run(command, cwd=scratch, expected_exit=3)
    assert conflict.returncode == 3
    assert json.loads(conflict.stdout)["error"]["message"] == "report destination already exists"
    assert report.read_bytes() == original
    assert _artifact_bytes(scratch / "city-runs") == before
    assert guard.with_name(smoke_release._NETWORK_GUARD_LOG).is_file()
