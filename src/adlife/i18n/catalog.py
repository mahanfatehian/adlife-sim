"""Packaged guide catalogs: strict loading, validation and message rendering.

Each language has one JSON catalog in ``adlife/i18n/catalogs``; the browser receives the
exact packaged bytes, so Python and JavaScript translate from the same document. A
message is a string or a plural object with exactly ``one`` and ``other``. Placeholders
look like ``{name}``; every parameter renders as a ``<bdi>`` element in markup (spec 5.3),
and isolation is never done by inserting control characters.
"""

from __future__ import annotations

import builtins
import functools
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Final, Literal, NamedTuple, TypeAlias

from markupsafe import Markup, escape

from adlife.i18n.formatting import (
    LANGUAGES,
    MINUTES_PER_DAY,
    Language,
    TextDirection,
    format_decimal,
    format_integer,
    format_time_of_day,
    plural_category,
    text_direction,
)
from adlife.i18n.refs import (
    MESSAGE_KEY_PATTERN,
    ClockParam,
    IdParam,
    IntParam,
    ListParam,
    MessageRef,
    NumParam,
    PctParam,
    PersonParam,
    RefContext,
    RefParam,
    TextParam,
)

CATALOG_SCHEMA_VERSION: Final = 1
MAX_CATALOG_BYTES: Final = 262_144
MAX_MESSAGE_LENGTH: Final = 600
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_KEY: Final = re.compile(MESSAGE_KEY_PATTERN)
MAX_KEY_LENGTH: Final = 96
PLACEHOLDER_PATTERN: Final = re.compile(r"\{([a-z][a-z0-9_]*)\}")
# Renderers split on the slightly wider spec pattern; validation guarantees both agree.
_SPLIT: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_REFUSED_CHARACTER: Final = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c<>]"
)
_REFUSED_SUBSTRINGS: Final = ("http://", "https://")
_PERSIAN_REFUSED: Final = re.compile(r"[\u064a\u0643]")
_ENVELOPE_KEYS: Final = frozenset({"schema_version", "language", "direction", "messages"})

Direction: TypeAlias = Literal["auto", "ltr", "rtl"]


class CatalogError(ValueError):
    """A refused catalog, an unknown key, or a reference that cannot be rendered."""


class PluralMessage(NamedTuple):
    one: str
    other: str


Message: TypeAlias = str | PluralMessage


@dataclass(frozen=True, slots=True)
class _Isolate:
    direction: Direction
    children: tuple[_Node, ...]


@dataclass(frozen=True, slots=True)
class _Raw:
    markup: Markup


_Node: TypeAlias = str | _Isolate | _Raw


def placeholders(template: str) -> frozenset[str]:
    """Return the placeholder names one message template uses."""
    return frozenset(PLACEHOLDER_PATTERN.findall(template))


def message_placeholders(message: Message) -> frozenset[str]:
    """Return every placeholder of a message, across both plural forms."""
    if isinstance(message, PluralMessage):
        return placeholders(message.one) | placeholders(message.other)
    return placeholders(message)


class Catalog:
    """One validated language catalog with formatting and reference rendering."""

    __slots__ = ("_messages", "direction", "language")

    language: Language
    direction: TextDirection

    def __init__(self, language: Language, messages: Mapping[str, Message]) -> None:
        if language not in LANGUAGES:
            raise CatalogError("catalog language is not supported")
        self.language = language
        self.direction = text_direction(language)
        self._messages: dict[str, Message] = dict(messages)

    @classmethod
    def from_mapping(cls, language: Language, messages: Mapping[str, object]) -> Catalog:
        """Validate raw catalog messages (spec 5.1 rules 2-5) and build a catalog."""
        if language not in LANGUAGES:
            raise CatalogError("catalog language is not supported")
        if not isinstance(messages, Mapping):
            raise CatalogError("catalog messages must be an object")
        validated: dict[str, Message] = {}
        for key, value in messages.items():
            validated[_checked_key(key)] = _checked_message(key, value, language)
        return cls(language, validated)

    def has(self, key: str) -> bool:
        return key in self._messages

    def message_keys(self) -> frozenset[str]:
        """Return every message key of this catalog."""
        return frozenset(self._messages)

    def message(self, key: str) -> Message:
        """Return the raw message for ``key``; raises :class:`CatalogError` when unknown."""
        try:
            return self._messages[key]
        except KeyError:
            raise CatalogError(f"unknown message key {key!r}") from None

    def text(
        self,
        key: str,
        /,
        *,
        count: builtins.int | None = None,
        **params: str | builtins.int,
    ) -> str:
        """Render a message as plain text; integer parameters are localized."""
        return self.format_text(key, count, params)

    def format_text(
        self,
        key: str,
        count: builtins.int | None,
        params: Mapping[str, object],
    ) -> str:
        """Render ``key`` as plain text from explicit parameters (the ``tr_text`` global).

        Text and whole numbers are accepted; ``params`` may name ``count`` only to fill
        ``{count}`` with text in a message that is not plural.
        """
        _check_count(count)
        return _plain(self._fill(key, count, self._value_nodes(params)))

    def format_markup(
        self,
        key: str,
        count: builtins.int | None,
        params: Mapping[str, object],
    ) -> Markup:
        """Render ``key`` as escaped markup with each parameter in ``<bdi>`` (``tr``)."""
        _check_count(count)
        return Markup(_markup(self._fill(key, count, self._value_nodes(params))))

    def markup(self, ref: MessageRef, ctx: RefContext) -> Markup:
        """Render a reference as escaped HTML with every parameter in ``<bdi>``."""
        return Markup(_markup(self._ref_nodes(_checked_ref(ref), ctx)))

    def plain(self, ref: MessageRef, ctx: RefContext) -> str:
        """Render a reference as plain text, the same words as :meth:`markup`."""
        return _plain(self._ref_nodes(_checked_ref(ref), ctx))

    def dec(self, value: float, decimals: builtins.int, signed: bool = False) -> str:
        return format_decimal(value, decimals, self.language, signed=signed)

    def pct(self, ratio: float, decimals: builtins.int = 0) -> str:
        """Format a ratio (0.5 is 50) through ``common.percent``."""
        if type(ratio) is not float and type(ratio) is not builtins.int:
            raise TypeError("percentage ratio must be a number")
        value = self.dec(ratio * 100, decimals)
        return _plain(self._fill("common.percent", None, {"value": value}))

    def clock(self, minute: builtins.int, days: builtins.int) -> str:
        """Format an absolute minute: ``HH:MM`` in one-day runs, else ``common.day_clock``."""
        return _plain(self._clock_nodes(minute, days))

    def duration(self, seconds: builtins.int) -> str:
        """Format whole seconds: ``common.seconds`` below a minute, else minutes and seconds."""
        if type(seconds) is not builtins.int:
            raise TypeError("duration must be whole seconds")
        if seconds < 0:
            raise ValueError("duration must not be negative")
        if seconds < 60:
            return self.text("common.seconds", count=seconds)
        minutes, rest = divmod(seconds, 60)
        return self.text("common.minutes_seconds", minutes=minutes, seconds=rest)

    # Rendering internals -------------------------------------------------------------

    def _template(self, key: str, count: builtins.int | None) -> str:
        message = self.message(key)
        if isinstance(message, PluralMessage):
            if count is None:
                raise CatalogError(f"message {key!r} is plural and needs a count")
            return message.one if plural_category(self.language, count) == "one" else message.other
        return message

    def _fill(
        self,
        key: str,
        count: builtins.int | None,
        values: Mapping[str, _Node],
    ) -> tuple[_Node, ...]:
        nodes: list[_Node] = []
        for index, part in enumerate(_SPLIT.split(self._template(key, count))):
            if index % 2 == 0:
                if part:
                    nodes.append(part)
            elif part in values:
                nodes.append(values[part])
            elif part == "count" and count is not None:
                nodes.append(_Isolate("ltr", (self.int(count),)))
            else:
                raise CatalogError(f"message {key!r} is missing its {part!r} parameter")
        return tuple(nodes)

    def _value_nodes(self, params: Mapping[str, object]) -> dict[str, _Node]:
        return {name: self._value_node(value) for name, value in params.items()}

    def _value_node(self, value: object) -> _Node:
        if isinstance(value, Markup):
            return _Raw(value)
        if isinstance(value, str):
            return _Isolate("auto", (value,))
        if type(value) is builtins.int:
            return _Isolate("ltr", (self.int(value),))
        raise TypeError("message parameters must be text, whole numbers or markup")

    def _ref_nodes(self, ref: MessageRef, ctx: RefContext) -> tuple[_Node, ...]:
        params = ref.params or {}
        values = {name: self._param_node(param, ctx) for name, param in params.items()}
        return self._fill(ref.key, ref.count, values)

    def _param_node(self, param: RefParam, ctx: RefContext) -> _Node:
        if isinstance(param, TextParam):
            return _Isolate("auto", (param.text,))
        if isinstance(param, IdParam):
            return _Isolate("ltr", (param.identifier,))
        if isinstance(param, IntParam):
            return _Isolate("ltr", (self.int(param.value),))
        if isinstance(param, NumParam):
            return _Isolate("ltr", (self.dec(param.num, param.decimals, bool(param.signed)),))
        if isinstance(param, PctParam):
            return _Isolate("ltr", (self.pct(param.pct, param.decimals),))
        if isinstance(param, ClockParam):
            return self._clock_node(param.clock, ctx.days)
        if isinstance(param, PersonParam):
            return self._person_node(param.person, ctx)
        if isinstance(param, ListParam):
            separator = self._template("common.list_separator", None)
            joined: list[_Node] = []
            for index, item in enumerate(param.items):
                if index:
                    joined.append(separator)
                joined.append(self._param_node(item, ctx))
            return _Isolate(self.direction, tuple(joined))
        if isinstance(param, MessageRef):
            return _Isolate(self.direction, self._ref_nodes(param, ctx))
        raise CatalogError("message parameter kind is not supported")

    def _clock_nodes(self, minute: builtins.int, days: builtins.int) -> tuple[_Node, ...]:
        if type(minute) is not builtins.int or type(days) is not builtins.int:
            raise TypeError("clock minute and day count must be integers")
        if minute < 0 or days < 1:
            raise ValueError("clock minute or day count is out of range")
        if days == 1 and minute <= MINUTES_PER_DAY:
            return (format_time_of_day(minute, self.language),)
        day, minute_of_day = divmod(minute, MINUTES_PER_DAY)
        values: dict[str, _Node] = {
            "day": _Isolate("ltr", (self.int(day + 1),)),
            "time": _Isolate("ltr", (format_time_of_day(minute_of_day, self.language),)),
        }
        return self._fill("common.day_clock", None, values)

    def _clock_node(self, minute: builtins.int, days: builtins.int) -> _Node:
        nodes = self._clock_nodes(minute, days)
        if len(nodes) == 1 and isinstance(nodes[0], str):
            return _Isolate("ltr", nodes)
        return _Isolate(self.direction, nodes)

    def _person_node(self, agent_id: str, ctx: RefContext) -> _Node:
        label = ctx.people.get(agent_id)
        if label is None:
            raise CatalogError(f"person {agent_id!r} is not in the reference context")
        if label.template_key is not None:
            name_nodes = self._fill(f"persona.{label.template_key}.name", None, {})
            name: _Node = _Isolate(self.direction, name_nodes)
        else:
            name = _Isolate("auto", (label.display_name,))
        values: dict[str, _Node] = {
            "name": name,
            "number": _Isolate("ltr", (self.int(label.number),)),
        }
        return _Isolate(self.direction, self._fill("common.person_ref", None, values))

    def int(self, value: builtins.int) -> str:
        """Format an integer with grouping and the language's digits."""
        return format_integer(value, self.language)


def _checked_ref(ref: object) -> MessageRef:
    if isinstance(ref, MessageRef):
        return ref
    if isinstance(ref, Mapping):
        return MessageRef.model_validate(dict(ref))
    raise TypeError("a message reference is required")


def _check_count(count: object) -> None:
    if count is not None and type(count) is not builtins.int:
        raise TypeError("count must be a whole number")


def _plain(nodes: Iterable[_Node]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, _Raw):
            parts.append(node.markup.striptags())
        elif isinstance(node, _Isolate):
            parts.append(_plain(node.children))
        else:
            parts.append(node)
    return "".join(parts)


def _markup(nodes: Iterable[_Node]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, _Raw):
            parts.append(str(node.markup))
        elif isinstance(node, _Isolate):
            parts.append(f'<bdi dir="{node.direction}">{_markup(node.children)}</bdi>')
        else:
            parts.append(str(escape(node)))
    return "".join(parts)


# Validation ------------------------------------------------------------------------


def _checked_key(key: object) -> str:
    if not isinstance(key, str) or len(key) > MAX_KEY_LENGTH or _KEY.fullmatch(key) is None:
        raise CatalogError("catalog key is invalid")
    return key


def _checked_message(key: str, value: object, language: Language) -> Message:
    if isinstance(value, str):
        return _checked_template(key, value, language)
    if isinstance(value, Mapping):
        if set(value) != {"one", "other"}:
            raise CatalogError(f"plural message {key!r} must have exactly one and other")
        one = _checked_template(key, value["one"], language)
        other = _checked_template(key, value["other"], language)
        if not placeholders(one) <= placeholders(other):
            raise CatalogError(f"plural message {key!r} uses a placeholder only in its one form")
        return PluralMessage(one, other)
    raise CatalogError(f"message {key!r} must be a string or a plural object")


def _checked_template(key: str, template: object, language: Language) -> str:
    if not isinstance(template, str) or not 1 <= len(template) <= MAX_MESSAGE_LENGTH:
        raise CatalogError(f"message {key!r} must be 1-{MAX_MESSAGE_LENGTH} characters")
    residue = PLACEHOLDER_PATTERN.sub("", template)
    if "{" in residue or "}" in residue:
        raise CatalogError(f"message {key!r} has a malformed placeholder")
    if _REFUSED_CHARACTER.search(template) is not None:
        raise CatalogError(f"message {key!r} contains a refused character")
    lowered = template.lower()
    if any(fragment in lowered for fragment in _REFUSED_SUBSTRINGS):
        raise CatalogError(f"message {key!r} contains a web address")
    if language == "fa" and _PERSIAN_REFUSED.search(template) is not None:
        raise CatalogError(f"message {key!r} uses Arabic yeh or kaf instead of Persian letters")
    return template


def catalog_parity_problems(reference: Catalog, candidate: Catalog) -> list[str]:
    """Describe every structural difference of ``candidate`` from ``reference``.

    Both catalogs need the same keys and the same kind per key. A string message needs
    the same placeholders. A plural ``other`` form needs the same placeholders; a
    ``one`` form may use any of them, because Persian also uses ``one`` for zero and
    must then show ``{count}`` where English says "once".
    """
    problems: list[str] = []
    reference_keys = reference.message_keys()
    candidate_keys = candidate.message_keys()
    problems.extend(f"missing key {key}" for key in sorted(reference_keys - candidate_keys))
    problems.extend(f"extra key {key}" for key in sorted(candidate_keys - reference_keys))
    for key in sorted(reference_keys & candidate_keys):
        expected = reference.message(key)
        actual = candidate.message(key)
        if isinstance(expected, PluralMessage) != isinstance(actual, PluralMessage):
            problems.append(f"key {key} differs in kind")
        elif isinstance(expected, PluralMessage) and isinstance(actual, PluralMessage):
            if placeholders(expected.other) != placeholders(actual.other):
                problems.append(f"key {key} other form differs in placeholders")
        elif message_placeholders(expected) != message_placeholders(actual):
            problems.append(f"key {key} differs in placeholders")
    return problems


# Packaged catalogs -----------------------------------------------------------------


def _checked_language(language: object) -> Language:
    for supported in LANGUAGES:
        if language == supported and type(language) is str:
            return supported
    raise ValueError("unsupported guide language")


@functools.cache
def catalog_bytes(language: Language) -> bytes:
    """Return the exact packaged catalog bytes the browser receives."""
    checked = _checked_language(language)
    resource = files("adlife.i18n").joinpath("catalogs", f"{checked}.json")
    try:
        with resource.open("rb") as source:
            data = source.read(MAX_CATALOG_BYTES + 1)
    except OSError:
        raise CatalogError("packaged catalog is unavailable") from None
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("packaged catalog exceeds its size limit")
    return data


def parse_catalog_messages(data: bytes, language: Language) -> dict[str, object]:
    """Strictly decode one catalog document, check its envelope and return its messages."""
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("catalog exceeds its size limit")
    if data.startswith(_UTF8_BOM):
        raise CatalogError("catalog must not start with a byte order mark")
    try:
        text = data.decode("utf-8", errors="strict")
        document = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_refuse)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise CatalogError("catalog is not valid UTF-8 JSON") from None
    if not isinstance(document, dict) or set(document) != _ENVELOPE_KEYS:
        raise CatalogError("catalog envelope is invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise CatalogError("catalog schema version is unsupported")
    if document["language"] != language or document["direction"] != text_direction(language):
        raise CatalogError("catalog language or direction does not match")
    messages = document["messages"]
    if not isinstance(messages, dict):
        raise CatalogError("catalog messages must be an object")
    return messages


@functools.cache
def load_catalog(language: Language) -> Catalog:
    """Load and validate one packaged catalog; translations must match English."""
    checked = _checked_language(language)
    messages = parse_catalog_messages(catalog_bytes(checked), checked)
    catalog = Catalog.from_mapping(checked, messages)
    if checked != "en":
        problems = catalog_parity_problems(load_catalog("en"), catalog)
        if problems:
            raise CatalogError(f"{checked} catalog differs from en: {problems[0]}")
    return catalog


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("catalog contains a duplicate key")
        result[key] = value
    return result


def _refuse(_value: str) -> object:
    raise CatalogError("catalog contains a non-finite number")


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "MAX_CATALOG_BYTES",
    "MAX_MESSAGE_LENGTH",
    "PLACEHOLDER_PATTERN",
    "Catalog",
    "CatalogError",
    "Message",
    "PluralMessage",
    "catalog_bytes",
    "catalog_parity_problems",
    "load_catalog",
    "message_placeholders",
    "parse_catalog_messages",
    "placeholders",
]
