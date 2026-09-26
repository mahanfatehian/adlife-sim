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

Language-model cognition never supplies purchase probability — that stays rule-derived so
results remain reproducible and explainable. A failed provider never aborts a run; it
falls back, and the fallback is visible in the artifact.

## Domain contracts

Everything crossing a boundary is a frozen, strictly validated model: `schema_version`
literals, bounded numeric fields, rejected non-finite numbers, revalidation on copy.
Persona fields reject national identifiers, phone numbers, email addresses, and free-form
secrets. An invalid state cannot be constructed even by bypassing the intended API.

## Determinism

There is no global random generator. Every stochastic decision is drawn from a keyed
random oracle — a stream keyed by run, agent, and purpose — so evaluation order cannot
change an outcome. Combined with a deterministic clock that advances in fixed ticks of
15 simulated minutes (96 ticks per simulated day), the same seed
and scenario produce the same events, event for event — which the replay command verifies
rather than assumes (the derived replay run carries its own identifier; every other byte
must match). The test suite is additionally run under varying `PYTHONHASHSEED` values.

## The nine-stage tick

The engine commits one tick of 15 simulated minutes through a fixed atomic stage order
(96 ticks per simulated day; a timestamp is an absolute simulated minute):

1. **Movement** — two-phase planning and resolution along world routes.
2. **Advertising eligibility and attention** (planned) — exposure opportunities render
   impressions; the attention policy decides noticed/ignored.
3. **Cognition resolution** — every planned request must have its resolved answer before
   any byte is written.
4. **Response and memory** — the rule-bounded state change with its episodic memory.
5. **Social propagation** — word-of-mouth planning and application over relationship
   edges.
6. **Purchase proxy** — the rule-only purchase decision.
7. **Daily reflection** — on a day boundary.
8. **Checkpoint persistence** — boundary states committed before anyone observes them.
9. **Completion** — `run.completed` on the last tick.

Nothing is applied to an agent or the sequence counter until every stage has minted: a
refusal anywhere leaves the tick entirely uncommitted. The stage arithmetic is pinned by
unit suites; whole runs are pinned by integration and golden suites.

## Runs, artifacts, and observability

A run is a whole scenario driven through the ports by `SimulationRunner`, which owns
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

Every event carries stable identifiers and explicit `caused_by` links, so any reported
number can be traced back to the events that produced it. State is a fold over the
stream. The live TUI is a **read-only adapter**: it subscribes to committed ticks through
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
