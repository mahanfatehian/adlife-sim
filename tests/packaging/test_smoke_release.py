"""Exercise smoke workspace lifetime without creating another environment."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import smoke_release


def test_smoke_subprocesses_do_not_create_console_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_child(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        expected = 0x08000000 if sys.platform == "win32" else 0
        assert kwargs.get("creationflags", 0) == expected
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, 0, "captured", "")

    monkeypatch.setattr(smoke_release.subprocess, "run", run_child)
    assert smoke_release._run(["adlife", "--version"]).stdout == "captured"


def test_smoke_uses_the_running_supported_interpreter_without_fetching_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def record_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(smoke_release, "_uv_executable", lambda: "uv")
    monkeypatch.setattr(smoke_release, "_run", record_command)
    smoke_release._create_interpreter(tmp_path)
    assert commands == [["uv", "venv", "--python", sys.executable, str(tmp_path / "venv")]]


def test_smoke_cleans_workspace_and_returns_no_dead_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    # Environment provisioning is external; run all CLI/report checks for real
    # with the already installed environment so this regression stays offline.
    monkeypatch.setattr(
        smoke_release, "_create_interpreter", lambda path: (Path(sys.executable), None)
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args: None)

    result = smoke_release.smoke(tmp_path / "unused.whl")

    assert not workspace.exists()
    assert result is None


def test_smoke_cleans_workspace_when_installation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))

    def fail_provisioning(path: Path) -> None:
        raise RuntimeError("environment unavailable")

    monkeypatch.setattr(smoke_release, "_create_interpreter", fail_provisioning)
    with pytest.raises(RuntimeError, match="environment unavailable"):
        smoke_release.smoke(tmp_path / "unused.whl")
    assert not workspace.exists()


def test_smoke_exercises_verified_catalog_run_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a wheel smoke that omits any packaged-catalog consumer boundary."""
    workspace = tmp_path / "smoke-workspace"
    workspace.mkdir()
    scratch = workspace / "scratch"
    calls: list[tuple[list[str], Path | None]] = []
    city_sha256 = "a" * 64
    place_sha256 = "c" * 64
    assignments_sha256 = "d" * 64
    scenario_sha256 = "e" * 64
    trace_sha256 = "b" * 64
    opportunity_stream_sha256 = "f" * 64
    opportunity_summary_sha256 = "1" * 64
    opportunity_counts = {
        "opportunity_count": 4,
        "roadside_billboard_count": 0,
        "mobile_feed_count": 4,
    }

    monkeypatch.setattr(smoke_release.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    monkeypatch.setattr(
        smoke_release,
        "_create_interpreter",
        lambda path: (workspace / "venv" / "Scripts" / "python.exe", None),
    )
    monkeypatch.setattr(smoke_release, "_install_wheel", lambda *args: None)

    def run_child(
        command: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command[1:], cwd))
        arguments = command[1:]
        document: dict[str, object] = {}
        if arguments == ["--format", "json", "doctor", "--offline"]:
            document = {"status": "ok"}
        elif arguments[:3] == ["--format", "json", "run"]:
            document = {"status": "complete"}
        elif arguments[:3] == ["--format", "json", "report"]:
            report = scratch / "smoke-study" / "reports" / "smoke.html"
            report.parent.mkdir(parents=True)
            report.write_text("x" * 10_001, encoding="utf-8")
        elif arguments == ["--format", "json", "city-catalog", "list"]:
            document = {
                "cities": [
                    {
                        "city_id": "fictional-grid-v2",
                        "qualification": "fictional-fixture",
                        "pack_schema_version": 2,
                    }
                ]
            }
        elif arguments == [
            "--format",
            "json",
            "city-catalog",
            "show",
            "fictional-grid-v2",
        ]:
            document = {
                "city_id": "fictional-grid-v2",
                "qualification": "fictional-fixture",
                "pack_schema_version": 2,
                "pack_sha256": city_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-places", "validate"]:
            place_path = Path(arguments[4])
            place_document = json.loads(place_path.read_text(encoding="utf-8"))
            assert place_document["city_id"] == "fictional-grid-v2"
            assert place_document["city_sha256"] == city_sha256
            assert {place["kind"] for place in place_document["places"]} == {
                "home",
                "workplace",
                "leisure",
            }
            assert all(
                place["provenance"]["method"] == "operator-authored-fictional"
                for place in place_document["places"]
            )
            document = {
                "valid": True,
                "city_id": "fictional-grid-v2",
                "place_set_sha256": place_sha256,
            }
        elif arguments[:4] == ["--format", "json", "city-campaign", "validate"]:
            scenario_path = Path(arguments[4])
            scenario_document = json.loads(scenario_path.read_text(encoding="utf-8"))
            assert scenario_document["scenario_id"] == "clean-room-spatial"
            assert scenario_document["city_id"] == "fictional-grid-v2"
            assert scenario_document["city_sha256"] == city_sha256
            assert {item["channel"] for item in scenario_document["placements"]} == {
                "roadside-billboard",
                "mobile-feed",
            }
            document = {
                "valid": True,
                "scenario_id": "clean-room-spatial",
                "scenario_sha256": scenario_sha256,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "campaign_count": 1,
                "placement_count": 2,
                "billboard_count": 1,
                "phone_count": 1,
                "max_billboard_binding_error_meters": 0.0,
            }
        elif arguments[:3] == ["--format", "json", "city-run"]:
            document = {
                "run_id": "catalog-smoke",
                "run_schema_version": 4,
                "city_id": "fictional-grid-v2",
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "claim_scope": "synthetic-opportunity-not-impression",
                "frame_count": 1_440,
                "position_count": 2_880,
                "directory": str(scratch / "city-output" / "city-runs" / "catalog-smoke"),
            }
        elif arguments == [
            "--format",
            "json",
            "city-replay",
            "city-output",
            "catalog-smoke",
        ]:
            document = {
                "run_id": "catalog-smoke",
                "identical": True,
                "city_sha256": city_sha256,
                "place_set_sha256": place_sha256,
                "place_assignments_sha256": assignments_sha256,
                "trace_sha256": trace_sha256,
                "scenario_sha256": scenario_sha256,
                "opportunity_stream_sha256": opportunity_stream_sha256,
                "opportunity_summary_sha256": opportunity_summary_sha256,
                "opportunity_stream_bytes": 1_234,
                "opportunity_count": 4,
                "opportunity_counts": opportunity_counts,
                "claim_scope": "synthetic-opportunity-not-impression",
                "frame_count": 1_440,
                "position_count": 2_880,
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(document), "")

    monkeypatch.setattr(smoke_release, "_run", run_child)

    smoke_release.smoke(tmp_path / "unused.whl")

    invoked = [arguments for arguments, _cwd in calls]
    assert ["--format", "json", "city-catalog", "list"] in invoked
    assert [
        "--format",
        "json",
        "city-catalog",
        "show",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-places",
        "validate",
        str(scratch / "fictional-grid-v2-places.json"),
        "--city-id",
        "fictional-grid-v2",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-campaign",
        "validate",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--city-id",
        "fictional-grid-v2",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-run",
        "--city-id",
        "fictional-grid-v2",
        "--places",
        str(scratch / "fictional-grid-v2-places.json"),
        "--spatial-campaign",
        str(scratch / "fictional-grid-v2-spatial-campaign.json"),
        "--output-root",
        "city-output",
        "--run-id",
        "catalog-smoke",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in invoked
    assert [
        "--format",
        "json",
        "city-replay",
        "city-output",
        "catalog-smoke",
    ] in invoked
    assert all(cwd == scratch for _arguments, cwd in calls)
    assert not workspace.exists()
