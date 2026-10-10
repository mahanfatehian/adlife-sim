# Saved-Run Spatial Response Ledger Design

**Status:** Approved autonomous roadmap refinement for D3/D4

**Parent roadmap:**
`docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`

## Purpose

Schema-v6 city runs already expose a verified `spatial-response-metrics-v1` projection at
`GET /api/spatial-response-metrics`. The saved-run browser does not consume that endpoint,
so an analyst can inspect individual response records and final state but cannot inspect the
full-run receipt-backed response summary. This slice presents that existing evidence in the
read-only browser without adding a simulation mechanism, persistence format, remote server,
or write-capable API.

This is incremental D3/D4 progress. It does not complete the production workbench, city
catalog, job execution, authentication, provider settings, purchase proxy, social model, or
external validation gates.

## Evidence contract

The browser consumes the exact verified object served by the existing fixed endpoint. It
must require:

- model ID `spatial-response-metrics-v1`;
- claim scope `synthetic-response-metrics-not-observed-outcomes`;
- one overall direct-response receipt set;
- the canonical `roadside` and `mobile` direct-response receipt sets;
- one overall committed-state receipt set; and
- one to twenty canonical campaign committed-state receipt sets, matching the run's
  campaign catalog exactly.

The browser must not derive, repair, interpolate, or persist metrics. It must not invent a
zero when a request fails or when a legacy run has no response metrics. Backend startup
continues to independently rederive the supplied projection from the loaded immutable run.

## Presentation contract

The response ledger is a full-run evidence panel, independent of the scrubbed timeline
minute. It has two deliberately separate tables:

1. **Rule-response receipts** show response count, reach, frequency, mean planned sentiment
   delta, and mean planned recall delta for all channels, roadside, and mobile. Every cell
   exposes its numerator, denominator, source artifact, and source event type to keyboard and
   assistive-technology users.
2. **Committed-state receipts** show initial mean, final mean, and mean change for brand
   sentiment, recall strength, and purchase-intention proxy. The analyst can switch between
   the overall series and one campaign using a bounded native selection control. Every row
   exposes its denominator and source artifacts.

Copy must state that response counts are deterministic rule-processing records, not observed
engagement; planned deltas differ from bounded committed state changes; purchase intention is
an uncalibrated synthetic proxy, not purchase probability, a transaction, sales, or a sales
forecast. Committed state must never be allocated to a channel because same-minute notices
from multiple channels can share one nonlinear state update.

## Interaction and accessibility

- Existing play, pause, speed, scrub, agent selection, and campaign selection remain
  presentation-only and cannot modify the full-run ledger.
- The ledger is keyboard reachable. Metric cells carry visible focus and an accessible
  provenance label rather than relying on a mouse-only title.
- A campaign selector uses native form semantics and canonical bounded options.
- Compact status text may be an atomic polite live region; large tables are not live regions.
- At 390 CSS pixels, tables remain contained with horizontal scrolling inside the panel and
  never widen the document.
- Persian, Unicode, and hostile-looking labels are inserted through DOM text properties only.
- No external image, font, script, tile, or other network request is introduced.

## Failure behavior

- Runs without verified response metrics do not show the panel.
- A metrics fetch or validation failure shows the existing safe error banner and no fabricated
  evidence.
- A superseded timeline request cannot erase or mutate already loaded full-run metrics.
- Unknown model IDs, claim scopes, missing series, non-finite values, or malformed receipts
  are refused by rendering validation.
- Source paths, raw provider responses, prompts, credentials, environment values, and raw
  campaign copy never enter the panel.

## Verification

Browser tests must exercise real derived receipts, a campaign with zero responses, more than
one campaign, timeline scrubbing, legacy absence, safe hostile/Unicode labels, keyboard
provenance, narrow layout, and a zero-external-request network guard. API tests continue to
prove exact response bytes, GET-only behavior, legacy absence, and startup refusal when a
supplied projection does not match independently rederived evidence.
