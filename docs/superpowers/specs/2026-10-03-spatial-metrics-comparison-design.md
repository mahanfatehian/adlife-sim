# Spatial Metrics and Matched-Run Comparison Design

**Status:** Approved autonomous roadmap refinement for C4a

**Parent roadmap:**
`docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

Schema-v5 city runs contain validated mobility, opportunity, impression and notice
evidence, but an analyst must currently interpret raw counts. C4a adds the smallest honest
analysis layer: pure metrics with explicit numerator, denominator and artifact provenance,
plus a comparison of two already completed matched runs.

This slice does not add response, recall, social, purchase, cognition or calibrated-human
claims. It does not rerun a study, write into either source run, or create a new run schema.
Metrics are deterministic projections of artifacts that the store has already verified.

## Alternatives considered

1. **Artifact-derived metrics and matched-run deltas (selected).** This uses the complete
   schema-v5 evidence available now and makes no new behavioral assumption.
2. **Implement spatial response/state first.** Rejected for this slice because city agents
   currently contain mobility assignments rather than response traits, and spatial campaign
   v1 contains identity/creative hashes rather than the price, interests and message inputs
   required by the existing response rule. Reusing that rule would silently invent inputs.
3. **Build write-enabled web study workflows first.** Deferred because those workflows need
   a stable scientific result contract and authorization boundary; adding them now would
   make the UI look more complete than the underlying study semantics.

## Pure metrics contract

`core/experiments/spatial_metrics.py` owns the adapter-free projection. Its public function
accepts a validated `SpatialOpportunityEvaluation`, a matching
`SpatialAttentionEvaluation`, the canonical fictional agent IDs, and exact run provenance.
It returns a frozen, extra-forbidden `SpatialMetrics` document with:

- schema/model identity and claim scope `synthetic-metrics-not-observed-outcomes`;
- source run schema 5, scenario/city/assignment/trace fingerprints, seed, population and
  duration;
- total opportunity, impression and noticed counts;
- opportunity, impression and noticed reach;
- impression frequency and notice rate;
- the same counts/rates for roadside and mobile channels;
- one receipt per metric containing numerator, denominator, value, source event types and
  source artifact paths.

The interface is
`derive_spatial_metrics(opportunities, attention, *, agent_ids, agents_sha256, trace_sha256,
days)`. The projection revalidates its inputs and refuses mismatched scenario/city
fingerprints, seeds, counts, opportunity-to-impression causality, duplicate population IDs
or unknown agent identifiers. Reach uses
unique fictional agent IDs. Frequency is impressions divided by impression reach. Notice
rate is notices divided by impressions. Any zero denominator produces the exact finite
value `0.0`; NaN and infinity are impossible. The two channel records have a fixed canonical
order: roadside, then mobile.

The core consumes no store, path, CLI, FastAPI or provider type. It performs no I/O and
cannot mutate a run.

## Opportunity-structure fingerprint

Comparison must distinguish a metric delta from a controlled channel effect. The core
therefore derives a normalized opportunity-structure SHA-256 from canonical records with
scenario, campaign and opportunity identities removed. It retains placement ID, channel,
agent, exact time and the channel-specific physical/model evidence:

- roadside road, direction, fraction, side, distance and angle evidence;
- phone activity, eligibility draw and declared opportunity probability.

The fingerprint is input-order invariant because the evaluation has canonical order. A
campaign name, creative hash or campaign ID change alone cannot change a roadside
structure fingerprint. A changed placement, schedule, phone draw, channel, agent or time
does change it. This is a model-structure receipt, not a geographic truth claim.

## Matched-run comparison

`compare_spatial_metrics(control, treatment, *, control_run_id, treatment_run_id)` accepts
two metrics documents. Their embedded source provenance must identify schema-v5 runs with
identical city fingerprint, mobility trace, fictional assignment fingerprint, seed,
duration and population size. Mismatches are input incompatibility, not corruption of
either otherwise valid artifact.

The frozen `SpatialMetricsComparison` contains:

- control/treatment run and scenario identities;
- the shared seed, city, mobility and assignment provenance;
- control and treatment metric snapshots;
- treatment-minus-control deltas for every metric;
- both opportunity-structure fingerprints;
- classification `matched-opportunity-structure` or `opportunity-confounded`;
- claim scope `synthetic-comparison-not-causal-or-observed-effect`.

The word “effect” is not used for a raw delta. If structures differ, output prominently
states that opportunity volume/timing/channel changed and the delta is confounded. If
structures match, the output says only that model opportunity structure was held constant;
it still does not establish an observed-world causal effect. Comparing a run with itself is
a valid A/A check and every delta must be exactly `0.0`.

This first comparison is one matched run pair, not a repeated-seed confidence interval.
Multi-seed geographic experiment orchestration remains C4b.

## CLI and browser contract

Two read-only CLI commands are added:

```text
adlife city-metrics ROOT RUN_ID
adlife city-compare ROOT CONTROL_RUN_ID TREATMENT_RUN_ID
```

Both validate portable run IDs before loading. Missing/corrupt/incompatible artifacts retain
artifact exit code 4. A valid pair that is scientifically incompatible exits 2 with a concise
diagnostic. JSON mode emits exactly one document on stdout. Human mode leads with the
synthetic claim and, for comparison, the confounding classification. Neither command writes
files or modifies source artifacts.

The validated saved-run FastAPI application adds read-only `/api/spatial-metrics` for v5
runs. It accepts no path and no run selector. Older manifests receive 404 because the
required attention evidence does not exist. The vanilla viewer adds a compact metrics panel
showing reach, frequency and notice rate with numerator/denominator tooltips and the
synthetic claim. It derives nothing in JavaScript; the API document is authoritative.

Comparison remains CLI-only in C4a. A future workbench may present it after write/auth
boundaries are defined.

## Failure, security and compatibility

- Manifest v1-v5 meanings and bytes remain unchanged; no schema-v6 artifact is introduced.
- The store validates all source hashes before metrics are calculated.
- Metrics and comparison are memory-bounded by existing opportunity/attention limits.
- No endpoint or command accepts an arbitrary artifact path beneath a request.
- Unknown, duplicate or inconsistent causal records are already refused by load and are
  rechecked at the pure metrics seam.
- Campaign/user strings are never rendered as HTML; existing safe DOM insertion remains.
- No network, provider key, raw response or cognition cache enters the projection.
- Viewing, metrics and comparison leave source artifact hashes byte-identical.

## Acceptance evidence

- golden unit tests pin every numerator, denominator, value, source and structure hash;
- empty evidence produces finite zero-denominator values;
- input permutations and multiple `PYTHONHASHSEED` values are identical;
- copy/campaign identity changes do not alter the applicable normalized roadside structure;
- inconsistent opportunity/attention/population inputs are refused;
- A/A is exactly zero for every metric;
- incompatible city, mobility, assignment, seed, duration or population is refused;
- differing opportunity structure is compared only with the explicit confounded label;
- CLI JSON is clean, exact and read-only; exit classes remain correct;
- API/UI tests prove bounded read-only rendering, safe text and legacy absence;
- source artifacts hash identically before and after metrics/comparison/viewing;
- Ruff, strict mypy, full/hash-seed/coverage suites, build and exact-wheel smoke pass.

## Deferred work

- C3 still owns spatial personas, campaign response inputs, bounded response/state updates,
  optional cognition, memory, social propagation and any purchase proxy.
- C4b owns multi-seed geographic experiment execution, confidence intervals and
  self-contained spatial comparison reports.
- D owns write-enabled browser study workflows after authorization design.
- F owns any calibration or real-population claim.
