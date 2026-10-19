# City mobility pilot

Run `uv run adlife city` and open `http://127.0.0.1:8765` in a browser. The bundled
grid is fictional and works offline. The viewer is read-only, loopback-only, and uses
packaged HTML, CSS, and JavaScript. It shows synthetic agents on roads, activity at
each minute, a selected agent's planned directed route, an all-day timeline,
day/night presentation, and visible attribution.

The packaged catalog is also offline and content-addressed:

```bash
uv run adlife city-catalog list
uv run adlife city-catalog show fictional-grid-v2
uv run adlife city --city-id fictional-grid-v2
```

The packaged catalog contains three entries: `fictional-grid-v2`,
`fictional-harbor-loop-v2`, and `fictional-river-junction-v2`. All three have origin
`fictional` and qualification `fictional-fixture`; none is a real city or rights-review
claim. Catalog selection verifies each pack's canonical SHA-256 and public metadata before
use. The CLI catalog reads packaged resources only, makes no network request, and accepts
no arbitrary resource URL. Running `adlife city` without a selector retains the original
bundled fictional v1 grid.

## Local browser city workbench

Start the bounded single-operator workbench with:

```bash
uv run adlife city-workbench --workspace ./workbench
```

The command binds loopback only and never opens a browser automatically. Open
`http://127.0.0.1:8765` yourself and operate `CITY -> CAMPAIGN -> RUN -> INSPECT`. CITY
selects a re-verified packaged fictional city or registers one local CityPack V2 in the
current workspace. The browser sends file bytes, never its filename or path. Raw upload
and complete canonical stored content are each capped at 4 MiB; every other JSON write
route keeps the 128 KiB ceiling. CAMPAIGN chooses a complete packaged fictional creative
and bounded phone/billboard assumptions; the server derives roadside coordinates. RUN
validates without writing, then revalidates and submits one job. INSPECT lists only verified
completed artifacts and opens the immutable run-scoped timeline. The browser holds one
unsaved in-memory draft and never accepts a path, coordinate, creative copy, or arbitrary
URL. A provider credential is accepted only by its dedicated bounded route and is cleared
from the browser input after every outcome.

Every workspace upload is labelled `operator-supplied-unreviewed`. Technical validation
does not establish ownership, license compliance, redistribution rights, representative
coverage, or fitness for use. Source URLs are displayed as inert text and are not
dereferenced; default operation makes no outbound request. The operator must acquire and
use the source lawfully.

Seed input is an exact canonical decimal string from `0` through `9223372036854775807` and
is never converted through JavaScript `Number`. Polling exposes truthful coarse phases;
schema-3 uses `queued`, `simulating`, `selecting-explanations`,
`requesting-explanations`, `persisting`, `verifying`, and a terminal state, and schema 4 uses
`queued`, `planning-cognition`, `requesting-cognition`, `applying-cognition`, `persisting`,
`verifying`, and a terminal state. Cancellation is
cooperative before publication; persistence and the final independent artifact load are
not interrupted. Refreshing the page can resume the current server-owned job while the
same process lives. A process restart loses that operational record, while completed-run
discovery remains bounded, stable, and includes only artifacts that load through the full
`CityRunStore` integrity boundary. `/runs/{run_id}` reuses the read-only saved-run
inspector. Opening it verifies the complete artifact once and binds the page to a bounded,
process-local immutable snapshot through an opaque same-origin header token. Subsequent
page reads reuse that exact state; reloading the inspector and direct API reads without the
token verify current committed files again. Invalid, evicted, or cross-run tokens are
refused. For a response-capable transition, the GET-only
`/api/runs/{run_id}/timeline` projection returns the frame plus bounded
opportunity/attention/response/state pages from that one verified snapshot. It is a read
optimization, not a second scientific model; the individual endpoints remain
available for earlier schema capabilities and compatibility.

The inspector's timeline button replays saved presentation frames; pressing it at the end
restarts at minute zero. It does not re-execute the scientific model. Use
`adlife city-replay ROOT RUN_ID` for full deterministic recomputation and digest
verification against the immutable source artifact.

Default schema-3 workbench runs are schema-v9; schema-2/schema-v8 and
schema-1/schema-v7 requests remain compatible. They retain the schema-v6 opportunity, attention,
response, state, and metric semantics and add canonical `inputs/workbench.json`. Schema-4
bounded AI cognition runs are schema-v10, described [below](#bounded-ai-cognition-schema-v10).
The final
manifest binds `workbench_input_schema_version: 3` (4 for V10, 2 for V8, 1 for V7) and
`workbench_input_sha256`; the sidecar
freezes the accepted city, settings, scenario, population assumptions, and exact fictional
creative-template snapshot. Load and replay refuse any missing, noncanonical, changed, or
inconsistent sidecar. The accepted pack is also frozen as canonical `inputs/city.json`;
replay, metrics, reports, and inspection do not consult the current workspace registry.
Job IDs, phases, and timestamps are operational process-local state, not scientific inputs.

The Audience editor assigns explicit per-persona response assumptions to generated
synthetic agents. The schema-1 compatibility path retains a homogeneous cohort.
These are controllable modeling assumptions, not demographic or behavioral
calibration. Its model clock, day/night styling, fixed schedules, movement, exposures,
attention, and response state are illustrative synthetic evidence. Workbench studies are
capped at seven model days; month navigation, observed traffic, and calendar-accurate local
behavior are not claimed.

### Optional model assistance: explanation and bounded cognition sources

The RUN step's Model assistance section has two separate choices. **Operation** decides what
a model may do: `Explanation only` (schema 3, saved as V9) or `Bounded AI cognition`
(schema 4, saved as V10). **Source** decides where the output comes from: `Rules — offline`,
`Mock — reproducible demo`, `Local compatible model`, or `Remote compatible model`. Rules is
the offline default and mock mode is also deterministic, offline, and credential-free. The
fixed `local-ollama` origin approves `http://127.0.0.1:11434/v1`. To expose additional
startup-approved endpoints, run:

```bash
uv run adlife city-workbench --workspace ./workbench \
  --provider-origin ID=URL \
  --provider-key-env ID=ENVIRONMENT_VARIABLE
```

`--provider-origin ID=URL` is repeatable and approves an exact base URL; the fixed
`local-ollama` loopback origin is always available. `--provider-key-env
ID=ENVIRONMENT_VARIABLE` binds an approved remote ID to an environment-variable name, not a
secret value. The browser can submit a remote credential once to the OS keyring and can run
an explicit bounded compatibility check. A ChatGPT or Codex product login, subscription,
or OAuth token is not accepted. This local profile has no OAuth or multi-user authentication.

The adapter implements only a bounded OpenAI-compatible Chat Completions subset: a
non-streaming request to the exact approved `/chat/completions` target and a strict JSON
result. It does not discover URLs, follow redirects, or accept arbitrary headers. Schema-v9
explanations are a noncausal presentation layer after deterministic response evaluation:
they cannot change movement, exposure, attention, response values, campaign state, budget,
purchase probability, metrics, events, or persona identities. Provider failure or invalid
output becomes a deterministic, visibly labelled fallback.

Schema-v9 freezes `inputs/cognition.json`, `outputs/cognition.jsonl`, and
`outputs/cognition-summary.json`. Live provider explanations are nondeterministic; frozen
replay is deterministic and performs no provider call. The inspector renders each terminal
record beside its matching response and distinguishes AI-generated, deterministic, mock,
fallback, and budget-skipped explanations.

### Bounded AI cognition (schema-v10)

Operation `Bounded AI cognition` submits workbench schema 4 and publishes manifest V10 under
`spatial-response-v2`. Rules still compute movement, opportunities, attention, and every
deterministic baseline response with the `spatial-response-v1` formulas below. AdLife then
selects, in canonical `(model_minute, agent_id, campaign_id, baseline_response_id)` order, at
most 4 noticed responses per agent and 60 per run; every other eligible response is
`unselected` and keeps the neutral vector. For each selected response the source returns one
closed `CityCognitionResultV2`: the echoed request ID, the exact disclosure
`synthetic-bounded-cognition-not-observed-behavior`, and these categories.

| Field | Allowed values |
| --- | --- |
| `affect` | `annoyed`, `skeptical`, `neutral`, `curious`, `positive`, `uncertain` |
| `relevance` | `none`, `low`, `medium`, `high`, `uncertain` |
| `credibility` | `low`, `medium`, `high`, `uncertain` |
| `salience` | `none`, `low`, `medium`, `high`, `uncertain` |
| `safety_flags` | unique subset of `stereotype-risk`, `unsupported-inference`, `unsafe-content` |

There is no provider prose and no provider-authored number, identifier, state, event, budget,
probability, or purchase field; unknown fields, duplicate keys, a wrong request ID, truncation,
and refusal are invalid results. The causal request carries only interests, price sensitivity,
novelty seeking, advertising skepticism, impulsivity, the recall encoding of the response's
channel, the initial rule sentiment and recall, the campaign's target interests and relative
price, the creative (delimited as untrusted data), and the channel, time, frequency, and rule
factors. Name, age, occupation, background, and MBTI stay descriptive and are omitted from the
causal prompt, and purchase intention is never sent. A descriptive-only edit changes the
provenance hashes but not the causal request ID, the mock signal, the cache identity, or the
V10 numeric cognition.

AdLife, not the model, applies the frozen `city-cognition-influence-v1` policy. The neutral
vector `affect=neutral, relevance=medium, credibility=medium, salience=medium` is an exact
identity over the baseline. For a fully applicable vector:

| Affect | Adjustment | Relevance or salience | Factor | Credibility | Factor |
| --- | ---: | --- | ---: | --- | ---: |
| annoyed | -0.04 | none | 0.50 | low | 0.75 |
| skeptical | -0.02 | low | 0.75 | medium | 1.00 |
| neutral | 0.00 | medium | 1.00 | high | 1.10 |
| curious | 0.02 | high | 1.15 | | |
| positive | 0.04 | | | | |

```text
applied_sentiment_delta = clamp((s + affect_adjustment) * relevance_factor * credibility_factor, -0.20, 0.20)
applied_recall_delta = clamp(r * relevance_factor * credibility_factor * salience_factor, 0.00, 0.30)
```

Here `s` and `r` are the deterministic baseline `sentiment_delta` and `recall_delta`. An
`uncertain` category or any safety flag keeps the result auditable but applies the neutral
vector to the whole result; a result cannot be applied partially. Rules mode always uses the
exact neutral vector, and mock mode derives a deterministic, flag-free vector from each request
under `city-cognition-mock-signal-v1` without any network call. These tables are explicit
synthetic assumptions, not calibrated psychological effects.

Every agent/campaign pair keeps two tracks that start from the same initial sentiment and
recall. The rule track folds baseline deltas with the V6 equations and alone carries purchase
intention. The cognition track folds applied deltas:

```text
cognition_sentiment_after = clamp(cognition_sentiment_before + sum(applied_sentiment_delta), -1, 1)
cognition_recall_after = 1 - (1 - cognition_recall_before) * product(1 - applied_recall_delta)
```

All responses of one agent and campaign in the same minute read one immutable pre-minute rule
state and one immutable pre-minute cognition state and commit one atomic dual-state update, so
provider completion order cannot change the result. Every eligible response, including
unselected, fallback, budget-skipped, uncertain, and safety-neutralized ones, produces one
applied response, so final response counts match between the tracks and only sentiment and
recall may differ.

Rules and mock resolve every selected request offline. Local and remote sources fund 10, 20
(default), 40, or 60 requests; the remaining selected requests are budget-skipped and neutral.
Each funded request reserves 1,024 output tokens: at most 4 HTTP attempts of up to 256 output
tokens each. Input tokens and vendor charges are outside that reservation. A provider run
needs a passing bounded-signal compatibility check for the exact profile revision first.
Unavailable, timed-out, rate-limited, oversized, protocol-incompatible, or invalid answers
become typed neutral `fallback` records; corrupt cache content, cancellation, and storage or
verification failure fail the run instead.

`city-metrics --layer response` keeps the two tracks apart: the rule-baseline family uses the
receipts below and holds the uncalibrated purchase-intention proxy, while the
provider-dependent cognition family reports applied means, cognition-state receipts,
categories, sources, fallbacks, neutralization, and funded coverage as exploratory evidence,
not a campaign effect. The saved-run inspector shows the same separation for every response:
deterministic baseline, signal or neutral status, fixed mapping, applied cognition delta, and
the atomic rule/cognition state update. `city-compare --layer response`, `city-study`, and
`city-report` refuse V10 runs for now. The [live demo script](investor-demo.md) walks through
one run with a local Ollama model.

This local profile still lacks authentication or multi-user authorization, report download,
comparison/paired-study operation, a rights-reviewed real-city/worldwide catalog, calibrated
traffic/population behavior, and sales prediction. Operator-supplied packs remain unreviewed
for rights even when they pass every technical check.

## Local street packs: import or author

To use a **local street extract you are authorized to use**, import a complete local
OpenStreetMap Overpass JSON document and run:

```bash
uv run adlife city-import streets.json --output city.json --city-id my-city --name "My City"
uv run adlife city --pack city.json --agents 30 --days 7 --seed 42
```

Schema 1 remains the compatibility default. V2 preserves road geometry, explicit
direction, source provenance, exact bounds, an IANA time zone, fixed omission
disclosures, and stable way/end-node-derived road identities. Request schema 2 with:

```bash
uv run adlife city-import streets.json --output city-v2.json \
  --city-id my-city --name "My City" --schema-version 2 \
  --time-zone Asia/Tehran --source-date 2026-09-29 \
  --source-version local-extract-1
uv run adlife city --pack city-v2.json --agents 30 --days 7 --seed 42
```

The date, version, and IANA time-zone values are operator-supplied facts. The importer
validates their shape but does not independently verify provenance, license rights, or
fitness for a real-city catalog. `city-import` writes the requested local file; it does not
register that pack in either the packaged CLI catalog or a workbench workspace. Use the
workbench CITY upload control when a workspace-local immutable registration is intended.

The importer makes no network request. Acquire the extract yourself under the
[OpenStreetMap license and attribution requirements](https://www.openstreetmap.org/copyright).
For example, an Overpass query for a **small area** can request ways and all their
member nodes (replace the four bounding-box coordinates):

```text
[out:json][timeout:25];
way["highway"](south,west,north,east);
(._;>;);
out body;
```

Keep the input below 16 MiB; the resulting pack must fit the existing 4 MiB,
10,000-node and 20,000-road limits. Input is additionally capped at 100,000 total
elements, 50,000 node elements, 50,000 way elements, and 100,000 way-member
references. The importer preserves shared OSM vertices,
way shape and supported one-way directions; it includes motor-vehicle road classes
from motorway through service and corresponding link roads. Non-drivable classes and
roads restricted to non-car traffic are excluded. The most specific OSM access tag
(`motorcar`, then `motor_vehicle`, `vehicle`, `access`) controls inclusion; only `yes`,
`designated` and `permissive` are accepted. Vehicle-specific one-way tags override
generic direction. Unsupported conditional, directional-access, or reversible rules
are refused, not guessed. Node-level barriers or motor-vehicle access tags are also
refused because the graph cannot represent them safely. Schema 2 refuses an extract containing an OSM turn
restriction relation rather than silently losing its meaning. An Overpass response with a `remark`
is refused as potentially partial. The selected directed graph must be
strongly connected. If the extract is disconnected, the default is to refuse it;
`--largest-component` explicitly keeps the largest strongly connected component and
reports the number of routable nodes and road segments discarded. This can omit large
parts of a city, so inspect the result before using it. Conversion of the same input
is byte-stable independent of element ordering. Existing outputs are never replaced.
More than 20,000 selected input road segments is refused before graph selection, even
with `--largest-component`; this protects memory and prevents implicit truncation.

Imported packs contain `ODbL-1.0`, `© OpenStreetMap contributors`, and the OSM copyright
URL; tags or contributor contact details are not copied into the pack. Importing road
geometry does **not** make this a real traffic or population model. OSM turn restrictions,
time-dependent access, speed limits, intersections without shared nodes, traffic,
transit and land use are not modeled. A legal driving route is not guaranteed. The
current city pilot is a spatial preview, not a routing/navigation product.
The importer's `pack_sha256` is the canonical city-pack model fingerprint used by the
viewer API; it does not include the output file's trailing newline.

For schema 2, `source_sha256` hashes a canonical projection of supported road ways,
their ordered member IDs, referenced coordinates, and only the tags used for road
class, car access, junction, and direction decisions. JSON element/tag order and
irrelevant or private tags do not affect that hash or enter the pack; coordinates or
decision-relevant tags do. The JSON command result includes bounded aggregate quality
counts for input nodes/ways, eligible and excluded ways, retained nodes/roads, and any
nodes/roads discarded by explicit largest-component selection. These technical checks
are not legal review, city qualification, traffic validation, or navigation certification.

You can still author a pack directly:

```bash
uv run adlife city --pack path/to/city.json --agents 30 --days 7 --seed 42
```

The pack format is intentionally explicit. Example (fictional geometry):

```json
{
  "schema_version": 1,
  "city_id": "sample-city",
  "name": "Fictional Sample City",
  "source_url": "https://example.org/fictional-map",
  "license": "CC0-1.0",
  "attribution": "Fictional streets for demonstration",
  "nodes": [
    {"node_id": "a", "longitude": 0.0, "latitude": 0.0},
    {"node_id": "b", "longitude": 0.01, "latitude": 0.0},
    {"node_id": "c", "longitude": 0.01, "latitude": 0.01}
  ],
  "roads": [
    {"road_id": "ab", "source_node": "a", "target_node": "b", "kind": "residential"},
    {"road_id": "bc", "source_node": "b", "target_node": "c", "kind": "primary"},
    {"road_id": "ca", "source_node": "c", "target_node": "a", "kind": "residential"}
  ]
}
```

Coordinates are WGS84 degrees; a road is a segment between its endpoint nodes.
`one_way: true` permits traversal only from `source_node` to `target_node`. Two-way is
the default. Road kinds are `motorway`, `trunk`, `primary`, `secondary`, `tertiary`,
`residential`, `service`, and `path`. Break a curved way into successive segments;
unbroken endpoints are rendered as straight lines. Shared intersections must reuse
the *same* node ID. The directed graph must be strongly connected, so every selected
home/work pair can make a return trip. Files are limited to 4 MiB, 10,000 nodes and
20,000 roads. Invalid, disconnected, duplicate, non-finite and zero-length geometry
is refused before startup. Date-line crossings are also refused because the pilot's
flat canvas cannot draw them faithfully. Input order does not affect the pack hash or trace.
Schema 2 replaces `one_way` with explicit `directions`, adds optional intermediate
`shape` points, exact `bounds`, an IANA `time_zone`, structured source provenance, and
declared omissions. The local OSM converter represents every adjacent OSM member pair
as a stable road segment, so its `shape` is empty while the complete way geometry is
retained through successive shared nodes.

OpenStreetMap data is available under the [ODbL](https://www.openstreetmap.org/copyright).
The importer preserves its license and displays `© OpenStreetMap contributors` as
attribution, with `source_url` linking to the copyright page. Do not copy Google Maps
or unlicensed map content. There is no automatic city search or downloader; data
acquisition and license compliance remain the operator's responsibility. No public
map-tile service, geocoder, downloader, or arbitrary URL loader is used.
Importer validation is technical evidence, not permission to distribute a database.
ODbL/commercial redistribution needs recorded rights review before a real-world pack
can be marked `rights-reviewed` or bundled. Follow the
[city-source qualification checklist](data/city-source-qualification.md); this release
has no rights-reviewed real-city catalog entry.

## Synthetic place inputs

The mobility model can optionally use a local schema-v1 place set instead of generating
unlabelled node assignments. The document is bound to the exact city ID and canonical
pack SHA-256, contains 3–10,000 bounded public labels, and supplies at least one home,
workplace and leisure point. Each point references an existing road node and declares
one provenance method: `operator-authored-fictional`, `source-derived`, or `inferred`.
The latter two require a bounded public reference; fictional authored points must not
claim one. Do not put addresses, personal data, credentials or private source URLs in
labels or references.

```json
{
  "schema_version": 1,
  "city_id": "fictional-grid-v2",
  "city_sha256": "<copy pack_sha256 from city-catalog show>",
  "name": "Fictional study places",
  "places": [
    {"place_id": "home-west", "kind": "home", "node_id": "west-north", "label": "Fictional west home", "provenance": {"method": "operator-authored-fictional"}},
    {"place_id": "work-center", "kind": "workplace", "node_id": "center-center", "label": "Fictional center workplace", "provenance": {"method": "operator-authored-fictional"}},
    {"place_id": "leisure-south", "kind": "leisure", "node_id": "center-south", "label": "Fictional south leisure", "provenance": {"method": "operator-authored-fictional"}}
  ]
}
```

Supply at least as many distinct home points as agents. This three-place example supports
one agent. Validation checks the binding,
node references, capacity, deterministic assignment and every required directed route:

```bash
uv run adlife city-places validate places.json --city-id fictional-grid-v2 --agents 1 --days 7 --seed 42
uv run adlife city --city-id fictional-grid-v2 --places places.json --agents 1 --days 7 --seed 42
```

Place input order does not change the fingerprint or keyed assignments. A workplace or
leisure selection cannot equal that agent's home node. The viewer presents place glyphs,
the selected agent's three labels and provenance, but stays read-only.

## Spatial campaign and response inputs

A separate schema-v1 spatial campaign document can be checked against a local or catalog
city before any advertising model exists:

```bash
uv run adlife city-campaign validate spatial-campaign.json --city-id fictional-grid-v2
```

The document is capped at 2 MiB and binds `city_id` plus `city_sha256`, 1–20 fictional
campaigns and 1–500 placements. Each campaign carries the SHA-256 of its creative bytes.
Every placement references a campaign, uses non-overlapping absolute model-minute windows
within 1–7 days, and declares a per-agent/per-day cap.

A `roadside-billboard` supplies a stable road ID, supported travel direction, physical
source-to-target road fraction, explicit WGS84 coordinate, left/right side, orientation
and bounded approximate view distance. Validation interpolates all road shape points and
allows at most one meter of authoring/serialization error; it never moves the coordinate.
Endpoints, unknown roads, wrong directions, out-of-bounds and off-network coordinates are
refused. A `mobile-feed` supplies the versioned `keyed-activity-minute-v1` policy, eligible
synthetic activities and an analyst-authored per-minute probability. V1 permits one phone
policy per campaign so extra records cannot silently multiply opportunity.

The pure C2 core evaluator can now combine this contract with the immutable mobility model
to derive typed synthetic opportunity evidence. It keeps the causal stages separate:

```text
matching directed road traversal
  -> active crossing -> <=1 m bound-coordinate passage
  -> approximate-facing heuristic -> cap -> roadside opportunity

active eligible activity-minute
  -> keyed phone threshold success -> cap -> phone opportunity
```

Road crossings use continuous route time quantized once to model milliseconds. A
billboard face normal passes the disclosed heuristic only when its circular angle from a
bounded approach point is at most 90 degrees. Phone draws use an order-independent
SHA-derived SplitMix64 counter stream keyed by seed, campaign, placement and fictional
agent, with absolute minute as counter. Caps are scoped to placement, agent and day.
Evaluation exposes matching, active, proximity, approximate-facing, phone-eligible,
threshold-success, capped and emitted counts so a later metric cannot silently substitute
its denominator.

Every opportunity record says `synthetic-opportunity-not-impression`. The phone probability
is not observed phone behavior; road proximity and approximate orientation are not measured
visibility or attention. C2 remains pure; C3a added a schema-v4 storage adapter for the
canonical opportunity stream and summary. C3b adds a separate schema-v5 attention evidence
layer: every opportunity becomes one synthetic impression and an order-independent keyed
draw below the fixed 0.5 threshold is labeled noticed. The neutral probability is
uncalibrated, identical across channels, and does not use campaign copy,
campaign/creative identity or provider output. Every record says
`synthetic-attention-not-observed-behavior`.

C3c is an opt-in deterministic response layer. `--spatial-response` requires
`--spatial-campaign`: supplying only `--spatial-campaign` preserves the schema-v5
opportunity/attention contract, while supplying both creates schema-v6. The strict response
JSON is bound to the exact city and scenario fingerprints. It contains one bounded trait
profile for every member of the exact agent-ID set generated for the run, one set of
relative-price/target-interest assumptions per scenario campaign, and a complete initial
campaign-scoped state for every agent/campaign pair. It is not bound to one mobility
assignment; each saved run separately freezes and hashes its generated assignments. Campaign
and creative hashes must match the scenario. Only persisted `spatial.noticed` records produce
responses; ignored impressions remain no-ops.

This is the exact minimal schema-v1 JSON shape for one fictional agent and one campaign.
Replace the hashes and IDs with the matching city/scenario values, then include one profile
for every generated agent, one campaign entry for every scenario campaign, and the complete
agent-by-campaign `initial_states` product. All six trait values, `recall_strength`, and
`purchase_intention` are in `[0, 1]`; `brand_sentiment` is in `[-1, 1]`.

<!-- spatial-response-input:start -->
```json
{
  "schema_version": 1,
  "city_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "scenario_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
  "profiles": [
    {
      "agent_id": "person-001",
      "fictional": true,
      "interests": ["coffee"],
      "traits": {
        "price_sensitivity": 0.5,
        "novelty_seeking": 0.5,
        "advertising_skepticism": 0.5,
        "mobile_recall_encoding": 0.5,
        "roadside_recall_encoding": 0.5,
        "impulsivity": 0.5
      }
    }
  ],
  "campaigns": [
    {
      "campaign_id": "demo-campaign",
      "creative_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "target_interests": ["coffee"],
      "relative_price": 1.0
    }
  ],
  "initial_states": [
    {
      "agent_id": "person-001",
      "campaign_id": "demo-campaign",
      "brand_sentiment": 0.0,
      "recall_strength": 0.0,
      "purchase_intention": 0.0
    }
  ]
}
```
<!-- spatial-response-input:end -->

`relative_price` is the dimensionless advertised price divided by an analyst-declared
category reference price and must be in `(0, 100]`; this slice models neither currency nor a
consumer budget. In the recall formula, `channel_recall_encoding` selects
`mobile_recall_encoding` for `mobile-feed` and `roadside_recall_encoding` for
`roadside-billboard`, after the attention model has already emitted a notice.

The transparent `spatial-response-v1` evaluator uses these exact formulas (`clamp` limits a
value to the stated interval and Jaccard is set intersection divided by set union):

```text
interest_match = Jaccard(profile interests, campaign target interests)
affordability = clamp(1.25 - relative_price * price_sensitivity, 0, 1)
value_match = 0.55 * interest_match + 0.25 * novelty_seeking + 0.20 * affordability
frequency_fatigue = min(1, prior_notices_today / frequency_cap_per_agent_per_day)
sentiment_delta = clamp(
  0.18 * value_match - 0.12 * advertising_skepticism - 0.06 * frequency_fatigue,
  -0.2, 0.2
)
recall_delta = clamp(
  0.22 * channel_recall_encoding + 0.12 * novelty_seeking - 0.08 * frequency_fatigue,
  0, 0.3
)
sentiment_after = clamp(sentiment_before + sum(sentiment_delta), -1, 1)
recall_after = 1 - (1 - recall_before) * product(1 - recall_delta)
purchase_intention_after = clamp(
  0.40 * ((sentiment_after + 1) / 2) + 0.25 * value_match +
  0.20 * recall_after + 0.15 * impulsivity,
  0, 1
)
```

All notices for an agent/campaign in the same minute read one immutable pre-minute state and
the same pre-minute fatigue counters; they cannot affect one another while planning. Their
bounded deltas are combined in canonical order and commit as one commutative, atomic state
update. State is isolated by campaign. A later minute intentionally reads the committed
state, and the daily placement notice counter resets by day without silently resetting the
campaign state.

Every response and state-update record says
`synthetic-response-not-observed-behavior`. The run model is
`illustrative-road-spatial-response-study-v1`; the evaluator is `spatial-response-v1`, the
artifact summary is `spatial-response-artifact-v1`, and the final-state document is
`spatial-response-state-v1`. Purchase intention is an uncalibrated bounded proxy, not
purchase probability, sales, or a transaction. The slice adds no cognition, prose provider
output, memory, social propagation, budget mutation, purchase event, or movement change.

The saved-run viewer can inspect persisted current-minute opportunity and
current-minute attention evidence without changing it. Schema-v6, schema-v7, schema-v8, and schema-v9 additionally
expose current-minute response/state-update records and complete final end-of-run campaign
state.
Opportunity, attention, and response rails page immutable records in canonical order with a
fixed 100-card bound and exact ranges. Minute changes return every rail to offset zero;
same-minute agent selection keeps the displayed pages because selection is presentation-only.
A delayed, failed, or inconsistent page cannot replace the last committed evidence, and map
markers are explicitly scoped to the displayed opportunity or attention page.
The original read-only `spatial-metrics-v1` projection remains attention-only for
schema-v5, schema-v6, schema-v7, schema-v8, and schema-v9. It never folds response scores or final state into
metrics; every value records its exact numerator, denominator, and source paths under
`synthetic-metrics-not-observed-outcomes`.

Schema-v6 also has a separate additive `spatial-response-metrics-v1` projection under
`synthetic-response-metrics-not-observed-outcomes`. For a filtered canonical set of direct
rule-response records `R`, its event receipts are:

```text
response_count = len(R)
response_reach = unique responding agents / population_size
response_frequency = len(R) / unique responding agents
mean_rule_sentiment_delta = fsum(response.sentiment_delta) / len(R)
mean_rule_recall_delta = fsum(response.recall_delta) / len(R)
```

Zero-event frequency and means use numerator 0, denominator 0, and value `0.0`. For a
state field over the complete matched initial/final agent-campaign set `S`, the receipt is:

```text
initial_total = fsum(initial.x)
final_total = fsum(final.x)
change_total = final_total - initial_total
initial_mean = initial_total / len(S)
final_mean = final_total / len(S)
mean_change = change_total / len(S)
```

Direct-response receipts name `outputs/spatial-responses.jsonl` and event type
`spatial.response`; state receipts name `inputs/spatial-response.json` and
`outputs/response-state.json`. Overall state weights every complete agent/campaign pair
equally, each campaign contains exactly one state per fictional agent, and a campaign with
no response remains visible with zero event values and unchanged state.

State fields are brand sentiment, recall strength, and purchase-intention proxy. Response
events can be grouped by channel, but those channel series are event-only: committed state
is not attributed to a channel because same-minute notices may share one nonlinear update.
`mean_rule_recall_delta` is the planned encoding term, whereas
`recall_strength.mean_change` is the committed nonlinear state change after saturation.
In this model one persisted notice mechanically creates one response; response counts are
rule-processing counts, not engagement, persuasion, or observed acceptance. Purchase
intention remains an uncalibrated internal proxy, never purchase probability or sales.

Use `city-metrics --layer response` and `city-compare --layer response` for this V6/V7/V8/V9
layer. The default `--layer attention` preserves the exact prior output. Response
comparison reports `matched-opportunity-structure` or `opportunity-confounded` separately
from `matched-response-assumptions` or `response-assumption-confounded`. The viewer exposes
the same verified response projection through GET-only `/api/spatial-response-metrics`;
`/api/spatial-metrics` remains attention-only. Response comparisons carry
`synthetic-response-comparison-not-causal-or-observed-effect`.

Schema-v10 runs use the separate version-2 response metrics described under
[Bounded AI cognition](#bounded-ai-cognition-schema-v10): `city-metrics --layer response`
prints the rule-baseline family, computed with exactly these receipts, separately from the
provider-dependent cognition family, and `city-compare --layer response` refuses V10 runs.

## Repeated-seed spatial studies

`city-study` analyzes an explicit 2–100-pair definition over already committed runs. One
seed pair is the experimental unit; it never pools agents, notices, events, opportunities,
or state rows across seeds. Exact assignment, trace, and optional place-assignment evidence
must match within a pair—the declared seed alone does not prove common random numbers.
Attention scope accepts homogeneous schema-v5 (`all-v5`), schema-v6 (`all-v6`),
schema-v7 (`all-v7`), schema-v8 (`all-v8`), or schema-v9 (`all-v9`) inputs;
attention-and-response scope accepts homogeneous schema-v6 (`all-v6`), schema-v7
(`all-v7`), schema-v8 (`all-v8`), or schema-v9 (`all-v9`) inputs. Opportunity classification is independently
`matched-opportunity-structure` or `opportunity-confounded`; response assumptions are
independently `matched-response-assumptions` or `response-assumption-confounded`.
Schema-v10 runs are refused in both scopes for now, so `city-report` cannot publish them
either: no paired-cognition protocol yet freezes compatible signals across arms.

The `spatial-paired-study-v1` result carries
`synthetic-study-not-observed-or-causal-effect`. Each treatment-minus-control statistic
contains a mean, sample standard deviation, median, deterministic 95% bootstrap interval,
nullable paired standardized difference, sign counts, agreement, and a direction label.
Each metric uses an independently keyed SplitMix64 stream to generate 10,000 resamples.
The resampled means are sorted, and zero-based elements 249 and 9749 form the interval.
Positive or negative direction requires at least 0.8 of
all seed pairs; zeros stay in that denominator, all-zero is `stable-null`, and no p-value
is produced. The result describes simulator seed variation only. Two to 49 pairs are
exploratory; 50–100 are `full-protocol-50-or-more-seeds`, not a registered,
representative, validated, or statistically powered study.

Bounded C4b evidence is complete: `city-report` turns the verified result into a fixed-path
zero-JavaScript evidence ledger. Broader C3/C4 memory, social propagation and rule-owned
purchase integration remain open, as do V10 studies and reports, authentication,
calibration, and external validity.

Weekdays place fictional agents at home until 08:00, at work after road travel, and
return them at 17:00. Weekends replace work with a leisure visit from 11:00 to 16:00.
Day 1 is treated as Monday. The UI's light/dark styling switches at fixed 06:00 and
19:00 clock times; it is not a calculation of local sunrise, sunset, or time zone. An
IANA time zone is recorded as provenance but does not make the fixed schedule
calendar-accurate: there is no start date, holiday calendar, daylight-saving policy or
local solar calculation.
Routes minimize free-flow travel time using fixed illustrative per-road-kind speeds.
The speeds are motorway 60 km/h, trunk 48, primary 36, secondary 30, tertiary 24,
residential 18, service 12, and path 4.8. They are assumptions, not measured speeds.
If arrival is later than the nominal return time, return travel starts on arrival;
an assignment that still cannot finish before midnight is refused at startup instead
of producing a discontinuous position. The selected-agent panel displays the
planned directed leg and highlights its road segments.
Each minute's position is interpolated along the selected directed road segments;
none is an observed trajectory. The API reports city-pack SHA-256, seed, agent count,
days and model identifier for an exact-input trace under the same implementation and
compatible runtime. Cross-platform bitwise identity of floating-point interpolation
is not asserted. The ephemeral `city` command does **not** persist a run or use the
campaign replay command.

For a saved, replayable city run, use either a local pack or the verified fictional
catalog entry:

```bash
uv run adlife city-run city.json --output-root ./city-output --run-id study-42 --agents 20 --days 3 --seed 42
# alternatively: uv run adlife city-run --city-id fictional-grid-v2 --output-root ./city-output --run-id study-42
# place-aware: add --places places.json (uses run manifest v3)
# spatial opportunity + attention evidence: add --spatial-campaign spatial-campaign.json (uses schema-v5)
# bounded response/state evidence: also add --spatial-response spatial-response.json (uses schema-v6)
uv run adlife city-replay ./city-output study-42
uv run adlife city-view ./city-output study-42
```

Metrics and studies require campaign-backed artifacts; a mobility-only `study-42` is not
a valid input. After authoring and validating the campaign and response files described
above, this seed-0/seed-1 schema-v6 A/A flow is executable without network access:

```bash
uv run adlife city-run --city-id fictional-grid-v2 \
  --spatial-campaign spatial-campaign.json --spatial-response spatial-response.json \
  --output-root ./city-output --run-id response-0 --agents 1 --days 1 --seed 0
uv run adlife city-run --city-id fictional-grid-v2 \
  --spatial-campaign spatial-campaign.json --spatial-response spatial-response.json \
  --output-root ./city-output --run-id response-1 --agents 1 --days 1 --seed 1
uv run adlife city-replay ./city-output response-0
uv run adlife city-replay ./city-output response-1
uv run adlife city-metrics ./city-output response-0 --layer response
uv run adlife city-compare ./city-output response-0 response-0 --layer response
```

Save this exact definition as `spatial-study.json`; an A/A definition may reuse one
verified run for both arms of each seed pair:

<!-- spatial-study-definition:start -->
```json
{
  "schema_version": 1,
  "study_id": "response-aa",
  "design": "a-a",
  "analysis_scope": "attention-and-response",
  "pairs": [
    {
      "seed": 0,
      "control_run_id": "response-0",
      "treatment_run_id": "response-0"
    },
    {
      "seed": 1,
      "control_run_id": "response-1",
      "treatment_run_id": "response-1"
    }
  ]
}
```
<!-- spatial-study-definition:end -->

```bash
uv run adlife city-study ./city-output spatial-study.json
uv run adlife city-report ./city-output spatial-study.json
```

The report is published once at
`./city-output/city-reports/response-aa.html`; a second publication refuses to clobber it.

`city-run` reserves a new ID and freezes the validated pack, generated fictional
home/work/leisure assignments, seed, model/runtime identity, and hashes of every
minute's normalized positions. Saved runs are limited to 30 agents and seven days;
the ephemeral preview retains its wider bounds. With `--places`, v3 also freezes the
canonical place set and assignment document and binds both hashes in the final manifest.
With `--spatial-campaign`, schema-v5 freezes `inputs/spatial-campaign.json`,
`outputs/spatial-opportunities.jsonl`, and `outputs/opportunity-summary.json`; its final
manifest binds the scenario, stream and summary hashes plus exact stream bytes/count. It
also freezes `outputs/spatial-attention.jsonl` and `outputs/attention-summary.json` and
binds the attention model, fixed 0.5 probability, claim scope, hashes, bytes and counts.
With `--spatial-response`, schema-v6 retains all v5 files and additionally freezes
`inputs/spatial-response.json`, `outputs/spatial-responses.jsonl`,
`outputs/response-state.json`, and `outputs/response-summary.json`. The v6 manifest binds
their hash and count receipts before it is published last. `response_input_sha256` is the
canonical response-input fingerprint excluding the saved file's trailing newline.
`response_stream_sha256`, `response_state_sha256`, and `response_summary_sha256` hash exact
persisted bytes, including the JSONL line endings and the trailing newline in each
single-document state/summary file; the manifest also records stream bytes,
response/state-update counts, campaign count, and complete final-state count. Earlier
v1/v2/v3 runs and schema-v4 opportunity-only runs remain readable. `city-replay` refuses a changed,
missing, incompatible, or partial artifact and never repairs or mutates the source.
`city-view` validates the full trace before opening a loopback-only viewer; its HTTP
API has no path or run-selection endpoint. For v4, v5, v6, v7, v8, v9, and v10, a bounded
summary endpoint
and canonical 100-record pages of current-minute opportunity records drive the read-only
evidence rail and page-scoped map markers. V5, v6, v7, v8, v9, and v10 add bounded summary
and page endpoints
for current-minute attention; noticed markers reflect only persisted model evidence. The
browser can traverse every opportunity, attention, and response page with exact ranges while
keeping only one bounded page per rail in the DOM. Selecting a record changes presentation
only. The panels retain the literal synthetic claim scopes and create no downstream event.
V6, V7, V8, V9, and V10 add `/api/response-summary`, paged `/api/response-events`, and paged
`/api/response-state`; V10 pages carry applied responses with their dual-state updates and
pair each final rule state with its cognition state. The state endpoint always labels its values
`final-end-of-run-not-scrubbed-minute`: changing the timeline does not pretend that final
state belongs to the scrubbed minute. Schema-v5, schema-v6, schema-v7, schema-v8, schema-v9,
and schema-v10 runs also expose
`/api/spatial-metrics` and a metrics evidence ledger. The projection is derived only after
full artifact validation and has exact
numerator/denominator/source receipts; it remains attention-only and is not written back to
the run. A fully prevalidated schema-v6, schema-v7, schema-v8 or schema-v9 viewer additionally exposes the
separate GET-only `/api/spatial-response-metrics` projection as a full-run response ledger;
it is likewise derived in memory, never written back, and keeps channel receipts event-only
rather than inventing channel attribution for nonlinear committed state. A schema-v10 viewer
serves the version-2 rule-baseline and cognition metrics there instead, plus its bounded
cognition summary, terminal records, and frozen workbench input.
`city-compare` requires matched city, assignment, trace, seed, duration, and population
provenance. Identical A/A inputs produce exact-zero deltas. A different normalized
placement/channel structure is labeled `opportunity-confounded`, and every result carries
`synthetic-comparison-not-causal-or-observed-effect` rather than a causal or observed claim.
The header shows the saved ID and schema version. A crash during publication can leave an
incomplete, reserved run directory: inspect it and choose a new ID; no command overwrites
it silently. Integrity hashes
detect accidental or adversarial edits to individual files but do not authenticate
against an owner who rewrites the entire artifact consistently.

This is an early product-track slice. The packaged browser now operates the complete
local single-run city workbench workflow with optional noncausal provider explanations and
bounded AI cognition signals, but
authentication, OAuth, browser comparison/report operation, rights-reviewed real-city
data, traffic data, and population calibration are not implemented. Schema-v6 through
schema-v9 saved runs retain bounded, uncalibrated rule response and campaign-state evidence
over schema-v5 opportunity/attention evidence; schema-v7 through schema-v10 additionally
freeze workbench inputs. Schema-v9 adds explanation artifacts but no causal cognition,
memory, social, or purchase events. Schema-v10 keeps the same rule evidence as a baseline and
adds bounded cognition signals that change only a separate synthetic cognition state, still
without memory, social, budget, or purchase events. None is a
validated geographic advertising-outcome study. Descriptive, noncausal MBTI labels are
allowed as optional fictional context. Using MBTI as a causal or predictive input is
not implemented and is not scientifically validated by this simulator.

Workspace CityPack V2 uploads broaden the selectable geometry, not the validity claim:
they are operator-supplied, unreviewed for rights, and cannot turn synthetic agents or
uncalibrated response equations into observations of a real population.

## Audience authoring and saved identities

CAMPAIGN includes Audience controls backed by GET /api/persona-templates: four wholly
fictional starting points with explicit causal values. Add, duplicate, remove and edit
personas; the last persona cannot be removed. Count totals must match the run's 1–30 agents,
and runs stay within 1–7 days. Editing any assumption invalidates the validation receipt.
IDs cannot be renamed. The saved schema-v8/schema-v9 inspector shows names beside immutable agent IDs
from verified assignments, with biography/MBTI collapsed and labeled noncausal.

Name, age, occupation, background and MBTI never infer numeric traits or mobility. Explicit
interests, six response traits and initial campaign state are the causal response inputs;
IDs/counts/assignments determine structure. Templates are not demographic samples. Screening
refuses recognized personal data/secrets and unsafe text but cannot prove fictional identity.
Purchase intention remains an uncalibrated proxy; no purchase transactions are simulated.
