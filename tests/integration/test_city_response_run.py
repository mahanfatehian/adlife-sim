"""Schema-v6 city runs freeze and replay bounded spatial response evidence."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

import adlife.city.run_store as city_store_module
from adlife.city.run_store import CityRunStore, StoredCityRun
from adlife.city.runs import create_city_run, replay_city_run
from adlife.core.domain.city_run import CityRunManifestV5, CityRunManifestV6
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_response import SpatialResponseInput
from adlife.core.ports.run_store import (
    CorruptRunArtifact,
    DuplicateRun,
    StorageError,
    UnsafeRunLocation,
)
from adlife.core.simulation.spatial_response import (
    SpatialResponseEvaluation,
    spatial_response_lines,
    spatial_response_state_document,
    summarize_spatial_response_artifact,
)
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _create_response_run(
    root: Path,
    *,
    run_id: str = "response-study",
    probability: float = 1.0,
    seed: int = 42,
):
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 3}],
                probability=probability,
                cap=2,
            )
        ],
    )
    response_input = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    return create_city_run(
        pack,
        root=root,
        run_id=run_id,
        seed=seed,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=response_input,
    )


def test_v6_run_freezes_exact_response_artifacts_and_loads_them(tmp_path: Path) -> None:
    stored = _create_response_run(tmp_path)
    loaded = CityRunStore(tmp_path).load("response-study")

    assert isinstance(stored.manifest, CityRunManifestV6)
    assert loaded.manifest == stored.manifest
    assert loaded.response_input == stored.response_input
    assert loaded.response_evaluation == stored.response_evaluation
    assert loaded.response_input is not None
    assert loaded.response_evaluation is not None
    summary = summarize_spatial_response_artifact(loaded.response_evaluation)
    state = spatial_response_state_document(loaded.response_evaluation)
    directory = stored.directory
    response_input_bytes = (directory / "inputs" / "spatial-response.json").read_bytes()
    response_stream_bytes = (directory / "outputs" / "spatial-responses.jsonl").read_bytes()
    response_state_bytes = (directory / "outputs" / "response-state.json").read_bytes()
    response_summary_bytes = (directory / "outputs" / "response-summary.json").read_bytes()
    assert response_input_bytes == (canonical_json(loaded.response_input) + "\n").encode("utf-8")
    assert response_stream_bytes == b"".join(spatial_response_lines(loaded.response_evaluation))
    assert response_state_bytes == (canonical_json(state) + "\n").encode("utf-8")
    assert response_summary_bytes == (canonical_json(summary) + "\n").encode("utf-8")
    assert stored.manifest.response_input_sha256 == loaded.response_input.fingerprint
    assert (
        stored.manifest.response_input_sha256
        == sha256(response_input_bytes.removesuffix(b"\n")).hexdigest()
    )
    assert stored.manifest.response_stream_sha256 == summary.stream_sha256
    assert stored.manifest.response_stream_sha256 == sha256(response_stream_bytes).hexdigest()
    assert stored.manifest.response_state_sha256 == summary.state_document_sha256
    assert stored.manifest.response_state_sha256 == sha256(response_state_bytes).hexdigest()
    assert stored.manifest.response_summary_sha256 == sha256(response_summary_bytes).hexdigest()
    assert stored.manifest.response_count == stored.manifest.noticed_count


def _save_copy(
    store: CityRunStore,
    stored: StoredCityRun,
    *,
    response_input: SpatialResponseInput | None,
    response_evaluation: SpatialResponseEvaluation | None,
) -> Path:
    return store.save(
        stored.manifest,
        stored.pack,
        stored.mobility.agents,
        places=stored.mobility.places,
        place_assignments=stored.mobility.place_assignments,
        spatial_scenario=stored.spatial_scenario,
        opportunity_evaluation=stored.opportunity_evaluation,
        attention_evaluation=stored.attention_evaluation,
        response_input=response_input,
        response_evaluation=response_evaluation,
    )


def test_v6_zero_notice_run_has_complete_state_and_empty_stream(tmp_path: Path) -> None:
    stored = _create_response_run(tmp_path, seed=27)
    loaded = CityRunStore(tmp_path).load("response-study")

    assert isinstance(stored.manifest, CityRunManifestV6)
    assert stored.manifest.opportunity_count == stored.manifest.impression_count == 4
    assert stored.manifest.noticed_count == 0
    assert stored.manifest.response_count == 0
    assert stored.manifest.state_update_count == 0
    assert stored.manifest.final_state_count == 2
    assert (stored.directory / "outputs" / "spatial-responses.jsonl").read_bytes() == b""
    assert loaded.response_input is not None
    assert loaded.response_evaluation is not None
    assert len(loaded.response_evaluation.final_states) == 2
    for initial, final in zip(
        loaded.response_input.initial_states,
        loaded.response_evaluation.final_states,
        strict=True,
    ):
        assert (final.agent_id, final.campaign_id) == (
            initial.agent_id,
            initial.campaign_id,
        )
        assert final.brand_sentiment == initial.brand_sentiment
        assert final.recall_strength == initial.recall_strength
        assert final.purchase_intention == initial.purchase_intention
        assert final.response_count == 0
        assert final.last_response_minute is None


def test_response_input_without_spatial_scenario_is_refused_before_reservation(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_phone()])
    response_input = _response_input(scenario)

    with pytest.raises(ValueError, match=r"response|scenario"):
        create_city_run(
            pack,
            root=tmp_path,
            run_id="invalid-response-study",
            seed=42,
            agent_count=1,
            days=1,
            spatial_response=response_input,
        )

    assert not (tmp_path / "city-runs" / "invalid-response-study").exists()


@pytest.mark.parametrize(
    "relative_path",
    [
        "inputs/spatial-response.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ],
)
def test_changed_response_artifact_is_refused(
    tmp_path: Path,
    relative_path: str,
) -> None:
    stored = _create_response_run(tmp_path)
    path = stored.directory / relative_path
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


@pytest.mark.parametrize(
    "relative_path",
    [
        "inputs/spatial-response.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ],
)
def test_missing_response_artifact_is_refused(
    tmp_path: Path,
    relative_path: str,
) -> None:
    stored = _create_response_run(tmp_path)
    (stored.directory / relative_path).unlink()

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


@pytest.mark.parametrize(
    "relative_path",
    [
        "inputs/spatial-response.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ],
)
def test_truncated_response_artifact_is_refused(
    tmp_path: Path,
    relative_path: str,
) -> None:
    stored = _create_response_run(tmp_path)
    path = stored.directory / relative_path
    contents = path.read_bytes()
    assert contents
    path.write_bytes(contents[:-1])

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


@pytest.mark.parametrize(
    ("relative_path", "size"),
    [
        ("inputs/spatial-response.json", 2_097_153),
        ("outputs/spatial-responses.jsonl", 2_147_483_649),
        ("outputs/response-state.json", 4_194_305),
        ("outputs/response-summary.json", 65_537),
    ],
)
def test_oversized_response_artifact_is_refused_before_unbounded_read(
    tmp_path: Path,
    relative_path: str,
    size: int,
) -> None:
    stored = _create_response_run(tmp_path)
    with (stored.directory / relative_path).open("wb") as target:
        target.truncate(size)

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


@pytest.mark.parametrize(
    "relative_path",
    [
        "inputs/spatial-response.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ],
)
def test_response_artifact_cannot_follow_a_symlink(
    tmp_path: Path,
    relative_path: str,
) -> None:
    stored = _create_response_run(tmp_path)
    target = stored.directory / relative_path
    outside = tmp_path / f"outside-{target.name}"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")

    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load("response-study")


def test_reordered_same_size_response_stream_is_refused(tmp_path: Path) -> None:
    stored = _create_response_run(tmp_path)
    stream = stored.directory / "outputs" / "spatial-responses.jsonl"
    lines = stream.read_bytes().splitlines(keepends=True)
    assert len(lines) >= 2
    lines[0], lines[1] = lines[1], lines[0]
    stream.write_bytes(b"".join(lines))

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_input_sha256", "a" * 64),
        ("response_stream_sha256", "b" * 64),
        ("response_state_sha256", "c" * 64),
        ("response_summary_sha256", "d" * 64),
        ("response_stream_bytes", 1),
    ],
)
def test_valid_looking_changed_manifest_response_receipt_is_refused(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    stored = _create_response_run(tmp_path)
    manifest_path = stored.directory / "run.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document[field] = value
    manifest_path.write_bytes((canonical_json(document) + "\n").encode("utf-8"))

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load("response-study")


def test_v6_store_requires_response_input_and_evaluation_before_reservation(
    tmp_path: Path,
) -> None:
    stored = _create_response_run(tmp_path / "source")
    assert stored.response_input is not None
    assert stored.response_evaluation is not None

    for suffix, response_input, response_evaluation in (
        ("missing-both", None, None),
        ("missing-input", None, stored.response_evaluation),
        ("missing-evaluation", stored.response_input, None),
    ):
        root = tmp_path / suffix
        with pytest.raises(CorruptRunArtifact):
            _save_copy(
                CityRunStore(root),
                stored,
                response_input=response_input,
                response_evaluation=response_evaluation,
            )
        assert not (root / "city-runs" / "response-study").exists()


def test_v6_store_recomputes_response_evaluation_before_reservation(tmp_path: Path) -> None:
    stored = _create_response_run(tmp_path / "source")
    assert stored.spatial_scenario is not None
    assert stored.response_input is not None
    assert stored.response_evaluation is not None
    changed_input = _response_input(
        stored.spatial_scenario,
        agent_ids=("person-001", "person-002"),
        sentiment=-0.5,
    )
    root = tmp_path / "changed"

    with pytest.raises(CorruptRunArtifact):
        _save_copy(
            CityRunStore(root),
            stored,
            response_input=changed_input,
            response_evaluation=stored.response_evaluation,
        )

    assert not (root / "city-runs" / "response-study").exists()


def test_create_v6_returns_the_same_canonical_response_input_that_it_persists(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_phone()])
    canonical = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    noncanonical = SpatialResponseInput.model_construct(
        schema_version=canonical.schema_version,
        city_sha256=canonical.city_sha256,
        scenario_sha256=canonical.scenario_sha256,
        profiles=tuple(reversed(canonical.profiles)),
        campaigns=canonical.campaigns,
        initial_states=tuple(reversed(canonical.initial_states)),
    )

    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="canonical-response",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=noncanonical,
    )
    loaded = CityRunStore(tmp_path).load("canonical-response")

    assert stored.response_input == canonical
    assert stored.response_input == loaded.response_input
    assert stored.response_input is not noncanonical
    assert stored.manifest.response_input_sha256 == canonical.fingerprint
    assert replay_city_run(stored).identical is True


def test_create_v6_refuses_agent_campaign_or_creative_mismatch_before_reservation(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_phone()])
    valid = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    creative_document = valid.model_dump(mode="python")
    creative_document["campaigns"][0]["creative_sha256"] = "0" * 64
    wrong_creative = SpatialResponseInput.model_validate(creative_document)
    campaign_document = valid.model_dump(mode="python")
    campaign_document["campaigns"][0]["campaign_id"] = "other-campaign"
    for state in campaign_document["initial_states"]:
        state["campaign_id"] = "other-campaign"
    wrong_campaign = SpatialResponseInput.model_validate(campaign_document)
    wrong_agent = _response_input(
        scenario,
        agent_ids=("person-001", "person-003"),
    )

    for suffix, response_input in (
        ("agent", wrong_agent),
        ("campaign", wrong_campaign),
        ("creative", wrong_creative),
    ):
        run_id = f"wrong-{suffix}"
        with pytest.raises(ValueError):
            create_city_run(
                pack,
                root=tmp_path,
                run_id=run_id,
                seed=42,
                agent_count=2,
                days=1,
                spatial_scenario=scenario,
                spatial_response=response_input,
            )
        assert not (tmp_path / "city-runs" / run_id).exists()


def test_v5_run_remains_response_free_and_rejects_response_evidence(
    tmp_path: Path,
) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(pack, [_phone()])
    stored = create_city_run(
        pack,
        root=tmp_path / "source",
        run_id="attention-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )
    assert isinstance(stored.manifest, CityRunManifestV5)
    assert stored.response_input is None
    assert stored.response_evaluation is None
    assert not (stored.directory / "inputs" / "spatial-response.json").exists()
    assert not (stored.directory / "outputs" / "spatial-responses.jsonl").exists()
    response_input = _response_input(
        scenario,
        agent_ids=("person-001", "person-002"),
    )
    root = tmp_path / "rejected"

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(root).save(
            stored.manifest,
            stored.pack,
            stored.mobility.agents,
            spatial_scenario=stored.spatial_scenario,
            opportunity_evaluation=stored.opportunity_evaluation,
            attention_evaluation=stored.attention_evaluation,
            response_input=response_input,
        )

    assert not (root / "city-runs" / "attention-study").exists()


def test_duplicate_v6_save_preserves_every_original_artifact_byte(tmp_path: Path) -> None:
    stored = _create_response_run(tmp_path)
    assert stored.response_input is not None
    assert stored.response_evaluation is not None
    before = {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }

    with pytest.raises(DuplicateRun):
        _save_copy(
            CityRunStore(tmp_path),
            stored,
            response_input=stored.response_input,
            response_evaluation=stored.response_evaluation,
        )

    assert before == {
        path.relative_to(stored.directory): path.read_bytes()
        for path in stored.directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "failed_name",
    [
        "spatial-response.json",
        "response-summary.json",
        "response-state.json",
        "spatial-responses.jsonl",
    ],
)
def test_response_artifact_write_failure_cannot_publish_manifest_or_reuse_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    stored = _create_response_run(tmp_path / "source")
    assert stored.response_input is not None
    assert stored.response_evaluation is not None
    root = tmp_path / "failed"
    store = CityRunStore(root)
    original_write_new = city_store_module._write_new
    original_write_stream = city_store_module._write_response_stream

    def fail_document(path: Path, contents: bytes) -> None:
        if path.name == failed_name:
            raise OSError("injected response document failure")
        original_write_new(path, contents)

    def fail_stream(path: Path, evaluation: SpatialResponseEvaluation) -> None:
        if path.name == failed_name:
            raise OSError("injected response stream failure")
        original_write_stream(path, evaluation)

    monkeypatch.setattr(city_store_module, "_write_new", fail_document)
    monkeypatch.setattr(city_store_module, "_write_response_stream", fail_stream)

    with pytest.raises(StorageError):
        _save_copy(
            store,
            stored,
            response_input=stored.response_input,
            response_evaluation=stored.response_evaluation,
        )

    directory = root / "city-runs" / "response-study"
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    with pytest.raises(CorruptRunArtifact):
        store.load("response-study")
    with pytest.raises(DuplicateRun):
        _save_copy(
            store,
            stored,
            response_input=stored.response_input,
            response_evaluation=stored.response_evaluation,
        )


@pytest.mark.parametrize(
    "short_name",
    [
        "spatial-response.json",
        "response-summary.json",
        "response-state.json",
        "spatial-responses.jsonl",
    ],
)
def test_short_response_artifact_write_is_detected_before_manifest_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    short_name: str,
) -> None:
    stored = _create_response_run(tmp_path / "source")
    assert stored.response_input is not None
    assert stored.response_evaluation is not None
    root = tmp_path / "short"
    store = CityRunStore(root)
    original_write_new = city_store_module._write_new
    original_write_stream = city_store_module._write_response_stream

    def short_document(path: Path, contents: bytes) -> None:
        if path.name != short_name:
            original_write_new(path, contents)
            return
        with path.open("xb") as target:
            target.write(contents[:-1])

    def short_stream(path: Path, evaluation: SpatialResponseEvaluation) -> None:
        if path.name != short_name:
            original_write_stream(path, evaluation)
            return
        contents = b"".join(spatial_response_lines(evaluation))
        assert contents
        with path.open("xb") as target:
            target.write(contents[:-1])

    monkeypatch.setattr(city_store_module, "_write_new", short_document)
    monkeypatch.setattr(city_store_module, "_write_response_stream", short_stream)

    with pytest.raises(StorageError):
        _save_copy(
            store,
            stored,
            response_input=stored.response_input,
            response_evaluation=stored.response_evaluation,
        )

    directory = root / "city-runs" / "response-study"
    assert directory.is_dir()
    assert not (directory / "run.json").exists()
    with pytest.raises(CorruptRunArtifact):
        store.load("response-study")
    with pytest.raises(DuplicateRun):
        _save_copy(
            store,
            stored,
            response_input=stored.response_input,
            response_evaluation=stored.response_evaluation,
        )


def test_v6_replay_is_exact_and_does_not_mutate_source_artifacts(tmp_path: Path) -> None:
    first = _create_response_run(tmp_path / "first")
    second = _create_response_run(tmp_path / "second")
    assert first.response_evaluation is not None
    before = _file_hashes(first.directory)

    result = replay_city_run(CityRunStore(tmp_path / "first").load("response-study"))

    assert result.identical is True
    assert first.manifest == second.manifest
    assert result.response_model_id == first.manifest.spatial_response_model_id
    assert result.response_input_sha256 == first.manifest.response_input_sha256
    assert result.response_stream_sha256 == first.manifest.response_stream_sha256
    assert result.response_state_sha256 == first.manifest.response_state_sha256
    assert result.response_summary_sha256 == first.manifest.response_summary_sha256
    assert result.response_claim_scope == "synthetic-response-not-observed-behavior"
    assert result.response_stream_bytes == first.manifest.response_stream_bytes
    assert result.response_count == first.manifest.response_count
    assert result.state_update_count == first.manifest.state_update_count
    assert result.response_campaign_count == first.manifest.response_campaign_count
    assert result.final_state_count == first.manifest.final_state_count
    assert result.response_counts == first.response_evaluation.counts
    assert _file_hashes(first.directory) == before == _file_hashes(second.directory)
