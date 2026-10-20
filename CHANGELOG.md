# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- **Two-part live demonstration script** — [docs/investor-demo.md](docs/investor-demo.md) is
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
  [docs/investor-demo.md](docs/investor-demo.md) shows the GapGPT start command and profile.
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
