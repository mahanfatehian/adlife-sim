// Animated story map: a canvas 2D drawing of the fictional town and its people.
// Geography never mirrors: the canvas container is dir="ltr" in every language.
// Positions come only from the story's movement tracks; the page makes no /frame calls.

import { dir, fmt, lang, t } from "./guide-i18n.js";

const MINUTES_PER_DAY = 1440;
const PADDING = 24;
const ROAD_WIDTH = 6;
const NODE_DIAMETER = 4;
const PLACE_DOT = 9;
const PERSON_SIZE = 12;
const RING_DIAMETER = 18;
const PULSE_MS = 700;
// Centre distance between two people who share a spot; markers keep a small gap.
const SPACING = PERSON_SIZE + 4;
// People within this many pixels of each other stand at one spot.
const SAME_SPOT = 1;
// Around a highlighted person the others sit outside its status ring, on an arc of at
// most 240 degrees that faces away from the label bubble.
const FOCUS_RADIUS = RING_DIAMETER / 2 + 3 + PERSON_SIZE / 2 + 3;
const ARC_STEP = Math.PI / 3;
const ARC_SPAN = (Math.PI * 4) / 3;
// Place labels keep clear of everyone gathered at their own or another place.
const PLACE_CLEARANCE = FOCUS_RADIUS + PERSON_SIZE / 2 + 1;
const PASSING_CLEARANCE = 8;
const LABEL_GAP = 4;
const EDGE = 3;
// Past this many places a label with no free spot shrinks to a badge on its place.
const CROWDED_PLACES = 12;
const BADGE_DIAMETER = 16;
// Roads thin out in a dense town, so its streets stay apart on a small map.
const MIN_ROAD_WIDTH = 3;

export const PLACE_ROLES = Object.freeze(["home", "work", "leisure"]);
// Glyphs on a 16 x 16 grid: a house, a briefcase and a tree. story.html.j2 draws the same
// paths in the legend under the map.
export const PLACE_GLYPHS = Object.freeze({
  home: Object.freeze(["M8 1.6 15.2 8.2h-2v6.4H2.8V8.2H.8zM6.7 14.6v-4h2.6v4z", "evenodd"]),
  work: Object.freeze([
    "M5.2 5V3.2C5.2 2.5 5.7 2 6.4 2h3.2c.7 0 1.2.5 1.2 1.2V5h2.8c.7 0 1.2.5 1.2 1.2v6.6c0 .7-.5 "
      + "1.2-1.2 1.2H2.4c-.7 0-1.2-.5-1.2-1.2V6.2C1.2 5.5 1.7 5 2.4 5zM6.6 5h2.8V3.4H6.6z",
    "evenodd",
  ]),
  leisure: Object.freeze(["M8 1 12.6 7h-2.3l3.4 4.6H8.9V15H7.1v-3.4H2.3L5.7 7H3.4z", "nonzero"]),
});
const ACTIVITIES = new Set(["home", "work", "leisure", "commute"]);

// Fallbacks equal the guide.css tokens (section 7.1); the live tokens win when present.
const TOKENS = {
  "--map-bg": "#F8FAFC",
  "--map-road": "#A3AEC2",
  "--map-node": "#CBD5E1",
  "--ink": "#111827",
  "--ink-2": "#374151",
  "--ink-3": "#4B5563",
  "--line": "#D1D5DB",
  "--line-strong": "#9CA3AF",
  "--surface": "#FFFFFF",
  "--rules": "#334155",
  "--ai": "#6D28D9",
  "--practice": "#92400E",
  "--bad": "#B91C1C",
  "--neutral": "#4B5563",
  "--persona-1": "#0072B2",
  "--persona-2": "#D55E00",
  "--persona-3": "#009E73",
  "--persona-4": "#CC79A7",
  "--persona-5": "#56B4E9",
  "--persona-6": "#E69F00",
  "--persona-7": "#F0E442",
  "--persona-8": "#000000",
  "--persona-9": "#882255",
  "--persona-10": "#44AA99",
};

// Status ring colour and whether the ring is dashed ("no AI effect" is never solid colour).
const RING = {
  answered: ["--ai", false],
  reused: ["--ai", false],
  practice: ["--practice", false],
  rules: ["--rules", false],
  failed: ["--bad", false],
  unsure: ["--neutral", true],
  warning: ["--neutral", true],
  over_limit: ["--neutral", true],
  not_chosen: ["--neutral", true],
  not_noticed: ["--ink-3", true],
};

/** The catalog word for one place role: home, work or leisure (free time). */
export function placeLabel(role) {
  if (role === "home") {
    return t("story.map.place.home");
  }
  if (role === "work") {
    return t("story.map.place.work");
  }
  return t("story.map.place.leisure");
}

// Words in one label run together with a middle dot; Persian uses its own comma, because a
// middle dot beside Persian digits reads as a zero.
function joiner() {
  return lang() === "fa" ? "، " : " · ";
}

function nodeLookup(nodesById, nodeId) {
  const node = nodesById instanceof Map ? nodesById.get(nodeId) : nodesById[nodeId];
  if (!node) {
    throw new Error("story map: unknown node");
  }
  return [node.longitude, node.latitude];
}

function along(points, minute) {
  // Points are [minute, lon, lat] in time order; linear between consecutive points.
  let low = 0;
  let high = points.length - 1;
  if (minute <= points[0][0]) {
    return [points[0][1], points[0][2]];
  }
  if (minute >= points[high][0]) {
    return [points[high][1], points[high][2]];
  }
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if (points[middle][0] <= minute) {
      low = middle;
    } else {
      high = middle;
    }
  }
  const [t0, x0, y0] = points[low];
  const [t1, x1, y1] = points[high];
  if (t1 <= t0) {
    return [x1, y1];
  }
  const fraction = (minute - t0) / (t1 - t0);
  return [x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction];
}

/**
 * Where one person is at an absolute (fractional) model minute.
 * Before the day's first outbound point: home. Inside a leg: linear between track points.
 * Between legs: the destination node (work, or leisure on leisure days). After the return,
 * and on days without legs: home.
 */
export function positionAt(person, minuteFloat, nodesById) {
  const home = nodeLookup(nodesById, person.anchors.home);
  if (!Number.isFinite(minuteFloat)) {
    return home;
  }
  const dayIndex = Math.floor(minuteFloat / MINUTES_PER_DAY);
  const day = person.days.find((entry) => entry.day_index === dayIndex);
  if (!day || day.outbound.length === 0 || day.inbound.length === 0) {
    return home;
  }
  const outbound = day.outbound;
  const inbound = day.inbound;
  if (minuteFloat < outbound[0][0]) {
    return home;
  }
  if (minuteFloat <= outbound[outbound.length - 1][0]) {
    return along(outbound, minuteFloat);
  }
  if (minuteFloat < inbound[0][0]) {
    const anchor = day.destination === "leisure" ? person.anchors.leisure : person.anchors.work;
    return nodeLookup(nodesById, anchor);
  }
  if (minuteFloat <= inbound[inbound.length - 1][0]) {
    return along(inbound, minuteFloat);
  }
  return home;
}

/** Every [start, end] minute interval in which at least one person is travelling, merged. */
export function movementIntervals(story) {
  const raw = [];
  for (const person of story.people) {
    for (const day of person.days) {
      for (const leg of [day.outbound, day.inbound]) {
        if (leg.length >= 2 && leg[leg.length - 1][0] > leg[0][0]) {
          raw.push([leg[0][0], leg[leg.length - 1][0]]);
        }
      }
    }
  }
  raw.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const [start, end] of raw) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) {
      last[1] = Math.max(last[1], end);
    } else {
      merged.push([start, end]);
    }
  }
  return merged;
}

/** Each node that is somebody's home, work or free-time place, with its roles in order. */
export function storyPlaces(story) {
  const known = new Set(story.map.nodes.map((node) => node.node_id));
  const roles = new Map();
  for (const person of story.people) {
    for (const role of PLACE_ROLES) {
      const nodeId = person.anchors[role];
      if (!known.has(nodeId)) {
        continue;
      }
      if (!roles.has(nodeId)) {
        roles.set(nodeId, new Set());
      }
      roles.get(nodeId).add(role);
    }
  }
  return [...roles]
    .map(([nodeId, found]) => ({ nodeId, roles: PLACE_ROLES.filter((role) => found.has(role)) }))
    .sort((a, b) => (a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0));
}

function readTokens(element) {
  const styles = getComputedStyle(element);
  const tokens = {};
  for (const [name, fallback] of Object.entries(TOKENS)) {
    const value = styles.getPropertyValue(name).trim();
    tokens[name] = value || fallback;
  }
  return tokens;
}

function personaToken(colorIndex) {
  return `--persona-${(colorIndex % 10) + 1}`;
}

function rootFontSize() {
  return Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
}

function tracePerson(context, shape, x, y, size) {
  const half = size / 2;
  context.beginPath();
  if (shape === 0) {
    context.arc(x, y, half, 0, Math.PI * 2);
  } else if (shape === 1) {
    context.rect(x - half * 0.9, y - half * 0.9, half * 1.8, half * 1.8);
  } else if (shape === 2) {
    context.moveTo(x, y - half * 1.1);
    context.lineTo(x + half * 1.05, y + half * 0.8);
    context.lineTo(x - half * 1.05, y + half * 0.8);
    context.closePath();
  } else {
    context.moveTo(x, y - half * 1.15);
    context.lineTo(x + half * 1.15, y);
    context.lineTo(x, y + half * 1.15);
    context.lineTo(x - half * 1.15, y);
    context.closePath();
  }
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.lineTo(x + width - radius, y);
  context.arcTo(x + width, y, x + width, y + radius, radius);
  context.lineTo(x + width, y + height - radius);
  context.arcTo(x + width, y + height, x + width - radius, y + height, radius);
  context.lineTo(x + radius, y + height);
  context.arcTo(x, y + height, x, y + height - radius, radius);
  context.lineTo(x, y + radius);
  context.arcTo(x, y, x + radius, y, radius);
  context.closePath();
}

function drawGlyph(context, role, x, y, size) {
  const [path, rule] = PLACE_GLYPHS[role];
  context.save();
  context.translate(x, y);
  context.scale(size / 16, size / 16);
  context.fill(new Path2D(path), rule);
  context.restore();
}

function boxesOverlap(a, b, gap = 0) {
  return (
    a.left < b.left + b.width + gap &&
    b.left < a.left + a.width + gap &&
    a.top < b.top + b.height + gap &&
    b.top < a.top + a.height + gap
  );
}

function circleHitsBox(x, y, radius, box) {
  const nearX = Math.min(Math.max(x, box.left), box.left + box.width);
  const nearY = Math.min(Math.max(y, box.top), box.top + box.height);
  return (x - nearX) ** 2 + (y - nearY) ** 2 < radius * radius;
}

// Liang-Barsky: does the segment from (x0, y0) to (x1, y1) cross the box?
function segmentHitsBox(x0, y0, x1, y1, box) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  let low = 0;
  let high = 1;
  const edges = [
    [-dx, x0 - box.left],
    [dx, box.left + box.width - x0],
    [-dy, y0 - box.top],
    [dy, box.top + box.height - y0],
  ];
  for (const [p, q] of edges) {
    if (p === 0) {
      if (q < 0) {
        return false;
      }
      continue;
    }
    const r = q / p;
    if (p < 0) {
      if (r > high) {
        return false;
      }
      low = Math.max(low, r);
    } else {
      if (r < low) {
        return false;
      }
      high = Math.min(high, r);
    }
  }
  return true;
}

// Offsets for people sharing a spot with nobody highlighted: one ring, clockwise from the top.
function ringOffsets(count) {
  if (count < 2) {
    return [[0, 0]];
  }
  const radius = SPACING / (2 * Math.sin(Math.PI / count));
  return Array.from({ length: count }, (_, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / count;
    return [Math.cos(angle) * radius, Math.sin(angle) * radius];
  });
}

// Offsets for the others at a highlighted person's spot: an arc centred on one direction.
function arcOffsets(count, centre) {
  const span = Math.min(ARC_SPAN, ARC_STEP * (count - 1));
  const step = count > 1 ? span / (count - 1) : 0;
  const needed = count > 1 ? SPACING / (2 * Math.sin(step / 2)) : 0;
  const radius = Math.max(FOCUS_RADIUS, needed);
  return Array.from({ length: count }, (_, index) => {
    const angle = centre + (index - (count - 1) / 2) * step;
    return [Math.cos(angle) * radius, Math.sin(angle) * radius];
  });
}

export class StoryMap {
  constructor(canvas, story) {
    this.canvas = canvas;
    this.story = story;
    this.context = canvas.getContext("2d");
    this.minute = 0;
    this.highlighted = null;
    this.labelText = null;
    this.pulseStart = null;
    this.pulseFrame = 0;
    this.width = 0;
    this.height = 0;
    this.base = null;
    this.avoid = null;
    this.nodesById = new Map(
      story.map.nodes.map((node) => [node.node_id, node]),
    );
    this.colorByAgent = new Map();
    for (const persona of story.personas) {
      for (const agentId of persona.agent_ids) {
        this.colorByAgent.set(agentId, persona.color_index);
      }
    }
    this.people = story.people
      .map((person) => ({
        person,
        color: this.colorByAgent.get(person.agent_id) ?? 0,
      }))
      .sort((a, b) => a.person.number - b.person.number);
    this.places = storyPlaces(story);
    this.tokens = readTokens(canvas);
    this.observer = null;
    if (typeof ResizeObserver === "function") {
      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(canvas.parentElement || canvas);
    }
    this.resize();
  }

  /** Move the clock and every person to one absolute model minute (fractional allowed). */
  setTime(minuteFloat) {
    this.minute = minuteFloat;
    this.draw();
  }

  /** Ring and label one person in the colour of a moment status, or clear with null. */
  highlight(agentId, status) {
    if (agentId === null || agentId === undefined) {
      this.highlighted = null;
      this.labelText = null;
    } else {
      const person = this.story.people.find((entry) => entry.agent_id === agentId);
      if (!person) {
        throw new Error("story map: unknown person");
      }
      this.highlighted = { person, status: RING[status] ? status : "not_noticed" };
    }
    this.draw();
  }

  /** The person label shown in the bubble; the clock part follows the map time. */
  setLabel(text) {
    this.labelText = text || null;
    this.measureOverlay();
    this.draw();
  }

  /** One short expanding ring at the highlighted person (skipped under reduced motion). */
  pulse() {
    if (!this.highlighted) {
      return;
    }
    this.pulseStart = performance.now();
    const step = (now) => {
      if (this.pulseStart === null) {
        return;
      }
      if (now - this.pulseStart >= PULSE_MS) {
        this.pulseStart = null;
        this.pulseFrame = 0;
        this.draw();
        return;
      }
      this.draw(now);
      this.pulseFrame = requestAnimationFrame(step);
    };
    cancelAnimationFrame(this.pulseFrame);
    this.pulseFrame = requestAnimationFrame(step);
  }

  resize() {
    const frame = this.canvas.parentElement || this.canvas;
    const box = frame.getBoundingClientRect();
    const width = Math.max(1, Math.floor(box.width));
    const height = Math.max(1, Math.floor(box.height));
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    this.tokens = readTokens(this.canvas);
    this.width = width;
    this.height = height;
    this.ratio = ratio;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.measureOverlay();
    this.project = this.projection(width, height);
    this.base = this.drawBase();
    this.draw();
  }

  destroy() {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    this.pulseStart = null;
    cancelAnimationFrame(this.pulseFrame);
  }

  // Equirectangular over map.bounds, x scaled by cos(mid-latitude). The town is centred in
  // the frame unless that puts part of it under the clock; then it fits beside or below the
  // clock, whichever keeps it larger.
  projection(width, height) {
    const { west, south, east, north } = this.story.map.bounds;
    const middle = (((south + north) / 2) * Math.PI) / 180;
    const scaleX = Math.cos(middle);
    const worldWidth = Math.max((east - west) * scaleX, 1e-9);
    const worldHeight = Math.max(north - south, 1e-9);
    const fit = ([x0, y0, x1, y1]) => {
      const usableWidth = Math.max(x1 - x0, 1);
      const usableHeight = Math.max(y1 - y0, 1);
      const scale = Math.min(usableWidth / worldWidth, usableHeight / worldHeight);
      const offsetX = x0 + (usableWidth - worldWidth * scale) / 2;
      const offsetY = y0 + (usableHeight - worldHeight * scale) / 2;
      return { scale, offsetX, offsetY, right: offsetX + worldWidth * scale };
    };
    let placement = fit([PADDING, PADDING, width - PADDING, height - PADDING]);
    const avoid = this.avoid;
    // Everyone gathered around a highlighted person stays clear of the clock too. The clock
    // sits in the top corner at the end of the reading direction: right, or left in Persian.
    const reach = FOCUS_RADIUS + PERSON_SIZE / 2 + 2;
    if (avoid) {
      const onLeft = avoid.x0 + avoid.x1 < width;
      const covered = onLeft
        ? placement.offsetX - reach < avoid.x1
        : placement.right + reach > avoid.x0;
      if (covered && placement.offsetY - reach < avoid.y1) {
        const right = width - PADDING;
        const bottom = height - PADDING;
        const below = fit([PADDING, Math.max(PADDING, avoid.y1 + reach), right, bottom]);
        const beside = onLeft
          ? fit([Math.min(right - 1, avoid.x1 + reach), PADDING, right, bottom])
          : fit([PADDING, PADDING, Math.max(PADDING + 1, avoid.x0 - reach), bottom]);
        placement = below.scale >= beside.scale ? below : beside;
      }
    }
    const { scale, offsetX, offsetY } = placement;
    return (longitude, latitude) => [
      offsetX + (longitude - west) * scaleX * scale,
      offsetY + (north - latitude) * scale,
    ];
  }

  // The full road width, unless the town's typical street is drawn shorter than eight road
  // widths; then thinner, never below MIN_ROAD_WIDTH.
  roadWidth() {
    const lengths = this.story.map.roads
      .map((road) => {
        const [x0, y0] = this.project(...road.points[0]);
        const [x1, y1] = this.project(...road.points[road.points.length - 1]);
        return Math.hypot(x1 - x0, y1 - y0);
      })
      .sort((a, b) => a - b);
    if (lengths.length === 0) {
      return ROAD_WIDTH;
    }
    const typical = lengths[Math.floor(lengths.length / 2)];
    return Math.max(MIN_ROAD_WIDTH, Math.min(ROAD_WIDTH, typical / 8));
  }

  drawBase() {
    const layer = document.createElement("canvas");
    layer.width = this.canvas.width;
    layer.height = this.canvas.height;
    const context = layer.getContext("2d");
    context.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
    const tokens = this.tokens;
    context.fillStyle = tokens["--map-bg"];
    context.fillRect(0, 0, this.width, this.height);
    context.lineCap = "round";
    context.lineJoin = "round";
    context.strokeStyle = tokens["--map-road"];
    context.lineWidth = this.roadWidth();
    for (const road of this.story.map.roads) {
      context.beginPath();
      road.points.forEach(([longitude, latitude], index) => {
        const [x, y] = this.project(longitude, latitude);
        if (index === 0) {
          context.moveTo(x, y);
        } else {
          context.lineTo(x, y);
        }
      });
      context.stroke();
    }
    context.fillStyle = tokens["--map-node"];
    for (const node of this.story.map.nodes) {
      const [x, y] = this.project(node.longitude, node.latitude);
      context.beginPath();
      context.arc(x, y, NODE_DIAMETER / 2, 0, Math.PI * 2);
      context.fill();
    }
    this.drawPlaces(context);
    return layer;
  }

  // Each place gets a small dot on its node and a label beside it: the role glyphs and, when
  // they fit, the words ("Home · Work"). Labels sit diagonally off the node where possible,
  // clear of the roads, the clock, the other labels and everyone who gathers at a place;
  // a label that cannot fit its words keeps its glyphs only (the legend explains them).
  drawPlaces(context) {
    const tokens = this.tokens;
    const size = Math.max(12, Math.round(rootFontSize() * 0.75));
    const family = getComputedStyle(this.canvas).fontFamily || "sans-serif";
    const rtl = dir() === "rtl";
    const metrics = {
      glyph: Math.round(size * 1.05),
      gap: 2,
      pad: Math.round(size * 0.5),
      textGap: Math.round(size * 0.4),
      height: Math.round(size * 1.7),
    };
    context.save();
    context.font = `600 ${size}px ${family}`;
    context.direction = dir();
    const entries = this.places.map((place) => {
      const node = this.nodesById.get(place.nodeId);
      const [x, y] = this.project(node.longitude, node.latitude);
      const text = place.roles.map(placeLabel).join(joiner());
      const glyphs = place.roles.length * metrics.glyph + (place.roles.length - 1) * metrics.gap;
      const compact = metrics.pad * 2 + glyphs;
      const full = compact + metrics.textGap + Math.ceil(context.measureText(text).width);
      return { ...place, x, y, text, glyphs, compact, full };
    });
    for (const entry of entries) {
      context.beginPath();
      context.arc(entry.x, entry.y, PLACE_DOT / 2, 0, Math.PI * 2);
      context.fillStyle = tokens["--surface"];
      context.fill();
      context.lineWidth = 2;
      context.strokeStyle = tokens["--ink-3"];
      context.stroke();
    }
    const labels = this.arrangePlaceLabels(entries, metrics.height);
    // The speech bubble steers clear of these, so it never hides a place's words.
    this.placeBoxes = labels.filter((label) => label.box).map((label) => label.box);
    for (const { entry, box, hasWords } of labels) {
      if (box) {
        this.drawPlaceLabel(context, box, entry, hasWords, metrics, rtl);
      } else {
        this.drawPlaceBadge(context, entry);
      }
    }
    context.restore();
    // What the map marks, for tests and tools: "node:home+work ..." and the worded count.
    this.canvas.dataset.places = entries
      .map((entry) => `${entry.nodeId}:${entry.roles.join("+")}`)
      .join(" ");
    this.canvas.dataset.placesWorded = String(labels.filter((label) => label.hasWords).length);
  }

  // Greedy placement in a few fixed orders (most hemmed-in first, widest first, top down,
  // bottom up); the arrangement with the most worded labels and the fewest road crossings
  // wins. The result depends only on the layout, so it is the same on every redraw.
  arrangePlaceLabels(entries, height) {
    const { west, south, east, north } = this.story.map.bounds;
    const [centreX, centreY] = this.project((west + east) / 2, (south + north) / 2);
    const segments = [];
    for (const road of this.story.map.roads) {
      const points = road.points.map(([longitude, latitude]) => this.project(longitude, latitude));
      for (let index = 1; index < points.length; index += 1) {
        segments.push([...points[index - 1], ...points[index]]);
      }
    }
    const anchors = new Set(entries.map((entry) => entry.nodeId));
    const keepOut = this.story.map.nodes.map((node) => {
      const [x, y] = this.project(node.longitude, node.latitude);
      return [x, y, anchors.has(node.node_id) ? PLACE_CLEARANCE - 1 : PASSING_CLEARANCE];
    });
    const scene = { centreX, centreY, segments, keepOut };
    const byNode = (a, b) => (a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0);
    const room = new Map(
      entries.map((entry) => [entry, this.labelOptions(entry, entry.full, height, scene, []).length]),
    );
    const orders = [
      [...entries].sort((a, b) => room.get(a) - room.get(b) || b.full - a.full || byNode(a, b)),
      [...entries].sort((a, b) => b.full - a.full || byNode(a, b)),
      [...entries].sort((a, b) => a.y - b.y || a.x - b.x || byNode(a, b)),
      [...entries].sort((a, b) => b.y - a.y || a.x - b.x || byNode(a, b)),
    ];
    let best = null;
    for (const order of orders) {
      const placed = [];
      let cost = 0;
      for (const entry of order) {
        const boxes = placed.filter((label) => label.box).map((label) => label.box);
        let option = this.labelOptions(entry, entry.full, height, scene, boxes)[0];
        const hasWords = Boolean(option);
        option = option || this.labelOptions(entry, entry.compact, height, scene, boxes)[0];
        if (!option && entries.length > CROWDED_PLACES) {
          // A crowded town: a label with no free spot becomes a small glyph badge on the
          // place itself instead of a box laid over its neighbours.
          cost += 40;
          placed.push({ entry, box: null, hasWords: false });
          continue;
        }
        option = option || this.labelOptions(entry, entry.compact, height, scene, boxes, true)[0];
        if (!option) {
          continue;
        }
        cost += option.score + (hasWords ? 0 : 20);
        placed.push({ entry, box: option.box, hasWords });
      }
      if (!best || cost < best.cost) {
        best = { cost, placed };
      }
    }
    return best ? best.placed : [];
  }

  // The free spots for one label, best first. Diagonal spots come first (they stay off a
  // grid's roads), then the sides; facing away from the town centre breaks ties. A loose
  // spot only has to stay on the canvas and off the clock.
  labelOptions(entry, width, height, scene, placedBoxes, loose = false) {
    const { centreX, centreY, segments, keepOut } = scene;
    const { x, y } = entry;
    const diagonal = PLACE_CLEARANCE / Math.SQRT2;
    const candidates = [
      [1, 1, x + diagonal, y + diagonal],
      [1, -1, x + diagonal, y - diagonal - height],
      [-1, 1, x - diagonal - width, y + diagonal],
      [-1, -1, x - diagonal - width, y - diagonal - height],
      [1, 0, x + PLACE_CLEARANCE, y - height / 2],
      [-1, 0, x - PLACE_CLEARANCE - width, y - height / 2],
      [0, 1, x - width / 2, y + PLACE_CLEARANCE],
      [0, -1, x - width / 2, y - PLACE_CLEARANCE - height],
    ];
    const outX = x - centreX;
    const outY = y - centreY;
    const reach = Math.hypot(outX, outY);
    const clock = this.avoid
      ? {
          left: this.avoid.x0,
          top: this.avoid.y0,
          width: this.avoid.x1 - this.avoid.x0,
          height: this.avoid.y1 - this.avoid.y0,
        }
      : null;
    const options = [];
    for (const [dx, dy, left, top] of candidates) {
      const box = { left: Math.round(left), top: Math.round(top), width, height };
      if (
        box.left < EDGE ||
        box.top < EDGE ||
        box.left + width > this.width - EDGE ||
        box.top + height > this.height - EDGE ||
        (clock && boxesOverlap(box, clock))
      ) {
        continue;
      }
      if (
        !loose &&
        (keepOut.some(([cx, cy, radius]) => circleHitsBox(cx, cy, radius, box)) ||
          placedBoxes.some((other) => boxesOverlap(box, other, LABEL_GAP)))
      ) {
        continue;
      }
      const inflated = {
        left: box.left - ROAD_WIDTH / 2,
        top: box.top - ROAD_WIDTH / 2,
        width: width + ROAD_WIDTH,
        height: height + ROAD_WIDTH,
      };
      const roads = segments.filter((segment) => segmentHitsBox(...segment, inflated)).length;
      const facing = reach > 0 ? (dx * outX + dy * outY) / (Math.hypot(dx, dy) * reach) : 0;
      const score = (dx !== 0 && dy !== 0 ? 0 : 3) + roads * 4 + (1 - facing) * 0.75;
      options.push({ box, score });
    }
    return options.sort((a, b) => a.score - b.score);
  }

  // The first role's glyph in a small disc centred on the place, for a crowded town.
  drawPlaceBadge(context, entry) {
    const tokens = this.tokens;
    context.save();
    context.beginPath();
    context.arc(entry.x, entry.y, BADGE_DIAMETER / 2, 0, Math.PI * 2);
    context.fillStyle = tokens["--surface"];
    context.fill();
    context.lineWidth = 1.5;
    context.strokeStyle = tokens["--ink-3"];
    context.stroke();
    context.fillStyle = tokens["--ink-2"];
    const glyph = BADGE_DIAMETER - 6;
    drawGlyph(context, entry.roles[0], entry.x - glyph / 2, entry.y - glyph / 2, glyph);
    context.restore();
  }

  drawPlaceLabel(context, box, entry, hasWords, metrics, rtl) {
    const tokens = this.tokens;
    if (!box) {
      return;
    }
    context.save();
    context.globalAlpha = 0.92;
    context.fillStyle = tokens["--surface"];
    roundedRect(context, box.left + 0.5, box.top + 0.5, box.width - 1, box.height - 1, 6);
    context.fill();
    context.globalAlpha = 1;
    context.lineWidth = 1;
    context.strokeStyle = tokens["--line-strong"];
    context.stroke();
    context.fillStyle = tokens["--ink-2"];
    const glyphTop = box.top + (box.height - metrics.glyph) / 2;
    let cursor = rtl ? box.left + box.width - metrics.pad - metrics.glyph : box.left + metrics.pad;
    for (const role of entry.roles) {
      drawGlyph(context, role, cursor, glyphTop, metrics.glyph);
      cursor += rtl ? -(metrics.glyph + metrics.gap) : metrics.glyph + metrics.gap;
    }
    if (hasWords) {
      context.textBaseline = "middle";
      context.textAlign = rtl ? "right" : "left";
      const offset = metrics.pad + entry.glyphs + metrics.textGap;
      const textX = rtl ? box.left + box.width - offset : box.left + offset;
      context.fillText(entry.text, textX, box.top + box.height / 2 + 0.5);
    }
    context.restore();
  }

  /**
   * Where everyone is drawn now, and the label bubble. People at one spot spread out in
   * person-number order: around a ring when nobody there is highlighted, otherwise the
   * highlighted person keeps the exact spot and the others take an arc beside it, away
   * from the bubble. People at nearby spots are then nudged apart.
   */
  layout() {
    const focusId = this.highlighted ? this.highlighted.person.agent_id : null;
    const points = this.people.map(({ person, color }) => {
      const [longitude, latitude] = positionAt(person, this.minute, this.nodesById);
      const [x, y] = this.project(longitude, latitude);
      return { person, color, x, y, focus: person.agent_id === focusId, group: 0 };
    });
    const focus = points.find((point) => point.focus) || null;
    const bubble = focus ? this.bubbleLayout(focus) : null;
    const groups = [];
    for (const point of points) {
      let group = groups.find(
        (entry) => Math.abs(entry.x - point.x) <= SAME_SPOT && Math.abs(entry.y - point.y) <= SAME_SPOT,
      );
      if (!group) {
        group = { x: point.x, y: point.y, members: [] };
        groups.push(group);
      }
      point.group = groups.indexOf(group);
      group.members.push(point);
    }
    for (const group of groups) {
      if (group.members.length < 2) {
        continue;
      }
      const centre = group.members.find((point) => point.focus);
      if (centre) {
        const others = group.members.filter((point) => point !== centre);
        const offsets = this.focusOffsets(centre, others.length, bubble);
        others.forEach((point, index) => {
          point.x = centre.x + offsets[index][0];
          point.y = centre.y + offsets[index][1];
        });
      } else {
        const offsets = ringOffsets(group.members.length);
        group.members.forEach((point, index) => {
          point.x = group.x + offsets[index][0];
          point.y = group.y + offsets[index][1];
        });
      }
    }
    this.separate(points);
    return { points, focus, bubble };
  }

  // The arc for the others at the highlighted person's spot: away from the bubble first,
  // then to either side or back towards the town, whichever keeps everyone on the canvas
  // and out from under the bubble.
  focusOffsets(centre, count, bubble) {
    const away = bubble && bubble.pointerUp ? -Math.PI / 2 : Math.PI / 2;
    const { west, south, east, north } = this.story.map.bounds;
    const [townX, townY] = this.project((west + east) / 2, (south + north) / 2);
    const inward = Math.atan2(townY - centre.y, townX - centre.x);
    const directions = [away, away + Math.PI / 2, away - Math.PI / 2, inward, away + Math.PI];
    const margin = PERSON_SIZE / 2 + 2;
    const bubbleBox = bubble
      ? {
          left: bubble.left - 2,
          top: bubble.top - 8,
          width: bubble.width + 4,
          height: bubble.height + 16,
        }
      : null;
    for (const direction of directions) {
      const offsets = arcOffsets(count, direction);
      const clear = offsets.every(([dx, dy]) => {
        const x = centre.x + dx;
        const y = centre.y + dy;
        return (
          x >= margin &&
          y >= margin &&
          x <= this.width - margin &&
          y <= this.height - margin &&
          !(bubbleBox && circleHitsBox(x, y, margin, bubbleBox))
        );
      });
      if (clear) {
        return offsets;
      }
    }
    return arcOffsets(count, away);
  }

  // People at different spots that still touch are pushed apart along the line between
  // them; the highlighted person never moves. Everyone else stays on the canvas.
  separate(points) {
    const minimum = SPACING - 2;
    for (let pass = 0; pass < 4; pass += 1) {
      let moved = false;
      for (let first = 0; first < points.length; first += 1) {
        for (let second = first + 1; second < points.length; second += 1) {
          const a = points[first];
          const b = points[second];
          if (a.group === b.group) {
            continue;
          }
          const distance = Math.hypot(b.x - a.x, b.y - a.y);
          if (distance >= minimum) {
            continue;
          }
          // A pair on the very same pixel parts vertically, the lower number on top.
          const [unitX, unitY] =
            distance < 1e-6 ? [0, 1] : [(b.x - a.x) / distance, (b.y - a.y) / distance];
          const push = minimum - distance;
          const shareA = a.focus ? 0 : b.focus ? 1 : 0.5;
          const shareB = 1 - shareA;
          a.x -= unitX * push * shareA;
          a.y -= unitY * push * shareA;
          b.x += unitX * push * shareB;
          b.y += unitY * push * shareB;
          moved = true;
        }
      }
      if (!moved) {
        break;
      }
    }
    const margin = PERSON_SIZE / 2 + 1;
    for (const point of points) {
      if (!point.focus) {
        point.x = Math.min(Math.max(point.x, margin), Math.max(margin, this.width - margin));
        point.y = Math.min(Math.max(point.y, margin), Math.max(margin, this.height - margin));
      }
    }
  }

  draw(now = null) {
    const context = this.context;
    if (!context || !this.base) {
      return;
    }
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, this.canvas.width, this.canvas.height);
    context.drawImage(this.base, 0, 0);
    context.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
    const highlighted = this.highlighted;
    if (highlighted) {
      this.drawTrail(highlighted.person);
    }
    const { points, focus, bubble } = this.layout();
    for (const point of points) {
      if (!point.focus) {
        this.drawPerson(point, highlighted ? 0.5 : 1);
      }
    }
    if (focus) {
      this.drawRing(focus, highlighted.status, now);
      this.drawPerson(focus, 1);
      this.drawBubble(focus, bubble);
    }
    context.globalAlpha = 1;
  }

  drawTrail(person) {
    const context = this.context;
    const dayIndex = Math.floor(this.minute / MINUTES_PER_DAY);
    const day = person.days.find((entry) => entry.day_index === dayIndex);
    if (!day) {
      return;
    }
    context.save();
    context.globalAlpha = 0.35;
    context.strokeStyle = this.tokens[personaToken(this.colorByAgent.get(person.agent_id) ?? 0)];
    context.lineWidth = 3;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.setLineDash([2, 6]);
    for (const leg of [day.outbound, day.inbound]) {
      context.beginPath();
      leg.forEach(([, longitude, latitude], index) => {
        const [x, y] = this.project(longitude, latitude);
        if (index === 0) {
          context.moveTo(x, y);
        } else {
          context.lineTo(x, y);
        }
      });
      context.stroke();
    }
    context.restore();
  }

  drawPerson(point, alpha) {
    const context = this.context;
    context.save();
    context.globalAlpha = alpha;
    tracePerson(context, point.color % 4, point.x, point.y, PERSON_SIZE);
    context.fillStyle = this.tokens[personaToken(point.color)];
    context.fill();
    context.lineWidth = 1.5;
    context.strokeStyle = this.tokens["--ink"];
    context.stroke();
    context.restore();
  }

  drawRing(point, status, now) {
    const context = this.context;
    const [token, dashed] = RING[status] || RING.not_noticed;
    const colour = this.tokens[token];
    context.save();
    context.lineWidth = 3;
    context.strokeStyle = colour;
    context.beginPath();
    context.arc(point.x, point.y, RING_DIAMETER / 2 + 1.5, 0, Math.PI * 2);
    if (dashed) {
      context.setLineDash([4, 3]);
    }
    context.stroke();
    if (now !== null && this.pulseStart !== null) {
      const progress = Math.min(1, (now - this.pulseStart) / PULSE_MS);
      context.setLineDash([]);
      context.globalAlpha = 0.55 * (1 - progress);
      context.lineWidth = 2;
      context.beginPath();
      context.arc(point.x, point.y, RING_DIAMETER / 2 + 4 + progress * 18, 0, Math.PI * 2);
      context.stroke();
    }
    context.restore();
  }

  // The clock overlay's box in canvas pixels, so the label bubble can stay clear of it.
  measureOverlay() {
    const frame = this.canvas.parentElement;
    const overlay = frame ? frame.querySelector(".story-clock") : null;
    this.avoid = null;
    if (!overlay || !overlay.textContent) {
      return;
    }
    const box = overlay.getBoundingClientRect();
    const origin = this.canvas.getBoundingClientRect();
    if (box.width > 0 && box.height > 0) {
      this.avoid = {
        x0: box.left - origin.left - 6,
        y0: box.top - origin.top - 6,
        x1: box.right - origin.left + 6,
        y1: box.bottom - origin.top + 6,
      };
    }
  }

  placeBubble(point, width, height) {
    const gap = RING_DIAMETER / 2 + 10;
    const margin = 6;
    const maxLeft = Math.max(margin, this.width - width - margin);
    const centred = Math.min(Math.max(point.x - width / 2, margin), maxLeft);
    const clear = (left, top) =>
      top >= margin &&
      top + height <= this.height - margin &&
      (!this.avoid ||
        left + width <= this.avoid.x0 ||
        left >= this.avoid.x1 ||
        top + height <= this.avoid.y0 ||
        top >= this.avoid.y1);
    const above = point.y - gap - height;
    const below = point.y + gap;
    const shift = (left) => Math.min(Math.max(left, margin), maxLeft);
    // Centred first, then slid so the pointer sits near either end of the bubble.
    const lefts = [centred, shift(point.x - width + 24), shift(point.x - 24)];
    // A second row one place label further out, for when a label sits right beside the spot.
    const step = (this.placeBoxes && this.placeBoxes[0] ? this.placeBoxes[0].height : 24) + 6;
    const options = [];
    for (const [rowAbove, rowBelow] of [[above, below], [above - step, below + step]]) {
      for (const left of lefts) {
        options.push([left, rowAbove, false], [left, rowBelow, true]);
      }
    }
    if (this.avoid) {
      const beside = shift(this.avoid.x0 - width);
      options.push([beside, above, false], [beside, below, true]);
    }
    // Among the spots that stay on the canvas and off the clock, cover the least place text.
    const covered = (left, top) =>
      (this.placeBoxes || []).reduce((sum, box) => {
        const x = Math.min(left + width, box.left + box.width) - Math.max(left, box.left);
        const y = Math.min(top + height, box.top + box.height) - Math.max(top, box.top);
        return sum + (x > 0 && y > 0 ? x * y : 0);
      }, 0);
    let best = null;
    for (const [left, top, pointerUp] of options) {
      if (!clear(left, top)) {
        continue;
      }
      const cost = covered(left, top);
      if (best === null || cost < best.cost) {
        best = { cost, left, top, pointerUp };
      }
      if (cost === 0) {
        break;
      }
    }
    if (best !== null) {
      return { left: best.left, top: best.top, pointerUp: best.pointerUp };
    }
    const top = Math.min(Math.max(above, margin), Math.max(margin, this.height - height - margin));
    return { left: centred, top, pointerUp: top > point.y };
  }

  // What the highlighted person is doing, once the map clock stands on one of their moments.
  activityNow() {
    const person = this.highlighted ? this.highlighted.person : null;
    if (!person || !Array.isArray(person.moments)) {
      return null;
    }
    const moment = person.moments.find((entry) => Math.abs(entry.minute - this.minute) < 1e-6);
    return moment && ACTIVITIES.has(moment.activity) ? moment.activity : null;
  }

  // The bubble's words, size and place: "Veyra #8 · 07:39 · at home".
  bubbleLayout(point) {
    if (!this.labelText || !this.context) {
      return null;
    }
    const context = this.context;
    const days = this.story.run.days;
    const parts = [this.labelText, fmt.clock(Math.max(0, Math.floor(this.minute)), days)];
    const activity = this.activityNow();
    if (activity) {
      parts.push(t(`activity.${activity}`));
    }
    const text = parts.join(joiner());
    const family = getComputedStyle(this.canvas).fontFamily || "sans-serif";
    // The label follows the page's type scale, so it stays readable on a large projector.
    const size = Math.round(Math.max(14, rootFontSize() * 0.875));
    const font = `600 ${size}px ${family}`;
    context.save();
    context.font = font;
    context.direction = dir();
    const width = Math.ceil(context.measureText(text).width) + Math.round(size * 1.25);
    context.restore();
    const height = Math.round(size * 1.9);
    const { left, top, pointerUp } = this.placeBubble(point, width, height);
    return { text, font, left, top, width, height, pointerUp };
  }

  drawBubble(point, bubble) {
    if (!bubble) {
      return;
    }
    const context = this.context;
    const { text, font, left, top, width, height, pointerUp } = bubble;
    context.save();
    context.font = font;
    context.direction = dir();
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.shadowColor = "rgba(17,24,39,.18)";
    context.shadowBlur = 6;
    context.shadowOffsetY = 1;
    context.fillStyle = this.tokens["--surface"];
    roundedRect(context, left, top, width, height, 8);
    context.fill();
    context.shadowColor = "transparent";
    context.lineWidth = 1;
    context.strokeStyle = this.tokens["--line-strong"];
    context.stroke();
    const tipX = Math.min(Math.max(point.x, left + 12), left + width - 12);
    context.beginPath();
    if (pointerUp) {
      context.moveTo(tipX - 6, top + 0.5);
      context.lineTo(tipX, top - 6);
      context.lineTo(tipX + 6, top + 0.5);
    } else {
      context.moveTo(tipX - 6, top + height - 0.5);
      context.lineTo(tipX, top + height + 6);
      context.lineTo(tipX + 6, top + height - 0.5);
    }
    context.fill();
    context.stroke();
    // A bubble moved out past a place label keeps a thin leader line to its person.
    const edge = pointerUp ? top - 6 : top + height + 6;
    const reach = pointerUp ? point.y + RING_DIAMETER / 2 : point.y - RING_DIAMETER / 2;
    if (pointerUp ? edge - reach > 4 : reach - edge > 4) {
      context.beginPath();
      context.moveTo(tipX, edge);
      context.lineTo(point.x, reach);
      context.lineWidth = 1.5;
      context.strokeStyle = this.tokens["--line-strong"];
      context.stroke();
    }
    context.fillStyle = this.tokens["--ink"];
    context.fillText(text, left + width / 2, top + height / 2 + 0.5);
    context.restore();
  }
}
