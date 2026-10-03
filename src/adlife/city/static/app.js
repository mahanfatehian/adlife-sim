"use strict";

const byId = (id) => document.getElementById(id);
const state = { meta: null, city: null, agents: [], places: null, placeAssignments: [], opportunitySummary: null, opportunityPage: null, attentionSummary: null, attentionPage: null, spatialMetrics: null, frame: null, selected: null, minute: 0, playing: false, timer: null, zoom: 1, panX: 0, panY: 0, request: 0 };
const canvas = byId("city-map");
const ctx = canvas.getContext("2d");
const stage = byId("map-stage");
const activities = { home: "At home", commute: "Travelling on streets", work: "At work", leisure: "Leisure visit" };

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  return response.json();
}

function showError(message) {
  const banner = byId("error-banner");
  banner.textContent = message;
  banner.hidden = false;
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
  byId("time-slider").value = String(minute);
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
      row.addEventListener("click", () => { state.selected = agent.agent_id; renderPeople(); setMinute(state.minute); });
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

function renderOpportunityEvidence() {
  if (!state.opportunitySummary || !state.opportunityPage) return;
  const page = state.opportunityPage;
  const list = byId("opportunity-list");
  while (list.firstChild) list.removeChild(list.firstChild);
  const selectedCount = page.agent_counts[state.selected] || 0;
  byId("opportunity-current").textContent = `${page.total} AT THIS MINUTE · ${selectedCount} FOR SELECTED AGENT`;
  byId("opportunity-empty").hidden = page.total !== 0;
  const pageNote = byId("opportunity-page-note");
  pageNote.hidden = page.items.length === page.total;
  pageNote.textContent = `Showing the first ${page.items.length} of ${page.total} canonical records.`;
  for (const opportunity of page.items) {
    const campaign = spatialCampaignById(opportunity.campaign_id);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "opportunity-card";
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
    card.addEventListener("click", () => {
      state.selected = opportunity.agent_id;
      renderPeople(); renderSelected(); renderOpportunityEvidence(); renderAttentionEvidence(); renderMap();
    });
    list.append(card);
  }
}

function renderAttentionEvidence() {
  if (!state.attentionSummary || !state.attentionPage) return;
  const page = state.attentionPage;
  const list = byId("attention-list");
  while (list.firstChild) list.removeChild(list.firstChild);
  const selectedCount = page.agent_counts[state.selected] || 0;
  byId("attention-current").textContent = `${page.total} EVENTS AT THIS MINUTE \u00b7 ${selectedCount} FOR SELECTED AGENT`;
  byId("attention-empty").hidden = page.total !== 0;
  const pageNote = byId("attention-page-note");
  pageNote.hidden = page.items.length === page.total;
  pageNote.textContent = `Showing the first ${page.items.length} of ${page.total} canonical events.`;
  for (const event of page.items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `attention-card ${event.event_type === "spatial.noticed" ? "notice" : "impression"}`;
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
    card.addEventListener("click", () => {
      state.selected = event.agent_id;
      renderPeople(); renderSelected(); renderOpportunityEvidence(); renderAttentionEvidence(); renderMap();
    });
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
      byId(`${prefix}-value`).textContent = metricValueText(name, receipt.value);
      byId(`${prefix}-receipt`).textContent = `${receipt.numerator} / ${receipt.denominator}`;
      byId(prefix).title = `Numerator ${receipt.numerator}; denominator ${receipt.denominator}. Sources: ${receipt.source_artifacts.join(", ")} (${receipt.source_event_types.join(", ")}).`;
    }
  }
  byId("metrics-panel").hidden = false;
}

async function setMinute(minute) {
  if (!state.meta) return;
  const next = Math.max(0, Math.min(state.meta.days * 1440 - 1, Math.floor(minute)));
  const request = ++state.request;
  try {
    const requests = [fetchJson(`/api/frame?minute=${next}&agent_id=${encodeURIComponent(state.selected)}`)];
    const opportunityIndex = state.opportunitySummary ? requests.length : null;
    if (opportunityIndex !== null) requests.push(fetchJson(`/api/opportunities?minute=${next}`));
    const attentionIndex = state.attentionSummary ? requests.length : null;
    if (attentionIndex !== null) requests.push(fetchJson(`/api/attention-events?minute=${next}`));
    const responses = await Promise.all(requests);
    const frame = responses[0];
    const opportunityPage = opportunityIndex === null ? null : responses[opportunityIndex];
    const attentionPage = attentionIndex === null ? null : responses[attentionIndex];
    if (request !== state.request) return;
    state.frame = frame; state.opportunityPage = opportunityPage; state.attentionPage = attentionPage; state.minute = next;
    updateLabels(); renderPeople(); renderSelected(); renderOpportunityEvidence(); renderAttentionEvidence(); renderMap();
  } catch (error) { showError(String(error)); stopPlayback(); }
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
    const [meta, city, agents] = await Promise.all([fetchJson("/api/meta"), fetchJson("/api/city"), fetchJson("/api/agents")]);
    state.meta = meta; state.city = city; state.agents = agents; state.selected = agents[0].agent_id;
    if (meta.place_set_sha256) {
      const [places, assignmentDocument] = await Promise.all([fetchJson("/api/places"), fetchJson("/api/place-assignments")]);
      state.places = places;
      state.placeAssignments = assignmentDocument.assignments;
      for (const key of document.querySelectorAll(".place-key")) key.hidden = false;
    }
    if (meta.spatial_opportunities === true) {
      state.opportunitySummary = await fetchJson("/api/opportunity-summary");
      byId("opportunity-scenario").textContent = state.opportunitySummary.scenario_name;
      byId("opportunity-total").textContent = String(state.opportunitySummary.counts.opportunity_count);
      byId("opportunity-roadside").textContent = String(state.opportunitySummary.counts.roadside_opportunity_count);
      byId("opportunity-phone").textContent = String(state.opportunitySummary.counts.phone_opportunity_count);
      byId("opportunity-panel").hidden = false;
      for (const key of document.querySelectorAll(".opportunity-key")) key.hidden = false;
    }
    if (meta.spatial_attention === true) {
      state.attentionSummary = await fetchJson("/api/attention-summary");
      byId("attention-impressions").textContent = String(state.attentionSummary.counts.impression_count);
      byId("attention-notices").textContent = String(state.attentionSummary.counts.noticed_count);
      byId("attention-probability").textContent = `${Math.round(state.attentionSummary.notice_probability * 100)}%`;
      byId("attention-panel").hidden = false;
      for (const key of document.querySelectorAll(".attention-key")) key.hidden = false;
    }
    if (meta.spatial_metrics === true) {
      state.spatialMetrics = await fetchJson("/api/spatial-metrics");
      renderSpatialMetrics();
    }
    byId("city-name").textContent = meta.city_name;
    if (meta.saved === true && typeof meta.run_id === "string") {
      byId("saved-run-label").textContent = `SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}`;
      byId("saved-run-label").hidden = false;
    }
    byId("map-title").textContent = meta.city_name;
    byId("agent-count").textContent = String(meta.agent_count).padStart(2, "0");
    byId("road-count").textContent = String(city.roads.length).padStart(2, "0");
    byId("seed-value").textContent = String(meta.seed);
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
attachMapControls();
boot();
