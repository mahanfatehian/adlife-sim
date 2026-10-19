# Live demonstration: bounded AI cognition in the city workbench

This is the script for presenting AdLife Lab live. It has two parts:

- **Part 1 — SETUP** (about 5 minutes once the software is installed): check the
  installation, warm the local model, start the browser workbench, create the provider
  profile, and pass the bounded-signal compatibility check.
- **Part 2 — SIMULATION** (about 10 minutes): run one schema-4 / V10 bounded AI cognition
  study from the browser, walk through the saved-run inspector, print the metrics, and replay
  the saved run offline.

Everything runs on one Windows laptop over loopback. Every person, place, product, and
number is fictional and synthetic. Nothing shown is validated psychology, an observed
outcome, or a sales forecast. Commands are for Windows PowerShell and run from the
repository folder. The timings were measured on the development laptop with a local GPU and
will differ on other hardware.

## Before the presentation

- Install the prerequisites and download the model (step 1). The model is about 4.7 GB.
- Rehearse the whole script once on the presenting machine with a separate workspace such as
  `.\rehearsal-workbench`. Reusing the live workspace would make the live run reuse the
  rehearsal's cached answers (shown as **Cache**) and its run IDs.
- Pick a free port. This script uses `50050`; see [Troubleshooting](#troubleshooting) if it
  is taken.
- If no model is available on the day, use the offline path in
  [Without a working model](#without-a-working-model-mock-and-rules).

## Part 1 — SETUP

### 1. Prerequisites (once per machine)

Install [Git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/), and
[Ollama](https://ollama.com/download). Then:

```powershell
git clone https://github.com/mahanfatehian/adlife-sim.git
cd adlife-sim
uv sync
ollama pull qwen2.5:7b-instruct
```

`uv sync` installs the pinned Python environment. `ollama pull` downloads the local
instruction model once; `ollama list` should then show `qwen2.5:7b-instruct`.

### 2. Check the installation (about 5 seconds)

```powershell
uv run adlife doctor --offline
```

Expected: eight `PASS` lines (package, Python, resources, writable storage, SQLite JSON1,
UTF-8 console, offline rule/mock providers, and no network) ending with
`8/8 checks passed`. The command makes no network request.

### 3. Warm the local model (3-12 seconds; about 100 seconds on the first load after a reboot)

```powershell
ollama run qwen2.5:7b-instruct --keepalive 30m hello
ollama ps
```

Expected: a short greeting, then `ollama ps` lists `qwen2.5:7b-instruct` with an `UNTIL`
time about 30 minutes ahead. Ollama unloads an idle model when its keep-alive expires, so
repeat the warm-up shortly before Part 2, or set the Ollama app's `OLLAMA_KEEP_ALIVE`
environment variable to `30m`.

### 4. Start the workbench (about 4 seconds)

```powershell
uv run adlife city-workbench --workspace .\demo-workbench --port 50050
```

Expected: `city workbench: http://127.0.0.1:50050`. Leave this window open (Ctrl+C stops
the server) and open <http://127.0.0.1:50050> in a browser; the command never opens one
itself. The workspace folder is created if needed and keeps provider profiles, the
cognition cache, and saved runs under `.\demo-workbench\city-runs\<run-id>\`.

The server talks only to endpoints approved at startup. The built-in `local-ollama` origin
(`http://127.0.0.1:11434/v1`) is always approved, so the local demo needs no other flag and
no credential. To use a remote OpenAI-compatible endpoint instead, approve it with
`--provider-origin ID=URL` and bind its API key to an environment-variable name with
`--provider-key-env ID=ENVIRONMENT_VARIABLE`:

```powershell
uv run adlife city-workbench --workspace .\demo-workbench --port 50050 `
  --provider-origin remote-lab=https://llm.example.org/v1 `
  --provider-key-env remote-lab=ADLIFE_REMOTE_API_KEY
```

A remote endpoint must use HTTPS with a host name and support non-streaming Chat Completions
with a strict `json_schema` response format. Set `ADLIFE_REMOTE_API_KEY` in that PowerShell
session before starting, without typing the key into a command that is saved in your
history, or leave out `--provider-key-env` and paste the key once into the **API credential**
field, which stores it in the operating-system keyring. A ChatGPT or Codex subscription,
product login, or OAuth token is not accepted. A remote check allows 10 seconds instead of
120.

### 5. Create the local provider profile (once per workspace)

1. **CITY**: keep `Fictional Grid City V2` and click **Configure campaign**.
2. **CAMPAIGN**: click **Review run** (the defaults are fine for this step).
3. **RUN**, section **Model assistance**: set **Operation** to `Bounded AI cognition` and
   **Source** to `Local compatible model`.
4. The hint `No local profile yet` opens **Create an immutable profile** with Approved
   endpoint `local-ollama`, Profile ID `local-ollama`, Display name `Local Ollama`, and Model
   ID `qwen2.5:7b-instruct` already filled in. Click **Save profile revision**.

Expected: **Exact profile revision** shows `Local Ollama · qwen2.5:7b-instruct · <revision>`,
Credential `Available on server`, and Output tokens
`256 per attempt for signals (profile allows 384)`. A profile revision is immutable and stays
in the workspace across restarts; to change a setting, save a new revision.

### 6. Run the bounded-signal compatibility check (2-6 seconds with a warm model)

Click **Check compatibility** in the highlighted row under the funding control. While it runs
the page says
`Checking compatibility… a local model that is still loading may take a minute or more.`

Expected: `Bounded-signal contract confirmed.`, and the row heading reads
`Bounded-signal contract · confirmed for this revision`. The check sends one fixed synthetic
probe under the exact V10 prompt and result contract and discards the answer. The result lives
only in the server process: a page reopened in the same workbench session shows
`confirmed earlier in this workbench session`, but you must check again after restarting the
workbench or after changing Operation, Source, or profile. At most 5 checks may start per
minute.

Say: *"This proves the model can return the strict closed-category result AdLife requires.
It does not prove the model is right about anything."*

## Part 2 — SIMULATION

With the rehearsed configuration below, the rule-owned evidence is fixed on any compatible
installation: 32 synthetic opportunities, 32 impressions, 18 notices, and therefore 18
eligible responses. Only the model's category choices vary between live runs.

### 7. CITY (30 seconds)

Click **CITY** in the stage rail. Select `Fictional Grid City V2` (labelled
`Bundled fictional fixture` and `Verified pack`) and click **Configure campaign**. Say:
*"A fictional street network, verified by hash; no map service, no real residents."*

### 8. CAMPAIGN and audience (2 minutes)

| Control | Value |
| --- | --- |
| Scenario ID / Scenario name | `ollama-demo-study` / `Fictional Ollama demo study` |
| Campaign ID / Campaign name | `fictional-launch` / `Fictional launch` |
| Creative template | `Lumen Pocket · fictional consumer device` |
| Campaign target interests | Commuting, Technology |
| Relative price | `1.25` |
| Ad delivery | **Phone feed** only (leave Roadside billboard off) |
| Eligible activities | Home, Commute, Leisure |
| Chance per eligible minute / Daily frequency cap | `0.05` / `4` |
| Active windows | Day `1`, Start `07:00`, End `21:00`; remove the Day 2 row with its × button |
| Fictional audience | `Fictional Lumo` with 4 people, then choose `Fictional Nuvin` under **Fictional starting point**, click **Add persona**, and set it to 4 people |

The audience line then reads `2 definitions · 8 people`. Click **Review run**.

Say: *"Interests and the six response traits are model inputs. Names, ages, occupations,
biographies, and MBTI are descriptive only: they never reach the model or the equations."*

### 9. RUN settings and model assistance (1 minute)

1. Run settings: Run ID `demo-v10-001` (use a new ID for every run), Synthetic people `8`,
   Model days `1`, Seed `42`. Response mode stays `Rules engine (offline)`.
2. Model assistance: Operation `Bounded AI cognition`, Source `Local compatible model`, the
   `Local Ollama` profile revision, and Funded responses `10 requests` (the default is 20).
3. Read the funding text aloud: `The first 10 of up to 60 selected responses go to the model;
   the rest are recorded as budget-skipped neutral signals.` and `Each funded request reserves
   1,024 output tokens: at most 4 HTTP attempts of up to 256 output tokens each. 10 requests
   reserve 10,240 output tokens. Input tokens and vendor charges are not included; this is a
   reservation, not a spending limit.`
4. If the check row heading reads `Bounded-signal contract · required before start`, click
   **Check compatibility** again.

Point at the boundary card: *"External AI selects a small set of synthetic appraisal
categories. AdLife applies a fixed mapping. These categories may change only the displayed
cognition state. They cannot move agents, create or select events, change purchase
intention, or make purchases."*

### 10. Validate and review (30 seconds)

Click **Validate study** (1-2 seconds). Expected: `Validated. Review the immutable binding
receipt before starting.` The review shows Operation `Bounded AI cognition`, Model source
`Local model · Local Ollama · qwen2.5:7b-instruct · rev …`, Funding
`10 funded of up to 60 selected · 10,240 output tokens reserved`, Compatibility
`Bounded-signal check passed for this revision`, and, under Technical provenance, Saved as
`Workbench schema 4 → run V10`. Read **Who decides what**:

| Subject | Owner |
| --- | --- |
| Movement, exposure, attention | Rules |
| Purchase intention | Rules: an uncalibrated proxy score; purchases are not modeled |
| Cognition signals | Local model, with deterministic neutral fallback |
| Reproducibility | Live signals are not guaranteed reproducible |
| Saved replay | Frozen and offline |

### 11. Run it and watch the phases (about 25-31 seconds)

Click **Start bounded cognition run**. The **Simulation status** track moves through:

| Phase | When | What is happening |
| --- | ---: | --- |
| Queued, Planning cognition | about 1 s | Rules compute movement, attention, and baseline responses; signal requests are selected in stable order. |
| Requesting cognition | about 1-27 s | The 10 funded requests go to the model; Ollama answers one at a time, about 2.5-3 s each. Failures resolve to neutral fallback. |
| Applying cognition | about 25-27 s | The frozen `city-cognition-influence-v1` mapping updates the separate cognition state. |
| Persisting, Verifying | about 25-30 s | The artifact is written, then independently reloaded and verified. |
| Completed | about 26-31 s | `The run is saved and ready to view.` |

Short phases can pass between two status polls; the track still marks every step `DONE`.
Click **Inspect run**.

### 12. Inspect the saved run (4 minutes)

The page opens `/runs/demo-v10-001` after verifying every saved file (1-3 seconds).

1. **Map and timeline.** Press ▶ or drag the **City timeline**: fictional agents move along
   roads through the model day. For the current minute, the sidebar lists the phone
   opportunities, **Modeled attention** (noticed or ignored, from a fixed 0.5 draw), and
   **Responses & bounded cognition**; choose a person to follow one agent. Say: *"All of this
   is rule-owned and identical on every rerun."*
2. **Bounded AI cognition signals panel.** Model `qwen2.5:7b-instruct` with its profile
   revision; tiles Eligible 18, Selected 18, Funded 10, Unselected 0, Provider 10, Cache 0,
   Fallback 0, Budget skipped 8; `How the 18 eligible responses were resolved`
   (`Signal applied` 10, `Neutral · budget skipped` 8); the neutralization line (`Neutral
   vector applied to 8 of 18 eligible responses`, with the uncertain and safety-neutralized
   counts); the authority pipeline Rule
   baseline → Signal or neutral → Fixed mapping → Applied delta → Dual-state commit; and the
   prompt, result-schema, and influence-policy hashes. **Provider usage** reports what the
   model actually did, kept apart from the reservation; one rehearsal read
   `10 HTTP attempts in this run · 6,745 prompt + 1,400 completion tokens`, against 10,240
   reserved output tokens. A model failure would show here as Fallback with its reason.
3. **Saved signal timeline.** Open it, keep **Show** on `All selected requests (18)`, and
   click the first entry, `#1 · DAY 01 · 07:04`, `PERSON-005 · FICTIONAL-LAUNCH`. The page
   jumps to that minute and agent and shows the response's five-stage trace. In rehearsal it
   read:

   | Stage | What it showed |
   | --- | --- |
   | 1 Deterministic baseline (rule-owned) | sentiment Δ +0.0398, recall Δ +0.254 |
   | 2 Provider signal | affect `neutral`, relevance `high`, credibility `high`, salience `medium`, with that request's usage (`1 HTTP attempt` and its prompt and completion tokens) |
   | 3 `city-cognition-influence-v1` mapping | sentiment (0.0398 + 0) × 1.15 × 1.10 = +0.0503; recall 0.254 × 1.15 × 1.10 × 1.00 = 0.3213, clamped to 0.3 |
   | 4 Applied cognition delta | sentiment Δ +0.0503, recall Δ +0.3, cognition track only |
   | 5 Atomic rule/cognition state update | rule sentiment 0 → 0.0398, cognition sentiment 0 → 0.0503 |

   The baseline numbers are rule-owned and identical on every run of this configuration;
   the model's categories, and therefore stages 2-5, can differ in a live run. Then set
   **Show** to `Budget skipped (8)` and open an entry: the neutral vector leaves the applied
   delta exactly equal to the baseline.
4. **Final states.** Under **Final campaign state**, each agent has a `RULE TRACK ·
   RULE-OWNED` card with sentiment, recall, and the intention proxy, and a `COGNITION TRACK ·
   SYNTHETIC` card with sentiment and recall only plus its `VS RULE TRACK` gap (or
   `IDENTICAL TO THE RULE TRACK` when every signal was neutral). Purchase intention never
   reads the cognition track.
5. **Metrics.** Open **Response metrics**: the rule-baseline tables keep the uncalibrated
   purchase-intention proxy, and the cognition metrics are labelled exploratory.

### 13. Metrics from the command line (about 6 seconds)

Open a second PowerShell window in the repository folder; the server keeps running.

```powershell
uv run adlife city-metrics .\demo-workbench demo-v10-001 --layer response
```

Expected lines, in order: `synthetic bounded-cognition metrics, not observed outcomes:
demo-v10-001`, `Rule baseline (rule-owned)` with response, reach, sentiment, recall, and
purchase-intention-proxy receipts and the line `purchase intention proxy: uncalibrated
rule-owned proxy, not a probability or a forecast; purchases are not modeled`, then
`Provider-dependent cognition (exploratory, not a campaign effect)` with
`signal source: local; effect scope bounded-signals-v1; funded response limit 10`, the
profile, model, and revision, `coverage: eligible responses 18; selected 18; funded 10;
budget-skipped 8; unselected 0`, the source and category counts (the output states how many
categories are fixed neutral vectors rather than model choices), applied means, the synthetic
cognition state, provenance hashes, and a disclosure. In rehearsal the model mostly chose
high relevance and credibility, so mean synthetic sentiment ended at 0.076 against 0.065 on
the rule track; live values differ from run to run.

### 14. Offline replay (about 6-8 seconds)

```powershell
uv run adlife city-replay .\demo-workbench demo-v10-001
```

Expected: `city replay identical: demo-v10-001 (1440 minute frames, 32 synthetic
opportunities, 32 impressions, 18 notices)`, then `18 rule-owned baseline responses, 18
bounded sentiment/recall applications, 18 dual state updates`, `8 rule states, 8 cognition
states; purchase intention remains rule-owned`, `cognition contract: bounded synthetic
cognition (mode: local; effect scope: bounded-signals-v1); frozen offline replay`, and the
evidence hashes. Replay regenerates the rule layers, revalidates every saved signal against
its request, reapplies the fixed mapping, and requires exact equality without calling the
model. To prove it, quit Ollama from the system tray and run the command again, or add
`--format json` after `adlife` and point at `"identical": true`. Restart Ollama before any
further live run.

### 15. Optional extensions

- **Run again and cache.** In the inspector, click **↻ Run again with these inputs** (the
  same button sits on the run's card under INSPECT). The workbench reopens at RUN with
  `Inputs restored from run demo-v10-001 with a new run ID. Review them, then validate.`,
  the suggested Run ID `demo-v10-002`, the same audience, settings, and profile, and the check
  still confirmed earlier in this workbench session. Set Funded responses to `20 requests`,
  validate, and start: all 18 selected requests are funded, 10 from the verified cache of the
  first run and 8 from the model (about 30 seconds), and Provider usage reads
  `8 HTTP attempts in this run` with `10 cached answers reused without a call`. Use a
  different seed or a fresh workspace when every answer should be a live provider call.
- **Mock.** Repeat with Source `Mock — reproducible demo` and Run ID `demo-mock-001`. It needs
  no profile or check, finishes in about 3-10 seconds, and fills all 18 signals from the
  offline deterministic mock.
- **Rules.** Source `Rules — offline` applies the exact neutral vector, so the cognition
  track equals the rule track: a useful contrast for the authority boundary.

## Without a working model: mock and rules

If Ollama or the remote endpoint is unavailable, run Part 2 with Source
`Mock — reproducible demo`. Funded responses shows `60 · all selected, resolved offline`,
the review lists `Mock (offline)` and `Up to 60 selected · resolved offline · no
reservation`, and no profile, credential, or compatibility check is needed. The run makes no
network call, is identical on rerun, and the inspector, metrics, and replay steps work
unchanged with `mock` instead of `local`. Say plainly that mock categories come from a fixed
hash rule (`city-cognition-mock-signal-v1`), not a model.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Not compatible with bounded signals · endpoint resolution refused.` after about 2 s | Ollama is not running, so nothing listens on `127.0.0.1:11434` | Start the Ollama app (or `ollama serve`), warm the model, and check again. |
| `Not compatible with bounded signals · protocol incompatible.` at once, with the hint `The local server refused this request. The usual cause is a model ID that is not installed` | The Model ID is not installed, or the endpoint lacks strict JSON-schema output | Compare the Model ID with `ollama list`; run `ollama pull qwen2.5:7b-instruct`, or save a new profile revision whose Model ID matches exactly. |
| `Not confirmed: the model did not answer within the check's time limit.` | The model was still loading after 120 seconds | Run the warm-up command the hint shows, `ollama run qwen2.5:7b-instruct --keepalive 30m hello`, then check again. |
| **Start bounded cognition run** stays disabled: `Check bounded-signal compatibility for this profile revision to enable the run.` | No passing check in this server process for this exact revision | Click **Check compatibility**; repeat after a restart or after changing Operation, Source, or profile. |
| A remote profile shows Credential `Required` and **Check compatibility** stays disabled | The server has no API key for that profile | Set the bound environment variable before starting the workbench, or save the key once in **API credential**. |
| Every funded response shows **Fallback** with a reason such as `timeout-exhausted` | The model stopped, unloaded, or overloaded during the run | The run is still valid with neutral cognition. Warm the model, check again, and rerun with a new Run ID. |
| Responses listed as `Set aside · safety flag` or `Set aside · uncertain answer` | The model set a safety flag or answered `uncertain` | Working as designed: the categories stay visible as recorded but not applied, and the whole vector is neutral. The `Uncertain or safety neutralized` filter lists them. |
| Sources show **Cache** instead of **Provider** | Same inputs, seed, and profile revision as an earlier run in this workspace | Use a new seed or a fresh workspace folder. |
| Validation marks the Run ID: `A saved run already uses this ID. Choose a new run ID.` | A saved run in this workspace has that ID; runs are never overwritten | Enter a new Run ID such as `demo-v10-002`, or use **↻ Run again with these inputs**, which suggests a free one. |
| `ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 50050)` right after the `city workbench:` line | Another process already uses the port | Use another port such as `--port 50051`, or find the owner with `Get-NetTCPConnection -LocalPort 50050 -State Listen`. |
| `[winerror 10013] an attempt was made to access a socket in a way forbidden by its access permissions` | The port is in a Windows-reserved range | List the ranges with `netsh interface ipv4 show excludedportrange protocol=tcp` and pick a port outside them. |
| `workbench workspace is unsafe or unavailable` | The workspace path is a link, junction, or file, or uses an 8.3 short name | Use a plain folder such as `.\demo-workbench`. |
| The run library reports unavailable entries withheld | A run was written by an earlier development build of the unreleased V10 format, or a file was changed | Create a fresh run; damaged or incompatible runs are refused, never repaired. |
| A V10 page shows `Bounded cognition inspector assets are unavailable` | An incomplete or outdated installation | Run `uv sync` again and restart the workbench. |

## Talking points

- The language model chooses only closed categories (affect, relevance, credibility,
  salience, and optional safety flags) for an ad that the rules say an agent noticed. It never
  writes a number, an identifier, or an event.
- AdLife applies the fixed, published `city-cognition-influence-v1` tables and clamps. The
  model's choice changes only a separate synthetic cognition state.
- Movement, exposure, attention, the baseline responses, budgets, and the purchase-intention
  proxy stay rule-owned; purchases are not modeled at all.
- Names, ages, occupations, biographies, and MBTI never reach the model.
- An `uncertain` answer or any safety flag neutralizes the whole answer; failures and unfunded
  requests are neutral and labelled, so every response is accounted for.
- Each funded request reserves 1,024 output tokens: at most 4 HTTP attempts of up to 256
  output tokens each. Input tokens and vendor charges are outside that reservation.
- Live model answers are not reproducible; saved runs are. `city-replay` reapplies the frozen
  signals offline and must match every saved byte of evidence.
- The cognition evidence is exploratory: it is not validated psychology, not a prediction about
  any person, and not a sales forecast or campaign effect. Comparisons, studies, and reports
  of V10 runs are not supported yet.
- Integrity hashes detect damage and isolated edits; they are unsigned, so they cannot prove
  who produced a file against someone who rewrites every artifact.

## What not to say

- No accuracy, validation, adoption, revenue, or investment figures: none exist.
- Do not call the categories the agent's thoughts, feelings, or observed behavior.
- Do not present purchase intention as a purchase probability or a sales forecast.
- Do not claim a campaign effect, a causal effect, or a comparison between V10 runs.
- Do not attribute anything to MBTI, names, or other descriptive persona fields.
- Do not promise that a fresh run will reproduce the same model answers.

If asked about external validity, the honest answer is the model card's own words:
**unknown, no calibration performed**. See the [model card](methodology/model-card.md),
[limitations](methodology/limitations.md), and [city pilot](city-pilot.md) for the full
contract.
