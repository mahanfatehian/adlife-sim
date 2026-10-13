# City Persona Authoring Design

**Status:** Approved for implementation planning on 2026-10-13
**Date:** 2026-10-12
**Scope:** Fictional, per-person identity and response assumptions in the local city workbench

## Intent

The city workbench currently expands one homogeneous cohort across every simulated agent. This design adds a compact audience editor so an operator can give fictional people distinct names, context, interests, response traits, and initial campaign state while preserving the simulator's strongest scientific boundary:

> Descriptive identity fields, including MBTI, do not control mobility, attention, purchase, or any other numeric outcome.

The feature is for authored synthetic scenarios, demonstrations, and sensitivity work. It does not model real residents, infer psychology, or validate MBTI as a scientific predictor.

## Current constraints

- Workbench draft/input schema 1 stores one shared cohort and supports 1–30 agents over 1–7 days.
- `SpatialResponseInput` schema 1 already supports heterogeneous profiles and initial state per agent. No response equation needs to change.
- City mobility agents contain only stable IDs and home/work/leisure nodes. Their schedules are common synthetic schedules, not occupation- or personality-derived behavior.
- City run manifest V7 binds workbench input schema 1. Its binding deliberately requires identical profiles and state across agents.
- The legacy zone `PersonProfile` has a different trait model and must not be reused as a city persona.
- Workbench requests and saved inputs remain bounded to 131,072 bytes.

## Chosen approach

Add workbench draft/input schema 2 with a small set of persona definitions and explicit counts. Materialize those definitions into the existing canonical `person-001` through `person-NNN` IDs before a job is accepted. Freeze both persona definitions and the materialized assignment in `inputs/workbench.json`, then compile only causal interests, traits, and state into the existing `inputs/spatial-response.json`.

This is preferred over one large free-form biography per individual because it gives the UI a manageable audience editor, keeps request size bounded, and makes the persona-to-agent mapping inspectable. It is preferred over extending `SpatialResponseProfile` because descriptive metadata would then alter its fingerprint and therefore scientific event identifiers even when no causal assumption changed.

## Data model

### Persona definition

`WorkbenchPersonaDraftV2` is frozen, strict, and extra-forbidden. It contains:

| Field | Contract | Scientific role |
|---|---|---|
| `persona_id` | editor-assigned `persona-NNN` ID, unique and immutable after creation | structural assignment key |
| `count` | strict integer, 1–30 | number of assigned agents |
| `fictional` | literal `true` | mandatory disclosure |
| `display_name` | 1–80 characters | presentation only |
| `age` | optional strict integer, 18–65 | descriptive context only |
| `occupation` | optional bounded label, 1–80 characters | descriptive context only |
| `background` | optional fictional biography/psychology text, at most 800 characters | descriptive context only |
| `mbti` | `null` or one of the 16 uppercase labels | descriptive context only |
| `interests` | 1–12 unique strings, each 1–80 characters | explicit response-model input |
| `traits` | existing six `SpatialResponseTraits`, each finite in [0, 1] | explicit response-model input |
| `initial_state` | existing sentiment, recall, and intention bounds | explicit response-model input |

The 16 accepted MBTI labels are `INTJ`, `INTP`, `ENTJ`, `ENTP`, `INFJ`, `INFP`, `ENFJ`, `ENFP`, `ISTJ`, `ISFJ`, `ESTJ`, `ESFJ`, `ISTP`, `ISFP`, `ESTP`, and `ESFP`. The UI labels this field: “Descriptive scenario context; not empirically validated and not a rule input.”

No field may contain line-breaking control characters, bidi override controls, credentials, authorization-header material, phone numbers, email addresses, national identifiers, or other content caught by the repository's sensitive-text screens. These screens are a refusal boundary, not a claim that arbitrary prose is proven free of personal information.

Each canonical persona object is at most 3,072 UTF-8 bytes, and the complete canonical population object is at most 98,304 UTF-8 bytes. These aggregate byte limits apply in addition to field-character limits, so a collection of individually valid high-byte Unicode strings can still be refused with an adjacent size error. The complete workbench request retains its independent 131,072-byte ceiling.

### Population and assignment

`WorkbenchPopulationDraftV2` contains one to 30 persona definitions. The sum of all `count` values must equal `settings.agent_count` exactly.

The browser editor allocates the smallest unused positive three-digit `persona-NNN` ID when a persona is added or duplicated, stores it in draft state, and submits that same ID to both validation and job creation. Bundled templates carry fixed IDs. The server validates submitted IDs but never generates, renumbers, or replaces them during validation or submission. The normal editor does not expose ID renaming. Canonicalization sorts definitions by that ID, canonicalizes each interest set, and assigns contiguous agent IDs in that order. For example, counts of two for `persona-001` followed by one for `persona-002` produce:

```text
person-001 -> persona-001
person-002 -> persona-001
person-003 -> persona-002
```

The normalized input freezes an explicit `{agent_id, persona_id}` assignment record for every agent. UI ordering, locale, dictionary ordering, and `PYTHONHASHSEED` therefore cannot change who receives which assumptions. `persona_id`, `count`, and the materialized assignment are structural causal configuration: changing one can move a causal profile onto a different mobility agent and is not covered by descriptive-field invariance.

### Workbench schema 2

`WorkbenchRunDraftV2` keeps the current `city_id`, `scenario`, and `settings` contracts, replaces `cohort` with `population`, and uses exact integer `schema_version: 2` dispatch. Booleans are not accepted as integers.

`NormalizedWorkbenchRunDraftV2.population` is the one authoritative location for canonical persona definitions and the complete assignment tuple. `WorkbenchRunInputV2` freezes that normalized schema-2 draft plus the selected `CreativeTemplate` snapshot. Creative content and persona definitions are not duplicated in competing sidecars or sibling fields.

Schema 1 remains readable and keeps its current homogeneous-cohort rules. Version-specific code paths must not weaken schema-1 binding to make schema 2 fit.

### Run manifest V8

Persona-authored runs use `CityRunManifestV8`, a sibling built from the common response-manifest base with the V7 scientific receipt fields plus `workbench_input_schema_version: 2` and the canonical hash of the complete schema-2 workbench input. It does not inherit V7's validator that requires input schema 1. Direct CLI city runs without workbench provenance retain their existing manifest versions.

All explicit manifest-version families must dispatch V8 intentionally, including run preparation/loading, artifact verification, web DTOs, analysis, response projections, studies, reports, and CLI readers. Unknown versions remain a clean refusal.

## Causal boundary

The following fields may affect numeric response behavior because the operator supplies them explicitly as assumptions:

- interests;
- the six response traits;
- initial sentiment, recall, and purchase intention.

The following descriptive fields must never alter random keys, mobility, notice opportunities, attention, response equations, purchases, budgets, or metrics:

- display name;
- age;
- occupation;
- background;
- MBTI.

The implementation must not derive a trait or initial state from MBTI or demographic prose. Changing descriptive fields changes the workbench provenance hash but leaves the compiled spatial-response input, scientific event stream, and metrics byte-equivalent after normalization for run identity and wall-clock metadata.

## Validation and compilation flow

1. The browser constructs a bounded schema-2 draft.
2. `POST /api/scenarios/validate` parses strict JSON, rejects duplicate members and non-finite values, validates persona text/IDs/counts, materializes the assignment, and returns a safe preview plus normalized-input fingerprint. It creates no files and reserves no run ID.
3. `POST /api/jobs` repeats the same construction synchronously from the same submitted IDs before accepting a job. Validation is never trusted from an earlier preview, and an unchanged draft must reproduce the preview fingerprint.
4. The constructor builds the existing mobility input, scenario, and heterogeneous `SpatialResponseInput` from the materialized assignment.
5. The run store stages and atomically publishes the run without overwriting an existing run.
6. Independent loading re-parses, hashes, and cross-binds all frozen inputs before the run is exposed.

The compiler verifies exact coverage of `person-001` through `person-NNN`, exactly one assignment per agent, complete agent×campaign initial state, and equality between every compiled profile/state and its assigned persona's explicit causal fields.

## Artifact and replay contract

- `inputs/workbench.json` is the authoritative frozen persona authoring record.
- `inputs/spatial-response.json` remains the authoritative causal response input.
- `inputs/agents.json` remains the mobility-anchor contract and does not absorb biography fields.
- The manifest hashes all declared scientific inputs and refuses missing, undeclared scientific sidecars, noncanonical, oversized, symlinked, or changed artifacts. This requirement does not create a generic allowlist for unrelated operator files outside the run contract.
- A recomputed workbench hash is insufficient by itself: loading independently cross-binds persona assignments to agent IDs and causal response inputs.
- Replay uses only frozen inputs. Removing or changing a later built-in persona template cannot change an existing run.
- Source artifacts are read-only during replay verification.

Paired-study compatibility compares the materialized causal profile/state assumptions, not the whole workbench hash. Different run IDs and descriptive metadata are harmless; changed interests, traits, or state invalidate a claim that the population assumptions are matched.

## User interface

The existing CITY → CAMPAIGN → RUN → INSPECT flow gains an Audience section rather than a separate persona application.

The editor provides:

- a concise persona list showing name, count, optional MBTI, and count total;
- add, duplicate, and remove actions that never silently alter another persona;
- an edit panel for fictional background, interests, traits, and initial state;
- adjacent field errors and a clear mismatch when counts do not equal population size;
- deterministic preview of assigned `person-NNN` IDs;
- a small set of bundled fictional templates as editable starting points;
- an always-visible disclosure that identity prose and MBTI are descriptive, while the displayed numeric traits/state are causal assumptions.

The saved-run inspector resolves identity only from verified frozen workbench input. Agent rows show the fictional name alongside immutable agent ID. Descriptive context is collapsed by default, while the exact causal traits/state and event timeline remain available for technical inspection.

No UI control may imply that occupation or MBTI creates a commute, that these people are sampled residents, or that a persona represents a real individual.

## Security and privacy

- Only fictional identities are accepted; `fictional` cannot be set false.
- No real addresses, contact details, government identifiers, photos, trajectories, personal-data imports, or observed profiles are accepted.
- Public-text, sensitive-persona, provider-secret, and structural secret-member screening applies to every persona string and nested value before persistence.
- Validation errors are bounded and never echo full submitted biographies or secret-like text.
- The current loopback-only bind, same-origin write checks, restrictive CSP, no CORS, safe DOM text insertion, bidi isolation, and no external browser assets remain unchanged.
- The 3,072-byte per-persona, 98,304-byte population, and 131,072-byte complete workbench ceilings are enforced independently with bounded value-free errors.
- Paths and filenames are never accepted in persona input.

## Compatibility

- Workbench schema 1 and manifest V7 keep byte-for-byte semantics and their current homogeneous binding.
- Direct response-enabled manifest V6 remains readable.
- Browser receipt and response-source validation becomes explicit version dispatch for supported V6/V7/V8 combinations; it does not accept arbitrary future integers.
- Existing reports and studies render V8 intentionally. Paired studies compare the materialized causal population assumptions described above; no consumer silently drops persona provenance.
- The response model identifier does not change because its equations do not change.

## Failure behavior

- Invalid persona input fails before job reservation or filesystem mutation.
- Duplicate persona IDs, invalid counts, incomplete assignments, unknown fields, invalid MBTI, and unsupported versions are input errors.
- A mismatch discovered while loading a saved run is an artifact-integrity error, not an automatic repair.
- Job cancellation and worker failure retain the current no-clobber staging behavior.
- UI state is not evidence; every accepted run is reconstructed and validated server-side.

## Required tests

### Model and constructor

- Strict schema, type, range, uniqueness, count-sum, text, per-persona/population/request byte size, NaN/Infinity, boolean, and version-confusion cases.
- One- and 30-agent boundaries, duplicate IDs, missing/extra assignments, and incomplete initial-state coverage.
- Permuted definitions and interests produce the same canonical materialization.
- Distinct personas compile into the correct existing profiles and initial state.
- Changing only name, age, occupation, background, or MBTI leaves causal inputs and normalized scientific output unchanged. Renaming an assignment ID is refused by the normal editor and explicit raw-ID changes are tested as structural remapping, not descriptive invariance.
- Changing one explicit causal trait changes only the documented response path; mobility and synthetic attention remain unchanged.

### Artifacts and replay

- V8 canonical round trip and read-only replay.
- Independent cross-binding catches rehashed but scientifically inconsistent workbench content.
- Missing, changed, oversized, symlinked, noncanonical, undeclared scientific sidecars, and unsupported-version artifacts refuse cleanly.
- Legacy V1/V7 and direct V6 fixtures retain behavior.
- Replay remains identical after persona-template removal or modification.

### API and browser

- Validation is side-effect free; job submission revalidates.
- Count editing, add/duplicate/remove, keyboard focus, 390-pixel containment, and stale-response protection.
- Persian and bidi-safe rendering, malicious strings, CSP, no external requests, and no secret reflection.
- Saved inspector reopens from frozen input and visibly distinguishes descriptive context from causal assumptions.

### Repository gates

- Core architecture import boundary.
- Relevant unit, integration, browser, security, study, replay, and packaging suites.
- Ruff format/lint, strict mypy, full pytest under multiple hash seeds, branch coverage, wheel smoke, and offline demo gates required by the repository.

## Explicit non-goals

- Real-person ingestion or identity matching.
- Claims that MBTI predicts advertising response.
- Demographic calibration or census representativeness.
- Automatic trait inference from biography, occupation, age, or MBTI.
- Changes to the mobility schedule or response equations.
- Provider configuration or provider-generated persona creation; those require separate reviewed designs.

## Implementation order after approval

1. Add schema-2 models, canonical materialization, and red/green model tests.
2. Add V8 frozen-input persistence, independent binding, and legacy compatibility tests.
3. Extend web DTO/API version dispatch and saved-run identity projections.
4. Build the audience editor and inspector presentation with browser tests.
5. Run security, determinism, replay, study, packaging, and full repository gates.
