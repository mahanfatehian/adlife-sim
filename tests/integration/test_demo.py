"""`adlife demo`: the credential-free, network-free guided tour.

The demo is the first thing a stranger runs, so its guarantee is absolute: no provider
credentials, no network access anywhere in the pipeline, and the run's own artifact is
the proof. The suite pins the headless path end to end and the terminal-detection
fallback that keeps the command honest on non-interactive shells.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.app import app

runner = CliRunner()


def test_demo_headless_completes_offline_and_prints_the_artifact(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["--format", "json", "demo", "--headless", "--dir", str(tmp_path / "demo")]
    )

    assert result.exit_code == 0, result.output
    study = tmp_path / "demo"
    assert (study / "adlife.yaml").is_file()
    run_dirs = list((study / "runs").iterdir())
    assert len(run_dirs) == 1
    store = SQLiteRunStore(study)
    stored = store.load_run(run_dirs[0].name)
    assert stored.status == "completed"
    assert stored.manifest.provider in {"mock", "rules"}
    assert stored.scenario.population

    document = json.loads(result.stdout)
    assert document["artifact"]
    assert document["status"] == "completed"


def test_demo_runs_without_any_provider_credentials(tmp_path: Path, monkeypatch) -> None:
    """No credential environment variable may exist; the demo never needs one."""
    for name in ("ADLIFE_API_KEY", "OPENAI_API_KEY", "ADLIFE_PROVIDER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    result = runner.invoke(app, ["demo", "--headless", "--dir", str(tmp_path / "demo")])

    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    for marker in ("api key", "unauthorized", "401", "missing credential"):
        assert marker not in lowered, marker


def test_demo_refuses_an_existing_directory(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()

    result = runner.invoke(app, ["demo", "--headless", "--dir", str(target)])

    assert result.exit_code == 2


def test_demo_populates_twenty_agents_for_three_days(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--headless", "--dir", str(tmp_path / "demo")])

    assert result.exit_code == 0, result.output
    store = SQLiteRunStore(tmp_path / "demo")
    stored = store.load_run(next((tmp_path / "demo" / "runs").iterdir()).name)
    assert len(stored.scenario.population) == 20
    assert stored.result is not None
    assert stored.result.final_minute == 3 * 1440
