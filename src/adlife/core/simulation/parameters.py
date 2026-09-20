"""The run-level model parameters and the one object that carries them.

Task 13's sensitivity analysis must be able to ask "what if attention were 20 percent
weaker?" and get an honest answer, which means the engine's behavioural constants must
become run inputs rather than baked-in module numbers. This module is that input: one
frozen, bounded, serialisable record of every knob a run may turn, with the documented
values as defaults.

THE DEFAULTS ARE THE ENGINE. Every default here equals the module constant the engine
used before this record existed, so a model built without parameters commits
byte-identical events to one built with them - which is what keeps the golden and
property suites authoritative while experiments vary the rest. The default constants
themselves are intentionally NOT imported here: ``memory``, ``social`` and ``decision``
consume this record, so importing them back would close a cycle. The contract instead
is pinned by ``tests/unit/simulation/test_parameters.py``, which asserts each default
equals the consuming module's constant.

Each field names the family it belongs to, and the experiments layer groups them the
same way: attention, persuasion, memory decay, homophily, word of mouth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import Field

from adlife.core.domain.person import DomainModel

ParameterName = Literal[
    "notice_scale",
    "sentiment_gain",
    "recall_gain",
    "recall_retention",
    "ad_fatigue_retention",
    "salience_retention",
    "social_similarity_weight",
    "share_probability_scale",
    "social_proof_gain",
    "social_recall_gain",
]

_PARAMETER_FIELDS: tuple[ParameterName, ...] = (
    "notice_scale",
    "sentiment_gain",
    "recall_gain",
    "recall_retention",
    "ad_fatigue_retention",
    "salience_retention",
    "social_similarity_weight",
    "share_probability_scale",
    "social_proof_gain",
    "social_recall_gain",
)

_PARAMETER_BOUNDS: dict[ParameterName, tuple[float, float]] = {
    "notice_scale": (0.0, 2.0),
    "sentiment_gain": (0.0, 2.0),
    "recall_gain": (0.0, 2.0),
    "recall_retention": (0.0, 1.0),
    "ad_fatigue_retention": (0.0, 1.0),
    "salience_retention": (0.0, 1.0),
    "social_similarity_weight": (0.0, 1.0),
    "share_probability_scale": (0.0, 2.0),
    "social_proof_gain": (0.0, 1.0),
    "social_recall_gain": (0.0, 1.0),
}


class ModelParameters(DomainModel):
    """Every behavioural knob one run may turn, frozen and bounded.

    The multipliers scale a documented quantity without redefining it - ``notice_scale``
    scales the documented notice probability, the gains scale the documented deltas -
    and the retention values REPLACE the decay constants, so they carry the same [0, 1]
    bounds the constants are read from. ``social_similarity_weight`` is the one
    structural knob: at 0.0 the word-of-mouth receiver draw is exactly the uniform draw
    the engine has always made, and a positive weight mixes in an interest-similarity
    preference over the same keyed draw, so homophily can be studied without changing
    the default behaviour.
    """

    schema_version: Literal[1] = 1

    # attention: the documented notice probability, scaled before its final clamp.
    notice_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    # persuasion: the composed reaction deltas, scaled and re-clamped into their bands.
    sentiment_gain: float = Field(default=1.0, ge=0.0, le=2.0)
    recall_gain: float = Field(default=1.0, ge=0.0, le=2.0)
    # memory decay: the documented retention values, replaced verbatim.
    recall_retention: float = Field(default=0.85, ge=0.0, le=1.0)
    ad_fatigue_retention: float = Field(default=0.60, ge=0.0, le=1.0)
    salience_retention: float = Field(default=0.85, ge=0.0, le=1.0)
    # homophily: the similarity share of the word-of-mouth receiver draw; 0 is uniform.
    social_similarity_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    # word of mouth: the share signal scale and the documented social gains.
    share_probability_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    social_proof_gain: float = Field(default=0.25, ge=0.0, le=1.0)
    social_recall_gain: float = Field(default=0.10, ge=0.0, le=1.0)

    def as_mapping(self) -> dict[str, float]:
        """The manifest's view: parameter name to plain float, in documented order."""
        return {name: float(getattr(self, name)) for name in _PARAMETER_FIELDS}

    def with_(self, name: ParameterName, value: float) -> Self:
        """Return the same parameters with one knob turned, clamped into its bounds.

        Clamping is the documented perturbation semantics, not a silent repair: a
        sensitivity delta that walks a retention past 1.0 asks for the model with that
        retention AT its physical limit, and the clamped value is what the arm's
        manifest records, so the sweep's claim matches the run it drove.
        """
        if name not in _PARAMETER_FIELDS:
            raise ValueError(f"unknown model parameter: {name}")
        lower, upper = _PARAMETER_BOUNDS[name]
        return type(self).model_validate({**self.as_mapping(), name: min(upper, max(lower, value))})

    @property
    def families(self) -> Mapping[str, tuple[ParameterName, ...]]:
        """The documented parameter families, used by the sensitivity sweep."""
        return {
            "attention": ("notice_scale",),
            "persuasion": ("sentiment_gain", "recall_gain"),
            "memory-decay": ("recall_retention", "ad_fatigue_retention", "salience_retention"),
            "homophily": ("social_similarity_weight",),
            "word-of-mouth": (
                "share_probability_scale",
                "social_proof_gain",
                "social_recall_gain",
            ),
        }


DEFAULT_PARAMETERS = ModelParameters()
"""The engine's documented behaviour: the defaults, unchanged."""

__all__ = [
    "DEFAULT_PARAMETERS",
    "ModelParameters",
    "ParameterName",
]
