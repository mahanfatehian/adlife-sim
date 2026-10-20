// AdLife guided demo: the start page (explanation, preflight, AI connection, saved runs).
import { href, initI18n, t, tn } from "./guide-i18n.js";
import { bdi, byId, clear, el, getJson, prefersReducedMotion } from "./guide-core.js";
import {
  dotList,
  errorNodes,
  fetchAllRuns,
  followWorkbench,
  message,
  mountConnection,
  renderActiveJob,
} from "./guide-connection.js";

const SHOWN_RUNS = 8;
const GUIDED_SCENARIO = /^guided-/;
const PRODUCT_SCENARIO = /^guided-(device|coffee|meal|delivery)$/;
const RUN_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const TEMPLATE_BY_KEY = Object.freeze({
  device: "fictional-device-launch-v1",
  coffee: "fictional-coffee-pass-v1",
  meal: "fictional-crispy-meal-v1",
  delivery: "fictional-home-delivery-v1",
});
const MODES = new Set(["rules", "mock", "local", "remote"]);
const PROVIDER_MODES = new Set(["local", "remote"]);

function setCheck(item, state, ...content) {
  item.dataset.state = state;
  const text = item.querySelector(".setup-check__text");
  clear(text);
  text.append(...content);
}

function renderAiStatus(payload) {
  const item = byId("preflight-ai");
  if (payload.source === "mock" || payload.source === "rules") {
    setCheck(item, "info", t("start.preflight.ai.offline"));
  } else if (payload.ready) {
    setCheck(item, "ok", t("start.preflight.ai.ok"));
  } else {
    setCheck(item, "warn", t("start.preflight.ai.untested"));
  }
}

function byRunId(left, right) {
  if (left.run_id === right.run_id) return 0;
  return left.run_id < right.run_id ? -1 : 1;
}

function isGuided(run) {
  return GUIDED_SCENARIO.test(run.scenario_id ?? "");
}

function technicalLink(path) {
  return el(
    "a",
    {
      className: "g-btn g-btn--quiet",
      attrs: { href: path, target: "_blank", rel: "noopener noreferrer" },
    },
    t("common.technical_view"),
    el("span", { className: "g-visually-hidden", text: ` ${t("common.new_tab")}` }),
  );
}

function runCard(run, products, models) {
  const v10 = run.run_schema_version === 10;
  const match = PRODUCT_SCENARIO.exec(run.scenario_id ?? "");
  const product = match === null ? undefined : products.get(TEMPLATE_BY_KEY[match[1]]);
  const mode = MODES.has(run.cognition_mode) ? run.cognition_mode : "rules";
  const meta = [
    message("start.runs.meta", {
      people: { key: "common.people", count: run.agent_count },
      days: { key: "common.days", count: run.days },
      ai: { key: `mode.${mode}` },
    }),
  ];
  if (typeof run.provider_profile_id === "string" && run.provider_profile_id !== "") {
    // The audience knows the model's name, not the profile the guide stored it under.
    const model = models.get(`${run.provider_profile_id}@${run.provider_revision_sha256}`);
    meta.push(bdi(model ?? run.provider_profile_id, "ltr"));
  }
  const counts = [tn("start.runs.noticed", run.noticed_count ?? 0)];
  if (v10 && PROVIDER_MODES.has(run.cognition_mode)) {
    counts.push(tn("start.runs.answers", (run.provider_count ?? 0) + (run.cache_count ?? 0)));
  }
  const actions = el("div", { className: "setup-run__actions" });
  if (v10) {
    actions.append(
      el("a", {
        className: "g-btn g-btn--secondary",
        text: t("start.runs.story"),
        attrs: { href: href(`/guide/runs/${run.run_id}`) },
      }),
      el("a", {
        className: "g-btn g-btn--quiet",
        text: t("start.runs.report"),
        attrs: { href: href(`/guide/runs/${run.run_id}/report`) },
      }),
    );
  }
  if (typeof run.inspector_url === "string" && run.inspector_url.startsWith("/runs/")) {
    actions.append(technicalLink(run.inspector_url));
  }
  const card = el(
    "li",
    { className: "setup-run", dataset: { runId: run.run_id, guided: isGuided(run) ? "true" : "false" } },
    el("h3", { className: "setup-run__title" }, product === undefined ? t("start.runs.saved_run") : bdi(product)),
    el("p", { className: "setup-run__id" }, bdi(run.run_id, "ltr")),
    dotList("setup-run__meta", ...meta),
    v10
      ? dotList("setup-run__counts", ...counts)
      : el("p", { className: "setup-run__older", text: t("start.runs.older") }),
    actions,
  );
  return card;
}

const shelf = { ordered: [], products: new Map(), models: new Map() };

function showRuns(count) {
  const list = byId("runs-list");
  clear(list);
  list.append(
    ...shelf.ordered.slice(0, count).map((run) => runCard(run, shelf.products, shelf.models)),
  );
  byId("runs-all").hidden = shelf.ordered.length <= count;
}

function showAllRuns() {
  showRuns(shelf.ordered.length);
  byId("runs-list").querySelectorAll(".setup-run")[SHOWN_RUNS]?.querySelector("a")?.focus();
}

function renderRuns(runs, products, models) {
  const status = byId("runs-status");
  const usable = runs.filter((run) => RUN_ID.test(run?.run_id ?? ""));
  const guided = usable.filter(isGuided).sort((left, right) => byRunId(right, left));
  const others = usable.filter((run) => !isGuided(run)).sort(byRunId);
  shelf.ordered = [...guided, ...others];
  shelf.products = products;
  shelf.models = models;
  status.classList.remove("g-loading");
  if (shelf.ordered.length === 0) {
    status.textContent = t("start.runs.empty");
    status.hidden = false;
    showRuns(0);
  } else {
    status.hidden = true;
    showRuns(SHOWN_RUNS);
  }
  const backups = usable.filter((run) => run.run_schema_version === 10 && isGuided(run)).length;
  const item = byId("preflight-backups");
  if (backups > 0) setCheck(item, "ok", tn("start.preflight.backups", backups));
  else setCheck(item, "warn", t("start.preflight.no_backups"));
}

async function loadRuns() {
  const [runs, catalog, providers] = await Promise.all([
    fetchAllRuns(),
    getJson("/api/creative-templates").catch(() => null),
    getJson("/api/providers").catch(() => null),
  ]);
  const products = new Map();
  for (const template of Array.isArray(catalog?.templates) ? catalog.templates : []) {
    if (typeof template?.template_id === "string" && typeof template.product_name === "string") {
      products.set(template.template_id, template.product_name);
    }
  }
  const models = new Map();
  for (const profile of Array.isArray(providers?.profiles) ? providers.profiles : []) {
    if (typeof profile?.model_id === "string" && typeof profile.revision_sha256 === "string") {
      models.set(`${profile.profile_id}@${profile.revision_sha256}`, profile.model_id);
    }
  }
  renderRuns(runs, products, models);
}

async function main() {
  await initI18n();
  byId("preflight-motion").hidden = !prefersReducedMotion();
  byId("runs-all").addEventListener("click", showAllRuns);
  const banner = byId("active-job");
  const tasks = [
    mountConnection(byId("start-connection"), { variant: "full", onChange: renderAiStatus }),
    followWorkbench((job) => renderActiveJob(banner, job, href)),
    loadRuns().catch((error) => {
      const status = byId("runs-status");
      status.classList.remove("g-loading");
      clear(status);
      status.append(...errorNodes(error));
      status.hidden = false;
      setCheck(byId("preflight-backups"), "warn", ...errorNodes(error));
    }),
  ];
  await Promise.allSettled(tasks);
}

void main();
