# The five-minute demonstration

A scripted, defense-ready demonstration: every step runs offline on a laptop, every
number on screen is re-derivable from persisted inputs/results, and nothing in the script
claims real-world validation. Allow about five minutes of explanation plus computation;
runtime depends on the machine and study size.

Before presenting, prepare two fresh, validated study folders that differ only in the
declared campaign treatment. Use small studies for the live comparison and preserve the
full 50-seed research comparison separately. Compare refuses existing run destinations;
use fresh copies when repeating this script. See [quickstart](quickstart.md) for setup.

## 0. The problem (60 seconds, spoken)

Advertising decisions are made on dashboards that show what happened, never why. Controlled
experimentation on real audiences is slow, expensive, and ethically fraught. AdLife Lab is
a local, inspectable simulator of a small synthetic consumer society where a campaign
question can be asked as a controlled experiment: same people, same world, same randomness,
one declared treatment — and the paired result describes that treatment within the model.
It does not establish an effect on real consumers.

## 1. The offline demo (60 seconds)

```bash
uv run adlife demo --headless
```

Twenty synthetic agents, three simulated days, mock cognition, no network, no credentials.
The summary on screen is the artifact's own numbers — events, exposure, notice, shares.

## 2. The comparison and its limits (90 seconds of explanation)

```bash
uv run adlife compare control-study treatment-study --seeds 2 --treatment-path campaigns
```

Explain while it runs: both arms run per seed with identical populations, worlds, and keyed
random streams. This two-seed command is a workflow smoke, not a statistical conclusion.
Show the separately prepared A/A control (same scenario twice: exactly zero) before reading
a treatment result. If the treatment switches phone to billboard, it changes exposure
opportunities too: the result is not an isolated channel effect. Mention the 80%
directional-stability rule and that sensitivity sweeps report parameter-dependent rank flips.

## 3. Verified replay (45 seconds)

```bash
uv run adlife replay <study> <run-id>
```

The engine re-executes the stored run from its frozen inputs and verifies the event stream
event for event after normalizing structured run identifiers. Events carry recorded causal
references where applicable; manifests, checkpoints, and results supply state/provenance.
The event stream is auditable, not a complete event-sourced encoding of every state field.

## 4. The methodology disclosure (30 seconds, spoken — do not skip)

State plainly: every agent is fictional; results are synthetic and exploratory; nothing
here is calibrated against real consumers; external validity is unknown. The value
proposition is a transparent, reproducible instrument for studying mechanisms and relative
effects — not a crystal ball. The [model card](methodology/model-card.md) and
[limitations](methodology/limitations.md) say all of this in writing, and every generated
report carries the disclosure automatically.

Also disclose that standard CLI/demo states start with no active purchase need and zero
disposable budget, so their purchase count is structurally zero. Purchase-enabled core
scenarios require explicit initial states; do not present the default demo as sales prediction.

## 5. The reusable core boundary (30 seconds)

The model core imports no UI, no database, no provider: interfaces are ports, implemented
by adapters. The CLI, the live dashboard, and the report generator are all consumers of the
same core. That boundary is enforced by tests — which is exactly what makes the engine
embeddable elsewhere later.

## 6. A failure question (15 seconds)

Explain that the demo requires no API, invalid input is refused cleanly, and replay refuses
corrupt/missing recorded inputs rather than fabricating a match. The detailed recovery
boundaries and executed checks are in the [defense audit](defense-readiness.md).

## What NOT to say

- No accuracy percentages — none exist and none are claimed.
- No claim of validation against actual consumers — never run, never claimed.
- No adoption, revenue, or investment figures — none exist.
- No absolute market numbers — results are relative, paired differences.
- No isolated phone-versus-billboard channel-effect claim.
- No statistical conclusion from the two-seed live smoke.
- No default-demo purchase forecast: its purchase count is structurally zero.

If asked for proof of external validity, the honest answer is the model card's own words:
**unknown, no calibration performed** — and that honesty is the credibility.
