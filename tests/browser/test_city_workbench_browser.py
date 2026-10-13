from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
_LONG_BROWSER_TEST = pytest.mark.skipif(
    os.environ.get("ADLIFE_SKIP_LONG_TESTS") == "1" or sys.gettrace() is not None,
    reason="long browser workloads require an uninstrumented timing gate",
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


def _artifact_hashes(directory: Path) -> dict[str, str]:
    """Snapshot every persisted byte without depending on filesystem metadata."""

    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _persona_population(page):
    return page.locator("#persona-editor").evaluate("root => root.editor.serialize()")


def _set_audience_count(page, count: int) -> None:
    """Explicitly edit counts; changing run settings must never rewrite personas."""
    page.locator("#stage-campaign").click()
    page.locator("#persona-count").fill(str(count))
    page.locator("#campaign-continue").click()


def _publish_v7_inspector_fixture(page, root: Path):
    """Create an explicitly legacy inspector fixture, independent of authoring jobs."""
    from adlife.city.runs import create_city_run
    from adlife.city.workbench_transport import parse_workbench_http_draft
    from adlife.city.workbench_validation import construct_workbench_run

    captured = []

    def capture(route: Route) -> None:
        captured.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/scenarios/validate", capture)
    page.locator("#validate-run").click()
    expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
    page.unroute("**/api/scenarios/validate", capture)
    payload = captured[-1]
    assert payload["schema_version"] == 2
    population = payload.pop("population")
    assert len(population["personas"]) == 1
    persona = population["personas"][0]
    payload["schema_version"] = 1
    payload["cohort"] = {key: persona[key] for key in ("interests", "traits", "initial_state")}
    validated = construct_workbench_run(parse_workbench_http_draft(payload))
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
    assert stored.manifest.schema_version == 7
    return stored


def test_audience_templates_preserve_causal_values_and_submit_schema_two(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            expect(page.locator("#persona-disclosure")).to_contain_text("MBTI")
            page.locator("#persona-template").select_option("persona-004")
            page.locator("#persona-add").click()
            page.locator("#persona-remove").click()
            page.locator("#persona-template").select_option("persona-002")
            page.locator("#persona-add").click()
            before = _persona_population(page)["personas"][1]
            expect(page.locator("#persona-id")).to_have_text("persona-002")
            assert page.locator("input[name='persona_id']").count() == 0
            page.locator("#persona-display-name").fill("شخصیت خیالی")
            page.locator("#persona-description summary").click()
            page.locator("#persona-occupation").fill("")
            page.locator("#persona-age").fill("")
            page.locator("#persona-background").fill("")
            page.locator("#persona-mbti").select_option("")
            after = _persona_population(page)["personas"][1]
            assert after["interests"] == ["books", "cooking"]
            assert after["traits"] == before["traits"]
            assert after["initial_state"] == before["initial_state"]
            assert all(after[key] is None for key in ("age", "occupation", "background", "mbti"))
            assert after["fictional"] is True
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("persona-browser")
            page.locator("#persona-use-total").click()
            posted = []
            page.on(
                "request",
                lambda req: (
                    posted.append(json.loads(req.post_data))
                    if req.method == "POST" and req.url.endswith(("/validate", "/api/jobs"))
                    else None
                ),
            )
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#stage-campaign").click()
            page.locator("#persona-display-name").fill("شخصیت تازه خیالی")
            expect(page.locator("#start-run")).to_be_disabled()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            assert posted[-1] == posted[-2]
            assert posted[-1]["schema_version"] == 2
            assert "cohort" not in posted[-1]
            assert posted[-1]["population"]["personas"][1]["interests"] == ["books", "cooking"]
            manifest = json.loads((root / "city-runs/persona-browser/run.json").read_text())
            assert manifest["schema_version"] == 8
        finally:
            browser.close()
            playwright.stop()


def test_audience_ids_bounds_deep_copy_and_stable_serialization(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            expect(page.locator("#persona-remove")).to_be_disabled()
            page.locator("#persona-count").fill("1")
            page.locator("#persona-template").select_option("persona-001")
            page.locator("#persona-add").click()
            expect(page.locator("#persona-id")).to_have_text("persona-002")
            page.locator("#persona-duplicate").click()
            expect(page.locator("#persona-id")).to_have_text("persona-003")
            page.locator("#persona-trait-price-sensitivity").fill("0.12")
            assert _persona_population(page)["personas"][1]["traits"]["price_sensitivity"] == 0.65
            page.locator("[data-persona-id='persona-002']").click()
            page.locator("#persona-remove").click()
            page.locator("#persona-add").click()
            assert [p["persona_id"] for p in _persona_population(page)["personas"]] == [
                "persona-001",
                "persona-003",
                "persona-002",
            ]
            snapshot = _persona_population(page)
            assert _persona_population(page) == snapshot
            page.locator("#persona-editor").evaluate(
                "root => { while(root.editor.serialize().personas.length < 30) "
                "root.editor.add('persona-001'); }"
            )
            expect(page.locator("#persona-roster button")).to_have_count(30)
            expect(page.locator("#persona-add")).to_be_disabled()
            expect(page.locator("#persona-duplicate")).to_be_disabled()
            expect(page.locator("#persona-detail")).to_have_count(1)
            assert (
                page.locator("#persona-editor").evaluate("root => root.editor.add('persona-001')")
                is False
            )
            assert len(_persona_population(page)["personas"]) == 30
            page.locator("#persona-roster button").last.click()
            expect(page.locator("#persona-id")).to_have_text("persona-030")
            page.locator("#campaign-continue").click()
            page.locator("#persona-use-total").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
        finally:
            browser.close()
            playwright.stop()


def test_audience_roster_previews_canonical_agent_assignments_without_renumbering(
    tmp_path: Path,
) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()

            first = page.locator("[data-persona-id='persona-001']")
            expect(first.locator(".persona-row-mbti")).to_have_text("ENFP")
            expect(first.locator(".persona-row-agents")).to_have_text("person-001 to person-020")

            page.locator("#persona-count").fill("2")
            expect(first.locator(".persona-row-agents")).to_have_text("person-001 to person-002")
            page.locator("#persona-template").select_option("persona-002")
            page.locator("#persona-add").click()
            second = page.locator("[data-persona-id='persona-002']")
            expect(second.locator(".persona-row-mbti")).to_have_text("ISTJ")
            expect(second.locator(".persona-row-agents")).to_have_text("person-003")

            page.locator("#persona-duplicate").click()
            third = page.locator("[data-persona-id='persona-003']")
            expect(third.locator(".persona-row-agents")).to_have_text("person-004")
            second.click()
            page.locator("#persona-remove").click()
            expect(third.locator(".persona-row-agents")).to_have_text("person-003")

            page.locator("#persona-add").click()
            assert [p["persona_id"] for p in _persona_population(page)["personas"]] == [
                "persona-001",
                "persona-003",
                "persona-002",
            ]
            expect(
                page.locator("[data-persona-id='persona-002'] .persona-row-agents")
            ).to_have_text("person-003")
            expect(third.locator(".persona-row-agents")).to_have_text("person-004")
            page.locator("#persona-description summary").click()
            page.locator("#persona-mbti").select_option("")
            expect(page.locator("[data-persona-id='persona-002'] .persona-row-mbti")).to_have_count(
                0
            )
            assert [p["persona_id"] for p in _persona_population(page)["personas"]] == [
                "persona-001",
                "persona-003",
                "persona-002",
            ]
        finally:
            browser.close()
            playwright.stop()


def test_count_mismatch_never_rewrites_audience_and_blank_count_stays_invalid(
    tmp_path: Path,
) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            before = _persona_population(page)
            page.locator("#campaign-continue").click()
            page.locator("#agent-count").fill("3")
            assert _persona_population(page) == before
            expect(page.locator("#persona-count-status")).to_contain_text("20")
            page.locator("#persona-use-total").click()
            expect(page.locator("#agent-count")).to_have_value("20")
            page.locator("#stage-campaign").click()
            page.locator("#persona-count").fill("")
            assert _persona_population(page)["personas"][0]["count"] is None
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#panel-campaign")).to_be_visible()
            expect(page.locator("#persona-count")).to_be_focused()
            expect(page.locator("#persona-count-error")).not_to_be_empty()
        finally:
            browser.close()
            playwright.stop()


@pytest.mark.parametrize(
    "field,control",
    [
        ("background", "persona-background"),
        ("interests", "persona-interests"),
        ("traits.mobile_recall_encoding", "persona-trait-mobile-recall-encoding"),
        ("initial_state.purchase_intention", "persona-initial-purchase-intention"),
    ],
)
def test_indexed_persona_errors_reveal_exact_field_without_reflecting_values(
    tmp_path: Path,
    field: str,
    control: str,
) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#persona-count").fill("19")
            page.locator("#persona-add").click()
            page.locator("[data-persona-id='persona-001']").click()
            page.route(
                "**/api/scenarios/validate",
                lambda route: route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "message": "Refused",
                                "fields": {f"population.personas.1.{field}": "secret-unsafe-value"},
                            }
                        }
                    ),
                ),
            )
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#persona-id")).to_have_text("persona-002")
            expect(page.locator(f"#{control}")).to_be_focused()
            if field == "background":
                expect(page.locator("#persona-description")).to_have_attribute("open", "")
            assert "secret-unsafe-value" not in page.locator("body").inner_text()
        finally:
            browser.close()
            playwright.stop()


def test_audience_keyboard_small_screen_and_large_text_containment(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url, width=390)
        try:
            page.emulate_media(reduced_motion="reduce")
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#persona-template").focus()
            page.keyboard.press("Tab")
            expect(page.locator("#persona-add")).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.locator("#persona-display-name")).to_be_focused()
            page.locator("#persona-description summary").focus()
            page.keyboard.press("Enter")
            expect(page.locator("#persona-background")).to_be_visible()
            page.locator("html").evaluate("el => el.style.fontSize = '200%'")
            assert page.locator("#persona-editor").evaluate(
                "el => el.scrollWidth <= el.clientWidth"
            )
            assert (
                page.locator("#persona-add").evaluate("el => getComputedStyle(el).animationName")
                == "none"
            )
            page.locator("#persona-editor").screenshot(
                path=str(
                    Path(__file__).parents[2] / ".superpowers/sdd/"
                    "2026-10-13-city-persona-authoring/task-7-audience-390.png"
                )
            )
        finally:
            browser.close()
            playwright.stop()


@pytest.mark.parametrize("failure", ["nested-interest", "aggregate-persona"])
def test_real_persona_validation_paths_recover_adjacent_focus(
    tmp_path: Path,
    failure: str,
) -> None:
    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            if failure == "nested-interest":
                value = "invalid-interest-" + "x" * 81
                page.locator("#persona-interests").fill(value)
                path = "population.personas.0.interests.0"
                control = "#persona-interests"
                adjacent = "#persona-interests-error"
            else:
                value = "😀" * 700
                page.locator("#persona-description summary").click()
                page.locator("#persona-background").fill(value)
                page.locator("#persona-description summary").click()
                path = "population.personas.0"
                control = "#persona-detail"
                adjacent = "#persona-error"
            page.locator("#persona-add").click()
            expect(page.locator("#persona-id")).to_have_text("persona-002")
            page.locator("#campaign-continue").click()
            page.locator("#persona-use-total").click()
            with page.expect_response("**/api/scenarios/validate") as refused:
                page.locator("#validate-run").click()
            assert refused.value.status == 422
            assert path in refused.value.json()["error"]["fields"]
            expect(page.locator("#panel-campaign")).to_be_visible()
            expect(page.locator("#persona-id")).to_have_text("persona-001")
            expect(page.locator(control)).to_be_focused()
            expect(page.locator(adjacent)).not_to_be_empty()
            assert value not in page.locator(adjacent).inner_text()
            assert value not in page.locator("body").inner_text()
            if failure == "aggregate-persona":
                expect(page.locator("#persona-description")).to_have_attribute("open", "")
                page.locator("#persona-background").fill("")
            else:
                page.locator("#persona-interests").fill("custom-interest")
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            assert not (root / "city-runs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_cancellation_settings_drift_cannot_rebind_later_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = workbench_web.WorkbenchRunRepository.prepare

    def blocked_prepare(repository, validated):
        entered.set()
        if not release.wait(30):
            raise RuntimeError("cancellation binding gate timed out")
        return original(repository, validated)

    monkeypatch.setattr(workbench_web.WorkbenchRunRepository, "prepare", blocked_prepare)
    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#persona-count").fill("1")
            page.locator("#campaign-continue").click()
            page.locator("#persona-use-total").click()
            page.locator("#run-id").fill("cancel-binding")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            with page.expect_response("**/api/jobs") as accepted:
                page.locator("#start-run").click()
            queued = accepted.value.json()["job"]
            assert queued["accepted_settings"]["seed"] == "42"
            assert entered.wait(5)
            job_id = queued["job_id"]
            drifted = {**queued["accepted_settings"], "seed": "43"}
            summary = _library_summary("cancel-binding", seed="43").model_dump(mode="json")
            summary.update(run_schema_version=8, days=2, frame_count=2880)
            cancelled = False

            def drift_cancellation(route: Route) -> None:
                nonlocal cancelled
                response = route.fetch()
                document = response.json()
                assert document["accepted_settings"]["seed"] == "42"
                document["accepted_settings"] = drifted
                cancelled = True
                route.fulfill(response=response, json=document)

            def drift_completion(route: Route) -> None:
                if not cancelled:
                    route.continue_()
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"X-AdLife-Test-Completed": "1"},
                    body=json.dumps(
                        {
                            **queued,
                            "accepted_settings": drifted,
                            "phase": "completed",
                            "can_cancel": False,
                            "result": summary,
                        }
                    ),
                )

            page.route(f"**/api/jobs/{job_id}/cancel", drift_cancellation)
            page.route(f"**/api/jobs/{job_id}", drift_completion)
            with page.expect_response(
                lambda response: response.headers.get("x-adlife-test-completed") == "1"
            ):
                page.locator("#cancel-job").click()
            page.wait_for_function("""() => !document.querySelector('#error-banner').hidden ||
                !document.querySelector('#job-inspect-link').hidden""")
            expect(page.locator("#error-banner")).to_contain_text("incompatible")
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            expect(page.locator("#job-settings")).to_contain_text("42")
            assert "43" not in page.locator("#job-settings").inner_text()
            assert not (root / "city-runs/cancel-binding/run.json").exists()
        finally:
            release.set()
            browser.close()
            playwright.stop()


@pytest.mark.parametrize("input_version,run_version", [(1, 7), (2, 8)])
def test_exact_completed_schema_pairs_resume_from_server_authority(
    tmp_path: Path, input_version: int, run_version: int
) -> None:
    from tests.security.test_workbench_browser_security import _job_documents

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            settings, job = _job_documents()
            summary = _library_summary("city-study-001").model_dump(mode="json")
            settings.update(
                workbench_input_schema_version=input_version,
                run_schema_version=run_version,
                days=1,
                agent_count=1,
            )
            summary["run_schema_version"] = run_version
            job.update(phase="completed", can_cancel=False, result=summary)

            def active(route: Route) -> None:
                response = route.fetch()
                document = response.json()
                document["active_job"] = job
                route.fulfill(response=response, json=document)

            page.route("**/api/workbench", active)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#stage-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible()
            expect(page.locator("#job-inspect-link")).to_have_attribute(
                "href", "/runs/city-study-001"
            )
            expect(page.locator("#error-banner")).to_be_hidden()
        finally:
            browser.close()
            playwright.stop()


def test_hostile_persona_text_is_inert_and_interests_are_unfiltered(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            hostile = '<img src="https://outside.invalid/x" onerror="window.personaExecuted=true">'
            page.locator("#persona-display-name").fill(hostile)
            page.locator("#persona-interests").fill("crafts\nکتاب\ncustom-interest")  # noqa: RUF001
            assert _persona_population(page)["personas"][0]["interests"] == [
                "crafts",
                "کتاب",
                "custom-interest",
            ]
            expect(page.locator("#persona-roster")).to_contain_text(hostile)
            assert page.locator("#persona-editor img").count() == 0
            assert page.evaluate("window.personaExecuted === undefined")
            assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            assert page.locator("#persona-editor img").count() == 0
            assert page.evaluate("window.personaExecuted === undefined")
        finally:
            browser.close()
            playwright.stop()


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

            expect(page.get_by_role("heading", name="Choose a city", exact=True)).to_be_visible()
            expect(page.locator("#city-details")).not_to_have_attribute("open", "")
            expect(page.locator("#stage-city")).to_have_attribute("aria-current", "step")
            expect(page.locator("#city-select")).to_have_value("fictional-grid-v2")
            expect(page.locator("#city-name")).to_have_text("Fictional Grid City V2")
            expect(page.locator("#city-time-zone")).to_have_text("Etc/UTC")
            expect(page.locator("#city-attribution")).to_contain_text("Fictional demonstration")
            expect(page.locator("#city-omissions li")).to_have_count(3)
            expect(page.locator("#city-map-alternative")).to_contain_text("12 roads")
            expect(page.locator("#city-map-alternative")).to_contain_text("9 nodes")
            expect(page.locator("#provider-boundary")).to_contain_text(
                "This city workflow does not call an AI provider"
            )
            expect(page.locator(".map-column")).to_be_visible()
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            expect(page.locator(".map-column")).to_be_hidden()
            page.locator("#stage-inspect").click()
            expect(page.locator(".map-column")).to_be_hidden()
            page.locator("#stage-city").click()
            expect(page.locator(".map-column")).to_be_visible()
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
            page.locator("#persona-trait-novelty-seeking").fill("0.8")
            page.locator("#persona-initial-brand-sentiment").fill("-0.2")

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
            assert (
                request_documents[2]["population"]["personas"][0]["traits"]["novelty_seeking"]
                == 0.8
            )
            assert (
                request_documents[2]["population"]["personas"][0]["initial_state"][
                    "brand_sentiment"
                ]
                == -0.2
            )
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
                expect(page.locator("#map-status")).to_have_text("Verified pack")
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

                page.locator("#stage-campaign").click()
                page.locator("#persona-display-name").fill("Revised fictional persona")
                page.locator("#campaign-continue").click()
                release.set()
                expect(page.locator("#validate-run")).to_be_enabled(timeout=10_000)
                expect(page.locator("#validation-review")).to_be_hidden()
                expect(page.locator("#validation-state")).to_contain_text("revised draft")

                page.locator("#validate-run").click()
                expect(page.locator("#validation-review")).to_be_visible(timeout=10_000)
                expect(page.locator("#review-seed")).to_have_text("42")
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_persona_edit_during_job_acceptance_cannot_unlock_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = workbench_web._construct_validated_run
    calls = 0

    def block_submission(payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            if not release.wait(15):
                raise RuntimeError("job acceptance gate timed out")
        return original(payload)

    monkeypatch.setattr(workbench_web, "_construct_validated_run", block_submission)
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#persona-count").fill("1")
            page.locator("#campaign-continue").click()
            page.locator("#persona-use-total").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()
            assert entered.wait(5)
            page.locator("#stage-campaign").click()
            page.locator("#persona-display-name").fill("A revised fictional character")
            with page.expect_response("**/api/jobs"):
                release.set()
            page.wait_for_load_state("networkidle")
            expect(page.locator("#job-panel")).to_be_hidden()
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            expect(page.locator("#start-run")).to_be_disabled()
        finally:
            release.set()
            browser.close()
            playwright.stop()


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
            _set_audience_count(page, 1)
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
            from adlife.city.run_store import CityRunStore

            stored = CityRunStore(root).load("browser-complete")
            assert stored.manifest.schema_version == 8
            assert stored.workbench_input.schema_version == 2

            page.locator("#stage-inspect").click()
            expect(page.locator("#run-library")).to_contain_text("browser-complete")
            expect(page.locator("#run-library a[href='/runs/browser-complete']")).to_be_visible()
        finally:
            browser.close()
            playwright.stop()


def test_v8_inspector_accepts_authoritative_persona_at_inclusive_byte_limit(tmp_path: Path):
    from adlife.city.run_store import CityRunStore
    from adlife.city.runs import create_city_run
    from adlife.city.workbench_input import WorkbenchRunDraftV2
    from adlife.city.workbench_validation import construct_workbench_run
    from adlife.core.domain.serialization import canonical_json
    from tests.unit.city.test_workbench_input import draft_v2_data

    payload = draft_v2_data(agent_count=1)
    persona = payload["population"]["personas"][0]
    persona["traits"] = dict.fromkeys(persona["traits"], 1e-6)
    persona["initial_state"] = dict.fromkeys(persona["initial_state"], 1e-6)
    persona["background"] = ""
    quotient, remainder = divmod(3072 - len(canonical_json(persona).encode("utf-8")), 4)
    persona["background"] = "\U0001f600" * quotient + "a" * remainder
    assert len(canonical_json(persona).encode("utf-8")) == 3072
    validated = construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(json.dumps(payload))
    )
    settings = validated.workbench_input.draft.settings

    with _workbench(tmp_path) as (base_url, root):
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
        assert stored.manifest.schema_version == 8
        verified = CityRunStore(root).load(settings.run_id)
        frozen = verified.workbench_input.draft.population.personas[0]
        assert len(canonical_json(frozen).encode("utf-8")) == 3072
        before = _artifact_hashes(stored.directory)
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            projected_bytes = page.evaluate(
                "value => new TextEncoder().encode(JSON.stringify(value)).length",
                frozen.model_dump(mode="json"),
            )
            assert projected_bytes == 3099
            page.goto(f"{base_url}/runs/{settings.run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=15_000)
            expect(page.locator("#people-list .person")).to_have_count(1)
            page.locator("#people-list .person").click()
            page.locator("#identity-context summary").click()
            expect(page.locator("#identity-background")).to_have_text(persona["background"])
            assert _artifact_hashes(stored.directory) == before
        finally:
            browser.close()
            playwright.stop()


def test_v8_inspector_switches_verified_persona_response_inputs_offline(tmp_path: Path):
    from adlife.city.runs import create_city_run
    from adlife.city.workbench_input import WorkbenchRunDraftV2
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_input import draft_v2_data, persona_data

    payload = draft_v2_data(agent_count=2)
    first = payload["population"]["personas"][0]
    first.update(
        {
            "count": 1,
            "display_name": "Fictional Alpha",
            "background": "Alpha's noncausal fictional biography.",
            "mbti": "ENFP",
            "interests": [
                '<img src="https://outside.invalid/input" onerror="window.inputExecuted=true">',
                "alpha-interest",
            ],
            "traits": {
                "price_sensitivity": 0.11,
                "novelty_seeking": 0.22,
                "advertising_skepticism": 0.33,
                "mobile_recall_encoding": 0.44,
                "roadside_recall_encoding": 0.55,
                "impulsivity": 0.66,
            },
            "initial_state": {
                "brand_sentiment": -0.25,
                "recall_strength": 0.15,
                "purchase_intention": 0.35,
            },
        }
    )
    second = persona_data(persona_id="persona-002", count=1)
    second.update(
        {
            "display_name": "Fictional Beta",
            "background": "Beta's noncausal fictional biography.",
            "mbti": "ISTJ",
            "interests": ["beta-interest", "food"],
            "traits": {
                "price_sensitivity": 0.71,
                "novelty_seeking": 0.72,
                "advertising_skepticism": 0.73,
                "mobile_recall_encoding": 0.74,
                "roadside_recall_encoding": 0.75,
                "impulsivity": 0.76,
            },
            "initial_state": {
                "brand_sentiment": 0.45,
                "recall_strength": 0.65,
                "purchase_intention": 0.85,
            },
        }
    )
    payload["population"]["personas"] = [second, first]
    validated = construct_workbench_run(
        WorkbenchRunDraftV2.model_validate_json(json.dumps(payload))
    )
    settings = validated.workbench_input.draft.settings

    with _workbench(tmp_path) as (base_url, root):
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
        before = _artifact_hashes(stored.directory)
        external_requests: list[str] = []
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:

            def keep_browser_offline(route: Route) -> None:
                if route.request.url.startswith(base_url):
                    route.continue_()
                else:
                    external_requests.append(route.request.url)
                    route.abort()

            page.route("**/*", keep_browser_offline)
            page.goto(f"{base_url}/runs/{settings.run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=15_000)
            expect(page.locator("#identity-response-inputs")).to_have_count(1)
            expect(page.locator("#identity-response-inputs")).to_be_visible()
            expect(page.locator("#identity-response-label")).to_have_text("Verified causal inputs")
            expect(page.locator("#selected-name")).to_have_text("Fictional Alpha")
            expect(page.locator("#identity-background")).to_have_text(
                "Alpha's noncausal fictional biography."
            )
            expect(page.locator("#identity-context")).not_to_have_attribute("open", "")
            expect(page.locator("#identity-interests")).to_contain_text("alpha-interest")
            for field, value in first["traits"].items():
                expect(page.locator(f"#identity-trait-{field.replace('_', '-')}")).to_have_text(
                    str(value)
                )
            for field, value in first["initial_state"].items():
                expect(page.locator(f"#identity-initial-{field.replace('_', '-')}")).to_have_text(
                    str(value)
                )
            assert page.locator("#identity-response-inputs img").count() == 0
            assert page.evaluate("window.inputExecuted === undefined")

            page.locator("#people-list .person").nth(1).click()
            expect(page.locator("#selected-name")).to_have_text("Fictional Beta")
            expect(page.locator("#identity-background")).to_have_text(
                "Beta's noncausal fictional biography."
            )
            expect(page.locator("#identity-interests")).to_have_text("beta-interest · food")
            for field, value in second["traits"].items():
                expect(page.locator(f"#identity-trait-{field.replace('_', '-')}")).to_have_text(
                    str(value)
                )
            for field, value in second["initial_state"].items():
                expect(page.locator(f"#identity-initial-{field.replace('_', '-')}")).to_have_text(
                    str(value)
                )
            assert external_requests == []
            assert _artifact_hashes(stored.directory) == before
        finally:
            browser.close()
            playwright.stop()


@pytest.mark.parametrize("first_persona", [False, True])
def test_later_persona_field_errors_are_not_silent_or_unsafe(tmp_path: Path, first_persona):
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            first_path = "population.personas.0.display_name" if first_persona else "settings.seed"
            first_control = "#persona-display-name" if first_persona else "#seed"
            unsafe = '<img src="https://outside.invalid/x" onerror="window.errorExecuted=true">'
            page.route(
                "**/api/scenarios/validate",
                lambda route: route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "code": "validation-error",
                                "message": "Study fields were refused.",
                                "fields": {
                                    first_path: "Review this value.",
                                    "population.personas.0.background": unsafe,
                                },
                            }
                        }
                    ),
                ),
            )
            page.locator("#validate-run").click()
            expect(page.locator(first_control)).to_be_focused()
            expect(page.locator("#error-banner-message")).to_contain_text(
                "Additional persona fields were refused"
            )
            assert unsafe not in page.locator("body").inner_text()
            assert page.locator("#error-banner img").count() == 0
            assert page.evaluate("window.errorExecuted === undefined")
            if first_persona:
                expect(page.locator("#persona-display-name-error")).not_to_be_empty()
        finally:
            browser.close()
            playwright.stop()


def test_v7_inspector_sends_one_run_bound_snapshot_token_for_all_scoped_reads(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    run_id = "browser-snapshot"
    scoped_requests: list[object] = []

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.on(
                "request",
                lambda request: (
                    scoped_requests.append(request)
                    if request.url.startswith(f"{base_url}/api/runs/{run_id}/")
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill(run_id)
            page.locator("#agent-count").fill("1")
            _set_audience_count(page, 1)
            _publish_v7_inspector_fixture(page, root)
            page.goto(f"{base_url}/runs/{run_id}")
            page.wait_for_url(f"{base_url}/runs/{run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=30_000)
            expect(page.locator("#identity-response-inputs")).to_have_count(1)
            expect(page.locator("#identity-response-inputs")).to_be_hidden()
            page.locator("#assumptions-drawer > summary").click()
            expect(page.locator("#assumptions-cohort-section")).to_be_visible()
            page.locator("#time-slider").fill("480")
            expect(page.locator("#clock-label")).to_have_text("08:00", timeout=15_000)

            assert scoped_requests
            tokens = {request.headers.get("x-adlife-view-token") for request in scoped_requests}
            assert len(tokens) == 1
            token = tokens.pop()
            assert token is not None
            assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
            assert all(token not in request.url for request in scoped_requests)
        finally:
            browser.close()
            playwright.stop()


@_LONG_BROWSER_TEST
def test_browser_drives_combined_study_and_inspection_never_changes_artifacts(
    tmp_path: Path,
) -> None:
    """Explicit V7 inspector compatibility; historical node ID is a stable CI selector."""
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    run_id = "browser-combined-study"

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:

            def keep_browser_offline(route: Route) -> None:
                if route.request.url.startswith(base_url):
                    route.continue_()
                else:
                    external_requests.append(route.request.url)
                    route.abort()

            page.route("**/*", keep_browser_offline)
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(base_url, wait_until="networkidle")

            page.locator("#city-continue").click()
            page.locator("#scenario-name").fill("Browser combined city study")
            page.locator("#campaign-name").fill("Browser phone and roadside campaign")
            page.locator("input[name='phone-activity'][value='home']").check()
            page.locator("input[name='phone-activity'][value='work']").check()
            page.locator("#phone-probability").fill("1")
            page.locator("#roadside-enabled").check()
            page.locator("#road-id").select_option("middle-east")
            page.locator("#road-direction").select_option("backward")
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill(run_id)
            page.locator("#agent-count").fill("2")
            _set_audience_count(page, 2)
            page.locator("#days").fill("2")
            page.locator("#seed").fill("9223372036854775807")
            page.locator("#validate-run").click()

            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            expect(page.locator("#review-placements")).to_have_text(
                "mobile-feed + roadside-billboard"
            )
            page.locator("#validation-review .technical-details summary").click()
            review_hashes = {
                "workbench-input": page.locator("#review-input-hash").inner_text(),
                "city": page.locator("#review-city-hash").inner_text(),
                "scenario": page.locator("#review-scenario-hash").inner_text(),
                "creative": page.locator("#review-creative-hash").inner_text(),
            }
            expect(page.locator("#review-seed")).to_have_text("9223372036854775807")
            stored = _publish_v7_inspector_fixture(page, root)
            review_hashes["workbench-input"] = stored.manifest.workbench_input_sha256
            run_directory = root / "city-runs" / run_id
            before_inspection = _artifact_hashes(run_directory)
            assert "run.json" in before_inspection
            assert "inputs/workbench.json" in before_inspection
            assert len(before_inspection) >= 10

            page.goto(f"{base_url}/runs/{run_id}")
            page.wait_for_url(f"{base_url}/runs/{run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=30_000)
            page.locator("#assumptions-drawer summary").click()
            expect(page.locator("#assumptions-scenario")).to_have_text(
                "Browser combined city study"
            )
            expect(page.locator("#assumptions-campaign")).to_have_text(
                "Browser phone and roadside campaign"
            )
            expect(page.locator("#assumptions-seed")).to_have_text("9223372036854775807")
            expect(page.locator("#assumptions-population")).to_have_text("2 SYNTHETIC AGENTS")
            expect(page.locator("#assumptions-days")).to_have_text("2 MODEL DAYS")
            expect(page.locator("#assumptions-mode")).to_have_text("DETERMINISTIC-RULES")
            placement_text = page.locator("#assumptions-placements").inner_text()
            assert all(
                token in placement_text
                for token in ("PHONE", "ROADSIDE", "MIDDLE-EAST", "BACKWARD")
            )
            assert (
                page.locator("#assumptions-manifest-sha256").inner_text()
                == (before_inspection["run.json"])
            )
            for name, expected_hash in review_hashes.items():
                assert page.locator(f"#assumptions-{name}-sha256").inner_text() == expected_hash

            page.locator("#time-slider").fill("480")
            expect(page.locator("#day-label")).to_have_text("DAY 01", timeout=15_000)
            expect(page.locator("#clock-label")).to_have_text("08:00")
            expect(page.locator("#people-list .person")).to_have_count(2)
            campaign_selector = page.locator("#response-metrics-campaign")
            campaign_selector.select_option("fictional-campaign")
            expect(campaign_selector).to_have_value("fictional-campaign")
            expect(campaign_selector).to_have_attribute(
                "data-causal-campaign-id", "fictional-campaign"
            )
            expect(page.locator("#map-text-summary")).to_contain_text(
                "Selected campaign FICTIONAL-CAMPAIGN."
            )
            selected_evidence = page.locator("#opportunity-list .opportunity-card").first
            expect(selected_evidence).to_be_visible(timeout=15_000)
            evidence_id = selected_evidence.get_attribute("data-evidence-id")
            assert evidence_id is not None
            selected_evidence.click()
            selected_evidence = page.locator(
                f"#opportunity-list [data-evidence-id='{evidence_id}']"
            )
            expect(selected_evidence).to_have_class(
                "opportunity-card selected causal-selected causal-related"
            )
            expect(page.locator("#map-text-summary")).to_contain_text(
                f"Selected opportunity evidence {evidence_id.upper()};"
            )
            expect(campaign_selector).to_have_value("fictional-campaign")
            second_person = page.locator("#people-list .person").nth(1)
            second_id = second_person.locator("strong").inner_text()
            second_person.click()
            expect(page.locator("#selected-id")).to_have_text(second_id, timeout=15_000)
            assert _artifact_hashes(run_directory) == before_inspection

            page.locator("#workbench-back-link").click()
            page.wait_for_url(base_url + "/")
            expect(page.get_by_role("heading", name="Choose a city", exact=True)).to_be_visible(
                timeout=15_000
            )
            page.locator("#stage-inspect").click()
            library_link = page.locator(f"#run-library a[href='/runs/{run_id}']")
            expect(library_link).to_be_visible(timeout=15_000)
            library_link.click()
            page.wait_for_url(f"{base_url}/runs/{run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=30_000)
            expect(page.locator("#assumptions-seed")).to_have_text("9223372036854775807")

            assert _artifact_hashes(run_directory) == before_inspection
            assert external_requests == []
            assert console_errors == []
            assert page_errors == []
        finally:
            browser.close()
            playwright.stop()


@_LONG_BROWSER_TEST
def test_maximum_browser_run_keeps_rendered_lists_bounded_and_responsive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit V7 maximum inspector fixture; preserve the historical CI node ID."""
    browser_path = _installed_browser()
    external_requests: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    pending_requests: set[object] = set()
    in_flight_timeline_requests: set[object] = set()
    timeline_request_started_at: dict[object, float] = {}
    timeline_request_durations: list[float] = []
    peak_in_flight_timeline_requests = 0
    timeline_request_count = 0
    expanded_opportunities: list[dict[str, object]] = []
    opportunity_page_template: dict[str, object] | None = None
    run_id = "browser-maximum-load"
    total_started = time.perf_counter()

    library_runs = tuple(
        _library_summary(f"library-run-{index:03d}", seed=str(index + 1)) for index in range(25)
    )

    def bounded_library_page(
        _repository: object,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> WorkbenchRunPage:
        selected = library_runs[offset : offset + limit]
        return WorkbenchRunPage(
            offset=offset,
            limit=limit,
            returned_count=len(selected),
            verified_total=len(library_runs),
            scanned_count=len(library_runs),
            corrupt_count=0,
            truncated=False,
            runs=selected,
        )

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "list_runs",
        bounded_library_page,
    )

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:

            def expanded_opportunity_page(offset: int) -> dict[str, object]:
                assert opportunity_page_template is not None
                page_document = dict(opportunity_page_template)
                page_items = expanded_opportunities[offset : offset + 100]
                agent_counts: dict[str, int] = {}
                for item in expanded_opportunities:
                    agent_id = item["agent_id"]
                    assert isinstance(agent_id, str)
                    agent_counts[agent_id] = agent_counts.get(agent_id, 0) + 1
                page_document.update(
                    {
                        "offset": offset,
                        "total": len(expanded_opportunities),
                        "channel_counts": {
                            "roadside-billboard": 0,
                            "mobile-feed": len(expanded_opportunities),
                        },
                        "agent_counts": agent_counts,
                        "next_offset": (
                            offset + len(page_items)
                            if offset + len(page_items) < len(expanded_opportunities)
                            else None
                        ),
                        "items": page_items,
                    }
                )
                return page_document

            def ensure_expanded_opportunities(page_document: dict[str, object]) -> None:
                nonlocal opportunity_page_template
                if expanded_opportunities:
                    return
                items = page_document["items"]
                assert isinstance(items, list)
                assert len(items) == 30
                opportunity_page_template = dict(page_document)
                for repetition in range(4):
                    for item in items:
                        assert isinstance(item, dict)
                        clone = dict(item)
                        source_id = clone["opportunity_id"]
                        assert isinstance(source_id, str)
                        clone["opportunity_id"] = sha256(
                            f"browser-page:{repetition}:{source_id}".encode()
                        ).hexdigest()
                        clone["ordinal_for_agent_placement_day"] = repetition + 1
                        expanded_opportunities.append(clone)
                assert len(expanded_opportunities) == 120

            def keep_browser_offline(route: Route) -> None:
                url = route.request.url
                if not url.startswith(base_url):
                    external_requests.append(url)
                    route.abort()
                    return
                parsed = urlparse(url)
                query = parse_qs(parsed.query)
                timeline_path = f"/api/runs/{run_id}/timeline"
                opportunity_path = f"/api/runs/{run_id}/opportunities"
                if parsed.path == timeline_path and query.get("minute") == ["0"]:
                    response = route.fetch()
                    document = response.json()
                    assert isinstance(document, dict)
                    page_document = document["opportunity_page"]
                    assert isinstance(page_document, dict)
                    ensure_expanded_opportunities(page_document)
                    offset_values = query.get("opportunity_offset")
                    assert offset_values is not None and len(offset_values) == 1
                    document["opportunity_page"] = expanded_opportunity_page(int(offset_values[0]))
                    route.fulfill(response=response, json=document)
                    return
                if parsed.path == opportunity_path and query.get("minute") == ["0"]:
                    response = route.fetch()
                    offset_values = query.get("offset")
                    assert offset_values is not None and len(offset_values) == 1
                    route.fulfill(
                        response=response,
                        json=expanded_opportunity_page(int(offset_values[0])),
                    )
                    return
                route.continue_()

            def request_started(request: object) -> None:
                nonlocal peak_in_flight_timeline_requests, timeline_request_count
                if str(getattr(request, "url", "")).startswith(base_url):
                    pending_requests.add(request)
                path = urlparse(str(getattr(request, "url", ""))).path
                if path == f"/api/runs/{run_id}/timeline":
                    timeline_request_count += 1
                    in_flight_timeline_requests.add(request)
                    timeline_request_started_at[request] = time.perf_counter()
                    peak_in_flight_timeline_requests = max(
                        peak_in_flight_timeline_requests,
                        len(in_flight_timeline_requests),
                    )

            def request_finished(request: object) -> None:
                pending_requests.discard(request)
                started_at = timeline_request_started_at.pop(request, None)
                if started_at is not None:
                    timeline_request_durations.append(time.perf_counter() - started_at)
                in_flight_timeline_requests.discard(request)

            page.route("**/*", keep_browser_offline)
            page.on("request", request_started)
            page.on("requestfinished", request_finished)
            page.on("requestfailed", request_finished)
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(base_url, wait_until="networkidle")

            page.locator("#city-continue").click()
            page.locator("input[name='phone-activity'][value='home']").check()
            page.locator("#phone-probability").fill("1")
            page.locator("#phone-cap").fill("100")
            page.locator("#phone-window-0-start").fill("00:00")
            page.locator("#phone-window-0-end").fill("00:01")
            page.locator("#phone-windows .remove-window").nth(1).click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill(run_id)
            page.locator("#agent-count").fill("30")
            _set_audience_count(page, 30)
            page.locator("#days").fill("7")
            page.locator("#seed").fill("9223372036854775807")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)

            run_started = time.perf_counter()
            _publish_v7_inspector_fixture(page, root)
            run_elapsed = time.perf_counter() - run_started
            run_directory = root / "city-runs" / run_id
            before_inspection = _artifact_hashes(run_directory)
            assert len(before_inspection) >= 10
            inspector_started = time.perf_counter()
            page.goto(f"{base_url}/runs/{run_id}")
            page.wait_for_url(f"{base_url}/runs/{run_id}")
            expect(page.locator("#assumptions-status")).to_have_text("Verified", timeout=60_000)
            expect(page.locator("#people-list .person")).to_have_count(30, timeout=60_000)
            expect(page.locator("#opportunity-list")).to_be_visible(timeout=60_000)
            inspector_ready_elapsed = time.perf_counter() - inspector_started
            evidence_dom_counts = {
                "opportunity": page.locator("#opportunity-list > *").count(),
                "attention": page.locator("#attention-list > *").count(),
                "response": page.locator("#response-list > *").count(),
            }
            assert evidence_dom_counts["opportunity"] == 100
            assert evidence_dom_counts["attention"] <= 100
            assert evidence_dom_counts["response"] <= 100
            expect(page.locator("#opportunity-page-note")).to_have_text("Records 1-100 of 120")
            pagination_started = time.perf_counter()
            page.locator("#opportunity-page-next").click()
            expect(page.locator("#opportunity-page-note")).to_have_text(
                "Records 101-120 of 120", timeout=60_000
            )
            assert page.locator("#opportunity-list .opportunity-card").count() == 20
            page.locator("#opportunity-page-previous").click()
            expect(page.locator("#opportunity-page-note")).to_have_text(
                "Records 1-100 of 120", timeout=60_000
            )
            assert page.locator("#opportunity-list .opportunity-card").count() == 100
            evidence_pagination_elapsed = time.perf_counter() - pagination_started

            last_person = page.locator("#people-list .person").nth(29)
            last_id = last_person.locator("strong").inner_text()
            selection_started = time.perf_counter()
            last_person.click()
            expect(page.locator("#selected-id")).to_have_text(last_id, timeout=60_000)
            selection_elapsed = time.perf_counter() - selection_started
            day_seven_started = time.perf_counter()
            page.locator("#time-slider").fill("10079")
            expect(page.locator("#day-label")).to_have_text("DAY 07", timeout=60_000)
            expect(page.locator("#clock-label")).to_have_text("23:59")
            day_seven_elapsed = time.perf_counter() - day_seven_started
            day_one_started = time.perf_counter()
            page.locator("#time-slider").fill("0")
            expect(page.locator("#clock-label")).to_have_text("00:00", timeout=60_000)
            day_one_elapsed = time.perf_counter() - day_one_started
            playback_started = time.perf_counter()
            page.locator("#playback-speed").select_option("4")
            playback_initial_minute = int(page.locator("#time-slider").input_value())
            page.locator("#play-button").click()
            expect(page.locator("#play-button")).to_have_attribute("aria-label", "Pause timeline")
            page.wait_for_function(
                "minimum => Number(document.querySelector('#time-slider').value) >= minimum",
                arg=playback_initial_minute + 3,
                timeout=60_000,
            )
            playback_final_minute = int(page.locator("#time-slider").input_value())
            page.locator("#play-button").click()
            expect(page.locator("#play-button")).to_have_attribute("aria-label", "Play timeline")
            page.wait_for_load_state("networkidle", timeout=60_000)
            playback_transition_elapsed = time.perf_counter() - playback_started
            playback_transitions = playback_final_minute - playback_initial_minute
            assert playback_transitions >= 3
            assert peak_in_flight_timeline_requests == 1

            library_started = time.perf_counter()
            page.locator("#workbench-back-link").click()
            page.wait_for_url(base_url + "/")
            expect(page.locator("#run-library .run-card")).to_have_count(20, timeout=60_000)
            page.locator("#stage-inspect").click()
            expect(page.locator("#run-library .run-card").first).to_be_visible()
            expect(page.locator("#library-count")).to_have_text("25 verified runs")
            expect(page.locator("#library-page")).to_have_text("Page 1")
            first_library_count = page.locator("#run-library .run-card").count()
            page.locator("#library-next").click()
            expect(page.locator("#library-page")).to_have_text("Page 2", timeout=60_000)
            expect(page.locator("#run-library .run-card")).to_have_count(5)
            second_library_count = page.locator("#run-library .run-card").count()
            page.locator("#library-previous").click()
            expect(page.locator("#library-page")).to_have_text("Page 1", timeout=60_000)
            expect(page.locator("#run-library .run-card")).to_have_count(20)
            final_library_count = page.locator("#run-library .run-card").count()
            library_dom_counts = [
                first_library_count,
                second_library_count,
                final_library_count,
            ]
            assert library_dom_counts == [20, 5, 20]
            assert max(library_dom_counts) == 20
            page.wait_for_load_state("networkidle", timeout=60_000)
            library_elapsed = time.perf_counter() - library_started

            total_elapsed = time.perf_counter() - total_started
            print(
                f"\nmaximum workbench browser run: {run_elapsed:.2f}s worker, "
                f"{inspector_ready_elapsed:.2f}s inspector readiness, "
                f"{selection_elapsed:.2f}s agent selection, "
                f"{day_seven_elapsed:.2f}s day-7 scrub, "
                f"{day_one_elapsed:.2f}s day-1 scrub, "
                f"{evidence_pagination_elapsed:.2f}s evidence next/previous, "
                f"{playback_transition_elapsed:.2f}s for {playback_transitions} "
                "playback transitions, "
                f"{library_elapsed:.2f}s verified-library return, "
                f"{total_elapsed:.2f}s complete UI exercise; "
                f"timeline requests {timeline_request_count}, peak in-flight "
                f"{peak_in_flight_timeline_requests}, durations "
                f"{[round(duration, 2) for duration in timeline_request_durations]}; "
                f"evidence DOM {evidence_dom_counts}, library DOM {library_dom_counts}; "
                "30 agents x 7 days"
            )
            assert _artifact_hashes(run_directory) == before_inspection
            assert pending_requests == set()
            assert in_flight_timeline_requests == set()
            assert timeline_request_started_at == {}
            assert external_requests == []
            assert console_errors == []
            assert page_errors == []
            expect(page.locator("#error-banner")).to_be_hidden()
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
                _set_audience_count(page, 1)
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
                _set_audience_count(page, 1)
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
    [
        "accepted-seed-mismatch",
        "external-inspector-url",
        "encoded-inspector-url",
        "crossed-input",
        "crossed-run",
        "future-input",
        "future-run",
        "completed-run-mismatch",
        "accepted-settings-drift",
    ],
)
def test_incompatible_completed_job_never_unlocks_or_requests_inspector(
    tmp_path: Path,
    corruption: str,
) -> None:
    browser_path = _installed_browser()
    job_id = "job-" + "a" * 32
    accepted_settings = {
        "schema_version": 1,
        "workbench_input_schema_version": 2,
        "run_schema_version": 8,
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
        "run_schema_version": 8,
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
        }.get(corruption, "/runs/city-study-001"),
    }
    if corruption in ("crossed-input", "future-input"):
        accepted_settings["workbench_input_schema_version"] = (
            1 if corruption == "crossed-input" else 3
        )
    if corruption in ("crossed-run", "future-run"):
        accepted_settings["run_schema_version"] = 7 if corruption == "crossed-run" else 9
    if corruption == "completed-run-mismatch":
        mismatched_result["run_schema_version"] = 7
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
                if corruption == "accepted-settings-drift":
                    completed["accepted_settings"] = {**accepted_settings, "seed": "43"}
                    completed["result"] = {**mismatched_result, "seed": "43"}
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
