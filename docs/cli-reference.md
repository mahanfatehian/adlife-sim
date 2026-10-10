# CLI reference

Commands honor the global options; every failure
exits with one of the documented codes and, in JSON modes, emits an error object on
stdout.

## Global options

Set on the root command, before the subcommand:

```bash
adlife --format human|json|jsonl --no-color <command> …
```

| Option | Meaning |
| --- | --- |
| `--format` | `human` (default) prose; `json` one machine-readable document on stdout; `jsonl` one JSON object per line for streams. |
| `--no-color` | Disable colored output. `NO_COLOR=1` and `TERM=dumb` are honored automatically. |
| `--version` | Print `adlife <version>` and exit. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error (a defect, not your input). |
| 2 | Invalid input: refused configuration (including provider credentials or URLs), bad file, broken scenario, refused flag combination. |
| 3 | Run conflict: a run identifier already exists, or an operation would overwrite an artifact. |
| 4 | Artifact error: a stored run or required cognition cache record is missing, corrupt, or does not reproduce. |
| 130 | Interrupt: an active run records its interrupted artifact before exiting. |

A Python traceback is printed only for internal defects (exit 1) — expected failures such
as invalid input, run conflicts, provider misconfiguration, or corrupted artifacts print a
concise diagnostic on stderr and nothing more. Set `ADLIFE_DEBUG=1` to request the full
traceback for any failure.

## `adlife init NAME [--parent DIR]`

Create a new study directory from the packaged demo project. Refuses to touch a non-empty
target (exit 2). Default parent is the working directory.

## `adlife validate PATH`

Validate a study directory: schema, world/routine routability, campaign containment.
Print a JSON validation result and exit 0 when valid; exit 2 with a diagnostic otherwise.
`--verbose` adds validation detail on stderr. YAML documents are bounded to 1 MiB and
32 nesting levels; aliases, duplicate keys, and custom object tags are refused.

## `adlife city-catalog list [--search TEXT]`

List the packaged, integrity-verified city catalog. Selection is offline and
content-addressed: every entry pins a v2 resource by canonical SHA-256 and matching
metadata. The only shipped entry is `fictional-grid-v2`, whose origin is `fictional`
and whose qualification is `fictional-fixture`; no real city is qualified by this
release. `--search` applies case-insensitive local token matching to ID, display name,
provider and dataset. No matches is a successful empty result. JSON mode emits one
`{"cities": [...]}` document; JSONL emits one entry per line.

## `adlife city-catalog show CITY_ID`

Show one exact catalog record, including hash, schema, qualification, bounds, time
zone, public source metadata and known omissions. An unknown ID exits 2. A missing,
malformed, mismatched or hash-invalid packaged catalog resource exits 4. Listing and
showing the catalog make no network request and accept no URL or arbitrary file path.

## `adlife city-places validate PLACE_SET [PACK | --city-id ID] [--agents N] [--days N] [--seed N]`

Validate one bounded local synthetic place-set JSON document against exactly one local
`PACK` or verified catalog `--city-id`. The command checks the immutable city hash,
place provenance, home capacity, keyed assignment and directed routability without
writing an artifact or making a network request. `--agents` accepts 1–250 (default
20), `--days` 1–31 (default 7), and `--seed` defaults to 42. Invalid, mismatched,
insufficient or unreadable inputs exit 2 with bounded diagnostics. JSON mode emits one
document containing the city/place hashes and assignment count.

## `adlife city-campaign validate SCENARIO [PACK | --city-id ID]`

Validate one bounded local spatial-campaign JSON document against exactly one local
`PACK` or verified catalog `--city-id`. The command checks the immutable city hash,
campaign references, creative hashes, active windows, frequency caps, phone opportunity
policy, road direction and explicit billboard coordinate/road-fraction binding. It does
not snap coordinates, fetch creative or map data, run a simulation, or write an artifact.
Malformed, mismatched, off-network, out-of-bounds or unsupported-direction inputs exit 2
with bounded diagnostics. Catalog corruption exits 4. JSON mode emits one document with
the scenario/city hashes, channel counts and maximum accepted binding error.

## `adlife city [--pack FILE | --city-id ID] [--places FILE] [--agents N] [--days N] [--seed N] [--port N]`

Start the read-only geographic mobility pilot at `http://127.0.0.1:8765` (loopback
only). Without either selector, the viewer preserves the original explicitly fictional,
bundled v1 offline grid. `--pack` loads one versioned local city-pack JSON file;
`--city-id` selects an integrity-verified packaged v2 entry such as
`fictional-grid-v2`. The selectors are mutually exclusive. The HTTP API cannot open
paths or change the loaded pack. `--agents` accepts 1–250
(default 20), `--days` 1–31 (default 7), `--seed` defaults to 42, and `--port` defaults
to 8765. `--places` optionally supplies a validated place set bound to the selected
pack. Invalid packs or place sets exit 2 before the server starts. The interactive
server accepts only the default human output mode. It does not open a browser
automatically.

This command does not run or persist the advertising engine. Its minute-addressable
frames model illustrative home/work/leisure travel on local roads; they are not
traffic measurements or real-person predictions. See [city-pilot.md](city-pilot.md).

## `adlife city-run [PACK | --city-id ID] [--places FILE] [--spatial-campaign FILE] [--spatial-response FILE] --output-root ROOT --run-id ID [--agents N] [--days N] [--seed N]`

Save a deterministic city run under `ROOT/city-runs/ID`. Supply
exactly one local `PACK` or verified catalog `--city-id`; neither and both exit 2. The
validated pack is frozen into the artifact, so catalog replacement cannot change
replay. `--agents` accepts 1–30 (default 20), `--days` 1–7 (default 7), and `--seed`
defaults to 42. Creating an existing ID exits 3 without overwriting it.
The command hashes every minute frame and the generated fictional assignments. A
completed artifact contains `run.json`, `inputs/city.json`, and `inputs/agents.json`.
With `--places`, schema-v3 artifacts also freeze `inputs/places.json` and
`inputs/place-assignments.json` plus both content hashes.
With `--spatial-campaign`, schema-v5 artifacts additionally freeze
`inputs/spatial-campaign.json`, `outputs/spatial-opportunities.jsonl`, and
`outputs/opportunity-summary.json`, then derive `outputs/spatial-attention.jsonl` and
`outputs/attention-summary.json`. The command validates the scenario against the exact
selected city and duration before reserving the run ID. JSON output includes both claim
scopes, model/probability metadata, hashes, bytes/counts and funnel totals. Opportunity
records retain `synthetic-opportunity-not-impression`; attention records say
`synthetic-attention-not-observed-behavior`. Every opportunity becomes one synthetic
impression and an order-independent draw below the fixed 0.5 probability becomes noticed.
This is an uncalibrated assumption. It does not affect cognition, state, budget, movement,
or purchase probability. Older schema-v4 artifacts remain readable as opportunity-only
evidence.

`--spatial-response` requires `--spatial-campaign`. Supplying `--spatial-campaign` alone
continues to create schema-v5; supplying both flags creates schema-v6 under run model
`illustrative-road-spatial-response-study-v1`. Before reserving the run ID, the command
loads strict local JSON, binds every fictional profile to the exact agent-ID set generated
for the run, binds every campaign to the exact scenario, and validates complete
campaign-scoped initial state. The response
input is not bound to one mobility assignment; the saved run separately freezes and hashes
its generated assignments. The schema-v1 field contract and exact JSON shape are in
[city-pilot.md](city-pilot.md). V6 additionally freezes
`inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`. Only persisted notices
create `spatial-response-v1` rule responses and atomic state updates, each carrying
`synthetic-response-not-observed-behavior`. The bounded purchase-intention value is an
uncalibrated proxy, not purchase probability, sales, or a transaction; the path adds no
provider call, memory, social propagation, budget mutation, purchase event, or movement.

JSON success output includes response model/claim identity plus the exact v6 receipt keys
`response_input_sha256`, `response_stream_sha256`, `response_state_sha256`,
`response_summary_sha256`, `response_stream_bytes`, `response_count`,
`state_update_count`, `response_campaign_count`, and `final_state_count`.
`response_input_sha256` is the canonical input fingerprint excluding the saved file's
trailing newline; `response_stream_sha256`, `response_state_sha256`, and
`response_summary_sha256` cover exact persisted bytes, including JSONL line endings and the
trailing newline in each single-document state/summary file. Human output prints the same
values with readable labels. A malformed,
mismatched, non-finite, oversized, or sensitive response document exits 2 without reserving
the run ID. A separate read-only command may summarize persisted evidence; it does not turn
it into an observed outcome.
Interrupted or failed publication may leave an incomplete, reserved directory; use a
new run ID after examining it. JSON mode emits one result document on stdout.

## `adlife city-replay ROOT ID`

Load the frozen inputs, regenerate every minute frame, and compare the full trace hash and
counts. For schema-v4, also re-evaluate and compare the canonical opportunity stream and
independent summary. For schema-v5, additionally recompute and compare the canonical
attention stream and summary, including the fixed 0.5 model contract. Schema-v6 repeats all
of those checks, then re-evaluates the frozen response input and verifies the canonical
response stream, complete final-state document, summary, hashes, bytes, and counts. Success
emits
`"identical": true`; missing, damaged,
partial, or incompatible artifacts exit 4 without modifying the source files. This
proves replay against the saved artifact on a compatible implementation/runtime; it
is not cryptographic authentication against an owner rewriting all artifact files.
Saved city runs are not zone advertising runs and do not claim measured traffic,
real-person behavior, or externally valid geographic campaign effects.

## `adlife city-metrics ROOT ID [--layer attention|response]`

Validate a saved run and derive deterministic spatial metrics without changing or caching
anything in the artifact. `city-metrics --layer attention` is the default and preserves
the exact prior schema-v5/schema-v6 JSON shape: persisted opportunity and attention evidence only, under
`synthetic-metrics-not-observed-outcomes`, with exact numerator, denominator, and source
paths. It does not fold response records or final campaign state.

`city-metrics --layer response` requires schema-v6 and emits the separate
`spatial-response-metrics-v1` document under
`synthetic-response-metrics-not-observed-outcomes`. It reports response count, reach,
frequency, planned mean rule sentiment/recall deltas, and complete-grid initial/final/change
state receipts overall and by campaign. Channel response series are event-only; committed
nonlinear state is not allocated to a channel. Purchase intention is an uncalibrated proxy,
not purchase probability or sales. Corrupt, partial, older, or incompatible artifacts exit
4.

## `adlife city-compare ROOT CONTROL_ID TREATMENT_ID [--layer attention|response]`

Compare two fully verified runs only when city, place assignments, trace model, seed,
duration, and population match. `--layer attention` is the default, retains the exact prior
output, accepts schema-v5, schema-v6, or cross-version v5/v6 evidence, and never treats
response state as an attention metric. Same-run A/A produces exact-zero deltas. A changed
normalized placement/channel structure is `opportunity-confounded`, never a channel effect,
and the result carries `synthetic-comparison-not-causal-or-observed-effect`.

`city-compare --layer response` requires schema-v6 and emits
`spatial-response-metrics-comparison-v1` under
`synthetic-response-comparison-not-causal-or-observed-effect`. It independently classifies
opportunity structure as `matched-opportunity-structure` or `opportunity-confounded` and
numeric response assumptions as `matched-response-assumptions` or
`response-assumption-confounded`. All deltas are treatment minus control. The command never
modifies either run; provenance mismatch is invalid input and corrupt/incompatible artifacts
exit 4.

## `adlife city-study ROOT STUDY.json`

Analyze the explicit study definition's 2–100 saved, seed-matched run pairs using the
contiguous seeds `0..N-1`. Attention-only studies use homogeneous schema-v5 or schema-v6
runs; attention-and-response studies require schema-v6. Every source is verified, and
the command does not rerun, discover, modify, cache, or publish artifacts. There is no
output or discovery option. The strict path-free JSON definition is capped at 64 KiB.
JSON emits the fully revalidated compact result; human
output labels synthetic evidence, opportunity/response-assumption confounding, and
A/A status. The fixed paired bootstrap describes simulator seed variation, not
population confidence, sales, observed outcomes, or causal effects. Fewer than 50 seeds
are exploratory; 50 or more follow the full protocol, not a registered study. Invalid
definitions or scientific incompatibility exit 2; missing, corrupt, unsupported, or
failed verified A/A artifacts exit 4.

The result model is `spatial-paired-study-v1` with claim scope
`synthetic-study-not-observed-or-causal-effect`. One seed pair is the experimental unit;
agent, notice, event, opportunity, and state rows are never pooled across seeds. Exact
assignment, trace, and optional place-assignment hashes—not the seed number alone—establish
common-random-number evidence within a pair. Each statistic includes mean, sample standard
deviation, median, a deterministic 10,000-resample paired interval, nullable standardized
difference, sign counts, agreement, and the 0.8 direction rule; it emits no p-value.

## `adlife city-report ROOT STUDY.json`

Analyze the same explicit verified study as `city-study`, then publish a self-contained
zero-JavaScript evidence ledger at `ROOT/city-reports/<study_id>.html`. There is no output,
force or browser-opening option. Any existing destination exits 3 unchanged; report-directory
symlinks and junctions are refused. JSON emits the nine-field receipt with a relative POSIX
report path. Its exact fields are `schema_version`, `format_id`, `claim_scope`, `study_id`,
`study_definition_sha256`, `study_result_sha256`, `report_path`, `report_sha256`, and
`report_bytes`; hash and size cover the exact emitted UTF-8/LF bytes. The report is capped at
8 MiB, contains no JavaScript or network-capable resource, makes no network request, and
uses a CSP that permits only the exact hashed inline stylesheet. Scientific/input and
source-artifact errors retain `city-study` exit codes; filesystem and unexpected failures
exit 1, interruption 130.
Publication uses an atomic no-clobber hard link after fsyncing the complete file. Directory
fsync after the link is best effort: an unsupported or failed post-commit directory fsync is
non-fatal, so the contract does not promise the new directory entry survives sudden power
loss on every filesystem. Source city-run artifacts remain unchanged.

## `adlife city-view ROOT ID [--port N]`

Open a **validated saved** city mobility run in the same read-only browser timeline
at `http://127.0.0.1:8765` (loopback only). The run is fully verified before the
server binds; a corrupt or partial run exits 4. The HTTP API cannot select another
run or open a filesystem path. The header displays the saved run ID and artifact
schema version. For schema-v4, schema-v5, and schema-v6 runs, the viewer adds a read-only
current-minute opportunity
panel with persisted totals, channel counts and canonical records for the timeline
scrubber. Its bounded `/api/opportunities` endpoint accepts a required minute, optional
saved agent ID, offset and a maximum page size of 100; `/api/opportunity-summary` exposes
only validated scenario metadata and aggregate counts. Neither endpoint accepts a path or
changes the run. Every record remains `synthetic-opportunity-not-impression`; the viewer
does not invent a response or outcome. Schema-v5 and schema-v6 also add bounded
`/api/attention-events` and `/api/attention-summary` endpoints and displays persisted
current-minute attention evidence. Those records remain
`synthetic-attention-not-observed-behavior`; noticed means only that the deterministic draw
was below the uncalibrated 0.5 threshold. Schema-v6 additionally exposes the exact persisted
response artifact through `/api/response-summary`, bounded and filterable
`/api/response-events`, and bounded `/api/response-state`. The state endpoint returns
complete final end-of-run campaign state, not state at the scrubbed minute, and labels that
scope `final-end-of-run-not-scrubbed-minute`. All three routes retain
`synthetic-response-not-observed-behavior`, accept no path/run selector, and never mutate
the source artifact. The three current-minute evidence rails use fixed 100-record Previous
and Next pages with exact displayed ranges. They preserve canonical server order, reset to
the first page when the minute changes, retain the current page during same-minute agent
selection, and refuse stale or inconsistent page responses without replacing the last
committed evidence. Opportunity and attention map markers represent only the displayed
page; full-minute totals remain unchanged. `--port` accepts 1–65535. Like
`adlife city`, this interactive command accepts human output mode only and does not launch
a browser automatically. Schema-v5 and schema-v6 runs also expose a read-only
`/api/spatial-metrics` receipt ledger. It uses the same exact numerator, denominator, and
source paths as `city-metrics`, remains attention-only, and does not write to the artifact.
A fully prevalidated schema-v6 viewer additionally exposes the separate GET-only,
path-free `/api/spatial-response-metrics` document; mutation methods return 405 and older
or unconfigured runs return 404.

## `adlife city-import INPUT --output PACK --city-id ID --name NAME [--schema-version 1|2] [--largest-component]`

Convert a locally supplied, complete OpenStreetMap Overpass JSON street extract into
a validated city pack. This command performs no network request. Schema 1 remains the
compatibility default. Schema 2 additionally requires `--time-zone`, `--source-date`
and `--source-version`; it preserves explicit direction, complete way geometry through
successive nodes, exact bounds, normalized source provenance and declared omissions.
Input is limited to 16 MiB; output follows the city-pack 4 MiB / 10,000-node /
20,000-road limits. More than 100,000 total elements, 50,000 node elements, 50,000 way
elements, 100,000 way-member references or 20,000 selected road segments is refused.
The destination must be new; an existing file or symlink exits 3 and is not modified.
Malformed, incomplete, unsupported or disconnected geometry exits 2 without creating
an output. `--largest-component` explicitly discards all but the largest strongly
connected directed road component, with deterministic tie breaking and reported loss
counts. JSON mode returns the canonical city-pack fingerprint (`pack_sha256`, excluding
the file's trailing newline), counts and destination as one document; v2 also returns
bounded quality counts. The pack carries OSM ODbL attribution, but validation or import
does not grant commercial redistribution rights and does not add a real-world pack to
the catalog. See [city-pilot.md](city-pilot.md) for supported roads, rights review and
scientific limits.

## `adlife population generate [--size N] [--seed N] [--locale LC] [--out FILE] [--as FORMAT]`

Generate a fictional population document. `--size` 1–30 (default 20), `--seed` (default
42), `--locale` `fa-IR` (default) or `en-US`, `--out` writes YAML to a file instead of
stdout, `--as yaml|json` selects the output document format (default YAML).
Global `--format json|jsonl` selects JSON on stdout. An existing `--out` file is
refused with exit 3; machine mode reports the newly written file in a JSON result.

## `adlife campaign import PATH [--project-root DIR]`

Validate one campaign YAML against the project and print the normalized campaign as JSON.
Asset paths inside the campaign are resolved and confirmed to lie beneath the project root
before they are opened; anything else is refused (exit 2).

## `adlife run PROJECT [options]`

Run one study from its first tick to `run.completed`, persisting every artifact.

| Option | Meaning |
| --- | --- |
| `--campaign FILE…` | Replace the project's campaigns with these project-relative YAML files. |
| `--run-id ID` | Lowercase letters, digits and hyphens, beginning with a letter or digit, at most 40 characters. Windows device names are refused on every platform. An existing identifier exits 3. |
| `--mode rules\|hybrid\|replay` | Default `rules` needs no provider; `hybrid` uses the configured provider. The legacy `replay` value exits 2 and directs you to `adlife replay PROJECT RUN_ID`, which has the required frozen source identity. |
| `--days N` | Override simulated days (1–7). |
| `--population-size N` | Override population size (1–30). |
| `--seed N` | Override the run seed. |
| `--live` | Open the live dashboard when stdin and stdout are an interactive terminal and `TERM` is not `dumb`; otherwise fall back to headless. |
| `--headless` | Never open a dashboard (the default behaviour). |
| `--no-headless-fallback` | With `--live`: exit 2 instead of falling back when no terminal is available. |
| `--cache-dir DIR` | Cognition cache directory for hybrid/replay modes (default `cache/` under the study). |

`adlife --format json run PROJECT --live` and the corresponding `jsonl` combination
are refused (exit 2): a dashboard cannot share machine-readable stdout. Generated run
identifiers include a digest of the final scenario, seed and mode, including campaign
overrides. Identical inputs choose the same identifier and an existing run is refused.

## `adlife replay PROJECT RUN_ID [--provider-mode MODE] [--cache-dir DIR]`

Re-execute a stored run from its recorded inputs and verify the fresh event stream against
the recorded one. By default the provider mode is inferred from the source artifact;
`--provider-mode rules|hybrid|mock` explicitly overrides it. Mock replay uses the original
request identity. Hybrid replay validates cached successful answers and reconstructs
recorded rule fallbacks without constructing a live provider or reading credentials. Emits
`"replay-identical": true` on success. Corrupted artifacts exit 4; a mismatch is reported
as a failure, never repaired. A replay destination (`replay-<run-id>`) that already exists
is refused with exit 3: replay never deletes or overwrites run artifacts, and the stored
source run is never modified. Long source identifiers receive a digest suffix in the
derived replay identifier to avoid truncation collisions. Replay uses recorded model
parameters and ignores subsequent edits to project YAML.
For an interrupted source, replay verifies only the committed prefix up to its recorded
stop minute, including a stop before the first tick. A verified prefix exits 0 with
`"status": "interrupted"` and its `final_minute`; it does not execute later cognition.
Running or failed sources lack a supported terminal boundary and are refused with exit 4.

## `adlife compare CONTROL TREATMENT [--seeds N] [--treatment-path PATH]`

Paired whole-run comparison of two studies: both arms run per seed with the same
population, world, and keyed random streams. `--seeds` 1–200 (default: the quick-study
count, 20). `--treatment-path` declares which scenario document the treatment may differ in
(default `campaigns`). The A/A case (comparing a study with itself) must return exactly
zero paired differences.
The CLI normalizes the treatment's directory-derived scenario identifier and display
name to the control's before validation, so separate study directories can be compared.
The output label still names both directories. Population, initial state, world, duration
and all other scientific inputs retain the core's strict paired-design checks; a refused
design exits 2 with a concise diagnostic.
Comparison run identifiers use a bounded digest of the full comparison label, frozen
scenarios, declared treatment paths and seeds. Different comparisons can share a control
study without overwriting earlier runs; repeating the identical comparison exits 3.

## `adlife doctor [PATH] [--offline] [--no-color]`

Report installation readiness: CLI and core versions, packaged resources, provider
availability (mock and rules always; live providers only when not `--offline`), storage
writability, optional project schema check, and console encoding. `--offline` performs no
network access at all. Doctor returns a check report with `all_ok`; inspect that field
for readiness, since completed diagnostic checks return exit 0 even when a check fails.
Live probes refuse credential-bearing URLs and non-loopback plaintext HTTP.

## `adlife report PROJECT RUN_ID [--output FILE]`

Write the self-contained HTML report for a stored run (default destination
`reports/<run-id>.html` under the study). The report is a single file — charts, styles,
and the Plotly runtime inlined — that renders with the network disconnected, opens with a
synthetic-data disclosure, and ends with the manifest block needed to reproduce the run.
An output destination inside the study's `runs/` tree is refused with exit 3.
An existing non-HTML destination is also refused, protecting configuration, population,
campaign documents and other source files from being replaced by report output.
The default report directory must resolve inside the study, including through directory
links or junctions. An explicit `--output` may name a destination outside the study.

## `adlife demo [--headless] [--dir DIR] [--seed N]`

The offline demonstration: build a packaged demo study (20 agents, 3 simulated days, mock
provider, two campaigns), run it, and open the live dashboard when a real terminal
exists — `--headless` prints a summary instead. The study is created in a temp directory
unless `--dir` is given (an existing directory is refused). Requires no network and no
credentials. JSON and JSONL output automatically keep the demo headless.
The demo uses a location-independent scenario identity and a run identifier derived
from its frozen inputs and seed. With the same packaged inputs, seed and code, fresh
demo directories produce identical scenario, event and metric exports. Existing demo
directories are still refused; the destination does not alter mock responses.

## JSON output

`--format json` prints one document per command; `--format jsonl` prints one JSON object
per line for stream-shaped output (events, live progress). Diagnostics always go to
stderr, so `adlife --format json run my-study > doc.json` is clean piped output.
