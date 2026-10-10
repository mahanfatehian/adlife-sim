# Production City Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a **program roadmap**, not authorization to claim later phases shipped. Write a focused executable plan for each later subsystem before coding it.

**Goal:** Evolve the existing CLI research instrument and city mobility pilot into a secure, reproducible geographic simulation workbench, then earn any external-validity claims with evidence.

**Architecture:** Preserve the adapter-free deterministic core and existing offline CLI. Add versioned city-run artifacts first, then licensed/scalable city data, geographic campaign events, a FastAPI/vanilla workbench, protected identity/provider settings, validation and operational controls. A remote deployment is gated separately from a local demo.

**Tech Stack:** Existing Python 3.11–3.13, uv, Pydantic, SQLite, FastAPI, Uvicorn, vanilla HTML/CSS/JavaScript, pytest/Hypothesis, Ruff and mypy. Additional services or map libraries require a benchmark, rights review and a focused design decision; none is a default requirement of this plan.

**Spec:** [Production-track city platform design](../specs/2026-09-28-production-city-platform-design.md)

## Global Constraints

- Work on `main`; do not change `defense-ready`. Preserve current zone-engine artifacts and CLI contracts.
- Core imports no FastAPI, Typer, Textual, SQLite, Plotly or provider adapter.
- Offline rules/mock demonstrations and tests require no network, tiles, account or API key.
- No real-person data, personal trajectories, invented demographic calibration or unlicensed map data.
- Same frozen inputs, seed, model version and recorded cognition produce the same normalized run; fresh remote-provider output is not promised deterministic.
- Every behavior change follows red test, narrow green test, related suites and full gates; never regenerate goldens merely to pass.
- Never put provider secrets in prompts, artifacts, browser storage, URLs, logs or reports. Never let a browser endpoint accept an arbitrary server path.
- Do not expose a write-capable web server beyond loopback before the identity, authorization and security gates below pass.
- Use local configured Git identity if committing. Do not add a remote, push, publish or alter global Git config as a side effect of following this roadmap.

## Review Focus

- A city pack or catalog entry disappears/changes after a run: replay must use frozen bytes or refuse, never silently use the new version (A1–A4, B4).
- A browser asks for another user's run or provider settings: deny without revealing existence or a secret (E1–E2).
- A road extract omits a one-way/turn/access rule: importer must report unsupported semantics or a documented approximation, never present a legal route (B2–B3).
- An advertising placement is off the driven road or outside its window: no billboard impression; the UI cannot invent one (C1–C3, D2).
- A crash splits persistence and notification: observers only receive committed ticks; repair/refusal is typed and auditable (A2–A4, G3).

---

## How to use this roadmap

Each package below is an independently reviewable deliverable. `Depends on` is a hard
prerequisite, not merely suggested order. Run the required verification for each package,
record results in its focused plan, and commit only after the narrow and relevant tests
pass. Do **not** check a box because a prototype screen looks right. Later packages
deliberately name acceptance behavior but leave implementation signatures to a new
dated subsystem spec/plan, written after predecessor interfaces are stable. The
[first executable plan](2026-09-28-persisted-city-runs.md) locks the near-term files,
interfaces, tests and commands.

The current baseline includes the original zone campaign engine, city mobility pilot,
offline city importer, and local FastAPI viewer. Package A adds saved **mobility**
traces and replay, not geographic advertising runs. See
[city pilot](../../city-pilot.md) and
[architecture](../../architecture.md) for exact shipped behavior. At the start of
each execution session, inspect `git status --short --branch`, current tests, code
and this document; historical plans are not proof of current behavior.

## Dependency path and shipment gates

```text
Baseline (shipped on main)
    A. Durable city mobility runs/replay
    B. Licensed city catalog and versioned larger packs  ─┐
    C. Geographic campaign engine (requires A + B)       ├─ D. Analyst workbench
    E. Identity/provider security (required before remote writes or shared access)
    F. Calibration and external validation (separate claim gate)
    G. Deployment, reliability, performance and release evidence (requires C–E)
```

`A` can run on existing small packs. `B` can progress in parallel after its data-rights
decision. `D` can ship a **local read-only** incremental viewer earlier, but not a
shared write-capable service. `F` is not an automatic consequence of `G`. Worldwide
selection means an extensible catalog with independently qualified packs, not a
promise that every city is already modeled.

## A. Persist and replay city mobility runs — next vertical slice

**Entry:** Existing `CityPack`, `CityMobility`, local viewer and city tests pass.
**Detailed execution:** [persisted-city-runs plan](2026-09-28-persisted-city-runs.md).

- [x] **A1 — Version the city-run contract.** Freeze exact city bytes/hash, synthetic
  assignments, seed, days, model/parameter identity and output schema. Add strict
  cross-reference and size validation. Tests reject changed/missing packs, invalid
  assignments and unknown versions. Do not reuse the zone `Scenario` dishonestly.
- [x] **A2 — Add atomic city-run storage.** Publish a new run without clobbering,
  store a canonical trace/event digest and explicit status, and detect corruption.
  Fault-injection tests cover interrupted/partial publication, short write,
  changed frozen inputs/trace evidence, symlink escape and failed publication;
  this format has no separate city event export to mismatch. Choose streaming
  evidence rather than persisting every one-minute frame for 250 agents × 31 days.
- [x] **A3 — Add CLI run and replay commands.** Keep `adlife city` as an ephemeral
  viewer; a new command creates an artifact and another verifies it without writing
  to the source. Test exact exit-code and JSON-stdout behavior, duplicate IDs,
  corrupt/missing artifacts and source hashes before/after replay.
- [x] **A4 — Open a saved run in the local API/UI.** The viewer reads a validated
  city-run artifact at startup; no HTTP path parameter chooses server files.
  Scrubbing any minute uses the frozen run and shows its run ID/version. Headless
  and browser views agree on core frames. Browser failures do not change a run.

**Exit gate:** Two fresh runs with identical inputs have identical normalized trace
evidence; a replay reports equality and does not mutate source; altered artifacts
refuse; old `adlife city` and zone replay still pass.

**Package A verification (2026-09-28, Windows/Python 3.12.11):** Final full suite
`3193 passed, 12 skipped`; branch coverage `91.40%` (85% floor); Ruff format/lint
and strict mypy passed. On the final checkout, the full `PYTHONHASHSEED=0` suite
passed (`3193 passed, 12 skipped` in 377.87s), and the full `PYTHONHASHSEED=12345`
suite passed (`3193 passed, 12 skipped` in 393.29s). Final wheel built and passed
clean-room smoke; installed city-run/city-replay reproduced a packaged fictional
pack outside the checkout. Headless browser checks confirmed saved identity and
minute scrub at desktop width and identity visibility at 700px. The 30-agent/7-day
lifecycle on the bundled pack completed in 5.877s create, 3.376s load/verify and
3.333s replay.
One symlink regression test is skipped on this Windows account because it lacks
directory-symlink privilege; production code still rejects symlink targets and
existing path-boundary tests pass.

## B. Licensed, scalable city data and selection

**Depends on:** A1 schema contract for run pinning; rights review before distribution.

**Foundation progress (2026-09-29):** City-pack v2, geometry-aware deterministic
routing, v2 frozen-run replay, bounded local v2 OSM ingestion, and an offline
content-addressed catalog are implemented. The catalog deliberately contains only the
`fictional-grid-v2` fixture. B1–B4 remain unchecked because no real-city data-rights
decision, rights-reviewed pack, or representative permitted-extract benchmark exists;
the implemented technical foundation is not evidence that those human and empirical
gates have passed. See the
[focused implementation plan](2026-09-29-city-pack-v2-catalog-foundation.md) and
[qualification checklist](../../data/city-source-qualification.md).

- [ ] **B1 — Record the data-product decision.** Document intended jurisdictions,
  city selection criteria, data suppliers, permitted commercial use, ODbL obligations,
  redistribution and retention terms, tile/geocoder service plan, and legal reviewer.
  Test/CI fixtures stay fictional or permitted. No OSMF public tile download or
  Nominatim autocomplete is built into production. Evidence: a reviewed provenance
  manifest and demonstrable no-network offline path.
- [ ] **B2 — Design city-pack v2, not a silent v1 limit raise.** Represent geometry,
  stable road identifiers, directed connectivity, time zone, source/version,
  omissions and optional rule classes. Benchmark memory and routing on a real
  permitted extract before choosing format/index. Tests cover intersection identity,
  directed routes, spatial bounds, malformed coordinates, dateline/polar cases and
  deterministic import. v1 remains readable.
- [ ] **B3 — Build bounded ingestion and QA.** Accept a documented local/licensed
  extract format, validate completeness/access/turn semantics, generate a quality
  report (coverage, disconnected roads, dropped segments, unsupported rules), and
  refuse outputs beyond explicit limits. Make conversion resumable only if the
  intermediate is verifiable. Tests fuzz malformed/oversized inputs and check
  byte-stable output independent of source order.
- [ ] **B4 — Build a curated catalog.** Catalog entries pin immutable pack hashes,
  rights, source date, coverage, timezone and attribution. Implement local search
  and selection without arbitrary URLs. Tests cover missing/renamed/updated packs,
  duplicate IDs, stale versions, permissions and offline city choice. Start with a
  small set of qualified cities; add more through the same pipeline.
- [ ] **B5 — Choose and integrate basemap rendering.** Keep the existing canvas as
  offline fallback; evaluate self-hosted/licensed vector tiles and a vanilla-JS
  map renderer on a representative pack. Attribution is always visible. Test offline
  fallback, tile failure, large geometry, browser accessibility and no unlicensed
  resource requests. Map styling is presentation only.
- [x] **B6 — Define synthetic place assignment.** Add validated home, workplace and
  leisure zone/point sets from licensed land-use data or operator-authored fictional
  scenario inputs. Allow an analyst to choose or constrain spawn/home/work/hangout
  areas without importing real addresses. Assign agents with keyed draws and prove
  every itinerary remains routable; refuse insufficient or unreachable zones.
  Document whether each place was sourced, inferred or authored, and test that
  input order does not change assignments.

  **Evidence (2026-09-30):** schema-v1 place sets are city-ID/hash bound, strictly
  validated and provenance-explicit; keyed canonical assignment proves order invariance,
  home capacity and directed routability. Manifest v3 freezes place inputs and assignments,
  replay rejects edits, the read-only viewer exposes their labels/provenance, and the
  installed-wheel smoke exercises validation, run and replay without network access.

**Exit gate:** A qualified catalog entry can be selected, pinned, inspected and
replayed even if the catalog later updates; data/license/coverage are visible;
unsupported routing semantics are disclosed, never marketed as navigation.

## C. Geographic campaign simulation

**Depends on:** A durable city run and B's stable road/geometry identifiers.

- [x] **C1 — Version spatial scenario and placements.** Define coordinates/road
  binding, billboard orientation/side, phone opportunity model, active windows,
  creative hash and frequency caps. Validate IDs, geometry, time and campaign
  references; refuse impossible placement rather than snapping invisibly. Tests
  include injection, out-of-bounds, one-way, duplicate and off-network cases.

  **Evidence (2026-09-30):** canonical schema-v1 scenarios bind exact city/creative
  hashes, campaign references, non-overlapping windows, caps, explicit roadside
  direction/fraction/coordinate/side/orientation and versioned phone policy. Core v1/v2
  geometry validation refuses unsupported directions, bounds errors and coordinates over
  one meter from the declared road fraction without snapping. The bounded offline
  `city-campaign validate` command and installed-wheel smoke cover local/catalog inputs.
- [x] **C2 — Specify measurable *model* semantics.** Define road traversal,
  proximity and approximate visibility separately from notice, and phone-use
  opportunity separately from location. Document denominator and causal chain.
  Golden tests assert none/one/multiple crossing cases, reverse direction, segment
  boundaries, time windows and repeat caps. Do not call a proximity result a
  verified real-world impression.

  **Evidence (2026-10-01):** immutable continuous road traversals preserve legacy trace
  bytes. Pure core evaluation separates matching/active/proximity/approximate-facing and
  phone eligible/threshold/cap stages; typed records say
  `synthetic-opportunity-not-impression`. Crossing time, curved approach geometry,
  directions, half-open windows, cap scope, canonical IDs and a precisely specified
  SHA-derived SplitMix64 phone stream have golden/property/hash-seed coverage. The maximum
  20-policy/30-agent/7-day phone denominator is bounded by an automated performance gate.
- [ ] **C3 — Join mobility and campaign policies through core ports.** Plan each
  tick from immutable state, resolve cognition before commit, persist before observer
  notification, maintain deterministic total order and bounded state. Reuse existing
  rule policies only where their units and assumptions match; retain old zone runs.
  Tests verify campaign copy cannot control movement, events, budget or purchase
  probability; failed cognition/storage leaves a typed outcome.

  **C3a evidence (2026-10-01):** the first storage slice is complete without claiming
  the full causal bridge. Schema-v4 city runs freeze the validated scenario, canonical
  `synthetic-opportunity-not-impression` JSONL stream and independent funnel summary;
  manifest hashes/byte/count bounds, final-manifest publication, corruption tests,
  deterministic replay and installed-wheel smoke cover the artifact. At that slice,
  opportunities did not drive cognition, agent state, purchases, metrics, or reports. A
  subsequent bounded UI slice exposed validated current-minute opportunity evidence in the
  read-only saved-run viewer without adding a causal transition; C3b/C3c and C4a below record
  the later bounded additions.

  **C3b evidence (2026-10-02):** schema-v5 adds a second immutable, replay-verified
  evidence layer: one synthetic impression per opportunity and a deterministic noticed
  label from a fixed 0.5 keyed draw. Canonical `spatial-attention.jsonl` and
  `attention-summary.json` are manifest-bound and exposed through bounded read-only API/UI
  views. Every record says `synthetic-attention-not-observed-behavior`; the model is
  uncalibrated and cannot affect cognition, state, budget, movement or purchase logic.

  **C3c evidence (2026-10-03):** schema-v6 optionally freezes a strict response input and
  converts only persisted notices through the transparent `spatial-response-v1` rule model.
  Canonical response/state-update JSONL, complete final campaign state and an independent
  summary are manifest-bound, replayed, and exposed read-only in the saved-run viewer. Every
  record says `synthetic-response-not-observed-behavior`; same-minute planning reads one
  immutable pre-minute state and commits one atomic campaign-scoped update. Purchase
  intention is an uncalibrated state proxy, not a probability, sale, or transaction.

  C3 remains open because C3c does not integrate bounded cognition, memory, social
  propagation, or the separately specified rule-owned purchase path with spatial studies;
  it also does not join the zone engine or create a complete geographic campaign model.
- [ ] **C4 — Extend spatial metrics, comparison and reports.** Derive opportunity,
  impression, notice, recall/social and proxy metrics from persisted events with
  provenance; implement matched geographic A/A and paired A/B with common random
  numbers. Hold opportunity structure constant when claiming a channel effect or
  label its confounding. Tests verify exact A/A zero, input-order invariance,
  replay equivalence, zero denominators and artifact-derived report values.

  **C4a evidence (2026-10-03):** verified schema-v5 and schema-v6 artifacts now have a pure
  read-only, attention-only spatial metrics projection with exact
  numerator/denominator/source receipts and literal
  `synthetic-metrics-not-observed-outcomes` scope. `city-metrics`, a bounded viewer API/UI
  ledger, and installed-wheel smoke expose the same deterministic model without adding a
  cache or artifact schema. Matched `city-compare` requires city/assignment/trace/seed/
  duration/population parity, guarantees exact-zero same-run A/A deltas, labels changed
  normalized opportunity structure `opportunity-confounded`, and carries
  `synthetic-comparison-not-causal-or-observed-effect`.

  **C4b evidence (2026-10-05):** schema-v6 now has a separate receipt-backed response
  projection and independently classified response comparison while the existing attention
  output remains the default. `city-study` analyzes an explicit bounded definition of
  2–100 verified seed pairs as `spatial-paired-study-v1`, with one seed pair as the
  experimental unit and independently keyed deterministic bootstrap streams. `city-report`
  publishes the revalidated result to a fixed contained path as a zero-JavaScript,
  no-network, atomic no-clobber evidence ledger. Both carry synthetic/non-causal claim
  scopes and leave source artifacts unchanged.

  C4 remains open for spatial social and rule-owned purchase integration and other declared
  outcome metrics; job orchestration, the workbench, authentication, calibration, and
  external validity also remain separate open gates. Bounded C4b completion does not close
  C3, C4, D, E, F, or G.

**Exit gate:** An operator can place a fictional billboard on a qualified road,
run a spatial study offline, inspect persisted evidence on that road/time, replay it,
and compare a declared treatment without claiming actual sales.

## D. Analyst web workbench: FastAPI + vanilla browser

**Depends on:** A for saved timeline; C for advertising panels. Any remote writes
also depend on E. Keep existing CLI/TUI/report paths working.

- [ ] **D1 — Define a bounded web API.** List cities, create/validate scenarios,
  start and inspect jobs, page immutable event/agent timelines and fetch reports.
  Use versioned response schemas, pagination, cancellation and clear error codes;
  no path-taking endpoints or long synchronous simulation inside a request.
  Contract tests cover empty/corrupt runs, oversized requests and stable ordering.
- [ ] **D2 — Build the full timeline and map inspector.** Show time zone/model clock,
  day/night provenance, routes, activity, ad opportunity/impression/response, memory
  and social causal links. Offer a catalog-backed world-map city picker; let the
  analyst select agent, campaign, event and minute, including week/month navigation
  when the run's measured performance budget permits it;
  use a virtualized/bounded event list. Browser rendering consumes persisted/API
  data and uses safe DOM text insertion. Play/pause/speed/scrub never alter results.
- [ ] **D3 — Build scenario/provider selection and study views.** Expose only
  settings permitted by E; make rules/mock the credential-free default. Show run
  provenance, fallback counts, paired comparison, uncertainty and self-contained
  report download with synthetic disclosure. Labels distinguish assumptions from
  observations. Test malformed inputs and clean failures.
- [ ] **D4 — Browser QA and accessibility.** Automated tests cover keyboard
  navigation, screen-reader labels, small screens, Persian/unicode text, resize,
  offline resources, XSS/CSP, large timeline and slow API responses. A live browser
  smoke uses a fresh sample artifact; no screenshots substitute for assertions.

  **D3/D4 progress (2026-10-10):** the saved-run browser now presents the verified
  schema-v6 full-run response ledger with direct-rule and campaign-state receipts,
  keyboard provenance, safe text insertion, malformed-response refusal, narrow-screen
  containment, and no external resources. D remains open: scenario/study operation,
  jobs, report download, broader timeline mechanisms, authentication, provider settings,
  and the complete shell-free workflow are not implemented here.

**Exit gate:** A complete local geographic study can be operated through the UI
without a shell, and every displayed outcome can be traced to a stored event.

## E. Identity, authorization and protected provider settings

**Depends on:** D's endpoint design. **Blocks all shared/remote write deployment.**

- [ ] **E1 — Separate identity from model providers.** Implement OIDC login with
  authorization code + PKCE, exact registered redirects, issuer/audience/state/nonce
  checks, secure server-side session lifecycle and CSRF protection. Define roles
  (`viewer`, `analyst`, `admin`) and workspace ownership. Test cross-workspace
  denial and session fixation/expiry. Do not invent a password database.
- [ ] **E2 — Protect model configuration.** Model/provider metadata is visible only
  to authorized users; credentials are supplied through a managed secret source,
  never returned after write. URL allowlisting, DNS rebinding/redirect/private-net
  SSRF defenses, TLS policy, bounded HTTP bodies/timeouts/retries and cost limits
  are tested with malicious endpoints and fake secrets. Rules/mock remain available.
- [ ] **E3 — Authorize every data boundary.** Include city packs, runs, events,
  reports, caches, exports and job control. Apply quotas and rate limits; verify
  no existence leak from another workspace and no secret in errors or logs.
  Record administrative audit events without provider bodies/keys.
- [ ] **E4 — Threat-model and security gate.** Review browser, API, file upload,
  OIDC, provider egress, artifact tampering, backup and supply chain against a
  selected [OWASP ASVS](https://owasp.org/projects/asvs) level. Add adversarial
  tests and an independent penetration review before an enterprise pilot.

**Exit gate:** A non-loopback installation denies anonymous access, enforces roles
on every resource, protects secrets, and has a documented incident response path.

## F. Model credibility, calibration and responsible claims

**Depends on:** C's well-defined spatial metrics; lawful aggregate datasets.

- [ ] **F1 — Pre-register measurable targets.** Define what a commercial customer
  would compare (for example, aggregate opportunity ranking), permitted data,
  geography/time sampling, controls, uncertainty and failure thresholds **before**
  fitting. Separate model-discovery and validation datasets. No customer KPI is
  silently interpreted as individual behavior.
- [ ] **F2 — Calibrate and validate out of sample.** Fit only documented parameters
  to licensed aggregate data; hold out cities/periods, compare with simple baselines,
  run A/A, sensitivity, ablation and uncertainty analysis, and record negative
  results. Version dataset and parameter provenance. No investment/customer deck
  claims accuracy until the exact target's threshold is met.
- [ ] **F3 — Review personality representations.** MBTI may be an optional
  descriptive scenario label only; any numeric/causal mapping requires its own
  empirical protocol and ethics review. Test that labels alone do not change
  movement, ad response or purchase. Never infer real individuals' personalities.
- [ ] **F4 — Publish model cards per version.** State intended use, excluded use,
  calibration population, sample limits, subgroup/error analyses, bias risks,
  provider influence and validity horizon. Reports inherit the correct model card
  and disclosure. Regressions block claim upgrades.

**Exit gate:** Engineering release is possible without F, but it must still be
marketed as synthetic exploratory software. Any predictive claim requires F's
separate, independently reviewed evidence.

## G. Operational and release quality

**Depends on:** A–E for an authenticated geographic product; F only for predictive
claims. This package is not permission to deploy or publish automatically.

- [ ] **G1 — Select a measured deployment profile.** Decide single-organization
  versus shared tenants based on actual needs. Document topology, database/object
  store choice, TLS/reverse proxy, secret manager, migration/rollback and least
  privilege. Do not add PostgreSQL/queues/Kubernetes merely for appearance;
  introduce them only when SQLite/job benchmarks or tenancy requirements justify it.
- [ ] **G2 — Performance budgets and load tests.** Benchmark city import, route
  generation, run execution, concurrent scrubbing and report generation on named
  hardware and licensed representative packs. Measure CPU, peak RAM, disk, p50/p95
  latency and artifact size. Set explicit service targets from those results; test
  overload refusal and cancellation. Do not claim worldwide scale from a 250-agent
  fixture.
- [ ] **G3 — Recovery and observability.** Fault-inject DB/export/worker/process
  failures, prove idempotent job retry and detectable incomplete artifacts, exercise
  backup/restore with hash verification, and surface health/metrics without PII or
  secrets. Define retention/deletion with legal input and ensure restore does not
  silently create a different run.
- [ ] **G4 — Supply chain and platform QA.** Lock dependencies; run strict mypy,
  Ruff, full/coverage/hash-seed suites, browser/security tests, multi-OS CI, SBOM,
  dependency/license review, wheel and exact installed-wheel smoke. Reproduce the
  offline demo and geographic demo from a clean location. Tag/publication require
  explicit owner approval, signed artifact verification and rollback documentation.
- [ ] **G5 — Enterprise pilot checklist.** Provide install/operations guide,
  access matrix, data-rights ledger, model card, SLOs, backup exercise, threat
  model, support boundaries and an honest demo. Record customer-specific review
  findings; do not imply Amazon, KFC or any other company endorsed the product.

**Exit gate:** A controlled enterprise pilot can be installed, secured, monitored,
recovered and independently reproduced. “Production-ready” is asserted only for
the specified deployment profile and use case whose G/E gates passed.

## Mandatory verification at each behavior-bearing milestone

```text
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
PYTHONHASHSEED=0 uv run pytest -q
PYTHONHASHSEED=12345 uv run pytest -q
uv run pytest --cov=adlife --cov-branch --cov-report=term-missing
uv build --no-sources
uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl
uv run adlife doctor --offline
uv run adlife --format json demo --headless
git diff --check
```

Use PowerShell environment syntax for `PYTHONHASHSEED` on Windows. Add narrow
city/API/browser/failure tests for each package. The 85% configured coverage floor
is a floor, not permission to lose meaningful coverage. Do not make a live LLM,
public tiles or network a default gate. Record exact results and environment in
the focused plan or milestone evidence document.

## Agent handoff and state ledger

1. Read `AGENTS.md`, this spec/roadmap, the applicable focused plan, current
   `README.md`, `docs/architecture.md`, model card and limitations. Inspect actual
   code/tests; documentation can lag.
2. Check `git status --short --branch`, `git log --oneline -n 5`, and branch. Preserve
   unrelated changes. Never work on or merge into `defense-ready` for this program.
3. Pick **one** unchecked package, confirm its dependencies and acceptance evidence,
   then write its dated focused spec/plan if one does not exist. Do not silently
   widen scope or present a package as complete because its UI is polished.
4. Follow test-first per behavior; keep exact failing-test evidence, narrow/wider
   green outputs and full gates. Review diff, update status/docs/model-card claims,
   and use a focused commit with the repository's existing identity if configured.
5. On handoff, record the package ID, commit, files, test outputs, remaining risks,
   the next dependency-unblocked package and whether the branch is pushed. No plan
   itself authorizes a push, remote change, deployment or publication.

Current ledger (2026-10-05, after the bounded C4b slice): **A1–A4, B6, C1, and C2 are
checked.** Technical foundations also include city-pack v2/import/catalog work, C3a
opportunity storage, C3b attention evidence, C3c response/state evidence, C4a read-only
metrics/comparison, C4b repeated-seed analysis/static reporting, and bounded local viewer
slices. Those foundations do not close the
unchecked parent gates: B1–B5 still require the relevant data-product, rights, qualification,
scale, and basemap decisions; C3 still requires the broader cognition/memory/social/purchase
integration described above; C4 remains open for spatial social/purchase integration and
the other declared geographic outcome metrics; D–G retain their unchecked production,
identity, calibration, operational, and
enterprise gates. No rights-reviewed real city, authentication boundary, or external-validity
evidence is claimed by this ledger.
