"""Seed a running AdLife city workbench with ready-to-open demonstration runs.

This is the backup for the live demonstration in ``docs/investor-demo.md``. When the local
model is slow, or the presenter wants pre-made results, one command fills a workspace with
saved runs that open straight in the inspector. Start the workbench first, then run this
from the repository folder:

    uv run python scripts/demo_workspace.py --base-url http://127.0.0.1:50050
    uv run python scripts/demo_workspace.py --base-url http://127.0.0.1:50050 --with-llm

The script is only a loopback HTTP client of that running workbench. It drives the same
API the browser page uses: it reads the per-process security token from the page, then
validates and submits each fictional study and polls its job to a terminal phase. Every
invocation creates new runs with fresh, numbered run IDs:

* a Bounded AI cognition run (workbench schema 4, run V10) with the Mock source;
* an Explanation-only run (workbench schema 3, run V9) with the Rules source; and,
* with ``--with-llm``, a Bounded AI cognition run with the Local source and 10 funded
  responses, after warming the model in Ollama, reusing or creating the ``local-ollama``
  profile revision the browser would create, and passing the bounded-signal check.

Each study uses the bundled fictional city and creative, two fictional persona templates
with four people each, one model day, and seed 1 by default. The live script uses seed 42;
a different seed keeps the saved model answers of a backup run from being reused from the
workspace cache by a later live run.

Standard output gets one line per run: its ID, terminal phase, source counts, and
inspector URL. Progress goes to standard error. Any failure exits non-zero with a concise
message. The script talks only to ``127.0.0.1`` or ``localhost``, needs no credential,
writes no file, and never prints or stores the workbench security token. Every person,
place, product, and number in these runs is fictional and synthetic.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Final, TextIO

import httpx

DEFAULT_MODEL: Final = "qwen2.5:7b-instruct"
DEFAULT_RUN_PREFIX: Final = "backup"
DEFAULT_SEED: Final = "1"
DEFAULT_TIMEOUT_SECONDS: Final = 300.0
MAX_TIMEOUT_SECONDS: Final = 3_600.0

CITY_ID: Final = "fictional-grid-v2"
CREATIVE_TEMPLATE_ID: Final = "fictional-device-launch-v1"
# Fictional Lumo and Fictional Nuvin with four people each, as in the live script.
AUDIENCE: Final = (("persona-001", 4), ("persona-004", 4))
AGENT_COUNT: Final = sum(count for _persona_id, count in AUDIENCE)
MODEL_DAYS: Final = 1

BOUNDED_SCOPE: Final = "bounded-signals-v1"
LOCAL_APPROVAL_ID: Final = "local-ollama"
LOCAL_PROFILE_LABEL: Final = "Local Ollama"
LOCAL_FUNDED_RESPONSES: Final = 10
OLLAMA_KEEP_ALIVE: Final = "30m"
OUTCOMES: Final = ("rules", "mock", "provider", "cache", "fallback", "budget_skipped")
RUN_KINDS: Final = ("v10-mock", "v9-rules", "v10-local")

REQUEST_TIMEOUT_SECONDS: Final = 120.0
CONNECT_TIMEOUT_SECONDS: Final = 5.0
# The workbench gives a local-trust compatibility check up to 120 seconds.
CHECK_TIMEOUT_SECONDS: Final = 150.0
POLL_INTERVAL_SECONDS: Final = 0.25
MAX_RUN_ID_ATTEMPTS: Final = 20
MAX_RUN_PREFIX_LENGTH: Final = 20
RUN_PAGE_LIMIT: Final = 100

_TERMINAL_PHASES: Final = frozenset({"completed", "failed", "cancelled"})
_LOOPBACK_AUTHORITY = re.compile(r"(?P<host>127\.0\.0\.1|localhost):(?P<port>[1-9][0-9]{0,4})")
_LOCAL_OLLAMA_BASE_URL = re.compile(r"http://127\.0\.0\.1:(?P<port>[1-9][0-9]{0,4})/v1")
_CSRF_META = re.compile(r'<meta name="adlife-csrf-token" content="([A-Za-z0-9_-]{16,256})">')
_RUN_PREFIX = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_SEED = re.compile(r"0|[1-9][0-9]{0,18}")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}")
_JOB_ID = re.compile(r"job-[0-9a-f]{32}")
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_REVISION = re.compile(r"[0-9a-f]{64}")
_MAX_SEED: Final = 2**63 - 1
_CHECK_HINTS: Final = {
    "endpoint-resolution-refused": (
        "nothing answered at the local Ollama endpoint; start the Ollama app (or `ollama serve`)"
    ),
    "protocol-incompatible": (
        "the model may not be installed; compare --model with `ollama list` or run `ollama pull`"
    ),
    "timeout-exhausted": "the model did not answer in time; warm it again and retry",
    "credential-unavailable": "the workbench could not resolve the local profile's credential",
    "endpoint-policy-refused": "the workbench's startup policy does not allow this profile",
}


class DemoError(Exception):
    """One concise, credential-free failure message for the operator."""


class WorkbenchRefusal(DemoError):
    """The workbench answered with an error status and, usually, its bounded error document."""

    def __init__(self, message: str, *, status: int, code: str | None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class RunPlan:
    """One fictional study to submit: its run-ID infix, label, and cognition selection."""

    kind: str
    label: str
    schema_version: int
    cognition: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """The terminal facts the script reports for one submitted run."""

    run_id: str
    phase: str
    label: str
    counts: Mapping[str, int] | None = None
    inspector_url: str | None = None
    error_code: str | None = None

    def line(self) -> str:
        if self.counts is None or self.inspector_url is None:
            return f"{self.run_id}  {self.phase}  {self.label}  error={self.error_code or 'none'}"
        sources = " ".join(f"{name.replace('_', '-')}={self.counts[name]}" for name in OUTCOMES)
        return f"{self.run_id}  {self.phase}  {self.label}  {sources}  {self.inspector_url}"


@dataclass(frozen=True, slots=True)
class Options:
    """Validated command-line choices."""

    base_url: str
    with_llm: bool = False
    model: str = DEFAULT_MODEL
    run_prefix: str = DEFAULT_RUN_PREFIX
    seed: str = DEFAULT_SEED
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # Not a command-line choice: a test may skip the two offline runs.
    offline_runs: bool = field(default=True, repr=False)


def parse_base_url(raw: str) -> str:
    """Return ``http://HOST:PORT`` for a loopback workbench URL and refuse anything else.

    The refusal never repeats the value, which could carry a credential or a mistyped host.
    """
    text = raw.removesuffix("/")
    match = (
        _LOOPBACK_AUTHORITY.fullmatch(text.removeprefix("http://"))
        if text.startswith("http://")
        else None
    )
    if match is None or int(match["port"]) > 65_535:
        raise DemoError("--base-url must be http://127.0.0.1:<port> or http://localhost:<port>")
    return f"http://{match['host']}:{int(match['port'])}"


def ollama_root(local_base_url: str) -> str:
    """Return the native Ollama root behind the workbench's loopback ``/v1`` approval."""
    match = _LOCAL_OLLAMA_BASE_URL.fullmatch(local_base_url)
    if match is None or int(match["port"]) > 65_535:
        raise DemoError("the workbench's local-ollama endpoint is not a loopback Ollama URL")
    return f"http://127.0.0.1:{int(match['port'])}"


def parse_options(argv: Sequence[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        prog="demo_workspace.py",
        description=(
            "Seed a running AdLife city workbench with ready-to-open fictional demo runs "
            "through its loopback HTTP API."
        ),
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="the running workbench: http://127.0.0.1:<port> or http://localhost:<port>",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="also warm the local Ollama model and create a Local-source bounded run",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model ID for --with-llm (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--run-prefix",
        default=DEFAULT_RUN_PREFIX,
        help=f"start of every new run ID (default {DEFAULT_RUN_PREFIX})",
    )
    parser.add_argument(
        "--seed",
        default=DEFAULT_SEED,
        help=f"whole-number simulation seed (default {DEFAULT_SEED}; the live script uses 42)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds to wait for each job and for the model to load (default 300)",
    )
    arguments = parser.parse_args(argv)
    try:
        base_url = parse_base_url(arguments.base_url)
    except DemoError as error:
        parser.error(str(error))
    prefix = arguments.run_prefix
    if len(prefix) > MAX_RUN_PREFIX_LENGTH or _RUN_PREFIX.fullmatch(prefix) is None:
        parser.error(
            "--run-prefix must be 1-20 lowercase letters, digits, and single hyphens, "
            "starting with a letter"
        )
    seed = arguments.seed
    if _SEED.fullmatch(seed) is None or int(seed) > _MAX_SEED:
        parser.error("--seed must be a whole number from 0 through 9223372036854775807")
    if _MODEL_ID.fullmatch(arguments.model) is None:
        parser.error("--model must be an Ollama model ID such as qwen2.5:7b-instruct")
    timeout = arguments.timeout
    if not 1.0 <= timeout <= MAX_TIMEOUT_SECONDS:
        parser.error("--timeout must be from 1 through 3600 seconds")
    return Options(
        base_url=base_url,
        with_llm=arguments.with_llm,
        model=arguments.model,
        run_prefix=prefix,
        seed=seed,
        timeout=timeout,
    )


def _say(stream: TextIO, message: str) -> None:
    # Server messages may hold non-ASCII punctuation; a legacy console must not crash on it.
    text = message.encode("ascii", "backslashreplace").decode("ascii")
    print(f"demo_workspace: {text}", file=stream, flush=True)


def _mapping(document: Mapping[str, object], key: str, *, step: str) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, Mapping):
        raise DemoError(f"{step}: the workbench response has no {key!r} object")
    return value


def _items(document: Mapping[str, object], key: str, *, step: str) -> list[Mapping[str, object]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise DemoError(f"{step}: the workbench response has no {key!r} list")
    return list(value)


def _text(document: Mapping[str, object], key: str, *, step: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise DemoError(f"{step}: the workbench response has no {key!r} text")
    return value


def _integer(document: Mapping[str, object], key: str, *, step: str) -> int:
    value = document.get(key)
    if type(value) is not int:
        raise DemoError(f"{step}: the workbench response has no {key!r} count")
    return value


def _refusal(step: str, status: int, document: Mapping[str, object] | None) -> WorkbenchRefusal:
    error = None if document is None else document.get("error")
    if not isinstance(error, Mapping):
        return WorkbenchRefusal(f"{step} failed with HTTP {status}", status=status, code=None)
    code = error.get("code")
    text = f"{step} was refused (HTTP {status} {code}): {error.get('message')}"
    fields = error.get("fields")
    if isinstance(fields, Mapping) and fields:
        details = "; ".join(f"{name}: {reason}" for name, reason in list(fields.items())[:4])
        text = f"{text} [{details}]"
    return WorkbenchRefusal(text, status=status, code=code if isinstance(code, str) else None)


class Workbench:
    """A loopback client sending exactly the request metadata the workbench page sends."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._host = base_url.removeprefix("http://")
        self._token: str | None = None
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Host": self._host},
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def _send(
        self,
        method: str,
        path: str,
        *,
        step: str,
        body: Mapping[str, object] | None = None,
        timeout: float | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        headers = {"Accept": accept}
        content = None
        if method == "POST":
            if self._token is None:
                raise DemoError(f"{step}: the workbench security token was not read first")
            # The same-origin write contract: exact media type, exact origin, process token.
            headers["Content-Type"] = "application/json"
            headers["Origin"] = f"http://{self._host}"
            headers["X-AdLife-CSRF"] = self._token
            content = json.dumps(
                {} if body is None else body,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        try:
            return self._client.request(
                method,
                path,
                headers=headers,
                content=content,
                timeout=(
                    httpx.USE_CLIENT_DEFAULT
                    if timeout is None
                    else httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS)
                ),
            )
        except httpx.TimeoutException:
            raise DemoError(f"{step}: the workbench at {self.base_url} did not answer") from None
        except httpx.HTTPError:
            raise DemoError(
                f"{step}: cannot reach the workbench at {self.base_url}; start it with "
                "`uv run adlife city-workbench --workspace <folder> --port <port>` first"
            ) from None

    def request_json(
        self,
        method: str,
        path: str,
        *,
        step: str,
        body: Mapping[str, object] | None = None,
        timeout: float | None = None,
        expect: frozenset[int] = frozenset({200}),
    ) -> tuple[int, Mapping[str, object]]:
        response = self._send(method, path, step=step, body=body, timeout=timeout)
        document: Mapping[str, object] | None = None
        if response.headers.get("content-type", "").lower().startswith("application/json"):
            try:
                decoded: object = response.json()
            except ValueError:
                decoded = None
            if isinstance(decoded, Mapping):
                document = decoded
        if response.status_code not in expect:
            raise _refusal(step, response.status_code, document)
        if document is None:
            raise DemoError(f"{step}: the workbench returned an unexpected response")
        return response.status_code, document

    def get_json(self, path: str, *, step: str) -> Mapping[str, object]:
        return self.request_json("GET", path, step=step)[1]

    def connect(self) -> None:
        """Read the per-process security token from the page, as the browser does."""
        response = self._send("GET", "/", step="open the workbench page", accept="text/html")
        content_type = response.headers.get("content-type", "").lower()
        match = (
            _CSRF_META.search(response.text)
            if response.status_code == 200 and content_type.startswith("text/html")
            else None
        )
        if match is None:
            raise DemoError(
                f"{self.base_url} is not an AdLife city workbench: its page has no security token"
            )
        self._token = match.group(1)

    def open_inspector(self, run_id: str) -> None:
        """Require the saved-run inspector page to verify the run and open."""
        response = self._send(
            "GET", f"/runs/{run_id}", step=f"open the inspector of {run_id}", accept="text/html"
        )
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code != 200 or not content_type.startswith("text/html"):
            raise DemoError(
                f"the inspector page of {run_id} did not open (HTTP {response.status_code})"
            )


def require_bounded_workbench(workbench: Workbench, *, with_llm: bool) -> int:
    """Require bounded AI cognition and an idle worker; return the offline funded limit."""
    step = "read the workbench capabilities"
    document = workbench.get_json("/api/workbench", step=step)
    if document.get("active_job") is not None:
        raise DemoError("another run is active in this workbench; wait for it to finish")
    capabilities = _mapping(document, "capabilities", step=step)
    bounded = _mapping(capabilities, "bounded_cognition", step=step)
    modes = bounded.get("modes")
    limits = bounded.get("funded_response_limits")
    offline_limit = bounded.get("offline_funded_response_limit")
    if (
        bounded.get("effect_scope") != BOUNDED_SCOPE
        or bounded.get("workbench_schema_version") != 4
        or bounded.get("run_schema_version") != 10
        or not isinstance(modes, list)
        or "mock" not in modes
        or (with_llm and "local" not in modes)
        or not isinstance(limits, list)
        or (with_llm and LOCAL_FUNDED_RESPONSES not in limits)
        or type(offline_limit) is not int
    ):
        raise DemoError(
            "this workbench does not offer the expected bounded AI cognition "
            "(workbench schema 4, run V10); update the repository and restart the workbench"
        )
    return offline_limit


def read_audience(workbench: Workbench) -> list[dict[str, object]]:
    """Check the bundled city and creative; copy the two persona templates with counts."""
    step = "read the city catalog"
    cities = _items(workbench.get_json("/api/catalog/cities", step=step), "cities", step=step)
    if CITY_ID not in {city.get("city_id") for city in cities}:
        raise DemoError(f"the workbench offers no {CITY_ID} city")
    step = "read the creative templates"
    creatives = _items(
        workbench.get_json("/api/creative-templates", step=step), "templates", step=step
    )
    if CREATIVE_TEMPLATE_ID not in {creative.get("template_id") for creative in creatives}:
        raise DemoError(f"the workbench offers no {CREATIVE_TEMPLATE_ID} creative template")
    step = "read the persona templates"
    personas = _items(
        workbench.get_json("/api/persona-templates", step=step), "templates", step=step
    )
    templates = {persona.get("persona_id"): persona for persona in personas}
    audience: list[dict[str, object]] = []
    for persona_id, count in AUDIENCE:
        template = templates.get(persona_id)
        if template is None:
            raise DemoError(f"the workbench offers no {persona_id} persona template")
        audience.append({**template, "count": count})
    return audience


def list_run_ids(workbench: Workbench) -> set[str]:
    """Return every verified saved run ID, following the bounded pages of ``/api/runs``."""
    step = "list the saved runs"
    run_ids: set[str] = set()
    offset = 0
    while True:
        page = workbench.get_json(f"/api/runs?offset={offset}&limit={RUN_PAGE_LIMIT}", step=step)
        runs = _items(page, "runs", step=step)
        run_ids.update(_text(run, "run_id", step=step) for run in runs)
        offset += len(runs)
        if not runs or offset >= _integer(page, "verified_total", step=step):
            return run_ids


def next_run_number(existing: Iterable[str], prefix: str) -> int:
    """Return one past the highest number an earlier invocation used with this prefix."""
    kinds = "|".join(re.escape(kind) for kind in RUN_KINDS)
    pattern = re.compile(rf"{re.escape(prefix)}-(?:{kinds})-(?P<number>[0-9]{{3,}})")
    numbers = [int(match["number"]) for run_id in existing if (match := pattern.fullmatch(run_id))]
    return max(numbers, default=0) + 1


def offline_plans(offline_limit: int) -> tuple[RunPlan, ...]:
    return (
        RunPlan(
            kind="v10-mock",
            label="V10 bounded/mock",
            schema_version=4,
            cognition={
                "effect_scope": BOUNDED_SCOPE,
                "mode": "mock",
                "provider_profile_id": None,
                "provider_revision_sha256": None,
                "funded_response_limit": offline_limit,
            },
        ),
        RunPlan(
            kind="v9-rules",
            label="V9 explanation-only/rules",
            schema_version=3,
            cognition={
                "mode": "rules",
                "provider_profile_id": None,
                "provider_revision_sha256": None,
                "aggregate_output_token_ceiling": 0,
            },
        ),
    )


def local_plan(profile_id: str, revision: str) -> RunPlan:
    return RunPlan(
        kind="v10-local",
        label="V10 bounded/local",
        schema_version=4,
        cognition={
            "effect_scope": BOUNDED_SCOPE,
            "mode": "local",
            "provider_profile_id": profile_id,
            "provider_revision_sha256": revision,
            "funded_response_limit": LOCAL_FUNDED_RESPONSES,
        },
    )


def build_draft(
    plan: RunPlan, *, run_id: str, seed: str, audience: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """The browser's draft for the live script's study, with this run's ID and cognition."""
    return {
        "schema_version": plan.schema_version,
        "city_id": CITY_ID,
        "scenario": {
            "scenario_id": "ollama-demo-study",
            "name": "Fictional Ollama demo study",
            "campaign": {
                "campaign_id": "fictional-launch",
                "name": "Fictional launch",
                "creative_template_id": CREATIVE_TEMPLATE_ID,
                "target_interests": ["commuting", "technology"],
                "relative_price": 1.25,
            },
            "phone": {
                "active_windows": [{"day": 1, "start_minute": 420, "end_minute": 1260}],
                "frequency_cap_per_agent_per_day": 4,
                "eligible_activities": ["home", "commute", "leisure"],
                "opportunity_probability_per_minute": 0.05,
            },
            "roadside": None,
        },
        "population": {"personas": [dict(persona) for persona in audience]},
        "cognition": dict(plan.cognition),
        "settings": {
            "run_id": run_id,
            "agent_count": AGENT_COUNT,
            "days": MODEL_DAYS,
            # The workbench transport takes the seed as an exact decimal string.
            "seed": seed,
            "response_mode": "deterministic-rules",
        },
    }


def read_source_counts(workbench: Workbench, run_id: str) -> dict[str, int]:
    """Read the six terminal source counts from the verified V9 or V10 cognition summary."""
    step = f"read the cognition summary of {run_id}"
    view = workbench.get_json(f"/api/runs/{run_id}/cognition-summary", step=step)
    summary = _mapping(view, "summary", step=step)
    key = "outcomes" if view.get("schema_version") == 2 else "outcome_counts"
    counts = _mapping(summary, key, step=step)
    return {name: _integer(counts, name, step=step) for name in OUTCOMES}


def _cancel(workbench: Workbench, job_id: str, run_id: str) -> None:
    with suppress(DemoError):
        workbench.request_json(
            "POST", f"/api/jobs/{job_id}/cancel", step=f"cancel the job of {run_id}", body={}
        )


def follow_job(
    workbench: Workbench,
    job: Mapping[str, object],
    *,
    plan: RunPlan,
    run_id: str,
    timeout: float,
    err: TextIO,
) -> RunOutcome:
    """Poll one job to a terminal phase, then verify its saved run end to end."""
    step = f"follow the job of {run_id}"
    job_id = _text(job, "job_id", step=step)
    if _JOB_ID.fullmatch(job_id) is None:
        raise DemoError(f"{step}: the workbench returned an unexpected job ID")
    started = time.monotonic()
    phase = _text(job, "phase", step=step)
    announced = ""
    try:
        while True:
            if phase != announced:
                _say(err, f"  {run_id}: {phase} ({time.monotonic() - started:.1f} s)")
                announced = phase
            if phase in _TERMINAL_PHASES:
                break
            if time.monotonic() - started >= timeout:
                _cancel(workbench, job_id, run_id)
                raise DemoError(
                    f"{run_id} did not finish within {timeout:g} s (last phase {phase}); "
                    "its cancellation was requested"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            job = workbench.get_json(f"/api/jobs/{job_id}", step=step)
            phase = _text(job, "phase", step=step)
    except KeyboardInterrupt:
        _cancel(workbench, job_id, run_id)
        raise
    if phase != "completed":
        error = job.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        return RunOutcome(
            run_id=run_id,
            phase=phase,
            label=plan.label,
            error_code=code if isinstance(code, str) else None,
        )
    inspector_path = _text(_mapping(job, "result", step=step), "inspector_url", step=step)
    if inspector_path != f"/runs/{run_id}":
        raise DemoError(f"{step}: the workbench returned an unexpected inspector URL")
    counts = read_source_counts(workbench, run_id)
    workbench.open_inspector(run_id)
    return RunOutcome(
        run_id=run_id,
        phase=phase,
        label=plan.label,
        counts=counts,
        inspector_url=f"{workbench.base_url}{inspector_path}",
    )


def create_run(
    workbench: Workbench,
    plan: RunPlan,
    *,
    options: Options,
    number: int,
    audience: Sequence[Mapping[str, object]],
    err: TextIO,
) -> RunOutcome:
    """Validate and submit one study under the first free numbered run ID, then follow it."""
    for attempt in range(MAX_RUN_ID_ATTEMPTS):
        run_id = f"{options.run_prefix}-{plan.kind}-{number + attempt:03d}"
        draft = build_draft(plan, run_id=run_id, seed=options.seed, audience=audience)
        _say(err, f"{run_id}: {plan.label}, seed {options.seed}")
        _, validation = workbench.request_json(
            "POST", "/api/scenarios/validate", step=f"validate {run_id}", body=draft
        )
        if validation.get("valid") is not True:
            raise DemoError(f"validate {run_id}: the workbench did not confirm the draft")
        try:
            _, accepted = workbench.request_json(
                "POST", "/api/jobs", step=f"start {run_id}", body=draft, expect=frozenset({202})
            )
        except WorkbenchRefusal as refusal:
            # A saved but unlisted (for example damaged) run can still hold this ID.
            if refusal.status == 409 and refusal.code == "run-conflict":
                _say(err, f"{run_id} is already used in this workspace; trying the next number")
                continue
            raise
        job = _mapping(accepted, "job", step=f"start {run_id}")
        return follow_job(
            workbench, job, plan=plan, run_id=run_id, timeout=options.timeout, err=err
        )
    raise DemoError(f"no free run ID after {MAX_RUN_ID_ATTEMPTS} tries; choose another prefix")


def local_profile_draft(model: str, base_url: str) -> dict[str, object]:
    """The exact draft the browser's prefilled local-ollama form saves for this model."""
    return {
        "schema_version": 1,
        "profile_id": LOCAL_APPROVAL_ID,
        "display_label": LOCAL_PROFILE_LABEL,
        "mode": "local",
        "base_url": base_url,
        "model_id": model,
        "protocol": "openai-chat-completions-json-schema-v1",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed_policy": "fixed",
        "seed": 42,
        "max_output_tokens": 384,
        "attempt_timeout_seconds": 30,
        "transient_retries": 2,
        "repair_attempts": 1,
        "origin_approval_id": LOCAL_APPROVAL_ID,
    }


def _matching_profile(
    profiles: Sequence[Mapping[str, object]], draft: Mapping[str, object]
) -> Mapping[str, object] | None:
    for profile in profiles:
        if all(profile.get(name) == value for name, value in draft.items()):
            return profile
    return None


def local_endpoint(workbench: Workbench) -> str:
    """Return the base URL of the workbench's built-in loopback ``local-ollama`` approval."""
    step = "read the provider endpoints"
    providers = workbench.get_json("/api/providers", step=step)
    origin = next(
        (
            item
            for item in _items(providers, "origins", step=step)
            if item.get("approval_id") == LOCAL_APPROVAL_ID and item.get("trust_class") == "local"
        ),
        None,
    )
    if origin is None:
        raise DemoError("the workbench offers no local-ollama endpoint")
    return _text(origin, "base_url", step=step)


def ensure_local_profile(
    workbench: Workbench, model: str, base_url: str, *, err: TextIO
) -> tuple[str, str]:
    """Reuse or create the local-ollama revision for this model; return its ID and revision."""
    step = "read the provider profiles"
    providers = workbench.get_json("/api/providers", step=step)
    draft = local_profile_draft(model, base_url)
    profile = _matching_profile(_items(providers, "profiles", step=step), draft)
    if profile is not None:
        _say(err, f"reusing the {LOCAL_APPROVAL_ID} profile revision for {model}")
    else:
        try:
            _, profile = workbench.request_json(
                "POST",
                "/api/providers",
                step="create the local-ollama profile",
                body=draft,
                expect=frozenset({201}),
            )
            _say(err, f"created the {LOCAL_APPROVAL_ID} profile revision for {model}")
        except WorkbenchRefusal as refusal:
            if refusal.code != "provider-profile-conflict":
                raise
            # Saved meanwhile, for example from the browser: the same immutable revision.
            providers = workbench.get_json("/api/providers", step=step)
            profile = _matching_profile(_items(providers, "profiles", step=step), draft)
            if profile is None:
                raise
    profile_id = _text(profile, "profile_id", step=step)
    revision = _text(profile, "revision_sha256", step=step)
    if _PROFILE_ID.fullmatch(profile_id) is None or _REVISION.fullmatch(revision) is None:
        raise DemoError(f"{step}: the workbench returned an unexpected profile revision")
    if profile.get("endpoint_policy_accepted") is not True:
        raise DemoError("the workbench's startup policy does not allow the local-ollama profile")
    if profile.get("credential_available") is not True:
        raise DemoError("the workbench cannot resolve the local-ollama profile's credential")
    return profile_id, revision


def warm_model(root: str, model: str, *, timeout: float, err: TextIO) -> None:
    """Load the model with Ollama's keep-alive call, so the check need not wait for it."""
    _say(err, f"warming {model} in Ollama (a cold start can take about 100 s)")
    started = time.monotonic()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                f"{root}/api/generate",
                json={"model": model, "keep_alive": OLLAMA_KEEP_ALIVE, "stream": False},
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException:
        raise DemoError(f"Ollama did not load {model} within {timeout:g} s") from None
    except httpx.HTTPError:
        raise DemoError(
            f"Ollama is not reachable at {root.removeprefix('http://')}; start the Ollama app "
            "(or `ollama serve`) and retry"
        ) from None
    if response.status_code == 404:
        raise DemoError(f"Ollama has no model {model}; run `ollama pull {model}` first")
    try:
        document: object = response.json()
    except ValueError:
        document = None
    if (
        response.status_code != 200
        or not isinstance(document, Mapping)
        or document.get("done") is not True
    ):
        raise DemoError(f"Ollama could not load {model} (HTTP {response.status_code})")
    _say(err, f"{model} is loaded for {OLLAMA_KEEP_ALIVE} ({time.monotonic() - started:.1f} s)")


def run_bounded_check(workbench: Workbench, profile_id: str, revision: str, *, err: TextIO) -> None:
    """Pass the bounded-signal compatibility check the workbench requires before the run."""
    step = "run the bounded-signal check"
    path = f"/api/providers/{profile_id}/revisions/{revision}"
    _say(err, "running the bounded-signal compatibility check")
    status, result = workbench.request_json(
        "POST",
        f"{path}/check",
        step=step,
        body={"effect_scope": BOUNDED_SCOPE},
        timeout=CHECK_TIMEOUT_SECONDS,
        expect=frozenset({200, 429}),
    )
    if status == 429:
        # At most five checks start per minute; an earlier pass in this process still holds.
        status, result = workbench.request_json(
            "GET", f"{path}/bounded-check", step=step, expect=frozenset({200, 404})
        )
        if status != 200:
            raise DemoError(
                "the workbench starts at most five checks per minute and has no earlier pass "
                "for this profile revision; wait a minute and retry"
            )
    outcome = result.get("status")
    if outcome != "compatible":
        reason = result.get("reason_code")
        hint = _CHECK_HINTS.get(reason, "") if isinstance(reason, str) else ""
        raise DemoError(
            f"the bounded-signal check did not pass: {outcome} ({reason})"
            + (f"; {hint}" if hint else "")
        )
    _say(err, "bounded-signal contract confirmed")


def seed_workspace(options: Options, *, out: TextIO, err: TextIO) -> list[RunOutcome]:
    """Create the demo runs, printing one line per run as it reaches a terminal phase."""
    workbench = Workbench(options.base_url)
    outcomes: list[RunOutcome] = []

    def submit(plan: RunPlan, *, number: int, audience: Sequence[Mapping[str, object]]) -> None:
        outcome = create_run(
            workbench, plan, options=options, number=number, audience=audience, err=err
        )
        outcomes.append(outcome)
        print(outcome.line(), file=out, flush=True)
        if outcome.phase != "completed":
            raise DemoError(f"{outcome.run_id} ended {outcome.phase} ({outcome.error_code})")

    try:
        workbench.connect()
        offline_limit = require_bounded_workbench(workbench, with_llm=options.with_llm)
        audience = read_audience(workbench)
        number = next_run_number(list_run_ids(workbench), options.run_prefix)
        _say(err, f"seeding {options.base_url}: {AGENT_COUNT} fictional agents, 1 model day")
        if options.offline_runs:
            for plan in offline_plans(offline_limit):
                submit(plan, number=number, audience=audience)
        if options.with_llm:
            # Load the model first: a mistyped --model must not leave a saved profile revision.
            local_base_url = local_endpoint(workbench)
            warm_model(ollama_root(local_base_url), options.model, timeout=options.timeout, err=err)
            profile_id, revision = ensure_local_profile(
                workbench, options.model, local_base_url, err=err
            )
            run_bounded_check(workbench, profile_id, revision, err=err)
            submit(local_plan(profile_id, revision), number=number, audience=audience)
            cached = outcomes[-1].counts
            if cached is not None and cached["cache"] > 0:
                _say(
                    err,
                    f"{outcomes[-1].run_id} reused {cached['cache']} saved model answers from "
                    "this workspace's cache; pass another --seed for new model calls",
                )
        listed = list_run_ids(workbench)
        missing = [outcome.run_id for outcome in outcomes if outcome.run_id not in listed]
        if missing:
            raise DemoError(f"the saved run list does not include {', '.join(missing)}")
    finally:
        workbench.close()
    ready = f"{len(outcomes)} run{'' if len(outcomes) == 1 else 's'} ready"
    _say(err, f"{ready}: open a URL above, or INSPECT in the workbench")
    return outcomes


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_options(argv)
    try:
        seed_workspace(options, out=sys.stdout, err=sys.stderr)
    except DemoError as error:
        _say(sys.stderr, f"error: {error}")
        return 1
    except KeyboardInterrupt:
        _say(sys.stderr, "interrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
