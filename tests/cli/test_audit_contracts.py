"""Regressions for the public machine-output and safe-input contracts."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def study(tmp_path: Path) -> Path:
    result = runner.invoke(app, ["init", "study", "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / "study"


@pytest.mark.parametrize("name", ["con", "prn", "aux", "nul", "com1", "lpt9"])
def test_reserved_run_names_are_input_errors(tmp_path, name):
    root = study(tmp_path)
    result = runner.invoke(app, ["--format", "json", "run", str(root), "--run-id", name])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert not list((root / "runs").iterdir())


def test_live_json_is_refused_before_any_run_is_created(tmp_path):
    root = study(tmp_path)
    result = runner.invoke(app, ["--format", "json", "run", str(root), "--live"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert not list((root / "runs").iterdir())


def test_default_run_ids_distinguish_campaign_overrides(tmp_path):
    root = study(tmp_path)
    ids = []
    for campaign in ("demo-phone.yaml", "demo-billboard.yaml"):
        result = runner.invoke(
            app, ["--format", "json", "run", str(root), "--campaign", f"campaigns/{campaign}"]
        )
        assert result.exit_code == 0, result.output
        ids.append(json.loads(result.stdout)["run_id"])
    assert ids[0] != ids[1]


@pytest.mark.parametrize(
    "command", [["init", "new"], ["population", "generate", "--size", "2"], ["demo", "--headless"]]
)
@pytest.mark.parametrize("fmt", ["json", "jsonl"])
def test_machine_success_always_has_a_document(tmp_path, monkeypatch, command, fmt):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--format", fmt, *command])
    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.stdout), dict)


@pytest.mark.parametrize(
    "args", [["run", "--days", "8"], ["nonesuch"], ["population", "generate", "--size", "31"]]
)
def test_parser_errors_have_machine_documents(args):
    result = runner.invoke(app, ["--format", "json", *args])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["exit_code"] == 2


def test_campaign_import_invalid_yaml_has_machine_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("campaign: [", encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "campaign", "import", str(path)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["exit_code"] == 2


def test_campaign_override_cannot_read_outside_study(tmp_path):
    root = study(tmp_path)
    external = tmp_path / "external.yaml"
    external.write_bytes((root / "campaigns" / "demo-phone.yaml").read_bytes())
    result = runner.invoke(app, ["run", str(root), "--campaign", str(external)])
    assert result.exit_code == 2, result.output
    assert not list((root / "runs").iterdir())


def test_demo_machine_mode_does_not_open_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr("adlife.cli.commands.demo._terminal_available", lambda: True)

    async def unexpected(*args, **kwargs):
        raise AssertionError("dashboard corrupted machine stdout")

    monkeypatch.setattr("adlife.cli.commands.run._run_live", unexpected)
    result = runner.invoke(app, ["--format", "json", "demo", "--dir", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["offline"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("profiles", None),
        ("relationships", None),
        ("profiles", [{}]),
        ("profiles", [{"interests": [{}]}]),
    ],
)
def test_malformed_population_is_input_error(tmp_path, field, value):
    import yaml

    root = study(tmp_path)
    path = root / "population.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document[field] = value
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_population_header_requires_exact_supported_integer_version(tmp_path, version):
    import yaml

    root = study(tmp_path)
    path = root / "population.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["schema_version"] = version
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])

    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["valid"] is False
    assert "schema_version" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("filename", ["population.yaml", "campaigns/demo-phone.yaml"])
def test_untrusted_yaml_diagnostics_do_not_echo_credentials(tmp_path, filename):
    root = study(tmp_path)
    (root / filename).write_text("secret: [Bearer super-secret-credential", encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2, result.output
    assert "super-secret-credential" not in result.output


@pytest.mark.parametrize("filename", ["population.yaml", "campaigns/demo-phone.yaml"])
def test_yaml_nesting_bound_is_a_clean_input_error(tmp_path, filename):
    root = study(tmp_path)
    (root / filename).write_text("[" * 1500 + "]" * 1500, encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.stderr


def test_report_cannot_overwrite_source_artifact(tmp_path):
    root = study(tmp_path)
    run = runner.invoke(app, ["run", str(root), "--run-id", "source"])
    assert run.exit_code == 0, run.output
    artifact = root / "runs" / "source" / "run.json"
    before = artifact.read_bytes()
    result = runner.invoke(
        app, ["--format", "json", "report", str(root), "source", "--output", str(artifact)]
    )
    assert result.exit_code == 3, result.output
    assert artifact.read_bytes() == before


def test_population_output_refuses_existing_files(tmp_path):
    path = tmp_path / "existing.yaml"
    path.write_text("preserve", encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "population", "generate", "--out", str(path)])
    assert result.exit_code == 3, result.output
    assert path.read_text(encoding="utf-8") == "preserve"


def test_init_existing_file_is_input_error(tmp_path):
    path = tmp_path / "existing"
    path.write_text("preserve", encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "init", "existing", "--parent", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert path.read_text(encoding="utf-8") == "preserve"


def test_demo_accepts_largest_seed_with_valid_generated_id(tmp_path):
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "demo",
            "--headless",
            "--seed",
            str(2**63 - 1),
            "--dir",
            str(tmp_path / "demo"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["run_id"]) <= 40


@pytest.mark.parametrize(
    "filename", ["adlife.yaml", "population.yaml", "campaigns/demo-phone.yaml"]
)
def test_report_cannot_overwrite_study_input(tmp_path, filename):
    root = study(tmp_path)
    run = runner.invoke(app, ["run", str(root), "--run-id", "source"])
    assert run.exit_code == 0, run.output
    target = root / filename
    before = target.read_bytes()
    result = runner.invoke(
        app, ["--format", "json", "report", str(root), "source", "--output", str(target)]
    )
    assert result.exit_code == 3, result.output
    assert target.read_bytes() == before


def test_default_report_directory_cannot_escape_through_a_link(tmp_path):
    import os
    import subprocess

    root = study(tmp_path)
    run = runner.invoke(app, ["run", str(root), "--run-id", "source"])
    assert run.exit_code == 0, run.output
    reports = root / "reports"
    reports.rmdir()
    external = tmp_path / "external-reports"
    external.mkdir()
    if os.name == "nt":
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(reports), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
    else:
        reports.symlink_to(external, target_is_directory=True)
    result = runner.invoke(app, ["--format", "json", "report", str(root), "source"])
    assert result.exit_code == 2, result.output
    assert list(external.iterdir()) == []
