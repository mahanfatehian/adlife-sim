"""Security boundary of the saved-run V10 bounded cognition inspector assets."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.browser.browser_support import _installed_browser
from tests.browser.test_bounded_cognition_inspector_browser import (
    HELPER,
    STATIC,
    SavedRun,
    documents,
    inspector,
    model_label_overrides,
    response_minutes,
    saved_workspace_run,
)

FORBIDDEN_SCRIPT_TOKENS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "Worker(",
    "SharedWorker(",
    "caches.",
    "importScripts(",
    "WebSocket(",
    "EventSource(",
    "sendBeacon",
    "http://",
    "https://",
    "document.cookie",
    "history.pushState",
    "history.replaceState",
    "location.hash",
)
HELPER_ONLY_FORBIDDEN = (
    "fetch(",
    "XMLHttpRequest",
    "setTimeout",
    "setInterval",
    "requestAnimationFrame",
    "document.",
    "createElement",
    "addEventListener",
    "console.",
)
SECRET = "sk-live-" + "a1b2c3d4e5f6g7h8i9j0"
INSTRUMENT = """(() => {
  window.__effects = [];
  const record = (name) => function (...args) { window.__effects.push(name); };
  window.fetch = record("fetch");
  window.XMLHttpRequest = record("XMLHttpRequest");
  window.WebSocket = record("WebSocket");
  window.EventSource = record("EventSource");
  window.Worker = record("Worker");
  window.SharedWorker = record("SharedWorker");
  window.setTimeout = record("setTimeout");
  window.setInterval = record("setInterval");
  window.requestAnimationFrame = record("requestAnimationFrame");
  navigator.sendBeacon = record("sendBeacon");
  for (const name of ["getItem", "setItem", "removeItem", "clear", "key"]) {
    Storage.prototype[name] = record(`storage.${name}`);
  }
  for (const [target, name] of [[window, "indexedDB"], [window, "caches"], [document, "cookie"]]) {
    Object.defineProperty(target, name, {
      configurable: true,
      get() { window.__effects.push(name); return undefined; },
      set() { window.__effects.push(name); },
    });
  }
  window.__mutations = 0;
  new MutationObserver((records) => { window.__mutations += records.length; })
    .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
})();"""


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
def saved_run(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], SavedRun]:
    cache: dict[str, SavedRun] = {}

    def saved(name: str) -> SavedRun:
        if name not in cache:
            root = tmp_path_factory.mktemp(f"security-{name}")
            cache[name] = saved_workspace_run(root, name, f"secure-{name}")
        return cache[name]

    return saved


def test_inspector_assets_expose_no_markup_execution_storage_or_remote_surface() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    application = (STATIC / "app.js").read_text(encoding="utf-8")
    for token in FORBIDDEN_SCRIPT_TOKENS:
        assert token not in helper, token
        assert token not in application, token
    for token in HELPER_ONLY_FORBIDDEN:
        assert token not in helper, token
    assert helper.count("window") == 1
    assert 'Object.defineProperty(window, "AdLifeBoundedCognition"' in helper
    stylesheet = (STATIC / "app-cognition-v2.css").read_text(encoding="utf-8").lower()
    for token in ("@import", "@font-face", "url(", "expression(", "javascript:"):
        assert token not in stylesheet, token
    assert re.search(r"(?m)(?:^|[;{])\s*behavior\s*:", stylesheet) is None
    # Only V10 markup or the class a verified V10 run receives is styled.
    rules = re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.DOTALL)
    selectors = re.findall(r"(?:^|[{}])\s*([^@{}\s][^{}]*)\{", rules)
    assert len(selectors) > 50
    for selector in selectors:
        for part in selector.split(","):
            assert ".bounded" in part or ":root" in part, part


def test_helper_load_and_every_call_have_no_side_effects(
    browser: Browser, saved_run: Callable[[str], SavedRun]
) -> None:
    summary, records, applied = documents(saved_run("mixed"))
    page: Page = browser.new_page()
    try:
        page.set_content("<!doctype html><html><head></head><body><main></main></body></html>")
        page.evaluate(INSTRUMENT)
        before_globals = page.evaluate("() => Object.getOwnPropertyNames(window)")
        page.add_script_tag(path=str(HELPER))
        page.evaluate("() => { window.__mutations = 0; window.__effects = []; }")
        outcome = page.evaluate(
            """([summary, records, applied]) => {
              const helper = window.AdLifeBoundedCognition;
              const s = helper.validateSummary(summary);
              const r = helper.validateRecords(records, s);
              const index = helper.indexTerminalRecords(r);
              const presentations = applied.map(
                (item) => helper.presentationForAppliedResponse(item, index),
              );
              try { helper.validateSummary({ ...summary, mode: "cloud" }); } catch (_) {}
              return { count: presentations.length };
            }""",
            [summary, records, applied],
        )
        assert outcome == {"count": len(applied)}
        page.wait_for_timeout(50)
        assert page.evaluate("() => window.__effects") == []
        assert page.evaluate("() => window.__mutations") == 0
        added = set(page.evaluate("() => Object.getOwnPropertyNames(window)")) - set(before_globals)
        assert added == {"AdLifeBoundedCognition"}
        assert page.evaluate("() => document.body.innerHTML") == "<main></main>"
    finally:
        page.close()


def _secret_model(document: dict[str, Any], _query: Mapping[str, str]) -> dict[str, Any]:
    document["model_id"] = f"model {SECRET}"
    return document


def test_credential_shaped_provider_label_is_refused_without_reaching_the_dom(
    browser: Browser, saved_run: Callable[[str], SavedRun]
) -> None:
    with inspector(
        browser, saved_run("mixed"), overrides={"cognition-summary": _secret_model}
    ) as session:
        page = session.page
        expect(page.locator("#error-banner")).to_have_text(
            "Error: Saved bounded cognition summary is invalid"
        )
        assert SECRET not in page.content()
        assert SECRET not in page.url
        assert page.locator("#bounded-cognition-panel").count() == 0
        assert not any(SECRET in url for _method, url, _headers in session.requests)


def test_browsing_v10_evidence_leaves_no_browser_state_or_history(
    browser: Browser, saved_run: Callable[[str], SavedRun]
) -> None:
    run = saved_run("mixed")
    with inspector(browser, run) as session:
        page = session.page
        start_url = page.url
        history = page.evaluate("() => history.length")
        for minute in response_minutes(run)[:4]:
            session.minute(minute)
        page.locator("#bounded-signal-index > summary").click()
        page.locator("#bounded-signal-list .bounded-signal-jump").first.click()
        page.wait_for_function("() => state.selectedEvidence !== null")
        residue = page.evaluate(
            """async () => ({
              local: localStorage.length,
              session: sessionStorage.length,
              cookie: document.cookie,
              databases: (await indexedDB.databases()).length,
              workers: (await navigator.serviceWorker.getRegistrations()).length,
              caches: (await caches.keys()).length,
              history: history.length,
              hash: location.hash,
              search: location.search,
              handlers: Array.from(document.querySelectorAll(
                '#bounded-cognition-panel *, #response-list *, #response-state-list *',
              )).filter((node) => Array.from(node.attributes)
                .some((attribute) => attribute.name.startsWith('on'))).length,
              scripts: Array.from(document.scripts).map((script) => script.getAttribute('src')),
            })"""
        )
        assert residue == {
            "local": 0,
            "session": 0,
            "cookie": "",
            "databases": 0,
            "workers": 0,
            "caches": 0,
            "history": history,
            "hash": "",
            "search": "",
            "handlers": 0,
            "scripts": ["/static/app-cognition-v2.js", "/static/app.js"],
        }
        assert page.url == start_url
        assert session.external == []
        assert {method for method, _url, _headers in session.requests} == {"GET"}


def test_hostile_markup_in_saved_text_never_becomes_executable(
    browser: Browser, saved_run: Callable[[str], SavedRun]
) -> None:
    run = saved_run("mixed")
    hostile = (
        "<img src=x onerror=window.securityPwned=1></script><script>window.securityPwned=2</script>"
    )

    with inspector(browser, run, overrides=model_label_overrides("مدل " + hostile)) as session:
        page = session.page
        expect(page.locator("#bounded-cognition-panel")).to_be_visible()
        for minute in response_minutes(run)[:3]:
            session.minute(minute)
        model = page.locator('#bounded-cognition-provenance [data-fact="model"] bdi')
        expect(model).to_have_text("مدل " + hostile)
        assert page.evaluate("() => window.securityPwned") is None
        assert page.locator("img, svg, iframe, object, embed").count() == 0
        assert page.evaluate("() => document.scripts.length") == 2
        assert session.page_errors == []
