"""A saved city trace is frozen, no-clobber and refused after artifact damage."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

from adlife import __version__
from adlife.city import run_store as city_store_module
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.runs import create_city_run
from adlife.core.domain import city_run
from adlife.core.domain.city import parse_city_pack_json
from adlife.core.domain.city_places import parse_city_place_set_json
from adlife.core.domain.city_run import CityRunManifest, CityRunManifestV4
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from adlife.core.simulation.city_mobility import CityMobility
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.spatial_opportunity import (
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    spatial_opportunity_lines,
    summarize_spatial_opportunity_artifact,
)
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario


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


def spatial_specimen() -> tuple[
    CityRunManifestV4,
    CityMobility,
    SpatialCampaignScenario,
    SpatialOpportunityEvaluation,
]:
    pack = load_pack(pack_data())
    mobility = CityMobility(pack, seed=42, agent_count=1, days=1)
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=2,
            )
        ],
    )
    return _spatial_manifest(mobility, scenario)


def _spatial_manifest(
    mobility: CityMobility,
    scenario: SpatialCampaignScenario,
) -> tuple[
    CityRunManifestV4,
    CityMobility,
    SpatialCampaignScenario,
    SpatialOpportunityEvaluation,
]:
    pack = mobility.pack
    trace = summarize_city_trace(mobility)
    evaluation = evaluate_spatial_opportunities(mobility, scenario)
    opportunity = summarize_spatial_opportunity_artifact(evaluation)
    opportunity_summary_bytes = (canonical_json(opportunity) + "\n").encode("utf-8")
    place_fields: dict[str, object] = {}
    if mobility.places is not None:
        place_fields = {
            "place_schema_version": mobility.places.schema_version,
            "place_set_sha256": mobility.places.fingerprint,
            "place_assignments_sha256": sha256(
                canonical_json(mobility.place_assignment_document()).encode("utf-8")
            ).hexdigest(),
        }
    manifest = CityRunManifestV4(
        run_id="spatial-study",
        package_version=__version__,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        city_sha256=pack.fingerprint,
        agents_sha256=trace.agents_sha256,
        trace_sha256=trace.trace_sha256,
        scenario_sha256=scenario.fingerprint,
        opportunity_stream_sha256=opportunity.stream_sha256,
        opportunity_summary_sha256=sha256(opportunity_summary_bytes).hexdigest(),
        opportunity_stream_bytes=opportunity.stream_bytes,
        opportunity_count=opportunity.counts.opportunity_count,
        seed=42,
        agent_count=1,
        days=1,
        frame_count=trace.frame_count,
        position_count=trace.position_count,
        city_schema_version=pack.schema_version,
        spatial_scenario_schema_version=scenario.schema_version,
        spatial_opportunity_schema_version=evaluation.schema_version,
        **place_fields,
    )
    return manifest, mobility, scenario, evaluation


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


def test_v2_save_freezes_canonical_geometry_and_loads_identical_trace(tmp_path: Path) -> None:
    pack = load_pack_v2(pack_v2_data())
    stored = create_city_run(pack, root=tmp_path, run_id="v2-study", seed=42, agent_count=2, days=1)
    loaded = CityRunStore(tmp_path).load("v2-study")
    city_bytes = (stored.directory / "inputs" / "city.json").read_bytes()
    assert stored.manifest.schema_version == 2
    assert stored.manifest.model_id == "illustrative-road-mobility-v2"
    assert loaded.pack == pack
    assert loaded.mobility.frame(481) == stored.mobility.frame(481)
    assert city_bytes == (canonical_json(pack) + "\n").encode("utf-8")


def test_v3_save_freezes_places_and_assignments_and_loads_identical_trace(
    tmp_path: Path,
) -> None:
    pack = load_pack_v2(pack_v2_data())
    places = mobility_place_set()
    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=7,
        places=places,
    )
    loaded = CityRunStore(tmp_path).load("place-study")

    model = getattr(city_run, "CityRunManifestV3", None)
    assert model is not None, "CityRunManifestV3 is not implemented"
    assert isinstance(stored.manifest, model)
    assert stored.manifest.model_id == "illustrative-road-mobility-v3"
    assert stored.manifest.place_set_sha256 == places.fingerprint
    assert loaded.mobility.places == places
    assert loaded.mobility.place_assignments == stored.mobility.place_assignments
    assert (stored.directory / "inputs" / "places.json").read_bytes() == (
        canonical_json(places) + "\n"
    ).encode("utf-8")
    assert (stored.directory / "inputs" / "place-assignments.json").read_bytes() == (
        canonical_json(stored.mobility.place_assignment_document()) + "\n"
    ).encode("utf-8")


def test_v4_save_freezes_canonical_spatial_inputs_and_outputs(tmp_path: Path) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)

    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    loaded = store.load("spatial-study")

    assert loaded.manifest == manifest
    assert loaded.spatial_scenario == scenario
    assert loaded.opportunity_evaluation == evaluation
    assert (directory / "inputs" / "spatial-campaign.json").read_bytes() == (
        canonical_json(scenario) + "\n"
    ).encode("utf-8")
    summary = summarize_spatial_opportunity_artifact(evaluation)
    assert (directory / "outputs" / "opportunity-summary.json").read_bytes() == (
        canonical_json(summary) + "\n"
    ).encode("utf-8")
    assert (directory / "outputs" / "spatial-opportunities.jsonl").read_bytes() == b"".join(
        spatial_opportunity_lines(evaluation)
    )
    assert manifest.opportunity_count == 2
    assert manifest.opportunity_stream_bytes > 0


def test_v4_save_supports_zero_opportunities_and_an_empty_stream(tmp_path: Path) -> None:
    pack = load_pack(pack_data())
    mobility = CityMobility(pack, seed=42, agent_count=1, days=1)
    manifest, _, scenario, evaluation = _spatial_manifest(
        mobility,
        _scenario(
            pack,
            [
                _phone(
                    windows=[{"start_minute": 0, "end_minute": 1}],
                    probability=0.0,
                )
            ],
        ),
    )
    store = CityRunStore(tmp_path)

    directory = store.save(
        manifest,
        pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    loaded = store.load("spatial-study")

    assert manifest.opportunity_count == 0
    assert manifest.opportunity_stream_bytes == 0
    assert (directory / "outputs" / "spatial-opportunities.jsonl").read_bytes() == b""
    assert loaded.opportunity_evaluation == evaluation


def test_v4_save_supports_places_and_mixed_channel_evidence(tmp_path: Path) -> None:
    pack = load_pack(pack_data())
    mobility = _mobility(pack)
    manifest, _, scenario, evaluation = _spatial_manifest(
        mobility,
        _scenario(
            pack,
            [
                _billboard(windows=[{"start_minute": 481, "end_minute": 482}]),
                _phone(
                    campaign_id="phone-campaign",
                    activities=["commute"],
                    windows=[{"start_minute": 481, "end_minute": 482}],
                    cap=1,
                ),
            ],
        ),
    )
    assert mobility.places is not None
    store = CityRunStore(tmp_path)

    directory = store.save(
        manifest,
        pack,
        mobility.agents,
        places=mobility.places,
        place_assignments=mobility.place_assignments,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    loaded = store.load("spatial-study")

    assert manifest.place_set_sha256 == mobility.places.fingerprint
    assert evaluation.counts.roadside_opportunity_count == 1
    assert evaluation.counts.phone_opportunity_count == 1
    assert (directory / "inputs" / "places.json").is_file()
    assert loaded.mobility.places == mobility.places
    assert loaded.opportunity_evaluation == evaluation


def test_v4_spatial_inputs_are_required_as_one_pair_before_reserving_id(
    tmp_path: Path,
) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)

    for extra in (
        {"spatial_scenario": scenario},
        {"opportunity_evaluation": evaluation},
        {},
    ):
        with pytest.raises(CorruptRunArtifact):
            store.save(manifest, mobility.pack, mobility.agents, **extra)
        assert not (tmp_path / "city-runs" / "spatial-study").exists()


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/spatial-campaign.json",
        "outputs/opportunity-summary.json",
        "outputs/spatial-opportunities.jsonl",
    ],
)
def test_missing_v4_spatial_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    (directory / artifact).unlink()

    with pytest.raises(CorruptRunArtifact):
        store.load("spatial-study")


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/spatial-campaign.json",
        "outputs/opportunity-summary.json",
        "outputs/spatial-opportunities.jsonl",
    ],
)
def test_changed_v4_spatial_artifact_is_refused_even_if_json_remains_valid(
    tmp_path: Path, artifact: str
) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    path = directory / artifact
    path.write_bytes(path.read_bytes() + b" \n")

    with pytest.raises(CorruptRunArtifact):
        store.load("spatial-study")


@pytest.mark.parametrize(
    ("artifact", "size"),
    [
        ("inputs/spatial-campaign.json", 2_097_153),
        ("outputs/opportunity-summary.json", 65_537),
    ],
)
def test_oversized_v4_document_is_refused_before_unbounded_read(
    tmp_path: Path, artifact: str, size: int
) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    with (directory / artifact).open("wb") as target:
        target.truncate(size)

    with pytest.raises(CorruptRunArtifact):
        store.load("spatial-study")


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/spatial-campaign.json",
        "outputs/opportunity-summary.json",
        "outputs/spatial-opportunities.jsonl",
    ],
)
def test_v4_artifact_cannot_follow_a_symlink(tmp_path: Path, artifact: str) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    target = directory / artifact
    outside = tmp_path / f"outside-{target.name}"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")

    with pytest.raises(UnsafeRunLocation):
        store.load("spatial-study")


def test_duplicate_v4_save_preserves_every_original_artifact_byte(tmp_path: Path) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    with pytest.raises(DuplicateRun):
        store.save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=evaluation,
        )

    assert before == {
        p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("artifact", ["inputs/places.json", "inputs/place-assignments.json"])
def test_missing_v3_place_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=1,
        places=mobility_place_set(),
    )
    (stored.directory / artifact).unlink()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("place-study")


def test_changed_but_valid_v3_place_set_is_refused(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=1,
        places=mobility_place_set(),
    )
    path = stored.directory / "inputs" / "places.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["name"] = "Changed fictional places"
    changed = parse_city_place_set_json(json.dumps(document))
    path.write_text(canonical_json(changed) + "\n", encoding="utf-8")
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("place-study")


def test_changed_v3_place_assignment_is_refused(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=1,
        places=mobility_place_set(),
    )
    path = stored.directory / "inputs" / "place-assignments.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    first, second = document["assignments"]
    assert first["home_place_id"] != second["home_place_id"]
    first["home_place_id"], second["home_place_id"] = (
        second["home_place_id"],
        first["home_place_id"],
    )
    first["home_node"], second["home_node"] = second["home_node"], first["home_node"]
    path.write_bytes((canonical_json(document) + "\n").encode("utf-8"))
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("place-study")


@pytest.mark.parametrize(
    ("artifact", "size"),
    [
        ("inputs/places.json", 1_048_577),
        ("inputs/place-assignments.json", 65_537),
    ],
)
def test_oversized_v3_place_artifact_is_refused(tmp_path: Path, artifact: str, size: int) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="place-study",
        seed=42,
        agent_count=2,
        days=1,
        places=mobility_place_set(),
    )
    with (stored.directory / artifact).open("wb") as target:
        target.truncate(size)
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("place-study")


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (("time_zone",), "Europe/London"),
        (("source", "version"), "changed-source"),
        (("roads", 0, "shape", 0, "latitude"), 0.003),
    ],
)
def test_changed_but_valid_v2_input_is_refused(
    tmp_path: Path, member: tuple[str | int, ...], value: object
) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="v2-study",
        seed=42,
        agent_count=2,
        days=1,
    )
    city_file = stored.directory / "inputs" / "city.json"
    root = json.loads(city_file.read_text(encoding="utf-8"))
    cursor: object = root
    for part in member[:-1]:
        if isinstance(cursor, dict):
            assert isinstance(part, str)
            cursor = cursor[part]
        else:
            assert isinstance(cursor, list) and isinstance(part, int)
            cursor = cursor[part]
    final = member[-1]
    if isinstance(cursor, dict):
        assert isinstance(final, str)
        cursor[final] = value
    else:
        assert isinstance(cursor, list) and isinstance(final, int)
        cursor[final] = value
    changed = parse_city_pack_json(json.dumps(root))
    city_file.write_text(canonical_json(changed) + "\n", encoding="utf-8")
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("v2-study")


def test_manifest_and_pack_schema_versions_must_match(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack_v2(pack_v2_data()),
        root=tmp_path,
        run_id="v2-study",
        seed=42,
        agent_count=2,
        days=1,
    )
    manifest_file = stored.directory / "run.json"
    document = json.loads(manifest_file.read_text(encoding="utf-8"))
    document["schema_version"] = 1
    document["model_id"] = "illustrative-road-mobility-v1"
    del document["city_schema_version"]
    manifest_file.write_text(canonical_json(document) + "\n", encoding="utf-8")
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("v2-study")


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


@pytest.mark.parametrize("artifact", ["inputs/city.json", "inputs/agents.json"])
def test_truncated_frozen_input_is_refused(tmp_path: Path, artifact: str) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(manifest, mobility.pack, mobility.agents)
    (directory / artifact).write_bytes(b"{")

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


def test_failed_v4_stream_write_cannot_publish_a_completed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)

    def fail_stream(path: Path, value: SpatialOpportunityEvaluation) -> None:
        raise OSError("injected spatial stream failure")

    monkeypatch.setattr(city_store_module, "_write_opportunity_stream", fail_stream)
    with pytest.raises(StorageError):
        store.save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=evaluation,
        )

    directory = tmp_path / "city-runs" / "spatial-study"
    assert directory.exists()
    assert not (directory / "run.json").exists()
    assert (directory / "outputs" / "opportunity-summary.json").is_file()
    with pytest.raises(CorruptRunArtifact):
        store.load("spatial-study")


@pytest.mark.parametrize("short_file", ["city.json", "agents.json", ".run.json.tmp"])
def test_short_write_cannot_publish_a_completed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_file: str
) -> None:
    manifest, mobility = specimen()
    store = CityRunStore(tmp_path)
    original_write = city_store_module._write_new

    def short_write(path: Path, contents: bytes) -> None:
        if path.name == short_file:
            with path.open("xb") as target:
                target.write(contents[:-1])
        else:
            original_write(path, contents)

    monkeypatch.setattr(city_store_module, "_write_new", short_write)
    with pytest.raises(StorageError):
        store.save(manifest, mobility.pack, mobility.agents)

    directory = tmp_path / "city-runs" / "sample-run"
    assert not (directory / "run.json").exists()
    assert not (directory / ".run.json.tmp").exists()
    with pytest.raises(CorruptRunArtifact):
        store.load("sample-run")


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
