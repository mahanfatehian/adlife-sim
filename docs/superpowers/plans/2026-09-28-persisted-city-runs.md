# Persisted City Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save, reopen and verify an offline city mobility trace without implying that it is a geographic advertising run.

**Architecture:** Keep the existing `CityMobility` engine and `CityPack` v1 unchanged initially. A core summary streams canonical minute frames into a digest; a dedicated adapter freezes the pack and settings in a new no-clobber city-run directory. CLI commands create and replay artifacts, and a separate local viewer command opens a validated saved run through the existing read-only FastAPI app. No frame matrix is persisted or invented in the browser.

**Tech Stack:** Existing Python 3.11–3.13, uv, Pydantic, FastAPI, Typer, SQLite-free city artifact adapter, pytest/Hypothesis, Ruff, mypy.

**Spec:** [Production-track city platform design](../specs/2026-09-28-production-city-platform-design.md), specifically geographic data, timeline, replay and local profile contracts. This is package **A** of the [roadmap](2026-09-28-production-city-platform-roadmap.md).

## Global Constraints

- Preserve `adlife city` ephemeral viewer and all existing zone run/replay artifacts.
- New saved-city command limits are 1–30 agents and 1–7 days until the streaming hash path is benchmarked; the existing unsaved city preview remains 1–250 and 1–31.
- A saved run contains only fictional agents, local pack, seed, settings and derived integrity evidence; no provider or campaign data.
- `src/adlife/core` remains adapter-free. The UI renders only core frames from frozen validated inputs.
- Same pack, seed, settings and compatible implementation must reproduce the same normalized minute-frame stream. Record runtime/model version; do not claim cross-platform bitwise equality of floating-point positions.
- Never overwrite an existing run ID, follow symlinks outside an explicitly chosen output root, or silently repair a corrupt source.
- No network, API key, tile service or new default dependency is required.

## Review Focus

- Same logical pack with nodes/roads permuted has the same hash and trace: Task 1 test.
- A changed `inputs/city.json` that still validates is detected before viewing: Task 2 test.
- Two writers race for one run ID: exactly one succeeds, without replacing the winner: Task 2 test.
- A replay or viewer sees a partial/crashed publication: clean refusal, not a trace from available fragments: Task 2/3 test.
- Arbitrary path or another run ID in an HTTP URL cannot open server files: Task 4 test.

---

## File map and locked interfaces

| File | Responsibility |
| --- | --- |
| `src/adlife/core/domain/city_run.py` | Strict `CityRunManifest` and `CityTraceSummary` v1; run ID, bounds, version and hash validation. |
| `src/adlife/core/simulation/city_trace.py` | `summarize_city_trace(mobility: CityMobility) -> CityTraceSummary`; stream each `frame_document(minute)` in ascending minute order as canonical UTF-8 JSON plus `\n` into SHA-256; do not keep all frames in memory. |
| `src/adlife/city/run_store.py` | `CityRunStore(root: Path)`, `save(manifest: CityRunManifest, pack: CityPack, agents: tuple[CityAgent, ...]) -> Path`, `load(run_id: str) -> StoredCityRun`; bounded/contained no-clobber file I/O, reconstruction and typed corruption errors. |
| `src/adlife/city/runs.py` | `create_city_run(...) -> StoredCityRun` and `replay_city_run(stored: StoredCityRun) -> CityReplayResult`; orchestration only. |
| `src/adlife/cli/commands/city_run.py` | `adlife city-run PACK --output-root ROOT --run-id ID [--agents N --days N --seed N]`. |
| `src/adlife/cli/commands/city_replay.py` | `adlife city-replay ROOT ID`; read/verify, no source mutation. |
| `src/adlife/cli/commands/city_view.py` | `adlife city-view ROOT ID [--port N]`; loopback, saved run only. |
| `src/adlife/city/web.py`, `static/index.html`, `static/app.js` | Optional validated run metadata and visible saved-run identity; retain read-only routes. |
| `tests/unit/city/test_city_trace.py`, `test_city_run_contract.py` | Core determinism and strict models. |
| `tests/integration/test_city_run_store.py`, `test_city_run_replay.py` | Corruption, no-clobber, failure and replay invariants. |
| `tests/cli/test_city_run.py`, `test_city_replay.py`, `test_city_view.py`, `tests/unit/city/test_city_web.py` | Exit/JSON and viewer contracts. |

Artifact v1 layout, under an operator-selected root:

```text
ROOT/city-runs/RUN_ID/
  run.json                # schema, ID, model/runtime, seed, bounds, city/trace hashes
  inputs/city.json        # canonical, validated pack; never a reference to mutable catalog
  inputs/agents.json      # canonical generated fictional home/work/leisure assignments
```

`run.json` records the SHA-256 of canonical `inputs/city.json` and `agents.json`
and of the full normalized minute-frame stream, plus frame/position counts. It
records completion status and metadata; a missing or incomplete manifest is invalid.
On load, regenerated assignments must match the frozen `agents.json` exactly;
neither the file nor the generator silently replaces the other. The trace hash
proves exact re-execution *against
that artifact*, not tamper-proofness against an owner rewriting both files. Do not
reuse `RunManifest`/`Scenario` from the zone ad engine; those fields have different
semantics. Reuse `validate_run_id` and canonical serialization where appropriate.

The first implementation must benchmark the full 30-agent, 7-day streaming digest.
If it is unreasonably slow, retain the bounds and optimize in a separate tested task;
do not switch to sparse sampling while still claiming full-frame replay.

### Task 1: Strict city-run contracts and a streaming trace fingerprint

**Files:** Create `src/adlife/core/domain/city_run.py`, `src/adlife/core/simulation/city_trace.py`; test `tests/unit/city/test_city_run_contract.py`, `tests/unit/city/test_city_trace.py`.

**Interfaces:** Produce `CityRunManifest` (`schema_version=1`, completed status,
run ID, model ID, package/runtime identity, city hash, trace hash, assignments hash,
seed, agent count, days, frame and position counts); `CityTraceSummary` (the three
hash/count fields); `summarize_city_trace(mobility: CityMobility) -> CityTraceSummary`.
The digest input is `canonical_json(mobility.frame_document(minute)) + "\n"` for
`minute in range(days * 1440)`, with no selected-agent route in that stream.

- [x] Write failing tests: two identical runs and permuted packs give identical
  summaries; a changed seed or route changes the summary; `frame_count == days*1440`
  and `position_count == frame_count*agent_count`; unknown schema, booleans-as-int,
  malformed hashes and an invalid run ID are refused.
- [x] Run `uv run pytest tests/unit/city/test_city_run_contract.py tests/unit/city/test_city_trace.py -q`; confirm the new interface is absent/RED.
- [x] Implement the two files with bounded immutable models and streaming hashing;
  keep wall-clock metadata out of the digest and avoid storing all frames in a list.
- [x] Run those two files, `tests/unit/city/test_city_mobility.py`, Ruff and mypy;
  require PASS. Review the digest's ordering and exact JSON encoding.
- [x] Commit this independently testable core contract with the configured identity.

### Task 2: No-clobber, bounded city artifact store

**Files:** Create `src/adlife/city/run_store.py`; test `tests/integration/test_city_run_store.py`.

**Interfaces:** Produce `StoredCityRun(manifest: CityRunManifest, pack: CityPack,
mobility: CityMobility, directory: Path)`, `CityRunStore.save(...) -> Path`, and
`CityRunStore.load(run_id) -> StoredCityRun`. Define typed duplicate, missing,
unsafe-path and corrupt-artifact errors whose messages never echo input text.
Loading verifies canonical pack and agents hashes, model/schema compatibility,
configuration bounds, generated-versus-frozen assignments and full trace digest
before returning a run.

- [x] Write failing tests: new directory layout; duplicate refusal; no replacement
  even under two racing writers; missing/truncated/malformed/oversized manifest and
  pack; changed valid pack or assignment; symlink escape; bad hash; partial directory; failure
  before publication leaves no successful final artifact or cleans temporary data.
- [x] Run `uv run pytest tests/integration/test_city_run_store.py -q`; confirm RED.
- [x] Implement bounded reads and a no-clobber publication primitive proven on
  Windows and POSIX. Do not assume `os.replace()` prevents replacing a destination
  directory. Write+fsync staged files, make the final state unambiguously complete,
  and handle cleanup without deleting another writer's artifact.
- [x] Run the integration test, existing `tests/integration/test_sqlite_store.py`,
  Ruff and mypy; require PASS. Inspect every filesystem target before cleanup.
- [x] Commit the store and its failure-injection tests.

### Task 3: Create and verify saved city runs through the CLI

**Files:** Create `src/adlife/city/runs.py`, `src/adlife/cli/commands/city_run.py`,
`src/adlife/cli/commands/city_replay.py`; modify `src/adlife/cli/app.py` and
`docs/cli-reference.md`; test `tests/integration/test_city_run_replay.py`,
`tests/cli/test_city_run.py`, `tests/cli/test_city_replay.py`.

**Interfaces:** `create_city_run(pack: CityPack, *, root: Path, run_id: str, seed: int,
agent_count: int, days: int) -> StoredCityRun`; `replay_city_run(stored: StoredCityRun)
-> CityReplayResult` with `identical: bool`, verified counts and hashes. CLI JSON
mode emits exactly one document on stdout. Input refusal is code 2, duplicate is
3, corrupt/missing artifact is 4, interrupt is 130, internal defect is 1.

- [x] Write failing tests for offline create/replay, fresh-run equality, changed
  source hash detection, duplicate IDs, invalid pack and bounds, malformed run ID,
  corrupt source, interrupted creation, JSON stdout and no traceback for expected
  errors. Hash **every source file** before/after replay and assert equality.
- [x] Run `uv run pytest tests/integration/test_city_run_replay.py tests/cli/test_city_run.py tests/cli/test_city_replay.py -q`; confirm RED.
- [x] Implement orchestration and command registration through existing CLI error
  mapping; do not call a network/provider or mutate source during verification.
- [x] Run the narrow suite, `tests/cli/test_city.py`, `tests/cli/test_city_import.py`,
  `tests/cli/test_replay_safety.py`, Ruff and mypy; require PASS.
- [x] Commit only the new run/replay CLI and its documented contract.

### Task 4: Open a saved run in the read-only city viewer

**Files:** Create `src/adlife/cli/commands/city_view.py`; modify
`src/adlife/city/web.py`, `src/adlife/city/static/index.html`,
`src/adlife/city/static/app.js`, `src/adlife/cli/app.py`, `docs/city-pilot.md`,
`README.md`; test `tests/cli/test_city_view.py`, `tests/unit/city/test_city_web.py`,
`tests/packaging/test_wheel.py`.

**Interfaces:** `adlife city-view ROOT ID [--port 8765]` loads through
`CityRunStore` **before** binding loopback; `create_city_app(simulation,
*, run_id: str | None = None)` keeps the old ephemeral API behavior and adds
`run_id`/saved status to `/api/meta` only for a validated saved run. Frame endpoint
continues to call `CityMobility.frame_document` directly.

- [x] Write failing tests: saved run ID appears in metadata/UI; frame at any minute
  equals core output; corrupt run refuses before Uvicorn starts; HTTP never accepts
  a path or alternate run ID; old ephemeral viewer has no false saved-run label;
  no external assets/scripts; wheel includes updated static resources.
- [x] Run `uv run pytest tests/cli/test_city_view.py tests/unit/city/test_city_web.py -q`; confirm RED.
- [x] Implement command and minimal UI metadata, preserving safe `textContent`, CSP,
  loopback binding and existing canvas/time controls.
- [x] Run city CLI/unit/browser-equivalent tests, packaging tests and all full gates
  from the roadmap. Test a fresh temp run with `city-run`, `city-replay`, `city-view`
  (the last via API test when no interactive browser is available).
- [x] Review docs for the exact caveat: saved **mobility** run, not geographic ad
  campaign or calibrated traffic. Inspect diff and commit the viewer/docs slice.

## Completion handoff

Record: focused commits, narrow RED/green results, full gate outputs, 30×7 digest
wall time/peak memory, wheel-smoke result, source-artifact before/after hashes, CLI
examples, and remaining limitations. Check A1–A4 in the roadmap only after the
whole exit gate is demonstrated. The next work is B's licensed city catalog/pack-v2
design; do not begin geographic advertising until stable road IDs and saved-run
replay are in place.

**Execution record (2026-09-28, Windows/Python 3.12.11):** Core trace contract
`83128e5`, no-clobber store `a1f9cf1`, run/replay CLI `d5af93d`, read-only viewer
`9d378e0`, and staged-byte safety fix `d233a18`. Each behavior slice had a RED
regression before implementation and narrow/related GREEN suites afterward. A
reviewer found a short-write completion gap; three injected short-write cases
failed before the fix and passed after. High-level replay tests hash all source
files before/after, and browser/API tests prove a view does not change them.

Final full suite: `3193 passed, 12 skipped` in 418.70s. Full branch coverage:
`3193 passed, 12 skipped`, 91.40% (85% floor) in 960.90s. Ruff format/lint,
strict mypy, `uv build --no-sources`, clean-room wheel smoke, and installed-wheel
city run/replay passed. On the final checkout, the full `PYTHONHASHSEED=0` suite
passed (`3193 passed, 12 skipped` in 377.87s), as did `PYTHONHASHSEED=12345`
(`3193 passed, 12 skipped` in 393.29s). One new directory-symlink test is skipped
on this Windows account without symlink privilege; refusal is implemented and
other containment checks run.

The streaming 30-agent/7-day digest took 12.986s with tracemalloc and 224,629
bytes peak traced Python allocation; it covered 10,080 frames/302,400 positions.
The untraced complete saved-city lifecycle took 5.877s create, 3.376s load and
3.333s replay. No sparse sampling was substituted. Final wheel smoke produced a
4,862,319-byte self-contained report. A fresh external wheel installation ran
`city-run` and `city-replay` against the wheel's own fictional pack with equal
trace hashes. The artifact is a synthetic mobility trace, not a geographic
advertising result, real-population model or cryptographically signed record.
