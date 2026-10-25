# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The wheel release smoke check covers schema-v10 runs (2026-10-25)** — the clean-room
  smoke of the built wheel (`scripts/smoke_release.py`) now also submits schema-4 drafts
  through the installed workbench API:
  - a rules run and a practice run that answer every noticed ad offline;
  - a run whose local AI is a loopback stand-in that checks each question is prompt
    contract v3 (the persona block with the person card, and no advertiser internals),
    then answers in character, coolly for a person who distrusts ads and warmly otherwise.

  For each run the smoke reads the guided story and report: the provider run's story says
  the AI answered as the person, and its report shows the in-character limits. It then
  replays the run offline with no further AI request. This closes the roadmap item "add a
  V10 run to the wheel release smoke check".
- **Quick checks on every push, the full CI for pull requests and releases (2026-10-25)** —
  a push to `main` now runs `.github/workflows/quick.yml`: lint, types, the wheel smoke, the
  dependency and secret scans, and the whole test suite (without coverage, with the browser
  tests required) split into six parallel Linux shards by `scripts/ci_test_shards.py`, which
  puts every test file in exactly one shard and balances them by the times measured on CI
  (`scripts/ci_test_durations.json`, refreshed from each run's `junit-*` artifacts). The
  full CI (`.github/workflows/ci.yml`: Python 3.11 to 3.13 on Linux plus Windows and macOS,
  the 85% coverage floor, the performance gates and the long guided browser workload) no
  longer runs per push; it runs for pull requests, for every release through the release
  workflow, every Monday, and on demand.
- **The story and report say when the AI answered as the person (2026-10-25)** — for a run
  made with prompt contract v3 and a real AI, the story headline now says the AI answered
  those moments as the person who noticed the ad, each answered moment says "The AI answered
  as {name}", and the how-it-works box, the reaction glossary line and the limits describe
  the in-character answers: the AI reads each fictional person's whole profile, name and life
  story included, and its labels are a guess at how that person would react. Runs saved
  under prompt contract v2, practice runs and rules-only runs keep their wording, including
  "Names and descriptions of the people are never sent to the AI", which was true for them
  and is not for v3 runs. The start page describes the in-character AI that new runs use.
  English, German and Persian. The model card and limitations record the first live
  rehearsal of prompt v3 (GapGPT `gapgpt-qwen-3.6`): 304 of 304 questions answered, 22
  distinct answer vectors, relevance following interest overlap (Spearman 0.69) and
  credibility following advertising skepticism (-0.70), one run without held-out pairs.
- **The AI answers as the person (prompt contract v3) (2026-10-25)** — new bounded-cognition
  runs use prompt contract `city-cognition-persona-prompt-v3` (causal contract
  `CITY_COGNITION_DEFAULT_CAUSAL_CONTRACT_V4`: selection policy V3 with the same result schema
  V2, fallback, mock and influence policies). Instead of judging the ad from outside, the model
  role-plays the one fictional person who noticed it. Each question now sends that person's
  profile (name, age, job, background, MBTI personality type, interests, and the six
  temperament traits, each as a number with fixed words), how they feel about the brand on the
  rules track just before this ad (liking, ad memory and purchase interest), the ads of this
  campaign they noticed earlier (how many, and up to the last five with day, time, channel,
  activity and what the rules made of each), their situation (day, time, what they are doing,
  phone feed or roadside billboard), and the ad with its price in words. It never sends an
  earlier AI answer or a "with AI" value, so there is no feedback loop and every question is
  still built from frozen inputs and the rule baseline before any AI call. It no longer sends
  the advertiser's internals (target interests, frequency cap) or the rules engine's own
  numbers for that notice (interest, affordability, value and fatigue factors and the rules'
  sentiment and recall deltas). The answer is still the four closed categories and safety
  flags, mapped by `city-cognition-influence-v1`; movement, attention, the rules track and the
  purchase-intention proxy stay rule-owned. Sending age, job and personality type adds a
  stereotype risk: the prompt asks for `stereotype-risk` when an answer would rest on a
  stereotype, and a flagged answer is neutralized. The ten packaged persona templates now tell
  a short invented life each (daily life and hobbies, one dream or goal, one current worry or
  circumstance). Runs saved earlier keep prompt contract `city-cognition-signal-prompt-v2`,
  which excluded name, age, occupation, background and MBTI, and their own frozen persona
  copies, so they still load, verify, replay and open unchanged.
- **AI coverage of the whole run: selection policy V3 and retries (2026-10-24)** — new
  bounded-cognition runs use `city-cognition-selection-v3` (`spread-evenly`): the AI limit of
  60, 120 (local default), 250, 500, or 1000 (remote default) requests is spread evenly over the
  run's noticed responses in time order, with no per-person cap, and 1000 means every noticed
  ad, up to 1,000; rules and mock use the offline limit 1000. Every selected request is funded,
  so V3 records no budget-skipped signals. Each funded request reserves 1,536 output tokens: at
  most 6 HTTP attempts of up to 256 output tokens each (a first attempt, 3 transient retries and
  2 schema repairs), so 1000 funded requests reserve 1,536,000 output tokens; input tokens and
  vendor charges are outside that reservation. Remote sources run 8 requests at a time and local
  ones 4, within an 1800-second aggregate deadline; the cognition input may reach 512 KiB and
  the terminal stream 8 MiB. A failed attempt is retried after an exponential backoff (1, 2,
  then 4 seconds, capped at 8, with a deterministic ±20% jitter) or a server's `Retry-After` of
  up to 30 seconds, never past the deadline; questions start in an interleaved order across the
  run, so a deadline cut thins every day evenly, and fallback records report the tokens their
  invalid attempts spent. `cognition_progress` also carries the run's frozen
  `concurrency_limit`. Runs saved earlier keep selection policy V2 byte for byte (earliest
  first; at most 4 noticed responses per agent and 60 per run; 10, 20, 40 or 60 funded with
  1,024 output tokens each; 4 at a time within 180 seconds), so they still verify, replay and
  open unchanged.
- **The story and the report cover the whole simulated period (2026-10-24)** — key moments
  are budgeted per day (`max(3, ceil(60 / days))`, rules only `max(3, ceil(20 / days))`): each
  day gets its AI moments, evenly spaced, topped up with rules-only noticed moments to six, so a
  run whose AI questions ran out early still replays to its last day. A person's 40 kept
  moments and the report's `max(8, 2 × days)` listed moments take turns over the days. The
  story page ends with a closing step at the end of the last simulated day and its day-by-day
  totals (also in the Summary tab and, for runs of several days, in the report), and its counter
  names the day. Saved runs keep their own selection caps and wording: the story reads them from
  the run's frozen cognition input. The setup page takes its AI limit options (including
  **Every noticed ad**), per-source defaults and wording from the server; the progress page
  shows the run's own concurrency.
- **README: two clear ways in (2026-10-25)** — the README now opens with how to install from
  source, then separates **using the app** (`uv run adlife app`, the guided pages at `/guide`)
  from **using the engine on its own** (the campaign engine with `adlife demo`, `init`, `run`,
  `replay`, `report`, and `compare`, and the city engine with `city-run`, `city-replay`,
  `city-view`, and `city-metrics`, including how to open and verify runs made in the app). The
  detailed city-track material moved, unchanged, to a reference section, and the Status table
  and Roadmap now match what is actually done and what is still open.

### Removed

- **PyInstaller standalone build (2026-10-25)** — AdLife ships only as a Python package run
  with uv; there is no standalone executable. The PyInstaller specification
  (`packaging/adlife.spec`) and its smoke harness and network guard, the release workflow's
  `build-native` job, the `pyinstaller` development and packaging dependencies, the dormant
  binary mode of `scripts/install.sh`, and the "Frozen binaries" installation instructions were
  removed. The release workflow still verifies the signed tag, reruns CI, builds and
  smoke-tests the wheel and sdist, writes checksums, the SBOM, and the license report, and
  publishes to PyPI.

- **Windows demo start script and presenter guide (2026-10-21)** — `scripts/start-guided-demo.ps1`
  and `docs/guided-demo.md` were removed. `uv run adlife app` starts the app on every operating
  system, keys are typed into the app itself, and [docs/user-guide.md](docs/user-guide.md) is
  the general user guide that replaces the presenter script.

- **Technical workbench interface (2026-10-21)** — `adlife city-workbench` now serves only
  the guided pages under `/guide` and the local JSON API under `/api/*`, and its root address
  `/` redirects (`307`) to `/guide`. The technical workbench page at `/`
  (`CITY -> CAMPAIGN -> RUN -> INSPECT`), the saved-run inspector at `/runs/{run_id}`, the
  draft page at `/runs/{run_id}/reuse`, the `/static/*` dashboard assets, and the
  `/assets/workbench*.js` and `.css` files were removed and answer `404`; only
  `/assets/workbench-icon.svg` remains, as the pages' icon. The guided pages and the run
  report no longer link to them (the report keeps its Technical details section). Every
  `/api/*` route is unchanged; run-scoped reads always verify the saved files afresh, and a
  request that still sends the removed inspector's view-token header is refused with `409`.
  A saved run's timeline and evidence open with `adlife city-view ROOT RUN_ID`. The investor
  demonstration script `docs/investor-demo.md` was removed with the interface it described;
  `scripts/demo_workspace.py` now reads its security token from `/guide`, checks each V10
  run's story, and prints guide addresses instead of inspector URLs.

### Added

- **Connect any OpenAI-compatible service from the app (2026-10-21)** — the guided pages no
  longer need an AI approved at startup. **Online AI** takes any service's base URL, model
  name, and API key (OpenRouter, GapGPT, or another OpenAI-compatible gateway); **AI on this
  computer** takes a local server's address, so LM Studio (`127.0.0.1:1234/v1`) works as well
  as Ollama (`127.0.0.1:11434/v1`). The new `POST /api/providers/origins` route approves one
  typed address for the running process through exactly the startup rules (https online, a
  loopback literal on any port except the server's own locally, no private or reserved
  addresses, at most 20 origins); the same address always gets the same approval id, so a
  saved profile works again after a restart once its address is connected again. A typed key
  is held in server memory for that address (`"remember": false`, the default) and cleared
  when the server stops; `"remember": true` stores it in the approved OS keyring as before.
  Addresses used before are offered for one-tap reuse, the setup page names a missing address,
  and nothing names a default service any more. Provider answers that carry extra members
  their gateway adds (OpenRouter's `provider` and `cost`, LM Studio's `stats`) are accepted:
  members nobody reads are ignored, while tool calls, refusals, wrong types, and every size,
  credential, and secret screen still refuse an answer.
- **`adlife app` (2026-10-21)** — starts the guided app with the workspace `./adlife-workspace`
  and port 8765 and opens `http://127.0.0.1:8765/guide` in the default browser once the server
  answers (`--no-open` to skip). It shares the server and options of `adlife city-workbench`,
  which still never opens a browser.
- **More people, products, and towns (2026-10-21)** — ten fictional persona templates (Pimo,
  Zarel, Ketra, Tavi, Selun, and Halvi join Lumo, Veyra, Orilo, and Nuvin) with ten distinct
  colour and shape marks; eight fictional products (Spokelark Pass, Pagewren Box, Pulsefern
  Coach, and Humvale Stream join the original four); and five fictional towns
  (`fictional-old-town-v2`, 30 junctions with a ring road and two bridges, and
  `fictional-new-district-v2`, 39 junctions with diagonals and one-way streets, join the grid,
  harbour loop, and river junction). The setup page has a town picker with a street preview
  and a verified billboard spot per town (a town without one offers phone ads only), and the
  "All personas" audience puts three of each profile into a 30-person study. The grid town's
  billboard now faces its traffic (259°) instead of away from it, so billboard runs there
  produce ad moments.

- **One-command guided demo start on Windows (2026-10-20)** —
  `scripts/start-guided-demo.ps1` asks for the online AI key without showing it, keeps it in
  its own process environment only while the server runs, starts `adlife city-workbench` with
  the GapGPT address approved (or any OpenAI-compatible service via `-ProviderName`,
  `-ProviderUrl`, and `-KeyVariable`), opens `/guide` in the default browser, and removes the
  key from the environment when the server stops. `-PracticeOnly` starts without a key. The
  progress page now titles a finished run "Simulation finished" (or "Simulation stopped"),
  Persian pages separate items with «،» instead of a middle dot that reads as a Persian zero
  beside digits, and the AI connection card shows only the source choices until the provider
  listing has loaded.
- **Story map places and presenter-rehearsal fixes (2026-10-20)** — the story map marks every
  home, workplace, and free-time place with a glyph and a translated label (also in the "Who
  is who" legend), fans out people who share a spot while the highlighted person keeps it, and
  the speech bubble adds the activity ("Veyra #8 · 07:39 · at home"). After a rehearsal
  review: a moment whose change matches the rules now still shows its with-AI values ("(the
  same change as with rules only)") instead of a bare "the same as rules only" next to
  different bars; **How was this computed?** adds the "new values" step for both tracks
  (liking adds the change; ad memory moves that share of the remaining way to 1), so its
  numbers add up; the liking effect names the real direction ("fell less", "rose instead of
  falling"); the opening reads "Opening · 12 moments" instead of "Moment 0 of 12"; the summary
  explains the over-limit drop from "Chosen for the AI" to "Sent to the AI"; practice runs
  carry a note that their "AI" wording means practice answers; a person's most common labels
  name every tied value ("skeptical / annoyed"); the setup card's limit sentence uses the
  chosen limit; saved-run cards show the model name; the key hint points at the start script;
  and Persian uses «میزان ارتباط» for relevance and its own digits for the seed. The report
  follows the same changes. A second, German- and Persian-focused rehearsal followed: the
  arithmetic lines are now left-to-right equations with "≈" and the change in parentheses
  ("−0.270 + (−0.085) ≈ −0.354", every number rounded on its own); the effect sentence sits
  above the bars so it stays in view on a 1280×720 projector; the map's speech bubble avoids
  the place labels and draws a leader line when it moves out; labels that each came once read
  "(once each)"; value pairs and "Rules only" no longer break across lines; the report counts
  requests "(retries included)" and shows the seed in Persian digits; setup lists interests
  after a colon; run and model names never break at a hyphen; the key hint gives the full
  `powershell -ExecutionPolicy Bypass -File scripts\start-guided-demo.ps1` command; and the
  Persian map labels are «کار» and «تفریح», so they fit beside their places.
- **Guided presenter view (2026-10-20)** — `adlife city-workbench` now prints
  `city workbench: http://127.0.0.1:{port}/guide`, a plain-language view for presenting a
  study in English, German, or Persian (right to left), next to the unchanged technical
  workbench at `/` and the saved-run inspector. `/guide` explains the run in three steps,
  checks the AI connection, and lists the saved runs; `/guide/new` sets up a study with three
  cards and one button; `/guide/jobs/{job_id}` shows live progress such as "3 of 10
  answered"; `/guide/runs/{run_id}` replays a saved schema-v10 run as a story on an animated
  map; and `/guide/runs/{run_id}/report` renders a script-free HTML report of that run, with
  a self-contained download, without writing to the workspace. Story and report sentences
  come from one builder (`GET /api/runs/{run_id}/story`, `adlife-run-story-v1`) and one
  catalog per language, so every language says the same thing. The guide writes only through
  the existing `/api/*` routes, runs no inline script, and loads nothing from the internet.
  The presenter script, with the PowerShell start command for an online AI, is
  [docs/guided-demo.md](docs/guided-demo.md).
- **Bounded AI cognition in the city workbench (2026-10-19)** — the workbench RUN step now
  separates **Operation** (`Explanation only` or `Bounded AI cognition`) from **Source**
  (rules, mock, local or remote compatible model). `Bounded AI cognition` submits workbench
  schema 4 and publishes manifest V10 under `spatial-response-v2`. For each selected noticed
  response, the source chooses only closed categories — affect, relevance, credibility,
  salience and safety flags — and AdLife maps them through the frozen
  `city-cognition-influence-v1` policy into a separate synthetic cognition state. Movement,
  opportunities, attention, baseline responses, budgets and the purchase-intention proxy stay
  rule-owned, and name, age, occupation, background and MBTI are excluded from the causal
  prompt. Selection keeps at most 4 responses per agent and 60 per run. Local and remote runs
  fund 10, 20 (default), 40 or 60 requests and require a passing bounded-signal compatibility
  check. Each funded request reserves 1,024 output tokens: at most 4 HTTP attempts of up to
  256 output tokens each, so 60 funded requests reserve 61,440 output tokens. Input tokens and
  vendor charges are outside that reservation. Uncertain or safety-flagged answers
  stay auditable but are neutralized as a whole vector, unavailable or invalid answers become
  typed neutral fallbacks, and unfunded selections are recorded as budget-skipped. Rules and
  mock remain offline. V10 freezes `inputs/cognition.json`, `outputs/cognition.jsonl`,
  `outputs/cognition-summary.json`, the dual rule/cognition response state and every policy
  hash; `city-replay` reproduces it offline with no provider, cache, keyring, environment or
  network access. Live provider signals are nondeterministic. A ChatGPT or Codex
  subscription, product login or OAuth token is not accepted as a provider credential. V10 is
  an unreleased artifact schema: V10 runs written by earlier development builds, before the
  final output budget and the safety-flag and appraisal prompt guidance, are refused and must
  be recreated.
- **V10 inspection, read-only views and metrics (2026-10-19)** — the saved-run inspector at
  `/runs/{run_id}` and in `adlife city-view` verifies V10 evidence, then shows coverage, a
  source and effect badge for every response, the per-response trace (deterministic
  baseline, signal or neutral status, fixed mapping, applied cognition delta, atomic
  rule/cognition update), the profile/model/contract provenance, and the actual provider
  usage (HTTP attempts and reported prompt and completion tokens) per run and per request,
  kept apart from the output reservation. The read-only
  cognition-summary, cognition-records and workbench-input views serve V10, and
  `GET /api/workbench` publishes the bounded-cognition capability the browser reads instead of
  pinning limits. `city-metrics --layer response` prints the separate
  `spatial-response-metrics-v2` families: a rule baseline that keeps the uncalibrated purchase
  proxy, and provider-dependent cognition labelled exploratory, not a campaign effect.
  `city-compare --layer response`, `city-study` and `city-report` refuse V10 runs for now
  (V6-V9 keep them), and the default attention comparison pairs a V10 run only with another
  V10 run.
- **Run again from a saved run (2026-10-19)** — the inspector and the run library offer
  **Run again with these inputs**, which reopens the workbench at RUN with a schema-v8 or later
  run's verified inputs and a suggested new run ID; the saved run is never changed. The
  reopened RUN step shows the bounded-signal check this server process already recorded for
  the selected revision instead of asking for it again (a plain reload still asks), and refused
  fields now open their own step.
- **Live model progress (2026-10-20)** — while a local or remote bounded-cognition job is
  requesting cognition, the job panel shows **Model answered 3 of 10 requests** with a progress
  bar that moves on every poll, and the page's polite live region speaks the count at most every
  five seconds; rules and mock jobs, which contact no model, show no count.
  `GET /api/jobs/{job_id}` adds `cognition_progress`: `null` for schema 1-3 jobs and before
  planning ends, then `{"resolved": k, "total": N}`, where `N` is the run's funded provider
  requests (0 for rules and mock) and `k` counts provider answers, cache hits and fallbacks; `k`
  only grows, never exceeds `N` and equals it once cognition is applied. The count is
  operational only and never enters a V10 artifact or replay.
- **Two-part live demonstration script** — docs/investor-demo.md (since removed) is
  now the SETUP and SIMULATION script for presenting bounded AI cognition through a local
  Ollama `qwen2.5:7b-instruct` model, with the offline mock/rules fallback and
  troubleshooting.
- **Backup demo workspace (2026-10-19)** — `scripts/demo_workspace.py --base-url
  http://127.0.0.1:<port>` seeds a running workbench with ready-to-open runs through the same
  loopback API the browser uses: a V10 bounded-cognition run from the mock source and a V9
  explanation-only run from rules, plus, with `--with-llm`, a V10 run with 10 funded requests
  from a local Ollama model after it warms the model, reuses or creates the `local-ollama`
  profile revision and passes the bounded-signal check. Each invocation takes new numbered
  run IDs, so a saved run is never overwritten; the script refuses any non-loopback URL and
  never prints the page's security token.
- **Optional city response explanations (2026-10-17)** — schema-3 workbench requests now
  publish schema-v9 runs with noncausal rules, mock, local, or remote explanations. Exact
  provider profiles are immutable; remote origins require startup approval; credentials
  resolve from an approved OS keyring or explicitly bound environment-variable name and
  never enter artifacts. The bounded OpenAI-compatible Chat Completions adapter validates a
  strict result schema, screens unsafe output, and records deterministic fallback. Frozen
  `inputs/cognition.json`, `outputs/cognition.jsonl`, and
  `outputs/cognition-summary.json` make saved inspection and replay provider-free. These
  explanations cannot change movement, exposure, attention, rule responses, campaign state,
  budget, purchase probability, or metrics. OAuth and multi-user authentication remain out
  of scope.

- **Immutable workspace city registry (2026-10-14)** — the loopback workbench can now
  register a bounded local CityPack V2 without exposing a client/server path, list it
  beside three bundled fictional fixtures, select it, run it, inspect its frozen source
  metadata, and replay from `inputs/city.json` without consulting current registry state.
  Uploads are immutable, no-clobber, capped at 4 MiB for both raw and canonical bytes,
  labelled `operator-supplied-unreviewed`, and never promoted to packaged
  `rights-reviewed` status by technical validation. Source URLs remain inert metadata;
  default operation performs no outbound request.
- **Fictional persona authoring (2026-10-13)** — default browser schema-2 Audience
  authoring publishes schema-v8 with frozen `inputs/workbench.json`, deterministic persona
  assignments and verified saved identities. Schema-1/schema-v7 homogeneous cohort and
  direct V6 compatibility remain supported. Four bundled fictional templates support
  explicit count reconciliation, immutable IDs and collapsed identity context.
  Name, age, occupation, background and MBTI are noncausal; explicit interests, six traits
  and initial state are causal response assumptions. No MBTI predictive power,
  real-person validation, identity-derived mobility or purchase transactions are claimed.
  V8 replay, metrics, comparisons, reports and homogeneous paired-study consumers retain
  strict provenance. Replay preserves source bytes and is independent of current templates.
  Persona text screening refuses recognized unsafe patterns; it does not prove absence
  of personal information. This is an unreleased artifact-schema extension, not package
  version 0.8.0 or a public launch.
- **Browser-operated local city workbench and immutable inspection (D1b/D2 local)** —
  `city-workbench --workspace PATH` now validates and freezes a complete packaged-city,
  fictional-creative scenario, executes at most one deterministic-rules job through
  truthful process-local phases, and lists only independently verified completed runs.
  Schema-1 compatibility runs extend schema-v6 evidence as schema-v7; default schema-2
  persona authoring uses schema-v8 with canonical
  `inputs/workbench.json` bound by `workbench_input_sha256`; load and replay refuse missing,
  noncanonical, changed, or inconsistent provenance. Run-scoped APIs and the shared
  immutable inspector verifies current artifacts on opening and serves token-bound snapshots
  for subsequent scoped reads; reload and tokenless reads verify anew. Same-origin/CSRF/body
  bounds, no-path HTTP inputs, workspace link/reparse refusal, no-clobber publication, and
  exact-wheel coverage remain enforced. Every phase retains a frozen `accepted_settings`
  receipt, which completion cross-checks against the independently verified run. The
  packaged `CITY -> CAMPAIGN -> RUN -> INSPECT` workflow selects verified fictional
  geography, edits bounded campaign assumptions, transports seeds as exact decimal strings,
  validates, operates/cancels/resumes polling of the process-local job, and reopens the
  verified library in the scoped inspector. OAuth, report download,
  comparison/paired-study operation, real-city calibration, authentication, process-restart
  recovery, and outcome prediction remain explicitly unavailable.
- **City-pack v2 and offline catalog foundation** — backwards-compatible city packs
  can now preserve directed road geometry, exact bounds, IANA time-zone metadata,
  source provenance and known omissions; mobility-only v2 runs freeze those inputs and
  replay independently of later catalog changes. New `city-catalog list/show` and
  `--city-id` selectors verify packaged resources by canonical hash. This initial
  foundation began with the explicit `fictional-grid-v2` fixture; the current packaged
  catalog has three fictional fixtures and no rights-reviewed real city.
- **Bounded local OSM v2 ingestion** — explicit source date/version/time-zone inputs,
  deterministic source hashing and quality counts, stable segment identities, and
  refusal of access/turn semantics the model cannot represent. Import and catalog
  workflows stay offline and do not contact public tiles or geocoders.
- **C3c spatial response evidence** — `city-run --spatial-response` now requires a
  validated `--spatial-campaign` and creates a replay-verified schema-v6 artifact. The
  transparent `spatial-response-v1` evaluator converts only persisted noticed records into
  bounded campaign-scoped state under `synthetic-response-not-observed-behavior`; it adds
  no provider call, purchase event, budget mutation, or movement change. The saved-run
  viewer exposes verified response evidence and explicitly scoped final end-of-run state.
- **Spatial response metrics and C4b study evidence** — schema-v6 runs now expose the
  additive `spatial-response-metrics-v1` receipt projection through
  `city-metrics --layer response`, `city-compare --layer response`, and the read-only
  `/api/spatial-response-metrics` endpoint while preserving the original attention
  defaults. `city-study` analyzes an explicit two-to-100-pair definition with one seed
  pair as the experimental unit under `spatial-paired-study-v1`; `city-report` publishes
  the same verified result as a fixed-path, zero-JavaScript, no-clobber evidence ledger.
  These bounded C4b outputs describe simulator-seed variation only and retain the
  `synthetic-study-not-observed-or-causal-effect` scope.
- **Announcement-readiness hardening** — regression coverage for hybrid provider
  identity, non-destructive replay, the cognition fallback across ticks, the CLI
  error boundary, and documentation consistency (tick scale pinned by a doc test).
- `ADLIFE_DEBUG=1` requests a full traceback for any command failure; expected
  failures now print a concise diagnostic only (see Fixed).

### Fixed

- **Remote OpenAI-compatible gateways such as GapGPT/vLLM (2026-10-20)** — a presenting laptop
  that cannot run Ollama needs a remote endpoint, and GapGPT (`https://api.gapgpt.app/v1`,
  model `gapgpt-qwen-3.6`, which resolves to Qwen/Qwen3.6-35B-A3B-FP8 served by vLLM) failed
  the bounded-signal path in five ways; each is fixed and reproduced offline by a loopback
  stand-in gateway in the tests. (1) The frozen `city-cognition-signal-result-v2` schema used
  `uniqueItems`, which vLLM grammar decoding refuses with HTTP 400 and OpenAI strict
  structured outputs does not support; the schema no longer carries it, and
  `CityCognitionResultV2` still refuses duplicate safety flags. The result-schema and
  causal-contract hashes change, and with them request IDs and mock signals: the V10 core
  golden and the replay witness are re-frozen, and V10 runs saved before this change are
  refused and must be recreated. (2) vLLM adds its own members to every answer
  (`prompt_logprobs`, `prompt_token_ids`, `prompt_text`, `kv_transfer_params`,
  `ec_transfer_params`, `metrics`; `stop_reason`, `token_ids`, `routed_experts`;
  `annotations`, `audio`, `function_call`, `tool_calls`, `reasoning`, `reasoning_content`).
  Each is now admitted only as null or the type vLLM sends, and is never parsed or kept; the
  whole body still passes every credential and secret screen, and any other unknown member, a
  real function or tool call, or non-text reasoning stays a protocol error. (3) An answer cut
  at the 256-token attempt cap (`finish_reason` `length`) is accepted when its text is still a
  complete object that passes every check, such as an object followed by runaway whitespace;
  an object that never closed, or no text at all, is a repairable `invalid-json` result, so
  the one schema repair runs instead of an immediate protocol fallback, and a complete object
  that fails validation keeps its usual repair reason; no fallback or repair reason was added.
  (4) Qwen on vLLM can spend the whole cap thinking and answer nothing. A provider profile
  revision can now turn model thinking off: the profile form's **Turn off model thinking (Qwen
  on vLLM servers)** checkbox, on by default for a remote model ID that names Qwen, adds
  `chat_template_kwargs` `{"enable_thinking": false}` to every request, repair and
  compatibility check of that revision (0 reasoning tokens in 8 of 8 live calls). The choice
  is saved as `disable_thinking` in the revision and frozen into the V10 provider snapshot and
  effective settings, so checks, cache entries, terminal records and replay bind it. While it
  is off nothing is sent, so OpenAI, which refuses unknown request members, is unaffected, and
  existing profile revisions, saved profiles and saved runs keep their exact bytes and hashes.
  V9 explanations with such a profile send it too. (5) A remote compatibility check now waits
  up to 60 seconds instead of 10 (single GapGPT calls took 3 to 26 seconds), and its single
  attempt may use the whole deadline; a local check keeps 120 seconds, and run attempts keep
  the profile's frozen timeout of at most 30 seconds. In a live rehearsal with fixes (1), (2),
  (4) and (5) applied, a check passed in 6.2 seconds and a Part 2 study (seed 42, 10 funded)
  completed in 31 seconds with 9 provider signals and 1 fallback from a whitespace runaway,
  the case that (3) now sends to the schema repair.
  docs/investor-demo.md (since removed) shows the GapGPT start command and profile.
- **Real local-model provider path (2026-10-19)** — a local `qwen2.5:7b-instruct` needs
  about 140-160 output tokens for the strict bounded-signal result, so the V10 per-attempt
  cap is 256 output tokens instead of the development value that truncated every answer.
  The bounded-signal prompt now states when each safety flag applies: before, the model
  flagged almost every ordinary answer, so neutralization erased nearly every signal; in a
  44-request live check with the new wording, no ordinary answer was flagged. The
  neutralization rule itself is unchanged. A local compatibility check now waits up to 120
  seconds for a model that is still loading (remote checks: see the GapGPT/vLLM entry above);
  run attempts keep their frozen 30-second timeout.
- **Varied bounded signals from a local model (2026-10-19)** — with the earlier wording a
  local `qwen2.5:7b-instruct` returned one vector (neutral, high, high, medium) for 89% of 73
  varied requests, and changing one causal input moved the targeted category in 3% of
  counterfactual pairs, so the cognition track was close to a fixed uplift. The bounded-signal
  prompt (`city-cognition-signal-prompt-v2`; same ID, result schema and safety-flag wording)
  now asks for a separate judgment per category from the agent's point of view, with
  qualitative anchors that name the supplied inputs and no numeric cutoffs. In a live check
  the same model gave 17 distinct vectors for the 73 requests, relevance followed interest
  overlap (Spearman 0.82) and credibility followed advertising skepticism (-0.70), and 62% of
  targeted categories in held-out pairs moved the expected way (1.5% the wrong way); 284
  pooled requests were 97.9% valid on the first attempt with no spurious safety flag.
  Initial brand sentiment remains largely ignored and price moves affect in only about half
  of the pairs; see the model card. Every V10 run freezes the prompt text and hash, so V10
  runs saved with the earlier wording are refused and must be recreated.
- **V9 explanations from real providers (2026-10-19)** — a real 10-digit `created`
  timestamp in a provider response envelope matched the persona phone/national-ID screen,
  so every schema-v9 explanation fell back. The whole raw response body is now screened only
  for credentials and provider secrets; decoded envelope text and model-authored text keep
  the full screen.
- **Windows saved-run reads during city-registry access (2026-10-19)** — a run read that
  overlapped a city-registry root anchor (for example `GET /api/cities`) hit a sharing
  violation and reported the run unavailable; run reads now wait out the anchor for up to
  10 seconds, as the registry already did.
- **Long scenario names on phone-width inspectors (2026-10-19)** — the inspector's
  current-minute scenario heading could not wrap a long unbroken token. On macOS, whose
  fallback for `Segoe UI` is the wider Tahoma, a long scenario name widened a 390-pixel page to
  417 pixels. The heading now wraps anywhere, like the other user-supplied names.
- **Release smoke on Windows short temporary paths (2026-10-19)** — when the temporary
  directory is reached through an 8.3 short name, as on GitHub's Windows runners
  (`C:\Users\RUNNER~1\...`), `scripts/smoke_release.py` failed its spatial runs with
  `city run opportunity stream has an unsafe location`. The smoke now resolves its workspace
  to the long form before using it, as pytest does for its own temporary root.
- **Workbench cognition field errors (2026-10-19)** — server-side cognition validation errors
  now name their exact `cognition.*` fields, so the browser highlights the matching control.
- **Counts, labels and help text (2026-10-19)** — a count of exactly one now reads in the
  singular in the workbench (audience and persona totals, map summary, funding, cost
  disclosure and job panel), the inspector, and the human output of `city-run` and
  `city-replay`; JSON output is unchanged. The run-step field is now labelled
  `Baseline responses` (`Rules engine (offline)`), and its help says the rules compute every
  baseline response while model assistance only explains results or supplies bounded signals
  for the separate cognition track. `city-compare --help` now says the attention layer accepts
  schema-v5 through schema-v10 runs and the response layer schema-v6 through schema-v9. The
  job panel's phase badge shows the readable phase (`REQUESTING COGNITION`) instead of its
  identifier, the inspector writes reserved output tokens with thousands separators, and the
  selected agent's map label is drawn above its opportunity and notice rings.
- **Verified timeline interaction and causal presentation** — the run-scoped workbench
  inspector now obtains each frame and its bounded evidence/state pages through one GET-only
  projection from one freshly verified run context. The legacy viewer exposes the same
  projection contract over its startup-validated immutable context. Superseded evidence
  selections cannot roll back a newer choice, causal highlighting follows persisted event
  identifiers rather than every
  record in a campaign, the selected campaign receipt is visible without filtering the
  full-run event totals, and autoplay uses one concise screen-reader announcement region.
  Legacy individual viewer reads remain supported.

- **Provider identity in run manifests** — a hybrid run answering through a real
  configured local or remote OpenAI-compatible provider was persisted with manifest
  provider `mock`, and a replay was recorded as its source's provider rather than
  `replay`. The manifest now records the cognition the run was wired to
  (`rules | mock | local | remote | replay`), consistently across headless runs, live
  dashboard runs, replays, and demo runs; rules and mock runs are unchanged.
- **Cognition fallback across ticks** — a hybrid run whose provider fails after the
  first tick could abort with `UnknownCognitionRequest`, because the cached cognition
  service kept tick 1's rule inputs. The service now adopts each tick's fallback while
  keeping the run's budget books, so a failing provider falls back for the whole run
  as documented.
- **Replay is non-destructive** — `adlife replay` deleted an existing
  `replay-<run-id>` directory before re-running. It now refuses the conflicting
  destination with exit 3 and leaves every stored artifact untouched; the source run
  is never modified.
- **Duplicate-run exit code** — refusing an existing run identifier exited 2; the
  documented run-conflict code is 3, and the CLI now follows the documentation.
- **CLI error boundary** — expected failures (invalid input, run conflicts, provider
  misconfiguration, corrupted artifacts) printed full Python tracebacks. They now
  print one concise diagnostic on stderr (with the clean JSON error object on stdout
  in JSON modes); tracebacks remain for unexpected internal defects and for
  `ADLIFE_DEBUG=1`.
- **Documentation consistency** — quickstart, architecture, reproducibility, and the
  ODD protocol described a one-minute tick / 1,440 ticks per day; the engine advances
  in 15-simulated-minute ticks (96 per simulated day; timestamps remain absolute
  simulated minutes). The README status section no longer contradicts its own
  capability table, reproducibility claims distinguish event-for-event equality from
  byte-level file equality, and the `--run-id` help text renders correctly in
  rich-based terminals.
- **Live dashboard robustness** — an agent-row highlight processed while the layout
  was still mounting could crash the live dashboard with `NoMatches`. The detail
  panel update is now best-effort and re-projected on the next committed tick; a
  read-only dashboard never dies on a transient query.
- Removed internal implementation-plan task references from user-facing help text,
  error messages, and module docstrings.

### Prior unreleased work

- **Release-candidate certification** — metamorphic property suite (determinism across
  cognition modes, hybrid replay equality, seed sensitivity, agent-order and
  intent-order permutation invariance, causal-chain tracing, no-campaign control,
  social-off ablation, counterfactual display names); a 30-agent × 7-day performance
  gate with a 20-second CI ceiling and recorded peak memory; an adversarial security
  suite (asset-path traversal, symlink escape, YAML object tags, hostile display names,
  secret absence in artifacts, corrupt SQLite/JSONL detection, network ban on offline
  runs); committed golden scenario evidence (`rule-small`, `mock-small`) byte-compared
  on every run and movable only through `scripts/regenerate_golden.py`.
- A routability invariant suite pinning every packaged routine against the packaged
  world through the real movement resolver.

### Fixed

- **Weekend routine crash** — every packaged template's weekend return home boarded a
  highway route from the cafe, a leg no world could route, so any run reaching a
  weekend day (day 5–6) failed mid-tick. Weekend evenings now walk home directly, and
  the invariant suite keeps every template routable.
- **Performance** — snapshot fingerprints are now computed lazily (the per-tick
  plan/commit guard compares object identity), and revalidation is memoized by object
  identity, removing a whole-population hash and repeated deep validation walks from
  every tick. The 30-agent × 7-day run fell from ~150 s to ~15 s on the reference
  laptop.

## [0.1.0] — 2026-09-22

Initial development release of the complete research instrument.

### Added

- **Core model** — frozen, strictly validated domain contracts; deterministic
  clock advancing in 15-simulated-minute ticks (96 per day); keyed random oracle;
  ten-zone routable world; two-phase
  movement planning; fictional population, routine, and relationship-graph generation
  from packaged templates.
- **Behaviour** — campaign ingestion with safe YAML and path containment; exposure
  opportunity, impression, and attention; rule-bounded response, memory encoding with
  five-slot salience retention, daily decay, social influence over relationship edges,
  and a rule-only purchase proxy.
- **Cognition** — ports with rules, mock, and OpenAI-compatible hybrid providers;
  budgeted, cached, retried service; terminal rule fallback; every fallback recorded as
  an event; replay answers from the recorded cache.
- **Persistence** — event sinks, SQLite store, manifests recording version, scenario
  fingerprint, provider, seed, and model parameters; replayable run artifacts
  (`events.jsonl`, `results.sqlite3`, `run.json`, `metrics.json`, `provider-usage.json`,
  frozen `inputs/`).
- **Orchestration** — whole-run runner with atomic nine-stage tick commit, checkpoints,
  interruption recording, and an optional per-tick observer seam.
- **Experiments** — the metrics fold (every value with numerator, denominator, and
  sources), paired A/B comparisons under common random numbers with an A/A exactly-zero
  guarantee, no-campaign anchoring, and one-knob sensitivity sweeps with rank-flip
  reporting.
- **CLI** — `init`, `validate`, `population generate`, `campaign import`, `run`,
  `replay`, `compare`, `doctor`, `report`, `demo` under the global
  `--format human|json|jsonl` output contract with documented exit codes.
- **Live dashboard** — a read-only Textual TUI (agent table, details and memories,
  bounded event stream, metrics strip) driven through the same loop as headless runs,
  with pause/step/speed/filter controls and headless fallback off-terminal.
- **Reports** — self-contained offline HTML reports (inlined Plotly, no remote
  resources, escaped chart payloads) with the synthetic-data disclosure, full metrics
  provenance, fictional diaries, and the reproduction manifest.
- **Documentation** — ODD protocol, model card, pre-registered experiment protocol,
  limitations, architecture, reproducibility, quickstart, CLI reference, and the
  five-minute demonstration script.

### Security

- Untrusted-input posture for campaign and population files (safe YAML, strict schemas,
  project-root path containment); minimised provider prompts; secret redaction in logs;
  reports escape user-provided text and embed no remote scripts.

[Unreleased]: https://github.com/mahanfatehian/adlife-sim/compare/0.1.0...HEAD
[0.1.0]: https://github.com/mahanfatehian/adlife-sim/releases/tag/v0.1.0
