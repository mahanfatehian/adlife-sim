// Animated story map: a canvas 2D drawing of the fictional town and its people.
// Geography never mirrors: the canvas container is dir="ltr" in every language.
// Positions come only from the story's movement tracks; the page makes no /frame calls.

import { dir, fmt, lang } from "./guide-i18n.js";

const MINUTES_PER_DAY = 1440;
const PADDING = 24;
const ROAD_WIDTH = 6;
const NODE_DIAMETER = 4;
const HOME_SIZE = 7;
const PERSON_SIZE = 12;
const RING_DIAMETER = 18;
const FAN_RADIUS = 7;
const PULSE_MS = 700;

// Fallbacks equal the guide.css tokens (section 7.1); the live tokens win when present.
const TOKENS = {
  "--map-bg": "#F8FAFC",
  "--map-road": "#A3AEC2",
  "--map-node": "#CBD5E1",
  "--ink": "#111827",
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
  return `--persona-${(colorIndex % 8) + 1}`;
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
    this.people = story.people.map((person) => ({
      person,
      color: this.colorByAgent.get(person.agent_id) ?? 0,
    }));
    this.homeNodes = [...new Set(story.people.map((person) => person.anchors.home))];
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
    const reach = PERSON_SIZE + FAN_RADIUS;
    if (avoid && placement.right + reach > avoid.x0 && placement.offsetY - reach < avoid.y1) {
      const below = fit([PADDING, Math.max(PADDING, avoid.y1 + reach), width - PADDING, height - PADDING]);
      const beside = fit([PADDING, PADDING, Math.max(PADDING + 1, avoid.x0 - reach), height - PADDING]);
      placement = below.scale >= beside.scale ? below : beside;
    }
    const { scale, offsetX, offsetY } = placement;
    return (longitude, latitude) => [
      offsetX + (longitude - west) * scaleX * scale,
      offsetY + (north - latitude) * scale,
    ];
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
    context.lineWidth = ROAD_WIDTH;
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
    context.lineWidth = 1.5;
    for (const nodeId of this.homeNodes) {
      const node = this.nodesById.get(nodeId);
      if (!node) {
        continue;
      }
      const [x, y] = this.project(node.longitude, node.latitude);
      context.fillStyle = tokens["--surface"];
      context.strokeStyle = tokens["--ink-3"];
      context.fillRect(x - HOME_SIZE / 2, y - HOME_SIZE / 2, HOME_SIZE, HOME_SIZE);
      context.strokeRect(x - HOME_SIZE / 2, y - HOME_SIZE / 2, HOME_SIZE, HOME_SIZE);
    }
    return layer;
  }

  placed() {
    // Pixel positions; people at the same point fan out in a small circle by agent order.
    const points = this.people.map(({ person, color }) => {
      const [longitude, latitude] = positionAt(person, this.minute, this.nodesById);
      const [x, y] = this.project(longitude, latitude);
      return { person, color, x, y };
    });
    const groups = new Map();
    for (const point of points) {
      const key = `${Math.round(point.x)}:${Math.round(point.y)}`;
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key).push(point);
    }
    for (const group of groups.values()) {
      if (group.length < 2) {
        continue;
      }
      group.sort((a, b) => a.person.number - b.person.number);
      group.forEach((point, index) => {
        const angle = -Math.PI / 2 + (Math.PI * 2 * index) / group.length;
        point.x += Math.cos(angle) * FAN_RADIUS;
        point.y += Math.sin(angle) * FAN_RADIUS;
      });
    }
    return points;
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
    const points = this.placed();
    let focus = null;
    for (const point of points) {
      if (highlighted && point.person.agent_id === highlighted.person.agent_id) {
        focus = point;
        continue;
      }
      this.drawPerson(point, highlighted ? 0.5 : 1);
    }
    if (focus) {
      this.drawRing(focus, highlighted.status, now);
      this.drawPerson(focus, 1);
      this.drawBubble(focus);
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
    const options = [
      [centred, above, false],
      [centred, below, true],
    ];
    if (this.avoid) {
      const beside = Math.min(Math.max(this.avoid.x0 - width, margin), maxLeft);
      options.push([beside, above, false], [beside, below, true]);
    }
    for (const [left, top, pointerUp] of options) {
      if (clear(left, top)) {
        return { left, top, pointerUp };
      }
    }
    const top = Math.min(Math.max(above, margin), Math.max(margin, this.height - height - margin));
    return { left: centred, top, pointerUp: top > point.y };
  }

  drawBubble(point) {
    if (!this.labelText) {
      return;
    }
    const context = this.context;
    const days = this.story.run.days;
    const clock = fmt.clock(Math.max(0, Math.floor(this.minute)), days);
    // A middle dot beside Persian digits reads as a zero, so Persian uses its own comma.
    const text = `${this.labelText}${lang() === "fa" ? "، " : " · "}${clock}`;
    context.save();
    const family = getComputedStyle(this.canvas).fontFamily || "sans-serif";
    // The label follows the page's type scale, so it stays readable on a large projector.
    const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const size = Math.round(Math.max(14, root * 0.875));
    context.font = `600 ${size}px ${family}`;
    context.direction = dir();
    context.textAlign = "center";
    context.textBaseline = "middle";
    const width = Math.ceil(context.measureText(text).width) + Math.round(size * 1.25);
    const height = Math.round(size * 1.9);
    const { left, top, pointerUp } = this.placeBubble(point, width, height);
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
    context.fillStyle = this.tokens["--ink"];
    context.fillText(text, left + width / 2, top + height / 2 + 0.5);
    context.restore();
  }
}
