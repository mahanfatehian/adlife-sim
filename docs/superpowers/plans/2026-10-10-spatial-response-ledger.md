# Saved-Run Spatial Response Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present the existing verified schema-v6 response-metric projection in the read-only saved-run browser with receipt-level provenance and honest scientific labels.

**Architecture:** Keep simulation, artifacts, and the FastAPI contract unchanged. The vanilla browser fetches the existing immutable response-metrics endpoint once at startup, strictly validates its public shape, and renders separate direct-rule and committed-state ledgers using safe DOM text insertion.

**Tech Stack:** Python/FastAPI contract fixtures, vanilla HTML/CSS/JavaScript, Playwright Chromium, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-10-spatial-response-ledger-design.md`

## Global Constraints

- Keep `src/adlife/core` adapter-free and do not change simulation decisions or artifact schemas.
- Bind only to `spatial-response-metrics-v1` with claim scope `synthetic-response-metrics-not-observed-outcomes`.
- Keep the server loopback-only, read-only, fixed-route, offline, and independent of provider credentials.
- Use DOM text properties only; do not use `innerHTML` or add external resources.
- Never allocate committed state by channel or describe a synthetic proxy as observed behavior, purchase probability, transactions, sales, or forecast accuracy.
- Follow RED-GREEN TDD, run browser tests headlessly, and preserve existing CLI/TUI/report paths.

## Review Focus

- A malformed or failed response-metrics request must show no fabricated zero evidence.
- A campaign with zero direct responses must remain visible with valid zero receipts and unchanged state.
- Timeline scrubbing and agent selection must not alter the full-run ledger.
- Hostile-looking campaign labels must remain inert text and create no network request.
- The 390-pixel layout must contain wide receipt tables without widening the document.

---

### Task 1: Pin the browser evidence contract

**Files:**
- Modify: `tests/browser/test_city_places_browser.py`

**Interfaces:**
- Consumes: `derive_spatial_response_metrics(...) -> SpatialResponseMetrics` and `create_city_app(..., spatial_response_metrics=...)`.
- Produces: browser assertions for visibility, exact receipts, provenance, immutable scrubbing, safe labels, accessibility, and narrow containment.

- [ ] **Step 1: Extend the schema-v6 response viewer fixture.** Derive the real response metrics from the same verified scenario, opportunity, attention, response input, and response evaluation, then pass them to `create_city_app`.
- [ ] **Step 2: Write failing full-run ledger assertions.** Assert exact overall/channel values and receipt labels, a zero-response campaign, selected campaign state, honest claim copy, and no channel state table.
- [ ] **Step 3: Write failing interaction/security assertions.** Scrub the timeline and select another agent without changing ledger values; exercise keyboard focus and campaign selection; assert hostile labels remain text, 390-pixel containment, no console/page errors, and no external requests.
- [ ] **Step 4: Run the focused browser test and confirm RED.** Run `uv run --offline pytest tests/browser/test_city_places_browser.py -q -k response_viewer`; expected failure is missing response-ledger DOM/behavior, not fixture construction.

### Task 2: Render the immutable response ledger

**Files:**
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Test: `tests/browser/test_city_places_browser.py`

**Interfaces:**
- Consumes: `GET /api/meta` flag `spatial_response_metrics` and `GET /api/spatial-response-metrics`.
- Produces: `renderSpatialResponseMetrics()` and a campaign-selection render path over one immutable in-memory projection.

- [ ] **Step 1: Add semantic hidden-by-default markup.** Add a keyboard-focusable full-run panel, direct-rule receipt table, bounded native campaign selector, committed-state receipt table, and explicit limitations/disclosures.
- [ ] **Step 2: Add strict response-metric parsing and safe rendering.** Fetch once only when advertised; require exact model/scope and bounded canonical series; reject malformed/non-finite receipts; set text and accessibility attributes without HTML insertion.
- [ ] **Step 3: Preserve presentation-only controls.** Re-render only the chosen campaign state series; do not connect the ledger to minute/agent state or mutate fetched data.
- [ ] **Step 4: Add focused styling.** Extend the existing forensic-console visual language with coral direct-response and blue committed-state accents, visible focus, readable data type, contained table scrolling, reduced-motion compatibility, and 390-pixel layout rules.
- [ ] **Step 5: Run the focused browser tests and confirm GREEN.** Run `uv run --offline pytest tests/browser/test_city_places_browser.py -q -k response_viewer`.

### Task 3: Reconcile and verify the public contract

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: this plan with exact completion evidence.

**Interfaces:**
- Consumes: the completed browser behavior from Task 2.
- Produces: truthful documentation that records incremental D3/D4 progress while keeping the phase-D exit gate open.

- [ ] **Step 1: Add executable documentation assertions if current contract tests do not cover the new public wording.** Pin the endpoint/panel scope and the uncalibrated-proxy/channel-attribution limitations.
- [ ] **Step 2: Reconcile public documentation.** Document the saved-run ledger and keep workbench jobs, writes, authentication/provider settings, purchase/social behavior, calibration, and external validity explicitly open.
- [ ] **Step 3: Run related verification.** Run browser, city-web, documentation, security, architecture, Ruff format/lint, and strict mypy checks.
- [ ] **Step 4: Request independent review.** Fix every Critical or Important finding test-first and rerun affected checks.
- [ ] **Step 5: Run the final repository gates appropriate to the accumulated changes.** Record exact outputs; do not claim unexecuted platform-specific evidence.
- [ ] **Step 6: Commit and push the logical slice.** Use the configured repository identity and existing remote only; do not publish or create a release.
