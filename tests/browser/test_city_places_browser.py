from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Route, expect, sync_playwright

from adlife.city.web import create_city_app
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.experiments.spatial_metrics import SpatialMetrics, derive_spatial_metrics
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
)
from adlife.core.simulation.spatial_opportunity import SpatialOpportunityEvaluation
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import (
    _billboard,
    _evaluate,
    _mobility,
    _phone,
    _scenario,
)

_BROWSER_PATHS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)


def _installed_browser() -> Path:
    browser = next((path for path in _BROWSER_PATHS if path.is_file()), None)
    if browser is None:
        pytest.skip("no supported local Chromium browser executable is installed")
    return browser


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
            == "SYNTHETIC OPPORTUNITY · NOT AN IMPRESSION"
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
        assert page.locator("#metrics-panel").get_attribute("tabindex") == "0"
        assert page.locator("#metrics-claim").inner_text() == (
            "SYNTHETIC METRICS \u00b7 NOT OBSERVED OUTCOMES"
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
            == "SYNTHETIC ATTENTION \u00b7 NOT OBSERVED BEHAVIOR"
        )
        assert page.locator("#attention-current").inner_text() == (
            f"{len(minute_zero)} EVENTS AT THIS MINUTE \u00b7 "
            f"{len(selected_zero)} FOR SELECTED AGENT"
        )
        assert page.locator(".attention-card").count() == len(minute_zero)
        assert page.locator(".attention-card.selected").count() == len(selected_zero)
        assert page.locator(".attention-key:visible").count() == 2
        assert page.locator("#attention-list").get_attribute("aria-live") == "polite"
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
