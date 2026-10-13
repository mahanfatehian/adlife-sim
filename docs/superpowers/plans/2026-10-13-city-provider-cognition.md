# City Provider Cognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional local and remote OpenAI-compatible AI explanations to city runs while keeping movement, exposure, response, purchase-intention state, events, and metrics entirely rule-owned and replayable offline. The simulator still has no purchase-transaction model.

**Architecture:** Define a provider-neutral, narrative-only core contract; add schema-3 workbench inputs and immutable provider-profile revisions; resolve credentials only from OS keyring or explicitly bound environment variables; call a hardened OpenAI-compatible Chat Completions subset through a new city-specific adapter; freeze validated terminal records in manifest V9; expose configuration and evidence through bounded APIs and a concise UI. The provider never receives authority to mutate scientific state.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, httpx/httpcore, keyring, FastAPI, vanilla HTML/CSS/JavaScript, Playwright, pytest, uv/Hatch.

**Spec:** `docs/superpowers/specs/2026-10-12-city-provider-cognition-design.md`

**Dependency Gate:** Complete and certify both the persona/schema-2/V8 plan and the city-registry plan, including its route-specific request-size mechanism, before starting this plan. Do not retrofit the legacy numeric `CognitionResult` or reuse `OpenAICompatibleProvider` unchanged.

## Global Constraints

- AI output is explanatory evidence only. It cannot supply probabilities, numeric modifiers, movement, events, budgets, identities, memory, or state, including purchase-intention state; the simulator has no purchase-transaction model.
- Rules and mock modes remain offline, deterministic defaults. Local/remote failures produce a grounded deterministic fallback only for expected provider failures.
- Every selected request receives one terminal record in stable order; incomplete cognition never publishes a run.
- Secrets never enter prompts, profiles, manifests, cache records, usage summaries, logs, exceptions, DTOs, reports, or browser state.
- Live provider calls are not reproducible; frozen V9 replay is fully offline and verifies recorded evidence against deterministic scientific inputs.
- Remote endpoints are HTTPS, explicitly startup-approved, public-address only, proxy-independent, redirect-free, and rebound-safe. Local endpoints are literal loopback approvals only.
- Keep `oauth: false`: product login, subscription, identity tokens, cookies, and ambient ChatGPT/Codex credentials are not accepted by this Chat Completions contract. Any provider-specific OAuth implementation requires a separate reviewed design.
- Keep `src/adlife/core` adapter-free. Use TDD, `uv`, exact assertions, focused commits, and push accepted commits to `origin/main`.

## Review Focus

- No provider/profile/prose/cache variation can change deterministic scientific bytes.
- Keys echoed by a provider, HTTP library, keyring backend, repair prompt, or browser error must still be absent everywhere.
- Mixed-address DNS, rebinding, peer mismatch, proxy inheritance, redirect, or lost TLS hostname must fail closed.
- Cancellation/deadline races cannot publish an incomplete run or schedule work after cancellation.
- Missing, duplicated, reordered, rehashed, or cross-bound cognition records must fail replay verification.

---

### Task 1: Provider-Neutral Narrative Contract

**Files:**
- Create: `src/adlife/core/domain/city_cognition.py`
- Create: `src/adlife/core/ports/city_cognition.py`
- Create: `src/adlife/core/simulation/city_cognition.py`
- Create: `tests/unit/city/test_city_cognition_contract.py`
- Create: `tests/unit/city/test_city_cognition.py`
- Modify: `tests/architecture/test_core_import_boundary.py`

**Interfaces:**

```python
class CityCognitionProvider(Protocol):
    async def call(self, request: CityCognitionRequestV1) -> CityProviderAttemptV1: ...
    async def repair_call(
        self,
        request: CityCognitionRequestV1,
        *,
        context: CityProviderRepairContextV1,
    ) -> CityProviderAttemptV1: ...

def build_city_cognition_requests(
    context: CityCognitionRunContextV1,
    mobility: CityMobility,
    response_evaluation: SpatialResponseEvaluation,
) -> tuple[CityCognitionRequestV1, ...]: ...

def allocate_city_cognition_reservations(
    requests: tuple[CityCognitionRequestV1, ...],
    *,
    aggregate_output_token_ceiling: int,
) -> tuple[CityCognitionReservationV1, ...]: ...

def validate_city_cognition_evaluation(
    cognition_input: CityCognitionInputV1,
    requests: tuple[CityCognitionRequestV1, ...],
    records: tuple[CityCognitionTerminalRecordV1, ...],
) -> CityCognitionEvaluationV1: ...
```

Add strict V1 request/result/attempt/repair-context/reservation/terminal-record/summary/input/evaluation/run-context/agent-context/campaign-context/profile-snapshot models. `CityProviderRepairContextV1` contains only a bounded reason code and screened safe excerpt, never raw provider content. Result fields are exactly matching `request_id`, `interpretation` (1-480 safe characters), one to three unique `decision_factors` (1-120 characters), zero to three enumerated safety flags, and the exact synthetic-narrative disclosure.

- [ ] **Step 1: Write failing contract tests** forbidding unknown and numeric influence fields, identity/event/state/movement/probability/budget injection, invalid IDs, unsafe text, nonfinite values, and oversized requests/records.
- [ ] **Step 2: Write failing selection/invariance tests** for content-derived request IDs/hashes, persisted noticed responses only, shared state-update/activity binding, sort order `(model_minute, agent_id, campaign_id, response_event_id)`, four per agent, 60 globally, and input/async/hash-seed invariance.
- [ ] **Step 3: Prove provider prose changes cannot alter mobility, opportunities, attention, responses, purchase-intention state, events, or metrics.** Prove descriptive persona edits do not affect numeric evidence and state explicitly that no purchase-transaction model exists.
- [ ] **Step 4: Run focused tests and confirm missing interfaces.**

  Run: `uv run pytest tests/unit/city/test_city_cognition_contract.py tests/unit/city/test_city_cognition.py tests/architecture/test_core_import_boundary.py -q`

- [ ] **Step 5: Implement deterministic request construction, reservation, rules/mock/fallback results, and terminal validation.** Requests and records each remain at most 8,192 canonical bytes.
- [ ] **Step 6: Re-run tests, Ruff, mypy, and commit.**

  Commit: `feat(city): define noncausal cognition evidence`

### Task 2: Workbench Schema 3

**Files:**
- Modify: `src/adlife/city/workbench_input.py`
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_validation.py`
- Modify: `src/adlife/city/workbench_binding.py`
- Modify: `tests/unit/city/test_workbench_input.py`
- Modify: `tests/unit/city/test_workbench_transport.py`
- Modify: `tests/unit/city/test_workbench_validation.py`
- Modify: `tests/unit/city/test_workbench_binding.py`

**Interfaces:**

```python
class WorkbenchCognitionDraftV3(DomainModel):
    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: str | None
    provider_revision_sha256: str | None
    aggregate_output_token_ceiling: int

class NormalizedWorkbenchCognitionV3(DomainModel):
    mode: Literal["rules", "mock", "local", "remote"]
    provider_profile_id: str | None
    provider_revision_sha256: str | None
    aggregate_output_token_ceiling: int
    prompt_contract_version: Literal[1]
    result_schema_version: Literal[1]
    selection_policy_version: Literal[1]
    fallback_policy_version: Literal[1]
    selected_requests_per_agent: Literal[4]
    selected_requests_per_run: Literal[60]
    concurrency_limit: Literal[4]
    max_request_bytes: Literal[8192]
    max_wire_response_bytes: Literal[1048576]
    max_output_tokens_per_attempt: Literal[384]
    reservation_tokens_per_request: Literal[1536]
    initial_attempts: Literal[1]
    transient_retries: Literal[2]
    repair_attempts: Literal[1]
    attempt_timeout_seconds: Literal[30]
    aggregate_deadline_seconds: Literal[180]

WorkbenchRunDraftDocument = WorkbenchRunDraft | WorkbenchRunDraftV2 | WorkbenchRunDraftV3
WorkbenchRunInputDocument = WorkbenchRunInput | WorkbenchRunInputV2 | WorkbenchRunInputV3

def build_city_cognition_run_context(
    workbench_input: WorkbenchRunInputV3,
    *,
    response_evaluation: SpatialResponseEvaluation,
) -> CityCognitionRunContextV1: ...
```

- [ ] **Step 1: Write failing model/dispatch tests** for schema-2 population retention, exact rules/mock/local/remote combinations, 64-hex revisions, bool-as-int refusal, frozen policy/limit constants, deterministic-rules response mode, and V1/V2 compatibility. Rules/mock require no profile and an aggregate provider ceiling exactly 0. Local/remote require a matching immutable profile ID/hash and a ceiling from 0 through 92,160 divisible by 1,536.
- [ ] **Step 2: Run focused tests and confirm schema 3 is unsupported.**

  Run: `uv run pytest tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_binding.py -q`

- [ ] **Step 3: Implement `WorkbenchRunDraftV3`, `NormalizedWorkbenchCognitionV3`, normalized draft, `WorkbenchRunInputV3`, exact unions, and run-context construction.** Freeze the prompt/result-schema/selection/fallback versions and every hard selection, request, wire, concurrency, attempt, token, timeout, and deadline ceiling in the workbench input. Task 3 profiles select lower effective settings where allowed; `CityCognitionInputV1` freezes those resolved effective values separately.
- [ ] **Step 4: Re-run tests and commit.**

  Commit: `feat(workbench): add versioned explanation settings`

### Task 3: Immutable Profiles and Secret Storage

**Files:**
- Create: `src/adlife/city/provider_profiles.py`
- Create: `src/adlife/city/provider_runtime.py`
- Create: `src/adlife/adapters/cognition/city_credentials.py`
- Modify: `src/adlife/cli/commands/city_workbench.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/unit/city/test_provider_profiles.py`
- Create: `tests/unit/city/test_provider_runtime.py`
- Create: `tests/unit/cognition/test_city_credentials.py`
- Modify: `tests/security/test_redaction_corpus.py`
- Modify: `tests/cli/test_city_workbench.py`

**Dependencies:** Add with `uv add`: `keyring>=25,<26` and direct `httpcore>=1.0.9,<1.1`.

**Interfaces:**

```python
class ProviderProfileDraftV1(DomainModel):
    profile_id: str  # portable 1-64 character slug
    display_label: str  # 1-80 safe characters
    mode: Literal["local", "remote"]
    base_url: str  # normalized, <= 2,048 characters
    model_id: str  # 1-120 safe characters
    protocol: Literal["openai-chat-completions-json-schema-v1"]
    temperature: float  # finite, 0.0-2.0
    top_p: float  # finite, > 0.0 and <= 1.0
    seed_policy: Literal["omit", "fixed"]
    seed: int | None  # exact 0..2**63-1 iff policy is fixed
    max_output_tokens: int  # exact 1-384
    attempt_timeout_seconds: int  # exact 1-30
    transient_retries: int  # exact 0-2
    repair_attempts: int  # exact 0-1
    origin_approval_id: str

class ProviderProfileRevisionV1(ProviderProfileDraftV1):
    revision_sha256: Hash
    credential_slot_id: str

class ProviderProfileRegistry:
    def list(self) -> tuple[ProviderProfileRevisionV1, ...]: ...
    def create(self, draft: ProviderProfileDraftV1) -> ProviderProfileRevisionV1: ...
    def load(self, profile_id: str, revision_sha256: str) -> ProviderProfileRevisionV1: ...

class CityCredentialStore(Protocol):
    def available(self, profile: ProviderProfileRevisionV1) -> bool: ...
    def resolve(self, profile: ProviderProfileRevisionV1) -> ResolvedCredential: ...
    def store(self, profile: ProviderProfileRevisionV1, credential: SecretBuffer) -> None: ...
```

- [ ] **Step 1: Write failing profile tests** for every exact field/range and cross-field seed rule above, canonical immutable files at `<workspace>/provider-profiles/<profile_id>/<revision_sha256>.json`, 20 IDs, ten revisions per ID, 16,384-byte limit, collision/concurrency/reparse refusal, stable hashes, and no mutable current pointer/deletion. Prove lower output-token/timeout/retry/repair settings and sampling/seed choices are frozen in `CityCognitionInputV1` while hard ceilings remain unchanged.
- [ ] **Step 2: Write failing credential/redaction tests** for an approved OS-backed keyring taking precedence over an origin-bound environment variable, local dummy credentials, unavailable/failing/plaintext/insecure third-party backend refusal, visible-ASCII 1-4,096-byte keys, best-effort `SecretBuffer.clear()`, and absence from reprs/logs/errors/artifacts/DTOs. Inject fake keyring backends so CI never invokes an OS prompt; never inspect an ambient provider variable unless startup policy names it.
- [ ] **Step 3: Write failing CLI tests** for repeatable non-secret `--provider-origin ID=URL` and `--provider-key-env ID=ENVIRONMENT_VARIABLE`, default `local-ollama=http://127.0.0.1:11434/v1`, and command-line credential refusal.
- [ ] **Step 4: Run focused tests and confirm missing dependencies/interfaces.**

  Run: `uv run pytest tests/unit/city/test_provider_profiles.py tests/unit/city/test_provider_runtime.py tests/unit/cognition/test_city_credentials.py tests/security/test_redaction_corpus.py tests/cli/test_city_workbench.py -q`

- [ ] **Step 5: Add dependencies with `uv`, implement immutable profiles/startup policy/credential stores, and clear copied secrets in `finally` as a best-effort memory hygiene measure rather than a zeroization guarantee.** Profile edits produce a new revision and distinct keyring username; a keyring backend must pass the explicit OS-backed policy before use.
- [ ] **Step 6: Re-run tests, inspect lockfile changes, and commit.**

  Commit: `feat(workbench): store immutable provider profiles`

### Task 4: Hardened Endpoint, Prompt, Transport, and Cache

**Files:**
- Create: `src/adlife/adapters/cognition/city_endpoint_policy.py`
- Create: `src/adlife/adapters/cognition/city_prompts.py`
- Create: `src/adlife/adapters/cognition/city_openai_compatible.py`
- Create: `src/adlife/adapters/cognition/city_cache.py`
- Create: `tests/unit/cognition/test_city_endpoint_policy.py`
- Create: `tests/unit/cognition/test_city_prompts.py`
- Create: `tests/unit/cognition/test_city_openai_compatible.py`
- Create: `tests/unit/cognition/test_city_cache.py`
- Create: `tests/security/test_city_provider_security.py`
- Modify: `tests/security/test_redaction_corpus.py`

**Interfaces:**

```python
class CityEndpointPolicy:
    def bind(self, profile: ProviderProfileRevisionV1) -> BoundProviderEndpoint: ...
    async def resolve_attempt(
        self,
        endpoint: BoundProviderEndpoint,
    ) -> ResolvedProviderEndpoint: ...

class CityOpenAICompatibleProvider(CityCognitionProvider): ...

class CityCognitionCache:
    @staticmethod
    def make_key(
        request: CityCognitionRequestV1,
        cognition_input: CityCognitionInputV1,
    ) -> str: ...
    def get(self, key: str) -> CityCognitionCacheRecordV1 | None: ...
    def put(self, key: str, record: CityCognitionCacheRecordV1) -> None: ...
```

Cache records live at `<workspace>/city-cognition-cache/<key>.json` with SHA-256-only filenames, pinned-directory checks, no-clobber publication, canonical bytes, and symlink/reparse refusal. A corrupt or cross-bound record raises a typed corruption error; it is never a miss, fallback, deletion, or overwrite.

- [ ] **Step 1: Write failing endpoint tests** for literal `127.0.0.1`/`[::1]` local approvals, `localhost` refusal, remote HTTPS and exact origin/base-path approval, userinfo/query/fragment/header/UDS/redirect refusal, all private/special IPv4/IPv6 tables, mixed DNS answers, rebinding, peer mismatch, proxy environment variables, and TLS hostname preservation.
- [ ] **Step 2: Write failing prompt/transport tests** for untrusted-data delimiters, no chain-of-thought request, strict JSON-schema output, non-streaming `POST <base>/chat/completions`, `trust_env=False`, no redirects, HTTP/1.1, fresh per-attempt DNS/socket, deterministic public-IP pinning, original Host/SNI/certificate checks, `Accept-Encoding: identity`, refusal of any hostile `Content-Encoding`, separate 1 MiB wire and decoded bounds, compression bombs, SSE, tool/function calls, content arrays, and TLS hostname preservation. Assert that request `temperature`, `top_p`, fixed-versus-omitted `seed`, and `max_tokens`, plus the whole-attempt timeout, come from the frozen `CityCognitionInputV1` effective settings and cannot be reread from the mutable profile registry.
- [ ] **Step 3: Write failing provider/cache security tests** for huge/deep/malformed/nonfinite/unknown/numeric output, request-ID mismatch, hostile prompts, slow streams, secret echoes, no raw-response persistence, bound cache hashes, corrupt-record hard failure, safe no-clobber cache publication, and no silent deletion/overwrite. Raw invalid provider content never crosses the adapter boundary: strip every exact known secret and run provider-secret/sensitive-text screening before repair; if a credential echo or unsafe content remains, skip repair and return `invalid-result`.
- [ ] **Step 4: Run focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/cognition/test_city_endpoint_policy.py tests/unit/cognition/test_city_prompts.py tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_cache.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py -q`

- [ ] **Step 5: Implement a city-specific adapter.** Reuse only proven redaction/envelope/size concepts from the legacy adapter; do not reuse its numeric result type or weaker endpoint policy. `CityProviderRepairContextV1` contains only a bounded reason code and screened safe excerpt; it cannot carry a raw body or credential-shaped text.
- [ ] **Step 6: Re-run tests, Ruff, mypy, and commit.**

  Commit: `feat(cognition): harden city provider transport`

### Task 5: Bounded Service and Cancellable Job Phases

**Files:**
- Create: `src/adlife/adapters/cognition/city_service.py`
- Modify: `src/adlife/city/runs.py`
- Create: `tests/unit/cognition/test_city_service.py`
- Modify: `tests/unit/city/test_city_run_preparation.py`

**Interfaces:**

```python
RETRYABLE_PROVIDER_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS_SECONDS = (0.25, 0.75)

class CityCognitionCancellation:
    def bind_running_loop(self, loop: asyncio.AbstractEventLoop) -> None: ...
    def cancel(self) -> None: ...
    async def wait(self) -> None: ...
    def raise_if_cancelled(self) -> None: ...

class CityCognitionService:
    async def resolve_many(
        self,
        cognition_input: CityCognitionInputV1,
        requests: tuple[CityCognitionRequestV1, ...],
        *,
        provider: CityCognitionProvider | None,
        cancellation: CityCognitionCancellation,
    ) -> CityCognitionEvaluationV1: ...

def prepare_city_scientific_run(...) -> PreparedCityScientificRun: ...
```

- [ ] **Step 1: Write failing service tests** for deterministic 1,536-token reservation winners allocated before cache lookup, four concurrent calls, 384-token attempted-slice accounting, exact status set `{429, 500, 502, 503, 504}`, exact no-jitter delays `(0.25, 0.75)`, 180-second aggregate deadline, funded cache hits consuming zero attempt slices without reallocating reservations, corrupt-cache hard failure, and exact retryable/nonretryable classifications. Parameterize the frozen effective settings to prove reduced whole-attempt timeout, transient-retry count `0..2`, and repair count `0..1` are honored exactly at runtime while reservation capacity remains 1,536 and every attempted slice remains 384 regardless of a profile's lower `max_output_tokens`.
- [ ] **Step 2: Write failing completion/cancellation tests** requiring one ordered terminal record per selection, deterministic fallback when the aggregate deadline expires or an expected runtime provider failure occurs, propagation of programming/input/storage/cache-corruption errors, prompt cancellation of DNS/connect/read/repair, no post-cancel work, no publication, and credential clearing/provider close. Run the async segment inside the existing executor worker's owned event loop; `cancel()` uses a locked bound-loop/event bridge and `loop.call_soon_threadsafe` so the HTTP await is interrupted rather than polled.
- [ ] **Step 3: Cover cancellation before loop binding, during DNS/connect/read/repair, after evaluation, during service shutdown, and before executor drain.** Operator cancellation yields no publishable evaluation while deadline exhaustion yields complete fallback evidence.
- [ ] **Step 4: Run focused tests and confirm failure.**

  Run: `uv run pytest tests/unit/cognition/test_city_service.py tests/unit/city/test_city_run_preparation.py -q`

- [ ] **Step 5: Implement scientific preparation, bounded cognition evaluation, and the thread-safe event-loop cancellation bridge.** Do not construct V9 yet. Preserve `prepare_city_run()` as the V1-V8 compatibility wrapper; Task 6 owns V9 finalization and publication.
- [ ] **Step 6: Re-run tests and commit.**

  Commit: `feat(workbench): resolve bounded city explanations`

### Task 6: Manifest V9, Atomic Storage, and Offline Replay

**Files:**
- Modify: `src/adlife/core/domain/city_run.py`
- Modify: `src/adlife/city/run_store.py`
- Modify: `src/adlife/city/runs.py`
- Modify: `src/adlife/city/analysis.py`
- Modify: `src/adlife/city/studies.py`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/cli/commands/city_view.py`
- Modify: `src/adlife/cli/commands/city_metrics.py`
- Modify: `src/adlife/cli/commands/city_compare.py`
- Modify: `src/adlife/core/experiments/_spatial_study_receipts.py`
- Modify: `src/adlife/core/experiments/_spatial_study_validation.py`
- Modify: `src/adlife/core/experiments/spatial_metrics.py`
- Modify: `src/adlife/core/experiments/spatial_response_metrics.py`
- Modify: `src/adlife/core/experiments/spatial_observations.py`
- Modify: `src/adlife/core/experiments/spatial_study.py`
- Modify: `tests/unit/city/test_city_run_contract.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/unit/city/test_workbench_runs.py`
- Modify: `tests/unit/city/test_city_analysis.py`
- Modify: `tests/unit/city/test_city_web.py`
- Modify: `tests/unit/city/test_spatial_study_contract.py`
- Modify: `tests/unit/experiments/test_spatial_metrics.py`
- Modify: `tests/unit/experiments/test_spatial_response_metrics.py`
- Modify: `tests/unit/experiments/test_spatial_observations.py`
- Modify: `tests/unit/experiments/test_spatial_study_result.py`
- Modify: `tests/unit/experiments/test_spatial_comparison.py`
- Modify: `tests/unit/experiments/test_spatial_response_comparison.py`
- Create: `tests/integration/test_city_cognition_artifacts.py`
- Modify: `tests/integration/test_city_run_store.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Modify: `tests/integration/test_city_workbench_artifacts.py`
- Modify: `tests/integration/test_city_spatial_study.py`
- Modify: `tests/cli/test_city_replay.py`
- Modify: `tests/cli/test_city_view.py`
- Modify: `tests/cli/test_city_metrics.py`
- Modify: `tests/cli/test_city_compare.py`
- Modify: `tests/browser/test_spatial_report_browser.py`
- Modify: `tests/security/test_spatial_report_security.py`

**Interfaces and artifacts:** Add `CityRunManifestV9` and exact V9 unions. Extend `PreparedCityRun`/`StoredCityRun` with optional verified cognition input/evaluation.

```python
def finalize_city_run(
    prepared: PreparedCityScientificRun,
    *,
    cognition_input: CityCognitionInputV1,
    cognition_evaluation: CityCognitionEvaluationV1,
) -> PreparedCityRun: ...

class CityCognitionManifestReceiptV1(DomainModel):
    cognition_input_sha256: Hash
    cognition_input_bytes: int
    request_schema_version: Literal[1]
    result_schema_version: Literal[1]
    explainer_id: str
    prompt_contract_version: Literal[1]
    prompt_contract_sha256: Hash
    result_schema_contract_version: Literal[1]
    result_schema_contract_sha256: Hash
    selection_policy_version: Literal[1]
    selection_policy_sha256: Hash
    fallback_policy_version: Literal[1]
    fallback_policy_sha256: Hash
    provider_profile_snapshot_sha256: Hash | None
    provider_revision_sha256: Hash | None
    effective_settings_sha256: Hash
    budget_allocation_sha256: Hash
    selected_request_count: int
    outcome_counts: CityCognitionOutcomeCountsV1
    terminal_stream_sha256: Hash
    terminal_stream_bytes: int
    terminal_record_count: int
    summary_sha256: Hash

class CityCognitionStudyReceiptV1(CityCognitionManifestReceiptV1):
    # Exact bounded noncausal V9 provenance; no prose or credential.
    pass

class WorkbenchAcceptedSettings(DomainModel):
    workbench_input_schema_version: Literal[1, 2, 3]
    expected_manifest_schema_version: Literal[7, 8, 9]
    cognition_mode: Literal["rules", "mock", "local", "remote"] | None
    provider_profile_id: str | None
    provider_revision_sha256: Hash | None
    aggregate_output_token_ceiling: int | None
```

Every V9 run contains:

```text
inputs/cognition.json           <= 65,536 bytes
outputs/cognition.jsonl         <= 1,048,576 bytes, <= 60 records, <= 8,192 each
outputs/cognition-summary.json  <= 16,384 bytes
```

- [ ] **Step 1: Write failing V9 contract/storage/job tests** binding schema-3 input, provider snapshot, prompt/result/selection/fallback policy identities and hashes, budgets, outcome counts, stream hash/bytes/count, and summary hash. Require all three files even for an empty stream and reject them on V1-V8. Require the legal input/manifest pairs 1/7, 2/8, and 3/9; resolve profile, endpoint policy, and an operation-local credential before run-ID reservation; verify the non-secret cognition settings at job completion.
- [ ] **Step 2: Write failing independent verification tests** that regenerate numeric evidence and selected requests, cross-bind response/update records, require canonical order and one terminal record each, validate source/result/attempt/reason/profile combinations, and regenerate deterministic rules/mock/fallback/skipped text.
- [ ] **Step 3: Write failing offline replay tests** proving no profile registry, credential, keyring, environment, cache, catalog, or network access; replace/remove the current prompt text, result schema, selection policy, and fallback defaults before replay; hash every source artifact before and after. Cover missing/duplicate/reordered/rehashed/cross-run records.
- [ ] **Step 4: Write failing consumer tests** for explicit V9 metrics/study/report/CLI support while keeping cognition text/configuration out of A/A and numeric-treatment equality. Add a bounded `CityCognitionStudyReceiptV1` containing every non-secret hash/count/policy/profile value needed to reconstruct the complete V9 source manifest; verify it as provenance while comparing only scientific inputs. Prove identical science across rules, mock, provider, cache, fallback, and budget-skipped runs.
- [ ] **Step 5: Pin every post-persona version family exactly:** manifest/summary/web unions use `Literal[1, 2, 3, 4, 5, 6, 7, 8, 9]`; attention provenance `Literal[5, 6, 7, 8, 9]`; response provenance `Literal[6, 7, 8, 9]`; study source versions `Literal[5, 6, 7, 8, 9]`; workbench receipt versions `Literal[1, 2, 3]`. Replace future-accepting checks such as `schema_version >= 5` with explicit sets and test refusal of 10/bool/float.
- [ ] **Step 6: Run focused tests and confirm V9 is unsupported.**

  Run: `uv run pytest tests/unit/city/test_city_run_contract.py tests/unit/city/test_city_analysis.py tests/unit/city/test_city_web.py tests/unit/city/test_spatial_study_contract.py tests/unit/experiments/test_spatial_metrics.py tests/unit/experiments/test_spatial_response_metrics.py tests/unit/experiments/test_spatial_observations.py tests/unit/experiments/test_spatial_study_result.py tests/unit/experiments/test_spatial_comparison.py tests/unit/experiments/test_spatial_response_comparison.py tests/integration/test_city_cognition_artifacts.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_spatial_study.py tests/cli/test_city_replay.py tests/cli/test_city_view.py tests/cli/test_city_metrics.py tests/cli/test_city_compare.py tests/browser/test_spatial_report_browser.py tests/security/test_spatial_report_security.py -q`

- [ ] **Step 7: Implement V9 finalization, manifest-last persistence, independent load verification, replay, complete study receipt/projection, and every listed consumer dispatch.** Integrate schema-3 jobs only here with phases `queued`, `simulating`, `selecting-explanations`, `requesting-explanations`, `persisting`, `verifying`, and terminal states. Use the Task 5 cancellation bridge; clear the captured credential after provider close on every outcome; never expose it through `_JobRecord`.
- [ ] **Step 8: Re-run under two hash seeds, then commit.**

  Commit: `feat(city): freeze and replay cognition evidence`

### Task 7: Provider and Cognition APIs

**Files:**
- Create: `src/adlife/city/workbench_providers.py`
- Modify: `src/adlife/city/workbench_security.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Create: `tests/unit/city/test_workbench_providers.py`
- Modify: `tests/unit/city/test_workbench_security.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Routes:**

```text
GET  /api/providers
POST /api/providers
POST /api/providers/{profile_id}/revisions/{revision_id}/credential
POST /api/providers/{profile_id}/revisions/{revision_id}/check
GET  /api/runs/{run_id}/cognition-summary
GET  /api/runs/{run_id}/cognition-records
```

- Preserve V7/V8 workbench-input DTOs. Add `WorkbenchRunInputViewV3`, an exact `WorkbenchRunInputViewDocument` union, and V9-only dispatch. Task 6 already extends accepted settings and verified run summaries with non-secret cognition mode/profile revision/budget identity. Workbench-input and cognition endpoints resolve the existing verified inspector snapshot token rather than rereading artifacts.

- [ ] **Step 1: Write failing API tests** for profile list/create, revision immutability, credential availability/store, compatibility check, V9 evidence projection, exact V7/V8/V9 input DTO dispatch, accepted-setting cross-checks, shared verified inspector snapshots, and path-free/value-free DTOs.
- [ ] **Step 2: Write failing credential parser tests** requiring exactly `{"credential": "<value>"}`, an 8,192-byte route limit, constant diagnostics, and no Pydantic/JSON echo. Ordinary requests remain 131,072 bytes; city upload remains 4 MiB.
- [ ] **Step 3: Write failing compatibility-check tests** for one in flight, refusal during simulation, five starts per rolling minute through an injected monotonic clock, one attempt, ten-second deadline, no retry/repair, 384-token and 1 MiB limits, and status/reason-code-only responses. One shared locked state machine atomically arbitrates check/check, check/job, job/check, shutdown, cancellation, and credential-replacement races so two operations cannot both observe idle and start.
- [ ] **Step 4: Run focused tests and confirm endpoints are absent.**

  Run: `uv run pytest tests/unit/city/test_workbench_providers.py tests/unit/city/test_workbench_security.py tests/unit/city/test_workbench_web.py tests/unit/city/test_workbench_jobs.py tests/security/test_workbench_browser_security.py -q`

- [ ] **Step 5: Implement the controller/routes and truthful capability response:** `provider_configuration: true`, `oauth: false`, cognition modes rules/mock/local/remote, and response modes still only deterministic-rules.
- [ ] **Step 6: Re-run tests and commit.**

  Commit: `feat(workbench): expose secure provider controls`

### Task 8: Provider Panel and Saved-Run Explanations

**Files:**
- Create: `src/adlife/city/static/workbench-providers.js`
- Create: `src/adlife/city/static/workbench-providers.css`
- Modify: `src/adlife/city/static/workbench.html`
- Modify: `src/adlife/city/static/workbench.js`
- Modify: `src/adlife/city/static/workbench.css`
- Modify: `src/adlife/city/static/index.html`
- Modify: `src/adlife/city/static/app.js`
- Modify: `src/adlife/city/static/app.css`
- Modify: `src/adlife/city/web.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/browser/test_city_workbench_browser.py`
- Modify: `tests/browser/test_city_places_browser.py`
- Modify: `tests/security/test_workbench_browser_security.py`
- Modify: `tests/packaging/test_wheel.py`
- Modify: `scripts/smoke_release.py`

- [ ] **Step 1: Invoke the frontend-design skill** and define one concise `Explanations` section, plain labels, progressive disclosure, no generic AI decoration, and map/timeline primacy.
- [ ] **Step 2: Write failing workbench tests** for rules/mock/local/remote choices, profile creation, keyring submission, credential availability without value, compatibility check/cost warning, funded/skipped ceiling, causal-boundary copy, keyboard/focus recovery, stale responses, reduced motion, and 390-pixel containment.
- [ ] **Step 3: Write failing secret/browser tests** allowing the secret only transiently in the password input's live `value` property and the dedicated request body. It never appears in attributes/text nodes, globals, web/local/session/IndexedDB storage, logs, URLs, retained request objects, later DTOs, or any other DOM location; clear it in `finally` after success, refusal, network failure, or cancellation and render server text inertly.
- [ ] **Step 4: Write failing inspector tests** for V9-only summary fetching, explanation adjacent to the computed response, provider/model/source/fallback/disclosure display, read-only timeline, hostile/Persian/bidi text, and artifact immutability. Label only provider/cache results `AI-generated explanation`; label rules/mock/fallback/budget-skipped records truthfully as deterministic, mock, fallback, or skipped explanations.
- [ ] **Step 5: Run browser/security/package tests and confirm failure.**

  Run: `uv run pytest tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/security/test_workbench_browser_security.py tests/packaging/test_wheel.py -q`

- [ ] **Step 6: Implement the panel and inspector projection.** Do not show an OAuth button; state only that product login/subscription tokens are not accepted by this compatible-endpoint configuration.
- [ ] **Step 7: Re-run headless tests and commit.**

  Commit: `feat(workbench): add optional AI explanations`

### Task 9: Documentation and Release Certification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/city-pilot.md`
- Modify: `docs/cli-reference.md`
- Modify: `docs/reproducibility.md`
- Modify: `docs/methodology/model-card.md`
- Modify: `docs/methodology/limitations.md`
- Modify: `docs/methodology/experiment-protocol.md`
- Modify: `docs/superpowers/plans/2026-09-28-production-city-platform-roadmap.md`
- Modify: `tests/packaging/test_documentation.py`
- Modify: `tests/packaging/test_smoke_release.py`
- Modify: `tests/packaging/test_release_workflow.py`
- Modify: `packaging/adlife.spec`

- [ ] **Step 1: Add failing documentation assertions** for the compatible Chat Completions subset, startup origin/env bindings, keyring path, noncausal AI boundary, live-call nondeterminism versus frozen replay, offline defaults, `oauth: false`, and refusal to reuse product-login/subscription tokens.
- [ ] **Step 2: Extend installed-wheel and frozen-build smoke** with rules, mock, and a local fake OpenAI-compatible endpoint under the network guard. Never require a public provider, real credential, or real OS keyring. Add the keyring dynamic-discovery resources/hooks required by `packaging/adlife.spec` and prove the release workflow includes them.
- [ ] **Step 3: Update public documentation and roadmap evidence** without predictive, sentient-agent, real-person, or OAuth-support claims.
- [ ] **Step 4: Run focused provider, artifact, security, browser, docs, lint, and type gates.**

  Run: `uv run pytest tests/unit/city/test_city_cognition_contract.py tests/unit/city/test_city_cognition.py tests/unit/cognition/test_city_endpoint_policy.py tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_service.py tests/unit/cognition/test_city_cache.py tests/unit/city/test_provider_profiles.py tests/unit/city/test_workbench_providers.py tests/integration/test_city_cognition_artifacts.py tests/integration/test_city_run_replay.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py tests/security/test_workbench_browser_security.py tests/browser/test_city_workbench_browser.py tests/browser/test_city_places_browser.py tests/packaging/test_documentation.py tests/packaging/test_wheel.py tests/packaging/test_smoke_release.py tests/packaging/test_release_workflow.py -q`

  Run: `uv sync --locked --all-groups`

  Run: `uv run ruff format --check .`

  Run: `uv run ruff check .`

  Run: `uv run mypy src`

- [ ] **Step 5: Run repository deterministic, coverage, package, offline, and exact-wheel gates.**

  Run: `uv run pytest -q`

  Run: `$env:PYTHONHASHSEED='0'; uv run pytest -q; Remove-Item Env:PYTHONHASHSEED`

  Run: `$env:PYTHONHASHSEED='12345'; uv run pytest -q; Remove-Item Env:PYTHONHASHSEED`

  Run: `uv run pytest --cov=adlife --cov-branch --cov-report=term-missing`

  Run: `uv build --no-sources`

  Run: `uv run python scripts/smoke_release.py dist/adlife_sim-0.1.0-py3-none-any.whl`

  Run: `uv run adlife doctor --offline`

  Run: `uv run adlife --format json demo --headless`

  Run: `git diff --check`

- [ ] **Step 6: Commit and push the certified slice.**

  Commit: `docs(cognition): document city provider boundaries`
