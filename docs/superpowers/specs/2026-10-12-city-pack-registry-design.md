# Workspace City-Pack Registry Design

**Status:** Proposed; written-spec review required before implementation planning
**Date:** 2026-10-12
**Scope:** Safe, offline upload and selection of operator-supplied CityPack V2 files in the local workbench

## Intent

The city workbench currently exposes only the packaged fictional grid even though the CLI can produce validated CityPack files from offline source data. This design lets an operator choose a local CityPack V2 JSON file in the browser, register it inside the pinned workbench workspace, inspect its provenance and geometry, and use it in the existing validate → run → inspect → replay flow.

Registration proves only that the document satisfies AdLife's technical CityPack contract. It does not prove ownership, redistribution rights, routing accuracy, source authenticity, population representativeness, or fitness for commercial decisions.

## Current constraints

- CityPack V1 and V2 are strict, frozen domain models; the browser workbench requires V2.
- The domain limit is 4,194,304 input bytes, 10,000 nodes, 20,000 roads, 64 intermediate coordinates per road, and 16 omissions.
- V2 validates enclosing bounds, installed IANA time zone, provenance, canonical IDs, road directions, and strong graph connectivity.
- Pack identity is SHA-256 over canonical JSON bytes without a trailing newline. Saved `inputs/city.json` is the same canonical JSON plus one newline.
- The packaged catalog has a separate, human-reviewed qualification contract. Its `fictional-fixture` and `rights-reviewed` labels must not be granted to uploaded data.
- Workbench API writes are loopback-only, same-origin, CSRF-protected `POST application/json` requests. Their ordinary body ceiling is 131,072 bytes.
- Existing workbench runs already freeze the selected pack and replay without consulting a catalog.

## Chosen approach

Add a workspace-scoped immutable registry and one resolver shared by catalog listing, detail, validation, and job submission. The browser reads a selected file and sends its bytes directly as `application/json`; it never sends a client path or filename. The registry stores only canonical validated CityPack V2 content in one exclusive file per city ID.

This approach is preferred over a mutable global catalog because workspaces remain isolated and portable. It is preferred over a separate index plus object store because a single immutable record has one publication boundary and cannot leave index/content divergence. It is preferred over server-side URL import because the default workbench stays offline and avoids SSRF, downloader, and licensing ambiguity.

## Registry layout and limits

The registry is rooted at:

```text
<pinned-workbench-root>/city-packs/
```

Each accepted pack is stored as:

```text
city-packs/<city_id>.json
```

The file contains canonical CityPack JSON plus one trailing newline. No mutable index is stored. Temporary files live in a separate `<pinned-workbench-root>/city-pack-staging/` directory and can never be mistaken for registrations.

Every registry operation takes one in-process mutex and one cooperating cross-process advisory lock rooted directly under the pinned workspace. Under that lock, discovery enumerates direct `city-packs` entries only until either the directory ends or a 51st entry is observed. A 51st entry is an overflow refusal. With 50 or fewer entries, every entry must be a regular, non-linked canonical `.json` file; unknown names or types make the registry corrupt. Entries are then sorted by filename, fully validated, checked for canonical bytes, and required to have `filename stem == parsed city_id`. Nothing is skipped or silently normalized.

The following limits are fixed:

- raw upload: at most 4,194,304 bytes;
- complete canonical stored file, including its final newline: at most 4,194,304 bytes;
- local registrations: at most 50;
- existing ordinary scenario/job bodies: unchanged at 131,072 bytes;
- packaged catalog: unchanged at its current 50-entry and 262,144-byte index limits.

Raw and canonical size are checked independently because canonical escaping can change byte length.

## Registration contract

`POST /api/catalog/cities/upload` accepts exactly one CityPack V2 JSON object as `application/json` and no query parameters.

The server:

1. applies the normal loopback Host, same-origin, CSRF, method, and media-type checks;
2. streams and counts at most 4,194,304 bytes without raising the limit for any other route;
3. decodes strict UTF-8 and rejects BOM/UTF-16/UTF-32 ambiguity;
4. rejects duplicate members, non-finite values including exponent overflow, nesting deeper than the repository's shared `MAX_JSON_DEPTH`, malformed JSON, and non-object roots;
5. parses and validates exact CityPack schema version 2;
6. computes the existing canonical pack fingerprint;
7. derives the destination only from the validated `city_id`;
8. rejects collisions against both packaged and workspace city IDs;
9. writes, flushes, and `fsync`s a temporary file in the separate verified staging directory, then reopens and validates it;
10. under the registry locks, rescans capacity/collisions and publishes the already-fsynced, preverified staging file as an exclusive final link; creation of that final link is the commit point, after which it reopens/revalidates and syncs the registry directory where supported before reporting success.

An identical second upload is still a `409` conflict. Registration never overwrites, updates, shadows, or silently repairs an existing record.

Successful registration returns `201` and the same path-free public row used by catalog listing. It does not return the uploaded filename, server path, temporary name, or filesystem details.

Failure mapping is:

| Status | Meaning |
|---|---|
| `400` | malformed/ambiguous JSON request or request metadata |
| `403` | same-origin or CSRF refusal |
| `409` | packaged/local ID collision, capacity reached, unsafe workspace, or unsafe existing registry state |
| `413` | raw or canonical byte limit exceeded |
| `422` | well-formed document that is not a valid CityPack V2 |
| `500` | unexpected implementation or durable-publication failure, with a bounded generic diagnostic |

Validation errors never echo submitted source strings or document fragments.

## Filesystem safety and atomicity

The existing pinned `WorkbenchWorkspace` guarantees are extended to the registry directory, staging directory, lock file, every ancestor, temporary file, and final record:

- reject symlinks, dangling links, Windows reparse points/junctions, non-directories in the directory chain, and nonregular record files;
- recheck the pinned workspace device/inode when available before reading or publishing;
- reject root replacement and path resolution changes;
- reject separators, traversal, percent-encoded traversal, dot names, absolute/drive/UNC forms, and Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) when deriving filenames;
- create staging only inside the verified staging directory, `fsync` and revalidate complete content before publication, publish exclusively without `replace`, and remove only the exact staging file before the final-link commit point;
- never repair or delete an unexpected existing destination.

A final name is not selectable by a cooperating reader until the registry lock is released. Because the complete staging file is flushed, fsynced, canonically validated, and identity-checked before the exclusive final link is created, a successful link is the cross-process commit point and is never rolled back. A crash, directory-sync error, post-commit verification error, or response-delivery failure after that point may produce an ambiguous HTTP outcome, but any surviving final file is treated by every process as a committed candidate and must pass the normal full read verification before selection. Listing is the authoritative recovery check, and retry never overwrites it. Failure before the exclusive link removes only the operation's staging file and cannot create a final registration.

A failed short write, pre-commit verification, publication, or concurrent duplicate must expose no partial record and must preserve all pre-existing bytes. Post-commit failures may leave the complete committed record and are reported as potentially committed, never as rolled back. Capacity checking and publication occur under the same cross-process lock, including for two different city IDs.

## Catalog resolver

Introduce a workspace-scoped resolver injected into:

- `GET /api/catalog/cities`;
- `GET /api/catalog/cities/{city_id}`;
- scenario validation;
- job submission and construction.

The resolver combines verified packaged rows and verified workspace registrations while keeping their trust classifications distinct. Packaged lookup behavior and CLI catalog behavior remain unchanged.

The browser DTO uses explicit fields:

| Field | Packaged entry | Workspace entry |
|---|---|---|
| `storage_origin` | `package` | `workspace-upload` |
| `qualification` | existing `fictional-fixture` or `rights-reviewed` | `operator-supplied-unreviewed` |
| `data_origin` | existing catalog value | `operator-supplied` |
| existing hash field | `pack_sha256` in rows and `city_sha256` in details | `pack_sha256` in rows and `city_sha256` in details |

`operator-supplied-unreviewed` is a separate workbench transport status, not a new `CityCatalogEntry` qualification. The packaged rights policy remains closed.

One invalid workspace record refuses local catalog listing, detail, upload, validation, and new jobs with a clean typed error; it is never skipped in a way that could make a conflicting or corrupted ID appear absent. Registry health is evaluated lazily so corruption does not prevent application startup, saved-run listing, saved-run inspection, or replay. One invalid packaged entry retains the packaged catalog's current refusal behavior.

Validation and job submission receive the exact already resolved pack value. A queued job must not look up the city again after acceptance, so a later registry change cannot alter its inputs.

## User interface

The CITY step gains:

- an “Add city pack” file chooser accepting JSON;
- a concise upload state and adjacent, actionable validation errors;
- a catalog distinction between bundled fixture and operator-supplied pack;
- an always-visible `Operator supplied · rights not reviewed` label for local entries;
- the pack hash, declared provider/dataset/version/date, license, attribution, bounds, time zone, and omissions;
- the existing road-network placement canvas using validated pack geometry.

The browser uses the File API only to read bytes for the upload request. It does not retain the file in IndexedDB/local storage, expose the local path, interpret source URLs, fetch tiles, geocode, or make any network request outside the loopback origin.

Selecting or uploading a different city invalidates map placements and any prior validation receipt exactly as other causal draft changes do. A failed upload leaves the previously selected valid city and current draft untouched.

The current generic “Verified” label is removed for uploaded entries. Technical validation and rights review are stated separately.

## Frozen runs and replay

No run schema change is required for this slice:

- the existing workbench input freezes the selected city ID;
- scenario and manifest fields bind the exact canonical pack hash;
- `inputs/city.json` freezes the full validated pack, including declared source, license, attribution, bounds, and omissions;
- loading independently checks all existing cross-bindings;
- replay uses frozen bytes and never consults packaged or workspace catalogs.

Registry qualification is an operational catalog status, not scientific evidence. A saved inspector presents the frozen source/license as operator-declared metadata and does not reconstruct a historical rights-review status from the current registry.

Removing, changing, corrupting, or losing the registry after a run must not alter replay, metrics, report data, or source artifacts. Corruption of the frozen run remains a hard artifact-integrity refusal.

## Licensing and scientific honesty

- A technically valid pack does not establish permission to use or redistribute its data.
- `license` and `attribution` are operator-declared source metadata, not an AdLife endorsement.
- Uploading ODbL-tagged data does not make it `rights-reviewed`.
- The existing documented human qualification process remains the only way to add a rights-reviewed packaged dataset.
- A source URL is inert metadata. The server never opens it during upload, selection, execution, inspection, or replay.
- A road graph provides synthetic mobility opportunities; it does not prove traffic realism, resident behavior, or commercial forecast validity.

## Compatibility

- CityPack V1 remains supported by current CLI paths but is refused by the browser registry.
- Pack hash and canonicalization semantics do not change; no golden files are regenerated.
- Existing catalog hash field names, CLI import, fictional default, CityRun V1–V7 readers, deterministic execution, and replay remain intact. Workbench capabilities and CLI help are updated to say that the browser accepts technically validated local V2 packs while bundled geometry remains the default.
- Workbench list/detail browser validation intentionally accepts both the existing packaged classifications and the new exact local classification; arbitrary strings remain refused.
- The package wheel still works with only bundled resources and no pre-existing registry.

## Failure behavior

- Upload failure never starts a job or reserves a run ID.
- Unsafe workspace/registry state is a clean refusal; the app does not write through it.
- A record is selectable only after durable exclusive publication and independent revalidation.
- A catalog read never repairs, deletes, or hides a corrupt record.
- Existing runs remain inspectable and replayable when the registry is unavailable.
- Default and test execution perform zero outbound network I/O; browser/API traffic remains same-origin loopback.

## Required tests

### Domain and registry

- Valid bounded V2 upload, strict UTF-8, canonical hash, durable restart, and workspace isolation.
- Same semantic JSON with different formatting produces the same canonical hash.
- Exact raw/canonical limits, limit plus one, malformed/deep JSON, duplicate keys, NaN/Infinity/overflow, invalid Unicode/BOM, unknown fields, boolean/float/string version, V1 refusal, and every V2 graph/provenance invariant.
- Packaged/local ID collision, duplicate local upload, 50-entry capacity, and concurrent duplicate publication preserve original bytes.
- Traversal, separators, absolute/drive/UNC paths, dot names, reserved Windows names, symlinks, dangling links, junctions/reparse points, root replacement, nonregular files, and corrupt existing records.
- Short write, flush, pre-commit verification, publication, directory-sync, post-commit verification, response failure, and process-interruption fault injection proves that another reader observes either no final record before the commit point or one complete normally verified record after it—never a partial record.

### API and browser

- `201` path-free row, then `GET` detail → select → place campaign → validate → run → inspect.
- Route-specific 4 MiB streaming cap while scenario/job routes remain at 128 KiB.
- Omitted `Content-Length` accepted with streamed bounds; malformed, duplicate, oversized, or body-mismatched declared lengths refused, along with streamed overflow and method/media/query/Host/Origin/CSRF violations.
- Fake credentials and sensitive identifiers in invalid uploads are absent from HTTP errors, rendered DOM, logs, request-body diagnostics, temporary names, and artifacts; raw upload bodies are never logged.
- Uploaded filename and server paths never appear in responses, DOM, logs, errors, artifacts, or temp names.
- Hostile/Persian attribution and omissions render as text with bidi safety and no external execution.
- UI uses truthful technical/rights labels and remains keyboard-usable at 390-pixel width.
- Network-blocked tests cover upload, listing, selection, execution, inspection, and replay.

### Artifacts and replay

- A registered pack completes the existing V7 workbench workflow.
- Replay remains identical after registry removal, replacement attempt, or corruption.
- Hash every source-run artifact before and after replay and prove no mutation.
- Frozen pack corruption/misbinding refuses and is not repaired from the registry.

### Repository gates

- Existing city/catalog/import/workbench/security/browser/run/replay suites.
- Core architecture import boundary, package resource smoke, Ruff format/lint, strict mypy, full pytest under multiple hash seeds, branch coverage, wheel smoke, and offline demo gates.

## Explicit non-goals

- Browser-side OSM/Overpass conversion.
- Server-side URL import, basemap tiles, geocoding, or live traffic.
- Deletion, replacement, or in-place updates of registrations.
- Rights approval through the UI.
- Remote/shared workbench access.
- CityPack V1 registration.
- Any claim that uploaded city data makes simulated people real residents.

## Implementation order after approval

1. Add immutable registry storage, resolver, and red/green filesystem/domain tests.
2. Add route-specific bounded upload security and API tests without changing other request caps.
3. Inject the resolver into list, detail, validation, and accepted job construction.
4. Add the city upload/qualification UI and browser tests.
5. Verify frozen replay independence, packaging, offline behavior, and full repository gates.
