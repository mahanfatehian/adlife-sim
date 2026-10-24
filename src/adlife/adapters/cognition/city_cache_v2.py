"""Isolated causal cache for validated bounded city signals.

Only reusable provider evidence is stored. Run provenance is checked at admission,
then freshly bound by the service when a cache result becomes a terminal record.
"""

from __future__ import annotations

import json
import weakref
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
from adlife.adapters.cognition.city_prompts_v2 import (
    city_cognition_provider_projection_sha256_v2,
)
from adlife.city.workbench_workspace import WorkbenchWorkspace
from adlife.core.domain.city_cognition import MAX_CITY_COGNITION_USAGE_INTEGER, Hash, RequestId
from adlife.core.domain.city_cognition_v2 import (
    _CITY_COGNITION_TERMINAL_POLICY_HASHES_BY_VERSION,
    CityCognitionInputV2,
    CityCognitionRequestV2,
    CityCognitionResultV2,
    city_cognition_contract_selection_version,
)
from adlife.core.domain.person import DomainModel
from adlife.core.domain.serialization import canonical_json

CITY_COGNITION_CACHE_KEY_VERSION_V2: Literal["city-cognition-cache-v2"] = "city-cognition-cache-v2"
MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES = 131_072
_POLICY_FIELDS = (
    "prompt_contract_sha256",
    "result_schema_contract_sha256",
    "selection_policy_sha256",
    "fallback_policy_sha256",
    "mock_policy_sha256",
    "influence_policy_sha256",
)
_IDENTITY_FIELDS = (
    "cache_key_version",
    "request_id",
    "provider_projection_sha256",
    "causal_contract_sha256",
    "provider_revision_sha256",
    "provider_profile_snapshot_sha256",
    "provider_effective_settings_sha256",
    *_POLICY_FIELDS,
)


# One frozen input serves every cache key of a run. Revalidate each input object once
# and reuse that validated copy, so admitting N requests costs N request checks plus
# one input check instead of N input checks (quadratic at 1000 requests). Entries are
# keyed by object identity, verified through a weak reference, and dropped with it.
_CHECKED_INPUTS: dict[
    int, tuple[weakref.ReferenceType[CityCognitionInputV2], CityCognitionInputV2]
] = {}


def _checked_cache_input(cognition_input: CityCognitionInputV2) -> CityCognitionInputV2:
    object_id = id(cognition_input)
    entry = _CHECKED_INPUTS.get(object_id)
    if entry is not None and entry[0]() is cognition_input:
        return entry[1]
    checked = CityCognitionInputV2.model_validate(
        cognition_input.model_dump(mode="python", warnings="none")
    )

    def forget(reference: weakref.ReferenceType[CityCognitionInputV2]) -> None:
        current = _CHECKED_INPUTS.get(object_id)
        if current is not None and current[0] is reference:
            _CHECKED_INPUTS.pop(object_id, None)

    _CHECKED_INPUTS[object_id] = (weakref.ref(cognition_input, forget), checked)
    return checked


def _cache_key_material_v2(
    request: CityCognitionRequestV2,
    cognition_input: CityCognitionInputV2,
) -> dict[str, str]:
    """Admit a current funded request before deriving reusable cache identity."""
    if type(request) is not CityCognitionRequestV2:
        raise TypeError("request must be a CityCognitionRequestV2")
    if type(cognition_input) is not CityCognitionInputV2:
        raise TypeError("cognition_input must be a CityCognitionInputV2")
    checked: tuple[CityCognitionRequestV2, CityCognitionInputV2] | None = None
    with suppress(TypeError, ValueError, RecursionError):
        checked = (
            CityCognitionRequestV2.model_validate(
                request.model_dump(mode="python", warnings="none")
            ),
            _checked_cache_input(cognition_input),
        )
    if checked is None:
        raise ValueError("city cognition V2 cache key material failed validation")
    request, frozen = checked
    if (
        frozen.mode not in {"local", "remote"}
        or request.run_input_sha256 != frozen.run_input_sha256
        or request.causal_identity.causal_contract_sha256 != frozen.causal_contract_sha256
    ):
        raise ValueError("city cognition V2 cache requires a matching provider input")
    reservation = next((r for r in frozen.reservations if r.request_id == request.request_id), None)
    if (
        reservation is None
        or reservation.request_record_sha256 != request.request_record_sha256
        or not reservation.funded
        or reservation.reserved_output_tokens
        != frozen.selection_limits.reservation_tokens_per_request
    ):
        raise ValueError("city cognition V2 cache requires a funded frozen selection")
    profile, settings = frozen.provider_profile, frozen.effective_settings
    assert profile is not None and settings is not None
    return {
        "cache_key_version": CITY_COGNITION_CACHE_KEY_VERSION_V2,
        "request_id": request.request_id,
        "provider_projection_sha256": city_cognition_provider_projection_sha256_v2(request),
        "causal_contract_sha256": frozen.causal_contract_sha256,
        "provider_revision_sha256": profile.revision_sha256,
        "provider_profile_snapshot_sha256": profile.fingerprint,
        "provider_effective_settings_sha256": settings.fingerprint,
        **{field: getattr(frozen.causal_contract, field) for field in _POLICY_FIELDS},
    }


def _key(material: dict[str, str]) -> str:
    return sha256(canonical_json(material).encode("utf-8")).hexdigest()


class CityCognitionCacheRecordV2(DomainModel):
    """One reusable, strictly addressed result without complete run/request data."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    schema_version: Literal[2]
    cache_key_version: Literal["city-cognition-cache-v2"]
    key: Hash
    request_id: RequestId
    provider_projection_sha256: Hash
    causal_contract_sha256: Hash
    provider_revision_sha256: Hash
    provider_profile_snapshot_sha256: Hash
    provider_effective_settings_sha256: Hash
    prompt_contract_sha256: Hash
    result_schema_contract_sha256: Hash
    selection_policy_sha256: Hash
    fallback_policy_sha256: Hash
    mock_policy_sha256: Hash
    influence_policy_sha256: Hash
    result: CityCognitionResultV2
    prompt_tokens: int | None = Field(default=None, ge=0, le=MAX_CITY_COGNITION_USAGE_INTEGER)
    completion_tokens: int | None = Field(default=None, ge=0, le=MAX_CITY_COGNITION_USAGE_INTEGER)

    @model_validator(mode="before")
    @classmethod
    def exact_version(cls, value: Any) -> Any:
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("city cognition V2 cache schema must be an exact integer")
        return value

    @model_validator(mode="after")
    def coherent_binding(self) -> Self:
        if type(self.result) is not CityCognitionResultV2:
            raise ValueError("city cognition V2 cache requires an exact V2 result")
        result = CityCognitionResultV2.model_validate(
            self.result.model_dump(mode="python", warnings="none")
        )
        if result.request_id != self.request_id:
            raise ValueError("city cognition V2 cache result answers another request")
        # A record keeps the frozen contract of the run that answered it: V2 and V3
        # request IDs differ, so a cached answer is only ever reused by its own version.
        version = city_cognition_contract_selection_version(self.causal_contract_sha256)
        if version is None or any(
            getattr(self, field) != expected
            for field, expected in _CITY_COGNITION_TERMINAL_POLICY_HASHES_BY_VERSION[version]
        ):
            raise ValueError("city cognition V2 cache policies differ from the frozen contract")
        if self.key != _key({field: getattr(self, field) for field in _IDENTITY_FIELDS}):
            raise ValueError("city cognition V2 cache key does not address its evidence")
        if (self.prompt_tokens is None) != (self.completion_tokens is None):
            raise ValueError("city cognition V2 cache usage must be wholly present or absent")
        if (
            len((canonical_json(self) + "\n").encode("utf-8"))
            > MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES
        ):
            raise ValueError("city cognition V2 cache record exceeds its size limit")
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


def _parse_record(document: bytes, key: str) -> CityCognitionCacheRecordV2:
    record: CityCognitionCacheRecordV2 | None = None
    try:
        if len(document) > MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES:
            raise ValueError("oversized record")
        text = document.decode("utf-8", errors="strict")
        payload = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        if not isinstance(payload, dict):
            raise ValueError("cache document is not an object")
        checked = CityCognitionCacheRecordV2.model_validate_json(text)
        if checked.key == key and (canonical_json(checked) + "\n").encode("utf-8") == document:
            record = checked
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        pass
    if record is None:
        raise CityCognitionCacheCorruption()
    return record


class CityCognitionCacheV2:
    """Bounded canonical records over the shared namespace-pinned byte store."""

    __slots__ = ("_storage", "workspace")

    def __init__(self, workspace: WorkbenchWorkspace) -> None:
        self._storage = CityCognitionCacheByteStorage(
            workspace,
            namespace="city-cognition-cache-v2",
            max_record_bytes=MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES,
        )
        self.workspace = workspace

    @staticmethod
    def make_key(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2) -> str:
        return _key(_cache_key_material_v2(request, cognition_input))

    def get(self, key: str) -> CityCognitionCacheRecordV2 | None:
        document = self._storage.read(key)
        return None if document is None else _parse_record(document, key)

    def put(self, key: str, record: CityCognitionCacheRecordV2) -> None:
        self._storage._filename(key)
        if type(record) is not CityCognitionCacheRecordV2:
            raise TypeError("record must be a CityCognitionCacheRecordV2")
        checked: CityCognitionCacheRecordV2 | None = None
        with suppress(TypeError, ValidationError, ValueError, RecursionError):
            checked = CityCognitionCacheRecordV2.model_validate(
                record.model_dump(mode="python", warnings="none")
            )
        if checked is None or checked.key != key:
            raise ValueError("city cognition V2 cache record failed validation")
        self._storage.publish(key, (canonical_json(checked) + "\n").encode("utf-8"))


__all__ = [
    "CITY_COGNITION_CACHE_KEY_VERSION_V2",
    "MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES",
    "CityCognitionCacheConflict",
    "CityCognitionCacheCorruption",
    "CityCognitionCachePublicationError",
    "CityCognitionCacheRecordV2",
    "CityCognitionCacheUnavailable",
    "CityCognitionCacheV2",
]
