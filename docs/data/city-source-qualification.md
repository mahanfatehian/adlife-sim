# City-source qualification

This is the release checklist for adding a **real-world** city pack to the packaged
catalog. It is a governance record template, not legal advice and not evidence that a
city has already been approved.

**Current status:** real-city qualification remains pending. The shipped catalog
contains only `fictional-grid-v2`, whose origin is `fictional` and whose qualification
is `fictional-fixture`. It is not a substitute for a reviewed real-city pack.

Passing city-pack validation proves only that a bounded graph is structurally usable.
It does not prove ownership, license compliance, redistribution rights, routing
safety, representative coverage, or fitness for commercial use. A catalog
`rights-reviewed` value records a maintainer decision; the validator cannot discover
whether that decision was legally correct.

## Required decision record

Create one record per immutable pack hash. Keep confidential contracts outside the
repository and cite a non-secret evidence reference. Do not put credentials, personal
contact details, private URLs, or confidential terms into a pack or catalog entry.

### Pack and source identity

- City ID and display name:
- Data origin (`real-world`; fictional fixtures use the separate fixture path):
- Pack schema version and resource name:
- Pack SHA-256:
- Coverage bounds and stated coverage limits:
- IANA time zone:
- Supplier and acquisition method/date:
- Dataset/provider and source URL:
- Source version and source date:
- Source SHA-256 or immutable supplier reference:
- Declared known omissions:

### Rights and data handling

- Applicable jurisdiction(s):
- Rights holder or authorized supplier:
- License name, version, and authoritative license text:
- Required attribution and notice placement:
- Database/derived-database or share-alike obligations:
- Permission for modification and generation of the normalized city pack:
- Permission for internal use:
- Permission for commercial use:
- Permission for commercial redistribution in source archives, wheels, frozen
  applications, containers, backups, and customer deliveries:
- Retention, deletion, update, and withdrawal obligations:
- Personal-data/privacy assessment (city packs must contain no personal trajectories):
- Geographic or customer restrictions:
- Separate basemap/public tile rights and operating policy:
- Separate geocoder rights, retention rules, and operating policy:

The OpenStreetMap database is offered under the ODbL and requires attribution. The
exact obligations for an extracted or transformed database depend on how it is used
and distributed. ODbL/commercial redistribution therefore needs recorded rights
review before an OSM-derived pack may be marked `rights-reviewed` or bundled. Importer
output that says `ODbL-1.0` has not completed that review.

Public tile services and geocoders are separate services with separate policies; an
underlying data license does not grant unlimited API use. AdLife's offline importer,
catalog, simulator, replay, and packaged viewer do not contact a public tile server or
geocoder. A future basemap or search integration requires its own supplier and security
review.

### Human review

- Reviewer identity reference (non-secret) and reviewer role:
- Review date:
- Evidence reviewed:
- Decision (`approved`, `rejected`, or `time-limited approval`):
- Approved products, territories, distribution forms, and uses:
- Conditions or exceptions:
- Expiry date, if any:
- Re-review owner and re-review date:
- Re-review triggers (license/source/supplier change, new distribution channel,
  withdrawal notice, material pack transformation, or policy change):

The catalog's public `reviewer_role` and `reviewed_on` fields are a small audit marker,
not the complete decision record. For a real-world entry they must be present, the
review date cannot predate the source date or follow the catalog's immutable
`issued_on` date, and the entry must be classified as both `real-world` and
`rights-reviewed`. Validation never consults the host clock, so identical catalog bytes
have identical qualification results.

### Technical qualification

- Local source input was acquired outside AdLife; no downloader was added:
- Importer and city-pack size/resource limits pass:
- Unsupported access, conditional, reversible, barrier, and turn semantics are either
  refused or listed as omissions; they are never silently treated as legal routes:
- Bounds, road geometry, traversal direction, source provenance, and omissions were
  inspected:
- Strong directed connectivity and deterministic import tests pass:
- Catalog metadata matches the parsed pack (ID, name, bounds, time zone, source,
  omissions, schema, and SHA-256):
- Offline catalog selection works with network access blocked:
- Installed-wheel resource and clean-room smoke tests pass:
- Representative performance and memory measurements are attached:

## Approval and publication gate

Two independent results are required: a completed human rights decision and passing
technical qualification. Neither result implies the other. Add the exact immutable
pack and catalog entry only after both are complete, review the public attribution,
and rerun the full release gates. A new source version or any changed pack SHA-256 is a
new qualification decision, not a silent catalog update.

An IANA time zone is provenance for local clock interpretation. It does not make the
current fixed weekday/weekend schedule calendar-accurate: the model has no start date,
holiday calendar, daylight-saving transition policy, local sunrise/sunset calculation,
measured commuting demand, or traffic calibration.
