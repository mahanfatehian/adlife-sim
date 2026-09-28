# City mobility pilot: first production-track slice

## Decision and scope

`main` gains an opt-in, local-first city mobility pilot. The historical zone-based
advertising simulation and the frozen `defense-ready` branch remain unchanged. A
city pack is a versioned, bounded road graph with explicit source and attribution.
Its nodes carry WGS84 coordinates; roads carry directed connectivity, category,
and length derived from coordinates. The core selects deterministic home and work
nodes, computes shortest road paths, and emits complete one-minute day traces. A
FastAPI adapter serves a vanilla HTML/CSS/JavaScript timeline and map. It binds to
loopback by default and does not require an external tile or API service.

This is a mobility pilot, not a claim that synthetic people represent residents,
that road paths reproduce measured traffic, or that advertising effects have been
geographically calibrated. The UI must say so. Its trace must come from the same
pure core function used by tests; drawing alone must not invent simulation state.

## Data and replay contract

City packs are local JSON with `schema_version: 1`, city identity, source URL,
license, visible attribution, nodes and roads. All identifiers and geometry are
validated. No implicit OSM download occurs. A pack may be made from properly
licensed OSM extracts; the included small fixture is explicitly fictional and is
not presented as a real city. Input ordering must not change routes or traces.
The pack's canonical SHA-256 is exposed by the API, alongside seed and parameters.
An identical pack, seed and parameters produce byte-identical trace JSON under the
same implementation and compatible Python/runtime math. Cross-platform bitwise
identity is not claimed for floating-point interpolation.

The server accepts a pack path at startup, loads it once, and offers read-only
endpoints. It never accepts arbitrary file paths or provider secrets over HTTP.
City data is not integrated with the existing advertising run store or replay CLI
in this slice; the UI must not present the mobility trace as a persisted AdLife
campaign run. Existing replay guarantees remain unchanged.

## Mobility model

Profiles are fictional and generated from keyed random streams. Each agent gets
distinct home/work nodes in the connected graph. Weekdays: home until 08:00,
commute to work by the shortest directed road path, work until 17:00, return home;
weekends: home, a deterministic leisure trip, then return. Movement interpolates
along real road segments at fixed, disclosed road-kind speeds; it never jumps straight
between geographical coordinates. An itinerary that cannot return before midnight
is rejected. Date-line crossings are out of scope for the flat pilot map. Road ties
resolve by canonical road/node ID.
No MBTI label influences decisions in this slice; MBTI-based outcome claims
require evidence and calibration before implementation.

## UI and security

The dashboard includes city attribution, streets, current positions, day/night
timeline, agent list and selected-agent path/activity. It has accessible text
labels and never relies on color alone. It works offline as installed package
resources; no CDN, external scripts or fonts. User-controlled strings are placed
with textContent, not HTML injection. The server uses a fixed static directory
from installed resources and read-only routes. Host defaults to `127.0.0.1`.

Provider configuration, OAuth login, arbitrary geographic campaigns, measured
traffic, large-scale city coverage, and multi-user deployment are future phases.
They require an explicit authentication/secrets boundary and cannot be honestly
implied by this pilot. The product should grow through validated city ingestion,
geographic advertising semantics, then secure authenticated configuration.
