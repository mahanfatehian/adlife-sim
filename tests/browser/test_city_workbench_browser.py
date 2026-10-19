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
from playwright.sync_api import APIResponse, Route, expect, sync_playwright

import adlife.city.workbench_web as workbench_web
from adlife.city.catalog import CityCatalogError, select_catalog_city
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


def _city_pack_upload(
    city_id: str,
    *,
    name: str | None = None,
    attribution: str | None = None,
    source_url: str = "https://outside.invalid/city-source",
) -> bytes:
    """Build one valid operator-supplied V2 pack without test-only server hooks."""

    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = name or f"Operator City {city_id}"
    payload["source"].update(
        {
            "provider": "Operator mapping team",
            "dataset": "Prepared street extract",
            "version": "2026.10",
            "published_on": "2026-10-01",
            "source_url": source_url,
            "license": "Operator declared",
            "attribution": attribution or "Operator supplied street geometry",
        }
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _near_limit_city_pack_upload(city_id: str) -> bytes:
    payload = json.loads(_city_pack_upload(city_id))
    shape = [
        {
            "longitude": longitude,
            "latitude": latitude,
        }
        for _index in range(32)
        for longitude, latitude in ((0, 0), (0, 0.06))
    ]
    shaped_roads = [
        {
            "road_id": f"shape-{index:04d}",
            "source_node": "center-center",
            "target_node": "center-north",
            "kind": "residential",
            "directions": ["forward", "backward"],
            "shape": shape,
        }
        for index in range(1_562)
    ]
    empty_roads = [
        {
            "road_id": f"empty-{index:04d}",
            "source_node": "center-center",
            "target_node": "center-north",
            "kind": "residential",
            "directions": ["forward", "backward"],
            "shape": [],
        }
        for index in range(3_989)
    ]
    payload["roads"] = [*payload["roads"], *shaped_roads, *empty_roads]
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _wait_for_route_handler(page, delivered, *, what: str, timeout_s: float = 10.0) -> None:
    """Pump Playwright events until a Python route handler has recorded what it saw.

    Playwright schedules a route handler as a task, after the page has issued the request,
    so the page may already show its next state while the handler has not run. Never index
    a handler's records right after the UI reacts; let the dispatcher deliver them first.
    """
    deadline = time.monotonic() + timeout_s
    while not delivered():
        if time.monotonic() >= deadline:
            raise AssertionError(f"the route handler never recorded {what}")
        page.wait_for_timeout(20)


def _is_credential_post(request) -> bool:
    return request.method == "POST" and urlparse(request.url).path.endswith("/credential")


def _persona_population(page):
    return page.locator("#persona-editor").evaluate("root => root.editor.serialize()")


def _set_audience_count(page, count: int) -> None:
    """Explicitly edit counts; changing run settings must never rewrite personas."""
    page.locator("#stage-campaign").click()
    page.locator("#persona-count").fill(str(count))
    page.locator("#campaign-continue").click()


def _remote_provider_document(*, credential_available: bool = False) -> dict[str, object]:
    revision = "a" * 64
    return {
        "schema_version": 1,
        "profiles": [
            {
                "schema_version": 1,
                "profile_id": "remote-demo",
                "display_label": "Remote demo",
                "mode": "remote",
                "base_url": "https://api.example.invalid/v1",
                "model_id": "fictional-model",
                "protocol": "openai-chat-completions-json-schema-v1",
                "temperature": 0.2,
                "top_p": 0.9,
                "seed_policy": "fixed",
                "seed": 42,
                "max_output_tokens": 384,
                "attempt_timeout_seconds": 30,
                "transient_retries": 2,
                "repair_attempts": 1,
                "origin_approval_id": "remote-demo",
                "revision_sha256": revision,
                "credential_slot_id": "b" * 64,
                "credential_available": credential_available,
                "endpoint_policy_accepted": True,
                "last_check": None,
            }
        ],
        "origins": [
            {
                "approval_id": "local-ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "trust_class": "local",
                "environment_variable": None,
            },
            {
                "approval_id": "remote-demo",
                "base_url": "https://api.example.invalid/v1",
                "trust_class": "remote",
                "environment_variable": None,
            },
        ],
        "provider_configuration": True,
        "oauth": False,
        "cognition_modes": ["rules", "mock", "local", "remote"],
    }


def test_explanations_panel_creates_exact_profile_and_emits_schema_three(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    submitted: list[dict[str, object]] = []

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.on(
                "request",
                lambda request: (
                    submitted.append(json.loads(request.post_data or "{}"))
                    if request.method == "POST" and request.url.endswith("/api/scenarios/validate")
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()

            expect(page.get_by_role("heading", name="Model assistance", exact=True)).to_be_visible()
            expect(page.locator("#cognition-effect-scope")).to_have_value("explanation-only")
            expect(page.locator("#cognition-mode option")).to_have_count(4)
            expect(page.locator("#cognition-mode")).to_have_value("rules")
            expect(page.locator("#provider-settings")).to_be_hidden()
            expect(page.locator("#provider-boundary")).to_contain_text(
                "purchase intention are always rule-computed"
            )
            expect(page.locator("#cognition-boundary")).to_contain_text("Only explanatory text.")
            expect(page.locator("#provider-product-login-note")).to_contain_text(
                "ChatGPT or Codex subscription"
            )

            page.locator("#cognition-mode").select_option("local")
            expect(page.locator("#provider-settings")).to_be_visible()
            expect(page.locator("#cognition-budget")).to_have_attribute("step", "1536")
            page.locator("#provider-create-details").evaluate("node => { node.open = true; }")
            page.locator("#provider-profile-id").fill("local-browser")
            page.locator("#provider-label").fill("Local browser model")
            page.locator("#provider-model").fill("fictional-local-model")
            page.locator("#provider-create-submit").click()
            expect(page.locator("#provider-profile")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-profile option")).to_have_count(1)
            expect(page.locator("#provider-status")).to_contain_text("Profile saved")

            revision = page.locator("#provider-profile").input_value().split("/")[1]
            assert re.fullmatch(r"[0-9a-f]{64}", revision)
            page.locator("#cognition-budget").fill("3072")
            page.locator("#cognition-budget").dispatch_event("input")
            expect(page.locator("#cognition-budget-status")).to_contain_text(
                "2 selected responses funded"
            )
            expect(page.locator("#provider-cost-warning")).to_contain_text("not a spending limit")

            page.route(
                "**/api/providers/local-browser/revisions/*/check",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": "compatible", "reason_code": None}),
                ),
            )
            page.locator("#provider-check").click()
            expect(page.locator("#provider-check-status")).to_have_text(
                "Compatible inference confirmed.", timeout=10_000
            )

            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            assert submitted[-1]["schema_version"] == 3
            assert submitted[-1]["cognition"] == {
                "mode": "local",
                "provider_profile_id": "local-browser",
                "provider_revision_sha256": revision,
                "aggregate_output_token_ceiling": 3072,
            }

            page.locator("#cognition-mode").select_option("mock")
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            assert submitted[-1]["schema_version"] == 3
            assert submitted[-1]["cognition"] == {
                "mode": "mock",
                "provider_profile_id": None,
                "provider_revision_sha256": None,
                "aggregate_output_token_ceiling": 0,
            }
        finally:
            browser.close()
            playwright.stop()


def test_one_person_one_agent_and_one_explanation_read_in_the_singular(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            persona_total = page.locator("#persona-total")
            expect(persona_total).to_have_text("1 definition · 20 people")
            page.locator("#persona-count").fill("1")
            expect(persona_total).to_have_text("1 definition · 1 person")
            expect(page.locator("#persona-roster .persona-row-definition")).to_have_text(
                "persona-001 · 1 person"
            )
            page.locator("#campaign-continue").click()
            status = page.locator("#persona-count-status")
            expect(status).to_have_text(
                "1 person assigned in the audience · 20 run agents · counts must match"
            )
            page.locator("#agent-count").fill("1")
            expect(status).to_have_text("1 person assigned in the audience · 1 run agent")

            page.locator("#cognition-mode").select_option("local")
            page.locator("#provider-create-details").evaluate("node => { node.open = true; }")
            page.locator("#provider-profile-id").fill("local-single")
            page.locator("#provider-label").fill("Local single explanation")
            page.locator("#provider-model").fill("fictional-local-model")
            page.locator("#provider-create-submit").click()
            expect(page.locator("#provider-profile")).to_be_enabled(timeout=10_000)
            page.locator("#cognition-budget").fill("1536")
            page.locator("#cognition-budget").dispatch_event("input")
            expect(page.locator("#cognition-budget-status")).to_have_text(
                "1 selected response funded · up to 59 budget-skipped."
            )
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            expect(page.locator("#review-funding")).to_have_text(
                "1,536 output-token reservation · 1 explanation funded"
            )
            expect(page.locator("#review-population")).to_have_text(
                "1 synthetic person · 2 model days"
            )
        finally:
            browser.close()
            playwright.stop()


def test_remote_credential_is_transient_after_success_refusal_and_network_failure(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    credential_prefix = "sk" + "-browser-"
    secrets = (
        credential_prefix + "success-fixture",
        credential_prefix + "refusal-fixture",
        credential_prefix + "network-fixture",
    )
    credential_bodies: list[str] = []
    credential_urls: list[str] = []
    attempts = 0

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        console_messages: list[str] = []
        try:
            page.on("console", lambda message: console_messages.append(message.text))

            def providers(route: Route) -> None:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_remote_provider_document()),
                )

            def credential(route: Route) -> None:
                nonlocal attempts
                attempts += 1
                credential_urls.append(route.request.url)
                credential_bodies.append(route.request.post_data or "")
                if attempts == 1:
                    response = _remote_provider_document(credential_available=True)["profiles"][0]
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(response),
                    )
                elif attempts == 2:
                    route.fulfill(
                        status=409,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "error": {
                                    "code": "credential-store-unavailable",
                                    "message": "Secure credential storage is unavailable.",
                                    "fields": {},
                                }
                            }
                        ),
                    )
                else:
                    route.abort("connectionfailed")

            page.route("**/api/providers", providers)
            page.route("**/api/providers/remote-demo/revisions/*/credential", credential)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")
            expect(page.locator("#provider-profile")).to_have_value(f"remote-demo/{'a' * 64}")
            expect(page.locator("#provider-credential")).to_have_attribute("autocomplete", "off")

            # Each attempt ends on its own outcome; the "Saving…" status and the cleared field
            # appear before the request even reaches the route handler, so they prove nothing.
            outcomes = (
                "Credential saved on the server.",
                "Secure credential storage is unavailable.",
                "The local workbench did not respond. Check that it is still running.",
            )
            for index, (secret, outcome) in enumerate(zip(secrets, outcomes, strict=True), start=1):
                page.locator("#provider-credential").fill(secret)
                with page.expect_request(_is_credential_post) as sent:
                    page.locator("#provider-save-credential").click()
                assert json.loads(sent.value.post_data or "") == {"credential": secret}
                assert secret not in sent.value.url
                assert all(secret not in value for value in sent.value.headers.values())
                expect(page.locator("#provider-credential")).to_have_value("", timeout=10_000)
                expect(page.locator("#provider-credential-status")).to_have_text(
                    outcome, timeout=10_000
                )
                # The control unlocks only in the attempt's finally block, after every clear.
                expect(page.locator("#provider-save-credential")).to_be_enabled(timeout=10_000)
                expect(page.locator("#provider-credential")).to_have_value("")
                _wait_for_route_handler(
                    page,
                    lambda count=index: len(credential_bodies) >= count,
                    what=f"credential attempt {index}",
                )
                assert len(credential_bodies) == index
                snapshot = page.evaluate(
                    """secret => ({
                      text: document.body.textContent.includes(secret),
                      html: document.documentElement.outerHTML.includes(secret),
                      attributes: [...document.querySelectorAll('*')].some(node =>
                        [...node.attributes].some(attribute => attribute.value.includes(secret))),
                      local: Object.values(localStorage).includes(secret),
                      session: Object.values(sessionStorage).includes(secret),
                      globalPrimitive: Object.getOwnPropertyNames(window).some(name => {
                        try { return typeof window[name] === 'string' && window[name] === secret; }
                        catch (_error) { return false; }
                      }),
                    })""",
                    secret,
                )
                assert snapshot == {
                    "text": False,
                    "html": False,
                    "attributes": False,
                    "local": False,
                    "session": False,
                    "globalPrimitive": False,
                }
                assert all(secret not in url for url in credential_urls)
                assert all(secret not in message for message in console_messages)
                assert json.loads(credential_bodies[index - 1]) == {"credential": secret}

            expect(page.locator("#provider-credential-status")).to_contain_text("did not respond")
        finally:
            browser.close()
            playwright.stop()


def test_replacing_remote_credential_clears_stale_compatibility_status(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    listed = _remote_provider_document(credential_available=True)
    listed["profiles"][0]["last_check"] = {
        "status": "compatible",
        "reason_code": None,
    }
    updated = _remote_provider_document(credential_available=True)["profiles"][0]

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.route(
                "**/api/providers",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(listed),
                ),
            )
            page.route(
                "**/api/providers/remote-demo/revisions/*/credential",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(updated),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")
            expect(page.locator("#provider-check-status")).to_have_text(
                "Compatible inference confirmed."
            )

            page.locator("#provider-credential").fill("credential-" + "replacement")
            page.locator("#provider-save-credential").click()

            expect(page.locator("#provider-credential")).to_have_value("", timeout=10_000)
            expect(page.locator("#provider-credential-status")).to_contain_text("Credential saved")
            expect(page.locator("#provider-check-status")).to_have_text("")
        finally:
            browser.close()
            playwright.stop()


def test_empty_provider_numbers_are_refused_before_any_write(tmp_path: Path) -> None:
    browser_path = _installed_browser()
    profile_posts: list[str] = []
    validation_posts: list[str] = []

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:

            def providers(route: Route) -> None:
                if route.request.method == "GET":
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(_remote_provider_document(credential_available=True)),
                    )
                    return
                profile_posts.append(route.request.post_data or "")
                route.abort("failed")

            page.route("**/api/providers", providers)
            page.on(
                "request",
                lambda request: (
                    validation_posts.append(request.post_data or "")
                    if request.method == "POST" and request.url.endswith("/api/scenarios/validate")
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")
            page.locator("#provider-create-details").evaluate("node => { node.open = true; }")
            page.locator("#provider-create-details .provider-advanced").evaluate(
                "node => { node.open = true; }"
            )
            page.locator("#provider-profile-id").fill("empty-number-check")
            page.locator("#provider-label").fill("Empty number check")
            page.locator("#provider-model").fill("fictional-model")

            for control_id, valid_value in (
                ("provider-temperature", "0.2"),
                ("provider-retries", "2"),
            ):
                page.locator(f"#{control_id}").fill("")
                page.locator("#provider-create-submit").click()
                expect(page.locator("#provider-create-error")).to_contain_text("Enter a")
                assert page.evaluate("document.activeElement.id") == control_id
                assert profile_posts == []
                page.locator(f"#{control_id}").fill(valid_value)

            page.locator("#cognition-budget").fill("")
            page.locator("#validate-run").click()
            expect(page.locator("#cognition-budget-error")).to_contain_text("whole number")
            assert page.evaluate("document.activeElement.id") == "cognition-budget"
            assert validation_posts == []
        finally:
            browser.close()
            playwright.stop()


def test_credential_is_cleared_on_selection_change_and_while_request_is_pending(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    provider_document = _remote_provider_document(credential_available=True)
    first_profile = provider_document["profiles"][0]
    second_profile = {
        **first_profile,
        "profile_id": "remote-second",
        "display_label": "Remote second",
        "revision_sha256": "c" * 64,
        "credential_slot_id": "d" * 64,
        "credential_available": False,
    }
    updated_second_profile = {**second_profile, "credential_available": True}
    provider_document["profiles"].append(second_profile)

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.add_init_script(
                """
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = (input, init) => {
                    const url = String(input);
                    if (url.includes('/api/providers/remote-second/revisions/') &&
                        url.endsWith('/credential')) {
                      return new Promise((resolve, reject) => {
                        window.__resumeCredentialRequest = () => {
                          originalFetch(input, init).then(resolve, reject);
                        };
                      });
                    }
                    return originalFetch(input, init);
                  };
                """
            )
            page.route(
                "**/api/providers",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(provider_document),
                ),
            )
            page.route(
                "**/api/providers/remote-second/revisions/*/credential",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(updated_second_profile),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")
            expect(page.locator("#provider-profile option")).to_have_count(2)

            page.locator("#provider-credential").fill("unsent-" + "first-profile")
            page.locator("#provider-profile").select_option(f"remote-second/{'c' * 64}")
            expect(page.locator("#provider-credential")).to_have_value("")

            page.locator("#provider-credential").fill("unsent-" + "second-profile")
            page.locator("#cognition-mode").select_option("rules")
            expect(page.locator("#provider-credential")).to_have_value("")

            page.locator("#cognition-mode").select_option("remote")
            page.locator("#provider-profile").select_option(f"remote-second/{'c' * 64}")
            page.locator("#provider-credential").fill("pending-" + "credential")
            page.locator("#provider-save-credential").click()

            expect(page.locator("#provider-credential-status")).to_contain_text("Saving")
            expect(page.locator("#provider-credential")).to_have_value("")
            page.locator("#provider-profile").select_option(f"remote-demo/{'a' * 64}")
            page.evaluate("window.__resumeCredentialRequest()")
            expect(page.locator("#provider-save-credential")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-credential-status")).to_have_text("")
            expect(page.locator("#provider-credential")).to_have_value("")
            page.locator("#provider-profile").select_option(f"remote-second/{'c' * 64}")
            expect(page.locator("#provider-credential-state")).to_have_text("Available on server")
            expect(page.locator("#provider-check")).to_be_enabled()
        finally:
            browser.close()
            playwright.stop()


def test_stale_provider_actions_release_controls_for_current_selection(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    provider_document = _remote_provider_document(credential_available=True)
    first_profile = provider_document["profiles"][0]
    second_profile = {
        **first_profile,
        "profile_id": "remote-second",
        "display_label": "Remote second",
        "revision_sha256": "c" * 64,
        "credential_slot_id": "d" * 64,
    }
    created_profile = {
        **first_profile,
        "profile_id": "remote-created",
        "display_label": "Remote created",
        "revision_sha256": "e" * 64,
        "credential_slot_id": "f" * 64,
    }
    provider_document["profiles"].append(second_profile)

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.add_init_script(
                """
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = (input, init) => {
                    const url = String(input);
                    const method = init?.method || 'GET';
                    const delayed =
                      (url.endsWith('/api/providers') && method === 'POST') ||
                      url.endsWith('/check') || url.endsWith('/credential');
                    if (delayed) {
                      return new Promise((resolve, reject) => {
                        window.__resumeProviderAction = () => {
                          originalFetch(input, init).then(resolve, reject);
                        };
                      });
                    }
                    return originalFetch(input, init);
                  };
                """
            )

            def providers(route: Route) -> None:
                response = provider_document if route.request.method == "GET" else created_profile
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(response),
                )

            page.route("**/api/providers", providers)
            page.route(
                "**/api/providers/*/revisions/*/check",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": "compatible", "reason_code": None}),
                ),
            )
            page.route(
                "**/api/providers/*/revisions/*/credential",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(second_profile),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")

            page.locator("#provider-create-details").evaluate("node => { node.open = true; }")
            page.locator("#provider-profile-id").fill("remote-created")
            page.locator("#provider-label").fill("Remote created")
            page.locator("#provider-model").fill("fictional-model")
            page.locator("#provider-create-submit").click()
            expect(page.locator("#provider-status")).to_contain_text("Saving")
            page.locator("#cognition-mode").select_option("local")
            page.evaluate("window.__resumeProviderAction()")
            expect(page.locator("#provider-create-submit")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-status")).to_have_text("")

            page.locator("#cognition-mode").select_option("remote")
            page.locator("#provider-profile").select_option(f"remote-demo/{'a' * 64}")
            page.locator("#provider-check").click()
            expect(page.locator("#provider-check-status")).to_contain_text("Checking")
            page.locator("#provider-profile").select_option(f"remote-second/{'c' * 64}")
            page.evaluate("window.__resumeProviderAction()")
            expect(page.locator("#provider-check")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-check-status")).to_have_text("")

            page.locator("#provider-credential").fill("race-" + "credential")
            page.locator("#provider-save-credential").click()
            expect(page.locator("#provider-credential-status")).to_contain_text("Saving")
            expect(page.locator("#provider-credential")).to_have_value("")
            page.locator("#provider-profile").select_option(f"remote-demo/{'a' * 64}")
            page.evaluate("window.__resumeProviderAction()")
            expect(page.locator("#provider-save-credential")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-credential-status")).to_have_text("")
        finally:
            browser.close()
            playwright.stop()


def test_provider_registry_load_survives_mode_change_while_pending(tmp_path: Path) -> None:
    browser_path = _installed_browser()

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.add_init_script(
                """
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = (input, init) => {
                    const url = String(input);
                    if (url.endsWith('/api/providers') && (init?.method || 'GET') === 'GET') {
                      return new Promise((resolve, reject) => {
                        window.__resumeProviderLoad = () => {
                          originalFetch(input, init).then(resolve, reject);
                        };
                      });
                    }
                    return originalFetch(input, init);
                  };
                """
            )
            page.route(
                "**/api/providers",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_remote_provider_document(credential_available=True)),
                ),
            )
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_function("() => typeof window.__resumeProviderLoad === 'function'")
            page.locator("#cognition-mode").evaluate(
                """node => {
                  node.value = 'remote';
                  node.dispatchEvent(new Event('change', {bubbles: true}));
                }"""
            )
            page.evaluate("window.__resumeProviderLoad()")

            expect(page.locator("#provider-profile")).to_have_value(
                f"remote-demo/{'a' * 64}", timeout=10_000
            )
            expect(page.locator("#provider-profile")).to_be_enabled()
            expect(page.locator("#provider-endpoint")).to_have_text(
                "https://api.example.invalid/v1"
            )
            expect(page.locator("#error-banner")).to_be_hidden()
        finally:
            browser.close()
            playwright.stop()


def test_stale_provider_check_cannot_override_a_newer_mode_selection(tmp_path: Path) -> None:
    browser_path = _installed_browser()

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(browser_path, base_url)
        try:
            page.add_init_script(
                """
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = (input, init) => {
                    const url = String(input);
                    if (url.includes('/api/providers/remote-demo/revisions/') &&
                        url.endsWith('/check')) {
                      return new Promise(resolve => {
                        window.__resolveProviderCheck = () => resolve(new Response(
                          JSON.stringify({status: 'compatible', reason_code: null}),
                          {status: 200, headers: {'Content-Type': 'application/json'}}
                        ));
                      });
                    }
                    return originalFetch(input, init);
                  };
                """
            )
            page.route(
                "**/api/providers",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_remote_provider_document(credential_available=True)),
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").select_option("remote")
            page.locator("#provider-check").click()
            expect(page.locator("#provider-check-status")).to_have_text("Checking compatibility…")
            page.locator("#cognition-mode").select_option("rules")
            page.evaluate("window.__resolveProviderCheck()")
            page.wait_for_timeout(100)

            expect(page.locator("#provider-settings")).to_be_hidden()
            expect(page.locator("#provider-check-status")).to_have_text("")
            expect(page.locator("#cognition-mode")).to_have_value("rules")
            page.locator("#cognition-mode").select_option("remote")
            expect(page.locator("#provider-check-status")).to_have_text(
                "Compatible inference confirmed."
            )
        finally:
            browser.close()
            playwright.stop()


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
    assert payload["schema_version"] == 3
    payload.pop("cognition")
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


def test_audience_templates_preserve_causal_values_and_submit_schema_three(tmp_path: Path) -> None:
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
            assert posted[-1]["schema_version"] == 3
            assert posted[-1]["cognition"] == {
                "mode": "rules",
                "provider_profile_id": None,
                "provider_revision_sha256": None,
                "aggregate_output_token_ceiling": 0,
            }
            assert "cohort" not in posted[-1]
            assert posted[-1]["population"]["personas"][1]["interests"] == ["books", "cooking"]
            manifest = json.loads((root / "city-runs/persona-browser/run.json").read_text())
            assert manifest["schema_version"] == 9
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
    original = workbench_web.WorkbenchRunRepository.prepare_scientific

    def blocked_prepare(repository, validated, operation):
        entered.set()
        if not release.wait(30):
            raise RuntimeError("cancellation binding gate timed out")
        return original(repository, validated, operation)

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare_scientific",
        blocked_prepare,
    )
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
            summary.update(
                run_schema_version=9,
                days=2,
                frame_count=2880,
                cognition_mode="rules",
                provider_profile_id=None,
                provider_revision_sha256=None,
                aggregate_output_token_ceiling=0,
            )
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


@pytest.mark.parametrize("input_version,run_version", [(1, 7), (2, 8), (3, 9), (4, 10)])
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
            if input_version == 4:
                bounded = {
                    "effect_scope": "bounded-signals-v1",
                    "cognition_mode": "mock",
                    "provider_profile_id": None,
                    "provider_revision_sha256": None,
                    "funded_response_limit": 60,
                    "aggregate_output_token_ceiling": 0,
                }
                settings.update(bounded)
                summary.update(
                    bounded,
                    workbench_input_schema_version=4,
                    opportunity_count=3,
                    impression_count=3,
                    noticed_count=2,
                    response_count=2,
                    eligible_response_count=2,
                    selected_request_count=2,
                    funded_request_count=2,
                    budget_skipped_count=0,
                    unselected_response_count=0,
                    applied_response_count=2,
                    rules_count=0,
                    mock_count=2,
                    provider_count=0,
                    cache_count=0,
                    fallback_count=0,
                    neutralized_response_count=0,
                    uncertain_result_count=0,
                    safety_neutralized_result_count=0,
                )
            elif input_version == 3:
                settings.update(
                    cognition_mode="rules",
                    provider_profile_id=None,
                    provider_revision_sha256=None,
                    aggregate_output_token_ceiling=0,
                )
                summary.update(
                    cognition_mode="rules",
                    provider_profile_id=None,
                    provider_revision_sha256=None,
                    aggregate_output_token_ceiling=0,
                )
            else:
                for field in (
                    "cognition_mode",
                    "provider_profile_id",
                    "provider_revision_sha256",
                    "aggregate_output_token_ceiling",
                ):
                    settings.pop(field, None)
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
            expect(page.locator("#job-settings")).to_contain_text("Simulation")
            expect(page.locator("#job-settings")).to_contain_text("Deterministic rules")
            if input_version == 4:
                expect(page.locator("#job-settings")).to_contain_text("Bounded AI cognition")
                expect(page.locator("#job-settings")).not_to_contain_text("Explanations")
                expect(page.locator("#job-phase-track li")).to_have_count(7)
            else:
                expect(page.locator("#job-settings")).to_contain_text("Explanations")
            expect(page.locator("#error-banner")).to_be_hidden()
        finally:
            browser.close()
            playwright.stop()


def test_offline_completed_job_with_provider_half_binding_is_refused(
    tmp_path: Path,
) -> None:
    from tests.security.test_workbench_browser_security import _job_documents

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            settings, job = _job_documents()
            settings.update(days=1, agent_count=1)
            settings["provider_profile_id"] = "misplaced-profile"
            summary = _library_summary("city-study-001").model_dump(mode="json")
            summary.update(
                run_schema_version=9,
                cognition_mode="rules",
                provider_profile_id="misplaced-profile",
                provider_revision_sha256=None,
                aggregate_output_token_ceiling=0,
            )
            job.update(phase="completed", can_cancel=False, result=summary)

            def active(route: Route) -> None:
                response = route.fetch()
                document = response.json()
                document["active_job"] = job
                route.fulfill(response=response, json=document)

            page.route("**/api/workbench", active)
            page.goto(base_url, wait_until="networkidle")

            expect(page.locator("#error-banner")).to_be_visible()
            expect(page.locator("#error-banner")).to_contain_text("incompatible")
            expect(page.locator("#stage-run")).to_be_disabled()
            expect(page.locator("#job-inspect-link")).to_be_hidden()
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
                "purchase intention are always rule-computed"
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


def test_city_pack_upload_is_authoritative_preserves_draft_and_runs_from_new_city(
    tmp_path: Path,
) -> None:
    browser_path = _installed_browser()
    source_url_prefix = "https://outside.invalid/"
    source_url = source_url_prefix + "s" * (2060 - len(source_url_prefix))
    attribution = (
        "\u0646\u0642\u0634\u0647 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc "
        '<img src="https://outside.invalid/pixel" onerror="window.cityExecuted=true">'
    )
    document = _city_pack_upload(
        "operator-harbor",
        name="\u0634\u0647\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc",
        attribution=attribution,
        source_url=source_url,
    )

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(browser_path, base_url)
        external_requests: list[str] = []
        local_requests: list[str] = []
        try:
            page.on(
                "request",
                lambda request: (
                    external_requests.append(request.url)
                    if not request.url.startswith(base_url)
                    else local_requests.append(urlparse(request.url).path)
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            local_requests.clear()
            expect(page.locator("#city-qualification")).to_have_text("Bundled fictional fixture")
            page.locator("#city-continue").click()
            page.locator("#campaign-name").fill("Draft that must survive a city change")
            page.locator("#phone-probability").fill("0.17")
            page.locator("#roadside-enabled").check()
            page.locator("#road-fraction").fill("0.33")
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("operator-harbor-run")
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            page.locator("#stage-city").click()
            local_requests.clear()

            with page.expect_response("**/api/catalog/cities/upload") as uploaded:
                page.locator("#city-pack-file").set_input_files(
                    {
                        "name": "private-customer-map.json",
                        "mimeType": "application/json",
                        "buffer": document,
                    }
                )
            response = uploaded.value
            assert response.status == 201
            assert response.request.post_data_buffer == document
            assert response.request.headers["content-type"] == "application/json"
            assert response.request.headers["x-adlife-csrf"] == "browser-workbench-token"
            accepted = response.json()

            expect(page.locator("#city-upload-status")).to_have_text(
                "City pack ready.", timeout=15_000
            )
            expect(page.locator("#city-upload-error")).to_be_empty()
            expect(page.locator("#city-select")).to_have_value("operator-harbor")
            expect(page.locator("#city-name")).to_have_text(
                "\u0634\u0647\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc"
            )
            expect(page.locator("#city-qualification")).to_have_text(
                "Operator supplied - rights not reviewed"
            )
            expect(page.locator("#map-summary")).to_contain_text(
                "City-pack geometry only; no live map or traffic feed."
            )
            expect(page.locator("#map-summary")).not_to_contain_text("fictional")
            expect(page.locator("#city-map")).to_have_attribute(
                "aria-label", "Verified city-pack road map"
            )
            expect(page.locator("#city-pack-file")).to_have_value("")
            expect(page.locator("#validation-review")).to_be_hidden()
            expect(page.locator("#start-run")).to_be_disabled()
            expect(page.locator("#campaign-name")).to_have_value(
                "Draft that must survive a city change"
            )
            expect(page.locator("#phone-probability")).to_have_value("0.17")
            expect(page.locator("#road-fraction")).to_have_value("0.33")
            expect(page.locator("#run-id")).to_have_value("operator-harbor-run")
            expect(page.locator("#roadside-enabled")).not_to_be_checked()

            page.locator("#city-details summary").click()
            for selector, value in (
                ("#city-provider", "Operator mapping team"),
                ("#city-dataset", "Prepared street extract"),
                ("#city-version", "2026.10"),
                ("#city-published-on", "2026-10-01"),
                ("#city-license", "Operator declared"),
                ("#city-time-zone", "Etc/UTC"),
                ("#city-hash", accepted["pack_sha256"]),
            ):
                expect(page.locator(selector)).to_have_text(value)
            expect(page.locator("#city-bounds")).to_contain_text("0.08")
            expect(page.locator("#city-attribution")).to_have_text(attribution)
            expect(page.locator("#city-attribution")).to_have_attribute("dir", "auto")
            assert page.locator("#city-details img, #city-details script").count() == 0
            assert page.evaluate("window.cityExecuted === undefined")
            assert source_url not in page.locator("body").inner_text()
            assert "private-customer-map.json" not in page.locator("body").inner_text()
            assert page.locator("#city-source-url").count() == 0
            assert external_requests == []
            assert local_requests[:3] == [
                "/api/catalog/cities/upload",
                "/api/catalog/cities",
                "/api/catalog/cities/operator-harbor",
            ]

            page.locator("#city-continue").click()
            page.locator("#roadside-enabled").check()
            page.locator("#persona-count").fill("1")
            page.locator("#campaign-continue").click()
            page.locator("#persona-use-total").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            expect(page.locator("#job-inspect-link")).to_have_attribute(
                "href", "/runs/operator-harbor-run"
            )
            page.locator("#job-inspect-link").click()
            page.wait_for_url("**/runs/operator-harbor-run")
            expect(page.locator("#saved-run-label")).to_contain_text("operator-harbor-run")
            assert (root / "city-packs" / "operator-harbor.json").is_file()
            assert (root / "city-runs" / "operator-harbor-run" / "run.json").is_file()
        finally:
            browser.close()
            playwright.stop()


def test_failed_city_pack_upload_preserves_selected_city_and_validated_draft(
    tmp_path: Path,
) -> None:
    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-name").fill("Keep this complete draft")
            page.locator("#roadside-enabled").check()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("preserved-after-refusal")
            page.locator("#validate-run").click()
            expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
            population_before = _persona_population(page)
            page.locator("#stage-city").click()

            with page.expect_response("**/api/catalog/cities/upload") as refused:
                page.locator("#city-pack-file").set_input_files(
                    {
                        "name": r"C:\private\customer-secret.json",
                        "mimeType": "application/json",
                        "buffer": b"{}",
                    }
                )
            assert refused.value.status == 422
            expect(page.locator("#city-upload-error")).to_contain_text(
                "not a valid CityPack V2 document"
            )
            expect(page.locator("#city-upload-status")).to_be_empty()
            expect(page.locator("#city-pack-file")).to_have_value("")
            expect(page.locator("#city-pack-choose")).to_be_focused()
            expect(page.locator("#city-select")).to_have_value("fictional-grid-v2")
            page.locator("#stage-run").click()
            expect(page.locator("#validation-review")).to_be_visible()
            expect(page.locator("#start-run")).to_be_enabled()
            expect(page.locator("#campaign-name")).to_have_value("Keep this complete draft")
            expect(page.locator("#roadside-enabled")).to_be_checked()
            expect(page.locator("#run-id")).to_have_value("preserved-after-refusal")
            assert _persona_population(page) == population_before
            body = page.locator("body").inner_text()
            assert "customer-secret.json" not in body
            assert r"C:\private" not in body
            assert not (root / "city-packs").exists()
        finally:
            browser.close()
            playwright.stop()


def test_city_pack_upload_survives_server_restart_and_fits_keyboard_small_screen(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    document = _city_pack_upload("restart-harbor")

    first_app = create_city_workbench_app(workspace, csrf_token="first-upload-token")
    with _serve(first_app) as base_url:
        playwright, browser, page = _open_page(_installed_browser(), base_url, width=390)
        try:
            page.emulate_media(reduced_motion="reduce")
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-pack-choose").focus()
            expect(page.locator("#city-pack-choose")).to_be_focused()
            page.locator("#city-pack-file").set_input_files(
                {
                    "name": "restart.json",
                    "mimeType": "application/json",
                    "buffer": document,
                }
            )
            expect(page.locator("#city-upload-status")).to_have_text(
                "City pack ready.", timeout=15_000
            )
            assert page.locator("#city-pack-intake").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            assert (
                page.locator("#city-pack-choose").evaluate(
                    "element => getComputedStyle(element).animationName"
                )
                == "none"
            )
        finally:
            browser.close()
            playwright.stop()

    second_app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="second-upload-token",
    )
    with _serve(second_app) as base_url:
        playwright, browser, page = _open_page(_installed_browser(), base_url, width=390)
        try:
            page.goto(base_url, wait_until="networkidle")
            expect(page.locator('#city-select option[value="restart-harbor"]')).to_have_count(1)
            page.locator("#city-select").select_option("restart-harbor")
            expect(page.locator("#city-name")).to_have_text("Operator City restart-harbor")
            expect(page.locator("#city-qualification")).to_have_text(
                "Operator supplied - rights not reviewed"
            )
        finally:
            browser.close()
            playwright.stop()


def test_near_limit_city_pack_completes_bounded_authoritative_detail_confirmation(
    tmp_path: Path,
) -> None:
    document = _near_limit_city_pack_upload("near-limit-operator-city")
    assert 3_800_000 < len(document) <= 4_194_304

    with _workbench(tmp_path) as (base_url, root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            with (
                page.expect_response(
                    "**/api/catalog/cities/near-limit-operator-city",
                    timeout=90_000,
                ) as detail_response,
                page.expect_response(
                    "**/api/catalog/cities/upload",
                    timeout=90_000,
                ) as uploaded,
            ):
                page.locator("#city-pack-file").set_input_files(
                    {
                        "name": "near-limit-city.json",
                        "mimeType": "application/json",
                        "buffer": document,
                    }
                )
            assert uploaded.value.status == 201
            assert detail_response.value.status == 200
            expect(page.locator("#city-upload-status")).to_have_text(
                "City pack ready.", timeout=90_000
            )
            expect(page.locator("#city-upload-error")).to_be_empty()
            expect(page.locator("#city-select")).to_have_value("near-limit-operator-city")
            expect(page.locator("#city-name")).to_have_text(
                "Operator City near-limit-operator-city"
            )
            persisted_size = (root / "city-packs" / "near-limit-operator-city.json").stat().st_size
            assert 4_190_000 < persisted_size <= 4_194_304
            assert persisted_size > len(document)
            assert len(detail_response.value.body()) <= 4_259_840
        finally:
            browser.close()
            playwright.stop()


def test_oversized_city_pack_is_refused_before_browser_file_read_or_post(
    tmp_path: Path,
) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        upload_requests: list[str] = []
        try:
            page.on(
                "request",
                lambda request: (
                    upload_requests.append(request.url)
                    if urlparse(request.url).path == "/api/catalog/cities/upload"
                    else None
                ),
            )
            page.goto(base_url, wait_until="networkidle")
            page.evaluate(
                """() => {
                  window.cityPackFileReads = 0;
                  const original = File.prototype.arrayBuffer;
                  File.prototype.arrayBuffer = function () {
                    window.cityPackFileReads += 1;
                    return original.call(this);
                  };
                }"""
            )
            page.locator("#city-pack-file").set_input_files(
                {
                    "name": "oversized.json",
                    "mimeType": "application/json",
                    "buffer": b"x" * 4_194_305,
                }
            )

            expect(page.locator("#city-upload-error")).to_have_text(
                "Choose a city pack no larger than 4 MiB."
            )
            expect(page.locator("#city-upload-status")).to_be_empty()
            expect(page.locator("#city-pack-file")).to_have_value("")
            expect(page.locator("#city-pack-choose")).to_be_focused()
            assert page.evaluate("window.cityPackFileReads") == 0
            assert upload_requests == []
        finally:
            browser.close()
            playwright.stop()


def test_rights_reviewed_city_uses_the_exact_reviewed_trust_label(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)

        def rights_reviewed(route: Route) -> None:
            response = route.fetch()
            document = response.json()
            path = urlparse(route.request.url).path
            if path == "/api/catalog/cities":
                selected = next(
                    city for city in document["cities"] if city["city_id"] == "fictional-grid-v2"
                )
            else:
                selected = document
            selected.update(
                {
                    "data_origin": "real-world",
                    "qualification": "rights-reviewed",
                    "reviewer_role": "Mapping rights reviewer",
                    "reviewed_on": "2026-10-02",
                }
            )
            route.fulfill(response=response, json=document)

        try:
            page.route("**/api/catalog/cities**", rights_reviewed)
            page.goto(base_url, wait_until="networkidle")
            expect(page.locator("#city-qualification")).to_have_text("Rights reviewed")
            expect(page.locator("#error-banner")).to_be_hidden()
        finally:
            browser.close()
            playwright.stop()


def test_stale_city_pack_upload_response_cannot_replace_the_newer_selection(
    tmp_path: Path,
) -> None:
    first_document = _city_pack_upload("stale-upload-city")
    second_document = _city_pack_upload("current-upload-city")

    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        held: list[tuple[Route, APIResponse]] = []
        calls = 0

        def reorder_uploads(route: Route) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                held.append((route, route.fetch()))
                return
            route.continue_()

        try:
            page.route("**/api/catalog/cities/upload", reorder_uploads)
            page.goto(base_url, wait_until="networkidle")
            with page.expect_request("**/api/catalog/cities/upload"):
                page.locator("#city-pack-file").set_input_files(
                    {
                        "name": "older.json",
                        "mimeType": "application/json",
                        "buffer": first_document,
                    }
                )
            with page.expect_response("**/api/catalog/cities/upload") as current_upload:
                page.locator("#city-pack-file").set_input_files(
                    {
                        "name": "current.json",
                        "mimeType": "application/json",
                        "buffer": second_document,
                    }
                )
            # The first handler records its upload only after its own server fetch returns,
            # which can be after the newer upload has already been answered.
            _wait_for_route_handler(page, lambda: len(held) == 1, what="the held stale upload")
            assert len(held) == 1
            accepted = current_upload.value.json()
            assert accepted["city_id"] == "current-upload-city"
            expect(page.locator("#city-upload-status")).to_have_text(
                "City pack ready.", timeout=15_000
            )
            expect(page.locator("#city-select")).to_have_value("current-upload-city")

            stale_route, stale_response = held.pop()
            # Deliver the stale answer to the page before asserting that it changed nothing.
            with page.expect_response("**/api/catalog/cities/upload"):
                stale_route.fulfill(response=stale_response)
            page.wait_for_timeout(300)

            expect(page.locator("#city-select")).to_have_value("current-upload-city")
            expect(page.locator("#city-name")).to_have_text("Operator City current-upload-city")
            expect(page.locator("#city-upload-status")).to_have_text("City pack ready.")
            expect(page.locator("#city-upload-error")).to_be_empty()
        finally:
            for route, _response in held:
                route.abort()
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

    def block_first_validation(
        payload: dict[str, object],
        resolver: workbench_web.WorkbenchCityResolver,
    ):
        nonlocal calls
        with lock:
            calls += 1
            call = calls
        if call == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("stale validation gate timed out")
        return original(payload, resolver)

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

    def block_submission(payload, resolver):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            if not release.wait(15):
                raise RuntimeError("job acceptance gate timed out")
        return original(payload, resolver)

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


def test_setup_never_waits_for_a_slow_run_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    original = workbench_web.WorkbenchRunRepository.list_runs

    def slow_list_runs(repository: object, *, offset: int = 0, limit: int = 20) -> WorkbenchRunPage:
        # Listing re-verifies every saved run, which takes seconds once runs accumulate.
        entered.set()
        if not release.wait(20):
            raise RuntimeError("library gate timed out")
        return original(repository, offset=offset, limit=limit)  # type: ignore[arg-type]

    monkeypatch.setattr(workbench_web.WorkbenchRunRepository, "list_runs", slow_list_runs)
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url)
                assert entered.wait(10)
                expect(page.locator("#city-continue")).to_be_enabled(timeout=10_000)
                expect(page.locator("#library-count")).to_have_text("Loading verified runs…")
                page.locator("#city-continue").click()
                page.locator("#campaign-continue").click()
                expect(page.locator("#validate-run")).to_be_enabled()
                expect(page.locator("#run-id")).to_have_value("city-study-001")
                release.set()
                expect(page.locator("#library-count")).to_have_text(
                    "0 verified runs", timeout=10_000
                )
                expect(page.locator("#error-banner")).to_be_hidden()
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_inspect_stage_reuses_the_library_listing_already_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    original = workbench_web.WorkbenchRunRepository.list_runs

    def counted_list_runs(
        repository: object, *, offset: int = 0, limit: int = 20
    ) -> WorkbenchRunPage:
        calls.append(offset)
        entered.set()
        if not release.wait(20):
            raise RuntimeError("library gate timed out")
        return original(repository, offset=offset, limit=limit)  # type: ignore[arg-type]

    monkeypatch.setattr(workbench_web.WorkbenchRunRepository, "list_runs", counted_list_runs)
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url)
                assert entered.wait(10)
                # Opening INSPECT during the page-load listing must not re-verify every run again.
                page.locator("#stage-inspect").click()
                expect(page.locator("#panel-inspect")).to_be_visible()
                page.wait_for_timeout(300)
                assert calls == [0]
                release.set()
                expect(page.locator("#library-count")).to_have_text(
                    "0 verified runs", timeout=10_000
                )
                # Once nothing is loading, returning to INSPECT asks for a fresh listing.
                page.locator("#stage-city").click()
                page.locator("#stage-inspect").click()
                for _ in range(50):
                    if len(calls) == 2:
                        break
                    page.wait_for_timeout(100)
                assert calls == [0, 0]
                expect(page.locator("#error-banner")).to_be_hidden()
            finally:
                release.set()
                browser.close()
                playwright.stop()
    finally:
        release.set()


def test_late_library_never_rewrites_a_validated_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = _installed_browser()
    release = threading.Event()

    def late_list_runs(
        _repository: object, *, offset: int = 0, limit: int = 20
    ) -> WorkbenchRunPage:
        if not release.wait(20):
            raise RuntimeError("library gate timed out")
        # The listing reveals that the suggested ID is already taken.
        return _library_page(offset=offset, run_id="city-study-001")

    monkeypatch.setattr(workbench_web.WorkbenchRunRepository, "list_runs", late_list_runs)
    try:
        with _workbench(tmp_path) as (base_url, _root):
            playwright, browser, page = _open_page(browser_path, base_url)
            try:
                page.goto(base_url)
                page.locator("#city-continue").click()
                page.locator("#campaign-continue").click()
                page.locator("#validate-run").click()
                expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
                release.set()
                expect(page.locator("#library-count")).to_have_text(
                    "21 verified runs", timeout=10_000
                )
                # The validated draft keeps its run ID instead of silently drifting.
                expect(page.locator("#run-id")).to_have_value("city-study-001")
                expect(page.locator("#review-run")).to_have_text("city-study-001")
                expect(page.locator("#validation-review")).to_be_visible()
                # An unvalidated draft takes the next free suggestion on the next listing.
                page.locator("#seed").fill("43")
                page.locator("#stage-inspect").click()
                expect(page.locator("#library-count")).to_have_text("21 verified runs")
                page.locator("#stage-run").click()
                expect(page.locator("#run-id")).to_have_value("city-study-002", timeout=10_000)
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
            assert stored.manifest.schema_version == 9
            assert stored.workbench_input.schema_version == 3

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
    original = workbench_web.WorkbenchRunRepository.prepare_scientific

    def blocked_prepare(repository: object, validated: object, operation: object):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("refresh resume gate timed out")
        return original(repository, validated, operation)

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare_scientific",
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
                # The reload reopens on the live job rather than on an empty city step.
                expect(page.locator("#panel-run")).to_be_visible()
                expect(page.locator("#stage-run")).to_have_attribute("aria-current", "step")
                expect(page.locator("#job-panel")).to_be_visible()
                page.locator("#stage-run").click()
                expect(page.locator("#job-panel")).to_be_visible()
                expect(page.locator("#job-settings")).to_contain_text("refresh-resume")
                expect(page.locator("#job-settings")).to_contain_text("9223372036854775807")
                expect(page.locator("#job-phase")).to_have_text("SIMULATING")

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
    original = workbench_web.WorkbenchRunRepository.prepare_scientific

    def blocked_prepare(repository: object, validated: object, operation: object):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("cancellation gate timed out")
        return original(repository, validated, operation)

    monkeypatch.setattr(
        workbench_web.WorkbenchRunRepository,
        "prepare_scientific",
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
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#cognition-mode").focus()
            provider_snapshot = page.evaluate(
                """() => ({
                  documentWidth: document.documentElement.scrollWidth,
                  viewport: window.innerWidth,
                  active: document.activeElement.id,
                  outline: getComputedStyle(document.activeElement).outlineStyle,
                  sectionWidth: document.getElementById('explanations-section')
                    .getBoundingClientRect().width,
                  panelWidth: document.querySelector('.setup-column')
                    .getBoundingClientRect().width,
                  transitions: getComputedStyle(document.getElementById('provider-settings'))
                    .transitionDuration,
                })"""
            )
            assert provider_snapshot["documentWidth"] <= provider_snapshot["viewport"]
            assert provider_snapshot["sectionWidth"] <= provider_snapshot["panelWidth"]
            assert provider_snapshot["active"] == "cognition-mode"
            assert provider_snapshot["outline"] != "none"
            assert provider_snapshot["transitions"] in {"0s", "0ms"}
        finally:
            browser.close()
            playwright.stop()


def _wait_in_viewport(page, selector: str) -> None:
    """Wait out smooth scrolling until the element sits fully inside the viewport."""
    page.wait_for_function(
        """selector => {
          const box = document.querySelector(selector).getBoundingClientRect();
          return box.height > 0 && box.top >= -1 && box.bottom <= window.innerHeight + 1;
        }""",
        arg=selector,
        timeout=5_000,
    )


def test_a_refused_campaign_field_opens_its_hidden_step_with_a_receipt(tmp_path: Path) -> None:
    """Shortening the run below a delivery window must never make Validate a silent no-op."""
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#days").fill("1")
            page.locator("#validate-run").click()

            message = "Window 2 must use a day from 1 through 1."
            expect(page.locator("#panel-campaign")).to_be_visible()
            expect(page.locator("#panel-run")).to_be_hidden()
            expect(page.locator("#stage-campaign")).to_have_attribute("aria-current", "step")
            expect(page.locator("#phone-windows-error")).to_have_text(message)
            day = page.locator("#phone-windows .schedule-row").nth(1).locator("input").first
            expect(day).to_be_focused()
            expect(day).to_have_attribute("aria-invalid", "true")
            _wait_in_viewport(page, "#phone-windows-error")
            expect(page.locator("#validation-state")).to_have_text(
                f"Validation was refused: {message}"
            )

            # A server refusal of a campaign field opens the campaign step the same way.
            page.locator("#phone-windows .remove-window").nth(1).click()
            page.route(
                "**/api/scenarios/validate",
                lambda route: route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": 1,
                            "error": {
                                "code": "invalid-fields",
                                "message": "One or more request fields are invalid.",
                                "fields": {
                                    "scenario.campaign.relative_price": "Use a positive price."
                                },
                            },
                        }
                    ),
                ),
            )
            page.locator("#campaign-continue").click()
            expect(page.locator("#panel-run")).to_be_visible()
            page.locator("#validate-run").click()
            expect(page.locator("#panel-campaign")).to_be_visible()
            expect(page.locator("#relative-price")).to_be_focused()
            expect(page.locator("#relative-price-error")).to_have_text("Use a positive price.")
            page.locator("#stage-run").click()
            expect(page.locator("#validation-state")).to_have_text(
                "Validation was refused: Use a positive price."
            )
            expect(page.locator("#start-run")).to_be_disabled()
        finally:
            browser.close()
            playwright.stop()


def test_a_saved_run_id_is_refused_on_its_field_at_validation_and_at_start(
    tmp_path: Path,
) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        validations: list[str] = []
        page.on(
            "request",
            lambda request: (
                validations.append(request.url)
                if request.method == "POST" and request.url.endswith("/api/scenarios/validate")
                else None
            ),
        )
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("browser-duplicate")
            page.locator("#agent-count").fill("1")
            _set_audience_count(page, 1)
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            page.wait_for_function(
                "() => document.querySelector("
                "'#run-library a[href=\"/runs/browser-duplicate\"]') !== null",
                timeout=30_000,
            )

            # The verified library already shows the saved ID: refuse it before any request.
            sent = len(validations)
            page.locator("#run-id").fill("browser-duplicate")
            page.locator("#validate-run").click()
            message = "A saved run already uses this ID. Choose a new run ID."
            expect(page.locator("#run-id-error")).to_have_text(message)
            expect(page.locator("#run-id")).to_be_focused()
            expect(page.locator("#run-id")).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#validation-state")).to_have_text(
                f"Validation was refused: {message}"
            )
            assert len(validations) == sent

            # A listing that has not caught up still gets the server's refusal on the field.
            empty = {
                "schema_version": 1,
                "offset": 0,
                "limit": 20,
                "returned_count": 0,
                "verified_total": 0,
                "scanned_count": 0,
                "corrupt_count": 0,
                "truncated": False,
                "runs": [],
            }
            page.route(
                "**/api/runs?offset=0&limit=20",
                lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(empty)
                ),
            )
            page.locator("#stage-inspect").click()
            expect(page.locator("#library-count")).to_have_text("0 verified runs")
            page.locator("#stage-run").click()
            page.locator("#run-id").fill("browser-duplicate")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").scroll_into_view_if_needed()
            with page.expect_response("**/api/jobs") as refused:
                page.locator("#start-run").click()
            assert refused.value.status == 409
            expect(page.locator("#run-id-error")).to_have_text(message)
            expect(page.locator("#run-id")).to_be_focused()
            _wait_in_viewport(page, "#run-id")
            expect(page.locator("#validation-state")).to_have_text(f"Start was refused: {message}")
            expect(page.locator("#validation-review")).to_be_hidden()
            expect(page.locator("#start-run")).to_be_disabled()
            expect(page.locator("#error-banner")).to_be_hidden()
        finally:
            browser.close()
            playwright.stop()


def test_a_start_refusal_without_a_field_is_shown_and_focused_on_screen(
    tmp_path: Path,
) -> None:
    busy = {
        "schema_version": 1,
        "error": {
            "code": "job-busy",
            "message": "Another workbench run is already active.",
            "fields": {},
        },
    }
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.route(
                "**/api/jobs",
                lambda route: route.fulfill(
                    status=409, content_type="application/json", body=json.dumps(busy)
                ),
            )
            page.locator("#start-run").scroll_into_view_if_needed()
            page.locator("#start-run").click()
            expect(page.locator("#error-banner-message")).to_have_text(
                "Another workbench run is already active."
            )
            expect(page.locator("#error-banner")).to_be_focused()
            _wait_in_viewport(page, "#error-banner")
        finally:
            browser.close()
            playwright.stop()


def test_run_again_restores_every_saved_input_with_a_sibling_run_id(tmp_path: Path) -> None:
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        posted: list[dict[str, object]] = []
        page.on(
            "request",
            lambda request: (
                posted.append(json.loads(request.post_data or "{}"))
                if request.method == "POST" and request.url.endswith("/api/scenarios/validate")
                else None
            ),
        )
        try:
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#scenario-name").fill("Reuse study")
            page.locator("#campaign-name").fill("Reuse campaign")
            page.locator("#relative-price").fill("1.25")
            page.locator("input[name='phone-activity'][value='home']").check()
            page.locator("#phone-cap").fill("4")
            page.locator("#phone-windows .remove-window").nth(1).click()
            page.locator("#phone-window-0-start").fill("07:00")
            page.locator("#phone-window-0-end").fill("21:00")
            page.locator("#persona-count").fill("2")
            page.locator("#persona-template").select_option("persona-004")
            page.locator("#persona-add").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("reuse-source-001")
            page.locator("#agent-count").fill("3")
            page.locator("#days").fill("1")
            page.locator("#seed").fill("7")
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            original = posted[-1]
            page.locator("#start-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            # A finished job states its total wall-clock time from the worker's own timestamps.
            expect(page.locator("#job-settings > div", has_text="Duration")).to_have_count(1)

            page.locator("#reset-draft").click()
            expect(page.locator("#scenario-name")).to_have_value("Synthetic city study")
            page.locator("#stage-inspect").click()
            card = page.locator("#run-library .run-card", has_text="reuse-source-001")
            expect(card).to_be_visible(timeout=15_000)
            expect(card).to_contain_text("Explanations")
            card.get_by_role("button", name="Run again with these inputs").click()

            expect(page.locator("#panel-run")).to_be_visible()
            expect(page.locator("#run-id")).to_have_value("reuse-source-002")
            expect(page.locator("#run-id")).to_be_focused()
            expect(page.locator("#validation-state")).to_contain_text(
                "Inputs restored from run reuse-source-001"
            )
            expect(page.locator("#start-run")).to_be_disabled()
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            restored = posted[-1]
            assert restored["settings"]["run_id"] == "reuse-source-002"
            assert {**restored, "settings": {**restored["settings"], "run_id": "x"}} == {
                **original,
                "settings": {**original["settings"], "run_id": "x"},
            }
        finally:
            browser.close()
            playwright.stop()


def test_projector_text_keeps_a_readable_floor_in_setup_and_the_library(tmp_path: Path) -> None:
    """The labels a presenter reads aloud at 1440 x 900 stay at least 11 or 12 pixels."""
    floors = {
        ".field-help": 11,
        ".section-note": 11,
        ".field label span": 11,
        ".schedule-row label": 11,
        ".validation-state": 12,
        ".library-summary": 12,
        ".run-card dt": 10,
        ".run-card dd": 12,
    }
    with _workbench(tmp_path) as (base_url, _root):
        playwright, browser, page = _open_page(_installed_browser(), base_url)
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base_url, wait_until="networkidle")
            page.locator("#city-continue").click()
            page.locator("#campaign-continue").click()
            page.locator("#run-id").fill("projector-text")
            page.locator("#agent-count").fill("1")
            _set_audience_count(page, 1)
            page.locator("#validate-run").click()
            expect(page.locator("#start-run")).to_be_enabled(timeout=15_000)
            page.locator("#start-run").click()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            page.locator("#stage-inspect").click()
            expect(page.locator("#run-library .run-card")).to_have_count(1, timeout=30_000)
            sizes = page.evaluate(
                """selectors => Object.fromEntries(selectors.map((selector) => [
                  selector,
                  Math.min(...Array.from(document.querySelectorAll(selector)).map(
                    (node) => Number.parseFloat(getComputedStyle(node).fontSize),
                  )),
                ]))""",
                list(floors),
            )
            assert {key: sizes[key] >= floor for key, floor in floors.items()} == {
                key: True for key in floors
            }, sizes
        finally:
            browser.close()
            playwright.stop()
