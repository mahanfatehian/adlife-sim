"""Message references: the contract between the story builder and both renderers.

A builder decides *what* to say and emits a :class:`MessageRef` (a catalog key plus typed
parameters); the story page (JavaScript) and the HTML report (Jinja) translate the same
reference, so their sentences cannot drift apart. The JSON form is::

    {"key": "story.moment.noticed",
     "params": {"time": {"clock": 437}, "name": {"person": "person-006"},
                "product": {"text": "Lumen Pocket"},
                "where": {"key": "story.where.mobile-feed.home"}}}

``count`` selects the plural form and is also available to the message as ``{count}``,
so it is reserved and never a parameter name. A reference holds at most 12 parameters and
nests at most three levels deep, where a nested reference and a list each add a level.
Serialization always uses the wire names (``int``, ``id``, ``list``) and omits absent
optional members, so ``model_dump()`` in any mode round-trips through validation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Final, Literal, TypeAlias

from pydantic import (
    Discriminator,
    Field,
    SerializerFunctionWrapHandler,
    Tag,
    model_serializer,
    model_validator,
)

from adlife.core.domain.person import DomainModel
from adlife.i18n.formatting import MAX_SAFE_INTEGER

MESSAGE_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)+$"
MAX_MESSAGE_KEY_LENGTH: Final = 96
PARAMETER_NAME_PATTERN: Final = r"^[a-z][a-z0-9_]*$"
MAX_PARAMETER_NAME_LENGTH: Final = 32
RESERVED_PARAMETER_NAMES: Final = frozenset({"count"})
MAX_REF_PARAMS: Final = 12
MAX_REF_DEPTH: Final = 3
MAX_LIST_ITEMS: Final = 8
MAX_TEXT_PARAM_LENGTH: Final = 240
MAX_ID_PARAM_LENGTH: Final = 120
MAX_CLOCK_MINUTE: Final = 7 * 1440
AGENT_ID_PATTERN: Final = r"^person-[0-9]{3}$"

ParamKind = Literal["text", "id", "key", "int", "num", "pct", "clock", "person", "list"]
PARAM_KINDS: Final[tuple[ParamKind, ...]] = (
    "text",
    "id",
    "key",
    "int",
    "num",
    "pct",
    "clock",
    "person",
    "list",
)

MessageKey: TypeAlias = Annotated[
    str, Field(min_length=3, max_length=MAX_MESSAGE_KEY_LENGTH, pattern=MESSAGE_KEY_PATTERN)
]
ParameterName: TypeAlias = Annotated[
    str, Field(min_length=1, max_length=MAX_PARAMETER_NAME_LENGTH, pattern=PARAMETER_NAME_PATTERN)
]


class _RefModel(DomainModel):
    """Strict, frozen wire model that always serializes with wire names."""

    kind: ClassVar[ParamKind]

    @model_serializer(mode="wrap")
    def _wire_form(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        fields = type(self).model_fields
        wire: dict[str, Any] = {}
        for name, value in data.items():
            if value is None:
                continue
            field = fields.get(name)
            wire[field.alias if field is not None and field.alias else name] = value
        return wire


class TextParam(_RefModel):
    """Verbatim user or data text, rendered in ``<bdi dir="auto">``."""

    kind: ClassVar[ParamKind] = "text"
    text: str = Field(max_length=MAX_TEXT_PARAM_LENGTH)


class IdParam(_RefModel):
    """An ASCII identifier, rendered in ``<bdi dir="ltr">``; digits are never localized."""

    kind: ClassVar[ParamKind] = "id"
    identifier: str = Field(
        alias="id",
        min_length=1,
        max_length=MAX_ID_PARAM_LENGTH,
        pattern=r"^[\x20-\x7e]+$",
    )


class IntParam(_RefModel):
    """A localized integer, rendered in ``<bdi dir="ltr">``."""

    kind: ClassVar[ParamKind] = "int"
    value: int = Field(alias="int", ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)


class NumParam(_RefModel):
    """A localized decimal with 0-3 fraction digits, rendered in ``<bdi dir="ltr">``."""

    kind: ClassVar[ParamKind] = "num"
    num: float = Field(gt=-1e21, lt=1e21)
    decimals: int = Field(ge=0, le=3)
    signed: bool | None = None


class PctParam(_RefModel):
    """A ratio shown as a percentage through ``common.percent``."""

    kind: ClassVar[ParamKind] = "pct"
    pct: float = Field(gt=-1e18, lt=1e18)
    decimals: int = Field(ge=0, le=1)


class ClockParam(_RefModel):
    """An absolute simulated minute: ``HH:MM`` for one-day runs, else ``common.day_clock``."""

    kind: ClassVar[ParamKind] = "clock"
    clock: int = Field(ge=0, le=MAX_CLOCK_MINUTE)


class PersonParam(_RefModel):
    """A person reference resolved through the context into ``common.person_ref``."""

    kind: ClassVar[ParamKind] = "person"
    person: str = Field(pattern=AGENT_ID_PATTERN)


class ListParam(_RefModel):
    """Up to eight parameters joined with ``common.list_separator``."""

    kind: ClassVar[ParamKind] = "list"
    # JSON arrays arrive as Python lists; only the container is lax, every item is strict.
    items: tuple[RefParam, ...] = Field(alias="list", max_length=MAX_LIST_ITEMS, strict=False)


class MessageRef(_RefModel):
    """A catalog key with an optional plural count and typed parameters (spec 5.3).

    The same model is the ``key`` parameter kind, so references nest.
    """

    kind: ClassVar[ParamKind] = "key"
    key: MessageKey
    count: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER)
    params: dict[ParameterName, RefParam] | None = Field(default=None, max_length=MAX_REF_PARAMS)

    @model_validator(mode="after")
    def bounded_reference(self) -> MessageRef:
        if self.params is not None and RESERVED_PARAMETER_NAMES.intersection(self.params):
            raise ValueError("count is supplied by the reference count, not a parameter")
        if ref_depth(self) > MAX_REF_DEPTH:
            raise ValueError("message reference nesting is too deep")
        return self


def _param_kind(value: Any) -> str | None:
    if isinstance(value, _RefModel):
        return value.kind
    if isinstance(value, Mapping):
        present = [kind for kind in PARAM_KINDS if kind in value]
        # Exactly one kind member identifies the shape; anything else is refused.
        return present[0] if len(present) == 1 else None
    return None


RefParam: TypeAlias = Annotated[
    Annotated[TextParam, Tag("text")]
    | Annotated[IdParam, Tag("id")]
    | Annotated[MessageRef, Tag("key")]
    | Annotated[IntParam, Tag("int")]
    | Annotated[NumParam, Tag("num")]
    | Annotated[PctParam, Tag("pct")]
    | Annotated[ClockParam, Tag("clock")]
    | Annotated[PersonParam, Tag("person")]
    | Annotated[ListParam, Tag("list")],
    Discriminator(_param_kind),
]

ListParam.model_rebuild()
MessageRef.model_rebuild()


def ref_depth(ref: MessageRef) -> int:
    """Return the nesting depth: 1 for a flat reference, plus 1 per nested ref or list."""
    params = ref.params or {}
    return 1 + max((_param_depth(param) for param in params.values()), default=0)


def _param_depth(param: RefParam) -> int:
    if isinstance(param, MessageRef):
        return ref_depth(param)
    if isinstance(param, ListParam):
        return 1 + max((_param_depth(item) for item in param.items), default=0)
    return 0


@dataclass(frozen=True, slots=True)
class PersonLabel:
    """How a person reference is named: a template persona key, or saved display text."""

    template_key: str | None
    display_name: str
    number: int


@dataclass(frozen=True, slots=True)
class RefContext:
    """Run facts every reference may need: the day count and the people by agent ID."""

    days: int
    people: Mapping[str, PersonLabel]


# Typed constructors keep builder code short and independent of the wire aliases.


def message_ref(key: str, /, *, count: int | None = None, **params: RefParam) -> MessageRef:
    """Build a validated reference; ``params`` are parameter models from this module."""
    payload: dict[str, object] = {"key": key}
    if count is not None:
        payload["count"] = count
    if params:
        payload["params"] = dict(params)
    return MessageRef.model_validate(payload)


def text_param(text: str) -> TextParam:
    return TextParam.model_validate({"text": text})


def id_param(identifier: str) -> IdParam:
    return IdParam.model_validate({"id": identifier})


def int_param(value: int) -> IntParam:
    return IntParam.model_validate({"int": value})


def num_param(value: float, decimals: int, *, signed: bool = False) -> NumParam:
    payload: dict[str, object] = {"num": value, "decimals": decimals}
    if signed:
        payload["signed"] = True
    return NumParam.model_validate(payload)


def pct_param(ratio: float, decimals: int = 0) -> PctParam:
    return PctParam.model_validate({"pct": ratio, "decimals": decimals})


def clock_param(minute: int) -> ClockParam:
    return ClockParam.model_validate({"clock": minute})


def person_param(agent_id: str) -> PersonParam:
    return PersonParam.model_validate({"person": agent_id})


def list_param(items: Sequence[RefParam]) -> ListParam:
    return ListParam.model_validate({"list": tuple(items)})


__all__ = [
    "AGENT_ID_PATTERN",
    "MAX_REF_DEPTH",
    "MAX_REF_PARAMS",
    "MESSAGE_KEY_PATTERN",
    "PARAM_KINDS",
    "ClockParam",
    "IdParam",
    "IntParam",
    "ListParam",
    "MessageRef",
    "NumParam",
    "ParamKind",
    "PctParam",
    "PersonLabel",
    "PersonParam",
    "RefContext",
    "RefParam",
    "TextParam",
    "clock_param",
    "id_param",
    "int_param",
    "list_param",
    "message_ref",
    "num_param",
    "pct_param",
    "person_param",
    "ref_depth",
    "text_param",
]
