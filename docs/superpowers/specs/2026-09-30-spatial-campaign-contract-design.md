# Spatial Campaign Contract: Focused Design

> Status: approved production-roadmap slice for implementation on `main`. This design
> implements C1 of the production city platform roadmap. It defines and validates
> geographic campaign inputs; it does not yet generate advertising events or outcomes.

## Outcome

An analyst can author a bounded, versioned spatial campaign scenario for one immutable
city pack and validate it offline before any simulation starts. A scenario identifies
fictional campaigns, creative hashes, active windows and frequency caps. Billboard
placements bind an explicit coordinate and physical road fraction to one stable road and
supported travel direction; phone placements declare a versioned synthetic opportunity
policy. Invalid, ambiguous, off-network or mismatched inputs are refused, never snapped or
silently repaired.

This milestone does **not** call proximity an impression, decide whether an agent notices
an ad, alter mobility, invoke cognition, persist campaign events, or predict sales. Those
semantics belong to C2â€“C4 after this input boundary is stable.

## Architecture boundary

`src/adlife/core/domain/spatial_campaign.py` owns immutable contracts, canonicalization,
fingerprints and validation against a `CityPackDocument`. It imports no CLI, FastAPI,
storage or provider adapter. `src/adlife/city/spatial_loader.py` owns bounded local file
loading. A Typer command selects a local or verified packaged city and reports validation
evidence; it has no network path and writes no run artifact.

The existing abstract zone `Campaign` remains unchanged. Spatial campaigns are a separate
contract so a geographic placement cannot be smuggled into the old route/zone semantics,
and existing campaign golden artifacts remain byte-compatible.

## Scenario v1

`SpatialCampaignScenario` contains:

- exact integer `schema_version: 1`;
- a portable `scenario_id`, bounded public `name`, and `days` from 1 through 7;
- `city_id` and canonical `city_sha256`, binding the scenario to one exact pack;
- 1â€“20 `SpatialCampaign` records, canonicalized by campaign ID;
- 1â€“500 discriminated placement records, canonicalized by placement ID.

Each campaign has a unique stable ID, bounded public name and lowercase SHA-256 digest of
the exact creative bytes the operator intends to represent. Creative content is not
embedded, fetched or interpreted in C1.

Every placement has a unique ID, references one declared campaign, has 1â€“14 absolute
active windows within the scenario duration, and declares a per-agent/per-day frequency
cap. Windows use half-open absolute model minutes `[start_minute, end_minute)`, must be
strictly forward, are canonicalized, and cannot overlap. Each campaign must own at least
one placement.

Public names reuse the established metadata screen: blank/control/bidirectional spoofing,
credential-shaped and email-like private identifiers are refused. Models are frozen,
strict, finite and `extra="forbid"`. The parser rejects duplicate JSON keys, non-object
roots, non-finite constants, coerced/unknown versions and unsupported fields.

## Billboard placement v1

A billboard record declares:

- `channel: "roadside-billboard"`;
- stable `road_id` and a `travel_direction` (`forward` or `backward`);
- `road_fraction`, strictly between 0 and 1, measured along physical source-to-target
  geometry regardless of viewing direction;
- explicit WGS84 `longitude` and `latitude`;
- `side` (`left` or `right`) relative to the declared travel direction;
- absolute clockwise `orientation_degrees` in `[0, 360)`;
- bounded `max_view_distance_meters`, an input for C2's approximate visibility model.

Validation resolves the road by stable ID, verifies the direction is traversable under
the pack's v1/v2 direction contract, checks the coordinate against city bounds, computes
the physical point at `road_fraction` along all intermediate geometry, and measures the
declared-to-computed error. At most one meter of serialization/authoring tolerance is
accepted. The declared coordinate is never mutated or snapped. Larger error is an
off-network placement and is refused.

Endpoints are excluded because one coordinate may belong to several roads and C1 has no
junction visibility semantics. Two billboard records cannot occupy the same
road/direction/fraction/side key. Orientation is preserved but not marketed as a verified
line-of-sight result; C2 must define how it contributes to approximate opportunity.

## Phone placement v1

A phone record declares:

- `channel: "mobile-feed"`;
- `opportunity_model: "keyed-activity-minute-v1"`;
- a nonempty canonical set of eligible synthetic activities from `home`, `work`,
  `leisure`, and `commute`;
- a finite `opportunity_probability_per_minute` in `[0, 1]`.

This is an explicit synthetic opportunity input, not an observed device-use rate. C2 will
define its keyed draw namespace, cap ordering and event semantics. C1 only validates and
fingerprints it. Duplicate phone policies for the same campaign are refused so repeated
records cannot silently multiply opportunity.

## Binding and geometry evidence

Validation returns immutable evidence containing the scenario fingerprint, city hash,
campaign/placement counts, channel counts and maximum billboard binding error. Geometry
is calculated in core code from the frozen pack. Input order cannot affect the scenario
fingerprint or evidence.

Failure is explicit for:

- city ID or hash mismatch;
- unknown campaign or campaign without placement;
- unknown road;
- backward placement on a forward-only road (or the inverse);
- coordinate outside city bounds;
- coordinate more than one meter from the declared road fraction;
- endpoint, duplicate physical placement, duplicate phone policy, overlapping window,
  invalid cap/hash/ID, non-finite number or screened metadata.

## CLI contract

`adlife city-campaign validate SCENARIO [PACK | --city-id ID]` requires exactly one city
selector, loads bounded local JSON, validates the complete binding, and emits concise
human or exact machine output. Catalog selection is offline and content-addressed. The
command never fetches a URL, reads creative bytes, starts a simulation, modifies a city
run, or writes outside the caller's normal terminal output.

## Security and scientific boundaries

- Scenario input is capped at 2 MiB and is decoded as UTF-8 JSON only.
- Errors never echo input bodies, labels, URLs or credential-like values.
- No HTTP endpoint accepts a path and no network call is introduced.
- Coordinates and road geometry describe synthetic placement inputs, not measured ad
  inventory, legal placement rights, traffic, viewability or attention.
- Creative hashes prove content identity only; they do not prove ownership, safety or
  licensing.
- Phone opportunity probabilities are analyst assumptions until calibrated evidence is
  separately reviewed; no real device or individual is represented.

## Acceptance evidence

- Contract tests cover exact versions/types, canonical order/hash, references, windows,
  caps, public text, injection, duplicates and unknown fields.
- Binding tests cover v1/v2 geometry, intermediate shapes, out-of-bounds, off-network,
  one-way directions, road endpoints and immutable evidence.
- Property tests permute campaigns, placements, windows and activities without changing
  the canonical fingerprint or validation evidence.
- Loader/CLI tests cover size, UTF-8, malformed input, selector conflicts, offline catalog
  use, clean JSON stdout and redacted failure diagnostics.
- Architecture, Ruff, mypy, full pytest, hash-seed, coverage and wheel gates remain green.
