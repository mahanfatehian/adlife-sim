"""Server contracts the bounded-cognition workbench controls depend on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from adlife.city.workbench_input import NormalizedWorkbenchCognitionV4, WorkbenchCognitionDraftV4
from adlife.city.workbench_providers import WorkbenchProviderCheckResultV2
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from tests.browser.test_bounded_cognition_workbench_browser import (
    BOUNDARY,
    BOUNDED,
    C1,
    EXPLANATION,
    LIMITS,
    _SignalProvider,
    _v2_check,
    runtime_bounded_capability,
)
from tests.unit.city.test_workbench_input import draft_v3_data

TOKEN = "bounded-controls-token"
ORIGIN = "http://127.0.0.1:8765"
REVISION = "a" * 64
OFFLINE = LIMITS["offline_funded_response_limit"]
CAPABILITY_KEYS = {
    "effect_scope",
    "workbench_schema_version",
    "run_schema_version",
    "modes",
    "funded_response_limits",
    "default_provider_funded_response_limit",
    "offline_funded_response_limit",
    "selected_requests_per_run",
    "reserved_output_tokens_per_request",
    "max_output_tokens_per_attempt",
    "max_http_attempts_per_request",
    "compatibility_check_required_modes",
}


def _client(tmp_path: Path, **options: Any) -> tuple[TestClient, Path]:
    root = tmp_path / "workspace"
    app = create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN, **options)
    return TestClient(app, base_url=ORIGIN), root


def _post(client: TestClient, path: str, payload: dict[str, Any]) -> Any:
    return client.post(
        path,
        headers={"Content-Type": "application/json", "Origin": ORIGIN, "X-AdLife-CSRF": TOKEN},
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _single(model: Any, field: str) -> Any:
    (value,) = get_args(model.model_fields[field].annotation)
    return value


def _browser_draft(scope: str, mode: str, *, limit: int | None = None) -> dict[str, Any]:
    """The exact outer schema and cognition body the workbench serializer emits."""
    provider = mode in {"local", "remote"}
    draft = draft_v3_data(mode=mode, aggregate_output_token_ceiling=92_160 if provider else 0)
    draft["settings"]["seed"] = str(draft["settings"]["seed"])
    if scope == BOUNDED:
        funded = LIMITS["default_provider_funded_response_limit"] if limit is None else limit
        draft["schema_version"] = 4
        draft["cognition"] = {
            "effect_scope": BOUNDED,
            "mode": mode,
            "provider_profile_id": "provider-main" if provider else None,
            "provider_revision_sha256": REVISION if provider else None,
            "funded_response_limit": funded if provider else OFFLINE,
        }
    return draft


def test_bounded_capability_is_the_runtime_contract_the_browser_accepts(tmp_path: Path) -> None:
    client, _root = _client(tmp_path)
    with client:
        capabilities = client.get("/api/workbench").json()["capabilities"]
    # The server publishes the runtime models' own values, never a restatement.
    assert runtime_bounded_capability() == C1["bounded_cognition"]
    assert {name: capabilities[name] for name in C1} == C1

    # Mirror the browser's structural acceptance rules so the real document always passes.
    bounded = capabilities["bounded_cognition"]
    assert set(bounded) == CAPABILITY_KEYS
    numbers = (
        "selected_requests_per_run",
        "reserved_output_tokens_per_request",
        "max_output_tokens_per_attempt",
        "max_http_attempts_per_request",
    )
    assert all(type(bounded[name]) is int and bounded[name] >= 1 for name in numbers)
    assert bounded["max_output_tokens_per_attempt"] <= 4_096
    assert bounded["max_http_attempts_per_request"] <= 16
    assert bounded["selected_requests_per_run"] <= 1_000
    assert bounded["reserved_output_tokens_per_request"] == (
        bounded["max_output_tokens_per_attempt"] * bounded["max_http_attempts_per_request"]
    )
    limits = bounded["funded_response_limits"]
    assert all(type(limit) is int for limit in limits)
    assert limits == sorted(set(limits)) and limits[0] >= 1
    assert limits[-1] <= bounded["selected_requests_per_run"]
    assert bounded["default_provider_funded_response_limit"] in limits
    assert bounded["offline_funded_response_limit"] == bounded["selected_requests_per_run"]
    assert bounded["modes"] == capabilities["cognition_modes"]
    assert bounded["compatibility_check_required_modes"] == ["local", "remote"]
    # Attempts are one initial call plus the frozen transient-retry and repair ceilings.
    frozen = NormalizedWorkbenchCognitionV4
    assert bounded["max_http_attempts_per_request"] == 1 + _single(
        frozen, "transient_retries"
    ) + _single(frozen, "repair_attempts")
    assert _single(WorkbenchCognitionDraftV4, "effect_scope") == bounded["effect_scope"] == BOUNDED


def test_served_workbench_copy_is_clean_utf8(tmp_path: Path) -> None:
    client, _root = _client(tmp_path)
    paths = (
        "/",
        "/assets/workbench.js",
        "/assets/workbench-providers.js",
        "/assets/workbench.css",
        "/assets/workbench-providers.css",
    )
    with client:
        responses = [client.get(path) for path in paths]
    for path, response in zip(paths, responses, strict=True):
        assert response.status_code == 200, path
        assert "charset=utf-8" in response.headers["content-type"].lower(), path
        text = response.content.decode("utf-8")
        # UTF-8 decoded as Windows-1252 and saved again leaves these sequences behind.
        for marker in ("Â", "Ã", "â€", "�"):
            assert marker not in text, (path, marker)


@pytest.mark.parametrize("mode", ["rules", "mock", "local", "remote"])
@pytest.mark.parametrize("scope", [EXPLANATION, BOUNDED])
def test_browser_shaped_drafts_validate_for_every_scope_and_source(
    tmp_path: Path, scope: str, mode: str
) -> None:
    client, root = _client(tmp_path)
    with client:
        response = _post(client, "/api/scenarios/validate", _browser_draft(scope, mode))

    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True
    assert not (root / "city-runs").exists() or not any((root / "city-runs").iterdir())


@pytest.mark.parametrize(
    "smuggled",
    [
        {
            "aggregate_output_token_ceiling": LIMITS["default_provider_funded_response_limit"]
            * LIMITS["reserved_output_tokens_per_request"]
        },
        {"causal_contract_sha256": "b" * 64},
        {"reservation_tokens_per_request": LIMITS["reserved_output_tokens_per_request"]},
        {"max_output_tokens_per_attempt": LIMITS["max_output_tokens_per_attempt"]},
        {"base_url": "http://127.0.0.1:11434/v1"},
        {"credential": "not-a-real-credential"},
        {"effect_scope": EXPLANATION},
        {"funded_response_limit": max(LIMITS["funded_response_limits"]) + 1},
        {"funded_response_limit": str(LIMITS["default_provider_funded_response_limit"])},
        {"funded_response_limit": float(LIMITS["default_provider_funded_response_limit"])},
    ],
    ids=lambda value: next(iter(value)) + ":" + type(next(iter(value.values()))).__name__,
)
def test_schema4_refuses_browser_supplied_authority(
    tmp_path: Path, smuggled: dict[str, object]
) -> None:
    draft = _browser_draft(BOUNDED, "local")
    draft["cognition"].update(smuggled)
    client, root = _client(tmp_path)
    with client:
        response = _post(client, "/api/scenarios/validate", draft)

    assert response.status_code == 422
    assert "not-a-real-credential" not in response.text
    assert not (root / "city-runs").exists() or not any((root / "city-runs").iterdir())


def test_bounded_check_receipt_has_the_exact_shape_the_browser_accepts(tmp_path: Path) -> None:
    profile_payload = {
        "schema_version": 1,
        "profile_id": "local-ollama",
        "display_label": "Local Ollama",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5:7b-instruct",
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": "local-ollama",
    }
    providers: list[_SignalProvider] = []

    def factory(**kwargs: object) -> _SignalProvider:
        providers.append(_SignalProvider(**kwargs))
        return providers[-1]

    client, _root = _client(tmp_path, provider_factory_v2=factory)
    with client:
        profile = _post(client, "/api/providers", profile_payload).json()
        checked = _post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {"effect_scope": BOUNDED},
        )
        listed = client.get("/api/providers").json()

    assert checked.status_code == 200, checked.text
    receipt = checked.json()
    expected = _v2_check("local-ollama", profile["revision_sha256"])
    assert set(receipt) == set(expected) == set(WorkbenchProviderCheckResultV2.model_fields)
    assert receipt["status"] == "compatible" and receipt["effect_scope"] == BOUNDED
    assert (receipt["profile_id"], receipt["revision_sha256"]) == (
        "local-ollama",
        profile["revision_sha256"],
    )
    # The scoped V2 success never masquerades as the profile's V1 explanation check.
    assert listed["profiles"][0]["last_check"] is None
    assert len(providers) == 1 and providers[0].closed


def test_served_workbench_carries_the_exact_boundary_statement(tmp_path: Path) -> None:
    client, _root = _client(tmp_path)
    with client:
        shell = client.get("/").text
        provider_script = client.get("/assets/workbench-providers.js").text

    for control in (
        'id="cognition-effect-scope"',
        'id="cognition-funded-responses"',
        'id="cognition-boundary"',
        'id="bounded-cost-disclosure"',
        'id="review-ownership"',
        'id="job-phase-track"',
        'id="start-run-requirement"',
    ):
        assert shell.count(control) == 1, control
    assert provider_script.count(BOUNDARY) == 1
