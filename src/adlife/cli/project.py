"""Project assembly: a study directory in, a validated scenario out.

A project is ``adlife.yaml`` plus a population document plus campaign YAMLs; every
command consumes exactly that, so the translation lives here once. Assembly never
invents simulation semantics: the world is the documented default the routine
templates reference, populations come from the packaged generator or an explicit
``population.yaml``, campaigns come through the same safe import path ``campaign
import`` uses, and the assembled :class:`~adlife.core.domain.scenario.Scenario` is
validated by the domain's own validators before anyone sees it.

THE ROUTABLE DEFAULT WORLD. Movement requires a named commute route to cover both of
an agent's zones, and the packaged routines commute over ``highway-north`` (an
office-bound agent from ``home-north``) or ``highway-center`` (everyone else). The
default world therefore routes exactly those documented pairs - home-north to the
office, home-center to the retail-center - plus the walk legs the routines make
between the retail-center, the cafe and the two homes. A generated profile is assigned
to a compatible (home, work) pair by a keyed draw, so a generated population always
runs; an explicit population that pairs an unroutable commute is refused by
``validate`` naming the profile, not discovered as a mid-run crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from adlife.cli.errors import CommandError
from adlife.config.loader import load_app_config
from adlife.config.models import AppConfig
from adlife.core.domain.campaign import Campaign
from adlife.core.domain.person import PersonProfile
from adlife.core.domain.scenario import Scenario
from adlife.core.domain.state import ConsumerState
from adlife.core.domain.world import Relationship, Route, World, Zone
from adlife.core.simulation.population import generate_population, generate_relationships

_GITIGNORE_TEXT = """# AdLife Lab study directory
# Local credentials never enter the repository.
.env
# Run artifacts and reports are outputs; the YAML inputs above are the study.
runs/
reports/
# Locally added creative assets stay out; packaged demo assets stay in.
assets/local/
"""


@dataclass(frozen=True)
class Project:
    """One loaded study: its config, its validated scenario, and its root."""

    root: Path
    config: AppConfig
    scenario: Scenario


def load_app_resource(name: str) -> str:
    """Read a packaged demo file by importlib name, never by a path beside __file__."""
    resource = resources.files("adlife.resources.demo").joinpath(name)
    return resource.read_text(encoding="utf-8")


def init_project(destination: Path) -> Path:
    """Create one study directory from the packaged demo, refusing to overwrite."""
    root = destination
    if root.exists() and any(root.iterdir()):
        raise CommandError(
            f"target {root} already exists and is not empty; init never overwrites a study",
        )
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("assets", "campaigns", "runs", "reports"):
        (root / directory).mkdir(exist_ok=True)
    (root / "adlife.yaml").write_text(load_app_resource("adlife.yaml"), encoding="utf-8")
    (root / "population.yaml").write_text(load_app_resource("population.yaml"), encoding="utf-8")
    (root / "campaigns" / "demo-phone.yaml").write_text(
        load_app_resource("campaigns/demo-phone.yaml"), encoding="utf-8"
    )
    (root / "campaigns" / "demo-billboard.yaml").write_text(
        load_app_resource("campaigns/demo-billboard.yaml"), encoding="utf-8"
    )
    (root / ".gitignore").write_text(_GITIGNORE_TEXT, encoding="utf-8")
    return root


def default_world() -> World:
    """The documented ten-zone world the built-in routines and commute routes assume."""
    return World(
        world_id="world-default",
        zones=(
            Zone(zone_id="home-north", name="North Home", kind="home"),
            Zone(zone_id="home-center", name="Center Home", kind="home"),
            Zone(zone_id="office", name="Office", kind="work"),
            Zone(zone_id="retail-center", name="Retail Center", kind="retail"),
            Zone(zone_id="cafe", name="Cafe", kind="social"),
            Zone(zone_id="online", name="Online", kind="online"),
            Zone(zone_id="highway-north", name="North Highway", kind="highway"),
            Zone(zone_id="highway-center", name="Center Highway", kind="highway"),
        ),
        routes=(
            Route(
                route_id="highway-north",
                source_zone="home-north",
                target_zone="office",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
            Route(
                route_id="retail-commute",
                source_zone="office",
                target_zone="retail-center",
                transit_zone="highway-north",
                travel_minutes=30,
            ),
            Route(
                route_id="highway-center",
                source_zone="home-center",
                target_zone="retail-center",
                transit_zone="highway-center",
                travel_minutes=30,
            ),
            Route(
                route_id="high-street",
                source_zone="retail-center",
                target_zone="cafe",
                travel_minutes=15,
            ),
            Route(
                route_id="center-home",
                source_zone="cafe",
                target_zone="home-center",
                travel_minutes=15,
            ),
            Route(
                route_id="north-walk",
                source_zone="cafe",
                target_zone="home-north",
                travel_minutes=15,
            ),
        ),
    )


_ROUTABLE_ASSIGNMENTS: dict[str, tuple[str, str]] = {
    "office-worker": ("home-north", "office"),
    "retail-worker": ("home-center", "retail-center"),
    "student": ("home-north", "office"),
    "freelancer": ("home-center", "retail-center"),
    "unemployed": ("home-north", "office"),
}


def _assign_routable_zones(
    profiles: tuple[PersonProfile, ...], seed: int
) -> tuple[PersonProfile, ...]:
    """Assign generated profiles to (home, work) pairs the default world routes.

    The generator's weighted tables name every documented occupation and home zone; the
    default world routes two commute pairs. The assignment is a keyed draw per agent -
    deterministic in the run seed, stable in the profile's other fields - so a
    generated population is always runnable without changing what the generator
    invented about the person.

    One documented adaptation beyond the zone pair: the packaged unemployed routine
    commutes FROM the cafe to the highway transit, a leg no world can route because a
    named route cannot also cover the cafe. An unemployed generated profile therefore
    follows the office-worker routine - the profile's occupation, traits and identity
    are untouched, and the routine's activities are the same documented set.
    """
    from adlife.core.simulation.rng import RandomOracle

    oracle = RandomOracle(seed)
    assigned: list[PersonProfile] = []
    for profile in profiles:
        home_zone, work_zone = _ROUTABLE_ASSIGNMENTS[profile.occupation]
        draw = oracle.uniform("project:home-assignment", profile.agent_id, 0, 0)
        if draw >= 0.5:
            home_zone, work_zone = _ROUTABLE_ASSIGNMENTS[
                "retail-worker" if profile.occupation != "retail-worker" else "office-worker"
            ]
        routine_template = (
            "office-worker" if profile.occupation == "unemployed" else profile.routine_template
        )
        assigned.append(
            profile.model_copy(
                update={
                    "home_zone": home_zone,
                    "work_or_study_zone": work_zone,
                    "routine_template": routine_template,
                }
            )
        )
    return tuple(assigned)


def _initial_state_for(profile: PersonProfile) -> ConsumerState:
    return ConsumerState(
        agent_id=profile.agent_id,
        location=profile.home_zone,
        activity="sleep",
        mood=0.0,
        fatigue=0.0,
        brand_sentiment=profile.initial_brand_sentiment,
        recall_strength=0.0,
        purchase_intention=0.0,
        cognition_budget_remaining=6,
    )


def _load_population_document(
    path: Path,
) -> tuple[tuple[PersonProfile, ...], tuple[Relationship, ...]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CommandError(f"population.yaml could not be parsed: {exc}") from None
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CommandError("population.yaml must define schema_version: 1")

    profiles: list[PersonProfile] = []
    for raw in document.get("profiles", ()):
        if not isinstance(raw, dict):
            raise CommandError("population.yaml profiles must be mappings")
        data = dict(raw)
        if isinstance(data.get("interests"), list):
            data["interests"] = frozenset(data["interests"])
        try:
            profiles.append(PersonProfile.model_validate(data))
        except ValidationError as exc:
            raise CommandError(f"population.yaml: invalid profile: {exc}") from None

    relationships: list[Relationship] = []
    for raw in document.get("relationships", ()):
        if not isinstance(raw, dict):
            raise CommandError("population.yaml relationships must be mappings")
        try:
            relationships.append(Relationship.model_validate(raw))
        except ValidationError as exc:
            raise CommandError(f"population.yaml: invalid relationship: {exc}") from None
    return tuple(profiles), tuple(relationships)


def _load_campaign_file(path: Path) -> Campaign:
    from adlife.cli.commands.campaign import _adapt_yaml_sequences

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CommandError(f"{path.name} could not be parsed: {exc}") from None
    if not isinstance(document, dict):
        raise CommandError(f"{path.name} must contain a campaign mapping")
    try:
        return Campaign.model_validate(_adapt_yaml_sequences(document))
    except ValidationError as exc:
        raise CommandError(f"{path.name}: invalid campaign: {exc}") from None


def load_project(
    root: Path,
    *,
    days: int | None = None,
    population_size: int | None = None,
    seed: int | None = None,
) -> Project:
    """Load and validate one study directory into a run-ready scenario.

    ``days``, ``population_size`` and ``seed`` are the ``run`` command's documented
    overrides: they re-enter the same validation the project was written with, so an
    override can never smuggle in an invalid combination.
    """
    root = root.resolve()
    if not root.is_dir():
        raise CommandError(f"project directory {root} does not exist")
    config_path = root / "adlife.yaml"
    if not config_path.is_file():
        raise CommandError(f"{root} is not an AdLife study: adlife.yaml is missing")

    try:
        config = load_app_config(config_path)
    except ValueError as exc:
        raise CommandError(str(exc)) from None

    resolved_days = days if days is not None else config.simulation.days
    resolved_seed = seed if seed is not None else config.simulation.seed
    resolved_size = (
        population_size if population_size is not None else config.simulation.population_size
    )

    population_path = root / "population.yaml"
    if population_path.is_file() and population_size is None:
        profiles, relationships = _load_population_document(population_path)
    else:
        locale = "fa-IR"
        generated = generate_population(resolved_size, resolved_seed, locale)
        profiles = _assign_routable_zones(generated, resolved_seed)
        relationships = generate_relationships(profiles, resolved_seed)

    campaigns: list[Campaign] = []
    campaigns_dir = root / "campaigns"
    if campaigns_dir.is_dir():
        for campaign_path in sorted(campaigns_dir.glob("*.yaml")):
            campaigns.append(_load_campaign_file(campaign_path))

    scenario_id = f"project-{sha256(str(root).encode('utf-8')).hexdigest()[:12]}"
    try:
        scenario = Scenario(
            scenario_id=scenario_id,
            name=root.name,
            days=resolved_days,
            tick_minutes=config.simulation.tick_minutes,
            world=default_world(),
            population=profiles,
            initial_states=tuple(_initial_state_for(profile) for profile in profiles),
            relationships=relationships,
            campaigns=tuple(campaigns),
        )
    except ValidationError as exc:
        raise CommandError(f"the assembled scenario is invalid: {exc}") from None
    _validate_routability(scenario)
    return Project(root=root, config=config, scenario=scenario)


def _validate_routability(scenario: Scenario) -> None:
    """Refuse a population whose commute the default world cannot route.

    Every routine template commutes between a profile's home zone and its work zone
    over a named route, and movement refuses an intent the named route does not cover.
    Checking that HERE means an unroutable study is a validation error naming the
    profile and the pair, never a mid-run crash on the first morning commute.
    """
    routes = {route.route_id: route for route in scenario.world.routes}

    def covers(pair: tuple[str, str], route_ids: tuple[str, ...]) -> bool:
        for route_id in route_ids:
            route = routes.get(route_id)
            if route is None:
                continue
            zones = {route.source_zone, route.target_zone, route.transit_zone}
            if set(pair) <= zones:
                return True
        return False

    for profile in scenario.population:
        work_zone = profile.work_or_study_zone
        if work_zone is None:
            continue
        commute_routes = (
            ("highway-north",) if profile.home_zone == "home-north" else ("highway-center",)
        )
        if not covers((profile.home_zone, work_zone), commute_routes):
            raise CommandError(
                f"{profile.agent_id} ({profile.occupation}): the default world has no "
                f"commute route from {profile.home_zone} to {work_zone}; the packaged "
                "routines commute over highway-north (home-north to office) and "
                "highway-center (home-center to retail-center). Adjust the population "
                "or provide a population.yaml with routable home and work zones."
            )


__all__ = ["Project", "default_world", "init_project", "load_app_resource", "load_project"]
