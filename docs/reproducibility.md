# Reproducibility

The event-stream guarantees below apply to **zone advertising campaign runs**. Saved
city mobility runs have a separate full-minute trace hash and `city-replay` command,
described at the end of this document; they are not event-sourced campaign runs.

In rules mode, the same seed, frozen scenario, model parameters and code produce the
same normalized events and final state. Mock cognition additionally uses the original
request identity and complete request JSON to select its deterministic fixture; replay
preserves those inputs. Live-provider replay requires recorded validated cognition.

The offline `demo` command fixes its scenario identity independently of the destination
folder and derives its run identity from canonical frozen inputs and the seed. Repeating
the same packaged demo inputs, seed and code in a fresh folder therefore preserves mock
request identities, scenario, event and metric exports. Ordinary study identities still
record their source directory; use recorded replay to reproduce those runs.

Replay normalizes only structured run/event references for its new destination; it
does not erase differences in arbitrary narrative text. Wall-clock timestamps, platform
metadata and SQLite file layout are not reproducibility targets. Entire artifact
directories are **not** promised byte-identical. The JSONL export rebuilt from the same
authoritative database is byte-identical to its canonical event export.

## What a seed guarantees

A seed fixes the model's keyed random draws for fixed inputs:

- **Keyed random streams.** There is no global random generator. Each agent's decisions —
  movement draws, attention draws, share draws, purchase draws — come from a random
  oracle keyed by seed, purpose, agent, simulated tick and draw index. Adding an agent
  does not consume another agent's draws, but changing social interactions can still
  change outcomes.
- **Deterministic scheduling.** The clock advances in fixed ticks of 15 simulated minutes
  (96 ticks per simulated day); stage and event ordering are total orders, not iteration
  orders.
- **Recorded inputs.** The run's `inputs/` directory freezes its scenario — world,
  population, routines and campaigns. Model parameters live in the manifest (a null
  mapping means the recorded package's defaults), not in later-edited project files.

For a fixed scenario, changing the model seed changes draws; changing a campaign can
propagate into later memory, social state and purchasing rules. CLI population generation
also uses the supplied seed unless a population document is supplied. Paired experiments
hold the already-loaded initial population and all non-treatment inputs fixed.

## The manifest

Every campaign run persists `runs/<run-id>/run.json` recording:

- the code version and the exact model parameters in force (`ModelParameters` with every
  documented knob);
- the scenario fingerprint (a hash over the frozen inputs);
- the provider, model identifier, and seed;
- run timing and final status.

A result is auditable against its own manifest, not against anyone's memory of the
configuration.

Git and lockfile metadata use explicit unavailable placeholders when a study is outside
a checkout or has no lockfile. Preserve the exact wheel/source revision and dependency
lock alongside a reported study; placeholders are not proof of an identical environment.

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

Interrupted runs verify their recorded committed prefix, including a stop before the
first tick. Running or failed artifacts without a trustworthy interrupted boundary are
refused. Replay never writes to the source directory. Core-only in-place recovery is
limited to still-running rules/mock artifacts exactly at a stored checkpoint (or their
start event); closed artifacts and uncheckpointed tails are not resumed in place.

## Environment hygiene

Two sources of nondeterminism outside the model are handled:

- **Hash iteration order.** The test suite runs under varying `PYTHONHASHSEED` values to
  catch any iteration-order leak. If you hack on the core, do the same
  (`PYTHONHASHSEED=0 …` then `PYTHONHASHSEED=12345 …`).
- **Language-model providers.** Fresh live answers can vary despite identical seeds.
  Bounded modifiers and social/memory inputs affect numeric state; later rule-derived
  intention and purchase outcomes can change too. The provider never supplies purchase
  probability directly, and reason text does not drive numeric formulas. Exact hybrid
  reproduction requires recorded validated cognition and fallback provenance; fresh
  provider calls are not a reproducibility check.

## Recipe: reproduce a campaign run

```bash
# from the manifest, read the seed, the parameters, and the scenario hash
cat my-study/runs/<run-id>/run.json

# re-execute and verify
uv run adlife replay my-study <run-id>
```

Replay re-executes the run and compares the fresh stream to the recorded one with the run
identifier references normalized away. A mismatch indicates changed code, missing or
corrupt recorded inputs/cognition, or a defect; it is never silently repaired. Integrity
checks detect inconsistent artifacts, not a coordinated rewrite of every file: runs are
not cryptographically signed or claimed to be tamper-proof against a malicious owner.

## Saved city mobility traces

`adlife city-run PACK --output-root ROOT --run-id ID` or the catalog form
`adlife city-run --city-id fictional-grid-v2 ...` writes a distinct artifact at
`ROOT/city-runs/ID/`. Catalog selection is offline and content-addressed: the packaged
v2 resource's canonical SHA-256 and public metadata are checked before the run starts.
The only bundled catalog pack is explicitly fictional. A selected pack is then frozen
inside the run, so replay does not consult the current catalog and cannot drift when a
catalog is later replaced.

The artifact freezes the city pack (including v2 road geometry, direction, source
provenance, bounds, time zone and omissions), generated fictional assignments, seed,
duration, model/runtime identity and the SHA-256 digest of **every** normalized minute
frame in order. Versioned run manifests pair v1 packs with the v1 mobility model and v2
packs with the v2 model; incompatible pairings are refused rather than coerced.
`adlife city-replay ROOT ID` validates those inputs and regenerates the complete trace
without changing source files; a mismatch, partial publication or incompatible artifact
is refused. `adlife city-view ROOT ID` performs the same load verification before
opening the read-only local timeline. A digest proves equality against that artifact,
not authenticity against an owner rewriting all files. Identical frames are expected
on a compatible implementation/runtime; cross-platform bitwise identity of
floating-point interpolation is not claimed.
