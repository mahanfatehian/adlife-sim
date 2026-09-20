"""`adlife compare CONTROL TREATMENT`: a paired whole-run comparison of two studies."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from adlife.adapters.storage.sqlite_store import SQLiteRunStore
from adlife.cli.cognition import RuleCognitionPort, rule_fallback_for
from adlife.cli.errors import command_boundary
from adlife.cli.project import load_project
from adlife.core.experiments.comparison import ExperimentRunner
from adlife.core.experiments.design import QUICK_SEED_COUNT, ExperimentDesign, _slug
from adlife.core.simulation.runner import RunIdentity


@command_boundary
def command(
    control_path: Annotated[Path, typer.Argument(help="The control study directory.")],
    treatment_path: Annotated[Path, typer.Argument(help="The treatment study directory.")],
    seeds: Annotated[
        int,
        typer.Option("--seeds", min=1, max=200, help="How many paired seeds to run."),
    ] = QUICK_SEED_COUNT,
    treatment_path_option: Annotated[
        str,
        typer.Option(
            "--treatment-path",
            help="The scenario document path the treatment may change.",
        ),
    ] = "campaigns",
) -> None:
    """Run both arms per seed through the experiment runner and pair the differences."""
    control = load_project(control_path)
    treatment = load_project(treatment_path)

    design = ExperimentDesign(
        label=f"{control.root.name}-vs-{treatment.root.name}",
        treatment_paths=(treatment_path_option,),
    )
    identity = RunIdentity.for_project(
        project_root=control.root,
        provider="rules",
        model_id="rule-baseline",
    )
    runner = ExperimentRunner(
        store_factory=lambda: SQLiteRunStore(control.root),
        cognition_factory=lambda model: RuleCognitionPort(),
        fallback_provider_factory=rule_fallback_for,
        identity=identity,
    )
    import asyncio

    seed_tuple = tuple(range(seeds))
    result = asyncio.run(
        runner.compare(
            control.scenario,
            treatment.scenario,
            seeds=seed_tuple,
            design=design,
            run_id_prefix=f"cmp-{_slug(design.label)[:20]}",
        )
    )
    document = {
        "label": result.label,
        "seeds": list(result.seeds),
        "metrics": {
            name: {
                "mean_paired_difference": statistic.mean_paired_difference,
                "std_paired_difference": statistic.std_paired_difference,
                "median_paired_difference": statistic.median_paired_difference,
                "ci_low": statistic.ci_low,
                "ci_high": statistic.ci_high,
                "effect_size": statistic.effect_size,
                "agreement_fraction": statistic.agreement_fraction,
                "direction": statistic.direction,
            }
            for name, statistic in result.metrics.items()
        },
    }
    from adlife.cli.output import emit_json

    emit_json(document)
