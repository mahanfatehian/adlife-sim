"""Quick checks lint and shard-test every push to main; the full CI runs for PRs and releases."""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "ci_test_shards.py"
DURATIONS = ROOT / "scripts" / "ci_test_durations.json"


def _workflow(name: str) -> dict[object, object]:
    document = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
    assert isinstance(document, dict)
    return document


def _triggers(workflow: dict[object, object]) -> dict[str, object]:
    """Return Actions triggers despite PyYAML's YAML 1.1 ``on`` coercion."""
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _shard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_test_shards", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _collected_files() -> frozenset[str]:
    """Every file a bare ``pytest`` collects from, as in ci.yml, but without the long-test skip.

    A shard hands pytest explicit files, which bypasses ``testpaths`` and ``python_files``, so
    only pytest's own collection shows a file (or a doctest or plugin item) the shard script
    misses. Dropping ``ADLIFE_SKIP_LONG_TESTS`` collects the modules that skip at import too.
    """
    env = {key: value for key, value in os.environ.items() if key != "ADLIFE_SKIP_LONG_TESTS"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    # One unindented node ID per item; the warning text after them is indented.
    files = frozenset(
        line.split("::", 1)[0]
        for line in result.stdout.splitlines()
        if "::" in line and not line[:1].isspace()
    )
    assert files, "pytest collected nothing"
    return files


def _run_shard(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _quick_shard_count() -> int:
    tests = _workflow("quick.yml")["jobs"]["tests"]  # type: ignore[index]
    shards = tests["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    return len(shards)


@pytest.mark.parametrize("count", [1, 2, 6])
def test_the_shards_run_every_test_file_exactly_once(count: int) -> None:
    printed = [_run_shard(str(index), str(count)) for index in range(1, count + 1)]
    assert all(result.returncode == 0 for result in printed), [r.stderr for r in printed]
    groups = [result.stdout.splitlines() for result in printed]
    every = [name for group in groups for name in group]
    assert all(groups), "no shard may be empty"
    assert len(every) == len(set(every)), "a test file must not run in two shards"
    assert set(every) == set(_shard_module().suite_files())
    # A file pytest collects but no shard lists would only ever run in the full CI.
    missing = _collected_files() - set(every)
    assert not missing, sorted(missing)
    assert all((ROOT / name).is_file() for name in every)


def test_the_shards_are_deterministic_and_balanced_by_measured_time() -> None:
    module = _shard_module()
    count = _quick_shard_count()
    files = module.suite_files()
    durations = json.loads(DURATIONS.read_text(encoding="utf-8"))["seconds"]
    assert module.shards(files, durations, count) == module.shards(files, durations, count)
    assert _run_shard("2", str(count)).stdout == _run_shard("2", str(count)).stdout
    known = [durations[name] for name in files if name in durations]
    assert len(known) >= 0.9 * len(files), "regenerate scripts/ci_test_durations.json"
    default = statistics.median(known)
    weights = {name: durations.get(name, default) for name in files}
    totals = [
        sum(weights[name] for name in group) for group in module.shards(files, durations, count)
    ]
    # Slowest-first packing stays within 4/3 of the ideal split, or one heaviest file.
    assert max(totals) <= max(4 / 3 * sum(totals) / count, max(weights.values())) + 1e-9


@pytest.mark.parametrize("arguments", [(), ("0", "6"), ("7", "6"), ("x", "6"), ("1", "2", "3")])
def test_the_shard_script_refuses_bad_arguments(arguments: tuple[str, ...]) -> None:
    result = _run_shard(*arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr


def test_quick_checks_run_on_every_push_to_main() -> None:
    workflow = _workflow("quick.yml")
    assert _triggers(workflow) == {"push": {"branches": ["main"]}, "workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = {"lint", "tests", "shell-quality", "build", "security"}
    assert set(workflow["jobs"]) == jobs  # type: ignore[arg-type]


def test_quick_checks_lint_type_check_and_test_like_the_full_ci() -> None:
    quick = _workflow("quick.yml")["jobs"]
    full = _workflow("ci.yml")["jobs"]
    full_steps = {step.get("name"): step for step in full["test"]["steps"]}  # type: ignore[index]
    lint_steps = {step.get("name"): step for step in quick["lint"]["steps"]}  # type: ignore[index]
    for name in ("Install locked dependencies", "Ruff format check", "Ruff lint", "Mypy strict"):
        assert lint_steps[name]["run"] == full_steps[name]["run"], name
    # The same pinned checkout and uv setup as the full CI.
    pinned = [step["uses"] for step in full["test"]["steps"][:2]]  # type: ignore[index]
    for job in ("lint", "tests"):
        assert [step["uses"] for step in quick[job]["steps"][:2]] == pinned  # type: ignore[index]
    assert quick["shell-quality"] == full["shell-quality"]  # type: ignore[index]
    assert quick["build"] == full["build"]  # type: ignore[index]
    assert quick["security"] == full["security"]  # type: ignore[index]

    tests = quick["tests"]  # type: ignore[index]
    assert tests["runs-on"] == "ubuntu-latest"
    assert tests["strategy"]["fail-fast"] is False
    count = _quick_shard_count()
    step = next(step for step in tests["steps"] if step.get("name") == "Test shard")
    # A failing shard script stops the step (bash -e) before pytest could run everything.
    assert [line.strip() for line in step["run"].splitlines() if line.strip()] == [
        f"uv run python scripts/ci_test_shards.py ${{{{ matrix.shard }}}} {count} > shard.txt",
        "uv run pytest -q --junitxml=junit.xml $(cat shard.txt)",
    ]
    assert tests["name"] == f"tests (shard ${{{{ matrix.shard }}}} of {count})"
    assert step["env"] == {"ADLIFE_SKIP_LONG_TESTS": "1", "ADLIFE_REQUIRE_BROWSER": "1"}
    assert "--cov" not in step["run"]
    # Each shard keeps its measured times, even when a test fails.
    upload = tests["steps"][tests["steps"].index(step) + 1]
    assert upload["if"] == "always()"
    assert upload["with"] == {
        "name": "junit-${{ matrix.shard }}",
        "path": "junit.xml",
        "retention-days": 7,
    }


def test_the_shard_timings_come_from_junit_reports(tmp_path: Path) -> None:
    module = _shard_module()
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.packaging.test_quick_ci" name="a" time="1.25"/>'
        '<testcase classname="tests.packaging.test_quick_ci" name="b" time="0.5"/>'
        '<testcase classname="tests.unit.city.test_run_story.TestStory" name="c" time="2"/>'
        '<testcase classname="tests.nowhere.test_gone" name="d" time="9"/>'
        '<testcase classname="" name="tests.integration.test_maximum_run" time="0.000"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    files = [
        "tests/integration/test_maximum_run.py",
        "tests/packaging/test_quick_ci.py",
        "tests/unit/city/test_run_story.py",
    ]
    # A module skipped at collection is measured too, at no time.
    assert module.measured_seconds([report], files) == {
        "tests/integration/test_maximum_run.py": 0.0,
        "tests/packaging/test_quick_ci.py": 1.8,
        "tests/unit/city/test_run_story.py": 2.0,
    }


def test_the_full_ci_runs_for_pull_requests_releases_weekly_and_on_demand() -> None:
    triggers = _triggers(_workflow("ci.yml"))
    assert set(triggers) == {"workflow_call", "workflow_dispatch", "pull_request", "schedule"}
    assert "push" not in triggers
    schedule = triggers["schedule"]
    assert isinstance(schedule, list) and len(schedule) == 1
    minute, hour, day, month, weekday = schedule[0]["cron"].split()
    assert (day, month) == ("*", "*") and weekday.isdigit() and minute.isdigit() and hour.isdigit()


def test_full_history_secret_scans_ignore_only_reviewed_fixture_findings() -> None:
    """Manual and weekly runs scan all history; only reviewed fixture findings are ignored."""
    text = (ROOT / ".gitleaksignore").read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert entries == sorted(set(entries))
    for entry in entries:
        commit, path, rule, line = entry.split(":")
        assert len(commit) == 40 and set(commit) <= set("0123456789abcdef"), entry
        assert rule in {"generic-api-key", "jwt"}, entry
        assert line.isdigit(), entry
        # Fake credentials in tests and the secret-pattern examples in person.py docstrings.
        assert path.startswith("tests/") or path == "src/adlife/core/domain/person.py", entry


def test_the_measure_command_expands_report_patterns_in_any_shell(tmp_path: Path) -> None:
    # PowerShell passes "junit-*/junit.xml" through unexpanded, so the script expands it.
    module = _shard_module()
    for shard in (2, 1):
        (tmp_path / f"junit-{shard}").mkdir()
        (tmp_path / f"junit-{shard}" / "junit.xml").write_text("<testsuites/>", encoding="utf-8")
    pattern = str(tmp_path / "junit-*" / "junit.xml")
    assert module.report_paths([pattern]) == [
        tmp_path / "junit-1" / "junit.xml",
        tmp_path / "junit-2" / "junit.xml",
    ]
    plain = str(tmp_path / "junit-1" / "junit.xml")
    assert module.report_paths([plain]) == [Path(plain)]
    with pytest.raises(SystemExit, match="no JUnit report matches"):
        module.report_paths([str(tmp_path / "missing-*" / "junit.xml")])
