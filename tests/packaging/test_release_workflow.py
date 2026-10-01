"""Validate publication gates using the parsed workflow dependency graph."""

import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def test_ci_runs_maximum_performance_outside_coverage() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    coverage = next(
        step for step in steps if step.get("name") == "Test suite with branch coverage (85% floor)"
    )
    performance = next(step for step in steps if step.get("name") == "Maximum rules performance")

    assert coverage["env"]["ADLIFE_SKIP_LONG_TESTS"] == "1"
    assert performance["run"] == "uv run pytest -q -s tests/integration/test_maximum_run.py"
    ceiling = performance["env"]["ADLIFE_PERF_CEILING_SECONDS"]
    assert "runner.os == 'Windows'" in ceiling
    assert "'60'" in ceiling


@pytest.mark.parametrize("publisher", ["publish-pypi", "publish-release"])
def test_publication_waits_for_completed_asset_assembly(publisher: str) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]
    pending = [publisher]
    ancestors: set[str] = set()
    while pending:
        current = pending.pop()
        needs = jobs[current].get("needs", [])
        for dependency in [needs] if isinstance(needs, str) else needs:
            assert dependency in jobs, f"unknown dependency: {dependency}"
            if dependency not in ancestors:
                ancestors.add(dependency)
                pending.append(dependency)
    assert {"verify-tag", "build-native", "assemble-release"} <= ancestors


def test_pypi_consumes_the_distributions_uploaded_by_assembly() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]
    uploads = {
        step["with"]["name"]: step["with"]
        for step in jobs["assemble-release"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    }
    publisher_steps = jobs["publish-pypi"]["steps"]
    downloads = [
        step["with"]
        for step in publisher_steps
        if step.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert len(downloads) == 1, "publish must consume assembled distributions"
    downloaded = downloads[0]
    assert downloaded["name"] in uploads
    assert uploads[downloaded["name"]]["path"].rstrip("/") == "dist"
    assert uploads[downloaded["name"]]["if-no-files-found"] == "error"
    assert downloaded["path"].rstrip("/") == "dist"
    # No intervening shell build may replace the verified distributions.
    assert all("run" not in step for step in publisher_steps)


def test_dependency_audit_targets_locked_project_requirements(tmp_path: Path) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    commands = [
        shlex.split(line)
        for step in workflow["jobs"]["security"]["steps"]
        for line in step.get("run", "").splitlines()
        if line.strip()
    ]
    audit = next(command for command in commands if "pip-audit" in command)
    assert "--requirement" in audit, "isolated audit tool must consume project requirements"
    target = audit[audit.index("--requirement") + 1]
    export = next(command for command in commands if command[:2] == ["uv", "export"])
    assert export[export.index("--output-file") + 1] == target
    assert commands.index(export) < commands.index(audit)
    assert "--locked" in export
    assert "--no-deps" in audit
    # Exercise the actual exporter, keeping vulnerability-service access out of tests.
    exported = tmp_path / "requirements.txt"
    export[export.index("--output-file") + 1] = str(exported)
    completed = subprocess.run(
        [*export, "--offline", "--quiet"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    requirements = exported.read_text(encoding="utf-8")
    assert "mesa==" in requirements and "httpx==" in requirements
    assert "pytest==" not in requirements
    assert "-e ." not in requirements


def test_frozen_binary_smoke_exercises_packaged_catalog_and_replay() -> None:
    """Catch native release artifacts that omit or cannot consume city resources."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = workflow["jobs"]["build-native"]["steps"]
    smoke = next(
        step
        for step in steps
        if step.get("name") == "Smoke-test the exact frozen artifact on its builder"
    )
    commands = [
        shlex.split(line) for line in smoke["run"].splitlines() if line.strip().startswith('"$EXE"')
    ]
    assert ["$EXE", "--format", "json", "city-catalog", "list"] in commands
    assert [
        "$EXE",
        "--format",
        "json",
        "city-catalog",
        "show",
        "fictional-grid-v2",
    ] in commands
    assert [
        "$EXE",
        "--format",
        "json",
        "city-run",
        "--city-id",
        "fictional-grid-v2",
        "--output-root",
        "frozen-city-output",
        "--run-id",
        "catalog-frozen",
        "--agents",
        "2",
        "--days",
        "1",
        "--seed",
        "42",
    ] in commands
    assert [
        "$EXE",
        "--format",
        "json",
        "city-replay",
        "frozen-city-output",
        "catalog-frozen",
    ] in commands
