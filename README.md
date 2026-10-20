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

It is a research and teaching instrument: a controlled environment where you can change a declared
campaign input and compare seed-matched synthetic runs under common random numbers. The resulting
difference is a contrast inside this model, not automatically a causal effect; changed opportunity
structure and changed numeric response assumptions are classified explicitly.

A separate, opt-in city track runs fictional agents on a street map in a local browser workbench.
Its **bounded AI cognition** mode lets a language model choose a few closed appraisal categories
for an ad an agent noticed; AdLife maps them through a fixed policy into a separate synthetic
cognition state, while movement, attention, baseline responses, and the purchase-intention proxy
stay rule-owned.

> **Live demonstration:** [docs/investor-demo.md](docs/investor-demo.md) is the two-part
> SETUP and SIMULATION script for presenting the city workbench with bounded AI cognition from a
> local Ollama `qwen2.5:7b-instruct` model, including the offline fallback when no model is
> available.
>
> Backup: `scripts/demo_workspace.py` seeds a running workbench with ready-to-open runs; see [Backup: seed a demo workspace](docs/investor-demo.md#backup-seed-a-demo-workspace).
>
> Guided view: [docs/guided-demo.md](docs/guided-demo.md) is the presenter guide for the plain-language pages at `/guide` in English, German, and Persian, with the online-AI start command, a 10-minute script, and a rehearsal checklist.

---

## Scientific scope

> **Fictional-only use is an operator policy: do not supply real-person data.** Packaged templates are
> optional, editable starting points for authored personas, not a restriction to template-only input.
> Results are synthetic and exploratory: they describe behaviour *inside
> a synthetic model* and are not a representative survey of, nor a statistically representative
> prediction about, any real population, market, or country.

Persona validation rejects recognized national identifiers, phone numbers, email addresses, and secret
patterns. This screening is best effort and cannot prove fictional identity or detect all real-person
data. Operators remain responsible for supplying fictional data only. Provider prompts are minimised
and carry no filesystem paths; generated reports render a synthetic-data disclosure.

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
| Schema-v6 response metrics and matched response comparison | Complete as an additive read-only layer; attention remains the default C4a contract |
| Explicit 2–100-pair spatial studies and zero-JavaScript HTML evidence reports | Bounded C4b evidence complete; simulator-seed uncertainty only |
| Verified offline, content-addressed city catalog with three `fictional-fixture` entries | Foundation complete; zero rights-reviewed real-city entries |
| Browser-operated local city workbench, immutable schema-v9 and schema-v10 cognition evidence, workspace V2 city-pack upload, verified run library, and run-scoped inspection | Local single-operator slice complete; one process-local worker and operator-supplied uploads remain rights-unreviewed |
| Optional OpenAI-compatible noncausal explanations with approved origins, protected credentials, deterministic fallback, and frozen replay | Complete for the local workbench; authentication and OAuth are not implemented |
| Bounded AI cognition (workbench schema 4 / manifest V10): closed provider-selected categories, the frozen `city-cognition-influence-v1` mapping into a separate cognition state, verified saved-run inspector, V10 metrics, and offline replay | Complete for the local workbench, `city-view`, `city-metrics`, and `city-replay`; `city-compare --layer response`, `city-study`, and `city-report` refuse V10 runs for now; exploratory, not validated psychology |
| Rights-reviewed real-city entries, calibrated geographic campaign effects, authenticated multi-user deployment | Not implemented |
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
uv run adlife city-workbench --workspace ./workbench  # browser-operated local city study
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

`adlife city-catalog list` exposes a packaged, offline, content-addressed catalog. It
contains exactly three bundled fictional fixtures and zero rights-reviewed real-city
entries: `fictional-grid-v2`, `fictional-harbor-loop-v2`, and
`fictional-river-junction-v2`. Selection verifies each immutable pack hash and its
metadata before use. Use a packaged ID with `city` or `city-run`. `city-import` writes a
local pack file but does not register it in this packaged CLI catalog. No command contacts
a public tile server or geocoder, and a locally imported real-world pack is not
automatically catalog-qualified. The
[city-source qualification checklist](docs/data/city-source-qualification.md) defines
the human rights review required before a real-world entry may ship.

Start the local browser-operated workbench with:

```bash
uv run adlife city-workbench --workspace ./workbench
```

Then open `http://127.0.0.1:8765` yourself; the command never opens a browser or visible
terminal. The packaged, no-network UI implements `CITY -> CAMPAIGN -> RUN -> INSPECT`:
choose a verified bundled fixture or upload one local CityPack V2 into this workspace,
compose bounded phone/billboard assumptions from a packaged fictional creative, validate
without writing a run artifact, start and poll a deterministic run, then reopen it from the
verified library in the immutable scoped inspector. Uploads are capped at 4 MiB for both
raw request and canonical stored bytes; other JSON write routes remain capped at 128 KiB.
Technical validation does not establish ownership, license compliance, redistribution
rights, or real-city qualification, so uploaded entries are labelled
`Operator supplied - rights not reviewed`. Source URLs are inert displayed metadata and
are never dereferenced. The browser keeps only one unsaved in-memory draft. The server
remains authoritative for geometry, validation, run-ID collisions, artifact integrity,
and all scientific results.

The CAMPAIGN stage includes an Audience editor: choose from four bundled templates,
add/duplicate/remove personas, edit counts and explicit assumptions, then validate and run.
Counts must total 1–30 agents over 1–7 days; changing run agent count never silently changes
the audience. Persona IDs are immutable in the UI. Saved schema-v8 inspection shows verified
fictional names beside stable agent IDs; biography and MBTI stay collapsed with a noncausal label.

Name, age, occupation, background and MBTI are descriptive, noncausal context. They do not
infer response values or mobility. Only explicit interests, six response traits and initial
campaign state affect response equations. Persona IDs/counts/assignments are structural.
Purchase intention is an uncalibrated proxy, not a transaction or real purchase probability.
Screening refuses recognized personal identifiers, credentials and unsafe text; it cannot
prove the absence of personal information. Supply wholly fictional data only.

Workbench input and control seed values cross the HTTP boundary as canonical exact decimal
strings from `0` through `9223372036854775807`; the server converts the accepted string
once to the Python integer used by the deterministic model. One process-local worker reports
`queued -> evaluating -> persisting -> verifying -> completed`. Cancellation is
cooperative before persistence. Late cancellation is refused once publication starts;
publication and verification continue, may still fail safely, and reach `completed` only
after the independently verified result matches the frozen receipt. A browser refresh can
resume polling the same active process through `/api/workbench` and its frozen
`accepted_settings`. A server restart discards transient job history, but the bounded
library rediscovers fully verified run artifacts at
`<workspace>/city-runs/<run-id>/`.

Default browser workbench runs submit schema-3 and create schema-v9 city artifacts; choosing
Operation `Bounded AI cognition` submits schema 4 and creates a schema-v10 artifact. Rules is
the offline default with a zero explanation budget; mock is also offline and requires no
credential. Schema-2/schema-v8 fictional-persona and schema-1/schema-v7 homogeneous-cohort
requests remain compatible. Schema-v7 through schema-v9 retain schema-v6 scientific
evidence; schema-v10 keeps the same opportunity and attention evidence and a rule baseline
computed by the same equations, stored in its dual-state `spatial-response-v2` form. All of
them add canonical `inputs/workbench.json`, bound by
`workbench_input_sha256`, so the accepted city, creative snapshot, campaign,
population assumptions, and settings are frozen for load and replay. The scoped inspector
freshly verifies the artifact when the page opens and exposes its read-only model-clock
timeline and persisted evidence. That page's reads reuse one opaque-token-bound,
process-local immutable snapshot; reload and direct tokenless API reads verify the files
again. Playback, timeline scrubbing, filtering, and selection are presentation state only,
and Play at the end restarts from minute zero. This visual replay does not replace the
independent `adlife city-replay` recomputation. A run-scoped response-capable timeline
transition uses one GET-only `/api/runs/{run_id}/timeline` projection from the page's
verified context. The legacy
`/api/timeline` route exposes the same contract over the immutable context validated when
that viewer starts, while older schema capabilities retain their compatible individual
reads. This removes repeated reconstruction without changing any persisted result; a new
page load re-verifies current files and refuses inconsistent changes.

### Model assistance: AI explanation and bounded AI cognition

The RUN step's **Model assistance** section keeps provider location separate from provider
authority. **Operation** decides what a model may do, and with it the exact saved schema;
**Source** decides where the output comes from: `Rules — offline`, `Mock — reproducible demo`,
`Local compatible model`, or `Remote compatible model`. The presence of a provider profile
never selects a schema. The three modes are:

| Mode | Workbench choice | Request / saved run | What the model may change | Network and credentials |
| --- | --- | --- | --- | --- |
| **Rules** | Source `Rules — offline` (the default) | schema 3 / V9 with `Explanation only`, or schema 4 / V10 with `Bounded AI cognition` (exact neutral vector) | nothing; no model is called | none |
| **AI explanation** | Operation `Explanation only` with a Mock, Local, or Remote source | schema 3 / V9 | noncausal prose beside already-computed responses; no number, event, or state | Mock is offline; Local and Remote call one startup-approved endpoint |
| **Bounded AI cognition** | Operation `Bounded AI cognition` with a Mock, Local, or Remote source | schema 4 / V10 | closed appraisal categories that AdLife maps through the frozen `city-cognition-influence-v1` policy into a separate synthetic cognition state only | Mock is offline; Local and Remote require a passing bounded-signal compatibility check |

Start the workbench with explicit server-side endpoint policy; the fixed `local-ollama`
loopback approval (`http://127.0.0.1:11434/v1`) is always available:

```bash
uv run adlife city-workbench --workspace ./workbench \
  --provider-origin ID=URL \
  --provider-key-env ID=ENVIRONMENT_VARIABLE
```

`--provider-origin ID=URL` approves one exact base URL for this process and may be repeated.
`--provider-key-env ID=ENVIRONMENT_VARIABLE` binds a remote profile to an environment-variable
*name*, never a credential value. A credential entered in the panel is sent once to the
dedicated loopback route and stored only through an approved operating-system keyring; it is
not put in a profile, run artifact, prompt, URL, log, browser storage, report, or later API
response. A ChatGPT or Codex product login, subscription, or OAuth token is not accepted as
an API credential. There is no OAuth flow in this local profile.

The provider implements a deliberately narrow OpenAI-compatible Chat Completions subset. It
makes bounded, non-streaming requests to the startup-approved `/chat/completions` target and
accepts only the strict explanation or bounded-signal result schema. Invalid, unavailable,
oversized, timed-out, or unsafe answers become typed deterministic fallbacks.

**AI explanation (schema 3 / V9).** Provider prose is a **noncausal annotation**: it
cannot change movement, exposure, attention, deterministic response values, campaign state,
budget, purchase probability, or metrics, and it cannot create events or identities. The
rules engine computes those facts first; the provider can only explain the frozen evidence.
Schema-v9 freezes the explanation request and every terminal outcome as
`inputs/cognition.json`, `outputs/cognition.jsonl`, and
`outputs/cognition-summary.json`, with hashes and counts in the manifest. Live provider
explanations are nondeterministic; frozen schema-v9 replay is deterministic and performs no
provider call. The saved-run inspector labels provider/cache, rules, mock, fallback, and
budget-skipped records separately and renders each explanation beside its matching computed
response.

**Bounded AI cognition (schema 4 / V10).** Rules first compute movement, opportunities,
attention, and every deterministic baseline response. AdLife then selects, in stable order,
at most 4 noticed responses per agent and 60 per run, and asks the source for one closed result
per selected response: `affect`, `relevance`, `credibility`, and `salience` categories plus
optional `safety_flags`. There is no provider prose and no provider-authored number. AdLife,
not the model, maps the categories through the frozen `city-cognition-influence-v1` tables into
bounded deltas on a separate synthetic cognition sentiment/recall track. Movement, attention,
the baseline responses and rule state, budgets, and the purchase-intention proxy never read
that track. An `uncertain` category or any safety flag keeps the answer auditable but applies
the exact neutral vector to the whole result. Name, age, occupation, background, and MBTI
never reach the causal prompt.

Local and remote sources fund 10, 20 (default), 40, or 60 of the selected requests; the rest
are recorded as budget-skipped neutral signals. Rules and mock resolve every selected request
offline and reserve no tokens. Each funded request reserves 1,024 output tokens: at most 4
HTTP attempts of up to 256 output tokens each, so 60 funded requests reserve 61,440 output
tokens. Input tokens and vendor charges are not included, and the reservation is not a
spending limit. A provider run first needs a passing bounded-signal compatibility check for
that exact profile revision; an explanation check does not count.

Schema-v10 freezes the complete non-secret cognition input in `inputs/cognition.json`, one
terminal record per selected request in `outputs/cognition.jsonl`, the outcome counts in
`outputs/cognition-summary.json`, and the final rule and cognition states in
`outputs/response-state.json`, all bound by the manifest. Live provider signals are
nondeterministic, even at temperature 0 with a fixed seed; frozen schema-v10 replay reapplies
the saved signals and performs no provider call, cache read, keyring or environment lookup, or
network request. The saved-run inspector shows each response's trace (deterministic baseline,
signal or neutral status, fixed mapping, applied cognition delta, atomic rule/cognition state
update) with source badges, coverage, profile/model/contract provenance, and the provider's
actual HTTP attempts and reported tokens, kept apart from the reservation.
`city-metrics --layer response` prints the rule baseline, with the uncalibrated
purchase-intention proxy, separately from provider-dependent cognition, which is exploratory
and not a campaign effect. The categories are synthetic model output, not private thoughts,
observed behavior, validated psychology, or a prediction.

Not yet done for schema-v10 runs:

- `city-compare --layer response`, `city-study`, and `city-report` refuse V10 runs for now;
  V6-V9 runs keep those commands. The default attention-layer `city-compare` pairs a V10 run
  only with another V10 run.
- No paired-cognition protocol freezes compatible signals across study arms, so
  provider-dependent cognition is never reported as a campaign effect.
- The wheel and frozen-executable release smoke checks do not yet exercise V10 runs.
- V10 is an unreleased artifact schema: runs written by earlier development builds are
  refused and must be created again.

This local profile still does not authenticate users, qualify a real-city/worldwide catalog,
download reports, operate comparisons or paired studies in the browser, calibrate
traffic/population behavior, or predict sales. A local upload can contain authorized real
street geometry, but the upload is not rights review or catalog qualification. The model
clock is illustrative and workbench runs remain capped at seven model days.

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
schema-v5, schema-v6, schema-v7, schema-v8, schema-v9, and schema-v10 runs, `city-view` shows
read-only current-minute opportunity and current-minute attention evidence on the map and in
bounded evidence rails; schema-v6 through schema-v10 also show response records and complete
end-of-run campaign state, which schema-v10 keeps as separate rule and cognition states. It
also renders
the verified full-run response ledger, keeping planned rule deltas separate from committed
state and retaining exact receipt provenance without changing the scrubbed timeline or
source run. The existing `/api/spatial-metrics` ledger and default `city-metrics` output
remain attention-only for every one of these schema versions under
`synthetic-metrics-not-observed-outcomes`. Schema-v6, schema-v7, schema-v8, and schema-v9 add the separate
`spatial-response-metrics-v1` projection under
`synthetic-response-metrics-not-observed-outcomes`, selected with `--layer response` or read
from `/api/spatial-response-metrics`. It reports direct rule-response receipts and
overall/campaign committed state with exact numerator and denominator evidence; channel
series are event-only because nonlinear state cannot be allocated honestly between
channels. No metric is calibrated to observed behavior.
Campaign copy, identity, creative content and provider configuration are not inputs to the
attention draw. In the response model, campaign ID routes each notice to its campaign
assumptions and campaign-scoped state. Campaign name, copy, creative hash and provider
configuration do not change numeric response values when the frozen response assumptions
remain fixed.

`city-metrics` and `city-compare` preserve the exact attention output by default. Their
additive `--layer response` mode requires schema-v6, schema-v7, schema-v8 or schema-v9 and independently
classifies opportunity structure and numeric response assumptions. A same-run A/A
comparison is exactly zero. When
the normalized placement/channel opportunity structure differs, the result is explicitly
`opportunity-confounded`, not a channel effect. Every comparison carries a non-causal,
unobserved claim scope: attention comparisons use
`synthetic-comparison-not-causal-or-observed-effect`, while response comparisons use
`synthetic-response-comparison-not-causal-or-observed-effect`. For schema-v10,
`city-metrics --layer response` instead emits the separate `spatial-response-metrics-v2`
document with a rule-baseline family and a provider-dependent cognition family, while
`city-compare --layer response` refuses V10 runs.

Bounded C4b evidence is also complete: `city-study` analyzes an explicit definition of
2–100 already committed, seed-matched run pairs as `spatial-paired-study-v1`, and
`city-report` publishes its fixed-path, zero-JavaScript evidence ledger. One seed pair is
the experimental unit; the analysis never pools agents or events across seeds. Its
`synthetic-study-not-observed-or-causal-effect` results describe deterministic simulator-seed
variation, not population confidence. Older schema-v4 artifacts remain opportunity-only.
Broader C3/C4 social and purchase integration, authentication, OAuth, browser
comparison/report operation, V10 studies and reports, calibrated real-city behavior and
external validity remain open. Nothing here
claims observed device use, viewability, attention, traffic, people, purchases, sales, or
causal effects.
Use `--largest-component` only if dropping disconnected road segments is acceptable.
Use `--agents`, `--days`, `--seed`, and `--port` to change the preview. No map tile server,
provider key, or network connection is needed. The server does not open a browser or
terminal window for you.

`city-run` always freezes a bounded mobility trace with all-minute integrity evidence;
schema-v5 spatial runs additionally persist opportunity and attention evidence, schema-v6
runs add the bounded response/state layer above, schema-v7/schema-v8 workbench runs add immutable
accepted-input provenance, schema-v9 adds frozen noncausal explanations without changing
that scientific model, and schema-v10 adds frozen bounded cognition signals that change only
a separate cognition state. `city-replay` verifies
the selected contract without changing source artifacts, and `city-view` opens its verified
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
| [docs/investor-demo.md](docs/investor-demo.md) | The live two-part demo script: SETUP (workbench, local model, compatibility check) and SIMULATION (bounded AI cognition run, inspector, metrics, offline replay) |

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
the recorded one. Live provider explanations and bounded cognition signals are
nondeterministic; frozen schema-v9 and schema-v10 replay use the recorded validated cognition
and make no provider call. Wall-clock metadata is not part of event equality.
The recipe is in [docs/reproducibility.md](docs/reproducibility.md).

---

## Limitations

Results are synthetic and exploratory. The society is small (1–30 agents) over short horizons (1–7
simulated days) with two advertising channels; purchase is a rule-derived proxy, not a transaction
model; nothing is calibrated against real-world data.
Standard CLI/demo initial states have no active purchase need and zero disposable budget,
so their purchase count is structurally zero. Purchase-enabled core studies require explicit
initial states. In the zone campaign engine, sentiment, recall and intention are shared
per-person state, not campaign-specific; the separate spatial schema-v6 response model uses
campaign-scoped state.
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

- Personas must be fictional; city audiences can be authored from editable templates.
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
