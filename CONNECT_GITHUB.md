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

The repository's CI (`.github/workflows/ci.yml`) enforces exactly what the local
gates enforce. Before pushing, run the same sequence so the push is green:

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
