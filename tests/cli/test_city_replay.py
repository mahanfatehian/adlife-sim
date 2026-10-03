import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.city.runs import create_city_run
from adlife.cli.app import app
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_opportunity import _phone, _scenario
from tests.unit.city.test_spatial_response import _response_input

_RESPONSE_RECEIPT_KEYS = {
    "response_model_id",
    "response_claim_scope",
    "response_input_sha256",
    "response_stream_sha256",
    "response_state_sha256",
    "response_summary_sha256",
    "response_stream_bytes",
    "response_count",
    "state_update_count",
    "response_campaign_count",
    "final_state_count",
    "response_counts",
}


def _artifact_bytes(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _create_response_run(root: Path, *, run_id: str = "response-study"):
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
    return create_city_run(
        pack,
        root=root,
        run_id=run_id,
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
        spatial_response=_response_input(
            scenario,
            agent_ids=("person-001", "person-002"),
        ),
    )


def test_city_replay_reports_verified_trace_in_json(tmp_path: Path) -> None:
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    result = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "study"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["identical"] is True
    assert document["trace_sha256"] == stored.manifest.trace_sha256
    assert "_lines" not in document


def test_city_replay_refuses_missing_or_corrupt_source_cleanly(tmp_path: Path) -> None:
    missing = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "missing"])
    assert missing.exit_code == 4
    assert json.loads(missing.stdout)["error"]["exit_code"] == 4
    stored = create_city_run(
        load_pack(pack_data()), root=tmp_path, run_id="study", seed=42, agent_count=2, days=1
    )
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    corrupt = CliRunner().invoke(app, ["--format", "json", "city-replay", str(tmp_path), "study"])
    assert corrupt.exit_code == 4
    assert json.loads(corrupt.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in corrupt.output


def test_city_replay_refuses_invalid_identifier_as_input_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--format", "json", "city-replay", str(tmp_path), "../escape"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["exit_code"] == 2


def test_city_replay_reports_verified_spatial_provenance(tmp_path: Path) -> None:
    pack = load_pack(pack_data())
    scenario = _scenario(
        pack,
        [
            _phone(
                windows=[{"start_minute": 0, "end_minute": 2}],
                probability=1.0,
                cap=1,
            )
        ],
    )
    stored = create_city_run(
        pack,
        root=tmp_path,
        run_id="spatial-study",
        seed=42,
        agent_count=2,
        days=1,
        spatial_scenario=scenario,
    )

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "spatial-study"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["scenario_sha256"] == scenario.fingerprint
    assert document["opportunity_stream_sha256"] == (stored.manifest.opportunity_stream_sha256)
    assert document["opportunity_summary_sha256"] == (stored.manifest.opportunity_summary_sha256)
    assert document["opportunity_stream_bytes"] == stored.manifest.opportunity_stream_bytes
    assert document["opportunity_count"] == stored.manifest.opportunity_count == 2
    assert document["opportunity_counts"]["phone_eligible_agent_minute_count"] == 4
    assert document["opportunity_claim_scope"] == "synthetic-opportunity-not-impression"
    assert document["attention_model_id"] == stored.manifest.spatial_attention_model_id
    assert document["attention_stream_sha256"] == stored.manifest.attention_stream_sha256
    assert document["attention_summary_sha256"] == stored.manifest.attention_summary_sha256
    assert document["attention_stream_bytes"] == stored.manifest.attention_stream_bytes
    assert document["impression_count"] == stored.manifest.impression_count == 2
    assert document["noticed_count"] == stored.manifest.noticed_count
    assert document["attention_counts"]["noticed_count"] == document["noticed_count"]
    assert document["attention_claim_scope"] == "synthetic-attention-not-observed-behavior"
    assert "claim_scope" not in document
    assert _RESPONSE_RECEIPT_KEYS.isdisjoint(document)
    assert result.stderr == ""

    human = CliRunner().invoke(
        app,
        ["city-replay", str(tmp_path), "spatial-study"],
    )
    assert human.exit_code == 0, human.output
    assert human.stdout.splitlines() == [
        "city replay identical: spatial-study "
        f"({stored.manifest.frame_count} minute frames, "
        f"{stored.manifest.opportunity_count} synthetic opportunities, "
        f"{stored.manifest.impression_count} impressions, "
        f"{stored.manifest.noticed_count} notices)"
    ]
    assert human.stderr == ""


def test_city_replay_v6_reports_exact_json_and_human_response_receipts_without_mutation(
    tmp_path: Path,
) -> None:
    stored = _create_response_run(tmp_path)
    before = _artifact_bytes(stored.directory)

    machine = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "response-study"],
    )

    assert machine.exit_code == 0, machine.output
    assert len(machine.stdout.splitlines()) == 1
    document = json.loads(machine.stdout)
    assert set(document) == {
        "run_id",
        "identical",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "frame_count",
        "position_count",
        "scenario_sha256",
        "opportunity_stream_sha256",
        "opportunity_summary_sha256",
        "opportunity_stream_bytes",
        "opportunity_count",
        "opportunity_counts",
        "opportunity_claim_scope",
        "attention_model_id",
        "attention_claim_scope",
        "attention_notice_probability",
        "attention_stream_sha256",
        "attention_summary_sha256",
        "attention_stream_bytes",
        "impression_count",
        "noticed_count",
        "attention_counts",
        *_RESPONSE_RECEIPT_KEYS,
    }
    assert document["response_model_id"] == "spatial-response-v1"
    assert document["response_claim_scope"] == "synthetic-response-not-observed-behavior"
    assert document["response_input_sha256"] == stored.manifest.response_input_sha256
    assert document["response_stream_sha256"] == stored.manifest.response_stream_sha256
    assert document["response_state_sha256"] == stored.manifest.response_state_sha256
    assert document["response_summary_sha256"] == stored.manifest.response_summary_sha256
    assert document["response_stream_bytes"] == stored.manifest.response_stream_bytes
    assert document["response_count"] == stored.manifest.response_count
    assert document["state_update_count"] == stored.manifest.state_update_count
    assert document["response_campaign_count"] == stored.manifest.response_campaign_count
    assert document["final_state_count"] == stored.manifest.final_state_count
    assert stored.response_evaluation is not None
    assert document["response_counts"] == stored.response_evaluation.counts.model_dump(mode="json")
    assert "claim_scope" not in document
    assert "_lines" not in document
    assert machine.stderr == ""

    human = CliRunner().invoke(
        app,
        ["city-replay", str(tmp_path), "response-study"],
    )

    assert human.exit_code == 0, human.output
    assert human.stdout.splitlines() == [
        "city replay identical: response-study "
        f"({stored.manifest.frame_count} minute frames, "
        f"{stored.manifest.opportunity_count} synthetic opportunities, "
        f"{stored.manifest.impression_count} impressions, "
        f"{stored.manifest.noticed_count} notices)",
        f"response evidence: {stored.manifest.response_count} rule responses, "
        f"{stored.manifest.state_update_count} state updates, "
        f"{stored.manifest.response_campaign_count} campaigns, "
        f"{stored.manifest.final_state_count} final campaign states",
        "response contract: spatial-response-v1; synthetic-response-not-observed-behavior",
        f"response input SHA-256: {stored.manifest.response_input_sha256}",
        f"response stream SHA-256: {stored.manifest.response_stream_sha256} "
        f"({stored.manifest.response_stream_bytes} bytes)",
        f"response state SHA-256: {stored.manifest.response_state_sha256}",
        f"response summary SHA-256: {stored.manifest.response_summary_sha256}",
    ]
    assert human.stderr == ""
    assert _artifact_bytes(stored.directory) == before


@pytest.mark.parametrize(
    "relative_path",
    [
        "inputs/spatial-response.json",
        "outputs/spatial-responses.jsonl",
        "outputs/response-state.json",
        "outputs/response-summary.json",
    ],
)
def test_city_replay_maps_corrupt_response_artifacts_to_clean_artifact_errors(
    tmp_path: Path,
    relative_path: str,
) -> None:
    stored = _create_response_run(tmp_path)
    target = stored.directory / relative_path
    target.write_bytes(target.read_bytes() + b" ")
    corrupted = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "response-study"],
    )

    assert result.exit_code == 4
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["error"]["exit_code"] == 4
    assert result.stderr.startswith("error: ")
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert _artifact_bytes(stored.directory) == corrupted
