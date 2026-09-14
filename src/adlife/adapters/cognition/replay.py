"""Replay a recorded cognition run from the cache, byte-identically and offline."""

from __future__ import annotations

from adlife.adapters.cognition.cache import CacheMiss, CognitionCache, CorruptCacheRecord
from adlife.core.ports.cognition import (
    CognitionAnswer,
    CognitionRecord,
    CognitionRequest,
    CognitionResult,
    ProviderMetadata,
    ProviderUsage,
)


class ReplayCognitionProvider:
    """Serve previously validated cognition results and nothing else.

    The provider is constructed with the metadata the run was recorded under, because the
    cache key covers the provider kind, model and sampling settings: replaying under
    different metadata is a different question and must miss rather than answer wrongly.
    It opens no socket and consults no credential.
    """

    __slots__ = ("_cache", "_metadata")

    def __init__(self, cache: CognitionCache, provider_metadata: ProviderMetadata) -> None:
        if not isinstance(cache, CognitionCache):
            raise TypeError("cache must be a CognitionCache")
        if not isinstance(provider_metadata, ProviderMetadata):
            raise TypeError("provider_metadata must be a ProviderMetadata")
        self._cache = cache
        self._metadata = provider_metadata

    def record_for(self, request: CognitionRequest) -> CognitionRecord:
        """Find the record this exact request was recorded under.

        Identity is model equality, which is the same order-insensitive comparison the
        cache key is built from: two requests whose persona or campaign mappings were
        assembled in a different key order are the same question and must replay, not
        fail. The refusal stays inside the :class:`CognitionError` hierarchy so a
        service can fall back on it rather than abort the run.
        """
        if not isinstance(request, CognitionRequest):
            raise TypeError("request must be a CognitionRequest")
        key = self._cache.make_key(request, self._metadata)
        record = self._cache.get(key)
        if record is None:
            raise CacheMiss(key)
        if record.request != request:
            raise CorruptCacheRecord(f"cached record {key} answers a different cognition request")
        return record

    async def evaluate(self, request: CognitionRequest) -> CognitionResult:
        return self.record_for(request).result

    async def answer(self, request: CognitionRequest) -> CognitionAnswer:
        """Return the recorded result and its recorded provenance from ONE read.

        The recorded ``provider_kind`` and ``fallback_reason`` are carried through
        untouched, so a run that originally fell back replays as that fallback rather
        than as an ordinary replay; only ``cache_hit`` is restamped, because serving from
        the cache is what this provider does. Reading the record once also makes the
        result and its provenance a single observation of a single file.
        """
        record = self.record_for(request)
        return CognitionAnswer(
            result=record.result,
            usage=record.usage.model_copy(update={"cache_hit": True}),
        )

    def usage_for(self, request: CognitionRequest) -> ProviderUsage:
        """Report the recorded usage, marked as the cache hit this replay is."""
        return self.record_for(request).usage.model_copy(update={"cache_hit": True})


__all__ = ["ReplayCognitionProvider"]
