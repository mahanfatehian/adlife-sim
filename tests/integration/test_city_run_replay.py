"""Saved city traces replay from frozen inputs without touching source bytes."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adlife.city.catalog import select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import (
    CityReplayResult,
    create_city_run,
    finalize_city_run,
    prepare_city_scientific_run,
    publish_city_run,
    replay_city_run,
)
from adlife.city.workbench_catalog import (
    UnknownWorkbenchCity,
    WorkbenchCityRegistryUnavailable,
    WorkbenchCityResolver,
)
from adlife.city.workbench_input import WorkbenchRunDraftV3
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_web import create_city_workbench_app
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city import CityPackV2, parse_city_pack_json
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DISCLOSURE,
    CityCognitionResultV1,
    CityCognitionSource,
)
from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV9
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import CorruptRunArtifact, DuplicateRun
from adlife.core.simulation.city_cognition import (
    build_city_budget_skipped_result,
    build_city_cognition_terminal_record,
    build_city_fallback_result,
    build_city_rules_result,
    validate_city_cognition_evaluation,
)
from tests.integration.test_city_workbench_artifacts import _create_uploaded_workbench_run
from tests.unit.city.test_city_catalog_contract import write_catalog_root
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_city_run_preparation import _provider_revision
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_workbench_input import draft_v3_data


def _create_v9_run(root: Path, *, run_id: str = "v9-study"):
    document = draft_v3_data(mode="rules")
    document["settings"]["run_id"] = run_id
    compiled = construct_workbench_run(
        WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    )
    settings = compiled.workbench_input.draft.settings
    scientific = prepare_city_scientific_run(
        compiled.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=compiled.scenario,
        spatial_response=compiled.response_input,
        workbench_input=compiled.workbench_input,
    )
    records = tuple(
        build_city_cognition_terminal_record(
            request,
            selection_index=index,
            source="rules",
            result=build_city_rules_result(request),
        )
        for index, request in enumerate(scientific.cognition_requests)
    )
    evaluation = validate_city_cognition_evaluation(
        scientific.cognition_input,
        scientific.cognition_requests,
        records,
    )
    prepared = finalize_city_run(
        scientific,
        cognition_input=scientific.cognition_input,
        cognition_evaluation=evaluation,
    )
    return publish_city_run(prepared, root=root)


def _create_provider_v9_run(
    root: Path,
    *,
    source: CityCognitionSource,
):
    profile = _provider_revision(root / "profile-registry")
    budget = 0 if source == "budget-skipped" else 92_160
    document = draft_v3_data(
        mode="local",
        aggregate_output_token_ceiling=budget,
    )
    document["settings"]["run_id"] = f"{source}-v9"
    document["cognition"]["provider_profile_id"] = profile.profile_id
    document["cognition"]["provider_revision_sha256"] = profile.revision_sha256
    compiled = construct_workbench_run(
        WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    )
    settings = compiled.workbench_input.draft.settings
    scientific = prepare_city_scientific_run(
        compiled.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=compiled.scenario,
        spatial_response=compiled.response_input,
        workbench_input=compiled.workbench_input,
        provider_profile=profile,
    )
    snapshot = scientific.cognition_input.provider_profile
    assert snapshot is not None
    records = []
    for index, request in enumerate(scientific.cognition_requests):
        if source in {"provider", "cache"}:
            result = CityCognitionResultV1(
                schema_version=1,
                request_id=request.request_id,
                interpretation=f"Frozen {source} explanation {index}.",
                decision_factors=("Frozen deterministic response evidence",),
                safety_flags=(),
                disclosure=CITY_COGNITION_DISCLOSURE,
            )
            attempt_count = 1 if source == "provider" else 0
            reason = None
        elif source == "fallback":
            result = build_city_fallback_result(
                request,
                reason="timeout-exhausted",
                policy=scientific.cognition_input.fallback_policy,
            )
            attempt_count = 2
            reason = "timeout-exhausted"
        else:
            result = build_city_budget_skipped_result(
                request,
                policy=scientific.cognition_input.fallback_policy,
            )
            attempt_count = 0
            reason = "budget-skipped"
        records.append(
            build_city_cognition_terminal_record(
                request,
                selection_index=index,
                source=source,
                result=result,
                attempt_count=attempt_count,
                reason=reason,
                provider_profile=snapshot,
            )
        )
    evaluation = validate_city_cognition_evaluation(
        scientific.cognition_input,
        scientific.cognition_requests,
        tuple(records),
    )
    prepared = finalize_city_run(
        scientific,
        cognition_input=scientific.cognition_input,
        cognition_evaluation=evaluation,
    )
    return publish_city_run(prepared, root=root / "runs")


@pytest.mark.parametrize("catalog_change", ["removed", "modified"])
def test_persona_replay_uses_frozen_inputs_after_template_catalog_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_change: str,
) -> None:
    from adlife.city import workbench_personas
    from tests.integration.test_city_workbench_artifacts import _create_v8

    stored = _create_v8(tmp_path / "source")
    before = _file_hashes(stored.directory)
    baseline = replay_city_run(stored)
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    if catalog_change == "modified":
        (catalog_root / "persona_templates.json").write_bytes(b"{}")
    # Redirect the actual package-resource reader to a missing or invalid catalog.
    monkeypatch.setattr(workbench_personas, "files", lambda _package: catalog_root)
    with pytest.raises(workbench_personas.PersonaTemplateCatalogError):
        workbench_personas.load_persona_template_catalog()
    replay = replay_city_run(CityRunStore(tmp_path / "source").load("launch-run"))
    assert replay.identical is True
    assert replay == baseline
    assert _file_hashes(stored.directory) == before


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _assert_no_response_evidence(result: CityReplayResult) -> None:
    assert result.response_model_id is None
    assert result.response_claim_scope is None
    assert result.response_input_sha256 is None
    assert result.response_stream_sha256 is None
    assert result.response_state_sha256 is None
    assert result.response_summary_sha256 is None
    assert result.response_stream_bytes is None
    assert result.response_count is None
    assert result.state_update_count is None
    assert result.response_campaign_count is None
    assert result.final_state_count is None
    assert result.response_counts is None


def test_fresh_city_runs_have_the_same_trace_and_replay_never_mutates_source(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    first = create_city_run(
        pack, root=tmp_path / "first", run_id="study", seed=42, agent_count=3, days=1
    )
    second = create_city_run(
        pack, root=tmp_path / "second", run_id="study", seed=42, agent_count=3, days=1
    )
    before = _file_hashes(first.directory)

    result = replay_city_run(CityRunStore(tmp_path / "first").load("study"))

    assert result.identical is True
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    assert result.frame_count == 1440
    assert result.position_count == 4320
    _assert_no_response_evidence(result)
    assert _file_hashes(first.directory) == before
    assert _file_hashes(second.directory) == before


def test_changed_source_is_not_silently_repaired(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    city_file = stored.directory / "inputs" / "city.json"
    city_file.write_bytes(city_file.read_bytes() + b" ")
    before = _file_hashes(stored.directory)

    with pytest.raises(CorruptRunArtifact):
        replay_city_run(CityRunStore(tmp_path).load("study"))

    assert _file_hashes(stored.directory) == before


def test_v2_replay_is_identical_and_preserves_every_source_artifact(tmp_path: Path) -> None:
    pack = load_pack_v2(pack_v2_data())
    first = create_city_run(
        pack, root=tmp_path / "first", run_id="v2-study", seed=17, agent_count=3, days=1
    )
    second = create_city_run(
        pack, root=tmp_path / "second", run_id="v2-study", seed=17, agent_count=3, days=1
    )
    first_before = _file_hashes(first.directory)
    second_before = _file_hashes(second.directory)
    result = replay_city_run(CityRunStore(tmp_path / "first").load("v2-study"))
    assert result.identical is True
    assert first.manifest.schema_version == 2
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    _assert_no_response_evidence(result)
    assert _file_hashes(first.directory) == first_before
    assert _file_hashes(second.directory) == second_before


def test_v3_place_replay_is_identical_and_preserves_every_source_artifact(
    tmp_path: Path,
) -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    first = create_city_run(
        pack,
        root=tmp_path / "first",
        run_id="place-study",
        seed=17,
        agent_count=2,
        days=7,
        places=places,
    )
    second = create_city_run(
        pack,
        root=tmp_path / "second",
        run_id="place-study",
        seed=17,
        agent_count=2,
        days=7,
        places=places,
    )
    before = _file_hashes(first.directory)
    result = replay_city_run(CityRunStore(tmp_path / "first").load("place-study"))
    assert result.identical is True
    assert first.manifest.schema_version == 3
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    _assert_no_response_evidence(result)
    assert _file_hashes(first.directory) == before == _file_hashes(second.directory)


def test_catalog_selected_run_replays_after_catalog_is_removed(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog-source"
    pack, _ = write_catalog_root(catalog_root)
    selected = select_catalog_city(pack.city_id, root=catalog_root)
    stored = create_city_run(
        selected,
        root=tmp_path / "runs",
        run_id="catalog-study",
        seed=42,
        agent_count=2,
        days=1,
    )
    before = _file_hashes(stored.directory)
    for path in sorted(catalog_root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    catalog_root.rmdir()
    loaded = CityRunStore(tmp_path / "runs").load("catalog-study")
    result = replay_city_run(loaded)
    assert result.identical is True
    assert loaded.pack == selected
    assert _file_hashes(stored.directory) == before


def test_spatial_city_run_replays_exact_attention_artifacts_without_mutation(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 3}],
                probability=1.0,
                cap=2,
            )
        ],
    )
    first = create_city_run(
        pack,
        root=tmp_path / "first",
        run_id="spatial-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )
    second = create_city_run(
        pack,
        root=tmp_path / "second",
        run_id="spatial-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )
    before = _file_hashes(first.directory)

    result = replay_city_run(CityRunStore(tmp_path / "first").load("spatial-study"))

    assert isinstance(first.manifest, CityRunManifestV5)
    assert first.manifest == second.manifest
    assert first.spatial_scenario == scenario
    assert first.opportunity_evaluation is not None
    assert first.attention_evaluation is not None
    assert result.identical is True
    assert result.scenario_sha256 == scenario.fingerprint
    assert result.opportunity_stream_sha256 == first.manifest.opportunity_stream_sha256
    assert result.opportunity_summary_sha256 == first.manifest.opportunity_summary_sha256
    assert result.opportunity_stream_bytes == first.manifest.opportunity_stream_bytes
    assert result.opportunity_count == first.manifest.opportunity_count == 4
    assert result.attention_model_id == first.manifest.spatial_attention_model_id
    assert result.attention_stream_sha256 == first.manifest.attention_stream_sha256
    assert result.attention_summary_sha256 == first.manifest.attention_summary_sha256
    assert result.attention_stream_bytes == first.manifest.attention_stream_bytes
    assert result.impression_count == first.manifest.impression_count == 4
    assert result.noticed_count == first.manifest.noticed_count
    assert result.attention_counts == first.attention_evaluation.counts
    _assert_no_response_evidence(result)
    assert _file_hashes(first.directory) == before == _file_hashes(second.directory)


def test_spatial_city_run_can_bind_places_and_replay_them(tmp_path: Path) -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 1}],
                probability=1.0,
                cap=1,
            )
        ],
    )

    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="spatial-place-study",
        seed=17,
        agent_count=2,
        days=1,
        places=places,
        spatial_scenario=scenario,
    )
    result = replay_city_run(CityRunStore(tmp_path).load("spatial-place-study"))

    assert isinstance(stored.manifest, CityRunManifestV5)
    assert stored.manifest.place_set_sha256 == places.fingerprint
    assert result.place_set_sha256 == places.fingerprint
    assert result.opportunity_count == 2
    assert result.impression_count == 2


def test_spatial_city_run_refuses_city_or_duration_mismatch_before_reserving_id(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    other_pack = load_pack({**pack_data(), "city_id": "other-fictional-city"})
    wrong_city = _scenario(
        other_pack,
        [_phone(windows=[{"start_minute": 0, "end_minute": 1}])],
    )
    wrong_duration = _scenario(
        pack,
        [_phone(windows=[{"start_minute": 0, "end_minute": 1}])],
        days=2,
    )

    for run_id, scenario in (
        ("wrong-city", wrong_city),
        ("wrong-duration", wrong_duration),
    ):
        with pytest.raises(ValueError):
            create_city_run(
                pack,
                root=tmp_path,
                run_id=run_id,
                seed=42,
                agent_count=1,
                days=1,
                spatial_scenario=scenario,
            )
        assert not (tmp_path / "city-runs" / run_id).exists()


@pytest.mark.parametrize(
    ("input_schema_version", "run_schema_version"),
    [(1, 7), (2, 8)],
)
def test_uploaded_run_replay_ignores_removed_replaced_and_corrupt_registry(
    tmp_path: Path,
    input_schema_version: int,
    run_schema_version: int,
) -> None:
    root = tmp_path / f"registry-independent-v{run_schema_version}"
    run_id = f"registry-independent-v{run_schema_version}"
    workspace, resolved, stored = _create_uploaded_workbench_run(
        root,
        input_schema_version=input_schema_version,
        run_id=run_id,
    )
    record = workspace.root / "city-packs" / f"{resolved.pack.city_id}.json"
    before = _file_hashes(stored.directory)
    baseline = replay_city_run(CityRunStore(root).load(run_id))
    assert _file_hashes(stored.directory) == before

    record.unlink()
    with pytest.raises(UnknownWorkbenchCity):
        WorkbenchCityResolver(prepare_workbench_workspace(root)).resolve_city(resolved.pack.city_id)
    assert replay_city_run(CityRunStore(root).load(run_id)) == baseline
    assert _file_hashes(stored.directory) == before

    replacement_data = resolved.pack.model_dump(mode="json")
    replacement_data["name"] = "Externally replaced operator city"
    replacement = parse_city_pack_json(canonical_json(replacement_data))
    assert isinstance(replacement, CityPackV2)
    assert replacement.fingerprint != resolved.pack.fingerprint
    record.write_bytes((canonical_json(replacement) + "\n").encode("utf-8"))
    replacement_resolved = WorkbenchCityResolver(prepare_workbench_workspace(root)).resolve_city(
        resolved.pack.city_id
    )
    assert replacement_resolved.pack.fingerprint == replacement.fingerprint
    assert replay_city_run(CityRunStore(root).load(run_id)) == baseline
    assert _file_hashes(stored.directory) == before

    record.write_bytes(b'{"schema_version":2}\n')
    with pytest.raises(WorkbenchCityRegistryUnavailable):
        WorkbenchCityResolver(prepare_workbench_workspace(root)).list_cities()

    app = create_city_workbench_app(
        prepare_workbench_workspace(root),
        csrf_token="fixed-replay-integration-token",
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        catalog = client.get("/api/catalog/cities")
        inspector = client.get(f"/runs/{run_id}")
        metadata = client.get(f"/api/runs/{run_id}/meta")
        frozen_city = client.get(f"/api/runs/{run_id}/city")
        frame = client.get(f"/api/runs/{run_id}/frame?minute=0")

    assert catalog.status_code == 409
    assert catalog.json()["error"]["code"] == "city-registry-unavailable"
    assert all(
        response.status_code == 200 for response in (inspector, metadata, frozen_city, frame)
    )
    assert metadata.json()["run_schema_version"] == run_schema_version
    assert metadata.json()["city_sha256"] == resolved.pack.fingerprint
    assert frozen_city.json()["city_id"] == resolved.pack.city_id
    assert replay_city_run(CityRunStore(root).load(run_id)) == baseline
    assert _file_hashes(stored.directory) == before


@pytest.mark.parametrize(
    ("input_schema_version", "run_schema_version"),
    [(1, 7), (2, 8)],
)
def test_uploaded_run_refuses_misbound_frozen_city_without_registry_repair(
    tmp_path: Path,
    input_schema_version: int,
    run_schema_version: int,
) -> None:
    root = tmp_path / f"misbound-frozen-v{run_schema_version}"
    run_id = f"misbound-frozen-v{run_schema_version}"
    workspace, resolved, stored = _create_uploaded_workbench_run(
        root,
        input_schema_version=input_schema_version,
        run_id=run_id,
    )
    original_hashes = _file_hashes(stored.directory)
    sidecar = stored.directory / "inputs" / "city.json"
    changed_data = resolved.pack.model_dump(mode="json")
    changed_data["name"] = "Canonical but causally different frozen city"
    changed_pack = parse_city_pack_json(canonical_json(changed_data))
    assert isinstance(changed_pack, CityPackV2)
    changed_bytes = (canonical_json(changed_pack) + "\n").encode("utf-8")
    assert changed_pack.fingerprint != resolved.pack.fingerprint
    sidecar.write_bytes(changed_bytes)
    tampered_hashes = _file_hashes(stored.directory)
    assert tampered_hashes != original_hashes

    with pytest.raises(CorruptRunArtifact):
        replay_city_run(CityRunStore(root).load(run_id))

    registered = WorkbenchCityResolver(prepare_workbench_workspace(workspace.root)).resolve_city(
        resolved.pack.city_id
    )
    assert registered.pack.fingerprint == resolved.pack.fingerprint
    assert registered.pack.fingerprint != changed_pack.fingerprint
    assert sidecar.read_bytes() == changed_bytes
    assert _file_hashes(stored.directory) == tampered_hashes


def test_v9_finalization_publishes_complete_cognition_and_replays_read_only(
    tmp_path: Path,
) -> None:
    stored = _create_v9_run(tmp_path)
    before = _file_hashes(stored.directory)

    result = replay_city_run(CityRunStore(tmp_path).load("v9-study"))

    assert isinstance(stored.manifest, CityRunManifestV9)
    assert stored.cognition_input is not None
    assert stored.cognition_evaluation is not None
    assert result.identical is True
    assert result.cognition_evaluation == stored.cognition_evaluation
    assert tuple(record.request_id for record in result.cognition_evaluation.records) == tuple(
        record.request_id for record in stored.cognition_evaluation.records
    )
    assert result.cognition_evaluation.summary.terminal_stream_sha256 == (
        stored.manifest.cognition.terminal_stream_sha256
    )
    assert _file_hashes(stored.directory) == before


def test_v9_replay_uses_only_frozen_evidence_without_runtime_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.adapters.cognition.city_cache import CityCognitionCache
    from adlife.adapters.cognition.city_credentials import OSKeyringCityCredentialStore
    from adlife.city import runs as runs_module
    from adlife.city.provider_profiles import ProviderProfileRegistry

    stored = _create_v9_run(tmp_path)
    loaded = CityRunStore(tmp_path).load("v9-study")
    before = _file_hashes(stored.directory)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline replay consulted a live runtime service")

    monkeypatch.setattr(ProviderProfileRegistry, "load", forbidden)
    monkeypatch.setattr(OSKeyringCityCredentialStore, "resolve", forbidden)
    monkeypatch.setattr(CityCognitionCache, "get", forbidden)
    monkeypatch.setattr(WorkbenchCityResolver, "resolve_city", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr(runs_module, "CITY_COGNITION_PROMPT_CONTRACT_TEXT", "removed")
    monkeypatch.setattr(runs_module, "CITY_COGNITION_RESULT_SCHEMA_DOCUMENT", "removed")
    monkeypatch.setattr(runs_module, "CITY_COGNITION_DEFAULT_SELECTION_POLICY", object())
    monkeypatch.setattr(runs_module, "CITY_COGNITION_DEFAULT_FALLBACK_POLICY", object())

    first = replay_city_run(loaded)
    second = replay_city_run(loaded)

    assert first == second
    assert first.cognition_evaluation == loaded.cognition_evaluation
    assert _file_hashes(stored.directory) == before


@pytest.mark.parametrize(
    "source",
    ["provider", "cache", "fallback", "budget-skipped"],
)
def test_v9_replay_preserves_every_provider_terminal_outcome_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: CityCognitionSource,
) -> None:
    from adlife.adapters.cognition.city_cache import CityCognitionCache
    from adlife.adapters.cognition.city_credentials import OSKeyringCityCredentialStore
    from adlife.city.provider_profiles import ProviderProfileRegistry

    stored = _create_provider_v9_run(tmp_path / source, source=source)
    loaded = CityRunStore(tmp_path / source / "runs").load(f"{source}-v9")
    before = _file_hashes(stored.directory)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("replay consulted live provider state")

    monkeypatch.setattr(ProviderProfileRegistry, "load", forbidden)
    monkeypatch.setattr(OSKeyringCityCredentialStore, "resolve", forbidden)
    monkeypatch.setattr(CityCognitionCache, "get", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)

    replayed = replay_city_run(loaded)

    assert replayed.cognition_evaluation is not None
    assert loaded.cognition_evaluation is not None
    assert replayed.cognition_evaluation == loaded.cognition_evaluation
    assert {record.source for record in replayed.cognition_evaluation.records} == {source}
    assert _file_hashes(stored.directory) == before


def test_v9_replay_refuses_cross_run_cognition_without_repair(
    tmp_path: Path,
) -> None:
    first = _create_v9_run(tmp_path / "first", run_id="first-v9")
    second = _create_v9_run(tmp_path / "second", run_id="second-v9")
    first_before = _file_hashes(first.directory)
    second_before = _file_hashes(second.directory)
    assert second.cognition_evaluation is not None
    crossed = replace(first, cognition_evaluation=second.cognition_evaluation)

    with pytest.raises(CorruptRunArtifact, match="cognition"):
        replay_city_run(crossed)

    assert _file_hashes(first.directory) == first_before
    assert _file_hashes(second.directory) == second_before


def test_v9_replay_does_not_disguise_an_implementation_type_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city import runs as runs_module

    _create_v9_run(tmp_path)
    loaded = CityRunStore(tmp_path).load("v9-study")

    def fail_validation(*_args: object, **_kwargs: object) -> None:
        raise TypeError("injected cognition implementation defect")

    monkeypatch.setattr(
        runs_module,
        "validate_city_cognition_evaluation",
        fail_validation,
    )

    with pytest.raises(TypeError, match="injected cognition implementation defect"):
        replay_city_run(loaded)


def test_v9_publication_refuses_an_existing_destination(tmp_path: Path) -> None:
    stored = _create_v9_run(tmp_path)
    before = _file_hashes(stored.directory)

    with pytest.raises(DuplicateRun):
        _create_v9_run(tmp_path)

    assert _file_hashes(stored.directory) == before
