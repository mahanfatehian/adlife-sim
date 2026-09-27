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
  real-person data by policy; validation is not proof of fictional identity.
- Operating as a decision system for real advertising spend.

## Synthetic population construction

Agents are generated from packaged fictional templates: trait draws (attention,
skepticism, susceptibility, novelty seeking, impulsivity, price sensitivity), interest
sets and daily routines over the packaged eight-zone world, plus a connected
relationship graph. Persona validation rejects recognized national identifiers, phone
numbers, email addresses, and secret patterns. Screening is best effort: it cannot
establish that an arbitrary name is fictional. Users must supply fictional data only.

The standard CLI/demo initializes `active_need=False` and `disposable_budget=0`; it does
not generate purchase needs or budgets. Its committed-purchase count is therefore zero
by construction, not a finding about campaign effectiveness. Purchase-enabled core
experiments must explicitly supply need and budget in the `Scenario` initial states.
Sentiment, recall, intention, fatigue and social proof are shared per-person scalars,
not per-campaign state; multi-campaign final-state changes cannot be assigned to one ad.

## The LLM's role (and its limits)

In hybrid mode an OpenAI-compatible provider supplies narrative text and bounded numeric
inputs. `rule_modifier` (−0.10 to +0.10) adjusts rule sentiment and recall deltas before
the configured gains and final clamps. `share_probability` affects social sharing;
`relevance` sets advertising-memory salience. Provider `valence` and `credibility` are
recorded but do not directly enter the engine's current numeric state updates. Social
message valence instead comes from the sender's pre-response sentiment. Provider-reported
`sentiment_delta` and `recall_delta` do not replace rule deltas (the cognition event
records the composed deltas). Reason text does not enter numeric formulas; retained
memory summaries can still influence later remote cognition as context.

The wire adapter clamps finite, representable numeric overshoots with a warning before
strict result validation. Non-finite numbers, booleans and structural/request-ID errors
are refused; extra wire fields are dropped with a warning, not granted state authority.

The provider never supplies purchase probability directly: the purchase proxy reads
rule-derived intention, need, budget, and a keyed draw. Earlier provider-influenced
sentiment and social proof can affect later rule-derived intention and purchase outcomes.
The engine owns movement, event creation, identities, time, and budget updates. Requests
are minimised and screened for known sensitive text and paths; budgets, timeouts, retries,
and response-size bounds constrain provider calls. Provider failures fall back to rules,
with a `cognition.fallback` event. Rules and mock modes require no network.

## Data

No built-in real-world consumer data or telemetry. User-supplied campaign/population
YAML and optional remote cognition are additional inputs; users must keep those inputs
fictional. YAML is validated and campaign asset paths are confined to the project.
Provider usage (request counts, cache hits, failures) is recorded locally per run.

## Metrics

The metrics fold computes the documented set — reach, impressions, frequency, notice
rate, sentiment, recall, fatigue, direct/indirect awareness, word-of-mouth reach,
intention delta, high-intention count, purchases, and cognition counters — each with its
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
and must not be given. Age, occupation and income bands are fictional model inputs,
not evidence about corresponding real-world groups; names come from fictional lists.
Consumers of results should not
read agent archetypes as claims about real populations.

## Prompt-injection boundary

Campaign files and population files are **untrusted input**: YAML is parsed with a safe
loader, schemas are strict, referenced asset paths are resolved and confirmed to lie
beneath the project root before being opened. Prompt text is built from validated domain
fields and screened for filesystem paths and known secret patterns. Campaign text is
fenced as untrusted JSON data; fence markers in that text are escaped. The provider has
no tools or filesystem access through this adapter, and its validated answer cannot
create arbitrary events, movement, budgets, or identifiers. Prompt instructions do not
guarantee model obedience: hostile copy can influence the permitted narrative and bounded
numeric channels. Screening is best effort, not universal secret detection; opaque
unlabelled tokens and some transport-header-shaped campaign copy may pass the campaign
screen. The configured transport credential is additionally screened by exact value in
provider responses, even when it has no recognizable vendor pattern. Never put
credentials or real-person data in campaign text.

## Reproducibility limits

Rules and mock modes are exactly reproducible (the same stream, event for event;
`adlife replay` verifies). Hybrid mode's live provider is nondeterministic; the artifact remains
auditable because validated answers and fallback provenance are recorded. Reproducing a
hybrid run requires its recorded cognition; fresh live calls with the same seed do not
guarantee the same numeric outcomes. Two runs on different code versions are
different models — the manifest records the version, and cross-version comparisons should
re-baseline.

## External-validity status

**Unknown.** The model has not been calibrated against any real-world data, and no claim
of external validity is made. Its honest use is mechanism study and relative comparison
inside the synthetic world, not absolute prediction.
