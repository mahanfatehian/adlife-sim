from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from math import ceil
from types import MappingProxyType
from typing import Literal, TypeVar, cast

import yaml

from adlife.core.domain.person import ConsumerTraits, PersonProfile
from adlife.core.domain.world import Relationship
from adlife.core.simulation.rng import RandomOracle

Occupation = Literal["student", "office-worker", "retail-worker", "freelancer", "unemployed"]
IncomeBand = Literal["low", "middle", "high"]
RelationshipKind = Literal["friend", "colleague", "family", "online"]

_Choice = TypeVar("_Choice")

_SUPPORTED_LOCALES = frozenset({"fa-IR", "en-US"})

# These distributions are invented modeling constants, not demographic estimates.
_OCCUPATION_WEIGHTS: tuple[tuple[Occupation, float], ...] = (
    ("student", 0.20),
    ("office-worker", 0.28),
    ("retail-worker", 0.18),
    ("freelancer", 0.20),
    ("unemployed", 0.14),
)
_INCOME_WEIGHTS: dict[Occupation, tuple[tuple[IncomeBand, float], ...]] = {
    "student": (("low", 0.65), ("middle", 0.30), ("high", 0.05)),
    "office-worker": (("low", 0.20), ("middle", 0.60), ("high", 0.20)),
    "retail-worker": (("low", 0.55), ("middle", 0.40), ("high", 0.05)),
    "freelancer": (("low", 0.35), ("middle", 0.45), ("high", 0.20)),
    "unemployed": (("low", 0.75), ("middle", 0.23), ("high", 0.02)),
}
_HOUSEHOLD_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("solo-home", 0.35),
    ("shared-home", 0.35),
    ("family-home", 0.30),
)
_HOME_ZONE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("home-north", 1.0),
    ("home-center", 1.0),
    ("home-south", 1.0),
)
_WORK_ZONES: dict[Occupation, str | None] = {
    "student": "university",
    "office-worker": "office",
    "retail-worker": "retail-center",
    "freelancer": "cafe",
    "unemployed": None,
}
_INTERESTS = (
    "cinema",
    "cooking",
    "fitness",
    "gaming",
    "music",
    "reading",
    "sports",
    "technology",
    "travel",
    "visual-design",
)


def _name_resource_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a mapping with string keys")
    return cast(Mapping[str, object], value)


@lru_cache(maxsize=1)
def _load_fictional_first_names() -> Mapping[str, tuple[str, ...]]:
    resource = files("adlife.resources.names").joinpath("fictional.yaml")
    root = _name_resource_mapping(
        yaml.safe_load(resource.read_text(encoding="utf-8")),
        "fictional name resource",
    )
    if set(root) != {"schema_version", "locales"}:
        raise ValueError("fictional name resource must define schema_version and locales")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ValueError("fictional name resource schema_version must be 1")

    locale_values = _name_resource_mapping(root["locales"], "fictional name locales")
    if set(locale_values) != _SUPPORTED_LOCALES:
        raise ValueError("fictional name resource must define fa-IR and en-US")

    names_by_locale: dict[str, tuple[str, ...]] = {}
    for locale in sorted(locale_values):
        value = locale_values[locale]
        if not isinstance(value, list) or not value:
            raise ValueError(f"fictional names for {locale} must be a non-empty list")
        if not all(isinstance(name, str) for name in value):
            raise ValueError(f"fictional names for {locale} must be strings")
        names = tuple(cast(str, name) for name in value)
        if any(not name.isascii() or not name.isalpha() for name in names):
            raise ValueError(f"fictional names for {locale} must be single ASCII words")
        if len(names) != len(set(names)):
            raise ValueError(f"fictional names for {locale} must be unique")
        names_by_locale[locale] = names
    return MappingProxyType(names_by_locale)


def _weighted_choice(
    choices: tuple[tuple[_Choice, float], ...],
    draw: float,
) -> _Choice:
    threshold = draw * sum(weight for _, weight in choices)
    cumulative = 0.0
    for value, weight in choices:
        cumulative += weight
        if threshold < cumulative:
            return value
    return choices[-1][0]


def _validate_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
        raise ValueError("seed must be an integer between 0 and 2**63 - 1")


def _profile_interests(oracle: RandomOracle, agent_id: str) -> frozenset[str]:
    ranked = sorted(
        _INTERESTS,
        key=lambda interest: (
            -oracle.uniform(f"profile:interest:{interest}", agent_id, 0, 0),
            interest,
        ),
    )
    return frozenset(ranked[:3])


def generate_population(size: int, seed: int, locale: str) -> tuple[PersonProfile, ...]:
    """Generate a stable population from packaged, invented first names."""
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 30:
        raise ValueError("size must be between 1 and 30")
    _validate_seed(seed)
    names_by_locale = _load_fictional_first_names()
    if locale not in names_by_locale:
        supported = ", ".join(sorted(names_by_locale))
        raise ValueError(f"unsupported locale {locale!r}; expected one of: {supported}")

    oracle = RandomOracle(seed)
    names = names_by_locale[locale]
    profiles: list[PersonProfile] = []
    for person_number in range(1, size + 1):
        agent_id = f"person-{person_number:03d}"
        name_draw = oracle.uniform(f"profile:first-name:{locale}", agent_id, 0, 0)
        first_name = names[min(int(name_draw * len(names)), len(names) - 1)]
        occupation = _weighted_choice(
            _OCCUPATION_WEIGHTS,
            oracle.uniform("profile:occupation", agent_id, 0, 0),
        )
        income_band = _weighted_choice(
            _INCOME_WEIGHTS[occupation],
            oracle.uniform("profile:income-band", agent_id, 0, 0),
        )
        traits = ConsumerTraits.model_validate(
            {
                trait_name: round(
                    oracle.uniform(f"trait:{trait_name}", agent_id, 0, 0),
                    4,
                )
                for trait_name in ConsumerTraits.model_fields
            }
        )
        profiles.append(
            PersonProfile(
                agent_id=agent_id,
                display_name=f"{first_name} {person_number:03d}",
                fictional=True,
                age=18 + int(oracle.uniform("profile:age", agent_id, 0, 0) * 48),
                occupation=occupation,
                income_band=income_band,
                household_type=_weighted_choice(
                    _HOUSEHOLD_WEIGHTS,
                    oracle.uniform("profile:household-type", agent_id, 0, 0),
                ),
                home_zone=_weighted_choice(
                    _HOME_ZONE_WEIGHTS,
                    oracle.uniform("profile:home-zone", agent_id, 0, 0),
                ),
                work_or_study_zone=_WORK_ZONES[occupation],
                interests=_profile_interests(oracle, agent_id),
                traits=traits,
                initial_brand_sentiment=round(
                    oracle.uniform("profile:initial-brand-sentiment", agent_id, 0, 0) * 0.4 - 0.2,
                    4,
                ),
                routine_template=occupation,
            )
        )
    return tuple(profiles)


def _canonical_pair(left_id: str, right_id: str) -> tuple[str, str]:
    return (left_id, right_id) if left_id < right_id else (right_id, left_id)


def _homophily(left: PersonProfile, right: PersonProfile) -> float:
    interest_union = left.interests | right.interests
    interest_similarity = len(left.interests & right.interests) / len(interest_union)
    return (
        0.55 * interest_similarity
        + 0.20 * (left.occupation == right.occupation)
        + 0.15 * (left.income_band == right.income_band)
        + 0.10 * (left.home_zone == right.home_zone)
    )


def _relationship_kind(
    left: PersonProfile,
    right: PersonProfile,
    oracle: RandomOracle,
    pair_key: str,
) -> RelationshipKind:
    colleague_weight = 0.35 if left.work_or_study_zone == right.work_or_study_zone else 0.10
    family_weight = 0.25 if left.household_type == right.household_type else 0.10
    weights: tuple[tuple[RelationshipKind, float], ...] = (
        ("friend", 0.40),
        ("colleague", colleague_weight),
        ("family", family_weight),
        ("online", 0.20),
    )
    return _weighted_choice(
        weights,
        oracle.uniform("relationship:kind", pair_key, 0, 0),
    )


def generate_relationships(
    profiles: Sequence[PersonProfile],
    seed: int,
) -> tuple[Relationship, ...]:
    """Generate an undirected deterministic graph with canonical edge records."""
    _validate_seed(seed)
    ordered = tuple(sorted(profiles, key=lambda profile: profile.agent_id))
    if len(ordered) > 30:
        raise ValueError("relationship population cannot exceed 30")
    agent_ids = tuple(profile.agent_id for profile in ordered)
    if len(agent_ids) != len(set(agent_ids)):
        raise ValueError("relationship population contains duplicate agent_id")
    if len(ordered) < 2:
        return ()

    by_id = {profile.agent_id: profile for profile in ordered}
    oracle = RandomOracle(seed)
    pairs: set[tuple[str, str]] = set()
    if len(ordered) == 2:
        pairs.add(_canonical_pair(agent_ids[0], agent_ids[1]))
    else:
        pairs.update(
            _canonical_pair(agent_ids[index], agent_ids[(index + 1) % len(agent_ids)])
            for index in range(len(agent_ids))
        )

        degree = dict.fromkeys(agent_ids, 0)
        for source_id, target_id in pairs:
            degree[source_id] += 1
            degree[target_id] += 1

        target_mean_degree = min(4, len(ordered) - 1)
        target_edge_count = ceil(len(ordered) * target_mean_degree / 2)
        candidates: list[tuple[float, str, str]] = []
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1 :]:
                source_id, target_id = _canonical_pair(left.agent_id, right.agent_id)
                if (source_id, target_id) in pairs:
                    continue
                pair_key = f"{source_id}|{target_id}"
                priority = 0.75 * _homophily(left, right) + 0.25 * oracle.uniform(
                    "relationship:edge-priority",
                    pair_key,
                    0,
                    0,
                )
                candidates.append((priority, source_id, target_id))

        for _, source_id, target_id in sorted(
            candidates,
            key=lambda candidate: (-candidate[0], candidate[1], candidate[2]),
        ):
            if len(pairs) >= target_edge_count:
                break
            if degree[source_id] >= 8 or degree[target_id] >= 8:
                continue
            pairs.add((source_id, target_id))
            degree[source_id] += 1
            degree[target_id] += 1

    relationships: list[Relationship] = []
    for source_id, target_id in sorted(pairs):
        left = by_id[source_id]
        right = by_id[target_id]
        pair_key = f"{source_id}|{target_id}"
        strength_basis = 0.5 * _homophily(left, right) + 0.5 * oracle.uniform(
            "relationship:strength",
            pair_key,
            0,
            0,
        )
        relationships.append(
            Relationship(
                source_id=source_id,
                target_id=target_id,
                kind=_relationship_kind(left, right, oracle, pair_key),
                strength=round(0.2 + 0.8 * strength_basis, 4),
            )
        )
    return tuple(relationships)


__all__ = ["generate_population", "generate_relationships"]
