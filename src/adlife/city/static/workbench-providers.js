"use strict";

(() => {
  const HASH_PATTERN = /^[0-9a-f]{64}$/;
  const PROFILE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
  const SEED_PATTERN = /^(?:0|[1-9][0-9]{0,18})$/;
  const MAX_SEED = 9223372036854775807n;
  const MODES = Object.freeze(["rules", "mock", "local", "remote"]);
  const PROVIDER_MODES = new Set(["local", "remote"]);
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

  const byId = (id) => document.getElementById(id);

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

  class ProviderDraftError extends Error {
    constructor(controlId, message) {
      super(message);
      this.controlId = controlId;
      this.path = controlId === "cognition-budget"
        ? "cognition.aggregate_output_token_ceiling"
        : controlId === "cognition-mode"
          ? "cognition.mode"
          : "cognition.provider_profile_id";
    }
  }

  class AdLifeProviderPanel {
    constructor({ requestJson, postCredential, onChange, announce }) {
      if (
        typeof requestJson !== "function" ||
        typeof postCredential !== "function" ||
        typeof onChange !== "function" ||
        typeof announce !== "function"
      ) {
        throw new TypeError("Provider panel callbacks are required.");
      }
      this.requestJson = requestJson;
      this.postCredential = postCredential;
      this.onChange = onChange;
      this.announce = announce;
      this.profiles = new Map();
      this.origins = [];
      this.loadGeneration = 0;
      this.generation = 0;
      this.busy = false;
      this.installEvents();
      this.renderMode();
    }

    installEvents() {
      byId("cognition-mode").addEventListener("change", () => {
        this.generation += 1;
        byId("provider-credential").value = "";
        this.clearActionStatuses();
        this.renderMode();
        this.onChange();
      });
      byId("provider-profile").addEventListener("change", () => {
        this.generation += 1;
        byId("provider-credential").value = "";
        this.clearActionStatuses();
        this.renderSelection();
        this.onChange();
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

    async load() {
      const generation = ++this.loadGeneration;
      const documentValue = await this.requestJson("/api/providers");
      const verified = assertProviderDocument(documentValue);
      if (generation !== this.loadGeneration) return;
      this.profiles = new Map(verified.profiles.map((profile) => [profileKey(profile), profile]));
      this.origins = verified.origins;
      this.renderMode();
    }

    clearActionStatuses() {
      setText("provider-status", "");
      setText("provider-credential-status", "");
      setText("provider-check-status", "");
    }

    mode() {
      const mode = byId("cognition-mode").value;
      if (!MODES.includes(mode)) {
        throw new ProviderDraftError("cognition-mode", "Choose a supported explanation source.");
      }
      return mode;
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
      const mode = byId("cognition-mode").value;
      const usesProvider = PROVIDER_MODES.has(mode);
      byId("provider-settings").hidden = !usesProvider;
      setText(
        "explanation-source-label",
        { rules: "Rules", mock: "Mock", local: "Local model", remote: "Remote model" }[mode] || "Unavailable",
      );
      setText(
        "cognition-mode-help",
        {
          rules: "Deterministic grounded wording; offline and reproducible.",
          mock: "Deterministic provider-shaped wording for demos and tests.",
          local: "Calls one approved loopback OpenAI-compatible endpoint.",
          remote: "Calls one approved HTTPS OpenAI-compatible endpoint.",
        }[mode] || "Choose a supported source.",
      );
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
        byId("provider-label").value = origin.trust_class === "local" ? "Local model" : "Remote model";
      }
    }

    renderProfiles(preferred = null) {
      const mode = byId("cognition-mode").value;
      const select = byId("provider-profile");
      const previous = preferred || select.value;
      byId("provider-credential").value = "";
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
      this.renderSelection();
    }

    renderSelection() {
      const profile = this.selectedProfile();
      setText("provider-check-status", "");
      byId("provider-receipt").hidden = profile === null;
      const remote = profile?.mode === "remote";
      byId("provider-credential-controls").hidden = !remote;
      this.refreshActionControls();
      if (profile === null) {
        setText("provider-endpoint", "—");
        setText("provider-model-receipt", "—");
        setText("provider-credential-state", "—");
        return;
      }
      setText("provider-endpoint", profile.base_url);
      setText("provider-model-receipt", profile.model_id);
      setText(
        "provider-credential-state",
        profile.credential_available ? "Available on server" : "Required",
      );
      if (profile.last_check !== null) this.renderCheck(profile.last_check);
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
        this.renderProfiles(profileKey(profile));
        setText("provider-status", "Profile saved as an immutable revision.");
        byId("provider-profile").focus();
        this.announce("Provider profile revision saved.");
        this.onChange();
      } catch (error) {
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
      const generation = this.generation;
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
      this.busy = true;
      this.refreshActionControls();
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

    renderCheck(result) {
      if (result.status === "compatible") {
        setText("provider-check-status", "Compatible inference confirmed.");
      } else if (result.status === "unavailable") {
        setText("provider-check-status", `Check unavailable · ${result.reason_code.replaceAll("-", " ")}.`);
      } else {
        setText("provider-check-status", `Not compatible · ${result.reason_code.replaceAll("-", " ")}.`);
      }
    }

    async checkCompatibility() {
      const profile = this.selectedProfile();
      if (this.busy || profile === null || !profile.credential_available) return;
      const generation = this.generation;
      const key = profileKey(profile);
      this.busy = true;
      this.refreshActionControls();
      setText("provider-check-status", "Checking compatibility…");
      try {
        const result = assertCheck(await this.requestJson(
          `/api/providers/${profile.profile_id}/revisions/${profile.revision_sha256}/check`,
          { method: "POST", body: {} },
        ));
        this.profiles.set(key, { ...profile, last_check: result });
        if (generation !== this.generation || this.selectedProfile() === null || profileKey(this.selectedProfile()) !== key) return;
        this.renderCheck(result);
        this.announce("Provider compatibility check completed.");
      } catch (error) {
        if (generation !== this.generation) return;
        setText("provider-check-status", safeMessage(error, "The compatibility check could not be completed."));
        byId("provider-check").focus();
      } finally {
        this.busy = false;
        this.refreshActionControls();
      }
    }

    serialize() {
      const mode = this.mode();
      if (!PROVIDER_MODES.has(mode)) {
        return {
          mode,
          provider_profile_id: null,
          provider_revision_sha256: null,
          aggregate_output_token_ceiling: 0,
        };
      }
      const profile = this.selectedProfile();
      if (profile === null || profile.mode !== mode || !profile.endpoint_policy_accepted) {
        throw new ProviderDraftError("provider-profile", `Choose an approved ${mode} profile revision.`);
      }
      const budget = exactInteger("cognition-budget", 0, 92160);
      if (budget % 1536 !== 0) {
        throw new ProviderDraftError("cognition-budget", "Use a multiple of 1,536 output tokens.");
      }
      return {
        mode,
        provider_profile_id: profile.profile_id,
        provider_revision_sha256: profile.revision_sha256,
        aggregate_output_token_ceiling: budget,
      };
    }

    reset() {
      this.generation += 1;
      byId("cognition-mode").value = "rules";
      byId("cognition-budget").value = "0";
      byId("provider-credential").value = "";
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
