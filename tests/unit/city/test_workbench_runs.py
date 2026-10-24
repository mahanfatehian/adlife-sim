"""Verified workbench run discovery stays bounded, fresh, and path-free."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    prepare_workbench_workspace,
)
from adlife.core.domain.city_cognition_v2 import CURRENT_CITY_COGNITION_CAUSAL_CONTRACT
from adlife.core.ports.run_store import DuplicateRun, UnsafeRunLocation
from tests.integration.test_city_cognition_v2_artifacts import V10_PATHS
from tests.unit.city.test_city_run_preparation import _validated
from tests.unit.city.test_workbench_validation import persona_draft


def _module():
    return importlib.import_module("adlife.city.workbench_runs")


def _provider_v2_case(tmp_path: Path, *, factory):
    from adlife.city.provider_compatibility import (
        WorkbenchProviderCompatibilityRecord,
        WorkbenchProviderCompatibilityRegistry,
        build_workbench_provider_compatibility_key,
    )
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.unit.city.test_city_run_preparation import _validated_v4
    from tests.unit.city.test_provider_profiles import _draft

    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    validated = _validated_v4(
        mode="local",
        limit=10,
        profile=profile,
        agents=1,
    )
    registry = WorkbenchProviderCompatibilityRegistry()
    registry.record(
        WorkbenchProviderCompatibilityRecord(
            key=build_workbench_provider_compatibility_key(
                profile,
                effect_scope="bounded-signals-v1",
            ),
            status="compatible",
            reason_code=None,
        )
    )
    repository = _module().WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        compatibility_registry=registry,
        provider_factory_v2=factory,
    )
    operation = repository.preflight_cognition_v2(validated)
    staged = repository.prepare_scientific_v2(validated, operation)
    return repository, operation, staged


@pytest.mark.parametrize("authorization", ["missing", "v1", "failed", "v2"])
def test_schema4_preflight_requires_exact_compatibility_before_endpoint_or_credential(
    tmp_path, authorization
):
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.unit.city.test_provider_profiles import _draft
    from tests.unit.city.test_workbench_transport_v4 import compiled_v4

    module = importlib.import_module("adlife.city.provider_compatibility")
    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    # A new run, frozen as the server freezes it: the current contract and its default limit.
    validated = compiled_v4(
        mode="local",
        mutation=lambda payload: payload["cognition"].update(
            provider_revision_sha256=profile.revision_sha256
        ),
        causal_contract=CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    )
    registry = module.WorkbenchProviderCompatibilityRegistry()
    key = module.build_workbench_provider_compatibility_key(
        profile, effect_scope="explanation-only" if authorization == "v1" else "bounded-signals-v1"
    )
    if authorization != "missing":
        registry.record(
            module.WorkbenchProviderCompatibilityRecord(
                key=key,
                status="incompatible" if authorization == "failed" else "compatible",
                reason_code="invalid-result" if authorization == "failed" else None,
            )
        )
    calls = []

    class Endpoints:
        def bind(self, profile):
            calls.append("endpoint")
            assert authorization == "v2", "compatibility must precede endpoint binding"
            return "endpoint"

    class Credentials:
        def resolve(self, profile):
            calls.append("credential")
            assert calls == ["endpoint", "credential"]
            return None

    repository = _module().WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        endpoint_policy=Endpoints(),
        credential_store=Credentials(),
        compatibility_registry=registry,
    )
    if authorization == "v2":
        operation = repository.preflight_cognition_v2(validated)
        assert operation.profile == profile and calls == ["endpoint", "credential"]
    else:
        with pytest.raises(module.WorkbenchProviderCompatibilityRequired):
            repository.preflight_cognition_v2(validated)
        assert calls == []
    assert not (workspace.root / "city-runs").exists()


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_schema4_offline_preflight_bypasses_profiles_and_compatibility(tmp_path, mode):
    from tests.unit.city.test_workbench_transport_v4 import compiled_v4

    class Forbidden:
        def __getattr__(self, name):
            pytest.fail("offline preflight touched provider infrastructure")

    repository = _module().WorkbenchRunRepository(
        prepare_workbench_workspace(tmp_path / "state"),
        provider_registry=Forbidden(),
        endpoint_policy=Forbidden(),
        credential_store=Forbidden(),
        compatibility_registry=Forbidden(),
    )
    operation = repository.preflight_cognition_v2(
        compiled_v4(mode=mode, causal_contract=CURRENT_CITY_COGNITION_CAUSAL_CONTRACT)
    )
    assert operation.profile is operation.endpoint is operation.credential is None


def test_summary_projects_complete_verified_v10_cognition_evidence(tmp_path: Path) -> None:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    staged, prepared, _directory = _saved_v10(
        tmp_path,
        mode="rules",
        agents=1,
        days=2,
        run_id="summary-v10",
    )
    stored = (
        _module().WorkbenchRunRepository(prepare_workbench_workspace(tmp_path)).load("summary-v10")
    )
    summary = _module()._summary(stored)
    cognition = prepared.cognition_evaluation.summary
    coverage = cognition.coverage
    outcomes = cognition.outcomes
    neutralization = cognition.neutralization

    assert summary.model_dump(mode="json") == {
        "schema_version": 1,
        "run_id": "summary-v10",
        "run_schema_version": 10,
        "workbench_input_schema_version": 4,
        "city_id": stored.pack.city_id,
        "city_schema_version": 2,
        "city_sha256": stored.manifest.city_sha256,
        "scenario_id": stored.spatial_scenario.scenario_id,
        "scenario_sha256": stored.spatial_scenario.fingerprint,
        "campaign_ids": ["fictional-launch"],
        "channels": ["mobile-feed"],
        "seed": "42",
        "days": 2,
        "agent_count": 1,
        "frame_count": 2_880,
        "opportunity_count": stored.manifest.opportunity_count,
        "impression_count": stored.manifest.impression_count,
        "noticed_count": stored.manifest.noticed_count,
        "response_count": stored.manifest.response_count,
        "effect_scope": "bounded-signals-v1",
        "cognition_mode": "rules",
        "provider_profile_id": None,
        "provider_revision_sha256": None,
        "funded_response_limit": 60,
        "aggregate_output_token_ceiling": 0,
        "eligible_response_count": coverage.eligible_response_count,
        "selected_request_count": coverage.selected_request_count,
        "funded_request_count": coverage.funded_request_count,
        "budget_skipped_count": coverage.budget_skipped_count,
        "unselected_response_count": coverage.unselected_response_count,
        "applied_response_count": coverage.applied_response_count,
        "rules_count": outcomes.rules,
        "mock_count": outcomes.mock,
        "provider_count": outcomes.provider,
        "cache_count": outcomes.cache,
        "fallback_count": outcomes.fallback,
        "neutralized_response_count": neutralization.neutralized_response_count,
        "uncertain_result_count": neutralization.uncertain_result_count,
        "safety_neutralized_result_count": neutralization.safety_neutralized_result_count,
        "inspector_url": "/runs/summary-v10",
    }
    assert summary.response_count == summary.applied_response_count
    assert summary.selected_request_count == len(staged.cognition_requests)
    assert summary.inspector_url == "/runs/summary-v10"


def test_legacy_summary_json_shape_does_not_gain_v10_keys(tmp_path: Path) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="legacy-shape", workbench=True)
    summary = _module().WorkbenchRunRepository(workspace).verified_summary("legacy-shape")
    document = summary.model_dump(mode="json")
    encoded = summary.model_dump_json()
    for name in (
        "workbench_input_schema_version",
        "effect_scope",
        "funded_response_limit",
        "eligible_response_count",
        "selected_request_count",
        "funded_request_count",
        "budget_skipped_count",
        "unselected_response_count",
        "applied_response_count",
        "rules_count",
        "mock_count",
        "provider_count",
        "cache_count",
        "fallback_count",
        "neutralized_response_count",
        "uncertain_result_count",
        "safety_neutralized_result_count",
    ):
        assert name not in document
        assert f'"{name}"' not in encoded


@pytest.mark.parametrize("mode", ["rules", "mock"])
def test_repository_v2_lifecycle_keeps_preparation_and_finalization_separate(
    tmp_path: Path,
    mode: str,
) -> None:
    from adlife.adapters.cognition.city_service import CityCognitionCancellation
    from adlife.city.runs import PreparedCityRun, PreparedCityScientificRunV2
    from tests.unit.city.test_city_run_preparation import _validated_v4

    workspace = prepare_workbench_workspace(tmp_path / mode)
    repository = _module().WorkbenchRunRepository(workspace)
    validated = _validated_v4(
        mode=mode,
        agents=1,
        mutation=lambda document: document["settings"].update(run_id=f"repository-{mode}-v10"),
    )

    operation = repository.preflight_cognition_v2(validated)
    staged = repository.prepare_scientific_v2(validated, operation)
    assert type(staged) is PreparedCityScientificRunV2
    assert not (workspace.root / "city-runs").exists()

    evaluation = repository.resolve_cognition_v2(
        staged,
        operation,
        CityCognitionCancellation(),
    )
    assert evaluation.cognition_input_sha256 == staged.cognition_input.fingerprint
    assert not isinstance(evaluation, PreparedCityRun)
    prepared = repository.finalize_cognition_v2(staged, evaluation)
    assert type(prepared) is PreparedCityRun
    assert prepared.manifest.schema_version == 10
    assert not (workspace.root / "city-runs").exists()

    repository.publish(prepared)
    restored = repository.load(prepared.manifest.run_id)
    assert restored.cognition_input == prepared.cognition_input
    assert restored.cognition_evaluation == prepared.cognition_evaluation
    assert repository.verified_summary(prepared.manifest.run_id).run_schema_version == 10


def test_repository_v2_provider_uses_exact_factory_service_baseline_and_clears_secret(
    tmp_path: Path,
) -> None:
    from adlife.city.provider_compatibility import (
        WorkbenchProviderCompatibilityRecord,
        WorkbenchProviderCompatibilityRegistry,
        build_workbench_provider_compatibility_key,
    )
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.integration.test_city_cognition_v2_pipeline import _terminal_evaluation
    from tests.unit.city.test_city_run_preparation import _validated_v4
    from tests.unit.city.test_provider_profiles import _draft

    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    validated = _validated_v4(
        mode="local",
        limit=10,
        profile=profile,
        agents=1,
        mutation=lambda document: document["settings"].update(run_id="provider-repository-v10"),
    )
    registry = WorkbenchProviderCompatibilityRegistry()
    registry.record(
        WorkbenchProviderCompatibilityRecord(
            key=build_workbench_provider_compatibility_key(
                profile, effect_scope="bounded-signals-v1"
            ),
            status="compatible",
            reason_code=None,
        )
    )
    factory_calls: list[dict[str, object]] = []
    service_calls: list[dict[str, object]] = []

    class Provider:
        closed = False

        async def call(self, request):  # pragma: no cover - fake service owns this provider
            raise AssertionError("injected service must not call the provider")

        async def repair_call(self, request, *, context):  # pragma: no cover
            raise AssertionError("injected service must not repair")

        async def aclose(self) -> None:
            self.closed = True

    provider = Provider()

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return provider

    class Service:
        evaluation = None

        async def resolve_many(
            self,
            cognition_input,
            requests,
            *,
            baseline_evaluation,
            provider,
            cancellation,
            progress,
        ):
            service_calls.append(
                {
                    "cognition_input": cognition_input,
                    "requests": requests,
                    "baseline_evaluation": baseline_evaluation,
                    "provider": provider,
                    "cancellation": cancellation,
                    "progress": progress,
                }
            )
            await provider.aclose()
            return self.evaluation

    service = Service()
    repository = _module().WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        compatibility_registry=registry,
        cognition_service_v2=service,
        provider_factory_v2=factory,
    )
    operation = repository.preflight_cognition_v2(validated)
    assert operation.credential is not None and not operation.credential.secret.cleared
    staged = repository.prepare_scientific_v2(validated, operation)
    service.evaluation = _terminal_evaluation(staged)

    def progress() -> None:  # pragma: no cover - the fake service never resolves a request
        raise AssertionError("the fake service reports no progress")

    evaluation = repository.resolve_cognition_v2(
        staged,
        operation,
        importlib.import_module(
            "adlife.adapters.cognition.city_service"
        ).CityCognitionCancellation(),
        progress=progress,
    )

    assert evaluation == service.evaluation
    assert len(factory_calls) == len(service_calls) == 1
    assert factory_calls[0]["cognition_input"] == staged.cognition_input
    assert service_calls[0]["baseline_evaluation"] == staged.baseline_evaluation
    assert service_calls[0]["provider"] is provider
    # The repository hands the job worker's progress hook to the service unchanged.
    assert service_calls[0]["progress"] is progress
    assert provider.closed is True
    assert operation.credential.secret.cleared is True


def test_repository_v2_factory_defect_clears_operation_credential(tmp_path: Path) -> None:
    from adlife.city.provider_compatibility import (
        WorkbenchProviderCompatibilityRecord,
        WorkbenchProviderCompatibilityRegistry,
        build_workbench_provider_compatibility_key,
    )
    from adlife.city.provider_profiles import ProviderProfileRegistry
    from tests.unit.city.test_city_run_preparation import _validated_v4
    from tests.unit.city.test_provider_profiles import _draft

    workspace = prepare_workbench_workspace(tmp_path / "state")
    profiles = ProviderProfileRegistry(workspace)
    profile = profiles.create(_draft(profile_id="provider-main"))
    validated = _validated_v4(mode="local", limit=10, profile=profile, agents=1)
    registry = WorkbenchProviderCompatibilityRegistry()
    registry.record(
        WorkbenchProviderCompatibilityRecord(
            key=build_workbench_provider_compatibility_key(
                profile, effect_scope="bounded-signals-v1"
            ),
            status="compatible",
            reason_code=None,
        )
    )

    def broken_factory(**_kwargs):
        raise RuntimeError("private provider factory defect")

    repository = _module().WorkbenchRunRepository(
        workspace,
        provider_registry=profiles,
        compatibility_registry=registry,
        provider_factory_v2=broken_factory,
    )
    operation = repository.preflight_cognition_v2(validated)
    staged = repository.prepare_scientific_v2(validated, operation)
    with pytest.raises(RuntimeError, match="factory defect"):
        repository.resolve_cognition_v2(
            staged,
            operation,
            importlib.import_module(
                "adlife.adapters.cognition.city_service"
            ).CityCognitionCancellation(),
        )
    assert operation.credential is not None and operation.credential.secret.cleared


def test_repository_v2_closes_invalid_factory_resource_before_clearing_secret(
    tmp_path: Path,
) -> None:
    from adlife.adapters.cognition.city_service import CityCognitionCancellation

    class InvalidProviderResource:
        secret = None
        closed_while_secret_live = False

        async def aclose(self) -> None:
            assert self.secret is not None
            self.closed_while_secret_live = not self.secret.cleared

    resource = InvalidProviderResource()
    repository, operation, staged = _provider_v2_case(
        tmp_path,
        factory=lambda **_kwargs: resource,
    )
    assert operation.credential is not None
    resource.secret = operation.credential.secret
    with pytest.raises(TypeError, match="runtime contract"):
        repository.resolve_cognition_v2(
            staged,
            operation,
            CityCognitionCancellation(),
        )
    assert resource.closed_while_secret_live is True
    assert operation.credential.secret.cleared


@pytest.mark.parametrize("defect", ["cancellation", "binding"])
def test_repository_v2_validation_defect_always_clears_operation_credential(
    tmp_path: Path,
    defect: str,
) -> None:
    from adlife.adapters.cognition.city_service import CityCognitionCancellation

    repository, operation, staged = _provider_v2_case(
        tmp_path,
        factory=lambda **_kwargs: pytest.fail("validation defect reached the provider factory"),
    )
    assert operation.credential is not None
    cancellation: object = CityCognitionCancellation()
    if defect == "cancellation":
        cancellation = object()
    else:
        assert operation.endpoint is not None
        operation.endpoint = replace(
            operation.endpoint,
            base_url="http://127.0.0.1:9999/v1",
        )

    with pytest.raises((TypeError, ValueError)):
        repository.resolve_cognition_v2(staged, operation, cancellation)
    assert operation.credential.secret.cleared


def test_repository_v2_rebinds_complete_endpoint_before_factory_use(tmp_path: Path) -> None:
    from adlife.adapters.cognition.city_service import CityCognitionCancellation

    factory_calls: list[object] = []

    def forbidden_factory(**kwargs):
        factory_calls.append(kwargs)
        pytest.fail("fabricated endpoint reached the provider factory")

    repository, operation, staged = _provider_v2_case(tmp_path, factory=forbidden_factory)
    assert operation.endpoint is not None and operation.credential is not None
    operation.endpoint = replace(
        operation.endpoint,
        hostname="169.254.169.254",
        port=80,
        base_path="/metadata",
        host_header="169.254.169.254",
        tls_server_hostname=None,
    )

    with pytest.raises(ValueError, match="frozen provider"):
        repository.resolve_cognition_v2(
            staged,
            operation,
            CityCognitionCancellation(),
        )
    assert factory_calls == []
    assert operation.credential.secret.cleared


def test_repository_schema2_preparation_does_not_publish(tmp_path: Path) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = _module().WorkbenchRunRepository(workspace)
    prepared = repository.prepare(construct_workbench_run(persona_draft()))
    assert prepared.manifest.schema_version == 8
    assert not (workspace.root / "city-runs" / "launch-run").exists()


def _saved_run(root: Path, *, run_id: str, workbench: bool) -> None:
    validated = _validated(run_id=run_id, agents=1, days=2)
    create_city_run(
        validated.pack,
        root=root,
        run_id=run_id,
        seed=42,
        agent_count=1,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input if workbench else None,
    )


def test_empty_discovery_and_page_arguments_are_strict_and_bounded(tmp_path: Path) -> None:
    module = _module()
    repository = module.WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / "state"))

    page = repository.list_runs(offset=0, limit=20)

    assert page.model_dump(mode="json") == {
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
    for offset, limit in ((True, 20), (-1, 20), (10_001, 20), (0, False), (0, 0), (0, 101)):
        with pytest.raises(ValueError, match="pagination"):
            repository.list_runs(offset=offset, limit=limit)
    with pytest.raises(ValidationError, match="truncated"):
        module.WorkbenchRunPage(
            offset=0,
            limit=20,
            returned_count=0,
            verified_total=0,
            scanned_count=0,
            corrupt_count=0,
            truncated=True,
            runs=(),
        )


def test_discovery_freshly_verifies_v6_and_v7_and_pages_in_run_id_order(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="zulu-v6", workbench=False)
    _saved_run(workspace.root, run_id="alpha-v7", workbench=True)
    repository = module.WorkbenchRunRepository(workspace)

    first = repository.list_runs(offset=0, limit=1)
    second = repository.list_runs(offset=1, limit=1)

    assert first.returned_count == second.returned_count == 1
    assert first.verified_total == second.verified_total == 2
    assert first.scanned_count == second.scanned_count == 2
    assert [item.run_id for item in first.runs] == ["alpha-v7"]
    assert [item.run_id for item in second.runs] == ["zulu-v6"]
    assert [item.run_schema_version for item in (*first.runs, *second.runs)] == [7, 6]
    summary = first.runs[0]
    assert summary.city_id == "fictional-grid-v2"
    assert summary.scenario_id == "launch-study"
    assert summary.campaign_ids == ("fictional-launch",)
    assert summary.channels == ("mobile-feed", "roadside-billboard")
    assert summary.seed == "42"
    assert summary.days == 2
    assert summary.agent_count == 1
    assert summary.frame_count == 2_880
    assert summary.impression_count == summary.opportunity_count
    assert 0 <= summary.noticed_count <= summary.impression_count
    assert summary.response_count == summary.noticed_count
    assert summary.inspector_url == "/runs/alpha-v7"
    assert "directory" not in summary.model_dump(mode="json")

    run_manifest = workspace.root / "city-runs" / "alpha-v7" / "run.json"
    document = json.loads(run_manifest.read_text(encoding="utf-8"))
    document["trace_sha256"] = "0" * 64
    run_manifest.write_text(json.dumps(document), encoding="utf-8")
    refreshed = repository.list_runs(offset=0, limit=20)
    assert [item.run_id for item in refreshed.runs] == ["zulu-v6"]
    assert refreshed.corrupt_count == 1


def test_summary_serializes_maximum_seed_as_an_exact_decimal_string(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    maximum = 2**63 - 1
    validated = _validated(run_id="maximum-seed", seed=maximum, agents=1, days=2)
    create_city_run(
        validated.pack,
        root=workspace.root,
        run_id="maximum-seed",
        seed=maximum,
        agent_count=1,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    summary = module.WorkbenchRunRepository(workspace).verified_summary("maximum-seed")

    assert summary.seed == "9223372036854775807"
    assert summary.model_dump(mode="json")["seed"] == "9223372036854775807"


def test_summary_bounds_cover_the_largest_valid_workbench_run() -> None:
    module = _module()

    summary = module.WorkbenchRunSummary(
        run_id="maximum-workbench-run",
        run_schema_version=7,
        city_id="maximum-city",
        city_schema_version=2,
        city_sha256="1" * 64,
        scenario_id="maximum-scenario",
        scenario_sha256="2" * 64,
        campaign_ids=tuple(f"campaign-{index:02d}" for index in range(20)),
        channels=("mobile-feed", "roadside-billboard"),
        seed="9223372036854775807",
        days=7,
        agent_count=30,
        frame_count=10_080,
        opportunity_count=520_800,
        impression_count=520_800,
        noticed_count=520_800,
        response_count=520_800,
        inspector_url="/runs/maximum-workbench-run",
    )

    assert summary.agent_count == 30
    assert summary.days == 7
    assert summary.response_count == 520_800
    with pytest.raises(ValidationError, match="inspector"):
        summary.model_copy(update={"inspector_url": "/runs/a-different-run"})
    with pytest.raises(ValidationError, match="frame count"):
        summary.model_copy(update={"frame_count": 10_079})
    with pytest.raises(ValidationError, match="campaign"):
        summary.model_copy(update={"campaign_ids": ()})
    with pytest.raises(ValidationError, match="channel"):
        summary.model_copy(update={"channels": ()})

    legacy = summary.model_dump(mode="python")
    legacy.update(
        {
            "run_schema_version": 2,
            "city_schema_version": 1,
            "scenario_id": None,
            "scenario_sha256": None,
            "campaign_ids": (),
            "channels": (),
            "opportunity_count": 0,
            "impression_count": 0,
            "noticed_count": 0,
            "response_count": 0,
        }
    )
    with pytest.raises(ValidationError, match="city schema"):
        module.WorkbenchRunSummary.model_validate(legacy)


def test_discovery_excludes_partial_invalid_and_symlinked_candidates(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="valid-run", workbench=True)
    runs = workspace.root / "city-runs"
    (runs / "partial-run").mkdir()
    (runs / "not a portable id").mkdir()
    (runs / "plain-file").write_text("not a run", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = runs / "linked-run"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        linked = None

    page = module.WorkbenchRunRepository(workspace).list_runs(offset=0, limit=100)

    assert [item.run_id for item in page.runs] == ["valid-run"]
    assert page.scanned_count == 4 + int(linked is not None)
    assert page.corrupt_count == 3 + int(linked is not None)
    assert page.truncated is False


def test_discovery_scans_at_most_256_direct_candidates(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    runs = workspace.root / "city-runs"
    runs.mkdir()
    for index in range(257):
        (runs / f"partial-{index:03d}").mkdir()

    page = module.WorkbenchRunRepository(workspace).list_runs(offset=0, limit=100)

    assert page.scanned_count == 256
    assert page.corrupt_count == 256
    assert page.verified_total == 0
    assert page.truncated is True


def test_repository_rechecks_workspace_and_refuses_every_existing_run_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = module.WorkbenchRunRepository(workspace)
    run_directory = workspace.root / "city-runs" / "reserved-run"
    run_directory.mkdir(parents=True)

    with pytest.raises(DuplicateRun):
        repository.ensure_available("reserved-run")

    def replaced(_workspace):
        raise UnsafeWorkbenchWorkspace("workbench workspace identity changed")

    monkeypatch.setattr(module, "verify_workbench_workspace", replaced)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.list_runs(offset=0, limit=20),
        lambda: repository.load("reserved-run"),
    ):
        with pytest.raises(UnsafeWorkbenchWorkspace, match="identity"):
            operation()


def test_repository_prepares_publishes_and_independently_verifies_one_run(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = module.WorkbenchRunRepository(workspace)
    validated = _validated(run_id="repository-run", agents=1, days=2)

    repository.ensure_available("repository-run")
    prepared = repository.prepare(validated)
    assert not (workspace.root / "city-runs").exists()
    published = repository.publish(prepared)
    summary = repository.verified_summary("repository-run")

    assert published.manifest.run_id == summary.run_id == "repository-run"
    assert summary.run_schema_version == 7
    assert repository.load("repository-run").manifest == published.manifest


def test_repository_publishes_and_summarizes_schema2_as_v8(tmp_path: Path) -> None:
    from adlife.city.workbench_validation import construct_workbench_run
    from tests.unit.city.test_workbench_validation import persona_draft

    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = _module().WorkbenchRunRepository(workspace)
    validated = construct_workbench_run(persona_draft())
    prepared = repository.prepare(validated)
    stored = repository.publish(prepared)
    assert stored.manifest.schema_version == 8
    assert repository.verified_summary("launch-run").run_schema_version == 8
    assert repository.list_runs(offset=0, limit=20).runs[0].run_schema_version == 8


def test_repository_rejects_reparse_points_at_every_direct_store_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="stored-run", workbench=True)
    repository = module.WorkbenchRunRepository(workspace)
    prepared = repository.prepare(_validated(run_id="fresh-run", agents=1, days=2))
    original = module._require_direct_directory

    def reject_runs_directory(path: Path, *, label: str) -> None:
        if path == workspace.root / "city-runs":
            raise UnsafeRunLocation("simulated reparse point")
        original(path, label=label)

    monkeypatch.setattr(module, "_require_direct_directory", reject_runs_directory)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.load("stored-run"),
        lambda: repository.publish(prepared),
    ):
        with pytest.raises(UnsafeRunLocation, match="reparse"):
            operation()


def test_repository_preflight_rejects_a_reparse_point_at_the_candidate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    workspace.root.joinpath("city-runs").mkdir()
    repository = module.WorkbenchRunRepository(workspace)
    prepared = repository.prepare(_validated(run_id="fresh-run", agents=1, days=2))
    original = module._direct_location_exists

    def reject_candidate(path: Path, *, label: str) -> bool:
        if path == workspace.root / "city-runs" / "fresh-run":
            raise UnsafeRunLocation("simulated candidate reparse point")
        return original(path, label=label)

    monkeypatch.setattr(module, "_direct_location_exists", reject_candidate)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.publish(prepared),
    ):
        with pytest.raises(UnsafeRunLocation, match="candidate reparse"):
            operation()


# Discovery reuses a summary only for byte-identical runs; anything else verifies fully.

_CACHED_V10 = "cached-v10"


class _StoreLoads:
    """Record every full store verification by run identifier, then perform it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.run_ids: list[str] = []
        verify = CityRunStore.load

        def load(store: CityRunStore, run_id: str):
            self.run_ids.append(run_id)
            return verify(store, run_id)

        monkeypatch.setattr(CityRunStore, "load", load)

    def take(self) -> list[str]:
        observed, self.run_ids = self.run_ids, []
        return observed


@pytest.fixture(scope="module")
def v10_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    # Two people are the smallest population whose evidence streams are all non-empty.
    _staged, _prepared, directory = _saved_v10(
        tmp_path_factory.mktemp("v10-template"),
        mode="rules",
        agents=2,
        days=2,
        run_id=_CACHED_V10,
    )
    assert all(path.stat().st_size for path in directory.rglob("*") if path.is_file())
    return directory


def _copied_v10(template: Path, root: Path) -> Path:
    destination = root / "city-runs" / template.name
    destination.parent.mkdir(exist_ok=True)
    shutil.copytree(template, destination)
    return destination


def _flip_one_byte_keeping_size_and_mtime(path: Path) -> None:
    before = os.stat(path)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    with path.open("r+b") as target:
        target.write(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(path)
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def _directory_link(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            return False
        import _winapi

        try:
            _winapi.CreateJunction(str(target), str(link))
        except OSError:
            return False
    return True


def test_unchanged_runs_are_relisted_without_a_full_store_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, v10_template: Path
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _copied_v10(v10_template, workspace.root)
    _saved_run(workspace.root, run_id="alpha-v7", workbench=True)
    _saved_run(workspace.root, run_id="zulu-v6", workbench=False)
    loads = _StoreLoads(monkeypatch)
    repository = module.WorkbenchRunRepository(workspace)

    first = repository.list_runs(offset=0, limit=20)
    assert loads.take() == ["alpha-v7", _CACHED_V10, "zulu-v6"]
    second = repository.list_runs(offset=0, limit=20)
    window = repository.list_runs(offset=1, limit=1)

    assert loads.take() == []
    assert second == first
    assert second.model_dump_json() == first.model_dump_json()
    assert [item.run_schema_version for item in first.runs] == [7, 10, 6]
    assert window.runs == first.runs[1:2]
    assert (window.verified_total, window.scanned_count, window.corrupt_count) == (3, 3, 0)

    # Opening a run and a job's verification step still verify the artifact afresh.
    assert repository.load(_CACHED_V10).manifest.run_id == _CACHED_V10
    assert repository.verified_summary("alpha-v7") == first.runs[0]
    assert loads.take() == [_CACHED_V10, "alpha-v7"]


@pytest.mark.parametrize("relative", sorted(V10_PATHS))
def test_one_flipped_byte_in_any_artifact_forces_full_reverification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    v10_template: Path,
    relative: str,
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    directory = _copied_v10(v10_template, workspace.root)
    loads = _StoreLoads(monkeypatch)
    repository = _module().WorkbenchRunRepository(workspace)
    assert repository.list_runs(offset=0, limit=20).verified_total == 1
    assert loads.take() == [_CACHED_V10]

    _flip_one_byte_keeping_size_and_mtime(directory / relative)
    page = repository.list_runs(offset=0, limit=20)

    assert loads.take() == [_CACHED_V10]
    assert (page.verified_total, page.scanned_count, page.corrupt_count) == (0, 1, 1)
    assert page.runs == ()


def test_a_failed_run_is_rechecked_and_restored_bytes_verify_once_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, v10_template: Path
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    directory = _copied_v10(v10_template, workspace.root)
    stream = directory / "outputs" / "spatial-responses.jsonl"
    original = stream.read_bytes()
    stamp = os.stat(stream)
    loads = _StoreLoads(monkeypatch)
    repository = _module().WorkbenchRunRepository(workspace)
    valid = repository.list_runs(offset=0, limit=20)

    _flip_one_byte_keeping_size_and_mtime(stream)
    damaged = [repository.list_runs(offset=0, limit=20) for _ in range(2)]
    stream.write_bytes(original)
    os.utime(stream, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    restored = [repository.list_runs(offset=0, limit=20) for _ in range(2)]

    assert loads.take() == [_CACHED_V10] * 4
    assert all(page.corrupt_count == 1 and page.runs == () for page in damaged)
    assert valid.verified_total == 1
    assert restored == [valid, valid]


@pytest.mark.parametrize("change", ["hard-link", "undeclared-directory"])
def test_cache_never_masks_a_store_refusal_when_artifact_bytes_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    v10_template: Path,
    change: str,
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    directory = _copied_v10(v10_template, workspace.root)
    loads = _StoreLoads(monkeypatch)
    repository = _module().WorkbenchRunRepository(workspace)
    assert repository.list_runs(offset=0, limit=20).verified_total == 1
    assert loads.take() == [_CACHED_V10]

    if change == "hard-link":
        try:
            os.link(directory / "inputs" / "city.json", tmp_path / "second-name.json")
        except OSError:
            pytest.skip("hard links are unavailable on this filesystem")
    else:
        (directory / "inputs" / "undeclared").mkdir()
    page = repository.list_runs(offset=0, limit=20)

    assert loads.take() == [_CACHED_V10]
    assert (page.verified_total, page.corrupt_count) == (0, 1)


def test_a_link_inside_a_run_is_never_followed_and_disables_the_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="linked-run", workbench=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.json").write_text("{}", encoding="utf-8")
    if not _directory_link(workspace.root / "city-runs" / "linked-run" / "extra", outside):
        pytest.skip("directory links are unavailable on this platform")
    loads = _StoreLoads(monkeypatch)
    repository = module.WorkbenchRunRepository(workspace)

    pages = [repository.list_runs(offset=0, limit=20) for _ in range(2)]

    # The store ignores this undeclared top-level entry, so the listing is unchanged, but
    # the cache refuses to read through the link and the run is verified every time.
    assert pages[0] == pages[1]
    assert [item.run_id for item in pages[0].runs] == ["linked-run"]
    assert loads.take() == ["linked-run", "linked-run"]
    assert repository._observe("linked-run") is None
    assert "linked-run" not in repository._verified_runs


def test_deleted_runs_leave_both_the_listing_and_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="gone-run", workbench=True)
    _saved_run(workspace.root, run_id="kept-run", workbench=True)
    loads = _StoreLoads(monkeypatch)
    repository = _module().WorkbenchRunRepository(workspace)
    assert repository.list_runs(offset=0, limit=20).verified_total == 2
    assert loads.take() == ["gone-run", "kept-run"]

    shutil.rmtree(workspace.root / "city-runs" / "gone-run")
    page = repository.list_runs(offset=0, limit=20)

    assert [item.run_id for item in page.runs] == ["kept-run"]
    assert (page.verified_total, page.scanned_count, page.corrupt_count) == (1, 1, 0)
    assert loads.take() == []
    assert list(repository._verified_runs) == ["kept-run"]

    shutil.rmtree(workspace.root / "city-runs")
    assert repository.list_runs(offset=0, limit=20).scanned_count == 0
    assert list(repository._verified_runs) == []


def test_an_added_run_is_verified_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="first-run", workbench=True)
    loads = _StoreLoads(monkeypatch)
    repository = _module().WorkbenchRunRepository(workspace)
    assert repository.list_runs(offset=0, limit=20).verified_total == 1
    assert loads.take() == ["first-run"]

    _saved_run(workspace.root, run_id="second-run", workbench=False)
    pages = [repository.list_runs(offset=0, limit=20) for _ in range(3)]

    assert loads.take() == ["second-run"]
    assert pages[0] == pages[1] == pages[2]
    assert [item.run_id for item in pages[0].runs] == ["first-run", "second-run"]


def test_a_run_that_changes_during_verification_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="moving-run", workbench=True)
    notes = workspace.root / "city-runs" / "moving-run" / "notes.txt"
    verify = CityRunStore.load
    calls: list[str] = []

    def verify_while_a_writer_adds_a_file(store: CityRunStore, run_id: str):
        calls.append(run_id)
        stored = verify(store, run_id)
        if len(calls) == 1:
            notes.write_bytes(b"written while the run was being verified\n")
        return stored

    monkeypatch.setattr(CityRunStore, "load", verify_while_a_writer_adds_a_file)
    repository = _module().WorkbenchRunRepository(workspace)

    pages = [repository.list_runs(offset=0, limit=20) for _ in range(3)]

    # The store ignores the undeclared file, but bytes that moved during verification are
    # never vouched for; the next listing verifies the settled bytes once and keeps them.
    assert calls == ["moving-run", "moving-run"]
    assert pages[0] == pages[1] == pages[2]
    assert pages[0].verified_total == 1


def test_a_job_verification_seeds_the_listing_through_the_same_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from adlife.city.workbench_jobs import CityJobManager
    from tests.unit.city.test_workbench_transport_v4 import compiled_v4

    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = _module().WorkbenchRunRepository(workspace)
    validated = compiled_v4(
        mode="rules",
        mutation=lambda payload: (
            payload["settings"].update(run_id="seeded-v10", agent_count=1, days=2, seed=42),
            payload["population"]["personas"][0].update(count=1),
        ),
        causal_contract=CURRENT_CITY_COGNITION_CAUSAL_CONTRACT,
    )
    loads = _StoreLoads(monkeypatch)
    manager = CityJobManager(repository)
    try:
        submitted = manager.submit(validated)
        deadline = time.monotonic() + 60
        while (terminal := manager.get(submitted.job_id)).phase not in {
            "completed",
            "failed",
            "cancelled",
        }:
            assert time.monotonic() < deadline, "V10 workbench job did not terminate"
            time.sleep(0.01)
    finally:
        manager.close()
    assert terminal.phase == "completed"
    assert loads.take() == ["seeded-v10"]

    page = repository.list_runs(offset=0, limit=20)

    assert loads.take() == []
    assert page.runs == (terminal.result,)


def test_verified_summary_cache_is_bounded_and_drops_the_least_recent_entry(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="bounded-run", workbench=True)
    repository = module.WorkbenchRunRepository(workspace)
    summary = repository.list_runs(offset=0, limit=20).runs[0]
    limit = module.MAX_DISCOVERY_CANDIDATES

    for index in range(limit + 8):
        repository._remember(f"seeded-{index:03d}", (), summary)
        assert len(repository._verified_runs) <= limit

    assert len(repository._verified_runs) == limit
    assert "bounded-run" not in repository._verified_runs
    assert next(iter(repository._verified_runs)) == "seeded-008"
    # A listing keeps only runs it scanned, so the seeded strangers are evicted as well.
    assert repository.list_runs(offset=0, limit=20).runs == (summary,)
    assert list(repository._verified_runs) == ["bounded-run"]


def test_concurrent_listings_and_job_verifications_share_one_consistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    run_ids = ["first-run", "second-run", "third-run"]
    for run_id in run_ids:
        _saved_run(workspace.root, run_id=run_id, workbench=True)
    expected = module.WorkbenchRunRepository(workspace).list_runs(offset=0, limit=20)
    repository = module.WorkbenchRunRepository(workspace)
    start = Barrier(6)

    def listings() -> list[object]:
        start.wait(timeout=60)
        return [repository.list_runs(offset=0, limit=20) for _ in range(3)]

    def verifications() -> list[object]:
        start.wait(timeout=60)
        return [repository.verified_summary("second-run") for _ in range(3)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        listed = [pool.submit(listings) for _ in range(4)]
        verified = [pool.submit(verifications) for _ in range(2)]
        pages = [page for future in listed for page in future.result(timeout=300)]
        summaries = [item for future in verified for item in future.result(timeout=300)]

    assert len(pages) == 12 and all(page == expected for page in pages)
    assert len(summaries) == 6 and all(item == expected.runs[1] for item in summaries)
    assert sorted(repository._verified_runs) == run_ids
    loads = _StoreLoads(monkeypatch)
    assert repository.list_runs(offset=0, limit=20) == expected
    assert loads.take() == []
