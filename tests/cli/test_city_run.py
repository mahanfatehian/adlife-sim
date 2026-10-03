import json
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from adlife.city.catalog import select_catalog_city
from adlife.city.response_loader import MAX_SPATIAL_RESPONSE_INPUT_BYTES
from adlife.city.run_store import CityRunStore
from adlife.city.spatial_loader import MAX_SPATIAL_CAMPAIGN_BYTES
from adlife.cli.app import app
from adlife.core.domain.serialization import canonical_json
from adlife.core.domain.spatial_campaign import SpatialCampaignScenario
from tests.cli.test_city_campaign import _scenario_for, _write_local_inputs
from tests.cli.test_city_places_cli import write_city_place_inputs
from tests.unit.city.test_city_pack import load_pack, pack_data
from tests.unit.city.test_spatial_response import _response_input


def _pack(tmp_path: Path) -> Path:
    path = tmp_path / "pack.json"
    path.write_text(canonical_json(load_pack(pack_data())) + "\n", encoding="utf-8")
    return path


def _write_response_input(
    tmp_path: Path,
    scenario: SpatialCampaignScenario,
    *,
    agent_count: int = 2,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    response_input = _response_input(
        scenario,
        agent_ids=tuple(f"person-{index:03d}" for index in range(1, agent_count + 1)),
    )
    path = tmp_path / "spatial-response.json"
    path.write_text(canonical_json(response_input) + "\n", encoding="utf-8")
    return path


def test_city_run_creates_saved_artifact_with_clean_json(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "study",
            "--agents",
            "2",
            "--days",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["run_id"] == "study"
    assert document["frame_count"] == 1440
    assert document["position_count"] == 2880
    assert "_lines" not in document
    assert (tmp_path / "city-runs" / "study" / "run.json").is_file()


def test_city_run_refuses_duplicate_without_changing_manifest(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    args = [
        "city-run",
        str(pack),
        "--output-root",
        str(tmp_path),
        "--run-id",
        "study",
        "--agents",
        "2",
        "--days",
        "1",
    ]
    assert CliRunner().invoke(app, args).exit_code == 0
    manifest = tmp_path / "city-runs" / "study" / "run.json"
    before = manifest.read_bytes()
    duplicate = CliRunner().invoke(app, ["--format", "json", *args])
    assert duplicate.exit_code == 3
    assert json.loads(duplicate.stdout)["error"]["exit_code"] == 3
    assert manifest.read_bytes() == before
    assert "Traceback" not in duplicate.output


def test_city_run_refuses_invalid_pack_bounds_and_run_id(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    bad_pack = tmp_path / "bad.json"
    bad_pack.write_text('{"api_key":"topsecret123"', encoding="utf-8")
    for extra in (
        [str(bad_pack)],
        [str(pack), "--agents", "31"],
        [str(pack), "--days", "8"],
        [str(pack), "--run-id", "../escape"],
    ):
        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "city-run",
                "--output-root",
                str(tmp_path),
                "--run-id",
                "study",
                *extra,
            ],
        )
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "topsecret123" not in result.output
        assert "Traceback" not in result.output
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_refuses_a_credential_shaped_id_without_persisting_or_echoing_it(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    secret_run_id = "sk-live-abcdefghij"

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--output-root",
            str(tmp_path),
            "--run-id",
            secret_run_id,
            "--agents",
            "2",
            "--days",
            "1",
        ],
    )

    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert secret_run_id not in result.output
    assert not (tmp_path / "city-runs" / secret_run_id).exists()


def test_city_run_interrupt_exits_130_without_an_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _pack(tmp_path)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("adlife.cli.commands.city_run.create_city_run", interrupt)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "study",
        ],
    )
    assert result.exit_code == 130
    assert json.loads(result.stdout)["error"]["exit_code"] == 130
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_can_freeze_a_verified_catalog_pack(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            "--city-id",
            "fictional-grid-v2",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "catalog-study",
            "--agents",
            "2",
            "--days",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    stored = CityRunStore(tmp_path).load("catalog-study")
    assert stored.pack.city_id == "fictional-grid-v2"
    assert stored.manifest.schema_version == 2


def test_city_run_requires_exactly_one_pack_selector(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    common = [
        "city-run",
        "--output-root",
        str(tmp_path),
        "--run-id",
        "study",
        "--agents",
        "2",
        "--days",
        "1",
    ]
    neither = CliRunner().invoke(app, common)
    both = CliRunner().invoke(app, [*common, str(pack), "--city-id", "fictional-grid-v2"])
    unknown = CliRunner().invoke(app, [*common, "--city-id", "unknown-city"])
    assert neither.exit_code == both.exit_code == unknown.exit_code == 2
    assert "Traceback" not in neither.output + both.output + unknown.output
    assert not (tmp_path / "city-runs" / "study").exists()


def test_city_run_and_replay_expose_frozen_place_evidence(tmp_path: Path) -> None:
    pack, places, place_set = write_city_place_inputs(tmp_path)
    created = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--places",
            str(places),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "place-study",
            "--agents",
            "2",
            "--days",
            "7",
        ],
    )
    assert created.exit_code == 0, created.output
    document = json.loads(created.stdout)
    assert document["run_schema_version"] == 3
    assert document["place_set_sha256"] == place_set.fingerprint
    assert len(document["place_assignments_sha256"]) == 64

    stored = CityRunStore(tmp_path).load("place-study")
    assert stored.manifest.schema_version == 3
    assert (stored.directory / "inputs" / "places.json").is_file()
    replay = CliRunner().invoke(
        app,
        ["--format", "json", "city-replay", str(tmp_path), "place-study"],
    )
    assert replay.exit_code == 0, replay.output
    replayed = json.loads(replay.stdout)
    assert replayed["place_set_sha256"] == place_set.fingerprint
    assert replayed["place_assignments_sha256"] == document["place_assignments_sha256"]


def test_city_run_persists_spatial_study_with_clean_json(tmp_path: Path) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--spatial-campaign",
            str(scenario_path),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "spatial-study",
            "--agents",
            "2",
            "--days",
            "2",
            "--seed",
            "42",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["run_schema_version"] == 5
    assert document["scenario_sha256"] == scenario.fingerprint
    assert len(document["opportunity_stream_sha256"]) == 64
    assert len(document["opportunity_summary_sha256"]) == 64
    assert document["opportunity_stream_bytes"] > 0
    assert document["opportunity_count"] > 0
    assert document["opportunity_counts"]["opportunity_count"] == document["opportunity_count"]
    assert document["opportunity_claim_scope"] == "synthetic-opportunity-not-impression"
    assert document["attention_model_id"] == "spatial-attention-v1"
    assert len(document["attention_stream_sha256"]) == 64
    assert len(document["attention_summary_sha256"]) == 64
    assert document["attention_stream_bytes"] > 0
    assert document["impression_count"] == document["opportunity_count"]
    assert 0 <= document["noticed_count"] <= document["impression_count"]
    assert document["attention_counts"]["noticed_count"] == document["noticed_count"]
    assert document["attention_claim_scope"] == "synthetic-attention-not-observed-behavior"
    assert "claim_scope" not in document
    assert "_lines" not in document
    assert result.stderr == ""
    stored = CityRunStore(tmp_path).load("spatial-study")
    assert stored.spatial_scenario == scenario
    assert stored.opportunity_evaluation is not None
    assert stored.attention_evaluation is not None
    assert {
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
    }.isdisjoint(document)


def test_city_run_persists_v6_response_study_with_exact_json_and_human_contract(
    tmp_path: Path,
) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    response_path = _write_response_input(tmp_path, scenario)
    common = [
        "city-run",
        str(pack),
        "--spatial-campaign",
        str(scenario_path),
        "--spatial-response",
        str(response_path),
        "--output-root",
        str(tmp_path),
        "--agents",
        "2",
        "--days",
        "2",
        "--seed",
        "42",
    ]

    machine = CliRunner().invoke(
        app,
        ["--format", "json", *common, "--run-id", "response-study"],
    )

    assert machine.exit_code == 0, machine.output
    assert len(machine.stdout.splitlines()) == 1
    document = json.loads(machine.stdout)
    stored = CityRunStore(tmp_path).load("response-study")
    assert document["run_schema_version"] == 6
    assert document["response_model_id"] == "spatial-response-v1"
    assert document["response_claim_scope"] == "synthetic-response-not-observed-behavior"
    assert document["response_input_sha256"] == stored.manifest.response_input_sha256
    assert document["response_stream_sha256"] == stored.manifest.response_stream_sha256
    assert document["response_state_sha256"] == stored.manifest.response_state_sha256
    assert document["response_summary_sha256"] == stored.manifest.response_summary_sha256
    assert document["response_stream_bytes"] == stored.manifest.response_stream_bytes
    assert document["response_count"] == document["noticed_count"]
    assert document["state_update_count"] == stored.manifest.state_update_count
    assert document["response_campaign_count"] == 2
    assert document["final_state_count"] == 4
    assert document["response_counts"]["response_count"] == document["response_count"]
    assert "claim_scope" not in document
    assert "_lines" not in document
    assert set(document) == {
        "run_id",
        "city_id",
        "city_sha256",
        "trace_sha256",
        "frame_count",
        "position_count",
        "run_schema_version",
        "directory",
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
    assert machine.stderr == ""

    jsonl = CliRunner().invoke(
        app,
        ["--format", "jsonl", *common, "--run-id", "response-jsonl"],
    )
    assert jsonl.exit_code == 0, jsonl.output
    assert len(jsonl.stdout.splitlines()) == 1
    assert json.loads(jsonl.stdout)["response_model_id"] == "spatial-response-v1"
    assert jsonl.stderr == ""

    human = CliRunner().invoke(
        app,
        [*common, "--run-id", "response-human"],
    )
    assert human.exit_code == 0, human.output
    assert human.stderr == ""
    human_manifest = CityRunStore(tmp_path).load("response-human").manifest
    assert human.stdout.splitlines() == [
        "saved city run: response-human "
        f"({human_manifest.frame_count} verified minute frames, "
        f"{human_manifest.opportunity_count} synthetic opportunities, "
        f"{human_manifest.impression_count} impressions, "
        f"{human_manifest.noticed_count} notices)",
        f"response evidence: {human_manifest.response_count} rule responses, "
        f"{human_manifest.state_update_count} state updates, "
        f"{human_manifest.response_campaign_count} campaigns, "
        f"{human_manifest.final_state_count} final campaign states",
        "response contract: spatial-response-v1; synthetic-response-not-observed-behavior",
        f"response input SHA-256: {human_manifest.response_input_sha256}",
        f"response stream SHA-256: {human_manifest.response_stream_sha256} "
        f"({human_manifest.response_stream_bytes} bytes)",
        f"response state SHA-256: {human_manifest.response_state_sha256}",
        f"response summary SHA-256: {human_manifest.response_summary_sha256}",
    ]


def test_city_run_help_exposes_response_dependency() -> None:
    result = CliRunner().invoke(app, ["city-run", "--help"])

    assert result.exit_code == 0, result.output
    normalized = " ".join(strip_ansi(result.stdout).split())
    assert "--spatial-response" in normalized
    assert "requires" in normalized
    assert normalized.count("--spatial-campaign") >= 2


def test_city_run_refuses_response_dependency_and_bad_inputs_before_reservation(
    tmp_path: Path,
) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    malformed = tmp_path / "malformed-response.json"
    malformed.write_text('{"api_key":"topsecret123"', encoding="utf-8")
    oversized = tmp_path / "oversized-response.json"
    with oversized.open("wb") as target:
        target.truncate(MAX_SPATIAL_RESPONSE_INPUT_BYTES + 1)
    wrong_population = _write_response_input(
        tmp_path / "wrong-population",
        scenario,
        agent_count=1,
    )
    dependency = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--spatial-response",
            str(tmp_path / "does-not-exist.json"),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "missing-campaign",
            "--agents",
            "2",
            "--days",
            "2",
        ],
    )
    assert dependency.exit_code == 2
    dependency_error = json.loads(dependency.stdout)["error"]
    assert dependency_error["message"] == "--spatial-response requires --spatial-campaign"
    assert "could not be read" not in dependency.output
    assert not (tmp_path / "city-runs" / "missing-campaign").exists()

    cases = (
        ("malformed-response", malformed, scenario_path),
        ("oversized-response", oversized, scenario_path),
        ("wrong-population", wrong_population, scenario_path),
    )
    for run_id, response_path, campaign_path in cases:
        arguments = [
            "--format",
            "json",
            "city-run",
            str(pack),
            "--spatial-response",
            str(response_path),
            "--output-root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--agents",
            "2",
            "--days",
            "2",
        ]
        arguments.extend(["--spatial-campaign", str(campaign_path)])
        result = CliRunner().invoke(app, arguments)
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "topsecret123" not in result.output
        assert "Traceback" not in result.output
        assert not (tmp_path / "city-runs" / run_id).exists()


def test_city_run_can_use_catalog_city_for_spatial_study(tmp_path: Path) -> None:
    pack = select_catalog_city("fictional-grid-v2")
    scenario = _scenario_for(pack, catalog=True)
    path = tmp_path / "catalog-spatial.json"
    path.write_text(canonical_json(scenario) + "\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-run",
            "--city-id",
            "fictional-grid-v2",
            "--spatial-campaign",
            str(path),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "catalog-spatial",
            "--agents",
            "1",
            "--days",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["run_schema_version"] == 5
    assert document["city_id"] == "fictional-grid-v2"
    assert document["scenario_sha256"] == scenario.fingerprint
    assert document["impression_count"] == document["opportunity_count"]


def test_city_run_refuses_bad_spatial_inputs_without_reserving_run(
    tmp_path: Path,
) -> None:
    pack, scenario_path, scenario = _write_local_inputs(tmp_path)
    malformed = tmp_path / "malformed-spatial.json"
    malformed.write_text('{"credential":"sk-live-ABCDEFGHIJKLMNOP"', encoding="utf-8")
    oversized = tmp_path / "oversized-spatial.json"
    with oversized.open("wb") as target:
        target.truncate(MAX_SPATIAL_CAMPAIGN_BYTES + 1)
    wrong = scenario.model_copy(update={"city_sha256": "f" * 64})
    wrong_path = tmp_path / "wrong-spatial.json"
    wrong_path.write_text(canonical_json(wrong) + "\n", encoding="utf-8")

    cases: tuple[tuple[str, Path, int], ...] = (
        ("malformed-study", malformed, 2),
        ("oversized-study", oversized, 2),
        ("wrong-city-study", wrong_path, 2),
        ("wrong-duration-study", scenario_path, 1),
    )
    for run_id, spatial_path, days in cases:
        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "city-run",
                str(pack),
                "--spatial-campaign",
                str(spatial_path),
                "--output-root",
                str(tmp_path),
                "--run-id",
                run_id,
                "--agents",
                "1",
                "--days",
                str(days),
            ],
        )
        assert result.exit_code == 2, result.output
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
        assert "sk-live-ABCDEFGHIJKLMNOP" not in result.output
        assert "No such option" not in result.output
        assert "Traceback" not in result.output
        assert not (tmp_path / "city-runs" / run_id).exists()
