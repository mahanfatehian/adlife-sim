# Connecting this repository to GitHub

This checkout is already configured with a canonical remote:

```
git remote -v
# origin  https://github.com/mahanfatehian/adlife-sim (fetch/push)
```

If you ever clone the project fresh — a fork, a new machine, a collaborator joining —
use this runbook.

## 1. Create (or fork) the repository

- **Canonical repo owner**: <https://github.com/mahanfatehian/adlife-sim>
- **Forking instead?** Fork it on GitHub, then clone *your* fork; the canonical
  repository stays the upstream you pull specification changes from.

## 2. Add the remote

```
git remote add origin https://github.com/mahanfatehian/adlife-sim.git
git push -u origin main
```

For an SSH remote use `git@github.com:mahanfatehian/adlife-sim.git` instead.

## 3. Verify the toolchain gates the repository expects

Every push to `main` runs the quick checks (`.github/workflows/quick.yml`): lint, types,
shell-script lint (ShellCheck and PSScriptAnalyzer), the wheel smoke, the dependency and
secret scans, and every test file except the two performance benchmarks and the long guided
browser workload (skipped there with `ADLIFE_SKIP_LONG_TESTS=1`), split into six parallel
Linux shards. The full CI (`.github/workflows/ci.yml`) adds Python 3.11 to 3.13, Windows and
macOS, the 85% coverage floor, the performance gates, and the long guided browser workload;
it runs for pull requests, for every release, weekly, and on demand
(**Actions → CI → Run workflow**, or `gh workflow run ci.yml --ref main`).

The shards are balanced by the per-file times in `scripts/ci_test_durations.json`, which no
CI run refreshes: refresh them by hand from all six `junit-*` artifacts of one complete quick
run (`gh run download RUN_ID --pattern 'junit-*'`, then
`uv run python scripts/ci_test_shards.py --measure "junit-*/junit.xml"`; the script expands
the pattern itself, so this works in PowerShell too), and commit the file.
A cancelled run can lack a shard's report.

Before pushing, run the same local gates:

```
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
```

## 4. Branch protection recommendations (owner action)

- Require a pull request for `main`, with the `test` matrix and the packaging job
  as required status checks.
- Require linear history; the project's commit convention is one focused commit per
  task.
- Enable secret scanning and push protection.

## 5. Releases

Releases are tag-driven, not push-driven: a maintainer pushes a signed, annotated
`vX.Y.Z` tag and the release workflow takes over. The full runbook — including the
one-time Trusted Publishing setup — lives in `docs/releasing.md`.
