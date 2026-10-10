"use strict";

(() => {
  const MAX_JSON_BYTES = 1_048_576;
  const MAX_SEED = 9223372036854775807n;
  const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,79}$/;
  const RUN_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,39}$/;
  const JOB_ID_PATTERN = /^job-[0-9a-f]{32}$/;
  const HASH_PATTERN = /^[0-9a-f]{64}$/;
  const SEED_PATTERN = /^(?:0|[1-9][0-9]{0,18})$/;
  const INSPECTOR_PATTERN = /^\/runs\/[a-z0-9][a-z0-9-]{0,39}$/;
  const STATUS_PATTERN = /^\/api\/jobs\/job-[0-9a-f]{32}$/;
  const TERMINAL_PHASES = new Set(["completed", "failed", "cancelled"]);
  const LEGAL_PHASES = new Set([
    "queued",
    "evaluating",
    "persisting",
    "verifying",
    "completed",
    "failed",
    "cancelled",
  ]);
  const WORKER_FAILURE_MESSAGES = new Map([
    ["evaluation-failed", "The deterministic city evaluation failed."],
    ["persistence-failed", "The city run could not be persisted."],
    ["verification-failed", "The persisted city run could not be verified."],
  ]);
  const STAGES = ["city", "campaign", "run", "inspect"];
  const POLL_DELAYS = [250, 500, 1000, 2000];

  const byId = (id) => document.getElementById(id);
  const csrfMeta = document.querySelector('meta[name="adlife-csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

  const state = {
    capabilities: null,
    cities: [],
    templates: [],
    cityDetail: null,
    cityGeneration: 0,
    validationGeneration: 0,
    validatedDraft: null,
    activeJob: null,
    jobGeneration: 0,
    pollTimer: null,
    libraryGeneration: 0,
    libraryOffset: 0,
    libraryLimit: 20,
    lastLibraryPage: null,
    shuttingDown: false,
    resetting: false,
  };

  class DraftError extends Error {
    constructor(path, message, controlId) {
      super(message);
      this.path = path;
      this.controlId = controlId;
    }
  }

  class RequestError extends Error {
    constructor(message, documentValue = null) {
      super(message);
      this.documentValue = documentValue;
    }
  }

  const FIELD_TARGETS = new Map([
    ["city_id", ["city-select-error", "city-select"]],
    ["scenario.scenario_id", ["scenario-id-error", "scenario-id"]],
    ["scenario.name", ["scenario-name-error", "scenario-name"]],
    ["scenario.campaign.campaign_id", ["campaign-id-error", "campaign-id"]],
    ["scenario.campaign.name", ["campaign-name-error", "campaign-name"]],
    ["scenario.campaign.creative_template_id", ["creative-template-error", "creative-template"]],
    ["scenario.campaign.target_interests", ["target-interests-error", null]],
    ["scenario.campaign.relative_price", ["relative-price-error", "relative-price"]],
    ["scenario.phone", ["placements-error", "phone-enabled"]],
    ["scenario.phone.active_windows", ["phone-windows-error", "phone-add-window"]],
    ["scenario.phone.eligible_activities", ["phone-activities-error", null]],
    ["scenario.phone.opportunity_probability_per_minute", ["phone-probability-error", "phone-probability"]],
    ["scenario.phone.frequency_cap_per_agent_per_day", ["phone-cap-error", "phone-cap"]],
    ["scenario.roadside", ["placements-error", "roadside-enabled"]],
    ["scenario.roadside.active_windows", ["road-windows-error", "road-add-window"]],
    ["scenario.roadside.road_id", ["road-id-error", "road-id"]],
    ["scenario.roadside.travel_direction", ["road-direction-error", "road-direction"]],
    ["scenario.roadside.road_fraction", ["road-fraction-error", "road-fraction"]],
    ["scenario.roadside.side", ["road-side-error", "road-side"]],
    ["scenario.roadside.orientation_degrees", ["road-orientation-error", "road-orientation"]],
    ["scenario.roadside.max_view_distance_meters", ["road-distance-error", "road-distance"]],
    ["scenario.roadside.frequency_cap_per_agent_per_day", ["road-cap-error", "road-cap"]],
    ["cohort.interests", ["cohort-interests-error", null]],
    ["cohort.traits", ["traits-error", "trait-price"]],
    ["cohort.initial_state", ["initial-state-error", "initial-sentiment"]],
    ["settings.run_id", ["run-id-error", "run-id"]],
    ["settings.agent_count", ["agent-count-error", "agent-count"]],
    ["settings.days", ["days-error", "days"]],
    ["settings.seed", ["seed-error", "seed"]],
  ]);

  const AGGREGATE_PATHS = [
    "scenario.phone.active_windows",
    "scenario.roadside.active_windows",
    "scenario.phone",
    "scenario.roadside",
    "scenario.campaign.target_interests",
    "cohort.traits",
    "cohort.initial_state",
    "cohort.interests",
  ];

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function boundedText(value, maximum = 500) {
    return typeof value === "string" && value.length > 0 && value.length <= maximum;
  }

  function setText(id, value) {
    byId(id).textContent = String(value);
  }

  function makeElement(tag, text = null, className = null) {
    const element = document.createElement(tag);
    if (text !== null) {
      element.textContent = String(text);
    }
    if (className) {
      element.className = className;
    }
    return element;
  }

  function announce(message) {
    setText("workbench-live", message);
  }

  function clearBanner() {
    byId("error-banner").hidden = true;
    setText("error-banner-message", "");
  }

  function showBanner(message) {
    const safeMessage = boundedText(message, 500)
      ? message
      : "The local workbench could not complete the request.";
    setText("error-banner-message", safeMessage);
    byId("error-banner").hidden = false;
    announce(safeMessage);
  }

  function clearFieldErrors() {
    const seen = new Set();
    for (const [errorId, controlId] of FIELD_TARGETS.values()) {
      if (!seen.has(errorId)) {
        setText(errorId, "");
        seen.add(errorId);
      }
      if (controlId) {
        byId(controlId).removeAttribute("aria-invalid");
      }
    }
  }

  function targetForField(path) {
    if (FIELD_TARGETS.has(path)) {
      return FIELD_TARGETS.get(path);
    }
    for (const aggregate of AGGREGATE_PATHS) {
      if (path.startsWith(`${aggregate}.`)) {
        return FIELD_TARGETS.get(aggregate);
      }
    }
    return null;
  }

  function renderFieldErrors(fields) {
    clearFieldErrors();
    let firstControl = null;
    let hasRootError = false;
    if (!isObject(fields)) {
      showBanner("The server refused one or more study fields.");
      return;
    }
    for (const [path, message] of Object.entries(fields).slice(0, 32)) {
      if (!boundedText(path, 160) || !boundedText(message, 240)) {
        hasRootError = true;
        continue;
      }
      const target = targetForField(path);
      if (!target) {
        hasRootError = true;
        continue;
      }
      const [errorId, controlId] = target;
      if (!byId(errorId).textContent) {
        setText(errorId, message);
      }
      if (controlId) {
        const control = byId(controlId);
        control.setAttribute("aria-invalid", "true");
        if (!firstControl) {
          firstControl = control;
        }
      }
    }
    if (hasRootError) {
      showBanner("The server refused one or more study fields. Review the draft and try again.");
    }
    if (firstControl) {
      firstControl.focus();
    }
  }

  function requestErrorMessage(documentValue) {
    if (
      isObject(documentValue) &&
      isObject(documentValue.error) &&
      boundedText(documentValue.error.message, 500)
    ) {
      return documentValue.error.message;
    }
    return "The local workbench could not complete the request.";
  }

  function validateApiPath(path) {
    if (
      typeof path !== "string" ||
      path.length > 300 ||
      !path.startsWith("/api/") ||
      path.includes("\\") ||
      path.includes("//") ||
      path.includes("#") ||
      /[^A-Za-z0-9_?=&/.-]/.test(path)
    ) {
      throw new RequestError("The workbench refused an unsafe local request path.");
    }
    return path;
  }

  async function fetchJson(path, options = {}) {
    const safePath = validateApiPath(path);
    const method = options.method || "GET";
    if (method !== "GET" && method !== "POST") {
      throw new RequestError("The workbench refused an unsupported request method.");
    }
    const headers = { Accept: "application/json" };
    const request = {
      method,
      headers,
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      signal: options.signal,
    };
    if (method === "POST") {
      if (!boundedText(csrfToken, 256)) {
        throw new RequestError("The local security token is unavailable. Reload the page.");
      }
      headers["Content-Type"] = "application/json";
      headers["X-AdLife-CSRF"] = csrfToken;
      request.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(safePath, request);
    } catch (_error) {
      throw new RequestError("The local workbench did not respond. Check that it is still running.");
    }
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      throw new RequestError("The local workbench returned an unexpected response type.");
    }
    const text = await response.text();
    if (new TextEncoder().encode(text).length > MAX_JSON_BYTES) {
      throw new RequestError("The local workbench response exceeded its browser limit.");
    }
    let documentValue;
    try {
      documentValue = JSON.parse(text);
    } catch (_error) {
      throw new RequestError("The local workbench returned malformed JSON.");
    }
    if (!response.ok) {
      throw new RequestError(requestErrorMessage(documentValue), documentValue);
    }
    return documentValue;
  }

  function parseSeedText(value) {
    if (typeof value !== "string" || !SEED_PATTERN.test(value)) {
      throw new DraftError(
        "settings.seed",
        "Enter a whole-number seed from 0 through 9223372036854775807.",
        "seed",
      );
    }
    if (BigInt(value) > MAX_SEED) {
      throw new DraftError(
        "settings.seed",
        "Enter a whole-number seed from 0 through 9223372036854775807.",
        "seed",
      );
    }
    return value;
  }

  function requiredText(id, path, maximum) {
    const value = byId(id).value;
    if (!boundedText(value, maximum) || value.trim() !== value) {
      throw new DraftError(path, "Enter a non-empty value without outer spaces.", id);
    }
    return value;
  }

  function numberValue(id, path, minimum, maximum, integer = false) {
    const text = byId(id).value;
    if (text === "") {
      throw new DraftError(path, "Enter a value within the shown bounds.", id);
    }
    const value = Number(text);
    if (!Number.isFinite(value) || value < minimum || value > maximum) {
      throw new DraftError(path, "Enter a value within the shown bounds.", id);
    }
    if (integer && !Number.isInteger(value)) {
      throw new DraftError(path, "Enter a whole number within the shown bounds.", id);
    }
    return value;
  }

  function checkedValues(name, path, controlId = null) {
    const values = [...document.querySelectorAll(`input[name="${name}"]:checked`)]
      .map((input) => input.value)
      .sort();
    if (values.length === 0) {
      throw new DraftError(path, "Choose at least one value.", controlId);
    }
    return values;
  }

  function minuteFromTime(value, path, control) {
    if (!/^\d{2}:\d{2}$/.test(value)) {
      throw new DraftError(path, "Enter a complete model-clock time.", control.id);
    }
    const [hour, minute] = value.split(":").map(Number);
    if (hour > 23 || minute > 59) {
      throw new DraftError(path, "Enter a valid model-clock time.", control.id);
    }
    return hour * 60 + minute;
  }

  function readWindows(containerId, path, days) {
    const rows = [...byId(containerId).children];
    if (rows.length < 1 || rows.length > 14) {
      throw new DraftError(path, "Keep between 1 and 14 active windows.", `${containerId === "phone-windows" ? "phone" : "road"}-add-window`);
    }
    const windows = rows.map((row, index) => {
      const dayControl = row.querySelector('[data-window-field="day"]');
      const startControl = row.querySelector('[data-window-field="start"]');
      const endControl = row.querySelector('[data-window-field="end"]');
      const day = Number(dayControl.value);
      if (!Number.isInteger(day) || day < 1 || day > days) {
        throw new DraftError(path, `Window ${index + 1} must use a day from 1 through ${days}.`, dayControl.id);
      }
      const start = minuteFromTime(startControl.value, path, startControl);
      const endClock = minuteFromTime(endControl.value, path, endControl);
      const end = endClock === 0 && endControl.value === "00:00" ? 1440 : endClock;
      if (end <= start) {
        throw new DraftError(path, `Window ${index + 1} must end after it starts.`, endControl.id);
      }
      return { day, start_minute: start, end_minute: end };
    });
    windows.sort((left, right) => left.day - right.day || left.start_minute - right.start_minute || left.end_minute - right.end_minute);
    for (let index = 1; index < windows.length; index += 1) {
      const previous = windows[index - 1];
      const current = windows[index];
      if (previous.day === current.day && current.start_minute < previous.end_minute) {
        throw new DraftError(path, "Active windows on the same day cannot overlap.", null);
      }
    }
    return windows;
  }

  function readDraft() {
    if (!state.cityDetail) {
      throw new DraftError("city_id", "Choose a verified city.", "city-select");
    }
    const days = numberValue("days", "settings.days", 1, 7, true);
    const phoneEnabled = byId("phone-enabled").checked;
    const roadsideEnabled = byId("roadside-enabled").checked;
    if (!phoneEnabled && !roadsideEnabled) {
      throw new DraftError("scenario", "Enable at least one opportunity placement.", "phone-enabled");
    }
    const phone = phoneEnabled
      ? {
          active_windows: readWindows("phone-windows", "scenario.phone.active_windows", days),
          frequency_cap_per_agent_per_day: numberValue("phone-cap", "scenario.phone.frequency_cap_per_agent_per_day", 1, 100, true),
          eligible_activities: checkedValues("phone-activity", "scenario.phone.eligible_activities"),
          opportunity_probability_per_minute: numberValue("phone-probability", "scenario.phone.opportunity_probability_per_minute", 0, 1),
        }
      : null;
    const roadside = roadsideEnabled
      ? {
          active_windows: readWindows("road-windows", "scenario.roadside.active_windows", days),
          frequency_cap_per_agent_per_day: numberValue("road-cap", "scenario.roadside.frequency_cap_per_agent_per_day", 1, 100, true),
          road_id: requiredText("road-id", "scenario.roadside.road_id", 80),
          travel_direction: requiredText("road-direction", "scenario.roadside.travel_direction", 20),
          road_fraction: numberValue("road-fraction", "scenario.roadside.road_fraction", 0.0000001, 0.9999999),
          side: requiredText("road-side", "scenario.roadside.side", 20),
          orientation_degrees: numberValue("road-orientation", "scenario.roadside.orientation_degrees", 0, 359.999999),
          max_view_distance_meters: numberValue("road-distance", "scenario.roadside.max_view_distance_meters", 0.0000001, 1000),
        }
      : null;

    return {
      schema_version: 1,
      city_id: state.cityDetail.city_id,
      scenario: {
        scenario_id: requiredText("scenario-id", "scenario.scenario_id", 80),
        name: requiredText("scenario-name", "scenario.name", 120),
        campaign: {
          campaign_id: requiredText("campaign-id", "scenario.campaign.campaign_id", 80),
          name: requiredText("campaign-name", "scenario.campaign.name", 120),
          creative_template_id: requiredText("creative-template", "scenario.campaign.creative_template_id", 80),
          target_interests: checkedValues("target-interest", "scenario.campaign.target_interests"),
          relative_price: numberValue("relative-price", "scenario.campaign.relative_price", 0.0000001, 100),
        },
        phone,
        roadside,
      },
      cohort: {
        interests: checkedValues("cohort-interest", "cohort.interests"),
        traits: {
          price_sensitivity: numberValue("trait-price", "cohort.traits.price_sensitivity", 0, 1),
          novelty_seeking: numberValue("trait-novelty", "cohort.traits.novelty_seeking", 0, 1),
          advertising_skepticism: numberValue("trait-skepticism", "cohort.traits.advertising_skepticism", 0, 1),
          mobile_recall_encoding: numberValue("trait-mobile-recall", "cohort.traits.mobile_recall_encoding", 0, 1),
          roadside_recall_encoding: numberValue("trait-road-recall", "cohort.traits.roadside_recall_encoding", 0, 1),
          impulsivity: numberValue("trait-impulsivity", "cohort.traits.impulsivity", 0, 1),
        },
        initial_state: {
          brand_sentiment: numberValue("initial-sentiment", "cohort.initial_state.brand_sentiment", -1, 1),
          recall_strength: numberValue("initial-recall", "cohort.initial_state.recall_strength", 0, 1),
          purchase_intention: numberValue("initial-intention", "cohort.initial_state.purchase_intention", 0, 1),
        },
      },
      settings: {
        run_id: requiredText("run-id", "settings.run_id", 40),
        agent_count: numberValue("agent-count", "settings.agent_count", 1, 30, true),
        days,
        seed: parseSeedText(byId("seed").value),
        response_mode: "deterministic-rules",
      },
    };
  }

  function invalidateValidation(message = "Validate the revised draft before starting a run.") {
    state.validationGeneration += 1;
    state.validatedDraft = null;
    byId("validation-review").hidden = true;
    byId("start-run").disabled = true;
    byId("validation-state").classList.remove("valid");
    setText("validation-state", message);
  }

  function showStage(stage) {
    if (!STAGES.includes(stage)) {
      return;
    }
    const button = byId(`stage-${stage}`);
    if (button.disabled) {
      return;
    }
    for (const name of STAGES) {
      const isCurrent = name === stage;
      byId(`panel-${name}`).hidden = !isCurrent;
      const stageButton = byId(`stage-${name}`);
      if (isCurrent) {
        stageButton.setAttribute("aria-current", "step");
      } else {
        stageButton.removeAttribute("aria-current");
      }
    }
    if (stage === "inspect") {
      loadRunPage(state.libraryOffset);
    }
    announce(`${stage.toUpperCase()} stage selected.`);
  }

  function addScheduleRow(containerId, day, start, end) {
    const container = byId(containerId);
    if (container.children.length >= 14) {
      return;
    }
    const prefix = containerId === "phone-windows" ? "phone" : "road";
    const index = Number(container.dataset.nextIndex || "0");
    container.dataset.nextIndex = String(index + 1);
    const row = makeElement("div", null, "schedule-row");

    const dayLabel = makeElement("label");
    const dayText = makeElement("span", "Day");
    const dayInput = document.createElement("input");
    dayInput.type = "number";
    dayInput.min = "1";
    dayInput.max = "7";
    dayInput.step = "1";
    dayInput.value = String(day);
    dayInput.id = `${prefix}-window-${index}-day`;
    dayInput.dataset.windowField = "day";
    dayLabel.htmlFor = dayInput.id;
    dayLabel.append(dayText, dayInput);

    const startLabel = makeElement("label");
    const startText = makeElement("span", "Start");
    const startInput = document.createElement("input");
    startInput.type = "time";
    startInput.value = start;
    startInput.id = `${prefix}-window-${index}-start`;
    startInput.dataset.windowField = "start";
    startLabel.htmlFor = startInput.id;
    startLabel.append(startText, startInput);

    const endLabel = makeElement("label");
    const endText = makeElement("span", "End");
    const endInput = document.createElement("input");
    endInput.type = "time";
    endInput.value = end;
    endInput.id = `${prefix}-window-${index}-end`;
    endInput.dataset.windowField = "end";
    endLabel.htmlFor = endInput.id;
    endLabel.append(endText, endInput);

    const remove = makeElement("button", "×", "remove-window");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${prefix} active window`);
    remove.addEventListener("click", () => {
      row.remove();
      invalidateValidation();
    });
    row.append(dayLabel, startLabel, endLabel, remove);
    container.append(row);
  }

  function resetSchedules() {
    for (const id of ["phone-windows", "road-windows"]) {
      byId(id).replaceChildren();
      byId(id).dataset.nextIndex = "0";
    }
    addScheduleRow("phone-windows", 1, "08:00", "18:00");
    addScheduleRow("phone-windows", 2, "08:00", "18:00");
    addScheduleRow("road-windows", 1, "07:00", "19:00");
    addScheduleRow("road-windows", 2, "07:00", "19:00");
  }

  function setNamedChecks(name, values) {
    const selected = new Set(values);
    for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
      input.checked = selected.has(input.value);
    }
  }

  function resetDraft() {
    state.resetting = true;
    setText("scenario-id-error", "");
    byId("scenario-id").value = "city-study";
    byId("scenario-name").value = "Synthetic city study";
    byId("campaign-id").value = "fictional-campaign";
    byId("campaign-name").value = "Fictional city campaign";
    if (state.templates.some((item) => item.template_id === "fictional-device-launch-v1")) {
      byId("creative-template").value = "fictional-device-launch-v1";
    }
    setNamedChecks("target-interest", ["commuting", "technology"]);
    setNamedChecks("cohort-interest", ["commuting", "technology"]);
    setNamedChecks("phone-activity", ["commute", "leisure"]);
    byId("relative-price").value = "1.0";
    byId("phone-enabled").checked = true;
    byId("roadside-enabled").checked = false;
    byId("phone-probability").value = "0.05";
    byId("phone-cap").value = "3";
    byId("road-fraction").value = "0.5";
    byId("road-side").value = "right";
    byId("road-orientation").value = "90";
    byId("road-distance").value = "120";
    byId("road-cap").value = "2";
    for (const [id, value] of [
      ["trait-price", "0.5"],
      ["trait-novelty", "0.6"],
      ["trait-skepticism", "0.4"],
      ["trait-mobile-recall", "0.7"],
      ["trait-road-recall", "0.6"],
      ["trait-impulsivity", "0.3"],
      ["initial-sentiment", "0.0"],
      ["initial-recall", "0.1"],
      ["initial-intention", "0.2"],
      ["agent-count", "20"],
      ["days", "2"],
      ["seed", "42"],
    ]) {
      byId(id).value = value;
    }
    byId("run-id").dataset.touched = "false";
    byId("run-id").value = suggestedRunId();
    resetSchedules();
    updatePlacementVisibility();
    renderCreative();
    populateRoadDirections();
    renderMap();
    clearFieldErrors();
    clearBanner();
    invalidateValidation("Draft reset. Validate it before starting a run.");
    state.resetting = false;
    announce("Draft reset. Any active worker was left unchanged.");
  }

  function updatePlacementVisibility() {
    const phone = byId("phone-enabled").checked;
    const roadside = byId("roadside-enabled").checked;
    byId("phone-fields").hidden = !phone;
    byId("roadside-fields").hidden = !roadside;
    byId("phone-card").classList.toggle("enabled", phone);
    byId("roadside-card").classList.toggle("enabled", roadside);
    let label = "No placement selected";
    if (phone && roadside) {
      label = "Phone + roadside opportunities";
    } else if (roadside) {
      label = "Roadside opportunity";
    } else if (phone) {
      label = "Phone opportunity";
    }
    setText("thread-placement", label);
    renderMap();
  }

  function assertCapabilities(documentValue) {
    if (
      !isObject(documentValue) ||
      documentValue.schema_version !== 1 ||
      !isObject(documentValue.capabilities) ||
      documentValue.capabilities.scenario_validation !== true ||
      documentValue.capabilities.jobs !== true ||
      documentValue.capabilities.run_execution !== true ||
      documentValue.capabilities.provider_configuration !== false ||
      documentValue.capabilities.oauth !== false ||
      !Array.isArray(documentValue.capabilities.response_modes) ||
      documentValue.capabilities.response_modes.length !== 1 ||
      documentValue.capabilities.response_modes[0] !== "deterministic-rules"
    ) {
      throw new RequestError("The workbench capability document is incompatible.");
    }
    return documentValue;
  }

  function assertCatalog(documentValue) {
    if (!isObject(documentValue) || documentValue.schema_version !== 1 || !Array.isArray(documentValue.cities)) {
      throw new RequestError("The verified city catalog is incompatible.");
    }
    const cities = documentValue.cities.map((city) => {
      if (
        !isObject(city) ||
        !ID_PATTERN.test(city.city_id || "") ||
        !boundedText(city.display_name, 120) ||
        city.pack_schema_version !== 2 ||
        !HASH_PATTERN.test(city.pack_sha256 || "") ||
        city.data_origin !== "fictional" ||
        city.qualification !== "fictional-fixture" ||
        Object.hasOwn(city, "resource_name")
      ) {
        throw new RequestError("The verified city catalog contains an invalid entry.");
      }
      return city;
    });
    if (cities.length < 1 || cities.length > 100) {
      throw new RequestError("The verified city catalog has an unsupported size.");
    }
    return cities;
  }

  function assertTemplates(documentValue) {
    if (!isObject(documentValue) || documentValue.schema_version !== 1 || !Array.isArray(documentValue.templates)) {
      throw new RequestError("The creative catalog is incompatible.");
    }
    return documentValue.templates.map((template) => {
      if (
        !isObject(template) ||
        !ID_PATTERN.test(template.template_id || "") ||
        template.template_version !== 1 ||
        !boundedText(template.product_name, 80) ||
        !boundedText(template.product_category, 80) ||
        !boundedText(template.message, 240) ||
        !boundedText(template.call_to_action, 120) ||
        !HASH_PATTERN.test(template.creative_sha256 || "")
      ) {
        throw new RequestError("The creative catalog contains an invalid entry.");
      }
      return template;
    });
  }

  function assertCityDetail(documentValue, expectedId) {
    if (
      !isObject(documentValue) ||
      documentValue.schema_version !== 1 ||
      documentValue.city_id !== expectedId ||
      !boundedText(documentValue.display_name, 120) ||
      documentValue.pack_schema_version !== 2 ||
      documentValue.data_origin !== "fictional" ||
      documentValue.qualification !== "fictional-fixture" ||
      !boundedText(documentValue.time_zone, 64) ||
      !HASH_PATTERN.test(documentValue.city_sha256 || "") ||
      !Array.isArray(documentValue.known_omissions) ||
      documentValue.known_omissions.length < 1 ||
      !isObject(documentValue.source) ||
      !boundedText(documentValue.source.attribution, 500) ||
      !boundedText(documentValue.source.license, 80) ||
      !isObject(documentValue.pack) ||
      documentValue.pack.city_id !== expectedId ||
      documentValue.pack.schema_version !== 2 ||
      !isObject(documentValue.pack.bounds) ||
      !Array.isArray(documentValue.pack.nodes) ||
      !Array.isArray(documentValue.pack.roads) ||
      documentValue.pack.nodes.length < 2 ||
      documentValue.pack.roads.length < 1
    ) {
      throw new RequestError("The selected city detail is incompatible.");
    }
    const nodeIds = new Set();
    for (const node of documentValue.pack.nodes) {
      if (
        !isObject(node) ||
        !ID_PATTERN.test(node.node_id || "") ||
        !Number.isFinite(node.longitude) ||
        !Number.isFinite(node.latitude) ||
        nodeIds.has(node.node_id)
      ) {
        throw new RequestError("The selected city contains invalid nodes.");
      }
      nodeIds.add(node.node_id);
    }
    const roadIds = new Set();
    for (const road of documentValue.pack.roads) {
      if (
        !isObject(road) ||
        !ID_PATTERN.test(road.road_id || "") ||
        roadIds.has(road.road_id) ||
        !nodeIds.has(road.source_node) ||
        !nodeIds.has(road.target_node) ||
        !Array.isArray(road.directions) ||
        road.directions.length < 1 ||
        road.directions.some((direction) => !["forward", "backward"].includes(direction)) ||
        !Array.isArray(road.shape)
      ) {
        throw new RequestError("The selected city contains invalid roads.");
      }
      for (const point of road.shape) {
        if (!isObject(point) || !Number.isFinite(point.longitude) || !Number.isFinite(point.latitude)) {
          throw new RequestError("The selected city contains invalid road geometry.");
        }
      }
      roadIds.add(road.road_id);
    }
    return documentValue;
  }

  function renderCatalog() {
    const select = byId("city-select");
    const options = state.cities.map((city) => {
      const option = makeElement("option", city.display_name);
      option.value = city.city_id;
      return option;
    });
    select.replaceChildren(...options);
    const preferred = state.cities.find((city) => city.city_id === "fictional-grid-v2") || state.cities[0];
    select.value = preferred.city_id;
    select.disabled = false;
  }

  function renderTemplates() {
    const select = byId("creative-template");
    const options = state.templates.map((template) => {
      const option = makeElement("option", `${template.product_name} · ${template.product_category}`);
      option.value = template.template_id;
      return option;
    });
    select.replaceChildren(...options);
    const preferred = state.templates.find((item) => item.template_id === "fictional-device-launch-v1") || state.templates[0];
    select.value = preferred.template_id;
    select.disabled = false;
    renderCreative();
  }

  function renderCreative() {
    const selected = state.templates.find((item) => item.template_id === byId("creative-template").value);
    if (!selected) {
      setText("creative-product", "Creative unavailable");
      setText("creative-message", "Select a verified fictional creative.");
      setText("creative-category", "—");
      setText("creative-action", "—");
      return;
    }
    setText("creative-product", selected.product_name);
    setText("creative-message", selected.message);
    setText("creative-category", selected.product_category);
    setText("creative-action", selected.call_to_action);
  }

  function populateRoads() {
    const select = byId("road-id");
    if (!state.cityDetail) {
      select.replaceChildren();
      return;
    }
    const options = state.cityDetail.pack.roads.map((road) => {
      const option = makeElement("option", `${road.road_id} · ${road.kind}`);
      option.value = road.road_id;
      return option;
    });
    select.replaceChildren(...options);
    if (state.cityDetail.pack.roads.some((road) => road.road_id === "middle-west")) {
      select.value = "middle-west";
    }
    populateRoadDirections();
  }

  function populateRoadDirections() {
    const select = byId("road-direction");
    const road = state.cityDetail
      ? state.cityDetail.pack.roads.find((item) => item.road_id === byId("road-id").value)
      : null;
    if (!road) {
      select.replaceChildren();
      return;
    }
    const previous = select.value;
    const options = road.directions.map((direction) => {
      const option = makeElement("option", direction === "forward" ? "Forward" : "Backward");
      option.value = direction;
      return option;
    });
    select.replaceChildren(...options);
    select.value = road.directions.includes(previous) ? previous : road.directions[0];
  }

  function renderCityDetail() {
    const detail = state.cityDetail;
    setText("city-name", detail.display_name);
    setText("city-time-zone", detail.time_zone);
    setText("city-schema", String(detail.pack_schema_version));
    setText("city-hash", detail.city_sha256);
    setText("city-license", detail.source.license);
    setText("city-attribution", detail.source.attribution);
    setText("city-qualification", "VERIFIED FICTIONAL FIXTURE");
    const omissions = detail.known_omissions.map((item) => makeElement("li", item));
    byId("city-omissions").replaceChildren(...omissions);
    byId("stage-campaign").disabled = false;
    byId("city-continue").disabled = false;
    populateRoads();
    renderMap();
  }

  function roadPoints(road, nodes) {
    const source = nodes.get(road.source_node);
    const target = nodes.get(road.target_node);
    return [source, ...road.shape, target];
  }

  function pointAlong(points, fraction) {
    const segments = [];
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      const dx = points[index].longitude - points[index - 1].longitude;
      const dy = points[index].latitude - points[index - 1].latitude;
      const length = Math.hypot(dx, dy);
      segments.push({ left: points[index - 1], right: points[index], length });
      total += length;
    }
    let remaining = total * fraction;
    for (const segment of segments) {
      if (remaining <= segment.length || segment === segments[segments.length - 1]) {
        const ratio = segment.length === 0 ? 0 : Math.min(1, remaining / segment.length);
        return {
          longitude: segment.left.longitude + (segment.right.longitude - segment.left.longitude) * ratio,
          latitude: segment.left.latitude + (segment.right.latitude - segment.left.latitude) * ratio,
        };
      }
      remaining -= segment.length;
    }
    return points[0];
  }

  function renderMap() {
    const canvas = byId("city-map");
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#071820";
    context.fillRect(0, 0, canvas.width, canvas.height);
    if (!state.cityDetail) {
      setText("map-status", "WAITING FOR CITY");
      return;
    }
    const pack = state.cityDetail.pack;
    const bounds = pack.bounds;
    const width = bounds.east - bounds.west;
    const height = bounds.north - bounds.south;
    if (!(width > 0 && height > 0)) {
      showBanner("The verified city has unusable map bounds.");
      return;
    }
    const margin = 58;
    const project = (point) => ({
      x: margin + ((point.longitude - bounds.west) / width) * (canvas.width - margin * 2),
      y: canvas.height - margin - ((point.latitude - bounds.south) / height) * (canvas.height - margin * 2),
    });
    const nodes = new Map(pack.nodes.map((node) => [node.node_id, node]));

    context.strokeStyle = "rgba(118, 169, 207, .14)";
    context.lineWidth = 1;
    for (let index = 1; index < 8; index += 1) {
      const x = (canvas.width / 8) * index;
      const y = (canvas.height / 8) * index;
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, canvas.height);
      context.stroke();
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(canvas.width, y);
      context.stroke();
    }

    const widths = { residential: 3, secondary: 5, primary: 7, trunk: 9 };
    for (const road of pack.roads) {
      const points = roadPoints(road, nodes).map(project);
      context.beginPath();
      context.moveTo(points[0].x, points[0].y);
      for (const point of points.slice(1)) {
        context.lineTo(point.x, point.y);
      }
      context.lineCap = "round";
      context.lineJoin = "round";
      context.lineWidth = widths[road.kind] || 4;
      context.strokeStyle = road.kind === "trunk" ? "#76a9cf" : "rgba(118, 169, 207, .68)";
      context.stroke();
    }

    for (const node of pack.nodes) {
      const point = project(node);
      context.beginPath();
      context.arc(point.x, point.y, 5, 0, Math.PI * 2);
      context.fillStyle = "#071218";
      context.fill();
      context.lineWidth = 3;
      context.strokeStyle = "#84dfc2";
      context.stroke();
    }

    if (byId("roadside-enabled").checked) {
      const selected = pack.roads.find((road) => road.road_id === byId("road-id").value);
      const fraction = Number(byId("road-fraction").value);
      if (selected && Number.isFinite(fraction) && fraction > 0 && fraction < 1) {
        const marker = project(pointAlong(roadPoints(selected, nodes), fraction));
        context.beginPath();
        context.arc(marker.x, marker.y, 14, 0, Math.PI * 2);
        context.fillStyle = "rgba(217, 187, 115, .2)";
        context.fill();
        context.lineWidth = 4;
        context.strokeStyle = "#d9bb73";
        context.stroke();
        context.beginPath();
        context.moveTo(marker.x, marker.y - 20);
        context.lineTo(marker.x, marker.y + 20);
        context.stroke();
      }
    }
    if (byId("phone-enabled").checked) {
      context.beginPath();
      context.arc(canvas.width - 44, 44, 12, 0, Math.PI * 2);
      context.fillStyle = "#84dfc2";
      context.fill();
      context.fillStyle = "#071218";
      context.font = "bold 14px sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText("P", canvas.width - 44, 44);
    }
    setText("map-status", "PACK HASH VERIFIED");
    setText("map-summary", `${state.cityDetail.display_name} contains ${pack.roads.length} roads and ${pack.nodes.length} nodes. Geometry is fictional and model-only.`);
    const items = pack.roads.map((road) => makeElement("li", `${road.road_id}: ${road.kind}, ${road.directions.join(" / ")}`));
    byId("map-road-list").replaceChildren(...items);
  }

  async function loadCity(cityId) {
    if (!ID_PATTERN.test(cityId)) {
      showBanner("The selected city identifier is invalid.");
      return;
    }
    const generation = ++state.cityGeneration;
    state.cityDetail = null;
    byId("city-continue").disabled = true;
    byId("stage-campaign").disabled = true;
    byId("stage-run").disabled = true;
    invalidateValidation("City changed. Validate the completed draft again.");
    setText("city-name", "Verifying city…");
    setText("map-status", "VERIFYING PACK");
    try {
      const documentValue = await fetchJson(`/api/catalog/cities/${cityId}`);
      const detail = assertCityDetail(documentValue, cityId);
      if (generation !== state.cityGeneration) {
        return;
      }
      state.cityDetail = detail;
      renderCityDetail();
      announce(`${detail.display_name} verified.`);
    } catch (error) {
      if (generation !== state.cityGeneration) {
        return;
      }
      showBanner(error instanceof Error ? error.message : "The city could not be verified.");
      setText("city-name", "City unavailable");
      setText("map-status", "CITY UNAVAILABLE");
    }
  }

  function assertValidation(documentValue, draft) {
    if (
      !isObject(documentValue) ||
      documentValue.schema_version !== 1 ||
      documentValue.valid !== true ||
      !isObject(documentValue.run) ||
      documentValue.run.run_id !== draft.settings.run_id ||
      documentValue.run.agent_count !== draft.settings.agent_count ||
      documentValue.run.days !== draft.settings.days ||
      documentValue.run.seed !== draft.settings.seed ||
      documentValue.run.response_mode !== "deterministic-rules" ||
      !isObject(documentValue.scenario) ||
      !Array.isArray(documentValue.scenario.channels) ||
      !isObject(documentValue.hashes) ||
      !["input_sha256", "city_sha256", "scenario_sha256", "creative_sha256", "response_sha256"].every((key) => HASH_PATTERN.test(documentValue.hashes[key] || ""))
    ) {
      throw new RequestError("The validation receipt is incompatible with this draft.");
    }
    return documentValue;
  }

  function renderValidation(documentValue) {
    setText("review-run", documentValue.run.run_id);
    setText("review-seed", documentValue.run.seed);
    setText("review-population", `${documentValue.run.agent_count} synthetic people · ${documentValue.run.days} model days`);
    setText("review-placements", documentValue.scenario.channels.join(" + "));
    setText("review-input-hash", documentValue.hashes.input_sha256);
    setText("review-city-hash", documentValue.hashes.city_sha256);
    setText("review-scenario-hash", documentValue.hashes.scenario_sha256);
    setText("review-creative-hash", documentValue.hashes.creative_sha256);
    setText("review-response-hash", documentValue.hashes.response_sha256);
    byId("validation-review").hidden = false;
    byId("start-run").disabled = false;
    byId("validation-state").classList.add("valid");
    setText("validation-state", "Validated. Review the immutable binding receipt before starting.");
  }

  function renderLocalDraftError(error) {
    clearFieldErrors();
    const target = targetForField(error.path);
    if (target) {
      const [errorId, mappedControl] = target;
      setText(errorId, error.message);
      const control = byId(error.controlId || mappedControl);
      if (control) {
        control.setAttribute("aria-invalid", "true");
        control.focus();
      }
    } else {
      showBanner(error.message);
    }
  }

  async function validateDraft() {
    clearBanner();
    clearFieldErrors();
    let draft;
    try {
      draft = readDraft();
    } catch (error) {
      if (error instanceof DraftError) {
        renderLocalDraftError(error);
        return;
      }
      showBanner("The draft could not be read safely.");
      return;
    }
    const generation = ++state.validationGeneration;
    byId("validate-run").disabled = true;
    byId("start-run").disabled = true;
    setText("validation-state", "Validating complete study bindings…");
    try {
      const documentValue = await fetchJson("/api/scenarios/validate", { method: "POST", body: draft });
      const receipt = assertValidation(documentValue, draft);
      if (generation !== state.validationGeneration) {
        return;
      }
      state.validatedDraft = JSON.stringify(draft);
      renderValidation(receipt);
      announce("Study validated without writing an artifact.");
    } catch (error) {
      if (generation !== state.validationGeneration) {
        return;
      }
      invalidateValidation("Validation was refused. Revise the highlighted fields and try again.");
      if (error instanceof RequestError && isObject(error.documentValue) && isObject(error.documentValue.error)) {
        renderFieldErrors(error.documentValue.error.fields);
      } else {
        showBanner(error instanceof Error ? error.message : "The study could not be validated.");
      }
    } finally {
      byId("validate-run").disabled = false;
    }
  }

  function assertAcceptedSettings(value, runId) {
    if (
      !isObject(value) ||
      value.schema_version !== 1 ||
      !RUN_ID_PATTERN.test(runId || "") ||
      !ID_PATTERN.test(value.city_id || "") ||
      !ID_PATTERN.test(value.scenario_id || "") ||
      !ID_PATTERN.test(value.campaign_id || "") ||
      !SEED_PATTERN.test(value.seed || "") ||
      BigInt(value.seed) > MAX_SEED ||
      !Number.isInteger(value.agent_count) ||
      value.agent_count < 1 ||
      value.agent_count > 30 ||
      !Number.isInteger(value.days) ||
      value.days < 1 ||
      value.days > 7 ||
      value.response_mode !== "deterministic-rules"
    ) {
      throw new RequestError("The worker returned incompatible accepted settings.");
    }
    return value;
  }

  function assertRunSummary(value) {
    if (
      !isObject(value) ||
      value.schema_version !== 1 ||
      !RUN_ID_PATTERN.test(value.run_id || "") ||
      !Number.isInteger(value.run_schema_version) ||
      value.run_schema_version < 1 ||
      value.run_schema_version > 7 ||
      !ID_PATTERN.test(value.city_id || "") ||
      !SEED_PATTERN.test(value.seed || "") ||
      BigInt(value.seed) > MAX_SEED ||
      !INSPECTOR_PATTERN.test(value.inspector_url || "") ||
      value.inspector_url !== `/runs/${value.run_id}` ||
      !Number.isInteger(value.days) ||
      value.days < 1 ||
      value.days > 7 ||
      !Number.isInteger(value.agent_count) ||
      value.agent_count < 1 ||
      value.agent_count > 30 ||
      !Number.isInteger(value.frame_count) ||
      value.frame_count !== value.days * 1440
    ) {
      throw new RequestError("A verified run summary is incompatible.");
    }
    return value;
  }

  function assertJob(value) {
    if (
      !isObject(value) ||
      value.schema_version !== 1 ||
      !JOB_ID_PATTERN.test(value.job_id || "") ||
      !RUN_ID_PATTERN.test(value.run_id || "") ||
      !LEGAL_PHASES.has(value.phase) ||
      typeof value.cancellation_requested !== "boolean" ||
      typeof value.can_cancel !== "boolean"
    ) {
      throw new RequestError("The worker returned an incompatible job record.");
    }
    assertAcceptedSettings(value.accepted_settings, value.run_id);
    if (value.phase === "completed") {
      const result = assertRunSummary(value.result);
      const settings = value.accepted_settings;
      if (
        result.run_id !== value.run_id ||
        result.run_schema_version !== 7 ||
        result.city_id !== settings.city_id ||
        result.scenario_id !== settings.scenario_id ||
        !Array.isArray(result.campaign_ids) ||
        result.campaign_ids.length !== 1 ||
        result.campaign_ids[0] !== settings.campaign_id ||
        result.seed !== settings.seed ||
        result.agent_count !== settings.agent_count ||
        result.days !== settings.days
      ) {
        throw new RequestError("The worker result is incompatible with its accepted settings.");
      }
    } else if (value.result !== null) {
      throw new RequestError("An active worker record carried a completed result.");
    }
    if (value.phase === "failed") {
      if (
        !isObject(value.error) ||
        !WORKER_FAILURE_MESSAGES.has(value.error.code) ||
        WORKER_FAILURE_MESSAGES.get(value.error.code) !== value.error.message
      ) {
        throw new RequestError("The worker returned an incompatible failure record.");
      }
    } else if (value.error !== null) {
      throw new RequestError("A non-failed worker record carried a failure.");
    }
    return value;
  }

  function appendFact(list, label, value) {
    const wrapper = makeElement("div");
    wrapper.append(makeElement("dt", label), makeElement("dd", value));
    list.append(wrapper);
  }

  function renderJob(job) {
    state.activeJob = job;
    byId("job-panel").hidden = false;
    setText("job-phase", job.phase.toUpperCase());
    const settings = job.accepted_settings;
    const facts = byId("job-settings");
    facts.replaceChildren();
    appendFact(facts, "Run", job.run_id);
    appendFact(facts, "City", settings.city_id);
    appendFact(facts, "Campaign", settings.campaign_id);
    appendFact(facts, "Population", `${settings.agent_count} · ${settings.days} days`);
    appendFact(facts, "Seed", settings.seed);
    appendFact(facts, "Mode", "Deterministic rules");
    const cancel = byId("cancel-job");
    cancel.hidden = !job.can_cancel;
    cancel.disabled = !job.can_cancel;
    const inspect = byId("job-inspect-link");
    inspect.hidden = true;
    inspect.href = "/";
    if (job.phase === "completed") {
      setText("job-message", "The artifact was persisted and independently verified. Inspection is ready; this page will not navigate automatically.");
      inspect.href = job.result.inspector_url;
      inspect.hidden = false;
      inspect.focus();
    } else if (job.phase === "failed") {
      setText("job-message", job.error.message);
    } else if (job.phase === "cancelled") {
      setText("job-message", "The cooperative cancellation completed before publication. No completed artifact was presented.");
    } else if (job.cancellation_requested) {
      setText("job-message", "Cancellation requested. Evaluation may finish its current bounded step before stopping.");
    } else {
      const phaseMessages = {
        queued: "The worker accepted this study and is preparing evaluation.",
        evaluating: "Deterministic model evaluation is in progress.",
        persisting: "Evaluation finished; the immutable artifact is being persisted.",
        verifying: "The saved artifact is being independently verified.",
      };
      setText("job-message", phaseMessages[job.phase]);
    }
  }

  function stopPolling() {
    state.jobGeneration += 1;
    if (state.pollTimer !== null) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(statusUrl, generation, attempt) {
    if (state.shuttingDown || generation !== state.jobGeneration) {
      return;
    }
    const delay = POLL_DELAYS[Math.min(attempt, POLL_DELAYS.length - 1)];
    state.pollTimer = window.setTimeout(() => pollJob(statusUrl, generation, attempt), delay);
  }

  async function pollJob(statusUrl, generation, attempt) {
    if (state.shuttingDown || generation !== state.jobGeneration) {
      return;
    }
    state.pollTimer = null;
    try {
      const job = assertJob(await fetchJson(statusUrl));
      if (generation !== state.jobGeneration) {
        return;
      }
      if (statusUrl !== `/api/jobs/${job.job_id}`) {
        throw new RequestError("The worker status address changed unexpectedly.");
      }
      renderJob(job);
      if (TERMINAL_PHASES.has(job.phase)) {
        if (job.phase === "completed") {
          await loadRunPage(0);
        }
        return;
      }
      schedulePoll(statusUrl, generation, attempt + 1);
    } catch (error) {
      if (generation !== state.jobGeneration) {
        return;
      }
      showBanner(error instanceof Error ? error.message : "The worker status could not be read.");
      schedulePoll(statusUrl, generation, attempt + 1);
    }
  }

  function beginPolling(job, statusUrl) {
    if (!STATUS_PATTERN.test(statusUrl) || statusUrl !== `/api/jobs/${job.job_id}`) {
      throw new RequestError("The worker returned an unsafe status address.");
    }
    stopPolling();
    const generation = state.jobGeneration;
    renderJob(job);
    if (!TERMINAL_PHASES.has(job.phase)) {
      schedulePoll(statusUrl, generation, 0);
    }
  }

  async function startRun() {
    clearBanner();
    clearFieldErrors();
    let draft;
    try {
      draft = readDraft();
    } catch (error) {
      if (error instanceof DraftError) {
        renderLocalDraftError(error);
        return;
      }
      showBanner("The draft could not be read safely.");
      return;
    }
    if (state.validatedDraft !== JSON.stringify(draft)) {
      invalidateValidation();
      showBanner("The draft changed after validation. Validate the revised draft before starting.");
      return;
    }
    byId("start-run").disabled = true;
    try {
      const documentValue = await fetchJson("/api/jobs", { method: "POST", body: draft });
      if (
        !isObject(documentValue) ||
        documentValue.schema_version !== 1 ||
        !boundedText(documentValue.status_url, 80)
      ) {
        throw new RequestError("The worker acceptance response is incompatible.");
      }
      const job = assertJob(documentValue.job);
      beginPolling(job, documentValue.status_url);
      announce(`Run ${job.run_id} accepted in ${job.phase} phase.`);
    } catch (error) {
      byId("start-run").disabled = false;
      if (error instanceof RequestError && isObject(error.documentValue) && isObject(error.documentValue.error)) {
        const fields = error.documentValue.error.fields;
        if (isObject(fields) && Object.keys(fields).length > 0) {
          renderFieldErrors(fields);
        } else {
          showBanner(error.message);
        }
      } else {
        showBanner(error instanceof Error ? error.message : "The run could not be started.");
      }
    }
  }

  async function cancelActiveJob() {
    const job = state.activeJob;
    if (!job || !job.can_cancel || !JOB_ID_PATTERN.test(job.job_id)) {
      return;
    }
    byId("cancel-job").disabled = true;
    try {
      const cancelled = assertJob(await fetchJson(`/api/jobs/${job.job_id}/cancel`, { method: "POST", body: {} }));
      if (cancelled.job_id !== job.job_id) {
        throw new RequestError("The cancellation response identified a different job.");
      }
      renderJob(cancelled);
      announce("Cooperative cancellation requested.");
    } catch (error) {
      showBanner(error instanceof Error ? error.message : "Cancellation could not be requested.");
      byId("cancel-job").disabled = false;
    }
  }

  function assertRunPage(documentValue) {
    if (
      !isObject(documentValue) ||
      documentValue.schema_version !== 1 ||
      !Number.isInteger(documentValue.offset) ||
      !Number.isInteger(documentValue.limit) ||
      !Number.isInteger(documentValue.returned_count) ||
      !Number.isInteger(documentValue.verified_total) ||
      !Number.isInteger(documentValue.scanned_count) ||
      !Number.isInteger(documentValue.corrupt_count) ||
      typeof documentValue.truncated !== "boolean" ||
      !Array.isArray(documentValue.runs) ||
      documentValue.runs.length !== documentValue.returned_count
    ) {
      throw new RequestError("The verified run library response is incompatible.");
    }
    documentValue.runs.forEach(assertRunSummary);
    return documentValue;
  }

  function suggestedRunId() {
    const used = new Set(
      state.lastLibraryPage && Array.isArray(state.lastLibraryPage.runs)
        ? state.lastLibraryPage.runs.map((run) => run.run_id)
        : [],
    );
    for (let index = 1; index <= 999; index += 1) {
      const candidate = `city-study-${String(index).padStart(3, "0")}`;
      if (!used.has(candidate)) {
        return candidate;
      }
    }
    return "city-study-001";
  }

  function renderRunPage(page) {
    state.lastLibraryPage = page;
    state.libraryOffset = page.offset;
    setText("library-count", `${page.verified_total} verified run${page.verified_total === 1 ? "" : "s"}`);
    const integrity = [];
    if (page.corrupt_count > 0) {
      integrity.push(`${page.corrupt_count} unavailable entr${page.corrupt_count === 1 ? "y" : "ies"} withheld`);
    }
    if (page.truncated) {
      integrity.push("bounded scan truncated");
    }
    setText("library-integrity", integrity.join(" · "));
    const library = byId("run-library");
    if (page.runs.length === 0) {
      library.replaceChildren(makeElement("p", "No verified completed runs on this page. Configure and start a local deterministic study, then return here.", "empty-library"));
    } else {
      const cards = page.runs.map((run) => {
        const article = makeElement("article", null, "run-card");
        const header = makeElement("header");
        header.append(makeElement("h2", run.run_id), makeElement("span", `SCHEMA ${run.run_schema_version}`));
        const facts = makeElement("dl");
        appendFact(facts, "City", run.city_id);
        appendFact(facts, "Seed", run.seed);
        appendFact(facts, "Scale", `${run.agent_count} × ${run.days} days`);
        appendFact(facts, "Frames", String(run.frame_count));
        appendFact(facts, "Opportunities", String(run.opportunity_count));
        appendFact(facts, "Responses", String(run.response_count));
        const link = makeElement("a", "Inspect verified run", "button primary");
        link.href = run.inspector_url;
        article.append(header, facts, link);
        return article;
      });
      library.replaceChildren(...cards);
    }
    const pageNumber = Math.floor(page.offset / page.limit) + 1;
    setText("library-page", `Page ${pageNumber}`);
    byId("library-previous").disabled = page.offset === 0;
    byId("library-next").disabled = page.offset + page.returned_count >= page.verified_total;
    if (byId("run-id").dataset.touched !== "true") {
      byId("run-id").value = suggestedRunId();
    }
  }

  async function loadRunPage(offset) {
    if (!Number.isInteger(offset) || offset < 0 || offset > 10000) {
      return;
    }
    const generation = ++state.libraryGeneration;
    try {
      const page = assertRunPage(await fetchJson(`/api/runs?offset=${offset}&limit=${state.libraryLimit}`));
      if (generation !== state.libraryGeneration) {
        return;
      }
      renderRunPage(page);
    } catch (error) {
      if (generation !== state.libraryGeneration) {
        return;
      }
      showBanner(error instanceof Error ? error.message : "The run library could not be verified.");
      setText("library-count", "Verified library unavailable");
    }
  }

  function installEvents() {
    for (const button of document.querySelectorAll("[data-stage]")) {
      button.addEventListener("click", () => showStage(button.dataset.stage));
    }
    for (const button of document.querySelectorAll("[data-go-stage]")) {
      button.addEventListener("click", () => showStage(button.dataset.goStage));
    }
    byId("city-continue").addEventListener("click", () => showStage("campaign"));
    byId("campaign-continue").addEventListener("click", () => {
      byId("stage-run").disabled = false;
      showStage("run");
    });
    byId("city-select").addEventListener("change", () => loadCity(byId("city-select").value));
    byId("creative-template").addEventListener("change", renderCreative);
    byId("road-id").addEventListener("change", () => {
      populateRoadDirections();
      renderMap();
    });
    byId("road-direction").addEventListener("change", renderMap);
    byId("road-fraction").addEventListener("input", renderMap);
    byId("phone-enabled").addEventListener("change", updatePlacementVisibility);
    byId("roadside-enabled").addEventListener("change", updatePlacementVisibility);
    byId("phone-add-window").addEventListener("click", () => {
      const day = Math.min(7, byId("phone-windows").children.length + 1);
      addScheduleRow("phone-windows", day, "08:00", "18:00");
      invalidateValidation();
    });
    byId("road-add-window").addEventListener("click", () => {
      const day = Math.min(7, byId("road-windows").children.length + 1);
      addScheduleRow("road-windows", day, "07:00", "19:00");
      invalidateValidation();
    });
    byId("validate-run").addEventListener("click", validateDraft);
    byId("start-run").addEventListener("click", startRun);
    byId("cancel-job").addEventListener("click", cancelActiveJob);
    byId("reset-draft").addEventListener("click", resetDraft);
    byId("library-previous").addEventListener("click", () => loadRunPage(Math.max(0, state.libraryOffset - state.libraryLimit)));
    byId("library-next").addEventListener("click", () => loadRunPage(state.libraryOffset + state.libraryLimit));
    byId("run-id").addEventListener("input", () => {
      byId("run-id").dataset.touched = "true";
    });
    byId("days").addEventListener("input", () => {
      const days = Number(byId("days").value);
      if (Number.isInteger(days) && days >= 1 && days <= 7) {
        setText("days-help", `Schedule rows beyond day ${days} remain visible and must be revised before validation.`);
      }
    });
    byId("main-content").addEventListener("input", () => {
      if (!state.resetting) {
        invalidateValidation();
      }
    });
    byId("main-content").addEventListener("change", () => {
      if (!state.resetting) {
        invalidateValidation();
      }
    });
    window.addEventListener("beforeunload", () => {
      state.shuttingDown = true;
      stopPolling();
    });
  }

  async function boot() {
    installEvents();
    resetSchedules();
    updatePlacementVisibility();
    clearFieldErrors();
    try {
      const [capabilitiesDocument, catalogDocument, templatesDocument, runsDocument] = await Promise.all([
        fetchJson("/api/workbench"),
        fetchJson("/api/catalog/cities"),
        fetchJson("/api/creative-templates"),
        fetchJson("/api/runs?offset=0&limit=20"),
      ]);
      state.capabilities = assertCapabilities(capabilitiesDocument);
      state.cities = assertCatalog(catalogDocument);
      state.templates = assertTemplates(templatesDocument);
      renderCatalog();
      renderTemplates();
      renderRunPage(assertRunPage(runsDocument));
      await loadCity(byId("city-select").value);
      const active = state.capabilities.active_job;
      if (active !== null) {
        const job = assertJob(active);
        beginPolling(job, `/api/jobs/${job.job_id}`);
        byId("stage-run").disabled = false;
      }
    } catch (error) {
      showBanner(error instanceof Error ? error.message : "The local workbench could not start.");
    }
  }

  boot();
})();
