# Spatial City Run Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist and replay C2 spatial opportunities as a strict schema-v4 city artifact
without changing legacy city runs or claiming impressions/outcomes.

**Architecture:** The pure C2 evaluator remains the only producer. A new strict manifest
binds frozen inputs and canonical output hashes; the city storage adapter writes fixed
paths and publishes the manifest last. Load/replay reconstruct expected evidence from
frozen inputs and compare the JSONL stream incrementally.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, Typer, SHA-256, canonical JSON/JSONL, pytest,
Hypothesis, Ruff, mypy, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-01-spatial-city-run-artifacts-design.md`

## Global Constraints

- Keep `src/adlife/core` free of Typer, FastAPI, SQLite, Plotly and provider adapters.
- Preserve manifest v1-v3 behavior and existing trace bytes.
- Publish `run.json` only after all fixed-path inputs/outputs verify.
- Bound the opportunity stream at exactly 536,870,912 bytes and 520,800 records.
- Persist only `synthetic-opportunity-not-impression`; add no cognition/state/outcome path.
- Use no network and reflect no untrusted scenario contents in diagnostics.

## Review Focus

- A valid-looking changed JSONL line must be corruption, not accepted or normalized.
- A scenario mismatch must fail before reserving the requested run ID.
- Optional place artifacts must be all present and bound, or all absent.
- A storage failure before manifest publication must remain visibly incomplete.
- Replay must not change timestamps, bytes or directory contents.

---

### Task 1: Freeze the v4 manifest contract

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `tests/unit/city/test_city_run_contract.py`

**Interfaces:**
- Produces: `CityRunManifestV4` and v4 dispatch from
  `parse_city_run_manifest_json(document)`.

- [x] Add failing strict-model tests for exact versions, hashes, byte/count bounds and
  optional place-field all-or-none validation.
- [x] Run the focused tests and confirm failure because v4 is unsupported.
- [x] Implement the minimal immutable v4 model and parser dispatch.
- [x] Run city-run contract, architecture, Ruff and mypy checks.
- [x] Commit and push `feat(city): define spatial run manifest`.

**Evidence:** The focused suite first reported 16 failures because v4 was unsupported,
while all 31 legacy cases remained green. After the minimal model/dispatch change, all 47
manifest tests passed. The related run-store, replay and architecture suites passed 182
tests with one expected platform/catalog skip; Ruff and strict mypy over 111 source files
passed.

### Task 2: Define canonical opportunity artifact evidence

**Files:**
- Modify: `src/adlife/core/simulation/spatial_opportunity.py`
- Modify: `tests/unit/city/test_spatial_opportunity.py`

**Interfaces:**
- Produces: `summarize_spatial_opportunity_artifact(evaluation) ->
  SpatialOpportunityArtifactSummary` and
  `spatial_opportunity_lines(evaluation) -> Iterator[bytes]`.

- [x] Add failing golden tests for canonical lines, empty SHA-256, byte/count values and
  the 520,800-record model ceiling.
- [x] Confirm RED on the missing interfaces.
- [x] Implement a frozen strict summary and iterator without I/O.
- [x] Run focused/property/performance/architecture tests, Ruff and mypy.
- [x] Commit and push `feat(city): summarize spatial opportunity artifacts`.

**Evidence:** Three artifact tests first failed on the missing iterator, summary model and
summarizer. They now pin exact canonical line bytes, empty-stream SHA-256, frozen summary
fields, the 520,800-record ceiling and 536,870,912-byte preflight refusal. All 114 related
opportunity, property, maximum-workload and architecture tests passed in 11.72 seconds;
Ruff passed and strict mypy remained clean over 111 source files.

### Task 3: Persist and load schema-v4 runs atomically

**Files:**
- Modify: `src/adlife/city/run_store.py`
- Modify: `tests/integration/test_city_run_store.py`

**Interfaces:**
- Consumes: v4 manifest, canonical line iterator and summary.
- Produces: `StoredCityRun.spatial_scenario` and
  `StoredCityRun.opportunity_evaluation`; optional `spatial_scenario` and
  `opportunity_evaluation` keyword arguments on `CityRunStore.save()`.

- [x] Add failing save/load tests for zero/mixed streams, fixed layout and legacy
  compatibility.
- [x] Add failing corruption tests for missing/changed/appended/oversized/symlinked
  scenario, summary and stream artifacts plus optional-place incoherence.
- [x] Confirm failures are caused by unsupported v4 persistence.
- [x] Implement preflight validation, exclusive streaming writes, exact incremental reads
  and final-manifest publication.
- [x] Inject write failures and prove no completed manifest is published; prove duplicate
  save preserves every original byte.
- [x] Run run-store/security/architecture tests, Ruff and mypy.
- [x] Commit and push `feat(city): persist spatial opportunity runs`.

**Evidence:** Nine v4 tests first failed because `CityRunStore.save()` had no spatial
inputs; after correcting one test-fixture import, every failure was the missing keyword
contract. The adapter now independently reconstructs evidence before writing, streams
canonical JSONL with exclusive creation, verifies exact bytes on load and publishes the
manifest last. Empty/mixed streams, optional places, missing/changed/oversized/symlinked
artifacts, duplicate preservation and injected stream failure are covered. Related
storage, replay, manifest and architecture suites passed 196 tests with four expected
Windows capability/catalog skips; Ruff and strict mypy over 111 source files passed.

### Task 4: Create and replay complete spatial runs

**Files:**
- Modify: `src/adlife/city/runs.py`
- Modify: `tests/integration/test_city_run_replay.py`

**Interfaces:**
- Consumes: `create_city_run(..., spatial_scenario: SpatialCampaignScenario | None)`.
- Produces: opportunity provenance fields on `CityReplayResult`.

- [x] Add failing creation/replay tests for v4 with and without places, two-run byte
  identity, source hash immutability, and scenario/city/day mismatch before save.
- [x] Confirm RED because creation ignores spatial scenarios.
- [x] Build v4 from independently summarized C2 evaluation and pass it to the store.
- [x] Extend replay to verify scenario, summary and stream provenance without writes.
- [x] Run city create/replay/store suites, both focused hash seeds, Ruff and mypy.
- [x] Commit and push `feat(city): replay spatial opportunity runs`.

**Evidence:** Three creation/replay tests first failed because `create_city_run()` did not
accept a spatial scenario. V4 creation now supports runs with or without place bindings;
two independently created runs have identical manifests/scientific artifacts, replay
recomputes all opportunity provenance, and source file hashes remain unchanged. City and
duration mismatches fail before reserving an ID. The related suite passed 199 tests with
four expected skips; focused runs under `PYTHONHASHSEED=0` and `12345` each passed 57 tests
with four skips. Ruff and strict mypy over 111 source files passed.

### Task 5: Expose the bounded CLI workflow

**Files:**
- Modify: `src/adlife/cli/commands/city_run.py`
- Modify: `src/adlife/cli/commands/city_replay.py`
- Modify: `tests/cli/test_city_run.py`
- Modify: `tests/cli/test_city_replay.py`

**Interfaces:**
- Produces: `city-run --spatial-campaign PATH` and v4 JSON/human provenance.

- [x] Add failing CLI tests for successful local/catalog runs, clean JSON, malformed,
  oversized, city-mismatched and duration-mismatched scenarios.
- [x] Confirm expected exit code 2 and no reserved run for every invalid input.
- [x] Load the bounded scenario and emit scenario/stream/summary hashes and counts.
- [x] Extend replay output with the same verified fields.
- [x] Run all city CLI and error-boundary tests, Ruff and mypy.
- [x] Commit and push `feat(cli): run persisted spatial studies`.

**Evidence:** Creation tests first failed with Typer's missing `--spatial-campaign`
diagnostic and replay lacked scenario provenance. Local and packaged-catalog runs now emit
clean JSON with schema v4, scenario/stream/summary hashes, exact byte/record counts, every
funnel denominator and the literal claim scope. Malformed, oversized, city-mismatched and
duration-mismatched scenarios exit 2 without reserving an ID or exposing input contents.
The related CLI, error-boundary, storage, replay, manifest and architecture suites passed
236 tests with four expected skips. Both focused hash seeds passed 22 tests; CLI help,
Ruff and strict mypy over 111 source files passed.

### Task 6: Documentation, packaging and release evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `tests/packaging/test_documentation.py`
- Modify: this plan with exact evidence.

- [x] Document C3a creation/replay without claiming full C3, UI, metrics or outcomes.
- [x] Extend installed-wheel smoke with a schema-v4 run and replay.
- [x] Run lock sync, Ruff, mypy, full pytest, two hash seeds and branch coverage.
- [x] Build sdist/wheel and run the exact wheel smoke outside the checkout.
- [x] Inspect status/diff/check; commit and push
  `docs(city): document spatial run artifacts`.

**Evidence so far:** The documentation contract first failed on the pre-C3a public
contract. README, architecture, CLI reference, city pilot, reproducibility, model card,
limitations and the long-term roadmap now agree that v4 persists/replays opportunity
evidence while the causal bridge, viewer opportunity panel, metrics and reports remain
open. All 21 documentation tests pass. The clean-room smoke's regression first failed
because the script still required v3; all five smoke-script tests now prove it supplies
the spatial scenario and verifies v4 stream/summary provenance through replay. A separate
regression also proved and fixed that the mobility-only viewer must accept a valid v4 run.

**Final evidence (2026-10-01):** `uv sync --locked --all-groups` resolved 78 and checked
68 packages. Ruff format checked 264 files, Ruff lint passed, and mypy passed all 111
source files. The full suite passed with 3,493 tests and 16 skips in 473.80 seconds;
complete reruns under `PYTHONHASHSEED=0` and `12345` passed the same counts in 533.29 and
538.86 seconds. Branch coverage passed 3,493/16 in 1,019.84 seconds at 91.24%, above the
85% floor. Headless Chromium passed its browser interaction test (1 in 4.94 seconds), all
25 TUI tests passed, and both live/headless equivalence tests passed in 50.56 seconds.

`uv build --no-sources` produced the 271,076-byte sdist and 334,825-byte wheel. The exact
wheel passed the outside-checkout clean-room smoke, including a 4,862,319-byte report and
packaged-catalog schema-v4 spatial run/replay. Source version, human/JSON offline doctor,
human/JSON headless demos and default noninteractive demo completed; doctor reported the
expected local CP1252 warning (7/8) while proving no network use, and both headless demos
emitted 2,042 events. The isolated maximum rules run passed at 18.45 seconds with 5,685
events, 2 MiB traced peak memory, a 3,629,056-byte database and 4,862,947-byte report.
The maximum spatial evaluator passed at 10.50 seconds over 6,048,000 eligible
agent-minutes with 4,200 retained opportunities. `git diff --check` passed before the
documentation commit and the pushed tree was clean.
