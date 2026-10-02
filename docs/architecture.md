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
the normalized stream and summary. This storage boundary is not the remaining C3 causal
bridge: no opportunity reaches cognition, agent state, purchase logic, metrics or reports.

`core/simulation/spatial_attention.py` is the separate C3b evidence boundary. For each
validated opportunity it emits exactly one synthetic impression and uses an
order-independent SHA-256 keyed draw to record notice when the draw is below the fixed 0.5
probability. The probability is deliberately neutral and uncalibrated. Campaign copy,
campaign identity, creative content, channel and provider configuration are excluded from
the draw key, so they cannot control notice. Records carry the literal claim
`synthetic-attention-not-observed-behavior`. The pure evaluator cannot call cognition,
create arbitrary domain events, mutate agent state or budget, change movement, or affect
purchase probability.

The city adapter owns file and package-resource loading. Its packaged catalog is an
offline, content-addressed index: it verifies each v2 resource's canonical SHA-256 and
matching ID/schema/metadata before selection. The only bundled entry is
`fictional-grid-v2` with qualification `fictional-fixture`; no real city is represented
as reviewed. The server binds to loopback, has no HTTP input for filesystem paths or
secrets, and makes no network request. Catalog selection, import, simulation, replay and
viewing do not contact a public tile service or geocoder and do not accept an arbitrary
resource URL.

This boundary is deliberate: animating old abstract zone changes on a city map would
misrepresent the science. Saved city studies use separate strict v1/v2/v3/v4/v5
`CityRunManifest` contracts, a core full-minute trace digest, and a no-clobber
`CityRunStore`; v1-v3 remain mobility-only.
V3 freezes the canonical place set and its generated assignment document alongside the
pack under `city-runs/<run-id>/`; their hashes are part of the final manifest.
V4 additionally freezes the validated spatial scenario, opportunity stream and summary.
V5 adds canonical `outputs/spatial-attention.jsonl` and
`outputs/attention-summary.json`, with their hashes, sizes, counts, fixed model identity,
0.5 probability and claim scope bound into the manifest.
`city-run` publishes the manifest last,
`city-replay` regenerates every minute before reporting equality, and `city-view`
validates before serving one immutable mobility projection. For V4 and V5, the same
loopback-only adapter serves a bounded summary and canonical, paged
current-minute opportunity evidence; V5 additionally serves current-minute attention
evidence. It does not create or mutate evidence. V4 remains opportunity-only evidence,
while V5 adds bounded synthetic attention evidence. Neither is an event-sourced
advertising-outcome run; the remaining C3 response/state bridge and C4 still require
separate contracts and tests.

## The cognition seam

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
