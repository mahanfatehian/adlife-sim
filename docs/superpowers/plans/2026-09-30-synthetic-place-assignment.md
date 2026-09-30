# Synthetic Place Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, provenance-explicit synthetic place assignment that is validated, persisted, replayed and visible in the local city workbench.

**Architecture:** Introduce a separate core place-set contract bound to an immutable city-pack hash, then make it an optional input to `CityMobility`. Place-aware runs use manifest v3 and freeze the place set plus generated assignments; CLI/FastAPI/browser layers consume the same core-owned data without adding write paths.

**Tech Stack:** Python 3.11–3.13, Pydantic v2, Typer, FastAPI, vanilla HTML/CSS/JavaScript, uv, pytest/Hypothesis, Ruff, mypy.

**Spec:** [Synthetic place assignment design](../specs/2026-09-30-synthetic-place-assignment-design.md)

## Global Constraints

- Work on `main`; do not change `defense-ready`.
- Keep `src/adlife/core` free of FastAPI, Typer, Textual, SQLite, Plotly and provider adapters.
- Preserve byte-compatible v1/v2 city traces and readable v1/v2 saved runs.
- No network, credentials, real addresses, real-person trajectories or invented calibration.
- Every production behavior follows a witnessed RED test, minimal GREEN implementation and related-suite verification.
- Use canonical JSON, bounded documents, no-clobber storage and exact replay refusal.
- Push each focused commit to the existing `origin/main`; never add or change a remote.

## Review Focus

- A place set names the right city ID but the wrong content hash: refuse before assignment or persistence (Tasks 1–4).
- Duplicate role/node entries silently weight one destination: schema validation refuses them (Task 1).
- Permuted place JSON changes assignments: canonicalization and keyed-selection tests prove equality (Task 2).
- A saved place document or assignment is edited after publication: load/replay refuses corruption (Task 3).
- The browser renders a malicious label or requests external resources: metadata validation plus text-only/no-network browser checks refuse or contain it (Tasks 1 and 5).

---

### Task 1: Versioned place-set contract and bounded loader

**Files:**
- Create: `src/adlife/core/domain/city_places.py`
- Modify: `src/adlife/core/domain/__init__.py`
- Create: `src/adlife/city/place_loader.py`
- Create: `tests/unit/city/test_city_places.py`
- Create: `tests/unit/city/test_place_loader.py`
- Modify: `tests/contract/test_schema_versions.py`

**Interfaces:**
- Produces: `CityPlace`, `CityPlaceProvenance`, `CityPlaceSet`, `parse_city_place_set_json`, `load_city_place_set(path)`, `MAX_CITY_PLACE_SET_BYTES`.
- Consumes: existing `DomainModel`, canonical JSON and city public-metadata policy.

- [ ] Write failing tests for strict versions/types, duplicate keys, provenance/reference rules, public metadata, unique IDs, duplicate role/node weighting, required roles, canonical order and fingerprint stability.
- [ ] Run the narrow tests and confirm failures are caused by missing place contracts.
- [ ] Implement the minimal immutable Pydantic models and strict parser.
- [ ] Write and witness failing bounded-loader tests for UTF-8, oversize, invalid schema and redacted diagnostics.
- [ ] Implement the bounded local loader and exports.
- [ ] Run the two unit files and schema contract tests.
- [ ] Commit and push `feat(city): define versioned synthetic place sets`.

### Task 2: Deterministic place-aware mobility

**Files:**
- Modify: `src/adlife/core/simulation/city_mobility.py`
- Modify: `src/adlife/core/simulation/__init__.py`
- Modify: `tests/unit/city/test_city_mobility.py`
- Create: `tests/property/test_city_place_assignment.py`

**Interfaces:**
- Consumes: `CityPlaceSet` from Task 1.
- Produces: `CityPlaceAssignment`, optional `places` argument on `CityMobility`, `place_assignments`, `place_assignment_document()` and place-aware metadata.

- [ ] Write failing tests for city/hash binding, unknown nodes, unique-home capacity, non-home destinations, role/node correspondence, route construction and place-order invariance.
- [ ] Run the narrow tests and confirm the expected missing-interface failures.
- [ ] Implement keyed canonical assignment and route all selected nodes through the existing directed graph.
- [ ] Add property coverage for place-order permutations and repeated seeds.
- [ ] Re-run mobility/property tests and the v1/v2 trace compatibility assertion.
- [ ] Commit and push `feat(city): assign synthetic places deterministically`.

### Task 3: Manifest v3, atomic storage and replay

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/runs.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/integration/test_city_run_store.py`
- Modify: `tests/integration/test_city_run_replay.py`

**Interfaces:**
- Consumes: place-aware `CityMobility` and place fingerprints/assignment documents.
- Produces: `CityRunManifestV3`, optional `places` on `create_city_run`, and v3 load/replay support.

- [ ] Write failing manifest tests for exact v3 schema/model/place hashes and v1/v2 compatibility.
- [ ] Implement strict v3 dispatch and count validation.
- [ ] Write failing storage/replay tests for frozen place bytes, assignment bytes, corruption, missing documents, schema mismatch, no-clobber and source immutability.
- [ ] Extend publication/load verification with `places.json` and `place-assignments.json`, keeping the final manifest publish order.
- [ ] Run city contract, store and replay suites.
- [ ] Commit and push `feat(city): persist place-aware city runs`.

### Task 4: CLI validation and place-aware run/view flows

**Files:**
- Create: `src/adlife/cli/commands/city_places.py`
- Modify: `src/adlife/cli/app.py`
- Modify: `src/adlife/cli/commands/city.py`
- Modify: `src/adlife/cli/commands/city_run.py`
- Create: `tests/cli/test_city_places.py`
- Modify: `tests/cli/test_city.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/cli/test_city_replay.py`
- Modify: `tests/cli/test_json_output.py`

**Interfaces:**
- Consumes: loaders, `CityMobility`, v3 create/load/replay.
- Produces: `city-places validate`, `city --places`, `city-run --places` and exact JSON results.

- [ ] Write failing command tests for successful validation, pack/catalog selection, invalid combinations, wrong binding, insufficient homes, JSON cleanliness and v3 run/replay.
- [ ] Register the command group and load place inputs before constructing mobility or saving runs.
- [ ] Re-run all city CLI suites and root/help contract tests.
- [ ] Commit and push `feat(cli): add synthetic place workflows`.

### Task 5: Read-only API and browser place inspection

**Files:**
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `tests/unit/city/test_city_web.py`
- Modify: `tests/cli/test_city_view.py`
- Create: `tests/browser/test_city_places.py`

**Interfaces:**
- Consumes: place-aware mobility from Tasks 2–4.
- Produces: `/api/places`, `/api/place-assignments`, map markers, selected itinerary labels and provenance disclosure.

- [ ] Write failing API tests for exact documents, explicit no-place 404s and immutable saved-run behavior.
- [ ] Implement the two read-only endpoints and metadata fields.
- [ ] Write DOM/browser assertions for marker legend, selected place labels, provenance, timeline scrub, no console errors and no external requests.
- [ ] Extend the established urban-console design with three accessible place glyphs and compact labels; do not add a framework or external asset.
- [ ] Run API tests, static-resource packaging tests and the browser smoke at desktop and narrow widths.
- [ ] Commit and push `feat(city): inspect synthetic places in the viewer`.

### Task 6: Documentation, packaging and release gates

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: packaging/resource tests as required by the installed-wheel flow.

**Interfaces:**
- Consumes: the complete place-aware behavior.
- Produces: honest public contract, installed-wheel example and recorded verification evidence.

- [ ] Add failing documentation/smoke expectations for the new CLI and packaged fictional fixture or generated test input.
- [ ] Update public documentation without claiming real residents, calibrated schedules or worldwide catalog coverage; mark only B6 complete.
- [ ] Run narrow docs/package tests, Ruff format/lint, strict mypy and the full pytest suite.
- [ ] Run `PYTHONHASHSEED=0` and `PYTHONHASHSEED=12345` full suites, branch coverage, wheel build and exact clean-room wheel smoke.
- [ ] Run offline doctor/demo plus a fresh place-aware city run/replay/view API and real browser smoke.
- [ ] Record exact evidence in this plan and the roadmap.
- [ ] Commit and push `docs(city): document place-aware mobility evidence`.

## Self-review record

- Spec coverage: place validation, origin disclosure, analyst constraints, keyed assignment, routability, persistence, replay, CLI and viewer are each owned by a task.
- Interface scan: Task 1's place types feed Task 2; Task 2's assignments feed Task 3; Task 3's optional place run feeds Tasks 4–5; Task 6 consumes only shipped interfaces.
- Compatibility: legacy traces and v1/v2 manifests have explicit regression gates.
- Review focus: all five high-risk conditions have an owning test step.
- Proportion: the plan fixes public signatures and evidence while leaving implementation bodies to TDD.
