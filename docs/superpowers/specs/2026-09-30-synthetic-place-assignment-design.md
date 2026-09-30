# Synthetic Place Assignment: Focused Design

> Status: approved production-roadmap slice for implementation on `main`. This
> design implements B6 of the production city platform roadmap. It does not alter
> `defense-ready`, add real-person data, or claim calibrated mobility.

## Outcome

An analyst can supply a bounded, versioned set of synthetic home, workplace and
leisure points for a specific immutable city pack. AdLife validates the set,
assigns synthetic agents deterministically, saves the exact place input and
assignments in a replayable city-run artifact, and shows the places in the local
read-only viewer.

This milestone supports **node-bound points**, not polygons, addresses, live
geocoding, inferred real homes, or imported personal trajectories. A later schema
version may add zones after its spatial and privacy contract is designed.

## Separation of concerns

The city pack describes the road graph and its public data provenance. A place set
describes analyst-selected scenario inputs bound to the exact city ID and city-pack
hash. It is intentionally a separate artifact: an operator-authored fictional home
must not be presented as sourced road data, and changing a scenario must not create
a misleading new city pack.

`src/adlife/core` owns the immutable contracts, keyed assignment and routing
invariants. `src/adlife/city` owns bounded file loading and saved-run persistence.
Typer and FastAPI remain adapters over those contracts.

## Place-set v1 contract

`CityPlaceSet` is canonical JSON with:

- `schema_version: 1`;
- `city_id` and `city_sha256`, binding it to one immutable pack;
- a bounded public `name`;
- 3–10,000 `places`, sorted canonically by kind then place ID;
- at least one `home`, one `workplace`, and one `leisure` point.

Each `CityPlace` contains a stable portable `place_id`, one role, a city `node_id`,
a bounded public display label, and explicit provenance:

- `operator-authored-fictional` — intentionally invented by the analyst; no source
  reference is allowed;
- `source-derived` — directly derived from a legally usable source; a public source
  reference is required;
- `inferred` — inferred from a documented source or method; a public method/source
  reference is required.

Place IDs are globally unique. A role cannot contain two place IDs bound to the
same node because that would silently weight that node twice. Labels and references
reuse the existing public-metadata controls: no display controls, credentials, or
private email-like identifiers. Unknown fields, duplicate JSON keys, non-finite
numbers, invalid UTF-8, oversized input and unsupported versions are refused.

## Assignment and routability

`CityMobility` accepts an optional validated place set. Without one, all v1/v2
behavior and trace digests remain unchanged. With one:

1. the city ID and canonical fingerprint must match the loaded pack;
2. every node reference must exist;
3. there must be at least one distinct home place per agent;
4. for each agent, at least one workplace and leisure candidate must be on a node
   different from that agent's home;
5. home selection is without replacement; workplace and leisure places may be
   shared;
6. selections use the existing keyed `RandomOracle`, canonical candidate order and
   stable agent IDs, so JSON input order cannot affect assignments;
7. all home/work/home and home/leisure/home paths are computed by the core directed
   router. Any unavailable path or trip that cannot finish in the model day refuses
   the simulation before a frame is exposed.

The existing `CityAgent` node assignment remains unchanged for compatibility. A new
immutable `CityPlaceAssignment` records the three selected place IDs and node IDs.
The model exposes these records separately; it never treats place labels as routing
instructions.

## Persistence and replay

A place-aware saved mobility run uses city-run manifest schema v3 and model ID
`illustrative-road-mobility-v3`. It freezes:

- canonical `inputs/city.json` and its existing digest;
- canonical `inputs/places.json` and `place_set_sha256`;
- canonical `inputs/agents.json` and the existing agent digest;
- canonical `inputs/place-assignments.json` and
  `place_assignments_sha256`;
- seed, duration, population, model/package/runtime identities and trace evidence.

The completion manifest remains the final publication step. Loading reconstructs
the model from the frozen city and place set, then byte-compares both assignment
documents and all digests. Missing, changed, oversized, non-canonical or mismatched
place inputs are corruption. City-run v1/v2 artifacts remain readable and are not
rewritten.

## CLI contract

- `adlife city-places validate PLACE_SET [PACK | --city-id ID] --agents N` validates
  binding, capacity, assignments and routability without writing artifacts.
- `adlife city ... --places PLACE_SET` opens an ephemeral place-aware viewer.
- `adlife city-run ... --places PLACE_SET` writes a v3 run.
- `city-replay` and `city-view` discover v3 from the saved manifest without new
  flags.

All expected failures use existing concise CLI boundaries; JSON modes keep stdout
machine-readable. No command downloads map or place data.

## Viewer contract

The existing visual language remains: dark urban operations console, muted road
geometry, amber selection and mint activity. Places add a compact, distinctive
three-shape map layer and selected-agent itinerary labels; this is an extension,
not a redesign.

`/api/places` returns the canonical place-set document only when configured, and
`/api/place-assignments` returns core-generated assignments. With no place set both
return an explicit 404. The viewer draws home, workplace and leisure markers from
city nodes and shows provenance text. It uses text nodes, not HTML injection, makes
no external requests, and cannot mutate the run.

## Security and scientific boundaries

- No arbitrary filesystem path is accepted by HTTP.
- Place files are local CLI inputs and are never fetched by URL.
- Public metadata cannot contain credentials, email-like private identifiers, or
  display-control spoofing.
- Operator-authored inputs are visibly fictional; source-derived and inferred inputs
  carry their evidence reference.
- The feature does not make simulated agents real residents, produce calibrated
  commuting, or predict advertising/sales outcomes.
- Model time and the current fixed routine remain illustrative. Calendar start,
  daylight-saving behavior, congestion and variable schedules remain future work.

## Acceptance evidence

- Contract/property tests cover strict parsing, binding, capacity, provenance,
  duplicate weighting and input-order invariance.
- Mobility tests prove selected place IDs correspond to routed nodes and legacy
  traces remain byte-compatible.
- Storage fault/corruption tests cover both new frozen documents and no-clobber
  publication.
- Replay proves identical evidence and source immutability.
- CLI tests cover exact human/JSON behavior and input refusal.
- FastAPI tests and a real browser smoke prove the place layer, labels, provenance,
  timeline scrub and saved-run identity without network access.
- Ruff, strict mypy, full pytest, hash-seed suites, branch coverage, wheel build and
  clean-room wheel smoke remain release gates.
