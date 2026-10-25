# Reproducibility

The event-stream guarantees below apply to **zone advertising campaign runs**. Saved
city mobility runs have a separate full-minute trace hash and `city-replay` command,
described at the end of this document; they are not event-sourced campaign runs.

In rules mode, the same seed, frozen scenario, model parameters and code produce the
same normalized events and final state. Mock cognition additionally uses the original
request identity and complete request JSON to select its deterministic fixture; replay
preserves those inputs. Live-provider replay requires recorded validated cognition.

The offline `demo` command fixes its scenario identity independently of the destination
folder and derives its run identity from canonical frozen inputs and the seed. Repeating
the same packaged demo inputs, seed and code in a fresh folder therefore preserves mock
request identities, scenario, event and metric exports. Ordinary study identities still
record their source directory; use recorded replay to reproduce those runs.

Replay normalizes only structured run/event references for its new destination; it
does not erase differences in arbitrary narrative text. Wall-clock timestamps, platform
metadata and SQLite file layout are not reproducibility targets. Entire artifact
directories are **not** promised byte-identical. The JSONL export rebuilt from the same
authoritative database is byte-identical to its canonical event export.

## What a seed guarantees

A seed fixes the model's keyed random draws for fixed inputs:

- **Keyed random streams.** There is no global random generator. Each agent's decisions —
  movement draws, attention draws, share draws, purchase draws — come from a random
  oracle keyed by seed, purpose, agent, simulated tick and draw index. Adding an agent
  does not consume another agent's draws, but changing social interactions can still
  change outcomes.
- **Deterministic scheduling.** The clock advances in fixed ticks of 15 simulated minutes
  (96 ticks per simulated day); stage and event ordering are total orders, not iteration
  orders.
- **Recorded inputs.** The run's `inputs/` directory freezes its scenario — world,
  population, routines and campaigns. Model parameters live in the manifest (a null
  mapping means the recorded package's defaults), not in later-edited project files.

For a fixed scenario, changing the model seed changes draws; changing a campaign can
propagate into later memory, social state and purchasing rules. CLI population generation
also uses the supplied seed unless a population document is supplied. Paired experiments
hold the already-loaded initial population and all non-treatment inputs fixed.

## The manifest

Every campaign run persists `runs/<run-id>/run.json` recording:

- the code version and the exact model parameters in force (`ModelParameters` with every
  documented knob);
- the scenario fingerprint (a hash over the frozen inputs);
- the provider, model identifier, and seed;
- run timing and final status.

A result is auditable against its own manifest, not against anyone's memory of the
configuration.

Git and lockfile metadata use explicit unavailable placeholders when a study is outside
a checkout or has no lockfile. Preserve the exact wheel/source revision and dependency
lock alongside a reported study; placeholders are not proof of an identical environment.

## Replay: the guarantee, checked

```bash
uv run adlife replay my-study <run-id>
```

Replay re-executes the stored run from its recorded inputs through the same drive loop and
compares the fresh event stream to the recorded one. The output says
`"replay-identical": true` only when the streams match event for event. A mismatch is
reported as a failure, never repaired. This is also how the live dashboard is kept honest:
a run driven under the TUI is verified to leave an event stream equal, event for event, to
a headless run of the same scenario and seed.

Interrupted runs verify their recorded committed prefix, including a stop before the
first tick. Running or failed artifacts without a trustworthy interrupted boundary are
refused. Replay never writes to the source directory. Core-only in-place recovery is
limited to still-running rules/mock artifacts exactly at a stored checkpoint (or their
start event); closed artifacts and uncheckpointed tails are not resumed in place.

## Environment hygiene

Two sources of nondeterminism outside the model are handled:

- **Hash iteration order.** The test suite runs under varying `PYTHONHASHSEED` values to
  catch any iteration-order leak. If you hack on the core, do the same
  (`PYTHONHASHSEED=0 …` then `PYTHONHASHSEED=12345 …`).
- **Language-model providers.** Fresh live answers can vary despite identical seeds.
  Bounded modifiers and social/memory inputs affect numeric state; later rule-derived
  intention and purchase outcomes can change too. The provider never supplies purchase
  probability directly, and reason text does not drive numeric formulas. Exact hybrid
  reproduction requires recorded validated cognition and fallback provenance; fresh
  provider calls are not a reproducibility check.

## Recipe: reproduce a campaign run

```bash
# from the manifest, read the seed, the parameters, and the scenario hash
cat my-study/runs/<run-id>/run.json

# re-execute and verify
uv run adlife replay my-study <run-id>
```

Replay re-executes the run and compares the fresh stream to the recorded one with the run
identifier references normalized away. A mismatch indicates changed code, missing or
corrupt recorded inputs/cognition, or a defect; it is never silently repaired. Integrity
checks detect inconsistent artifacts, not a coordinated rewrite of every file: runs are
not cryptographically signed or claimed to be tamper-proof against a malicious owner.

## Saved city mobility traces

`adlife city-campaign validate` is deterministic validation, not a saved run. Its
schema-v1 scenario fingerprint is canonical across campaign, placement, window and
eligible-activity input order, and its geometry evidence is derived from the selected
immutable city pack. The command writes no artifact and its fingerprint is not an
advertising outcome. Passing the same validated document to
`city-run --spatial-campaign` creates a schema-v5 artifact; the validation command by
itself is still not a saved run. C3a names schema-v4 opportunity persistence, C3b names the
schema-v5 attention addition, and C3c adds a validated `--spatial-response` document to
create a schema-v6 artifact with deterministic bounded response/state evidence. Older
schema-v4 artifacts remain readable as opportunity-only evidence.

The workbench's guided pages submit schema-4/schema-v10 bounded AI cognition requests; its
API also accepts schema-3/schema-v9 and keeps schema-2/schema-v8 and
schema-1/schema-v7 compatible without changing the CLI contract. V7, V8, and V9 retain the exact
schema-v6 scientific receipt plus canonical `inputs/workbench.json`, which freezes the
complete accepted draft and creative-template snapshot. Its canonical bytes must match
`workbench_input_sha256`, and its city, settings, run ID, campaign, creative and response
bindings must match the other frozen inputs. Load and replay refuse a missing,
noncanonical, oversized, linked, changed, or inconsistent sidecar. Replay uses that
validated recorded workbench input; it never reconstructs an accepted request from the
current package catalog. Process-local job IDs, phases, and wall-clock transition times
are operational metadata and do not enter the run, keyed draws, or artifact hashes.

The browser control contract preserves every seed as a canonical decimal string, including
`9223372036854775807`, until the HTTP adapter validates it and converts it exactly once to
the bounded Python integer used by domain construction. The browser's unsaved draft, page,
validation display, polling interval, playback state, selected agent/evidence, filters, map
zoom, and model-clock position are presentation state only and never enter a schema-v7, schema-v8, or schema-v9
artifact. Refresh may resume polling a live process-local job from its frozen
`accepted_settings`; process restart does not reconstruct transient job history. Reopening
a completed run starts from a fresh artifact verification and the persisted canonical
`inputs/workbench.json`, not browser storage or the current catalog.

Schema-v9 adds a deterministic selection pass and a bounded, noncausal explanation pass
after all response evidence is computed. Rules and mock explanations are offline and
deterministic. Local or remote OpenAI-compatible answers may vary, but they cannot change
movement, exposure, attention, response values, campaign state, budget, purchase
probability, metrics, or event identity. Their only output is validated explanatory prose
or a deterministic terminal fallback.

The exact schema-3 settings are frozen in `inputs/workbench.json`; the selected evidence and
effective contracts are frozen in `inputs/cognition.json`; terminal outcomes are canonical
records in `outputs/cognition.jsonl`; aggregate provenance is stored in
`outputs/cognition-summary.json`. The manifest binds each file's hash, bytes, count, profile
revision, policies, and budget. Independent load verification regenerates numeric evidence,
selection, rules/mock/fallback text, record order, cross-references, and summary receipts.
Live provider explanations are nondeterministic; frozen schema-v9 replay is deterministic,
uses those recorded terminal outcomes, and performs no provider call, keyring lookup,
environment lookup, cache lookup, or network access. Replay never consults the current
profile registry and never modifies the source run.

### Bounded AI cognition reproducibility (schema-v10)

A schema-4 run freezes its accepted draft in `inputs/workbench.json` (input schema 4), the
complete non-secret cognition input, policies, profile snapshot, effective settings, funded
limit, and output reservations in `inputs/cognition.json`, one terminal record per selected
request in `outputs/cognition.jsonl`, and the outcome summary in
`outputs/cognition-summary.json`. The dual-state `outputs/spatial-responses.jsonl`, the final
rule and cognition states in `outputs/response-state.json`, and
`outputs/response-summary.json` complete the evidence, and the V10 manifest binds every hash,
byte count, and record count.

Live provider signals are nondeterministic. Even at temperature 0 with a fixed seed, a local
`qwen2.5:7b-instruct` can choose different categories for a byte-identical request, and it
occasionally mis-echoes the request ID, which costs one schema-repair attempt (for example 11
HTTP attempts for 10 funded requests). Fresh calls are therefore never a reproducibility
check. Frozen schema-v10 replay is: `city-replay` regenerates mobility, opportunities,
attention, the deterministic baselines, the selected request identities (under the run's
stored selection-policy version: V3 spreads the AI limit evenly over the run, and a run saved
earlier keeps its V2 earliest-first caps), and the reservation order; validates every frozen
result against its exact request and the frozen policies; reapplies
`city-cognition-influence-v1` in canonical order; regenerates the dual-state stream, states,
summaries, and manifest receipt; and requires exact equality. It makes no provider call and
reads no cache, profile registry, keyring, environment variable, current prompt constant, or
network, and it never regenerates a missing result, repairs a mismatch, or silently
neutralizes corruption. Source artifacts are byte-identical before and after replay.

The core builds every request and reservation before provider work starts, and all responses
of one agent and campaign in the same minute read one immutable pre-minute state and commit
one atomic update, so network completion order, input permutations, and `PYTHONHASHSEED`
cannot change a run whose terminal records are identical. A causal request ID addresses only
causal inputs. In runs saved earlier (prompt contract `city-cognition-signal-prompt-v2`),
changing a persona's name, age, occupation, background, or MBTI changes the workbench and run
provenance hashes but not the causal request IDs, the deterministic mock signals, the cache
identity, or V10 numeric cognition. New runs (prompt contract
`city-cognition-persona-prompt-v3`) ask the AI to answer as the person, so those fields are
causal for the cognition track: the same edit changes the causal request IDs and the cache
identity, never the rule baseline. Every v3 request is rebuilt from the frozen persona copy in
`inputs/workbench.json` and the deterministic rule baseline, never from an earlier answer, so
verification and replay rebuild identical requests. Rules and mock runs are offline and
reproduce exactly. Within one workspace, a validated V2 cache entry for the same causal
request, profile revision, effective settings, and contract hashes is reused with source
`cache` and bound to the new run's own terminal record; a terminal record itself is never
shared across runs.

The rule-baseline metrics of a V10 run are derived only from the deterministic baselines and
rule states, so different valid provider signals leave them exactly unchanged. Canonical
hashes detect accidental damage, isolated edits, incomplete writes, and internal rebinding;
they are unsigned, so they do not authenticate provider output or protect against someone
who rewrites every artifact and recomputes the whole hash chain. V10 is an unreleased artifact
schema: runs written by earlier development builds (before its final output budget and prompt
contract) no longer validate and must be recreated.

For a run-scoped workbench read, one GET-only `/api/runs/{run_id}/timeline` request
projects the frame and four bounded evidence/state documents from one freshly verified run
context; every scoped API request performs a fresh artifact verification, and a request
carrying the removed inspector's view-token header is refused. The legacy `/api/timeline`
route returns the same projection contract from the
immutable context validated when that viewer starts. The nested documents are contract-equal
to the existing individual endpoint projections. Batching changes neither canonical bytes
nor simulation ordering, and a failed or superseded request cannot replace the last
committed browser frame.

The pure C2 evaluator is nevertheless deterministic evidence. Continuous route crossings
come from the same frozen paths and speeds as the unchanged minute trace. Crossing time is
quantized to the nearest model millisecond before half-open-window checks. Phone stream
keys are the leading 64 bits of SHA-256 over
`{seed}|spatial-phone-opportunity-v1:{campaign_id}:{placement_id}|{agent_id}|0`;
absolute minute is transformed with the documented SplitMix64 counter finalizer, and its
upper 53 bits form the `[0,1)` draw. Probability and cap are not part of that stream key,
so paired variants reuse common random numbers. Records are ordered by continuous time,
agent, campaign, placement and channel; IDs hash scenario/city fingerprints and that
canonical identity. Campaign, placement, window and activity input order and
`PYTHONHASHSEED` therefore cannot alter an evaluation. In schema-v4, schema-v5, schema-v6,
schema-v7, schema-v8, schema-v9, and schema-v10 runs this same computation is also artifact
replay: the frozen scenario is
re-evaluated and the canonical stream and independently derived summary must match their
manifest-bound evidence.

Schema-v5 then derives one attention record per opportunity. The draw is the upper 53 bits
of SHA-256 over canonical model ID, root seed, agent, placement, channel and absolute model
millisecond; scenario/campaign/creative/provider data are not draw inputs. A draw below
the fixed 0.5 probability records noticed. Records are canonically ordered and carry
`synthetic-attention-not-observed-behavior`. This is deterministic, uncalibrated evidence,
not observed behavior, and it cannot affect cognition, state, budget, movement or purchase
probability.

Schema-v6 then evaluates only the persisted noticed records against the canonical
`inputs/spatial-response.json`. Response-input collection permutations are canonicalized and
cannot change the result. The persisted attention stream must already have canonical record
order: noncanonical persisted notice order is corruption and is refused rather than silently
reordered. The response input binds the exact agent-ID set generated for the run but is not
bound to one mobility assignment, which permits fixed response assumptions across mobility seeds; every
saved run separately freezes and hashes its generated assignments. All notices for one
agent/campaign in the same minute read one immutable pre-minute state and the same pre-minute
fatigue counters; the evaluator combines their deltas canonically and commits one
commutative, atomic state update. State remains campaign-scoped, finite, and bounded. The run
model ID is
`illustrative-road-spatial-response-study-v1`, the evaluator is `spatial-response-v1`, the
summary is `spatial-response-artifact-v1`, and the final-state document is
`spatial-response-state-v1`. Records carry
`synthetic-response-not-observed-behavior`; purchase intention is an uncalibrated proxy, not
purchase probability, sales, or a transaction.

`adlife city-metrics ROOT ID` derives canonical read-only projections only after complete
artifact verification. The default attention layer remains the exact C4a contract for
schema-v5, schema-v6, schema-v7, schema-v8, schema-v9, and schema-v10: it reads opportunity
and attention evidence, records
the source schema version, and does not consume response records or final campaign state.
Every value
carries its exact numerator, denominator, and source paths plus
`synthetic-metrics-not-observed-outcomes`.

For schema-v6, schema-v7, schema-v8, and schema-v9, `city-metrics --layer response` derives the separate
`spatial-response-metrics-v1` projection under
`synthetic-response-metrics-not-observed-outcomes`. It re-evaluates response evidence from
the frozen response input and notice stream before folding it. Direct rule responses have
overall/channel/campaign event receipts; complete initial/final state has overall/campaign
receipts only. Channel state is deliberately absent because a shared nonlinear commit
cannot be decomposed after the fact. Both projections are deterministic, uncalibrated,
derived in memory, and never stored back into the source run. For schema-v10,
`city-metrics --layer response` instead derives the separate `spatial-response-metrics-v2`
document after regenerating the dual-state stream from the verified evidence: its rule
baseline equals the V1 derivation over the same rule evidence, and its provider-dependent
cognition family depends on the frozen signals, which replay reproduces exactly.

`adlife city-compare ROOT CONTROL_ID TREATMENT_ID` preserves the attention layer by default
and requires identical city, assignment, trace model, seed, duration, and population
provenance. Same-run A/A deltas are exactly zero. Its additive schema-v6/schema-v7/schema-v8/schema-v9
response layer separately classifies opportunity structure and numeric response
assumptions; a different normalized placement/channel structure is
`opportunity-confounded`, and changed assumptions are `response-assumption-confounded`.
Attention comparisons carry
`synthetic-comparison-not-causal-or-observed-effect`; response comparisons carry their
separate response claim scope. Both explicitly refuse causal or observed-effect
interpretation. A schema-v10 run compares on the attention layer only with another
schema-v10 run, and the response layer refuses it.

### Repeated-seed study reproducibility

`adlife city-study ROOT STUDY.json` analyzes explicit contiguous `0..N-1` run pairs as
`spatial-paired-study-v1` under `synthetic-study-not-observed-or-causal-effect`. One seed
pair is the experimental unit; the analyzer never pools agents, events, opportunities,
notices, or campaign-state rows as independent samples. Matching assignment, mobility trace,
and optional place-assignment hashes provide the within-pair common-random-number evidence.
Attention studies are homogeneous all-v5, all-v6, all-v7, all-v8 or all-v9;
attention-and-response studies are homogeneous all-v6, all-v7, all-v8 or all-v9. Schema-v10
runs are refused by both scopes, and therefore by `city-report`, for now.

Each metric's treatment-minus-control vector yields its mean, sample standard deviation,
median, nullable paired standardized difference, exact sign counts, agreement, direction,
and a deterministic 95% interval. Positive/negative direction requires at least `0.8` of
all pairs; zeros remain in the denominator, all-zero is `stable-null`, and no p-value is
produced. These are summaries of simulator seed variation, not population confidence.

Intervals use 10,000 resamples of whole seed pairs with replacement. Each metric receives
an independently keyed SplitMix64 stream derived from its canonical metric key and sample
size, so adding or reordering another metric cannot change it. Resampled means use
`math.fsum`; after sorting, zero-based elements 249 and 9749 are the interval endpoints.
Modulo bias is avoided with rejection sampling. The algorithm does not use Python's global
random generator.

An exactly constant vector is treated algebraically: mean and median equal the constant,
sample deviation is exactly `0.0`, the nullable paired standardized difference is `null`,
and both bootstrap endpoints equal the same constant. This avoids manufacturing variation
through floating-point regrouping.

Two to 49 pairs are `exploratory-under-50-seeds`; 50–100 are
`full-protocol-50-or-more-seeds`. That label is not registered, not representative, not
validated, and not statistically powered. It records a protocol-size tier only.

`study_definition_sha256` and `study_result_sha256` hash canonical JSON UTF-8 without a
trailing LF. Each pair's `manifest_sha256` hashes exact persisted canonical `run.json`
bytes, including its trailing LF. Existing stream/state hashes retain their manifest
meanings.

`report_sha256` and `report_bytes` cover the exact emitted UTF-8/LF HTML bytes. The static
report contains no JavaScript or network resource and is published only to the fixed
`city-reports/<study_id>.html` path without replacing an existing entry.

Canonical study results are capped at 32 MiB and projected with
`O(largest source run + bounded result)` memory; static reports omit per-seed scalar triples
and are capped at 8 MiB. Analysis and reporting leave every source run byte-identical.

Current city manifests record package version, Python version, run model, and evidence
identities. City manifests do not prove Git revision or lockfile identity; preserve the
source revision and dependency lock separately when reporting a study.

`adlife city-run PACK --output-root ROOT --run-id ID` or the catalog form
`adlife city-run --city-id fictional-grid-v2 ...` writes a distinct artifact at
`ROOT/city-runs/ID/`. Catalog selection is offline and content-addressed: the packaged
v2 resource's canonical SHA-256 and public metadata are checked before the run starts.
All three bundled catalog packs are explicitly fictional. The browser workbench may instead
select an immutable local V2 upload. In both cases the accepted pack is frozen as canonical
`inputs/city.json`, so replay, metrics, reports, and inspection are independent of the
current packaged catalog or workspace registry. Removing, replacing, or corrupting registry
state cannot change a saved run; corrupt frozen input is refused rather than repaired from
either catalog.

The artifact freezes the city pack (including v2 road geometry, direction, source
provenance, bounds, time zone and omissions), generated fictional assignments, seed,
duration, model/runtime identity and the SHA-256 digest of **every** normalized minute
frame in order. Versioned run manifests pair v1 packs with the v1 mobility model and v2
packs with the v2 model; incompatible pairings are refused rather than coerced. When
`--places` is supplied, manifest v3 additionally freezes canonical `places.json` and
`place-assignments.json`, records both SHA-256 digests, and reconstructs the assignment
document during load. Editing, omitting or swapping either file is therefore refused
before replay or viewing. With `--spatial-campaign`, schema-v5 additionally freezes
`inputs/spatial-campaign.json`, streams canonical opportunity records to
`outputs/spatial-opportunities.jsonl`, and writes
`outputs/opportunity-summary.json`. The manifest records the scenario, stream and summary
hashes plus exact stream bytes/count, and is published last. The fixed claim scope remains
`synthetic-opportunity-not-impression`. V5 also freezes
`outputs/spatial-attention.jsonl` and `outputs/attention-summary.json`, binding their hashes,
sizes, counts and the fixed model metadata. Schema-v4 is the retained opportunity-only
predecessor. When `--spatial-response` is also supplied, schema-v6 retains every v5 file and
adds `inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`. The final manifest binds
the response input, stream, state, and summary receipts. `response_input_sha256` is the
canonical response-input fingerprint excluding the saved file's trailing newline;
`response_stream_sha256`, `response_state_sha256`, and `response_summary_sha256` hash exact
persisted bytes, including JSONL line endings and the trailing newline in each
single-document state/summary file. It also records exact stream bytes, response and
state-update counts, campaign count, and complete final-state count. All files are written
and verified before the final manifest is atomically published; a failed publication leaves
a reserved, non-loadable directory rather than a partially trusted run.

Workbench publication retains all of those v6 files as schema-v7 (input 1), schema-v8
(input 2), or schema-v9 (input 3) and adds canonical
`inputs/workbench.json` plus its manifest schema/hash binding. The sidecar is written and
verified before the final manifest becomes authoritative. V9 additionally writes and
verifies all three cognition files before that manifest-last publication. Schema-v10
(input 4) writes its version-2 cognition files and dual-state response files instead of the
v6 response layer and publishes `run.json` last under the same no-clobber contract; failure or
cancellation before that point leaves no loadable completed run.

`adlife city-replay ROOT ID` validates those inputs and regenerates the complete trace
without changing source files; for v4 through v10 it also recomputes and compares the
normalized opportunity stream and summary, for v5 through v10 it independently recomputes
attention, and for v6/v7/v8/v9 it re-evaluates the response stream, final-state document,
and summary. V7/V8/V9/V10 also verify and reuse their immutable workbench sidecars; V9 verifies
the frozen explanation stream without a provider, and V10 reapplies its frozen bounded
signals to regenerate the dual-state response evidence, as described above. A mismatch,
partial publication, or incompatible artifact is refused.
`adlife city-view ROOT ID` performs the same load verification before
opening the read-only mobility timeline. For schema-v4 it also presents canonical,
persisted current-minute opportunity evidence; scrubbing or selecting an evidence record
does not recompute, append to or mutate the source artifact. Schema-v5 additionally presents
persisted current-minute attention evidence with the synthetic claim and 0.5 assumption.
Schema-v6, schema-v7, schema-v8, and schema-v9 present persisted current-minute response/state-update records and
complete final end-of-run state explicitly labeled as not state at the scrubbed minute;
schema-v10 presents its applied responses, dual-state updates, and final rule and cognition
states the same way.
Schema-v5, schema-v6, schema-v7, schema-v8, schema-v9, and schema-v10 viewers derive the same
attention-only, receipt-bearing
spatial metrics ledger in memory; a fully prevalidated schema-v6/schema-v7/schema-v8/schema-v9 viewer also
re-derives the separate response metrics document, and a schema-v10 viewer re-derives its
version-2 document. Viewing, scrubbing, metrics, comparison,
study analysis, and reporting never modify the saved artifact. A digest proves equality
against that artifact, not authenticity against an owner rewriting all files. Identical
frames are expected on a compatible implementation/runtime;
cross-platform bitwise identity of floating-point interpolation is not claimed.

## Schema-2 persona replay provenance

Schema-v8 binds schema-2 canonical inputs/workbench.json with the same scientific equations
as direct V6 and schema-1/schema-v7 compatibility. Reordering persona definitions preserves
canonical population hashes and assignments. A persona ID is structural: changing it changes
provenance and may change canonical assignment order. Counts determine contiguous person-NNN
assignments. Name, age, occupation, background and MBTI are noncausal descriptive metadata for
the rules: a descriptive edit changes workbench provenance but leaves mobility, scientific
response-input fingerprints, normalized evidence and rule metrics unchanged (in a new
schema-v10 run it can still change the separate cognition track, as described above). Each run
keeps its own frozen persona copies, so a later change to the packaged template text never
changes a saved run's load, verification, or replay. Explicit interests, six response
traits and initial campaign state are causal assumptions and may change response evidence.

Replay reads frozen persona records, creative snapshots and scientific inputs; changing or
removing bundled template resources cannot change a saved run. Independent binding refuses
rehashed-but-misassigned sidecars. Scientific replay verifies identical recomputation and
leaves every source artifact byte unchanged. V8 metrics/reports/studies/viewers explicitly
support the version; paired studies ignore descriptive identity but reject differing causal
population profiles or initial state. Campaign/scenario treatments retain the declared study rules.
