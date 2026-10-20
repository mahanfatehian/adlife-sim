"""Template globals that translate guide pages and the run report on the server.

``template_functions(catalog, ctx)`` returns plain callables, so this module needs no
Jinja import. In templates:

* ``tr(key, **params)`` renders escaped markup with every parameter in ``<bdi>``:
  text in ``dir="auto"``, whole numbers localized in ``dir="ltr"``; a ``Markup`` value
  (from ``bdi_id``, ``ref`` or ``tr``) is inserted as it is. ``count`` selects the plural
  form; a text ``count`` only fills ``{count}`` in a non-plural message.
* ``tr_text(key, **params)`` renders the same words as plain text, for attributes and
  ``<title>``.
* ``ref(r)`` / ``ref_text(r)`` render a :class:`MessageRef` (or its JSON mapping).
* ``fmt_int``, ``fmt_dec``, ``fmt_pct``, ``fmt_clock`` and ``fmt_duration`` return plain
  localized strings; wrap a number in ``bdi_id(...)`` to keep it a left-to-right island.
* ``bdi_text(s)`` isolates user or data text (``dir="auto"``); ``bdi_id(s)`` isolates an
  identifier, hash, model name or formatted number (``dir="ltr"``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from markupsafe import Markup, escape

from adlife.i18n.catalog import Catalog, CatalogError, PluralMessage
from adlife.i18n.refs import MessageRef, RefContext

_EMPTY_CONTEXT = RefContext(days=1, people={})


def template_functions(
    catalog: Catalog,
    ctx: RefContext | None = None,
) -> dict[str, Callable[..., object]]:
    """Return the translation and formatting globals for one catalog and run context."""
    if not isinstance(catalog, Catalog):
        raise TypeError("a guide catalog is required")
    context = _EMPTY_CONTEXT if ctx is None else ctx

    def split_count(key: str, params: dict[str, object]) -> int | None:
        count = params.pop("count", None)
        if count is None or type(count) is int:
            return count
        if isinstance(count, str) and not isinstance(catalog.message(key), PluralMessage):
            # A non-plural message may show caller text where it says {count}.
            params["count"] = count
            return None
        raise CatalogError(f"message {key!r} needs a whole-number count")

    def tr(key: str, /, **params: object) -> Markup:
        count = split_count(key, params)
        return catalog.format_markup(key, count, params)

    def tr_text(key: str, /, **params: object) -> str:
        count = split_count(key, params)
        return catalog.format_text(key, count, params)

    def ref(value: MessageRef | Mapping[str, object]) -> Markup:
        return catalog.markup(_as_ref(value), context)

    def ref_text(value: MessageRef | Mapping[str, object]) -> str:
        return catalog.plain(_as_ref(value), context)

    def fmt_int(value: int) -> str:
        return catalog.int(value)

    def fmt_dec(value: float, decimals: int, signed: bool = False) -> str:
        return catalog.dec(value, decimals, signed)

    def fmt_pct(ratio: float, decimals: int = 0) -> str:
        return catalog.pct(ratio, decimals)

    def fmt_clock(minute: int, days: int | None = None) -> str:
        return catalog.clock(minute, context.days if days is None else days)

    def fmt_duration(seconds: int) -> str:
        return catalog.duration(seconds)

    def bdi_text(value: str) -> Markup:
        return _isolated(value, "auto")

    def bdi_id(value: str) -> Markup:
        return _isolated(value, "ltr")

    return {
        "tr": tr,
        "tr_text": tr_text,
        "ref": ref,
        "ref_text": ref_text,
        "fmt_int": fmt_int,
        "fmt_dec": fmt_dec,
        "fmt_pct": fmt_pct,
        "fmt_clock": fmt_clock,
        "fmt_duration": fmt_duration,
        "bdi_text": bdi_text,
        "bdi_id": bdi_id,
    }


def _as_ref(value: object) -> MessageRef:
    if isinstance(value, MessageRef):
        return value
    if isinstance(value, Mapping):
        return MessageRef.model_validate(dict(value))
    raise TypeError("a message reference is required")


def _isolated(value: object, direction: str) -> Markup:
    if not isinstance(value, str):
        raise TypeError("isolated text must be a string")
    text = value.striptags() if isinstance(value, Markup) else value
    return Markup(f'<bdi dir="{direction}">{escape(text)}</bdi>')


__all__ = ["template_functions"]
