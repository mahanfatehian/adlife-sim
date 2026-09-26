# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Announcement-readiness hardening** — regression coverage for hybrid provider
  identity, non-destructive replay, the cognition fallback across ticks, the CLI
  error boundary, and documentation consistency (tick scale pinned by a doc test).
- `ADLIFE_DEBUG=1` requests a full traceback for any command failure; expected
  failures now print a concise diagnostic only (see Fixed).

### Fixed

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
