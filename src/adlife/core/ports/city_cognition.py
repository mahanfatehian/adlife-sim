"""Adapter-free port for optional city narrative providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from adlife.core.domain.city_cognition import (
    CityCognitionRequestV1,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
)
from adlife.core.domain.city_cognition_v2 import (
    CityCognitionRequestV2,
    CityProviderAttemptV2,
    CityProviderRepairContextV2,
)


@runtime_checkable
class CityCognitionProvider(Protocol):
    """A provider can return prose attempts but receives no simulation authority."""

    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1: ...

    async def repair_call(
        self,
        request: CityCognitionRequestV1,
        *,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1: ...


@runtime_checkable
class CityCognitionProviderV2(Protocol):
    """Closed signal attempts; consumers must exact-revalidate V2 output at runtime."""

    async def call(self, request: CityCognitionRequestV2) -> CityProviderAttemptV2: ...

    async def repair_call(
        self,
        request: CityCognitionRequestV2,
        *,
        context: CityProviderRepairContextV2,
    ) -> CityProviderAttemptV2: ...


__all__ = ["CityCognitionProvider", "CityCognitionProviderV2"]
