"""Print one balanced shard of the test files for the quick CI, or refresh its timings.

usage: python scripts/ci_test_shards.py INDEX COUNT        (1 <= INDEX <= COUNT)
       python scripts/ci_test_shards.py --measure JUNIT_XML...

Every test file under ``tests/`` lands in exactly one of the COUNT shards, so the shards
together run the whole suite. Files are packed by their measured run time
(``scripts/ci_test_durations.json``), the slowest first, each into the shard with the least
time so far; a file without a measurement counts as the median. The output is one path per
line, relative to the repository root, ready to pass to pytest.

``--measure`` rewrites that file from pytest JUnit reports, such as the ``junit-*``
artifacts every quick CI run uploads (``gh run download RUN_ID --pattern 'junit-*'``).
"""

from __future__ import annotations

import json
import statistics
import sys
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = Path(__file__).with_name("ci_test_durations.json")
USAGE = (
    "usage: python scripts/ci_test_shards.py INDEX COUNT   (1 <= INDEX <= COUNT)\n"
    "       python scripts/ci_test_shards.py --measure JUNIT_XML..."
)
ABOUT = (
    "Measured pytest seconds per test file on the quick CI's Linux runners. "
    "scripts/ci_test_shards.py packs the test shards with these times; refresh them with "
    "python scripts/ci_test_shards.py --measure on a recent run's junit-* artifacts."
)


def suite_files(root: Path = ROOT) -> list[str]:
    """Every file pytest collects tests from, as sorted repository-relative paths."""
    found = {
        path.relative_to(root).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for path in (root / "tests").rglob(pattern)
    }
    return sorted(found)


def shards(files: list[str], durations: dict[str, float], count: int) -> list[list[str]]:
    """Split ``files`` into ``count`` groups of similar total time, deterministically."""
    known = [durations[name] for name in files if name in durations]
    default = statistics.median(known) if known else 1.0
    weight = {name: durations.get(name, default) for name in files}
    totals = [0.0] * count
    groups: list[list[str]] = [[] for _ in range(count)]
    for name in sorted(files, key=lambda item: (-weight[item], item)):
        lightest = min(range(count), key=lambda index: (totals[index], index))
        totals[lightest] += weight[name]
        groups[lightest].append(name)
    return [sorted(group) for group in groups]


def measured_seconds(reports: list[Path], files: list[str]) -> dict[str, float]:
    """Sum each test file's case times from JUnit reports (``classname`` is dotted)."""
    known = set(files)
    seconds: dict[str, float] = defaultdict(float)
    for report in reports:
        for case in ElementTree.parse(report).iter("testcase"):
            # A module skipped at collection has no class name; its name is the module.
            parts = (case.get("classname") or case.get("name", "")).split(".")
            # A class inside the module adds trailing parts; the longest file prefix wins.
            for end in range(len(parts), 0, -1):
                name = "/".join(parts[:end]) + ".py"
                if name in known:
                    seconds[name] += float(case.get("time") or 0.0)
                    break
    return {name: round(seconds[name], 1) for name in sorted(seconds)}


def main(argv: list[str]) -> int:
    if argv[:1] == ["--measure"] and len(argv) > 1:
        seconds = measured_seconds([Path(value) for value in argv[1:]], suite_files())
        document = {"about": ABOUT, "seconds": seconds}
        text = json.dumps(document, indent=2) + "\n"
        DURATIONS.write_text(text, encoding="utf-8", newline="\n")
        print(f"measured {len(seconds)} test files", file=sys.stderr)
        return 0
    try:
        index, count = (int(value) for value in argv)
    except ValueError:
        index = count = 0
    if not 1 <= index <= count:
        print(USAGE, file=sys.stderr)
        return 2
    durations = json.loads(DURATIONS.read_text(encoding="utf-8"))["seconds"]
    for name in shards(suite_files(), durations, count)[index - 1]:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
