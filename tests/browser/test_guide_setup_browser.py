"""Browser operation of the guided start, setup and live progress pages (/guide).

Expected wording comes from the packaged catalogs, so these tests follow a deliberate
copy change and never restate translations. Nothing here reaches a real model provider:
origins use the reserved .invalid domain, the provider factory is injected, credentials
live in memory, and system DNS resolution is refused for the whole module.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page, Route, expect, sync_playwright

from adlife.adapters.cognition.city_credentials import (
    ProviderCredentialUnavailable,
    ResolvedCredential,
    SecretBuffer,
)
from adlife.adapters.cognition.city_endpoint_policy import SystemCityAddressResolver
from adlife.city.provider_profiles import ProviderProfileRegistry, ProviderProfileRevisionV1
from adlife.city.provider_runtime import build_provider_runtime_policy
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_jobs import WorkbenchJobView
from adlife.city.workbench_personas import load_persona_template_catalog
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition import CityCognitionRuntimeFallbackReason
from adlife.core.domain.city_cognition_v2 import CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
from tests.browser.test_bounded_cognition_workbench_browser import (
    _HeldSignalProvider,
    _SignalProvider,
)
from tests.browser.test_city_workbench_browser import (
    _LONG_BROWSER_TEST,
    _installed_browser,
    _serve,
    _wait_for_route_handler,
)

CATALOG_DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "adlife" / "i18n" / "catalogs"
CATALOGS: dict[str, dict[str, Any]] = {
    language: json.loads((CATALOG_DIRECTORY / f"{language}.json").read_text(encoding="utf-8"))[
        "messages"
    ]
    for language in ("en", "de", "fa")
}
GAPGPT = build_provider_runtime_policy(provider_origins=("gapgpt=https://api.gapgpt.invalid/v1",))
# A visibly fake key: never a real credential, and no common secret prefix.
TEST_KEY = "guide-browser-test-key-abcdefghij"
JOB_PATH = re.compile(r"/guide/jobs/(job-[0-9a-f]{32})\?lang=(en|de|fa)$")
CHECK_REASONS = (
    *get_args(CityCognitionRuntimeFallbackReason),
    "credential-unavailable",
    "endpoint-policy-refused",
)
UNAVAILABLE_REASONS = frozenset({"credential-unavailable", "endpoint-policy-refused"})


# ----- catalog wording ----------------------------------------------------------------------


def _digits(text: str, language: str) -> str:
    if language != "fa":
        return text
    return "".join(chr(0x06F0 + int(char)) if char.isdigit() else char for char in text)


def _say(key: str, language: str = "en", *, count: int | None = None, **params: str) -> str:
    """One catalog message with plain parameter text, as the page shows it."""
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


def _pattern(key: str, language: str = "en", **params: str) -> re.Pattern[str]:
    """A full-match pattern for one message whose parameters are regular expressions."""
    message = str(CATALOGS[language][key])
    parts = re.split(r"\{([a-z_]+)\}", message)
    pieces = [
        re.escape(part) if index % 2 == 0 else f"(?:{params[part]})"
        for index, part in enumerate(parts)
    ]
    return re.compile("^" + "".join(pieces) + "$")


# ----- fixtures and sessions ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _refuse_system_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """No provider hostname is ever resolved while these pages are driven."""

    async def refuse(_self: object, _hostname: str, _port: int) -> tuple[str, ...]:
        raise OSError("guide browser tests never resolve provider hosts")

    monkeypatch.setattr(SystemCityAddressResolver, "resolve", refuse)


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
        # A refused API answer is part of several scenarios; Chrome logs every one.
        if message.text.startswith("Failed to load resource") and "/api/" in location:
            return
        errors.append(f"console: {message.text}")

    return record


@contextmanager
def _guide(
    tmp_path: Path,
    browser: Browser,
    *,
    width: int = 1280,
    height: int = 720,
    reduced_motion: bool = False,
    **app_options: Any,
) -> Iterator[tuple[Page, str, Path]]:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="guide-browser-token",
        **app_options,
    )
    with _serve(app) as base_url:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            reduced_motion="reduce" if reduced_motion else "no-preference",
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
        page.on("console", _record_console(errors))
        try:
            yield page, base_url, root
            assert errors == []
        finally:
            context.close()


def _fulfill(route: Route, document: object, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(document))


def _refusal(code: str, message: str = "Refused.", fields: dict[str, str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "error": {"code": code, "message": message, "fields": fields or {}},
    }


def _profiles(root: Path) -> list[ProviderProfileRevisionV1]:
    return list(ProviderProfileRegistry(prepare_workbench_workspace(root)).list())


class _MemoryCredentialStore:
    """Keys kept per exact revision in memory, like the OS keyring but test-local."""

    def __init__(self, *, always: bool = False) -> None:
        self.always = always
        self.stored: dict[str, str] = {}

    def available(self, profile: Any) -> bool:
        return self.always or profile.revision_sha256 in self.stored

    def store(self, profile: Any, credential: SecretBuffer) -> None:
        self.stored[profile.revision_sha256] = credential.reveal_text()

    def resolve(self, profile: Any) -> ResolvedCredential:
        value = self.stored.get(profile.revision_sha256, TEST_KEY if self.always else None)
        if value is None:
            raise ProviderCredentialUnavailable("unavailable")
        return ResolvedCredential(
            source="keyring",
            secret=SecretBuffer(value),
            origin_approval_id=profile.origin_approval_id,
            credential_slot_id=profile.credential_slot_id,
        )


def _default_draft(
    *,
    mode: str,
    run_id: str,
    profile_id: str | None = None,
    revision: str | None = None,
    limit: int = 60,
    seed: str = "42",
) -> dict[str, Any]:
    """The exact spec section 2.4 body of the rehearsed default study."""
    templates = load_persona_template_catalog().model_dump(mode="json")["templates"]

    def persona(index: int) -> dict[str, Any]:
        item = copy.deepcopy(templates[index])
        item["count"] = 4
        return item

    return {
        "schema_version": 4,
        "city_id": "fictional-grid-v2",
        "scenario": {
            "scenario_id": "guided-device",
            "name": "Guided demo study - Lumen Pocket",
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": "Lumen Pocket campaign",
                "creative_template_id": "fictional-device-launch-v1",
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "phone": {
                "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1260}],
                "frequency_cap_per_agent_per_day": 4,
                "eligible_activities": ["commute", "home", "leisure"],
                "opportunity_probability_per_minute": 0.01,
            },
            "roadside": None,
        },
        "population": {"personas": [persona(0), persona(1)]},
        "settings": {
            "run_id": run_id,
            "agent_count": 8,
            "days": 1,
            "seed": seed,
            "response_mode": "deterministic-rules",
        },
        "cognition": {
            "effect_scope": "bounded-signals-v1",
            "mode": mode,
            "provider_profile_id": profile_id,
            "provider_revision_sha256": revision,
            "funded_response_limit": limit,
        },
    }


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _scripted_job(
    job_id: str,
    phase: str,
    *,
    resolved: int | None = None,
    total: int = 10,
    started: datetime | None = None,
) -> dict[str, Any]:
    """A remote guided job view, validated by the server model before it is served."""
    began = started or datetime.now(UTC) - timedelta(seconds=12)
    document = {
        "schema_version": 1,
        "job_id": job_id,
        "run_id": "guided-device-1020-1405",
        "accepted_settings": {
            "schema_version": 1,
            "workbench_input_schema_version": 4,
            "run_schema_version": 10,
            "city_id": "fictional-grid-v2",
            "scenario_id": "guided-device",
            "campaign_id": "fictional-launch",
            "seed": "42",
            "agent_count": 8,
            "days": 1,
            "response_mode": "deterministic-rules",
            "effect_scope": "bounded-signals-v1",
            "cognition_mode": "remote",
            "provider_profile_id": "guide-gapgpt",
            "provider_revision_sha256": "a" * 64,
            "funded_response_limit": 10,
            "aggregate_output_token_ceiling": 10 * CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
        },
        "phase": phase,
        "created_at": _stamp(began),
        "updated_at": _stamp(began + timedelta(seconds=1)),
        "cancellation_requested": False,
        "can_cancel": True,
        "error": None,
        "result": None,
        "cognition_progress": None if resolved is None else {"resolved": resolved, "total": total},
    }
    WorkbenchJobView.model_validate(document)
    return document


def _no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, overflow


def _tab_to(page: Page, selector: str, *, limit: int = 120) -> None:
    """Press Tab until the element is focused, as a keyboard-only presenter would."""
    target = page.locator(selector)
    for _ in range(limit):
        page.keyboard.press("Tab")
        if target.evaluate("node => node === document.activeElement"):
            return
    raise AssertionError(f"{selector} is not reachable with the Tab key")


def _focus_ring(page: Page) -> str:
    return str(
        page.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return `${s.outlineStyle} ${s.outlineWidth}`; }"
        )
    )


# ----- 1. the practice path -------------------------------------------------------------------


def test_practice_path_submits_the_exact_default_draft_and_finishes_with_links(
    tmp_path: Path, browser: Browser
) -> None:
    with _guide(tmp_path, browser) as (page, base_url, root):
        posted: list[str] = []
        page.on(
            "request",
            lambda request: (
                posted.append(request.post_data or "")
                if request.method == "POST" and urlparse(request.url).path == "/api/jobs"
                else None
            ),
        )
        page.goto(f"{base_url}/guide/new?lang=en")
        product = page.locator("#ad-products [role='radio']", has_text="Lumen Pocket")
        expect(product).to_have_attribute("aria-checked", "true")
        preset = page.get_by_role("radio", name=_say("setup.people.preset.contrast"))
        expect(preset).to_have_attribute("aria-checked", "true")
        expect(page.locator("#people-total")).to_have_text(_say("setup.people.total", count=8))
        practice = page.locator("#conn-sources [role='radio'][data-value='mock']")
        expect(practice).to_have_attribute("aria-checked", "true")
        expect(page.locator("#setup-connection")).to_contain_text(_say("common.not_an_ai"))
        expect(page.locator("#ai-limit-block")).to_be_hidden()
        expect(page.locator("#ai-offline")).to_have_text(_say("setup.ai.limit.offline"))
        run = page.locator("#setup-run")
        expect(run).to_have_text(_say("setup.run"))
        expect(run).to_have_attribute("aria-disabled", "false")

        run.click()
        page.wait_for_url(JOB_PATH, timeout=15_000)
        assert len(posted) == 1
        body = json.loads(posted[0])
        run_id = body["settings"]["run_id"]
        assert re.fullmatch(r"guided-device-[0-9]{4}-[0-9]{4}", run_id)
        assert body == _default_draft(mode="mock", run_id=run_id)

        expect(page.locator("#job-counter-text")).to_have_text(_say("progress.offline"))
        watch = page.locator("#job-watch")
        expect(watch).to_be_visible(timeout=30_000)
        expect(watch).to_be_focused()
        expect(page.locator("#job-title")).to_have_text(_say("progress.title.done"))
        expect(page.locator("#job-outcome-title")).to_have_text(
            _pattern("progress.done", time=r"[0-9]+ seconds?|[0-9]+ min [0-9]+ s")
        )
        result = page.request.get(f"{base_url}/api/runs?offset=0&limit=100").json()["runs"][0]
        expect(page.locator("#job-outcome-counts")).to_have_text(
            _say("progress.done.practice", count=result["mock_count"])
        )
        steps = page.locator("#job-steps > li")
        assert steps.evaluate_all("items => items.map(item => item.dataset.state)") == ["done"] * 4
        expect(page.locator("#job-steps > li[data-step='done']")).to_have_attribute(
            "aria-current", "step"
        )
        story_href = f"/guide/runs/{run_id}?lang=en"
        report_href = f"/guide/runs/{run_id}/report?lang=en"
        expect(watch).to_have_attribute("href", story_href)
        expect(page.locator("#job-report")).to_have_attribute("href", report_href)
        technical = page.locator("#job-technical")
        expect(technical).to_have_attribute("href", f"/runs/{run_id}")
        expect(technical).to_have_attribute("target", "_blank")
        assert page.request.get(f"{base_url}{story_href}").status == 200
        assert page.request.get(f"{base_url}{report_href}").status == 200
        assert CityRunStore(root).load(run_id).manifest.schema_version == 10

        # The rehearsed run is now the first backup on the start page.
        page.goto(f"{base_url}/guide?lang=en")
        expect(page.locator("#preflight-backups")).to_have_text(
            _say("start.preflight.backups", count=1)
        )
        expect(page.locator("#preflight-backups")).to_have_attribute("data-state", "ok")
        expect(page.locator("#preflight-ai")).to_have_text(_say("start.preflight.ai.offline"))
        card = page.locator("#runs-list > .setup-run").first
        expect(card).to_have_attribute("data-run-id", run_id)
        expect(card.locator(".setup-run__title")).to_have_text("Lumen Pocket")
        expect(card.get_by_role("link", name=_say("start.runs.story"))).to_have_attribute(
            "href", story_href
        )
        expect(card.get_by_role("link", name=_say("start.runs.report"))).to_have_attribute(
            "href", report_href
        )
        expect(card).to_contain_text(_say("mode.mock"))


# ----- 2. the online path without network -----------------------------------------------------


@_LONG_BROWSER_TEST
def test_online_ai_stops_at_the_key_prompt_then_counts_answers_live(
    tmp_path: Path, browser: Browser
) -> None:
    permits = threading.Semaphore(0)
    providers: list[_SignalProvider] = []

    def factory(**kwargs: object) -> _SignalProvider:
        # The compatibility check answers at once; the run's answers then wait for permits.
        provider = (
            _HeldSignalProvider(permits, **kwargs) if providers else _SignalProvider(**kwargs)
        )
        providers.append(provider)
        return provider

    store = _MemoryCredentialStore()
    try:
        with _guide(
            tmp_path,
            browser,
            provider_runtime=GAPGPT,
            credential_store=store,
            provider_factory_v2=factory,
        ) as (page, base_url, root):
            checks: list[str] = []
            page.on(
                "request",
                lambda request: (
                    checks.append(request.post_data or "")
                    if request.method == "POST" and request.url.endswith("/check")
                    else None
                ),
            )
            page.goto(f"{base_url}/guide/new?lang=en")
            online = page.locator("#conn-sources [role='radio'][data-value='remote']")
            expect(online).to_have_attribute("aria-checked", "true")
            expect(page.locator("#setup-connection")).to_contain_text("gapgpt-qwen-3.6")
            expect(page.locator("#conn-status")).to_have_text(_say("connection.untested"))
            run = page.locator("#setup-run")
            expect(run).to_have_text(_say("setup.run_with_test"))
            expect(page.locator("#ai-estimate")).to_have_text(
                _say("setup.ai.limit.estimate", tokens="13,000")
            )

            # "Test the AI and run" first creates the guided profile, then stops: no key yet.
            run.click()
            expect(page.locator("#conn-key-needed")).to_be_visible()
            expect(page.locator("#conn-key-needed")).to_be_focused()
            expect(page.locator("#setup-run-note")).to_have_text(_say("setup.blocked.key"))
            expect(page.locator("#conn-key-needed")).to_contain_text(
                _say(
                    "connection.key.env_hint",
                    script="scripts\\start-guided-demo.ps1",
                    variable="GAPGPT_API_KEY",
                )
            )
            assert checks == []
            assert providers == []
            (profile,) = _profiles(root)
            assert profile.profile_id == "guide-gapgpt"
            assert profile.model_id == "gapgpt-qwen-3.6"
            assert profile.disable_thinking is True
            assert profile.transient_retries == 1
            assert profile.max_output_tokens == 256
            expect(page.locator("#setup-connection")).to_contain_text(
                _say("connection.thinking_off")
            )

            # The key is read once, sent once and cleared from the page.
            page.locator("#conn-key-details > summary").click()
            page.locator("#conn-key").fill(TEST_KEY)
            page.locator("#conn-key-save").click()
            expect(page.locator("#conn-key-status")).to_have_text(_say("connection.key.saved"))
            expect(page.locator("#conn-key")).to_have_value("")
            assert store.stored == {profile.revision_sha256: TEST_KEY}
            attributes = page.evaluate(
                "() => [...document.querySelectorAll('*')]"
                ".flatMap(node => [...node.attributes].map(item => item.value)).join(' ')"
            )
            assert TEST_KEY not in page.content()
            assert TEST_KEY not in attributes
            assert TEST_KEY not in page.url

            run.click()
            page.wait_for_url(JOB_PATH, timeout=20_000)
            assert checks == ['{"effect_scope":"bounded-signals-v1"}']
            counter = page.locator("#job-counter-text")
            expect(counter).to_have_text(
                _say("progress.answered", resolved="0", total="10"), timeout=20_000
            )
            bar = page.locator("#job-bar")
            expect(bar).to_be_visible()
            expect(page.locator("#job-counter-note")).to_have_text(
                _say("progress.parallel", parallel="4")
            )
            for answered in (1, 2):
                permits.release()
                expect(counter).to_have_text(
                    _say("progress.answered", resolved=str(answered), total="10"), timeout=10_000
                )
                assert bar.evaluate("node => [node.value, node.max]") == [answered, 10]
            ask = page.locator("#job-steps > li[data-step='ask']")
            expect(ask).to_have_attribute("data-state", "active")
            expect(ask).to_have_attribute("aria-current", "step")
            expect(page.locator("#job-stop")).to_be_visible()
            permits.release(64)
            watch = page.locator("#job-watch")
            expect(watch).to_be_visible(timeout=30_000)
            expect(counter).to_have_text(_say("progress.answered", resolved="10", total="10"))
            expect(page.locator("#job-outcome-counts")).to_have_text(
                _say("progress.done.counts", answered="10", reused="0", failed="0", over="3")
            )
            expect(page.locator("#job-stop")).to_be_hidden()
            assert sum(provider.calls for provider in providers[1:]) == 10

            # D9: the same study with the same connection gets a fresh seed next time.
            page.goto(f"{base_url}/guide/new?lang=en")
            expect(page.locator("#conn-status")).to_have_text(_say("connection.ok_earlier"))
            expect(run).to_have_text(_say("setup.run"))
            page.locator("#setup-more-summary").click()
            expect(page.locator("#more-seed")).to_have_value("43")
            # A manual seed that repeats the saved run warns that answers would be reused.
            page.locator("#more-seed").fill("42")
            expect(page.locator("#ai-cache")).to_be_visible()
            page.locator("#more-seed-new").click()
            expect(page.locator("#more-seed")).to_have_value("43")
            expect(page.locator("#ai-cache")).to_be_hidden()

            # The start page restores the recorded check with GET requests only.
            page.goto(f"{base_url}/guide?lang=en")
            expect(page.locator("#preflight-ai")).to_have_attribute("data-state", "ok")
            expect(page.locator("#preflight-ai")).to_have_text(_say("start.preflight.ai.ok"))
            expect(page.locator("#conn-status")).to_have_text(_say("connection.ok_earlier"))
            expect(page.locator("#conn-key-needed")).to_be_hidden()
            assert len(checks) == 1
    finally:
        permits.release(64)


# ----- 3. plain failure wording ---------------------------------------------------------------


def test_failed_checks_explain_every_reason_and_rate_and_busy_refusals(
    tmp_path: Path, browser: Browser
) -> None:
    answers: list[tuple[int, dict[str, Any]]] = []
    seen: list[str] = []

    def check(route: Route) -> None:
        seen.append(route.request.post_data or "")
        status, document = answers.pop(0)
        _fulfill(route, document, status)

    with _guide(
        tmp_path,
        browser,
        provider_runtime=GAPGPT,
        credential_store=_MemoryCredentialStore(always=True),
    ) as (page, base_url, _root):
        page.route("**/check", check)
        test = page.locator("#conn-test")
        status = page.locator("#conn-status")
        hint = page.locator("#conn-hint")
        for reason in CHECK_REASONS:
            state = "unavailable" if reason in UNAVAILABLE_REASONS else "incompatible"
            answers.append((200, {"status": state, "reason_code": reason}))
            page.goto(f"{base_url}/guide?lang=en")
            expect(status).to_have_text(_say("connection.untested"))
            test.click()
            expect(status).to_have_text(
                _say("connection.failed", reason=_say(f"reason.{reason}")), timeout=10_000
            )
            expect(hint).to_have_text(_say(f"hint.{reason}"))
            expect(page.locator("#preflight-ai")).to_have_attribute("data-state", "warn")
        # The button rests for a moment after each test, then accepts the next one.
        expect(test).to_be_disabled()
        expect(test).to_be_enabled(timeout=6_000)

        for code_status, document, key in (
            (429, _refusal("provider-check-rate-limited"), "connection.rate_limited"),
            (409, _refusal("provider-operation-busy"), "connection.busy"),
            (502, _refusal("provider-check-failed"), "connection.check_failed"),
        ):
            answers.append((code_status, document))
            page.goto(f"{base_url}/guide?lang=en")
            expect(status).to_have_text(_say("connection.untested"))
            test.click()
            expect(status).to_have_text(_say(key), timeout=10_000)
            expect(hint).to_be_hidden()
        assert len(seen) == len(CHECK_REASONS) + 3
        assert set(seen) == {'{"effect_scope":"bounded-signals-v1"}'}

        # A local model adds the Ollama hint to its failure.
        answers.append((200, {"status": "incompatible", "reason_code": "timeout-exhausted"}))
        page.goto(f"{base_url}/guide?lang=en")
        page.locator("#conn-sources [role='radio'][data-value='local']").click()
        expect(page.locator("#conn-model")).to_have_value("qwen2.5:7b-instruct")
        expect(test).to_be_enabled()
        test.click()
        expect(status).to_have_text(
            _say("connection.failed", reason=_say("reason.timeout-exhausted")), timeout=10_000
        )
        expect(page.locator("#conn-state")).to_contain_text(_say("connection.local_hint"))


# ----- 4. a taken run name ----------------------------------------------------------------------


def test_a_taken_run_name_is_renamed_and_submitted_again(tmp_path: Path, browser: Browser) -> None:
    bodies: list[dict[str, Any]] = []

    def jobs(route: Route) -> None:
        bodies.append(json.loads(route.request.post_data or "{}"))
        if len(bodies) == 1:
            _fulfill(
                route,
                _refusal(
                    "run-conflict",
                    "The run identifier is already in use.",
                    {"settings.run_id": "A saved run already uses this ID. Choose a new run ID."},
                ),
                409,
            )
        else:
            route.continue_()

    with _guide(tmp_path, browser) as (page, base_url, _root):
        page.route("**/api/jobs", jobs)
        page.goto(f"{base_url}/guide/new?lang=en")
        expect(page.locator("#setup-run")).to_have_text(_say("setup.run"))
        page.locator("#setup-run").click()
        expect(page.locator("#setup-run-note")).to_have_text(
            _pattern("setup.error.renamed", run_id=r"guided-device-[0-9]{4}-[0-9]{4}-2")
        )
        page.wait_for_url(JOB_PATH, timeout=15_000)
        _wait_for_route_handler(page, lambda: len(bodies) >= 2, what="the renamed submission")
        first, second = bodies
        assert second["settings"]["run_id"] == f"{first['settings']['run_id']}-2"
        assert {**second, "settings": {**second["settings"], "run_id": None}} == {
            **first,
            "settings": {**first["settings"], "run_id": None},
        }
        expect(page.locator("#job-run")).to_have_text(
            _say("progress.run", run_id=second["settings"]["run_id"])
        )
        expect(page.locator("#job-watch")).to_be_visible(timeout=30_000)


# ----- 5. another simulation is running -------------------------------------------------------


def test_an_active_job_shows_a_banner_and_names_the_run_blocker(
    tmp_path: Path, browser: Browser
) -> None:
    job_id = "job-" + "c" * 32
    active = _scripted_job(job_id, "requesting-cognition", resolved=3)

    def workbench(route: Route) -> None:
        response = route.fetch()
        document = response.json()
        document["active_job"] = active
        route.fulfill(response=response, json=document)

    with _guide(tmp_path, browser) as (page, base_url, _root):
        page.route("**/api/workbench", workbench)
        for path in ("/guide", "/guide/new"):
            page.goto(f"{base_url}{path}?lang=en")
            banner = page.locator("#active-job")
            expect(banner).to_be_visible()
            expect(banner).to_contain_text(_say("start.active_job"))
            expect(
                banner.get_by_role("link", name=_say("start.active_job.open"))
            ).to_have_attribute("href", f"/guide/jobs/{job_id}?lang=en")
        run = page.locator("#setup-run")
        expect(run).to_have_attribute("aria-disabled", "true")
        # Blocked, not disabled: the button keeps its tab stop and explains itself on click.
        # Playwright treats aria-disabled as disabled, so the click skips that wait.
        assert run.evaluate("node => node.disabled === false && node.tabIndex === 0")
        run.click(force=True)
        blockers = page.locator("#setup-blockers")
        expect(blockers).to_be_visible()
        expect(blockers).to_be_focused()
        expect(blockers).to_contain_text(_say("setup.blocked.title"))
        expect(blockers).to_contain_text(_say("setup.blocked.busy"))
        expect(
            blockers.get_by_role("link", name=_say("setup.blocked.busy.open"))
        ).to_have_attribute("href", f"/guide/jobs/{job_id}?lang=en")
        assert page.url.endswith("/guide/new?lang=en")

        # With nobody in the audience the first blocker names the people and fixes focus.
        page.unroute("**/api/workbench")
        page.goto(f"{base_url}/guide/new?lang=en")
        expect(page.locator("#people-list > li")).to_have_count(4)
        expect(page.locator("#people-total")).to_have_text(_say("setup.people.total", count=8))
        for row in page.locator("#people-list > li").all():
            down = row.locator("[data-step='down']")
            while down.is_enabled():
                down.click()
        expect(page.locator("#people-total")).to_have_text(_say("setup.people.total", count=0))
        expect(page.get_by_role("radio", name=_say("setup.people.preset.custom"))).to_be_visible()
        expect(run).to_have_attribute("aria-disabled", "true")
        run.click(force=True)
        fix = blockers.get_by_role("button", name=_say("setup.blocked.people"))
        expect(fix).to_be_visible()
        fix.click()
        expect(page.locator("#people-list [data-step='up']").first).to_be_focused()


# ----- 6. Persian, right to left, at phone width ---------------------------------------------


def test_persian_pages_read_right_to_left_at_phone_width_with_persian_digits(
    tmp_path: Path, browser: Browser
) -> None:
    job_id = "job-" + "d" * 32
    script = [
        _scripted_job(job_id, "planning-cognition"),
        _scripted_job(job_id, "requesting-cognition", resolved=3),
    ]

    with _guide(tmp_path, browser, width=390, height=844) as (page, base_url, _root):
        page.route(
            f"**/api/jobs/{job_id}",
            lambda route: _fulfill(route, script.pop(0) if len(script) > 1 else script[0]),
        )
        for path in ("/guide", "/guide/new"):
            page.goto(f"{base_url}{path}?lang=fa")
            expect(page.locator("html")).to_have_attribute("dir", "rtl")
            expect(page.locator("html")).to_have_attribute("lang", "fa")
            assert page.evaluate("() => getComputedStyle(document.body).direction") == "rtl"
            expect(page.locator("#setup-connection, #start-connection")).to_contain_text(
                _say("connection.source.mock", "fa")
            )
            _no_horizontal_overflow(page)
        expect(page.locator("#people-total")).to_have_text(
            _say("setup.people.total", "fa", count=8)
        )
        # Below 600 px the Run bar scrolls with the page instead of covering it.
        assert page.locator("#setup-runbar").evaluate(
            "node => getComputedStyle(node).position"
        ) == ("static")
        page.locator("#setup-more-summary").click()
        expect(page.locator("#more-from")).to_have_text(_digits("07:00", "fa"))
        expect(page.locator("#more-until")).to_have_text(_digits("21:00", "fa"))
        _no_horizontal_overflow(page)

        page.goto(f"{base_url}/guide/jobs/{job_id}?lang=fa")
        counter = page.locator("#job-counter-text")
        expect(counter).to_have_text(
            _say("progress.answered", "fa", resolved=_digits("3", "fa"), total=_digits("10", "fa")),
            timeout=10_000,
        )
        expect(page.locator("#job-title")).to_have_text(_say("progress.title", "fa"))
        persian_digit = re.compile(f"[{chr(0x06F0)}-{chr(0x06F9)}]")
        expect(page.locator("#job-elapsed")).to_contain_text(persian_digit)
        expect(page.locator("#job-steps > li[data-step='simulate']")).to_have_attribute(
            "data-state", "done"
        )
        expect(page.locator("#job-steps > li[data-step='ask']")).to_have_attribute(
            "aria-current", "step"
        )
        _no_horizontal_overflow(page)


# ----- 7. keyboard only, with reduced motion --------------------------------------------------


def test_keyboard_only_presenter_runs_the_practice_study_with_reduced_motion(
    tmp_path: Path, browser: Browser
) -> None:
    with _guide(tmp_path, browser, reduced_motion=True) as (page, base_url, _root):
        page.goto(f"{base_url}/guide?lang=en")
        expect(page.locator("#preflight-motion")).to_be_visible()
        expect(page.locator("#preflight-motion")).to_have_text(_say("start.preflight.motion_off"))
        expect(page.locator("#preflight-ai")).to_have_text(_say("start.preflight.ai.offline"))
        _tab_to(page, "#start-new")
        assert not _focus_ring(page).startswith("none")
        page.keyboard.press("Enter")
        page.wait_for_url(f"{base_url}/guide/new?lang=en")

        products = page.locator("#ad-products [role='radio']")
        expect(products.first).to_be_visible()
        _tab_to(page, "#ad-products [role='radio'][aria-checked='true']")
        page.keyboard.press("ArrowRight")
        chosen = page.locator("#ad-products [role='radio'][aria-checked='true']")
        expect(chosen).to_have_count(1)
        expect(chosen).to_be_focused()
        expect(chosen).not_to_have_attribute("data-value", "fictional-device-launch-v1")
        page.keyboard.press("ArrowLeft")
        expect(chosen).to_have_attribute("data-value", "fictional-device-launch-v1")
        expect(chosen).to_be_focused()

        _tab_to(page, "#setup-run")
        assert not _focus_ring(page).startswith("none")
        assert page.locator("#setup-run").evaluate(
            "node => getComputedStyle(node).transitionDuration.split(',')"
            ".every(item => parseFloat(item) === 0)"
        )
        page.keyboard.press("Enter")
        page.wait_for_url(JOB_PATH, timeout=15_000)
        expect(page.locator("#job-watch")).to_be_focused(timeout=30_000)
