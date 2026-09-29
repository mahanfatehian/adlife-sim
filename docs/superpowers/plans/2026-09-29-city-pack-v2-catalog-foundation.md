# City-Pack V2 and Offline Catalog Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backwards-compatible city-pack v2 with explicit provenance, curved directed road geometry, deterministic local ingestion, saved-run replay, and a verified offline city catalog built only from fictional or rights-reviewed packs.

**Architecture:** Keep the existing `CityPack` v1 and its canonical hash unchanged. Add separate v2 domain and run-manifest types, dispatch at bounded JSON boundaries, and make shared mobility operate on a typed v1-or-v2 union. Catalog and file loading remain adapters; the core contains only immutable domain contracts and deterministic routing. This milestone ships a fictional catalog entry and the qualification machinery, not a claim that any real city has completed legal review.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib `zoneinfo`, Typer, FastAPI, vanilla JavaScript, pytest/Hypothesis, uv.

**Spec:** `docs/superpowers/specs/2026-09-28-production-city-platform-design.md`

## Global Constraints

- Work only on `main`; never modify the preserved `defense-ready` branch.
- Use `uv` for Python, dependency and project commands.
- Keep `src/adlife/core` free of Typer, FastAPI, Textual, SQLite, Plotly and provider adapters.
- Preserve byte-equivalent v1 canonical JSON, fingerprints, mobility frames and saved-run replay.
- New default tests, demos and catalog selection require no network or credentials.
- Only fictional fixtures or packs whose rights review is recorded as complete may be selectable.
- Do not call a pack “licensed”, “qualified”, or navigation-grade merely because it validates.
- Follow RED→GREEN TDD for every behavior change and use the configured 85% coverage floor.

## Review Focus

- JSON version tokens such as `true`, `1.0`, missing values and unknown versions must fail closed without being treated as v1 or v2.
- Curved roads traversed backward must reverse geometry and direction without changing stable road identity.
- Roads that cross visually without sharing a node must never become connected.
- A catalog rename, missing resource or hash mismatch must refuse selection and must not affect replay of a previously frozen run.
- Time-zone/source/license text, geometry and catalog paths must reject controls, credentials, traversal and resource-exhaustion inputs.

---

### Task 1: Versioned city-pack v2 domain contract

**Files:**
- Modify: `src/adlife/core/domain/city.py`
- Modify: `src/adlife/core/domain/__init__.py`
- Modify: `tests/unit/city/test_city_pack.py`
- Modify: `tests/contract/test_schema_versions.py`

**Interfaces:**
- Consumes: existing frozen `DomainModel`, `canonical_json`, v1 `CityNode`, `CityRoad`, and `CityPack`.
- Produces: `CityCoordinate`, `CityBounds`, `CitySource`, `CityRoadV2`, `CityPackV2`, `CityPackDocument`, and `parse_city_pack_json(document: str | bytes) -> CityPackDocument`.

- [ ] **Step 1: Write failing domain tests**

Add tests proving v1 canonical bytes/fingerprint are unchanged; v2 normalizes node, road, direction and omission order; v2 requires an IANA time-zone, exact enclosing bounds, public source/license metadata and at least one known omission; shape points and all numbers are finite/bounded; directions are nonempty and unique; duplicate IDs, missing endpoints, disconnected directed graphs, dateline crossings, unsupported polar geometry, visual-only intersections, bad/boolean/float/unknown versions and extra fields are refused.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/unit/city/test_city_pack.py tests/contract/test_schema_versions.py -q`

Expected: FAIL because v2 models and version dispatch do not exist.

- [ ] **Step 3: Implement the minimal v2 models and parser**

Keep `CityPack` exactly v1. V2 roads carry zero to 64 intermediate WGS84 `shape` points and a canonical tuple of `forward`/`backward` directions. V2 packs retain the existing 10,000-node/20,000-road safety limits, cap total intermediate points at 100,000, refuse latitudes outside ±85 degrees and dateline-crossing segments, and store exact bounds, `time_zone`, source dataset/provider/version/date/hash/URL/license/attribution, and bounded known omissions. Dispatch only on a strict integer schema token.

- [ ] **Step 4: Run domain and architecture tests**

Run: `uv run pytest tests/unit/city/test_city_pack.py tests/contract/test_schema_versions.py tests/architecture/test_core_import_boundary.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adlife/core/domain/city.py src/adlife/core/domain/__init__.py tests/unit/city/test_city_pack.py tests/contract/test_schema_versions.py
git commit -m "feat(city): define versioned city pack geometry"
```

### Task 2: Geometry-aware deterministic mobility and viewer

**Files:**
- Modify: `src/adlife/core/simulation/city_mobility.py`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/static/app.js`
- Modify: `tests/unit/city/test_city_mobility.py`
- Modify: `tests/unit/city/test_city_web.py`
- Modify: `tests/packaging/test_task5_resources.py`

**Interfaces:**
- Consumes: `CityPackDocument`, v2 road directions and intermediate shape from Task 1.
- Produces: v1/v2 `CityMobility`, geometry-aware `shortest_path`, `CityPath.directions`, `CityPath.geometry`, and route JSON containing oriented geometry.

- [ ] **Step 1: Write failing mobility and API tests**

Add tests proving a curved v2 road uses polyline length and places an agent on the curve, backward traversal reverses the line, prohibited directions are absent, deterministic tie-breaking still uses stable road IDs, route JSON contains oriented geometry, `/api/city` exposes v2 metadata, and a known v1 trace digest is unchanged. Add a static browser assertion that road and selected-route drawing consume shape/route geometry rather than endpoint chords.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/unit/city/test_city_mobility.py tests/unit/city/test_city_web.py tests/packaging/test_task5_resources.py -q`

Expected: FAIL because mobility accepts v1 only and the viewer ignores road shapes.

- [ ] **Step 3: Implement geometry-aware routing and rendering**

Build one canonical directed edge per allowed direction, sum Haversine lengths along its oriented polyline, interpolate by cumulative segment distance, and expose route geometry without changing the existing `CityPosition` schema. Preserve the v1 arithmetic path so old traces stay byte-identical. Render v2 road polylines and selected-route geometry in the canvas; presentation remains read-only.

- [ ] **Step 4: Run related mobility, trace and web tests**

Run: `uv run pytest tests/unit/city/test_city_mobility.py tests/unit/city/test_city_trace.py tests/unit/city/test_city_web.py tests/cli/test_city.py tests/packaging/test_task5_resources.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adlife/core/simulation/city_mobility.py src/adlife/city/web.py src/adlife/city/static/app.js tests/unit/city/test_city_mobility.py tests/unit/city/test_city_web.py tests/packaging/test_task5_resources.py
git commit -m "feat(city): route agents along v2 road geometry"
```

### Task 3: Load, save and replay v2 city runs without breaking v1

**Files:**
- Modify: `src/adlife/city/loader.py`
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `src/adlife/city/runs.py`
- Modify: `tests/unit/city/test_city_loader.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/integration/test_city_run_store.py`
- Modify: `tests/integration/test_city_run_replay.py`

**Interfaces:**
- Consumes: `parse_city_pack_json`, `CityPackDocument`, and v2 mobility model identity from Tasks 1–2.
- Produces: `CityRunManifestV2`, `CityRunManifestDocument`, `parse_city_run_manifest_json`, and v1/v2-capable load/save/replay services.

- [ ] **Step 1: Write failing loader and persistence tests**

Add tests for bounded v2 file loading, unchanged bundled v1 loading, v2 manifest/model pairing, v2 create/load/replay equality, v1 saved-run compatibility, canonical frozen v2 bytes, tampered geometry/source/time-zone rejection, manifest/pack version mismatch and unknown manifest versions. Hash every source artifact before and after replay.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/unit/city/test_city_loader.py tests/unit/city/test_city_run_contract.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py -q`

Expected: FAIL because loader and run storage parse v1 types directly.

- [ ] **Step 3: Implement explicit v1/v2 run dispatch**

Keep `CityRunManifest` as schema v1 and add schema v2 with model ID `illustrative-road-mobility-v2` plus `city_schema_version=2`. Select manifest version from the validated pack, canonicalize through version dispatch, and verify model/pack pairing before any publication. Continue freezing city bytes inside each run so future catalogs cannot alter replay.

- [ ] **Step 4: Run all saved-city tests**

Run: `uv run pytest tests/unit/city tests/cli/test_city_run.py tests/cli/test_city_replay.py tests/cli/test_city_view.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adlife/city/loader.py src/adlife/core/domain/city_run.py src/adlife/city/run_store.py src/adlife/city/runs.py tests/unit/city/test_city_loader.py tests/unit/city/test_city_run_contract.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py
git commit -m "feat(city): persist and replay v2 mobility packs"
```

### Task 4: Deterministic v2 local ingestion and quality evidence

**Files:**
- Modify: `src/adlife/city/osm.py`
- Modify: `src/adlife/cli/commands/city_import.py`
- Modify: `tests/unit/city/test_osm_import.py`
- Modify: `tests/cli/test_city_import.py`
- Modify: `docs/city-pilot.md`

**Interfaces:**
- Consumes: `CityPackV2`, `CitySource`, v2 geometry and canonical JSON from Task 1.
- Produces: `convert_overpass_json_v2(...) -> OSMImportV2Result` and `adlife city-import --schema-version 2` with explicit source date/version/time-zone inputs and a bounded quality report.

- [ ] **Step 1: Write failing ingestion tests**

Add tests proving order-independent v2 output, stable segment IDs derived from way/end-node identity, forward/reverse direction mapping, normalized relevant-source hashing, explicit omissions, exact quality counts, strict required provenance, malformed/oversized input refusal, conditional/turn/access refusal, and no network use. Prove inserting an unrelated earlier segment does not rename unchanged segments.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/unit/city/test_osm_import.py tests/cli/test_city_import.py -q`

Expected: FAIL because v2 conversion and options do not exist.

- [ ] **Step 3: Implement v2 conversion behind an explicit flag**

Preserve v1 default output. V2 requires time zone, source date and source version; uses content-normalized relevant OSM input for the source hash; derives stable road IDs from way/source/target IDs; records the importer’s unsupported rules as omissions; emits bounded QA counts; and still refuses semantics it cannot safely model. The command reads only the local input and never downloads tiles, geocoding or OSM data.

- [ ] **Step 4: Run importer, CLI and determinism tests**

Run: `uv run pytest tests/unit/city/test_osm_import.py tests/cli/test_city_import.py tests/golden/test_manifest_fingerprint.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adlife/city/osm.py src/adlife/cli/commands/city_import.py tests/unit/city/test_osm_import.py tests/cli/test_city_import.py docs/city-pilot.md
git commit -m "feat(city): import v2 packs with quality evidence"
```

### Task 5: Verified offline city catalog and selection

**Files:**
- Create: `src/adlife/core/domain/city_catalog.py`
- Modify: `src/adlife/core/domain/__init__.py`
- Create: `src/adlife/city/catalog.py`
- Create: `src/adlife/city/catalog.json`
- Create: `src/adlife/city/catalog/fictional-grid-v2.json`
- Create: `src/adlife/cli/commands/city_catalog.py`
- Modify: `src/adlife/cli/app.py`
- Modify: `src/adlife/cli/commands/city.py`
- Modify: `src/adlife/cli/commands/city_run.py`
- Create: `tests/unit/city/test_city_catalog_contract.py`
- Create: `tests/cli/test_city_catalog.py`
- Modify: `tests/cli/test_city.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Modify: `tests/packaging/test_wheel.py`

**Interfaces:**
- Consumes: v2 pack fingerprint/source metadata, bounded loader, existing CLI output/error contract, and v2 saved runs.
- Produces: strict `CityCatalog`/`CityCatalogEntry`, `load_city_catalog`, `select_catalog_city`, `adlife city-catalog list|show`, and mutually exclusive `--city-id` selection for `city`/`city-run`.

- [ ] **Step 1: Write failing catalog, CLI and packaging tests**

Test stable sorted list/search/show output, exact JSON/JSONL, fictional classification visibility, duplicate IDs, unsafe resource names, missing/renamed pack, hash/ID/schema/source metadata mismatch, unreviewed real pack refusal, offline selection with sockets blocked, mutually exclusive selectors, no silent default on unknown IDs, replay after catalog replacement, and installed-wheel resource presence.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/unit/city/test_city_catalog_contract.py tests/cli/test_city_catalog.py tests/cli/test_city.py tests/cli/test_city_run.py tests/packaging/test_wheel.py -q`

Expected: FAIL because no catalog contract or commands exist.

- [ ] **Step 3: Implement the packaged, content-addressed catalog**

Catalog entries carry stable ID/display name, resource name, immutable pack hash/schema, a separate structured data-origin class (`fictional` or `real-world`), qualification class (`fictional-fixture` or `rights-reviewed`), reviewer role/date when reviewed, coverage, time zone, source date/version, license, attribution and omissions. Fictional qualification requires fictional origin; real-world origin requires a review dated no earlier than source publication and no later than the validation date. Load only package resources or an explicitly supplied test root, validate before selection, and never accept an arbitrary URL. Bundle one clearly fictional v2 grid; no real city is marked reviewed in this milestone.

- [ ] **Step 4: Run catalog, CLI, replay and packaging tests**

Run: `uv run pytest tests/unit/city/test_city_catalog_contract.py tests/cli/test_city_catalog.py tests/cli/test_city.py tests/cli/test_city_run.py tests/integration/test_city_run_replay.py tests/packaging/test_wheel.py tests/packaging/test_resources.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adlife/core/domain/city_catalog.py src/adlife/city/catalog.py src/adlife/city/catalog.json src/adlife/city/catalog/fictional-grid-v2.json src/adlife/cli/commands/city_catalog.py src/adlife/cli/app.py src/adlife/cli/commands/city.py src/adlife/cli/commands/city_run.py tests/unit/city/test_city_catalog_contract.py tests/cli/test_city_catalog.py tests/cli/test_city.py tests/cli/test_city_run.py tests/packaging/test_wheel.py
git commit -m "feat(city): add verified offline city catalog"
```

### Task 6: Governance, public documentation and final verification

**Files:**
- Create: `docs/data/city-source-qualification.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `docs/superpowers/plans/2026-09-29-city-pack-v2-catalog-foundation.md`
- Modify: `tests/packaging/test_documentation.py`

**Interfaces:**
- Consumes: shipped v2/import/catalog behavior and measured verification evidence from Tasks 1–5.
- Produces: an explicit city-source qualification checklist and truthful public contract; B1/B2/B3/B4 remain unchecked wherever human legal review or representative real-city benchmarks are still absent.

- [ ] **Step 1: Write failing documentation contract tests**

Require docs to state that the bundled catalog entry is fictional, catalog selection is offline/content-addressed, v2 preserves geometry/direction/provenance, public tiles/geocoding are not contacted, ODbL/commercial redistribution needs recorded review, and time zone does not make the fixed schedule calendar-accurate.

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `uv run pytest tests/packaging/test_documentation.py -q`

Expected: FAIL until the public contract is documented.

- [ ] **Step 3: Update governance and documentation**

Document jurisdiction/supplier/license/redistribution/retention/tile/geocoder/reviewer fields and explicitly mark real-city qualification pending. Record exact benchmark and verification results without claiming Package B or production readiness is complete.

- [ ] **Step 4: Run final gates**

Run the mandatory roadmap matrix: locked sync, Ruff format/lint, mypy, full pytest, full pytest under hash seeds 0 and 12345, branch coverage, build, exact-wheel smoke, offline doctor, JSON headless demo, v2 import/run/replay/catalog smoke, and `git diff --check`.

Expected: all applicable gates pass; environmental skips/warnings are reported exactly.

- [ ] **Step 5: Commit**

```bash
git add docs tests/packaging/test_documentation.py
git commit -m "docs(city): define v2 data qualification contract"
```

## Execution record — 2026-09-29

The six-task foundation was implemented on `main` in focused commits beginning with
`caefbd8` (v2 domain), `b4be697` (geometry-aware mobility), `32b0cc8` (v2 saved runs),
`36e992c` plus `2ee165a` (bounded ingestion and adversarial access refusal), and
`f07dbfb` (verified offline catalog). Independent review found and closed strict
catalog-version, data-origin, review-date, conditional-access, node-barrier,
resource-bound, surrogate-text, and negative-zero boundary gaps before the final gate.

Final verification on Windows with Python 3.12.11:

- locked environment sync, Ruff formatting/lint and strict mypy passed;
- full suite: `3314 passed, 13 skipped in 445.11s`;
- `PYTHONHASHSEED=0`: `3314 passed, 13 skipped in 410.41s`;
- `PYTHONHASHSEED=12345`: `3314 passed, 13 skipped in 413.69s`;
- branch coverage: `91.15%` (`3314 passed, 13 skipped`), above the 85% floor;
- wheel and source distribution built; the exact wheel passed clean-room smoke outside
  the checkout, including catalog selection, a v2 city run, identical replay, and a
  4,862,319-byte self-contained report;
- the available Windows PyInstaller build and exact frozen executable smoke passed;
- the 30-agent/seven-day rule run completed in 14.60 seconds under the 40-second
  machine-adjusted ceiling, emitted 5,685 events, used 2 MiB traced peak memory, and
  produced a 3,629,056-byte database plus 4,862,947-byte report;
- offline doctor exited 0 with seven of eight checks passing; its only failed check was
  the environmental Windows `cp1252` stdout encoding warning, with `PYTHONUTF8=1`
  documented as the remedy;
- human and JSON headless demos completed offline with 2,042 events, and a fresh v2
  import/run/replay flow produced 1,440 frames and 2,880 positions with identical
  trace hashes.

This closes the technical **foundation milestone**, not Package B or production
readiness. The missing real-city rights decision and representative permitted-data
benchmark remain explicit roadmap gates.
