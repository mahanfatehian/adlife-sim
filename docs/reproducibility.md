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
`PYTHONHASHSEED` therefore cannot alter an evaluation. In schema-v4, schema-v5, and
schema-v6 runs this same computation is also artifact replay: the frozen scenario is
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

`adlife city-metrics ROOT ID` derives a canonical read-only projection only after complete
schema-v5 or schema-v6 artifact verification. It remains attention-only for both schemas:
the fold reads opportunity and attention evidence, records the exact source schema version,
and does not consume response records or final campaign state. Every value carries its exact
numerator, denominator, and source paths plus `synthetic-metrics-not-observed-outcomes`. The
projection is deterministic and uncalibrated; it is neither stored in the artifact nor
presented as an observed outcome.
`adlife city-compare ROOT CONTROL_ID TREATMENT_ID` requires identical city, assignment,
trace model, seed, duration, and population provenance. Same-run A/A deltas are exactly zero.
Different normalized placement/channel opportunity structure is classified
`opportunity-confounded`, and every comparison carries
`synthetic-comparison-not-causal-or-observed-effect`. Thus a differing structure cannot be
silently interpreted as a channel effect.

`adlife city-run PACK --output-root ROOT --run-id ID` or the catalog form
`adlife city-run --city-id fictional-grid-v2 ...` writes a distinct artifact at
`ROOT/city-runs/ID/`. Catalog selection is offline and content-addressed: the packaged
v2 resource's canonical SHA-256 and public metadata are checked before the run starts.
The only bundled catalog pack is explicitly fictional. A selected pack is then frozen
inside the run, so replay does not consult the current catalog and cannot drift when a
catalog is later replaced.

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

`adlife city-replay ROOT ID` validates those inputs and regenerates the complete trace
without changing source files; for v4, v5, and v6 it also recomputes and compares the
normalized opportunity stream and summary, for v5 and v6 it independently recomputes
attention, and for v6 it re-evaluates the response stream, final-state document, and
summary. A mismatch, partial publication, or incompatible artifact is refused.
`adlife city-view ROOT ID` performs the same load verification before
opening the read-only mobility timeline. For schema-v4 it also presents canonical,
persisted current-minute opportunity evidence; scrubbing or selecting an evidence record
does not recompute, append to or mutate the source artifact. Schema-v5 additionally presents
persisted current-minute attention evidence with the synthetic claim and 0.5 assumption.
Schema-v6 also presents persisted current-minute response/state-update records and complete
final end-of-run state explicitly labeled as not state at the scrubbed minute. Schema-v5 and
schema-v6 viewers derive the same attention-only, receipt-bearing spatial metrics ledger in
memory; viewing, scrubbing, metrics, and comparison never modify the saved artifact. A
digest proves equality against that artifact, not authenticity against an owner rewriting
all files. Identical frames are expected on a compatible implementation/runtime;
cross-platform bitwise identity of floating-point interpolation is not claimed.
