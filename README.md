# AdLife Lab

**A local-first synthetic consumer society for studying advertising exposure, memory, and word of mouth.**

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0--only-green)](LICENSE)
[![Typing: strict](https://img.shields.io/badge/mypy-strict-brightgreen)](https://mypy.readthedocs.io/)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-orange)](https://docs.astral.sh/ruff/)

AdLife Lab simulates a small fictional society of 1–30 synthetic consumers over a period of up to seven
simulated days. Each agent follows a daily routine, moves between zones and along routes, maintains
memories and relationships, encounters advertising through a mobile feed or a highway billboard, discusses
what it saw with people it knows, and updates its recall, brand sentiment, and purchase intention
accordingly. The engine advances in fixed 15-simulated-minute ticks — 96 ticks per simulated day; a
timestamp is an absolute simulated minute, so day 1 ends at minute 1,440.

It is a research and teaching instrument: a controlled environment where you can change one variable in a
campaign, re-run an identical population under an identical seed, and attribute the difference in outcome
to that change alone.

---

## Scientific scope

> **Every agent in this simulator is fictional and generated from templates. No real person's data is
> used, collected, or modelled.** Results are synthetic and exploratory: they describe behaviour *inside
> a synthetic model* and are not a representative survey of, nor a statistically representative
> prediction about, any real population, market, or country.

This constraint is enforced in code, not merely documented. Persona fields reject national identifiers,
phone numbers, email addresses, and free-form secrets; provider prompts are minimised and carry no
filesystem paths; and every generated report renders a synthetic-data disclosure.

The simulator does not make purchasing or targeting decisions for real people, and it does not connect to
any advertising network.

---

## Design principles

**Determinism is a hard requirement.** With the same seed, frozen inputs, model parameters,
code and request identities, rules/mock execution produces the same normalized events
and final state. Every stochastic decision is drawn from a keyed random oracle rather than
a global generator, so a change in evaluation order cannot change an outcome. The test suite is run under
varying `PYTHONHASHSEED` values to catch iteration-order leaks.

**Campaign runs record an auditable event stream.** The advertising simulation emits an ordered, causally linked stream of
immutable events with stable identifiers. Reports derive their numbers from persisted events,
boundary-state checkpoints and provider-usage records, with explicit metric provenance.
A completed run can be replayed; live-provider replay requires its validated cognition cache.

**The core is free of adapters.** `adlife.core` contains the world, agent, campaign, and behaviour model
and imports no CLI framework, no terminal UI, no database driver, no plotting library, and no model
provider. Interfaces to the outside world are defined as ports and implemented separately, which keeps the
model reusable and independently testable.

**Offline by default.** Rule mode and mock-provider mode require no network access, no API key, and no
external service. Language-model cognition is optional, bounded, and never supplies purchase probability —
that stays rule-derived so results remain reproducible and explainable.

**Contracts are strict and immutable.** Domain models are frozen, strictly validated, reject non-finite
numbers, and revalidate on copy through the supported model API.

---

## Status

AdLife Lab is a local research instrument. The table distinguishes implemented repository
capabilities from external publication; it does not claim a public package or release exists.
Nothing here makes decisions for real people or is calibrated against real-world data.

| Capability | Status |
| --- | --- |
| Typed package, configuration, and safe project loading | Complete |
| Versioned, immutable domain contracts | Complete |
| Deterministic clock, keyed random oracle, stable event identifiers, fingerprints | Complete |
| Fictional population, daily routines, and relationship graph generation | Complete |
| World routes and two-phase movement planning | Complete |
| Campaign ingestion, exposure opportunity, impression, and attention | Complete |
| Response dynamics, memory decay, social influence, and purchase proxy | Complete |
| Cognition ports, mock provider, rules provider, and replay | Complete |
| OpenAI-compatible provider with bounded fallback | Complete |
| Event sinks, SQLite storage, and replayable run artifacts | Complete |
| Full run orchestration | Complete |
| Metrics and paired experiments | Complete |
| Complete command-line interface | Complete |
| Live terminal user interface | Complete |
| Self-contained HTML reports | Complete |
| Opt-in deterministic street-mobility pilot, city-pack v2, saved/replayable spatial evidence, local web timeline, and offline OSM extract importer | Initial product-track pilot; separate from the zone campaign engine |
| Schema-v6 spatial rule responses and campaign-scoped state over persisted notices | C3c evidence complete; uncalibrated and not a purchase or sales model |
| Verified offline, content-addressed city catalog with one `fictional-fixture` entry | Foundation complete; no real city is qualified |
| Rights-reviewed real-city entries, calibrated geographic campaign effects, authenticated provider settings | Not implemented |
| Wheel build and release smoke test (`uv build`, `scripts/smoke_release.py`) | Complete |
| Frozen-build configuration, installers, CI and release workflows | Implemented; platform execution requires verification |
| Package-index publication (PyPI) and public tagged releases | Not performed by this audit |

---

## Requirements

- Python 3.11, 3.12, or 3.13 (3.12 is the development baseline)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management

## Installation

<!-- adlife-install:start -->
uv tool install adlife-sim
<!-- adlife-install:end -->

Until the package-index release exists (see the roadmap), install from source:

```bash
git clone https://github.com/mahanfatehian/adlife-sim.git
cd adlife-sim
uv sync
```

Verify the installation:

```bash
uv run adlife --version
```

## Usage

The fastest look is the offline demonstration — it builds a synthetic study, runs it on the mock
provider, and needs no network, no API key, and no configuration:

```bash
uv run adlife demo
```

A full walkthrough, from an empty directory to a self-contained HTML report, is in
[docs/quickstart.md](docs/quickstart.md). The shape of a study:

```bash
uv run adlife init my-study              # create a study from the packaged demo project
uv run adlife validate my-study          # gate: every input checks before anything runs
uv run adlife run my-study --seed 42     # one deterministic whole run, fully persisted
uv run adlife replay my-study <run-id>   # re-execute and verify the stream, event for event
uv run adlife report my-study <run-id>   # self-contained HTML report (works offline)
uv run adlife compare control treatment  # paired A/B across seeds (common random numbers)
uv run adlife run my-study --live        # the four-panel terminal dashboard
uv run adlife doctor --offline           # installation readiness, zero network
uv run adlife city                       # local street-mobility pilot at 127.0.0.1:8765
uv run adlife city-catalog list          # packaged catalog; currently fictional only
uv run adlife city --city-id fictional-grid-v2
uv run adlife city-import streets.json --output city.json --city-id my-city --name "My City"
uv run adlife city-run --city-id fictional-grid-v2 --output-root ./city-output --run-id study-42 --agents 20 --days 3
uv run adlife city-replay ./city-output study-42
uv run adlife city-view ./city-output study-42
```

Campaign and population documents are authored in YAML and validated before use. They are treated as
untrusted input: YAML is parsed with a safe loader, and any referenced asset path is resolved and
confirmed to lie beneath the project root before it is opened. Every command honors the global
`--format human|json|jsonl` output contract for data commands; the interactive `city`
server accepts human mode only. See
[docs/cli-reference.md](docs/cli-reference.md) for the complete surface.

### City mobility pilot

`uv run adlife city` opens a loopback-only FastAPI viewer with a **fictional** offline
grid. It has a day/night timeline, minute-level road-constrained positions, agent
activities, and visible source attribution. Use `--pack CITY.json` to load a validated,
locally supplied street graph. City-pack v2 preserves road geometry, explicit traversal
direction, source provenance, exact bounds, an IANA time zone, and known omissions.
`adlife city-import` converts a complete local Overpass JSON extract into a validated
pack without network access; importing does not establish license or redistribution
rights.

`adlife city-catalog list` exposes a packaged, offline, content-addressed catalog. Its
only shipped entry is `fictional-grid-v2`, explicitly classified as
`fictional-fixture`; selection verifies the immutable pack hash and metadata before use.
Use `--city-id fictional-grid-v2` with `city` or `city-run`. No command contacts a public
tile server or geocoder, and a locally imported real-world pack is not automatically
catalog-qualified. The
[city-source qualification checklist](docs/data/city-source-qualification.md) defines
the human rights review required before a real-world entry may ship.
An optional schema-v1 place-set JSON can bind fictional home, workplace and leisure
points to exact road nodes and to the selected pack hash. Validate it before use, then
pass the same file to either the ephemeral viewer or a saved run:

```bash
uv run adlife city-places validate places.json --city-id fictional-grid-v2 --agents 20 --days 3 --seed 42
uv run adlife city --city-id fictional-grid-v2 --places places.json --agents 20 --days 3 --seed 42
uv run adlife city-run --city-id fictional-grid-v2 --places places.json --output-root ./city-output --run-id study-42 --agents 20 --days 3 --seed 42
```

Place-aware saved runs use manifest v3 and freeze both the canonical place set and its
keyed per-agent assignments. The viewer shows the selected fictional labels and declared
provenance; it does not turn them into real addresses or observed visits.

Spatial campaign inputs have a separate strict validation boundary:

```bash
uv run adlife city-campaign validate spatial-campaign.json --city-id fictional-grid-v2
uv run adlife city-run --city-id fictional-grid-v2 --places places.json \
  --spatial-campaign spatial-campaign.json --output-root ./city-output \
  --run-id spatial-42 --agents 20 --days 3 --seed 42
uv run adlife city-run --city-id fictional-grid-v2 --places places.json \
  --spatial-campaign spatial-campaign.json --spatial-response spatial-response.json \
  --output-root ./city-output --run-id response-42 --agents 20 --days 3 --seed 42
uv run adlife city-replay ./city-output spatial-42
uv run adlife city-replay ./city-output response-42
uv run adlife city-metrics ./city-output spatial-42
uv run adlife city-compare ./city-output spatial-42 spatial-42
```

The schema binds a scenario to the exact city hash, validates creative hashes, active
windows and caps, and proves each billboard coordinate matches a stable road fraction and
supported direction without snapping. Phone placements declare an explicit synthetic
opportunity policy. The adapter-free C2 core can now evaluate deterministic typed
opportunities from that scenario plus immutable mobility: directional billboard passages
use continuous crossing time and a disclosed facing heuristic, while phone opportunities
use activity-filtered keyed draws. It reports every pre/post-filter denominator and labels
each record `synthetic-opportunity-not-impression`.

The validation command alone writes no run artifact. Passing the validated document to
`city-run --spatial-campaign` creates a schema-v5 artifact that freezes the scenario,
canonical opportunity stream and summary, and a separate deterministic attention stream
and summary. The additional files are `outputs/spatial-attention.jsonl` and
`outputs/attention-summary.json`; their hashes, byte counts and record counts are bound by
the manifest. Every opportunity creates one synthetic impression, then an
order-independent keyed draw records noticed when it is below the fixed neutral probability
0.5. That value is an uncalibrated research assumption, not observed attention, and every
attention record says `synthetic-attention-not-observed-behavior`.

`--spatial-response` requires `--spatial-campaign`. A spatial campaign without a response
input remains schema-v5 opportunity/attention evidence. Supplying both produces a schema-v6
run under `illustrative-road-spatial-response-study-v1`: only persisted noticed records enter
the deterministic `spatial-response-v1` evaluator. The input, canonical response/state
stream, complete final campaign-scoped state, and summary are manifest-bound as
`inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`. Every `spatial.response`
and `spatial.state-updated` stream record carries the claim scope
`synthetic-response-not-observed-behavior`; the final-state document carries that claim once
at top level instead of repeating it on each state entry. Purchase intention is a bounded,
uncalibrated state proxy, not purchase probability, a sale, or a transaction; this slice
adds no cognition, memory, social propagation, budget mutation, purchase event, or movement
change.

`city-replay` recomputes and verifies every layer without changing the source. For verified
schema-v5 and schema-v6 runs, `city-view` shows read-only current-minute opportunity and
current-minute attention evidence on the map and in bounded evidence rails; schema-v6 also
shows response records and complete end-of-run campaign state. The viewer's spatial metrics
ledger remains attention-only for both schema versions: every metric is derived solely from
opportunity and attention evidence and carries its exact numerator, denominator, and source
paths under `synthetic-metrics-not-observed-outcomes`. No metric is calibrated to observed
behavior. Campaign copy, identity, creative content and provider configuration are not inputs
to the attention draw. In the response model, campaign ID routes each notice to its campaign
assumptions and campaign-scoped state. Campaign name, copy, creative hash and provider
configuration do not change numeric response values when the frozen response assumptions
remain fixed.

`city-metrics` prints that same deterministic attention-only, receipt-bearing projection
without writing a cache or changing the schema-v5 or schema-v6 run. `city-compare` accepts
only matched city, assignment, trace,
seed, duration, and population provenance. A same-run A/A comparison is exactly zero. When
the normalized placement/channel opportunity structure differs, the result is explicitly
`opportunity-confounded`, not a channel effect. Every comparison carries
`synthetic-comparison-not-causal-or-observed-effect`. Older schema-v4 artifacts remain
readable as opportunity-only evidence and do not fabricate attention or response state. C3
remains open for bounded cognition, memory, social propagation, and separately specified
rule-owned purchase semantics; C4b repeated-seed uncertainty and self-contained spatial
reporting also remain open. Nothing here claims observed device use, viewability, attention,
traffic, people, purchases, sales, or causal effects.
Use `--largest-component` only if dropping disconnected road segments is acceptable.
Use `--agents`, `--days`, `--seed`, and `--port` to change the preview. No map tile server,
provider key, or network connection is needed. The server does not open a browser or
terminal window for you.

`city-run` always freezes a bounded mobility trace with all-minute integrity evidence;
schema-v5 spatial runs additionally persist opportunity and attention evidence, and
schema-v6 runs add the bounded response/state layer above. `city-replay` verifies the
selected contract without changing source artifacts, and `city-view` opens its verified
mobility projection plus persisted evidence in the same read-only local timeline.

This remains an early geographic research slice, not a calibrated advertising outcome
model: its synthetic impression, fixed-0.5 notice label, response score, and purchase
intention proxy are model evidence, not measured behavior. It does not simulate calibrated
residents or traffic or predict purchases or sales. A real street network improves spatial
fidelity but does not validate agent behavior.
City-pack format and scientific boundaries are in [docs/city-pilot.md](docs/city-pilot.md).

---

## Project layout

```
src/adlife/
├── core/              # Simulation model — no adapters, no I/O frameworks
│   ├── domain/        # Frozen, strictly validated contracts
│   ├── simulation/    # Clock, random oracle, movement, exposure, response, memory, social
│   ├── experiments/   # Metrics fold, paired comparisons, sensitivity sweeps
│   └── ports/         # Interfaces the outside world implements
├── adapters/          # Concrete implementations of the ports (storage, cognition)
├── config/            # Strict settings, safe loading, path containment
├── cli/               # Command-line surface and output contract
├── tui/               # Live terminal dashboard (read-only adapter over the core)
├── reporting/         # Self-contained HTML report rendering
├── city/              # Local FastAPI city pilot, pack loader, vanilla web assets
└── resources/         # Packaged data (fictional name and routine templates, demo project)

tests/
├── contract/          # Contract and interface guarantees
├── unit/              # Focused behavioural tests
├── property/          # Invariants swept across many seeds
├── golden/            # Fingerprint and reproducibility checks
└── packaging/         # Packaged-resource checks
```

---

## Development

```bash
uv sync                              # install dependencies
uv run pytest -q                     # run the test suite
uv run pytest --cov=adlife           # run with coverage (85% floor, enforced)
uv run ruff check .                  # lint
uv run ruff format --check src tests # formatting
uv run mypy src                      # strict type checking
```

All of the above are enforced gates. Contributions are expected to be test-driven: a behavioural change
lands with a test that fails before the change and passes after it.

To check for iteration-order dependence:

```bash
PYTHONHASHSEED=0 uv run pytest -q
PYTHONHASHSEED=12345 uv run pytest -q
```

---

## Documentation

| Document | Contents |
| --- | --- |
| [docs/quickstart.md](docs/quickstart.md) | Zero to HTML report in a handful of commands |
| [docs/installation.md](docs/installation.md) | Every installation path, frozen binaries, verification |
| [docs/cli-reference.md](docs/cli-reference.md) | Every command, flag, exit code, and the output contract |
| [docs/architecture.md](docs/architecture.md) | Layers, the cognition seam, auditable events, the tick, artifacts |
| [docs/city-pilot.md](docs/city-pilot.md) | Street-pack format, city viewer, and mobility model boundaries |
| [docs/data/city-source-qualification.md](docs/data/city-source-qualification.md) | Human rights and technical gate for any future real-city catalog entry |
| [Production city platform design](docs/superpowers/specs/2026-09-28-production-city-platform-design.md) | Target architecture, scientific/data-rights boundaries, and release profiles — proposed, not shipped |
| [Production implementation roadmap](docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md) | Sequenced work packages, acceptance tests, and agent handoff through production readiness |
| [docs/reproducibility.md](docs/reproducibility.md) | What a seed guarantees and how to reproduce any run |
| [docs/defense-readiness.md](docs/defense-readiness.md) | Audited defense contracts, regressions, and verification evidence |
| [docs/methodology/odd-protocol.md](docs/methodology/odd-protocol.md) | The ODD protocol: entities, scales, scheduling, submodels, formulas |
| [docs/methodology/model-card.md](docs/methodology/model-card.md) | Intended and excluded use, risks, validity status |
| [docs/methodology/experiment-protocol.md](docs/methodology/experiment-protocol.md) | The pre-registered comparison and sensitivity protocol |
| [docs/methodology/limitations.md](docs/methodology/limitations.md) | What the model cannot support |
| [docs/investor-demo.md](docs/investor-demo.md) | A five-minute demonstration script |

---

## Research method

AdLife Lab is an agent-based model documented under the ODD (Overview, Design concepts, Details)
protocol — see [docs/methodology/odd-protocol.md](docs/methodology/odd-protocol.md). Campaign effects
are studied with paired comparisons under common random numbers: two arms share seed, initial
population, world, and keyed random streams, so paired differences describe the declared treatment
inside this model, not causal effects in real populations. An A/A control (the same scenario twice)
must return exactly zero before any treatment
result is read, and a no-campaign control anchors what the campaigns themselves contribute. The full
pre-registration, including the 80% directional-stability rule and sensitivity ranges, lives in
[docs/methodology/experiment-protocol.md](docs/methodology/experiment-protocol.md).

---

## Reproducibility

With the same frozen inputs, parameters and original request identities, rules/mock
execution is reproducible event for event. Every stochastic decision is drawn
from a keyed random oracle scoped to its agent and purpose, so a change in evaluation order cannot
change an outcome. Every campaign run persists a manifest recording the code version, scenario fingerprint,
provider, and seed, and `adlife replay` re-executes a stored run and verifies the event stream matches
the recorded one. Live API responses are not inherently reproducible: replay requires the
recorded validated cognition. Wall-clock metadata is not part of event equality.
The recipe is in [docs/reproducibility.md](docs/reproducibility.md).

---

## Limitations

Results are synthetic and exploratory. The society is small (1–30 agents) over short horizons (1–7
simulated days) with two advertising channels; purchase is a rule-derived proxy, not a transaction
model; nothing is calibrated against real-world data.
Standard CLI/demo initial states have no active purchase need and zero disposable budget,
so their purchase count is structurally zero. Purchase-enabled core studies require explicit
initial states. Sentiment, recall and intention are shared per-person state, not campaign-specific.
Read
[docs/methodology/limitations.md](docs/methodology/limitations.md) before citing any number this
software produces.

---

## Roadmap

1. **Package-index release** — publish `adlife-sim` to PyPI so the `uv tool install adlife-sim`
   command above works everywhere; the wheel build and its release smoke test already run locally.
2. **Continuous integration and releases** — automated gates and tagged releases on GitHub-hosted
   runners.
3. **Production-track city product on `main`** — mature the current opt-in pilot with
   licensed city ingestion, geography-aware campaign events, persisted replayable traces,
   authenticated administration and protected provider configuration. The
   [production roadmap](docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md)
   separates these unshipped capabilities into testable milestones and a distinct
   external-validation gate. The `defense-ready` branch retains the research snapshot.

---

## Contributing, security, and citation

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) — fictional data only, tests required, DCO
  sign-off.
- Security: [SECURITY.md](SECURITY.md) — report vulnerabilities privately through GitHub.
- Citation: [CITATION.cff](CITATION.cff) and [CHANGELOG.md](CHANGELOG.md).

---

## Privacy, ethics, and data handling

- Personas are fictional and generated from templates; no real individual is represented.
- Persona inputs screen recognized identifier and secret patterns; screening is best effort and
  cannot prove fictional identity. Users must supply fictional data only.
- Campaign files are untrusted input: safe YAML loading, and asset paths confined to the project root.
- API keys are read only from environment variables or hidden prompts, never from committed files.
- Logs redact authorization headers and likely secret patterns.
- Generated reports escape user-provided text and embed charts without remote scripts.
- Every report carries a synthetic-data disclosure.

---

## License

Released under the **GNU Affero General Public License v3.0 only** (AGPL-3.0-only). See [LICENSE](LICENSE).

The AGPL requires that a modified version operated as a network service also offer its source. As the sole
copyright holder, the author may additionally offer the software under separate commercial terms.
