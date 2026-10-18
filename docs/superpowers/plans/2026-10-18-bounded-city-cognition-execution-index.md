# Bounded City Cognition V10 Execution Index

This index orders the implementation plan suite for
`docs/superpowers/specs/2026-10-18-bounded-city-cognition-design.md`. It is not a substitute
for the task-level red/green instructions in each plan.

## Required order

1. `2026-10-18-bounded-city-cognition-core-implementation.md`
2. `2026-10-18-bounded-city-cognition-runtime-implementation.md`
3. `2026-10-18-bounded-city-cognition-experience-implementation.md`

The core plan freezes scientific types, hashes, deterministic policies, dual-state
evaluation, cross-platform goldens, and manifest V10. The runtime plan then adds hardened
provider transport, V2 cache/service, schema-4 binding, atomic V10 storage, workbench job
activation, and offline replay. The experience plan consumes only those committed contracts
to add metrics, studies, reports, read-only APIs, workbench controls, the saved-run
inspector, packaging proof, and the upgraded local preview.

## Approval checkpoint

After this plan suite is committed and pushed, present the specification, this index, and
all three implementation plans to the repository owner. Do not change product code until
the owner has reviewed the written suite and explicitly confirmed that it captures the
intended upgrade. The requested execution method is native task-by-task implementation in
this workspace; it does not need to be asked again after plan approval.

## Cross-plan gates

- Read the V10 specification and all three plans before starting implementation.
- Execute tasks in numeric order within each plan and finish one plan before the next.
- A task begins with a failing externally meaningful test and ends only after its narrow and
  related suites pass.
- Keep schema-4 submission disabled until pure V2 preparation, exact V10 persistence, and
  repository/job publication are all available in one coherent commit.
- Keep V1-V9 parsing, artifacts, prompts, cache namespaces, replay, metrics, reports, and UI
  behavior unchanged unless a named regression test proves an intentional additive dispatch.
- Inspect the staged path list before every commit. Never use a broad `git add` in this shared
  worktree, and never continue to commit or push after a failed command.
- Push only focused passing commits to the existing `origin/main`; never add or change a
  remote or global Git configuration.
- Do not stop the current port-50050 preview until the final built code is verified and its
  background replacement is ready.

## Shared interface ledger

- Causal identity: `CityCognitionCausalContractV2`,
  `CityCognitionCausalRequestIdentityV2`, and `CityCognitionRequestV2.request_id` exclude
  descriptive provenance; `request_record_sha256` binds the complete internal request and
  full run provenance.
- Provider authority: `CityCognitionResultV2` contains only the exact closed category vector,
  echoed causal request ID, safety flags, and disclosure.
- Pure application: `apply_spatial_cognition_v2(...) -> SpatialResponseEvaluationV2` embeds
  the unchanged V1 baseline and updates only `SpatialCognitionStateV2`.
- Publication: exact workbench schema `4` pairs only with manifest `10`; manifest dispatch
  chooses every V2 artifact parser before reading untrusted sidecars.
- Replay: V10 regenerates requests from frozen run/scientific inputs, consumes frozen terminal
  evidence and policies, and has no provider, cache, credential, environment, or network
  dependency; it never writes the source run.
- Presentation: metrics and studies compare only the rule baseline. Provider-dependent
  cognition remains a separate exploratory evidence family with no purchase or campaign-
  effect delta.

## Final integration gate

After all three plans are green, run the repository-wide lint, formatting, mypy, full test,
two hash-seed, branch-coverage, wheel, clean-room smoke, optional frozen-build, browser,
security, performance, and user-facing CLI gates listed in the experience plan. Only then
replace the background preview and hand off `http://127.0.0.1:50050/` with the verified
service still running.
