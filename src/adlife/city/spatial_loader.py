"""Read bounded local spatial campaign scenarios without reflecting their contents."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from adlife.core.domain.spatial_campaign import (
    SpatialCampaignScenario,
    parse_spatial_campaign_scenario_json,
)

MAX_SPATIAL_CAMPAIGN_BYTES = 2_097_152


class SpatialCampaignError(ValueError):
    """A local spatial campaign cannot be read or fails its versioned contract."""


def load_spatial_campaign_scenario(path: Path) -> SpatialCampaignScenario:
    """Read one local UTF-8 JSON scenario under a hard materialization bound."""
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SPATIAL_CAMPAIGN_BYTES + 1)
    except OSError:
        raise SpatialCampaignError("spatial campaign scenario could not be read") from None
    if len(data) > MAX_SPATIAL_CAMPAIGN_BYTES:
        raise SpatialCampaignError("spatial campaign scenario is too large")
    try:
        document = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SpatialCampaignError("spatial campaign scenario must be UTF-8") from None
    try:
        return parse_spatial_campaign_scenario_json(document)
    except (ValidationError, ValueError):
        raise SpatialCampaignError(
            "spatial campaign scenario failed schema or version validation"
        ) from None


__all__ = [
    "MAX_SPATIAL_CAMPAIGN_BYTES",
    "SpatialCampaignError",
    "load_spatial_campaign_scenario",
]
