# City Persona Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the homogeneous city-workbench cohort with a versioned, replay-safe audience editor for bounded fictional personas without changing the deterministic response equations.

**Architecture:** Add schema-2 workbench models and a deterministic persona-to-agent materializer, compile only explicit interests/traits/state into the existing spatial-response input, and freeze descriptive identity in the workbench sidecar. Add CityRun manifest V8 as a sibling of V7, update every version-aware consumer explicitly, then expose verified identities through the vanilla-HTML workbench and saved-run inspector.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, vanilla HTML/CSS/JavaScript, Playwright, pytest, Hypothesis, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-12-city-personas-design.md`

## Global Constraints

- Preserve schema-1/V7 and direct V6 parsing, storage, replay, reports, and study behavior.
- Keep `src/adlife/core` adapter-free and leave the spatial-response model identifier and equations unchanged.
- Accept 1-30 agents, 1-7 days, strict integer versions/seeds/counts, finite numeric values, and exact `persona-NNN` IDs.
- Enforce 3,072 canonical UTF-8 bytes per persona, 98,304 bytes per population, and 131,072 bytes per workbench request.
- Treat name, age, occupation, background, and MBTI as noncausal metadata. Never infer numeric traits, purchase-intention state, mobility, or events from those fields; the simulator has no purchase-transaction model.
- Apply public-text, sensitive-persona, user-text credential, and structural-secret-member refusal before persistence.
- Use test-first red/green cycles, exact assertions, `uv` commands, focused commits, and push accepted commits to `origin/main` under the configured repository identity.

## Review Focus

- Reordering the raw persona-definition array must preserve the canonical population hash and assignments. Changing a persona ID is structural, changes provenance, and may remap agents when canonical ID order changes; the UI must not offer ID renaming.
- Four-byte Unicode near persona, population, and request ceilings must fail before job reservation with value-free errors.
- A rehashed `workbench.json` that does not match compiled response profiles/state must fail independent artifact binding.
- A descriptive-only edit must change provenance while leaving response-input fingerprints and normalized scientific output unchanged.
- V8 must be explicitly supported by every Python union, study source-version literal, browser projection, and report reader; no open-ended future-version acceptance is allowed.

---

### Task 1: Schema-2 Persona Models and Canonical Materialization

**Files:**
- Modify: `src/adlife/city/workbench_input.py`
- Modify: `src/adlife/city/workbench_security.py`
- Modify: `tests/unit/city/test_workbench_input.py`
- Modify: `tests/unit/city/test_workbench_security.py`
- Create: `tests/property/test_city_workbench_properties.py`

**Interfaces:**

```python
WorkbenchRunDraftDocument = WorkbenchRunDraft | WorkbenchRunDraftV2
WorkbenchRunInputDocument = WorkbenchRunInput | WorkbenchRunInputV2


def materialize_workbench_population(
    population: WorkbenchPopulationDraftV2,
    *,
    agent_count: int,
) -> NormalizedWorkbenchPopulationV2: ...


def parse_workbench_run_input_json(
    document: str | bytes,
) -> WorkbenchRunInputDocument: ...
```

Add `WorkbenchPersonaDraftV2`, `WorkbenchPopulationDraftV2`, `WorkbenchPersonaAssignmentV2`, `NormalizedWorkbenchPopulationV2`, `WorkbenchRunDraftV2`, `NormalizedWorkbenchRunDraftV2`, and `WorkbenchRunInputV2`.

- [ ] **Step 1: Write failing strict-model tests** for exact versions/types, all 16 MBTI literals, fictional-only identities, ID/count uniqueness, count totals, canonical interest/persona ordering, explicit assignments, non-finite values, bool-as-int refusal, and 1/30-agent boundaries. Parameterize every persona text field through `_validate_public_metadata`, `contains_sensitive_text`, and `contains_user_secret_text`; test nested `contains_user_labelled_secret_member` refusal before persistence.
- [ ] **Step 2: Write failing byte/property tests** for 3,072/98,304/131,072-byte boundaries, four-byte Unicode, raw-array permutation preserving the normalized hash and assignments, persona-ID changes altering provenance and any order-dependent assignments, contiguous assignments, and deterministic `{agent_id, persona_id}` materialization.
- [ ] **Step 3: Run the narrow tests and record the expected missing-interface failures.**

  Run: `uv run pytest tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_security.py tests/property/test_city_workbench_properties.py -q`

- [ ] **Step 4: Implement strict schema-2 models, canonical byte validation, deterministic materialization, and exact schema-version dispatch while leaving schema 1 unchanged.**
- [ ] **Step 5: Re-run the narrow tests, Ruff, and mypy.**

  Run: `uv run pytest tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_security.py tests/property/test_city_workbench_properties.py -q`

  Run: `uv run ruff check src/adlife/city/workbench_input.py src/adlife/city/workbench_security.py tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_security.py tests/property/test_city_workbench_properties.py`

  Run: `uv run mypy src/adlife/city/workbench_input.py`

- [ ] **Step 6: Commit.**

  Commit: `feat(city): add fictional persona input schema`

### Task 2: Bundled Fictional Persona Templates

**Files:**
- Create: `src/adlife/city/persona_templates.json`
- Create: `src/adlife/city/workbench_personas.py`
- Create: `tests/unit/city/test_workbench_personas.py`
- Modify: `tests/packaging/test_resources.py`
- Modify: `tests/packaging/test_wheel.py`

**Interfaces:**
- Add `PersonaTemplateCatalog` with `templates: tuple[WorkbenchPersonaDraftV2, ...]` and `load_persona_template_catalog(*, root: Path | None = None) -> PersonaTemplateCatalog`.
- The catalog contains exactly four validated records with fixed template IDs `persona-001` through `persona-004`, is bounded to 32,768 canonical bytes, and returns templates in stable ID order.
- Templates are fictional starting points only and contain explicit causal values; no numeric value may be inferred from display name, biography, occupation, age, or MBTI.

- [ ] **Step 1: Write failing loader tests** for the exact four-template set, exact resource structure, stable order, uniqueness, text/byte bounds, all causal field bounds, every raw persona string and nested secret label, and immutable return values.
- [ ] **Step 2: Write failing packaging tests** proving the resource is present and loadable from the exact wheel outside the source checkout.
- [ ] **Step 3: Run the focused tests and confirm the resource/loader are absent.**

  Run: `uv run pytest tests/unit/city/test_workbench_personas.py tests/packaging/test_resources.py tests/packaging/test_wheel.py -q`

- [ ] **Step 4: Add a small, diverse, wholly fictional catalog and strict loader.** Each resource record is the exact flat `WorkbenchPersonaDraftV2` shape; the JSON field order and documentation group descriptive fields before explicit causal fields without inventing a competing nested model.
- [ ] **Step 5: Re-run the focused tests and commit.**

  Commit: `feat(city): add fictional persona templates`

### Task 3: Persona Compilation and Scientific Invariance

**Files:**
- Modify: `src/adlife/city/workbench_validation.py`
- Modify: `src/adlife/city/workbench_binding.py`
- Modify: `tests/unit/city/test_workbench_validation.py`
- Create: `tests/unit/city/test_workbench_binding.py`
- Modify: `tests/integration/test_city_response_run.py`

**Interfaces:**

```python
def construct_workbench_run(
    draft: WorkbenchRunDraftDocument,
) -> ValidatedWorkbenchRun: ...

@dataclass(frozen=True, slots=True)
class ValidatedWorkbenchRun:
    workbench_input: WorkbenchRunInputDocument
    ...

def validate_workbench_execution_binding(
    workbench_input: WorkbenchRunInputDocument,
    ...,
) -> WorkbenchRunInputDocument: ...
```

Compile heterogeneous interests/traits/initial state per assigned agent into the existing `SpatialResponseInput`; never add descriptive persona fields to that scientific input.

- [ ] **Step 1: Write failing compiler tests** for exact `person-NNN` coverage, correct persona profile/state compilation, missing/extra/misassigned agent refusal, and unchanged schema-1 homogeneous binding.
- [ ] **Step 2: Write failing invariance tests** comparing inputs that differ only in name, age, occupation, background, and MBTI. Assert identical mobility input, spatial-response input fingerprint, normalized opportunity/attention/response/state streams, and metrics. Add a contrasting explicit trait/interest/state edit test.
- [ ] **Step 3: Run the focused tests and confirm schema 2 is unsupported.**

  Run: `uv run pytest tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_binding.py tests/integration/test_city_response_run.py -q`

- [ ] **Step 4: Implement schema-specific normalization, compilation, and binding while preserving the response model identifier and schema-1 path.**
- [ ] **Step 5: Re-run focused tests, Ruff, and mypy; commit.**

  Commit: `feat(city): compile per-person response assumptions`

### Task 4: Manifest V8, Atomic Storage, and Replay Binding

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/runs.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/unit/city/test_city_run_preparation.py`
- Modify: `tests/unit/city/test_workbench_runs.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/integration/test_city_run_store.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Modify: `tests/integration/test_city_workbench_artifacts.py`

**Interfaces:**
- Add `CityRunManifestV8` as a sibling of V7 based on `_CityRunResponseManifestBase`, with `schema_version: Literal[8]` and `workbench_input_schema_version: Literal[2]`.
- Expand `PreparedCityRun`, `_validated_workbench_input`, `prepare_city_run`, `StoredCityRun.workbench_input`, `_canonical_bytes`, save/load, and `CityRunManifestDocument` with exact V8/schema-2 unions; Task 3 already widened `ValidatedWorkbenchRun.workbench_input`.
- The only legal pairs are workbench input 1 to manifest V7 and workbench input 2 to manifest V8. Add the expected input/run schema to `WorkbenchAcceptedSettings` so completion validation cannot loosely accept either version.

- [ ] **Step 1: Write failing manifest/job tests** for exact integer schema 8, exact input schema 2, the two legal input/manifest pairs, cross-pair refusal, accepted-setting completion checks, inherited receipt/count invariants, unknown-member refusal, and unchanged V1-V7 fixtures.
- [ ] **Step 2: Write failing artifact tests** for canonical V8 round-trip, manifest-last publication, missing/changed/noncanonical/oversized/symlinked input, undeclared sidecars, and rehashed-but-misbound persona assignments.
- [ ] **Step 3: Write failing replay tests** that hash every source artifact before and after an actual `replay_city_run` and prove replay remains identical after bundled template removal or modification.
- [ ] **Step 4: Run the focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/city/test_city_run_contract.py tests/unit/city/test_city_run_preparation.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py -q`

- [ ] **Step 5: Implement exact V8 dispatch and persistence.** Do not inherit V7's schema-1 validator or alter legacy serialization.
- [ ] **Step 6: Re-run the complete focused suite, then replay/artifact tests under two hash seeds.**

  Run: `uv run pytest tests/unit/city/test_city_run_contract.py tests/unit/city/test_city_run_preparation.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py -q`

  Run: `$env:PYTHONHASHSEED='0'; uv run pytest tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py -q`

  Run: `$env:PYTHONHASHSEED='12345'; uv run pytest tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py -q`

- [ ] **Step 7: Run Ruff, mypy, and commit.**

  Run: `uv run ruff check src/adlife/core/domain/city_run.py src/adlife/city/runs.py src/adlife/city/run_store.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py tests/unit/city/test_city_run_contract.py tests/unit/city/test_city_run_preparation.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py`

  Run: `uv run mypy src/adlife/core/domain/city_run.py src/adlife/city/runs.py src/adlife/city/run_store.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py`

  Commit: `feat(city): persist persona runs as manifest v8`

### Task 5: Exact V8 Consumer Ripple

**Files:**
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/city/studies.py`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/cli/commands/city_view.py`
- Modify: `src/adlife/cli/commands/city_metrics.py`
- Modify: `src/adlife/cli/commands/city_compare.py`
- Modify: `src/adlife/core/experiments/_spatial_study_receipts.py`
- Modify: `src/adlife/core/experiments/_spatial_study_validation.py`
- Modify: `src/adlife/core/experiments/spatial_metrics.py`
- Modify: `src/adlife/core/experiments/spatial_response_metrics.py`
- Modify: `src/adlife/core/experiments/spatial_observations.py`
- Modify: `src/adlife/core/experiments/spatial_study.py`
- Modify: `tests/unit/city/test_city_analysis.py`
- Modify: `tests/unit/city/test_spatial_study_contract.py`
- Modify: `tests/unit/experiments/test_spatial_metrics.py`
- Modify: `tests/unit/experiments/test_spatial_response_metrics.py`
- Modify: `tests/unit/experiments/test_spatial_observations.py`
- Modify: `tests/unit/experiments/test_spatial_study_result.py`
- Modify: `tests/integration/test_city_spatial_study.py`
- Modify: `tests/browser/test_spatial_report_browser.py`

**Interfaces:**
- Extend attention provenance to `Literal[5, 6, 7, 8]`.
- Extend response provenance to `Literal[6, 7, 8]`.
- Extend workbench receipt versions to `Literal[1, 2]`.
- Paired studies ignore descriptive identity differences but reject differing causal interests, traits, or initial state.

- [ ] **Step 1: Write failing V8 tests** for metrics, reports, CLI readers, observations, and paired-study acceptance/refusal at the causal boundary. The native experiment validators must accept exact integer 8, reject 9/bool/float, and reconstruct V8 manifests from complete receipts.
- [ ] **Step 2: Run the exact consumer suites and confirm explicit V8 refusal.**

  Run: `uv run pytest tests/unit/city/test_city_analysis.py tests/unit/city/test_spatial_study_contract.py tests/unit/experiments/test_spatial_metrics.py tests/unit/experiments/test_spatial_response_metrics.py tests/unit/experiments/test_spatial_observations.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study.py tests/browser/test_spatial_report_browser.py tests/cli/test_city_view.py tests/cli/test_city_metrics.py tests/cli/test_city_compare.py -q`

- [ ] **Step 3: Add explicit V8 dispatch to every listed consumer** without accepting arbitrary future versions or weakening V6/V7 validation.
- [ ] **Step 4: Re-run the suites plus architecture tests and commit.**

  Run: `uv run pytest tests/unit/city/test_city_analysis.py tests/unit/city/test_spatial_study_contract.py tests/unit/experiments/test_spatial_metrics.py tests/unit/experiments/test_spatial_response_metrics.py tests/unit/experiments/test_spatial_observations.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study.py tests/browser/test_spatial_report_browser.py tests/cli/test_city_view.py tests/cli/test_city_metrics.py tests/cli/test_city_compare.py tests/architecture -q`

  Commit: `feat(city): project persona runs across analysis`

### Task 6: Workbench API and Verified Persona DTOs

**Files:**
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `tests/unit/city/test_workbench_transport.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**

```python
def parse_workbench_http_draft(
    payload: Mapping[str, object],
) -> WorkbenchRunDraftDocument: ...
```

- Preserve `WorkbenchRunInputView` for V7.
- Add `WorkbenchRunInputViewV2`, `WorkbenchRunInputViewDocument: TypeAlias = WorkbenchRunInputView | WorkbenchRunInputViewV2`, and `build_workbench_run_input_view(stored: StoredCityRun) -> WorkbenchRunInputViewDocument`, with V7 to view 1 and V8 to view 2 only.
- V2 exposes `NormalizedWorkbenchPopulationV2`; do not duplicate it as another persona view model.
- Add exact `GET /api/persona-templates`, returning only validated bundled templates.

- [ ] **Step 1: Write failing API tests** for template listing, schema-2 validation/jobs, unchanged-draft fingerprints, value-free adjacent errors including false/non-boolean `fictional`, invalid IDs/counts/bytes, V8 list/detail/input projections, and schema-1 compatibility.
- [ ] **Step 2: Write failing security tests** for every persona string, nested secret member names, fake credentials/contact/ID strings, hostile Persian/bidi/control text, oversized bodies, no reflection, and no workspace reservation on refusal.
- [ ] **Step 3: Run focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/city/test_workbench_personas.py tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_web.py tests/unit/city/test_workbench_jobs.py tests/security/test_workbench_browser_security.py -q`

- [ ] **Step 4: Implement exact dispatch, safe projections, and template endpoint.** Extend `_WORKBENCH_FIELD_COMPONENTS` in `workbench_web.py` with `population`, `personas`, `persona_id`, `count`, `display_name`, `age`, `occupation`, `background`, `mbti`, and `fictional` so errors remain adjacent and value-free. Preserve one active job, synchronous pre-reservation validation, no-clobber staging, and current error envelopes.
- [ ] **Step 5: Re-run focused tests and commit.**

  Commit: `feat(workbench): expose verified fictional personas`

### Task 7: Audience Editor

**Files:**
- Create: `src/adlife/city/static/workbench-personas.js`
- Create: `src/adlife/city/static/workbench-personas.css`
- Modify: `src/adlife/city/static/workbench.html`
- Modify: `src/adlife/city/static/workbench.js`
- Modify: `src/adlife/city/static/workbench.css`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/browser/test_city_workbench_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`
- Modify: `tests/packaging/test_wheel.py`

**Interfaces:**
- Add `window.AdLifePersonaEditor` with deterministic `nextPersonaId`, add, duplicate, remove, edit, serialize, and render operations.
- The server remains the only validation authority; client-side checks are usability aids.

- [ ] **Step 1: Invoke the frontend-design skill** and record a compact design direction that keeps the map/timeline as the visual signature and removes generic AI-dashboard decoration.
- [ ] **Step 2: Write failing Playwright tests** for template selection, smallest-unused ID allocation including template-ID collisions, immutable IDs/no rename control, add/duplicate/remove, refusal to remove the last persona or add a 31st, a usable bounded 30-single-person draft, agent-count changes that never silently alter counts/assignments, count mismatch recovery, causal-field editing, descriptive disclosure, receipt invalidation, job submission, keyboard/focus order, reduced motion, and 390-pixel containment.
- [ ] **Step 3: Write failing hostile-text/CSP/package tests** proving text is rendered inertly, assets make no external requests, and the exact wheel includes and serves both new files.
- [ ] **Step 4: Run the tests and confirm failure.**

  Run: `uv run pytest tests/browser/test_city_workbench_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py -q`

- [ ] **Step 5: Implement the focused audience editor.** Keep causal disclosure adjacent to controls, collapse biographies and technical details by default, and remove redundant helper copy rather than hiding required warnings.
- [ ] **Step 6: Re-run headless browser/security/package tests and commit.**

  Commit: `feat(workbench): add fictional audience editor`

### Task 8: Saved-Run Identity Inspector

**Files:**
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `tests/browser/test_city_places_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**
- Retain the V7 cohort panel.
- For V8, map verified assignments to personas, display the fictional name beside the immutable agent ID, and keep biography/MBTI collapsed with an explicit noncausal label.

- [ ] **Step 1: Write failing inspector tests** for V7 fallback, V8 identity mapping, immutable IDs, missing/mismatched verified assignments, inert hostile text, keyboard disclosures, narrow layouts, and no external requests.
- [ ] **Step 2: Run the browser/security tests and confirm failure.**

  Run: `uv run pytest tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py -q`

- [ ] **Step 3: Implement the verified identity projection and compact inspector UI.** Never read unverified sidecar data in the browser endpoint.
- [ ] **Step 4: Re-run tests and commit.**

  Commit: `feat(city): show verified identities in run inspector`

### Task 9: Documentation, Packaging, and Release Gates

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: `tests/browser/test_city_workbench_browser.py`

- [ ] **Step 1: Write failing docs/smoke assertions** for fictional personas, the MBTI/noncausal boundary, V8 provenance, template availability, and the UI workflow. Make the default browser lifecycle expect V8 while explicit fixtures continue proving V7 compatibility.
- [ ] **Step 2: Update documentation and roadmap evidence** without implying real-person validity, MBTI predictive power, or identity-based numeric inference.
- [ ] **Step 3: Run persona, docs, browser, packaging, lint, and type suites.**

  Run: `uv sync --locked --all-groups`

  Run: `uv run pytest tests/unit/city tests/property/test_city_workbench_properties.py tests/integration/test_city_response_run.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_spatial_study.py tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py -q`

  Run: `uv run ruff format --check .`

  Run: `uv run ruff check .`

  Run: `uv run mypy src`

- [ ] **Step 4: Run full deterministic, coverage, and exact-wheel gates.**

  Run: `uv run pytest -q`

  Run: `$env:PYTHONHASHSEED='0'; uv run pytest -q`

  Run: `$env:PYTHONHASHSEED='12345'; uv run pytest -q`

  Run: `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`

  Run: `uv build --no-sources`

  Run: `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`

  Run: `git diff --check`

- [ ] **Step 5: Commit and push the certified slice.**

  Commit: `docs(city): document fictional persona authoring`
