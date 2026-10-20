// AdLife guided demo: translation, number formatting and message references.
//
// This module mirrors adlife.i18n (Python) rule for rule, so the story page and the
// server-rendered report say exactly the same sentences; the shared fixtures
// tests/unit/i18n/format_cases.json and ref_cases.json pin both implementations.
//
// Usage: await initI18n() once, then t()/tn() for page text, fmt.* for numbers and
// times, and resolveNodes()/resolveText() for message references from the story JSON.
// A missing key or parameter renders as the key in white square brackets (U+27E6,
// U+27E7) and is reported with console.error; the browser tests fail on any console
// error, so a defect is never silent.
//
// Plural messages: pass every parameter the "other" form uses, even when count is 1.
// Persian uses its "one" form for 0 and 1, and that form may show any of them.

import { getJson } from "./guide-core.js";

const LANGUAGES = Object.freeze(["en", "de", "fa"]);
const KEY_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)+$/;
const MAX_KEY_LENGTH = 96;
const MAX_MESSAGE_LENGTH = 600;
const PARAMETER_NAME_PATTERN = /^[a-z][a-z0-9_]*$/;
const MAX_PARAMETER_NAME_LENGTH = 32;
// Renderers split on the spec pattern; catalog validation forbids a leading underscore.
const PLACEHOLDER_SPLIT = /\{([a-z_][a-z0-9_]*)\}/;
const AGENT_ID_PATTERN = /^person-[0-9]{3}$/;
const ID_PARAM_PATTERN = /^[\x20-\x7e]+$/;
const PARAM_KINDS = Object.freeze(["text", "id", "key", "int", "num", "pct", "clock", "person", "list"]);
const PARAM_MEMBERS = Object.freeze({
  text: ["text"],
  id: ["id"],
  key: ["key", "count", "params"],
  int: ["int"],
  num: ["num", "decimals", "signed"],
  pct: ["pct", "decimals"],
  clock: ["clock"],
  person: ["person"],
  list: ["list"],
});
const MAX_REF_PARAMS = 12;
const MAX_REF_DEPTH = 3;
const MAX_LIST_ITEMS = 8;
const MAX_TEXT_PARAM_LENGTH = 240;
const MAX_ID_PARAM_LENGTH = 120;
const MAX_CLOCK_MINUTE = 7 * 1440;
const MINUTES_PER_DAY = 1440;
const MAX_DECIMALS = 6;
const MAX_DECIMAL_MAGNITUDE = 1e21;
const MINUS_SIGN = "\u2212";
const PERSIAN_ZERO = 0x06f0;
// [decimal separator, grouping separator]
const SEPARATORS = Object.freeze({
  en: Object.freeze([".", ","]),
  de: Object.freeze([",", "."]),
  fa: Object.freeze(["\u066b", "\u066c"]),
});
const MISSING_OPEN = "\u27e6";
const MISSING_CLOSE = "\u27e7";

class I18nError extends Error {
  constructor(message) {
    super(message);
    this.name = "I18nError";
  }
}

let activeCatalog = null;
let initPromise = null;

/**
 * Load the catalog for <html lang> from /guide/assets/i18n-<lang>.json, validate it and
 * make it the page's active catalog. Safe to call more than once.
 */
export async function initI18n() {
  if (initPromise === null) {
    initPromise = (async () => {
      const language = documentLanguage();
      const envelope = await getJson(`/guide/assets/i18n-${language}.json`);
      if (
        !isPlainObject(envelope)
        || Object.keys(envelope).sort().join(",") !== "direction,language,messages,schema_version"
        || envelope.schema_version !== 1
        || envelope.language !== language
        || envelope.direction !== directionOf(language)
      ) {
        throw new I18nError("the guide catalog envelope is invalid");
      }
      activeCatalog = createCatalog(language, envelope.messages);
      return activeCatalog;
    })();
    initPromise.catch(() => {
      initPromise = null;
    });
  }
  return initPromise;
}

/** The page language: "en", "de" or "fa". */
export function lang() {
  return activeCatalog === null ? documentLanguage() : activeCatalog.language;
}

/** The page direction: "rtl" for Persian, otherwise "ltr". */
export function dir() {
  return directionOf(lang());
}

/** A guide link that keeps the page language: path + "?lang=" + lang(). */
export function href(path) {
  if (
    typeof path !== "string"
    || !/^\/(?![/\\])/.test(path)
    || /[?#\s\\]/.test(path)
  ) {
    throw new TypeError("href() needs a local path without a query or fragment");
  }
  return `${path}?lang=${lang()}`;
}

/** Translate a message; integer parameters are localized. params.count selects a plural. */
export function t(key, params = {}) {
  return requireActive(key).t(key, params);
}

/** Translate a plural message for count; {count} shows the localized count. */
export function tn(key, count, params = {}) {
  return requireActive(key).tn(key, count, params);
}

/** Number and time formatting in the page language (the shared rounding algorithm). */
export const fmt = Object.freeze({
  int(n) {
    return requireActive("fmt.int").fmt.int(n);
  },
  dec(x, decimals, { signed = false } = {}) {
    return requireActive("fmt.dec").fmt.dec(x, decimals, { signed });
  },
  pct(r, decimals = 0) {
    return requireActive("fmt.pct").fmt.pct(r, decimals);
  },
  clock(minute, days) {
    return requireActive("fmt.clock").fmt.clock(minute, days);
  },
  duration(s) {
    return requireActive("fmt.duration").fmt.duration(s);
  },
});

/**
 * Build the context message references need: the run's day count and its people.
 * people: [{agent_id, template_key, display_name, number}]
 */
export function makeRefContext({ days, people }) {
  if (!Number.isInteger(days) || days < 1) throw new TypeError("makeRefContext() needs whole days");
  if (!Array.isArray(people)) throw new TypeError("makeRefContext() needs a people list");
  const byAgent = new Map();
  for (const person of people) {
    if (
      !isPlainObject(person)
      || typeof person.agent_id !== "string"
      || typeof person.display_name !== "string"
      || !Number.isInteger(person.number)
      || !(person.template_key === undefined || person.template_key === null
        || typeof person.template_key === "string")
    ) {
      throw new TypeError("makeRefContext() received an invalid person");
    }
    byAgent.set(person.agent_id, Object.freeze({
      templateKey: person.template_key ?? null,
      displayName: person.display_name,
      number: person.number,
    }));
  }
  return Object.freeze({ days, people: byAgent });
}

/** Resolve a message reference into text nodes and <bdi> elements. */
export function resolveNodes(ref, ctx) {
  return requireActive(refKey(ref)).resolveNodes(ref, ctx);
}

/** Resolve a message reference into plain text, the same words as resolveNodes(). */
export function resolveText(ref, ctx) {
  return requireActive(refKey(ref)).resolveText(ref, ctx);
}

/**
 * Build a catalog from a messages object (tests and initI18n). The result has the same
 * API as this module: {language, direction, has, t, tn, fmt, resolveNodes, resolveText}.
 */
export function createCatalog(language, messages) {
  if (!LANGUAGES.includes(language)) throw new I18nError("unsupported guide language");
  const table = validatedMessages(messages);
  const direction = directionOf(language);

  function template(key, count) {
    const message = table.get(key);
    if (message === undefined) throw new I18nError(`unknown message key ${key}`);
    if (typeof message === "string") return message;
    if (count === null) throw new I18nError(`message ${key} is plural and needs a count`);
    return pluralCategory(language, count) === "one" ? message.one : message.other;
  }

  function fill(key, count, values) {
    const nodes = [];
    template(key, count).split(PLACEHOLDER_SPLIT).forEach((part, index) => {
      if (index % 2 === 0) {
        if (part) nodes.push(part);
      } else if (Object.prototype.hasOwnProperty.call(values, part)) {
        nodes.push(values[part]);
      } else if (part === "count" && count !== null) {
        nodes.push(isolate("ltr", [formatInteger(count, language)]));
      } else {
        throw new I18nError(`message ${key} is missing its ${part} parameter`);
      }
    });
    return nodes;
  }

  function valueNodes(params) {
    if (!isPlainObject(params)) throw new I18nError("message parameters must be an object");
    const values = Object.create(null);
    for (const [name, value] of Object.entries(params)) {
      if (typeof value === "string") values[name] = isolate("auto", [value]);
      else if (Number.isInteger(value)) values[name] = isolate("ltr", [formatInteger(value, language)]);
      else throw new I18nError(`parameter ${name} must be text or a whole number`);
    }
    return values;
  }

  function percent(ratio, decimals) {
    if (typeof ratio !== "number") throw new I18nError("percentage ratio must be a number");
    const value = formatDecimal(ratio * 100, decimals, language, false);
    return plain(fill("common.percent", null, { value }));
  }

  function clockNodes(minute, days) {
    if (!Number.isInteger(minute) || !Number.isInteger(days) || minute < 0 || days < 1) {
      throw new I18nError("clock minute or day count is out of range");
    }
    if (days === 1 && minute <= MINUTES_PER_DAY) return [formatTimeOfDay(minute, language)];
    const minuteOfDay = minute % MINUTES_PER_DAY;
    const day = (minute - minuteOfDay) / MINUTES_PER_DAY;
    return fill("common.day_clock", null, {
      day: isolate("ltr", [formatInteger(day + 1, language)]),
      time: isolate("ltr", [formatTimeOfDay(minuteOfDay, language)]),
    });
  }

  function duration(seconds) {
    if (!Number.isInteger(seconds) || seconds < 0) throw new I18nError("duration must be whole seconds");
    if (seconds < 60) return plain(fill("common.seconds", seconds, {}));
    const rest = seconds % 60;
    return plain(fill("common.minutes_seconds", null, valueNodes({
      minutes: (seconds - rest) / 60,
      seconds: rest,
    })));
  }

  function paramNode(param, ctx) {
    const kind = paramKind(param);
    if (kind === "text") return isolate("auto", [param.text]);
    if (kind === "id") return isolate("ltr", [param.id]);
    if (kind === "int") return isolate("ltr", [formatInteger(param.int, language)]);
    if (kind === "num") {
      return isolate("ltr", [formatDecimal(param.num, param.decimals, language, param.signed === true)]);
    }
    if (kind === "pct") return isolate("ltr", [percent(param.pct, param.decimals)]);
    if (kind === "clock") {
      const nodes = clockNodes(param.clock, ctx.days);
      return nodes.length === 1 && typeof nodes[0] === "string"
        ? isolate("ltr", nodes)
        : isolate(direction, nodes);
    }
    if (kind === "person") return personNode(param.person, ctx);
    if (kind === "list") {
      const separator = template("common.list_separator", null);
      const joined = [];
      param.list.forEach((item, index) => {
        if (index) joined.push(separator);
        joined.push(paramNode(item, ctx));
      });
      return isolate(direction, joined);
    }
    return isolate(direction, refNodes(param, ctx));
  }

  function personNode(agentId, ctx) {
    const label = ctx.people.get(agentId);
    if (label === undefined) throw new I18nError(`person ${agentId} is not in the reference context`);
    const name = label.templateKey === null
      ? isolate("auto", [label.displayName])
      : isolate(direction, fill(`persona.${label.templateKey}.name`, null, {}));
    return isolate(direction, fill("common.person_ref", null, {
      name,
      number: isolate("ltr", [formatInteger(label.number, language)]),
    }));
  }

  function refNodes(ref, ctx) {
    const values = Object.create(null);
    for (const [name, param] of Object.entries(ref.params || {})) values[name] = paramNode(param, ctx);
    return fill(ref.key, ref.count ?? null, values);
  }

  function checkedContext(ctx) {
    if (!isPlainObject(ctx) || !Number.isInteger(ctx.days) || !(ctx.people instanceof Map)) {
      throw new I18nError("a reference context from makeRefContext() is required");
    }
    return ctx;
  }

  const catalogFmt = Object.freeze({
    int: (n) => guardedText("fmt.int", () => formatInteger(n, language)),
    dec: (x, decimals, { signed = false } = {}) => guardedText(
      "fmt.dec",
      () => formatDecimal(x, decimals, language, signed === true),
    ),
    pct: (r, decimals = 0) => guardedText("fmt.pct", () => percent(r, decimals)),
    clock: (minute, days) => guardedText("fmt.clock", () => plain(clockNodes(minute, days))),
    duration: (s) => guardedText("fmt.duration", () => duration(s)),
  });

  return Object.freeze({
    language,
    direction,
    has(key) {
      return table.has(key);
    },
    t(key, params = {}) {
      return guardedText(key, () => {
        const count = isPlainObject(params) && Number.isInteger(params.count) ? params.count : null;
        if (count !== null && count < 0) throw new I18nError("count must not be negative");
        return plain(fill(key, count, valueNodes(params)));
      });
    },
    tn(key, count, params = {}) {
      return guardedText(key, () => {
        if (!Number.isInteger(count) || count < 0) throw new I18nError(`message ${key} needs a whole count`);
        if (!isPlainObject(params)) throw new I18nError("message parameters must be an object");
        return plain(fill(key, count, valueNodes({ ...params, count })));
      });
    },
    fmt: catalogFmt,
    resolveNodes(ref, ctx) {
      const fragment = document.createDocumentFragment();
      try {
        checkedRef(ref, 1);
        appendNodes(fragment, refNodes(ref, checkedContext(ctx)));
      } catch (error) {
        fragment.replaceChildren(document.createTextNode(missing(refKey(ref), error)));
      }
      return fragment;
    },
    resolveText(ref, ctx) {
      return guardedText(refKey(ref), () => {
        checkedRef(ref, 1);
        return plain(refNodes(ref, checkedContext(ctx)));
      });
    },
  });
}

// Formatting (mirrors adlife/i18n/formatting.py) -------------------------------------

function pluralCategory(language, count) {
  if (language === "fa") return count === 0 || count === 1 ? "one" : "other";
  return count === 1 ? "one" : "other";
}

function localizeDigits(text, language) {
  if (language !== "fa") return text;
  return text.replace(/[0-9]/g, (digit) => String.fromCharCode(PERSIAN_ZERO + Number(digit)));
}

function group(digits, separator) {
  if (digits.length < 4) return digits;
  const head = digits.length % 3 || 3;
  const groups = [digits.slice(0, head)];
  for (let index = head; index < digits.length; index += 3) groups.push(digits.slice(index, index + 3));
  return groups.join(separator);
}

function formatInteger(value, language) {
  if (!Number.isSafeInteger(value)) throw new I18nError("integer is outside the shared formatting range");
  const body = localizeDigits(group(String(Math.abs(value)), SEPARATORS[language][1]), language);
  return (value < 0 ? MINUS_SIGN : "") + body;
}

function formatDecimal(value, decimals, language, signed) {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) >= MAX_DECIMAL_MAGNITUDE) {
    throw new I18nError("decimal value is outside the shared formatting range");
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > MAX_DECIMALS) {
    throw new I18nError("decimal places are outside the supported range");
  }
  // toFixed rounds half up on the exact binary value, like Python's ROUND_HALF_UP.
  const text = Math.abs(value).toFixed(decimals);
  const [integerPart, fraction = ""] = text.split(".");
  const [decimalSeparator, grouping] = SEPARATORS[language];
  let body = group(integerPart, grouping);
  if (fraction) body += decimalSeparator + fraction;
  let sign = "";
  if (/[1-9]/.test(text)) {
    if (value < 0) sign = MINUS_SIGN;
    else if (signed) sign = "+";
  }
  return sign + localizeDigits(body, language);
}

function formatTimeOfDay(minuteOfDay, language) {
  if (!Number.isInteger(minuteOfDay) || minuteOfDay < 0 || minuteOfDay > MINUTES_PER_DAY) {
    throw new I18nError("minute of day is outside 00:00 through 24:00");
  }
  const minutes = minuteOfDay % 60;
  const hours = (minuteOfDay - minutes) / 60;
  const text = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  return localizeDigits(text, language);
}

// Message references (mirrors adlife/i18n/refs.py) -----------------------------------

function paramKind(param) {
  if (!isPlainObject(param)) throw new I18nError("message parameter must be an object");
  const present = PARAM_KINDS.filter((kind) => Object.prototype.hasOwnProperty.call(param, kind));
  if (present.length !== 1) throw new I18nError("message parameter kind is ambiguous");
  return present[0];
}

function checkedRef(ref, depth) {
  if (!isPlainObject(ref)) throw new I18nError("message reference must be an object");
  checkedMembers(ref, PARAM_MEMBERS.key);
  if (typeof ref.key !== "string" || ref.key.length > MAX_KEY_LENGTH || !KEY_PATTERN.test(ref.key)) {
    throw new I18nError("message reference key is invalid");
  }
  if (ref.count !== undefined && ref.count !== null && !(Number.isSafeInteger(ref.count) && ref.count >= 0)) {
    throw new I18nError(`message ${ref.key} has an invalid count`);
  }
  const params = ref.params === undefined || ref.params === null ? {} : ref.params;
  if (!isPlainObject(params)) throw new I18nError(`message ${ref.key} parameters must be an object`);
  const names = Object.keys(params);
  if (names.length > MAX_REF_PARAMS) throw new I18nError(`message ${ref.key} has too many parameters`);
  let deepest = depth;
  for (const name of names) {
    if (name === "count" || name.length > MAX_PARAMETER_NAME_LENGTH || !PARAMETER_NAME_PATTERN.test(name)) {
      throw new I18nError(`message ${ref.key} has an invalid parameter name`);
    }
    deepest = Math.max(deepest, checkedParam(params[name], depth));
  }
  if (deepest > MAX_REF_DEPTH) throw new I18nError(`message ${ref.key} nests too deeply`);
  return deepest;
}

function checkedParam(param, depth) {
  const kind = paramKind(param);
  checkedMembers(param, PARAM_MEMBERS[kind]);
  if (kind === "key") return checkedRef(param, depth + 1);
  if (kind === "list") {
    if (!Array.isArray(param.list) || param.list.length > MAX_LIST_ITEMS) {
      throw new I18nError("list parameter must hold at most 8 items");
    }
    let deepest = depth + 1;
    for (const item of param.list) deepest = Math.max(deepest, checkedParam(item, depth + 1));
    return deepest;
  }
  const valid = {
    text: () => typeof param.text === "string" && param.text.length <= MAX_TEXT_PARAM_LENGTH,
    id: () => typeof param.id === "string" && param.id.length >= 1
      && param.id.length <= MAX_ID_PARAM_LENGTH && ID_PARAM_PATTERN.test(param.id),
    int: () => Number.isSafeInteger(param.int),
    num: () => isFiniteNumber(param.num) && Math.abs(param.num) < MAX_DECIMAL_MAGNITUDE
      && Number.isInteger(param.decimals) && param.decimals >= 0 && param.decimals <= 3
      && (param.signed === undefined || param.signed === null || typeof param.signed === "boolean"),
    pct: () => isFiniteNumber(param.pct) && Math.abs(param.pct) < 1e18
      && (param.decimals === 0 || param.decimals === 1),
    clock: () => Number.isInteger(param.clock) && param.clock >= 0 && param.clock <= MAX_CLOCK_MINUTE,
    person: () => typeof param.person === "string" && AGENT_ID_PATTERN.test(param.person),
  }[kind]();
  if (!valid) throw new I18nError(`${kind} parameter is invalid`);
  return depth;
}

function checkedMembers(value, allowed) {
  for (const member of Object.keys(value)) {
    if (!allowed.includes(member)) throw new I18nError(`unexpected reference member ${member}`);
  }
}

// Shared helpers ----------------------------------------------------------------------

function isolate(direction, children) {
  return { direction, children };
}

function plain(nodes) {
  let text = "";
  for (const node of nodes) text += typeof node === "string" ? node : plain(node.children);
  return text;
}

function appendNodes(parent, nodes) {
  for (const node of nodes) {
    if (typeof node === "string") {
      if (node) parent.append(document.createTextNode(node));
    } else {
      const element = document.createElement("bdi");
      element.setAttribute("dir", node.direction);
      appendNodes(element, node.children);
      parent.append(element);
    }
  }
}

function validatedMessages(messages) {
  if (!isPlainObject(messages)) throw new I18nError("catalog messages must be an object");
  const table = new Map();
  for (const [key, value] of Object.entries(messages)) {
    if (key.length > MAX_KEY_LENGTH || !KEY_PATTERN.test(key)) throw new I18nError(`catalog key ${key} is invalid`);
    if (typeof value === "string" && validTemplate(value)) {
      table.set(key, value);
    } else if (
      isPlainObject(value)
      && Object.keys(value).sort().join(",") === "one,other"
      && validTemplate(value.one)
      && validTemplate(value.other)
    ) {
      table.set(key, Object.freeze({ one: value.one, other: value.other }));
    } else {
      throw new I18nError(`catalog message ${key} is invalid`);
    }
  }
  return table;
}

function validTemplate(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= MAX_MESSAGE_LENGTH;
}

function documentLanguage() {
  const language = document.documentElement.getAttribute("lang");
  if (!LANGUAGES.includes(language)) throw new I18nError("the page language is not supported");
  return language;
}

function directionOf(language) {
  return language === "fa" ? "rtl" : "ltr";
}

function refKey(ref) {
  return isPlainObject(ref) && typeof ref.key === "string" && KEY_PATTERN.test(ref.key) ? ref.key : "?";
}

function requireActive(key) {
  if (activeCatalog === null) {
    return Object.freeze({
      t: () => missing(key, new I18nError("initI18n() has not finished")),
      tn: () => missing(key, new I18nError("initI18n() has not finished")),
      fmt: new Proxy({}, { get: () => () => missing(key, new I18nError("initI18n() has not finished")) }),
      resolveNodes: () => {
        const fragment = document.createDocumentFragment();
        fragment.append(missing(key, new I18nError("initI18n() has not finished")));
        return fragment;
      },
      resolveText: () => missing(key, new I18nError("initI18n() has not finished")),
    });
  }
  return activeCatalog;
}

function guardedText(key, render) {
  try {
    return render();
  } catch (error) {
    return missing(key, error);
  }
}

function missing(key, error) {
  if (!(error instanceof I18nError)) throw error;
  console.error(`AdLife guide translation: ${error.message}`);
  return `${MISSING_OPEN}${key}${MISSING_CLOSE}`;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}
