"""Experiment design: what a paired comparison may vary, and what it may not.

A paired comparison borrows its honesty from the runs it drives: the same seed, the
same initial population, the same world, the same keyed random streams, on both arms -
so a paired difference is the declared treatment and nothing else. This module is the
guardrail that makes that true before a single tick runs:

* :class:`ExperimentDesign` declares the treatment's label and the scenario paths the
  treatment may change.
* :func:`validate_paired_design` refuses a scenario pair that differs in duration,
  population, initial states, sociality, world - or anywhere outside the declared
  paths - naming the first offending path.
* :func:`validate_paired_manifests` refuses a run pair that was not produced by the
  same engine build (package version, git SHA, lockfile, provider, model, prompt) or
  that does not carry the same seed: a paired difference across engine versions or
  seeds measures the harness, not the treatment.
* :func:`paired_statistics` turns per-seed paired differences into
  :class:`PairedStatistic` records - mean, standard deviation, median, a bootstrap 95
  percent interval drawn from :data:`BOOTSTRAP_SEED` (fixed, so a comparison is
  reproducible), a standardized effect size, and the fraction of seeds agreeing with
  the mean's direction. The direction is declared ``stable`` only when at least
  :data:`STABLE_DIRECTION_THRESHOLD` of the seeds agree; otherwise it is ``unstable``.

Seed-count conventions: :data:`FULL_SEED_COUNT` for the full academic experiment,
:data:`QUICK_SEED_COUNT` for continuous integration.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from adlife.core.domain.results import RunManifest
from adlife.core.domain.scenario import Scenario
from adlife.core.experiments.metrics import METRIC_NAMES, RunMetrics

FULL_SEED_COUNT = 50
"""Seeds for the full academic experiment."""

QUICK_SEED_COUNT = 20
"""Seeds for the quick continuous-integration comparison."""

BOOTSTRAP_RESAMPLES = 10_000
"""Bootstrap resamples behind every reported interval; fixed, never configured."""

BOOTSTRAP_SEED = 0x51EED_2026
"""The bootstrap's generator seed: fixed so an interval is reproducible byte for byte."""

STABLE_DIRECTION_THRESHOLD = 0.8
"""A direction is stable only when at least this fraction of seeds agree with the mean."""


class ExperimentDesignError(RuntimeError):
    """Raised when a paired comparison's inputs violate its declared design.

    The message names the first violated control - the field, the scenario path or the
    seed - so a refused experiment explains exactly which comparison it cannot honest.
    """


@dataclass(frozen=True)
class ExperimentDesign:
    """One paired comparison's declared treatment.

    ``treatment_paths`` are dotted paths into the scenario document - dictionary keys
    and list indices, as in ``campaigns`` or ``campaigns.0.frequency_cap`` - that the
    treatment scenario may change relative to the control. Everything else must be
    equal, and the validator refuses the pair naming the first path that is not.
    """

    label: str
    treatment_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PairedStatistic:
    """The paired differences of one metric across the seeds, with their receipt."""

    metric: str
    n_seeds: int
    mean_paired_difference: float
    std_paired_difference: float
    median_paired_difference: float
    ci_low: float
    ci_high: float
    effect_size: float
    agreement_fraction: float
    direction: Literal["stable", "unstable"]


@dataclass(frozen=True)
class ComparisonResult:
    """One completed paired comparison: per-seed metrics and per-metric statistics."""

    label: str
    seeds: tuple[int, ...]
    metrics: Mapping[str, PairedStatistic]
    control_runs: Mapping[int, RunMetrics] = field(default_factory=dict)
    treatment_runs: Mapping[int, RunMetrics] = field(default_factory=dict)


def _slug(value: str) -> str:
    slug = "".join(character if character.isalnum() else "-" for character in value.lower())
    slug = "-".join(part for part in slug.split("-") if part)
    if not slug or not slug[0].isalnum():
        raise ExperimentDesignError(f"label {value!r} must slugify into a run-id fragment")
    return slug[:31]


def _mask(tree: object, path: str) -> None:
    """Remove the declared treatment subtree from a scenario document, in place."""
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        if isinstance(node, dict):
            if part not in node:
                raise ExperimentDesignError(
                    f"treatment path {path!r} does not exist in the scenario (at {part!r})"
                )
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as error:
                raise ExperimentDesignError(
                    f"treatment path {path!r} does not exist in the scenario (at {part!r})"
                ) from error
        else:
            raise ExperimentDesignError(
                f"treatment path {path!r} walks through a scalar at {part!r}"
            )
    leaf = parts[-1]
    if isinstance(node, dict):
        if leaf not in node:
            raise ExperimentDesignError(
                f"treatment path {path!r} does not exist in the scenario (at {leaf!r})"
            )
        del node[leaf]
    elif isinstance(node, list):
        try:
            del node[int(leaf)]
        except (ValueError, IndexError) as error:
            raise ExperimentDesignError(
                f"treatment path {path!r} does not exist in the scenario (at {leaf!r})"
            ) from error
    else:
        raise ExperimentDesignError(f"treatment path {path!r} names a scalar leaf")


def _first_difference(left: object, right: object, path: str) -> str | None:
    """The dotted path of the first difference between two JSON documents, if any."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            if key not in left or key not in right:
                return f"{path}.{key}" if path else str(key)
            difference = _first_difference(
                left[key], right[key], f"{path}.{key}" if path else str(key)
            )
            if difference is not None:
                return difference
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}[{len(left)}vs{len(right)}]"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
            difference = _first_difference(left_item, right_item, f"{path}.{index}")
            if difference is not None:
                return difference
        return None
    return None if left == right else path


def _refuse(condition: bool, message: str) -> None:
    if condition:
        raise ExperimentDesignError(message)


def validate_paired_design(
    control: Scenario,
    treatment: Scenario,
    design: ExperimentDesign,
) -> None:
    """Refuse a scenario pair a paired comparison may not drive.

    The targeted checks come first and name their control; the generic masked-tree
    comparison then refuses any remaining difference outside the declared treatment
    paths, naming the first differing path.
    """
    control_document = control.model_dump(mode="json")
    treatment_document = treatment.model_dump(mode="json")

    _refuse(control.days != treatment.days, "duration differs: both arms must run the same days")
    _refuse(
        control_document["population"] != treatment_document["population"],
        "population differs: both arms must start from the same population",
    )
    _refuse(
        control_document["initial_states"] != treatment_document["initial_states"],
        "initial states differ: both arms must start from the same states",
    )
    _refuse(
        control.social_enabled != treatment.social_enabled,
        "social differs: both arms must agree on social_enabled",
    )
    _refuse(
        control_document["world"] != treatment_document["world"],
        "world differs: both arms must run the same world",
    )

    for path in design.treatment_paths:
        _mask(control_document, path)
        _mask(treatment_document, path)
    difference = _first_difference(control_document, treatment_document, "")
    _refuse(
        difference is not None,
        f"scenario change outside the declared treatment at {difference!r}: "
        f"declared paths are {list(design.treatment_paths)}",
    )


def validate_paired_manifests(manifest_a: RunManifest, manifest_b: RunManifest) -> None:
    """Refuse a run pair that does not measure the treatment on the same harness."""
    checks: tuple[tuple[str, object, object], ...] = (
        ("package_version", manifest_a.package_version, manifest_b.package_version),
        ("git_sha", manifest_a.git_sha, manifest_b.git_sha),
        ("lockfile_sha256", manifest_a.lockfile_sha256, manifest_b.lockfile_sha256),
        ("provider", manifest_a.provider, manifest_b.provider),
        ("model_id", manifest_a.model_id, manifest_b.model_id),
        ("prompt_version", manifest_a.prompt_version, manifest_b.prompt_version),
        ("prompt_hash", manifest_a.prompt_hash, manifest_b.prompt_hash),
        ("seed", manifest_a.seed, manifest_b.seed),
    )
    for name, value_a, value_b in checks:
        _refuse(value_a != value_b, f"{name} differs between the paired runs")


def paired_statistics(
    differences: Mapping[str, Sequence[float]],
) -> dict[str, PairedStatistic]:
    """Summarize per-seed paired differences per metric name."""
    generator = random.Random(BOOTSTRAP_SEED)
    summary: dict[str, PairedStatistic] = {}
    for name in METRIC_NAMES:
        if name not in differences:
            raise ExperimentDesignError(f"no paired differences supplied for metric {name!r}")
        values = tuple(float(value) for value in differences[name])
        count = len(values)
        if count == 0:
            raise ExperimentDesignError(f"no paired differences supplied for metric {name!r}")
        mean = sum(values) / count
        std = statistics.stdev(values) if count >= 2 else 0.0
        median = statistics.median(values)
        if any(values):
            bootstrap_means: list[float] = []
            for _ in range(BOOTSTRAP_RESAMPLES):
                sample_total = 0.0
                for _ in range(count):
                    sample_total += values[generator.randrange(count)]
                bootstrap_means.append(sample_total / count)
            bootstrap_means.sort()
            ci_low = bootstrap_means[int(0.025 * (BOOTSTRAP_RESAMPLES - 1))]
            ci_high = bootstrap_means[int(0.975 * (BOOTSTRAP_RESAMPLES - 1))]
        else:
            ci_low, ci_high = 0.0, 0.0
        effect = mean / std if std > 0.0 else 0.0
        mean_sign = (mean > 0.0) - (mean < 0.0)
        agreeing = sum(1 for value in values if (value > 0.0) - (value < 0.0) == mean_sign)
        agreement = agreeing / count
        summary[name] = PairedStatistic(
            metric=name,
            n_seeds=count,
            mean_paired_difference=mean,
            std_paired_difference=std,
            median_paired_difference=median,
            ci_low=ci_low,
            ci_high=ci_high,
            effect_size=effect,
            agreement_fraction=agreement,
            direction="stable" if agreement >= STABLE_DIRECTION_THRESHOLD else "unstable",
        )
    return summary


__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "FULL_SEED_COUNT",
    "QUICK_SEED_COUNT",
    "STABLE_DIRECTION_THRESHOLD",
    "ComparisonResult",
    "ExperimentDesign",
    "ExperimentDesignError",
    "PairedStatistic",
    "paired_statistics",
    "validate_paired_design",
    "validate_paired_manifests",
]
