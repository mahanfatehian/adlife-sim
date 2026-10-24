"""Layout guards for every guided page at projector, laptop and phone sizes.

One module-scoped workbench serves one practice run that the real setup page starts and
the real progress page watches to completion; one headless system browser then opens the
start page, the setup page, that completed progress page and that run's story in English
and Persian at 1024 x 576, 1280 x 720, 1920 x 1080 and 390 x 844. Every page must fit the
window without horizontal scrolling, show no visible text under 12 px, draw a focus
outline on a keyboard-focused control and log no console or page error. Practice answers
never contact a provider, and the system address resolver is refused for the module.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from adlife.adapters.cognition.city_endpoint_policy import SystemCityAddressResolver
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.browser_support import _installed_browser, _serve

LANGUAGES = ("en", "fa")
VIEWPORTS = ((1024, 576), (1280, 720), (1920, 1080), (390, 844))
PAGES = ("start", "setup", "progress", "story")
JOB_PATH = re.compile(r"/guide/jobs/(job-[0-9a-f]{32})\?lang=en$")
STORY_PATH = re.compile(r"^/guide/runs/([a-z0-9][a-z0-9-]*)\?lang=en$")
SMALLEST_TEXT_PX = 12.0
READY = 20_000
FOCUSABLE = "button, a.g-btn, [role='tab'], [role='radio']"

# The smallest computed font size of any visible element that holds text of its own.
# Visually hidden helpers and the bodies of closed disclosures are not on screen.
SMALLEST_TEXT = """() => {
  let smallest = Infinity;
  let where = '';
  for (const node of document.querySelectorAll('body *')) {
    const text = [...node.childNodes].some(
      (child) => child.nodeType === Node.TEXT_NODE && child.textContent.trim() !== '');
    if (!text) continue;
    if (node.closest('.g-visually-hidden')) continue;
    const closed = node.closest('details:not([open])');
    if (closed && !node.closest('summary')) continue;
    const box = node.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) continue;
    const style = getComputedStyle(node);
    if (style.visibility !== 'visible' || Number(style.opacity) === 0) continue;
    const size = parseFloat(style.fontSize);
    if (size < smallest) {
      smallest = size;
      const sample = node.textContent.trim().slice(0, 40);
      where = `${node.tagName.toLowerCase()}#${node.id}.${node.className} ${sample}`;
    }
  }
  return [smallest, where];
}"""


@dataclass(frozen=True)
class _Site:
    base_url: str
    job_id: str
    run_id: str


@dataclass
class _Session:
    page: Page
    errors: list[str]


async def _refuse_resolution(_self: object, _hostname: str, _port: int) -> tuple[str, ...]:
    raise OSError("guide layout tests never resolve provider hosts")


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


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory, browser: Browser) -> Iterator[_Site]:
    """Serve one workspace and run the practice study through the real setup page once."""
    root = tmp_path_factory.mktemp("guide-layout") / "workspace"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SystemCityAddressResolver, "resolve", _refuse_resolution)
        app = create_city_workbench_app(
            prepare_workbench_workspace(root), csrf_token="layout-browser-token"
        )
        with _serve(app) as base_url:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            try:
                page = context.new_page()
                page.goto(f"{base_url}/guide/new?lang=en")
                run = page.locator("#setup-run")
                expect(run).to_have_attribute("aria-disabled", "false", timeout=READY)
                run.click()
                page.wait_for_url(JOB_PATH, timeout=READY)
                watch = page.locator("#job-watch")
                expect(watch).to_be_visible(timeout=60_000)
                job = JOB_PATH.search(page.url)
                story = STORY_PATH.fullmatch(watch.get_attribute("href") or "")
                assert job is not None and story is not None
                job_id, run_id = job.group(1), story.group(1)
            finally:
                context.close()
            yield _Site(base_url, job_id, run_id)


@contextmanager
def _session(browser: Browser, width: int, height: int) -> Iterator[_Session]:
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    session = _Session(page, [])
    page.on(
        "console",
        lambda message: (
            session.errors.append(f"console: {message.text}") if message.type == "error" else None
        ),
    )
    page.on("pageerror", lambda error: session.errors.append(f"pageerror: {error}"))
    try:
        yield session
    finally:
        context.close()


def _open(page: Page, site: _Site, name: str, language: str) -> None:
    """Open one guided page and wait until its script has rendered its content."""
    if name == "start":
        page.goto(f"{site.base_url}/guide?lang={language}")
        expect(page.locator("#runs-list > .setup-run").first).to_be_visible(timeout=READY)
        expect(page.locator("#runs-status")).to_be_hidden()
    elif name == "setup":
        page.goto(f"{site.base_url}/guide/new?lang={language}")
        expect(page.locator("#setup-run")).to_have_attribute(
            "aria-disabled", "false", timeout=READY
        )
    elif name == "progress":
        page.goto(f"{site.base_url}/guide/jobs/{site.job_id}?lang={language}")
        expect(page.locator("#job-watch")).to_be_visible(timeout=READY)
        expect(page.locator("#job-steps > li[data-step='done']")).to_have_attribute(
            "data-state", "done"
        )
    else:
        page.goto(f"{site.base_url}/guide/runs/{site.run_id}?lang={language}")
        expect(page.locator("#story-root")).to_have_attribute("data-state", "ready", timeout=READY)
    expect(page.locator("html")).to_have_attribute("lang", language)


def _assert_fits(page: Page, where: str) -> None:
    widths = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert widths[0] <= widths[1], f"{where}: page scrolls sideways {widths}"
    smallest, element = page.evaluate(SMALLEST_TEXT)
    assert smallest >= SMALLEST_TEXT_PX, f"{where}: {smallest}px text in {element}"


def _assert_focus_ring(page: Page, where: str) -> None:
    """Tab to the first visible control and check the browser draws its outline."""
    for _ in range(80):
        page.keyboard.press("Tab")
        focused = page.evaluate(
            "(selector) => { const node = document.activeElement;"
            " return !!node && node.matches(selector) && node.getClientRects().length > 0; }",
            FOCUSABLE,
        )
        if focused:
            break
    else:
        raise AssertionError(f"{where}: no button is reachable with the Tab key")
    style, width = page.evaluate(
        "() => { const style = getComputedStyle(document.activeElement);"
        " return [style.outlineStyle, parseFloat(style.outlineWidth)]; }"
    )
    assert style != "none" and width > 0, f"{where}: focus outline {style} {width}"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("width", "height"), VIEWPORTS, ids=[f"{width}x{height}" for width, height in VIEWPORTS]
)
@pytest.mark.parametrize("name", PAGES)
def test_guided_page_fits_the_window_with_readable_text_and_a_focus_ring(
    browser: Browser, site: _Site, name: str, width: int, height: int, language: str
) -> None:
    where = f"{name} {language} {width}x{height}"
    with _session(browser, width, height) as session:
        page = session.page
        _open(page, site, name, language)
        _assert_fits(page, where)
        _assert_focus_ring(page, where)
        if name == "story":
            page.locator("#story-next").click()
            expect(page.locator("#story-card")).to_be_visible()
            _assert_fits(page, f"{where} first moment")
            for tab in ("people", "summary"):
                page.locator(f"#story-tab-{tab}").click()
                expect(page.locator(f"#story-pane-{tab}")).to_be_visible()
                _assert_fits(page, f"{where} {tab} tab")
        assert session.errors == [], where
