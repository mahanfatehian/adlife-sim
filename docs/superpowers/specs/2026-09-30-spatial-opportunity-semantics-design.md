# Spatial Opportunity Semantics: Focused Design

> Status: approved production-roadmap slice for implementation on `main`. This design
> implements C2 of the production city platform roadmap. It derives auditable synthetic
> opportunity evidence; it does not create impressions, attention, cognition or outcomes.

## Outcome

Given one immutable `CityMobility` configuration and one validated
`SpatialCampaignScenario`, core code can deterministically answer two deliberately narrow
questions:

1. When did a synthetic route traverse an active roadside placement in its declared
   direction, pass its bound coordinate, and satisfy the versioned approximate-facing
   heuristic?
2. During which active synthetic activity-minutes did the keyed phone-use assumption
   produce an opportunity draw below its declared threshold?

The result is a canonical sequence of typed **synthetic opportunity** records plus
explicit stage counts. An opportunity is not called an impression, view, notice,
attention, memory, response, purchase or real-person observation.

## Architecture boundary

`src/adlife/core/simulation/city_mobility.py` exposes an immutable continuous road
traversal schedule without changing the existing minute-frame document. This preserves
all saved city trace hashes and replay behavior.

`src/adlife/core/simulation/spatial_opportunity.py` owns the pure evaluator. It may import
core domain and simulation modules only. It must not import CLI, storage, FastAPI,
Textual, Plotly or a provider adapter. C2 writes no artifacts and performs no I/O.

The evaluator revalidates the scenario/city binding and requires scenario duration to
equal mobility duration. It reads immutable inputs and returns immutable models. C3 may
later persist these records and turn them into a causal event stream, but must not alter
C2 semantics silently.

## Continuous traversal contract

`CityRoadTraversal` describes one directed physical road traversal in one planned leg:

- stable agent ID and zero-based day index;
- leg (`outbound` or `return`) and stable road sequence index;
- road ID and physical travel direction;
- absolute continuous start/end model minutes;
- distance in meters.

`CityMobility.road_traversals(day_index)` returns traversals ordered by agent, leg start,
and road sequence. It uses the same frozen paths, free-flow speeds and routine departures
as `frame()`. Invalid day indices are refused. No frame field or trace serialization is
changed.

## Causal chain and denominators

The evaluator publishes aggregate stage counts so later metrics cannot silently change
their denominator:

```text
road traversal
  -> active directional crossing
  -> bound-coordinate proximity passage
  -> approximate-facing passage
  -> per-placement daily cap
  -> roadside opportunity

eligible synthetic activity-minute
  -> keyed phone draw succeeds
  -> per-placement daily cap
  -> phone opportunity
```

The counts are:

- matching roadside traversals, before active-window filtering;
- active roadside crossings;
- proximity passages (zero modeled centerline distance at the bound road fraction);
- approximately visible passages;
- eligible phone agent-minutes;
- successful phone draws before caps;
- candidates refused by a frequency cap;
- emitted opportunities.

These are model-process denominators, not estimates of real traffic, devices, people or
viewability.

## Roadside opportunity v1

For each planned road traversal whose road ID and direction match a billboard placement:

1. Interpret `road_fraction` along the physical source-to-target polyline.
2. Convert it to progress in the declared travel direction (`f` forward, `1-f`
   backward).
3. Compute the continuous crossing time from traversal start and duration.
4. Quantize once to the nearest integer model millisecond. Active windows are half-open
   integer-minute intervals converted to milliseconds; a crossing at the end boundary is
   inactive.
5. Treat the path as passing the validated placement coordinate with zero modeled
   centerline distance. This is proximity evidence only.
6. Walk backward from the placement along the approach polyline by at most
   `max_view_distance_meters`. Compute the bearing from the placement toward that
   approach point.
7. Interpret `orientation_degrees` as the billboard face-normal bearing, clockwise from
   north. The approximate-facing heuristic passes when the smallest circular difference
   from the approach bearing is at most 90 degrees.
8. Apply the placement-scoped, agent-scoped, zero-based-day frequency cap in chronological
   order.

The emitted record includes crossing minute and millisecond, road/direction/fraction,
side, actual modeled approach distance and view-angle difference. Side is preserved as
declared attribution; because v1 binds the coordinate to the road centerline, it is not
misrepresented as measured lateral offset or line of sight.

The opportunity is anchored at the crossing instant. The approach point is a transparent
orientation heuristic, not an exposure-duration model. Intersections, buildings,
occlusion, lanes, traffic, speed variation, visual acuity and legal inventory are not
modeled.

## Phone opportunity v1

For every active integer model minute and synthetic agent:

1. Read the existing activity from the immutable city frame.
2. Count it as eligible only if the activity is in the placement's declared set.
3. Draw from the keyed namespace
   `spatial-phone-opportunity-v1:{campaign_id}:{placement_id}` using agent ID and absolute
   minute. Placement/scenario list order and the probability threshold are not part of
   the draw key, preserving common random numbers across policy variants.
4. A candidate succeeds exactly when `draw < opportunity_probability_per_minute`.
5. Apply the placement/agent/day cap in chronological order.

Phone opportunities occur at millisecond zero of the eligible minute and record the draw,
threshold and activity. Probability zero never succeeds; probability one always succeeds
until capped. The policy is an analyst-authored synthetic assumption, not observed device
use or location surveillance.

## Canonical opportunity record

Roadside and phone opportunity records are strict frozen discriminated models. Each
contains:

- schema and model version;
- SHA-256 opportunity ID derived from canonical immutable identity;
- scenario and city fingerprints;
- campaign, placement and agent IDs;
- channel-specific evidence;
- zero-based day, absolute model minute and millisecond within that minute;
- one-based ordinal under the placement/agent/day cap;
- the literal claim scope `synthetic-opportunity-not-impression`.

Records are sorted by absolute model time, agent, campaign, placement and channel before
caps and IDs are assigned. Input order, dictionary order and `PYTHONHASHSEED` cannot alter
the result. Event IDs do not use UUIDs, wall time or process state.

## Compatibility, scale and failure behavior

- Existing zone campaigns, city frames, trace hashes, saved manifests and replay remain
  byte-compatible.
- The evaluator accepts only matching city IDs/hashes and matching durations, and calls
  the C1 geometry validator before evaluation.
- Scenario limits bound placements, days and windows. Phone work iterates active window
  minutes only and stops evaluating a placement/agent/day after the cap is reached only
  when doing so cannot change published pre-cap denominators; otherwise it records the
  full bounded denominator honestly.
- No network, provider, credential or untrusted creative text enters the evaluator.
- An invalid geometry, direction or model invariant is a clean exception, never a partial
  result.

## Acceptance evidence

- Golden tests cover no crossing, one crossing, multiple placements in one minute,
  forward/reverse travel, curved geometry, shape-point boundaries, active-window start/end
  behavior and frequency caps.
- Phone tests cover activities, probability 0/1, exact keyed draws, common random numbers,
  day-scoped caps and order invariance.
- Tests prove stage denominators, stable IDs, strict/frozen outputs, non-finite rejection,
  input immutability and unchanged legacy city trace hashes.
- Property tests permute campaign/placement/window/activity order without changing results.
- Architecture, Ruff, mypy, full pytest, multiple hash seeds, branch coverage and wheel
  gates remain green.

