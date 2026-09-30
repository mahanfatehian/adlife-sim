# Spatial Opportunity Semantics Implementation Plan

> Execution target: `main`, inline test-driven implementation. Commit and push each
> completed logical group. Keep every command background/noninteractive on Windows.

**Goal:** Complete roadmap C2 with a pure deterministic evaluator that distinguishes
route traversal, proximity, approximate-facing evidence and phone-use assumptions from
impressions, notice and outcomes.

**Spec:** [Spatial opportunity semantics design](../specs/2026-09-30-spatial-opportunity-semantics-design.md)

**Non-goals:** Persistence, CLI run integration, FastAPI, UI, cognition, impression/notice
rules, response/purchase effects, metrics and reports remain C3/C4 work.

## Required process

For every behavioral task: add the externally meaningful regression, run it red, make the
smallest core-only fix, rerun focused tests, run related suites, inspect the diff, commit,
and push. Do not regenerate compatibility goldens.

### Task 1: Freeze design and execution contract

**Files:**
- Add: `docs/superpowers/specs/2026-09-30-spatial-opportunity-semantics-design.md`
- Add: this plan.

- [x] Define terms, causal chain, denominators and scientific claim boundary.
- [x] Fix time quantization, orientation convention, draw namespace, cap scope and order.
- [x] Preserve existing city frame and trace serialization.
- [ ] Commit and push `docs(city): plan spatial opportunity semantics`.

### Task 2: Expose immutable continuous road traversals

**Files:**
- Modify: `src/adlife/core/simulation/city_mobility.py`
- Modify: `tests/unit/city/test_city_mobility.py`
- Verify: `tests/unit/city/test_city_trace.py`

**Produces:** `CityRoadTraversal` and `CityMobility.road_traversals(day_index)`.

- [x] Add red tests for outbound/return direction, continuous boundaries, multi-road
  sequence, weekend routing, invalid day and stable order.
- [x] Prove the existing frame document and compatibility trace digest do not change.
- [x] Implement from the already frozen path/edge schedule without duplicating routing.
- [x] Run city mobility/trace/property tests, Ruff and mypy.
- [x] Commit and push `feat(city): expose deterministic road traversals`.

**Evidence:** Eight focused tests first failed on the missing traversal API. After the
minimal implementation, 39 mobility/trace/property tests passed; Ruff format/lint and
strict mypy over 110 source files passed. The existing v1 trace digest remained unchanged.

### Task 3: Define typed opportunity evidence and roadside semantics

**Files:**
- Add: `src/adlife/core/simulation/spatial_opportunity.py`
- Add: `tests/unit/city/test_spatial_opportunity.py`
- Modify: `src/adlife/core/simulation/__init__.py` only if public exports are already
  conventional there.

**Produces:** strict roadside/phone opportunity models, stage counts, evaluation result and
`evaluate_spatial_opportunities` (roadside path first).

- [x] Add red contract/golden tests for strict frozen output, stable IDs and one/no/multiple
  directional crossings.
- [x] Add red geometry tests for forward/reverse physical fractions, curved roads,
  shape-point approach bearings, facing threshold and half-open windows.
- [x] Implement millisecond quantization, polyline approach point/bearing and roadside
  candidate generation in pure core code.
- [x] Add canonical cap machinery and stable chronological ordering; exhaustive repeated
  cap-scope tests use phone agent-minutes in Task 4 because current two-leg routing cannot
  traverse one physical road in the same direction twice in a day.
- [x] Run focused city/spatial/architecture tests, Ruff and mypy.
- [x] Commit and push `feat(city): evaluate roadside opportunities`.

**Evidence:** Seven golden tests first failed because the evaluator module did not exist.
Eight final roadside tests cover typed/frozen/non-finite contracts, exact identity material,
stage denominators, inactive and back-facing refusal, the inclusive 90-degree threshold,
forward/reverse travel, continuous same-minute ordering, exact half-open boundaries, curved
shape-point approach geometry, input immutability and duration mismatch. All 170 related
city/spatial/architecture/documentation tests passed; Ruff and strict mypy over 111 source
files passed.

### Task 4: Implement keyed phone opportunities and canonical evaluation

**Files:**
- Modify: `src/adlife/core/simulation/spatial_opportunity.py`
- Modify: `tests/unit/city/test_spatial_opportunity.py`
- Add: `tests/property/test_spatial_opportunity_order.py`

- [x] Add red tests for activity eligibility, probability 0/1, exact draw namespace,
  threshold-independent common draws, windows and day-scoped caps.
- [x] Add a property test permuting campaigns, placements, windows and activities.
- [x] Implement active-window iteration, keyed draws, canonical candidate ordering, shared
  cap application and immutable evaluation counts.
- [x] Prove evaluator inputs are unchanged and outputs match across hash seeds.
- [x] Run focused/property/core-boundary tests, Ruff and mypy.
- [x] Commit and push `feat(city): evaluate phone opportunities`.

**Evidence:** Six phone tests and one property test first failed on the explicit pre-C2
phone refusal. The exact draw test then failed separately while replacing per-draw
Mersenne-Twister construction with the specified SHA-derived SplitMix64 counter stream.
All 15 opportunity/property tests passed under normal execution and both hash seeds; 397
related city/property/architecture/documentation tests passed with one expected catalog
skip. The maximum accepted phone workload (20 policies, 30 agents, 7 days) evaluates 6,048,000
eligible agent-minutes, deterministically finds 3,025,584 successes, cap-retains 4,200
records and passed the automated 30-second ceiling in 9.75 seconds. The same workload
improved from 45.76 seconds with per-draw `random.Random` construction to 9.43 seconds in
the direct benchmark (4.85x). Ruff and strict mypy over 111 source files passed.

### Task 5: Public methodology and release evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: this plan with exact evidence.

- [ ] Document opportunity terms and denominators without claiming real impressions,
  traffic, device use, attention or outcomes.
- [ ] Mark only C2 complete; state clearly that C3 persistence/run integration and C4
  metrics/reporting remain required.
- [ ] Run lock sync, Ruff format/lint, strict mypy, full pytest, both hash seeds and branch
  coverage.
- [ ] Build sdist/wheel and run the exact clean-room smoke.
- [ ] Inspect status/diff/check and commit/push
  `docs(city): document spatial opportunity semantics`.

## Final self-review questions

- Can a professor point to the exact denominator before each filter?
- Does a billboard record prove only a synthetic directional passage and heuristic facing?
- Does changing phone probability reuse the same keyed draw?
- Can list/dictionary/hash iteration change an opportunity or ID?
- Can a cap affect another placement, agent or day?
- Are existing city trace bytes and replay unchanged?
- Is every remaining claim honestly deferred to C3/C4?

