"""Schema-v7 city runs freeze, bind, and replay their complete workbench input."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city import run_store as store_module
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run, replay_city_run
from adlife.city.workbench_binding import validate_workbench_execution_binding
from adlife.city.workbench_catalog import WorkbenchCityResolver
from adlife.city.workbench_input import WorkbenchRunInputV2, parse_workbench_run_input_json
from adlife.city.workbench_runs import WorkbenchRunRepository
from adlife.city.workbench_transport import parse_workbench_http_draft
from adlife.city.workbench_validation import construct_workbench_run
from adlife.city.workbench_workspace import prepare_workbench_workspace
from adlife.core.domain.city_run import CityRunManifestV6, CityRunManifestV7
from adlife.core.domain.json_values import MAX_JSON_DEPTH
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from tests.integration.test_city_workbench_registry import _document, _roadside_draft
from tests.unit.city.test_city_run_preparation import _validated
from tests.unit.city.test_workbench_input import persona_data


def _create(root: Path, *, run_id: str = "launch-run"):
    validated = _validated(run_id=run_id)
    return create_city_run(
        validated.pack,
        root=root,
        run_id=run_id,
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def test_v10_direct_storage_freezes_schema4_without_job_submission(tmp_path):
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, prepared, directory = _saved_v10(tmp_path)
    restored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert restored.manifest.workbench_input_schema_version == 4
    assert restored.workbench_input == prepared.workbench_input
    assert (directory / "inputs/workbench.json").read_bytes() == (
        canonical_json(prepared.workbench_input) + "\n"
    ).encode()
    validate_workbench_execution_binding(
        restored.workbench_input,
        pack=restored.pack,
        run_id=restored.manifest.run_id,
        seed=restored.manifest.seed,
        agent_count=restored.manifest.agent_count,
        days=restored.manifest.days,
        places=None,
        spatial_scenario=restored.spatial_scenario,
        spatial_response=restored.response_input,
    )


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("depth", [MAX_JSON_DEPTH + 1, MAX_JSON_DEPTH * 16])
def test_v8_loader_screens_excessive_nesting_as_corruption_without_mutation(tmp_path, depth):
    from tests.unit.city.test_city_analysis import _persona_run

    stored = _persona_run(tmp_path)
    data = stored.workbench_input.model_dump(mode="json")
    data["api_key"] = "depth-placeholder"
    document = json.dumps(data).replace(
        '"depth-placeholder"', "[" * depth + '"opaque-private-depth-marker"' + "]" * depth
    )
    (stored.directory / "inputs" / "workbench.json").write_text(
        document + "\n", encoding="utf-8", newline="\n"
    )
    before = _hashes(stored.directory)
    with pytest.raises(CorruptRunArtifact) as captured:
        CityRunStore(tmp_path).load("persona-study")
    assert "opaque-private-depth-marker" not in str(captured.value)
    assert _hashes(stored.directory) == before


def _save_copy(store: CityRunStore, stored, *, workbench_input) -> Path:
    return store.save(
        stored.manifest,
        stored.pack,
        stored.mobility.agents,
        places=stored.mobility.places,
        place_assignments=stored.mobility.place_assignments,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        response_input=stored.response_input,
        response_evaluation=stored.response_evaluation,
        workbench_input=workbench_input,
    )


def _rewrite_bound_input(stored, mutate) -> None:
    assert stored.workbench_input is not None
    document = stored.workbench_input.model_dump(mode="json")
    mutate(document)
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    parsed = store_module.parse_workbench_run_input_json(encoded)
    sidecar = (canonical_json(parsed) + "\n").encode("utf-8")
    (stored.directory / "inputs" / "workbench.json").write_bytes(sidecar)
    assert stored.manifest.schema_version in (7, 8)
    manifest = stored.manifest.model_copy(update={"workbench_input_sha256": parsed.fingerprint})
    (stored.directory / "run.json").write_text(
        canonical_json(manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_v7_round_trip_freezes_canonical_input_and_replays_without_mutation(
    tmp_path: Path,
) -> None:
    stored = _create(tmp_path)
    sidecar = stored.directory / "inputs" / "workbench.json"

    assert isinstance(stored.manifest, CityRunManifestV7)
    assert stored.workbench_input is not None
    assert sidecar.read_bytes() == (canonical_json(stored.workbench_input) + "\n").encode("utf-8")
    assert stored.manifest.workbench_input_schema_version == 1
    assert stored.manifest.workbench_input_sha256 == stored.workbench_input.fingerprint

    loaded = CityRunStore(tmp_path).load("launch-run")
    before = _hashes(stored.directory)
    result = replay_city_run(loaded)

    assert loaded.workbench_input == stored.workbench_input
    assert result.identical is True
    assert _hashes(stored.directory) == before


def test_schema_v6_remains_readable_and_has_no_workbench_sidecar(tmp_path: Path) -> None:
    validated = _validated()
    stored = create_city_run(
        validated.pack,
        root=tmp_path,
        run_id="legacy-response",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    loaded = CityRunStore(tmp_path).load("legacy-response")

    assert isinstance(stored.manifest, CityRunManifestV6)
    assert loaded.workbench_input is None
    assert not (stored.directory / "inputs" / "workbench.json").exists()
    assert replay_city_run(loaded).identical is True


def test_schema_v6_refuses_an_undeclared_workbench_sidecar(tmp_path: Path) -> None:
    validated = _validated()
    stored = create_city_run(
        validated.pack,
        root=tmp_path,
        run_id="legacy-response",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    (stored.directory / "inputs" / "workbench.json").write_text(
        canonical_json(validated.workbench_input) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(CorruptRunArtifact, match="undeclared workbench"):
        CityRunStore(tmp_path).load("legacy-response")


def test_store_requires_workbench_input_exactly_for_schema_v7_before_reservation(
    tmp_path: Path,
) -> None:
    v7 = _create(tmp_path / "v7-source")
    missing_root = tmp_path / "v7-missing-sidecar"

    with pytest.raises(CorruptRunArtifact, match="workbench"):
        _save_copy(CityRunStore(missing_root), v7, workbench_input=None)

    assert not (missing_root / "city-runs" / "launch-run").exists()

    validated = _validated(run_id="legacy-response")
    v6 = create_city_run(
        validated.pack,
        root=tmp_path / "v6-source",
        run_id="legacy-response",
        seed=42,
        agent_count=2,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    unexpected_root = tmp_path / "v6-unexpected-sidecar"

    with pytest.raises(CorruptRunArtifact, match="workbench"):
        _save_copy(
            CityRunStore(unexpected_root),
            v6,
            workbench_input=validated.workbench_input,
        )

    assert not (unexpected_root / "city-runs" / "legacy-response").exists()


def test_v7_replay_refuses_missing_or_different_in_memory_workbench_binding(
    tmp_path: Path,
) -> None:
    loaded = CityRunStore(tmp_path).load(_create(tmp_path).manifest.run_id)

    with pytest.raises(CorruptRunArtifact, match="workbench"):
        replay_city_run(replace(loaded, workbench_input=None))

    other = _validated(run_id="other-run").workbench_input
    with pytest.raises(CorruptRunArtifact, match="workbench"):
        replay_city_run(replace(loaded, workbench_input=other))


@pytest.mark.parametrize("mutation", ["missing", "noncanonical", "invalid", "hash"])
def test_v7_load_refuses_missing_noncanonical_invalid_or_hash_drifted_sidecar(
    tmp_path: Path, mutation: str
) -> None:
    stored = _create(tmp_path)
    path = stored.directory / "inputs" / "workbench.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "noncanonical":
        path.write_text(
            json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2),
            encoding="utf-8",
        )
    elif mutation == "invalid":
        path.write_bytes(b"{}\n")
    else:
        assert stored.workbench_input is not None
        document = stored.workbench_input.model_dump(mode="json")
        document["draft"]["scenario"]["name"] = "A changed fictional study"
        path.write_text(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("launch-run")


def test_v7_load_refuses_oversized_or_symlinked_sidecar(tmp_path: Path) -> None:
    oversized = _create(tmp_path / "oversized")
    oversized_path = oversized.directory / "inputs" / "workbench.json"
    oversized_path.write_bytes(b"x" * (store_module.MAX_WORKBENCH_INPUT_BYTES + 1))
    with pytest.raises(CorruptRunArtifact, match="size"):
        CityRunStore(tmp_path / "oversized").load("launch-run")

    linked = _create(tmp_path / "linked")
    linked_path = linked.directory / "inputs" / "workbench.json"
    outside = tmp_path / "outside-workbench.json"
    outside.write_bytes(linked_path.read_bytes())
    linked_path.unlink()
    try:
        linked_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path / "linked").load("launch-run")


@pytest.mark.parametrize(
    "binding",
    [
        "run",
        "seed",
        "agent-count",
        "days",
        "city",
        "scenario",
        "campaign",
        "creative",
    ],
)
def test_v7_load_refuses_rehashed_sidecar_that_describes_other_scientific_inputs(
    tmp_path: Path, binding: str
) -> None:
    stored = _create(tmp_path)

    def mutate(document: dict[str, object]) -> None:
        draft = document["draft"]
        assert isinstance(draft, dict)
        settings = draft["settings"]
        scenario = draft["scenario"]
        assert isinstance(settings, dict) and isinstance(scenario, dict)
        if binding == "run":
            settings["run_id"] = "other-run"
        elif binding == "seed":
            settings["seed"] = 43
        elif binding == "agent-count":
            settings["agent_count"] = 3
        elif binding == "days":
            settings["days"] = 1
        elif binding == "city":
            draft["city_id"] = "another-fictional-city"
        elif binding == "scenario":
            scenario["scenario_id"] = "other-scenario"
        elif binding == "campaign":
            campaign = scenario["campaign"]
            assert isinstance(campaign, dict)
            campaign["campaign_id"] = "other-campaign"
        else:
            creative = document["creative_template"]
            assert isinstance(creative, dict)
            creative["message"] = "A different fictional creative."

    _rewrite_bound_input(stored, mutate)

    with pytest.raises(CorruptRunArtifact, match="workbench"):
        CityRunStore(tmp_path).load("launch-run")


@pytest.mark.parametrize("failed_name", ["workbench.json", ".run.json.tmp"])
def test_v7_duplicate_and_staged_write_failure_never_overwrite_or_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_name: str
) -> None:
    stored = _create(tmp_path / "first")
    before = _hashes(stored.directory)
    validated = _validated()
    with pytest.raises(DuplicateRun):
        create_city_run(
            validated.pack,
            root=tmp_path / "first",
            run_id="launch-run",
            seed=42,
            agent_count=2,
            days=2,
            spatial_scenario=validated.scenario,
            spatial_response=validated.response_input,
            workbench_input=validated.workbench_input,
        )
    assert _hashes(stored.directory) == before

    original = store_module._write_new

    def fail_staged_write(path: Path, contents: bytes) -> None:
        if path.name == failed_name:
            if failed_name == ".run.json.tmp":
                with path.open("xb") as target:
                    target.write(contents[:1])
            raise OSError("injected staged artifact failure")
        original(path, contents)

    monkeypatch.setattr(store_module, "_write_new", fail_staged_write)
    if failed_name == ".run.json.tmp":

        def fail_manifest_stage(source, contents):
            source.write(contents[:1])
            raise OSError("injected staged artifact failure")

        monkeypatch.setattr(store_module, "_write_manifest_stage", fail_manifest_stage)
    with pytest.raises(StorageError):
        _create(tmp_path / "failed")
    failed = tmp_path / "failed" / "city-runs" / "launch-run"
    assert failed.is_dir()
    assert not (failed / "run.json").exists()
    assert not (failed / ".run.json.tmp").exists()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path / "failed").load("launch-run")
    with pytest.raises(DuplicateRun):
        _create(tmp_path / "failed")


def _schema2_copy(workbench_input) -> WorkbenchRunInputV2:
    data = workbench_input.model_dump(mode="json")
    data["schema_version"] = data["draft"]["schema_version"] = 2
    cohort = data["draft"].pop("cohort")
    count = data["draft"]["settings"]["agent_count"]
    data["draft"]["population"] = {
        "personas": [{**persona_data(count=count), **cohort}],
        "assignments": [
            {"agent_id": f"person-{i:03d}", "persona_id": "persona-001"}
            for i in range(1, count + 1)
        ],
    }
    parsed = parse_workbench_run_input_json(json.dumps(data))
    assert isinstance(parsed, WorkbenchRunInputV2)
    return parsed


def test_schema2_scientific_binding_accepts_equivalent_explicit_assumptions() -> None:
    validated = _validated()
    schema2 = _schema2_copy(validated.workbench_input)
    bound = validate_workbench_execution_binding(
        schema2,
        pack=validated.pack,
        run_id="launch-run",
        seed=42,
        agent_count=2,
        days=2,
        places=None,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
    )
    assert isinstance(bound, WorkbenchRunInputV2)
    assert bound == schema2


def test_legacy_store_explicitly_refuses_schema2_before_reservation(tmp_path: Path) -> None:
    stored = _create(tmp_path / "source")
    destination = tmp_path / "destination"
    with pytest.raises(CorruptRunArtifact, match="workbench"):
        _save_copy(
            CityRunStore(destination), stored, workbench_input=_schema2_copy(stored.workbench_input)
        )
    assert not destination.exists()


def test_v7_loader_explicitly_refuses_schema2_sidecar_without_mutation(tmp_path: Path) -> None:
    stored = _create(tmp_path)
    schema2 = _schema2_copy(stored.workbench_input)
    (stored.directory / "inputs" / "workbench.json").write_text(
        canonical_json(schema2) + "\n", encoding="utf-8", newline="\n"
    )
    manifest = stored.manifest.model_copy(update={"workbench_input_sha256": schema2.fingerprint})
    (stored.directory / "run.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8", newline="\n"
    )
    before = _hashes(stored.directory)
    with pytest.raises(CorruptRunArtifact, match="workbench"):
        CityRunStore(tmp_path).load("launch-run")
    assert _hashes(stored.directory) == before


def _create_v8(root: Path):
    from tests.unit.city.test_workbench_validation import persona_draft

    validated = construct_workbench_run(persona_draft())
    return create_city_run(
        validated.pack,
        root=root,
        run_id="launch-run",
        seed=42,
        agent_count=3,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )


def _create_uploaded_workbench_run(
    root: Path,
    *,
    input_schema_version: int,
    run_id: str,
):
    workspace = prepare_workbench_workspace(root)
    city_id = f"registered-city-v{input_schema_version}"
    resolved = WorkbenchCityResolver(workspace).register_city(_document(city_id))
    wire_draft = _roadside_draft(
        input_schema_version,
        city_id=city_id,
        run_id=run_id,
    )
    draft = parse_workbench_http_draft(wire_draft)
    validated = construct_workbench_run(draft, selected_pack=resolved.pack)
    repository = WorkbenchRunRepository(workspace)
    stored = repository.publish(repository.prepare(validated))
    return workspace, resolved, stored


@pytest.mark.parametrize(
    ("input_schema_version", "run_schema_version"),
    [(1, 7), (2, 8)],
)
def test_uploaded_city_v7_v8_freezes_exact_canonical_bindings(
    tmp_path: Path,
    input_schema_version: int,
    run_schema_version: int,
) -> None:
    root = tmp_path / f"uploaded-v{run_schema_version}"
    run_id = f"frozen-upload-v{run_schema_version}"
    _workspace, resolved, stored = _create_uploaded_workbench_run(
        root,
        input_schema_version=input_schema_version,
        run_id=run_id,
    )
    assert stored.workbench_input is not None
    assert stored.spatial_scenario is not None
    assert stored.response_input is not None

    assert stored.manifest.schema_version == run_schema_version
    assert stored.workbench_input.schema_version == input_schema_version
    assert stored.workbench_input.draft.city_id == resolved.pack.city_id
    assert stored.pack == resolved.pack
    assert stored.manifest.city_sha256 == resolved.pack.fingerprint
    assert stored.manifest.workbench_input_schema_version == input_schema_version
    assert stored.manifest.workbench_input_sha256 == stored.workbench_input.fingerprint
    assert stored.spatial_scenario.city_id == resolved.pack.city_id
    assert stored.spatial_scenario.city_sha256 == resolved.pack.fingerprint
    assert stored.response_input.city_sha256 == resolved.pack.fingerprint

    assert (stored.directory / "inputs" / "city.json").read_bytes() == (
        canonical_json(resolved.pack) + "\n"
    ).encode("utf-8")
    assert (stored.directory / "inputs" / "workbench.json").read_bytes() == (
        canonical_json(stored.workbench_input) + "\n"
    ).encode("utf-8")

    loaded = CityRunStore(root).load(run_id)
    assert loaded.pack == resolved.pack
    assert loaded.workbench_input == stored.workbench_input
    assert loaded.spatial_scenario == stored.spatial_scenario
    assert loaded.response_input == stored.response_input


def test_v8_canonical_round_trip_and_replay_hash_every_source_artifact(tmp_path: Path) -> None:
    stored = _create_v8(tmp_path)
    assert stored.manifest.schema_version == 8
    assert stored.manifest.workbench_input_schema_version == 2
    assert stored.manifest.workbench_input_sha256 == stored.workbench_input.fingerprint
    assert (stored.directory / "inputs/workbench.json").read_bytes() == (
        canonical_json(stored.workbench_input) + "\n"
    ).encode("utf-8")
    before = _hashes(stored.directory)
    loaded = CityRunStore(tmp_path).load("launch-run")
    assert loaded.workbench_input == stored.workbench_input
    assert replay_city_run(loaded).identical is True
    assert _hashes(stored.directory) == before


@pytest.mark.parametrize("mutation", ["missing", "changed", "noncanonical", "oversized", "schema1"])
def test_v8_refuses_missing_changed_noncanonical_oversized_and_cross_paired_inputs(
    tmp_path: Path,
    mutation: str,
) -> None:
    stored = _create_v8(tmp_path)
    sidecar = stored.directory / "inputs/workbench.json"
    if mutation == "missing":
        sidecar.unlink()
    elif mutation == "oversized":
        sidecar.write_bytes(b"x" * (store_module.MAX_WORKBENCH_INPUT_BYTES + 2))
    elif mutation == "noncanonical":
        sidecar.write_text(json.dumps(json.loads(sidecar.read_bytes()), indent=2), encoding="utf-8")
    elif mutation == "schema1":
        legacy = _validated(agents=3).workbench_input
        sidecar.write_bytes((canonical_json(legacy) + "\n").encode("utf-8"))
        manifest = stored.manifest.model_copy(update={"workbench_input_sha256": legacy.fingerprint})
        (stored.directory / "run.json").write_bytes(
            (canonical_json(manifest) + "\n").encode("utf-8")
        )
    else:
        data = stored.workbench_input.model_dump(mode="json")
        data["draft"]["population"]["personas"][0]["display_name"] = "Changed fictional name"
        changed = parse_workbench_run_input_json(json.dumps(data))
        sidecar.write_bytes((canonical_json(changed) + "\n").encode("utf-8"))
    before = _hashes(stored.directory)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("launch-run")
    assert _hashes(stored.directory) == before


def test_v8_store_refuses_missing_and_cross_paired_inputs_before_reservation(
    tmp_path: Path,
) -> None:
    stored = _create_v8(tmp_path / "source")
    for index, workbench_input in enumerate((None, _validated(agents=3).workbench_input)):
        destination = tmp_path / f"refused-{index}"
        with pytest.raises(CorruptRunArtifact):
            _save_copy(CityRunStore(destination), stored, workbench_input=workbench_input)
        assert not destination.exists()


@pytest.mark.parametrize("boundary", ["save", "load", "replay"])
def test_v8_refuses_rehashed_but_causally_misassigned_population(
    tmp_path: Path, boundary: str
) -> None:
    stored = _create_v8(tmp_path / "source")

    def mutate(document):
        # A valid different allocation: person-002 is now assigned persona-002.
        population = document["draft"]["population"]
        population["personas"][0]["count"] = 1
        population["personas"][1]["count"] = 2
        population["assignments"][1]["persona_id"] = "persona-002"

    document = stored.workbench_input.model_dump(mode="json")
    mutate(document)
    changed = parse_workbench_run_input_json(json.dumps(document))
    changed_manifest = stored.manifest.model_copy(
        update={"workbench_input_sha256": changed.fingerprint}
    )
    with pytest.raises(CorruptRunArtifact):
        if boundary == "save":
            _save_copy(
                CityRunStore(tmp_path / "destination"),
                replace(stored, manifest=changed_manifest),
                workbench_input=changed,
            )
        elif boundary == "load":
            _rewrite_bound_input(stored, mutate)
            CityRunStore(tmp_path / "source").load("launch-run")
        else:
            replay_city_run(replace(stored, manifest=changed_manifest, workbench_input=changed))
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("failed_name", ["workbench.json", ".run.json.tmp"])
def test_v8_manifest_last_failure_is_not_reusable_or_clobbering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    stored = _create_v8(tmp_path / "source")
    before = _hashes(stored.directory)
    with pytest.raises(DuplicateRun):
        _create_v8(tmp_path / "source")
    assert _hashes(stored.directory) == before
    original = store_module._write_new

    def failing_write(path, contents):
        if path.name == failed_name:
            run_directory = path.parent if path.name == ".run.json.tmp" else path.parent.parent
            assert not (run_directory / "run.json").exists()
            raise OSError("injected failure")
        original(path, contents)

    monkeypatch.setattr(store_module, "_write_new", failing_write)
    if failed_name == ".run.json.tmp":

        def fail_manifest_stage(_source, _contents):
            assert not (tmp_path / "failed/city-runs/launch-run/run.json").exists()
            raise OSError("injected failure")

        monkeypatch.setattr(store_module, "_write_manifest_stage", fail_manifest_stage)
    with pytest.raises(StorageError):
        _create_v8(tmp_path / "failed")
    directory = tmp_path / "failed/city-runs/launch-run"
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path / "failed").load("launch-run")
    with pytest.raises(DuplicateRun):
        _create_v8(tmp_path / "failed")


def test_v8_sidecar_accounts_for_exactly_one_storage_newline(tmp_path: Path, monkeypatch) -> None:
    stored = _create_v8(tmp_path / "source")
    canonical = canonical_json(stored.workbench_input).encode("utf-8")
    # Exercise the storage bound at equality without changing the schema-2 model bounds.
    monkeypatch.setattr(store_module, "MAX_WORKBENCH_INPUT_BYTES", len(canonical))
    _save_copy(CityRunStore(tmp_path / "copy"), stored, workbench_input=stored.workbench_input)
    loaded = CityRunStore(tmp_path / "copy").load("launch-run")
    assert loaded.workbench_input.fingerprint == sha256(canonical).hexdigest()
    sidecar = loaded.directory / "inputs/workbench.json"
    assert sidecar.stat().st_size == len(canonical) + 1
    sidecar.write_bytes(canonical + b"\n\n")
    with pytest.raises(CorruptRunArtifact, match="size"):
        CityRunStore(tmp_path / "copy").load("launch-run")


def test_v8_refuses_symlinked_input_without_reading_the_target(tmp_path: Path) -> None:
    stored = _create_v8(tmp_path / "source")
    sidecar = stored.directory / "inputs/workbench.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(sidecar.read_bytes())
    sidecar.unlink()
    try:
        sidecar.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")
    before = outside.read_bytes()
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path / "source").load("launch-run")
    assert outside.read_bytes() == before
