"""Browser operation of the guided story page: the key-moment player, map, tabs and RTL.

One module-scoped workspace holds saved V10 runs built by the real pure pipeline
(``run_story_fixtures``), served by the real workbench app, and one headless system
browser opens every page. No provider is ever reached: the system address resolver is
refused for the whole module, and the provider runs use ``.invalid`` origins.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from adlife.adapters.cognition.city_endpoint_policy import SystemCityAddressResolver
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.i18n import load_catalog
from adlife.reporting.run_story_model import RunStoryV1, StoryMoment, story_ref_context
from tests.browser.test_city_workbench_browser import _artifact_hashes, _installed_browser, _serve
from tests.unit.city.run_story_fixtures import HOSTILE_PERSONA_NAME, saved_story_run

RUNS = {
    "mock": "default-mock",
    "mixed": "mixed",
    "rules": "rules",
    "hostile": "hostile",
    "v9": "v9",
}
PERSIAN_DIGIT = f"[{chr(0x06F0)}-{chr(0x06F9)}]"
PERSIAN_DIGITS = re.compile(PERSIAN_DIGIT)
ASCII_DIGITS = re.compile("[0-9]")
PERSIAN_ONE = chr(0x06F1)
PERSIAN_ZERO = chr(0x06F0)
PERSIAN_CLOCK = re.compile(f"{PERSIAN_DIGIT}{{2}}:{PERSIAN_DIGIT}{{2}}")
READY = 15_000
# Chrome reports every 4xx response as a console error; the error pages expect those.
RESOURCE_ERROR = "Failed to load resource"


@dataclass(frozen=True)
class _Site:
    base_url: str
    root: Path
    run_ids: dict[str, str]


async def _refuse_resolution(_self: object, _hostname: str, _port: int) -> tuple[str, ...]:
    raise OSError("tests never resolve provider hosts")


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Site]:
    root = tmp_path_factory.mktemp("guide-story") / "workspace"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SystemCityAddressResolver, "resolve", _refuse_resolution)
        run_ids: dict[str, str] = {}
        for short, name in RUNS.items():
            run_id = f"story-{name}"
            saved_story_run(root, name, run_id=run_id)  # type: ignore[arg-type]
            run_ids[short] = run_id
        app = create_city_workbench_app(
            prepare_workbench_workspace(root), csrf_token="story-browser-token"
        )
        with _serve(app) as base_url:
            yield _Site(base_url, root, run_ids)


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


@dataclass
class _Session:
    page: Page
    errors: list[str]
    methods: list[str]


@contextmanager
def _session(
    browser: Browser,
    *,
    width: int = 1280,
    height: int = 720,
    reduced_motion: str = "no-preference",
) -> Iterator[_Session]:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion=reduced_motion,  # type: ignore[arg-type]
    )
    page = context.new_page()
    session = _Session(page, [], [])
    page.on(
        "console",
        lambda message: session.errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: session.errors.append(str(error)))
    page.on("request", lambda request: session.methods.append(request.method))
    try:
        yield session
    finally:
        context.close()


def _open(page: Page, site: _Site, run_id: str, lang: str = "en") -> None:
    page.goto(f"{site.base_url}/guide/runs/{run_id}?lang={lang}")
    expect(page.locator("#story-root")).to_have_attribute("data-state", "ready", timeout=READY)


def _story(page: Page, site: _Site, run_id: str) -> RunStoryV1:
    response = page.request.get(f"{site.base_url}/api/runs/{run_id}/story")
    assert response.status == 200
    return RunStoryV1.model_validate_json(response.body())


def _all_steps(story: RunStoryV1) -> list[tuple[str, StoryMoment]]:
    steps = [(person.agent_id, moment) for person in story.people for moment in person.moments]
    return sorted(steps, key=lambda step: (step[1].minute, step[1].millisecond, step[0]))


def _slide(page: Page, index: int) -> None:
    page.evaluate(
        """(value) => {
          const slider = document.getElementById('story-slider');
          slider.value = String(value);
          slider.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        index,
    )


def _distinct_canvas_colours(page: Page) -> int:
    return int(
        page.evaluate(
            """() => {
              const canvas = document.getElementById('story-map');
              const context = canvas.getContext('2d');
              const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
              const seen = new Set();
              for (let index = 0; index < data.length; index += 4 * 7) {
                seen.add((data[index] << 16) | (data[index + 1] << 8) | data[index + 2]);
              }
              return seen.size;
            }"""
        )
    )


def _overflow(page: Page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"))


def test_intro_headline_and_funnel_match_the_story_and_page_keys_step(
    browser: Browser, site: _Site
) -> None:
    run_id = site.run_ids["mock"]
    catalog = load_catalog("en")
    with _session(browser) as session:
        page = session.page
        _open(page, site, run_id)
        story = _story(page, site, run_id)
        context = story_ref_context(story)
        expect(page.locator("#story-headline > li")).to_have_text(
            [catalog.plain(ref, context) for ref in story.headline]
        )
        funnel = story.funnel
        expect(page.locator("#story-intro-funnel .g-stat__value")).to_have_text(
            [str(funnel.opportunities), str(funnel.noticed), str(funnel.chosen), str(funnel.used)]
        )
        expect(page.locator("#story-intro-funnel .g-stat__label")).to_have_text(
            [
                catalog.text("story.summary.funnel.opportunities"),
                catalog.text("story.summary.funnel.noticed"),
                catalog.text("story.summary.funnel.chosen"),
                catalog.text("story.summary.funnel.practice"),
            ]
        )
        total = len(story.key_moments)
        counter = page.locator("#story-counter")
        card = page.locator("#story-card")
        expect(counter).to_have_text(catalog.text("story.counter", index=0, total=total))
        expect(card).to_be_hidden()
        assert _distinct_canvas_colours(page) >= 4

        first, second = story.key_moments[0], story.key_moments[1]
        page.keyboard.press("PageDown")
        expect(counter).to_have_text(catalog.text("story.counter", index=1, total=total))
        expect(card).to_be_visible()
        expect(card).to_have_attribute("data-agent", first.agent_id)
        expect(card).to_have_attribute("data-moment", first.moment_id)
        expect(page.locator("#story-intro")).to_be_hidden()
        page.keyboard.press("PageDown")
        expect(card).to_have_attribute("data-moment", second.moment_id)
        page.keyboard.press("PageUp")
        expect(card).to_have_attribute("data-moment", first.moment_id)
        page.keyboard.press("ArrowRight")
        expect(counter).to_have_text(catalog.text("story.counter", index=2, total=total))
        page.keyboard.press("ArrowLeft")
        expect(counter).to_have_text(catalog.text("story.counter", index=1, total=total))
        page.keyboard.press("Home")
        expect(page.locator("#story-intro")).to_be_visible()
        expect(card).to_be_hidden()
        expect(counter).to_have_text(catalog.text("story.counter", index=0, total=total))
        page.keyboard.press("PageUp")
        expect(counter).to_have_text(catalog.text("story.counter", index=0, total=total))
        assert _distinct_canvas_colours(page) >= 4
        assert session.errors == []


def test_presenter_keys_keep_stepping_after_clicks_and_tab_switches(
    browser: Browser, site: _Site
) -> None:
    run_id = site.run_ids["mock"]
    catalog = load_catalog("en")
    with _session(browser) as session:
        page = session.page
        _open(page, site, run_id)
        total = len(_story(page, site, run_id).key_moments)
        counter = page.locator("#story-counter")
        play_text = page.locator("#story-play-text")

        def at(index: int) -> None:
            expect(counter).to_have_text(catalog.text("story.counter", index=index, total=total))

        page.locator("#story-next").click()
        at(1)
        # A mouse click lets go of the button: the arrows step and Space plays or pauses.
        page.keyboard.press("ArrowRight")
        at(2)
        page.keyboard.press("Space")
        expect(play_text).to_have_text(catalog.text("story.pause"))
        page.keyboard.press("Space")
        expect(play_text).to_have_text(catalog.text("story.play"))
        at(2)
        page.locator("#story-tab-people").click()
        page.locator("#story-tab-story").click()
        page.keyboard.press("ArrowRight")
        at(3)
        page.keyboard.press("2")
        expect(page.locator("#story-pane-people")).to_be_visible()
        page.keyboard.press("1")
        page.keyboard.press("ArrowLeft")
        at(2)
        # Keyboard users still move between tabs with the arrows once a tab has focus.
        page.locator("#story-tab-story").focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator("#story-tab-people")).to_be_focused()
        expect(page.locator("#story-pane-people")).to_be_visible()
        # Home always returns to the intro, also from inside the tab list.
        page.keyboard.press("Home")
        expect(page.locator("#story-pane-story")).to_be_visible()
        expect(page.locator("#story-intro")).to_be_visible()
        at(0)
        assert session.errors == []


def test_every_status_in_the_mixed_run_shows_its_badge_and_lanes(
    browser: Browser, site: _Site
) -> None:
    run_id = site.run_ids["mixed"]
    catalog = load_catalog("en")
    with _session(browser, width=1600, height=1000) as session:
        page = session.page
        _open(page, site, run_id)
        story = _story(page, site, run_id)
        steps = _all_steps(story)
        statuses = sorted({moment.status for _agent, moment in steps})
        assert set(statuses) >= {
            "answered",
            "reused",
            "unsure",
            "warning",
            "failed",
            "over_limit",
            "not_chosen",
            "not_noticed",
        }
        page.locator("#story-filter [data-filter='all']").click()
        expect(page.locator("#story-filter [data-filter='all']")).to_have_attribute(
            "aria-checked", "true"
        )
        expect(page.locator("#story-counter")).to_contain_text(str(len(steps)))
        badge = page.locator("#story-card .story-badge")
        for status in statuses:
            index = next(i for i, (_agent, moment) in enumerate(steps) if moment.status == status)
            agent_id, moment = steps[index]
            _slide(page, index)
            expect(page.locator("#story-card")).to_have_attribute("data-moment", moment.moment_id)
            expect(page.locator("#story-card")).to_have_attribute("data-agent", agent_id)
            expect(badge).to_have_attribute("data-status", status)
            expect(badge).to_have_text(catalog.text(f"status.{status}"))
            lanes = page.locator("#story-card .story-lane")
            expect(lanes).to_have_count(3 if moment.noticed else 0)
            chips = page.locator("#story-card .story-chips > li")
            expect(chips).to_have_count(4 if moment.labels is not None else 0)
            if status in {"unsure", "warning"}:
                expect(page.locator("#story-card .story-set-aside")).to_have_text(
                    catalog.text("story.labels.set_aside")
                )
            disclosure = page.locator("#story-card details.story-math")
            expect(disclosure).to_have_count(1 if moment.noticed else 0)
        assert session.errors == []


def test_map_positions_follow_the_story_tracks_at_every_moment(
    browser: Browser, site: _Site
) -> None:
    with _session(browser) as session:
        page = session.page
        for key in ("mixed", "mock"):
            run_id = site.run_ids[key]
            _open(page, site, run_id)
            story = _story(page, site, run_id)
            assert any(
                moment.activity == "commute" for person in story.people for moment in person.moments
            )
            result = page.evaluate(
                """async (runId) => {
                  const { positionAt } = await import('/guide/assets/guide-map.js');
                  const response = await fetch(`/api/runs/${runId}/story`, { cache: 'no-store' });
                  const story = await response.json();
                  const nodes = new Map(story.map.nodes.map((node) => [node.node_id, node]));
                  const wrong = [];
                  let checked = 0;
                  for (const person of story.people) {
                    for (const moment of person.moments) {
                      const [longitude, latitude] = positionAt(person, moment.minute, nodes);
                      checked += 1;
                      if (Math.abs(longitude - moment.position[0]) > 1e-5
                        || Math.abs(latitude - moment.position[1]) > 1e-5) {
                        wrong.push([person.agent_id, moment.moment_id]);
                      }
                    }
                  }
                  return { checked, wrong };
                }""",
                run_id,
            )
            moments = sum(len(person.moments) for person in story.people)
            assert result == {"checked": moments, "wrong": []}
        assert session.errors == []


@pytest.mark.parametrize(("width", "height"), [(1024, 576), (390, 844)])
def test_persian_story_runs_right_to_left_with_persian_digits(
    browser: Browser, site: _Site, width: int, height: int
) -> None:
    run_id = site.run_ids["mock"]
    with _session(browser, width=width, height=height) as session:
        page = session.page
        _open(page, site, run_id, lang="fa")
        directions = page.evaluate(
            "() => [document.documentElement.dir, getComputedStyle(document.body).direction,"
            " getComputedStyle(document.getElementById('story-map-frame')).direction]"
        )
        assert directions == ["rtl", "rtl", "ltr"]
        counter = page.locator("#story-counter")
        clock = page.locator("#story-clock")
        intro_counter = counter.text_content() or ""
        assert PERSIAN_ZERO in intro_counter
        assert ASCII_DIGITS.search(intro_counter) is None
        page.keyboard.press("ArrowLeft")
        expect(page.locator("#story-card")).to_be_visible()
        expect(counter).to_contain_text(PERSIAN_ONE)
        counter_text = counter.text_content() or ""
        assert ASCII_DIGITS.search(counter_text) is None
        clock_text = clock.text_content() or ""
        assert PERSIAN_CLOCK.fullmatch(clock_text), clock_text
        assert PERSIAN_DIGITS.search(page.locator("#story-card").text_content() or "")
        page.keyboard.press("ArrowRight")
        expect(page.locator("#story-intro")).to_be_visible()
        assert _overflow(page) <= 0
        for key in ("2", "3", "1"):
            page.keyboard.press(key)
            assert _overflow(page) <= 0
        page.keyboard.press("PageDown")
        page.keyboard.press("PageDown")
        expect(page.locator("#story-card")).to_be_visible()
        assert _overflow(page) <= 0
        assert session.errors == []


def test_follow_limits_the_steps_to_one_person_and_the_summary_funnel_matches(
    browser: Browser, site: _Site
) -> None:
    run_id = site.run_ids["mock"]
    catalog = load_catalog("en")
    with _session(browser) as session:
        page = session.page
        _open(page, site, run_id)
        story = _story(page, site, run_id)
        person = next(person for person in story.people if len(person.moments) >= 2)
        context = story_ref_context(story)
        label = context.people[person.agent_id]
        name = catalog.text(
            "common.person_ref",
            name=catalog.text(f"persona.{label.template_key}.name"),
            number=label.number,
        )
        page.keyboard.press("2")
        expect(page.locator("#story-pane-people")).to_be_visible()
        expect(page.locator("#story-pane-story")).to_be_hidden()
        expect(page.locator("#story-tab-people")).to_have_attribute("aria-selected", "true")
        expect(page.locator(".story-person")).to_have_count(len(story.people))
        page.locator(f".story-follow[data-agent='{person.agent_id}']").click()
        expect(page.locator("#story-pane-story")).to_be_visible()
        option = page.locator("#story-filter-person")
        expect(option).to_have_attribute("aria-checked", "true")
        expect(option).to_have_text(catalog.text("story.filter.person", name=name))
        counter = page.locator("#story-counter")
        card = page.locator("#story-card")
        steps = len(person.moments)
        expect(counter).to_have_text(catalog.text("story.counter", index=1, total=steps))
        for position, moment in enumerate(person.moments, start=1):
            expect(card).to_have_attribute("data-agent", person.agent_id)
            expect(card).to_have_attribute("data-moment", moment.moment_id)
            expect(counter).to_have_text(catalog.text("story.counter", index=position, total=steps))
            page.keyboard.press("PageDown")
        page.locator("#story-filter [data-filter='key']").click()
        expect(counter).to_contain_text(str(len(story.key_moments)))

        page.keyboard.press("3")
        expect(page.locator("#story-pane-summary")).to_be_visible()
        funnel = story.funnel
        expect(page.locator(".story-summary-funnel .g-stat__value")).to_have_text(
            [str(funnel.opportunities), str(funnel.noticed), str(funnel.chosen), str(funnel.used)]
        )
        expect(page.locator(".story-spread")).to_have_count(4)
        expect(page.locator(".story-averages .g-pair")).to_have_count(3)
        assert session.errors == []


def test_a_rules_run_shows_two_funnel_steps_and_no_ai_labels(browser: Browser, site: _Site) -> None:
    run_id = site.run_ids["rules"]
    catalog = load_catalog("en")
    with _session(browser) as session:
        page = session.page
        _open(page, site, run_id)
        story = _story(page, site, run_id)
        expect(page.locator("#story-intro-funnel .g-stat__value")).to_have_text(
            [str(story.funnel.opportunities), str(story.funnel.noticed)]
        )
        page.keyboard.press("3")
        expect(page.locator("#story-summary")).to_contain_text(
            catalog.text("story.summary.labels.none")
        )
        expect(page.locator(".story-coverage")).to_have_count(0)
        assert session.errors == []


def test_autoplay_still_steps_under_reduced_motion(browser: Browser, site: _Site) -> None:
    run_id = site.run_ids["mock"]
    catalog = load_catalog("en")
    with _session(browser, reduced_motion="reduce") as session:
        page = session.page
        page.clock.install()
        _open(page, site, run_id)
        total = len(_story(page, site, run_id).key_moments)
        counter = page.locator("#story-counter")
        page.locator("#story-play").click()
        expect(counter).to_have_text(catalog.text("story.counter", index=1, total=total))
        expect(page.locator("#story-play-text")).to_have_text(catalog.text("story.pause"))
        page.clock.run_for(6_100)
        expect(counter).to_have_text(catalog.text("story.counter", index=2, total=total))
        page.clock.run_for(6_100)
        expect(counter).to_have_text(catalog.text("story.counter", index=3, total=total))
        page.locator("#story-play").click()
        expect(page.locator("#story-play-text")).to_have_text(catalog.text("story.play"))
        page.clock.run_for(13_000)
        expect(counter).to_have_text(catalog.text("story.counter", index=3, total=total))
        assert session.errors == []


def test_hostile_names_render_as_inert_text(browser: Browser, site: _Site) -> None:
    with _session(browser) as session:
        page = session.page
        for key in ("hostile", "mixed"):
            _open(page, site, site.run_ids[key])
            for _ in range(3):
                page.keyboard.press("PageDown")
            page.keyboard.press("2")
            expect(page.locator("#story-people")).to_contain_text(HOSTILE_PERSONA_NAME)
            page.keyboard.press("3")
            page.keyboard.press("1")
            injected = page.locator(
                "#story-app img, #story-app script, #story-app iframe, #story-app svg[onload]"
            )
            expect(injected).to_have_count(0)
            assert page.evaluate("() => window.pwned") is None
        assert session.errors == []


def test_older_and_missing_runs_explain_themselves_and_the_page_only_reads(
    browser: Browser, site: _Site
) -> None:
    catalog = load_catalog("en")
    before = _artifact_hashes(site.root)
    with _session(browser) as session:
        page = session.page
        v9 = site.run_ids["v9"]
        page.goto(f"{site.base_url}/guide/runs/{v9}?lang=en")
        expect(page.locator("#story-root")).to_have_attribute(
            "data-state", "problem", timeout=READY
        )
        unavailable = page.locator("[data-problem='unavailable']")
        expect(unavailable).to_be_visible()
        expect(unavailable).to_have_text(catalog.text("story.unavailable"))
        technical = page.locator("#story-problem-technical")
        expect(technical).to_be_visible()
        expect(technical).to_have_attribute("href", f"/runs/{v9}")
        expect(page.locator("#story-app")).to_be_hidden()

        page.goto(f"{site.base_url}/guide/runs/story-never-saved?lang=en")
        expect(page.locator("[data-problem='not_found']")).to_be_visible(timeout=READY)
        expect(page.locator("#story-problem-technical")).to_be_hidden()

        _open(page, site, site.run_ids["mock"])
        for key in ("PageDown", "PageDown", "2", "3", "1", "Home"):
            page.keyboard.press(key)
        expect(page.locator("#story-intro")).to_be_visible()
        unexpected = [error for error in session.errors if RESOURCE_ERROR not in error]
        assert unexpected == []
        assert set(session.methods) == {"GET"}
    assert _artifact_hashes(site.root) == before
