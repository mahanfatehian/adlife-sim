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
- On a projector, zoom the browser to 125% (Ctrl and +). Both pages reflow at that zoom
  without sideways scrolling, and the small labels in the inspector's sidebar become readable.
- If no model is available on the day, use the offline path in
  [Without a working model](#without-a-working-model-mock-and-rules).
- For finished runs to fall back on, seed them with one command once the workbench is
  running; see [Backup: seed a demo workspace](#backup-seed-a-demo-workspace).

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

### 3. Warm the local model (3-30 seconds; about 100 seconds on the first load after a reboot)

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

Expected: `city workbench: http://127.0.0.1:50050/guide`. Leave this window open (Ctrl+C stops
the server) and open <http://127.0.0.1:50050> in a browser; the command never opens one
itself. The workspace folder is created if needed and keeps provider profiles, the
cognition cache, and saved runs under `.\demo-workbench\city-runs\<run-id>\`.

The printed `/guide` address opens the guided view, plain-language pages for presenting to a
non-technical audience, with its own script in [guided-demo.md](guided-demo.md); this script
uses the technical workbench at the address without `/guide`.

The server talks only to endpoints approved at startup. The built-in `local-ollama` origin
(`http://127.0.0.1:11434/v1`) is always approved, so the local demo needs no other flag and
no credential. To use a remote OpenAI-compatible endpoint instead, for example on a
presenting laptop that cannot run Ollama, approve it with `--provider-origin ID=URL` and bind
its API key to an environment-variable name with `--provider-key-env ID=ENVIRONMENT_VARIABLE`.
For the GapGPT gateway:

```powershell
$secureKey = Read-Host "GapGPT API key" -AsSecureString
$env:GAPGPT_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
uv run adlife city-workbench --workspace .\demo-workbench --port 50050 `
  --provider-origin gapgpt=https://api.gapgpt.app/v1 `
  --provider-key-env gapgpt=GAPGPT_API_KEY
```

The first two lines ask for the key without showing it and keep it out of your command
history; it lasts only for that PowerShell window. Alternatively leave out
`--provider-key-env` and paste the key once into the **API credential** field, which stores it
in the operating-system keyring. Then, in step 5, choose Source `Remote compatible model`,
Approved endpoint `gapgpt`, and Model ID `gapgpt-qwen-3.6`. GapGPT serves that ID with
Qwen/Qwen3.6-35B-A3B-FP8 on vLLM, which can otherwise spend the whole 256-token answer budget
thinking and return no answer, so **Turn off model thinking (Qwen on vLLM servers)** switches
itself on for a remote model ID that names Qwen: leave it checked. It adds
`chat_template_kwargs` `{"enable_thinking": false}` to every request, repair, and
compatibility check, and it is saved in the profile revision and frozen into each run; leave it
off for OpenAI itself, which refuses unknown request fields. A remote endpoint must use HTTPS
with a host name and support non-streaming Chat Completions with a strict `json_schema`
response format. A ChatGPT or Codex subscription, product login, or OAuth token is not
accepted. A remote check allows 60 seconds instead of 120 (single GapGPT calls took 3 to 26
seconds in rehearsal), and each run attempt keeps the profile's 30-second timeout.

### 5. Create the local provider profile (once per workspace)

1. **CITY**: keep `Fictional Grid City V2` and click **Configure campaign**.
2. **CAMPAIGN**: click **Review run** (the defaults are fine for this step).
3. **RUN**, section **Model assistance**: set **Operation** to `Bounded AI cognition` and
   **Source** to `Local compatible model`.
4. The hint `No local profile yet` opens **Create an immutable profile** with Approved
   endpoint `local-ollama`, Profile ID `local-ollama`, Display name `Local Ollama`, and Model
   ID `qwen2.5:7b-instruct` already filled in. Click **Save profile revision**.
   If the workspace already holds that profile (for example after
   [seeding backup runs](#backup-seed-a-demo-workspace)), the hint does not appear and
   **Exact profile revision** already offers it: select it and go on to step 6.

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
only in the server process. **↻ Run again with these inputs** reopens RUN with
`confirmed earlier in this workbench session`, but a reloaded or newly opened page asks for
the check again (one click, about 2 seconds), and so do restarting the workbench and changing
Operation, Source, or profile. At most 5 checks may start per minute.

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
   Model days `1`, Seed `42`. Baseline responses stays `Rules engine (offline)`: the rules
   compute every baseline response, and the model only feeds the separate cognition track.
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

Click **Validate study** (under a second). Expected: `Validated. Review the immutable binding
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

1. **Map and timeline.** Press ▶ or drag the timeline slider under **Model time** at the
   bottom of the page: fictional agents move along roads through the model day. For the
   current minute, the sidebar lists the phone opportunities, **Modeled attention** (noticed
   or ignored, from a fixed 0.5 draw), and **Responses & bounded cognition**; choose a person
   to follow one agent. Say: *"All of this is rule-owned and identical on every rerun."*
2. **Bounded AI cognition signals panel.** Model `qwen2.5:7b-instruct` with its profile
   revision; tiles Eligible 18, Selected 18, Funded 10, Unselected 0, Provider 10, Cache 0,
   Fallback 0, Budget skipped 8; `How the 18 eligible responses were resolved`
   (`Signal applied` 10, `Neutral · budget skipped` 8); the neutralization line (`Neutral
   vector applied to 8 of 18 eligible responses`, with the uncertain and safety-neutralized
   counts); the authority pipeline Rule
   baseline → Signal or neutral → Fixed mapping → Applied delta → Dual-state commit; and the
   prompt, result-schema, and influence-policy hashes. **Provider usage** reports what the
   model actually did, kept apart from the reservation; the rehearsal read
   `10 HTTP attempts in this run · 10,018 prompt + 1,415 completion tokens`, against 10,240
   reserved output tokens. A model failure would show here as Fallback with its reason.
3. **Saved signal timeline.** Open it, keep **Show** on `All selected requests (18)`, and
   click the first entry, `#1 · DAY 01 · 07:04`, `PERSON-005 · FICTIONAL-LAUNCH`. The page
   jumps to that minute and agent and shows the response's five-stage trace. In rehearsal it
   read:

   | Stage | What it showed |
   | --- | --- |
   | 1 Deterministic baseline (rule-owned) | sentiment Δ +0.0398, recall Δ +0.254 |
   | 2 Provider signal | affect `curious`, relevance `high`, credibility `medium`, salience `high`, with that request's usage (`1 HTTP attempt · 992 prompt + 137 completion tokens`) |
   | 3 `city-cognition-influence-v1` mapping | sentiment (0.0398 + 0.02) × 1.15 × 1.00 = +0.0687; recall 0.254 × 1.15 × 1.00 × 1.15 = 0.3359, clamped to 0.3 |
   | 4 Applied cognition delta | sentiment Δ +0.0687, recall Δ +0.3, cognition track only |
   | 5 Atomic rule/cognition state update | rule sentiment 0 → 0.0398, cognition sentiment 0 → 0.0687 |

   The baseline numbers are rule-owned and identical on every run of this configuration;
   the model's categories, and therefore stages 2-5, can differ in a live run. In rehearsal
   both personas shared the campaign's technology interest and had moderate advertising
   skepticism, so every funded answer chose relevance `high` and credibility `medium`, while
   affect (`curious` or `skeptical`) and salience (`high` or `medium`) changed with the agent
   and the moment; step 15 adds a persona the model judges very differently. Then set
   **Show** to `Budget skipped (8)` and open an entry: the neutral vector leaves the applied
   delta exactly equal to the baseline.
4. **Final states.** Under **Final campaign state**, each agent has a `RULE TRACK ·
   RULE-OWNED` card with sentiment, recall, and the intention proxy, and a `COGNITION TRACK ·
   SYNTHETIC` card with sentiment and recall only plus its `VS RULE TRACK` gap (or
   `IDENTICAL TO THE RULE TRACK` when every signal was neutral). Purchase intention never
   reads the cognition track.
5. **Metrics.** Scroll the sidebar to **Response metrics** (open by default; clicking its
   heading hides it): the rule-baseline tables keep the uncalibrated
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
budget-skipped 8; unselected 0; funded coverage 10/18=0.555556`, the source and category
counts (the output states how many categories are fixed neutral vectors rather than model
choices), applied means, the synthetic cognition state, provenance hashes, and a
disclosure. In rehearsal the model chose high
relevance and medium credibility for every funded response, so mean synthetic sentiment
ended at 0.082 against 0.065 on the rule track; live values can differ from run to run.

### 14. Offline replay (about 6-8 seconds)

```powershell
uv run adlife city-replay .\demo-workbench demo-v10-001
```

Expected: `city replay identical: demo-v10-001 (1440 minute frames, 32 synthetic
opportunities, 32 impressions, 18 notices)`, then `response evidence: 18 rule-owned baseline
responses, 18 bounded sentiment/recall applications, 18 dual state updates`, `state
evidence: 8 rule states, 8 cognition states; purchase intention remains rule-owned`,
`response contract: spatial-response-v2; synthetic-bounded-cognition-not-observed-behavior`,
`cognition contract: bounded synthetic cognition (mode: local; effect scope:
bounded-signals-v1); frozen offline replay`, `cognition evidence: 18 selected (10 funded, 8
budget-skipped), 0 unselected; 18 frozen terminals`, and the evidence hashes. Replay
regenerates the rule layers, revalidates every saved signal against its request, reapplies
the fixed mapping, and requires exact equality without calling the model. To prove it, quit Ollama from the system tray and run the command again, or add
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
- **A persona the model judges differently.** Click **↻ Run again with these inputs**, open
  **CAMPAIGN**, choose `Fictional Veyra` under **Fictional starting point**, click **Add
  persona**, and set it to 4 people (`3 definitions · 12 people`). Set Synthetic people to
  `12` and Funded responses to `20 requests`, use a new Run ID, validate, and start (about
  50 seconds). Veyra's interests (books, cooking) miss the campaign's targets, and her
  advertising skepticism (0.75) and price sensitivity (0.8) are high. In rehearsal all 8 of
  her answers read `skeptical`, relevance `medium`, credibility `low`, salience `low`, while
  the Lumo and Nuvin agents kept relevance `high` and credibility `medium`. Open the saved
  signal timeline and compare a Veyra entry with a Lumo entry. Some answers may come from
  the cache of the first run, and an answer that fails validation shows as **Fallback**
  with its reason (1 of 20 did in rehearsal).
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
network call, is identical on rerun, and the inspector, metrics, and replay steps work the
same way with `mock` instead of `local`, except that every selected request is funded:
metrics read `funded response limit 60`, `provider: none (signals resolved offline)`, and
`funded 18; budget-skipped 0`, and replay reports `18 selected (18 funded, 0
budget-skipped)`. Say plainly that mock categories come from a fixed hash rule
(`city-cognition-mock-signal-v1`), not a model.

## Backup: seed a demo workspace

If the local model is slow on the day, or you would rather open finished runs than wait,
seed backup runs before the presentation. Keep the workbench from step 4 running, open a
second PowerShell window in the repository folder, and run one of:

```powershell
uv run python scripts/demo_workspace.py --base-url http://127.0.0.1:50050
uv run python scripts/demo_workspace.py --base-url http://127.0.0.1:50050 --with-llm
```

The script drives the running workbench through the same loopback API the browser uses: it
reads the page's security token, validates and starts each study, and waits for its job.
Every run is therefore an ordinary verified saved run, listed under INSPECT. Each one uses the
Part 2 study (`Fictional Grid City V2`, the `Lumen Pocket` creative, `Fictional Lumo` and
`Fictional Nuvin` with 4 people each, 1 model day):

| Run ID | Operation · Source | Saved as |
| --- | --- | --- |
| `backup-v10-mock-001` | `Bounded AI cognition` · `Mock — reproducible demo` | Workbench schema 4 → run V10 |
| `backup-v9-rules-001` | `Explanation only` · `Rules — offline` | Workbench schema 3 → run V9 |
| `backup-v10-local-001` | `Bounded AI cognition` · `Local compatible model`, `10 requests` funded; only with `--with-llm` | Workbench schema 4 → run V10 |

It prints one line per run with its run ID, phase, source counts, and inspector URL, with
progress lines starting `demo_workspace:` in between. Expected after about 10 seconds:

```text
backup-v10-mock-001  completed  V10 bounded/mock  rules=0 mock=19 provider=0 cache=0 fallback=0 budget-skipped=0  http://127.0.0.1:50050/runs/backup-v10-mock-001
backup-v9-rules-001  completed  V9 explanation-only/rules  rules=19 mock=0 provider=0 cache=0 fallback=0 budget-skipped=0  http://127.0.0.1:50050/runs/backup-v9-rules-001
```

With `--with-llm` the script then warms the model in Ollama for 30 minutes (`--model` picks
another installed model), reuses or creates the same `local-ollama` profile revision that
**Save profile revision** creates in step 5, passes the bounded-signal compatibility check,
and starts the local run. Its line follows about 30 seconds later with a warm model, reading
`provider=10` and `budget-skipped=9`; a cold model adds 12-100 seconds of loading first.

- Each invocation takes the next number (`-002`, `-003`, ...), so a saved run is never
  overwritten; `--run-prefix` replaces `backup`. `--with-llm` also creates the two offline
  runs again, and all three share that invocation's number: after an earlier offline
  seeding, the local run is `backup-v10-local-002`.
- The script uses seed 1 instead of Part 2's 42, so its counts differ slightly (19 eligible
  responses instead of 18), and a later live `demo-v10-001` run in the same workspace still
  asks the model instead of reusing these answers as **Cache**. Running `--with-llm` again
  with the same seed does reuse them (`cache=10`); pass another `--seed` for new model calls.
- The script's check counts only inside this server process: **↻ Run again with these
  inputs** on the seeded local run shows it as confirmed, but a newly opened page still
  needs its own **Check compatibility** click in step 6.
- It talks only to `127.0.0.1` or `localhost`, needs no credential, and never prints the
  page's security token. Any failure ends with a one-line `demo_workspace: error:` message
  and exit code 1; a mistyped option prints the usage and `demo_workspace.py: error:` with
  exit code 2.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Not compatible with bounded signals · endpoint resolution refused.` after about 2 s | Ollama is not running, so nothing listens on `127.0.0.1:11434` | Start the Ollama app (or `ollama serve`), warm the model, and check again. |
| `Not compatible with bounded signals · protocol incompatible.` at once, with the hint `The local server refused this request. The usual cause is a model ID that is not installed` | The Model ID is not installed, or the endpoint lacks strict JSON-schema output | Compare the Model ID with `ollama list`; run `ollama pull qwen2.5:7b-instruct`, or save a new profile revision whose Model ID matches exactly. |
| `Not confirmed: the model did not answer within the check's time limit.` | The model was still loading after 120 seconds | Run the warm-up command the hint shows, `ollama run qwen2.5:7b-instruct --keepalive 30m hello`, then check again. |
| **Start bounded cognition run** stays disabled: `Check bounded-signal compatibility for this profile revision to enable the run.` | No passing check in this server process for this exact revision | Click **Check compatibility**; repeat after a restart or after changing Operation, Source, or profile. |
| A remote profile shows Credential `Required` and **Check compatibility** stays disabled | The server has no API key for that profile | Set the bound environment variable before starting the workbench, or save the key once in **API credential**. |
| `Not compatible with bounded signals · invalid result.` for a remote Qwen profile such as `gapgpt-qwen-3.6` | The model spent its whole answer budget thinking: this profile revision does not turn thinking off | Save a new profile revision with **Turn off model thinking (Qwen on vLLM servers)** checked, select it, and check again. |
| `Not compatible with bounded signals · authentication refused.` for a remote profile | The endpoint rejected the API key | Set the bound environment variable (for example `GAPGPT_API_KEY`) to the right key and restart the workbench, or save the key again in **API credential**. |
| `Not confirmed: the model did not answer within the check's time limit.` for a remote profile | The gateway did not answer within 60 seconds | Check again; if it repeats, the gateway is overloaded: use the local model or [the offline mock path](#without-a-working-model-mock-and-rules). |
| Every funded response shows **Fallback** with a reason such as `timeout-exhausted` | The model stopped, unloaded, or overloaded during the run | The run is still valid with neutral cognition. Warm the model, check again, and rerun with a new Run ID. |
| Responses listed as `Set aside · safety flag` or `Set aside · uncertain answer` | The model set a safety flag or answered `uncertain` | Working as designed: the categories stay visible as recorded but not applied, and the whole vector is neutral. The `Uncertain or safety neutralized` filter lists them. |
| Sources show **Cache** instead of **Provider** | Same inputs, seed, and profile revision as an earlier run in this workspace | Use a new seed or a fresh workspace folder. |
| `Start was refused: A saved run already uses this ID. Choose a new run ID.` | A saved run in this workspace has that ID; runs are never overwritten | Enter a new Run ID such as `demo-v10-002`, validate again, or use **↻ Run again with these inputs**, which suggests a free one. |
| `ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 50050)` right after the `city workbench:` line | Another process already uses the port | Find the owner with `Get-NetTCPConnection -LocalPort 50050 -State Listen`. Do not just try the next number: on the development laptop, system services hold 50051 and 50052. Check a candidate first: `Get-NetTCPConnection -LocalPort 48080 -ErrorAction SilentlyContinue` prints nothing for a free port, and the port must lie outside every range in the next row's command. Then start with, for example, `--port 48080` and use that port in every URL, including the backup script's `--base-url`. |
| `[winerror 10013] an attempt was made to access a socket in a way forbidden by its access permissions` | The port is in a Windows-reserved range | List the ranges with `netsh interface ipv4 show excludedportrange protocol=tcp` and pick a port outside them. Hyper-V and WSL reserve such ranges inside the dynamic range 49152-65535, and the ranges can move after a restart, so check again on the day; a port below 49152, such as 48080, normally stays clear of them. |
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
- Live model answers are not guaranteed to be reproducible; saved runs are. `city-replay`
  reapplies the frozen signals offline and must match every saved byte of evidence.
- The cognition evidence is exploratory: it is not validated psychology, not a prediction about
  any person, and not a sales forecast or campaign effect. Comparisons and studies of V10 runs
  are not supported yet, and `city-report` refuses them; each saved V10 run has its own
  browser run report in the guided view.
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
