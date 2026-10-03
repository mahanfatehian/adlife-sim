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


@contextmanager
def _response_viewer() -> Iterator[tuple[str, SpatialCampaignScenario, SpatialResponseEvaluation]]:
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
        source_run_schema_version=6,
    )
    application = create_city_app(
        simulation,
        run_id="browser-response-study",
        run_schema_version=6,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
        spatial_metrics=metrics,
        response_input=response_input,
        response_evaluation=response_evaluation,
    )
    with _serve(application) as base_url:
        yield base_url, scenario, response_evaluation


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
        assert page.locator("#attention-list").get_attribute("aria-live") is None
        assert page.locator("#attention-current").get_attribute("aria-live") == "polite"
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
        _response_viewer() as (base_url, scenario, response_evaluation),
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
                if "/api/response-state" in request.url
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
        assert len(selected_states) == 5
        assert page.locator("#saved-run-label").inner_text().endswith("V6")
        assert page.locator("#response-panel").is_visible()
        assert page.locator("#response-scenario").inner_text() == scenario.name
        assert page.locator("#response-panel img").count() == 0
        assert page.locator("#response-claim").inner_text() == (
            "SYNTHETIC RESPONSE · NOT OBSERVED BEHAVIOR"
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
        assert page.locator("#response-current").get_attribute("aria-live") == "polite"
        assert page.locator(".response-card").count() == 100
        assert page.locator(".response-card.rule-response").count() > 0
        assert page.locator(".response-card.state-update").count() > 0
        assert "SENTIMENT Δ" in page.locator(".response-card.rule-response").first.inner_text()
        assert "RECALL Δ" in page.locator(".response-card.rule-response").first.inner_text()
        assert "INTENTION PROXY" in page.locator(".response-card.state-update").first.inner_text()
        assert page.locator("#response-page-note").is_visible()
        assert page.locator("#response-page-note").inner_text() == (
            f"Showing the first 100 of {len(minute_zero)} canonical response records."
        )

        assert page.locator("#response-state-panel").is_visible()
        assert page.locator("#response-state-panel").get_attribute("tabindex") == "0"
        assert page.locator("#response-state-time").inner_text() == "FINAL STATE / END OF RUN"
        assert page.locator("#response-state-warning").inner_text() == (
            "NOT STATE AT THE SCRUBBED MINUTE"
        )
        assert page.locator("#response-state-claim").inner_text() == (
            "UNCALIBRATED SYNTHETIC PROXIES · PURCHASE INTENTION IS NOT PURCHASE "
            "PROBABILITY OR SALES"
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

        page.locator("#time-slider").evaluate(
            "element => { element.value = '1'; "
            "element.dispatchEvent(new Event('input', { bubbles: true })); }"
        )
        expect(page.locator("#response-current")).to_have_text(
            "0 RECORDS AT THIS MINUTE · 0 FOR SELECTED AGENT"
        )
        assert page.locator(".response-card").count() == 0
        assert page.locator("#response-empty").is_visible()
        assert page.locator(".response-state-card").count() == 5
        assert page.locator("#response-state-warning").inner_text() == (
            "NOT STATE AT THE SCRUBBED MINUTE"
        )

        person_five = page.locator("#people-list .person").nth(4)
        person_five.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#selected-id")).to_have_text("PERSON-005")
        expect(page.locator(".response-state-card").first).to_contain_text("PERSON-005")
        assert page.locator(".response-state-card").count() == 5
        assert page.locator(".response-state-card").filter(has_text="0 RESPONSES").count() == 5
        assert any("agent_id=person-005" in url for url in response_state_requests)

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
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        response_box = page.locator("#response-panel").bounding_box()
        assert response_box is not None and response_box["width"] <= 390
        narrow_screenshot = tmp_path / "city-spatial-response-viewer-narrow.png"
        page.screenshot(path=narrow_screenshot, full_page=True)
        assert narrow_screenshot.stat().st_size > 25_000
        assert response_evaluation.model_dump(mode="json") == response_before
        assert not external_requests
        assert not console_errors
        assert not page_errors
        browser.close()


def test_response_viewer_ignores_a_superseded_request_failure_during_playback() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _), sync_playwright() as playwright:
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
              document.documentElement.dataset.staleFailureStarted = 'false';
              document.documentElement.dataset.staleFailureSettled = 'false';
              document.documentElement.dataset.newerFrameCompleted = 'false';
              window.fetch = (...args) => {
                const url = String(args[0]);
                if (
                  document.documentElement.dataset.staleFailureStarted === 'false'
                  && url.includes('/api/frame?minute=5')
                ) {
                  document.documentElement.dataset.staleFailureStarted = 'true';
                  return new Promise((_, reject) => setTimeout(() => {
                    document.documentElement.dataset.staleFailureSettled = 'true';
                    reject(new Error('superseded request failed'));
                  }, 500));
                }
                const result = originalFetch(...args);
                if (url.includes('/api/frame?minute=2')) {
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

    assert snapshot["playLabel"] == "Pause timeline"
    assert snapshot["errorHidden"] is True
    assert "superseded request failed" not in snapshot["errorText"]
    assert int(snapshot["committedMinute"]) >= 2


def test_response_viewer_exposes_compact_accessible_status_and_keyboard_provenance() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _), sync_playwright() as playwright:
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
            """element => ({
              active: document.activeElement === element,
              style: getComputedStyle(element).outlineStyle,
              width: getComputedStyle(element).outlineWidth,
              color: getComputedStyle(element).outlineColor,
            })"""
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
              sliderValueText: document.getElementById('time-slider').getAttribute(
                'aria-valuetext'
              ),
            })"""
        )
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
        or person_focus["color"] != "rgb(223, 182, 107)"
    ):
        violations.append(f"person focus indicator is not high contrast: {person_focus!r}")
    if semantics["listLiveRegions"] != [None, None, None, None]:
        violations.append(f"large lists are live regions: {semantics!r}")
    expected_status = {"role": "status", "live": "polite", "atomic": "true"}
    if semantics["compactStatuses"] != [expected_status] * 4:
        violations.append(f"compact statuses are not atomic live regions: {semantics!r}")
    if semantics["sliderValueText"] != "Day 1, 00:00":
        violations.append(f"timeline lacks human-readable value text: {semantics!r}")
    assert not violations, "\n".join(violations)


def test_response_viewer_busy_minute_is_legible_and_contained_at_narrow_width() -> None:
    browser_path = _installed_browser()
    with _response_viewer() as (base_url, _, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")

        assert page.locator(".response-card").count() == 100
        assert page.locator("#response-page-note").is_visible()
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
    assert layout["cardsContained"] is True
