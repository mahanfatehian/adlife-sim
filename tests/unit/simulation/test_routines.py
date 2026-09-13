from importlib.resources import files
from itertools import pairwise

import pytest
from pydantic import ValidationError

from adlife.core.domain.person import PersonProfile
from adlife.core.domain.world import RoutineBlock
from adlife.core.simulation.rng import RandomOracle
from adlife.core.simulation.routines import RoutineTemplate, load_routine_templates, routine_at

_TEMPLATE_IDS = {
    "student",
    "office-worker",
    "retail-worker",
    "freelancer",
    "unemployed",
}
_EVERYDAY_ACTIVITIES = {
    "sleep",
    "breakfast",
    "commute",
    "phone-check",
    "socializing",
    "reflection",
}


def _block(
    template_id: str,
    day_type: str,
    start: int,
    end: int,
) -> RoutineBlock:
    return RoutineBlock(
        template_id=template_id,
        day_type=day_type,
        start_minute_of_day=start,
        end_minute_of_day=end,
        activity="sleep",
        zone_id="home",
    )


def _full_day(template_id: str, day_type: str) -> tuple[RoutineBlock, ...]:
    return (_block(template_id, day_type, 0, 1440),)


def _resolved_schedule(
    profile: PersonProfile,
    day_index: int,
    oracle: RandomOracle,
) -> tuple[RoutineBlock, ...]:
    blocks: list[RoutineBlock] = []
    for minute_of_day in range(1440):
        block = routine_at(profile, day_index * 1440 + minute_of_day, oracle)
        if not blocks or block != blocks[-1]:
            blocks.append(block)
    return tuple(blocks)


def test_routine_resource_loads_from_package() -> None:
    resource = files("adlife.resources.routines").joinpath("default.yaml")

    assert resource.is_file()


def test_resource_defines_all_five_templates_for_both_day_types() -> None:
    templates = load_routine_templates()

    assert set(templates) == _TEMPLATE_IDS
    assert all(template.weekday for template in templates.values())
    assert all(template.weekend for template in templates.values())


def test_resource_schedules_cover_each_day_without_gaps_or_overlaps() -> None:
    for template in load_routine_templates().values():
        for blocks in (template.weekday, template.weekend):
            assert blocks[0].start_minute_of_day == 0
            assert blocks[-1].end_minute_of_day == 1440
            assert all(
                previous.end_minute_of_day == following.start_minute_of_day
                for previous, following in pairwise(blocks)
            )


def test_resource_schedules_include_required_daily_activities() -> None:
    for template in load_routine_templates().values():
        for blocks in (template.weekday, template.weekend):
            activities = {block.activity for block in blocks}
            assert activities >= _EVERYDAY_ACTIVITIES
            assert activities & {"shopping", "leisure"}
            assert activities & {"work", "study"}


@pytest.mark.parametrize("day_type", ["weekday", "weekend"])
def test_unemployed_schedule_includes_meaningful_self_study(day_type: str) -> None:
    template = load_routine_templates()["unemployed"]
    blocks = template.weekday if day_type == "weekday" else template.weekend

    study_blocks = tuple(block for block in blocks if block.activity == "study")

    assert sum(block.end_minute_of_day - block.start_minute_of_day for block in study_blocks) >= 60
    assert all(block.zone_id == "home" for block in study_blocks)


def test_routine_template_rejects_a_gap() -> None:
    with pytest.raises(ValidationError, match="gap"):
        RoutineTemplate(
            template_id="test-template",
            weekday=(
                _block("test-template", "weekday", 0, 600),
                _block("test-template", "weekday", 601, 1440),
            ),
            weekend=_full_day("test-template", "weekend"),
        )


def test_routine_template_rejects_an_overlap() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        RoutineTemplate(
            template_id="test-template",
            weekday=(
                _block("test-template", "weekday", 0, 601),
                _block("test-template", "weekday", 600, 1440),
            ),
            weekend=_full_day("test-template", "weekend"),
        )


def test_routine_at_is_repeatable_and_preserves_complete_coverage(
    valid_profile: PersonProfile,
) -> None:
    oracle = RandomOracle(42)

    first = _resolved_schedule(valid_profile, 0, oracle)
    second = _resolved_schedule(valid_profile, 0, oracle)

    assert first == second
    assert first[0].start_minute_of_day == 0
    assert first[-1].end_minute_of_day == 1440
    assert all(
        previous.end_minute_of_day == following.start_minute_of_day
        for previous, following in pairwise(first)
    )


def test_commute_and_leisure_jitter_stays_within_thirty_minutes(
    valid_profile: PersonProfile,
) -> None:
    template = load_routine_templates()[valid_profile.routine_template]

    actual = _resolved_schedule(valid_profile, 0, RandomOracle(42))

    assert tuple(block.activity for block in actual) == tuple(
        block.activity for block in template.weekday
    )
    assert all(
        abs(jittered.start_minute_of_day - base.start_minute_of_day) <= 30
        and abs(jittered.end_minute_of_day - base.end_minute_of_day) <= 30
        for jittered, base in zip(actual, template.weekday, strict=True)
    )


def test_jitter_changes_at_least_one_target_boundary_across_fixed_seeds(
    valid_profile: PersonProfile,
) -> None:
    template = load_routine_templates()[valid_profile.routine_template]
    base_boundaries = tuple(
        (block.start_minute_of_day, block.end_minute_of_day) for block in template.weekday
    )

    generated_boundaries = {
        tuple(
            (block.start_minute_of_day, block.end_minute_of_day)
            for block in _resolved_schedule(valid_profile, 0, RandomOracle(seed))
        )
        for seed in range(4)
    }

    assert any(boundaries != base_boundaries for boundaries in generated_boundaries)


def test_jitter_preserves_fifteen_minute_tick_alignment(
    valid_profile: PersonProfile,
) -> None:
    blocks = _resolved_schedule(valid_profile, 0, RandomOracle(3))

    assert all(
        block.start_minute_of_day % 15 == 0 and block.end_minute_of_day % 15 == 0
        for block in blocks
    )
    assert all(block.end_minute_of_day - block.start_minute_of_day >= 15 for block in blocks)


def test_routine_at_selects_weekend_and_resolves_profile_zones(
    valid_profile: PersonProfile,
) -> None:
    blocks = _resolved_schedule(valid_profile, 5, RandomOracle(42))

    assert all(block.day_type == "weekend" for block in blocks)
    assert all(block.zone_id not in {"home", "work-or-study", "commute"} for block in blocks)
    assert all(
        block.zone_id == valid_profile.home_zone for block in blocks if block.activity == "sleep"
    )
    assert all(
        block.zone_id == valid_profile.work_or_study_zone
        for block in blocks
        if block.activity == "work"
    )
    assert all(block.route_id is not None for block in blocks if block.activity == "commute")


def test_routine_at_rejects_negative_simulated_minute(valid_profile: PersonProfile) -> None:
    with pytest.raises(ValueError, match="simulated_minute must be nonnegative"):
        routine_at(valid_profile, -1, RandomOracle(42))


def test_routine_at_rejects_unknown_template(valid_profile: PersonProfile) -> None:
    profile = valid_profile.model_copy(update={"routine_template": "unknown-template"})

    with pytest.raises(ValueError, match="unknown routine template"):
        routine_at(profile, 0, RandomOracle(42))
