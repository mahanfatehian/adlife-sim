// AdLife guided demo: the live progress page. It reads the job view the server already
// publishes (phase plus cognition_progress) and never changes anything but a Stop request.
import { fmt, href, initI18n, t, tn } from "./guide-i18n.js";
import { ApiError, announce, byId, clear, getJson, meta, postJson } from "./guide-core.js";
import { message } from "./guide-connection.js";

// Bounded cognition answers at most this many questions at a time (frozen V2 policy).
const CONCURRENCY = 4;
const JOB_ID = /^job-[0-9a-f]{32}$/;
const RUN_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
const FIRST_POLL_MS = 250;
const POLL_MS = 1000;
const RETRY_MS = Object.freeze([2000, 4000, 5000]);
const STOP_CONFIRM_MS = 5000;
const REMAINING_STEP_SECONDS = 5;
const REMAINING_MAX_SECONDS = 600;
const PHASE_RANK = Object.freeze({
  queued: 0,
  evaluating: 1,
  simulating: 2,
  "planning-cognition": 3,
  "selecting-explanations": 3,
  "requesting-explanations": 4,
  "requesting-cognition": 4,
  "applying-cognition": 5,
  persisting: 6,
  verifying: 7,
  completed: 8,
  failed: 8,
  cancelled: 8,
});
const STEP_OF = Object.freeze({
  queued: "simulate",
  evaluating: "simulate",
  simulating: "simulate",
  "planning-cognition": "simulate",
  "selecting-explanations": "ask",
  "requesting-explanations": "ask",
  "requesting-cognition": "ask",
  "applying-cognition": "save",
  persisting: "save",
  verifying: "save",
  completed: "done",
});
const STEPS = Object.freeze(["simulate", "ask", "save", "done"]);
const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const PROVIDER_MODES = new Set(["local", "remote"]);
const FAILURES = new Set(["evaluation-failed", "persistence-failed", "verification-failed"]);
const STATE_TEXT = Object.freeze({
  waiting: () => t("progress.state.waiting"),
  active: () => t("progress.state.active"),
  done: () => t("progress.state.done"),
  stopped: () => t("progress.state.stopped"),
});

const view = {
  jobId: null,
  job: null,
  rank: -1,
  resolved: -1,
  activeStep: null,
  announcedStep: null,
  failures: 0,
  pollTimer: 0,
  clockTimer: 0,
  finished: false,
  focused: false,
  startedAt: null,
  startedResolved: 0,
  stopArmed: false,
  stopTimer: 0,
  stopping: false,
};

function statusOf(error) {
  return error instanceof ApiError && Number.isInteger(error.status) ? error.status : 0;
}

function codeOf(error) {
  return error instanceof ApiError && typeof error.code === "string" ? error.code : null;
}

function parseStamp(text) {
  if (typeof text !== "string") return null;
  const value = Date.parse(text.replace(/\.(\d{3})\d*Z$/, ".$1Z"));
  return Number.isFinite(value) ? value : null;
}

function elapsedSeconds(job) {
  const created = parseStamp(job.created_at);
  if (created === null) return null;
  const end = TERMINAL.has(job.phase) ? parseStamp(job.updated_at) : Date.now();
  return end === null ? null : Math.max(0, Math.floor((end - created) / 1000));
}

function modeOf(job) {
  return job.accepted_settings?.cognition_mode ?? null;
}

function guided(job) {
  return job.accepted_settings?.workbench_input_schema_version === 4;
}

function stepLabel(step, mode) {
  if (step === "ask" && mode === "mock") return t("progress.step.ask.mock");
  if (step === "ask" && mode === "rules") return t("progress.step.ask.rules");
  return t(`progress.step.${step}`);
}

function showLink(id, path) {
  const link = byId(id);
  if (path === null) {
    link.hidden = true;
    link.removeAttribute("href");
  } else {
    link.href = path;
    link.hidden = false;
  }
}

// ----- rendering ------------------------------------------------------------------------

function renderHeader(job) {
  let title = "progress.title";
  if (job.phase === "completed") title = "progress.title.done";
  else if (TERMINAL.has(job.phase)) title = "progress.title.stopped";
  byId("job-title").textContent = t(title);
  const line = byId("job-run");
  clear(line);
  if (RUN_ID.test(job.run_id ?? "")) line.append(message("progress.run", { run_id: { id: job.run_id } }));
  byId("job-legacy").hidden = guided(job);
}

function renderSteps(job) {
  const mode = modeOf(job);
  const step = STEP_OF[job.phase];
  if (step !== undefined) view.activeStep = step;
  const current = STEPS.indexOf(view.activeStep ?? "simulate");
  for (const item of byId("job-steps").querySelectorAll(".setup-step")) {
    const index = STEPS.indexOf(item.dataset.step);
    let state = "waiting";
    if (job.phase === "completed") state = "done";
    else if (index < current) state = "done";
    else if (index === current) state = TERMINAL.has(job.phase) ? "stopped" : "active";
    item.dataset.state = state;
    const here = job.phase === "completed" ? item.dataset.step === "done" : state === "active";
    if (here) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    item.querySelector(".g-steps__state").textContent = STATE_TEXT[state]();
    if (item.dataset.step === "ask") {
      item.querySelector(".g-steps__label").textContent = stepLabel("ask", mode);
    }
  }
}

function renderCounter(job) {
  const mode = modeOf(job);
  const progress = job.cognition_progress ?? null;
  const big = byId("job-counter-text");
  const bar = byId("job-bar");
  const note = byId("job-counter-note");
  clear(big);
  bar.hidden = true;
  note.hidden = true;
  big.dataset.kind = "text";
  // A finished offline run: the outcome card below says all this card did, so it yields
  // its room and the next step moves up into view.
  byId("job-counter").hidden =
    job.phase === "completed" && guided(job) && (mode === "mock" || mode === "rules");
  if (!guided(job)) {
    big.append(stepLabel(STEP_OF[job.phase] ?? view.activeStep ?? "simulate", mode));
  } else if (mode === "mock") {
    big.append(t("progress.offline"));
  } else if (mode === "rules") {
    big.append(t("progress.offline.rules"));
  } else if (progress === null) {
    big.append(t("progress.preparing"));
  } else if (progress.total === 0) {
    big.append(t("progress.no_questions"));
  } else {
    big.dataset.kind = "count";
    big.append(
      message("progress.answered", {
        resolved: { int: progress.resolved },
        total: { int: progress.total },
      }),
    );
    bar.max = progress.total;
    bar.value = progress.resolved;
    bar.setAttribute(
      "aria-valuetext",
      t("progress.answered", { resolved: progress.resolved, total: progress.total }),
    );
    bar.hidden = false;
    note.textContent = t("progress.parallel", { parallel: CONCURRENCY });
    note.hidden = false;
  }
}

function renderClock() {
  const job = view.job;
  if (job === null) return;
  const seconds = elapsedSeconds(job);
  const elapsed = byId("job-elapsed");
  elapsed.textContent = seconds === null ? "" : t("progress.elapsed", { time: fmt.duration(seconds) });
  byId("job-times").hidden = TERMINAL.has(job.phase) || seconds === null;
}

function renderRemaining(job) {
  const node = byId("job-remaining");
  node.hidden = true;
  const progress = job.cognition_progress ?? null;
  if (job.phase !== "requesting-cognition" || progress === null || progress.total === 0) return;
  if (view.startedAt === null) {
    view.startedAt = Date.now();
    view.startedResolved = progress.resolved;
    return;
  }
  const answered = progress.resolved - view.startedResolved;
  if (answered < 2) return;
  const perAnswer = (Date.now() - view.startedAt) / answered;
  const left = progress.total - progress.resolved;
  const seconds =
    Math.ceil((left * perAnswer) / 1000 / REMAINING_STEP_SECONDS) * REMAINING_STEP_SECONDS;
  if (left <= 0 || seconds <= 0 || seconds > REMAINING_MAX_SECONDS) return;
  node.textContent = t("progress.remaining", { time: fmt.duration(seconds) });
  node.hidden = false;
}

function renderStop(job) {
  const button = byId("job-stop");
  const status = byId("job-status");
  button.hidden = !(job.can_cancel === true) || view.stopping;
  if (button.hidden) disarmStop();
  const stopping = (job.cancellation_requested === true || view.stopping) && !TERMINAL.has(job.phase);
  if (stopping) {
    status.textContent = t("progress.stopping");
    status.hidden = false;
  } else if (status.dataset.kind !== "reconnecting") {
    status.hidden = true;
  }
}

function renderOutcome(job) {
  const box = byId("job-outcome");
  if (!TERMINAL.has(job.phase)) {
    box.hidden = true;
    return;
  }
  const title = byId("job-outcome-title");
  const counts = byId("job-outcome-counts");
  const note = byId("job-outcome-note");
  counts.hidden = true;
  note.hidden = true;
  for (const id of ["job-watch", "job-report", "job-saved"]) showLink(id, null);
  if (job.phase === "completed") {
    const result = job.result ?? {};
    const mode = modeOf(job);
    title.textContent = t("progress.done", { time: fmt.duration(elapsedSeconds(job) ?? 0) });
    if (PROVIDER_MODES.has(mode) && guided(job)) {
      counts.textContent = t("progress.done.counts", {
        answered: result.provider_count ?? 0,
        reused: result.cache_count ?? 0,
        failed: result.fallback_count ?? 0,
        over: result.budget_skipped_count ?? 0,
      });
      const funded = result.funded_request_count ?? 0;
      if (funded > 0 && (result.provider_count ?? 0) === 0 && (result.cache_count ?? 0) === funded) {
        note.textContent = t("progress.done.all_reused");
        note.hidden = false;
      } else if (funded > 0 && (result.fallback_count ?? 0) === funded) {
        note.textContent = t("progress.done.all_failed");
        note.hidden = false;
      }
    } else if (mode === "mock") {
      counts.textContent = tn("progress.done.practice", result.mock_count ?? 0);
    } else {
      counts.textContent = t("progress.done.rules");
    }
    counts.hidden = false;
    const runId = RUN_ID.test(result.run_id ?? "") ? result.run_id : null;
    const story = runId !== null && result.run_schema_version === 10;
    showLink("job-watch", story ? href(`/guide/runs/${runId}`) : null);
    showLink("job-report", story ? href(`/guide/runs/${runId}/report`) : null);
    // A run without a story view is still saved: point to the saved-runs list instead.
    showLink("job-saved", story ? null : href("/guide"));
  } else if (job.phase === "failed") {
    const code = FAILURES.has(job.error?.code) ? job.error.code : "evaluation-failed";
    title.textContent = t("progress.failed", { reason: t(`failure.${code}`) });
  } else {
    title.textContent = t("progress.cancelled");
  }
  box.hidden = false;
}

function announceChanges(job) {
  const step = job.phase === "completed" ? "done" : STEP_OF[job.phase];
  if (TERMINAL.has(job.phase)) {
    if (view.announcedStep !== job.phase) {
      view.announcedStep = job.phase;
      announce(byId("job-outcome-title").textContent ?? "", { force: true });
    }
    return;
  }
  if (step !== undefined && step !== view.announcedStep) {
    view.announcedStep = step;
    announce(stepLabel(step, modeOf(job)), { force: true });
    return;
  }
  const progress = job.cognition_progress ?? null;
  if (job.phase === "requesting-cognition" && progress !== null && progress.total > 0) {
    announce(t("progress.answered.sr", { resolved: progress.resolved, total: progress.total }));
  }
}

function finish(job) {
  view.finished = true;
  clearInterval(view.clockTimer);
  clearTimeout(view.pollTimer);
  if (job.phase === "completed" && !view.focused) {
    view.focused = true;
    const watch = byId("job-watch");
    (watch.hidden ? byId("job-again") : watch).focus();
  }
}

/** Accept one job snapshot unless it is older than what the page already shows. */
function accept(job) {
  if (job === null || typeof job !== "object" || job.job_id !== view.jobId) return;
  const rank = PHASE_RANK[job.phase];
  if (rank === undefined) return;
  const resolved = Number.isInteger(job.cognition_progress?.resolved) ? job.cognition_progress.resolved : -1;
  if (rank < view.rank || (rank === view.rank && resolved < view.resolved)) return;
  view.rank = rank;
  view.resolved = resolved;
  view.job = job;
  renderHeader(job);
  renderSteps(job);
  renderCounter(job);
  renderRemaining(job);
  renderClock();
  renderStop(job);
  renderOutcome(job);
  announceChanges(job);
  if (TERMINAL.has(job.phase)) finish(job);
}

function showLost() {
  view.finished = true;
  clearInterval(view.clockTimer);
  clearTimeout(view.pollTimer);
  byId("job-counter").hidden = true;
  byId("job-stop").hidden = true;
  const title = byId("job-outcome-title");
  title.textContent = t("progress.lost");
  byId("job-outcome-counts").hidden = true;
  byId("job-outcome-note").hidden = true;
  for (const id of ["job-watch", "job-report"]) showLink(id, null);
  showLink("job-saved", href("/guide"));
  byId("job-outcome").hidden = false;
  announce(t("progress.lost"), { force: true });
}

function setReconnecting(on) {
  const status = byId("job-status");
  if (on) {
    status.dataset.kind = "reconnecting";
    status.textContent = t("progress.reconnecting");
    status.hidden = false;
  } else if (status.dataset.kind === "reconnecting") {
    delete status.dataset.kind;
    status.hidden = true;
    if (view.job !== null) renderStop(view.job);
  }
}

// ----- polling and stopping -------------------------------------------------------------

function schedule(delay) {
  clearTimeout(view.pollTimer);
  view.pollTimer = setTimeout(() => {
    void poll();
  }, delay);
}

async function poll() {
  if (view.finished) return;
  let job = null;
  try {
    job = await getJson(`/api/jobs/${view.jobId}`);
  } catch (error) {
    if (statusOf(error) === 404) {
      showLost();
      return;
    }
    view.failures += 1;
    setReconnecting(true);
    schedule(RETRY_MS[Math.min(view.failures - 1, RETRY_MS.length - 1)]);
    return;
  }
  view.failures = 0;
  setReconnecting(false);
  accept(job);
  if (!view.finished) schedule(POLL_MS);
}

function disarmStop() {
  clearTimeout(view.stopTimer);
  view.stopArmed = false;
  const button = byId("job-stop");
  button.textContent = t("progress.stop");
  button.classList.remove("g-btn--danger");
  button.classList.add("g-btn--secondary");
}

async function stop() {
  const button = byId("job-stop");
  if (view.stopping || view.finished) return;
  if (!view.stopArmed) {
    view.stopArmed = true;
    button.textContent = t("progress.stop.confirm");
    button.classList.remove("g-btn--secondary");
    button.classList.add("g-btn--danger");
    view.stopTimer = setTimeout(disarmStop, STOP_CONFIRM_MS);
    return;
  }
  disarmStop();
  view.stopping = true;
  if (view.job !== null) renderStop(view.job);
  try {
    accept(await postJson(`/api/jobs/${view.jobId}/cancel`, {}));
  } catch (error) {
    view.stopping = false;
    if (codeOf(error) !== "job-state-conflict" && view.job !== null) renderStop(view.job);
  }
}

async function main() {
  await initI18n();
  const jobId = meta("adlife-job-id");
  if (typeof jobId !== "string" || !JOB_ID.test(jobId)) {
    showLost();
    return;
  }
  view.jobId = jobId;
  byId("job-stop").addEventListener("click", () => {
    void stop();
  });
  view.clockTimer = setInterval(renderClock, 1000);
  schedule(FIRST_POLL_MS);
}

void main();
