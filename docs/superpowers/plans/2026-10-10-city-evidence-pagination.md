# Saved-Run Evidence Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every persisted current-minute opportunity, attention, and response record
inspectable in the read-only saved-run browser through bounded, accessible pagination.

**Architecture:** Reuse the existing immutable FastAPI offset/limit endpoints. Add one generic
vanilla-JavaScript pager controller with independent request generations, strict response-envelope
validation, and per-rail rendering callbacks. Do not change core, storage, artifacts, schemas, or
simulation behavior.

**Tech stack:** Existing FastAPI API, vanilla HTML/CSS/JavaScript, Playwright Chromium, pytest,
Ruff, and mypy.

**Spec:** `docs/superpowers/specs/2026-10-10-city-evidence-pagination-design.md`

## Constraints

- Use explicit `offset` and `limit=100`; keep at most 100 cards per rail in the DOM.
- Preserve canonical server order and complete-minute summary counts.
- Reset offsets on minute changes; preserve them on same-minute agent selection.
- Stop playback on deliberate page navigation.
- Refuse malformed pages and retain the last committed page on failure.
- Ignore stale page responses after a newer timeline or same-rail request.
- Use safe DOM text insertion, fixed same-origin GET paths, and no external resources.
- Do not add final-state pagination, writes, authentication, jobs, or provider configuration.

### Task 1: Pin reachable-evidence behavior

**Files:**
- Modify: `tests/browser/test_city_places_browser.py`

- [x] Add a browser test that traverses all busy-minute opportunity, attention, and response pages
  and compares canonical IDs/order/uniqueness with the source evaluations.
- [x] Assert exact ranges and terminal button states for 150, 222, and 144 records.
- [x] Assert Previous restores the first page and buttons work from the keyboard.
- [x] Assert minute scrubbing resets all offsets and leaves no prior-minute cards.
- [x] Confirm focused tests fail because pagination controls and later pages are absent.

### Task 2: Implement one bounded pager

**Files:**
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Test: `tests/browser/test_city_places_browser.py`

- [x] Add semantic Previous/Next controls and a compact status to all three evidence rails.
- [x] Add strict shared page-envelope validation and exact range rendering.
- [x] Add independent rail request generations guarded by the current timeline generation.
- [x] Preserve offsets on same-minute selection, reset on minute changes, and stop playback when
  paging.
- [x] Keep the last committed page on HTTP or validation failure and expose a concise diagnostic.
- [x] Run focused browser tests and confirm GREEN.

### Task 3: Harden asynchronous and narrow-screen behavior

**Files:**
- Modify: `tests/browser/test_city_places_browser.py`
- Modify as required: `src/adlife/city/static/app.js`, `src/adlife/city/static/app.css`

- [x] Add delayed-response tests proving an old page cannot overwrite a newer minute or request.
- [x] Add malformed offset/`next_offset` tests proving refusal without fabricated zero evidence.
- [x] Assert controls, status, and focus remain contained at 390 pixels; keep large lists out of
  live regions.
- [x] Assert no external requests and no mutation of source simulation/evaluation objects.
- [x] Run the complete headless browser file.

### Task 4: Reconcile and verify

**Files:**
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: this plan with exact evidence.

- [x] Document bounded immutable pagination and its presentation-only scope.
- [x] Record incremental D1/D2/D4 progress without closing those parent gates.
- [x] Run browser, city-web, documentation, security, formatting, lint, strict typing, and full
  repository gates appropriate to the final diff.
- [x] Request independent review and resolve all Critical or Important findings test-first.
- [x] Commit the logical slice with the configured identity and attempt the existing authorized
  push without interactive desktop authentication.

## Completion evidence

- The initial browser regression failed because every evidence rail exposed only its first 100
  records and had no navigation controls.
- The busy-minute fixture now proves complete canonical traversal of 150 opportunities, 222
  attention evaluations, and 144 response evaluations with no duplicate or missing record IDs.
- Independent adversarial review exposed incomplete item and aggregate-map validation. Fourteen
  focused cases failed before the fix; validation now checks exact shapes, references, hashes,
  enums, binary64 bounds, time coherence, count-map keys, and reconciled totals before commit.
- The final browser file passed with `52 passed in 557.38s`; the independent adversarial subset
  passed with `19 passed, 32 deselected in 78.48s`.
- A late cross-slice review reproduced a transactional mismatch when selection or scrubbing
  triggered a failed coordinated refresh. The new regression failed before the fix and now proves
  person rows, all three evidence-card kinds, the timeline slider, details, and evidence remain on
  the last fully committed state after HTTP or render failure.
- The combined city web, CLI, release-workflow, documentation, architecture, integration-security,
  security, and installed-wheel suites passed with `1088 passed, 2 skipped in 216.30s`.
- Ruff formatting and lint, strict mypy over 133 source files, JavaScript syntax checking, and
  `git diff --check` passed before the final repository run. The complete suite passed with
  `4675 passed, 29 skipped in 1989.57s` under the repository's documented 60-second Windows CI
  performance allowance.
- The explicit 30-agent, seven-day benchmark passed in `135.13s` total test time: its untraced run
  took `37.04s`, produced 5,685 events, used 2 MiB peak traced memory, and wrote a 3,629,056-byte
  database plus a 4,862,947-byte self-contained report.
- `uv build --no-sources` rebuilt the exact wheel and sdist. The exact wheel clean-room smoke then
  verified schema-v6 API/UI, catalog metrics, A/A comparison, replay, repeated-seed study, and
  reports without network access or source-checkout dependence.
- Two independent final reviews reported no remaining Critical or Important findings in the
  settled pagination/validation and release-provenance slices.
- The final implementation was committed under the configured repository identity in focused
  city, release, and packaging commits. A non-interactive push to the unchanged `origin` was
  attempted with terminal prompts disabled and was refused because this background session had no
  usable HTTPS credential; no authentication window was opened and no remote was changed.
