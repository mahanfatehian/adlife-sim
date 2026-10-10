# Local City Workbench Browser Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the approved local browser-operated city study: choose a verified packaged city, compose and validate one bounded synthetic campaign, run it through the process-local worker, reopen verified artifacts, and inspect their immutable timeline and causal evidence without using the shell.

**Architecture:** Extend the D1b loopback workbench rather than creating another server, simulator, or frontend. Browser control requests translate into the existing strict workbench/domain contracts; completed-run reads always pass through the pinned `WorkbenchRunRepository` and shared verified-run view service. A packaged vanilla HTML/CSS/JavaScript client owns only draft and presentation state, while schema-v7 artifacts remain the scientific authority.

**Tech Stack:** Python 3.11-3.13, uv, strict Pydantic v2, FastAPI/Starlette, vanilla HTML/CSS/JavaScript, Canvas 2D, Playwright with an installed local Chromium browser, pytest, Ruff, and strict mypy.

**Spec:** [Local City Workbench Design](../specs/2026-10-10-local-city-workbench-design.md), especially sections 7, 8, 10-15; D2-D4 in the [production roadmap](2026-09-28-production-city-platform-roadmap.md).

## Global Constraints

- Enter only after D1b schema-v7 persistence, bounded jobs, verified run discovery, and run-scoped viewer reads are implemented and their focused tests pass.
- Preserve schema-v1-through-v7 artifact compatibility, legacy `/api/...` viewer behavior, all CLI/TUI/report paths, and deterministic scientific evidence.
- Keep `src/adlife/core` free of FastAPI, Typer, Textual, SQLite, browser libraries, Uvicorn, and provider implementations.
- Bind only to `127.0.0.1`, one process worker, reload disabled; the command never opens a browser or visible terminal automatically.
- The browser never submits a filesystem path, coordinate, provider credential, creative copy, or arbitrary URL. Roadside coordinates remain server-derived from a verified road and fraction.
- The browser holds one unsaved draft in memory only. Do not use `localStorage`, `sessionStorage`, IndexedDB, service workers, cookies, or URL state.
- All dynamic text uses `textContent`, DOM construction, and `<bdi>`/CSS isolation where needed. Do not use `innerHTML`, `outerHTML`, `insertAdjacentHTML`, inline script, `eval`, or string-to-code execution.
- All application requests are same-origin root-relative paths selected by code. Keep the restrictive CSP, no CORS grant, exact JSON content type, Origin and CSRF checks, bounded bodies, Trusted Host validation, and `Cache-Control: no-store` for dynamic/token-bearing responses.
- Browser-visible seed values use canonical decimal strings in control/view DTOs. Never convert a seed through JavaScript `Number`; validate with `BigInt`, preserve the original canonical decimal string, and convert to Python `int` only at the HTTP-to-domain boundary.
- Run settings remain 1-30 agents, 1-7 model days, and seed `0..9223372036854775807`. Seven-day navigation is implemented; month navigation is not promised while the accepted run contract remains capped at seven days.
- The only enabled city response mode is `deterministic-rules`. Do not add provider selectors, API-key fields, OAuth/login controls, comparison/report-download controls, or claims of real-city/worldwide availability.
- Every person, place, creative, behavior, opportunity, attention label, response, and metric remains explicitly synthetic. Purchase intention is an uncalibrated state proxy, not probability, transactions, sales, or a forecast.
- Playback, speed, zoom, filters, selected agent/campaign/evidence, and day jumps are presentation state only and may never write or alter an artifact.
- Every behavior change follows RED, narrow GREEN, related suites, diff review, and a focused commit. Do not regenerate golden artifacts merely to make a test pass.

## Review Focus

- The maximum seed `9223372036854775807` must survive browser input, validation, job creation, persisted input, run-library display, and inspector display exactly; Task 1 and Task 8 pin the complete path.
- A stale city-detail, validation, job poll, run-library, frame, or evidence response must never replace a newer user selection; Tasks 4, 5, 7, and 8 exercise each request class.
- A schema-v7 assumptions panel must come only from a freshly verified `inputs/workbench.json`; v1-v6 return a stable unavailable response, and a corrupt v7 run never renders as valid; Tasks 2, 6, and 8 cover this boundary.
- Fast playback or a slow API must keep at most one timeline transition in flight, retain the last committed UI state on failure, and stop cleanly; Tasks 7 and 8 measure the request backlog.
- Hostile Unicode/campaign text, malformed API payloads, a malicious API-base meta value, and an injected inspector URL must remain inert text and cannot cause an external request or DOM execution; Tasks 3, 5, 6, and 8 prove this.

---

## Entry gate and scope

Before Task 1, run the D1b focused suites and inspect the actual interfaces:

```bash
uv run pytest tests/unit/city/test_workbench_jobs.py \
  tests/unit/city/test_workbench_runs.py \
  tests/unit/city/test_workbench_web.py \
  tests/integration/test_city_workbench_artifacts.py -q
uv run mypy src
git status --short --branch
```

Require PASS. If D1b is incomplete, finish its focused plan first; do not emulate jobs in browser code or weaken artifact verification to start this plan.

This plan completes the browser-operated **local deterministic** workflow and D4 evidence for that profile. It can close D2 after its inspector criteria pass. It records only partial D3 progress: scenario operation and provenance are included, but provider configuration, OAuth, paired studies, comparison, and report download remain separately gated. It does not close roadmap D, E, F, or G, qualify a real city, or create an enterprise/remote deployment.

## Visual and interaction direction

Preserve the existing cartographic control-room identity instead of redesigning the product. Use the current local system-font stack and existing tokens: ink `#071218`, deep surface `#0b1b23`, paper `#e8f1ed`, mint/cyan `#84dfc2`, amber `#d9bb73`, and coral `#f19a78` only for failures or cancellation. The four numbered stages are meaningful workflow sequence, not decoration.

The memorable interaction is one persistent **causal thread**: a campaign placement selected in setup is recognizable on the map, at a timeline minute, in the evidence card, and in its metric receipt. Keep other motion restrained and remove nonessential animation under `prefers-reduced-motion: reduce`.

```text
┌ CITY ─ CAMPAIGN ─ RUN ─ INSPECT ──────────────────────────────┐
│ setup / assumptions (left) │ verified city map (main)         │
│ field errors beside inputs │ selected placement + provenance  │
│ run phase / library        │ evidence drawer when inspecting  │
└────────────────────────────┴────────────────────────────────────┘
  model clock: day jump | play/pause | speed | scrub | exact time
```

At 390 CSS pixels the order becomes stage rail, active setup panel, map/text alternative, evidence, then clock. No page-level horizontal overflow is permitted.

## Locked file map

| File | Responsibility in this plan |
| --- | --- |
| `src/adlife/city/workbench_transport.py` | Browser-safe exact-seed request translation and strict path-free city/input view DTOs. |
| `src/adlife/city/workbench_web.py` | Verified city detail, schema-v7 input view, workbench static assets, and existing control routes. |
| `src/adlife/city/workbench_jobs.py` | Exact non-secret accepted-settings projection needed to resume an active job after refresh. |
| `src/adlife/city/web.py` | Shared verified inspector metadata/read composition only where D1b has not already extracted it. |
| `src/adlife/city/static/workbench.html` | Semantic four-stage workbench shell, labels, errors, live region, map alternative, and library surface. |
| `src/adlife/city/static/workbench.css` | Existing control-room visual system, responsive containment, focus, bidi, forced-colors, and reduced-motion behavior. |
| `src/adlife/city/static/workbench.js` | Catalog/editor/job/library state machine, exact seed handling, safe rendering, polling, cancellation, and stale-response guards. |
| `src/adlife/city/static/index.html` | Inspector provenance/assumptions panel, explicit map controls, textual frame alternative, campaign/evidence controls, and model-clock controls. |
| `src/adlife/city/static/app.css` | Inspector responsive/accessibility states and causal-thread presentation. |
| `src/adlife/city/static/app.js` | Validated API-base resolution, v7 input projection, selection state, bounded playback, and inspector rendering. |
| `tests/unit/city/test_workbench_transport.py` | Exact seed and browser-facing DTO contract tests. |
| `tests/unit/city/test_workbench_web.py`, `test_workbench_jobs.py` | New read endpoints, response/error shapes, resumable accepted settings, asset/security headers, and control API compatibility. |
| `tests/unit/city/test_city_web.py` | Legacy/run-scoped viewer parity and new inspector metadata/HTML contracts. |
| `tests/browser/test_city_workbench_browser.py` | Full four-stage workflow, failures, stale responses, accessibility, responsive layout, and offline security. |
| `tests/browser/test_city_places_browser.py` | Existing inspector regressions plus enhanced model-clock/map/evidence behavior. |
| `tests/security/test_workbench_browser_security.py` | Static and HTTP adversarial checks for DOM sinks, request destinations, CSP, hostile text, and artifact immutability. |
| `tests/packaging/test_wheel.py`, `scripts/smoke_release.py`, `tests/packaging/test_smoke_release.py` | Exact-wheel resources and installed-workbench lifecycle smoke outside the checkout. |
| `README.md`, `docs/architecture.md`, `docs/cli-reference.md`, `docs/reproducibility.md`, `docs/city-pilot.md`, methodology model card/limitations, roadmap, `CHANGELOG.md` | Truthful local-workbench operation, exact limitations, and milestone evidence. |

The additive browser-facing API is:

| Method/path | Contract |
| --- | --- |
| `GET /api/catalog/cities/{city_id}` | Re-verifies the packaged catalog and returns schema-1 safe catalog metadata, exact pack hash, and the selected v2 pack; never a resource/path. |
| `GET /api/runs/{run_id}/workbench-input` | Freshly verifies a schema-v7 artifact and returns a bounded presentation projection of the frozen draft/creative with seed as a decimal string; verified v1-v6 return a stable 404 unavailable response. |
| `GET /api/runs/{run_id}/meta` | Existing metadata plus v7 `workbench_input_available` and `workbench_input_sha256`; other scientific viewer shapes remain unchanged. |

The POST draft wire contract changes only at the workbench HTTP boundary: `settings.seed` is a canonical decimal **string**. The internal `WorkbenchRunDraft`, normalized persisted `WorkbenchRunInput`, manifests, and deterministic engine continue to use a validated Python integer. Existing non-HTTP construction tests retain integer inputs.

The reset/default browser draft is fixed so first use and browser assertions do not depend on locale, wall-clock time, or catalog iteration: select `fictional-grid-v2`; prefer `fictional-device-launch-v1`; use scenario `city-study` / “Synthetic city study,” campaign `fictional-campaign` / “Fictional city campaign,” interests `commuting` and `technology`, relative price `1.0`, 20 agents, 2 days, and seed `"42"`. Enable phone by default with commute/leisure, probability `0.05`, cap 3, and 08:00-18:00 on days 1 and 2. Roadside defaults, when enabled, are `middle-west`, forward, fraction `0.5`, right side, 90 degrees, 120 meters, cap 2, and 07:00-19:00 on days 1 and 2. Trait defaults are price `0.5`, novelty `0.6`, skepticism `0.4`, mobile recall `0.7`, roadside recall `0.6`, impulsivity `0.3`; initial sentiment/recall/intention are `0.0/0.1/0.2`. Suggest the lowest `city-study-NNN`, starting at `001`, not present in the currently loaded verified library page; label it as a suggestion and keep server collision checks authoritative. Reset restores these values but never cancels an active job or deletes an artifact.

### Task 1: Add browser-safe city detail and exact seed transport

**Files:** Create `src/adlife/city/workbench_transport.py`, `tests/unit/city/test_workbench_transport.py`; modify `src/adlife/city/workbench_web.py`, `tests/unit/city/test_workbench_web.py`.

**Interfaces:**

- `parse_workbench_http_draft(payload: Mapping[str, object]) -> WorkbenchRunDraft` requires `settings.seed` to match `^(?:0|[1-9][0-9]{0,18})$`, bounds it at `2**63 - 1`, converts it exactly to `int`, and lets the existing strict domain model validate every other field.
- `WorkbenchDraftTransportError(ValueError)` carries only the constant field `settings.seed` and safe message `Enter a whole-number seed from 0 through 9223372036854775807.`; the controller maps it to the existing 422 `invalid-fields` envelope without echoing the submitted token.
- `WorkbenchCityDetail` is a strict frozen schema-1 DTO containing safe catalog metadata without `resource_name`, `city_sha256`, and the verified `CityPackV2`.
- `build_workbench_city_detail(city_id: str) -> WorkbenchCityDetail` selects only through `load_city_catalog()`/`select_catalog_city()` and cross-checks the selected entry/hash before returning.
- Validation/job summaries and workbench limits expose seed values as canonical decimal strings; no control response emits a large seed as a JSON number.

- [ ] **Write RED transport tests.** Prove canonical `"0"`, `"42"`, and `"9223372036854775807"` produce exact internal integers; reject JSON numeric seeds, booleans, signs, whitespace, exponent/decimal notation, leading zeroes, empty/oversized strings, and `"9223372036854775808"`. Assert the caller's decoded mapping is not mutated.
- [ ] **Write RED API tests.** Require `GET /api/catalog/cities/fictional-grid-v2` to return the exact verified hash/pack and no resource/path; require unknown/malformed IDs and catalog integrity failures to use screened envelopes. Update validation and job POST fixtures to send seed strings and assert validation summaries echo the exact string.
- [ ] **Run the narrow tests and confirm RED.** Run `uv run pytest tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_web.py -q`; expected failure is the missing module/endpoint and numeric-only seed contract.
- [ ] **Implement the strict translation and endpoint.** Reuse the existing safe catalog adapters, create no second city parser, and route both validation and job creation through `parse_workbench_http_draft()` so the two writes cannot drift.
- [ ] **Run GREEN and related boundaries.** Run the two narrow files, `tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_security.py -q`, then Ruff on changed files and `uv run mypy src`.
- [ ] **Inspect and commit.** Confirm no seed was converted through float/JavaScript, no resource name/path escaped, and no internal persisted schema changed. Commit as `feat(workbench): expose exact browser inputs`.

**Acceptance:** The maximum supported seed crosses the HTTP boundary exactly, validation remains side-effect-free, and a browser can retrieve one fully verified pack without choosing a path or using network data.

### Task 2: Expose the verified schema-v7 workbench input view

**Files:** Modify `src/adlife/city/workbench_transport.py`, `src/adlife/city/workbench_web.py`, `src/adlife/city/web.py` only if required by the D1b shared view service, `tests/unit/city/test_workbench_transport.py`, `tests/unit/city/test_workbench_web.py`, `tests/unit/city/test_city_web.py`.

**Interfaces:**

- `WorkbenchRunInputView` is a strict frozen schema-1 projection with `run_id`, `run_schema_version: Literal[7]`, `workbench_input_schema_version: Literal[1]`, manifest/input/city/scenario/creative hashes, normalized scenario/cohort/settings, and the complete frozen creative template. Its settings seed is a canonical decimal string.
- `build_workbench_run_input_view(stored: StoredCityRun) -> WorkbenchRunInputView` accepts only a fully verified v7 run with its required sidecar and proves the view hashes/IDs still match the manifest before projecting it.
- The run-scoped metadata response adds `workbench_input_available: true` and the manifest hash only for v7. It does not claim the sidecar for v1-v6.
- `GET /api/runs/{run_id}/workbench-input` calls `repository.load(run_id)` for every request; it never serves a cache or raw filesystem bytes.

- [ ] **Write RED DTO tests.** Assert exact keys, immutable round trip, maximum seed string, complete creative/cohort assumptions, and refusal when a stored v7 record has a missing or mismatched sidecar.
- [ ] **Write RED endpoint tests.** Assert a valid v7 view, stable 404 `workbench-input-unavailable` for each verified v1-v6 fixture, 404 `not-found` for an unknown/invalid run, and screened 409 `run-unavailable` for corrupt/tampered v7 or an unsafe replaced workspace. Assert no accepted path/query parameter and `no-store`/CSP/security headers.
- [ ] **Prove fresh verification.** Load the endpoint once, tamper a copied artifact through a test fixture, request it again, and require refusal rather than a cached successful document.
- [ ] **Run RED.** Run `uv run pytest tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_web.py tests/unit/city/test_city_web.py -q`; confirm the endpoint/view is absent.
- [ ] **Implement the view through D1b services.** Do not reopen `inputs/workbench.json` in the controller and do not recompute scientific results in FastAPI. The response is explicitly a browser projection; its manifest hash refers to persisted canonical input, not the string-seed projection bytes.
- [ ] **Run GREEN and artifact regressions.** Run the narrow files plus `tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_run_replay.py -q`, Ruff, and mypy.
- [ ] **Inspect and commit.** Verify v1-v6 scientific payloads are byte/shape compatible and corrupt v7 cannot be displayed. Commit as `feat(workbench): expose verified run assumptions`.

**Acceptance:** The inspector can show exactly which normalized assumptions and creative a v7 run used, while legacy and corrupt runs are described truthfully and never fabricated.

### Task 3: Replace the foundation landing page with the semantic CITY stage

**Files:** Modify `src/adlife/city/static/workbench.html`, `src/adlife/city/static/workbench.css`, `src/adlife/city/workbench_web.py`, `tests/unit/city/test_workbench_web.py`, `tests/packaging/test_wheel.py`; create `src/adlife/city/static/workbench.js`, `tests/browser/test_city_workbench_browser.py`.

**Interfaces:**

- The shell contains one skip link, one persistent synthetic disclosure, a four-step `CITY -> CAMPAIGN -> RUN -> INSPECT` rail, one polite live region, one main map canvas, one always-available textual map alternative, one setup region, and a bounded completed-run library region.
- `workbench.js` boots from `/api/workbench`, `/api/catalog/cities`, `/api/creative-templates`, `/api/runs?offset=0&limit=20`, then loads the selected `/api/catalog/cities/{city_id}` with a monotonic city request generation.
- Dynamic elements are built with `document.createElement`, `replaceChildren`, `textContent`, attributes, and event listeners only.
- `GET /assets/workbench.js` is a packaged local JavaScript asset with `nosniff` and no-store middleware behavior; the shell contains one external deferred script reference and no inline script.

- [ ] **Write RED static/server tests.** Assert the semantic landmarks, persistent labels/error slots, exact stage names, local script resource, no inline handlers, no external asset URL, no dead provider/OAuth/API-key controls, CSP compatibility, and the packaged JS resource.
- [ ] **Write the first RED browser test.** With a real loopback workbench app, assert the verified fictional city is selected, its rights/provenance/known omissions/time zone are visible, roads render on canvas, the textual alternative names the city/road count, CITY is current, and no external request/console/page error occurs.
- [ ] **Run RED.** Run `uv run pytest tests/unit/city/test_workbench_web.py tests/browser/test_city_workbench_browser.py -q`; confirm failure at the missing shell/client behavior.
- [ ] **Implement the CITY stage and design system.** Extend the existing navy/mint/amber control-room language. Keep 44-pixel controls, visible focus, native controls, restrained motion, bidi isolation, and a CSS-only 390-pixel stack. Do not add a JS/CSS framework or map/tile dependency.
- [ ] **Implement safe catalog boot/rendering.** Validate response schema/IDs/hashes before committing UI state. Draw only verified pack geometry; changing city clears downstream validation/job state. A late city response must be ignored.
- [ ] **Run GREEN and resource checks.** Run the narrow unit/browser tests with a required local browser, then `uv run pytest tests/packaging/test_wheel.py -q`, Ruff, and `git diff --check`.
- [ ] **Inspect and commit.** Search `rg -n "innerHTML|insertAdjacentHTML|localStorage|sessionStorage|https?://" src/adlife/city/static/workbench.*`; every hit must be absent or an intentional non-fetching text assertion. Commit as `feat(workbench): build verified city stage`.

**Acceptance:** Opening `/` presents a usable, keyboard-reachable city selection stage backed by a verified local pack, not a marketing placeholder or external map.

### Task 4: Build the CAMPAIGN and RUN draft editor with exact validation

**Files:** Modify `src/adlife/city/static/workbench.html`, `src/adlife/city/static/workbench.css`, `src/adlife/city/static/workbench.js`, `tests/browser/test_city_workbench_browser.py`, `tests/unit/city/test_workbench_web.py` where the public wire contract changes.

**Interfaces:**

- `readDraft() -> object` creates exactly one schema-1 HTTP draft from labeled controls; it sends the seed as the original canonical decimal string.
- `parseSeedText(value: string) -> string` validates canonical decimal syntax and uses `BigInt(value)` only for comparison with `9223372036854775807n`; it never returns `Number(value)`.
- Reusable schedule rows produce 1-14 non-overlapping `{day,start_minute,end_minute}` entries for each enabled placement. The browser sends road ID/direction/fraction, never latitude/longitude.
- `validateDraft() -> Promise<void>` POSTs through one same-origin JSON/CSRF helper, commits only the latest validation generation, renders the returned hashes/counts, and moves focus to the first adjacent field error on refusal.
- `renderFieldErrors(fields)` matches an exact `data-field-path` first, then the nearest declared aggregate prefix (for example an indexed active-window error to that placement's schedule editor), and otherwise uses the single error summary; it never creates a selector from untrusted field text.

- [ ] **Write RED happy-path browser coverage.** Operate city, creative template, campaign identity/name/target interests/relative price, phone and roadside toggles, verified road/direction/fraction, schedules, six cohort traits, initial state, run ID, 1-30 agents, 1-7 days, and seed. Validate phone-only, roadside-only, and combined drafts and assert validation writes no artifact.
- [ ] **Write RED field behavior coverage.** Assert at least one placement, road directions follow the selected verified road, days remove out-of-range schedule rows only after confirmation/validation guidance, bounds are visible, edits preserve unrelated values, and any edit after success invalidates the prior review.
- [ ] **Write RED exact-seed and error-focus coverage.** Use `9223372036854775807`; intercept the request text and require that exact decimal string. Test malformed/too-large seeds, server field errors, root errors, and first-invalid-field focus without losing the remainder of the draft.
- [ ] **Run RED.** Run `uv run pytest tests/browser/test_city_workbench_browser.py -q`; expected failures are missing editor behavior and seed serializer.
- [ ] **Implement the editor without a parallel domain model.** Populate choices from verified APIs, use plain typed form controls, canonicalize arrays in the client for stable review, and leave all authoritative validation to `construct_workbench_run()` through the HTTP translator.
- [ ] **Implement validation state.** Show response mode as a read-only `Deterministic rules` capability with an explicit note that providers/OAuth are not installed. Show input/city/scenario/creative/response hashes and homogeneous-cohort disclosure before enabling `Start run`.
- [ ] **Run GREEN and backend parity.** Run browser tests, `tests/unit/city/test_workbench_web.py tests/unit/city/test_workbench_validation.py -q`, then Ruff/mypy and `git diff --check`.
- [ ] **Inspect and commit.** Verify no API key/provider URL field, coordinate field, creative-copy editor, or filesystem/path field entered the DOM or request. Commit as `feat(workbench): compose validated city studies`.

**Acceptance:** An analyst can configure every supported workbench assumption, receive precise inline refusal, and review a complete validated synthetic study without reserving a run.

### Task 5: Operate jobs, cancellation, completion, and the verified run library

**Files:** Modify `src/adlife/city/workbench_jobs.py`, `src/adlife/city/static/workbench.html`, `src/adlife/city/static/workbench.css`, `src/adlife/city/static/workbench.js`, `tests/unit/city/test_workbench_jobs.py`, `tests/browser/test_city_workbench_browser.py`, `tests/unit/city/test_workbench_web.py`.

**Interfaces:**

- `startRun()` re-reads and submits the complete draft to `POST /api/jobs`; prior validation is advisory and the server remains authoritative.
- `WorkbenchAcceptedSettings` is a strict frozen schema-1 nested job DTO containing only city/scenario/campaign IDs, exact decimal seed, agent count, days, and `deterministic-rules`; every active and terminal job view carries the accepted settings copied from its frozen validated input.
- `pollJob(jobId, generation)` follows a server-returned status URL only after it matches `^/api/jobs/job-[0-9a-f]{32}$` and its ID equals the returned job record; it uses bounded backoff of 250, 500, 1000, then 2000 ms, and stops on `completed`, `failed`, `cancelled`, a superseding generation, or page teardown.
- `cancelActiveJob()` POSTs exactly `{}` with Origin/CSRF and reflects the returned cooperative state; it never claims an evaluating phase stopped immediately.
- `loadRunPage(offset, generation)` renders verified summaries only, validates every `inspector_url` against `^/runs/[a-z0-9][a-z0-9-]{0,39}$`, and supports bounded previous/next paging.
- Completion displays a focused, prominent **Inspect run** link. It never navigates automatically.
- Boot resumes polling the exact `active_job` returned by `/api/workbench` and renders its accepted settings; refreshing intentionally resets the unsaved editor draft but cannot hide or invent the process-local active job.

- [ ] **Write RED real lifecycle coverage.** Start a 1-agent/1-day deterministic run through the real app, observe accepted settings and legal phase labels, poll to completion, verify the v7 sidecar exists, focus the Inspect action, and assert no navigation occurred before the click.
- [ ] **Write RED library coverage.** Test empty guidance, stable run ordering, exact decimal seed display, v6/v7 reopening, page navigation, corrupt/truncated counts without presenting invalid entries, refresh, and a completed run appearing only after verified completion.
- [ ] **Write RED cancellation/failure coverage.** Use barriers/routes rather than sleeps to test queued/evaluating cancellation, late cancellation conflict, busy/duplicate run conflict, screened evaluation/persistence/verification failure, retained accepted draft, and retry with a new run ID. Refresh during a blocked active job and require the exact accepted settings and polling to resume from `/api/workbench`.
- [ ] **Write RED stale-response coverage.** Delay an old poll and old library page, switch job/page/run state, then release them; neither response may replace the newer selection or re-enable an invalid action.
- [ ] **Run RED.** Run `uv run pytest tests/browser/test_city_workbench_browser.py tests/unit/city/test_workbench_web.py -q`; confirm the absent operational flow.
- [ ] **Implement the bounded client state machine.** Keep one active polling controller/generation, render exact server phases, disable only conflicting controls, and terminate timers on terminal state or unload. Do not infer completion from a directory or elapsed time.
- [ ] **Run GREEN and job regressions.** Run the browser file plus `tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_runs.py tests/integration/test_city_workbench_artifacts.py -q`, Ruff, and mypy.
- [ ] **Inspect and commit.** Confirm the browser cannot invent a job, completed summary, path, or run URL. Commit as `feat(workbench): operate verified city runs`.

**Acceptance:** The complete `CITY -> CAMPAIGN -> RUN` path works without a shell, failures preserve the user's draft, cancellation language is truthful, and only a freshly verified artifact unlocks inspection.

### Task 6: Bind the immutable inspector to run-scoped reads and v7 assumptions

**Files:** Modify `src/adlife/city/static/index.html`, `src/adlife/city/static/app.css`, `src/adlife/city/static/app.js`, `src/adlife/city/web.py`, `src/adlife/city/workbench_web.py`, `tests/unit/city/test_city_web.py`, `tests/unit/city/test_workbench_web.py`, `tests/browser/test_city_places_browser.py`, `tests/browser/test_city_workbench_browser.py`.

**Interfaces:**

- `validatedApiBase() -> string` accepts only `/api` or `/api/runs/<portable-run-id>` from `<meta name="adlife-api-base">`; malformed, encoded, query-bearing, absolute, protocol-relative, traversal, or backslash values fail before any fetch.
- `apiPath(relative: string) -> string` accepts only a fixed root-relative viewer endpoint/query produced by code and prefixes the validated base. Every scientific fetch in `app.js` uses it, including evidence contract endpoints.
- A v7 inspector fetches `${apiBase}/workbench-input` only when metadata says it is available and renders frozen creative, campaign/placement schedule, cohort interests/traits, common initial state, exact seed, city/scenario/input hashes, and the homogeneous-cohort disclosure.
- A legacy v1-v6 inspector remains fully usable and labels workbench assumptions unavailable; it does not issue the v7-only request.

- [ ] **Write RED API-base tests.** Compare legacy `/api` and nested `/api/runs/{id}` responses for a verified v7 fixture, then use browser interception to prove every request stays under the chosen base. Require fail-closed behavior for malicious/encoded meta values with zero attacker-controlled requests.
- [ ] **Write RED assumptions tests.** Open the completed workbench run from Task 5 and assert exact creative/cohort/settings/hashes and maximum seed display. Open v1-v6 fixtures and assert the panel's honest unavailable state. Tamper v7 after one successful request and require a screened failure on refresh.
- [ ] **Write RED XSS/bidi tests.** Put permitted Persian/Unicode text plus markup-like characters in a copied fixture, render it as text inside `<bdi>`, and prove no script/image/event-handler node or external request appears.
- [ ] **Run RED.** Run `uv run pytest tests/unit/city/test_city_web.py tests/unit/city/test_workbench_web.py tests/browser/test_city_places_browser.py tests/browser/test_city_workbench_browser.py -q`; confirm hard-coded `/api` or missing assumptions behavior fails.
- [ ] **Implement one API-base helper and migrate every fetch.** Do not retain a second hard-coded path in evidence pagination, frame/state requests, or metrics. Preserve the existing raw integer-token parser for scientific endpoints whose frozen schemas still encode seed as a JSON integer.
- [ ] **Render provenance safely.** Add a compact assumptions drawer and workbench back link without removing existing evidence panels. Use only verified DTO fields and DOM text APIs; do not hash in the browser or treat the projection body as canonical artifact bytes.
- [ ] **Run GREEN and parity suites.** Run the narrow tests, all city viewer CLI/API/browser tests, Ruff, mypy, and static sink searches.
- [ ] **Inspect and commit.** Verify legacy viewer URLs/shapes still work and every workbench inspector read freshly verifies disk. Commit as `feat(workbench): inspect frozen run assumptions`.

**Acceptance:** Clicking Inspect opens the existing evidence instrument against the selected immutable artifact, shows its frozen v7 assumptions, and cannot be redirected to another origin or server path.

### Task 7: Complete model-clock, map, causal selection, and bounded playback controls

**Files:** Modify `src/adlife/city/static/index.html`, `src/adlife/city/static/app.css`, `src/adlife/city/static/app.js`, `tests/browser/test_city_places_browser.py`, `tests/browser/test_city_workbench_browser.py`.

**Interfaces:**

- The model clock shows the pack IANA time-zone label but states that day/night shading is the illustrative fixed model rule (06:00/19:00), not local sunrise, calendar time, traffic, or DST behavior.
- Explicit controls provide previous/next day, play/pause, presentation speed, minute scrub, zoom in/out/reset, and keyboard operation. Speed and zoom never appear in artifacts or API writes.
- Playback is self-scheduling: it awaits one complete `setMinute()` transition before scheduling the next. At most one frame/evidence batch is in flight; changing minute/agent/campaign/evidence stops or supersedes playback safely.
- The canvas text alternative reports selected minute, selected synthetic person/activity/road, visible evidence-page scope, selected campaign/evidence, and placement provenance.
- Selecting a campaign or current-page evidence record establishes a presentation-only causal thread across placement marker, ledger card, response chain, and metric receipt without hiding the unfiltered totals.

- [ ] **Write RED control tests.** Keyboard-operate every control; assert day jumps clamp within 1-7 days, exact `aria-valuetext`, speed label, zoom bounds/reset, and no artifact-mutating request method.
- [ ] **Write RED slow-API/backlog tests.** Delay frame/evidence replies while playback runs and measure active requests; require a maximum of one timeline batch, no timer pile-up, stale responses ignored, last committed minute retained after failure, and clean pause/teardown.
- [ ] **Write RED causal/text-alternative tests.** Select agent, campaign, placement/evidence, and minute; require matching highlighted identifiers across map/ledger/receipt and equivalent textual facts. Page-scoped map markers must remain labeled as page-scoped.
- [ ] **Write RED accessibility/rendering tests.** Assert concise live announcements, visible focus, 44-pixel targets, no color/emoji-only status, no horizontal overflow at 390px, usable 200% text sizing, forced-colors focus, and reduced-motion suppression of nonessential transitions/automatic animation.
- [ ] **Run RED.** Run the two browser files with `ADLIFE_REQUIRE_BROWSER=1`; confirm failures at missing controls/backlog behavior.
- [ ] **Implement bounded presentation controls.** Replace `setInterval` playback with an awaited generation-aware loop. Reuse the existing committed-state rollback and evidence pagination; do not interpolate an unsaved frame or refold metrics in JavaScript.
- [ ] **Run GREEN and immutable-evidence checks.** Hash every source artifact before/after a browser control session and require equality. Run existing viewer unit/browser suites, `git diff --check`, and inspect browser console/page errors.
- [ ] **Inspect and commit.** Verify the UI distinguishes model clock, illustrative light, current-minute evidence, final response state, and full-run metrics. Commit as `feat(city): complete timeline inspection controls`.

**Acceptance:** The inspector supports the full seven-day timeline and causal selection without request backlog, artifact mutation, misleading time semantics, or mouse-only controls.

### Task 8: Certify the complete browser workflow under adversarial conditions

**Files:** Expand `tests/browser/test_city_workbench_browser.py`; create `tests/security/test_workbench_browser_security.py`; modify production files only after a new focused RED regression exposes a defect.

**Interfaces:** No new feature API. This task proves Tasks 1-7 as one installed-style local workflow.

- [ ] **Add one complete real happy path.** From a fresh workspace, use only browser actions to select the catalog city, configure a combined campaign, use maximum seed, validate, start, poll, inspect, scrub, select an agent/campaign/evidence item, return to the workbench, and reopen from the library. Assert persisted v7 evidence and source hashes remain unchanged.
- [ ] **Add fault and race paths.** Cover malformed catalog/detail/validation/job/run/input/frame/evidence documents, slow responses, HTTP failures, server-screened job failure, queued cancellation, late-cancel conflict, duplicate/busy conflicts, stale city/validation/poll/library/timeline replies, and browser refresh during an active process-local job.
- [ ] **Add security paths.** Cover hostile campaign/Unicode/bidi text, markup-like creative data, malicious `inspector_url`, CSRF/origin/content-type refusal, query/path traversal, hostile API-base meta, no browser storage, no external request, restrictive CSP, no unsafe DOM sink, and no secret-shaped request/failure text in DOM, response, logs, or artifacts.
- [ ] **Add accessibility/responsive paths.** Complete the workflow keyboard-only; inspect accessible names/live status/error association; test 390x844, desktop, 200% text, reduced motion, forced colors, resize, and canvas textual alternative. Require zero uncaught console/page errors.
- [ ] **Add a maximum UI-load fixture.** Open a verified 30-agent/7-day artifact, exercise playback/evidence pagination, and assert bounded evidence DOM (at most the 100-record page plus fixed chrome), bounded run library page, responsive input, and no growing request backlog. Record elapsed browser smoke time without inventing a production SLO.
- [ ] **Run the required browser/security gate.** On Windows: `$env:ADLIFE_REQUIRE_BROWSER='1'; uv run pytest tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py -q; Remove-Item Env:ADLIFE_REQUIRE_BROWSER`. On POSIX use `ADLIFE_REQUIRE_BROWSER=1 uv run pytest ...`. Require PASS; a missing browser is a failed certification, not a silent skip.
- [ ] **Run all workbench/API/artifact tests.** Run `uv run pytest tests/unit/city/test_workbench_*.py tests/integration/test_city_workbench_artifacts.py -q`, Ruff, mypy, and `git diff --check`.
- [ ] **Request fresh review.** Review exact-seed integrity, stale-response guards, job polling teardown, unsafe DOM/request sinks, accessibility, run verification, and legacy viewer compatibility. Fix every Critical/Important finding test-first.
- [ ] **Commit.** Commit only test-driven fixes and certification coverage as `test(workbench): certify browser-operated studies`.

**Acceptance:** The browser workflow is offline, keyboard-operable, responsive, race-safe, XSS/CSP-safe, exact at the maximum seed, and incapable of changing committed simulation artifacts.

### Task 9: Package and document the completed local browser slice

**Files:** Modify `tests/packaging/test_wheel.py`, `scripts/smoke_release.py`, `tests/packaging/test_smoke_release.py`, `README.md`, `docs/architecture.md`, `docs/cli-reference.md`, `docs/reproducibility.md`, `docs/city-pilot.md`, `docs/methodology/model-card.md`, `docs/methodology/limitations.md`, `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`, `CHANGELOG.md`.

**Interfaces:**

- The wheel must contain `workbench.html`, `workbench.css`, `workbench.js`, icon, inspector HTML/CSS/JS, creative templates, catalog index, and city pack.
- Exact-wheel smoke constructs the installed app outside the checkout, submits a 1-agent/1-day draft with seed as a string, polls to verified v7 completion, discovers the run, fetches its workbench-input projection and run-scoped inspector resources/data, and proves `inputs/workbench.json` exists and no network call occurred.
- Documentation presents one command and one local URL, states the workspace/artifact location, explains job/cancellation/restart behavior, and labels every current limitation.

- [ ] **Write RED wheel/resource assertions.** Require `workbench.js`, its shell reference, all new API routes, and absence of unsafe sinks/external resources in packaged bytes.
- [ ] **Write RED clean-room lifecycle smoke.** Drive the new workflow through the installed wheel with a socket/network guard and a shadow checkout; assert module origins are site-packages and every inspected run is v7/verified.
- [ ] **Run RED.** Run `uv run pytest tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py -q`; confirm missing resource/lifecycle assertions fail before documentation claims change.
- [ ] **Implement smoke/resource updates.** Keep the smoke bounded and deterministic; do not launch a browser, make a network call, or leave a background server/process.
- [ ] **Update public docs truthfully.** Document the complete local workflow, exact decimal seed transport, verified city-only catalog, deterministic-rules-only engine, process-local jobs, illustrative model clock, homogeneous cohort assumption, synthetic claim scope, and absence of provider/OAuth/real-city/report-download/comparison features.
- [ ] **Update roadmap status precisely.** Mark D2 complete only if Tasks 1-8 pass. Record D3 scenario-operation progress but leave D3 unchecked while provider/study views remain excluded. Mark D4 complete only for this local slice after required browser evidence; leave parent D/E/F/G open and real-city B gates unchanged.
- [ ] **Run packaging/docs checks.** Run packaging and documentation tests, `uv build --no-sources`, then the exact built-wheel smoke outside the checkout.
- [ ] **Inspect and commit.** Search public text for unsupported `production-ready`, `real residents`, `sales prediction`, `worldwide`, provider, and OAuth claims. Commit as `docs(workbench): ship local browser operation`.

**Acceptance:** A fresh wheel contains and exercises the complete offline workbench independently of the source tree, and the public contract says exactly what shipped and what remains deferred.

### Task 10: Run final gates, push verified commits, and start the hidden owner preview

**Files:** No source changes unless a failing gate first receives a focused RED regression and fix.

- [ ] **Run the complete mandatory matrix.** Run:

  ```bash
  uv sync --locked --all-groups
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy src
  uv run pytest -q
  ```

- [ ] **Run both deterministic hash-seed suites.** On Windows run `$env:PYTHONHASHSEED='0'; uv run pytest -q`, then `12345`, restoring/removing the variable afterward. Record exact counts and durations.
- [ ] **Run branch coverage.** Run `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`; require at least the configured 85% floor without excluding new browser/application code merely to pass.
- [ ] **Run required browser and security gates.** Set `ADLIFE_REQUIRE_BROWSER=1`; run all `tests/browser` plus `tests/security`, and require PASS with no browser skip.
- [ ] **Build and smoke the exact wheel.** Run `uv build --no-sources` and `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl` from the repository command, verifying the script itself uses an external temporary clean room.
- [ ] **Run public command gates.** Run `uv run adlife --version`, `uv run adlife doctor --offline`, `uv run adlife --format json doctor --offline`, `uv run adlife demo --headless`, `uv run adlife --format json demo --headless`, and `uv run adlife city-workbench --help`.
- [ ] **Inspect repository state.** Run `git diff --check`, `git status --short`, `git log --oneline -n 20`, and `git remote -v`. Do not add/change a remote or Git configuration.
- [ ] **Push only verified focused commits.** Use the repository's already configured identity and existing authorized remote; push each completed commit and wait for the corresponding GitHub CI run to finish green. Do not force-push, publish, tag, or create a release.
- [ ] **Start the owner-requested preview only after all gates pass.** Create a fresh workspace beneath the system temporary directory, choose a free port, and start `uv run adlife city-workbench --workspace <path> --port <port>` with `Start-Process -WindowStyle Hidden` and redirected logs. Do not open a browser or visible terminal.
- [ ] **Probe the hidden preview.** Verify `/`, `/assets/workbench.js`, `/api/workbench`, city detail, creative catalog, validation, a tiny job to completion, run discovery, workbench input, inspector HTML, meta/frame/evidence, and no external network. If any probe fails, stop the process and report the failure rather than leaving a broken preview.

## Completion handoff

Report focused commit IDs and CI URLs/status, RED/GREEN evidence per task, exact unit/integration/browser/security/full-suite/hash-seed/coverage counts, wheel path and clean-room smoke output, maximum-seed evidence, artifact before/after hashes, browser console/network results, and the hidden preview URL/PID/workspace/log paths with an exact stop command.

The handoff must state plainly that provider settings/OAuth, remote multi-user deployment, real-city catalog qualification/worldwide selection, paired study operation/report download, calibrated traffic/population behavior, external validity, spatial cognition/social/purchase integration, and enterprise operations remain follow-on work. A polished local UI is not evidence that those gates passed.
