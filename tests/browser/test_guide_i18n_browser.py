"""guide-i18n.js and guide-core.js in a real browser, checked against Python (spec 10.1).

The parity test imports the served ``/guide/assets/guide-i18n.js`` and runs both shared
fixtures (``format_cases.json`` and ``ref_cases.json``) through it, so the story page
and the server-rendered report cannot drift apart. The packaged catalogs are then loaded
the way a guide page loads them (``initI18n`` reads ``<html lang>``) and compared with
the Python catalog in every language. The workbench app serves the assets once the guide
routes are installed; until then a minimal stand-in serves the same packaged bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from playwright.sync_api import Browser, ConsoleMessage, Page, sync_playwright

import adlife
from adlife.city.workbench_web import _SHELL_HEADERS, create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.i18n import LANGUAGES, Catalog, MessageRef, catalog_bytes, load_catalog
from tests.browser.test_city_workbench_browser import _installed_browser, _serve
from tests.unit.i18n.test_i18n_formatting import FORMAT_FIXTURE, fixture_catalogs, run_case
from tests.unit.i18n.test_i18n_refs import (
    REF_FIXTURE,
    fixture_context,
    markup_tree,
)
from tests.unit.i18n.test_i18n_refs import (
    fixture_catalogs as ref_catalogs,
)

GUIDE_STATIC = Path(adlife.__file__).resolve().parent / "city" / "static" / "guide"
ASSET_ROUTE = "/guide/assets/{asset_name}"
MISSING = re.compile("^\u27e6(?P<key>[^\u27e7]+)\u27e7$")

_TREE_JS = """
function tree(parent) {
  const out = [];
  for (const node of parent.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      const last = out.length - 1;
      if (last >= 0 && typeof out[last] === "string") out[last] += node.data;
      else out.push(node.data);
    } else {
      if (node.tagName !== "BDI" || node.attributes.length !== 1) {
        throw new Error("unexpected node");
      }
      out.push({ bdi: node.getAttribute("dir"), children: tree(node) });
    }
  }
  return out;
}
"""

_PARITY_JS = (
    "async ({ formats, refs }) => {"
    + _TREE_JS
    + """
  const i18n = await import("/guide/assets/guide-i18n.js");
  const formatCatalogs = {};
  for (const [language, messages] of Object.entries(formats.messages)) {
    formatCatalogs[language] = i18n.createCatalog(language, messages);
  }
  const formatted = formats.cases.map((item) => {
    const catalog = formatCatalogs[item.language];
    const args = item.args;
    if (item.call === "int") return catalog.fmt.int(args[0]);
    const signed = item.signed === true;
    if (item.call === "dec") return catalog.fmt.dec(args[0], args[1], { signed });
    if (item.call === "pct") return catalog.fmt.pct(...args);
    if (item.call === "clock") return catalog.fmt.clock(...args);
    if (item.call === "duration") return catalog.fmt.duration(...args);
    if (item.call === "t") return catalog.t(args[0], args[1]);
    if (item.call === "tn") return catalog.tn(args[0], args[1], args[2] || {});
    throw new Error("unknown call");
  });
  const refCatalogs = {};
  for (const [language, messages] of Object.entries(refs.messages)) {
    refCatalogs[language] = i18n.createCatalog(language, messages);
  }
  const contexts = {};
  for (const [name, context] of Object.entries(refs.contexts)) {
    contexts[name] = i18n.makeRefContext(context);
  }
  const resolved = refs.cases.map((item) => {
    const catalog = refCatalogs[item.language];
    const context = contexts[item.context];
    const holder = document.createElement("div");
    holder.append(catalog.resolveNodes(item.ref, context));
    return { plain: catalog.resolveText(item.ref, context), nodes: tree(holder) };
  });
  const refused = refs.errors.map((item) => {
    const catalog = refCatalogs[item.language];
    const context = contexts[item.context];
    const holder = document.createElement("div");
    holder.append(catalog.resolveNodes(item.ref, context));
    return { plain: catalog.resolveText(item.ref, context), nodes: tree(holder) };
  });
  return { formatted, resolved, refused, pwned: window.pwned === 1 };
}
"""
)

_ACTIVE_JS = """async (language) => {
  document.documentElement.setAttribute("lang", language);
  const i18n = await import("/guide/assets/guide-i18n.js");
  await i18n.initI18n();
  const ctx = i18n.makeRefContext({ days: 2, people: [
    { agent_id: "person-006", template_key: "veyra", display_name: "Veyra", number: 6 },
  ] });
  const ref = { key: "story.moment.noticed", params: {
    time: { clock: 1877 }, name: { person: "person-006" }, product: { text: "Lumen Pocket" },
    where: { key: "story.where.mobile-feed.commute" } } };
  const headline = { key: "story.headline.people", params: {
    people: { key: "common.people", count: 8 }, days: { key: "common.days", count: 2 },
    city: { key: "city.fictional-grid-v2" } } };
  return {
    lang: i18n.lang(),
    dir: i18n.dir(),
    href: i18n.href("/guide/runs/guided-device-1020-1405/report"),
    answered: i18n.t("progress.answered", { resolved: 3, total: 10 }),
    people: i18n.tn("common.people", 10240),
    shown_zero: i18n.tn("story.headline.shown", 0, { product: "Lumen Pocket" }),
    shown_one: i18n.tn("story.headline.shown", 1, { product: "Lumen Pocket" }),
    dec: i18n.fmt.dec(-0.2213, 2),
    signed: i18n.fmt.dec(0.04, 2, { signed: true }),
    int: i18n.fmt.int(10240),
    pct: i18n.fmt.pct(0.5),
    clock_one_day: i18n.fmt.clock(437, 1),
    clock_two_days: i18n.fmt.clock(1877, 2),
    duration: i18n.fmt.duration(61),
    sentence: i18n.resolveText(ref, ctx),
    headline: i18n.resolveText(headline, ctx),
    has_bdi: i18n.resolveNodes(ref, ctx).querySelectorAll("bdi").length > 0,
    missing: i18n.t("no.such_key"),
  };
}"""


def _stand_in_app() -> FastAPI:
    """Serve the packaged foundation assets exactly as the guide asset route does."""
    application = FastAPI()

    @application.get(ASSET_ROUTE)
    def asset(asset_name: str) -> Response:
        if asset_name in {"guide-core.js", "guide-i18n.js"}:
            return Response(
                (GUIDE_STATIC / asset_name).read_bytes(),
                media_type="text/javascript; charset=utf-8",
                headers=_SHELL_HEADERS,
            )
        match = re.fullmatch(r"i18n-(en|de|fa)\.json", asset_name)
        if match is not None:
            language = match.group(1)
            assert language in LANGUAGES
            return Response(
                catalog_bytes(language),  # type: ignore[arg-type]
                media_type="application/json",
                headers=_SHELL_HEADERS,
            )
        return Response(status_code=404)

    return application


def _application(root: Path) -> object:
    workbench = create_city_workbench_app(
        prepare_workbench_workspace(root), csrf_token="guide-i18n-token"
    )
    if any(getattr(route, "path", None) == ASSET_ROUTE for route in workbench.routes):
        return workbench
    return _stand_in_app()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Browser, str]]:
    browser_path = _installed_browser()
    application = _application(tmp_path_factory.mktemp("guide-i18n") / "workspace")
    with _serve(application) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(browser_path),
            headless=True,
            args=["--no-first-run", "--disable-background-networking"],
        )
        try:
            yield browser, base_url
        finally:
            browser.close()


def _catalog_page(browser: Browser, base_url: str, language: str = "en") -> tuple[Page, list[str]]:
    page = browser.new_page()
    errors: list[str] = []

    def console(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)

    page.on("console", console)
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    external: list[str] = []

    def route(route: Any) -> None:
        if route.request.url.startswith(base_url + "/"):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    page.route("**/*", route)
    response = page.goto(f"{base_url}/guide/assets/i18n-{language}.json")
    assert response is not None and response.status == 200
    assert response.body() == catalog_bytes(language)  # type: ignore[arg-type]
    assert response.headers["content-security-policy"] == _SHELL_HEADERS["Content-Security-Policy"]
    assert external == []
    return page, errors


def test_javascript_formats_and_resolves_exactly_like_python(
    served: tuple[Browser, str],
) -> None:
    browser, base_url = served
    page, errors = _catalog_page(browser, base_url)
    try:
        result = page.evaluate(_PARITY_JS, {"formats": FORMAT_FIXTURE, "refs": REF_FIXTURE})
    finally:
        page.close()

    format_catalogs = fixture_catalogs()
    for case, javascript in zip(FORMAT_FIXTURE["cases"], result["formatted"], strict=True):
        python = run_case(format_catalogs[case["language"]], case)
        assert javascript == python == case["expected"], case["id"]

    catalogs: dict[str, Catalog] = ref_catalogs()
    for case, javascript in zip(REF_FIXTURE["cases"], result["resolved"], strict=True):
        catalog = catalogs[case["language"]]
        context = fixture_context(case["context"])
        ref = MessageRef.model_validate(case["ref"])
        assert javascript["plain"] == catalog.plain(ref, context) == case["plain"], case["id"]
        assert javascript["nodes"] == markup_tree(str(catalog.markup(ref, context))), case["id"]
        assert javascript["nodes"] == case["nodes"], case["id"]

    for case, javascript in zip(REF_FIXTURE["errors"], result["refused"], strict=True):
        match = MISSING.fullmatch(javascript["plain"])
        assert match is not None, case["id"]
        assert javascript["nodes"] == [javascript["plain"]], case["id"]
    # Each refused reference reports once per renderer, and nothing else is reported.
    assert len(errors) == 2 * len(REF_FIXTURE["errors"])
    assert all(error.startswith("AdLife guide translation: ") for error in errors), errors
    assert result["pwned"] is False


@pytest.mark.parametrize("language", LANGUAGES)
def test_packaged_catalogs_render_the_same_in_the_browser_and_in_python(
    served: tuple[Browser, str], language: str
) -> None:
    browser, base_url = served
    page, errors = _catalog_page(browser, base_url, language)
    try:
        result = page.evaluate(_ACTIVE_JS, language)
    finally:
        page.close()

    catalog = load_catalog(language)  # type: ignore[arg-type]
    people = {"person-006": ("veyra", "Veyra", 6)}
    from adlife.i18n import PersonLabel, RefContext, message_ref
    from adlife.i18n.refs import clock_param, person_param, text_param

    context = RefContext(days=2, people={key: PersonLabel(*value) for key, value in people.items()})
    sentence = message_ref(
        "story.moment.noticed",
        time=clock_param(1877),
        name=person_param("person-006"),
        product=text_param("Lumen Pocket"),
        where=message_ref("story.where.mobile-feed.commute"),
    )
    headline = message_ref(
        "story.headline.people",
        people=message_ref("common.people", count=8),
        days=message_ref("common.days", count=2),
        city=message_ref("city.fictional-grid-v2"),
    )
    assert result == {
        "lang": language,
        "dir": "rtl" if language == "fa" else "ltr",
        "href": f"/guide/runs/guided-device-1020-1405/report?lang={language}",
        "answered": catalog.text("progress.answered", resolved=3, total=10),
        "people": catalog.text("common.people", count=10240),
        "shown_zero": catalog.text("story.headline.shown", count=0, product="Lumen Pocket"),
        "shown_one": catalog.text("story.headline.shown", count=1, product="Lumen Pocket"),
        "dec": catalog.dec(-0.2213, 2),
        "signed": catalog.dec(0.04, 2, True),
        "int": catalog.int(10240),
        "pct": catalog.pct(0.5),
        "clock_one_day": catalog.clock(437, 1),
        "clock_two_days": catalog.clock(1877, 2),
        "duration": catalog.duration(61),
        "sentence": catalog.plain(sentence, context),
        "headline": catalog.plain(headline, context),
        "has_bdi": True,
        "missing": "\u27e6no.such_key\u27e7",
    }
    assert errors == ["AdLife guide translation: unknown message key no.such_key"]


_CORE_JS = """async () => {
  const core = await import("/guide/assets/guide-core.js");
  const refused = (action) => {
    try {
      action();
      return false;
    } catch (error) {
      return error instanceof TypeError;
    }
  };
  const node = core.el(
    "a",
    {
      className: "g-btn",
      text: "<b>x</b>",
      attrs: { href: "/guide?lang=fa", "aria-current": true, hidden: false },
      dataset: { step: 2 },
    },
    " tail",
    null,
    false,
    [core.bdi("r-1", "ltr")],
  );
  const live = document.createElement("div");
  live.id = "g-live";
  document.body.append(live);
  core.announce("first");
  const firstText = live.textContent;
  core.announce("second");
  const throttled = live.textContent;
  core.announce("third", { force: true });
  const forced = live.textContent;
  core.announce("third", { force: true });
  const repeated = live.textContent;
  let notFound = null;
  try {
    await core.getJson("/api/no-such-route");
  } catch (error) {
    notFound = [error instanceof core.ApiError, error.status, error.code];
  }
  let noToken = null;
  try {
    await core.postJson("/api/jobs", {});
  } catch (error) {
    noToken = [error instanceof core.ApiError, error.code];
  }
  const unsafePaths = [
    "https://example.invalid/api/x",
    "//example.invalid/api/x",
    "/api/../x",
    "/api/x#y",
    "/guide/new",
    "/api" + String.fromCharCode(92) + "x",
  ];
  const pathRefusals = await Promise.all(unsafePaths.map(async (path) => {
    try {
      await core.getJson(path);
      return false;
    } catch (error) {
      return error instanceof TypeError;
    }
  }));
  let assetPost = false;
  try {
    await core.postJson("/guide/assets/i18n-en.json", {});
  } catch (error) {
    assetPost = error instanceof TypeError;
  }
  const backslash = String.fromCharCode(92);
  const tab = String.fromCharCode(9);
  return {
    tag: node.tagName,
    text: node.textContent,
    html: node.childElementCount,
    href: node.getAttribute("href"),
    current: node.getAttribute("aria-current"),
    hidden: node.hasAttribute("hidden"),
    step: node.dataset.step,
    bdiDir: node.querySelector("bdi").getAttribute("dir"),
    refusals: [
      refused(() => core.el("script")),
      refused(() => core.el("style")),
      refused(() => core.el("iframe")),
      refused(() => core.el("form")),
      refused(() => core.el("div", { attrs: { style: "color:red" } })),
      refused(() => core.el("div", { attrs: { onclick: "x" } })),
      refused(() => core.el("a", { attrs: { href: "https://example.invalid" } })),
      refused(() => core.el("a", { attrs: { href: "//example.invalid" } })),
      refused(() => core.el("a", { attrs: { href: "/" + backslash + "example.invalid" } })),
      refused(() => core.el("a", { attrs: { href: "javascript:alert(1)" } })),
      refused(() => core.el("a", { attrs: { href: "/" + tab + "/x" } })),
      refused(() => core.el("img", { attrs: { src: "data:image/png;base64,x" } })),
      refused(() => core.el("div", { text: 3 })),
      refused(() => core.el("div", {}, 3)),
      refused(() => core.bdi("x", "up")),
      refused(() => core.el("div", { attrs: { srcdoc: "x" } })),
    ],
    firstText,
    throttled,
    forced,
    repeated,
    notFound,
    noToken,
    pathRefusals,
    assetPost,
    reduced: typeof core.prefersReducedMotion() === "boolean",
    meta: core.meta("adlife-csrf-token"),
  };
}"""


def test_guide_core_builds_only_safe_nodes_and_requests(served: tuple[Browser, str]) -> None:
    browser, base_url = served
    page, errors = _catalog_page(browser, base_url)
    try:
        result = page.evaluate(_CORE_JS)
    finally:
        page.close()

    assert result["tag"] == "A"
    assert result["text"] == "<b>x</b> tailr-1"
    assert result["html"] == 1  # only the <bdi>: the markup-like text stayed text
    assert result["href"] == "/guide?lang=fa"
    assert result["current"] == ""
    assert result["hidden"] is False
    assert result["step"] == "2"
    assert result["bdiDir"] == "ltr"
    assert result["refusals"] == [True] * 16
    assert result["firstText"] == "first"
    assert result["throttled"] == "first"  # the second waits for the 5 s interval
    assert result["forced"] == "third"
    assert result["repeated"] == "third\u00a0"  # repeated text is announced again
    assert result["notFound"][0] is True and result["notFound"][1] == 404
    assert result["noToken"] == [True, "missing-csrf-token"]
    assert result["pathRefusals"] == [True] * 6
    assert result["assetPost"] is True
    assert result["reduced"] is True
    assert result["meta"] is None
    assert [error for error in errors if "404" not in error] == []
