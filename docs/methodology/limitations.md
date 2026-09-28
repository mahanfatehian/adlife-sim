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
- **Principal state is shared across campaigns.** Sentiment, recall, intention,
  social proof and ad fatigue are per-person scalars. Multi-campaign final-state changes
  are not campaign-attributable, even though exposure counts, awareness and event links
  identify campaigns.
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
  fictional agents for up to 31 days on a local directed road graph. It is not joined
  to campaign exposures, purchases, social influence, the run store, or replay. Its
  minute-addressable trace is reproducible from the same city-pack bytes, seed, and
  parameters, but it is not a persisted campaign artifact. Its home/work/leisure
  assignments and travel speeds are illustrative, not demographic, traffic, or
  behavioral measurements. The bundled grid is fictional; a real street extract
  supplies geography, not real residents or validated effects.

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
