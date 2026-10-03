"""Portable identifier rules shared by domain documents and artifact ports."""

from __future__ import annotations

import re
from typing import Literal

from adlife.core.domain.person import contains_secret_or_email_text

PORTABLE_RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

WINDOWS_RESERVED_RUN_IDS: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{digit}" for digit in range(1, 10))}
    | {f"lpt{digit}" for digit in range(1, 10)}
)

PortableRunIdentifierFailure = Literal["shape", "reserved", "credential"]


class PortableRunIdentifierError(ValueError):
    """A run/study identifier is not safe to persist on every supported platform."""

    def __init__(self, reason: PortableRunIdentifierFailure) -> None:
        self.reason = reason
        messages = {
            "shape": "run identifier does not use the portable identifier grammar",
            "reserved": "run identifier is reserved on Windows",
            "credential": "run identifier carries credential-shaped text",
        }
        super().__init__(messages[reason])


def validate_portable_run_identifier(value: object) -> str:
    """Return one safe portable ID without ever reflecting a refused value."""
    if isinstance(value, str) and contains_secret_or_email_text(value):
        # Screen before reporting a shape failure: emails and labelled secrets often use
        # punctuation excluded by the portable grammar and must never reach an echoing
        # adapter diagnostic through that earlier branch.
        raise PortableRunIdentifierError("credential")
    if not isinstance(value, str) or PORTABLE_RUN_ID_PATTERN.fullmatch(value) is None:
        raise PortableRunIdentifierError("shape")
    if value in WINDOWS_RESERVED_RUN_IDS:
        raise PortableRunIdentifierError("reserved")
    return value


__all__ = [
    "PORTABLE_RUN_ID_PATTERN",
    "WINDOWS_RESERVED_RUN_IDS",
    "PortableRunIdentifierError",
    "validate_portable_run_identifier",
]
