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
accordingly.

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

**Determinism is a hard requirement.** The same seed and the same scenario produce byte-identical events
and byte-identical final state. Every stochastic decision is drawn from a keyed random oracle rather than
a global generator, so a change in evaluation order cannot change an outcome. The test suite is run under
varying `PYTHONHASHSEED` values to catch iteration-order leaks.

**Runs are event-sourced and auditable.** The simulation emits an ordered, causally linked stream of
immutable events with stable identifiers. Final state is a fold over that stream, so any reported number
can be traced back to the events that produced it, and a completed run can be replayed.

**The core is free of adapters.** `adlife.core` contains the world, agent, campaign, and behaviour model
and imports no CLI framework, no terminal UI, no database driver, no plotting library, and no model
provider. Interfaces to the outside world are defined as ports and implemented separately, which keeps the
model reusable and independently testable.

**Offline by default.** Rule mode and mock-provider mode require no network access, no API key, and no
external service. Language-model cognition is optional, bounded, and never supplies purchase probability —
that stays rule-derived so results remain reproducible and explainable.

**Contracts are strict and immutable.** Domain models are frozen, strictly validated, reject non-finite
numbers, and revalidate on copy, so an invalid state cannot be constructed even by bypassing the intended
API.

---

## Status

Under active development. The behavioural core is complete and tested; the orchestration, persistence,
and presentation layers are in progress.

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
| Wheels, frozen binaries, and installers | Planned |

---

## Requirements

- Python 3.11, 3.12, or 3.13 (3.12 is the development baseline)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management

## Installation

<!-- adlife-install:start -->
uv tool install adlife-sim
<!-- adlife-install:end -->

Until a package index release exists (see the roadmap), install from source:

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
uv run adlife replay my-study <run-id>   # re-execute and verify byte-identical events
uv run adlife report my-study <run-id>   # self-contained HTML report (works offline)
uv run adlife compare control treatment  # paired A/B across seeds (common random numbers)
uv run adlife run my-study --live        # the four-panel terminal dashboard
uv run adlife doctor --offline           # installation readiness, zero network
```

Campaign and population documents are authored in YAML and validated before use. They are treated as
untrusted input: YAML is parsed with a safe loader, and any referenced asset path is resolved and
confirmed to lie beneath the project root before it is opened. Every command honors the global
`--format human|json|jsonl` output contract; see
[docs/cli-reference.md](docs/cli-reference.md) for the complete surface.

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
| [docs/cli-reference.md](docs/cli-reference.md) | Every command, flag, exit code, and the output contract |
| [docs/architecture.md](docs/architecture.md) | Layers, the cognition seam, event sourcing, the tick, artifacts |
| [docs/reproducibility.md](docs/reproducibility.md) | What a seed guarantees and how to reproduce any run |
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
population, world, and keyed random streams, so a paired difference is the declared treatment and
nothing else. An A/A control (the same scenario twice) must return exactly zero before any treatment
result is read, and a no-campaign control anchors what the campaigns themselves contribute. The full
pre-registration, including the 80% directional-stability rule and sensitivity ranges, lives in
[docs/methodology/experiment-protocol.md](docs/methodology/experiment-protocol.md).

---

## Reproducibility

The same seed, scenario, and code produce byte-identical events. Every stochastic decision is drawn
from a keyed random oracle scoped to its agent and purpose, so a change in evaluation order cannot
change an outcome. Every run persists a manifest recording the code version, scenario fingerprint,
provider, and seed, and `adlife replay` re-executes a stored run and verifies the event stream matches
the recorded one. The recipe is in [docs/reproducibility.md](docs/reproducibility.md).

---

## Limitations

Results are synthetic and exploratory. The society is small (1–30 agents) over short horizons (1–7
simulated days) with two advertising channels; purchase is a rule-derived proxy, not a transaction
model; nothing is calibrated against real-world data. Read
[docs/methodology/limitations.md](docs/methodology/limitations.md) before citing any number this
software produces.

---

## Roadmap

1. **Wheels, frozen binaries, and installers** — the `uv tool install adlife-sim` line above becomes
   real with a package-index release and prebuilt artifacts (tracking Task 18 of the implementation
   plan).
2. **Continuous integration and releases** — automated gates and tagged releases on GitHub.
3. **A separate commercial product** — the local, AGPL-licensed engine is designed to stay reusable:
   the model core is adapter-free, so a future, separately licensed SaaS offering can be built *on* it
   while this repository remains the open, inspectable research instrument. No hosted service exists
   today; any such product would be a distinct codebase and offering.

---

## Contributing, security, and citation

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) — fictional data only, tests required, DCO
  sign-off.
- Security: [SECURITY.md](SECURITY.md) — report vulnerabilities privately through GitHub.
- Citation: [CITATION.cff](CITATION.cff) and [CHANGELOG.md](CHANGELOG.md).

---

## Privacy, ethics, and data handling

- Personas are fictional and generated from templates; no real individual is represented.
- Persona inputs reject national identifiers, phone numbers, email addresses, and free-form secrets.
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
