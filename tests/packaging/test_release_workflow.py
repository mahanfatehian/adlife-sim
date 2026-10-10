"""Validate publication gates using the parsed workflow dependency graph."""

import re
import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def _workflow_triggers(workflow: dict[object, object]) -> dict[str, object]:
    """Return Actions triggers despite PyYAML's YAML 1.1 ``on`` coercion."""
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def test_ci_type_checks_windows_and_posix_platform_apis() -> None:
    """Keep platform-only stdlib attributes valid from every developer OS."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    step = next(
        step for step in workflow["jobs"]["test"]["steps"] if step.get("name") == "Mypy strict"
    )
    commands = [shlex.split(line) for line in step["run"].splitlines() if line.strip()]

    assert ["uv", "run", "mypy", "--platform", "linux", "src"] in commands
    assert ["uv", "run", "mypy", "--platform", "win32", "src"] in commands


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


def test_ci_runs_maximum_spatial_opportunity_performance_outside_coverage() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    coverage = next(
        step for step in steps if step.get("name") == "Test suite with branch coverage (85% floor)"
    )
    performance = next(
        step for step in steps if step.get("name") == "Maximum spatial opportunity performance"
    )

    assert coverage["env"]["ADLIFE_SKIP_LONG_TESTS"] == "1"
    assert performance["run"] == (
        "uv run pytest -q -s tests/integration/test_spatial_opportunity_performance.py"
    )
    ceiling = performance["env"]["ADLIFE_SPATIAL_PERF_CEILING_SECONDS"]
    assert "runner.os == 'Windows'" in ceiling
    assert "'60'" in ceiling


def test_ci_requires_browser_qa_and_lints_the_complete_repository() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    by_name = {step.get("name"): step for step in steps}

    coverage = by_name["Test suite with branch coverage (85% floor)"]
    assert coverage["env"]["ADLIFE_REQUIRE_BROWSER"] == "1"
    assert by_name["Ruff format check"]["run"] == "uv run ruff format --check ."
    assert by_name["Ruff lint"]["run"] == "uv run ruff check ."


def test_release_reuses_the_exact_tagged_ci_before_building() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())

    assert "workflow_call" in _workflow_triggers(ci)
    quality_gate = release["jobs"]["quality-gates"]
    assert quality_gate == {
        "name": "full CI gates for tagged commit",
        "needs": "verify-tag",
        "uses": "./.github/workflows/ci.yml",
    }
    assert set(release["jobs"]["build-native"]["needs"]) == {
        "verify-tag",
        "quality-gates",
    }


def test_release_tag_must_be_the_event_commit_reachable_from_main() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["verify-tag"]["steps"]
        if step.get("name") == "Require an annotated, signed tag"
    )
    script = step["run"]

    assert 'TAG_REF="refs/tags/${GITHUB_REF_NAME}"' in script
    assert 'TAG_COMMIT="$(git rev-parse "${TAG_REF}^{commit}")"' in script
    assert 'EVENT_COMMIT="$(git rev-parse "${GITHUB_SHA}^{commit}")"' in script
    assert '[ "$TAG_COMMIT" = "$EVENT_COMMIT" ]' in script
    assert "+refs/heads/main:refs/remotes/origin/main" in script
    assert 'git merge-base --is-ancestor "$TAG_COMMIT" refs/remotes/origin/main' in script
    assert 'git verify-tag "${TAG_REF}"' in script


def test_release_exports_the_verified_tag_object_and_peeled_commit() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    verify = workflow["jobs"]["verify-tag"]
    step = next(
        step for step in verify["steps"] if step.get("name") == "Require an annotated, signed tag"
    )

    assert step["id"] == "tag"
    assert verify["outputs"]["tag_object_sha"] == "${{ steps.tag.outputs.tag_object_sha }}"
    assert verify["outputs"]["tag_commit_sha"] == "${{ steps.tag.outputs.tag_commit_sha }}"
    assert 'TAG_OBJECT_SHA="$(git rev-parse "${TAG_REF}")"' in step["run"]
    assert 'echo "tag_object_sha=$TAG_OBJECT_SHA" >> "$GITHUB_OUTPUT"' in step["run"]
    assert 'echo "tag_commit_sha=$TAG_COMMIT" >> "$GITHUB_OUTPUT"' in step["run"]
    assert step["run"].index('git verify-tag "${TAG_REF}"') < step["run"].index(
        'echo "tag_object_sha=$TAG_OBJECT_SHA"'
    )


def test_release_assembly_is_read_only_and_draft_creation_is_isolated() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]
    assembly = jobs["assemble-release"]

    assert assembly["permissions"] == {"contents": "read"}
    assembly_checkout = next(
        step for step in assembly["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert assembly_checkout["with"]["persist-credentials"] is False
    assert all("gh release" not in step.get("run", "") for step in assembly["steps"])

    asset_upload = next(
        step
        for step in assembly["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == "verified-release-assets"
    )
    assert asset_upload["with"]["path"].rstrip("/") == "assets"
    assert asset_upload["with"]["if-no-files-found"] == "error"

    draft = jobs["create-draft-release"]
    assert set(draft["needs"]) == {"verify-tag", "assemble-release"}
    assert draft["permissions"] == {"contents": "write"}
    assert len(draft["steps"]) == 3
    draft_checkout = next(
        step for step in draft["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert draft_checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    draft_downloads = [
        step["with"]
        for step in draft["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert draft_downloads == [{"name": "verified-release-assets", "path": "assets/"}]


def test_release_build_and_report_tools_are_exactly_pinned_and_locked() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())

    build_requirements = project["build-system"]["requires"]
    assert len(build_requirements) == 1
    hatchling_requirement = build_requirements[0]
    assert re.fullmatch(r"hatchling==[0-9]+(?:\.[0-9]+)+", hatchling_requirement)
    packaging_requirements = project["dependency-groups"]["packaging"]
    packaging_by_name = {item.partition("==")[0]: item for item in packaging_requirements}
    for package in ("hatchling", "pyinstaller", "cyclonedx-bom", "pip-licenses"):
        assert package in packaging_by_name
        requirement = packaging_by_name[package]
        assert re.fullmatch(rf"{re.escape(package)}==[0-9]+(?:\.[0-9]+)+", requirement)
    assert packaging_by_name["hatchling"] == hatchling_requirement

    steps = workflow["jobs"]["assemble-release"]["steps"]
    install_step = next(
        step for step in steps if step.get("name") == "Install locked release toolchain"
    )
    build_step = next(step for step in steps if step.get("name") == "Build wheel and sdist")
    report_step = next(
        step for step in steps if step.get("name") == "SBOM and third-party license report"
    )
    assert install_step["run"] == "uv sync --locked --all-groups"
    assert steps.index(install_step) < steps.index(build_step)
    assert build_step["run"] == "uv build --no-sources --no-build-isolation"
    script = report_step["run"]
    assert "uv tool run" not in script
    assert "uv run --locked cyclonedx-py" in script
    assert (
        "uv run --locked pip-licenses --format=json --output-file=assets/third-party-licenses.json"
    ) in script
    assert "pip-licenses --python" not in script


def test_release_pins_uv_and_python_for_every_toolchain_setup() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    setup_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]

    assert len(setup_steps) == 2
    uv_versions = [step.get("with", {}).get("version") for step in setup_steps]
    python_versions = [step.get("with", {}).get("python-version") for step in setup_steps]
    assert all(
        isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version)
        for version in uv_versions
    )
    assert len(set(uv_versions)) == 1
    assert all(
        isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version)
        for version in python_versions
    )
    assert len(set(python_versions)) == 1


@pytest.mark.parametrize(
    ("job_name", "step_name", "gh_command"),
    [
        ("create-draft-release", "Create the draft GitHub release", "gh release create"),
        ("publish-release", "Publish the draft release", "gh release edit"),
    ],
)
def test_release_rechecks_remote_tag_identity_at_each_release_mutation(
    job_name: str,
    step_name: str,
    gh_command: str,
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    job = workflow["jobs"][job_name]
    direct_needs = job["needs"]
    assert "verify-tag" in ([direct_needs] if isinstance(direct_needs, str) else direct_needs)

    checkout = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0

    step = next(step for step in job["steps"] if step.get("name") == step_name)
    assert step["env"]["EXPECTED_TAG_OBJECT"] == ("${{ needs.verify-tag.outputs.tag_object_sha }}")
    assert step["env"]["EXPECTED_TAG_COMMIT"] == ("${{ needs.verify-tag.outputs.tag_commit_sha }}")
    script = step["run"]
    operations = [
        'git ls-remote --exit-code --refs origin "$TAG_REF"',
        'git fetch --no-tags origin "+refs/heads/main:refs/remotes/origin/main"',
        'git merge-base --is-ancestor "$EXPECTED_TAG_COMMIT" refs/remotes/origin/main',
        gh_command,
    ]
    positions = [script.index(operation) for operation in operations]
    assert positions == sorted(positions)
    assert '[ "$REMOTE_TAG_OBJECT" = "$EXPECTED_TAG_OBJECT" ]' in script
    assert "--verify-tag" in script[positions[-1] :]


def test_pypi_rechecks_remote_tag_identity_immediately_before_publication() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    job = workflow["jobs"]["publish-pypi"]
    direct_needs = job["needs"]
    assert "verify-tag" in ([direct_needs] if isinstance(direct_needs, str) else direct_needs)

    checkout_steps = [
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkout_steps) == 1, "PyPI provenance recheck requires the tagged repository"
    assert checkout_steps[0]["with"] == {"fetch-depth": 0, "persist-credentials": False}

    publish_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )
    preflight = job["steps"][publish_index - 1]
    assert preflight["name"] == "Recheck release provenance before PyPI publication"
    assert preflight["env"]["EXPECTED_TAG_OBJECT"] == (
        "${{ needs.verify-tag.outputs.tag_object_sha }}"
    )
    assert preflight["env"]["EXPECTED_TAG_COMMIT"] == (
        "${{ needs.verify-tag.outputs.tag_commit_sha }}"
    )
    script = preflight["run"]
    operations = [
        'git ls-remote --exit-code --refs origin "$TAG_REF"',
        'git fetch --no-tags origin "+refs/heads/main:refs/remotes/origin/main"',
        'git merge-base --is-ancestor "$EXPECTED_TAG_COMMIT" refs/remotes/origin/main',
    ]
    positions = [script.index(operation) for operation in operations]
    assert positions == sorted(positions)
    assert '[ "$REMOTE_TAG_OBJECT" = "$EXPECTED_TAG_OBJECT" ]' in script


def test_release_runbook_requires_an_immutable_no_bypass_tag_ruleset() -> None:
    runbook = " ".join((ROOT / "docs/releasing.md").read_text(encoding="utf-8").split())

    assert "active tag ruleset" in runbook
    assert "targeting `v*`" in runbook
    assert "**Restrict updates**" in runbook
    assert "**Restrict deletions**" in runbook
    assert "leave its bypass list empty" in runbook


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
    assert {"verify-tag", "quality-gates", "build-native", "assemble-release"} <= ancestors


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
    # The provenance preflight is the only permitted shell step; no rebuild may
    # replace the distributions that assembly uploaded.
    shell_steps = [step for step in publisher_steps if "run" in step]
    assert [step.get("name") for step in shell_steps] == [
        "Recheck release provenance before PyPI publication"
    ]
    assert all(
        command not in shell_steps[0]["run"]
        for command in ("uv build", "hatch build", "pip wheel", "python -m build")
    )


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
