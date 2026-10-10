"""Verified workbench run discovery stays bounded, fresh, and path-free."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adlife.city.runs import create_city_run
from adlife.city.workbench_workspace import (
    UnsafeWorkbenchWorkspace,
    prepare_workbench_workspace,
)
from adlife.core.ports.run_store import DuplicateRun, UnsafeRunLocation
from tests.unit.city.test_city_run_preparation import _validated


def _module():
    return importlib.import_module("adlife.city.workbench_runs")


def _saved_run(root: Path, *, run_id: str, workbench: bool) -> None:
    validated = _validated(run_id=run_id, agents=1, days=2)
    create_city_run(
        validated.pack,
        root=root,
        run_id=run_id,
        seed=42,
        agent_count=1,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input if workbench else None,
    )


def test_empty_discovery_and_page_arguments_are_strict_and_bounded(tmp_path: Path) -> None:
    module = _module()
    repository = module.WorkbenchRunRepository(prepare_workbench_workspace(tmp_path / "state"))

    page = repository.list_runs(offset=0, limit=20)

    assert page.model_dump(mode="json") == {
        "schema_version": 1,
        "offset": 0,
        "limit": 20,
        "returned_count": 0,
        "verified_total": 0,
        "scanned_count": 0,
        "corrupt_count": 0,
        "truncated": False,
        "runs": [],
    }
    for offset, limit in ((True, 20), (-1, 20), (10_001, 20), (0, False), (0, 0), (0, 101)):
        with pytest.raises(ValueError, match="pagination"):
            repository.list_runs(offset=offset, limit=limit)
    with pytest.raises(ValidationError, match="truncated"):
        module.WorkbenchRunPage(
            offset=0,
            limit=20,
            returned_count=0,
            verified_total=0,
            scanned_count=0,
            corrupt_count=0,
            truncated=True,
            runs=(),
        )


def test_discovery_freshly_verifies_v6_and_v7_and_pages_in_run_id_order(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="zulu-v6", workbench=False)
    _saved_run(workspace.root, run_id="alpha-v7", workbench=True)
    repository = module.WorkbenchRunRepository(workspace)

    first = repository.list_runs(offset=0, limit=1)
    second = repository.list_runs(offset=1, limit=1)

    assert first.returned_count == second.returned_count == 1
    assert first.verified_total == second.verified_total == 2
    assert first.scanned_count == second.scanned_count == 2
    assert [item.run_id for item in first.runs] == ["alpha-v7"]
    assert [item.run_id for item in second.runs] == ["zulu-v6"]
    assert [item.run_schema_version for item in (*first.runs, *second.runs)] == [7, 6]
    summary = first.runs[0]
    assert summary.city_id == "fictional-grid-v2"
    assert summary.scenario_id == "launch-study"
    assert summary.campaign_ids == ("fictional-launch",)
    assert summary.channels == ("mobile-feed", "roadside-billboard")
    assert summary.seed == "42"
    assert summary.days == 2
    assert summary.agent_count == 1
    assert summary.frame_count == 2_880
    assert summary.impression_count == summary.opportunity_count
    assert 0 <= summary.noticed_count <= summary.impression_count
    assert summary.response_count == summary.noticed_count
    assert summary.inspector_url == "/runs/alpha-v7"
    assert "directory" not in summary.model_dump(mode="json")

    run_manifest = workspace.root / "city-runs" / "alpha-v7" / "run.json"
    document = json.loads(run_manifest.read_text(encoding="utf-8"))
    document["trace_sha256"] = "0" * 64
    run_manifest.write_text(json.dumps(document), encoding="utf-8")
    refreshed = repository.list_runs(offset=0, limit=20)
    assert [item.run_id for item in refreshed.runs] == ["zulu-v6"]
    assert refreshed.corrupt_count == 1


def test_summary_serializes_maximum_seed_as_an_exact_decimal_string(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    maximum = 2**63 - 1
    validated = _validated(run_id="maximum-seed", seed=maximum, agents=1, days=2)
    create_city_run(
        validated.pack,
        root=workspace.root,
        run_id="maximum-seed",
        seed=maximum,
        agent_count=1,
        days=2,
        spatial_scenario=validated.scenario,
        spatial_response=validated.response_input,
        workbench_input=validated.workbench_input,
    )

    summary = module.WorkbenchRunRepository(workspace).verified_summary("maximum-seed")

    assert summary.seed == "9223372036854775807"
    assert summary.model_dump(mode="json")["seed"] == "9223372036854775807"


def test_summary_bounds_cover_the_largest_valid_workbench_run() -> None:
    module = _module()

    summary = module.WorkbenchRunSummary(
        run_id="maximum-workbench-run",
        run_schema_version=7,
        city_id="maximum-city",
        city_schema_version=2,
        city_sha256="1" * 64,
        scenario_id="maximum-scenario",
        scenario_sha256="2" * 64,
        campaign_ids=tuple(f"campaign-{index:02d}" for index in range(20)),
        channels=("mobile-feed", "roadside-billboard"),
        seed="9223372036854775807",
        days=7,
        agent_count=30,
        frame_count=10_080,
        opportunity_count=520_800,
        impression_count=520_800,
        noticed_count=520_800,
        response_count=520_800,
        inspector_url="/runs/maximum-workbench-run",
    )

    assert summary.agent_count == 30
    assert summary.days == 7
    assert summary.response_count == 520_800
    with pytest.raises(ValidationError, match="inspector"):
        summary.model_copy(update={"inspector_url": "/runs/a-different-run"})
    with pytest.raises(ValidationError, match="frame count"):
        summary.model_copy(update={"frame_count": 10_079})
    with pytest.raises(ValidationError, match="campaign"):
        summary.model_copy(update={"campaign_ids": ()})
    with pytest.raises(ValidationError, match="channel"):
        summary.model_copy(update={"channels": ()})

    legacy = summary.model_dump(mode="python")
    legacy.update(
        {
            "run_schema_version": 2,
            "city_schema_version": 1,
            "scenario_id": None,
            "scenario_sha256": None,
            "campaign_ids": (),
            "channels": (),
            "opportunity_count": 0,
            "impression_count": 0,
            "noticed_count": 0,
            "response_count": 0,
        }
    )
    with pytest.raises(ValidationError, match="city schema"):
        module.WorkbenchRunSummary.model_validate(legacy)


def test_discovery_excludes_partial_invalid_and_symlinked_candidates(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="valid-run", workbench=True)
    runs = workspace.root / "city-runs"
    (runs / "partial-run").mkdir()
    (runs / "not a portable id").mkdir()
    (runs / "plain-file").write_text("not a run", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = runs / "linked-run"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        linked = None

    page = module.WorkbenchRunRepository(workspace).list_runs(offset=0, limit=100)

    assert [item.run_id for item in page.runs] == ["valid-run"]
    assert page.scanned_count == 4 + int(linked is not None)
    assert page.corrupt_count == 3 + int(linked is not None)
    assert page.truncated is False


def test_discovery_scans_at_most_256_direct_candidates(tmp_path: Path) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    runs = workspace.root / "city-runs"
    runs.mkdir()
    for index in range(257):
        (runs / f"partial-{index:03d}").mkdir()

    page = module.WorkbenchRunRepository(workspace).list_runs(offset=0, limit=100)

    assert page.scanned_count == 256
    assert page.corrupt_count == 256
    assert page.verified_total == 0
    assert page.truncated is True


def test_repository_rechecks_workspace_and_refuses_every_existing_run_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = module.WorkbenchRunRepository(workspace)
    run_directory = workspace.root / "city-runs" / "reserved-run"
    run_directory.mkdir(parents=True)

    with pytest.raises(DuplicateRun):
        repository.ensure_available("reserved-run")

    def replaced(_workspace):
        raise UnsafeWorkbenchWorkspace("workbench workspace identity changed")

    monkeypatch.setattr(module, "verify_workbench_workspace", replaced)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.list_runs(offset=0, limit=20),
        lambda: repository.load("reserved-run"),
    ):
        with pytest.raises(UnsafeWorkbenchWorkspace, match="identity"):
            operation()


def test_repository_prepares_publishes_and_independently_verifies_one_run(
    tmp_path: Path,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    repository = module.WorkbenchRunRepository(workspace)
    validated = _validated(run_id="repository-run", agents=1, days=2)

    repository.ensure_available("repository-run")
    prepared = repository.prepare(validated)
    assert not (workspace.root / "city-runs").exists()
    published = repository.publish(prepared)
    summary = repository.verified_summary("repository-run")

    assert published.manifest.run_id == summary.run_id == "repository-run"
    assert summary.run_schema_version == 7
    assert repository.load("repository-run").manifest == published.manifest


def test_repository_rejects_reparse_points_at_every_direct_store_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    _saved_run(workspace.root, run_id="stored-run", workbench=True)
    repository = module.WorkbenchRunRepository(workspace)
    prepared = repository.prepare(_validated(run_id="fresh-run", agents=1, days=2))
    original = module._require_direct_directory

    def reject_runs_directory(path: Path, *, label: str) -> None:
        if path == workspace.root / "city-runs":
            raise UnsafeRunLocation("simulated reparse point")
        original(path, label=label)

    monkeypatch.setattr(module, "_require_direct_directory", reject_runs_directory)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.load("stored-run"),
        lambda: repository.publish(prepared),
    ):
        with pytest.raises(UnsafeRunLocation, match="reparse"):
            operation()


def test_repository_preflight_rejects_a_reparse_point_at_the_candidate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    workspace = prepare_workbench_workspace(tmp_path / "state")
    workspace.root.joinpath("city-runs").mkdir()
    repository = module.WorkbenchRunRepository(workspace)
    prepared = repository.prepare(_validated(run_id="fresh-run", agents=1, days=2))
    original = module._direct_location_exists

    def reject_candidate(path: Path, *, label: str) -> bool:
        if path == workspace.root / "city-runs" / "fresh-run":
            raise UnsafeRunLocation("simulated candidate reparse point")
        return original(path, label=label)

    monkeypatch.setattr(module, "_direct_location_exists", reject_candidate)
    for operation in (
        lambda: repository.ensure_available("fresh-run"),
        lambda: repository.publish(prepared),
    ):
        with pytest.raises(UnsafeRunLocation, match="candidate reparse"):
            operation()
