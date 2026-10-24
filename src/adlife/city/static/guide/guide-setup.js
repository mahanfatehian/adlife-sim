// AdLife guided demo: the setup page. Three cards (the ad, the people, the AI), tested
// defaults under "More options", and one Run button that names what still blocks it.
import { fmt, href, initI18n, t, tn } from "./guide-i18n.js";
import { announce, bdi, byId, clear, el, getJson, postJson } from "./guide-core.js";
import {
  BOUNDED_SCOPE,
  ensureReady,
  errorCode,
  errorNodes,
  errorStatus,
  fetchAllRuns,
  followWorkbench,
  message,
  mountConnection,
  radioGroup,
  renderActiveJob,
} from "./guide-connection.js";

// Noticed ads a person can send to the AI; the frozen V2 selection policy fixes it.
const PER_PERSON = 4;
// A documented estimate: about 1.2-1.5k input plus at most 256 output tokens per question.
const TOKENS_PER_QUESTION = 1300;
const DEFAULT_CITY = "fictional-grid-v2";
// The rehearsed grid first, then the packaged towns from small to large; any other
// packaged town follows in catalog order.
const TOWN_ORDER = Object.freeze([
  "fictional-grid-v2",
  "fictional-harbor-loop-v2",
  "fictional-river-junction-v2",
  "fictional-old-town-v2",
  "fictional-new-district-v2",
]);
const CAMPAIGN_ID = "fictional-launch";
const DEFAULT_PRODUCT = "fictional-device-launch-v1";
const PRODUCTS = Object.freeze({
  "fictional-device-launch-v1": Object.freeze({ key: "device", interests: ["commuting", "technology"] }),
  "fictional-coffee-pass-v1": Object.freeze({ key: "coffee", interests: ["food", "leisure"] }),
  "fictional-crispy-meal-v1": Object.freeze({ key: "meal", interests: ["commuting", "food"] }),
  "fictional-home-delivery-v1": Object.freeze({ key: "delivery", interests: ["food", "leisure"] }),
  "fictional-bike-share-v1": Object.freeze({ key: "bike", interests: ["commuting", "leisure"] }),
  "fictional-book-box-v1": Object.freeze({ key: "books", interests: ["books", "leisure"] }),
  "fictional-fitness-app-v1": Object.freeze({ key: "fitness", interests: ["cooking", "technology"] }),
  "fictional-music-stream-v1": Object.freeze({ key: "music", interests: ["commuting", "music"] }),
});
const PERSONA_KEYS = Object.freeze({
  "persona-001": "lumo",
  "persona-002": "veyra",
  "persona-003": "orilo",
  "persona-004": "nuvin",
  "persona-005": "pimo",
  "persona-006": "zarel",
  "persona-007": "ketra",
  "persona-008": "tavi",
  "persona-009": "selun",
  "persona-010": "halvi",
});
const PRESETS = Object.freeze({
  contrast: Object.freeze({ "persona-001": 4, "persona-002": 4 }),
  classic: Object.freeze({ "persona-001": 4, "persona-004": 4 }),
  all: Object.freeze(Object.fromEntries(Object.keys(PERSONA_KEYS).map((id) => [id, 3]))),
});
// Ten persona colours; a mark's shape is its colour index % 4 (as on the story map).
const MARK_SHAPES = ["circle", "square", "triangle", "diamond"];
const KNOWN_INTERESTS = new Set([
  "books",
  "commuting",
  "cooking",
  "crafts",
  "food",
  "gardening",
  "leisure",
  "music",
  "technology",
]);
// One billboard spot per packaged town: halfway along a busy two-way road, facing the
// traffic that approaches it in the travel direction (a sign seen from more than 90
// degrees off its facing never counts). A town without a spot offers phone ads only.
const ROADSIDE_SPOT = Object.freeze({
  road_fraction: 0.5,
  max_view_distance_meters: 120,
  frequency_cap_per_agent_per_day: 2,
});
const ROADSIDE = Object.freeze({
  "fictional-grid-v2": Object.freeze({
    road_id: "middle-west",
    travel_direction: "forward",
    side: "right",
    orientation_degrees: 259,
  }),
  "fictional-harbor-loop-v2": Object.freeze({
    road_id: "harbor-arc-north",
    travel_direction: "forward",
    side: "right",
    orientation_degrees: 270,
  }),
  "fictional-river-junction-v2": Object.freeze({
    road_id: "junction-south-arm",
    travel_direction: "forward",
    side: "right",
    orientation_degrees: 0,
  }),
  "fictional-old-town-v2": Object.freeze({
    road_id: "old-bridge",
    travel_direction: "forward",
    side: "right",
    orientation_degrees: 9,
  }),
  "fictional-new-district-v2": Object.freeze({
    road_id: "street-3-de",
    travel_direction: "backward",
    side: "right",
    orientation_degrees: 90,
  }),
});
const ROADSIDE_START = 420;
const ROADSIDE_END = 1140;
const HOUR_STEP = 30;
const DAY_MINUTES = 1440;
const MIN_CAP = 1;
const MAX_CAP = 20;
const MAX_PERSONA_COUNT = 30;
const MAX_RUN_ID = 40;
const RUN_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
const SEED_PATTERN = /^(?:0|[1-9][0-9]{0,18})$/;
const MAX_SEED = 9223372036854775807n;
const AUTO_SEED = 42;
const RENAME_ATTEMPTS = 6;
const RENAME_NOTICE_MS = 1500;
const JOB_ID = /^job-[0-9a-f]{32}$/;
const PROVIDER_SOURCES = new Set(["local", "remote"]);
const MINUS = "\u2212";

const state = {
  loaded: false,
  capabilities: null,
  maxPeople: MAX_PERSONA_COUNT,
  maxDays: 7,
  templates: [],
  personas: [],
  city: DEFAULT_CITY,
  towns: [],
  product: DEFAULT_PRODUCT,
  where: "phone",
  preset: "contrast",
  counts: new Map(),
  days: 1,
  seedAuto: true,
  seed: String(AUTO_SEED),
  from: 420,
  until: 1260,
  cap: 4,
  frequency: 0.01,
  activities: new Set(["commute", "home", "leisure"]),
  price: 1.25,
  interests: new Set(PRODUCTS[DEFAULT_PRODUCT].interests),
  runIdAuto: true,
  runId: "",
  limit: 10,
  conn: null,
  connection: null,
  runs: null,
  runsLoading: null,
  activeJob: null,
  busy: false,
  busyLabel: null,
  serverMarks: new Set(),
  blockersShown: false,
};
const groups = {};

// ----- small formatting helpers ----------------------------------------------------------

/** Minute of the day as HH:MM with this language's digits; 1440 reads 24:00. */
function hhmm(minute) {
  return fmt.clock(minute, 1);
}

function sortedText(values) {
  return [...values].sort();
}

function interestParam(value) {
  return KNOWN_INTERESTS.has(value) ? { key: `interest.${value}` } : { text: value };
}

function validSeed(value) {
  return SEED_PATTERN.test(value) && BigInt(value) <= MAX_SEED;
}

function provider() {
  return state.conn !== null && PROVIDER_SOURCES.has(state.conn.source);
}

function roadside() {
  return ROADSIDE[state.city] ?? null;
}

/** A packaged town's catalog name, or its own name for a town the catalog does not know. */
function cityParam(cityId) {
  if (TOWN_ORDER.includes(cityId)) return { key: `city.${cityId}` };
  const town = state.towns.find((item) => item.cityId === cityId);
  return { text: town?.name ?? cityId };
}

function markClass(colorIndex) {
  const slot = colorIndex % 10;
  const shape = colorIndex % 4 === slot % 4 ? "" : ` g-mark--${MARK_SHAPES[colorIndex % 4]}`;
  return `g-mark--c${slot}${shape}`;
}

function template() {
  return state.templates.find((item) => item.template_id === state.product) ?? state.templates[0];
}

function productKey() {
  return PRODUCTS[template()?.template_id]?.key ?? "study";
}

function scenarioId() {
  return `guided-${productKey()}`;
}

function totalPeople() {
  let total = 0;
  for (const count of state.counts.values()) total += count;
  return total;
}

function defaultRunId() {
  const now = new Date();
  const two = (value) => String(value).padStart(2, "0");
  const date = `${two(now.getMonth() + 1)}${two(now.getDate())}`;
  const time = `${two(now.getHours())}${two(now.getMinutes())}`;
  return `guided-${productKey()}-${date}-${time}`;
}

/** D9: for a provider source, the smallest seed from 42 that no saved run of this study used. */
function takenSeeds() {
  const profile = state.conn?.profile;
  if (!provider() || !profile || !Array.isArray(state.runs)) return new Set();
  const scenario = scenarioId();
  const people = totalPeople();
  return new Set(
    state.runs
      .filter(
        (run) =>
          run.run_schema_version === 10 &&
          run.city_id === state.city &&
          run.scenario_id === scenario &&
          run.agent_count === people &&
          run.provider_profile_id === profile.profile_id &&
          run.provider_revision_sha256 === profile.revision_sha256,
      )
      .map((run) => run.seed),
  );
}

function automaticSeed() {
  const taken = takenSeeds();
  let seed = AUTO_SEED;
  while (taken.has(String(seed))) seed += 1;
  return String(seed);
}

// ----- the draft (spec section 2.4) -----------------------------------------------------

function dayWindows(start, end) {
  return Array.from({ length: state.days }, (_item, index) => ({
    day: index + 1,
    start_minute: start,
    end_minute: end,
  }));
}

function buildDraft(runId) {
  const chosen = template();
  const name = chosen.product_name;
  const phone =
    state.where === "billboard"
      ? null
      : {
          active_windows: dayWindows(state.from, state.until),
          frequency_cap_per_agent_per_day: state.cap,
          eligible_activities: sortedText(state.activities),
          opportunity_probability_per_minute: state.frequency,
        };
  const spot = roadside();
  const billboard =
    state.where === "phone" || spot === null
      ? null
      : {
          road_id: spot.road_id,
          travel_direction: spot.travel_direction,
          road_fraction: ROADSIDE_SPOT.road_fraction,
          side: spot.side,
          orientation_degrees: spot.orientation_degrees,
          max_view_distance_meters: ROADSIDE_SPOT.max_view_distance_meters,
          frequency_cap_per_agent_per_day: ROADSIDE_SPOT.frequency_cap_per_agent_per_day,
          active_windows: dayWindows(ROADSIDE_START, ROADSIDE_END),
        };
  const personas = state.personas
    .filter((persona) => (state.counts.get(persona.persona_id) ?? 0) > 0)
    .map((persona) => ({ ...structuredClone(persona), count: state.counts.get(persona.persona_id) }));
  const conn = state.conn;
  const cognition = provider()
    ? {
        effect_scope: BOUNDED_SCOPE,
        mode: conn.source,
        provider_profile_id: conn.profile.profile_id,
        provider_revision_sha256: conn.profile.revision_sha256,
        funded_response_limit: state.limit,
      }
    : {
        effect_scope: BOUNDED_SCOPE,
        mode: conn.source,
        provider_profile_id: null,
        provider_revision_sha256: null,
        funded_response_limit: state.capabilities?.offline_funded_response_limit ?? 60,
      };
  return {
    schema_version: 4,
    city_id: state.city,
    scenario: {
      scenario_id: scenarioId(),
      name: `Guided demo study - ${name}`,
      campaign: {
        campaign_id: CAMPAIGN_ID,
        name: `${name} campaign`,
        creative_template_id: chosen.template_id,
        target_interests: sortedText(state.interests),
        relative_price: state.price,
      },
      phone,
      roadside: billboard,
    },
    population: { personas },
    settings: {
      run_id: runId,
      agent_count: totalPeople(),
      days: state.days,
      seed: state.seed,
      response_mode: "deterministic-rules",
    },
    cognition,
  };
}

// ----- validation, blockers and field targets -------------------------------------------

function clientErrors() {
  const errors = new Map();
  if (!validSeed(state.seed)) errors.set("seed", t("setup.error.field"));
  if (!RUN_ID_PATTERN.test(state.runId)) {
    errors.set("run", t("setup.more.run_id.invalid", { max: MAX_RUN_ID }));
  }
  if (state.where !== "billboard") {
    if (state.until <= state.from) errors.set("hours", t("setup.more.hours.invalid"));
    if (state.activities.size === 0) errors.set("activities", t("setup.more.activities.invalid"));
  }
  if (state.interests.size === 0) errors.set("interests", t("setup.more.interests.invalid"));
  return errors;
}

const TARGETS = Object.freeze({
  town: { error: "town-error", more: false, focus: () => focusTown() },
  product: { error: "ad-error", more: false, focus: () => groups.products?.focus() },
  where: { error: "ad-error", more: false, focus: () => groups.where.focus() },
  people: { error: "people-error", more: false, focus: () => focusPeople() },
  ai: { error: null, more: false, focus: () => state.connection?.focusSource() },
  days: { error: "more-days-error", more: true, focus: () => byId("more-days-up").focus() },
  seed: { error: "more-seed-error", more: true, focus: () => byId("more-seed").focus() },
  hours: { error: "more-hours-error", more: true, focus: () => byId("more-from-down").focus() },
  cap: { error: "more-cap-error", more: true, focus: () => byId("more-cap-up").focus() },
  frequency: { error: "more-frequency-error", more: true, focus: () => groups.frequency.focus() },
  activities: {
    error: "more-activities-error",
    more: true,
    focus: () => byId("more-activities").querySelector("input")?.focus(),
  },
  price: { error: "more-price-error", more: true, focus: () => groups.price.focus() },
  interests: {
    error: "more-interests-error",
    more: true,
    focus: () => byId("more-interests").querySelector("input")?.focus(),
  },
  run: { error: "more-run-error", more: true, focus: () => byId("more-run-id").focus() },
});

/** Map one refused field path from the server to the control that owns it. */
function fieldTarget(field) {
  if (field === "city_id") return "town";
  if (field.startsWith("population.") || field.startsWith("settings.agent_count")) return "people";
  if (field.startsWith("scenario.campaign.relative_price")) return "price";
  if (field.startsWith("scenario.campaign.target_interests")) return "interests";
  if (field.startsWith("scenario.campaign.")) return "product";
  if (field.startsWith("scenario.phone.frequency_cap")) return "cap";
  if (field.startsWith("scenario.phone.opportunity_probability")) return "frequency";
  if (field.startsWith("scenario.phone.eligible_activities")) return "activities";
  if (field.startsWith("scenario.phone")) return "hours";
  if (field.startsWith("scenario.roadside") || field === "scenario") return "where";
  if (field === "settings.seed") return "seed";
  if (field === "settings.days") return "days";
  if (field === "settings.run_id") return "run";
  if (field.startsWith("cognition")) return "ai";
  return null;
}

function focusTarget(name) {
  const target = TARGETS[name];
  if (target === undefined) return;
  if (target.more) byId("setup-more").open = true;
  target.focus();
}

function focusTown() {
  if (groups.towns) groups.towns.focus();
  else groups.products?.focus();
}

function focusPeople() {
  const button = byId("people-list").querySelector(".setup-stepper__button:not([disabled])");
  if (button !== null) button.focus();
  else groups.preset.focus();
}

function blockers() {
  const list = [];
  if (totalPeople() === 0) {
    list.push({ text: t("setup.blocked.people"), target: "people" });
  }
  if (provider() && !state.conn.addressValid) {
    list.push({ text: t("setup.blocked.address"), target: "address" });
  } else if (provider() && !state.conn.modelValid) {
    list.push({ text: t("setup.blocked.model"), target: "model" });
  }
  const errors = clientErrors();
  const marked = [...state.serverMarks].filter((name) => TARGETS[name]?.more);
  if (errors.size > 0 || marked.length > 0) {
    list.push({ text: t("setup.blocked.invalid"), target: [...errors.keys(), ...marked][0] });
  }
  if (state.activeJob !== null) {
    list.push({
      text: t("setup.blocked.busy"),
      target: null,
      link: { text: t("setup.blocked.busy.open"), href: href(`/guide/jobs/${state.activeJob.job_id}`) },
    });
  }
  return list;
}

function renderBlockers({ focus = false } = {}) {
  const box = byId("setup-blockers");
  const list = byId("setup-blockers-list");
  const items = state.blockersShown ? blockers() : [];
  clear(list);
  for (const blocker of items) {
    const item = el("li", { className: "setup-blockers__item" });
    if (blocker.target === null) {
      item.append(el("span", { text: blocker.text }));
    } else {
      const fix = el("button", {
        className: "setup-linkbutton",
        text: blocker.text,
        attrs: { type: "button" },
      });
      fix.addEventListener("click", () => {
        if (blocker.target === "model") state.connection?.focusModel();
        else if (blocker.target === "address") state.connection?.focusAddress();
        else focusTarget(blocker.target);
      });
      item.append(fix);
    }
    if (blocker.link !== undefined) {
      item.append(
        el("a", {
          className: "g-btn g-btn--secondary setup-blockers__open",
          text: blocker.link.text,
          attrs: { href: blocker.link.href },
        }),
      );
    }
    list.append(item);
  }
  box.hidden = items.length === 0;
  if (items.length === 0) state.blockersShown = false;
  if (focus && items.length > 0) box.focus();
}

// ----- rendering ------------------------------------------------------------------------

function setError(id, text) {
  if (id === null) return;
  const node = byId(id);
  node.textContent = text ?? "";
  node.hidden = !text;
}

function renderErrors() {
  const errors = clientErrors();
  const texts = new Map();
  for (const [name, text] of errors) texts.set(TARGETS[name].error, text);
  for (const name of state.serverMarks) {
    const id = TARGETS[name]?.error;
    if (id && !texts.has(id)) texts.set(id, t("setup.error.field"));
  }
  for (const target of Object.values(TARGETS)) {
    if (target.error !== null) setError(target.error, texts.get(target.error) ?? null);
  }
  byId("more-seed").setAttribute("aria-invalid", texts.has("more-seed-error") ? "true" : "false");
  byId("more-run-id").setAttribute("aria-invalid", texts.has("more-run-error") ? "true" : "false");
}

function renderTown() {
  groups.towns?.set(state.city);
  const offersBillboard = roadside() !== null;
  for (const value of ["billboard", "both"]) {
    byId("ad-where").querySelector(`[data-value="${value}"]`).hidden = !offersBillboard;
  }
  byId("ad-where-note").hidden = offersBillboard;
}

function renderAd() {
  groups.products?.set(state.product);
  groups.where.set(state.where);
  const aimed = byId("ad-aimed");
  clear(aimed);
  if (state.interests.size > 0) {
    aimed.append(
      message("setup.ad.aimed_at", {
        interests: { list: sortedText(state.interests).map(interestParam) },
      }),
    );
  }
  aimed.hidden = state.interests.size === 0;
}

function renderPeople() {
  const total = totalPeople();
  for (const row of byId("people-list").querySelectorAll(".setup-person")) {
    const id = row.dataset.personaId;
    const count = state.counts.get(id) ?? 0;
    const name = row.dataset.name ?? "";
    row.dataset.count = String(count);
    const output = row.querySelector("output");
    output.textContent = fmt.int(count);
    output.setAttribute("aria-label", t("setup.people.count", { name, count }));
    row.querySelector('[data-step="down"]').disabled = count <= 0;
    row.querySelector('[data-step="up"]').disabled =
      count >= MAX_PERSONA_COUNT || total >= state.maxPeople;
  }
  byId("people-total").textContent = tn("setup.people.total", total);
  const max = byId("people-max");
  max.textContent = t("setup.people.max", { max: state.maxPeople });
  max.hidden = total < state.maxPeople;
  const custom = byId("people-preset").querySelector('[data-value="custom"]');
  custom.hidden = state.preset !== "custom";
  groups.preset.set(state.preset);
}

function renderAi() {
  const offline = !provider();
  byId("ai-limit-block").hidden = offline || state.conn === null;
  byId("ai-offline").hidden = !offline || state.conn === null;
  groups.limits?.set(String(state.limit));
  byId("ai-estimate").textContent = t("setup.ai.limit.estimate", {
    tokens: state.limit * TOKENS_PER_QUESTION,
  });
  byId("ai-explain").textContent = t("setup.ai.limit.explain", {
    per_person: PER_PERSON,
    // What is sent is bounded by the chosen limit, not only by the server's selection cap.
    per_run: Math.min(state.limit, state.capabilities?.selected_requests_per_run ?? 60),
  });
  byId("ai-cache").hidden = !(provider() && !state.seedAuto && takenSeeds().has(state.seed));
}

function renderOptions() {
  if (state.seedAuto) {
    state.seed = automaticSeed();
    if (document.activeElement !== byId("more-seed")) byId("more-seed").value = state.seed;
  }
  if (state.runIdAuto && document.activeElement !== byId("more-run-id")) {
    byId("more-run-id").value = state.runId;
  }
  byId("more-days").textContent = fmt.int(state.days);
  byId("more-days-down").disabled = state.days <= 1;
  byId("more-days-up").disabled = state.days >= state.maxDays;
  byId("more-from").textContent = hhmm(state.from);
  byId("more-until").textContent = hhmm(state.until);
  byId("more-from-down").disabled = state.from <= 0;
  byId("more-from-up").disabled = state.from + HOUR_STEP >= state.until;
  byId("more-until-down").disabled = state.until - HOUR_STEP <= state.from;
  byId("more-until-up").disabled = state.until >= DAY_MINUTES;
  byId("more-cap").textContent = fmt.int(state.cap);
  byId("more-cap-down").disabled = state.cap <= MIN_CAP;
  byId("more-cap-up").disabled = state.cap >= MAX_CAP;
  groups.frequency.set(String(state.frequency));
  groups.price.set(String(state.price));
  byId("more-price-help").textContent = t("setup.more.price.help", { typical: fmt.dec(1, 1) });
  for (const input of byId("more-activities").querySelectorAll("input")) {
    input.checked = state.activities.has(input.value);
  }
  for (const input of byId("more-interests").querySelectorAll("input")) {
    input.checked = state.interests.has(input.value);
  }
  const phoneOnly = state.where === "billboard";
  for (const id of ["more-hours-field", "more-cap-field", "more-frequency-field", "more-activities-field"]) {
    byId(id).hidden = phoneOnly;
  }
}

function idParam(value, valid) {
  if (valid) return { id: value };
  return { text: value === "" ? "\u2014" : value.slice(0, 40) };
}

function renderSummary() {
  const phone = state.where !== "billboard";
  const node = byId("setup-summary");
  clear(node);
  node.append(
    message("setup.summary", {
      city: cityParam(state.city),
      days: { key: "common.days", count: state.days },
      from: { clock: phone ? state.from : ROADSIDE_START },
      until: { clock: phone ? state.until : ROADSIDE_END },
      cap: { int: phone ? state.cap : ROADSIDE_SPOT.frequency_cap_per_agent_per_day },
      // A short number reads in the page's digits (Persian too); anything else stays an id.
      seed: /^[0-9]{1,4}$/.test(state.seed)
        ? { int: Number(state.seed) }
        : idParam(state.seed, validSeed(state.seed)),
      run_id: idParam(state.runId, RUN_ID_PATTERN.test(state.runId)),
    }),
  );
}

function renderRunButton() {
  const button = byId("setup-run");
  const blocked = !state.loaded || state.conn === null || blockers().length > 0;
  const label =
    state.busyLabel ??
    (provider() && state.conn.check !== "ok" ? t("setup.run_with_test") : t("setup.run"));
  button.textContent = label;
  button.setAttribute("aria-disabled", blocked || state.busy ? "true" : "false");
  if (state.busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

function refresh() {
  if (!state.loaded) {
    renderRunButton();
    return;
  }
  renderTown();
  renderAd();
  renderPeople();
  renderAi();
  renderOptions();
  renderErrors();
  renderSummary();
  renderRunButton();
  if (state.blockersShown) renderBlockers();
}

function setRunNote(...nodes) {
  const note = byId("setup-run-note");
  clear(note);
  note.append(...nodes);
  note.hidden = nodes.length === 0;
}

function showAiError(nodes) {
  const box = byId("ai-error");
  clear(box);
  box.append(...nodes);
  box.hidden = nodes.length === 0;
  if (nodes.length > 0) box.focus();
}

// ----- building the dynamic parts ------------------------------------------------------

/** Known products in the spec order (the rehearsed default first), then any others. */
function productOrder(item) {
  const index = Object.keys(PRODUCTS).indexOf(item.template_id);
  return index < 0 ? Object.keys(PRODUCTS).length : index;
}

function buildProducts() {
  const group = byId("ad-products");
  clear(group);
  const ordered = state.templates
    .map((item, index) => ({ item, index }))
    .sort((left, right) => productOrder(left.item) - productOrder(right.item) || left.index - right.index)
    .map(({ item }) => item);
  for (const item of ordered) {
    const known = PRODUCTS[item.template_id] !== undefined;
    const category = known ? t(`creative.${item.template_id}.category`) : bdi(item.product_category);
    const text = known ? t(`creative.${item.template_id}.message`) : bdi(item.message);
    group.append(
      el(
        "button",
        {
          className: "g-choice setup-product",
          attrs: { type: "button", role: "radio", "aria-checked": "false" },
          dataset: { value: item.template_id },
        },
        el(
          "span",
          { className: "setup-product__head" },
          el("span", { className: "g-choice__title setup-product__name" }, bdi(item.product_name)),
          el("span", { className: "g-choice__meta setup-product__category" }, category),
        ),
        el("span", { className: "setup-product__message" }, text),
      ),
    );
  }
  groups.products = radioGroup(group, { onSelect: (value) => selectProduct(value) });
}

// ----- the town picker -------------------------------------------------------------------

const CITY_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,79}$/;
const TOWN_ATTEMPTS = 2;
const TOWN_RETRY_MS = 400;
const MAJOR_ROADS = new Set(["motorway", "trunk", "primary"]);
const PREVIEW_PADDING = 6;

function townOrder(left, right) {
  const rank = (town) => {
    const index = TOWN_ORDER.indexOf(town.cityId);
    return index < 0 ? TOWN_ORDER.length : index;
  };
  return rank(left) - rank(right) || left.index - right.index;
}

/** Nodes by ID and each road as a list of [longitude, latitude] points, or null. */
function previewShape(pack) {
  if (!Array.isArray(pack?.nodes) || !Array.isArray(pack.roads)) return null;
  const nodes = new Map();
  for (const node of pack.nodes) {
    if (typeof node?.node_id === "string" && Number.isFinite(node.longitude) && Number.isFinite(node.latitude)) {
      nodes.set(node.node_id, [node.longitude, node.latitude]);
    }
  }
  const roads = [];
  for (const road of pack.roads) {
    const start = nodes.get(road?.source_node);
    const end = nodes.get(road?.target_node);
    if (start === undefined || end === undefined) continue;
    const shape = (Array.isArray(road.shape) ? road.shape : [])
      .filter((point) => Number.isFinite(point?.longitude) && Number.isFinite(point?.latitude))
      .map((point) => [point.longitude, point.latitude]);
    roads.push({ kind: road.kind, points: [start, ...shape, end] });
  }
  return roads.length > 0 ? { nodes: [...nodes.values()], roads } : null;
}

/** Draw one town's streets into its preview canvas: geography never mirrors. */
function drawPreview(canvas, shape) {
  const box = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(box.width));
  const height = Math.max(1, Math.round(box.height));
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  if (!context) return;
  const styles = getComputedStyle(canvas);
  const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = token("--map-bg", "#F8FAFC");
  context.fillRect(0, 0, width, height);
  const points = [...shape.nodes, ...shape.roads.flatMap((road) => road.points)];
  const west = Math.min(...points.map((point) => point[0]));
  const east = Math.max(...points.map((point) => point[0]));
  const south = Math.min(...points.map((point) => point[1]));
  const north = Math.max(...points.map((point) => point[1]));
  const scaleX = Math.cos((((south + north) / 2) * Math.PI) / 180);
  const worldWidth = Math.max((east - west) * scaleX, 1e-9);
  const worldHeight = Math.max(north - south, 1e-9);
  const usableWidth = Math.max(width - PREVIEW_PADDING * 2, 1);
  const usableHeight = Math.max(height - PREVIEW_PADDING * 2, 1);
  const scale = Math.min(usableWidth / worldWidth, usableHeight / worldHeight);
  const offsetX = PREVIEW_PADDING + (usableWidth - worldWidth * scale) / 2;
  const offsetY = PREVIEW_PADDING + (usableHeight - worldHeight * scale) / 2;
  const project = ([longitude, latitude]) => [
    offsetX + (longitude - west) * scaleX * scale,
    offsetY + (north - latitude) * scale,
  ];
  context.lineCap = "round";
  context.lineJoin = "round";
  // Minor streets first, so the main roads draw over every crossing.
  for (const major of [false, true]) {
    context.strokeStyle = major ? token("--ink-3", "#4B5563") : token("--map-road", "#A3AEC2");
    context.lineWidth = major ? 2.5 : 1.5;
    for (const road of shape.roads) {
      if (MAJOR_ROADS.has(road.kind) !== major) continue;
      context.setLineDash(road.kind === "path" ? [2, 3] : []);
      context.beginPath();
      road.points.forEach((point, index) => {
        const [x, y] = project(point);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    }
  }
  context.setLineDash([]);
  context.fillStyle = token("--ink-3", "#4B5563");
  for (const node of shape.nodes) {
    const [x, y] = project(node);
    context.beginPath();
    context.arc(x, y, 1.5, 0, Math.PI * 2);
    context.fill();
  }
}

function drawPreviews() {
  for (const canvas of byId("town-list").querySelectorAll("canvas")) {
    const town = state.towns.find((item) => item.cityId === canvas.dataset.city);
    if (town?.shape) drawPreview(canvas, town.shape);
  }
}

function townButton(town) {
  const known = TOWN_ORDER.includes(town.cityId);
  return el(
    "button",
    {
      className: "g-choice setup-town",
      attrs: { type: "button", role: "radio", "aria-checked": "false" },
      dataset: { value: town.cityId },
    },
    el("canvas", {
      className: "setup-town__map",
      attrs: { "aria-hidden": "true", dir: "ltr" },
      dataset: { city: town.cityId },
    }),
    el("span", { className: "g-choice__title setup-town__name" }, known ? t(`city.${town.cityId}`) : bdi(town.name)),
    el("span", { className: "g-choice__meta setup-town__stats" }),
  );
}

/** One town's street count and preview, once its graph has arrived. */
function fillTown(town) {
  const button = byId("town-list").querySelector(`[data-value="${town.cityId}"]`);
  if (button === null) return;
  const stats = button.querySelector(".setup-town__stats");
  clear(stats);
  if (town.nodes !== null && town.roads !== null) {
    stats.append(message("setup.town.stats", { nodes: { int: town.nodes }, roads: { int: town.roads } }));
  }
  const canvas = button.querySelector("canvas");
  if (town.shape) drawPreview(canvas, town.shape);
}

/** The rehearsed packaged towns at once, so the page never shifts; previews follow. */
function buildTowns() {
  state.towns = TOWN_ORDER.map((cityId, index) => ({
    cityId,
    name: null,
    index,
    shape: null,
    nodes: null,
    roads: null,
  }));
  const list = byId("town-list");
  clear(list);
  for (const town of state.towns) list.append(townButton(town));
  groups.towns = radioGroup(list, { onSelect: (value) => selectTown(value) });
  byId("setup-town").hidden = false;
  if (typeof ResizeObserver === "function") {
    let width = list.getBoundingClientRect().width;
    new ResizeObserver(() => {
      const next = list.getBoundingClientRect().width;
      if (Math.abs(next - width) >= 1) {
        width = next;
        drawPreviews();
      }
    }).observe(list);
  }
}

/** Without a verified town listing the picker offers nothing: the grid alone is used. */
function dropTowns() {
  byId("setup-town").hidden = true;
  state.towns = [];
  groups.towns = null;
  if (state.city !== DEFAULT_CITY) {
    state.city = DEFAULT_CITY;
    clearMark("town");
    refresh();
  }
}

/** Keep only the towns the server lists as packaged, then draw each one's streets. */
async function loadTowns() {
  let listing = null;
  for (let attempt = 1; listing === null; attempt += 1) {
    try {
      listing = await getJson("/api/catalog/cities");
    } catch (_error) {
      // One retry rides out a registry that another request held for a moment.
      if (attempt >= TOWN_ATTEMPTS) {
        dropTowns();
        return;
      }
      await pause(TOWN_RETRY_MS);
    }
  }
  const rows = (Array.isArray(listing?.cities) ? listing.cities : []).filter(
    (row) =>
      row?.storage_origin === "package" &&
      typeof row.city_id === "string" &&
      CITY_ID_PATTERN.test(row.city_id) &&
      typeof row.display_name === "string",
  );
  if (!rows.some((row) => row.city_id === DEFAULT_CITY)) {
    dropTowns();
    return;
  }
  state.towns = rows
    .map((row, index) => ({
      cityId: row.city_id,
      name: row.display_name,
      index,
      shape: null,
      nodes: null,
      roads: null,
    }))
    .sort(townOrder);
  const list = byId("town-list");
  const listed = new Set(state.towns.map((town) => town.cityId));
  for (const button of [...list.querySelectorAll('[role="radio"]')]) {
    if (!listed.has(button.dataset.value)) button.remove();
  }
  for (const town of state.towns) {
    if (list.querySelector(`[data-value="${town.cityId}"]`) === null) list.append(townButton(town));
  }
  if (!listed.has(state.city)) selectTown(DEFAULT_CITY);
  else groups.towns?.set(state.city);
  // One town at a time: each read opens the workspace city registry.
  for (const town of [...state.towns]) {
    const detail = await getJson(`/api/catalog/cities/${town.cityId}`).catch(() => null);
    const pack = detail?.city_id === town.cityId ? detail.pack : null;
    town.shape = previewShape(pack);
    town.nodes = Array.isArray(pack?.nodes) ? pack.nodes.length : null;
    town.roads = Array.isArray(pack?.roads) ? pack.roads.length : null;
    fillTown(town);
  }
}

/** The ten template personas keep colours 0-9 in catalog order; others follow from 10. */
function personaLook(persona, others) {
  const key = PERSONA_KEYS[persona.persona_id] ?? null;
  const templates = Object.values(PERSONA_KEYS);
  const index =
    key === null ? templates.length + others.indexOf(persona.persona_id) : templates.indexOf(key);
  return { key, colorIndex: index };
}

function buildPeople() {
  const list = byId("people-list");
  clear(list);
  const others = state.personas
    .filter((persona) => PERSONA_KEYS[persona.persona_id] === undefined)
    .map((persona) => persona.persona_id);
  for (const persona of state.personas) {
    const look = personaLook(persona, others);
    const name = look.key === null ? persona.display_name : t(`persona.${look.key}.name`);
    const interests = (Array.isArray(persona.interests) ? persona.interests : []).slice(0, 8);
    const title = el("p", { className: "setup-person__name" }, look.key === null ? bdi(name) : name);
    const about = el("div", { className: "setup-person__about" });
    if (look.key !== null) {
      about.append(el("p", { className: "setup-person__tagline", text: t(`persona.${look.key}.tagline`) }));
    }
    if (interests.length > 0) {
      about.append(
        el(
          "p",
          { className: "setup-person__likes" },
          message("setup.people.likes", { interests: { list: interests.map(interestParam) } }),
        ),
      );
    }
    const down = el("button", {
      className: "setup-stepper__button",
      text: MINUS,
      attrs: { type: "button", "aria-label": t("setup.people.remove", { name }) },
      dataset: { step: "down" },
    });
    const up = el("button", {
      className: "setup-stepper__button",
      text: "+",
      attrs: { type: "button", "aria-label": t("setup.people.add", { name }) },
      dataset: { step: "up" },
    });
    down.addEventListener("click", () => stepPersona(persona.persona_id, -1));
    up.addEventListener("click", () => stepPersona(persona.persona_id, 1));
    list.append(
      el(
        "li",
        { className: "setup-person", dataset: { personaId: persona.persona_id, name } },
        el("span", {
          className: `g-mark g-mark--large ${markClass(look.colorIndex)} setup-person__mark`,
          attrs: { "aria-hidden": "true" },
        }),
        title,
        el(
          "div",
          { className: "g-stepper setup-person__stepper" },
          down,
          el("output"),
          up,
        ),
        about,
      ),
    );
  }
}

function buildLimits() {
  const group = byId("ai-limit");
  clear(group);
  const limits = Array.isArray(state.capabilities?.funded_response_limits)
    ? state.capabilities.funded_response_limits.filter((value) => Number.isInteger(value) && value > 0)
    : [10];
  if (!limits.includes(state.limit)) state.limit = limits[0] ?? 10;
  for (const value of limits) {
    group.append(
      el("button", {
        text: tn("setup.ai.limit.option", value),
        attrs: { type: "button", role: "radio", "aria-checked": "false" },
        dataset: { value: String(value) },
      }),
    );
  }
  groups.limits = radioGroup(group, {
    onSelect: (value) => {
      state.limit = Number(value);
      refresh();
    },
  });
}

// ----- user actions ---------------------------------------------------------------------

function clearMark(...names) {
  for (const name of names) state.serverMarks.delete(name);
}

function selectTown(value) {
  if (!state.towns.some((town) => town.cityId === value)) return;
  state.city = value;
  if (roadside() === null) state.where = "phone";
  clearMark("town", "where");
  refresh();
}

function selectProduct(value) {
  if (!state.templates.some((item) => item.template_id === value)) return;
  state.product = value;
  state.interests = new Set(PRODUCTS[value]?.interests ?? state.interests);
  if (state.runIdAuto) state.runId = defaultRunId();
  clearMark("product", "interests");
  refresh();
}

function applyPreset(name) {
  const counts = PRESETS[name];
  if (counts === undefined) {
    state.preset = "custom";
    refresh();
    return;
  }
  state.preset = name;
  for (const persona of state.personas) {
    state.counts.set(persona.persona_id, counts[persona.persona_id] ?? 0);
  }
  clearMark("people");
  refresh();
}

function stepPersona(personaId, delta) {
  const count = state.counts.get(personaId) ?? 0;
  const next = Math.min(MAX_PERSONA_COUNT, Math.max(0, count + delta));
  if (delta > 0 && totalPeople() >= state.maxPeople) return;
  state.counts.set(personaId, next);
  state.preset = "custom";
  clearMark("people");
  refresh();
}

function stepper(id, onStep) {
  byId(`${id}-down`).addEventListener("click", () => onStep(-1));
  byId(`${id}-up`).addEventListener("click", () => onStep(1));
}

function wireOptions() {
  stepper("more-days", (delta) => {
    state.days = Math.min(state.maxDays, Math.max(1, state.days + delta));
    clearMark("days", "hours");
    refresh();
  });
  stepper("more-from", (delta) => {
    state.from = Math.min(state.until - HOUR_STEP, Math.max(0, state.from + delta * HOUR_STEP));
    clearMark("hours");
    refresh();
  });
  stepper("more-until", (delta) => {
    state.until = Math.min(DAY_MINUTES, Math.max(state.from + HOUR_STEP, state.until + delta * HOUR_STEP));
    clearMark("hours");
    refresh();
  });
  stepper("more-cap", (delta) => {
    state.cap = Math.min(MAX_CAP, Math.max(MIN_CAP, state.cap + delta));
    clearMark("cap");
    refresh();
  });
  groups.frequency = radioGroup(byId("more-frequency"), {
    onSelect: (value) => {
      state.frequency = Number(value);
      clearMark("frequency");
      refresh();
    },
  });
  groups.price = radioGroup(byId("more-price"), {
    onSelect: (value) => {
      state.price = Number(value);
      clearMark("price");
      refresh();
    },
  });
  byId("more-activities").addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (input.checked) state.activities.add(input.value);
    else state.activities.delete(input.value);
    clearMark("activities");
    refresh();
  });
  byId("more-interests").addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (input.checked) state.interests.add(input.value);
    else state.interests.delete(input.value);
    clearMark("interests");
    refresh();
  });
  const seed = byId("more-seed");
  seed.addEventListener("input", () => {
    state.seedAuto = false;
    state.seed = seed.value.trim();
    clearMark("seed");
    refresh();
  });
  seed.addEventListener("blur", () => {
    if (seed.value.trim() === "") {
      state.seedAuto = true;
      refresh();
    }
  });
  byId("more-seed-new").addEventListener("click", () => {
    const current = validSeed(state.seed) ? BigInt(state.seed) : BigInt(AUTO_SEED) - 1n;
    state.seed = String(current >= MAX_SEED ? 0n : current + 1n);
    state.seedAuto = false;
    seed.value = state.seed;
    clearMark("seed");
    refresh();
  });
  const runId = byId("more-run-id");
  runId.addEventListener("input", () => {
    state.runIdAuto = false;
    state.runId = runId.value.trim();
    clearMark("run");
    refresh();
  });
  runId.addEventListener("blur", () => {
    if (runId.value.trim() === "") {
      state.runIdAuto = true;
      state.runId = defaultRunId();
      runId.value = state.runId;
      refresh();
    }
  });
  byId("setup-more-open").addEventListener("click", () => {
    const more = byId("setup-more");
    more.open = true;
    const summary = byId("setup-more-summary");
    summary.scrollIntoView({ block: "start" });
    summary.focus();
  });
}

function wireCards() {
  groups.where = radioGroup(byId("ad-where"), {
    onSelect: (value) => {
      state.where = value;
      clearMark("where", "hours", "cap", "frequency", "activities");
      refresh();
    },
  });
  groups.preset = radioGroup(byId("people-preset"), { onSelect: (value) => applyPreset(value) });
  byId("setup-run").addEventListener("click", () => {
    void run();
  });
}

function onConnection(payload) {
  state.conn = payload;
  clearMark("ai");
  showAiError([]);
  refresh();
}

function onActiveJob(job) {
  state.activeJob = job !== null && typeof job?.job_id === "string" ? job : null;
  renderActiveJob(byId("active-job"), state.activeJob, href);
  refresh();
}

// ----- running --------------------------------------------------------------------------

function pause(milliseconds) {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function renamed(base, attempt) {
  const suffix = `-${attempt}`;
  return `${base.slice(0, MAX_RUN_ID - suffix.length)}${suffix}`;
}

async function showBusyJob() {
  try {
    const workbench = await getJson("/api/workbench");
    onActiveJob(workbench?.active_job ?? null);
  } catch (_error) {
    // The blocker below still explains the refusal.
  }
  state.blockersShown = true;
  if (state.activeJob === null) setRunNote(t("setup.blocked.busy"));
  renderBlockers({ focus: true });
}

function markFields(fields) {
  const names = [];
  for (const field of Object.keys(fields ?? {})) {
    const name = fieldTarget(field);
    if (name !== null && !names.includes(name)) names.push(name);
  }
  if (names.length === 0) return false;
  for (const name of names) state.serverMarks.add(name);
  refresh();
  if (names.includes("ai")) showAiError([t("setup.error.field")]);
  setRunNote(t("setup.error.field"));
  focusTarget(names[0]);
  return true;
}

function handleSubmitError(error) {
  const code = errorCode(error);
  if (code === "provider-compatibility-required") {
    state.connection?.markUntested();
    showAiError([t("setup.error.compat")]);
    return;
  }
  if (code === "job-busy") {
    void showBusyJob();
    return;
  }
  if (code === "credential-store-unavailable") {
    showAiError([t("hint.credential-unavailable")]);
    return;
  }
  if (code === "provider-endpoint-refused") {
    showAiError([t("hint.endpoint-policy-refused")]);
    return;
  }
  if (code === "run-conflict" && markFields({ "settings.run_id": "" })) return;
  if (errorStatus(error) === 422 && markFields(error.fields)) return;
  setRunNote(...errorNodes(error));
}

async function submit() {
  const base = state.runId;
  for (let attempt = 1; attempt <= RENAME_ATTEMPTS; attempt += 1) {
    const runId = attempt === 1 ? base : renamed(base, attempt);
    let response = null;
    try {
      response = await postJson("/api/jobs", buildDraft(runId));
    } catch (error) {
      if (errorCode(error) === "run-conflict" && attempt < RENAME_ATTEMPTS) continue;
      handleSubmitError(error);
      return;
    }
    const jobId = response?.job?.job_id;
    if (typeof jobId !== "string" || !JOB_ID.test(jobId)) {
      setRunNote(t("common.error.unexpected"));
      return;
    }
    if (runId !== base) {
      state.runId = runId;
      state.runIdAuto = false;
      byId("more-run-id").value = runId;
      refresh();
      setRunNote(message("setup.error.renamed", { run_id: { id: runId } }));
      announce(t("setup.error.renamed", { run_id: runId }), { force: true });
      await pause(RENAME_NOTICE_MS);
    }
    location.assign(href(`/guide/jobs/${jobId}`));
    return;
  }
}

async function run() {
  if (state.busy || !state.loaded || state.conn === null) return;
  const list = blockers();
  if (list.length > 0) {
    state.blockersShown = true;
    renderBlockers({ focus: true });
    return;
  }
  state.blockersShown = false;
  renderBlockers();
  setRunNote();
  showAiError([]);
  state.busy = true;
  try {
    if (provider() && state.conn.check !== "ok") {
      state.busyLabel = t("setup.testing");
      refresh();
      const ready = await ensureReady();
      if (!ready.ok) {
        if (ready.needsKey === true) setRunNote(t("setup.blocked.key"));
        return;
      }
    }
    state.busyLabel = t("setup.starting");
    refresh();
    if (state.runsLoading !== null) await state.runsLoading;
    refresh();
    if (blockers().length > 0) {
      state.blockersShown = true;
      renderBlockers({ focus: true });
      return;
    }
    await submit();
  } catch (error) {
    setRunNote(...errorNodes(error));
  } finally {
    state.busy = false;
    state.busyLabel = null;
    refresh();
  }
}

// ----- boot -----------------------------------------------------------------------------

async function main() {
  await initI18n();
  wireCards();
  wireOptions();
  buildTowns();
  refresh();
  state.runsLoading = fetchAllRuns().then(
    (runs) => {
      state.runs = runs;
      refresh();
    },
    () => {
      state.runs = [];
    },
  );
  const connected = mountConnection(byId("setup-connection"), {
    variant: "compact",
    onChange: onConnection,
  }).then((handle) => {
    state.connection = handle;
  });
  // The town listing and previews never hold up the page: the grid works without them.
  // They load after the AI connection has read its provider registry, so the two
  // workspace registries are not opened at the same moment.
  const towns = connected
    .catch(() => undefined)
    .then(() => loadTowns())
    .catch(() => dropTowns());
  try {
    const [workbench, creatives, personas] = await Promise.all([
      followWorkbench(onActiveJob),
      getJson("/api/creative-templates"),
      getJson("/api/persona-templates"),
    ]);
    state.capabilities = workbench?.capabilities?.bounded_cognition ?? null;
    state.maxPeople = workbench?.limits?.agent_count?.maximum ?? MAX_PERSONA_COUNT;
    state.maxDays = workbench?.limits?.days?.maximum ?? 7;
    state.templates = (Array.isArray(creatives?.templates) ? creatives.templates : []).filter(
      (item) => typeof item?.template_id === "string" && typeof item.product_name === "string",
    );
    state.personas = (Array.isArray(personas?.templates) ? personas.templates : []).filter(
      (item) => typeof item?.persona_id === "string",
    );
    if (!state.templates.some((item) => item.template_id === state.product)) {
      state.product = state.templates[0]?.template_id ?? DEFAULT_PRODUCT;
      state.interests = new Set(PRODUCTS[state.product]?.interests ?? ["commuting"]);
    }
    buildProducts();
    buildPeople();
    buildLimits();
    for (const persona of state.personas) state.counts.set(persona.persona_id, 0);
    state.runId = defaultRunId();
    state.loaded = true;
    applyPreset("contrast");
  } catch (error) {
    setRunNote(...errorNodes(error));
  }
  await Promise.all([connected, towns]);
  refresh();
}

void main();
