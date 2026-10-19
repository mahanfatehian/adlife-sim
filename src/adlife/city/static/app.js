"use strict";

const byId = (id) => document.getElementById(id);
const portableRunIdPattern = /^[a-z0-9][a-z0-9-]{0,39}$/;
const viewTokenPattern = /^[A-Za-z0-9_-]{43}$/;
const reservedRunIds = new Set([
  "con", "prn", "aux", "nul",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
]);
const credentialShapedRunIdPatterns = [
  /^(?:sk|pk|rk)-(?:live|test|proj)-?[a-z0-9][a-z0-9-]{7,}$/,
  /^(?:sk|pk|rk)-[a-z0-9][a-z0-9-]{19,}$/,
  /^xox[abeprs]-[a-z0-9-]{10,}$/,
  /^xapp-[a-z0-9-]{10,}$/,
];
const viewerApiEndpoints = new Set([
  "/meta",
  "/city",
  "/agents",
  "/places",
  "/place-assignments",
  "/opportunity-summary",
  "/opportunities",
  "/attention-summary",
  "/spatial-metrics",
  "/spatial-response-metrics",
  "/attention-events",
  "/response-summary",
  "/response-events",
  "/response-state",
  "/frame",
  "/timeline",
  "/workbench-input",
  "/cognition-summary",
  "/cognition-records",
]);

function invalidApiBase() {
  throw new Error("Viewer API base is invalid");
}

function validatedApiBase() {
  const declarations = document.querySelectorAll('meta[name="adlife-api-base"]');
  if (declarations.length !== 1) invalidApiBase();
  const value = declarations[0].getAttribute("content");
  if (value === "/api") return value;
  if (typeof value !== "string") invalidApiBase();
  const match = /^\/api\/runs\/([a-z0-9][a-z0-9-]{0,39})$/.exec(value);
  if (!match) invalidApiBase();
  const runId = match[1];
  if (
    !portableRunIdPattern.test(runId)
    || reservedRunIds.has(runId)
    || credentialShapedRunIdPatterns.some((pattern) => pattern.test(runId))
  ) invalidApiBase();
  return value;
}

function validatedViewToken() {
  const declarations = document.querySelectorAll('meta[name="adlife-view-token"]');
  if (declarations.length !== 1) invalidApiBase();
  const value = declarations[0].getAttribute("content");
  if (value === "") return null;
  if (typeof value !== "string" || !viewTokenPattern.test(value)) invalidApiBase();
  return value;
}

const viewToken = validatedViewToken();

function apiPath(relative) {
  if (typeof relative !== "string" || relative.includes("#") || relative.includes("\\")) {
    invalidApiBase();
  }
  const path = relative.split("?", 1)[0];
  if (!viewerApiEndpoints.has(path)) invalidApiBase();
  return `${validatedApiBase()}${relative}`;
}

const EVIDENCE_PAGE_SIZE = 100;
const evidencePageKinds = ["opportunity", "attention", "response"];
const evidencePageContracts = {
  opportunity: {
    endpoint: "/opportunities",
    pageKey: "opportunityPage",
    schemaVersion: 1,
    claimScope: "synthetic-opportunity-not-impression",
    modelKey: null,
    modelId: "spatial-opportunity-v1",
    idKey: "opportunity_id",
    eventTypes: null,
    channelEventType: null,
    maximumTotal: 520800,
    keys: ["schema_version", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "channel_counts", "agent_counts", "next_offset", "items"],
  },
  attention: {
    endpoint: "/attention-events",
    pageKey: "attentionPage",
    schemaVersion: 1,
    claimScope: "synthetic-attention-not-observed-behavior",
    modelKey: "attention_model_id",
    modelId: "spatial-attention-v1",
    idKey: "event_id",
    eventTypes: ["spatial.impression", "spatial.noticed"],
    channelEventType: null,
    maximumTotal: 1041600,
    keys: ["schema_version", "attention_model_id", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "event_type_counts", "channel_counts", "agent_counts", "next_offset", "items"],
  },
  response: {
    endpoint: "/response-events",
    pageKey: "responsePage",
    schemaVersion: 1,
    claimScope: "synthetic-response-not-observed-behavior",
    modelKey: "response_model_id",
    modelId: "spatial-response-v1",
    idKey: "event_id",
    eventTypes: ["spatial.response", "spatial.state-updated"],
    channelEventType: "spatial.response",
    maximumTotal: 1041600,
    keys: ["schema_version", "response_model_id", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "event_type_counts", "channel_counts", "agent_counts", "next_offset", "items"],
  },
};
// The V10 response page keeps the V9 envelope members with an exact version-2 contract.
const boundedResponsePageContract = Object.freeze({
  ...evidencePageContracts.response,
  schemaVersion: 2,
  claimScope: "synthetic-bounded-cognition-not-observed-behavior",
  modelId: "spatial-response-v2",
  eventTypes: ["spatial.applied-response", "spatial.dual-state-updated"],
  channelEventType: "spatial.applied-response",
});
const evidenceChannels = ["roadside-billboard", "mobile-feed"];
const evidenceHashPattern = /^[0-9a-f]{64}$/;
const evidenceIdPattern = /^[a-z0-9][a-z0-9-]{0,79}$/;
const opportunityCommonKeys = [
  "schema_version", "model_id", "claim_scope", "opportunity_id", "scenario_sha256",
  "city_sha256", "campaign_id", "placement_id", "agent_id", "channel", "day_index",
  "model_minute", "millisecond_within_minute", "ordinal_for_agent_placement_day",
];
const opportunityVariantKeys = {
  "roadside-billboard": [
    ...opportunityCommonKeys, "basis", "road_id", "travel_direction", "road_fraction", "side",
    "minimum_distance_meters", "approach_distance_meters", "view_angle_degrees",
  ],
  "mobile-feed": [
    ...opportunityCommonKeys, "basis", "activity", "eligibility_draw",
    "opportunity_probability_per_minute",
  ],
};
const attentionCommonKeys = [
  "schema_version", "model_id", "claim_scope", "event_type", "event_id", "caused_by",
  "opportunity_id", "scenario_sha256", "city_sha256", "campaign_id", "placement_id",
  "agent_id", "channel", "day_index", "model_minute", "millisecond_within_minute",
  "notice_probability", "notice_draw",
];
const responseRuleKeys = [
  "schema_version", "model_id", "claim_scope", "event_type", "event_id", "caused_by",
  "opportunity_id", "response_input_sha256", "scenario_sha256", "city_sha256",
  "campaign_id", "placement_id", "agent_id", "channel", "day_index", "model_minute",
  "millisecond_within_minute", "state_before_sha256", "prior_notices_today",
  "frequency_cap_per_agent_per_day", "interest_match", "relative_price",
  "price_sensitivity", "affordability", "novelty_seeking", "advertising_skepticism",
  "channel_recall_encoding", "impulsivity", "frequency_fatigue", "value_match",
  "sentiment_delta", "recall_delta",
];
const responseStateKeys = [
  "schema_version", "agent_id", "campaign_id", "brand_sentiment", "recall_strength",
  "purchase_intention", "response_count", "last_response_minute",
];
const responseStateUpdateKeys = [
  "schema_version", "model_id", "claim_scope", "event_type", "event_id",
  "caused_by_event_ids", "response_input_sha256", "scenario_sha256", "city_sha256",
  "campaign_id", "agent_id", "day_index", "model_minute", "previous_state", "state",
];
const responseStatePageKeys = [
  "schema_version", "model_id", "claim_scope", "response_input_sha256",
  "scenario_sha256", "city_sha256", "state_scope", "agent_id", "offset", "limit",
  "total", "next_offset", "items",
];
const timelineBatchKeys = [
  "schema_version", "minute", "agent_id", "frame", "opportunity_page",
  "attention_page", "response_page", "response_state_page",
];
const timelineFrameKeys = ["minute", "positions", "route"];
const timelinePositionKeys = [
  "agent_id", "minute", "longitude", "latitude", "activity", "road_id",
  "coordinate_label",
];
const timelineRouteKeys = [
  "agent_id", "direction", "destination_activity", "node_ids", "road_ids",
  "directions", "geometry", "duration_minutes", "distance_meters",
];
const cognitionSummaryDocumentKeys = [
  "schema_version", "run_id", "run_schema_version", "cognition_input_sha256", "mode",
  "explainer_id", "provider_profile_id", "provider_revision_sha256", "model_id",
  "aggregate_output_token_ceiling", "summary",
];
const cognitionSummaryKeys = [
  "schema_version", "selected_request_count", "outcome_counts", "reason_counts",
  "attempt_count", "prompt_tokens", "completion_tokens", "terminal_stream_sha256",
  "terminal_stream_bytes", "terminal_record_count", "disclosure",
];
const cognitionOutcomeKeys = [
  "schema_version", "rules", "mock", "provider", "cache", "fallback", "budget_skipped",
];
const cognitionReasonKeys = [
  "schema_version", "endpoint_resolution_refused", "authentication_refused",
  "timeout_exhausted", "rate_limit_exhausted", "server_error_exhausted",
  "response_too_large", "protocol_incompatible", "invalid_result", "budget_skipped",
];
const cognitionRecordDocumentKeys = [
  "schema_version", "run_id", "run_schema_version", "cognition_input_sha256", "records",
];
const cognitionRecordKeys = [
  "schema_version", "selection_index", "request_id", "request_sha256", "run_input_sha256",
  "response_input_sha256", "scenario_sha256", "city_sha256", "response_event_id",
  "notice_event_id", "opportunity_id", "state_update_event_id", "agent_id", "campaign_id",
  "placement_id", "model_minute", "millisecond_within_minute", "state_update_cause_count",
  "source", "result", "attempt_count", "reason", "provider_profile_id",
  "provider_revision_sha256", "provider_profile_snapshot_sha256", "provider_protocol",
  "provider_effective_settings_sha256", "prompt_contract_sha256",
  "result_schema_contract_sha256", "selection_policy_sha256", "fallback_policy_sha256",
  "prompt_tokens", "completion_tokens", "disclosure", "record_hash",
];
const cognitionResultKeys = [
  "schema_version", "request_id", "interpretation", "decision_factors", "safety_flags",
  "disclosure",
];
const cognitionSources = ["rules", "mock", "provider", "cache", "fallback", "budget-skipped"];
const cognitionReasons = [
  "endpoint-resolution-refused", "authentication-refused", "timeout-exhausted",
  "rate-limit-exhausted", "server-error-exhausted", "response-too-large",
  "protocol-incompatible", "invalid-result", "budget-skipped",
];
const cognitionSafetyFlags = ["stereotype-risk", "unsupported-inference", "unsafe-content"];
const cognitionDisclosure = "synthetic-narrative-not-observed-behavior";
// Manifest-V10 bounded cognition: exact version-2 evidence, never a widened V1 contract.
const boundedRunSchemaVersion = 10;
const boundedEffectScope = "bounded-signals-v1";
const boundedResponseModelId = "spatial-response-v2";
const boundedClaimScope = "synthetic-bounded-cognition-not-observed-behavior";
const boundedHelperFunctions = [
  "validateSummary", "validateRecords", "indexTerminalRecords", "presentationForAppliedResponse",
];
const boundedAppliedSources = [...cognitionSources, "unselected"];
const boundedNeutralizationStatuses = [
  "none", "uncertain", "safety", "rules", "fallback", "budget-skipped", "unselected",
];
const boundedAppliedKeys = [
  "schema_version", "model_id", "claim_scope", "event_type", "event_id",
  "cognition_input_sha256", "baseline", "terminal_record_hash", "unselected_marker", "source",
  "neutralization_status", "influence_policy_sha256", "previous_rule_state_sha256",
  "previous_cognition_state_sha256", "applied_sentiment_delta", "applied_recall_delta",
];
const boundedDualKeys = [
  "schema_version", "model_id", "claim_scope", "event_type", "event_id",
  "caused_by_event_ids", "response_input_sha256", "scenario_sha256", "city_sha256",
  "cognition_input_sha256", "influence_policy_sha256", "campaign_id", "agent_id", "day_index",
  "model_minute", "previous_rule_state", "rule_state", "previous_cognition_state",
  "cognition_state",
];
const boundedCognitionStateKeys = [
  "schema_version", "agent_id", "campaign_id", "synthetic_sentiment", "synthetic_recall",
  "response_count", "last_response_minute",
];
const boundedStatePageKeys = [
  ...responseStatePageKeys, "cognition_input_sha256", "rule_state_model_id",
  "cognition_state_model_id",
];
const boundedStatePairKeys = ["rule_state", "cognition_state"];
const boundedResponseSummaryKeys = [
  "schema_version", "model_id", "claim_scope", "response_model_id", "response_input_sha256",
  "scenario_sha256", "city_sha256", "cognition_input_sha256", "influence_policy_sha256",
  "attention_seed", "stream_sha256", "stream_bytes", "state_document_sha256",
  "state_document_bytes", "rule_state_document_sha256", "cognition_state_document_sha256",
  "counts",
];
const boundedResponseCountKeys = [
  "schema_version", "baseline_response_count", "applied_response_count",
  "dual_state_update_count", "roadside_response_count", "phone_response_count",
  "campaign_count", "final_rule_state_count", "final_cognition_state_count",
];
const boundedWorkbenchCognitionKeys = [
  "effect_scope", "mode", "provider_profile_id", "provider_revision_sha256",
  "funded_response_limit", "causal_contract", "causal_contract_sha256",
  "request_schema_version", "selected_requests_per_agent", "selected_requests_per_run",
  "concurrency_limit", "max_request_bytes", "max_terminal_record_bytes",
  "max_wire_response_bytes", "max_output_tokens_per_attempt",
  "reservation_tokens_per_request", "initial_attempts", "transient_retries",
  "repair_attempts", "attempt_timeout_seconds", "aggregate_deadline_seconds",
  "aggregate_output_token_ceiling", "response_model", "rule_state_noninterference",
];
const boundedCausalContractKeys = [
  "schema_version", "disclosure", "prompt_contract_id", "prompt_contract_version",
  "prompt_contract_text", "prompt_contract_sha256", "result_schema_id", "result_schema_version",
  "result_schema_document", "result_schema_contract_sha256", "selection_policy_version",
  "selection_policy", "selection_policy_sha256", "fallback_policy_version", "fallback_policy",
  "fallback_policy_sha256", "mock_policy_version", "mock_policy", "mock_policy_sha256",
  "influence_policy_version", "influence_policy", "influence_policy_sha256",
];
const state = {
  meta: null,
  metaSeedToken: null,
  workbenchInput: null,
  personaByAgent: new Map(),
  city: null,
  agents: [],
  places: null,
  placeAssignments: [],
  opportunitySummary: null,
  opportunityPage: null,
  attentionSummary: null,
  attentionPage: null,
  responseSummary: null,
  responsePage: null,
  responseStatePage: null,
  spatialMetrics: null,
  spatialResponseMetrics: null,
  spatialResponseSeedToken: null,
  cognitionSummary: null,
  cognitionRecords: null,
  cognitionByResponseId: new Map(),
  boundedCognition: null,
  boundedSignalFilter: "all",
  frame: null,
  selected: null,
  selectedCampaign: null,
  selectedEvidence: null,
  causalSelectionGeneration: 0,
  minute: 0,
  playing: false,
  playbackGeneration: 0,
  playbackDelay: null,
  playbackSpeed: 1,
  playbackRestarting: false,
  zoom: 1,
  panX: 0,
  panY: 0,
  request: 0,
  timelineLoading: false,
  timelineAbortController: null,
  evidenceRequests: { opportunity: 0, attention: 0, response: 0 },
};
const MINIMUM_ZOOM = 0.5;
const MAXIMUM_ZOOM = 8;
const PLAYBACK_DELAY_MILLISECONDS = { 0.5: 800, 1: 400, 2: 200, 4: 100 };
const canvas = byId("city-map");
const ctx = canvas.getContext("2d");
const stage = byId("map-stage");
const activities = { home: "At home", commute: "Travelling on streets", work: "At work", leisure: "Leisure visit" };

async function fetchJson(relative, signal = null) {
  const url = apiPath(relative);
  const headers = viewToken === null ? {} : { "X-AdLife-View-Token": viewToken };
  const response = await fetch(url, { cache: "no-store", headers, signal });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  return response.json();
}

function topLevelIntegerToken(source, wantedKey) {
  let depth = 0;
  let found = null;
  for (let index = 0; index < source.length;) {
    const character = source[index];
    if (character === '"') {
      const start = index;
      index += 1;
      let escaped = false;
      while (index < source.length) {
        const stringCharacter = source[index];
        if (escaped) escaped = false;
        else if (stringCharacter === "\\") escaped = true;
        else if (stringCharacter === '"') break;
        index += 1;
      }
      if (index >= source.length) throw new Error(`Spatial response ${wantedKey} is invalid`);
      index += 1;
      if (depth !== 1) continue;
      let cursor = index;
      while (/\s/.test(source[cursor] || "")) cursor += 1;
      if (source[cursor] !== ":") continue;
      const key = JSON.parse(source.slice(start, index));
      if (key !== wantedKey) continue;
      cursor += 1;
      while (/\s/.test(source[cursor] || "")) cursor += 1;
      const match = /^(?:0|[1-9][0-9]*)/.exec(source.slice(cursor));
      if (!match) throw new Error(`Spatial response ${wantedKey} is invalid`);
      let end = cursor + match[0].length;
      while (/\s/.test(source[end] || "")) end += 1;
      if (![",", "}"].includes(source[end]) || found !== null) {
        throw new Error(`Spatial response ${wantedKey} is invalid`);
      }
      found = match[0];
      continue;
    }
    if (character === "{" || character === "[") depth += 1;
    else if (character === "}" || character === "]") depth -= 1;
    index += 1;
  }
  if (found === null) throw new Error(`Spatial response ${wantedKey} is invalid`);
  return found;
}

async function fetchJsonWithIntegerToken(relative, key) {
  const url = apiPath(relative);
  const headers = viewToken === null ? {} : { "X-AdLife-View-Token": viewToken };
  const response = await fetch(url, { cache: "no-store", headers });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  const source = await response.text();
  const document = JSON.parse(source);
  return { document, integerToken: topLevelIntegerToken(source, key) };
}

function updateErrorBannerInset() {
  const banner = byId("error-banner");
  if (banner.hidden) {
    banner.style.removeProperty("bottom");
    return;
  }
  const baseInset = window.matchMedia("(max-width: 900px)").matches ? 12 : 164;
  const timeline = document.querySelector(".timeline");
  let inset = baseInset;
  if (timeline instanceof HTMLElement) {
    const viewportHeight = window.innerHeight;
    const timelineRect = timeline.getBoundingClientRect();
    const visibleTimelineTop = Math.max(0, timelineRect.top);
    const visibleTimelineBottom = Math.min(viewportHeight, timelineRect.bottom);
    const bannerHeight = banner.getBoundingClientRect().height;
    const naturalBottom = viewportHeight - baseInset;
    const naturalTop = naturalBottom - bannerHeight;
    const overlapsVisibleTimeline = visibleTimelineTop < visibleTimelineBottom
      && naturalTop < visibleTimelineBottom
      && naturalBottom > visibleTimelineTop;
    if (overlapsVisibleTimeline) {
      inset = Math.max(baseInset, viewportHeight - visibleTimelineTop + 12);
    }
  }
  banner.style.bottom = `${Math.ceil(inset)}px`;
}

let errorBannerPositionFrame = null;

function scheduleErrorBannerInset() {
  if (errorBannerPositionFrame !== null) return;
  errorBannerPositionFrame = window.requestAnimationFrame(() => {
    errorBannerPositionFrame = null;
    updateErrorBannerInset();
  });
}

function showError(message, scope = "application") {
  const banner = byId("error-banner");
  banner.textContent = message;
  banner.dataset.errorScope = scope;
  banner.hidden = false;
  updateErrorBannerInset();
}

function clearError(scope) {
  const banner = byId("error-banner");
  if (banner.dataset.errorScope !== scope) return;
  banner.textContent = "";
  delete banner.dataset.errorScope;
  banner.hidden = true;
  banner.style.removeProperty("bottom");
}

function clockText(minute) {
  const hour = Math.floor((minute % 1440) / 60);
  const mins = minute % 60;
  return `${String(hour).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

function dayText(minute) { return `DAY ${String(Math.floor(minute / 1440) + 1).padStart(2, "0")}`; }

function nightAt(minute) {
  const hour = Math.floor((minute % 1440) / 60);
  return hour < 6 || hour >= 19;
}

function modelTimeZone() {
  return state.city && typeof state.city.time_zone === "string"
    ? state.city.time_zone
    : "not declared";
}

function updateLabels() {
  const minute = state.minute;
  const night = nightAt(minute);
  byId("day-label").textContent = dayText(minute);
  byId("clock-label").textContent = clockText(minute);
  byId("timeline-time").textContent = `${dayText(minute)} · ${clockText(minute)}`;
  byId("light-label").textContent = night ? "NIGHT" : "DAYLIGHT";
  const slider = byId("time-slider");
  slider.value = String(minute);
  slider.setAttribute(
    "aria-valuetext",
    `Day ${Math.floor(minute / 1440) + 1}, ${clockText(minute)} model time (${modelTimeZone()})`,
  );
  if (state.meta) {
    const dayIndex = Math.floor(minute / 1440);
    byId("previous-day-button").disabled = dayIndex === 0 || state.timelineLoading;
    byId("next-day-button").disabled = dayIndex === state.meta.days - 1 || state.timelineLoading;
  }
  document.body.classList.toggle("night", night);
}

const mapFitMargin = 55;
const mapOverlayGap = 12;

// Canvas-relative box of one map overlay, or null when it is not rendered.
function mapOverlayBox(selector, canvasBox) {
  const element = document.querySelector(selector);
  if (!element || element.hidden || element.getClientRects().length === 0) return null;
  const box = element.getBoundingClientRect();
  if (box.width === 0 || box.height === 0) return null;
  return {
    left: box.left - canvasBox.left,
    right: box.right - canvasBox.left,
    top: box.top - canvasBox.top,
    bottom: box.bottom - canvasBox.top,
  };
}

// The fitted city keeps clear of the overlays drawn above the canvas: the legend band at the
// bottom always, and the top-right controls either by a top band or a right band, whichever
// leaves the larger city. Pan and zoom still move the city freely from this fit.
let mapFitCache = null;
let mapFitFrame = null;

// Overlay boxes only change on a resize or a legend change; measuring them on every
// animation frame would force a synchronous layout during playback.
if (typeof ResizeObserver === "function") {
  const observer = new ResizeObserver(() => {
    mapFitCache = null;
    if (mapFitFrame !== null) return;
    mapFitFrame = window.requestAnimationFrame(() => {
      mapFitFrame = null;
      if (state.city && state.frame) renderMap();
    });
  });
  for (const selector of ["#map-stage", ".map-legend", ".map-zoom-controls", ".map-help", ".map-compass"]) {
    const element = document.querySelector(selector);
    if (element) observer.observe(element);
  }
}

function mapFitInsets(width, height, xSpan, ySpan) {
  const key = `${width}|${height}|${xSpan}|${ySpan}`;
  if (mapFitCache !== null && mapFitCache.key === key) return mapFitCache.insets;
  const insets = measureMapFitInsets(width, height, xSpan, ySpan);
  mapFitCache = { key, insets };
  return insets;
}

function measureMapFitInsets(width, height, xSpan, ySpan) {
  const canvasBox = canvas.getBoundingClientRect();
  const legend = mapOverlayBox(".map-legend", canvasBox);
  const help = mapOverlayBox(".map-help", canvasBox);
  const controls = [".map-compass", ".map-zoom-controls"]
    .map((selector) => mapOverlayBox(selector, canvasBox))
    .filter((box) => box !== null);
  const bottom = legend && legend.top < height
    ? Math.max(mapFitMargin, height - legend.top + mapOverlayGap)
    : mapFitMargin;
  const helpBottom = help ? help.bottom + mapOverlayGap : 0;
  const controlBottom = Math.max(0, ...controls.map((box) => box.bottom + mapOverlayGap));
  const controlLeft = Math.min(width, ...controls.map((box) => box.left - mapOverlayGap));
  const candidates = [
    { top: Math.max(mapFitMargin, helpBottom, controlBottom), right: mapFitMargin },
    { top: Math.max(mapFitMargin, helpBottom), right: Math.max(mapFitMargin, width - controlLeft) },
  ].map((insets) => ({ ...insets, left: mapFitMargin, bottom }));
  const scaleFor = (insets) => Math.min(
    (width - insets.left - insets.right) / xSpan,
    (height - insets.top - insets.bottom) / ySpan,
  );
  const best = scaleFor(candidates[1]) > scaleFor(candidates[0]) ? candidates[1] : candidates[0];
  // A tiny viewport keeps the plain centred fit rather than an unusable sliver.
  return scaleFor(best) > 0 ? best : { top: mapFitMargin, right: mapFitMargin, bottom: mapFitMargin, left: mapFitMargin };
}

function projection() {
  const nodes = state.city.nodes;
  const shape = state.city.roads.flatMap((road) => road.shape || []);
  const points = [...nodes, ...shape];
  const longs = points.map((point) => point.longitude);
  const lats = points.map((point) => point.latitude);
  const minLon = Math.min(...longs), maxLon = Math.max(...longs);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const midLat = (minLat + maxLat) / 2;
  const correction = Math.max(0.01, Math.cos(midLat * Math.PI / 180));
  const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
  const xSpan = Math.max(0.00001, (maxLon - minLon) * correction);
  const ySpan = Math.max(0.00001, maxLat - minLat);
  const insets = mapFitInsets(width, height, xSpan, ySpan);
  const fitWidth = Math.max(1, width - insets.left - insets.right);
  const fitHeight = Math.max(1, height - insets.top - insets.bottom);
  const scale = Math.min(fitWidth / xSpan, fitHeight / ySpan) * state.zoom;
  const centerX = insets.left + fitWidth / 2, centerY = insets.top + fitHeight / 2;
  const centerLon = (minLon + maxLon) / 2, centerLat = (minLat + maxLat) / 2;
  return (longitude, latitude) => [centerX + (longitude - centerLon) * correction * scale + state.panX, centerY - (latitude - centerLat) * scale + state.panY];
}

function selectedPlaceAssignment() {
  return state.placeAssignments.find((item) => item.agent_id === state.selected) || null;
}

function placeById(placeId) {
  if (!state.places) return null;
  return state.places.places.find((place) => place.place_id === placeId) || null;
}

function spatialPlacementById(placementId) {
  if (!state.opportunitySummary) return null;
  return state.opportunitySummary.placements.find((item) => item.placement_id === placementId) || null;
}

function spatialCampaignById(campaignId) {
  if (!state.opportunitySummary) return null;
  return state.opportunitySummary.campaigns.find((item) => item.campaign_id === campaignId) || null;
}

function boundedCognitionRun() {
  return state.meta !== null && state.meta.run_schema_version === boundedRunSchemaVersion;
}

function boundedCognitionHelper() {
  const helper = window.AdLifeBoundedCognition;
  if (
    !helper
    || typeof helper !== "object"
    || !Object.isFrozen(helper)
    || boundedHelperFunctions.some((name) => typeof helper[name] !== "function")
  ) throw new Error("Bounded cognition inspector assets are unavailable");
  return helper;
}

// A V10 applied response carries its identity in the embedded rule-owned baseline.
function evidenceSubject(item) {
  return item.event_type === "spatial.applied-response" ? item.baseline : item;
}

function responseEvidenceCauses(item) {
  if (item.event_type === "spatial.response") return [item.caused_by, item.opportunity_id];
  if (item.event_type === "spatial.applied-response") {
    return [item.baseline.caused_by, item.baseline.opportunity_id, item.baseline.event_id];
  }
  return item.caused_by_event_ids;
}

function causalEvidenceIds() {
  if (state.selectedEvidence === null) return new Set();
  const records = [];
  for (const item of state.opportunityPage ? state.opportunityPage.items : []) {
    records.push({ id: item.opportunity_id, causes: [] });
  }
  for (const item of state.attentionPage ? state.attentionPage.items : []) {
    records.push({ id: item.event_id, causes: [item.caused_by, item.opportunity_id] });
  }
  for (const item of state.responsePage ? state.responsePage.items : []) {
    records.push({ id: item.event_id, causes: responseEvidenceCauses(item) });
  }
  const graph = new Map();
  const connect = (left, right) => {
    if (!graph.has(left)) graph.set(left, new Set());
    if (!graph.has(right)) graph.set(right, new Set());
    graph.get(left).add(right);
    graph.get(right).add(left);
  };
  for (const record of records) {
    if (!graph.has(record.id)) graph.set(record.id, new Set());
    for (const cause of record.causes) connect(record.id, cause);
  }
  const related = new Set([state.selectedEvidence.id]);
  const pending = [state.selectedEvidence.id];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const adjacent of graph.get(current) || []) {
      if (related.has(adjacent)) continue;
      related.add(adjacent);
      pending.push(adjacent);
    }
  }
  return related;
}

function isCausalEvidence(evidenceId) {
  return causalEvidenceIds().has(evidenceId);
}

function drawPlaceMarker(place, x, y, selected) {
  const offsets = { home: -10, workplace: 0, leisure: 10 };
  const colors = { home: "#8fb7ff", workplace: "#dfb66b", leisure: "#d68fc4" };
  x += offsets[place.kind] || 0;
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = selected ? colors[place.kind] : "#102733";
  ctx.strokeStyle = colors[place.kind] || "#d8e2df";
  ctx.lineWidth = selected ? 3 : 2;
  ctx.beginPath();
  if (place.kind === "home") {
    ctx.moveTo(0, -7); ctx.lineTo(7, 0); ctx.lineTo(0, 7); ctx.lineTo(-7, 0); ctx.closePath();
  } else if (place.kind === "workplace") {
    ctx.rect(-6, -6, 12, 12);
  } else {
    ctx.moveTo(0, -7); ctx.lineTo(7, 6); ctx.lineTo(-7, 6); ctx.closePath();
  }
  ctx.fill(); ctx.stroke(); ctx.restore();
}

function drawOpportunityMarker(opportunity, project) {
  const placement = spatialPlacementById(opportunity.placement_id);
  const position = state.frame.positions.find((item) => item.agent_id === opportunity.agent_id);
  const coordinate = opportunity.channel === "roadside-billboard" && placement
    ? [placement.longitude, placement.latitude]
    : position ? [position.longitude, position.latitude] : null;
  if (!coordinate) return;
  const [x, y] = project(coordinate[0], coordinate[1]);
  const causal = isCausalEvidence(opportunity.opportunity_id);
  const selected = opportunity.agent_id === state.selected || causal;
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = selected ? "#fff3c8" : "#f0ca83";
  ctx.fillStyle = opportunity.channel === "roadside-billboard" ? "#f0ca83" : "#69d7e4";
  ctx.lineWidth = causal ? 5 : selected ? 3 : 2;
  ctx.shadowColor = ctx.fillStyle;
  ctx.shadowBlur = selected ? 14 : 8;
  ctx.beginPath();
  if (opportunity.channel === "roadside-billboard") {
    ctx.moveTo(0, -10); ctx.lineTo(10, 0); ctx.lineTo(0, 10); ctx.lineTo(-10, 0); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#0b141d"; ctx.fillRect(-2, -2, 4, 4);
  } else {
    ctx.arc(0, 0, selected ? 14 : 11, 0, Math.PI * 2); ctx.stroke();
    ctx.fillRect(-3, -5, 6, 10);
  }
  ctx.restore();
}

function drawAttentionMarker(event, project) {
  if (event.event_type !== "spatial.impression") return;
  const placement = spatialPlacementById(event.placement_id);
  const position = state.frame.positions.find((item) => item.agent_id === event.agent_id);
  const coordinate = event.channel === "roadside-billboard" && placement
    ? [placement.longitude, placement.latitude]
    : position ? [position.longitude, position.latitude] : null;
  if (!coordinate) return;
  const [x, y] = project(coordinate[0], coordinate[1]);
  const causal = isCausalEvidence(event.event_id);
  const selected = event.agent_id === state.selected || causal;
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = event.noticed ? "#77d6bd" : "#b6a7ef";
  ctx.lineWidth = causal ? 5 : selected ? 3 : 2;
  ctx.shadowColor = ctx.strokeStyle;
  ctx.shadowBlur = event.noticed ? 15 : 7;
  ctx.beginPath(); ctx.arc(0, 0, selected ? 18 : 15, 0, Math.PI * 2); ctx.stroke();
  if (event.noticed) {
    ctx.globalAlpha = .65;
    ctx.beginPath(); ctx.arc(0, 0, selected ? 24 : 21, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
}

function renderMap() {
  if (!state.city || !state.frame) return;
  const pixelRatio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * pixelRatio) || canvas.height !== Math.round(height * pixelRatio)) {
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
  }
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const project = projection();
  const nodes = new Map(state.city.nodes.map((node) => [node.node_id, node]));
  const roadColors = { motorway: "#b09e6c", trunk: "#b09e6c", primary: "#8b9e9e", secondary: "#698b96", tertiary: "#698b96", residential: "#466976", service: "#466976", path: "#466976" };
  for (const road of state.city.roads) {
    const start = nodes.get(road.source_node), end = nodes.get(road.target_node);
    const roadPoints = [start, ...(road.shape || []), end].map((point) => project(point.longitude, point.latitude));
    const drawRoad = () => {
      ctx.beginPath();
      for (const [index, point] of roadPoints.entries()) {
        if (index === 0) ctx.moveTo(point[0], point[1]); else ctx.lineTo(point[0], point[1]);
      }
      ctx.stroke();
    };
    ctx.strokeStyle = "#061923";
    ctx.lineWidth = road.kind === "motorway" || road.kind === "trunk" ? 12 : 8;
    ctx.lineCap = "round";
    drawRoad();
    ctx.strokeStyle = roadColors[road.kind] || "#466976";
    ctx.lineWidth -= 5;
    drawRoad();
    const oneDirection = road.one_way || (road.directions && road.directions.length === 1);
    if (oneDirection) {
      const arrowPoints = road.directions && road.directions[0] === "backward" ? [...roadPoints].reverse() : roadPoints;
      const [x1, y1] = arrowPoints[0], [x2, y2] = arrowPoints[arrowPoints.length - 1];
      const angle = Math.atan2(y2 - y1, x2 - x1), mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      ctx.strokeStyle = "#c2a96f"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(mx - 5 * Math.cos(angle - .6), my - 5 * Math.sin(angle - .6));
      ctx.lineTo(mx, my); ctx.lineTo(mx - 5 * Math.cos(angle + .6), my - 5 * Math.sin(angle + .6)); ctx.stroke();
    }
  }
  if (state.frame.route && state.frame.route.agent_id === state.selected) {
    const routeNodes = state.frame.route.node_ids;
    const routeGeometry = state.frame.route.geometry;
    if (routeGeometry.length > 0 || routeNodes.length > 1) {
      ctx.strokeStyle = "#f2ca7d";
      ctx.lineWidth = 4;
      ctx.lineJoin = "round";
      ctx.setLineDash([10, 6]);
      ctx.beginPath();
      if (routeGeometry.length > 0) {
        for (const line of routeGeometry) {
          for (const [index, point] of line.entries()) {
            const [x, y] = project(point.longitude, point.latitude);
            if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          }
        }
      } else {
        for (const [index, nodeId] of routeNodes.entries()) {
          const node = nodes.get(nodeId);
          const [x, y] = project(node.longitude, node.latitude);
          if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
  if (state.places) {
    const assignment = selectedPlaceAssignment();
    const selectedIds = new Set(assignment ? [assignment.home_place_id, assignment.work_place_id, assignment.leisure_place_id] : []);
    for (const place of state.places.places) {
      const node = nodes.get(place.node_id);
      if (!node) continue;
      const [x, y] = project(node.longitude, node.latitude);
      drawPlaceMarker(place, x, y, selectedIds.has(place.place_id));
    }
  }
  for (const node of state.city.nodes) {
    const [x, y] = project(node.longitude, node.latitude);
    ctx.fillStyle = "#a3c0bd"; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
  }
  const positions = [...state.frame.positions].sort((a, b) => (a.agent_id === state.selected ? 1 : 0) - (b.agent_id === state.selected ? 1 : 0));
  for (const position of positions) {
    const [x, y] = project(position.longitude, position.latitude);
    const selected = position.agent_id === state.selected;
    ctx.fillStyle = selected ? "#dfb66b" : (position.activity === "commute" ? "#e1c37f" : "#77d6bd");
    ctx.strokeStyle = selected ? "#f7f3de" : "#143642";
    ctx.lineWidth = selected ? 3 : 2;
    ctx.beginPath(); ctx.arc(x, y, selected ? 8 : 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    if (selected) {
      ctx.fillStyle = "#f5f0de"; ctx.font = "600 12px Consolas, monospace";
      ctx.fillText(position.agent_id.toUpperCase(), x + 14, y - 11);
    }
  }
  if (state.opportunityPage) {
    for (const opportunity of state.opportunityPage.items) {
      drawOpportunityMarker(opportunity, project);
    }
  }
  if (state.attentionPage) {
    for (const event of state.attentionPage.items) drawAttentionMarker(event, project);
  }
}

function renderPeople() {
  const list = byId("people-list");
  if (list.childElementCount === 0) {
    for (const agent of state.agents) {
      const row = document.createElement("button");
      row.type = "button"; row.className = "person";
      row.setAttribute("role", "listitem");
      const pip = document.createElement("span"); pip.className = "person-pip";
      const label = document.createElement("strong"); label.textContent = agent.agent_id.toUpperCase();
      const activity = document.createElement("small");
      row.append(pip, label, activity);
      const persona = state.personaByAgent.get(agent.agent_id);
      if (persona) {
        const name = document.createElement("bdi");
        name.dir = "auto";
        name.textContent = persona.display_name;
        row.classList.add("with-identity");
        row.append(name);
      }
      row.addEventListener("click", () => setMinute(state.minute, agent.agent_id));
      list.append(row);
    }
  }
  const positions = new Map(state.frame.positions.map((position) => [position.agent_id, position]));
  for (const [index, agent] of state.agents.entries()) {
    const row = list.children[index];
    row.classList.toggle("selected", agent.agent_id === state.selected);
    row.querySelector("small").textContent = positions.get(agent.agent_id).activity;
  }
}

function renderSelected() {
  const agent = state.agents.find((item) => item.agent_id === state.selected);
  const position = state.frame.positions.find((item) => item.agent_id === state.selected);
  if (!agent || !position) return;
  byId("selected-id").textContent = agent.agent_id.toUpperCase();
  const persona = state.personaByAgent.get(agent.agent_id);
  byId("selected-name").hidden = !persona;
  byId("identity-context").hidden = !persona;
  byId("identity-response-inputs").hidden = !persona;
  byId("selected-name").textContent = persona ? persona.display_name : "";
  for (const field of ["age", "occupation", "background", "mbti"]) {
    byId(`identity-${field}`).textContent = persona
      ? (persona[field] === null ? "Not provided" : String(persona[field]))
      : "";
  }
  const responseInputs = {
    "identity-interests": persona ? persona.interests.join(" · ") : "",
    "identity-trait-price-sensitivity": persona ? persona.traits.price_sensitivity : "",
    "identity-trait-novelty-seeking": persona ? persona.traits.novelty_seeking : "",
    "identity-trait-advertising-skepticism": persona
      ? persona.traits.advertising_skepticism : "",
    "identity-trait-mobile-recall-encoding": persona
      ? persona.traits.mobile_recall_encoding : "",
    "identity-trait-roadside-recall-encoding": persona
      ? persona.traits.roadside_recall_encoding : "",
    "identity-trait-impulsivity": persona ? persona.traits.impulsivity : "",
    "identity-initial-brand-sentiment": persona ? persona.initial_state.brand_sentiment : "",
    "identity-initial-recall-strength": persona ? persona.initial_state.recall_strength : "",
    "identity-initial-purchase-intention": persona
      ? persona.initial_state.purchase_intention : "",
  };
  for (const [id, value] of Object.entries(responseInputs)) {
    byId(id).textContent = String(value);
  }
  byId("selected-activity").textContent = activities[position.activity] || position.activity;
  byId("selected-place").textContent = position.coordinate_label;
  byId("home-node").textContent = agent.home_node;
  byId("work-node").textContent = agent.work_node;
  byId("leisure-node").textContent = agent.leisure_node;
  const assignment = selectedPlaceAssignment();
  if (assignment) {
    const home = placeById(assignment.home_place_id);
    const work = placeById(assignment.work_place_id);
    const leisure = placeById(assignment.leisure_place_id);
    byId("home-place").textContent = home ? home.label : assignment.home_place_id;
    byId("work-place").textContent = work ? work.label : assignment.work_place_id;
    byId("leisure-place").textContent = leisure ? leisure.label : assignment.leisure_place_id;
    const evidence = [home, work, leisure].filter(Boolean).map((place) => {
      const origin = place.provenance.method.replaceAll("-", " ");
      return `${place.label}: ${origin}${place.provenance.reference ? ` — ${place.provenance.reference}` : ""}`;
    });
    byId("place-evidence-text").textContent = evidence.join(" · ");
    byId("place-evidence").hidden = false;
  } else {
    byId("home-place").textContent = "Generated node";
    byId("work-place").textContent = "Generated node";
    byId("leisure-place").textContent = "Generated node";
    byId("place-evidence").hidden = true;
  }
  byId("road-segment").textContent = position.road_id || "At destination";
  const route = state.frame.route;
  if (route && route.agent_id === state.selected) {
    const listed = route.road_ids.slice(0, 6).join(" → ") || "No road travel";
    const remaining = route.road_ids.length > 6 ? ` → +${route.road_ids.length - 6} more` : "";
    byId("route-summary").textContent = `${route.direction === "return" ? "Return" : "Outbound"}: ${listed}${remaining} · ${(route.distance_meters / 1000).toFixed(1)} km`;
  } else {
    byId("route-summary").textContent = "Loading route…";
  }
}

function pageScopeText(label, page) {
  if (!page) return `${label}: unavailable`;
  if (page.total === 0) return `${label}: 0 records`;
  return `${label}: records ${page.offset + 1}-${page.offset + page.items.length} of ${page.total}`;
}

function renderCausalPresentation() {
  const campaignId = state.selectedCampaign;
  for (const id of ["response-causal", "response-metrics-panel"]) {
    const element = byId(id);
    element.classList.toggle("causal-selected", campaignId !== null);
    if (campaignId === null) delete element.dataset.campaignId;
    else element.dataset.campaignId = campaignId;
  }
  const campaignSelector = byId("response-metrics-campaign");
  if (campaignId === null) delete campaignSelector.dataset.causalCampaignId;
  else campaignSelector.dataset.causalCampaignId = campaignId;
  showResponseMetricSeries(campaignId);
}

function renderMapTextAlternative() {
  if (!state.frame) return;
  const position = state.frame.positions.find((item) => item.agent_id === state.selected);
  if (!position) return;
  const road = position.road_id ? position.road_id.toUpperCase() : "AT DESTINATION";
  let causal = "No campaign or evidence record selected.";
  if (state.selectedCampaign !== null) {
    causal = `Selected campaign ${state.selectedCampaign.toUpperCase()}.`;
  }
  if (state.selectedEvidence !== null) {
    const evidence = state.selectedEvidence;
    const placement = evidence.placementId
      ? spatialPlacementById(evidence.placementId)
      : null;
    const placementText = evidence.placementId
      ? ` Placement ${evidence.placementId.toUpperCase()}${placement ? ` (${placement.channel})` : ""}.`
      : " Placement provenance is carried by the selected state-update causes.";
    causal = `Selected ${evidence.kind} evidence ${evidence.id.toUpperCase()}; campaign ${evidence.campaignId.toUpperCase()}.${placementText}`;
  }
  byId("map-text-summary").textContent = `Day ${Math.floor(state.minute / 1440) + 1}, ${clockText(state.minute)} model time. ${state.selected.toUpperCase()} activity ${position.activity.toUpperCase()}, road ${road}. ${causal}`;
  byId("map-evidence-scope").textContent = `page-scoped map markers; ${pageScopeText("opportunities", state.opportunityPage)}; ${pageScopeText("attention", state.attentionPage)}; ${pageScopeText("responses", state.responsePage)}. Full-run totals remain unfiltered.`;
  renderCausalPresentation();
}

function selectedEvidenceIdentity(kind, item) {
  const subject = evidenceSubject(item);
  return {
    kind,
    id: kind === "opportunity" ? item.opportunity_id : item.event_id,
    campaignId: subject.campaign_id,
    placementId: typeof subject.placement_id === "string" ? subject.placement_id : null,
  };
}

// V10 response metrics expose the rule-owned series under rule_baseline; V6-V9 keep overall.
function responseMetricSeriesSource() {
  const metrics = state.spatialResponseMetrics;
  return boundedCognitionRun()
    ? { overall: metrics.rule_baseline, campaigns: metrics.rule_baseline.campaigns }
    : { overall: metrics.overall, campaigns: metrics.campaigns };
}

function showResponseMetricSeries(campaignId) {
  if (!state.spatialResponseMetrics) return;
  const selector = byId("response-metrics-campaign");
  const source = responseMetricSeriesSource();
  if (campaignId === null) {
    selector.value = "overall";
    renderResponseMetricsState(source.overall, "ALL CAMPAIGNS");
    return;
  }
  const campaign = source.campaigns.find(
    (item) => item.campaign_id === campaignId,
  );
  if (!campaign) return;
  selector.value = campaignId;
  renderResponseMetricsState(campaign, `CAMPAIGN / ${campaignId.toUpperCase()}`);
}

async function selectCausalEvidence(kind, item) {
  stopPlayback();
  const generation = ++state.causalSelectionGeneration;
  const evidence = selectedEvidenceIdentity(kind, item);
  const subject = evidenceSubject(item);
  if (!await setMinute(state.minute, subject.agent_id, null, false)) return;
  if (generation !== state.causalSelectionGeneration) return;
  state.selectedCampaign = subject.campaign_id;
  state.selectedEvidence = evidence;
  renderOpportunityEvidence();
  renderAttentionEvidence();
  renderResponseEvidence();
  renderMap();
  renderMapTextAlternative();
  announceTimeline(byId("map-text-summary").textContent);
  const selectedCard = document.querySelector(`[data-evidence-id="${evidence.id}"]`);
  if (selectedCard instanceof HTMLElement) selectedCard.focus();
}

function evidenceLabel(kind) {
  return `${kind[0].toUpperCase()}${kind.slice(1)} evidence page`;
}

function evidenceValidationError(kind) {
  return new Error(`${evidenceLabel(kind)} is invalid`);
}

function validEvidenceId(value) {
  return typeof value === "string" && evidenceIdPattern.test(value);
}

function validEvidenceHash(value) {
  return typeof value === "string" && evidenceHashPattern.test(value);
}

function validEvidenceNumber(value, minimum, maximum) {
  return typeof value === "number"
    && Number.isFinite(value)
    && value >= minimum
    && value <= maximum;
}

function knownEvidenceAgents() {
  return new Set(state.agents.map((agent) => agent.agent_id));
}

function evidenceReference(item) {
  if (!state.opportunitySummary) return null;
  const campaign = state.opportunitySummary.campaigns.find(
    (candidate) => candidate.campaign_id === item.campaign_id,
  );
  const placement = state.opportunitySummary.placements.find(
    (candidate) => candidate.placement_id === item.placement_id,
  );
  if (
    !campaign
    || !placement
    || placement.campaign_id !== item.campaign_id
    || placement.channel !== item.channel
  ) return null;
  return placement;
}

function expectedEvidenceScenarioHash(kind) {
  const summary = kind === "opportunity"
    ? state.opportunitySummary
    : kind === "attention" ? state.attentionSummary : state.responseSummary;
  return summary && summary.scenario_sha256;
}

function validateCommonEvidenceRecord(kind, item, requestedMinute) {
  if (
    item.schema_version !== 1
    || item.model_id !== evidencePageContracts[kind].modelId
    || item.claim_scope !== evidencePageContracts[kind].claimScope
    || !validEvidenceHash(item.scenario_sha256)
    || item.scenario_sha256 !== expectedEvidenceScenarioHash(kind)
    || !validEvidenceHash(item.city_sha256)
    || item.city_sha256 !== state.meta.city_sha256
    || !validEvidenceId(item.campaign_id)
    || !validEvidenceId(item.agent_id)
    || !knownEvidenceAgents().has(item.agent_id)
    || !Number.isInteger(item.day_index)
    || item.day_index < 0
    || item.day_index > 6
    || item.model_minute !== requestedMinute
    || item.day_index !== Math.floor(requestedMinute / 1440)
  ) throw evidenceValidationError(kind);
  if (
    state.responseSummary
    && kind === "response"
    && item.city_sha256 !== state.responseSummary.city_sha256
  ) throw evidenceValidationError(kind);
}

function validateOpportunityRecord(itemValue, requestedMinute) {
  const kind = "opportunity";
  const item = requireRecord(itemValue, "Opportunity evidence record");
  const expectedKeys = opportunityVariantKeys[item.channel];
  if (!expectedKeys || !hasExactKeys(item, expectedKeys)) throw evidenceValidationError(kind);
  validateCommonEvidenceRecord(kind, item, requestedMinute);
  const placement = evidenceReference(item);
  if (
    !placement
    || !validEvidenceHash(item.opportunity_id)
    || !validEvidenceId(item.placement_id)
    || !Number.isInteger(item.millisecond_within_minute)
    || item.millisecond_within_minute < 0
    || item.millisecond_within_minute >= 60000
    || !Number.isInteger(item.ordinal_for_agent_placement_day)
    || item.ordinal_for_agent_placement_day < 1
    || item.ordinal_for_agent_placement_day > 100
  ) throw evidenceValidationError(kind);
  if (item.channel === "roadside-billboard") {
    if (
      item.basis !== "directional-road-passage-v1"
      || !validEvidenceId(item.road_id)
      || item.road_id !== placement.road_id
      || !["forward", "backward"].includes(item.travel_direction)
      || item.travel_direction !== placement.travel_direction
      || !validEvidenceNumber(item.road_fraction, 0, 1)
      || item.road_fraction === 0
      || item.road_fraction === 1
      || item.road_fraction !== placement.road_fraction
      || !["left", "right"].includes(item.side)
      || item.side !== placement.side
      || !validEvidenceNumber(item.minimum_distance_meters, 0, 1)
      || !validEvidenceNumber(item.approach_distance_meters, 0, 1000)
      || item.approach_distance_meters === 0
      || !validEvidenceNumber(item.view_angle_degrees, 0, 90)
    ) throw evidenceValidationError(kind);
  } else if (
    item.basis !== "keyed-activity-minute-v1"
    || !["home", "commute", "work", "leisure"].includes(item.activity)
    || !Array.isArray(placement.eligible_activities)
    || !placement.eligible_activities.includes(item.activity)
    || !validEvidenceNumber(item.eligibility_draw, 0, 1)
    || item.eligibility_draw === 1
    || !validEvidenceNumber(item.opportunity_probability_per_minute, 0, 1)
    || item.opportunity_probability_per_minute !== placement.opportunity_probability_per_minute
    || item.eligibility_draw >= item.opportunity_probability_per_minute
  ) throw evidenceValidationError(kind);
}

function validateAttentionRecord(itemValue, requestedMinute) {
  const kind = "attention";
  const item = requireRecord(itemValue, "Attention evidence record");
  const expectedKeys = item.event_type === "spatial.impression"
    ? [...attentionCommonKeys, "noticed"]
    : attentionCommonKeys;
  if (!hasExactKeys(item, expectedKeys)) throw evidenceValidationError(kind);
  validateCommonEvidenceRecord(kind, item, requestedMinute);
  if (
    !evidencePageContracts.attention.eventTypes.includes(item.event_type)
    || !validEvidenceHash(item.event_id)
    || !validEvidenceHash(item.caused_by)
    || !validEvidenceHash(item.opportunity_id)
    || !validEvidenceId(item.placement_id)
    || !evidenceChannels.includes(item.channel)
    || !evidenceReference(item)
    || !Number.isInteger(item.millisecond_within_minute)
    || item.millisecond_within_minute < 0
    || item.millisecond_within_minute >= 60000
    || item.notice_probability !== 0.5
    || !validEvidenceNumber(item.notice_draw, 0, 1)
    || item.notice_draw === 1
  ) throw evidenceValidationError(kind);
  if (item.event_type === "spatial.impression") {
    if (
      typeof item.noticed !== "boolean"
      || item.noticed !== (item.notice_draw < item.notice_probability)
      || item.caused_by !== item.opportunity_id
    ) throw evidenceValidationError(kind);
  } else if (item.notice_draw >= item.notice_probability) {
    throw evidenceValidationError(kind);
  }
}

function validateResponseStateValue(value, kind) {
  const responseState = requireRecord(value, "Spatial response state");
  if (
    !hasExactKeys(responseState, responseStateKeys)
    || responseState.schema_version !== 1
    || !validEvidenceId(responseState.agent_id)
    || !knownEvidenceAgents().has(responseState.agent_id)
    || !validEvidenceId(responseState.campaign_id)
    || !state.opportunitySummary.campaigns.some(
      (campaign) => campaign.campaign_id === responseState.campaign_id,
    )
    || !validEvidenceNumber(responseState.brand_sentiment, -1, 1)
    || !validEvidenceNumber(responseState.recall_strength, 0, 1)
    || !validEvidenceNumber(responseState.purchase_intention, 0, 1)
    || !Number.isInteger(responseState.response_count)
    || responseState.response_count < 0
    || responseState.response_count > 520800
    || (
      responseState.last_response_minute !== null
      && (
        !Number.isInteger(responseState.last_response_minute)
        || responseState.last_response_minute < 0
        || responseState.last_response_minute >= state.meta.days * 1440
      )
    )
    || ((responseState.response_count === 0) !== (responseState.last_response_minute === null))
  ) throw evidenceValidationError(kind);
  return responseState;
}

// The V10 cognition track: synthetic sentiment/recall and progress only, never purchase.
function validateCognitionStateValue(value, kind) {
  const cognitionState = requireRecord(value, "Synthetic cognition state");
  if (
    !hasExactKeys(cognitionState, boundedCognitionStateKeys)
    || cognitionState.schema_version !== 2
    || !validEvidenceId(cognitionState.agent_id)
    || !knownEvidenceAgents().has(cognitionState.agent_id)
    || !validEvidenceId(cognitionState.campaign_id)
    || !state.opportunitySummary.campaigns.some(
      (campaign) => campaign.campaign_id === cognitionState.campaign_id,
    )
    || !validEvidenceNumber(cognitionState.synthetic_sentiment, -1, 1)
    || !validEvidenceNumber(cognitionState.synthetic_recall, 0, 1)
    || !Number.isInteger(cognitionState.response_count)
    || cognitionState.response_count < 0
    || cognitionState.response_count > 520800
    || (
      cognitionState.last_response_minute !== null
      && (
        !Number.isInteger(cognitionState.last_response_minute)
        || cognitionState.last_response_minute < 0
        || cognitionState.last_response_minute >= state.meta.days * 1440
      )
    )
    || ((cognitionState.response_count === 0) !== (cognitionState.last_response_minute === null))
  ) throw evidenceValidationError(kind);
  return cognitionState;
}

function timelineFrameValidationError() {
  return new Error("Timeline frame is invalid");
}

function validateTimelineCoordinate(value) {
  const coordinate = requireRecord(value, "Timeline coordinate");
  if (
    !hasExactKeys(coordinate, ["longitude", "latitude"])
    || !validEvidenceNumber(coordinate.longitude, -180, 180)
    || !validEvidenceNumber(coordinate.latitude, -90, 90)
  ) throw timelineFrameValidationError();
  return coordinate;
}

function validateTimelineFrame(value, requestedMinute, selectedAgent) {
  try {
    const frame = requireRecord(value, "Timeline frame");
    if (
      !hasExactKeys(frame, timelineFrameKeys)
      || frame.minute !== requestedMinute
      || !Array.isArray(frame.positions)
      || frame.positions.length !== state.agents.length
    ) throw timelineFrameValidationError();
    const knownAgents = knownEvidenceAgents();
    const knownRoads = new Set(state.city.roads.map((road) => road.road_id));
    const knownNodes = new Set(state.city.nodes.map((node) => node.node_id));
    const positionedAgents = new Set();
    for (const positionValue of frame.positions) {
      const position = requireRecord(positionValue, "Timeline position");
      if (
        !hasExactKeys(position, timelinePositionKeys)
        || !knownAgents.has(position.agent_id)
        || positionedAgents.has(position.agent_id)
        || position.minute !== requestedMinute
        || !validEvidenceNumber(position.longitude, -180, 180)
        || !validEvidenceNumber(position.latitude, -90, 90)
        || !["home", "commute", "work", "leisure"].includes(position.activity)
        || (
          position.road_id !== null
          && (!validEvidenceId(position.road_id) || !knownRoads.has(position.road_id))
        )
        || typeof position.coordinate_label !== "string"
        || Array.from(position.coordinate_label).length < 1
        || Array.from(position.coordinate_label).length > 160
        || /[\u0000-\u001f\u007f-\u009f]/.test(position.coordinate_label)
      ) throw timelineFrameValidationError();
      positionedAgents.add(position.agent_id);
    }
    if (
      positionedAgents.size !== knownAgents.size
      || [...knownAgents].some((agentId) => !positionedAgents.has(agentId))
    ) throw timelineFrameValidationError();

    const route = requireRecord(frame.route, "Timeline route");
    if (
      !hasExactKeys(route, timelineRouteKeys)
      || route.agent_id !== selectedAgent
      || !knownAgents.has(route.agent_id)
      || !["outbound", "return"].includes(route.direction)
      || !["work", "leisure"].includes(route.destination_activity)
      || !Array.isArray(route.node_ids)
      || !Array.isArray(route.road_ids)
      || !Array.isArray(route.directions)
      || !Array.isArray(route.geometry)
      || route.node_ids.length !== route.road_ids.length + 1
      || route.directions.length !== route.road_ids.length
      || route.geometry.length !== route.road_ids.length
      || route.node_ids.length < 1
      || route.node_ids.some((nodeId) => !validEvidenceId(nodeId) || !knownNodes.has(nodeId))
      || route.road_ids.some((roadId) => !validEvidenceId(roadId) || !knownRoads.has(roadId))
      || route.directions.some((direction) => !["forward", "backward"].includes(direction))
      || !validEvidenceNumber(route.duration_minutes, 0, 1_000_000_000)
      || !validEvidenceNumber(route.distance_meters, 0, 1_000_000_000)
    ) throw timelineFrameValidationError();
    for (const line of route.geometry) {
      if (!Array.isArray(line) || line.length < 2 || line.length > 100_000) {
        throw timelineFrameValidationError();
      }
      for (const coordinate of line) validateTimelineCoordinate(coordinate);
    }
    return frame;
  } catch (error) {
    if (error instanceof Error && error.message === "Timeline frame is invalid") throw error;
    throw timelineFrameValidationError();
  }
}

function responseStatePageValidationError() {
  return new Error("Spatial response state page is invalid");
}

function validateBoundedResponseStatePage(value, selectedAgent, requestedOffset) {
  try {
    const page = requireRecord(value, "Spatial response state page");
    const summary = state.responseSummary;
    const campaigns = state.opportunitySummary && state.opportunitySummary.campaigns;
    if (!summary || summary.schema_version !== 2 || !Array.isArray(campaigns)) {
      throw responseStatePageValidationError();
    }
    const campaignIds = campaigns.map((campaign) => campaign.campaign_id);
    const total = requireBoundedInteger(page.total, 0, 600, "Response state total");
    if (requestedOffset > total || (total > 0 && requestedOffset === total)) {
      throw responseStatePageValidationError();
    }
    const expectedLength = total === 0 ? 0 : Math.min(EVIDENCE_PAGE_SIZE, total - requestedOffset);
    const expectedNext = requestedOffset + expectedLength < total
      ? requestedOffset + expectedLength
      : null;
    if (
      !hasExactKeys(page, boundedStatePageKeys)
      || page.schema_version !== 2
      || page.model_id !== "spatial-response-state-v2"
      || page.claim_scope !== boundedClaimScope
      || page.response_input_sha256 !== summary.response_input_sha256
      || page.scenario_sha256 !== summary.scenario_sha256
      || page.scenario_sha256 !== state.opportunitySummary.scenario_sha256
      || page.city_sha256 !== summary.city_sha256
      || page.city_sha256 !== state.meta.city_sha256
      || page.cognition_input_sha256 !== summary.cognition_input_sha256
      || page.rule_state_model_id !== "spatial-rule-state-v2"
      || page.cognition_state_model_id !== "spatial-cognition-state-v2"
      || page.state_scope !== "final-end-of-run-not-scrubbed-minute"
      || page.agent_id !== selectedAgent
      || !knownEvidenceAgents().has(page.agent_id)
      || page.offset !== requestedOffset
      || page.limit !== EVIDENCE_PAGE_SIZE
      || total !== summary.counts.campaign_count
      || summary.counts.final_rule_state_count !== state.agents.length * total
      || !Array.isArray(page.items)
      || page.items.length !== expectedLength
      || page.next_offset !== expectedNext
    ) throw responseStatePageValidationError();
    const seenCampaigns = [];
    for (const pairValue of page.items) {
      const pair = requireRecord(pairValue, "Spatial response state pair");
      if (!hasExactKeys(pair, boundedStatePairKeys)) throw responseStatePageValidationError();
      const rule = validateResponseStateValue(pair.rule_state, "response");
      const cognition = validateCognitionStateValue(pair.cognition_state, "response");
      if (
        rule.agent_id !== selectedAgent
        || cognition.agent_id !== selectedAgent
        || cognition.campaign_id !== rule.campaign_id
        || cognition.response_count !== rule.response_count
        || cognition.last_response_minute !== rule.last_response_minute
        || seenCampaigns.includes(rule.campaign_id)
      ) throw responseStatePageValidationError();
      seenCampaigns.push(rule.campaign_id);
    }
    const expectedCampaigns = campaignIds.slice(requestedOffset, requestedOffset + expectedLength);
    if (!sameStrings(seenCampaigns, expectedCampaigns)) throw responseStatePageValidationError();
    return page;
  } catch (error) {
    if (
      error instanceof Error
      && error.message === "Spatial response state page is invalid"
    ) throw error;
    throw responseStatePageValidationError();
  }
}

function validateResponseStatePage(value, selectedAgent, requestedOffset) {
  if (boundedCognitionRun()) {
    return validateBoundedResponseStatePage(value, selectedAgent, requestedOffset);
  }
  try {
    const page = requireRecord(value, "Spatial response state page");
    const summary = state.responseSummary;
    const campaigns = state.opportunitySummary && state.opportunitySummary.campaigns;
    if (!summary || !Array.isArray(campaigns)) throw responseStatePageValidationError();
    const campaignIds = campaigns.map((campaign) => campaign.campaign_id);
    const total = requireBoundedInteger(page.total, 0, 600, "Response state total");
    if (requestedOffset > total || (total > 0 && requestedOffset === total)) {
      throw responseStatePageValidationError();
    }
    const expectedLength = total === 0 ? 0 : Math.min(EVIDENCE_PAGE_SIZE, total - requestedOffset);
    const expectedNext = requestedOffset + expectedLength < total
      ? requestedOffset + expectedLength
      : null;
    if (
      !hasExactKeys(page, responseStatePageKeys)
      || page.schema_version !== 1
      || page.model_id !== "spatial-response-state-v1"
      || page.claim_scope !== "synthetic-response-not-observed-behavior"
      || !validEvidenceHash(page.response_input_sha256)
      || page.response_input_sha256 !== summary.response_input_sha256
      || !validEvidenceHash(page.scenario_sha256)
      || page.scenario_sha256 !== summary.scenario_sha256
      || page.scenario_sha256 !== state.opportunitySummary.scenario_sha256
      || !validEvidenceHash(page.city_sha256)
      || page.city_sha256 !== summary.city_sha256
      || page.city_sha256 !== state.meta.city_sha256
      || page.state_scope !== "final-end-of-run-not-scrubbed-minute"
      || page.agent_id !== selectedAgent
      || !knownEvidenceAgents().has(page.agent_id)
      || page.offset !== requestedOffset
      || page.limit !== EVIDENCE_PAGE_SIZE
      || total !== summary.counts.campaign_count
      || total !== state.meta.response_campaign_count
      || summary.counts.final_state_count !== state.agents.length * total
      || summary.counts.final_state_count !== state.meta.final_state_count
      || !Array.isArray(page.items)
      || page.items.length !== expectedLength
      || page.next_offset !== expectedNext
    ) throw responseStatePageValidationError();
    const seenCampaigns = new Set();
    for (const itemValue of page.items) {
      const item = validateResponseStateValue(itemValue, "response");
      if (
        item.agent_id !== selectedAgent
        || !campaignIds.includes(item.campaign_id)
        || seenCampaigns.has(item.campaign_id)
      ) throw responseStatePageValidationError();
      seenCampaigns.add(item.campaign_id);
    }
    const expectedCampaigns = campaignIds.slice(requestedOffset, requestedOffset + expectedLength);
    if (!sameStrings([...seenCampaigns], expectedCampaigns)) {
      throw responseStatePageValidationError();
    }
    return page;
  } catch (error) {
    if (
      error instanceof Error
      && error.message === "Spatial response state page is invalid"
    ) throw error;
    throw responseStatePageValidationError();
  }
}

function completeTimelineBatchAvailable() {
  return state.meta.spatial_opportunities === true
    && state.meta.spatial_attention === true
    && state.meta.spatial_response === true;
}

function timelineBatchUrl(minute, selectedAgent, offsets) {
  return `/timeline?minute=${minute}`
    + `&agent_id=${encodeURIComponent(selectedAgent)}`
    + `&opportunity_offset=${offsets.opportunity}`
    + `&attention_offset=${offsets.attention}`
    + `&response_offset=${offsets.response}`
    + `&response_state_offset=0&limit=${EVIDENCE_PAGE_SIZE}`;
}

function validateTimelineBatch(value, requestedMinute, selectedAgent, offsets) {
  const batch = requireRecord(value, "Timeline batch");
  if (
    !hasExactKeys(batch, timelineBatchKeys)
    || batch.schema_version !== 1
    || batch.minute !== requestedMinute
    || batch.agent_id !== selectedAgent
  ) throw new Error("Timeline batch is invalid");
  return {
    frame: validateTimelineFrame(batch.frame, requestedMinute, selectedAgent),
    opportunityPage: validateEvidencePage(
      "opportunity", batch.opportunity_page, requestedMinute, offsets.opportunity,
    ),
    attentionPage: validateEvidencePage(
      "attention", batch.attention_page, requestedMinute, offsets.attention,
    ),
    responsePage: validateEvidencePage(
      "response", batch.response_page, requestedMinute, offsets.response,
    ),
    responseStatePage: validateResponseStatePage(
      batch.response_state_page, selectedAgent, 0,
    ),
  };
}

function validateResponseRecord(itemValue, requestedMinute) {
  const kind = "response";
  const item = requireRecord(itemValue, "Response evidence record");
  const expectedKeys = item.event_type === "spatial.response"
    ? responseRuleKeys
    : responseStateUpdateKeys;
  if (!hasExactKeys(item, expectedKeys)) throw evidenceValidationError(kind);
  validateCommonEvidenceRecord(kind, item, requestedMinute);
  if (
    !evidencePageContracts.response.eventTypes.includes(item.event_type)
    || !validEvidenceHash(item.event_id)
    || !validEvidenceHash(item.response_input_sha256)
    || item.response_input_sha256 !== state.responseSummary.response_input_sha256
  ) throw evidenceValidationError(kind);
  if (item.event_type === "spatial.response") {
    const unitIntervalFields = [
      "interest_match", "price_sensitivity", "affordability", "novelty_seeking",
      "advertising_skepticism", "channel_recall_encoding", "impulsivity",
      "frequency_fatigue", "value_match",
    ];
    if (
      !validEvidenceHash(item.caused_by)
      || !validEvidenceHash(item.opportunity_id)
      || !validEvidenceId(item.placement_id)
      || !evidenceChannels.includes(item.channel)
      || !evidenceReference(item)
      || !Number.isInteger(item.millisecond_within_minute)
      || item.millisecond_within_minute < 0
      || item.millisecond_within_minute >= 60000
      || !validEvidenceHash(item.state_before_sha256)
      || !Number.isInteger(item.prior_notices_today)
      || item.prior_notices_today < 0
      || item.prior_notices_today > 100
      || !Number.isInteger(item.frequency_cap_per_agent_per_day)
      || item.frequency_cap_per_agent_per_day < 1
      || item.frequency_cap_per_agent_per_day > 100
      || item.prior_notices_today >= item.frequency_cap_per_agent_per_day
      || item.frequency_cap_per_agent_per_day !== evidenceReference(item).frequency_cap_per_agent_per_day
      || !validEvidenceNumber(item.relative_price, 0, 100)
      || item.relative_price === 0
      || unitIntervalFields.some((field) => !validEvidenceNumber(item[field], 0, 1))
      || !validEvidenceNumber(item.sentiment_delta, -0.2, 0.2)
      || !validEvidenceNumber(item.recall_delta, 0, 0.3)
    ) throw evidenceValidationError(kind);
    return;
  }
  if (
    !Array.isArray(item.caused_by_event_ids)
    || item.caused_by_event_ids.length < 1
    || item.caused_by_event_ids.length > 520800
    || item.caused_by_event_ids.some((identifier) => !validEvidenceHash(identifier))
    || new Set(item.caused_by_event_ids).size !== item.caused_by_event_ids.length
  ) throw evidenceValidationError(kind);
  const previousState = validateResponseStateValue(item.previous_state, kind);
  const updatedState = validateResponseStateValue(item.state, kind);
  if (
    previousState.agent_id !== item.agent_id
    || updatedState.agent_id !== item.agent_id
    || previousState.campaign_id !== item.campaign_id
    || updatedState.campaign_id !== item.campaign_id
    || updatedState.response_count !== previousState.response_count + item.caused_by_event_ids.length
    || updatedState.last_response_minute !== requestedMinute
    || (
      previousState.last_response_minute !== null
      && previousState.last_response_minute >= requestedMinute
    )
  ) throw evidenceValidationError(kind);
}

function validateBoundedResponseRecord(itemValue, requestedMinute) {
  const kind = "response";
  const item = requireRecord(itemValue, "Response evidence record");
  const summary = state.responseSummary;
  if (
    !summary
    || summary.schema_version !== 2
    || item.schema_version !== 2
    || item.model_id !== boundedResponseModelId
    || item.claim_scope !== boundedClaimScope
    || !validEvidenceHash(item.event_id)
    || item.cognition_input_sha256 !== summary.cognition_input_sha256
    || item.influence_policy_sha256 !== summary.influence_policy_sha256
  ) throw evidenceValidationError(kind);
  if (item.event_type === "spatial.applied-response") {
    if (!hasExactKeys(item, boundedAppliedKeys)) throw evidenceValidationError(kind);
    const baseline = requireRecord(item.baseline, "Applied response baseline");
    if (baseline.event_type !== "spatial.response") throw evidenceValidationError(kind);
    // The embedded baseline is the complete, unchanged V1 rule response.
    validateResponseRecord(baseline, requestedMinute);
    const selected = item.source !== "unselected";
    if (
      !boundedAppliedSources.includes(item.source)
      || !boundedNeutralizationStatuses.includes(item.neutralization_status)
      || item.previous_rule_state_sha256 !== baseline.state_before_sha256
      || !validEvidenceHash(item.previous_cognition_state_sha256)
      || (
        selected
          ? !validEvidenceHash(item.terminal_record_hash) || item.unselected_marker !== null
          : item.terminal_record_hash !== null || item.unselected_marker !== "unselected-neutral"
      )
      || !validEvidenceNumber(item.applied_sentiment_delta, -0.2, 0.2)
      || !validEvidenceNumber(item.applied_recall_delta, 0, 0.3)
    ) throw evidenceValidationError(kind);
    return;
  }
  if (
    item.event_type !== "spatial.dual-state-updated"
    || !hasExactKeys(item, boundedDualKeys)
    || item.response_input_sha256 !== summary.response_input_sha256
    || item.scenario_sha256 !== summary.scenario_sha256
    || item.scenario_sha256 !== state.opportunitySummary.scenario_sha256
    || item.city_sha256 !== summary.city_sha256
    || item.city_sha256 !== state.meta.city_sha256
    || !validEvidenceId(item.campaign_id)
    || !state.opportunitySummary.campaigns.some(
      (campaign) => campaign.campaign_id === item.campaign_id,
    )
    || !validEvidenceId(item.agent_id)
    || !knownEvidenceAgents().has(item.agent_id)
    || item.model_minute !== requestedMinute
    || item.day_index !== Math.floor(requestedMinute / 1440)
    || !Array.isArray(item.caused_by_event_ids)
    || item.caused_by_event_ids.length < 1
    || item.caused_by_event_ids.length > 520800
    || item.caused_by_event_ids.some((identifier) => !validEvidenceHash(identifier))
    || new Set(item.caused_by_event_ids).size !== item.caused_by_event_ids.length
  ) throw evidenceValidationError(kind);
  const tracks = [
    [
      validateResponseStateValue(item.previous_rule_state, kind),
      validateResponseStateValue(item.rule_state, kind),
    ],
    [
      validateCognitionStateValue(item.previous_cognition_state, kind),
      validateCognitionStateValue(item.cognition_state, kind),
    ],
  ];
  for (const [previous, committed] of tracks) {
    if (
      previous.agent_id !== item.agent_id
      || committed.agent_id !== item.agent_id
      || previous.campaign_id !== item.campaign_id
      || committed.campaign_id !== item.campaign_id
      || committed.response_count !== previous.response_count + item.caused_by_event_ids.length
      || committed.last_response_minute !== requestedMinute
      || (previous.last_response_minute !== null && previous.last_response_minute >= requestedMinute)
    ) throw evidenceValidationError(kind);
  }
}

// One or more applied responses precede exactly one dual update per canonical group; a page
// boundary may split a group, but never reorder or orphan it.
function validateBoundedPageGrammar(page) {
  let pending = [];
  let sawUpdate = false;
  for (const item of page.items) {
    if (item.event_type === "spatial.applied-response") {
      pending.push(item);
      continue;
    }
    const causes = item.caused_by_event_ids;
    const tail = causes.slice(causes.length - pending.length);
    if (
      pending.length > causes.length
      || (pending.length !== causes.length && (sawUpdate || page.offset === 0))
      || pending.some((applied, index) => (
        applied.event_id !== tail[index]
        || applied.baseline.agent_id !== item.agent_id
        || applied.baseline.campaign_id !== item.campaign_id
      ))
    ) throw evidenceValidationError("response");
    pending = [];
    sawUpdate = true;
  }
  if (pending.length > 0 && page.next_offset === null) throw evidenceValidationError("response");
}

function evidencePageContract(kind) {
  return kind === "response" && boundedCognitionRun()
    ? boundedResponsePageContract
    : evidencePageContracts[kind];
}

function validateEvidenceCountMap(kind, value, expectedKeys, expectedTotal, allowZero) {
  const counts = requireRecord(value, `${kind} counts`);
  const keys = Object.keys(counts);
  if (
    expectedKeys !== null
    && !sameStrings([...keys].sort(), [...expectedKeys].sort())
  ) throw evidenceValidationError(kind);
  if (expectedKeys === null) {
    const knownAgents = knownEvidenceAgents();
    if (keys.some((key) => !knownAgents.has(key))) throw evidenceValidationError(kind);
  }
  let total = 0;
  for (const key of keys) {
    const count = counts[key];
    if (
      !Number.isInteger(count)
      || count < (allowZero ? 0 : 1)
      || count > evidencePageContracts[kind].maximumTotal
    ) throw evidenceValidationError(kind);
    total += count;
  }
  if (total !== expectedTotal) throw evidenceValidationError(kind);
  return counts;
}

function validateEvidencePage(kind, value, requestedMinute, requestedOffset) {
  try {
    const contract = evidencePageContract(kind);
    const bounded = contract === boundedResponsePageContract;
    const page = requireRecord(value, evidenceLabel(kind));
    const total = requireBoundedInteger(page.total, 0, contract.maximumTotal, `${kind} total`);
    if (requestedOffset > total || (total > 0 && requestedOffset === total)) {
      throw evidenceValidationError(kind);
    }
    const expectedLength = total === 0 ? 0 : Math.min(EVIDENCE_PAGE_SIZE, total - requestedOffset);
    const expectedNext = requestedOffset + expectedLength < total
      ? requestedOffset + expectedLength
      : null;
    if (
      !hasExactKeys(page, contract.keys)
      || page.schema_version !== contract.schemaVersion
      || page.claim_scope !== contract.claimScope
      || page.minute !== requestedMinute
      || page.agent_id !== null
      || page.offset !== requestedOffset
      || page.limit !== EVIDENCE_PAGE_SIZE
      || !Array.isArray(page.items)
      || page.items.length !== expectedLength
      || page.next_offset !== expectedNext
      || (contract.modelKey !== null && page[contract.modelKey] !== contract.modelId)
    ) throw evidenceValidationError(kind);
    const eventTypeCounts = contract.eventTypes === null
      ? null
      : validateEvidenceCountMap(
        kind,
        page.event_type_counts,
        contract.eventTypes,
        total,
        true,
      );
    const expectedChannelTotal = contract.channelEventType !== null
      ? eventTypeCounts[contract.channelEventType]
      : total;
    const channelCounts = validateEvidenceCountMap(
      kind,
      page.channel_counts,
      evidenceChannels,
      expectedChannelTotal,
      true,
    );
    const agentCounts = validateEvidenceCountMap(
      kind,
      page.agent_counts,
      null,
      total,
      false,
    );
    const ids = new Set();
    const pageChannelCounts = { "roadside-billboard": 0, "mobile-feed": 0 };
    const pageEventTypeCounts = {};
    const pageAgentCounts = {};
    for (const itemValue of page.items) {
      if (kind === "opportunity") validateOpportunityRecord(itemValue, requestedMinute);
      else if (kind === "attention") validateAttentionRecord(itemValue, requestedMinute);
      else if (bounded) validateBoundedResponseRecord(itemValue, requestedMinute);
      else validateResponseRecord(itemValue, requestedMinute);
      const item = itemValue;
      const subject = evidenceSubject(item);
      const evidenceId = item[contract.idKey];
      if (!validEvidenceHash(evidenceId) || ids.has(evidenceId)) {
        throw evidenceValidationError(kind);
      }
      ids.add(evidenceId);
      pageAgentCounts[subject.agent_id] = (pageAgentCounts[subject.agent_id] || 0) + 1;
      if (item.event_type) {
        pageEventTypeCounts[item.event_type] = (pageEventTypeCounts[item.event_type] || 0) + 1;
      }
      if (subject.channel) pageChannelCounts[subject.channel] += 1;
    }
    if (bounded) validateBoundedPageGrammar(page);
    if (
      Object.entries(pageAgentCounts).some(([key, count]) => count > (agentCounts[key] || 0))
      || Object.entries(pageChannelCounts).some(([key, count]) => count > channelCounts[key])
      || (
        eventTypeCounts !== null
        && Object.entries(pageEventTypeCounts).some(
          ([key, count]) => count > eventTypeCounts[key],
        )
      )
    ) throw evidenceValidationError(kind);
    return page;
  } catch (error) {
    if (error instanceof Error && error.message === `${evidenceLabel(kind)} is invalid`) {
      throw error;
    }
    throw evidenceValidationError(kind);
  }
}

function renderEvidencePagination(kind, page) {
  const pagination = byId(`${kind}-pagination`);
  const previous = byId(`${kind}-page-previous`);
  const next = byId(`${kind}-page-next`);
  const status = byId(`${kind}-page-note`);
  pagination.hidden = page.total === 0;
  if (page.total === 0) {
    status.textContent = "";
    previous.disabled = true;
    next.disabled = true;
    return;
  }
  status.textContent = `Records ${page.offset + 1}-${page.offset + page.items.length} of ${page.total}`;
  previous.disabled = page.offset === 0;
  next.disabled = page.next_offset === null;
}

function renderEvidenceKind(kind) {
  if (kind === "opportunity") renderOpportunityEvidence();
  else if (kind === "attention") renderAttentionEvidence();
  else renderResponseEvidence();
}

function setEvidencePaginationBusy(kind, busy) {
  const page = state[evidencePageContracts[kind].pageKey];
  const previous = byId(`${kind}-page-previous`);
  const next = byId(`${kind}-page-next`);
  if (busy) {
    previous.disabled = true;
    next.disabled = true;
  } else if (page) {
    renderEvidencePagination(kind, page);
  }
}

function renderOpportunityEvidence() {
  if (!state.opportunitySummary || !state.opportunityPage) return;
  const page = state.opportunityPage;
  const list = byId("opportunity-list");
  const fragment = document.createDocumentFragment();
  const selectedCount = page.agent_counts[state.selected] || 0;
  for (const opportunity of page.items) {
    const campaign = spatialCampaignById(opportunity.campaign_id);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "opportunity-card";
    card.dataset.evidenceId = opportunity.opportunity_id;
    card.dataset.campaignId = opportunity.campaign_id;
    card.dataset.placementId = opportunity.placement_id;
    card.classList.toggle("selected", opportunity.agent_id === state.selected);
    card.classList.toggle(
      "causal-selected",
      state.selectedEvidence !== null && state.selectedEvidence.id === opportunity.opportunity_id,
    );
    const causalSelected = state.selectedEvidence !== null
      && state.selectedEvidence.id === opportunity.opportunity_id;
    card.classList.toggle("causal-related", isCausalEvidence(opportunity.opportunity_id));
    card.setAttribute("aria-pressed", String(causalSelected));
    const channel = document.createElement("span");
    channel.className = `opportunity-channel ${opportunity.channel === "mobile-feed" ? "phone" : "roadside"}`;
    channel.textContent = opportunity.channel === "mobile-feed" ? "PHONE" : "ROADSIDE BILLBOARD";
    const at = document.createElement("span");
    at.className = "opportunity-at";
    at.textContent = `${clockText(opportunity.model_minute)}:${String(Math.floor(opportunity.millisecond_within_minute / 1000)).padStart(2, "0")}`;
    const identity = document.createElement("strong");
    identity.textContent = `${opportunity.agent_id.toUpperCase()} · ${opportunity.placement_id.toUpperCase()}`;
    const detail = document.createElement("small");
    detail.textContent = `${campaign ? campaign.name : opportunity.campaign_id} · ordinal ${opportunity.ordinal_for_agent_placement_day}`;
    card.append(channel, at, identity, detail);
    card.addEventListener("click", () => selectCausalEvidence("opportunity", opportunity));
    fragment.append(card);
  }
  list.replaceChildren(fragment);
  byId("opportunity-current").textContent = `${page.total} AT THIS MINUTE · ${selectedCount} FOR SELECTED AGENT`;
  byId("opportunity-empty").hidden = page.total !== 0;
  renderEvidencePagination("opportunity", page);
}

function renderAttentionEvidence() {
  if (!state.attentionSummary || !state.attentionPage) return;
  const page = state.attentionPage;
  const list = byId("attention-list");
  const fragment = document.createDocumentFragment();
  const selectedCount = page.agent_counts[state.selected] || 0;
  for (const event of page.items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `attention-card ${event.event_type === "spatial.noticed" ? "notice" : "impression"}`;
    card.dataset.evidenceId = event.event_id;
    card.dataset.campaignId = event.campaign_id;
    card.dataset.placementId = event.placement_id;
    card.classList.toggle("selected", event.agent_id === state.selected);
    card.classList.toggle(
      "causal-selected",
      state.selectedEvidence !== null && state.selectedEvidence.id === event.event_id,
    );
    const causalSelected = state.selectedEvidence !== null
      && state.selectedEvidence.id === event.event_id;
    card.classList.toggle("causal-related", isCausalEvidence(event.event_id));
    card.setAttribute("aria-pressed", String(causalSelected));
    const stageLabel = document.createElement("span");
    stageLabel.className = "attention-stage";
    stageLabel.textContent = event.event_type === "spatial.noticed" ? "NOTICE" : "IMPRESSION";
    const at = document.createElement("span");
    at.className = "attention-at";
    at.textContent = `${clockText(event.model_minute)}:${String(Math.floor(event.millisecond_within_minute / 1000)).padStart(2, "0")}`;
    const identity = document.createElement("strong");
    identity.textContent = `${event.agent_id.toUpperCase()} \u00b7 ${event.placement_id.toUpperCase()}`;
    const decision = document.createElement("small");
    const comparison = event.notice_draw < event.notice_probability ? "<" : "\u2265";
    decision.textContent = `DRAW ${event.notice_draw.toFixed(4)} ${comparison} ${event.notice_probability.toFixed(4)} \u00b7 CAUSE ${event.caused_by.slice(0, 10).toUpperCase()}`;
    card.append(stageLabel, at, identity, decision);
    card.addEventListener("click", () => selectCausalEvidence("attention", event));
    fragment.append(card);
  }
  list.replaceChildren(fragment);
  byId("attention-current").textContent = `${page.total} EVENTS AT THIS MINUTE \u00b7 ${selectedCount} FOR SELECTED AGENT`;
  byId("attention-empty").hidden = page.total !== 0;
  renderEvidencePagination("attention", page);
}

function proxyText(value, label) {
  if (!Number.isFinite(value)) throw new Error(`${label} is not finite`);
  const rounded = Number(value.toFixed(4));
  return String(rounded === 0 && value !== 0 ? Number(value.toPrecision(4)) : rounded);
}

function signedProxyText(value, label) {
  const formatted = proxyText(value, label);
  return value >= 0 ? `+${formatted}` : formatted;
}

function invalidCognitionEvidence() {
  throw new Error("Saved cognition evidence is invalid");
}

function validateCognitionText(value, minimum, maximum) {
  if (
    typeof value !== "string"
    || value.trim().length === 0
    || Array.from(value).length < minimum
    || Array.from(value).length > maximum
    || /[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]/u.test(value)
  ) invalidCognitionEvidence();
  return value;
}

function canonicalJson(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(
      (key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`,
    ).join(",")}}`;
  }
  invalidCognitionEvidence();
}

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function validateCognitionSummary(value) {
  const documentValue = requireRecord(value, "Saved cognition summary");
  const input = state.workbenchInput;
  if (!input || input.run_schema_version !== 9) invalidCognitionEvidence();
  const cognition = requireRecord(input.cognition, "Saved cognition summary");
  if (
    !hasExactKeys(documentValue, cognitionSummaryDocumentKeys)
    || documentValue.schema_version !== 1
    || documentValue.run_id !== input.run_id
    || documentValue.run_id !== state.meta.run_id
    || documentValue.run_schema_version !== 9
    || !validEvidenceHash(documentValue.cognition_input_sha256)
    || !["rules", "mock", "local", "remote"].includes(documentValue.mode)
    || documentValue.mode !== cognition.mode
    || documentValue.aggregate_output_token_ceiling !== cognition.aggregate_output_token_ceiling
    || documentValue.provider_profile_id !== cognition.provider_profile_id
    || documentValue.provider_revision_sha256 !== cognition.provider_revision_sha256
  ) invalidCognitionEvidence();
  const offline = ["rules", "mock"].includes(documentValue.mode);
  const expectedExplainer = offline
    ? `city-${documentValue.mode}-explainer-v1`
    : "city-provider-explainer-v1";
  if (
    documentValue.explainer_id !== expectedExplainer
    || (
      offline
      && (
        documentValue.provider_profile_id !== null
        || documentValue.provider_revision_sha256 !== null
        || documentValue.model_id !== null
        || documentValue.aggregate_output_token_ceiling !== 0
      )
    )
    || (
      !offline
      && (
        !validEvidenceId(documentValue.provider_profile_id)
        || !validEvidenceHash(documentValue.provider_revision_sha256)
        || validateCognitionText(documentValue.model_id, 1, 120) !== documentValue.model_id
        || documentValue.aggregate_output_token_ceiling % 1536 !== 0
      )
    )
  ) invalidCognitionEvidence();

  const summary = requireRecord(documentValue.summary, "Saved cognition summary");
  const outcomes = requireRecord(summary.outcome_counts, "Saved cognition summary");
  const reasons = requireRecord(summary.reason_counts, "Saved cognition summary");
  if (
    !hasExactKeys(summary, cognitionSummaryKeys)
    || !hasExactKeys(outcomes, cognitionOutcomeKeys)
    || !hasExactKeys(reasons, cognitionReasonKeys)
    || summary.schema_version !== 1
    || outcomes.schema_version !== 1
    || reasons.schema_version !== 1
    || summary.disclosure !== cognitionDisclosure
    || !validEvidenceHash(summary.terminal_stream_sha256)
  ) invalidCognitionEvidence();
  const selected = requireBoundedInteger(
    summary.selected_request_count, 0, 60, "Saved cognition summary",
  );
  const outcomeTotal = cognitionOutcomeKeys.slice(1).reduce((total, key) => (
    total + requireBoundedInteger(outcomes[key], 0, 60, "Saved cognition summary")
  ), 0);
  const reasonTotal = cognitionReasonKeys.slice(1).reduce((total, key) => (
    total + requireBoundedInteger(reasons[key], 0, 60, "Saved cognition summary")
  ), 0);
  if (
    outcomeTotal !== selected
    || summary.terminal_record_count !== selected
    || reasons.budget_skipped !== outcomes.budget_skipped
    || reasonTotal - reasons.budget_skipped !== outcomes.fallback
    || !Number.isInteger(summary.attempt_count)
    || summary.attempt_count < outcomes.provider
    || summary.attempt_count > 4 * (outcomes.provider + outcomes.fallback)
  ) invalidCognitionEvidence();
  requireBoundedInteger(summary.prompt_tokens, 0, 128849018820, "Saved cognition summary");
  requireBoundedInteger(summary.completion_tokens, 0, 128849018820, "Saved cognition summary");
  requireBoundedInteger(summary.terminal_stream_bytes, 0, 1048576, "Saved cognition summary");
  if (
    offline
      ? cognitionOutcomeKeys.slice(1).some((key) => (
        outcomes[key] !== (key === documentValue.mode ? selected : 0)
      ))
      : outcomes.rules !== 0 || outcomes.mock !== 0
  ) invalidCognitionEvidence();
  return documentValue;
}

function validateCognitionResult(value, requestId) {
  const result = requireRecord(value, "Saved cognition result");
  if (
    !hasExactKeys(result, cognitionResultKeys)
    || result.schema_version !== 1
    || result.request_id !== requestId
    || result.disclosure !== cognitionDisclosure
  ) invalidCognitionEvidence();
  validateCognitionText(result.interpretation, 1, 480);
  if (
    !Array.isArray(result.decision_factors)
    || result.decision_factors.length < 1
    || result.decision_factors.length > 3
    || new Set(result.decision_factors).size !== result.decision_factors.length
  ) invalidCognitionEvidence();
  for (const factor of result.decision_factors) validateCognitionText(factor, 1, 120);
  if (
    !Array.isArray(result.safety_flags)
    || result.safety_flags.length > 3
    || new Set(result.safety_flags).size !== result.safety_flags.length
    || result.safety_flags.some((flag) => !cognitionSafetyFlags.includes(flag))
  ) invalidCognitionEvidence();
  return result;
}

async function validateCognitionRecords(value, summaryDocument) {
  const documentValue = requireRecord(value, "Saved cognition records");
  const summary = summaryDocument.summary;
  if (
    !hasExactKeys(documentValue, cognitionRecordDocumentKeys)
    || documentValue.schema_version !== 1
    || documentValue.run_id !== summaryDocument.run_id
    || documentValue.run_schema_version !== 9
    || documentValue.cognition_input_sha256 !== summaryDocument.cognition_input_sha256
    || !Array.isArray(documentValue.records)
    || documentValue.records.length !== summary.terminal_record_count
  ) invalidCognitionEvidence();
  const responseIds = new Set();
  const sourceCounts = Object.fromEntries(cognitionSources.map((source) => [source, 0]));
  const reasonCounts = Object.fromEntries(cognitionReasons.map((reason) => [reason, 0]));
  let attempts = 0;
  let promptTokens = 0;
  let completionTokens = 0;
  let terminalStream = "";
  for (let index = 0; index < documentValue.records.length; index += 1) {
    const record = requireRecord(documentValue.records[index], "Saved cognition record");
    if (
      !hasExactKeys(record, cognitionRecordKeys)
      || record.schema_version !== 1
      || record.selection_index !== index
      || typeof record.request_id !== "string"
      || !/^cog-[0-9a-f]{64}$/.test(record.request_id)
      || !validEvidenceHash(record.request_sha256)
      || record.run_input_sha256 !== state.workbenchInput.workbench_input_sha256
      || record.response_input_sha256 !== state.responseSummary.response_input_sha256
      || record.scenario_sha256 !== state.workbenchInput.scenario_sha256
      || record.city_sha256 !== state.workbenchInput.city_sha256
      || !validEvidenceHash(record.response_event_id)
      || responseIds.has(record.response_event_id)
      || !validEvidenceHash(record.notice_event_id)
      || !validEvidenceHash(record.opportunity_id)
      || !validEvidenceHash(record.state_update_event_id)
      || typeof record.agent_id !== "string"
      || !/^person-(?:00[1-9]|0[12][0-9]|030)$/.test(record.agent_id)
      || !validEvidenceId(record.campaign_id)
      || !validEvidenceId(record.placement_id)
      || !Number.isInteger(record.model_minute)
      || record.model_minute < 0
      || record.model_minute >= 10080
      || !Number.isInteger(record.millisecond_within_minute)
      || record.millisecond_within_minute < 0
      || record.millisecond_within_minute >= 60000
      || !Number.isInteger(record.state_update_cause_count)
      || record.state_update_cause_count < 1
      || record.state_update_cause_count > 520800
      || !cognitionSources.includes(record.source)
      || record.disclosure !== cognitionDisclosure
      || !validEvidenceHash(record.prompt_contract_sha256)
      || !validEvidenceHash(record.result_schema_contract_sha256)
      || !validEvidenceHash(record.selection_policy_sha256)
      || !validEvidenceHash(record.fallback_policy_sha256)
      || !validEvidenceHash(record.record_hash)
    ) invalidCognitionEvidence();
    validateCognitionResult(record.result, record.request_id);
    const providerValues = [
      record.provider_profile_id,
      record.provider_revision_sha256,
      record.provider_profile_snapshot_sha256,
      record.provider_protocol,
      record.provider_effective_settings_sha256,
    ];
    const hasProvider = providerValues.every((item) => item !== null);
    if (providerValues.some((item) => item !== null) !== hasProvider) invalidCognitionEvidence();
    if (
      hasProvider
      && (
        record.provider_profile_id !== summaryDocument.provider_profile_id
        || record.provider_revision_sha256 !== summaryDocument.provider_revision_sha256
        || !validEvidenceHash(record.provider_profile_snapshot_sha256)
        || record.provider_protocol !== "openai-chat-completions-json-schema-v1"
        || !validEvidenceHash(record.provider_effective_settings_sha256)
      )
    ) invalidCognitionEvidence();
    const hasUsage = record.prompt_tokens !== null && record.completion_tokens !== null;
    if ((record.prompt_tokens !== null || record.completion_tokens !== null) !== hasUsage) {
      invalidCognitionEvidence();
    }
    if (hasUsage) {
      requireBoundedInteger(record.prompt_tokens, 0, 2147483647, "Saved cognition record");
      requireBoundedInteger(record.completion_tokens, 0, 2147483647, "Saved cognition record");
    }
    const validSource = (
      ["rules", "mock"].includes(record.source)
        ? record.source === summaryDocument.mode
          && record.attempt_count === 0 && record.reason === null && !hasProvider && !hasUsage
        : record.source === "provider"
          ? record.attempt_count >= 1 && record.attempt_count <= 4
            && record.reason === null && hasProvider
          : record.source === "cache"
            ? record.attempt_count === 0 && record.reason === null && hasProvider
            : record.source === "fallback"
              ? record.attempt_count >= 0 && record.attempt_count <= 4
                && cognitionReasons.includes(record.reason)
                && record.reason !== "budget-skipped" && hasProvider
              : record.source === "budget-skipped"
                && record.attempt_count === 0 && record.reason === "budget-skipped"
                && hasProvider && !hasUsage
    );
    if (!validSource) invalidCognitionEvidence();
    const recordFacts = Object.fromEntries(
      Object.entries(record).filter(([key]) => key !== "record_hash"),
    );
    if (await sha256Text(canonicalJson(recordFacts)) !== record.record_hash) {
      invalidCognitionEvidence();
    }
    responseIds.add(record.response_event_id);
    sourceCounts[record.source] += 1;
    if (record.reason !== null) reasonCounts[record.reason] += 1;
    attempts += record.attempt_count;
    promptTokens += record.prompt_tokens || 0;
    completionTokens += record.completion_tokens || 0;
    terminalStream += `${canonicalJson(record)}\n`;
  }
  for (const source of cognitionSources) {
    if (sourceCounts[source] !== summary.outcome_counts[source.replaceAll("-", "_")]) {
      invalidCognitionEvidence();
    }
  }
  for (const reason of cognitionReasons) {
    if (reasonCounts[reason] !== summary.reason_counts[reason.replaceAll("-", "_")]) {
      invalidCognitionEvidence();
    }
  }
  if (
    attempts !== summary.attempt_count
    || promptTokens !== summary.prompt_tokens
    || completionTokens !== summary.completion_tokens
    || new TextEncoder().encode(terminalStream).length !== summary.terminal_stream_bytes
    || await sha256Text(terminalStream) !== summary.terminal_stream_sha256
  ) invalidCognitionEvidence();
  return documentValue;
}

async function loadCognitionEvidence() {
  const [summaryValue, recordsValue] = await Promise.all([
    fetchJson("/cognition-summary"),
    fetchJson("/cognition-records"),
  ]);
  const summary = validateCognitionSummary(summaryValue);
  const records = await validateCognitionRecords(recordsValue, summary);
  state.cognitionSummary = summary;
  state.cognitionRecords = records;
  state.cognitionByResponseId = new Map(
    records.records.map((record) => [record.response_event_id, record]),
  );
}

function cognitionSourceLabel(source) {
  return {
    provider: "AI-generated explanation",
    cache: "AI-generated explanation",
    rules: "Deterministic explanation",
    mock: "Mock explanation",
    fallback: "Deterministic fallback",
    "budget-skipped": "Explanation skipped",
  }[source];
}

function readableCognitionToken(value) {
  return value.replaceAll("-", " ").replace(/(^| )\w/g, (letter) => letter.toUpperCase());
}

function appendExplanationFact(list, label, value, className = "") {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  const isolated = document.createElement("bdi");
  term.textContent = label;
  isolated.dir = "auto";
  isolated.textContent = value;
  detail.append(isolated);
  if (className) detail.className = className;
  row.append(term, detail);
  list.append(row);
}

function createResponseExplanation(record, response) {
  if (
    record.agent_id !== response.agent_id
    || record.campaign_id !== response.campaign_id
    || record.placement_id !== response.placement_id
    || record.model_minute !== response.model_minute
    || record.millisecond_within_minute !== response.millisecond_within_minute
  ) invalidCognitionEvidence();
  const details = document.createElement("details");
  details.className = `response-explanation source-${record.source}`;
  details.dataset.responseEventId = record.response_event_id;
  const summary = document.createElement("summary");
  const label = document.createElement("strong");
  const status = document.createElement("span");
  label.textContent = cognitionSourceLabel(record.source);
  status.textContent = record.reason === null ? "Saved" : readableCognitionToken(record.reason);
  summary.append(label, status);
  const body = document.createElement("div");
  body.className = "response-explanation-body";
  const copy = document.createElement("bdi");
  copy.className = "explanation-copy";
  copy.dir = "auto";
  copy.textContent = record.result.interpretation;
  const facts = document.createElement("dl");
  facts.className = "explanation-facts";
  const provider = state.cognitionSummary.provider_profile_id === null
    ? (record.source === "mock" ? "Deterministic mock" : "Offline rules")
    : state.cognitionSummary.provider_profile_id;
  const model = state.cognitionSummary.model_id === null
    ? "Not used"
    : state.cognitionSummary.model_id;
  appendExplanationFact(facts, "Source", record.source, "explanation-source");
  appendExplanationFact(facts, "Provider", provider, "explanation-provider");
  appendExplanationFact(facts, "Model", model, "explanation-model");
  appendExplanationFact(
    facts,
    "Fallback",
    record.reason === null ? "None" : readableCognitionToken(record.reason),
    "explanation-fallback",
  );
  const factorsTitle = document.createElement("h4");
  factorsTitle.textContent = "Decision factors";
  const factors = document.createElement("ul");
  factors.className = "explanation-factors";
  for (const value of displayedDecisionFactors(record.result.decision_factors)) {
    const item = document.createElement("li");
    const isolated = document.createElement("bdi");
    isolated.className = "explanation-factor";
    isolated.dir = "auto";
    isolated.textContent = value;
    item.append(isolated);
    factors.append(item);
  }
  const safety = document.createElement("p");
  safety.className = "explanation-safety";
  const flags = record.result.safety_flags;
  safety.textContent = flags.length === 0
    ? "Safety flags: None recorded"
    : `Model's own caution flags: ${flags.map((flag) => `${readableCognitionToken(flag)} (${explanationFlagMeaning(flag)})`).join(" · ")}`;
  const disclosure = document.createElement("p");
  disclosure.className = "explanation-disclosure";
  disclosure.textContent = "Synthetic narrative, not observed behavior. The deterministic response above was already committed.";
  body.append(copy, facts, factorsTitle, factors, safety);
  if (flags.length !== 0) {
    body.append(createTextElement(
      "p",
      "explanation-safety-note",
      "These flags are the model's self-assessment of its own explanation text, not a finding about this synthetic person. In explanation mode they change nothing in the saved results.",
    ));
  }
  body.append(disclosure);
  details.append(summary, body);
  return details;
}

// Plain-language meaning of each closed V1 flag, as the model applies it to its own wording.
function explanationFlagMeaning(flag) {
  return {
    "stereotype-risk": "its wording may lean on a stereotype",
    "unsupported-inference": "it may say more than the supplied facts",
    "unsafe-content": "it judged the content possibly unsafe",
  }[flag] || "self-reported";
}

// Words that continue a sentence and cannot open a decision factor of their own.
const continuationStart = /^(?:to|and|or|of|for|with|in|on|at|by|from|that|which|who|whose|but|as|than|because|while|so|into|onto|over|about|without)\b/;

// A model that runs into the 120-character cap sometimes splits one sentence across two
// factors; show the fragments as the one sentence they are. The saved record is unchanged.
function displayedDecisionFactors(values) {
  const joined = [];
  for (const value of values) {
    const previous = joined.length === 0 ? null : joined[joined.length - 1];
    const continues = previous !== null
      && !/[.!?;:)\]»”"]\s*$/.test(previous)
      && (continuationStart.test(value) || (previous.length >= 100 && /^[a-z]/.test(value)));
    if (continues) joined[joined.length - 1] = `${previous} ${value}`;
    else joined.push(value);
  }
  return joined;
}

function cognitionSummaryPresentation(documentValue) {
  const outcomes = documentValue.summary.outcome_counts;
  const total = documentValue.summary.terminal_record_count;
  const aiGenerated = outcomes.provider + outcomes.cache;
  if (total === 0) {
    return ["No saved explanations", "Deterministic simulation · no saved explanations"];
  }
  if (aiGenerated === total) {
    return ["AI-generated explanations", "Deterministic simulation · saved AI explanations"];
  }
  if (aiGenerated > 0) {
    return [
      "Mixed explanation evidence",
      "Deterministic simulation · saved mixed explanation evidence",
    ];
  }
  if (outcomes.rules === total) {
    return [
      "Deterministic explanations",
      "Deterministic simulation · saved local explanations",
    ];
  }
  if (outcomes.mock === total) {
    return ["Mock explanations", "Deterministic simulation · saved mock explanations"];
  }
  return ["Saved explanation evidence", "Deterministic simulation · saved explanation evidence"];
}

function renderCognitionSummary() {
  if (!state.cognitionSummary || !state.cognitionRecords) return;
  const documentValue = state.cognitionSummary;
  const summary = documentValue.summary;
  const offline = ["rules", "mock"].includes(documentValue.mode);
  const [label, boundary] = cognitionSummaryPresentation(documentValue);
  byId("cognition-summary-label").textContent = label;
  byId("cognition-summary-status").textContent = `${summary.terminal_record_count} saved`;
  byId("cognition-summary-mode").textContent = documentValue.mode;
  byId("cognition-summary-outcomes").textContent = cognitionSources
    .filter((source) => summary.outcome_counts[source.replaceAll("-", "_")] > 0)
    .map((source) => (
      `${cognitionSourceLabel(source)} ${summary.outcome_counts[source.replaceAll("-", "_")]}`
    ))
    .join(" · ") || "No selected responses";
  byId("cognition-summary-provider").textContent = offline
    ? (documentValue.mode === "mock" ? "Deterministic mock" : "Offline rules")
    : documentValue.provider_profile_id;
  byId("cognition-summary-model").textContent = documentValue.model_id || "Not used";
  byId("cognition-summary-fallbacks").textContent = (
    `${summary.outcome_counts.fallback} / ${summary.outcome_counts.budget_skipped}`
  );
  byId("cognition-summary-budget").textContent = offline
    ? "No provider tokens"
    : `${documentValue.aggregate_output_token_ceiling} reserved tokens`;
  byId("cognition-summary").open = false;
  byId("cognition-summary").hidden = false;
  byId("model-boundary-label").textContent = boundary;
}

function renderResponseEvidence() {
  if (!state.responseSummary || !state.responsePage) return;
  if (boundedCognitionRun()) {
    renderBoundedResponseEvidence();
    return;
  }
  const page = state.responsePage;
  const list = byId("response-list");
  const fragment = document.createDocumentFragment();
  const selectedCount = page.agent_counts[state.selected] || 0;
  for (const record of page.items) {
    const item = document.createElement("div");
    item.className = "response-evidence-item";
    const card = document.createElement("button");
    card.type = "button";
    card.className = "response-card";
    card.dataset.evidenceId = record.event_id;
    card.dataset.campaignId = record.campaign_id;
    if (typeof record.placement_id === "string") card.dataset.placementId = record.placement_id;
    card.classList.toggle("selected", record.agent_id === state.selected);
    card.classList.toggle(
      "causal-selected",
      state.selectedEvidence !== null && state.selectedEvidence.id === record.event_id,
    );
    const causalSelected = state.selectedEvidence !== null
      && state.selectedEvidence.id === record.event_id;
    card.classList.toggle("causal-related", isCausalEvidence(record.event_id));
    card.setAttribute("aria-pressed", String(causalSelected));
    const stageLabel = document.createElement("span");
    stageLabel.className = "response-stage";
    const at = document.createElement("span");
    at.className = "response-at";
    at.textContent = clockText(record.model_minute);
    const identity = document.createElement("strong");
    identity.textContent = `${record.agent_id.toUpperCase()} \u00b7 ${record.campaign_id.toUpperCase()}`;
    const detail = document.createElement("small");
    if (record.event_type === "spatial.response") {
      card.classList.add("rule-response");
      stageLabel.textContent = "RULE RESPONSE";
      at.textContent += `:${String(Math.floor(record.millisecond_within_minute / 1000)).padStart(2, "0")}`;
      detail.textContent = `SENTIMENT \u0394 ${signedProxyText(record.sentiment_delta, "Sentiment delta")} \u00b7 RECALL \u0394 ${signedProxyText(record.recall_delta, "Recall delta")} \u00b7 VALUE MATCH ${proxyText(record.value_match, "Value match")} \u00b7 CAUSE ${record.caused_by.slice(0, 10).toUpperCase()}`;
    } else if (record.event_type === "spatial.state-updated") {
      card.classList.add("state-update");
      stageLabel.textContent = "STATE UPDATE";
      detail.textContent = `SENTIMENT ${proxyText(record.previous_state.brand_sentiment, "Previous sentiment")} \u2192 ${proxyText(record.state.brand_sentiment, "Updated sentiment")} \u00b7 RECALL ${proxyText(record.previous_state.recall_strength, "Previous recall")} \u2192 ${proxyText(record.state.recall_strength, "Updated recall")} \u00b7 INTENTION PROXY ${proxyText(record.previous_state.purchase_intention, "Previous intention proxy")} \u2192 ${proxyText(record.state.purchase_intention, "Updated intention proxy")} \u00b7 ${record.caused_by_event_ids.length} CAUSES`;
    } else {
      throw new Error("Unknown spatial response record type");
    }
    card.append(stageLabel, at, identity, detail);
    card.addEventListener("click", () => selectCausalEvidence("response", record));
    item.append(card);
    if (record.event_type === "spatial.response") {
      const cognition = state.cognitionByResponseId.get(record.event_id);
      if (cognition) {
        item.dataset.responseEventId = record.event_id;
        item.append(createResponseExplanation(cognition, record));
      }
    }
    fragment.append(item);
  }
  list.replaceChildren(fragment);
  byId("response-current").textContent = `${page.total} RECORDS AT THIS MINUTE \u00b7 ${selectedCount} FOR SELECTED AGENT`;
  byId("response-empty").hidden = page.total !== 0;
  renderEvidencePagination("response", page);
}

function renderResponseState() {
  if (!state.responseStatePage) return;
  const page = state.responseStatePage;
  if (page.state_scope !== "final-end-of-run-not-scrubbed-minute") {
    throw new Error("Spatial response state scope is invalid");
  }
  if (boundedCognitionRun()) {
    renderBoundedResponseState();
    return;
  }
  const list = byId("response-state-list");
  while (list.firstChild) list.removeChild(list.firstChild);
  byId("response-state-current").textContent = `${page.total} FINAL CAMPAIGN STATES FOR SELECTED AGENT`;
  byId("response-state-empty").hidden = page.total !== 0;
  const pageNote = byId("response-state-page-note");
  pageNote.hidden = page.next_offset === null;
  pageNote.textContent = `Showing the first ${page.items.length} of ${page.total} final campaign states.`;
  for (const item of page.items) {
    const card = document.createElement("article");
    card.className = "response-state-card";
    const identity = document.createElement("strong");
    identity.textContent = `${item.agent_id.toUpperCase()} \u00b7 ${item.campaign_id.toUpperCase()}`;
    const count = document.createElement("span");
    count.textContent = `${item.response_count} RESPONSES`;
    const values = document.createElement("small");
    values.textContent = `SENTIMENT ${proxyText(item.brand_sentiment, "Final sentiment")} \u00b7 RECALL ${proxyText(item.recall_strength, "Final recall")} \u00b7 INTENTION PROXY ${proxyText(item.purchase_intention, "Final intention proxy")}`;
    const timing = document.createElement("small");
    timing.textContent = item.last_response_minute === null
      ? "NO RESPONSE COMMITTED"
      : `LAST RESPONSE ${dayText(item.last_response_minute)} \u00b7 ${clockText(item.last_response_minute)}`;
    card.append(identity, count, values, timing);
    list.append(card);
  }
}

// ---- Manifest-V10 bounded cognition inspector (inert for every other run version) ----

function createTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function createIsolatedText(text, className = "") {
  const isolated = createTextElement("bdi", className, text);
  isolated.dir = "auto";
  return isolated;
}

const boundedSourceTitles = {
  provider: "Provider",
  cache: "Cache",
  mock: "Mock",
  rules: "Rules",
  fallback: "Fallback",
  "budget-skipped": "Budget skipped",
  unselected: "Not selected",
};

function boundedModeLabel(mode) {
  return {
    rules: "Offline rules · neutral identity",
    mock: "Offline deterministic mock",
    local: "Local compatible provider",
    remote: "Remote compatible provider",
  }[mode];
}

function boundedCognitionPresentation(documentValue) {
  const outcomes = documentValue.summary.outcomes;
  if (documentValue.mode === "rules") {
    return [
      "Bounded cognition · rules identity",
      "Rule-owned simulation · neutral bounded cognition identity",
    ];
  }
  if (documentValue.mode === "mock") {
    return [
      "Bounded cognition · mock signals",
      "Rule-owned simulation · deterministic mock cognition signals",
    ];
  }
  if (outcomes.provider + outcomes.cache > 0) {
    return ["Bounded AI cognition signals", "Rule-owned simulation · bounded AI cognition signals"];
  }
  return [
    "Bounded AI cognition · neutral outcomes",
    "Rule-owned simulation · bounded cognition with neutral outcomes",
  ];
}

function boundedBoundaryText(mode) {
  const limits = "They cannot move agents, create or select events, change purchase intention, or make purchases.";
  if (mode === "rules") {
    return `Rules mode applies the exact neutral vector, so the cognition state follows the rule baseline. ${limits}`;
  }
  const selector = mode === "mock"
    ? "An offline deterministic mock selects"
    : "External AI selects";
  return `${selector} a small set of synthetic appraisal categories. AdLife applies a fixed mapping. These categories may change only the displayed cognition state. ${limits}`;
}

function boundedSignalText(record) {
  const vector = [
    record.result.affect, record.result.relevance, record.result.credibility,
    record.result.salience,
  ].join(" · ");
  if (record.source === "fallback") return `neutral vector · ${record.reason.replaceAll("-", " ")}`;
  if (record.source === "budget-skipped") return "neutral vector · budget skipped";
  if (record.source === "rules") return "neutral vector · rules identity";
  if (record.result.safety_flags.length !== 0) {
    return `${vector} · safety ${record.result.safety_flags.join(", ")} → neutralized`;
  }
  if (vector.split(" · ").includes("uncertain")) return `${vector} → neutralized (uncertain)`;
  return vector;
}

function boundedSignalMatches(record, filter) {
  if (filter === "all") return true;
  if (filter === "neutralized") {
    return record.result.safety_flags.length !== 0
      || [
        record.result.affect, record.result.relevance, record.result.credibility,
        record.result.salience,
      ].includes("uncertain");
  }
  return record.source === filter;
}

const boundedSignalFilters = [
  ["all", "All selected requests"],
  ["provider", "Provider signals"],
  ["cache", "Cached signals"],
  ["mock", "Mock signals"],
  ["rules", "Rules identity"],
  ["fallback", "Fallback"],
  ["budget-skipped", "Budget skipped"],
  ["neutralized", "Uncertain or safety neutralized"],
];

function appendBoundedFact(list, key, label, value, className = "") {
  const row = document.createElement("div");
  row.dataset.fact = key;
  const term = createTextElement("dt", "", label);
  const detail = createTextElement("dd", className);
  detail.append(createIsolatedText(value));
  row.append(term, detail);
  list.append(row);
}

function ensureBoundedCognitionPanel() {
  const existing = byId("bounded-cognition-panel");
  if (existing) return existing;
  const panel = document.createElement("section");
  panel.className = "bounded-cognition-panel";
  panel.id = "bounded-cognition-panel";
  panel.tabIndex = -1;
  panel.setAttribute("aria-labelledby", "bounded-cognition-title");
  panel.setAttribute("aria-describedby", "bounded-cognition-boundary bounded-cognition-replay");

  const heading = createTextElement("div", "bounded-cognition-heading");
  const titles = document.createElement("div");
  titles.append(
    createTextElement("span", "eyebrow", "Saved cognition evidence · manifest V10"),
    Object.assign(createTextElement("h3", "", "Bounded cognition"), { id: "bounded-cognition-title" }),
  );
  const mode = createTextElement("span", "bounded-mode-badge");
  mode.id = "bounded-cognition-mode";
  heading.append(titles, mode);
  const model = createTextElement("p", "bounded-model");
  model.id = "bounded-cognition-model";

  const boundary = createTextElement("p", "bounded-boundary");
  boundary.id = "bounded-cognition-boundary";

  const pipeline = document.createElement("ol");
  pipeline.className = "bounded-pipeline";
  pipeline.id = "bounded-cognition-pipeline";
  pipeline.setAttribute("aria-label", "Authority order for every eligible response");
  for (const [title, owner] of [
    ["Rule baseline", "deterministic"],
    ["Signal or neutral", "closed categories"],
    ["Fixed mapping", "influence-v1"],
    ["Applied delta", "cognition only"],
    ["Dual-state commit", "one atomic update"],
  ]) {
    const step = document.createElement("li");
    step.append(createTextElement("strong", "", title), createTextElement("small", "", owner));
    pipeline.append(step);
  }

  const authority = document.createElement("ul");
  authority.className = "bounded-authority";
  authority.id = "bounded-cognition-authority";
  authority.setAttribute("aria-label", "Evidence ownership");

  const coverage = document.createElement("div");
  coverage.className = "bounded-coverage";
  coverage.id = "bounded-cognition-coverage";
  coverage.setAttribute("role", "group");
  coverage.setAttribute("aria-label", "Selected, funded, and neutral cognition coverage");
  const outcomes = document.createElement("section");
  outcomes.className = "bounded-outcomes";
  outcomes.id = "bounded-cognition-outcomes";
  outcomes.setAttribute("aria-labelledby", "bounded-cognition-outcomes-title");
  const outcomesTitle = createTextElement("h4", "bounded-outcomes-title");
  outcomesTitle.id = "bounded-cognition-outcomes-title";
  const outcomeList = document.createElement("dl");
  outcomeList.className = "bounded-outcome-list";
  outcomeList.id = "bounded-cognition-outcome-list";
  outcomes.append(outcomesTitle, outcomeList);
  const neutralization = createTextElement("p", "bounded-neutralization");
  neutralization.id = "bounded-cognition-neutralization";
  const reasons = createTextElement("p", "bounded-reasons");
  reasons.id = "bounded-cognition-reasons";

  const provenance = document.createElement("dl");
  provenance.className = "bounded-provenance";
  provenance.id = "bounded-cognition-provenance";

  const replay = createTextElement("p", "bounded-replay");
  replay.id = "bounded-cognition-replay";
  const disclosure = createTextElement("p", "bounded-disclosure");
  disclosure.id = "bounded-cognition-disclosure";

  const index = document.createElement("details");
  index.className = "bounded-signal-index";
  index.id = "bounded-signal-index";
  const indexSummary = document.createElement("summary");
  const indexCount = createTextElement("span", "bounded-signal-count");
  indexCount.id = "bounded-signal-count";
  indexSummary.append(createTextElement("strong", "", "Saved signal timeline"), indexCount);
  const filterLabel = createTextElement("label", "bounded-signal-filter-label", "Show");
  filterLabel.htmlFor = "bounded-signal-filter";
  const filter = document.createElement("select");
  filter.id = "bounded-signal-filter";
  filterLabel.append(filter);
  const list = document.createElement("ol");
  list.className = "bounded-signal-list";
  list.id = "bounded-signal-list";
  list.setAttribute("aria-label", "Saved bounded cognition requests in selection order");
  const empty = createTextElement("p", "bounded-signal-empty", "No saved request matches this filter.");
  empty.id = "bounded-signal-empty";
  index.append(indexSummary, filterLabel, list, empty);
  filter.addEventListener("change", () => {
    const value = filter.value;
    if (!boundedSignalFilters.some(([key]) => key === value)) return;
    state.boundedSignalFilter = value;
    renderBoundedSignalIndex();
  });

  panel.append(
    heading, model, boundary, coverage, outcomes, neutralization, reasons, index, pipeline,
    authority, provenance, replay, disclosure,
  );
  byId("response-claim").after(panel);
  return panel;
}

function renderBoundedModelLine(documentValue) {
  const line = byId("bounded-cognition-model");
  line.dataset.mode = documentValue.mode;
  if (documentValue.mode === "mock") {
    line.replaceChildren(createTextElement("span", "", "No model · offline deterministic mock signals"));
    return;
  }
  if (documentValue.mode === "rules") {
    line.replaceChildren(createTextElement("span", "", "No model · rules identity, no signal requests"));
    return;
  }
  line.replaceChildren(
    createTextElement("span", "bounded-model-label", "Model"),
    createIsolatedText(documentValue.model_id, "bounded-model-id"),
    createTextElement(
      "span",
      "bounded-model-profile",
      `profile ${documentValue.provider_profile_id} · revision ${documentValue.provider_revision_sha256.slice(0, 12)}…`,
    ),
  );
}

// Every eligible response resolves to exactly one applied status; the rows partition them.
const boundedOutcomeRows = [
  ["signal", "Signal applied", "Model or mock categories passed through the fixed mapping."],
  ["safety", "Set aside · safety flag", "The model flagged its own answer, so the neutral vector applied."],
  ["uncertain", "Set aside · uncertain answer", "A category was “uncertain” (no safety flag), so the neutral vector applied."],
  ["fallback", "Neutral · fallback", "The provider call failed under the frozen fallback policy."],
  ["budget-skipped", "Neutral · budget skipped", "Selected beyond the funded limit; no provider call."],
  ["rules", "Neutral · rules identity", "Rules mode requests no signal."],
  ["unselected", "Neutral · not selected", "Beyond 4 per agent or 60 per run; no request."],
];

function renderBoundedOutcomes(documentValue, records) {
  const coverage = documentValue.summary.coverage;
  const counts = Object.fromEntries(boundedOutcomeRows.map(([key]) => [key, 0]));
  counts.unselected = coverage.unselected_response_count;
  for (const record of records) {
    const vector = [
      record.result.affect, record.result.relevance, record.result.credibility,
      record.result.salience,
    ];
    if (["rules", "fallback", "budget-skipped"].includes(record.source)) counts[record.source] += 1;
    else if (record.result.safety_flags.length !== 0) counts.safety += 1;
    else if (vector.includes("uncertain")) counts.uncertain += 1;
    else counts.signal += 1;
  }
  const eligible = coverage.eligible_response_count;
  byId("bounded-cognition-outcomes-title").textContent = eligible === 0
    ? "No ad was noticed, so no response needed a signal"
    : `How the ${eligible} eligible ${eligible === 1 ? "response was" : "responses were"} resolved`;
  const list = byId("bounded-cognition-outcome-list");
  list.replaceChildren(...boundedOutcomeRows
    .filter(([key]) => counts[key] > 0 || (
      key === "signal" && documentValue.mode !== "rules" && eligible > 0
    ))
    .map(([key, label, explanation]) => {
      const row = document.createElement("div");
      row.dataset.outcome = key;
      const term = createTextElement("dt", "");
      term.append(createTextElement("strong", "", label), createTextElement("small", "", explanation));
      row.append(term, createTextElement("dd", "", String(counts[key])));
      return row;
    }));
}

function renderBoundedCognitionSummary() {
  if (!state.boundedCognition) return;
  const documentValue = state.boundedCognition.summary;
  const summary = documentValue.summary;
  const offline = ["rules", "mock"].includes(documentValue.mode);
  ensureBoundedCognitionPanel();
  const [title, boundaryLabel] = boundedCognitionPresentation(documentValue);
  byId("bounded-cognition-title").textContent = title;
  byId("bounded-cognition-mode").textContent = boundedModeLabel(documentValue.mode);
  byId("bounded-cognition-mode").dataset.mode = documentValue.mode;
  renderBoundedModelLine(documentValue);
  byId("bounded-cognition-boundary").textContent = boundedBoundaryText(documentValue.mode);

  const authority = byId("bounded-cognition-authority");
  const ownership = [
    ["Movement, exposure, and attention", "rules"],
    ["Purchase intention", "rule-owned uncalibrated proxy · purchases not modeled"],
    [
      "Cognition signals",
      offline
        ? (documentValue.mode === "mock" ? "deterministic offline mock" : "neutral rules identity")
        : "selected provider with deterministic fallback",
    ],
    ["Fresh live signals", "not guaranteed reproducible"],
    ["Saved replay", "frozen and offline"],
  ];
  authority.replaceChildren(...ownership.map(([subject, owner]) => {
    const item = document.createElement("li");
    item.append(createTextElement("span", "", subject), createTextElement("strong", "", owner));
    return item;
  }));

  const coverage = summary.coverage;
  const outcomes = summary.outcomes;
  const offlineTile = documentValue.mode === "mock"
    ? ["mock", boundedSourceTitles.mock, outcomes.mock, "offline mock answers"]
    : ["rules", boundedSourceTitles.rules, outcomes.rules, "neutral identity, no request"];
  const tiles = [
    ["eligible", "Eligible", coverage.eligible_response_count, "noticed ad responses"],
    ["selected", "Selected", coverage.selected_request_count, "chosen for a signal"],
    ["funded", "Funded", coverage.funded_request_count, "within the funded limit"],
    ["unselected", "Unselected", coverage.unselected_response_count, "neutral, no request"],
    ...(offline ? [offlineTile] : []),
    ["provider", "Provider", outcomes.provider, "answered by the model"],
    ["cache", "Cache", outcomes.cache, "verified earlier answers"],
    ["fallback", "Fallback", outcomes.fallback, "provider failed, neutral"],
    ["budget-skipped", "Budget skipped", outcomes.budget_skipped, "over the funded limit"],
  ];
  byId("bounded-cognition-coverage").dataset.offline = String(offline);
  byId("bounded-cognition-coverage").replaceChildren(...tiles.map(([key, label, count, caption]) => {
    const tile = document.createElement("div");
    tile.className = "bounded-tile";
    tile.dataset.count = key;
    tile.append(
      createTextElement("span", "", label),
      createTextElement("strong", "", String(count)),
      createTextElement("small", "", caption),
    );
    return tile;
  }));
  renderBoundedOutcomes(documentValue, state.boundedCognition.records.records);
  const neutral = summary.neutralization;
  byId("bounded-cognition-neutralization").textContent = `Neutral vector applied to ${neutral.neutralized_response_count} of ${coverage.eligible_response_count} eligible responses · uncertain results ${neutral.uncertain_result_count} · safety-neutralized results ${neutral.safety_neutralized_result_count} (these two diagnostic counts may overlap).`;
  const reasonText = cognitionReasons
    .filter((reason) => reason !== "budget-skipped")
    .filter((reason) => summary.reasons[reason.replaceAll("-", "_")] > 0)
    .map((reason) => `${reason.replaceAll("-", " ")} ${summary.reasons[reason.replaceAll("-", "_")]}`)
    .join(" · ");
  byId("bounded-cognition-reasons").textContent = `Fallback reasons: ${reasonText || "none recorded"}.`;

  const provenance = byId("bounded-cognition-provenance");
  provenance.replaceChildren();
  appendBoundedFact(provenance, "mode", "Source mode", `${boundedModeLabel(documentValue.mode)} (${documentValue.mode})`);
  appendBoundedFact(
    provenance, "profile", "Provider profile",
    offline ? "Not used · offline" : documentValue.provider_profile_id,
  );
  appendBoundedFact(provenance, "model", "Model", documentValue.model_id || "Not used");
  appendBoundedFact(
    provenance, "revision", "Profile revision",
    documentValue.provider_revision_sha256 || "Not used", "bounded-hash",
  );
  const frozenCognition = state.workbenchInput.cognition;
  appendBoundedFact(
    provenance, "funded-limit", "Funded request limit",
    offline
      ? `${documentValue.funded_response_limit} · resolved locally, no provider tokens`
      : `${documentValue.funded_response_limit} · ${frozenCognition.aggregate_output_token_ceiling} reserved output tokens (${frozenCognition.reservation_tokens_per_request} per request, at most ${frozenCognition.max_output_tokens_per_attempt} per attempt)`,
  );
  appendBoundedFact(
    provenance, "prompt", "Prompt contract",
    `${documentValue.prompt_contract_id} · ${documentValue.prompt_contract_sha256}`, "bounded-hash",
  );
  appendBoundedFact(
    provenance, "result-schema", "Result schema",
    `${documentValue.result_schema_id} · ${documentValue.result_schema_contract_sha256}`,
    "bounded-hash",
  );
  appendBoundedFact(
    provenance, "influence", "Influence policy",
    `${documentValue.influence_policy_id} · ${documentValue.influence_policy_sha256}`,
    "bounded-hash",
  );
  appendBoundedFact(
    provenance, "causal-contract", "Causal contract",
    documentValue.causal_contract_sha256, "bounded-hash",
  );
  appendBoundedFact(
    provenance, "cognition-input", "Cognition input",
    documentValue.cognition_input_sha256, "bounded-hash",
  );

  byId("bounded-cognition-replay").textContent = "Saved replay is frozen and offline: this inspector reads the verified saved signals and never calls a provider. A fresh live run is not guaranteed to reproduce them.";
  byId("bounded-cognition-disclosure").textContent = `Disclosure: ${summary.disclosure}. Categories and the fixed mapping are synthetic assumptions, not observed behavior, validated psychology, or a prediction.`;

  const filter = byId("bounded-signal-filter");
  const records = state.boundedCognition.records.records;
  filter.replaceChildren(...boundedSignalFilters.map(([key, label]) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = `${label} (${records.filter((record) => boundedSignalMatches(record, key)).length})`;
    return option;
  }));
  filter.value = state.boundedSignalFilter;
  byId("bounded-signal-index").open = false;
  renderBoundedSignalIndex();
  renderBoundedCognitionShortcut(title, coverage, documentValue);
  byId("model-boundary-label").textContent = boundaryLabel;
}

// A keyboard-reachable shortcut from the top of the sidebar to the run-level evidence.
function renderBoundedCognitionShortcut(title, coverage, documentValue) {
  let shortcut = byId("bounded-cognition-shortcut");
  if (!shortcut) {
    shortcut = document.createElement("button");
    shortcut.type = "button";
    shortcut.id = "bounded-cognition-shortcut";
    shortcut.className = "bounded-cognition-shortcut";
    shortcut.addEventListener("click", () => {
      const panel = byId("bounded-cognition-panel");
      panel.scrollIntoView({ block: "start" });
      panel.focus({ preventScroll: true });
    });
    document.querySelector(".sidebar-intro").append(shortcut);
  }
  const source = createTextElement("small", "bounded-shortcut-source");
  if (documentValue.model_id === null) {
    source.textContent = documentValue.mode === "mock" ? "Offline mock · no model" : "Rules identity · no model";
  } else {
    source.append(createTextElement("span", "", "Model "), createIsolatedText(documentValue.model_id));
  }
  shortcut.replaceChildren(
    createTextElement("span", "", title),
    createTextElement(
      "strong",
      "",
      `${coverage.selected_request_count} saved ${coverage.selected_request_count === 1 ? "request" : "requests"} · ${coverage.eligible_response_count} eligible responses`,
    ),
    source,
    createTextElement("small", "", "View the bounded cognition evidence"),
  );
}

function renderBoundedSignalIndex() {
  if (!state.boundedCognition) return;
  const records = state.boundedCognition.records.records;
  const filter = state.boundedSignalFilter;
  const fragment = document.createDocumentFragment();
  let shown = 0;
  for (const record of records) {
    if (!boundedSignalMatches(record, filter)) continue;
    shown += 1;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = `bounded-signal-jump source-${record.source}`;
    button.dataset.recordHash = record.record_hash;
    button.dataset.source = record.source;
    button.append(
      createTextElement(
        "span",
        "bounded-signal-time",
        `#${record.selection_index + 1} · ${dayText(record.model_minute)} · ${clockText(record.model_minute)}`,
      ),
      createTextElement("strong", "", `${record.agent_id.toUpperCase()} · ${record.campaign_id.toUpperCase()}`),
      createTextElement("small", "", `${boundedSourceTitles[record.source]} · ${boundedSignalText(record)}`),
    );
    button.setAttribute(
      "aria-label",
      `Go to saved request ${record.selection_index + 1}: ${record.agent_id}, ${dayText(record.model_minute)} ${clockText(record.model_minute)}, ${boundedSourceTitles[record.source]}, ${boundedSignalText(record)}`,
    );
    button.addEventListener("click", () => jumpToBoundedSignal(record));
    item.append(button);
    fragment.append(item);
  }
  byId("bounded-signal-list").replaceChildren(fragment);
  byId("bounded-signal-empty").hidden = shown !== 0;
  byId("bounded-signal-count").textContent = `${shown} of ${records.length} requests`;
}

async function jumpToBoundedSignal(record) {
  stopPlayback();
  const generation = ++state.causalSelectionGeneration;
  if (!await setMinute(record.model_minute, record.agent_id, null, false)) return;
  if (generation !== state.causalSelectionGeneration || !state.responsePage) return;
  const applied = state.responsePage.items.find((item) => (
    item.event_type === "spatial.applied-response"
    && item.terminal_record_hash === record.record_hash
  ));
  if (!applied) {
    announceTimeline(`Saved request ${record.selection_index + 1} is on another response evidence page at this minute.`);
    return;
  }
  state.selectedCampaign = applied.baseline.campaign_id;
  state.selectedEvidence = selectedEvidenceIdentity("response", applied);
  renderOpportunityEvidence();
  renderAttentionEvidence();
  renderResponseEvidence();
  renderMap();
  renderMapTextAlternative();
  announceTimeline(`Saved request ${record.selection_index + 1} selected. ${byId("map-text-summary").textContent}`);
  const card = document.querySelector(`[data-evidence-id="${applied.event_id}"]`);
  if (card instanceof HTMLElement) {
    // Start the selected response at the top so its whole ordered trace stays in view.
    card.scrollIntoView({ block: "start" });
    card.focus({ preventScroll: true });
  }
}

function boundedDualStageText(appliedId, update) {
  if (update === null) {
    return "Committed atomically by this minute group's dual-state update on the next evidence page.";
  }
  const position = update.caused_by_event_ids.indexOf(appliedId) + 1;
  return `Committed atomically as cause ${position} of ${update.caused_by_event_ids.length} by update ${update.event_id.slice(0, 10).toUpperCase()} · rule sentiment ${proxyText(update.previous_rule_state.brand_sentiment, "Previous rule sentiment")} → ${proxyText(update.rule_state.brand_sentiment, "Rule sentiment")} · cognition sentiment ${proxyText(update.previous_cognition_state.synthetic_sentiment, "Previous cognition sentiment")} → ${proxyText(update.cognition_state.synthetic_sentiment, "Cognition sentiment")}`;
}

// Text badges name the signal source and its effect; styles add shape, never meaning alone.
function createBoundedBadges(presentation) {
  const row = createTextElement("p", "bounded-badges");
  for (const badge of presentation.badges) {
    const element = createTextElement("span", `bounded-badge badge-${badge.kind}`);
    element.dataset.badge = badge.kind;
    element.dataset.value = badge.key;
    element.append(
      createTextElement("span", "bounded-badge-kind", badge.kind === "source" ? "Source" : "Effect"),
      createTextElement("strong", "", badge.label),
    );
    row.append(element);
  }
  return row;
}

const boundedChipLabels = {
  applied: "Recorded appraisal categories passed to the fixed mapping",
  neutral: "Fixed neutral vector, applied as an exact identity",
  "set-aside": "Recorded model categories, set aside and not applied",
};

function createBoundedChips(stage) {
  const chips = document.createElement("ul");
  chips.className = `bounded-chips chips-${stage.chipsKind}`;
  chips.dataset.kind = stage.chipsKind;
  chips.setAttribute("aria-label", boundedChipLabels[stage.chipsKind]);
  for (const chip of stage.chips) {
    const chipItem = createTextElement("li", `bounded-chip chip-${chip.key}`);
    chipItem.dataset.dimension = chip.key;
    chipItem.dataset.value = chip.value;
    chipItem.append(
      createTextElement("span", "", chip.title),
      createTextElement("strong", "", chip.value),
    );
    chips.append(chipItem);
  }
  for (const flag of stage.safetyFlags) {
    const flagItem = createTextElement("li", "bounded-chip chip-flag");
    flagItem.dataset.flag = flag;
    flagItem.append(
      createTextElement("span", "", "Safety flag"),
      createTextElement("strong", "", flag.replaceAll("-", " ")),
    );
    chips.append(flagItem);
  }
  return chips;
}

function createBoundedTrace(presentation, update) {
  const trace = document.createElement("ol");
  trace.className = "bounded-trace";
  trace.dataset.effect = presentation.effect;
  trace.setAttribute(
    "aria-label",
    `Bounded cognition trace for ${presentation.agentId.toUpperCase()} at ${clockText(presentation.modelMinute)}`,
  );
  presentation.stages.forEach((stage, index) => {
    const item = document.createElement("li");
    item.className = `bounded-stage stage-${stage.key}`;
    item.dataset.stage = stage.key;
    const marker = createTextElement("span", "bounded-stage-marker", String(index + 1));
    marker.setAttribute("aria-hidden", "true");
    const body = createTextElement("div", "bounded-stage-body");
    const header = createTextElement("div", "bounded-stage-heading");
    header.append(
      createTextElement("strong", "bounded-stage-title", stage.title),
      createTextElement("span", "bounded-stage-status", stage.status),
    );
    body.append(header);
    if (stage.key === "signal" && stage.chips.length > 0) {
      body.append(createBoundedChips(stage));
      if (stage.chipsNote !== null) {
        const note = createTextElement("p", "bounded-chips-note", stage.chipsNote);
        note.dataset.kind = stage.chipsKind;
        body.append(note);
      }
    }
    const detail = stage.key === "dual"
      ? boundedDualStageText(presentation.appliedEventId, update)
      : stage.detail;
    body.append(createTextElement("p", "bounded-stage-detail", detail));
    if (stage.key === "mapping") {
      for (const line of stage.formula) {
        body.append(createTextElement("p", "bounded-stage-formula", line));
      }
    }
    item.append(marker, body);
    trace.append(item);
  });
  return trace;
}

function boundedTrackSection(kind, title, rows) {
  const section = document.createElement("section");
  section.className = `bounded-track bounded-${kind}-track`;
  section.dataset.track = kind;
  section.append(createTextElement("h4", "", title));
  const list = document.createElement("dl");
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(createTextElement("dt", "", label), createTextElement("dd", "", value));
    list.append(row);
  }
  section.append(list);
  return section;
}

function boundedChange(previous, committed, label) {
  return `${proxyText(previous, `Previous ${label}`)} → ${proxyText(committed, label)}`;
}

function createBoundedDualTracks(update) {
  const tracks = createTextElement("div", "bounded-dual-tracks");
  const previousRule = update.previous_rule_state;
  const rule = update.rule_state;
  const previousCognition = update.previous_cognition_state;
  const cognition = update.cognition_state;
  tracks.append(
    boundedTrackSection("rule", "Rule track · rule-owned", [
      ["Sentiment", boundedChange(previousRule.brand_sentiment, rule.brand_sentiment, "rule sentiment")],
      ["Recall", boundedChange(previousRule.recall_strength, rule.recall_strength, "rule recall")],
      [
        "Purchase-intention proxy (uncalibrated)",
        boundedChange(previousRule.purchase_intention, rule.purchase_intention, "intention proxy"),
      ],
      ["Responses", `${previousRule.response_count} → ${rule.response_count}`],
    ]),
    boundedTrackSection("cognition", "Cognition track · synthetic", [
      [
        "Sentiment",
        boundedChange(previousCognition.synthetic_sentiment, cognition.synthetic_sentiment, "cognition sentiment"),
      ],
      [
        "Recall",
        boundedChange(previousCognition.synthetic_recall, cognition.synthetic_recall, "cognition recall"),
      ],
      ["Responses", `${previousCognition.response_count} → ${cognition.response_count}`],
      ["Last minute", `${dayText(cognition.last_response_minute)} · ${clockText(cognition.last_response_minute)}`],
    ]),
  );
  return tracks;
}

function renderBoundedResponseEvidence() {
  const page = state.responsePage;
  const list = byId("response-list");
  const fragment = document.createDocumentFragment();
  const selectedCount = page.agent_counts[state.selected] || 0;
  const cognition = state.boundedCognition;
  const helper = cognition === null ? null : boundedCognitionHelper();
  const updates = page.items.filter((item) => item.event_type === "spatial.dual-state-updated");
  for (const record of page.items) {
    const subject = evidenceSubject(record);
    const item = createTextElement("div", "response-evidence-item");
    const card = document.createElement("button");
    card.type = "button";
    card.className = "response-card";
    card.dataset.evidenceId = record.event_id;
    card.dataset.campaignId = subject.campaign_id;
    if (typeof subject.placement_id === "string") card.dataset.placementId = subject.placement_id;
    card.classList.toggle("selected", subject.agent_id === state.selected);
    const causalSelected = state.selectedEvidence !== null
      && state.selectedEvidence.id === record.event_id;
    card.classList.toggle("causal-selected", causalSelected);
    card.classList.toggle("causal-related", isCausalEvidence(record.event_id));
    card.setAttribute("aria-pressed", String(causalSelected));
    const stageLabel = createTextElement("span", "response-stage");
    const at = createTextElement("span", "response-at", clockText(subject.model_minute));
    const identity = createTextElement(
      "strong",
      "",
      `${subject.agent_id.toUpperCase()} · ${subject.campaign_id.toUpperCase()}`,
    );
    const detail = document.createElement("small");
    if (record.event_type === "spatial.applied-response") {
      const presentation = helper === null
        ? null
        : helper.presentationForAppliedResponse(record, cognition.index);
      item.classList.add("bounded-applied-item");
      item.dataset.appliedEventId = record.event_id;
      item.dataset.baselineEventId = record.baseline.event_id;
      card.classList.add("rule-response", "bounded-applied-card");
      card.dataset.source = record.source;
      card.dataset.neutralization = record.neutralization_status;
      if (record.terminal_record_hash !== null) {
        card.dataset.terminalRecordHash = record.terminal_record_hash;
      }
      stageLabel.textContent = "APPLIED RESPONSE";
      at.textContent += `:${String(Math.floor(subject.millisecond_within_minute / 1000)).padStart(2, "0")}`;
      detail.textContent = `${subject.channel === "mobile-feed" ? "PHONE" : "ROADSIDE"} · ${boundedSourceTitles[record.source].toUpperCase()} · APPLIED SENTIMENT Δ ${signedProxyText(record.applied_sentiment_delta, "Applied sentiment delta")} · RECALL Δ ${signedProxyText(record.applied_recall_delta, "Applied recall delta")}`;
      card.append(stageLabel, at, identity, detail);
      item.append(card);
      if (presentation === null) {
        item.append(createTextElement(
          "p",
          "bounded-trace-pending",
          "Verifying the saved cognition evidence for this response…",
        ));
      } else {
        const update = updates.find((candidate) => (
          candidate.caused_by_event_ids.includes(record.event_id)
        )) || null;
        item.dataset.effect = presentation.effect;
        item.append(createBoundedBadges(presentation), createBoundedTrace(presentation, update));
      }
    } else if (record.event_type === "spatial.dual-state-updated") {
      item.classList.add("bounded-dual-item");
      card.classList.add("state-update", "bounded-dual-card");
      stageLabel.textContent = "DUAL STATE UPDATE";
      detail.textContent = `${record.caused_by_event_ids.length} ${record.caused_by_event_ids.length === 1 ? "CAUSE" : "CAUSES"} · RULE AND COGNITION TRACKS COMMITTED TOGETHER`;
      card.append(stageLabel, at, identity, detail);
      item.append(card, createBoundedDualTracks(record));
    } else {
      throw new Error("Unknown spatial response record type");
    }
    card.addEventListener("click", () => selectCausalEvidence("response", record));
    fragment.append(item);
  }
  list.replaceChildren(fragment);
  byId("response-current").textContent = `${page.total} RECORDS AT THIS MINUTE · ${selectedCount} FOR SELECTED AGENT`;
  byId("response-empty").hidden = page.total !== 0;
  renderEvidencePagination("response", page);
}

function renderBoundedResponseState() {
  const page = state.responseStatePage;
  const list = byId("response-state-list");
  byId("response-state-current").textContent = `${page.total} FINAL CAMPAIGN STATES FOR SELECTED AGENT · RULE AND COGNITION TRACKS`;
  byId("response-state-empty").hidden = page.total !== 0;
  const pageNote = byId("response-state-page-note");
  pageNote.hidden = page.next_offset === null;
  pageNote.textContent = `Showing the first ${page.items.length} of ${page.total} final campaign states.`;
  const fragment = document.createDocumentFragment();
  for (const pair of page.items) {
    const rule = pair.rule_state;
    const cognition = pair.cognition_state;
    const group = createTextElement("article", "bounded-state-pair");
    group.dataset.campaignId = rule.campaign_id;
    group.append(createTextElement(
      "strong",
      "bounded-state-identity",
      `${rule.agent_id.toUpperCase()} · ${rule.campaign_id.toUpperCase()}`,
    ));
    const timing = (minute) => (
      minute === null
        ? "NO RESPONSE COMMITTED"
        : `LAST RESPONSE ${dayText(minute)} · ${clockText(minute)}`
    );
    const ruleCard = createTextElement("section", "response-state-card bounded-rule-card");
    ruleCard.dataset.track = "rule";
    ruleCard.append(
      createTextElement("strong", "", "RULE TRACK · RULE-OWNED"),
      createTextElement("span", "", `${rule.response_count} RESPONSES`),
      createTextElement(
        "small",
        "",
        `SENTIMENT ${proxyText(rule.brand_sentiment, "Final rule sentiment")} · RECALL ${proxyText(rule.recall_strength, "Final rule recall")} · INTENTION PROXY ${proxyText(rule.purchase_intention, "Final intention proxy")}`,
      ),
      createTextElement("small", "", timing(rule.last_response_minute)),
    );
    const cognitionCard = createTextElement(
      "section",
      "response-state-card bounded-cognition-card",
    );
    cognitionCard.dataset.track = "cognition";
    // Both tracks start from one initial state, so the gap is exactly what the bounded
    // signals did to the synthetic cognition track for this agent and campaign.
    const sentimentGap = cognition.synthetic_sentiment - rule.brand_sentiment;
    const recallGap = cognition.synthetic_recall - rule.recall_strength;
    const gap = createTextElement(
      "small",
      "bounded-track-gap",
      sentimentGap === 0 && recallGap === 0
        ? "IDENTICAL TO THE RULE TRACK"
        : `VS RULE TRACK · SENTIMENT ${signedProxyText(sentimentGap, "Sentiment gap")} · RECALL ${signedProxyText(recallGap, "Recall gap")}`,
    );
    gap.dataset.identical = String(sentimentGap === 0 && recallGap === 0);
    cognitionCard.append(
      createTextElement("strong", "", "COGNITION TRACK · SYNTHETIC"),
      createTextElement("span", "", `${cognition.response_count} RESPONSES`),
      createTextElement(
        "small",
        "",
        `SENTIMENT ${proxyText(cognition.synthetic_sentiment, "Final cognition sentiment")} · RECALL ${proxyText(cognition.synthetic_recall, "Final cognition recall")}`,
      ),
      createTextElement("small", "", timing(cognition.last_response_minute)),
      gap,
    );
    group.append(ruleCard, cognitionCard);
    fragment.append(group);
  }
  list.replaceChildren(fragment);
}

function validateBoundedResponseSummary(value) {
  const summary = requireRecord(value, "Spatial response summary");
  const counts = requireRecord(summary.counts, "Spatial response summary");
  const invalidSummary = () => {
    throw new Error("Spatial response summary is invalid");
  };
  if (
    !hasExactKeys(summary, boundedResponseSummaryKeys)
    || !hasExactKeys(counts, boundedResponseCountKeys)
    || summary.schema_version !== 2
    || summary.model_id !== "spatial-response-artifact-v2"
    || summary.claim_scope !== boundedClaimScope
    || summary.response_model_id !== boundedResponseModelId
    || counts.schema_version !== 2
    || [
      "response_input_sha256", "scenario_sha256", "city_sha256", "cognition_input_sha256",
      "influence_policy_sha256", "stream_sha256", "state_document_sha256",
      "rule_state_document_sha256", "cognition_state_document_sha256",
    ].some((field) => !validEvidenceHash(summary[field]))
    || summary.city_sha256 !== state.meta.city_sha256
    || (state.opportunitySummary && summary.scenario_sha256 !== state.opportunitySummary.scenario_sha256)
    || !Number.isInteger(summary.attention_seed)
    || summary.attention_seed < 0
    || !Number.isInteger(summary.stream_bytes)
    || summary.stream_bytes < 0
    || summary.stream_bytes > 2147483648
    || !Number.isInteger(summary.state_document_bytes)
    || summary.state_document_bytes < 1
    || summary.state_document_bytes > 8388608
  ) invalidSummary();
  for (const key of boundedResponseCountKeys.slice(1)) {
    requireBoundedInteger(counts[key], 0, 520800, "Spatial response summary");
  }
  if (
    counts.baseline_response_count !== counts.applied_response_count
    || counts.baseline_response_count !== counts.roadside_response_count + counts.phone_response_count
    || counts.final_rule_state_count !== counts.final_cognition_state_count
    || counts.campaign_count < 1
    || counts.campaign_count > 20
    || counts.final_rule_state_count !== state.agents.length * counts.campaign_count
    || (
      counts.baseline_response_count === 0
        ? counts.dual_state_update_count !== 0
        : counts.dual_state_update_count < 1
          || counts.dual_state_update_count > counts.baseline_response_count
    )
  ) invalidSummary();
  return summary;
}

function renderBoundedResponseFrame() {
  document.body.classList.add("bounded-cognition-run");
  const rail = byId("response-causal");
  rail.classList.add("bounded-causal");
  rail.setAttribute(
    "aria-label",
    "Notice causes a deterministic baseline; a bounded signal or neutral status passes the fixed mapping and commits with the baseline in one dual-state update",
  );
  const fragment = document.createDocumentFragment();
  ["NOTICE", "BASELINE", "SIGNAL", "MAPPING", "DUAL STATE"].forEach((label, index) => {
    if (index > 0) {
      const arrow = createTextElement("i", "", "→");
      arrow.setAttribute("aria-hidden", "true");
      fragment.append(arrow);
    }
    fragment.append(createTextElement("span", "", label));
  });
  rail.replaceChildren(fragment);
  byId("response-title").textContent = "Responses & bounded cognition";
  byId("response-claim").textContent = "Modeled response · bounded synthetic cognition · not observed behavior";
  const updateLabel = document.querySelector("#response-panel .response-totals div:nth-child(2) span");
  if (updateLabel) updateLabel.textContent = "DUAL UPDATES";
  byId("response-state-claim").textContent = "Rule track: uncalibrated synthetic scores; the intention proxy is rule-owned, not purchase probability or sales. Cognition track: synthetic sentiment and recall only.";
}

async function loadBoundedCognitionEvidence() {
  const helper = boundedCognitionHelper();
  const [summaryValue, recordsValue] = await Promise.all([
    fetchJson("/cognition-summary"),
    fetchJson("/cognition-records"),
  ]);
  const summary = helper.validateSummary(summaryValue);
  const input = state.workbenchInput;
  const cognition = input.cognition;
  const contract = cognition.causal_contract;
  const responseSummary = state.responseSummary;
  if (
    summary.run_id !== state.meta.run_id
    || summary.run_id !== input.run_id
    || summary.mode !== cognition.mode
    || summary.provider_profile_id !== cognition.provider_profile_id
    || summary.provider_revision_sha256 !== cognition.provider_revision_sha256
    || summary.funded_response_limit !== cognition.funded_response_limit
    || summary.causal_contract_sha256 !== cognition.causal_contract_sha256
    || [
      "prompt_contract_sha256", "result_schema_contract_sha256", "selection_policy_sha256",
      "fallback_policy_sha256", "mock_policy_sha256", "influence_policy_sha256",
    ].some((field) => summary[field] !== contract[field])
    || summary.prompt_contract_id !== contract.prompt_contract_id
    || summary.result_schema_id !== contract.result_schema_id
    || summary.influence_policy_id !== contract.influence_policy.policy_id
    || summary.cognition_input_sha256 !== responseSummary.cognition_input_sha256
    || summary.influence_policy_sha256 !== responseSummary.influence_policy_sha256
    || summary.summary.coverage.eligible_response_count
      !== responseSummary.counts.baseline_response_count
  ) invalidCognitionEvidence();
  // The saved prompt and result schema must hash to the identities the run declares.
  // The prompt text is checked here but never rendered.
  const [promptSha256, resultSchemaSha256] = await Promise.all([
    sha256Text(contract.prompt_contract_text),
    sha256Text(contract.result_schema_document),
  ]);
  if (
    promptSha256 !== summary.prompt_contract_sha256
    || resultSchemaSha256 !== summary.result_schema_contract_sha256
  ) invalidCognitionEvidence();
  const records = helper.validateRecords(recordsValue, summary);
  if (records.records.some((record) => (
    record.run_input_sha256 !== input.workbench_input_sha256
    || record.response_input_sha256 !== responseSummary.response_input_sha256
    || record.scenario_sha256 !== input.scenario_sha256
    || record.city_sha256 !== input.city_sha256
    || !knownEvidenceAgents().has(record.agent_id)
  ))) invalidCognitionEvidence();
  state.boundedCognition = Object.freeze({
    summary,
    records,
    index: helper.indexTerminalRecords(records),
  });
}

function metricValueText(name, value) {
  if (!Number.isFinite(value)) throw new Error(`Metric ${name} is not finite`);
  if (name.endsWith("_reach") || name === "notice_rate") {
    const percentage = value * 100;
    return `${Number.isInteger(percentage) ? percentage : Number(percentage.toFixed(1))}%`;
  }
  return String(Number(value.toFixed(3)));
}

function renderSpatialMetrics() {
  if (!state.spatialMetrics) return;
  if (state.spatialMetrics.claim_scope !== "synthetic-metrics-not-observed-outcomes") {
    throw new Error("Spatial metrics claim scope is invalid");
  }
  const series = [state.spatialMetrics.overall, ...state.spatialMetrics.channels];
  const names = ["opportunity_reach", "impression_reach", "noticed_reach", "impression_frequency", "notice_rate"];
  for (const group of series) {
    for (const name of names) {
      const receipt = group[name];
      const token = name.replaceAll("_", "-");
      const prefix = `metrics-${group.channel}-${token}`;
      const valueText = metricValueText(name, receipt.value);
      byId(`${prefix}-value`).textContent = valueText;
      byId(`${prefix}-receipt`).textContent = `${receipt.numerator} / ${receipt.denominator}`;
      const cell = byId(prefix);
      const provenance = `Numerator ${receipt.numerator}; denominator ${receipt.denominator}. Sources: ${receipt.source_artifacts.join(", ")} (${receipt.source_event_types.join(", ")}).`;
      cell.title = provenance;
      cell.tabIndex = 0;
      cell.setAttribute(
        "aria-label",
        `${group.channel} ${name.replaceAll("_", " ")}: ${valueText}. ${provenance}`,
      );
    }
  }
  byId("metrics-panel").hidden = false;
}

const responseMetricNames = [
  ["response_count", "Response count"],
  ["response_reach", "Response reach"],
  ["response_frequency", "Response frequency"],
  ["mean_rule_sentiment_delta", "Mean planned sentiment delta"],
  ["mean_rule_recall_delta", "Mean planned recall delta"],
];
const responseStateNames = [
  ["brand_sentiment", "Brand sentiment"],
  ["recall_strength", "Recall strength"],
  ["purchase_intention_proxy", "Purchase intention proxy"],
];
const responseHashFields = [
  "scenario_sha256",
  "city_sha256",
  "agents_sha256",
  "trace_sha256",
  "opportunity_structure_sha256",
  "response_input_sha256",
  "response_assumption_structure_sha256",
  "response_stream_sha256",
  "response_state_sha256",
];
const responseMetricReceiptKeys = [
  "schema_version",
  "name",
  "numerator",
  "denominator",
  "value",
  "source_event_types",
  "source_artifacts",
];
const responseStateReceiptKeys = [
  "schema_version",
  "name",
  "initial_total",
  "final_total",
  "change_total",
  "denominator",
  "initial_mean",
  "final_mean",
  "mean_change",
  "source_artifacts",
];
const responseDocumentKeys = [
  "schema_version",
  "model_id",
  "claim_scope",
  "source_run_schema_version",
  "opportunity_model_id",
  "attention_model_id",
  "response_model_id",
  ...responseHashFields,
  "seed",
  "population_size",
  "days",
  "campaign_count",
  "overall",
  "channels",
  "campaigns",
];
const maxSpatialResponses = 520800;
const maxResponseStateRows = 600;
const smallestNormalBinary64 = 2 ** -1022;
const binary64View = new DataView(new ArrayBuffer(8));

function requireRecord(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function requireFiniteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function requireBoundedNumber(value, minimum, maximum, label) {
  const number = requireFiniteNumber(value, label);
  if (number < minimum || number > maximum) throw new Error(`${label} is invalid`);
  return number;
}

function requireBoundedInteger(value, minimum, maximum, label) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function sameStrings(actual, expected) {
  return Array.isArray(actual)
    && actual.length === expected.length
    && actual.every((value, index) => value === expected[index]);
}

function hasExactKeys(record, expected) {
  return sameStrings(Object.keys(record).sort(), [...expected].sort());
}

function validateResponseMetricReceipt(receiptValue, name) {
  const receipt = requireRecord(receiptValue, "Response metric receipt");
  if (
    !hasExactKeys(receipt, responseMetricReceiptKeys)
    || receipt.schema_version !== 1
    || receipt.name !== name
    || !sameStrings(receipt.source_event_types, ["spatial.response"])
    || !sameStrings(receipt.source_artifacts, ["outputs/spatial-responses.jsonl"])
  ) {
    throw new Error("Response metric receipt is invalid");
  }
  const denominator = requireBoundedInteger(
    receipt.denominator,
    0,
    maxSpatialResponses,
    "Response metric denominator",
  );
  const countReceipt = ["response_count", "response_reach", "response_frequency"].includes(name);
  const numerator = countReceipt
    ? requireBoundedInteger(
      receipt.numerator,
      0,
      maxSpatialResponses,
      "Response metric numerator",
    )
    : requireBoundedNumber(
      receipt.numerator,
      -maxSpatialResponses,
      maxSpatialResponses,
      "Response metric numerator",
    );
  const value = requireBoundedNumber(
    receipt.value,
    countReceipt ? 0 : -maxSpatialResponses,
    maxSpatialResponses,
    "Response metric value",
  );
  const expected = denominator === 0 ? 0 : numerator / denominator;
  if (value !== expected || Object.is(value, -0)) {
    throw new Error("Response metric receipt is inconsistent");
  }
  return receipt;
}

function validateResponseStateReceipt(receiptValue, name) {
  const receipt = requireRecord(receiptValue, "Response state receipt");
  if (
    !hasExactKeys(receipt, responseStateReceiptKeys)
    || receipt.schema_version !== 1
    || receipt.name !== name
    || !sameStrings(receipt.source_artifacts, [
      "inputs/spatial-response.json",
      "outputs/response-state.json",
    ])
  ) {
    throw new Error("Response state receipt is invalid");
  }
  const denominator = requireBoundedInteger(
    receipt.denominator,
    1,
    maxResponseStateRows,
    "Response state denominator",
  );
  const minimum = name === "brand_sentiment" ? -maxResponseStateRows : 0;
  const initialTotal = requireBoundedNumber(
    receipt.initial_total,
    minimum,
    maxResponseStateRows,
    "Response initial total",
  );
  const finalTotal = requireBoundedNumber(
    receipt.final_total,
    minimum,
    maxResponseStateRows,
    "Response final total",
  );
  const changeTotal = requireBoundedNumber(
    receipt.change_total,
    -(maxResponseStateRows * 2),
    maxResponseStateRows * 2,
    "Response change total",
  );
  const meanMinimum = name === "brand_sentiment" ? -1 : 0;
  const initialMean = requireBoundedNumber(
    receipt.initial_mean,
    meanMinimum,
    1,
    "Response initial mean",
  );
  const finalMean = requireBoundedNumber(
    receipt.final_mean,
    meanMinimum,
    1,
    "Response final mean",
  );
  const meanChange = requireBoundedNumber(
    receipt.mean_change,
    -2,
    2,
    "Response mean change",
  );
  if (
    changeTotal !== finalTotal - initialTotal
    || initialMean !== initialTotal / denominator
    || finalMean !== finalTotal / denominator
    || meanChange !== changeTotal / denominator
    || Object.is(meanChange, -0)
  ) {
    throw new Error("Response state receipt is inconsistent");
  }
  return receipt;
}

function validateResponseEventSeries(seriesValue, additionalKeys = []) {
  const series = requireRecord(seriesValue, "Response metric series");
  const expectedKeys = ["schema_version", ...responseMetricNames.map(([name]) => name), ...additionalKeys];
  if (!hasExactKeys(series, expectedKeys) || series.schema_version !== 1) {
    throw new Error("Response metric series is invalid");
  }
  for (const [name] of responseMetricNames) validateResponseMetricReceipt(series[name], name);
  if (
    series.response_count.denominator !== 1
    || series.response_frequency.numerator !== series.response_count.numerator
    || series.response_frequency.denominator !== series.response_reach.numerator
    || series.mean_rule_sentiment_delta.denominator !== series.response_count.numerator
    || series.mean_rule_recall_delta.denominator !== series.response_count.numerator
  ) {
    throw new Error("Response metric series is inconsistent");
  }
  return series;
}

function validateResponseAggregateSeries(seriesValue, additionalKeys = []) {
  const series = validateResponseEventSeries(
    seriesValue,
    [...responseStateNames.map(([name]) => name), ...additionalKeys],
  );
  for (const [name] of responseStateNames) validateResponseStateReceipt(series[name], name);
  const denominators = new Set(responseStateNames.map(([name]) => series[name].denominator));
  if (denominators.size !== 1) throw new Error("Response state series is inconsistent");
  return series;
}

function preciseEvidenceSum(values) {
  const partials = [];
  for (const value of values) {
    let highInput = value;
    let count = 0;
    for (const partial of partials) {
      let lowInput = partial;
      if (Math.abs(highInput) < Math.abs(lowInput)) {
        [highInput, lowInput] = [lowInput, highInput];
      }
      const high = highInput + lowInput;
      const low = lowInput - (high - highInput);
      if (low !== 0) partials[count++] = low;
      highInput = high;
    }
    partials.length = count;
    partials.push(highInput);
  }
  if (partials.length === 0) return 0;
  let count = partials.length;
  let high = partials[--count];
  let low = 0;
  while (count > 0) {
    const value = partials[--count];
    const combined = high + value;
    const roundedValue = combined - high;
    low = value - roundedValue;
    high = combined;
    if (low !== 0) break;
  }
  if (
    count > 0
    && ((low < 0 && partials[count - 1] < 0) || (low > 0 && partials[count - 1] > 0))
  ) {
    const correction = low * 2;
    const corrected = high + correction;
    if (correction === corrected - high) high = corrected;
  }
  return high;
}

function binary64Ulp(value) {
  const absolute = Math.abs(value);
  if (absolute === 0 || absolute < smallestNormalBinary64) return Number.MIN_VALUE;
  binary64View.setFloat64(0, absolute, false);
  const biasedExponent = (binary64View.getUint32(0, false) >>> 20) & 0x7ff;
  return 2 ** (biasedExponent - 1023 - 52);
}

function matchesPartitionedEvidence(total, parts) {
  const regrouped = preciseEvidenceSum(parts);
  if (total === regrouped) return true;
  if (
    parts.length <= 1
    || parts.every((part) => Math.abs(part) < smallestNormalBinary64)
  ) return false;
  const roundingBudget = preciseEvidenceSum([
    binary64Ulp(total),
    binary64Ulp(regrouped),
    ...parts.map(binary64Ulp),
  ]) / 2;
  return Math.abs(total - regrouped) <= roundingBudget;
}

function containsCredentialShapedCampaignId(value) {
  return [
    /(?:^|-)(?:sk|pk|rk)-(?:live|test|proj)-?[a-z0-9][a-z0-9-]{7,}/i,
    /(?:^|-)(?:sk|pk|rk)-[a-z0-9][a-z0-9-]{19,}/i,
    /(?:^|-)xox[abeprs]-[a-z0-9-]{10,}/i,
    /(?:^|-)xapp-[a-z0-9-]{10,}/i,
  ].some((pattern) => pattern.test(value));
}

function validatePartitionedResponseEvidence(overall, slices) {
  const count = slices.reduce((total, series) => total + series.response_count.numerator, 0);
  if (overall.response_count.numerator !== count) {
    throw new Error("Response metric partitions are inconsistent");
  }
  for (const name of ["mean_rule_sentiment_delta", "mean_rule_recall_delta"]) {
    const numerators = slices.map((series) => series[name].numerator);
    const denominator = slices.reduce((total, series) => total + series[name].denominator, 0);
    if (
      !matchesPartitionedEvidence(overall[name].numerator, numerators)
      || overall[name].denominator !== denominator
    ) {
      throw new Error("Response metric partitions are inconsistent");
    }
  }
}

function validateSpatialResponseSeed(value, token) {
  if (
    typeof token !== "string"
    || !/^(?:0|[1-9][0-9]*)$/.test(token)
    || BigInt(token) > 9223372036854775807n
    || !Number.isInteger(value)
    || Number(token) !== value
  ) {
    throw new Error("Spatial response seed is invalid");
  }
}

function validateSpatialResponseMetrics(documentValue, seedToken) {
  const document = requireRecord(documentValue, "Spatial response metrics");
  if (
    !hasExactKeys(document, responseDocumentKeys)
    || document.schema_version !== 1
    || document.model_id !== "spatial-response-metrics-v1"
    || document.claim_scope !== "synthetic-response-metrics-not-observed-outcomes"
    || ![6, 7, 8, 9].includes(document.source_run_schema_version)
    || document.source_run_schema_version !== state.meta.run_schema_version
    || document.opportunity_model_id !== "spatial-opportunity-v1"
    || document.attention_model_id !== "spatial-attention-v1"
    || document.response_model_id !== "spatial-response-v1"
  ) {
    throw new Error("Spatial response metrics contract is invalid");
  }
  for (const field of responseHashFields) {
    if (typeof document[field] !== "string" || !/^[0-9a-f]{64}$/.test(document[field])) {
      throw new Error("Spatial response metrics contract is invalid");
    }
  }
  validateSpatialResponseSeed(document.seed, seedToken);
  if (seedToken !== state.metaSeedToken) {
    throw new Error("Spatial response seed is inconsistent");
  }
  const populationSize = requireBoundedInteger(
    document.population_size,
    1,
    30,
    "Spatial response population",
  );
  requireBoundedInteger(document.days, 1, 7, "Spatial response days");
  const campaignCount = requireBoundedInteger(
    document.campaign_count,
    1,
    20,
    "Spatial response campaign count",
  );
  const overall = validateResponseAggregateSeries(document.overall);
  if (
    !Array.isArray(document.channels)
    || document.channels.length !== 2
  ) {
    throw new Error("Spatial response metric channels are invalid");
  }
  const channels = document.channels.map((channel) => (
    validateResponseEventSeries(channel, ["channel"])
  ));
  if (channels[0].channel !== "roadside" || channels[1].channel !== "mobile") {
    throw new Error("Spatial response metric channels are invalid");
  }
  if (!Array.isArray(document.campaigns) || document.campaigns.length !== campaignCount) {
    throw new Error("Spatial response metric campaigns are invalid");
  }
  let previousCampaign = "";
  const campaigns = [];
  for (const campaign of document.campaigns) {
    const validatedCampaign = validateResponseAggregateSeries(campaign, ["campaign_id"]);
    if (
      typeof campaign.campaign_id !== "string"
      || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(campaign.campaign_id)
      || containsCredentialShapedCampaignId(campaign.campaign_id)
    ) {
      throw new Error("Spatial response metric campaign identity is invalid");
    }
    if (campaign.campaign_id <= previousCampaign) {
      throw new Error("Spatial response metric campaign order is invalid");
    }
    previousCampaign = campaign.campaign_id;
    campaigns.push(validatedCampaign);
  }
  const sourceCampaigns = state.opportunitySummary && Array.isArray(state.opportunitySummary.campaigns)
    ? state.opportunitySummary.campaigns
    : [];
  if (
    sourceCampaigns.length !== campaigns.length
    || campaigns.some((campaign, index) => sourceCampaigns[index].campaign_id !== campaign.campaign_id)
  ) {
    throw new Error("Spatial response metric campaign identity is invalid");
  }
  const allSeries = [overall, ...channels, ...campaigns];
  for (const series of allSeries) {
    if (
      series.response_reach.denominator !== populationSize
      || series.response_reach.numerator > populationSize
    ) {
      throw new Error("Response metric population evidence is inconsistent");
    }
  }
  validatePartitionedResponseEvidence(overall, channels);
  validatePartitionedResponseEvidence(overall, campaigns);
  const overallStateDenominator = populationSize * campaignCount;
  for (const [name] of responseStateNames) {
    const overallState = overall[name];
    const campaignStates = campaigns.map((campaign) => campaign[name]);
    if (
      overallState.denominator !== overallStateDenominator
      || campaignStates.some((receipt) => receipt.denominator !== populationSize)
      || !matchesPartitionedEvidence(
        overallState.initial_total,
        campaignStates.map((receipt) => receipt.initial_total),
      )
      || !matchesPartitionedEvidence(
        overallState.final_total,
        campaignStates.map((receipt) => receipt.final_total),
      )
    ) {
      throw new Error("Response state partitions are inconsistent");
    }
  }
  return document;
}

function responseMetricValueText(name, value) {
  if (name === "response_count") return String(value);
  if (name === "response_reach") {
    const percentage = value * 100;
    return `${Number.isInteger(percentage) ? percentage : Number(percentage.toFixed(1))}%`;
  }
  if (name.startsWith("mean_rule_")) return signedProxyText(value, name);
  return proxyText(value, name);
}

function compactReceiptNumber(value) {
  return Number.isInteger(value) ? String(value) : proxyText(value, "Response receipt numerator");
}

function responseStateValueCell(name, kind, value, receipt) {
  const cell = document.createElement("td");
  cell.id = `response-metrics-state-${name.replaceAll("_", "-")}-${kind}`;
  cell.dataset.value = String(value);
  cell.tabIndex = 0;
  const visible = kind === "change"
    ? signedProxyText(value, `${name} ${kind}`)
    : proxyText(value, `${name} ${kind}`);
  const strong = document.createElement("strong");
  strong.textContent = visible;
  const small = document.createElement("small");
  small.textContent = `n = ${receipt.denominator}`;
  const provenance = `Exact value ${value}. Denominator ${receipt.denominator}. Sources: ${receipt.source_artifacts.join(", ")}.`;
  cell.title = provenance;
  cell.setAttribute("aria-label", `${name.replaceAll("_", " ")} ${kind}: ${visible}. ${provenance}`);
  cell.append(strong, small);
  return cell;
}

function renderResponseMetricsState(series, label) {
  const body = byId("response-metrics-state-body");
  const fragment = document.createDocumentFragment();
  for (const [name, displayName] of responseStateNames) {
    const receipt = series[name];
    const row = document.createElement("tr");
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = displayName;
    row.append(
      heading,
      responseStateValueCell(name, "initial", receipt.initial_mean, receipt),
      responseStateValueCell(name, "final", receipt.final_mean, receipt),
      responseStateValueCell(name, "change", receipt.mean_change, receipt),
    );
    fragment.append(row);
  }
  while (body.firstChild) body.removeChild(body.firstChild);
  body.append(fragment);
  byId("response-metrics-series-label").textContent = label;
}

function responseCampaignName(campaignId) {
  const campaigns = state.opportunitySummary && Array.isArray(state.opportunitySummary.campaigns)
    ? state.opportunitySummary.campaigns
    : [];
  const campaign = campaigns.find((item) => item.campaign_id === campaignId);
  return campaign && typeof campaign.name === "string" ? campaign.name : campaignId;
}

function renderSpatialResponseMetrics() {
  const metrics = validateSpatialResponseMetrics(
    state.spatialResponseMetrics,
    state.spatialResponseSeedToken,
  );
  renderResponseMetricTables(metrics);
}

// Shared by V6-V9 metrics and the V10 rule baseline; both carry exact V1 rule receipts.
function renderResponseMetricTables(metrics) {
  const eventBody = byId("response-metrics-event-body");
  const eventFragment = document.createDocumentFragment();
  const groups = [
    ["overall", metrics.overall],
    ["roadside", metrics.channels[0]],
    ["mobile", metrics.channels[1]],
  ];
  for (const [name, displayName] of responseMetricNames) {
    const row = document.createElement("tr");
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = displayName;
    row.append(heading);
    for (const [groupName, group] of groups) {
      const receipt = group[name];
      const cell = document.createElement("td");
      cell.id = `response-metrics-${groupName}-${name.replaceAll("_", "-")}`;
      cell.dataset.value = String(receipt.value);
      cell.tabIndex = 0;
      const valueText = responseMetricValueText(name, receipt.value);
      const strong = document.createElement("strong");
      strong.textContent = valueText;
      const small = document.createElement("small");
      small.textContent = `${compactReceiptNumber(receipt.numerator)}/${receipt.denominator}`;
      const provenance = `Numerator ${receipt.numerator}; denominator ${receipt.denominator}. Sources: ${receipt.source_artifacts.join(", ")} (${receipt.source_event_types.join(", ")}).`;
      cell.title = provenance;
      cell.setAttribute(
        "aria-label",
        `${groupName} ${name.replaceAll("_", " ")}: ${valueText}. ${provenance}`,
      );
      cell.append(strong, small);
      row.append(cell);
    }
    eventFragment.append(row);
  }
  while (eventBody.firstChild) eventBody.removeChild(eventBody.firstChild);
  eventBody.append(eventFragment);

  const selector = byId("response-metrics-campaign");
  while (selector.options.length > 1) selector.remove(1);
  for (const campaign of metrics.campaigns) {
    const option = document.createElement("option");
    option.value = campaign.campaign_id;
    option.textContent = `${responseCampaignName(campaign.campaign_id)} · ${campaign.campaign_id.toUpperCase()}`;
    selector.append(option);
  }
  selector.addEventListener("change", () => {
    try {
      stopPlayback();
      const campaignId = selector.value === "overall" ? null : selector.value;
      if (
        campaignId !== null
        && !metrics.campaigns.some((item) => item.campaign_id === campaignId)
      ) throw new Error("Unknown response metric campaign");
      state.selectedCampaign = campaignId;
      state.selectedEvidence = null;
      state.causalSelectionGeneration += 1;
      showResponseMetricSeries(campaignId);
      if (state.frame) renderTimeline();
    } catch (error) {
      showError(String(error));
    }
  });
  renderResponseMetricsState(metrics.overall, "ALL CAMPAIGNS");
  byId("response-metrics-panel").hidden = false;
}

// ---- Manifest-V10 response metrics: rule baseline and exploratory cognition stay separate ----

const boundedMetricsKeys = [
  "schema_version", "model_id", "claim_scope", "source_run_schema_version",
  "opportunity_model_id", "attention_model_id", "response_model_id", "scenario_sha256",
  "city_sha256", "agents_sha256", "trace_sha256", "opportunity_structure_sha256",
  "response_input_sha256", "response_assumption_structure_sha256", "response_stream_sha256",
  "response_state_sha256", "response_summary_sha256", "rule_state_document_sha256",
  "cognition_state_document_sha256", "cognition_input_sha256",
  "cognition_terminal_stream_sha256", "cognition_summary_sha256", "influence_policy_sha256",
  "seed", "population_size", "days", "campaign_count", "rule_baseline",
  "provider_dependent_cognition",
];
const boundedMetricsHashFields = boundedMetricsKeys.filter((key) => key.endsWith("_sha256"));
const boundedRuleBaselineKeys = [
  "schema_version", "claim_scope", "purchase_intention_proxy_scope",
  ...responseMetricNames.map(([name]) => name), ...responseStateNames.map(([name]) => name),
  "channels", "campaigns",
];
const boundedCognitionMetricKeys = [
  "schema_version", "claim_scope", "disclosure", "mode", "cognition_input_sha256",
  "cognition_terminal_stream_sha256", "cognition_summary_sha256", "causal_contract_sha256",
  "prompt_contract_sha256", "result_schema_contract_sha256", "selection_policy_sha256",
  "fallback_policy_sha256", "mock_policy_sha256", "influence_policy_id",
  "influence_policy_sha256", "provider_profile_id", "provider_model_id",
  "provider_revision_sha256", "provider_profile_snapshot_sha256", "effective_settings_sha256",
  "funded_response_limit", "coverage", "source_counts", "fallback_counts",
  "neutralization_counts", "category_counts", "funded_coverage", "mean_applied_sentiment",
  "mean_applied_recall", "synthetic_sentiment", "synthetic_recall",
];
const boundedCategoryFamilies = [
  ["affect", ["annoyed", "skeptical", "neutral", "curious", "positive", "uncertain"]],
  ["relevance", ["none", "low", "medium", "high", "uncertain"]],
  ["credibility", ["low", "medium", "high", "uncertain"]],
  ["salience", ["none", "low", "medium", "high", "uncertain"]],
];

function invalidBoundedMetrics() {
  throw new Error("Spatial response metrics contract is invalid");
}

function validateRuleBaselineMetrics(value, populationSize, campaignCount) {
  const rule = requireRecord(value, "Rule baseline metrics");
  if (
    !hasExactKeys(rule, boundedRuleBaselineKeys)
    || rule.schema_version !== 2
    || rule.claim_scope !== "rule-owned-synthetic-baseline-not-observed-outcomes"
    || rule.purchase_intention_proxy_scope !== "uncalibrated-rule-owned-proxy"
  ) invalidBoundedMetrics();
  for (const [name] of responseMetricNames) validateResponseMetricReceipt(rule[name], name);
  for (const [name] of responseStateNames) validateResponseStateReceipt(rule[name], name);
  if (
    rule.response_count.denominator !== 1
    || rule.response_frequency.numerator !== rule.response_count.numerator
    || rule.response_frequency.denominator !== rule.response_reach.numerator
    || rule.mean_rule_sentiment_delta.denominator !== rule.response_count.numerator
    || rule.mean_rule_recall_delta.denominator !== rule.response_count.numerator
  ) throw new Error("Response metric series is inconsistent");
  if (!Array.isArray(rule.channels) || rule.channels.length !== 2) invalidBoundedMetrics();
  const channels = rule.channels.map((channel) => validateResponseEventSeries(channel, ["channel"]));
  if (channels[0].channel !== "roadside" || channels[1].channel !== "mobile") {
    invalidBoundedMetrics();
  }
  if (!Array.isArray(rule.campaigns) || rule.campaigns.length !== campaignCount) {
    invalidBoundedMetrics();
  }
  let previousCampaign = "";
  const campaigns = rule.campaigns.map((campaign) => {
    const validated = validateResponseAggregateSeries(campaign, ["campaign_id"]);
    if (
      typeof campaign.campaign_id !== "string"
      || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(campaign.campaign_id)
      || containsCredentialShapedCampaignId(campaign.campaign_id)
      || campaign.campaign_id <= previousCampaign
    ) invalidBoundedMetrics();
    previousCampaign = campaign.campaign_id;
    return validated;
  });
  const sourceCampaigns = state.opportunitySummary.campaigns;
  if (
    sourceCampaigns.length !== campaigns.length
    || campaigns.some((campaign, index) => sourceCampaigns[index].campaign_id !== campaign.campaign_id)
  ) invalidBoundedMetrics();
  for (const series of [rule, ...channels, ...campaigns]) {
    if (
      series.response_reach.denominator !== populationSize
      || series.response_reach.numerator > populationSize
    ) throw new Error("Response metric population evidence is inconsistent");
  }
  validatePartitionedResponseEvidence(rule, channels);
  validatePartitionedResponseEvidence(rule, campaigns);
  for (const [name] of responseStateNames) {
    const overallState = rule[name];
    const campaignStates = campaigns.map((campaign) => campaign[name]);
    if (
      overallState.denominator !== populationSize * campaignCount
      || campaignStates.some((receipt) => receipt.denominator !== populationSize)
      || !matchesPartitionedEvidence(
        overallState.initial_total,
        campaignStates.map((receipt) => receipt.initial_total),
      )
      || !matchesPartitionedEvidence(
        overallState.final_total,
        campaignStates.map((receipt) => receipt.final_total),
      )
    ) throw new Error("Response state partitions are inconsistent");
  }
  return { overall: rule, channels, campaigns };
}

function validateBoundedQuotient(receiptValue, keys, name, extra) {
  const receipt = requireRecord(receiptValue, "Cognition metric receipt");
  if (!hasExactKeys(receipt, keys) || receipt.schema_version !== 2 || receipt.name !== name) {
    invalidBoundedMetrics();
  }
  requireBoundedInteger(receipt.denominator, 0, 520800, "Cognition metric denominator");
  requireFiniteNumber(receipt.numerator, "Cognition metric numerator");
  requireFiniteNumber(receipt.value, "Cognition metric value");
  const expected = receipt.denominator === 0 ? 0 : receipt.numerator / receipt.denominator;
  if (receipt.value !== expected || Object.is(receipt.value, -0) || !extra(receipt)) {
    invalidBoundedMetrics();
  }
  return receipt;
}

function validateBoundedStateReceipt(receiptValue, name, denominator) {
  const receipt = requireRecord(receiptValue, "Cognition state receipt");
  if (
    !hasExactKeys(receipt, responseStateReceiptKeys)
    || receipt.schema_version !== 2
    || receipt.name !== name
    || receipt.denominator !== denominator
    || !sameStrings(receipt.source_artifacts, [
      "inputs/spatial-response.json",
      "outputs/response-state.json",
    ])
  ) invalidBoundedMetrics();
  const minimum = name === "synthetic_sentiment" ? -1 : 0;
  requireBoundedNumber(receipt.initial_mean, minimum, 1, "Cognition state mean");
  requireBoundedNumber(receipt.final_mean, minimum, 1, "Cognition state mean");
  requireBoundedNumber(receipt.mean_change, -2, 2, "Cognition state change");
  if (
    receipt.change_total !== receipt.final_total - receipt.initial_total
    || receipt.initial_mean !== receipt.initial_total / denominator
    || receipt.final_mean !== receipt.final_total / denominator
    || receipt.mean_change !== receipt.change_total / denominator
    || Object.is(receipt.mean_change, -0)
  ) invalidBoundedMetrics();
  return receipt;
}

function validateBoundedCognitionMetrics(value, populationSize, campaignCount) {
  const cognition = requireRecord(value, "Provider-dependent cognition metrics");
  const verified = state.boundedCognition;
  if (!verified) invalidBoundedMetrics();
  const summary = verified.summary;
  if (
    !hasExactKeys(cognition, boundedCognitionMetricKeys)
    || cognition.schema_version !== 2
    || cognition.claim_scope !== "provider-dependent-exploratory-not-campaign-effect"
    || cognition.disclosure !== boundedClaimScope
    || cognition.mode !== summary.mode
    || cognition.cognition_input_sha256 !== summary.cognition_input_sha256
    || cognition.cognition_terminal_stream_sha256 !== summary.summary.terminal_stream_sha256
    || [
      "causal_contract_sha256", "prompt_contract_sha256", "result_schema_contract_sha256",
      "selection_policy_sha256", "fallback_policy_sha256", "mock_policy_sha256",
      "influence_policy_sha256",
    ].some((field) => cognition[field] !== summary[field])
    || cognition.influence_policy_id !== summary.influence_policy_id
    || cognition.provider_profile_id !== summary.provider_profile_id
    || cognition.provider_model_id !== summary.model_id
    || cognition.provider_revision_sha256 !== summary.provider_revision_sha256
    || cognition.funded_response_limit !== summary.funded_response_limit
    || ["cognition_summary_sha256", "effective_settings_sha256"].some(
      (field) => !validEvidenceHash(cognition[field]),
    )
    || (
      cognition.provider_profile_snapshot_sha256 !== null
      && !validEvidenceHash(cognition.provider_profile_snapshot_sha256)
    )
    || (cognition.provider_profile_snapshot_sha256 === null) !== (summary.provider_profile_id === null)
    || canonicalJson(cognition.coverage) !== canonicalJson(summary.summary.coverage)
    || canonicalJson(cognition.source_counts) !== canonicalJson(summary.summary.outcomes)
    || canonicalJson(cognition.fallback_counts) !== canonicalJson(summary.summary.reasons)
    || canonicalJson(cognition.neutralization_counts)
      !== canonicalJson(summary.summary.neutralization)
  ) invalidBoundedMetrics();
  const categories = requireRecord(cognition.category_counts, "Cognition category counts");
  if (
    !hasExactKeys(categories, ["schema_version", ...boundedCategoryFamilies.map(([key]) => key)])
    || categories.schema_version !== 2
  ) invalidBoundedMetrics();
  for (const [family, values] of boundedCategoryFamilies) {
    const counts = requireRecord(categories[family], "Cognition category counts");
    if (!hasExactKeys(counts, values)) invalidBoundedMetrics();
    const total = values.reduce((sum, key) => (
      sum + requireBoundedInteger(counts[key], 0, 60, "Cognition category count")
    ), 0);
    if (total !== summary.summary.coverage.selected_request_count) invalidBoundedMetrics();
  }
  const coverage = summary.summary.coverage;
  validateBoundedQuotient(
    cognition.funded_coverage,
    ["schema_version", "name", "numerator", "denominator", "value", "source_artifacts"],
    "funded_coverage",
    (receipt) => receipt.numerator === coverage.funded_request_count
      && receipt.denominator === coverage.eligible_response_count
      && sameStrings(receipt.source_artifacts, ["outputs/cognition-summary.json"]),
  );
  for (const name of ["mean_applied_sentiment", "mean_applied_recall"]) {
    validateBoundedQuotient(
      cognition[name],
      [...responseMetricReceiptKeys],
      name,
      (receipt) => receipt.denominator === coverage.eligible_response_count
        && sameStrings(receipt.source_event_types, ["spatial.applied-response"])
        && sameStrings(receipt.source_artifacts, ["outputs/spatial-responses.jsonl"]),
    );
  }
  const grid = populationSize * campaignCount;
  validateBoundedStateReceipt(cognition.synthetic_sentiment, "synthetic_sentiment", grid);
  validateBoundedStateReceipt(cognition.synthetic_recall, "synthetic_recall", grid);
  return cognition;
}

function validateBoundedResponseMetrics(documentValue, seedToken) {
  const metrics = requireRecord(documentValue, "Spatial response metrics");
  if (
    !hasExactKeys(metrics, boundedMetricsKeys)
    || metrics.schema_version !== 2
    || metrics.model_id !== "spatial-response-metrics-v2"
    || metrics.claim_scope !== "synthetic-dual-state-metrics-not-observed-outcomes"
    || metrics.source_run_schema_version !== boundedRunSchemaVersion
    || metrics.source_run_schema_version !== state.meta.run_schema_version
    || metrics.opportunity_model_id !== "spatial-opportunity-v1"
    || metrics.attention_model_id !== "spatial-attention-v1"
    || metrics.response_model_id !== boundedResponseModelId
    || boundedMetricsHashFields.some((field) => !validEvidenceHash(metrics[field]))
    || metrics.cognition_input_sha256 !== state.responseSummary.cognition_input_sha256
    || metrics.influence_policy_sha256 !== state.responseSummary.influence_policy_sha256
    || metrics.response_input_sha256 !== state.responseSummary.response_input_sha256
    || metrics.city_sha256 !== state.meta.city_sha256
  ) invalidBoundedMetrics();
  validateSpatialResponseSeed(metrics.seed, seedToken);
  if (seedToken !== state.metaSeedToken) throw new Error("Spatial response seed is inconsistent");
  const populationSize = requireBoundedInteger(
    metrics.population_size, 1, 30, "Spatial response population",
  );
  requireBoundedInteger(metrics.days, 1, 7, "Spatial response days");
  const campaignCount = requireBoundedInteger(
    metrics.campaign_count, 1, 20, "Spatial response campaign count",
  );
  const rule = validateRuleBaselineMetrics(metrics.rule_baseline, populationSize, campaignCount);
  const cognition = validateBoundedCognitionMetrics(
    metrics.provider_dependent_cognition, populationSize, campaignCount,
  );
  if (
    cognition.coverage.eligible_response_count !== rule.overall.response_count.numerator
    || cognition.synthetic_sentiment.initial_total !== rule.overall.brand_sentiment.initial_total
    || cognition.synthetic_recall.initial_total !== rule.overall.recall_strength.initial_total
  ) invalidBoundedMetrics();
  return { metrics, rule, cognition };
}

function boundedMetricRow(body, key, label, value, receipt) {
  const row = document.createElement("tr");
  row.dataset.metric = key;
  const heading = createTextElement("th", "", label);
  heading.scope = "row";
  const cell = document.createElement("td");
  cell.tabIndex = 0;
  cell.append(createTextElement("strong", "", value), createTextElement("small", "", receipt));
  cell.setAttribute("aria-label", `${label}: ${value}. ${receipt}.`);
  row.append(heading, cell);
  body.append(row);
}

function renderBoundedResponseMetrics() {
  const { rule, cognition } = validateBoundedResponseMetrics(
    state.spatialResponseMetrics,
    state.spatialResponseSeedToken,
  );
  renderResponseMetricTables(rule);
  byId("response-metrics-claim").textContent = "Rule baseline · modeled response metrics · not observed outcomes";
  byId("response-metrics-event-scope").textContent = "Rule baseline · full-run event totals";
  let section = byId("bounded-cognition-metrics");
  if (!section) {
    section = document.createElement("section");
    section.className = "bounded-cognition-metrics";
    section.id = "bounded-cognition-metrics";
    section.setAttribute("aria-labelledby", "bounded-cognition-metrics-title");
    byId("response-metrics-panel").append(section);
  }
  const title = createTextElement("h3", "", "Provider-dependent cognition · exploratory");
  title.id = "bounded-cognition-metrics-title";
  const claim = createTextElement(
    "p",
    "bounded-metrics-claim",
    "Synthetic cognition-track measurements · not a campaign effect · no purchase quantity",
  );
  const table = document.createElement("table");
  table.className = "bounded-metrics-table";
  const caption = createTextElement(
    "caption",
    "",
    "Exploratory cognition-track receipts with numerator and denominator",
  );
  const body = document.createElement("tbody");
  const funded = cognition.funded_coverage;
  boundedMetricRow(
    body, "funded-coverage", "Funded coverage",
    `${Number((funded.value * 100).toFixed(1))}%`,
    `${funded.numerator} funded / ${funded.denominator} eligible`,
  );
  for (const [key, label] of [
    ["mean_applied_sentiment", "Mean applied sentiment Δ"],
    ["mean_applied_recall", "Mean applied recall Δ"],
  ]) {
    const receipt = cognition[key];
    boundedMetricRow(
      body, key.replaceAll("_", "-"), label,
      signedProxyText(receipt.value, label),
      `${compactReceiptNumber(receipt.numerator)} / ${receipt.denominator} applied responses`,
    );
  }
  for (const [key, label] of [
    ["synthetic_sentiment", "Synthetic sentiment"],
    ["synthetic_recall", "Synthetic recall"],
  ]) {
    const receipt = cognition[key];
    boundedMetricRow(
      body, key.replaceAll("_", "-"), label,
      `${proxyText(receipt.initial_mean, label)} → ${proxyText(receipt.final_mean, label)}`,
      `change ${signedProxyText(receipt.mean_change, label)} · n = ${receipt.denominator}`,
    );
  }
  table.append(caption, body);
  const selected = cognition.coverage.selected_request_count;
  const categoriesNote = createTextElement(
    "p",
    "bounded-category-note",
    `Recorded categories across all ${selected} selected ${selected === 1 ? "request" : "requests"}, including the fixed neutral vector of rules, fallback, and budget-skipped requests and answers later set aside as uncertain or safety-flagged.`,
  );
  const categories = document.createElement("dl");
  categories.className = "bounded-category-counts";
  for (const [family, values] of boundedCategoryFamilies) {
    const row = document.createElement("div");
    row.dataset.family = family;
    const counts = cognition.category_counts[family];
    row.append(
      createTextElement("dt", "", family[0].toUpperCase() + family.slice(1)),
      createTextElement(
        "dd",
        "",
        values.filter((key) => counts[key] > 0).map((key) => `${key} ${counts[key]}`).join(" · ")
          || "none selected",
      ),
    );
    categories.append(row);
  }
  section.replaceChildren(title, claim, table, categoriesNote, categories);
}

function evidencePageUrl(kind, minute, offset) {
  const endpoint = evidencePageContracts[kind].endpoint;
  return `${endpoint}?minute=${minute}&offset=${offset}&limit=${EVIDENCE_PAGE_SIZE}`;
}

async function loadEvidencePage(kind, offset) {
  if (state.timelineLoading) return;
  const contract = evidencePageContracts[kind];
  const committedPage = state[contract.pageKey];
  if (!committedPage || !Number.isInteger(offset) || offset < 0) return;
  stopPlayback();
  const timelineRequest = state.request;
  const minute = state.minute;
  const pageRequest = ++state.evidenceRequests[kind];
  const errorScope = `evidence:${kind}`;
  setEvidencePaginationBusy(kind, true);
  try {
    const page = validateEvidencePage(
      kind,
      await fetchJson(evidencePageUrl(kind, minute, offset)),
      minute,
      offset,
    );
    if (
      timelineRequest !== state.request
      || pageRequest !== state.evidenceRequests[kind]
      || minute !== state.minute
    ) return;
    const committedSelection = state.selectedEvidence;
    const selectionLeftPage = committedSelection !== null
      && committedSelection.kind === kind
      && !page.items.some(
        (item) => selectedEvidenceIdentity(kind, item).id === committedSelection.id,
      );
    state[contract.pageKey] = page;
    if (selectionLeftPage) state.selectedEvidence = null;
    try {
      renderOpportunityEvidence();
      renderAttentionEvidence();
      renderResponseEvidence();
      renderMap();
      renderMapTextAlternative();
    } catch (error) {
      state[contract.pageKey] = committedPage;
      state.selectedEvidence = committedSelection;
      try {
        renderOpportunityEvidence();
        renderAttentionEvidence();
        renderResponseEvidence();
        renderMap();
        renderMapTextAlternative();
      } catch (_) {
        // Preserve the original render error; the committed state and ledger DOM remain usable.
      }
      throw error;
    }
    clearError(errorScope);
    const selectionUpdate = selectionLeftPage
      ? " The previous evidence selection was cleared because it is not on this page."
      : "";
    announceTimeline(
      `${evidenceLabel(kind)}. ${byId(`${kind}-page-note`).textContent}.${selectionUpdate}`,
    );
  } catch (error) {
    if (
      timelineRequest !== state.request
      || pageRequest !== state.evidenceRequests[kind]
      || minute !== state.minute
    ) return;
    showError(String(error), errorScope);
    setEvidencePaginationBusy(kind, false);
  }
}

function navigateEvidencePage(kind, direction) {
  const page = state[evidencePageContracts[kind].pageKey];
  if (!page) return;
  const offset = direction === "next"
    ? page.next_offset
    : Math.max(0, page.offset - EVIDENCE_PAGE_SIZE);
  if (offset === null || offset === page.offset) return;
  loadEvidencePage(kind, offset);
}

function renderTimeline() {
  updateLabels();
  renderPeople();
  renderSelected();
  renderOpportunityEvidence();
  renderAttentionEvidence();
  renderResponseEvidence();
  renderResponseState();
  renderMap();
  renderMapTextAlternative();
}

async function setMinute(
  minute,
  selectedAgent = state.selected,
  playbackGeneration = null,
  announceCommittedFrame = true,
) {
  if (!state.meta) return false;
  if (playbackGeneration === null) stopPlayback();
  else if (!state.playing || playbackGeneration !== state.playbackGeneration) return false;
  if (!state.agents.some((agent) => agent.agent_id === selectedAgent)) {
    showError("Could not select an unknown simulated person");
    return false;
  }
  const next = Math.max(0, Math.min(state.meta.days * 1440 - 1, Math.floor(minute)));
  const request = ++state.request;
  const abortController = new AbortController();
  state.timelineAbortController = abortController;
  const sameMinute = state.frame !== null && next === state.minute;
  const offsets = {};
  state.timelineLoading = true;
  for (const kind of evidencePageKinds) {
    const page = state[evidencePageContracts[kind].pageKey];
    offsets[kind] = sameMinute && page ? page.offset : 0;
    state.evidenceRequests[kind] += 1;
    setEvidencePaginationBusy(kind, true);
  }
  updateLabels();
  try {
    let frame;
    let opportunityPage;
    let attentionPage;
    let responsePage;
    let responseStatePage;
    if (completeTimelineBatchAvailable()) {
      ({ frame, opportunityPage, attentionPage, responsePage, responseStatePage } = (
        validateTimelineBatch(
          await fetchJson(
            timelineBatchUrl(next, selectedAgent, offsets),
            abortController.signal,
          ),
          next,
          selectedAgent,
          offsets,
        )
      ));
    } else {
      const requests = [fetchJson(
        `/frame?minute=${next}&agent_id=${encodeURIComponent(selectedAgent)}`,
        abortController.signal,
      )];
      const opportunityIndex = state.opportunitySummary ? requests.length : null;
      if (opportunityIndex !== null) requests.push(fetchJson(
        evidencePageUrl("opportunity", next, offsets.opportunity),
        abortController.signal,
      ));
      const attentionIndex = state.attentionSummary ? requests.length : null;
      if (attentionIndex !== null) requests.push(fetchJson(
        evidencePageUrl("attention", next, offsets.attention),
        abortController.signal,
      ));
      const responseIndex = state.responseSummary ? requests.length : null;
      if (responseIndex !== null) requests.push(fetchJson(
        evidencePageUrl("response", next, offsets.response),
        abortController.signal,
      ));
      const responseStateIndex = state.responseSummary ? requests.length : null;
      if (responseStateIndex !== null) requests.push(fetchJson(
        `/response-state?agent_id=${encodeURIComponent(selectedAgent)}`,
        abortController.signal,
      ));
      const responses = await Promise.all(requests);
      frame = validateTimelineFrame(responses[0], next, selectedAgent);
      opportunityPage = opportunityIndex === null
        ? null
        : validateEvidencePage(
          "opportunity", responses[opportunityIndex], next, offsets.opportunity,
        );
      attentionPage = attentionIndex === null
        ? null
        : validateEvidencePage(
          "attention", responses[attentionIndex], next, offsets.attention,
        );
      responsePage = responseIndex === null
        ? null
        : validateEvidencePage("response", responses[responseIndex], next, offsets.response);
      responseStatePage = responseStateIndex === null
        ? null
        : validateResponseStatePage(responses[responseStateIndex], selectedAgent, 0);
    }
    if (
      request !== state.request
      || state.timelineAbortController !== abortController
      || (
        playbackGeneration !== null
        && (!state.playing || playbackGeneration !== state.playbackGeneration)
      )
    ) return false;
    state.timelineLoading = false;
    const committedTimeline = {
      frame: state.frame,
      opportunityPage: state.opportunityPage,
      attentionPage: state.attentionPage,
      responsePage: state.responsePage,
      responseStatePage: state.responseStatePage,
      minute: state.minute,
      selected: state.selected,
    };
    state.frame = frame;
    state.opportunityPage = opportunityPage;
    state.attentionPage = attentionPage;
    state.responsePage = responsePage;
    state.responseStatePage = responseStatePage;
    state.minute = next;
    state.selected = selectedAgent;
    try {
      renderTimeline();
    } catch (error) {
      Object.assign(state, committedTimeline);
      try {
        renderTimeline();
      } catch (_) {
        // Preserve the original render error after restoring the last committed timeline state.
      }
      throw error;
    }
    state.timelineAbortController = null;
    clearError("timeline");
    if (!state.playing) {
      updatePlaybackControl(
        state.minute >= state.meta.days * 1440 - 1
          ? "END OF RUN · PRESS PLAY TO REPLAY"
          : "PAUSED",
      );
    }
    if (playbackGeneration === null && announceCommittedFrame) {
      announceTimeline(byId("map-text-summary").textContent);
    }
    return true;
  } catch (error) {
    const current = request === state.request
      && state.timelineAbortController === abortController;
    if (current) state.timelineAbortController = null;
    if (
      !current
      || (error instanceof Error && error.name === "AbortError")
      || (
        playbackGeneration !== null
        && (!state.playing || playbackGeneration !== state.playbackGeneration)
      )
    ) return false;
    state.timelineLoading = false;
    for (const kind of evidencePageKinds) setEvidencePaginationBusy(kind, false);
    updateLabels();
    showError(String(error), "timeline");
    stopPlayback("STOPPED · LAST COMMITTED FRAME RETAINED");
    return false;
  }
}

function invalidWorkbenchInput() {
  throw new Error("Frozen workbench input is invalid");
}

function requireWorkbenchText(value, minimum, maximum) {
  if (
    typeof value !== "string"
    || Array.from(value).length < minimum
    || Array.from(value).length > maximum
    || /[\u0000-\u001f\u007f-\u009f]/.test(value)
  ) invalidWorkbenchInput();
  return value;
}

function requireWorkbenchId(value, maximum = 80) {
  if (
    typeof value !== "string"
    || value.length > maximum
    || !/^[a-z0-9][a-z0-9-]*$/.test(value)
  ) invalidWorkbenchInput();
  return value;
}

function validateWorkbenchStringList(value, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    invalidWorkbenchInput();
  }
  const items = value.map((item) => requireWorkbenchText(item, 1, 80));
  if (new Set(items).size !== items.length) invalidWorkbenchInput();
  return items;
}

function validateWorkbenchWindow(value, days) {
  const windowValue = requireRecord(value, "Frozen workbench input");
  if (!hasExactKeys(windowValue, ["start_minute", "end_minute"])) invalidWorkbenchInput();
  const maximum = days * 1440;
  const start = requireBoundedInteger(
    windowValue.start_minute,
    0,
    maximum - 1,
    "Frozen workbench input",
  );
  const end = requireBoundedInteger(
    windowValue.end_minute,
    1,
    maximum,
    "Frozen workbench input",
  );
  if (end <= start || Math.floor(start / 1440) !== Math.floor((end - 1) / 1440)) {
    invalidWorkbenchInput();
  }
  return windowValue;
}

function validateWorkbenchWindows(value, days) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 14) {
    invalidWorkbenchInput();
  }
  const windows = value.map((windowValue) => validateWorkbenchWindow(windowValue, days));
  for (let index = 1; index < windows.length; index += 1) {
    if (
      windows[index].start_minute < windows[index - 1].end_minute
      || windows[index].start_minute < windows[index - 1].start_minute
    ) invalidWorkbenchInput();
  }
  return windows;
}

function validateWorkbenchPhone(value, days) {
  if (value === null) return null;
  const phone = requireRecord(value, "Frozen workbench input");
  if (!hasExactKeys(phone, [
    "active_windows",
    "frequency_cap_per_agent_per_day",
    "placement_id",
    "eligible_activities",
    "opportunity_probability_per_minute",
  ])) invalidWorkbenchInput();
  if (phone.placement_id !== "phone-placement") invalidWorkbenchInput();
  validateWorkbenchWindows(phone.active_windows, days);
  requireBoundedInteger(
    phone.frequency_cap_per_agent_per_day,
    1,
    100,
    "Frozen workbench input",
  );
  if (
    !Array.isArray(phone.eligible_activities)
    || phone.eligible_activities.length < 1
    || phone.eligible_activities.length > 4
    || new Set(phone.eligible_activities).size !== phone.eligible_activities.length
    || phone.eligible_activities.some(
      (activity) => !["home", "commute", "work", "leisure"].includes(activity),
    )
  ) invalidWorkbenchInput();
  requireBoundedNumber(
    phone.opportunity_probability_per_minute,
    0,
    1,
    "Frozen workbench input",
  );
  return phone;
}

function validateWorkbenchRoadside(value, days) {
  if (value === null) return null;
  const roadside = requireRecord(value, "Frozen workbench input");
  if (!hasExactKeys(roadside, [
    "active_windows",
    "frequency_cap_per_agent_per_day",
    "placement_id",
    "road_id",
    "travel_direction",
    "road_fraction",
    "side",
    "orientation_degrees",
    "max_view_distance_meters",
  ])) invalidWorkbenchInput();
  if (
    roadside.placement_id !== "roadside-placement"
    || !["forward", "backward"].includes(roadside.travel_direction)
    || !["left", "right"].includes(roadside.side)
  ) invalidWorkbenchInput();
  requireWorkbenchId(roadside.road_id);
  validateWorkbenchWindows(roadside.active_windows, days);
  requireBoundedInteger(
    roadside.frequency_cap_per_agent_per_day,
    1,
    100,
    "Frozen workbench input",
  );
  const roadFraction = requireBoundedNumber(
    roadside.road_fraction,
    0,
    1,
    "Frozen workbench input",
  );
  if (roadFraction === 0 || roadFraction === 1) invalidWorkbenchInput();
  requireBoundedNumber(
    roadside.orientation_degrees,
    0,
    359.99999999999994,
    "Frozen workbench input",
  );
  const distance = requireBoundedNumber(
    roadside.max_view_distance_meters,
    0,
    1000,
    "Frozen workbench input",
  );
  if (distance === 0) invalidWorkbenchInput();
  return roadside;
}

function validateWorkbenchCognition(value) {
  const cognition = requireRecord(value, "Frozen workbench input");
  const keys = [
    "mode", "provider_profile_id", "provider_revision_sha256",
    "aggregate_output_token_ceiling", "prompt_contract_version", "result_schema_version",
    "selection_policy_version", "fallback_policy_version", "selected_requests_per_agent",
    "selected_requests_per_run", "concurrency_limit", "max_request_bytes",
    "max_wire_response_bytes", "max_output_tokens_per_attempt",
    "reservation_tokens_per_request", "initial_attempts", "transient_retries",
    "repair_attempts", "attempt_timeout_seconds", "aggregate_deadline_seconds",
  ];
  const fixedIntegers = {
    prompt_contract_version: 1,
    result_schema_version: 1,
    selection_policy_version: 1,
    fallback_policy_version: 1,
    selected_requests_per_agent: 4,
    selected_requests_per_run: 60,
    concurrency_limit: 4,
    max_request_bytes: 8192,
    max_wire_response_bytes: 1048576,
    max_output_tokens_per_attempt: 384,
    reservation_tokens_per_request: 1536,
    initial_attempts: 1,
    transient_retries: 2,
    repair_attempts: 1,
    attempt_timeout_seconds: 30,
    aggregate_deadline_seconds: 180,
  };
  if (
    !hasExactKeys(cognition, keys)
    || !["rules", "mock", "local", "remote"].includes(cognition.mode)
    || Object.entries(fixedIntegers).some(([key, expected]) => cognition[key] !== expected)
    || !Number.isInteger(cognition.aggregate_output_token_ceiling)
    || cognition.aggregate_output_token_ceiling < 0
    || cognition.aggregate_output_token_ceiling > 92160
  ) invalidWorkbenchInput();
  const offline = ["rules", "mock"].includes(cognition.mode);
  if (
    offline
      ? cognition.provider_profile_id !== null
        || cognition.provider_revision_sha256 !== null
        || cognition.aggregate_output_token_ceiling !== 0
      : !validEvidenceId(cognition.provider_profile_id)
        || cognition.provider_profile_id.length > 64
        || !validEvidenceHash(cognition.provider_revision_sha256)
        || cognition.aggregate_output_token_ceiling % 1536 !== 0
  ) invalidWorkbenchInput();
  return cognition;
}

function validateBoundedWorkbenchCognition(value) {
  const cognition = requireRecord(value, "Frozen workbench input");
  const fixedIntegers = {
    request_schema_version: 2,
    selected_requests_per_agent: 4,
    selected_requests_per_run: 60,
    concurrency_limit: 4,
    max_request_bytes: 8192,
    max_terminal_record_bytes: 8192,
    max_wire_response_bytes: 1048576,
    initial_attempts: 1,
    transient_retries: 2,
    repair_attempts: 1,
    attempt_timeout_seconds: 30,
    aggregate_deadline_seconds: 180,
  };
  if (
    !hasExactKeys(cognition, boundedWorkbenchCognitionKeys)
    || cognition.effect_scope !== boundedEffectScope
    || !["rules", "mock", "local", "remote"].includes(cognition.mode)
    || ![10, 20, 40, 60].includes(cognition.funded_response_limit)
    || Object.entries(fixedIntegers).some(([key, expected]) => cognition[key] !== expected)
    || cognition.response_model !== boundedResponseModelId
    || cognition.rule_state_noninterference !== true
    || !validEvidenceHash(cognition.causal_contract_sha256)
  ) invalidWorkbenchInput();
  // The output budget is the run's own frozen authority, never a browser constant: one
  // attempt's cap, times every allowed attempt, reserves a whole funded request.
  const attemptTokens = requireBoundedInteger(
    cognition.max_output_tokens_per_attempt, 1, 65536, "Frozen workbench input",
  );
  const attemptsPerRequest = cognition.initial_attempts + cognition.transient_retries
    + cognition.repair_attempts;
  if (cognition.reservation_tokens_per_request !== attemptTokens * attemptsPerRequest) {
    invalidWorkbenchInput();
  }
  const offline = ["rules", "mock"].includes(cognition.mode);
  if (
    offline
      ? cognition.provider_profile_id !== null
        || cognition.provider_revision_sha256 !== null
        || cognition.funded_response_limit !== 60
        || cognition.aggregate_output_token_ceiling !== 0
      : !validEvidenceId(cognition.provider_profile_id)
        || cognition.provider_profile_id.length > 64
        || !validEvidenceHash(cognition.provider_revision_sha256)
        || cognition.aggregate_output_token_ceiling
          !== cognition.funded_response_limit * cognition.reservation_tokens_per_request
  ) invalidWorkbenchInput();
  const contract = requireRecord(cognition.causal_contract, "Frozen workbench input");
  if (
    !hasExactKeys(contract, boundedCausalContractKeys)
    || contract.schema_version !== 2
    || contract.disclosure !== boundedClaimScope
    || contract.prompt_contract_id !== "city-cognition-signal-prompt-v2"
    || contract.prompt_contract_version !== 2
    || contract.result_schema_id !== "city-cognition-signal-result-v2"
    || contract.result_schema_version !== 2
    || contract.selection_policy_version !== 2
    || contract.fallback_policy_version !== 2
    || contract.mock_policy_version !== 1
    || contract.influence_policy_version !== 1
    || typeof contract.prompt_contract_text !== "string"
    || typeof contract.result_schema_document !== "string"
    || [
      "prompt_contract_sha256", "result_schema_contract_sha256", "selection_policy_sha256",
      "fallback_policy_sha256", "mock_policy_sha256", "influence_policy_sha256",
    ].some((field) => !validEvidenceHash(contract[field]))
    || [
      "selection_policy", "fallback_policy", "mock_policy", "influence_policy",
    ].some((field) => !contract[field] || typeof contract[field] !== "object")
    || contract.influence_policy.policy_id !== "city-cognition-influence-v1"
  ) invalidWorkbenchInput();
  return cognition;
}

function validateWorkbenchInput(value) {
  const documentValue = requireRecord(value, "Frozen workbench input");
  // Exact V10 branch: schema-4 bounded cognition input, never a relaxed version union.
  const isBoundedRun = documentValue.run_schema_version === boundedRunSchemaVersion;
  const isPersonaRun = [8, 9].includes(documentValue.run_schema_version) || isBoundedRun;
  const isCognitionRun = documentValue.run_schema_version === 9;
  if (!hasExactKeys(documentValue, [
    "run_id",
    "run_schema_version",
    "workbench_input_schema_version",
    "manifest_sha256",
    "workbench_input_sha256",
    "city_sha256",
    "scenario_sha256",
    "creative_sha256",
    "scenario",
    isPersonaRun ? "population" : "cohort",
    "settings",
    ...(isCognitionRun || isBoundedRun ? ["cognition"] : []),
    "creative_template",
  ])) invalidWorkbenchInput();
  const settings = requireRecord(documentValue.settings, "Frozen workbench input");
  if (!hasExactKeys(settings, [
    "run_id", "agent_count", "days", "seed", "response_mode",
  ])) invalidWorkbenchInput();
  const runId = requireWorkbenchId(documentValue.run_id, 40);
  const expectedInputSchema = isBoundedRun ? 4 : isCognitionRun ? 3 : isPersonaRun ? 2 : 1;
  if (
    (![7, 8, 9].includes(documentValue.run_schema_version) && !isBoundedRun)
    || documentValue.run_schema_version !== state.meta.run_schema_version
    || documentValue.workbench_input_schema_version !== expectedInputSchema
    || runId !== settings.run_id
    || runId !== state.meta.run_id
    || settings.response_mode !== "deterministic-rules"
    || settings.agent_count !== state.meta.agent_count
    || settings.days !== state.meta.days
  ) invalidWorkbenchInput();
  if (
    typeof settings.seed !== "string"
    || !/^(?:0|[1-9][0-9]{0,18})$/.test(settings.seed)
    || BigInt(settings.seed) > 9223372036854775807n
    || settings.seed !== state.metaSeedToken
  ) invalidWorkbenchInput();
  requireBoundedInteger(settings.agent_count, 1, 30, "Frozen workbench input");
  const days = requireBoundedInteger(settings.days, 1, 7, "Frozen workbench input");
  for (const field of [
    "manifest_sha256",
    "workbench_input_sha256",
    "city_sha256",
    "scenario_sha256",
    "creative_sha256",
  ]) {
    if (!validEvidenceHash(documentValue[field])) invalidWorkbenchInput();
  }
  if (
    documentValue.workbench_input_sha256 !== state.meta.workbench_input_sha256
    || documentValue.city_sha256 !== state.meta.city_sha256
  ) invalidWorkbenchInput();

  const scenario = requireRecord(documentValue.scenario, "Frozen workbench input");
  if (!hasExactKeys(scenario, ["scenario_id", "name", "campaign", "phone", "roadside"])) {
    invalidWorkbenchInput();
  }
  requireWorkbenchId(scenario.scenario_id);
  requireWorkbenchText(scenario.name, 1, 120);
  const campaign = requireRecord(scenario.campaign, "Frozen workbench input");
  if (!hasExactKeys(campaign, [
    "campaign_id", "name", "creative_template_id", "target_interests", "relative_price",
  ])) invalidWorkbenchInput();
  requireWorkbenchId(campaign.campaign_id);
  requireWorkbenchText(campaign.name, 1, 120);
  requireWorkbenchId(campaign.creative_template_id);
  validateWorkbenchStringList(campaign.target_interests, 1, 12);
  const relativePrice = requireBoundedNumber(
    campaign.relative_price,
    0,
    100,
    "Frozen workbench input",
  );
  if (relativePrice === 0) invalidWorkbenchInput();
  const phone = validateWorkbenchPhone(scenario.phone, days);
  const roadside = validateWorkbenchRoadside(scenario.roadside, days);
  if (phone === null && roadside === null) invalidWorkbenchInput();

  if (isPersonaRun) validateWorkbenchPopulation(documentValue.population, settings.agent_count);
  else validateWorkbenchCohort(documentValue.cohort);
  if (isCognitionRun) validateWorkbenchCognition(documentValue.cognition);
  if (isBoundedRun) validateBoundedWorkbenchCognition(documentValue.cognition);

  const creative = requireRecord(documentValue.creative_template, "Frozen workbench input");
  if (!hasExactKeys(creative, [
    "template_id",
    "template_version",
    "product_name",
    "product_category",
    "message",
    "call_to_action",
    "disclosure",
  ])) invalidWorkbenchInput();
  if (
    creative.template_version !== 1
    || creative.template_id !== campaign.creative_template_id
    || creative.disclosure !== "Fictional creative for synthetic simulation only."
  ) invalidWorkbenchInput();
  requireWorkbenchId(creative.template_id);
  requireWorkbenchText(creative.product_name, 1, 80);
  requireWorkbenchText(creative.product_category, 1, 80);
  requireWorkbenchText(creative.message, 1, 240);
  requireWorkbenchText(creative.call_to_action, 1, 120);
  requireWorkbenchText(creative.disclosure, 1, 80);
  return documentValue;
}

function validateWorkbenchCohort(value) {
  const cohort = requireRecord(value, "Frozen workbench input");
  if (!hasExactKeys(cohort, ["interests", "traits", "initial_state"])) invalidWorkbenchInput();
  validateWorkbenchStringList(cohort.interests, 1, 12);
  const traits = requireRecord(cohort.traits, "Frozen workbench input");
  const traitKeys = [
    "price_sensitivity",
    "novelty_seeking",
    "advertising_skepticism",
    "mobile_recall_encoding",
    "roadside_recall_encoding",
    "impulsivity",
  ];
  if (!hasExactKeys(traits, traitKeys)) invalidWorkbenchInput();
  for (const key of traitKeys) {
    requireBoundedNumber(traits[key], 0, 1, "Frozen workbench input");
  }
  const initialState = requireRecord(cohort.initial_state, "Frozen workbench input");
  if (!hasExactKeys(initialState, [
    "brand_sentiment", "recall_strength", "purchase_intention",
  ])) invalidWorkbenchInput();
  requireBoundedNumber(initialState.brand_sentiment, -1, 1, "Frozen workbench input");
  requireBoundedNumber(initialState.recall_strength, 0, 1, "Frozen workbench input");
  requireBoundedNumber(initialState.purchase_intention, 0, 1, "Frozen workbench input");
}

function requirePersonaText(value, maximum) {
  requireWorkbenchText(value, 1, maximum);
  if (
    value.trim().length === 0
    || /[\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]/u.test(value)
  ) invalidWorkbenchInput();
}

function validateWorkbenchPopulation(value, agentCount) {
  const population = requireRecord(value, "Frozen workbench input");
  if (
    !hasExactKeys(population, ["personas", "assignments"])
    || !Array.isArray(population.personas)
    || population.personas.length < 1 || population.personas.length > 30
    || !Array.isArray(population.assignments)
    || population.assignments.length !== agentCount
    || state.agents.length !== agentCount
  ) invalidWorkbenchInput();
  const personas = new Map();
  const mbtiLabels = new Set([
    "INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP",
  ]);
  for (const item of population.personas) {
    const persona = requireRecord(item, "Frozen workbench input");
    if (
      !hasExactKeys(persona, [
        "persona_id", "count", "fictional", "display_name", "age", "occupation",
        "background", "mbti", "interests", "traits", "initial_state",
      ])
      || typeof persona.persona_id !== "string"
      || !/^persona-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})$/.test(persona.persona_id)
      || personas.has(persona.persona_id)
      || persona.fictional !== true
    ) invalidWorkbenchInput();
    requireBoundedInteger(persona.count, 1, 30, "Frozen workbench input");
    requirePersonaText(persona.display_name, 80);
    if (persona.age !== null) requireBoundedInteger(persona.age, 18, 65, "Frozen workbench input");
    if (persona.occupation !== null) requirePersonaText(persona.occupation, 80);
    if (persona.background !== null) requirePersonaText(persona.background, 800);
    if (persona.mbti !== null && !mbtiLabels.has(persona.mbti)) invalidWorkbenchInput();
    validateWorkbenchCohort({
      interests: persona.interests, traits: persona.traits, initial_state: persona.initial_state,
    });
    for (const interest of persona.interests) requirePersonaText(interest, 80);
    // Canonical byte limits are server-verified. JSON.stringify uses different numeric
    // formatting and cannot authoritatively recheck the persisted Python byte bound.
    personas.set(persona.persona_id, persona);
  }
  const expectedIds = Array.from({ length: agentCount }, (_, index) => (
    `person-${String(index + 1).padStart(3, "0")}`
  ));
  const agentIds = state.agents.map((agent) => agent.agent_id);
  if (!sameStrings([...agentIds].sort(), expectedIds)) invalidWorkbenchInput();
  const assignments = new Map();
  for (const item of population.assignments) {
    const assignment = requireRecord(item, "Frozen workbench input");
    if (
      !hasExactKeys(assignment, ["agent_id", "persona_id"])
      || !expectedIds.includes(assignment.agent_id)
      || assignments.has(assignment.agent_id)
      || !personas.has(assignment.persona_id)
    ) invalidWorkbenchInput();
    assignments.set(assignment.agent_id, assignment.persona_id);
  }
  const identities = new Map();
  let index = 0;
  for (const personaId of [...personas.keys()].sort()) {
    const persona = personas.get(personaId);
    for (let count = 0; count < persona.count; count += 1) {
      const agentId = expectedIds[index++];
      if (agentId === undefined || assignments.get(agentId) !== personaId) invalidWorkbenchInput();
      identities.set(agentId, persona);
    }
  }
  if (index !== agentCount) invalidWorkbenchInput();
  return identities;
}

function validateWorkbenchSummaryBinding(summaryValue, input) {
  const summary = requireRecord(summaryValue, "Frozen workbench input");
  const scenario = input.scenario;
  if (
    summary.scenario_id !== scenario.scenario_id
    || summary.scenario_name !== scenario.name
    || summary.scenario_sha256 !== input.scenario_sha256
    || !Array.isArray(summary.campaigns)
    || summary.campaigns.length !== 1
  ) invalidWorkbenchInput();
  const summaryCampaign = requireRecord(summary.campaigns[0], "Frozen workbench input");
  if (
    !hasExactKeys(summaryCampaign, ["campaign_id", "name", "creative_sha256"])
    || summaryCampaign.campaign_id !== scenario.campaign.campaign_id
    || summaryCampaign.name !== scenario.campaign.name
    || summaryCampaign.creative_sha256 !== input.creative_sha256
  ) invalidWorkbenchInput();
}

function workbenchClock(minute, endMinute = false) {
  const withinDay = minute % 1440;
  if (endMinute && withinDay === 0 && minute > 0) return "24:00";
  return `${String(Math.floor(withinDay / 60)).padStart(2, "0")}:${String(withinDay % 60).padStart(2, "0")}`;
}

function workbenchWindowText(windowValue) {
  const day = Math.floor(windowValue.start_minute / 1440) + 1;
  return `DAY ${String(day).padStart(2, "0")} · ${workbenchClock(windowValue.start_minute)}–${workbenchClock(windowValue.end_minute, true)}`;
}

function appendWorkbenchPlacement(label, details) {
  const card = document.createElement("article");
  card.className = "assumption-placement";
  const title = document.createElement("strong");
  title.textContent = label;
  const value = document.createElement("bdi");
  value.textContent = details;
  card.append(title, value);
  byId("assumptions-placements").append(card);
}

function renderWorkbenchAssumptions(input) {
  const scenario = input.scenario;
  const campaign = scenario.campaign;
  const cohort = input.cohort;
  const creative = input.creative_template;
  const settings = input.settings;
  byId("assumptions-run").textContent = input.run_id;
  byId("assumptions-scenario").textContent = scenario.name;
  byId("assumptions-scenario-id").textContent = scenario.scenario_id;
  byId("assumptions-campaign").textContent = campaign.name;
  byId("assumptions-campaign-id").textContent = campaign.campaign_id;
  byId("assumptions-target-interests").textContent = campaign.target_interests.join(" · ");
  byId("assumptions-relative-price").textContent = String(campaign.relative_price);
  byId("assumptions-creative-product").textContent = creative.product_name;
  byId("assumptions-creative-category").textContent = creative.product_category;
  byId("assumptions-creative-template-id").textContent = creative.template_id;
  byId("assumptions-creative-message").textContent = creative.message;
  byId("assumptions-creative-action").textContent = creative.call_to_action;
  byId("assumptions-creative-disclosure").textContent = creative.disclosure;
  byId("assumptions-cohort-section").hidden = !cohort;
  if (cohort) {
    byId("assumptions-cohort-interests").textContent = cohort.interests.join(" · ");
    byId("assumptions-traits").textContent = [
      ["PRICE SENSITIVITY", cohort.traits.price_sensitivity],
      ["NOVELTY SEEKING", cohort.traits.novelty_seeking],
      ["AD SKEPTICISM", cohort.traits.advertising_skepticism],
      ["MOBILE RECALL", cohort.traits.mobile_recall_encoding],
      ["ROADSIDE RECALL", cohort.traits.roadside_recall_encoding],
      ["IMPULSIVITY", cohort.traits.impulsivity],
    ].map(([label, value]) => `${label} ${value}`).join(" · ");
    byId("assumptions-initial-state").textContent = [
      `SENTIMENT ${cohort.initial_state.brand_sentiment}`,
      `RECALL ${cohort.initial_state.recall_strength}`,
      `PURCHASE INTENTION ${cohort.initial_state.purchase_intention}`,
    ].join(" · ");
  } else {
    byId("assumptions-disclosure").textContent = "Fictional personas have explicit response assumptions. Names and descriptive context do not affect simulation. This audience is not a sample of real residents.";
  }
  byId("assumptions-seed").textContent = settings.seed;
  byId("assumptions-population").textContent = `${settings.agent_count} SYNTHETIC ${settings.agent_count === 1 ? "AGENT" : "AGENTS"}`;
  byId("assumptions-days").textContent = `${settings.days} MODEL ${settings.days === 1 ? "DAY" : "DAYS"}`;
  byId("assumptions-mode").textContent = settings.response_mode.toUpperCase();
  for (const field of [
    "manifest_sha256",
    "workbench_input_sha256",
    "city_sha256",
    "scenario_sha256",
    "creative_sha256",
  ]) {
    byId(`assumptions-${field.replaceAll("_", "-")}`).textContent = input[field];
  }
  const placements = byId("assumptions-placements");
  placements.textContent = "";
  if (scenario.phone) {
    const windows = scenario.phone.active_windows.map(workbenchWindowText).join(" · ");
    appendWorkbenchPlacement(
      "PHONE",
      `${windows} · ACTIVITIES ${scenario.phone.eligible_activities.join(", ").toUpperCase()} · P ${scenario.phone.opportunity_probability_per_minute}/MIN · CAP ${scenario.phone.frequency_cap_per_agent_per_day}/AGENT/DAY`,
    );
  }
  if (scenario.roadside) {
    const windows = scenario.roadside.active_windows.map(workbenchWindowText).join(" · ");
    appendWorkbenchPlacement(
      "ROADSIDE",
      `${windows} · ROAD ${scenario.roadside.road_id.toUpperCase()} · ${scenario.roadside.travel_direction.toUpperCase()} · FRACTION ${scenario.roadside.road_fraction} · ${scenario.roadside.side.toUpperCase()} · ${scenario.roadside.orientation_degrees}° · ${scenario.roadside.max_view_distance_meters} M · CAP ${scenario.roadside.frequency_cap_per_agent_per_day}/AGENT/DAY`,
    );
  }
  byId("assumptions-content").hidden = false;
  byId("assumptions-status").textContent = "Verified";
  byId("assumptions-drawer").open = false;
  // Only the workbench inspector (/api/runs/{id}) has a workbench to return to; the
  // standalone viewer of a V10 run verifies the same input without one.
  byId("workbench-back-link").hidden = !validatedApiBase().startsWith("/api/runs/");
}

function renderWorkbenchUnavailable(meta) {
  const status = meta.saved === true && Number.isInteger(meta.run_schema_version)
    ? `UNAVAILABLE FOR SCHEMA V${meta.run_schema_version}`
    : "UNAVAILABLE FOR TEMPORARY VIEW";
  byId("assumptions-status").textContent = status;
  byId("assumptions-content").hidden = true;
  byId("assumptions-drawer").open = false;
  byId("workbench-back-link").hidden = true;
}

function renderWorkbenchFailure() {
  byId("assumptions-status").textContent = "VERIFICATION FAILED";
  byId("assumptions-content").hidden = true;
  byId("assumptions-drawer").open = false;
  byId("workbench-back-link").hidden = true;
}

function reducedMotionRequested() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function announceTimeline(message) {
  byId("timeline-announcement").textContent = message;
}

function updatePlaybackControl(message) {
  const button = byId("play-button");
  const reduced = reducedMotionRequested();
  const atEnd = state.meta !== null && state.minute >= state.meta.days * 1440 - 1;
  button.disabled = reduced || state.playbackRestarting;
  button.textContent = state.playing ? "Ⅱ" : "▶";
  button.setAttribute(
    "aria-label",
    state.playing
      ? "Pause timeline"
      : (
        state.playbackRestarting
          ? "Restarting timeline from start"
          : (atEnd ? "Replay timeline from start" : "Play timeline")
      ),
  );
  byId("playback-status").textContent = reduced
    ? "AUTOMATIC PLAYBACK OFF · reduced motion preference"
    : (state.playbackRestarting ? "RESTARTING · LOADING FIRST FRAME" : message);
}

function stopPlayback(message = "PAUSED") {
  const wasPlaying = state.playing;
  state.playing = false;
  state.playbackGeneration += 1;
  if (state.playbackDelay !== null) window.clearTimeout(state.playbackDelay);
  state.playbackDelay = null;
  if (state.timelineAbortController !== null) {
    state.timelineAbortController.abort();
    state.timelineAbortController = null;
    state.timelineLoading = false;
    for (const kind of evidencePageKinds) setEvidencePaginationBusy(kind, false);
    updateLabels();
  }
  updatePlaybackControl(message);
  if (wasPlaying) {
    if (message.startsWith("END OF RUN")) announceTimeline("Playback reached the end of the run and paused.");
    else if (message.startsWith("STOPPED")) announceTimeline("Playback stopped. The last committed frame was retained.");
    else announceTimeline("Playback paused.");
  }
}

function schedulePlaybackStep(generation) {
  if (!state.playing || generation !== state.playbackGeneration) return;
  const delay = PLAYBACK_DELAY_MILLISECONDS[state.playbackSpeed];
  state.playbackDelay = window.setTimeout(() => {
    state.playbackDelay = null;
    playbackStep(generation);
  }, delay);
}

async function playbackStep(generation) {
  if (!state.playing || generation !== state.playbackGeneration || !state.meta) return;
  const finalMinute = state.meta.days * 1440 - 1;
  if (state.minute >= finalMinute) {
    stopPlayback("END OF RUN · PRESS PLAY TO REPLAY");
    return;
  }
  const committed = await setMinute(state.minute + 1, state.selected, generation);
  if (!committed || !state.playing || generation !== state.playbackGeneration) return;
  schedulePlaybackStep(generation);
}

async function togglePlayback() {
  if (state.playbackRestarting) return;
  if (state.playing) {
    stopPlayback();
    return;
  }
  if (reducedMotionRequested() || !state.meta || state.timelineLoading) {
    updatePlaybackControl("PAUSED");
    return;
  }
  const replaying = state.minute >= state.meta.days * 1440 - 1;
  if (replaying) {
    state.playbackRestarting = true;
    updatePlaybackControl("RESTARTING · LOADING FIRST FRAME");
    const restarted = await setMinute(0);
    state.playbackRestarting = false;
    if (!restarted || reducedMotionRequested() || state.timelineLoading) {
      updatePlaybackControl("PAUSED");
      return;
    }
  }
  state.playing = true;
  state.playbackGeneration += 1;
  const generation = state.playbackGeneration;
  updatePlaybackControl(`PLAYING · ${state.playbackSpeed}×`);
  announceTimeline(
    replaying
      ? `Timeline replay restarted at ${state.playbackSpeed}× presentation speed.`
      : `Playback started at ${state.playbackSpeed}× presentation speed.`,
  );
  playbackStep(generation);
}

function updateZoomControls() {
  byId("zoom-level").textContent = `${Math.round(state.zoom * 100)}%`;
  byId("zoom-out-button").disabled = state.zoom <= MINIMUM_ZOOM;
  byId("zoom-in-button").disabled = state.zoom >= MAXIMUM_ZOOM;
}

function setZoom(zoom) {
  state.zoom = Math.max(MINIMUM_ZOOM, Math.min(MAXIMUM_ZOOM, zoom));
  updateZoomControls();
  renderMap();
}

function resetMapView() {
  state.panX = 0;
  state.panY = 0;
  setZoom(1);
}

function jumpModelDay(direction) {
  if (!state.meta || !Number.isInteger(direction) || Math.abs(direction) !== 1) return;
  const dayIndex = Math.floor(state.minute / 1440);
  const targetDay = Math.max(0, Math.min(state.meta.days - 1, dayIndex + direction));
  if (targetDay === dayIndex) return;
  setMinute(targetDay * 1440 + (state.minute % 1440));
}

function attachMapControls() {
  let drag = null;
  canvas.addEventListener("pointerdown", (event) => { drag = { x: event.clientX, y: event.clientY }; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    state.panX += event.clientX - drag.x; state.panY += event.clientY - drag.y;
    drag = { x: event.clientX, y: event.clientY }; renderMap();
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
  canvas.addEventListener("pointercancel", () => { drag = null; });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    setZoom(state.zoom * (event.deltaY < 0 ? 1.1 : .9));
  }, { passive: false });
  byId("zoom-out-button").addEventListener("click", () => setZoom(state.zoom / 1.25));
  byId("zoom-reset-button").addEventListener("click", resetMapView);
  byId("zoom-in-button").addEventListener("click", () => setZoom(state.zoom * 1.25));
  updateZoomControls();
  new ResizeObserver(renderMap).observe(stage);
}

function citySourceText(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function setCitySourceField(name, value) {
  const element = byId(`city-source-${name}`);
  const row = element.closest("div");
  if (value === null) {
    element.textContent = "";
    if (row) row.hidden = true;
    return;
  }
  element.textContent = value;
  if (row) row.hidden = false;
}

function renderCitySource(meta) {
  const source = meta.source && typeof meta.source === "object" && !Array.isArray(meta.source)
    ? meta.source
    : {};
  const bounds = meta.bounds && typeof meta.bounds === "object" && !Array.isArray(meta.bounds)
    ? meta.bounds
    : null;
  const boundValues = bounds
    ? [bounds.west, bounds.south, bounds.east, bounds.north]
    : [];
  const boundsText = boundValues.length === 4 && boundValues.every(Number.isFinite)
    ? `W ${boundValues[0]} · S ${boundValues[1]} · E ${boundValues[2]} · N ${boundValues[3]}`
    : null;
  const omissions = Array.isArray(meta.known_omissions)
    && meta.known_omissions.length > 0
    && meta.known_omissions.every((item) => typeof item === "string" && item.length > 0)
    ? meta.known_omissions.join(" · ")
    : null;
  const fields = {
    provider: citySourceText(source.provider),
    dataset: citySourceText(source.dataset),
    version: citySourceText(source.version),
    "published-on": citySourceText(source.published_on),
    license: citySourceText(source.license) || citySourceText(meta.license),
    attribution: citySourceText(source.attribution) || citySourceText(meta.attribution),
    "source-url": citySourceText(source.source_url) || citySourceText(meta.source_url),
    bounds: boundsText,
    "time-zone": citySourceText(meta.time_zone),
    omissions,
    sha256: citySourceText(meta.city_sha256),
  };
  for (const [name, value] of Object.entries(fields)) setCitySourceField(name, value);
  const frozen = meta.saved === true;
  byId("city-source-label").textContent = frozen ? "Frozen city input" : "City input";
  byId("city-source-disclosure").textContent = frozen
    ? "Copied from this run's frozen, validated city input. Source and license fields are operator-declared; this does not assert current registry status or rights review."
    : "Copied from the active validated city input. Source and license fields are operator-declared; this does not assert rights review.";
  byId("city-source-status").textContent = "Operator-declared";
  byId("city-source-drawer").open = false;
  byId("city-source-panel").hidden = false;
}

async function boot() {
  try {
    const [metaResponse, city, agents] = await Promise.all([
      fetchJsonWithIntegerToken("/meta", "seed"),
      fetchJson("/city"),
      fetchJson("/agents"),
    ]);
    const meta = metaResponse.document;
    validateSpatialResponseSeed(meta.seed, metaResponse.integerToken);
    state.meta = meta; state.metaSeedToken = metaResponse.integerToken;
    state.city = city; state.agents = agents; state.selected = agents[0].agent_id;
    byId("model-clock-note").textContent = `Clock: ${modelTimeZone()}. Daylight is illustrative from 06:00–19:00; it does not model local sunrise, traffic, calendars, or DST.`;
    // Exact V10 dispatch: the run must declare bounded cognition and its helper must be loaded.
    const boundedRun = meta.run_schema_version === boundedRunSchemaVersion;
    if (boundedRun) {
      boundedCognitionHelper();
      if (
        meta.saved !== true
        || meta.bounded_cognition !== true
        || meta.cognition_effect_scope !== boundedEffectScope
        || meta.spatial_opportunities !== true
        || meta.spatial_attention !== true
        || meta.spatial_response !== true
        || meta.response_model_id !== boundedResponseModelId
        || meta.workbench_input_available !== true
      ) throw new Error("Saved bounded cognition run metadata is invalid");
    }
    if (meta.workbench_input_available === true) {
      try {
        if (boundedRun) {
          // A V10 run is served by the workbench (/api/runs/{id}) and standalone city-view (/api).
          if (!validEvidenceHash(meta.workbench_input_sha256)) invalidWorkbenchInput();
        } else if (
          ![7, 8, 9].includes(meta.run_schema_version)
          || !validEvidenceHash(meta.workbench_input_sha256)
          || !validatedApiBase().startsWith("/api/runs/")
        ) invalidWorkbenchInput();
        state.workbenchInput = validateWorkbenchInput(await fetchJson("/workbench-input"));
      } catch (_) {
        renderWorkbenchFailure();
        invalidWorkbenchInput();
      }
    } else {
      renderWorkbenchUnavailable(meta);
    }
    if (meta.place_set_sha256) {
      const [places, assignmentDocument] = await Promise.all([fetchJson("/places"), fetchJson("/place-assignments")]);
      state.places = places;
      state.placeAssignments = assignmentDocument.assignments;
      for (const key of document.querySelectorAll(".place-key")) key.hidden = false;
    }
    if (meta.spatial_opportunities === true) {
      state.opportunitySummary = await fetchJson("/opportunity-summary");
      if (state.workbenchInput) {
        try {
          validateWorkbenchSummaryBinding(state.opportunitySummary, state.workbenchInput);
        } catch (_) {
          renderWorkbenchFailure();
          invalidWorkbenchInput();
        }
      }
      byId("opportunity-scenario").textContent = state.opportunitySummary.scenario_name;
      byId("opportunity-total").textContent = String(state.opportunitySummary.counts.opportunity_count);
      byId("opportunity-roadside").textContent = String(state.opportunitySummary.counts.roadside_opportunity_count);
      byId("opportunity-phone").textContent = String(state.opportunitySummary.counts.phone_opportunity_count);
      byId("opportunity-panel").hidden = false;
      for (const key of document.querySelectorAll(".opportunity-key")) key.hidden = false;
    }
    if (meta.spatial_attention === true) {
      state.attentionSummary = await fetchJson("/attention-summary");
      byId("attention-impressions").textContent = String(state.attentionSummary.counts.impression_count);
      byId("attention-notices").textContent = String(state.attentionSummary.counts.noticed_count);
      byId("attention-probability").textContent = `${Math.round(state.attentionSummary.notice_probability * 100)}%`;
      byId("attention-panel").hidden = false;
      for (const key of document.querySelectorAll(".attention-key")) key.hidden = false;
    }
    if (meta.spatial_response === true) {
      const responseSummary = await fetchJson("/response-summary");
      if (boundedRun) {
        state.responseSummary = validateBoundedResponseSummary(responseSummary);
      } else {
        state.responseSummary = responseSummary;
        if (state.responseSummary.claim_scope !== "synthetic-response-not-observed-behavior") {
          throw new Error("Spatial response claim scope is invalid");
        }
      }
      const responseCounts = state.responseSummary.counts;
      byId("response-scenario").textContent = state.opportunitySummary
        ? state.opportunitySummary.scenario_name
        : "Spatial response study";
      byId("response-responses").textContent = String(
        boundedRun ? responseCounts.baseline_response_count : responseCounts.response_count,
      );
      byId("response-updates").textContent = String(
        boundedRun ? responseCounts.dual_state_update_count : responseCounts.state_update_count,
      );
      byId("response-campaigns").textContent = String(responseCounts.campaign_count);
      if (boundedRun) renderBoundedResponseFrame();
      byId("response-panel").hidden = false;
      byId("response-state-panel").hidden = false;
    }
    if (meta.run_schema_version === 9) {
      if (
        meta.saved !== true
        || !state.workbenchInput
        || !state.responseSummary
        || !validatedApiBase().startsWith("/api/runs/")
      ) invalidCognitionEvidence();
      await loadCognitionEvidence();
      renderCognitionSummary();
    } else if (boundedRun) {
      if (!state.workbenchInput || !state.responseSummary) invalidCognitionEvidence();
      await loadBoundedCognitionEvidence();
      renderBoundedCognitionSummary();
    } else {
      byId("model-boundary-label").textContent = "Deterministic simulation · no saved explanations";
    }
    if (meta.spatial_metrics === true) {
      state.spatialMetrics = await fetchJson("/spatial-metrics");
      renderSpatialMetrics();
    }
    if (meta.spatial_response_metrics === true) {
      const responseMetrics = await fetchJsonWithIntegerToken(
        "/spatial-response-metrics",
        "seed",
      );
      state.spatialResponseMetrics = responseMetrics.document;
      state.spatialResponseSeedToken = responseMetrics.integerToken;
      if (boundedRun) renderBoundedResponseMetrics();
      else renderSpatialResponseMetrics();
    }
    byId("city-name").textContent = meta.city_name;
    if (meta.saved === true && typeof meta.run_id === "string") {
      byId("saved-run-label").textContent = `SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}`;
      byId("saved-run-label").hidden = false;
    }
    byId("map-title").textContent = meta.city_name;
    byId("agent-count").textContent = String(meta.agent_count).padStart(2, "0");
    byId("road-count").textContent = String(city.roads.length).padStart(2, "0");
    byId("seed-value").textContent = state.metaSeedToken;
    byId("model-label").textContent = meta.model.replace("illustrative-road-", "").replaceAll("-", " ").toUpperCase();
    byId("people-total").textContent = `${agents.length} total`;
    byId("attribution").textContent = meta.attribution;
    byId("license-label").textContent = meta.license;
    renderCitySource(meta);
    byId("timeline-duration").textContent = `${meta.days} ${meta.days === 1 ? "DAY" : "DAYS"}`;
    byId("time-slider").max = String(meta.days * 1440 - 1);
    if (state.workbenchInput) {
      if (
        [8, 9].includes(state.workbenchInput.run_schema_version)
        || state.workbenchInput.run_schema_version === boundedRunSchemaVersion
      ) {
        state.personaByAgent = validateWorkbenchPopulation(
          state.workbenchInput.population, state.workbenchInput.settings.agent_count,
        );
      }
      renderWorkbenchAssumptions(state.workbenchInput);
    }
    if (state.request === 0) await setMinute(0);
    else if (state.frame !== null) renderTimeline();
  } catch (error) { showError(String(error)); }
}

byId("time-slider").addEventListener("input", (event) => setMinute(Number(event.target.value)));
byId("play-button").addEventListener("click", togglePlayback);
byId("previous-day-button").addEventListener("click", () => jumpModelDay(-1));
byId("next-day-button").addEventListener("click", () => jumpModelDay(1));
byId("playback-speed").addEventListener("change", (event) => {
  const speed = Number(event.target.value);
  if (!Object.hasOwn(PLAYBACK_DELAY_MILLISECONDS, String(speed))) return;
  state.playbackSpeed = speed;
  byId("playback-speed-label").textContent = `${speed}×`;
  if (state.playing) {
    byId("playback-status").textContent = `PLAYING · ${speed}×`;
  }
});
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
reducedMotionQuery.addEventListener("change", () => {
  if (reducedMotionRequested()) stopPlayback();
  else updatePlaybackControl("PAUSED");
});
const compactReceiptQuery = window.matchMedia("(max-width: 520px)");
function syncAdvancedReceiptVisibility(query) {
  for (const id of ["metrics-panel", "response-metrics-panel"]) {
    byId(id).open = !query.matches;
  }
}
compactReceiptQuery.addEventListener("change", (event) => {
  syncAdvancedReceiptVisibility(event);
});
syncAdvancedReceiptVisibility(compactReceiptQuery);
window.addEventListener("pagehide", () => stopPlayback("PAUSED"));
window.addEventListener("resize", scheduleErrorBannerInset);
window.addEventListener("scroll", scheduleErrorBannerInset, { passive: true });
for (const kind of evidencePageKinds) {
  byId(`${kind}-page-previous`).addEventListener(
    "click",
    () => navigateEvidencePage(kind, "previous"),
  );
  byId(`${kind}-page-next`).addEventListener(
    "click",
    () => navigateEvidencePage(kind, "next"),
  );
}
attachMapControls();
boot();
