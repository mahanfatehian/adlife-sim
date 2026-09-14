"""The content-addressed cognition cache.

One JSON record per key lives beneath the run cache directory. Keys are SHA-256 digests
of the documented material, so a record can never be addressed by a path fragment.

A credential cannot reach a KEY, because no key material carries one: there is no
credential field on `ProviderMetadata` and `normalize_base_url` strips the userinfo,
query and fragment a URL could smuggle one through. The other channel is the raw provider
body, and that one is screened rather than structurally closed: `CognitionRecord` runs
`redact_provider_body` over it, which removes labelled secrets and value-shaped ones and
then refuses to store any body the repository's own detector still calls a secret. Every
other field on a record is validated text that is rejected outright if it carries one.
"""

from __future__ import annotations

import os
import re
from itertools import count
from pathlib import Path

from adlife.core.ports.cognition import (
    CognitionError,
    CognitionRecord,
    CognitionRequest,
    ProviderMetadata,
)
from adlife.core.simulation.engine import canonical_sha256

CACHE_KEY_VERSION = "cognition-cache-v1"
"""Bump this to invalidate every cached record when the key recipe changes."""

_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TEMPORARY_SUFFIXES = count()


class CacheMiss(CognitionError):
    """Raised when replay finds no record for a key. It names the exact key."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no cognition record is cached for key {key}")
        self.key = key


class CorruptCacheRecord(CognitionError):
    """Raised when a stored record cannot be trusted; it is never silently ignored."""


class CognitionCache:
    """Read and write cognition records addressed by their content."""

    __slots__ = ("_directory",)

    def __init__(self, directory: Path) -> None:
        if not isinstance(directory, Path):
            raise TypeError("directory must be a Path")
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def make_key(request: CognitionRequest, provider_metadata: ProviderMetadata) -> str:
        """Digest everything that can change a provider's answer.

        The material is the documented list: provider kind, the credential-free base URL,
        the model identifier and optional digest, the sampling settings, the prompt
        version and prompt digest, and the canonical cognition request JSON. The creative
        digest is a field of that request, so it is covered exactly once.
        """
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        if not isinstance(provider_metadata, ProviderMetadata):
            raise TypeError("provider_metadata must be a ProviderMetadata")
        sampling = provider_metadata.sampling
        material: dict[str, object] = {
            "base_url": provider_metadata.base_url or "",
            "cache_version": CACHE_KEY_VERSION,
            "model_digest": provider_metadata.model_digest or "",
            "model_id": provider_metadata.model_id,
            "prompt_sha256": provider_metadata.prompt_sha256,
            "prompt_version": request.prompt_version,
            "provider_kind": provider_metadata.kind,
            "request_sha256": canonical_sha256(request),
            "sampling": {
                "max_output_tokens": sampling.max_output_tokens,
                "seed": sampling.seed,
                "temperature": sampling.temperature,
                "top_p": sampling.top_p,
            },
        }
        return canonical_sha256(material)

    def path_for(self, key: str) -> Path:
        if not isinstance(key, str) or _KEY_PATTERN.match(key) is None:
            raise ValueError(f"cache key must be a sha-256 hex digest: {key!r}")
        return self._directory / f"{key}.json"

    def get(self, key: str) -> CognitionRecord | None:
        """Load one record and re-derive its key from its own content.

        The stored ``key`` field is the record's own claim about itself, so it is checked
        against the requested key AND against the digest of the request and provider
        metadata the record carries. A hand-edited or corrupted file whose ``key`` still
        matches its filename is therefore refused rather than served.
        """
        path = self.path_for(key)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            record = CognitionRecord.model_validate_json(text)
        except ValueError as error:
            raise CorruptCacheRecord(
                f"cognition cache record {key} is not a valid record"
            ) from error
        if record.key != key or self.make_key(record.request, record.provider_metadata) != key:
            raise CorruptCacheRecord(
                f"cognition cache record {key} does not address its own content"
            )
        return record

    def put(self, key: str, record: CognitionRecord) -> None:
        """Store one record atomically: write a sibling, then replace the final path."""
        path = self.path_for(key)
        if not isinstance(record, CognitionRecord):
            raise TypeError("record must be a CognitionRecord")
        derived = self.make_key(record.request, record.provider_metadata)
        if derived != key or record.key != key:
            raise ValueError(
                f"cognition record key {record.key} does not address its own request "
                f"under {key}; expected {derived}"
            )
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = self._directory / f"{key}.{os.getpid()}.{next(_TEMPORARY_SUFFIXES)}.tmp"
        try:
            temporary.write_text(record.model_dump_json(), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["CACHE_KEY_VERSION", "CacheMiss", "CognitionCache", "CorruptCacheRecord"]
