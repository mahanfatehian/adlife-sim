from __future__ import annotations

import re

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from tests.browser.test_city_places_browser import _installed_browser
from tests.integration.test_city_spatial_study import (
    analyze,
    definition,
    descriptive_edit,
    make_runs,
    persona_runs,
)
from tests.security.test_spatial_report_security import public_city_result
from tests.unit.reporting.test_spatial_html import report_module


def test_saved_v8_viewer_displays_response_metrics_offline_without_identity_support(tmp_path):
    from adlife.city.analysis import (
        metrics_for_stored_city_run,
        response_metrics_for_stored_city_run,
    )
    from adlife.city.web import create_city_app
    from tests.browser.test_city_places_browser import _serve
    from tests.unit.city.test_city_analysis import _artifact_bytes, _persona_run

    browser_path = _installed_browser()
    stored = _persona_run(tmp_path)
    before = _artifact_bytes(tmp_path)
    application = create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=8,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=metrics_for_stored_city_run(stored),
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        spatial_response_metrics=response_metrics_for_stored_city_run(stored),
    )
    with _serve(application) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_path), headless=True)
        page = browser.new_page()
        external, errors = [], []

        def route_request(route):
            if route.request.url.startswith(base_url + "/"):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")
        expect(page.locator("#response-metrics-panel")).to_be_visible()
        assert page.locator("#response-metrics-event-body tr").count() > 0
        assert page.locator("#response-metrics-state-body tr").count() > 0
        assert "V8" in page.locator("#saved-run-label").inner_text()
        assert not external and not errors
        browser.close()
    assert _artifact_bytes(tmp_path) == before


def contrast(foreground, background):
    def luminance(color):
        channels = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


@pytest.mark.parametrize("schema,response", [(5, False), (6, True), (8, False), (8, True)])
def test_static_evidence_report_with_javascript_disabled_is_accessible_offline(
    tmp_path, schema, response
):
    browser_path = _installed_browser()
    if schema == 8:
        persona_runs(tmp_path, change=descriptive_edit)
    else:
        make_runs(tmp_path, response=response)
    result = analyze(tmp_path, definition(response=response))
    hostile = 'شهر آزمایشی تهران </script><img src="https://invalid.test/x">'
    if schema == 8:
        from adlife.core.experiments._spatial_study_validation import document_sha256
        from adlife.core.experiments.spatial_study import SpatialStudyResult

        city = result.city.model_copy(update={"name": hostile})
        result = SpatialStudyResult.model_validate(
            result.model_dump(mode="python")
            | {"city": city, "city_provenance_sha256": document_sha256(city)}
        )
    else:
        result = public_city_result(result, hostile)
    from tests.unit.city.test_city_analysis import _artifact_bytes

    sources_before = _artifact_bytes(tmp_path / "city-runs")
    receipt = report_module().publish_spatial_study_report(result, tmp_path)
    report = tmp_path / receipt.report_path
    assert _artifact_bytes(tmp_path / "city-runs") == sources_before
    assert "Fictional biography marker" not in report.read_text(encoding="utf-8")
    assert "Another fictional name" not in report.read_text(encoding="utf-8")
    unexpected, console_errors, page_errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        context = browser.new_context(
            java_script_enabled=False,
            service_workers="block",
            viewport={"width": 1440, "height": 1050},
        )
        page = context.new_page()

        def route_request(route: Route):
            if route.request.url == report.as_uri():
                route.continue_()
            else:
                unexpected.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.on(
            "request",
            lambda request: (
                unexpected.append(request.url) if request.url != report.as_uri() else None
            ),
        )
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(report.as_uri(), wait_until="networkidle")
        expect(page.get_by_role("heading", level=1)).to_have_text("Study verdict")
        if schema == 8 and response:
            assert "schema v6, v7 or v8" in page.locator("main").inner_text()
        assert page.get_by_role("main").count() == 1
        assert page.locator(".evidence-route li").count() == 5
        assert page.get_by_role("table").count() == 1
        assert page.locator("caption").is_visible()
        assert page.locator("tbody th[scope=row]").count() == len(result.statistics)
        assert page.locator("thead th[scope=col]").count() == 7
        assert page.locator("script, img, svg, iframe, form").count() == 0
        assert page.locator(".study-name bdi").nth(1).inner_text() == result.city.name
        assert (
            page.locator(".study-name bdi")
            .nth(1)
            .evaluate("element => getComputedStyle(element).direction")
            == "rtl"
        )
        assert (
            page.locator(".evidence-route").evaluate("element => getComputedStyle(element).display")
            == "grid"
        )
        # Actual keyboard navigation, not only programmatic focus.
        page.keyboard.press("Tab")
        skip = page.get_by_role("link", name="Skip to evidence")
        expect(skip).to_be_focused()
        assert skip.is_visible()
        assert skip.bounding_box()["y"] >= 0
        focus = skip.evaluate(
            "element => ({width: getComputedStyle(element).outlineWidth, "
            "style: getComputedStyle(element).outlineStyle})"
        )
        assert focus == {"width": "3px", "style": "solid"}
        page.keyboard.press("Enter")
        expect(page.locator("main")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator(".table-region")).to_be_focused()
        assert (
            page.locator(".table-region").evaluate(
                "element => getComputedStyle(element).outlineWidth"
            )
            == "3px"
        )
        # Contrast from real computed foreground/background colors, not palette tokens.
        for selector in (
            "body",
            ".warning",
            ".proxy-note" if response else ".scope",
            ".eyebrow",
            ".statistics td",
            ".sources",
            ".evidence-route span",
        ):
            colors = page.locator(selector).first.evaluate(
                "element => ({"
                "foreground: getComputedStyle(element).color, "
                "background: getComputedStyle(document.documentElement).backgroundColor})"
            )
            assert contrast(colors["foreground"], colors["background"]) >= 4.5, (selector, colors)
        page.locator("header").scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / "spatial-report-wide.png"))
        page.locator(".evidence-route").screenshot(path=str(tmp_path / "spatial-report-route.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert (
            page.locator(".evidence-route").evaluate("element => getComputedStyle(element).display")
            == "block"
        )
        region = page.locator(".table-region")
        assert region.bounding_box()["width"] <= 390
        assert (
            page.locator(".sources").first.evaluate(
                "element => parseFloat(getComputedStyle(element).fontSize)"
            )
            >= 10
        )
        region.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(150)
        assert region.evaluate("element => element.scrollLeft") > 0
        page.locator("header").scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / "spatial-report-narrow.png"))
        page.emulate_media(media="print")
        assert (
            page.locator(".evidence-route").evaluate("element => getComputedStyle(element).display")
            == "block"
        )
        assert region.evaluate("element => getComputedStyle(element).overflowX") == "visible"
        assert (
            page.locator("thead").evaluate("element => getComputedStyle(element).display")
            == "table-header-group"
        )
        assert not skip.is_visible()
        for selector in (".statistics th[scope=row]", ".statistics td", ".sources"):
            assert (
                page.locator(selector).first.evaluate(
                    "element => parseFloat(getComputedStyle(element).fontSize)"
                )
                >= 10
            )
        page.emulate_media(media="screen", forced_colors="active", reduced_motion="reduce")
        region.focus()
        assert region.evaluate("element => getComputedStyle(element).outlineWidth") == "3px"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not unexpected and not console_errors and not page_errors
        context.close()
        browser.close()
