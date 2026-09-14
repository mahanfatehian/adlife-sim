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
> used, collected, or modelled.** Results describe behaviour *inside a synthetic model*. They are not a
> statistically representative prediction about any real population, market, or country.

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
| Cognition ports, mock provider, rules provider, and replay | In progress |
| OpenAI-compatible provider with bounded fallback | Planned |
| Event sinks, SQLite storage, and replayable run artifacts | Planned |
| Full run orchestration | Planned |
| Metrics and paired experiments | Planned |
| Complete command-line interface | Planned |
| Live terminal user interface | Planned |
| JSON, CSV, and self-contained HTML reports | Planned |
| Packaging, frozen binaries, and installers | Planned |

---

## Requirements

- Python 3.11, 3.12, or 3.13 (3.12 is the development baseline)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management

## Installation

The package is not published to a package index. Install it from source:

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

```bash
uv run adlife --help
```

Campaign definitions are authored in YAML and validated before use. Campaign files are treated as
untrusted input: YAML is parsed with a safe loader, and any referenced asset path is resolved and
confirmed to lie beneath the project root before it is opened.

```bash
uv run adlife campaign --help
```

Additional commands are introduced as the orchestration, reporting, and interface layers land.

---

## Project layout

```
src/adlife/
├── core/              # Simulation model — no adapters, no I/O frameworks
│   ├── domain/        # Frozen, strictly validated contracts
│   ├── simulation/    # Clock, random oracle, movement, exposure, response, memory, social
│   └── ports/         # Interfaces the outside world implements
├── adapters/          # Concrete implementations of the ports
├── config/            # Strict settings, safe loading, path containment
├── cli/               # Command-line surface
└── resources/         # Packaged data (fictional name and routine templates)

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
