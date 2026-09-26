# Model card — AdLife Lab

A model card documents what a model is for, what it is not for, and where it can be
trusted. Read this before quoting any number the simulator produces.

## Intended use

- Studying **mechanisms** of advertising exposure, attention, memory, fatigue, social
  transmission, and intention formation inside a small, fully controlled synthetic
  society.
- Teaching and demonstrating agent-based modelling, paired experimental design, and
  reproducible simulation practice.
- Comparing **relative** outcomes between two declared arms of the same synthetic world
  (phone versus billboard, campaign A versus campaign B) under common random numbers.

## Excluded use (do not)

- Forecasting real market outcomes, sales, or election-like behaviour.
- Claiming any accuracy, adoption, or investment figure derived from this model.
- Targeting, scoring, or profiling real people — the model contains none and accepts no
  real-person data.
- Operating as a decision system for real advertising spend.

## Synthetic population construction

Agents are generated from packaged fictional templates: trait draws (attention,
skepticism, susceptibility, novelty seeking, impulsivity, price sensitivity), interest
sets, budgets, and daily routines over a ten-zone world, plus a connected relationship
graph. Persona validation rejects national identifiers, phone numbers, email addresses,
and free-form secrets, so a real person cannot be reconstructed or inserted.

## The LLM's role (and its limits)

In hybrid mode an OpenAI-compatible provider may answer bounded cognition channels
(narrative colour: diary lines, reflection text). The provider never supplies purchase
probability, state transitions, or any numeric outcome — those are rule-derived. Requests
are minimised (no filesystem paths, no secrets), budgets and retries bound cost, failures
fall back to rules, and every fallback is recorded as a `cognition.fallback` event in the
artifact. In rules and mock modes no network exists at all.

## Data

No real-world data. No telemetry. No external datasets enter the model; user-supplied
campaign and population YAML are validated and path-confined. Provider usage (request
counts, cache hits, failures) is recorded locally per run.

## Metrics

The metrics fold computes the documented set — reach, impressions, frequency, notice
rate, sentiment, recall, fatigue, direct/indirect awareness, word-of-mouth reach,
intention delta, high-intention share, purchases, and cognition counters — each with its
numerator, denominator, and event/state sources recorded, so provenance is re-derivable
from the artifact.

## Ethical risks and mitigations

- **Dual use:** the engine cannot connect to any advertising network and makes no
  targeting decisions for real people; it is scoped to synthetic study.
- **Transparency:** every run persists its inputs, parameters, seed, and full event
  stream; reports carry the synthetic-data disclosure.
- **Cost and consent:** hybrid mode is opt-in, bounded, and local-first; rules mode needs
  nothing at all.

## Stereotype risks

Traits and interests are template-driven and intentionally simple; any mapping from
template categories to real social groups is an interpretation the model does not make
and must not be given. Personas carry no demographics that map to protected real-world
classes, and generated names come from fictional lists. Consumers of results should not
read agent archetypes as claims about real populations.

## Prompt-injection boundary

Campaign files and population files are **untrusted input**: YAML is parsed with a safe
loader, schemas are strict, referenced asset paths are resolved and confirmed to lie
beneath the project root before being opened. Prompt text sent to providers is built from
validated domain fields, is minimised, and never includes filesystem paths, secrets, or
raw user files. A hostile campaign string can, at worst, be displayed (escaped) in a
report; it cannot exfiltrate data or alter the model's rules.

## Reproducibility limits

Rules and mock modes are exactly reproducible (the same stream, event for event;
`adlife replay` verifies). Hybrid mode's live provider is nondeterministic; the artifact remains
auditable because provider answers never touch numeric outcomes and every fallback is
recorded. Replay answers from the recorded cache. Two runs on different code versions are
different models — the manifest records the version, and cross-version comparisons should
re-baseline.

## External-validity status

**Unknown.** The model has not been calibrated against any real-world data, and no claim
of external validity is made. Its honest use is mechanism study and relative comparison
inside the synthetic world, not absolute prediction.
