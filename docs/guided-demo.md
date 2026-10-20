# Guided demo: presenting AdLife Lab to a live audience

This is the presenter guide for the guided view: the plain-language pages at `/guide` that
show a simulation in English, Deutsch, or فارسی (right to left). It covers starting the
server with an online AI, the backups to record beforehand, a 10-minute script, what to do
when the online AI fails, and a rehearsal checklist. The technical workbench and the
saved-run inspector stay one click away on every guided page (**Technical view**, which opens
in a new tab); their own step-by-step script is [investor-demo.md](investor-demo.md).

The server and the browser run on one Windows laptop over loopback; only the online AI's
questions leave it. Every person, place, and product is fictional, and every number is
simulated: nothing shown is observed behaviour, validated psychology, or a sales forecast.
Commands are for Windows PowerShell and run from the repository folder after `uv sync`.

## What the audience sees

| Page | Address | What it shows |
| --- | --- | --- |
| Start | `/guide` | "How it works" in three boxes, the **Ready to present?** preflight, and the **Saved runs** |
| Setup | `/guide/new` | Three cards (the ad, the people, the AI) and one button |
| Progress | `/guide/jobs/<job-id>` | The four steps of a run and the live "3 of 10 answered" counter |
| Story | `/guide/runs/<run-id>` | An animated map of the fictional town and the **Story**, **People**, and **Summary** tabs |
| Report | `/guide/runs/<run-id>/report` | A printable report of one saved run, with a download that works without a server |

Every guided page and the report carry the same language links at the top (**English**,
**Deutsch**, **فارسی**). Switching language keeps the page you are on; Persian pages read
right to left, while the map, the numeric bars, run names, and model names stay left to
right.

## Before the audience

### 1. Start the server with the online AI (about 5 seconds)

Open a PowerShell window in the repository folder and run:

```powershell
$secureKey = Read-Host "GapGPT API key" -AsSecureString
$env:GAPGPT_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
uv run adlife city-workbench --workspace .\demo-guided --port 50050 `
  --provider-origin gapgpt=https://api.gapgpt.app/v1 --provider-key-env gapgpt=GAPGPT_API_KEY
```

- The first line asks for the key without showing it: paste it and press Enter. It never
  appears on screen or in the command history.
- The second line keeps the key in this PowerShell window's environment only. It is never a
  command-line argument, never written to the workspace, and gone when the window closes.
- The third command starts the server, approves the GapGPT address for this process only,
  and tells the server which environment variable holds that service's key.

Expected: a single line, `city workbench: http://127.0.0.1:50050/guide`. Leave the window
open (Ctrl+C stops the server). The command never opens a browser itself: open that address
in Chrome or Edge.

- Use the fresh folder `.\demo-guided`. Runs saved by earlier development builds of this
  format are refused by this version, so an older folder such as `.\demo-workbench` may list
  them as unavailable. Leave `.\demo-workbench` as it is; do not delete it on the day.
- Only one server can use a port. Stop any other workbench on port 50050 first (Ctrl+C in its
  window). To rehearse on a computer where another server already uses 50050, start with a
  separate folder and another free port, for example `--port 48080`, and use that port in
  every address. The [port troubleshooting rows](investor-demo.md#troubleshooting) explain
  how to check that a port is free.

### 2. Test the AI connection (one paid request, about 6 seconds)

On the start page, the **AI connection** card shows **Online AI**, the service
`gapgpt · api.gapgpt.app`, the model `gapgpt-qwen-3.6`, and "Key available on the server".
It also notes that model thinking is turned off for this Qwen model, which keeps its answers
short enough. Click **Test connection**. The test asks one fixed question, uses a little of
the AI budget, and checks the answer format, not whether the AI is right. While it waits, the
card counts the seconds. Expected after about 6 seconds: "Connected: the AI answered in the
format AdLife needs." The preflight then reads "AI connection tested".

A passing test is remembered only while this server process runs. After a restart, test
again, or let **Test the AI and run** on the setup page run the test before it starts.

If the card says "This service needs an API key.", the server was started without the key:
stop it, repeat step 1 in the same window, and reload the page. Saving the key on this
computer is a separate opt-in under **Or save the key on this computer** (paste it, then
**Save key**). It stores the key in Windows Credential Manager, under a name that contains
`adlife.city-provider.v1`, until you remove it there. The environment variable is the
recommended path because nothing outlives the PowerShell window.

### 3. Prepare the room

- Press Ctrl and + until the page fills the projector (125% suits a 1280×720 projector).
  Every guided page reflows without sideways scrolling.
- A presentation clicker works on the story page: it sends Page Down and Page Up.
- If the computer has animations turned off, the preflight says so and the story steps from
  moment to moment without motion. Nothing else changes.

## Backups

Record two backup runs in `.\demo-guided` through the guide itself (**New simulation** on
the start page), so a finished run is always one click away:

1. **Practice answers** run of the default study. Keep every default, choose
   **Practice answers** in card 3, and click **Run simulation**. It finishes in a few
   seconds, uses seed 42, and makes no network call. Practice answers are fixed offline
   answers, clearly labelled "not an AI", and they write no saved AI answers, so the live run
   still asks the AI.
2. **Online AI** run of the coffee study. Choose Ember Cup Pass in card 1, keep
   **Online AI** with the AI limit of 10 questions, and click **Run simulation** (it reads
   **Test the AI and run** when the connection has not passed its test yet). Its study
   (`guided-coffee`) differs from the live device study (`guided-device`), so the live run on
   stage still gets seed 42 and fresh answers from the AI.

Do not run the device study with the online AI in this folder before the audience. The guide
picks the seed automatically so that a live run never reuses saved AI answers: after such a
rehearsal, the live run would get seed 43, and its story would differ from the one described
below. That run would still be valid, just different.

The start page lists both backups under **Saved runs**, and the preflight counts them
("2 guided runs saved as backups").

## The 10-minute script

| Time | Screen | Action |
| --- | --- | --- |
| 0:00–0:45 | Start | The three-box explanation, the green preflight; switch to **فارسی** and back |
| 0:45–1:30 | Setup | Keep Lumen Pocket and **Two contrasting groups**; **Online AI**, limit 10; **Run simulation** |
| 1:30–2:15 | Progress | "3 of 10 answered": the AI only picks four labels |
| 2:15–6:30 | Story | Headline, then **Next moment**: Veyra's morning moments, the commute, Lumo in the evening, the over-limit moment; the **People** tab (Lumo #3 never noticed the ad); the **Summary** tab |
| 6:30–8:30 | Report | **Open the report**; switch to **فارسی** (right to left) and **Deutsch**; the feedback per person; the limits box; **Download this report** |
| 8:30–10:00 | Technical view | Questions; the exact maths and fingerprints in the inspector |

The moments and counts below come from the default study with seed 42 in rehearsal. The
rules part repeats exactly; the AI's labels can differ from run to run.

### 0:00–0:45 · Start

- Read the three boxes: rules decide who sees the ad and who notices it; the AI labels each
  noticed ad (reaction, relevance, believability, and how much it stood out); a fixed formula
  turns those labels into a separate "with AI" score, and the rules-only score stays next to
  it.
- Point at the green preflight: AI connection tested, backups saved.
- Click **فارسی**: the page turns right to left with Persian digits. Click **English** to
  return.

### 0:45–1:30 · Setup

- Click **New simulation**. Card 1 keeps Lumen Pocket on the **Phone feed**, aimed at people
  who like commuting and technology.
- Card 2 keeps **Two contrasting groups**: four Lumo, who love new things, and four Veyra, who
  watch prices and distrust ads. Say that the names are decoration: they never reach the AI
  and do not change results; interests and traits do.
- Card 3 keeps **Online AI** with the **AI limit** of 10 questions. Only noticed ads are sent,
  earliest first; noticed ads over the limit get no AI effect.
- Leave **More options** closed: it holds tested defaults (one simulated day, ads from 07:00
  to 21:00, an automatic random seed).
- Click **Run simulation**.

### 1:30–2:15 · Progress

- The tracker moves through "Simulating the day", "Asking the AI", "Saving and checking", and
  "Done". While the AI answers, the counter climbs: "3 of 10 answered".
- Say what the AI does here: for each noticed ad it only picks four labels from fixed lists.
  It writes no text, gives no numbers, and cannot move anyone or decide who notices an ad.
- Answers arrive in small groups, so the counter can pause while the elapsed time keeps
  running. With GapGPT a 10-question run took about 30 seconds in rehearsal. A question that
  fails counts as "no AI effect" and the story shows why.
- When it reads "Done", focus moves to **Watch the story**: click it.

### 2:15–6:30 · Story

- The opening shows the headline and a five-number funnel: 8 people spent one simulated day in
  Fictional Grid City; the ad was shown 27 times and noticed 13 times. Press Page Down or click
  **Play the story**.
- Each moment card is one chain, read top to bottom: what happened ("07:17 · Veyra #6 noticed
  the Lumen Pocket ad on the phone at home."), the **Rules only** row, the AI labels, and the
  **With AI** row, with paired bars for liking and ad memory. **How was this computed?** opens
  the exact arithmetic.
- Walk through Veyra's morning: Veyra #6 at 07:17 and Veyra #8 at 07:39, both at home, then
  Veyra #8 again at 08:12 while commuting, when the map follows the trip to work.
- Continue to the evening, when the Lumo moments arrive (Lumo #2 at 17:26, Lumo #4 at 18:00),
  and compare the labels of a Veyra moment with those of a Lumo moment. The labels come from
  the AI, so they can differ from the rehearsal.
- Stop at the first moment marked **Over the AI limit** (Veyra #8 at 20:18): it was noticed,
  but the 10 questions were used up, so it gets no AI effect and the rules-only result stands.
- Open the **People** tab: Lumo #3's card shows that this person never noticed the ad, so
  nothing changed for them. "Follow" on a card limits the story to that person.
- Open the **Summary** tab: the funnel from ad moments to AI answers used, what the AI said
  across all answers, and the averages with rules only and with AI side by side.

Keys on the story page: Page Down or the arrow in reading direction for the next moment, Page
Up for the previous one, Space to play or pause, Home to return to the opening, and 1, 2, or 3
for the tabs.

### 6:30–8:30 · Report

- Click **Open the report**. Show **In one minute** (the headline and the funnel) and
  **How to read this report**.
- Switch to **فارسی**: the whole report reads right to left. Then show **Deutsch**.
- In **The people and their ad moments**, show one person's most common AI labels and their
  ad moments as sentences.
- Show the limits box, **What this is, and what it is not**, and read one line aloud.
- Click **Download this report**. The file opens without the server or an internet
  connection, so it can be emailed after the talk.

### 8:30–10:00 · Technical view

- Take questions. **Technical view** opens the saved-run inspector in a new tab with the
  per-response trace, the exact formula, the frozen inputs, and their fingerprints.
- To show that a saved run is checkable offline, run
  `uv run adlife city-replay .\demo-guided <run-id>` in a second PowerShell window. It
  recomputes the saved run and never calls the AI.

## If the online AI fails

The connection test and the setup page explain every failure in plain words:

| The guide says | What to do |
| --- | --- |
| "Not connected: the service could not be reached safely." | Turn off any VPN "fake IP" mode or proxy, check the internet connection, then test again. |
| "Not connected: the service refused the key." | Check the key and the account balance, restart the server with step 1, then test again. |
| "Not connected: no answer in time." | Test again; if it keeps happening, use practice answers. |
| "Not connected: the service limited the number of requests." | Wait a minute, then test again or choose a smaller AI limit. |
| "Not connected: the answer did not use the required labels." | Test again; if it repeats, choose another model or use practice answers. |
| "Online AI is not set up on this server." | Stop the server and start it again with step 1. |
| "The local server did not answer. Is its terminal window still open?" | Start the server again with step 1 and reload the page. |

If the AI cannot be reached in time, do not debug on stage:

- In card 3, choose **Practice answers** (labelled "not an AI") and click
  **Run simulation**. The run needs no network and finishes in a few seconds; every page
  says plainly that practice answers are not an AI.
- Or open a backup: on the start page, **Saved runs**, then **Open story** or **Report**.
- If the AI fails during a live run, the run still completes: failed questions show
  **AI failed** with the reason and get no AI effect. **Stop** cancels a run before it is
  saved; it asks once more before stopping.

## Rehearsal checklist

1. The live check passes: **Test connection** reads "Connected" on the presenting laptop, on
   the network you will use on the day.
2. One live 10-question run completes (the coffee backup above), and its story and report open
   in all three languages.
3. The backups are listed on the start page under **Saved runs**.
4. The zoom is set for the room, and the story page fits the projector without scrolling.
5. Never touch `.\demo-workbench` or port 50050 from the development laptop while it is
   serving.

## What to say, and what not to say

- Say that every person, place, and product is fictional, and that results are simulated.
- Say that the AI only picks labels from fixed lists; AdLife's fixed formula turns them into
  the separate "with AI" score, and the rules-only score always stays next to it.
- Do not call the labels anyone's thoughts or feelings: they are the AI's ratings of an ad for
  a fictional profile.
- Do not present the purchase-interest score as a purchase probability or a forecast:
  purchases are not simulated, and the score comes from the rules only.
- Do not claim a campaign effect, and do not compare two runs as an experiment.
- Do not promise that a new live run gets the same AI answers. A saved run always replays the
  same, without calling the AI.
- AdLife records the model name it asked for; it cannot confirm which model version the
  service actually ran.

## After the demo

Press Ctrl+C in the server window, then close the window: the key in its environment goes
with it. If you saved the key on this computer instead, remove the `adlife.city-provider.v1`
entry in Windows Credential Manager when you no longer need it.
