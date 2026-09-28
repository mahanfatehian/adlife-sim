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
uses directed road paths. The FastAPI adapter loads one pack at startup and exposes
read-only metadata, geometry, fictional agents and minute-addressable frames; the
vanilla browser client only renders those frames. It binds to loopback, has no HTTP
input for filesystem paths or secrets, and makes no tile/API calls.

This boundary is deliberate: animating old abstract zone changes on a city map would
misrepresent the science. Saved **mobility-only** runs now use a separate strict
`CityRunManifest`, a core full-minute trace digest, and a no-clobber `CityRunStore`.
`city-run` freezes the pack and generated assignments under `city-runs/<run-id>/`,
`city-replay` regenerates every minute before reporting equality, and `city-view`
validates before serving one immutable run. This is not an event-sourced advertising
run; geographic campaign encounters remain a separate future model.

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
2. **Advertising eligibility and attention** (planned) — exposure opportunities render
   impressions; the attention policy decides noticed/ignored.
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
