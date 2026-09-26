# CLI reference

Every command consumes a study directory and honors the global options; every failure
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
| 2 | Invalid input: refused configuration, bad file, broken scenario, refused flag combination. |
| 3 | Run conflict: a run identifier already exists, or an operation would overwrite an artifact. |
| 4 | Artifact corruption: a stored run failed validation when read. |
| 130 | Interrupt: the run recorded an interrupted artifact before exiting. |

A Python traceback is printed only for internal defects (exit 1) — expected failures such
as invalid input, run conflicts, provider misconfiguration, or corrupted artifacts print a
concise diagnostic on stderr and nothing more. Set `ADLIFE_DEBUG=1` to request the full
traceback for any failure.

## `adlife init NAME [--parent DIR]`

Create a new study directory from the packaged demo project. Refuses to touch a non-empty
target (exit 2). Default parent is the working directory.

## `adlife validate PATH`

Validate a study directory: schema, world/routine routability, campaign containment.
Exit 0 quietly when valid; exit 2 with a per-file error list otherwise.

## `adlife population generate [--size N] [--seed N] [--locale LC] [--out FILE] [--as FORMAT]`

Generate a fictional population document. `--size` 1–30 (default 20), `--seed` (default
42), `--locale` `fa-IR` (default) or `en-US`, `--out` writes YAML to a file instead of
stdout, `--as` selects the output document format.

## `adlife campaign import PATH [--project-root DIR]`

Validate one campaign YAML against the project and print the normalized campaign as JSON.
Asset paths inside the campaign are resolved and confirmed to lie beneath the project root
before they are opened; anything else is refused (exit 2).

## `adlife run PROJECT [options]`

Run one study from its first tick to `run.completed`, persisting every artifact.

| Option | Meaning |
| --- | --- |
| `--campaign FILE…` | Replace the project's campaigns with these project-relative YAML files. |
| `--run-id ID` | Explicit run identifier (slug pattern); an existing identifier is refused with exit 3. |
| `--mode rules\|hybrid\|replay` | Cognition mode. `rules` needs nothing; `hybrid` uses the configured provider over the rule core; `replay` requires a cognition cache. |
| `--days N` | Override simulated days (1–7). |
| `--population-size N` | Override population size (1–30). |
| `--seed N` | Override the run seed. |
| `--live` | Open the live dashboard when stdin and stdout are an interactive terminal and `TERM` is not `dumb`; otherwise fall back to headless. |
| `--headless` | Never open a dashboard (the default behaviour). |
| `--no-headless-fallback` | With `--live`: exit 2 instead of falling back when no terminal is available. |
| `--cache-dir DIR` | Cognition cache directory for hybrid/replay modes (default `cache/` under the study). |

`--live --format jsonl` is refused (exit 2): a dashboard and a machine stream cannot share
stdout.

## `adlife replay PROJECT RUN_ID [--provider-mode MODE] [--cache-dir DIR]`

Re-execute a stored run from its recorded inputs and verify the fresh event stream against
the recorded one. `--provider-mode` is `rules` (default), `hybrid`, or `mock`. Emits
`"replay-identical": true` on success. Corrupted artifacts exit 4; a mismatch is reported
as a failure, never repaired. A replay destination (`replay-<run-id>`) that already exists
is refused with exit 3: replay never deletes or overwrites run artifacts, and the stored
source run is never modified.

## `adlife compare CONTROL TREATMENT [--seeds N] [--treatment-path PATH]`

Paired whole-run comparison of two studies: both arms run per seed with the same
population, world, and keyed random streams. `--seeds` 1–200 (default: the quick-study
count). `--treatment-path` declares which scenario document the treatment may differ in
(default `campaigns`). The A/A case (comparing a study with itself) must return exactly
zero paired differences.

## `adlife doctor [PATH] [--offline] [--no-color]`

Report installation readiness: CLI and core versions, packaged resources, provider
availability (mock and rules always; live providers only when not `--offline`), storage
writability, optional project schema check, and console encoding. `--offline` performs no
network access at all.

## `adlife report PROJECT RUN_ID [--output FILE]`

Write the self-contained HTML report for a stored run (default destination
`reports/<run-id>.html` under the study). The report is a single file — charts, styles,
and the Plotly runtime inlined — that renders with the network disconnected, opens with a
synthetic-data disclosure, and ends with the manifest block needed to reproduce the run.

## `adlife demo [--headless] [--dir DIR] [--seed N]`

The offline demonstration: build a packaged demo study (20 agents, 3 simulated days, mock
provider, two campaigns), run it, and open the live dashboard when a real terminal
exists — `--headless` prints a summary instead. The study is created in a temp directory
unless `--dir` is given (an existing directory is refused). Requires no network and no
credentials.

## JSON output

`--format json` prints one document per command; `--format jsonl` prints one JSON object
per line for stream-shaped output (events, live progress). Diagnostics always go to
stderr, so `adlife --format json run my-study > doc.json` is clean piped output.
