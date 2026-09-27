# CLI reference

Commands honor the global options; every failure
exits with one of the documented codes and, in JSON modes, emits an error object on
stdout.

## Global options

Set on the root command, before the subcommand:

```bash
adlife --format human|json|jsonl --no-color <command> …
```

| Option | Meaning |
| --- | --- |
| `--format` | `human` (default) prose; `json` one machine-readable document on stdout; `jsonl` one JSON object per line for streams. |
| `--no-color` | Disable colored output. `NO_COLOR=1` and `TERM=dumb` are honored automatically. |
| `--version` | Print `adlife <version>` and exit. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Internal error (a defect, not your input). |
| 2 | Invalid input: refused configuration (including provider credentials or URLs), bad file, broken scenario, refused flag combination. |
| 3 | Run conflict: a run identifier already exists, or an operation would overwrite an artifact. |
| 4 | Artifact error: a stored run or required cognition cache record is missing, corrupt, or does not reproduce. |
| 130 | Interrupt: an active run records its interrupted artifact before exiting. |

A Python traceback is printed only for internal defects (exit 1) — expected failures such
as invalid input, run conflicts, provider misconfiguration, or corrupted artifacts print a
concise diagnostic on stderr and nothing more. Set `ADLIFE_DEBUG=1` to request the full
traceback for any failure.

## `adlife init NAME [--parent DIR]`

Create a new study directory from the packaged demo project. Refuses to touch a non-empty
target (exit 2). Default parent is the working directory.

## `adlife validate PATH`

Validate a study directory: schema, world/routine routability, campaign containment.
Print a JSON validation result and exit 0 when valid; exit 2 with a diagnostic otherwise.
`--verbose` adds validation detail on stderr. YAML documents are bounded to 1 MiB and
32 nesting levels; aliases, duplicate keys, and custom object tags are refused.

## `adlife population generate [--size N] [--seed N] [--locale LC] [--out FILE] [--as FORMAT]`

Generate a fictional population document. `--size` 1–30 (default 20), `--seed` (default
42), `--locale` `fa-IR` (default) or `en-US`, `--out` writes YAML to a file instead of
stdout, `--as yaml|json` selects the output document format (default YAML).
Global `--format json|jsonl` selects JSON on stdout. An existing `--out` file is
refused with exit 3; machine mode reports the newly written file in a JSON result.

## `adlife campaign import PATH [--project-root DIR]`

Validate one campaign YAML against the project and print the normalized campaign as JSON.
Asset paths inside the campaign are resolved and confirmed to lie beneath the project root
before they are opened; anything else is refused (exit 2).

## `adlife run PROJECT [options]`

Run one study from its first tick to `run.completed`, persisting every artifact.

| Option | Meaning |
| --- | --- |
| `--campaign FILE…` | Replace the project's campaigns with these project-relative YAML files. |
| `--run-id ID` | Lowercase letters, digits and hyphens, beginning with a letter or digit, at most 40 characters. Windows device names are refused on every platform. An existing identifier exits 3. |
| `--mode rules\|hybrid\|replay` | Default `rules` needs no provider; `hybrid` uses the configured provider. The legacy `replay` value exits 2 and directs you to `adlife replay PROJECT RUN_ID`, which has the required frozen source identity. |
| `--days N` | Override simulated days (1–7). |
| `--population-size N` | Override population size (1–30). |
| `--seed N` | Override the run seed. |
| `--live` | Open the live dashboard when stdin and stdout are an interactive terminal and `TERM` is not `dumb`; otherwise fall back to headless. |
| `--headless` | Never open a dashboard (the default behaviour). |
| `--no-headless-fallback` | With `--live`: exit 2 instead of falling back when no terminal is available. |
| `--cache-dir DIR` | Cognition cache directory for hybrid/replay modes (default `cache/` under the study). |

`adlife --format json run PROJECT --live` and the corresponding `jsonl` combination
are refused (exit 2): a dashboard cannot share machine-readable stdout. Generated run
identifiers include a digest of the final scenario, seed and mode, including campaign
overrides. Identical inputs choose the same identifier and an existing run is refused.

## `adlife replay PROJECT RUN_ID [--provider-mode MODE] [--cache-dir DIR]`

Re-execute a stored run from its recorded inputs and verify the fresh event stream against
the recorded one. By default the provider mode is inferred from the source artifact;
`--provider-mode rules|hybrid|mock` explicitly overrides it. Mock replay uses the original
request identity. Hybrid replay validates cached successful answers and reconstructs
recorded rule fallbacks without constructing a live provider or reading credentials. Emits
`"replay-identical": true` on success. Corrupted artifacts exit 4; a mismatch is reported
as a failure, never repaired. A replay destination (`replay-<run-id>`) that already exists
is refused with exit 3: replay never deletes or overwrites run artifacts, and the stored
source run is never modified. Long source identifiers receive a digest suffix in the
derived replay identifier to avoid truncation collisions. Replay uses recorded model
parameters and ignores subsequent edits to project YAML.
For an interrupted source, replay verifies only the committed prefix up to its recorded
stop minute, including a stop before the first tick. A verified prefix exits 0 with
`"status": "interrupted"` and its `final_minute`; it does not execute later cognition.
Running or failed sources lack a supported terminal boundary and are refused with exit 4.

## `adlife compare CONTROL TREATMENT [--seeds N] [--treatment-path PATH]`

Paired whole-run comparison of two studies: both arms run per seed with the same
population, world, and keyed random streams. `--seeds` 1–200 (default: the quick-study
count, 20). `--treatment-path` declares which scenario document the treatment may differ in
(default `campaigns`). The A/A case (comparing a study with itself) must return exactly
zero paired differences.
The CLI normalizes the treatment's directory-derived scenario identifier and display
name to the control's before validation, so separate study directories can be compared.
The output label still names both directories. Population, initial state, world, duration
and all other scientific inputs retain the core's strict paired-design checks; a refused
design exits 2 with a concise diagnostic.
Comparison run identifiers use a bounded digest of the full comparison label, frozen
scenarios, declared treatment paths and seeds. Different comparisons can share a control
study without overwriting earlier runs; repeating the identical comparison exits 3.

## `adlife doctor [PATH] [--offline] [--no-color]`

Report installation readiness: CLI and core versions, packaged resources, provider
availability (mock and rules always; live providers only when not `--offline`), storage
writability, optional project schema check, and console encoding. `--offline` performs no
network access at all. Doctor returns a check report with `all_ok`; inspect that field
for readiness, since completed diagnostic checks return exit 0 even when a check fails.
Live probes refuse credential-bearing URLs and non-loopback plaintext HTTP.

## `adlife report PROJECT RUN_ID [--output FILE]`

Write the self-contained HTML report for a stored run (default destination
`reports/<run-id>.html` under the study). The report is a single file — charts, styles,
and the Plotly runtime inlined — that renders with the network disconnected, opens with a
synthetic-data disclosure, and ends with the manifest block needed to reproduce the run.
An output destination inside the study's `runs/` tree is refused with exit 3.
An existing non-HTML destination is also refused, protecting configuration, population,
campaign documents and other source files from being replaced by report output.
The default report directory must resolve inside the study, including through directory
links or junctions. An explicit `--output` may name a destination outside the study.

## `adlife demo [--headless] [--dir DIR] [--seed N]`

The offline demonstration: build a packaged demo study (20 agents, 3 simulated days, mock
provider, two campaigns), run it, and open the live dashboard when a real terminal
exists — `--headless` prints a summary instead. The study is created in a temp directory
unless `--dir` is given (an existing directory is refused). Requires no network and no
credentials. JSON and JSONL output automatically keep the demo headless.
The demo uses a location-independent scenario identity and a run identifier derived
from its frozen inputs and seed. With the same packaged inputs, seed and code, fresh
demo directories produce identical scenario, event and metric exports. Existing demo
directories are still refused; the destination does not alter mock responses.

## JSON output

`--format json` prints one document per command; `--format jsonl` prints one JSON object
per line for stream-shaped output (events, live progress). Diagnostics always go to
stderr, so `adlife --format json run my-study > doc.json` is clean piped output.
