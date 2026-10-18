"""A saved city trace is frozen, no-clobber and refused after artifact damage."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
from adlife.core.simulation.spatial_attention import (
    SpatialAttentionEvaluation,
    evaluate_spatial_attention,
    spatial_attention_lines,
    summarize_spatial_attention_artifact,
)
from adlife.core.simulation.spatial_opportunity import (
    SpatialOpportunityEvaluation,
    evaluate_spatial_opportunities,
    spatial_opportunity_lines,
    summarize_spatial_opportunity_artifact,
)
from tests.unit.city.test_city_mobility import mobility_place_set
from tests.unit.city.test_city_pack import load_pack, load_pack_v2, pack_data, pack_v2_data
from tests.unit.city.test_spatial_opportunity import _billboard, _mobility, _phone, _scenario


def test_v10_store_uses_frozen_core_capacity_and_payload_lf_scopes(tmp_path, monkeypatch):
    from adlife.core.simulation import spatial_response_v2 as response
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, prepared, _ = _saved_v10(tmp_path)
    frozen = prepared.cognition_input
    assert frozen.max_request_bytes == frozen.max_terminal_record_bytes == 8192
    expected = {
        "MAX_CITY_COGNITION_INPUT_BYTES_V2": 131_072,
        "MAX_CITY_COGNITION_STREAM_BYTES": 1_048_576,
        "MAX_CITY_COGNITION_SUMMARY_BYTES": 16_384,
        "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2": 2_147_483_648,
        "MAX_SPATIAL_RESPONSE_STATE_BYTES_V2": 8_388_608,
        "MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2": 131_072,
    }
    for name, literal in expected.items():
        assert getattr(city_store_module, name, None) == literal
    assert (
        city_store_module.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2
        == response.MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2
    )
    assert (
        city_store_module.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2
        == response.MAX_SPATIAL_RESPONSE_STATE_BYTES_V2
    )
    assert (
        city_store_module.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2
        == response.MAX_SPATIAL_RESPONSE_SUMMARY_BYTES_V2
    )
    required = {
        "inputs/cognition.json": 131_073,
        "outputs/cognition-summary.json": 16_385,
        "outputs/response-state.json": 8_388_608,
        "outputs/response-summary.json": 131_072,
    }
    observed = {}
    original = CityRunStore._read_document

    def read_with_scope(self, path, *, limit, label):
        relative = "/".join(path.parts[-2:])
        if relative in required:
            observed[relative] = limit
        return original(self, path, limit=limit, label=label)

    monkeypatch.setattr(CityRunStore, "_read_document", read_with_scope)
    CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert observed == required


class _GeneratedReader:
    """A bounded repeated-byte stream, with no allocation proportional to its size."""

    def __init__(self, handle, size, *, chunk=65_536, short=False):
        self.handle, self.size, self.chunk, self.short = handle, size, chunk, short
        self.position = 0
        self.max_requested = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()

    def fileno(self):
        return self.handle.fileno()

    def read(self, count=-1):
        assert 0 <= count <= self.chunk, "V10 attempted an unbounded or giant read"
        self.max_requested = max(count, self.max_requested)
        count = min(count, self.size - self.position)
        if self.short:
            count = min(count, 17)
        self.position += count
        return b"x" * count


@pytest.mark.parametrize("limit", [131_073, 16_385, 8_388_608, 131_072])
@pytest.mark.parametrize("overflow", [0, 1])
@pytest.mark.parametrize("short", [False, True])
def test_v10_document_bounded_io_accepts_exact_physical_limit_and_refuses_plus_one(
    tmp_path,
    monkeypatch,
    limit,
    overflow,
    short,
):
    path = tmp_path / "bounded-document.json"
    path.write_bytes(b"placeholder")
    original = city_store_module._open_artifact
    readers = []

    @contextmanager
    def generated(path_arg, mode="r", **kwargs):
        with original(path_arg, mode, **kwargs) as handle:
            if path_arg == path and mode == "rb":
                reader = _GeneratedReader(handle, limit + overflow, short=short)
                readers.append(reader)
                yield reader
            else:
                yield handle

    monkeypatch.setattr(city_store_module, "_open_artifact", generated)
    if overflow:
        with pytest.raises(CorruptRunArtifact):
            CityRunStore(tmp_path)._read_document(path, limit=limit, label="bounded V10 document")
    else:
        data = CityRunStore(tmp_path)._read_document(
            path, limit=limit, label="bounded V10 document"
        )
        assert len(data) == limit
    assert readers and readers[0].position == limit + overflow
    assert readers[0].max_requested <= 65_536


def test_v10_thirty_agent_seven_day_store_streams_real_admitted_evidence(tmp_path, monkeypatch):
    from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10, _save_prepared

    staged, prepared = _prepared_v10(tmp_path, agents=30, days=7)
    original_open = city_store_module._open_artifact
    original_read_bytes = Path.read_bytes
    reads = []

    class Guard:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, count=-1):
            assert 0 <= count <= 65_536
            reads.append(count)
            return self.handle.read(count)

    @contextmanager
    def bounded_open(path, mode="r", **kwargs):
        with original_open(path, mode, **kwargs) as handle:
            yield (
                Guard(handle) if path.name == "spatial-responses.jsonl" and mode == "rb" else handle
            )

    def bounded_read_bytes(path):
        assert path.name != "spatial-responses.jsonl", (
            "response stream was retained as a whole byte copy"
        )
        return original_read_bytes(path)

    monkeypatch.setattr(city_store_module, "_open_artifact", bounded_open)
    monkeypatch.setattr(Path, "read_bytes", bounded_read_bytes)
    directory = _save_prepared(tmp_path, prepared)
    stored = CityRunStore(tmp_path).load(prepared.manifest.run_id)
    assert reads
    digest = sha256()
    count = 0
    with original_open(directory / "outputs/spatial-responses.jsonl", "rb") as source:
        while chunk := source.read(65_536):
            count += len(chunk)
            digest.update(chunk)
    assert count == prepared.manifest.response_stream_bytes < 2_147_483_648
    assert digest.hexdigest() == prepared.manifest.response_stream_sha256
    assert stored.response_evaluation == prepared.response_evaluation
    assert stored.response_evaluation.final_rule_states == staged.baseline_evaluation.final_states
    assert stored.manifest.agent_count == 30 and stored.manifest.days == 7


@pytest.mark.parametrize("delta", [0, 1])
def test_v10_real_response_writer_and_verifier_use_one_inclusive_combined_counter(
    tmp_path, monkeypatch, delta
):
    from adlife.core.simulation.spatial_response_v2 import spatial_response_v2_lines
    from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10

    _, prepared = _prepared_v10(tmp_path)
    evaluation = prepared.response_evaluation
    exact = sum(map(len, spatial_response_v2_lines(evaluation)))
    assert getattr(city_store_module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", None) == 2_147_483_648
    monkeypatch.setattr(city_store_module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", exact - delta)
    path = tmp_path / "spatial-responses.jsonl"
    if delta:
        with pytest.raises(CorruptRunArtifact):
            city_store_module._write_response_stream(path, evaluation)
    else:
        city_store_module._write_response_stream(path, evaluation)
        CityRunStore(tmp_path)._verify_response_stream(path, evaluation)
        assert path.stat().st_size == exact


@pytest.mark.parametrize(
    "declared_size,actual_size,admitted",
    [
        (2_147_483_648, 2_147_483_648, True),
        (2_147_483_649, 2_147_483_649, False),
        (2_147_483_648, 2_147_483_649, False),
        (2_147_483_648, 2_147_483_647, False),
        (2_311_310_880, 2_311_310_880, False),
    ],
)
def test_v10_virtual_response_io_enforces_frozen_two_gib_combined_limit(
    tmp_path,
    monkeypatch,
    declared_size,
    actual_size,
    admitted,
):
    from types import SimpleNamespace

    from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10

    _, prepared = _prepared_v10(tmp_path)
    evaluation = prepared.response_evaluation
    assert getattr(city_store_module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", None) == 2_147_483_648
    path = tmp_path / "spatial-responses.jsonl"
    path.write_bytes(b"placeholder")
    original_open, original_stat = city_store_module._open_artifact, Path.stat
    readers = []

    def lines(_evaluation):
        assert _evaluation is evaluation
        remaining = declared_size
        block = b"x" * 65_536
        while remaining:
            amount = min(remaining, len(block))
            yield block[:amount]
            remaining -= amount

    @contextmanager
    def generated(path_arg, mode="r", **kwargs):
        with original_open(path_arg, mode, **kwargs) as handle:
            if path_arg == path and mode == "rb":
                reader = _GeneratedReader(handle, actual_size)
                readers.append(reader)
                yield reader
            else:
                yield handle

    def size_stat(value):
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
        fields["st_size"] = declared_size
        return SimpleNamespace(**fields)

    def generated_stat(path_arg, *args, **kwargs):
        value = original_stat(path_arg, *args, **kwargs)
        return size_stat(value) if path_arg == path else value

    monkeypatch.setattr(city_store_module, "spatial_response_v2_lines", lines, raising=False)
    monkeypatch.setattr(
        city_store_module,
        "summarize_spatial_response_artifact_v2",
        lambda _: SimpleNamespace(stream_bytes=declared_size),
        raising=False,
    )
    monkeypatch.setattr(city_store_module, "_open_artifact", generated)
    monkeypatch.setattr(Path, "stat", generated_stat)
    if admitted:
        CityRunStore(tmp_path)._verify_response_stream(path, evaluation)
        assert readers and readers[0].position == 2_147_483_648
    else:
        with pytest.raises(CorruptRunArtifact):
            CityRunStore(tmp_path)._verify_response_stream(path, evaluation)
        if declared_size > 2_147_483_648:
            assert not readers or readers[0].position == 0
    assert all(reader.max_requested <= 65_536 for reader in readers)


@pytest.mark.parametrize("size", [2_147_483_648, 2_147_483_649, 2_311_310_880])
@pytest.mark.parametrize("short_write", [False, True])
def test_v10_virtual_response_writer_refuses_combined_overflow_before_writing_it(
    tmp_path,
    monkeypatch,
    size,
    short_write,
):
    from tests.integration.test_city_cognition_v2_artifacts import _prepared_v10

    _, prepared = _prepared_v10(tmp_path)
    evaluation = prepared.response_evaluation
    assert getattr(city_store_module, "MAX_SPATIAL_RESPONSE_STREAM_BYTES_V2", None) == 2_147_483_648
    path = tmp_path / "virtual-response.jsonl"
    original = city_store_module._open_artifact
    written = 0

    class Sink:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, block):
            nonlocal written
            assert len(block) <= 65_536
            assert written + len(block) <= 2_147_483_648, "writer wrote overflow before admission"
            amount = min(17, len(block)) if short_write else len(block)
            written += amount
            return amount

    @contextmanager
    def virtual_open(path_arg, mode="r", **kwargs):
        with original(path_arg, mode, **kwargs) as handle:
            yield Sink(handle) if path_arg == path and mode == "xb" else handle

    def lines(_evaluation):
        assert _evaluation is evaluation
        remaining = size
        block = b"x" * 65_536
        while remaining:
            count = min(remaining, len(block))
            yield block[:count]
            remaining -= count

    monkeypatch.setattr(city_store_module, "_open_artifact", virtual_open)
    monkeypatch.setattr(city_store_module, "spatial_response_v2_lines", lines, raising=False)
    if size > 2_147_483_648 or short_write:
        expected = OSError if short_write else CorruptRunArtifact
        with pytest.raises(expected) as error:
            city_store_module._write_response_stream(path, evaluation)
        assert type(error.value) is expected
        assert written <= 2_147_483_648
    else:
        city_store_module._write_response_stream(path, evaluation)
        assert written == 2_147_483_648


def test_bounded_io_double_exercises_the_nonwindows_descriptor_branch(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    original_open = os.open
    observed = []

    @contextmanager
    def synthetic_parent(_path):
        yield 12345

    def native_open(name, flags, mode=0o777, *, dir_fd=None):
        assert dir_fd == 12345
        observed.append(flags)
        return original_open(tmp_path / str(name), flags, mode)

    monkeypatch.setattr(city_store_module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(city_store_module, "_hold_parents", synthetic_parent)
    monkeypatch.setattr(os, "open", native_open)
    path = tmp_path / "descriptor-document"
    city_store_module._write_new(path, b"small physical fixture")
    original_artifact = city_store_module._open_artifact
    readers = []

    @contextmanager
    def generated(path_arg, mode, **kwargs):
        with original_artifact(path_arg, mode, **kwargs) as source:
            reader = _GeneratedReader(source, 131_073, short=True)
            readers.append(reader)
            yield reader

    monkeypatch.setattr(city_store_module, "_open_artifact", generated)
    data = CityRunStore(tmp_path)._read_document(path, limit=131_073, label="bounded document")
    assert len(data) == 131_073
    assert readers[0].max_requested <= 65_536
    assert len(observed) == 2


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


def attention_specimen(
    *, probability: float = 1.0
) -> tuple[
    object,
    CityMobility,
    SpatialCampaignScenario,
    SpatialOpportunityEvaluation,
    SpatialAttentionEvaluation,
]:
    pack = load_pack(pack_data())
    mobility = CityMobility(pack, seed=42, agent_count=1, days=1)
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=probability,
                cap=2,
            )
        ],
    )
    trace = summarize_city_trace(mobility)
    opportunity_evaluation = evaluate_spatial_opportunities(mobility, scenario)
    opportunity = summarize_spatial_opportunity_artifact(opportunity_evaluation)
    opportunity_summary_bytes = (canonical_json(opportunity) + "\n").encode("utf-8")
    attention_evaluation = evaluate_spatial_attention(opportunity_evaluation, seed=42)
    attention = summarize_spatial_attention_artifact(attention_evaluation)
    attention_summary_bytes = (canonical_json(attention) + "\n").encode("utf-8")
    manifest_model = getattr(city_run, "CityRunManifestV5", None)
    assert manifest_model is not None, "CityRunManifestV5 is not implemented"
    manifest = manifest_model(
        run_id="attention-study",
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
        attention_stream_sha256=attention.stream_sha256,
        attention_summary_sha256=sha256(attention_summary_bytes).hexdigest(),
        attention_stream_bytes=attention.stream_bytes,
        impression_count=attention.counts.impression_count,
        noticed_count=attention.counts.noticed_count,
        seed=42,
        agent_count=1,
        days=1,
        frame_count=trace.frame_count,
        position_count=trace.position_count,
        city_schema_version=pack.schema_version,
        spatial_scenario_schema_version=scenario.schema_version,
        spatial_opportunity_schema_version=opportunity_evaluation.schema_version,
        spatial_attention_schema_version=attention_evaluation.schema_version,
        spatial_attention_model_id=attention_evaluation.model_id,
    )
    return manifest, mobility, scenario, opportunity_evaluation, attention_evaluation


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


def test_v5_save_freezes_and_loads_exact_attention_evidence(tmp_path: Path) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)

    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    loaded = store.load("attention-study")

    assert loaded.manifest == manifest
    assert loaded.opportunity_evaluation == opportunities
    assert loaded.attention_evaluation == attention
    summary = summarize_spatial_attention_artifact(attention)
    assert (directory / "outputs" / "attention-summary.json").read_bytes() == (
        canonical_json(summary) + "\n"
    ).encode("utf-8")
    assert (directory / "outputs" / "spatial-attention.jsonl").read_bytes() == b"".join(
        spatial_attention_lines(attention)
    )


def test_v5_save_supports_empty_attention_evidence(tmp_path: Path) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen(probability=0.0)
    store = CityRunStore(tmp_path)

    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    loaded = store.load("attention-study")

    assert manifest.opportunity_count == manifest.impression_count == manifest.noticed_count == 0
    assert manifest.attention_stream_bytes == 0
    assert (directory / "outputs" / "spatial-attention.jsonl").read_bytes() == b""
    assert loaded.attention_evaluation == attention


def test_v5_attention_input_is_required_and_recomputed_before_reserving_id(
    tmp_path: Path,
) -> None:
    manifest, mobility, scenario, opportunities, _attention = attention_specimen()
    store = CityRunStore(tmp_path)

    with pytest.raises(CorruptRunArtifact):
        store.save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
        )
    assert not (tmp_path / "city-runs" / "attention-study").exists()

    changed = evaluate_spatial_attention(opportunities, seed=43)
    with pytest.raises(CorruptRunArtifact):
        store.save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=changed,
        )
    assert not (tmp_path / "city-runs" / "attention-study").exists()


@pytest.mark.parametrize(
    "artifact",
    ["outputs/attention-summary.json", "outputs/spatial-attention.jsonl"],
)
def test_missing_v5_attention_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    (directory / artifact).unlink()

    with pytest.raises(CorruptRunArtifact):
        store.load("attention-study")


@pytest.mark.parametrize(
    "artifact",
    ["outputs/attention-summary.json", "outputs/spatial-attention.jsonl"],
)
def test_changed_v5_attention_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    path = directory / artifact
    path.write_bytes(path.read_bytes() + b" \n")

    with pytest.raises(CorruptRunArtifact):
        store.load("attention-study")


def test_appended_valid_attention_record_is_refused(tmp_path: Path) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    stream = directory / "outputs" / "spatial-attention.jsonl"
    first_line = stream.read_bytes().splitlines(keepends=True)[0]
    with stream.open("ab") as target:
        target.write(first_line)

    with pytest.raises(CorruptRunArtifact):
        store.load("attention-study")


@pytest.mark.parametrize(
    ("artifact", "size"),
    [
        ("outputs/attention-summary.json", 65_537),
        ("outputs/spatial-attention.jsonl", 1_073_741_825),
    ],
)
def test_oversized_v5_attention_artifact_is_refused(
    tmp_path: Path, artifact: str, size: int
) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
    )
    with (directory / artifact).open("wb") as target:
        target.truncate(size)

    with pytest.raises(CorruptRunArtifact):
        store.load("attention-study")


@pytest.mark.parametrize(
    "artifact",
    ["outputs/attention-summary.json", "outputs/spatial-attention.jsonl"],
)
def test_v5_attention_artifact_cannot_follow_a_symlink(tmp_path: Path, artifact: str) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)
    directory = store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=opportunities,
        attention_evaluation=attention,
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
        store.load("attention-study")


def test_v4_load_does_not_fabricate_attention(tmp_path: Path) -> None:
    manifest, mobility, scenario, evaluation = spatial_specimen()
    store = CityRunStore(tmp_path)
    store.save(
        manifest,
        mobility.pack,
        mobility.agents,
        spatial_scenario=scenario,
        opportunity_evaluation=evaluation,
    )

    loaded = store.load("spatial-study")
    assert loaded.attention_evaluation is None
    assert loaded.response_input is None
    assert loaded.response_evaluation is None


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

    def fail_publication(*_args, **_kwargs) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(city_store_module, "_publish_manifest", fail_publication)
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


def test_failed_v5_attention_stream_write_cannot_publish_a_completed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, mobility, scenario, opportunities, attention = attention_specimen()
    store = CityRunStore(tmp_path)

    def fail_stream(path: Path, value: SpatialAttentionEvaluation) -> None:
        raise OSError("injected attention stream failure")

    monkeypatch.setattr(city_store_module, "_write_attention_stream", fail_stream)
    with pytest.raises(StorageError):
        store.save(
            manifest,
            mobility.pack,
            mobility.agents,
            spatial_scenario=scenario,
            opportunity_evaluation=opportunities,
            attention_evaluation=attention,
        )

    directory = tmp_path / "city-runs" / "attention-study"
    assert directory.exists()
    assert not (directory / "run.json").exists()
    assert (directory / "outputs" / "attention-summary.json").is_file()
    with pytest.raises(CorruptRunArtifact):
        store.load("attention-study")


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
    if short_file == ".run.json.tmp":

        def short_stage(source, contents):
            source.write(contents[:-1])
            source.flush()

        monkeypatch.setattr(city_store_module, "_write_manifest_stage", short_stage)
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
