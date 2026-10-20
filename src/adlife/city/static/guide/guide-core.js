// AdLife guided demo: shared DOM, request and announcement helpers.
//
// Every guide page module imports this file. Markup is never built from strings:
// elements come from document.createElement, text is set with textContent, and el()
// refuses inline styles, event-handler attributes and non-local URLs, so hostile saved
// text can only ever appear as text.

const JSON_PATH_PREFIXES = Object.freeze(["/api/", "/guide/assets/"]);
const MAX_PATH_LENGTH = 300;
// The story document is at most 4 MiB; every other guide response is far smaller.
const MAX_JSON_BYTES = 4_194_304 + 65_536;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
const FIELD_PATH_PATTERN = /^[a-z0-9][a-z0-9_.-]{0,159}$/;
const TAG_PATTERN = /^[a-z][a-z0-9]*$/;
const ATTRIBUTE_PATTERN = /^[a-z][a-z0-9-]*$/;
const DATASET_PATTERN = /^[a-z][a-zA-Z0-9]*$/;
const META_NAME_PATTERN = /^[a-z][a-z0-9-]*$/;
const REFUSED_TAGS = new Set([
  "applet", "base", "embed", "form", "frame", "frameset", "iframe", "link", "math",
  "meta", "noscript", "object", "portal", "script", "style", "svg", "template",
]);
const REFUSED_ATTRIBUTES = new Set(["style", "srcdoc", "srcset", "is", "formaction", "action"]);
const URL_ATTRIBUTES = new Set(["href", "src", "poster", "cite", "ping"]);
const ANNOUNCE_INTERVAL_MS = 5000;

/** Return the element with this ID; a missing element is a page defect and throws. */
export function byId(id) {
  const node = typeof id === "string" ? document.getElementById(id) : null;
  if (node === null) throw new Error(`guide page element #${String(id)} is missing`);
  return node;
}

/** Return the content of <meta name="...">, or null when the page has none. */
export function meta(name) {
  if (typeof name !== "string" || !META_NAME_PATTERN.test(name)) {
    throw new TypeError("meta() needs a lowercase meta name");
  }
  const node = document.head.querySelector(`meta[name="${name}"]`);
  return node === null ? null : node.getAttribute("content");
}

/** Remove every child of a node. */
export function clear(node) {
  node.replaceChildren();
  return node;
}

/**
 * Create an element. text is set with textContent; attrs refuse style, srcdoc and every
 * on* handler, and URL attributes must stay on this server ("/", "#" or "?"). Children
 * may be nodes, strings (text nodes), arrays, or null/false (skipped). Numbers are
 * refused so unlocalized digits cannot slip in: format them with fmt first.
 */
export function el(tag, { className, text, attrs, dataset } = {}, ...children) {
  if (typeof tag !== "string" || !TAG_PATTERN.test(tag) || REFUSED_TAGS.has(tag)) {
    throw new TypeError(`el() refused the element <${String(tag)}>`);
  }
  const node = document.createElement(tag);
  if (className !== undefined && className !== null) {
    if (typeof className !== "string") throw new TypeError("el() className must be a string");
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    if (typeof text !== "string") throw new TypeError("el() text must be a string");
    node.textContent = text;
  }
  if (attrs !== undefined && attrs !== null) {
    for (const [name, value] of Object.entries(attrs)) setSafeAttribute(node, name, value);
  }
  if (dataset !== undefined && dataset !== null) {
    for (const [name, value] of Object.entries(dataset)) {
      if (!DATASET_PATTERN.test(name)) throw new TypeError(`el() refused data key ${name}`);
      if (value === undefined || value === null) continue;
      if (!["string", "number", "boolean"].includes(typeof value)) {
        throw new TypeError(`el() data-${name} must be text, a number or a boolean`);
      }
      node.dataset[name] = String(value);
    }
  }
  appendChildren(node, children);
  return node;
}

/** Create <bdi dir="..."> holding verbatim text: user text, IDs and numbers. */
export function bdi(text, dir = "auto") {
  if (typeof text !== "string") throw new TypeError("bdi() text must be a string");
  if (dir !== "auto" && dir !== "ltr" && dir !== "rtl") {
    throw new TypeError("bdi() direction must be auto, ltr or rtl");
  }
  const node = document.createElement("bdi");
  node.setAttribute("dir", dir);
  node.textContent = text;
  return node;
}

/**
 * A refused or failed request. status is the HTTP status, or 0 when the server could
 * not be reached. code is the server's error code, or one of "network-error",
 * "unexpected-response" and "missing-csrf-token". fields maps refused draft paths to
 * the server's messages; pages show translated text, never those messages.
 */
export class ApiError extends Error {
  status = 0;
  code = "";
  fields = {};

  constructor(status, code, fields = {}) {
    super(`guide request failed (${status} ${code})`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = Object.freeze({ ...fields });
  }
}

/** GET a JSON document from /api/ or /guide/assets/. */
export async function getJson(path) {
  return requestJson("GET", path, undefined);
}

/** POST a JSON object to /api/ with the page's CSRF token. */
export async function postJson(path, body) {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw new TypeError("postJson() body must be a plain object");
  }
  return requestJson("POST", path, body);
}

let lastAnnouncementAt = Number.NEGATIVE_INFINITY;
let pendingAnnouncement = null;
let pendingTimer = 0;
let repeatMarker = false;

/**
 * Announce text in the page's polite live region (#g-live), at most once every
 * 5 seconds. A throttled announcement is not lost: the latest one is spoken when the
 * interval ends. force speaks at once, for phase changes and completion. Focus never
 * moves.
 */
export function announce(text, { force = false } = {}) {
  if (typeof text !== "string") throw new TypeError("announce() text must be a string");
  const now = performance.now();
  if (force || now - lastAnnouncementAt >= ANNOUNCE_INTERVAL_MS) {
    cancelPendingAnnouncement();
    speak(text, now);
    return;
  }
  pendingAnnouncement = text;
  if (!pendingTimer) {
    pendingTimer = window.setTimeout(() => {
      pendingTimer = 0;
      const latest = pendingAnnouncement;
      pendingAnnouncement = null;
      if (latest !== null) speak(latest, performance.now());
    }, ANNOUNCE_INTERVAL_MS - (now - lastAnnouncementAt));
  }
}

/** True when the computer asks for reduced motion. */
export function prefersReducedMotion() {
  return typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function setSafeAttribute(node, name, value) {
  if (typeof name !== "string" || !ATTRIBUTE_PATTERN.test(name)) {
    throw new TypeError(`el() refused the attribute ${String(name)}`);
  }
  if (REFUSED_ATTRIBUTES.has(name) || name.startsWith("on")) {
    throw new TypeError(`el() refused the attribute ${name}`);
  }
  if (value === undefined || value === null || value === false) return;
  let text;
  if (value === true) text = "";
  else if (typeof value === "string") text = value;
  else if (typeof value === "number" && Number.isFinite(value)) text = String(value);
  else throw new TypeError(`el() attribute ${name} must be text, a number or a boolean`);
  if (URL_ATTRIBUTES.has(name) && !isLocalUrl(text)) {
    throw new TypeError(`el() refused a non-local ${name}`);
  }
  node.setAttribute(name, text);
}

function isLocalUrl(value) {
  // "/x" (never "//" or "/\"), "#x" or "?x", with no whitespace, controls or backslash.
  return /^(?:\/(?![/\\])|[#?])/.test(value) && !/[\u0000- \u007f-\u009f\\]/.test(value);
}

function appendChildren(node, children) {
  for (const child of children) {
    if (child === undefined || child === null || child === false) continue;
    if (Array.isArray(child)) appendChildren(node, child);
    else if (typeof child === "string") node.append(document.createTextNode(child));
    else if (child instanceof Node) node.append(child);
    else throw new TypeError("el() children must be nodes or strings");
  }
}

function checkedPath(path) {
  if (
    typeof path !== "string"
    || path.length > MAX_PATH_LENGTH
    || !JSON_PATH_PREFIXES.some((prefix) => path.startsWith(prefix))
    || path.includes("//")
    || path.includes("\\")
    || path.includes("#")
    || /[^A-Za-z0-9_?=&/.-]/.test(path)
    // fetch() would resolve "." and ".." segments to a different route before sending.
    || /(?:^|\/)\.{1,2}(?:\/|\?|$)/.test(path)
  ) {
    throw new TypeError("guide request path refused before sending");
  }
  return path;
}

async function requestJson(method, path, body) {
  const safePath = checkedPath(path);
  const headers = { Accept: "application/json" };
  const init = {
    method,
    headers,
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  };
  if (method === "POST") {
    if (!safePath.startsWith("/api/")) throw new TypeError("guide writes go to /api/ only");
    const token = meta("adlife-csrf-token");
    if (typeof token !== "string" || token.length === 0 || token.length > 256) {
      throw new ApiError(0, "missing-csrf-token");
    }
    headers["Content-Type"] = "application/json";
    headers["X-AdLife-CSRF"] = token;
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(safePath, init);
  } catch (_error) {
    throw new ApiError(0, "network-error");
  } finally {
    // A credential body must not outlive the request.
    init.body = undefined;
  }
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.startsWith("application/json")) {
    throw new ApiError(response.status, "unexpected-response");
  }
  let text;
  try {
    text = await response.text();
  } catch (_error) {
    throw new ApiError(0, "network-error");
  }
  if (text.length > MAX_JSON_BYTES || new TextEncoder().encode(text).length > MAX_JSON_BYTES) {
    throw new ApiError(response.status, "unexpected-response");
  }
  let documentValue;
  try {
    documentValue = JSON.parse(text);
  } catch (_error) {
    throw new ApiError(response.status, "unexpected-response");
  }
  if (!response.ok) {
    const error = isPlainObject(documentValue) && isPlainObject(documentValue.error)
      ? documentValue.error
      : null;
    const code = error !== null && typeof error.code === "string" && ERROR_CODE_PATTERN.test(error.code)
      ? error.code
      : "unexpected-response";
    throw new ApiError(response.status, code, safeFields(error === null ? null : error.fields));
  }
  return documentValue;
}

function safeFields(fields) {
  const copy = {};
  if (!isPlainObject(fields)) return copy;
  for (const [path, message] of Object.entries(fields).slice(0, 32)) {
    if (FIELD_PATH_PATTERN.test(path) && typeof message === "string" && message.length <= 240) {
      copy[path] = message;
    }
  }
  return copy;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function cancelPendingAnnouncement() {
  if (pendingTimer) window.clearTimeout(pendingTimer);
  pendingTimer = 0;
  pendingAnnouncement = null;
}

function speak(text, now) {
  const region = document.getElementById("g-live");
  lastAnnouncementAt = now;
  if (region === null) return;
  // A repeated sentence gets an invisible no-break space so it is announced again.
  repeatMarker = region.textContent === text ? !repeatMarker : false;
  region.textContent = repeatMarker ? `${text}\u00a0` : text;
}
