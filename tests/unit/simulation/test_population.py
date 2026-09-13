from importlib.resources import files

import pytest
import yaml

from adlife.core.domain.person import PersonProfile
from adlife.core.domain.world import Relationship
from adlife.core.simulation.population import generate_population, generate_relationships

_NAME_RESOURCE_PACKAGE = "adlife.resources.names"
_NAME_RESOURCE_FILENAME = "fictional.yaml"


def _degrees(
    profiles: tuple[PersonProfile, ...],
    edges: tuple[Relationship, ...],
) -> dict[str, int]:
    degree = {profile.agent_id: 0 for profile in profiles}
    for edge in edges:
        degree[edge.source_id] += 1
        degree[edge.target_id] += 1
    return degree


def test_population_generation_is_repeatable() -> None:
    assert generate_population(20, 42, "fa-IR") == generate_population(20, 42, "fa-IR")


def test_fictional_first_names_load_from_package_yaml() -> None:
    resource = files(_NAME_RESOURCE_PACKAGE).joinpath(_NAME_RESOURCE_FILENAME)

    assert resource.is_file()
    payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload["locales"]) == {"fa-IR", "en-US"}
    for names in payload["locales"].values():
        assert len(names) == 10
        assert len(names) == len(set(names))
        assert all(name.isascii() and name.isalpha() for name in names)


@pytest.mark.parametrize(
    ("locale", "expected_names"),
    [
        ("fa-IR", ("Arnika 001", "Parvian 002", "Navira 003")),
        ("en-US", ("Ivara 001", "Corin 002", "Aven 003")),
    ],
)
def test_generated_identity_uses_stable_packaged_fictional_names(
    locale: str,
    expected_names: tuple[str, ...],
) -> None:
    resource = files(_NAME_RESOURCE_PACKAGE).joinpath(_NAME_RESOURCE_FILENAME)
    packaged_names = set(yaml.safe_load(resource.read_text(encoding="utf-8"))["locales"][locale])

    profiles = generate_population(3, 42, locale)

    assert tuple(profile.display_name for profile in profiles) == expected_names
    assert all(profile.display_name.split()[0] in packaged_names for profile in profiles)
    assert all(profile.fictional is True for profile in profiles)


def test_generated_ids_are_stable() -> None:
    profiles = generate_population(3, 42, "fa-IR")

    assert [profile.agent_id for profile in profiles] == [
        "person-001",
        "person-002",
        "person-003",
    ]


def test_generated_identity_is_explicitly_fictional_and_numbered() -> None:
    profiles = generate_population(10, 42, "en-US")

    assert all(profile.fictional is True for profile in profiles)
    for index, profile in enumerate(profiles, start=1):
        first_name, person_number = profile.display_name.split()
        assert first_name.isalpha()
        assert person_number == f"{index:03d}"


def test_locales_select_different_fixed_fictional_name_sets() -> None:
    fa_profile = generate_population(1, 42, "fa-IR")[0]
    en_profile = generate_population(1, 42, "en-US")[0]

    assert fa_profile.display_name != en_profile.display_name


def test_trait_values_use_independent_stable_oracle_keys() -> None:
    profile = generate_population(1, 42, "fa-IR")[0]

    assert profile.traits.model_dump(mode="python") == {
        "price_sensitivity": 0.5715,
        "novelty_seeking": 0.985,
        "social_susceptibility": 0.8048,
        "advertising_skepticism": 0.8589,
        "mobile_attention": 0.0558,
        "outdoor_attention": 0.0776,
        "brand_loyalty": 0.7435,
        "impulsivity": 0.6212,
    }


@pytest.mark.parametrize("size", [0, 31])
def test_population_size_outside_supported_range_is_rejected(size: int) -> None:
    with pytest.raises(ValueError, match="size must be between 1 and 30"):
        generate_population(size, 42, "fa-IR")


def test_unknown_locale_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported locale"):
        generate_population(1, 42, "unknown")


def test_relationship_generation_is_repeatable_and_order_independent() -> None:
    profiles = generate_population(20, 42, "fa-IR")

    assert generate_relationships(profiles, 42) == generate_relationships(
        tuple(reversed(profiles)), 42
    )


def test_single_person_has_no_relationships() -> None:
    profiles = generate_population(1, 42, "fa-IR")

    assert generate_relationships(profiles, 42) == ()


def test_two_people_have_one_canonical_relationship() -> None:
    profiles = generate_population(2, 42, "fa-IR")

    edges = generate_relationships(profiles, 42)

    assert len(edges) == 1
    assert (edges[0].source_id, edges[0].target_id) == ("person-001", "person-002")


def test_relationship_graph_has_no_isolates_or_excess_degree() -> None:
    profiles = generate_population(20, 42, "fa-IR")
    edges = generate_relationships(profiles, 42)

    degree = _degrees(profiles, edges)

    assert min(degree.values()) >= 1
    assert max(degree.values()) <= 8


def test_relationship_edges_are_canonical_unique_and_bounded() -> None:
    profiles = generate_population(20, 42, "fa-IR")

    edges = generate_relationships(profiles, 42)
    pairs = tuple((edge.source_id, edge.target_id) for edge in edges)

    assert all(source_id < target_id for source_id, target_id in pairs)
    assert len(pairs) == len(set(pairs))
    assert all(edge.kind in {"friend", "colleague", "family", "online"} for edge in edges)
    assert all(0.2 <= edge.strength <= 1.0 for edge in edges)


def test_relationship_mean_degree_is_between_three_and_five() -> None:
    profiles = generate_population(20, 42, "fa-IR")

    edges = generate_relationships(profiles, 42)
    mean_degree = 2 * len(edges) / len(profiles)

    assert 3 <= mean_degree <= 5
