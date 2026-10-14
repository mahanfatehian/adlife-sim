"""Adapter-free port for optional city narrative providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from adlife.core.domain.city_cognition import (
    CityCognitionRequestV1,
    CityProviderAttemptV1,
    CityProviderRepairContextV1,
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


__all__ = ["CityCognitionProvider"]
