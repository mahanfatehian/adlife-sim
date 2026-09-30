"""Saved city traces replay from frozen inputs without touching source bytes."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city.catalog import select_catalog_city
from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run, replay_city_run
from adlife.core.domain.city_run import CityRunManifestV4
from adlife.core.ports.run_store import CorruptRunArtifact
from tests.unit.city.test_city_catalog_contract import write_catalog_root
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


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


def test_spatial_city_run_replays_exact_opportunity_artifacts_without_mutation(
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

    assert isinstance(first.manifest, CityRunManifestV4)
    assert first.manifest == second.manifest
    assert first.spatial_scenario == scenario
    assert first.opportunity_evaluation is not None
    assert result.identical is True
    assert result.scenario_sha256 == scenario.fingerprint
    assert result.opportunity_stream_sha256 == first.manifest.opportunity_stream_sha256
    assert result.opportunity_summary_sha256 == first.manifest.opportunity_summary_sha256
    assert result.opportunity_stream_bytes == first.manifest.opportunity_stream_bytes
    assert result.opportunity_count == first.manifest.opportunity_count == 4
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

    assert isinstance(stored.manifest, CityRunManifestV4)
    assert stored.manifest.place_set_sha256 == places.fingerprint
    assert result.place_set_sha256 == places.fingerprint
    assert result.opportunity_count == 2


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
