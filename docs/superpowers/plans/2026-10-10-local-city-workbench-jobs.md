# Immutable Workbench Runs and Bounded Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver D1b of the approved local city workbench: persist the complete accepted workbench input in schema-v7 city artifacts, execute one validated run at a time through truthful bounded job phases, and expose verified completed-run discovery without accepting paths or blocking an HTTP request on simulation work.

**Architecture:** Extend the existing schema-v6 city receipt rather than creating a second artifact family. Split the current runner into pure deterministic preparation and no-clobber publication seams, then bind the canonical `WorkbenchRunInput` sidecar and creative snapshot to a schema-v7 manifest. Keep the process-local worker, job state machine, workspace checks, FastAPI routes, and operational timestamps in `adlife.city`; keep `adlife.core` adapter-free. Every completed result is independently reloaded through `CityRunStore` before it becomes visible.

**Tech Stack:** Python 3.11-3.13, uv, strict Pydantic v2, dataclasses, one `ThreadPoolExecutor` worker, FastAPI/Starlette, canonical JSON/SHA-256, pytest/Hypothesis, Ruff, mypy.

**Spec:** [Local City Workbench Design](../specs/2026-10-10-local-city-workbench-design.md), sections 6.2-8; D1b in the [production roadmap](2026-09-28-production-city-platform-roadmap.md).

## Global Constraints

- Preserve all schema-v1-through-v6 artifacts, CLI commands, replay semantics, viewer APIs, and deterministic evidence byte-for-byte.
- A CLI run remains schema v6 unless a complete validated `WorkbenchRunInput` is explicitly supplied. Schema v7 is only the schema-v6 response receipt plus the canonical workbench sidecar binding.
- Keep `src/adlife/core` free of FastAPI, Typer, Textual, SQLite, storage adapters, Uvicorn, and provider implementations.
- Persist only normalized accepted input and the complete selected fictional creative snapshot. Never persist CSRF tokens, job IDs/timestamps, request bodies, headers, credentials, absolute workspace paths, or exception text.
- Validate and freeze a job synchronously before acceptance. Invalid input creates no job and no run directory.
- Permit at most one queued/evaluating/persisting/verifying job. Keep only a bounded number of terminal records and use one process-local worker with no subprocess or shell command.
- Cancellation is cooperative only in `queued` or `evaluating`; after the atomic transition to `persisting`, return a conflict and finish publication/verification.
- Recheck the pinned workspace and run-ID collision before queueing and again before publication. `CityRunStore` remains the final no-clobber authority.
- Only independently loaded and verified artifacts appear in completed-run discovery or completed job summaries. Partial/corrupt directories are never silently advertised as completed.
- Keep every HTTP collection, query, identifier, error envelope, and operational string bounded. Clients never submit filesystem paths.
- Use TDD for every behavior change: observe the narrow regression fail, make the smallest fix, run related suites, inspect the diff, then commit and push the verified logical group.

## Review Focus

- A missing, non-canonical, tampered, oversized, symlinked, or creative-mismatched `inputs/workbench.json` must make a schema-v7 artifact unreadable and unreplayable: Tasks 1 and 3.
- Preparation must not reserve or write a run; publication must refuse collisions; the compatibility wrapper must preserve schema-v1-through-v6 behavior: Tasks 2 and 3.
- Invalid drafts, duplicate run IDs, and a busy worker must produce stable refusals without creating jobs or artifacts: Tasks 4 and 5.
- A cancellation before publication must leave no run artifact, while a late cancellation must return a stable conflict and allow verification to finish: Task 4.
- Run discovery and completed job results must be derived only from a fresh verified `CityRunStore.load()`, with corrupt entries screened and never treated as successful: Tasks 4-6.

---

## Scope boundary and file map

| File | D1b responsibility |
| --- | --- |
| `src/adlife/core/domain/city_run.py` | Strict `CityRunManifestV7` and versioned parser/union compatibility. |
| `src/adlife/city/workbench_input.py` | Strict parser for canonical persisted workbench input. |
| `src/adlife/city/runs.py` | `PreparedCityRun`, deterministic `prepare_city_run()`, no-clobber `publish_city_run()`, compatibility wrapper, v7 replay checks. |
| `src/adlife/city/run_store.py` | Save/load the bounded canonical sidecar and verify manifest/creative/scenario bindings. |
| `src/adlife/city/analysis.py`, `studies.py`, `web.py` | Treat v7 as v6 scientific evidence plus workbench provenance without rewriting its source schema; provide shared verified-view responses. |
| `src/adlife/core/experiments/spatial_*.py` | Extend strict source-run version contracts to 7 wherever schema-v6 response evidence is accepted. |
| `src/adlife/city/workbench_runs.py` | Pinned workspace run-store service and verified paginated run summaries. |
| `src/adlife/city/workbench_jobs.py` | One-worker state machine, bounded terminal history, cooperative cancellation, safe summaries. |
| `src/adlife/city/workbench_web.py` | Job submission/status/cancellation, run discovery, truthful capabilities and error mappings. |
| `tests/unit/city/test_city_run_contract.py` | V7 contract and parser compatibility. |
| `tests/integration/test_city_workbench_artifacts.py` | Sidecar publication/load/replay/tamper/compatibility regressions. |
| `tests/unit/city/test_city_run_preparation.py` | Prepare/publish separation and wrapper equivalence. |
| `tests/unit/city/test_workbench_runs.py` | Verified bounded discovery and workspace containment. |
| `tests/unit/city/test_workbench_jobs.py` | Legal phases, one-worker bound, cancellation/failure/history contracts. |
| `tests/unit/city/test_workbench_web.py` | Complete D1b HTTP boundary and no-path/no-long-request behavior. |
| `tests/unit/city/test_city_web.py`, browser viewer tests | Legacy/run-scoped read parity, API-base composition, and schema-v7 projection coverage. |
| `docs/architecture.md`, `docs/cli-reference.md`, roadmap, `CHANGELOG.md` | Truthful shipped D1b contract and remaining D2-D4 scope. |

The new D1b API adds:

| Method/path | Result |
| --- | --- |
| `GET /api/workbench` | Capabilities with jobs/execution true, current active-job summary, and deterministic-rules only. |
| `GET /api/runs?offset=N&limit=N` | Stable bounded page of independently verified completed runs ordered by run ID. |
| `GET /api/jobs/{job_id}` | One bounded process-local job record. |
| `POST /api/jobs` | Revalidate/freeze the complete draft synchronously and return `202` with job/status URL. |
| `POST /api/jobs/{job_id}/cancel` | Request cooperative cancellation or return a stable phase conflict. |
| `GET /runs/{run_id}` | Existing immutable inspector HTML configured with a validated run-scoped API base. |
| `GET /api/runs/{run_id}/...` | Existing verified viewer reads mirrored beneath a portable run ID. |

### Task 1: Define the strict schema-v7 receipt and persisted input parser

**Files:** Modify `src/adlife/core/domain/city_run.py`, `src/adlife/city/workbench_input.py`, `tests/unit/city/test_city_run_contract.py`, `tests/unit/city/test_workbench_input.py`.

**Interfaces (Consumes/Produces):**

- `CityRunManifestV7(CityRunManifestV6)` fixes `schema_version: Literal[7]`, `workbench_input_schema_version: Literal[1]`, and a lowercase 64-hex `workbench_input_sha256`.
- `CityRunManifestDocument` and `parse_city_run_manifest_json()` recognize 1-7 while retaining exact unsupported-version and extra-field refusals.
- `parse_workbench_run_input_json(data: str | bytes) -> WorkbenchRunInput` performs strict Pydantic parsing; storage separately enforces canonical bytes and size.

- [ ] Add RED tests for a valid v7 receipt, strict integer schema values, hash format, unknown/missing fields, v1-v6 parser compatibility, and unsupported v8 refusal.
- [ ] Add RED round-trip/refusal tests for persisted workbench input, including unknown fields, wrong creative snapshot, non-finite values, and malformed UTF-8/JSON.
- [ ] Run `uv run pytest tests/unit/city/test_city_run_contract.py tests/unit/city/test_workbench_input.py -q`; confirm RED at missing v7/parser behavior.
- [ ] Implement the smallest strict extensions; do not import workbench/application models into `adlife.core`.
- [ ] Run the narrow files, `uv run ruff check` on changed files, and `uv run mypy src`; require PASS.
- [ ] Inspect the diff for any altered v1-v6 contract, then commit as `feat(workbench): define schema v7 artifacts`.

### Task 2: Split deterministic preparation from publication

**Files:** Modify `src/adlife/city/runs.py`; create `tests/unit/city/test_city_run_preparation.py`; adjust directly affected runner tests only when their externally meaningful contract is unchanged.

**Interfaces (Consumes/Produces):**

- Frozen `PreparedCityRun` contains the manifest, verified pack, mobility, optional place/scenario/opportunity/attention/response inputs and evaluations, and optional complete workbench input. It contains no directory or store.
- `prepare_city_run(..., workbench_input: WorkbenchRunInput | None = None) -> PreparedCityRun` performs all deterministic evaluation in memory and no filesystem operation. In this task the optional value is retained/validated in memory only; schema-v7 manifest construction is enabled atomically with sidecar-aware save/load in Task 3.
- `publish_city_run(prepared, *, root: Path) -> StoredCityRun` saves once through `CityRunStore`, then returns the published in-memory result; independent job verification remains a caller step.
- `create_city_run(...)` remains a compatibility wrapper. Through the end of this task it produces the same schema-v1-through-v6 manifests and canonical bytes as before; Task 3 atomically enables v7 publication when a complete workbench input is present.

- [ ] Write RED tests proving preparation creates no root/directory, duplicate IDs are not silently overwritten, wrapper and explicit prepare/publish evidence are equal, and ordinary CLI-style response runs remain v6.
- [ ] Test that a supplied workbench input must match run ID, seed, agent count, days, city, scenario, response campaign/creative binding, and deterministic mobility, and that current workbench input refuses any non-`None` place set because that scientific choice is not represented by schema 1; mismatches fail before publication.
- [ ] Test identical accepted scientific inputs under different run IDs yield identical normalized evidence after removing receipt-owned run identifiers.
- [ ] Run `uv run pytest tests/unit/city/test_city_run_preparation.py -q`; confirm RED at missing seams.
- [ ] Extract the existing evaluation code without changing ordering, hashes, rule parameters, or exception types. Build v7 only when the complete workbench input is present.
- [ ] Run the narrow file plus `tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_response_run.py tests/cli/test_city_run.py -q`, Ruff, and mypy.
- [ ] Inspect canonical v6 fixture bytes and the adapter-free core boundary, then commit as `refactor(city): separate run preparation and publication`.

### Task 3: Persist, bind, load, and replay the workbench sidecar

**Files:** Modify `src/adlife/city/run_store.py`, `src/adlife/city/runs.py`; create `tests/integration/test_city_workbench_artifacts.py`; extend `tests/integration/test_city_run_replay.py` where useful.

**Interfaces (Consumes/Produces):**

- `StoredCityRun.workbench_input: WorkbenchRunInput | None` is present only for v7.
- `CityRunStore.save(..., workbench_input=None)` requires it exactly for v7 and writes canonical `inputs/workbench.json` before committing `run.json`.
- `CityRunStore.load()` bounds, parses, canonical-byte checks, and hashes the sidecar; verifies workbench city/settings/run ID, scenario/campaign IDs, and the complete creative snapshot hash against every persisted spatial campaign/response creative hash.
- `replay_city_run()` accepts v7 anywhere v6 response evidence is accepted and verifies the sidecar binding without letting it alter deterministic evaluation.
- Task 3 atomically enables `CityRunManifestV7` creation and publication only after save/load/replay understand the required sidecar. Existing metrics, study validation, comparison, CLI-view, and viewer projection contracts preserve the truthful source value `7`; they accept v7 in exactly the evidence paths that accept v6 and do not coerce it back to 6.

- [ ] Add RED integration tests for canonical v7 layout/load/replay and continued v6 readability.
- [ ] Add one mutation test each for missing, non-canonical, oversized, invalid, symlinked, hash-mismatched, run-setting-mismatched, city-mismatched, and creative-mismatched sidecars; every case must fail cleanly.
- [ ] Add strict projection tests proving v7 metrics/studies/comparisons/CLI-view responses retain `source_run_schema_version: 7`, while response-free schema constraints and legacy v5/v6 outputs remain unchanged. Extend every hard-coded v6 consumer found by repository search deliberately; do not use a catch-all version comparison.
- [ ] Inject a write failure before final manifest publication and prove no valid run can load; prove an existing run is never overwritten and temporary files are bounded/cleaned where the store promises cleanup.
- [ ] Hash all source artifact files before/after replay and assert replay never mutates them.
- [ ] Run `uv run pytest tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_run_replay.py -q`; confirm RED.
- [ ] Implement the sidecar as another bounded frozen input using existing canonical-byte and containment helpers. Extend all V4/V5/V6 type families deliberately to include V7 where response evidence semantics apply.
- [ ] Run the narrow/related store, replay, response, web, analysis, study and CLI suites; run Ruff and mypy.
- [ ] Inspect every filesystem path and version branch, then commit as `feat(workbench): bind immutable run inputs`.

### Task 4: Discover verified runs and execute one bounded job

**Files:** Create `src/adlife/city/workbench_runs.py`, `src/adlife/city/workbench_jobs.py`, `tests/unit/city/test_workbench_runs.py`, `tests/unit/city/test_workbench_jobs.py`.

**Interfaces (Consumes/Produces):**

- `WorkbenchRunRepository` owns `<workspace>/city-runs`, re-verifies the pinned workspace before every read/publication, validates run IDs, refuses collisions before queueing, publishes prepared runs, independently reloads completed runs, and returns bounded `WorkbenchRunSummary` values.
- Discovery enumerates only direct portable-ID directories in stable run-ID order, calls `CityRunStore.load()` for each candidate, and excludes incomplete/corrupt/unsafe entries while retaining a bounded diagnostic count rather than exposing paths/errors.
- Discovery examines at most 256 direct candidates per request; if more exist it reports a safe truncation flag instead of performing unbounded filesystem work. Pagination is applied to the verified, stably ordered bounded set.
- `CityJobManager` uses exactly one `ThreadPoolExecutor(max_workers=1)`, permits one active record, and retains at most 32 terminal records.
- Phases are `queued`, `evaluating`, `persisting`, `verifying`, then `completed`, `failed`, or `cancelled`. Public records contain ISO-8601 UTC operational times, safe codes/messages, and a verified run summary only on completion.
- Server job IDs use bounded process entropy and never affect scientific artifacts. `close()` stops acceptance and drains/shuts down safely for tests/app lifecycle.
- Strict frozen public DTOs pin exact response keys. `WorkbenchRunSummary` includes run ID/schema, city/scenario IDs and hashes, seed/days/agent/frame/evidence counts, channels, and `inspector_url`; `WorkbenchJobView` includes job/run IDs, phase, UTC timestamps, `cancellation_requested`, `can_cancel`, optional constant error, and optional completed summary. The paged result includes offset, limit, returned count, verified total within the bounded scan, scanned/corrupt counts, and `truncated`.
- Job registration and executor scheduling are one failure-safe operation: if `executor.submit()` refuses because of shutdown or injection, the prospective record is removed (or made a bounded terminal failure) and `_active_job_id` is cleared before a safe exception escapes. Submission racing `close()` can never strand a ghost active job.

- [ ] Write RED repository tests for empty discovery, stable pagination, verified v6/v7 inclusion, corrupt/partial/symlink exclusion, strict offset/limit bounds, collision checks, workspace replacement, and fresh-load behavior.
- [ ] Write RED job tests for the exact success transition sequence, at-most-one active job, bounded history, unknown IDs, queued/evaluating cancellation with no artifact, late-cancel conflict, evaluation/storage/verification failure, safe redaction, and manager shutdown.
- [ ] Fault-inject executor submission failure and a submit/close race; prove there is no ghost active job, no artifact, no leaked exception text, and a later manager query remains internally consistent.
- [ ] Use barriers/events in tests instead of sleeps. Assert the submit call returns before a blocked evaluation completes and no subprocess/shell path exists.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py -q`; confirm RED.
- [ ] Implement lock-owned transitions and immutable public snapshots. Freeze `ValidatedWorkbenchRun` in the record before starting the worker; check cancellation before evaluation, after evaluation, and immediately before the `persisting` transition.
- [ ] Run the narrow files repeatedly, then with `PYTHONHASHSEED=0` and `12345`; run related workspace/store tests, Ruff, and mypy.
- [ ] Review every transition and failure path, then commit as `feat(workbench): execute bounded city jobs`.

### Task 5: Expose the D1b control API

**Files:** Modify `src/adlife/city/workbench_web.py`, `tests/unit/city/test_workbench_web.py`, `tests/unit/city/test_workbench_security.py` only if a boundary regression requires it.

**Interfaces (Consumes/Produces):**

- `create_city_workbench_app()` owns one repository and job manager and shuts the worker down during application lifespan.
- `POST /api/jobs` parses through the existing strict bounded JSON path, reconstructs and freezes the complete validated input synchronously, preflights the run ID, submits one job, and returns `202` with `{schema_version, job, status_url}`.
- `GET /api/jobs/{job_id}` returns the current immutable public record; cancellation uses an empty `{}` body under the same write security boundary.
- `GET /api/runs` accepts strict non-boolean integers `offset >= 0`, `1 <= limit <= 100`, and returns the exact bounded page DTO, including scan truncation rather than an unbounded total claim.
- Expected errors map to constant safe 400/404/409/413/422 envelopes. Unexpected worker failures are recorded safely and never echo secrets, paths, tracebacks, or request data.

- [ ] Add RED end-to-end API tests for submit returning before a blocked worker, polling to verified completion, cancellation, duplicate/busy conflicts, restart discovery, bounded pagination, and truthful active-job capabilities.
- [ ] Assert malformed/invalid drafts create no job or directory; all state-changing requests still require exact origin/content-type/CSRF; query/path injection and unknown job/run IDs are clean refusals.
- [ ] Inject credential-shaped request values and failures from validation/evaluation/storage/verification; assert response bodies, job records, logs, and artifacts contain none of them.
- [ ] Prove concurrent POSTs cannot both be accepted and that no HTTP request waits for deterministic evaluation.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_web.py tests/unit/city/test_workbench_security.py -q`; confirm RED.
- [ ] Implement routes through the new repository/manager only. Update capabilities to `phase=bounded-jobs`, jobs/execution true, inspection false, and provider/OAuth false.
- [ ] Run the narrow files plus all `tests/unit/city/test_city_web.py tests/security -q`, Ruff, and mypy.
- [ ] Inspect OpenAPI/routes and strict response DTO serialization for any accepted path, credential field, unbounded string, or dead inspector URL, then commit as `feat(workbench): expose bounded run jobs`.

### Task 6: Compose verified run-scoped inspector reads

**Files:** Modify `src/adlife/city/web.py`, `src/adlife/city/workbench_web.py`, `src/adlife/city/static/index.html`, `src/adlife/city/static/app.js`, `tests/unit/city/test_city_web.py`, relevant browser viewer tests, and `tests/unit/city/test_workbench_web.py`.

**Interfaces (Consumes/Produces):**

- Extract one verified-run view context/response service used by both the legacy preloaded `create_city_app()` routes and workbench run-scoped handlers. Do not create/mount an application per request and do not duplicate scientific projection logic.
- Every `/api/runs/{run_id}/...` request validates the portable ID, rechecks the pinned workspace, freshly loads/verifies the artifact, then applies the existing bounds, filters, ordering, and canonical evidence paging.
- `/runs/{run_id}` renders the existing inspector with a safely injected `<meta name="adlife-api-base" content="/api/runs/{run_id}">`; legacy inspector HTML defaults to `/api`.
- All viewer fetches resolve a same-origin root-relative API base read from that meta value. Only `/api` or `/api/runs/<validated-portable-id>` is accepted; malformed bases fail closed before a request.
- A completed run summary receives its working `inspector_url` only after these routes exist. D1b includes immutable run inspection/read APIs, not report generation/download; the latter remains explicitly deferred by the more specific approved workbench design.

- [ ] Add RED parity tests comparing every legacy and run-scoped scientific response for the same verified v6 and v7 artifacts, including error/status/header parity, minute/agent bounds, filters, and all evidence pages.
- [ ] Add RED tests for invalid/unknown/corrupt run IDs, workspace replacement between requests, local artifact tampering after a prior successful read, and no cache masking corruption.
- [ ] Add RED DOM/JavaScript tests for legacy `/api`, nested run base resolution, malicious meta values, encoded run IDs, stale response protection, and no external requests or unsafe HTML interpolation.
- [ ] Run the affected unit/browser tests and confirm RED at the missing shared context/base behavior.
- [ ] Extract pure response builders conservatively, add run-scoped routes, then update every viewer fetch through one validated base helper. Preserve legacy route payloads byte-for-byte where canonical serialization is asserted.
- [ ] Run all city-web/view/browser suites for v1-v7, Ruff, and mypy. Test a freshly tampered run after first load to prove every request verifies current disk state.
- [ ] Inspect the diff for duplicated handlers, unvalidated route parameters, or CSP changes, then commit as `feat(workbench): compose verified run inspection`.

### Task 7: Document and package the completed D1b contract

**Files:** Modify `docs/architecture.md`, `docs/cli-reference.md`, `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`, `CHANGELOG.md`, `tests/packaging/test_wheel.py`; mark this plan's completed checkboxes only after evidence exists.

- [ ] Add RED wheel/installed-app coverage that submits a tiny deterministic workbench job, polls it, discovers and inspects the verified run, and confirms `inputs/workbench.json` exists without relying on the source checkout.
- [ ] Update documentation to explain one process-local worker, phase/cancellation boundaries, schema-v7 sidecar binding, verified discovery, deterministic-rules-only scope, and the continued absence of the full browser editor/inspector workflow.
- [ ] Mark roadmap D1b complete only when all Task 1-5 acceptance evidence passes. Do not mark D2-D4 complete or claim report download/provider settings/real city calibration.
- [ ] Run packaging tests, docs link/contract tests, Ruff, and mypy; inspect the built resource list.
- [ ] Commit as `docs(workbench): document bounded run jobs`.

### Task 8: Certify D1b before proceeding to the browser-operation plan

**Files:** No source change unless a failing gate first gains a focused RED regression and fix.

- [ ] Run `uv sync --locked --all-groups`.
- [ ] Run `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy src`.
- [ ] Run `uv run pytest -q`, then full pytest under `PYTHONHASHSEED=0` and `PYTHONHASHSEED=12345`; record exact counts.
- [ ] Run branch coverage and preserve the configured 85% floor.
- [ ] Run `uv build --no-sources` and the exact-wheel `scripts/smoke_release.py` outside the checkout.
- [ ] Run offline doctor/demo, workbench help, `git diff --check`, status/log/remote inspection, and the relevant no-network/security suites.
- [ ] Request a fresh code review focused on schema compatibility, state transitions, cancellation races, containment, secret screening, and honest capability claims. Fix every Critical/Important finding test-first.
- [ ] Push each verified local commit under the repository's existing identity and wait for the corresponding GitHub CI run to finish green. Do not change remotes or Git configuration.

## Completion handoff

Report commit IDs, RED/green evidence, exact gate counts, coverage, wheel smoke, API lifecycle evidence, and any review findings. D1b completion unlocks the separate D2/D3 browser-operation plan: run-scoped viewer composition, the four-stage `CITY -> CAMPAIGN -> RUN -> INSPECT` shell, browser polling/cancellation, and verified run-library reopening. Do not launch the owner's final hidden preview until that browser slice and its D4 QA are complete.
