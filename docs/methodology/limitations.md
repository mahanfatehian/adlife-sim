# Limitations

What this model cannot support. Read alongside the
[model card](model-card.md) and the [experiment protocol](experiment-protocol.md).

## Scale

- **1–30 agents.** Social dynamics at this scale are conversation networks, not
  markets or epidemics. Network-level phenomena (cascade thresholds, opinion leaders)
  cannot be studied honestly here.
- **1–7 simulated days.** Long-horizon effects — brand building, churn, seasonality —
  are out of scope by construction.
- **Two advertising channels** (mobile feed, highway billboard), each with fixed
  placement geometry. Media mix beyond these two, and creative-quality variation, are
  not modelled.

## Behavioural simplifications

- **Purchase is a proxy, not a transaction model.** It is a rule-derived threshold plus
  draw over budget and intention. No payment psychology, no categories beyond the
  campaign's reference price, no post-purchase behaviour beyond a memory.
- **Routines are templates.** Agents follow generated daily routines; they do not plan,
  adapt their schedules, or respond to congestion.
- **Memory is five slots.** Retention keeps the strongest five episodic memories;
  anything subtler (graded recency curves, interference between memories) is not
  represented.
- **Relationships are static edges.** Strength is fixed per run; no friendship formation
  or decay.
- **Word of mouth is one message per edge per tick** with a single share probability;
  message content is a valence/strength pair, not a narrative.

## Validity

- **No calibration.** No parameter has been fitted to real-world data; all constants are
  documented design choices. External validity is unknown (see the model card).
- **Results are relative, not absolute.** The honest reading of a comparison is the
  paired difference between arms of the *same* synthetic world — never an absolute
  "market size" or "expected sales" number.
- **Channel-opportunity confounding.** Phone and billboard arms differ in exposure
  opportunity as well as channel; see the experiment protocol's warning before reading
  channel effects.

## Engineering caveats

- **Hybrid mode is not reproducible in its provider answers** — by design, provider
  output never touches numeric outcomes, and every fallback is recorded; replay answers
  from the cache. Rules and mock modes are exactly reproducible.
- **Console encoding.** On non-UTF-8 consoles (a default Windows `cp1252` shell),
  non-ASCII output may need `PYTHONUTF8=1`; `adlife doctor` reports this.
- **The live dashboard needs a real terminal.** In piped or non-interactive shells,
  `--live` falls back to headless (or refuses with `--no-headless-fallback`).

## What would falsify the tool's usefulness

If paired A/A comparisons under the pinned protocol ever produce nonzero differences, or
replay of a stored run fails to reproduce its event stream, the tool's central guarantees
are broken and its results should not be trusted until fixed. The test suite treats both
as release blockers.
