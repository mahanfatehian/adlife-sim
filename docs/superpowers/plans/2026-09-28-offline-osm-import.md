# Offline OSM Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert bounded local Overpass JSON to a validated, attributed city pack through a safe CLI.

**Architecture:** A city adapter parses and normalizes OSM geometry into the existing immutable CityPack. A separate CLI command owns file I/O, no-clobber publication, and machine output. The core schema and city dashboard remain unchanged.

**Tech Stack:** Python 3.12, Pydantic, Typer, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-09-28-offline-osm-import-design.md`

## Global Constraints

- No default network access; no tile or API dependency.
- At most 16 MiB input and 50,000 input node elements; refuse more than 20,000 selected input segments before conversion.
- At most 4 MiB output, 10,000 pack nodes, and 20,000 pack roads.
- Preserve OSM ODbL attribution and deterministic byte output.
- Never overwrite an existing output or mutate the input.
- Keep `src/adlife/core` adapter-free.

## Review Focus

- Permuted OSM elements and road order yield identical output bytes: test in Task 1.
- Reversed one-way and implied/explicit direction yield the correct directed edges: test in Task 1.
- Disconnected networks refuse by default and opt-in extraction reports losses: test in Task 2.
- Malicious or incomplete OSM data cannot leak its body into diagnostics: test in Task 1 and CLI Task 3.
- Existing output and invalid final pack leave the file unchanged or absent: test in Task 3.

---

### Task 1: Bounded OSM conversion

**Files:**
- Create: `src/adlife/city/osm.py`
- Test: `tests/unit/city/test_osm_import.py`

**Interfaces:**
- Produces: `convert_overpass_json(data: bytes, *, city_id: str, name: str, largest_component: bool = False) -> OSMImportResult`; result has `pack: CityPack`, `dropped_nodes: int`, `dropped_roads: int`.
- Produces: `OSMImportError(ValueError)` for safe, input-free diagnostics.

- [x] Write failing tests for geometry/metadata, element permutation, direction and malformed/bounded input.
- [x] Run `uv run pytest tests/unit/city/test_osm_import.py -q`; expect feature-missing failure.
- [x] Implement parsing, road-class filtering, direction, stable IDs, strict validation, and safe errors.
- [x] Run the same tests; expect pass.
- [x] Commit this task's test and converter.

### Task 2: Explicit graph extraction

**Files:**
- Modify: `src/adlife/city/osm.py`
- Test: `tests/unit/city/test_osm_import.py`

**Interfaces:**
- Consumes Task 1's converter and result; the opt-in flag changes only graph selection and loss counts.

- [x] Add failing tests for default disconnected refusal, deterministic largest component, dropped counts, and no viable component.
- [x] Run narrow tests; expect graph-selection failures.
- [x] Implement deterministic directed SCC selection with no implicit truncation.
- [x] Run narrow tests and related city tests; expect pass.
- [x] Commit graph-selection tests and implementation.

### Task 3: CLI publication and documentation

**Files:**
- Create: `src/adlife/cli/commands/city_import.py`
- Modify: `src/adlife/cli/app.py`, `docs/city-pilot.md`, `docs/cli-reference.md`, `README.md`
- Test: `tests/cli/test_city_import.py`

**Interfaces:**
- Consumes Task 1/2 converter, canonical serialization, and existing CLI error/output contracts.
- Produces: `adlife city-import INPUT --output PACK --city-id ID --name NAME [--largest-component]`.

- [x] Add failing CLI tests for valid publication, JSON output, no-clobber, malformed input and no partial file.
- [x] Run `uv run pytest tests/cli/test_city_import.py -q`; expect feature-missing failure.
- [x] Implement bounded read, atomic no-clobber write, command registration, and docs.
- [x] Run city CLI/unit tests; expect pass.
- [x] Run Ruff, mypy, full pytest, hash-seed test, build and wheel smoke; report exact outcomes.
- [x] Commit final CLI, tests and docs.

Tasks 1 and 2 were committed together because their converter and graph tests share
one focused module; Task 3 is a separate CLI/documentation commit.
