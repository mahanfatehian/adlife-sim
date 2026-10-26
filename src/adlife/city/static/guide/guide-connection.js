// AdLife guided demo: the AI connection component and small helpers shared by the
// start and setup pages. Page loads only read (GET); a profile, a key or a paid test
// request is only ever sent from a click.
import { dir, makeRefContext, resolveNodes, t } from "./guide-i18n.js";
import { ApiError, announce, bdi, clear, el, getJson, postJson } from "./guide-core.js";

export const BOUNDED_SCOPE = "bounded-signals-v1";
// The languages inner thoughts can be written in, in the order the server stores them.
export const THOUGHT_LANGUAGES = Object.freeze(["en", "de", "fa"]);

// Ollama's default OpenAI-compatible address and a model it serves; LM Studio and any
// other server on this computer just need their own address typed instead.
const LOCAL_DEFAULT_ADDRESS = "127.0.0.1:11434/v1";
const LOCAL_DEFAULT_MODEL = "qwen2.5:7b-instruct";
const LM_STUDIO_ADDRESS = "127.0.0.1:1234/v1";
const LOOPBACK = "127.0.0.1";
// Only a placeholder: any OpenAI-compatible service works.
const REMOTE_EXAMPLE_ADDRESS = "openrouter.ai/api/v1";
const PROTOCOL = "openai-chat-completions-json-schema-v1";
const MAX_CANDIDATES = 10;
const MAX_MODEL_LENGTH = 120;
const MAX_ADDRESS_LENGTH = 300;
const MAX_RECENT_ADDRESSES = 4;
const RESOLVE_DELAY_MS = 400;
const TEST_COOLDOWN_MS = 3000;
const CHECK_LIMIT_SECONDS = Object.freeze({ remote: 60, local: 120 });
const SOURCES = Object.freeze(["remote", "local", "mock", "rules"]);
const PROVIDER_SOURCES = new Set(["remote", "local"]);
const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const RUN_PAGE_LIMIT = 100;
const MAX_RUN_PAGES = 100;
const WORKBENCH_POLL_MS = 3000;
const PROFILE_FACTS = Object.freeze([
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
]);
// Every reason a bounded check can report, plus the catalog's catch-all.
const REASONS = new Set([
  "endpoint-resolution-refused",
  "authentication-refused",
  "timeout-exhausted",
  "rate-limit-exhausted",
  "server-error-exhausted",
  "response-too-large",
  "protocol-incompatible",
  "invalid-result",
  "credential-unavailable",
  "endpoint-policy-refused",
  "budget-skipped",
  "unknown",
]);

const SOURCE_TEXT = Object.freeze({
  remote: () => t("connection.source.remote"),
  local: () => t("connection.source.local"),
  mock: () => t("connection.source.mock"),
  rules: () => t("connection.source.rules"),
});
// Why a typed address could not be used ("invalid" is rendered with its own parameter).
const ADDRESS_ERRORS = Object.freeze({
  required: () => t("connection.url.required"),
  limit: () => t("connection.url.limit"),
  "is-local": () => t("connection.url.is-local"),
  "is-remote": () => t("connection.url.is-remote"),
});
const SOURCE_HINT = Object.freeze({
  remote: () => t("connection.source.remote.hint"),
  local: () => t("connection.source.local.hint"),
  mock: () => t("connection.source.mock.hint"),
  rules: () => t("connection.source.rules.hint"),
});

let refContextValue = null;

/** The message-reference context for pages that show no story people. */
export function refContext() {
  if (refContextValue === null) refContextValue = makeRefContext({ days: 1, people: [] });
  return refContextValue;
}

/** A catalog sentence as nodes; parameters are isolated in their own elements. */
export function message(key, params = {}, count = undefined) {
  const ref = { key };
  if (count !== undefined) ref.count = count;
  if (Object.keys(params).length > 0) ref.params = params;
  return resolveNodes(ref, refContext());
}

/** Inline items separated by a middle dot drawn in CSS (no separator text in code). */
export function dotList(className, ...items) {
  return el(
    "span",
    { className: `setup-dots ${className}`.trim() },
    ...items.map((item) => el("span", { className: "setup-dots__item" }, item)),
  );
}

export function errorCode(error) {
  return error instanceof ApiError && typeof error.code === "string" && error.code !== ""
    ? error.code
    : null;
}

export function errorStatus(error) {
  return error instanceof ApiError && Number.isInteger(error.status) ? error.status : 0;
}

/** Plain wording for any failed request; a server message is never shown verbatim. */
export function errorNodes(error) {
  const code = errorCode(error);
  if (code === "network-error") return [t("common.error.server")];
  if (code === null || code === "unexpected-response" || code === "missing-csrf-token") {
    return [t("common.error.unexpected")];
  }
  return [
    t("common.error.generic"),
    " ",
    el("span", { className: "setup-error-code" }, message("common.error.code", { code: { id: code } })),
  ];
}

/** Every verified saved run, following pages while the server's scan is complete. */
export async function fetchAllRuns() {
  const runs = [];
  let offset = 0;
  for (let page = 0; page < MAX_RUN_PAGES; page += 1) {
    const listing = await getJson(`/api/runs?offset=${offset}&limit=${RUN_PAGE_LIMIT}`);
    const items = Array.isArray(listing?.runs) ? listing.runs : [];
    runs.push(...items);
    offset += items.length;
    if (listing?.truncated !== false || items.length === 0 || offset >= listing.verified_total) {
      break;
    }
  }
  return runs;
}

/**
 * Read the workbench document once and keep following its active job while one runs.
 * `onJob` receives the active job view, or null once none is active.
 */
export async function followWorkbench(onJob) {
  const workbench = await getJson("/api/workbench");
  onJob(workbench?.active_job ?? null);
  if (workbench?.active_job) {
    const timer = setInterval(async () => {
      try {
        const latest = await getJson("/api/workbench");
        const job = latest?.active_job ?? null;
        onJob(job);
        if (job === null) clearInterval(timer);
      } catch (_error) {
        // A missed poll is retried by the next tick.
      }
    }, WORKBENCH_POLL_MS);
  }
  return workbench;
}

/** Show or hide an active-job banner (G1 and G2) that links to the live progress page. */
export function renderActiveJob(banner, job, pageHref) {
  clear(banner);
  const jobId = job?.job_id;
  if (typeof jobId !== "string" || !/^job-[0-9a-f]{32}$/.test(jobId)) {
    banner.hidden = true;
    return;
  }
  banner.append(
    el("span", { className: "setup-banner__text", text: t("start.active_job") }),
    el("a", {
      className: "g-btn g-btn--secondary",
      text: t("start.active_job.open"),
      attrs: { href: pageHref(`/guide/jobs/${jobId}`) },
    }),
  );
  banner.hidden = false;
}

/**
 * Keyboard and pointer behaviour for a group of `role="radio"` buttons: one tab stop,
 * arrow keys move and select in reading direction, Home/End jump. Disabled radios stay
 * focusable (`aria-disabled`) so their reason can be read.
 */
export function radioGroup(group, { onSelect = () => {} } = {}) {
  const radios = () => [...group.querySelectorAll('[role="radio"]')].filter((item) => !item.hidden);
  const mark = (selected) => {
    for (const item of radios()) {
      const on = item === selected;
      item.setAttribute("aria-checked", on ? "true" : "false");
      item.tabIndex = on ? 0 : -1;
    }
  };
  const choose = (radio, focus) => {
    mark(radio);
    if (focus) radio.focus();
    onSelect(radio.dataset.value, radio);
  };
  group.addEventListener("click", (event) => {
    const radio = event.target instanceof Element ? event.target.closest('[role="radio"]') : null;
    if (radio !== null && group.contains(radio) && !radio.hidden) choose(radio, false);
  });
  group.addEventListener("keydown", (event) => {
    const list = radios();
    const index = list.indexOf(event.target);
    if (index < 0 || list.length === 0) return;
    const forward = dir() === "rtl" ? "ArrowLeft" : "ArrowRight";
    const backward = dir() === "rtl" ? "ArrowRight" : "ArrowLeft";
    let next = null;
    if (event.key === "ArrowDown" || event.key === forward) next = (index + 1) % list.length;
    else if (event.key === "ArrowUp" || event.key === backward) {
      next = (index - 1 + list.length) % list.length;
    } else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = list.length - 1;
    if (next === null) return;
    event.preventDefault();
    choose(list[next], true);
  });
  const current = radios().find((item) => item.getAttribute("aria-checked") === "true");
  mark(current ?? radios()[0] ?? null);
  return {
    set(value) {
      const radio = radios().find((item) => item.dataset.value === String(value));
      if (radio !== undefined) mark(radio);
    },
    value() {
      return radios().find((item) => item.getAttribute("aria-checked") === "true")?.dataset.value ?? null;
    },
    focus() {
      (radios().find((item) => item.tabIndex === 0) ?? radios()[0])?.focus();
    },
  };
}

/**
 * A comparable form of a typed or approved address: no scheme, no trailing slash, the
 * default ports dropped and `localhost` read as 127.0.0.1, the way the server normalizes.
 */
function addressKey(text) {
  let value = String(text ?? "").trim().toLowerCase();
  value = value.replace(/^[a-z][a-z0-9+.-]*:\/\//, "");
  value = value.replace(/\/+$/, "");
  value = value.replace(/^localhost(?=[:/]|$)/, "127.0.0.1");
  value = value.replace(/^([^/]+):(?:80|443)(?=\/|$)/, "$1");
  return value;
}

/** An address as people read it: the server's base URL without its scheme. */
function shownAddress(baseUrl) {
  return String(baseUrl ?? "").replace(/^[a-z][a-z0-9+.-]*:\/\//, "");
}

function profileKey(profile) {
  return `${profile.profile_id}/${profile.revision_sha256}`;
}

function usableProfile(profile) {
  return (
    profile !== null &&
    typeof profile === "object" &&
    ID_PATTERN.test(profile.profile_id ?? "") &&
    HASH_PATTERN.test(profile.revision_sha256 ?? "") &&
    typeof profile.model_id === "string"
  );
}

/** True when a listed revision has exactly the guided draft's facts (absent thinking = off). */
function sameFacts(profile, draft) {
  return (
    PROFILE_FACTS.every((name) => profile[name] === draft[name]) &&
    (profile.disable_thinking === true) === (draft.disable_thinking === true)
  );
}

function revisionPath(profile, action) {
  return `/api/providers/${profile.profile_id}/revisions/${profile.revision_sha256}/${action}`;
}

/** The chosen thought languages in the server's order, without unknowns or repeats. */
export function thoughtLanguagesOf(languages) {
  const chosen = Array.isArray(languages) ? languages : [];
  return THOUGHT_LANGUAGES.filter((language) => chosen.includes(language));
}

class Connection {
  constructor(root, variant, onChange) {
    this.root = root;
    this.variant = variant === "compact" ? "compact" : "full";
    this.onChange = typeof onChange === "function" ? onChange : () => {};
    this.origins = [];
    this.profiles = [];
    this.loadError = null;
    this.source = "mock";
    // What the operator typed per source; the server approves it on the first click.
    this.addresses = { remote: "", local: LOCAL_DEFAULT_ADDRESS };
    this.addressError = null;
    // Model text per source and address.
    this.models = new Map();
    this.profile = null;
    this.check = null;
    this.fresh = false;
    this.reason = null;
    this.notice = null;
    this.keyNotice = null;
    this.needsKey = false;
    this.modelTouched = false;
    this.profileRefused = false;
    this.busy = false;
    this.cooldown = false;
    this.testStarted = 0;
    this.testLimit = CHECK_LIMIT_SECONDS.remote;
    this.ticker = 0;
    this.resolveTimer = 0;
    this.pending = false;
    this.inflight = null;
    this.generation = 0;
    this.recorded = new Map();
    // A run with inner thoughts needs a check of the thoughts contract, recorded apart.
    this.thoughtLanguages = [];
    this.serviceSignature = "";
    // A source picked before the listing arrived wins over the automatic default.
    this.loaded = false;
    this.earlyChoice = null;
    this.build();
  }

  // ----- structure -------------------------------------------------------------------

  build() {
    const root = this.root;
    clear(root);
    root.classList.add("setup-conn", `setup-conn--${this.variant}`);
    const d = {};
    this.dom = d;

    d.sourceLabel = el("p", {
      className: "setup-conn__label",
      text: t("connection.source.label"),
      attrs: { id: "conn-source-label" },
    });
    d.sources = el(
      "div",
      {
        className: "g-seg setup-conn__sources",
        attrs: {
          role: "radiogroup",
          "aria-labelledby": "conn-source-label",
          id: "conn-sources",
          "aria-busy": "true",
        },
      },
      ...SOURCES.map((source) =>
        el("button", {
          className: "setup-conn__source",
          text: SOURCE_TEXT[source](),
          attrs: { type: "button", role: "radio", "aria-checked": "false" },
          dataset: { value: source },
        }),
      ),
    );
    d.sourceHint = el("p", { className: "setup-conn__hint" });
    d.practiceBadge = el("span", {
      className: "g-badge g-badge--practice",
      text: t("common.not_an_ai"),
    });
    d.sourceHintRow = el("div", { className: "setup-conn__hint-row" }, d.sourceHint, d.practiceBadge);

    d.address = el("input", {
      className: "g-input",
      attrs: {
        id: "conn-url",
        type: "text",
        dir: "ltr",
        spellcheck: "false",
        autocomplete: "off",
        autocapitalize: "off",
        inputmode: "url",
        maxlength: String(MAX_ADDRESS_LENGTH),
        "aria-describedby": "conn-url-help conn-url-error",
      },
    });
    d.addressHelp = el("p", { className: "g-help", attrs: { id: "conn-url-help" } });
    d.addressError = el("p", { className: "g-error", attrs: { id: "conn-url-error" } });
    d.recentLabel = el("p", {
      className: "setup-conn__recent-label",
      text: t("connection.recent"),
      attrs: { id: "conn-recent-label" },
    });
    d.recentList = el("div", {
      className: "setup-conn__recent-list",
      attrs: { role: "group", "aria-labelledby": "conn-recent-label" },
    });
    d.recent = el("div", { className: "setup-conn__recent" }, d.recentLabel, d.recentList);
    d.serviceRow = el(
      "div",
      { className: "g-field" },
      el("label", {
        className: "g-label",
        text: t("connection.url"),
        attrs: { for: "conn-url" },
      }),
      d.address,
      d.addressHelp,
      d.addressError,
      d.recent,
    );

    d.model = el("input", {
      className: "g-input",
      attrs: {
        id: "conn-model",
        type: "text",
        dir: "ltr",
        spellcheck: "false",
        autocomplete: "off",
        autocapitalize: "off",
        maxlength: "200",
        "aria-describedby": "conn-model-help conn-model-error",
      },
    });
    d.modelHelp = el("p", {
      className: "g-help",
      text: t("connection.model.hint"),
      attrs: { id: "conn-model-help" },
    });
    d.modelError = el("p", { className: "g-error", attrs: { id: "conn-model-error" } });
    d.thinking = el("p", { className: "setup-conn__thinking", text: t("connection.thinking_off") });
    d.modelRow = el(
      "div",
      { className: "g-field" },
      el("label", {
        className: "g-label",
        text: t("connection.model"),
        attrs: { for: "conn-model" },
      }),
      d.model,
      d.modelHelp,
      d.modelError,
      d.thinking,
    );

    d.keyAvailable = el("p", {
      className: "setup-conn__key-ok",
      text: t("connection.key.available"),
    });
    d.keyInput = el("input", {
      className: "g-input",
      attrs: {
        id: "conn-key",
        type: "password",
        autocomplete: "off",
        spellcheck: "false",
        dir: "ltr",
        placeholder: t("connection.key.placeholder"),
        "aria-describedby": "conn-key-warning",
      },
    });
    d.keySave = el("button", {
      className: "g-btn g-btn--secondary",
      text: t("connection.key.save"),
      attrs: { type: "button", id: "conn-key-save" },
    });
    d.keyRemember = el("input", {
      className: "setup-conn__remember-box",
      attrs: { id: "conn-key-remember", type: "checkbox", "aria-describedby": "conn-key-warning" },
    });
    d.keyStatus = el("p", { className: "setup-conn__key-status", attrs: { id: "conn-key-status" } });
    d.keyNeeded = el("p", {
      className: "setup-conn__key-title",
      text: t("connection.key.needed"),
      attrs: { id: "conn-key-needed", tabindex: "-1" },
    });
    d.keyRow = el(
      "div",
      { className: "g-field setup-conn__key" },
      el("label", { className: "g-label", text: t("connection.key.label"), attrs: { for: "conn-key" } }),
      d.keyAvailable,
      d.keyNeeded,
      el("div", { className: "setup-conn__key-form" }, d.keyInput, d.keySave),
      el(
        "label",
        { className: "setup-conn__remember", attrs: { for: "conn-key-remember" } },
        d.keyRemember,
        el("span", { text: t("connection.key.remember") }),
      ),
      el("p", {
        className: "g-help",
        text: t("connection.key.memory_note"),
        attrs: { id: "conn-key-warning" },
      }),
      d.keyStatus,
    );

    d.test = el("button", {
      className: "g-btn g-btn--secondary",
      text: t("connection.test"),
      attrs: { type: "button", id: "conn-test" },
    });
    d.testRow = el(
      "div",
      { className: "setup-conn__test" },
      d.test,
      el("p", { className: "g-help", text: t("connection.test_note") }),
    );

    d.providerBlock = el(
      "div",
      { className: "setup-conn__provider" },
      d.serviceRow,
      d.modelRow,
      d.keyRow,
      d.testRow,
    );

    d.status = el("p", { className: "setup-conn__status", attrs: { id: "conn-status" } });
    d.hint = el("p", { className: "setup-conn__next", attrs: { id: "conn-hint" } });
    d.localHint = el("p", { className: "setup-conn__next", text: t("connection.local_hint") });
    d.statusBlock = el(
      "div",
      { className: "setup-conn__state", attrs: { id: "conn-state" } },
      d.status,
      d.hint,
      d.localHint,
    );

    d.panel = el(
      "div",
      { className: "setup-conn__panel" },
      d.sourceLabel,
      d.sources,
      d.sourceHintRow,
      d.providerBlock,
    );

    if (this.variant === "compact") {
      d.summarySource = el("span", { className: "setup-conn__summary-source" });
      d.summaryModel = el("span", { className: "setup-conn__summary-model" });
      d.change = el(
        "details",
        { className: "g-details setup-conn__change", attrs: { id: "conn-change" } },
        el("summary", { text: t("connection.change") }),
        d.panel,
      );
      root.append(
        el("div", { className: "setup-conn__summary" }, d.summarySource, d.summaryModel),
        d.statusBlock,
        d.change,
      );
    } else {
      root.append(
        el("h3", { className: "setup-conn__title", text: t("connection.title") }),
        d.panel,
        d.statusBlock,
      );
    }
    // Until the provider listing arrives only the source choices show; update() decides the rest.
    for (const node of [d.practiceBadge, d.providerBlock, d.statusBlock]) node.hidden = true;

    this.sourceGroup = radioGroup(d.sources, { onSelect: (value) => this.selectSource(value) });
    d.address.addEventListener("input", () => this.onAddressInput());
    d.recentList.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (button !== null && d.recentList.contains(button)) this.useRecent(button.dataset.address ?? "");
    });
    d.model.addEventListener("input", () => this.onModelInput());
    d.keySave.addEventListener("click", () => {
      void this.saveKey();
    });
    d.keyInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void this.saveKey();
      }
    });
    d.test.addEventListener("click", () => {
      void this.test();
    });
  }

  // ----- state helpers ---------------------------------------------------------------

  originsOf(source) {
    return this.origins.filter((origin) => origin.trust_class === source);
  }

  addressText() {
    return PROVIDER_SOURCES.has(this.source) ? this.addresses[this.source] ?? "" : "";
  }

  /** The approved origin for the typed address, or null until the server approves it. */
  origin() {
    if (!PROVIDER_SOURCES.has(this.source)) return null;
    const key = addressKey(this.addressText());
    if (key === "") return null;
    return this.originsOf(this.source).find((origin) => addressKey(origin.base_url) === key) ?? null;
  }

  modelSlot() {
    return `${this.source}|${addressKey(this.addressText())}`;
  }

  modelText() {
    return PROVIDER_SOURCES.has(this.source) ? this.models.get(this.modelSlot()) ?? "" : "";
  }

  modelValue() {
    const value = this.modelText().trim();
    return value.length >= 1 && value.length <= MAX_MODEL_LENGTH ? value : null;
  }

  /** Saved profiles of a source, newest first. */
  profilesOf(source) {
    return this.profiles.filter((profile) => profile.mode === source).reverse();
  }

  /** The model last used with an address, or Ollama's default model for its address. */
  prefill(source, address) {
    const key = addressKey(address);
    const used = this.profilesOf(source).find((profile) => addressKey(profile.base_url) === key);
    if (used !== undefined) return used.model_id;
    return source === "local" && key === addressKey(LOCAL_DEFAULT_ADDRESS) ? LOCAL_DEFAULT_MODEL : "";
  }

  /** Addresses of a source used before or approved at startup, newest first. */
  recentAddresses(source) {
    const seen = new Set();
    const listed = [];
    const add = (baseUrl) => {
      const key = addressKey(baseUrl);
      if (key === "" || seen.has(key)) return;
      seen.add(key);
      listed.push(shownAddress(baseUrl));
    };
    for (const profile of this.profilesOf(source)) add(profile.base_url);
    for (const origin of this.originsOf(source)) add(origin.base_url);
    if (source === "local") add(LOCAL_DEFAULT_ADDRESS);
    return listed.slice(0, MAX_RECENT_ADDRESSES);
  }

  draft() {
    const origin = this.origin();
    const model = this.modelValue();
    if (origin === null || model === null) return null;
    const remote = origin.trust_class === "remote";
    const value = {
      schema_version: 1,
      profile_id: `guide-${origin.approval_id}`.slice(0, 64),
      display_label: `Guided - ${origin.approval_id}`.slice(0, 80),
      mode: origin.trust_class,
      base_url: origin.base_url,
      model_id: model,
      protocol: PROTOCOL,
      temperature: 0.2,
      top_p: 0.9,
      seed_policy: "fixed",
      seed: 42,
      max_output_tokens: 256,
      attempt_timeout_seconds: 30,
      transient_retries: 3,
      repair_attempts: 2,
      origin_approval_id: origin.approval_id,
    };
    if (remote && /qwen/i.test(model)) value.disable_thinking = true;
    return value;
  }

  thinkingOff() {
    if (this.profile !== null) return this.profile.disable_thinking === true;
    return this.draft()?.disable_thinking === true;
  }

  locked() {
    return this.busy || this.check === "testing";
  }

  /** A key typed but not sent yet; it is sent with the next test or run. */
  typedKey() {
    return this.source === "remote" ? this.dom.keyInput.value : "";
  }

  /** Whether the API key is known to be missing (true), present (false) or unknown (null). */
  keyMissing() {
    if (this.source !== "remote") return false;
    if (this.typedKey() !== "") return false;
    if (this.profile !== null) return this.profile.credential_available !== true;
    if (this.needsKey) return true;
    const origin = this.origin();
    // A startup-bound environment variable may hold the key; otherwise one must be typed.
    return origin !== null && typeof origin.environment_variable === "string" ? null : true;
  }

  payload() {
    const provider = PROVIDER_SOURCES.has(this.source);
    const missing = this.keyMissing();
    const address = this.addressText().trim();
    return {
      source: this.source,
      mode: this.source,
      origin: provider ? this.origin() : null,
      address: provider ? address : null,
      addressValid: provider ? address !== "" && this.addressError === null : true,
      profile: provider ? this.profile : null,
      credentialAvailable: provider ? (missing === null ? null : !missing) : null,
      check: provider ? this.check : null,
      reasonCode: provider ? this.reason : null,
      ready: provider ? this.check === "ok" && this.profile !== null : true,
      model: provider ? this.modelValue() : null,
      modelValid: provider ? this.modelValue() !== null : true,
    };
  }

  emit() {
    this.onChange(this.payload());
  }

  // ----- loading and resolution (GET only) -------------------------------------------

  async start() {
    try {
      const listing = await getJson("/api/providers");
      this.origins = (Array.isArray(listing?.origins) ? listing.origins : []).filter(
        (origin) =>
          origin !== null &&
          ID_PATTERN.test(origin.approval_id ?? "") &&
          typeof origin.base_url === "string" &&
          PROVIDER_SOURCES.has(origin.trust_class),
      );
      this.profiles = (Array.isArray(listing?.profiles) ? listing.profiles : []).filter(usableProfile);
    } catch (error) {
      this.loadError = error;
    }
    // Start each source on the address used last (or approved at startup), with its model.
    for (const source of PROVIDER_SOURCES) {
      const address = this.recentAddresses(source)[0] ?? this.addresses[source];
      this.addresses[source] = address;
      this.models.set(`${source}|${addressKey(address)}`, this.prefill(source, address));
    }
    await this.chooseDefault();
    const early = this.earlyChoice;
    this.earlyChoice = null;
    this.loaded = true;
    this.dom.sources.removeAttribute("aria-busy");
    if (early !== null && early !== this.source && this.available(early)) {
      this.source = early;
      if (PROVIDER_SOURCES.has(early)) await this.resolveNow();
      else this.resetProvider();
    }
    this.syncAddressInput();
    this.syncModelInput();
    if (this.loadError !== null) this.notice = { tone: "bad", nodes: errorNodes(this.loadError) };
    this.update();
    this.emit();
  }

  async chooseDefault() {
    for (const source of ["remote", "local"]) {
      const found = await this.findRecorded(this.originsOf(source));
      if (found !== null) {
        this.source = source;
        this.addresses[source] = shownAddress(found.base_url);
        this.models.set(`${source}|${addressKey(found.base_url)}`, found.model_id);
        this.profile = found;
        this.check = "ok";
        this.fresh = false;
        return;
      }
    }
    // An online service used before (or approved at startup) is the likely choice again.
    if (this.addresses.remote !== "") {
      this.source = "remote";
      await this.resolveNow();
      return;
    }
    this.source = "mock";
  }

  /** The newest revision of these origins that already passed its bounded check, if any. */
  async findRecorded(origins) {
    const ids = new Set(origins.map((origin) => origin.approval_id));
    const listed = this.profiles.filter(
      (profile) => ids.has(profile.origin_approval_id) && profile.endpoint_policy_accepted === true,
    );
    const preferred = (profile) =>
      profile.model_id === this.models.get(`${profile.mode}|${addressKey(profile.base_url)}`);
    const ordered = [
      ...listed.filter(preferred).reverse(),
      ...listed.filter((profile) => !preferred(profile)).reverse(),
    ].slice(0, MAX_CANDIDATES);
    for (const profile of ordered) {
      if (await this.recordedOk(profile)) return profile;
    }
    return null;
  }

  /** The recorded-check cache key: one per revision and contract (with or without thoughts). */
  checkKey(profile) {
    return `${profileKey(profile)}${this.thoughtLanguages.length > 0 ? "|thoughts" : ""}`;
  }

  recordedOk(profile) {
    const key = this.checkKey(profile);
    const query = this.thoughtLanguages.length > 0 ? "?thoughts=true" : "";
    if (!this.recorded.has(key)) {
      this.recorded.set(
        key,
        getJson(`${revisionPath(profile, "bounded-check")}${query}`).then(
          (result) => result?.status === "compatible",
          () => false,
        ),
      );
    }
    return this.recorded.get(key);
  }

  async resolveNow() {
    const generation = ++this.generation;
    this.profile = null;
    this.check = null;
    this.fresh = false;
    this.reason = null;
    this.needsKey = false;
    const origin = this.origin();
    const model = this.modelValue();
    if (this.addressText().trim() === "" || model === null) return;
    this.check = "untested";
    // An address the server has not approved yet has no usable revision until the click.
    if (origin === null) return;
    const candidates = this.profiles.filter(
      (profile) =>
        profile.origin_approval_id === origin.approval_id &&
        profile.model_id === model &&
        profile.endpoint_policy_accepted === true,
    );
    for (const profile of candidates.slice(-MAX_CANDIDATES).reverse()) {
      const ok = await this.recordedOk(profile);
      if (generation !== this.generation) return;
      if (ok) {
        this.profile = profile;
        this.check = "ok";
        return;
      }
    }
    const draft = this.draft();
    this.profile = candidates.find((profile) => sameFacts(profile, draft)) ?? null;
  }

  requestResolve(delay) {
    clearTimeout(this.resolveTimer);
    this.generation += 1;
    this.profile = null;
    this.check = PROVIDER_SOURCES.has(this.source) && this.modelValue() !== null ? "untested" : null;
    this.fresh = false;
    this.reason = null;
    this.needsKey = false;
    this.pending = true;
    this.resolveTimer = setTimeout(() => {
      void this.settle();
    }, delay);
  }

  /** Finish any scheduled or running resolution before acting on the current choice. */
  async settle() {
    if (this.pending) {
      clearTimeout(this.resolveTimer);
      this.resolveTimer = 0;
      this.pending = false;
      const running = this.resolveNow().then(() => {
        this.update();
        this.emit();
      });
      this.inflight = running;
    }
    while (this.inflight !== null) {
      const running = this.inflight;
      await running;
      if (this.inflight === running) this.inflight = null;
    }
  }

  // ----- user actions ----------------------------------------------------------------

  /** Every source can be chosen: an AI source only needs an address typed in. */
  available(source) {
    return SOURCES.includes(source);
  }

  /** Forget the provider state when an offline source is chosen. */
  resetProvider() {
    clearTimeout(this.resolveTimer);
    this.pending = false;
    this.generation += 1;
    this.profile = null;
    this.check = null;
    this.reason = null;
    this.needsKey = false;
  }

  selectSource(source) {
    if (!this.loaded) {
      // Remember the pick; the listing decides whether it is available.
      if (SOURCES.includes(source)) this.earlyChoice = source;
      return;
    }
    if (!SOURCES.includes(source) || this.locked()) {
      this.update();
      return;
    }
    if (source === this.source) return;
    this.source = source;
    this.notice = null;
    this.keyNotice = null;
    this.addressError = null;
    this.profileRefused = false;
    this.syncAddressInput();
    this.syncModelInput();
    if (PROVIDER_SOURCES.has(source)) this.requestResolve(0);
    else this.resetProvider();
    this.update();
    this.emit();
  }

  onAddressInput() {
    if (!PROVIDER_SOURCES.has(this.source) || this.locked()) return;
    const previous = this.modelSlot();
    this.addresses[this.source] = this.dom.address.value;
    // Keep the typed model when the address changes, unless that address has its own.
    if (!this.models.has(this.modelSlot())) {
      const remembered = this.prefill(this.source, this.addressText());
      this.models.set(this.modelSlot(), remembered || this.models.get(previous) || "");
      this.syncModelInput();
    }
    this.addressError = null;
    this.profileRefused = false;
    this.notice = null;
    this.keyNotice = null;
    this.requestResolve(RESOLVE_DELAY_MS);
    this.update();
    this.emit();
  }

  /** Fill in an address used before, with the model last used there. */
  useRecent(address) {
    if (!PROVIDER_SOURCES.has(this.source) || this.locked() || address === "") return;
    this.addresses[this.source] = address;
    if (!this.models.has(this.modelSlot())) {
      this.models.set(this.modelSlot(), this.prefill(this.source, address));
    }
    this.addressError = null;
    this.profileRefused = false;
    this.notice = null;
    this.keyNotice = null;
    this.syncAddressInput();
    this.syncModelInput();
    this.requestResolve(0);
    this.update();
    this.emit();
  }

  syncAddressInput() {
    this.dom.address.value = this.addressText();
  }

  onModelInput() {
    if (!PROVIDER_SOURCES.has(this.source) || this.locked()) return;
    this.models.set(this.modelSlot(), this.dom.model.value);
    this.modelTouched = true;
    this.profileRefused = false;
    this.notice = null;
    this.keyNotice = null;
    this.requestResolve(RESOLVE_DELAY_MS);
    this.update();
    this.emit();
  }

  syncModelInput() {
    this.dom.model.value = this.modelText();
    this.modelTouched = false;
  }

  replaceProfile(view) {
    if (!usableProfile(view)) return;
    const key = profileKey(view);
    const index = this.profiles.findIndex((profile) => profileKey(profile) === key);
    if (index >= 0) this.profiles[index] = view;
    else this.profiles.push(view);
    this.profile = view;
  }

  async reloadProfiles() {
    const listing = await getJson("/api/providers");
    this.profiles = (Array.isArray(listing?.profiles) ? listing.profiles : []).filter(usableProfile);
  }

  /**
   * Have the server approve the typed address for this process, only ever from a click.
   * Returns the origin, or null with the reason shown under the address.
   */
  async ensureOrigin() {
    const known = this.origin();
    if (known !== null) return known;
    const typed = this.addressText().trim();
    if (typed === "") {
      this.addressError = "required";
      return null;
    }
    let view = null;
    try {
      view = await postJson("/api/providers/origins", { base_url: typed });
    } catch (error) {
      const code = errorCode(error);
      if (code === "invalid-provider-origin") this.addressError = "invalid";
      else if (code === "provider-origin-limit") this.addressError = "limit";
      else throw error;
      return null;
    }
    if (
      view === null ||
      typeof view !== "object" ||
      !ID_PATTERN.test(view.approval_id ?? "") ||
      typeof view.base_url !== "string" ||
      !PROVIDER_SOURCES.has(view.trust_class)
    ) {
      throw new ApiError(0, "unexpected-response");
    }
    if (!this.origins.some((origin) => origin.approval_id === view.approval_id)) this.origins.push(view);
    if (view.trust_class !== this.source) {
      // 127.0.0.1 typed under "Online AI", or a web address under "AI on this computer".
      this.addressError = view.trust_class === "local" ? "is-local" : "is-remote";
      return null;
    }
    this.addressError = null;
    return view;
  }

  /** Create the guided profile revision, only ever from a click. */
  async ensureProfile() {
    await this.settle();
    if (this.profile !== null) return this.profile;
    if ((await this.ensureOrigin()) === null) return null;
    await this.resolveNow();
    if (this.profile !== null) return this.profile;
    const draft = this.draft();
    if (draft === null) return null;
    const generation = this.generation;
    let view = null;
    try {
      view = await postJson("/api/providers", draft);
    } catch (error) {
      const code = errorCode(error);
      if (code === "provider-profile-conflict") {
        await this.reloadProfiles();
        view = this.profiles.find((profile) => sameFacts(profile, draft)) ?? null;
        if (view === null) throw error;
      } else if (code === "invalid-provider-profile") {
        this.profileRefused = true;
        throw error;
      } else {
        throw error;
      }
    }
    if (generation !== this.generation) return null;
    this.replaceProfile(view);
    this.check = "untested";
    this.fresh = false;
    return this.profile;
  }

  /** The "Use this key" button; a typed key is also sent by the next test or run. */
  async saveKey() {
    if (this.dom.keyInput.value === "") {
      this.dom.keyInput.focus();
      return;
    }
    if (this.locked()) return;
    this.busy = true;
    this.update();
    try {
      await this.sendKey();
    } finally {
      this.busy = false;
      this.update();
      this.emit();
    }
  }

  /**
   * Send the typed key once, for this connection, and clear the field whatever happens.
   * Without "Remember" the server keeps it in memory only until it stops. Returns
   * whether the key is now in place.
   */
  async sendKey() {
    const input = this.dom.keyInput;
    const value = input.value;
    const remember = this.dom.keyRemember.checked === true;
    if (value === "") return true;
    this.keyNotice = { tone: "info", text: t("connection.key.saving") };
    this.update();
    try {
      let profile = null;
      try {
        profile = await this.ensureProfile();
      } catch (error) {
        this.keyNotice = this.profileRefused ? null : { tone: "bad", nodes: errorNodes(error) };
        return false;
      }
      if (profile === null) {
        this.keyNotice = null;
        return false;
      }
      const view = await postJson(revisionPath(profile, "credential"), { credential: value, remember });
      // A key in memory serves the whole service, so every earlier test of it is stale.
      this.recorded.clear();
      this.replaceProfile(view);
      this.check = "untested";
      this.fresh = false;
      this.reason = null;
      this.needsKey = false;
      const saved = remember ? t("connection.key.saved") : t("connection.key.held");
      this.keyNotice = { tone: "ok", text: saved };
      announce(saved, { force: true });
      return true;
    } catch (error) {
      const code = errorCode(error);
      if (code === "credential-store-unavailable") {
        this.keyNotice = { tone: "bad", text: t("connection.key.store_unavailable") };
      } else if (code === "provider-endpoint-refused") {
        this.keyNotice = { tone: "bad", text: t("hint.endpoint-policy-refused") };
      } else if (code === "provider-operation-busy") {
        this.keyNotice = { tone: "bad", text: t("connection.busy") };
      } else {
        this.keyNotice = { tone: "bad", nodes: errorNodes(error) };
      }
      return false;
    } finally {
      input.value = "";
    }
  }

  /** The "Test connection" button: never a paid request without a key. */
  async test() {
    if (this.locked() || this.cooldown) return false;
    const profile = await this.prepare();
    if (profile === null) return false;
    if (!(await this.sendTypedKey())) return false;
    if (this.keyMissing() === true) {
      this.promptForKey();
      return false;
    }
    return this.runCheck(profile);
  }

  /** Settle the choice and create the profile when needed; failures are shown in place. */
  async prepare() {
    this.busy = true;
    this.notice = null;
    this.update();
    try {
      return await this.ensureProfile();
    } catch (error) {
      if (!this.profileRefused) this.notice = { tone: "bad", nodes: errorNodes(error) };
      this.openPanel();
      return null;
    } finally {
      this.busy = false;
      this.update();
      this.emit();
    }
  }

  /** Send a key typed into the field before a test or run; true when nothing failed. */
  async sendTypedKey() {
    if (this.typedKey() === "") return true;
    this.busy = true;
    this.update();
    try {
      const sent = await this.sendKey();
      if (!sent) this.openPanel();
      return sent;
    } finally {
      this.busy = false;
      this.update();
      this.emit();
    }
  }

  promptForKey() {
    this.needsKey = true;
    this.openPanel();
    this.update();
    this.emit();
    this.dom.keyInput.focus();
  }

  async runCheck(profile) {
    const generation = this.generation;
    this.check = "testing";
    this.reason = null;
    this.notice = null;
    this.needsKey = false;
    this.testStarted = Date.now();
    this.testLimit = CHECK_LIMIT_SECONDS[profile.mode] ?? CHECK_LIMIT_SECONDS.remote;
    clearInterval(this.ticker);
    this.ticker = setInterval(() => this.renderState(), 1000);
    this.update();
    this.emit();
    announce(t("connection.testing", { seconds: 0, limit: this.testLimit }), { force: true });
    let passed = false;
    try {
      const body = { effect_scope: BOUNDED_SCOPE };
      if (this.thoughtLanguages.length > 0) body.thought_languages = [...this.thoughtLanguages];
      const key = this.checkKey(profile);
      const result = await postJson(revisionPath(profile, "check"), body);
      this.recorded.delete(key);
      if (generation === this.generation) {
        if (this.checkKey(profile) !== key) {
          // Thoughts were turned on or off during the test: it checked the other contract.
          this.check = "untested";
          if (result?.status === "compatible") this.recorded.set(key, Promise.resolve(true));
        } else if (result?.status === "compatible") {
          this.check = "ok";
          this.fresh = true;
          passed = true;
          // "Key ready … now test the connection" has done its job.
          if (this.keyNotice?.tone === "ok") this.keyNotice = null;
          this.recorded.set(key, Promise.resolve(true));
        } else {
          this.check = "failed";
          this.reason = REASONS.has(result?.reason_code) ? result.reason_code : "unknown";
        }
      }
    } catch (error) {
      if (generation === this.generation) {
        this.check = "untested";
        const code = errorCode(error);
        const status = errorStatus(error);
        if (code === "provider-operation-busy") {
          this.notice = { tone: "bad", text: t("connection.busy") };
        } else if (status === 429) {
          this.notice = { tone: "bad", text: t("connection.rate_limited") };
        } else if (status === 502) {
          this.notice = { tone: "bad", text: t("connection.check_failed") };
        } else {
          this.notice = { tone: "bad", nodes: errorNodes(error) };
        }
      }
    } finally {
      clearInterval(this.ticker);
      this.ticker = 0;
      this.cooldown = true;
      setTimeout(() => {
        this.cooldown = false;
        this.update();
      }, TEST_COOLDOWN_MS);
      this.update();
      this.emit();
      announce(this.dom.status.textContent ?? "", { force: true });
    }
    return passed;
  }

  /** What Run calls: settle, create the profile, stop at the key prompt, test when needed. */
  async ensureReady() {
    if (!PROVIDER_SOURCES.has(this.source)) return { ok: true, profile: null };
    await this.settle();
    if (this.addressText().trim() === "" || this.modelValue() === null) {
      this.modelTouched = true;
      if (this.addressText().trim() === "") this.addressError = "required";
      this.openPanel();
      this.update();
      return { ok: false, profile: null };
    }
    if (this.check === "ok" && this.profile !== null && this.typedKey() === "") {
      return { ok: true, profile: this.profile };
    }
    if (this.locked()) return { ok: false, profile: this.profile };
    const profile = await this.prepare();
    if (profile === null) return { ok: false, profile: null };
    if (!(await this.sendTypedKey())) return { ok: false, profile };
    if (this.keyMissing() === true) {
      this.promptForKey();
      return { ok: false, profile, needsKey: true };
    }
    while (this.cooldown) {
      await new Promise((resolve) => {
        setTimeout(resolve, 100);
      });
    }
    const ok = await this.runCheck(profile);
    if (!ok) this.openPanel();
    return { ok, profile: this.profile };
  }

  /**
   * Choose the inner-thought languages the next run asks for. Turning thoughts on or off
   * switches to the other contract's recorded check, which may still be untested.
   */
  async setThoughtLanguages(languages) {
    const next = thoughtLanguagesOf(languages);
    const switched = (next.length > 0) !== (this.thoughtLanguages.length > 0);
    this.thoughtLanguages = next;
    if (!switched || !PROVIDER_SOURCES.has(this.source) || this.check === "testing") return;
    // Look this service's revisions up again under the other contract's recorded check.
    await this.resolveNow();
    this.update();
    this.emit();
  }

  markUntested() {
    if (this.profile !== null) this.recorded.delete(this.checkKey(this.profile));
    if (PROVIDER_SOURCES.has(this.source)) {
      this.check = "untested";
      this.fresh = false;
    }
    this.update();
    this.emit();
  }

  openPanel() {
    if (this.dom.change !== undefined) this.dom.change.open = true;
  }

  // ----- rendering -------------------------------------------------------------------

  update() {
    const d = this.dom;
    const provider = PROVIDER_SOURCES.has(this.source);
    const locked = this.locked();
    for (const button of d.sources.querySelectorAll('[role="radio"]')) {
      const value = button.dataset.value;
      const on = value === this.source;
      button.setAttribute("aria-checked", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
      if (locked && !on) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
    }
    d.sourceHint.textContent = SOURCE_HINT[this.source]();
    d.practiceBadge.hidden = this.source !== "mock";
    d.providerBlock.hidden = !provider;
    if (provider) this.renderProvider(locked);
    this.renderState();
    if (this.variant === "compact") this.renderSummary();
    this.root.dataset.source = this.source;
    this.root.dataset.check = this.check ?? "none";
  }

  renderProvider(locked) {
    const d = this.dom;
    const source = this.source;
    d.address.disabled = locked;
    d.address.placeholder = source === "local" ? LOCAL_DEFAULT_ADDRESS : REMOTE_EXAMPLE_ADDRESS;
    clear(d.addressHelp);
    d.addressHelp.append(
      source === "local"
        ? message("connection.url.hint.local", {
            ollama: { id: LOCAL_DEFAULT_ADDRESS },
            lmstudio: { id: LM_STUDIO_ADDRESS },
          })
        : message("connection.url.hint.remote"),
    );
    clear(d.addressError);
    if (this.addressError === "invalid") {
      d.addressError.append(message("connection.url.invalid", { loopback: { id: LOOPBACK } }));
    } else if (this.addressError !== null) {
      d.addressError.append(ADDRESS_ERRORS[this.addressError]());
    }
    d.addressError.hidden = this.addressError === null;
    d.address.setAttribute("aria-invalid", this.addressError === null ? "false" : "true");

    const current = addressKey(this.addressText());
    const recent = this.recentAddresses(source).filter((address) => addressKey(address) !== current);
    const signature = `${source}|${recent.join(" ")}`;
    if (signature !== this.serviceSignature) {
      this.serviceSignature = signature;
      clear(d.recentList);
      for (const address of recent) {
        d.recentList.append(
          el(
            "button",
            {
              className: "g-chip setup-conn__recent-item",
              attrs: { type: "button" },
              dataset: { address },
            },
            bdi(address, "ltr"),
          ),
        );
      }
    }
    d.recent.hidden = recent.length === 0;
    for (const button of d.recentList.querySelectorAll("button")) button.disabled = locked;

    d.model.disabled = locked;
    const invalid = this.modelValue() === null && (this.modelTouched || this.modelText() !== "");
    if (this.profileRefused) d.modelError.textContent = t("connection.profile_error");
    else if (invalid) d.modelError.textContent = t("connection.model.invalid");
    else d.modelError.textContent = "";
    d.modelError.hidden = d.modelError.textContent === "";
    d.model.setAttribute("aria-invalid", d.modelError.hidden ? "false" : "true");
    d.thinking.hidden = !this.thinkingOff();

    // Only an online service needs a key; a server on this computer gets a fixed dummy one.
    const remote = source === "remote";
    const ready = this.profile !== null && this.profile.credential_available === true;
    d.keyRow.hidden = !remote;
    d.keyAvailable.hidden = !ready;
    d.keyNeeded.hidden = !(this.needsKey && !ready && this.typedKey() === "");
    d.keyInput.placeholder = ready ? t("connection.key.placeholder_replace") : t("connection.key.placeholder");
    d.keySave.disabled = locked;
    d.keyInput.disabled = locked;
    d.keyRemember.disabled = locked;
    clear(d.keyStatus);
    if (this.keyNotice !== null) {
      d.keyStatus.append(...(this.keyNotice.nodes ?? [this.keyNotice.text]));
      d.keyStatus.dataset.tone = this.keyNotice.tone;
    }
    d.keyStatus.hidden = this.keyNotice === null;

    d.test.disabled = locked || this.cooldown || this.modelValue() === null;
    d.test.setAttribute("aria-describedby", "conn-status");
  }

  stateParts() {
    if (this.source === "mock") return { tone: "practice", nodes: [t("connection.source.mock.hint")] };
    if (this.source === "rules") return { tone: "neutral", nodes: [t("connection.source.rules.hint")] };
    if (this.notice !== null && this.check !== "testing") {
      return { tone: this.notice.tone, nodes: this.notice.nodes ?? [this.notice.text] };
    }
    if (this.check === "ok") {
      return { tone: "ok", nodes: [this.fresh ? t("connection.ok") : t("connection.ok_earlier")] };
    }
    if (this.check === "testing") {
      const seconds = Math.max(0, Math.floor((Date.now() - this.testStarted) / 1000));
      return { tone: "info", nodes: [t("connection.testing", { seconds, limit: this.testLimit })] };
    }
    if (this.check === "failed") {
      const reason = this.reason ?? "unknown";
      const hintCode = reason === "budget-skipped" ? "unknown" : reason;
      return {
        tone: "bad",
        nodes: [t("connection.failed", { reason: t(`reason.${reason}`) })],
        hint: t(`hint.${hintCode}`),
        local: this.origin()?.trust_class === "local",
      };
    }
    if (this.needsKey) return { tone: "warn", nodes: [t("connection.key.needed")] };
    if (this.addressText().trim() === "" || this.modelValue() === null) {
      return { tone: "neutral", nodes: [t("connection.enter_details")] };
    }
    return { tone: "neutral", nodes: [t("connection.untested")] };
  }

  renderState() {
    const d = this.dom;
    const parts = this.stateParts();
    clear(d.status);
    d.status.append(...parts.nodes);
    d.status.dataset.tone = parts.tone;
    d.statusBlock.dataset.tone = parts.tone;
    // The full panel already shows the offline source's hint right under the choice.
    d.statusBlock.hidden = this.variant === "full" && !PROVIDER_SOURCES.has(this.source);
    d.hint.textContent = parts.hint ?? "";
    d.hint.hidden = !parts.hint;
    d.localHint.hidden = parts.local !== true;
    if (this.variant === "compact") this.renderSummary();
  }

  renderSummary() {
    const d = this.dom;
    d.summarySource.textContent = SOURCE_TEXT[this.source]();
    clear(d.summaryModel);
    const model = PROVIDER_SOURCES.has(this.source) ? this.modelValue() : null;
    if (model !== null) d.summaryModel.append(bdi(model, "ltr"));
    else if (this.source === "mock") {
      d.summaryModel.append(
        el("span", { className: "g-badge g-badge--practice", text: t("common.not_an_ai") }),
      );
    }
    d.summaryModel.hidden = d.summaryModel.childNodes.length === 0;
  }

  handle() {
    return {
      element: this.root,
      state: () => this.payload(),
      markUntested: () => this.markUntested(),
      setThoughtLanguages: (languages) => this.setThoughtLanguages(languages),
      openPanel: () => this.openPanel(),
      focusSource: () => {
        this.openPanel();
        this.sourceGroup.focus();
      },
      focusModel: () => {
        this.openPanel();
        if (!this.dom.providerBlock.hidden) this.dom.model.focus();
        else this.sourceGroup.focus();
      },
      focusAddress: () => {
        this.openPanel();
        if (!this.dom.providerBlock.hidden) this.dom.address.focus();
        else this.sourceGroup.focus();
      },
    };
  }
}

let current = null;

/** Mount the component in "full" (start page) or "compact" (setup card) form. */
export async function mountConnection(root, { variant = "full", onChange = () => {} } = {}) {
  const connection = new Connection(root, variant, onChange);
  current = connection;
  await connection.start();
  return connection.handle();
}

/** Make the selected source usable for a run; resolves to { ok, profile }. */
export async function ensureReady() {
  if (current === null) return { ok: false, profile: null };
  return current.ensureReady();
}
