from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from playwright.sync_api import Request as PlaywrightRequest
from playwright.sync_api import Route, expect, sync_playwright

from adlife.city.runs import create_city_run
from adlife.city.web import create_city_app
from adlife.city.workbench_input import WorkbenchRunDraft, WorkbenchRunDraftV2
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.experiments.spatial_response_metrics import (
    SpatialResponseMetrics,
    derive_spatial_response_metrics,
)
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
)
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    evaluate_spatial_responses,
)
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)
from tests.unit.city.test_spatial_response import _response_input
from tests.unit.city.test_workbench_input import draft_data, persona_data

_BROWSER_PATHS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)
_MAXIMUM_RUN_ID = "r" + ("a" * 39)
_MAXIMUM_SCENARIO_ID = "s" + ("a" * 79)
_MAXIMUM_CAMPAIGN_ID = "c" + ("a" * 79)


def _installed_browser() -> Path:
    browser = next((path for path in _BROWSER_PATHS if path.is_file()), None)
    if browser is None:
        message = "no supported local Chromium browser executable is installed"
        if os.environ.get("ADLIFE_REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return browser


def test_required_browser_gate_never_silently_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_installed_browser.__globals__, "_BROWSER_PATHS", ())
    monkeypatch.setenv("ADLIFE_REQUIRE_BROWSER", "1")

    try:
        _installed_browser()
    except (pytest.skip.Exception, pytest.fail.Exception) as error:
        assert isinstance(error, pytest.fail.Exception)
    else:
        pytest.fail("required browser gate accepted a missing browser")


_API_BASE_META = re.compile(
    r'\s*<meta\s+name="adlife-api-base"\s+content="[^"]*"\s*/?>',
)


def _viewer_html_with_api_bases(*values: str) -> str:
    source = (
        resources.files("adlife.city").joinpath("static", "index.html").read_text(encoding="utf-8")
    )
    source = _API_BASE_META.sub("", source)
    tags = "".join(f'\n  <meta name="adlife-api-base" content="{value}">' for value in values)
    return source.replace('<meta charset="utf-8">', f'<meta charset="utf-8">{tags}', 1)


def _artifact_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@contextmanager
def _serve(application: object) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("city browser test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("city browser test server did not stop")


@contextmanager
def _place_viewer() -> Iterator[str]:
    simulation = CityMobility(
        load_pack_v2(pack_v2_data()),
        seed=42,
        agent_count=2,
        days=7,
        places=mobility_place_set(),
    )
    application = create_city_app(
        simulation,
        run_id="browser-place-study",
        run_schema_version=3,
    )
    with _serve(application) as base_url:
        yield base_url


@contextmanager
def _spatial_viewer() -> Iterator[
    tuple[str, SpatialCampaignScenario, SpatialOpportunityEvaluation]
]:
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2),
        ],
    )
    scenario = scenario.model_copy(
        update={"name": "پویش آزمایشی شهر </script><img src=x onerror=alert(1)>"}
    )
    evaluation = _evaluate(simulation, scenario)
    application = create_city_app(
        simulation,
        run_id="browser-spatial-study",
        run_schema_version=4,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    with _serve(application) as base_url:
        yield base_url, scenario, evaluation


@contextmanager
def _attention_viewer() -> Iterator[
    tuple[
        str,
        SpatialCampaignScenario,
        SpatialOpportunityEvaluation,
        SpatialAttentionEvaluation,
        SpatialMetrics,
    ]
]:
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [
            _billboard(),
            _phone(windows=[{"start_minute": 0, "end_minute": 2}], cap=2),
        ],
    )
    scenario = scenario.model_copy(
        update={"name": "پویش آزمایشی شهر </script><img src=x onerror=alert(1)>"}
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        agents_sha256="a" * 64,
        trace_sha256="b" * 64,
        days=simulation.days,
    )
    application = create_city_app(
        simulation,
        run_id="browser-attention-study",
        run_schema_version=5,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
    )
    with _serve(application) as base_url:
        yield base_url, scenario, opportunities, attention, metrics


@contextmanager
def _response_viewer(
    *, run_schema_version: int = 6
) -> Iterator[
    tuple[
        str,
        SpatialCampaignScenario,
        SpatialOpportunityEvaluation,
        SpatialAttentionEvaluation,
        SpatialResponseEvaluation,
        SpatialResponseMetrics,
    ]
]:
    pack = load_pack(pack_data())
    simulation = CityMobility(pack, seed=42, agent_count=30, days=1)
    scenario = _scenario(
        pack,
        [
            _phone(
                placement_id=f"phone-{index}",
                campaign_id=f"campaign-{index}",
                windows=[{"start_minute": 0, "end_minute": 1}],
                cap=1,
            )
            for index in range(1, 6)
        ]
        + [
            _phone(
                placement_id="phone-6",
                campaign_id="campaign-6",
                probability=0.0,
                windows=[{"start_minute": 0, "end_minute": 1}],
                cap=1,
            )
        ],
    )
    hostile_name = "پویش پاسخ </script><img src=x onerror=alert(1)>"
    scenario = scenario.model_copy(
        update={
            "name": hostile_name,
            "campaigns": tuple(
                campaign.model_copy(update={"name": f"{hostile_name} {index}"})
                for index, campaign in enumerate(scenario.campaigns, start=1)
            ),
        }
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    agent_ids = tuple(agent.agent_id for agent in simulation.agents)
    response_input = _response_input(scenario, agent_ids=agent_ids)
    response_evaluation = evaluate_spatial_responses(
        response_input,
        scenario,
        opportunities,
        attention,
        agent_ids=agent_ids,
    )
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=agent_ids,
        agents_sha256="e" * 64,
        trace_sha256="f" * 64,
        days=simulation.days,
        source_run_schema_version=run_schema_version,
    )
    response_metrics = derive_spatial_response_metrics(
        response_input,
        response_evaluation,
        scenario=scenario,
        opportunities=opportunities,
        attention=attention,
        attention_metrics=metrics,
        agent_ids=agent_ids,
    )
    application = create_city_app(
        simulation,
        run_id="browser-response-study",
        run_schema_version=run_schema_version,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
        response_input=response_input,
        response_evaluation=response_evaluation,
        spatial_response_metrics=response_metrics,
    )
    with _serve(application) as base_url:
        yield (
            base_url,
            scenario,
            opportunities,
            attention,
            response_evaluation,
            response_metrics,
        )


@contextmanager
def _workbench_assumptions_viewer(
    tmp_path: Path,
    *,
    schema_version: int = 7,
    distinct_names: bool = False,
) -> Iterator[tuple[str, Path, dict[str, object]]]:
    root = tmp_path / "workbench"
    workspace = prepare_workbench_workspace(root)
    hostile_prefix = "پویش آزمایشی <img src=x onerror=alert(1)> "
    hostile_name = hostile_prefix + (chr(0x1F600) * (120 - len(hostile_prefix)))
    campaign_name = "پویش <img src=x onerror=alert(1)> / campaign"
    document = draft_data(phone=True, roadside=True)
    document["scenario"]["scenario_id"] = _MAXIMUM_SCENARIO_ID
    document["scenario"]["name"] = hostile_name
    document["scenario"]["campaign"].update(
        {
            "campaign_id": _MAXIMUM_CAMPAIGN_ID,
            "name": campaign_name,
        }
    )
    document["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    document["scenario"]["roadside"].update(
        {
            "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 480}],
            "road_id": "middle-west",
            "travel_direction": "backward",
        }
    )
    document["settings"].update(
        {
            "run_id": _MAXIMUM_RUN_ID,
            "agent_count": 1,
            "days": 1,
            "seed": 2**63 - 1,
        }
    )
    if schema_version == 8:
        document.pop("cohort")
        document["schema_version"] = 2
        document["settings"]["agent_count"] = 3
        first = persona_data(persona_id="persona-002", count=1)
        second = persona_data(persona_id="persona-001", count=2)
        for persona in (first, second):
            persona["display_name"] = "مینا <img src=x onerror=alert(1)>"
            persona["background"] = "زندگی خیالی <script>alert(1)</script>"
        first["occupation"] = "Fictional artist"
        second["occupation"] = "Fictional designer"
        if distinct_names:
            first["display_name"] = "کیان خیالی"
        document["population"] = {"personas": [first, second]}
        draft = WorkbenchRunDraftV2.model_validate_json(json.dumps(document, ensure_ascii=False))
    else:
        draft = WorkbenchRunDraft.model_validate_json(json.dumps(document, ensure_ascii=False))
    validated = construct_workbench_run(draft)
    settings = validated.workbench_input.draft.settings
    stored = create_city_run(
        validated.pack,
        root=root,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )
    application = create_city_workbench_app(workspace, csrf_token="browser-assumptions-token")
    expected = {
        "run_id": settings.run_id,
        "manifest_sha256": sha256((stored.directory / "run.json").read_bytes()).hexdigest(),
        "workbench_input_sha256": validated.workbench_input.fingerprint,
        "city_sha256": validated.pack.fingerprint,
        "scenario_sha256": validated.scenario.fingerprint,
        "creative_sha256": validated.workbench_input.creative_template.fingerprint,
        "hostile_name": hostile_name,
        "campaign_name": campaign_name,
    }
    with _serve(application) as base_url:
        yield base_url, root, expected


def test_viewer_default_document_uses_one_legacy_same_origin_api_base() -> None:
    browser_path = _installed_browser()
    api_requests: list[str] = []
    external_requests: list[str] = []
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def route_request(route: Route) -> None:
            url = route.request.url
            if url.startswith(f"{base_url}/api/"):
                api_requests.append(url)
                route.continue_()
            elif url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(url)
                route.abort()

        page.route("**/*", route_request)
        page.goto(base_url, wait_until="networkidle")

        api_base = page.locator('meta[name="adlife-api-base"]')
        assert api_base.count() == 1
        assert api_base.get_attribute("content") == "/api"
        assert api_requests
        assert not external_requests
        browser.close()


def test_viewer_uses_an_embedded_icon_without_a_missing_resource_error() -> None:
    browser_path = _installed_browser()
    console_errors: list[dict[str, object]] = []
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.on(
            "console",
            lambda message: (
                console_errors.append({"text": message.text, "location": message.location})
                if message.type == "error"
                else None
            ),
        )
        page.goto(base_url, wait_until="networkidle")
        page.wait_for_timeout(500)

        icon = page.locator('link[rel="icon"]')
        assert icon.count() == 1
        assert (icon.get_attribute("href") or "").startswith("data:image/svg+xml,")
        assert not any(
            str(error["location"].get("url", "")).endswith("/favicon.ico")
            for error in console_errors
        )
        browser.close()


def test_viewer_routes_every_scientific_request_through_the_run_api_base() -> None:
    browser_path = _installed_browser()
    nested_requests: list[str] = []
    legacy_requests: list[str] = []
    external_requests: list[str] = []
    page_errors: list[str] = []
    with _response_viewer() as viewer, sync_playwright() as playwright:
        base_url = viewer[0]
        html = _viewer_html_with_api_bases("/api/runs/nested-run")
        run_prefix = f"{base_url}/api/runs/nested-run"
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def route_request(route: Route) -> None:
            url = route.request.url
            if url in {base_url, f"{base_url}/"}:
                route.fulfill(status=200, content_type="text/html", body=html)
                return
            if url.startswith(f"{run_prefix}/"):
                suffix = url[len(run_prefix) :]
                nested_requests.append(suffix)
                upstream = route.fetch(url=f"{base_url}/api{suffix}")
                route.fulfill(response=upstream)
                return
            if url.startswith(f"{base_url}/api/"):
                legacy_requests.append(url)
                route.abort()
                return
            if url.startswith(base_url):
                route.continue_()
                return
            external_requests.append(url)
            route.abort()

        page.route("**/*", route_request)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        expect(page.locator("#response-panel")).to_be_visible()
        requested_paths = {item.partition("?")[0] for item in nested_requests}
        assert {
            "/meta",
            "/city",
            "/agents",
            "/opportunity-summary",
            "/attention-summary",
            "/response-summary",
            "/spatial-metrics",
            "/spatial-response-metrics",
            "/timeline",
        } <= requested_paths
        assert {
            "/opportunities",
            "/attention-events",
            "/response-events",
            "/response-state",
            "/frame",
        }.isdisjoint(requested_paths)
        assert not legacy_requests
        assert not external_requests
        assert not page_errors
        browser.close()


def test_viewer_refuses_hostile_api_base_metadata_before_any_api_request() -> None:
    browser_path = _installed_browser()
    cases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("missing", ()),
        ("duplicate", ("/api", "/api")),
        ("absolute", ("https://attacker.invalid/api",)),
        ("protocol-relative", ("//attacker.invalid/api",)),
        ("query", ("/api/runs/safe-run?redirect=https://attacker.invalid",)),
        ("fragment", ("/api/runs/safe-run#attacker",)),
        ("encoded", ("/api/runs/%73afe-run",)),
        ("traversal", ("/api/runs/../safe-run",)),
        ("backslash", (r"\api\runs\safe-run",)),
        ("uppercase", ("/api/runs/Safe-run",)),
        ("reserved", ("/api/runs/con",)),
        ("credential-shaped", (f"/api/runs/sk-{'a' * 24}",)),
    )
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )

        def route_handler(
            html_document: str,
            observed_api_requests: list[str],
            observed_external_requests: list[str],
        ):
            def handle(route: Route) -> None:
                url = route.request.url
                if url in {base_url, f"{base_url}/"}:
                    route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=html_document,
                    )
                elif url.startswith(f"{base_url}/api"):
                    observed_api_requests.append(url)
                    route.abort()
                elif url.startswith(base_url):
                    route.continue_()
                else:
                    observed_external_requests.append(url)
                    route.abort()

            return handle

        def page_error_handler(observed_errors: list[str]):
            def handle(error: object) -> None:
                observed_errors.append(str(error))

            return handle

        for label, values in cases:
            api_requests: list[str] = []
            external_requests: list[str] = []
            page_errors: list[str] = []
            html = _viewer_html_with_api_bases(*values)
            page = browser.new_page()
            page.route("**/*", route_handler(html, api_requests, external_requests))
            page.on("pageerror", page_error_handler(page_errors))
            page.goto(base_url, wait_until="networkidle")

            expect(page.locator("#error-banner"), message=label).to_contain_text(
                "Viewer API base is invalid"
            )
            assert not api_requests, label
            assert not external_requests, label
            assert not page_errors, label
            page.close()
        browser.close()


def test_schema_v7_inspector_renders_verified_frozen_assumptions_as_inert_text(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    input_requests: list[str] = []
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with (
        _workbench_assumptions_viewer(tmp_path) as (
            base_url,
            _root,
            expected,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        input_url = f"{base_url}/api/runs/{expected['run_id']}/workbench-input"

        def route_request(route: Route) -> None:
            url = route.request.url
            if url == input_url:
                input_requests.append(url)
                route.continue_()
            elif url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")

        expect(page.locator("#assumptions-panel")).to_be_visible()
        expect(page.locator("#assumptions-drawer")).not_to_have_attribute("open", "")
        expect(page.locator("#assumptions-status")).to_have_text("Verified")
        expect(page.locator("#workbench-back-link")).to_be_visible()
        assert page.locator("#workbench-back-link").get_attribute("href") == "/"
        page.locator("#assumptions-drawer summary").click()
        expect(page.locator("#assumptions-drawer")).to_have_attribute("open", "")
        assert page.locator("#assumptions-run").inner_text() == _MAXIMUM_RUN_ID
        assert page.locator("#assumptions-seed").inner_text() == "9223372036854775807"
        assert page.locator("#assumptions-scenario").inner_text() == expected["hostile_name"]
        assert page.locator("#assumptions-scenario-id").inner_text() == _MAXIMUM_SCENARIO_ID
        assert page.locator("#assumptions-campaign").inner_text() == expected["campaign_name"]
        assert page.locator("#assumptions-campaign-id").inner_text() == _MAXIMUM_CAMPAIGN_ID
        assert page.locator("#assumptions-creative-template-id").inner_text() == (
            "fictional-device-launch-v1"
        )
        assert page.locator("#assumptions-creative-product").inner_text() == "Lumen Pocket"
        assert (
            "fictional compact device" in page.locator("#assumptions-creative-message").inner_text()
        )
        assert page.locator("#assumptions-target-interests").inner_text() == (
            "commuting · technology"
        )
        assert page.locator("#assumptions-cohort-interests").inner_text() == (
            "commuting · technology"
        )
        assert "PRICE SENSITIVITY 0.5" in page.locator("#assumptions-traits").inner_text()
        assert "PURCHASE INTENTION 0.2" in page.locator("#assumptions-initial-state").inner_text()
        placements = page.locator("#assumptions-placements").inner_text()
        assert "PHONE" in placements and "DAY 01 · 08:00\u201309:00" in placements
        assert all(token in placements for token in ("ROADSIDE", "MIDDLE-WEST", "BACKWARD"))
        for field in (
            "manifest_sha256",
            "workbench_input_sha256",
            "city_sha256",
            "scenario_sha256",
            "creative_sha256",
        ):
            assert page.locator(f"#assumptions-{field.replace('_', '-')}").inner_text() == str(
                expected[field]
            )
        assert (
            "homogeneous synthetic cohort"
            in page.locator("#assumptions-disclosure").inner_text().lower()
        )
        assert page.locator("#assumptions-panel bdi").count() >= 15
        assert page.locator("#assumptions-panel img").count() == 0
        assert page.locator("#assumptions-panel script").count() == 0
        assert input_requests == [input_url]
        assert external_requests == []
        assert console_errors == []
        assert page_errors == []
        browser.close()


def test_schema_v1_through_v6_inspector_never_requests_workbench_input() -> None:
    browser_path = _installed_browser()
    input_requests: list[str] = []
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        for version in range(1, 7):
            page = browser.new_page()

            def rewrite_schema(route: Route, *, schema_version: int = version) -> None:
                response = route.fetch()
                document = response.json()
                document["run_schema_version"] = schema_version
                document.pop("workbench_input_available", None)
                document.pop("workbench_input_sha256", None)
                route.fulfill(json=document)

            page.route("**/api/meta", rewrite_schema)
            page.route(
                "**/api/workbench-input",
                lambda route: (input_requests.append(route.request.url), route.abort()),
            )
            page.goto(base_url, wait_until="networkidle")

            expect(page.locator("#assumptions-panel"), message=f"schema v{version}").to_be_visible()
            expect(page.locator("#assumptions-status"), message=f"schema v{version}").to_have_text(
                f"UNAVAILABLE FOR SCHEMA V{version}"
            )
            expect(page.locator("#assumptions-content")).to_be_hidden()
            expect(page.locator("#workbench-back-link")).to_be_hidden()
            assert not page.locator("#error-banner").is_visible(), version
            page.close()
        assert input_requests == []
        browser.close()


def test_schema_v7_inspector_refuses_a_malformed_assumptions_projection(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    with (
        _workbench_assumptions_viewer(tmp_path) as (
            base_url,
            _root,
            _expected,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        input_url = f"{base_url}/api/runs/{_expected['run_id']}/workbench-input"

        def corrupt_input(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            document["settings"]["seed"] = "9223372036854775808"
            document["creative_template"]["message"] = "<img src=https://attacker.invalid/x>"
            route.fulfill(json=document)

        page.route(input_url, corrupt_input)
        page.goto(f"{base_url}/runs/{_expected['run_id']}", wait_until="networkidle")

        expect(page.locator("#assumptions-status")).to_have_text("VERIFICATION FAILED")
        expect(page.locator("#assumptions-content")).to_be_hidden()
        expect(page.locator("#error-banner")).to_contain_text("Frozen workbench input is invalid")
        assert page.locator("#assumptions-panel img").count() == 0
        assert "attacker.invalid" not in page.locator("body").inner_text()
        browser.close()


def test_schema_v7_inspector_cross_checks_assumption_identity_with_run_summary(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    with (
        _workbench_assumptions_viewer(tmp_path) as (
            base_url,
            _root,
            expected,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        for field in ("scenario_id", "campaign_id"):
            page = browser.new_page()

            def corrupt_summary(route: Route, *, changed_field: str = field) -> None:
                response = route.fetch()
                document = response.json()
                if changed_field == "scenario_id":
                    document["scenario_id"] = "different-scenario"
                else:
                    document["campaigns"][0]["campaign_id"] = "different-campaign"
                route.fulfill(json=document)

            page.route(
                f"{base_url}/api/runs/{expected['run_id']}/opportunity-summary",
                corrupt_summary,
            )
            page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")

            expect(page.locator("#assumptions-status"), message=field).to_have_text(
                "VERIFICATION FAILED"
            )
            expect(page.locator("#assumptions-content"), message=field).to_be_hidden()
            expect(page.locator("#error-banner"), message=field).to_contain_text(
                "Frozen workbench input is invalid"
            )
            page.close()
        browser.close()


def test_schema_v8_inspector_maps_verified_saved_identities_and_collapsed_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adlife.city import workbench_personas
    from adlife.city import workbench_web as inspector_web

    with (
        _workbench_assumptions_viewer(tmp_path, schema_version=8) as (base_url, root, expected),
        sync_playwright() as playwright,
    ):
        # A saved identity must never be reconstructed from a changed/absent catalog.
        def unavailable_catalog(**_kwargs: object) -> object:
            raise AssertionError("Saved inspection must not load persona templates")

        monkeypatch.setattr(
            workbench_personas, "load_persona_template_catalog", unavailable_catalog
        )
        monkeypatch.setattr(inspector_web, "load_persona_template_catalog", unavailable_catalog)
        before = _artifact_hashes(root)
        browser = playwright.chromium.launch(
            executable_path=str(_installed_browser()), headless=True
        )
        page = browser.new_page()
        requests: list[tuple[str, str]] = []
        page.on("request", lambda request: requests.append((request.method, request.url)))
        page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
        expect(page.locator("#assumptions-status")).to_have_text("Verified")
        name = "مینا <img src=x onerror=alert(1)>"
        expect(page.locator("#selected-name")).to_have_text(name)
        expect(page.locator("#identity-context")).not_to_have_attribute("open", "")
        expect(page.locator("#identity-context summary")).to_have_text(
            "Fictional context — does not affect simulation"
        )
        assert page.locator("#selected-name").get_attribute("dir") == "auto"
        assert page.locator("#selected-name").evaluate("el => el.closest('[aria-live]')") is None
        rows = page.locator("#people-list .person")
        assert rows.count() == 3
        for index, occupation in enumerate(
            ["Fictional designer", "Fictional designer", "Fictional artist"]
        ):
            agent_id = f"PERSON-{index + 1:03d}"
            expect(rows.nth(index).locator("strong")).to_have_text(agent_id)
            expect(rows.nth(index).locator("bdi")).to_have_text(name)
            rows.nth(index).click()
            expect(page.locator("#selected-id")).to_have_text(agent_id)
            expect(page.locator("#map-text-summary")).to_contain_text(agent_id)
            page.locator("#identity-context summary").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#identity-context")).to_have_attribute("open", "")
            expect(page.locator("#identity-occupation")).to_have_text(occupation)
            expect(page.locator("#identity-background")).to_have_text(
                "زندگی خیالی <script>alert(1)</script>"
            )
            expect(page.locator("#identity-age")).to_have_text("28")
            expect(page.locator("#identity-mbti")).to_have_text("INTJ")
            assert (
                page.locator("#identity-context summary").evaluate(
                    "el => getComputedStyle(el).outlineStyle"
                )
                != "none"
            )
            page.keyboard.press("Space")
        page.locator("#assumptions-drawer summary").click()
        assert "homogeneous" not in page.locator("#assumptions-disclosure").inner_text()
        expect(page.locator("#assumptions-cohort-title").locator("..")).to_be_hidden()
        assert (
            page.locator(
                "#identity-context img, #identity-context script, #people-list img"
            ).count()
            == 0
        )
        assert all(method == "GET" and url.startswith(base_url) for method, url in requests)
        assert not any("persona-templates" in url or "workbench.json" in url for _, url in requests)
        assert _artifact_hashes(root) == before
        browser.close()


def test_schema_v8_inspector_refuses_incomplete_or_misbound_identity_before_rendering(
    tmp_path: Path,
) -> None:
    with (
        _workbench_assumptions_viewer(tmp_path, schema_version=8) as (base_url, _root, expected),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(_installed_browser()), headless=True
        )
        mutations = [
            "missing-population",
            "missing-assignment",
            "duplicate-assignment",
            "extra-assignment",
            "swapped-assignment",
            "unknown-persona",
            "duplicate-persona",
            "wrong-count",
            "wrong-total",
            "crossed-input",
            "crossed-run",
            "wrong-agent-set",
            "duplicate-agent",
            "unknown-member",
            "false-fictional",
            "invalid-mbti",
            "wrong-age",
            "unsafe-control",
            "wrong-trait",
            "wrong-state",
            "missing-name",
            "oversize-persona",
        ]
        for mutation in mutations:
            page = browser.new_page()

            def corrupt(route: Route, *, case: str = mutation) -> None:
                document = route.fetch().json()
                population = document["population"]
                personas = population["personas"]
                assignments = population["assignments"]
                if case == "missing-population":
                    document.pop("population")
                elif case == "missing-assignment":
                    assignments.pop()
                elif case == "duplicate-assignment":
                    assignments[1] = assignments[0]
                elif case == "extra-assignment":
                    assignments.append({"agent_id": "person-004", "persona_id": "persona-001"})
                elif case == "swapped-assignment":
                    assignments[0]["persona_id"], assignments[2]["persona_id"] = (
                        assignments[2]["persona_id"],
                        assignments[0]["persona_id"],
                    )
                elif case == "unknown-persona":
                    assignments[0]["persona_id"] = "persona-999"
                elif case == "duplicate-persona":
                    personas[1]["persona_id"] = personas[0]["persona_id"]
                elif case == "wrong-count":
                    personas[0]["count"] = 1
                elif case == "wrong-total":
                    document["settings"]["agent_count"] = 2
                elif case == "crossed-input":
                    document["workbench_input_schema_version"] = 1
                elif case == "crossed-run":
                    document["run_schema_version"] = 7
                elif case == "unknown-member":
                    personas[0]["secret_sentinel"] = "must-not-reflect"
                elif case == "false-fictional":
                    personas[0]["fictional"] = 1
                elif case == "invalid-mbti":
                    personas[0]["mbti"] = "INVALID"
                elif case == "wrong-age":
                    personas[0]["age"] = True
                elif case == "unsafe-control":
                    personas[0]["display_name"] = "must-not-reflect\u202e"
                elif case == "wrong-trait":
                    personas[0]["traits"]["impulsivity"] = 2
                elif case == "wrong-state":
                    personas[0]["initial_state"]["brand_sentiment"] = -2
                elif case == "missing-name":
                    personas[0].pop("display_name")
                elif case == "oversize-persona":
                    personas[0]["background"] = "😀" * 800
                route.fulfill(json=document)

            if mutation in ("wrong-agent-set", "duplicate-agent"):

                def corrupt_agents(route: Route, *, case: str = mutation) -> None:
                    document = route.fetch().json()
                    document[2]["agent_id"] = (
                        "person-004" if case == "wrong-agent-set" else "person-001"
                    )
                    route.fulfill(json=document)

                page.route("**/agents", corrupt_agents)
            else:
                page.route("**/workbench-input", corrupt)
            page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
            expect(page.locator("#assumptions-status"), message=mutation).to_have_text(
                "VERIFICATION FAILED"
            )
            expect(page.locator("#error-banner"), message=mutation).to_contain_text(
                "Frozen workbench input is invalid"
            )
            expect(page.locator("#selected-name"), message=mutation).to_be_hidden()
            assert page.locator("#people-list").inner_text() == "", mutation
            assert "must-not-reflect" not in page.locator("body").inner_text(), mutation
            page.close()
        browser.close()


def test_schema_v8_identity_changes_only_with_the_committed_timeline_selection(
    tmp_path: Path,
) -> None:
    with (
        _workbench_assumptions_viewer(tmp_path, schema_version=8, distinct_names=True) as (
            base_url,
            _root,
            expected,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(_installed_browser()), headless=True
        )
        page = browser.new_page()
        page.add_init_script("""(() => {
          const original = window.fetch;
          window.identityPending = false;
          window.fetch = async (...args) => {
            if (String(args[0]).includes('/timeline?') && String(args[0]).includes('person-003')) {
              window.identityPending = true;
              const fail = await new Promise(resolve => { window.releaseIdentity = resolve; });
              if (fail) return new Response('{}', {status: 500});
            }
            return original(...args);
          };
        })();""")
        page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
        expect(page.locator("#selected-id")).to_have_text("PERSON-001")
        page.locator("#identity-context summary").click()

        def snapshot() -> object:
            return page.evaluate("""() => ({
              id: document.querySelector('#selected-id').textContent,
              name: document.querySelector('#selected-name').textContent,
              context: document.querySelector('#identity-context').textContent,
              activity: document.querySelector('#selected-activity').textContent,
              response: document.querySelector('#response-state-panel').textContent,
              map: document.querySelector('#city-map').toDataURL(),
              alternative: document.querySelector('#map-text-summary').textContent,
              selected: document.querySelector('.person.selected').textContent,
            })""")

        before = snapshot()
        page.locator(".person").nth(2).click()
        page.wait_for_function("window.identityPending")
        assert snapshot() == before
        page.evaluate("window.releaseIdentity(true)")
        expect(page.locator("#error-banner")).to_be_visible()
        assert snapshot() == before
        page.evaluate("window.identityPending = false")
        page.locator(".person").nth(2).click()
        page.wait_for_function("window.identityPending")
        assert snapshot() == before
        page.evaluate("window.releaseIdentity(false)")
        expect(page.locator("#selected-id")).to_have_text("PERSON-003")
        expect(page.locator("#selected-name")).to_have_text("کیان خیالی")
        expect(page.locator("#identity-occupation")).to_have_text("Fictional artist")
        expect(page.locator("#map-text-summary")).to_contain_text("PERSON-003")
        page.evaluate("window.identityPending = false")
        page.locator(".person").nth(2).click()
        page.wait_for_function("window.identityPending")
        page.locator(".person").nth(1).click()
        expect(page.locator("#selected-id")).to_have_text("PERSON-002")
        newest = snapshot()
        page.evaluate("window.releaseIdentity(false)")
        page.wait_for_timeout(100)
        assert snapshot() == newest
        browser.close()


def test_schema_v8_context_is_keyboard_accessible_and_contained_at_390px_200_percent(
    tmp_path: Path,
) -> None:
    with (
        _workbench_assumptions_viewer(tmp_path, schema_version=8) as (base_url, _root, expected),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(_installed_browser()), headless=True
        )
        page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
        page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
        expect(page.locator("#selected-name")).to_be_visible()
        original_font = page.locator("#selected-name").evaluate(
            "el => parseFloat(getComputedStyle(el).fontSize)"
        )
        page.evaluate("document.documentElement.style.fontSize = '200%'")
        assert (
            page.locator("#selected-name").evaluate(
                "el => parseFloat(getComputedStyle(el).fontSize)"
            )
            >= original_font * 2
        )
        summary = page.locator("#identity-context summary")
        summary.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#identity-background")).to_be_visible()
        expect(page.locator("#play-button")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for selector in ("#identity-context", "#selected-name", "#people-list"):
            assert page.locator(selector).evaluate("el => el.scrollWidth <= el.clientWidth"), (
                selector
            )
        assert (
            page.locator("#identity-context").evaluate("el => getComputedStyle(el).animationName")
            == "none"
        )
        browser.close()


def test_place_viewer_renders_provenance_and_scrubs_without_external_requests(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 960})

        def route_request(route: Route) -> None:
            if route.request.url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        assert (
            page.locator("#saved-run-label").inner_text() == "SAVED RUN / browser-place-study · V3"
        )
        assert page.locator("#model-label").inner_text() == "MOBILITY V3"
        assert page.locator(".place-key:visible").count() == 3
        assert page.locator("#home-place").inner_text() != "Generated node"
        assert page.locator("#work-place").inner_text() == "North Works"
        assert "operator authored fictional" in page.locator("#place-evidence-text").inner_text()
        assert "source derived" in page.locator("#place-evidence-text").inner_text()
        assert page.locator("#place-evidence").is_visible()
        assert not page.locator("#opportunity-panel").is_visible()
        assert len(page.locator("#city-map").evaluate("canvas => canvas.toDataURL()")) > 1_000

        page.locator("#time-slider").evaluate(
            "element => { element.value = '900'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#timeline-time")).to_have_text("DAY 01 · 15:00")

        page.locator("#people-list .person").nth(1).click()
        expect(page.locator("#selected-id")).to_have_text("PERSON-002")
        assert page.locator("#home-place").inner_text() != "Generated node"

        screenshot = tmp_path / "city-place-viewer.png"
        page.screenshot(path=screenshot, full_page=True)
        assert screenshot.stat().st_size > 20_000
        page.set_viewport_size({"width": 700, "height": 900})
        assert page.locator("#saved-run-label").is_visible()
        assert not external_requests
        assert not console_errors
        assert not page_errors
        browser.close()


def test_spatial_viewer_renders_minute_evidence_without_calling_it_an_impression(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with _spatial_viewer() as (base_url, scenario, evaluation), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 960})

        def route_request(route: Route) -> None:
            if route.request.url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("#saved-run-label").inner_text().endswith("V4")
        assert page.locator("#opportunity-panel").is_visible()
        assert page.locator("#opportunity-scenario").inner_text() == scenario.name
        assert page.locator("#opportunity-total").inner_text() == str(
            evaluation.counts.opportunity_count
        )
        assert page.locator("#opportunity-roadside").inner_text() == "1"
        assert page.locator("#opportunity-phone").inner_text() == "4"
        assert (
            page.locator("#opportunity-claim").inner_text()
            == "Modeled opportunity · not an impression"
        )
        assert page.locator("#opportunity-current").inner_text() == (
            "2 AT THIS MINUTE · 1 FOR SELECTED AGENT"
        )
        assert page.locator(".opportunity-card").count() == 2
        assert page.locator(".opportunity-card.selected").count() == 1
        assert page.locator(".opportunity-key:visible").count() == 2
        assert "PHONE" in page.locator(".opportunity-card").first.inner_text()

        page.locator("#time-slider").evaluate(
            "element => { element.value = '481'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#opportunity-current")).to_have_text(
            "1 AT THIS MINUTE · 0 FOR SELECTED AGENT"
        )
        assert page.locator(".opportunity-card").count() == 1
        roadside_card = page.locator(".opportunity-card")
        roadside = roadside_card.inner_text()
        assert "ROADSIDE BILLBOARD" in roadside
        assert "PERSON-002" in roadside
        assert "BILLBOARD-AB" in roadside
        roadside_card.click()
        expect(page.locator("#opportunity-current")).to_have_text(
            "1 AT THIS MINUTE · 1 FOR SELECTED AGENT"
        )
        assert roadside_card.evaluate("element => element.classList.contains('selected')")

        screenshot = tmp_path / "city-spatial-opportunity-viewer.png"
        page.screenshot(path=screenshot, full_page=True)
        assert screenshot.stat().st_size > 25_000
        page.set_viewport_size({"width": 700, "height": 900})
        assert page.locator("#opportunity-panel").is_visible()
        assert page.locator("#opportunity-claim").is_visible()
        assert not external_requests
        assert not console_errors
        assert not page_errors
        browser.close()


def test_attention_viewer_renders_causal_timeline_without_external_requests(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with (
        _attention_viewer() as (
            base_url,
            scenario,
            _opportunities,
            attention,
            metrics,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})

        def route_request(route: Route) -> None:
            if route.request.url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        minute_zero = tuple(event for event in attention.events if event.model_minute == 0)
        selected_zero = tuple(event for event in minute_zero if event.agent_id == "person-001")
        assert page.locator("#saved-run-label").inner_text().endswith("V5")
        assert page.locator("#attention-panel").is_visible()
        assert page.locator("#metrics-panel").is_visible()
        assert not page.locator("#response-metrics-panel").is_visible()
        assert page.locator("#metrics-panel").get_attribute("tabindex") == "0"
        assert page.locator("#metrics-claim").inner_text() == (
            "Modeled metrics \u00b7 not observed outcomes"
        )
        assert page.locator("#metrics-overall-opportunity-reach-value").inner_text() == "100%"
        assert page.locator("#metrics-overall-opportunity-reach-receipt").inner_text() == "2 / 2"
        assert page.locator("#metrics-overall-impression-frequency-value").inner_text() == (
            f"{metrics.overall.impression_frequency.value:g}"
        )
        assert "outputs/spatial-attention.jsonl" in (
            page.locator("#metrics-overall-notice-rate").get_attribute("title") or ""
        )
        page.locator("#metrics-panel").focus()
        assert page.locator("#metrics-panel").evaluate(
            "element => document.activeElement === element"
        )
        assert page.locator("#opportunity-scenario").inner_text() == scenario.name
        assert page.locator("#opportunity-scenario img").count() == 0
        metrics_screenshot = tmp_path / "city-spatial-metrics-viewer.png"
        page.screenshot(path=metrics_screenshot, full_page=True)
        assert metrics_screenshot.stat().st_size > 25_000
        assert page.locator("#attention-impressions").inner_text() == str(
            attention.counts.impression_count
        )
        assert page.locator("#attention-notices").inner_text() == str(
            attention.counts.noticed_count
        )
        assert page.locator("#attention-probability").inner_text() == "50%"
        assert (
            page.locator("#attention-claim").inner_text()
            == "Modeled attention \u00b7 not observed behavior"
        )
        assert page.locator("#attention-current").inner_text() == (
            f"{len(minute_zero)} EVENTS AT THIS MINUTE \u00b7 "
            f"{len(selected_zero)} FOR SELECTED AGENT"
        )
        assert page.locator(".attention-card").count() == len(minute_zero)
        assert page.locator(".attention-card.selected").count() == len(selected_zero)
        assert page.locator(".attention-key:visible").count() == 2
        assert page.locator("#attention-list").get_attribute("aria-live") is None
        assert page.locator("#attention-current").get_attribute("aria-live") is None
        assert "IMPRESSION" in page.locator(".attention-card").first.inner_text()
        assert "CAUSE" in page.locator(".attention-card").first.inner_text()

        page.locator("#time-slider").evaluate(
            "element => { element.value = '481'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        minute_481 = tuple(event for event in attention.events if event.model_minute == 481)
        expect(page.locator("#attention-current")).to_have_text(
            f"{len(minute_481)} EVENTS AT THIS MINUTE \u00b7 0 FOR SELECTED AGENT"
        )
        assert page.locator(".attention-card").count() == len(minute_481)
        page.locator(".attention-card").first.click()
        expect(page.locator("#selected-id")).to_have_text("PERSON-002")
        assert page.locator(".attention-card.selected").count() == len(minute_481)

        screenshot = tmp_path / "city-spatial-attention-viewer.png"
        page.screenshot(path=screenshot, full_page=True)
        assert screenshot.stat().st_size > 25_000
        page.set_viewport_size({"width": 700, "height": 950})
        assert page.locator("#attention-panel").is_visible()
        assert page.locator("#attention-claim").is_visible()
        assert page.locator("#metrics-panel").is_visible()
        assert not external_requests
        assert not console_errors
        assert not page_errors
        browser.close()


def test_metrics_viewer_renders_zero_denominators_without_nan_or_infinity(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    pack = load_pack(pack_data())
    simulation = _mobility(pack, agent_count=2)
    scenario = _scenario(
        pack,
        [_phone(windows=[{"start_minute": 0, "end_minute": 1}], probability=0.0)],
    )
    opportunities = _evaluate(simulation, scenario)
    attention = evaluate_spatial_attention(opportunities, seed=simulation.seed)
    metrics = derive_spatial_metrics(
        opportunities,
        attention,
        agent_ids=tuple(agent.agent_id for agent in simulation.agents),
        agents_sha256="c" * 64,
        trace_sha256="d" * 64,
        days=1,
    )
    application = create_city_app(
        simulation,
        run_id="zero-metrics-study",
        run_schema_version=5,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
    )

    with _serve(application) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 700, "height": 950})
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("#metrics-overall-impression-frequency-value").inner_text() == "0"
        assert page.locator("#metrics-overall-impression-frequency-receipt").inner_text() == (
            "0 / 0"
        )
        assert page.locator("#metrics-overall-notice-rate-value").inner_text() == "0%"
        assert page.locator("#metrics-overall-notice-rate-receipt").inner_text() == "0 / 0"
        assert "NaN" not in page.locator("#metrics-panel").inner_text()
        assert "Infinity" not in page.locator("#metrics-panel").inner_text()
        screenshot = tmp_path / "city-zero-metrics-viewer.png"
        page.screenshot(path=screenshot, full_page=True)
        assert screenshot.stat().st_size > 20_000
        browser.close()


def test_response_viewer_renders_safe_causal_rail_and_final_state_without_network(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_requests: list[str] = []
    response_state_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    with (
        _response_viewer() as (
            base_url,
            scenario,
            _,
            _,
            response_evaluation,
            response_metrics,
        ),
        sync_playwright() as playwright,
    ):
        response_before = response_evaluation.model_dump(mode="json")
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})

        def route_request(route: Route) -> None:
            if route.request.url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "request",
            lambda request: (
                response_state_requests.append(request.url)
                if "/api/timeline" in request.url
                else None
            ),
        )
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")

        minute_zero = tuple(
            record for record in response_evaluation.records if record.model_minute == 0
        )
        selected_zero = tuple(record for record in minute_zero if record.agent_id == "person-001")
        selected_states = tuple(
            state for state in response_evaluation.final_states if state.agent_id == "person-001"
        )
        assert len(minute_zero) == 144
        assert len(selected_states) == 6
        assert page.locator("#saved-run-label").inner_text().endswith("V6")
        assert page.locator("#response-panel").is_visible()
        assert page.locator("#response-scenario").inner_text() == scenario.name
        assert page.locator("#response-panel img").count() == 0
        assert page.locator("#response-claim").inner_text() == (
            "Modeled response · not observed behavior"
        )
        assert page.locator("#response-causal span").all_inner_texts() == [
            "NOTICE",
            "RULE RESPONSE",
            "STATE UPDATE",
        ]
        assert page.locator("#response-causal").get_attribute("aria-label") == (
            "Notice causes a rule response; responses are atomically committed as a state update"
        )
        assert page.locator("#response-responses").inner_text() == str(
            response_evaluation.counts.response_count
        )
        assert page.locator("#response-updates").inner_text() == str(
            response_evaluation.counts.state_update_count
        )
        assert page.locator("#response-campaigns").inner_text() == str(
            response_evaluation.counts.campaign_count
        )
        assert page.locator("#response-current").inner_text() == (
            f"{len(minute_zero)} RECORDS AT THIS MINUTE · {len(selected_zero)} FOR SELECTED AGENT"
        )
        assert page.locator("#response-list").get_attribute("aria-live") is None
        assert page.locator("#response-current").get_attribute("aria-live") is None
        assert page.locator(".response-card").count() == 100
        assert page.locator(".response-card.rule-response").count() > 0
        assert page.locator(".response-card.state-update").count() > 0
        assert "SENTIMENT Δ" in page.locator(".response-card.rule-response").first.inner_text()
        assert "RECALL Δ" in page.locator(".response-card.rule-response").first.inner_text()
        assert "INTENTION PROXY" in page.locator(".response-card.state-update").first.inner_text()
        assert page.locator("#response-page-note").is_visible()
        assert page.locator("#response-page-note").inner_text() == (
            f"Records 1-100 of {len(minute_zero)}"
        )

        assert page.locator("#response-state-panel").is_visible()
        assert page.locator("#response-state-panel").get_attribute("tabindex") == "0"
        assert page.locator("#response-state-time").inner_text() == "End of run"
        assert page.locator("#response-state-warning").inner_text() == (
            "Final state · not the selected minute"
        )

        assert page.locator("#response-metrics-panel").is_visible()
        assert page.locator("#response-metrics-panel").get_attribute("tabindex") == "0"
        assert page.locator("#response-metrics-claim").inner_text() == (
            "Modeled response metrics · not observed outcomes"
        )
        assert (
            "deterministic rule-processing records"
            in page.locator("#response-metrics-disclosure").inner_text()
        )
        assert "not purchase probability, transactions, sales, or a sales forecast" in (
            page.locator("#response-metrics-disclosure").inner_text().lower()
        )
        assert (
            "Committed state is not allocated by channel"
            in page.locator("#response-metrics-state-note").inner_text()
        )
        assert page.locator("#response-metrics-event-body tr").count() == 5
        assert page.locator("#response-metrics-state-body tr").count() == 3
        assert page.locator("#response-metrics-state-table th").filter(has_text="ROAD").count() == 0
        assert (
            page.locator("#response-metrics-state-table th").filter(has_text="PHONE").count() == 0
        )

        event_names = (
            "response_count",
            "response_reach",
            "response_frequency",
            "mean_rule_sentiment_delta",
            "mean_rule_recall_delta",
        )
        groups = (
            ("overall", response_metrics.overall),
            ("roadside", response_metrics.channels[0]),
            ("mobile", response_metrics.channels[1]),
        )
        for group_name, group in groups:
            for metric_name in event_names:
                receipt = getattr(group, metric_name)
                cell = page.locator(
                    f"#response-metrics-{group_name}-{metric_name.replace('_', '-')}"
                )
                assert float(cell.get_attribute("data-value") or "nan") == receipt.value
                numerator_text, denominator_text = cell.locator("small").inner_text().split("/")
                assert float(numerator_text) == pytest.approx(receipt.numerator, abs=0.00005)
                assert int(denominator_text) == receipt.denominator
                provenance = cell.get_attribute("aria-label") or ""
                assert "Numerator" in provenance
                assert "denominator" in provenance
                assert "outputs/spatial-responses.jsonl" in provenance
                assert "spatial.response" in provenance

        campaign_selector = page.locator("#response-metrics-campaign")
        assert campaign_selector.locator("option").count() == 7
        assert scenario.campaigns[0].name in campaign_selector.locator("option").nth(1).inner_text()
        assert page.locator("#response-metrics-panel img").count() == 0
        initial_overall_state = page.locator("#response-metrics-state-body").inner_text()
        overall_receipt = response_metrics.overall.purchase_intention_proxy
        assert (
            float(
                page.locator(
                    "#response-metrics-state-purchase-intention-proxy-initial"
                ).get_attribute("data-value")
                or "nan"
            )
            == overall_receipt.initial_mean
        )
        assert (
            float(
                page.locator(
                    "#response-metrics-state-purchase-intention-proxy-final"
                ).get_attribute("data-value")
                or "nan"
            )
            == overall_receipt.final_mean
        )
        assert (
            float(
                page.locator(
                    "#response-metrics-state-purchase-intention-proxy-change"
                ).get_attribute("data-value")
                or "nan"
            )
            == overall_receipt.mean_change
        )
        state_provenance = (
            page.locator("#response-metrics-state-purchase-intention-proxy-change").get_attribute(
                "aria-label"
            )
            or ""
        )
        assert "Denominator" in state_provenance
        assert "inputs/spatial-response.json" in state_provenance
        assert "outputs/response-state.json" in state_provenance
        assert "NaN" not in page.locator("#response-metrics-panel").inner_text()
        assert "Infinity" not in page.locator("#response-metrics-panel").inner_text()

        zero_campaign = next(
            campaign
            for campaign in response_metrics.campaigns
            if campaign.campaign_id == "campaign-6"
        )
        assert zero_campaign.response_count.value == 0.0
        for state_name in (
            "brand_sentiment",
            "recall_strength",
            "purchase_intention_proxy",
        ):
            assert getattr(zero_campaign, state_name).mean_change == 0.0
        campaign_selector.select_option("campaign-6")
        assert page.locator("#response-metrics-series-label").inner_text().endswith("CAMPAIGN-6")
        for state_name in (
            "brand-sentiment",
            "recall-strength",
            "purchase-intention-proxy",
        ):
            change = page.locator(f"#response-metrics-state-{state_name}-change")
            assert float(change.get_attribute("data-value") or "nan") == 0.0
            assert change.inner_text().startswith("+0")
        campaign_selector.select_option("overall")
        assert page.locator("#response-metrics-state-body").inner_text() == initial_overall_state

        first_response_metric = page.locator("#response-metrics-overall-response-count")
        first_response_metric.focus()
        assert first_response_metric.evaluate("element => document.activeElement === element")
        assert first_response_metric.evaluate(
            "element => getComputedStyle(element).outlineStyle"
        ) != ("none")
        campaign_selector.focus()
        assert campaign_selector.evaluate("element => document.activeElement === element")
        full_run_event_ledger = page.locator("#response-metrics-event-table").inner_text()
        assert page.locator("#response-state-claim").inner_text() == (
            "Uncalibrated synthetic scores · purchase intention is not purchase "
            "probability or sales"
        )
        assert page.locator("#response-state-list").get_attribute("aria-live") is None
        assert page.locator(".response-state-card").count() == len(selected_states)
        assert page.locator(".response-state-card").filter(has_text="0 RESPONSES").count() > 0
        assert not page.locator("#response-state-page-note").is_visible()

        person_two_attention = page.locator(".attention-card").filter(has_text="PERSON-002").first
        person_two_attention.click()
        expect(page.locator("#selected-id")).to_have_text("PERSON-002")
        expect(page.locator(".response-state-card").first).to_contain_text("PERSON-002")

        person_three_response = page.locator(".response-card").filter(has_text="PERSON-003").first
        person_three_response.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#selected-id")).to_have_text("PERSON-003")
        expect(page.locator(".response-state-card").first).to_contain_text("PERSON-003")
        causal_full_run_ledger = page.locator("#response-metrics-panel").inner_text()
        assert page.locator("#response-metrics-event-table").inner_text() == full_run_event_ledger

        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#response-current")).to_have_text(
            "0 RECORDS AT THIS MINUTE · 0 FOR SELECTED AGENT"
        )
        assert page.locator(".response-card").count() == 0
        assert page.locator("#response-empty").is_visible()
        assert page.locator(".response-state-card").count() == 6
        assert page.locator("#response-state-warning").inner_text() == (
            "Final state · not the selected minute"
        )
        assert page.locator("#response-metrics-panel").inner_text() == causal_full_run_ledger

        person_five = page.locator("#people-list .person").nth(4)
        person_five.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#selected-id")).to_have_text("PERSON-005")
        expect(page.locator(".response-state-card").first).to_contain_text("PERSON-005")
        assert page.locator(".response-state-card").count() == 6
        assert page.locator(".response-state-card").filter(has_text="0 RESPONSES").count() == 6
        assert any("agent_id=person-005" in url for url in response_state_requests)
        assert page.locator("#response-metrics-panel").inner_text() == causal_full_run_ledger

        page.locator("#response-state-panel").focus()
        assert page.locator("#response-state-panel").evaluate(
            "element => document.activeElement === element"
        )
        served_javascript = page.request.get(f"{base_url}/static/app.js").text()
        assert "innerHTML" not in served_javascript

        screenshot = tmp_path / "city-spatial-response-viewer.png"
        page.screenshot(path=screenshot, full_page=True)
        assert screenshot.stat().st_size > 30_000
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator("#response-panel").is_visible()
        assert page.locator("#response-state-panel").is_visible()
        assert page.locator("#response-metrics-panel").is_visible()
        assert page.locator("#metrics-panel").evaluate("element => element.tagName") == "DETAILS"
        assert (
            page.locator("#response-metrics-panel").evaluate("element => element.tagName")
            == "DETAILS"
        )
        assert page.locator("#metrics-panel").get_attribute("open") is None
        assert page.locator("#response-metrics-panel").get_attribute("open") is None
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        response_box = page.locator("#response-panel").bounding_box()
        assert response_box is not None and response_box["width"] <= 390
        response_metrics_box = page.locator("#response-metrics-panel").bounding_box()
        assert response_metrics_box is not None and response_metrics_box["width"] <= 390
        narrow_screenshot = tmp_path / "city-spatial-response-viewer-narrow.png"
        page.screenshot(path=narrow_screenshot, full_page=True)
        assert narrow_screenshot.stat().st_size > 25_000
        assert response_evaluation.model_dump(mode="json") == response_before
        assert not external_requests
        assert not console_errors
        assert not page_errors
        browser.close()


def test_schema_v7_response_viewer_accepts_truthful_metric_provenance() -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer(run_schema_version=7) as (
            base_url,
            _,
            _,
            _,
            _,
            _,
        ),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        expect(page.locator("#saved-run-label")).to_contain_text("V7")
        expect(page.locator("#response-metrics-panel")).to_be_visible()
        expect(page.locator("#error-banner")).to_be_hidden()
        browser.close()


def test_saved_run_viewer_pages_every_busy_minute_evidence_record_in_canonical_order() -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer() as (
            base_url,
            _,
            opportunities,
            attention,
            responses,
            _,
        ),
        sync_playwright() as playwright,
    ):
        expected_opportunity_ids = [
            item.opportunity_id for item in opportunities.opportunities if item.model_minute == 0
        ]
        expected_attention_ids = [
            item.event_id for item in attention.events if item.model_minute == 0
        ]
        expected_response_ids = [
            item.event_id for item in responses.records if item.model_minute == 0
        ]
        assert [
            len(expected_opportunity_ids),
            len(expected_attention_ids),
            len(expected_response_ids),
        ] == [150, 222, 144]
        source_before = (
            opportunities.model_dump(mode="json"),
            attention.model_dump(mode="json"),
            responses.model_dump(mode="json"),
        )

        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        external_requests: list[str] = []

        def route_request(route: Route) -> None:
            if route.request.url.startswith(base_url):
                route.continue_()
            else:
                external_requests.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.goto(base_url, wait_until="networkidle")

        def rendered_ids(kind: str) -> list[str]:
            return page.locator(f".{kind}-card").evaluate_all(
                "cards => cards.map(card => card.dataset.evidenceId)"
            )

        assert page.locator("#opportunity-page-note").inner_text() == "Records 1-100 of 150"
        assert page.locator("#opportunity-page-previous").is_disabled()
        assert page.locator("#opportunity-page-next").is_enabled()
        assert rendered_ids("opportunity") == expected_opportunity_ids[:100]
        opportunity_ids = rendered_ids("opportunity")

        page.locator("#opportunity-page-next").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 101-150 of 150")
        assert page.locator("#opportunity-page-previous").is_enabled()
        assert page.locator("#opportunity-page-next").is_disabled()
        opportunity_ids.extend(rendered_ids("opportunity"))
        assert opportunity_ids == expected_opportunity_ids
        assert len(set(opportunity_ids)) == 150

        page.locator("#opportunity-page-previous").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 1-100 of 150")
        assert rendered_ids("opportunity") == expected_opportunity_ids[:100]

        assert page.locator("#attention-page-note").inner_text() == "Records 1-100 of 222"
        attention_ids = rendered_ids("attention")
        assert attention_ids == expected_attention_ids[:100]
        page.locator("#attention-page-next").click()
        expect(page.locator("#attention-page-note")).to_have_text("Records 101-200 of 222")
        attention_ids.extend(rendered_ids("attention"))
        page.locator("#attention-page-next").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#attention-page-note")).to_have_text("Records 201-222 of 222")
        assert page.locator("#attention-page-next").is_disabled()
        attention_ids.extend(rendered_ids("attention"))
        assert attention_ids == expected_attention_ids
        assert len(set(attention_ids)) == 222

        assert page.locator("#response-page-note").inner_text() == "Records 1-100 of 144"
        response_ids = rendered_ids("response")
        assert response_ids == expected_response_ids[:100]
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        assert page.locator("#attention-page-note").inner_text() == "Records 201-222 of 222"
        assert page.locator("#response-page-previous").is_enabled()
        assert page.locator("#response-page-next").is_disabled()
        response_ids.extend(rendered_ids("response"))
        assert response_ids == expected_response_ids
        assert len(set(response_ids)) == 144

        page.locator(".response-card").first.click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        assert rendered_ids("response") == expected_response_ids[100:]

        page.locator("#opportunity-page-next").click()
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 101-150 of 150")
        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#response-current")).to_have_text(
            "0 RECORDS AT THIS MINUTE · 0 FOR SELECTED AGENT"
        )
        assert page.locator(".opportunity-card").count() == 0
        assert page.locator(".attention-card").count() == 0
        assert page.locator(".response-card").count() == 0
        assert page.locator("#opportunity-pagination").is_hidden()
        assert page.locator("#attention-pagination").is_hidden()
        assert page.locator("#response-pagination").is_hidden()

        page.locator("#time-slider").evaluate(
            "element => { element.value = '0'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 1-100 of 150")
        expect(page.locator("#attention-page-note")).to_have_text("Records 1-100 of 222")
        expect(page.locator("#response-page-note")).to_have_text("Records 1-100 of 144")
        assert rendered_ids("opportunity") == expected_opportunity_ids[:100]
        assert rendered_ids("attention") == expected_attention_ids[:100]
        assert rendered_ids("response") == expected_response_ids[:100]
        assert not external_requests
        browser.close()

    assert source_before == (
        opportunities.model_dump(mode="json"),
        attention.model_dump(mode="json"),
        responses.model_dump(mode="json"),
    )


def test_delayed_evidence_page_cannot_overwrite_a_newer_timeline_minute() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, responses, _), sync_playwright() as playwright:
        source_before = responses.model_dump(mode="json")
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            r"""() => {
              const originalFetch = window.fetch.bind(window);
              document.documentElement.dataset.delayedPageStarted = 'false';
              document.documentElement.dataset.delayedPageSettled = 'false';
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (
                  document.documentElement.dataset.delayedPageStarted === 'false'
                  && url.includes('/api/response-events?minute=0&offset=100&limit=100')
                ) {
                  document.documentElement.dataset.delayedPageStarted = 'true';
                  return new Promise((resolve, reject) => {
                    window.releaseDelayedEvidencePage = () => {
                      originalFetch(...args).then(resolve, reject).finally(() => {
                        document.documentElement.dataset.delayedPageSettled = 'true';
                      });
                    };
                  });
                }
                return originalFetch(...args);
              };
            }"""
        )

        page.locator("#response-page-next").click()
        expect(page.locator("html")).to_have_attribute("data-delayed-page-started", "true")
        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#response-current")).to_have_text(
            "0 RECORDS AT THIS MINUTE · 0 FOR SELECTED AGENT"
        )
        page.evaluate("window.releaseDelayedEvidencePage()")
        expect(page.locator("html")).to_have_attribute("data-delayed-page-settled", "true")
        page.wait_for_timeout(100)

        assert page.locator("#time-slider").input_value() == "1"
        assert page.locator(".response-card").count() == 0
        assert page.locator("#response-pagination").is_hidden()
        assert page.locator("#error-banner").is_hidden()
        assert responses.model_dump(mode="json") == source_before
        browser.close()


def test_delayed_evidence_page_cannot_overwrite_a_newer_same_rail_request() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, responses, _), sync_playwright() as playwright:
        expected_ids = [
            record.event_id for record in responses.records if record.model_minute == 0
        ][:100]
        source_before = responses.model_dump(mode="json")
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            """() => {
              const originalFetch = window.fetch.bind(window);
              document.documentElement.dataset.delayedPageStarted = 'false';
              document.documentElement.dataset.delayedPageSettled = 'false';
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (
                  document.documentElement.dataset.delayedPageStarted === 'false'
                  && url.includes('/api/response-events?minute=0&offset=100&limit=100')
                ) {
                  document.documentElement.dataset.delayedPageStarted = 'true';
                  return new Promise((resolve, reject) => {
                    window.releaseDelayedEvidencePage = () => {
                      originalFetch(...args).then(resolve, reject).finally(() => {
                        document.documentElement.dataset.delayedPageSettled = 'true';
                      });
                    };
                  });
                }
                return originalFetch(...args);
              };
            }"""
        )

        page.locator("#response-page-next").click()
        expect(page.locator("html")).to_have_attribute("data-delayed-page-started", "true")
        page.evaluate("loadEvidencePage('response', 0)")
        expect(page.locator("#response-page-note")).to_have_text("Records 1-100 of 144")
        page.evaluate("window.releaseDelayedEvidencePage()")
        expect(page.locator("html")).to_have_attribute("data-delayed-page-settled", "true")
        page.wait_for_timeout(100)

        assert page.locator("#response-page-note").inner_text() == "Records 1-100 of 144"
        assert (
            page.locator(".response-card").evaluate_all(
                "cards => cards.map(card => card.dataset.evidenceId)"
            )
            == expected_ids
        )
        assert page.locator("#response-page-previous").is_disabled()
        assert page.locator("#response-page-next").is_enabled()
        assert page.locator("#error-banner").is_hidden()
        assert responses.model_dump(mode="json") == source_before
        browser.close()


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("offset", "Response evidence page is invalid"),
        ("next_offset", "Response evidence page is invalid"),
        (
            "http",
            "Could not load /api/response-events?minute=0&offset=0&limit=100 (503)",
        ),
    ],
)
def test_malformed_evidence_page_is_refused_without_replacing_the_last_committed_page(
    corruption: str,
    expected_error: str,
) -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, responses, _), sync_playwright() as playwright:
        expected_ids = [
            record.event_id for record in responses.records if record.model_minute == 0
        ][100:]
        source_before = responses.model_dump(mode="json")
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")

        def corrupt_page(route: Route) -> None:
            if corruption == "http":
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"temporarily unavailable"}',
                )
                return
            response = route.fetch()
            document = response.json()
            if corruption == "offset":
                document["offset"] = 1
            else:
                document["next_offset"] = 99
            route.fulfill(response=response, json=document)

        page.route(
            "**/api/response-events?minute=0&offset=0&limit=100",
            corrupt_page,
        )
        page.locator("#response-page-previous").click()
        expect(page.locator("#error-banner")).to_be_visible()
        expect(page.locator("#error-banner")).to_contain_text(expected_error)

        assert page.locator("#response-page-note").inner_text() == "Records 101-144 of 144"
        assert (
            page.locator(".response-card").evaluate_all(
                "cards => cards.map(card => card.dataset.evidenceId)"
            )
            == expected_ids
        )
        assert page.locator("#response-page-previous").is_enabled()
        assert page.locator("#response-page-next").is_disabled()
        assert responses.model_dump(mode="json") == source_before
        browser.close()


@pytest.mark.parametrize(
    ("kind", "corruption"),
    [
        ("opportunity", "null-agent"),
        ("opportunity", "extra-record-field"),
        ("opportunity", "unknown-campaign"),
        ("opportunity", "invalid-hash"),
        ("attention", "wrong-city-hash"),
        ("opportunity", "incoherent-time"),
        ("attention", "invalid-number"),
        ("attention", "invalid-event-shape"),
        ("response", "invalid-nested-state"),
        ("opportunity", "channel-count-keys"),
        ("attention", "event-count-sum"),
        ("attention", "unknown-agent-count"),
        ("response", "response-channel-sum"),
    ],
)
def test_evidence_page_refuses_corrupt_records_and_aggregate_counts_transactionally(
    kind: str,
    corruption: str,
) -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator(f"#{kind}-page-next").click()
        expect(page.locator(f"#{kind}-page-note")).to_contain_text("Records 101-")
        committed_note = page.locator(f"#{kind}-page-note").inner_text()
        committed_ids = page.locator(f".{kind}-card").evaluate_all(
            "cards => cards.map(card => card.dataset.evidenceId)"
        )

        endpoint = {
            "opportunity": "opportunities",
            "attention": "attention-events",
            "response": "response-events",
        }[kind]

        def corrupt_page(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            item = document["items"][0]
            if corruption == "null-agent":
                item["agent_id"] = None
            elif corruption == "extra-record-field":
                item["credential"] = "sk-live-abcdef1234567890"
            elif corruption == "unknown-campaign":
                item["campaign_id"] = "unknown-campaign"
            elif corruption == "invalid-hash":
                item["scenario_sha256"] = "not-a-sha256"
            elif corruption == "wrong-city-hash":
                item["city_sha256"] = "0" * 64
            elif corruption == "incoherent-time":
                item["day_index"] = 1
            elif corruption == "invalid-number":
                item["notice_draw"] = None
            elif corruption == "invalid-event-shape":
                item["noticed"] = "yes"
            elif corruption == "invalid-nested-state":
                state_update = next(
                    record
                    for record in document["items"]
                    if record["event_type"] == "spatial.state-updated"
                )
                state_update["previous_state"]["agent_id"] = None
            elif corruption == "channel-count-keys":
                document["channel_counts"]["telepathy"] = 0
            elif corruption == "event-count-sum":
                document["event_type_counts"]["spatial.impression"] += 1
            elif corruption == "unknown-agent-count":
                document["agent_counts"]["person-999"] = 0
            elif corruption == "response-channel-sum":
                document["channel_counts"]["roadside-billboard"] += 1
            else:  # pragma: no cover - parameter table owns the cases
                raise AssertionError(corruption)
            route.fulfill(response=response, json=document)

        page.route(
            f"**/api/{endpoint}?minute=0&offset=0&limit=100",
            corrupt_page,
        )
        page.locator(f"#{kind}-page-previous").click()
        expect(page.locator("#error-banner")).to_be_visible()
        expect(page.locator("#error-banner")).to_contain_text(
            f"{kind.capitalize()} evidence page is invalid"
        )
        assert page.locator(f"#{kind}-page-note").inner_text() == committed_note
        assert (
            page.locator(f".{kind}-card").evaluate_all(
                "cards => cards.map(card => card.dataset.evidenceId)"
            )
            == committed_ids
        )
        assert (
            page.evaluate(
                "kind => state[evidencePageContracts[kind].pageKey].offset",
                kind,
            )
            == 100
        )
        browser.close()


def test_evidence_page_accepts_the_largest_binary64_value_below_one() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        def use_open_upper_bound(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            impression = next(
                item for item in document["items"] if item["event_type"] == "spatial.impression"
            )
            impression["notice_draw"] = math.nextafter(1.0, 0.0)
            impression["noticed"] = False
            route.fulfill(response=response, json=document)

        page.route(
            "**/api/attention-events?minute=0&offset=100&limit=100",
            use_open_upper_bound,
        )
        page.locator("#attention-page-next").click()
        expect(page.locator("#attention-page-note")).to_have_text("Records 101-200 of 222")
        expect(page.locator("#error-banner")).to_be_hidden()
        browser.close()


def test_evidence_page_render_failure_preserves_the_last_committed_page() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        committed_ids = page.locator(".response-card").evaluate_all(
            "cards => cards.map(card => card.dataset.evidenceId)"
        )
        page.evaluate(
            """() => {
              window.savedRenderMap = renderMap;
              renderMap = () => { throw new Error('injected render failure'); };
            }"""
        )

        page.locator("#response-page-previous").click()
        expect(page.locator("#error-banner")).to_contain_text("injected render failure")
        assert page.locator("#response-page-note").inner_text() == "Records 101-144 of 144"
        assert (
            page.locator(".response-card").evaluate_all(
                "cards => cards.map(card => card.dataset.evidenceId)"
            )
            == committed_ids
        )
        assert page.locator("#response-page-previous").is_enabled()
        assert page.locator("#response-page-next").is_disabled()
        assert page.evaluate("state.responsePage.offset") == 100
        browser.close()


def test_successful_evidence_retry_clears_only_its_own_current_error() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        pattern = "**/api/response-events?minute=0&offset=0&limit=100"

        def fail_page(route: Route) -> None:
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"temporarily unavailable"}',
            )

        page.route(pattern, fail_page)
        page.locator("#response-page-previous").click()
        expect(page.locator("#error-banner")).to_contain_text("Could not load")
        page.unroute(pattern, fail_page)

        page.locator("#response-page-previous").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 1-100 of 144")
        expect(page.locator("#error-banner")).to_be_hidden()

        page.evaluate("showError('unrelated boot integrity failure')")
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        expect(page.locator("#error-banner")).to_have_text("unrelated boot integrity failure")
        expect(page.locator("#error-banner")).to_be_visible()
        browser.close()


def test_failed_coordinated_refresh_keeps_the_last_committed_selection_and_minute() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, responses, _), sync_playwright() as playwright:
        source_before = responses.model_dump(mode="json")
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def fail_candidate_frame(route: Route) -> None:
            url = route.request.url
            if "agent_id=person-002" in url or "minute=1" in url:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"detail":"temporarily unavailable"}',
                )
                return
            route.continue_()

        page.route("**/api/timeline?*", fail_candidate_frame)
        page.goto(base_url, wait_until="networkidle")

        def committed_snapshot() -> dict[str, object]:
            return page.evaluate(
                """() => ({
                  selected: state.selected,
                  selectedLabel: document.getElementById('selected-id').textContent,
                  selectedPeople: Array.from(document.querySelectorAll('.person.selected'))
                    .map(item => item.textContent),
                  minute: state.minute,
                  slider: document.getElementById('time-slider').value,
                  opportunityNote: document.getElementById('opportunity-page-note').textContent,
                  opportunityIds: Array.from(document.querySelectorAll('.opportunity-card'))
                    .map(item => item.dataset.evidenceId),
                  attentionNote: document.getElementById('attention-page-note').textContent,
                  attentionIds: Array.from(document.querySelectorAll('.attention-card'))
                    .map(item => item.dataset.evidenceId),
                  responseNote: document.getElementById('response-page-note').textContent,
                  responseIds: Array.from(document.querySelectorAll('.response-card'))
                    .map(item => item.dataset.evidenceId),
                })"""
            )

        committed = committed_snapshot()
        assert committed["selected"] == "person-001"
        selection_sources = [
            page.locator("#people-list .person").nth(1),
            page.locator(".opportunity-card").filter(has_text="PERSON-002").first,
            page.locator(".attention-card").filter(has_text="PERSON-002").first,
            page.locator(".response-card").filter(has_text="PERSON-002").first,
        ]

        for source in selection_sources:
            expect(source).to_be_visible()
            source.click()
            page.wait_for_function("() => state.timelineLoading === false")
            expect(page.locator("#error-banner")).to_contain_text("Could not load /api/timeline")
            error_box = page.locator("#error-banner").bounding_box()
            timeline_box = page.locator(".timeline").bounding_box()
            assert error_box is not None and timeline_box is not None
            assert error_box["y"] + error_box["height"] <= timeline_box["y"] - 8
            assert committed_snapshot() == committed

        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#error-banner")).to_contain_text("Could not load /api/timeline")
        assert committed_snapshot() == committed

        page.unroute("**/api/timeline?*", fail_candidate_frame)
        page.evaluate(
            """() => {
              window.savedTimelineRenderMap = renderMap;
              renderMap = () => { throw new Error('injected timeline render failure'); };
            }"""
        )
        page.locator("#people-list .person").nth(1).click()
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#error-banner")).to_contain_text("injected timeline render failure")
        assert committed_snapshot() == committed
        assert responses.model_dump(mode="json") == source_before
        browser.close()


def test_mobile_error_banner_stays_above_visible_timeline_after_scroll_and_resize() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")

        page.route(
            "**/api/timeline?*",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"temporarily unavailable"}',
            ),
        )
        page.locator(".timeline").evaluate("element => element.scrollIntoView({ block: 'end' })")
        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#error-banner")).to_contain_text("Could not load /api/timeline")

        def assert_banner_clears_timeline() -> None:
            geometry = page.evaluate(
                """() => {
                  const banner = document.getElementById('error-banner').getBoundingClientRect();
                  const timeline = document.querySelector('.timeline').getBoundingClientRect();
                  return {
                    bannerBottom: banner.bottom,
                    timelineTop: timeline.top,
                    timelineBottom: timeline.bottom,
                    viewportHeight: window.innerHeight,
                  };
                }"""
            )
            assert geometry["timelineTop"] < geometry["viewportHeight"], geometry
            assert geometry["timelineBottom"] > 0, geometry
            assert geometry["bannerBottom"] <= geometry["timelineTop"] - 8, geometry

        assert_banner_clears_timeline()
        page.set_viewport_size({"width": 390, "height": 720})
        page.locator(".timeline").evaluate("element => element.scrollIntoView({ block: 'end' })")
        page.wait_for_function(
            """() => {
              const banner = document.getElementById('error-banner').getBoundingClientRect();
              const timeline = document.querySelector('.timeline').getBoundingClientRect();
              return banner.bottom <= timeline.top - 8;
            }"""
        )
        assert_banner_clears_timeline()
        browser.close()


def test_manual_scrub_stops_playback_and_ignores_its_superseded_failure() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            r"""() => {
              const originalFetch = window.fetch.bind(window);
              document.documentElement.dataset.staleFailureStarted = 'false';
              document.documentElement.dataset.staleFailureSettled = 'false';
              document.documentElement.dataset.newerFrameCompleted = 'false';
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (
                  document.documentElement.dataset.staleFailureStarted === 'false'
                  && /\/api\/timeline\?minute=(?!0(?:&|$))/.test(url)
                ) {
                  document.documentElement.dataset.staleFailureStarted = 'true';
                  return new Promise((_, reject) => setTimeout(() => {
                    document.documentElement.dataset.staleFailureSettled = 'true';
                    reject(new Error('superseded request failed'));
                  }, 500));
                }
                const result = originalFetch(...args);
                if (url.includes('/api/timeline?minute=2')) {
                  return result.then((response) => {
                    document.documentElement.dataset.newerFrameCompleted = 'true';
                    return response;
                  });
                }
                return result;
              };
            }"""
        )

        page.locator("#play-button").click()
        expect(page.locator("html")).to_have_attribute(
            "data-stale-failure-started", "true", timeout=3_000
        )
        page.locator("#time-slider").evaluate(
            "element => { element.value = '2'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("html")).to_have_attribute(
            "data-newer-frame-completed", "true", timeout=3_000
        )
        expect(page.locator("html")).to_have_attribute(
            "data-stale-failure-settled", "true", timeout=3_000
        )
        page.wait_for_timeout(100)

        snapshot = page.evaluate(
            """() => ({
              playLabel: document.getElementById('play-button').getAttribute('aria-label'),
              errorHidden: document.getElementById('error-banner').hidden,
              errorText: document.getElementById('error-banner').textContent,
              committedMinute: document.getElementById('time-slider').value,
            })"""
        )
        browser.close()

    assert snapshot["playLabel"] == "Play timeline"
    assert snapshot["errorHidden"] is True
    assert "superseded request failed" not in snapshot["errorText"]
    assert snapshot["committedMinute"] == "2"


def test_full_response_transitions_use_one_combined_timeline_read_with_page_offsets() -> None:
    browser_path = _installed_browser()
    timeline_reads: list[str] = []
    legacy_timeline_reads: list[str] = []
    legacy_paths = {
        "/api/frame",
        "/api/opportunities",
        "/api/attention-events",
        "/api/response-events",
        "/api/response-state",
    }
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def observe(request: PlaywrightRequest) -> None:
            url = request.url
            path = urlparse(url).path
            if path == "/api/timeline":
                timeline_reads.append(url)
            elif path in legacy_paths:
                legacy_timeline_reads.append(url)

        page.on("request", observe)
        page.goto(base_url, wait_until="networkidle")
        expect(page.locator("#response-panel")).to_be_visible()

        assert len(timeline_reads) == 1
        assert not legacy_timeline_reads
        assert parse_qs(urlparse(timeline_reads[0]).query) == {
            "minute": ["0"],
            "agent_id": ["person-001"],
            "opportunity_offset": ["0"],
            "attention_offset": ["0"],
            "response_offset": ["0"],
            "response_state_offset": ["0"],
            "limit": ["100"],
        }

        page.locator("#opportunity-page-next").click()
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 101-150 of 150")
        page.locator("#attention-page-next").click()
        expect(page.locator("#attention-page-note")).to_have_text("Records 101-200 of 222")
        page.locator("#attention-page-next").click()
        expect(page.locator("#attention-page-note")).to_have_text("Records 201-222 of 222")
        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")

        timeline_reads.clear()
        legacy_timeline_reads.clear()
        page.locator("#people-list .person").nth(1).click()
        expect(page.locator("#selected-id")).to_have_text("PERSON-002")

        assert len(timeline_reads) == 1
        assert not legacy_timeline_reads
        assert parse_qs(urlparse(timeline_reads[0]).query) == {
            "minute": ["0"],
            "agent_id": ["person-002"],
            "opportunity_offset": ["100"],
            "attention_offset": ["200"],
            "response_offset": ["100"],
            "response_state_offset": ["0"],
            "limit": ["100"],
        }
        expect(page.locator("#opportunity-page-note")).to_have_text("Records 101-150 of 150")
        expect(page.locator("#attention-page-note")).to_have_text("Records 201-222 of 222")
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        expect(page.locator(".response-state-card").first).to_contain_text("PERSON-002")
        browser.close()


def test_partial_viewers_keep_the_exact_legacy_timeline_reads() -> None:
    browser_path = _installed_browser()
    cases = (
        ("mobility", _place_viewer, {"/api/frame"}),
        ("opportunity", _spatial_viewer, {"/api/frame", "/api/opportunities"}),
        (
            "attention",
            _attention_viewer,
            {"/api/frame", "/api/opportunities", "/api/attention-events"},
        ),
    )
    with sync_playwright() as playwright:
        for label, viewer_factory, expected_paths in cases:
            with viewer_factory() as viewer:
                base_url = viewer if isinstance(viewer, str) else viewer[0]
                browser = playwright.chromium.launch(
                    executable_path=str(browser_path),
                    headless=True,
                    args=["--no-first-run", "--disable-background-networking"],
                )
                page = browser.new_page()
                observed: list[str] = []
                page.on(
                    "request",
                    lambda request, observed=observed: (
                        observed.append(request.url) if "/api/" in request.url else None
                    ),
                )
                page.goto(base_url, wait_until="networkidle")
                observed.clear()
                page.locator("#time-slider").evaluate(
                    "element => { element.value = '1'; "
                    "element.dispatchEvent(new Event('input', { bubbles: true })); }"
                )
                page.wait_for_function("() => state.timelineLoading === false")

                paths = [urlparse(url).path for url in observed]
                assert set(paths) == expected_paths, (label, observed)
                assert len(paths) == len(expected_paths), (label, observed)
                assert "/api/timeline" not in paths
                frame_url = next(url for url in observed if urlparse(url).path == "/api/frame")
                assert parse_qs(urlparse(frame_url).query) == {
                    "minute": ["1"],
                    "agent_id": ["person-001"],
                }
                for path, offset_name in (
                    ("/api/opportunities", "offset"),
                    ("/api/attention-events", "offset"),
                ):
                    if path not in expected_paths:
                        continue
                    evidence_url = next(url for url in observed if urlparse(url).path == path)
                    assert parse_qs(urlparse(evidence_url).query) == {
                        "minute": ["1"],
                        offset_name: ["0"],
                        "limit": ["100"],
                    }
                expect(page.locator("#error-banner"), message=label).to_be_hidden()
                browser.close()


def test_malformed_combined_timeline_documents_cannot_replace_committed_ui() -> None:
    browser_path = _installed_browser()
    current = {"corruption": "extra-top-level"}
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def corrupt_candidate(route: Route) -> None:
            if "minute=1" not in route.request.url:
                route.continue_()
                return
            response = route.fetch()
            document = response.json()
            corruption = current["corruption"]
            if corruption == "extra-top-level":
                document["unexpected"] = True
            elif corruption == "wrong-binding":
                document["agent_id"] = "person-002"
            elif corruption == "malformed-frame":
                document["frame"]["positions"][0]["longitude"] = "east"
            elif corruption == "malformed-opportunity-page":
                document["opportunity_page"]["unexpected"] = True
            elif corruption == "state-extra-field":
                document["response_state_page"]["unexpected"] = True
            elif corruption == "state-hash-mismatch":
                document["response_state_page"]["response_input_sha256"] = "0" * 64
            elif corruption == "state-agent-mismatch":
                document["response_state_page"]["items"][0]["agent_id"] = "person-002"
            elif corruption == "duplicate-state":
                first = document["response_state_page"]["items"][0]
                document["response_state_page"]["items"][1]["campaign_id"] = first["campaign_id"]
            else:  # pragma: no cover - closed table below
                raise AssertionError(corruption)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(document, ensure_ascii=False),
            )

        page.route("**/api/timeline?*", corrupt_candidate)
        page.goto(base_url, wait_until="networkidle")
        committed = page.evaluate(
            """() => ({
              minute: state.minute,
              selected: state.selected,
              slider: document.getElementById('time-slider').value,
              responseIds: Array.from(document.querySelectorAll('.response-card'))
                .map((item) => item.dataset.evidenceId),
              stateText: document.getElementById('response-state-list').textContent,
            })"""
        )

        expected_errors = {
            "extra-top-level": "Timeline batch is invalid",
            "wrong-binding": "Timeline batch is invalid",
            "malformed-frame": "Timeline frame is invalid",
            "malformed-opportunity-page": "Opportunity evidence page is invalid",
            "state-extra-field": "Spatial response state page is invalid",
            "state-hash-mismatch": "Spatial response state page is invalid",
            "state-agent-mismatch": "Spatial response state page is invalid",
            "duplicate-state": "Spatial response state page is invalid",
        }
        for corruption, expected_error in expected_errors.items():
            current["corruption"] = corruption
            page.locator("#time-slider").evaluate(
                "element => { element.value = '1'; "
                "element.dispatchEvent(new Event('input', { bubbles: true })); }"
            )
            page.wait_for_function("() => state.timelineLoading === false")
            expect(page.locator("#error-banner"), message=corruption).to_contain_text(
                expected_error
            )
            assert (
                page.evaluate(
                    """() => ({
                      minute: state.minute,
                      selected: state.selected,
                      slider: document.getElementById('time-slider').value,
                      responseIds: Array.from(document.querySelectorAll('.response-card'))
                        .map((item) => item.dataset.evidenceId),
                      stateText: document.getElementById('response-state-list').textContent,
                    })"""
                )
                == committed
            ), corruption

        page.unroute("**/api/timeline?*", corrupt_candidate)
        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_function("() => state.timelineLoading === false")
        assert page.evaluate("() => state.minute") == 1
        expect(page.locator("#time-slider")).to_have_value("1")
        expect(page.locator("#error-banner")).to_be_hidden()
        assert page.evaluate("() => state.timelineAbortController === null")
        browser.close()


def test_model_clock_day_speed_and_zoom_controls_are_bounded_keyboard_operable() -> None:
    browser_path = _installed_browser()
    request_methods: list[str] = []
    with _place_viewer() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.on(
            "request",
            lambda request: (
                request_methods.append(request.method) if "/api/" in request.url else None
            ),
        )
        page.goto(base_url, wait_until="networkidle")

        expect(page.locator("#model-clock-note")).to_contain_text("Etc/UTC")
        expect(page.locator("#model-clock-note")).to_contain_text("06:00")
        expect(page.locator("#model-clock-note")).to_contain_text("19:00")
        expect(page.locator("#model-clock-note")).to_contain_text("does not model local sunrise")
        expect(page.locator("#model-clock-note")).to_contain_text("traffic, calendars, or DST")
        expect(page.locator("#playback-speed-label")).to_have_text("1\N{MULTIPLICATION SIGN}")

        next_day = page.locator("#next-day-button")
        next_day.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#time-slider")).to_have_attribute(
            "aria-valuetext", "Day 2, 00:00 model time (Etc/UTC)"
        )
        assert page.locator("#time-slider").input_value() == "1440"

        previous_day = page.locator("#previous-day-button")
        previous_day.focus()
        page.keyboard.press("Space")
        page.wait_for_function("() => state.timelineLoading === false")
        assert page.locator("#time-slider").input_value() == "0"
        expect(previous_day).to_be_disabled()

        page.locator("#time-slider").evaluate(
            "element => { element.value = String(6 * 1440 + 123); "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#next-day-button")).to_be_disabled()
        assert page.locator("#time-slider").input_value() == str(6 * 1440 + 123)

        speed = page.locator("#playback-speed")
        speed.select_option("4")
        expect(page.locator("#playback-speed-label")).to_have_text("4\N{MULTIPLICATION SIGN}")
        assert page.evaluate("() => state.playbackSpeed") == 4

        zoom_in = page.locator("#zoom-in-button")
        zoom_in.focus()
        for _ in range(30):
            page.keyboard.press("Enter")
        expect(zoom_in).to_be_disabled()
        expect(page.locator("#zoom-level")).to_have_text("800%")
        assert page.evaluate("() => state.zoom") == 8

        page.locator("#zoom-reset-button").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#zoom-level")).to_have_text("100%")
        assert page.evaluate("() => [state.zoom, state.panX, state.panY]") == [1, 0, 0]

        zoom_out = page.locator("#zoom-out-button")
        for _ in range(30):
            zoom_out.press("Enter")
        expect(zoom_out).to_be_disabled()
        expect(page.locator("#zoom-level")).to_have_text("50%")
        assert request_methods and set(request_methods) == {"GET"}
        browser.close()


def test_play_at_end_restarts_the_saved_timeline_from_the_beginning() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as viewer, sync_playwright() as playwright:
        base_url = viewer[0]
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        final_minute = 1_439
        page.locator("#time-slider").fill(str(final_minute))
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#play-button")).to_have_attribute(
            "aria-label", "Replay timeline from start"
        )
        expect(page.locator("#playback-status")).to_contain_text("PRESS PLAY TO REPLAY")

        page.locator("#time-slider").fill("12")
        page.wait_for_function("() => state.timelineLoading === false")
        expect(page.locator("#play-button")).to_have_attribute("aria-label", "Play timeline")
        page.locator("#time-slider").fill(str(final_minute))
        page.wait_for_function("() => state.timelineLoading === false")

        page.locator("#playback-speed").select_option("4")
        page.locator("#play-button").click()
        page.wait_for_function(
            "finalMinute => state.playing && state.minute < finalMinute",
            arg=final_minute,
            timeout=5_000,
        )

        assert int(page.locator("#time-slider").input_value()) < 10
        expect(page.locator("#play-button")).to_have_attribute("aria-label", "Pause timeline")
        page.locator("#play-button").click()
        browser.close()


def test_replay_restart_is_single_flight_while_the_first_frame_is_loading() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as viewer, sync_playwright() as playwright:
        base_url = viewer[0]
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator("#time-slider").fill("1439")
        page.wait_for_function("() => state.timelineLoading === false")
        page.evaluate(
            """() => {
              const originalFetch = window.fetch.bind(window);
              window.replayRestartProbe = { starts: 0 };
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (!url.includes('/api/timeline?minute=0&')) return originalFetch(...args);
                window.replayRestartProbe.starts += 1;
                return new Promise((resolve, reject) => setTimeout(() => {
                  originalFetch(...args).then(resolve, reject);
                }, 350));
              };
            }"""
        )

        page.locator("#play-button").evaluate("button => { button.click(); button.click(); }")
        page.wait_for_function("() => window.replayRestartProbe.starts === 1")
        expect(page.locator("#play-button")).to_be_disabled()
        expect(page.locator("#playback-status")).to_contain_text("RESTARTING")
        page.wait_for_function("() => state.playing === true", timeout=5_000)

        assert page.evaluate("() => window.replayRestartProbe.starts") == 1
        expect(page.locator("#play-button")).to_be_enabled()
        expect(page.locator("#play-button")).to_have_attribute("aria-label", "Pause timeline")
        page.locator("#play-button").click()
        browser.close()


def test_playback_awaits_each_slow_timeline_batch_without_timer_backlog() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            """() => {
              const originalFetch = window.fetch.bind(window);
              window.playbackProbe = { active: 0, maximum: 0, starts: 0 };
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (!url.includes('/api/timeline?minute=')) return originalFetch(...args);
                window.playbackProbe.active += 1;
                window.playbackProbe.starts += 1;
                window.playbackProbe.maximum = Math.max(
                  window.playbackProbe.maximum,
                  window.playbackProbe.active,
                );
                return new Promise((resolve, reject) => setTimeout(() => {
                  originalFetch(...args).then(resolve, reject).finally(() => {
                    window.playbackProbe.active -= 1;
                  });
                }, 350));
              };
            }"""
        )

        page.locator("#playback-speed").select_option("4")
        page.locator("#play-button").click()
        page.wait_for_function("() => window.playbackProbe.starts >= 2", timeout=5_000)
        probe = page.evaluate("() => ({ ...window.playbackProbe })")
        assert probe["maximum"] == 1

        committed_before_pause = page.locator("#time-slider").input_value()
        page.locator("#play-button").click()
        expect(page.locator("#play-button")).to_have_attribute("aria-label", "Play timeline")
        page.wait_for_function("() => window.playbackProbe.active === 0")
        paused_minute = page.locator("#time-slider").input_value()
        assert paused_minute == committed_before_pause
        starts = page.evaluate("() => window.playbackProbe.starts")
        page.wait_for_timeout(500)
        assert page.locator("#time-slider").input_value() == paused_minute
        assert page.evaluate("() => window.playbackProbe.starts") == starts

        page.locator("#play-button").click()
        page.wait_for_function("() => window.playbackProbe.active === 1")
        page.evaluate("() => window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        page.wait_for_function("() => window.playbackProbe.active === 0")
        expect(page.locator("#play-button")).to_have_attribute("aria-label", "Play timeline")
        assert page.locator("#time-slider").input_value() == paused_minute
        app_source = (
            resources.files("adlife.city").joinpath("static", "app.js").read_text(encoding="utf-8")
        )
        assert "setInterval(" not in app_source
        browser.close()


def test_playback_uses_one_quiet_live_region_and_manual_navigation_announces_once() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        timeline_regions = page.evaluate(
            """() => ({
              announcement: (() => {
                const element = document.getElementById('timeline-announcement');
                return element && {
                  role: element.getAttribute('role'),
                  live: element.getAttribute('aria-live'),
                  atomic: element.getAttribute('aria-atomic'),
                };
              })(),
              changing: [
                'map-text-summary', 'opportunity-current', 'attention-current',
                'response-current', 'response-state-current', 'playback-status',
              ].map((id) => ({
                id,
                role: document.getElementById(id).getAttribute('role'),
                live: document.getElementById(id).getAttribute('aria-live'),
              })),
            })"""
        )
        assert timeline_regions["announcement"] == {
            "role": "status",
            "live": "polite",
            "atomic": "true",
        }
        assert timeline_regions["changing"] == [
            {"id": identifier, "role": None, "live": None}
            for identifier in (
                "map-text-summary",
                "opportunity-current",
                "attention-current",
                "response-current",
                "response-state-current",
                "playback-status",
            )
        ]

        page.evaluate(
            """() => {
              window.timelineAnnouncements = [];
              const target = document.getElementById('timeline-announcement');
              new MutationObserver(() => {
                window.timelineAnnouncements.push(target.textContent);
              }).observe(target, { childList: true, characterData: true, subtree: true });
            }"""
        )

        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        page.wait_for_function("() => window.timelineAnnouncements.length === 1")
        pagination_announcements = page.evaluate("() => [...window.timelineAnnouncements]")
        assert len(pagination_announcements) == 1, pagination_announcements
        assert "Response evidence page" in pagination_announcements[0]
        assert "Records 101-144 of 144" in pagination_announcements[0]

        page.evaluate("() => { window.timelineAnnouncements.length = 0; }")
        page.locator("#response-page-previous").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 1-100 of 144")
        page.wait_for_function("() => window.timelineAnnouncements.length === 1")
        pagination_announcements = page.evaluate("() => [...window.timelineAnnouncements]")
        assert len(pagination_announcements) == 1, pagination_announcements
        assert "Records 1-100 of 144" in pagination_announcements[0]

        page.evaluate("() => { window.timelineAnnouncements.length = 0; }")
        page.locator("#playback-speed").select_option("4")
        page.locator("#play-button").click()
        page.wait_for_function("() => state.minute >= 3", timeout=6_000)
        page.locator("#play-button").click()
        page.wait_for_function("() => state.timelineLoading === false")
        autoplay_announcements = page.evaluate("() => [...window.timelineAnnouncements]")
        assert len(autoplay_announcements) == 2, autoplay_announcements
        assert "Playback started" in autoplay_announcements[0]
        assert "Playback paused" in autoplay_announcements[1]

        page.evaluate("() => { window.timelineAnnouncements.length = 0; }")
        manual_minute = int(page.locator("#time-slider").input_value()) + 2
        page.locator("#time-slider").evaluate(
            "(element, minute) => { element.value = String(minute); "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }",
            manual_minute,
        )
        page.wait_for_function("() => state.timelineLoading === false")
        manual_announcements = page.evaluate("() => [...window.timelineAnnouncements]")
        assert len(manual_announcements) == 1, manual_announcements
        assert "model time" in manual_announcements[0]
        assert "PERSON-001" in manual_announcements[0]
        browser.close()


def test_text_map_alternative_tracks_causal_evidence_without_filtering_totals() -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer() as (
            base_url,
            _,
            _,
            _,
            response_evaluation,
            response_metrics,
        ),
        sync_playwright() as playwright,
    ):
        visible_records = [
            record for record in response_evaluation.records if record.model_minute == 0
        ][:100]
        selected_record = next(
            record for record in visible_records if record.event_type == "spatial.response"
        )
        related_update = next(
            record
            for record in visible_records
            if record.event_type == "spatial.state-updated"
            and selected_record.event_id in record.caused_by_event_ids
        )
        unrelated_record = next(
            record
            for record in visible_records
            if record.event_type == "spatial.response"
            and record.campaign_id == selected_record.campaign_id
            and record.event_id != selected_record.event_id
        )
        campaign_receipt = next(
            campaign
            for campaign in response_metrics.campaigns
            if campaign.campaign_id == selected_record.campaign_id
        )
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        alternative = page.locator("#map-text-summary")
        expect(alternative).to_contain_text("Day 1, 00:00")
        expect(alternative).to_contain_text("PERSON-001")
        expect(alternative).to_contain_text("activity")
        expect(page.locator("#map-evidence-scope")).to_contain_text("page-scoped")
        totals_before = page.evaluate(
            """() => ({
              opportunities: document.getElementById('opportunity-total').textContent,
              impressions: document.getElementById('attention-impressions').textContent,
              responses: document.getElementById('response-responses').textContent,
            })"""
        )

        record = page.locator(f'[data-evidence-id="{selected_record.event_id}"]')
        expect(record).to_be_visible()
        evidence_id = record.get_attribute("data-evidence-id")
        campaign_id = record.get_attribute("data-campaign-id")
        placement_id = record.get_attribute("data-placement-id")
        assert evidence_id and campaign_id and placement_id
        record.click()
        page.wait_for_function("() => state.timelineLoading === false")

        selected = page.locator(f'[data-evidence-id="{evidence_id}"]')
        expect(selected).to_have_class(re.compile(r"\bcausal-selected\b"))
        expect(selected).to_have_attribute("aria-pressed", "true")
        expect(page.locator(f'[data-evidence-id="{related_update.event_id}"]')).to_have_class(
            re.compile(r"\bcausal-related\b")
        )
        unrelated = page.locator(f'[data-evidence-id="{unrelated_record.event_id}"]')
        expect(unrelated).not_to_have_class(re.compile(r"\bcausal-related\b"))
        expect(unrelated).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#response-causal")).to_have_attribute("data-campaign-id", campaign_id)
        expect(page.locator("#response-metrics-panel")).to_have_attribute(
            "data-campaign-id", campaign_id
        )
        expect(page.locator("#response-metrics-campaign")).to_have_value(campaign_id)
        expect(page.locator("#response-metrics-series-label")).to_have_text(
            f"CAMPAIGN / {campaign_id.upper()}"
        )
        expect(page.locator("#response-metrics-event-scope")).to_have_text("Full-run event totals")
        assert (
            float(
                page.locator(
                    "#response-metrics-state-purchase-intention-proxy-final"
                ).get_attribute("data-value")
                or "nan"
            )
            == campaign_receipt.purchase_intention_proxy.final_mean
        )
        expect(page.locator("#response-metrics-campaign")).to_have_attribute(
            "data-causal-campaign-id", campaign_id
        )
        expect(alternative).to_contain_text(evidence_id.upper())
        expect(alternative).to_contain_text(campaign_id.upper())
        expect(alternative).to_contain_text(placement_id.upper())
        expect(page.locator("#map-evidence-scope")).to_contain_text("page-scoped")
        assert (
            page.evaluate(
                """() => ({
              opportunities: document.getElementById('opportunity-total').textContent,
              impressions: document.getElementById('attention-impressions').textContent,
              responses: document.getElementById('response-responses').textContent,
            })"""
            )
            == totals_before
        )
        browser.close()


def test_paging_away_from_selected_evidence_clears_every_causal_highlight() -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer() as (base_url, _, _, _, response_evaluation, _),
        sync_playwright() as playwright,
    ):
        first_page = [record for record in response_evaluation.records if record.model_minute == 0][
            :100
        ]
        selected_record = next(
            record for record in first_page if record.event_type == "spatial.response"
        )
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        page.locator(f'[data-evidence-id="{selected_record.event_id}"]').click()
        page.wait_for_function(
            "evidenceId => state.selectedEvidence && state.selectedEvidence.id === evidenceId",
            arg=selected_record.event_id,
        )
        related_before = page.locator(
            "#opportunity-list .causal-related, #attention-list .causal-related"
        ).count()
        assert related_before > 0

        page.locator("#response-page-next").click()
        expect(page.locator("#response-page-note")).to_have_text("Records 101-144 of 144")
        page.wait_for_function("() => state.selectedEvidence === null", timeout=5_000)

        assert (
            page.locator(
                "#opportunity-list .causal-related, #attention-list .causal-related, "
                "#response-list .causal-related, #response-list .causal-selected"
            ).count()
            == 0
        )
        expect(page.locator("#response-metrics-campaign")).to_have_value(
            selected_record.campaign_id
        )
        expect(page.locator("#map-text-summary")).to_contain_text(
            f"Selected campaign {selected_record.campaign_id.upper()}."
        )
        browser.close()


def test_causal_selection_is_atomic_when_evidence_or_campaign_supersedes_it() -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer() as (base_url, scenario, _, _, response_evaluation, _),
        sync_playwright() as playwright,
    ):
        visible_rules = [
            record
            for record in [item for item in response_evaluation.records if item.model_minute == 0][
                :100
            ]
            if record.event_type == "spatial.response"
        ]
        first = visible_rules[0]
        second = next(
            record
            for record in visible_rules
            if record.agent_id != first.agent_id and record.campaign_id != first.campaign_id
        )
        campaign_choice = next(
            campaign.campaign_id
            for campaign in scenario.campaigns
            if campaign.campaign_id not in {first.campaign_id, second.campaign_id}
        )
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.evaluate(
            """() => {
              const originalFetch = window.fetch.bind(window);
              window.causalDelay = { target: null, used: false, started: false, settled: false };
              window.configureCausalDelay = (agentId) => {
                window.causalDelay = {
                  target: `agent_id=${encodeURIComponent(agentId)}`,
                  used: false,
                  started: false,
                  settled: false,
                };
              };
              window.fetch = (...args) => {
                const url = String(args[0]);
                const probe = window.causalDelay;
                if (
                  url.includes('/api/timeline?')
                  && probe.target !== null
                  && url.includes(probe.target)
                  && !probe.used
                ) {
                  probe.used = true;
                  probe.started = true;
                  return new Promise((resolve, reject) => window.setTimeout(() => {
                    originalFetch(...args).then(resolve, reject).finally(() => {
                      probe.settled = true;
                    });
                  }, 450));
                }
                return originalFetch(...args);
              };
            }"""
        )

        page.evaluate("agentId => window.configureCausalDelay(agentId)", first.agent_id)
        page.locator(f'[data-evidence-id="{first.event_id}"]').click()
        page.wait_for_function("() => window.causalDelay.started === true")
        page.locator(f'[data-evidence-id="{second.event_id}"]').click()
        page.wait_for_function(
            "evidenceId => state.selectedEvidence && state.selectedEvidence.id === evidenceId",
            arg=second.event_id,
        )
        page.wait_for_function("() => window.causalDelay.settled === true")
        assert page.evaluate(
            """() => ({
              agent: state.selected,
              campaign: state.selectedCampaign,
              evidence: state.selectedEvidence && state.selectedEvidence.id,
            })"""
        ) == {
            "agent": second.agent_id,
            "campaign": second.campaign_id,
            "evidence": second.event_id,
        }

        page.evaluate("agentId => window.configureCausalDelay(agentId)", first.agent_id)
        page.locator(f'[data-evidence-id="{first.event_id}"]').click()
        page.wait_for_function("() => window.causalDelay.started === true")
        page.locator("#response-metrics-campaign").select_option(campaign_choice)
        page.wait_for_function("() => window.causalDelay.settled === true")
        assert page.evaluate(
            """() => ({
              campaign: state.selectedCampaign,
              evidence: state.selectedEvidence,
              selector: document.getElementById('response-metrics-campaign').value,
            })"""
        ) == {"campaign": campaign_choice, "evidence": None, "selector": campaign_choice}
        browser.close()


def test_representative_core_ui_text_scales_at_200_percent_without_horizontal_overflow() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")
        representative_text = {
            "mapTitle": "#map-text-title",
            "mapSummary": "#map-text-summary",
            "responseDetail": ".response-card small",
            "metricsSeries": "#response-metrics-series-label",
            "selectedActivity": "#selected-activity",
            "zoomReset": "#zoom-reset-button",
            "timelineTime": "#timeline-time",
        }
        for selector in representative_text.values():
            assert page.locator(selector).count() > 0, selector

        def computed_font_sizes() -> dict[str, float]:
            return page.evaluate(
                """selectors => Object.fromEntries(
                  Object.entries(selectors).map(([name, selector]) => [
                    name,
                    Number.parseFloat(getComputedStyle(document.querySelector(selector)).fontSize),
                  ])
                )""",
                representative_text,
            )

        font_sizes_before = computed_font_sizes()
        page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
        font_sizes_after = computed_font_sizes()
        overflow = page.evaluate(
            """() => ({
              documentWidth: document.documentElement.scrollWidth,
              viewportWidth: window.innerWidth,
              elements: Array.from(document.querySelectorAll('body *'))
                .map((element) => {
                  const rect = element.getBoundingClientRect();
                  return { tag: element.tagName, id: element.id, className: element.className,
                    left: rect.left, right: rect.right, width: rect.width };
                })
                .filter((item) => item.left < -0.5 || item.right > window.innerWidth + 0.5)
                .slice(0, 12),
            })"""
        )
        browser.close()

    for name, initial_size in font_sizes_before.items():
        assert font_sizes_after[name] >= initial_size * 1.9, {
            "name": name,
            "before": initial_size,
            "after": font_sizes_after[name],
        }
    assert overflow["documentWidth"] <= overflow["viewportWidth"], overflow


def test_inspector_controls_respect_accessibility_preferences_and_leave_artifacts_immutable(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    with (
        _workbench_assumptions_viewer(tmp_path) as (base_url, root, expected),
        sync_playwright() as playwright,
    ):
        run_directory = root / "city-runs" / str(expected["run_id"])
        before = _artifact_hashes(run_directory)
        methods: list[str] = []
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.emulate_media(reduced_motion="reduce", forced_colors="active")
        page.on(
            "request",
            lambda request: methods.append(request.method) if "/api/" in request.url else None,
        )
        page.goto(f"{base_url}/runs/{expected['run_id']}", wait_until="networkidle")
        page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")

        expect(page.locator("#play-button")).to_be_disabled()
        expect(page.locator("#playback-status")).to_contain_text("reduced motion")
        for selector in (
            "#previous-day-button",
            "#play-button",
            "#next-day-button",
            "#zoom-out-button",
            "#zoom-reset-button",
            "#zoom-in-button",
        ):
            box = page.locator(selector).bounding_box()
            assert box is not None and box["height"] >= 44 and box["width"] >= 44

        page.locator("#zoom-reset-button").focus()
        assert page.locator("#zoom-reset-button").evaluate(
            "element => getComputedStyle(element).outlineStyle !== 'none'"
        )
        overflow = page.evaluate(
            """() => ({
              documentWidth: document.documentElement.scrollWidth,
              viewportWidth: window.innerWidth,
              elements: Array.from(document.querySelectorAll('body *'))
                .map((element) => {
                  const rect = element.getBoundingClientRect();
                  return { tag: element.tagName, id: element.id, className: element.className,
                    left: rect.left, right: rect.right, width: rect.width };
                })
                .filter((item) => item.left < -0.5 || item.right > window.innerWidth + 0.5)
                .slice(0, 12),
            })"""
        )
        assert overflow["documentWidth"] <= overflow["viewportWidth"], overflow
        assert methods and set(methods) == {"GET"}
        browser.close()
        assert _artifact_hashes(run_directory) == before


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("claim", "Spatial response metrics contract is invalid"),
        ("projection-model", "Spatial response metrics contract is invalid"),
        ("response-model", "Spatial response metrics contract is invalid"),
        ("source-schema", "Spatial response metrics contract is invalid"),
        ("missing-series", "Response metric series is invalid"),
        ("extra-field", "Spatial response metrics contract is invalid"),
        ("series", "Response metric series is inconsistent"),
        ("rounding-budget", "Response metric partitions are inconsistent"),
        ("subnormal-partition", "Response metric partitions are inconsistent"),
        ("power-boundary-partition", "Response metric partitions are inconsistent"),
        ("half-even-partition", "Response metric partitions are inconsistent"),
        ("seed-upper-bound", "Spatial response seed is invalid"),
        ("credential-campaign", "Spatial response metric campaign identity is invalid"),
        ("unknown-campaign", "Spatial response metric campaign identity is invalid"),
        ("channel-partition", "Response metric partitions are inconsistent"),
        ("population", "Response metric population evidence is inconsistent"),
        ("state-partition", "Response state partitions are inconsistent"),
        ("http-failure", "Could not load /api/spatial-response-metrics (503)"),
        ("non-finite", "Spatial response population is invalid"),
    ],
)
def test_response_viewer_refuses_malformed_full_run_metrics_without_fabricating_evidence(
    corruption: str,
    expected_error: str,
) -> None:
    browser_path = _installed_browser()
    with (
        _response_viewer() as (base_url, _, _, _, _, response_metrics),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def corrupt_opportunity_summary(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            campaigns = document["campaigns"]
            assert isinstance(campaigns, list)
            campaign = campaigns[-1]
            assert isinstance(campaign, dict)
            campaign["campaign_id"] = "sk-live-abcdef1234"
            route.fulfill(json=document)

        def corrupt_metrics(route: Route) -> None:
            if corruption == "http-failure":
                route.fulfill(status=503, body="temporarily unavailable")
                return
            if corruption == "non-finite":
                body = response_metrics.model_dump_json().replace(
                    '"population_size":30',
                    '"population_size":1e400',
                )
                route.fulfill(body=body, content_type="application/json")
                return
            document = response_metrics.model_dump(mode="json")
            if corruption == "claim":
                document["claim_scope"] = "observed-sales"
            elif corruption == "projection-model":
                document["model_id"] = "unknown-projection-model"
            elif corruption == "response-model":
                document["response_model_id"] = "unknown-response-model"
            elif corruption == "source-schema":
                document["source_run_schema_version"] = 7
            elif corruption == "missing-series":
                overall = document["overall"]
                assert isinstance(overall, dict)
                del overall["response_frequency"]
            elif corruption == "extra-field":
                document["unexpected_credential"] = "sk-live-abcdef1234"
            elif corruption == "rounding-budget":
                overall = document["overall"]
                assert isinstance(overall, dict)
                receipt = overall["mean_rule_sentiment_delta"]
                assert isinstance(receipt, dict)
                numerator = float(receipt["numerator"])
                numerator = math.nextafter(math.nextafter(numerator, math.inf), math.inf)
                receipt["numerator"] = numerator
                receipt["value"] = numerator / int(receipt["denominator"])
            elif corruption == "subnormal-partition":
                overall = document["overall"]
                channels = document["channels"]
                campaigns = document["campaigns"]
                assert isinstance(overall, dict)
                assert isinstance(channels, list)
                assert isinstance(campaigns, list)
                smallest_subnormal = math.ulp(0.0)
                series_with_values = [
                    (overall, smallest_subnormal),
                    *((channel, smallest_subnormal) for channel in channels),
                    *(
                        (campaign, smallest_subnormal if index < 2 else 0.0)
                        for index, campaign in enumerate(campaigns)
                    ),
                ]
                for series, numerator in series_with_values:
                    assert isinstance(series, dict)
                    receipt = series["mean_rule_sentiment_delta"]
                    assert isinstance(receipt, dict)
                    denominator = int(receipt["denominator"])
                    receipt["numerator"] = numerator
                    receipt["value"] = numerator / denominator if denominator else 0.0
            elif corruption == "power-boundary-partition":
                overall = document["overall"]
                channels = document["channels"]
                campaigns = document["campaigns"]
                assert isinstance(overall, dict)
                assert isinstance(channels, list)
                assert isinstance(campaigns, list)
                total = 15.999999999999998
                campaign_parts = [
                    3.9999999999999982,
                    8.0,
                    0.4999999999999996,
                    0.4999999999999997,
                    0.9999999999999993,
                    1.9999999999999982,
                ]
                series_with_values = [
                    (overall, total),
                    (channels[0], 0.0),
                    (channels[1], total),
                    *zip(campaigns, campaign_parts, strict=True),
                ]
                for series, numerator in series_with_values:
                    assert isinstance(series, dict)
                    receipt = series["mean_rule_sentiment_delta"]
                    assert isinstance(receipt, dict)
                    denominator = int(receipt["denominator"])
                    receipt["numerator"] = numerator
                    receipt["value"] = numerator / denominator if denominator else 0.0
            elif corruption == "half-even-partition":
                overall = document["overall"]
                channels = document["channels"]
                campaigns = document["campaigns"]
                assert isinstance(overall, dict)
                assert isinstance(channels, list)
                assert isinstance(campaigns, list)
                total = 1.596434553705404e-17
                campaign_parts = [
                    -3.878577591183869e-226,
                    -5.340434698482726e-213,
                    -9.205851459807051e-191,
                    2.112101272004847e-17,
                    -5.156667182994425e-18,
                    6.744381928370066e-122,
                ]
                series_with_values = [
                    (overall, total),
                    (channels[0], 0.0),
                    (channels[1], total),
                    *zip(campaigns, campaign_parts, strict=True),
                ]
                for series, numerator in series_with_values:
                    assert isinstance(series, dict)
                    receipt = series["mean_rule_sentiment_delta"]
                    assert isinstance(receipt, dict)
                    denominator = int(receipt["denominator"])
                    receipt["numerator"] = numerator
                    receipt["value"] = numerator / denominator if denominator else 0.0
            elif corruption == "seed-upper-bound":
                document["seed"] = 2**63
            elif corruption in {"credential-campaign", "unknown-campaign"}:
                campaigns = document["campaigns"]
                assert isinstance(campaigns, list)
                campaign = campaigns[-1]
                assert isinstance(campaign, dict)
                campaign["campaign_id"] = (
                    "sk-live-abcdef1234"
                    if corruption == "credential-campaign"
                    else "campaign-unknown"
                )
            elif corruption == "channel-partition":
                channels = document["channels"]
                assert isinstance(channels, list)
                roadside = channels[0]
                assert isinstance(roadside, dict)
                for name in ("response_count", "response_frequency"):
                    receipt = roadside[name]
                    assert isinstance(receipt, dict)
                    receipt["numerator"] = 1
                    receipt["value"] = 1 if name == "response_count" else 0
                for name in ("mean_rule_sentiment_delta", "mean_rule_recall_delta"):
                    receipt = roadside[name]
                    assert isinstance(receipt, dict)
                    receipt["denominator"] = 1
            elif corruption == "population":
                overall = document["overall"]
                assert isinstance(overall, dict)
                response_reach = overall["response_reach"]
                assert isinstance(response_reach, dict)
                response_reach["denominator"] = response_reach["numerator"]
                response_reach["value"] = 1
            elif corruption == "state-partition":
                campaigns = document["campaigns"]
                assert isinstance(campaigns, list)
                campaign = campaigns[-1]
                assert isinstance(campaign, dict)
                receipt = campaign["brand_sentiment"]
                assert isinstance(receipt, dict)
                initial_total = float(receipt["initial_total"])
                final_total = initial_total + 0.01
                denominator = int(receipt["denominator"])
                receipt["final_total"] = final_total
                receipt["change_total"] = final_total - initial_total
                receipt["final_mean"] = final_total / denominator
                receipt["mean_change"] = (final_total - initial_total) / denominator
            else:
                overall = document["overall"]
                assert isinstance(overall, dict)
                response_count = overall["response_count"]
                assert isinstance(response_count, dict)
                response_count["numerator"] = 0
                response_count["value"] = 0
            route.fulfill(json=document)

        if corruption == "credential-campaign":
            page.route("**/api/opportunity-summary", corrupt_opportunity_summary)
        page.route("**/api/spatial-response-metrics", corrupt_metrics)
        page.goto(base_url, wait_until="networkidle")

        expect(page.locator("#error-banner")).to_be_visible()
        expect(page.locator("#error-banner")).to_contain_text(expected_error)
        assert "sk-live-abcdef1234" not in page.locator("#error-banner").inner_text()
        assert not page.locator("#response-metrics-panel").is_visible()
        assert page.locator("#response-metrics-event-body tr").count() == 0
        assert page.locator("#response-metrics-state-body tr").count() == 0
        browser.close()


def test_response_metrics_endpoint_is_not_requested_without_its_capability() -> None:
    browser_path = _installed_browser()
    metric_requests: list[str] = []
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def hide_metrics_capability(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            assert document.pop("spatial_response_metrics") is True
            route.fulfill(json=document)

        def observe_metrics_request(route: Route) -> None:
            metric_requests.append(route.request.url)
            route.continue_()

        page.route("**/api/meta", hide_metrics_capability)
        page.route("**/api/spatial-response-metrics", observe_metrics_request)
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("#response-panel").is_visible()
        assert not page.locator("#response-metrics-panel").is_visible()
        assert page.locator("#response-metrics-event-body tr").count() == 0
        assert page.locator("#response-metrics-state-body tr").count() == 0
        assert metric_requests == []
        browser.close()


def test_response_viewer_preserves_the_maximum_seed_without_javascript_rounding() -> None:
    browser_path = _installed_browser()
    maximum_seed = 2**63 - 1
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page()

        def use_maximum_seed(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            document["seed"] = maximum_seed
            route.fulfill(json=document)

        page.route("**/api/meta", use_maximum_seed)
        page.route("**/api/spatial-response-metrics", use_maximum_seed)
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("#seed-value").inner_text() == str(maximum_seed)
        assert page.locator("#response-metrics-panel").is_visible()
        assert not page.locator("#error-banner").is_visible()
        browser.close()


def test_response_viewer_exposes_compact_accessible_status_and_keyboard_provenance() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(base_url, wait_until="networkidle")

        metric = page.locator("#metrics-overall-notice-rate")
        metric.focus()
        metric_snapshot = metric.evaluate(
            """element => ({
              tabIndex: element.tabIndex,
              active: document.activeElement === element,
              accessibleProvenance: element.getAttribute('aria-label'),
              title: element.getAttribute('title'),
              outlineStyle: getComputedStyle(element).outlineStyle,
              outlineWidth: getComputedStyle(element).outlineWidth,
            })"""
        )
        person = page.locator("#people-list .person").first
        person.focus()
        person_focus = person.evaluate(
            """element => {
              const style = getComputedStyle(element);
              const channels = (value) => value
                .slice(value.indexOf('(') + 1, value.indexOf(')'))
                .split(',')
                .slice(0, 3)
                .map(Number);
              const luminance = (value) => channels(value)
                .map((channel) => {
                  const normalized = channel / 255;
                  return normalized <= 0.04045
                    ? normalized / 12.92
                    : ((normalized + 0.055) / 1.055) ** 2.4;
                })
                .reduce((total, channel, index) => (
                  total + channel * [0.2126, 0.7152, 0.0722][index]
                ), 0);
              const foreground = luminance(style.outlineColor);
              const background = luminance(style.backgroundColor);
              return {
                active: document.activeElement === element,
                style: style.outlineStyle,
                width: style.outlineWidth,
                contrastRatio: (Math.max(foreground, background) + 0.05)
                  / (Math.min(foreground, background) + 0.05),
              };
            }"""
        )
        semantics = page.evaluate(
            """() => ({
              listLiveRegions: [
                'opportunity-list',
                'attention-list',
                'response-list',
                'response-state-list',
              ].map((id) => document.getElementById(id).getAttribute('aria-live')),
              compactStatuses: [
                'opportunity-current',
                'attention-current',
                'response-current',
                'response-state-current',
              ].map((id) => {
                const element = document.getElementById(id);
                return element && {
                  role: element.getAttribute('role'),
                  live: element.getAttribute('aria-live'),
                  atomic: element.getAttribute('aria-atomic'),
                };
              }),
              timelineAnnouncement: (() => {
                const element = document.getElementById('timeline-announcement');
                return element && {
                  role: element.getAttribute('role'),
                  live: element.getAttribute('aria-live'),
                  atomic: element.getAttribute('aria-atomic'),
                };
              })(),
              sliderValueText: document.getElementById('time-slider').getAttribute(
                'aria-valuetext'
              ),
            })"""
        )
        assert page.evaluate("() => signedProxyText(0.00001, 'small change')") == "+0.00001"
        assert page.evaluate("() => compactReceiptNumber(0.00001)") == "0.00001"
        browser.close()

    violations: list[str] = []
    if metric_snapshot["tabIndex"] != 0 or metric_snapshot["active"] is not True:
        violations.append(f"metric cell is not keyboard focusable: {metric_snapshot!r}")
    accessible_provenance = metric_snapshot["accessibleProvenance"] or ""
    if not all(token in accessible_provenance for token in ("Numerator", "denominator", "Sources")):
        violations.append(f"metric provenance is title-only: {metric_snapshot!r}")
    if (
        metric_snapshot["outlineStyle"] == "none"
        or float(metric_snapshot["outlineWidth"].removesuffix("px")) < 2
    ):
        violations.append(f"metric focus indicator is not visible: {metric_snapshot!r}")
    if (
        person_focus["active"] is not True
        or person_focus["style"] == "none"
        or float(person_focus["width"].removesuffix("px")) < 2
        or person_focus["contrastRatio"] < 3
    ):
        violations.append(f"person focus indicator is not high contrast: {person_focus!r}")
    if semantics["listLiveRegions"] != [None, None, None, None]:
        violations.append(f"large lists are live regions: {semantics!r}")
    quiet_status = {"role": None, "live": None, "atomic": None}
    if semantics["compactStatuses"] != [quiet_status] * 4:
        violations.append(f"per-frame statuses are live regions: {semantics!r}")
    expected_status = {"role": "status", "live": "polite", "atomic": "true"}
    if semantics["timelineAnnouncement"] != expected_status:
        violations.append(f"timeline announcement is not atomic and polite: {semantics!r}")
    if semantics["sliderValueText"] != "Day 1, 00:00 model time (not declared)":
        violations.append(f"timeline lacks human-readable value text: {semantics!r}")
    assert not violations, "\n".join(violations)


def test_response_viewer_busy_minute_is_legible_and_contained_at_narrow_width() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _, _, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")

        assert page.locator(".response-card").count() == 100
        assert page.locator("#response-page-note").is_visible()
        page.locator("#response-page-next").focus()
        assert page.locator("#response-page-next").evaluate(
            "element => document.activeElement === element"
        )
        assert (
            page.locator("#response-page-next").evaluate(
                "element => getComputedStyle(element).outlineStyle"
            )
            != "none"
        )
        layout = page.evaluate(
            """() => {
              const fontSize = (selector) => Number.parseFloat(
                getComputedStyle(document.querySelector(selector)).fontSize
              );
              return {
                fonts: {
                  causal: fontSize('#response-causal'),
                  claim: fontSize('#response-claim'),
                  current: fontSize('#response-current'),
                  responseDetail: fontSize('.response-card small'),
                  pageDisclosure: fontSize('#response-page-note'),
                  stateWarning: fontSize('#response-state-warning'),
                  stateClaim: fontSize('#response-state-claim'),
                  stateDetail: fontSize('.response-state-card small'),
                },
                viewportWidth: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                panelWidth: document.getElementById('response-panel').getBoundingClientRect().width,
                paginationContained: ['opportunity', 'attention', 'response'].every((kind) => {
                  const panel = document.getElementById(`${kind}-panel`);
                  const pagination = document.getElementById(`${kind}-pagination`);
                  const paginationWidth = pagination.getBoundingClientRect().width;
                  return paginationWidth <= panel.getBoundingClientRect().width;
                }),
                paginationStatuses: ['opportunity', 'attention', 'response'].map((kind) => {
                  const status = document.getElementById(`${kind}-page-note`);
                  return {
                    role: status.getAttribute('role'),
                    live: status.getAttribute('aria-live'),
                    atomic: status.getAttribute('aria-atomic'),
                  };
                }),
                listLiveRegions: ['opportunity-list', 'attention-list', 'response-list'].map(
                  (id) => document.getElementById(id).getAttribute('aria-live')
                ),
                cardsContained: [...document.querySelectorAll('.response-card')].every(
                  (card) => card.scrollWidth <= card.clientWidth
                ),
              };
            }"""
        )
        browser.close()

    undersized = {name: size for name, size in layout["fonts"].items() if size < 10}
    assert not undersized, f"response evidence below 10px: {undersized!r}"
    assert layout["documentWidth"] <= layout["viewportWidth"]
    assert layout["panelWidth"] <= layout["viewportWidth"]
    assert layout["paginationContained"] is True
    assert layout["paginationStatuses"] == [{"role": None, "live": None, "atomic": None}] * 3
    assert layout["listLiveRegions"] == [None, None, None]
    assert layout["cardsContained"] is True
