# Quickstart: zero to self-contained report

Everything below runs offline in rules or mock mode: no API key, no account, no network.

## 0. Install

```bash
git clone https://github.com/mahanfatehian/adlife-sim.git
cd adlife-sim
uv sync
```

Confirm the CLI answers:

```bash
uv run adlife --version
```

## 1. Create a study

```bash
uv run adlife init demo-study
# created study at demo-study
# next: adlife validate demo-study
```

A study is a directory holding `adlife.yaml` (the run configuration), `population.yaml`,
and `campaigns/*.yaml`. The packaged demo project pairs a three-agent population with the
default ten-zone world and two campaigns: a mobile-feed phone placement and a highway
billboard.

Create studies outside the repository if you do not want them in your checkout:

```bash
uv run adlife init demo-study --parent "$TEMP"
```

## 2. Validate

```bash
uv run adlife validate demo-study
```

A valid project exits 0 quietly. Every broken input exits 2 with an error naming the file
and the defect — this is the gate every other command runs first.

## 3. Run

```bash
uv run adlife run demo-study
# run run-project-…-42: completed at minute 1440, 161 events, artifacts in demo-study/runs/run-project-…-42
```

One simulated day is 1,440 simulated minutes, driven as 96 ticks of 15 simulated minutes.
A tick is the engine's atomic commit unit; a timestamp is an absolute simulated minute,
so day 1 ends at minute 1,440. Overrides: `--seed`, `--days`
(1–7), `--population-size` (1–30), `--run-id`, `--campaign`.

What lands in `demo-study/runs/<run-id>/`:

| File | Contents |
| --- | --- |
| `events.jsonl` | the full ordered, causally linked event stream |
| `results.sqlite3` | the same run in the SQLite store |
| `run.json` | the manifest: code version, scenario fingerprint, provider, seed, parameters |
| `metrics.json` | per-run counters |
| `inputs/` | the frozen scenario the run was driven with |
| `provider-usage.json` | cognition requests, cache hits, fallbacks |

## 4. Replay it

```bash
uv run adlife replay demo-study <run-id>
```

Replay re-executes the stored run from its recorded inputs and verifies the fresh event
stream matches the recorded one event for event — the reproducibility guarantee, checked,
not assumed.

## 5. Read the report

```bash
uv run adlife report demo-study <run-id>
```

Writes `demo-study/reports/<run-id>.html`: a self-contained file (Plotly inlined, system
fonts, no remote scripts) that renders with the network disconnected. It opens with a
synthetic-data disclosure and carries the manifest block a reader needs to reproduce the
run. Override the destination with `--output`/`-o`.

## 6. Watch it live

```bash
uv run adlife run demo-study --live
```

On a real interactive terminal this opens the four-panel dashboard: agent table, selected
agent details and memories, bounded live event stream, metrics strip. Keys: `Space`
pause/resume, `N` step one tick while paused, `+`/`-` speed, `F` filter the stream, `Q`
quit. Quitting mid-run records a valid interrupted artifact (exit 130) that replays like
any other. In a non-interactive shell the command falls back to headless; pass
`--no-headless-fallback` to refuse instead. `--live --format jsonl` is rejected: a live
dashboard and a machine stream cannot share stdout.

## 7. Compare two campaigns

```bash
uv run adlife compare control-study treatment-study --seeds 20
```

Both arms run per seed with identical populations, worlds, and keyed random streams, so a
paired difference is the treatment effect and nothing else. The A/A control — comparing a
study with itself — must return exactly zero before any treatment result means anything.

## 8. The offline demonstration

```bash
uv run adlife demo              # runs and opens the dashboard on a real terminal
uv run adlife demo --headless   # prints a summary instead
uv run adlife demo --seed 7 --dir ./my-demo
```

Twenty agents, three simulated days, mock provider, packaged campaigns, temp directory by
default — the one-command demonstration used in [docs/investor-demo.md](investor-demo.md).

## 9. Check your installation

```bash
uv run adlife doctor --offline
```

Reports versions, packaged resources, provider availability (mock/rules always; live
providers only without `--offline`), storage writability, and the console encoding. On a
Windows console that is not UTF-8, doctor says so and suggests `set PYTHONUTF8=1`.

## Output contract

Every command honors the global `--format human|json|jsonl` and `--no-color` options
(`NO_COLOR` and `TERM=dumb` are honored automatically). JSON modes emit one
machine-readable document on stdout with diagnostics on stderr. Exit codes: 0 success,
1 internal error, 2 invalid input, 3 run conflict, 4 artifact corruption, 130 interrupt.
Details in [cli-reference.md](cli-reference.md).
