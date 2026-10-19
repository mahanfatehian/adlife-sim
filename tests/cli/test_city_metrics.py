from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from adlife.city import analysis as city_analysis
from adlife.city.analysis import metrics_for_stored_city_run
from adlife.city.runs import create_city_run
from adlife.cli.app import app
from adlife.core.domain.city_run import CityRunManifestV7
from tests.integration.test_city_run_replay import _create_v9_run
from tests.unit.city.test_city_analysis import _artifact_bytes, _persona_run, _spatial_run
from tests.unit.city.test_city_pack import load_pack, pack_data


def test_city_metrics_help_lists_v10_with_legacy_schemas():
    result = CliRunner().invoke(app, ["city-metrics", "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    help_text = " ".join(strip_ansi(result.output).replace("│", " ").split())
    assert "Saved schema-v5 through schema-v10 run ID." in help_text


@pytest.mark.parametrize("layer", ["attention", "response"])
def test_city_metrics_reads_persisted_v9_read_only(tmp_path: Path, layer: str) -> None:
    stored = _create_v9_run(tmp_path)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "v9-study",
            "--layer",
            layer,
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["source_run_schema_version"] == 9
    assert _artifact_bytes(stored.directory) == before


@pytest.mark.parametrize("layer", ["attention", "response"])
def test_city_metrics_reads_persisted_v8_read_only(tmp_path, layer):
    _persona_run(tmp_path)
    before = _artifact_bytes(tmp_path)
    result = CliRunner().invoke(
        app, ["--format", "json", "city-metrics", str(tmp_path), "persona-study", "--layer", layer]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["source_run_schema_version"] == 8
    assert _artifact_bytes(tmp_path) == before


def _v7(stored):
    manifest = CityRunManifestV7.model_validate(
        stored.manifest.model_dump(mode="python")
        | {
            "schema_version": 7,
            "workbench_input_schema_version": 1,
            "workbench_input_sha256": sha256(stored.manifest.run_id.encode()).hexdigest(),
        }
    )
    return replace(stored, manifest=manifest)


def test_city_metrics_emits_exact_machine_document_and_is_read_only(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "study")
    before = _artifact_bytes(stored.directory)
    expected = metrics_for_stored_city_run(stored).model_dump(mode="json")

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "study"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_v6_json_preserves_schema_and_attention_only_receipts(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "response-study"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert set(document) == {
        "schema_version",
        "model_id",
        "claim_scope",
        "source_run_schema_version",
        "opportunity_model_id",
        "attention_model_id",
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "seed",
        "population_size",
        "days",
        "overall",
        "channels",
    }
    assert document["source_run_schema_version"] == 6
    assert document["claim_scope"] == "synthetic-metrics-not-observed-outcomes"
    assert document["overall"]["opportunity_count"]["numerator"] == 4
    assert document["overall"]["impression_count"]["numerator"] == 4
    assert document["overall"]["noticed_count"]["numerator"] == 1

    metric_names = {
        "opportunity_count",
        "impression_count",
        "noticed_count",
        "opportunity_reach",
        "impression_reach",
        "noticed_reach",
        "impression_frequency",
        "notice_rate",
    }
    artifacts = {
        artifact
        for series in (document["overall"], *document["channels"])
        for name in metric_names
        for artifact in series[name]["source_artifacts"]
    }
    assert artifacts == {
        "outputs/spatial-opportunities.jsonl",
        "outputs/spatial-attention.jsonl",
    }
    assert all(
        set(series) == {"schema_version", "channel"} | metric_names
        for series in (document["overall"], *document["channels"])
    )
    assert stored.response_evaluation is not None
    assert "_lines" not in document
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_default_and_explicit_attention_layers_have_identical_output(
    tmp_path: Path,
) -> None:
    _spatial_run(tmp_path, "response-study", response=True)
    runner = CliRunner()

    default = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "response-study"],
    )
    explicit = runner.invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "response-study",
            "--layer",
            "attention",
        ],
    )

    assert default.exit_code == explicit.exit_code == 0
    assert (explicit.stdout, explicit.stderr) == (default.stdout, default.stderr)


def test_city_metrics_response_layer_emits_exact_machine_document_and_is_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)
    helper = getattr(city_analysis, "response_metrics_for_stored_city_run", None)
    assert callable(helper), "stored schema-v6 response metric projection is not implemented"
    expected = helper(stored).model_dump(mode="json")

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "response-study",
            "--layer",
            "response",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document == expected
    assert document["model_id"] == "spatial-response-metrics-v1"
    assert document["claim_scope"] == "synthetic-response-metrics-not-observed-outcomes"
    assert document["source_run_schema_version"] == 6
    assert document["overall"]["response_count"]["numerator"] == 1
    assert document["overall"]["purchase_intention_proxy"]["denominator"] == 2
    assert document["channels"][0]["channel"] == "roadside"
    assert document["channels"][1]["channel"] == "mobile"
    assert document["campaigns"][0]["campaign_id"] == "fictional-launch"
    assert "_lines" not in document
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_layers_preserve_v7_source_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = _v7(_spatial_run(tmp_path, "v7-response", response=True))
    monkeypatch.setattr(
        "adlife.cli.commands.city_metrics.CityRunStore.load",
        lambda _store, _run_id: stored,
    )

    for layer in ("attention", "response"):
        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "city-metrics",
                str(tmp_path),
                "v7-response",
                "--layer",
                layer,
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["source_run_schema_version"] == 7


def test_city_metrics_response_layer_human_output_exposes_event_and_state_metrics(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        ["city-metrics", str(tmp_path), "response-study", "--layer", "response"],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "synthetic response metrics, not observed outcomes: response-study"
    assert any(line.startswith("overall:") for line in lines)
    assert any(line.startswith("roadside:") for line in lines)
    assert any(line.startswith("mobile:") for line in lines)
    assert any(line.startswith("fictional-launch:") for line in lines)
    normalized = result.stdout.lower()
    for label in (
        "responses",
        "response reach",
        "response frequency",
        "rule sentiment delta",
        "rule recall delta",
        "brand sentiment",
        "recall strength",
        "purchase intention proxy",
    ):
        assert label in normalized
    assert "purchase probability" not in normalized
    assert "sales" not in normalized
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_v10_json_separates_rule_and_exploratory_cognition(
    tmp_path: Path,
) -> None:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, _, directory = _saved_v10(tmp_path, mode="mock")
    before = _artifact_bytes(directory)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "artifact-v10",
            "--layer",
            "response",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["schema_version"] == 2
    assert document["source_run_schema_version"] == 10
    assert document["rule_baseline"]["purchase_intention_proxy"]["name"] == (
        "purchase_intention_proxy"
    )
    assert "purchase" not in json.dumps(document["provider_dependent_cognition"])
    assert document["provider_dependent_cognition"]["claim_scope"] == (
        "provider-dependent-exploratory-not-campaign-effect"
    )
    assert result.stderr == ""
    assert _artifact_bytes(directory) == before


def test_city_metrics_v10_human_output_labels_authority_and_limitations(tmp_path: Path) -> None:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _saved_v10(tmp_path, mode="mock")

    result = CliRunner().invoke(
        app,
        ["city-metrics", str(tmp_path), "artifact-v10", "--layer", "response"],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "synthetic bounded-cognition metrics, not observed outcomes: artifact-v10"
    assert "Rule baseline (rule-owned)" in lines
    assert "Provider-dependent cognition (exploratory, not a campaign effect)" in lines
    assert "purchase intention proxy" in result.stdout.lower()
    assert "purchase probability" not in result.stdout.lower()
    assert "sales" not in result.stdout.lower()
    assert result.stderr == ""


def _v10_cli(root: Path, *arguments: str):
    return CliRunner().invoke(
        app, [*arguments, "city-metrics", str(root), "artifact-v10", "--layer", "response"]
    )


def test_city_metrics_v10_json_is_the_exact_v2_document_on_one_line(tmp_path: Path) -> None:
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _saved_v10(tmp_path, mode="mock")
    expected = city_analysis.response_metrics_for_stored_city_run(
        CityRunStore(tmp_path).load("artifact-v10")
    ).model_dump(mode="json")

    result = _v10_cli(tmp_path, "--format", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert expected["model_id"] == "spatial-response-metrics-v2"
    assert "_lines" not in expected
    assert result.stdout.count("\n") == 1
    assert result.stderr == ""


def test_city_metrics_v10_human_sections_keep_rule_and_cognition_authority_apart(
    tmp_path: Path,
) -> None:
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _saved_v10(tmp_path, mode="mock")
    metrics = city_analysis.response_metrics_for_stored_city_run(
        CityRunStore(tmp_path).load("artifact-v10")
    )

    result = _v10_cli(tmp_path)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    rule_start = lines.index("Rule baseline (rule-owned)")
    cognition_start = lines.index(
        "Provider-dependent cognition (exploratory, not a campaign effect)"
    )
    assert 0 < rule_start < cognition_start
    rule_lines = lines[rule_start + 1 : cognition_start]
    cognition_lines = lines[cognition_start + 1 :]
    assert [line.split(":", 1)[0] for line in rule_lines] == [
        "overall",
        "roadside",
        "mobile",
        *(series.campaign_id for series in metrics.rule_baseline.campaigns),
        "purchase intention proxy",
    ]
    assert rule_lines[-1] == (
        "purchase intention proxy: uncalibrated rule-owned proxy, not a probability or a "
        "forecast; purchases are not modeled"
    )
    assert [line.split(":", 1)[0] for line in cognition_lines] == [
        "signal source",
        "provider",
        "coverage",
        "terminal sources",
        "fallback reasons",
        "neutralization",
        "terminal result categories",
        "affect categories",
        "relevance categories",
        "credibility categories",
        "salience categories",
        "applied cognition per response",
        "synthetic cognition state",
        "provenance",
        "disclosure",
    ]
    assert all("purchase" not in line.lower() for line in cognition_lines)
    cognition = metrics.provider_dependent_cognition
    coverage = cognition.coverage
    assert cognition_lines[0] == (
        "signal source: mock; effect scope bounded-signals-v1; funded response limit 60"
    )
    assert cognition_lines[1] == "provider: none (signals resolved offline)"
    assert cognition_lines[2] == (
        f"coverage: eligible responses {coverage.eligible_response_count}; "
        f"selected {coverage.selected_request_count}; "
        f"funded {coverage.funded_request_count}; budget-skipped 0; unselected "
        f"{coverage.unselected_response_count}; funded coverage "
        f"{coverage.funded_request_count}/{coverage.eligible_response_count}="
        f"{format(cognition.funded_coverage.value, 'g')}"
    )
    assert cognition_lines[4] == "fallback reasons: none"
    assert cognition_lines[6] == (
        f"terminal result categories: {coverage.selected_request_count} selected requests, "
        "of which 0 are fixed neutral vectors from rules, fallback or budget-skipped "
        "terminals (not provider choices)"
    )
    provenance = cognition_lines[13]
    for digest in (
        cognition.cognition_input_sha256,
        cognition.cognition_terminal_stream_sha256,
        cognition.cognition_summary_sha256,
        cognition.causal_contract_sha256,
        cognition.influence_policy_sha256,
    ):
        assert digest in provenance
    assert "city-cognition-influence-v1" in provenance
    assert cognition_lines[14] == (
        "disclosure: bounded AI cognition signals are synthetic, provider-dependent "
        "exploratory evidence; they are not observed behavior, validated psychology, a "
        "prediction, or a campaign effect, and they change only the separate cognition "
        "track"
    )
    assert result.stderr == ""


def test_city_metrics_v10_provider_output_names_profile_without_transport(tmp_path: Path) -> None:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, prepared, _ = _saved_v10(tmp_path, mode="local", source="fallback", agents=8)
    profile = prepared.cognition_input.provider_profile
    assert profile is not None

    human = _v10_cli(tmp_path)
    machine = _v10_cli(tmp_path, "--format", "json")

    assert human.exit_code == machine.exit_code == 0, human.output + machine.output
    provider_line = next(line for line in human.stdout.splitlines() if line.startswith("provider:"))
    assert provider_line == (
        f"provider: profile {profile.profile_id}; model {profile.model_id}; revision "
        f"{profile.revision_sha256}; effective settings "
        f"{prepared.cognition_input.effective_settings.fingerprint}"
    )
    assert "fallback reasons: timeout-exhausted 10; budget-skipped 2" in human.stdout
    for output in (human.stdout, machine.stdout):
        assert profile.base_url not in output
        assert profile.credential_slot_id not in output
        assert profile.display_label not in output
        assert "authorization" not in output.lower()


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"mode": "rules"}, id="rules"),
        pytest.param({"mode": "mock"}, id="mock"),
        pytest.param({"mode": "local", "source": "provider", "agents": 8}, id="local-provider"),
        pytest.param({"mode": "local", "source": "fallback", "agents": 8}, id="local-fallback"),
    ],
)
def test_city_metrics_v10_category_lines_disclose_fixed_neutral_terminals(
    tmp_path: Path, options: dict[str, object]
) -> None:
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _saved_v10(tmp_path, **options)
    cognition = city_analysis.response_metrics_for_stored_city_run(
        CityRunStore(tmp_path).load("artifact-v10")
    ).provider_dependent_cognition
    sources = cognition.source_counts
    # Rules, fallback, and budget-skipped terminals carry AdLife's fixed neutral vector.
    identity = sources.rules + sources.fallback + sources.budget_skipped
    selected = cognition.coverage.selected_request_count

    result = _v10_cli(tmp_path)

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    header = lines.index(
        f"terminal result categories: {selected} selected requests, of which {identity} "
        "are fixed neutral vectors from rules, fallback or budget-skipped terminals "
        "(not provider choices)"
    )
    assert lines[header - 1].startswith("neutralization: ")
    assert [line.split(":", 1)[0] for line in lines[header + 1 : header + 5]] == [
        "affect categories",
        "relevance categories",
        "credibility categories",
        "salience categories",
    ]
    if options.get("source") == "fallback":
        # No provider category reached this run: every selected terminal is the fixed
        # neutral vector of a fallback or budget-skipped record.
        assert (selected, identity) == (12, 12)
        assert lines[header + 1 : header + 5] == [
            "affect categories: annoyed 0; skeptical 0; neutral 12; curious 0; positive 0; "
            "uncertain 0",
            "relevance categories: none 0; low 0; medium 12; high 0; uncertain 0",
            "credibility categories: low 0; medium 12; high 0; uncertain 0",
            "salience categories: none 0; low 0; medium 12; high 0; uncertain 0",
        ]
    elif options["mode"] == "rules":
        assert identity == selected == sources.rules
    elif options["mode"] == "mock":
        assert identity == 0
    else:
        assert 0 < identity == sources.budget_skipped < selected
    assert result.stderr == ""


def test_city_metrics_v10_attention_layer_keeps_the_v1_attention_shape(tmp_path: Path) -> None:
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, _, directory = _saved_v10(tmp_path, mode="mock")
    before = _artifact_bytes(directory)
    runner = CliRunner()

    machine = runner.invoke(
        app, ["--format", "json", "city-metrics", str(tmp_path), "artifact-v10"]
    )
    human = runner.invoke(app, ["city-metrics", str(tmp_path), "artifact-v10"])

    assert machine.exit_code == human.exit_code == 0, machine.output + human.output
    document = json.loads(machine.stdout)
    assert document["schema_version"] == 1
    assert document["model_id"] == "spatial-metrics-v1"
    assert document["source_run_schema_version"] == 10
    assert set(document) == {
        "schema_version",
        "model_id",
        "claim_scope",
        "source_run_schema_version",
        "opportunity_model_id",
        "attention_model_id",
        "scenario_sha256",
        "city_sha256",
        "agents_sha256",
        "trace_sha256",
        "opportunity_structure_sha256",
        "seed",
        "population_size",
        "days",
        "overall",
        "channels",
    }
    lines = human.stdout.splitlines()
    assert lines[0] == "synthetic metrics, not observed outcomes: artifact-v10"
    assert [line.split(":", 1)[0] for line in lines[1:]] == ["overall", "roadside", "mobile"]
    assert "cognition" not in human.stdout.lower()
    assert _artifact_bytes(directory) == before


def test_city_metrics_v10_corrupt_cognition_evidence_uses_artifact_exit(tmp_path: Path) -> None:
    """Characterization-only: the store's load-time verification already refused this.

    A reordered terminal stream never reaches metric derivation, so this node is green
    before V10 metrics exist; the forged-evidence test below covers the derivation path.
    """
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _, _, directory = _saved_v10(tmp_path, mode="mock")
    stream = directory / "outputs/cognition.jsonl"
    stream.write_bytes(b"".join(reversed(stream.read_bytes().splitlines(keepends=True))))

    result = _v10_cli(tmp_path, "--format", "json")

    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in result.output


def test_city_metrics_v10_inconsistent_verified_evidence_maps_to_artifact_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adlife.city.run_store import CityRunStore
    from tests.integration.test_city_cognition_v2_artifacts import _saved_v10

    _saved_v10(tmp_path / "rules", mode="rules")
    _saved_v10(tmp_path / "mock", mode="mock")
    rules = CityRunStore(tmp_path / "rules").load("artifact-v10")
    mock = CityRunStore(tmp_path / "mock").load("artifact-v10")
    # Both runs verified on load; crossing their terminal evidence is corruption only the
    # metric derivation can see, and it must not surface as an input error (exit 2).
    forged = replace(rules, cognition_evaluation=mock.cognition_evaluation)
    monkeypatch.setattr(
        "adlife.cli.commands.city_metrics.CityRunStore.load",
        lambda _store, _run_id: forged,
    )

    machine = _v10_cli(tmp_path / "rules", "--format", "json")
    human = _v10_cli(tmp_path / "rules")

    message = "verified V10 city run could not be projected to response metrics"
    for result in (machine, human):
        assert result.exit_code == 4, result.output
        assert message in result.output
        assert "Traceback" not in result.output
    error = json.loads(machine.stdout)["error"]
    assert (error["exit_code"], error["type"], error["message"]) == (
        4,
        "CorruptRunArtifact",
        message,
    )


def test_city_metrics_response_layer_requires_schema_v6(tmp_path: Path) -> None:
    stored = _spatial_run(tmp_path, "attention-study")
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(
        app,
        [
            "--format",
            "json",
            "city-metrics",
            str(tmp_path),
            "attention-study",
            "--layer",
            "response",
        ],
    )

    assert result.exit_code == 4
    assert "schema-v6" in json.loads(result.stdout)["error"]["message"]
    assert "Traceback" not in result.output
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_human_output_leads_with_claim_and_receipts(tmp_path: Path) -> None:
    _spatial_run(tmp_path, "study")

    result = CliRunner().invoke(app, ["city-metrics", str(tmp_path), "study"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic metrics, not observed outcomes: study",
        "overall: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
        "roadside: opportunities 0/1=0; impressions 0/1=0; noticed 0/1=0; "
        "opportunity reach 0/2=0; impression frequency 0/0=0; notice rate 0/0=0",
        "mobile: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
    ]
    assert result.stderr == ""


def test_city_metrics_v6_human_output_stays_attention_only_and_read_only(
    tmp_path: Path,
) -> None:
    stored = _spatial_run(tmp_path, "response-study", response=True)
    before = _artifact_bytes(stored.directory)

    result = CliRunner().invoke(app, ["city-metrics", str(tmp_path), "response-study"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "synthetic metrics, not observed outcomes: response-study",
        "overall: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
        "roadside: opportunities 0/1=0; impressions 0/1=0; noticed 0/1=0; "
        "opportunity reach 0/2=0; impression frequency 0/0=0; notice rate 0/0=0",
        "mobile: opportunities 4/1=4; impressions 4/1=4; noticed 1/1=1; "
        "opportunity reach 2/2=1; impression frequency 4/2=2; notice rate 1/4=0.25",
    ]
    assert "response" not in result.stdout.lower().replace("response-study", "")
    assert "purchase" not in result.stdout.lower()
    assert result.stderr == ""
    assert _artifact_bytes(stored.directory) == before


def test_city_metrics_maps_invalid_missing_legacy_and_corrupt_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "../escape"],
    )
    assert invalid.exit_code == 2
    assert json.loads(invalid.stdout)["error"]["exit_code"] == 2
    assert "Traceback" not in invalid.output

    missing = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "missing"],
    )
    assert missing.exit_code == 4
    assert json.loads(missing.stdout)["error"]["exit_code"] == 4

    create_city_run(
        load_pack(pack_data()),
        root=tmp_path,
        run_id="legacy",
        seed=42,
        agent_count=2,
        days=1,
    )
    legacy = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "legacy"],
    )
    assert legacy.exit_code == 4
    assert "schema-v5" in json.loads(legacy.stdout)["error"]["message"]

    stored = _spatial_run(tmp_path, "corrupt")
    (stored.directory / "run.json").write_text("{}", encoding="utf-8")
    corrupt = runner.invoke(
        app,
        ["--format", "json", "city-metrics", str(tmp_path), "corrupt"],
    )
    assert corrupt.exit_code == 4
    assert json.loads(corrupt.stdout)["error"]["exit_code"] == 4
    assert "Traceback" not in corrupt.output
