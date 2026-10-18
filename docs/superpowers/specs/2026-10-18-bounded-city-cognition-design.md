# Bounded City Cognition Design

**Date:** 2026-10-18

**Status:** Approved design

**Scope:** Provider-selected, bounded synthetic cognition signals for saved city runs

## Intent

AdLife's city workbench can already call a local or remote OpenAI-compatible provider,
but manifest-V9 provider output is deliberately noncausal prose generated after the
deterministic response state has been computed. This design adds an explicit mode in
which a real provider selects a closed set of synthetic cognition categories before the
response layer is finalized.

The intended product behavior is:

> A configured external model may select bounded appraisal categories for a fictional
> agent's noticed advertisement. AdLife, not the model, maps those categories through a
> frozen policy and updates a separate synthetic cognition state. Movement, opportunities,
> attention, baseline event identity and population, budgets, purchase intention, and
> purchases remain rule-owned.

This makes provider inference part of the modeled cognition path without granting an
untrusted model arbitrary state or numeric authority. It does not claim to reproduce real
human psychology, predict an individual, validate MBTI, or predict sales.

## Current behavior and compatibility

City run manifest V9 and workbench input schema 3 remain unchanged. Their
`CityCognitionResultV1` is narrative-only and continues to explain an already-computed
response. Existing V1-V9 artifacts retain their exact parsing, validation, replay, metrics,
reporting, and UI semantics.

The new behavior uses:

- workbench input schema 4;
- city run manifest V10;
- spatial response model `spatial-response-v2`;
- bounded cognition request/result/input/evaluation contracts version 2;
- influence policy `city-cognition-influence-v1`.

There is no in-place V9-to-V10 migration. A historical V9 run never gains invented
influence. An operator may create a new V10 run from equivalent user inputs, but it has a
new run identifier and its own provenance.

## Approaches considered

### Integrated dual-state response model — chosen

AdLife first computes deterministic response proposals, resolves the selected cognition
signals, and then commits one response group containing both a rule-owned state and a
provider-influenced cognition state. This is the only approach that places the provider at
the cognition stage while structurally proving that purchase intention does not read
provider-derived state.

### Post-run cognition projection

A projection after the existing response evaluation would preserve more V9 code and be
smaller, but it would remain an annotation layer rather than part of the response commit.
It is not chosen because the product requirement is an actual bounded AI cognition mode.

### Legacy numeric cognition reuse

The legacy zone-engine `CognitionResult` accepts provider-authored numeric modifiers and
sharing probabilities. Reusing it would couple two different models, allow more authority
than this design needs, and permit later rule behavior to read provider-influenced state.
It is explicitly rejected.

## User-visible modes

The workbench distinguishes three concepts rather than calling every provider feature
"AI-powered":

1. **Rules** — no external call; both state tracks use the deterministic rule result.
2. **AI explanation** — existing schema-3/V9 behavior; a provider may explain completed
   rule evidence but cannot change it.
3. **Bounded AI cognition** — schema-4/V10 behavior; a provider selects closed cognition
   categories and the core applies the frozen influence policy.

Mock mode remains available as an offline test/demo source for bounded cognition. It uses
deterministic content-addressed signals and never makes a network call. Rules mode is the
credential-free default.

The UI never describes provider output as private thoughts, chain-of-thought, observed
behavior, validated psychology, or a prediction. The preferred label is **bounded AI
cognition signals**.

The exact version/mode matrix is:

| Effect scope | Source | Workbench / manifest | Provider budget | Compatibility |
| --- | --- | --- | --- | --- |
| `explanation-only` | rules or mock | schema 3 / V9 | none | not applicable |
| `explanation-only` | local or remote | schema 3 / V9 | existing V9 policy | exact V9 explanation check |
| `bounded-signals-v1` | rules or mock | schema 4 / V10 | none; all selected requests resolve locally | not applicable |
| `bounded-signals-v1` | local or remote | schema 4 / V10 | 10, 20, 40, or 60 funded requests | exact V10 signal check required |

The new UI never infers a schema from the presence of a profile. Effect scope and source
jointly select the exact schema. V9 rules/mock behavior therefore remains available, while
V10 rules/mock provides an offline identity/mock path through the new dual-state model.

## Scientific boundary

### Provider-influenced fields

Only the V10 cognition track may differ because of provider output:

- applied synthetic sentiment delta;
- applied synthetic recall delta;
- cumulative synthetic cognition sentiment;
- cumulative synthetic cognition recall;
- categorical cognition counts and coverage.

### Rule-owned fields

The following never read provider output or provider-influenced state:

- mobility and route selection;
- places, schedules, and activities;
- campaign opportunity creation;
- attention and notice decisions;
- response eligibility;
- deterministic baseline sentiment and recall;
- purchase-intention proxy;
- budgets and monetary values;
- baseline event identity, event eligibility, and event count;
- agent, campaign, placement, city, and run identity;
- any future purchase transaction unless a later version explicitly specifies a separate
  rule-owned purchase model.

The simulator still has no purchase transaction model in this slice. Purchase intention
remains an uncalibrated rule-owned proxy, not a probability, sale, or forecast.

### Persona boundary

The provider request may include only fields already declared as causal scenario inputs:

- interests;
- price sensitivity;
- novelty seeking;
- advertising skepticism;
- impulsivity;
- channel-specific recall encoding;
- bounded initial rule state;
- bounded campaign and creative inputs;
- channel, time, frequency, and deterministic rule factors.

Display name, age, occupation, biography, and MBTI remain descriptive and are omitted from
the causal signal prompt. They may continue to appear in the saved-run identity panel and
in the existing noncausal V9 explanation mode. Treating those labels as causal would
require a separate empirical and ethics protocol.

## Bounded result contract

`CityCognitionResultV2` contains exactly:

| Field | Contract |
| --- | --- |
| `schema_version` | exact integer `2` |
| `request_id` | exact content-addressed request identifier supplied by AdLife |
| `affect` | `annoyed`, `skeptical`, `neutral`, `curious`, `positive`, or `uncertain` |
| `relevance` | `none`, `low`, `medium`, `high`, or `uncertain` |
| `credibility` | `low`, `medium`, `high`, or `uncertain` |
| `salience` | `none`, `low`, `medium`, `high`, or `uncertain` |
| `safety_flags` | unique subset of `stereotype-risk`, `unsupported-inference`, and `unsafe-content` |
| `disclosure` | exact literal `synthetic-bounded-cognition-not-observed-behavior` |

There is no provider prose and no provider-authored number. Unknown fields are rejected.
The provider cannot return agent, campaign, placement, event, city, state, movement,
budget, probability, purchase, memory document, prompt, URL, credential, or filesystem
fields. A mismatched request ID is invalid.

The adapter accepts a result only when the outer protocol is valid, the strict JSON schema
is satisfied, duplicate JSON keys are absent, the response is within the wire and decoded
bounds, and `finish_reason` is exactly `stop`. Refusal, content filtering, truncation,
tools, streaming, arrays of content parts, malformed JSON, non-finite values, or any extra
field produce a typed invalid-result outcome.

If any category is `uncertain`, or if `safety_flags` is nonempty, the record remains
auditable but the complete applied vector becomes the neutral vector. The provider cannot
partially apply a result.

## Surrounding V2 contracts

The result is only one part of the authority boundary. The V2 family has these exact
structural responsibilities.

### Request V2

`CityCognitionRequestV2` contains:

- exact schema version and content-addressed request ID;
- run-input, response-input, scenario, and city hashes;
- core-owned baseline response, notice, opportunity, minute-group, agent, campaign, and
  placement bindings;
- channel, day, minute, and millisecond;
- pre-minute rule-state and cognition-state hashes;
- deterministic baseline proposal and its hash;
- the minimized causal profile, campaign creative, frequency, and rule factors described
  in the persona boundary;
- the exact synthetic disclosure.

Its request ID is the SHA-256 content address of every field except the ID itself. The
adapter builds a minimized wire projection that retains the request ID and causal facts but
omits internal identities and all descriptive persona fields. The complete internal request
is frozen and validated; only the minimized projection crosses the provider boundary.

### Terminal record V2

`CityCognitionTerminalRecordV2` contains:

- selection index, request ID/hash, run and scientific input hashes;
- baseline response, minute-group, notice, opportunity, agent, campaign, placement, and
  time bindings copied from the request;
- source in `rules`, `mock`, `provider`, `cache`, `fallback`, or `budget-skipped`;
- one validated `CityCognitionResultV2`;
- attempt count, bounded fallback reason, and bounded usage;
- complete non-secret provider/profile/settings binding when the source belongs to a
  provider-mode run;
- prompt, result-schema, selection, fallback, mock-policy, and influence-policy hashes;
- record hash over all preceding facts and the exact disclosure.

The source matrix is closed:

| Source | Attempts | Reason | Provider binding | Result authority |
| --- | ---: | --- | --- | --- |
| rules | 0 | absent | absent | exact neutral vector |
| mock | 0 | absent | absent | exact deterministic mock vector |
| provider | 1-4 | absent | complete | validated provider vector |
| cache | 0 | absent | complete | validated cached provider vector |
| fallback | 0-4 | exact non-budget reason | complete | exact neutral vector |
| budget-skipped | 0 | `budget-skipped` | complete | exact neutral vector |

### Applied response V2

Each `SpatialAppliedResponseV2` embeds one revalidated `SpatialRuleResponse` as its
`baseline`, plus:

- a core-derived applied-evidence ID;
- the influence-policy hash;
- either one terminal-record hash or the exact `unselected-neutral` marker, never both;
- source and neutralization status;
- applied sentiment and recall deltas.

Neutralization status is exactly `none`, `uncertain`, `safety`, `rules`, `fallback`,
`budget-skipped`, or `unselected`. The cross-field validator proves the status, source,
terminal binding, and applied values agree.

### Dual-state update V2

Each `SpatialDualStateUpdatedV2` contains the canonical applied-response cause IDs, the
complete spatial provenance, identity and minute, previous and committed rule state, and
previous and committed cognition state. It has exactly one record per nonempty
`(model_minute, agent_id, campaign_id)` group. Its ID binds both pre-states, ordered causes,
and both committed states.

### Input, evaluation, and summary V2

`CityCognitionInputV2` freezes source/effect scope, all complete non-secret provider
metadata, effective settings, every contract/policy document and hash, funded-response
limit, hard transport limits, and the canonical reservations. `CityCognitionEvaluationV2`
contains the ordered terminal records and summary and binds the input/run hashes.

The summary contains eligible, selected, funded, budget-skipped, unselected, source,
fallback, uncertain, safety-neutralized, attempt, usage, byte, record, and stream-hash
counts. Cross-field validators enforce the cardinality equations defined below.

## Frozen influence policy

The neutral vector is:

```text
affect=neutral
relevance=medium
credibility=medium
salience=medium
```

It is an exact identity over the deterministic baseline.

For a fully applicable vector, `city-cognition-influence-v1` uses these immutable tables:

| Affect | Adjustment |
| --- | ---: |
| annoyed | -0.04 |
| skeptical | -0.02 |
| neutral | 0.00 |
| curious | 0.02 |
| positive | 0.04 |

| Relevance | Factor |
| --- | ---: |
| none | 0.50 |
| low | 0.75 |
| medium | 1.00 |
| high | 1.15 |

| Credibility | Factor |
| --- | ---: |
| low | 0.75 |
| medium | 1.00 |
| high | 1.10 |

| Salience | Factor |
| --- | ---: |
| none | 0.50 |
| low | 0.75 |
| medium | 1.00 |
| high | 1.15 |

Given the deterministic rule proposal `s` and `r`:

```text
applied_sentiment_delta = clamp(
  (s + affect_adjustment) * relevance_factor * credibility_factor,
  -0.20,
  0.20,
)

applied_recall_delta = clamp(
  r * relevance_factor * credibility_factor * salience_factor,
  0.00,
  0.30,
)
```

The policy document, canonical hash, table values, formula identifier, clamp bands, and
neutralization rule are frozen into the V10 input and manifest receipt. A later change
requires a new policy version and artifact version; it is never silently substituted when
replaying V10.

These values are explicit synthetic assumptions, not calibrated psychological effects.

## Dual-state response model

`spatial-response-v2` keeps two state tracks for every agent/campaign pair:

### Rule state

The rule state has the existing V1 fields and equations:

- rule brand sentiment;
- rule recall strength;
- rule purchase intention;
- response count;
- last response minute.

It is computed from deterministic baseline responses only. Given the same V9-compatible
scientific inputs, its numeric values match the V1 rule model exactly.

### Cognition state

The cognition state contains:

- agent and campaign identity copied from validated inputs;
- synthetic cognition sentiment in `[-1, 1]`;
- synthetic cognition recall in `[0, 1]`;
- applied response count;
- last applied response minute.

It has no purchase-intention, probability, budget, movement, or arbitrary data field.

Both tracks begin from the same bounded initial sentiment and recall. The rule track folds
baseline deltas. The cognition track folds applied deltas. Recall uses the existing
retained-headroom equation rather than direct addition.

Every eligible baseline response also produces one applied V2 response, including
unselected, rules, fallback, budget-skipped, uncertain, and safety-neutralized identity
cases. It therefore increments the cognition response count, and the group's minute becomes
the last applied response minute. For every final agent/campaign state, cognition response
count and last-response presence exactly match the rule state; only sentiment and recall
may differ.

### Atomic minute groups

All noticed responses sharing `(model_minute, agent_id, campaign_id)` read one immutable
pre-minute rule state and one immutable pre-minute cognition state. AdLife:

1. computes the deterministic baseline proposals;
2. constructs and selects content-addressed cognition requests;
3. resolves one terminal result for every selected request;
4. assigns the neutral vector to unselected or fallback responses;
5. orders proposals by the existing canonical response key;
6. applies all baseline and cognition deltas with stable `fsum`/product ordering;
7. commits exactly one dual-state update.

No model state becomes externally visible before all required results for the group are
available. Async completion order cannot affect response order, floating-point folding,
state, identifiers, or artifacts.

Provider output is not included in a later provider request in V10. This prevents latency
or completion order from changing subsequent prompts and keeps each request independently
replayable.

## Identifiers and authority

Core code creates every request, baseline response, applied evidence, and state-update
identifier. Provider output only echoes the exact request ID. A provider cannot supply,
select, or create an identifier or change which baseline events exist.

A V2 applied response binds:

- the deterministic baseline proposal hash;
- the terminal cognition record hash or an exact unselected-neutral marker;
- the influence-policy hash;
- the pre-minute rule and cognition state hashes;
- the notice, opportunity, scenario, city, run input, agent, campaign, placement, and time
  already validated by core;
- baseline and applied deltas.

Changing a provider category therefore changes the core-derived applied-evidence identifier
and downstream cognition-state hashes, but it cannot inject an identifier or change the
baseline response identity. Duplicate, orphaned, reordered, cross-run, cross-agent, cross-campaign,
cross-minute, or cross-policy bindings are corruption.

## Selection, budgets, and concurrency

Selection remains deterministic:

```text
(model_minute, agent_id, campaign_id, baseline_response_id)
```

At most the first four eligible responses per agent and the first 60 responses globally
are selected. Every unselected response uses the neutral vector and records the exact
unselected status in the V2 response evidence.

The cardinalities are exact:

```text
eligible = selected + unselected
selected = funded + budget_skipped
terminal_record_count = selected
applied_response_count = eligible
funded = min(selected, configured_funded_response_limit)
```

Selection is the per-agent/global scientific cap. Funding is the provider-call cap inside
that selection. Rules and mock set `configured_funded_response_limit=60`, resolve every
selected request locally, reserve zero provider tokens, and cannot create budget-skipped
records. Local and remote modes choose `10`, `20`, `40`, or `60`; the default is `20`.

Provider modes use:

| Limit | Value |
| --- | ---: |
| concurrent requests | 4 |
| decoded request bytes | 8,192 |
| wire response bytes | 1,048,576 |
| output tokens per attempt | 128 |
| whole-request reservation | 512 output tokens |
| initial attempts | 1 |
| transient retries | at most 2 |
| schema repair attempts | at most 1 |
| attempt timeout | at most 30 seconds |
| aggregate deadline | 180 seconds |
| selected responses per agent | 4 |
| selected responses per run | 60 |
| maximum aggregate reservation | 30,720 output tokens |

The UI exposes a funded AI-response count in whole requests: `10`, `20`, `40`, or `60`,
defaulting to `20` when a provider mode is selected. The frozen aggregate output ceiling is
the count multiplied by 512. Rules and mock modes process all selected responses without
reserving provider output tokens.

Reservations are allocated before cache lookup in canonical order and are never reassigned
because a request finishes early, fails, or hits cache. Usage is informational and bounded;
input tokens and vendor pricing remain outside the reservation guarantee.

### Deterministic mock policy

`city-cognition-mock-signal-v1` derives a non-uncertain, flag-free signal from each internal
request without provider access:

```text
digest = SHA-256(UTF-8("city-cognition-mock-signal-v1\0" + request_id))
affect = [annoyed, skeptical, neutral, curious, positive][digest[0] mod 5]
relevance = [none, low, medium, high][digest[1] mod 4]
credibility = [low, medium, high][digest[2] mod 3]
salience = [none, low, medium, high][digest[3] mod 4]
safety_flags = []
```

The policy text/hash is frozen in `CityCognitionInputV2`, each terminal record, the summary,
and manifest receipt. Rules mode uses the exact neutral vector and does not use the mock
derivation. Mock results never enter the provider cache.

## Provider and prompt security

The new signal adapter retains all V9 transport controls:

- immutable origin-bound provider profile revisions;
- credentials only from explicitly bound environment variables or an approved OS keyring;
- loopback HTTP only for local providers;
- HTTPS and exact startup allowlisting for remote providers;
- URL userinfo refusal;
- DNS rebinding, redirect, private-network, and endpoint drift protections;
- `trust_env=False`, no streaming, no tools, bounded bodies, timeouts, retries, and repair;
- no raw provider response persistence;
- no credential, authorization header, URL secret, or provider error body in artifacts,
  prompts, cache, logs, browser state, reports, or exceptions.

The prompt contains a minimal causal projection of the internal request, not the entire
stored persona or workbench document. Campaign creative is delimited as untrusted data and
delimiter collisions are neutralized. The system instruction asks only for the exact
closed result object and never asks for reasoning or chain-of-thought.

Prompt injection may influence which allowed category the model selects. The security
guarantee is bounded authority, strict validation, neutral fallback, and rule-state
noninterference—not perfect provider obedience.

Provider compatibility status is keyed to the exact profile revision, effective settings,
prompt-contract hash, and V2 result-schema hash. V9 explanation compatibility does not
satisfy the V10 signal check. Bounded provider mode requires an explicit successful
compatibility check before run submission. Credential replacement or contract revision
invalidates the check.

## Cache and fallback

V2 cache records use a distinct contract namespace and key that includes:

- canonical minimized request hash;
- frozen cognition input hash;
- provider profile revision and snapshot hashes;
- effective settings hash;
- prompt, result-schema, fallback, selection, and influence-policy hashes.

A V1 narrative cache entry can never satisfy a V2 signal request. Corrupt or cross-bound
cache content is a hard error, not a miss or fallback.

Expected provider failures—endpoint refusal, authentication refusal, exhausted timeout,
exhausted rate limit, selected server errors, oversized response, incompatible protocol,
or invalid result—produce one deterministic neutral terminal record with an exact reason.
Budget-skipped and unselected responses are also neutral but remain distinct statuses.

Cancellation, cache corruption, malformed frozen inputs, storage failure, artifact
corruption, and programming defects do not become successful fallback runs.

## Workbench schema 4 and manifest V10

Workbench input schema 4 retains schema-3 city, campaign, population, and provider-profile
selection. Its cognition section additionally freezes:

- effect scope `bounded-signals-v1`;
- request/result/prompt/selection/fallback/influence-policy versions and hashes;
- funded AI-response count;
- the exact provider budget and transport ceilings;
- response model `spatial-response-v2`;
- the explicit rule-state noninterference contract.

The only legal workbench/manifest pairs remain exact:

```text
1 -> 7
2 -> 8
3 -> 9
4 -> 10
```

Future versions, booleans, floats, coercions, and unsupported pairs are refused.

Manifest V10 extends the response receipt with:

- response model V2 identity and hashes;
- baseline and applied response counts;
- rule-state and cognition-state document hashes;
- cognition input/stream/summary hashes, byte counts, and record counts;
- provider/fallback/unselected outcome counts;
- influence-policy identity and hash;
- applied/neutralized/uncertain/safety-neutralized counts;
- workbench schema-4 binding.

## Frozen artifacts and publication

V10 reuses the established artifact locations with versioned contents:

- `inputs/workbench.json` — schema-4 frozen workbench input;
- `inputs/spatial-response.json` — validated response input;
- `inputs/cognition.json` — complete non-secret V2 cognition input and policy documents;
- `outputs/cognition.jsonl` — one canonical V2 terminal record per selected request;
- `outputs/cognition-summary.json` — V2 outcome, budget, source, and policy summary;
- `outputs/spatial-responses.jsonl` — canonical V2 baseline/applied responses and dual-state
  updates;
- `outputs/response-state.json` — complete final rule and cognition states;
- `outputs/response-summary.json` — V2 counts and stream/state hashes;
- `run.json` — V10 manifest, published last.

V10 requires all files even when there are no selected provider requests. V1-V8 continue
to forbid cognition artifacts. V9 requires only V1 cognition contents. V10 requires only
V2 contents. Missing, extra, wrong-version, noncanonical, oversized, symlinked/reparse,
reordered, duplicate, or cross-bound content is refused.

V2 freezes these canonical decoded-document ceilings:

| Document | Maximum bytes |
| --- | ---: |
| internal cognition request | 8,192 |
| cognition terminal record | 8,192 |
| cognition input | 131,072 |
| cognition terminal stream | 1,048,576 |
| cognition summary | 16,384 |
| response stream | 2,147,483,648 |
| dual response state document | 8,388,608 |
| response summary | 131,072 |

Serialization and loading enforce the same limit. Maximum-size construction tests prove
the permitted 30-agent, 7-day run fits before the constants are accepted.

The store stages and verifies every file, flushes required data, and publishes `run.json`
last through the existing no-clobber atomic contract. Failure or cancellation before that
point leaves no loadable completed run. A destination collision is always refused.

## Offline replay

V10 replay:

1. loads and validates the frozen workbench, city, campaign, population, provider metadata,
   response input, cognition input, and terminal records;
2. regenerates mobility, opportunities, attention, and deterministic baseline proposals;
3. regenerates the selected request identities and reservation order;
4. validates every frozen result against its exact request and frozen policies;
5. reapplies the frozen influence policy in canonical order;
6. regenerates dual-state response evidence and every summary/receipt;
7. requires exact equality with the stored artifacts and manifest.

Replay never consults a provider, network, cache, profile registry, credential source,
keyring, environment variable, mutable current policy, or current prompt constant. It does
not regenerate missing results, repair a mismatch, or silently neutralize corruption.

The source run is read-only. A high-level regression test hashes every source artifact
before and after replay.

Fresh external-provider runs are not promised to produce the same signals. A saved V10
run and its replay are reproducible because the validated signals and policy are frozen.

Canonical hashes detect accidental damage, isolated edits, incomplete writes, and internal
rebinding. They do not attest who produced provider output and do not protect against an
attacker who can rewrite every artifact and recompute the complete unsigned hash chain.
Provider-output authenticity would require an external signature or trust root and is
outside this design.

## Metrics, comparison, and reporting

V10 exposes two clearly separated metric families:

- **rule baseline metrics**, including the existing rule-owned purchase-intention proxy;
- **provider-dependent cognition metrics**, including applied sentiment, applied recall,
  signal categories, source/fallback counts, and funded coverage.

Existing response metric schema V1 does not silently accept V10. A response metric V2
receipt binds both tracks and the cognition input/stream/policy provenance. Purchase
intention is present only in the rule baseline family.

Matched comparisons and studies may compare V10 rule-baseline metrics under the existing
common-random-number contract. Provider-dependent cognition metrics are shown as
provider-dependent exploratory results and are not attributed as a clean campaign effect
unless a later paired-cognition protocol freezes compatible signals across arms. A
comparison must refuse or explicitly classify incompatible influence provenance rather
than hide it.

Reports remain self-contained and include no raw provider body, secret, external resource,
or executable provider text. They show the exact configured profile revision and model ID,
contract hash, source counts, coverage, rule/cognition separation, frozen replay statement,
and synthetic/nonvalidated disclosure. AdLife does not claim to attest a vendor's underlying
model weights or undeclared server-side model revision.

## Workbench and inspector

The workbench provider step adds an explicit operation choice and keeps provider location
separate from provider authority:

- provider source: rules, mock, local compatible endpoint, or remote compatible endpoint;
- effect scope: explanation-only or bounded cognition signals;
- exact profile revision and model;
- exact-contract compatibility status;
- funded AI-response count and maximum HTTP-attempt disclosure;
- input-token/vendor-cost warning;
- offline/fallback behavior.

A persistent boundary card states:

> External AI selects a small set of synthetic appraisal categories. AdLife applies a
> fixed mapping. These categories may change only the displayed cognition state. They
> cannot move agents, create or select events, change purchase intention, or make
> purchases.

The run review screen separately identifies:

- movement, exposure, and attention: rules;
- purchase intention and purchases: rule-owned proxy / purchases not modeled;
- cognition signals: selected provider with deterministic fallback;
- fresh live signals: not guaranteed reproducible;
- saved replay: frozen and offline.

The saved-run inspector shows, for each eligible response:

```text
deterministic baseline
  -> provider signal or neutral status
  -> frozen policy mapping
  -> applied cognition delta
  -> atomic rule/cognition state update
```

It shows enum chips, source, fallback reason, profile/model/contract revision, policy hash,
coverage, and the exact disclosure. Provider values render as inert text labels; no raw
body or Markdown is retained or rendered.

## Error handling

Expected user/configuration failures produce concise validation or conflict responses and
never a traceback or partially published run. Provider failure produces a neutral result
only when it matches the frozen fallback policy. Invalid input, cache corruption, storage
failure, verification failure, or an implementation exception fails the run.

Important outcomes are:

- provider unavailable: deterministic neutral terminal record;
- provider invalid JSON/schema/request ID: deterministic neutral terminal record;
- provider safety/uncertain answer: validated record, whole-vector neutralization;
- cache corruption: hard failure;
- cancellation before publication: no completed manifest;
- storage failure: no loadable completed run;
- replay mismatch: corrupt-artifact refusal;
- browser/UI failure: no mutation of the committed run.

## Required tests

### Contract and security

- Reject unknown fields, numeric signals, booleans-as-integers, NaN/Infinity, duplicate
  keys, provider-authored IDs/state/events/budget/probability/purchase fields, wrong request
  ID, wrong disclosure, and wrong schema version.
- Accept only `finish_reason=stop`; reject tool calls, streaming/SSE, content arrays,
  truncation, filtering, refusal, oversized bodies, and hostile encodings.
- Prove minimized prompts omit name, age, occupation, biography, MBTI, credentials, paths,
  unrelated agents, post-state, and raw documents.
- Extend the secret-redaction corpus across results, repair, cache, artifacts, usage,
  reports, exceptions, logs, URLs, and browser state.
- Bind compatibility checks and cache keys to the exact V2 contract and influence policy.

### Mapping and state

- Exhaustively exercise every applicable category combination and every uncertain/safety
  neutralization case.
- Prove applied deltas and cumulative cognition state stay within bounds.
- Prove the neutral vector is an exact identity over baseline deltas.
- Prove multiple same-minute responses share immutable pre-state and commit once.
- Prove input permutation, async completion order, and multiple `PYTHONHASHSEED` values do
  not change a run when terminal records are identical.

### Noninterference

- Changing any provider category may change only V10 cognition fields and their bound
  hashes.
- Movement, opportunities, attention, baseline responses, baseline state, purchase
  intention, budgets, agent/campaign/placement IDs, and event eligibility/count remain
  identical.
- Provider output cannot create an event, choose a target, modify a route, or supply a
  purchase probability.
- Descriptive persona fields remain absent from the causal prompt and cannot change V10
  numeric science by themselves.

### Failure and atomicity

- Expected provider failures produce exactly one neutral terminal record per funded
  request.
- Unselected, budget-skipped, fallback, uncertain, and safety-neutralized outcomes remain
  distinct.
- Cancellation, incomplete terminal streams, cache corruption, persistence failure, and
  verification failure publish no completed V10 manifest.
- One terminal record exists per selected request with no duplicate, orphan, reordered, or
  cross-bound result.

### Artifacts and replay

- V1-V9 golden load/replay behavior is unchanged.
- V9 rejects V2 cognition content and V10 requires it; unknown V11 refuses.
- Any isolated or internally inconsistent modification to an identity, pre-state, baseline
  proposal, signal, policy, provider binding, response, state, stream, summary, or manifest
  is detected. Tests do not claim authenticity against complete hash-chain replacement.
- Replay succeeds with network, cache, keyring, environment, provider registry, and current
  prompt/policy defaults unavailable.
- Recorded identical cognition produces identical downstream V10 artifacts.
- Source artifact hashes are identical before and after replay.

### API and browser

- Provider compatibility, funded coverage, rules/provider ownership, fallback, and
  disclosures are truthful and accessible.
- The inspector associates every signal with the exact response and immutable snapshot.
- Hostile and Persian text remains inert; narrow viewports, keyboard navigation, forced
  colors, reduced motion, and delayed APIs remain usable.
- No credential or raw provider body reaches the DOM, browser persistence, URLs, later
  DTOs, reports, or downloadable artifacts.

### Repository gates

- Focused unit, integration, contract, property, security, browser, replay, report,
  packaging, and clean-room wheel tests pass.
- Ruff format/lint, strict mypy, configured branch coverage, multiple hash seeds, wheel
  build/smoke, offline doctor/demo, and `git diff --check` pass.
- Existing maximum rules-mode performance remains within its regression ceiling. Provider
  latency is reported separately and is not hidden as simulator compute time.

## Explicit non-goals

- Letting an LLM decide movement, event identity, budget, purchase intention, purchase
  probability, or purchases.
- Treating MBTI, biography, name, occupation, or age as validated causal features.
- Adding social propagation or share events before a separately specified spatial social
  model exists.
- Claiming real-person behavior, real-city traffic fidelity, sales prediction, calibration,
  or external validity.
- Accepting ChatGPT product-login or subscription tokens as provider credentials.
- Adding authentication, multi-user authorization, SaaS tenancy, arbitrary provider URLs,
  or a generic agent framework as part of this feature.
- Reinterpreting or rewriting historical V9 artifacts.

## Acceptance criteria

The feature is complete only when a fresh local workbench session can:

1. configure or select an approved compatible provider;
2. pass the exact bounded-signal compatibility check;
3. create a schema-4/V10 run in bounded cognition mode;
4. show real provider/cache/fallback signal provenance in the timeline;
5. show baseline and applied cognition state while keeping purchase intention rule-owned;
6. replay the saved run with all provider access removed;
7. detect incomplete, isolated, and internally inconsistent cognition/artifact modification;
8. generate a self-contained report with the same verified evidence;
9. run rules/mock paths entirely offline;
10. pass the repository's complete release-quality gates without weakening coverage,
    typing, security, or compatibility.
