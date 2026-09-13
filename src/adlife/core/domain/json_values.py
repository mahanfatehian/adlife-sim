from collections.abc import Iterator, Mapping
from math import isfinite
from types import MappingProxyType
from typing import Self


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
    frozen = _freeze_json_value(value, active=set())
    if not isinstance(frozen, FrozenJsonMapping):
        raise ValueError("value must be a JSON object")
    return frozen


def thaw_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


def _validate_json_string(value: str) -> str:
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("JSON strings cannot contain surrogate code points")
    return value


def _freeze_json_value(value: object, active: set[int]) -> object:
    if isinstance(value, str):
        return _validate_json_string(value)

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            raise ValueError("JSON containers cannot be cyclic")
        active.add(marker)
        try:
            frozen: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                _validate_json_string(key)
                frozen[key] = _freeze_json_value(item, active)
            return FrozenJsonMapping(frozen)
        finally:
            active.remove(marker)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in active:
            raise ValueError("JSON containers cannot be cyclic")
        active.add(marker)
        try:
            return tuple(_freeze_json_value(item, active) for item in value)
        finally:
            active.remove(marker)
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


__all__ = ["FrozenJsonMapping", "freeze_json_mapping", "thaw_json_mapping"]
