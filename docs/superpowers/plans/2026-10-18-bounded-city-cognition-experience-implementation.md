# Bounded City Cognition Experience and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present, analyze, compare, study, report, package, and demonstrate manifest-V10 bounded cognition without mixing provider-dependent exploratory evidence with rule-owned purchase or campaign-effect claims.

**Architecture:** This plan starts only after the V10 core and runtime/storage plans are green. It adds version-2 metric and study projections rather than widening V1 response contracts, dispatches read-only API and CLI views by exact manifest version, and keeps all browser rendering downstream of fully verified saved artifacts. The workbench chooses schema 3/V9 for explanation-only operation and schema 4/V10 for bounded signals; the saved-run viewer displays the deterministic baseline, bounded signal, frozen mapping, and dual-state commit as distinct evidence.

**Tech Stack:** Python 3.11-3.13, Pydantic, FastAPI/Starlette, Typer, Jinja2, vanilla HTML/CSS/JavaScript, Playwright, PyInstaller, Hatchling, uv, pytest, Hypothesis, Ruff, and strict mypy.

**Spec:** `docs/superpowers/specs/2026-10-18-bounded-city-cognition-design.md`

**Depends on:**

- `docs/superpowers/plans/2026-10-18-bounded-city-cognition-core-implementation.md`
- `docs/superpowers/plans/2026-10-18-bounded-city-cognition-runtime-implementation.md`

## Global Constraints

- Begin this plan only after every core-plan and runtime-plan task is green and pushed to the existing `origin/main`.
- Work only on `main`; do not modify the preserved `defense-ready` branch.
- Use `uv` for every Python, dependency, test, build, and application command.
- Before the first production-code step in each task, run both its representative red command and its later exact focused-green-suite command against the red tree. Account for every newly added behavior test: each must collect and fail for the intended missing behavior, while pre-existing regressions remain green. For a not-yet-created module or symbol, use `importlib.import_module`/`getattr` inside the named test so the node runs; a collection error is not acceptable red evidence. Characterization-only evidence tests are explicitly labelled and may start green, but cannot authorize untested behavior changes.
- Keep `src/adlife/core` adapter-free. It must not import FastAPI, Typer, Jinja2, SQLite, Plotly, HTTP clients, provider adapters, cache adapters, credential stores, or browser code.
- Keep V1 response metrics/comparison/study models, persisted artifacts, version-specific JSON/CLI shapes, and saved-run evidence semantics behavior-compatible for manifests V1-V9. Shared discovery/capability responses may gain explicitly tested additive V10 members, and shared HTML/CSS/JavaScript may gain V10 controls and accessibility styling, but neither may reinterpret a V1 member, V1 route, or V1 run serialization. V10 uses explicit version-2 types and exact dispatch.
- Purchase intention appears only in the rule-baseline family and is always labelled an uncalibrated proxy. No provider-dependent cognition document may contain a purchase field or campaign-effect delta.
- Provider-dependent cognition is exploratory evidence. Studies may calculate paired statistics only from rule-owned baseline observations.
- Never expose or persist credentials, authorization headers, URL userinfo, raw provider bodies, repair bodies, prompts, chain-of-thought, cache paths, absolute/source-checkout paths, or secret-shaped nested values in metrics, studies, reports, API DTOs, browser state, logs, or exceptions. Existing fixed relative artifact paths in typed CLI receipts remain unchanged.
- Provider latency is operational timing only. Measure and report it separately from deterministic simulator compute time, and never include wall-clock latency in scientific hashes, metrics, comparisons, study statistics, replay equality, or canonical artifacts.
- Rules and mock V10 paths remain fully offline. Provider experience tests use only a bounded loopback fake OpenAI-compatible server and explicitly fail on any other network request.
- Preserve canonical JSON, exact integer validation, finite-number validation, stable ordering, positive zero, no-overwrite report publication, path containment, source-run immutability, and strict unknown-version refusal.
- Keep all browser text inert. Do not render provider values as HTML or Markdown, do not add remote assets, and do not use local storage, session storage, IndexedDB, Cache Storage, service workers, analytics, or telemetry.
- Keep the current preview at `http://127.0.0.1:50050/` available until a separately started candidate passes HTTP and browser smoke. Any Windows background process must use a hidden window.
- Use exact-path staging shown below. Never use `git add .`, `git add src`, `git add tests`, or another broad directory stage.
- Push each focused, passing commit to the existing `origin/main`; never add or modify a remote and never change global Git configuration.

## Review Focus

- **Version confusion:** a V9 response/cognition document, a V10 document, `True`, `10.0`, an unknown V11, and a mixed V9/V10 comparison or study must each take one explicit tested path, never union trial-parsing. Tasks 1-5 own these tests.
- **Scientific overclaiming:** different valid provider signals must leave rule-baseline metrics and A/A statistics exactly unchanged; cognition snapshots must have no purchase or campaign-effect delta. Tasks 1-4 own these tests.
- **Hostile presentation data:** Persian/bidirectional text, `</script>`, credential-shaped nested strings, and malformed Unicode must remain inert and absent from scripts, URLs, storage, logs, and report execution. Tasks 4, 5, and 7 own these tests.
- **Stale asynchronous UI state:** delayed compatibility checks, validation responses, job updates, pagination, and run changes must never display evidence for the wrong profile, scope, run, minute, or immutable snapshot. Tasks 5-7 own these tests.
- **Installed/frozen operation:** the exact wheel and, when locally available, exact PyInstaller executable must run rules/mock V10 offline plus provider V10 against loopback only, then replay without provider/cache/keyring/environment access. Tasks 8-9 own these tests.

---

### Task 1: Derive version-2 rule-baseline and cognition metrics

**Files:**

- Create: `src/adlife/core/experiments/spatial_response_metrics_v2.py`
- Modify: `src/adlife/core/experiments/spatial_metrics.py`
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/cli/commands/city_metrics.py`
- Create: `tests/unit/experiments/test_spatial_response_metrics_v2.py`
- Modify: `tests/unit/experiments/test_spatial_metrics.py`
- Modify: `tests/unit/city/test_city_analysis.py`
- Modify: `tests/cli/test_city_metrics.py`
- Create: `tests/property/test_spatial_response_metrics_v2_order.py`

**Interfaces:**

- Consumes: `SpatialResponseEvaluationV2`, `CityCognitionInputV2`, `CityCognitionEvaluationV2`, and `CityRunManifestV10` from the prerequisite plans; existing `SpatialResponseInput`, `SpatialCampaignScenario`, `SpatialOpportunityEvaluation`, `SpatialAttentionEvaluation`, and `SpatialMetrics`.
- Produces: `SpatialRuleBaselineMetricsV2`, `SpatialProviderDependentCognitionMetricsV2`, and `SpatialResponseMetricsV2` in `spatial_response_metrics_v2.py`.
- Produces:

  ```python
  def derive_spatial_response_metrics_v2(
      response_input: SpatialResponseInput,
      response: SpatialResponseEvaluationV2,
      *,
      scenario: SpatialCampaignScenario,
      opportunities: SpatialOpportunityEvaluation,
      attention: SpatialAttentionEvaluation,
      agent_ids: Collection[str],
      attention_metrics: SpatialMetrics,
      cognition_input: CityCognitionInputV2,
      cognition: CityCognitionEvaluationV2,
  ) -> SpatialResponseMetricsV2: ...
  ```

- Changes the existing city projection signature to:

  ```python
  StoredSpatialResponseMetrics: TypeAlias = SpatialResponseMetrics | SpatialResponseMetricsV2

  def response_metrics_for_stored_city_run(
      stored: StoredCityRun,
  ) -> StoredSpatialResponseMetrics: ...
  ```

- `SpatialResponseMetricsV2.rule_baseline` contains the existing baseline event/state receipt shapes and purchase-intention proxy. `provider_dependent_cognition` contains only applied sentiment/recall receipts, final cognition sentiment/recall receipts, category/source/fallback counts, funded coverage, and exact cognition/policy provenance.

- [ ] **Step 1: Add the focused V10 metric contract test.** Create `test_v10_metrics_separate_rule_and_cognition_families` with representative assertions:

  ```python
  metrics = derive_spatial_response_metrics_v2(**v10_metric_inputs)
  assert metrics.schema_version == 2
  assert metrics.source_run_schema_version == 10
  assert metrics.rule_baseline.purchase_intention_proxy.name == "purchase_intention_proxy"
  cognition = metrics.provider_dependent_cognition.model_dump(mode="json")
  assert "purchase" not in canonical_json(cognition)
  assert cognition["claim_scope"] == "provider-dependent-exploratory-not-campaign-effect"
  ```

- [ ] **Step 2: Add exact provenance and cardinality tests.** Assert the response input/stream/state/summary hashes, cognition input/stream/summary hashes, influence-policy hash, eligible/selected/funded/unselected equations, source counts, category counts, and denominators reproduce the verified V10 artifacts.

- [ ] **Step 3: Add rule-noninterference tests.** Build two valid V10 evaluations with different terminal categories and assert `left.rule_baseline == right.rule_baseline`, including exact purchase-intention receipts, while only the cognition family and cognition-derived hashes differ.

- [ ] **Step 4: Add zero-denominator and bounds tests.** Assert an empty eligible-response set yields positive `0.0`, coverage `0.0`, no NaN/Infinity, complete final-state denominators, and canonical empty category/source counts.

- [ ] **Step 5: Add strict-version tests.** Assert V1 metrics reject a V10 response, V2 metrics reject V1/V9 evidence, and `True`, `10.0`, strings, unknown 11, wrong policy hashes, wrong stream hashes, and mismatched cognition summaries fail validation.

- [ ] **Step 6: Add the attention-metric V10 test.** Extend `SpatialMetrics` only for unchanged attention evidence from source schema 10; assert schemas 5-9 serialize exactly as before and schema 11/bool/float remain refused.

- [ ] **Step 7: Add deterministic-order properties.** Permute response records, terminal completion order, input mappings, campaigns, and agent IDs before revalidation and assert the derived canonical V2 metric bytes either normalize identically or reject noncanonical persisted evidence.

- [ ] **Step 8: Run the exact red node.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_metrics_v2.py::test_v10_metrics_separate_rule_and_cognition_families -q`

  Expected: the named node collects and FAILS inside the test because `adlife.core.experiments.spatial_response_metrics_v2` is not implemented; collection itself must succeed.

- [ ] **Step 9: Implement the V2 metric models.** Keep every model frozen/forbid-extra, use exact integer validators, reuse existing public event/state receipt types only where their source semantics remain exact, and use stable `math.fsum` folding.

- [ ] **Step 10: Implement `derive_spatial_response_metrics_v2`.** Revalidate every complete input, derive rule baseline only from embedded `SpatialRuleResponse` and rule states, derive cognition only from applied responses/cognition states/terminal summary, and bind every frozen hash.

- [ ] **Step 11: Add exact V10 dispatch in `analysis.py`.** Extend unchanged attention metrics to V10 and dispatch response metrics by manifest class without trial-parsing unions.

- [ ] **Step 12: Update `city_metrics` output.** Preserve all V1 human/JSON text; for V10 print separate `Rule baseline` and `Provider-dependent cognition` sections and the exploratory/synthetic disclosure.

- [ ] **Step 13: Run the focused green suite.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_metrics_v2.py tests/unit/experiments/test_spatial_metrics.py tests/unit/city/test_city_analysis.py tests/cli/test_city_metrics.py tests/property/test_spatial_response_metrics_v2_order.py -q`

  Expected: PASS.

- [ ] **Step 14: Run V1 metric regressions.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_metrics.py tests/property/test_spatial_response_metrics_order.py tests/cli/test_city_metrics.py -q`

  Expected: PASS with every existing V1 assertion unchanged.

- [ ] **Step 15a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/core/experiments/spatial_response_metrics_v2.py src/adlife/core/experiments/spatial_metrics.py src/adlife/city/analysis.py src/adlife/cli/commands/city_metrics.py tests/unit/experiments/test_spatial_response_metrics_v2.py tests/unit/experiments/test_spatial_metrics.py tests/unit/city/test_city_analysis.py tests/cli/test_city_metrics.py tests/property/test_spatial_response_metrics_v2_order.py`

- [ ] **Step 15b: Run focused lint.**

  Run: `uv run ruff check src/adlife/core/experiments/spatial_response_metrics_v2.py src/adlife/core/experiments/spatial_metrics.py src/adlife/city/analysis.py src/adlife/cli/commands/city_metrics.py tests/unit/experiments/test_spatial_response_metrics_v2.py tests/unit/experiments/test_spatial_metrics.py tests/unit/city/test_city_analysis.py tests/cli/test_city_metrics.py tests/property/test_spatial_response_metrics_v2_order.py`

- [ ] **Step 15c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 15d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 16a: Stage only Task 1 files.**

  Run: `git add -- src/adlife/core/experiments/spatial_response_metrics_v2.py src/adlife/core/experiments/spatial_metrics.py src/adlife/city/analysis.py src/adlife/cli/commands/city_metrics.py tests/unit/experiments/test_spatial_response_metrics_v2.py tests/unit/experiments/test_spatial_metrics.py tests/unit/city/test_city_analysis.py tests/cli/test_city_metrics.py tests/property/test_spatial_response_metrics_v2_order.py`

- [ ] **Step 16b: Check staged Task 1 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 16c: Verify the staged Task 1 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the nine Task 1 paths listed in Step 16a.

- [ ] **Step 16d: Commit Task 1.**

  Run: `git commit -m "feat(metrics): separate bounded cognition evidence"`

- [ ] **Step 16e: Push the checked Task 1 commit.**

  Run: `git push origin main`

---

### Task 2: Compare V10 baseline metrics without inventing cognition effects

**Files:**

- Create: `src/adlife/core/experiments/spatial_response_comparison_v2.py`
- Modify: `src/adlife/core/experiments/spatial_observations.py`
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/cli/commands/city_compare.py`
- Create: `tests/unit/experiments/test_spatial_response_comparison_v2.py`
- Modify: `tests/unit/experiments/test_spatial_observations.py`
- Modify: `tests/unit/city/test_city_analysis.py`
- Modify: `tests/cli/test_city_compare.py`
- Create: `tests/property/test_spatial_response_comparison_v2_order.py`

**Interfaces:**

- Consumes: `SpatialResponseMetricsV2` from Task 1.
- Produces: `SpatialRuleBaselineMetricsComparisonV2`, `SpatialCognitionProvenanceComparisonV2`, and `SpatialResponseMetricsComparisonV2`.
- Produces:

  ```python
  CognitionProvenanceClassification: TypeAlias = Literal[
      "matched-provider-dependent-exploratory",
      "incompatible-provider-provenance",
  ]

  def compare_spatial_response_metrics_v2(
      control: SpatialResponseMetricsV2,
      treatment: SpatialResponseMetricsV2,
      *,
      control_run_id: str,
      treatment_run_id: str,
  ) -> SpatialResponseMetricsComparisonV2: ...
  ```

- Changes the city projection return to `SpatialResponseMetricsComparison | SpatialResponseMetricsComparisonV2`; mixed V1/V2 inputs are refused.
- Produces `spatial_metric_observations(...)` dispatch that emits paired-study scalar observations from `rule_baseline` only when given V2 metrics.

- [ ] **Step 1: Add the comparison contract test.** Assert treatment-minus-control deltas exist only under `rule_baseline`, full control/treatment cognition snapshots remain separately labelled, and no cognition object has a `delta`, `effect`, `lift`, or purchase member.

- [ ] **Step 2: Add an exact A/A noninterference test.** Use different valid terminal signals with identical deterministic inputs and assert every baseline delta, including purchase-intention proxy, is positive zero while cognition provenance is classified rather than subtracted.

- [ ] **Step 3: Add matched/incompatible provenance tests.** Match only when both sides have the exact causal contract, prompt/result schema, influence policy, provider profile revision, and effective settings; otherwise return `incompatible-provider-provenance` without hiding either snapshot.

- [ ] **Step 4: Add strict compatibility tests.** Refuse mixed V9/V10, different seeds/populations/days/campaign identities, cross-bound hashes, unknown versions, and booleans/floats as versions.

- [ ] **Step 5: Add baseline-only observation tests.** Assert V2 observations keep the existing stable rule metric keys and source artifacts, include the rule-owned purchase proxy, and contain no provider category/source/fallback scalar.

- [ ] **Step 6: Add order/hash-seed properties.** Assert comparison JSON and observation tuples are stable under input ordering and both configured hash seeds.

- [ ] **Step 7: Run the exact red node.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_comparison_v2.py::test_v10_comparison_has_no_cognition_effect_delta -q`

  Expected: the named node collects and FAILS inside the test because `adlife.core.experiments.spatial_response_comparison_v2` is not implemented; collection itself must succeed.

- [ ] **Step 8: Implement the V2 comparison models and function.** Recompute all baseline deltas from the two complete baseline snapshots, preserve positive zero, carry both complete cognition metric snapshots, and classify cognition provenance without subtraction.

- [ ] **Step 9: Implement observation and city-analysis dispatch.** Select by exact metric/manifest type and reject mixed versions before computing any delta.

- [ ] **Step 10: Update `city_compare`.** Preserve V1 output. For V10, print baseline deltas first and a separate cognition provenance/coverage section that explicitly says it is not a campaign effect.

- [ ] **Step 11: Run the focused green suite.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_comparison_v2.py tests/unit/experiments/test_spatial_observations.py tests/unit/city/test_city_analysis.py tests/cli/test_city_compare.py tests/property/test_spatial_response_comparison_v2_order.py -q`

  Expected: PASS.

- [ ] **Step 12: Run V1 comparison and A/A regressions.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_response_comparison.py tests/property/test_spatial_response_comparison_order.py tests/unit/experiments/test_spatial_observations.py tests/cli/test_city_compare.py -q`

  Expected: PASS.

- [ ] **Step 13a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/core/experiments/spatial_response_comparison_v2.py src/adlife/core/experiments/spatial_observations.py src/adlife/city/analysis.py src/adlife/cli/commands/city_compare.py tests/unit/experiments/test_spatial_response_comparison_v2.py tests/unit/experiments/test_spatial_observations.py tests/unit/city/test_city_analysis.py tests/cli/test_city_compare.py tests/property/test_spatial_response_comparison_v2_order.py`

- [ ] **Step 13b: Run focused lint.**

  Run: `uv run ruff check src/adlife/core/experiments/spatial_response_comparison_v2.py src/adlife/core/experiments/spatial_observations.py src/adlife/city/analysis.py src/adlife/cli/commands/city_compare.py tests/unit/experiments/test_spatial_response_comparison_v2.py tests/unit/experiments/test_spatial_observations.py tests/unit/city/test_city_analysis.py tests/cli/test_city_compare.py tests/property/test_spatial_response_comparison_v2_order.py`

- [ ] **Step 13c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 13d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 14a: Stage only Task 2 files.**

  Run: `git add -- src/adlife/core/experiments/spatial_response_comparison_v2.py src/adlife/core/experiments/spatial_observations.py src/adlife/city/analysis.py src/adlife/cli/commands/city_compare.py tests/unit/experiments/test_spatial_response_comparison_v2.py tests/unit/experiments/test_spatial_observations.py tests/unit/city/test_city_analysis.py tests/cli/test_city_compare.py tests/property/test_spatial_response_comparison_v2_order.py`

- [ ] **Step 14b: Check staged Task 2 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 14c: Verify the staged Task 2 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the nine Task 2 paths listed in Step 14a.

- [ ] **Step 14d: Commit Task 2.**

  Run: `git commit -m "feat(compare): classify bounded cognition provenance"`

- [ ] **Step 14e: Push the checked Task 2 commit.**

  Run: `git push origin main`

---

### Task 3: Add baseline-only V10 paired studies and cognition receipts

**Files:**

- Modify: `src/adlife/core/experiments/_spatial_study_receipts.py`
- Modify: `src/adlife/core/experiments/_spatial_study_validation.py`
- Create: `src/adlife/core/experiments/spatial_study_v2.py`
- Modify: `src/adlife/city/studies.py`
- Modify: `src/adlife/cli/commands/city_study.py`
- Create: `tests/unit/experiments/test_spatial_study_v2.py`
- Modify: `tests/unit/experiments/test_spatial_study_validation.py`
- Modify: `tests/unit/experiments/test_spatial_study_result.py`
- Create: `tests/integration/test_city_spatial_study_v10.py`
- Modify: `tests/cli/test_city_study.py`
- Modify: `tests/property/test_spatial_study_order.py`
- Modify: `tests/integration/test_spatial_study_memory.py`

**Interfaces:**

- Consumes: `CityCognitionManifestReceiptV2`, `CityRunManifestV10`, `SpatialResponseMetricsV2`, and Task 2 baseline observations.
- Produces: `CityCognitionStudyReceiptV2`, `SpatialStudyResponseReceiptV2`, `SpatialStudyArmReceiptV2`, `SpatialStudyPairReceiptV2`, and `SpatialStudyResultV2`.
- `SpatialStudyResultV2` has schema 2/model `spatial-paired-study-v2`, source schema 10/response model V2, `rule-baseline-exact-zero-verified | not-applicable` A/A status, cognition provenance classifications/counts, baseline-only scalar pairs/statistics, and no cognition delta/statistic fields.
- Changes:

  ```python
  SpatialStudyResultDocument: TypeAlias = SpatialStudyResult | SpatialStudyResultV2

  def analyze_stored_spatial_study(
      store: CityRunStore,
      definition: SpatialStudyDefinition,
  ) -> SpatialStudyResultDocument: ...
  ```

- [ ] **Step 1: Add V2 receipt contract tests.** Assert complete 4/10 workbench/manifest binding, cognition input/stream/summary/policy hashes, source/category/fallback counts, response V2 hashes/counts, exact integer validation, canonical campaign IDs, and no secret/path/raw-body fields.

- [ ] **Step 2: Add the baseline-only statistic test.** Use provider-different A/A pairs and assert:

  ```python
  assert result.a_a_status == "rule-baseline-exact-zero-verified"
  assert all(item.mean_paired_difference == 0.0 for item in result.statistics)
  assert all("cognition" not in item.metric_key for item in result.statistics)
  assert result.pairs[0].cognition_provenance_classification == (
      "incompatible-provider-provenance"
  )
  ```

- [ ] **Step 3: Add mixed-version and provenance tests.** Refuse mixed V9/V10 arms or seeds, accept all-V10 rules/mock/provider pairs, and classify differing V10 cognition provenance without refusing otherwise matched baseline analysis.

- [ ] **Step 4: Add replayed-artifact and corruption tests.** Refuse mismatched receipt hashes/counts/policies, incomplete cognition evidence, cross-run terminal provenance, and response metrics not derived from the same verified run.

- [ ] **Step 5: Add deterministic statistics tests.** Preserve the existing 10,000-resample bootstrap seed/order, directional-stability threshold, positive zero, seed-order invariance, and A/A exact-zero failure semantics for baseline observations.

- [ ] **Step 6: Add streaming-memory tests.** Assert `_load_projection` releases each full run before loading the next and retains only bounded receipts/observations; V10 terminal records/raw response streams are not retained in the study result.

- [ ] **Step 7: Add CLI tests.** Assert JSON is one valid document, human output clearly separates baseline statistics from cognition provenance, corrupt artifacts use artifact exit semantics, and mixed versions fail without traceback.

- [ ] **Step 8: Run the exact red node.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_study_v2.py::test_v10_study_statistics_use_only_rule_baseline -q`

  Expected: the named node collects and FAILS inside the test because `adlife.core.experiments.spatial_study_v2` is not implemented; collection itself must succeed.

- [ ] **Step 9: Implement the V2 receipt graph and validator.** Keep V1 receipt classes and `SpatialStudyResult` unchanged; select the V2 graph only after exact manifest 10 discrimination.

- [ ] **Step 10: Implement `SpatialStudyResultV2`.** Reuse the existing deterministic bootstrap/statistic function over baseline observations, store cognition receipts/classifications only, and enforce the canonical JSON size ceiling.

- [ ] **Step 11: Implement exact city study dispatch.** Inspect the first declared run version, require every other declared run to match it, project V10 one run at a time, and never discover or rerun an undeclared run.

- [ ] **Step 12: Update `city_study`.** Revalidate the returned union by exact schema discriminator, preserve V1 output, and add truthful V10 human text with no cognition effect estimate.

- [ ] **Step 13: Run the focused green suite.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_study_v2.py tests/unit/experiments/test_spatial_study_validation.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study_v10.py tests/cli/test_city_study.py tests/property/test_spatial_study_order.py tests/integration/test_spatial_study_memory.py -q`

  Expected: PASS.

- [ ] **Step 14: Run V1 study regressions.**

  Run: `uv run pytest tests/unit/experiments/test_spatial_study.py tests/integration/test_city_spatial_study.py tests/property/test_spatial_study_statistics_order.py tests/security/test_spatial_study_security.py -q`

  Expected: PASS.

- [ ] **Step 15a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/core/experiments/_spatial_study_receipts.py src/adlife/core/experiments/_spatial_study_validation.py src/adlife/core/experiments/spatial_study_v2.py src/adlife/city/studies.py src/adlife/cli/commands/city_study.py tests/unit/experiments/test_spatial_study_v2.py tests/unit/experiments/test_spatial_study_validation.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study_v10.py tests/cli/test_city_study.py tests/property/test_spatial_study_order.py tests/integration/test_spatial_study_memory.py`

- [ ] **Step 15b: Run focused lint.**

  Run: `uv run ruff check src/adlife/core/experiments/_spatial_study_receipts.py src/adlife/core/experiments/_spatial_study_validation.py src/adlife/core/experiments/spatial_study_v2.py src/adlife/city/studies.py src/adlife/cli/commands/city_study.py tests/unit/experiments/test_spatial_study_v2.py tests/unit/experiments/test_spatial_study_validation.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study_v10.py tests/cli/test_city_study.py tests/property/test_spatial_study_order.py tests/integration/test_spatial_study_memory.py`

- [ ] **Step 15c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 15d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 16a: Stage only Task 3 files.**

  Run: `git add -- src/adlife/core/experiments/_spatial_study_receipts.py src/adlife/core/experiments/_spatial_study_validation.py src/adlife/core/experiments/spatial_study_v2.py src/adlife/city/studies.py src/adlife/cli/commands/city_study.py tests/unit/experiments/test_spatial_study_v2.py tests/unit/experiments/test_spatial_study_validation.py tests/unit/experiments/test_spatial_study_result.py tests/integration/test_city_spatial_study_v10.py tests/cli/test_city_study.py tests/property/test_spatial_study_order.py tests/integration/test_spatial_study_memory.py`

- [ ] **Step 16b: Check staged Task 3 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 16c: Verify the staged Task 3 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the twelve Task 3 paths listed in Step 16a.

- [ ] **Step 16d: Commit Task 3.**

  Run: `git commit -m "feat(study): analyze v10 rule baselines"`

- [ ] **Step 16e: Push the checked Task 3 commit.**

  Run: `git push origin main`

---

### Task 4: Render and publish self-contained V10 study reports

**Files:**

- Modify: `src/adlife/reporting/spatial_html.py`
- Modify: `src/adlife/reporting/templates/spatial-report.html.j2`
- Modify: `src/adlife/reporting/static/spatial-report.css`
- Modify: `src/adlife/cli/commands/city_report.py`
- Modify: `tests/unit/reporting/spatial_report_fixtures.py`
- Modify: `tests/unit/reporting/test_spatial_html.py`
- Modify: `tests/browser/test_spatial_report_browser.py`
- Modify: `tests/security/test_spatial_report_security.py`
- Modify: `tests/cli/test_city_report.py`

**Interfaces:**

- Consumes: `SpatialStudyResult | SpatialStudyResultV2` from Task 3.
- Changes:

  ```python
  def render_spatial_study_html(
      result: SpatialStudyResult | SpatialStudyResultV2,
  ) -> bytes: ...

  def publish_spatial_study_report(
      result: SpatialStudyResult | SpatialStudyResultV2,
      root: Path,
  ) -> SpatialReportReceipt: ...
  ```

- Produces one zero-JavaScript, self-contained report. V10 adds separate `Rule baseline` and `Bounded cognition evidence` sections; cognition shows aggregate enums/source/fallback/coverage and frozen provenance, never a treatment-effect statistic.

- [ ] **Step 1: Add the V10 report content test.** Assert the report contains effect scope, profile revision/model ID when present, prompt/result/influence hashes, source/fallback/category counts, funded coverage, offline replay statement, unsigned-hash limitation, synthetic disclosure, and rule-owned purchase proxy label.

- [ ] **Step 2: Add a no-overclaim test.** Assert the cognition section contains `provider-dependent exploratory evidence`, `not observed behavior`, and the nonvalidated-psychology disclosure. Forbid `sales lift`, `conversion probability`, affirmative prediction/validation language, and every cognition treatment-minus-control field or label; do not forbid truthful negative disclosures merely because they contain the words being disclaimed.

- [ ] **Step 3: Add self-contained security tests.** Assert no HTTP(S) asset, remote font/script, JavaScript execution, raw provider body, prompt, credential, authorization header, cache path, or environment/keyring name appears.

- [ ] **Step 4: Add hostile text tests.** Parameterize campaign/city/profile labels with Persian, bidi controls, `</script>`, HTML/SVG event attributes, malformed Unicode replacements, and credential-shaped nested values; assert escaped visible text and no executable DOM node.

- [ ] **Step 5: Add publication failure tests.** Fault render, stage open/write/fsync, atomic no-clobber link, and best-effort post-link directory sync at their existing commit boundaries. Assert source run hashes are unchanged, prior reports are not overwritten, pre-commit temporary files are cleaned, a post-link sync failure retains the already complete successful publication contract, and report conflicts retain exit code 3.

- [ ] **Step 6: Add browser accessibility tests.** Verify headings, table captions, keyboard navigation, 390-pixel/200% layout, forced colors, reduced motion, print layout, Persian direction isolation, and zero-denominator rendering.

- [ ] **Step 7: Add `city_report` V10 tests.** Assert human/JSON output is concise, no traceback is emitted, incompatible mixed studies fail cleanly, and report receipt hashes the exact bytes.

- [ ] **Step 8: Run the exact red node.**

  Run: `uv run pytest tests/unit/reporting/test_spatial_html.py::test_v10_report_separates_rule_and_cognition_evidence -q`

  Expected: FAIL at `render_spatial_study_html(v2_result)` with a Pydantic literal-validation error because the current renderer revalidates the schema-2 fixture as `SpatialStudyResult` schema 1.

- [ ] **Step 9: Implement exact report dispatch and template branches.** Revalidate by schema version before rendering, pass only bounded typed values to Jinja, retain autoescaping, and keep the existing V1 HTML semantics unchanged.

- [ ] **Step 10: Implement V10 report styling.** Use the existing typography/layout system, visually separate rule and cognition evidence without decorative AI imagery, and add no remote dependency.

- [ ] **Step 11: Update `city_report`.** Preserve preflight-before-analysis and no-clobber publication, accept the exact study union, and retain concise typed errors.

- [ ] **Step 12: Run the focused green suite.**

  Run: `uv run pytest tests/unit/reporting/test_spatial_html.py tests/browser/test_spatial_report_browser.py tests/security/test_spatial_report_security.py tests/cli/test_city_report.py -q`

  Expected: PASS.

- [ ] **Step 13: Run V1 report regressions.**

  Run: `uv run pytest tests/unit/reporting/test_spatial_html.py tests/integration/test_city_spatial_study.py tests/cli/test_city_report.py -q`

  Expected: PASS with existing V1 fixtures unchanged.

- [ ] **Step 14a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/reporting/spatial_html.py src/adlife/cli/commands/city_report.py tests/unit/reporting/spatial_report_fixtures.py tests/unit/reporting/test_spatial_html.py tests/browser/test_spatial_report_browser.py tests/security/test_spatial_report_security.py tests/cli/test_city_report.py`

- [ ] **Step 14b: Run focused lint.**

  Run: `uv run ruff check src/adlife/reporting/spatial_html.py src/adlife/cli/commands/city_report.py tests/unit/reporting/spatial_report_fixtures.py tests/unit/reporting/test_spatial_html.py tests/browser/test_spatial_report_browser.py tests/security/test_spatial_report_security.py tests/cli/test_city_report.py`

- [ ] **Step 14c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 14d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 15a: Stage only Task 4 files.**

  Run: `git add -- src/adlife/reporting/spatial_html.py src/adlife/reporting/templates/spatial-report.html.j2 src/adlife/reporting/static/spatial-report.css src/adlife/cli/commands/city_report.py tests/unit/reporting/spatial_report_fixtures.py tests/unit/reporting/test_spatial_html.py tests/browser/test_spatial_report_browser.py tests/security/test_spatial_report_security.py tests/cli/test_city_report.py`

- [ ] **Step 15b: Check staged Task 4 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 15c: Verify the staged Task 4 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the nine Task 4 paths listed in Step 15a.

- [ ] **Step 15d: Commit Task 4.**

  Run: `git commit -m "feat(report): present bounded cognition evidence"`

- [ ] **Step 15e: Push the checked Task 4 commit.**

  Run: `git push origin main`

---

### Task 5: Expose exact, read-only V10 API and `city-view` projections

**Files:**

- Create: `src/adlife/city/workbench_cognition_views.py`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `src/adlife/cli/commands/city_view.py`
- Create: `tests/unit/city/test_workbench_cognition_views.py`
- Modify: `tests/unit/city/test_city_web.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/cli/test_city_view.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**

- Consumes: fully verified `StoredCityRun` V9/V10 objects; runtime `WorkbenchRunInputViewV4`; `SpatialResponseEvaluationV2`, V2 response summary/state documents, `SpatialResponseMetricsV2`, `CityCognitionInputV2`, and `CityCognitionEvaluationV2`.
- Produces `WorkbenchCognitionSummaryViewV1`, `WorkbenchCognitionSummaryViewV2`, `WorkbenchCognitionRecordsViewV1`, and `WorkbenchCognitionRecordsViewV2`.
- Produces:

  ```python
  WorkbenchCognitionSummaryView: TypeAlias = (
      WorkbenchCognitionSummaryViewV1 | WorkbenchCognitionSummaryViewV2
  )
  WorkbenchCognitionRecordsView: TypeAlias = (
      WorkbenchCognitionRecordsViewV1 | WorkbenchCognitionRecordsViewV2
  )

  def build_workbench_cognition_summary_view(
      stored: StoredCityRun,
  ) -> WorkbenchCognitionSummaryView: ...

  def build_workbench_cognition_records_view(
      stored: StoredCityRun,
  ) -> WorkbenchCognitionRecordsView: ...
  ```

- Extends `CityRunSchemaVersion` to literal 10 and changes `CityViewContext.response_evaluation`, `spatial_response_metrics`, response summary, and state documents to exact V1/V2 unions selected at context construction.
- Keeps endpoints `/workbench-input`, `/cognition-summary`, `/cognition-records`, `/response-summary`, `/response-events`, `/response-state`, and `/spatial-response-metrics`; the workbench-input response is schema 3 for V9 and schema 4 for V10, while cognition/response projections use response `schema_version` 1 for V9 and 2 for V10.

- [ ] **Step 1: Add DTO contract tests.** Assert V2 summary/records revalidate all run/input/request/policy bindings and expose only typed non-secret values; assert the saved workbench-input route returns the runtime plan's exact `WorkbenchRunInputViewV4`; V1 views retain exact keys and values.

- [ ] **Step 2: Add strict version tests.** Assert V9 cannot project V2, V10 cannot project V1, and V11/bool/float versions refuse before reading version-specific fields.

- [ ] **Step 3: Add response endpoint tests.** Assert V10 response pages keep stream order as one or more `spatial.applied-response` records followed by one `spatial.dual-state-updated` record per canonical group; each applied record exposes its embedded `spatial.response` baseline without inventing a standalone baseline stream line. State pages expose separate rule/cognition tracks and purchase only on rule state.

- [ ] **Step 4: Add cognition endpoint tests.** Assert terminal records bind the exact applied response, unselected responses have no terminal record, summaries expose coverage/policies/provenance, and no raw body/prompt/path/secret appears.

- [ ] **Step 5: Add immutable snapshot/race tests.** Delay pages while switching requested runs/minutes, reuse stale snapshot tokens, and mutate backing test fixtures after context creation; assert the response stays with one immutable verified context or refuses stale input.

- [ ] **Step 6: Add read-only tests.** Snapshot every source artifact before/after every V10 GET and `city-view` startup; monkeypatch provider/cache/keyring/environment/write functions to fail if called.

- [ ] **Step 7: Add capability tests.** Expose exact effect scopes `explanation-only` and `bounded-signals-v1`, funded limits `[10, 20, 40, 60]`, schema pair `4 -> 10`, and the V2 compatibility-check requirement without exposing mutable contract constants for browser submission.

- [ ] **Step 8: Add `city_view` tests.** Assert a V10 run launches the exact read-only context, JSON output remains disallowed, corrupt artifacts fail before Uvicorn starts, and no provider operation occurs.

- [ ] **Step 9: Run the exact red node.**

  Run: `uv run pytest tests/unit/city/test_workbench_cognition_views.py::test_v10_summary_view_is_path_free_and_fully_bound -q`

  Expected: the named node collects and FAILS inside the test because `adlife.city.workbench_cognition_views` is not implemented; collection itself must succeed.

- [ ] **Step 10: Implement the four DTOs and builders.** Discriminate by manifest class first, revalidate complete V1/V2 source models, and return frozen path-free projections.

- [ ] **Step 11: Implement V10 city-view context and endpoint dispatch.** Build indexes from canonical tuples, keep pagination bounded, and never repair or normalize persisted corruption in the adapter.

- [ ] **Step 12: Refactor workbench saved-run routes through exact builders.** Preserve exact V1 route semantics, dispatch the workbench-input route to V3 or V4 by manifest class, and add V2 cognition responses without union trial parsing.

- [ ] **Step 13: Update `city_view`.** Derive V10 attention/response metrics through Task 1, pass the exact V2 evidence into `create_city_app`, and preserve loopback-only Uvicorn settings.

- [ ] **Step 14: Run the focused green suite.**

  Run: `uv run pytest tests/unit/city/test_workbench_cognition_views.py tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/cli/test_city_view.py tests/security/test_workbench_browser_security.py -q`

  Expected: PASS.

- [ ] **Step 15: Run V1 API regressions.**

  Run: `uv run pytest tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/cli/test_city_view.py -q`

  Expected: PASS with schemas V1-V9 unchanged.

- [ ] **Step 16a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/city/workbench_cognition_views.py src/adlife/city/web.py src/adlife/city/workbench_web.py src/adlife/cli/commands/city_view.py tests/unit/city/test_workbench_cognition_views.py tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/cli/test_city_view.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 16b: Run focused lint.**

  Run: `uv run ruff check src/adlife/city/workbench_cognition_views.py src/adlife/city/web.py src/adlife/city/workbench_web.py src/adlife/cli/commands/city_view.py tests/unit/city/test_workbench_cognition_views.py tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/cli/test_city_view.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 16c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 16d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 17a: Stage only Task 5 files.**

  Run: `git add -- src/adlife/city/workbench_cognition_views.py src/adlife/city/web.py src/adlife/city/workbench_web.py src/adlife/cli/commands/city_view.py tests/unit/city/test_workbench_cognition_views.py tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/cli/test_city_view.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 17b: Check staged Task 5 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 17c: Verify the staged Task 5 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the nine Task 5 paths listed in Step 17a.

- [ ] **Step 17d: Commit Task 5.**

  Run: `git commit -m "feat(api): expose verified v10 cognition"`

- [ ] **Step 17e: Push the checked Task 5 commit.**

  Run: `git push origin main`

---

### Task 6: Add bounded-cognition operation controls to the workbench

**Files:**

- Modify: `src/adlife/city/static/workbench.html`
- Modify: `src/adlife/city/static/workbench.css`
- Modify: `src/adlife/city/static/workbench.js`
- Modify: `src/adlife/city/static/workbench-providers.js`
- Modify: `src/adlife/city/static/workbench-providers.css`
- Create: `tests/browser/test_bounded_cognition_workbench_browser.py`
- Modify: `tests/browser/test_city_workbench_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`
- Modify: `tests/unit/city/test_workbench_web.py`

**Interfaces:**

- Consumes: Task 5 capability fields; runtime-plan schema-3/schema-4 validation, scoped provider compatibility, `WorkbenchAcceptedSettings`, V10 job phases, and verified run summaries.
- Produces browser draft selection:

  ```javascript
  // explanation-only => outer schema_version 3; preserve the exact V3 cognition shape
  {
    mode,
    provider_profile_id,
    provider_revision_sha256,
    aggregate_output_token_ceiling
  }

  // bounded-signals-v1 => outer schema_version 4
  {
    effect_scope: "bounded-signals-v1",
    mode,
    provider_profile_id,
    provider_revision_sha256,
    funded_response_limit: 10 | 20 | 40 | 60
  }
  ```

- Adds controls `#cognition-effect-scope` and `#cognition-funded-responses`; retains `#cognition-budget` only for V9 explanation mode.
- Provider compatibility POST body is `{}` for V1 explanation checks and exactly `{"effect_scope":"bounded-signals-v1"}` for V2 checks.

- [ ] **Step 1: Add the missing-control browser test.** Open the run step and assert operation and source are independent controls, bounded provider mode defaults to 20 funded responses, and rules/mock display fixed 60 with no provider-token reservation.

- [ ] **Step 2: Add the exact serializer matrix test.** Assert all eight legal scope/source pairs select schema 3 or 4 exactly. Schema 3 must retain its four-field cognition body and omit `effect_scope`; schema 4 must include `effect_scope="bounded-signals-v1"` and omit any browser-supplied policy hash, transport ceiling, aggregate token ceiling, credential, or endpoint.

- [ ] **Step 3: Add scoped compatibility tests.** Record request bodies, prove a V1 success cannot authorize V2, prove effect/profile/settings/credential changes invalidate status, and prove stale delayed checks cannot re-enable submit.

- [ ] **Step 4: Add review/accepted-settings tests.** Assert the review names movement/attention/purchase as rules, signals as provider-or-fallback, live signals as not guaranteed reproducible, saved replay as frozen/offline, and verifies exact 3/9 or 4/10 worker receipts.

- [ ] **Step 5: Add budget/cost copy tests.** Assert V10 displays 512 reserved output tokens and at most four HTTP attempts per funded request, warns that input tokens/vendor charges are outside the reservation, and never labels the value a spending limit.

- [ ] **Step 6: Add credential lifecycle tests.** Assert credential fields clear on source/scope/profile/reset/navigation/submission/error; values never enter URL/history/DOM text/browser storage or later DTOs.

- [ ] **Step 7: Add interaction/accessibility tests.** Cover keyboard/focus order, validation errors, double submit, cancel, and the exact V10 phase sequence `planning-cognition -> requesting-cognition -> applying-cognition` without relabelling V9 phases. Also cover delayed phase responses, a 390-pixel viewport, 200% zoom, forced colors, reduced motion, Persian labels, and concise nonduplicated copy.

- [ ] **Step 8: Run the exact red node.**

  Run: `uv run pytest tests/browser/test_bounded_cognition_workbench_browser.py::test_operation_and_source_controls_select_exact_schema -q`

  Expected: FAIL because locator `#cognition-effect-scope` does not exist.

- [ ] **Step 9: Implement the semantic HTML controls and boundary card.** Replace explanation-only headings with neutral `Model assistance` language; keep the persistent exact authority statement from the spec and explicit synthetic/nonvalidated wording.

- [ ] **Step 10: Implement provider-panel state and scoped checks.** Increment one generation token on every authority-affecting change, abort/ignore stale results, and never infer effect scope from profile presence.

- [ ] **Step 11: Implement exact schema serialization and validation review.** Derive outer schema from effect scope, accept only server-declared modes/limits, and compare worker receipts to the submitted immutable draft.

- [ ] **Step 12: Implement focused responsive styles.** Reuse the existing design system, keep important state textual rather than color-only, remove redundant paragraphs, and add no animation beyond reduced-motion-safe existing behavior.

- [ ] **Step 13: Run the focused green suite.**

  Run: `uv run pytest tests/browser/test_bounded_cognition_workbench_browser.py tests/browser/test_city_workbench_browser.py tests/security/test_workbench_browser_security.py tests/unit/city/test_workbench_web.py -q`

  Expected: PASS.

- [ ] **Step 14: Run with browser skips forbidden.**

  ```powershell
  $env:ADLIFE_REQUIRE_BROWSER='1'
  try {
      uv run pytest tests/browser/test_bounded_cognition_workbench_browser.py -q
      if ($LASTEXITCODE -ne 0) {
          throw "bounded cognition workbench browser suite failed"
      }
  }
  finally {
      Remove-Item Env:ADLIFE_REQUIRE_BROWSER -ErrorAction SilentlyContinue
  }
  ```

  Expected: PASS; a missing browser is a failure for this gate.

- [ ] **Step 15a: Run focused formatting.**

  Run: `uv run ruff format --check tests/browser/test_bounded_cognition_workbench_browser.py tests/browser/test_city_workbench_browser.py tests/security/test_workbench_browser_security.py tests/unit/city/test_workbench_web.py`

- [ ] **Step 15b: Run focused lint.**

  Run: `uv run ruff check tests/browser/test_bounded_cognition_workbench_browser.py tests/browser/test_city_workbench_browser.py tests/security/test_workbench_browser_security.py tests/unit/city/test_workbench_web.py`

- [ ] **Step 15c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 15d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 16a: Stage only Task 6 files.**

  Run: `git add -- src/adlife/city/static/workbench.html src/adlife/city/static/workbench.css src/adlife/city/static/workbench.js src/adlife/city/static/workbench-providers.js src/adlife/city/static/workbench-providers.css tests/browser/test_bounded_cognition_workbench_browser.py tests/browser/test_city_workbench_browser.py tests/security/test_workbench_browser_security.py tests/unit/city/test_workbench_web.py`

- [ ] **Step 16b: Check staged Task 6 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 16c: Verify the staged Task 6 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the nine Task 6 paths listed in Step 16a.

- [ ] **Step 16d: Commit Task 6.**

  Run: `git commit -m "feat(workbench): configure bounded ai cognition"`

- [ ] **Step 16e: Push the checked Task 6 commit.**

  Run: `git push origin main`

---

### Task 7: Add the saved-run V10 cognition inspector

**Files:**

- Create: `src/adlife/city/static/app-cognition-v2.js`
- Create: `src/adlife/city/static/app-cognition-v2.css`
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `src/adlife/city/web.py`
- Create: `tests/browser/test_bounded_cognition_inspector_browser.py`
- Modify: `tests/browser/test_city_places_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`
- Modify: `tests/packaging/test_wheel.py`

**Interfaces:**

- Consumes: Task 5 schema-2 cognition/response/state/metric endpoints.
- Produces one frozen global helper with no I/O side effects:

  ```javascript
  window.AdLifeBoundedCognition = Object.freeze({
    validateSummary,
    validateRecords,
    indexTerminalRecords,
    presentationForAppliedResponse,
  });
  ```

- `validateSummary(value)` and `validateRecords(value, summary)` return deeply frozen validated projections. `indexTerminalRecords(records)` returns a read-only `record_hash` lookup because every applied record binds `terminal_record_hash`, not request ID; a secondary request-ID view may be exposed only if it cannot weaken the primary hash binding. `presentationForAppliedResponse(applied, index)` resolves the exact `applied.terminal_record_hash`, then returns inert labels for baseline, signal/status, mapping, applied delta, and dual-state binding. `source="unselected"` must have no lookup.

- [ ] **Step 1: Add pure-helper contract tests.** Assert helpers index by unique terminal `record_hash`, resolve the applied record's exact `terminal_record_hash`, and reject extra/missing fields, wrong schemas, unknown enums, nonfinite values, duplicate record hashes/request IDs, reordered records, orphan/cross-run/cross-agent/cross-campaign/cross-minute/cross-policy bindings, request-ID-only matches with a wrong record hash, and a terminal for `unselected`.

- [ ] **Step 2: Add helper side-effect tests.** Before/after calls, assert no fetch, XHR, WebSocket, DOM mutation, storage access, worker, timer, or network operation and assert returned objects cannot be mutated.

- [ ] **Step 3: Add the inspector sequence test.** For every eligible response assert visible ordered stages:

  ```text
  deterministic baseline
    -> signal or neutral status
    -> city-cognition-influence-v1 mapping
    -> applied cognition delta
    -> atomic rule/cognition state update
  ```

- [ ] **Step 4: Add authority-label tests.** Purchase intention appears only on the rule card; the cognition card shows sentiment/recall/count/last minute only; unselected/rules/fallback/budget-skipped/uncertain/safety states are distinct.

- [ ] **Step 5: Add provenance/coverage tests.** Display exact profile/model/revision, prompt/result/influence hashes, disclosure, selected/funded/unselected counts, overlapping uncertain/safety counts, and saved-offline replay wording.

- [ ] **Step 6: Add stale-request tests.** Delay cognition/response/state pages, switch agent/minute/run, and assert late data is ignored unless run ID, snapshot token, agent, minute, and generation all still match.

- [ ] **Step 7: Add hostile/accessibility tests.** Render Persian/bidi labels and hostile campaign text as inert text; test keyboard selection/filtering, 390 pixels, 200%, forced colors, reduced motion, screen-reader labels, and no color-only status.

- [ ] **Step 8: Add immutability and network tests.** Hash every source artifact before/after browsing and fail any request outside the viewer origin or any write method.

- [ ] **Step 9: Run the exact red node.**

  Run: `uv run pytest tests/browser/test_bounded_cognition_inspector_browser.py::test_pure_v2_helpers_validate_and_freeze_evidence -q`

  Expected: FAIL at the asset-response assertion with `expected 200, got 404` for `/static/app-cognition-v2.js`.

- [ ] **Step 10: Implement the pure helper asset.** Use closed literal sets and exact own-key checks; never use `innerHTML`, `eval`, dynamic script, Markdown, or JSON embedded in executable script.

- [ ] **Step 11: Implement the inspector DOM and fetch orchestration.** Load V2 assets before `app.js`, preserve the V9 explanation panel, request only same-origin read-only pages, and render with `textContent`/created nodes.

- [ ] **Step 12: Implement focused inspector styles and asset allowlisting.** Keep bounded DOM pages, clear track separation, direction isolation, accessible focus, and responsive layouts; add both assets to the exact allowlist.

- [ ] **Step 13: Run the focused green suite.**

  Run: `uv run pytest tests/browser/test_bounded_cognition_inspector_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py -q`

  Expected: PASS.

- [ ] **Step 14: Run with browser skips forbidden.**

  ```powershell
  $env:ADLIFE_REQUIRE_BROWSER='1'
  try {
      uv run pytest tests/browser/test_bounded_cognition_inspector_browser.py -q
      if ($LASTEXITCODE -ne 0) {
          throw "bounded cognition inspector browser suite failed"
      }
  }
  finally {
      Remove-Item Env:ADLIFE_REQUIRE_BROWSER -ErrorAction SilentlyContinue
  }
  ```

  Expected: PASS.

- [ ] **Step 15a: Run focused formatting.**

  Run: `uv run ruff format --check src/adlife/city/web.py tests/browser/test_bounded_cognition_inspector_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py`

- [ ] **Step 15b: Run focused lint.**

  Run: `uv run ruff check src/adlife/city/web.py tests/browser/test_bounded_cognition_inspector_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py`

- [ ] **Step 15c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 15d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 16a: Stage only Task 7 files.**

  Run: `git add -- src/adlife/city/static/app-cognition-v2.js src/adlife/city/static/app-cognition-v2.css src/adlife/city/static/index.html src/adlife/city/static/app.js src/adlife/city/static/app.css src/adlife/city/web.py tests/browser/test_bounded_cognition_inspector_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py`

- [ ] **Step 16b: Check staged Task 7 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 16c: Verify the staged Task 7 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the ten Task 7 paths listed in Step 16a.

- [ ] **Step 16d: Commit Task 7.**

  Run: `git commit -m "feat(inspector): trace bounded cognition evidence"`

- [ ] **Step 16e: Push the checked Task 7 commit.**

  Run: `git push origin main`

---

### Task 8: Reconcile public documentation and extend wheel/frozen clean-room smoke

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/installation.md`
- Modify: `docs/investor-demo.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/releasing.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/methodology/experiment-protocol.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `scripts/smoke_frozen.py`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_resources.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/packaging/test_smoke_frozen.py`
- Modify: `tests/security/test_redaction_corpus.py`
- Create: `tests/integration/test_city_cognition_v2_performance.py`

**Interfaces:**

- Consumes: completed V10 workbench/API/metrics/study/report/browser behavior from Tasks 1-7.
- Produces no new scientific contract. Public docs describe three exact modes, the provider authority boundary, offline/fresh-run replay distinction, credential rules, research limitations, and exact CLI/browser workflow.
- Extends `smoke_release.py` and `smoke_frozen.py` to exercise schema-4/V10 rules, mock, and loopback provider paths, metrics, replay, inspector APIs, study, and report from installed artifacts only.

- [ ] **Step 1: Add failing documentation assertions.** Require the exact three-mode matrix, V10 bounded categories, fixed influence mapping, rule-owned purchase proxy, persona exclusion, 10/20/40/60 funding, 512-token reservation, up-to-four attempts, provider cost caveat, live nondeterminism, frozen offline replay, unsigned hash-chain limitation, and no ChatGPT/Codex subscription/OAuth credential reuse.

- [ ] **Step 2: Add installed-resource tests.** Assert the wheel includes `app-cognition-v2.js`, `app-cognition-v2.css`, all existing city/workbench assets, report template/CSS, city packs, fictional persona/creative catalogs, and no source-checkout absolute path.

- [ ] **Step 3: Add wheel smoke expectations.** From a fresh temporary directory, run offline doctor, schema-4 rules and mock workbench runs, V10 metrics, replay, study, report, and read-only inspector endpoints with external networking prohibited.

- [ ] **Step 4: Add loopback provider smoke expectations.** Start only the bounded fake server, perform the exact V2 compatibility check and provider run, assert funded/cache/fallback provenance, remove provider/cache/keyring/environment access, replay, and verify source hashes are unchanged.

- [ ] **Step 5: Add frozen smoke expectations.** Exercise the same rules/mock/provider/replay/API/report contract against the exact executable path passed to `smoke_frozen.py`; assert every other network destination fails.

- [ ] **Step 6: Extend the secret-redaction corpus.** Parameterize nested credential-shaped values through compatibility responses, usage, fallback errors, metrics, comparisons, studies, reports, API DTOs, DOM text, URLs, logs, and exceptions; assert no secret marker or sensitive member name escapes.

- [ ] **Step 6a: Add the V10 compute/latency separation test.** In `test_v10_rules_maximum_run_reports_compute_without_provider_wait`, build the supported 30-agent/7-day rules fixture, measure pure preparation/local resolution/application separately, print the deterministic counts and serialized sizes, and assert its elapsed values are absent from all canonical artifacts. In `test_loopback_provider_wait_is_operational_not_scientific_evidence`, use one fixed-delay loopback fake provider, measure provider wait outside pure planning/application, assert no external request, and assert neither wall-clock value appears in metrics, studies, receipts, replay hashes, or canonical run bytes. Record the observation without inventing a provider latency SLO.

- [ ] **Step 7a: Run the exact documentation red node.**

  Run: `uv run pytest tests/packaging/test_documentation.py::test_bounded_cognition_public_contract_is_documented -q`

  Expected: FAIL with `AssertionError: bounded cognition mode matrix is missing` because the public documentation does not yet contain the exact V10 mode/authority/replay contract.

- [ ] **Step 7b: Run the exact release-smoke red nodes before changing either script.**

  Run: `uv run pytest tests/packaging/test_smoke_release.py::test_smoke_release_exercises_v10_bounded_cognition tests/packaging/test_smoke_frozen.py::test_smoke_frozen_exercises_v10_bounded_cognition -q`

  Expected: FAIL because the current smoke entry points do not create, inspect, study, report, and offline-replay schema-4/V10 rules/mock/loopback-provider runs.

- [ ] **Step 7c: Run the new resource/redaction/performance tests as characterization gates.**

  Run: `uv run pytest tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/security/test_redaction_corpus.py tests/integration/test_city_cognition_v2_performance.py -q`

  Expected: PASS if Tasks 1-7 already satisfy the additive resource, secret, and compute-versus-wait contracts. Any failure is a substantive owning-task defect: add a minimal failing regression there, fix it, and rerun this command before Steps 8-10. Do not alter smoke scripts or documentation to conceal such a failure.

- [ ] **Step 8: Update public documentation.** State what is implemented, keep all people/data fictional, distinguish city-map illustration from real traffic fidelity, reject sales/individual/MBTI/causal claims, and preserve explicit remaining limitations.

- [ ] **Step 9: Extend `smoke_release.py`.** Use packaged resources and installed commands only, return no dangling temporary path, bound all waits/bodies, and clean temporary credentials/cache/workspaces in `finally` without printing secrets.

- [ ] **Step 10: Extend `smoke_frozen.py`.** Use the passed executable for every product command, keep the fake provider loopback-only, preserve the external-network guard, and verify no source module import is required.

- [ ] **Step 11: Run focused documentation/package/security tests.**

  Run: `uv run pytest tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py tests/packaging/test_smoke_frozen.py tests/security/test_redaction_corpus.py -q`

  Expected: PASS.

- [ ] **Step 11a: Run the V10 compute/latency evidence tests.**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_performance.py -q -s`

  Expected: PASS with compute and loopback-provider waiting printed separately and absent from scientific artifacts.

- [ ] **Step 12a: Build the exact wheel.**

  Run: `uv build --no-sources`

  Expected: the exact versioned wheel exists under `dist/`.

- [ ] **Step 12b: Smoke the exact wheel outside the checkout.**

  Run: `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`

  Expected: PASS with a single value-free success line and no external network access.

- [ ] **Step 13a: Build the exact native PyInstaller executable when PyInstaller is available on this host.**

  Run: `uv run pyinstaller packaging/adlife.spec --clean --noconfirm`

- [ ] **Step 13b: Smoke the exact native executable built in Step 13a.**

  ```powershell
  $frozenExecutable = if (Test-Path -LiteralPath 'dist/adlife/adlife.exe') { 'dist/adlife/adlife.exe' } else { 'dist/adlife/adlife' }
  uv run python scripts/smoke_frozen.py $frozenExecutable
  if ($LASTEXITCODE -ne 0) {
      throw "frozen smoke failed"
  }
  ```

  Expected: PASS. If PyInstaller or a required native tool is genuinely unavailable, record the exact environmental reason; do not claim this gate passed.

- [ ] **Step 14a: Run focused formatting.**

  Run: `uv run ruff format --check scripts/smoke_release.py scripts/smoke_frozen.py tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py tests/packaging/test_smoke_frozen.py tests/security/test_redaction_corpus.py tests/integration/test_city_cognition_v2_performance.py`

- [ ] **Step 14b: Run focused lint.**

  Run: `uv run ruff check scripts/smoke_release.py scripts/smoke_frozen.py tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py tests/packaging/test_smoke_frozen.py tests/security/test_redaction_corpus.py tests/integration/test_city_cognition_v2_performance.py`

- [ ] **Step 14c: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 14d: Check the unstaged diff.**

  Run: `git diff --check`

- [ ] **Step 15a: Stage only Task 8 files.**

  Run: `git add -- README.md CHANGELOG.md docs/architecture.md docs/city-pilot.md docs/cli-reference.md docs/installation.md docs/investor-demo.md docs/quickstart.md docs/reproducibility.md docs/releasing.md docs/methodology/model-card.md docs/methodology/limitations.md docs/methodology/experiment-protocol.md docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md scripts/smoke_release.py scripts/smoke_frozen.py tests/packaging/test_documentation.py tests/packaging/test_resources.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py tests/packaging/test_smoke_frozen.py tests/security/test_redaction_corpus.py tests/integration/test_city_cognition_v2_performance.py`

- [ ] **Step 15b: Check staged Task 8 whitespace.**

  Run: `git diff --cached --check`

- [ ] **Step 15c: Verify the staged Task 8 path set.**

  Run: `git diff --cached --name-only`

  Expected: only the twenty-three Task 8 paths listed in Step 15a.

- [ ] **Step 15d: Commit Task 8.**

  Run: `git commit -m "docs(cognition): document and package bounded signals"`

- [ ] **Step 15e: Push the checked Task 8 commit.**

  Run: `git push origin main`

---

### Task 9: Run release certification, performance evidence, and safe preview replacement

**Files:** No product files. This is an evidence and handoff task; if a gate reveals a defect, return to the owning task, add a failing regression test there, fix it, rerun its focused suite, and make one exact-path fix commit before restarting Task 9.

**Interfaces:**

- Consumes: the complete repository at the Task 8 commit.
- Produces: terminal/log evidence for the maintainer report and one reachable hidden-window local preview at `http://127.0.0.1:50050/`.

- [ ] **Step 1a: Confirm the branch.**

  Run: `git branch --show-current`

  Expected: `main`.

- [ ] **Step 1b: Confirm the worktree is clean.**

  Run: `git status --short`

  Expected: no output.

- [ ] **Step 1c: Record the unchanged remote.**

  Run: `git remote -v`

  Expected: only the already configured remote; do not add or edit one.

- [ ] **Step 2a: Synchronize the exact lock.**

  Run: `uv sync --locked --all-groups`

- [ ] **Step 2b: Run repository formatting.**

  Run: `uv run ruff format --check .`

- [ ] **Step 2c: Run repository lint.**

  Run: `uv run ruff check .`

- [ ] **Step 2d: Run strict typing.**

  Run: `uv run mypy src`

- [ ] **Step 3a: Run the full suite with the ambient hash seed.**

  Run: `uv run pytest -q`

- [ ] **Step 3b: Run the full suite with hash seed 0.**

  ```powershell
  $env:PYTHONHASHSEED='0'
  try {
      uv run pytest -q
      if ($LASTEXITCODE -ne 0) {
          throw "hash-seed-0 full suite failed"
      }
  }
  finally {
      Remove-Item Env:PYTHONHASHSEED -ErrorAction SilentlyContinue
  }
  ```

- [ ] **Step 3c: Run the full suite with hash seed 12345.**

  ```powershell
  $env:PYTHONHASHSEED='12345'
  try {
      uv run pytest -q
      if ($LASTEXITCODE -ne 0) {
          throw "hash-seed-12345 full suite failed"
      }
  }
  finally {
      Remove-Item Env:PYTHONHASHSEED -ErrorAction SilentlyContinue
  }
  ```

  Expected: all three runs have the same pass count and no required gate is hidden by a skip.

- [ ] **Step 4: Run branch coverage without lowering the floor.**

  Run: `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`

  Expected: PASS at or above the configured 85% floor.

- [ ] **Step 5: Run browser suites with a real installed browser required.**

  ```powershell
  $env:ADLIFE_REQUIRE_BROWSER='1'
  try {
      uv run pytest tests/browser -q
      if ($LASTEXITCODE -ne 0) {
          throw "required browser suite failed"
      }
  }
  finally {
      Remove-Item Env:ADLIFE_REQUIRE_BROWSER -ErrorAction SilentlyContinue
  }
  ```

  Expected: PASS with no silent browser skip and no console error/unexpected request.

- [ ] **Step 6: Run the maximum deterministic performance gate.**

  Run: `uv run pytest tests/integration/test_maximum_run.py::test_maximum_run_30_agents_7_days_within_the_performance_ceiling -q -s`

  Expected: PASS; record wall time, peak memory, event count, database/run size, and report size exactly as printed.

- [ ] **Step 7a: Measure the maximum V10 rules compute path.**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_performance.py::test_v10_rules_maximum_run_reports_compute_without_provider_wait -q -s`

  Expected: PASS with deterministic planning/application time, counts, and artifact sizes printed separately.

- [ ] **Step 7b: Measure loopback-provider waiting separately.**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_performance.py::test_loopback_provider_wait_is_operational_not_scientific_evidence -q -s`

  Expected: PASS; provider wait is reported separately, no wall-clock value enters canonical evidence, and no provider SLO is inferred.

- [ ] **Step 8a: Rebuild the wheel after all tests.**

  Run: `uv build --no-sources`

- [ ] **Step 8b: Resmoke the exact wheel.**

  Run: `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`

- [ ] **Step 8c: Rebuild the native executable when PyInstaller is available.**

  Run: `uv run pyinstaller packaging/adlife.spec --clean --noconfirm`

- [ ] **Step 8d: Resmoke the exact native executable built in Step 8c.**

  ```powershell
  $frozenExecutable = if (Test-Path -LiteralPath 'dist/adlife/adlife.exe') { 'dist/adlife/adlife.exe' } else { 'dist/adlife/adlife' }
  uv run python scripts/smoke_frozen.py $frozenExecutable
  if ($LASTEXITCODE -ne 0) {
      throw "frozen smoke failed"
  }
  ```

  Expected: every locally available build passes. Record an unavailable native tool honestly rather than marking success.

- [ ] **Step 9a: Check the installed version command.**

  Run: `uv run adlife --version`

- [ ] **Step 9b: Run the human offline doctor.**

  Run: `uv run adlife doctor --offline`

- [ ] **Step 9c: Run the machine-readable offline doctor.**

  Run: `uv run adlife --format json doctor --offline`

- [ ] **Step 9d: Run the human headless demo.**

  Run: `uv run adlife demo --headless`

- [ ] **Step 9e: Run the machine-readable headless demo.**

  Run: `uv run adlife --format json demo --headless`

- [ ] **Step 9f: Check repository whitespace.**

  Run: `git diff --check`

- [ ] **Step 9g: Confirm gates left the worktree clean.**

  Run: `git status --short`

  Expected: no output.

- [ ] **Step 10: Start an upgraded candidate on port 50051 without a visible terminal.** Use the same preview workspace and approved provider-origin argument, redirect stdout/stderr to temporary files outside the repository, retain the returned PID, and pass `-WindowStyle Hidden` if `Start-Process` is used.

- [ ] **Step 11: Certify the candidate before touching port 50050.** Require HTTP 200 for `/`, `/api/workbench`, `/runs/001`, and `/api/runs/001/meta`; run a browser smoke for workbench controls, V9 run `001`, and a fresh V10 rules/mock fixture; require no console errors or external network request.

- [ ] **Step 12: Resolve the existing port-50050 process safely.** Query the listener PID and command line, verify it is the intended AdLife preview using workspace `C:\your workspace\github\adlife-owner-preview-20261012-123157`, and refuse to stop an unrelated process.

- [ ] **Step 13: Replace the preview with minimal downtime.** Stop only the verified old preview, launch the upgraded command on port 50050 in a hidden background process with redirected logs, and retain its PID. Do not open a visible PowerShell, cmd, or terminal window.

- [ ] **Step 14: Verify the final preview and preserve the candidate fallback.** Require HTTP 200 for `/`, `/api/workbench`, `/runs/001`, and `/api/runs/001/meta`; perform the final browser smoke. Keep the verified port-50051 candidate alive until this passes. If port 50050 startup still fails after one clean retry, leave the candidate available, report the exact failure, and do not claim replacement or rollback succeeded.

- [ ] **Step 15: Stop the port-50051 candidate only after port 50050 passes.** Verify the PID still belongs to the candidate before stopping it; remove only its temporary logs/PID files after reading them for errors.

- [ ] **Step 16a: Capture final worktree status.**

  Run: `git status --short`

- [ ] **Step 16b: Capture final whitespace evidence.**

  Run: `git diff --check`

- [ ] **Step 16c: Capture the final focused history.**

  Run: `git log --oneline -n 20`

- [ ] **Step 16d: Capture the unchanged remote configuration.**

  Run: `git remote -v`

  Expected: clean status, no diff errors, focused commits on `main`, and unchanged remotes.

---

## Completion Evidence

This plan is complete only when V10 metrics keep purchase intention in the rule baseline, comparisons/studies calculate no provider-dependent campaign effect, reports and APIs expose only verified non-secret evidence, the workbench can create exact schema-4/V10 runs, the inspector traces baseline-to-dual-state evidence, rules/mock operate offline, loopback provider smoke succeeds, replay remains provider-free, the exact wheel and available native executable pass clean-room smoke, the full release matrix is green, performance evidence separates provider waiting from deterministic compute, and the upgraded preview remains reachable without a visible terminal window.

The final maintainer report must preserve the research limits: bounded categories and fixed mappings are synthetic assumptions; the simulator is not validated consumer psychology, observed resident behavior, representative real-city traffic, causal advertising lift, purchase probability, sales prediction, MBTI validation, or attestation of a provider's undeclared model weights.
