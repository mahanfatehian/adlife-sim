"""The run manifest's provider identity: what actually answered, and nothing else.

The manifest's ``provider`` field is the auditable record of which cognition provider
a run used, and it uses the domain vocabulary ``rules | mock | local | remote |
replay``. These tests pin that a hybrid run through a real configured provider is
never persisted as ``mock``, a replay is never persisted as ``mock``, and the
deterministic modes keep their recorded names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def make_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def manifest_provider(project: Path, run_id: str) -> str:
    manifest = json.loads((project / "runs" / run_id / "run.json").read_text("utf-8"))
    return str(manifest["provider"])


def write_local_provider_config(project: Path, model: str = "test-model") -> None:
    """Point the study's provider at a loopback OpenAI-compatible endpoint."""
    config_path = project / "adlife.yaml"
    import yaml

    document = yaml.safe_load(config_path.read_text("utf-8"))
    document["provider"] = {
        "mode": "local",
        "model": model,
        "base_url": "http://127.0.0.1:11434/v1",
        "retries": 0,
    }
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")


def test_rules_run_records_rules(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = runner.invoke(app, ["run", str(project), "--run-id", "run-rules", "--mode", "rules"])
    assert result.exit_code == 0, result.output
    assert manifest_provider(project, "run-rules") == "rules"


def test_hybrid_mock_provider_records_mock(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = runner.invoke(app, ["run", str(project), "--run-id", "run-mock", "--mode", "hybrid"])
    assert result.exit_code == 0, result.output
    assert manifest_provider(project, "run-mock") == "mock"


def test_hybrid_local_provider_is_not_recorded_as_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hybrid run through a real configured provider cannot be persisted as mock.

    The provider is unreachable (nothing listens on the loopback endpoint), so every
    cognition request falls back to the rule formula — the documented hybrid behavior —
    but the manifest must still record the provider the run was actually wired to
    (``local``), not collapse it to ``mock``. The recorded fallbacks in the event
    stream are the evidence the provider was really dispatched.
    """
    monkeypatch.setenv("ADLIFE_API_KEY", "")  # local mode ignores credentials entirely
    project = make_project(tmp_path)
    write_local_provider_config(project)

    result = runner.invoke(app, ["run", str(project), "--run-id", "run-local", "--mode", "hybrid"])
    assert result.exit_code == 0, result.output
    assert manifest_provider(project, "run-local") == "local"

    events = (project / "runs" / "run-local" / "events.jsonl").read_text("utf-8")
    assert "cognition.fallback" in events, "an unreachable local provider must fall back"


def test_replay_run_records_replay(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    rules = runner.invoke(app, ["run", str(project), "--run-id", "run-source", "--mode", "rules"])
    assert rules.exit_code == 0, rules.output

    result = runner.invoke(
        app,
        ["--format", "json", "replay", str(project), "run-source"],
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["replay-identical"] is True
    assert manifest_provider(project, str(document["replay_run_id"])) == "replay"


def test_default_run_id_is_stable_across_python_hash_seeds(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    from hashlib import sha256

    project = make_project(tmp_path)
    command = [sys.executable, "-m", "adlife", "--format", "json", "run", str(project)]
    first = subprocess.run(
        command,
        env={**os.environ, "PYTHONHASHSEED": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    run_id = json.loads(first.stdout)["run_id"]
    original = {
        path.relative_to(project): sha256(path.read_bytes()).hexdigest()
        for path in (project / "runs").rglob("*")
        if path.is_file()
    }

    repeated = subprocess.run(
        command,
        env={**os.environ, "PYTHONHASHSEED": "12345"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert repeated.returncode == 3, repeated.stdout + repeated.stderr
    error = json.loads(repeated.stdout)["error"]
    assert run_id in error["message"]
    assert [path.name for path in (project / "runs").iterdir()] == [run_id]
    assert all(
        sha256((project / path).read_bytes()).hexdigest() == digest
        for path, digest in original.items()
    )


def test_demo_same_seed_is_identical_in_fresh_locations(tmp_path: Path) -> None:
    def demo(name: str, seed: int) -> tuple[str, Path]:
        project = tmp_path / name
        result = runner.invoke(
            app,
            [
                "--format",
                "json",
                "demo",
                "--headless",
                "--dir",
                str(project),
                "--seed",
                str(seed),
            ],
        )
        assert result.exit_code == 0, result.output
        run_id = json.loads(result.stdout)["run_id"]
        return run_id, project / "runs" / run_id

    first_id, first = demo("first", 42)
    repeated_id, repeated = demo("different-location", 42)

    assert repeated_id == first_id
    for filename in ("inputs/scenario.json", "events.jsonl", "metrics.json"):
        assert (repeated / filename).read_bytes() == (first / filename).read_bytes(), filename

    other_id, other = demo("different-seed", 43)
    assert other_id != first_id
    first_scenario = json.loads((first / "inputs/scenario.json").read_text("utf-8"))
    other_scenario = json.loads((other / "inputs/scenario.json").read_text("utf-8"))
    assert first_scenario["population"] != other_scenario["population"]
    assert (first / "events.jsonl").read_bytes() != (other / "events.jsonl").read_bytes()
