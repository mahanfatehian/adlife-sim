# Spatial Response and Campaign-State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, deterministic rules-only response and campaign-scoped state
bridge after persisted spatial notices, with schema-v6 replayable artifacts and read-only
timeline inspection.

**Architecture:** A strict companion input freezes fictional profiles, campaign response
assumptions and initial agent/campaign state. A pure core evaluator plans every response
from the immutable start-of-minute snapshot and commits one canonical bounded state update
per agent/campaign/minute. The city adapter persists exact response evidence manifest-last;
CLI and loopback viewer only expose already validated artifacts.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, SHA-256 canonical JSON/JSONL, Typer,
FastAPI, vanilla HTML/CSS/JavaScript, pytest/Hypothesis/Playwright, Ruff, mypy and uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-03-spatial-response-state-design.md`

## Global Constraints

- Keep `src/adlife/core` free of FastAPI, Typer, SQLite, Plotly and provider adapters.
- Preserve exact schema-v1-v5 meanings and ordinary `--spatial-campaign` schema-v5 output.
- Only a validated `spatial.noticed` record may cause a response or state update.
- All state remains campaign-scoped, finite and bounded; same-minute planning is atomic.
- Add no provider call, prose cognition, memory, social propagation, budget mutation,
  purchase event, movement change, authentication, remote data or network requirement.
- Use run model ID `illustrative-road-spatial-response-study-v1`, evaluator model ID
  `spatial-response-v1`, artifact model ID `spatial-response-artifact-v1`, state-document
  model ID `spatial-response-state-v1`, and claim scope
  `synthetic-response-not-observed-behavior`; never conflate these identities.
- Keep publication no-clobber and manifest-last; replay never repairs or mutates source.
- Keep machine stdout exactly parseable and redact/refuse sensitive input text.
- Commit locally after every task; push only because the repository owner explicitly
  authorized pushes for this work.

## Review Focus

- A response input with the right counts but one wrong agent/campaign/creative binding must
  fail before the run ID is reserved.
- Two notices in one minute must read one pre-minute state and commit one commutative update;
  changing source order must not change bytes.
- An ignored impression, malformed causal notice or injected identifier must never create a
  response or arbitrary state transition.
- A storage failure or valid-looking response-artifact edit must leave the run incomplete or
  fail closed without modifying any source byte.
- Schema-v6 viewing/metrics must remain read-only, while schemas v1-v5 neither regress nor
  fabricate response evidence.

---

### Task 1: Define and load the strict spatial-response input

**Files:**
- Create: `src/adlife/core/domain/spatial_response.py`
- Create: `src/adlife/city/response_loader.py`
- Create: `tests/unit/city/test_spatial_response_contract.py`
- Create: `tests/unit/city/test_spatial_response_loader.py`
- Create: `tests/property/test_spatial_response_input_order.py`
- Modify: `tests/architecture/test_core_import_boundary.py`

**Interfaces:**
- Produces: `SpatialResponseTraits`, `SpatialResponseProfile`,
  `SpatialCampaignResponseInput`, `SpatialResponseInitialState`, `SpatialResponseInput`,
  `parse_spatial_response_input_json(document: str | bytes) -> SpatialResponseInput`,
  `validate_spatial_response_input(response_input, scenario, *, agent_ids) -> None`,
  `SpatialResponseInput.fingerprint`, `MAX_SPATIAL_RESPONSE_INPUT_BYTES`,
  `SpatialResponseInputError` and `load_spatial_response_input(...)`.
- Validation consumes a `SpatialCampaignScenario` and exact generated agent IDs; it requires
  city/scenario hashes, campaign IDs/creative hashes, population IDs and the complete
  agent-by-campaign initial-state product to match.

- [ ] **Step 1: Write failing contract and loader tests.** Pin strict version tokens,
  extra-field/nonfinite/bool/duplicate-key refusal, canonical tuple/set ordering, sensitive
  interest refusal, all numeric bounds, fingerprint stability, exact cross-reference and
  Cartesian-product validation, 2 MiB bounded reads, safe diagnostics and input-order/hash-
  seed invariance. Name the production mutation each test catches.
- [ ] **Step 2: Run the focused tests and confirm RED.**
  Run: `uv run pytest -q tests/unit/city/test_spatial_response_contract.py tests/unit/city/test_spatial_response_loader.py tests/property/test_spatial_response_input_order.py`
  Expected: collection/import failure because the response contract is absent.
- [ ] **Step 3: Implement the minimum strict domain and loader contract.** Use frozen,
  extra-forbidden, strict `DomainModel` types, stable frozenset serializers, bounded UTF-8
  reads, canonical SHA-256 and diagnostics that never echo input contents or paths.
- [ ] **Step 4: Run focused, domain-security and architecture tests.**
  Run: `uv run pytest -q tests/unit/city/test_spatial_response_contract.py tests/unit/city/test_spatial_response_loader.py tests/property/test_spatial_response_input_order.py tests/architecture/test_core_import_boundary.py tests/security/test_redaction_corpus.py`
  Expected: all pass.
- [ ] **Step 5: Run Ruff/mypy, commit and push.**
  Commit: `feat(city): define spatial response inputs`

### Task 2: Evaluate atomic rule responses and state updates in the core

**Files:**
- Create: `src/adlife/core/simulation/spatial_response.py`
- Create: `tests/unit/city/test_spatial_response.py`
- Create: `tests/property/test_spatial_response_invariants.py`
- Modify: `src/adlife/core/simulation/__init__.py`

**Interfaces:**
- Consumes: `SpatialResponseInput`, `SpatialCampaignScenario`,
  `SpatialOpportunityEvaluation`, `SpatialAttentionEvaluation` and canonical agent IDs.
- Produces: `SpatialResponseState`, `SpatialRuleResponse`, `SpatialStateUpdated`,
  `SpatialResponseRecord`, `SpatialResponseCounts`, `SpatialResponseEvaluation`,
  `SpatialResponseStateDocument`, `SpatialResponseArtifactSummary`,
  `evaluate_spatial_responses(response_input, scenario, opportunities, attention, *,
  agent_ids) -> SpatialResponseEvaluation`,
  `spatial_response_lines(evaluation) -> Iterator[bytes]`,
  `spatial_response_state_document(evaluation) -> SpatialResponseStateDocument` and
  `summarize_spatial_response_artifact(evaluation) -> SpatialResponseArtifactSummary`.

- [ ] **Step 1: Write failing golden rule and causality tests.** Hand-calculate literal
  Jaccard, affordability, placement/day fatigue, channel recall encoding, value, sentiment,
  recall and intention proxy results. Pin exact causal hashes, one response per notice,
  ignored-impression no-op, state bounds and copy/creative numerical independence.
- [ ] **Step 2: Write failing atomicity/property tests.** Prove two same-minute notices share
  one snapshot and one commutative state update; a next-minute notice intentionally differs;
  campaigns remain isolated; input/notice/agent permutations and hash seeds preserve bytes;
  malformed causes/IDs and tampered records are refused; no output contains budget,
  movement, provider, prose, purchase event or arbitrary text fields.
- [ ] **Step 3: Run focused tests and confirm RED.**
  Run: `uv run pytest -q tests/unit/city/test_spatial_response.py tests/property/test_spatial_response_invariants.py`
  Expected: collection/import failure because the evaluator is absent.
- [ ] **Step 4: Implement the pure evaluator and artifact projections.** Plan a complete
  minute from immutable state, validate all groups before updating the working map, use
  canonical causal ordering, saturating commutative recall, exact 2 GiB streaming bound
  and the formulas fixed by the spec.
- [ ] **Step 5: Run response, opportunity, attention, property and architecture suites.**
  Run: `uv run pytest -q tests/unit/city/test_spatial_response.py tests/property/test_spatial_response_invariants.py tests/unit/city/test_spatial_opportunity.py tests/unit/city/test_spatial_attention.py tests/architecture/test_core_import_boundary.py`
  Expected: all pass.
- [ ] **Step 6: Run Ruff/mypy, commit and push.**
  Commit: `feat(city): evaluate spatial campaign responses`

### Task 3: Persist and replay schema-v6 response artifacts

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `src/adlife/city/runs.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/integration/test_city_run_store.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Create: `tests/integration/test_city_response_run.py`

**Interfaces:**
- Produces: standalone `CityRunManifestV6` and optional `response_input` /
  `response_evaluation` on `StoredCityRun`.
- `CityRunManifestV6` adds `spatial_response_schema_version`,
  `spatial_response_model_id`, `response_input_sha256`, `response_stream_sha256`,
  `response_state_sha256`, `response_summary_sha256`, `response_stream_bytes`,
  `response_count`, `state_update_count`, `response_campaign_count` and
  `final_state_count` to the complete v5 receipt set.
- Persists: `inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
  `outputs/response-state.json` and `outputs/response-summary.json`.
- Extends: `create_city_run(..., spatial_response=...)` and `CityReplayResult` with exact
  response input/model/stream/state/summary hashes, stream bytes and counts.

- [ ] **Step 1: Write failing v6 manifest tests.** Pin strict schema/model/hash/count fields,
  `response_count == noticed_count`, update-count bounds, full final-state projection and
  parser dispatch. Add golden canonical-byte/hash regression evidence for schemas v1-v5.
- [ ] **Step 2: Write failing store/replay tests.** Cover normal and zero-notice v6 runs,
  v5 unchanged without response input, all-or-none inputs before reservation, manifest-last
  publication, duplicate preservation, every missing/changed/appended/reordered/oversized/
  symlinked response artifact, short-write/fault injection at every new file, exact stream
  EOF, fresh-run equality and before/after source hashes on replay.
- [ ] **Step 3: Run focused tests and confirm RED.**
  Run: `uv run pytest -q tests/unit/city/test_city_run_contract.py tests/integration/test_city_response_run.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py`
  Expected: v6 cases fail because schema/store/replay support is absent; legacy cases pass.
- [ ] **Step 4: Implement v6 without changing v1-v5 models.** Independently recompute all
  response evidence before reserving the ID; exclusively write/fsync/verify fixed files;
  publish `run.json` last; load via bounded canonical reads plus incremental stream compare;
  replay from frozen inputs only.
- [ ] **Step 5: Run all city store/replay/security suites and verify old artifact bytes.**
  Run: `uv run pytest -q tests/unit/city/test_city_run_contract.py tests/integration/test_city_response_run.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/security`
  Expected: all pass.
- [ ] **Step 6: Run Ruff/mypy, commit and push.**
  Commit: `feat(city): persist spatial response studies`

### Task 4: Expose explicit CLI activation and keep spatial analysis usable

**Files:**
- Modify: `src/adlife/cli/commands/city_run.py`
- Modify: `src/adlife/cli/commands/city_replay.py`
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/core/experiments/spatial_metrics.py`
- Modify: `src/adlife/cli/commands/city_metrics.py`
- Modify: `src/adlife/cli/commands/city_compare.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/cli/test_city_replay.py`
- Modify: `tests/cli/test_city_metrics.py`
- Modify: `tests/cli/test_city_compare.py`
- Modify: `tests/unit/city/test_city_analysis.py`

**Interfaces:**
- Adds: `city-run --spatial-response FILE`, valid only with `--spatial-campaign`.
- JSON/human create and replay output expose explicit response model/claim/hash/count fields
  only for v6. Existing spatial metrics accept source schema 5 or 6 but remain strictly
  opportunity/attention metrics and comparisons.

- [ ] **Step 1: Write failing CLI and analysis tests.** Cover the flag dependency, schema-v5
  unchanged path, exact v6 JSON/human output, malformed/oversized/mismatched input before
  reservation, safe redacted diagnostics, exit codes 1/2/3/4/130, replay equality, metrics
  and exact-zero A/A comparison on verified v6, and source-byte immutability.
- [ ] **Step 2: Run focused tests and confirm RED.**
  Run: `uv run pytest -q tests/cli/test_city_run.py tests/cli/test_city_replay.py tests/cli/test_city_metrics.py tests/cli/test_city_compare.py tests/unit/city/test_city_analysis.py`
  Expected: response option/output and v6 analysis cases fail.
- [ ] **Step 3: Implement the narrow CLI/analysis surface.** Load and validate before run
  reservation; omit response keys entirely for v1-v5; keep stdout/stderr contracts; pass
  source schema into existing metric provenance without adding response-effect metrics.
- [ ] **Step 4: Run CLI/analysis/error/output suites.**
  Run: `uv run pytest -q tests/cli tests/unit/city/test_city_analysis.py tests/unit/experiments/test_spatial_metrics.py tests/unit/experiments/test_spatial_comparison.py`
  Expected: all pass.
- [ ] **Step 5: Run Ruff/mypy, commit and push.**
  Commit: `feat(cli): expose spatial response studies`

### Task 5: Present response causality in the read-only loopback viewer

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
- Adds prevalidated schema-v6 response evidence to `create_city_app(...)`.
- Adds GET-only `/api/response-summary`, bounded
  `/api/response-events?minute=&agent_id=&offset=&limit=`, and bounded/paginated
  `/api/response-state?agent_id=&offset=&limit=` explicitly labeled as final state.
- Adds a timeline rail labeled `NOTICE -> RULE RESPONSE -> STATE UPDATE`, using safe DOM
  text for direct deltas and before/after synthetic state proxies.

- [ ] **Step 1: Read and apply `frontend-design` before changing the UI.** Preserve the
  established dashboard language and hierarchy; do not redesign unrelated surfaces.
- [ ] **Step 2: Write failing constructor/API tests.** Pin v6 coherence revalidation, exact
  summary/page/filter/order/status/method behavior, v1-v5 404, maximum page 100, unknown
  agent/minute errors, complete zero-response final-state visibility, final-not-scrubbed
  labeling, no path selector, and source immutability.
- [ ] **Step 3: Run API tests and confirm RED.**
  Run: `uv run pytest -q tests/unit/city/test_city_web.py tests/cli/test_city_view.py`
  Expected: response API cases fail because inputs/routes are absent.
- [ ] **Step 4: Implement prevalidated read-only API support.** Build minute indexes at app
  construction; request handlers only slice immutable evidence.
- [ ] **Step 5: Write failing browser tests.** Pin current-minute response/state values,
  selected-agent filtering, pagination disclosure, prominent synthetic/unobserved and
  uncalibrated-proxy language, keyboard/narrow layout, Persian/hostile safe text, no
  `innerHTML`, no external request and no presentation effect on simulation state.
- [ ] **Step 6: Implement the compact response rail and run browser/API suites.**
  Run: `uv run pytest -q tests/unit/city/test_city_web.py tests/cli/test_city_view.py tests/browser/test_city_places_browser.py`
  Expected: all pass.
- [ ] **Step 7: Run Ruff/mypy, inspect desktop/narrow screenshots, commit and push.**
  Commit: `feat(city): present spatial response evidence`

### Task 6: Reconcile public contracts and certify installed behavior

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/specs/2026-09-28-production-city-platform-design.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: this plan with exact completion evidence.

**Interfaces:**
- Produces truthful v5/v6 documentation, a clean-wheel rules-only response run/replay/
  metrics/viewer smoke, and current roadmap/status text. C3 remains open.

- [ ] **Step 1: Write failing executable documentation/smoke assertions.** Pin the option,
  filenames, schema/model/claim IDs, response receipts, v5/v6 distinction, source hash
  immutability, packaged UI resources and explicit exclusion of cognition, memory, social,
  budgets, purchases, calibration and spatial HTML reports.
- [ ] **Step 2: Reconcile public docs and stale status.** Explain formulas, atomic minute
  semantics, campaign-scoped state, limitations and replay receipts; update the production
  design's obsolete starting-state wording, roadmap ledger and changelog without claiming
  C3, C4b, real-city qualification or external validity complete.
- [ ] **Step 3: Extend exact-wheel smoke with a generated fictional response input.** Create
  schema v6, replay it, derive attention-only metrics/A/A, prove all source hashes unchanged
  and verify the packaged response API/UI resources without network access.
- [ ] **Step 4: Run focused packaging/documentation tests.**
  Run: `uv run pytest -q tests/packaging tests/security tests/architecture`
  Expected: all pass.
- [ ] **Step 5: Run the full release-quality matrix.** Run locked sync, Ruff format/lint,
  strict mypy, full pytest, `PYTHONHASHSEED=0`, `PYTHONHASHSEED=12345`, branch coverage,
  build, exact-wheel smoke, offline doctor/demo, response/spatial performance gates and
  `git diff --check`. Run a local frozen build only when supported and available; make no
  claim about unexecuted OS builds.
- [ ] **Step 6: Request a fresh whole-branch review and make one TDD fix pass for every
  critical/important finding.** Record rulings and deferred minors in the execution ledger.
- [ ] **Step 7: Record exact evidence, commit and push.**
  Commit: `docs(city): document spatial response studies`
