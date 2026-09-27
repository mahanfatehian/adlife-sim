# ODD protocol — AdLife Lab

This document describes the model under the ODD (Overview, Design concepts, Details)
protocol, a reporting format for agent-based models. The formulas and limitations below
describe the current implementation, reconciled against the code on 2026-09-27; they are
not empirically calibrated laws of consumer behaviour.

## 1. Purpose

AdLife Lab simulates a small fictional society of consumers who follow daily routines,
move between zones, encounter advertising through two channels (a mobile feed and
highway billboards), talk to people they know, and update recall, brand sentiment, and
purchase intention accordingly. It exists to study *mechanisms* — how attention, memory
decay, fatigue, and word of mouth shape campaign outcomes under controlled conditions —
and to serve as a research and teaching instrument. Rules/mock runs are deterministic
under frozen inputs; live-provider reproducibility requires recorded cognition answers,
not merely the same seed.

It is explicitly **not** a forecasting tool for real markets. All agents are fictional;
results are synthetic and exploratory.

## 2. Entities, state variables, and scales

**Entities**

- **Person** — one fictional consumer: a profile (traits, interests, routine) and
  a mutable consumer state.
- **Campaign** — an advertised product: placements (mobile-feed and/or billboard),
  target interests, price, category reference price, frequency caps, creative assets.
- **World** — zones connected by named routes; the packaged default world has eight zones.
- **Relationship** — an undirected social edge of kind `friend`, `colleague`, `family`, or
  `online`, with a strength in [0, 1].
- **Memory** — one episodic record with salience, kind, and causal links.

**Principal state variables** (per person, all bounded and validated)

| Variable | Range | Meaning |
| --- | --- | --- |
| `brand_sentiment` | −1 … 1 | shared sentiment across campaigns |
| `recall_strength` | 0 … 1 | shared recall strength |
| `purchase_intention` | 0 … 1 | most recently applied rule-derived intention |
| `social_proof` | −1 … 1 | signed, shared word-of-mouth accumulation |
| `ad_fatigue` | 0 … 1 | shared response fatigue |
| `daily_reinforcement` | 0 … 0.20 | shared recall reinforcement bank |
| `disposable_budget` | ≥ 0 | remaining purchase budget |
| `active_need` | boolean | gate for a purchase proxy |
| `memories` | ≤ 5 retained | strongest-first episodic memory set |

These scalar states are not stored separately by campaign. Campaign-specific records
are exposure counts keyed by campaign/channel, awareness identifiers, and campaign links
on memories/events. Consequently, final shared recall, fatigue and sentiment cannot be
attributed to one campaign. The exposure-count fatigue in the attention/response formulas
below is distinct from the shared `ad_fatigue` state.

**Scales.** One tick is 15 simulated minutes; a day is 1,440 simulated minutes (`1440`),
driven as 96 ticks. Timestamps are absolute simulated minutes, so day 1 ends at minute
1,440. Runs cover 1–7 simulated days with 1–30 agents. Population size, day count, and
seed are run inputs.

## 3. Process overview and scheduling

The runner plans movement, advertising eligibility and attention, then resolves the
noticed ads' cognition requests. The engine commits movement; exposure/attention events
and counters; resolved cognition, bounded responses and memories; social propagation;
purchase proxies; daily reflection at a day boundary; and a completion event on the last
tick. Agent states, event sequence and social accumulators change only after this
in-memory commit has been fully constructed and validated. A refusal within that commit
leaves those values unchanged. Stage and event ordering use explicit stable orders.

Persistence is a separate boundary: after the in-memory commit, the runner appends events
to the authoritative store, publishes them to sinks, advances the clock, and saves a
checkpoint at a day boundary. These steps are not one all-or-nothing transaction across
the model, store and sinks; a persistence failure can leave an already committed model
or a durable event tail. A day-boundary checkpoint, not an arbitrary event tail, is the
supported resumable state.

## 4. Design concepts

- **Keyed stochasticity.** Model draws use a random oracle keyed by root seed, namespace,
  agent/pair identifier, tick (absolute simulated minute for tick decisions), and decision
  index. Run ID is not part of the key. Each key seeds a local generator; there is no
  shared mutable generator whose draw order shifts subsequent draws. This does not
  constrain a remote language model's randomness.
- **Emergence vs. intended behaviour.** Aggregate outcomes (reach, word-of-mouth chains,
  purchases) emerge from individual routines and thresholds; nothing targets an outcome.
- **Adaptation and learning.** Agents adapt through bounded state updates (sentiment,
  recall, fatigue, social proof) and memory salience; they do not learn strategies.
- **Interaction.** Word of mouth flows only across eligible explicit relationship edges,
  at most once per sender/campaign/day and once per unordered edge/campaign/day, gated by
  a share probability. Receiving a message alone does not trigger forwarding.
- **Stochasticity with audit.** Draws are recorded where they decide an outcome
  (`random_draw < probability` is validated, not implied).
- **Observation.** Events provide ordered observations and causal links; they are not a
  complete event-sourced encoding of state. State-update payloads name changed fields
  without their values. Final states/checkpoints must be read separately, or reproduced
  by re-executing the frozen scenario with the required cognition answers.

## 5. Initialization

A run starts from a `Scenario`: the world (zones and routes), a generated or supplied
population (profiles, routines, initial consumer states, relationship graph), campaigns,
and run-level `ModelParameters`. The generator draws fictional profiles, traits, interests
and an undirected connected relationship graph (for two or more people) under its seed,
using packaged names and invented distributions. Generated profiles receive routable
routine assignments. For compatibility with existing generated-study inputs, unemployed
profiles keep the historical office-worker schedule proxy without changing occupation;
explicit populations can select the separate, routable unemployed template. This is a
schedule approximation, not calibrated employment behavior.
Generated initial sentiment is `round(0.4·draw − 0.2, 4)`, not
necessarily zero. The CLI initializes sentiment from the profile; recall, intention,
fatigue, reinforcement and social proof start at zero. It leaves `active_need=False` and
`disposable_budget=0`, so ordinary CLI-created initial states cannot commit a purchase
proxy. A supplied core scenario can explicitly initialize need, budget and other bounded
states; the scenario, not the seed alone, determines those inputs.

## 6. Input data

There is no built-in real-world consumer dataset or empirical calibration. Packaged
names, routines and demo campaigns are fictional (`adlife.resources`). User-supplied
campaign/population YAML and optional remote cognition are additional inputs, not evidence
of real-world validity. YAML uses safe loading and strict schema validation; campaign
asset paths are confined beneath the project root. Provider text is untrusted and passes
bounded validation/fallback; these screens are not a guarantee against every prompt
injection or unknown secret. See the model card and limitations for that boundary.

## 7. Submodels

The formulas below describe the implemented arithmetic (module references in parentheses).
`clamp(x, lo, hi)` bounds `x`; `sigmoid(x) = 1/(1+e^(−x))`; `jaccard(A,B)=|A∩B|/|A∪B|`.
Jaccard is zero when both sets are empty. Arithmetic inputs must be finite and bounded.

### 7.1 Attention: notice probability (`policies.notice_probability`)

For agent *a* and campaign *c* on channel *ch*:

```
attention  = mobile_attention   if ch = mobile-feed
           = outdoor_attention  if ch = highway-billboard
interest   = jaccard(a.interests, c.target_interests)
fatigue    = min(1, exposure_count(a, c, ch) / placement.frequency_cap)
raw        = −1.2 + 1.5·attention + interest + 0.8·placement.visibility
                 − 0.7·advertising_skepticism − 0.5·fatigue
P_base     = clamp(sigmoid(raw), 0, 1)
P(notice)  = clamp(notice_scale · P_base, 0, 1)
noticed    ⇔ random_draw < P(notice)
```

This runs only for an eligible, allocated placement opportunity; reaching the frequency
cap suppresses further delivery. The draw is validated against the probability, never
implied. `notice_scale` is a run-level parameter (default 1.0).

### 7.2 Response to a noticed ad (`decision.evaluate_rule_response`)

```
value_match    = 0.55·interest + 0.25·novelty_seeking + 0.20·affordability
affordability  = clamp(1.25 − (price / category_reference_price)·price_sensitivity, 0, 1)
sentiment_delta = clamp(0.18·value_match − 0.12·advertising_skepticism
                        − 0.06·frequency_fatigue, −0.20, 0.20)
recall_delta   = clamp(0.22·channel_attention + 0.12·novelty_seeking
                       − 0.08·frequency_fatigue, 0, 0.30)
intention      = clamp(0.40·normalized_sentiment + 0.25·value_match
                       + 0.20·social_proof + 0.15·impulsivity, 0, 1)
share_probability = clamp(|sentiment_delta|·social_susceptibility·novelty_seeking, 0, 1)
valence        = clamp(sentiment_delta·5, −1, 1)
credibility    = clamp(1 − advertising_skepticism, 0, 1)
relevance      = interest
```

where `normalized_sentiment = (clamp(sentiment + sentiment_delta, −1, 1) + 1) / 2`.
Every resulting delta lands in a validated band (`RuleResponse`).

The baseline reads the opportunity's projected state, including prior exposure counts.
`compose_response` then applies:

```
applied_sentiment_delta = clamp((sentiment_delta + rule_modifier) · sentiment_gain, −0.20, 0.20)
applied_recall_delta    = clamp((recall_delta + rule_modifier) · recall_gain, 0, 0.30)
```

The modifier is zero in rules mode and bounded to [−0.10, 0.10] in provider answers.
Intention remains the rule baseline's value; provider-reported intention and deltas are
not substituted. The accepted share probability feeds social planning; relevance sets
advertising-memory salience. Valence and credibility are recorded but do not directly
enter the current engine's state-update formulas. Narrative text does not enter numeric
arithmetic, although retained summaries can be context for later remote cognition.

`apply_response` adds the composed sentiment delta with a [−1, 1] clamp, replaces
`purchase_intention` with the baseline intention, adds 0.10 to `ad_fatigue` with a [0, 1]
clamp, marks campaign awareness, and banks recall reinforcement:

```
projected_recall = clamp(recall_strength + daily_reinforcement, 0, 1)
headroom        = max(0, 0.20 − daily_reinforcement)
reinforcement   = min(applied_recall_delta · (1 − projected_recall), headroom)
daily_reinforcement ← clamp(daily_reinforcement + reinforcement, 0, 0.20)
```

### 7.3 Memory encoding and decay (`memory`)

Episodic memories are encoded per noticed ad and per committed purchase proxy; the
strongest five are retained (ranked by salience, then recency, then identifier).
At each day boundary, reflection applies the bank once, then resets it:

```
recall_strength ← clamp(recall_strength · recall_retention + daily_reinforcement, 0, 1)
ad_fatigue      ← clamp(ad_fatigue · ad_fatigue_retention, 0, 1)
daily_reinforcement ← 0
older memory salience ← clamp(salience · salience_retention, 0, 1)
```

Default retentions are 0.85, 0.60 and 0.85 respectively. Memories created during the day
just completed are not decayed at that boundary; only older memories are. The engine
creates advertising and committed-purchase memories, not a separate memory on every
social receipt or reflection. Daily reinforcement is diminished recall gain from direct
responses and social receipts, not sentiment movement; both sources share the
`MAX_DAILY_REINFORCEMENT = 0.20` bank. Neither source immediately changes recall strength.

### 7.4 Word of mouth and social proof (`social`)

A fresh noticed ad gives the noticer a share opportunity. Both endpoints must be awake
and either co-located after this tick's movement or connected by an `online` relationship.
Eligible neighbors exclude edges already used by that campaign that day. The receiver
is selected by a keyed draw; with `w = social_similarity_weight`, each receiver's weight
is `(1 − w) + w·jaccard(sender.interests, receiver.interests)`. The default `w=0` is
uniform over eligible neighbors. If `w=1` and every similarity is zero, no receiver has
a supported preference: selection falls back to uniform over eligible neighbors using
the same keyed draw, without an additional random call.

```
P(share) = clamp(cognition_share_probability · share_probability_scale · edge_strength, 0, 1)
shared   ⇔ random_draw < P(share)
```

Stable acceptance permits at most one message per sender/campaign/day and one traversal
per unordered edge/campaign/day. Social receipt alone does not schedule a new share.
Message valence is the sender's pre-response `brand_sentiment` from the movement-projected
snapshot, not this tick's provider valence. For the receiver, with its susceptibility:

```
shift = valence · edge_strength · social_susceptibility · social_proof_gain
social_proof ← clamp(social_proof + shift, −1, 1)
projected_recall = clamp(recall_strength + daily_reinforcement, 0, 1)
reinforcement = min(social_recall_gain · edge_strength · social_susceptibility
                    · (1 − projected_recall), max(0, 0.20 − daily_reinforcement))
daily_reinforcement ← clamp(daily_reinforcement + reinforcement, 0, 0.20)
```

Defaults are `social_proof_gain=0.25`, `social_recall_gain=0.10` and
`share_probability_scale=1.0`. Receipt also marks awareness, without incrementing direct
exposure counts or directly updating sentiment, intention or recall strength.

### 7.5 Purchase proxy (`decision`, rule-only)

The engine considers a campaign only on a tick when that agent noticed its ad. When
`purchase_intention ≥ PURCHASE_INTENTION_THRESHOLD = 0.70` and the agent has an active
need and sufficient budget, a purchase proxy may commit:

```
P(commit) = purchase_intention      committed ⇔ random_draw < P
```

A committed proxy decrements the budget by the campaign price, clears active need, records a
`purchase-proxy` episodic memory, and emits the state-change events with causes.
The standalone decision returns refusal reasons (`no-active-need`, `budget-below-price`,
`intention-below-threshold`, `draw-above-intention`), but the engine does not emit an event
for uncommitted proxies. **No language-model output directly supplies purchase
probability.** Bounded provider influences can affect subsequent state and therefore
later rule-derived intention; this is not numerical independence from cognition.

### 7.6 Run-level parameters (`parameters.ModelParameters`)

Ten quantities are bounded run inputs: `notice_scale`, `sentiment_gain`, `recall_gain`,
`recall_retention`, `ad_fatigue_retention`, `salience_retention`,
`share_probability_scale`, `social_proof_gain`, `social_recall_gain`, and
`social_similarity_weight`. The three multipliers for notice/sentiment/recall default to
1.0; other defaults appear above. Formula coefficients, delta bands, the 0.70 purchase
threshold, 0.20 daily cap and five-memory limit are fixed, not exposed knobs. Sensitivity
analysis perturbs one exposed parameter at a time and records each arm's actual bounded
parameters in its manifest; a relative perturbation of the default zero similarity
weight remains zero (see the experiment protocol).
