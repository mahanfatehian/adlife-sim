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
from adlife.core.simulation.city_mobility import CityMobility
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack_v2, pack_v2_data

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
