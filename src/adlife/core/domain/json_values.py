"""Immutable JSON values, and the bounds that make one storable.

Everything free-form this repository writes to disk passes through
:func:`freeze_json_mapping`: an event payload, a result's metric map, and a cognition
prompt document. It is therefore the one place a bound on the SHAPE of that data belongs.

DEPTH IS A BOUND, NOT A DETAIL. pydantic-core's serializer refuses a document of roughly a
hundred nested containers with a bare ``ValueError`` reading "Circular reference detected
(depth exceeded)". Without a bound here the domain model ACCEPTED such a document and the
refusal arrived later, from whichever writer serialised it - outside the storage and
event-sink families, as an unexpected defect rather than a refused tick. A document this
deep is not something the simulator builds; :data:`MAX_JSON_DEPTH` is far above anything
it does build and far below what its own serializer can render.
"""

from collections.abc import Iterator, Mapping
from math import isfinite
from types import MappingProxyType
from typing import Self

MAX_JSON_DEPTH = 32
"""How many containers a stored JSON document may nest.

The deepest structure this repository constructs is three. The number that matters on the
other side is pydantic-core's own recursion guard at about a hundred: a bound at or above
it would let the serializer fail before the model did, which is the defect this closes.
"""


class FrozenJsonMapping(Mapping[str, object]):
    __slots__ = ("_values",)
    _values: Mapping[str, object]

    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values or {})))

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self._values)!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return dict(self.items()) == dict(other.items())

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError(f"{type(self).__name__} is immutable")

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self


def freeze_json_mapping(value: object) -> FrozenJsonMapping:
    frozen = _freeze_json_value(value, active=set(), depth=0)
    if not isinstance(frozen, FrozenJsonMapping):
        raise ValueError("value must be a JSON object")
    return frozen


def thaw_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


def _validate_json_string(value: str) -> str:
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("JSON strings cannot contain surrogate code points")
    return value


def _freeze_json_value(value: object, active: set[int], depth: int) -> object:
    if isinstance(value, str):
        return _validate_json_string(value)

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        marker = _enter_container(value, active, depth)
        try:
            frozen: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                _validate_json_string(key)
                frozen[key] = _freeze_json_value(item, active, depth + 1)
            return FrozenJsonMapping(frozen)
        finally:
            active.remove(marker)
    if isinstance(value, (list, tuple)):
        marker = _enter_container(value, active, depth)
        try:
            return tuple(_freeze_json_value(item, active, depth + 1) for item in value)
        finally:
            active.remove(marker)
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def _enter_container(value: object, active: set[int], depth: int) -> int:
    """Refuse a cycle and a document that nests too deeply; return the cycle marker."""
    if depth >= MAX_JSON_DEPTH:
        raise ValueError(f"JSON containers cannot nest more than {MAX_JSON_DEPTH} levels deep")
    marker = id(value)
    if marker in active:
        raise ValueError("JSON containers cannot be cyclic")
    active.add(marker)
    return marker


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


__all__ = ["MAX_JSON_DEPTH", "FrozenJsonMapping", "freeze_json_mapping", "thaw_json_mapping"]
