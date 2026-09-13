from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from math import isfinite
from numbers import Complex, Integral, Real
from typing import TypeVar

from pydantic import BaseModel

from adlife.core.domain.campaign import BillboardPlacement, PhonePlacement, Placement

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _raw_python(value: object) -> object:
    if isinstance(value, BaseModel):
        return {
            field_name: _raw_python(getattr(value, field_name))
            for field_name in type(value).model_fields
            if hasattr(value, field_name)
        }
    if isinstance(value, tuple):
        return tuple(_raw_python(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_raw_python(item) for item in value)
    if isinstance(value, list):
        return [_raw_python(item) for item in value]
    if isinstance(value, set):
        return {_raw_python(item) for item in value}
    if isinstance(value, Mapping):
        return {key: _raw_python(item) for key, item in value.items()}
    return value


def _reject_non_finite(value: object, *, label: str) -> None:
    finite = True
    if isinstance(value, (bool, Integral)):
        pass
    elif isinstance(value, Decimal):
        finite = value.is_finite()
    elif isinstance(value, Real):
        try:
            finite = isfinite(value)
        except OverflowError:
            finite = False
    elif isinstance(value, Complex):
        finite = isfinite(value.real) and isfinite(value.imag)
    if not finite:
        raise ValueError(f"{label} must not contain non-finite numeric values")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite(item, label=label)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reject_non_finite(item, label=label)


def revalidate_model(
    value: object,
    model_type: type[_ModelT],
    *,
    label: str,
) -> _ModelT:
    if not isinstance(value, model_type):
        raise TypeError(f"{label} must be a {model_type.__name__}")
    raw = _raw_python(value)
    if not isinstance(raw, Mapping):
        raise TypeError(f"{label} must contain model field data")
    _reject_non_finite(raw, label=label)
    validated = model_type.model_validate(raw)
    _reject_non_finite(_raw_python(validated), label=label)
    return validated


def revalidate_placement(value: object, *, label: str = "placement") -> Placement:
    if isinstance(value, PhonePlacement):
        return revalidate_model(value, PhonePlacement, label=label)
    if isinstance(value, BillboardPlacement):
        return revalidate_model(value, BillboardPlacement, label=label)
    raise TypeError(f"{label} must be a PhonePlacement or BillboardPlacement")


__all__ = ["revalidate_model", "revalidate_placement"]
