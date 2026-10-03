# Spatial Metrics and Matched-Run Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic artifact-derived spatial metrics, matched-run deltas and a
read-only metrics view without inventing response or real-world outcome semantics.

**Architecture:** Pure core projections consume already validated opportunity/attention
evaluations and immutable run provenance. A thin city analysis adapter prepares those
inputs from `StoredCityRun`; CLI and FastAPI/vanilla UI serialize the frozen result without
writing artifacts. Pair comparison accepts only matched mobility/population/seed provenance
and labels changed opportunity structure as confounded.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, SHA-256 canonical JSON, Typer, FastAPI,
vanilla JavaScript, pytest/Hypothesis/Playwright, Ruff, mypy and uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-03-spatial-metrics-comparison-design.md`

## Global Constraints

- Keep `src/adlife/core` free of FastAPI, Typer, SQLite, Plotly and provider adapters.
- Preserve manifest schemas v1-v5 and their exact artifact meanings; introduce no schema v6.
- Use only verified opportunity/attention records; add no cognition, state, memory, social,
  budget, movement or purchase transition.
- Zero denominators produce finite `0.0`; every displayed metric carries its receipt.
- A raw delta is never labeled an observed or causal effect.
- Metrics, comparison, API and UI are read-only and path-free after CLI run selection.
- Keep the default workflow offline and preserve clean machine-readable stdout.

## Review Focus

- A valid attention evaluation with an agent absent from the declared population must be
  refused rather than silently lowering the reach denominator.
- An empty evidence stream must return exact finite zero values and stable hashes.
- Same aggregate counts with different event timing/agents must not be called matched
  opportunity structure.
- A pair with valid but different trace/assignment/seed provenance must fail as incompatible,
  not be reported as a treatment delta.
- Metrics/API/UI access must leave every source artifact byte-identical.

---

### Task 1: Derive strict spatial metrics and receipts in the core

**Files:**
- Create: `src/adlife/core/experiments/spatial_metrics.py`
- Modify: `src/adlife/core/experiments/__init__.py`
- Create: `tests/unit/experiments/test_spatial_metrics.py`
- Create: `tests/property/test_spatial_metrics_order.py`

**Interfaces:**
- Consumes: `SpatialOpportunityEvaluation`, `SpatialAttentionEvaluation`, canonical agent
  IDs, assignment/trace hashes and duration.
- Produces: `SpatialMetricReceipt`, `SpatialMetricSeries`, `SpatialMetrics`,
  `derive_spatial_metrics(...)` and `spatial_opportunity_structure_sha256(...)`.

- [x] Write failing golden tests for all overall/channel numerators, denominators, values,
  fixed sources, claim/model identity and exact normalized structure hash.
- [x] Write failing boundary/property tests for empty streams, zero denominators, unknown or
  duplicate agents, mismatched evaluations, tampered causality, input permutation and
  structure changes hidden by equal aggregate counts.
- [x] Run the focused tests and confirm RED because the module/API does not exist.
- [x] Implement frozen extra-forbidden models, canonical structure projection and the pure
  fold. Validate all inputs at the seam and use fixed roadside/mobile ordering.
- [x] Run focused, spatial attention/opportunity, property and architecture suites; run Ruff
  and strict mypy.
- [x] Commit and push `feat(city): derive spatial study metrics`.

### Task 2: Compare matched spatial runs without overstating causality

**Files:**
- Create: `src/adlife/core/experiments/spatial_comparison.py`
- Modify: `src/adlife/core/experiments/__init__.py`
- Create: `tests/unit/experiments/test_spatial_comparison.py`
- Create: `tests/property/test_spatial_comparison_order.py`

**Interfaces:**
- Consumes: two `SpatialMetrics` documents plus exact control/treatment run IDs.
- Produces: `SpatialMetricDeltaSeries`, `SpatialMetricsComparison`,
  `SpatialComparisonError` and `compare_spatial_metrics(...)`.

- [x] Write failing tests proving exact A/A zero, treatment-minus-control signs, canonical
  channel deltas, claim scope and matched/confounded classifications.
- [x] Write failing refusal tests for city, trace, assignment, seed, duration and population
  mismatch; prove equal counts with different structure remains confounded.
- [x] Confirm RED on the missing comparison module.
- [x] Implement the strict pure comparison, preserving both source snapshots and forbidding
  non-finite deltas or causal-effect wording.
- [x] Run focused/property/experiment/architecture suites, Ruff and strict mypy.
- [x] Commit and push `feat(city): compare matched spatial metrics`.

### Task 3: Add the read-only city analysis adapter and CLI commands

**Files:**
- Create: `src/adlife/city/analysis.py`
- Create: `src/adlife/cli/commands/city_metrics.py`
- Create: `src/adlife/cli/commands/city_compare.py`
- Modify: `src/adlife/cli/app.py`
- Create: `tests/unit/city/test_city_analysis.py`
- Create: `tests/cli/test_city_metrics.py`
- Create: `tests/cli/test_city_compare.py`

**Interfaces:**
- Produces: `metrics_for_stored_city_run(stored: StoredCityRun) -> SpatialMetrics` and
  `compare_stored_city_runs(control, treatment) -> SpatialMetricsComparison`.
- Adds: `adlife city-metrics ROOT RUN_ID` and
  `adlife city-compare ROOT CONTROL_RUN_ID TREATMENT_RUN_ID`.

- [x] Write failing adapter tests for v5 projection, legacy refusal and no artifact mutation.
- [x] Write failing CLI tests for exact human/JSON output, invalid IDs, missing/corrupt runs,
  incompatible pairs, A/A zero, confounded labeling and clean stderr/stdout boundaries.
- [x] Confirm RED because the adapter and commands are absent.
- [x] Implement the adapter and register both commands. Map valid comparison incompatibility
  to exit 2 and artifact failures to exit 4 through existing command boundaries.
- [x] Hash source artifacts before and after metrics/comparison in integration tests and
  assert exact equality.
- [x] Run city store/replay/CLI/security/hash-seed suites, Ruff and strict mypy.
- [x] Commit and push `feat(cli): inspect and compare spatial metrics`.

### Task 4: Present metrics through the read-only local viewer

**Files:**
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/cli/commands/city_view.py`
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `tests/unit/city/test_city_web.py`
- Modify: `tests/cli/test_city_view.py`
- Modify: `tests/browser/test_city_places_browser.py`

**Interfaces:**
- Adds optional prevalidated `spatial_metrics` to `create_city_app(...)` and read-only
  `GET /api/spatial-metrics`.
- Adds a safe-DOM metrics panel with overall/channel reach, frequency, notice rate,
  numerator/denominator receipts and the synthetic claim.

- [x] Write failing API tests for exact response, v1-v4 404, method refusal, offline/path-free
  behavior and source immutability.
- [x] Confirm RED on the missing endpoint/app input.
- [x] Implement the prevalidated API response without filesystem access inside requests.
- [x] Write failing browser tests for metric values, receipt labels, disclosure, keyboard
  access, narrow layout, Persian/unicode safety and zero denominators.
- [x] Add the compact panel using text-only safe DOM operations; preserve play/scrub/selection
  as presentation-only behavior and existing CSP/offline operation.
- [x] Run API/CLI/browser/TUI suites, Ruff and strict mypy; inspect desktop/narrow screenshots.
- [x] Commit and push `feat(city): present spatial study metrics`.

### Task 5: Reconcile contracts and certify the installed workflow

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: this plan with exact evidence.

**Interfaces:**
- Produces truthful C4a documentation and exact-wheel metrics/A/A comparison smoke.

- [x] Write failing documentation/smoke assertions for metric receipts, synthetic claims,
  confounding disclosure and the two installed commands.
- [x] Document C4a without marking response, repeated-seed experiments or spatial reports
  complete; retain all calibration/external-validity limitations.
- [x] Extend clean-room smoke to run `city-metrics` and same-run `city-compare`, assert exact
  A/A zero and prove source hashes are unchanged.
- [x] Run locked sync, Ruff, mypy, full/hash-seed/coverage suites, build, exact-wheel smoke,
  doctor/demo, performance gates and `git diff --check`.
- [x] Record exact evidence; commit and push
  `docs(city): document spatial metrics comparison`.

## Completion Evidence — 2026-10-03

- RED/green regression coverage was recorded for each task in
  `.superpowers/sdd/2026-10-03-spatial-metrics-comparison/progress.md`.
- Focused implementation commits: `ff21daa`, `28650a1`, `e4cdbd6`, and `8f12b63`.
- `uv sync --locked --all-groups`: resolved 78 packages; checked 68 packages.
- `uv run ruff format --check .`: 283 files already formatted.
- `uv run ruff check .`: all checks passed.
- `uv run mypy src`: no issues in 117 source files.
- `uv run pytest -q`: 3,592 passed, 18 skipped in 469.60 seconds.
- `PYTHONHASHSEED=0 uv run pytest -q`: 3,592 passed, 18 skipped in 456.01 seconds.
- `PYTHONHASHSEED=12345 uv run pytest -q`: 3,592 passed, 18 skipped in 454.35 seconds.
- Branch coverage: 3,592 passed, 18 skipped; 90.86% total against the 85% floor.
- `uv build --no-sources`: built the sdist and exact `0.1.0` wheel.
- Exact-wheel smoke: installed outside the checkout; produced a 4,862,319-byte report;
  verified city metrics, exact-zero A/A comparison, source-preserving replay, and packaged
  resources.
- Windows x86-64 PyInstaller build completed with PyInstaller 6.22.3. Its exact executable
  passed version, offline doctor, packaged catalog list/show, city run/replay, project init,
  rules run, and a 4,863,286-byte self-contained report smoke.
- Offline CLI: version succeeded; human/JSON doctor completed with 7/8 checks because this
  Windows host advertises `cp1252` stdout; human/JSON headless demos each completed 4,320
  model minutes with 2,042 events and no credentials.
- Maximum 30-agent/7-day rules run: 13.98 seconds, 5,685 events, 2 MiB peak traced memory,
  3,629,056-byte database, and 4,862,947-byte report.
- Maximum spatial phone evaluation: 8.61 seconds for 6,048,000 eligible agent-minutes and
  4,200 retained opportunities.
