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

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def make_project(tmp_path: Path, name: str = "study") -> Path:
    result = runner.invoke(app, ["init", name, "--parent", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / name


def artifact_digests(run_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in run_dir.rglob("*")
        if path.is_file()
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


def test_replay_defaults_to_the_recorded_mock_provider(tmp_path):
    project = make_project(tmp_path)
    run = runner.invoke(app, ["run", str(project), "--run-id", "mock-source", "--mode", "hybrid"])
    assert run.exit_code == 0, run.output
    # Mutable project inputs must have no bearing on replay.
    (project / "adlife.yaml").write_text("broken: [", encoding="utf-8")
    result = runner.invoke(app, ["--format", "json", "replay", str(project), "mock-source"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["replay-identical"] is True


def test_long_run_ids_get_distinct_replay_destinations(tmp_path):
    project = make_project(tmp_path)
    names = ["a" * 39 + "b", "a" * 39 + "c"]
    destinations = []
    for name in names:
        a_completed_run(project, name)
        result = runner.invoke(app, ["--format", "json", "replay", str(project), name])
        assert result.exit_code == 0, result.output
        destinations.append(json.loads(result.stdout)["replay_run_id"])
    assert destinations[0] != destinations[1]
    assert all(len(name) <= 40 for name in destinations)


def test_normalization_never_changes_arbitrary_payload_text():
    from adlife.cli.commands.replay import _stream
    from adlife.core.domain.events import DomainEvent, EventSource, EventType

    source = DomainEvent(
        event_id="run-a:event-00000000",
        run_id="run-a",
        simulated_minute=0,
        sequence=0,
        event_type=EventType.RUN_STARTED,
        source=EventSource.RULE,
        payload={"description": "run-a"},
    )
    replay = source.model_copy(
        update={
            "event_id": "run-b:event-00000000",
            "run_id": "run-b",
            "payload": {"description": "run-b"},
        }
    )
    assert _stream((source,), "run-a") != _stream((replay,), "run-b")


def test_hybrid_replay_uses_only_recorded_cache_and_frozen_inputs(tmp_path, monkeypatch):
    import httpx
    import yaml

    project = make_project(tmp_path)
    config_path = project / "adlife.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["provider"] = {"mode": "local", "model": "audit-model", "retries": 0}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline fixture")

    monkeypatch.setattr(httpx.AsyncClient, "send", unavailable)
    run = runner.invoke(app, ["run", str(project), "--run-id", "hybrid-source", "--mode", "hybrid"])
    assert run.exit_code == 0, run.output
    before = artifact_digests(project / "runs" / "hybrid-source")
    config_path.write_text("invalid: [", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not construct a live provider")

    monkeypatch.setattr(
        "adlife.adapters.cognition.openai_compatible.OpenAICompatibleProvider", forbidden
    )
    result = runner.invoke(app, ["--format", "json", "replay", str(project), "hybrid-source"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["replay-identical"] is True
    assert artifact_digests(project / "runs" / "hybrid-source") == before


@pytest.mark.parametrize("corrupt", [False, True])
def test_hybrid_successful_answers_replay_from_validated_cache(tmp_path, monkeypatch, corrupt):
    import yaml

    from adlife.adapters.cognition.mock import MockCognitionProvider
    from adlife.adapters.cognition.openai_compatible import ProviderCall
    from adlife.core.ports.cognition import CognitionAnswer, ProviderUsage

    project = make_project(tmp_path)
    config_path = project / "adlife.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["provider"] = {"mode": "local", "model": "audit-model", "retries": 0}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    async def answer(self, request):
        result = await MockCognitionProvider(seed=42).evaluate(request)
        return ProviderCall(
            answer=CognitionAnswer(
                result=result,
                usage=ProviderUsage(
                    provider_kind="local-llm",
                    model_id="audit-model",
                    prompt_tokens=2,
                    completion_tokens=3,
                    latency_ms=1,
                ),
            ),
            raw_response=None,
        )

    monkeypatch.setattr(
        "adlife.adapters.cognition.openai_compatible.OpenAICompatibleProvider.call", answer
    )
    run = runner.invoke(app, ["run", str(project), "--run-id", "cache-source", "--mode", "hybrid"])
    assert run.exit_code == 0, run.output
    files = list((project / "cache").glob("*.json"))
    assert files
    manifest = json.loads((project / "runs" / "cache-source" / "run.json").read_text("utf-8"))
    cached = json.loads(files[0].read_text(encoding="utf-8"))
    assert manifest["prompt_hash"] == cached["provider_metadata"]["prompt_sha256"]
    if corrupt:
        path = files[0]
        record = json.loads(path.read_text(encoding="utf-8"))
        record["request"]["mood"] = 0.91
        path.write_text(json.dumps(record), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("network constructor during replay")

    monkeypatch.setattr(
        "adlife.adapters.cognition.openai_compatible.OpenAICompatibleProvider", forbidden
    )
    before = artifact_digests(project / "runs" / "cache-source")
    result = runner.invoke(app, ["--format", "json", "replay", str(project), "cache-source"])
    assert result.exit_code == (4 if corrupt else 0), result.output
    if not corrupt:
        assert json.loads(result.stdout)["replay-identical"] is True
    assert artifact_digests(project / "runs" / "cache-source") == before


@pytest.mark.parametrize("stop_minute,hybrid", [(0, False), (15, False), (600, True)])
def test_replay_verifies_interrupted_prefix_without_future_cognition(
    tmp_path, monkeypatch, stop_minute, hybrid
):
    import asyncio

    import httpx
    import yaml

    from adlife.adapters.storage.sqlite_store import SQLiteRunStore
    from adlife.cli.commands.run import _cognition_seams, _identity
    from adlife.cli.project import load_project
    from adlife.core.simulation.runner import InterruptedRun, SimulationRunner

    root = make_project(tmp_path)
    if hybrid:
        config_path = root / "adlife.yaml"
        config = yaml.safe_load(config_path.read_text("utf-8"))
        config["provider"] = {"mode": "local", "model": "audit-model", "retries": 0}
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline fixture")

    monkeypatch.setattr(httpx.AsyncClient, "send", unavailable)
    project = load_project(root)
    mode = "hybrid" if hybrid else "rules"
    cognition, fallback = _cognition_seams(project, mode, 42, root / "cache")

    def cognition_factory(model):
        if stop_minute == 0:
            raise KeyboardInterrupt
        return cognition

    async def stop_after_commit(plan, outcome, model):
        if model.clock.current_minute >= stop_minute:
            raise KeyboardInterrupt

    source_runner = SimulationRunner(
        cognition_factory=cognition_factory,
        fallback_provider_factory=fallback,
        identity=_identity(project, mode, project.config.provider.model),
    )
    store = SQLiteRunStore(root)
    with pytest.raises(InterruptedRun):
        asyncio.run(
            source_runner.run(
                project.scenario,
                seed=42,
                store=store,
                sinks=(),
                run_id="interrupted-source",
                provider_name="local" if hybrid else "rules",
                model_id=project.config.provider.model,
                tick_observer=stop_after_commit,
            )
        )
    source = store.load_run("interrupted-source")
    assert source.result is not None
    assert source.result.final_minute == stop_minute
    before = artifact_digests(root / "runs" / "interrupted-source")

    def forbidden(*args, **kwargs):
        raise AssertionError("interrupted replay attempted a live provider")

    monkeypatch.setattr(
        "adlife.adapters.cognition.openai_compatible.OpenAICompatibleProvider", forbidden
    )
    result = runner.invoke(app, ["--format", "json", "replay", str(root), "interrupted-source"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["replay-identical"] is True
    replayed = store.load_run(document["replay_run_id"])
    assert replayed.status == "interrupted"
    assert replayed.result is not None
    assert replayed.result.final_minute == stop_minute
    assert len(replayed.events) == len(source.events)
    assert artifact_digests(root / "runs" / "interrupted-source") == before


@pytest.mark.parametrize("status", ["running", "failed"])
def test_replay_refuses_sources_without_a_trustworthy_terminal_boundary(tmp_path, status):
    import asyncio

    from adlife.adapters.storage.sqlite_store import SQLiteRunStore
    from adlife.cli.cognition import rule_fallback_for
    from adlife.cli.commands.run import _identity
    from adlife.cli.project import load_project
    from adlife.core.simulation.runner import SimulationRunner

    root = make_project(tmp_path)
    project = load_project(root)
    store = SQLiteRunStore(root)
    identity = _identity(project, "rules", "rule-v1")
    if status == "running":
        store.create_run(
            identity.manifest_for(
                run_id="unfinished",
                scenario=project.scenario,
                seed=42,
                provider="rules",
                model_id="rule-v1",
            ),
            scenario=project.scenario,
        )
    else:

        def fail(model):
            raise RuntimeError("test interruption before cognition")

        source_runner = SimulationRunner(
            cognition_factory=fail,
            fallback_provider_factory=rule_fallback_for,
            identity=identity,
        )
        with pytest.raises(RuntimeError):
            asyncio.run(
                source_runner.run(
                    project.scenario, seed=42, store=store, sinks=(), run_id="unfinished"
                )
            )
    before = artifact_digests(root / "runs" / "unfinished")
    result = runner.invoke(app, ["--format", "json", "replay", str(root), "unfinished"])
    assert result.exit_code == 4, result.output
    assert not (root / "runs" / "replay-unfinished").exists()
    assert artifact_digests(root / "runs" / "unfinished") == before
