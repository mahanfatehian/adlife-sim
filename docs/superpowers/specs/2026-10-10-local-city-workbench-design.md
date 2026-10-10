# Local City Workbench Design

**Date:** 2026-10-10  
**Status:** Approved for staged implementation<br>
**Roadmap scope:** D1-D4, first shell-free loopback slice  
**Parent design:** `2026-09-28-production-city-platform-design.md`

## 1. Purpose

This specification defines the first complete browser-operated AdLife city study. An
analyst must be able to choose a verified packaged city, configure a bounded spatial
campaign, choose deterministic run settings, start a background job, follow truthful
progress, and inspect the persisted result without using the command line.

The slice turns the existing read-only city viewer into a local workbench. It reuses
the verified city catalog, spatial domain models, run store, deterministic city runner,
metrics, and evidence viewer. It does not create a second simulation engine or weaken
artifact verification.

This is a single-operator, loopback-only application. It is a useful local product
milestone, not a claim of enterprise deployment readiness or real-world predictive
validity.

## 2. User outcome

The primary user is an analyst preparing and inspecting a fictional geographic
advertising study. A successful workflow is:

1. start one local AdLife command;
2. open the workbench in a browser;
3. select a verified catalog city;
4. configure one bounded campaign and its phone or roadside placements;
5. set population, duration, and seed;
6. validate the complete draft without reserving a run;
7. start a run with a fresh portable run identifier;
8. observe coarse, truthful execution phases;
9. activate the prominent **Inspect run** action for the completed immutable run;
10. scrub its day/night timeline and inspect agents, routes, opportunities, attention,
    responses, final state, and artifact-derived metrics;
11. reopen another completed run from a stable ordered list.

The browser must always identify people, behavior, exposure, and results as synthetic.
Purchase intention remains an uncalibrated state proxy, not purchase probability,
transactions, sales, or a forecast.

## 3. Chosen approach

Extend the existing FastAPI and vanilla HTML/CSS/JavaScript city application with a
separate workbench composition layer. Keep the current immutable viewer endpoints and
rendering logic as the inspection surface. Add small server-side services for draft
validation, bounded job execution, and completed-run discovery.

This approach is preferred over:

- a second frontend or SPA, which would duplicate the tested viewer and introduce a
  build toolchain without product value;
- invoking CLI subprocesses from HTTP handlers, which would make cancellation, errors,
  testing, and artifact ownership harder to reason about;
- an enterprise-first multi-user service, which would require the separate identity,
  authorization, tenancy, provider-secret, and deployment gates in roadmap E and G.

## 4. Scope

### 4.1 Included

- one new `adlife city-workbench` command bound to `127.0.0.1` only;
- one server-owned workspace root supplied when the command starts;
- catalog-backed city listing and selection;
- a structured single-campaign editor with at most one phone policy and one roadside
  placement using the existing supported spatial schema;
- deterministic cohort-wide response assumptions expanded into the existing strict
  per-agent response input;
- run settings for 1-30 agents, 1-7 days, and seed `0..2^63-1`;
- validation using the existing strict domain contracts before a run is reserved;
- one active city job at a time with a bounded in-process worker;
- explicit job phases and terminal success, failure, or cancellation state;
- completed-run listing from verified `CityRunStore` artifacts only;
- automatic transition from a completed job to the existing run inspector;
- immutable run inspection, evidence pagination, and metrics already supported by the
  saved-run viewer;
- offline static assets, responsive layout, keyboard operation, and browser tests.

### 4.2 Explicitly excluded

- arbitrary city-pack, campaign, response, report, or artifact paths from HTTP clients;
- file upload;
- public network binding;
- accounts, OIDC, OAuth login, roles, workspaces, or remote multi-user access;
- provider API keys in browser requests, responses, DOM, storage, URLs, or logs;
- local or remote LLM influence on city responses;
- paired studies, comparisons, and report download in this first slice;
- cancellation that pretends to interrupt an indivisible phase already executing;
- real residents, live traffic, live advertising delivery, or sales prediction;
- a worldwide city catalog. The UI shows only verified packaged catalog entries, which
  currently means the fictional grid.

Provider-backed geographic cognition, study comparison/report operation, authenticated
remote use, and qualified real-city data each require a later focused specification.

## 5. Architecture

```text
Browser (vanilla HTML/CSS/JS)
        |
        | versioned JSON over loopback HTTP
        v
FastAPI workbench adapter
        |
        +--> City catalog service
        +--> Draft validation service
        +--> Bounded city job manager
        +--> Completed-run query service
        |
        v
Existing city application services
        |
        +--> create_city_run(...)
        +--> CityRunStore
        +--> metrics projections
        +--> immutable viewer data
        |
        v
Adapter-free core domain and simulation
```

The workbench module belongs outside `src/adlife/core`. Core code must not import
FastAPI, Typer, storage adapters, or provider-specific code. HTTP schemas translate to
existing strict domain values before any simulation function is called.

The workbench app is not constructed around one preloaded `CityMobility` object. It
owns a server-selected workspace and resolves a requested catalog ID or run ID through
fixed services. The existing `create_city_app()` remains a read-only single-run adapter;
shared viewer response helpers may be extracted only when doing so removes duplication
without changing their public contracts.

## 6. Server-owned state

### 6.1 Workspace

The startup command receives `--workspace PATH`. Before resolving or creating anything,
it checks the original absolute path and every existing ancestor/component and refuses a
symbolic link, junction, mount-point reparse target, or non-directory ancestor. After
creation it repeats the component walk, resolves the final directory once, and pins that
exact path into the app. HTTP requests never supply paths. All mutable workbench state
and `city-runs/` artifacts stay beneath this root.

The command refuses an unsafe workspace before resolution so symlink or junction status
cannot disappear through canonicalization. Existing run-store containment, portable
identifier, no-clobber, and corruption checks remain authoritative.

### 6.2 Drafts

The first slice keeps one browser draft client-side and sends the complete structured
document for validation or job creation. The server does not persist draft campaigns.
Refreshing the page resets unsaved draft fields to a packaged safe template.

The draft contains:

- selected catalog `city_id`;
- scenario identity and display name;
- exactly one bounded campaign record selected from a packaged fictional creative
  template whose content hash is fixed and visible;
- zero or one phone placement and zero or one roadside placement, with at least one
  placement in total;
- one declared cohort interest set, the six bounded `SpatialResponseTraits`, campaign
  target interests and relative price, and common initial sentiment/recall/intention;
- agent count, days, seed, and proposed run ID.

The application expands the cohort assumptions after constructing `CityMobility`: it
creates one `SpatialResponseProfile` for every generated `person-NNN`, one
`SpatialCampaignResponseInput` for the campaign, and the complete agent/campaign grid of
`SpatialResponseInitialState` records. Every generated profile receives the explicitly
displayed common interests and traits. This homogeneous-response-cohort assumption is
shown in the editor and inspector; it is not presented as demographic calibration.

For a roadside placement, the browser selects a road, direction, fractional position,
side, orientation, view distance, schedule, and frequency cap. The server derives the
exact coordinate from the verified road geometry and validates the binding. The browser
does not submit latitude or longitude. For a phone placement, the browser selects
eligible synthetic activities, per-minute opportunity probability, schedule, and cap.
Schedules are entered as model day/time values and normalized to absolute model minutes.

Creative copy and asset upload are not accepted in this slice. A small packaged set of
versioned fictional creative templates supplies bounded structured content: template ID,
product name/category, message, call to action, and disclosure. The analyst edits campaign
identity, targeting assumptions, placement, schedule, and response assumptions.

The accepted normalized draft and the complete selected creative-template snapshot are
persisted canonically as `inputs/workbench.json`. The existing `creative_sha256` is the
SHA-256 of the canonical creative-template object. A new `CityRunManifestV7` extends the
schema-v6 evidence receipt with `workbench_input_schema_version: 1` and
`workbench_input_sha256`; load and replay require the sidecar, verify its canonical bytes
against the manifest, and verify its creative subtree against every campaign creative
hash. Package upgrades therefore cannot change what a saved run used. Existing CLI city
runs remain schema-v6 unless given a complete workbench input, and all existing schema-v6
artifacts remain readable. A later creative-ingestion contract must apply the same
freeze-and-bind rule to new user-provided content and assets.

### 6.3 Jobs

`CityJobManager` owns at most one running job and a bounded recent terminal-job history.
It uses a single background worker and calls application functions directly rather than
shelling out. A job record contains only bounded non-secret metadata:

- server-generated `job_id`;
- requested `run_id`;
- phase;
- created and last-transition timestamps for operational display only;
- terminal error code and screened message when applicable;
- completed run summary when successful.

Job state is operational metadata, not scientific evidence. Simulation outputs remain
fully determined by frozen inputs and seed; wall-clock times never enter the model or
artifact hashes.

Allowed phases are:

```text
queued -> evaluating -> persisting -> verifying -> completed
   |            |              |            |
   +------------+--------------+------------+-> failed
   +------------+-> cancelled
```

The city run application service is split without changing scientific semantics:

- `prepare_city_run()` constructs mobility and all deterministic in-memory evidence;
- `publish_city_run()` atomically saves that prepared evidence through `CityRunStore`;
- a final independent `CityRunStore.load()` is the verifying phase.

The existing `create_city_run()` remains a compatibility wrapper around prepare and
publish. This real seam makes the displayed evaluation, persistence, and verification
phases truthful.

Complete domain validation and input freezing happen synchronously inside `POST /api/jobs`
before a job is accepted; invalid input creates no job. The worker therefore transitions
from queued directly to evaluating.

The job manager owns the phase transition lock. A cancellation request in queued or
evaluating state sets `cancellation_requested`; the worker checks it at each boundary and
after deterministic evaluation, discarding prepared in-memory evidence instead of
publishing it. Evaluation is CPU-bound and is not claimed to stop instantly.
Once the manager atomically enters persisting, cancellation returns a conflict explaining
that artifact publication and verification will finish. The worker must never publish a
partial run as completed.

The job manager is intentionally process-local. Restart recovery lists only fully
verified completed artifacts; abandoned operational job records disappear. Partial or
corrupt artifact directories are not listed as completed and surface through screened
diagnostics during direct inspection.

## 7. HTTP contract

Workbench control API responses use a top-level `schema_version: 1`. Scientific viewer
responses retain their existing domain/artifact schemas: for example, the city document
keeps its city-pack schema version and the agents endpoint remains a list. JSON request
bodies have strict content-type and size limits. Unknown fields are rejected. Stable
collection ordering is part of the contract.

### 7.1 Read endpoints

- `GET /api/workbench`
  - returns capabilities, active-job summary, limits, and disclosure text;
- `GET /api/catalog/cities`
  - returns verified catalog entries ordered by `city_id`;
- `GET /api/runs?offset=&limit=`
  - returns verified completed runs ordered by `run_id`; scientific contents never
    depend on this display ordering;
- `GET /api/jobs/{job_id}`
  - returns one bounded job record;
- existing viewer reads are mirrored beneath `/api/runs/{run_id}/...` without accepting a
  filesystem path.

Run-backed endpoints must load and verify the selected artifact before exposing data.
They retain current minute bounds, agent validation, canonical evidence order, and page
limits.

The legacy single-run `create_city_app()` and its `/api/...` shapes remain compatible.
The workbench serves `GET /runs/{run_id}` using the same inspector HTML with a safe
`<meta>` API-base value of `/api/runs/{run_id}`. The portable run ID is validated before
HTML rendering. `app.js` resolves all viewer fetches through that meta value, defaulting
to `/api` for the legacy app. No inline script or untrusted HTML interpolation is needed.
Shared run-view response construction moves into a small verified-run service so legacy
and run-scoped handlers cannot drift.

### 7.2 Write endpoints

- `POST /api/scenarios/validate`
  - validates the complete draft and returns normalized non-secret metadata plus field
    errors; it creates no directory and reserves no run ID;
- `POST /api/jobs`
  - repeats validation, refuses an existing run ID, snapshots the accepted domain
    inputs, creates a queued job, and returns `202` with its job URL;
- `POST /api/jobs/{job_id}/cancel`
  - requests cancellation only in a cancellable phase and returns the resulting state.

The write API requires same-origin requests with an exact JSON content type and a
per-process CSRF token delivered in a safe `<meta>` element in the initial HTML and
required in a custom header. Dynamic workbench HTML and token-bearing responses use
`Cache-Control: no-store`. The app retains `TrustedHostMiddleware`, restrictive CSP, no
CORS permission, loopback binding, and no browser-readable provider credentials. The
CSRF token is intentionally readable by same-origin JavaScript, is operational entropy,
and is never persisted into run artifacts.

The command starts exactly one Uvicorn process worker with reload disabled. Process-local
jobs and CSRF state are not advertised as safe under multiple workers.

### 7.3 Error envelope

Expected failures return a stable JSON envelope:

```json
{
  "schema_version": 1,
  "error": {
    "code": "invalid-scenario",
    "message": "Campaign placement is outside the selected city.",
    "fields": {"campaigns.0.placements.0": "Unknown road identifier."}
  }
}
```

Messages never include traceback text, request bodies, credentials, arbitrary provider
responses, or absolute server paths. Status mapping is:

- `400` malformed or semantically invalid request;
- `404` unknown catalog entity, job, or completed run;
- `409` duplicate run ID, active-job limit, late cancellation, or state conflict;
- `413` oversized request;
- `422` structurally valid JSON that fails strict field validation when field detail is
  useful;
- `500` screened unexpected internal failure with server-side diagnostic logging that
  also applies secret redaction.

## 8. Simulation and artifact flow

1. Parse the bounded request into workbench transport models.
2. Resolve the selected city exclusively through the verified packaged catalog.
3. Construct `CityMobility`, derive the placement coordinate and complete homogeneous
   cohort response input, then independently validate `SpatialCampaignScenario` and
   `SpatialResponseInput` against the city and requested duration/population.
4. Refuse a run ID collision before queueing and recheck immediately before creation.
5. Freeze the validated values in the job record.
6. Execute `prepare_city_run()` in the single worker and honor a pending cancellation at
   the post-evaluation checkpoint.
7. Let `publish_city_run()` and `CityRunStore` perform no-clobber publication.
8. Load the published run again through `CityRunStore.load()` before declaring the job
   complete.
9. Return a run-scoped inspector URL.

Validation and job creation use the same construction function so their behavior cannot
drift. Validation success is advisory: job creation always validates again.

No HTTP response is held open for the simulation. The browser polls the job resource
with bounded backoff and stops on a terminal state. Closing the browser does not cancel a
job. Playback and scrub speed affect presentation only.

## 9. Provider boundary

The existing city engine has no cognition-provider port. Its spatial response evidence
is produced by the deterministic `spatial-response-v1` rule model. The original zone
simulator's mock/local/remote provider wiring cannot be reused as if it already affected
city outcomes.

Therefore this slice exposes one enabled response mode:

- **Deterministic rules** — offline, reproducible, credential-free.

The workbench may show a concise capability note that provider-backed city cognition is
not installed. It must not render enabled mock/local/remote controls, ask for an API key,
or imply that a provider affected a city run.

A later provider milestone must first define what bounded provider values may affect,
persist validated cognition and provenance, preserve rule-owned purchase semantics,
support deterministic fallback and replay, and protect provider configuration. Secrets
will remain in environment, OS keyring, or a managed server-side secret source and will
never be returned after configuration.

OAuth identity is separate from model-provider configuration. It is excluded while the
app remains loopback-only and single-operator.

## 10. Browser experience

The workbench uses four real workflow stages:

```text
CITY -> CAMPAIGN -> RUN -> INSPECT
```

### 10.1 Layout

- a compact stage rail communicates completion and validation state;
- a left setup surface owns city, campaign, and run controls;
- the existing city-network canvas remains the main visual instrument;
- a contextual evidence drawer replaces an endlessly growing setup sidebar while
  inspection is active;
- the model-clock strip remains persistent and becomes the primary navigation device
  for day/night time;
- completed runs are reachable from a small library surface without exposing paths.

The visual direction extends the existing cartographic control-room language rather than
redesigning it. The palette remains restrained navy/cyan/amber with coral reserved for
errors and destructive cancellation. System-local fonts keep the application fully
offline. The signature interaction is a consistent causal thread connecting a placement
on the map, its current-minute evidence, and the corresponding metric receipt.

### 10.2 Interaction and accessibility

- every field has a persistent label, constraints, and adjacent error text;
- validation moves focus to the first invalid field and preserves other input;
- a completed job moves focus to a clear “Inspect run” action rather than navigating
  unexpectedly;
- status announcements are concise and use one polite live region;
- keyboard users can operate every workflow stage, person list, evidence page, timeline,
  and map zoom control;
- the canvas has a textual current-frame alternative and explicit zoom buttons;
- controls have at least 44-pixel targets;
- reduced-motion preference disables nonessential transitions and timeline animation;
- the 390-pixel layout stacks setup, map, evidence, and clock without horizontal page
  overflow;
- Persian and bidirectional text are isolated so identifiers and measurements remain
  legible;
- no color or emoji is the sole carrier of state.

### 10.3 Empty, running, and failure states

- first use opens on the city stage with the verified fictional city selected and a
  transparent notice that the catalog contains synthetic data;
- an active job shows its exact phase, run ID, accepted settings, and whether cancellation
  is still possible;
- an empty run library explains how to create the first run;
- field failures remain inside the editor;
- job failures preserve the accepted draft and provide a screened actionable message;
- corrupt artifacts are never silently omitted from direct access or displayed as valid
  results.

## 11. Security properties

The implementation must prove:

- the server listens only on loopback;
- Host validation permits loopback names only;
- state-changing requests require the correct per-process CSRF header and same origin;
- CORS does not authorize another origin;
- API bodies, collection sizes, strings, pagination, jobs, and history are bounded;
- the client cannot choose filesystem paths;
- portable run IDs and no-clobber behavior are enforced twice;
- symlink and containment checks remain in the storage boundary;
- campaign text is rendered with DOM text operations, never HTML injection;
- static assets are local and CSP permits no remote script, style, frame, or connection;
- diagnostics and JSON envelopes contain no credentials or request-body echo;
- default tests and the complete rules workflow require no network;
- completed-run reads verify artifact integrity before returning evidence.

## 12. Testing strategy

Every behavior change follows red-green-refactor. Tests assert public behavior rather
than implementation details.

### 12.1 Contract and unit tests

- catalog response schema and stable ordering;
- strict request validation, unknown fields, booleans-as-integers, non-finite values,
  excessive strings, collections, nesting, and body size;
- campaign and response construction against the chosen city;
- validation creates no artifact and reserves no run;
- duplicate identifiers and active-job conflicts;
- job state transition legality, bounded history, cancellation windows, and screened
  failure records;
- deterministic construction of identical frozen job inputs;
- verified completed-run listing and refusal of partial, corrupt, or symlinked entries;
- CSRF, Origin/Host, content-type, and no-CORS behavior;
- architecture boundary remains adapter-free.

### 12.2 Integration tests

- complete POST -> job polling -> completed verified run -> inspector data flow;
- job creation revalidates after a prior validation response;
- simultaneous start requests produce at most one active job and never overwrite a run;
- storage failure yields a failed job and no falsely completed artifact;
- process restart discovers completed artifacts but does not invent recovery of transient
  jobs;
- same city/campaign/settings/seed under different run IDs produce normalized identical
  evidence;
- the workbench and CLI city-run paths produce equivalent persisted scientific evidence.

### 12.3 Browser tests

Headless Playwright tests cover:

- the four-stage happy path from catalog to inspected result;
- inline validation and focus restoration;
- polling through slow phase changes and a screened failure;
- run-library reopening;
- timeline play, pause, scrub, agent selection, evidence paging, and map controls without
  changing persisted results;
- keyboard-only navigation and accessible names/status;
- 390-pixel and desktop containment;
- reduced motion;
- Persian/unicode campaign names and bidirectional identifiers;
- malicious campaign text and CSP/no-external-request assertions;
- stale responses cannot replace a newer selected run or job;
- browser console has no uncaught error during the successful workflow.

## 13. Documentation and command contract

The focused implementation updates:

- CLI help for `city-workbench`;
- `docs/cli-reference.md` with startup, workspace, limits, and exit behavior;
- `docs/architecture.md` with the loopback workbench and job boundary;
- `docs/reproducibility.md` clarifying that job timing is operational only;
- the roadmap D progress ledger without marking D complete until the complete local study
  exit gate is met;
- README quick-start only after the workflow passes installed-wheel smoke.

The command defaults should make a safe local preview easy while keeping artifact location
explicit. It must not open a browser automatically unless a future explicit flag is added.
This avoids desktop popups in automated and agent-driven environments.

## 14. Delivery decomposition

Implementation is split into independently testable milestones:

This design is delivered through a focused plan series rather than one oversized plan:

1. **D1a — transport and validation:** workbench schemas, versioned creative snapshots,
   catalog/read APIs, security middleware, command shell, and draft-to-domain validation;
2. **D1b — artifacts and jobs:** schema-v7 binding, prepare/publish/verify seams, bounded
   job lifecycle, cancellation, and completed-run discovery;
3. **D2/D3 — browser operation:** run-scoped viewer composition, four-stage shell,
   scenario editor, progress, run library, and inspector transition;
4. **D4 — release QA:** accessibility, security/failure browser coverage,
   documentation, installed-wheel smoke, and full gates.

Each plan is written, reviewed, implemented test-first, and verified before the next plan
begins. The plans share this approved product contract but may not silently move work or
acceptance criteria out of the slice.

Provider-backed city cognition, paired studies/comparison/report download, authenticated
remote access, and real-city catalog expansion are separate follow-on specifications.

## 15. Acceptance criteria

This slice is complete only when:

- one command starts the loopback workbench from an installed wheel;
- the browser can operate the complete included workflow without shell commands;
- a valid draft produces a verified schema-v7 workbench city run whose scientific
  evidence remains compatible with the schema-v6 inspector projections;
- invalid drafts, duplicate runs, concurrent jobs, cancellation, storage failures, and
  corrupt artifacts fail with stable screened behavior;
- every inspected result comes from a committed verified artifact;
- identical scientific inputs retain deterministic normalized evidence;
- browser playback and UI timing do not alter outputs;
- secrets are neither accepted nor emitted by this slice;
- all new unit, integration, browser, security, architecture, documentation, and packaging
  tests pass;
- Ruff format/lint, strict mypy, full pytest, both hash-seed suites, coverage, wheel build,
  exact wheel smoke, offline doctor/demo, and `git diff --check` remain green;
- the existing CLI, TUI, reports, city replay, and immutable saved-run viewer contracts
  remain compatible.

Completion of this slice advances roadmap D but does not by itself complete D, E, F, G,
the broader spatial cognition/social/purchase work, real-city rights qualification, or an
enterprise production-readiness claim.
