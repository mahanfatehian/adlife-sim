# CLI reference

Commands honor the global options; every failure
exits with one of the documented codes and, in JSON modes, emits an error object on
stdout.

## Global options

Set on the root command, before the subcommand:

```bash
adlife --format human|json|jsonl --no-color <command> …
```

| Option | Meaning |
| --- | --- |
| `--format` | `human` (default) prose; `json` one machine-readable document on stdout; `jsonl` one JSON object per line for streams. |
| `--no-color` | Disable colored output. `NO_COLOR=1` and `TERM=dumb` are honored automatically. |
| `--version` | Print `adlife <version>` and exit. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error (a defect, not your input). |
| 2 | Invalid input: refused configuration (including provider credentials or URLs), bad file, broken scenario, refused flag combination. |
| 3 | Run conflict: a run identifier already exists, or an operation would overwrite an artifact. |
| 4 | Artifact error: a stored run or required cognition cache record is missing, corrupt, or does not reproduce. |
| 130 | Interrupt: an active run records its interrupted artifact before exiting. |

A Python traceback is printed only for internal defects (exit 1) — expected failures such
as invalid input, run conflicts, provider misconfiguration, or corrupted artifacts print a
concise diagnostic on stderr and nothing more. Set `ADLIFE_DEBUG=1` to request the full
traceback for any failure.

## `adlife init NAME [--parent DIR]`

Create a new study directory from the packaged demo project. Refuses to touch a non-empty
target (exit 2). Default parent is the working directory.

## `adlife validate PATH`

Validate a study directory: schema, world/routine routability, campaign containment.
Print a JSON validation result and exit 0 when valid; exit 2 with a diagnostic otherwise.
`--verbose` adds validation detail on stderr. YAML documents are bounded to 1 MiB and
32 nesting levels; aliases, duplicate keys, and custom object tags are refused.

## `adlife city-catalog list [--search TEXT]`

List the packaged, integrity-verified city catalog. Selection is offline and
content-addressed: every entry pins a v2 resource by canonical SHA-256 and matching
metadata. The five shipped entries are `fictional-grid-v2`,
`fictional-harbor-loop-v2`, `fictional-new-district-v2`, `fictional-old-town-v2`, and
`fictional-river-junction-v2`; all have origin
`fictional` and qualification `fictional-fixture`, and this release contains zero
rights-reviewed real cities. `--search` applies case-insensitive local token matching to
ID, display name, provider and dataset. No matches is a successful empty result. JSON mode
emits one `{"cities": [...]}` document; JSONL emits one entry per line. `city-import`
writes a local file but does not register it in the packaged catalog, and CLI `--city-id`
selection remains packaged-only; workspace uploads are available only inside
`city-workbench --workspace PATH`.

## `adlife city-catalog show CITY_ID`

Show one exact catalog record, including hash, schema, qualification, bounds, time
zone, public source metadata and known omissions. An unknown ID exits 2. A missing,
malformed, mismatched or hash-invalid packaged catalog resource exits 4. Listing and
showing the catalog make no network request and accept no URL or arbitrary file path.

## `adlife city-places validate PLACE_SET [PACK | --city-id ID] [--agents N] [--days N] [--seed N]`

Validate one bounded local synthetic place-set JSON document against exactly one local
`PACK` or verified catalog `--city-id`. The command checks the immutable city hash,
place provenance, home capacity, keyed assignment and directed routability without
writing an artifact or making a network request. `--agents` accepts 1–250 (default
20), `--days` 1–31 (default 7), and `--seed` defaults to 42. Invalid, mismatched,
insufficient or unreadable inputs exit 2 with bounded diagnostics. JSON mode emits one
document containing the city/place hashes and assignment count.

## `adlife city-campaign validate SCENARIO [PACK | --city-id ID]`

Validate one bounded local spatial-campaign JSON document against exactly one local
`PACK` or verified catalog `--city-id`. The command checks the immutable city hash,
campaign references, creative hashes, active windows, frequency caps, phone opportunity
policy, road direction and explicit billboard coordinate/road-fraction binding. It does
not snap coordinates, fetch creative or map data, run a simulation, or write an artifact.
Malformed, mismatched, off-network, out-of-bounds or unsupported-direction inputs exit 2
with bounded diagnostics. Catalog corruption exits 4. JSON mode emits one document with
the scenario/city hashes, channel counts and maximum accepted binding error.

## `adlife city [--pack FILE | --city-id ID] [--places FILE] [--agents N] [--days N] [--seed N] [--port N]`

Start the read-only geographic mobility pilot at `http://127.0.0.1:8765` (loopback
only). Without either selector, the viewer preserves the original explicitly fictional,
bundled v1 offline grid. `--pack` loads one versioned local city-pack JSON file;
`--city-id` selects an integrity-verified packaged v2 entry such as
`fictional-grid-v2`. The selectors are mutually exclusive. The HTTP API cannot open
paths or change the loaded pack. `--agents` accepts 1–250
(default 20), `--days` 1–31 (default 7), `--seed` defaults to 42, and `--port` defaults
to 8765. `--places` optionally supplies a validated place set bound to the selected
pack. Invalid packs or place sets exit 2 before the server starts. The interactive
server accepts only the default human output mode. It does not open a browser
automatically.

This command does not run or persist the advertising engine. Its minute-addressable
frames model illustrative home/work/leisure travel on local roads; they are not
traffic measurements or real-person predictions. See [city-pilot.md](city-pilot.md).

## `adlife app [--workspace PATH] [--port N] [--open/--no-open] [--provider-origin ID=URL] [--provider-key-env ID=ENVIRONMENT_VARIABLE]`

Start the guided app with sensible defaults: the same server as `adlife city-workbench`,
with the workspace `./adlife-workspace` (created if missing) and port 8765, and the default
browser opened at `http://127.0.0.1:8765/guide` once the server answers (`--no-open` skips
that). Everything else, including the provider options and the loopback-only binding, is
exactly as described for `adlife city-workbench` below. Press Ctrl+C to stop. The guided
pages are documented for users in [user-guide.md](user-guide.md).

## `adlife city-workbench --workspace PATH [--port N] [--provider-origin ID=URL] [--provider-key-env ID=ENVIRONMENT_VARIABLE]`

Serve the guided pages at `/guide` and their local API on loopback at
`http://127.0.0.1:8765`. The root address `http://127.0.0.1:8765` redirects (`307`) to
`/guide`. `--workspace` is required and names a dedicated local
directory; each existing path component is checked without following symlinks or
Windows reparse points, missing directories are created one at a time, and the resolved
directory identity is pinned and rechecked before the server starts. `--port` accepts
1–65535. The command is interactive, accepts human output mode only, uses one Uvicorn
worker with reload disabled, and never opens a browser automatically. `--provider-origin
ID=URL` may be repeated to approve exact provider base URLs at startup; the fixed
`local-ollama` loopback approval is always present. Any other OpenAI-compatible address can
also be connected from the guided pages while the server runs (`POST /api/providers/origins`):
it passes exactly the same rules (https for an online service, a `127.0.0.1` or `[::1]`
literal on any port except this server's own for a server on this computer), is approved for
this process only, and is forgotten on restart. `--provider-key-env
ID=ENVIRONMENT_VARIABLE` may be repeated to bind a startup-approved remote provider ID to an
environment-variable name. Credential values are never command-line arguments.

The command prints one line, `city workbench: http://127.0.0.1:8765/guide`, the address of
the guided view: plain-language pages for running a study in English, German, or Persian
(`?lang=en`, `?lang=de`, or `?lang=fa`; Persian pages read right to left). The guided pages
are the only browser interface this server serves; [user-guide.md](user-guide.md) explains
them step by step. The setup page composes a bounded phone/billboard study of
one packaged fictional town from a packaged fictional creative and persona templates, runs it
through the one local worker, and the start page lists verified artifacts from the bounded
library. The browser owns an unsaved draft and presentation state only; the server derives
coordinates and remains authoritative for validation, collisions, storage, and scientific
evidence.

The earlier technical workbench page at `/` (`CITY -> CAMPAIGN -> RUN -> INSPECT`), the
saved-run inspector at `/runs/{run_id}`, its `/runs/{run_id}/reuse` draft page, and their
`/static/*` and `/assets/workbench*.js`/`.css` assets were removed and now answer `404`; only
`/assets/workbench-icon.svg` remains, as the pages' icon. To inspect a saved run's timeline
and evidence in a browser, run `adlife city-view ROOT RUN_ID` against the workspace.

The guided setup page submits schema-4 requests. The JSON API also accepts schema 3, which
creates a schema-v9 run, and schema-1/schema-2 compatibility requests; it
reports capabilities, lists the combined verified city choices and complete packaged
fictional creative snapshots, and validates a bounded scenario through
`POST /api/scenarios/validate`. Writes require an exact same-origin request, exact
`application/json`, and a per-process CSRF header. Only
`POST /api/catalog/cities/upload` has a 4 MiB streamed request ceiling; its complete
canonical stored CityPack V2 must also fit 4 MiB. Scenario and job JSON routes retain the
128 KiB body limit. Validation derives roadside coordinates from verified geometry,
constructs a complete synthetic persona response grid, and returns only identifiers,
counts, channels, and canonical hashes. It creates no run directory and reserves no run
identifier.

An upload returns one path-free catalog row and is immutable within that workspace. It is
always `operator-supplied-unreviewed`: technical validation does not establish ownership,
license compliance, redistribution rights, or packaged-catalog qualification. The server
does not fetch a source URL; it stays inert metadata, never a link. Default
workbench operation performs no outbound request.

The process-local control API exposes these bounded catalog, job, and discovery routes:

- `GET /api/catalog/cities` returns packaged and workspace rows in stable order;
- `GET /api/catalog/cities/{city_id}` re-verifies and returns one path-free city detail;
- `POST /api/catalog/cities/upload` validates and immutably registers one local V2 pack;
- `POST /api/scenarios/validate` validates the complete draft without reserving a run ID;
- `POST /api/jobs` repeats validation and returns `202` with a server-generated job ID; a run
  ID that a saved run already uses is refused with `409` `run-conflict`, naming the
  `settings.run_id` field;
- `GET /api/jobs/{job_id}` reports a truthful bounded phase; schema-3 may report `queued`,
  `simulating`, `selecting-explanations`, `requesting-explanations`, `persisting`,
  `verifying`, or a terminal phase, and schema 4 may report `queued`, `planning-cognition`,
  `requesting-cognition`, `applying-cognition`, `persisting`, `verifying`, or a terminal
  phase;
- `POST /api/jobs/{job_id}/cancel` requests cooperative cancellation before persistence;
- `GET /api/runs?offset=&limit=` lists only independently loaded, verified completed runs; a
  run whose exact artifact content is unchanged reuses its earlier full verification in this
  process;
- `GET /api/persona-templates` returns the ten validated fictional persona templates;
- `GET /api/providers` lists approved origins and immutable profile revisions without credentials;
- `POST /api/providers/origins` with exactly `{"base_url": "..."}` approves one typed
  OpenAI-compatible address for this process and returns its origin view; a missing scheme
  becomes https (http for a loopback literal), `localhost` becomes `127.0.0.1`, the same
  address always returns the same approval, and an unsafe address is refused with `422`
  `invalid-provider-origin` (at most 20 origins: `409` `provider-origin-limit`);
- `POST /api/providers` creates one immutable profile revision at an approved origin;
- `POST /api/providers/{profile_id}/revisions/{revision_id}/credential` takes
  `{"credential": "..."}` with an optional exact boolean `"remember"`: without it the server
  holds the key in memory for that origin until it stops; with `true` it stores the key in the
  approved OS keyring for that revision. It never echoes the key;
- `POST /api/providers/{profile_id}/revisions/{revision_id}/check` runs one bounded explicit
  compatibility check: body `{}` checks the explanation contract, and
  `{"effect_scope": "bounded-signals-v1"}` checks the bounded-signal contract;
- `GET /api/providers/{profile_id}/revisions/{revision_id}/bounded-check` reads, without
  running anything, the bounded-signal check result this process already recorded for that
  exact revision, or returns `404` `provider-check-not-recorded` when none has run since the
  workbench started;
- `GET /api/runs/{run_id}/workbench-input` returns the verified schema-v10/schema-v9/schema-v8/schema-v7
  bounded assumptions/creative projection with the seed as an exact decimal string;
- `GET /api/runs/{run_id}/cognition-summary` returns verified schema-v9 explanation provenance
  or the schema-v10 bounded-signal summary;
- `GET /api/runs/{run_id}/cognition-records` returns verified bounded schema-v9 or schema-v10
  terminal records in selection order;
- `GET /api/runs/{run_id}/timeline?minute=&agent_id=&...` combines the immutable frame and
  bounded evidence/state pages from one verified run context; `/api/timeline`
  exposes the equivalent contract over the legacy viewer's startup-validated immutable
  context, and the individual read endpoints remain supported;
- `GET /api/runs/{run_id}/meta` and the other run-scoped reads under `/api/runs/{run_id}/...`
  mirror the `adlife city-view` API of that one saved run;
- `GET /` redirects (`307`) to `/guide`;
- `GET /guide`, `GET /guide/new`, `GET /guide/jobs/{job_id}`, and `GET /guide/runs/{run_id}`
  serve the guided view's start, setup, live progress, and story pages, and
  `GET /guide/runs/{run_id}/report` renders that saved run as a script-free HTML report. They
  accept only `lang=en`, `lang=de`, or `lang=fa` (English when absent) and refuse any other
  query with `422` `invalid-query`; the guide writes only through the `/api/*` routes above;
- `GET /guide/runs/{run_id}/report/download` takes the same `lang` query and returns the same
  report as a self-contained attachment named `adlife-report-{run_id}-{lang}.html`; neither
  report route writes to the workspace;
- `GET /api/runs/{run_id}/story` returns a verified schema-v10 run as `adlife-run-story-v1`
  story data with the map geometry and movement tracks; it accepts no query and refuses an
  older run with `404` `story-unavailable`;
- `GET /guide/assets/{asset_name}` serves the guide's allowlisted scripts, stylesheets, and
  translation catalogs.

Every scoped `/api/runs/{run_id}/...` read performs a fresh complete artifact verification;
library discovery re-reads every artifact byte and reuses an earlier full verification only
for a run whose exact content is unchanged. A request that still carries the removed
inspector's `X-AdLife-View-Token` header is refused with `409` `run-unavailable`. The
guided story's play control steps through the saved run's moments. It is a presentation
control, not the independent scientific replay performed by `adlife city-replay ROOT ID`.

Only one mutating provider/job operation is active; provider checks, credential writes, and
simulation jobs share the same admission boundary. No simulation request is held open:
clients poll the status URL. Cancellation during queued/evaluating/simulating or explanation
selection is observed at phase boundaries. Late cancellation
is refused once persisting starts; publication and verification continue, may still fail
safely, and reach `completed` only after independent verification. Operational job records
and their timestamps are bounded, process-local metadata and disappear on restart. Verified
run artifacts remain discoverable under `<workspace>/city-runs/<run-id>/`. Refreshing the
browser while the process is alive retrieves the active job from `/api/workbench` and
resumes polling its server-owned record; this is not restart recovery.
Every job response retains a frozen `accepted_settings` receipt through every phase. It
records the accepted city, scenario, campaign, exact decimal seed string, agent count,
days, deterministic response mode, explanation mode, exact profile revision, and aggregate
output-token ceiling; a completed job is emitted only when its verified result matches
that receipt. A schema-4 receipt also records `run_schema_version` 10, the effect scope, and
the funded response limit; its ceiling is the funded limit times the per-request
reservation for a local or remote source and 0 for rules or mock.

A schema-3 workbench run is schema-v9, and a schema-4 run (every guided run) is schema-v10;
schema-2/schema-v8 and schema-1/schema-v7 requests remain compatible. Schema-v7 through
schema-v9 have the same deterministic spatial evidence as schema-v6, and schema-v10 keeps the
same opportunity and attention evidence with its dual-state `spatial-response-v2` responses;
all add canonical `inputs/workbench.json`. The final manifest binds
`workbench_input_schema_version` and `workbench_input_sha256`; load, replay, discovery, and
inspection refuse a missing, changed, noncanonical, or execution-inconsistent sidecar.
The exact selected city is frozen in `inputs/city.json`; replay, metrics, reports, and
inspection are independent of later workspace-registry removal, replacement, or
corruption. Ordinary CLI `city-run --spatial-response` output remains schema-v6.

Workbench `settings.seed` must be a canonical decimal JSON string from `"0"` through
`"9223372036854775807"`; JSON numbers, signs, whitespace, exponents, decimals, and leading
zeroes are refused. The server converts the accepted string to the deterministic model's
bounded Python integer. The guided pages never convert it through JavaScript `Number`.

### Workbench model assistance: operations, sources, and credentials

A job request's operation (`cognition.effect_scope`) and source (`cognition.mode`) jointly
select the exact request and manifest pair; the presence of a provider profile never selects a
schema. The guided setup page always submits `bounded-signals-v1`:

| Operation (`effect_scope`) | Source (`mode`) | Workbench / manifest | Provider budget | Compatibility check |
| --- | --- | --- | --- | --- |
| `explanation-only` | `rules` or `mock` | schema 3 / V9 | none | not applicable |
| `explanation-only` | `local` or `remote` | schema 3 / V9 | 1,536 to 92,160 output-token ceiling | optional explanation check |
| `bounded-signals-v1` | `rules` or `mock` | schema 4 / V10 | none; every selected request resolves offline | not applicable |
| `bounded-signals-v1` | `local` or `remote` | schema 4 / V10 | 60, 120, 250, 500, or 1000 funded requests (runs saved earlier: 10, 20, 40, or 60) | exact bounded-signal check required |

**Explanation only.** `rules` is the offline default with a zero output-token ceiling.
`mock` is deterministic and offline. `local` and `remote` require an exact immutable
provider-profile revision plus an
aggregate ceiling from 1,536 through 92,160 tokens in 1,536-token steps; a ceiling of
15,360 funds 10 explanations. The numeric response
mode remains deterministic rules in every case. Provider explanations are noncausal and
cannot change movement, exposure, attention, response values, campaign state, budget,
purchase probability, metrics, events, or identities.

**Bounded AI cognition.** A schema-4 request's `cognition` member carries `effect_scope`
`bounded-signals-v1`, `mode`, `provider_profile_id`, `provider_revision_sha256`, and
`funded_response_limit`; the server derives the output-token ceiling, and an omitted limit
takes the source's default. New runs use selection policy `city-cognition-selection-v3`
(`spread-evenly`): the limit is spread evenly over the run's noticed responses in time order,
with no per-person cap, and every selected request is funded. Rules and mock send null profile
fields and the offline limit 1000: every selected request resolves locally and reserves no
tokens. Local and remote fund 60, 120 (local default), 250, 500, or 1000 (remote default)
requests; 1000 means every noticed ad, up to 1,000. Each funded request reserves 1,536 output
tokens: at most 6 HTTP attempts of up to 256 output tokens each (a first attempt, 3 transient
retries, and 2 schema repairs), so 1000 funded requests reserve 1,536,000 output tokens. Input
tokens and vendor charges are outside that reservation, and it is not a spending limit.
Provider work is bounded to 8 concurrent requests for a remote source and 4 for a local one,
at most 30 seconds per attempt, and 1800 seconds in aggregate. A failed attempt is retried
after an exponential backoff (1, 2, then 4 seconds, capped at 8, with a deterministic ±20%
jitter) or a server's `Retry-After` of up to 30 seconds, and no wait passes the aggregate
deadline; requests start in an interleaved order across the run, so a deadline cut thins every
day evenly, while results are still written in request order. Runs saved earlier keep
selection policy V2 and still verify and replay unchanged: earliest first, at most 4 noticed
responses per agent and 60 per run; the offline limit 60; 10, 20 (the default), 40, or 60
funded requests, with the remaining selected requests budget-skipped and neutral; 1,024
output tokens per funded request; 4 concurrent requests; and 180 seconds in aggregate.
`POST /api/jobs` returns 409 `provider-compatibility-required` until a bounded-signal check for
that exact profile revision has passed in the current process; an explanation check never
satisfies it. A local check waits up to 120 seconds for a model that is still loading and a
remote check up to 60 seconds for a slow gateway; each check's single attempt may use its whole
deadline, while run attempts keep the profile's frozen timeout. At most 5 checks start per 60
seconds, results are process-local, and a job cannot start while a check runs.
`GET /api/workbench` publishes these limits as
`capabilities.bounded_cognition`, which the guided setup page reads instead of pinning them. Movement,
attention, baseline responses, and the purchase-intention proxy stay rule-owned: the source
may only choose closed `affect`, `relevance`, `credibility`, `salience`, and `safety_flags`
categories, which AdLife maps through the frozen `city-cognition-influence-v1` policy into
the separate cognition state.

The supported wire contract is a narrow OpenAI-compatible Chat Completions subset: a
bounded, non-streaming `POST` to the exact startup-approved `/chat/completions` endpoint and
one strict JSON result. The bounded-signal result schema uses only keywords that OpenAI strict
structured outputs and vLLM grammar decoding both accept (no `uniqueItems`; the result model
refuses duplicate safety flags itself). A vLLM server's own metadata members, such as
`prompt_token_ids`, `prompt_text`, `metrics`, `stop_reason`, `reasoning`, and an empty
`tool_calls`, are admitted only as null or their observed type and are never parsed or kept
(the reasoning, echoed prompt, and metrics text in them is screened for the provider's own
key, an email, a vendor key shape, or a credential label or header name followed at once by
`:` or `=`, rather than the raw-body prose heuristics, so reasoning such as "basic
groceries" no longer discards an answer, while "Password: ..." still does); any other
unknown member is refused. An answer cut at the output cap (`finish_reason`
`length`) is accepted only when its text is a complete valid result; one that never closed,
or has no text, is an `invalid-json` result that the one schema repair may recover. A
profile revision may set `disable_thinking` (the guided connection card sets it for a remote
model ID that names Qwen): every
request, repair, and compatibility check of that revision then carries
`chat_template_kwargs` `{"enable_thinking": false}`, so a Qwen model on vLLM, such as
GapGPT's `gapgpt-qwen-3.6`, answers instead of spending the output cap thinking. The member
is part of the revision and of the frozen V10 provider snapshot and effective settings; it is
omitted while false, so existing revisions and runs keep their exact bytes, and it is never
sent to an endpoint whose profile leaves it off, such as OpenAI, which refuses unknown request
members. A ChatGPT or Codex product login, subscription token, or OAuth token is not accepted
as an API credential. The workbench reports `oauth: false`; it does not
implement product-login OAuth. Remote credentials resolve from an approved OS keyring or an
explicitly bound environment-variable name. Values are never returned by the APIs or stored
in profiles, artifacts, browser storage, prompts, logs, reports, or error text.

Schema-v9 adds `inputs/cognition.json`, `outputs/cognition.jsonl`, and
`outputs/cognition-summary.json`, all manifest-bound and independently verified. Live
provider explanations are nondeterministic; frozen schema-v9 replay is deterministic and
performs no provider call. Rules, mock, provider/cache, fallback, and budget-skipped outcomes
remain distinguishable in the verified `/api/runs/{run_id}/cognition-records` read.

Schema-v10 writes the version-2 `inputs/cognition.json`, `outputs/cognition.jsonl`, and
`outputs/cognition-summary.json` together with the dual-state `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`, all manifest-bound and
independently verified. Every selected request has exactly one terminal record whose source
is `rules`, `mock`, `provider`, `cache`, `fallback`, or `budget-skipped`; responses beyond the
selection caps are `unselected`. An `uncertain` category or any safety flag keeps the record
but applies the exact neutral vector. Live provider signals are nondeterministic; frozen
schema-v10 replay is deterministic and performs no provider call. A validated V2 cache entry
is reused only for the same causal request, profile revision, effective settings, and
contract hashes, so rerunning the same inputs in the same workspace may show `cache` instead
of `provider`.

The local profile still does not authenticate users, download reports, operate comparisons
or paired studies in the browser, supply a rights-reviewed real-city/worldwide catalog, use
calibrated population or traffic behavior, or predict sales. The model clock is an
illustrative seven-day-or-shorter simulation clock rather than observed local time.

## `adlife city-run [PACK | --city-id ID] [--places FILE] [--spatial-campaign FILE] [--spatial-response FILE] --output-root ROOT --run-id ID [--agents N] [--days N] [--seed N]`

Save a deterministic city run under `ROOT/city-runs/ID`. Supply
exactly one local `PACK` or verified catalog `--city-id`; neither and both exit 2. The
validated pack is frozen into the artifact, so catalog replacement cannot change
replay. `--agents` accepts 1–30 (default 20), `--days` 1–7 (default 7), and `--seed`
defaults to 42. Creating an existing ID exits 3 without overwriting it.
The command hashes every minute frame and the generated fictional assignments. A
completed artifact contains `run.json`, `inputs/city.json`, and `inputs/agents.json`.
With `--places`, schema-v3 artifacts also freeze `inputs/places.json` and
`inputs/place-assignments.json` plus both content hashes.
With `--spatial-campaign`, schema-v5 artifacts additionally freeze
`inputs/spatial-campaign.json`, `outputs/spatial-opportunities.jsonl`, and
`outputs/opportunity-summary.json`, then derive `outputs/spatial-attention.jsonl` and
`outputs/attention-summary.json`. The command validates the scenario against the exact
selected city and duration before reserving the run ID. JSON output includes both claim
scopes, model/probability metadata, hashes, bytes/counts and funnel totals. Opportunity
records retain `synthetic-opportunity-not-impression`; attention records say
`synthetic-attention-not-observed-behavior`. Every opportunity becomes one synthetic
impression and an order-independent draw below the fixed 0.5 probability becomes noticed.
This is an uncalibrated assumption. It does not affect cognition, state, budget, movement,
or purchase probability. Older schema-v4 artifacts remain readable as opportunity-only
evidence.

`--spatial-response` requires `--spatial-campaign`. Supplying `--spatial-campaign` alone
continues to create schema-v5; supplying both flags creates schema-v6 under run model
`illustrative-road-spatial-response-study-v1`. Before reserving the run ID, the command
loads strict local JSON, binds every fictional profile to the exact agent-ID set generated
for the run, binds every campaign to the exact scenario, and validates complete
campaign-scoped initial state. The response
input is not bound to one mobility assignment; the saved run separately freezes and hashes
its generated assignments. The schema-v1 field contract and exact JSON shape are in
[city-pilot.md](city-pilot.md). V6 additionally freezes
`inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`. Only persisted notices
create `spatial-response-v1` rule responses and atomic state updates, each carrying
`synthetic-response-not-observed-behavior`. The bounded purchase-intention value is an
uncalibrated proxy, not purchase probability, sales, or a transaction; the path adds no
provider call, memory, social propagation, budget mutation, purchase event, or movement.

JSON success output includes response model/claim identity plus the exact v6 receipt keys
`response_input_sha256`, `response_stream_sha256`, `response_state_sha256`,
`response_summary_sha256`, `response_stream_bytes`, `response_count`,
`state_update_count`, `response_campaign_count`, and `final_state_count`.
`response_input_sha256` is the canonical input fingerprint excluding the saved file's
trailing newline; `response_stream_sha256`, `response_state_sha256`, and
`response_summary_sha256` cover exact persisted bytes, including JSONL line endings and the
trailing newline in each single-document state/summary file. Human output prints the same
values with readable labels. A malformed,
mismatched, non-finite, oversized, or sensitive response document exits 2 without reserving
the run ID. A separate read-only command may summarize persisted evidence; it does not turn
it into an observed outcome.
Interrupted or failed publication may leave an incomplete, reserved directory; use a
new run ID after examining it. JSON mode emits one result document on stdout.

## `adlife city-replay ROOT ID`

Load the frozen inputs, regenerate every minute frame, and compare the full trace hash and
counts. For schema-v4, also re-evaluate and compare the canonical opportunity stream and
independent summary. For schema-v5, additionally recompute and compare the canonical
attention stream and summary, including the fixed 0.5 model contract. Schema-v6 repeats all
of those checks, then re-evaluates the frozen response input and verifies the canonical
response stream, complete final-state document, summary, hashes, bytes, and counts.
Schema-v7, schema-v8, and schema-v9 additionally require the canonical, hash-bound
`inputs/workbench.json` (input schema 1, 2, and 3, respectively) and reuse that validated
accepted input during replay. Schema-v8/v9 also verify the authored population and persona
assignments. Schema-v9 additionally verifies the complete frozen cognition input, terminal
record stream, summary, policies, budgets, and cross-references without resolving a
credential, profile registry, cache, or provider. Schema-v10 requires input schema 4,
regenerates the deterministic baselines and the selected request identities, validates every
frozen bounded-signal result against its exact request and the frozen policies, reapplies
`city-cognition-influence-v1` in canonical order, and requires the dual rule/cognition
response stream, final states, summaries, and manifest receipt to match exactly, again
without a provider, cache, keyring, environment variable, profile registry, or network
request. Success emits
`"identical": true`; missing, damaged,
partial, or incompatible artifacts exit 4 without modifying the source files. This
proves replay against the saved artifact on a compatible implementation/runtime; it
is not cryptographic authentication against an owner rewriting all artifact files.
Saved city runs are not zone advertising runs and do not claim measured traffic,
real-person behavior, or externally valid geographic campaign effects.

## `adlife city-metrics ROOT ID [--layer attention|response]`

Validate a saved run and derive deterministic spatial metrics without changing or caching
anything in the artifact. `city-metrics --layer attention` is the default and preserves
the exact prior schema-v5/schema-v6/schema-v7/schema-v8/schema-v9 JSON shape, which schema-v10
runs share: persisted opportunity and
attention evidence only, under
`synthetic-metrics-not-observed-outcomes`, with exact numerator, denominator, and source
paths. It does not fold response records or final campaign state.

`city-metrics --layer response` requires schema-v6, schema-v7, schema-v8, schema-v9 or schema-v10.
For schema-v6 through schema-v9 it emits the separate
`spatial-response-metrics-v1` document under
`synthetic-response-metrics-not-observed-outcomes`. It reports response count, reach,
frequency, planned mean rule sentiment/recall deltas, and complete-grid initial/final/change
state receipts overall and by campaign. Channel response series are event-only; committed
nonlinear state is not allocated to a channel. Purchase intention is an uncalibrated proxy,
not purchase probability or sales. Corrupt, partial, older, or incompatible artifacts exit
4.

For a schema-v10 run, `city-metrics --layer response` emits the version-2
`spatial-response-metrics-v2` document under
`synthetic-dual-state-metrics-not-observed-outcomes`, dispatched by the exact manifest version.
Its `rule_baseline` family (`rule-owned-synthetic-baseline-not-observed-outcomes`) applies the
V1 receipts to the deterministic baseline responses and committed rule states, so it keeps the
uncalibrated purchase-intention proxy. Its `provider_dependent_cognition` family
(`provider-dependent-exploratory-not-campaign-effect`) reports mean applied sentiment and
recall, synthetic cognition-state receipts, category, source, fallback, and neutralization
counts, funded coverage, and the exact cognition-input, contract, influence-policy, and
profile/model/revision provenance; it has no purchase, delta, effect, or lift member. Human
output prints the families under `Rule baseline (rule-owned)` and `Provider-dependent
cognition (exploratory, not a campaign effect)`. Category counts cover every selected terminal,
including the fixed neutral vectors of rules, fallback, and budget-skipped terminals, and the
output states how many of those it includes.

## `adlife city-compare ROOT CONTROL_ID TREATMENT_ID [--layer attention|response]`

Compare two fully verified runs only when city, place assignments, trace model, seed,
duration, and population match. `--layer attention` is the default, retains the exact prior
output, accepts schema-v5, schema-v6, schema-v7, schema-v8, schema-v9, or mixed compatible evidence, and never
treats
response state as an attention metric. A schema-v10 run compares only with another
schema-v10 run; pairing it with any schema-v5 through schema-v9 run is refused before either
run is projected (exit 4). Same-run A/A produces exact-zero deltas. A changed
normalized placement/channel structure is `opportunity-confounded`, never a channel effect,
and the result carries `synthetic-comparison-not-causal-or-observed-effect`.

`city-compare --layer response` refuses any schema-v10 run for now, because its V1 contract has
no cognition family. It requires schema-v6, schema-v7, schema-v8 or schema-v9 and emits
`spatial-response-metrics-comparison-v1` under
`synthetic-response-comparison-not-causal-or-observed-effect`. It independently classifies
opportunity structure as `matched-opportunity-structure` or `opportunity-confounded` and
numeric response assumptions as `matched-response-assumptions` or
`response-assumption-confounded`. All deltas are treatment minus control. The command never
modifies either run; provenance mismatch is invalid input and corrupt/incompatible artifacts
exit 4.

## `adlife city-study ROOT STUDY.json`

Analyze the explicit study definition's 2–100 saved, seed-matched run pairs using the
contiguous seeds `0..N-1`. Attention-only studies use homogeneous schema-v5, schema-v6,
schema-v7, schema-v8 or schema-v9 runs; attention-and-response studies require homogeneous schema-v6,
schema-v7, schema-v8 or schema-v9. Schema-v10 runs are refused for now in either scope, so
`city-report` cannot publish them either. Every source is verified, and
the command does not rerun, discover, modify, cache, or publish artifacts. There is no
output or discovery option. The strict path-free JSON definition is capped at 64 KiB.
JSON emits the fully revalidated compact result; human
output labels synthetic evidence, opportunity/response-assumption confounding, and
A/A status. The fixed paired bootstrap describes simulator seed variation, not
population confidence, sales, observed outcomes, or causal effects. Fewer than 50 seeds
are exploratory; 50 or more follow the full protocol, not a registered study. Invalid
definitions or scientific incompatibility exit 2; missing, corrupt, unsupported, or
failed verified A/A artifacts exit 4.

The result model is `spatial-paired-study-v1` with claim scope
`synthetic-study-not-observed-or-causal-effect`. One seed pair is the experimental unit;
agent, notice, event, opportunity, and state rows are never pooled across seeds. Exact
assignment, trace, and optional place-assignment hashes—not the seed number alone—establish
common-random-number evidence within a pair. Each statistic includes mean, sample standard
deviation, median, a deterministic 10,000-resample paired interval, nullable standardized
difference, sign counts, agreement, and the 0.8 direction rule; it emits no p-value.

## `adlife city-report ROOT STUDY.json`

Analyze the same explicit verified study as `city-study`, then publish a self-contained
zero-JavaScript evidence ledger at `ROOT/city-reports/<study_id>.html`. There is no output,
force or browser-opening option. Any existing destination exits 3 unchanged; report-directory
symlinks and junctions are refused. JSON emits the nine-field receipt with a relative POSIX
report path. Its exact fields are `schema_version`, `format_id`, `claim_scope`, `study_id`,
`study_definition_sha256`, `study_result_sha256`, `report_path`, `report_sha256`, and
`report_bytes`; hash and size cover the exact emitted UTF-8/LF bytes. The report is capped at
8 MiB, contains no JavaScript or network-capable resource, makes no network request, and
uses a CSP that permits only the exact hashed inline stylesheet. Scientific/input and
source-artifact errors retain `city-study` exit codes; filesystem and unexpected failures
exit 1, interruption 130.
Publication uses an atomic no-clobber hard link after fsyncing the complete file. Directory
fsync after the link is best effort: an unsupported or failed post-commit directory fsync is
non-fatal, so the contract does not promise the new directory entry survives sudden power
loss on every filesystem. Source city-run artifacts remain unchanged.

## `adlife city-view ROOT ID [--port N]`

Open a **validated saved** city mobility run in the same read-only browser timeline
at `http://127.0.0.1:8765` (loopback only). The run is fully verified before the
server binds; a corrupt or partial run exits 4. The HTTP API cannot select another
run or open a filesystem path. The header displays the saved run ID and artifact
schema version. For schema-v4, schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9 runs, the viewer adds a
read-only current-minute opportunity
panel with persisted totals, channel counts and canonical records for the timeline
scrubber. Its bounded `/api/opportunities` endpoint accepts a required minute, optional
saved agent ID, offset and a maximum page size of 100; `/api/opportunity-summary` exposes
only validated scenario metadata and aggregate counts. Neither endpoint accepts a path or
changes the run. Every record remains `synthetic-opportunity-not-impression`; the viewer
does not invent a response or outcome. Schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9 also add bounded
`/api/attention-events` and `/api/attention-summary` endpoints and displays persisted
current-minute attention evidence. Those records remain
`synthetic-attention-not-observed-behavior`; noticed means only that the deterministic draw
was below the uncalibrated 0.5 threshold. Schema-v6, schema-v7, schema-v8, and schema-v9 additionally expose the
exact persisted
response artifact through `/api/response-summary`, bounded and filterable
`/api/response-events`, and bounded `/api/response-state`. The state endpoint returns
complete final end-of-run campaign state, not state at the scrubbed minute, and labels that
scope `final-end-of-run-not-scrubbed-minute`. All three routes retain
`synthetic-response-not-observed-behavior`, accept no path/run selector, and never mutate
the source artifact. The three current-minute evidence rails use fixed 100-record Previous
and Next pages with exact displayed ranges. They preserve canonical server order, reset to
the first page when the minute changes, retain the current page during same-minute agent
selection, and refuse stale or inconsistent page responses without replacing the last
committed evidence. Opportunity and attention map markers represent only the displayed
page; full-minute totals remain unchanged. `--port` accepts 1–65535. Like
`adlife city`, this interactive command accepts human output mode only and does not launch
a browser automatically. Schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9 runs also expose a read-only
`/api/spatial-metrics` receipt ledger. It uses the same exact numerator, denominator, and
source paths as `city-metrics`, remains attention-only, and does not write to the artifact.
A fully prevalidated schema-v6, schema-v7, schema-v8 or schema-v9 viewer additionally exposes the separate
GET-only, path-free `/api/spatial-response-metrics` document; mutation methods return 405 and older
or unconfigured runs return 404.

A schema-v10 run opens in the same viewer, with the same opportunity, attention,
response-summary, and attention-only `/api/spatial-metrics` routes, plus the bounded cognition
inspector. Its `/api/response-events` pages (schema 2) carry `spatial.applied-response` and
`spatial.dual-state-updated` records, `/api/response-state` returns final
`{rule_state, cognition_state}` pairs labelled `final-end-of-run-not-scrubbed-minute`, and
`/api/spatial-response-metrics` returns the `spatial-response-metrics-v2` document. Only a V10
viewer adds the GET-only, path-free `/api/workbench-input`, `/api/cognition-summary`, and
`/api/cognition-records` routes; a query string is refused with 422. The page re-checks the
frozen contract hashes and influence arithmetic in the browser, shows coverage, a source and
effect badge for each response, and the per-response trace from deterministic baseline to the
atomic rule/cognition state update, and fails closed if its helper assets are missing.

## `adlife city-import INPUT --output PACK --city-id ID --name NAME [--schema-version 1|2] [--largest-component]`

Convert a locally supplied, complete OpenStreetMap Overpass JSON street extract into
a validated city pack. This command performs no network request. Schema 1 remains the
compatibility default. Schema 2 additionally requires `--time-zone`, `--source-date`
and `--source-version`; it preserves explicit direction, complete way geometry through
successive nodes, exact bounds, normalized source provenance and declared omissions.
Input is limited to 16 MiB; output follows the city-pack 4 MiB / 10,000-node /
20,000-road limits. More than 100,000 total elements, 50,000 node elements, 50,000 way
elements, 100,000 way-member references or 20,000 selected road segments is refused.
The destination must be new; an existing file or symlink exits 3 and is not modified.
Malformed, incomplete, unsupported or disconnected geometry exits 2 without creating
an output. `--largest-component` explicitly discards all but the largest strongly
connected directed road component, with deterministic tie breaking and reported loss
counts. JSON mode returns the canonical city-pack fingerprint (`pack_sha256`, excluding
the file's trailing newline), counts and destination as one document; v2 also returns
bounded quality counts. The pack carries OSM ODbL attribution, but validation or import
does not grant commercial redistribution rights and does not add a real-world pack to
the catalog. See [city-pilot.md](city-pilot.md) for supported roads, rights review and
scientific limits.

## `adlife population generate [--size N] [--seed N] [--locale LC] [--out FILE] [--as FORMAT]`

Generate a fictional population document. `--size` 1–30 (default 20), `--seed` (default
42), `--locale` `fa-IR` (default) or `en-US`, `--out` writes YAML to a file instead of
stdout, `--as yaml|json` selects the output document format (default YAML).
Global `--format json|jsonl` selects JSON on stdout. An existing `--out` file is
refused with exit 3; machine mode reports the newly written file in a JSON result.

## `adlife campaign import PATH [--project-root DIR]`

Validate one campaign YAML against the project and print the normalized campaign as JSON.
Asset paths inside the campaign are resolved and confirmed to lie beneath the project root
before they are opened; anything else is refused (exit 2).

## `adlife run PROJECT [options]`

Run one study from its first tick to `run.completed`, persisting every artifact.

| Option | Meaning |
| --- | --- |
| `--campaign FILE…` | Replace the project's campaigns with these project-relative YAML files. |
| `--run-id ID` | Lowercase letters, digits and hyphens, beginning with a letter or digit, at most 40 characters. Windows device names are refused on every platform. An existing identifier exits 3. |
| `--mode rules\|hybrid\|replay` | Default `rules` needs no provider; `hybrid` uses the configured provider. The legacy `replay` value exits 2 and directs you to `adlife replay PROJECT RUN_ID`, which has the required frozen source identity. |
| `--days N` | Override simulated days (1–7). |
| `--population-size N` | Override population size (1–30). |
| `--seed N` | Override the run seed. |
| `--live` | Open the live dashboard when stdin and stdout are an interactive terminal and `TERM` is not `dumb`; otherwise fall back to headless. |
| `--headless` | Never open a dashboard (the default behaviour). |
| `--no-headless-fallback` | With `--live`: exit 2 instead of falling back when no terminal is available. |
| `--cache-dir DIR` | Cognition cache directory for hybrid/replay modes (default `cache/` under the study). |

`adlife --format json run PROJECT --live` and the corresponding `jsonl` combination
are refused (exit 2): a dashboard cannot share machine-readable stdout. Generated run
identifiers include a digest of the final scenario, seed and mode, including campaign
overrides. Identical inputs choose the same identifier and an existing run is refused.

## `adlife replay PROJECT RUN_ID [--provider-mode MODE] [--cache-dir DIR]`

Re-execute a stored run from its recorded inputs and verify the fresh event stream against
the recorded one. By default the provider mode is inferred from the source artifact;
`--provider-mode rules|hybrid|mock` explicitly overrides it. Mock replay uses the original
request identity. Hybrid replay validates cached successful answers and reconstructs
recorded rule fallbacks without constructing a live provider or reading credentials. Emits
`"replay-identical": true` on success. Corrupted artifacts exit 4; a mismatch is reported
as a failure, never repaired. A replay destination (`replay-<run-id>`) that already exists
is refused with exit 3: replay never deletes or overwrites run artifacts, and the stored
source run is never modified. Long source identifiers receive a digest suffix in the
derived replay identifier to avoid truncation collisions. Replay uses recorded model
parameters and ignores subsequent edits to project YAML.
For an interrupted source, replay verifies only the committed prefix up to its recorded
stop minute, including a stop before the first tick. A verified prefix exits 0 with
`"status": "interrupted"` and its `final_minute`; it does not execute later cognition.
Running or failed sources lack a supported terminal boundary and are refused with exit 4.

## `adlife compare CONTROL TREATMENT [--seeds N] [--treatment-path PATH]`

Paired whole-run comparison of two studies: both arms run per seed with the same
population, world, and keyed random streams. `--seeds` 1–200 (default: the quick-study
count, 20). `--treatment-path` declares which scenario document the treatment may differ in
(default `campaigns`). The A/A case (comparing a study with itself) must return exactly
zero paired differences.
The CLI normalizes the treatment's directory-derived scenario identifier and display
name to the control's before validation, so separate study directories can be compared.
The output label still names both directories. Population, initial state, world, duration
and all other scientific inputs retain the core's strict paired-design checks; a refused
design exits 2 with a concise diagnostic.
Comparison run identifiers use a bounded digest of the full comparison label, frozen
scenarios, declared treatment paths and seeds. Different comparisons can share a control
study without overwriting earlier runs; repeating the identical comparison exits 3.

## `adlife doctor [PATH] [--offline] [--no-color]`

Report installation readiness: CLI and core versions, packaged resources, provider
availability (mock and rules always; live providers only when not `--offline`), storage
writability, optional project schema check, and console encoding. `--offline` performs no
network access at all. Doctor returns a check report with `all_ok`; inspect that field
for readiness, since completed diagnostic checks return exit 0 even when a check fails.
Live probes refuse credential-bearing URLs and non-loopback plaintext HTTP.

## `adlife report PROJECT RUN_ID [--output FILE]`

Write the self-contained HTML report for a stored run (default destination
`reports/<run-id>.html` under the study). The report is a single file — charts, styles,
and the Plotly runtime inlined — that renders with the network disconnected, opens with a
synthetic-data disclosure, and ends with the manifest block needed to reproduce the run.
An output destination inside the study's `runs/` tree is refused with exit 3.
An existing non-HTML destination is also refused, protecting configuration, population,
campaign documents and other source files from being replaced by report output.
The default report directory must resolve inside the study, including through directory
links or junctions. An explicit `--output` may name a destination outside the study.

## `adlife demo [--headless] [--dir DIR] [--seed N]`

The offline demonstration: build a packaged demo study (20 agents, 3 simulated days, mock
provider, two campaigns), run it, and open the live dashboard when a real terminal
exists — `--headless` prints a summary instead. The study is created in a temp directory
unless `--dir` is given (an existing directory is refused). Requires no network and no
credentials. JSON and JSONL output automatically keep the demo headless.
The demo uses a location-independent scenario identity and a run identifier derived
from its frozen inputs and seed. With the same packaged inputs, seed and code, fresh
demo directories produce identical scenario, event and metric exports. Existing demo
directories are still refused; the destination does not alter mock responses.

## JSON output

`--format json` prints one document per command; `--format jsonl` prints one JSON object
per line for stream-shaped output (events, live progress). Diagnostics always go to
stderr, so `adlife --format json run my-study > doc.json` is clean piped output.

## Fictional persona request contract

The guided setup page loads GET /api/persona-templates and submits schema-4
population.personas plus cognition settings to `POST /api/jobs`; API clients may also submit
schema 3, and schema-1/schema-2 requests remain compatible. The exact pairs are (1,7), (2,8), (3,9), and (4,10), with
schema-v9 used for schema-3 and schema-v10 for schema 4. Future versions, booleans, floats,
and other pairs are refused. Schema 4 uses the same persona contract. In new runs (prompt
contract `city-cognition-persona-prompt-v3`) its bounded-signal request asks the AI to answer
as the person and carries the persona card (name, age, occupation, background, MBTI,
interests, and the six traits), the rule-track sentiment, recall, and purchase intention just
before the notice, the agent's earlier noticed responses for the campaign, the situation, and
the creative, but never an earlier AI answer. Runs saved earlier (prompt contract
`city-cognition-signal-prompt-v2`) carry only interests, the response traits (with the recall
encoding of the response's channel), and initial sentiment and recall, never name, age,
occupation, background, MBTI, or purchase intention. The template GET is
read-only and returns exactly ten validated fictional records (catalog ceiling 32,768
canonical UTF-8 bytes). Requests allow 1–30 agents and 1–7 days. Persona records are capped
at 3,072 canonical UTF-8 bytes, populations at 98,304 and workbench inputs/HTTP bodies at
131,072. Counts, versions and seeds are exact bounded integers after seed-string transport;
non-finite values and unknown members are refused before job reservation.

Each persona has persona_id, count, fictional: true, display_name, optional age (18–65),
occupation, background and MBTI, plus explicit interests, six traits and initial_state.
Persona IDs are `persona-001` through `persona-999`; each count is 1–30 and their sum must
equal `settings.agent_count`. Names/occupation labels are bounded to 80 characters,
background to 800, and interests to 1–12 unique 1–80-character strings. Optional MBTI
is null or one of the 16 uppercase labels; it is never converted into numeric assumptions.
Name, age, occupation, background and MBTI are noncausal for the rules; new schema-v10 runs
send them to the AI as the persona card it answers as. The six causal traits are
price_sensitivity, novelty_seeking, advertising_skepticism, mobile_recall_encoding,
roadside_recall_encoding and impulsivity; initial_state contains brand_sentiment,
recall_strength and purchase_intention. All six traits and recall/intention are finite values
from 0 through 1; sentiment is from -1 through 1. IDs/counts/assignments are structural, with no
client-supplied mobility agent IDs, coordinates or hashes. Security screening refuses
recognized identifiers, credentials, structural secret labels and unsafe display text;
it does not prove absence of personal information. Supply fictional data only.
Before job reservation, validation and job submission also apply what the job applies
later: its re-parse of the frozen input (interest sets sorted) and the screens of the
cognition context that schema-3 and schema-4 planning builds. Persona or campaign text
refused there, such as path-like slashes (`Node.js/React`, `B.Sc./M.Sc.`, `/r/name`,
`4.5/5`) or a bare credential label, returns `400` `invalid-persona-text` or
`invalid-campaign-text` naming the field (a persona by its position in the request).
A new persona's text is also refused there for a phone or identity number in the shapes
recognized since saved runs were made (`555.123.4567`, `01.23.45.67.89`, `0151/2345678`, or
digits separated by no-break spaces). Saved runs are never re-screened, so a saved run
holding such text still opens.

The guided setup page chooses templates and their counts; the server validates every
request and IDs cannot be renamed. V8 verified input DTOs expose normalized
personas/assignments; the `adlife city-view` inspector of a V10 run collapses noncausal
context. V8 works with city-view, city-replay, city-metrics, city-compare and homogeneous
all-v8 city-study sources. Paired studies accept descriptive-only changes and refuse causal
profile/state changes. Browser comparison/study/report download remains unshipped.
The guided view's report download (`/guide/runs/{run_id}/report/download`) covers one saved
schema-v10 run only; it is not a comparison or study report.
