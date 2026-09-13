import pytest
from pydantic import ValidationError

from adlife.core.domain.campaign import (
    BillboardPlacement,
    Campaign,
    CreativeFeatures,
    PhonePlacement,
    Price,
    TimeWindow,
)


def campaign_data(campaign: Campaign) -> dict[str, object]:
    return campaign.model_dump(mode="python")


def test_campaign_round_trip_preserves_discriminated_phone_placement(
    valid_campaign: Campaign,
) -> None:
    restored = Campaign.model_validate_json(valid_campaign.model_dump_json())

    assert restored == valid_campaign
    assert isinstance(restored.placements[0], PhonePlacement)
    assert restored.schema_version == 1


def test_campaign_parses_billboard_placement_by_channel(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["placements"] = (
        {
            "schema_version": 1,
            "channel": "highway-billboard",
            "route_id": "highway-north",
            "active_windows": (
                {
                    "start_minute_of_day": 420,
                    "end_minute_of_day": 600,
                },
            ),
            "frequency_cap": 4,
            "visibility": 0.9,
        },
    )

    campaign = Campaign.model_validate(data)

    assert isinstance(campaign.placements[0], BillboardPlacement)
    assert campaign.placements[0].route_id == "highway-north"


def test_campaign_requires_at_least_one_placement(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["placements"] = ()

    with pytest.raises(ValidationError):
        Campaign.model_validate(data)


def test_campaign_rejects_duplicate_placements(valid_campaign: Campaign) -> None:
    placement = valid_campaign.placements[0]
    data = campaign_data(valid_campaign)
    data["placements"] = (placement, placement)

    with pytest.raises(ValidationError, match="placements must be unique"):
        Campaign.model_validate(data)


def test_campaign_requires_positive_price() -> None:
    with pytest.raises(ValidationError):
        Price(amount=0.0, currency="USD")


def test_campaign_requires_positive_category_reference_price(
    valid_campaign: Campaign,
) -> None:
    data = campaign_data(valid_campaign)
    data["category_reference_price"] = 0.0

    with pytest.raises(ValidationError):
        Campaign.model_validate(data)


@pytest.mark.parametrize("currency", ["usd", "US", "USDX", "123"])
def test_price_requires_uppercase_three_letter_currency(currency: str) -> None:
    with pytest.raises(ValidationError):
        Price(amount=10.0, currency=currency)


def test_asset_path_requires_sha256(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["asset_path"] = "assets/phone.png"
    data["asset_sha256"] = None

    with pytest.raises(ValidationError, match="asset_sha256 is required"):
        Campaign.model_validate(data)


def test_asset_sha256_has_digest_shape(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["asset_path"] = "assets/phone.png"
    data["asset_sha256"] = "not-a-digest"

    with pytest.raises(ValidationError):
        Campaign.model_validate(data)


def test_campaign_period_must_move_forward(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["start_minute"] = 300
    data["end_minute"] = 300

    with pytest.raises(ValidationError, match="end_minute must be after start_minute"):
        Campaign.model_validate(data)


def test_time_window_must_move_forward() -> None:
    with pytest.raises(ValidationError, match="end_minute_of_day must be after"):
        TimeWindow(start_minute_of_day=600, end_minute_of_day=600)


def test_placement_requires_an_active_window() -> None:
    with pytest.raises(ValidationError):
        PhonePlacement(
            channel="mobile-feed",
            active_windows=(),
            frequency_cap=3,
            visibility=0.8,
        )


@pytest.mark.parametrize(
    ("frequency_cap", "visibility"),
    [(0, 0.5), (8, 0.5), (3, -0.01), (3, 1.01)],
)
def test_phone_placement_rejects_values_outside_channel_bounds(
    frequency_cap: int,
    visibility: float,
) -> None:
    with pytest.raises(ValidationError):
        PhonePlacement(
            channel="mobile-feed",
            active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=60),),
            frequency_cap=frequency_cap,
            visibility=visibility,
        )


@pytest.mark.parametrize(
    ("frequency_cap", "visibility"),
    [(0, 0.5), (15, 0.5), (3, -0.01), (3, 1.01)],
)
def test_billboard_placement_rejects_values_outside_channel_bounds(
    frequency_cap: int,
    visibility: float,
) -> None:
    with pytest.raises(ValidationError):
        BillboardPlacement(
            channel="highway-billboard",
            route_id="highway-north",
            active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=60),),
            frequency_cap=frequency_cap,
            visibility=visibility,
        )


def test_phone_placement_is_limited_to_online_zone() -> None:
    with pytest.raises(ValidationError):
        PhonePlacement(
            channel="mobile-feed",
            zone="office",
            active_windows=(TimeWindow(start_minute_of_day=0, end_minute_of_day=60),),
            frequency_cap=3,
            visibility=0.8,
        )


def test_campaign_is_frozen(valid_campaign: Campaign) -> None:
    with pytest.raises(ValidationError, match="Instance is frozen"):
        valid_campaign.name = "Changed"


def test_creative_features_are_frozen(valid_campaign: Campaign) -> None:
    with pytest.raises(ValidationError, match="Instance is frozen"):
        valid_campaign.creative_features.description = "Changed"


def test_campaign_forbids_unknown_fields(valid_campaign: Campaign) -> None:
    data = campaign_data(valid_campaign)
    data["tracking_pixel"] = "https://example.invalid/pixel"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Campaign.model_validate(data)


def test_creative_features_have_bounded_collections() -> None:
    with pytest.raises(ValidationError):
        CreativeFeatures(
            description="A fictional creative.",
            dominant_colors=tuple(f"color-{index}" for index in range(9)),
        )
