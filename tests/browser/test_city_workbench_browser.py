from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Route, expect, sync_playwright

import adlife.city.workbench_web as workbench_web
from adlife.city.catalog import CityCatalogError
from adlife.city.workbench_runs import WorkbenchRunPage, WorkbenchRunSummary
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace

_BROWSER_PATHS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)


def _installed_browser() -> Path:
    browser = next((path for path in _BROWSER_PATHS if path.is_file()), None)
    if browser is None:
        message = "no supported local Chromium browser executable is installed"
        if os.environ.get("ADLIFE_REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)
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
        raise RuntimeError("workbench browser test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("workbench browser test server did not stop")


@contextmanager
def _workbench(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="browser-workbench-token",
    )
    with _serve(app) as base_url:
        yield base_url, root


def _open_page(browser_path: Path, base_url: str, *, width: int = 1440):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        executable_path=str(browser_path),
        headless=True,
        args=["--no-first-run", "--disable-background-networking"],
    )
    page = browser.new_page(viewport={"width": width, "height": 1000})
    return playwright, browser, page


def _library_summary(run_id: str, *, seed: str = "42") -> WorkbenchRunSummary:
    return WorkbenchRunSummary(
        run_id=run_id,
        run_schema_version=7,
        city_id="fictional-grid-v2",
        city_schema_version=2,
        city_sha256="a" * 64,
        scenario_id="city-study",
        scenario_sha256="b" * 64,
        campaign_ids=("fictional-campaign",),
        channels=("mobile-feed",),
        seed=seed,
        days=1,
        agent_count=1,
        frame_count=1440,
        opportunity_count=0,
        impression_count=0,
        noticed_count=0,
        response_count=0,
        inspector_url=f"/runs/{run_id}",
    )


def _library_page(*, offset: int, run_id: str) -> WorkbenchRunPage:
    return WorkbenchRunPage(
        offset=offset,
        limit=20,
        returned_count=1,
        verified_total=21,
        scanned_count=21,
        corrupt_count=0,
        truncated=False,
        runs=(_library_summary(run_id),),
    )


def test_city_stage_boots_from_verified_local_catalog_without_external_requests(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.on(
                "request",
                lambda request: (
                    external_requests.append(request.url)
                    if not request.url.startswith(base_url)
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

            expect(
                page.get_by_role("heading", name="Choose a verified fictional city")
            ).to_be_visible()
            expect(page.locator("#stage-city")).to_have_attribute("aria-current", "step")
            expect(page.locator("#city-select")).to_have_value("fictional-grid-v2")
            expect(page.locator("#city-name")).to_have_text("Fictional Grid City V2")
            expect(page.locator("#city-time-zone")).to_have_text("Etc/UTC")
            expect(page.locator("#city-attribution")).to_contain_text("Fictional demonstration")
            expect(page.locator("#city-omissions li")).to_have_count(3)
            expect(page.locator("#city-map-alternative")).to_contain_text("12 roads")
            expect(page.locator("#city-map-alternative")).to_contain_text("9 nodes")
            expect(page.locator("#provider-boundary")).to_contain_text(
                "Provider and OAuth settings are not installed"
            )
            assert page.locator("#city-map").evaluate(
                """canvas => canvas.getContext('2d')
                  .getImageData(0, 0, canvas.width, canvas.height)
                  .data.some(value => value !== 0)"""
            )
            assert external_requests == []
            assert console_errors == []
            assert page_errors == []
        finally:
            browser.close()
            playwright.stop()


def test_maximum_seed_is_posted_as_exact_string_and_validation_writes_no_run(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    request_documents: list[dict[str, object]] = []

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:

            def inspect_validation(route: Route) -> None:
                request_documents.append(json.loads(route.request.post_data or "{}"))
                route.continue_()

            page.route("**/api/scenarios/validate", inspect_validation)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#seed").fill("9223372036854775807")
            page.locator("#validate-run").click()

            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            expect(page.locator("#review-seed")).to_have_text("9223372036854775807")
            expect(page.locator("#start-run")).to_be_enabled()
            assert request_documents[-1]["settings"]["seed"] == "9223372036854775807"
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_phone_roadside_and_combined_drafts_use_only_verified_placement_inputs(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    request_documents: list[dict[str, object]] = []

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:

            def inspect_validation(route: Route) -> None:
                request_documents.append(json.loads(route.request.post_data or "{}"))
                route.continue_()

            page.route("**/api/scenarios/validate", inspect_validation)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-name").fill("A complete fictional campaign")
            page.locator("#relative-price").fill("2.5")
            page.locator("#trait-novelty").fill("0.8")
            page.locator("#initial-sentiment").fill("-0.2")

            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=10_000)

            page.locator("#stage-campaign").click()
            page.locator("#phone-enabled").uncheck()
            page.locator("#roadside-enabled").check()
            page.locator("#road-id").select_option("middle-east")
            page.locator("#road-direction").select_option("backward")
            page.locator("#road-fraction").fill("0.75")
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=10_000)

            page.locator("#stage-campaign").click()
            page.locator("#phone-enabled").check()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=10_000)

            assert len(request_documents) == 3
            assert request_documents[0]["scenario"]["phone"] is not None
            assert request_documents[0]["scenario"]["roadside"] is None
            assert request_documents[1]["scenario"]["phone"] is None
            roadside = request_documents[1]["scenario"]["roadside"]
            assert roadside["road_id"] == "middle-east"
            assert roadside["travel_direction"] == "backward"
            assert roadside["road_fraction"] == 0.75
            assert request_documents[2]["scenario"]["phone"] is not None
            assert request_documents[2]["scenario"]["roadside"] is not None
            assert request_documents[2]["scenario"]["campaign"]["relative_price"] == 2.5
            assert request_documents[2]["cohort"]["traits"]["novelty_seeking"] == 0.8
            assert request_documents[2]["cohort"]["initial_state"]["brand_sentiment"] == -0.2
            serialized = json.dumps(request_documents, sort_keys=True)
            assert "latitude" not in serialized
            assert "longitude" not in serialized
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_validation_errors_focus_the_field_and_an_edit_invalidates_review(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#seed").fill("01")
            page.locator("#validate-run").click()

            expect(page.locator("#seed-error")).to_contain_text("whole-number seed")
            assert page.locator("#seed").evaluate("element => document.activeElement === element")

            page.locator("#seed").fill("42")
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            page.locator("#stage-campaign").click()
            page.locator("#campaign-name").fill("A revised fictional campaign")
            page.locator("#stage-run").click()

            expect(page.locator("#validation-review")).to_be_hidden()
            expect(page.locator("#start-run")).to_be_disabled()
            expect(page.locator("#validation-state")).to_contain_text("Validate the revised draft")
            expect(page.locator("#campaign-name")).to_have_value("A revised fictional campaign")
        finally:
            browser.close()
            playwright.stop()


def test_stale_city_failure_cannot_replace_a_newer_verified_city(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()
    original = workbench_web.build_workbench_city_detail

    def fail_first_detail(city_id: str):
        nonlocal calls
        with lock:
            calls += 1
            call = calls
        if call == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("stale city gate timed out")
            raise CityCatalogError("stale response must be ignored")
        return original(city_id)

    monkeypatch.setattr(workbench_web, "build_workbench_city_detail", fail_first_detail)
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url, wait_until="domcontentloaded")
                expect(page.locator("#city-select")).to_be_enabled(timeout=10_000)
                assert entered.wait(5)
                page.locator("#city-select").dispatch_event("change")
                expect(page.locator("#city-name")).to_have_text(
                    "Fictional Grid City V2", timeout=10_000
                )
                release.set()
                page.wait_for_timeout(300)

                expect(page.locator("#error-banner")).to_be_hidden()
                expect(page.locator("#city-continue")).to_be_enabled()
                expect(page.locator("#map-status")).to_have_text("PACK HASH VERIFIED")
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_edit_during_validation_prevents_the_stale_receipt_from_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()
    original = workbench_web._construct_validated_run

    def block_first_validation(payload: dict[str, object]):
        nonlocal calls
        with lock:
            calls += 1
            call = calls
        if call == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("stale validation gate timed out")
        return original(payload)

    monkeypatch.setattr(workbench_web, "_construct_validated_run", block_first_validation)
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url, wait_until="networkidle")
                page.locator("#city-continue").click()
                page.locator("#campaign-continue").click()
                page.locator("#validate-run").click()
                assert entered.wait(5)

                page.locator("#seed").fill("43")
                release.set()
                expect(page.locator("#validate-run")).to_be_enabled(timeout=10_000)
                expect(page.locator("#validation-review")).to_be_hidden()
                expect(page.locator("#validation-state")).to_contain_text("revised draft")

                page.locator("#validate-run").click()
                expect(page.locator("#validation-review")).to_be_visible(timeout=10_000)
                expect(page.locator("#review-seed")).to_have_text("43")
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_stale_library_page_cannot_replace_a_newer_verified_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    offset_twenty_calls = 0
    lock = threading.Lock()

    def controlled_list_runs(
        _repository: object,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> WorkbenchRunPage:
        nonlocal offset_twenty_calls
        assert limit == 20
        if offset == 0:
            return _library_page(offset=0, run_id="page-zero")
        assert offset == 20
        with lock:
            offset_twenty_calls += 1
            call = offset_twenty_calls
        if call == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("stale library gate timed out")
            return _library_page(offset=20, run_id="stale-page")
        return _library_page(offset=20, run_id="newer-page")

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "list_runs",
        controlled_list_runs,
    )
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url, wait_until="networkidle")
                page.locator("#stage-inspect").click()
                expect(page.locator("#run-library")).to_contain_text("page-zero")
                page.locator("#library-next").click()
                assert entered.wait(5)
                page.locator("#library-next").click()
                expect(page.locator("#run-library")).to_contain_text("newer-page", timeout=10_000)
                release.set()
                page.wait_for_timeout(300)

                expect(page.locator("#run-library")).not_to_contain_text("stale-page")
                expect(page.locator("#library-page")).to_have_text("Page 2")
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_real_job_completes_without_auto_navigation_and_enters_verified_library(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("browser-complete")
            page.locator("#agent-count").fill("1")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            original_url = page.url
            page.locator("#start-run").click()

            inspect = page.locator("#job-inspect-link")
            expect(inspect).to_be_visible(timeout=30_000)
            expect(inspect).to_have_attribute("href", "/runs/browser-complete")
            assert page.url == original_url
            assert inspect.evaluate("element => document.activeElement === element")
            assert (root / "city-runs" / "browser-complete" / "inputs" / "workbench.json").is_file()

            page.locator("#stage-inspect").click()
            expect(page.locator("#run-library")).to_contain_text("browser-complete")
            expect(page.locator("#run-library a[href='/runs/browser-complete']")).to_be_visible()
        finally:
            browser.close()
            playwright.stop()


def test_refresh_resumes_the_exact_active_job_and_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    original = workbench_web.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: object, validated: object):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("refresh resume gate timed out")
        return original(repository, validated)

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare",
        blocked_prepare,
    )
    try:
        with _workbench(tmp_path) as (base_url, root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url, wait_until="networkidle")
                page.locator("#city-continue").click()
                page.locator("#campaign-continue").click()
                page.locator("#run-id").fill("refresh-resume")
                page.locator("#agent-count").fill("1")
                page.locator("#seed").fill("9223372036854775807")
                page.locator("#validate-run").click()
                expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
                page.locator("#start-run").click()
                assert entered.wait(5)

                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#stage-run")).to_be_enabled(timeout=10_000)
                page.locator("#stage-run").click()
                expect(page.locator("#job-panel")).to_be_visible()
                expect(page.locator("#job-settings")).to_contain_text("refresh-resume")
                expect(page.locator("#job-settings")).to_contain_text("9223372036854775807")
                expect(page.locator("#job-phase")).to_have_text("EVALUATING")

                release.set()
                expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
                expect(page.locator("#job-inspect-link")).to_have_attribute(
                    "href", "/runs/refresh-resume"
                )
                assert (
                    root / "city-runs" / "refresh-resume" / "inputs" / "workbench.json"
                ).is_file()
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_cooperative_cancellation_never_presents_a_completed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    original = workbench_web.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: object, validated: object):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("cancellation gate timed out")
        return original(repository, validated)

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare",
        blocked_prepare,
    )
    try:
        with _workbench(tmp_path) as (base_url, root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url, wait_until="networkidle")
                page.locator("#city-continue").click()
                page.locator("#campaign-continue").click()
                page.locator("#run-id").fill("cancel-browser")
                page.locator("#agent-count").fill("1")
                page.locator("#validate-run").click()
                expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
                page.locator("#start-run").click()
                assert entered.wait(5)

                expect(page.locator("#cancel-job")).to_be_visible()
                page.locator("#cancel-job").click()
                expect(page.locator("#job-message")).to_contain_text("Cancellation requested")
                release.set()
                expect(page.locator("#job-phase")).to_have_text("CANCELLED", timeout=15_000)
                expect(page.locator("#job-inspect-link")).to_be_hidden()
                assert not (root / "city-runs" / "cancel-browser").exists()
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


@pytest.mark.parametrize(
    "corruption",
    ["accepted-seed-mismatch", "external-inspector-url", "encoded-inspector-url"],
)
def test_incompatible_completed_job_never_unlocks_or_requests_inspector(
    tmp_path: Path,
    corruption: str,
) -> None:
    browser_path = _installed_browser()
    job_id = "job-" + "a" * 32
    accepted_settings = {
        "schema_version": 1,
        "city_id": "fictional-grid-v2",
        "scenario_id": "city-study",
        "campaign_id": "fictional-campaign",
        "seed": "42",
        "agent_count": 20,
        "days": 2,
        "response_mode": "deterministic-rules",
    }
    queued_job = {
        "schema_version": 1,
        "job_id": job_id,
        "run_id": "city-study-001",
        "accepted_settings": accepted_settings,
        "phase": "queued",
        "created_at": "2026-10-10T00:00:00.000000Z",
        "updated_at": "2026-10-10T00:00:00.000000Z",
        "cancellation_requested": False,
        "can_cancel": True,
        "error": None,
        "result": None,
    }
    mismatched_result = {
        "schema_version": 1,
        "run_id": "city-study-001",
        "run_schema_version": 7,
        "city_id": "fictional-grid-v2",
        "city_schema_version": 2,
        "city_sha256": "a" * 64,
        "scenario_id": "city-study",
        "scenario_sha256": "b" * 64,
        "campaign_ids": ["fictional-campaign"],
        "channels": ["mobile-feed"],
        "seed": "43" if corruption == "accepted-seed-mismatch" else "42",
        "days": 2,
        "agent_count": 20,
        "frame_count": 2880,
        "opportunity_count": 0,
        "impression_count": 0,
        "noticed_count": 0,
        "response_count": 0,
        "inspector_url": {
            "accepted-seed-mismatch": "/runs/city-study-001",
            "external-inspector-url": "//outside.invalid/run",
            "encoded-inspector-url": "/runs/%2e%2e",
        }[corruption],
    }
    external_requests: list[str] = []

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.on(
                "request",
                lambda request: (
                    external_requests.append(request.url)
                    if not request.url.startswith(base_url)
                    else None
                ),
            )

            def fake_submission(route: Route) -> None:
                route.fulfill(
                    status=202,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": 1,
                            "job": queued_job,
                            "status_url": f"/api/jobs/{job_id}",
                        }
                    ),
                )

            def fake_mismatched_completion(route: Route) -> None:
                completed = {
                    **queued_job,
                    "phase": "completed",
                    "can_cancel": False,
                    "result": mismatched_result,
                }
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(completed),
                )

            page.route("**/api/jobs", fake_submission)
            page.route(f"**/api/jobs/{job_id}", fake_mismatched_completion)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()

            expect(page.locator("#error-banner")).to_be_visible(timeout=10_000)
            expect(page.locator("#error-banner")).to_contain_text("incompatible")
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            assert page.url == base_url + "/"
            assert external_requests == []
        finally:
            browser.close()
            playwright.stop()


def test_workbench_is_keyboard_legible_and_contained_at_390_pixels(tmp_path: Path) -> None:
    browser_path = _installed_browser()

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url, width=390)
        try:
            page.emulate_media(reduced_motion="reduce")
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").focus()
            snapshot = page.evaluate(
                """() => ({
                  viewport: window.innerWidth,
                  documentWidth: document.documentElement.scrollWidth,
                  active: document.activeElement.id,
                  outline: getComputedStyle(document.activeElement).outlineStyle,
                  controlHeight: document.getElementById('city-continue')
                    .getBoundingClientRect().height,
                  disclosureVisible: !!document.getElementById('synthetic-disclosure').offsetParent,
                  live: document.getElementById('workbench-live').getAttribute('aria-live'),
                  stageNames: [...document.querySelectorAll('.stage-name')]
                    .map(node => node.textContent.trim()),
                  animation: getComputedStyle(
                    document.querySelector('.runtime-pulse')
                  ).animationName,
                })"""
            )
            assert snapshot["documentWidth"] <= snapshot["viewport"]
            assert snapshot["active"] == "city-continue"
            assert snapshot["outline"] != "none"
            assert snapshot["controlHeight"] >= 44
            assert snapshot["disclosureVisible"] is True
            assert snapshot["live"] == "polite"
            assert snapshot["stageNames"] == ["CITY", "CAMPAIGN", "RUN", "INSPECT"]
            assert snapshot["animation"] == "none"
        finally:
            browser.close()
            playwright.stop()
