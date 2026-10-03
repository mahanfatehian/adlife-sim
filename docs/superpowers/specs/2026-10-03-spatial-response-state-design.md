# Spatial Response and Campaign-State Design

**Status:** Approved autonomous roadmap refinement for C3c

**Parent roadmap:**
`docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

Schema-v5 city runs stop at deterministic synthetic opportunity, impression and notice
evidence. C3c adds the smallest honest response layer after that boundary: an explicit,
versioned input freezes fictional response traits and campaign assumptions; one pure
rules model converts only persisted notices into bounded response evidence and
campaign-scoped state updates; schema-v6 artifacts freeze and replay the result.

This slice is not a calibrated model of residents, sales or real-world advertising
effectiveness. It adds no provider call, language-model output, memory, social
propagation, purchase event, budget mutation or movement decision. It does not complete
C3. Its claim scope is `synthetic-response-not-observed-behavior`.

## Alternatives considered

1. **Separate, explicit spatial-response input and a new rule model (selected).** This
   freezes every numeric assumption and avoids fabricating the zone simulator's richer
   persona, campaign, social and budget fields.
2. **Reuse the zone `PersonProfile`, `Campaign` and `ConsumerState` contracts.** Rejected:
   their channel names, fatigue units, shared per-person state and purchase/social fields
   do not match geographic campaign evidence. Adapting missing fields silently would make
   the model look more grounded than it is.
3. **Ask an LLM to interpret campaign copy.** Deferred. Provider-derived cognition needs
   a separately specified, bounded and replayable contract; it must never create events,
   move agents, mutate budgets or supply purchase probability.
4. **Add repeated-seed reporting first.** Valid but lower leverage: current spatial
   comparisons can measure only opportunity/attention structure. C3c creates the response
   evidence later C4 work can aggregate without inventing outcome semantics.

## Versioned response input

`core/domain/spatial_response.py` owns a strict schema-version-1 document. It contains:

- `city_sha256` and `scenario_sha256` bindings;
- one canonical fictional `SpatialResponseProfile` for every city agent;
- one canonical `SpatialCampaignResponseInput` for every scenario campaign;
- one canonical `SpatialResponseInitialState` for every agent/campaign pair.

Profiles contain only an agent ID, `fictional: true`, one to twelve bounded interests and
six `[0, 1]` rule traits: price sensitivity, novelty seeking, advertising skepticism,
mobile recall encoding, roadside recall encoding and impulsivity. The two encoding fields
act only after the fixed attention model has already emitted a notice; they are not a
second notice probability. The contract intentionally omits names, demographics and MBTI
labels. These traits are transparent synthetic assumptions, not inferred facts about
residents.

Campaign response inputs bind the exact campaign ID and creative SHA-256, one to twelve
target interests and a dimensionless `relative_price` in `(0, 100]`. `relative_price`
means advertised price divided by an analyst-declared category reference price; no
currency or budget is modeled in this slice. Campaign name, copy and creative bytes are
not numeric inputs.

Initial state is campaign-scoped and contains agent ID, campaign ID, brand sentiment in
`[-1, 1]`, recall strength in `[0, 1]` and purchase intention in `[0, 1]`. Purchase
intention is a synthetic state proxy, not a transaction probability. Supplying a bounded
initial condition is allowed; after a notice, only the deterministic rule may update it.

Collections are sorted canonically. Agent IDs must exactly equal the generated city
population, campaign IDs and creative hashes must exactly equal the spatial scenario, and
initial states must form the exact agent-by-campaign Cartesian product. Missing, extra,
duplicate or mismatched entries fail before a run ID is reserved. Unknown fields,
non-finite numbers, booleans where numbers/integers are intended, duplicate JSON keys,
unsupported schema versions, sensitive persona text and oversized documents are refused.
The document fingerprint is SHA-256 over canonical UTF-8 JSON.

The response input deliberately binds the exact agent-ID cohort rather than one mobility
assignment fingerprint. This permits the same fictional profiles and initial state to be
held constant across repeated-seed mobility runs. Each saved run still freezes and hashes
its exact generated home/work/leisure assignments independently, and matched comparison
rules continue to reject assignment drift inside one treatment/control pair.

## Deterministic response rule

`core/simulation/spatial_response.py` is pure and adapter-free. Its response model ID is
`spatial-response-v1`. Its only causal inputs are the validated response document,
matching spatial scenario, matching opportunity and attention evaluations and the exact
generated agent IDs. It filters `spatial.noticed` events; impressions that were ignored
and opportunities without a notice cause no response or state transition.

For one agent/placement/day, `prior_notices_today` counts notices committed in earlier
model minutes. Its denominator is that placement's existing
`frequency_cap_per_agent_per_day`; the unit is therefore an exact prior-notice fraction of
the declared daily opportunity cap, never a lifetime exposure count. Notices in the same
minute all read the same immutable pre-minute state and the same prior daily count. They do
not fatigue one another until the minute commits. The count resets by day because it is
keyed by `day_index`; no unrecorded state mutation is needed.

For each noticed event, the model derives:

```text
interest_match = Jaccard(profile interests, campaign target interests)
affordability = clamp(1.25 - relative_price * price_sensitivity, 0, 1)
frequency_fatigue = min(1, prior_notices_today / frequency_cap_per_agent_per_day)
value_match = 0.55 * interest_match + 0.25 * novelty_seeking + 0.20 * affordability
sentiment_delta = clamp(
    0.18 * value_match - 0.12 * advertising_skepticism - 0.06 * frequency_fatigue,
    -0.20,
    0.20,
)
recall_delta = clamp(
    0.22 * channel_recall_encoding + 0.12 * novelty_seeking
    - 0.08 * frequency_fatigue,
    0,
    0.30,
)
```

`channel_recall_encoding` selects the profile's mobile or roadside encoding assumption
after a notice has already occurred. It cannot create or suppress a notice. Each
`spatial.response` record carries the exact notice ID, opportunity/campaign/placement/
agent/channel/time evidence and the bounded intermediate scores above. Its ID is SHA-256
over model identity, event type and causal notice ID.

At the end of an agent/campaign/model-minute group, the model commits one
`spatial.state-updated` record. It sums planned sentiment deltas, applies recall
gain against the minute's shared headroom, and derives the new intention:

```text
sentiment_after = clamp(sentiment_before + sum(sentiment_delta), -1, 1)
recall_after = 1 - (1 - recall_before) * product(1 - recall_delta)
purchase_intention_after = clamp(
    0.40 * ((sentiment_after + 1) / 2)
    + 0.25 * value_match
    + 0.20 * recall_after
    + 0.15 * impulsivity,
    0,
    1,
)
```

The update records the complete previous and next campaign-scoped states plus every
causal response ID in canonical order. Cumulative notice count increases by the group
size. No response record can name an agent, campaign, placement, time or cause that was
not present in the validated attention stream. Campaign copy cannot alter any formula.

Records are canonically ordered by minute, agent, campaign, source notice evidence and
record kind, with the state update after all responses in its group. Input permutations,
agent iteration order, dictionary/set order and `PYTHONHASHSEED` cannot alter canonical
bytes. The model uses no wall clock, ambient random generator or network.

## Artifact and schema-v6 contract

Response runs are explicitly activated by both `--spatial-campaign SCENARIO.json` and
`--spatial-response RESPONSE.json`. A spatial campaign without the response flag retains
the exact schema-v5 contract and bytes. A response input without a spatial campaign is an
input error.

Schema v6 extends, but does not reinterpret, every v5 receipt. It uses model ID
`illustrative-road-spatial-response-study-v1` and additionally freezes:

```text
inputs/spatial-response.json
outputs/spatial-responses.jsonl
outputs/response-state.json
outputs/response-summary.json
```

The manifest binds the response input, stream, final-state document and summary by exact
SHA-256; it records response schema version, response model ID, stream byte count, response
count, state-update count, campaign count and final-state count. Exactly
one response must exist per notice. Final state count must equal population times response
campaign count. State updates are zero exactly when responses are zero and otherwise are
between one and the response count.

The canonical JSONL stream contains at most 1,041,600 records (one response per possible
notice plus at most one state update per response) and at most 2 GiB. Response input is
bounded to 2 MiB, the final-state document to 4 MiB, and the summary and manifest to 64
KiB. Records contain no free-form campaign copy, provider body or credential.

The final-state document carries all canonical campaign-scoped states and the exact input,
city and scenario fingerprints. The summary carries counts, model/claim identity, the
stream hash/bytes and final-state document hash. Hashes describe exact canonical bytes,
including the newline for one-document files where the store contract already does so.

Publication remains no-clobber and manifest-last. All inputs and outputs are written and
verified before `run.json` is atomically published. A failed write leaves an incomplete,
non-loadable directory whose ID is never reused. Load refuses missing, appended,
truncated, reordered, noncanonical, oversized, hash-mismatched or symlinked response
artifacts. Schemas v1-v5 remain readable and byte-compatible and never fabricate response
evidence.

Replay regenerates mobility, opportunities, attention, responses and final state from the
frozen inputs, compares the exact evaluations and artifact receipts, and never repairs or
mutates the source run.

## CLI and read-only viewer

`adlife city-run` adds only:

```text
--spatial-response RESPONSE.json
```

Human and JSON results expose response model/claim identity, response/state-update/final-
state counts and the four response artifact hashes/stream byte count. Invalid or
scientifically mismatched input exits 2 before reservation; duplicate IDs remain 3;
corrupt saved artifacts remain 4; unexpected defects remain 1; interruption remains 130.
Diagnostics never echo document contents or credentials and JSON stdout stays exactly one
machine-readable document.

`city-replay` exposes the same verified response receipts. No response-specific write or
repair command is added.

The existing loopback `city-view` may receive a fully prevalidated schema-v6 evaluation.
It adds a read-only summary, bounded minute/agent response page, and final campaign-state
view. The browser shows direct response evidence and bounded state proxies alongside the
timeline using text-only DOM construction. It does not derive formulas in JavaScript,
choose a new artifact, mutate the run, claim a purchase, or expose arbitrary paths.
Legacy runs receive 404 for response resources. All HTTP methods that would mutate state
remain unavailable.

Spatial self-contained reports and repeated-seed uncertainty remain C4b. Provider settings,
OAuth, authentication, multi-user workspaces and remote writes remain D/E work and must
not be smuggled into this local scientific slice.

## Security, scientific and compatibility boundaries

- Response inputs describe synthetic fictional agents only and carry no real resident
  identity.
- No provider, API credential, prompt, raw response, cache or outbound request enters the
  model or artifacts.
- No campaign text is evaluated numerically; changing name/copy/creative hash alone does
  not change numeric response values.
- The model cannot create movement, opportunity, impression, notice, social or purchase
  events and cannot mutate money/budget.
- A response cannot bypass bounds or inject agent, campaign, placement, time or cause IDs.
- Purchase intention is a bounded internal proxy, not purchase probability, sales or a
  committed transaction.
- The parameters are transparent illustrative assumptions and are not calibrated to any
  city, person, brand or population.
- The adapter-free core boundary and offline default remain unchanged.

## Acceptance evidence

- parser/model tests pin canonicalization, exact bindings, Cartesian state coverage,
  sensitive-text refusal, strict numbers, bounds, duplicate keys and size limits;
- golden rule tests pin every intermediate score, delta, state update and causal ID;
- ignored impressions produce zero responses; every notice produces exactly one response;
- same-minute multi-notice groups read one snapshot and commit once, invariant to input
  order and hash seed;
- day changes reset the fatigue counter without changing campaign state invisibly;
- hostile/tampered records cannot inject IDs, causes, movement, budgets or arbitrary state;
- changing only campaign name/creative identity preserves numeric outcomes;
- state always remains finite and within bounds under maximum repeated notice load;
- schema-v6 store fault, corruption, symlink, no-clobber and manifest-last tests pass;
- replay reproduces exact canonical response evidence and preserves every source byte;
- schema-v1-v5 compatibility remains green and ordinary spatial runs remain v5;
- CLI/API/browser tests prove exact output, clean failures, pagination, read-only behavior,
  safe Persian/hostile text and no network;
- Ruff, strict mypy, full/hash-seed/coverage suites, wheel/frozen smoke and the spatial
  performance gate pass.

## Deferred work

- C3 follow-up: bounded optional cognition, daily reflection/memory, social propagation
  and a separately specified rule-owned purchase proxy for spatial studies.
- C4b: repeated-seed orchestration, simulator-seed uncertainty and self-contained spatial
  reports using response metrics once specified.
- D/E: catalog workbench, write jobs, authentication, authorization and managed provider
  secrets.
- F: lawful calibration/holdout data, parameter fitting, external validation and any
  predictive claim.
