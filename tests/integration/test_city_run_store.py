"""A saved city trace is frozen, no-clobber and refused after artifact damage."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from adlife import __version__
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.core.domain.city_run import CityRunManifest
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace
from tests.unit.city.test_city_pack import load_pack, pack_data


def specimen() -> tuple[CityRunManifest, CityMobility]:
    mobility = CityMobility(load_pack(pack_data()), seed=42, agent_count=2, days=1)
    summary = summarize_city_trace(mobility)
    manifest = CityRunManifest(
        run_id="sample-run",
        package_version=__version__,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        city_sha256=mobility.pack.fingerprint,
        agents_sha256=summary.agents_sha256,
        trace_sha256=summary.trace_sha256,
        seed=42,
        agent_count=2,
        days=1,
        frame_count=summary.frame_count,
        position_count=summary.position_count,
    )
    return manifest, mobility


def test_save_freezes_inputs_and_loads_an_identical_trace(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)

    directory = store.save(manifest, mobility.pack, mobility.agents)
    loaded = store.load(manifest.run_id)

    assert directory == tmp_path / "city-runs" / "sample-run"
    assert (directory / "run.json").is_file()
    assert (directory / "inputs" / "city.json").is_file()
    assert (directory / "inputs" / "agents.json").is_file()
    assert isinstance(loaded, StoredCityRun)
    assert loaded.manifest == manifest
    assert loaded.pack == mobility.pack
    assert loaded.mobility.frame(480) == mobility.frame(480)


def test_duplicate_save_preserves_every_original_artifact_byte(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    with pytest.raises(DuplicateRun):
        store.save(manifest, mobility.pack, mobility.agents)

    assert before == {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


def test_two_writers_racing_for_one_run_id_cannot_replace_the_winner(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)

    def attempt() -> object:
        try:
            return store.save(manifest, mobility.pack, mobility.agents)
        except DuplicateRun as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))

    assert sum(isinstance(result, Path) for result in results) == 1
    assert sum(isinstance(result, DuplicateRun) for result in results) == 1
    assert store.load("sample-run").manifest == manifest


def test_mismatched_manifest_is_refused_before_it_creates_a_run(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    bad = manifest.model_copy(update={"city_sha256": "0" * 64})
    store = CityRunStore(tmp_path)

    with pytest.raises(CorruptRunArtifact):
        store.save(bad, mobility.pack, mobility.agents)

    assert not (tmp_path / "city-runs" / "sample-run").exists()


@pytest.mark.parametrize("artifact", ["run.json", "inputs/city.json", "inputs/agents.json"])
def test_missing_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    (directory / artifact).unlink()

    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


def test_changed_but_valid_city_input_is_refused(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    data = pack_data()
    data["name"] = "Changed fictional city"
    changed = load_pack(data)
    (directory / "inputs" / "city.json").write_text(
        canonical_json(changed) + "\n", encoding="utf-8"
    )

    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


def test_changed_but_valid_agent_assignment_is_refused(tmp_path: Path) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    path = directory / "inputs" / "agents.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["agents"][0]["home_node"] = "c"
    path.write_text(canonical_json(document) + "\n", encoding="utf-8")

    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


@pytest.mark.parametrize("artifact", ["run.json", "inputs/city.json", "inputs/agents.json"])
def test_oversized_artifact_is_refused_before_unbounded_read(tmp_path: Path, artifact: str) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    with (directory / artifact).open("wb") as target:
        target.truncate(4_194_305)

    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


def test_partial_published_directory_is_not_a_completed_run(tmp_path: Path) -> None:
    directory = tmp_path / "city-runs" / "sample-run"
    (directory / "inputs").mkdir(parents=True)
    (directory / "inputs" / "city.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("sample-run")


@pytest.mark.parametrize("document", [b"{", b"{}", b'{"schema_version":99}'])
def test_malformed_or_incompatible_manifest_is_refused(tmp_path: Path, document: bytes) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    (directory / "run.json").write_bytes(document)

    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


def test_failed_final_publication_is_not_loadable_or_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)

    def fail_publication(source: Path, destination: Path) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr("adlife.city.run_store.os.replace", fail_publication)
    with pytest.raises(StorageError):
        store.save(manifest, mobility.pack, mobility.agents)

    directory = tmp_path / "city-runs" / "sample-run"
    assert directory.exists()
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")
    with pytest.raises(DuplicateRun):
        store.save(manifest, mobility.pack, mobility.agents)


def test_run_location_cannot_follow_a_symlink_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    city_runs = tmp_path / "city-runs"
    city_runs.mkdir()
    try:
        (city_runs / "sample-run").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create directory symlinks")

    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load("sample-run")


def test_invalid_run_identifier_is_refused_before_a_path_is_built(tmp_path: Path) -> None:
    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load("../elsewhere")
