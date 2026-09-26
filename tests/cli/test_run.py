"""`adlife run`: the whole workflow, from a project directory to a stored artifact.

The run command is the CLI's heart, so the tests pin the contract the rest of the
product depends on: a rules run completes and leaves the exact spec-14 artifact
layout, an existing run identifier is refused rather than overwritten, the live
mode refuses a non-terminal without fallback, and replay reproduces the stored
stream byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()

RUN_DIRECTORY_ENTRIES = {
    "run.json",
    "inputs",
    "events.jsonl",
    "results.sqlite3",
    "metrics.json",
    "provider-usage.json",
}


def make_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def test_rules_run_completes_and_writes_the_specified_artifacts(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        ["--format", "json", "run", str(project), "--run-id", "run-cli", "--mode", "rules"],
    )
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    assert document["run_id"] == "run-cli"
    assert document["status"] == "completed"

    run_dir = project / "runs" / "run-cli"
    assert {entry.name for entry in run_dir.iterdir()} == RUN_DIRECTORY_ENTRIES


def test_run_refuses_an_existing_run_identifier(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first = runner.invoke(app, ["run", str(project), "--run-id", "run-twice", "--mode", "rules"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app, ["--format", "json", "run", str(project), "--run-id", "run-twice", "--mode", "rules"]
    )
    # A run conflict is a provider/run conflict, not an invalid input: exit 3.
    assert second.exit_code == 3
    assert "run-twice" in second.stdout


def test_run_accepts_documented_overrides(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "run",
            str(project),
            "--run-id",
            "run-overrides",
            "--mode",
            "rules",
            "--days",
            "2",
            "--population-size",
            "3",
            "--seed",
            "7",
        ],
    )
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    assert document["final_minute"] == 2 * 1440

    manifest = json.loads((project / "runs" / "run-overrides" / "run.json").read_text("utf-8"))
    assert manifest["seed"] == 7


def test_run_rejects_an_invalid_run_id(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        ["run", str(project), "--run-id", "RUN_WITH_UPPER", "--mode", "rules"],
    )
    assert result.exit_code == 2


def test_live_mode_refuses_non_tty_without_fallback(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        ["run", str(project), "--live", "--no-headless-fallback", "--mode", "rules"],
    )
    assert result.exit_code == 2


def test_live_mode_falls_back_to_headless_by_default(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app, ["run", str(project), "--run-id", "run-live", "--live", "--mode", "rules"]
    )
    assert result.exit_code == 0, result.output
    assert (project / "runs" / "run-live" / "run.json").is_file()


def test_live_with_jsonl_stream_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        ["--format", "jsonl", "run", str(project), "--live", "--mode", "rules"],
    )
    assert result.exit_code == 2
    assert "jsonl" in result.output.lower()


def test_replay_mode_without_a_cache_refuses(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app, ["--format", "json", "run", str(project), "--run-id", "run-replay", "--mode", "replay"]
    )
    assert result.exit_code == 3


def test_replay_reproduces_a_rules_run(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    rules = runner.invoke(app, ["run", str(project), "--run-id", "run-again", "--mode", "rules"])
    assert rules.exit_code == 0, rules.output

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "replay",
            str(project),
            "run-again",
        ],
    )
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    assert document["replay-identical"] is True
    assert document["event_count"] > 0


def test_replay_refuses_an_unknown_run(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(app, ["--format", "json", "replay", str(project), "run-ghost"])
    assert result.exit_code == 4


def test_jsonl_mode_streams_events(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "--format",
            "jsonl",
            "run",
            str(project),
            "--run-id",
            "run-jsonl",
            "--mode",
            "rules",
        ],
    )
    assert result.exit_code == 0, result.output

    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert lines, "jsonl mode must stream at least the start and completion events"
    assert lines[0]["event_type"] == "run.started"
    assert lines[-1]["event_type"] == "run.completed"


def test_compare_wraps_the_experiment_runner(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "compare",
            str(project),
            str(project),
            "--seeds",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output

    document = json.loads(result.stdout)
    assert document["metrics"]["recall"]["mean_paired_difference"] == 0
