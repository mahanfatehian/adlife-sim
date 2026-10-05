# Production-track city platform: target design

> Status: **partially implemented** target. The local deterministic city foundation and
> bounded C1–C3c/C4a/C4b evidence slices described below exist on `main`; rights-reviewed real
> city qualification, remote authentication/authorization, calibrated outcomes, and
> external validity do not. This document does not rewrite the `defense-ready` branch or
> retroactively change the original CLI specification. Read the
> [execution roadmap](../plans/2026-09-28-production-city-platform-roadmap.md)
> for work packages and gates.

## Product contract

An analyst selects a licensed city, constructs a **synthetic** population and
campaign, runs a controlled simulation, inspects any simulated time and agent on a
map, explains every advertising outcome through events, compares paired scenarios,
and exports a reproducible report. The browser is vanilla HTML/CSS/JavaScript over
FastAPI. The existing CLI and offline demo remain supported. A remotely accessible
installation adds authenticated access and protected provider configuration; the
local offline profile remains usable without accounts, internet, or an LLM.

Real streets do not make simulated agents real residents. Without calibration and
out-of-sample validation, results remain mechanism experiments, not sales forecasts
or spend recommendations. Do not substitute invented traffic, demographic, or
conversion rates for licensed measurements. No personal trajectories, real-person
profiles, ad-network integrations, or live consumer targeting belong in this scope.

## Implemented baseline, updated from code on 2026-10-05

- `src/adlife/core/simulation/runner.py` and `src/adlife/adapters/storage/sqlite_store.py`
  implement the zone-based advertising run/replay path with SQLite authority,
  canonical JSONL export, frozen inputs, and deterministic rules/mock execution.
- `src/adlife/core/domain/city.py` validates bounded version-1 and version-2 directed road
  graphs (10,000 nodes, 20,000 roads, 4 MiB). Version 2 preserves geometry, direction,
  bounds, time zone, source provenance and omissions. `city-run` freezes bounded 1–30-agent,
  1–7-day traces; `city-replay` verifies them from their saved inputs. The wider ephemeral
  viewer remains limited to 250 agents and 31 days. All schedules and speeds are synthetic.
- `src/adlife/city/osm.py` and `adlife city-import` convert a **local** bounded
  Overpass JSON extract into an attributed ODbL pack without network access. They do
  not download or catalog worldwide cities; unsupported turn/access rules are refused.
- The packaged, content-addressed catalog contains only the explicit
  `fictional-grid-v2` fixture. No rights-reviewed real-city entry has shipped.
- `src/adlife/city/web.py` serves a read-only, loopback FastAPI viewer, and
  `src/adlife/city/static/` is vanilla browser code. It paints an illustrative road canvas
  and fixed 06:00/19:00 light/dark styling, **not** local sunrise. It can validate and view a
  saved run but exposes no path/run selector or write endpoint.
- C1/C2 define and evaluate the spatial scenario/opportunity contracts; C3a/C3b persist and
  replay opportunity and fixed-0.5 synthetic attention evidence. C3c schema-v6 adds bounded
  deterministic response/state evidence
  from noticed records under `synthetic-response-not-observed-behavior`. Its purchase
  intention is an uncalibrated proxy, not a sale or transaction. C4a metrics retain their
  read-only opportunity/attention default; schema-v6 adds a separate response projection.
  Bounded C4b evidence is complete: `city-study` analyzes explicit verified seed pairs and
  `city-report` publishes a fixed-path zero-JavaScript evidence ledger. The broader C3 and
  C4 gates remain open for spatial social/purchase mechanisms and other declared metrics;
  job orchestration and the analyst workbench also remain open.
- The city path does not join the existing zone campaign engine, provide authentication or
  protected provider settings, use calibrated traffic/population data, or establish
  external validity. The current model card and limitations govern every claim.

## Architecture and non-negotiable seams

```text
CLI / vanilla browser / TUI / reports
        FastAPI application and authorization boundary
        adapters: city data, run store, provider transport, secrets
        core ports and versioned domain contracts
        deterministic city/campaign simulation and experiment policies
```

`src/adlife/core` stays free of FastAPI, Typer, Textual, SQL drivers, Plotly,
browser libraries, and provider implementations. HTTP controllers validate input and
authorize access; they do not calculate exposure or mutate a run. The browser draws
committed, persisted state and may control **presentation time**, not model time.
No UI endpoint may read an arbitrary filesystem path or accept a provider credential
as a query parameter. The CLI and web application call the same application services
and persist the same versioned run format.

Keep the legacy zone simulator and city pilot operating while the spatial path grows.
Do not display a zone-simulator exposure on a road map as if it happened at a
coordinate. Introduce a versioned geographic scenario and run type, then bridge
shared policies only where semantics are genuinely identical. A schema migration
must be explicit; old artifacts remain readable or fail with a clear version error.

## Geographic data contract

City selection uses a curated catalog of immutable, content-addressed packs with
display name, stable ID, bounds, time zone, source version/date, license, attribution,
coverage and known omissions. One pack version is pinned into each run. Catalog
listing is not a promise that every city on earth is available. A city may be added
only after ingestion, rights, routability and quality checks pass; absence is a
clear refusal, not a silent fallback to fictional geometry.

Scale beyond the v1 pack limit requires a separately versioned format and benchmarked
storage/indexing. Its graph must retain directed connectivity and road geometry;
access, turn restrictions, speed limits and time-dependent rules are modeled only
when the source supports and tests them. Missing data is labeled or refused rather
than treated as a legal navigation route. Map presentation uses a self-hosted or
commercially licensed basemap; public OSMF tile and geocoding services must not be
the production dependency. No Google Maps content is copied into OSM-derived packs
without express rights. Preserve data provenance and visible attribution on map,
export and report. See [OSM copyright](https://www.openstreetmap.org/copyright),
[OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/) and
[Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/).

## Simulation and timeline contract

- All agents are synthetic. Home/work/leisure zones may eventually be selected by
  an operator; generated assignments are seeded and documented. Real addresses or
  movement traces are not ingested as agent data.
- Every run freezes city pack bytes/hash, scenario, population generator and
  assignments, seed, model/parameter versions, campaign creatives, time-zone rules,
  provider identity **without secrets**, and validated cognition required for replay.
- Specify the simulated start date, city IANA time zone, and treatment of daylight
  saving transitions before claiming calendar-accurate day/night or commuting.
  Until then, label the clock as a model clock and light/dark as illustrative.
- Route, activity and advertising events have stable total ordering and causal
  references. Movement and ad decisions read the same immutable pre-tick snapshot;
  an observer only sees durable committed ticks. Replay compares normalized events
  and state/results and refuses disagreement. A saved frame is never fabricated by
  browser interpolation that contradicts core state.
- Preserve offline rules/mock operation. Bounded LLM cognition may influence only
  the documented channels; it cannot choose purchase probability, move an agent,
  create events, mutate budgets, or bypass validation. Live calls vary; recorded
  validated answers are required for exact hybrid replay.

## Geographic advertising contract

Define distinct `opportunity`, `impression`, `noticed`, `response`, and `share`
events. A billboard is associated with a permitted road segment and side/position;
its opportunity is evaluated against the directed traversed segment, time window,
frequency cap and an explicit visibility approximation. A phone placement requires
a modeled phone-use opportunity, not merely proximity to a road. Attribution must
follow persisted event lineage. Do not claim viewability, attention or sales measurements
from these synthetic events. An experiment changing phone to billboard also changes
opportunity structure unless held equal by design; report this confound.

## Web application and identity contract

The first workbench may remain loopback-only and single-operator. Before remote
exposure or shared organizations, introduce a separate identity/authorization
boundary: OIDC login, explicit roles, workspace/run ownership, denial by default,
CSRF-protected writes, secure server-side sessions, audit events, rate and resource
limits, TLS and deployment configuration. Identity login and **LLM provider
configuration** are different concepts and must not be presented as one OAuth
switch. If a model vendor offers OAuth, treat it as a separately reviewed provider
integration. Follow [OAuth security BCP RFC 9700](https://www.rfc-editor.org/rfc/rfc9700)
for authorization-code/PKCE, exact redirects, state/nonce and mix-up defenses.

An operator may select rules, mock, local or an approved OpenAI-compatible endpoint.
The UI may show provider type, model, non-secret URL and health status, but never
echo a stored key. Secrets come from environment/OS keyring/managed secret store,
not scenario files, run manifests, browser local storage, URLs, logs, caches,
reports or error bodies. Remote URL handling must prevent userinfo, HTTP downgrade,
redirect-to-private-network and server-side request forgery; local loopback is an
explicitly separate trust class. Provider usage/cost budgets are bounded and shown.

## Scientific validity and personality

Increasing agent count or adding map detail is not validation. To claim useful
external predictions, define a target quantity, obtain legally usable aggregate
observations, fit parameters on training data, test on held-out cities/periods, measure
uncertainty and subgroup error where appropriate, and publish failures. Keep
structural assumptions, sensitivity results and A/A controls visible. Until such
evidence exists, all outputs say `synthetic and exploratory`.

The requested MBTI attribute can appear only as an optional, disclosed scenario
label unless a separately reviewed empirical study justifies a specific causal
mapping. No default MBTI-based mobility, persuasion, or purchase rule; no claim that
four letters make agents realistic. Prefer observable, validated behavioral inputs
when calibration data actually exists. Never infer personality of real individuals.

## Release profiles and acceptance

1. **Offline research:** current CLI/TUI/report and city viewer work without network,
   credentials or external tiles; existing deterministic and replay gates remain green.
2. **Local geographic study:** a user can load a licensed city, save a spatial run,
   replay it, inspect ads/events on the timeline and compare controlled arms; all
   inputs and provenance travel with the artifact.
3. **Controlled organizational deployment:** users can authenticate, are authorized
   for every run/pack/provider action, secrets are protected, data is backed up and
   restored, incidents are observable, the deployment survives realistic load and
   failures, and legal/data-rights review is recorded.
4. **Externally validated decision support:** only after a predeclared target and
   out-of-sample evaluation support the exact claimed use. This gate is separate
   from engineering production readiness and cannot be passed by more UI code.

The [roadmap](../plans/2026-09-28-production-city-platform-roadmap.md) defines
work packages and their evidence. Do not mark a profile shipped from an unchecked
plan item.
