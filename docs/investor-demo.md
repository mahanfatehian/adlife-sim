# The five-minute demonstration

A scripted, defense-ready demonstration: every step runs offline on a laptop, every
number on screen is re-derivable from an artifact, and nothing in the script claims real-
world validation. Total time: about five minutes.

## 0. The problem (60 seconds, spoken)

Advertising decisions are made on dashboards that show what happened, never why. Controlled
experimentation on real audiences is slow, expensive, and ethically fraught. AdLife Lab is
a local, inspectable simulator of a small synthetic consumer society where a campaign
question can be asked as a controlled experiment: same people, same world, same randomness,
one declared difference — and the answer is attributable to that difference alone.

## 1. The offline demo (60 seconds)

```bash
uv run adlife demo --headless
```

Twenty synthetic agents, three simulated days, mock cognition, no network, no credentials.
The summary on screen is the artifact's own numbers — events, exposure, notice, shares.

## 2. The experiment: phone versus billboard (90 seconds)

```bash
uv run adlife compare control-study treatment-study --seeds 50
```

Explain while it runs: both arms run per seed with identical populations, worlds, and keyed
random streams — a paired difference is the treatment effect and nothing else. Point at the
A/A control (same scenario twice: exactly zero) as the null the machinery had to satisfy
before any treatment result meant anything. Mention the 80% directional-stability rule and
that sensitivity sweeps report which parameter perturbations flip the ranking.

## 3. Causal replay (45 seconds)

```bash
uv run adlife replay <study> <run-id>
```

The engine re-executes the stored run from its frozen inputs and verifies the event stream
event for event. Every event carries its causes; any number in the report can be walked
back to the moments that produced it.

## 4. The methodology disclosure (30 seconds, spoken — do not skip)

State plainly: every agent is fictional; results are synthetic and exploratory; nothing
here is calibrated against real consumers; external validity is unknown. The value
proposition is a transparent, reproducible instrument for studying mechanisms and relative
effects — not a crystal ball. The [model card](methodology/model-card.md) and
[limitations](methodology/limitations.md) say all of this in writing, and every generated
report carries the disclosure automatically.

## 5. The reusable core boundary (30 seconds)

The model core imports no UI, no database, no provider: interfaces are ports, implemented
by adapters. The CLI, the live dashboard, and the report generator are all consumers of the
same core. That boundary is enforced by tests — which is exactly what makes the engine
embeddable elsewhere later.

## 6. Where a product could go (15 seconds, honest)

The AGPL-licensed engine in this repository is the research instrument. Because the core is
adapter-free, a future, separate commercial offering could be built *on* the engine while
this repository remains open and inspectable. No hosted service exists today; any such
product would be a distinct codebase and a separate decision.

## What NOT to say

- No accuracy percentages — none exist and none are claimed.
- No claim of validation against actual consumers — never run, never claimed.
- No adoption, revenue, or investment figures — none exist.
- No absolute market numbers — results are relative, paired differences.

If asked for proof of external validity, the honest answer is the model card's own words:
**unknown, no calibration performed** — and that honesty is the credibility.
