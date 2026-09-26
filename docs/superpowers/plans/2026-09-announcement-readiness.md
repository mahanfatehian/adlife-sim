# Announcement Readiness — 2026-09

**Goal:** take the repository from release-candidate state to public-announcement-ready
quality in one complete execution: fix the known defects, audit the whole public surface,
verify every gate, and leave focused local commits (no remote, no push, no publish).

**Constraints:** `uv` only; the adapter-free `src/adlife/core` boundary is preserved;
test-driven fixes; determinism, security, disclosure, typing, coverage and packaging
gates are never weakened; no Git remote is added and nothing is pushed or published.

**Method:** every behavioral fix lands test-first — a regression test that fails against
the previous behavior, then the minimal robust fix, then the focused tests, then the
wider suite.

---

## Fix 1 — Hybrid/provider run identity metadata

The CLI records the manifest's provider as `provider="rules" if mode == "rules" else
"mock"`, so a hybrid run answering through a real local (Ollama) or remote
OpenAI-compatible provider is persisted as `mock`, and a replay run is persisted as
`mock` rather than `replay`. The domain vocabulary already exists:
`RunManifest.provider` is `Literal["rules", "mock", "local", "remote", "replay"]` and
per-answer `ProviderKind` is `rule|mock|replay|local-llm|remote-llm|fallback`.

- [x] Add a regression test: a hybrid run through a real configured provider
      (loopback local provider) cannot be persisted with manifest provider `mock`;
      the manifest records `local`/`remote` per the configured endpoint.
- [x] Add a regression test: a replay run is recorded as `replay`, not `mock`.
- [x] Add a regression test: rules stays `rules` and mock stays `mock` (no behavior
      change for deterministic modes).
- [x] Fix `adlife run` to derive the manifest provider from the actual wiring
      (mode + configured provider settings), using the existing vocabulary only.
- [x] Fix the stale-fallback crash: a hybrid run whose provider fails in tick 2+
      must use that tick's rule inputs (per-tick fallback refresh on the persisted
      service, budgets intact) instead of raising UnknownCognitionRequest.
- [x] Verify run JSON output, reports, and provider-usage artifacts agree with the
      manifest (live TUI path and demo path wired to the same identity).
- [x] Confirm the same-seed rule/mock runs still produce byte-identical streams
      (golden + metamorphic suites).

## Fix 2 — Replay is non-destructive

`adlife replay` deletes an existing `replay-<run-id>` directory with `shutil.rmtree`
before re-running. Replay must never silently destroy artifacts.

- [x] Add a regression test: an existing replay artifact is not silently deleted;
      the command refuses the conflicting destination (exit 3 run conflict) or
      isolates a unique replay workspace, per the run-store contract.
- [x] Add a regression test: the source run artifacts are byte-for-byte untouched
      by replay (hash `events.jsonl`, `results.sqlite3`, `run.json`, `metrics.json`,
      `provider-usage.json` before and after).
- [x] Add a regression test: replay still verifies deterministic event equality and
      emits `"replay-identical": true`.
- [x] Remove the destructive delete; replace with the safe design above.
- [x] Confirm no other command silently destroys existing artifacts.

## Fix 3 — Tick-duration documentation reconciliation

The implementation and specification say one tick is **15 simulated minutes**
(`SimClock(duration, tick_minutes=15)`, `tick_minutes: Literal[15]`), but several
documents claim a one-minute tick / 1,440 ticks per day. The implementation is the
source of truth (the spec and the clock tests pin 15).

- [x] Grep the whole repository for one-minute-tick language and fix every hit:
      README, architecture, quickstart, reproducibility, ODD protocol, CHANGELOG,
      source comments.
- [x] Keep the terminology straight everywhere: a *simulated-minute timestamp*
      (e.g. `final_minute: 1440` = end of day 1) is not a *tick duration*
      (15 simulated minutes) and not *ticks per day* (96 for a 1,440-minute day).
- [x] Update or add a documentation test so the contradiction cannot quietly return.

## Fix 4 — Stale README/project status

The README status section calls orchestration, persistence, and presentation "in
progress" while its own capability table marks them complete, and the roadmap cites
internal plan task numbers.

- [x] Rewrite the status section to describe what is implemented today, without
      exaggeration; separate the open-source research instrument from remaining
      release/publication work and the future commercial direction.
- [x] Remove internal implementation-plan task references from the roadmap.
- [x] Keep the disclosures and the no-external-validity stance intact.

## Fix 5 — CLI expected-error behavior

`command_boundary` prints a full Python traceback for every expected user/domain
error. Expected errors must be concise, professional, and exit with the documented
codes; tracebacks belong to unexpected internal defects (and to an explicit
verbose/debug mode).

- [x] Add tests: expected errors (invalid YAML/config, bad input, duplicate run,
      provider config, corrupted artifact) print a concise diagnostic on stderr with
      NO traceback; human and JSON modes both covered; JSON stdout stays one clean
      document.
- [x] Add tests: unexpected internal defects may show diagnostic detail on stderr;
      exit codes 1/2/3/4/130 preserved.
- [x] Implement: no traceback for expected errors by default; support
      `ADLIFE_DEBUG=1` (or an explicit debug flag) to request full diagnostics.
- [x] Preserve clean JSON/JSONL stdout in all cases.

## Fix 6 — Remove internal plan language from public surfaces

- [x] `adlife run --help`: `--live` help says "(Task 15)" — remove the task ref.
- [x] Source comments/docstrings visible in public surfaces: replace "Task N" and
      "the coming CLI" phrasing with self-contained descriptions (public-facing
      help strings, error messages, docstrings of user-visible modules first).
- [x] README roadmap: no implementation-plan task references.
- [x] Historical planning docs (docs/superpowers/*) may keep task references.
- [x] Sweep for other stale scaffolding wording in help text and docs.

## Deep announcement-readiness audit

- [x] Documentation consistency: cross-check README, CHANGELOG, architecture, CLI
      reference, installation, quickstart, reproducibility, methodology docs against
      actual CLI behavior and implementation; reconcile every disagreement
      (replay-refusal documented, ADLIFE_DEBUG documented, reproducibility recipe
      corrected to event-for-event equality, exit-code table verified).
- [x] CLI polish: `uv run adlife --help`, `--version`, `doctor --offline`,
      `demo --headless` — fixed `--run-id` help being swallowed as rich markup;
      verified exit behavior, NO_COLOR handling, JSON cleanliness, cp1252 advice.
- [x] Reproducibility/determinism: same seed+scenario deterministic (golden +
      metamorphic green after every change); replay works; rules and mock are
      network-free (security suite's offline network ban); provider metadata changes
      do not alter simulation decisions (golden records unchanged); manifest/
      fingerprint semantics coherent.
- [x] Persistence/artifact integrity: audited every write/delete path (replay refusal
      fixed; store writes are atomic temp+rename; report overwrites only its own
      derived HTML; demo refuses existing directories).
- [x] Provider/cognition safety: keys resolve only from env/hidden prompt, never
      enter manifests/cache/reports; local mode ignores ADLIFE_API_KEY by design;
      remote http refused (InsecureProviderUrl); fallbacks recorded as events;
      purchase probability stays rule-only (pinned by tests); cache keys exclude
      secrets (redaction corpus green).
- [x] Security: safe YAML, path containment, symlink protection, HTML escaping,
      secret redaction, bounded provider responses, malformed JSON handling, corrupt
      artifact detection — tests/security + tests/contract green (1494 passed).
- [x] Packaging: pyproject pinned, wheel resources, PyInstaller spec, installers,
      CI matrix, signed-tag release workflow, clean-room smoke script; hosted-runner
      and OS-specific steps documented rather than claimed locally verified.
- [x] Public-announcement review as a maintainer: fresh-eyes pass over README,
      quickstart, claims, status, terminology; honesty checks strengthened.

## Verification gates

- [x] `uv sync --locked --all-groups`
- [x] `uv run ruff format --check .` (179 files formatted)
- [x] `uv run ruff check .` (all checks passed)
- [x] `uv run mypy src` (86 source files, no issues)
- [x] `uv run pytest -q` (2810 passed, 6 skipped)
- [x] `PYTHONHASHSEED=0 uv run pytest -q` (2810 passed)
- [x] `PYTHONHASHSEED=12345 uv run pytest -q` (2810 passed)
- [x] `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`
      (90.15% branch against the 85% floor)
- [x] `uv build --no-sources` (wheel + sdist)
- [x] `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`
      (clean-room venv, whole offline workflow, 5.4 MB self-contained report)
- [x] `uv run adlife --version` (adlife 0.1.0)
- [x] `uv run adlife doctor --offline` (7/8; the UTF-8 check reports the cp1252
      console with the documented `PYTHONUTF8=1` advice — environmental, not a defect)
- [x] `uv run adlife --format json demo --headless` (offline, JSON contract held)
- [x] `git diff --check` (clean)

## Closure

- [x] Update CHANGELOG.md under the Unreleased section for all readiness work.
- [ ] Focused local commits per logical group; no remote, no push, no publish.
- [ ] Final release-readiness report ending with ANNOUNCEMENT READY or
      NOT ANNOUNCEMENT READY.
