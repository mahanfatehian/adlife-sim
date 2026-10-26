# AdLife Lab user guide

AdLife Lab runs on your own computer and shows, step by step, how a few fictional people
living in a fictional town meet an ad during one simulated day, which of them notice it, and
what an AI adds when it labels each noticed ad. You set up a study in the browser, watch it
run, replay it as a story on a map, and open a report you can print or share. The pages
speak English, German (Deutsch), and Persian (فارسی, right to left).

Every person, place, and product is fictional, and every number is simulated: nothing here
is observed behaviour, validated psychology, or a sales forecast.

## Install

You need Python 3.11, 3.12, or 3.13 and [uv](https://docs.astral.sh/uv/), the package and
environment manager this project uses.

1. Install uv once. On Windows (PowerShell):

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   On macOS or Linux:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Get the code and its dependencies:

   ```bash
   git clone https://github.com/mahanfatehian/adlife-sim.git
   cd adlife-sim
   uv sync
   ```

## Start the app

From the repository folder:

```bash
uv run adlife app
```

The app starts at `http://127.0.0.1:8765/guide` and opens in your default browser. It
listens on this computer only. Leave the terminal window open while you use it, and press
Ctrl+C there to stop it.

- Saved runs and settings go into the folder `adlife-workspace` next to where you started
  the app. Start it from the same folder again and your saved runs are back.
- `--port 9000` uses another port, `--workspace PATH` another folder, and `--no-open` skips
  opening the browser. `uv run adlife app --help` lists every option.
- `http://127.0.0.1:8765/` (the root address) also leads to the app.

## The start page

The start page explains the idea in three boxes (**How it works**), checks what is ready
(**Before you start**), sets up the **AI connection**, and lists your **Saved runs** with a
link to each run's story and report. Switch language at any time with **English**,
**Deutsch**, or **فارسی** at the top; every page keeps the page you are on.

Click **New simulation** to set up a study.

## Connect an AI

The AI never decides who sees or notices an ad: rules do. For each noticed ad it only picks
four labels from fixed lists (its reaction, how relevant and believable the ad is, and how
much it stood out). A fixed formula turns those labels into a separate "with AI" score, and
the rules-only score always stays next to it.

Choose where the labels come from:

| Choice | What it is | Needs |
| --- | --- | --- |
| **Online AI** | Any online OpenAI-compatible service | Its address, a model name, and an API key |
| **AI on this computer** | A model server on your computer, such as Ollama or LM Studio | Its address and a model name |
| **Practice answers** | Fixed offline answers for trying the app (not an AI) | Nothing |
| **No AI** | Rules only: "with AI" stays equal to "rules only" | Nothing |

### Online AI

1. Type the service's base URL in the address field, as its documentation gives it,
   for example `openrouter.ai/api/v1`. You may leave out `https://`; it is added for you.
2. Type the exact model name the service expects under the model field.
3. Paste your API key and click **Use this key**, or simply go on: the key is sent with the
   next test or run.
4. Click **Test connection**. The test asks one fixed question and uses a little of your AI
   budget; it checks the answer format, not whether the AI is right.

Your key is sent once to the app's local server and cleared from the page. By default the
server keeps it in memory only and forgets it when you stop the app. Tick
**Remember on this computer (in the system's credential store)** to keep it across restarts
in your operating system's credential store (Windows Credential Manager, the macOS
Keychain, or the Secret Service on Linux). The key is never written to a file, never shown
again, and never included in a saved run.

The service must support the OpenAI Chat Completions API with JSON-schema structured output
for the model you choose. AdLife was rehearsed with GapGPT (`api.gapgpt.app/v1`, model
`gapgpt-qwen-3.6`); with any other service, pass the connection test before you rely on it.
For Qwen models the app turns the model's step-by-step "thinking" off, so it answers quickly
with the four labels only.

Addresses you used before appear under the address field, so the next time you only pick
one and paste the key (unless you asked the computer to remember it).

### AI on this computer

Run a model server on your computer, then type its address: Ollama answers at
`127.0.0.1:11434/v1` (the default), LM Studio at `127.0.0.1:1234/v1`. Type the model name as
the server lists it, for example `qwen2.5:7b-instruct` in Ollama, and click
**Test connection**. No API key is needed.

### Practice answers and No AI

Both work offline and need no setup. **Practice answers** are clearly labelled "not an AI"
on every page; use them to learn the app or when the network is down.

### Starting with an approved service (optional)

For scripted setups, the server can approve an address and read its key from an environment
variable at startup instead. The key stays in that terminal's environment only:

```powershell
$secureKey = Read-Host "API key" -AsSecureString
$env:MYAI_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
uv run adlife app --provider-origin myai=https://api.example.com/v1 --provider-key-env myai=MYAI_API_KEY
```

```bash
read -rs MYAI_API_KEY && export MYAI_API_KEY
uv run adlife app --provider-origin myai=https://api.example.com/v1 --provider-key-env myai=MYAI_API_KEY
```

## Set up a simulation

The setup page has three cards and one button. Everything else uses tested defaults under
**More options**.

**1 · The ad**. Pick one of eight fictional products and where the ad appears (phone feed,
billboard, or both):

| Product | What it is |
| --- | --- |
| Lumen Pocket | a pocket device |
| Ember Cup Pass | a café membership |
| Cinder Crunch Box | a quick meal |
| Parcel Finch | home delivery |
| Spokelark Pass | a bike-share pass |
| Pagewren Box | a book subscription |
| Pulsefern Coach | a fitness app |
| Humvale Stream | music streaming |

**Town**. Pick one of five fictional towns; a small preview shows its streets. Their streets
decide where people live, work, and spend their free time:

| Town | Character |
| --- | --- |
| Fictional Grid City | a small regular grid |
| Fictional Harbour Loop | a loop around a harbour |
| Fictional River Junction | arms meeting at a river crossing |
| Fictional Old Town | a ring road, irregular lanes, and two bridges |
| Fictional New District | a dense grid with diagonals, a park, and an industrial edge |

**2 · The people**. Choose up to 30 people from ten fictional profiles. **Audience** offers
ready mixes: **Two contrasting groups** (four Lumo, who love new things, and four Veyra, who
watch prices and distrust ads), **Two tech-fan groups**, and **All personas** (three of each
profile). The rules use only each profile's interests and traits: names and life stories never
change the rules-only results. The AI reads the whole profile (name, age, job, life story,
personality type, interests, and traits) and answers as that person, so in a new run these can
change the "with AI" results. Each life story is short and invented: daily life and hobbies,
one dream, and one current worry. Runs saved by an earlier version never sent names or life
stories to the AI, and they still open unchanged.

| Profile | In short |
| --- | --- |
| Lumo | loves new things, remembers phone ads, fairly open to ads |
| Veyra | watches prices, distrusts ads, decides carefully |
| Orilo | remembers billboards, decides carefully |
| Nuvin | remembers phone ads, not price-conscious |
| Pimo | remembers phone ads, acts on impulse, open to ads |
| Zarel | strongly distrusts ads, decides carefully, forgets phone ads |
| Ketra | remembers billboards, watches prices, decides carefully |
| Tavi | loves new things, watches prices, remembers phone ads |
| Selun | decides very carefully, prefers the familiar, forgets phone ads |
| Halvi | open to ads, not price-conscious, keen on new things |

**3 · The AI**. Your AI choice from the start page, which you can change here, and the AI
limit: how many noticed ads at most are sent to the AI. Choose 60, 120, 250 or 500 questions,
or **Every noticed ad** (up to 1,000). Online AI starts with every noticed ad; AI on this
computer starts with 120, because a local server answers one question at a time. The
questions are spread evenly over the whole run, so every simulated day gets some; noticed ads
that are not chosen get no AI effect. A question that fails is tried again a few times
(waiting a little longer each time) before it counts as "no AI effect". Runs saved by an
earlier version keep their own limit, at most 60 questions sent earliest first, and still
open unchanged.

**Inner thoughts (optional)**, under the AI limit, has a box for each of English, Deutsch,
and فارسی. Tick any combination: only German, German and Persian, all three. The AI
then also writes what each person is thinking when they notice the ad, and the story shows
it on that moment. Each language makes every answer longer, so the page shows the extra
token estimate; leave every box empty to keep thoughts off. The boxes appear only for
online AI and AI on this computer. Turning thoughts on needs its own AI test, so the button
may read **Test the AI and run** again.

The story replays the whole simulated period: its key moments are spread over every day, and
its last step shows the end of the last day with the totals of each day.

**More options** holds the simulated days, the ad hours, how often ad slots come up, the
price, the interests the ad targets, the run name, and the random seed. The same settings and
seed always give the same rules-only results.

Click **Run simulation**. With an AI that has not passed its test since the app started, the
button reads **Test the AI and run** and tests it first.

## Watch it run

The progress page moves through four steps and, while the AI answers, counts the questions:
"3 of 10 answered". A question that fails counts as "no AI effect", and the story later shows
why. **Stop** cancels a run before anything is saved (it asks once more). When the run is
done, **Watch the story** opens it.

## The story

The story replays the run on a map of the town. Homes, workplaces, and free-time places are
marked with small labels, and the legend under the map explains every mark.

- The opening shows the headline and a funnel from ads shown to AI answers used. Press
  **Play the story** or **Next moment** to step through the **Key moments**;
  **All ad moments** shows every ad that was shown, noticed or not.
- Each moment card reads from top to bottom: what happened, the rules-only change, the AI's
  labels, and where the person ends up with AI, with paired bars for liking and ad memory.
  **How was this computed?** shows the exact arithmetic.
- In a run with inner thoughts, an answered moment also shows what that person is thinking: the
  thought in the page language (or every language the run has), with **Other languages**
  for the rest. The AI wrote it as this fictional person, and it changed no number.
- **People** shows one card per person: what they noticed, their most common labels, and
  where they ended. Follow a person to see only their moments.
- **Summary** shows the whole run: the funnel, what the AI said across all answers, and the
  averages with rules only and with AI side by side.

Keys: Page Down and Page Up (or the arrow keys) step through moments, Space plays or pauses,
Home returns to the opening, and 1, 2, or 3 switch tabs. A presentation clicker works too.

## The report

**Open the report** shows the whole run as one readable page: **In one minute**,
**How to read this report**, **The whole run**, **The people and their ad moments**,
**About the AI**, **What this is, and what it is not**, and **Technical details**. Switch
its language at the top. **Download this report** saves a self-contained HTML file that
opens without the app or an internet connection, so you can share or print it.

## Saved runs and offline checks

Every run is saved under `adlife-workspace/city-runs/<run-name>/` with its inputs and
fingerprints. To check that a saved run is reproducible, recompute it without the AI:

```bash
uv run adlife city-replay ./adlife-workspace <run-name>
```

## Troubleshooting

| You see | What to do |
| --- | --- |
| The address is "not allowed" | Use the service's https address, or `127.0.0.1` with a port for a server on this computer. |
| "Not connected: the service could not be reached safely." | Turn off any VPN "fake IP" mode or proxy, check the internet connection, then test again. |
| "Not connected: the service refused the key." | Check the key and the account balance, paste the key again, then test again. |
| "Not connected: no answer in time." | Test again; if it keeps happening, use practice answers. |
| "Not connected: the service limited the number of requests." | Wait a minute, then test again or choose a smaller AI limit. |
| "Not connected: the answer format cannot be used." | Check the model name, and that the service supports JSON-schema answers for it. |
| "Not connected: the answer did not use the required labels." | Test again; if it repeats, choose another model. |
| An AI on this computer does not answer | Start the model server, load the model, and check its address and port. |
| The page says the local server did not answer | The terminal running `adlife app` was closed: start it again and reload the page. |
| `adlife app` says the port is in use | Another program uses port 8765: stop it or start with `--port 9000`. |

## What the results mean

- Every person, place, and product is fictional, and the results are simulated.
- The AI only picks labels from fixed lists; AdLife's fixed formula turns them into the
  separate "with AI" score, and the rules-only score always stays next to it. The labels are
  an AI playing a fictional person, not anyone's real thoughts or feelings.
- In a new run each question tells the AI who the person is, how they feel about the brand
  right now (from the rules only), the ads of this campaign they noticed before, and where
  they are and what they are doing. It never shows the AI its own earlier answers.
- An AI told a person's age, job, and personality type can fall back on stereotypes. An answer
  the AI marks as a stereotype gets no AI effect, but it may not notice every one.
- The purchase-interest score comes from the rules only. It is not a purchase probability
  or a forecast: purchases are not simulated.
- A new run with the same settings repeats the rules exactly, but an online AI can answer
  differently. A saved run always replays the same, without calling the AI.
- AdLife records the model name it asked for; it cannot confirm which model version the
  service actually ran.

For the scientific method, assumptions, and limits, see the
[methodology documents](methodology/limitations.md) and the [README](../README.md).
