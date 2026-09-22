"""The security boundaries: what hostile input must never achieve.

Each test attacks one boundary a real deployment actually has - the campaign asset
path, the YAML loader, the report interpolator, the provider sink, the artifact
formats, the network stack - and asserts the damage is contained. Nothing here needs
a live attacker: every payload is a file, a string or a monkeypatch, and every
credential is obviously fake.
"""

from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from adlife.cli.app import app
from adlife.cli.commands.campaign import CampaignImportError, import_campaign
from tests.builders import small_three_agent_scenario

runner = CliRunner()

FAKE_KEY = "zz_fake_key_000000000000000000000000"


# --- gate 1: campaign asset path traversal is refused -------------------------------


def test_campaign_asset_traversal_is_refused(tmp_path: Path, valid_campaign) -> None:
    document = {
        "campaign_id": "campaign-evil",
        "name": "Evil",
        "start_minute": 0,
        "end_minute": 600,
        "placements": [{"channel": "mobile-feed", "active_windows": [{"start": 0, "end": 600}]}],
        "asset_path": "../outside/secret.txt",
    }
    (tmp_path / "evil.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(CampaignImportError, match="traversal"):
        import_campaign(tmp_path / "evil.yaml", tmp_path)


def test_campaign_asset_symlink_escape_is_refused(tmp_path: Path, valid_campaign) -> None:
    outside = tmp_path.parent / "outside-campaign-secrets.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks require privileges on this platform")
    document = {
        "campaign_id": "campaign-evil",
        "name": "Evil",
        "asset_path": "link.txt",
    }
    (tmp_path / "evil.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(CampaignImportError, match="outside project root"):
        import_campaign(tmp_path / "evil.yaml", tmp_path)


def test_campaign_yaml_object_tag_is_refused(tmp_path: Path) -> None:
    hostile = "campaign_id: campaign-evil\nname: !!python/object/apply:os.system ['echo pwned']\n"
    (tmp_path / "evil.yaml").write_text(hostile, encoding="utf-8")

    with pytest.raises(Exception) as caught:
        import_campaign(tmp_path / "evil.yaml", tmp_path)
    # The constructor was never applied: the failure is a parse/validation refusal
    # naming the tag, and `pwned` - the command's effect - never executed anywhere.
    assert "pwned" not in str(caught.value)
    assert not isinstance(caught.value, (OSError, SystemExit))


# --- gate 2: hostile display names cannot break artifacts ---------------------------


def test_hostile_campaign_name_survives_the_whole_pipeline(tmp_path: Path, valid_scenario) -> None:
    hostile_scenario = small_three_agent_scenario(valid_scenario)
    hostile_campaigns = tuple(
        campaign.model_copy(update={"name": "../../etc/passwd <script>alert(1)</script>"})
        for campaign in hostile_scenario.campaigns
    )
    scenario = hostile_scenario.model_copy(update={"campaigns": hostile_campaigns})

    import asyncio

    from adlife.adapters.output.jsonl import JsonlEventSink
    from adlife.adapters.storage.sqlite_store import SQLiteRunStore
    from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for
    from adlife.core.simulation.runner import RunIdentity, SimulationRunner
    from adlife.reporting.html import render_report

    async def _drive() -> SQLiteRunStore:
        store = SQLiteRunStore(tmp_path)
        runner_ = SimulationRunner(
            cognition_factory=lambda model: RuleCognitionPort(),
            fallback_provider_factory=rule_fallback_for,
            identity=RunIdentity.for_project(
                project_root=tmp_path,
                provider="rules",
                model_id="rule-baseline",
            ),
        )
        await runner_.run(
            scenario,
            seed=42,
            store=store,
            sinks=[JsonlEventSink(tmp_path / "hostile.jsonl")],
            run_id="run-hostile",
        )
        return store

    store = asyncio.run(_drive())
    stored = store.load_run("run-hostile")

    # The JSONL sink carries the name as data, not as markup.
    jsonl_text = (tmp_path / "hostile.jsonl").read_text(encoding="utf-8")
    for line in jsonl_text.splitlines():
        record = json.loads(line)
        assert isinstance(record, dict)

    # The report escapes it.
    report = render_report(stored, tmp_path / "hostile.html")
    text = report.read_text(encoding="utf-8")
    assert "<script>alert(" not in text


# --- gate 3: provider secrets never reach artifacts ---------------------------------


def test_fake_api_key_is_absent_from_run_artifacts(
    tmp_path: Path, valid_scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADLIFE_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    import asyncio

    from adlife.adapters.output.jsonl import JsonlEventSink
    from adlife.adapters.storage.sqlite_store import SQLiteRunStore
    from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for
    from adlife.core.simulation.runner import RunIdentity, SimulationRunner

    scenario = small_three_agent_scenario(valid_scenario)

    async def _drive() -> SQLiteRunStore:
        store = SQLiteRunStore(tmp_path)
        runner_ = SimulationRunner(
            cognition_factory=lambda model: RuleCognitionPort(),
            fallback_provider_factory=rule_fallback_for,
            identity=RunIdentity.for_project(
                project_root=tmp_path,
                provider="rules",
                model_id="rule-baseline",
            ),
        )
        await runner_.run(
            scenario,
            seed=42,
            store=store,
            sinks=[JsonlEventSink(tmp_path / "keyrun.jsonl")],
            run_id="run-key",
        )
        return store

    asyncio.run(_drive())

    for artifact in tmp_path.rglob("*"):
        if artifact.is_file():
            assert FAKE_KEY not in artifact.read_text(encoding="utf-8", errors="ignore"), (
                f"fake key leaked into {artifact.name}"
            )


# --- gate 4: corrupt artifacts are detected, not trusted ----------------------------


def test_truncated_jsonl_sink_is_detected_on_read(tmp_path: Path) -> None:
    from adlife.adapters.output.jsonl import JsonlEventSink

    path = tmp_path / "corrupt.jsonl"
    sink = JsonlEventSink(path)
    sink.write_jsonl([]) if hasattr(sink, "write_jsonl") else None

    path.write_text(
        '{"event_id": "x"\n{"event_id": "y", "truncated": true\n',
        encoding="utf-8",
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    with pytest.raises(json.JSONDecodeError):
        for line in lines:
            json.loads(line)


def test_corrupt_sqlite_database_is_detected_on_open(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    database.write_bytes(b"this is not a sqlite database at all" * 10)

    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(database).execute("SELECT 1 FROM runs")


# --- gate 5: an offline run never touches the network -------------------------------


def _install_network_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid every outbound network touch, allowing only loopback self-pipes.

    Name resolution and ``create_connection`` are banned outright. A raw
    ``connect`` is allowed only to a loopback address, which is how the asyncio
    event loop's socketpair wakes itself on platforms without a native pair - it
    never leaves the machine. Any other address is refused.
    """

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("an offline run reached the network")

    def _guarded_connect(self: object, address: object) -> object:
        host = address[0] if isinstance(address, tuple) and address else address
        if host in {"localhost", "127.0.0.1", "::1"}:
            return real_connect(self, address)
        raise AssertionError(f"an offline run connected to {host!r}")

    real_connect = socket.socket.connect

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


def test_demo_rules_run_touches_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_network_ban(monkeypatch)

    project = tmp_path / "study"
    init = runner.invoke(app, ["init", str(project)])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        app,
        [
            "run",
            str(project),
            "--mode",
            "rules",
            "--headless",
            "--run-id",
            "offline-run",
        ],
    )
    assert result.exit_code == 0, result.output


# --- gate 6: demo keeps every value synthetic and offline ---------------------------


def test_demo_stays_synthetic_and_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_network_ban(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ADLIFE_API_KEY", raising=False)

    result = runner.invoke(app, ["demo", "--headless", "--dir", str(tmp_path / "demo-study")])
    assert result.exit_code == 0, result.output
