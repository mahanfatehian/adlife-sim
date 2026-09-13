import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, TypeAdapter

_JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)


def _json_fallback(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonicalize_dump(source: object, dumped: object) -> object:
    if isinstance(source, BaseModel):
        if not isinstance(dumped, Mapping):
            raise TypeError("Pydantic model did not produce a JSON object")
        fields = type(source).model_fields
        return {
            key: _canonicalize_dump(getattr(source, key), item) if key in fields else item
            for key, item in dumped.items()
        }

    if isinstance(source, Mapping):
        if not isinstance(dumped, Mapping):
            raise TypeError("mapping did not produce a JSON object")
        canonical: dict[str, object] = {}
        for key, item in source.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            canonical[key] = _canonicalize_dump(item, dumped[key])
        return canonical

    if isinstance(source, (set, frozenset)):
        if not isinstance(dumped, list):
            raise TypeError("set did not produce a JSON array")
        items = [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]
        return sorted(items, key=_canonical_json)

    if isinstance(source, (list, tuple)):
        if not isinstance(dumped, list):
            raise TypeError("sequence did not produce a JSON array")
        return [
            _canonicalize_dump(item, dumped_item)
            for item, dumped_item in zip(source, dumped, strict=True)
        ]

    return dumped


def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    if isinstance(value, BaseModel):
        dumped: object = value.model_dump(mode="json")
    else:
        dumped = _JSON_ADAPTER.dump_python(value, mode="json", fallback=_json_fallback)
    canonical = _canonical_json(_canonicalize_dump(value, dumped)).encode("utf-8")
    return sha256(canonical).hexdigest()


def stable_event_id(run_id: str, sequence: int) -> str:
    return f"{run_id}:event-{sequence:08d}"


__all__ = ["canonical_sha256", "stable_event_id"]
