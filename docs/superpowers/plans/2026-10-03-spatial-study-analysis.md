# Spatial Repeated-Seed Study and Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable schema-v6 response metrics, deterministic repeated-seed spatial
analysis, and a secure self-contained static report over verified saved city runs.

**Architecture:** Pure core modules derive response receipts, typed scalar observations,
matched comparisons and keyed-bootstrap statistics. A bounded definition loader and city
application service stream verified artifacts into a small result. CLI and reporting
adapters expose that immutable result without rerunning, mutating, or rediscovering runs.

**Tech stack:** Python 3.11-3.13, strict Pydantic v2, SHA-256 canonical JSON/JSONL,
Typer, Jinja2, vanilla HTML/CSS, pytest/Hypothesis/Playwright, Ruff, mypy and uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-03-spatial-study-analysis-design.md`

## Global constraints

- Work only on `main`; never change `defense-ready`.
- Keep `src/adlife/core` free of FastAPI, Typer, Textual, SQLite, Plotly and provider
  adapters.
- Preserve C4a attention metrics/commands and schema-v1-v6 city artifact meanings.
- Treat one seed pair as the experimental unit; never pool agent/event numerators across
  seeds.
- Never label seed variation population confidence, causal effect, validation, sales, or
  real behavior.
- Keep response state overall/campaign scoped; never allocate nonlinear committed state to
  a channel.
- Derive only from fully verified artifacts; do not persist derived metrics into source
  runs and never repair them.
- Keep analysis/report offline, bounded, deterministic and independent of directory order,
  mapping order, `random.Random`, wall clock and `PYTHONHASHSEED`.
- Keep report publication atomic and no-clobber; no arbitrary output path, JavaScript,
  remote resource, raw input/event/provider body, credential, environment value or local
  root may enter it.
- Every behavior starts with a RED externally meaningful test, then narrow/related/full
  verification. Commit and push each logical task because the owner explicitly authorized
  it; never add or modify a remote.

## Review focus

- A valid-looking fabricated response evaluation must not pass because its internal fields
  agree; metrics must anchor it back to response input and attention evidence.
- Mixed-channel same-minute responses must not produce a made-up channel state effect.
- A constant nonzero paired difference must have nullable, not zero, standardized effect.
- Adding or reordering metrics must not change another metric's bootstrap interval.
- Seed equality is not enough for common random numbers; exact assignment/trace/place
  receipts must match within each pair.
- A report conflict or injected write/fsync/link failure must preserve the old file and
  leave no temporary file.
- Analysis/reporting must leave every source run byte-identical.

---

### Task 0: Close the credential-shaped run-ID persistence channel

**Files:**
- Modify: `src/adlife/core/ports/run_store.py`
- Modify: `src/adlife/cli/commands/run.py`
- Modify: `tests/contract/test_run_store.py`
- Modify: `tests/cli/test_city_run.py`

- [x] **Step 1: Reproduce the gap.** Prove a known vendor-token-shaped lowercase slug is
  accepted as a city run ID, persisted, and echoed.
- [x] **Step 2: Add RED port and CLI regressions.** Require typed refusal, no echo, exit 2,
  and no directory.
- [x] **Step 3: Reuse the repository's narrow secret/contact screen in the shared run-ID
  validator.** Route the zone CLI through the same validator and use a generic diagnostic.
- [x] **Step 4: Run persistence, city/zone CLI and redaction suites plus Ruff/mypy.** Result:
  `1201 passed, 12 skipped`; Ruff and strict mypy passed.
- [x] **Step 5: Commit and push.** Commit: `e7e0d09 fix(security): reject credential-shaped run ids`.

### Task 1: Define exact response metrics and comparisons

**Files:**
- Create: `src/adlife/core/experiments/spatial_response_metrics.py`
- Create: `src/adlife/core/experiments/spatial_response_comparison.py`
- Create: `src/adlife/core/experiments/spatial_observations.py`
- Modify: `src/adlife/core/experiments/__init__.py`
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/cli/commands/city_metrics.py`
- Modify: `src/adlife/cli/commands/city_compare.py`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/cli/commands/city_view.py`
- Create: `tests/unit/experiments/test_spatial_response_metrics.py`
- Create: `tests/unit/experiments/test_spatial_response_comparison.py`
- Create: `tests/unit/experiments/test_spatial_observations.py`
- Create: `tests/property/test_spatial_response_metrics_order.py`
- Create: `tests/property/test_spatial_response_comparison_order.py`
- Modify: city analysis, CLI, API, browser and architecture tests.

**Interfaces:**

```python
def spatial_response_assumption_structure_sha256(
    response_input: SpatialResponseInput,
    scenario: SpatialCampaignScenario,
) -> str: ...


def derive_spatial_response_metrics(
    response_input: SpatialResponseInput,
    response: SpatialResponseEvaluation,
    *,
    scenario: SpatialCampaignScenario,
    opportunities: SpatialOpportunityEvaluation,
    attention: SpatialAttentionEvaluation,
    attention_metrics: SpatialMetrics,
    agent_ids: Sequence[str],
) -> SpatialResponseMetrics: ...


def compare_spatial_response_metrics(
    control: SpatialResponseMetrics,
    treatment: SpatialResponseMetrics,
    *,
    control_run_id: str,
    treatment_run_id: str,
) -> SpatialResponseMetricsComparison: ...


def spatial_metric_observations(
    attention: SpatialMetrics,
    response: SpatialResponseMetrics | None = None,
) -> tuple[SpatialMetricObservation, ...]: ...
```

- [ ] **Step 1: Write RED golden receipt tests.** Pin one-notice literals, zero-notice with
  nonzero initial state, a no-response campaign, multi-campaign weighting, saturation,
  planned-versus-committed recall, and mixed-channel response without channel state fields.
- [ ] **Step 2: Write RED integrity/property tests.** Cover strict/bounded models, all
  response-input/attention anchors, tampered first/untouched states, response/notice count
  mismatch, canonical order, assumption-hash sensitivity to channel/frequency cap and
  invariance to purely physical placement changes, credential-shaped copied identifiers,
  hash seeds and input permutations.
- [ ] **Step 3: Implement minimal pure response receipts.** Use separate count/reach/
  frequency/direct-mean/state receipt types, `math.fsum`, exact sources, positive-zero
  normalization and independent revalidation.
- [ ] **Step 4: Write RED comparison/observation tests.** Pin exact A/A, arm-swap negation,
  campaign-set refusal, independent opportunity/assumption classifications, canonical key
  grammar, state-channel exclusion and exact source receipts.
- [ ] **Step 5: Implement comparison and typed observation seams.** Keep C4a unchanged.
- [ ] **Step 6: Add read-only application, CLI layer switches and API.** Defaults retain
  exact attention output; response requires v6; constructor rederives supplied response
  metrics; older viewer runs return 404; all mutation methods return 405.
- [ ] **Step 7: Run focused/related suites, Ruff/mypy, inspect diff, commit and push.**
  Commit: `feat(city): derive spatial response metrics`.

### Task 2: Define and safely load repeated-seed study inputs

**Files:**
- Create: `src/adlife/core/domain/spatial_study.py`
- Create: `src/adlife/city/study_loader.py`
- Create: `tests/unit/city/test_spatial_study_contract.py`
- Create: `tests/unit/city/test_spatial_study_loader.py`
- Create: `tests/property/test_spatial_study_order.py`
- Create: `tests/security/test_spatial_study_security.py`

**Interfaces:**

```python
class SpatialStudyPair(DomainModel): ...


class SpatialStudyDefinition(DomainModel): ...


MAX_SPATIAL_STUDY_BYTES = 65_536


def parse_spatial_study_definition_json(
    document: str | bytes,
) -> SpatialStudyDefinition: ...


def load_spatial_study_definition(path: Path) -> SpatialStudyDefinition: ...
```

- [ ] **Step 1: Write RED strict contract tests.** Pin exact schemas/enums, two-to-100
  pairs, contiguous `0..N-1` seeds, canonical sorting, reuse rules, A/A versus contrast
  identity, fingerprint stability, portable/reserved/credential IDs and absence of free
  prose/path fields.
- [ ] **Step 2: Write RED parser/loader/security tests.** Cover duplicate JSON keys,
  bool/version/NaN/Infinity, extra fields, invalid UTF-8, nesting, 64 KiB bound, safe generic
  errors and secret non-echo.
- [ ] **Step 3: Implement minimum frozen strict domain and bounded adapter loader.** Reuse
  shared run-ID validation without leaking adapter errors into core.
- [ ] **Step 4: Run focused/domain/security/architecture suites plus Ruff/mypy.**
- [ ] **Step 5: Inspect diff, commit and push.**
  Commit: `feat(city): define repeated-seed studies`.

### Task 3: Analyze verified run pairs with independently keyed statistics

**Files:**
- Create: `src/adlife/core/experiments/spatial_study.py`
- Create: `src/adlife/city/studies.py`
- Create: `src/adlife/cli/commands/city_study.py`
- Modify: `src/adlife/cli/app.py`
- Create: `tests/unit/experiments/test_spatial_study.py`
- Create: `tests/integration/test_city_spatial_study.py`
- Create: `tests/integration/test_spatial_study_memory.py`
- Create: `tests/cli/test_city_study.py`

**Interfaces:**

```python
def spatial_paired_statistics(
    differences: Mapping[str, Sequence[float]],
    sources: Mapping[str, tuple[SpatialStudyMetricArtifact, ...]],
) -> tuple[SpatialPairedStatistic, ...]: ...


def analyze_stored_spatial_study(
    store: CityRunStore,
    definition: SpatialStudyDefinition,
) -> SpatialStudyResult: ...
```

- [ ] **Step 1: Write RED statistic goldens.** Pin exact two-, twenty- and fifty-seed
  documents; bootstrap seed derivation, rejection sampling and indices; all-zero and
  constant vectors; positive/negative/zero-heavy/exact-0.8 directions; nullable effect;
  even-sample median, all-zero versus directional agreement, metric insertion/order/hash-
  seed independence, and the exact wrapped SplitMix64 constants/byte order. Refuse vectors
  outside 2..100, mixed lengths, bool/non-finite members, more than 328/invalid/duplicate
  keys, mismatched source keys, wrong-but-allowed source tuples and unknown artifact
  literals before resampling.
- [ ] **Step 2: Implement pure result/statistics models.** Use versioned SplitMix64 and one
  discarded 10,000-mean vector per metric; no p-values or stateful global generator.
- [ ] **Step 3: Write RED artifact-analysis cases.** Independently vary every within-pair
  and cross-study provenance field, schemas, models, keys, campaign sets, arm identities,
  classifications and A/A invariants. Cover v5 attention, v6 attention, v6 response,
  mixed-schema refusal, maximum pairs and source tree hashes.
- [ ] **Step 3a: Write bypass/tamper result tests.** Construct results with changed scalar
  values/numerators/denominators/sources/deltas, definition/manifest hashes,
  classifications/counts, statistic means/intervals/directions, order and duplicate keys;
  require full coherence recomputation before CLI/report use.
- [ ] **Step 4: Implement streaming application projection.** Hold at most one full loaded
  run projection at a time with only a bounded constant number of revalidation copies;
  retain `O(largest run + bounded result)`, not `O(run count)`; reuse same-run A/A; copy
  only bounded public city provenance; hash canonical manifests; never copy paths/raw
  records/response inputs into the result. Enforce a 32 MiB canonical result ceiling.
- [ ] **Step 5: Write RED CLI contracts and implement `city-study`.** Pin exact JSON/human
  output, empty JSON stderr, generic safe failures and exit classes 1/2/4/130. Command is
  read-only and accepts no discovery/output option.
- [ ] **Step 6: Run focused/related/performance suites, Ruff/mypy, inspect diff, commit and
  push.** Include load/project/release sequencing, constant-factor peak memory, and a
  maximum 100-pair/20-campaign serialized-result bound. Commit:
  `feat(city): analyze repeated-seed studies`.

### Task 4: Render and publish a zero-JavaScript spatial study report

**Files:**
- Create: `src/adlife/reporting/spatial_html.py`
- Create: `src/adlife/reporting/templates/spatial-report.html.j2`
- Create: `src/adlife/reporting/static/spatial-report.css`
- Create: `src/adlife/cli/commands/city_report.py`
- Modify: `src/adlife/cli/app.py`
- Modify: `.gitattributes`
- Create: `tests/unit/reporting/test_spatial_html.py`
- Create: `tests/cli/test_city_report.py`
- Create: `tests/browser/test_spatial_report_browser.py`
- Create: `tests/security/test_spatial_report_security.py`
- Modify: packaging resource/wheel tests.

**Interfaces:**

```python
def render_spatial_study_html(result: SpatialStudyResult) -> bytes: ...


def publish_spatial_study_report(
    result: SpatialStudyResult,
    root: Path,
) -> SpatialReportReceipt: ...
```

- [ ] **Step 1: Apply `frontend-design` and write RED document tests.** Pin deterministic
  LF/UTF-8 bytes, exact result values, attention/response sections, disclosures,
  assumptions, limitations, Persian `<bdi>`, autoescaping, no scripts/external resources,
  CSS CSP hash, 8 MiB ceiling and no NaN/Infinity.
- [ ] **Step 2: Implement the strict static renderer.** Use `StrictUndefined`, autoescape,
  trusted packaged CSS only, system fonts and semantic field-ledger structure.
- [ ] **Step 3: Write RED no-clobber/fault CLI tests.** Cover fixed contained destination,
  file/directory/symlink/dangling conflicts, report-directory junction/symlink, injected
  open/write/file-fsync/link/directory-fsync failures, Python-3.11 Windows reparse-point
  detection, temporary cleanup, safe JSON receipt, exit codes and source immutability.
  File-fsync/link failures are pre-commit and leave no destination; a post-link directory-
  fsync failure is non-fatal and leaves the complete published destination.
- [ ] **Step 4: Implement atomic link publication and `city-report`.** Never fall back to
  replace. Return relative POSIX path/hash/bytes only.
- [ ] **Step 5: Write/run browser and security tests.** Disable JavaScript; assert semantic
  headings/tables, keyboard focus, visible focus, 390 px containment, print/forced-colors,
  contrast, Persian directionality, hostile markup safety and zero network requests. Render
  the maximum statistic/pair-provenance shape and prove it remains deterministic and below
  the 8 MiB report ceiling.
- [ ] **Step 6: Run resource/wheel/CLI/report/security suites, Ruff/mypy, inspect diff,
  commit and push.** Commit: `feat(report): add spatial study evidence report`.

### Task 5: Reconcile public contracts and certify installed C4b behavior

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: production target design/roadmap.
- Modify: `scripts/smoke_release.py`
- Modify: packaging documentation/smoke tests.
- Modify: this plan with exact execution evidence.

- [ ] **Step 1: Write RED executable documentation/smoke assertions.** Pin commands,
  schemas/models/scopes, formulas, seed unit, bootstrap, classifications, report security,
  v5/v6 behavior and explicit limitations.
- [ ] **Step 2: Reconcile public documentation.** Mark bounded C4b evidence complete but
  keep broader C4/social, job/workbench, calibration, authentication and external validity
  open. Never call 50 seeds registered or claim city Git/lock provenance.
- [ ] **Step 3: Extend exact-wheel smoke.** Create schema-v6 pairs for seeds 0 and 1, run
  response metrics, A/A `city-study`, and `city-report`; verify exact receipts/CSP/no script,
  second-write conflict, source tree hashes and network guard.
- [ ] **Step 4: Run focused packaging/documentation/security/architecture suites.**
- [ ] **Step 5: Run the full release-quality matrix.** Locked sync, Ruff format/lint,
  strict mypy, full pytest, hash seeds 0/12345, branch coverage, build, exact-wheel smoke,
  doctor/demo, spatial study/report performance, `git diff --check`, and supported frozen
  smoke. Make no claim for unexecuted OS-specific builds.
- [ ] **Step 6: Request independent whole-diff review and fix every critical/important
  finding test-first.**
- [ ] **Step 7: Record exact evidence, inspect status, commit and push.**
  Commit: `docs(city): document repeated-seed studies`.
