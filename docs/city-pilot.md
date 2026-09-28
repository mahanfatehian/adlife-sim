# City mobility pilot

Run `uv run adlife city` and open `http://127.0.0.1:8765` in a browser. The bundled
grid is fictional and works offline. The viewer is read-only, loopback-only, and uses
packaged HTML, CSS, and JavaScript. It shows synthetic agents on roads, activity at
each minute, a selected agent's planned directed route, an all-day timeline,
day/night presentation, and visible attribution.

To use a **local, properly licensed** street extract, import a complete local
OpenStreetMap Overpass JSON document and run:

```bash
uv run adlife city-import streets.json --output city.json --city-id my-city --name "My City"
uv run adlife city --pack city.json --agents 30 --days 7 --seed 42
```

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
10,000-node and 20,000-road limits. Input is additionally capped at 50,000 node
elements. The importer preserves shared OSM vertices,
way shape and supported one-way directions; it includes motor-vehicle road classes
from motorway through service and corresponding link roads. Non-drivable classes and
roads restricted to non-car traffic are excluded. The most specific OSM access tag
(`motorcar`, then `motor_vehicle`, `vehicle`, `access`) controls inclusion; only `yes`,
`designated` and `permissive` are accepted. Vehicle-specific one-way tags override
generic direction. Unsupported conditional, directional-access, or reversible rules
are refused, not guessed. An Overpass response with a `remark`
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

OpenStreetMap data is available under the [ODbL](https://www.openstreetmap.org/copyright).
The importer preserves its license and displays `© OpenStreetMap contributors` as
attribution, with `source_url` linking to the copyright page. Do not copy Google Maps
or unlicensed map content. There is no automatic city search or downloader; data
acquisition and license compliance remain the operator's responsibility. No public
map-tile service is used.

Weekdays place fictional agents at home until 08:00, at work after road travel, and
return them at 17:00. Weekends replace work with a leisure visit from 11:00 to 16:00.
Day 1 is treated as Monday. The UI's light/dark styling switches at fixed 06:00 and
19:00 clock times; it is not a calculation of local sunrise, sunset, or time zone.
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
is not asserted. It does **not** persist an
event-sourced city run or use the campaign replay command.

This is a first product-track slice. Authentication, provider/OAuth settings,
geographic ad placements, campaign decisions, saved/replayable city runs, traffic
data and population calibration are not implemented. Adding MBTI labels without
evidence would not make behavior realistic and is deliberately deferred.
