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
from adlife.core.domain.city_run import CityRunManifestV6, CityRunManifestV7
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from tests.unit.city.test_city_run_preparation import _validated


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


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


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
    assert isinstance(stored.manifest, CityRunManifestV7)
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
