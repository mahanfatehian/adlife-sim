"use strict";

// Pure, I/O-free validation and presentation for saved manifest-V10 bounded cognition.
// It never fetches, stores, schedules, or touches the DOM; every result is deeply frozen.
(() => {
  const DISCLOSURE = "synthetic-bounded-cognition-not-observed-behavior";
  const EFFECT_SCOPE = "bounded-signals-v1";
  const MODES = Object.freeze(["rules", "mock", "local", "remote"]);
  const OFFLINE_MODES = Object.freeze(["rules", "mock"]);
  const SOURCES = Object.freeze(["rules", "mock", "provider", "cache", "fallback", "budget-skipped"]);
  const APPLIED_SOURCES = Object.freeze([...SOURCES, "unselected"]);
  const NEUTRALIZATION_STATUSES = Object.freeze([
    "none", "uncertain", "safety", "rules", "fallback", "budget-skipped", "unselected",
  ]);
  const RUNTIME_REASONS = Object.freeze([
    "endpoint-resolution-refused", "authentication-refused", "timeout-exhausted",
    "rate-limit-exhausted", "server-error-exhausted", "response-too-large",
    "protocol-incompatible", "invalid-result",
  ]);
  const REASONS = Object.freeze([...RUNTIME_REASONS, "budget-skipped"]);
  const AFFECTS = Object.freeze(["annoyed", "skeptical", "neutral", "curious", "positive", "uncertain"]);
  const RELEVANCES = Object.freeze(["none", "low", "medium", "high", "uncertain"]);
  const CREDIBILITIES = Object.freeze(["low", "medium", "high", "uncertain"]);
  const SALIENCES = Object.freeze(["none", "low", "medium", "high", "uncertain"]);
  const SAFETY_FLAGS = Object.freeze(["stereotype-risk", "unsupported-inference", "unsafe-content"]);
  const CHANNELS = Object.freeze(["roadside-billboard", "mobile-feed"]);
  const ACTIVITIES = Object.freeze(["home", "commute", "work", "leisure"]);
  const PROVIDER_PROTOCOL = "openai-chat-completions-json-schema-v1";
  const MAX_USAGE_INTEGER = 2147483647;
  const MAX_RESPONSES = 520800;
  // Frozen selection-policy versions, keyed by the policy's canonical hash. A saved run is
  // checked against the version it was made with: V2 took the earliest four noticed ads per
  // person and sixty per run; V3 spreads the run's AI limit evenly over the whole run.
  const SELECTION_VERSIONS = Object.freeze({
    "547d141edb2706a972ff6d98fe26433f48bf6398ef60b4438aee27335b2563e3": Object.freeze({
      fundedLimits: Object.freeze([10, 20, 40, 60]),
      offlineLimit: 60,
      maxSelected: 60,
      attemptsPerRequest: 4,
      maxStreamBytes: 1048576,
    }),
    "2ccb4dc58bf0fa1a58d8192e00a192e4a10dffa83f2626da01caf723b191ac5a": Object.freeze({
      fundedLimits: Object.freeze([60, 120, 250, 500, 1000]),
      offlineLimit: 1000,
      maxSelected: 1000,
      attemptsPerRequest: 6,
      maxStreamBytes: 8388608,
    }),
  });

  function selectionVersion(hash) {
    return typeof hash === "string" && Object.hasOwn(SELECTION_VERSIONS, hash)
      ? SELECTION_VERSIONS[hash]
      : null;
  }

  // Frozen city-cognition V2 identities. The fallback, mock, and influence policies are
  // prompt-independent canonical documents, so their hashes stay exact here; the
  // selection policy is one of SELECTION_VERSIONS.
  // The prompt text, result schema, and causal contract are bound by their saved run
  // instead: each must be well-formed and agree across the summary, every terminal record,
  // and the frozen workbench input, so a revised prompt contract needs no browser change.
  // Every frozen prompt-contract id: prompt v2 (the AI judged the ad from outside) and the
  // persona prompt v3 (the AI answers as the person). A run is checked against its own.
  const PROMPT_CONTRACT_IDS = Object.freeze([
    "city-cognition-signal-prompt-v2",
    "city-cognition-persona-prompt-v3",
  ]);
  const FROZEN = Object.freeze({
    resultSchemaId: "city-cognition-signal-result-v2",
    fallbackPolicySha256: "2c8f7734948c1c70eaf5df54e3e20fc99625c62ff124c00596c86db86fa9cf6b",
    mockPolicySha256: "31efe5733e4bcfd4bf30294ec5e309f8e05b4312d99467311136f3e2ca1b2841",
    influencePolicyId: "city-cognition-influence-v1",
    influencePolicySha256: "9fca4386ff82c3e5247bd00b54c4a353a71eba55a2716b434d7b46c9b18cb460",
  });
  const FROZEN_POLICY_HASHES = Object.freeze([
    ["fallback_policy_sha256", FROZEN.fallbackPolicySha256],
    ["mock_policy_sha256", FROZEN.mockPolicySha256],
    ["influence_policy_sha256", FROZEN.influencePolicySha256],
  ]);
  const RUN_CONTRACT_HASH_FIELDS = Object.freeze([
    "causal_contract_sha256", "prompt_contract_sha256", "result_schema_contract_sha256",
  ]);
  // city-cognition-influence-v1 tables, in their frozen order.
  const INFLUENCE = Object.freeze({
    affect: Object.freeze([
      ["annoyed", -0.04], ["skeptical", -0.02], ["neutral", 0.0], ["curious", 0.02],
      ["positive", 0.04],
    ]),
    relevance: Object.freeze([["none", 0.5], ["low", 0.75], ["medium", 1.0], ["high", 1.15]]),
    credibility: Object.freeze([["low", 0.75], ["medium", 1.0], ["high", 1.1]]),
    salience: Object.freeze([["none", 0.5], ["low", 0.75], ["medium", 1.0], ["high", 1.15]]),
  });
  const MOCK_PREFIX = "city-cognition-mock-signal-v1\u0000";
  const MOCK_ORDERS = Object.freeze([
    Object.freeze(["annoyed", "skeptical", "neutral", "curious", "positive"]),
    Object.freeze(["none", "low", "medium", "high"]),
    Object.freeze(["low", "medium", "high"]),
    Object.freeze(["none", "low", "medium", "high"]),
  ]);
  const NEUTRAL_VECTOR = Object.freeze(["neutral", "medium", "medium", "medium"]);

  const SUMMARY_DOCUMENT_KEYS = Object.freeze([
    "schema_version", "run_id", "run_schema_version", "effect_scope",
    "cognition_input_sha256", "mode", "provider_profile_id", "provider_revision_sha256",
    "model_id", "funded_response_limit", "causal_contract_sha256", "prompt_contract_id",
    "prompt_contract_sha256", "result_schema_id", "result_schema_contract_sha256",
    "selection_policy_sha256", "fallback_policy_sha256", "mock_policy_sha256",
    "influence_policy_id", "influence_policy_sha256", "summary",
  ]);
  const SUMMARY_KEYS = Object.freeze([
    "schema_version", "effect_scope", "mode", "causal_contract_sha256", "mock_policy_sha256",
    "influence_policy_sha256", "coverage", "outcomes", "reasons", "neutralization",
    "attempt_count", "prompt_tokens", "completion_tokens", "terminal_stream_sha256",
    "terminal_stream_bytes", "terminal_record_count", "disclosure",
  ]);
  const COVERAGE_KEYS = Object.freeze([
    "schema_version", "eligible_response_count", "selected_request_count",
    "funded_request_count", "budget_skipped_count", "unselected_response_count",
    "applied_response_count",
  ]);
  const OUTCOME_KEYS = Object.freeze([
    "schema_version", "rules", "mock", "provider", "cache", "fallback", "budget_skipped",
  ]);
  const REASON_KEYS = Object.freeze([
    "schema_version", ...REASONS.map((reason) => reason.replaceAll("-", "_")),
  ]);
  const NEUTRALIZATION_KEYS = Object.freeze([
    "schema_version", "neutralized_response_count", "uncertain_result_count",
    "safety_neutralized_result_count",
  ]);
  const RECORDS_DOCUMENT_KEYS = Object.freeze([
    "schema_version", "run_id", "run_schema_version", "cognition_input_sha256", "records",
  ]);
  const TERMINAL_KEYS = Object.freeze([
    "schema_version", "selection_index", "request_id", "request_record_sha256",
    "run_input_sha256", "response_input_sha256", "scenario_sha256", "city_sha256",
    "baseline_response_id", "baseline_sha256", "notice_event_id", "opportunity_id",
    "baseline_state_update_event_id", "baseline_state_update_cause_count", "agent_id",
    "campaign_id", "placement_id", "channel", "activity", "day_index", "model_minute",
    "millisecond_within_minute", "source", "result", "attempt_count", "reason",
    "provider_profile_id", "provider_revision_sha256", "provider_profile_snapshot_sha256",
    "provider_protocol", "provider_effective_settings_sha256", "causal_contract_sha256",
    "prompt_contract_sha256", "result_schema_contract_sha256", "selection_policy_sha256",
    "fallback_policy_sha256", "mock_policy_sha256", "influence_policy_sha256",
    "prompt_tokens", "completion_tokens", "disclosure", "record_hash",
  ]);
  const RESULT_KEYS = Object.freeze([
    "schema_version", "request_id", "affect", "relevance", "credibility", "salience",
    "safety_flags", "disclosure",
  ]);
  const APPLIED_KEYS = Object.freeze([
    "schema_version", "model_id", "claim_scope", "event_type", "event_id",
    "cognition_input_sha256", "baseline", "terminal_record_hash", "unselected_marker",
    "source", "neutralization_status", "influence_policy_sha256",
    "previous_rule_state_sha256", "previous_cognition_state_sha256",
    "applied_sentiment_delta", "applied_recall_delta",
  ]);
  const BASELINE_KEYS = Object.freeze([
    "schema_version", "model_id", "claim_scope", "event_type", "event_id", "caused_by",
    "opportunity_id", "response_input_sha256", "scenario_sha256", "city_sha256",
    "campaign_id", "placement_id", "agent_id", "channel", "day_index", "model_minute",
    "millisecond_within_minute", "state_before_sha256", "prior_notices_today",
    "frequency_cap_per_agent_per_day", "interest_match", "relative_price",
    "price_sensitivity", "affordability", "novelty_seeking", "advertising_skepticism",
    "channel_recall_encoding", "impulsivity", "frequency_fatigue", "value_match",
    "sentiment_delta", "recall_delta",
  ]);
  const BASELINE_UNIT_FIELDS = Object.freeze([
    "interest_match", "price_sensitivity", "affordability", "novelty_seeking",
    "advertising_skepticism", "channel_recall_encoding", "impulsivity", "frequency_fatigue",
    "value_match",
  ]);

  const HASH = /^[0-9a-f]{64}$/;
  const REQUEST_ID = /^cog-[0-9a-f]{64}$/;
  const RUN_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
  const IDENTIFIER = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const PROFILE_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
  const AGENT_ID = /^person-[0-9]{3}$/;
  // Defense in depth: verified provider labels never carry credential-shaped text.
  const CREDENTIAL_SHAPES = Object.freeze([
    /(?:^|[^a-z0-9])(?:sk|pk|rk)-(?:live|test|proj)-?[a-z0-9][a-z0-9-]{7,}/i,
    /(?:^|[^a-z0-9])(?:sk|pk|rk)-[a-z0-9][a-z0-9-]{19,}/i,
    /(?:^|[^a-z0-9])xox[abeprs]-[a-z0-9-]{10,}/i,
    /(?:^|[^a-z0-9])xapp-[a-z0-9-]{10,}/i,
    /\bbearer\s+[a-z0-9._~+/=-]{8,}/i,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  ]);
  // The same display controls the core refuses; ordinary Persian and RTL marks stay valid.
  const UNSAFE_TEXT = /[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]/u;

  const validatedSummaries = new WeakSet();
  const validatedRecordDocuments = new WeakMap();
  const issuedIndexes = new WeakMap();

  function invalid(label) {
    throw new Error(`${label} is invalid`);
  }

  function isRecord(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function requireRecord(value, label) {
    if (!isRecord(value)) invalid(label);
    return value;
  }

  function exactKeys(value, expected, label) {
    const record = requireRecord(value, label);
    const keys = Object.keys(record);
    if (
      keys.length !== expected.length
      || Object.getOwnPropertySymbols(record).length !== 0
      || keys.some((key) => !expected.includes(key))
    ) invalid(label);
    return record;
  }

  function oneOf(value, allowed, label) {
    if (typeof value !== "string" || !allowed.includes(value)) invalid(label);
    return value;
  }

  function pattern(value, regex, label) {
    if (typeof value !== "string" || !regex.test(value)) invalid(label);
    return value;
  }

  function nullable(value, check) {
    return value === null ? null : check(value);
  }

  function integer(value, minimum, maximum, label) {
    if (!Number.isInteger(value) || value < minimum || value > maximum) invalid(label);
    return value;
  }

  function finite(value, minimum, maximum, label) {
    if (
      typeof value !== "number"
      || !Number.isFinite(value)
      || value < minimum
      || value > maximum
    ) invalid(label);
    return value;
  }

  function exactNumber(value, expected) {
    return typeof value === "number" && Object.is(value, expected);
  }

  function safeText(value, maximum, label) {
    if (
      typeof value !== "string"
      || value.trim().length === 0
      || Array.from(value).length > maximum
      || UNSAFE_TEXT.test(value)
      || CREDENTIAL_SHAPES.some((shape) => shape.test(value))
    ) invalid(label);
    return value;
  }

  // Copies only plain JSON values, defining (never assigning) keys, then freezes deeply.
  function frozenCopy(value, depth = 0) {
    if (depth > 32) invalid("Bounded cognition document");
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) invalid("Bounded cognition document");
      return value;
    }
    if (Array.isArray(value)) {
      return Object.freeze(value.map((item) => frozenCopy(item, depth + 1)));
    }
    if (!isRecord(value)) invalid("Bounded cognition document");
    const copy = {};
    for (const key of Object.keys(value)) {
      Object.defineProperty(copy, key, {
        value: frozenCopy(value[key], depth + 1),
        enumerable: true,
        writable: false,
        configurable: false,
      });
    }
    return Object.freeze(copy);
  }

  function canonicalJson(value) {
    if (value === null || typeof value === "string" || typeof value === "boolean") {
      return JSON.stringify(value);
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) invalid("Bounded cognition document");
      return JSON.stringify(value);
    }
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    if (isRecord(value)) {
      return `{${Object.keys(value).sort().map(
        (key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`,
      ).join(",")}}`;
    }
    return invalid("Bounded cognition document");
  }

  const SHA256_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function rotate(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
  }

  // Synchronous SHA-256 so validation stays pure and independent of secure-context APIs.
  function sha256Bytes(text) {
    const bytes = new TextEncoder().encode(text);
    const blocks = Math.ceil((bytes.length + 9) / 64);
    const padded = new Uint8Array(blocks * 64);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    const bitLength = bytes.length * 8;
    view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000), false);
    view.setUint32(padded.length - 4, bitLength >>> 0, false);
    const hash = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const words = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let index = 0; index < 16; index += 1) {
        words[index] = view.getUint32(offset + index * 4, false);
      }
      for (let index = 16; index < 64; index += 1) {
        const early = words[index - 15];
        const late = words[index - 2];
        const sigma0 = rotate(early, 7) ^ rotate(early, 18) ^ (early >>> 3);
        const sigma1 = rotate(late, 17) ^ rotate(late, 19) ^ (late >>> 10);
        words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
        const choice = (e & f) ^ (~e & g);
        const first = (h + sum1 + choice + SHA256_K[index] + words[index]) >>> 0;
        const sum0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const second = (sum0 + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + first) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (first + second) >>> 0;
      }
      hash[0] = (hash[0] + a) >>> 0;
      hash[1] = (hash[1] + b) >>> 0;
      hash[2] = (hash[2] + c) >>> 0;
      hash[3] = (hash[3] + d) >>> 0;
      hash[4] = (hash[4] + e) >>> 0;
      hash[5] = (hash[5] + f) >>> 0;
      hash[6] = (hash[6] + g) >>> 0;
      hash[7] = (hash[7] + h) >>> 0;
    }
    const digest = new Uint8Array(32);
    const digestView = new DataView(digest.buffer);
    hash.forEach((word, index) => digestView.setUint32(index * 4, word, false));
    return digest;
  }

  function sha256Hex(text) {
    return Array.from(sha256Bytes(text), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function vectorOf(result) {
    return [result.affect, result.relevance, result.credibility, result.salience];
  }

  function sameVector(left, right) {
    return left.length === right.length && left.every((value, index) => value === right[index]);
  }

  function isUncertain(result) {
    return vectorOf(result).includes("uncertain");
  }

  function mockVector(requestId) {
    const digest = sha256Bytes(MOCK_PREFIX + requestId);
    return MOCK_ORDERS.map((order, index) => order[digest[index] % order.length]);
  }

  function weight(table, category) {
    const row = INFLUENCE[table].find(([name]) => name === category);
    if (!row) invalid("Bounded cognition influence category");
    return row[1];
  }

  // Python's math.fsum of two doubles is their correctly rounded sum and never -0.0.
  function exactPairSum(left, right) {
    const sum = left + right;
    return sum === 0 ? 0 : sum;
  }

  function mappedDeltas(baselineSentiment, baselineRecall, result) {
    const affect = weight("affect", result.affect);
    const relevance = weight("relevance", result.relevance);
    const credibility = weight("credibility", result.credibility);
    const salience = weight("salience", result.salience);
    const rawSentiment = exactPairSum(baselineSentiment, affect) * relevance * credibility;
    const rawRecall = baselineRecall * relevance * credibility * salience;
    return {
      factors: Object.freeze({ affect, relevance, credibility, salience }),
      rawSentiment,
      rawRecall,
      sentiment: Math.max(-0.2, Math.min(0.2, rawSentiment)),
      recall: Math.max(0.0, Math.min(0.3, rawRecall)),
    };
  }

  function validateResult(value, requestId) {
    const result = exactKeys(value, RESULT_KEYS, "Bounded cognition result");
    if (
      result.schema_version !== 2
      || result.request_id !== requestId
      || result.disclosure !== DISCLOSURE
    ) invalid("Bounded cognition result");
    oneOf(result.affect, AFFECTS, "Bounded cognition affect");
    oneOf(result.relevance, RELEVANCES, "Bounded cognition relevance");
    oneOf(result.credibility, CREDIBILITIES, "Bounded cognition credibility");
    oneOf(result.salience, SALIENCES, "Bounded cognition salience");
    if (
      !Array.isArray(result.safety_flags)
      || result.safety_flags.length > 3
      || new Set(result.safety_flags).size !== result.safety_flags.length
    ) invalid("Bounded cognition safety flags");
    for (const flag of result.safety_flags) oneOf(flag, SAFETY_FLAGS, "Bounded cognition flag");
    return result;
  }

  function validateCounts(value, keys, maximum, label) {
    const counts = exactKeys(value, keys, label);
    if (counts.schema_version !== 2) invalid(label);
    for (const key of keys.slice(1)) integer(counts[key], 0, maximum, label);
    return counts;
  }

  function sumCounts(counts, keys) {
    return keys.slice(1).reduce((total, key) => total + counts[key], 0);
  }

  function validateSummary(value) {
    const label = "Saved bounded cognition summary";
    const documentValue = exactKeys(value, SUMMARY_DOCUMENT_KEYS, label);
    const version = selectionVersion(documentValue.selection_policy_sha256);
    if (
      documentValue.schema_version !== 2
      || documentValue.run_schema_version !== 10
      || documentValue.effect_scope !== EFFECT_SCOPE
      || !PROMPT_CONTRACT_IDS.includes(documentValue.prompt_contract_id)
      || documentValue.result_schema_id !== FROZEN.resultSchemaId
      || documentValue.influence_policy_id !== FROZEN.influencePolicyId
      || version === null
      || FROZEN_POLICY_HASHES.some(([field, expected]) => documentValue[field] !== expected)
    ) invalid(label);
    for (const field of RUN_CONTRACT_HASH_FIELDS) pattern(documentValue[field], HASH, label);
    pattern(documentValue.run_id, RUN_ID, label);
    pattern(documentValue.cognition_input_sha256, HASH, label);
    const mode = oneOf(documentValue.mode, MODES, label);
    if (
      !Number.isInteger(documentValue.funded_response_limit)
      || !version.fundedLimits.includes(documentValue.funded_response_limit)
    ) invalid(label);
    const offline = OFFLINE_MODES.includes(mode);
    if (offline) {
      if (
        documentValue.provider_profile_id !== null
        || documentValue.provider_revision_sha256 !== null
        || documentValue.model_id !== null
        || documentValue.funded_response_limit !== version.offlineLimit
      ) invalid(label);
    } else {
      pattern(documentValue.provider_profile_id, PROFILE_ID, label);
      pattern(documentValue.provider_revision_sha256, HASH, label);
      safeText(documentValue.model_id, 120, label);
    }

    const summary = exactKeys(documentValue.summary, SUMMARY_KEYS, label);
    if (
      summary.schema_version !== 2
      || summary.effect_scope !== EFFECT_SCOPE
      || summary.mode !== mode
      || summary.causal_contract_sha256 !== documentValue.causal_contract_sha256
      || summary.mock_policy_sha256 !== FROZEN.mockPolicySha256
      || summary.influence_policy_sha256 !== FROZEN.influencePolicySha256
      || summary.disclosure !== DISCLOSURE
    ) invalid(label);
    pattern(summary.terminal_stream_sha256, HASH, label);
    integer(summary.terminal_stream_bytes, 0, version.maxStreamBytes, label);
    const maxSelected = version.maxSelected;
    const coverage = validateCounts(summary.coverage, COVERAGE_KEYS, MAX_RESPONSES, label);
    const outcomes = validateCounts(summary.outcomes, OUTCOME_KEYS, maxSelected, label);
    const reasons = validateCounts(summary.reasons, REASON_KEYS, maxSelected, label);
    const neutralization = validateCounts(
      summary.neutralization, NEUTRALIZATION_KEYS, MAX_RESPONSES, label,
    );
    const selected = integer(coverage.selected_request_count, 0, maxSelected, label);
    integer(coverage.funded_request_count, 0, maxSelected, label);
    integer(coverage.budget_skipped_count, 0, maxSelected, label);
    integer(summary.terminal_record_count, 0, maxSelected, label);
    if (
      coverage.eligible_response_count !== selected + coverage.unselected_response_count
      || selected !== coverage.funded_request_count + coverage.budget_skipped_count
      || coverage.applied_response_count !== coverage.eligible_response_count
      || coverage.funded_request_count !== Math.min(selected, documentValue.funded_response_limit)
      || sumCounts(outcomes, OUTCOME_KEYS) !== selected
      || summary.terminal_record_count !== selected
      || outcomes.budget_skipped !== coverage.budget_skipped_count
      || reasons.budget_skipped !== outcomes.budget_skipped
      || sumCounts(reasons, REASON_KEYS) - reasons.budget_skipped !== outcomes.fallback
    ) invalid(label);
    if (offline) {
      if (
        OUTCOME_KEYS.slice(1).some((key) => outcomes[key] !== (key === mode ? selected : 0))
      ) invalid(label);
    } else if (outcomes.rules !== 0 || outcomes.mock !== 0) {
      invalid(label);
    }
    const attempts = integer(
      summary.attempt_count, 0, maxSelected * version.attemptsPerRequest, label,
    );
    if (
      attempts < outcomes.provider
      || attempts > version.attemptsPerRequest * (outcomes.provider + outcomes.fallback)
    ) {
      invalid(label);
    }
    const usageCapacity = (outcomes.provider + outcomes.cache + outcomes.fallback)
      * MAX_USAGE_INTEGER;
    integer(summary.prompt_tokens, 0, usageCapacity, label);
    integer(summary.completion_tokens, 0, usageCapacity, label);
    const signalCapacity = outcomes.provider + outcomes.cache;
    const uncertain = neutralization.uncertain_result_count;
    const safety = neutralization.safety_neutralized_result_count;
    const alwaysNeutral = coverage.unselected_response_count
      + outcomes.rules + outcomes.fallback + outcomes.budget_skipped;
    if (
      uncertain > signalCapacity
      || safety > signalCapacity
      || neutralization.neutralized_response_count < alwaysNeutral + Math.max(uncertain, safety)
      || neutralization.neutralized_response_count
        > alwaysNeutral + Math.min(uncertain + safety, signalCapacity)
    ) invalid(label);
    const frozen = frozenCopy(documentValue);
    validatedSummaries.add(frozen);
    return frozen;
  }

  function validateTerminal(value, index, summaryDocument) {
    const label = "Saved bounded cognition record";
    const record = exactKeys(value, TERMINAL_KEYS, label);
    if (
      record.schema_version !== 2
      || record.selection_index !== index
      || record.disclosure !== DISCLOSURE
    ) invalid(label);
    pattern(record.request_id, REQUEST_ID, label);
    for (const field of [
      "request_record_sha256", "run_input_sha256", "response_input_sha256", "scenario_sha256",
      "city_sha256", "baseline_response_id", "baseline_sha256", "notice_event_id",
      "opportunity_id", "baseline_state_update_event_id", "record_hash",
    ]) pattern(record[field], HASH, label);
    for (const [field, expected] of FROZEN_POLICY_HASHES) {
      if (record[field] !== expected) invalid(label);
    }
    // Every terminal binds the exact contract and selection policy its run summary declares.
    for (const field of [...RUN_CONTRACT_HASH_FIELDS, "selection_policy_sha256"]) {
      if (record[field] !== summaryDocument[field]) invalid(label);
    }
    const version = selectionVersion(summaryDocument.selection_policy_sha256);
    if (version === null) invalid(label);
    integer(record.baseline_state_update_cause_count, 1, MAX_RESPONSES, label);
    pattern(record.agent_id, AGENT_ID, label);
    pattern(record.campaign_id, IDENTIFIER, label);
    pattern(record.placement_id, IDENTIFIER, label);
    oneOf(record.channel, CHANNELS, label);
    oneOf(record.activity, ACTIVITIES, label);
    const minute = integer(record.model_minute, 0, 10079, label);
    if (integer(record.day_index, 0, 6, label) !== Math.floor(minute / 1440)) invalid(label);
    integer(record.millisecond_within_minute, 0, 59999, label);
    const source = oneOf(record.source, SOURCES, label);
    const result = validateResult(record.result, record.request_id);
    integer(record.attempt_count, 0, version.attemptsPerRequest, label);
    if (record.reason !== null) oneOf(record.reason, REASONS, label);
    const providerValues = [
      nullable(record.provider_profile_id, (item) => pattern(item, PROFILE_ID, label)),
      nullable(record.provider_revision_sha256, (item) => pattern(item, HASH, label)),
      nullable(record.provider_profile_snapshot_sha256, (item) => pattern(item, HASH, label)),
      nullable(record.provider_protocol, (item) => oneOf(item, [PROVIDER_PROTOCOL], label)),
      nullable(record.provider_effective_settings_sha256, (item) => pattern(item, HASH, label)),
    ];
    const hasProvider = providerValues.every((item) => item !== null);
    if (providerValues.some((item) => item !== null) && !hasProvider) invalid(label);
    if (
      hasProvider
      && (
        record.provider_profile_id !== summaryDocument.provider_profile_id
        || record.provider_revision_sha256 !== summaryDocument.provider_revision_sha256
      )
    ) invalid(label);
    for (const field of ["prompt_tokens", "completion_tokens"]) {
      nullable(record[field], (item) => integer(item, 0, MAX_USAGE_INTEGER, label));
    }
    const hasUsage = record.prompt_tokens !== null && record.completion_tokens !== null;
    if ((record.prompt_tokens !== null || record.completion_tokens !== null) && !hasUsage) {
      invalid(label);
    }
    const attempts = record.attempt_count;
    const valid = OFFLINE_MODES.includes(source)
      ? source === summaryDocument.mode && attempts === 0 && record.reason === null
        && !hasProvider && !hasUsage
      : source === "provider"
        ? attempts >= 1 && record.reason === null && hasProvider
        : source === "cache"
          ? attempts === 0 && record.reason === null && hasProvider
          : source === "fallback"
            ? RUNTIME_REASONS.includes(record.reason) && hasProvider
            : attempts === 0 && record.reason === "budget-skipped" && hasProvider && !hasUsage;
    if (!valid) invalid(label);
    const vector = vectorOf(result);
    if (
      ["rules", "fallback", "budget-skipped"].includes(source)
      && (!sameVector(vector, NEUTRAL_VECTOR) || result.safety_flags.length !== 0)
    ) invalid(label);
    if (
      source === "mock"
      && (!sameVector(vector, mockVector(record.request_id)) || result.safety_flags.length !== 0)
    ) invalid(label);
    if (!OFFLINE_MODES.includes(summaryDocument.mode)) {
      const funded = index < summaryDocument.funded_response_limit;
      if (funded === (source === "budget-skipped")) invalid(label);
    }
    const facts = {};
    for (const key of TERMINAL_KEYS) {
      if (key !== "record_hash") facts[key] = record[key];
    }
    if (sha256Hex(canonicalJson(facts)) !== record.record_hash) invalid(label);
    return record;
  }

  function validateRecords(value, summary) {
    const label = "Saved bounded cognition records";
    if (!validatedSummaries.has(summary)) invalid(label);
    const documentValue = exactKeys(value, RECORDS_DOCUMENT_KEYS, label);
    const totals = summary.summary;
    if (
      documentValue.schema_version !== 2
      || documentValue.run_id !== summary.run_id
      || documentValue.run_schema_version !== 10
      || documentValue.cognition_input_sha256 !== summary.cognition_input_sha256
      || !Array.isArray(documentValue.records)
      || documentValue.records.length !== totals.terminal_record_count
    ) invalid(label);
    const unique = {
      request_id: new Set(),
      request_record_sha256: new Set(),
      baseline_response_id: new Set(),
      record_hash: new Set(),
    };
    const bindings = { science: new Set(), run: new Set(), provider: new Set() };
    const sources = Object.fromEntries(SOURCES.map((source) => [source, 0]));
    const reasons = Object.fromEntries(REASONS.map((reason) => [reason, 0]));
    let attempts = 0;
    let promptTokens = 0;
    let completionTokens = 0;
    let uncertain = 0;
    let safety = 0;
    let neutralized = totals.coverage.unselected_response_count;
    let stream = "";
    documentValue.records.forEach((item, index) => {
      const record = validateTerminal(item, index, summary);
      for (const [field, seen] of Object.entries(unique)) {
        if (seen.has(record[field])) invalid(label);
        seen.add(record[field]);
      }
      bindings.science.add(
        `${record.response_input_sha256}:${record.scenario_sha256}:${record.city_sha256}`,
      );
      bindings.run.add(record.run_input_sha256);
      if (record.provider_profile_id !== null) {
        bindings.provider.add(
          `${record.provider_profile_snapshot_sha256}:${record.provider_effective_settings_sha256}`,
        );
      }
      sources[record.source] += 1;
      if (record.reason !== null) reasons[record.reason] += 1;
      attempts += record.attempt_count;
      promptTokens += record.prompt_tokens || 0;
      completionTokens += record.completion_tokens || 0;
      const recordUncertain = isUncertain(record.result);
      const recordSafety = record.result.safety_flags.length !== 0;
      if (recordUncertain) uncertain += 1;
      if (recordSafety) safety += 1;
      if (
        ["rules", "fallback", "budget-skipped"].includes(record.source)
        || recordUncertain
        || recordSafety
      ) neutralized += 1;
      stream += `${canonicalJson(record)}\n`;
    });
    if (
      bindings.science.size > 1
      || bindings.run.size > 1
      || bindings.provider.size > 1
      || SOURCES.some((source) => sources[source] !== totals.outcomes[source.replaceAll("-", "_")])
      || REASONS.some((reason) => reasons[reason] !== totals.reasons[reason.replaceAll("-", "_")])
      || attempts !== totals.attempt_count
      || promptTokens !== totals.prompt_tokens
      || completionTokens !== totals.completion_tokens
      || uncertain !== totals.neutralization.uncertain_result_count
      || safety !== totals.neutralization.safety_neutralized_result_count
      || neutralized !== totals.neutralization.neutralized_response_count
      || new TextEncoder().encode(stream).length !== totals.terminal_stream_bytes
      || sha256Hex(stream) !== totals.terminal_stream_sha256
    ) invalid(label);
    const frozen = frozenCopy(documentValue);
    validatedRecordDocuments.set(frozen, summary);
    return frozen;
  }

  function indexTerminalRecords(records) {
    const summary = validatedRecordDocuments.get(records);
    if (summary === undefined) invalid("Bounded cognition record index");
    const byHash = new Map();
    const byBaseline = new Map();
    for (const record of records.records) {
      byHash.set(record.record_hash, record);
      byBaseline.set(record.baseline_response_id, record.record_hash);
    }
    const index = Object.freeze({
      runId: records.run_id,
      cognitionInputSha256: records.cognition_input_sha256,
      size: byHash.size,
      records: records.records,
      get(recordHash) {
        return typeof recordHash === "string" && byHash.has(recordHash)
          ? byHash.get(recordHash)
          : null;
      },
      has(recordHash) {
        return typeof recordHash === "string" && byHash.has(recordHash);
      },
    });
    issuedIndexes.set(index, { summary, byHash, byBaseline });
    return index;
  }

  function validateBaseline(value) {
    const label = "Applied response baseline";
    const baseline = exactKeys(value, BASELINE_KEYS, label);
    if (
      baseline.schema_version !== 1
      || baseline.model_id !== "spatial-response-v1"
      || baseline.claim_scope !== "synthetic-response-not-observed-behavior"
      || baseline.event_type !== "spatial.response"
    ) invalid(label);
    for (const field of [
      "event_id", "caused_by", "opportunity_id", "response_input_sha256", "scenario_sha256",
      "city_sha256", "state_before_sha256",
    ]) pattern(baseline[field], HASH, label);
    pattern(baseline.agent_id, AGENT_ID, label);
    pattern(baseline.campaign_id, IDENTIFIER, label);
    pattern(baseline.placement_id, IDENTIFIER, label);
    oneOf(baseline.channel, CHANNELS, label);
    const minute = integer(baseline.model_minute, 0, 10079, label);
    if (integer(baseline.day_index, 0, 6, label) !== Math.floor(minute / 1440)) invalid(label);
    integer(baseline.millisecond_within_minute, 0, 59999, label);
    const cap = integer(baseline.frequency_cap_per_agent_per_day, 1, 100, label);
    if (integer(baseline.prior_notices_today, 0, 100, label) >= cap) invalid(label);
    finite(baseline.relative_price, 0, 100, label);
    if (baseline.relative_price === 0) invalid(label);
    for (const field of BASELINE_UNIT_FIELDS) finite(baseline[field], 0, 1, label);
    finite(baseline.sentiment_delta, -0.2, 0.2, label);
    finite(baseline.recall_delta, 0, 0.3, label);
    return baseline;
  }

  function validateApplied(value, summary) {
    const label = "Applied bounded cognition response";
    const applied = exactKeys(value, APPLIED_KEYS, label);
    if (
      applied.schema_version !== 2
      || applied.model_id !== "spatial-response-v2"
      || applied.claim_scope !== DISCLOSURE
      || applied.event_type !== "spatial.applied-response"
      || applied.cognition_input_sha256 !== summary.cognition_input_sha256
      || applied.influence_policy_sha256 !== FROZEN.influencePolicySha256
    ) invalid(label);
    for (const field of [
      "event_id", "previous_rule_state_sha256", "previous_cognition_state_sha256",
    ]) pattern(applied[field], HASH, label);
    const baseline = validateBaseline(applied.baseline);
    if (applied.previous_rule_state_sha256 !== baseline.state_before_sha256) invalid(label);
    const source = oneOf(applied.source, APPLIED_SOURCES, label);
    const status = oneOf(applied.neutralization_status, NEUTRALIZATION_STATUSES, label);
    if (source === "unselected") {
      if (applied.terminal_record_hash !== null || applied.unselected_marker !== "unselected-neutral") {
        invalid(label);
      }
    } else if (applied.unselected_marker !== null) {
      invalid(label);
    } else {
      pattern(applied.terminal_record_hash, HASH, label);
    }
    const coherent = ["rules", "fallback", "budget-skipped", "unselected"].includes(source)
      ? status === source
      : source === "mock"
        ? status === "none"
        : ["none", "uncertain", "safety"].includes(status);
    if (!coherent) invalid(label);
    finite(applied.applied_sentiment_delta, -0.2, 0.2, label);
    finite(applied.applied_recall_delta, 0, 0.3, label);
    return applied;
  }

  function plainNumber(value) {
    const rounded = Number(value.toFixed(4));
    return String(rounded === 0 && value !== 0 ? Number(value.toPrecision(4)) : rounded);
  }

  function signedNumber(value) {
    const text = plainNumber(value);
    return value >= 0 ? `+${text}` : text;
  }

  function readable(value) {
    return value.replaceAll("-", " ");
  }

  const SOURCE_LABELS = Object.freeze({
    provider: "Provider signal",
    cache: "Cached provider signal",
    mock: "Deterministic mock signal",
    rules: "Rules mode",
    fallback: "Deterministic fallback",
    "budget-skipped": "Budget skipped",
    unselected: "Not selected",
  });
  // Short, text-only source badges; the page never relies on color alone.
  const SOURCE_BADGES = Object.freeze({
    provider: "Provider",
    cache: "Cache",
    mock: "Mock",
    rules: "Rules",
    fallback: "Fallback",
    "budget-skipped": "Budget skipped",
    unselected: "Not selected",
  });
  const EFFECT_BADGES = Object.freeze({
    mapped: "Changes cognition",
    identity: "Equals baseline",
    safety: "Neutralized · safety flag",
    uncertain: "Neutralized · uncertain",
    neutral: "Neutral · equals baseline",
  });
  const NEUTRAL_OUTCOME = "AdLife applies the neutral vector, so this response changes"
    + " cognition exactly as the rule baseline does.";
  const SET_ASIDE = "AdLife does not guess a replacement: it sets the whole answer aside and"
    + " applies the neutral vector, so this response changes cognition exactly as the rule"
    + " baseline does. The answer stays recorded for audit.";

  function quotedList(values) {
    return values.map((value) => `“${readable(value)}”`).join(", ");
  }

  function signalStage(source, status, terminal) {
    const chips = terminal === null
      ? []
      : [
        ["affect", "Affect", terminal.result.affect],
        ["relevance", "Relevance", terminal.result.relevance],
        ["credibility", "Credibility", terminal.result.credibility],
        ["salience", "Salience", terminal.result.salience],
      ].map(([key, title, value]) => Object.freeze({ key, title, value }));
    const flags = terminal === null ? [] : [...terminal.result.safety_flags];
    const uncertain = chips.filter((chip) => chip.value === "uncertain").map((chip) => chip.key);
    let statusLabel;
    let detail;
    if (source === "unselected") {
      statusLabel = "Neutral · not selected";
      detail = "No request was sent: this response was not among the run's selected AI"
        + ` questions. ${NEUTRAL_OUTCOME}`;
    } else if (status === "rules") {
      statusLabel = "Neutral · rules identity";
      detail = `Rules mode sends no request. ${NEUTRAL_OUTCOME}`;
    } else if (status === "fallback") {
      statusLabel = `Neutral · fallback (${readable(terminal.reason)})`;
      detail = `The provider call ended with “${readable(terminal.reason)}”, which the frozen`
        + ` fallback policy covers. ${NEUTRAL_OUTCOME}`;
    } else if (status === "budget-skipped") {
      statusLabel = "Neutral · budget skipped";
      detail = "Selected, but beyond the run's funded request limit, so no provider call was"
        + ` made. ${NEUTRAL_OUTCOME}`;
    } else if (status === "uncertain") {
      statusLabel = "Neutralized · uncertain category";
      detail = `The model answered “uncertain” for ${uncertain.join(" and ")}. ${SET_ASIDE}`;
    } else if (status === "safety") {
      statusLabel = `Neutralized · safety flag (${flags.map(readable).join(", ")})`;
      detail = `The model attached the safety flag ${quotedList(flags)} to its answer`
        + `${uncertain.length > 0 ? ` and answered “uncertain” for ${uncertain.join(" and ")}` : ""}.`
        + ` ${SET_ASIDE}`;
    } else if (source === "mock") {
      statusLabel = "Mock signal applied";
      detail = "An offline deterministic mock derived these categories from the request ID;"
        + " no model or network was involved.";
    } else if (source === "cache") {
      statusLabel = "Cached signal applied";
      detail = "The model's earlier answer to this exact request was reused from the verified"
        + " cache; no new provider call was made.";
    } else {
      statusLabel = "Signal applied";
      detail = "The model chose these four closed categories for the noticed ad. Only the"
        + " fixed mapping below can turn them into a cognition change.";
    }
    // A model or mock answer is applied, a set-aside answer is only audited, and a rules,
    // fallback, or budget-skipped record holds the fixed neutral vector, applied as identity.
    const chipsKind = chips.length === 0
      ? null
      : status === "none"
        ? "applied"
        : ["safety", "uncertain"].includes(status) ? "set-aside" : "neutral";
    return Object.freeze({
      key: "signal",
      title: SOURCE_LABELS[source],
      status: statusLabel,
      detail,
      chips: Object.freeze(chips),
      chipsKind,
      chipsNote: chipsKind === "set-aside"
        ? "Model answer recorded for audit · not applied to cognition"
        : chipsKind === "neutral" ? "Fixed neutral vector · no model answer" : null,
      safetyFlags: Object.freeze(flags),
      uncertainDimensions: Object.freeze(uncertain),
      reason: terminal === null ? null : terminal.reason,
    });
  }

  function mappingFormula(baseline, mapped) {
    const { affect, relevance, credibility, salience } = mapped.factors;
    const clampNote = (raw, applied, bound) => (
      raw === applied ? "" : ` → clamped to the ${signedNumber(bound)} bound`
    );
    return Object.freeze([
      `Sentiment Δ = (baseline ${signedNumber(baseline.sentiment_delta)}`
        + ` ${affect < 0 ? "−" : "+"} affect ${plainNumber(Math.abs(affect))})`
        + ` × relevance ${relevance.toFixed(2)} × credibility ${credibility.toFixed(2)}`
        + ` = ${signedNumber(mapped.rawSentiment)}`
        + clampNote(mapped.rawSentiment, mapped.sentiment, mapped.sentiment),
      `Recall Δ = baseline ${signedNumber(baseline.recall_delta)}`
        + ` × relevance ${relevance.toFixed(2)} × credibility ${credibility.toFixed(2)}`
        + ` × salience ${salience.toFixed(2)} = ${signedNumber(mapped.rawRecall)}`
        + clampNote(mapped.rawRecall, mapped.recall, mapped.recall),
    ]);
  }

  function presentationForAppliedResponse(value, index) {
    const label = "Applied bounded cognition binding";
    const issued = issuedIndexes.get(index);
    if (issued === undefined) invalid(label);
    const applied = validateApplied(value, issued.summary);
    const baseline = applied.baseline;
    const source = applied.source;
    const status = applied.neutralization_status;
    let terminal = null;
    if (source === "unselected") {
      if (issued.byBaseline.has(baseline.event_id)) invalid(label);
    } else {
      terminal = issued.byHash.get(applied.terminal_record_hash) || null;
      if (
        terminal === null
        || terminal.record_hash !== applied.terminal_record_hash
        || terminal.source !== source
        || terminal.baseline_response_id !== baseline.event_id
        || terminal.notice_event_id !== baseline.caused_by
        || terminal.opportunity_id !== baseline.opportunity_id
        || terminal.response_input_sha256 !== baseline.response_input_sha256
        || terminal.scenario_sha256 !== baseline.scenario_sha256
        || terminal.city_sha256 !== baseline.city_sha256
        || terminal.agent_id !== baseline.agent_id
        || terminal.campaign_id !== baseline.campaign_id
        || terminal.placement_id !== baseline.placement_id
        || terminal.channel !== baseline.channel
        || terminal.day_index !== baseline.day_index
        || terminal.model_minute !== baseline.model_minute
        || terminal.millisecond_within_minute !== baseline.millisecond_within_minute
        || terminal.influence_policy_sha256 !== applied.influence_policy_sha256
      ) invalid(label);
      const expectedStatus = ["rules", "fallback", "budget-skipped"].includes(source)
        ? source
        : terminal.result.safety_flags.length !== 0
          ? "safety"
          : isUncertain(terminal.result) ? "uncertain" : "none";
      if (status !== expectedStatus) invalid(label);
    }
    const identity = status !== "none" || sameVector(vectorOf(terminal.result), NEUTRAL_VECTOR);
    const mapped = identity
      ? null
      : mappedDeltas(baseline.sentiment_delta, baseline.recall_delta, terminal.result);
    const expectedSentiment = identity ? baseline.sentiment_delta : mapped.sentiment;
    const expectedRecall = identity ? baseline.recall_delta : mapped.recall;
    if (
      !exactNumber(applied.applied_sentiment_delta, expectedSentiment)
      || !exactNumber(applied.applied_recall_delta, expectedRecall)
    ) invalid(label);
    const factors = mapped === null ? null : mapped.factors;
    const mappingDetail = factors === null
      ? "Neutral vector: an exact identity over the deterministic baseline."
      : `Affect ${signedNumber(factors.affect)} · relevance ×${factors.relevance.toFixed(2)}`
        + ` · credibility ×${factors.credibility.toFixed(2)}`
        + ` · salience ×${factors.salience.toFixed(2)}, then the frozen clamps.`;
    const changed = !identity && (
      !exactNumber(applied.applied_sentiment_delta, baseline.sentiment_delta)
      || !exactNumber(applied.applied_recall_delta, baseline.recall_delta)
    );
    const effect = status === "none"
      ? (changed ? "mapped" : "identity")
      : ["safety", "uncertain"].includes(status) ? status : "neutral";
    const badges = Object.freeze([
      Object.freeze({ kind: "source", key: source, label: SOURCE_BADGES[source] }),
      Object.freeze({ kind: "effect", key: effect, label: EFFECT_BADGES[effect] }),
    ]);
    const stages = Object.freeze([
      Object.freeze({
        key: "baseline",
        title: "Deterministic baseline",
        status: "Rule-owned",
        detail: `Rule response · sentiment Δ ${signedNumber(baseline.sentiment_delta)}`
          + ` · recall Δ ${signedNumber(baseline.recall_delta)}`,
      }),
      signalStage(source, status, terminal),
      Object.freeze({
        key: "mapping",
        title: `${FROZEN.influencePolicyId} mapping`,
        status: factors === null ? "Identity" : "Fixed mapping",
        detail: mappingDetail,
        formula: mapped === null ? Object.freeze([]) : mappingFormula(baseline, mapped),
      }),
      Object.freeze({
        key: "applied",
        title: "Applied cognition delta",
        status: identity ? "Equals baseline" : "Cognition track only",
        detail: identity
          ? `Sentiment Δ ${signedNumber(applied.applied_sentiment_delta)}`
            + ` · recall Δ ${signedNumber(applied.applied_recall_delta)} · identical to the baseline`
          : `Sentiment Δ ${signedNumber(applied.applied_sentiment_delta)}`
            + ` (baseline ${signedNumber(baseline.sentiment_delta)})`
            + ` · recall Δ ${signedNumber(applied.applied_recall_delta)}`
            + ` (baseline ${signedNumber(baseline.recall_delta)})`,
      }),
      Object.freeze({
        key: "dual",
        title: "Atomic rule/cognition state update",
        status: "One group commit",
        detail: "The rule track folds the baseline; the cognition track folds the applied delta.",
      }),
    ]);
    return Object.freeze({
      appliedEventId: applied.event_id,
      baselineEventId: baseline.event_id,
      agentId: baseline.agent_id,
      campaignId: baseline.campaign_id,
      placementId: baseline.placement_id,
      channel: baseline.channel,
      dayIndex: baseline.day_index,
      modelMinute: baseline.model_minute,
      millisecondWithinMinute: baseline.millisecond_within_minute,
      source,
      neutralizationStatus: status,
      effect,
      badges,
      selected: terminal !== null,
      terminalRecordHash: terminal === null ? null : terminal.record_hash,
      selectionIndex: terminal === null ? null : terminal.selection_index,
      requestId: terminal === null ? null : terminal.request_id,
      influencePolicyId: FROZEN.influencePolicyId,
      factors,
      baselineDeltas: Object.freeze({
        sentiment: baseline.sentiment_delta,
        recall: baseline.recall_delta,
      }),
      appliedDeltas: Object.freeze({
        sentiment: applied.applied_sentiment_delta,
        recall: applied.applied_recall_delta,
      }),
      previousRuleStateSha256: applied.previous_rule_state_sha256,
      previousCognitionStateSha256: applied.previous_cognition_state_sha256,
      stages,
    });
  }

  Object.defineProperty(window, "AdLifeBoundedCognition", {
    value: Object.freeze({
      validateSummary,
      validateRecords,
      indexTerminalRecords,
      presentationForAppliedResponse,
    }),
    enumerable: false,
    writable: false,
    configurable: false,
  });
})();
