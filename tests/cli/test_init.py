"""`adlife init`: a new study directory copied from packaged resources.

The project is the unit every other command consumes, so init is pinned literally: the
exact files, the exact directories, the refusal to touch a non-empty target, and the
guarantee that what init writes, `adlife validate` accepts.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def init_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def test_init_creates_expected_project(tmp_path: Path) -> None:
    root = init_project(tmp_path)

    assert (root / "adlife.yaml").is_file()
    assert (root / "population.yaml").is_file()
    assert (root / "campaigns" / "demo-phone.yaml").is_file()
    assert (root / "campaigns" / "demo-billboard.yaml").is_file()
    assert (root / "assets").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "reports").is_dir()


def test_init_writes_a_gitignore_that_keeps_inputs_and_drops_outputs(tmp_path: Path) -> None:
    root = init_project(tmp_path)
    text = (root / ".gitignore").read_text(encoding="utf-8")

    assert ".env" in text
    assert "runs/" in text
    assert "reports/" in text


def test_init_refuses_a_non_empty_target(tmp_path: Path) -> None:
    root = init_project(tmp_path)

    again = runner.invoke(app, ["init", "study", "--parent", str(tmp_path)])
    assert again.exit_code == 2
    assert (root / "adlife.yaml").is_file()


def test_init_writes_a_project_that_validates(tmp_path: Path) -> None:
    root = init_project(tmp_path)

    result = runner.invoke(app, ["--format", "json", "validate", str(root)])
    assert result.exit_code == 0, result.output
    import json

    assert json.loads(result.stdout)["valid"] is True


def test_init_writes_the_routable_demo_scenario(tmp_path: Path) -> None:
    from adlife.cli.project import load_project

    root = init_project(tmp_path)
    project = load_project(root)

    assert len(project.scenario.population) == 3
    campaign_ids = {campaign.campaign_id for campaign in project.scenario.campaigns}
    assert campaign_ids == {"demo-phone", "demo-billboard"}


def test_population_generate_writes_loadable_yaml(tmp_path: Path) -> None:
    out = tmp_path / "generated-population.yaml"
    result = runner.invoke(
        app,
        [
            "population",
            "generate",
            "--size",
            "3",
            "--seed",
            "42",
            "--locale",
            "fa-IR",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output

    import yaml

    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert [profile["agent_id"] for profile in document["profiles"]] == [
        "person-001",
        "person-002",
        "person-003",
    ]


def test_population_generate_to_stdout_is_yaml(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["population", "generate", "--size", "2", "--seed", "7", "--locale", "en-US"],
    )
    assert result.exit_code == 0, result.output

    import yaml

    document = yaml.safe_load(result.stdout)
    assert len(document["profiles"]) == 2
