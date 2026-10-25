// Story page: boot, intro, key-moment stepper, narration card, People and Summary tabs.
// Every visible word comes from the catalog; saved user text only via textContent in <bdi>.

import { dir, fmt, initI18n, makeRefContext, resolveNodes, resolveText, t, tn } from "./guide-i18n.js";
import { ApiError, announce, bdi, byId, clear, el, getJson, meta, prefersReducedMotion } from "./guide-core.js";
import { StoryMap, movementIntervals } from "./guide-map.js";

const DWELL_MS = 6000;
const TWEEN_MAX_MS = 2500;
const TWEEN_MS_PER_MINUTE = 50;
const IDLE_SHARE = 0.3;
const MAX_TICKS = 240;
const MAX_LIST_ITEMS = 8;
const DIMENSIONS = ["affect", "relevance", "credibility", "salience"];
const VALUES = {
  affect: ["annoyed", "skeptical", "neutral", "curious", "positive", "uncertain"],
  relevance: ["none", "low", "medium", "high", "uncertain"],
  credibility: ["low", "medium", "high", "uncertain"],
  salience: ["none", "low", "medium", "high", "uncertain"],
};
const FLAGS = ["stereotype-risk", "unsupported-inference", "unsafe-content"];
const INTERESTS = new Set([
  "books", "commuting", "cooking", "crafts", "food", "gardening", "leisure", "music", "technology",
]);
const PROVIDER_MODES = new Set(["local", "remote"]);
// Prompt contract v3 asked the AI to answer as the person who noticed the ad.
const PERSONA_PROMPT = "city-cognition-persona-prompt-v3";
// Summary lines with an in-character version ("-persona") for prompt v3 runs.
const PERSONA_VARIANTS = ["story-how-ai", "story-limits-names", "story-limits-feelings"];
const STATUSES = new Set([
  "answered", "reused", "practice", "rules", "unsure", "warning", "failed", "over_limit",
  "not_chosen", "not_noticed",
]);

const state = {
  story: null,
  ctx: null,
  map: null,
  personas: new Map(),
  people: new Map(),
  keySteps: [],
  allSteps: [],
  steps: [],
  filter: "key",
  followed: null,
  index: -1,
  tab: "story",
  playing: false,
  dwell: 0,
  frame: 0,
  minute: 0,
  intervals: [],
  mathOpen: false,
  ready: false,
  bound: false,
};

// ---------------------------------------------------------------- small DOM helpers

// el() appends strings as text nodes and skips null/false children.
const h = el;

function refNodes(ref) {
  return ref ? resolveNodes(ref, state.ctx) : document.createDocumentFragment();
}

function numberNode(value, decimals = 2, signed = false) {
  return bdi(fmt.dec(value, decimals, { signed }), "ltr");
}

// Ten colours; the shape is colour_index % 4, as on the map. A template colour class
// implies its own shape, so a persona from 10 on names its different shape explicitly.
const MARK_SHAPES = ["circle", "square", "triangle", "diamond"];

function mark(colorIndex, large = false) {
  const size = large ? " g-mark--large" : "";
  const slot = colorIndex % 10;
  const shape = colorIndex % 4 === slot % 4 ? "" : ` g-mark--${MARK_SHAPES[colorIndex % 4]}`;
  return h("span", {
    className: `g-mark g-mark--c${slot}${shape}${size}`,
    attrs: { "aria-hidden": "true" },
  });
}

function setDisabled(button, disabled) {
  button.setAttribute("aria-disabled", disabled ? "true" : "false");
}

function isDisabled(button) {
  return button.getAttribute("aria-disabled") === "true";
}

function restartEnter(node) {
  node.classList.remove("story-enter");
  if (prefersReducedMotion()) {
    return;
  }
  void node.offsetWidth;
  node.classList.add("story-enter");
}

// ---------------------------------------------------------------- catalog wording

function dimensionLabel(dimension) {
  if (dimension === "affect") {
    return t("label.affect");
  }
  if (dimension === "relevance") {
    return t("label.relevance");
  }
  if (dimension === "credibility") {
    return t("label.credibility");
  }
  return t("label.salience");
}

function chipText(dimension, value) {
  return t("label.chip", { label: dimensionLabel(dimension), value: t(`${dimension}.${value}`) });
}

function funnelLabel(name) {
  switch (name) {
    case "opportunities":
      return t("story.summary.funnel.opportunities");
    case "noticed":
      return t("story.summary.funnel.noticed");
    case "chosen":
      return t("story.summary.funnel.chosen");
    case "sent":
      return t("story.summary.funnel.sent");
    case "practice":
      return t("story.summary.funnel.practice");
    default:
      return t("story.summary.funnel.used");
  }
}

function personaOf(person) {
  return state.personas.get(person.persona_id) || null;
}

function colorOf(person) {
  const persona = personaOf(person);
  return persona ? persona.color_index : 0;
}

function nameParam(persona) {
  if (persona && persona.template_key) {
    return { key: `persona.${persona.template_key}.name` };
  }
  return { text: persona ? persona.display_name : "" };
}

function personRef(person) {
  return {
    key: "common.person_ref",
    params: { name: nameParam(personaOf(person)), number: { int: person.number } },
  };
}

function personText(person) {
  return resolveText(personRef(person), state.ctx);
}

function personaName(persona) {
  if (persona.template_key) {
    return document.createTextNode(t(`persona.${persona.template_key}.name`));
  }
  return bdi(persona.display_name, "auto");
}

function interestsParam(interests) {
  return {
    list: interests.slice(0, MAX_LIST_ITEMS).map((value) =>
      INTERESTS.has(value) ? { key: `interest.${value}` } : { text: value },
    ),
  };
}

function cityParam(city) {
  return city.catalog_key ? { key: `city.${city.catalog_key}` } : { text: city.name };
}

function aiTone(status) {
  if (status === "answered" || status === "reused") {
    return "ai";
  }
  if (status === "practice") {
    return "practice";
  }
  return "none";
}

function modeTone() {
  const mode = state.story.ai.mode;
  if (mode === "mock") {
    return "practice";
  }
  if (mode === "rules") {
    return "neutral";
  }
  return "ai";
}

// ---------------------------------------------------------------- boot

function showProblem(kind, { retry = false, code = null } = {}) {
  const root = byId("story-root");
  root.dataset.state = "problem";
  byId("story-loading").hidden = true;
  byId("story-app").hidden = true;
  const problem = byId("story-problem");
  problem.hidden = false;
  for (const line of problem.querySelectorAll("[data-problem]")) {
    line.hidden = line.dataset.problem !== kind;
  }
  const codeLine = byId("story-problem-code");
  clear(codeLine);
  codeLine.hidden = true;
  if (code) {
    try {
      const context = makeRefContext({ days: 1, people: [] });
      codeLine.append(resolveNodes({ key: "common.error.code", params: { code: { id: code } } }, context));
      codeLine.hidden = false;
    } catch {
      codeLine.hidden = true;
    }
  }
  byId("story-retry").hidden = !retry;
}

function showLoading() {
  byId("story-root").dataset.state = "loading";
  byId("story-loading").hidden = false;
  byId("story-problem").hidden = true;
  byId("story-app").hidden = true;
}

function problemFor(error) {
  if (error instanceof ApiError && error.code === "unexpected-response") {
    return ["unexpected", { retry: true }];
  }
  if (error instanceof ApiError && error.status > 0) {
    if (error.status === 404 && error.code === "not-found") {
      return ["not_found", {}];
    }
    if (error.status === 404 && error.code === "story-unavailable") {
      return ["unavailable", {}];
    }
    if (error.status === 409 && error.code === "run-unavailable") {
      return ["unverifiable", { retry: true }];
    }
    if (error.status === 409 && error.code === "story-too-large") {
      return ["too_large", {}];
    }
    if (error.status >= 500) {
      return ["server", { retry: true }];
    }
    const code = typeof error.code === "string" && /^[a-z0-9-]{1,64}$/.test(error.code) ? error.code : null;
    return ["generic", { retry: true, code }];
  }
  if (error instanceof SyntaxError) {
    return ["unexpected", { retry: true }];
  }
  return ["server", { retry: true }];
}

function isStatus(value) {
  return typeof value === "string" && STATUSES.has(value);
}

function validStory(story) {
  if (
    !story ||
    typeof story !== "object" ||
    story.schema_version !== 1 ||
    story.format !== "adlife-run-story-v1" ||
    !story.run ||
    !story.ai ||
    !story.funnel ||
    !story.map ||
    !story.averages ||
    !Array.isArray(story.people) ||
    !Array.isArray(story.personas) ||
    !Array.isArray(story.key_moments) ||
    !Array.isArray(story.headline) ||
    !Array.isArray(story.map.nodes) ||
    !Array.isArray(story.map.roads)
  ) {
    return false;
  }
  return story.people.every(
    (person) =>
      person &&
      typeof person.agent_id === "string" &&
      Array.isArray(person.moments) &&
      Array.isArray(person.days) &&
      person.moments.every((moment) => moment && isStatus(moment.status)),
  );
}

async function load() {
  showLoading();
  const runId = meta("adlife-run-id");
  if (!runId || !/^[a-z0-9][a-z0-9-]{0,127}$/.test(runId)) {
    showProblem("not_found");
    return;
  }
  let story;
  try {
    story = await getJson(`/api/runs/${runId}/story`);
  } catch (error) {
    const [kind, options] = problemFor(error);
    showProblem(kind, options);
    return;
  }
  if (!validStory(story)) {
    showProblem("unexpected", { retry: true });
    return;
  }
  try {
    render(story);
  } catch (error) {
    console.error(error);
    showProblem("unexpected", { retry: true });
  }
}

// ---------------------------------------------------------------- render once per story

function indexStory(story) {
  state.personas = new Map(story.personas.map((persona) => [persona.persona_id, persona]));
  state.people = new Map(story.people.map((person) => [person.agent_id, person]));
  const moments = new Map();
  for (const person of story.people) {
    for (const moment of person.moments) {
      moments.set(`${person.agent_id}|${moment.moment_id}`, { person, moment });
    }
  }
  const byTime = (a, b) =>
    a.moment.minute - b.moment.minute ||
    a.moment.millisecond - b.moment.millisecond ||
    (a.person.agent_id < b.person.agent_id ? -1 : a.person.agent_id > b.person.agent_id ? 1 : 0);
  state.allSteps = [...moments.values()].sort(byTime);
  state.keySteps = story.key_moments
    .map((entry) => moments.get(`${entry.agent_id}|${entry.moment_id}`))
    .filter(Boolean);
  state.ctx = makeRefContext({
    days: story.run.days,
    people: story.people.map((person) => {
      const persona = state.personas.get(person.persona_id);
      return {
        agent_id: person.agent_id,
        template_key: persona ? persona.template_key : null,
        display_name: persona ? persona.display_name : "",
        number: person.number,
      };
    }),
  });
}

function render(story) {
  if (state.map) {
    state.map.destroy();
    state.map = null;
  }
  stopTween();
  state.story = story;
  state.playing = false;
  window.clearTimeout(state.dwell);
  indexStory(story);
  state.intervals = movementIntervals(story);
  renderHeader();
  renderLegend();
  renderIntro();
  renderOutro();
  renderPeople();
  renderSummary();
  byId("story-loading").hidden = true;
  byId("story-problem").hidden = true;
  byId("story-app").hidden = false;
  const root = byId("story-root");
  root.dataset.state = "ready";
  root.dataset.mode = story.ai.mode;
  fitHeight();
  state.filter = "key";
  state.followed = null;
  state.steps = state.keySteps;
  state.index = -1;
  state.minute = introMinute();
  // The clock is on the map before the map measures itself, so the town is laid out clear of it.
  paintClock(state.minute);
  state.map = new StoryMap(byId("story-map"), story);
  state.map.setTime(state.minute);
  renderFilter();
  renderTicks();
  bindOnce();
  state.ready = true;
  selectTab("story");
  goTo(-1, { animate: false });
}

function renderHeader() {
  const { run, ai } = state.story;
  const title = byId("story-title");
  clear(title);
  title.append(
    resolveNodes(
      {
        key: "story.header",
        params: {
          product: { text: run.creative.product_name },
          people: { key: "common.people", count: run.people_count },
          days: { key: "common.days", count: run.days },
          city: cityParam(run.city),
        },
      },
      state.ctx,
    ),
  );
  const metaLine = byId("story-meta");
  clear(metaLine);
  const tone = ai.mode === "mock" ? "practice" : ai.mode === "rules" ? "rules" : "ai";
  metaLine.append(h("span", { className: `g-chip g-chip--${tone}` }, t(`mode.${ai.mode}`)));
  if (ai.mode === "mock") {
    metaLine.append(h("span", { className: "g-chip g-chip--practice story-not-ai" }, t("common.not_an_ai")));
  }
  // A practice run still says "AI" in its shared sentences; the note says how to read them.
  byId("story-practice-note").hidden = ai.mode !== "mock";
  if (typeof ai.model_id === "string" && ai.model_id) {
    metaLine.append(h("span", { className: "story-model g-mono" }, bdi(ai.model_id, "ltr")));
  }
}

function renderLegend() {
  const list = byId("story-legend-list");
  clear(list);
  list.className = "g-legend story-legend-list";
  const personas = [...state.story.personas].sort((a, b) => a.color_index - b.color_index);
  for (const persona of personas) {
    list.append(h("li", {}, mark(persona.color_index), personaName(persona)));
  }
}

function introMinute() {
  const first = state.steps[0] || state.allSteps[0];
  if (first) {
    return Math.max(0, first.moment.minute - 1);
  }
  const { phone, billboard } = state.story.run;
  const windows = (phone && phone.windows) || (billboard && billboard.windows) || [];
  if (windows.length) {
    return (windows[0].day - 1) * 1440 + windows[0].start_minute;
  }
  return 0;
}

// The closing step shows the end of the last simulated day: the end of that day's last
// active window when it has one, otherwise the day's last minute; never before the last step.
function outroMinute() {
  const { days, phone, billboard } = state.story.run;
  const end = days * 1440 - 1;
  const lastDay = (days - 1) * 1440;
  let windowEnd = -1;
  for (const placement of [phone, billboard]) {
    for (const active of (placement && placement.windows) || []) {
      windowEnd = Math.max(windowEnd, (active.day - 1) * 1440 + active.end_minute);
    }
  }
  const target = windowEnd >= lastDay ? Math.min(end, windowEnd) : end;
  const last = state.steps[state.steps.length - 1];
  return Math.max(last ? last.moment.minute : 0, target);
}

// The step after the last moment is the closing step; there is none without moments.
function lastIndex() {
  return state.steps.length > 0 ? state.steps.length : -1;
}

function atOutro() {
  return state.steps.length > 0 && state.index === state.steps.length;
}

// "Moment 3 of 51 · Day 2 of 7": a run of several days names the day of the step.
function withDay(text, dayIndex) {
  const days = state.story.run.days;
  if (days < 2) {
    return text;
  }
  return t("story.counter.day", { counter: text, day: Math.min(days, dayIndex + 1), days });
}

function dayTotals() {
  const rows = state.story.day_totals;
  return Array.isArray(rows) ? rows.filter((row) => row && Number.isInteger(row.day_index)) : [];
}

// One row per simulated day: ad moments, noticed and (with AI or practice) chosen and used.
function daysTable(className) {
  const mode = state.story.ai.mode;
  const columns = [
    ["shown", t("story.summary.funnel.opportunities")],
    ["noticed", t("story.summary.funnel.noticed")],
  ];
  if (mode !== "rules") {
    columns.push(
      ["chosen", t("story.summary.funnel.chosen")],
      ["used", mode === "mock" ? t("story.summary.funnel.practice") : t("story.summary.funnel.used")],
    );
  }
  return h(
    "table",
    { className: `story-days ${className}` },
    h("caption", { className: "g-visually-hidden" }, t("story.days.title")),
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", { attrs: { scope: "col" } }, t("story.days.column")),
        ...columns.map(([, label]) => h("th", { attrs: { scope: "col" } }, label)),
      ),
    ),
    h(
      "tbody",
      {},
      ...dayTotals().map((row) =>
        h(
          "tr",
          { dataset: { day: String(row.day_index + 1) } },
          h("th", { attrs: { scope: "row" } }, t("story.days.day", { day: row.day_index + 1 })),
          ...columns.map(([name]) => h("td", { dataset: { cell: name } }, fmt.int(row[name] || 0))),
        ),
      ),
    ),
  );
}

function renderOutro() {
  const host = byId("story-outro-days");
  clear(host);
  host.append(daysTable("story-outro-table"));
}

function funnelCells() {
  const funnel = state.story.funnel;
  const mode = state.story.ai.mode;
  const cells = [
    { name: "opportunities", tone: "rules", value: funnel.opportunities },
    { name: "noticed", tone: "rules", value: funnel.noticed },
  ];
  if (mode === "rules") {
    return cells;
  }
  if (mode === "mock") {
    cells.push(
      { name: "chosen", tone: "practice", value: funnel.chosen },
      { name: "practice", tone: "practice", value: funnel.used },
    );
    return cells;
  }
  cells.push(
    { name: "chosen", tone: "ai", value: funnel.chosen },
    { name: "sent", tone: "ai", value: funnel.sent },
    { name: "used", tone: "ai", value: funnel.used },
  );
  return cells;
}

function fillFunnel(list) {
  clear(list);
  const cells = funnelCells();
  const base = Math.max(1, cells[0].value);
  for (const cell of cells) {
    const item = h(
      "li",
      { dataset: { tone: cell.tone, cell: cell.name } },
      h("span", { className: "g-stat__value" }, fmt.int(cell.value)),
      h("span", { className: "g-stat__label" }, funnelLabel(cell.name)),
    );
    item.style.setProperty("--share", String(Math.min(1, cell.value / base)));
    list.append(item);
  }
}

function renderIntro() {
  const list = byId("story-headline");
  clear(list);
  state.story.headline.forEach((ref, index) => {
    list.append(h("li", { className: index === 0 ? "story-headline-lead" : "" }, refNodes(ref)));
  });
  fillFunnel(byId("story-intro-funnel"));
}

// ---------------------------------------------------------------- narration card

function rulesLane(moment) {
  return h(
    "section",
    { className: "g-lane g-lane--rules story-lane" },
    h("p", { className: "story-lane-text" }, refNodes(moment.rules_line)),
  );
}

// Step 2 of the chain: what the AI (or the practice answer) said. The tag shape repeats the
// bar keys below: a circle for the AI, a diamond for practice, a dashed ring for no effect.
function aiLane(moment) {
  const tone = aiTone(moment.status);
  const lane = h(
    "section",
    { className: `g-lane g-lane--${tone} story-lane story-lane--ai` },
    h(
      "p",
      { className: "story-lane-text story-lane-lead" },
      h("span", { className: `story-tag story-tag--${tone}`, attrs: { "aria-hidden": "true" } }),
      h("span", {}, refNodes(moment.ai_clause)),
    ),
  );
  if (moment.labels) {
    const setAside = moment.status === "unsure" || moment.status === "warning";
    let chipTone = "dashed";
    if (!setAside && tone === "ai") {
      chipTone = "ai";
    } else if (!setAside && tone === "practice") {
      chipTone = "practice";
    }
    if (setAside) {
      lane.append(h("p", { className: "story-set-aside" }, t("story.labels.set_aside")));
    }
    lane.append(
      h(
        "ul",
        { className: "g-chips story-chips" },
        ...DIMENSIONS.map((dimension) =>
          h("li", { className: `g-chip g-chip--${chipTone}` }, chipText(dimension, moment.labels[dimension])),
        ),
      ),
    );
  }
  return lane;
}

function clampFor(kind, value) {
  const low = kind === "liking" ? -1 : 0;
  return Math.min(1, Math.max(low, Number(value) || 0));
}

function pairRow(kind, tone, label, value, valueNode, start = null) {
  const track = h("span", { className: "g-pair__track" }, h("span", { className: "g-pair__fill" }));
  const row = h(
    "div",
    { className: `g-pair__row g-pair__row--${tone}` },
    h("span", { className: "g-pair__label" }, h("span", { className: `story-key story-key--${tone}`, attrs: { "aria-hidden": "true" } }), label),
    track,
    valueNode,
  );
  row.style.setProperty("--value", String(clampFor(kind, value)));
  if (start !== null) {
    track.append(h("span", { className: "story-start-mark" }));
    row.style.setProperty("--start", String(clampFor(kind, start)));
  }
  return row;
}

function valueNode(value) {
  const node = numberNode(value, 2);
  node.classList.add("g-pair__value");
  return node;
}

// One bar and its two-decimal number. Liking runs on -1..+1 around a centred zero axis,
// ad memory on 0..1. The track is a left-to-right island in every language.
// With a "from" value the bar starts where the person stood before the ad and then moves
// to where they ended (a CSS transition); under reduced motion it simply shows the end.
function barCell(kind, value, from = null) {
  const box = h(
    "span",
    { className: "story-bar-box" },
    h("span", { className: "g-pair__track", attrs: { "aria-hidden": "true" } }, h("span", { className: "g-pair__fill" })),
    valueNode(value),
  );
  const target = String(clampFor(kind, value));
  if (from === null || prefersReducedMotion()) {
    box.style.setProperty("--value", target);
  } else {
    box.style.setProperty("--value", String(clampFor(kind, from)));
    requestAnimationFrame(() => requestAnimationFrame(() => box.style.setProperty("--value", target)));
  }
  return h("td", { className: `story-bar story-bar--${kind}` }, box);
}

// Paired bars as one small table: "Rules only" first, "With AI" second; columns liking and
// ad memory. Screen readers get the same numbers as row and column headers.
function barsTable(caption, rows) {
  return h(
    "table",
    { className: "story-bars" },
    h("caption", { className: "g-visually-hidden" }, caption),
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("td", { className: "story-bars-corner", attrs: { "aria-hidden": "true" } }, caption),
        h("th", { attrs: { scope: "col" } }, t("common.liking")),
        h("th", { attrs: { scope: "col" } }, t("common.memory")),
      ),
    ),
    h(
      "tbody",
      {},
      ...rows.map((row) =>
        h(
          "tr",
          { className: `story-bars-row g-pair__row--${row.tone}` },
          h(
            "th",
            { attrs: { scope: "row" } },
            h("span", { className: `story-key story-key--${row.tone}`, attrs: { "aria-hidden": "true" } }),
            row.label,
          ),
          barCell("liking", row.liking, row.from ? row.from.liking : null),
          barCell("memory", row.memory, row.from ? row.from.memory : null),
        ),
      ),
    ),
  );
}

// Name the direction liking actually moved, from the rules-only change and the change with AI.
function likingEffectKey(effect, rule, applied) {
  if (effect === "higher") {
    if (rule >= 0) return "story.effect.liking.rose_more";
    return applied <= 0 ? "story.effect.liking.fell_less" : "story.effect.liking.rose_not_fell";
  }
  if (rule <= 0) return "story.effect.liking.fell_more";
  return applied >= 0 ? "story.effect.liking.rose_less" : "story.effect.liking.fell_not_rose";
}

function effectNote(moment) {
  const effect = moment.effect;
  if (!effect || (effect.liking === "same" && effect.memory === "same")) {
    return null;
  }
  const parts = [];
  if (effect.liking !== "same" && moment.rule_delta && moment.applied_delta) {
    parts.push(
      t(likingEffectKey(effect.liking, moment.rule_delta.liking, moment.applied_delta.liking)),
    );
  } else if (effect.liking === "same") {
    parts.push(t("story.effect.liking.same"));
  }
  if (effect.memory !== "same") {
    parts.push(t(`story.effect.memory.${effect.memory}`));
  }
  return h("p", { className: "g-pair__note story-effect" }, parts.join(" "));
}

// Step 3 of the chain: where the person ends up with AI, against rules only.
function withLane(moment) {
  const tone = aiTone(moment.status);
  const lane = h(
    "section",
    { className: `g-lane g-lane--${tone} story-lane story-lane--with` },
    h("p", { className: "story-lane-text" }, refNodes(moment.with_ai_line)),
  );
  // The plain-words effect comes before the bars, so it stays in view on a small projector.
  const note = effectNote(moment);
  if (note) {
    lane.append(note);
  }
  if (moment.after) {
    const aiRow = tone === "none" ? "neutral" : tone;
    lane.append(
      barsTable(t("story.effect.title"), [
        {
          tone: "rules",
          label: t("common.rules_only"),
          liking: moment.after.rules.liking,
          memory: moment.after.rules.memory,
          from: moment.before ? moment.before.rules : null,
        },
        {
          tone: aiRow,
          label: t("common.with_ai"),
          liking: moment.after.with_ai.liking,
          memory: moment.after.with_ai.memory,
          from: moment.before ? moment.before.with_ai : null,
        },
      ]),
    );
  }
  return lane;
}

function three(value, signed = false) {
  return { num: value, decimals: 3, signed };
}

function mathDisclosure(moment) {
  const details = h(
    "details",
    { className: "g-details story-math" },
    h("summary", {}, t("story.math.toggle")),
  );
  details.open = state.mathOpen;
  details.addEventListener("toggle", () => {
    state.mathOpen = details.open;
  });
  const body = h("div", { className: "story-math-body" });
  if (moment.rule_delta) {
    body.append(
      h(
        "p",
        {},
        resolveNodes(
          {
            key: "story.math.rules",
            params: {
              liking: three(moment.rule_delta.liking, true),
              memory: three(moment.rule_delta.memory, true),
            },
          },
          state.ctx,
        ),
      ),
    );
  }
  const math = moment.math;
  if (math && moment.rule_delta && moment.applied_delta) {
    body.append(
      h(
        "p",
        {},
        resolveNodes(
          {
            key: "story.math.ai.liking",
            params: {
              rule: three(moment.rule_delta.liking),
              adjust: three(math.affect_adjustment, true),
              relevance: three(math.relevance_factor),
              credibility: three(math.credibility_factor),
              raw: three(math.liking_raw),
              limit: three(math.liking_limit),
              applied: three(moment.applied_delta.liking),
            },
          },
          state.ctx,
        ),
      ),
      h(
        "p",
        {},
        resolveNodes(
          {
            key: "story.math.ai.memory",
            params: {
              rule: three(moment.rule_delta.memory),
              relevance: three(math.relevance_factor),
              credibility: three(math.credibility_factor),
              salience: three(math.salience_factor),
              raw: three(math.memory_raw),
              min: three(math.memory_min),
              max: three(math.memory_max),
              applied: three(moment.applied_delta.memory),
            },
          },
          state.ctx,
        ),
      ),
    );
  }
  // How each change moves the values: liking adds it; ad memory moves that share of the
  // remaining way to 1 (the simulator's update rule), so the numbers above add up.
  for (const [key, track, change] of [
    ["story.math.update.rules", "rules", moment.rule_delta],
    ["story.math.update.with_ai", "with_ai", moment.applied_delta],
  ]) {
    if (!change || !moment.before || !moment.after) continue;
    // Each equation is one left-to-right island (also in Persian), and "≈" because every
    // number is rounded on its own to three decimals.
    const dec = (value) => fmt.dec(value, 3);
    const before = moment.before[track];
    const after = moment.after[track];
    const one = fmt.int(1);
    body.append(
      h(
        "p",
        { className: "story-math-update" },
        resolveNodes(
          {
            key,
            params: {
              liking: { text: `${dec(before.liking)} + (${dec(change.liking)}) ≈ ${dec(after.liking)}` },
              memory: {
                text: `${dec(before.memory)} + ${dec(change.memory)} × (${one} − ${dec(before.memory)}) ≈ ${dec(after.memory)}`,
              },
              ...(track === "rules" ? { one: { int: 1 } } : {}),
            },
          },
          state.ctx,
        ),
      ),
    );
  }
  details.append(body);
  return details;
}

function renderCard(step) {
  const { person, moment } = step;
  const card = byId("story-card");
  clear(card);
  card.dataset.status = moment.status;
  card.dataset.agent = person.agent_id;
  card.dataset.moment = moment.moment_id;
  card.append(
    h(
      "header",
      { className: "story-card-head" },
      mark(colorOf(person), true),
      h("h2", { className: "story-card-person" }, refNodes(personRef(person))),
      h("span", { className: "g-badge story-badge", dataset: { status: moment.status } }, t(`status.${moment.status}`)),
    ),
    h("p", { className: "story-card-sentence" }, refNodes(moment.sentence)),
  );
  if (moment.noticed) {
    card.append(rulesLane(moment), aiLane(moment), withLane(moment), mathDisclosure(moment));
  }
  restartEnter(card);
}

// ---------------------------------------------------------------- People tab

// The most common value of one label over this person's answered moments; a tie names every
// tied value ("skeptical / annoyed"), so two different answers are never shown as one.
function modeText(person, dimension) {
  const counts = new Map();
  for (const moment of person.moments) {
    if (moment.labels && ["answered", "reused", "practice"].includes(moment.status)) {
      const value = moment.labels[dimension];
      counts.set(value, (counts.get(value) || 0) + 1);
    }
  }
  if (counts.size === 0) {
    return chipText(dimension, person.label_mode[dimension]);
  }
  const top = Math.max(...counts.values());
  const tied = VALUES[dimension].filter((value) => counts.get(value) === top);
  const values = tied.map((value) => t(`${dimension}.${value}`)).join(" / ");
  // When every answer differed, no value is "most common": say each came once.
  const once = top === 1 && tied.length > 1;
  return t("label.chip", {
    label: dimensionLabel(dimension),
    value: once ? t("label.once_each", { values }) : values,
  });
}

function personLabels(person) {
  if (!person.label_mode) {
    return null;
  }
  const practice = state.story.ai.mode === "mock" || (state.story.labels && state.story.labels.group === "practice");
  const title = practice
    ? tn("story.person.labels.practice", person.label_answers)
    : tn("story.person.labels", person.label_answers);
  const tone = practice ? "practice" : "ai";
  return h(
    "div",
    { className: "story-person-labels" },
    h("p", { className: "story-person-labels-title" }, title),
    h(
      "ul",
      { className: "g-chips" },
      ...DIMENSIONS.map((dimension) =>
        h("li", { className: `g-chip g-chip--${tone}` }, modeText(person, dimension)),
      ),
    ),
  );
}

// A person card is narrow (four per row on wide screens), so each measure gets its own
// pair of bars with the comparison word right under it.
function pair(kind, title, rows, note) {
  return h(
    "div",
    { className: `g-pair g-pair--${kind}` },
    h("p", { className: "g-pair__title" }, title),
    ...rows.map((row) => pairRow(kind, row.tone, row.label, row.value, valueNode(row.value))),
    h("p", { className: "story-compare" }, note),
  );
}

function personEnd(person) {
  const aiRow = modeTone();
  const end = person.end;
  const compare = (measure, bucket) => t("story.person.compare", { measure, compare: t(`compare.${bucket}`) });
  return h(
    "section",
    { className: "story-person-end" },
    h("h4", { className: "story-person-end-title" }, t("story.person.end")),
    pair(
      "liking",
      t("common.liking"),
      [
        { tone: "rules", label: t("common.rules_only"), value: end.rules.liking },
        { tone: aiRow, label: t("common.with_ai"), value: end.with_ai.liking },
      ],
      compare(t("common.liking"), person.compare.liking),
    ),
    pair(
      "memory",
      t("common.memory"),
      [
        { tone: "rules", label: t("common.rules_only"), value: end.rules.memory },
        { tone: aiRow, label: t("common.with_ai"), value: end.with_ai.memory },
      ],
      compare(t("common.memory"), person.compare.memory),
    ),
    h(
      "p",
      { className: "story-interest" },
      h("span", { className: "story-interest-label" }, t("common.purchase_interest")),
      numberNode(end.rules.purchase_interest, 2),
    ),
    h("p", { className: "g-pair__note" }, t("common.purchase_interest_note")),
  );
}

function personCard(person) {
  const ci = colorOf(person);
  const follow = h(
    "button",
    { className: "g-btn g-btn--secondary story-follow", attrs: { type: "button" }, dataset: { agent: person.agent_id } },
    resolveNodes({ key: "story.person.follow", params: { name: { person: person.agent_id } } }, state.ctx),
  );
  setDisabled(follow, person.moments.length === 0);
  follow.addEventListener("click", (event) => {
    letGo(event);
    if (!isDisabled(follow)) {
      followPerson(person.agent_id);
    }
  });
  return h(
    "article",
    { className: `g-card story-person story-tone-c${ci % 10}`, dataset: { agent: person.agent_id } },
    h("h3", { className: "story-person-name" }, mark(ci), h("span", {}, refNodes(personRef(person)))),
    h("p", { className: "story-person-summary" }, refNodes(person.summary)),
    personLabels(person),
    personEnd(person),
    h("p", { className: "story-person-actions" }, follow),
  );
}

function renderPeople() {
  const host = byId("story-people");
  clear(host);
  const personas = [...state.story.personas].sort((a, b) => a.color_index - b.color_index);
  for (const persona of personas) {
    const members = persona.agent_ids
      .map((agentId) => state.people.get(agentId))
      .filter(Boolean)
      .sort((a, b) => a.number - b.number);
    if (!members.length) {
      continue;
    }
    const head = h(
      "header",
      { className: "story-group-head" },
      h("h2", { className: "story-group-title" }, mark(persona.color_index, true), personaName(persona)),
      h("p", { className: "story-group-count g-meta" }, tn("report.persona.count", members.length)),
    );
    const about = h("div", { className: "story-group-about" });
    if (persona.template_key) {
      about.append(h("p", { className: "story-group-tagline" }, t(`persona.${persona.template_key}.tagline`)));
    }
    if (persona.trait_chips.length) {
      about.append(
        h(
          "ul",
          { className: "g-chips story-traits" },
          ...persona.trait_chips.map((chip) => h("li", { className: "g-chip" }, t(`trait.${chip.trait}.${chip.level}`))),
        ),
      );
    }
    if (persona.interests.length) {
      about.append(
        h(
          "p",
          { className: "story-group-interests g-meta" },
          resolveNodes({ key: "report.person.likes", params: { interests: interestsParam(persona.interests) } }, state.ctx),
        ),
      );
    }
    host.append(
      h(
        "section",
        { className: `story-group story-tone-c${persona.color_index % 10}`, dataset: { persona: persona.persona_id } },
        head,
        about,
        h("div", { className: "story-people-grid" }, ...members.map(personCard)),
      ),
    );
  }
}

// ---------------------------------------------------------------- Summary tab

function block(title, ...children) {
  return h(
    "section",
    { className: "story-block" },
    title ? h("h2", { className: "story-block-title" }, title) : null,
    ...children,
  );
}

function spread(dimension, counts, total, practice) {
  const values = VALUES[dimension].filter((value) => (counts[value] || 0) > 0);
  const bar = h("div", {
    className: "story-spread-bar",
    attrs: { "aria-hidden": "true", dir: "ltr" },
    dataset: { tone: practice ? "practice" : "ai" },
  });
  for (const value of values) {
    const segment = h("span", { className: "story-spread-seg", dataset: { dimension, value } });
    segment.style.setProperty("--share", String(counts[value] / Math.max(1, total)));
    bar.append(segment);
  }
  return h(
    "div",
    { className: "story-spread", dataset: { dimension, tone: practice ? "practice" : "ai" } },
    h("p", { className: "story-spread-name" }, dimensionLabel(dimension)),
    bar,
    h(
      "ul",
      { className: "g-chips story-spread-chips" },
      ...values.map((value) =>
        h(
          "li",
          { className: "g-chip story-spread-chip", dataset: { dimension, value } },
          h("span", { className: "story-swatch", dataset: { dimension, value }, attrs: { "aria-hidden": "true" } }),
          t("label.chip", { label: t(`${dimension}.${value}`), value: counts[value] }),
        ),
      ),
    ),
  );
}

function labelsBlock() {
  const labels = state.story.labels;
  const practice = Boolean(labels && labels.group === "practice");
  const title = practice ? t("story.summary.labels.title.practice") : t("story.summary.labels.title");
  if (!labels || labels.answers === 0) {
    return block(title, h("p", { className: "g-muted" }, t("story.summary.labels.none")));
  }
  const parts = DIMENSIONS.map((dimension) => spread(dimension, labels[dimension], labels.answers, practice));
  const flags = FLAGS.filter((flag) => (labels.safety_flags[flag] || 0) > 0);
  if (flags.length) {
    parts.push(
      h(
        "ul",
        { className: "g-chips story-flags" },
        ...flags.map((flag) =>
          h("li", { className: "g-chip g-chip--dashed" }, t("label.chip", { label: t(`flag.${flag}`), value: labels.safety_flags[flag] })),
        ),
      ),
    );
  }
  parts.push(h("p", { className: "g-meta" }, tn("story.summary.labels.note", labels.answers)));
  return block(title, h("div", { className: "story-spreads" }, ...parts));
}

function rangeNode(start, end) {
  return h(
    "span",
    { className: "g-pair__value story-range" },
    numberNode(start, 2),
    h("span", { className: "g-dir story-arrow", attrs: { "aria-hidden": "true" } }, "→"),
    numberNode(end, 2),
  );
}

function averagePair(kind, title, rows, note) {
  return h(
    "div",
    { className: `g-pair g-pair--${kind}` },
    h("p", { className: "g-pair__title" }, title),
    ...rows.map((row) => pairRow(kind, row.tone, row.label, row.end, rangeNode(row.start, row.end), row.start)),
    note ? h("p", { className: "g-pair__note" }, note) : null,
  );
}

function averagesBlock() {
  const averages = state.story.averages;
  const tone = modeTone();
  const scaleLiking = resolveNodes(
    {
      key: "common.scale.liking",
      params: { min: { num: -1, decimals: 0, signed: true }, max: { num: 1, decimals: 0, signed: true } },
    },
    state.ctx,
  );
  const scaleMemory = resolveNodes(
    { key: "common.scale.memory", params: { min: { num: 0, decimals: 0 }, max: { num: 1, decimals: 0 } } },
    state.ctx,
  );
  return block(
    t("story.summary.averages.title"),
    h(
      "p",
      { className: "story-range-legend g-meta" },
      t("story.summary.start"),
      h("span", { className: "g-dir story-arrow", attrs: { "aria-hidden": "true" } }, "→"),
      t("story.summary.end"),
    ),
    h(
      "div",
      { className: "story-averages" },
      averagePair(
        "liking",
        t("common.liking"),
        [
          { tone: "rules", label: t("common.rules_only"), ...averages.rules.liking },
          { tone, label: t("common.with_ai"), ...averages.with_ai.liking },
        ],
        scaleLiking,
      ),
      averagePair(
        "memory",
        t("common.memory"),
        [
          { tone: "rules", label: t("common.rules_only"), ...averages.rules.memory },
          { tone, label: t("common.with_ai"), ...averages.with_ai.memory },
        ],
        scaleMemory,
      ),
      averagePair(
        "memory",
        t("common.purchase_interest"),
        [{ tone: "rules", label: t("common.rules_only"), ...averages.rules.purchase_interest }],
        t("common.purchase_interest_note"),
      ),
    ),
  );
}

function coverageLines() {
  const ai = state.story.ai;
  if (!PROVIDER_MODES.has(ai.mode)) {
    return [];
  }
  const coverage = ai.coverage;
  const statuses = ai.statuses;
  const lines = [
    h(
      "p",
      { className: "g-meta story-coverage" },
      resolveNodes(
        {
          key: "story.summary.coverage",
          params: {
            noticed: { int: coverage.noticed },
            chosen: { int: coverage.chosen },
            sent: { int: coverage.sent },
            answered: { int: statuses.answered },
            reused: { int: statuses.reused },
            failed: { int: statuses.failed },
            set_aside: { int: statuses.unsure + statuses.warning },
            over_limit: { int: statuses.over_limit },
            not_chosen: { int: statuses.not_chosen },
          },
        },
        state.ctx,
      ),
    ),
  ];
  if (statuses.over_limit > 0) {
    // The drop from "chosen" to "sent" in the funnel above is the AI limit; say so.
    lines.unshift(
      h(
        "p",
        { className: "story-funnel-over" },
        tn("story.summary.funnel.over", statuses.over_limit, { limit: ai.funded_limit }),
      ),
    );
  }
  if (typeof ai.model_id === "string" && ai.model_id) {
    const usage = ai.usage_this_run;
    lines.push(
      h(
        "p",
        { className: "g-meta story-provenance" },
        resolveNodes(
          {
            key: "story.summary.provenance",
            params: {
              model: { id: ai.model_id },
              limit: { int: ai.funded_limit },
              attempts: { int: usage.attempts },
              prompt: { int: usage.prompt_tokens },
              completion: { int: usage.completion_tokens },
            },
          },
          state.ctx,
        ),
      ),
    );
  }
  return lines;
}

function renderSummary() {
  const host = byId("story-summary");
  clear(host);
  const funnel = h("ol", { className: "g-funnel story-summary-funnel" });
  fillFunnel(funnel);
  host.append(
    block(t("story.summary.funnel.title"), funnel, ...coverageLines()),
    block(t("story.days.title"), daysTable("story-summary-days")),
    labelsBlock(),
    averagesBlock(),
  );
  const ai = state.story.ai;
  byId("story-limits-vendor").hidden = !PROVIDER_MODES.has(ai.mode);
  const inCharacter = PROVIDER_MODES.has(ai.mode) && ai.contract.prompt_contract_id === PERSONA_PROMPT;
  for (const id of PERSONA_VARIANTS) {
    byId(id).hidden = inCharacter;
    byId(`${id}-persona`).hidden = !inCharacter;
  }
}

// ---------------------------------------------------------------- steps, filter and ticks

function personSteps(agentId) {
  return state.allSteps.filter((step) => step.person.agent_id === agentId);
}

function renderFilter() {
  const option = byId("story-filter-person");
  if (state.followed) {
    const person = state.people.get(state.followed);
    clear(option);
    // The name is an isolated island, so a right-to-left name keeps its number in place.
    option.append(resolveNodes({ key: "story.filter.person", params: { name: { person: person.agent_id } } }, state.ctx));
    option.hidden = false;
  } else {
    clear(option);
    option.hidden = true;
  }
  for (const button of byId("story-filter").querySelectorAll("[role='radio']")) {
    const checked = button.dataset.filter === state.filter;
    button.setAttribute("aria-checked", checked ? "true" : "false");
    button.tabIndex = checked ? 0 : -1;
  }
}

function renderTicks() {
  const ticks = byId("story-ticks");
  clear(ticks);
  const steps = state.steps;
  ticks.hidden = steps.length < 2 || steps.length > MAX_TICKS;
  if (ticks.hidden) {
    return;
  }
  for (const step of steps) {
    ticks.append(h("li", { className: "story-tick", dataset: { status: step.moment.status } }));
  }
  // The closing step has the slider's last position, so it gets the last mark.
  ticks.append(h("li", { className: "story-tick story-tick--end", dataset: { status: "end" } }));
}

function setFilter(name, agentId = null, { focusStep = null } = {}) {
  const ended = atOutro();
  const current = state.index >= 0 && !ended ? state.steps[state.index] : null;
  state.filter = name;
  state.followed = name === "person" ? agentId : state.followed;
  if (name === "key") {
    state.steps = state.keySteps;
  } else if (name === "all") {
    state.steps = state.allSteps;
  } else {
    state.steps = personSteps(state.followed);
  }
  renderFilter();
  renderTicks();
  let index = -1;
  if (focusStep !== null) {
    index = state.steps.length ? focusStep : -1;
  } else if (ended) {
    index = lastIndex();
  } else if (current) {
    index = state.steps.indexOf(current);
    if (index < 0) {
      const later = state.steps.findIndex((step) => step.moment.minute >= current.moment.minute);
      index = later >= 0 ? later : state.steps.length - 1;
    }
  }
  goTo(index, { user: true });
}

function followPerson(agentId) {
  if (state.playing) {
    pause();
  }
  selectTab("story");
  setFilter("person", agentId, { focusStep: 0 });
}

// ---------------------------------------------------------------- playback engine

function stopTween() {
  if (state.frame) {
    cancelAnimationFrame(state.frame);
    state.frame = 0;
  }
}

function paintClock(minute) {
  byId("story-clock").textContent = fmt.clock(Math.max(0, Math.floor(minute + 1e-9)), state.story.run.days);
}

function setMinute(minute) {
  state.minute = minute;
  if (state.map) {
    state.map.setTime(minute);
  }
  paintClock(minute);
}

function ease(u) {
  return u < 0.5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2;
}

// Model time for animation progress u in [0, 1]; idle stretches (nobody moving) are compressed.
function timeWarp(from, to) {
  const low = Math.min(from, to);
  const high = Math.max(from, to);
  const pieces = [];
  let cursor = low;
  for (const [start, end] of state.intervals) {
    if (end <= low || start >= high) {
      continue;
    }
    const a = Math.max(start, low);
    const b = Math.min(end, high);
    if (a > cursor) {
      pieces.push([cursor, a, false]);
    }
    if (b > a) {
      pieces.push([a, b, true]);
    }
    cursor = Math.max(cursor, b);
  }
  if (cursor < high) {
    pieces.push([cursor, high, false]);
  }
  const active = pieces.reduce((sum, [a, b, moving]) => sum + (moving ? b - a : 0), 0);
  const idle = pieces.reduce((sum, [a, b, moving]) => sum + (moving ? 0 : b - a), 0);
  let idleWeight = 1;
  if (active > 0 && idle > 0) {
    idleWeight = Math.min(1, ((IDLE_SHARE / (1 - IDLE_SHARE)) * active) / idle);
  }
  const weights = pieces.map(([a, b, moving]) => (b - a) * (moving ? 1 : idleWeight));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (!pieces.length || total <= 0) {
    return (u) => from + (to - from) * u;
  }
  return (u) => {
    let remaining = (from <= to ? u : 1 - u) * total;
    for (let index = 0; index < pieces.length; index += 1) {
      const [a, b] = pieces[index];
      if (remaining <= weights[index] || index === pieces.length - 1) {
        const fraction = weights[index] > 0 ? Math.min(1, remaining / weights[index]) : 1;
        return a + (b - a) * fraction;
      }
      remaining -= weights[index];
    }
    return to;
  };
}

function moveTo(target, animate) {
  stopTween();
  const from = state.minute;
  if (!animate || prefersReducedMotion() || Math.abs(target - from) < 1e-9) {
    setMinute(target);
    arrived();
    return 0;
  }
  const duration = Math.min(TWEEN_MAX_MS, Math.abs(target - from) * TWEEN_MS_PER_MINUTE);
  const warp = timeWarp(from, target);
  const started = performance.now();
  const frame = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    setMinute(progress >= 1 ? target : warp(ease(progress)));
    if (progress < 1) {
      state.frame = requestAnimationFrame(frame);
    } else {
      state.frame = 0;
      arrived();
    }
  };
  state.frame = requestAnimationFrame(frame);
  return duration;
}

function arrived() {
  if (state.index >= 0 && !atOutro() && state.map && !prefersReducedMotion()) {
    state.map.pulse();
  }
}

function updateControls() {
  const total = state.steps.length;
  const index = state.index;
  const ended = atOutro();
  // The opening is not a moment, so it names how many follow instead of "moment 0"; the
  // closing step names how many there were.
  let counter;
  if (index < 0) {
    counter = withDay(tn("story.counter.intro", total), Math.floor(introMinute() / 1440));
  } else if (ended) {
    counter = withDay(tn("story.counter.outro", total), state.story.run.days - 1);
  } else {
    counter = withDay(t("story.counter", { index: index + 1, total }), state.steps[index].moment.day_index);
  }
  byId("story-counter").textContent = counter;
  const slider = byId("story-slider");
  slider.max = String(Math.max(0, lastIndex()));
  slider.value = String(Math.max(0, index));
  slider.disabled = total === 0;
  slider.setAttribute("aria-valuetext", counter);
  const root = byId("story-root");
  root.dataset.intro = index < 0 ? "true" : "false";
  root.dataset.outro = ended ? "true" : "false";
  setDisabled(byId("story-prev"), index < 0);
  setDisabled(byId("story-restart"), index < 0);
  setDisabled(byId("story-next"), index >= lastIndex());
  setDisabled(byId("story-play"), total === 0);
  setDisabled(byId("story-intro-play"), total === 0);
  const ticks = byId("story-ticks").children;
  for (let position = 0; position < ticks.length; position += 1) {
    ticks[position].dataset.current = position === index ? "true" : "false";
  }
}

function updatePlayButton() {
  byId("story-play-text").textContent = state.playing ? t("story.pause") : t("story.play");
  byId("story-play-icon").hidden = state.playing;
  byId("story-pause-icon").hidden = !state.playing;
  byId("story-root").dataset.playing = state.playing ? "true" : "false";
}

function scheduleDwell(delay) {
  window.clearTimeout(state.dwell);
  if (!state.playing) {
    return;
  }
  state.dwell = window.setTimeout(() => {
    if (!state.playing) {
      return;
    }
    if (state.index < lastIndex()) {
      goTo(state.index + 1, { user: false });
    } else {
      pause();
    }
  }, delay + DWELL_MS);
}

function goTo(requested, { animate = true, user = false } = {}) {
  const total = state.steps.length;
  const index = Math.max(-1, Math.min(requested, lastIndex()));
  state.index = index;
  const step = index >= 0 && index < total ? state.steps[index] : null;
  const ended = atOutro();
  const intro = byId("story-intro");
  const card = byId("story-card");
  const outro = byId("story-outro");
  intro.hidden = index >= 0;
  card.hidden = !step;
  outro.hidden = !ended;
  const canvas = byId("story-map");
  const count = state.story.people.length;
  if (step) {
    renderCard(step);
    const label = personText(step.person);
    state.map.highlight(step.person.agent_id, step.moment.status);
    state.map.setLabel(label);
    canvas.setAttribute("aria-label", t("story.map.label", { count, name: label }));
  } else {
    state.map.highlight(null);
    // Nobody is highlighted: the opening headline describes who is on the map.
    canvas.setAttribute("aria-label", resolveText(state.story.headline[0], state.ctx));
    restartEnter(ended ? outro : intro);
  }
  byId("story-narration").scrollTop = 0;
  let target = introMinute();
  if (step) {
    target = step.moment.minute;
  } else if (ended) {
    target = outroMinute();
  }
  updateControls();
  const duration = moveTo(target, animate);
  if (step) {
    const sentence = resolveText(step.moment.sentence, state.ctx);
    announce(sentence, { force: user });
  } else if (ended) {
    announce(t("story.outro.title"), { force: user });
  }
  scheduleDwell(duration);
}

function step(delta) {
  const target = state.index + delta;
  if (target < -1 || target > lastIndex()) {
    return;
  }
  goTo(target, { user: true });
}

function play() {
  if (!state.steps.length) {
    return;
  }
  state.playing = true;
  updatePlayButton();
  if (state.index < 0 || state.index >= lastIndex()) {
    goTo(0, { user: true });
  } else {
    scheduleDwell(0);
  }
}

function pause() {
  state.playing = false;
  window.clearTimeout(state.dwell);
  updatePlayButton();
}

function togglePlay() {
  if (state.playing) {
    pause();
  } else {
    play();
  }
}

// ---------------------------------------------------------------- tabs, keys and layout

// focusTab moves focus to the tab itself (arrow keys inside the tab list). Otherwise focus
// stays where it is, unless it was inside a part that is now hidden: then the visible panel
// takes it, so the arrow keys keep stepping through the story instead of switching tabs.
function selectTab(name, focusTab = false) {
  state.tab = name;
  const root = byId("story-root");
  root.dataset.tab = name;
  for (const tab of document.querySelectorAll(".story-tab")) {
    const selected = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
    byId(tab.getAttribute("aria-controls")).hidden = !selected;
    if (selected && focusTab) {
      tab.focus();
    }
  }
  byId("story-filter-wrap").hidden = name !== "story";
  const active = document.activeElement;
  if (!focusTab && active instanceof Element && active.closest("[role='tabpanel'][hidden], #story-filter-wrap[hidden]")) {
    byId(`story-pane-${name}`).focus({ preventScroll: true });
  }
  if (name !== "story" && state.playing) {
    pause();
  }
  if (name === "story" && state.map) {
    state.map.resize();
  }
}

function moveInGroup(buttons, current, delta) {
  const visible = buttons.filter((button) => !button.hidden);
  const position = visible.indexOf(current);
  if (position < 0) {
    return null;
  }
  return visible[(position + delta + visible.length) % visible.length];
}

function compositeKey(event, buttons, activate) {
  const rtl = dir() === "rtl";
  let delta = 0;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    delta = event.key === "ArrowRight" && rtl ? -1 : 1;
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    delta = event.key === "ArrowLeft" && rtl ? 1 : -1;
  } else {
    // Home and End are optional in the tab pattern; here Home always means "back to the intro".
    return;
  }
  const target = moveInGroup(buttons, event.target.closest("button"), delta);
  if (target) {
    event.preventDefault();
    activate(target);
  }
}

function perform(action) {
  if (action.startsWith("tab-")) {
    selectTab(action.slice(4));
    return;
  }
  if (state.tab !== "story") {
    selectTab("story");
  }
  if (action === "next") {
    step(1);
  } else if (action === "previous") {
    step(-1);
  } else if (action === "toggle") {
    togglePlay();
  } else if (action === "intro") {
    goTo(-1, { user: true });
  }
}

function onKey(event) {
  if (!state.ready || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) {
    return;
  }
  const target = event.target instanceof Element ? event.target : null;
  if (target && target.closest("input, textarea, select, [contenteditable]")) {
    return;
  }
  const composite = Boolean(target && target.closest("[role='tablist'], [role='radiogroup']"));
  const activatable = Boolean(target && target.closest("button, a, summary"));
  const rtl = dir() === "rtl";
  let action = null;
  switch (event.key) {
    case "PageDown":
      action = "next";
      break;
    case "PageUp":
      action = "previous";
      break;
    case "ArrowRight":
      action = composite ? null : rtl ? "previous" : "next";
      break;
    case "ArrowLeft":
      action = composite ? null : rtl ? "next" : "previous";
      break;
    case " ":
      action = activatable ? null : "toggle";
      break;
    case "Home":
      action = "intro";
      break;
    case "1":
      action = "tab-story";
      break;
    case "2":
      action = "tab-people";
      break;
    case "3":
      action = "tab-summary";
      break;
    default:
      action = null;
  }
  if (!action) {
    return;
  }
  event.preventDefault();
  perform(action);
}

function fitHeight() {
  const app = byId("story-app");
  if (app.hidden) {
    return;
  }
  const top = app.getBoundingClientRect().top + window.scrollY;
  byId("story-root").style.setProperty("--story-top", `${Math.max(0, Math.round(top))}px`);
}

// A mouse or touch press leaves focus on the control it pressed, where Space and the arrow
// keys would act on that control instead of on the story. Let go of it then, so the
// presenter's keys keep stepping; a keyboard press (detail 0) keeps its focus.
function letGo(event) {
  const target = event.currentTarget;
  if (event.detail > 0 && target instanceof HTMLElement && document.activeElement === target) {
    target.blur();
  }
}

function onPress(id, action) {
  byId(id).addEventListener("click", (event) => {
    letGo(event);
    if (!isDisabled(event.currentTarget)) {
      action();
    }
  });
}

function bindOnce() {
  if (state.bound) {
    return;
  }
  state.bound = true;
  onPress("story-next", () => step(1));
  onPress("story-prev", () => step(-1));
  onPress("story-restart", () => goTo(-1, { user: true }));
  onPress("story-play", togglePlay);
  onPress("story-intro-play", play);
  const slider = byId("story-slider");
  slider.addEventListener("input", () => {
    goTo(Number(slider.value), { animate: false, user: true });
  });
  slider.addEventListener("pointerup", () => {
    if (document.activeElement === slider) {
      slider.blur();
    }
  });
  const tabs = [...document.querySelectorAll(".story-tab")];
  for (const tab of tabs) {
    tab.addEventListener("click", (event) => {
      selectTab(tab.dataset.tab);
      letGo(event);
    });
  }
  byId("story-tab-story").parentElement.addEventListener("keydown", (event) => {
    compositeKey(event, tabs, (target) => selectTab(target.dataset.tab, true));
  });
  const options = [...byId("story-filter").querySelectorAll("[role='radio']")];
  const choose = (button) => {
    if (button.dataset.filter !== state.filter || button.dataset.filter === "person") {
      setFilter(button.dataset.filter, state.followed);
    }
    button.focus();
  };
  for (const option of options) {
    option.addEventListener("click", (event) => {
      choose(option);
      letGo(event);
    });
  }
  byId("story-filter").addEventListener("keydown", (event) => compositeKey(event, options, choose));
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", fitHeight);
}

async function boot() {
  byId("story-retry").addEventListener("click", () => {
    load();
  });
  try {
    await initI18n();
  } catch (error) {
    console.error(error);
    showProblem("unexpected");
    return;
  }
  await load();
}

boot();
