"""Canonical serialization for everything this project writes to disk.

Specification section 22 names "event serialization" as a reusable core module, so it
lives here rather than inside whichever adapter happens to need it first. Two adapters do
need it - the SQLite store and the JSONL export - and if they disagreed about a single
byte the artifacts of one run would stop matching each other.

The canonical form is exact:

* object KEYS are sorted, so a mapping written by two processes is the same text;
* array ELEMENTS are left alone, because ``caused_by_event_ids`` is a causal record and
  sorting it would rewrite what happened;
* separators carry no padding, so the text of one event is one line;
* ``ensure_ascii`` is off, so Persian text is stored as the characters it is rather than
  as escape sequences;
* non-finite numbers are refused, matching the domain models, which already refuse them.

Every frozenset field in this repository carries a ``when_used="json"`` serializer that
sorts it, so ``model_dump(mode="json")`` is already order-stable and sorting keys on top
of it makes the whole document independent of dictionary and set iteration order.

A SERIALIZER CAN REFUSE, AND ITS REFUSAL IS NAMED HERE. ``model_dump(mode="json")`` and
``json.dumps`` both raise a bare ``ValueError`` on a document they will not render - a
nesting depth past pydantic-core's recursion guard is the case that reached this
repository. A bare ``ValueError`` from a writer is outside every family the ports publish,
so it is translated into :class:`DocumentNotSerialisable` here, once, and every caller
translates that one type into its own family. The domain bound in
:data:`~adlife.core.domain.json_values.MAX_JSON_DEPTH` means a validated document never
reaches this path; this is the answer for a document built around the model.

The screen at the bottom is the other half of writing to disk: what may be persisted at
all. It reuses the published rules from :mod:`adlife.core.domain.person` - the narrow
campaign-copy screen and the structural labelled-member walk - and never introduces a
second redactor. It is defense in depth on a best-effort basis, exactly as those rules
are, and it reports only WHERE it objected, never the text it objected to: naming the
text would put it into a traceback, which is the leak this screen exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from adlife.core.domain.events import DomainEvent
from adlife.core.domain.json_values import MAX_JSON_DEPTH, thaw_json_mapping
from adlife.core.domain.person import (
    contains_labelled_secret_member,
    contains_secret_or_email_text,
)

CANONICAL_SEPARATORS = (",", ":")
"""No padding: one document is one line."""

MAX_EVENT_LINE_CHARS = 1_048_576
"""An export line above this is refused unread; a single line is one event, not a file."""


class EventLineTooLong(ValueError):
    """Raised when a document claiming to be one event is above the line bound.

    This is a DISTINCT type rather than a plain ``ValueError`` so that a reader can say
    "too large" instead of "not an event". The two are different artifacts: a document
    over the bound may be a perfectly well-formed event that this build will not
    materialise, and reporting it as malformed sends the reader looking for the wrong
    damage. It also keeps the bound itself observable, so a test can tell the bound from
    its absence.
    """


class DocumentNotSerialisable(ValueError):
    """Raised when a serializer will not render a document this repository built.

    It is a DISTINCT type, and it is a ``ValueError`` so that the existing ``except
    ValueError`` readers keep working. Every writer translates it into the family its own
    port publishes: a bare ``ValueError`` escaping a writer reaches the command line as an
    unexpected defect rather than as a refused tick or a refused publication.

    The message names nothing about the document. The thing that was refused was built
    from campaign copy, provider paraphrase or a stored blob, and pydantic RENDERS what it
    rejects - putting it into a message or a ``__cause__`` is the leak the screens below
    exist to prevent.
    """


def _refuse_overdeep(payload: object) -> None:
    """Refuse a document nesting deeper than the domain's own shape bound.

    The depth bound lives in :mod:`adlife.core.domain.json_values`, but a document built
    through ``model_construct`` bypasses validation, so the serializer is the last door
    it passes through and must enforce the bound itself. The check is EXPLICIT rather
    than delegated to pydantic-core's serializer, whose refusal is adaptive to the C
    stack it runs on: a small-stack interpreter refuses a document that a large-stack
    one renders, which would make this contract platform-dependent. The walk is
    iterative, so checking never raises the recursion error it guards against.
    """
    pending: list[tuple[object, int]] = [(payload, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, Mapping):
            if depth >= MAX_JSON_DEPTH:
                raise DocumentNotSerialisable("a document could not be rendered as JSON")
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, (list, tuple)):
            if depth >= MAX_JSON_DEPTH:
                raise DocumentNotSerialisable("a document could not be rendered as JSON")
            pending.extend((item, depth + 1) for item in node)


def json_document(value: BaseModel) -> dict[str, object]:
    """``model_dump(mode="json")``, with the serializer's own refusal given a type."""
    try:
        dumped = value.model_dump(mode="json")
    except (ValueError, RecursionError):
        raise DocumentNotSerialisable(
            f"a {type(value).__name__} document could not be rendered as JSON"
        ) from None
    _refuse_overdeep(dumped)
    return dumped


def canonical_json(value: BaseModel | Mapping[str, object]) -> str:
    """Render one document in the canonical form described in the module docstring."""
    if isinstance(value, BaseModel):
        payload: object = json_document(value)
    elif isinstance(value, Mapping):
        payload = thaw_json_mapping(value)
        _refuse_overdeep(payload)
    else:
        raise TypeError("canonical_json needs a pydantic model or a mapping")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=CANONICAL_SEPARATORS,
        )
    except (ValueError, RecursionError):
        raise DocumentNotSerialisable("a document could not be rendered as JSON") from None


def canonical_event_line(event: DomainEvent) -> str:
    """One event as one canonical line, without its newline."""
    if not isinstance(event, DomainEvent):
        raise TypeError("canonical_event_line needs a DomainEvent")
    return canonical_json(event)


def parse_event_line(line: str) -> DomainEvent:
    """Read one canonical line back as the event that produced it.

    The length bound is checked before pydantic sees the text, so a corrupted export
    cannot make a reader materialise an arbitrarily large document.
    """
    if not isinstance(line, str):
        raise TypeError("parse_event_line needs a string")
    if len(line) > MAX_EVENT_LINE_CHARS:
        raise EventLineTooLong(f"event line exceeds {MAX_EVENT_LINE_CHARS} characters")
    return DomainEvent.model_validate_json(line)


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def persisted_model_objection(value: BaseModel, *, label: str) -> str | None:
    """:func:`persisted_text_objection` over a MODEL, rendered once through the one door.

    Every writer screened a model by calling ``model_dump(mode="json")`` itself, which is
    the call that raises a bare ``ValueError`` on a document the serializer refuses. There
    is one rendering path now, and it raises :class:`DocumentNotSerialisable`.
    """
    return persisted_text_objection(json_document(value), label=label)


def persisted_text_objection(value: object, *, label: str) -> str | None:
    """Name the place a document carries text that must not be written, or return None.

    WHAT THIS DOES AND DOES NOT CLAIM. It applies the two rules this repository already
    publishes for text that a person may write and a machine may store: the narrow
    campaign-copy screen, which knows every credential label, vendor key shape and
    contact address the repository can name, and the structural rule that refuses a
    member whose KEY is a credential label and whose value carries text. It is the same
    screen the campaign copy passes, so it does not refuse the advertising text this
    simulator exists to carry.

    It is NOT a guarantee that a persisted artifact carries no credential. An opaque
    high-entropy token under an unlabelled field is indistinguishable from an order
    number, and the broad raw-provider-body screen is deliberately not used here because
    it refuses ordinary advertising copy ("Cookie lovers unite: ..."). The control that
    actually keeps a provider body out of an artifact is upstream: a raw body is redacted
    by ``redact_provider_body`` before it can become any message at all.
    """
    if contains_labelled_secret_member(value):
        return f"{label} carries a member named for a credential"
    for text in _walk_strings(value):
        if contains_secret_or_email_text(text):
            return f"{label} carries text screened as a credential or a contact address"
    return None


__all__ = [
    "CANONICAL_SEPARATORS",
    "MAX_EVENT_LINE_CHARS",
    "DocumentNotSerialisable",
    "EventLineTooLong",
    "canonical_event_line",
    "canonical_json",
    "json_document",
    "parse_event_line",
    "persisted_model_objection",
    "persisted_text_objection",
]
