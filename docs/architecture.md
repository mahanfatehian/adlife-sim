# Architecture

AdLife Lab is a local-first, offline-capable agent-based simulator built as a layered
system with one rule above everything else: **the model core knows nothing about the
outside world.**

## Layers

```
┌──────────────────────────────────────────────────────────┐
│  cli/        command tree, output contract, wiring        │
│  tui/        live dashboard (read-only Textual adapter)   │
│  reporting/  self-contained HTML report rendering         │
│  city/       local FastAPI mobility pilot + vanilla viewer  │
├──────────────────────────────────────────────────────────┤
│  adapters/   SQLite run store, cognition providers, cache │
├──────────────────────────────────────────────────────────┤
│  core/ports/ interfaces the outside world implements      │
├──────────────────────────────────────────────────────────┤
│  core/       domain contracts, simulation, experiments    │
│              (imports no CLI, no DB driver, no provider)  │
└──────────────────────────────────────────────────────────┘
```

`adlife.core` imports no CLI framework, no terminal UI, no database driver, no plotting
library, and no model provider. Interfaces to the outside world are defined as ports in
`core/ports/` and implemented in `adapters/`. The import ban is enforced by a test, not
convention.

## Geographic mobility pilot

The opt-in `adlife city` path is a separate model under `core/domain/city.py` and
`core/simulation/city_mobility.py`. It does not modify the campaign engine's frozen
scenario, 15-minute tick, event store, or replay format. A versioned, immutable local
road graph is validated and canonicalized before a deterministic, seed-keyed schedule
uses directed road paths. Version 1 remains readable; version 2 preserves road geometry,
explicit traversal direction, exact bounds, source provenance, an IANA time zone and
known omissions. The FastAPI adapter loads one pack at startup and exposes read-only
metadata, geometry, fictional agents and minute-addressable frames; the vanilla browser
client only renders those frames.

An optional immutable `CityPlaceSet` is a separate core contract bound to the exact city
ID and canonical pack SHA-256. It names role-specific synthetic home, workplace and
leisure points at validated road nodes and records whether each was fictionally authored,
source-derived or inferred. `CityMobility` assigns these candidates with keyed draws in
canonical order, validates home capacity and every directed itinerary before generating
frames, and exposes assignments as data rather than mutable agent fields. Neither the
web adapter nor the CLI can inject an unvalidated assignment.

The separate local city workbench is an application-layer adapter under `adlife.city`,
not a new core dependency. Its server serves only the guided presenter pages under `/guide`
(its root address `/` redirects there with `307`) and the local JSON API under `/api/*`; the
earlier technical workbench page, the saved-run inspector at `/runs/{run_id}`, and their
`/static/*` and `/assets/workbench*` script and style assets were removed. Its local
deterministic path is deliberately bounded:

```text
guided pages at /guide (packaged vanilla browser) or a local API client
        -> loopback schema-3 or schema-4 HTTP (schema-1/schema-2 compatibility)
        -> same-origin, CSRF, size and strict-JSON boundary
        -> packaged/workspace city + fictional creative resolution
        -> immutable CityMobility / SpatialCampaignScenario / SpatialResponseInput
        -> one process-local worker:
             schema 3: simulate -> explain -> publish -> verify
             schema 4: plan cognition -> request signals -> apply mapping -> publish -> verify
        -> schema-v9 or schema-v10 artifact (schema-v7/schema-v8 compatibility)
           + verified run-scoped read API, guided story/report (schema-v10)
```

`adlife city-workbench --workspace PATH` first checks every unresolved workspace path
component for links, junctions, reparse points and non-directories, then pins the resolved
directory identity. HTTP clients never submit filesystem paths, creative copy, derived
  coordinates/hashes or mobility agent IDs. Schema-2/schema-3 clients submit bounded persona IDs and
explicit `fictional: true` records. Validation constructs and independently
checks complete domain inputs but performs no network request, reserves no run ID and
writes no artifact. Job submission repeats that construction synchronously, freezes the
accepted values, refuses collisions, and hands them to one process-local worker. Its
  legacy phases are `queued`, `evaluating`, `persisting`, `verifying`, then one terminal
state. Cancellation is cooperative through evaluation. Late cancellation is refused after
persistence starts; publication and independent verification continue, may still fail
safely, and reach `completed` only after the verified result matches the frozen receipt.
Wall-clock transition timestamps are operational display metadata and never enter
  scientific state or hashes. Schema-3 adds `simulating`, `selecting-explanations`, and
  `requesting-explanations` before the same manifest-last persistence boundary; schema 4
  instead reports `planning-cognition`, `requesting-cognition`, and `applying-cognition`.
Every job view also carries the same frozen `accepted_settings` receipt through every
phase (city, scenario, campaign, exact decimal seed, agent count, days, and rules mode);
completion is accepted only when the verified result matches that receipt.

The same pinned workspace owns an immutable `city-packs/` registry. A dedicated upload
route accepts one strict UTF-8 CityPack V2 document, with independent 4 MiB raw and
canonical-storage ceilings while ordinary JSON writes retain their 128 KiB ceiling. A
resolver combines those records with one verified snapshot of the packaged catalog;
validation and job acceptance receive the exact resolved pack instance rather than looking
it up again later. Workspace entries are always classified
`operator-supplied-unreviewed`: structural validation cannot grant ownership, license, or
redistribution rights. Source URL fields are inert text, are not dereferenced, and cause no
outbound request; the only browser/API traffic is same-origin loopback. Registry corruption
blocks catalog and new-run operations but not startup, saved-run reads, or replay.

The browser owns one unsaved draft and presentation state only. The guided setup page
retrieves verified creative and persona choices, sends the seed as a canonical exact decimal
string, and submits a schema-4 draft that the server validates completely before execution
(schema-3 creates schema-v9, and schema-v8 and schema-v7 remain the authoritative schema-2
and schema-1 compatibility paths). The HTTP adapter
converts that string once to a bounded Python integer; the existing strict domain
construction is still authoritative. A browser refresh can recover the active
process-local job and resume polling from `/api/workbench`, but a process restart cannot
recover transient job history.
The guided client never computes a route, coordinate, hash, metric, or simulation
result.

The run repository scans at most 256 portable run identifiers and lists only fully verified
artifacts in stable order. Each listing re-reads every artifact byte of each candidate under
the store's link-refusing rules: a candidate whose exact content fingerprint matches an
earlier full verification in this process reuses that verification's summary, while any
changed byte, file, or path, or anything the store would refuse, sends it through a new full
`CityRunStore` verification.
Every scoped read under `/api/runs/{run_id}/...` loads and freshly verifies the selected
artifact before it answers, and listing runs re-checks every artifact byte before it reuses a
verification. A request that still carries the removed inspector's view-token header is
refused rather than served. The guided story API at `/api/runs/{run_id}/story` caches at most
eight verified stories, each bound to the exact bytes it was built from, so a changed file is
rebuilt rather than trusted. The legacy single-run viewer (`adlife city-view`) and the
workbench routes share one immutable projection service; the latter mirrors scientific reads
under `/api/runs/{run_id}/...`. For schema-v7, schema-v8, and schema-v9,
`/api/runs/{run_id}/workbench-input` projects verified frozen assumptions and the exact
creative snapshot; it never exposes a filesystem path or treats the presentation
projection as canonical artifact bytes. A run-scoped response-capable interaction uses the
additive GET-only
`/api/runs/{run_id}/timeline` projection: frame, bounded
opportunity/attention/response pages, and selected-agent final state are built from that
one immutable context. The legacy `/api/timeline` contract uses its startup-validated
immutable context, and legacy individual endpoints remain compatible. FastAPI, the worker,
workspace pinning, CSRF and packaged-resource loading stay outside `adlife.core`. The
core includes pure road-coordinate, V9 and V10 manifest and native experiment contracts; none
imports workbench adapters, providers, HTTP, or storage frameworks.

A schema-3 workbench execution adds frozen noncausal cognition evidence as schema-v9;
a schema-4 execution adds frozen bounded cognition signals and a dual rule/cognition state as
schema-v10 (see [the bounded city cognition seam](#the-bounded-city-cognition-seam-schema-v10));
schema-2 remains schema-v8 and schema-1 remains schema-v7. Canonical
`inputs/workbench.json` freezes the complete accepted draft and selected creative-template
snapshot; the final manifest binds its schema and `workbench_input_sha256`. Publication,
load, replay, metrics, studies and run inspection require a canonical, hash-matching,
execution-consistent sidecar. Ordinary CLI `city-run --spatial-response` output remains
schema-v6 because it did not receive that complete workbench provenance.

`core/domain/spatial_campaign.py` is another separate immutable boundary. A schema-v1
spatial scenario is content-addressed, binds to an exact city ID/hash, references
fictional campaigns by stable ID, and declares billboard/phone placement assumptions.
Core validation resolves road IDs and traversal directions, interpolates the complete v1
or v2 road geometry, and refuses a declared coordinate more than one meter from its
physical road fraction; it never snaps or mutates input. The city adapter only loads a
bounded local JSON document and the CLI only reports validation evidence.

`core/simulation/spatial_opportunity.py` is the C2 pure evaluation boundary.
`CityMobility.road_traversals()` exposes continuous directed itinerary intervals without
changing the compatibility-critical minute-frame stream. The evaluator revalidates C1
bindings, derives half-open-window roadside crossings and an explicit orientation
heuristic, evaluates activity-filtered counter-based phone draws, applies
placement/agent/day caps, and returns frozen typed opportunities plus every funnel
denominator. Opportunity IDs and phone draws are keyed and order-independent. The result
literally carries `synthetic-opportunity-not-impression`. It performs no I/O and cannot
change movement, state, budget, cognition or purchase probability.

C3a persists that exact C2 output without broadening its meaning. A schema-v4 city run
freezes `inputs/spatial-campaign.json`, streams canonical records to
`outputs/spatial-opportunities.jsonl`, writes the independently derived
`outputs/opportunity-summary.json`, and binds all three hashes, byte/count bounds and the
mobility trace in the final manifest. Replay re-evaluates the frozen inputs and compares
the normalized stream and summary. An opportunity alone does not reach cognition, agent
state, purchase logic, or the later response rules.

`core/simulation/spatial_attention.py` is the separate C3b evidence boundary. For each
validated opportunity it emits exactly one synthetic impression and uses an
order-independent SHA-256 keyed draw to record notice when the draw is below the fixed 0.5
probability. The probability is deliberately neutral and uncalibrated. Campaign copy,
campaign identity, creative content, channel and provider configuration are excluded from
the draw key, so they cannot control notice. Records carry the literal claim
`synthetic-attention-not-observed-behavior`. The pure evaluator cannot call cognition,
create arbitrary domain events, mutate agent state or budget, change movement, or affect
purchase probability.

`core/domain/spatial_response.py` and `core/simulation/spatial_response.py` form the C3c
rules-only response boundary. `--spatial-response` supplies strict, finite fictional agent
traits, campaign assumptions, and complete initial campaign-scoped state bound to the exact
agent-ID set generated for the run and to the exact city, scenario, campaign IDs, and
creative hashes. It is deliberately not bound to one mobility assignment; every saved run
separately freezes and hashes its generated assignments. Only a validated persisted
`spatial.noticed` record can cause a `spatial.response`; ignored impressions are no-ops.
All notices for the same agent and campaign in the same minute read the same immutable
pre-minute state, then commit one commutative, atomic state update. The evaluator is
`spatial-response-v1`; the run model is `illustrative-road-spatial-response-study-v1`, the
summary model is `spatial-response-artifact-v1`, and the final-state document model is
`spatial-response-state-v1`. Every response and state-update JSONL record carries
`synthetic-response-not-observed-behavior`; the final-state document carries the claim at its
top level rather than on each state entry.

Schema-v6 freezes `inputs/spatial-response.json`, canonical response and state-update
records in `outputs/spatial-responses.jsonl`, complete end-of-run campaign state in
`outputs/response-state.json`, and its independent receipt document in
`outputs/response-summary.json`. Manifest-last publication binds the canonical response-input
fingerprint plus exact persisted stream, state-document, and summary hashes, bytes, and
counts; replay re-evaluates frozen v6 inputs and refuses any mismatch. Purchase intention is
a bounded, uncalibrated proxy, not purchase probability or sales. This boundary creates no
provider call, prose cognition, memory, social propagation, budget mutation, purchase event,
movement decision, or arbitrary identity.

The city adapter owns file and package-resource loading. Its packaged catalog is an
offline, content-addressed index: it verifies each v2 resource's canonical SHA-256 and
matching ID/schema/metadata before selection. Exactly three bundled entries are
`fictional-fixture` data and no real city is represented as rights-reviewed. The separate
workspace registry accepts immutable local V2 uploads without adding them to the packaged
CLI catalog. The server binds to loopback, has no HTTP input for filesystem paths or
secrets, and makes no outbound network request. Catalog selection, import, simulation,
replay and viewing do not contact a public tile service or geocoder and do not accept an
arbitrary resource URL. A source URL inside a pack remains inert metadata, not a fetch
target.

This boundary is deliberate: animating old abstract zone changes on a city map would
misrepresent the science. Saved city studies use separate strict v1/v2/v3/v4/v5/v6/v7/v8/v9/v10
`CityRunManifest` contracts, a core full-minute trace digest, and a no-clobber
`CityRunStore`; v1-v3 remain mobility-only.
V3 freezes the canonical place set and its generated assignment document alongside the
pack under `<workspace>/city-runs/<run-id>/`; their hashes are part of the final manifest.
V4 additionally freezes the validated spatial scenario, opportunity stream and summary.
V5 adds canonical `outputs/spatial-attention.jsonl` and
`outputs/attention-summary.json`, with their hashes, sizes, counts, fixed model identity,
0.5 probability and claim scope bound into the manifest.
V6 retains all v5 evidence and adds the four manifest-bound C3c response artifacts described
above; ordinary `--spatial-campaign` runs remain v5, while `--spatial-response` selects v6.
V7 retains the exact V6 scientific evidence and adds only the canonical, manifest-bound
workbench input sidecar described above.
V8 freezes schema-2 persona definitions and assignments in that same sidecar while
retaining V6 scientific evidence and the exact input-2/manifest-8 binding. V9 retains that
science and adds manifest-bound, independently verified noncausal explanation evidence.
V10 binds input schema 4, keeps the V5 opportunity/attention evidence, and replaces the
response layer with the dual-state `spatial-response-v2` stream, final rule and cognition
states, and version-2 cognition artifacts.
For both packaged and workspace-selected runs, canonical `inputs/city.json` is the
authoritative frozen city. Replay, metrics, reports, and run inspection read and verify the
artifact and remain independent of later registry removal, replacement, or corruption.
`city-run` publishes the manifest last,
`city-replay` regenerates every minute and each applicable evidence layer before reporting
equality, and `city-view`
validates before serving one immutable mobility projection. For V4 through V10, the same
loopback-only read-only adapter serves a bounded summary and canonical, paged
current-minute opportunity evidence; V5, V6, V7, V8, V9, and V10 additionally serve
current-minute attention evidence. V6, V7, V8, V9, and V10 add bounded current-minute response
records plus final end-of-run campaign state, explicitly not state at the scrubbed minute;
V10 serves its rule and cognition states side by side. `city-view` also
presents the verified
full-run `/api/spatial-response-metrics` projection as separate direct-rule and committed-state
receipts. It does not create or mutate evidence.
V4 remains opportunity-only evidence, V5 adds bounded synthetic attention evidence, V6
adds bounded rules-only response evidence, V7/V8 freeze workbench provenance, V9 freezes
noncausal explanations, and V10 freezes bounded cognition signals that change only a
separate cognition state; none is an
observed advertising-outcome run.

The adapter-free core exposes the original read-only spatial-metrics projection over
verified V5, V6, V7, V8, V9, and V10 artifacts. That C4a projection remains attention-only:
it folds opportunity and attention evidence, not response or final-state evidence. Each
value has an exact numerator, denominator, and source paths and carries
`synthetic-metrics-not-observed-outcomes`. The matched comparison requires identical city,
assignment, trace, seed, duration, and population provenance, guarantees exact-zero A/A
deltas, and labels changed normalized placement/channel structure
`opportunity-confounded`. It carries
`synthetic-comparison-not-causal-or-observed-effect`.

Schema V6, V7, V8, and V9 have a separate additive `spatial-response-metrics-v1` projection under
`synthetic-response-metrics-not-observed-outcomes`. Its event receipts summarize response
count, reach, frequency, and mean planned rule deltas overall, by channel, and by campaign.
Its complete-grid state receipts summarize initial, final, and changed brand sentiment,
recall strength, and purchase-intention proxy overall and by campaign. Channel state is
intentionally absent: notices from different channels can share one nonlinear committed
state update, so allocating that state to a channel would invent evidence. The response
comparison requires V6, V7, V8 or V9 and reports opportunity and response-assumption classifications
independently. `/api/spatial-metrics` remains the attention-only endpoint;
`/api/spatial-response-metrics` is a separate GET-only, path-free V6/V7/V8/V9 endpoint.

V10 does not widen that V1 contract. `core/experiments/spatial_response_metrics_v2.py`
derives a separate `spatial-response-metrics-v2` document from verified V10 evidence, selected
by the exact manifest class with no trial parsing. Its rule-baseline family folds only the
deterministic baselines embedded in the applied responses and the committed rule states, so it
equals the V1 derivation for the same rule evidence and is the only family with the
purchase-intention proxy. Its provider-dependent cognition family reports applied means,
cognition-state receipts, categories, sources, fallbacks, neutralization, funded coverage,
and provenance under `provider-dependent-exploratory-not-campaign-effect`. The same V2
document backs `/api/spatial-response-metrics` for a V10 viewer. The V1 response comparison
and the paired-study analysis refuse V10 runs, and the attention comparison pairs a V10 run
only with another V10 run.

Bounded C4b evidence is complete without closing the parent C4 gate. The immutable,
path-free study definition names 2–100 committed seed pairs. `city-study` verifies and
projects one run at a time, treats each seed pair as the experimental unit, and produces
`spatial-paired-study-v1` under `synthetic-study-not-observed-or-causal-effect` without
pooling agent or event rows. Independent keyed SplitMix64 streams create deterministic
paired intervals per metric. After `city-report` performs the same source verification and
analysis, its renderer consumes only that revalidated result and publishes a fixed-path,
zero-JavaScript, no-network evidence ledger; the renderer never receives raw events,
response inputs, provider bodies, or source paths. Both analysis and report are read-only.
The guided pages and the local API operate the complete local deterministic slice,
including validation, cooperative cancellation, and verified run-library reopening; a saved
run's timeline and evidence open in `adlife city-view`. Broader spatial social/purchase
mechanisms, OAuth, browser report download/comparison operation,
authentication, rights-reviewed real-city data, calibration, and external validity remain
open. It is still a single-operator loopback application rather than an authenticated or
process-recoverable service.
The guided view at `/guide` adds only a browser report of one saved schema-v10 run, rendered
on request with a self-contained download and never written to the workspace; it is not a
comparison or study report.

## The city explanation seam

Schema-v9 adds an optional, noncausal explanation pass after the city rules model has
computed and committed its complete response evidence. Selection is deterministic and
bounded; rules and mock modes stay offline, while local and remote modes resolve one exact
immutable provider-profile revision. Provider output cannot alter movement, exposure,
attention, a response record, campaign state, budget, purchase probability, metrics, event
identity, or persona identity. It can only supply validated prose explaining the already
computed evidence. Invalid or unavailable output produces a typed deterministic fallback.

The transport is a narrow OpenAI-compatible Chat Completions subset: one bounded,
non-streaming HTTP/1.1 `POST` to the startup-approved `/chat/completions` target with a
strict JSON response schema. Endpoint policy re-resolves and pins numeric peers for each
attempt, preserves the approved Host/TLS identity, refuses URL credentials and remote HTTP,
bounds response bytes, time, retries, repair, concurrency, and output tokens, and screens
provider-controlled text before it crosses the adapter port.

Remote credentials are revision-bound and operation-local. The service name
`adlife.city-provider.v1` identifies the approved OS keyring entry; alternatively, startup
policy may bind a provider to an environment-variable name. A credential value is never a
profile field and never enters a prompt, artifact, browser store, URL, log, report, error,
or response DTO. Only the dedicated credential route accepts it, and that route returns
constant value-free errors. The workbench advertises `oauth: false`: a ChatGPT/Codex product
login or subscription is not an API credential.

The schema-v9 manifest binds `inputs/cognition.json`, `outputs/cognition.jsonl`, and
`outputs/cognition-summary.json`, plus the exact profile revision, prompt/result/selection/
fallback contracts, budgets, record counts, byte counts, and hashes. Loading independently
regenerates the deterministic evidence and verifies every cross-reference. A live provider
answer is nondeterministic; frozen schema-v9 replay is deterministic and performs no
provider call, keyring lookup, environment lookup, cache read, or network request.

## The bounded city cognition seam (schema-v10)

Workbench schema 4 with the `bounded-signals-v1` operation makes provider inference part of the
modeled cognition path without granting it numeric, state, event, or identity authority. A
source selects closed synthetic appraisal categories for a noticed response; AdLife maps them
through a frozen policy into a separate cognition state before the response group commits.
The data flow of one run is:

```text
frozen schema-4 inputs/workbench.json (city, scenario, personas, settings, cognition)
  -> rules: mobility -> opportunities -> attention -> deterministic baseline proposals
  -> canonical (minute, agent, campaign, response) order, then the run's stored policy:
     city-cognition-selection-v3 (new runs) spreads the AI limit evenly over the whole run
     with no per-person cap; city-cognition-selection-v2 (runs saved earlier) took the
     earliest, at most 4 per agent and 60 per run; every other eligible response is
     `unselected`
  -> content-addressed requests and output reservations, all built before provider work
  -> one terminal record per selected request:
       rules (neutral) | mock (deterministic) | provider | cache | fallback | budget-skipped
  -> uncertain category or safety flag -> the whole applied vector becomes neutral
  -> city-cognition-influence-v1 tables and clamps -> applied sentiment/recall deltas
  -> one atomic dual-state update per (minute, agent, campaign):
       rule state <- baseline deltas    cognition state <- applied deltas
  -> manifest-V10 publication last -> independent load verification -> guided story/report
     and the `adlife city-view` inspector
```

The core owns every identifier. `core/simulation/city_cognition_v2.py` builds the selected
requests, reservations, rules/mock/fallback results, terminal records, and the frozen
mapping; `core/simulation/spatial_response_v2.py` commits the dual-state stream; and
`core/domain/city_cognition_v2.py` and `core/domain/city_run.py` hold the strict V2 contracts
and the V10 manifest. One frozen table, `CITY_COGNITION_SELECTION_LIMITS`, holds each
selection-policy version's caps, funded choices, concurrency, deadline, retry ceilings, and
size limits; every rebuild and verification reads the row of the run's stored version, so a
run saved under V2 keeps its V2 limits. None imports a provider, HTTP client, cache,
credential store, or framework. Provider transport stays in `adapters/cognition`
(`city_openai_compatible_v2.py`, `city_prompts_v2.py`, `city_service_v2.py`,
`city_cache_v2.py`), and the workbench worker drives the phases `planning-cognition`,
`requesting-cognition`, and `applying-cognition` before the shared persist and verify steps.

Each request binds a canonical causal identity: the response-input, scenario, and city
hashes, the baseline proposal and its bindings, the channel and time, the pre-minute rule
state, and a minimized causal profile of interests, response traits, and initial sentiment
and recall. Name, age, occupation, background, MBTI, and purchase intention are excluded.
Because every request and reservation exists before provider work starts and no request reads
an earlier provider answer, completion order cannot change prompts, identifiers, ordering,
floating-point folding, or artifacts. The adapter sends only a minimized wire projection with
campaign creative delimited as untrusted data, keeps every schema-v9 transport control
(approved origins, loopback-only plain HTTP, HTTPS remotes, pinned peers, no redirects,
`trust_env=False`, no streaming or tools, bounded bodies, time, retries, and repair), and
accepts a result only when the strict `city-cognition-signal-result-v2` schema holds, the
request ID is echoed exactly, no key is duplicated, and `finish_reason` is `stop`, or `length`
with text that is still a complete valid result; an answer the output cap cut short is a
repairable `invalid-json` result. A vLLM server's own metadata members are admitted only as
null or their observed type and are never parsed or kept, and a profile revision that turns
model thinking off adds `chat_template_kwargs` `{"enable_thinking": false}` to each request.
Each funded request reserves 1,536 output tokens: at most 6 HTTP attempts of up to 256 output
tokens each (1,024 tokens and 4 attempts in runs saved earlier). The service starts requests in
an interleaved order across the run and retries a failed attempt after an exponential backoff
with deterministic jitter, or a `Retry-After` hint of up to 30 seconds, never past the
aggregate deadline; results are still written in request order. Expected provider failures
become typed neutral fallback records; cache corruption, cancellation, storage failure, or a
verification mismatch fails the run instead.

V10 freezes `inputs/workbench.json` (schema 4), `inputs/cognition.json`,
`outputs/cognition.jsonl`, `outputs/cognition-summary.json`, the dual-state
`outputs/spatial-responses.jsonl`, `outputs/response-state.json`, and
`outputs/response-summary.json`, then publishes `run.json` last. Loading and `city-replay`
regenerate the deterministic layers and request identities, validate every frozen result
against its exact request and policies, reapply the mapping, and require exact equality,
without a provider, cache, keyring, environment variable, profile registry, or network
request. The guided setup page reads its limits from `GET /api/workbench`
`capabilities.bounded_cognition`; the `adlife city-view` saved-run page loads the frozen
`window.AdLifeBoundedCognition` helper from `app-cognition-v2.js`, which re-verifies hashes
and mapping arithmetic and renders provider values only as inert text.

## The zone-simulation cognition seam

Cognition — anything a language model could answer — reaches the core only through the
`CognitionPort` protocol and the `CognitionService` that wraps providers with budget,
retry, cache, and a terminal rule fallback. Four modes ship:

| Mode | Behaviour |
| --- | --- |
| `rules` | Every answer resolves through the documented rule formula. No network. |
| `mock` | Deterministic fixture answers keyed by request hash. No network. |
| `hybrid` | A real OpenAI-compatible provider answers bounded channels; failures fall back to rules and are recorded as `cognition.fallback` events. |
| `replay` | Answers come from the recorded cache; re-execution must reproduce the run. |

Language-model cognition never supplies purchase probability directly. The bounded
`rule_modifier` changes rule sentiment and recall deltas; `relevance` sets advertising
memory salience and `share_probability` feeds social sharing. Provider `valence` and
`credibility` are recorded, but not directly consumed by current numeric state updates;
social valence comes from the sender's pre-response sentiment. Narrative reason text
and provider-reported sentiment/recall deltas do not enter the numeric composition.
Retained memory summaries may be context for later remote cognition.
The current response keeps its rule-derived intention, while later rule calculations may
reflect earlier provider-influenced state. Engine code owns movement, event creation,
identities, time, and budget changes. Provider failures fall back with visible provenance;
fresh hybrid calls can vary and require recorded cognition for exact reproduction.

## Domain contracts

Domain boundaries use frozen, strictly validated models: exact integer `schema_version`
values, bounded numeric fields, rejected non-finite numbers, and revalidation on copy.
Persona screens reject recognized national identifiers, phone numbers, email addresses
and secret patterns; they do not prove fictional identity or detect every secret.
Core entry points revalidate inputs rather than trusting low-level construction bypasses.

## Determinism

There is no global random generator. Every stochastic decision is drawn from a keyed
random oracle keyed by root seed, namespace, agent/pair identifier, tick and decision
index; run ID is not a key component. Tick decisions use absolute simulated minutes.
Draws do not consume shared generator state. With a clock that advances in fixed ticks of
15 simulated minutes (96 ticks per simulated day), the same seed
and frozen scenario/cognition answers produce the same events — which replay verifies
rather than assumes (the derived replay run carries its own identifier; every other byte
must match). Fresh remote calls are not guaranteed to reproduce those answers. The test
suite is additionally run under varying `PYTHONHASHSEED` values.

## Tick scheduling and commit boundaries

The runner and engine process each tick of 15 simulated minutes in a fixed order
(96 ticks per simulated day; a timestamp is an absolute simulated minute):

1. **Movement** — two-phase planning and resolution along world routes.
2. **Advertising eligibility and attention** — the zone engine plans its existing
   exposure/notice events. The separate city C3b evaluator is artifact evidence only and
   is not injected into this tick pipeline.
3. **Cognition resolution** — every planned request must have its resolved answer before
   the engine commits the tick.
4. **Response and memory** — the rule-bounded state change with its episodic memory.
5. **Social propagation** — word-of-mouth planning and application over relationship
   edges.
6. **Purchase proxy** — the rule-only purchase decision.
7. **Daily reflection** — on a day boundary.
8. **Completion event** — `run.completed` on the last tick, within the engine commit.
9. **Persistence** — the runner appends committed events to the authoritative store,
   publishes to sinks, advances the clock, then saves a day-boundary checkpoint before
   invoking the tick observer.

The engine constructs and validates the whole in-memory tick before changing agent
states, event sequence or social accumulators. A refusal inside that commit leaves them
unchanged. Subsequent store, sink and checkpoint operations are separate failure
boundaries, not one distributed transaction: failure is reported, but does not roll back
the already committed model or a durable event tail. The stage arithmetic is pinned by
unit suites; whole runs are pinned by integration and golden suites.

## Campaign runs, artifacts, and observability

A campaign run is a whole scenario driven through the ports by `SimulationRunner`, which owns
which ticks happen, what must persist before anyone observes it, and what is recorded
when a run cannot continue. The artifact layout per run:

```
runs/<run-id>/
├── events.jsonl          # the full ordered, causally linked event stream
├── results.sqlite3       # the same run in the SQLite store
├── run.json              # manifest: version, scenario hash, provider, seed, parameters
├── metrics.json          # per-run counters
├── provider-usage.json   # cognition requests, cache hits, failures, fallbacks
└── inputs/               # the frozen scenario the run was driven with
```

Events carry stable identifiers and causal links where applicable. Metrics identify
their event and/or state sources. The stream is auditable, but is not a complete
event-sourced state encoding: state-update events name changed fields, not their values.
Final state comes from stored results/checkpoints or re-execution with frozen inputs and
required cognition answers. The live TUI is a **read-only adapter**: it subscribes to committed ticks through
a `tick_observer` hook on the runner — the same drive loop headless runs use — so a
run driven under the dashboard is event-for-event identical to a headless run of the
same scenario and seed.

## Experiments

The experiments package folds events into metrics (every metric carries its numerator,
denominator, value, and sources — provenance re-derivable from the artifact), runs paired
comparisons under common random numbers, and executes sensitivity sweeps that perturb one
`ModelParameters` knob at a time, watching for campaign-rank flips. Run-level parameters
are run inputs, not baked-in module numbers, and every arm's manifest records the exact
parameter set it ran with.

## Reports

`adlife/reporting` renders the self-contained HTML report: a pure data layer feeds off the
metrics fold and the stored artifact, a Jinja2 template (autoescape on) lays out eleven
sections beginning with a synthetic-data disclosure, and one inlined Plotly runtime powers
the charts with no remote requests. Chart payloads are escaped so no data value can close
its own script tag.

## Persona compilation and version binding

The only workbench input/manifest pairs are (1,7), (2,8), (3,9), and (4,10): schema-1
homogeneous cohort maps to schema-v7, schema-2 fictional population maps to schema-v8,
schema-3 adds exact explanation settings and maps to schema-v9, and schema 4 adds the
bounded-cognition effect scope, source, profile revision, and funded response limit and maps
to schema-v10. Future versions, booleans, floats, and other pairs are refused. The
application layer
materializes canonical persona-ID order into contiguous person-NNN assignments and compiles
only explicit interests, six response traits and initial campaign state into SpatialResponseInput.
Name, age, occupation, background and MBTI are noncausal descriptive metadata, excluded from
scientific response inputs and mobility. IDs/counts/assignments are structural provenance.

V8 load independently cross-binds every assignment/profile/state as well as canonical
inputs/workbench.json and its hash. Frozen persona records make replay independent of the
current template catalog. Schema-v8/schema-v9 consumers explicitly include metrics,
comparisons, native paired studies and viewers; unknown future schemas remain refused. The
saved-run inspector uses verified assignments to show fictional names beside immutable person
IDs and collapses noncausal biography/MBTI; every run-scoped read verifies afresh, as above.
