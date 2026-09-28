# Offline OSM city-pack import

## Scope

Add a local, deterministic converter from an operator-supplied Overpass JSON extract
to the existing version-1 city-pack format. This makes the city mobility viewer usable
with licensed real street geometry without adding network access, map tiles, accounts,
or geographic advertising behavior. The output remains an illustrative mobility model,
not a traffic model or a forecast of real people.

## Input and mapping

`adlife city-import INPUT --output PACK --city-id ID --name NAME` reads at most 16 MiB
of UTF-8 Overpass JSON. The document must have an `elements` array containing complete
`node` coordinates and `way` member IDs (an Overpass `out body; >; out skel;`-style
extract). Node and way IDs must be positive JSON integers, not booleans; duplicate IDs,
missing referenced nodes, non-finite/out-of-range coordinates, malformed tags and
unsupported one-way values are refused with safe diagnostics that never echo input.
An Overpass `remark` is treated as a failed or partial response and refused even when
it also contains usable-looking elements.
Other element types are ignored. Roads are successive pairs of a way's node sequence,
with IDs derived from the way ID and segment index. Input element order does not affect
the output bytes. Only motor-vehicle road classes (`motorway`, `trunk`, `primary`,
`secondary`, `tertiary`, `residential`, `service`, and their `_link` forms) are imported;
non-drivable classes and ways not permitted for cars are excluded.
`oneway=-1` reverses the segment endpoints. Explicit `oneway=no` overrides motorway
and roundabout implied one-way; reversible/conditional/unknown one-way directions are
refused rather than guessed. Car access is interpreted from the most specific present
tag (`motorcar`, `motor_vehicle`, `vehicle`, then `access`); only `yes`, `designated`
and `permissive` are included. Vehicle-specific `oneway:*` tags override generic
`oneway`. Directional/conditional car access not represented by this model is refused.
Every way's node references must be complete even if its highway class is excluded.
This is not turn-restriction-aware or legal navigation.
At most 50,000 input node elements are accepted before any city-pack selection.

## Graph and output

The existing CityPack validates a strongly connected directed graph, 10,000 nodes,
20,000 roads, and 4 MiB serialized size. Default import refuses disconnected results.
The converter refuses more than 20,000 selected input segments before creating road
objects, including with `--largest-component`; it never relies on component selection
to make an oversized extract fit.
`--largest-component` explicitly retains the largest strongly connected component,
breaking equal-size ties by its lexicographically smallest node ID, and reports exactly
how many nodes and roads were dropped. Nodes unused by retained roads are excluded.
No implicit truncation is permitted. Output is canonical UTF-8 JSON with newline,
created atomically and without replacing an existing file; a failed conversion leaves
no partial pack. JSON output mode emits one result document and no prose on stdout.
The source file is never altered.

The converter fixes `source_url=https://www.openstreetmap.org/copyright`,
`license=ODbL-1.0`, and `attribution=© OpenStreetMap contributors`; it copies no OSM
tags, contributor names, contact details, or source file path into the pack. The user
supplies only display city name and ID, both validated by the existing CityPack model.
The pack SHA-256 is reported so the exact converted geometry can be identified.
This is the canonical CityPack fingerprint, excluding the published file's final newline.

## Limits

This does not provide worldwide city selection, download data, persist a city run,
join the ad simulation, add accounts or OAuth, or calibrate population/traffic.
Those are separate product milestones and must not be implied by the CLI or docs.
