# Spatial Attention Evidence Design

**Status:** Approved autonomous roadmap refinement for C3b

**Parent roadmap:** `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

C3a persists deterministic geographic advertising opportunities but intentionally stops
before an outcome. C3b adds the smallest honest causal continuation: each retained
opportunity creates a synthetic model impression, and a transparent keyed draw may create
a synthetic notice event. Both are immutable evidence. Neither changes an agent, invokes
cognition, spends money, creates a purchase proxy, or claims observed human behavior.

This slice exists so a saved city study and its browser can distinguish opportunity,
impression and notice instead of presenting all three as one concept. Response, recall,
sharing, purchase rules, spatial metrics/comparison and calibrated attention remain later
work.

## Deterministic attention model

The pure core function
`evaluate_spatial_attention(opportunities, *, seed) -> SpatialAttentionEvaluation`
accepts an already validated `SpatialOpportunityEvaluation` and the city-run seed.

For every opportunity, in canonical opportunity order, it emits:

1. one `spatial.impression` record caused by the exact opportunity ID; then
2. one `spatial.noticed` record caused by that impression only when a keyed draw is less
   than `0.5`.

The v1 probability is deliberately the same for phone and roadside channels. It is an
illustrative neutral assumption, not a fitted probability and not a channel-effect claim.
The probability and algorithm are pinned by model ID `spatial-attention-v1` and present
in the artifact summary.

The notice draw is the first 53 bits of SHA-256 material divided by `2**53`. Its material
is canonical JSON containing the model ID, run seed, placement ID, agent ID, channel and
absolute opportunity millisecond. It excludes scenario hash, campaign ID, campaign name,
creative hash and provider output. Therefore changing campaign copy/identity while
holding placement and opportunity timing fixed cannot change numerical attention.

Event IDs are SHA-256 digests of canonical causal identity. Impression identity includes
the opportunity ID; notice identity includes the impression ID. IDs may therefore change
when frozen scenario provenance changes even though the keyed notice decision does not.

## Strict event and summary contracts

Every record is a frozen, extra-forbidden Pydantic model with:

- schema version 1 and attention model ID;
- literal claim scope `synthetic-attention-not-observed-behavior`;
- event type, stable event ID and one causal predecessor;
- scenario/city fingerprints, campaign/placement/agent/channel identity;
- day, model minute and millisecond within minute.

Every impression records the exact notice draw, probability and resulting boolean, so a
missing notice remains auditable. A notice record repeats that evidence and exists only
for a passing draw. Events are totally ordered by model minute, millisecond, agent,
campaign, placement, channel and stage (impression before notice). Cross-validation
rejects duplicate IDs, broken causes, mismatched draw evidence, fingerprint mismatches,
non-canonical ordering, non-finite values and incoherent counts.

`SpatialAttentionCounts` reports opportunity, impression and noticed totals plus both
channel splits. Impression count must equal opportunity count. Noticed counts cannot
exceed impressions. At most 1,041,600 records may exist (twice the existing 520,800
opportunity ceiling). Canonical JSONL is bounded to 1,073,741,824 bytes and summarized
with exact SHA-256, byte count and funnel counts.

## Saved-run compatibility and artifact layout

City-run manifest schemas v1-v4 remain readable with their existing meanings. A new
spatial run is schema v5 with model ID `illustrative-road-spatial-attention-study-v1`.
It contains every v4 binding plus attention schema/model identity, stream hash, summary
hash, byte count, impression count and noticed count.

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
    attention-summary.json
    spatial-attention.jsonl
  run.json                      # published last
```

The store independently recomputes both evaluations before save and during load. It
writes fixed paths exclusively, flushes them, compares exact canonical bytes, and
publishes `run.json` last. Missing, changed, appended, oversized, non-canonical or
symlinked attention artifacts are corruption. Replay recomputes the stream and hashes
without writing. Existing v4 artifacts neither require nor fabricate attention evidence.

## CLI and browser contract

`city-run --spatial-campaign` creates schema v5 and reports attention model, hashes,
bytes, impressions and notices in both human and JSON modes. `city-replay` returns the
same independently verified evidence.

The saved-run FastAPI adapter accepts v4 opportunity-only and v5 attention runs. V5 adds
read-only `/api/attention-summary` and paged `/api/attention-events` endpoints filtered
by exact model minute and optional known agent. No endpoint accepts a server path or
mutates the artifact. The vanilla browser shows persisted impression and notice counts
and current-minute causal records using safe DOM text insertion. Scrubbing, selection,
playback and rendering remain presentation-only.

## Failure, security and scientific boundaries

- Invalid attention inputs fail before run-directory reservation where practical.
- A failed attention write leaves no completed manifest; duplicate IDs never overwrite.
- Campaign copy, provider output and campaign identity are absent from the numeric draw.
- No network, credentials, raw provider bodies, arbitrary paths or personal data enter
  this model or artifact.
- C3b creates no cognition request, response delta, memory, social event, budget change,
  purchase probability or purchase proxy.
- UI labels say synthetic/model evidence and never imply measured attention, viewability,
  sales or real residents.

## Acceptance evidence

- golden tests pin exact draws, event IDs/order, causal links, counts and bytes;
- copy/campaign-ID changes preserve numerical notice decisions for fixed placements;
- input permutation and multiple `PYTHONHASHSEED` values produce identical evidence;
- v1-v4 manifests and artifacts remain readable and unchanged;
- v5 save/load/replay rejects every attention artifact corruption class;
- source hashes are unchanged by replay and browser observation;
- API pagination/order/error behavior and browser labels/interactions are asserted;
- Ruff, strict mypy, full/hash-seed/coverage suites, build and exact-wheel smoke pass.

## Deferred work

C3 remains open after C3b. A later focused slice must define responses and bounded state
transitions, optional cognition resolution, commit/persistence ordering and deterministic
fallback without allowing a provider to control events, movement, budgets or purchase
probability. C4 owns spatial metrics, matched comparison and reports.
