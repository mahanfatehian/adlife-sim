# Quickstart: zero to self-contained report

After installing dependencies, the study workflows below run offline in rules or mock
mode: no API key, no account, no provider network access. Only the optional local model in
section 10 talks to a model, and only over loopback.

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
default eight-zone world and two campaigns: a mobile-feed phone placement and a highway
billboard.

Create studies outside the repository if you do not want them in your checkout:

```bash
uv run adlife init demo-study --parent "$TEMP"
```

## 2. Validate

```bash
uv run adlife validate demo-study
```

A valid project exits 0 with `{"valid": true, "errors": []}`. Invalid study input exits 2
with a concise diagnostic — this is the input gate the study commands run first.

## 3. Run

```bash
uv run adlife run demo-study --run-id first
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
uv run adlife replay demo-study first
```

Replay re-executes the stored run from its recorded inputs and verifies the fresh event
stream matches the recorded one event for event — the reproducibility guarantee, checked,
not assumed.

## 5. Read the report

```bash
uv run adlife report demo-study first
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

Both arms run per seed with identical populations, worlds, and keyed random streams.
The paired differences describe the declared treatment inside this synthetic model,
not real-market effects. Phone and billboard placements can have different opportunity
counts; their comparison does not isolate a pure channel effect. The A/A control — comparing a
study with itself — must return exactly zero before any treatment result means anything.

## 8. The offline demonstration

```bash
uv run adlife demo              # runs and opens the dashboard on a real terminal
uv run adlife demo --headless   # prints a summary instead
uv run adlife demo --seed 7 --dir ./my-demo
```

Twenty agents, three simulated days, mock provider, packaged campaigns, temp directory by
default — the one-command zone-engine demonstration. The browser app for the city track is
described in section 10 and in [docs/user-guide.md](user-guide.md).

## 9. Check your installation

```bash
uv run adlife doctor --offline
```

Reports versions, packaged resources, provider availability (mock/rules always; live
providers only without `--offline`), storage writability, and the console encoding. On a
Windows console that is not UTF-8, doctor says so and suggests `set PYTHONUTF8=1`.

## 10. The city workbench with bounded AI cognition

The separate city track runs fictional agents on a street map in a local browser workbench:

```bash
uv run adlife city-workbench --workspace ./workbench --port 8765
```

Open `http://127.0.0.1:8765/guide` yourself (the root address redirects there). The guided
pages are the workbench's only browser interface: **New simulation** opens the setup
page, whose third card picks the AI source. Every guided run is bounded AI cognition:

| Operation | Source | Saved run | Needs |
| --- | --- | --- | --- |
| `Explanation only` | rules, mock, local, or remote (schema-3 API requests only) | schema-v9 | nothing for rules or mock |
| `Bounded AI cognition` | **Practice answers** or **No AI** | schema-v10 | nothing; fully offline |
| `Bounded AI cognition` | **AI on this computer** | schema-v10 | Ollama with `ollama pull qwen2.5:7b-instruct` and a passing **Test connection** on the start page |

In bounded AI cognition the model only chooses closed appraisal categories for ads an agent
noticed; AdLife maps them through a fixed policy into a separate synthetic cognition state,
and movement, attention, baseline responses, and the purchase-intention proxy stay
rule-owned. A Mock run saves in a few seconds; a local model answers one question at a
time, about 2.5-3 seconds each on the development laptop, up to the AI limit (120 by default
for **AI on this computer**). Each funded request reserves 1,536 output tokens: at most 6 HTTP
attempts of up to 256 output tokens each; input tokens and vendor charges are not included.
**Online AI** connects any OpenAI-compatible service: type its address,
model, and API key on the start page (or approve it at startup with `--provider-origin`). A
ChatGPT or Codex subscription or OAuth token is not accepted. Watch the saved run
as a story and open its report in the browser, then:

```bash
uv run adlife city-metrics ./workbench <run-id> --layer response
uv run adlife city-replay ./workbench <run-id>
```

`city-metrics` prints the rule baseline separately from the exploratory cognition family,
and `city-replay` reproduces the saved run offline without calling the model;
`uv run adlife city-view ./workbench <run-id>` opens the saved run's per-response inspector.
`uv run adlife app` starts the same server with a default workspace and opens the browser.
The step-by-step guide for users, with every AI choice and troubleshooting, is
[docs/user-guide.md](user-guide.md).

## Output contract

Every command honors the global `--format human|json|jsonl` and `--no-color` options
(`NO_COLOR` and `TERM=dumb` are honored automatically). JSON modes emit one
machine-readable document on stdout with diagnostics on stderr. Exit codes: 0 success,
1 internal error, 2 invalid input, 3 run conflict, 4 artifact corruption, 130 interrupt.
Details in [cli-reference.md](cli-reference.md).
