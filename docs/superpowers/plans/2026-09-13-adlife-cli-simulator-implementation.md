# AdLife Lab Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a polished, local-first Python CLI/TUI that simulates 1 to 30 fictional consumers with routines, memory, social relationships, mobile and billboard advertising exposure, deterministic experiments, bounded hybrid LLM cognition, replayable runs, and investor-ready reports.

**Architecture:** The reusable core uses Pydantic domain contracts and a two-phase Mesa-backed agent model: every tick plans against an immutable snapshot, then commits validated results in stable order. Ports isolate cognition, persistence, event output, and reporting; Typer, Textual, SQLite, Ollama/OpenAI-compatible HTTP, and HTML are adapters. Rule, mock, and replay modes are reproducible; live model output is cached and never described as deterministic.

**Tech Stack:** Python 3.12 development baseline; Python 3.11 through 3.13 support; uv; Hatchling; Mesa; Pydantic; Typer; Textual; httpx; SQLite; NetworkX; pandas; SciPy; Jinja2; Plotly; pytest; Hypothesis; Ruff; mypy; PyInstaller; GitHub Actions.

**Spec:** docs/superpowers/specs/2026-09-13-adlife-cli-simulator-design.md

## Global Constraints

- Repository folder: adlife
- Distribution: adlife-sim
- Import package: adlife
- Installed executable: adlife
- Initial version: 0.1.0
- Development interpreter: CPython 3.12
- Supported interpreters: CPython 3.11, 3.12, and 3.13
- Population range: 1 through 30 agents
- Duration range: 1 through 7 simulated days
- Tick length: 15 simulated minutes
- Hybrid cognition budget: at most 6 requests per agent and 180 total requests per run
- Tests and the default demo never require internet access or API keys
- Every campaign result is labeled synthetic and exploratory
- No accounts, web API, billing, subscriptions, Redis, PostgreSQL, or hosted infrastructure
- No identifiable-person data in fixtures, logs, prompts, or reports
- Secrets are accepted through environment variables or hidden prompts only
- Core modules never import Typer, Textual, SQLite, Plotly, or provider-specific adapters
- Agents never call an LLM or database directly
- Every tick uses plan and commit phases with stable ordering
- Canonical artifacts record seed, scenario hash, package version, Git SHA, lockfile hash, provider, model, prompt hash, and platform
- Local Git is initialized and committed; no remote is added and nothing is pushed
- Default source license: AGPL-3.0-only, retaining the sole copyright holder's ability to offer a separate commercial license
- Use test-driven development and commit after every independently passing task

---

## Execution Protocol

Run all commands from the root of a new empty folder. Read the specification and this plan completely before editing. Use apply_patch for hand-written files. Use uv for Python environments and dependencies. Do not modify global Git configuration. If Git cannot commit because identity is missing, stop and ask the repository owner to configure identity; do not invent one.

For every task:

1. add the named failing test;
2. run the exact narrow test and confirm the documented failure;
3. add only the implementation needed for that task;
4. run the narrow test;
5. run the task's wider verification command;
6. inspect git diff;
7. commit with the supplied message.

At phase boundaries, run:

~~~bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
~~~

## Locked File Map

~~~text
adlife/
├── .editorconfig
├── .github/
│   ├── dependabot.yml
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   └── feature_request.yml
│   ├── pull_request_template.md
│   └── workflows/
│       ├── ci.yml
│       └── release.yml
├── .gitignore
├── .python-version
├── AGENTS.md
├── CHANGELOG.md
├── CITATION.cff
├── CODE_OF_CONDUCT.md
├── CONNECT_GITHUB.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
├── SECURITY.md
├── pyproject.toml
├── uv.lock
├── docs/
│   ├── architecture.md
│   ├── cli-reference.md
│   ├── installation.md
│   ├── investor-demo.md
│   ├── quickstart.md
│   ├── releasing.md
│   ├── reproducibility.md
│   ├── methodology/
│   │   ├── experiment-protocol.md
│   │   ├── limitations.md
│   │   ├── model-card.md
│   │   └── odd-protocol.md
│   └── superpowers/
│       ├── plans/2026-09-13-adlife-cli-simulator-implementation.md
│       └── specs/2026-09-13-adlife-cli-simulator-design.md
├── examples/
│   ├── billboard-campaign.yaml
│   ├── phone-campaign.yaml
│   └── small-population.yaml
├── packaging/
│   └── adlife.spec
├── scripts/
│   ├── configure_repository.py
│   ├── install.ps1
│   ├── install.sh
│   └── smoke_release.py
├── src/adlife/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli/
│   │   ├── app.py
│   │   ├── errors.py
│   │   └── commands/
│   │       ├── campaign.py
│   │       ├── compare.py
│   │       ├── demo.py
│   │       ├── doctor.py
│   │       ├── init.py
│   │       ├── population.py
│   │       ├── replay.py
│   │       ├── report.py
│   │       ├── run.py
│   │       └── validate.py
│   ├── config/
│   │   ├── loader.py
│   │   ├── models.py
│   │   └── paths.py
│   ├── core/
│   │   ├── domain/
│   │   │   ├── campaign.py
│   │   │   ├── events.py
│   │   │   ├── person.py
│   │   │   ├── results.py
│   │   │   ├── scenario.py
│   │   │   ├── state.py
│   │   │   └── world.py
│   │   ├── experiments/
│   │   │   ├── comparison.py
│   │   │   ├── design.py
│   │   │   ├── metrics.py
│   │   │   └── sensitivity.py
│   │   ├── ports/
│   │   │   ├── cognition.py
│   │   │   ├── event_sink.py
│   │   │   └── run_store.py
│   │   └── simulation/
│   │       ├── agent.py
│   │       ├── clock.py
│   │       ├── decision.py
│   │       ├── engine.py
│   │       ├── exposure.py
│   │       ├── memory.py
│   │       ├── movement.py
│   │       ├── policies.py
│   │       ├── population.py
│   │       ├── rng.py
│   │       ├── routines.py
│   │       ├── runner.py
│   │       └── social.py
│   ├── adapters/
│   │   ├── cognition/
│   │   │   ├── cache.py
│   │   │   ├── mock.py
│   │   │   ├── openai_compatible.py
│   │   │   ├── prompts.py
│   │   │   ├── replay.py
│   │   │   ├── rules.py
│   │   │   └── service.py
│   │   ├── output/
│   │   │   ├── jsonl.py
│   │   │   └── plain.py
│   │   └── storage/
│   │       ├── schema.py
│   │       └── sqlite_store.py
│   ├── reporting/
│   │   ├── html.py
│   │   ├── resources.py
│   │   ├── static/report.css
│   │   └── templates/report.html.j2
│   ├── resources/
│   │   ├── demo/adlife.yaml
│   │   ├── demo/campaigns/billboard.yaml
│   │   ├── demo/campaigns/phone.yaml
│   │   ├── demo/population.yaml
│   │   └── routines/default.yaml
│   └── tui/
│       ├── app.py
│       ├── event_bus.py
│       ├── styles.tcss
│       └── widgets/
│           ├── agent_detail.py
│           ├── agent_table.py
│           ├── event_stream.py
│           └── metrics_strip.py
└── tests/
    ├── cli/
    ├── contract/
    ├── golden/
    ├── integration/
    ├── packaging/
    ├── property/
    ├── tui/
    └── unit/
~~~

Every Python package directory under src/adlife contains an __init__.py file; those repetitive files are omitted from the tree. Shared test builders and fixtures live in tests/conftest.py and tests/builders.py so test snippets never depend on production-only shortcuts.

# Phase 1 — Repository and Contracts

### Task 1: Bootstrap the typed package and local Git history

**Files:**
- Create: pyproject.toml
- Create: .python-version
- Create: .gitignore
- Create: .editorconfig
- Create: AGENTS.md
- Create: src/adlife/__init__.py
- Create: src/adlife/__main__.py
- Create: src/adlife/cli/app.py
- Create: tests/cli/test_root.py
- Preserve: docs/superpowers/specs/2026-09-13-adlife-cli-simulator-design.md
- Preserve: docs/superpowers/plans/2026-09-13-adlife-cli-simulator-implementation.md

**Interfaces:**
- Produces: adlife.__version__: str
- Produces: adlife.cli.app.app: typer.Typer
- Produces: adlife.cli.app.main() -> None

- [ ] **Step 1: Initialize local Git without a remote**

~~~bash
git init -b main
git status --short
git remote -v
~~~

Expected: an empty main branch and no remotes.

- [ ] **Step 2: Create package metadata**

Use this project configuration:

~~~toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "adlife-sim"
version = "0.1.0"
description = "A local-first synthetic consumer society and advertising simulation CLI"
readme = "README.md"
requires-python = ">=3.11,<3.14"
license = "AGPL-3.0-only"
keywords = ["agent-based-modeling", "simulation", "synthetic-audience", "advertising"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Environment :: Console",
  "License :: OSI Approved :: GNU Affero General Public License v3",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Scientific/Engineering",
]
dependencies = [
  "httpx>=0.27,<1",
  "jinja2>=3.1,<4",
  "mesa>=3,<4",
  "networkx>=3.3,<4",
  "pandas>=2.2,<3",
  "platformdirs>=4.3,<5",
  "plotly>=5.24,<7",
  "pydantic>=2.9,<3",
  "pyyaml>=6.0,<7",
  "scipy>=1.14,<2",
  "textual>=1",
  "typer>=0.16,<1",
]

[project.scripts]
adlife = "adlife.cli.app:main"

[dependency-groups]
dev = [
  "hypothesis>=6.112",
  "mypy>=1.11",
  "pyinstaller>=6.10",
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5",
  "ruff>=0.7",
  "types-pyyaml>=6.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/adlife"]

[tool.hatch.build]
include = [
  "src/adlife/**/*.py",
  "src/adlife/**/*.yaml",
  "src/adlife/**/*.j2",
  "src/adlife/**/*.css",
  "src/adlife/**/*.tcss",
]

[tool.pytest.ini_options]
addopts = "--strict-markers --strict-config"
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.coverage.run]
branch = true
source = ["adlife"]

[tool.coverage.report]
fail_under = 85
show_missing = true

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["adlife"]
~~~

Set .python-version to 3.12. Create an ignore file covering .venv, .env, __pycache__, build, dist, .pytest_cache, .mypy_cache, .ruff_cache, coverage files, *.sqlite3, generated runs, and generated reports. AGENTS.md must tell future agents to read the spec and plan, use uv, keep core adapter-free, run tests before claims, and never add a remote or push without explicit authorization.

- [ ] **Step 3: Write the failing root-command test**

~~~python
from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "adlife 0.1.0"


def test_help_lists_product_name() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AdLife Lab" in result.stdout
~~~

- [ ] **Step 4: Run the test and confirm the missing-package failure**

~~~bash
uv sync --all-groups
uv run pytest tests/cli/test_root.py -v
~~~

Expected: collection fails because adlife.cli.app does not exist.

- [ ] **Step 5: Implement the package entry point**

~~~python
# src/adlife/__init__.py
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("adlife-sim")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
~~~

~~~python
# src/adlife/cli/app.py
import typer

from adlife import __version__

app = typer.Typer(
    name="adlife",
    help="AdLife Lab — synthetic consumer society and campaign simulator.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"adlife {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the installed version.",
    ),
) -> None:
    del version


def main() -> None:
    app()
~~~

~~~python
# src/adlife/__main__.py
from adlife.cli.app import main

if __name__ == "__main__":
    main()
~~~

- [ ] **Step 6: Verify package, lockfile, and formatting**

~~~bash
uv lock
uv run pytest tests/cli/test_root.py -v
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run python -m adlife --version
~~~

Expected: all commands succeed and the final line is adlife 0.1.0.

- [ ] **Step 7: Create the first local commit**

~~~bash
git add .
git diff --cached --check
git commit -m "chore: bootstrap typed adlife package"
~~~

### Task 2: Add configuration, paths, and safe project loading

**Files:**
- Create: src/adlife/config/models.py
- Create: src/adlife/config/paths.py
- Create: src/adlife/config/loader.py
- Create: src/adlife/cli/errors.py
- Create: tests/unit/config/test_loader.py
- Create: tests/unit/config/test_paths.py

**Interfaces:**
- Produces: SimulationSettings, ProviderSettings, AppConfig
- Produces: load_app_config(path: Path) -> AppConfig
- Produces: resolve_project_path(root: Path, candidate: Path) -> Path
- Produces: ExitCode IntEnum

- [ ] **Step 1: Write rejection and round-trip tests**

~~~python
from pathlib import Path

import pytest

from adlife.config.loader import load_app_config
from adlife.config.paths import resolve_project_path


def test_loads_minimal_config(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation:\n  days: 3\n  tick_minutes: 15\n"
        "  population_size: 20\n  seed: 42\nprovider:\n  mode: mock\n",
        encoding="utf-8",
    )
    config = load_app_config(path)
    assert config.simulation.population_size == 20
    assert config.provider.mode == "mock"


def test_rejects_population_above_limit(tmp_path: Path) -> None:
    path = tmp_path / "adlife.yaml"
    path.write_text(
        "schema_version: 1\nsimulation:\n  days: 3\n  tick_minutes: 15\n"
        "  population_size: 31\n  seed: 42\nprovider:\n  mode: mock\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="less than or equal to 30"):
        load_app_config(path)


def test_path_cannot_escape_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside project root"):
        resolve_project_path(tmp_path, Path("../secret.txt"))
~~~

- [ ] **Step 2: Run the tests and confirm import failure**

~~~bash
uv run pytest tests/unit/config -v
~~~

- [ ] **Step 3: Implement strict configuration models**

~~~python
# src/adlife/config/models.py
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SimulationSettings(StrictModel):
    days: int = Field(default=3, ge=1, le=7)
    tick_minutes: Literal[15] = 15
    population_size: int = Field(default=20, ge=1, le=30)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    cognition_mode: Literal["rules", "hybrid", "replay"] = "rules"
    max_cognition_per_agent: int = Field(default=6, ge=0, le=6)
    max_cognition_total: int = Field(default=180, ge=0, le=180)


class ProviderSettings(StrictModel):
    mode: Literal["mock", "local", "remote"] = "mock"
    base_url: HttpUrl | None = None
    model: str = Field(default="mock-v1", min_length=1, max_length=120)
    timeout_seconds: float = Field(default=60, ge=1, le=120)
    retries: int = Field(default=2, ge=0, le=2)

    @model_validator(mode="before")
    @classmethod
    def apply_and_validate_url(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("mode") == "local" and data.get("base_url") is None:
            data["base_url"] = "http://127.0.0.1:11434/v1"
        if data.get("mode") == "remote" and data.get("base_url") is None:
            raise ValueError("remote provider requires base_url")
        return data


class AppConfig(StrictModel):
    schema_version: Literal[1]
    simulation: SimulationSettings
    provider: ProviderSettings
~~~

The loader must use yaml.safe_load, reject a non-mapping document, convert Pydantic validation errors into ValueError with the source path, and never interpolate environment variables inside YAML. The path resolver must call Path.resolve and require candidate.is_relative_to(root.resolve()).

- [ ] **Step 4: Add exact exit codes**

~~~python
# src/adlife/cli/errors.py
from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED = 1
    INPUT_ERROR = 2
    PROVIDER_ERROR = 3
    ARTIFACT_ERROR = 4
    INTERRUPTED = 130
~~~

- [ ] **Step 5: Run configuration verification**

~~~bash
uv run pytest tests/unit/config -v
uv run ruff format .
uv run ruff check .
uv run mypy src
~~~

- [ ] **Step 6: Commit**

~~~bash
git add src/adlife/config src/adlife/cli/errors.py tests/unit/config
git commit -m "feat: add safe project configuration"
~~~

### Task 3: Define versioned domain contracts

**Files:**
- Create: src/adlife/core/domain/person.py
- Create: src/adlife/core/domain/state.py
- Create: src/adlife/core/domain/world.py
- Create: src/adlife/core/domain/campaign.py
- Create: src/adlife/core/domain/events.py
- Create: src/adlife/core/domain/scenario.py
- Create: src/adlife/core/domain/results.py
- Create: tests/contract/test_scenario_contract.py
- Create: tests/contract/test_campaign_contract.py
- Create: tests/contract/test_event_contract.py

**Interfaces:**
- Produces: PersonProfile, ConsumerTraits, ConsumerState, Memory
- Produces: World, Zone, Route, RoutineBlock, Relationship
- Produces: Campaign, PhonePlacement, BillboardPlacement, CreativeFeatures
- Produces: DomainEvent, EventType, EventSource
- Produces: Scenario, RunManifest, SimulationResult

- [ ] **Step 1: Write contract tests for valid and invalid scenarios**

~~~python
from pydantic import ValidationError
import pytest

from adlife.core.domain.person import ConsumerTraits, PersonProfile


def test_traits_are_bounded() -> None:
    with pytest.raises(ValidationError):
        ConsumerTraits(
            price_sensitivity=1.1,
            novelty_seeking=0.5,
            social_susceptibility=0.5,
            advertising_skepticism=0.5,
            mobile_attention=0.5,
            outdoor_attention=0.5,
            brand_loyalty=0.5,
            impulsivity=0.5,
        )


def test_profile_is_fictional_and_serializable(valid_profile: PersonProfile) -> None:
    assert valid_profile.fictional is True
    restored = PersonProfile.model_validate_json(valid_profile.model_dump_json())
    assert restored == valid_profile
~~~

Add fixtures in tests/conftest.py for valid_profile, valid_campaign, and valid_scenario. Use person-001 and campaign-phone as stable identifiers.

- [ ] **Step 2: Run the contract tests and confirm missing models**

~~~bash
uv run pytest tests/contract -v
~~~

- [ ] **Step 3: Implement immutable identity and bounded mutable-state value objects**

~~~python
# src/adlife/core/domain/person.py
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConsumerTraits(DomainModel):
    price_sensitivity: float = Field(ge=0, le=1)
    novelty_seeking: float = Field(ge=0, le=1)
    social_susceptibility: float = Field(ge=0, le=1)
    advertising_skepticism: float = Field(ge=0, le=1)
    mobile_attention: float = Field(ge=0, le=1)
    outdoor_attention: float = Field(ge=0, le=1)
    brand_loyalty: float = Field(ge=0, le=1)
    impulsivity: float = Field(ge=0, le=1)


class PersonProfile(DomainModel):
    agent_id: str = Field(pattern=r"^person-[0-9]{3}$")
    display_name: str = Field(min_length=1, max_length=80)
    fictional: Literal[True] = True
    age: int = Field(ge=18, le=65)
    occupation: Literal["student", "office-worker", "retail-worker", "freelancer", "unemployed"]
    income_band: Literal["low", "middle", "high"]
    household_type: str = Field(min_length=1, max_length=80)
    home_zone: str
    work_or_study_zone: str | None
    interests: frozenset[str] = Field(min_length=1, max_length=12)
    traits: ConsumerTraits
    initial_brand_sentiment: float = Field(ge=-1, le=1)
    routine_template: str
~~~

ConsumerState is a separate frozen model. Updating state uses model_copy(update=...), preventing accidental mutation during the plan phase. Memory identifiers, state values, and counters must have explicit bounds from the specification.

- [ ] **Step 4: Implement discriminated campaign placements**

~~~python
from typing import Annotated, Literal, Union

from pydantic import Field

from adlife.core.domain.person import DomainModel


class TimeWindow(DomainModel):
    start_minute_of_day: int = Field(ge=0, lt=1440)
    end_minute_of_day: int = Field(gt=0, le=1440)


class PhonePlacement(DomainModel):
    channel: Literal["mobile-feed"]
    zone: Literal["online"] = "online"
    active_windows: tuple[TimeWindow, ...]
    frequency_cap: int = Field(ge=1, le=7)
    visibility: float = Field(ge=0, le=1)


class BillboardPlacement(DomainModel):
    channel: Literal["highway-billboard"]
    route_id: str
    active_windows: tuple[TimeWindow, ...]
    frequency_cap: int = Field(ge=1, le=14)
    visibility: float = Field(ge=0, le=1)


Placement = Annotated[
    Union[PhonePlacement, BillboardPlacement],
    Field(discriminator="channel"),
]
~~~

Campaign validation must require at least one placement, a positive price, a positive category_reference_price in the same currency, an ISO-style three-letter uppercase currency, unique placements, and asset_sha256 when asset_path is present.

- [ ] **Step 5: Implement causal immutable events**

~~~python
from enum import StrEnum
from typing import Any

from pydantic import Field

from adlife.core.domain.person import DomainModel


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    ACTIVITY_CHANGED = "agent.activity_changed"
    LOCATION_CHANGED = "agent.location_changed"
    CAMPAIGN_ELIGIBLE = "campaign.eligible"
    CAMPAIGN_IMPRESSION = "campaign.impression"
    CAMPAIGN_NOTICED = "campaign.noticed"
    CAMPAIGN_IGNORED = "campaign.ignored"
    COGNITION_REQUESTED = "cognition.requested"
    COGNITION_COMPLETED = "cognition.completed"
    COGNITION_FALLBACK = "cognition.fallback"
    MEMORY_CREATED = "memory.created"
    SOCIAL_SHARED = "social.shared"
    SOCIAL_RECEIVED = "social.received"
    STATE_UPDATED = "agent.state_updated"
    DAY_REFLECTED = "day.reflected"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class EventSource(StrEnum):
    RULE = "rule"
    MOCK = "mock"
    LOCAL_LLM = "local-llm"
    REMOTE_LLM = "remote-llm"
    REPLAY = "replay"
    FALLBACK = "fallback"


class DomainEvent(DomainModel):
    event_id: str
    run_id: str
    simulated_minute: int = Field(ge=0)
    sequence: int = Field(ge=0)
    event_type: EventType
    agent_id: str | None = None
    campaign_id: str | None = None
    channel: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: EventSource
    model_id: str | None = None
    prompt_hash: str | None = None
    caused_by_event_ids: tuple[str, ...] = ()
~~~

- [ ] **Step 6: Add whole-scenario cross-reference validation**

Scenario validation must reject duplicate IDs, unknown zones, unknown routes, relationship endpoints outside the population, self-relationships, disconnected graphs with at least three agents, campaigns outside the simulation duration, and more than 30 agents. Store relationships as versioned edge records, not NetworkX serialization.

- [ ] **Step 7: Verify and commit**

~~~bash
uv run pytest tests/contract -v
uv run ruff format .
uv run ruff check .
uv run mypy src
git add src/adlife/core/domain tests/contract tests/conftest.py
git commit -m "feat(core): define versioned simulation contracts"
~~~

### Task 4: Add deterministic clock, random oracle, event IDs, and fingerprints

**Files:**
- Create: src/adlife/core/simulation/clock.py
- Create: src/adlife/core/simulation/rng.py
- Create: src/adlife/core/simulation/engine.py
- Create: tests/unit/simulation/test_clock.py
- Create: tests/unit/simulation/test_rng.py
- Create: tests/unit/simulation/test_event_ids.py
- Create: tests/golden/test_manifest_fingerprint.py

**Interfaces:**
- Produces: SimClock.current_minute, minute_of_day, day_index, advance()
- Produces: RandomOracle.uniform(namespace, agent_id, tick, decision_index) -> float
- Produces: stable_event_id(run_id: str, sequence: int) -> str
- Produces: canonical_sha256(value: BaseModel | Mapping[str, object]) -> str

- [ ] **Step 1: Write deterministic primitive tests**

~~~python
from adlife.core.simulation.rng import RandomOracle


def test_random_oracle_is_keyed_not_sequence_dependent() -> None:
    oracle = RandomOracle(42)
    first = oracle.uniform("notice", "person-001", 15, 0)
    oracle.uniform("unrelated", "person-002", 30, 0)
    second = oracle.uniform("notice", "person-001", 15, 0)
    assert first == second


def test_different_key_changes_draw() -> None:
    oracle = RandomOracle(42)
    assert oracle.uniform("notice", "person-001", 15, 0) != oracle.uniform(
        "notice", "person-001", 30, 0
    )
~~~

Clock tests must cover midnight, the final tick, refusal to advance beyond configured duration, and 15-minute alignment.

- [ ] **Step 2: Run and confirm missing implementation**

~~~bash
uv run pytest tests/unit/simulation/test_clock.py tests/unit/simulation/test_rng.py -v
~~~

- [ ] **Step 3: Implement the keyed random oracle**

~~~python
from dataclasses import dataclass
from hashlib import sha256
import random


@dataclass(frozen=True, slots=True)
class RandomOracle:
    root_seed: int

    def _seed(self, namespace: str, agent_id: str, tick: int, decision_index: int) -> int:
        material = f"{self.root_seed}|{namespace}|{agent_id}|{tick}|{decision_index}"
        return int.from_bytes(sha256(material.encode("utf-8")).digest()[:8], "big")

    def uniform(
        self,
        namespace: str,
        agent_id: str,
        tick: int,
        decision_index: int,
    ) -> float:
        return random.Random(self._seed(namespace, agent_id, tick, decision_index)).random()
~~~

- [ ] **Step 4: Implement canonical hashing and stable IDs**

Canonical JSON uses sorted keys, compact separators, UTF-8, and Pydantic json-mode dumps. Do not hash Python repr or built-in hash values.

~~~python
def stable_event_id(run_id: str, sequence: int) -> str:
    return f"{run_id}:event-{sequence:08d}"
~~~

Run manifest fingerprint input contains scenario JSON, seed, package version, Git SHA, uv.lock SHA-256, provider mode, model identifier, prompt version, and platform. A missing Git SHA is recorded as uncommitted, not omitted.

- [ ] **Step 5: Verify exact deterministic behavior**

~~~bash
uv run pytest tests/unit/simulation/test_clock.py \
  tests/unit/simulation/test_rng.py \
  tests/unit/simulation/test_event_ids.py \
  tests/golden/test_manifest_fingerprint.py -v
~~~

- [ ] **Step 6: Commit**

~~~bash
git add src/adlife/core/simulation tests/unit/simulation tests/golden
git commit -m "feat(core): add deterministic simulation primitives"
~~~

### Task 5: Generate fictional populations, routines, and connected relationships

**Files:**
- Create: src/adlife/core/simulation/population.py
- Create: src/adlife/core/simulation/routines.py
- Create: src/adlife/resources/routines/default.yaml
- Create: tests/unit/simulation/test_population.py
- Create: tests/unit/simulation/test_routines.py
- Create: tests/property/test_population_invariants.py

**Interfaces:**
- Produces: generate_population(size: int, seed: int, locale: str) -> tuple[PersonProfile, ...]
- Produces: generate_relationships(profiles, seed) -> tuple[Relationship, ...]
- Produces: load_routine_templates() -> Mapping[str, RoutineTemplate]
- Produces: routine_at(profile, simulated_minute, oracle) -> RoutineBlock

- [ ] **Step 1: Write fixed-seed and invariant tests**

~~~python
from adlife.core.simulation.population import generate_population, generate_relationships


def test_population_generation_is_repeatable() -> None:
    assert generate_population(20, 42, "fa-IR") == generate_population(20, 42, "fa-IR")


def test_generated_ids_are_stable() -> None:
    profiles = generate_population(3, 42, "fa-IR")
    assert [profile.agent_id for profile in profiles] == [
        "person-001",
        "person-002",
        "person-003",
    ]


def test_relationship_graph_has_no_isolates() -> None:
    profiles = generate_population(20, 42, "fa-IR")
    edges = generate_relationships(profiles, 42)
    degree = {profile.agent_id: 0 for profile in profiles}
    for edge in edges:
        degree[edge.source_id] += 1
        degree[edge.target_id] += 1
    assert min(degree.values()) >= 1
    assert max(degree.values()) <= 8
~~~

Hypothesis tests must cover every size from 1 through 30, trait bounds, age bounds, unique IDs, graph connection for size at least 3, and no more than eight neighbors.

- [ ] **Step 2: Run tests and confirm failure**

~~~bash
uv run pytest tests/unit/simulation/test_population.py \
  tests/unit/simulation/test_routines.py \
  tests/property/test_population_invariants.py -v
~~~

- [ ] **Step 3: Implement profile generation without real-person data**

Use fixed fictional first-name lists embedded as package resources for fa-IR and en-US. Combine first name with the stable person number rather than a real surname. Generate occupations and income bands from explicit weighted tables. Use RandomOracle keys so adding a new trait does not change existing traits.

Every trait value is generated with:

~~~python
round(oracle.uniform(f"trait:{trait_name}", agent_id, 0, 0), 4)
~~~

Do not use Faker, online data, census claims, or protected attributes. The generator models variety, not a real demographic distribution.

- [ ] **Step 4: Implement routine templates**

The YAML resource defines all five weekday and weekend routines as non-overlapping minute windows. It includes sleep, breakfast, commute, work or study, phone-check, shopping or leisure, socializing, and reflection. Validation must require complete 0 through 1440 coverage and reject overlaps or gaps.

Apply deterministic jitter of at most 30 minutes to commute and leisure blocks while preserving ordering and day coverage.

- [ ] **Step 5: Implement connected social graph generation**

Start with a deterministic ring for populations of three or more, then add homophily-weighted edges until mean degree is between three and five. Canonicalize every edge so source_id is lexically smaller than target_id. Relationship kind is friend, colleague, family, or online; strength is 0.2 through 1.0.

- [ ] **Step 6: Verify package-resource loading**

~~~bash
uv build
uv run python -c "from adlife.core.simulation.routines import load_routine_templates; print(len(load_routine_templates()))"
uv run pytest tests/unit/simulation/test_population.py \
  tests/unit/simulation/test_routines.py \
  tests/property/test_population_invariants.py -v
~~~

Expected: the resource loads from the source tree and the built wheel contains it.

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/core/simulation/population.py \
  src/adlife/core/simulation/routines.py \
  src/adlife/resources tests/unit/simulation tests/property
git commit -m "feat(core): generate fictional populations and routines"
~~~

### Task 6: Add world routes and two-phase movement planning

**Files:**
- Create: src/adlife/core/simulation/agent.py
- Create: src/adlife/core/simulation/movement.py
- Create: tests/unit/simulation/test_movement.py
- Create: tests/property/test_world_invariants.py

**Interfaces:**
- Produces: Snapshot, MovementIntent, TickPlan, TickOutcome
- Produces: ConsumerAgent.plan_movement(snapshot: Snapshot) -> MovementIntent
- Produces: resolve_movement(intents, world) -> tuple[DomainEvent, ...]

- [ ] **Step 1: Write tests that expose ordering and route defects**

~~~python
def test_agent_cannot_teleport_between_unconnected_zones(valid_scenario) -> None:
    snapshot = snapshot_with_agent("person-001", zone="home-north")
    intent = MovementIntent(
        agent_id="person-001",
        from_zone="home-north",
        to_zone="retail-center",
        activity="shopping",
    )
    with pytest.raises(InvalidMovement, match="no route"):
        resolve_movement((intent,), valid_scenario.world, snapshot)


def test_reordering_intents_does_not_change_events(valid_world, movement_intents) -> None:
    forward = resolve_movement(tuple(movement_intents), valid_world)
    reverse = resolve_movement(tuple(reversed(movement_intents)), valid_world)
    assert normalize_events(forward) == normalize_events(reverse)
~~~

- [ ] **Step 2: Run the narrow tests and confirm failure**

~~~bash
uv run pytest tests/unit/simulation/test_movement.py \
  tests/property/test_world_invariants.py -v
~~~

- [ ] **Step 3: Implement immutable snapshots and intents**

Snapshots map agent identifiers to immutable profile/state pairs. Agents plan from the snapshot and routine; they do not mutate ConsumerState. Sort intents by agent_id before resolving them. A movement may remain in the same zone or traverse one declared route during a tick.

Use Mesa Model and Agent only inside agent.py and engine.py. Domain contracts must not expose Mesa objects. AdLifeModel calls super().__init__(seed=seed), owns agents, and exports plain snapshots.

- [ ] **Step 4: Implement the plan/commit boundary**

~~~python
class AdLifeModel(mesa.Model):
    def plan_tick(self) -> TickPlan:
        snapshot = self.snapshot()
        intents = tuple(
            self.agent_by_id[agent_id].plan(snapshot) for agent_id in sorted(self.agent_by_id)
        )
        return TickPlan(snapshot=snapshot, intents=intents)

    def commit_tick(
        self,
        plan: TickPlan,
        cognition: Mapping[str, CognitionResult],
    ) -> TickOutcome:
        if plan.snapshot.fingerprint != self.snapshot().fingerprint:
            raise StaleTickPlan("state changed between plan and commit")
        return self._commit_in_stable_order(plan, cognition)
~~~

Keep cognition types behind TYPE_CHECKING or the core cognition port to avoid adapter imports.

- [ ] **Step 5: Run movement and invariants**

~~~bash
uv run pytest tests/unit/simulation/test_movement.py \
  tests/property/test_world_invariants.py -v
uv run mypy src/adlife/core
~~~

- [ ] **Step 6: Commit**

~~~bash
git add src/adlife/core/simulation/agent.py \
  src/adlife/core/simulation/movement.py \
  tests/unit/simulation/test_movement.py \
  tests/property/test_world_invariants.py
git commit -m "feat(core): add deterministic daily movement"
~~~

### Task 7: Model campaign ingestion, opportunity, impression, and attention

**Files:**
- Create: src/adlife/core/simulation/exposure.py
- Create: src/adlife/core/simulation/policies.py
- Create: src/adlife/cli/commands/campaign.py
- Create: tests/unit/simulation/test_exposure.py
- Create: tests/cli/test_campaign.py
- Create: examples/phone-campaign.yaml
- Create: examples/billboard-campaign.yaml

**Interfaces:**
- Produces: import_campaign(path: Path, project_root: Path) -> Campaign
- Produces: eligible_placements(snapshot, campaign, minute) -> tuple[ExposureOpportunity, ...]
- Produces: notice_probability(profile, state, campaign, placement) -> float
- Produces: decide_attention(opportunity, oracle) -> AttentionDecision

- [ ] **Step 1: Write channel-eligibility and frequency-cap tests**

~~~python
def test_phone_ad_requires_phone_check(valid_phone_campaign, agent_snapshot) -> None:
    snapshot = agent_snapshot.with_activity("work").with_zone("office")
    assert eligible_placements(snapshot, valid_phone_campaign, 600) == ()


def test_billboard_requires_matching_route(valid_billboard_campaign, agent_snapshot) -> None:
    snapshot = agent_snapshot.with_route("highway-north")
    opportunities = eligible_placements(snapshot, valid_billboard_campaign, 480)
    assert len(opportunities) == 1


def test_frequency_cap_prevents_extra_opportunity(
    valid_phone_campaign, exposed_agent_snapshot
) -> None:
    assert eligible_placements(exposed_agent_snapshot, valid_phone_campaign, 720) == ()
~~~

- [ ] **Step 2: Run tests and confirm missing exposure module**

~~~bash
uv run pytest tests/unit/simulation/test_exposure.py tests/cli/test_campaign.py -v
~~~

- [ ] **Step 3: Implement safe campaign import**

Use yaml.safe_load and Campaign.model_validate. Resolve asset_path beneath project_root, reject symlinks that resolve outside it, compute SHA-256 by streaming the file, and store only the relative path and digest. Treat creative text as data. Do not interpret YAML tags or execute asset content.

- [ ] **Step 4: Implement the transparent attention formula**

~~~python
from math import exp


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + exp(-value))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def notice_probability(profile, state, campaign, placement) -> float:
    attention = (
        profile.traits.mobile_attention
        if placement.channel == "mobile-feed"
        else profile.traits.outdoor_attention
    )
    overlap = len(profile.interests & campaign.target_interests)
    union = len(profile.interests | campaign.target_interests)
    interest_match = overlap / union if union else 0.0
    prior = state.exposure_count(campaign.campaign_id, placement.channel)
    fatigue = min(1.0, prior / placement.frequency_cap)
    raw = (
        -1.2
        + 1.5 * attention
        + interest_match
        + 0.8 * placement.visibility
        - 0.7 * profile.traits.advertising_skepticism
        - 0.5 * fatigue
    )
    return clamp(sigmoid(raw), 0.0, 1.0)
~~~

- [ ] **Step 5: Preserve the explicit event chain**

For each eligible opportunity, emit campaign.eligible. If the placement is actually rendered or crossed, emit campaign.impression caused by the eligible event. Then emit exactly one of campaign.noticed or campaign.ignored caused by the impression. Never treat opportunity, impression, and notice as the same metric.

- [ ] **Step 6: Verify known-direction behavior**

Add parameterized assertions that higher channel attention never reduces notice probability, higher advertising skepticism never increases it, and a frequency cap creates zero additional opportunities after the cap.

~~~bash
uv run pytest tests/unit/simulation/test_exposure.py tests/cli/test_campaign.py -v
uv run ruff check src/adlife/core/simulation/exposure.py
~~~

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/core/simulation/exposure.py \
  src/adlife/core/simulation/policies.py \
  src/adlife/cli/commands/campaign.py examples tests
git commit -m "feat(core): model mobile and billboard exposure"
~~~

# Phase 2 — Consumer Dynamics and Hybrid Cognition

### Task 8: Add response, memory decay, social influence, and purchase proxy

**Files:**
- Create: src/adlife/core/simulation/decision.py
- Create: src/adlife/core/simulation/memory.py
- Create: src/adlife/core/simulation/social.py
- Create: tests/unit/simulation/test_decision.py
- Create: tests/unit/simulation/test_memory.py
- Create: tests/unit/simulation/test_social.py
- Create: tests/property/test_state_bounds.py

**Interfaces:**
- Produces: RuleResponse
- Produces: evaluate_rule_response(profile, state, campaign, placement) -> RuleResponse
- Produces: apply_response(state, response, caused_by) -> StateTransition
- Produces: decay_memories(state, day_index) -> ConsumerState
- Produces: plan_social_shares(snapshot, noticed_events, oracle) -> tuple[SocialIntent, ...]
- Produces: purchase_proxy(profile, state, campaign) -> PurchaseDecision

- [ ] **Step 1: Write boundary and direction tests**

~~~python
def test_price_sensitivity_reduces_intention_for_expensive_product(
    valid_profile, consumer_state, cheap_campaign, expensive_campaign, phone_placement
) -> None:
    cheap = evaluate_rule_response(valid_profile, consumer_state, cheap_campaign, phone_placement)
    expensive = evaluate_rule_response(
        valid_profile, consumer_state, expensive_campaign, phone_placement
    )
    assert expensive.purchase_intention <= cheap.purchase_intention


def test_recall_saturates_at_one(consumer_state, positive_response) -> None:
    updated = apply_response(consumer_state, positive_response, ("event-0001",))
    assert 0 <= updated.state.recall_strength <= 1


def test_social_disabled_produces_no_share(valid_snapshot, noticed_events) -> None:
    intents = plan_social_shares(
        valid_snapshot,
        noticed_events,
        RandomOracle(42),
        social_enabled=False,
    )
    assert intents == ()
~~~

Add tests for negative word of mouth, one-hop-per-tick, valid relationship edges, memory cap of five episodic entries, overnight fatigue reduction, nonnegative budget, and no purchase proxy without need and affordability.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/unit/simulation/test_decision.py \
  tests/unit/simulation/test_memory.py \
  tests/unit/simulation/test_social.py \
  tests/property/test_state_bounds.py -v
~~~

- [ ] **Step 3: Implement documented rule response**

~~~python
@dataclass(frozen=True, slots=True)
class RuleResponse:
    sentiment_delta: float
    recall_delta: float
    purchase_intention: float
    share_probability: float
    valence: float
    relevance: float
    credibility: float


def evaluate_rule_response(profile, state, campaign, placement) -> RuleResponse:
    interest_match = jaccard(profile.interests, campaign.target_interests)
    normalized_price = campaign.price.amount / campaign.category_reference_price
    affordability = clamp(1.25 - normalized_price * profile.traits.price_sensitivity, 0, 1)
    frequency_fatigue = min(
        1.0,
        state.exposure_count(campaign.campaign_id, placement.channel) / placement.frequency_cap,
    )
    value_match = (
        0.55 * interest_match + 0.25 * profile.traits.novelty_seeking + 0.20 * affordability
    )
    sentiment_delta = clamp(
        0.18 * value_match
        - 0.12 * profile.traits.advertising_skepticism
        - 0.06 * frequency_fatigue,
        -0.20,
        0.20,
    )
    recall_delta = clamp(
        0.22 * channel_attention(profile, placement.channel)
        + 0.12 * profile.traits.novelty_seeking
        - 0.08 * frequency_fatigue,
        0,
        0.30,
    )
    normalized_sentiment = (state.brand_sentiment + sentiment_delta + 1) / 2
    intention = clamp(
        0.40 * normalized_sentiment
        + 0.25 * value_match
        + 0.20 * state.social_proof
        + 0.15 * profile.traits.impulsivity,
        0,
        1,
    )
    return RuleResponse(
        sentiment_delta=sentiment_delta,
        recall_delta=recall_delta,
        purchase_intention=intention,
        share_probability=clamp(
            abs(sentiment_delta)
            * profile.traits.social_susceptibility
            * profile.traits.novelty_seeking,
            0,
            1,
        ),
        valence=clamp(sentiment_delta * 5, -1, 1),
        relevance=interest_match,
        credibility=clamp(1 - profile.traits.advertising_skepticism, 0, 1),
    )
~~~

Add category_reference_price as a positive campaign field so the formula is explicit and testable. The LLM never supplies purchase probability.

- [ ] **Step 4: Implement memory and fatigue mechanics**

Memory has created_minute, kind, summary, salience, campaign_id, and caused_by_event_ids. Encode only noticed advertising, trusted social messages, purchase-proxy decisions, and daily reflections. Sort memories by salience then recency and retain five episodic memories.

At each daily reflection:

~~~python
recall = clamp(state.recall_strength * 0.85 + state.daily_reinforcement, 0, 1)
fatigue = clamp(state.ad_fatigue * 0.60, 0, 1)
~~~

Clear daily reinforcement after applying it. Repeated encoding uses diminishing returns, so the same campaign cannot add the full recall amount repeatedly.

- [ ] **Step 5: Implement bounded social propagation**

Sharing requires a noticed event, a relationship edge, a co-location or online-contact opportunity, and a successful keyed random draw. Sort social intents by sender_id and receiver_id. Mark the receiver's state with social proof and awareness, not an advertising exposure. Include positive and negative valence. Store caused_by_event_ids. Reject a message that already traversed an edge in the current tick.

- [ ] **Step 6: Implement purchase proxy**

A purchase proxy requires active_need=True, disposable_budget at least campaign price, purchase intention at least 0.70, and a keyed random draw below intention. It emits a proxy decision event and never uses the word sale in machine fields or report claims.

- [ ] **Step 7: Verify 100-seed invariants**

~~~bash
uv run pytest tests/unit/simulation/test_decision.py \
  tests/unit/simulation/test_memory.py \
  tests/unit/simulation/test_social.py \
  tests/property/test_state_bounds.py -v
uv run mypy src/adlife/core
~~~

- [ ] **Step 8: Commit**

~~~bash
git add src/adlife/core/simulation/decision.py \
  src/adlife/core/simulation/memory.py \
  src/adlife/core/simulation/social.py \
  src/adlife/core/domain tests
git commit -m "feat(core): add bounded consumer response dynamics"
~~~

### Task 9: Define cognition ports, mock provider, rules provider, and replay

**Files:**
- Create: src/adlife/core/ports/cognition.py
- Create: src/adlife/adapters/cognition/mock.py
- Create: src/adlife/adapters/cognition/rules.py
- Create: src/adlife/adapters/cognition/cache.py
- Create: src/adlife/adapters/cognition/replay.py
- Create: tests/contract/test_cognition_provider.py
- Create: tests/unit/cognition/test_cache.py
- Create: tests/unit/cognition/test_replay.py

**Interfaces:**
- Produces: CognitionRequest, CognitionResult, ProviderUsage
- Produces: CognitionProvider.evaluate(request: CognitionRequest) -> Awaitable[CognitionResult]
- Produces: MockCognitionProvider, RuleCognitionProvider, ReplayCognitionProvider
- Produces: CognitionCache.get(key), put(key, record), make_key(request, provider_metadata)

- [ ] **Step 1: Write a shared provider contract suite**

~~~python
from collections.abc import Callable

import pytest

from adlife.core.ports.cognition import CognitionProvider


@pytest.mark.asyncio
async def assert_provider_contract(
    provider_factory: Callable[[], CognitionProvider],
    cognition_request,
) -> None:
    provider = provider_factory()
    result = await provider.evaluate(cognition_request)
    assert -1 <= result.valence <= 1
    assert 0 <= result.relevance <= 1
    assert 0 <= result.credibility <= 1
    assert -0.10 <= result.rule_modifier <= 0.10
    assert len(result.grounded_reasons) <= 3
~~~

Run this suite against rules, mock, and replay providers. The replay provider receives a pre-populated cache fixture.

- [ ] **Step 2: Run and confirm contract failure**

~~~bash
uv run pytest tests/contract/test_cognition_provider.py \
  tests/unit/cognition/test_cache.py \
  tests/unit/cognition/test_replay.py -v
~~~

- [ ] **Step 3: Implement the core protocol and schemas**

~~~python
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CognitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CognitionRequest(CognitionModel):
    request_id: str
    run_id: str
    simulated_minute: int
    agent_id: str
    fictional_persona: dict[str, object]
    activity: str
    mood: float = Field(ge=-1, le=1)
    relevant_memories: tuple[str, ...]
    campaign: dict[str, object]
    channel: str
    exposure_count: int = Field(ge=1)
    creative_sha256: str | None = None
    prompt_version: str = "cognition-v1"


class CognitionResult(CognitionModel):
    request_id: str
    interpretation: str = Field(min_length=1, max_length=240)
    valence: float = Field(ge=-1, le=1)
    relevance: float = Field(ge=0, le=1)
    credibility: float = Field(ge=0, le=1)
    discussion_hook: str = Field(min_length=1, max_length=200)
    grounded_reasons: tuple[str, ...] = Field(max_length=3)
    memory_summary: str = Field(min_length=1, max_length=280)
    rule_modifier: float = Field(ge=-0.10, le=0.10)
    safety_flags: tuple[str, ...] = ()


class CognitionProvider(Protocol):
    async def evaluate(self, request: CognitionRequest) -> CognitionResult: ...
~~~

- [ ] **Step 4: Implement deterministic rules and mock providers**

RuleCognitionProvider derives its result entirely from evaluate_rule_response. MockCognitionProvider hashes request_id and canonical request JSON into one of five fixed response fixtures. Neither reads a clock, network, environment variable, or mutable global random stream.

- [ ] **Step 5: Implement content-addressed cache and replay**

Cache key input:

~~~text
provider kind
base URL without credentials
model identifier and optional model digest
sampling settings
prompt version and prompt SHA-256
canonical cognition request JSON
creative SHA-256
~~~

Store one JSON record per key beneath the run cache directory. Write to a temporary sibling and atomically replace the final path. Record raw response, validated response, latency, token counts, cache_hit, and fallback_reason. ReplayCognitionProvider raises CacheMiss with the exact key and performs no network access.

- [ ] **Step 6: Verify cache secrecy and repeatability**

Add tests proving that API keys do not affect keys, do not appear in records, identical inputs return the same key, changed prompt versions change the key, and replay output equals the original validated output.

~~~bash
uv run pytest tests/contract/test_cognition_provider.py \
  tests/unit/cognition/test_cache.py \
  tests/unit/cognition/test_replay.py -v
~~~

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/core/ports/cognition.py \
  src/adlife/adapters/cognition tests/contract tests/unit/cognition
git commit -m "feat(cognition): add deterministic provider contracts and replay"
~~~

### Task 10: Add the OpenAI-compatible provider and safe fallback service

**Files:**
- Create: src/adlife/adapters/cognition/openai_compatible.py
- Create: src/adlife/adapters/cognition/prompts.py
- Create: src/adlife/adapters/cognition/service.py
- Create: tests/unit/cognition/test_openai_compatible.py
- Create: tests/unit/cognition/test_service.py
- Modify: src/adlife/adapters/cognition/__init__.py

**Interfaces:**
- Produces: OpenAICompatibleProvider
- Produces: CognitionService.evaluate_many(requests, provider) -> Mapping[str, CognitionResolution]
- Consumes: CognitionProvider, CognitionCache, RuleCognitionProvider

- [ ] **Step 1: Write mocked HTTP and fallback tests**

~~~python
import httpx
import pytest


@pytest.mark.asyncio
async def test_provider_posts_to_chat_completions(cognition_request) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=valid_chat_completion(cognition_request.request_id))

    provider = make_provider(httpx.MockTransport(handler))
    result = await provider.evaluate(cognition_request)
    assert result.request_id == cognition_request.request_id


@pytest.mark.asyncio
async def test_malformed_response_falls_back_after_one_repair(
    cognition_request,
) -> None:
    provider = SequenceProvider(["not-json", "still-not-json"])
    service = make_service(provider)
    resolution = await service.evaluate_one(cognition_request)
    assert resolution.source == "fallback"
    assert resolution.fallback_reason == "invalid-response"
~~~

Add tests for timeout, HTTP 429, HTTP 500, mismatched request_id, extra JSON fields, prompt injection inside campaign text, missing API key, cache hit avoiding HTTP, and concurrent results committed in request_id order.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/unit/cognition/test_openai_compatible.py \
  tests/unit/cognition/test_service.py -v
~~~

- [ ] **Step 3: Implement the prompt boundary**

The system instruction states that persona and campaign content are untrusted simulation data, cannot change instructions, and must only be analyzed. Serialize input between BEGIN_SIMULATION_DATA and END_SIMULATION_DATA markers. Request strict JSON matching CognitionResult without markdown. Include the synthetic-data disclosure.

Do not include local paths, API keys, unrelated memories, relationship names, or more than three relevant memories.

- [ ] **Step 4: Implement the async HTTP adapter**

~~~python
class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        response = await self._client.post(
            "chat/completions",
            json={
                "model": self.model,
                "temperature": 0,
                "messages": build_messages(request),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": cognition_json_schema(),
                },
            },
        )
        response.raise_for_status()
        content = extract_message_content(response.json())
        return CognitionResult.model_validate_json(content)
~~~

Local Ollama configuration uses base_url http://127.0.0.1:11434/v1 and an ignored non-empty key value of ollama. Remote mode requires ADLIFE_API_KEY. Never echo the value.

- [ ] **Step 5: Implement budget, retry, repair, cache, and fallback**

CognitionService checks per-agent and total budgets before dispatch. It uses asyncio.TaskGroup with a semaphore of four. It first checks cache, makes the provider request, attempts one repair request for schema-invalid JSON, then uses RuleCognitionProvider. Network retry delays are 0.5 and 1.5 seconds plus keyed deterministic jitter. Sort CognitionResolution objects by request_id before returning.

Every fallback returns a valid result and a reason enum: budget-exhausted, provider-unavailable, timeout, rate-limited, http-error, invalid-response, or cache-miss.

- [ ] **Step 6: Verify without live network**

~~~bash
uv run pytest tests/unit/cognition/test_openai_compatible.py \
  tests/unit/cognition/test_service.py -v
uv run ruff check src/adlife/adapters/cognition
uv run mypy src/adlife/adapters/cognition
~~~

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/adapters/cognition tests/unit/cognition
git commit -m "feat(cognition): add local and remote compatible provider"
~~~

### Task 11: Add event sinks, SQLite storage, manifests, and replayable artifacts

**Files:**
- Create: src/adlife/core/ports/event_sink.py
- Create: src/adlife/core/ports/run_store.py
- Create: src/adlife/adapters/output/plain.py
- Create: src/adlife/adapters/output/jsonl.py
- Create: src/adlife/adapters/storage/schema.py
- Create: src/adlife/adapters/storage/sqlite_store.py
- Create: tests/contract/test_event_sink.py
- Create: tests/contract/test_run_store.py
- Create: tests/integration/test_sqlite_store.py
- Create: tests/integration/test_artifact_layout.py

**Interfaces:**
- Produces: EventSink.append_many(run_id, events) -> None
- Produces: RunStore.create_run(manifest), append_events(events), save_checkpoint(checkpoint), complete_run(result)
- Produces: RunStore.load_run(run_id) -> StoredRun
- Produces: SQLiteRunStore, JsonlEventSink, PlainEventSink

- [ ] **Step 1: Write shared persistence contract tests**

~~~python
def assert_run_store_contract(store, manifest, domain_events) -> None:
    store.create_run(manifest)
    store.append_events(domain_events)
    loaded = store.load_run(manifest.run_id)
    assert loaded.manifest == manifest
    assert loaded.events == tuple(domain_events)


def test_append_is_atomic(store, manifest, domain_events, failing_connection) -> None:
    store.create_run(manifest)
    failing_connection.fail_after_statement(2)
    with pytest.raises(StorageError):
        store.append_events(domain_events)
    assert store.load_run(manifest.run_id).events == ()
~~~

Add tests for duplicate event IDs, non-monotonic sequences, unknown causal references, corrupt JSONL, schema version mismatch, interrupted checkpoint write, and UTF-8 Persian text.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/contract/test_event_sink.py \
  tests/contract/test_run_store.py \
  tests/integration/test_sqlite_store.py \
  tests/integration/test_artifact_layout.py -v
~~~

- [ ] **Step 3: Implement the event and store protocols**

Use runtime-checkable Protocol definitions. append_many receives a Sequence and must preserve order. Event sinks are observers; RunStore is authoritative persistence. PlainEventSink prints human-readable lines. JsonlEventSink writes one canonical event JSON per line and flushes after each committed tick.

- [ ] **Step 4: Implement versioned SQLite schema**

Create tables:

~~~sql
CREATE TABLE schema_meta (
  version INTEGER PRIMARY KEY
);
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  sequence INTEGER NOT NULL,
  simulated_minute INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  event_json TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE TABLE checkpoints (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  simulated_minute INTEGER NOT NULL,
  checkpoint_json TEXT NOT NULL,
  PRIMARY KEY(run_id, simulated_minute)
);
CREATE TABLE metrics (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  metric_name TEXT NOT NULL,
  metric_value REAL NOT NULL,
  PRIMARY KEY(run_id, metric_name)
);
~~~

Enable foreign keys, WAL, busy timeout, and synchronous NORMAL. One transaction contains the whole tick. Store ISO UTC wall-clock metadata separately from simulated time.

- [ ] **Step 5: Implement exact artifact layout**

create_run builds runs/RUN_ID through a temporary sibling and writes run.json and inputs before exposing the directory. Write daily checkpoints. Keep results.sqlite3, events.jsonl, metrics.json, and provider-usage.json beneath that directory. Path traversal tests must pass on all platforms.

- [ ] **Step 6: Verify and commit**

~~~bash
uv run pytest tests/contract/test_event_sink.py \
  tests/contract/test_run_store.py \
  tests/integration/test_sqlite_store.py \
  tests/integration/test_artifact_layout.py -v
git add src/adlife/core/ports src/adlife/adapters/output \
  src/adlife/adapters/storage tests/contract tests/integration
git commit -m "feat(storage): add replayable event artifacts"
~~~

### Task 12: Orchestrate complete two-phase runs

**Files:**
- Create: src/adlife/core/simulation/runner.py
- Modify: src/adlife/core/simulation/engine.py
- Create: tests/integration/test_rule_run.py
- Create: tests/integration/test_mock_run.py
- Create: tests/golden/test_repeatable_run.py
- Create: tests/property/test_event_causality.py

**Interfaces:**
- Produces: SimulationRunner.run(scenario, seed, provider, store, sinks) -> Awaitable[SimulationResult]
- Produces: SimulationRunner.resume(stored_run, provider, store, sinks) -> Awaitable[SimulationResult]
- Consumes: AdLifeModel.plan_tick and commit_tick
- Consumes: CognitionService, RunStore, and EventSink

- [ ] **Step 1: Write end-to-end engine tests**

~~~python
@pytest.mark.asyncio
async def test_three_agents_complete_one_rule_day(small_scenario, tmp_path) -> None:
    result = await run_scenario(
        small_scenario.with_days(1).with_population_size(3),
        seed=42,
        mode="rules",
        output_root=tmp_path,
    )
    assert result.status == "completed"
    assert result.final_minute == 1440
    assert result.event_count > 0


@pytest.mark.asyncio
async def test_rule_run_is_byte_repeatable(small_scenario, tmp_path) -> None:
    first = await run_normalized(small_scenario, seed=42, root=tmp_path / "a")
    second = await run_normalized(small_scenario, seed=42, root=tmp_path / "b")
    assert first.events_jsonl == second.events_jsonl
    assert first.metrics_json == second.metrics_json
~~~

Add a hybrid mock run, provider-timeout fallback, interruption checkpoint, resume, no-campaign control, and causal-chain completeness.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/integration/test_rule_run.py \
  tests/integration/test_mock_run.py \
  tests/golden/test_repeatable_run.py \
  tests/property/test_event_causality.py -v
~~~

- [ ] **Step 3: Implement the runner loop**

~~~python
async def run(
    self,
    scenario: Scenario,
    seed: int,
    provider: CognitionProvider,
    store: RunStore,
    sinks: Sequence[EventSink],
) -> SimulationResult:
    model = AdLifeModel(scenario=scenario, seed=seed)
    manifest = self.manifest_factory.create(scenario, seed, provider)
    store.create_run(manifest)
    self._publish(store, sinks, model.start_events(manifest.run_id))

    while not model.clock.finished:
        plan = model.plan_tick()
        requests = tuple(sorted(plan.cognition_requests, key=lambda item: item.request_id))
        cognition = await self.cognition_service.evaluate_many(requests, provider)
        outcome = model.commit_tick(plan, cognition)
        store.append_events(outcome.events)
        for sink in sinks:
            sink.append_many(manifest.run_id, outcome.events)
        if outcome.ends_simulated_day:
            store.save_checkpoint(model.checkpoint())
        model.clock.advance()

    result = model.complete()
    store.complete_run(result)
    return result
~~~

Ensure clock advancement and daily checkpoint ordering are covered by tests. On KeyboardInterrupt, commit the last completed tick, write an interrupted status, and return exit code 130 through the CLI. On an internal defect, append run.failed if storage remains available and preserve the causal exception class without secrets.

- [ ] **Step 4: Enforce stable commit semantics**

State transition order is:

1. movement;
2. advertising eligibility;
3. attention;
4. cognition resolution;
5. rule-bounded state change;
6. memory;
7. social propagation;
8. purchase proxy;
9. daily reflection.

Sort within each stage by agent_id, campaign_id, and request_id. All planning reads the same pre-tick snapshot. No outcome may depend on Python dictionary iteration.

- [ ] **Step 5: Verify complete simulations**

~~~bash
uv run pytest tests/integration/test_rule_run.py \
  tests/integration/test_mock_run.py \
  tests/golden/test_repeatable_run.py \
  tests/property/test_event_causality.py -v
uv run pytest tests -q
~~~

- [ ] **Step 6: Commit**

~~~bash
git add src/adlife/core/simulation/engine.py \
  src/adlife/core/simulation/runner.py tests
git commit -m "feat(core): orchestrate reproducible society runs"
~~~

# Phase 3 — Experiments, CLI, TUI, and Reports

### Task 13: Calculate metrics and run paired experiments

**Files:**
- Create: src/adlife/core/experiments/metrics.py
- Create: src/adlife/core/experiments/design.py
- Create: src/adlife/core/experiments/comparison.py
- Create: src/adlife/core/experiments/sensitivity.py
- Create: tests/unit/experiments/test_metrics.py
- Create: tests/integration/test_paired_experiment.py
- Create: tests/integration/test_sensitivity.py

**Interfaces:**
- Produces: MetricsCalculator.calculate(events) -> RunMetrics
- Produces: ExperimentDesign
- Produces: ExperimentRunner.compare(a, b, seeds) -> Awaitable[ComparisonResult]
- Produces: SensitivityRunner.run(scenario, parameters, deltas, seeds)

- [ ] **Step 1: Write metric and experimental-control tests**

~~~python
def test_no_campaign_control_has_zero_campaign_metrics(no_campaign_events) -> None:
    metrics = MetricsCalculator().calculate(no_campaign_events)
    assert metrics.reach == 0
    assert metrics.impressions == 0
    assert metrics.notice_rate == 0
    assert metrics.word_of_mouth_reach == 0


@pytest.mark.asyncio
async def test_identical_a_a_comparison_has_zero_difference(
    rule_experiment_runner, valid_scenario
) -> None:
    result = await rule_experiment_runner.compare(
        valid_scenario,
        valid_scenario,
        seeds=tuple(range(20)),
    )
    assert result.metrics["recall"].mean_paired_difference == 0
    assert result.metrics["sentiment"].mean_paired_difference == 0
~~~

Add tests that reject mismatched populations, seeds, durations, engine versions, and scenario changes outside the declared treatment variable.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/unit/experiments/test_metrics.py \
  tests/integration/test_paired_experiment.py \
  tests/integration/test_sensitivity.py -v
~~~

- [ ] **Step 3: Implement event-derived metrics**

Required metrics:

- eligible opportunities;
- impressions;
- unique reach;
- frequency;
- noticed count and notice rate;
- mean attitude delta;
- mean delayed recall;
- mean advertising fatigue;
- direct and indirect awareness;
- word-of-mouth reach;
- purchase-intention delta;
- high-intention count;
- purchase-proxy count;
- cognition requests, cache hits, failures, and fallbacks.

MetricsCalculator is a pure event fold. It does not read current agent state or SQLite directly. Every metric includes numerator, denominator, value, and source event types.

- [ ] **Step 4: Implement paired comparison and intervals**

For every seed, clone the same initial population, graph, state, and keyed random streams. Vary exactly one declared treatment. Calculate paired differences by seed. Report mean, standard deviation, median, bootstrap 95 percent interval with a fixed bootstrap seed, standardized effect size, and fraction of seeds agreeing with the mean direction.

Declare direction stable only when at least 80 percent of seeds agree. Otherwise label it unstable. Use 50 seeds for the full academic experiment and 20 for quick CI.

- [ ] **Step 5: Implement sensitivity analysis**

Perturb attention, persuasion, memory decay, homophily, and word-of-mouth parameters by -20, -10, +10, and +20 percent. Record whether campaign ranking flips. Keep parameter sets in the run manifest.

- [ ] **Step 6: Verify**

~~~bash
uv run pytest tests/unit/experiments/test_metrics.py \
  tests/integration/test_paired_experiment.py \
  tests/integration/test_sensitivity.py -v
uv run mypy src/adlife/core/experiments
~~~

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/core/experiments tests/unit/experiments tests/integration
git commit -m "feat(experiments): add paired campaign evaluation"
~~~

### Task 14: Build the complete Typer command tree and machine-output contract

**Files:**
- Modify: src/adlife/cli/app.py
- Create: src/adlife/cli/commands/init.py
- Create: src/adlife/cli/commands/validate.py
- Create: src/adlife/cli/commands/population.py
- Create: src/adlife/cli/commands/run.py
- Create: src/adlife/cli/commands/replay.py
- Create: src/adlife/cli/commands/compare.py
- Create: src/adlife/cli/commands/doctor.py
- Create: src/adlife/resources/demo/adlife.yaml
- Create: src/adlife/resources/demo/population.yaml
- Create: src/adlife/resources/demo/campaigns/phone.yaml
- Create: src/adlife/resources/demo/campaigns/billboard.yaml
- Create: tests/cli/test_init.py
- Create: tests/cli/test_validate.py
- Create: tests/cli/test_run.py
- Create: tests/cli/test_doctor.py
- Create: tests/cli/test_json_output.py

**Interfaces:**
- Produces commands: init, validate, population generate, campaign import, run, replay, compare, doctor
- Produces global: --format human|json|jsonl and --no-color
- Consumes core services through explicit command factories

- [ ] **Step 1: Write CLI behavior tests**

~~~python
def test_init_creates_expected_project(runner, tmp_path) -> None:
    result = runner.invoke(app, ["init", "study", "--parent", str(tmp_path)])
    assert result.exit_code == 0
    root = tmp_path / "study"
    assert (root / "adlife.yaml").is_file()
    assert (root / "campaigns" / "demo-phone.yaml").is_file()
    assert (root / "runs").is_dir()


def test_json_mode_keeps_stdout_machine_readable(runner, valid_project) -> None:
    result = runner.invoke(
        app,
        ["--format", "json", "validate", str(valid_project)],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["valid"] is True
    assert "warning" not in result.stdout.lower()


def test_live_mode_refuses_non_tty_without_fallback(runner, valid_project) -> None:
    result = runner.invoke(
        app,
        ["run", str(valid_project), "--live", "--no-headless-fallback"],
    )
    assert result.exit_code == ExitCode.INPUT_ERROR
~~~

Add tests for every documented exit code, missing config, invalid YAML, missing API key, Ctrl-C, NO_COLOR, TERM=dumb, Persian paths, and diagnostics going to stderr in JSON mode.

- [ ] **Step 2: Run and confirm command failures**

~~~bash
uv run pytest tests/cli -v
~~~

- [ ] **Step 3: Register a stable command tree**

~~~python
from typing import Literal

import typer

from adlife.cli.commands import (
    campaign,
    compare,
    doctor,
    init,
    population,
    replay,
    run,
    validate,
)

app = typer.Typer(name="adlife", help="AdLife Lab synthetic society simulator.")
app.add_typer(population.app, name="population")
app.add_typer(campaign.app, name="campaign")
app.command("init")(init.command)
app.command("validate")(validate.command)
app.command("run")(run.command)
app.command("replay")(replay.command)
app.command("compare")(compare.command)
app.command("doctor")(doctor.command)


@app.callback()
def root(
    ctx: typer.Context,
    output_format: Literal["human", "json", "jsonl"] = typer.Option("human", "--format"),
    no_color: bool = typer.Option(False, "--no-color"),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj.update(output_format=output_format, no_color=no_color)
~~~

Retain eager --version behavior from Task 1. Lazy-import Textual, pandas, SciPy, and Plotly inside commands so help and version remain fast.

The run command accepts --campaign with a project-relative YAML path, --run-id with the pattern [a-z0-9][a-z0-9-]{0,39}, --mode rules|hybrid|replay, --days, --population-size, --seed, --live, --headless, and --no-headless-fallback. It refuses an existing run identifier rather than overwriting artifacts.

- [ ] **Step 4: Implement project initialization**

Copy package resources with importlib.resources, never with paths relative to __file__. Refuse to overwrite a non-empty target. Create assets, campaigns, runs, and reports. The generated .gitignore excludes .env, runs, reports, and local assets while keeping YAML inputs.

- [ ] **Step 5: Implement doctor --offline**

Offline checks:

- package version and Python version;
- package resources present;
- config directory writable;
- current project schema if a path is supplied;
- SQLite JSON1 availability;
- UTF-8 terminal capability;
- rule and mock provider availability;
- no network access.

Without --offline, additionally probe the configured local or remote provider with a two-second health timeout. Redact base URL user info and authorization values.

- [ ] **Step 6: Implement command exit mapping**

Catch only known domain exceptions at the CLI boundary. Convert validation to 2, provider configuration to 3, corrupted artifacts to 4, KeyboardInterrupt to 130, and unexpected exceptions to 1. In normal mode show a concise message plus --verbose guidance. In JSON mode output one error object and place trace diagnostics on stderr.

- [ ] **Step 7: Verify all commands**

~~~bash
uv run pytest tests/cli -v
uv run adlife --help
uv run adlife doctor --offline
~~~

- [ ] **Step 8: Commit**

~~~bash
git add src/adlife/cli tests/cli
git commit -m "feat(cli): expose complete simulation workflow"
~~~

### Task 15: Add the live Textual terminal interface as a read-only adapter

**Files:**
- Create: src/adlife/tui/event_bus.py
- Create: src/adlife/tui/app.py
- Create: src/adlife/tui/styles.tcss
- Create: src/adlife/tui/widgets/agent_table.py
- Create: src/adlife/tui/widgets/agent_detail.py
- Create: src/adlife/tui/widgets/event_stream.py
- Create: src/adlife/tui/widgets/metrics_strip.py
- Create: tests/tui/test_event_bus.py
- Create: tests/tui/test_app.py
- Create: tests/integration/test_live_headless_equivalence.py

**Interfaces:**
- Produces: TuiEventBus.publish(events), subscribe()
- Produces: AdLifeTui(run_controller, event_bus)
- Consumes committed DomainEvent objects only

- [ ] **Step 1: Write TUI smoke and isolation tests**

~~~python
@pytest.mark.asyncio
async def test_tui_starts_and_shows_agents(tui_app) -> None:
    async with tui_app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert tui_app.query_one("#agent-table").row_count == 3
        assert "Day 1" in tui_app.query_one("#clock").renderable.plain


@pytest.mark.asyncio
async def test_pause_changes_rendering_not_engine_result(scenario, tmp_path) -> None:
    headless = await run_headless(scenario, tmp_path / "headless")
    live = await run_with_test_tui(scenario, tmp_path / "live", pause_at_tick=10)
    assert headless.normalized_events == live.normalized_events
~~~

Add tests for selecting an agent, filtering events, stepping one tick, changing display speed, bounded 100-event stream, resize to 80 by 24, and q quitting without corrupting artifacts.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/tui tests/integration/test_live_headless_equivalence.py -v
~~~

- [ ] **Step 3: Implement the bounded event bus**

The bus receives committed events after persistence succeeds. It maintains a deque of 100 display events and immutable latest projections. Subscriber exceptions are logged to stderr and cannot affect the engine.

- [ ] **Step 4: Implement the four-panel TUI**

Layout:

~~~text
Header: run, day/time, provider, seed, speed
Left 60%: agent table
Right 40% top: selected agent details and memories
Right 40% bottom: live event stream
Bottom: reach, notice, sentiment, recall, shares, high intent
Footer: Space pause | N step | +/- speed | F filter | Q quit
~~~

Use CSS resources loaded through importlib.resources. Do not display unsupported emoji as the only status indicator. Respect NO_COLOR. Use text labels and accessible contrast.

- [ ] **Step 5: Connect run --live without importing TUI in core**

The CLI constructs a run controller and starts Textual only when stdin and stdout are terminals and TERM is not dumb. Otherwise it selects PlainEventSink unless --no-headless-fallback was given. The engine receives only EventSink and control interfaces.

- [ ] **Step 6: Verify**

~~~bash
uv run pytest tests/tui tests/integration/test_live_headless_equivalence.py -v
uv run adlife init tui-study
uv run adlife run tui-study --campaign campaigns/demo-phone.yaml \
  --run-id tui-check --mode rules --days 1 --population-size 3 --live
~~~

Manually check 80x24 and 120x40 terminal sizes, then exit with q and confirm the run can be replayed.

- [ ] **Step 7: Commit**

~~~bash
git add src/adlife/tui src/adlife/cli/commands/run.py tests/tui \
  tests/integration/test_live_headless_equivalence.py
git commit -m "feat(tui): add live synthetic society dashboard"
~~~

### Task 16: Generate self-contained reports, comparison output, and the offline demo

**Files:**
- Create: src/adlife/reporting/resources.py
- Create: src/adlife/reporting/html.py
- Create: src/adlife/reporting/templates/report.html.j2
- Create: src/adlife/reporting/static/report.css
- Create: src/adlife/cli/commands/report.py
- Create: src/adlife/cli/commands/demo.py
- Modify: src/adlife/cli/app.py
- Create: tests/unit/reporting/test_html.py
- Create: tests/integration/test_demo.py
- Create: tests/packaging/test_resources.py

**Interfaces:**
- Produces: render_report(stored_run, destination: Path) -> Path
- Produces: render_comparison(comparison, destination: Path) -> Path
- Produces: create_demo_project(destination: Path) -> Path
- Produces commands: report and demo

- [ ] **Step 1: Write report safety and completeness tests**

~~~python
def test_report_contains_disclosure_and_reproducibility(stored_run, tmp_path) -> None:
    path = render_report(stored_run, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")
    assert "synthetic and exploratory" in text.lower()
    assert stored_run.manifest.scenario_hash in text
    assert f"Seed: {stored_run.manifest.seed}" in text


def test_report_escapes_campaign_text(stored_run_with_script_tag, tmp_path) -> None:
    path = render_report(stored_run_with_script_tag, tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")
    assert "<script>alert(" not in text
    assert "&lt;script&gt;" in text
~~~

Add tests for all required metrics, no remote script or font requests, Persian UTF-8, selected diaries labeled fictional, provider usage, fallbacks, and limitations.

- [ ] **Step 2: Run and confirm failure**

~~~bash
uv run pytest tests/unit/reporting/test_html.py \
  tests/integration/test_demo.py \
  tests/packaging/test_resources.py -v
~~~

- [ ] **Step 3: Implement a self-contained report**

Use Jinja2 autoescape. Inline CSS and Plotly JavaScript once inside the generated file. Serialize chart inputs through safe JSON. Render:

1. disclosure banner;
2. executive summary;
3. configuration and reproducibility;
4. population distribution;
5. reach and frequency;
6. notice and channel comparison;
7. sentiment, recall, and intention distributions;
8. word-of-mouth summary;
9. selected fictional diaries;
10. provider usage and fallbacks;
11. formulas, assumptions, and limitations.

Do not call a model to write the report. Narrative text comes from deterministic templates and computed metrics.

- [ ] **Step 4: Implement the credential-free demo**

adlife demo creates a temporary copy of package resources, runs 20 fictional agents for three days with the mock provider, and opens the TUI when a terminal is available. --headless prints a summary and artifact directory. The demo must complete with network disabled.

Register report and demo on the root Typer application only after these adapters exist. Import pandas, Plotly, Jinja2, and Textual lazily inside the command functions.

- [ ] **Step 5: Verify wheel resource inclusion**

~~~bash
uv build --no-sources
uv run pytest tests/packaging/test_resources.py -v
uv run adlife demo --headless
~~~

Inspect the report in a browser and verify that disconnecting the network does not break charts or styling.

- [ ] **Step 6: Commit**

~~~bash
git add src/adlife/reporting src/adlife/resources tests
git commit -m "feat(reporting): add investor-ready offline reports"
~~~

# Phase 4 — Academic Documentation, Packaging, and Release Engineering

### Task 17: Document methodology, ethics, reproducibility, and investor positioning

**Files:**
- Create: README.md
- Create: docs/architecture.md
- Create: docs/quickstart.md
- Create: docs/cli-reference.md
- Create: docs/reproducibility.md
- Create: docs/investor-demo.md
- Create: docs/methodology/odd-protocol.md
- Create: docs/methodology/model-card.md
- Create: docs/methodology/experiment-protocol.md
- Create: docs/methodology/limitations.md
- Create: CITATION.cff
- Create: CHANGELOG.md
- Create: SECURITY.md
- Create: CONTRIBUTING.md
- Create: CODE_OF_CONDUCT.md
- Create: LICENSE
- Create: tests/packaging/test_documentation.py

**Interfaces:**
- Produces a complete public explanation of what the model does and does not claim
- Produces a citable university artifact

- [ ] **Step 1: Write documentation-presence tests**

~~~python
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_readme_contains_required_disclosures() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "synthetic and exploratory" in text
    assert "not a representative survey" in text
    assert "adlife demo" in text


def test_methodology_documents_exist() -> None:
    required = [
        "odd-protocol.md",
        "model-card.md",
        "experiment-protocol.md",
        "limitations.md",
    ]
    for name in required:
        assert (ROOT / "docs" / "methodology" / name).stat().st_size > 1000
~~~

- [ ] **Step 2: Run and confirm missing documentation**

~~~bash
uv run pytest tests/packaging/test_documentation.py -v
~~~

- [ ] **Step 3: Write the public README**

Required order:

1. product statement and terminal demonstration command;
2. animated terminal recording link only after an owner-created recording exists;
3. scientific disclosure;
4. features;
5. quick start with uv;
6. local Ollama and remote compatible provider configuration;
7. sample campaign commands;
8. output and screenshots section using text descriptions until owner-supplied media exists;
9. architecture;
10. research method;
11. reproducibility;
12. limitations;
13. roadmap toward the separate SaaS;
14. contributing, security, citation, and license.

Do not state accuracy percentages, real consumer validation, customer adoption, or investment claims unless supported by repository evidence.

Before a GitHub origin exists, the installation section contains this machine-editable block:

~~~markdown
<!-- adlife-install:start -->
uv tool install adlife-sim
<!-- adlife-install:end -->
~~~

- [ ] **Step 4: Write the ODD protocol and model card**

The ODD document covers purpose, entities, state variables, scales, process overview and scheduling, design concepts, initialization, input data, and submodels. Record all formulas from the specification.

The model card covers intended use, excluded use, synthetic population construction, LLM role, data, metrics, ethical risks, stereotype risks, prompt-injection boundary, reproducibility limits, and external-validity status.

docs/investor-demo.md provides a five-minute defense and investor demonstration: problem statement, live offline demo, phone-versus-billboard comparison, causal event replay, methodology disclosure, reusable core boundary, and a final slide-free explanation of how the local engine can later sit behind the separate commercial SaaS.

- [ ] **Step 5: Write the experiment protocol**

Pre-register within the repository:

- primary metrics;
- no-campaign control;
- A/A control;
- paired A/B common random numbers;
- 50 full-study seeds;
- 80 percent directional-stability rule;
- sensitivity ranges;
- channel-opportunity confounding warning;
- optional blinded human ranking comparison;
- exact command that rebuilds tables and charts.

- [ ] **Step 6: Add project governance**

Use the complete AGPL-3.0-only license text. SECURITY.md defines private reporting without inventing an email address: ask reporters to use GitHub's private vulnerability reporting feature when enabled. CONTRIBUTING.md requires fictional data, tests, disclosure preservation, and a Developer Certificate of Origin sign-off. CITATION.cff identifies title, version 0.1.0, repository type software, release date, and the repository owner's name only after the owner edits it before publication.

- [ ] **Step 7: Verify and commit**

~~~bash
uv run pytest tests/packaging/test_documentation.py -v
rg -n "representative|accuracy|real people|prediction" README.md docs
git add README.md docs CITATION.cff CHANGELOG.md SECURITY.md \
  CONTRIBUTING.md CODE_OF_CONDUCT.md LICENSE tests/packaging/test_documentation.py
git commit -m "docs: document model methodology and limitations"
~~~

### Task 18: Build wheels, frozen binaries, installers, CI, and releases

**Files:**
- Create: packaging/adlife.spec
- Create: scripts/smoke_release.py
- Create: scripts/install.sh
- Create: scripts/install.ps1
- Create: scripts/configure_repository.py
- Create: tests/packaging/test_wheel.py
- Create: tests/packaging/test_installer.py
- Create: .github/workflows/ci.yml
- Create: .github/workflows/release.yml
- Create: .github/dependabot.yml
- Create: .github/ISSUE_TEMPLATE/bug_report.yml
- Create: .github/ISSUE_TEMPLATE/feature_request.yml
- Create: .github/pull_request_template.md
- Create: docs/installation.md
- Create: docs/releasing.md

**Interfaces:**
- Produces an installable wheel and source distribution
- Produces optional native executable per supported OS
- Produces adlife doctor --offline and release smoke gates
- Produces one-line Bash and PowerShell installation after PyPI publication

- [ ] **Step 1: Write clean-wheel smoke tests**

scripts/smoke_release.py creates a temporary virtual environment, installs the supplied wheel, changes to a directory outside the source checkout, and runs:

~~~text
adlife --version
adlife doctor --offline
adlife init smoke-study
adlife run smoke-study --campaign campaigns/demo-phone.yaml --run-id smoke \
  --mode rules --days 1 --population-size 2 --headless
adlife report smoke-study/runs/smoke
~~~

It validates JSON output and confirms an HTML report exists. tests/packaging/test_wheel.py builds a wheel, invokes the script, and asserts package YAML, Jinja, CSS, and Textual resources are present.

- [ ] **Step 2: Run the wheel test and confirm failure**

~~~bash
uv run pytest tests/packaging/test_wheel.py -v
~~~

- [ ] **Step 3: Make wheel installation the canonical distribution**

~~~bash
uv build --no-sources
uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl
~~~

Canonical public installation after publication:

~~~bash
uv tool install adlife-sim
adlife doctor --offline
~~~

Development installation from the local repository:

~~~bash
uv tool install .
adlife --version
~~~

- [ ] **Step 4: Create the PyInstaller specification**

Use one-directory output during development and one-file output only for release. Collect package metadata, Textual CSS, routine YAML, demo YAML, report template, report CSS, and Plotly resources. Build natively on each target OS; never cross-compile.

Stable release asset names:

~~~text
adlife-vX.Y.Z-linux-x86_64.tar.gz
adlife-vX.Y.Z-macos-x86_64.tar.gz
adlife-vX.Y.Z-macos-aarch64.tar.gz
adlife-vX.Y.Z-windows-x86_64.zip
SHA256SUMS
~~~

The frozen application contains no model weights and works in rules and mock modes without Ollama.

- [ ] **Step 5: Implement the shell installer**

scripts/install.sh must use set -eu, require Linux or macOS, detect x86_64 or arm64, avoid sudo, avoid editing shell startup files, and default to:

~~~bash
#!/bin/sh
set -eu

PACKAGE="adlife-sim"
VERSION="$(printenv ADLIFE_VERSION 2>/dev/null || true)"

case "$(uname -s)" in
  Linux|Darwin) ;;
  *)
    echo "AdLife supports this installer on Linux and macOS." >&2
    exit 2
    ;;
esac

case "$(uname -m)" in
  x86_64|amd64|arm64|aarch64) ;;
  *)
    echo "Unsupported processor architecture." >&2
    exit 2
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/ and rerun." >&2
  exit 2
fi

SPEC="$PACKAGE"
if [ -n "$VERSION" ]; then
  SPEC="$PACKAGE==$VERSION"
fi

uv tool install --upgrade "$SPEC"
command -v adlife >/dev/null 2>&1
adlife --version
adlife doctor --offline
~~~

If uv is absent, print the official uv installation command and require the user to rerun the AdLife installer after uv is available. Do not silently execute a second remote installer. ADLIFE_VERSION selects a package version and ADLIFE_INSTALL_DIR is reserved for verified binary-release mode.

The later binary-download branch must use mktemp -d plus a cleanup trap, verify the selected archive against SHA256SUMS before extraction, and atomically replace only the exact adlife executable inside ADLIFE_INSTALL_DIR.

scripts/install.ps1 performs the equivalent checks and uses user-writable paths. Both installers fail clearly when the package has not yet been published.

- [ ] **Step 6: Test installers without modifying the real user environment**

Use temporary HOME, XDG_BIN_HOME, and PATH values plus a fake uv executable that records arguments. Assert:

- exact distribution name;
- version pin behavior;
- no sudo;
- no shell-profile mutation;
- failure when uv is absent;
- health check after installation;
- cleanup after interruption.

~~~bash
uv run pytest tests/packaging/test_installer.py -v
shellcheck scripts/install.sh
~~~

- [ ] **Step 7: Implement repository URL configuration**

scripts/configure_repository.py reads git remote get-url origin, accepts SSH and HTTPS GitHub URLs, validates owner/repository characters, and updates only a marked installation block in README. The generated README command has this shell structure, where the URL value is constructed from the validated origin:

~~~bash
curl -fsSL "$ADLIFE_INSTALLER_URL" | sh
~~~

configure_repository.py writes the concrete tag-pinned URL into README; it never leaves a variable or example account in the public installation block. If origin is missing or not GitHub, exit without changing files.

Use this parsing and replacement boundary:

~~~python
START = "<!-- adlife-install:start -->"
END = "<!-- adlife-install:end -->"


def parse_github_slug(remote: str) -> str:
    remote = remote.strip()
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    elif remote.startswith("https://github.com/"):
        slug = remote.removeprefix("https://github.com/")
    else:
        raise ValueError("origin is not a GitHub SSH or HTTPS URL")
    slug = slug.removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug) is None:
        raise ValueError("invalid GitHub owner/repository")
    return slug


def installation_block(slug: str, version: str) -> str:
    url = f"https://raw.githubusercontent.com/{slug}/v{version}/scripts/install.sh"
    return f"{START}\ncurl -fsSL {url} | sh\n{END}"
~~~

- [ ] **Step 8: Add CI**

CI triggers on pull requests and pushes to main, cancels superseded runs, and contains:

- Linux Python 3.11, 3.12, and 3.13 test matrix;
- one Windows and one macOS Python 3.12 job;
- uv sync --locked --all-groups;
- Ruff format and lint;
- mypy strict;
- pytest with branch coverage at least 85 percent;
- Textual run_test smoke;
- no-network deterministic integration run;
- uv build --no-sources;
- clean-wheel installation smoke;
- ShellCheck and PowerShell analyzer;
- dependency and secret scanning.

Pin third-party actions to full commit SHAs resolved from their official repositories during implementation and annotate each SHA with its release tag. Dependabot tracks both GitHub Actions and uv dependencies. Fork pull requests receive no repository secrets.

- [ ] **Step 9: Add the protected release workflow**

Trigger only on signed annotated tags matching vX.Y.Z. Verify the tag equals project.version. Build natively on Linux x86_64, macOS x86_64, macOS arm64, and Windows x86_64. Smoke-test the exact frozen artifact on its builder. Gather artifacts, create SHA256SUMS, generate an SBOM and third-party license report, and create a draft GitHub release.

PyPI publication uses Trusted Publishing with id-token write and a protected pypi environment. Store no PyPI token or personal access token. Publish the GitHub release only after all artifacts and PyPI publication succeed.

- [ ] **Step 10: Verify packaging locally**

~~~bash
uv run pytest tests/packaging -v
uv build --no-sources
uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl
uv run pyinstaller packaging/adlife.spec --clean --noconfirm
dist/adlife/adlife --version
dist/adlife/adlife doctor --offline
~~~

On Windows, invoke the executable with the .exe suffix. Record any unavailable native build as a CI responsibility, not as locally verified.

- [ ] **Step 11: Commit**

~~~bash
git add packaging scripts tests/packaging .github docs/installation.md docs/releasing.md
git commit -m "build: add verified package and release infrastructure"
~~~

### Task 19: Run final scientific, performance, security, and Git handoff gates

**Files:**
- Create: tests/golden/scenarios/rule-small/
- Create: tests/golden/scenarios/mock-small/
- Create: tests/property/test_metamorphic.py
- Create: tests/integration/test_maximum_run.py
- Create: tests/integration/test_security_boundaries.py
- Create: CONNECT_GITHUB.md
- Modify: CHANGELOG.md

**Interfaces:**
- Produces a release-candidate local repository with auditable evidence
- Leaves the repository with no remote and no uncommitted files

- [ ] **Step 1: Add golden and metamorphic tests**

Required assertions:

- rules mode with identical seed produces byte-identical normalized events and metrics;
- mock mode with identical seed produces the same result;
- replay reproduces the validated hybrid result;
- a different seed changes at least one stochastic decision;
- permuting input agent order does not change aggregate metrics;
- reordering independent intents does not change committed events;
- every campaign state delta traces to an exposure or social causal chain;
- no campaign means zero attributed campaign delta;
- disabling social influence means zero indirect awareness;
- counterfactual display-name changes do not alter decisions.

- [ ] **Step 2: Add maximum-run performance test**

Run 30 agents for seven days in rules mode. Target under 10 seconds and 250 MB peak RSS on a four-core development laptop. CI marks 20 seconds as the hard regression threshold to reduce runner noise. The test records engine steps, event count, elapsed time, and peak memory.

- [ ] **Step 3: Add security-boundary tests**

Test path traversal, YAML object tags, HTML injection, prompt injection, authorization redaction, accidental API-key persistence, symlink escape, corrupt SQLite, corrupt JSONL, malicious campaign filenames, and network prohibition in demo mode. Use generated fake secrets and assert they are absent from every run artifact.

- [ ] **Step 4: Run the complete release candidate suite**

~~~bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov=adlife --cov-branch --cov-report=term-missing
uv build --no-sources
uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl
uv run adlife doctor --offline
uv run adlife --format json demo --headless
git diff --check
~~~

Expected: every command succeeds, coverage is at least 85 percent, and the demo makes no network request.

- [ ] **Step 5: Write exact GitHub connection instructions**

CONNECT_GITHUB.md explains that origin is the correct name for the owner's canonical repository; upstream is reserved for the parent of a fork. The user creates an empty GitHub repository without a generated README, license, or ignore file, then runs:

~~~bash
read -r -p "Paste the empty GitHub repository URL: " ADLIFE_REMOTE_URL
git remote add origin "$ADLIFE_REMOTE_URL"
git remote -v
uv run python scripts/configure_repository.py
git add README.md
git commit -m "docs: add canonical repository install URL"
git push -u origin main
~~~

For an authenticated GitHub CLI, document:

~~~bash
read -r -p "Enter owner/repository: " ADLIFE_REPOSITORY
gh repo create "$ADLIFE_REPOSITORY" --public --source=. --remote=origin --push
uv run python scripts/configure_repository.py
git add README.md
git commit -m "docs: add canonical repository install URL"
git push
~~~

The instructions explicitly prohibit force-pushing to reconcile a non-empty remote.

- [ ] **Step 6: Record release-candidate status and commit**

Add a 0.1.0 unreleased entry to CHANGELOG.md listing the deterministic engine, campaigns, hybrid cognition, TUI, reports, tests, and packaging. Record the full verification command without claiming a public release.

~~~bash
git add tests CONNECT_GITHUB.md CHANGELOG.md
git commit -m "test: certify adlife release candidate"
~~~

- [ ] **Step 7: Perform final repository inspection**

~~~bash
git status --short
git remote -v
git log --oneline --decorate --reverse
git ls-files | sort
~~~

Expected:

- git status --short prints nothing;
- git remote -v prints nothing;
- the log contains one focused commit for every task;
- no .env, API key, generated run, report, database, binary, or build directory is tracked.

## Final Acceptance Demonstration

Run this sequence from a clean shell after local implementation:

~~~bash
uv sync --locked --all-groups
uv run adlife doctor --offline
uv run adlife demo --headless
uv run adlife init university-study
uv run adlife population generate university-study --count 30 --locale fa-IR --seed 42
uv run adlife run university-study --campaign campaigns/demo-phone.yaml \
  --run-id phone --mode rules --days 7 --headless
uv run adlife run university-study --campaign campaigns/demo-billboard.yaml \
  --run-id billboard --mode rules --days 7 --headless
uv run adlife replay university-study/runs/phone
uv run adlife report university-study/runs/phone --open
uv run adlife compare university-study/runs/phone university-study/runs/billboard
~~~

For the live defense, run:

~~~bash
uv run adlife demo --live
~~~

The repository is accepted only when the offline demo, complete test suite, clean-wheel smoke, report generation, and clean Git checks all pass. Live LLM availability is an optional demonstration enhancement, not a completion dependency.

## Commit Sequence

~~~text
chore: bootstrap typed adlife package
feat: add safe project configuration
feat(core): define versioned simulation contracts
feat(core): add deterministic simulation primitives
feat(core): generate fictional populations and routines
feat(core): add deterministic daily movement
feat(core): model mobile and billboard exposure
feat(core): add bounded consumer response dynamics
feat(cognition): add deterministic provider contracts and replay
feat(cognition): add local and remote compatible provider
feat(storage): add replayable event artifacts
feat(core): orchestrate reproducible society runs
feat(experiments): add paired campaign evaluation
feat(cli): expose complete simulation workflow
feat(tui): add live synthetic society dashboard
feat(reporting): add investor-ready offline reports
docs: document model methodology and limitations
build: add verified package and release infrastructure
test: certify adlife release candidate
~~~

## Execution Handoff

The implementation agent must execute tasks in numeric order and treat every commit as a review gate. If a dependency API differs from the code shape shown here, preserve the locked interfaces and adapt only the integration layer. Record the resolved dependency version in uv.lock and add a regression test before proceeding.

No remote creation, authentication, push, PyPI publication, domain registration, or external release is authorized by this plan. Those actions occur only after the repository owner reviews the completed local history and explicitly requests them.
