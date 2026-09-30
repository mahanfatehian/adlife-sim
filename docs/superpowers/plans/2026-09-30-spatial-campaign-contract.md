# Spatial Campaign Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement C1 as a strict, canonical and offline-validatable geographic campaign
input boundary without adding exposure or outcome semantics prematurely.

**Architecture:** Add a separate core spatial-scenario contract and city binding function,
then a bounded loader and validation-only CLI adapter. Preserve the old zone campaign and
all city run versions unchanged. C2 will consume only the validated interfaces produced
here.

**Tech Stack:** Python 3.11â€“3.13, Pydantic v2, Typer, uv, pytest/Hypothesis, Ruff, mypy.

**Spec:** [Spatial campaign contract design](../specs/2026-09-30-spatial-campaign-contract-design.md)

## Global constraints

- Work on `main`; do not change `defense-ready`.
- Keep `src/adlife/core` adapter-free and keep the legacy zone campaign byte-compatible.
- No advertising event, impression, cognition, purchase, web write or remote data path in C1.
- Every behavior change begins with a witnessed failing test and receives narrow, related
  and full-suite verification.
- Use strict finite immutable contracts, canonical JSON, bounded local loading and concise
  redacted failures.
- Push each focused commit to the existing `origin/main`; never add or change a remote.

## Review focus

- A coordinate names a real road but is physically elsewhere: refuse; never snap it.
- A road supports only one direction: a placement cannot claim the other direction.
- Duplicate records multiply opportunity or frequency: canonical keys refuse them.
- Scenario element order changes hashes: canonicalization/property tests prove invariance.
- A scenario names the city but binds an old hash: refuse before any simulation work.
- Campaign text or parse errors leak credentials: reuse the public screen and generic loader
  diagnostics.

---

### Task 1: Versioned spatial scenario and placement contracts

**Files:**
- Create: `src/adlife/core/domain/spatial_campaign.py`
- Modify: `src/adlife/core/domain/__init__.py`
- Create: `tests/unit/city/test_spatial_campaign_contract.py`
- Modify: `tests/contract/test_schema_versions.py`

**Produces:** `SpatialActiveWindow`, `SpatialCampaign`, `RoadsideBillboardPlacement`,
`PhoneOpportunityPlacement`, `SpatialCampaignScenario`, strict parser and fingerprint.

- [x] Write failing tests for exact schema/types, IDs, creative hashes, public text,
  windows, caps, campaign references, duplicate IDs/policies/physical keys, canonical order
  and unknown fields.
- [x] Run the narrow tests and confirm the missing contract is the cause.
- [x] Implement the smallest immutable canonical models and strict JSON parser.
- [x] Add schema-version contract coverage and re-run narrow tests.
- [x] Commit and push `feat(city): define spatial campaign scenarios`.

**Evidence:** RED 23 failed / 21 passed because the contract module was absent; GREEN
44 focused passed. Related city/schema/architecture tests passed 359 / skipped 1; Ruff,
mypy (108 source files) and the full suite passed 3,404 / skipped 13 in 452.22 seconds.

### Task 2: Exact road and coordinate binding

**Files:**
- Modify: `src/adlife/core/domain/spatial_campaign.py`
- Create: `tests/unit/city/test_spatial_campaign_binding.py`
- Create: `tests/property/test_spatial_campaign_order.py`

**Consumes:** Task 1 scenario and existing `CityPackDocument` v1/v2 geometry.

**Produces:** `SpatialScenarioEvidence` and
`validate_spatial_scenario_against_city(scenario, pack)`.

- [x] Write failing tests for city ID/hash mismatch, unknown road, unsupported one-way
  direction, v1/v2/intermediate geometry, out-of-bounds coordinate, off-network error,
  endpoint refusal and exact evidence.
- [x] Implement deterministic geometry interpolation and bounded haversine error without
  snapping or mutating input.
- [x] Add property coverage for campaign/placement/window/activity permutations.
- [x] Run city domain, mobility compatibility and architecture tests.
- [x] Commit and push `feat(city): validate spatial campaign geometry`.

**Evidence:** RED 10 failed because city binding was absent; GREEN 10 passed, then 150
focused/domain/mobility/architecture tests passed. Ruff, mypy and the full suite passed
3,414 / skipped 13 in 461.09 seconds.

### Task 3: Bounded loader and offline validation CLI

**Files:**
- Create: `src/adlife/city/spatial_loader.py`
- Create: `src/adlife/cli/commands/city_campaign.py`
- Modify: `src/adlife/cli/app.py`
- Modify: `docs/cli-reference.md`
- Create: `tests/unit/city/test_spatial_loader.py`
- Create: `tests/cli/test_city_campaign.py`
- Modify: `tests/cli/test_json_output.py`

**Produces:** `load_spatial_campaign_scenario(path)`, 2 MiB input cap and
`city-campaign validate` for local/catalog city selection.

- [x] Write and witness failing loader tests for missing, unreadable, oversized, malformed,
  non-UTF-8 and invalid-version files with non-reflective diagnostics.
- [x] Implement the bounded loader.
- [x] Write and witness failing CLI tests for local/catalog success, exact JSON, selector
  refusal, bad binding, no traceback, offline behavior and help/reference coverage.
- [x] Register the command and translate only expected input/artifact errors.
- [x] Run CLI/root/documentation/security/architecture suites.
- [x] Commit and push `feat(cli): validate spatial campaign inputs`.

**Evidence:** RED 10 failed / 1 passed for the missing loader/command, strengthened to
cover the exact redacted JSON diagnostic; GREEN 11 focused passed. Related CLI passed
325, final focused/documentation/architecture passed 133, Ruff and mypy passed, and the
full suite passed 3,425 / skipped 13 in 461.83 seconds.

### Task 4: Public contract and release evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `scripts/smoke_release.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: this plan with exact evidence.

- [x] Document the input format and validation workflow without claiming impressions,
  attention, real inventory, traffic, residents or sales.
- [x] Mark only C1 complete and name C2â€“C4 as required before a geographic campaign run.
- [x] Run Ruff format/lint, strict mypy, full pytest, both hash seeds and branch coverage.
- [x] Build sdist/wheel and run the exact clean-room smoke without adding network access.
- [x] Record exact evidence, inspect diff/status and push
  `docs(city): document spatial campaign contract`.

**Evidence:** The installed-wheel smoke regression first failed because spatial validation
was absent, then all 5 smoke tests passed; the final packaging/architecture run passed 151
and skipped 4 platform-specific cases.
Lock sync succeeded; Ruff reported 256 formatted files and no lint issues; mypy reported no
issues in 110 source files. Final review added a failing regression proving that altered
duplicate phone policies could multiply one campaign's opportunity; schema v1 now permits
exactly one phone policy per campaign, and all 40 focused tests passed. The refreshed full
suite passed 3,425 / skipped 13 in 490.77 seconds; hash seed 0 passed the same counts in
487.99 seconds and seed 12345 in 430.05 seconds. Branch coverage passed 3,425 / skipped 13
in 862.29 seconds at 91.35% against the 85% minimum; spatial core/loader/CLI measured
96%/100%/95%. `uv build --no-sources` built the
0.1.0 sdist and wheel; the exact clean-room wheel smoke validated both spatial channels,
place-aware v3 replay and a 4,862,319-byte report. Direct offline validation produced
scenario SHA-256 `8e6177b96636bcc37e48e217871e45cff3cd3c27fcca9bebc65896eb3e51f3c6`
with zero-meter binding error. The maximum 30-agent/seven-day run passed at 16.45 seconds,
5,685 events, 2 MiB peak traced memory, a 3,629,056-byte database and 4,862,947-byte report.
Offline doctor retained only the documented Windows `cp1252` environment warning.

## Self-review record

- Scope: C1 ends at validated immutable inputs; it deliberately does not create ad events.
- Scientific honesty: phone probability and billboard geometry are assumptions, not
  observations; creative hash is identity evidence, not rights evidence.
- Compatibility: no legacy `Campaign`, city mobility trace or manifest schema changes.
- Handoff: C2 can consume stable road fraction/direction/orientation, explicit phone policy,
  active windows and caps without parsing adapter-owned data.
