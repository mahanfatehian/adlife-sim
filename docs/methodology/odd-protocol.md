# ODD protocol — AdLife Lab

This document describes the model under the ODD (Overview, Design concepts, Details)
protocol, the standard reporting format for agent-based models. Formulas are recorded
exactly as implemented; where this document and the code disagree, the code is the bug.

## 1. Purpose

AdLife Lab simulates a small fictional society of consumers who follow daily routines,
move between zones, encounter advertising through two channels (a mobile feed and
highway billboards), talk to people they know, and update recall, brand sentiment, and
purchase intention accordingly. It exists to study *mechanisms* — how attention, memory
decay, fatigue, and word of mouth shape campaign outcomes under controlled, fully
reproducible conditions — and to serve as a research and teaching instrument.

It is explicitly **not** a forecasting tool for real markets. All agents are fictional;
results are synthetic and exploratory.

## 2. Entities, state variables, and scales

**Entities**

- **Person** — one fictional consumer: a profile (traits, interests, budget, routine) and
  a mutable consumer state.
- **Campaign** — an advertised product: placements (mobile-feed and/or billboard),
  target interests, price, category reference price, frequency caps, creative assets.
- **World** — ten zones connected by named routes; the geography agents commute across.
- **Relationship** — a directed social edge of kind `friend`, `colleague`, `family`, or
  `online`, with a strength in [0, 1].
- **Memory** — one episodic record with salience, kind, and causal links.

**Principal state variables** (per person, all bounded and validated)

| Variable | Range | Meaning |
| --- | --- | --- |
| `brand_sentiment` (per campaign) | −1 … 1 | affect toward the brand |
| `recall` (per campaign) | 0 … 1 | unprompted recall strength |
| `purchase_intention` (per campaign) | 0 … 1 | rule-derived intention |
| `social_proof` (per campaign) | 0 … 1 | word-of-mouth accumulation |
| `ad_fatigue` (per campaign/channel) | 0 … 1 | frequency fatigue |
| `budget` | ≥ 0 | remaining purchase budget |
| `memories` | ≤ 5 retained | strongest-first episodic memory set |

**Scales.** One tick is one simulated minute; a day is 1,440 minutes (`1440`). Runs cover 1–7
simulated days with 1–30 agents. Population size, day count, and seed are run inputs.

## 3. Process overview and scheduling

The engine commits one tick through a fixed, atomic nine-stage order: movement;
advertising eligibility and attention (planned); cognition resolution; response and
memory encoding; social propagation; the purchase proxy; daily reflection on a day
boundary; checkpoint persistence; completion on the last tick. Nothing is applied to an
agent or the sequence counter until every stage has minted — a refusal anywhere leaves
the tick entirely uncommitted. Stage order and event ordering are total orders, not
iteration orders.

## 4. Design concepts

- **Keyed stochasticity.** Every draw comes from a random oracle keyed by run, agent, and
  purpose; there is no global generator, so evaluation order cannot change outcomes.
- **Emergence vs. intended behaviour.** Aggregate outcomes (reach, word-of-mouth chains,
  purchases) emerge from individual routines and thresholds; nothing targets an outcome.
- **Adaptation and learning.** Agents adapt through bounded state updates (sentiment,
  recall, fatigue, social proof) and memory salience; they do not learn strategies.
- **Interaction.** Word of mouth flows only across explicit relationship edges, one
  message per edge per tick, gated by a share probability.
- **Stochasticity with audit.** Draws are recorded where they decide an outcome
  (`random_draw < probability` is validated, not implied).
- **Observation.** The event stream is the observation surface: every state change emits
  causally linked events, and final state is a fold over the stream.

## 5. Initialization

A run starts from a `Scenario`: the world (zones and routes), a generated or supplied
population (profiles, routines, initial consumer states, relationship graph), campaigns,
and run-level `ModelParameters`. The generator draws personas, interests, budgets,
routines, and a connected relationship graph from packaged fictional templates under the
run seed. Generated profiles receive keyed, routable routine assignments. Initial states
are zero-valued (no sentiment, recall, fatigue, or social proof) with seeded budgets.

## 6. Input data

No external or real-world data enters the model. Personas, names, routines, and the demo
campaigns come from packaged fictional templates (`adlife.resources`). User-supplied
campaign YAML and population YAML are treated as untrusted input: safe YAML loading,
strict schema validation, asset paths confined beneath the project root.

## 7. Submodels

All formulas below are exactly as implemented (module references in parentheses).
`clamp(x, lo, hi)` bounds `x`; `sigmoid(x) = 1/(1+e^(−x))`; `jaccard(A,B)=|A∩B|/|A∪B|`.

### 7.1 Attention: notice probability (`policies.notice_probability`)

For agent *a* and campaign *c* on channel *ch*:

```
attention  = mobile_attention   if ch = mobile-feed
           = outdoor_attention  if ch = billboard
interest   = jaccard(a.interests, c.target_interests)
fatigue    = min(1, exposure_count(a, c, ch) / placement.frequency_cap)
raw        = −1.2 + 1.5·attention + interest + 0.8·placement.visibility
                 − 0.7·advertising_skepticism − 0.5·fatigue
P(notice)  = clamp(sigmoid(raw), 0, 1)
noticed    ⇔ random_draw < P(notice)
```

The draw is validated against the probability, never implied. The campaign-level knob
`notice_scale` scales this probability before its final clamp.

### 7.2 Response to a noticed ad (`decision.rule_response`)

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
```

where `normalized_sentiment = (clamp(sentiment + sentiment_delta, −1, 1) + 1) / 2`.
Every resulting delta lands in a validated band (`RuleResponse`).

### 7.3 Memory encoding and decay (`memory`)

Episodic memories are encoded per noticed ad and per committed purchase proxy; the
strongest five are retained (ranked by salience, then recency, then identifier).
At each day boundary, retention multiplies the tracked quantities:

```
recall          ← recall          · RECALL_RETENTION       (0.85)
ad_fatigue      ← ad_fatigue      · AD_FATIGUE_RETENTION   (0.60)
memory salience ← salience        · SALIENCE_RETENTION     (0.85)
```

Daily reinforcement (summed positive sentiment/recall movement per day) is capped at
`MAX_DAILY_REINFORCEMENT = 0.20`.

### 7.4 Word of mouth and social proof (`social`)

A noticed ad gives the noticer a share opportunity: one message across one relationship
edge per tick, sent when `random_draw < share_probability`. The receiver's update:

```
social_proof ← clamp(social_proof + SOCIAL_PROOF_GAIN·(0.5 + 0.5·edge_strength)·valence_norm, 0, 1)
recall       ← clamp(recall     + SOCIAL_RECALL_GAIN·(0.5 + 0.5·edge_strength)·strength_norm, 0, 1)
```

with `SOCIAL_PROOF_GAIN = 0.25` and `SOCIAL_RECALL_GAIN = 0.10`. The receiver edge is
drawn uniformly over the sender's relationships; `social_similarity_weight` mixes in an
interest-similarity preference over the same keyed draw (0.0 restores the uniform draw).
`share_probability_scale` scales the sender's share probability.

### 7.5 Purchase proxy (`decision`, rule-only)

When `purchase_intention ≥ PURCHASE_INTENTION_THRESHOLD = 0.70` and the agent has an
active need and sufficient budget, a purchase proxy may commit:

```
P(commit) = purchase_intention      committed ⇔ random_draw < P
```

A committed proxy decrements the budget by the campaign price, records a
`purchase-proxy` episodic memory, and emits the state-change events with causes.
Uncommitted proxies record their reason (`no-active-need`, `budget-below-price`,
`intention-below-threshold`, `draw-above-intention`). **No language-model output ever
supplies purchase probability.**

### 7.6 Run-level parameters (`parameters.ModelParameters`)

Every constant above is exposed as a bounded, documented run input — `notice_scale`,
`sentiment_gain`, `recall_gain`, the three retention values, `share_probability_scale`,
the social gains, and `social_similarity_weight` — with the documented values as
defaults, so sensitivity analysis perturbs one knob at a time and every arm's manifest
records exactly what ran.
