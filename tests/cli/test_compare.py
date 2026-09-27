"""Directory names are provenance, not a paired experiment's treatment."""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def paired_studies(tmp_path: Path) -> tuple[Path, Path]:
    for name in ("control-study", "treatment-study"):
        result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
        assert result.exit_code == 0, result.output
    return tmp_path / "control-study", tmp_path / "treatment-study"


@pytest.mark.parametrize("change_campaign", [False, True])
def test_compare_accepts_distinct_study_directories(tmp_path, change_campaign):
    control, treatment = paired_studies(tmp_path)
    if change_campaign:
        campaign_path = treatment / "campaigns" / "demo-phone.yaml"
        campaign = yaml.safe_load(campaign_path.read_text("utf-8"))
        campaign["price"]["amount"] += 100.0
        campaign_path.write_text(yaml.safe_dump(campaign), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "compare",
            str(control),
            str(treatment),
            "--seeds",
            "3",
            "--treatment-path",
            "campaigns",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["seeds"] == [0, 1, 2]
    assert document["label"] == "control-study-vs-treatment-study"
    if not change_campaign:
        assert all(metric["mean_paired_difference"] == 0 for metric in document["metrics"].values())
    scenarios = [
        json.loads(path.read_text("utf-8"))
        for path in (control / "runs").glob("*/inputs/scenario.json")
    ]
    assert len(scenarios) == 6
    assert len({scenario["scenario_id"] for scenario in scenarios}) == 1
    assert len({scenario["name"] for scenario in scenarios}) == 1
    assert all(scenario["population"] == scenarios[0]["population"] for scenario in scenarios)


@pytest.mark.parametrize("fmt", ["human", "json"])
def test_compare_rejects_a_real_population_difference_as_input(tmp_path, fmt):
    control, treatment = paired_studies(tmp_path)
    population_path = treatment / "population.yaml"
    population = yaml.safe_load(population_path.read_text("utf-8"))
    traits = population["profiles"][0]["traits"]
    traits["price_sensitivity"] = 0.99 if traits["price_sensitivity"] != 0.99 else 0.01
    population_path.write_text(yaml.safe_dump(population), encoding="utf-8")

    result = runner.invoke(
        app, ["--format", fmt, "compare", str(control), str(treatment), "--seeds", "1"]
    )

    assert result.exit_code == 2, result.output
    assert "population" in result.stderr
    assert "Traceback" not in result.stderr
    if fmt == "json":
        assert json.loads(result.stdout)["error"]["exit_code"] == 2
    assert not list((control / "runs").iterdir())
    assert not list((treatment / "runs").iterdir())


def test_a_a_then_a_b_comparisons_do_not_collide_or_overwrite(tmp_path):
    from hashlib import sha256

    control = tmp_path / "university-study"
    treatment = tmp_path / "university-study-treatment"
    for root in (control, treatment):
        initialized = runner.invoke(app, ["init", root.name, "--parent", str(tmp_path)])
        assert initialized.exit_code == 0, initialized.output
    campaign_path = treatment / "campaigns" / "demo-phone.yaml"
    campaign = yaml.safe_load(campaign_path.read_text("utf-8"))
    campaign["price"]["amount"] += 100.0
    campaign_path.write_text(yaml.safe_dump(campaign), encoding="utf-8")

    def compare(other):
        return runner.invoke(
            app, ["--format", "json", "compare", str(control), str(other), "--seeds", "1"]
        )

    baseline = compare(control)
    assert baseline.exit_code == 0, baseline.output
    original = {
        str(path.relative_to(control)): sha256(path.read_bytes()).hexdigest()
        for path in (control / "runs").rglob("*")
        if path.is_file()
    }
    experiment = compare(treatment)
    assert experiment.exit_code == 0, experiment.output
    assert len(list((control / "runs").iterdir())) == 4
    assert all(len(path.name) <= 40 for path in (control / "runs").iterdir())
    assert all(
        sha256((control / path).read_bytes()).hexdigest() == digest
        for path, digest in original.items()
    )
    duplicate = compare(treatment)
    assert duplicate.exit_code == 3, duplicate.output
    assert len(list((control / "runs").iterdir())) == 4
