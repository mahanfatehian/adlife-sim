// AdLife guided demo: the AI connection component and small helpers shared by the
// start and setup pages. Page loads only read (GET); a profile, a key or a paid test
// request is only ever sent from a click.
import { dir, makeRefContext, resolveNodes, t } from "./guide-i18n.js";
import { ApiError, announce, bdi, clear, el, getJson, postJson } from "./guide-core.js";

export const BOUNDED_SCOPE = "bounded-signals-v1";

const GAPGPT_ID = "gapgpt";
const GAPGPT_HOST = "api.gapgpt.app";
const GAPGPT_MODEL = "gapgpt-qwen-3.6";
const LOCAL_ID = "local-ollama";
const LOCAL_MODEL = "qwen2.5:7b-instruct";
const KEY_VARIABLE_FALLBACK = "GAPGPT_API_KEY";
const PROTOCOL = "openai-chat-completions-json-schema-v1";
const MAX_CANDIDATES = 10;
const MAX_MODEL_LENGTH = 120;
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

function hostOf(origin) {
  try {
    return new URL(origin.base_url).host;
  } catch (_error) {
    return "";
  }
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

class Connection {
  constructor(root, variant, onChange) {
    this.root = root;
    this.variant = variant === "compact" ? "compact" : "full";
    this.onChange = typeof onChange === "function" ? onChange : () => {};
    this.origins = [];
    this.profiles = [];
    this.loadError = null;
    this.source = "mock";
    this.originIds = { remote: null, local: null };
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
    d.noRemote = el("p", {
      className: "g-callout g-callout--warn setup-conn__no-remote",
      text: t("connection.no_remote"),
    });

    d.service = el("div", { className: "setup-conn__service" });
    d.serviceRow = el(
      "div",
      { className: "g-field" },
      el("p", {
        className: "g-label",
        text: t("connection.service"),
        attrs: { id: "conn-service-label" },
      }),
      d.service,
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
    d.keyStatus = el("p", { className: "setup-conn__key-status", attrs: { id: "conn-key-status" } });
    d.keyEnv = el("p", { className: "setup-conn__key-env" });
    d.keyDetails = el(
      "details",
      { className: "g-details setup-conn__key-save", attrs: { id: "conn-key-details" } },
      el("summary", { text: t("connection.key.save_title") }),
      el("p", {
        className: "g-help",
        text: t("connection.key.save_warning"),
        attrs: { id: "conn-key-warning" },
      }),
      el(
        "div",
        { className: "setup-conn__key-form" },
        el("label", {
          className: "g-label",
          text: t("connection.key.label"),
          attrs: { for: "conn-key" },
        }),
        d.keyInput,
        d.keySave,
      ),
    );
    d.keyNeeded = el(
      "div",
      { className: "setup-conn__key-needed", attrs: { id: "conn-key-needed", tabindex: "-1" } },
      el("p", { className: "setup-conn__key-title", text: t("connection.key.needed") }),
      d.keyEnv,
      d.keyDetails,
    );
    d.keyRow = el(
      "div",
      { className: "g-field setup-conn__key" },
      el("p", { className: "g-label", text: t("connection.key.label") }),
      d.keyAvailable,
      d.keyNeeded,
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
      d.noRemote,
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
    for (const node of [d.practiceBadge, d.noRemote, d.providerBlock, d.statusBlock]) node.hidden = true;

    this.sourceGroup = radioGroup(d.sources, { onSelect: (value) => this.selectSource(value) });
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

  origin() {
    if (!PROVIDER_SOURCES.has(this.source)) return null;
    const id = this.originIds[this.source];
    return this.origins.find((origin) => origin.approval_id === id) ?? null;
  }

  modelText() {
    const origin = this.origin();
    return origin === null ? "" : this.models.get(origin.approval_id) ?? "";
  }

  modelValue() {
    const value = this.modelText().trim();
    return value.length >= 1 && value.length <= MAX_MODEL_LENGTH ? value : null;
  }

  prefill(origin) {
    if (origin.approval_id === GAPGPT_ID || hostOf(origin) === GAPGPT_HOST) return GAPGPT_MODEL;
    if (origin.approval_id === LOCAL_ID) return LOCAL_MODEL;
    const listed = this.profiles.filter((item) => item.origin_approval_id === origin.approval_id);
    return listed.length > 0 ? listed[listed.length - 1].model_id : "";
  }

  draft() {
    const origin = this.origin();
    const model = this.modelValue();
    if (origin === null || model === null) return null;
    const remote = origin.trust_class === "remote";
    const value = {
      schema_version: 1,
      profile_id: `guide-${origin.approval_id}`.slice(0, 64),
      display_label: `Guided demo - ${origin.approval_id}`,
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
      transient_retries: remote ? 1 : 2,
      repair_attempts: 1,
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

  /** Whether the API key is known to be missing (true), present (false) or unknown (null). */
  keyMissing() {
    const origin = this.origin();
    if (origin === null || origin.trust_class !== "remote") return false;
    if (this.profile !== null) return this.profile.credential_available !== true;
    if (this.needsKey) return true;
    // Without a bound variable only a saved key can work, and no revision exists to hold one.
    return origin.environment_variable === null ? true : null;
  }

  payload() {
    const provider = PROVIDER_SOURCES.has(this.source);
    const missing = this.keyMissing();
    return {
      source: this.source,
      mode: this.source,
      origin: provider ? this.origin() : null,
      profile: provider ? this.profile : null,
      credentialAvailable: provider ? (missing === null ? null : !missing) : null,
      check: provider ? this.check : null,
      reasonCode: provider ? this.reason : null,
      ready: provider ? this.check === "ok" && this.profile !== null : true,
      model: provider ? this.modelValue() : null,
      modelValid: provider ? this.origin() !== null && this.modelValue() !== null : true,
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
    for (const source of PROVIDER_SOURCES) {
      this.originIds[source] = this.originsOf(source)[0]?.approval_id ?? null;
    }
    for (const origin of this.origins) this.models.set(origin.approval_id, this.prefill(origin));
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
        this.originIds[source] = found.origin_approval_id;
        this.models.set(found.origin_approval_id, found.model_id);
        this.profile = found;
        this.check = "ok";
        this.fresh = false;
        return;
      }
    }
    if (this.originsOf("remote").length > 0) {
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
    const preferred = (profile) => profile.model_id === this.models.get(profile.origin_approval_id);
    const ordered = [
      ...listed.filter(preferred).reverse(),
      ...listed.filter((profile) => !preferred(profile)).reverse(),
    ].slice(0, MAX_CANDIDATES);
    for (const profile of ordered) {
      if (await this.recordedOk(profile)) return profile;
    }
    return null;
  }

  recordedOk(profile) {
    const key = profileKey(profile);
    if (!this.recorded.has(key)) {
      this.recorded.set(
        key,
        getJson(revisionPath(profile, "bounded-check")).then(
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
    if (origin === null || model === null) return;
    this.check = "untested";
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

  /** Whether a source can be chosen: provider sources need an approved origin. */
  available(source) {
    return SOURCES.includes(source) && (!PROVIDER_SOURCES.has(source) || this.originsOf(source).length > 0);
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
    this.profileRefused = false;
    this.syncModelInput();
    if (PROVIDER_SOURCES.has(source)) this.requestResolve(0);
    else this.resetProvider();
    this.update();
    this.emit();
  }

  selectOrigin(approvalId) {
    if (!PROVIDER_SOURCES.has(this.source) || this.locked()) {
      this.update();
      return;
    }
    if (this.originIds[this.source] === approvalId) return;
    if (!this.originsOf(this.source).some((origin) => origin.approval_id === approvalId)) return;
    this.originIds[this.source] = approvalId;
    this.notice = null;
    this.keyNotice = null;
    this.profileRefused = false;
    this.syncModelInput();
    this.requestResolve(0);
    this.update();
    this.emit();
  }

  onModelInput() {
    const origin = this.origin();
    if (origin === null || this.locked()) return;
    this.models.set(origin.approval_id, this.dom.model.value);
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

  /** Create the guided profile revision, only ever from a click. */
  async ensureProfile() {
    await this.settle();
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

  async saveKey() {
    const input = this.dom.keyInput;
    const value = input.value;
    try {
      if (value === "") {
        input.focus();
        return;
      }
      if (this.locked()) return;
      this.busy = true;
      this.keyNotice = { tone: "info", text: t("connection.key.saving") };
      this.update();
      let profile = null;
      try {
        profile = await this.ensureProfile();
      } catch (error) {
        this.keyNotice = this.profileRefused ? null : { tone: "bad", nodes: errorNodes(error) };
        return;
      }
      if (profile === null) {
        this.keyNotice = null;
        return;
      }
      const view = await postJson(revisionPath(profile, "credential"), { credential: value });
      this.recorded.delete(profileKey(profile));
      this.replaceProfile(view);
      this.check = "untested";
      this.fresh = false;
      this.reason = null;
      this.needsKey = false;
      this.keyNotice = { tone: "ok", text: t("connection.key.saved") };
      announce(t("connection.key.saved"), { force: true });
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
    } finally {
      input.value = "";
      this.busy = false;
      this.update();
      this.emit();
    }
  }

  /** The "Test connection" button: never a paid request without a key. */
  async test() {
    if (this.locked() || this.cooldown) return false;
    const profile = await this.prepare();
    if (profile === null) return false;
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

  promptForKey() {
    this.needsKey = true;
    this.openPanel();
    this.update();
    this.emit();
    this.dom.keyNeeded.focus();
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
      const result = await postJson(revisionPath(profile, "check"), { effect_scope: BOUNDED_SCOPE });
      this.recorded.delete(profileKey(profile));
      if (generation === this.generation) {
        if (result?.status === "compatible") {
          this.check = "ok";
          this.fresh = true;
          passed = true;
          this.recorded.set(profileKey(profile), Promise.resolve(true));
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
    if (this.origin() === null || this.modelValue() === null) {
      this.modelTouched = true;
      this.openPanel();
      this.update();
      return { ok: false, profile: null };
    }
    if (this.check === "ok" && this.profile !== null) return { ok: true, profile: this.profile };
    if (this.locked()) return { ok: false, profile: this.profile };
    const profile = await this.prepare();
    if (profile === null) return { ok: false, profile: null };
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

  markUntested() {
    if (this.profile !== null) this.recorded.delete(profileKey(this.profile));
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
      const unavailable = PROVIDER_SOURCES.has(value) && this.originsOf(value).length === 0;
      if (unavailable || (locked && !on)) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
    }
    d.sourceHint.textContent = SOURCE_HINT[this.source]();
    d.practiceBadge.hidden = this.source !== "mock";
    const origins = provider ? this.originsOf(this.source) : [];
    d.noRemote.hidden = !(this.source === "remote" && origins.length === 0);
    d.providerBlock.hidden = !provider || origins.length === 0;
    if (provider && origins.length > 0) this.renderProvider(origins, locked);
    this.renderState();
    if (this.variant === "compact") this.renderSummary();
    this.root.dataset.source = this.source;
    this.root.dataset.check = this.check ?? "none";
  }

  renderProvider(origins, locked) {
    const d = this.dom;
    const origin = this.origin();
    const signature = `${this.source}|${origins.map((item) => item.approval_id).join(",")}`;
    if (signature !== this.serviceSignature) {
      this.serviceSignature = signature;
      clear(d.service);
      if (origins.length === 1) {
        d.service.append(
          dotList("setup-conn__service-line", bdi(origins[0].approval_id, "ltr"), bdi(hostOf(origins[0]), "ltr")),
        );
      } else {
        const group = el(
          "div",
          { className: "g-seg setup-conn__origins", attrs: { role: "radiogroup", "aria-labelledby": "conn-service-label" } },
          ...origins.map((item) =>
            el(
              "button",
              {
                attrs: { type: "button", role: "radio", "aria-checked": "false" },
                dataset: { value: item.approval_id },
              },
              dotList("", bdi(item.approval_id, "ltr"), bdi(hostOf(item), "ltr")),
            ),
          ),
        );
        d.service.append(group);
        this.originGroup = radioGroup(group, { onSelect: (value) => this.selectOrigin(value) });
      }
    }
    if (origins.length > 1 && this.originGroup !== undefined && origin !== null) {
      this.originGroup.set(origin.approval_id);
    }
    d.model.disabled = locked;
    const invalid = this.modelValue() === null && (this.modelTouched || this.modelText() !== "");
    if (this.profileRefused) d.modelError.textContent = t("connection.profile_error");
    else if (invalid) d.modelError.textContent = t("connection.model.invalid");
    else d.modelError.textContent = "";
    d.modelError.hidden = d.modelError.textContent === "";
    d.model.setAttribute("aria-invalid", d.modelError.hidden ? "false" : "true");
    d.thinking.hidden = !this.thinkingOff();

    const remote = origin !== null && origin.trust_class === "remote";
    const missing = this.keyMissing();
    d.keyRow.hidden = !remote || (missing === null && this.keyNotice === null);
    d.keyAvailable.hidden = missing !== false;
    d.keyNeeded.hidden = missing !== true;
    clear(d.keyEnv);
    if (remote && missing === true) {
      const variable =
        typeof origin.environment_variable === "string" && origin.environment_variable !== ""
          ? origin.environment_variable
          : KEY_VARIABLE_FALLBACK;
      d.keyEnv.append(
      message("connection.key.env_hint", {
        script: { id: "powershell -ExecutionPolicy Bypass -File scripts\\start-guided-demo.ps1" },
        variable: { id: variable },
      }),
    );
    }
    d.keySave.disabled = locked;
    d.keyInput.disabled = locked;
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
    if (this.originsOf(this.source).length === 0) {
      return { tone: "bad", nodes: [t(this.source === "remote" ? "connection.no_remote" : "connection.untested")] };
    }
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
