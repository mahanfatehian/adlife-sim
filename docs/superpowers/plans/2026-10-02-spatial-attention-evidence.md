# Spatial Attention Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn persisted spatial opportunities into deterministic, replayable synthetic
impression/notice evidence without adding cognition, mutable state or purchase behavior.

**Architecture:** A pure core evaluator produces a strict causal attention stream from
the existing opportunity evaluation and run seed. City-run schema v5 binds and atomically
persists both streams. CLI and read-only FastAPI/vanilla UI expose only validated evidence.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, SHA-256, canonical JSON/JSONL, Typer,
FastAPI, vanilla JavaScript, pytest/Hypothesis, Ruff, mypy and uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-02-spatial-attention-evidence-design.md`

## Global Constraints

- Keep `src/adlife/core` free of FastAPI, Typer, SQLite, Plotly and provider adapters.
- Preserve manifest schemas v1-v4 and their exact meanings.
- Use a content/provider-independent keyed 0.5 notice probability in v1.
- Add no cognition, state, memory, social, budget or purchase transition.
- Publish `run.json` last and refuse any mismatched attention evidence.
- Keep the default workflow offline and all browser endpoints read-only/path-free.

## Review Focus

- Campaign/creative identity changes must not change notice decisions for fixed physical
  opportunities.
- Empty and maximum streams must have coherent bounded hashes/counts without an
  unbounded joined byte string.
- A valid-looking edited/appended attention line must be refused as corruption.
- Legacy v4 opportunity-only runs must load/view/replay without fabricated attention.
- Browser pagination and selection must never reorder, invent or mutate persisted events.

---

### Task 1: Define the pure attention model and canonical artifact evidence

**Files:**
- Create: `src/adlife/core/simulation/spatial_attention.py`
- Create: `tests/unit/city/test_spatial_attention.py`
- Create: `tests/property/test_spatial_attention_order.py`

**Interfaces:**
- Consumes: `SpatialOpportunityEvaluation` and integer run seed.
- Produces: `SpatialImpression`, `SpatialNotice`, `SpatialAttentionCounts`,
  `SpatialAttentionEvaluation`, `evaluate_spatial_attention(...)`,
  `spatial_attention_lines(...)` and `summarize_spatial_attention_artifact(...)`.

- [x] Write golden failing tests for exact causal events, keyed draws, neutral channel
  probability, copy/identity independence, strict bounds and canonical JSONL summary.
- [x] Run focused tests and confirm RED on the missing module.
- [x] Implement the minimal immutable evaluator, validation, iterator and streaming hash.
- [x] Run unit/property/architecture suites, Ruff and mypy.
- [x] Commit and push `feat(city): model spatial attention evidence`.

### Task 2: Freeze schema-v5 identity and persist attention atomically

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/integration/test_city_run_store.py`

**Interfaces:**
- Produces: `CityRunManifestV5`, v5 parser dispatch and
  `StoredCityRun.attention_evaluation`.
- Extends: `CityRunStore.save(..., attention_evaluation=...)` for v5 only.

- [x] Write failing strict manifest and save/load tests, including legacy-v4 behavior.
- [x] Add failing missing/changed/appended/oversized/symlink/fault-injection cases.
- [x] Confirm RED because v5 and attention paths are unsupported.
- [x] Implement exact schema validation, independent recomputation, exclusive streaming
  writes, incremental comparison and final-manifest publication.
- [x] Run manifest/storage/security/architecture suites, Ruff and mypy.
- [x] Commit and push `feat(city): persist spatial attention runs`.

### Task 3: Create, replay and expose v5 through the CLI

**Files:**
- Modify: `src/adlife/city/runs.py`
- Modify: `src/adlife/cli/commands/city_run.py`
- Modify: `src/adlife/cli/commands/city_replay.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/cli/test_city_replay.py`

**Interfaces:**
- Produces: attention provenance fields on `CityReplayResult` and city run/replay output.

- [x] Write failing creation/replay/CLI tests for v5, two-run byte identity, source
  immutability and clean JSON provenance.
- [x] Confirm RED because spatial creation still emits v4.
- [x] Evaluate attention after opportunities, construct v5 and verify it during replay.
- [x] Emit model/claim/hash/bytes/impression/notice evidence in CLI output.
- [x] Run city creation/replay/CLI/hash-seed suites, Ruff and mypy.
- [x] Commit and push `feat(cli): expose spatial attention studies`.

### Task 4: Add read-only attention inspection to the local workbench

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
- Produces: `/api/attention-summary`, paged `/api/attention-events`, and persisted
  current-minute impression/notice panels for schema v5.

- [ ] Write failing API tests for exact order, filtering, pagination, invalid input,
  legacy-v4 absence and read-only behavior.
- [ ] Confirm RED on missing v5 app support/endpoints.
- [ ] Implement prevalidated in-memory indexes and bounded response pages.
- [ ] Write failing browser interaction/accessibility tests for scrub and selection.
- [ ] Add safe-DOM attention totals/cards/map markers and retain CSP/offline behavior.
- [ ] Run API/CLI/browser/TUI suites, Ruff and mypy.
- [ ] Commit and push `feat(city): inspect spatial attention evidence`.

### Task 5: Reconcile public contracts and certify the installed artifact

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
- Produces: truthful C3b public documentation and exact-wheel v5 run/replay smoke.

- [ ] Write failing documentation and smoke assertions for v5 attention provenance.
- [ ] Document modeled attention, fixed neutral probability and all deferred boundaries.
- [ ] Extend clean-room smoke to assert installed v5 creation and replay.
- [ ] Run lock sync, Ruff, mypy, full/hash-seed/coverage suites, build, exact-wheel smoke,
  doctor/demo and `git diff --check`.
- [ ] Record exact evidence; commit and push
  `docs(city): document spatial attention evidence`.
