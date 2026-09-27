# University defense readiness audit

**Goal:** verify the existing research instrument and repair demonstrated defects without
expanding its scope. The September 13 design and implementation documents are historical
inputs; executable contracts and the current university-defense requirements govern this audit.

**Method:** reproduce each behavioral defect with a failing regression, make a narrow fix,
run related checks, then verify the integrated repository. Keep the core adapter-free,
golden results intact, fictional data only, and the 85% coverage floor unchanged.

- [x] Reconcile bounded cognition, purchase-rule ownership, provider security, fallback,
      and recorded-answer replay; inspect cognition adapters, ports, and decision policy.
- [x] Make CLI failure categories coherent and machine output clean; exercise every
      command, malformed inputs, duplicate artifacts, and replay source preservation.
- [x] Verify domain immutability, strict configuration, safe YAML, cross references,
      deterministic scheduling, tick atomicity, and failure propagation.
- [x] Verify SQLite/export repair, corruption detection, checkpoints, and interruption.
- [x] Verify paired experiments, metrics provenance, sensitivity, report injection safety,
      TUI observation/control, and maximum-run performance.
- [x] Repair the clean-room smoke return contract and release dependency graph; inspect
      resources, installers, wheel independence, and the locally supported frozen build.
- [x] Reconcile stale CSV/design promises and public methodology with implemented scope.
- [x] Run locked sync, formatting, lint, strict typing, full tests, both hash seeds,
      branch coverage, wheel build/smoke, and a fresh offline university demo workflow.
- [x] Review the integrated diff and record exact evidence and remaining research limits
      in `docs/defense-readiness.md`.

Handoff: focused commits use the existing owner identity. The owner's final instruction
explicitly authorizes pushing those commits to the existing origin; no remote, tag, or
public release is created. Actual commit/push results belong in the final maintainer report.

Review focus: untrusted nested input, provider-boundary validation, storage failure after
commit, interruption during presentation, and installed-package execution outside checkout.
Independent cognition, CLI, and packaging reviews run in parallel; the maintainer reviews
their diffs and runs all final gates together.
