# Local City Workbench Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver D1a of the approved local city workbench: a loopback-only command, strict packaged city/creative discovery, deterministic draft-to-domain validation, and a hardened versioned HTTP boundary that creates no run artifacts yet.

**Architecture:** Add a workbench application layer under `adlife.city` that translates one bounded HTTP draft into the existing immutable `CityMobility`, `SpatialCampaignScenario`, and `SpatialResponseInput` contracts. Keep workbench transport models, workspace pinning, FastAPI, CSRF, and creative resource loading outside `adlife.core`; add only a pure public road-coordinate helper to the adapter-free spatial domain. Serve a minimal honest shell and validation API now, leaving schema-v7 persistence, jobs, run discovery, and the full operator UI to D1b-D3.

**Tech Stack:** Python 3.11-3.13, uv, strict Pydantic v2, FastAPI/Starlette, Typer/Uvicorn, packaged JSON/HTML/CSS, pytest/Hypothesis, Ruff, mypy.

**Spec:** [Local City Workbench Design](../specs/2026-10-10-local-city-workbench-design.md), D1a transport/validation foundation within D1-D4.

## Global Constraints

- Preserve the existing read-only `create_city_app()` and all schema-v1-through-v6 city artifacts and commands.
- Keep `src/adlife/core` free of FastAPI, Typer, Uvicorn, storage, and provider adapters.
- Accept only verified packaged cities and packaged fictional creative templates. HTTP clients never provide paths, coordinates, creative copy, derived hashes, fictional flags, or agent identifiers.
- Expose deterministic rules as the only response mode. Do not render or accept provider/OAuth configuration in this slice.
- Keep validation side-effect free: it creates no run directory, reserves no run ID, starts no worker, and makes no network request.
- Use strict finite values, exact schema versions, `extra="forbid"`, portable run IDs, stable collection ordering, canonical JSON, and complete city/scenario/response fingerprint binding.
- Bind Uvicorn only to `127.0.0.1`, exactly one worker, reload disabled, with no automatic browser launch or visible terminal window.
- Require exact JSON content type, same-origin, CSRF, and a hard byte limit for every state-changing endpoint. Never echo request bodies, credential-shaped strings, tracebacks, or absolute paths.
- Use TDD for every task. Observe the new regression test fail before implementation, then run narrow, related, and full verification without weakening assertions or coverage.
- D1b exclusively owns `inputs/workbench.json` persistence, `CityRunManifestV7`, prepare/publish seams, job execution/cancellation, and completed-run discovery. D2-D3 own the complete editor/runner/inspector browser experience.

## Review Focus

- A nested draft containing unknown fields, non-finite numbers, booleans-as-integers, credential-shaped text, client coordinates, or client hashes is refused without echoing the rejected value: Tasks 1, 4, and 5.
- Roadside coordinates come only from the verified city geometry; an unknown road, unsupported direction, invalid fraction, or mismatched city cannot be snapped or accepted: Task 2.
- A POST without exact origin/content type/CSRF, or an oversized fragmented body without `Content-Length`, is rejected before JSON/domain parsing: Task 4.
- A workspace containing a symlink, junction, mount-point reparse component, or non-directory ancestor is refused; swaps injected between the documented pre/post checkpoints are detected before serving. D1b must retain per-publication containment/no-clobber checks for later changes: Task 3 and Task 6.
- Repeating validation for the same canonical draft yields identical input/scenario/response hashes, makes no network request, and leaves the pinned workspace unchanged: Tasks 2 and 5.

---

## Scope boundary and file map

| File | D1a responsibility |
| --- | --- |
| `src/adlife/city/workbench_input.py` | Strict frozen draft, creative-template, and normalized workbench-input models plus canonical fingerprints. |
| `src/adlife/city/creative_templates.json` | Small versioned catalog of fictional, bounded, disclosure-bearing creative snapshots. |
| `src/adlife/city/workbench_creatives.py` | Bounded package-resource loader and exact template selection. |
| `src/adlife/core/domain/spatial_campaign.py` | One new pure helper for the coordinate at a verified road/direction/fraction; existing validation delegates to the same geometry logic. |
| `src/adlife/city/workbench_validation.py` | The only draft-to-domain construction service used by validation now and job creation later. |
| `src/adlife/city/workbench_workspace.py` | Pre-resolution component/reparse checks, safe creation, post-creation recheck, and pinned workspace identity. |
| `src/adlife/city/workbench_security.py` | Bounded body buffering, strict JSON parsing, same-origin/CSRF/content-type enforcement, and screened error envelopes. |
| `src/adlife/city/workbench_web.py` | FastAPI composition, honest capability/catalog/template reads, side-effect-free scenario validation, and safe shell/static delivery. |
| `src/adlife/city/static/workbench.html`, `workbench.css` | Minimal offline shell with synthetic disclosure and no placeholder controls for unavailable jobs/providers. |
| `src/adlife/cli/commands/city_workbench.py`, `src/adlife/cli/app.py` | Human-only `adlife city-workbench --workspace PATH [--port N]` command and registration. |
| `tests/unit/city/test_workbench_input.py`, `test_workbench_validation.py`, `test_workbench_workspace.py`, `test_workbench_security.py`, `test_workbench_web.py` | Contract, construction, filesystem, HTTP security, and endpoint regressions. |
| `tests/cli/test_city_workbench.py`, `tests/packaging/test_wheel.py` | CLI binding/no-popup behavior and installed-resource coverage. |

The D1a API is deliberately small:

| Method/path | Result |
| --- | --- |
| `GET /` | No-store workbench shell with CSRF meta value and prominent synthetic-data disclosure. |
| `GET /assets/workbench.css` | Packaged local stylesheet with immutable-safe content type; no external resources. |
| `GET /api/workbench` | Schema 1 capabilities/limits/disclosure; `jobs=false`, `active_job=null`, response mode `deterministic-rules`. |
| `GET /api/catalog/cities` | Stable `city_id`-ordered metadata from `load_city_catalog()` after each pack is verified. |
| `GET /api/creative-templates` | Stable template-ID-ordered full fictional snapshots and their canonical hashes. |
| `POST /api/scenarios/validate` | Side-effect-free construction and validation; returns only bounded normalized IDs/counts/channels and input/city/scenario/creative/response hashes. |

Errors use only:

```json
{"schema_version":1,"error":{"code":"invalid-scenario","message":"The scenario is invalid.","fields":{"scenario.roadside.road_id":"Unknown road identifier."}}}
```

The envelope never includes Pydantic `input`, request JSON, an exception representation, a filesystem path, or a traceback. D1a uses `400` for malformed/semantic requests, `404` for unknown catalog entities, `413` for body limits, `422` for safe field-level structural validation, and a screened `500` for unexpected defects.

### Task 1: Freeze strict draft and creative-template contracts

**Files:** Create `src/adlife/city/workbench_input.py`, `src/adlife/city/creative_templates.json`, `src/adlife/city/workbench_creatives.py`, `tests/unit/city/test_workbench_input.py`.

**Interfaces (Consumes/Produces):**

- Consumes a schema-1 JSON object with `city_id`, `scenario`, `cohort`, and `settings` only.
- Produces strict frozen `WorkbenchRunDraft` wire models, `NormalizedWorkbenchRunDraft` server models, `CreativeTemplate`, `CreativeTemplateCatalog`, and `WorkbenchRunInput`.
- Produces `load_creative_template_catalog() -> CreativeTemplateCatalog` and `select_creative_template(template_id: str) -> CreativeTemplate`; HTTP JSON decoding remains centralized in Task 4.
- Task 2 constructs `WorkbenchRunInput(schema_version=1, draft=<normalized draft>, creative_template=<complete snapshot>)`; it exposes a SHA-256 `fingerprint` over `canonical_json(self)`. The creative hash covers the entire selected template including template version and disclosure.

The HTTP draft shape and exact field ownership are locked as follows (either `phone` or `roadside` may be `null`/omitted, but not both):

```json
{
  "schema_version": 1,
  "city_id": "fictional-grid-v2",
  "scenario": {
    "scenario_id": "launch-study",
    "name": "Fictional launch study",
    "campaign": {
      "campaign_id": "fictional-launch",
      "name": "Fictional launch",
      "creative_template_id": "fictional-device-launch-v1",
      "target_interests": ["technology"],
      "relative_price": 1.0
    },
    "phone": {
      "active_windows": [{"day": 1, "start_minute": 480, "end_minute": 1080}],
      "frequency_cap_per_agent_per_day": 3,
      "eligible_activities": ["commute", "leisure"],
      "opportunity_probability_per_minute": 0.05
    },
    "roadside": {
      "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1140}],
      "frequency_cap_per_agent_per_day": 2,
      "road_id": "road-main",
      "travel_direction": "forward",
      "road_fraction": 0.5,
      "side": "right",
      "orientation_degrees": 90.0,
      "max_view_distance_meters": 120.0
    }
  },
  "cohort": {
    "interests": ["technology"],
    "traits": {
      "price_sensitivity": 0.5,
      "novelty_seeking": 0.5,
      "advertising_skepticism": 0.5,
      "mobile_recall_encoding": 0.5,
      "roadside_recall_encoding": 0.5,
      "impulsivity": 0.5
    },
    "initial_state": {
      "brand_sentiment": 0.0,
      "recall_strength": 0.0,
      "purchase_intention": 0.0
    }
  },
  "settings": {
    "run_id": "launch-run",
    "agent_count": 20,
    "days": 1,
    "seed": 42,
    "response_mode": "deterministic-rules"
  }
}
```

The normalized draft stored inside `WorkbenchRunInput` has the same logical nesting, replaces each `{day,start_minute,end_minute}` with sorted `SpatialActiveWindow` absolute minutes, and assigns server-owned placement IDs `phone-placement` and `roadside-placement`. It does not add a coordinate, city hash, creative hash, agent ID, or fictional flag; those stay in the complete selected template and separately validated derived scenario/response models. Thus the input fingerprint represents canonical accepted operator choices, while the scenario fingerprint represents server-derived geographic binding.

Additional bounds are:

- `settings`: portable `run_id`, `agent_count` 1-30, `days` 1-7, `seed` 0-`2^63-1`, and literal response mode `deterministic-rules`;
- `scenario`: bounded `scenario_id`/name, exactly one campaign, optional single phone placement, optional single roadside placement, and at least one placement total;
- campaign: bounded `campaign_id`/name, packaged `creative_template_id`, 1-12 unique safe target interests, and finite relative price in `(0, 100]`;
- cohort: 1-12 unique safe interests, all six existing `SpatialResponseTraits`, and common finite initial sentiment `[-1,1]`, recall `[0,1]`, and purchase intention `[0,1]`;
- each placement owns 1-14 non-overlapping same-model-day windows with `start_minute` in 0-1439, `end_minute` in 1-1440, and end greater than start; phone owns activity/probability/cap, while roadside owns road/direction/fraction/side/orientation/view-distance/cap and no coordinate;
- field-level HTTP errors use the exact wire paths above, such as `scenario.roadside.road_id`, `scenario.phone.active_windows.0.end_minute`, and `settings.agent_count`.

- [ ] Write tests that accept phone-only, roadside-only, and combined wire drafts; canonicalize unordered interests, activities, windows, and catalog items; and prove the normalized draft/server-owned placement IDs produce stable hashes after a JSON round trip.
- [ ] Add adversarial model tests for unknown fields, duplicate members, missing placement, wrong schema/mode, invalid/duplicate windows, `true` in integer fields, NaN/Infinity, surrogate/control text, emails, authorization/API-key shapes, client creative copy, longitude/latitude, hashes, agent IDs, and fictional flags. Raw JSON size/depth/key-duplication cases belong to the one parser in Task 4.
- [ ] Test the packaged catalog's byte bound, exact schema, unique sorted IDs, bounded safe public text, mandatory fictional disclosure, template selection, unknown ID refusal, and full-snapshot hash stability.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_input.py -q`; confirm RED because the models/loaders/resources do not exist.
- [ ] Implement the smallest strict models and bounded loader. Reuse `DomainModel`, `validate_portable_run_identifier`, public-text/secret screens, and `canonical_json`; do not write a second serializer or credential detector.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_input.py tests/security/test_redaction_corpus.py -q`, `uv run ruff check src/adlife/city/workbench_input.py src/adlife/city/workbench_creatives.py tests/unit/city/test_workbench_input.py`, and `uv run mypy src`; require PASS.
- [ ] Inspect the diff for any accepted client-derived field that belongs to the server, then commit as `feat(workbench): define strict city drafts`.

### Task 2: Construct and bind deterministic city domain inputs

**Files:** Modify `src/adlife/core/domain/spatial_campaign.py`, `tests/unit/city/test_spatial_campaign_contract.py`; create `src/adlife/city/workbench_validation.py`, `tests/unit/city/test_workbench_validation.py`.

**Interfaces (Consumes/Produces):**

- `road_coordinate(pack: CityPackDocument, *, road_id: str, travel_direction: TravelDirection, road_fraction: float) -> tuple[float, float]` returns the longitude/latitude on verified v1/v2 geometry and refuses unknown roads, unsupported direction, bool/non-finite/out-of-range fractions, and zero-length geometry. The fraction remains measured over the pack's canonical source-to-target geometry for both travel directions, preserving existing scenario semantics.
- Existing `validate_spatial_scenario_against_city()` delegates billboard coordinate calculation to the same helper, preserving its public result and error bounds.
- Frozen `ValidatedWorkbenchRun` carries `pack`, `mobility`, `workbench_input`, `scenario`, and `response_input`.
- `construct_workbench_run(draft: WorkbenchRunDraft) -> ValidatedWorkbenchRun` is the single application service used by POST validation now and POST job creation later.

Construction first normalizes model-day windows and the two fixed placement IDs into `NormalizedWorkbenchRunDraft`, resolves the city through `select_catalog_city()`, resolves the complete creative snapshot, and freezes both as `WorkbenchRunInput`. It then creates `CityMobility(pack, seed, agent_count, days)`, derives any roadside coordinate on the server, and creates one scenario campaign plus one or two placements. It expands the homogeneous cohort into one profile per generated `person-NNN`, one response campaign, and the complete agent/campaign initial-state grid. Finally it independently calls `validate_spatial_scenario_against_city()` and `validate_spatial_response_input(..., agent_ids=...)` before returning. Neither the client wire draft nor the normalized workbench input contains the derived coordinate; `SpatialCampaignScenario` owns it.

- [ ] Add RED tests for curved v2 road interpolation, forward/backward support, v1 geometry, and parity between the helper and scenario validation. Pin an exact coordinate produced from a multi-segment fixture rather than duplicating implementation arithmetic in the assertion.
- [ ] Add RED construction tests for 1 and 30 agents, phone-only/roadside-only/both, exact creative binding, schedule normalization at day boundaries, fixed server-owned placement IDs, complete profile/state grids, and identical normalized-input/scenario/response hashes on repeated construction.
- [ ] Add refusal tests for unknown city/template/road, unsupported road direction, invalid day for selected duration, overlapping windows after normalization, scenario/campaign/creative mismatch, and any incomplete response grid.
- [ ] Prove validation performs no network operation, does not instantiate `CityRunStore`, reserves no run ID, and creates no filesystem artifact.
- [ ] Run `uv run pytest tests/unit/city/test_spatial_campaign_contract.py tests/unit/city/test_workbench_validation.py -q`; confirm RED at the missing helper/service.
- [ ] Implement the pure helper first, then the application constructor. Translate known catalog/domain failures to typed `WorkbenchValidationError(code, field, safe_message)` constants; never interpolate rejected values.
- [ ] Run the two narrow files plus `tests/unit/city/test_spatial_response_contract.py tests/unit/city/test_city_catalog_contract.py tests/unit/city/test_city_mobility.py -q`; require PASS.
- [ ] Run Ruff and `uv run mypy src`, inspect `git diff` for adapter imports under `src/adlife/core`, then commit as `feat(workbench): validate deterministic city inputs`.

### Task 3: Pin a safe server-owned workspace

**Files:** Create `src/adlife/city/workbench_workspace.py`, `tests/unit/city/test_workbench_workspace.py`.

**Interfaces (Consumes/Produces):**

- Frozen `WorkbenchWorkspace(root: Path, device: int | None, inode: int | None)` stores one resolved directory and its creation-time identity where the platform exposes it.
- `prepare_workbench_workspace(path: Path) -> WorkbenchWorkspace` checks the original absolute component chain before resolution, creates only missing directories, repeats the component walk, resolves once with `strict=True`, verifies directory identity/reparse attributes again, and returns the pinned object.
- `verify_workbench_workspace(workspace: WorkbenchWorkspace) -> Path` rechecks the pinned root before later D1b mutation; D1a calls it before app startup and never accepts a path from HTTP.

- [ ] Write RED tests for a fresh relative/absolute directory, an existing safe directory, a file ancestor, symlink/dangling symlink, a component swapped between the injected pre/post checks, and a pinned root replaced before verification.
- [ ] On Windows, test both a real junction when the account permits it and simulated `FILE_ATTRIBUTE_REPARSE_POINT`/mount-point attributes without relying on `Path.is_junction()` from Python 3.12.
- [ ] Assert every observed unsafe condition is refused before Uvicorn startup and cannot create a directory or marker outside the exact requested workspace chain. Do not claim the path object alone prevents an attacker from replacing a component after the final check; D1b must recheck at each publication boundary.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_workspace.py -q`; confirm RED at the missing module.
- [ ] Implement with `os.lstat`/non-following stat and explicit component walks. Do not canonicalize before inspecting links/reparse points, do not use a glob as a destructive target, and do not copy private report-publication code wholesale.
- [ ] Run the narrow test plus `tests/cli/test_city_report.py -q`, Ruff, and `uv run mypy src`; require PASS on this Windows checkout, with privilege-dependent real-link cases explicitly skipped rather than weakened.
- [ ] Inspect every filesystem operation and commit as `feat(workbench): pin safe workspace roots`.

### Task 4: Enforce the HTTP write boundary before parsing

**Files:** Create `src/adlife/city/workbench_security.py`, `tests/unit/city/test_workbench_security.py`.

**Interfaces (Consumes/Produces):**

- `MAX_WORKBENCH_BODY_BYTES = 131_072` is the single hard request limit.
- `new_csrf_token() -> str` generates per-process operational entropy; it is never serialized to scientific artifacts.
- `WorkbenchSecurityMiddleware(app, *, csrf_token: str, max_body_bytes: int = ...)` protects state-changing `/api/` methods before endpoint parsing.
- `strict_json_object(data: bytes) -> dict[str, object]` is the only client-body JSON decoder; it decodes strict UTF-8 and refuses duplicate keys, non-finite constants, non-object roots, excess nesting, and malformed JSON.
- `workbench_error(status_code, code, message, fields=None) -> JSONResponse` emits the sole safe schema-1 envelope.

Middleware accepts POST only when `Content-Type` is exactly `application/json`, `Origin` exactly matches the trusted loopback scheme/Host including port, and `X-AdLife-CSRF` constant-time matches the process token. It checks `Content-Length` when present, then buffers ASGI chunks only through limit+1 so a fragmented/chunked request cannot bypass the cap; on success it replays the exact bytes once to the endpoint. GET/HEAD never require CSRF, no CORS middleware or permissive header is installed, and dynamic/token-bearing responses are `Cache-Control: no-store`.

- [ ] Write raw-ASGI and TestClient RED tests for missing/wrong/null/cross-port origins, hostile Host, missing/wrong CSRF, content-type parameters/charset, malformed length, and all accepted exact-header cases.
- [ ] Send an oversized body as several receive events without `Content-Length`; require 413 before `strict_json_object`, Pydantic, or the endpoint is called. Test exact-limit acceptance and limit+1 refusal.
- [ ] Test invalid UTF-8, duplicate keys, NaN/Infinity, deep nesting, arrays/scalars, and malformed JSON. Assert credential-shaped payload fragments never appear in response body, captured logs, or exception strings.
- [ ] Assert responses contain no CORS permission, unsafe CSP, traceback, absolute workspace path, `Server` details, or request echo.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_security.py -q`; confirm RED at the missing boundary.
- [ ] Implement the raw ASGI middleware and parser with bounded memory and safe constants. Reuse the published secret redactor only for bounded server diagnostics; client errors remain constant and value-free.
- [ ] Run the narrow file plus `tests/unit/city/test_city_web.py tests/security/test_redaction_corpus.py -q`, Ruff, and `uv run mypy src`; require PASS.
- [ ] Review middleware ordering and every early response, then commit as `feat(workbench): harden loopback write API`.

### Task 5: Serve honest capabilities, catalogs, and validation

**Files:** Create `src/adlife/city/workbench_web.py`, `src/adlife/city/static/workbench.html`, `src/adlife/city/static/workbench.css`, `tests/unit/city/test_workbench_web.py`.

**Interfaces (Consumes/Produces):**

- `create_city_workbench_app(workspace: WorkbenchWorkspace, *, csrf_token: str | None = None) -> FastAPI` constructs one process-local app after rechecking the pinned root.
- The routes and response shapes are exactly the table above. Collections are stably ordered; every response uses bounded strict data and `X-Content-Type-Options: nosniff`.
- Validation parses through `strict_json_object`, validates `WorkbenchRunDraft`, calls `construct_workbench_run`, and returns hashes/counts/placement summaries only. It does not retain a draft server-side.

- [ ] Write RED endpoint tests for every successful GET/POST contract, media type, stable ordering, limit disclosure, jobs/provider capability honesty, synthetic/cohort disclosure, and repeated-validation hash equality.
- [ ] Test 400/404/413/422/500 mappings, safe field paths, generic unexpected diagnostics, and `RequestValidationError`/Pydantic error screening. Inject secrets and absolute paths at every layer and assert they are absent from response and logs.
- [ ] Hash/snapshot the workspace before and after successful, invalid, and internally failing validation; assert it remains byte-for-byte empty apart from the already-created root.
- [ ] Assert no network function, background task, `CityRunStore`, provider adapter, schema-v7 artifact, or run directory is reached.
- [ ] Test root token injection is escaped/attribute-safe, HTML/CSS are packaged local resources, CSP permits only self, no inline/external script/font/tile exists, and no control implies that running/inspection/provider features already work.
- [ ] Run `uv run pytest tests/unit/city/test_workbench_web.py -q`; confirm RED at the missing app.
- [ ] Implement the app with `TrustedHostMiddleware`, the Task 4 boundary, explicit exception handlers, and importlib resources. Do not modify `create_city_app()` or its existing route shapes.
- [ ] Run the narrow test plus all `tests/unit/city/test_city_web.py tests/security -q`; run Ruff and `uv run mypy src`; require PASS.
- [ ] Exercise `/api/scenarios/validate` with phone, roadside, and combined drafts through TestClient, inspect the diff, then commit as `feat(workbench): expose validation foundation`.

### Task 6: Register the command, package resources, document truthfully, and bring it up

**Files:** Create `src/adlife/cli/commands/city_workbench.py`, `tests/cli/test_city_workbench.py`; modify `src/adlife/cli/app.py`, `tests/packaging/test_wheel.py`, `docs/cli-reference.md`, `docs/architecture.md`, `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`, and `CHANGELOG.md`.

**Interfaces (Consumes/Produces):**

- `adlife city-workbench --workspace PATH [--port 8765]` is human-output-only, prepares/rechecks the workspace before app creation, prints one loopback URL, and calls `uvicorn.run(app, host="127.0.0.1", port=port, workers=1, reload=False, log_level="warning", access_log=False, server_header=False)`.
- The command never imports `webbrowser`, never auto-opens a page, and refuses machine output because it is a long-running interactive server.
- Documentation calls this a D1a validation foundation, not a runnable-job UI, hosted service, real-city twin, calibrated population, provider-enabled simulation, or prediction product.

- [ ] Write RED CLI tests for registration/help, required workspace, port bounds, human-only output, exact loopback/one-worker/no-reload Uvicorn call, safe refusal before server start, concise expected errors/no traceback, Ctrl-C 130, and absence of any browser-launch call.
- [ ] Extend the wheel test to require and parse `creative_templates.json`, `workbench.html`, and `workbench.css`; instantiate the workbench app from an installed-wheel subprocess outside the source checkout.
- [ ] Run `uv run pytest tests/cli/test_city_workbench.py tests/packaging/test_wheel.py -q`; confirm RED for the missing command/resources.
- [ ] Implement command registration and documentation. Keep roadmap D1a checked only after all task gates pass; leave D1b-D4 unchecked.
- [ ] Run `uv run adlife city-workbench --help`, the CLI/packaging tests, `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy src`; require PASS.
- [ ] Commit as `feat(workbench): add local validation command`.

### Task 7: Certify D1a, then perform the owner-requested hidden local preview

**Files:** No source change unless a failing gate first gains a RED regression and a focused fix. Record completed plan checkboxes/results only after the evidence exists.

- [ ] Run `uv sync --locked --all-groups`.
- [ ] Run `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy src`; require zero failures.
- [ ] Run `uv run pytest -q`, then `$env:PYTHONHASHSEED='0'; uv run pytest -q; Remove-Item Env:PYTHONHASHSEED` and `$env:PYTHONHASHSEED='12345'; uv run pytest -q; Remove-Item Env:PYTHONHASHSEED`; record exact counts and ensure the environment variable is removed even when recording a failure.
- [ ] Run `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`; keep the configured 85% floor.
- [ ] Run `uv build --no-sources` and `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`. Verify workbench resource loading and app construction outside the checkout without network access.
- [ ] Run `uv run adlife --help`, `uv run adlife city-workbench --help`, `uv run adlife doctor --offline`, and `git diff --check`.
- [ ] Inspect `git status --short`, `git diff --check`, `git log --oneline -n 12`, and `git remote -v`. Focused local commits are required; pushing is not a technical acceptance gate and requires live explicit owner authorization. Never change Git identity, remotes, or global configuration.
- [ ] After certification, as a separate owner-requested demo step, create a fresh preview workspace beneath `$env:TEMP`, choose a free port, and start `uv run adlife city-workbench --workspace <path> --port <port>` with PowerShell `Start-Process -WindowStyle Hidden` and redirected stdout/stderr. Do not open a browser or create a visible console.
- [ ] Probe `/`, `/api/workbench`, `/api/catalog/cities`, `/api/creative-templates`, and one CSRF-authenticated validation request from the shell. If any probe fails, stop the process and report the failure without calling D1a certified. If all pass, record the PID/URL/log path and give the owner an exact `Stop-Process -Id <pid>` command; leaving it running is a demo convenience, not a quality gate.

## Completion handoff

Report exact commit IDs, RED/green evidence, full gate counts, coverage, wheel path/smoke result, API probe results, hidden-preview PID/URL/logs, and remaining D1b-D4 work. The current repository owner has separately requested pushes in this active session; that live authorization may be followed after each verified commit, but this plan is not standing authorization for another session or executor. The next executable plan must start with D1b artifact-v7 persistence and bounded jobs; do not claim the browser can run or inspect a new workbench study until D1b-D3 are implemented and tested.
