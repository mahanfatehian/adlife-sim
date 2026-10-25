# Limitations

What this model cannot support. Read alongside the
[model card](model-card.md) and the [experiment protocol](experiment-protocol.md).

## Scale of the campaign engine

- **1–30 agents.** Social dynamics at this scale are conversation networks, not
  markets or epidemics. Network-level phenomena (cascade thresholds, opinion leaders)
  cannot be studied honestly here.
- **1–7 simulated days.** Long-horizon effects — brand building, churn, seasonality —
  are out of scope by construction.
- **Two advertising channels** (mobile feed, highway billboard), each with fixed
  placement geometry. Media mix beyond these two, and creative-quality variation, are
  not modelled.

## Behavioural simplifications

- **Purchase is a proxy, not a transaction model.** It is a rule-derived threshold plus
  draw over budget and intention. No payment psychology, no categories beyond the
  campaign's reference price, no post-purchase behaviour beyond a memory.
- **Standard CLI/demo purchases are disabled by initial state.** `active_need=False`
  and `disposable_budget=0`; there is no generated need/budget sampling. Zero committed
  purchases in these runs are structural, not evidence of ineffective advertising.
  The core proxy supports explicit purchase-enabled `Scenario` initial states.
- **Zone-engine principal state is shared across campaigns.** In the zone campaign
  engine, sentiment, recall, intention, social proof and ad fatigue are per-person scalars.
  Multi-campaign final-state changes are not campaign-attributable, even though exposure
  counts, awareness and event links identify campaigns. This does not describe the separate
  spatial schema-v6 response model, whose bounded state is campaign-scoped.
- **Routines are templates.** Agents follow generated daily routines; they do not plan,
  adapt their schedules, or respond to congestion.
  Generated unemployed profiles retain the historical office-worker schedule proxy;
  their occupation is not changed. Explicit populations can select the separate,
  routable unemployed template. Neither schedule is calibrated employment behavior.
- **Memory is five slots.** Retention keeps the strongest five episodic memories;
  anything subtler (graded recency curves, interference between memories) is not
  represented.
- **Relationships are static edges.** Strength is fixed per run; no friendship formation
  or decay.
- **Word of mouth is day-bounded:** at most one message per sender/campaign/day and one
  traversal per unordered edge/campaign/day. Both endpoints must be awake and co-located
  or linked online; receipt alone does not trigger forwarding. Message valence is the
  sender's pre-response sentiment, not provider valence. At similarity weight 1 with
  all eligible similarities zero, there is no supported preference and selection uses
  the same keyed uniform receiver draw instead.

## Validity

- **No calibration.** No parameter has been fitted to real-world data; all constants are
  documented design choices. External validity is unknown (see the model card).
- **Results are relative, not absolute.** The honest reading of a comparison is the
  paired difference between arms of the *same* synthetic world — never an absolute
  "market size" or "expected sales" number.
- **Channel-opportunity confounding.** Phone and billboard arms differ in exposure
  opportunity as well as channel; see the experiment protocol's warning before reading
  channel effects.

## Engineering caveats

- **The D1b workbench is local orchestration, not an enterprise service.** One
  process-local worker executes at most one job or provider operation, and operational job
  history disappears on restart; only fully verified run artifacts are rediscovered.
  Schema-v7/schema-v8/schema-v9 add canonical `inputs/workbench.json` provenance to schema-v6 evidence, not a
  new or calibrated behavior model. The guided pages and the local API can validate and
  operate this local deterministic workflow, cooperatively cancel before persistence, resume
  polling after a page refresh in the same live process, and reopen the verified library. It does
  not provide authentication or multi-user authorization, OAuth,
  report download, comparison/paired-study operation, process-restart recovery, or a
  rights-reviewed real city/worldwide catalog. It remains loopback-only and makes no
  observed or calibrated population, traffic, attention, sales, or external-validity
  claim.
  The guided view at `/guide` adds only a browser report of one saved schema-v10 run, with a
  self-contained download; it is not a comparison or study report.
- **Workspace upload is not data qualification.** A local V2 pack can be stored as
  `operator-supplied-unreviewed`, but technical validity does not establish rights,
  ownership, license compliance, legal routing, coverage quality, or fitness for a customer
  use. Its declared source URL is inert and not dereferenced. The default workbench has no
  outbound data request.
- **The browser workflow does not make the city model more realistic.** Schema-2/schema-3 assign
  explicit interests, response traits and initial state per fictional persona; schema-1
  retains one homogeneous set for every synthetic agent. The model clock and day/night presentation are illustrative; fixed
  schedules are not calendar-, daylight-, traffic-, or demand-calibrated. Playback,
  filters, map controls, and selection reveal persisted model evidence but cannot turn it
  into observed residents or outcomes.
- **Geographic mobility is a separate pilot.** `adlife city` can show up to 250
  fictional agents for up to 31 days on a local directed road graph; that ephemeral viewer
  does not create an advertising artifact. The bounded `city-run` path can persist a city
  artifact for up to 30 agents and seven days, optionally with spatial campaign evidence;
  `city-replay` verifies every normalized minute-frame/evidence digest, and `city-view`
  observes the validated saved state. These are not zone campaign-engine run artifacts. The
  packaged, offline, content-addressed catalog contains five fictional fixtures and no
  rights-reviewed real city. A local real street extract supplies geography, not
  real residents, measured traffic, legal navigation routes, or validated effects.
  Optional place sets constrain synthetic home, workplace and leisure nodes with stated
  provenance and deterministic assignment. They do not model land-use capacity, household
  composition, job matching, venue preference or observed origin-destination demand.
- **Spatial opportunity and attention are bounded model evidence.** `city-campaign validate`
  checks hashes, references, windows, caps, direction and road-coordinate consistency. C2
  derives typed opportunities and explicit funnel denominators. C3a schema-v4 city runs
  persist and replay that canonical stream under `synthetic-opportunity-not-impression`.
  C3b schema-v5 runs add one synthetic impression per opportunity and a keyed
  noticed/ignored label using a fixed 0.5 probability under
  `synthetic-attention-not-observed-behavior`. The threshold is uncalibrated and is not
  observed attention. For schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9, the C4a spatial metrics remain
  attention-only: they summarize verified opportunity/attention files, not response state,
  with exact numerator, denominator, and source paths under
  `synthetic-metrics-not-observed-outcomes`. Schema-v6's separate response layer derives
  `spatial-response-metrics-v1` under
  `synthetic-response-metrics-not-observed-outcomes`; its state receipts are overall and
  campaign scoped, never allocated to a channel.
- **Schema-v6/schema-v7/schema-v8/schema-v9 response state is an illustrative rules model, not observed behavior.** Only
  a validated noticed record can produce a bounded response and campaign-scoped state
  update under `synthetic-response-not-observed-behavior`. Purchase intention is an
  uncalibrated internal proxy, not purchase probability, sales, or a transaction. Schema-v6
  adds no cognition, memory, social propagation, budget mutation, purchase event, or
  movement change. It does not join the zone simulator or make the city population
  calibrated. The saved-run browser's full-run response ledger makes the proxy visible with
  receipts; purchase intention remains not purchase probability or an observed outcome. C3
  therefore remains open for those broader causal mechanisms.
- **Schema-v9 provider prose is not an agent mind or a causal model.** The optional
  noncausal explanation pass sees bounded, already-computed response evidence. It cannot
  change movement, exposure, attention, deterministic response values, campaign state,
  budget, purchase probability, metrics, events, or identities. A fluent explanation can
  still be wrong, stereotyped, inconsistent, or misleading; strict validation and screening
  reduce technical risk but do not establish psychological or scientific validity. Live
  provider explanations are nondeterministic; frozen schema-v9 replay is deterministic and
  performs no provider call, credential lookup, profile lookup, cache lookup, or network
  request. Its `inputs/cognition.json`, `outputs/cognition.jsonl`, and
  `outputs/cognition-summary.json` preserve provenance, not truth.
- **Schema-v10 bounded cognition is provider-dependent, exploratory evidence.** A model
  chooses closed affect, relevance, credibility, and salience categories for selected noticed
  responses, and AdLife's frozen `city-cognition-influence-v1` tables turn them into bounded
  deltas on a separate synthetic cognition state. The categories are not validated psychology,
  the tables are not calibrated effects, and a general-purpose model brings its own
  tendencies: in rehearsal with the earlier prompts a local model gave nearly the same answer
  to most responses, and before its prompt defined the safety flags it flagged almost every
  ordinary answer. New runs ask the model to answer as the person (prompt contract
  `city-cognition-persona-prompt-v3`), from the persona card (name, age, job, background,
  MBTI, interests, and temperament), their current rule-track feelings, the campaign ads they
  noticed before, and their situation. An in-character answer is still a general-purpose
  model's guess about a fictional person, not a real reaction, and that prompt has not been
  rehearsed like the earlier one. Giving the model an age, a job, and an MBTI type adds a
  stereotype risk that the `stereotype-risk` flag catches only when the model flags itself; a
  flagged answer is neutralized, an unflagged one may still be biased. Runs saved earlier used
  prompt contract `city-cognition-signal-prompt-v2`, which excluded the descriptive persona
  fields.
  Movement, attention, baseline responses, budgets, and the purchase-intention proxy stay
  rule-owned, so V10 adds no memory, social propagation, budget mutation, or purchase event.
  No paired-cognition protocol exists yet: `city-compare --layer response`, `city-study`, and
  `city-report` refuse V10 runs, the attention comparison pairs a V10 run only with another
  V10 run, and cognition results must never be reported as a campaign effect. Live provider
  signals are nondeterministic even at temperature 0 with a fixed seed; frozen schema-v10
  replay reapplies the saved signals and performs no provider call, so it reproduces a saved
  run but cannot validate one.
- **The bounded-cognition budget is an output reservation, not a cost limit.** Each funded
  request reserves 1,536 output tokens: at most 6 HTTP attempts of up to 256 output tokens
  each. Input tokens and vendor charges are outside that reservation. A local Ollama model
  answers one request at a time, about 2.5-3 seconds each on the development laptop, so a
  local source defaults to 120 requests, and its largest funding of 1000 requests can outlast
  the 1800-second aggregate deadline, after which unanswered requests fall back to the
  neutral vector; because requests start in an interleaved order across the run, those
  fallbacks thin every day evenly instead of removing the last days. Runs saved earlier keep
  their V2 budget: 1,024 output tokens per request, at most 60 requests, and a 180-second
  deadline. A ChatGPT or Codex subscription, product login, or OAuth token is not accepted as
  a credential, and the workbench has no user authentication.
- **V10 artifacts are unsigned and unreleased.** Canonical hashes detect damaged, partial,
  isolated, or rebound files but cannot attest who produced a provider answer or resist
  someone who rewrites every file and recomputes the hash chain. V10 runs written by earlier
  development builds no longer validate, and the wheel release smoke checks do not yet
  exercise V10 runs.
- **Geographic evidence omits important mechanisms.** Road proximity and the half-plane
  orientation heuristic omit occlusion, lanes, buildings, traffic, speed variation and
  measured viewability. Phone probability is an analyst assumption, not an observed usage
  rate. Matched comparisons carry `synthetic-comparison-not-causal-or-observed-effect`; if
  the normalized placement/channel structure differs they are labeled
  `opportunity-confounded`, not interpreted as a channel effect.
- **C4b quantifies model-seed variation, not real uncertainty.** `city-study` treats each
  seed pair as one experimental unit and emits
  `synthetic-study-not-observed-or-causal-effect`; it provides no population confidence,
  p-value, representative sample, calibration, validation, or causal identification.
  `city-report` publishes that bounded spatial evidence as a zero-JavaScript report capped
  at 8 MiB, not as a sales forecast. A fixed report does not add missing spatial social,
  memory, cognition, rule-owned purchase, traffic, population, or external-validity
  mechanisms. Those broader C3/C4 and research gates remain open.
- **Time-zone metadata is not a civil-time model.** City-pack v2 records an IANA time
  zone, but the fixed schedule treats day 1 as Monday, has no start date, holidays or
  daylight-saving transition policy, and uses fixed light/dark hours rather than local
  sunrise and sunset. A time zone therefore does not make the fixed schedule
  calendar-accurate.

- **Fresh hybrid calls are not reproducible.** Bounded provider modifiers and social/
  memory inputs can change numeric outcomes, including later rule-derived intention.
  Purchase probability is never accepted directly from a provider. Reproduction needs
  the recorded cognition and fallback provenance; rules and mock modes are deterministic.
- **Console encoding.** On non-UTF-8 consoles (a default Windows `cp1252` shell),
  non-ASCII output may need `PYTHONUTF8=1`; `adlife doctor` reports this.
- **The live dashboard needs a real terminal.** In piped or non-interactive shells,
  `--live` falls back to headless (or refuses with `--no-headless-fallback`).

## What would falsify the tool's usefulness

If paired A/A comparisons under the pinned protocol ever produce nonzero differences, or
replay of a stored run fails to reproduce its event stream, the tool's central guarantees
are broken and its results should not be trusted until fixed. The test suite treats both
as release blockers.

## Fictional audience limits

Default schema-3 personas produce schema-v9 and schema-4 bounded AI cognition produces
schema-v10; schema-2/schema-v8 and schema-1/schema-v7 remain
compatible. Names, ages, occupations, backgrounds and MBTI are descriptive metadata,
noncausal for the rules, with no real-person validity, demographic calibration or MBTI
predictive power; new schema-v10 runs send them to the AI as the profile it answers as, where
they can move only the separate cognition track.
Explicit interests, six traits and initial campaign state are causal response assumptions;
IDs/counts/assignments are structural. Reordering definitions preserves assignments, while
changing IDs may remap agents. No identity-derived mobility or provider-generated personas
are implemented. Frozen inputs/workbench.json supports replay independent of current templates.

The guided audience choice and the verified identity inspector operate within 1–30 agents and 1–7 days.
Purchase intention is an uncalibrated proxy, never a transaction. Canonical byte ceilings
(3,072 per persona, 98,304 population, 131,072 input; 32,768 template catalog) bound resource
use. Screening refuses recognized sensitive identifiers/secrets and unsafe text; it cannot
prove absence of personal information. Use wholly fictional data only. There is no authenticated
shared deployment, durable job recovery, worldwide qualified catalog, provider persona generation,
or full browser comparison/study/report workflow. These limitations remain despite V9 and V10
consumer support.
