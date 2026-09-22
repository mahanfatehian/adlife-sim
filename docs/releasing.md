# Releasing (owner runbook)

How a release actually happens. Everything mechanical is in `.github/workflows/release.yml`;
this document is the human-side checklist.

## Prerequisites (once)

1. **Signing key for tags.** The release workflow verifies the tag signature with
   `git verify-tag`. Register your signing key with GitHub (or configure the runner to
   trust it) before cutting a release; unsigned tags are refused.
2. **Trusted Publishing on PyPI.** Create the `adlife-sim` project on PyPI and add a
   **Trusted Publisher** pointing at this repository and the `release.yml` workflow,
   with the `pypi` GitHub environment as the protected gate. No PyPI token is stored
   anywhere; publication authenticates through OIDC (`id-token: write`).
3. **Protected `pypi` environment.** In repository settings, make the `pypi`
   environment require reviewer approval — publication is then a two-human decision.

## Cutting a release

1. **Bump the version** in `pyproject.toml` and `CITATION.cff` (the documentation test
   keeps them in sync), and add the release section to `CHANGELOG.md`. Land via PR.
2. **Tag, annotated and signed, exactly matching the version:**

   ```bash
   git tag -s v0.1.0 -m "AdLife Lab 0.1.0"
   git push origin v0.1.0
   ```

   A tag `vX.Y.Z` where `pyproject.toml` says anything else stops the pipeline at the
   first job, by design.
3. **Watch the pipeline.** In order:
   - `verify-tag` — annotated + signed + version match;
   - `build-native` — four native frozen builds (never cross-compiled), each one
     smoke-testing **its own exact artifact**: `--version`, `doctor --offline`, a real
     run, and a report;
   - `assemble-release` — gathers assets, writes `SHA256SUMS`, generates the SBOM and
     third-party license report, and creates the **draft** GitHub release with the
     wheel, sdist, and frozen assets attached;
   - `publish-pypi` — Trusted Publishing upload through the protected environment;
   - `publish-release` — only after PyPI succeeds is the draft published.
4. **Flip the README install block.** After the release is public:

   ```bash
   ADLIFE_VERSION=0.1.0 python scripts/configure_repository.py
   git add README.md && git commit -m "docs: pin the installer URL to v0.1.0" && git push
   ```

   The script reads `git remote get-url origin`, validates the GitHub slug, and rewrites
   only the marked block with the tag-pinned `curl | sh` URL — no variables, no example
   accounts. It exits without changing anything when origin is not GitHub.
5. **Verify the release as a user would** (fresh machine or container if possible):
   `uv tool install adlife-sim==0.1.0`, `adlife doctor --offline`, and a SHA256SUMS
   check of a frozen asset.

## What the pipeline refuses

- Unsigned or lightweight tags (`git cat-file -t` must say `tag`).
- Tag/version mismatches.
- Frozen artifacts that fail their own smoke on the builder — a broken build never
  becomes an asset.
- Publication before every artifact exists.

## Hotfix releases

Same flow from the fix's branch: bump version, changelog, signed tag. The pipeline is
identical; there is no fast lane, which is the point.
