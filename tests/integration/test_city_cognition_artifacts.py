"""Schema-v9 city runs freeze and independently verify cognition evidence."""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from adlife import __version__
from adlife.city import run_store as store_module
from adlife.city.run_store import CityRunStore
from adlife.city.runs import prepare_city_scientific_run
from adlife.city.workbench_input import WorkbenchRunDraftV3
from adlife.city.workbench_validation import construct_workbench_run
from adlife.core.domain import city_run as city_run_domain
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_DISCLOSURE,
    CityCognitionEvaluationV1,
    CityCognitionResultV1,
    CityCognitionTerminalRecordV1,
)
from adlife.core.domain.city_run import CityRunManifestV9
from adlife.core.domain.serialization import canonical_json
from adlife.core.ports.run_store import CorruptRunArtifact, StorageError, UnsafeRunLocation
from adlife.core.simulation.city_cognition import (
    build_city_cognition_terminal_record,
    build_city_mock_result,
    build_city_rules_result,
    validate_city_cognition_evaluation,
)
from adlife.core.simulation.city_trace import summarize_city_trace
from adlife.core.simulation.spatial_attention import summarize_spatial_attention_artifact
from adlife.core.simulation.spatial_opportunity import summarize_spatial_opportunity_artifact
from adlife.core.simulation.spatial_response import summarize_spatial_response_artifact
from tests.unit.city.test_workbench_input import draft_v3_data


def _prepared(*, empty: bool = False, mode: str = "rules"):
    document = draft_v3_data(mode=mode)
    if empty:
        document["scenario"]["phone"]["opportunity_probability_per_minute"] = 0.0
    compiled = construct_workbench_run(
        WorkbenchRunDraftV3.model_validate_json(json.dumps(document))
    )
    settings = compiled.workbench_input.draft.settings
    return prepare_city_scientific_run(
        compiled.pack,
        run_id=settings.run_id,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        spatial_scenario=compiled.scenario,
        spatial_response=compiled.response_input,
        workbench_input=compiled.workbench_input,
    )


def _evaluation(prepared):
    result_builder = (
        build_city_rules_result
        if prepared.cognition_input.mode == "rules"
        else build_city_mock_result
    )
    records = tuple(
        build_city_cognition_terminal_record(
            request,
            selection_index=index,
            source=prepared.cognition_input.mode,
            result=result_builder(request),
        )
        for index, request in enumerate(prepared.cognition_requests)
    )
    return validate_city_cognition_evaluation(
        prepared.cognition_input,
        prepared.cognition_requests,
        records,
    )


def _manifest(prepared, evaluation) -> CityRunManifestV9:
    builder = getattr(city_run_domain, "build_city_cognition_manifest_receipt", None)
    assert builder is not None, "cognition receipt construction is not implemented"
    trace = summarize_city_trace(prepared.mobility)
    opportunity = summarize_spatial_opportunity_artifact(prepared.opportunity_evaluation)
    attention = summarize_spatial_attention_artifact(prepared.attention_evaluation)
    response = summarize_spatial_response_artifact(prepared.response_evaluation)
    opportunity_summary = (canonical_json(opportunity) + "\n").encode("utf-8")
    attention_summary = (canonical_json(attention) + "\n").encode("utf-8")
    response_summary = (canonical_json(response) + "\n").encode("utf-8")
    settings = prepared.workbench_input.draft.settings
    return CityRunManifestV9(
        run_id=settings.run_id,
        package_version=__version__,
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        city_sha256=prepared.pack.fingerprint,
        agents_sha256=trace.agents_sha256,
        trace_sha256=trace.trace_sha256,
        scenario_sha256=prepared.spatial_scenario.fingerprint,
        opportunity_stream_sha256=opportunity.stream_sha256,
        opportunity_summary_sha256=sha256(opportunity_summary).hexdigest(),
        opportunity_stream_bytes=opportunity.stream_bytes,
        opportunity_count=opportunity.counts.opportunity_count,
        attention_stream_sha256=attention.stream_sha256,
        attention_summary_sha256=sha256(attention_summary).hexdigest(),
        attention_stream_bytes=attention.stream_bytes,
        impression_count=attention.counts.impression_count,
        noticed_count=attention.counts.noticed_count,
        spatial_response_schema_version=prepared.response_evaluation.schema_version,
        spatial_response_model_id=prepared.response_evaluation.model_id,
        response_input_sha256=prepared.response_input.fingerprint,
        response_stream_sha256=response.stream_sha256,
        response_state_sha256=response.state_document_sha256,
        response_summary_sha256=sha256(response_summary).hexdigest(),
        response_stream_bytes=response.stream_bytes,
        response_count=response.counts.response_count,
        state_update_count=response.counts.state_update_count,
        response_campaign_count=response.counts.campaign_count,
        final_state_count=response.counts.final_state_count,
        seed=settings.seed,
        agent_count=settings.agent_count,
        days=settings.days,
        frame_count=trace.frame_count,
        position_count=trace.position_count,
        city_schema_version=prepared.pack.schema_version,
        spatial_scenario_schema_version=prepared.spatial_scenario.schema_version,
        spatial_opportunity_schema_version=prepared.opportunity_evaluation.schema_version,
        spatial_attention_schema_version=prepared.attention_evaluation.schema_version,
        spatial_attention_model_id=prepared.attention_evaluation.model_id,
        workbench_input_schema_version=prepared.workbench_input.schema_version,
        workbench_input_sha256=prepared.workbench_input.fingerprint,
        cognition=builder(
            prepared.cognition_input,
            evaluation,
        ),
    )


def _save(root: Path, *, empty: bool = False, mode: str = "rules"):
    prepared = _prepared(empty=empty, mode=mode)
    evaluation = _evaluation(prepared)
    manifest = _manifest(prepared, evaluation)
    directory = CityRunStore(root).save(
        manifest,
        prepared.pack,
        prepared.mobility.agents,
        spatial_scenario=prepared.spatial_scenario,
        opportunity_evaluation=prepared.opportunity_evaluation,
        attention_evaluation=prepared.attention_evaluation,
        response_input=prepared.response_input,
        response_evaluation=prepared.response_evaluation,
        workbench_input=prepared.workbench_input,
        cognition_input=prepared.cognition_input,
        cognition_evaluation=evaluation,
    )
    return prepared, evaluation, manifest, directory


def _replace_stream_evidence(
    directory: Path,
    manifest: CityRunManifestV9,
    evaluation: CityCognitionEvaluationV1,
    records: tuple[CityCognitionTerminalRecordV1, ...],
) -> None:
    stream = b"".join((canonical_json(record) + "\n").encode("utf-8") for record in records)
    summary = evaluation.summary.model_copy(
        update={
            "terminal_stream_sha256": sha256(stream).hexdigest(),
            "terminal_stream_bytes": len(stream),
        }
    )
    summary_bytes = (canonical_json(summary) + "\n").encode("utf-8")
    cognition = manifest.cognition.model_copy(
        update={
            "terminal_stream_sha256": sha256(stream).hexdigest(),
            "terminal_stream_bytes": len(stream),
            "summary_sha256": sha256(summary_bytes).hexdigest(),
        }
    )
    changed_manifest = manifest.model_copy(update={"cognition": cognition})
    (directory / "outputs" / "cognition.jsonl").write_bytes(stream)
    (directory / "outputs" / "cognition-summary.json").write_bytes(summary_bytes)
    (directory / "run.json").write_bytes((canonical_json(changed_manifest) + "\n").encode("utf-8"))


def test_v9_round_trip_freezes_all_three_canonical_cognition_artifacts(
    tmp_path: Path,
) -> None:
    prepared, evaluation, manifest, directory = _save(tmp_path)

    loaded = CityRunStore(tmp_path).load(manifest.run_id)

    assert loaded.manifest == manifest
    assert loaded.cognition_input == prepared.cognition_input
    assert loaded.cognition_evaluation == evaluation
    assert (directory / "inputs" / "cognition.json").read_bytes() == (
        canonical_json(prepared.cognition_input) + "\n"
    ).encode("utf-8")
    assert (directory / "outputs" / "cognition.jsonl").read_bytes() == b"".join(
        (canonical_json(record) + "\n").encode("utf-8") for record in evaluation.records
    )
    assert (directory / "outputs" / "cognition-summary.json").read_bytes() == (
        canonical_json(evaluation.summary) + "\n"
    ).encode("utf-8")


def test_v9_empty_selection_still_stores_input_empty_stream_and_summary(
    tmp_path: Path,
) -> None:
    prepared, evaluation, manifest, directory = _save(tmp_path, empty=True)

    loaded = CityRunStore(tmp_path).load(manifest.run_id)

    assert prepared.cognition_requests == ()
    assert evaluation.records == ()
    assert manifest.cognition.terminal_record_count == 0
    assert (directory / "inputs" / "cognition.json").is_file()
    assert (directory / "outputs" / "cognition.jsonl").read_bytes() == b""
    assert (directory / "outputs" / "cognition-summary.json").is_file()
    assert loaded.cognition_evaluation == evaluation


def test_v9_mock_evidence_round_trips_and_is_independently_regenerated(
    tmp_path: Path,
) -> None:
    prepared, evaluation, manifest, _directory = _save(tmp_path, mode="mock")

    loaded = CityRunStore(tmp_path).load(manifest.run_id)

    assert loaded.cognition_input == prepared.cognition_input
    assert loaded.cognition_evaluation == evaluation
    assert manifest.cognition.explainer_id == "city-mock-explainer-v1"
    assert manifest.cognition.outcome_counts.mock == len(evaluation.records)


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/cognition-summary.json",
    ],
)
def test_v9_missing_cognition_artifact_is_refused(tmp_path: Path, artifact: str) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    (directory / artifact).unlink()

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/cognition-summary.json",
    ],
)
def test_v8_and_earlier_refuse_undeclared_cognition_artifacts(
    tmp_path: Path,
    artifact: str,
) -> None:
    from tests.unit.city.test_city_analysis import _persona_run

    stored = _persona_run(tmp_path)
    path = stored.directory / artifact
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"\n" if path.suffix == ".jsonl" else b"{}\n")

    with pytest.raises(CorruptRunArtifact, match="undeclared cognition"):
        CityRunStore(tmp_path).load(stored.manifest.run_id)


def test_store_requires_cognition_arguments_exactly_for_v9_before_reserving_id(
    tmp_path: Path,
) -> None:
    prepared = _prepared()
    evaluation = _evaluation(prepared)
    manifest = _manifest(prepared, evaluation)
    store = CityRunStore(tmp_path)
    common = {
        "spatial_scenario": prepared.spatial_scenario,
        "opportunity_evaluation": prepared.opportunity_evaluation,
        "attention_evaluation": prepared.attention_evaluation,
        "response_input": prepared.response_input,
        "response_evaluation": prepared.response_evaluation,
        "workbench_input": prepared.workbench_input,
    }

    with pytest.raises(CorruptRunArtifact, match="cognition"):
        store.save(manifest, prepared.pack, prepared.mobility.agents, **common)

    assert not (tmp_path / "city-runs" / manifest.run_id).exists()


def test_legacy_store_refuses_cognition_arguments_before_reserving_id(tmp_path: Path) -> None:
    from tests.unit.city.test_city_analysis import _persona_run

    source = _persona_run(tmp_path / "source")
    prepared = _prepared()
    evaluation = _evaluation(prepared)
    destination = tmp_path / "destination"

    with pytest.raises(CorruptRunArtifact, match=r"legacy.*cognition"):
        CityRunStore(destination).save(
            source.manifest,
            source.pack,
            source.mobility.agents,
            spatial_scenario=source.spatial_scenario,
            opportunity_evaluation=source.opportunity_evaluation,
            attention_evaluation=source.attention_evaluation,
            response_input=source.response_input,
            response_evaluation=source.response_evaluation,
            workbench_input=source.workbench_input,
            cognition_input=prepared.cognition_input,
            cognition_evaluation=evaluation,
        )

    assert not (destination / "city-runs" / source.manifest.run_id).exists()


@pytest.mark.parametrize(
    ("artifact", "size"),
    [
        ("inputs/cognition.json", 65_537),
        ("outputs/cognition.jsonl", 1_048_577),
        ("outputs/cognition-summary.json", 16_385),
    ],
)
def test_v9_oversized_cognition_artifact_is_refused_before_unbounded_read(
    tmp_path: Path,
    artifact: str,
    size: int,
) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    with (directory / artifact).open("wb") as target:
        target.truncate(size)

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


@pytest.mark.parametrize(
    "artifact",
    [
        "inputs/cognition.json",
        "outputs/cognition.jsonl",
        "outputs/cognition-summary.json",
    ],
)
def test_v9_cognition_artifact_cannot_follow_a_symlink(
    tmp_path: Path,
    artifact: str,
) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    target = directory / artifact
    outside = tmp_path / f"outside-{target.name}"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create file symlinks")

    with pytest.raises(UnsafeRunLocation):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_reordered_v9_terminal_records_are_refused(tmp_path: Path) -> None:
    _prepared_run, evaluation, manifest, directory = _save(tmp_path)
    assert len(evaluation.records) >= 2
    path = directory / "outputs" / "cognition.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    lines[0], lines[1] = lines[1], lines[0]
    path.write_bytes(b"".join(lines))

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_duplicate_v9_terminal_record_is_refused(tmp_path: Path) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    path = directory / "outputs" / "cognition.jsonl"
    first = path.read_bytes().splitlines(keepends=True)[0]
    path.write_bytes(path.read_bytes() + first)

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


@pytest.mark.parametrize(
    "artifact",
    ["inputs/cognition.json", "outputs/cognition-summary.json"],
)
def test_noncanonical_v9_cognition_document_is_refused(
    tmp_path: Path,
    artifact: str,
) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    path = directory / artifact
    path.write_bytes(path.read_bytes().removesuffix(b"\n") + b" \n")

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_duplicate_json_member_in_v9_cognition_input_is_refused(tmp_path: Path) -> None:
    _prepared_run, _evaluation_value, manifest, directory = _save(tmp_path)
    path = directory / "inputs" / "cognition.json"
    document = path.read_bytes()
    path.write_bytes(b'{"schema_version":1,' + document[1:])

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_rehashed_plausible_rules_prose_is_refused_by_policy_regeneration(
    tmp_path: Path,
) -> None:
    _prepared_run, evaluation, manifest, directory = _save(tmp_path)
    original = evaluation.records[0]
    changed_result = CityCognitionResultV1(
        schema_version=1,
        request_id=original.request_id,
        interpretation="Plausible but substituted deterministic wording.",
        decision_factors=("Rule-owned response evidence",),
        safety_flags=(),
        disclosure=CITY_COGNITION_DISCLOSURE,
    )
    document = original.model_dump(mode="json")
    document["result"] = changed_result.model_dump(mode="json")
    document["record_hash"] = sha256(
        canonical_json(
            {key: value for key, value in document.items() if key != "record_hash"}
        ).encode("utf-8")
    ).hexdigest()
    changed = CityCognitionTerminalRecordV1.model_validate_json(json.dumps(document))
    _replace_stream_evidence(
        directory,
        manifest,
        evaluation,
        (changed, *evaluation.records[1:]),
    )

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_rehashed_cross_bound_terminal_record_is_refused(tmp_path: Path) -> None:
    _prepared_run, evaluation, manifest, directory = _save(tmp_path)
    original = evaluation.records[0]
    document = original.model_dump(mode="json")
    document["response_event_id"] = "0" * 64
    document["record_hash"] = sha256(
        canonical_json(
            {key: value for key, value in document.items() if key != "record_hash"}
        ).encode("utf-8")
    ).hexdigest()
    changed = CityCognitionTerminalRecordV1.model_validate_json(json.dumps(document))
    _replace_stream_evidence(
        directory,
        manifest,
        evaluation,
        (changed, *evaluation.records[1:]),
    )

    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)


def test_cognition_write_failure_cannot_publish_a_completed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared()
    evaluation = _evaluation(prepared)
    manifest = _manifest(prepared, evaluation)

    def fail_stream(path: Path, value: object) -> None:
        del path, value
        raise OSError("injected cognition stream failure")

    monkeypatch.setattr(store_module, "_write_cognition_stream", fail_stream)
    with pytest.raises(StorageError):
        CityRunStore(tmp_path).save(
            manifest,
            prepared.pack,
            prepared.mobility.agents,
            spatial_scenario=prepared.spatial_scenario,
            opportunity_evaluation=prepared.opportunity_evaluation,
            attention_evaluation=prepared.attention_evaluation,
            response_input=prepared.response_input,
            response_evaluation=prepared.response_evaluation,
            workbench_input=prepared.workbench_input,
            cognition_input=prepared.cognition_input,
            cognition_evaluation=evaluation,
        )

    directory = tmp_path / "city-runs" / manifest.run_id
    assert directory.exists()
    assert not (directory / "run.json").exists()
    with pytest.raises(CorruptRunArtifact):
        CityRunStore(tmp_path).load(manifest.run_id)
