"use strict";

const byId = (id) => document.getElementById(id);
const state = { meta: null, city: null, agents: [], frame: null, selected: null, minute: 0, playing: false, timer: null, zoom: 1, panX: 0, panY: 0, request: 0 };
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
  const longs = nodes.map((node) => node.longitude);
  const lats = nodes.map((node) => node.latitude);
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
    const [x1, y1] = project(start.longitude, start.latitude);
    const [x2, y2] = project(end.longitude, end.latitude);
    ctx.strokeStyle = "#061923";
    ctx.lineWidth = road.kind === "motorway" || road.kind === "trunk" ? 12 : 8;
    ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.strokeStyle = roadColors[road.kind] || "#466976";
    ctx.lineWidth -= 5;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    if (road.one_way) {
      const angle = Math.atan2(y2 - y1, x2 - x1), mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      ctx.strokeStyle = "#c2a96f"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(mx - 5 * Math.cos(angle - .6), my - 5 * Math.sin(angle - .6));
      ctx.lineTo(mx, my); ctx.lineTo(mx - 5 * Math.cos(angle + .6), my - 5 * Math.sin(angle + .6)); ctx.stroke();
    }
  }
  if (state.frame.route && state.frame.route.agent_id === state.selected) {
    const routeNodes = state.frame.route.node_ids;
    if (routeNodes.length > 1) {
      ctx.strokeStyle = "#f2ca7d";
      ctx.lineWidth = 4;
      ctx.lineJoin = "round";
      ctx.setLineDash([10, 6]);
      ctx.beginPath();
      for (const [index, nodeId] of routeNodes.entries()) {
        const node = nodes.get(nodeId);
        const [x, y] = project(node.longitude, node.latitude);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
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

async function setMinute(minute) {
  if (!state.meta) return;
  const next = Math.max(0, Math.min(state.meta.days * 1440 - 1, Math.floor(minute)));
  const request = ++state.request;
  try {
    const frame = await fetchJson(`/api/frame?minute=${next}&agent_id=${encodeURIComponent(state.selected)}`);
    if (request !== state.request) return;
    state.frame = frame; state.minute = next;
    updateLabels(); renderPeople(); renderSelected(); renderMap();
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
    byId("city-name").textContent = meta.city_name;
    if (meta.saved === true && typeof meta.run_id === "string") {
      byId("saved-run-label").textContent = `SAVED RUN / ${meta.run_id} · V${meta.run_schema_version}`;
      byId("saved-run-label").hidden = false;
    }
    byId("map-title").textContent = meta.city_name;
    byId("agent-count").textContent = String(meta.agent_count).padStart(2, "0");
    byId("road-count").textContent = String(city.roads.length).padStart(2, "0");
    byId("seed-value").textContent = String(meta.seed);
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
