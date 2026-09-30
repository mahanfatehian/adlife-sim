# Spatial City Run Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist and replay C2 spatial opportunities as a strict schema-v4 city artifact
without changing legacy city runs or claiming impressions/outcomes.

**Architecture:** The pure C2 evaluator remains the only producer. A new strict manifest
binds frozen inputs and canonical output hashes; the city storage adapter writes fixed
paths and publishes the manifest last. Load/replay reconstruct expected evidence from
frozen inputs and compare the JSONL stream incrementally.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, Typer, SHA-256, canonical JSON/JSONL, pytest,
Hypothesis, Ruff, mypy, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-01-spatial-city-run-artifacts-design.md`

## Global Constraints

- Keep `src/adlife/core` free of Typer, FastAPI, SQLite, Plotly and provider adapters.
- Preserve manifest v1-v3 behavior and existing trace bytes.
- Publish `run.json` only after all fixed-path inputs/outputs verify.
- Bound the opportunity stream at exactly 536,870,912 bytes and 520,800 records.
- Persist only `synthetic-opportunity-not-impression`; add no cognition/state/outcome path.
- Use no network and reflect no untrusted scenario contents in diagnostics.

## Review Focus

- A valid-looking changed JSONL line must be corruption, not accepted or normalized.
- A scenario mismatch must fail before reserving the requested run ID.
- Optional place artifacts must be all present and bound, or all absent.
- A storage failure before manifest publication must remain visibly incomplete.
- Replay must not change timestamps, bytes or directory contents.

---

### Task 1: Freeze the v4 manifest contract

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `tests/unit/city/test_city_run_contract.py`

**Interfaces:**
- Produces: `CityRunManifestV4` and v4 dispatch from
  `parse_city_run_manifest_json(document)`.

- [ ] Add failing strict-model tests for exact versions, hashes, byte/count bounds and
  optional place-field all-or-none validation.
- [ ] Run the focused tests and confirm failure because v4 is unsupported.
- [ ] Implement the minimal immutable v4 model and parser dispatch.
- [ ] Run city-run contract, architecture, Ruff and mypy checks.
- [ ] Commit and push `feat(city): define spatial run manifest`.

### Task 2: Define canonical opportunity artifact evidence

**Files:**
- Modify: `src/adlife/core/simulation/spatial_opportunity.py`
- Modify: `tests/unit/city/test_spatial_opportunity.py`

**Interfaces:**
- Produces: `summarize_spatial_opportunity_artifact(evaluation) ->
  SpatialOpportunityArtifactSummary` and
  `spatial_opportunity_lines(evaluation) -> Iterator[bytes]`.

- [ ] Add failing golden tests for canonical lines, empty SHA-256, byte/count values and
  the 520,800-record model ceiling.
- [ ] Confirm RED on the missing interfaces.
- [ ] Implement a frozen strict summary and iterator without I/O.
- [ ] Run focused/property/performance/architecture tests, Ruff and mypy.
- [ ] Commit and push `feat(city): summarize spatial opportunity artifacts`.

### Task 3: Persist and load schema-v4 runs atomically

**Files:**
- Modify: `src/adlife/city/run_store.py`
- Modify: `tests/integration/test_city_run_store.py`

**Interfaces:**
- Consumes: v4 manifest, canonical line iterator and summary.
- Produces: `StoredCityRun.spatial_scenario` and
  `StoredCityRun.opportunity_evaluation`; optional `spatial_scenario` and
  `opportunity_evaluation` keyword arguments on `CityRunStore.save()`.

- [ ] Add failing save/load tests for zero/mixed streams, fixed layout and legacy
  compatibility.
- [ ] Add failing corruption tests for missing/changed/appended/oversized/symlinked
  scenario, summary and stream artifacts plus optional-place incoherence.
- [ ] Confirm failures are caused by unsupported v4 persistence.
- [ ] Implement preflight validation, exclusive streaming writes, exact incremental reads
  and final-manifest publication.
- [ ] Inject write failures and prove no completed manifest is published; prove duplicate
  save preserves every original byte.
- [ ] Run run-store/security/architecture tests, Ruff and mypy.
- [ ] Commit and push `feat(city): persist spatial opportunity runs`.

### Task 4: Create and replay complete spatial runs

**Files:**
- Modify: `src/adlife/city/runs.py`
- Modify: `tests/integration/test_city_run_replay.py`

**Interfaces:**
- Consumes: `create_city_run(..., spatial_scenario: SpatialCampaignScenario | None)`.
- Produces: opportunity provenance fields on `CityReplayResult`.

- [ ] Add failing creation/replay tests for v4 with and without places, two-run byte
  identity, source hash immutability, and scenario/city/day mismatch before save.
- [ ] Confirm RED because creation ignores spatial scenarios.
- [ ] Build v4 from independently summarized C2 evaluation and pass it to the store.
- [ ] Extend replay to verify scenario, summary and stream provenance without writes.
- [ ] Run city create/replay/store suites, both focused hash seeds, Ruff and mypy.
- [ ] Commit and push `feat(city): replay spatial opportunity runs`.

### Task 5: Expose the bounded CLI workflow

**Files:**
- Modify: `src/adlife/cli/commands/city_run.py`
- Modify: `src/adlife/cli/commands/city_replay.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/cli/test_city_replay.py`

**Interfaces:**
- Produces: `city-run --spatial-campaign PATH` and v4 JSON/human provenance.

- [ ] Add failing CLI tests for successful local/catalog runs, clean JSON, malformed,
  oversized, city-mismatched and duration-mismatched scenarios.
- [ ] Confirm expected exit code 2 and no reserved run for every invalid input.
- [ ] Load the bounded scenario and emit scenario/stream/summary hashes and counts.
- [ ] Extend replay output with the same verified fields.
- [ ] Run all city CLI and error-boundary tests, Ruff and mypy.
- [ ] Commit and push `feat(cli): run persisted spatial studies`.

### Task 6: Documentation, packaging and release evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `tests/packaging/test_documentation.py`
- Modify: this plan with exact evidence.

- [ ] Document C3a creation/replay without claiming full C3, UI, metrics or outcomes.
- [ ] Extend installed-wheel smoke with a schema-v4 run and replay.
- [ ] Run lock sync, Ruff, mypy, full pytest, two hash seeds and branch coverage.
- [ ] Build sdist/wheel and run the exact wheel smoke outside the checkout.
- [ ] Inspect status/diff/check; commit and push
  `docs(city): document spatial run artifacts`.
