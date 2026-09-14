"""Offline cognition adapters: rules, mock, content-addressed cache, and replay."""

from adlife.adapters.cognition.cache import (
    CACHE_KEY_VERSION,
    CacheMiss,
    CognitionCache,
    CorruptCacheRecord,
)
from adlife.adapters.cognition.mock import (
    MOCK_FIXTURE_COUNT,
    MOCK_MODEL_ID,
    MockCognitionProvider,
)
from adlife.adapters.cognition.replay import ReplayCognitionProvider
from adlife.adapters.cognition.rules import (
    RULE_MODEL_ID,
    MismatchedRuleResponse,
    RuleCognitionInputs,
    RuleCognitionProvider,
    UnknownCognitionRequest,
    rule_cognition_result,
    rule_emotion,
)

__all__ = [
    "CACHE_KEY_VERSION",
    "MOCK_FIXTURE_COUNT",
    "MOCK_MODEL_ID",
    "RULE_MODEL_ID",
    "CacheMiss",
    "CognitionCache",
    "CorruptCacheRecord",
    "MismatchedRuleResponse",
    "MockCognitionProvider",
    "ReplayCognitionProvider",
    "RuleCognitionInputs",
    "RuleCognitionProvider",
    "UnknownCognitionRequest",
    "rule_cognition_result",
    "rule_emotion",
]
