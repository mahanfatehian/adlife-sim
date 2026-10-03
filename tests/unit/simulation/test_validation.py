from __future__ import annotations

import pytest
from pydantic import ValidationError

from adlife.core.domain.person import DomainModel
from adlife.core.simulation._validation import revalidate_model


class _StrictIntegerModel(DomainModel):
    value: int


def test_revalidation_cache_uses_object_identity_not_structural_equality() -> None:
    cached = revalidate_model(
        _StrictIntegerModel(value=1),
        _StrictIntegerModel,
        label="strict integer model",
    )
    poison = _StrictIntegerModel.model_construct(value=True)

    assert poison is not cached
    assert poison == cached
    assert hash(poison) == hash(cached)
    with pytest.raises(ValidationError, match="integer"):
        revalidate_model(
            poison,
            _StrictIntegerModel,
            label="strict integer model",
        )
