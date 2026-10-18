"""Pinned, immutable cache records for provider-backed city explanations.

The cache is an optimization, never artifact-integrity evidence.  A genuine absence is
the only cache miss.  Any present but malformed, noncanonical, redirected, oversized, or
cross-bound record is a typed hard failure and is left untouched for inspection.
"""

from __future__ import annotations

import json
from contextlib import suppress
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, ValidationError, model_validator

from adlife.adapters.cognition.city_cache_storage import (
    CityCognitionCacheByteStorage,
    CityCognitionCacheConflict,
    CityCognitionCacheCorruption,
    CityCognitionCachePublicationError,
    CityCognitionCacheUnavailable,
)
from adlife.city.workbench_workspace import WorkbenchWorkspace
from adlife.core.domain.city_cognition import (
    CITY_COGNITION_RESERVATION_TOKENS,
    MAX_CITY_COGNITION_USAGE_INTEGER,
    CityCognitionInputV1,
    CityCognitionRequestV1,
    CityCognitionResultV1,
    Hash,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

CITY_COGNITION_CACHE_KEY_VERSION: Literal["city-cognition-cache-v1"] = "city-cognition-cache-v1"
MAX_CITY_COGNITION_CACHE_RECORD_BYTES = 131_072


def _validated_request_and_input(
    request: CityCognitionRequestV1,
    cognition_input: CityCognitionInputV1,
) -> tuple[CityCognitionRequestV1, CityCognitionInputV1]:
    if not isinstance(request, CityCognitionRequestV1):
        raise TypeError("request must be a CityCognitionRequestV1")
    if not isinstance(cognition_input, CityCognitionInputV1):
        raise TypeError("cognition_input must be a CityCognitionInputV1")
    checked_request: CityCognitionRequestV1 | None = None
    checked_input: CityCognitionInputV1 | None = None
    try:
        checked_request = CityCognitionRequestV1.model_validate(
            request.model_dump(mode="python", warnings="none")
        )
        checked_input = CityCognitionInputV1.model_validate(
            cognition_input.model_dump(mode="python", warnings="none")
        )
    except (TypeError, ValidationError, ValueError):
        pass
    if checked_request is None or checked_input is None:
        raise ValueError("city cognition cache key material failed validation")
    if checked_input.mode not in {"local", "remote"}:
        raise ValueError("city cognition cache requires a provider input")
    if checked_request.run_input_sha256 != checked_input.run_input_sha256:
        raise ValueError("city cognition cache key material is cross-bound")
    reservation = next(
        (
            item
            for item in checked_input.reservations
            if item.request_id == checked_request.request_id
        ),
        None,
    )
    if (
        reservation is None
        or reservation.request_sha256 != checked_request.fingerprint
        or reservation.reserved_output_tokens != CITY_COGNITION_RESERVATION_TOKENS
    ):
        raise ValueError("city cognition cache request is not a funded frozen selection")
    return checked_request, checked_input


def _key_from_digests(request_sha256: str, cognition_input_sha256: str) -> str:
    material = {
        "cache_key_version": CITY_COGNITION_CACHE_KEY_VERSION,
        "cognition_input_sha256": cognition_input_sha256,
        "request_sha256": request_sha256,
    }
    return sha256(canonical_json(material).encode("utf-8")).hexdigest()


class CityCognitionCacheRecordV1(DomainModel):
    """One validated answer and the complete non-secret contracts that address it."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    schema_version: Literal[1]
    key: Hash
    request: CityCognitionRequestV1
    cognition_input: CityCognitionInputV1
    result: CityCognitionResultV1
    prompt_tokens: int | None = Field(default=None, ge=0, le=MAX_CITY_COGNITION_USAGE_INTEGER)
    completion_tokens: int | None = Field(
        default=None,
        ge=0,
        le=MAX_CITY_COGNITION_USAGE_INTEGER,
    )

    @model_validator(mode="after")
    def coherent_binding(self) -> Self:
        checked_request, checked_input = _validated_request_and_input(
            self.request,
            self.cognition_input,
        )
        if self.result.request_id != checked_request.request_id:
            raise ValueError("city cognition cache result answers another request")
        expected = _key_from_digests(
            checked_request.fingerprint,
            checked_input.fingerprint,
        )
        if self.key != expected:
            raise ValueError("city cognition cache key does not address its contracts")
        if (self.prompt_tokens is None) != (self.completion_tokens is None):
            raise ValueError("city cognition cache usage must be wholly present or absent")
        if len((canonical_json(self) + "\n").encode("utf-8")) > (
            MAX_CITY_COGNITION_CACHE_RECORD_BYTES
        ):
            raise ValueError("city cognition cache record exceeds its size limit")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_record(data: bytes, key: str) -> CityCognitionCacheRecordV1:
    record: CityCognitionCacheRecordV1 | None = None
    canonical: bytes | None = None
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("cache document is not an object")
        # The domain contracts are strict Python models whose tuple fields are JSON
        # arrays on disk.  Validate the original JSON representation after the
        # duplicate/depth/finite preflight so Pydantic applies its JSON tuple semantics
        # instead of incorrectly treating decoded lists as Python caller input.
        record = CityCognitionCacheRecordV1.model_validate_json(text)
        canonical = (canonical_json(record) + "\n").encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError, ValueError):
        pass
    if record is None or canonical is None:
        raise CityCognitionCacheCorruption()
    if canonical != data or record.key != key:
        raise CityCognitionCacheCorruption()
    return record


class CityCognitionCache:
    """Read and no-clobber publish bounded city cognition cache records."""

    __slots__ = ("_storage", "workspace")

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        self._storage = CityCognitionCacheByteStorage(
            workspace,
            namespace="city-cognition-cache",
            max_record_bytes=MAX_CITY_COGNITION_CACHE_RECORD_BYTES,
        )
        self.workspace = workspace

    @staticmethod
    def make_key(
        request: CityCognitionRequestV1,
        cognition_input: CityCognitionInputV1,
    ) -> str:
        checked_request, checked_input = _validated_request_and_input(
            request,
            cognition_input,
        )
        return _key_from_digests(checked_request.fingerprint, checked_input.fingerprint)

    def get(self, key: str) -> CityCognitionCacheRecordV1 | None:
        """Return one exact record; only an absent name is a cache miss."""
        data = self._storage.read(key)
        if data is None:
            return None
        return _parse_record(data, key)

    def put(self, key: str, record: CityCognitionCacheRecordV1) -> None:
        """Publish one canonical cache record atomically without replacing a name."""
        self._storage._filename(key)
        if not isinstance(record, CityCognitionCacheRecordV1):
            raise TypeError("record must be a CityCognitionCacheRecordV1")
        checked: CityCognitionCacheRecordV1 | None = None
        with suppress(TypeError, ValidationError, ValueError):
            checked = CityCognitionCacheRecordV1.model_validate(
                record.model_dump(mode="python", warnings="none")
            )
        if checked is None:
            raise ValueError("city cognition cache record failed validation")
        if checked.key != key:
            raise ValueError("city cognition cache key does not match the record")
        document = (canonical_json(checked) + "\n").encode("utf-8")
        if len(document) > MAX_CITY_COGNITION_CACHE_RECORD_BYTES:
            raise ValueError("city cognition cache record exceeds its size limit")

        self._storage.publish(key, document)


__all__ = [
    "CITY_COGNITION_CACHE_KEY_VERSION",
    "MAX_CITY_COGNITION_CACHE_RECORD_BYTES",
    "CityCognitionCache",
    "CityCognitionCacheConflict",
    "CityCognitionCacheCorruption",
    "CityCognitionCachePublicationError",
    "CityCognitionCacheRecordV1",
    "CityCognitionCacheUnavailable",
]
