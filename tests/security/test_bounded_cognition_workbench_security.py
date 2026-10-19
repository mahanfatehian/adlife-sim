"""Fail-closed browser handling of hostile bounded-cognition workbench data."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Route, expect

from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.test_bounded_cognition_workbench_browser import (
    BOUNDED,
    C1,
    EXPLANATION,
    LIMITS,
    LOCAL_REVISION,
    RESERVED,
    V9_TOKENS_PER_EXPLANATION,
    _choose,
    _fulfill,
    _provider_document,
    _providers,
    _session,
    _to_run_step,
    _v2_check,
    _validate,
)
from tests.browser.test_city_workbench_browser import _library_page, _library_summary

HOSTILE = "</script><img src=x onerror=alert(1)>"


def _tamper_capabilities(
    tamper: Callable[[dict[str, Any]], None],
) -> Callable[[Route], None]:
    def capabilities(route: Route) -> None:
        response = route.fetch()
        document = response.json()
        tamper(document["capabilities"])
        route.fulfill(response=response, json=document)

    return capabilities


def _bounded(caps: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = caps["bounded_cognition"]
    return value


def _shift(name: str, delta: float) -> Callable[[dict[str, Any]], None]:
    """Move one declared number relative to whatever the real server declared."""
    return lambda caps: _bounded(caps).update({name: _bounded(caps)[name] + delta})


def _implausible_cap(caps: dict[str, Any]) -> None:
    bounded = _bounded(caps)
    bounded["max_output_tokens_per_attempt"] = 1_000_000
    bounded["reserved_output_tokens_per_request"] = (
        1_000_000 * bounded["max_http_attempts_per_request"]
    )


# Each tamper breaks one structural rule; none restates a server limit.
TAMPERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "extra-member": lambda caps: _bounded(caps).update(aggregate_output_token_ceiling=0),
    "missing-member": lambda caps: _bounded(caps).pop("max_http_attempts_per_request"),
    "widened-limit": lambda caps: _bounded(caps)["funded_response_limits"].append(
        _bounded(caps)["selected_requests_per_run"] + 1
    ),
    "unsorted-limits": lambda caps: _bounded(caps)["funded_response_limits"].reverse(),
    "repeated-limit": lambda caps: _bounded(caps)["funded_response_limits"].insert(
        0, _bounded(caps)["funded_response_limits"][0]
    ),
    "empty-limits": lambda caps: _bounded(caps)["funded_response_limits"].clear(),
    "string-number": lambda caps: _bounded(caps).update(
        reserved_output_tokens_per_request=str(_bounded(caps)["reserved_output_tokens_per_request"])
    ),
    "fractional-cap": _shift("max_output_tokens_per_attempt", 0.5),
    "zero-attempts": lambda caps: _bounded(caps).update(max_http_attempts_per_request=0),
    "short-reservation": _shift("reserved_output_tokens_per_request", -1),
    "implausible-cap": _implausible_cap,
    "boolean-version": lambda caps: _bounded(caps).update(workbench_schema_version=True),
    "future-run-version": _shift("run_schema_version", 1),
    "default-outside-limits": lambda caps: _bounded(caps).update(
        default_provider_funded_response_limit=max(_bounded(caps)["funded_response_limits"]) + 1
    ),
    "offline-below-selection": _shift("offline_funded_response_limit", -1),
    "dropped-mode": lambda caps: _bounded(caps)["modes"].pop(),
    "check-not-required": lambda caps: _bounded(caps)["compatibility_check_required_modes"].pop(),
    "reordered-scopes": lambda caps: caps.update(cognition_effect_scopes=[BOUNDED, EXPLANATION]),
    "half-declared": lambda caps: caps.pop("bounded_cognition"),
}


def test_served_bounded_assets_keep_credentials_out_of_urls_history_and_storage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token="asset-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        shell = client.get("/").text
        script = client.get("/assets/workbench.js").text
        providers = client.get("/assets/workbench-providers.js").text

    references = re.findall(r'(?:href|src)="([^"]+)"', shell)
    assert sorted(set(references)) == [
        "#main-content",
        "/",
        "/assets/workbench-city-packs.js",
        "/assets/workbench-icon.svg",
        "/assets/workbench-personas.css",
        "/assets/workbench-personas.js",
        "/assets/workbench-providers.css",
        "/assets/workbench-providers.js",
        "/assets/workbench.css",
        "/assets/workbench.js",
    ]
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "pushState",
        "replaceState",
        "location.hash",
        "location.search",
        "URLSearchParams",
        "http://",
        "https://",
        "eval(",
        "new Function",
    ):
        assert forbidden not in script + providers, forbidden
    # workbench.js only ever erases the credential field; it never reads its value.
    credential_uses = [
        line.strip() for line in script.splitlines() if "provider-credential" in line
    ]
    assert credential_uses == ['const input = byId("provider-credential");']
    assert 'input.value = "";' in script
    # The panel reads the field once, in the credential submission handler.
    assert providers.count("input.value;") == 1
    assert "provider-credential" not in providers.split("serialize() {", 1)[1].split("reset() {")[0]


@pytest.mark.parametrize("tamper", list(TAMPERS.values()), ids=list(TAMPERS))
def test_tampered_bounded_capability_fails_closed(
    tmp_path: Path, tamper: Callable[[dict[str, Any]], None]
) -> None:
    posts: list[str] = []
    with _session(tmp_path) as (page, base_url, _root):
        page.on(
            "request",
            lambda request: posts.append(request.url) if request.method == "POST" else None,
        )
        page.route("**/api/workbench", _tamper_capabilities(tamper))
        page.goto(base_url, wait_until="networkidle")

        expect(page.locator("#error-banner")).to_contain_text(
            "The workbench capability document is incompatible."
        )
        option = page.locator("#cognition-effect-scope option[value='bounded-signals-v1']")
        expect(option).to_be_disabled()
        expect(page.locator("#cognition-effect-scope")).to_have_value("explanation-only")
        assert posts == []


def test_absent_bounded_capability_offers_explanations_only(tmp_path: Path) -> None:
    def strip(capabilities: dict[str, Any]) -> None:
        for name in C1:
            capabilities.pop(name)

    with _session(tmp_path) as (page, base_url, _root):
        page.route("**/api/workbench", _tamper_capabilities(strip))
        page.goto(base_url, wait_until="networkidle")
        page.locator("#city-continue").click()
        page.locator("#campaign-continue").click()

        option = page.locator("#cognition-effect-scope option[value='bounded-signals-v1']")
        expect(option).to_be_disabled()
        expect(option).to_have_text("Bounded AI cognition (not offered by this server)")
        expect(page.locator("#error-banner")).to_be_hidden()
        page.locator("#cognition-effect-scope").evaluate(
            "node => { node.value = 'bounded-signals-v1'; }"
        )
        page.locator("#validate-run").click()
        expect(page.locator("#cognition-effect-scope-error")).to_have_text(
            "Choose an operation offered by this server."
        )
        page.locator("#cognition-effect-scope").select_option("explanation-only")
        assert _validate(page)["schema_version"] == 3


FUNDED = LIMITS["default_provider_funded_response_limit"]
OFFLINE = LIMITS["offline_funded_response_limit"]


def _v10_summary(**changes: object) -> dict[str, object]:
    summary = _library_summary("bounded-library").model_dump(mode="json")
    summary.update(
        run_schema_version=10,
        workbench_input_schema_version=4,
        opportunity_count=4,
        impression_count=4,
        noticed_count=3,
        response_count=3,
        effect_scope=BOUNDED,
        cognition_mode="local",
        provider_profile_id="local-lab",
        provider_revision_sha256=LOCAL_REVISION,
        funded_response_limit=FUNDED,
        aggregate_output_token_ceiling=FUNDED * RESERVED,
        eligible_response_count=3,
        selected_request_count=3,
        funded_request_count=3,
        budget_skipped_count=0,
        unselected_response_count=0,
        applied_response_count=3,
        rules_count=0,
        mock_count=0,
        provider_count=2,
        cache_count=0,
        fallback_count=1,
        neutralized_response_count=1,
        uncertain_result_count=0,
        safety_neutralized_result_count=0,
    )
    summary.update(changes)
    return summary


@pytest.mark.parametrize(
    ("summary", "accepted"),
    [
        (_v10_summary(), True),
        (_v10_summary(provider_count=3), False),
        (_v10_summary(aggregate_output_token_ceiling=FUNDED * V9_TOKENS_PER_EXPLANATION), False),
        # A ceiling frozen under a different per-request reservation is not this contract.
        (_v10_summary(aggregate_output_token_ceiling=FUNDED * (RESERVED // 2)), False),
        (_v10_summary(funded_request_count=2, budget_skipped_count=1), False),
        (_v10_summary(workbench_input_schema_version=None), False),
        (_v10_summary(cognition_mode="mock", funded_response_limit=OFFLINE), False),
        (
            _v10_summary(
                cognition_mode="rules",
                provider_profile_id=None,
                provider_revision_sha256=None,
                funded_response_limit=OFFLINE,
                aggregate_output_token_ceiling=0,
                rules_count=2,
                mock_count=1,
                provider_count=0,
                fallback_count=0,
            ),
            False,
        ),
        (
            {
                **_library_summary("bounded-library").model_dump(mode="json"),
                "run_schema_version": 9,
                "cognition_mode": "rules",
                "provider_profile_id": None,
                "provider_revision_sha256": None,
                "aggregate_output_token_ceiling": 0,
                "effect_scope": BOUNDED,
            },
            False,
        ),
    ],
    ids=[
        "valid-v10",
        "outcome-count-drift",
        "v9-ceiling",
        "other-reservation-ceiling",
        "unfunded-provider",
        "missing-input-schema",
        "offline-with-profile",
        "rules-with-mock-outcome",
        "v9-with-effect-scope",
    ],
)
def test_v10_library_summaries_are_checked_exactly(
    tmp_path: Path, summary: dict[str, object], accepted: bool
) -> None:
    page_document = _library_page(offset=0, run_id="bounded-library").model_dump(mode="json")
    page_document.update(verified_total=1, scanned_count=1, runs=[summary])
    with _session(tmp_path) as (page, base_url, _root):
        page.route("**/api/runs?offset=0&limit=20", lambda route: _fulfill(route, page_document))
        page.goto(base_url, wait_until="networkidle")
        page.locator("#stage-inspect").click()
        if accepted:
            card = page.locator("#run-library .run-card")
            expect(card).to_contain_text("Bounded · Local model")
            expect(card).to_contain_text("3 selected · 2 model · 1 fallback")
            expect(page.locator("#error-banner")).to_be_hidden()
        else:
            expect(page.locator("#error-banner")).to_contain_text("run summary")
            expect(page.locator("#run-library .run-card")).to_have_count(0)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda receipt: receipt.update(prompt=HOSTILE),
        lambda receipt: receipt.update(profile_id="local-other"),
        lambda receipt: receipt.update(revision_sha256="e" * 64),
        lambda receipt: receipt.update(influence_policy_sha256="8" * 63),
        lambda receipt: receipt.update(reason_code="invalid-result"),
        lambda receipt: receipt.update(effect_scope="explanation-only"),
        lambda receipt: receipt.pop("compatibility_key_sha256"),
    ],
    ids=[
        "extra-hostile-member",
        "cross-profile",
        "cross-revision",
        "short-policy-hash",
        "compatible-with-reason",
        "explanation-scope",
        "missing-key-hash",
    ],
)
def test_hostile_bounded_check_receipts_never_authorize_or_render(
    tmp_path: Path, tamper: Callable[[dict[str, Any]], None]
) -> None:
    receipt = _v2_check("local-lab", LOCAL_REVISION)
    tamper(receipt)
    with _session(tmp_path) as (page, base_url, _root):
        _providers(page, _provider_document())
        page.route(
            "**/api/providers/*/revisions/*/check",
            lambda route: _fulfill(route, copy.deepcopy(receipt)),
        )
        _to_run_step(page, base_url)
        _choose(page, BOUNDED, "local")
        _validate(page)
        page.locator("#provider-check").click()
        expect(page.locator("#provider-check-status")).to_contain_text(
            re.compile("incompatible|inconsistent")
        )
        expect(page.locator("#start-run")).to_be_disabled()
        expect(page.locator("#review-compatibility")).to_have_text("Required before start")
        assert page.locator("img").count() == 0
        assert HOSTILE not in page.content()
        assert json.dumps(receipt) not in page.content()
