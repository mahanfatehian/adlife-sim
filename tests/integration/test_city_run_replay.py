"""Saved city traces replay from frozen inputs without touching source bytes."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
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
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionEvaluationV2,
    CityCognitionInputV2,
)
from adlife.core.domain.city_run import (
    CityRunManifestV5,
    CityRunManifestV9,
    CityRunManifestV10,
)
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import CorruptRunArtifact, DuplicateRun
from adlife.core.simulation.city_cognition import (
    build_city_budget_skipped_result,
    build_city_cognition_terminal_record,
    build_city_fallback_result,
    build_city_rules_result,
    validate_city_cognition_evaluation,
)
from adlife.core.simulation.spatial_response_v2 import SpatialResponseEvaluationV2
from tests.integration.test_city_cognition_v2_artifacts import _saved_v10
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
    before = _artifact_inventory(stored.directory)
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
    assert _artifact_inventory(stored.directory) == before


_HASH_CHUNK_SIZE = 1024 * 1024
_EMPTY_SHA256 = sha256(b"").hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_inventory(directory: Path) -> tuple[tuple[str, str, int, str], ...]:
    inventory: list[tuple[str, str, int, str]] = []
    paths = sorted(
        directory.rglob("*"),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    for path in paths:
        metadata = path.lstat()
        relative_path = path.relative_to(directory).as_posix()
        if stat.S_ISREG(metadata.st_mode):
            path_type = "file"
            size = metadata.st_size
            digest = _sha256_file(path)
        elif stat.S_ISDIR(metadata.st_mode):
            path_type = "directory"
            size = 0
            digest = _EMPTY_SHA256
        elif stat.S_ISLNK(metadata.st_mode):
            path_type = "symlink"
            target = os.fsencode(os.readlink(path))
            size = len(target)
            digest = sha256(target).hexdigest()
        else:
            path_type = "other"
            size = metadata.st_size
            digest = _EMPTY_SHA256
        inventory.append((relative_path, path_type, size, digest))
    return tuple(inventory)


def _create_v10_run(root: Path, **options):
    staged, prepared, directory = _saved_v10(root, **options)
    loaded = CityRunStore(root).load(prepared.manifest.run_id)
    assert loaded.directory == directory
    return staged, prepared, loaded


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
    before = _artifact_inventory(first.directory)

    result = replay_city_run(CityRunStore(tmp_path / "first").load("study"))

    assert result.identical is True
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    assert result.frame_count == 1440
    assert result.position_count == 4320
    _assert_no_response_evidence(result)
    assert _artifact_inventory(first.directory) == before
    assert _artifact_inventory(second.directory) == before


def test_changed_source_is_not_silently_repaired(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    city_file = stored.directory / "inputs" / "city.json"
    with city_file.open("ab") as stream:
        stream.write(b" ")
    before = _artifact_inventory(stored.directory)

    with pytest.raises(CorruptRunArtifact):
        replay_city_run(CityRunStore(tmp_path).load("study"))

    assert _artifact_inventory(stored.directory) == before


def test_v2_replay_is_identical_and_preserves_every_source_artifact(tmp_path: Path) -> None:
    pack = load_pack_v2(pack_v2_data())
    first = create_city_run(
        pack, root=tmp_path / "first", run_id="v2-study", seed=17, agent_count=3, days=1
    )
    second = create_city_run(
        pack, root=tmp_path / "second", run_id="v2-study", seed=17, agent_count=3, days=1
    )
    first_before = _artifact_inventory(first.directory)
    second_before = _artifact_inventory(second.directory)
    result = replay_city_run(CityRunStore(tmp_path / "first").load("v2-study"))
    assert result.identical is True
    assert first.manifest.schema_version == 2
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    _assert_no_response_evidence(result)
    assert _artifact_inventory(first.directory) == first_before
    assert _artifact_inventory(second.directory) == second_before


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
    before = _artifact_inventory(first.directory)
    result = replay_city_run(CityRunStore(tmp_path / "first").load("place-study"))
    assert result.identical is True
    assert first.manifest.schema_version == 3
    assert result.trace_sha256 == first.manifest.trace_sha256 == second.manifest.trace_sha256
    _assert_no_response_evidence(result)
    assert _artifact_inventory(first.directory) == before == _artifact_inventory(second.directory)


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
    before = _artifact_inventory(stored.directory)
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
    assert _artifact_inventory(stored.directory) == before


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
    before = _artifact_inventory(first.directory)

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
    assert _artifact_inventory(first.directory) == before == _artifact_inventory(second.directory)


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
    before = _artifact_inventory(stored.directory)
    baseline = replay_city_run(CityRunStore(root).load(run_id))
    assert _artifact_inventory(stored.directory) == before

    record.unlink()
    with pytest.raises(UnknownWorkbenchCity):
        WorkbenchCityResolver(prepare_workbench_workspace(root)).resolve_city(resolved.pack.city_id)
    assert replay_city_run(CityRunStore(root).load(run_id)) == baseline
    assert _artifact_inventory(stored.directory) == before

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
    assert _artifact_inventory(stored.directory) == before

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
    assert _artifact_inventory(stored.directory) == before


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
    original_hashes = _artifact_inventory(stored.directory)
    sidecar = stored.directory / "inputs" / "city.json"
    changed_data = resolved.pack.model_dump(mode="json")
    changed_data["name"] = "Canonical but causally different frozen city"
    changed_pack = parse_city_pack_json(canonical_json(changed_data))
    assert isinstance(changed_pack, CityPackV2)
    changed_bytes = (canonical_json(changed_pack) + "\n").encode("utf-8")
    assert changed_pack.fingerprint != resolved.pack.fingerprint
    sidecar.write_bytes(changed_bytes)
    tampered_hashes = _artifact_inventory(stored.directory)
    assert tampered_hashes != original_hashes

    with pytest.raises(CorruptRunArtifact):
        replay_city_run(CityRunStore(root).load(run_id))

    registered = WorkbenchCityResolver(prepare_workbench_workspace(workspace.root)).resolve_city(
        resolved.pack.city_id
    )
    assert registered.pack.fingerprint == resolved.pack.fingerprint
    assert registered.pack.fingerprint != changed_pack.fingerprint
    assert sidecar.read_bytes() == changed_bytes
    assert _artifact_inventory(stored.directory) == tampered_hashes


def test_v9_finalization_publishes_complete_cognition_and_replays_read_only(
    tmp_path: Path,
) -> None:
    stored = _create_v9_run(tmp_path)
    before = _artifact_inventory(stored.directory)

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
    assert _artifact_inventory(stored.directory) == before


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
    before = _artifact_inventory(stored.directory)

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
    assert _artifact_inventory(stored.directory) == before


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
    before = _artifact_inventory(stored.directory)

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
    assert _artifact_inventory(stored.directory) == before


def test_v9_replay_refuses_cross_run_cognition_without_repair(
    tmp_path: Path,
) -> None:
    first = _create_v9_run(tmp_path / "first", run_id="first-v9")
    second = _create_v9_run(tmp_path / "second", run_id="second-v9")
    first_before = _artifact_inventory(first.directory)
    second_before = _artifact_inventory(second.directory)
    assert second.cognition_evaluation is not None
    crossed = replace(first, cognition_evaluation=second.cognition_evaluation)

    with pytest.raises(CorruptRunArtifact, match="cognition"):
        replay_city_run(crossed)

    assert _artifact_inventory(first.directory) == first_before
    assert _artifact_inventory(second.directory) == second_before


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
    before = _artifact_inventory(stored.directory)

    with pytest.raises(DuplicateRun):
        _create_v9_run(tmp_path)

    assert _artifact_inventory(stored.directory) == before


def test_v10_replay_is_offline_and_source_artifacts_are_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from adlife.adapters.cognition.city_cache import CityCognitionCache
    from adlife.adapters.cognition.city_cache_v2 import CityCognitionCacheV2
    from adlife.adapters.cognition.city_credentials import OSKeyringCityCredentialStore
    from adlife.adapters.cognition.city_openai_compatible import CityOpenAICompatibleProvider
    from adlife.adapters.cognition.city_openai_compatible_v2 import (
        CityOpenAICompatibleProviderV2,
    )
    from adlife.city import runs as runs_module
    from adlife.city import workbench_personas
    from adlife.city.provider_profiles import ProviderProfileRegistry

    _, prepared, loaded = _create_v10_run(
        tmp_path,
        mode="local",
        source="provider",
        agents=8,
        run_id="offline-v10",
    )
    before = _artifact_inventory(loaded.directory)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("V10 replay consulted external or current runtime state")

    monkeypatch.setattr(CityOpenAICompatibleProvider, "__init__", forbidden)
    monkeypatch.setattr(CityOpenAICompatibleProviderV2, "__init__", forbidden)
    monkeypatch.setattr(CityCognitionCache, "__init__", forbidden)
    monkeypatch.setattr(CityCognitionCache, "get", forbidden)
    monkeypatch.setattr(CityCognitionCache, "put", forbidden)
    monkeypatch.setattr(CityCognitionCacheV2, "__init__", forbidden)
    monkeypatch.setattr(CityCognitionCacheV2, "get", forbidden)
    monkeypatch.setattr(CityCognitionCacheV2, "put", forbidden)
    monkeypatch.setattr(OSKeyringCityCredentialStore, "resolve", forbidden)
    monkeypatch.setattr(ProviderProfileRegistry, "__init__", forbidden)
    monkeypatch.setattr(ProviderProfileRegistry, "load", forbidden)
    monkeypatch.setattr(WorkbenchCityResolver, "resolve_city", forbidden)
    monkeypatch.setattr(workbench_personas, "load_persona_template_catalog", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(type(os.environ), "get", forbidden)
    monkeypatch.setattr(runs_module, "CITY_COGNITION_PROMPT_CONTRACT_TEXT", "changed")
    monkeypatch.setattr(runs_module, "CITY_COGNITION_RESULT_SCHEMA_DOCUMENT", "changed")
    monkeypatch.setattr(runs_module, "CITY_COGNITION_DEFAULT_SELECTION_POLICY", object())
    monkeypatch.setattr(runs_module, "CITY_COGNITION_DEFAULT_FALLBACK_POLICY", object())

    first = replay_city_run(loaded)
    second = replay_city_run(CityRunStore(tmp_path).load("offline-v10"))

    assert first == second
    assert first.cognition_evaluation == prepared.cognition_evaluation
    assert _artifact_inventory(loaded.directory) == before


def test_v10_replay_passes_the_frozen_contract_and_influence_policy_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city import runs as runs_module
    from adlife.core.simulation import spatial_response_v2 as spatial_response_v2_module

    _, prepared, loaded = _create_v10_run(tmp_path, mode="mock", run_id="contract-v10")
    frozen = prepared.cognition_input
    assert isinstance(frozen, CityCognitionInputV2)
    request_builder = runs_module.build_city_cognition_requests_v2
    input_builder = runs_module.build_city_cognition_input_v2
    influence_application = runs_module.apply_spatial_cognition_v2
    influence_vector_builder = spatial_response_v2_module.apply_city_cognition_influence_v1
    observed: list[object] = []
    inner_policies: list[object] = []

    def checked_requests(*args: object, **kwargs: object):
        assert kwargs.get("causal_contract") == frozen.causal_contract
        observed.append(kwargs["causal_contract"])
        return request_builder(*args, **kwargs)

    def checked_input(*args: object, **kwargs: object):
        assert kwargs.get("causal_contract") == frozen.causal_contract
        observed.append(kwargs["causal_contract"])
        return input_builder(*args, **kwargs)

    def checked_influence_application(baseline, cognition_input, cognition_evaluation):
        assert isinstance(cognition_input, CityCognitionInputV2)
        policy = cognition_input.causal_contract.influence_policy
        assert policy == frozen.causal_contract.influence_policy
        observed.append(policy)
        return influence_application(baseline, cognition_input, cognition_evaluation)

    def checked_influence_vector(*args: object, **kwargs: object):
        assert "policy" in kwargs
        policy = kwargs["policy"]
        assert policy == frozen.causal_contract.influence_policy
        inner_policies.append(policy)
        return influence_vector_builder(*args, **kwargs)

    monkeypatch.setattr(runs_module, "build_city_cognition_requests_v2", checked_requests)
    monkeypatch.setattr(runs_module, "build_city_cognition_input_v2", checked_input)
    monkeypatch.setattr(
        runs_module,
        "apply_spatial_cognition_v2",
        checked_influence_application,
    )
    monkeypatch.setattr(
        spatial_response_v2_module,
        "apply_city_cognition_influence_v1",
        checked_influence_vector,
    )

    replay_city_run(loaded)

    assert observed == [
        frozen.causal_contract,
        frozen.causal_contract,
        frozen.causal_contract.influence_policy,
    ]
    assert isinstance(prepared.response_evaluation, SpatialResponseEvaluationV2)
    assert inner_policies == [frozen.causal_contract.influence_policy] * (
        prepared.response_evaluation.counts.applied_response_count
    )


def test_v10_replay_regenerates_complete_evidence_from_frozen_inputs(tmp_path: Path) -> None:
    staged, prepared, loaded = _create_v10_run(
        tmp_path,
        mode="local",
        source="cache",
        agents=8,
        run_id="evidence-v10",
    )

    result = replay_city_run(loaded)

    assert isinstance(prepared.manifest, CityRunManifestV10)
    assert isinstance(prepared.response_evaluation, SpatialResponseEvaluationV2)
    assert isinstance(prepared.cognition_evaluation, CityCognitionEvaluationV2)
    assert result.identical is True
    assert result.response_schema_version == 2
    assert result.response_counts == prepared.response_evaluation.counts
    assert result.cognition_evaluation == prepared.cognition_evaluation
    assert result.cognition_effect_scope == prepared.cognition_input.effect_scope
    assert result.cognition_mode == prepared.cognition_input.mode
    assert result.cognition_coverage == prepared.manifest.cognition.coverage
    assert result.cognition_outcomes == prepared.manifest.cognition.outcomes
    assert result.cognition_reasons == prepared.manifest.cognition.reasons
    assert result.cognition_neutralization == prepared.manifest.cognition.neutralization
    assert result.cognition_input_sha256 == prepared.manifest.cognition.cognition_input_sha256
    assert result.cognition_terminal_stream_sha256 == (
        prepared.manifest.cognition.terminal_stream_sha256
    )
    assert result.cognition_summary_sha256 == prepared.manifest.cognition.summary_sha256
    assert result.rule_state_document_sha256 == prepared.manifest.rule_state_document_sha256
    assert result.cognition_state_document_sha256 == (
        prepared.manifest.cognition_state_document_sha256
    )
    assert result.baseline_response_count == staged.baseline_evaluation.counts.response_count
    assert (
        result.applied_response_count == prepared.response_evaluation.counts.applied_response_count
    )
    assert result.dual_state_update_count == (
        prepared.response_evaluation.counts.dual_state_update_count
    )
    assert result.final_rule_state_count == len(prepared.response_evaluation.final_rule_states)
    assert result.final_cognition_state_count == len(
        prepared.response_evaluation.final_cognition_states
    )


@pytest.mark.parametrize("corruption", ["reverse", "missing", "input-binding"])
def test_v10_replay_refuses_direct_bypass_corruption_without_normalizing_it(
    tmp_path: Path,
    corruption: str,
) -> None:
    _, _, loaded = _create_v10_run(tmp_path, mode="mock", agents=8)
    before = _artifact_inventory(loaded.directory)
    assert isinstance(loaded.cognition_evaluation, CityCognitionEvaluationV2)
    assert isinstance(loaded.cognition_input, CityCognitionInputV2)
    assert len(loaded.cognition_evaluation.records) > 1
    if corruption == "input-binding":
        bad_input = CityCognitionInputV2.model_construct(
            **(
                loaded.cognition_input.model_dump(mode="python")
                | {"causal_contract_sha256": "0" * 64}
            )
        )
        corrupted = replace(loaded, cognition_input=bad_input)
    else:
        records = loaded.cognition_evaluation.records
        changed = tuple(reversed(records)) if corruption == "reverse" else records[:-1]
        bad_evaluation = CityCognitionEvaluationV2.model_construct(
            **(loaded.cognition_evaluation.model_dump(mode="python") | {"records": changed})
        )
        corrupted = replace(loaded, cognition_evaluation=bad_evaluation)

    with pytest.raises(CorruptRunArtifact, match="V10"):
        replay_city_run(corrupted)

    assert _artifact_inventory(loaded.directory) == before


def test_v10_replay_refuses_a_noncanonical_mutable_mobility_container(tmp_path: Path) -> None:
    _, _, loaded = _create_v10_run(tmp_path, mode="rules")
    loaded.mobility.place_assignments = []

    with pytest.raises(CorruptRunArtifact, match="V10"):
        replay_city_run(loaded)


@pytest.mark.parametrize(
    ("mode", "source", "agents", "empty", "outcome"),
    [
        ("rules", None, 3, False, "rules"),
        ("mock", None, 3, False, "mock"),
        ("local", "provider", 8, False, "provider"),
        ("local", "cache", 8, False, "cache"),
        ("local", "fallback", 8, False, "fallback"),
        ("local", "provider", 30, False, "budget_skipped"),
        ("local", "provider", 3, True, "provider"),
    ],
)
def test_v10_replay_preserves_every_frozen_terminal_class(
    tmp_path: Path,
    mode: str,
    source: str | None,
    agents: int,
    empty: bool,
    outcome: str,
) -> None:
    _, prepared, loaded = _create_v10_run(
        tmp_path / f"{mode}-{source}-{agents}-{empty}",
        mode=mode,
        source=source,
        agents=agents,
        empty=empty,
    )

    result = replay_city_run(loaded)

    assert isinstance(prepared.manifest, CityRunManifestV10)
    assert result.cognition_outcomes == prepared.manifest.cognition.outcomes
    if empty:
        assert result.cognition_terminal_record_count == 0
        assert result.cognition_coverage is not None
        assert result.cognition_coverage.selected_request_count == 0
    else:
        assert result.cognition_outcomes is not None
        assert getattr(result.cognition_outcomes, outcome) > 0


def test_v10_identical_recorded_provider_cognition_has_identical_downstream_evidence(
    tmp_path: Path,
) -> None:
    _, _, first = _create_v10_run(
        tmp_path / "first",
        mode="local",
        source="provider",
        agents=8,
        run_id="recorded-provider-v10",
    )
    _, _, second = _create_v10_run(
        tmp_path / "second",
        mode="local",
        source="provider",
        agents=8,
        run_id="recorded-provider-v10",
    )

    assert replay_city_run(first) == replay_city_run(second)


def test_v10_replay_does_not_disguise_an_implementation_type_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adlife.city import runs as runs_module

    _, _, loaded = _create_v10_run(tmp_path, mode="rules")

    def fail_validation(*_args: object, **_kwargs: object) -> None:
        raise TypeError("injected V10 replay implementation defect")

    monkeypatch.setattr(runs_module, "validate_city_cognition_evaluation_v2", fail_validation)

    with pytest.raises(TypeError, match="injected V10 replay implementation defect"):
        replay_city_run(loaded)


def test_v10_replay_has_a_fixed_cross_process_hashseed_witness(tmp_path: Path) -> None:
    _, prepared, _ = _create_v10_run(
        tmp_path,
        mode="mock",
        agents=8,
        run_id="hashseed-v10",
    )
    script = """
import hashlib
import json
import sys
from pathlib import Path
from adlife.city.run_store import CityRunStore
from adlife.city.runs import replay_city_run

result = replay_city_run(CityRunStore(Path(sys.argv[1])).load(sys.argv[2]))
witness = {
    "trace": result.trace_sha256,
    "opportunity": result.opportunity_stream_sha256,
    "attention": result.attention_stream_sha256,
    "response": result.response_stream_sha256,
    "response_state": result.response_state_sha256,
    "response_summary": result.response_summary_sha256,
    "rule_state": result.rule_state_document_sha256,
    "cognition_state": result.cognition_state_document_sha256,
    "cognition_input": result.cognition_input_sha256,
    "terminal": result.cognition_terminal_stream_sha256,
    "cognition_summary": result.cognition_summary_sha256,
    "causal_contract": result.cognition_causal_contract_sha256,
    "effective_settings": result.cognition_effective_settings_sha256,
    "budget_allocation": result.cognition_budget_allocation_sha256,
    "influence_policy": result.cognition_influence_policy_sha256,
}
print(
    hashlib.sha256(
        json.dumps(witness, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
)
"""

    def witness(seed: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), prepared.manifest.run_id],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ | {"PYTHONHASHSEED": seed},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert completed.stderr == ""
        return completed.stdout.strip()

    seed_zero = witness("0")
    seed_other = witness("12345")
    assert seed_zero == seed_other
    assert seed_zero == "93b064c34f56d84b94217c91378855f8f065bb69441e147b95f02ec3393045f8"
