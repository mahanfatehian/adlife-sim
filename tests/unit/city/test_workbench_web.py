from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from adlife.city.analysis import (
    metrics_for_stored_city_run,
    response_metrics_for_stored_city_run,
)
from adlife.city.catalog import CityCatalogError, load_city_catalog, select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run
from adlife.city.web import create_city_app
from adlife.city.workbench_catalog import (
    PackagedCityCatalogUnavailable,
    WorkbenchCityRegistryUnavailable,
    WorkbenchCityResolver,
)
from adlife.city.workbench_city_registry import (
    MAX_CITY_PACK_UPLOAD_BYTES,
    CityPackPublicationError,
    CityPackRegistrationConflict,
    CityPackUploadTooLarge,
    InvalidCityPackUpload,
    MalformedCityPackUpload,
)
from adlife.city.workbench_input import WorkbenchRunDraft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city import CityPackV2
from adlife.core.domain.city_cognition_v2 import (
    CITY_COGNITION_V2_MAX_ATTEMPTS_PER_REQUEST,
    CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import UnsafeRunLocation
from tests.unit.city.test_workbench_input import (
    draft_data,
    draft_v2_data,
    draft_v3_data,
    persona_data,
)

TOKEN = "fixed-workbench-web-token"
ORIGIN = "http://127.0.0.1:8765"
UPLOAD_PATH = "/api/catalog/cities/upload"


def _assert_no_live_city_stage_bytes(root: Path) -> None:
    """Allow only the safe POSIX tombstones documented by the registry contract."""
    staging = root / "city-pack-staging"
    paths = list(staging.iterdir()) if staging.exists() else []
    if os.name == "nt":
        assert paths == []
        return
    records = root / "city-packs"
    record_paths = list(records.iterdir()) if records.exists() else []
    for path in paths:
        assert path.is_file() and not path.is_symlink()
        if path.read_bytes() != b"":
            assert any(
                record.is_file() and not record.is_symlink() and os.path.samefile(path, record)
                for record in record_paths
            )


def _module():
    spec = importlib.util.find_spec("adlife.city.workbench_web")
    assert spec is not None, "city workbench web application is not implemented"
    return importlib.import_module("adlife.city.workbench_web")


def _client(
    tmp_path: Path,
    *,
    token: str = TOKEN,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, Path]:
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    app = _module().create_city_workbench_app(workspace, csrf_token=token)
    return (
        TestClient(
            app,
            base_url=ORIGIN,
            raise_server_exceptions=raise_server_exceptions,
        ),
        root,
    )


def _post(client: TestClient, payload: dict[str, Any]):
    return client.post(
        "/api/scenarios/validate",
        headers={
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "X-AdLife-CSRF": TOKEN,
        },
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _secure_post(client: TestClient, path: str, payload: dict[str, Any]):
    return client.post(
        path,
        headers={
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "X-AdLife-CSRF": TOKEN,
        },
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _city_pack_payload(city_id: str = "operator-city") -> dict[str, Any]:
    payload = select_catalog_city("fictional-grid-v2").model_dump(mode="json")
    payload["city_id"] = city_id
    payload["name"] = f"Operator City {city_id}"
    return payload


def _city_pack_document(city_id: str = "operator-city", *, indent: int | None = None) -> bytes:
    return json.dumps(
        _city_pack_payload(city_id),
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
    ).encode("utf-8")


def _secure_upload(
    client: TestClient,
    document: bytes,
    *,
    path: str = UPLOAD_PATH,
    headers: dict[str, str] | None = None,
):
    request_headers = {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-AdLife-CSRF": TOKEN,
    }
    request_headers.update(headers or {})
    return client.post(path, headers=request_headers, content=document)


def _valid_draft(*, phone: bool = True, roadside: bool = False) -> dict[str, Any]:
    payload = draft_data(phone=phone, roadside=roadside)
    payload["settings"]["seed"] = str(payload["settings"]["seed"])
    if roadside:
        payload["scenario"]["roadside"]["road_id"] = "middle-west"
    return payload


def _tiny_draft(*, run_id: str = "browser-run") -> dict[str, Any]:
    payload = _valid_draft()
    payload["settings"].update({"run_id": run_id, "agent_count": 1, "days": 1})
    payload["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    return payload


def _persona_draft(*, run_id: str = "persona-browser-run") -> dict[str, Any]:
    payload = draft_v2_data(agent_count=2)
    payload["settings"].update({"seed": "9223372036854775807", "days": 1, "run_id": run_id})
    payload["population"]["personas"] = [
        persona_data(persona_id="persona-002"),
        persona_data(persona_id="persona-001"),
    ]
    payload["population"]["personas"][0]["traits"]["impulsivity"] = 0.8
    payload["scenario"]["phone"]["active_windows"] = [
        {"day": 1, "start_minute": 480, "end_minute": 540}
    ]
    return payload


def test_persona_templates_endpoint_is_exact_stable_validated_and_query_free(
    tmp_path: Path,
) -> None:
    from adlife.city.workbench_personas import load_persona_template_catalog

    client, root = _client(tmp_path)
    before = _snapshot(root)
    with client:
        response = client.get("/api/persona-templates")
        repeated = client.get("/api/persona-templates")
        refused = client.get("/api/persona-templates?secret=never-reflect-query")
    assert response.status_code == 200
    assert response.json() == load_persona_template_catalog().model_dump(mode="json")
    assert [item["persona_id"] for item in response.json()["templates"]] == [
        "persona-001",
        "persona-002",
        "persona-003",
        "persona-004",
    ]
    assert response.content == repeated.content
    assert refused.status_code == 422
    assert "never-reflect-query" not in refused.text
    assert _snapshot(root) == before


def test_persona_catalog_failure_is_a_constant_value_free_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from adlife.city.workbench_personas import PersonaTemplateCatalogError

    secret = "sk-private-catalog-secret-1234567890123456"

    def broken():
        raise PersonaTemplateCatalogError(f"C:/private/persona_templates.json {secret}")

    monkeypatch.setattr(_module(), "load_persona_template_catalog", broken, raising=False)
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR), client:
        response = client.get("/api/persona-templates")
    assert response.status_code == 500
    assert response.json() == {
        "schema_version": 1,
        "error": {
            "code": "persona-catalog-unavailable",
            "message": "The fictional persona catalog is unavailable.",
            "fields": {},
        },
    }
    assert secret not in response.text + caplog.text
    assert "C:/private" not in response.text + caplog.text


def test_schema2_validation_receipts_and_jobs_project_one_verified_v8_snapshot(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    payload = _persona_draft()
    with client:
        first = _post(client, payload)
        payload["population"]["personas"].reverse()
        repeated = _post(client, payload)
        assert first.status_code == repeated.status_code == 200
        assert first.content == repeated.content
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        accepted = submitted.json()["job"]
        assert accepted["accepted_settings"]["run_schema_version"] == 8
        assert accepted["accepted_settings"]["workbench_input_schema_version"] == 2
        terminal = _await_job(client, submitted.json()["status_url"])
        assert terminal["phase"] == "completed"
        assert terminal["result"]["run_schema_version"] == 8
        listing = client.get("/api/runs")
        detail = client.get("/api/runs/persona-browser-run/meta")
        frozen = client.get("/api/runs/persona-browser-run/workbench-input")
        shell = client.get("/runs/persona-browser-run")
        assert (
            listing.status_code
            == detail.status_code
            == frozen.status_code
            == shell.status_code
            == 200
        )
        assert listing.json()["runs"][0] == terminal["result"]
        assert detail.json()["workbench_input_available"] is True
        assert detail.json()["workbench_input_sha256"] == first.json()["hashes"]["input_sha256"]
        document = frozen.json()
        assert document["workbench_input_sha256"] == first.json()["hashes"]["input_sha256"]
        assert document["settings"]["seed"] == "9223372036854775807"
        assert document["population"]["assignments"] == [
            {"agent_id": "person-001", "persona_id": "persona-001"},
            {"agent_id": "person-002", "persona_id": "persona-002"},
        ]
        view_token = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">', shell.text
        )
        assert view_token is not None
        sidecar = root / "city-runs" / "persona-browser-run" / "inputs" / "workbench.json"
        sidecar.write_bytes(sidecar.read_bytes() + b" ")
        pinned = client.get(
            "/api/runs/persona-browser-run/workbench-input",
            headers={"X-AdLife-View-Token": view_token.group(1)},
        )
        assert pinned.status_code == 200
        assert pinned.content == frozen.content
        assert client.get("/runs/persona-browser-run").status_code == 409
        assert client.get("/api/runs/persona-browser-run/workbench-input").status_code == 409


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("fictional", False),
        ("fictional", "true"),
        ("fictional", 1),
        ("persona_id", "persona-000"),
        ("count", True),
        ("count", 0),
        ("display_name", ""),
        ("age", True),
        ("occupation", 123),
        ("mbti", "WRONG"),
        ("background", "😀" * 700),
    ],
)
def test_persona_diagnostics_are_adjacent_value_free_and_pre_reservation(
    tmp_path: Path,
    path: str,
    field: str,
    value: object,
) -> None:
    client, root = _client(tmp_path)
    payload = _persona_draft()
    payload["population"]["personas"][0][field] = value
    before = _snapshot(root)
    with client:
        response = _secure_post(client, path, payload)
        assert response.status_code == 422
        expected_path = (
            "population.personas.0" if field == "background" else f"population.personas.0.{field}"
        )
        assert response.json()["error"]["fields"] == {expected_path: "This field is invalid."}
        assert client.get("/api/workbench").json()["active_job"] is None
    assert _snapshot(root) == before


def _await_job(client: TestClient, status_url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        job = response.json()
        if job["phase"] in {"completed", "failed", "cancelled"}:
            return job
        pause.wait(0.005)
    raise AssertionError(f"job at {status_url} did not terminate")


def _snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((relative, "file", digest))
        else:
            entries.append((relative, "directory", ""))
    return tuple(entries)


def _artifact_snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Exclude the registry's advisory coordination file from artifact assertions."""
    return tuple(entry for entry in _snapshot(root) if entry[0] != ".city-pack-registry.lock")


def _saved_workbench_run(
    root: Path,
    *,
    run_id: str,
    schema_version: int,
    seed: int = 42,
):
    payload = _tiny_draft(run_id=run_id)
    payload["settings"]["seed"] = seed
    draft = WorkbenchRunDraft.model_validate_json(json.dumps(payload, separators=(",", ":")))
    validated = construct_workbench_run(draft)
    settings = validated.workbench_input.draft.settings
    return create_city_run(
        validated.pack,
        root=root,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input if schema_version == 7 else None,
    )


def _legacy_saved_run_app(stored):
    return create_city_app(
        stored.mobility,
        run_id=stored.manifest.run_id,
        run_schema_version=stored.manifest.schema_version,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        spatial_metrics=metrics_for_stored_city_run(stored),
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        spatial_response_metrics=response_metrics_for_stored_city_run(stored),
    )


def test_workbench_capabilities_are_versioned_bounded_and_honest(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get("/api/workbench")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "phase": "bounded-jobs",
        "capabilities": {
            "scenario_validation": True,
            "jobs": True,
            "run_execution": True,
            "run_inspection": True,
            "provider_configuration": True,
            "oauth": False,
            "cognition_modes": ["rules", "mock", "local", "remote"],
            "response_modes": ["deterministic-rules"],
            "cognition_effect_scopes": ["explanation-only", "bounded-signals-v1"],
            "bounded_cognition": {
                "effect_scope": "bounded-signals-v1",
                "workbench_schema_version": 4,
                "run_schema_version": 10,
                "modes": ["rules", "mock", "local", "remote"],
                "funded_response_limits": [10, 20, 40, 60],
                "default_provider_funded_response_limit": 20,
                "offline_funded_response_limit": 60,
                "selected_requests_per_run": 60,
                # The frozen output budget is a runtime constant; never pin its value here.
                "reserved_output_tokens_per_request": (
                    CITY_COGNITION_V2_RESERVATION_TOKENS_PER_REQUEST
                ),
                "max_output_tokens_per_attempt": CITY_COGNITION_V2_OUTPUT_TOKENS_PER_ATTEMPT,
                "max_http_attempts_per_request": CITY_COGNITION_V2_MAX_ATTEMPTS_PER_REQUEST,
                "compatibility_check_required_modes": ["local", "remote"],
            },
        },
        "active_job": None,
        "limits": {
            "request_body_bytes": 131072,
            "agent_count": {"minimum": 1, "maximum": 30},
            "days": {"minimum": 1, "maximum": 7},
            "seed": {"minimum": "0", "maximum": "9223372036854775807"},
            "placements": ["mobile-feed", "roadside-billboard"],
        },
        "disclosure": {
            "population": "Synthetic cohort assumptions; not real residents.",
            "city": (
                "City-pack geometry; no live map or traffic feed. "
                "Uploaded sources are operator-declared and unreviewed."
            ),
            "outcomes": "Modeled responses are not observed behavior, sales, or forecasts.",
        },
    }
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_bounded_capabilities_follow_runtime_models_and_expose_no_contract_authority(
    tmp_path: Path,
) -> None:
    from typing import get_args

    from adlife.city.workbench_input import (
        NormalizedWorkbenchCognitionV4,
        WorkbenchCognitionDraftV4,
    )

    client, _ = _client(tmp_path)
    with client:
        bounded = client.get("/api/workbench").json()["capabilities"]["bounded_cognition"]

    assert bounded["modes"] == list(
        get_args(WorkbenchCognitionDraftV4.model_fields["mode"].annotation)
    )
    assert bounded["funded_response_limits"] == list(
        get_args(WorkbenchCognitionDraftV4.model_fields["funded_response_limit"].annotation)
    )
    provider = WorkbenchCognitionDraftV4.model_validate(
        {
            "effect_scope": "bounded-signals-v1",
            "mode": "remote",
            "provider_profile_id": "any-profile",
            "provider_revision_sha256": "a" * 64,
        }
    )
    assert bounded["default_provider_funded_response_limit"] == provider.funded_response_limit
    fields = NormalizedWorkbenchCognitionV4.model_fields
    assert (
        bounded["reserved_output_tokens_per_request"]
        == get_args(fields["reservation_tokens_per_request"].annotation)[0]
    )
    assert (
        bounded["max_output_tokens_per_attempt"]
        == get_args(fields["max_output_tokens_per_attempt"].annotation)[0]
    )
    # A whole funded request reserves its worst case: every attempt at the per-attempt cap.
    assert (
        bounded["reserved_output_tokens_per_request"]
        == bounded["max_http_attempts_per_request"] * bounded["max_output_tokens_per_attempt"]
    )
    assert bounded["max_http_attempts_per_request"] == sum(
        get_args(fields[name].annotation)[0]
        for name in ("initial_attempts", "transient_retries", "repair_attempts")
    )
    # Browser options only: no policy hash, ceiling, or contract document to echo back.
    encoded = json.dumps(bounded)
    assert re.search(r"[0-9a-f]{64}", encoded) is None
    assert not {
        "aggregate_output_token_ceiling",
        "causal_contract",
        "prompt_contract_sha256",
    } & set(bounded)


def _local_provider_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile_id": "local-ollama",
        "display_label": "Local Ollama",
        "mode": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_id": "qwen2.5:7b",
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


def test_app_injects_one_process_local_compatibility_registry(tmp_path, monkeypatch):
    module = _module()
    repositories = []
    repository_arguments = []
    original = module.WorkbenchRunRepository

    def v2_factory(**_kwargs):
        raise AssertionError("identity test must not construct a provider")

    def repository(*args, **kwargs):
        repository_arguments.append(kwargs)
        instance = original(*args, **kwargs)
        repositories.append(instance)
        return instance

    monkeypatch.setattr(module, "WorkbenchRunRepository", repository)
    first = module.create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "first"),
        provider_factory_v2=v2_factory,
    )
    second = module.create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "second"),
        provider_factory_v2=v2_factory,
    )
    assert (
        first.state.provider_controller.compatibility_registry
        is repositories[0].compatibility_registry
    )
    assert (
        second.state.provider_controller.compatibility_registry
        is repositories[1].compatibility_registry
    )
    assert (
        first.state.provider_controller.compatibility_registry
        is not second.state.provider_controller.compatibility_registry
    )
    assert first.state.provider_controller._factory_v2 is v2_factory
    assert second.state.provider_controller._factory_v2 is v2_factory
    assert repository_arguments[0]["provider_factory_v2"] is v2_factory
    assert repository_arguments[1]["provider_factory_v2"] is v2_factory


@pytest.mark.parametrize(
    "payload,scope",
    [
        ({}, "explanation-only"),
        ({"effect_scope": "explanation-only"}, "explanation-only"),
        ({"effect_scope": "bounded-signals-v1"}, "bounded-signals-v1"),
    ],
)
def test_compatibility_route_dispatches_exact_effect_scope(tmp_path, payload, scope):
    from tests.unit.city.test_workbench_providers import _Provider, _ProviderV2

    counts = [0, 0]

    def v1(**kwargs):
        counts[0] += 1
        return _Provider(**kwargs)

    def v2(**kwargs):
        counts[1] += 1
        return _ProviderV2(**kwargs)

    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"),
        csrf_token=TOKEN,
        provider_factory=v1,
        provider_factory_v2=v2,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        response = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            payload,
        )
        listed = client.get("/api/providers").json()["profiles"][0]
    assert response.status_code == 200
    assert counts == ([1, 0] if scope == "explanation-only" else [0, 1])
    if scope == "explanation-only":
        assert response.json() == {"status": "compatible", "reason_code": None}
        assert listed["last_check"] == response.json()
    else:
        assert response.json()["effect_scope"] == scope
        assert response.json()["status"] == "compatible"
        assert listed["last_check"] is None


def test_reuse_page_serves_the_shell_with_only_a_portable_run_id(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    before = _artifact_snapshot(root)
    with client:
        plain = client.get("/")
        reuse = client.get("/runs/demo-v10-001/reuse")
        queried = client.get("/runs/demo-v10-001/reuse?again=1")
        malformed = [client.get(f"/runs/{value}/reuse") for value in ("Demo_V10", "a" * 41, "-x")]

    marker = '<meta name="adlife-reuse-run" content="{}">'
    assert plain.status_code == reuse.status_code == 200
    assert marker.format("") in plain.text
    assert marker.format("demo-v10-001") in reuse.text
    # Apart from the requested run ID, the reuse page is the ordinary workbench shell.
    assert reuse.text.replace(marker.format("demo-v10-001"), marker.format("")) == plain.text
    assert reuse.headers["content-security-policy"] == plain.headers["content-security-policy"]
    assert "__ADLIFE_" not in plain.text + reuse.text
    assert queried.status_code == 422
    assert [response.status_code for response in malformed] == [404, 404, 404]
    assert all("Demo_V10" not in response.text for response in malformed)
    # Opening the page reads nothing and writes nothing; the browser verifies the run later.
    assert _artifact_snapshot(root) == before


def test_run_settings_name_the_rule_engine_as_owner_of_every_baseline_response(
    tmp_path: Path,
) -> None:
    """The fixed rule-engine field must not read as a rival to the model source below it."""
    client, _root = _client(tmp_path)
    with client:
        page = client.get("/")

    assert page.status_code == 200
    html = page.text
    assert '<label for="response-mode">Baseline responses</label>' in html
    assert 'id="response-mode" type="text" value="Rules engine (offline)" readonly' in html
    assert ">Response mode<" not in html
    help_text = re.search(r'<small id="response-mode-help"[^>]*>([^<]*)</small>', html)
    assert help_text is not None
    assert help_text.group(1) == (
        "The rule engine computes movement, ad exposure, attention, and every baseline response "
        "offline. Model assistance below never replaces them: it can only explain saved results "
        "or supply bounded signals for a separate cognition track."
    )


def test_recorded_bounded_check_is_read_only_exact_and_scope_bound(tmp_path: Path) -> None:
    """A reopened page may read, never run, the process-local bounded-signal check."""
    from tests.unit.city.test_workbench_providers import _Provider, _ProviderV2

    calls = {"v1": 0, "v2": 0}

    def v1(**kwargs):
        calls["v1"] += 1
        return _Provider(**kwargs)

    def v2(**kwargs):
        calls["v2"] += 1
        return _ProviderV2(**kwargs)

    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = _module().create_city_workbench_app(
        workspace,
        csrf_token=TOKEN,
        provider_factory=v1,
        provider_factory_v2=v2,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        path = f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/bounded-check"
        before = client.get(path)
        explained = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {},
        )
        after_explanation = client.get(path)
        checked = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {"effect_scope": "bounded-signals-v1"},
        )
        recorded = client.get(path)
        again = client.get(path)
        queried = client.get(f"{path}?refresh=1")
        unknown = client.get(f"/api/providers/local-ollama/revisions/{'e' * 64}/bounded-check")
        posted = _secure_post(client, path, {})

    assert before.status_code == 404
    assert before.json()["error"]["code"] == "provider-check-not-recorded"
    # A V1 explanation success never stands in for the bounded-signal check.
    assert explained.status_code == 200
    assert after_explanation.status_code == 404
    assert checked.status_code == 200
    assert recorded.status_code == again.status_code == 200
    assert recorded.json() == checked.json() == again.json()
    assert recorded.json()["effect_scope"] == "bounded-signals-v1"
    assert recorded.json()["status"] == "compatible"
    assert recorded.headers["cache-control"] == "no-store"
    # Reading never contacts a provider: exactly one V1 and one V2 check ran.
    assert calls == {"v1": 1, "v2": 1}
    assert queried.status_code == 422
    assert unknown.status_code == 404
    assert posted.status_code in {404, 405}
    exposed = recorded.text + before.text
    assert str(workspace.root) not in exposed
    assert "11434" not in exposed


def test_provider_routes_create_list_and_run_explicit_compatibility_check(
    tmp_path: Path,
) -> None:
    from adlife.core.domain.city_cognition import CityProviderAttemptV1
    from adlife.core.simulation.city_cognition import build_city_mock_result

    providers: list[object] = []

    class CompatibleProvider:
        async def call(self, request: Any) -> CityProviderAttemptV1:
            return CityProviderAttemptV1(
                schema_version=1,
                status="valid",
                result=build_city_mock_result(request),
            )

        async def aclose(self) -> None:
            return None

    def factory(**kwargs: object) -> CompatibleProvider:
        providers.append(kwargs)
        return CompatibleProvider()

    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = _module().create_city_workbench_app(
        workspace,
        csrf_token=TOKEN,
        provider_factory=factory,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        empty = client.get("/api/providers")
        created = _secure_post(client, "/api/providers", _local_provider_payload())
        revision = created.json()["revision_sha256"]
        listed = client.get("/api/providers")
        checked = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{revision}/check",
            {},
        )

    assert empty.status_code == 200
    assert empty.json()["profiles"] == []
    assert empty.json()["oauth"] is False
    assert empty.json()["cognition_modes"] == ["rules", "mock", "local", "remote"]
    assert created.status_code == 201
    assert created.json()["credential_available"] is True
    assert listed.status_code == 200
    assert listed.json()["profiles"] == [created.json()]
    assert checked.status_code == 200
    assert checked.json() == {"status": "compatible", "reason_code": None}
    assert len(providers) == 1
    exposed = empty.text + created.text + listed.text + checked.text
    assert str(workspace.root) not in exposed
    assert 'credential"' not in exposed


def test_provider_credential_route_stores_once_without_echoing_secret(tmp_path: Path) -> None:
    from adlife.adapters.cognition.city_credentials import (
        ProviderCredentialUnavailable,
        ResolvedCredential,
        SecretBuffer,
    )
    from adlife.city.provider_runtime import build_provider_runtime_policy

    secret = "sk-test-browser-credential-1234567890"
    stored: dict[str, str] = {}

    class MemoryCredentialStore:
        def available(self, profile: Any) -> bool:
            return profile.revision_sha256 in stored

        def store(self, profile: Any, credential: SecretBuffer) -> None:
            stored[profile.revision_sha256] = credential.reveal_text()

        def resolve(self, profile: Any) -> ResolvedCredential:
            value = stored.get(profile.revision_sha256)
            if value is None:
                raise ProviderCredentialUnavailable("unavailable")
            return ResolvedCredential(
                source="keyring",
                secret=SecretBuffer(value),
                origin_approval_id=profile.origin_approval_id,
                credential_slot_id=profile.credential_slot_id,
            )

    runtime = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",)
    )
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = _module().create_city_workbench_app(
        workspace,
        csrf_token=TOKEN,
        provider_runtime=runtime,
        credential_store=MemoryCredentialStore(),
    )
    profile = {
        **_local_provider_payload(),
        "profile_id": "remote-acme",
        "display_label": "Remote Acme",
        "mode": "remote",
        "base_url": "https://api.example.invalid/v1",
        "origin_approval_id": "remote-acme",
    }
    with TestClient(app, base_url=ORIGIN) as client:
        created = _secure_post(client, "/api/providers", profile)
        revision = created.json()["revision_sha256"]
        response = _secure_post(
            client,
            f"/api/providers/remote-acme/revisions/{revision}/credential",
            {"credential": secret},
        )
        listed = client.get("/api/providers")

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.json()["credential_available"] is True
    assert listed.json()["profiles"][0]["credential_available"] is True
    assert stored[revision] == secret
    assert secret not in response.text + listed.text


def test_provider_credential_route_screens_startup_policy_drift(tmp_path: Path) -> None:
    from adlife.city.provider_runtime import build_provider_runtime_policy

    secret = "not-a-real-credential"
    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    approved = build_provider_runtime_policy(
        provider_origins=("remote-acme=https://api.example.invalid/v1",)
    )
    first_app = _module().create_city_workbench_app(
        workspace,
        csrf_token=TOKEN,
        provider_runtime=approved,
    )
    profile = {
        **_local_provider_payload(),
        "profile_id": "remote-acme",
        "display_label": "Remote Acme",
        "mode": "remote",
        "base_url": "https://api.example.invalid/v1",
        "origin_approval_id": "remote-acme",
    }
    with TestClient(first_app, base_url=ORIGIN) as client:
        created = _secure_post(client, "/api/providers", profile)
    assert created.status_code == 201

    restarted = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    revision = created.json()["revision_sha256"]
    with TestClient(restarted, base_url=ORIGIN) as client:
        response = _secure_post(
            client,
            f"/api/providers/remote-acme/revisions/{revision}/credential",
            {"credential": secret},
        )
        listed = client.get("/api/providers")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "provider-endpoint-refused"
    assert secret not in response.text
    assert listed.json()["profiles"][0]["endpoint_policy_accepted"] is False


@pytest.mark.parametrize("schema_version", [6, 7])
def test_run_scoped_reads_match_the_legacy_verified_view_contract(
    tmp_path: Path,
    schema_version: int,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = f"parity-v{schema_version}"
    stored = _saved_workbench_run(root, run_id=run_id, schema_version=schema_version)
    before = _snapshot(root)
    workbench = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    legacy = _legacy_saved_run_app(stored)
    suffixes = (
        "meta",
        "city",
        "agents",
        "places",
        "place-assignments",
        "opportunity-summary",
        "opportunities?minute=480&limit=1",
        "attention-summary",
        "spatial-metrics",
        "spatial-response-metrics",
        "attention-events?minute=480&limit=1",
        "response-summary",
        "response-events?minute=480&limit=1",
        "response-state?agent_id=person-001",
        "frame?minute=480&agent_id=person-001",
        (
            "timeline?minute=480&agent_id=person-001&opportunity_offset=0"
            "&attention_offset=0&response_offset=0&response_state_offset=0&limit=1"
        ),
    )
    refusal_suffixes = (
        "opportunities?minute=1440",
        "attention-events?minute=480&agent_id=person-999",
        "response-events?minute=-1",
        "response-state?limit=101",
        "frame?minute=1440&agent_id=person-001",
    )

    with (
        TestClient(workbench, base_url=ORIGIN) as scoped_client,
        TestClient(legacy, base_url=ORIGIN) as legacy_client,
    ):
        for suffix in (*suffixes, *refusal_suffixes):
            legacy_response = legacy_client.get(f"/api/{suffix}")
            scoped_response = scoped_client.get(f"/api/runs/{run_id}/{suffix}")
            assert scoped_response.status_code == legacy_response.status_code, suffix
            expected = legacy_response.json()
            if schema_version == 7 and suffix == "meta":
                assert stored.workbench_input is not None
                expected = {
                    **expected,
                    "workbench_input_available": True,
                    "workbench_input_sha256": stored.workbench_input.fingerprint,
                }
            assert scoped_response.json() == expected, suffix
            assert (
                scoped_response.headers["content-type"] == legacy_response.headers["content-type"]
            )
            assert (
                scoped_response.headers["cache-control"]
                == legacy_response.headers["cache-control"]
                == "no-store"
            )
            assert (
                scoped_response.headers["x-content-type-options"]
                == legacy_response.headers["x-content-type-options"]
                == "nosniff"
            )

    assert _snapshot(root) == before


def test_scoped_timeline_projection_verifies_the_run_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="single-load-timeline", schema_version=7)
    module = _module()
    original = module.WorkbenchRunRepository.load
    loaded: list[str] = []

    def counted_load(repository: object, run_id: str):
        loaded.append(run_id)
        return original(repository, run_id)

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(
            "/api/runs/single-load-timeline/timeline",
            params={"minute": 480, "agent_id": "person-001", "limit": 1},
        )

    assert response.status_code == 200
    assert loaded == ["single-load-timeline"]
    assert set(response.json()) == {
        "schema_version",
        "minute",
        "agent_id",
        "frame",
        "opportunity_page",
        "attention_page",
        "response_page",
        "response_state_page",
    }


def test_inspector_snapshot_reuses_one_verified_run_across_browser_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "snapshot-view"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    module = _module()
    original = module.WorkbenchRunRepository.load
    loaded: list[str] = []

    def counted_load(repository: object, candidate: str):
        loaded.append(candidate)
        return original(repository, candidate)

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(f"/runs/{run_id}")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        headers = {"X-AdLife-View-Token": token_match.group(1)}
        responses = (
            client.get(f"/api/runs/{run_id}/meta", headers=headers),
            client.get(f"/api/runs/{run_id}/city", headers=headers),
            client.get(f"/api/runs/{run_id}/agents", headers=headers),
            client.get(f"/api/runs/{run_id}/workbench-input", headers=headers),
            client.get(
                f"/api/runs/{run_id}/timeline",
                headers=headers,
                params={"minute": 480, "agent_id": "person-001", "limit": 1},
            ),
        )

    assert page.status_code == 200
    assert all(response.status_code == 200 for response in responses)
    assert loaded == [run_id]


def test_inspector_snapshot_token_is_bound_to_one_run_and_invalid_tokens_are_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="snapshot-a", schema_version=7)
    _saved_workbench_run(root, run_id="snapshot-b", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/runs/snapshot-a")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        token = token_match.group(1)
        wrong_run = client.get(
            "/api/runs/snapshot-b/meta",
            headers={"X-AdLife-View-Token": token},
        )
        malformed = client.get(
            "/api/runs/snapshot-a/meta",
            headers={"X-AdLife-View-Token": "not-a-valid-token"},
        )
        unknown = client.get(
            "/api/runs/snapshot-a/meta",
            headers={"X-AdLife-View-Token": "A" * 43},
        )

    expected = {
        "code": "run-unavailable",
        "message": "The saved run could not be verified.",
        "fields": {},
    }
    assert wrong_run.status_code == malformed.status_code == unknown.status_code == 409
    assert wrong_run.json()["error"] == malformed.json()["error"] == unknown.json()["error"]
    assert unknown.json()["error"] == expected
    assert token not in wrong_run.text + malformed.text + unknown.text


def test_repeated_inspector_pages_share_one_verified_snapshot_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "shared-snapshot"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    module = _module()
    original_load = module.WorkbenchRunRepository.load
    loaded: list[str] = []
    resolved_snapshot_ids: list[int] = []

    def counted_load(repository: object, candidate: str):
        loaded.append(candidate)
        return original_load(repository, candidate)

    original_resolve = module._InspectorSnapshots.resolve

    def observed_resolve(cache: object, token: str, *, run_id: str):
        snapshot = original_resolve(cache, token, run_id=run_id)
        if snapshot is not None:
            resolved_snapshot_ids.append(id(snapshot))
        return snapshot

    monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
    monkeypatch.setattr(module._InspectorSnapshots, "resolve", observed_resolve)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        pages = (client.get(f"/runs/{run_id}"), client.get(f"/runs/{run_id}"))
        tokens = []
        for page in pages:
            token_match = re.search(
                r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
                page.text,
            )
            assert token_match is not None
            tokens.append(token_match.group(1))
        responses = tuple(
            client.get(
                f"/api/runs/{run_id}/meta",
                headers={"X-AdLife-View-Token": token},
            )
            for token in tokens
        )

    assert all(page.status_code == 200 for page in pages)
    assert all(response.status_code == 200 for response in responses)
    assert loaded == [run_id, run_id]
    assert len(tokens) == len(set(tokens)) == 2
    assert len(set(resolved_snapshot_ids)) == 1


def test_open_inspector_snapshot_stays_atomic_and_reload_reverifies_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    run_id = "atomic-snapshot"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get(f"/runs/{run_id}")
        token_match = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">',
            page.text,
        )
        assert token_match is not None
        headers = {"X-AdLife-View-Token": token_match.group(1)}
        before = client.get(f"/api/runs/{run_id}/meta", headers=headers)
        manifest = root / "city-runs" / run_id / "run.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        same_page = client.get(f"/api/runs/{run_id}/meta", headers=headers)
        direct = client.get(f"/api/runs/{run_id}/meta")
        reload = client.get(f"/runs/{run_id}")

    assert page.status_code == before.status_code == same_page.status_code == 200
    assert same_page.json() == before.json()
    assert direct.status_code == reload.status_code == 409
    assert (
        direct.json()["error"]
        == reload.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )


@pytest.mark.parametrize("route", ["inspector", "library"])
def test_verified_runs_stay_available_while_the_city_registry_anchors_the_root(
    tmp_path: Path, route: str
) -> None:
    """A run read overlapping a city-registry operation waits instead of failing.

    Regression: on Windows the city registry anchors the workspace root with DELETE
    access during, for example, GET /api/cities, while every run read pins the same
    directory as a shared read lease. A run load that began inside that short critical
    section hit a sharing violation, so a verified run was reported unavailable.
    """
    from adlife.city import workbench_city_registry as registry

    client, root = _client(tmp_path)
    run_id = "registry-overlap"
    _saved_workbench_run(root, run_id=run_id, schema_version=7)
    anchored = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def anchor_root() -> None:
        try:
            with registry._hold_workspace_root(prepare_workbench_workspace(root)):
                anchored.set()
                release.wait(timeout=30)
        except BaseException as error:  # surfaced by the assertion below
            failures.append(error)
        finally:
            anchored.set()

    holder = threading.Thread(target=anchor_root, daemon=True)
    with client:
        holder.start()
        assert anchored.wait(timeout=30)
        timer = threading.Timer(0.3, release.set)
        timer.start()
        try:
            response = client.get(f"/runs/{run_id}" if route == "inspector" else "/api/runs")
        finally:
            release.set()
            timer.cancel()
            holder.join(timeout=30)

    assert failures == []
    assert response.status_code == 200
    if route == "inspector":
        assert re.search(
            r'<meta name="adlife-view-token" content="[A-Za-z0-9_-]{43}">', response.text
        )
    else:
        page = response.json()
        assert (page["verified_total"], page["corrupt_count"]) == (1, 0)
        assert [run["run_id"] for run in page["runs"]] == [run_id]


def test_completed_inspector_url_serves_the_existing_dashboard_with_a_scoped_api_base(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="inspectable-run", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/runs/inspectable-run")
        script = client.get("/static/app.js")
        style = client.get("/static/app.css")
        listing = client.get("/api/runs")

    assert page.status_code == script.status_code == style.status_code == 200
    assert page.text.count('name="adlife-api-base"') == 1
    assert 'name="adlife-api-base" content="/api/runs/inspectable-run"' in page.text
    assert page.text.count('name="adlife-view-token"') == 1
    assert re.search(
        r'<meta name="adlife-view-token" content="[A-Za-z0-9_-]{43}">',
        page.text,
    )
    assert 'src="/static/app.js"' in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert page.headers["cache-control"] == "no-store"
    assert script.headers["content-type"].startswith("text/javascript")
    assert style.headers["content-type"].startswith("text/css")
    assert listing.json()["runs"][0]["inspector_url"] == "/runs/inspectable-run"
    assert stored.manifest.run_id in page.text or "saved-run-label" in page.text


def test_v7_workbench_input_endpoint_returns_only_the_verified_frozen_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(
        root,
        run_id="maximum-input-view",
        schema_version=7,
        seed=2**63 - 1,
    )
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/runs/maximum-input-view/workbench-input")
        metadata = client.get("/api/runs/maximum-input-view/meta")
        refused_query = client.get(
            "/api/runs/maximum-input-view/workbench-input",
            params={"unexpected": "sk-never-echo-this-value-1234567890"},
        )
        refused_path = client.get("/api/runs/maximum-input-view/workbench-input/private")

    assert response.status_code == metadata.status_code == 200
    document = response.json()
    assert set(document) == {
        "run_id",
        "run_schema_version",
        "workbench_input_schema_version",
        "manifest_sha256",
        "workbench_input_sha256",
        "city_sha256",
        "scenario_sha256",
        "creative_sha256",
        "scenario",
        "cohort",
        "settings",
        "creative_template",
    }
    assert document["settings"]["seed"] == "9223372036854775807"
    assert (
        document["manifest_sha256"]
        == hashlib.sha256((stored.directory / "run.json").read_bytes()).hexdigest()
    )
    assert stored.workbench_input is not None
    assert document["workbench_input_sha256"] == stored.workbench_input.fingerprint
    assert metadata.json()["workbench_input_available"] is True
    assert metadata.json()["workbench_input_sha256"] == stored.workbench_input.fingerprint
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["permissions-policy"] == ("camera=(), microphone=(), geolocation=()")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "directory" not in response.text
    assert str(stored.directory) not in response.text
    assert refused_query.status_code == 422
    assert refused_query.json()["error"]["code"] == "invalid-query"
    assert "sk-never" not in refused_query.text
    assert refused_path.status_code == 404


def _save_legacy_run(root: Path, schema_version: int) -> str:
    from tests.integration.test_city_run_store import spatial_specimen
    from tests.unit.city.test_city_mobility import mobility_place_set
    from tests.unit.city.test_city_pack import (
        load_pack,
        load_pack_v2,
        pack_data,
        pack_v2_data,
    )

    run_id = f"legacy-input-v{schema_version}"
    if schema_version == 1:
        create_city_run(
            load_pack(pack_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
        )
    elif schema_version == 2:
        create_city_run(
            load_pack_v2(pack_v2_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
        )
    elif schema_version == 3:
        create_city_run(
            load_pack_v2(pack_v2_data()),
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
            places=mobility_place_set(),
        )
    elif schema_version == 4:
        manifest, mobility, scenario, opportunities = spatial_specimen()
        manifest = manifest.model_copy(update={"run_id": run_id})
        CityRunStore(root).save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
        )
    elif schema_version == 5:
        payload = _tiny_draft(run_id=run_id)
        payload["settings"]["seed"] = 42
        validated = construct_workbench_run(
            WorkbenchRunDraft.model_validate_json(json.dumps(payload, separators=(",", ":")))
        )
        create_city_run(
            validated.pack,
            root=root,
            run_id=run_id,
            seed=42,
            agent_count=1,
            days=1,
            spatial_scenario=validated.scenario,
        )
    else:
        _saved_workbench_run(root, run_id=run_id, schema_version=6)
    return run_id


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4, 5, 6])
def test_verified_legacy_runs_report_workbench_input_as_unavailable_without_claiming_it(
    tmp_path: Path,
    schema_version: int,
) -> None:
    root = tmp_path / f"workspace-v{schema_version}"
    prepare_workbench_workspace(root)
    run_id = _save_legacy_run(root, schema_version)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(f"/api/runs/{run_id}/workbench-input")
        metadata = client.get(f"/api/runs/{run_id}/meta")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "workbench-input-unavailable",
        "message": "This saved run has no browser workbench input.",
        "fields": {},
    }
    assert metadata.status_code == 200
    assert metadata.json()["run_schema_version"] == schema_version
    assert "workbench_input_available" not in metadata.json()
    assert "workbench_input_sha256" not in metadata.json()


def test_workbench_input_endpoint_reverifies_and_refuses_sidecar_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="input-tamper", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/input-tamper/workbench-input")
        sidecar = stored.directory / "inputs" / "workbench.json"
        sidecar.write_bytes(sidecar.read_bytes() + b"\n")
        second = client.get("/api/runs/input-tamper/workbench-input")

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == {
        "code": "run-unavailable",
        "message": "The saved run could not be verified.",
        "fields": {},
    }
    assert str(sidecar) not in second.text


@pytest.mark.parametrize(
    "run_id",
    ["missing-run", "CON", "sk-live-abcdefghij", "x" * 41],
)
def test_direct_inspection_screens_invalid_or_unknown_run_identifiers(
    tmp_path: Path,
    run_id: str,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        api = client.get(f"/api/runs/{run_id}/meta")
        workbench_input = client.get(f"/api/runs/{run_id}/workbench-input")
        page = client.get(f"/runs/{run_id}")

    assert api.status_code == workbench_input.status_code == page.status_code == 404
    assert (
        api.json()["error"]["code"]
        == workbench_input.json()["error"]["code"]
        == page.json()["error"]["code"]
        == "not-found"
    )
    assert run_id not in api.text
    assert run_id not in workbench_input.text
    assert run_id not in page.text


def test_run_scoped_reads_reverify_after_a_prior_success_and_refuse_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="tamper-run", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/tamper-run/meta")
        first_timeline = client.get("/api/runs/tamper-run/timeline?minute=480&agent_id=person-001")
        manifest = root / "city-runs" / "tamper-run" / "run.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        second = client.get("/api/runs/tamper-run/meta")
        second_timeline = client.get("/api/runs/tamper-run/timeline?minute=480&agent_id=person-001")
        page = client.get("/runs/tamper-run")

    assert first.status_code == first_timeline.status_code == 200
    assert second.status_code == second_timeline.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == second_timeline.json()["error"]
        == page.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )
    assert str(manifest) not in second.text + page.text


def test_run_scoped_reads_refuse_a_workspace_replaced_between_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="workspace-swap", schema_version=7)
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/workspace-swap/meta")

        runs_module = importlib.import_module("adlife.city.workbench_runs")

        def replaced(_workspace):
            raise runs_module.UnsafeWorkbenchWorkspace("replaced")

        monkeypatch.setattr(runs_module, "verify_workbench_workspace", replaced)
        second = client.get("/api/runs/workspace-swap/meta")
        workbench_input = client.get("/api/runs/workspace-swap/workbench-input")
        page = client.get("/runs/workspace-swap")

    assert first.status_code == 200
    assert second.status_code == workbench_input.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == workbench_input.json()["error"]
        == page.json()["error"]
        == {
            "code": "workspace-unavailable",
            "message": "The workbench workspace is unavailable.",
            "fields": {},
        }
    )


def test_run_scoped_reads_report_a_valid_run_with_an_unsafe_location_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="unsafe-location", schema_version=7)
    module = _module()
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    unsafe_detail = "unsafe run location must never be returned"

    with TestClient(app, base_url=ORIGIN) as client:
        first = client.get("/api/runs/unsafe-location/meta")

        def unsafe_load(_repository: object, _run_id: str) -> None:
            raise UnsafeRunLocation(unsafe_detail)

        monkeypatch.setattr(module.WorkbenchRunRepository, "load", unsafe_load)
        second = client.get("/api/runs/unsafe-location/meta")
        page = client.get("/runs/unsafe-location")

    assert first.status_code == 200
    assert second.status_code == page.status_code == 409
    assert (
        second.json()["error"]
        == page.json()["error"]
        == {
            "code": "run-unavailable",
            "message": "The saved run could not be verified.",
            "fields": {},
        }
    )
    assert unsafe_detail not in second.text + page.text


def test_run_scoped_validation_errors_never_echo_query_values(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    stored = _saved_workbench_run(root, run_id="screened-query", schema_version=7)
    workbench = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    legacy = _legacy_saved_run_app(stored)
    secret = "Authorization: Bearer sk-never-echo-scoped-query-1234567890"

    with (
        TestClient(workbench, base_url=ORIGIN) as scoped_client,
        TestClient(legacy, base_url=ORIGIN) as legacy_client,
    ):
        scoped = scoped_client.get(
            "/api/runs/screened-query/frame",
            params={"minute": secret},
        )
        direct = legacy_client.get("/api/frame", params={"minute": secret})
        scoped_timeline = scoped_client.get(
            "/api/runs/screened-query/timeline",
            params={"minute": secret, "agent_id": "person-001"},
        )
        direct_timeline = legacy_client.get(
            "/api/timeline",
            params={"minute": secret, "agent_id": "person-001"},
        )

    assert (
        scoped.status_code
        == direct.status_code
        == scoped_timeline.status_code
        == direct_timeline.status_code
        == 422
    )
    assert (
        scoped.json()
        == direct.json()
        == scoped_timeline.json()
        == direct_timeline.json()
        == {"detail": "request validation failed"}
    )
    assert secret not in scoped.text + direct.text + scoped_timeline.text + direct_timeline.text


def test_submit_is_async_then_polls_to_verified_completion_and_restart_discovery(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    with client:
        accepted = _secure_post(client, "/api/jobs", _tiny_draft())

        assert accepted.status_code == 202
        document = accepted.json()
        assert set(document) == {"schema_version", "job", "status_url"}
        assert document["schema_version"] == 1
        assert document["status_url"] == f"/api/jobs/{document['job']['job_id']}"
        assert document["job"]["run_id"] == "browser-run"
        assert document["job"]["accepted_settings"]["seed"] == "42"
        assert document["job"]["phase"] in {"queued", "evaluating"}

        completed = _await_job(client, document["status_url"])
        assert set(completed) == {
            "schema_version",
            "job_id",
            "run_id",
            "accepted_settings",
            "phase",
            "created_at",
            "updated_at",
            "cancellation_requested",
            "can_cancel",
            "error",
            "result",
            "cognition_progress",
        }
        assert completed["phase"] == "completed"
        assert completed["cognition_progress"] is None
        assert completed["error"] is None
        assert completed["result"]["run_id"] == "browser-run"
        assert completed["result"]["run_schema_version"] == 7
        assert completed["result"]["seed"] == "42"

        page = client.get("/api/runs?offset=0&limit=1")
        assert page.status_code == 200
        assert page.json()["returned_count"] == 1
        assert page.json()["runs"] == [completed["result"]]

    assert (root / "city-runs" / "browser-run" / "inputs" / "workbench.json").is_file()

    restarted = _module().create_city_workbench_app(
        prepare_workbench_workspace(root), csrf_token=TOKEN
    )
    with TestClient(restarted, base_url=ORIGIN) as restarted_client:
        page = restarted_client.get("/api/runs")
    assert page.status_code == 200
    assert [item["run_id"] for item in page.json()["runs"]] == ["browser-run"]


def test_submit_returns_while_evaluation_is_blocked_then_busy_job_can_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    entered = threading.Event()
    release = threading.Event()
    original_prepare = module.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: Any, validated: Any):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test evaluation gate timed out")
        return original_prepare(repository, validated)

    monkeypatch.setattr(module.WorkbenchRunRepository, "prepare", blocked_prepare)
    client, root = _client(tmp_path)
    try:
        with client:
            accepted = _secure_post(client, "/api/jobs", _tiny_draft(run_id="blocked-run"))
            assert accepted.status_code == 202
            assert entered.wait(5)

            busy = _secure_post(client, "/api/jobs", _tiny_draft(run_id="other-run"))
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "job-busy"
            capabilities = client.get("/api/workbench")
            assert capabilities.status_code == 200
            assert capabilities.json()["active_job"]["job_id"] == accepted.json()["job"]["job_id"]
            assert capabilities.json()["active_job"]["run_id"] == "blocked-run"
            assert capabilities.json()["active_job"]["phase"] == "evaluating"

            cancelled = _secure_post(
                client,
                accepted.json()["status_url"] + "/cancel",
                {},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["cancellation_requested"] is True
            assert cancelled.json()["can_cancel"] is False
            release.set()
            terminal = _await_job(client, accepted.json()["status_url"])
            assert terminal["phase"] == "cancelled"
    finally:
        release.set()

    assert not (root / "city-runs" / "blocked-run").exists()
    assert not (root / "city-runs" / "other-run").exists()


def test_concurrent_submissions_cannot_both_be_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    submission_barrier = threading.Barrier(2)
    evaluation_entered = threading.Event()
    release_evaluation = threading.Event()
    original_prepare = module.WorkbenchRunRepository.prepare

    def blocked_prepare(repository: Any, validated: Any):
        evaluation_entered.set()
        if not release_evaluation.wait(10):
            raise RuntimeError("test evaluation gate timed out")
        return original_prepare(repository, validated)

    monkeypatch.setattr(module.WorkbenchRunRepository, "prepare", blocked_prepare)
    client, root = _client(tmp_path)
    responses: list[Any] = []
    response_lock = threading.Lock()

    def submit(run_id: str) -> None:
        submission_barrier.wait(5)
        response = _secure_post(client, "/api/jobs", _tiny_draft(run_id=run_id))
        with response_lock:
            responses.append(response)

    first = threading.Thread(target=submit, args=("race-first",))
    second = threading.Thread(target=submit, args=("race-second",))
    try:
        with client:
            first.start()
            second.start()
            first.join(10)
            second.join(10)
            assert not first.is_alive()
            assert not second.is_alive()
            assert sorted(response.status_code for response in responses) == [202, 409]
            accepted = next(response for response in responses if response.status_code == 202)
            refused = next(response for response in responses if response.status_code == 409)
            assert refused.json()["error"]["code"] == "job-busy"
            assert evaluation_entered.wait(5)
            cancelled = _secure_post(client, accepted.json()["status_url"] + "/cancel", {})
            assert cancelled.status_code == 200
            release_evaluation.set()
            assert _await_job(client, accepted.json()["status_url"])["phase"] == "cancelled"
    finally:
        release_evaluation.set()
        first.join(10)
        second.join(10)

    assert not (root / "city-runs").exists()


def test_application_lifespan_closes_its_owned_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    closed = threading.Event()
    original_close = module.CityJobManager.close

    def observed_close(manager: Any) -> None:
        original_close(manager)
        closed.set()

    monkeypatch.setattr(module.CityJobManager, "close", observed_close)
    client, _ = _client(tmp_path)
    with client:
        assert not closed.is_set()
        assert client.get("/api/workbench").status_code == 200

    assert closed.is_set()


def test_duplicate_unknown_job_late_cancel_and_cancel_body_are_stable_refusals(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        accepted = _secure_post(client, "/api/jobs", _tiny_draft(run_id="conflict-run"))
        completed = _await_job(client, accepted.json()["status_url"])
        assert completed["phase"] == "completed"

        duplicate = _secure_post(client, "/api/jobs", _tiny_draft(run_id="conflict-run"))
        missing = client.get("/api/jobs/job-00000000000000000000000000000000")
        malformed = client.get("/api/jobs/not-a-job")
        late = _secure_post(client, accepted.json()["status_url"] + "/cancel", {})
        nonempty = _secure_post(
            client,
            accepted.json()["status_url"] + "/cancel",
            {"unexpected": "value-never-echoed"},
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "run-conflict"
    # The conflict names the run ID field, so the browser marks that input in place.
    assert duplicate.json()["error"]["fields"] == {
        "settings.run_id": "A saved run already uses this ID. Choose a new run ID."
    }
    assert missing.status_code == malformed.status_code == 404
    assert missing.json()["error"]["code"] == "job-not-found"
    assert malformed.json()["error"]["code"] == "job-not-found"
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "job-state-conflict"
    assert nonempty.status_code == 422
    assert nonempty.json()["error"]["fields"] == {"request": "Unknown field."}
    assert "value-never-echoed" not in nonempty.text


@pytest.mark.parametrize(
    "query",
    [
        "offset=-1",
        "offset=1.0",
        "offset=true",
        "offset=01",
        "offset=10001",
        "limit=0",
        "limit=101",
        "limit=1.0",
        "limit=true",
        "limit=01",
        "offset=0&offset=1",
        "limit=1&limit=2",
        "unknown=1",
        pytest.param("offset=" + "9" * 5_000, id="very-large-offset"),
    ],
)
def test_run_pagination_refuses_noncanonical_duplicate_or_unknown_query(
    tmp_path: Path,
    query: str,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get(f"/api/runs?{query}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-query"
    assert "true" not in response.text
    assert "unknown" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/workbench?unexpected=true", None),
        ("get", "/api/catalog/cities?unexpected=true", None),
        (
            "get",
            "/api/catalog/cities/fictional-grid-v2?unexpected=true",
            None,
        ),
        ("get", "/api/creative-templates?unexpected=true", None),
        ("post", "/api/scenarios/validate?dry_run=true", _tiny_draft()),
        ("post", "/api/jobs?dry_run=true", _tiny_draft()),
        ("get", "/api/jobs/job-00000000000000000000000000000000?x=1", None),
        ("post", "/api/jobs/job-00000000000000000000000000000000/cancel?x=1", {}),
    ],
)
def test_fixed_control_routes_refuse_every_query_parameter_without_side_effects(
    tmp_path: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    client, root = _client(tmp_path)
    with client:
        response = (
            client.get(path) if method == "get" else _secure_post(client, path, payload or {})
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-query"
    assert "dry_run" not in response.text
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize(
    "document",
    [
        b'{"settings":',
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b"[]",
    ],
)
def test_invalid_job_documents_create_neither_job_nor_artifact(
    tmp_path: Path,
    document: bytes,
) -> None:
    client, root = _client(tmp_path)
    with client:
        response = client.post(
            "/api/jobs",
            headers={
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "X-AdLife-CSRF": TOKEN,
            },
            content=document,
        )
        capabilities = client.get("/api/workbench")
        page = client.get("/api/runs")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed-json"
    assert capabilities.json()["active_job"] is None
    assert page.json()["verified_total"] == 0
    assert not (root / "city-runs").exists()


def test_structurally_invalid_job_draft_is_screened_and_never_registered(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, root = _client(tmp_path)
    secret = "sk-never-return-invalid-job-draft-1234567890"
    payload = _tiny_draft(run_id="invalid-draft")
    payload["settings"]["agent_count"] = True
    payload["api_key"] = secret

    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, "/api/jobs", payload)
        capabilities = client.get("/api/workbench")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert response.json()["error"]["fields"] == {
        "request": "Request data cannot contain credential fields.",
    }
    assert secret not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None
    assert not (root / "city-runs").exists()


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
def test_well_formed_structural_secret_is_a_semantic_refusal_without_job_or_files(
    tmp_path: Path,
    path: str,
) -> None:
    client, root = _client(tmp_path)
    payload = _tiny_draft()
    # Each string is individually allowed by V1, but the pair is structurally forbidden.
    payload["cohort"]["interests"] = ["api_key", "opaque-private-marker"]
    before = _snapshot(root)
    with client:
        response = _secure_post(client, path, payload)
        capabilities = client.get("/api/workbench")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert "opaque-private-marker" not in response.text
    assert "api_key" not in response.text
    assert capabilities.json()["active_job"] is None
    assert _snapshot(root) == before


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    ("case", "field", "message"),
    [
        ("secret", "request", "Request data cannot contain credential fields."),
        ("future-version", "schema_version", "Use workbench schema version 1, 2, or 3."),
        ("malformed-version", "schema_version", "Use workbench schema version 1, 2, or 3."),
    ],
)
def test_write_endpoints_return_value_free_typed_transport_refusals_before_mutation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    path: str,
    case: str,
    field: str,
    message: str,
) -> None:
    client, root = _client(tmp_path)
    marker = "opaque-private-transport-marker"
    payload = draft_v3_data()
    payload["settings"]["seed"] = "42"
    if case == "secret":
        payload["credentials"] = {"api_key": marker}
    elif case == "future-version":
        payload["schema_version"] = 5
    else:
        payload["schema_version"] = marker
    before = _snapshot(root)

    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, path, payload)
        capabilities = client.get("/api/workbench")
        runs = client.get("/api/runs")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-fields",
        "message": "One or more request fields are invalid.",
        "fields": {field: message},
    }
    assert marker not in response.text
    assert caplog.text == ""
    assert "internal-error" not in response.text
    assert capabilities.json()["active_job"] is None
    assert runs.json()["verified_total"] == 0
    assert _snapshot(root) == before


def test_secret_content_on_cancel_is_not_diagnosed_as_a_run_draft(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)
    with client:
        response = _secure_post(
            client, "/api/jobs/job-" + "a" * 32 + "/cancel", {"api_key": "opaque-private-marker"}
        )
    assert response.status_code == 422
    assert set(response.json()["error"]["fields"]) == {"request"}
    assert "opaque-private-marker" not in response.text
    assert _snapshot(root) == before


def test_worker_submission_refusal_is_constant_and_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    secret = "Authorization: Bearer sk-never-return-submission-1234567890"
    absolute_path = r"C:\private\workspace\executor.txt"

    def refuse_submission(*_args: object, **_kwargs: object) -> None:
        raise module.WorkbenchJobSubmissionError(f"{secret} at {absolute_path}")

    monkeypatch.setattr(module.CityJobManager, "submit", refuse_submission)
    client, root = _client(tmp_path)
    with caplog.at_level(logging.ERROR), client:
        response = _secure_post(client, "/api/jobs", _tiny_draft(run_id="submit-failure"))
        capabilities = client.get("/api/workbench")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "worker-unavailable",
        "message": "The workbench worker is not accepting jobs.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text
    assert absolute_path not in response.text + caplog.text
    assert capabilities.json()["active_job"] is None
    assert not (root / "city-runs").exists()


def test_schema3_offline_job_publishes_one_verified_manifest_v9_run(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    payload = draft_v3_data()
    payload["settings"]["seed"] = "42"

    with client:
        response = _secure_post(client, "/api/jobs", payload)
        assert response.status_code == 202
        terminal = _await_job(client, response.json()["status_url"])
        capabilities = client.get("/api/workbench")
        runs = client.get("/api/runs")

    assert terminal["phase"] == "completed"
    assert terminal["accepted_settings"]["workbench_input_schema_version"] == 3
    assert terminal["accepted_settings"]["run_schema_version"] == 9
    assert terminal["accepted_settings"]["cognition_mode"] == "rules"
    assert terminal["result"]["run_schema_version"] == 9
    assert terminal["result"]["cognition_mode"] == "rules"
    assert capabilities.json()["active_job"] is None
    assert runs.json()["verified_total"] == 1
    assert (root / "city-runs" / payload["settings"]["run_id"] / "run.json").is_file()


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_schema4_offline_api_job_publishes_freshly_verified_v10(
    tmp_path: Path,
    mode: str,
) -> None:
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    client, root = _client(tmp_path)
    payload = draft_v4_data(mode=mode)
    payload["settings"].update(
        run_id=f"api-{mode}-v10",
        seed="42",
        agent_count=1,
        days=2,
    )
    payload["population"]["personas"][0]["count"] = 1

    with client:
        response = _secure_post(client, "/api/jobs", payload)
        assert response.status_code == 202
        terminal = _await_job(client, response.json()["status_url"])
        listed = client.get("/api/runs").json()

    assert terminal["phase"] == "completed"
    assert terminal["accepted_settings"]["workbench_input_schema_version"] == 4
    assert terminal["accepted_settings"]["run_schema_version"] == 10
    assert terminal["accepted_settings"]["effect_scope"] == "bounded-signals-v1"
    assert terminal["result"]["run_schema_version"] == 10
    assert terminal["result"]["workbench_input_schema_version"] == 4
    assert terminal["result"]["inspector_url"] == f"/runs/api-{mode}-v10"
    assert listed["runs"][0] == terminal["result"]
    stored = CityRunStore(root).load(f"api-{mode}-v10")
    assert stored.manifest.schema_version == 10
    assert stored.cognition_evaluation is not None


def test_schema4_provider_job_requires_explicit_bounded_compatibility_without_leaks(
    tmp_path: Path,
) -> None:
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        payload = draft_v4_data(mode="local", limit=10)
        payload["settings"].update(run_id="needs-v2-check", agent_count=1, days=2, seed="42")
        payload["population"]["personas"][0]["count"] = 1
        payload["cognition"].update(
            provider_profile_id=profile["profile_id"],
            provider_revision_sha256=profile["revision_sha256"],
        )
        response = _secure_post(client, "/api/jobs", payload)
        capabilities = client.get("/api/workbench").json()

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "provider-compatibility-required",
        "message": "A successful bounded-cognition provider compatibility check is required.",
        "fields": {},
    }
    assert profile["revision_sha256"] not in response.text
    assert capabilities["active_job"] is None


def test_schema4_checked_provider_runs_with_the_same_v2_factory(tmp_path: Path) -> None:
    from tests.unit.city.test_workbench_providers import _ProviderV2
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    providers = []

    def factory(**kwargs):
        provider = _ProviderV2(**kwargs)
        providers.append(provider)
        return provider

    root = tmp_path / "workspace"
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
        provider_factory_v2=factory,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        checked = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {"effect_scope": "bounded-signals-v1"},
        )
        assert checked.status_code == 200
        payload = draft_v4_data(mode="local", limit=10)
        payload["settings"].update(
            run_id="checked-provider-v10",
            seed="42",
            days=2,
        )
        payload["cognition"].update(
            provider_profile_id=profile["profile_id"],
            provider_revision_sha256=profile["revision_sha256"],
        )
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        terminal = _await_job(client, submitted.json()["status_url"])

    assert terminal["phase"] == "completed"
    assert terminal["result"]["provider_count"] > 0
    assert len(providers) == 2
    assert all(provider.closed for provider in providers)
    assert all(provider.kwargs["credential"].secret.cleared for provider in providers)
    assert CityRunStore(root).load("checked-provider-v10").manifest.schema_version == 10


def _await_job_where(
    client: TestClient,
    status_url: str,
    predicate: Any,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        job = response.json()
        if predicate(job):
            return job
        pause.wait(0.005)
    raise AssertionError(f"job at {status_url} did not reach the expected state")


def test_schema4_job_status_counts_slow_model_answers_as_they_arrive(tmp_path: Path) -> None:
    from tests.unit.city.test_workbench_providers import _ProviderV2
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    permits = threading.Semaphore(0)
    providers = []

    class SlowProvider(_ProviderV2):
        async def call(self, request):
            while not permits.acquire(blocking=False):
                await asyncio.sleep(0.002)
            return await super().call(request)

    def factory(**kwargs):
        # The compatibility check answers at once; only the run's answers arrive slowly.
        provider = _ProviderV2(**kwargs) if not providers else SlowProvider(**kwargs)
        providers.append(provider)
        return provider

    def answered(job: dict[str, Any]) -> int:
        progress = job["cognition_progress"]
        return -1 if progress is None else progress["resolved"]

    root = tmp_path / "workspace"
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
        provider_factory_v2=factory,
    )
    polled: list[dict[str, Any]] = []
    try:
        with TestClient(app, base_url=ORIGIN) as client:
            profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
            checked = _secure_post(
                client,
                f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
                {"effect_scope": "bounded-signals-v1"},
            )
            assert checked.status_code == 200
            payload = draft_v4_data(mode="local", limit=10)
            payload["settings"].update(
                run_id="progress-provider-v10", seed="42", agent_count=3, days=2
            )
            payload["population"]["personas"][0]["count"] = 3
            payload["cognition"].update(
                provider_profile_id=profile["profile_id"],
                provider_revision_sha256=profile["revision_sha256"],
            )
            submitted = _secure_post(client, "/api/jobs", payload)
            assert submitted.status_code == 202
            status_url = submitted.json()["status_url"]
            assert submitted.json()["job"]["cognition_progress"] is None

            first = _await_job_where(client, status_url, lambda job: answered(job) >= 0)
            assert first["phase"] == "requesting-cognition"
            assert first["cognition_progress"] == {"resolved": 0, "total": 6}
            polled.append(first)
            for count in range(1, 7):
                permits.release()
                polled.append(
                    _await_job_where(
                        client, status_url, lambda job, expected=count: answered(job) >= expected
                    )
                )
            terminal = _await_job(client, status_url)
            active = client.get("/api/workbench").json()["active_job"]
    finally:
        permits.release(64)

    assert [job["cognition_progress"] for job in polled] == [
        {"resolved": count, "total": 6} for count in range(7)
    ]
    assert terminal["phase"] == "completed"
    assert terminal["cognition_progress"] == {"resolved": 6, "total": 6}
    assert terminal["result"]["funded_request_count"] == 6
    assert terminal["result"]["provider_count"] == 6
    assert active is None
    assert len(providers) == 2 and all(provider.closed for provider in providers)


def test_schema4_api_provider_failure_completes_with_fallback_evidence(tmp_path: Path) -> None:
    from adlife.adapters.cognition.city_provider_transport import CityProviderAuthenticationError
    from tests.unit.city.test_workbench_providers import _ProviderV2
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    providers = []

    class FailingProvider(_ProviderV2):
        async def call(self, request):
            self.calls += 1
            self.request = request
            raise CityProviderAuthenticationError()

    def factory(**kwargs):
        provider = _ProviderV2(**kwargs) if not providers else FailingProvider(**kwargs)
        providers.append(provider)
        return provider

    root = tmp_path / "workspace"
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
        provider_factory_v2=factory,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        checked = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {"effect_scope": "bounded-signals-v1"},
        )
        assert checked.status_code == 200
        payload = draft_v4_data(mode="local", limit=10)
        payload["settings"].update(run_id="fallback-provider-v10", seed="42", days=2)
        payload["cognition"].update(
            provider_profile_id=profile["profile_id"],
            provider_revision_sha256=profile["revision_sha256"],
        )
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        terminal = _await_job(client, submitted.json()["status_url"])

    assert terminal["phase"] == "completed"
    assert terminal["result"]["fallback_count"] > 0
    assert terminal["result"]["provider_count"] == 0
    assert len(providers) == 2 and all(provider.closed for provider in providers)
    assert CityRunStore(root).load("fallback-provider-v10").manifest.schema_version == 10


def test_schema4_api_enforces_funding_limit_as_budget_skipped_evidence(tmp_path: Path) -> None:
    from tests.unit.city.test_workbench_providers import _ProviderV2
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    providers = []

    def factory(**kwargs):
        provider = _ProviderV2(**kwargs)
        providers.append(provider)
        return provider

    root = tmp_path / "workspace"
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
        provider_factory_v2=factory,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        profile = _secure_post(client, "/api/providers", _local_provider_payload()).json()
        checked = _secure_post(
            client,
            f"/api/providers/local-ollama/revisions/{profile['revision_sha256']}/check",
            {"effect_scope": "bounded-signals-v1"},
        )
        assert checked.status_code == 200
        payload = draft_v4_data(mode="local", limit=10)
        payload["settings"].update(
            run_id="budget-provider-v10",
            seed="42",
            agent_count=10,
            days=2,
        )
        payload["population"]["personas"][0]["count"] = 10
        payload["cognition"].update(
            provider_profile_id=profile["profile_id"],
            provider_revision_sha256=profile["revision_sha256"],
        )
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        terminal = _await_job(client, submitted.json()["status_url"])

    result = terminal["result"]
    assert terminal["phase"] == "completed"
    assert result["funded_request_count"] == 10
    assert result["budget_skipped_count"] == result["selected_request_count"] - 10
    assert result["budget_skipped_count"] > 0
    assert result["provider_count"] == 10
    assert CityRunStore(root).load("budget-provider-v10").manifest.schema_version == 10


def test_schema4_api_uses_prevalidated_v2_cache_records_without_provider_calls(
    tmp_path: Path,
) -> None:
    from adlife.adapters.cognition.city_cache_v2 import CityCognitionCacheV2
    from adlife.city import workbench_transport
    from adlife.city.provider_profiles import ProviderProfileDraftV1, ProviderProfileRegistry
    from adlife.city.workbench_runs import WorkbenchCognitionOperationV2, WorkbenchRunRepository
    from tests.unit.city.test_workbench_providers import _ProviderV2
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data
    from tests.unit.cognition.test_city_cache_v2 import _record

    root = tmp_path / "workspace"
    workspace = prepare_workbench_workspace(root)
    registry = ProviderProfileRegistry(workspace)
    profile = registry.create(ProviderProfileDraftV1.model_validate(_local_provider_payload()))
    payload = draft_v4_data(mode="local", limit=10)
    payload["settings"].update(run_id="cache-provider-v10", seed="42", days=2)
    payload["cognition"].update(
        provider_profile_id=profile.profile_id,
        provider_revision_sha256=profile.revision_sha256,
    )
    validated = construct_workbench_run(workbench_transport.parse_workbench_http_draft(payload))
    repository = WorkbenchRunRepository(workspace, provider_registry=registry)
    staged = repository.prepare_scientific_v2(
        validated,
        WorkbenchCognitionOperationV2(profile=profile, endpoint=None, credential=None),
    )
    cache = CityCognitionCacheV2(workspace)
    funded_ids = {
        reservation.request_id
        for reservation in staged.cognition_input.reservations
        if reservation.funded
    }
    for request in staged.cognition_requests:
        if request.request_id in funded_ids:
            record = _record(request, staged.cognition_input)
            cache.put(record.key, record)
    assert funded_ids

    providers = []

    def factory(**kwargs):
        provider = _ProviderV2(**kwargs)
        providers.append(provider)
        return provider

    app = _module().create_city_workbench_app(
        workspace,
        csrf_token=TOKEN,
        provider_factory_v2=factory,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        checked = _secure_post(
            client,
            f"/api/providers/{profile.profile_id}/revisions/{profile.revision_sha256}/check",
            {"effect_scope": "bounded-signals-v1"},
        )
        assert checked.status_code == 200
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        terminal = _await_job(client, submitted.json()["status_url"])

    result = terminal["result"]
    assert terminal["phase"] == "completed"
    assert result["cache_count"] == len(funded_ids)
    assert result["provider_count"] == 0
    assert len(providers) == 2
    assert providers[0].calls == 1 and providers[1].calls == 0
    assert all(provider.closed for provider in providers)
    assert CityRunStore(root).load("cache-provider-v10").manifest.schema_version == 10


def test_endpoint_binding_submission_error_is_constant_and_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.adapters.cognition.city_endpoint_policy import CityEndpointBindingError

    secret = "sk-private-endpoint-marker"
    path = r"C:\private\endpoint-marker"

    def refuse(*_args, **_kwargs):
        error = CityEndpointBindingError()
        error.add_note(f"{secret} {path}")
        raise error

    monkeypatch.setattr(_module().CityJobManager, "submit", refuse)
    client, _root = _client(tmp_path)
    with client:
        response = _secure_post(client, "/api/jobs", _tiny_draft())
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "provider-endpoint-refused",
        "message": "The provider profile is not allowed by the current startup policy.",
        "fields": {},
    }
    assert secret not in response.text
    assert path not in response.text


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_v9_cognition_routes_project_one_pinned_verified_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    client, root = _client(tmp_path)
    payload = draft_v3_data(mode=mode)
    run_id = f"cognition-{mode}"
    payload["settings"].update({"seed": "42", "run_id": run_id})
    with client:
        submitted = _secure_post(client, "/api/jobs", payload)
        assert submitted.status_code == 202
        terminal = _await_job(client, submitted.json()["status_url"])
        assert terminal["phase"] == "completed"
        stored = CityRunStore(root).load(run_id)
        assert stored.cognition_evaluation is not None
        assert stored.cognition_input is not None
        assert stored.cognition_evaluation.records
        loaded: list[str] = []
        original_load = _module().WorkbenchRunRepository.load

        def counted_load(repository: object, candidate: str):
            loaded.append(candidate)
            return original_load(repository, candidate)

        monkeypatch.setattr(_module().WorkbenchRunRepository, "load", counted_load)
        before = _artifact_snapshot(root)
        page = client.get(f"/runs/{run_id}")
        token = re.search(
            r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">', page.text
        )
        assert token is not None
        headers = {"X-AdLife-View-Token": token.group(1)}
        summary = client.get(f"/api/runs/{run_id}/cognition-summary", headers=headers)
        records = client.get(f"/api/runs/{run_id}/cognition-records", headers=headers)
        frozen = client.get(f"/api/runs/{run_id}/workbench-input", headers=headers)
        assert summary.status_code == records.status_code == frozen.status_code == 200
        summary_data = summary.json()
        assert set(summary_data) == {
            "schema_version",
            "run_id",
            "run_schema_version",
            "cognition_input_sha256",
            "mode",
            "explainer_id",
            "provider_profile_id",
            "provider_revision_sha256",
            "model_id",
            "aggregate_output_token_ceiling",
            "summary",
        }
        assert summary_data["summary"] == stored.cognition_evaluation.summary.model_dump(
            mode="json"
        )
        assert summary_data["mode"] == mode
        assert summary_data["explainer_id"] == f"city-{mode}-explainer-v1"
        assert summary_data["model_id"] is None
        assert summary_data["provider_profile_id"] is None
        assert summary_data["provider_revision_sha256"] is None
        assert summary_data["aggregate_output_token_ceiling"] == 0
        assert summary_data["cognition_input_sha256"] == stored.cognition_input.fingerprint
        assert records.json() == {
            "schema_version": 1,
            "run_id": run_id,
            "run_schema_version": 9,
            "cognition_input_sha256": stored.cognition_input.fingerprint,
            "records": [
                record.model_dump(mode="json") for record in stored.cognition_evaluation.records
            ],
        }
        assert frozen.json()["run_schema_version"] == 9
        assert frozen.json()["workbench_input_schema_version"] == 3
        assert frozen.json()["cognition"]["mode"] == mode
        assert loaded == [run_id]
        assert str(root) not in summary.text + records.text + frozen.text
        assert _artifact_snapshot(root) == before

        artifact = stored.directory / "outputs" / "cognition.jsonl"
        artifact.write_bytes(artifact.read_bytes() + b" ")
        for suffix, expected in (("cognition-summary", summary), ("cognition-records", records)):
            assert (
                client.get(f"/api/runs/{run_id}/{suffix}", headers=headers).content
                == expected.content
            )
            fresh = client.get(f"/api/runs/{run_id}/{suffix}")
            assert fresh.status_code == 409
            assert fresh.json()["error"]["code"] == "run-unavailable"


@pytest.mark.parametrize("schema_version", [6, 7, 8])
def test_cognition_routes_refuse_verified_pre_v9_runs(tmp_path: Path, schema_version: int) -> None:
    client, root = _client(tmp_path)
    run_id = f"no-cognition-v{schema_version}"
    with client:
        if schema_version == 8:
            submitted = _secure_post(client, "/api/jobs", _persona_draft(run_id=run_id))
            assert _await_job(client, submitted.json()["status_url"])["phase"] == "completed"
        else:
            _saved_workbench_run(root, run_id=run_id, schema_version=schema_version)
        for suffix in ("cognition-summary", "cognition-records"):
            response = client.get(f"/api/runs/{run_id}/{suffix}")
            assert response.status_code == 404
            assert response.json()["error"] == {
                "code": "cognition-unavailable",
                "message": "This saved run has no frozen cognition evidence.",
                "fields": {},
            }


@pytest.mark.parametrize("suffix", ["cognition-summary", "cognition-records"])
def test_cognition_routes_screen_queries_and_snapshot_tokens(tmp_path: Path, suffix: str) -> None:
    client, _root = _client(tmp_path)
    marker = "Authorization: Bearer sk-never-echo-cognition-1234567890"
    with client:
        query = client.get(f"/api/runs/unknown/{suffix}", params={"private": marker})
        token = client.get(f"/api/runs/unknown/{suffix}", headers={"X-AdLife-View-Token": marker})
        missing = client.get(f"/api/runs/unknown/{suffix}")
    assert query.status_code == 422
    assert query.json()["error"]["code"] == "invalid-query"
    assert token.status_code == 409
    assert token.json()["error"]["code"] == "run-unavailable"
    assert missing.status_code == 404
    assert marker not in query.text + token.text + missing.text


@pytest.mark.parametrize("phase", ["prepare", "publish", "verified_summary"])
def test_worker_failures_expose_only_screened_terminal_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    module = _module()
    secret = "Authorization: Bearer sk-never-expose-worker-failure-1234567890"
    absolute_path = r"C:\private\workspace\job.json"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"{secret} at {absolute_path}")

    monkeypatch.setattr(module.WorkbenchRunRepository, phase, fail)
    client, root = _client(tmp_path)
    with caplog.at_level(logging.ERROR), client:
        accepted = _secure_post(
            client,
            "/api/jobs",
            _tiny_draft(run_id=f"fail-{phase.replace('_', '-')}"),
        )
        assert accepted.status_code == 202
        terminal = _await_job(client, accepted.json()["status_url"])

    assert terminal["phase"] == "failed"
    assert (
        terminal["error"]["code"]
        == {
            "prepare": "evaluation-failed",
            "publish": "persistence-failed",
            "verified_summary": "verification-failed",
        }[phase]
    )
    combined = json.dumps(terminal) + caplog.text
    assert secret not in combined
    assert absolute_path not in combined
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert secret.encode() not in content
            assert absolute_path.encode() not in content


def test_city_pack_upload_returns_one_exact_path_free_row_and_canonical_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    calls: list[bytes] = []
    original = module.WorkbenchCityResolver.register_city

    def counted_register(resolver: WorkbenchCityResolver, document: bytes):
        calls.append(document)
        return original(resolver, document)

    monkeypatch.setattr(module.WorkbenchCityResolver, "register_city", counted_register)
    client, root = _client(tmp_path)
    document = _city_pack_document(indent=2)

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 201
    assert calls == [document]
    workspace = prepare_workbench_workspace(root)
    resolved = WorkbenchCityResolver(workspace).resolve_city("operator-city")
    expected = module.build_workbench_city_catalog_row(resolved).model_dump(mode="json")
    assert response.json() == expected
    assert set(response.json()) == {
        "city_id",
        "display_name",
        "pack_schema_version",
        "storage_origin",
        "data_origin",
        "qualification",
        "reviewer_role",
        "reviewed_on",
        "coverage",
        "time_zone",
        "source",
        "known_omissions",
        "pack_sha256",
    }
    assert response.json()["storage_origin"] == "workspace-upload"
    assert response.json()["data_origin"] == "operator-supplied"
    assert response.json()["qualification"] == "operator-supplied-unreviewed"
    assert str(root) not in response.text
    assert "resource_name" not in response.text
    stored = root / "city-packs" / "operator-city.json"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"
    _assert_no_live_city_stage_bytes(root)
    assert not (root / "city-runs").exists()


def test_upload_route_coexists_with_a_city_whose_identifier_is_upload(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    document = _city_pack_document("upload")

    with client:
        missing = client.get(UPLOAD_PATH)
        registered = _secure_upload(client, document)
        detail = client.get(UPLOAD_PATH)
        duplicate = _secure_upload(client, document)

    assert missing.status_code == 404
    assert registered.status_code == 201
    assert registered.json()["city_id"] == "upload"
    assert detail.status_code == 200
    assert detail.json()["city_id"] == "upload"
    expected_pack = CityPackV2.model_validate_json(_city_pack_document("upload"))
    assert detail.json()["pack"] == expected_pack.model_dump(mode="json")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "city-pack-conflict"
    assert (root / "city-packs" / "upload.json").read_bytes() == (
        canonical_json(expected_pack).encode("utf-8") + b"\n"
    )


def test_city_pack_upload_rejects_query_and_security_failures_before_registration(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    document = _city_pack_document()
    before = _snapshot(root)

    with client:
        responses = [
            _secure_upload(
                client,
                document,
                path=f"{UPLOAD_PATH}?api_key=sk-query-never-echo-1234567890",
            ),
            _secure_upload(client, document, headers={"Content-Type": "text/plain"}),
            _secure_upload(client, document, headers={"Origin": "http://example.test"}),
            _secure_upload(client, document, headers={"X-AdLife-CSRF": "wrong-token"}),
            _secure_upload(
                client,
                document,
                headers={"Host": "example.test", "Origin": "http://example.test"},
            ),
        ]
        for method in ("PUT", "PATCH", "DELETE"):
            responses.append(
                client.request(
                    method,
                    UPLOAD_PATH,
                    headers={
                        "Content-Type": "application/json",
                        "Origin": ORIGIN,
                        "X-AdLife-CSRF": TOKEN,
                    },
                    content=document,
                )
            )

    assert [(response.status_code, response.json()["error"]["code"]) for response in responses] == [
        (400, "invalid-upload-request"),
        (400, "invalid-content-type"),
        (403, "invalid-origin"),
        (403, "invalid-csrf"),
        (400, "invalid-host"),
        (405, "method-not-allowed"),
        (405, "method-not-allowed"),
        (405, "method-not-allowed"),
    ]
    assert "sk-query-never-echo" not in "".join(response.text for response in responses)
    assert _snapshot(root) == before
    assert not (root / "city-packs").exists()


@pytest.mark.parametrize(
    "document",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        "{}".encode("utf-16"),
        b'{"schema_version":2,"schema_version":2}',
        b'{"value":NaN}',
        b'{"value":1e999}',
        b"[]",
        ('{"value":' + "[" * 40 + "0" + "]" * 40 + "}").encode(),
    ],
)
def test_city_pack_upload_maps_malformed_or_ambiguous_json_to_400(
    tmp_path: Path,
    document: bytes,
) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "malformed-city-pack",
        "message": "The city pack document is not valid JSON.",
        "fields": {},
    }
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 1},
        {"schema_version": True},
        {**_city_pack_payload(), "unexpected": "value-never-echoed"},
    ],
)
def test_city_pack_upload_maps_well_formed_invalid_v2_to_422(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    client, root = _client(tmp_path)
    before = _snapshot(root)
    document = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    with client:
        response = _secure_upload(client, document)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-city-pack",
        "message": "The city pack is not a valid CityPack V2 document.",
        "fields": {},
    }
    assert "unexpected" not in response.text
    assert "value-never-echoed" not in response.text
    assert _snapshot(root) == before


def test_city_pack_upload_has_an_inclusive_four_mib_raw_boundary(tmp_path: Path) -> None:
    client, root = _client(tmp_path)
    exact = b"{}" + b" " * (MAX_CITY_PACK_UPLOAD_BYTES - 2)

    with client:
        accepted_by_boundary = _secure_upload(client, exact)
        oversized = _secure_upload(client, exact + b" ")

    assert accepted_by_boundary.status_code == 422
    assert accepted_by_boundary.json()["error"]["code"] == "invalid-city-pack"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request-too-large"
    assert not (root / "city-packs").exists()


def test_canonical_city_pack_overflow_maps_to_413_before_registry_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module("adlife.city.workbench_city_registry")

    def canonical_overflow(_pack: CityPackV2) -> bytes:
        raise CityPackUploadTooLarge()

    monkeypatch.setattr(registry_module, "_canonical_pack_bytes", canonical_overflow)
    client, root = _client(tmp_path)
    before = _snapshot(root)

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "city-pack-too-large"
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (
            MalformedCityPackUpload(),
            400,
            "malformed-city-pack",
            "The city pack document is not valid JSON.",
        ),
        (
            InvalidCityPackUpload(),
            422,
            "invalid-city-pack",
            "The city pack is not a valid CityPack V2 document.",
        ),
        (
            CityPackUploadTooLarge(),
            413,
            "city-pack-too-large",
            "The city pack exceeds the upload size limit.",
        ),
        (
            CityPackRegistrationConflict(),
            409,
            "city-pack-conflict",
            "The city pack conflicts with the workspace catalog.",
        ),
        (
            WorkbenchCityRegistryUnavailable("sk-registry-error-never-echo-1234567890"),
            409,
            "city-registry-unavailable",
            "The workspace city registry is unavailable.",
        ),
        (
            PackagedCityCatalogUnavailable("sk-catalog-error-never-echo-1234567890"),
            500,
            "catalog-unavailable",
            "The verified city catalog is unavailable.",
        ),
        (
            CityPackPublicationError(False),
            500,
            "city-pack-publication-failed",
            "The city pack could not be published.",
        ),
        (
            CityPackPublicationError(True),
            500,
            "city-pack-publication-uncertain",
            "The city pack may already be registered. Reload the city catalog before retrying.",
        ),
    ],
)
def test_city_pack_upload_maps_typed_failures_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    status: int,
    code: str,
    message: str,
) -> None:
    module = _module()

    def refuse(_resolver: WorkbenchCityResolver, _document: bytes) -> None:
        raise error

    monkeypatch.setattr(module.WorkbenchCityResolver, "register_city", refuse)
    client, root = _client(tmp_path, raise_server_exceptions=False)
    before = _snapshot(root)

    with caplog.at_level(logging.ERROR), client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == status
    assert response.json()["error"] == {"code": code, "message": message, "fields": {}}
    assert "sk-registry-error" not in response.text + caplog.text
    assert "sk-catalog-error" not in response.text + caplog.text
    assert _snapshot(root) == before


def test_corrupt_registry_upload_refusal_preserves_existing_bytes_and_creates_no_record(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    records = root / "city-packs"
    records.mkdir()
    corrupt = records / "unexpected.txt"
    corrupt.write_bytes(b"corrupt-registry-marker")

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "city-registry-unavailable"
    assert corrupt.read_bytes() == b"corrupt-registry-marker"
    assert not (records / "operator-city.json").exists()
    _assert_no_live_city_stage_bytes(root)


def test_precommit_publication_failure_is_generic_and_leaves_no_live_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_module = importlib.import_module("adlife.city.workbench_city_registry")

    def fail_before_commit(*_args: object, **_kwargs: object) -> None:
        raise OSError("private publication path")

    monkeypatch.setattr(registry_module, "_link_stage", fail_before_commit)
    client, root = _client(tmp_path)

    with client:
        response = _secure_upload(client, _city_pack_document())

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "city-pack-publication-failed",
        "message": "The city pack could not be published.",
        "fields": {},
    }
    assert "private publication path" not in response.text
    assert not (root / "city-packs" / "operator-city.json").exists()
    _assert_no_live_city_stage_bytes(root)


@pytest.mark.asyncio
async def test_response_send_failure_after_201_keeps_one_discoverable_immutable_record(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    document = _city_pack_document("send-failure-city", indent=2)
    pending: list[Message] = [{"type": "http.request", "body": document, "more_body": False}]
    sent: list[Message] = []
    secret = "sk-send-failure-never-echo-1234567890"
    private_path = r"C:\private\response.json"
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": UPLOAD_PATH,
        "raw_path": UPLOAD_PATH.encode("ascii"),
        "query_string": b"",
        "headers": [
            (b"host", b"127.0.0.1:8765"),
            (b"content-type", b"application/json"),
            (b"origin", ORIGIN.encode("ascii")),
            (b"x-adlife-csrf", TOKEN.encode("ascii")),
        ],
        "client": ("127.0.0.1", 40_000),
        "server": ("127.0.0.1", 8_765),
    }

    async def receive() -> Message:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body":
            raise ConnectionError(f"{secret} at {private_path}")

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert [message["status"] for message in starts] == [201]
    assert secret not in caplog.text
    assert private_path not in caplog.text
    resolved = WorkbenchCityResolver(workspace).resolve_city("send-failure-city")
    assert resolved.pack == CityPackV2.model_validate_json(_city_pack_document("send-failure-city"))
    stored = workspace.root / "city-packs" / "send-failure-city.json"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"
    _assert_no_live_city_stage_bytes(workspace.root)

    retry_app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    with TestClient(retry_app, base_url=ORIGIN) as client:
        retry = _secure_upload(client, document)
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "city-pack-conflict"
    assert stored.read_bytes() == canonical_json(resolved.pack).encode("utf-8") + b"\n"


def test_catalog_and_creative_endpoints_are_complete_and_stably_ordered(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        cities_response = client.get("/api/catalog/cities")
        creatives_response = client.get("/api/creative-templates")

    assert cities_response.status_code == 200
    cities = cities_response.json()
    assert set(cities) == {"schema_version", "cities"}
    assert cities["schema_version"] == 1
    assert [item["city_id"] for item in cities["cities"]] == sorted(
        item["city_id"] for item in cities["cities"]
    )
    assert [item["city_id"] for item in cities["cities"]] == [
        "fictional-grid-v2",
        "fictional-harbor-loop-v2",
        "fictional-river-junction-v2",
    ]
    city = next(item for item in cities["cities"] if item["city_id"] == "fictional-grid-v2")
    assert set(city) == {
        "city_id",
        "display_name",
        "pack_schema_version",
        "storage_origin",
        "data_origin",
        "qualification",
        "reviewer_role",
        "reviewed_on",
        "coverage",
        "time_zone",
        "source",
        "known_omissions",
        "pack_sha256",
    }
    assert city["city_id"] == "fictional-grid-v2"
    assert city["storage_origin"] == "package"
    assert city["data_origin"] == "fictional"
    assert city["qualification"] == "fictional-fixture"
    assert city["pack_schema_version"] == 2
    assert len(city["pack_sha256"]) == 64
    assert city["known_omissions"]
    assert "resource_name" not in city

    assert creatives_response.status_code == 200
    creatives = creatives_response.json()
    assert creatives["schema_version"] == 1
    assert [item["template_id"] for item in creatives["templates"]] == [
        "fictional-coffee-pass-v1",
        "fictional-crispy-meal-v1",
        "fictional-device-launch-v1",
        "fictional-home-delivery-v1",
    ]
    assert all(len(item["creative_sha256"]) == 64 for item in creatives["templates"])
    assert all(
        item["disclosure"] == "Fictional creative for synthetic simulation only."
        for item in creatives["templates"]
    )


def test_city_detail_returns_the_exact_verified_pack_without_resource_paths(
    tmp_path: Path,
) -> None:
    catalog = load_city_catalog()
    entry = catalog.entries[0]
    pack = select_catalog_city(entry.city_id)
    client, _ = _client(tmp_path)

    with client:
        response = client.get(f"/api/catalog/cities/{entry.city_id}")

    assert response.status_code == 200
    document = response.json()
    assert document["schema_version"] == 1
    assert document["city_id"] == entry.city_id
    assert document["storage_origin"] == "package"
    assert document["city_sha256"] == entry.pack_sha256 == pack.fingerprint
    assert document["pack"] == pack.model_dump(mode="json")
    assert document["coverage"] == entry.coverage.model_dump(mode="json")
    assert document["source"] == entry.source.model_dump(mode="json")
    assert document["known_omissions"] == list(entry.known_omissions)
    assert "resource_name" not in response.text
    assert entry.resource_name not in response.text


def test_catalog_routes_are_lazy_and_load_one_verified_snapshot_per_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")
    original = catalog_module.load_packaged_workbench_cities
    calls = 0

    def counted_snapshot():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", counted_snapshot)
    root = tmp_path / "lazy-workspace"
    workspace = prepare_workbench_workspace(root)
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)
    assert calls == 0

    with TestClient(app, base_url=ORIGIN) as client:
        listed = client.get("/api/catalog/cities")
        assert calls == 1
        detailed = client.get("/api/catalog/cities/fictional-grid-v2")
        assert calls == 2

    assert listed.status_code == 200
    assert detailed.status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/catalog/cities", "/api/catalog/cities/fictional-grid-v2"],
)
def test_catalog_routes_screen_packaged_snapshot_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")
    secret = "sk-packaged-city-catalog-secret-1234567890"

    def unavailable() -> None:
        raise PackagedCityCatalogUnavailable(secret)

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", unavailable)
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR), client:
        response = client.get(path)

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text


def test_app_owns_one_lazy_resolver_and_resolves_each_accepted_draft_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city.workbench_catalog import ResolvedWorkbenchCity

    module = _module()
    selected_pack = select_catalog_city("fictional-grid-v2")
    resolved = ResolvedWorkbenchCity(
        pack=selected_pack,
        storage_origin="package",
        data_origin="fictional",
        qualification="fictional-fixture",
        reviewer_role=None,
        reviewed_on=None,
    )
    instances: list[Any] = []

    class RecordingResolver:
        def __init__(self, workspace: Any) -> None:
            self.workspace = workspace
            self.list_calls = 0
            self.resolve_calls: list[str] = []
            instances.append(self)

        def list_cities(self):
            self.list_calls += 1
            return (resolved,)

        def resolve_city(self, city_id: str):
            self.resolve_calls.append(city_id)
            return resolved

    parse_calls = 0
    construct_calls = 0
    real_parse = module.parse_workbench_http_draft
    real_construct = module.construct_workbench_run

    def counted_parse(payload: Any):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(payload)

    def counted_construct(draft: Any, *, selected_pack: Any = None):
        nonlocal construct_calls
        construct_calls += 1
        assert selected_pack is selected_pack_reference
        return real_construct(draft, selected_pack=selected_pack)

    selected_pack_reference = selected_pack
    monkeypatch.setattr(module, "WorkbenchCityResolver", RecordingResolver)
    monkeypatch.setattr(module, "parse_workbench_http_draft", counted_parse)
    monkeypatch.setattr(module, "construct_workbench_run", counted_construct)
    workspace = prepare_workbench_workspace(tmp_path / "workspace")
    app = module.create_city_workbench_app(workspace, csrf_token=TOKEN)

    assert len(instances) == 1
    assert instances[0].list_calls == 0
    assert instances[0].resolve_calls == []

    with TestClient(app, base_url=ORIGIN) as client:
        listed = client.get("/api/catalog/cities")
        detailed = client.get("/api/catalog/cities/fictional-grid-v2")
        validated = _secure_post(client, "/api/scenarios/validate", _tiny_draft())
        submitted = _secure_post(
            client,
            "/api/jobs",
            _tiny_draft(run_id="one-resolve-job"),
        )
        if submitted.status_code == 202:
            _await_job(client, submitted.json()["status_url"])

    assert listed.status_code == 200
    assert detailed.status_code == 200
    assert validated.status_code == 200
    assert submitted.status_code == 202
    assert len(instances) == 1
    assert instances[0].list_calls == 1
    assert instances[0].resolve_calls == [
        "fictional-grid-v2",
        "fictional-grid-v2",
        "fictional-grid-v2",
    ]
    assert parse_calls == 2
    assert construct_calls == 2


def test_corrupt_registry_blocks_new_city_operations_but_not_frozen_runs(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "workspace"
    prepare_workbench_workspace(root)
    _saved_workbench_run(root, run_id="frozen-run", schema_version=7)
    records = root / "city-packs"
    records.mkdir()
    (records / "unexpected.txt").write_bytes(b"corrupt registry state")

    app = module.create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        listing = client.get("/api/runs")
        inspector = client.get("/runs/frozen-run")
        metadata = client.get("/api/runs/frozen-run/meta")
        replay_frame = client.get("/api/runs/frozen-run/frame?minute=0")
        blocked = [
            client.get("/api/catalog/cities"),
            client.get("/api/catalog/cities/fictional-grid-v2"),
            client.get("/api/catalog/cities/unknown-city"),
            _secure_post(client, "/api/scenarios/validate", _tiny_draft()),
            _secure_post(client, "/api/jobs", _tiny_draft(run_id="blocked-new-run")),
        ]

    assert listing.status_code == 200
    assert listing.json()["runs"][0]["run_id"] == "frozen-run"
    assert inspector.status_code == 200
    assert metadata.status_code == 200
    assert metadata.json()["run_id"] == "frozen-run"
    assert replay_frame.status_code == 200
    assert all(response.status_code == 409 for response in blocked)
    assert all(
        response.json()["error"]
        == {
            "code": "city-registry-unavailable",
            "message": "The workspace city registry is unavailable.",
            "fields": {},
        }
        for response in blocked
    )
    assert not (root / "city-runs" / "blocked-new-run").exists()


@pytest.mark.parametrize(
    "operation",
    ["list", "detail", "validate", "submit"],
)
def test_packaged_catalog_corruption_blocks_only_new_city_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    module = _module()
    catalog_module = importlib.import_module("adlife.city.workbench_catalog")

    def unavailable() -> None:
        raise PackagedCityCatalogUnavailable("private provider detail")

    monkeypatch.setattr(catalog_module, "load_packaged_workbench_cities", unavailable)
    app = module.create_city_workbench_app(
        prepare_workbench_workspace(tmp_path / "workspace"),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        if operation == "list":
            response = client.get("/api/catalog/cities")
        elif operation == "detail":
            response = client.get("/api/catalog/cities/fictional-grid-v2")
        elif operation == "validate":
            response = _secure_post(client, "/api/scenarios/validate", _tiny_draft())
        else:
            response = _secure_post(
                client,
                "/api/jobs",
                _tiny_draft(run_id="catalog-blocked-run"),
            )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }


@pytest.mark.parametrize("city_id", ["unknown-city", "INVALID", "..%2Fprivate"])
def test_city_detail_unknown_or_malformed_identifiers_are_screened(
    tmp_path: Path,
    city_id: str,
) -> None:
    client, _ = _client(tmp_path)

    with client:
        response = client.get(f"/api/catalog/cities/{city_id}")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not-found",
        "message": "The requested resource was not found.",
        "fields": {},
    }
    assert city_id not in response.text


def test_city_detail_catalog_integrity_failure_is_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    client, _ = _client(tmp_path, raise_server_exceptions=False)
    secret = "sk-catalog-integrity-secret-1234567890"

    def unavailable(_city_id: str) -> None:
        raise CityCatalogError(secret)

    monkeypatch.setattr(module, "build_workbench_city_detail", unavailable)
    with caplog.at_level(logging.ERROR), client:
        response = client.get("/api/catalog/cities/fictional-grid-v2")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "catalog-unavailable",
        "message": "The verified city catalog is unavailable.",
        "fields": {},
    }
    assert secret not in response.text + caplog.text


@pytest.mark.parametrize(
    ("phone", "roadside", "channels"),
    [
        (True, False, ["mobile-feed"]),
        (False, True, ["roadside-billboard"]),
        (True, True, ["mobile-feed", "roadside-billboard"]),
    ],
)
def test_validation_endpoint_returns_only_bound_summaries_and_stable_hashes(
    tmp_path: Path,
    phone: bool,
    roadside: bool,
    channels: list[str],
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft(phone=phone, roadside=roadside)
    before = _snapshot(root)
    with client:
        first = _post(client, payload)
        second = _post(client, payload)

    assert first.status_code == 200
    assert first.json() == second.json()
    document = first.json()
    assert document["schema_version"] == 1
    assert document["valid"] is True
    assert document["run"] == {
        "run_id": "launch-run",
        "agent_count": 20,
        "days": 2,
        "seed": "42",
        "response_mode": "deterministic-rules",
    }
    assert document["city"]["city_id"] == "fictional-grid-v2"
    assert document["scenario"]["scenario_id"] == "launch-study"
    assert document["scenario"]["campaign_id"] == "fictional-launch"
    assert document["scenario"]["placement_count"] == len(channels)
    assert document["scenario"]["channels"] == channels
    assert [item["channel"] for item in document["scenario"]["placements"]] == channels
    assert document["response"]["profile_count"] == 20
    assert document["response"]["campaign_count"] == 1
    assert document["response"]["initial_state_count"] == 20
    assert document["creative"]["template_id"] == "fictional-device-launch-v1"
    for value in document["hashes"].values():
        assert len(value) == 64
    assert set(document) == {
        "schema_version",
        "valid",
        "run",
        "city",
        "scenario",
        "creative",
        "response",
        "hashes",
        "disclosure",
    }
    assert _artifact_snapshot(root) == before == ()


@pytest.mark.parametrize("path", ["/api/scenarios/validate", "/api/jobs"])
@pytest.mark.parametrize(
    "seed",
    [42, True, "01", "+42", " 42", "42.0", "9223372036854775808"],
)
def test_write_endpoints_require_a_canonical_decimal_seed_string(
    tmp_path: Path,
    path: str,
    seed: object,
) -> None:
    client, root = _client(tmp_path)
    payload = _tiny_draft(run_id="invalid-seed")
    payload["settings"]["seed"] = seed

    with client:
        response = _secure_post(client, path, payload)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid-fields",
        "message": "One or more request fields are invalid.",
        "fields": {
            "settings.seed": ("Enter a whole-number seed from 0 through 9223372036854775807.")
        },
    }
    assert _snapshot(root) == ()


def test_maximum_seed_remains_an_exact_string_in_validation_jobs_and_limits(
    tmp_path: Path,
) -> None:
    maximum = "9223372036854775807"
    client, _ = _client(tmp_path)
    validation_payload = _tiny_draft(run_id="maximum-seed-validation")
    validation_payload["settings"]["seed"] = maximum
    job_payload = _tiny_draft(run_id="maximum-seed-job")
    job_payload["settings"]["seed"] = maximum

    with client:
        capabilities = client.get("/api/workbench")
        validation = _post(client, validation_payload)
        accepted = _secure_post(client, "/api/jobs", job_payload)
        completed = _await_job(client, accepted.json()["status_url"])

    assert capabilities.status_code == validation.status_code == 200
    assert capabilities.json()["limits"]["seed"] == {
        "minimum": "0",
        "maximum": maximum,
    }
    assert validation.json()["run"]["seed"] == maximum
    assert accepted.status_code == 202
    assert accepted.json()["job"]["accepted_settings"]["seed"] == maximum
    assert completed["accepted_settings"]["seed"] == maximum
    assert completed["result"]["seed"] == maximum


@pytest.mark.parametrize(
    ("mutation", "status", "code", "field"),
    [
        ("unknown-city", 404, "unknown-city", "city_id"),
        (
            "unknown-template",
            404,
            "unknown-creative-template",
            "scenario.campaign.creative_template_id",
        ),
        ("unknown-road", 400, "invalid-road-placement", "scenario.roadside.road_id"),
        ("invalid-day", 400, "invalid-schedule", "scenario.phone.active_windows"),
    ],
)
def test_validation_maps_known_domain_refusals_without_echoing_values(
    tmp_path: Path,
    mutation: str,
    status: int,
    code: str,
    field: str,
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft(phone=True, roadside=True)
    if mutation == "unknown-city":
        payload["city_id"] = "missing-city"
    elif mutation == "unknown-template":
        payload["scenario"]["campaign"]["creative_template_id"] = "missing-template"
    elif mutation == "unknown-road":
        payload["scenario"]["roadside"]["road_id"] = "missing-road"
    else:
        payload["settings"]["days"] = 1
    before = _snapshot(root)

    with client:
        response = _post(client, payload)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]["fields"]) == {field}
    assert "missing" not in response.text
    assert _artifact_snapshot(root) == before == ()


def test_validation_maps_strict_json_and_pydantic_failures_without_input_echo(
    tmp_path: Path,
) -> None:
    client, root = _client(tmp_path)
    secret = "Authorization: Bearer sk-never-return-this-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    payload = _valid_draft()
    payload["settings"]["agent_count"] = True
    payload["scenario"]["name"] = absolute_path
    payload["api_key"] = secret
    headers = {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-AdLife-CSRF": TOKEN,
    }
    before = _snapshot(root)
    with client:
        malformed = client.post(
            "/api/scenarios/validate",
            headers=headers,
            content=b'{"api_key":"sk-do-not-echo",',
        )
        oversized = client.post(
            "/api/scenarios/validate",
            headers=headers,
            content=b"x" * 131_073,
        )
        invalid = _post(client, payload)
        unknown = client.get("/api/not-a-route")

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "malformed-json"
    assert "sk-do-not-echo" not in malformed.text
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request-too-large"
    assert invalid.status_code == 422
    fields = invalid.json()["error"]["fields"]
    assert fields == {"request": "Request data cannot contain credential fields."}
    assert secret not in invalid.text
    assert absolute_path not in invalid.text
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not-found"
    assert _snapshot(root) == before == ()


@pytest.mark.parametrize(
    "path",
    [
        ("scenario", "scenario_id"),
        ("scenario", "campaign", "campaign_id"),
    ],
)
def test_validation_refuses_secret_identifiers_without_echo(
    tmp_path: Path,
    path: tuple[str, ...],
) -> None:
    client, root = _client(tmp_path)
    secret = "sk-" + "a" * 32
    payload = _valid_draft()
    target: Any = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = secret

    with client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert secret not in response.text
    assert _snapshot(root) == ()


@pytest.mark.parametrize("unknown_key", ["Unknown", "sk-" + "a" * 32])
def test_validation_maps_unknown_keys_to_constant_field_without_echo(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    unknown_key: str,
) -> None:
    client, root = _client(tmp_path)
    secret_value = "Authorization: Bearer sk-never-log-this-1234567890"
    payload = _valid_draft()
    payload[unknown_key] = secret_value

    with caplog.at_level(logging.ERROR), client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["fields"] == {"request": "Unknown field."}
    combined = response.text + caplog.text
    assert unknown_key not in response.json()["error"]["fields"]
    assert unknown_key not in caplog.text
    if unknown_key.startswith("sk-"):
        assert unknown_key not in response.text
    assert secret_value not in combined
    assert "Traceback" not in combined
    assert _snapshot(root) == ()


def _schema4_provider_draft() -> dict[str, Any]:
    from tests.unit.city.test_workbench_transport_v4 import draft_v4_data

    payload = draft_v4_data(mode="local", limit=20)
    payload["settings"]["seed"] = str(payload["settings"]["seed"])
    payload["cognition"]["provider_profile_id"] = "local-ollama"
    payload["cognition"]["provider_revision_sha256"] = "a" * 64
    return payload


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("effect_scope", "explanation-only", "This field is invalid."),
        ("mode", "cloud-provider", "This field is invalid."),
        ("provider_profile_id", "Not A Profile", "This field is invalid."),
        ("provider_revision_sha256", "not-a-revision", "This field is invalid."),
        ("funded_response_limit", 15, "This field is invalid."),
        ("aggregate_output_token_ceiling", 1000, "Unknown field."),
    ],
)
def test_schema4_cognition_field_errors_name_their_exact_control_path(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    # Fixed draft field names are echoed so the browser focuses the matching control;
    # submitted values never are.
    payload = _schema4_provider_draft()
    payload["cognition"][field] = value
    client, root = _client(tmp_path)

    with client:
        accepted = _post(client, _schema4_provider_draft())
        before = _snapshot(root)
        response = _post(client, payload)

    assert accepted.status_code == 200
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert response.json()["error"]["fields"] == {f"cognition.{field}": message}
    if isinstance(value, str):
        assert value not in response.text
    assert _snapshot(root) == before


def test_cognition_section_and_schema3_budget_errors_keep_their_control_paths(
    tmp_path: Path,
) -> None:
    missing = _schema4_provider_draft()
    missing.pop("cognition")
    legacy = draft_v3_data(mode="local")
    legacy["settings"]["seed"] = str(legacy["settings"]["seed"])
    legacy["cognition"]["aggregate_output_token_ceiling"] = -1
    client, _ = _client(tmp_path)

    with client:
        responses = (_post(client, missing), _post(client, legacy))

    assert [response.status_code for response in responses] == [422, 422]
    assert responses[0].json()["error"]["fields"] == {"cognition": "This field is required."}
    assert responses[1].json()["error"]["fields"] == {
        "cognition.aggregate_output_token_ceiling": "This field is invalid."
    }


@pytest.mark.parametrize("activities", [[{}], [[]]])
def test_validation_maps_malformed_activity_members_to_422(
    tmp_path: Path,
    activities: list[object],
) -> None:
    client, root = _client(tmp_path)
    payload = _valid_draft()
    payload["scenario"]["phone"]["eligible_activities"] = activities

    with client:
        response = _post(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-fields"
    assert _snapshot(root) == ()


def test_request_validation_and_unexpected_failures_are_screened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _module()
    client, root = _client(tmp_path)
    secret = "sk-unexpected-secret-value-1234567890"
    absolute_path = r"C:\private\workspace\run.json"
    before = _snapshot(root)

    def request_failure(_draft: object, *, selected_pack: object) -> None:
        del selected_pack
        raise RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "settings", "seed"),
                    "msg": "Field required",
                    "input": secret,
                }
            ]
        )

    monkeypatch.setattr(module, "construct_workbench_run", request_failure)
    with client:
        request_response = _post(client, _valid_draft())
    assert request_response.status_code == 422
    assert request_response.json()["error"]["fields"] == {
        "settings.seed": "This field is required."
    }
    assert secret not in request_response.text

    def unexpected_failure(_draft: object, *, selected_pack: object) -> None:
        del selected_pack
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(f"at {absolute_path}") from cause

    monkeypatch.setattr(module, "construct_workbench_run", unexpected_failure)
    with caplog.at_level(logging.ERROR), client:
        unexpected_response = _post(client, _valid_draft())
    assert unexpected_response.status_code == 500
    assert unexpected_response.json()["error"] == {
        "code": "internal-error",
        "message": "The workbench could not complete the request.",
        "fields": {},
    }
    combined = unexpected_response.text + caplog.text
    assert secret not in combined
    assert absolute_path not in combined
    assert "Traceback" not in combined
    assert _artifact_snapshot(root) == before == ()


def test_validation_reaches_no_network_store_worker_or_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root = _client(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden validation side effect")

    before = _snapshot(root)
    with client:
        monkeypatch.setattr(socket.socket, "connect", refuse)
        monkeypatch.setattr(CityRunStore, "__init__", refuse)
        response = _post(client, _valid_draft(phone=True, roadside=True))

    assert response.status_code == 200
    assert _artifact_snapshot(root) == before == ()
    assert client.app.router.on_startup == []
    assert client.app.router.on_shutdown == []
    assert not (root / "city-runs").exists()


def test_shell_assets_are_local_escaped_honest_and_csp_restricted(tmp_path: Path) -> None:
    hostile_token = 'token"><script>alert("x")</script>'
    client, _ = _client(tmp_path, token=hostile_token)
    with client:
        shell = client.get("/")
        css = client.get("/assets/workbench.css")
        javascript = client.get("/assets/workbench.js")
        icon = client.get("/assets/workbench-icon.svg")
        hostile_host = client.get("/", headers={"Host": "example.test"})

    assert shell.status_code == 200
    assert shell.headers["content-type"].startswith("text/html")
    assert shell.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    assert hostile_token not in shell.text
    assert "&quot;&gt;&lt;script&gt;" in shell.text
    assert '<link rel="stylesheet" href="/assets/workbench.css">' in shell.text
    assert '<link rel="icon" href="/assets/workbench-icon.svg" type="image/svg+xml">' in shell.text
    assert shell.text.count('<script defer src="/assets/workbench.js"></script>') == 1
    assert "<script>" not in shell.text.lower()
    assert "onclick=" not in shell.text.lower()
    assert 'class="skip-link"' in shell.text
    assert 'id="synthetic-disclosure"' in shell.text
    assert '<h1 id="city-title">Choose a city</h1>' in shell.text
    assert ">Synthetic model</strong>" in shell.text
    assert "Fictional people and modeled responses" in shell.text
    assert 'aria-label="City study stages"' in shell.text
    assert [
        shell.text.count(f'class="stage-name">{stage}</b>')
        for stage in (
            "CITY",
            "CAMPAIGN",
            "RUN",
            "INSPECT",
        )
    ] == [1, 1, 1, 1]
    assert 'id="city-map"' in shell.text
    assert 'id="city-map-alternative"' in shell.text
    assert ">Map details</summary>" in shell.text
    assert "<summary>Data &amp; integrity</summary>" in shell.text
    assert 'id="completed-runs"' in shell.text
    assert "not observed behavior, real sales" in shell.text
    assert "CAUSAL THREAD" not in shell.text
    assert "Compose bounded campaign assumptions" not in shell.text
    for control in ("<input", "<select", "<button"):
        assert control in shell.text.lower()
    assert "http://" not in shell.text
    assert "https://" not in shell.text

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "@import" not in css.text.lower()
    assert "@font-face" not in css.text.lower()
    assert "http://" not in css.text
    assert "https://" not in css.text
    assert "url(" not in css.text.lower()

    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert "innerHTML" not in javascript.text
    assert "insertAdjacentHTML" not in javascript.text
    assert "localStorage" not in javascript.text
    assert "sessionStorage" not in javascript.text
    assert "http://" not in javascript.text
    assert "https://" not in javascript.text

    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.text.startswith("<svg")
    assert "<script" not in icon.text.lower()
    assert "<foreignobject" not in icon.text.lower()
    assert "href=" not in icon.text.lower()
    assert hostile_host.status_code == 400
    for response in (shell, css, javascript, icon, hostile_host):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers


def _saved_v10_workspace(root: Path, *runs: tuple[str, str]) -> Path:
    """Publish verified V10 runs through the real pure pipeline into one workspace."""
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10
    from tests.unit.city.test_workbench_cognition_views import dense_phone

    workspace = root / "workspace"
    prepare_workbench_workspace(workspace)
    for run_id, mode in runs:
        _saved_v10(
            workspace,
            mode=mode,
            run_id=run_id,
            agents=4 if mode == "mock" else 3,
            mutation=dense_phone if mode == "mock" else None,
        )
    return workspace


@pytest.fixture(scope="module")
def v10_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _saved_v10_workspace(
        tmp_path_factory.mktemp("v10-workbench"),
        ("inspect-mock", "mock"),
        ("inspect-local", "local"),
    )


def _view_token(page: Any) -> dict[str, str]:
    match = re.search(r'<meta name="adlife-view-token" content="([A-Za-z0-9_-]{43})">', page.text)
    assert match is not None
    return {"X-AdLife-View-Token": match.group(1)}


V10_SUFFIXES = (
    "meta",
    "city",
    "agents",
    "opportunity-summary",
    "attention-summary",
    "spatial-metrics",
    "spatial-response-metrics",
    "response-summary",
    "response-state?agent_id=person-001&limit=2",
    "workbench-input",
    "cognition-summary",
    "cognition-records",
)


@pytest.mark.parametrize("run_id", ["inspect-mock", "inspect-local"])
def test_v10_inspector_reads_match_the_standalone_viewer_exactly(
    v10_workspace: Path,
    run_id: str,
) -> None:
    from tests.unit.city.test_city_web import _v10_view_arguments

    stored = CityRunStore(v10_workspace).load(run_id)
    before = _artifact_snapshot(v10_workspace)
    standalone = create_city_app(stored.mobility, **_v10_view_arguments(stored))
    workbench = _module().create_city_workbench_app(
        prepare_workbench_workspace(v10_workspace),
        csrf_token=TOKEN,
    )
    minute = next(
        record.model_minute
        for record in stored.response_evaluation.records
        if record.event_type == "spatial.dual-state-updated"
    )
    timeline = f"timeline?minute={minute}&agent_id=person-001&response_offset=0&limit=3"
    suffixes = (*V10_SUFFIXES, f"response-events?minute={minute}&limit=2", timeline)

    with (
        TestClient(workbench, base_url=ORIGIN) as scoped,
        TestClient(standalone, base_url=ORIGIN) as single,
    ):
        page = scoped.get(f"/runs/{run_id}")
        assert page.status_code == 200
        assert f'content="/api/runs/{run_id}"' in page.text
        headers = _view_token(page)
        for suffix in suffixes:
            expected = single.get(f"/api/{suffix}")
            assert expected.status_code == 200, suffix
            # Pinned reads cover every route; fresh verification covers each family.
            fresh = suffix in {"meta", "cognition-summary", "workbench-input", timeline}
            for request_headers in (headers, {}) if fresh else (headers,):
                actual = scoped.get(f"/api/runs/{run_id}/{suffix}", headers=request_headers)
                assert actual.status_code == 200, suffix
                assert actual.json() == expected.json(), suffix
        meta = scoped.get(f"/api/runs/{run_id}/meta", headers=headers).json()

    assert meta["run_schema_version"] == 10
    assert meta["bounded_cognition"] is True
    assert meta["cognition_effect_scope"] == "bounded-signals-v1"
    assert meta["response_model_id"] == "spatial-response-v2"
    assert meta["workbench_input_sha256"] == stored.manifest.workbench_input_sha256
    assert _artifact_snapshot(v10_workspace) == before


def test_v10_snapshot_pins_one_verified_load_and_reload_reverifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_v10_workspace(tmp_path, ("pinned-v10", "mock"))
    module = _module()
    original = module.WorkbenchRunRepository.load
    loaded: list[str] = []

    def counted_load(repository: object, candidate: str):
        loaded.append(candidate)
        return original(repository, candidate)

    app = module.create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN)
    with TestClient(app, base_url=ORIGIN) as client:
        page = client.get("/runs/pinned-v10")
        headers = _view_token(page)
        monkeypatch.setattr(module.WorkbenchRunRepository, "load", counted_load)
        first = {
            suffix: client.get(f"/api/runs/pinned-v10/{suffix}", headers=headers)
            for suffix in V10_SUFFIXES
        }
        assert loaded == []
        # The preferred timeline projection verifies a tokenless run exactly once.
        timeline = client.get(
            "/api/runs/pinned-v10/timeline",
            params={"minute": 480, "agent_id": "person-001", "limit": 1},
        )
        assert timeline.status_code == 200 and loaded == ["pinned-v10"]
        assert timeline.json()["response_page"]["schema_version"] == 2
        artifact = root / "city-runs" / "pinned-v10" / "outputs" / "cognition.jsonl"
        artifact.write_bytes(artifact.read_bytes() + b" ")
        pinned = {
            suffix: client.get(f"/api/runs/pinned-v10/{suffix}", headers=headers)
            for suffix in V10_SUFFIXES
        }
        fresh = client.get("/api/runs/pinned-v10/cognition-summary")
        reload = client.get("/runs/pinned-v10")

    assert all(response.status_code == 200 for response in first.values())
    assert {suffix: response.content for suffix, response in pinned.items()} == {
        suffix: response.content for suffix, response in first.items()
    }
    assert fresh.status_code == reload.status_code == 409
    assert fresh.json()["error"]["code"] == "run-unavailable"
    assert loaded == ["pinned-v10", "pinned-v10", "pinned-v10"]
    assert str(root) not in "".join(response.text for response in first.values())


def test_v10_tokens_stay_bound_to_their_run_under_interleaved_requests(
    v10_workspace: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = CityRunStore(v10_workspace)
    runs = {run_id: store.load(run_id) for run_id in ("inspect-mock", "inspect-local")}
    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(v10_workspace),
        csrf_token=TOKEN,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        tokens = {run_id: _view_token(client.get(f"/runs/{run_id}")) for run_id in runs}
        stale = client.get("/api/runs/inspect-local/meta", headers=tokens["inspect-mock"])
        requests = [
            (run_id, minute)
            for minute in (0, 480, 600, 1500, 2000)
            for run_id in runs
            for _ in range(2)
        ]

        def fetch(request: tuple[str, int]) -> tuple[str, int, dict[str, Any], dict[str, Any]]:
            run_id, minute = request
            timeline = client.get(
                f"/api/runs/{run_id}/timeline",
                params={"minute": minute, "agent_id": "person-001", "limit": 100},
                headers=tokens[run_id],
            )
            records = client.get(f"/api/runs/{run_id}/cognition-records", headers=tokens[run_id])
            assert timeline.status_code == records.status_code == 200
            return run_id, minute, timeline.json(), records.json()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, requests))

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "run-unavailable"
    for run_id, minute, timeline, records in results:
        stored = runs[run_id]
        expected_input = stored.cognition_input.fingerprint
        assert timeline["minute"] == timeline["response_page"]["minute"] == minute
        assert records["run_id"] == run_id
        assert records["cognition_input_sha256"] == expected_input
        assert all(
            item["cognition_input_sha256"] == expected_input
            for item in timeline["response_page"]["items"]
        )
        assert timeline["response_state_page"]["cognition_input_sha256"] == expected_input


def test_v10_reads_never_touch_providers_cache_keyring_or_writers(
    v10_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring

    from adlife.adapters.cognition.city_cache_v2 import CityCognitionCacheV2
    from adlife.city import run_store

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a read-only V10 projection attempted a side effect")

    app = _module().create_city_workbench_app(
        prepare_workbench_workspace(v10_workspace),
        csrf_token=TOKEN,
        provider_factory=forbidden,
        provider_factory_v2=forbidden,
    )
    before = _artifact_snapshot(v10_workspace)
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, forbidden)
    for name in ("get", "put"):
        monkeypatch.setattr(CityCognitionCacheV2, name, forbidden, raising=False)
    monkeypatch.setattr(run_store.CityRunStore, "save", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    with TestClient(app, base_url=ORIGIN) as client:
        for run_id in ("inspect-mock", "inspect-local"):
            page = client.get(f"/runs/{run_id}")
            assert page.status_code == 200
            headers = _view_token(page)
            for suffix in (*V10_SUFFIXES, "timeline?minute=480&agent_id=person-001"):
                response = client.get(f"/api/runs/{run_id}/{suffix}", headers=headers)
                assert response.status_code == 200, suffix
            for suffix in ("meta", "cognition-records", "workbench-input"):
                assert client.get(f"/api/runs/{run_id}/{suffix}").status_code == 200, suffix
    assert _artifact_snapshot(v10_workspace) == before


def test_v10_workbench_input_and_cognition_routes_refuse_tampering_without_leaks(
    tmp_path: Path,
) -> None:
    root = _saved_v10_workspace(tmp_path, ("tampered-v10", "mock"))
    app = _module().create_city_workbench_app(prepare_workbench_workspace(root), csrf_token=TOKEN)
    sidecar = root / "city-runs" / "tampered-v10" / "inputs" / "workbench.json"
    with TestClient(app, base_url=ORIGIN) as client:
        frozen = client.get("/api/runs/tampered-v10/workbench-input")
        assert frozen.status_code == 200
        assert frozen.json()["run_schema_version"] == 10
        assert frozen.json()["workbench_input_schema_version"] == 4
        assert frozen.json()["cognition"]["effect_scope"] == "bounded-signals-v1"
        assert frozen.headers["content-security-policy"].startswith("default-src 'none'")
        sidecar.write_bytes(sidecar.read_bytes() + b" ")
        refused = [
            client.get(f"/api/runs/tampered-v10/{suffix}")
            for suffix in ("workbench-input", "cognition-summary", "cognition-records", "meta")
        ]
        page = client.get("/runs/tampered-v10")

    assert all(response.status_code == 409 for response in (*refused, page))
    assert all(response.json()["error"]["code"] == "run-unavailable" for response in refused)
    assert all(str(root) not in response.text for response in (*refused, page))
