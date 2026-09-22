# Experiment protocol (pre-registered)

This document pre-registers, inside the repository, the comparison protocol the
experiment tooling implements. Changing it after running studies should be done by adding
a dated addendum, not by editing the registration.

## Primary metrics

1. **Purchases** (rule-derived purchase proxies committed per arm).
2. **Purchase-intention delta** (mean paired difference of `purchase_intention` across
   the population, end of run vs. start).
3. **Notice rate** (noticed impressions ÷ eligible impressions).
4. **Word-of-mouth reach** (distinct receivers of a campaign message).

Secondary: reach, impressions, frequency, sentiment, recall, fatigue, direct and indirect
awareness, high-intention share, cognition counters. Every value carries its numerator,
denominator, and sources in the metrics fold.

## Controls

- **No-campaign control.** The same scenario with campaigns removed anchors what the
  campaigns themselves contribute; intention and sentiment movement without exposure is
  the baseline noise the treatment must exceed.
- **A/A control.** The same scenario compared with itself must return **exactly zero**
  paired differences for every metric on every seed. Any nonzero A/A result invalidates
  the harness, not the hypothesis. This check runs before any treatment comparison is
  read.
- **Paired A/B under common random numbers.** Both arms run per seed with the same seed,
  the same initial population, the same world, and the same keyed random streams; the
  only permitted difference is the declared treatment document. A paired difference is
  therefore the treatment effect and nothing else. The design validator refuses
  arms that differ in anything but the declared treatment path.

## Seeds

**50 full-study seeds** for a reported comparison (quick looks may use fewer —
`--seeds 20` — and must be labelled as exploratory). Seeds are the integers
`0..N−1`; both arms receive each seed.

## Decision rule: 80% directional stability

A comparison supports a directional claim only if the paired difference has the same
sign on **at least 80% of seeds** (≥ 40 of 50) for the primary metric, with the A/A
control at exactly zero. Below that threshold the honest report is "no stable direction
under this protocol", not a p-value mined from means.

## Sensitivity analysis

Sensitivity sweeps perturb **one** `ModelParameters` knob at a time at
**−20%, −10%, +10%, +20%** of its documented baseline (the documented `DEFAULT_DELTAS`),
holding all other knobs fixed, and report the campaign ranking (purchases first, then
intent-delta, then share-weighted reach) per arm. The reported risks are rank **flips**:
which perturbations change the ordering. The sweep covers the attention scale, the
persuasion gains, the three retention values, the share scale, the social gains, and the
homophily weight.

## Confounding warning: channel ≠ opportunity

Phone (mobile-feed) and billboard arms differ in **both** the channel *and* the exposure
opportunities the routines produce (a commuter passes billboards; a phone is always
present). A raw phone-versus-billboard difference confounds channel effect with
opportunity count. Interpret channel comparisons through the per-agent opportunity
counts the artifact records, or equalise opportunities by design; never read the raw
difference as "the channel's effect".

## Optional blinded human ranking

As an optional external-sanity exercise, generated diary lines may be ranked by human
raters blinded to arm; the ranking is reported alongside, never merged into, the
mechanical metrics. This is a legibility check, not a validation against real consumers.

## Exact rebuild command

```bash
uv run adlife compare control-study treatment-study --seeds 50
```

Both studies must validate (`adlife validate`), and the treatment may differ only in its
declared treatment document (`--treatment-path campaigns`, the default). The command
pairs runs per seed, computes the paired statistics, and refuses to run when the designs
are not comparable. The A/A check for a study against itself uses the same command with
the same study in both positions and must return all-zero differences.

## Reporting duties

Any published result from this software states: the seed count, the decision rule
outcome, the A/A result, the sensitivity-flip summary, and the synthetic-and-exploratory
disclosure. The self-contained HTML report embeds all of these automatically.
