"""Browser operation of the schema-4 bounded-cognition workbench controls."""

from __future__ import annotations

import copy
import inspect
import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from playwright.sync_api import Page, Route, expect

import adlife.city.workbench_web as workbench_web
from adlife.city.run_store import CityRunStore
from adlife.city.workbench_input import (
    NormalizedWorkbenchCognitionV4,
    WorkbenchCognitionDraftV4,
    WorkbenchRunDraftV4,
)
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_cognition_v2 import CityCognitionResultV2, CityProviderAttemptV2
from adlife.core.domain.city_run import CityRunManifestV10
from tests.browser.test_city_workbench_browser import (
    _installed_browser,
    _library_summary,
    _open_page,
    _serve,
    _set_audience_count,
)

BOUNDED = "bounded-signals-v1"
EXPLANATION = "explanation-only"
LOCAL_REVISION = "c" * 64
LOCAL_ALT_REVISION = "d" * 64
REMOTE_REVISION = "a" * 64
PROVIDER_MODES = ("local", "remote")


def _single(model: Any, field: str) -> Any:
    (value,) = get_args(model.model_fields[field].annotation)
    return value


def runtime_bounded_capability() -> dict[str, Any]:
    """The bounded-cognition options exactly as today's runtime models define them.

    Token limits are read from the frozen model literals, never restated here, so these
    tests follow a deliberate runtime limit change without edits.
    """

    def materialized(mode: str) -> int:
        provider = mode in PROVIDER_MODES
        return WorkbenchCognitionDraftV4.model_validate(
            {
                "effect_scope": BOUNDED,
                "mode": mode,
                "provider_profile_id": "provider-main" if provider else None,
                "provider_revision_sha256": REMOTE_REVISION if provider else None,
            }
        ).funded_response_limit

    frozen = NormalizedWorkbenchCognitionV4
    modes = list(get_args(WorkbenchCognitionDraftV4.model_fields["mode"].annotation))
    return {
        "effect_scope": _single(WorkbenchCognitionDraftV4, "effect_scope"),
        "workbench_schema_version": _single(WorkbenchRunDraftV4, "schema_version"),
        "run_schema_version": _single(CityRunManifestV10, "schema_version"),
        "modes": modes,
        "funded_response_limits": list(
            get_args(WorkbenchCognitionDraftV4.model_fields["funded_response_limit"].annotation)
        ),
        "default_provider_funded_response_limit": materialized("local"),
        "offline_funded_response_limit": materialized("rules"),
        "selected_requests_per_run": _single(frozen, "selected_requests_per_run"),
        "reserved_output_tokens_per_request": _single(frozen, "reservation_tokens_per_request"),
        "max_output_tokens_per_attempt": _single(frozen, "max_output_tokens_per_attempt"),
        "max_http_attempts_per_request": sum(
            _single(frozen, name)
            for name in ("initial_attempts", "transient_retries", "repair_attempts")
        ),
        "compatibility_check_required_modes": [mode for mode in modes if mode in PROVIDER_MODES],
    }


C1: dict[str, Any] = {
    "cognition_effect_scopes": [EXPLANATION, BOUNDED],
    "bounded_cognition": runtime_bounded_capability(),
}
# Every session asserts the real server declares exactly these runtime-derived values.
LIMITS: dict[str, Any] = C1["bounded_cognition"]
RESERVED = LIMITS["reserved_output_tokens_per_request"]
SELECTED = LIMITS["selected_requests_per_run"]
OFFLINE = LIMITS["offline_funded_response_limit"]
# V9 reserves output per explanation in whole 1,536-token steps; never a V10 ceiling.
V9_TOKENS_PER_EXPLANATION = 1536
# The explanation form funds 10 explanations by default so a live local model answers in
# under a minute; the operator may raise it to the full V9 ceiling.
V9_DEFAULT_RESERVATION = 10 * V9_TOKENS_PER_EXPLANATION
BOUNDARY = (
    "External AI selects a small set of synthetic appraisal categories. AdLife applies a "
    "fixed mapping. These categories may change only the displayed cognition state. They "
    "cannot move agents, create or select events, change purchase intention, or make "
    "purchases."
)
HOLD_CHECKS = """
  const originalFetch = window.fetch.bind(window);
  window.__heldChecks = [];
  window.__holdChecks = false;
  window.fetch = (input, init) => {
    if (window.__holdChecks && String(input).endsWith('/check')) {
      return new Promise((resolve, reject) => {
        window.__heldChecks.push(() => originalFetch(input, init).then(resolve, reject));
      });
    }
    return originalFetch(input, init);
  };
  window.__releaseChecks = () => window.__heldChecks.splice(0).forEach((release) => release());
"""


def _profile(
    profile_id: str,
    mode: str,
    revision: str,
    *,
    label: str = "Lab model",
    model: str = "qwen2.5:7b-instruct",
    max_output_tokens: int = 384,
) -> dict[str, object]:
    local = mode == "local"
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "display_label": label,
        "mode": mode,
        "base_url": "http://127.0.0.1:11434/v1" if local else "https://api.example.invalid/v1",
        "model_id": model,
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": max_output_tokens,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "local-ollama" if local else "remote-demo",
        "revision_sha256": revision,
        "credential_slot_id": "b" * 64,
        "credential_available": True,
        "endpoint_policy_accepted": True,
        "last_check": None,
    }


def _provider_document(*profiles: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "profiles": list(profiles)
        or [
            _profile("local-lab", "local", LOCAL_REVISION),
            _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"),
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


def _v2_check(profile_id: str, revision: str, status: str = "compatible") -> dict[str, object]:
    reason = None if status == "compatible" else "invalid-result"
    return {
        "status": status,
        "reason_code": reason,
        "effect_scope": BOUNDED,
        "profile_id": profile_id,
        "revision_sha256": revision,
        **{
            name: digit * 64
            for name, digit in (
                ("compatibility_key_sha256", "1"),
                ("causal_contract_sha256", "2"),
                ("prompt_contract_sha256", "3"),
                ("result_schema_contract_sha256", "4"),
                ("selection_policy_sha256", "5"),
                ("fallback_policy_sha256", "6"),
                ("mock_policy_sha256", "7"),
                ("influence_policy_sha256", "8"),
            )
        },
    }


def _fulfill(route: Route, document: object, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(document))


def _providers(page: Page, document: dict[str, object]) -> None:
    page.route("**/api/providers", lambda route: _fulfill(route, document))


def _to_run_step(page: Page, base_url: str) -> None:
    page.goto(base_url, wait_until="networkidle")
    page.locator("#city-continue").click()
    page.locator("#campaign-continue").click()
    expect(
        page.locator("#cognition-effect-scope option[value='bounded-signals-v1']")
    ).to_be_enabled()


def _choose(page: Page, scope: str, mode: str) -> None:
    page.locator("#cognition-effect-scope").select_option(scope)
    page.locator("#cognition-mode").select_option(mode)


def _validate(page: Page) -> dict[str, Any]:
    with (
        page.expect_request(
            lambda request: (
                request.method == "POST" and request.url.endswith("/api/scenarios/validate")
            )
        ) as captured,
        page.expect_response("**/api/scenarios/validate") as answered,
    ):
        page.locator("#validate-run").click()
    assert answered.value.status == 200, answered.value.text()
    expect(page.locator("#validation-review")).to_be_visible(timeout=15_000)
    return json.loads(captured.value.post_data or "{}")


def _offered(page: Page, base_url: str) -> dict[str, Any]:
    """The bounded capability the running server declares; expected numbers come from here."""
    document = page.request.get(f"{base_url}/api/workbench").json()
    return document["capabilities"]["bounded_cognition"]


def _generation(page: Page) -> int:
    return int(
        page.locator("#explanations-section").get_attribute("data-authority-generation") or "-1"
    )


def _ownership(page: Page) -> list[list[str]]:
    return page.locator("#review-ownership > div").evaluate_all(
        "rows => rows.map(row => [...row.children].map(cell => cell.textContent))"
    )


@contextmanager
def _session(
    tmp_path: Path, *, width: int = 1440, **app_options: Any
) -> Iterator[tuple[Page, str, Path]]:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="bounded-browser-token",
        **app_options,
    )
    browser_path = _installed_browser()
    with _serve(app) as base_url:
        playwright, browser, page = _open_page(browser_path, base_url, width=width)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            # The browser consumes the real server capability document, never a stand-in.
            offered = page.request.get(f"{base_url}/api/workbench").json()["capabilities"]
            assert {name: offered.get(name) for name in C1} == C1
            yield page, base_url, root
            assert errors == []
        finally:
            browser.close()
            playwright.stop()


def test_operation_and_source_controls_select_exact_schema(tmp_path: Path) -> None:
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        _to_run_step(page, base_url)
        limits = _offered(page, base_url)
        offline = str(limits["offline_funded_response_limit"])
        default = limits["default_provider_funded_response_limit"]
        chosen = next(value for value in limits["funded_response_limits"] if value != default)

        scope = page.locator("#cognition-effect-scope")
        source = page.locator("#cognition-mode")
        assert scope.locator("option").evaluate_all("nodes => nodes.map(node => node.value)") == [
            EXPLANATION,
            BOUNDED,
        ]
        expect(scope).to_have_value(EXPLANATION)
        expect(source).to_have_value("rules")
        expect(page.locator("#bounded-settings")).to_be_hidden()

        # Operation and source are independent: neither choice rewrites the other.
        source.select_option("mock")
        scope.select_option(BOUNDED)
        expect(source).to_have_value("mock")
        source.select_option("rules")
        expect(scope).to_have_value(BOUNDED)

        funded = page.locator("#cognition-funded-responses")
        for offline_source in ("rules", "mock"):
            source.select_option(offline_source)
            expect(funded).to_be_disabled()
            expect(funded).to_have_value(offline)
            expect(page.locator("#cognition-funded-status")).to_contain_text(
                "No provider tokens are reserved"
            )
            expect(page.locator("#bounded-cost-disclosure")).to_be_hidden()
            expect(page.locator("#provider-settings")).to_be_hidden()
            expect(page.locator("#provider-check-panel")).to_be_hidden()
        for provider in PROVIDER_MODES:
            source.select_option(provider)
            expect(funded).to_be_enabled()
            expect(funded).to_have_value(str(default))
            assert funded.locator("option").evaluate_all("nodes => nodes.map(n => n.value)") == [
                str(value) for value in limits["funded_response_limits"]
            ]
            expect(page.locator("#explanation-budget")).to_be_hidden()
            expect(page.locator("#provider-check-panel")).to_be_visible()

        expected = {
            (EXPLANATION, "rules"): (3, {"mode": "rules", "aggregate_output_token_ceiling": 0}),
            (EXPLANATION, "mock"): (3, {"mode": "mock", "aggregate_output_token_ceiling": 0}),
            (
                EXPLANATION,
                "local",
            ): (3, {"mode": "local", "aggregate_output_token_ceiling": V9_DEFAULT_RESERVATION}),
            (EXPLANATION, "remote"): (
                3,
                {"mode": "remote", "aggregate_output_token_ceiling": V9_DEFAULT_RESERVATION},
            ),
            (BOUNDED, "rules"): (4, {"mode": "rules", "funded_response_limit": int(offline)}),
            (BOUNDED, "mock"): (4, {"mode": "mock", "funded_response_limit": int(offline)}),
            (BOUNDED, "local"): (4, {"mode": "local", "funded_response_limit": default}),
            (BOUNDED, "remote"): (4, {"mode": "remote", "funded_response_limit": chosen}),
        }
        profiles = {
            "local": ("local-lab", LOCAL_REVISION),
            "remote": ("remote-demo", REMOTE_REVISION),
        }
        for (effect_scope, mode), (schema_version, facts) in expected.items():
            _choose(page, effect_scope, mode)
            if (effect_scope, mode) == (BOUNDED, "remote"):
                funded.select_option(str(chosen))
            draft = _validate(page)
            profile_id, revision = profiles.get(mode, (None, None))
            cognition = {
                "mode": mode,
                "provider_profile_id": profile_id,
                "provider_revision_sha256": revision,
                **facts,
            }
            if schema_version == 4:
                cognition = {"effect_scope": BOUNDED, **cognition}
            assert draft["schema_version"] == schema_version, (effect_scope, mode)
            assert draft["cognition"] == cognition, (effect_scope, mode)
            assert sorted(draft) == [
                "city_id",
                "cognition",
                "population",
                "scenario",
                "schema_version",
                "settings",
            ]
            expect(page.locator("#review-schema")).to_have_text(
                f"Workbench schema {schema_version} → run V{schema_version + 6}"
            )


def test_scoped_compatibility_checks_bind_scope_profile_and_generation(tmp_path: Path) -> None:
    checks: list[tuple[str, str]] = []
    behaviour = {"v2_shape": True}
    document = _provider_document(
        _profile("local-lab", "local", LOCAL_REVISION),
        _profile("local-alt", "local", LOCAL_ALT_REVISION, label="Alternate lab"),
        _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"),
    )

    def check(route: Route) -> None:
        body = route.request.post_data or ""
        checks.append((route.request.url, body))
        _, profile_id, _, revision, _ = route.request.url.rsplit("/", 4)
        if json.loads(body) == {"effect_scope": BOUNDED} and behaviour["v2_shape"]:
            _fulfill(route, _v2_check(profile_id, revision))
        else:
            _fulfill(route, {"status": "compatible", "reason_code": None})

    def credential(route: Route) -> None:
        _fulfill(route, _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"))

    with _session(tmp_path) as (page, base_url, _root):
        page.add_init_script(HOLD_CHECKS)
        _providers(page, document)
        page.route("**/api/providers/*/revisions/*/check", check)
        page.route("**/api/providers/*/revisions/*/credential", credential)
        _to_run_step(page, base_url)
        start = page.locator("#start-run")
        status = page.locator("#provider-check-status")

        generation = _generation(page)
        _choose(page, BOUNDED, "local")
        assert _generation(page) == generation + 2
        _validate(page)
        expect(start).to_be_disabled()
        expect(page.locator("#start-run-requirement")).to_contain_text(
            "Check bounded-signal compatibility"
        )
        expect(page.locator("#review-compatibility")).to_have_text("Required before start")
        expect(page.locator("#review-readiness")).to_have_text("Check required")

        # A V1 explanation success is presented as V1 and never authorizes V2.
        page.locator("#cognition-effect-scope").select_option(EXPLANATION)
        expect(page.locator("#provider-check-scope")).to_have_text("Explanation contract")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Compatible inference confirmed.")
        page.locator("#cognition-effect-scope").select_option(BOUNDED)
        expect(status).to_have_text("")
        _validate(page)
        expect(start).to_be_disabled()

        # A V1-shaped success answering a V2 request is refused, not trusted.
        behaviour["v2_shape"] = False
        page.locator("#provider-check").click()
        expect(status).to_contain_text("incompatible")
        expect(start).to_be_disabled()

        behaviour["v2_shape"] = True
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        expect(start).to_be_enabled()
        expect(page.locator("#review-compatibility")).to_have_text(
            "Bounded-signal check passed for this revision"
        )

        # Funding is not authority: it re-requires validation but keeps the scoped check.
        generation = _generation(page)
        page.locator("#cognition-funded-responses").select_option("40")
        assert _generation(page) == generation
        _validate(page)
        expect(start).to_be_enabled()

        # Profile changes invalidate the result, and returning cannot reuse it.
        expect(page.locator("#provider-profile")).to_have_value(f"local-alt/{LOCAL_ALT_REVISION}")
        for revision in (f"local-lab/{LOCAL_REVISION}", f"local-alt/{LOCAL_ALT_REVISION}"):
            generation = _generation(page)
            page.locator("#provider-profile").select_option(revision)
            assert _generation(page) == generation + 1
            expect(status).to_have_text("")
            _validate(page)
            expect(start).to_be_disabled()

        # A delayed check that resolves after a newer authority edit is ignored.
        page.evaluate("window.__holdChecks = true")
        page.locator("#provider-check").click()
        expect(status).to_have_text(
            "Checking compatibility… a local model that is still loading may take a minute or more."
        )
        page.locator("#cognition-mode").select_option("mock")
        page.locator("#cognition-mode").select_option("local")
        page.evaluate("window.__holdChecks = false; window.__releaseChecks()")
        expect(page.locator("#provider-check")).to_be_enabled()
        page.wait_for_timeout(200)
        expect(status).to_have_text("")
        _validate(page)
        expect(start).to_be_disabled()

        # Credential replacement is an authority edit that clears the V2 result.
        page.locator("#cognition-mode").select_option("remote")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        generation = _generation(page)
        page.locator("#provider-credential").fill("replacement-" + "credential-value")
        page.locator("#provider-save-credential").click()
        expect(page.locator("#provider-credential-status")).to_contain_text("Credential saved")
        assert _generation(page) == generation + 1
        expect(status).to_have_text("")
        _validate(page)
        expect(start).to_be_disabled()

        bodies = [(url.rsplit("/", 4)[1], body) for url, body in checks]
        assert bodies[0] == ("local-alt", "{}")
        assert all(body in {"{}", '{"effect_scope":"bounded-signals-v1"}'} for _, body in bodies)
        assert [body for _, body in bodies[1:]] == ['{"effect_scope":"bounded-signals-v1"}'] * (
            len(bodies) - 1
        )


@pytest.mark.parametrize(
    ("scope", "mode", "rows"),
    [
        (
            BOUNDED,
            "local",
            [
                ["Cognition signals", "Local model, with deterministic neutral fallback"],
                ["Reproducibility", "Live signals are not guaranteed reproducible"],
            ],
        ),
        (
            BOUNDED,
            "mock",
            [
                ["Cognition signals", "Mock: deterministic signals, offline"],
                ["Reproducibility", "Identical on rerun"],
            ],
        ),
        (EXPLANATION, "rules", [["Explanations", "Rules text only; cannot change results"]]),
    ],
)
def test_review_names_owners_and_worker_receipts_must_restate_the_draft(
    tmp_path: Path, scope: str, mode: str, rows: list[list[str]]
) -> None:
    job_id = "job-" + "f" * 32
    responses: list[dict[str, object]] = []
    submissions: list[dict[str, object]] = []

    def jobs(route: Route) -> None:
        submissions.append(json.loads(route.request.post_data or "{}"))
        job = responses.pop(0)
        _fulfill(route, {"schema_version": 1, "job": job, "status_url": f"/api/jobs/{job_id}"}, 202)

    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        page.route(
            "**/api/providers/*/revisions/*/check",
            lambda route: _fulfill(route, _v2_check("local-lab", LOCAL_REVISION)),
        )
        page.route("**/api/jobs", jobs)
        page.route(f"**/api/jobs/{job_id}", lambda route: _fulfill(route, responses[-1]))
        _to_run_step(page, base_url)
        _choose(page, scope, mode)
        if mode == "local":
            page.locator("#provider-check").click()
            expect(page.locator("#provider-check-status")).to_have_text(
                "Bounded-signal contract confirmed."
            )
        draft = _validate(page)
        limits = _offered(page, base_url)
        reserved = limits["reserved_output_tokens_per_request"]
        bounded = scope == BOUNDED
        operation = "Bounded AI cognition" if bounded else "Explanation only"
        expect(page.locator("#review-operation")).to_have_text(operation)
        assert _ownership(page) == [
            ["Movement, exposure, attention", "Rules"],
            ["Purchase intention", "Rules: an uncalibrated proxy score; purchases are not modeled"],
            *rows,
            ["Saved replay", "Frozen and offline"],
        ]
        expect(page.locator("#start-run")).to_have_text(
            "Start bounded cognition run" if bounded else "Start deterministic run"
        )
        if mode == "local":
            funded = limits["default_provider_funded_response_limit"]
            expect(page.locator("#review-funding")).to_have_text(
                f"{funded} funded of up to {limits['selected_requests_per_run']} selected · "
                f"{funded * reserved:,} output tokens reserved"
            )
            expect(page.locator("#review-source")).to_contain_text("qwen2.5:7b-instruct")

        cognition = draft["cognition"]
        accepted: dict[str, object] = {
            "schema_version": 1,
            "workbench_input_schema_version": draft["schema_version"],
            "run_schema_version": 10 if bounded else 9,
            "city_id": draft["city_id"],
            "scenario_id": draft["scenario"]["scenario_id"],
            "campaign_id": draft["scenario"]["campaign"]["campaign_id"],
            "seed": draft["settings"]["seed"],
            "agent_count": draft["settings"]["agent_count"],
            "days": draft["settings"]["days"],
            "response_mode": "deterministic-rules",
            "cognition_mode": cognition["mode"],
            "provider_profile_id": cognition["provider_profile_id"],
            "provider_revision_sha256": cognition["provider_revision_sha256"],
        }
        if bounded:
            limit = cognition["funded_response_limit"]
            other = next(value for value in limits["funded_response_limits"] if value != limit)
            accepted.update(
                effect_scope=BOUNDED,
                funded_response_limit=limit,
                aggregate_output_token_ceiling=limit * reserved if mode == "local" else 0,
            )
        else:
            accepted["aggregate_output_token_ceiling"] = cognition["aggregate_output_token_ceiling"]
        drifted: list[Callable[[dict[str, object]], None]] = (
            [
                lambda value: value.update(workbench_input_schema_version=3, run_schema_version=9),
                lambda value: value.update(funded_response_limit=other),
                lambda value: value.update(
                    aggregate_output_token_ceiling=limit * V9_TOKENS_PER_EXPLANATION
                ),
                lambda value: value.pop("effect_scope"),
            ]
            if bounded
            else [
                lambda value: value.update(effect_scope=BOUNDED),
                lambda value: value.update(workbench_input_schema_version=4, run_schema_version=10),
            ]
        )
        queued = {
            "schema_version": 1,
            "job_id": job_id,
            "run_id": draft["settings"]["run_id"],
            "phase": "queued",
            "created_at": "2026-10-19T00:00:00.000000Z",
            "updated_at": "2026-10-19T00:00:00.000000Z",
            "cancellation_requested": False,
            "can_cancel": True,
            "error": None,
            "result": None,
        }
        for drift in drifted:
            receipt = copy.deepcopy(accepted)
            drift(receipt)
            responses.append({**queued, "accepted_settings": receipt})
            page.locator("#start-run").click()
            expect(page.locator("#error-banner")).to_contain_text("incompatible")
            expect(page.locator("#job-panel")).to_be_hidden()
            expect(page.locator("#start-run")).to_be_enabled()

        responses.append({**queued, "accepted_settings": accepted})
        page.locator("#start-run").click()
        expect(page.locator("#job-panel")).to_be_visible()
        expect(page.locator("#error-banner")).to_be_hidden()
        expect(page.locator("#start-run")).to_be_disabled()
        settings_text = page.locator("#job-settings").inner_text()
        assert ("Bounded AI cognition" in settings_text) is bounded
        assert ("Explanations" in settings_text) is not bounded
        assert all(item == draft for item in submissions)
        assert len(submissions) == len(drifted) + 1


def test_server_compatibility_refusal_requires_a_fresh_bounded_check(tmp_path: Path) -> None:
    refusal = {
        "error": {
            "code": "provider-compatibility-required",
            "message": "A successful bounded-cognition provider compatibility check is required.",
            "fields": {},
        }
    }
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        page.route(
            "**/api/providers/*/revisions/*/check",
            lambda route: _fulfill(route, _v2_check("local-lab", LOCAL_REVISION)),
        )
        page.route("**/api/jobs", lambda route: _fulfill(route, refusal, 409))
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        status = page.locator("#provider-check-status")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        _validate(page)
        page.locator("#start-run").click()

        expect(page.locator("#error-banner")).to_contain_text("compatibility check is required")
        expect(status).to_have_text(
            "Check again: the server needs a current bounded-signal result."
        )
        expect(page.locator("#start-run")).to_be_disabled()
        expect(page.locator("#start-run-requirement")).to_be_visible()
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        expect(page.locator("#start-run")).to_be_enabled()


def test_bounded_cost_disclosure_reserves_whole_requests_without_spending_claims(
    tmp_path: Path,
) -> None:
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        _to_run_step(page, base_url)
        limits = _offered(page, base_url)
        reserved = limits["reserved_output_tokens_per_request"]
        per_attempt = limits["max_output_tokens_per_attempt"]
        attempts = limits["max_http_attempts_per_request"]
        selected = limits["selected_requests_per_run"]
        default = limits["default_provider_funded_response_limit"]
        largest = limits["funded_response_limits"][-1]
        page.locator("#cognition-mode").select_option("local")
        expect(page.locator("#provider-output-tokens")).to_have_text("384 per attempt")
        expect(page.locator("#explanation-budget")).to_be_visible()
        expect(page.locator("#bounded-cost-disclosure")).to_be_hidden()

        page.locator("#cognition-effect-scope").select_option(BOUNDED)
        disclosure = page.locator("#bounded-cost-disclosure")
        expect(disclosure).to_have_text(
            f"Each funded request reserves {reserved:,} output tokens: at most {attempts} HTTP "
            f"attempts of up to {per_attempt:,} output tokens each. {default} requests reserve "
            f"{default * reserved:,} output tokens. Input tokens and vendor charges are not "
            "included; this is a reservation, not a spending limit."
        )
        # The saved profile is shown unchanged beside the per-attempt signal cap.
        effective = min(384, per_attempt)
        expect(page.locator("#provider-output-tokens")).to_have_text(
            "384 per attempt"
            if effective == 384
            else f"{effective:,} per attempt for signals (profile allows 384)"
        )
        expect(page.locator("#cognition-funded-status")).to_have_text(
            f"The first {default} of up to {selected} selected responses go to the model; the "
            "rest are recorded as budget-skipped neutral signals."
        )
        page.locator("#cognition-funded-responses").select_option(str(largest))
        expect(disclosure).to_contain_text(
            f"{largest} requests reserve {largest * reserved:,} output tokens."
        )
        if largest == selected:
            expect(page.locator("#cognition-funded-status")).to_have_text(
                f"Every selected response (up to {selected}) goes to the model."
            )
        labels = page.locator("label, dt, legend, h1, h2, h3, summary, option").all_inner_texts()
        assert not any("spending limit" in text.lower() for text in labels)
        funded = page.locator("#cognition-funded-responses")
        expect(funded).to_have_accessible_description(
            re.compile(
                rf"(go|goes) to the model.*reserves {reserved:,} output tokens.*"
                r"not a spending limit"
            )
        )

        page.locator("#cognition-mode").select_option("mock")
        expect(disclosure).to_be_hidden()
        expect(page.locator("#cognition-funded-status")).to_contain_text(
            "No provider tokens are reserved"
        )
        expect(funded).not_to_have_accessible_description(re.compile("output tokens"))
        page.locator("#cognition-mode").select_option("local")
        expect(funded).to_have_value(str(largest))


def test_credentials_clear_on_every_authority_navigation_submission_and_error(
    tmp_path: Path,
) -> None:
    marker = "-".join(("bounded", "credential", "marker", "4815162342"))
    posted: list[str] = []
    document = _provider_document(
        _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"),
        _profile("remote-alt", "remote", "e" * 64, label="Remote alt"),
    )
    refusal = {"error": {"code": "provider-unavailable", "message": "Provider refused."}}
    transitions: dict[str, Callable[[Page], object]] = {
        "scope": lambda page: page.locator("#cognition-effect-scope").select_option(EXPLANATION),
        "source": lambda page: page.locator("#cognition-mode").select_option("local"),
        "profile": lambda page: page.locator("#provider-profile").select_option(
            f"remote-alt/{'e' * 64}"
        ),
        "navigation": lambda page: page.locator("#stage-campaign").click(),
        "validation": lambda page: page.locator("#validate-run").click(),
        "reset": lambda page: page.locator("#reset-draft").click(),
        "error": lambda page: page.locator("#provider-check").click(),
    }

    with _session(tmp_path) as (page, base_url, _root):
        page.on(
            "request",
            lambda request: posted.append(f"{request.url} {request.post_data or ''}"),
        )
        _providers(page, document)
        page.route(
            "**/api/providers/*/revisions/*/check",
            lambda route: _fulfill(route, refusal, 503),
        )
        _to_run_step(page, base_url)
        for name, transition in transitions.items():
            page.locator("#stage-run").click()
            _choose(page, BOUNDED, "remote")
            credential = page.locator("#provider-credential")
            credential.fill(marker)
            expect(credential).to_have_value(marker)
            transition(page)
            expect(credential, name).to_have_value("")
            if name == "error":
                expect(page.locator("#provider-check-status")).to_have_text("Provider refused.")
        snapshot = page.evaluate(
            """marker => ({
              text: document.body.textContent.includes(marker),
              html: document.documentElement.outerHTML.includes(marker),
              url: location.href.includes(marker),
              history: JSON.stringify(history.state || null).includes(marker),
              local: localStorage.length,
              session: sessionStorage.length,
            })""",
            marker,
        )
        assert snapshot == {
            "text": False,
            "html": False,
            "url": False,
            "history": False,
            "local": 0,
            "session": 0,
        }
        assert not any(marker in item for item in posted)


def test_scripted_v10_job_never_relabels_or_regresses_its_phases(tmp_path: Path) -> None:
    job_id = "job-" + "e" * 32
    script: list[str] = []

    with _session(tmp_path) as (page, base_url, _root):
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "rules")
        draft = _validate(page)
        accepted = {
            "schema_version": 1,
            "workbench_input_schema_version": 4,
            "run_schema_version": 10,
            "city_id": draft["city_id"],
            "scenario_id": draft["scenario"]["scenario_id"],
            "campaign_id": draft["scenario"]["campaign"]["campaign_id"],
            "seed": draft["settings"]["seed"],
            "agent_count": draft["settings"]["agent_count"],
            "days": draft["settings"]["days"],
            "response_mode": "deterministic-rules",
            "effect_scope": BOUNDED,
            "cognition_mode": "rules",
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "funded_response_limit": OFFLINE,
            "aggregate_output_token_ceiling": 0,
        }
        summary = _library_summary(draft["settings"]["run_id"]).model_dump(mode="json")
        summary.update(
            run_schema_version=10,
            workbench_input_schema_version=4,
            days=draft["settings"]["days"],
            agent_count=draft["settings"]["agent_count"],
            frame_count=draft["settings"]["days"] * 1440,
            opportunity_count=12,
            impression_count=12,
            noticed_count=5,
            response_count=5,
            effect_scope=BOUNDED,
            cognition_mode="rules",
            provider_profile_id=None,
            provider_revision_sha256=None,
            funded_response_limit=OFFLINE,
            aggregate_output_token_ceiling=0,
            eligible_response_count=5,
            selected_request_count=5,
            funded_request_count=5,
            budget_skipped_count=0,
            unselected_response_count=0,
            applied_response_count=5,
            rules_count=5,
            mock_count=0,
            provider_count=0,
            cache_count=0,
            fallback_count=0,
            neutralized_response_count=5,
            uncertain_result_count=0,
            safety_neutralized_result_count=0,
        )

        def job(phase: str) -> dict[str, object]:
            terminal = phase == "completed"
            return {
                "schema_version": 1,
                "job_id": job_id,
                "run_id": draft["settings"]["run_id"],
                "accepted_settings": accepted,
                "phase": phase,
                "created_at": "2026-10-19T00:00:00.000000Z",
                "updated_at": "2026-10-19T00:00:01.000000Z",
                "cancellation_requested": False,
                "can_cancel": not terminal and phase not in {"persisting", "verifying"},
                "error": None,
                "result": summary if terminal else None,
            }

        page.evaluate(
            """() => {
              const label = document.getElementById('job-phase');
              window.__phases = [];
              new MutationObserver(() => {
                if (window.__phases.at(-1) !== label.textContent) {
                  window.__phases.push(label.textContent);
                }
              }).observe(label, {childList: true, characterData: true, subtree: true});
            }"""
        )
        script.extend(
            [
                "requesting-cognition",
                "planning-cognition",
                "applying-cognition",
                "persisting",
                "verifying",
                "completed",
            ]
        )
        page.route(
            "**/api/jobs",
            lambda route: _fulfill(
                route,
                {
                    "schema_version": 1,
                    "job": job("planning-cognition"),
                    "status_url": f"/api/jobs/{job_id}",
                },
                202,
            ),
        )
        page.route(f"**/api/jobs/{job_id}", lambda route: _fulfill(route, job(script.pop(0))))
        page.locator("#start-run").click()
        expect(page.locator("#job-inspect-link")).to_be_visible(timeout=20_000)

        assert page.evaluate("window.__phases") == [
            "PLANNING-COGNITION",
            "REQUESTING-COGNITION",
            "APPLYING-COGNITION",
            "PERSISTING",
            "VERIFYING",
            "COMPLETED",
        ]
        steps = page.locator("#job-phase-track li")
        assert steps.evaluate_all("items => items.map(item => item.dataset.phase)") == [
            "queued",
            "planning-cognition",
            "requesting-cognition",
            "applying-cognition",
            "persisting",
            "verifying",
            "completed",
        ]
        assert set(steps.evaluate_all("items => items.map(item => item.dataset.status)")) == {
            "done"
        }
        # A projector-width panel shows the whole sequence as one readable row, in view.
        expect(page.locator("#job-phase-track")).to_be_in_viewport()
        tops = steps.evaluate_all(
            "items => items.map(item => Math.round(item.getBoundingClientRect().top))"
        )
        assert len(set(tops)) == 1, tops
        expect(page.locator("#job-settings")).to_contain_text(
            f"Rules · offline, up to {_offered(page, base_url)['selected_requests_per_run']} "
            "selected"
        )
        expect(page.locator("#error-banner")).to_be_hidden()


def test_v9_phase_cannot_relabel_a_bounded_job(tmp_path: Path) -> None:
    job_id = "job-" + "d" * 32

    with _session(tmp_path) as (page, base_url, _root):
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "mock")
        draft = _validate(page)
        accepted = {
            "schema_version": 1,
            "workbench_input_schema_version": 4,
            "run_schema_version": 10,
            "city_id": draft["city_id"],
            "scenario_id": draft["scenario"]["scenario_id"],
            "campaign_id": draft["scenario"]["campaign"]["campaign_id"],
            "seed": draft["settings"]["seed"],
            "agent_count": draft["settings"]["agent_count"],
            "days": draft["settings"]["days"],
            "response_mode": "deterministic-rules",
            "effect_scope": BOUNDED,
            "cognition_mode": "mock",
            "provider_profile_id": None,
            "provider_revision_sha256": None,
            "funded_response_limit": OFFLINE,
            "aggregate_output_token_ceiling": 0,
        }
        relabelled = {
            "schema_version": 1,
            "job_id": job_id,
            "run_id": draft["settings"]["run_id"],
            "accepted_settings": accepted,
            "phase": "selecting-explanations",
            "created_at": "2026-10-19T00:00:00.000000Z",
            "updated_at": "2026-10-19T00:00:00.000000Z",
            "cancellation_requested": False,
            "can_cancel": True,
            "error": None,
            "result": None,
        }
        page.route(
            "**/api/jobs",
            lambda route: _fulfill(
                route,
                {"schema_version": 1, "job": relabelled, "status_url": f"/api/jobs/{job_id}"},
                202,
            ),
        )
        page.locator("#start-run").click()
        expect(page.locator("#error-banner")).to_contain_text("incompatible")
        expect(page.locator("#job-panel")).to_be_hidden()


class _SignalProvider:
    """A loopback-free fake provider returning one closed category vector."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls = 0
        self.closed = False

    async def call(self, request: Any) -> CityProviderAttemptV2:
        self.calls += 1
        result = CityCognitionResultV2(
            schema_version=2,
            request_id=request.request_id,
            affect="curious",
            relevance="high",
            credibility="medium",
            salience="high",
            safety_flags=(),
            disclosure="synthetic-bounded-cognition-not-observed-behavior",
        )
        return CityProviderAttemptV2(
            schema_version=2, status="valid", result=result, repair_context=None, usage=None
        )

    async def repair_call(self, *_args: object, **_kwargs: object) -> CityProviderAttemptV2:
        raise AssertionError("a valid signal never needs repair")

    async def aclose(self) -> None:
        self.closed = True


def _gate(monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[threading.Event, threading.Event]:
    entered = threading.Event()
    release = threading.Event()
    repository = workbench_web.WorkbenchRunRepository
    original = getattr(repository, name)

    def gated(*args: object, **kwargs: object) -> object:
        entered.set()
        if not release.wait(30):
            raise RuntimeError(f"{name} gate timed out")
        return original(*args, **kwargs)

    static = isinstance(inspect.getattr_static(repository, name), staticmethod)
    monkeypatch.setattr(repository, name, staticmethod(gated) if static else gated)
    return entered, release


def test_real_bounded_local_run_shows_exact_phases_and_completes_as_v10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers: list[_SignalProvider] = []

    def factory(**kwargs: object) -> _SignalProvider:
        provider = _SignalProvider(**kwargs)
        providers.append(provider)
        return provider

    gates = {
        phase: _gate(monkeypatch, method)
        for phase, method in (
            ("PLANNING-COGNITION", "prepare_scientific_v2"),
            ("REQUESTING-COGNITION", "resolve_cognition_v2"),
            ("APPLYING-COGNITION", "finalize_cognition_v2"),
        )
    }
    job_posts: list[str] = []
    try:
        with _session(tmp_path, provider_factory_v2=factory) as (page, base_url, root):
            page.on(
                "request",
                lambda request: (
                    job_posts.append(request.post_data or "")
                    if request.method == "POST" and request.url.endswith("/api/jobs")
                    else None
                ),
            )
            _to_run_step(page, base_url)
            page.locator("#run-id").fill("bounded-local-browser")
            page.locator("#agent-count").fill("2")
            _set_audience_count(page, 2)
            _choose(page, BOUNDED, "local")
            expect(page.locator("#provider-empty-hint")).to_contain_text("qwen2.5:7b-instruct")
            expect(page.locator("#provider-create-details")).to_have_attribute("open", "")
            expect(page.locator("#provider-model")).to_have_value("qwen2.5:7b-instruct")
            page.locator("#provider-create-submit").click()
            expect(page.locator("#provider-profile")).to_be_enabled(timeout=10_000)
            expect(page.locator("#provider-empty-hint")).to_be_hidden()
            with page.expect_request("**/check") as check_request:
                page.locator("#provider-check").click()
            assert check_request.value.post_data == '{"effect_scope":"bounded-signals-v1"}'
            expect(page.locator("#provider-check-status")).to_have_text(
                "Bounded-signal contract confirmed.", timeout=15_000
            )
            draft = _validate(page)
            assert draft["schema_version"] == 4
            expect(page.locator("#start-run")).to_be_enabled()

            page.locator("#start-run").dblclick()
            for phase, (entered, release) in gates.items():
                assert entered.wait(15), phase
                expect(page.locator("#job-phase")).to_have_text(phase, timeout=10_000)
                current = page.locator("#job-phase-track li[aria-current='step']")
                expect(current).to_have_attribute("data-phase", phase.lower())
                release.set()
            expect(page.locator("#job-inspect-link")).to_be_visible(timeout=30_000)
            expect(page.locator("#job-inspect-link")).to_have_attribute(
                "href", "/runs/bounded-local-browser"
            )
            assert len(job_posts) == 1
            assert json.loads(job_posts[0]) == draft

            stored = CityRunStore(root).load("bounded-local-browser")
            assert stored.manifest.schema_version == 10
            assert stored.workbench_input is not None
            assert stored.workbench_input.schema_version == 4
            assert sum(provider.calls for provider in providers) > 0
            assert all(provider.closed for provider in providers)

            page.locator("#stage-inspect").click()
            card = page.locator("#run-library .run-card", has_text="bounded-local-browser")
            expect(card).to_contain_text("Bounded · Local model")
            expect(card).to_contain_text("selected")
    finally:
        for _entered, release in gates.values():
            release.set()


def test_bounded_rules_job_can_be_cancelled_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = _gate(monkeypatch, "resolve_cognition_v2")
    try:
        with _session(tmp_path) as (page, base_url, root):
            _to_run_step(page, base_url)
            page.locator("#run-id").fill("bounded-cancel")
            page.locator("#agent-count").fill("1")
            _set_audience_count(page, 1)
            _choose(page, BOUNDED, "rules")
            _validate(page)
            page.locator("#start-run").click()
            assert entered.wait(15)
            expect(page.locator("#job-phase")).to_have_text("REQUESTING-COGNITION", timeout=10_000)
            page.locator("#cancel-job").click()
            expect(page.locator("#job-message")).to_contain_text("Cancellation requested")
            release.set()
            expect(page.locator("#job-phase")).to_have_text("CANCELLED", timeout=15_000)
            stopped = page.locator("#job-phase-track li[data-status='stopped']")
            assert stopped.evaluate_all("items => items.map(item => item.dataset.phase)") == [
                "requesting-cognition",
                "completed",
            ]
            expect(page.locator("#job-phase-track li").last).to_contain_text("Cancelled")
            expect(page.locator("#job-inspect-link")).to_be_hidden()
            assert not (root / "city-runs" / "bounded-cancel").exists()
    finally:
        release.set()


def test_bounded_field_errors_focus_their_controls_and_keyboard_order(tmp_path: Path) -> None:
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        _to_run_step(page, base_url)
        page.locator("#cognition-effect-scope").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "cognition-mode"

        # An undeclared operation is refused locally and focuses its control.
        page.locator("#cognition-effect-scope").evaluate(
            """node => {
              const option = document.createElement('option');
              option.value = 'bounded-signals-v9';
              option.textContent = 'Undeclared';
              node.append(option);
              node.value = 'bounded-signals-v9';
            }"""
        )
        page.locator("#validate-run").click()
        expect(page.locator("#cognition-effect-scope-error")).to_have_text(
            "Choose an operation offered by this server."
        )
        assert page.evaluate("document.activeElement.id") == "cognition-effect-scope"
        expect(page.locator("#cognition-effect-scope")).to_have_attribute("aria-invalid", "true")
        page.locator("#cognition-effect-scope option[value='bounded-signals-v9']").evaluate(
            "node => node.remove()"
        )

        # An undeclared funding count is refused before any request.
        _choose(page, BOUNDED, "local")
        page.locator("#cognition-funded-responses").evaluate(
            """(node, value) => {
              const option = document.createElement('option');
              option.value = value;
              option.textContent = `${value} requests`;
              node.append(option);
              node.value = value;
            }""",
            str(max(LIMITS["funded_response_limits"]) + 1),
        )
        page.locator("#validate-run").click()
        expect(page.locator("#cognition-funded-responses-error")).to_have_text(
            "Choose a funded response count offered by this server."
        )
        assert page.evaluate("document.activeElement.id") == "cognition-funded-responses"

        # Server field paths route to the same bounded controls.
        page.locator("#cognition-funded-responses").select_option(
            str(LIMITS["default_provider_funded_response_limit"])
        )
        page.route(
            "**/api/scenarios/validate",
            lambda route: _fulfill(
                route,
                {
                    "error": {
                        "code": "invalid-fields",
                        "message": "One or more request fields are invalid.",
                        "fields": {
                            "cognition.funded_response_limit": "This field is invalid.",
                            "cognition.effect_scope": "This field is invalid.",
                        },
                    }
                },
                422,
            ),
        )
        page.locator("#validate-run").click()
        expect(page.locator("#cognition-funded-responses-error")).to_have_text(
            "This field is invalid."
        )
        expect(page.locator("#cognition-effect-scope-error")).to_have_text("This field is invalid.")
        assert page.evaluate("document.activeElement.id") == "cognition-funded-responses"


@pytest.mark.parametrize(
    ("width", "height", "media"),
    [(390, 900, {}), (720, 450, {}), (1440, 900, {"forced_colors": "active"})],
    ids=["phone-390", "zoom-200", "projector-forced-colors"],
)
def test_bounded_controls_fit_small_zoomed_and_forced_color_viewports(
    tmp_path: Path, width: int, height: int, media: dict[str, str]
) -> None:
    persian = "مدل محلی آزمایشی"
    hostile = "</script><img src=x onerror=alert(1)>"
    document = _provider_document(
        _profile("local-lab", "local", LOCAL_REVISION, label=persian, model=hostile),
    )
    with _session(tmp_path, width=width) as (page, base_url, _root):
        page.set_viewport_size({"width": width, "height": height})
        page.emulate_media(reduced_motion="reduce", **media)
        _providers(page, document)
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        _validate(page)
        snapshot = page.evaluate(
            """boundary => {
              const leaves = [...document.querySelectorAll('#explanations-section *')]
                .filter(node => node.childElementCount === 0);
              const settings = document.getElementById('bounded-settings');
              const controls = ['cognition-effect-scope', 'cognition-mode',
                'cognition-funded-responses', 'provider-check', 'start-run'];
              return {
                overflow: document.documentElement.scrollWidth - window.innerWidth,
                // UTF-8 read as Windows-1252 shows up as these sequences.
                mojibake: /\\u00c2|\\u00c3|\\u00e2\\u20ac/.test(document.body.innerText),
                boundaryCount: leaves.filter(node => node.textContent === boundary).length,
                spendingNotes: (document.body.innerText.match(/not a spending limit/g) || [])
                  .length,
                images: document.querySelectorAll('main img').length,
                bdi: document.querySelector('#review-source bdi')?.textContent,
                transition: getComputedStyle(settings).transitionDuration,
                controls: controls.map(id => {
                  const box = document.getElementById(id).getBoundingClientRect();
                  return box.width > 0 && box.right <= window.innerWidth + 0.5 &&
                    box.height >= 40;
                }),
              };
            }""",
            BOUNDARY,
        )
        assert snapshot["overflow"] <= 0
        assert snapshot["mojibake"] is False
        assert snapshot["boundaryCount"] == 1
        assert snapshot["spendingNotes"] == 1
        assert snapshot["images"] == 0
        assert snapshot["bdi"] == persian
        assert snapshot["transition"] in {"0s", "0ms"}
        assert all(snapshot["controls"])
        expect(page.locator("#provider-model-receipt")).to_have_text(hostile)
        expect(page.locator("#cognition-boundary")).to_have_attribute("role", "note")
        expect(page.locator("#review-ownership dt")).to_have_count(5)


def test_bounded_copy_follows_any_consistent_server_capability(tmp_path: Path) -> None:
    """No limit is pinned in the browser: another self-consistent declaration drives the copy."""
    declared: dict[str, Any] = {}

    def alternative(route: Route) -> None:
        response = route.fetch()
        document = response.json()
        bounded = document["capabilities"]["bounded_cognition"]
        per_attempt = bounded["max_output_tokens_per_attempt"] + 64
        attempts = bounded["max_http_attempts_per_request"] + 1
        selected = bounded["selected_requests_per_run"]
        bounded.update(
            max_output_tokens_per_attempt=per_attempt,
            max_http_attempts_per_request=attempts,
            reserved_output_tokens_per_request=per_attempt * attempts,
            funded_response_limits=[5, selected],
            default_provider_funded_response_limit=5,
        )
        declared.update(bounded)
        route.fulfill(response=response, json=document)

    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        page.route("**/api/workbench", alternative)
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")

        reserved = declared["reserved_output_tokens_per_request"]
        per_attempt = declared["max_output_tokens_per_attempt"]
        funded = page.locator("#cognition-funded-responses")
        assert funded.locator("option").evaluate_all("nodes => nodes.map(n => n.value)") == [
            "5",
            str(declared["selected_requests_per_run"]),
        ]
        expect(funded).to_have_value("5")
        expect(page.locator("#bounded-cost-disclosure")).to_contain_text(
            f"Each funded request reserves {reserved:,} output tokens: at most "
            f"{declared['max_http_attempts_per_request']} HTTP attempts of up to "
            f"{per_attempt:,} output tokens each. 5 requests reserve {5 * reserved:,} output "
            "tokens."
        )
        effective = min(384, per_attempt)
        expect(page.locator("#provider-output-tokens")).to_have_text(
            "384 per attempt"
            if effective == 384
            else f"{effective:,} per attempt for signals (profile allows 384)"
        )
        expect(page.locator("#error-banner")).to_be_hidden()


def test_check_timeout_suggests_warming_a_local_model(tmp_path: Path) -> None:
    document = _provider_document(
        _profile("local-ollama", "local", LOCAL_REVISION, label="Local Ollama"),
        {
            **_profile("local-llama", "local", LOCAL_ALT_REVISION, label="Other local"),
            "origin_approval_id": "local-llama",
        },
        _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"),
    )
    outcome = {"timeout": True}

    def check(route: Route) -> None:
        _, profile_id, _, revision, _ = route.request.url.rsplit("/", 4)
        bounded = json.loads(route.request.post_data or "{}") == {"effect_scope": BOUNDED}
        if outcome["timeout"]:
            body: dict[str, object] = {"status": "incompatible", "reason_code": "timeout-exhausted"}
            if bounded:
                body = {**_v2_check(profile_id, revision, status="incompatible"), **body}
        elif bounded:
            body = _v2_check(profile_id, revision)
        else:
            body = {"status": "compatible", "reason_code": None}
        _fulfill(route, body)

    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, document)
        page.route("**/api/providers/*/revisions/*/check", check)
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        page.locator("#provider-profile").select_option(f"local-ollama/{LOCAL_REVISION}")
        status = page.locator("#provider-check-status")
        hint = page.locator("#provider-check-hint")
        command = "ollama run qwen2.5:7b-instruct --keepalive 30m hello"

        page.locator("#provider-check").click()
        expect(status).to_have_text(
            "Not confirmed: the model did not answer within the check's time limit."
        )
        expect(hint).to_be_visible()
        expect(hint).to_contain_text("The local model may still be loading.")
        expect(hint.locator("code")).to_have_text(command)
        _validate(page)
        expect(page.locator("#start-run")).to_be_disabled()

        # Any authority edit clears both the stale status and its hint.
        page.locator("#cognition-effect-scope").select_option(EXPLANATION)
        expect(status).to_have_text("")
        expect(hint).to_be_hidden()
        page.locator("#provider-check").click()
        expect(hint.locator("code")).to_have_text(command)

        # A non-Ollama local origin gets the same advice without a command it may not have.
        page.locator("#provider-profile").select_option(f"local-llama/{LOCAL_ALT_REVISION}")
        expect(hint).to_be_hidden()
        page.locator("#provider-check").click()
        expect(hint).to_have_text(
            "The local model may still be loading. Warm it first, then check again."
        )
        expect(hint.locator("code")).to_have_count(0)

        # A remote timeout is not a loading problem the operator can warm away.
        page.locator("#cognition-mode").select_option("remote")
        page.locator("#provider-check").click()
        expect(status).to_contain_text("did not answer within")
        expect(hint).to_be_hidden()

        # A later success replaces the timeout advice.
        outcome["timeout"] = False
        _choose(page, BOUNDED, "local")
        page.locator("#provider-profile").select_option(f"local-ollama/{LOCAL_REVISION}")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        expect(hint).to_be_hidden()
        assert page.locator("main img").count() == 0


def test_check_outcomes_are_stated_and_a_refused_local_model_points_at_its_id(
    tmp_path: Path,
) -> None:
    document = _provider_document(
        _profile("local-ollama", "local", LOCAL_REVISION, label="Local Ollama", model="qwen2.5:7b"),
        {
            **_profile("local-llama", "local", LOCAL_ALT_REVISION, label="Other local"),
            "origin_approval_id": "local-llama",
        },
        _profile("remote-demo", "remote", REMOTE_REVISION, label="Remote lab"),
    )
    outcome = {"compatible": False}

    def check(route: Route) -> None:
        _, profile_id, _, revision, _ = route.request.url.rsplit("/", 4)
        if outcome["compatible"]:
            _fulfill(route, _v2_check(profile_id, revision))
            return
        _fulfill(
            route,
            {
                **_v2_check(profile_id, revision, status="incompatible"),
                "reason_code": "protocol-incompatible",
            },
        )

    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, document)
        page.route("**/api/providers/*/revisions/*/check", check)
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        page.locator("#provider-profile").select_option(f"local-ollama/{LOCAL_REVISION}")
        panel = page.locator("#provider-check-panel")
        scope = page.locator("#provider-check-scope")
        status = page.locator("#provider-check-status")
        hint = page.locator("#provider-check-hint")
        expect(scope).to_have_text("Bounded-signal contract · required before start")
        expect(panel).to_have_attribute("data-check-state", "none")

        page.locator("#provider-check").click()
        # The refusal keeps its exact reason and names the usual cause in plain words.
        expect(status).to_have_text("Not compatible with bounded signals · protocol incompatible.")
        expect(panel).to_have_attribute("data-check-state", "failed")
        expect(scope).to_have_text("Bounded-signal contract · not confirmed")
        expect(hint).to_be_visible()
        expect(hint).to_contain_text("qwen2.5:7b")
        expect(hint).to_contain_text("not installed")
        expect(hint.locator("code")).to_have_text("ollama list")

        # A non-Ollama local origin gets the same advice without a command it may not have.
        page.locator("#provider-profile").select_option(f"local-llama/{LOCAL_ALT_REVISION}")
        expect(panel).to_have_attribute("data-check-state", "none")
        expect(hint).to_be_hidden()
        page.locator("#provider-check").click()
        expect(hint).to_contain_text("not installed")
        expect(hint.locator("code")).to_have_count(0)

        # A remote refusal is not a local installation problem.
        page.locator("#cognition-mode").select_option("remote")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Not compatible with bounded signals · protocol incompatible.")
        expect(hint).to_be_hidden()

        # A success replaces the warning heading instead of leaving it beside the success.
        outcome["compatible"] = True
        _choose(page, BOUNDED, "local")
        page.locator("#provider-profile").select_option(f"local-ollama/{LOCAL_REVISION}")
        page.locator("#provider-check").click()
        expect(status).to_have_text("Bounded-signal contract confirmed.")
        expect(scope).to_have_text("Bounded-signal contract · confirmed for this revision")
        expect(panel).to_have_attribute("data-check-state", "confirmed")
        expect(hint).to_be_hidden()
        # Any authority edit returns the row to its neutral, required state.
        page.locator("#cognition-effect-scope").select_option(EXPLANATION)
        expect(scope).to_have_text("Explanation contract")
        expect(panel).to_have_attribute("data-check-state", "none")
        page.locator("#cognition-effect-scope").select_option(BOUNDED)
        expect(scope).to_have_text("Bounded-signal contract · required before start")
        expect(status).to_have_text("")


def test_inspector_run_again_restores_a_local_v10_draft_and_its_server_check(
    tmp_path: Path,
) -> None:
    """Inspect, then run again: the draft and this server's bounded check both come back."""
    providers: list[_SignalProvider] = []

    def factory(**kwargs: object) -> _SignalProvider:
        provider = _SignalProvider(**kwargs)
        providers.append(provider)
        return provider

    checks: list[str] = []
    posted: list[dict[str, Any]] = []
    with _session(tmp_path, provider_factory_v2=factory) as (page, base_url, _root):
        page.on(
            "request",
            lambda request: (
                checks.append(request.url)
                if request.method == "POST" and request.url.endswith("/check")
                else posted.append(json.loads(request.post_data or "{}"))
                if request.method == "POST" and request.url.endswith("/api/scenarios/validate")
                else None
            ),
        )
        _to_run_step(page, base_url)
        page.locator("#run-id").fill("bounded-reuse-001")
        page.locator("#agent-count").fill("2")
        _set_audience_count(page, 2)
        _choose(page, BOUNDED, "local")
        page.locator("#provider-create-submit").click()
        expect(page.locator("#provider-profile")).to_be_enabled(timeout=10_000)
        funded = str(LIMITS["funded_response_limits"][0])
        page.locator("#cognition-funded-responses").select_option(funded)
        page.locator("#provider-check").click()
        expect(page.locator("#provider-check-status")).to_have_text(
            "Bounded-signal contract confirmed.", timeout=15_000
        )
        expect(page.locator("#provider-check-scope")).to_have_text(
            "Bounded-signal contract · confirmed for this revision"
        )
        expect(page.locator("#provider-check-panel")).to_have_attribute(
            "data-check-state", "confirmed"
        )
        original = _validate(page)
        page.locator("#start-run").click()
        inspect_link = page.locator("#job-inspect-link")
        expect(inspect_link).to_be_visible(timeout=60_000)
        inspect_link.click()
        page.wait_for_url(f"{base_url}/runs/bounded-reuse-001")
        reuse = page.locator("#workbench-reuse-link")
        expect(reuse).to_be_visible(timeout=30_000)
        expect(reuse).to_have_attribute("href", "/runs/bounded-reuse-001/reuse")
        expect(page.locator("#workbench-back-link")).to_have_attribute("href", "/")
        reuse.click()
        page.wait_for_url(f"{base_url}/runs/bounded-reuse-001/reuse")
        expect(page.locator("#panel-run")).to_be_visible(timeout=30_000)
        expect(page.locator("#run-id")).to_have_value("bounded-reuse-002")
        expect(page.locator("#cognition-effect-scope")).to_have_value(BOUNDED)
        expect(page.locator("#cognition-mode")).to_have_value("local")
        expect(page.locator("#cognition-funded-responses")).to_have_value(funded)
        # The server enforces the same process-local check at submission; showing it again
        # needs no second inference.
        expect(page.locator("#provider-check-status")).to_have_text(
            "Bounded-signal contract confirmed.", timeout=15_000
        )
        expect(page.locator("#provider-check-scope")).to_have_text(
            "Bounded-signal contract · confirmed earlier in this workbench session"
        )
        restored = _validate(page)
        expect(page.locator("#start-run")).to_be_enabled()
        expect(page.locator("#review-compatibility")).to_have_text(
            "Bounded-signal check passed for this revision"
        )
        assert len(checks) == 1
        assert restored["settings"]["run_id"] == "bounded-reuse-002"
        assert {**restored, "settings": {**restored["settings"], "run_id": "x"}} == {
            **original,
            "settings": {**original["settings"], "run_id": "x"},
        }
        assert original == posted[0]


def test_reuse_page_admits_only_portable_run_ids_and_unknown_runs_change_nothing(
    tmp_path: Path,
) -> None:
    with _session(tmp_path) as (page, base_url, _root):
        # The server refuses a malformed run ID before any page or script is served.
        for malformed in ("Not_A_Run", "a" * 41):
            refused = page.request.get(f"{base_url}/runs/{malformed}/reuse")
            assert refused.status == 404
            assert refused.headers["content-type"].startswith("application/json")
        assert page.request.get(f"{base_url}/runs/missing-run/reuse?then=x").status == 422

        # An unknown run is refused in the banner and the default draft is untouched.
        page.goto(f"{base_url}/runs/missing-run/reuse", wait_until="networkidle")
        expect(page.locator("#error-banner")).to_be_visible(timeout=15_000)
        expect(page.locator("#scenario-name")).to_have_value("Synthetic city study")
        expect(page.locator("#panel-city")).to_be_visible()
        assert page.locator('meta[name="adlife-reuse-run"]').get_attribute("content") == (
            "missing-run"
        )
        # The plain workbench never asks for a reuse.
        page.goto(base_url, wait_until="networkidle")
        assert page.locator('meta[name="adlife-reuse-run"]').get_attribute("content") == ""


def test_library_and_job_facts_keep_cache_apart_and_read_plainly(tmp_path: Path) -> None:
    from tests.security.test_workbench_browser_security import _job_documents

    funded = LIMITS["funded_response_limits"][0]
    v10 = _library_summary("library-local-v10").model_dump(mode="json")
    v10.update(
        run_schema_version=10,
        workbench_input_schema_version=4,
        opportunity_count=30,
        impression_count=30,
        noticed_count=funded + 8,
        response_count=funded + 8,
        effect_scope=BOUNDED,
        cognition_mode="local",
        provider_profile_id="local-lab",
        provider_revision_sha256=LOCAL_REVISION,
        funded_response_limit=funded,
        aggregate_output_token_ceiling=funded * RESERVED,
        eligible_response_count=funded + 8,
        selected_request_count=funded + 8,
        funded_request_count=funded,
        budget_skipped_count=8,
        unselected_response_count=0,
        applied_response_count=funded + 8,
        rules_count=0,
        mock_count=0,
        provider_count=funded - 4,
        cache_count=3,
        fallback_count=1,
        neutralized_response_count=9,
        uncertain_result_count=0,
        safety_neutralized_result_count=0,
    )
    v9 = _library_summary("library-local-v9").model_dump(mode="json")
    v9.update(
        run_schema_version=9,
        cognition_mode="local",
        provider_profile_id="local-lab",
        provider_revision_sha256=LOCAL_REVISION,
        aggregate_output_token_ceiling=10 * V9_TOKENS_PER_EXPLANATION,
    )
    v7 = _library_summary("library-legacy-v7").model_dump(mode="json")
    page_document = {
        "schema_version": 1,
        "offset": 0,
        "limit": 20,
        "returned_count": 3,
        "verified_total": 3,
        "scanned_count": 3,
        "corrupt_count": 0,
        "truncated": False,
        "runs": [v10, v9, v7],
    }
    settings, job = _job_documents()
    settings.update(
        agent_count=1,
        days=1,
        cognition_mode="local",
        provider_profile_id="local-lab",
        provider_revision_sha256=LOCAL_REVISION,
        aggregate_output_token_ceiling=10 * V9_TOKENS_PER_EXPLANATION,
    )
    job.update(phase="requesting-explanations")

    def active(route: Route) -> None:
        response = route.fetch()
        document = response.json()
        document["active_job"] = job
        route.fulfill(response=response, json=document)

    with _session(tmp_path) as (page, base_url, _root):
        page.route("**/api/workbench", active)
        page.route(f"**/api/jobs/{job['job_id']}", lambda route: _fulfill(route, job))
        page.route("**/api/runs?offset=0&limit=20", lambda route: _fulfill(route, page_document))
        page.goto(base_url, wait_until="domcontentloaded")
        # The resumed job opens the run step and states its facts in words.
        expect(page.locator("#panel-run")).to_be_visible(timeout=15_000)
        facts = page.locator("#job-settings")
        expect(facts).to_contain_text("Population1 person · 1 day")
        expect(facts).to_contain_text("ExplanationsLocal model · 10 funded explanations")

        page.locator("#stage-inspect").click()
        cards = page.locator("#run-library .run-card")
        expect(cards).to_have_count(3)
        expect(cards.nth(0)).to_contain_text(
            f"Signals{funded + 8} selected · {funded - 4} model · 3 cached · 8 budget-skipped"
            " · 1 fallback"
        )
        expect(cards.nth(1)).to_contain_text("ExplanationsLocal model · 10 funded explanations")
        expect(cards.nth(2)).not_to_contain_text("Explanations")
        # V8 and later runs can seed a new draft; a homogeneous-cohort V7 run cannot.
        reuse = page.get_by_role("button", name="Run again with these inputs")
        expect(reuse).to_have_count(2)
        expect(
            cards.nth(2).get_by_role("button", name="Run again with these inputs")
        ).to_have_count(0)


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_job_panel_states_elapsed_and_total_time_while_the_model_answers(tmp_path: Path) -> None:
    """A long requesting phase shows that time is passing; a finished job shows its total."""
    from tests.security.test_workbench_browser_security import _job_documents

    settings, job = _job_documents()
    settings.update(
        agent_count=1,
        days=1,
        cognition_mode="local",
        provider_profile_id="local-lab",
        provider_revision_sha256=LOCAL_REVISION,
        aggregate_output_token_ceiling=10 * V9_TOKENS_PER_EXPLANATION,
    )
    started = datetime.now(UTC) - timedelta(seconds=27)
    job.update(
        phase="requesting-explanations",
        created_at=_utc_stamp(started),
        updated_at=_utc_stamp(started + timedelta(seconds=1)),
    )

    def active(route: Route) -> None:
        response = route.fetch()
        document = response.json()
        document["active_job"] = job
        route.fulfill(response=response, json=document)

    with _session(tmp_path) as (page, base_url, _root):
        page.route("**/api/workbench", active)
        page.route(f"**/api/jobs/{job['job_id']}", lambda route: _fulfill(route, job))
        page.goto(base_url, wait_until="domcontentloaded")
        elapsed = page.locator("#job-settings > div", has_text="Elapsed")
        expect(elapsed).to_have_count(1, timeout=15_000)
        seconds = int(elapsed.locator("dd").inner_text().removesuffix(" s"))
        assert 27 <= seconds <= 60
        # Every later poll re-reads the clock, so the presenter sees the wait move.
        expect(elapsed.locator("dd")).not_to_have_text(f"{seconds} s", timeout=10_000)
        # Wall-clock time is operational only: it never enters a draft or an artifact.
        assert "Duration" not in page.locator("#job-settings").inner_text()
