# Releasing (owner runbook)

How a release actually happens. Everything mechanical is in `.github/workflows/release.yml`;
this document is the human-side checklist.

## Prerequisites (once)

1. **Pinned signing trust root (required before the first release).** The workflow keeps
   `git verify-tag` fail-closed, but this repository does not yet contain the owner's real
   public signing key and full fingerprint. Publication therefore remains blocked until
   the owner adds that public key, pins its fingerprint in the workflow, and imports it
   into an isolated runner keyring. Do not add a placeholder key. Registering a key with
   GitHub may provide the web "Verified" badge, but it does not provision the runner's
   keyring.
2. **Immutable release-tag ruleset.** In repository settings, create an **active tag
   ruleset** targeting `v*`. Enable both **Restrict updates** and **Restrict deletions**,
   and leave its bypass list empty; repository administrators and automation must not
   receive a broad bypass. This is a required release control, not an optional hardening
   suggestion. The workflow compares the current remote annotated-tag object with the
   object verified at startup immediately before draft creation, PyPI publication, and
   final GitHub publication, then re-fetches `main` and repeats the ancestry check. Those
   shell checks are point-in-time; the no-bypass ruleset is the continuous guarantee that
   the tag cannot move or disappear in the interval before each release mutation. The
   `--verify-tag` option on both `gh release` commands adds an existence check but does not
   replace the object-identity comparison or the ruleset.
3. **Trusted Publishing on PyPI.** Create the `adlife-sim` project on PyPI and add a
   **Trusted Publisher** pointing at this repository and the `release.yml` workflow,
   with the `pypi` GitHub environment as the protected gate. No PyPI token is stored
   anywhere; publication authenticates through OIDC (`id-token: write`).
4. **Protected `pypi` environment.** In repository settings, make the `pypi`
   environment require reviewer approval — publication is then a two-human decision.

## Cutting a release

1. **Bump the version** in `pyproject.toml` and `CITATION.cff` (the documentation test
   keeps them in sync), and add the release section to `CHANGELOG.md`. Land via PR on
   `main`; never tag an unmerged release branch.
2. **Tag, annotated and signed, exactly matching the version:**

   ```bash
   git tag -s v0.1.0 -m "AdLife Lab 0.1.0"
   git push origin v0.1.0
   ```

   A tag `vX.Y.Z` where `pyproject.toml` says anything else, whose annotated-tag object or
   peeled commit differs from the verified identity, or whose commit is not in `main`
   stops the pipeline by design. Do not start a release until the active `v*` tag ruleset
   above has been verified with no bypass actors.
3. **Watch the pipeline.** In order:
   - `verify-tag` — annotated + signed + exact event commit + membership in `main` +
     version match; it exports both the annotated-tag object SHA and peeled commit SHA;
   - `quality-gates` — calls the repository's reusable CI workflow from the tagged
     commit, so the complete matrix must pass again for the exact release source;
   - `build-native` — four native frozen builds (never cross-compiled), each one
     smoke-testing **its own exact artifact**: `--version`, `doctor --offline`, a real
     run, and a report;
   - `assemble-release` — with read-only repository permissions and no persisted checkout
     credential, gathers assets, builds and smoke-tests the wheel, generates the SBOM and
     third-party license report from the exactly pinned, locked packaging toolchain, and
     checksums every release file;
   - `create-draft-release` — a minimal write-scoped job downloads only the verified asset
     handoff, rechecks the remote tag object and current `main` ancestry, and creates the
     **draft** containing the wheel, sdist, frozen assets, both provenance reports, and
     `SHA256SUMS`;
   - `publish-pypi` — after protected-environment approval, rechecks the same remote tag
     object and current `main` ancestry, then uploads the assembled distributions through
     Trusted Publishing;
   - `publish-release` — rechecks the same remote object and ancestry again; only after
     PyPI succeeds is the verified draft published.
4. **Flip the README install block.** After the release is public:

   ```bash
   ADLIFE_VERSION=0.1.0 uv run python scripts/configure_repository.py
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
- Tag refs that moved or disappeared after verification, and commits that are not in
  the freshly fetched `main` immediately before any irreversible publication.
- Tag/version mismatches.
- Any failure in the complete reusable CI workflow for the tagged commit.
- Frozen artifacts that fail their own smoke on the builder — a broken build never
  becomes an asset.
- Publication before every artifact exists.

The provenance reports describe the locked Python build environment, including
development tools; they are not a per-platform inventory of bundled native libraries.
`hatchling`, `PyInstaller`, `cyclonedx-bom`, and `pip-licenses` are exact direct pins for
the release path, while `uv.lock` fixes their complete transitive dependency graph. The
release workflow also pins both `uv` and CPython to exact patch versions on every build
job that installs or executes that toolchain.
The ordinary packaging tests force uv offline and require cached build/install
dependencies. Run the explicit build and clean-wheel smoke gate after dependency
setup to populate that cache; CI retains that separate gate.

## Recovering a partial draft

An asset-upload failure can leave a draft release even though publication remains
blocked. Before retrying the failed workflow, confirm that the release is still a draft
for the expected tag:

```bash
gh release view v0.1.0 --json isDraft,tagName,assets
```

If and only if it is that failed draft, remove the release with
`gh release delete v0.1.0 --yes`, then retry the failed workflow. Never pass
`--cleanup-tag`: the signed, ruleset-protected tag is release provenance and must not be
deleted as draft cleanup.

## Hotfix releases

Land the fix, version bump, and changelog on `main`, then create the signed tag from that
landed commit. The pipeline is identical; there is no branch-only fast lane, which is the
point.
