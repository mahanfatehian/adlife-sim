# Reproducibility

Reproducibility is a tested property of this software, not a hope. The same seed, the same
scenario, and the same code produce byte-identical events and byte-identical final state —
and `adlife replay` verifies it on demand.

## What a seed guarantees

A seed fixes every stochastic decision in the run:

- **Keyed random streams.** There is no global random generator. Each agent's decisions —
  movement draws, attention draws, share draws, purchase draws — come from a random
  oracle keyed by run, agent, and purpose. Adding an agent or changing an unrelated
  subsystem cannot shift another agent's stream.
- **Deterministic scheduling.** The clock advances one simulated minute per tick; stage
  and event ordering are total orders, not iteration orders.
- **Recorded inputs.** The run's `inputs/` directory freezes the exact scenario — world,
  population, routines, campaigns, model parameters — the run was driven with, not the
  files it was *later edited into*.

Consequently: re-running with the same seed and inputs reproduces the run exactly;
changing *only* the seed changes draws and nothing else; changing *only* a campaign file
changes exactly the campaign-related behaviour. That last property is what makes paired
comparisons honest.

## The manifest

Every run persists `runs/<run-id>/run.json` recording:

- the code version and the exact model parameters in force (`ModelParameters` with every
  documented knob);
- the scenario fingerprint (a hash over the frozen inputs);
- the provider, model identifier, and seed;
- run timing and final status.

A result is auditable against its own manifest, not against anyone's memory of the
configuration.

## Replay: the guarantee, checked

```bash
uv run adlife replay my-study <run-id>
```

Replay re-executes the stored run from its recorded inputs through the same drive loop and
compares the fresh event stream to the recorded one. The output says
`"replay-identical": true` only when the streams match event for event. A mismatch is
reported as a failure, never repaired. This is also how the live dashboard is kept honest:
a run driven under the TUI is verified to leave an event stream equal, event for event, to
a headless run of the same scenario and seed.

## Environment hygiene

Two sources of nondeterminism outside the model are handled:

- **Hash iteration order.** The test suite runs under varying `PYTHONHASHSEED` values to
  catch any iteration-order leak. If you hack on the core, do the same
  (`PYTHONHASHSEED=0 …` then `PYTHONHASHSEED=12345 …`).
- **Language-model providers.** In hybrid mode a live provider is nondeterministic. The
  boundary is explicit: provider answers never touch purchase probability; every fallback
  is recorded as a `cognition.fallback` event; and replay of a hybrid run answers from the
  recorded cache, so the *artifact* remains reproducible even when the provider was not.

## Recipe: reproduce any run

```bash
# from the manifest, read the seed, the parameters, and the scenario hash
cat my-study/runs/<run-id>/run.json

# re-execute and verify
uv run adlife replay my-study <run-id>

# or re-run fresh with the same overrides
uv run adlife run my-study --seed <seed> --run-id <new-id>
```

The fresh run's `events.jsonl` is byte-identical to the recorded one. If it is not, that
is a bug, and the pair of artifacts is the bug report.
