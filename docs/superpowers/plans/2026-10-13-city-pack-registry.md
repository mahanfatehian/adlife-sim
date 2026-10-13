# Workspace City-Pack Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the local workbench safely register, select, run, and replay operator-supplied CityPack V2 files while expanding the offline demo with two additional fictional cities and two fictional campaign templates.

**Architecture:** Add an immutable workspace registry with process/thread locking and canonical no-clobber publication, then compose it with one verified packaged-catalog snapshot through a resolver injected into validation and job creation. Add a route-specific 4 MiB upload boundary, preserve frozen-run replay independence, and provide a compact uploader with accurate trust labels.

**Tech Stack:** Python 3.11+ standard library, Pydantic v2, FastAPI/Starlette, vanilla HTML/CSS/JavaScript, Playwright, pytest, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-12-city-pack-registry-design.md`

## Global Constraints

- Browser registration accepts only strict UTF-8 CityPack V2 JSON; CityPack V1 remains a CLI compatibility path.
- Raw upload and canonical stored bytes, including the newline, are each at most 4,194,304 bytes. Other workbench bodies remain capped at 131,072 bytes.
- Store at most 50 immutable workspace registrations. Never overwrite, update, shadow, repair, or skip a corrupt entry.
- Preserve existing `pack_sha256` list and `city_sha256` detail names and all existing replay semantics.
- Workspace entries are always `operator-supplied-unreviewed`; declared source/license metadata is not rights approval.
- Default tests and demos perform no outbound network access; source URLs remain inert text.
- Keep the core adapter-free. Registry, upload, and transport qualifications live under `adlife.city`.
- Use test-first red/green cycles, exact assertions, `uv`, focused commits, and push accepted commits to `origin/main`.

## Review Focus

- A crash around the exclusive hard-link commit must leave another process seeing either no record or one complete canonical record.
- A 51st, unknown, linked, reserved-name, or noncanonical entry must refuse the registry rather than be skipped.
- Missing `Content-Length` remains valid under streamed bounds; duplicate, malformed, mismatched, and oversized lengths refuse.
- Registry corruption blocks catalog and new-run operations, but not app startup, saved-run inspection, or replay.
- Attribution, omissions, and filenames never become executable or reveal a client/server path.

---

### Task 1: Combined Catalog Trust Model

**Files:**
- Modify: `src/adlife/city/catalog.py`
- Create: `src/adlife/city/workbench_catalog.py`
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/unit/city/test_city_catalog_contract.py`
- Create: `tests/unit/city/test_workbench_catalog.py`
- Modify: `tests/unit/city/test_workbench_transport.py`
- Modify: `tests/unit/city/test_workbench_web.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class VerifiedCatalogCity:
    entry: CityCatalogEntry
    pack: CityPackV2


def load_verified_catalog_cities(
    *,
    root: Path | None = None,
) -> tuple[VerifiedCatalogCity, ...]: ...


StorageOrigin = Literal["package", "workspace-upload"]
WorkbenchDataOrigin = Literal["fictional", "real-world", "operator-supplied"]
WorkbenchQualification = Literal[
    "fictional-fixture",
    "rights-reviewed",
    "operator-supplied-unreviewed",
]


@dataclass(frozen=True, slots=True)
class ResolvedWorkbenchCity:
    pack: CityPackV2
    storage_origin: StorageOrigin
    data_origin: WorkbenchDataOrigin
    qualification: WorkbenchQualification
    reviewer_role: str | None
    reviewed_on: str | None


class UnknownWorkbenchCity(ValueError): ...


class WorkbenchCityRegistryUnavailable(RuntimeError): ...


class PackagedCityCatalogUnavailable(RuntimeError): ...
```

- Add explicit `WorkbenchCityCatalogRow` and extend `WorkbenchCityDetail` with `storage_origin`; builders consume `ResolvedWorkbenchCity` and never manufacture a core `CityCatalogEntry` for local data.

- [ ] **Step 1: Write failing contract/transport tests** for one efficient verified packaged snapshot, stable order, path-free DTOs, exact existing hash field names, and only these pairings: package/fictional/fictional-fixture, package/real-world/rights-reviewed, and workspace-upload/operator-supplied/operator-supplied-unreviewed.
- [ ] **Step 2: Run the focused tests and confirm the interfaces are absent.**

  Run: `uv run pytest tests/unit/city/test_city_catalog_contract.py tests/unit/city/test_workbench_catalog.py tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_web.py -q`

- [ ] **Step 3: Implement the snapshot and neutral workbench catalog types in `workbench_catalog.py`.** Keep transport DTOs in `workbench_transport.py`; do not extend `core.domain.city_catalog.Qualification`; preserve response schema version 1 and avoid a catalog/transport import cycle.
- [ ] **Step 4: Re-run tests, Ruff, and mypy; commit.**

  Commit: `feat(city): define combined workbench city catalog`

### Task 2: Immutable Registry and Atomic Publication

**Files:**
- Create: `src/adlife/city/workbench_city_registry.py`
- Modify: `src/adlife/city/workbench_workspace.py`
- Modify: `src/adlife/city/workbench_security.py`
- Create: `tests/unit/city/test_workbench_city_registry.py`
- Create: `tests/integration/test_city_workbench_registry.py`
- Modify: `tests/unit/city/test_workbench_workspace.py`
- Modify: `tests/unit/city/test_workbench_security.py`

**Interfaces:**

```python
class WorkspaceCityRegistry:
    def __init__(self, workspace: WorkbenchWorkspace) -> None: ...
    def discover(self) -> tuple[CityPackV2, ...]: ...
    def register(
        self,
        document: bytes,
        *,
        forbidden_city_ids: frozenset[str],
    ) -> CityPackV2: ...


def strict_json_object(
    data: bytes,
    *,
    max_body_bytes: int = MAX_WORKBENCH_BODY_BYTES,
) -> dict[str, object]: ...
```

Add distinct typed errors: `MalformedCityPackUpload`, `InvalidCityPackUpload`, `CityPackUploadTooLarge`, `CityPackRegistrationConflict`, `WorkspaceCityRegistryError`, and `CityPackPublicationError(potentially_committed: bool)`.

Use one parsing owner: `WorkspaceCityRegistry.register()` receives the raw bounded bytes and calls `strict_json_object(data, max_body_bytes=4_194_304)` exactly once before strict CityPack V2 validation. Exception messages are constant and value-free.

- [ ] **Step 1: Write failing validation/discovery tests** for canonical-model V2 restart round trips, isolation, raw and stored byte bounds, V1/wrong types, duplicate keys, UTF-8 BOM, UTF-16/32, non-object roots, malformed/surrogate input, NaN/Infinity/`1e999`, deep JSON, semantically identical differently formatted input, graph/provenance errors, filename-stem/city-ID mismatch, packaged/local collisions, a 51st entry, reserved names, traversal/drive/UNC forms, links/reparse/nonregular entries, root replacement, and full refusal on unknown/noncanonical registry records.
- [ ] **Step 2: Write failing fault/concurrency tests** for short/zero writes, flush/fsync, validation, hard link, directory sync, post-commit verification, `KeyboardInterrupt` before/after commit, duplicate and different-ID races at capacity across real processes/registry instances, a reader around the commit point, input-independent random staging names, pre-commit cleanup, and unchanged source/existing bytes. Inject a failure between final-link creation and staging unlink; discovery must accept the complete final record and ignore the orphan in the separate staging directory.
- [ ] **Step 3: Run tests and confirm the registry is absent.**

  Run: `uv run pytest tests/unit/city/test_workbench_city_registry.py tests/integration/test_city_workbench_registry.py tests/unit/city/test_workbench_workspace.py tests/unit/city/test_workbench_security.py -q`

- [ ] **Step 4: Implement immutable storage** at `city-packs/<city_id>.json` with separate `city-pack-staging/`, canonical JSON plus one newline, a process-global `RLock` shared by registry instances for the same workspace, and a `.city-pack-registry.lock` file opened and revalidated directly beneath the pinned workspace. Use `fcntl.flock` on POSIX and `msvcrt.locking` on Windows; verify workspace/lock/directory identity, regular type, and symbolic-link/reparse safety on every operation. Do not reject `st_nlink > 1`: a crash after commit may legitimately leave the stage and final names as two hard links.
- [ ] **Step 5: Publish through a preverified flushed/fsynced random staging file and exclusive `os.link` commit.** Staging names contain no submitted ID/text/secret. Never use replace. After the link, revalidate and sync the registry directory where supported, unlink the exact staging name, and sync the staging directory where supported. Any crash, unlink, verification, or sync failure after link creation leaves the final file committed, never rolls it back, and raises `CityPackPublicationError(potentially_committed=True)`; every pre-commit failure removes only the exact staging file.
- [ ] **Step 6: Re-run focused tests, Ruff, and mypy; commit.**

  Commit: `feat(city): add immutable workspace city registry`

### Task 3: Resolver-Bound Validation and Queued Jobs

**Files:**
- Modify: `src/adlife/city/workbench_city_registry.py`
- Modify: `src/adlife/city/workbench_catalog.py`
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_validation.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `tests/unit/city/test_workbench_city_registry.py`
- Modify: `tests/unit/city/test_workbench_validation.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`

**Interfaces:**

```python
class WorkbenchCityResolver:
    def __init__(self, workspace: WorkbenchWorkspace) -> None: ...
    def list_cities(self) -> tuple[ResolvedWorkbenchCity, ...]: ...
    def resolve_city(self, city_id: str) -> ResolvedWorkbenchCity: ...
    def register_city(self, document: bytes) -> ResolvedWorkbenchCity: ...


def construct_workbench_run(
    draft: WorkbenchRunDraft,
    *,
    selected_pack: CityPackV2 | None = None,
) -> ValidatedWorkbenchRun: ...
```

- [ ] **Step 1: Write failing resolver/transport/job tests** for deterministic combined order, packaged/local collisions, local classifications, exact row/detail DTOs, one lookup per acceptance, exact instance/fingerprint forwarding, mutation after acceptance, corrupt-local refusal, unknown IDs, and packaged-only default behavior.
- [ ] **Step 2: Prove a corrupt registry blocks list/detail/validation/new jobs but not app startup, saved-run listing, inspection, or replay.**
- [ ] **Step 3: Run the focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/city/test_workbench_city_registry.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_web.py tests/unit/city/test_workbench_jobs.py -q`

- [ ] **Step 4: Instantiate one lazy resolver in `create_city_workbench_app`.** Resolve once before constructing `ValidatedWorkbenchRun`; carry the exact accepted pack into the queued job. Map unknown city to 404, corrupt/unsafe workspace registry to 409, and packaged-catalog corruption to 500 for list, detail, validate, and submit; startup, saved-run list/inspect, and replay remain available.
- [ ] **Step 5: Re-run tests and commit.**

  Commit: `feat(city): resolve workspace cities in workbench runs`

### Task 4: Route-Specific Upload Boundary

**Files:**
- Modify: `src/adlife/city/workbench_security.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/unit/city/test_workbench_security.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**

```python
WorkbenchSecurityMiddleware(
    app,
    csrf_token=token,
    max_body_bytes=131_072,
    route_body_limits={"/api/catalog/cities/upload": 4_194_304},
)
```

Add `POST /api/catalog/cities/upload`, require `application/json`, forbid query parameters, and return HTTP 201 with exactly one path-free catalog row.

- [ ] **Step 1: Write failing middleware tests** for the upload-only limit, unchanged 128 KiB limits elsewhere, absent/malformed/duplicate/mismatched lengths, streamed overflow, media/method/query/Host/Origin/CSRF handling, and exact boundary bytes.
- [ ] **Step 2: Write failing upload/security tests** for encoding/JSON/schema failures, canonical overflow, conflicts/capacity/unsafe state, zero pre-commit artifacts, and absence of secrets, raw bodies, client filenames, server paths, and temp names in errors/logs/DTOs. Inject a real ASGI response-send failure after durable registration and prove the HTTP result can be ambiguous while a fresh resolver discovers one complete committed record.
- [ ] **Step 3: Run focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/city/test_workbench_security.py tests/unit/city/test_workbench_web.py tests/security/test_workbench_browser_security.py -q`

- [ ] **Step 4: Implement route-specific buffering and exact mappings:** 400 malformed/ambiguous upload or request metadata, 403 origin/CSRF, 404 unknown city on read routes, 409 collision/capacity/lock/unsafe-or-corrupt workspace registry, 413 raw or canonical size, 422 invalid V2, 500 packaged-catalog corruption, and 500 durable publication failure with a distinct bounded post-commit-uncertainty message.
- [ ] **Step 5: Re-run tests, mypy, and commit.**

  Commit: `feat(city): expose bounded city pack upload`

### Task 5: Frozen Artifact and Replay Independence Certification

**Files:**
- Modify: `tests/integration/test_city_workbench_registry.py`
- Modify: `tests/integration/test_city_workbench_artifacts.py`
- Modify: `tests/integration/test_city_run_replay.py`

- [ ] **Step 1: Add an end-to-end certification test** that uploads, selects, places, validates, runs, inspects, and replays a registered city after restart. Tasks 2-4 should already provide the production behavior, so this task expects no production edit.
- [ ] **Step 2: Hash every source-run artifact; remove/corrupt/replace registry state; replay from frozen input; assert normalized identity and unchanged source hashes.** Corrupt `inputs/city.json` and require hard refusal rather than registry repair.
- [ ] **Step 3: Run the focused tests and require them to pass.**

  Run: `uv run pytest tests/integration/test_city_workbench_registry.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_run_replay.py -q`

- [ ] **Step 4: If the proof fails, stop and return the defect to the owning Task 2, 3, or 4 red/green cycle; do not patch an unspecified production surface here.** No manifest change is required.
- [ ] **Step 5: Commit the high-level proof.**

  Commit: `test(city): prove uploaded city replay independence`

### Task 6: City Upload UI and Copy Simplification

**Files:**
- Create: `src/adlife/city/static/workbench-city-packs.js`
- Modify: `src/adlife/city/static/workbench.html`
- Modify: `src/adlife/city/static/workbench.js`
- Modify: `src/adlife/city/static/workbench.css`
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/browser/test_city_workbench_browser.py`
- Modify: `tests/browser/test_city_places_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`
- Modify: `tests/packaging/test_wheel.py`

- [ ] **Step 1: Invoke the frontend-design skill** and record a compact direction: preserve the map/timeline as the product signature, remove explanatory walls, use plain action labels, and retain rights/scientific warnings adjacent to the affected control or inside deliberate disclosures.
- [ ] **Step 2: Write failing Playwright tests** for upload/select/place/validate/run/inspect, restart durability, authoritative catalog reload, city changes clearing placements/receipts, failed uploads preserving the selected city and complete draft, concise progress/adjacent errors, stale responses, keyboard/focus, reduced motion, and 390-pixel containment. Require detail display for provider, dataset, version, publication date, license, attribution, bounds, time zone, omissions, and pack hash.
- [ ] **Step 3: Write failing security/inspector tests** for Persian/hostile/bidi attribution as inert `textContent` with `dir="auto"`/safe CSS, no filename/path exposure, no `localStorage`/IndexedDB/external fetch/source-URL dereference, CSP, and saved-run access despite registry corruption. The inspector must display only frozen source/license metadata as operator-declared metadata and never reconstruct current registry qualification.
- [ ] **Step 4: Run headless tests and confirm failure.**

  Run: `uv run pytest tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py -q`

- [ ] **Step 5: Implement and explicitly serve/load `workbench-city-packs.js`.** Read with `File.arrayBuffer()`, send same-origin credentials and CSRF with redirect refusal, clear the input immediately, and validate authoritative post-upload detail. Use `textContent`/`createTextNode`, never `innerHTML`.
- [ ] **Step 6: Render only these trust labels:** `Bundled fictional fixture`, `Rights reviewed`, and `Operator supplied - rights not reviewed`.
- [ ] **Step 7: Re-run tests and commit.**

  Commit: `feat(ui): add workspace city pack registration`

### Task 7: Two Cities and Two Campaign Templates

**Files:**
- Create: `src/adlife/city/catalog/fictional-harbor-loop-v2.json`
- Create: `src/adlife/city/catalog/fictional-river-junction-v2.json`
- Modify: `src/adlife/city/catalog.json`
- Modify: `src/adlife/city/creative_templates.json`
- Modify: `tests/unit/city/test_city_catalog_contract.py`
- Modify: `tests/unit/city/test_workbench_input.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/cli/test_city_catalog.py`
- Modify: `tests/packaging/test_resources.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_smoke_release.py`

- [ ] **Step 1: Write failing catalog tests** for three total packaged V2 cities, strict UTF-8 resource parsing, canonical-model fingerprint integrity, distinct bounds/topologies/fingerprints, strong connectivity, exact fictional metadata, catalog issuance/source-date consistency, and wheel inclusion. Do not require byte-canonical packaged resources.
- [ ] **Step 2: Write failing creative tests** for four total fictional templates including `fictional-home-delivery-v1` and `fictional-coffee-pass-v1`, unique bounded safe copy, and the exact synthetic disclosure.
- [ ] **Step 3: Run tests and confirm resources are absent.**

  Run: `uv run pytest tests/unit/city/test_city_catalog_contract.py tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_web.py tests/cli/test_city_catalog.py tests/packaging/test_resources.py tests/packaging/test_wheel.py -q`

- [ ] **Step 4: Author small original synthetic loop and radial graphs.** Declare no traffic, population, land-use, or resident calibration; compute canonical-model fingerprints with repository code, preserve the existing fixture's formatting, and inspect the diff instead of reformatting resources or regenerating unrelated goldens.
- [ ] **Step 5: Add two concise fictional creatives** with no real brands, people, or place claims.
- [ ] **Step 6: Re-run package/smoke tests and commit.**

  Run: `uv run pytest tests/unit/city/test_city_catalog_contract.py tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_web.py tests/cli/test_city_catalog.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py -q`

  Commit: `feat(city): add fictional city and campaign choices`

### Task 8: Documentation and Installed-Wheel Certification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/data/city-source-qualification.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/cli/test_city_import.py`
- Modify: `tests/cli/test_city_workbench.py`

- [ ] **Step 1: Add failing documentation/smoke assertions** for three fictional fixtures, zero rights-reviewed real cities, local V2 upload, 4 MiB versus 128 KiB limits, upload-not-rights-approval, inert source URLs, frozen replay, and no default outbound request.
- [ ] **Step 2: Extend exact-wheel smoke** to register a tiny V2 pack, select it, complete a small run, inspect it, and replay it under the existing network guard.
- [ ] **Step 3: Update public documentation and roadmap evidence.** Keep packaged CLI catalog behavior distinct from workspace uploads.
- [ ] **Step 4: Run focused suites, Ruff, and mypy.**

  Run: `uv sync --locked --all-groups`

  Run: `uv run pytest tests/unit/city/test_workbench_city_registry.py tests/integration/test_city_workbench_registry.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_run_replay.py tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/cli/test_city_catalog.py tests/cli/test_city_import.py tests/cli/test_city_workbench.py tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py -q`

  Run: `uv run ruff format --check .`

  Run: `uv run ruff check .`

  Run: `uv run mypy src`

- [ ] **Step 5: Run repository deterministic, coverage, and exact-wheel gates.**

  Run: `uv run pytest -q`

  Run: `$env:PYTHONHASHSEED='0'; uv run pytest -q`

  Run: `$env:PYTHONHASHSEED='12345'; uv run pytest -q`

  Run: `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`

  Run: `uv build --no-sources`

  Run: `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`

  Run: `uv run adlife doctor --offline`

  Run: `uv run adlife --format json doctor --offline`

  Run: `uv run adlife demo --headless`

  Run: `uv run adlife --format json demo --headless`

  Run: `git diff --check`

- [ ] **Step 6: Commit and push the certified slice.**

  Commit: `docs(city): document workspace city registry`
