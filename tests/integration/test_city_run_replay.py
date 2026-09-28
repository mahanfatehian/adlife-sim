"""Saved city traces replay from frozen inputs without touching source bytes."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from adlife.city.run_store import CityRunStore
from adlife.city.runs import create_city_run, replay_city_run
from adlife.core.ports.run_store import CorruptRunArtifact
from tests.unit.city.test_city_pack import load_pack, pack_data


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
