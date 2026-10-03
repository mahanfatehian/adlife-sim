# Spatial Repeated-Seed Study and Report Design

**Status:** Approved autonomous roadmap refinement for C4b

**Parent roadmap:**
`docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

C4a can derive attention metrics and compare one matched pair of verified city runs.
C3c adds deterministic response and campaign-state evidence, but that evidence is not yet
available through a metric contract. This slice adds three deliberately separate layers:

1. a schema-v6 response-metric projection with exact receipts;
2. a bounded repeated-seed analysis over already committed city runs; and
3. a self-contained, zero-JavaScript scientific report.

The experimental unit is one simulator seed. The analysis never pools agents, notices,
opportunities, or campaign states across seeds as if they were independent observations.
Intervals describe variation across deterministic simulator seeds only. They are not
population confidence intervals, causal estimates, sales forecasts, or evidence about real
residents.

This is an artifact-first slice. It does not synchronously generate `2N` runs, scan a
directory for names, recover partially executed batches, or add a job system. An operator
first creates complete runs with the existing `city-run` command, then supplies an explicit
bounded definition naming each seed-matched pair. A later workbench/job layer may execute
those runs while consuming the same definition and result contracts.

## Alternatives considered

1. **Analyze verified existing artifacts (selected).** This makes scientific validation,
   corruption behavior, memory use, and source immutability testable before adding job
   recovery and cancellation semantics.
2. **Automatically create every arm inside `city-study`.** Deferred. A crash after only one
   arm or seed requires a versioned job manifest, reservation, resume and cleanup policy.
3. **Add response fields to `SpatialMetrics` v1.** Rejected. C4a is intentionally
   attention-only and supports schema v5. Reinterpreting it would silently change an
   established public contract.
4. **Reuse the zone experiment bootstrap unchanged.** Rejected for this new versioned
   contract. Its one stateful random stream makes later metric intervals depend on earlier
   metric enumeration, and it reports effect size zero for a constant nonzero difference.
   Existing zone output remains compatible; C4b uses independently keyed streams.
5. **Extend the existing Plotly report.** Rejected. That report is zone-specific and
   executable. The spatial study report is a static evidence ledger with no JavaScript or
   network-capable element.

## Response metrics

`core/experiments/spatial_response_metrics.py` defines a schema-version-1 projection for
verified schema-v6 runs. It does not persist a new city-run artifact or require schema v7.
Its model ID is `spatial-response-metrics-v1` and its claim scope is
`synthetic-response-metrics-not-observed-outcomes`.

The document binds:

- source run, opportunity, attention, and response model IDs;
- city, scenario, agent-assignment, mobility-trace, opportunity-structure, exact response
  input, response stream, and final-state hashes;
- a normalized response-assumption structure hash;
- seed, duration, population, and campaign count;
- one overall event/state series;
- event-only series ordered `roadside`, then `mobile`; and
- canonical campaign series ordered by campaign ID.

The normalized assumption hash accepts both the response input and its bound spatial
scenario. It covers canonical agent IDs, `fictional: true`, interests, the six response
traits, campaign IDs, target interests, relative price, every initial agent/campaign state,
and each placement's ID, campaign ID, channel, and daily frequency cap. Channel selects the
response trait used for recall and the cap controls response fatigue, so both are numeric
response assumptions rather than opportunity provenance alone. It excludes city/scenario
fingerprints, creative SHA-256, windows, probability and physical placement geometry, which
do not directly enter `spatial-response-v1` after a notice exists. Campaign and placement
IDs remain included so series and fatigue groups cannot be paired under changed identities
by accident. Its model ID is `spatial-response-assumption-structure-v1`.

For a filtered set of direct rule-response records `R`:

```text
response_count = len(R)
response_reach = unique responding agents / population_size
response_frequency = len(R) / unique responding agents
mean_rule_sentiment_delta = fsum(response.sentiment_delta) / len(R)
mean_rule_recall_delta = fsum(response.recall_delta) / len(R)
```

Zero-event frequency and direct-delta means carry numerator `0`, denominator `0`, and value
`0.0`. All reductions use canonical record order and `math.fsum`; negative zero is
normalized to positive `0.0`.

For a state field `x` over the complete matched initial/final agent-campaign set `S`:

```text
initial_total = fsum(initial.x)
final_total = fsum(final.x)
change_total = final_total - initial_total
initial_mean = initial_total / len(S)
final_mean = final_total / len(S)
mean_change = change_total / len(S)
```

State fields are `brand_sentiment`, `recall_strength`, and
`purchase_intention_proxy`. Overall state values weight every agent/campaign pair equally;
campaign state values contain exactly one state per fictional agent. A campaign with no
response remains present with zero event values and unchanged state.

Every scalar has a strict receipt. Direct-response receipts name
`outputs/spatial-responses.jsonl` and event type `spatial.response`; state receipts name
`inputs/spatial-response.json` and `outputs/response-state.json`. Counts retain integer
numerators. Reach, frequency, bounded direct means and state aggregates use distinct model
types so their dynamic denominators and bounds can be validated rather than documented
only in prose.

Direct response events may be attributed to their recorded campaign and channel. Committed
state must not be attributed to a channel: same-minute notices from two channels may share
one nonlinear bounded state update, so allocating that update between channels would be an
invented decomposition. `mean_rule_recall_delta` is the rule's planned encoding term;
`recall_strength.mean_change` is the committed nonlinear state change. Purchase intention
is always labeled an uncalibrated internal proxy, never a probability, conversion, sale, or
transaction. Schema v6 contains no spatial social evidence, so no social metric is emitted.

In `spatial-response-v1`, every `spatial.noticed` record deterministically produces exactly
one `spatial.response`. Response count, reach, and frequency are therefore notice-derived
rule-processing counts, not engagement, acceptance, persuasion, or observed behavior;
overall and channel response counts equal their noticed counts by construction. Moreover,
a nonzero v1 contrast must change opportunity structure or a numeric response assumption.
A creative-only change under matched opportunity and response assumptions is numerically
inert. Directional stability must never be presented as a clean creative effect.

Derivation receives the exact scenario, opportunity evaluation, attention evaluation and
agent IDs in addition to the response input/evaluation and attention metrics. It revalidates
all of them, re-evaluates the expected response through `spatial-response-v1`, and requires
exact equality before folding any scalar. It also independently checks fingerprints, seed,
population, response/noticed counts, channel totals, canonical order, and computed
stream/state hashes. A valid-looking constructed evaluation with invented notice causes or
copied traits cannot become metrics merely because its own fields agree with one another.

## Response comparison and scalar observations

`compare_spatial_response_metrics` returns
`spatial-response-metrics-comparison-v1` under claim scope
`synthetic-response-comparison-not-causal-or-observed-effect`. It requires identical city,
agent assignment, trace, seed, duration, population, model identities, and campaign-ID set.
Scenario and response-input hashes may differ.

It reports two independent classifications:

- opportunity: `matched-opportunity-structure` or `opportunity-confounded`;
- response assumptions: `matched-response-assumptions` or
  `response-assumption-confounded`.

Every delta is treatment minus control. A/A produces exact `0.0`; swapping arms negates
each scalar. A creative-only change does not change the normalized response-assumption
hash, while a trait, interest, relative-price, campaign-ID, or initial-state change does.

`core/experiments/spatial_observations.py` flattens attention and optional response metrics
into unique lexicographically sorted `SpatialMetricObservation` records. Study code consumes
this typed seam instead of introspecting nested models. Keys are versioned by their grammar:

```text
attention.overall.<metric>
attention.channel.<roadside|mobile>.<metric>
response.overall.<event-metric>
response.channel.<roadside|mobile>.<event-metric>
response.campaign.<campaign-id>.<event-metric>
response.overall.state.<state-metric>.<initial_mean|final_mean|mean_change>
response.campaign.<campaign-id>.state.<state-metric>.<initial_mean|final_mean|mean_change>
```

Each observation carries value, numerator, denominator, and exact source artifacts. State
keys never contain a channel segment.

## Study definition

`core/domain/spatial_study.py` defines:

```text
SpatialStudyPair(seed, control_run_id, treatment_run_id)
SpatialStudyDefinition(
    schema_version=1,
    study_id,
    design="a-a" | "paired-contrast",
    analysis_scope="attention" | "attention-and-response",
    pairs,
)
```

Definitions contain two to one hundred pairs and no title, note, URL, path, or arbitrary
text. IDs use the portable run-ID grammar, exclude Windows device names, and reject known
credential/contact shapes without echoing them. Pairs canonicalize by seed. Seeds are the
exact contiguous protocol `0..N-1`; booleans, gaps, duplicates and selected subsets are
refused. A run ID cannot appear under two different seed entries. Equal control/treatment
IDs are valid only for an A/A study. A paired contrast must change scenario identity or,
for response scope, normalized response assumptions.

The definition fingerprint is SHA-256 of canonical UTF-8 JSON. The loader reads at most
64 KiB and refuses invalid UTF-8, duplicate keys, non-integer versions, non-finite numbers,
unknown fields and excessive nesting without echoing content or path.

## Matched-run and cross-seed validation

Within each pair, the declared seed must equal both manifests. Arms must share city schema
and fingerprint, duration, population, generated agent and trace hashes, place-set presence
and hash, place-assignment presence and hash, package/Python/source-schema/model identity,
and identical observation keys and source receipts. Exact agent/trace/assignment equality,
not seed equality alone, is the evidence that common random numbers were achieved.

Across the study, all pairs must share city metadata, duration, population, source manifest
schema/model, package version, Python version, and place-set identity. The control scenario
hash is constant across seeds and the treatment scenario hash is constant across seeds.
For response studies, exact response-input and normalized assumption hashes are constant
within each arm, and campaign-ID sets are constant. Trace, generated assignment, opportunity
structure, and metric values may vary across seeds but must be matched within each pair.

`attention` accepts either all-v5 or all-v6 runs. One study never mixes schemas.
`attention-and-response` requires schema v6 for every arm.

Aggregate `opportunity_classification` is `matched-opportunity-structure` only when every
pair is matched and is otherwise `opportunity-confounded`; separate matched/confounded pair
counts must sum to the study pair count. Aggregate `response_assumption_classification` is
`not-applicable` for attention-only studies, otherwise it is
`matched-response-assumptions` only when every pair is matched and
`response-assumption-confounded` when any pair is confounded. Its two counts are zero for
attention-only and otherwise sum to the pair count. `a_a_status` is
`exact-zero-verified` for a valid A/A result and `not-applicable` for a paired contrast;
there is no failed A/A result model.

An A/A definition requires identical scenario and, when applicable,
response-input/assumption identity. Every metric and applicable artifact hash must be
exact. The canonical manifest hash and run ID are receipts, not cross-arm A/A equality
fields: separately named runs necessarily differ there. Exact equality applies to
simulation inputs and outputs, including city/agent/trace/place, scenario, opportunity,
attention, response-input/stream/state, metric and normalized-structure hashes as
applicable. A nonzero A/A result after verified inputs is an invariant/artifact failure,
not a valid scientific result with a warning.

## Deterministic paired statistics

One `SpatialPairedStatistic` is produced for every canonical observation key over the
seed-sorted treatment-minus-control vector. It contains source artifacts, sample count,
mean, sample standard deviation, median, deterministic 95% bootstrap interval, nullable
paired standardized difference, positive/negative/zero counts, agreement fraction and one
of `stable-positive`, `stable-negative`, `stable-null`, or `unstable`.

The public statistic function accepts 1 to 328 unique grammar-valid observation keys in
lexicographic output order. Its sources mapping must have exactly the same key set. Each
source tuple must equal the canonical unique ordered artifacts implied by the parsed metric
key, not merely contain an allowed artifact literal; the same rule applies to every
`SpatialMetricObservation`. Every vector has the same length from 2 through 100, and every
member is an exact finite float (not a bool or implicit numeric coercion). It refuses
missing/extra/wrong sources, mixed sample sizes, unknown keys/artifacts, non-finite values,
and oversized mappings before allocating bootstrap work.

Rules are fixed:

- `mean = fsum(values) / n`;
- `sample_sd = sqrt(fsum((value - mean) ** 2 for value in values) / (n - 1))`;
- an even-length median is the arithmetic mean of the two central sorted values;
- paired standardized difference is `mean / sample_sd`, or `null` when deviation is zero;
- exact zero is used without a post-hoc epsilon;
- zeros remain in the direction denominator;
- positive or negative direction is stable at a fraction of at least `0.8`;
- `stable-null` requires every delta to be exactly zero;
- direction comes from seed signs, not the sign of the aggregate mean;
- agreement is `1.0` for an all-zero vector and otherwise
  `max(positive_count, negative_count) / n`, with zeros in `n`;
- no p-value is produced.

An exactly constant binary64 vector is normalized as an algebraic identity before those
floating reductions: mean and median equal the positive-zero-normalized constant,
sample deviation is exactly `0.0`, the standardized difference is `null`, and both
bootstrap endpoints equal that constant. This prevents `fsum(values) / n` rounding (for
example, three copies of `0.1`) from manufacturing seed variation where none exists. The
constant interval is the exact closed-form result of all 10,000 resamples and does not
advance a generator.

Each interval uses 10,000 paired bootstrap resamples. It resamples seed pairs with
replacement, computes each bootstrap mean as `fsum(selected_values) / n`, sorts the 10,000
means, and selects zero-based elements 249 and 9749. The generator is versioned SplitMix64.
Each metric receives an independent stream initialized from the first eight SHA-256 digest
bytes interpreted as one unsigned big-endian integer, over canonical JSON exactly shaped as
`{"metric_key": <key>, "model_id": "spatial-paired-bootstrap-v1", "sample_size": <n>}`.
Index generation uses rejection sampling before modulo reduction. Thus a metric is invariant
to mapping insertion order, new unrelated metrics, Python's `random` implementation, and
`PYTHONHASHSEED`.

SplitMix64 uses unsigned 64-bit wrap after every addition/multiplication. For each draw it
adds `0x9E3779B97F4A7C15`, applies xor-shift 30 and multiplier
`0xBF58476D1CE4E5B9`, xor-shift 27 and multiplier `0x94D049BB133111EB`, then xor-shift 31.
For sample size `n`, values at or above `2**64 - (2**64 % n)` are discarded and the first
accepted value modulo `n` is the index.

Two to forty-nine seeds are labeled `exploratory-under-50-seeds`; fifty to one hundred are
`full-protocol-50-or-more-seeds`. The latter label describes protocol size only and is not
called registered, representative, validated, or statistically powered.

## Result and memory contract

`SpatialStudyResult` uses model ID `spatial-paired-study-v1` and claim scope
`synthetic-study-not-observed-or-causal-effect`. It records the definition, exact seeds and
protocol, evidence tier, bootstrap identity/count/confidence/direction threshold, source
schema/model/package/Python identity, bounded city provenance, duration/population, arm
scenario and response-assumption identities, classification counts, A/A status, canonical
pair receipts, and canonical statistics.

Pair receipts retain seed/run IDs, canonical manifest hashes, shared assignment/trace/place
hashes, arm opportunity/attention stream hashes, opportunity-structure hashes, optional
response-input/stream/state and response-assumption hashes, classifications, and canonical
scalar receipts/deltas. Each scalar row contains the complete control and treatment
`SpatialMetricObservation`—value, numerator, denominator, and canonical source artifacts—
plus treatment-minus-control delta. They never copy trajectories, profiles, traits,
interests, campaign copy, raw events, provider data, environment values, or filesystem
paths.

`SpatialStudyResult` validators recompute the definition fingerprint, pair classifications
and count totals, every scalar delta, every aggregate classification and every statistic
from its canonical pair receipts. They refuse wrong order, missing/duplicate keys, changed
numerators/denominators/sources, means, intervals, counts, hashes or provenance. The
analyzer returns an identity-cached fully revalidated instance so normal reporting need not
repeat the bounded bootstrap work; a deserialized or bypass-constructed instance pays full
revalidation before it can be rendered.

`study_definition_sha256` is the definition fingerprint: SHA-256 of canonical JSON UTF-8
without a trailing LF. Each pair's manifest hash is SHA-256 of the exact canonical
persisted `run.json` bytes, including its one trailing LF. The study-result hash used by a
report receipt is SHA-256 of canonical result JSON UTF-8 without a trailing LF. Stream and
state hashes retain their existing manifest meanings and are not recomputed over a new
projection.

City provenance copies only already validated pack metadata: ID/name/schema/hash, time zone
where present, source provider/dataset/version/date/hash/plain-text URL, license,
attribution and known omissions. It does not invent Git or lockfile provenance: current
city manifests prove package/Python/model identity only.

The application loads and verifies one run, immediately projects it to small evidence, and
releases it before loading the next arm. A same-run A/A pair reuses one projection.
Revalidation and exact response recomputation may hold a bounded constant number of copies
of that run while projecting it; the required asymptotic bound is therefore
`O(largest source run + bounded result)`, never `O(number of source runs)`. Tests inspect
load/project/release sequencing and a measured constant-factor peak rather than claiming
only one in-memory object. Analysis is read-only and source directory bytes remain
unchanged.

Canonical result JSON is limited to 32 MiB. The maximum 100-pair/20-campaign response shape
must pass a serialized-size and performance regression under that ceiling; oversized
constructed results are refused rather than partially emitted. The HTML report intentionally
omits per-seed scalar triples while retaining every aggregate statistic and complete pair
provenance, and remains independently capped at 8 MiB.

## CLI contract

```text
adlife city-metrics ROOT RUN_ID --layer attention|response
adlife city-compare ROOT CONTROL TREATMENT --layer attention|response
adlife city-study ROOT STUDY.json
adlife city-report ROOT STUDY.json
```

Existing metric/compare commands default to `attention` and preserve their current JSON
shape in that default. Response mode requires schema v6.

`city-study` JSON success is exactly one serialized `SpatialStudyResult`; human output
leads with scope, seed tier, confounding and A/A status before statistics. Definition and
scientific compatibility errors exit 2; missing/corrupt/unsupported runs and an otherwise
verified A/A invariant failure exit 4; interrupt exits 130 and unexpected defects exit 1.
The command writes nothing.

`city-report` uses the same analysis and accepts no output or force option. Its destination
is exactly `ROOT/city-reports/<study_id>.html`. The directory must be a real contained
directory, not a symlink or junction. Any existing destination, including a symlink,
dangling symlink or directory, exits 3 unchanged. The JSON receipt uses a relative POSIX
path and contains exactly the identifying fields `schema_version`,
`format_id="spatial-study-html-v1"`, `claim_scope`, `study_id`,
`study_definition_sha256`, `study_result_sha256`, `report_path`, `report_sha256`, and
`report_bytes`; it never exposes the local root. Report SHA-256 and byte count cover the
exact emitted UTF-8/LF HTML bytes.

The response metric API is additive: `/api/spatial-response-metrics` exists only for a
fully prevalidated schema-v6 viewer. `/api/spatial-metrics` remains attention-only. Both are
GET-only, path-free, offline and read-only.

## Static spatial report

`reporting/spatial_html.py` consumes only a revalidated `SpatialStudyResult`. It uses a
dedicated Jinja template and packaged CSS; it never receives a store, source path, provider
body or raw event. The report is a print-friendly scientific field ledger, not another
dashboard.

The first viewport states that evidence is synthetic, exploratory, unobserved, non-causal,
and not sales. It shows study and city provenance, protocol verdicts, a semantic causal
spine (`Opportunity -> Impression -> Notice -> Rule response -> Campaign state proxy`),
all aggregate statistics and receipts, pair provenance, exact model assumptions,
reproduction instructions and limitations. Attention-only reports mark the last two causal
stages not analyzed. Response reports distinguish planned rule deltas from committed
bounded state changes, say that every notice is mechanically processed into one response,
warn that a nonzero v1 contrast changes opportunity structure or numeric assumptions, and
always call purchase intention a proxy.

The page uses semantic landmarks, one H1, ordered headings, captions, scoped table headers,
a skip link, visible focus rings, keyboard-focusable table regions, `<bdi dir="auto">` for
public Unicode text, responsive containment at 390 px, system fonts, print rules,
forced-colors support, and text in addition to color.

The report contains no script, SVG, form, frame, image, external font, external or
cross-document link, or runtime fetch. One same-document skip link is retained for keyboard
accessibility. Jinja uses autoescape and `StrictUndefined`; only trusted packaged CSS is
marked safe. A meta CSP denies every capability and permits only the base64 SHA-256 of the
exact normalized inline stylesheet. Source URLs are plain text. Hostile markup remains
escaped text.

Rendering produces deterministic UTF-8/LF bytes under an 8 MiB limit. Publication writes
and fsyncs a uniquely named same-directory temporary file, then uses an atomic no-clobber
link operation. It never uses `os.replace`. After linking it attempts to fsync the parent
directory on platforms that support directory handles. The link is the publication commit:
a post-link directory-fsync failure is best-effort and non-fatal, leaving the complete
destination and a successful receipt. The contract therefore promises atomic no-clobber
publication and durable file contents but does not claim the new directory entry survives
sudden power loss on every filesystem. Any failure before the link cleans the temporary
file and leaves no destination; no path can replace an existing report. Containment checks
treat a Windows reparse-point junction as a link even on Python 3.11 by inspecting file
attributes rather than relying on `Path.is_junction()`.

## Compatibility, security, and deferred work

- Existing `SpatialMetrics`, `city-metrics`, `city-compare`, zone reports, city-run schemas,
  and source artifacts retain their meanings and bytes.
- Run/study IDs pass the shared portable and credential-shaped-text refusal before they can
  enter paths, manifests, output or reports. Every city/campaign identifier copied from a
  legacy source artifact into a C4b metric key, result or report passes the same narrow
  known-credential/contact screen; refusal uses a generic diagnostic and never echoes it.
- No default test, analysis, report or installed-wheel smoke uses the network.
- No endpoint or report command accepts an arbitrary server-side run/report path.
- Derived analysis never mutates or silently repairs source artifacts.
- The report contains no response input traits/interests, campaign copy, raw provider data,
  credentials, environment values or absolute paths.
- Automatic study execution/jobs, cancellation/resume, workbench write APIs,
  authentication/authorization, managed provider secrets, social spatial behavior,
  calibrated traffic/population/outcomes, and external validity remain separate roadmap
  work. C4b does not make the parent C3, C4, D, E, F, or G gates complete.

## Acceptance evidence

- golden response metrics pin one-notice, zero-notice, saturation, mixed-channel,
  multi-campaign and exact receipt behavior;
- property tests prove response metric/hash/comparison invariance to input order and hash
  seed, A/A exact zero, arm-swap negation and independent confound classifications;
- definition/loader tests cover every bound, duplicate key, strict integer, secret ID,
  canonical order and safe diagnostic;
- study tests pin two-, twenty- and fifty-seed statistics, exact bootstrap indices,
  constant-nonzero nullable effect size, sign thresholds, metric-order independence and
  source receipts;
- integration tests cover every within-pair/across-study mismatch, v5/v6 scope, A/A
  invariant failure, maximum pair bound, one-run memory behavior and source immutability;
- CLI tests pin exact exit/stdout/stderr behavior and read-only execution;
- report unit/security/browser tests prove deterministic bytes, exact values, escaping,
  CSP, no executable or external resource, keyboard/390 px/print behavior, no-clobber and
  fault cleanup;
- installed-wheel smoke runs a two-seed schema-v6 A/A study and report under the existing
  network guard, verifies source hashes, then proves a second report attempt cannot clobber;
- Ruff, strict mypy, full and multi-hash-seed pytest, branch coverage, build, exact-wheel
  smoke and applicable performance gates remain green.
