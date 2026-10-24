"""Browser checks for the guide's packaged content: five towns, eight ads and ten personas.

The setup page offers every packaged fictional town with a street preview, sends the chosen
town (and that town's own billboard spot) in the draft, and the story map then draws that
town. Wording comes from the packaged catalogs; nothing here reaches a model provider.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page, Route, expect, sync_playwright

from adlife.city.catalog import load_verified_catalog_cities
from adlife.city.workbench_creatives import load_creative_template_catalog
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.browser_support import _installed_browser, _serve

CATALOG_DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "adlife" / "i18n" / "catalogs"
CATALOGS: dict[str, dict[str, Any]] = {
    language: json.loads((CATALOG_DIRECTORY / f"{language}.json").read_text(encoding="utf-8"))[
        "messages"
    ]
    for language in ("en", "de", "fa")
}
# The rehearsed grid first, then the packaged towns from small to large.
TOWNS = (
    "fictional-grid-v2",
    "fictional-harbor-loop-v2",
    "fictional-river-junction-v2",
    "fictional-old-town-v2",
    "fictional-new-district-v2",
)
# Each town's billboard: a busy two-way road, facing the traffic that approaches it.
BILLBOARDS = {
    "fictional-grid-v2": ("middle-west", "forward", 259),
    "fictional-harbor-loop-v2": ("harbor-arc-north", "forward", 270),
    "fictional-river-junction-v2": ("junction-south-arm", "forward", 0),
    "fictional-old-town-v2": ("old-bridge", "forward", 9),
    "fictional-new-district-v2": ("street-3-de", "backward", 90),
}
PERSONA_KEYS = (
    "lumo",
    "veyra",
    "orilo",
    "nuvin",
    "pimo",
    "zarel",
    "ketra",
    "tavi",
    "selun",
    "halvi",
)
JOB_PATH = re.compile(r"/guide/jobs/(job-[0-9a-f]{32})\?lang=(en|de|fa)$")
SHAPES = ("circle", "square", "triangle", "diamond")


def _digits(text: str, language: str) -> str:
    if language != "fa":
        return text
    return "".join(chr(0x06F0 + int(char)) if char.isdigit() else char for char in text)


def _say(key: str, language: str = "en", *, count: int | None = None, **params: str) -> str:
    message = CATALOGS[language][key]
    if isinstance(message, dict):
        assert count is not None, key
        one = count in (0, 1) if language == "fa" else count == 1
        message = message["one" if one else "other"]
        params.setdefault("count", _digits(str(count), language))
    for name, value in params.items():
        message = message.replace("{" + name + "}", value)
    assert "{" not in message, message
    return str(message)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    executable = _installed_browser()
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(
            executable_path=str(executable),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        try:
            yield chromium
        finally:
            chromium.close()


def _record_console(errors: list[str]) -> Callable[[ConsoleMessage], None]:
    def record(message: ConsoleMessage) -> None:
        if message.type != "error":
            return
        location = str(message.location.get("url", ""))
        if message.text.startswith("Failed to load resource") and "/api/" in location:
            return
        # Translation gaps belong to the catalog tests, not to this content check.
        if message.text.startswith("AdLife guide translation: unknown message key connection."):
            return
        errors.append(f"console: {message.text}")

    return record


@contextmanager
def _guide(
    tmp_path: Path, browser: Browser, *, width: int = 1280, height: int = 720
) -> Iterator[tuple[Page, str]]:
    app = create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"), csrf_token="guide-content-token"
    )
    with _serve(app) as base_url:
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
        page.on("console", _record_console(errors))
        try:
            yield page, base_url
            assert errors == []
        finally:
            context.close()


def _posted_drafts(page: Page) -> list[dict[str, Any]]:
    posted: list[dict[str, Any]] = []
    page.on(
        "request",
        lambda request: (
            posted.append(json.loads(request.post_data or "{}"))
            if request.method == "POST" and urlparse(request.url).path == "/api/jobs"
            else None
        ),
    )
    return posted


def _run_and_open_story(page: Page) -> str:
    """Press Run, wait for the finished job, open its story and return the run ID."""
    run = page.locator("#setup-run")
    expect(run).to_have_attribute("aria-disabled", "false")
    run.click()
    page.wait_for_url(JOB_PATH, timeout=15_000)
    watch = page.locator("#job-watch")
    expect(watch).to_be_visible(timeout=90_000)
    href = watch.get_attribute("href")
    assert href is not None
    run_id = urlparse(href).path.rsplit("/", 1)[-1]
    watch.click()
    page.wait_for_url(re.compile(rf"/guide/runs/{run_id}\?lang="))
    expect(page.locator("#story-map")).to_have_attribute("data-places", re.compile(r"\S"))
    return run_id


def _mark_look(class_name: str) -> tuple[str, str]:
    """The colour slot and the drawn shape of one persona mark."""
    classes = class_name.split()
    slot = next(name for name in classes if re.fullmatch(r"g-mark--c[0-9]", name))
    index = int(slot.removeprefix("g-mark--c"))
    explicit = [
        name.removeprefix("g-mark--") for name in classes if name.removeprefix("g-mark--") in SHAPES
    ]
    return slot, explicit[0] if explicit else SHAPES[index % 4]


@pytest.mark.parametrize(("language", "width", "height"), [("en", 1280, 720), ("fa", 390, 844)])
def test_the_town_picker_offers_every_packaged_town_with_a_street_preview(
    tmp_path: Path, browser: Browser, language: str, width: int, height: int
) -> None:
    packs = {city.entry.city_id: city.pack for city in load_verified_catalog_cities()}
    assert set(packs) == set(TOWNS)
    with _guide(tmp_path, browser, width=width, height=height) as (page, base_url):
        page.goto(f"{base_url}/guide/new?lang={language}")
        section = page.locator("#setup-town")
        expect(section).to_be_visible()
        expect(section.locator("#town-title")).to_have_text(_say("setup.town.title", language))
        towns = page.locator("#town-list [role='radio']")
        expect(towns).to_have_count(5)
        assert towns.evaluate_all("items => items.map(item => item.dataset.value)") == list(TOWNS)
        expect(towns.locator(".setup-town__name")).to_have_text(
            [_say(f"city.{city_id}", language) for city_id in TOWNS]
        )
        expect(towns.locator(".setup-town__stats")).to_have_text(
            [
                _say(
                    "setup.town.stats",
                    language,
                    nodes=_digits(str(len(packs[city_id].nodes)), language),
                    roads=_digits(str(len(packs[city_id].roads)), language),
                )
                for city_id in TOWNS
            ]
        )
        expect(page.locator("#town-list [aria-checked='true']")).to_have_attribute(
            "data-value", "fictional-grid-v2"
        )
        # Every preview is a drawn, decorative canvas that never mirrors with the page.
        for canvas in towns.locator("canvas").all():
            expect(canvas).to_have_attribute("aria-hidden", "true")
            expect(canvas).to_have_attribute("dir", "ltr")
            box = canvas.bounding_box()
            assert box is not None and box["width"] > 40 and box["height"] > 20
            assert canvas.evaluate("node => node.width > 0 && node.height > 0")
        expect(page.locator("#setup-summary")).to_contain_text(
            _say("city.fictional-grid-v2", language)
        )
        towns.nth(3).click()
        expect(towns.nth(3)).to_have_attribute("aria-checked", "true")
        expect(page.locator("#setup-summary")).to_contain_text(
            _say("city.fictional-old-town-v2", language)
        )
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0


def test_each_town_runs_a_practice_study_with_its_own_billboard_and_story_map(
    tmp_path: Path, browser: Browser
) -> None:
    packs = {city.entry.city_id: city.pack for city in load_verified_catalog_cities()}
    with _guide(tmp_path, browser) as (page, base_url):
        posted = _posted_drafts(page)
        for city_id in TOWNS:
            page.goto(f"{base_url}/guide/new?lang=en")
            page.locator(f"#town-list [data-value='{city_id}']").click()
            page.locator("#ad-where [data-value='both']").click()
            expect(page.locator("#ad-where-note")).to_be_hidden()
            run_id = _run_and_open_story(page)
            draft = posted[-1]
            assert draft["city_id"] == city_id
            road_id, direction, heading = BILLBOARDS[city_id]
            roadside = draft["scenario"]["roadside"]
            assert (roadside["road_id"], roadside["travel_direction"]) == (road_id, direction)
            assert roadside["orientation_degrees"] == heading
            assert draft["scenario"]["phone"] is not None
            # The story is about that town, and its map marks only that town's places.
            expect(page.locator("#story-app")).to_contain_text(_say(f"city.{city_id}"))
            story = page.request.get(f"{base_url}/api/runs/{run_id}/story").json()
            assert story["run"]["city"]["city_id"] == city_id
            assert story["run"]["billboard"]["road_id"] == road_id
            nodes = {node.node_id for node in packs[city_id].nodes}
            assert {node["node_id"] for node in story["map"]["nodes"]} == nodes
            places = page.locator("#story-map").get_attribute("data-places") or ""
            assert {item.split(":", 1)[0] for item in places.split()} <= nodes
        # A second study in the same minute is renamed and posted again, so count each town once.
        assert list(dict.fromkeys(draft["city_id"] for draft in posted)) == list(TOWNS)


def test_ten_personas_all_preset_and_a_billboard_study_in_the_old_town(
    tmp_path: Path, browser: Browser
) -> None:
    templates = load_persona_template_catalog().templates
    with _guide(tmp_path, browser) as (page, base_url):
        posted = _posted_drafts(page)
        page.goto(f"{base_url}/guide/new?lang=en")
        rows = page.locator("#people-list > li")
        expect(rows).to_have_count(10)
        expect(rows.locator(".setup-person__name")).to_have_text(
            [_say(f"persona.{key}.name") for key in PERSONA_KEYS]
        )
        expect(rows.locator(".setup-person__tagline")).to_have_text(
            [_say(f"persona.{key}.tagline") for key in PERSONA_KEYS]
        )
        looks = [
            _mark_look(name)
            for name in rows.locator(".g-mark").evaluate_all(
                "marks => marks.map(mark => mark.className)"
            )
        ]
        assert len({slot for slot, _shape in looks}) == 10
        # The default audience stays the rehearsed two contrasting groups.
        expect(page.locator("#people-total")).to_have_text(_say("setup.people.total", count=8))

        preset = page.get_by_role("radio", name=_say("setup.people.preset.all"))
        preset.click()
        expect(preset).to_have_attribute("aria-checked", "true")
        expect(page.locator("#people-total")).to_have_text(_say("setup.people.total", count=30))
        expect(rows.locator("output")).to_have_text(["3"] * 10)

        page.locator("#town-list [data-value='fictional-old-town-v2']").click()
        page.locator("#ad-where [data-value='billboard']").click()
        run_id = _run_and_open_story(page)
        draft = posted[-1]
        assert draft["city_id"] == "fictional-old-town-v2"
        assert draft["scenario"]["phone"] is None
        assert draft["scenario"]["roadside"]["road_id"] == "old-bridge"
        assert draft["settings"]["agent_count"] == 30
        personas = draft["population"]["personas"]
        assert [persona["persona_id"] for persona in personas] == [
            template.persona_id for template in templates
        ]
        assert all(persona["count"] == 3 for persona in personas)

        story = page.request.get(f"{base_url}/api/runs/{run_id}/story").json()
        assert [persona["template_key"] for persona in story["personas"]] == list(PERSONA_KEYS)
        assert [persona["color_index"] for persona in story["personas"]] == list(range(10))
        billboard = [
            moment
            for person in story["people"]
            for moment in person["moments"]
            if moment["channel"] == "roadside-billboard"
        ]
        assert billboard, "the old-town billboard is passed and seen"
        legend = page.locator("#story-legend-list > li")
        expect(legend).to_have_count(10)
        story_looks = [
            _mark_look(name)
            for name in legend.locator(".g-mark").evaluate_all(
                "marks => marks.map(mark => mark.className)"
            )
        ]
        assert story_looks == looks


def test_eight_products_aim_at_their_own_interests() -> None:
    # Checked without a browser: every packaged creative has a translated card and the
    # setup page names its target interests from the shared vocabulary.
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "adlife"
        / "city"
        / "static"
        / "guide"
        / "guide-setup.js"
    ).read_text(encoding="utf-8")
    templates = [template.template_id for template in load_creative_template_catalog().templates]
    assert len(templates) == 8
    interests = {
        key.removeprefix("interest.") for key in CATALOGS["en"] if key.startswith("interest.")
    }
    seen = set()
    for template_id in templates:
        entry = rf'"{re.escape(template_id)}": Object\.freeze\(\{{ '
        match = re.search(entry + r'key: "([a-z]+)", interests: \[([^\]]*)\] \}\)', source)
        assert match is not None, template_id
        assert len(match.group(1)) <= 23
        aimed = tuple(re.findall(r'"([a-z]+)"', match.group(2)))
        assert len(aimed) == 2 and set(aimed) <= interests
        seen.add(aimed)
        for language in ("en", "de", "fa"):
            assert CATALOGS[language][f"creative.{template_id}.category"]
            assert CATALOGS[language][f"creative.{template_id}.message"]
    assert len(seen) >= 6


def test_a_failed_town_listing_falls_back_to_the_grid(tmp_path: Path, browser: Browser) -> None:
    with _guide(tmp_path, browser) as (page, base_url):
        posted = _posted_drafts(page)
        page.route(
            "**/api/catalog/cities",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {
                        "schema_version": 1,
                        "error": {"code": "unavailable", "message": "Refused.", "fields": {}},
                    }
                ),
            ),
        )
        page.goto(f"{base_url}/guide/new?lang=en")
        expect(page.locator("#people-list > li")).to_have_count(10)
        expect(page.locator("#setup-town")).to_be_hidden()
        expect(page.locator("#setup-summary")).to_contain_text(_say("city.fictional-grid-v2"))
        page.locator("#ad-where [data-value='billboard']").click()

        def refuse(route: Route) -> None:
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "schema_version": 1,
                        "error": {"code": "run-unavailable", "message": "Refused.", "fields": {}},
                    }
                ),
            )

        page.route("**/api/jobs", refuse)
        page.locator("#setup-run").click()
        expect(page.locator("#setup-run-note")).to_be_visible()
        assert posted[-1]["city_id"] == "fictional-grid-v2"
        assert posted[-1]["scenario"]["roadside"]["road_id"] == "middle-west"
