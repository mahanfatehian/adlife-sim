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

- **Geographic mobility is a separate pilot.** `adlife city` can show up to 250
  fictional agents for up to 31 days on a local directed road graph; that ephemeral viewer
  does not create an advertising artifact. The bounded `city-run` path can persist a city
  artifact for up to 30 agents and seven days, optionally with spatial campaign evidence;
  `city-replay` verifies every normalized minute-frame/evidence digest, and `city-view`
  observes the validated saved state. These are not zone campaign-engine run artifacts. The packaged,
  offline, content-addressed catalog contains only the `fictional-grid-v2` fixture; no
  real city is catalog-qualified. A local real street extract supplies geography, not
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
  observed attention. For schema-v5 and schema-v6, the C4a spatial metrics remain
  attention-only: they summarize verified opportunity/attention files, not response state,
  with exact numerator, denominator, and source paths under
  `synthetic-metrics-not-observed-outcomes`. Schema-v6's separate response layer derives
  `spatial-response-metrics-v1` under
  `synthetic-response-metrics-not-observed-outcomes`; its state receipts are overall and
  campaign scoped, never allocated to a channel.
- **Schema-v6 response state is an illustrative rules model, not observed behavior.** Only
  a validated noticed record can produce a bounded response and campaign-scoped state
  update under `synthetic-response-not-observed-behavior`. Purchase intention is an
  uncalibrated internal proxy, not purchase probability, sales, or a transaction. Schema-v6
  adds no cognition, memory, social propagation, budget mutation, purchase event, or
  movement change. It does not join the zone simulator or make the city population
  calibrated. The saved-run browser's full-run response ledger makes the proxy visible with
  receipts; purchase intention remains not purchase probability or an observed outcome. C3
  therefore remains open for those broader causal mechanisms.
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
