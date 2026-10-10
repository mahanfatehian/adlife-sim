"use strict";

const byId = (id) => document.getElementById(id);
const portableRunIdPattern = /^[a-z0-9][a-z0-9-]{0,39}$/;
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
    claimScope: "synthetic-opportunity-not-impression",
    modelKey: null,
    modelId: "spatial-opportunity-v1",
    idKey: "opportunity_id",
    eventTypes: null,
    maximumTotal: 520800,
    keys: ["schema_version", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "channel_counts", "agent_counts", "next_offset", "items"],
  },
  attention: {
    endpoint: "/attention-events",
    pageKey: "attentionPage",
    claimScope: "synthetic-attention-not-observed-behavior",
    modelKey: "attention_model_id",
    modelId: "spatial-attention-v1",
    idKey: "event_id",
    eventTypes: ["spatial.impression", "spatial.noticed"],
    maximumTotal: 1041600,
    keys: ["schema_version", "attention_model_id", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "event_type_counts", "channel_counts", "agent_counts", "next_offset", "items"],
  },
  response: {
    endpoint: "/response-events",
    pageKey: "responsePage",
    claimScope: "synthetic-response-not-observed-behavior",
    modelKey: "response_model_id",
    modelId: "spatial-response-v1",
    idKey: "event_id",
    eventTypes: ["spatial.response", "spatial.state-updated"],
    maximumTotal: 1041600,
    keys: ["schema_version", "response_model_id", "claim_scope", "minute", "agent_id", "offset", "limit", "total", "event_type_counts", "channel_counts", "agent_counts", "next_offset", "items"],
  },
};
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
const state = { meta: null, metaSeedToken: null, city: null, agents: [], places: null, placeAssignments: [], opportunitySummary: null, opportunityPage: null, attentionSummary: null, attentionPage: null, responseSummary: null, responsePage: null, responseStatePage: null, spatialMetrics: null, spatialResponseMetrics: null, spatialResponseSeedToken: null, frame: null, selected: null, minute: 0, playing: false, timer: null, zoom: 1, panX: 0, panY: 0, request: 0, timelineLoading: false, evidenceRequests: { opportunity: 0, attention: 0, response: 0 } };
const canvas = byId("city-map");
const ctx = canvas.getContext("2d");
const stage = byId("map-stage");
const activities = { home: "At home", commute: "Travelling on streets", work: "At work", leisure: "Leisure visit" };

async function fetchJson(relative) {
  const url = apiPath(relative);
  const response = await fetch(url, { cache: "no-store" });
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
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  const source = await response.text();
  const document = JSON.parse(source);
  return { document, integerToken: topLevelIntegerToken(source, key) };
}

function showError(message, scope = "application") {
  const banner = byId("error-banner");
  banner.textContent = message;
  banner.dataset.errorScope = scope;
  banner.hidden = false;
}

function clearError(scope) {
  const banner = byId("error-banner");
  if (banner.dataset.errorScope !== scope) return;
  banner.textContent = "";
  delete banner.dataset.errorScope;
  banner.hidden = true;
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
    `Day ${Math.floor(minute / 1440) + 1}, ${clockText(minute)}`,
  );
  document.body.classList.toggle("night", night);
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
  const scale = Math.min((width - 110) / xSpan, (height - 110) / ySpan) * state.zoom;
  const centerLon = (minLon + maxLon) / 2, centerLat = (minLat + maxLat) / 2;
  return (longitude, latitude) => [width / 2 + (longitude - centerLon) * correction * scale + state.panX, height / 2 - (latitude - centerLat) * scale + state.panY];
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
  const selected = opportunity.agent_id === state.selected;
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = selected ? "#fff3c8" : "#f0ca83";
  ctx.fillStyle = opportunity.channel === "roadside-billboard" ? "#f0ca83" : "#69d7e4";
  ctx.lineWidth = selected ? 3 : 2;
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
  const selected = event.agent_id === state.selected;
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = event.noticed ? "#77d6bd" : "#b6a7ef";
  ctx.lineWidth = selected ? 3 : 2;
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
        || responseState.last_response_minute >= 10080
      )
    )
    || ((responseState.response_count === 0) !== (responseState.last_response_minute === null))
  ) throw evidenceValidationError(kind);
  return responseState;
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
    const contract = evidencePageContracts[kind];
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
      || page.schema_version !== 1
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
    const expectedChannelTotal = kind === "response"
      ? eventTypeCounts["spatial.response"]
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
      else validateResponseRecord(itemValue, requestedMinute);
      const item = itemValue;
      const evidenceId = item[contract.idKey];
      if (!validEvidenceHash(evidenceId) || ids.has(evidenceId)) {
        throw evidenceValidationError(kind);
      }
      ids.add(evidenceId);
      pageAgentCounts[item.agent_id] = (pageAgentCounts[item.agent_id] || 0) + 1;
      if (item.event_type) {
        pageEventTypeCounts[item.event_type] = (pageEventTypeCounts[item.event_type] || 0) + 1;
      }
      if (item.channel) pageChannelCounts[item.channel] += 1;
    }
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
    card.classList.toggle("selected", opportunity.agent_id === state.selected);
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
    card.addEventListener("click", () => setMinute(state.minute, opportunity.agent_id));
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
    card.classList.toggle("selected", event.agent_id === state.selected);
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
    card.addEventListener("click", () => setMinute(state.minute, event.agent_id));
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

function renderResponseEvidence() {
  if (!state.responseSummary || !state.responsePage) return;
  const page = state.responsePage;
  const list = byId("response-list");
  const fragment = document.createDocumentFragment();
  const selectedCount = page.agent_counts[state.selected] || 0;
  for (const record of page.items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "response-card";
    card.dataset.evidenceId = record.event_id;
    card.classList.toggle("selected", record.agent_id === state.selected);
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
    card.addEventListener("click", () => setMinute(state.minute, record.agent_id));
    fragment.append(card);
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
    || ![6, 7].includes(document.source_run_schema_version)
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
      if (selector.value === "overall") {
        renderResponseMetricsState(metrics.overall, "ALL CAMPAIGNS");
        return;
      }
      const campaign = metrics.campaigns.find((item) => item.campaign_id === selector.value);
      if (!campaign) throw new Error("Unknown response metric campaign");
      renderResponseMetricsState(campaign, `CAMPAIGN / ${campaign.campaign_id.toUpperCase()}`);
    } catch (error) {
      showError(String(error));
    }
  });
  renderResponseMetricsState(metrics.overall, "ALL CAMPAIGNS");
  byId("response-metrics-panel").hidden = false;
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
    state[contract.pageKey] = page;
    try {
      renderMap();
      renderEvidenceKind(kind);
    } catch (error) {
      state[contract.pageKey] = committedPage;
      try {
        renderMap();
      } catch (_) {
        // Preserve the original render error; the committed state and ledger DOM remain usable.
      }
      renderEvidenceKind(kind);
      throw error;
    }
    clearError(errorScope);
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
}

async function setMinute(minute, selectedAgent = state.selected) {
  if (!state.meta) return;
  if (!state.agents.some((agent) => agent.agent_id === selectedAgent)) {
    showError("Could not select an unknown simulated person");
    return;
  }
  const next = Math.max(0, Math.min(state.meta.days * 1440 - 1, Math.floor(minute)));
  const request = ++state.request;
  const sameMinute = state.frame !== null && next === state.minute;
  const offsets = {};
  state.timelineLoading = true;
  for (const kind of evidencePageKinds) {
    const page = state[evidencePageContracts[kind].pageKey];
    offsets[kind] = sameMinute && page ? page.offset : 0;
    state.evidenceRequests[kind] += 1;
    setEvidencePaginationBusy(kind, true);
  }
  try {
    const requests = [fetchJson(`/frame?minute=${next}&agent_id=${encodeURIComponent(selectedAgent)}`)];
    const opportunityIndex = state.opportunitySummary ? requests.length : null;
    if (opportunityIndex !== null) requests.push(fetchJson(evidencePageUrl("opportunity", next, offsets.opportunity)));
    const attentionIndex = state.attentionSummary ? requests.length : null;
    if (attentionIndex !== null) requests.push(fetchJson(evidencePageUrl("attention", next, offsets.attention)));
    const responseIndex = state.responseSummary ? requests.length : null;
    if (responseIndex !== null) requests.push(fetchJson(evidencePageUrl("response", next, offsets.response)));
    const responseStateIndex = state.responseSummary ? requests.length : null;
    if (responseStateIndex !== null) requests.push(fetchJson(`/response-state?agent_id=${encodeURIComponent(selectedAgent)}`));
    const responses = await Promise.all(requests);
    const frame = responses[0];
    const opportunityPage = opportunityIndex === null
      ? null
      : validateEvidencePage("opportunity", responses[opportunityIndex], next, offsets.opportunity);
    const attentionPage = attentionIndex === null
      ? null
      : validateEvidencePage("attention", responses[attentionIndex], next, offsets.attention);
    const responsePage = responseIndex === null
      ? null
      : validateEvidencePage("response", responses[responseIndex], next, offsets.response);
    const responseStatePage = responseStateIndex === null ? null : responses[responseStateIndex];
    if (request !== state.request) return;
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
  } catch (error) {
    if (request !== state.request) return;
    state.timelineLoading = false;
    for (const kind of evidencePageKinds) setEvidencePaginationBusy(kind, false);
    updateLabels();
    showError(String(error));
    stopPlayback();
  }
}

function stopPlayback() {
  state.playing = false;
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  byId("play-button").textContent = "▶";
  byId("play-button").setAttribute("aria-label", "Play timeline");
}

function togglePlayback() {
  if (state.playing) { stopPlayback(); return; }
  state.playing = true;
  byId("play-button").textContent = "Ⅱ";
  byId("play-button").setAttribute("aria-label", "Pause timeline");
  state.timer = setInterval(() => {
    if (!state.meta) return;
    setMinute((state.minute + 5) % (state.meta.days * 1440));
  }, 150);
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
  canvas.addEventListener("wheel", (event) => { event.preventDefault(); state.zoom = Math.max(.5, Math.min(8, state.zoom * (event.deltaY < 0 ? 1.1 : .9))); renderMap(); }, { passive: false });
  new ResizeObserver(renderMap).observe(stage);
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
    if (meta.place_set_sha256) {
      const [places, assignmentDocument] = await Promise.all([fetchJson("/places"), fetchJson("/place-assignments")]);
      state.places = places;
      state.placeAssignments = assignmentDocument.assignments;
      for (const key of document.querySelectorAll(".place-key")) key.hidden = false;
    }
    if (meta.spatial_opportunities === true) {
      state.opportunitySummary = await fetchJson("/opportunity-summary");
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
      state.responseSummary = await fetchJson("/response-summary");
      if (state.responseSummary.claim_scope !== "synthetic-response-not-observed-behavior") {
        throw new Error("Spatial response claim scope is invalid");
      }
      byId("response-scenario").textContent = state.opportunitySummary
        ? state.opportunitySummary.scenario_name
        : "Spatial response study";
      byId("response-responses").textContent = String(state.responseSummary.counts.response_count);
      byId("response-updates").textContent = String(state.responseSummary.counts.state_update_count);
      byId("response-campaigns").textContent = String(state.responseSummary.counts.campaign_count);
      byId("response-panel").hidden = false;
      byId("response-state-panel").hidden = false;
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
      renderSpatialResponseMetrics();
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
    byId("people-total").textContent = `${agents.length} ACTIVE`;
    byId("attribution").textContent = meta.attribution;
    byId("license-label").textContent = meta.license;
    byId("source-link").href = meta.source_url;
    byId("timeline-duration").textContent = `${meta.days} ${meta.days === 1 ? "DAY" : "DAYS"}`;
    byId("time-slider").max = String(meta.days * 1440 - 1);
    await setMinute(0);
  } catch (error) { showError(String(error)); }
}

byId("time-slider").addEventListener("input", (event) => setMinute(Number(event.target.value)));
byId("play-button").addEventListener("click", togglePlayback);
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
