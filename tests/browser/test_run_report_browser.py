"""Browser checks of the HTML run report: offline file, isolation, contrast, phone and print.

One module-scoped workspace holds two saved V10 runs built by the real pure pipeline
(``run_story_fixtures``): the guided default study answered by a fake online AI on an
``.invalid`` origin, and the mixed run whose scenario, campaign and persona names are
hostile Persian text. The real workbench app serves them and one headless system browser
reads every report. No provider is ever reached: the system address resolver is refused
while the runs are built.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Route, expect, sync_playwright

from adlife.adapters.cognition.city_endpoint_policy import SystemCityAddressResolver
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.i18n import load_catalog
from tests.browser.test_city_workbench_browser import _installed_browser, _serve
from tests.browser.test_spatial_report_browser import contrast
from tests.unit.city.run_story_fixtures import (
    HOSTILE_PERSONA_NAME,
    HOSTILE_SCENARIO,
    saved_story_run,
)

PROVIDER_RUN = "report-provider"
MIXED_RUN = "report-mixed"
PERSIAN_DIGITS = re.compile(f"[{chr(0x06F0)}-{chr(0x06F9)}]")
# Measured on Windows: the eight-person, ten-answer demo report prints on 9 A4 pages in
# English and German and 10 in Persian, two person cards per row.
DEMO_PRINT_PAGES = 10

# Every rendered text run with its colour composited over the real backgrounds behind it.
_TEXT_COLOURS_JS = """() => {
  const parse = (value) => {
    const match = /rgba?\\(([^)]+)\\)/.exec(value);
    if (!match) return null;
    const parts = match[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  };
  const over = (top, bottom) =>
    [0, 1, 2].map((i) => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
  const backdrop = (element) => {
    const chain = [];
    for (let node = element; node; node = node.parentElement) chain.unshift(node);
    let colour = [255, 255, 255, 1];
    for (const node of chain) {
      const own = parse(getComputedStyle(node).backgroundColor);
      if (own && own[3] > 0) colour = over(own, colour);
    }
    return colour;
  };
  const css = (colour) => `rgb(${colour.slice(0, 3).map(Math.round).join(", ")})`;
  const seen = new Set();
  const found = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    const element = text.parentElement;
    if (!text.textContent.trim() || seen.has(element)) continue;
    seen.add(element);
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    if (style.visibility !== "visible" || box.width <= 1 || box.height <= 1) continue;
    const behind = backdrop(element);
    found.push({
      element: `${element.tagName.toLowerCase()}.${element.className}`,
      text: text.textContent.trim().slice(0, 60),
      foreground: css(over(parse(style.color), behind)),
      background: css(behind),
      size: parseFloat(style.fontSize),
    });
  }
  return found;
}"""


@dataclass(frozen=True)
class _Site:
    base_url: str
    root: Path


async def _refuse_resolution(_self: object, _hostname: str, _port: int) -> tuple[str, ...]:
    raise OSError("tests never resolve provider hosts")


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Site]:
    root = tmp_path_factory.mktemp("run-report-browser") / "workspace"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SystemCityAddressResolver, "resolve", _refuse_resolution)
        saved_story_run(root, "default-provider", run_id=PROVIDER_RUN)
        saved_story_run(root, "mixed", run_id=MIXED_RUN)
        app = create_city_workbench_app(
            prepare_workbench_workspace(root), csrf_token="run-report-browser-token"
        )
        with _serve(app) as base_url:
            yield _Site(base_url, root)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    executable = _installed_browser()
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(
            executable_path=str(executable),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        try:
            yield launched
        finally:
            launched.close()


def _view_url(site: _Site, run_id: str, language: str) -> str:
    return f"{site.base_url}/guide/runs/{run_id}/report?lang={language}"


def _download(browser: Browser, site: _Site, run_id: str, language: str, folder: Path) -> Path:
    """Save the report the way the presenter does: the view's download link."""
    context = browser.new_context(accept_downloads=True)
    try:
        page = context.new_page()
        page.goto(_view_url(site, run_id, language))
        with page.expect_download() as pending:
            page.locator(".actions a[download]").click()
        download = pending.value
        assert download.suggested_filename == f"adlife-report-{run_id}-{language}.html"
        target = folder / download.suggested_filename
        download.save_as(target)
        return target
    finally:
        context.close()


def _offline_page(
    browser: Browser, report: Path, *, javascript: bool = False
) -> tuple[Page, list[str], list[str]]:
    """Open a saved report from disk with every request other than the file aborted."""
    context = browser.new_context(
        java_script_enabled=javascript,
        service_workers="block",
        viewport={"width": 1280, "height": 720},
    )
    page = context.new_page()
    unexpected: list[str] = []
    errors: list[str] = []

    def route(request_route: Route) -> None:
        if request_route.request.url == report.as_uri():
            request_route.continue_()
        else:
            unexpected.append(request_route.request.url)
            request_route.abort()

    page.route("**/*", route)
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(report.as_uri(), wait_until="load")
    return page, unexpected, errors


def _computed(page: Page, selector: str, prop: str) -> str:
    return str(
        page.locator(selector).first.evaluate(
            "(element, prop) => getComputedStyle(element)[prop]", prop
        )
    )


def _overflow(page: Page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"))


def _smallest_font(page: Page) -> float:
    sizes: list[dict[str, Any]] = page.evaluate(_TEXT_COLOURS_JS)
    return min(item["size"] for item in sizes)


def _pdf_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", pdf))


@pytest.mark.parametrize("language", ["en", "fa"])
def test_downloaded_report_reads_offline_without_javascript_or_requests(
    browser: Browser, site: _Site, tmp_path: Path, language: str
) -> None:
    catalog = load_catalog(language)  # type: ignore[arg-type]
    report = _download(browser, site, PROVIDER_RUN, language, tmp_path)
    page, unexpected, errors = _offline_page(browser, report)
    try:
        expect(page.get_by_role("heading", level=1)).to_have_text(catalog.text("report.title"))
        html = page.locator("html")
        expect(html).to_have_attribute("lang", language)
        expect(html).to_have_attribute("dir", "rtl" if language == "fa" else "ltr")
        assert _computed(page, "body", "direction") == ("rtl" if language == "fa" else "ltr")
        # The standalone file is inert and self-contained: one style element, no fetches.
        assert page.locator("script, img, svg, iframe, form, link, object, embed").count() == 0
        assert page.locator("style").count() == 1
        hrefs = page.locator("a[href]").evaluate_all(
            "links => links.map(l => l.getAttribute('href'))"
        )
        assert hrefs and all(href.startswith("#") for href in hrefs)
        assert page.locator("main > section").count() == 7
        assert page.locator(".person").count() == 8
        if language == "fa":
            assert PERSIAN_DIGITS.search(page.locator(".funnel").inner_text())
            # Model and run IDs stay left-to-right islands inside the Persian page.
            assert _computed(page, ".meta bdi[dir=ltr]", "direction") == "ltr"
        # Real computed colours over the real backgrounds, not palette tokens.
        texts: list[dict[str, Any]] = page.evaluate(_TEXT_COLOURS_JS)
        assert len(texts) > 100
        low = [
            (
                item["element"],
                item["text"],
                round(contrast(item["foreground"], item["background"]), 2),
            )
            for item in texts
            if contrast(item["foreground"], item["background"]) < 4.5
        ]
        assert not low, low
        assert min(item["size"] for item in texts) >= 12
        assert float(_computed(page, "body", "fontSize").removesuffix("px")) >= 15
        # Keyboard: the skip link appears with the focus ring and lands on the content.
        page.keyboard.press("Tab")
        skip = page.locator("a.skip")
        expect(skip).to_be_focused()
        assert skip.is_visible() and (skip.bounding_box() or {"y": -1})["y"] >= 0
        assert _computed(page, "a.skip", "outlineStyle") == "solid"
        assert _computed(page, "a.skip", "outlineWidth") == "3px"
        page.keyboard.press("Enter")
        expect(page.locator("main")).to_be_focused()
        # Phone width: no sideways scroll, tables stacked into label and value lists.
        page.set_viewport_size({"width": 390, "height": 844})
        assert _overflow(page) <= 0
        assert _computed(page, ".endstate td", "display") == "block"
        assert _computed(page, ".endstate thead", "display") == "none"
        assert _smallest_font(page) >= 12
        assert not unexpected and not errors, (unexpected, errors)
    finally:
        page.context.close()


@pytest.mark.parametrize("language", ["en", "fa"])
def test_hostile_persian_names_stay_isolated_and_inert(
    browser: Browser, site: _Site, tmp_path: Path, language: str
) -> None:
    report = _download(browser, site, MIXED_RUN, language, tmp_path)
    # JavaScript on: the file's own CSP (script-src 'none') must still keep it inert.
    page, unexpected, errors = _offline_page(browser, report, javascript=True)
    try:
        name = page.locator("bdi[dir=auto]", has_text="window.pwned=1").first
        assert name.inner_text() == HOSTILE_PERSONA_NAME
        assert name.evaluate("element => getComputedStyle(element).direction") == "rtl"
        scenario = page.locator("#technical bdi[dir=auto]", has_text="window.pwned=3").first
        assert scenario.inner_text() == HOSTILE_SCENARIO
        assert scenario.evaluate("element => getComputedStyle(element).direction") == "rtl"
        assert page.locator("img, svg, script").count() == 0
        assert page.evaluate("() => typeof window.pwned") == "undefined"
        # Every noticed status of the mixed run keeps a visible badge; moments nobody noticed
        # are only counted in the person's summary sentence.
        statuses = set(
            page.locator(".badge[data-status]").evaluate_all(
                "badges => badges.map(b => b.dataset.status)"
            )
        )
        assert {"answered", "failed", "over_limit", "unsure", "warning"} <= statuses
        assert "not_noticed" not in statuses
        page.set_viewport_size({"width": 390, "height": 844})
        assert _overflow(page) <= 0
        assert not unexpected and not errors, (unexpected, errors)
    finally:
        page.context.close()


def test_print_and_forced_colours_keep_the_report_readable(browser: Browser, site: _Site) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    errors: list[str] = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(_view_url(site, PROVIDER_RUN, "en"), wait_until="networkidle")
        expect(page.locator(".toolbar")).to_be_visible()
        page.emulate_media(media="print")
        for hidden in (".toolbar", ".langs", ".actions a[download]", ".toc", "a.skip"):
            expect(page.locator(hidden).first).to_be_hidden()
        # A busy person card can be taller than a page, so cards may continue on the next
        # page, while each heading, moment and end-state row stays whole.
        assert _computed(page, ".person-head", "breakInside") == "avoid"
        assert _computed(page, ".moment", "breakInside") == "avoid"
        assert _computed(page, ".endstate tr", "breakInside") == "avoid"
        assert _computed(page, "#people h2", "breakAfter") == "avoid"
        assert _computed(page, ".persona", "display") == "grid"
        for selector in (".bar", ".bar .fill", ".mark", ".chip"):
            adjust = page.locator(selector).first.evaluate(
                "element => { const s = getComputedStyle(element);"
                " return s.printColorAdjust || s.webkitPrintColorAdjust; }"
            )
            assert adjust == "exact", selector
        pages = _pdf_pages(page.pdf(format="A4", print_background=True))
        assert 2 <= pages <= DEMO_PRINT_PAGES, pages
        page.emulate_media(media="screen", forced_colors="active")
        for selector in (".chip", ".tag", ".badge", ".bar"):
            assert _computed(page, selector, "borderTopStyle") != "none", selector
            assert float(_computed(page, selector, "borderTopWidth").removesuffix("px")) >= 1
        assert _computed(page, ".bar", "forcedColorAdjust") == "none"
        assert _computed(page, ".bar .fill", "forcedColorAdjust") == "none"
        assert _overflow(page) <= 0
        assert not errors, errors
    finally:
        context.close()


def test_served_report_links_every_language_and_logs_no_console_errors(
    browser: Browser, site: _Site
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    errors: list[str] = []
    requests: list[str] = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    try:
        page.goto(_view_url(site, PROVIDER_RUN, "en"), wait_until="networkidle")
        expect(page.locator(".actions a[download]")).to_have_attribute(
            "href", f"/guide/runs/{PROVIDER_RUN}/report/download?lang=en"
        )
        expect(page.locator(".actions a").first).to_have_attribute(
            "href", f"/guide/runs/{PROVIDER_RUN}?lang=en"
        )
        for language in ("de", "fa", "en"):
            page.locator(f".langs a[lang={language}]").click()
            page.wait_for_load_state("networkidle")
            expect(page.locator("html")).to_have_attribute("lang", language)
            expect(page.locator(f".langs a[lang={language}]")).to_have_attribute(
                "aria-current", "true"
            )
            assert page.locator("main > section").count() == 7
            assert _overflow(page) <= 0
        assert all(url.startswith(site.base_url + "/") for url in requests), requests
        assert not errors, errors
    finally:
        context.close()
