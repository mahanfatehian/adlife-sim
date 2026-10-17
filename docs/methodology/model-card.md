# Model card — AdLife Lab

A model card documents what a model is for, what it is not for, and where it can be
trusted. Read this before quoting any number the simulator produces.

## Intended use

The opt-in city path is a separate product-track prototype alongside the zone campaign
engine. Its packaged, content-addressed catalog contains three fictional fixtures and zero
rights-reviewed real cities. The local workbench can store a V2 pack as
`operator-supplied-unreviewed`; passing structural checks does not establish rights or
license compliance. A street pack acquired under appropriate rights can provide real
geography, but the agents, schedules and speeds remain synthetic and uncalibrated. Spatial
campaign documents can be validated against roads, and the pure core can derive bounded
synthetic opportunity records. A
schema-v5 `city-run` freezes those records plus a separate attention evidence stream: one
synthetic impression per opportunity and noticed when an independent keyed draw is below
the fixed 0.5 probability. The records say
`synthetic-attention-not-observed-behavior`; the probability is not calibrated or observed.
The read-only map displays current-minute opportunity and current-minute attention evidence.

An opt-in schema-v6 run adds deterministic campaign-scoped response state for persisted
notices under `synthetic-response-not-observed-behavior`. Its purchase intention is an
uncalibrated bounded proxy, not a purchase probability, sales estimate, or transaction.
Schema-v6 adds no cognition, memory, social propagation, budget mutation, purchase event, or
movement change. All spatial evidence is persisted and replayed from frozen inputs; older
schema-v4 runs remain opportunity-only and ordinary schema-v5 runs remain
opportunity/attention-only.

The loopback workbench publishes schema-v9 for schema-3 requests, retaining
schema-2/schema-v8 and schema-1/schema-v7 compatibility. V9 can add optional bounded
noncausal explanations after deterministic response evaluation.
No version adds a new behavioral model: canonical `inputs/workbench.json` freezes the
complete accepted draft and exact fictional creative-template snapshot, and the manifest's
`workbench_input_sha256` binds it to the other run inputs. Verified discovery, replay and
run-scoped inspection refuse a missing, changed, noncanonical or inconsistent sidecar.
Process-local job progress is operational metadata and has no effect on synthetic outcomes.
The exact accepted city is frozen separately as canonical `inputs/city.json`; replay,
metrics, reports, and run-scoped inspection use the verified artifact and never depend on
the current workspace registry. Declared source URLs remain inert text and are not fetched.

The packaged browser operates `CITY -> CAMPAIGN -> RUN -> INSPECT` for this local
deterministic profile. Its Audience editor assigns explicitly displayed interests, traits,
and initial response-state assumptions per persona. The schema-1 compatibility path is a
homogeneous response cohort, not demographic calibration or evidence about a real
population. The model clock, map animation, playback speed, filtering, and selection are
presentation only and cannot change the frozen run. Optional provider-backed city
explanations annotate those transparent deterministic rules; they do not become agent state
or a decision input.

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
- Targeting, scoring, or profiling real people, or supplying real-person data; validation
  is not proof of fictional identity.
- Operating as a decision system for real advertising spend.

## Synthetic population construction

Fictional-only use is an operator policy. Packaged templates are optional, editable
starting points for city persona authoring; explicit population inputs are also supported.
The standard zone generator draws traits (attention, skepticism, susceptibility, novelty
seeking, impulsivity, price sensitivity), interests and daily routines over the packaged
eight-zone world, plus a connected relationship graph.

Persona validation rejects recognized national identifiers, phone numbers, email addresses,
and secret patterns. Screening is best effort and cannot prove fictional identity or detect
all real-person data. Users remain responsible for supplying fictional data only.

The standard CLI/demo initializes `active_need=False` and `disposable_budget=0`; it does
not generate purchase needs or budgets. Its committed-purchase count is therefore zero
by construction, not a finding about campaign effectiveness. Purchase-enabled core
experiments must explicitly supply need and budget in the `Scenario` initial states.
In the zone campaign engine, sentiment, recall, intention, fatigue and social proof are
shared per-person scalars, not per-campaign state; multi-campaign final-state changes cannot
be assigned to one ad. The separate spatial schema-v6 response model instead maintains
explicit campaign-scoped sentiment, recall, and purchase-intention proxy state.

## The LLM's role in schema-v9 city runs

The city workbench first computes the complete deterministic response stream and final
campaign state. It then selects at most 60 response records under a fixed aggregate budget.
Rules, mock, local, or remote explainers may produce bounded text for those records. This
text is a noncausal annotation: it cannot change movement, exposure, attention, response
values, sentiment, recall, purchase-intention state, budget, purchase probability, metrics,
events, or identities. It is not an agent's private thought and must not be presented as
sentience, observed psychology, sales prediction, or model calibration.

Schema-v9 freezes the effective request in `inputs/cognition.json`, one terminal outcome per
selected record in `outputs/cognition.jsonl`, and aggregate provenance in
`outputs/cognition-summary.json`. Strict schema validation, output screening, byte/token/
time/retry/repair bounds, endpoint policy, and deterministic fallback constrain the optional
OpenAI-compatible provider. Credentials never enter these files or the prompt.

Rules and mock explanations are deterministic and offline. Live provider explanations are
nondeterministic; frozen schema-v9 replay is deterministic and performs no provider call,
credential lookup, cache lookup, registry lookup, or network access. Replay verifies the
recorded terminal stream against the same numeric evidence and leaves the source unchanged.

## The zone engine LLM role (and its limits)

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

City-pack v2 can preserve real-world road geometry, traversal direction and public
source provenance, but it contains no real-person trajectories. Its IANA time zone is
source metadata: it does not make the fixed schedule calendar-accurate. The mobility
pilot can optionally assign synthetic home, workplace and leisure points supplied in a
pack-hash-bound place set. Those points must be routable road nodes, carry explicit
authored/source-derived/inferred provenance, and are frozen for v3/v4/v5/v6/v7/v8/v9 replay;
they are
scenario inputs, not addresses, mobility observations or evidence about residents. The
model has no dated calendar, holiday rules, daylight-saving transition policy, local
sunrise/sunset calculation, measured demand or calibrated traffic. Import validation
also does not prove license or commercial redistribution rights; those require the
separate recorded human review described in the
[city-source qualification checklist](../data/city-source-qualification.md).

Spatial scenario v1 records fictional campaign IDs, creative content hashes, time/cap
assumptions, exact roadside bindings and a declared phone-opportunity probability. A
creative hash proves byte identity only, not ownership or suitability. A road binding
proves geometric consistency only, not legal inventory, line of sight, viewability,
traffic, device use, attention or effect.

Spatial opportunity model v1 distinguishes matching road traversal, active crossing,
bound-coordinate proximity, an approximate billboard-facing heuristic, phone-eligible
synthetic activity-minutes, keyed threshold successes, cap refusals and emitted
opportunities. Each stage has a separate count. Phone draws are counter-based and keyed by
seed/campaign/placement/fictional agent/minute; roadside timing comes from illustrative
free-flow routes. Every record is explicitly a synthetic opportunity, not a verified
impression, through the literal claim scope `synthetic-opportunity-not-impression`.
Schema-v4 city artifacts persist and replay that stream and its independent summary.
Schema-v5 additionally persists deterministic attention model v1 at the fixed 0.5 notice
probability. Every opportunity creates one model impression; a threshold success creates a
noticed label carrying `synthetic-attention-not-observed-behavior`. This uncalibrated layer
excludes campaign/provider inputs from its draw. The attention evaluator itself does not
mutate cognition, agent state, or purchase logic, and schema-v5 stops at this evidence. The
read-only city viewer presents persisted current-minute opportunity and current-minute
attention evidence alongside mobility.

Schema-v6 retains that evidence and sends persisted noticed records across a separate
response boundary into the transparent rules-only response model. Strict response inputs
bind fictional interests and bounded traits, campaign target interests/relative price, and
complete initial state to the exact agent-ID set generated for the run and to the exact
scenario, not to one mobility assignment. Same-minute responses read a shared immutable
pre-minute state and commit one atomic campaign-scoped state update. Every
response/state-update stream record carries
`synthetic-response-not-observed-behavior`; the final-state document carries the claim at its
top level rather than on every state entry. The resulting sentiment, recall, and purchase
intention values are synthetic internal state: purchase intention is not purchase
probability, sales, or evidence of effect, and the formulas are not calibrated to any real
person, city, brand, or population.

Schema-v7, schema-v8, and schema-v9 workbench artifacts retain that entire schema-v6
scientific layer and bind the canonical `inputs/workbench.json` provenance sidecar (input
schema 1, 2, and 3, respectively); schema-v8/v9 additionally bind the authored persona
population and assignments. Replay validates and reuses those accepted inputs. V9's optional
provider explains frozen city response evidence through a separate adapter and does not
invoke the zone engine's numeric cognition composition or upgrade any geographic validity
claim.

## Metrics

The metrics fold computes the documented set — reach, impressions, frequency, notice
rate, sentiment, recall, fatigue, direct/indirect awareness, word-of-mouth reach,
intention delta, high-intention count, purchases, and cognition counters — each with its
numerator, denominator, and event/state sources recorded, so provenance is re-derivable
from the artifact.

For schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9 city runs, the original spatial-metrics fold
remains the attention default: it derives exact opportunity, impression, and noticed
receipts from verified persisted evidence and carries
`synthetic-metrics-not-observed-outcomes`. It does not consume v6 response or final-state
evidence.

Schema-v6, schema-v7, schema-v8, and schema-v9 add a separate `spatial-response-metrics-v1` fold under
`synthetic-response-metrics-not-observed-outcomes`. It derives direct rule-response receipts
plus complete initial/final campaign-state receipts. Event evidence may be grouped by
channel, but nonlinear committed state is only overall/campaign scoped. The response
comparison classifies opportunity structure and numeric response assumptions independently.
All of these uncalibrated values are model summaries, not observed outcomes, sales, or
causal effects.

`city-study` treats one verified seed pair as the experimental unit and produces
`spatial-paired-study-v1` under `synthetic-study-not-observed-or-causal-effect`; it never
pools agents or events across seeds. Its deterministic bootstrap describes simulator-seed
variation only. `city-report` renders the same revalidated aggregate evidence and pair
provenance as a zero-JavaScript static ledger. Neither command calibrates the model or
upgrades its external-validity status.

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

## Fictional personas and external validity

Default schema-3 authoring creates schema-v9, with schema-2/schema-v8 persona and
schema-1/schema-v7 homogeneous compatibility.
Four fictional templates are editable starting assumptions, not real residents or population
samples. Name, age, occupation, background and MBTI are noncausal descriptive context;
no personality predictive power, demographic calibration or real-person validity is claimed.
Only explicit interests, six response traits and initial campaign state enter response equations.
IDs/counts/assignments are structural; identity does not derive movement or provider prompts.
The saved inspector shows verified identities with collapsed noncausal biography/MBTI.

Studies remain bounded to 1–30 agents and 1–7 days. Purchase intention is an uncalibrated proxy,
not a purchase probability or transaction. Screening refuses recognized identifier/secret
patterns and unsafe display text but cannot prove absence of personal information. Fictional-only
inputs remain the operator's responsibility. Replay freezes persona records rather than relying
on current templates; deterministic invariance is an internal software property, not empirical
validation. No provider-generated personas or identity-derived mobility are implemented.
