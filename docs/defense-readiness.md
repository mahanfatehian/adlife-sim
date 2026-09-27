# University-defense readiness audit — 2026-09-27

This is a verification record for the audited code, not a claim of real-world predictive
validity or a substitute for rerunning the gates after later changes. The audit began at
`3ae4081` and retained the adapter-free core, existing golden fixtures, package version,
dependency lock, synthetic-data disclosure, and 85% coverage floor with branch measurement.

## Certification evidence

Local environment: Windows x86-64, CPython 3.12.11, uv-managed environment. Commands ran
without visible desktop terminals. The local subprocess-hiding harness is not part of the
package or repository changes.

The complete post-fix matrix passed. All Python/project commands used `uv`.

| Gate | Recorded result |
| --- | --- |
| `uv sync --locked --all-groups` | 71 packages resolved; 61 checked |
| `uv run ruff format --check .` | 192 files already formatted |
| `uv run ruff check .` | All checks passed |
| `uv run mypy src` | No issues in 86 source files; strict configuration unchanged |
| `uv run pytest -q` | 3,049 passed, 11 skipped; 490.26 seconds |
| Full pytest with `PYTHONHASHSEED=0` | 3,049 passed, 11 skipped; 487.70 seconds |
| Full pytest with `PYTHONHASHSEED=12345` | 3,049 passed, 11 skipped; 655.62 seconds |
| `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing` | 3,049 passed, 11 skipped; 1,296.22 seconds; 91.13% coverage, above the unchanged 85% floor |
| Packaging tests within that suite | 51 passed, 4 platform skips |
| TUI and live/headless tests | 25 TUI tests plus 2 integration tests passed |
| Security corpus and architecture boundary | 808 security tests and 79 architecture tests passed |
| `uv build --no-sources` | Wheel and source distribution built successfully |
| Exact built-wheel clean-room smoke | Passed offline with checkout `PYTHONPATH` cleared; report size 4,862,319 bytes |

The remaining 11 skips are seven file-symlink privilege cases on Windows and four POSIX
installer cases. Windows directory-junction containment tests did run. The former
unemployed-weekday model skip was removed: it first reproduced `InvalidMovement`, and the
repaired template now passes the real resolver test for both supported home/work pairs.
No model-failure exception remains hidden by that skip.

The final maximum-run check was repeated alone after the full matrix:

- 30 agents, seven days, rules mode; 5,685 events.
- 23.30 seconds for the untraced timed pass, below the existing 40-second gate. The
  aspirational 10-second target was not met; no timing threshold was changed.
- 2 MiB peak traced Python allocation (rounded, not process RSS), measured in a separate
  pass; 3,629,056-byte SQLite database and 4,862,947-byte HTML report.
- The complete two-pass benchmark/report test passed in 94.90 seconds.

Verified wheel: `dist/adlife_sim-0.1.0-py3-none-any.whl`.

```text
SHA256 a33c6de743996c1726f95408773c1359916f0671e638e006888b90a7e76b59d3
```

The smoke command was `uv run python scripts/smoke_release.py
dist/adlife_sim-0.1.0-py3-none-any.whl`, with `UV_OFFLINE=true` and no inherited checkout
import path. It installed that wheel into a fresh environment and exercised version,
offline doctor, initialization, a rules run, and reporting outside the checkout.

The Windows x86-64 PyInstaller bundle also built successfully. It was exercised from a
fresh temporary directory with the checkout import path cleared: version, offline doctor,
initialization, a one-day/two-person rules run, report, and headless demo all exited 0.
Native doctor passed seven checks and warned that its captured output stream used cp1252;
the source command passed all eight checks. This is an environment qualification, not a
claim that every Windows console is UTF-8. The bundle analysis contains no audit harness.

## Fresh demonstration evidence

- Source `--help`, `--version`, offline doctor, JSON offline doctor, headless demo, and
  JSON headless demo all exited 0. JSON commands emitted one parseable document with no
  diagnostic contamination of stdout.
- Both source and native demos completed 4,320 simulated minutes and 2,042 events with
  run ID `demo-7bf6d3531c96d7632d7cd047`. Their frozen `inputs/scenario.json`,
  `events.jsonl`, and `metrics.json` were byte-identical across separate temporary folders.
- A fresh `university-study` initialized and validated successfully. The requested
  three-day, 20-person, seed-42 phone run completed with 1,907 events; dedicated replay
  returned `replay-identical: true`. The matching billboard command completed with 1,781
  events and clean JSON output.
- The phone HTML report was 4,862,901 bytes. Both embedded scripts compiled in a JavaScript
  parser, chart JSON parsed with finite numeric values, and no remote-resource tags were
  present. This is syntax/data verification, not visual browser inspection.
- A five-seed A/A comparison was exactly zero across all 19 metrics, including its
  intervals and effect sizes, with stable-null agreement 1. A five-seed A/B workflow also
  completed using a single campaign-visibility treatment; mean notices changed by -1.2
  and mean notice rate by -0.07058823529411766. These are workflow checks, not evidence of
  a real-population advertising effect.

No macOS/Linux native execution or visible-terminal smoke is claimed. The available
session was noninteractive and the owner requested no desktop windows; Textual pilot and
headless-equivalence tests supplied the automated TUI evidence. The current demo contract
selects a live dashboard automatically in a suitable terminal, not through `demo --live`.

## Defense contracts

- **Repeatability:** fixed inputs, model parameters, seed, code, and cognition identity
  determine rules/mock results. Recorded validated answers are required for exact live
  hybrid replay. The offline demo has a destination-independent identity.
- **Cognition authority:** a bounded modifier adjusts rule-derived sentiment/recall;
  sharing probability controls the sharing draw; relevance determines memory salience.
  Provider valence and credibility are recorded but do not currently drive numeric state
  updates. Narrative text and reported deltas do not replace numeric rule formulas.
  Purchase probability is always computed by the deterministic purchase rule. Earlier
  cognition-influenced state may affect later rule outcomes.
- **No provider execution authority:** validated answers cannot choose movement, spend
  budget directly, select arbitrary people/campaigns, invent event types, or substitute a
  different request identity. Invalid answers follow bounded repair/fallback paths.
- **Atomicity:** core planning/commit protects model state. SQLite transactions protect
  stored batches. These are not one distributed transaction with export files and UI
  observers; failures are recorded or raised explicitly. An observer receives committed,
  persisted ticks and is detached if it raises an ordinary exception.
- **Artifact recovery:** SQLite is authoritative. A divergent JSONL export is detected,
  further extension is refused, and explicit rebuilding is deterministic and atomic.
  Replay never writes to its source and never repairs a mismatched result silently.
- **Replay scope:** completed runs and trustworthy interrupted prefixes can be checked.
  Failed/running artifacts without that boundary are refused. Core in-place recovery is
  restricted to supported running artifacts exactly at their start/checkpoint boundary.
- **Integrity versus authentication:** hashes, stable IDs, cross-references, and redundant
  artifact checks detect inconsistency. Artifacts are not cryptographically signed against
  a coordinated rewrite by their owner.
- **UI independence:** pause, speed, and step affect scheduling/presentation only. A paused,
  stepped, resumed run is tested against the exact headless event stream.
- **CLI categories:** 0 success; 1 internal defect; 2 invalid input/configuration/design;
  3 artifact conflict; 4 corrupt/incompatible/missing artifact or recorded cognition;
  130 interruption. Expected failures are concise; JSON modes keep stdout parseable.
- **Self-contained reporting:** bundled Plotly, styles, and script-safe chart JSON require
  no external resource fetch. Text is escaped, and report replacement is atomic.
- **Offline scope:** rules, mock, demo, replay of recorded answers, reporting, and offline
  doctor do not require a provider API. Installing dependencies on a fresh machine still
  requires either an available package cache or an installation source.

## Regression map

The substantive behavioral fixes were first demonstrated by failing tests, then checked
with narrow/related suites before the final matrix. Documentation-only corrections
describe tested behavior rather than adding features.

| Boundary | Defects corrected | Main regression evidence |
| --- | --- | --- |
| Cognition | Wrong request identities; malformed cache encoding; numeric-only campaign IDs breaking fallback; exact credential echoes including escaped/malformed JSON | `tests/unit/cognition/test_influence_contract.py`, `test_service.py`, `test_openai_compatible.py`, `test_cache.py`; `tests/security/test_redaction_corpus.py`; `tests/unit/simulation/test_commit_pipeline.py` |
| Input/domain | Coercive settings/version tags; unvalidated copy updates; mutable manifest parameters; missing campaign references; ambiguous/excessive YAML and unsafe diagnostics | `tests/unit/config/test_defense_inputs.py`; `tests/contract/test_defense_contracts.py`, `test_schema_versions.py`; `tests/cli/test_audit_contracts.py` |
| Runner/storage | Observer/cancellation ordering; early failures; swallowed persistence failure; unsafe resume boundaries; truncated identity collisions; path and event/checkpoint integrity gaps | `tests/integration/test_runner_failures.py`, `test_sqlite_store.py`; `tests/unit/simulation/test_runner_identity.py` |
| CLI/replay | Mixed exit-code meanings; unclean machine errors; path/output overwrite risks; replay metadata/cache/provenance mismatches; unsafe broad text normalization; legacy replay mode; demo destination dependence | `tests/cli/test_error_boundary.py`, `test_audit_contracts.py`, `test_replay_safety.py`, `test_run_identity.py`; `tests/integration/test_demo.py` |
| Comparison | Folder labels incorrectly treated as scientific changes; truncated comparison IDs colliding; incomplete parameter provenance; seed/input ordering; sensitivity validation; zero-heavy directional agreement | `tests/cli/test_compare.py`; `tests/integration/test_paired_experiment.py`, `test_sensitivity.py`; `tests/unit/experiments/test_design.py`, `test_metrics.py` |
| Social selection | Zero total similarity weight selecting the last neighbor rather than using the keyed uniform draw | `tests/unit/simulation/test_social.py` |
| Routine resources | A skipped weekday test hid an impossible cafe-to-highway return leg in the unemployed template; the return now uses the existing home route, with no movement-validation exemption | `tests/integration/test_routine_routability.py` (skip removed); routine, population, and golden suites |
| TUI | Queued-step wakeup; pause/resume scheduling; unbounded/incorrect observer aggregates | `tests/tui/test_controller_contract.py`, `test_event_bus.py`; `tests/integration/test_live_headless_equivalence.py` |
| Reports | Autoescaped executable chart assets; missing comparison chart block; incorrect sentiment/diary/campaign attribution; unsafe replacement after write failure | `tests/unit/reporting/test_html.py`; existing report-injection security tests |
| Packaging/release | Deleted temporary path returned by smoke; unnecessary interpreter download; Windows subprocess windows; nonportable installer harness; publication preceding asset verification/rebuilding different artifacts; audit targeting the wrong environment | `tests/packaging/test_smoke_release.py`, `test_release_workflow.py`, `test_installer.py`, `test_wheel.py`, `test_task5_resources.py` |
| Architecture/assertions | Incomplete import-boundary detection and assertions allowing both an expected refusal and an internal defect | `tests/architecture/test_core_import_boundary.py`; exact CLI/replay/demo assertions |

## Intentional scope and research limitations

- All people, campaigns, and observations are synthetic. No calibration, survey evidence,
  real-population accuracy, or causal advertising-effect claim is implied.
- The standard CLI/demo initializes no active purchase need and zero disposable budget;
  its purchase count is therefore structurally zero. Purchase-enabled core scenarios are
  tested, but this audit deliberately did not invent a new initialization model.
- Sentiment, recall, intention, fatigue, and social proof are shared agent state, not
  independent per-campaign scores. Campaign exposure counters and awareness are separate.
- Generated unemployed profiles retain the historical office-worker schedule proxy;
  explicitly supplied populations can use the separate, routable unemployed template.
  Occupational schedules are not empirically calibrated.
- Phone/billboard comparisons include different exposure opportunities; they are not an
  isolated channel-effect estimate.
- Paired seeds/common random numbers support comparison within this model. Bootstrap
  intervals and directional stability describe model-run variation, not real populations.
  Zero differences support neither nonzero direction; an all-zero A/A result is explicitly
  stable null consistency. The majority direction can differ from an outlier-driven mean.
- Fresh live-provider answers can vary and fail. Exact reproduction uses recorded validated
  cognition, not a claim that a remote model is deterministic.
- Credential/PII-pattern screening is defense in depth, not proof that arbitrary user text
  is non-sensitive. The configured provider credential additionally receives exact-value
  screening before diagnostic, repair, or cache exposure.
- CSV remains intentionally de-scoped; supported report surfaces are JSON and self-contained
  HTML. Historical plans now identify that scope explicitly.

See [reproducibility](reproducibility.md), [architecture](architecture.md),
[model card](methodology/model-card.md), [limitations](methodology/limitations.md), and
[experiment protocol](methodology/experiment-protocol.md) for the full public contracts.
