# AdLife Lab CLI Simulator Design

## 1. Document purpose

This specification defines a standalone, open-source university project that can also act as a technical proof of concept for a future commercial synthetic-audience SaaS. The university project is deliberately local-first and terminal-first. It does not contain accounts, subscriptions, payment processing, multi-tenancy, or a hosted control plane.

The working product name is **AdLife Lab**. The Python distribution name is **adlife-sim**, the import package is **adlife**, the repository folder is **adlife**, and the command installed on the user's PATH is **adlife**. The distribution name must be checked on PyPI before the first public release, while the import and command names remain stable.

## 2. Product statement

AdLife Lab simulates a small fictional society of 1 to 30 synthetic consumers. Each agent follows a daily routine, moves among locations, maintains memories and relationships, encounters advertising through a mobile feed or highway billboard, discusses experiences with other agents, and changes recall, sentiment, and purchase intention.

The product must be impressive in a live university demonstration while remaining scientifically honest. Its output describes behavior inside a synthetic model; it must never be presented as a statistically representative prediction of real people.

## 3. Primary goals

1. Run a complete seven-day simulation with 30 agents on a normal laptop.
2. Show the simulation live in a polished terminal user interface.
3. Accept campaign definitions from YAML and optionally a local image.
4. Support deterministic rule-only and mock-LLM runs.
5. Support a local Ollama endpoint and a configurable OpenAI-compatible API.
6. Preserve every important event so a run can be replayed and audited.
7. Compare two campaign runs using the same population and seed.
8. Produce JSON, CSV, and self-contained HTML reports.
9. Install as a command-line tool with uv and later through a one-line shell installer.
10. Keep the simulation core independent enough to reuse in the future SaaS.

## 4. Explicit non-goals

- No browser application or HTTP API.
- No login, registration, permissions, teams, billing, or subscriptions.
- No real consumer database or scraping.
- No personal data about identifiable people.
- No claim that 20 or 30 agents represent Iran or any other country.
- No multi-agent framework that autonomously executes tools.
- No LLM call on every simulation tick.
- No distributed task queue, Redis, PostgreSQL, Docker Compose, or Kubernetes.
- No real advertising placement or integration with advertising networks.
- No automated publication to PyPI until the package name and ownership are confirmed by the repository owner.

## 5. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Mature simulation, data, CLI, and LLM ecosystem |
| Project manager | uv | Locking, isolated tools, repeatable developer setup |
| Agent-based model | Mesa | Academic agent-based modeling concepts and agent collection primitives |
| CLI | Typer | Typed commands, help generation, and shell completion |
| Terminal UI | Textual | Live interactive interface inside a terminal |
| Validation | Pydantic | Strict campaign, persona, state, event, and provider schemas |
| Configuration | YAML through PyYAML | Human-readable project and campaign files |
| Persistence | Python sqlite3 | Portable single-file database without a service |
| HTTP | httpx | OpenAI-compatible and Ollama calls |
| Social graph | NetworkX | Relationships and influence propagation |
| Analytics | pandas and scipy | Aggregation, repeat-run statistics, and confidence intervals |
| HTML report | Jinja2 and Plotly | Portable report with interactive charts |
| Testing | pytest, pytest-cov, Hypothesis | Unit, integration, property, and coverage tests |
| Quality | Ruff and mypy | Formatting, linting, and static type checks |
| Packaging | Hatchling through pyproject.toml | Standards-based wheel and source distribution |
| Binary releases | PyInstaller, release phase only | Optional per-platform executable |

Mesa is a dependency of the core package, but AdLife-specific state transitions remain behind project-owned interfaces. This prevents the future SaaS from depending on Mesa types at its API boundary.

## 6. Operating modes

### 6.1 Rule mode

Rule mode performs no network calls. It uses mathematical state transitions and seeded pseudo-random draws. The same inputs, package version, and seed must create byte-equivalent canonical event JSON after timestamps and generated run identifiers are normalized.

### 6.2 Mock provider

The mock provider exercises the complete cognition pipeline with deterministic fixture responses. It is used in automated tests, the default quick-start demonstration, and environments without a model.

### 6.3 Local provider

Local mode calls an OpenAI-compatible endpoint at http://127.0.0.1:11434/v1 by default. It is intended for Ollama. The model name is configuration, not hard-coded business logic.

### 6.4 Remote provider

Remote mode calls a user-configured OpenAI-compatible base URL. The API key is read from ADLIFE_API_KEY or from an OS prompt and is never written into project YAML, SQLite, logs, reports, or crash output.

### 6.5 Simulation cognition modes

- rules: transparent formulas only;
- hybrid: formulas plus a bounded mock, local, or remote provider;
- replay: previously validated cognition results loaded from the cache.

Exact reproducibility is claimed only for rules, mock, and replay modes. Hosted or local generative models may vary even with identical sampling settings.

## 7. Simulation limits and timing

- Population: integer from 1 through 30.
- Duration: integer from 1 through 7 simulated days.
- Tick duration: 15 simulated minutes.
- Default duration: 3 simulated days for the quick demo.
- Default population: 20 agents for the quick demo.
- Maximum cognitive events: 6 per agent per run.
- Maximum provider retries: 2 after the initial attempt.
- Provider timeout: 60 seconds.
- Event ordering key: simulated minute, event priority, agent identifier, event sequence.
- Randomness: one root random seed plus deterministic named child streams.

The engine may run faster than real time. Live presentation speed affects rendering only and must not affect simulation outcomes.

## 8. World model

The initial world contains these zones:

1. home-north
2. home-center
3. home-south
4. university
5. office
6. retail-center
7. cafe
8. highway-north
9. highway-center
10. online

An agent occupies exactly one zone and performs exactly one primary activity at a tick. Standard activities are sleep, breakfast, commute, study, work, shopping, leisure, socializing, phone-check, and reflection.

Routines are generated from five templates: student, office-worker, retail-worker, freelancer, and unemployed. Each generated agent receives bounded time jitter from its seeded random stream, so agents sharing a template do not behave identically.

## 9. Agent model

### 9.1 Immutable identity

- agent_id
- fictional display_name
- age between 18 and 65
- occupation template
- income_band: low, middle, or high
- household_type
- home_zone
- work_or_study_zone
- interests
- traits
- initial brand sentiment
- routine template

Every generated identity is explicitly fictional.

### 9.2 Traits

Each trait is a float from 0 through 1:

- price_sensitivity
- novelty_seeking
- social_susceptibility
- advertising_skepticism
- mobile_attention
- outdoor_attention
- brand_loyalty
- impulsivity

### 9.3 Mutable state

- simulated location and activity
- mood from -1 through 1
- fatigue from 0 through 1
- brand sentiment from -1 through 1
- ad recall strength from 0 through 1
- purchase intention from 0 through 1
- exposure count by campaign and channel
- last five episodic memories
- daily reflection
- available cognitive-event budget

### 9.4 Relationships

The population owns an undirected weighted social graph. An edge weight from 0 through 1 represents interaction likelihood and trust. Generation must ensure:

- every agent has at least one connection when population is greater than one;
- no agent has more than eight initial connections;
- the graph is connected for populations of three or more;
- edge generation is deterministic for a seed.

## 10. Campaign model

A campaign contains:

- campaign_id
- name
- product_name
- product_category
- message
- call_to_action
- price amount and ISO currency
- positive category reference price in the same currency
- target interests
- start and end simulated minute
- one or more placements
- optional asset path
- asset SHA-256
- extracted creative attributes

A placement contains a channel, zone, active time windows, frequency cap, and visibility score.

Supported channels are:

- mobile-feed: available while an agent performs phone-check in the online zone;
- highway-billboard: available during commute in a matching highway zone.

Image ingestion is optional. If a vision-capable provider is configured, it extracts a structured creative description once. Otherwise the campaign YAML description remains authoritative. Raw image bytes are never stored in SQLite.

## 11. Deterministic behavior model

### 11.1 Notice probability

For an eligible exposure:

    channel_attention =
        mobile_attention for mobile-feed
        outdoor_attention for highway-billboard

    interest_match = Jaccard(agent.interests, campaign.target_interests)
    fatigue_penalty = min(1, prior_exposures / placement.frequency_cap)
    raw_notice = -1.2
                 + 1.5 * channel_attention
                 + 1.0 * interest_match
                 + 0.8 * placement.visibility
                 - 0.7 * advertising_skepticism
                 - 0.5 * fatigue_penalty
    notice_probability = sigmoid(raw_notice)

A draw from the agent's named exposure random stream decides whether the agent notices the campaign.

### 11.2 Rule-only response

When an advertisement is noticed:

    value_match = 0.55 * interest_match
                  + 0.25 * novelty_seeking
                  + 0.20 * (1 - price_sensitivity)

    sentiment_delta = clamp(
        0.18 * value_match
        - 0.12 * advertising_skepticism
        - 0.06 * fatigue_penalty,
        -0.20,
        0.20
    )

    recall_delta = clamp(
        0.22 * channel_attention
        + 0.12 * novelty_seeking
        - 0.08 * fatigue_penalty,
        0,
        0.30
    )

    purchase_intention = clamp(
        0.40 * normalized_sentiment
        + 0.25 * value_match
        + 0.20 * social_proof
        + 0.15 * impulsivity,
        0,
        1
    )

All clamps and normalizations are implemented in a pure policy module and covered by boundary tests.

### 11.3 Memory decay

At daily reflection:

    recall_next_day = clamp(recall_current * 0.85 + reinforcement, 0, 1)

Reinforcement comes from a repeated exposure or a trusted friend's conversation and is capped at 0.20 per day.

### 11.4 Social influence

An agent may share an advertisement only after noticing it. Share probability combines the cognition result, novelty seeking, sentiment magnitude, and edge trust. A receiver obtains a social-proof update, not a direct advertising exposure. One message may traverse at most one edge per simulated day.

## 12. LLM cognition contract

LLM cognition enriches language and bounded state deltas; it does not control movement, time, event creation, persistence, or arbitrary tool execution.

The provider receives:

- a minimized fictional persona;
- current activity, mood, and relevant memories;
- structured campaign description;
- channel and exposure count;
- strict output schema and disclosure language.

The provider must return:

- interpretation: 1 to 240 characters;
- emotion: one of curious, positive, neutral, skeptical, annoyed;
- sentiment_delta: -0.25 through 0.25;
- recall_delta: 0 through 0.30;
- purchase_reason: 1 to 240 characters;
- share_probability: 0 through 1;
- memory_summary: 1 to 280 characters;
- safety_flags: list of strings.

Pydantic validation clamps numeric values only after logging a validation warning. Invalid JSON is repaired once by a schema-repair prompt. A second invalid response triggers the deterministic rule fallback. Provider failures must never abort a run.

## 13. Event system

Every state change is represented by an immutable event:

- event_id
- run_id
- simulated_minute
- sequence
- event_type
- agent_id when applicable
- campaign_id when applicable
- channel when applicable
- payload
- source: rule, mock, local-llm, remote-llm, or fallback
- model identifier when applicable
- prompt hash when applicable
- caused_by_event_ids for complete causal traceability

Required event types:

- run.started
- agent.activity_changed
- agent.location_changed
- campaign.eligible
- campaign.impression
- campaign.noticed
- campaign.ignored
- cognition.requested
- cognition.completed
- cognition.fallback
- memory.created
- social.shared
- social.received
- agent.state_updated
- day.reflected
- run.completed
- run.failed

SQLite is the durable event store. JSONL is an append-only portable export. Current state is a projection reconstructed from events plus versioned initial inputs. Every tick uses two phases: all agents first plan against an immutable snapshot, then the engine resolves cognition and commits results in a stable order. This prevents agent iteration order from changing outcomes.

## 14. Project files and storage

Running adlife init NAME creates:

    NAME/
      adlife.yaml
      population.yaml
      campaigns/
        demo-phone.yaml
        demo-billboard.yaml
      assets/
      runs/
      reports/
      .gitignore

Each completed run directory contains:

    runs/RUN_ID/
      run.json
      inputs/
      events.jsonl
      results.sqlite3
      metrics.json
      provider-usage.json

The project never writes outside its project directory except normal uv package installation and an optional user-level configuration file at the platform-standard application configuration path.

## 15. Command-line interface

Required commands:

- adlife --version
- adlife doctor
- adlife init NAME
- adlife validate PATH
- adlife population generate
- adlife campaign import
- adlife run
- adlife replay
- adlife report
- adlife compare
- adlife demo

Commands return exit code 0 on success, 2 for user input or validation errors, 3 for provider configuration errors, and 4 for corrupted run artifacts. Unexpected defects use exit code 1.

The demo command creates a temporary sample project, runs 20 mock agents for three days, and opens the TUI without requiring a model or API key.

## 16. Terminal user interface

The Textual interface contains:

- header: run identifier, simulated day/time, speed, provider, and seed;
- population table: name, activity, zone, mood, recall, and purchase intention;
- event stream: the newest 100 relevant events;
- selected-agent inspector: identity, traits, relationships, memories, and recent reasoning;
- metrics strip: reach, notice rate, average sentiment change, recall, shares, and high-intent count;
- controls: pause, step, speed, filter, select agent, and quit.

TUI rendering consumes events through a read-only event bus. It cannot mutate simulation state. Headless and live runs therefore use the same engine and produce the same result.

## 17. Reporting and comparison

The report must include:

- methodological disclosure;
- run configuration and reproducibility data;
- population summary;
- reach and frequency;
- noticed versus ignored exposures;
- sentiment distribution before and after;
- recall distribution;
- purchase-intention distribution;
- channel comparison;
- social-spread graph summary;
- selected anonymized agent diaries;
- model usage and fallback counts;
- limitations.

The compare command rejects two runs unless population identity, root seed, duration, and engine version match. It produces paired agent-level differences and aggregate differences. Statistical intervals describe repeated model runs, not real-world population certainty.

## 18. Academic evaluation

The repository includes an experiment that compares:

1. mobile-feed versus highway-billboard;
2. single versus repeated exposure;
3. social influence enabled versus disabled;
4. rule-only versus LLM-enriched cognition.

Each full academic experiment runs with at least 50 seeds in rule or mock mode; quick automated checks may use 20. The analysis reports mean, standard deviation, bootstrap 95 percent interval, effect size, and directional stability for notice rate, recall, sentiment delta, purchase intention, and social shares.

Optional human validation may compare directional ranking against a small consented survey. Human data is stored outside the repository and is not required for automated tests or the public demo.

## 19. Security, privacy, and ethics

- Personas are fictional and generated from templates.
- Inputs reject national identifiers, phone numbers, email addresses, and free-form secrets in persona fields.
- Reports always display a synthetic-data disclosure.
- Provider prompts minimize data and contain no filesystem paths.
- API keys are accepted only through environment variables or hidden prompts.
- Logs redact authorization headers and likely secret patterns.
- Campaign files may be untrusted; YAML uses safe loading and paths are resolved beneath the project root.
- HTML reports escape user-provided text and embed charts without remote scripts.
- The application does not make purchasing or targeting decisions for real people.

## 20. Performance targets

On a four-core laptop with 8 GB RAM:

- rule-mode run: 30 agents for seven days in under 10 seconds;
- mock-mode run: 30 agents for seven days in under 20 seconds;
- SQLite database under 100 MB per maximum run;
- TUI refresh at least four times per second without changing simulation outcomes;
- startup to help output under two seconds after installation.

Network-provider duration is excluded because it depends on the configured model.

## 21. Quality gates

Before a tagged release:

- unit and integration tests pass on Linux, macOS, and Windows;
- statement coverage is at least 85 percent for src/adlife;
- Ruff formatting and linting pass;
- mypy passes with strict mode for project-owned modules;
- rule and mock golden scenarios are deterministic;
- package wheel installs into a clean environment;
- uv tool install of the built wheel exposes adlife;
- demo command finishes without network access;
- no secret is present in tracked files;
- install script passes shellcheck;
- documentation contains the synthetic-data limitation prominently.

## 22. Repository boundary for future SaaS

Reusable modules:

- domain schemas;
- deterministic policy functions;
- population generation;
- simulation orchestration;
- provider protocol;
- event serialization;
- metrics and comparison.

University-only adapters:

- Typer CLI;
- Textual TUI;
- local SQLite repository;
- local HTML report writer;
- shell installer.

A future SaaS may replace adapters with FastAPI, PostgreSQL, a task queue, object storage, authentication, and billing without changing the domain event format or policy contracts.

## 23. Definition of complete

The university project is complete when a new user can install it, run adlife demo without credentials, inspect a live 20-agent society, import a campaign, run a deterministic comparison, export an HTML report, inspect the methodology, and reproduce all automated tests from the public repository.
