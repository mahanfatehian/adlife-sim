"""Locale formatting shared by the guide's Python and JavaScript renderers.

One algorithm, two implementations: ``static/guide/guide-i18n.js`` mirrors every
function here, and ``tests/unit/i18n/format_cases.json`` pins both implementations to the
same expected strings.

Decimals round half away from zero on the exact binary value of the float, which is
what ``Math.abs(x).toFixed(d)`` does in JavaScript; the two agree for every magnitude
below 1e21, so larger values are refused rather than formatted differently. Integers are
limited to JavaScript's safe range for the same reason. A rounded zero never carries a
sign; negative values use U+2212 MINUS SIGN and ``+`` appears only when asked for.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final, Literal

Language = Literal["en", "de", "fa"]
TextDirection = Literal["ltr", "rtl"]
PluralCategory = Literal["one", "other"]

LANGUAGES: Final[tuple[Language, ...]] = ("en", "de", "fa")
DEFAULT_LANGUAGE: Final[Language] = "en"

MINUS_SIGN: Final = "\u2212"
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_DECIMAL_MAGNITUDE: Final = 1e21
MAX_DECIMALS: Final = 6
MINUTES_PER_DAY: Final = 1440

# (decimal separator, grouping separator) per language.
_SEPARATORS: Final[dict[Language, tuple[str, str]]] = {
    "en": (".", ","),
    "de": (",", "."),
    "fa": ("\u066b", "\u066c"),
}
_PERSIAN_DIGITS: Final = str.maketrans("0123456789", "".join(chr(0x06F0 + i) for i in range(10)))
_DECIMAL_CONTEXT: Final = Context(prec=64)


def parse_language(value: str | None) -> Language:
    """Return the guide language for a ``lang`` query value; ``None`` means English."""
    if value is None:
        return DEFAULT_LANGUAGE
    if value == "en":
        return "en"
    if value == "de":
        return "de"
    if value == "fa":
        return "fa"
    raise ValueError("unsupported guide language")


def text_direction(language: Language) -> TextDirection:
    """Return the base text direction of a guide language."""
    _require_language(language)
    return "rtl" if language == "fa" else "ltr"


def plural_category(language: Language, count: int) -> PluralCategory:
    """Select the CLDR plural form; Persian treats 0 like 1 ("i = 0 or n = 1")."""
    _require_language(language)
    _require_integer(count, label="plural count")
    if count < 0:
        raise ValueError("plural count must not be negative")
    if language == "fa":
        return "one" if count in (0, 1) else "other"
    return "one" if count == 1 else "other"


def localize_digits(text: str, language: Language) -> str:
    """Replace ASCII digits with the language's digits (Persian uses U+06F0-U+06F9)."""
    _require_language(language)
    return text.translate(_PERSIAN_DIGITS) if language == "fa" else text


def format_integer(value: int, language: Language) -> str:
    """Format a safe integer with grouping separators and localized digits."""
    _require_language(language)
    _require_integer(value, label="integer")
    if abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("integer is outside the shared formatting range")
    grouping = _SEPARATORS[language][1]
    body = localize_digits(_group(str(abs(value)), grouping), language)
    return (MINUS_SIGN if value < 0 else "") + body


def format_decimal(
    value: float,
    decimals: int,
    language: Language,
    *,
    signed: bool = False,
) -> str:
    """Format ``value`` with exactly ``decimals`` fraction digits.

    Rounding is half away from zero on the exact binary value, identical to
    ``Math.abs(x).toFixed(decimals)`` in JavaScript.
    """
    _require_language(language)
    if type(value) is not float and type(value) is not int:
        raise TypeError("decimal value must be a number")
    if not math.isfinite(value) or abs(value) >= MAX_DECIMAL_MAGNITUDE:
        raise ValueError("decimal value is outside the shared formatting range")
    _require_integer(decimals, label="decimal places")
    if not 0 <= decimals <= MAX_DECIMALS:
        raise ValueError("decimal places are outside the supported range")
    if type(signed) is not bool:
        raise TypeError("signed must be a boolean")
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(abs(value)).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
        context=_DECIMAL_CONTEXT,
    )
    text = format(rounded, "f")
    integer_part, _separator, fraction = text.partition(".")
    decimal_separator, grouping = _SEPARATORS[language]
    body = _group(integer_part, grouping)
    if fraction:
        body = body + decimal_separator + fraction
    if rounded.is_zero():
        sign = ""
    elif value < 0:
        sign = MINUS_SIGN
    else:
        sign = "+" if signed else ""
    return sign + localize_digits(body, language)


def format_time_of_day(minute_of_day: int, language: Language) -> str:
    """Format a minute of the day, 0 through 1440 inclusive, as ``HH:MM``."""
    _require_language(language)
    _require_integer(minute_of_day, label="minute of day")
    if not 0 <= minute_of_day <= MINUTES_PER_DAY:
        raise ValueError("minute of day is outside 00:00 through 24:00")
    hours, minutes = divmod(minute_of_day, 60)
    return localize_digits(f"{hours:02d}:{minutes:02d}", language)


def _group(digits: str, separator: str) -> str:
    if len(digits) < 4:
        return digits
    head = len(digits) % 3 or 3
    groups = [digits[:head]]
    groups.extend(digits[index : index + 3] for index in range(head, len(digits), 3))
    return separator.join(groups)


def _require_language(language: object) -> None:
    if language not in LANGUAGES or type(language) is not str:
        raise ValueError("unsupported guide language")


def _require_integer(value: object, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
