"""Replay is an audit, not an editor: it never destroys an existing artifact.

Replay writes a derived artifact beside the stored run it verifies. When a run or a
replay already occupies the destination, the honest behavior is to refuse and leave
every byte in place — the CLI's duplicate-run refusal sets that contract, and the
derived stream is reproducible from the source at any time, so nothing is lost by
refusing. These tests pin the refusal and prove the source artifacts survive replay
byte for byte.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def make_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def artifact_digests(run_dir: Path) -> dict[str, str]:
    files = ["events.jsonl", "results.sqlite3", "run.json", "metrics.json", "provider-usage.json"]
    return {
        name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
        for name in files
        if (run_dir / name).is_file()
    }


def a_completed_run(project: Path, run_id: str) -> None:
    result = runner.invoke(app, ["run", str(project), "--run-id", run_id, "--mode", "rules"])
    assert result.exit_code == 0, result.output


def test_replay_refuses_an_existing_replay_destination(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    a_completed_run(project, "run-again")

    first = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert second.exit_code == 3, second.output
    assert "replay-run-again" in second.output


def test_a_replay_refusal_leaves_the_previous_replay_intact(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    a_completed_run(project, "run-again")
    runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    before = artifact_digests(project / "runs" / "replay-run-again")

    refused = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert refused.exit_code == 3
    after = artifact_digests(project / "runs" / "replay-run-again")
    assert before == after, "a refused replay must not touch the existing replay artifacts"


def test_replay_never_overwrites_a_run_directory(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    a_completed_run(project, "run-again")
    # An attacker-shaped or unlucky name must never cause a deletion either.
    collision = project / "runs" / "replay-run-again"
    collision.mkdir(exist_ok=True)

    result = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert result.exit_code == 3
    assert collision.is_dir()


def test_replay_leaves_the_source_run_byte_identical(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    a_completed_run(project, "run-again")
    before = artifact_digests(project / "runs" / "run-again")

    result = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert result.exit_code == 0, result.output
    after = artifact_digests(project / "runs" / "run-again")
    assert before == after, "replay must not modify the source run artifacts"


def test_replay_still_verifies_deterministic_event_equality(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    a_completed_run(project, "run-again")

    result = runner.invoke(app, ["--format", "json", "replay", str(project), "run-again"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["replay-identical"] is True
    assert document["event_count"] > 0
