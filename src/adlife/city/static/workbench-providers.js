"use strict";

(() => {
  const HASH_PATTERN = /^[0-9a-f]{64}$/;
  const PROFILE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
  const SEED_PATTERN = /^(?:0|[1-9][0-9]{0,18})$/;
  const MAX_SEED = 9223372036854775807n;
  const MODES = Object.freeze(["rules", "mock", "local", "remote"]);
  const PROVIDER_MODES = new Set(["local", "remote"]);
  const EXPLANATION_SCOPE = "explanation-only";
  const BOUNDED_SCOPE = "bounded-signals-v1";
  const EFFECT_SCOPES = Object.freeze([EXPLANATION_SCOPE, BOUNDED_SCOPE]);
  const DEFAULT_LOCAL_ORIGIN = "local-ollama";
  const DEFAULT_LOCAL_MODEL = "qwen2.5:7b-instruct";
  const REASON_CODES = new Set([
    "endpoint-resolution-refused",
    "authentication-refused",
    "timeout-exhausted",
    "rate-limit-exhausted",
    "server-error-exhausted",
    "response-too-large",
    "protocol-incompatible",
    "invalid-result",
    "budget-skipped",
    "credential-unavailable",
    "endpoint-policy-refused",
  ]);
  const UNAVAILABLE_REASONS = new Set(["credential-unavailable", "endpoint-policy-refused"]);
  const BOUNDED_CHECK_HASHES = Object.freeze([
    "compatibility_key_sha256",
    "causal_contract_sha256",
    "prompt_contract_sha256",
    "result_schema_contract_sha256",
    "selection_policy_sha256",
    "fallback_policy_sha256",
    "mock_policy_sha256",
    "influence_policy_sha256",
  ]);
  const MODE_LABELS = Object.freeze({
    rules: "Rules",
    mock: "Mock",
    local: "Local model",
    remote: "Remote model",
  });
  const SCOPE_LABELS = Object.freeze({
    [EXPLANATION_SCOPE]: "Explanation",
    [BOUNDED_SCOPE]: "Bounded cognition",
  });
  const SCOPE_HELP = Object.freeze({
    [EXPLANATION_SCOPE]: "Text about saved results; saved as run V9.",
    [BOUNDED_SCOPE]: "Closed appraisal categories feed a separate cognition state; saved as run V10.",
  });
  const EXPLANATION_MODE_HELP = Object.freeze({
    rules: "Deterministic grounded wording; offline and reproducible.",
    mock: "Deterministic provider-shaped wording for demos and tests.",
    local: "Calls one approved loopback OpenAI-compatible endpoint.",
    remote: "Calls one approved HTTPS OpenAI-compatible endpoint.",
  });
  const BOUNDED_MODE_HELP = Object.freeze({
    rules: "Neutral signals for every selected response; offline and reproducible.",
    mock: "Deterministic content-addressed signals; offline, with no network call.",
    local: "One approved loopback OpenAI-compatible endpoint, with deterministic neutral fallback.",
    remote: "One approved HTTPS OpenAI-compatible endpoint, with deterministic neutral fallback.",
  });
  const BOUNDARY_COPY = Object.freeze({
    [EXPLANATION_SCOPE]: "Only explanatory text. A model may describe an already-computed fictional response; it cannot move agents, create or select events, change any state or purchase intention, or make purchases.",
    [BOUNDED_SCOPE]: "External AI selects a small set of synthetic appraisal categories. AdLife applies a fixed mapping. These categories may change only the displayed cognition state. They cannot move agents, create or select events, change purchase intention, or make purchases.",
  });
  const PURCHASE_OWNERSHIP = "Rules: an uncalibrated proxy score; purchases are not modeled";
  const TIMEOUT_REASON = "timeout-exhausted";

  const byId = (id) => document.getElementById(id);
  const formatCount = (value) => Number(value).toLocaleString("en-US");

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function boundedText(value, maximum) {
    return typeof value === "string" && value.length > 0 && value.length <= maximum;
  }

  function hasExactKeys(value, keys) {
    if (!isObject(value)) return false;
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length &&
      actual.every((key, index) => key === expected[index]);
  }

  function exactInteger(id, minimum, maximum) {
    const text = byId(id).value;
    if (text === "") {
      throw new ProviderDraftError(id, "Enter a whole number within the shown bounds.");
    }
    const value = Number(text);
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new ProviderDraftError(id, "Enter a whole number within the shown bounds.");
    }
    return value;
  }

  function finiteNumber(id, minimum, maximum, { exclusiveMinimum = false } = {}) {
    const text = byId(id).value;
    if (text === "") {
      throw new ProviderDraftError(id, "Enter a number within the shown bounds.");
    }
    const value = Number(text);
    const lowerBound = exclusiveMinimum ? value > minimum : value >= minimum;
    if (!Number.isFinite(value) || !lowerBound || value > maximum) {
      throw new ProviderDraftError(id, "Enter a number within the shown bounds.");
    }
    return value;
  }

  function requiredText(id, maximum) {
    const value = byId(id).value;
    if (!boundedText(value, maximum) || value.trim() !== value) {
      throw new ProviderDraftError(id, "Enter a value without leading or trailing spaces.");
    }
    return value;
  }

  function setText(id, value) {
    byId(id).textContent = String(value);
  }

  function safeMessage(error, fallback) {
    return error instanceof Error && boundedText(error.message, 500) ? error.message : fallback;
  }

  function profileKey(profile) {
    return `${profile.profile_id}/${profile.revision_sha256}`;
  }

  function assertCheck(value) {
    if (
      !hasExactKeys(value, ["status", "reason_code"]) ||
      !["compatible", "incompatible", "unavailable"].includes(value.status) ||
      !(value.reason_code === null || REASON_CODES.has(value.reason_code))
    ) {
      throw new Error("The compatibility response is incompatible.");
    }
    if (
      (value.status === "compatible") !== (value.reason_code === null) ||
      (value.status === "unavailable" && !UNAVAILABLE_REASONS.has(value.reason_code)) ||
      (value.status === "incompatible" && UNAVAILABLE_REASONS.has(value.reason_code))
    ) {
      throw new Error("The compatibility response is inconsistent.");
    }
    return value;
  }

  function assertBoundedCheck(value, profile) {
    if (
      !hasExactKeys(value, ["status", "reason_code", "effect_scope", "profile_id", "revision_sha256", ...BOUNDED_CHECK_HASHES]) ||
      value.effect_scope !== BOUNDED_SCOPE ||
      value.profile_id !== profile.profile_id ||
      value.revision_sha256 !== profile.revision_sha256 ||
      BOUNDED_CHECK_HASHES.some((name) => !HASH_PATTERN.test(value[name] || ""))
    ) {
      throw new Error("The bounded-signal compatibility response is incompatible.");
    }
    assertCheck({ status: value.status, reason_code: value.reason_code });
    return Object.freeze({ ...value });
  }

  function assertProfile(value) {
    if (
      !hasExactKeys(value, [
        "schema_version",
        "profile_id",
        "display_label",
        "mode",
        "base_url",
        "model_id",
        "protocol",
        "temperature",
        "top_p",
        "seed_policy",
        "seed",
        "max_output_tokens",
        "attempt_timeout_seconds",
        "transient_retries",
        "repair_attempts",
        "origin_approval_id",
        "revision_sha256",
        "credential_slot_id",
        "credential_available",
        "endpoint_policy_accepted",
        "last_check",
      ]) ||
      value.schema_version !== 1 ||
      !PROFILE_ID_PATTERN.test(value.profile_id || "") ||
      !boundedText(value.display_label, 80) ||
      !PROVIDER_MODES.has(value.mode) ||
      !boundedText(value.base_url, 2048) ||
      !boundedText(value.model_id, 120) ||
      value.protocol !== "openai-chat-completions-json-schema-v1" ||
      !Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2 ||
      !Number.isFinite(value.top_p) || value.top_p <= 0 || value.top_p > 1 ||
      !["omit", "fixed"].includes(value.seed_policy) ||
      !(value.seed === null || (Number.isInteger(value.seed) && value.seed >= 0)) ||
      ((value.seed_policy === "fixed") !== (value.seed !== null)) ||
      !Number.isInteger(value.max_output_tokens) || value.max_output_tokens < 1 || value.max_output_tokens > 384 ||
      !Number.isInteger(value.attempt_timeout_seconds) || value.attempt_timeout_seconds < 1 || value.attempt_timeout_seconds > 30 ||
      !Number.isInteger(value.transient_retries) || value.transient_retries < 0 || value.transient_retries > 2 ||
      !Number.isInteger(value.repair_attempts) || value.repair_attempts < 0 || value.repair_attempts > 1 ||
      !PROFILE_ID_PATTERN.test(value.origin_approval_id || "") ||
      !HASH_PATTERN.test(value.revision_sha256 || "") ||
      !HASH_PATTERN.test(value.credential_slot_id || "") ||
      typeof value.credential_available !== "boolean" ||
      typeof value.endpoint_policy_accepted !== "boolean" ||
      !(value.last_check === null || isObject(value.last_check))
    ) {
      throw new Error("The provider profile response is incompatible.");
    }
    if (value.last_check !== null) assertCheck(value.last_check);
    return value;
  }

  function assertProviderDocument(value) {
    if (
      !hasExactKeys(value, [
        "schema_version",
        "profiles",
        "origins",
        "provider_configuration",
        "oauth",
        "cognition_modes",
      ]) ||
      value.schema_version !== 1 ||
      !Array.isArray(value.profiles) ||
      !Array.isArray(value.origins) || value.origins.length > 20 ||
      value.profiles.length > 200 ||
      value.provider_configuration !== true ||
      value.oauth !== false ||
      JSON.stringify(value.cognition_modes) !== JSON.stringify(MODES)
    ) {
      throw new Error("The provider configuration response is incompatible.");
    }
    const profiles = value.profiles.map(assertProfile);
    const keys = new Set(profiles.map(profileKey));
    if (keys.size !== profiles.length) {
      throw new Error("The provider configuration contains duplicate revisions.");
    }
    const origins = value.origins.map((origin) => {
      if (
        !hasExactKeys(origin, ["approval_id", "base_url", "trust_class", "environment_variable"]) ||
        !PROFILE_ID_PATTERN.test(origin.approval_id || "") ||
        !boundedText(origin.base_url, 2048) ||
        !PROVIDER_MODES.has(origin.trust_class) ||
        !(origin.environment_variable === null || boundedText(origin.environment_variable, 128))
      ) {
        throw new Error("The provider origin response is incompatible.");
      }
      return origin;
    });
    return { profiles, origins };
  }

  const ERROR_PATHS = Object.freeze({
    "cognition-budget": "cognition.aggregate_output_token_ceiling",
    "cognition-mode": "cognition.mode",
    "cognition-effect-scope": "cognition.effect_scope",
    "cognition-funded-responses": "cognition.funded_response_limit",
  });

  class ProviderDraftError extends Error {
    constructor(controlId, message) {
      super(message);
      this.controlId = controlId;
      this.path = ERROR_PATHS[controlId] || "cognition.provider_profile_id";
    }
  }

  class AdLifeProviderPanel {
    constructor({ requestJson, postCredential, onChange, onAuthorityChange, announce }) {
      if (
        typeof requestJson !== "function" ||
        typeof postCredential !== "function" ||
        typeof onChange !== "function" ||
        typeof onAuthorityChange !== "function" ||
        typeof announce !== "function"
      ) {
        throw new TypeError("Provider panel callbacks are required.");
      }
      this.requestJson = requestJson;
      this.postCredential = postCredential;
      this.onChange = onChange;
      this.onAuthorityChange = onAuthorityChange;
      this.announce = announce;
      this.profiles = new Map();
      this.origins = [];
      this.bounded = null;
      this.providerFundedLimit = null;
      this.boundedCheck = null;
      this.loadGeneration = 0;
      this.generation = 0;
      this.busy = false;
      this.installEvents();
      this.configureBounded(null);
    }

    installEvents() {
      byId("cognition-effect-scope").addEventListener("change", () => {
        this.advanceAuthority();
        this.clearActionStatuses();
        this.renderMode();
        this.onChange();
      });
      byId("cognition-mode").addEventListener("change", () => {
        this.advanceAuthority();
        this.clearActionStatuses();
        this.renderMode();
        this.onChange();
      });
      byId("provider-profile").addEventListener("change", () => {
        this.advanceAuthority();
        this.clearActionStatuses();
        this.renderSelection();
        this.onChange();
      });
      byId("cognition-funded-responses").addEventListener("change", () => {
        const value = Number(byId("cognition-funded-responses").value);
        if (
          PROVIDER_MODES.has(byId("cognition-mode").value) &&
          this.bounded?.funded_response_limits.includes(value)
        ) {
          this.providerFundedLimit = value;
        }
        this.renderFunding();
      });
      byId("provider-origin").addEventListener("change", () => this.renderOrigin());
      byId("provider-seed-policy").addEventListener("change", () => this.renderSeedPolicy());
      byId("provider-create-form").addEventListener("submit", (event) => {
        event.preventDefault();
        void this.createProfile();
      });
      byId("provider-credential-controls").addEventListener("submit", (event) => {
        event.preventDefault();
        void this.saveCredential();
      });
      byId("provider-check").addEventListener("click", () => {
        void this.checkCompatibility();
      });
      byId("cognition-budget").addEventListener("input", () => this.renderBudget());
    }

    /** Accept the server-declared bounded-signal capability, or null when it is not offered. */
    configureBounded(capability) {
      this.bounded = capability;
      this.providerFundedLimit = capability === null
        ? null
        : capability.default_provider_funded_response_limit;
      const option = byId("cognition-effect-scope").querySelector(`option[value="${BOUNDED_SCOPE}"]`);
      option.disabled = capability === null;
      option.textContent = capability === null
        ? "Bounded AI cognition (not offered by this server)"
        : "Bounded AI cognition";
      if (capability === null && byId("cognition-effect-scope").value === BOUNDED_SCOPE) {
        byId("cognition-effect-scope").value = EXPLANATION_SCOPE;
      }
      this.renderMode();
    }

    async load() {
      const generation = ++this.loadGeneration;
      const documentValue = await this.requestJson("/api/providers");
      const verified = assertProviderDocument(documentValue);
      if (generation !== this.loadGeneration) return;
      this.profiles = new Map(verified.profiles.map((profile) => [profileKey(profile), profile]));
      this.origins = verified.origins;
      this.advanceAuthority();
      this.renderMode();
    }

    /** Every authority-affecting edit advances exactly one generation and drops scoped checks. */
    advanceAuthority() {
      this.generation += 1;
      this.boundedCheck = null;
      this.clearCredential();
      byId("explanations-section").dataset.authorityGeneration = String(this.generation);
      this.onAuthorityChange();
    }

    clearCredential() {
      byId("provider-credential").value = "";
    }

    clearActionStatuses() {
      setText("provider-status", "");
      setText("provider-credential-status", "");
      this.setCheckStatus("");
    }

    /** One place owns the check status and its optional next-step hint, so neither goes stale. */
    setCheckStatus(message, hint = null) {
      setText("provider-check-status", message);
      const element = byId("provider-check-hint");
      element.hidden = hint === null;
      element.replaceChildren(...(hint === null ? [] : hint));
    }

    /** A cold local model can outlast the check; say how to warm it instead of implying a defect. */
    timeoutHint(profile) {
      if (profile?.mode !== "local") return null;
      const lead = document.createTextNode(
        "The local model may still be loading. Warm it first, then check again",
      );
      if (profile.origin_approval_id !== DEFAULT_LOCAL_ORIGIN) {
        return [lead, document.createTextNode(".")];
      }
      const command = document.createElement("code");
      command.textContent = `ollama run ${profile.model_id} --keepalive 30m hello`;
      return [lead, document.createTextNode(", for example: "), command];
    }

    effectScope() {
      const scope = byId("cognition-effect-scope").value;
      if (!EFFECT_SCOPES.includes(scope) || (scope === BOUNDED_SCOPE && this.bounded === null)) {
        throw new ProviderDraftError("cognition-effect-scope", "Choose an operation offered by this server.");
      }
      return scope;
    }

    mode() {
      const mode = byId("cognition-mode").value;
      if (!MODES.includes(mode)) {
        throw new ProviderDraftError("cognition-mode", "Choose a supported model source.");
      }
      return mode;
    }

    scopeValue() {
      return byId("cognition-effect-scope").value === BOUNDED_SCOPE ? BOUNDED_SCOPE : EXPLANATION_SCOPE;
    }

    selectedProfile() {
      return this.profiles.get(byId("provider-profile").value) || null;
    }

    refreshActionControls() {
      const mode = byId("cognition-mode").value;
      const usesProvider = PROVIDER_MODES.has(mode);
      const profile = this.selectedProfile();
      byId("provider-create-submit").disabled = this.busy || !usesProvider ||
        byId("provider-origin").disabled;
      byId("provider-save-credential").disabled = this.busy || mode !== "remote" ||
        profile === null || profile.mode !== "remote" || !profile.endpoint_policy_accepted;
      byId("provider-check").disabled = this.busy || !usesProvider || profile === null ||
        !profile.endpoint_policy_accepted || !profile.credential_available;
    }

    renderMode() {
      const scope = this.scopeValue();
      const bounded = scope === BOUNDED_SCOPE;
      const mode = byId("cognition-mode").value;
      const usesProvider = PROVIDER_MODES.has(mode);
      byId("explanations-section").dataset.effectScope = scope;
      byId("provider-settings").hidden = !usesProvider;
      byId("provider-check-panel").hidden = !usesProvider;
      byId("explanation-budget").hidden = bounded || !usesProvider;
      byId("provider-product-login-note").hidden = mode !== "remote";
      setText(
        "explanation-source-label",
        `${SCOPE_LABELS[scope]} · ${MODE_LABELS[mode] || "Unavailable"}`,
      );
      setText("cognition-effect-scope-help", SCOPE_HELP[scope]);
      setText(
        "cognition-mode-help",
        (bounded ? BOUNDED_MODE_HELP : EXPLANATION_MODE_HELP)[mode] || "Choose a supported source.",
      );
      setText("cognition-boundary-text", BOUNDARY_COPY[scope]);
      byId("cognition-boundary-disclosure").hidden = !bounded;
      setText("provider-check-scope", bounded ? "Bounded-signal contract · required before start" : "Explanation contract");
      setText(
        "provider-check-help",
        bounded
          ? "Checks the exact signal contract with one bounded inference; may incur provider usage."
          : "Runs one bounded inference and may incur provider usage.",
      );
      this.renderFunding();
      if (!usesProvider) {
        byId("cognition-budget").value = "0";
        this.renderBudget();
        this.refreshActionControls();
        return;
      }
      if (byId("cognition-budget").value === "0") {
        byId("cognition-budget").value = "92160";
      }
      this.renderOrigins();
      this.renderProfiles();
      this.renderBudget();
    }

    renderFunding() {
      const capability = this.bounded;
      const visible = capability !== null && this.scopeValue() === BOUNDED_SCOPE;
      byId("bounded-settings").hidden = !visible;
      const disclosure = byId("bounded-cost-disclosure");
      if (!visible) {
        disclosure.hidden = true;
        disclosure.textContent = "";
        return;
      }
      const select = byId("cognition-funded-responses");
      const provider = PROVIDER_MODES.has(byId("cognition-mode").value);
      const values = provider
        ? capability.funded_response_limits
        : [capability.offline_funded_response_limit];
      const wanted = provider ? this.providerFundedLimit : capability.offline_funded_response_limit;
      const current = [...select.options].map((option) => Number(option.value));
      if (JSON.stringify(current) !== JSON.stringify(values)) {
        select.replaceChildren(...values.map((value) => {
          const option = document.createElement("option");
          option.value = String(value);
          option.textContent = provider
            ? `${value} requests${value === capability.default_provider_funded_response_limit ? " (default)" : ""}`
            : `${value} · all selected, resolved offline`;
          return option;
        }));
      }
      select.value = String(values.includes(wanted) ? wanted : values[0]);
      select.disabled = !provider;
      const limit = Number(select.value);
      const selected = capability.selected_requests_per_run;
      if (!provider) {
        setText(
          "cognition-funded-status",
          `Rules and mock resolve every selected response (up to ${selected}) locally. No provider tokens are reserved.`,
        );
        disclosure.hidden = true;
        disclosure.textContent = "";
        return;
      }
      setText(
        "cognition-funded-status",
        limit < selected
          ? `The first ${limit} of up to ${selected} selected responses go to the model; the rest are recorded as budget-skipped neutral signals.`
          : `Every selected response (up to ${selected}) goes to the model.`,
      );
      const reserved = capability.reserved_output_tokens_per_request;
      setText(
        "bounded-cost-disclosure",
        `Each funded request reserves ${formatCount(reserved)} output tokens: at most ${capability.max_http_attempts_per_request} HTTP attempts of up to ${formatCount(capability.max_output_tokens_per_attempt)} output tokens each. ${limit} requests reserve ${formatCount(limit * reserved)} output tokens. Input tokens and vendor charges are not included; this is a reservation, not a spending limit.`,
      );
      disclosure.hidden = false;
    }

    renderOrigins() {
      const mode = byId("cognition-mode").value;
      const originSelect = byId("provider-origin");
      const previous = originSelect.value;
      const matching = this.origins.filter((origin) => origin.trust_class === mode);
      originSelect.replaceChildren();
      for (const origin of matching) {
        const option = document.createElement("option");
        option.value = origin.approval_id;
        option.textContent = `${origin.approval_id} — ${origin.base_url}`;
        originSelect.append(option);
      }
      if (matching.some((origin) => origin.approval_id === previous)) {
        originSelect.value = previous;
      }
      originSelect.disabled = matching.length === 0;
      byId("provider-create-submit").disabled = matching.length === 0 || this.busy;
      if (matching.length === 0) {
        setText("provider-create-error", `No ${mode} endpoint was approved when this server started.`);
      } else {
        setText("provider-create-error", "");
      }
      this.renderOrigin();
    }

    renderOrigin() {
      const origin = this.origins.find(
        (candidate) => candidate.approval_id === byId("provider-origin").value,
      );
      if (!origin) return;
      if (!byId("provider-profile-id").value) byId("provider-profile-id").value = origin.approval_id;
      if (!byId("provider-label").value) {
        byId("provider-label").value = origin.approval_id === DEFAULT_LOCAL_ORIGIN
          ? "Local Ollama"
          : origin.trust_class === "local" ? "Local model" : "Remote model";
      }
      if (!byId("provider-model").value && origin.approval_id === DEFAULT_LOCAL_ORIGIN) {
        byId("provider-model").value = DEFAULT_LOCAL_MODEL;
      }
    }

    renderProfiles(preferred = null) {
      const mode = byId("cognition-mode").value;
      const select = byId("provider-profile");
      const previous = preferred || select.value;
      this.clearCredential();
      const matching = [...this.profiles.values()]
        .filter((profile) => profile.mode === mode)
        .sort((left, right) => profileKey(left).localeCompare(profileKey(right)));
      select.replaceChildren();
      if (matching.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No matching profile — create one below";
        select.append(option);
        select.disabled = true;
      } else {
        for (const profile of matching) {
          const option = document.createElement("option");
          option.value = profileKey(profile);
          option.textContent = `${profile.display_label} · ${profile.model_id} · ${profile.revision_sha256.slice(0, 10)}`;
          option.disabled = !profile.endpoint_policy_accepted;
          select.append(option);
        }
        const selectable = matching.filter((profile) => profile.endpoint_policy_accepted);
        select.disabled = selectable.length === 0;
        if (selectable.some((profile) => profileKey(profile) === previous)) {
          select.value = previous;
        } else if (selectable.length > 0) {
          select.value = profileKey(selectable[0]);
        }
      }
      this.renderEmptyHint(matching);
      this.renderSelection();
    }

    renderEmptyHint(matching) {
      const hint = byId("provider-empty-hint");
      const mode = byId("cognition-mode").value;
      const origins = this.origins.filter((origin) => origin.trust_class === mode);
      if (
        matching.some((profile) => profile.endpoint_policy_accepted) ||
        origins.length === 0 || !PROVIDER_MODES.has(mode)
      ) {
        hint.hidden = true;
        setText("provider-empty-hint", "");
        return;
      }
      const suggested = mode === "local" &&
        origins.some((origin) => origin.approval_id === DEFAULT_LOCAL_ORIGIN);
      const prefix = matching.length === 0
        ? `No ${mode} profile yet.`
        : `No ${mode} profile is allowed by this server's endpoint policy.`;
      setText(
        "provider-empty-hint",
        suggested
          ? `${prefix} Create one below: the approved ${DEFAULT_LOCAL_ORIGIN} endpoint and model ${DEFAULT_LOCAL_MODEL} are prefilled; edit the model ID if yours differs.`
          : `${prefix} Create one below from an approved endpoint.`,
      );
      hint.hidden = false;
      byId("provider-create-details").open = true;
    }

    renderSelection() {
      const profile = this.selectedProfile();
      this.setCheckStatus("");
      byId("provider-receipt").hidden = profile === null;
      const remote = profile?.mode === "remote";
      byId("provider-credential-controls").hidden = !remote;
      this.refreshActionControls();
      if (profile === null) {
        setText("provider-endpoint", "—");
        setText("provider-model-receipt", "—");
        setText("provider-credential-state", "—");
        setText("provider-output-tokens", "—");
        return;
      }
      setText("provider-endpoint", profile.base_url);
      setText("provider-model-receipt", profile.model_id);
      setText(
        "provider-credential-state",
        profile.credential_available ? "Available on server" : "Required",
      );
      const bounded = this.scopeValue() === BOUNDED_SCOPE && this.bounded !== null;
      // Signals cap each attempt at the server's limit without rewriting the saved profile.
      const effective = bounded
        ? Math.min(profile.max_output_tokens, this.bounded.max_output_tokens_per_attempt)
        : profile.max_output_tokens;
      setText(
        "provider-output-tokens",
        effective === profile.max_output_tokens
          ? `${formatCount(effective)} per attempt`
          : `${formatCount(effective)} per attempt for signals (profile allows ${formatCount(profile.max_output_tokens)})`,
      );
      if (bounded) {
        if (this.currentBoundedCheck(profile) !== null) this.renderCheck(this.boundedCheck.result, BOUNDED_SCOPE);
      } else if (profile.last_check !== null) {
        this.renderCheck(profile.last_check, EXPLANATION_SCOPE);
      }
    }

    renderSeedPolicy() {
      const fixed = byId("provider-seed-policy").value === "fixed";
      byId("provider-seed").disabled = !fixed;
    }

    renderBudget() {
      const mode = byId("cognition-mode").value;
      const input = byId("cognition-budget");
      input.disabled = !PROVIDER_MODES.has(mode);
      if (!PROVIDER_MODES.has(mode)) {
        input.value = "0";
        setText("cognition-budget-status", "Offline modes fund every selected explanation without provider tokens.");
        return;
      }
      const value = Number(input.value);
      if (!Number.isInteger(value) || value < 0 || value > 92160 || value % 1536 !== 0) {
        setText("cognition-budget-status", "Use a multiple of 1,536 from 0 through 92,160.");
        return;
      }
      const funded = value / 1536;
      const skipped = 60 - funded;
      setText(
        "cognition-budget-status",
        `${funded} selected responses funded · up to ${skipped} budget-skipped.`,
      );
    }

    readProfileDraft() {
      const origin = this.origins.find(
        (candidate) => candidate.approval_id === byId("provider-origin").value,
      );
      const mode = this.mode();
      if (!origin || origin.trust_class !== mode) {
        throw new ProviderDraftError("provider-origin", "Choose an approved endpoint.");
      }
      const profileId = requiredText("provider-profile-id", 64);
      if (!PROFILE_ID_PATTERN.test(profileId)) {
        throw new ProviderDraftError("provider-profile-id", "Use lowercase letters, numbers, and hyphens.");
      }
      const seedPolicy = byId("provider-seed-policy").value;
      let seed = null;
      if (seedPolicy === "fixed") {
        const seedText = byId("provider-seed").value;
        if (!SEED_PATTERN.test(seedText) || BigInt(seedText) > MAX_SEED || BigInt(seedText) > BigInt(Number.MAX_SAFE_INTEGER)) {
          throw new ProviderDraftError("provider-seed", "Use a fixed seed from 0 through 9007199254740991.");
        }
        seed = Number(seedText);
      } else if (seedPolicy !== "omit") {
        throw new ProviderDraftError("provider-seed-policy", "Choose a supported seed policy.");
      }
      return {
        schema_version: 1,
        profile_id: profileId,
        display_label: requiredText("provider-label", 80),
        mode,
        base_url: origin.base_url,
        model_id: requiredText("provider-model", 120),
        protocol: "openai-chat-completions-json-schema-v1",
        temperature: finiteNumber("provider-temperature", 0, 2),
        top_p: finiteNumber("provider-top-p", 0, 1, { exclusiveMinimum: true }),
        seed_policy: seedPolicy,
        seed,
        max_output_tokens: exactInteger("provider-max-output", 1, 384),
        attempt_timeout_seconds: exactInteger("provider-timeout", 1, 30),
        transient_retries: exactInteger("provider-retries", 0, 2),
        repair_attempts: exactInteger("provider-repair", 0, 1),
        origin_approval_id: origin.approval_id,
      };
    }

    async createProfile() {
      if (this.busy) return;
      setText("provider-create-error", "");
      let draft;
      try {
        draft = this.readProfileDraft();
      } catch (error) {
        setText("provider-create-error", safeMessage(error, "Review the profile fields."));
        const control = error instanceof ProviderDraftError ? byId(error.controlId) : byId("provider-profile-id");
        control?.focus();
        return;
      }
      const generation = this.generation;
      this.busy = true;
      this.refreshActionControls();
      setText("provider-status", "Saving profile revision…");
      try {
        const profile = assertProfile(
          await this.requestJson("/api/providers", { method: "POST", body: draft }),
        );
        this.profiles.set(profileKey(profile), profile);
        if (generation !== this.generation || this.mode() !== profile.mode) return;
        this.advanceAuthority();
        this.renderProfiles(profileKey(profile));
        byId("provider-create-details").open = false;
        setText("provider-status", "Profile saved as an immutable revision.");
        byId("provider-profile").focus();
        this.announce("Provider profile revision saved.");
        this.onChange();
      } catch (error) {
        this.clearCredential();
        if (generation !== this.generation) return;
        setText("provider-create-error", safeMessage(error, "The provider profile could not be saved."));
        byId("provider-profile-id").focus();
      } finally {
        this.busy = false;
        this.refreshActionControls();
      }
    }

    async saveCredential() {
      const input = byId("provider-credential");
      let credential = input.value;
      const profile = this.selectedProfile();
      if (this.busy || profile === null || profile.mode !== "remote") {
        input.value = "";
        credential = "";
        return;
      }
      if (!boundedText(credential, 4096) || [...credential].some((character) => {
        const code = character.codePointAt(0);
        return code < 0x21 || code > 0x7e;
      })) {
        setText("provider-credential-status", "Enter a visible ASCII credential up to 4,096 characters.");
        input.value = "";
        credential = "";
        input.focus();
        return;
      }
      // Replacing a credential invalidates every scoped compatibility result on the server.
      this.advanceAuthority();
      const generation = this.generation;
      this.busy = true;
      this.refreshActionControls();
      this.setCheckStatus("");
      setText("provider-credential-status", "Saving to the server-side secret store…");
      try {
        const pending = this.postCredential(
          `/api/providers/${profile.profile_id}/revisions/${profile.revision_sha256}/credential`,
          credential,
        );
        input.value = "";
        credential = "";
        const updated = assertProfile(await pending);
        if (profileKey(updated) !== profileKey(profile)) {
          throw new Error("The credential response identified a different profile revision.");
        }
        const key = profileKey(profile);
        this.profiles.set(key, updated);
        if (generation !== this.generation || this.selectedProfile() === null ||
            profileKey(this.selectedProfile()) !== key) return;
        this.renderProfiles(key);
        setText("provider-credential-status", "Credential saved on the server.");
        this.announce("Provider credential saved on the server.");
        this.onChange();
      } catch (error) {
        if (generation !== this.generation) return;
        setText("provider-credential-status", safeMessage(error, "The credential could not be stored."));
        input.focus();
      } finally {
        input.value = "";
        credential = "";
        this.busy = false;
        this.refreshActionControls();
      }
    }

    renderCheck(result, scope) {
      const reason = result.reason_code === null ? "" : result.reason_code.replaceAll("-", " ");
      const bounded = scope === BOUNDED_SCOPE;
      if (result.status === "compatible") {
        this.setCheckStatus(bounded ? "Bounded-signal contract confirmed." : "Compatible inference confirmed.");
      } else if (result.status === "unavailable") {
        this.setCheckStatus(`${bounded ? "Bounded-signal check" : "Check"} unavailable · ${reason}.`);
      } else if (result.reason_code === TIMEOUT_REASON) {
        this.setCheckStatus(
          "Not confirmed: the model did not answer within the check's time limit.",
          this.timeoutHint(this.selectedProfile()),
        );
      } else {
        this.setCheckStatus(`Not compatible${bounded ? " with bounded signals" : ""} · ${reason}.`);
      }
    }

    currentBoundedCheck(profile) {
      const check = this.boundedCheck;
      if (
        check === null || profile === null || check.generation !== this.generation ||
        check.key !== profileKey(profile)
      ) {
        return null;
      }
      return check;
    }

    async checkCompatibility() {
      const profile = this.selectedProfile();
      if (this.busy || profile === null || !profile.credential_available) return;
      const scope = this.scopeValue();
      const bounded = scope === BOUNDED_SCOPE;
      if (bounded && this.bounded === null) return;
      const generation = this.generation;
      const key = profileKey(profile);
      this.busy = true;
      this.refreshActionControls();
      this.setCheckStatus(
        profile.mode === "local"
          ? "Checking compatibility… a local model that is still loading may take a minute or more."
          : "Checking compatibility…",
      );
      try {
        const response = await this.requestJson(
          `/api/providers/${profile.profile_id}/revisions/${profile.revision_sha256}/check`,
          { method: "POST", body: bounded ? { effect_scope: BOUNDED_SCOPE } : {} },
        );
        if (bounded) {
          const result = assertBoundedCheck(response, profile);
          const selected = this.selectedProfile();
          if (generation !== this.generation || selected === null || profileKey(selected) !== key) return;
          this.boundedCheck = Object.freeze({ key, generation, result });
          this.renderCheck(result, BOUNDED_SCOPE);
          this.announce("Bounded-signal compatibility check completed.");
          this.onAuthorityChange();
          return;
        }
        const result = assertCheck(response);
        this.profiles.set(key, { ...profile, last_check: result });
        if (generation !== this.generation || this.selectedProfile() === null || profileKey(this.selectedProfile()) !== key) return;
        this.renderCheck(result, EXPLANATION_SCOPE);
        this.announce("Provider compatibility check completed.");
      } catch (error) {
        this.clearCredential();
        if (generation !== this.generation) return;
        this.setCheckStatus(safeMessage(error, "The compatibility check could not be completed."));
        byId("provider-check").focus();
      } finally {
        this.busy = false;
        this.refreshActionControls();
      }
    }

    /** The server no longer holds a success for this profile; require a fresh explicit check. */
    invalidateBoundedCheck() {
      this.boundedCheck = null;
      if (this.scopeValue() === BOUNDED_SCOPE) {
        this.setCheckStatus("Check again: the server needs a current bounded-signal result.");
      }
      this.onAuthorityChange();
    }

    /** Null when a validated selection may start; otherwise the exact missing authority. */
    startRequirement(selection) {
      if (
        !isObject(selection) || this.bounded === null ||
        selection.schemaVersion !== this.bounded.workbench_schema_version ||
        !this.bounded.compatibility_check_required_modes.includes(selection.cognition?.mode)
      ) {
        return null;
      }
      const profile = this.selectedProfile();
      const check = this.currentBoundedCheck(profile);
      if (
        check === null ||
        check.key !== `${selection.cognition.provider_profile_id}/${selection.cognition.provider_revision_sha256}`
      ) {
        return "Check bounded-signal compatibility for this profile revision to enable the run.";
      }
      if (check.result.status !== "compatible") {
        return "This profile revision did not pass the bounded-signal check; choose or fix a profile.";
      }
      return null;
    }

    /** Build the exact outer schema and cognition body the runtime accepts for the chosen scope. */
    serialize() {
      const scope = this.effectScope();
      const mode = this.mode();
      if (scope === BOUNDED_SCOPE) {
        const capability = this.bounded;
        if (!capability.modes.includes(mode)) {
          throw new ProviderDraftError("cognition-mode", "Choose a source offered for bounded cognition.");
        }
        if (!PROVIDER_MODES.has(mode)) {
          return Object.freeze({
            schemaVersion: capability.workbench_schema_version,
            cognition: {
              effect_scope: BOUNDED_SCOPE,
              mode,
              provider_profile_id: null,
              provider_revision_sha256: null,
              funded_response_limit: capability.offline_funded_response_limit,
            },
          });
        }
        const profile = this.selectedProfile();
        if (profile === null || profile.mode !== mode || !profile.endpoint_policy_accepted) {
          throw new ProviderDraftError("provider-profile", `Choose an approved ${mode} profile revision.`);
        }
        const limit = Number(byId("cognition-funded-responses").value);
        if (!capability.funded_response_limits.includes(limit)) {
          throw new ProviderDraftError(
            "cognition-funded-responses",
            "Choose a funded response count offered by this server.",
          );
        }
        return Object.freeze({
          schemaVersion: capability.workbench_schema_version,
          cognition: {
            effect_scope: BOUNDED_SCOPE,
            mode,
            provider_profile_id: profile.profile_id,
            provider_revision_sha256: profile.revision_sha256,
            funded_response_limit: limit,
          },
        });
      }
      if (!PROVIDER_MODES.has(mode)) {
        return Object.freeze({
          schemaVersion: 3,
          cognition: {
            mode,
            provider_profile_id: null,
            provider_revision_sha256: null,
            aggregate_output_token_ceiling: 0,
          },
        });
      }
      const profile = this.selectedProfile();
      if (profile === null || profile.mode !== mode || !profile.endpoint_policy_accepted) {
        throw new ProviderDraftError("provider-profile", `Choose an approved ${mode} profile revision.`);
      }
      const budget = exactInteger("cognition-budget", 0, 92160);
      if (budget % 1536 !== 0) {
        throw new ProviderDraftError("cognition-budget", "Use a multiple of 1,536 output tokens.");
      }
      return Object.freeze({
        schemaVersion: 3,
        cognition: {
          mode,
          provider_profile_id: profile.profile_id,
          provider_revision_sha256: profile.revision_sha256,
          aggregate_output_token_ceiling: budget,
        },
      });
    }

    /** Inert review facts for one serialized selection; profile labels stay plain text. */
    reviewFacts(selection) {
      const bounded = selection.schemaVersion === 4;
      const cognition = selection.cognition;
      const mode = cognition.mode;
      const provider = PROVIDER_MODES.has(mode);
      const profile = provider
        ? this.profiles.get(`${cognition.provider_profile_id}/${cognition.provider_revision_sha256}`) || null
        : null;
      const capability = this.bounded;
      let funding;
      if (bounded) {
        funding = provider
          ? `${cognition.funded_response_limit} funded of up to ${capability.selected_requests_per_run} selected · ${formatCount(cognition.funded_response_limit * capability.reserved_output_tokens_per_request)} output tokens reserved`
          : `Up to ${capability.selected_requests_per_run} selected · resolved offline · no reservation`;
      } else {
        funding = provider
          ? `${formatCount(cognition.aggregate_output_token_ceiling)} output-token reservation · ${cognition.aggregate_output_token_ceiling / 1536} explanations funded`
          : "None · offline";
      }
      const sourceLabel = MODE_LABELS[mode];
      const signals = provider
        ? `${sourceLabel}, with deterministic neutral fallback`
        : mode === "rules" ? "Rules: neutral signals" : "Mock: deterministic signals, offline";
      const ownership = bounded
        ? [
            ["Movement, exposure, attention", "Rules"],
            ["Purchase intention", PURCHASE_OWNERSHIP],
            ["Cognition signals", signals],
            [
              "Reproducibility",
              provider ? "Live signals are not guaranteed reproducible" : "Identical on rerun",
            ],
            ["Saved replay", "Frozen and offline"],
          ]
        : [
            ["Movement, exposure, attention", "Rules"],
            ["Purchase intention", PURCHASE_OWNERSHIP],
            ["Explanations", `${sourceLabel} text only; cannot change results`],
            ["Saved replay", "Frozen and offline"],
          ];
      return Object.freeze({
        operation: bounded ? "Bounded AI cognition" : "Explanation only",
        schema: bounded ? "Workbench schema 4 → run V10" : "Workbench schema 3 → run V9",
        sourceLabel: provider ? sourceLabel : `${sourceLabel} (offline)`,
        profileLabel: profile === null ? (provider ? cognition.provider_profile_id : null) : profile.display_label,
        modelId: profile === null ? null : profile.model_id,
        revision: provider ? cognition.provider_revision_sha256.slice(0, 10) : null,
        funding,
        compatibilityRequired: bounded && provider,
        startLabel: bounded ? "Start bounded cognition run" : "Start deterministic run",
        ownership: Object.freeze(ownership.map((row) => Object.freeze(row))),
      });
    }

    reset() {
      byId("cognition-effect-scope").value = EXPLANATION_SCOPE;
      byId("cognition-mode").value = "rules";
      byId("cognition-budget").value = "0";
      this.providerFundedLimit = this.bounded === null
        ? null
        : this.bounded.default_provider_funded_response_limit;
      this.advanceAuthority();
      this.clearActionStatuses();
      this.renderMode();
    }
  }

  Object.defineProperty(window, "AdLifeProviderPanel", {
    value: AdLifeProviderPanel,
    configurable: false,
    enumerable: false,
    writable: false,
  });
})();
