# Spatial City Run Artifacts Design

**Status:** Approved roadmap refinement for C3a

**Parent roadmap:** `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

C2 produces deterministic synthetic opportunity evidence but deliberately performs no
I/O. C3a makes that evidence a frozen, tamper-evident part of an offline city run so an
operator can create, reload and replay a geographic opportunity study. It does not yet
turn an opportunity into an impression, notice, response, cognition request, state
transition, budget change or purchase decision.

## Compatibility strategy

Saved mobility manifests v1-v3 remain byte-compatible and retain their current meaning.
A spatial campaign creates manifest schema v4 with model ID
`illustrative-road-spatial-study-v1`. V4 accepts city-pack schema 1 or 2 and optionally
the existing schema-v1 synthetic place set. No existing artifact is migrated or silently
upgraded.

The v4 manifest binds:

- the canonical city, agents and minute trace already used by legacy runs;
- an optional canonical place set and assignments as one all-or-none group;
- the canonical spatial scenario fingerprint and schema version;
- the exact opportunity JSONL SHA-256, byte count and record count;
- the exact opportunity-summary SHA-256 and opportunity model schema version.

All version fields require exact integers; booleans and coercible strings are refused.

## Artifact layout and publication

```text
city-runs/<run-id>/
  inputs/
    city.json
    agents.json
    spatial-campaign.json
    places.json                 # optional
    place-assignments.json      # optional
  outputs/
    opportunity-summary.json
    spatial-opportunities.jsonl
  run.json                      # published last
```

Every JSON document is canonical UTF-8 with one trailing newline. Each JSONL line is one
canonical C2 opportunity record in C2's total order. An empty evaluation produces a
zero-byte stream and the SHA-256 of empty bytes. The summary is the canonical C2 stage
counts document and retains every pre/post-filter denominator.

The store validates and computes all bytes before reserving an ID where practical, writes
with exclusive creation, flushes each file, verifies staged documents, and atomically
publishes `run.json` last. A failed write may leave a visibly incomplete reserved
directory, but never a completed manifest. A duplicate ID never changes original bytes.

The opportunity stream has a hard 536,870,912-byte ceiling. Loading and comparison are
streamed; the adapter never materializes the JSONL file as one byte string. The existing
campaign, placement, population, duration and per-day cap constraints bound the
opportunity count to 520,800 (20 capped phone policies plus at most 480 directional
roadside placements).

## Creation and replay

`create_city_run(..., spatial_scenario=scenario)` constructs immutable mobility from the
selected frozen inputs, runs the unchanged C2 evaluator, builds v4 hashes/counts and asks
the city adapter to persist the complete artifact. The store independently reconstructs
mobility and the evaluation before publication.

`CityRunStore.load()` rejects missing, extra, non-canonical or changed scenario, summary
or stream bytes. It reconstructs the evaluation from frozen inputs and compares the
stored JSONL incrementally against canonical expected records. It never trusts stored
counts without recomputation.

`replay_city_run()` recomputes trace and opportunity evidence, verifies every v4 binding,
returns the opportunity hashes/counts, and does not write to the source artifact. Two
fresh runs from identical frozen inputs have identical scientific bytes even though their
run IDs differ.

## CLI contract

`adlife city-run ... --spatial-campaign PATH` is the only new creation surface. The
scenario must be bounded valid UTF-8 JSON, match the exact selected city fingerprint, and
have the same duration as `--days`. Expected failures remain concise input errors and do
not reserve a run ID. Success output adds scenario and opportunity provenance while
retaining the existing mobility fields.

`adlife city-replay` reports the same v4 provenance after independent verification.
`city-view` remains a mobility observer during C3a; serving/filtering persisted
opportunities belongs to the later bounded API/UI stage.

## Security and scientific boundary

- Paths are derived only from the existing validated portable run ID and fixed filenames.
- Scenario and output files cannot be symlinks and must remain below the resolved root.
- Scenario text is never reflected in error messages.
- Exact canonical byte comparison catches appended whitespace, reordered keys and valid
  but changed records as corruption.
- `synthetic-opportunity-not-impression` remains present on every persisted record.
- Provider credentials, provider output and network access are absent from this flow.
- V4 evidence cannot affect routes, movement, state, budgets, cognition or purchases.

## Deferred work

C3 is not complete after C3a. C3b must define any opportunity-to-notice/response bridge,
atomic commit behavior and typed fallback without letting campaign/provider content
control movement, events, money or deterministic purchase rules. C4 owns metrics,
comparison and reports. D owns paged APIs and the full browser workbench.

## Acceptance evidence

- v1-v3 compatibility tests remain unchanged and green;
- v4 strict-contract tests cover exact versions, optional-place coherence and count/hash
  bounds;
- save/load tests cover zero and mixed-channel opportunity streams;
- tampering, missing/oversized/symlink artifacts and duplicate IDs fail closed;
- replay hashes source artifacts before and after and proves identical bytes;
- CLI JSON is clean for success and malformed/mismatched inputs;
- multiple `PYTHONHASHSEED` runs, branch coverage, build and exact-wheel smoke pass.
