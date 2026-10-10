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

- [ ] Add a browser test that traverses all busy-minute opportunity, attention, and response pages
  and compares canonical IDs/order/uniqueness with the source evaluations.
- [ ] Assert exact ranges and terminal button states for 150, 222, and 144 records.
- [ ] Assert Previous restores the first page and buttons work from the keyboard.
- [ ] Assert minute scrubbing resets all offsets and leaves no prior-minute cards.
- [ ] Confirm focused tests fail because pagination controls and later pages are absent.

### Task 2: Implement one bounded pager

**Files:**
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Test: `tests/browser/test_city_places_browser.py`

- [ ] Add semantic Previous/Next controls and a compact status to all three evidence rails.
- [ ] Add strict shared page-envelope validation and exact range rendering.
- [ ] Add independent rail request generations guarded by the current timeline generation.
- [ ] Preserve offsets on same-minute selection, reset on minute changes, and stop playback when
  paging.
- [ ] Keep the last committed page on HTTP or validation failure and expose a concise diagnostic.
- [ ] Run focused browser tests and confirm GREEN.

### Task 3: Harden asynchronous and narrow-screen behavior

**Files:**
- Modify: `tests/browser/test_city_places_browser.py`
- Modify as required: `src/adlife/city/static/app.js`, `src/adlife/city/static/app.css`

- [ ] Add delayed-response tests proving an old page cannot overwrite a newer minute or request.
- [ ] Add malformed offset/`next_offset` tests proving refusal without fabricated zero evidence.
- [ ] Assert controls, status, and focus remain contained at 390 pixels; keep large lists out of
  live regions.
- [ ] Assert no external requests and no mutation of source simulation/evaluation objects.
- [ ] Run the complete headless browser file.

### Task 4: Reconcile and verify

**Files:**
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: this plan with exact evidence.

- [ ] Document bounded immutable pagination and its presentation-only scope.
- [ ] Record incremental D1/D2/D4 progress without closing those parent gates.
- [ ] Run browser, city-web, documentation, security, formatting, lint, strict typing, and full
  repository gates appropriate to the final diff.
- [ ] Request independent review and resolve all Critical or Important findings test-first.
- [ ] Commit the logical slice with the configured identity and attempt the existing authorized
  push without interactive desktop authentication.
