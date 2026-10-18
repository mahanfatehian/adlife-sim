# Bounded City Cognition V10 Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the bounded cognition V2 core contracts to the hardened provider runtime, schema-4 workbench input, V10 atomic artifacts, process-local job orchestration, and fully offline replay without changing any V1-V9 behavior.

**Architecture:** This plan depends on the completed core V2 plan and keeps every external effect outside `src/adlife/core`. It first extracts reusable V1-preserving HTTP and cache primitives, then adds the isolated V2 provider/cache/service path, admits schema-4 documents while explicitly refusing their execution, authorizes them through an exact process-local compatibility registry, builds V10 evidence in memory, adds manifest-selected persistence, and only then enables jobs and replay.

**Tech Stack:** Python 3.11-3.13, Pydantic v2, FastAPI/Starlette, httpcore, asyncio, immutable workspace registries, canonical JSON/JSONL, uv, pytest, Hypothesis, Ruff, and mypy strict mode.

**Spec:** `docs/superpowers/specs/2026-10-18-bounded-city-cognition-design.md`

**Depends on:** `docs/superpowers/plans/2026-10-18-bounded-city-cognition-core-implementation.md`

## Global Constraints

- Complete and commit the core implementation plan before starting this plan; do not duplicate or rename its public V2 types.
- Work only on `main`; do not modify the preserved `defense-ready` branch.
- Use `uv` for every Python, test, lint, type-check, and build command.
- Follow red-green-refactor for every behavior change; a red run must fail at the named assertion or missing symbol, not from a fixture, import-path, or environment mistake.
- Before the first production-code step in each task, also run that task's later exact focused-green-suite command against the red tree and account for every newly added behavior test: each must collect and fail for its intended missing behavior, while pre-existing regression nodes remain green. The representative red-node command is a fast diagnostic, not a substitute for this full red inventory. For a not-yet-created module or symbol, use `importlib.import_module`/`getattr` inside the named test so pytest collects and runs the node; a collection error is not acceptable red evidence.
- Keep `src/adlife/core` adapter-free. This plan may modify only the core port `src/adlife/core/ports/city_cognition.py`; it must not import FastAPI, SQLite, HTTP clients, credentials, cache adapters, or provider-specific code.
- Preserve all V1 provider constructors, public exception identities, prompt bytes, cache namespace/keys, retry behavior, workbench schemas 1-3, manifests V1-V9, and stored bytes.
- Keep schema-4 job submission disabled until Task 9, after exact V2 compatibility and V10 save/load are both green.
- Provider results may affect only the V2 cognition sentiment/recall track through the frozen core influence policy; movement, opportunity, attention, baseline responses, rule state, purchase intention, events, budgets, and identities remain rule-owned.
- V2 prompts omit display name, age, occupation, biography/background, MBTI, internal hashes, purchase values, secrets, credentials, paths, and previous provider results.
- Never persist raw provider bodies, raw prompts, credentials, authorization headers, URL userinfo, provider exception bodies, or chain-of-thought/reasoning.
- Provider limits are exact: four concurrent requests, 8,192 decoded request bytes, 1,048,576 wire-response bytes, 128 output tokens per attempt, 512 reserved output tokens per funded request, at most two transient retries, at most one repair, 30 seconds per attempt, and 180 seconds per batch.
- Rules/mock set `funded_response_limit=60`, reserve zero provider tokens, require no provider/cache/credential/network, and resolve every selected request locally.
- Replay reads only frozen artifacts and must not consult a provider, network, cache, environment variable, keyring, provider registry, current prompt constant, or current influence-policy constant.
- Preserve canonical JSON, finite numbers, exact integer validation, deterministic ordering, path containment, symlink/reparse refusal, no-clobber publication, and `run.json`-last completion.
- Use only loopback fake providers in automated tests; default tests and demos must remain offline.
- Run existing V1 regression suites after every extraction or shared-runtime change.
- Stage only the exact paths named by each task. Inspect `git diff --cached --name-only` before every commit. Never use broad `git add tests`, `git add src/adlife/core`, or wildcard staging.
- Run `git add`, `git commit`, and `git push` as separate checked commands. Never use PowerShell semicolon chains that can push after a failed commit.
- Push only to the already configured `origin/main`; do not add or modify a remote or change global Git configuration.
- Keep the existing port-50050 preview running; this runtime plan does not replace the preview process.

## Review Focus

- A V1 service catching an exception re-exported after transport extraction must see the identical class object, not a lookalike class that escapes its retry/fallback boundary; Task 1 pins identity and V1 retry regressions.
- A present but malformed, oversized, redirected, cross-namespace, or cross-bound V2 cache entry must hard-fail rather than become a miss or neutral fallback; Tasks 2 and 4 exercise each case.
- A schema-4 provider run without the exact successful bounded-signal check must fail before destination inspection, credential use, or job creation; Tasks 5, 6, and 9 pin the disabled and enabled boundaries.
- A write, flush, verification, rename, cancellation, or process error before manifest publication must leave no loadable completed V10 run and must never overwrite a prior path; Task 8 injects failures at each publication stage.
- V10 replay with provider/cache/keyring/environment/current-policy access replaced by raising sentinels must still reproduce exact response, cognition, summary, and receipt evidence without changing one source byte; Task 10 proves this end to end.

---

## Runtime File Responsibility Map

- `src/adlife/adapters/cognition/city_provider_transport.py` owns one credential-bound, endpoint-pinned HTTP/1.1 JSON POST primitive and the single public provider error hierarchy used by V1 and V2.
- `src/adlife/adapters/cognition/city_cache_storage.py` owns namespace-pinned bounded byte reads and atomic no-clobber publication; V1 and V2 cache modules own their schemas and keys.
- `src/adlife/adapters/cognition/city_prompts_v2.py` owns the minimized V2 wire projection, projection hash, strict response format, normal payload, and bounded repair payload.
- `src/adlife/adapters/cognition/city_openai_compatible_v2.py` owns V2 OpenAI-compatible envelope parsing and maps untrusted bodies to typed V2 attempts.
- `src/adlife/adapters/cognition/city_cache_v2.py` owns the isolated `city-cognition-cache-v2` record and key contract.
- `src/adlife/adapters/cognition/city_service_v2.py` owns bounded async V2 resolution, retries, repair, deterministic fallback, and post-resolution cache publication.
- `src/adlife/city/provider_compatibility.py` owns process-local, effect-scope-specific compatibility keys and results; it persists no status or secret.
- `src/adlife/city/workbench_input.py`, `workbench_validation.py`, `workbench_transport.py`, and `workbench_binding.py` own schema-4 parse, normalization, views, and exact scientific binding.
- `src/adlife/city/runs.py` owns side-effect-free V2 preparation/finalization and offline V10 replay; it never contacts a provider or writes a path.
- `src/adlife/city/run_store.py` owns manifest-selected V10 save/load/verification and `run.json`-last publication.
- `src/adlife/city/workbench_runs.py` and `workbench_jobs.py` own operation-scoped secrets, compatibility admission, service execution, job phases, publication, and verification.
- `src/adlife/cli/commands/city_replay.py` exposes verified V10 replay facts without provider text or secret-bearing data.

### Task 1: Extract one hardened provider transport while preserving V1 identity

**Files:**
- Create: `src/adlife/adapters/cognition/city_provider_transport.py`
- Create: `tests/unit/cognition/test_city_provider_transport.py`
- Modify: `src/adlife/adapters/cognition/city_openai_compatible.py`
- Test: `tests/unit/cognition/test_city_openai_compatible.py`
- Test: `tests/unit/cognition/test_city_service.py`
- Test: `tests/unit/cognition/test_city_endpoint_policy.py`
- Test: `tests/unit/cognition/test_city_credentials.py`
- Test: `tests/security/test_city_provider_security.py`

**Interfaces:**
- Consumes: `CityEndpointPolicy`, `BoundProviderEndpoint`, `ResolvedCredential`, `SecretBuffer`, and `httpcore.AsyncNetworkBackend` from the existing V9 runtime.
- Produces:
  - `class CityProviderHttpTransport`
  - `CityProviderHttpTransport.__init__(*, endpoint_policy: CityEndpointPolicy, endpoint: BoundProviderEndpoint, credential: ResolvedCredential, attempt_timeout_seconds: int, max_wire_response_bytes: int, network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None) -> None`
  - `CityProviderHttpTransport.post_json(payload: Mapping[str, object]) -> Awaitable[bytes]`
  - `CityProviderHttpTransport.contains_bound_credential(value: str | bytes) -> bool`
  - `CityProviderHttpTransport.aclose() -> Awaitable[None]`
  - `CityProviderHttpTransport.is_closed: bool`
  - one definition each of `CityProviderCallError`, `CityProviderAuthenticationError`, `CityProviderTimeoutError`, `CityProviderRateLimitError`, `CityProviderServerError`, `CityProviderUnavailableError`, `CityProviderResponseTooLargeError`, `CityProviderProtocolError`, and `CityProviderTransportError`.
- Preserves: `city_openai_compatible.py` re-exports those exact class objects and keeps `CityOpenAICompatibleProvider`'s constructor, methods, repr, close behavior, payloads, and exception behavior unchanged.

- [ ] **Step 1: Write the shared-transport contract test**

  Add `test_transport_pins_endpoint_and_bounds_one_http11_identity_response` and assert that the fake backend observes the resolved numeric address plus original Host/SNI, `Accept-Encoding: identity`, HTTP/1.1 only, no redirect/proxy reuse, and a body returned only within the supplied byte limit.

- [ ] **Step 2: Write the V1 public-identity regression test**

  Assert every error imported from `city_openai_compatible` is the same object exported from `city_provider_transport`, and assert a V1 `CityCognitionService` still classifies each error into its existing exact retry/fallback reason.

- [ ] **Step 3: Write the unsafe transport cases**

  Cover endpoint re-resolution/peer drift, HTTP/2, redirects, compressed bodies, duplicate/invalid length headers, oversized declared and streamed bodies, timeouts, cancellation, credential echo detection, closed transport, and an exception whose text contains a fake authorization header; public exceptions must have constant value-free messages.

- [ ] **Step 4: Run the focused red node**

  Run: `uv run pytest tests/unit/cognition/test_city_provider_transport.py::test_transport_pins_endpoint_and_bounds_one_http11_identity_response -q`

  Expected: the named node collects and FAILS inside the test because `adlife.adapters.cognition.city_provider_transport` is not implemented; collection itself must succeed.

- [ ] **Step 5: Implement the shared transport and error hierarchy**

  Put endpoint resolution, peer verification, HTTP status classification, HTTP/1.1 enforcement, identity encoding, bounded streaming, timeout, sanitized error conversion, credential ownership, and credential-presence screening behind the exact interfaces above. `post_json` returns decoded response bytes but does not parse an OpenAI envelope.

- [ ] **Step 6: Refactor the V1 provider onto `CityProviderHttpTransport`**

  Keep V1 input/profile validation and OpenAI envelope/result parsing in `city_openai_compatible.py`. Re-export the shared errors, and make V1 call/repair bytes and exception types observationally identical.

- [ ] **Step 7: Run the new transport tests**

  Run: `uv run pytest tests/unit/cognition/test_city_provider_transport.py -q`

  Expected: PASS.

- [ ] **Step 8: Run the V1 transport, service, endpoint, credential, and security regressions**

  Run: `uv run pytest tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_service.py tests/unit/cognition/test_city_endpoint_policy.py tests/unit/cognition/test_city_credentials.py tests/security/test_city_provider_security.py -q`

  Expected: PASS with all pre-existing V1 assertions unchanged.

- [ ] **Step 9a: Run formatting for the extraction**

  Run: `uv run ruff format --check src/adlife/adapters/cognition/city_provider_transport.py src/adlife/adapters/cognition/city_openai_compatible.py tests/unit/cognition/test_city_provider_transport.py`

  Expected: exit 0.

- [ ] **Step 9b: Run lint for the extraction**

  Run: `uv run ruff check src/adlife/adapters/cognition/city_provider_transport.py src/adlife/adapters/cognition/city_openai_compatible.py tests/unit/cognition/test_city_provider_transport.py`

  Expected: exit 0.

- [ ] **Step 9c: Run strict typing for the extraction**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 10: Inspect the unstaged change**

  Run: `git diff -- src/adlife/adapters/cognition/city_provider_transport.py src/adlife/adapters/cognition/city_openai_compatible.py tests/unit/cognition/test_city_provider_transport.py tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_service.py tests/security/test_city_provider_security.py`

- [ ] **Step 11: Stage only Task 1 files**

  Run: `git add -- src/adlife/adapters/cognition/city_provider_transport.py src/adlife/adapters/cognition/city_openai_compatible.py tests/unit/cognition/test_city_provider_transport.py tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_service.py tests/security/test_city_provider_security.py`

- [ ] **Step 12: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the six Task 1 paths listed in Step 11.

- [ ] **Step 13: Commit the transport extraction**

  Run: `git commit -m "refactor(cognition): share hardened provider transport"`

- [ ] **Step 14: Push the checked commit**

  Run: `git push origin main`

### Task 2: Extract namespace-pinned cache byte storage while preserving V1 bytes

**Files:**
- Create: `src/adlife/adapters/cognition/city_cache_storage.py`
- Create: `tests/unit/cognition/test_city_cache_storage.py`
- Modify: `src/adlife/adapters/cognition/city_cache.py`
- Test: `tests/unit/cognition/test_city_cache.py`
- Test: `tests/unit/cognition/test_city_service.py`
- Test: `tests/unit/city/test_workbench_workspace.py`

**Interfaces:**
- Consumes: `WorkbenchWorkspace`, `WorkbenchSubdirectory`, and the existing immutable-registry pin/read/publish primitives.
- Produces:
  - `CacheNamespace = Literal["city-cognition-cache", "city-cognition-cache-v2"]`
  - `class CityCognitionCacheByteStorage`
  - `CityCognitionCacheByteStorage.__init__(workspace: WorkbenchWorkspace, *, namespace: CacheNamespace, max_record_bytes: int) -> None`
  - `CityCognitionCacheByteStorage.read(key: str) -> bytes | None`
  - `CityCognitionCacheByteStorage.publish(key: str, document: bytes) -> None`
  - the single definitions of `CityCognitionCacheCorruption`, `CityCognitionCacheConflict`, `CityCognitionCacheUnavailable`, and `CityCognitionCachePublicationError` used by both cache versions.
  - `CityCognitionCachePublicationError.__init__(potentially_committed: bool) -> None`, preserving the public boolean attribute and both existing constant messages exactly.
- Preserves: `CityCognitionCache`, `CityCognitionCacheRecordV1`, `CITY_COGNITION_CACHE_KEY_VERSION`, the `city-cognition-cache` directory, V1 key derivation, canonical record bytes, and re-exported public exception identity.

- [ ] **Step 1: Write byte-storage path and publication tests**

  Add tests proving only a 64-lowercase-hex key maps to `<key>.json`, namespaces cannot escape the pinned workspace, symlinks/reparse points/hard-link count changes are refused, reads are bounded, publication is fsynced/no-clobber, ambiguous post-link failure preserves `potentially_committed=True`, and abandoned stage handles are released.

- [ ] **Step 2: Write V1 byte-compatibility and exception-identity tests**

  Serialize a V1 cache fixture before delegation, assert the delegated V1 cache writes identical bytes under the same path/key, and assert V1 exception exports are the identical shared class objects.

- [ ] **Step 3: Run the focused red node**

  Run: `uv run pytest tests/unit/cognition/test_city_cache_storage.py::test_cache_byte_storage_refuses_namespace_and_link_escape -q`

  Expected: the named node collects and FAILS inside the test because `adlife.adapters.cognition.city_cache_storage` is not implemented; collection itself must succeed.

- [ ] **Step 4: Implement `CityCognitionCacheByteStorage`**

  Move only validated-key filename mapping, namespace directory pinning, bounded byte reads, stage-handle verification, no-clobber publication, directory sync, and shared errors. The byte store does not parse JSON or know V1/V2 models.

- [ ] **Step 5: Delegate V1 cache I/O without changing its schema or key**

  Keep V1 validation and canonical serialization in `city_cache.py`; use the byte store with namespace `city-cognition-cache` and `MAX_CITY_COGNITION_CACHE_RECORD_BYTES`.

- [ ] **Step 6: Run the new cache-storage tests**

  Run: `uv run pytest tests/unit/cognition/test_city_cache_storage.py -q`

  Expected: PASS.

- [ ] **Step 7: Run V1 cache/service/workspace regressions**

  Run: `uv run pytest tests/unit/cognition/test_city_cache.py tests/unit/cognition/test_city_service.py tests/unit/city/test_workbench_workspace.py -q`

  Expected: PASS with unchanged V1 key and record-byte assertions.

- [ ] **Step 8a: Run formatting**

  Run: `uv run ruff format --check src/adlife/adapters/cognition/city_cache_storage.py src/adlife/adapters/cognition/city_cache.py tests/unit/cognition/test_city_cache_storage.py`

  Expected: exit 0.

- [ ] **Step 8b: Run lint**

  Run: `uv run ruff check src/adlife/adapters/cognition/city_cache_storage.py src/adlife/adapters/cognition/city_cache.py tests/unit/cognition/test_city_cache_storage.py`

  Expected: exit 0.

- [ ] **Step 8c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 9: Inspect the unstaged change**

  Run: `git diff -- src/adlife/adapters/cognition/city_cache_storage.py src/adlife/adapters/cognition/city_cache.py tests/unit/cognition/test_city_cache_storage.py tests/unit/cognition/test_city_cache.py`

- [ ] **Step 10: Stage only Task 2 files**

  Run: `git add -- src/adlife/adapters/cognition/city_cache_storage.py src/adlife/adapters/cognition/city_cache.py tests/unit/cognition/test_city_cache_storage.py tests/unit/cognition/test_city_cache.py`

- [ ] **Step 11: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the four Task 2 paths listed in Step 10.

- [ ] **Step 12: Commit the cache extraction**

  Run: `git commit -m "refactor(cognition): share pinned cache storage"`

- [ ] **Step 13: Push the checked commit**

  Run: `git push origin main`

### Task 3: Add the minimized V2 prompt and strict OpenAI-compatible provider

**Files:**
- Create: `src/adlife/adapters/cognition/city_prompts_v2.py`
- Create: `src/adlife/adapters/cognition/city_openai_compatible_v2.py`
- Create: `tests/unit/cognition/test_city_prompts_v2.py`
- Create: `tests/unit/cognition/test_city_openai_compatible_v2.py`
- Modify: `src/adlife/core/ports/city_cognition.py`
- Modify: `tests/security/test_city_provider_security.py`
- Modify: `tests/security/test_redaction_corpus.py`
- Test: `tests/unit/cognition/test_city_prompts.py`
- Test: `tests/unit/cognition/test_city_openai_compatible.py`
- Test: `tests/unit/cognition/test_city_endpoint_policy.py`
- Test: `tests/unit/cognition/test_city_credentials.py`
- Test: `tests/architecture/test_core_import_boundary.py`

**Interfaces:**
- Consumes from the core plan: `CityCognitionRequestV2`, `CityCognitionResultV2`, `CityCognitionInputV2`, `CityProviderAttemptV2`, and `CityProviderRepairContextV2`.
- Produces in the core port:
  - `@runtime_checkable class CityCognitionProviderV2(Protocol)`
  - `CityCognitionProviderV2.call(request: CityCognitionRequestV2) -> Awaitable[CityProviderAttemptV2]`
  - `CityCognitionProviderV2.repair_call(request: CityCognitionRequestV2, *, context: CityProviderRepairContextV2) -> Awaitable[CityProviderAttemptV2]`
- Produces in `city_prompts_v2.py`:
  - `CITY_COGNITION_JSON_SCHEMA_NAME_V2: Final = "city-cognition-signal-result-v2"`; any exposed prompt-contract label is exactly `city-cognition-signal-prompt-v2`.
  - `build_city_cognition_provider_projection_v2(request: CityCognitionRequestV2) -> FrozenJsonMapping`
  - `city_cognition_provider_projection_sha256_v2(request: CityCognitionRequestV2) -> str`
  - `build_city_cognition_messages_v2(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2) -> tuple[dict[str, object], ...]`
  - `city_cognition_response_format_v2(cognition_input: CityCognitionInputV2) -> dict[str, object]`
  - `build_city_cognition_payload_v2(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2) -> dict[str, object]`
  - `build_city_cognition_repair_payload_v2(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2, *, context: CityProviderRepairContextV2) -> dict[str, object]`
- Produces `class CityOpenAICompatibleProviderV2` with these exact signatures and the same context-manager behavior, `is_closed`, and redacted repr as V1:
  - `CityOpenAICompatibleProviderV2.__init__(*, endpoint_policy: CityEndpointPolicy, endpoint: BoundProviderEndpoint, cognition_input: CityCognitionInputV2, credential: ResolvedCredential, network_backend_factory: Callable[[], httpcore.AsyncNetworkBackend] | None = None) -> None`
  - `CityOpenAICompatibleProviderV2.call(request: CityCognitionRequestV2) -> Awaitable[CityProviderAttemptV2]`
  - `CityOpenAICompatibleProviderV2.repair_call(request: CityCognitionRequestV2, *, context: CityProviderRepairContextV2) -> Awaitable[CityProviderAttemptV2]`
  - `CityOpenAICompatibleProviderV2.aclose() -> Awaitable[None]`.

- [ ] **Step 1: Write the exact minimized-projection snapshot test**

  Assert the canonical projection includes only request ID, synthetic disclosure, causal interests/traits, bounded initial rule sentiment/recall, campaign creative, channel/time/frequency, and deterministic rule factors. Assert serialized keys/values contain none of display name, age, occupation, biography/background, MBTI, agent/campaign/placement/event/city IDs, internal hashes, purchase intention/probability, filesystem paths, credentials, or previous signals.

- [ ] **Step 2: Write payload and repair tests**

  Assert model/profile binding, strict JSON schema, `stream=False`, no tools/reasoning, `max_tokens <= 128`, delimiter-neutralized campaign text, fixed/frozen seed behavior, and a repair prompt containing only a bounded reason code rather than raw provider text.

- [ ] **Step 3: Write adversarial provider parser tests**

  Cover duplicate keys, extra result fields, wrong/missing request ID or disclosure, every invalid enum/flag, non-finite constants, root/content arrays, refusal, tool calls, non-`stop` finish reasons, content filtering/truncation, malformed UTF-8/JSON, response/body overflow, credential echo, fake authorization text, remote HTTP/userinfo, redirects, and DNS/peer drift. Every accepted result must be `CityCognitionResultV2`; every invalid answer must be a typed invalid attempt or sanitized transport error.

- [ ] **Step 4: Extend the secret-redaction corpus**

  Add V2 nested result, repair context, provider envelope, exception, log, URL, prompt, and authorization-header carriers. Assert no corpus secret reaches repr, exception text, attempt models, logs, or canonical payload snapshots.

- [ ] **Step 5: Run the focused red node**

  Run: `uv run pytest tests/unit/cognition/test_city_prompts_v2.py::test_provider_projection_contains_only_approved_causal_fields -q`

  Expected: the named node collects and FAILS inside the test because `adlife.adapters.cognition.city_prompts_v2` is not implemented; collection itself must succeed.

- [ ] **Step 6: Add the V2 core port without unioning it with V1**

  Keep `CityCognitionProvider` unchanged and narrative-only. Export `CityCognitionProviderV2` separately so a V1 provider cannot satisfy V2 service admission through structural ambiguity.

- [ ] **Step 7: Implement the V2 projection, hash, messages, response format, and payloads**

  Hash the canonical minimized projection, not the complete internal request. Treat creative text as delimited untrusted data and reject/neutralize delimiter collision using the frozen prompt contract from the core input.

- [ ] **Step 8: Implement `CityOpenAICompatibleProviderV2`**

  Delegate all HTTP behavior to `CityProviderHttpTransport`. Parse the OpenAI-compatible outer envelope strictly, validate the inner object through `CityCognitionResultV2.model_validate_json`, require exact request ID and `finish_reason="stop"`, and never retain or expose the raw body after parsing.

- [ ] **Step 9: Run V2 prompt/provider/security tests**

  Run: `uv run pytest tests/unit/cognition/test_city_prompts_v2.py tests/unit/cognition/test_city_openai_compatible_v2.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py -q`

  Expected: PASS.

- [ ] **Step 10: Run V1 and boundary regressions**

  Run: `uv run pytest tests/unit/cognition/test_city_prompts.py tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_endpoint_policy.py tests/unit/cognition/test_city_credentials.py tests/architecture/test_core_import_boundary.py -q`

  Expected: PASS; no V1 payload or port semantics change.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/core/ports/city_cognition.py src/adlife/adapters/cognition/city_prompts_v2.py src/adlife/adapters/cognition/city_openai_compatible_v2.py tests/unit/cognition/test_city_prompts_v2.py tests/unit/cognition/test_city_openai_compatible_v2.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/core/ports/city_cognition.py src/adlife/adapters/cognition/city_prompts_v2.py src/adlife/adapters/cognition/city_openai_compatible_v2.py tests/unit/cognition/test_city_prompts_v2.py tests/unit/cognition/test_city_openai_compatible_v2.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/core/ports/city_cognition.py src/adlife/adapters/cognition/city_prompts_v2.py src/adlife/adapters/cognition/city_openai_compatible_v2.py tests/unit/cognition/test_city_prompts_v2.py tests/unit/cognition/test_city_openai_compatible_v2.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py`

- [ ] **Step 13: Stage only Task 3 files**

  Run: `git add -- src/adlife/core/ports/city_cognition.py src/adlife/adapters/cognition/city_prompts_v2.py src/adlife/adapters/cognition/city_openai_compatible_v2.py tests/unit/cognition/test_city_prompts_v2.py tests/unit/cognition/test_city_openai_compatible_v2.py tests/security/test_city_provider_security.py tests/security/test_redaction_corpus.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the seven Task 3 paths listed in Step 13.

- [ ] **Step 15: Commit the V2 provider**

  Run: `git commit -m "feat(cognition): add bounded signal provider"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

### Task 4: Add the isolated V2 cache and bounded resolution service

**Files:**
- Create: `src/adlife/adapters/cognition/city_cache_v2.py`
- Create: `src/adlife/adapters/cognition/city_service_v2.py`
- Create: `tests/unit/cognition/test_city_cache_v2.py`
- Create: `tests/unit/cognition/test_city_service_v2.py`
- Test: `tests/unit/cognition/test_city_cache.py`
- Test: `tests/unit/cognition/test_city_service.py`
- Test: `tests/security/test_redaction_corpus.py`

**Interfaces:**
- Consumes: V2 core request/input/result/attempt/evaluation models and builders, `CityCognitionProviderV2`, `CityCognitionCacheByteStorage`, and `city_cognition_provider_projection_sha256_v2`.
- Consumes the revised core terminal builder exactly as `build_city_cognition_terminal_record_v2(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2, *, selection_index: int, source: CityCognitionSourceV2, result: CityCognitionResultV2, attempt_count: int = 0, reason: CityCognitionFallbackReasonV2 | None = None, prompt_tokens: int | None = None, completion_tokens: int | None = None) -> CityCognitionTerminalRecordV2`; no caller separately supplies a provider profile or current policy default.
- Produces:
  - `CITY_COGNITION_CACHE_KEY_VERSION_V2: Literal["city-cognition-cache-v2"]`
  - `MAX_CITY_COGNITION_CACHE_RECORD_V2_BYTES = 131_072`
  - `class CityCognitionCacheRecordV2(DomainModel)` binding the key, causal request ID, minimized provider-projection hash, causal-contract hash, provider revision/snapshot hashes, effective-settings hash, exact contract/policy hashes, validated result, and bounded optional usage.
  - `class CityCognitionCacheV2`
  - `CityCognitionCacheV2.__init__(workspace: WorkbenchWorkspace) -> None`
  - `CityCognitionCacheV2.make_key(request: CityCognitionRequestV2, cognition_input: CityCognitionInputV2) -> str`
  - `CityCognitionCacheV2.get(key: str) -> CityCognitionCacheRecordV2 | None`
  - `CityCognitionCacheV2.put(key: str, record: CityCognitionCacheRecordV2) -> None`
  - `class CityCognitionServiceV2`
  - `CityCognitionServiceV2.__init__(*, cache: CityCognitionCacheV2 | None = None, sleep: Callable[[float], Awaitable[None]] | None = None) -> None`
  - `CityCognitionServiceV2.resolve_many(cognition_input: CityCognitionInputV2, requests: tuple[CityCognitionRequestV2, ...], *, baseline_evaluation: SpatialResponseEvaluation, provider: CityCognitionProviderV2 | None, cancellation: CityCognitionCancellation) -> Awaitable[CityCognitionEvaluationV2]`.
- Preserves: `CityCognitionCancellation`, retry delay constants, and every V1 cache/service public behavior.

- [ ] **Step 1: Write V2 cache isolation/key tests**

  Assert namespace `city-cognition-cache-v2` and exact key dependence on causal request ID, minimized projection hash, causal-contract hash, profile revision/snapshot hashes, effective-settings hash, and prompt/result/selection/fallback/mock/influence hashes. Assert the key excludes `run_input_sha256`, `request_record_sha256`, descriptive provenance, funding-limit position, and complete `CityCognitionInputV2` identity. Two run-bound requests with the same causal identity/projection and compatible provider contracts must have the same V2 key; every V2 key differs from every V1 key. A V1 record in either namespace and a V2 record in the V1 namespace must never be a hit.

- [ ] **Step 2: Write V2 cache corruption/publication tests**

  Cover noncanonical JSON, duplicate keys, oversized bytes, wrong projection hash, request/result mismatch, changed profile/settings/policy, symlink/reparse, no-clobber conflict, and ambiguous publication. Any present invalid record raises `CityCognitionCacheCorruption`; only absence returns `None`.

- [ ] **Step 3: Write V2 service order, budget, and fallback tests**

  Assert reservations are honored before cache lookup, only funded requests touch cache/provider, concurrency never exceeds four, reverse completion returns canonical selection order, cache hits do not reallocate budget, each approved provider failure maps to one exact neutral fallback, rules/mock never touch provider/cache/network, and budget-skipped remains distinct. Reuse one valid causal cache result from a request with different full run provenance, then assert the service creates a fresh terminal record bound to the current `request_record_sha256`, run input, baseline evaluation, and selection index.

- [ ] **Step 4: Write cancellation, repair, and cache-publication tests**

  Assert at most one initial plus two transient retries and one schema repair, per-attempt/batch deadlines, invalid request ID fallback, cancellation raises rather than returns fallback, defects/cache corruption propagate, provider closes and credentials clear, and cache writes begin only after a complete validated evaluation exists.

- [ ] **Step 5: Run the focused red nodes**

  Run: `uv run pytest tests/unit/cognition/test_city_cache_v2.py::test_v2_cache_key_isolated_and_binds_minimized_projection tests/unit/cognition/test_city_service_v2.py::test_service_returns_canonical_order_after_reverse_completion -q`

  Expected: both named nodes collect and FAIL inside their tests because `city_cache_v2` and `city_service_v2` are not implemented; collection itself must succeed.

- [ ] **Step 6: Implement `CityCognitionCacheRecordV2` and `CityCognitionCacheV2`**

  Use `CityCognitionCacheByteStorage(namespace="city-cognition-cache-v2", max_record_bytes=131_072)`. Revalidate all contracts and canonical bytes before reads/writes. Store reusable causal/provider evidence only: never store the complete internal request, `run_input_sha256`, `request_record_sha256`, descriptive persona provenance, terminal record, credentials, prompt strings, raw response bodies, URLs with secrets, or exceptions.

- [ ] **Step 7: Implement `CityCognitionServiceV2.resolve_many`**

  Mirror only V1's proven cancellation/retry/concurrency mechanics while using V2 builders and validators. Revalidate the supplied `SpatialResponseEvaluation`, allocate no new reservations, pass the frozen input to every `build_city_cognition_terminal_record_v2` call, pass the baseline to `validate_city_cognition_evaluation_v2`, publish only provider successes, never cache mock/fallback/skipped, always close the provider, and return only a complete canonical `CityCognitionEvaluationV2`.

- [ ] **Step 8: Run V2 cache/service tests**

  Run: `uv run pytest tests/unit/cognition/test_city_cache_v2.py tests/unit/cognition/test_city_service_v2.py tests/security/test_redaction_corpus.py -q`

  Expected: PASS.

- [ ] **Step 9: Run V1 cache/service regressions**

  Run: `uv run pytest tests/unit/cognition/test_city_cache.py tests/unit/cognition/test_city_service.py tests/unit/cognition/test_city_cache_storage.py -q`

  Expected: PASS.

- [ ] **Step 10a: Run formatting**

  Run: `uv run ruff format --check src/adlife/adapters/cognition/city_cache_v2.py src/adlife/adapters/cognition/city_service_v2.py tests/unit/cognition/test_city_cache_v2.py tests/unit/cognition/test_city_service_v2.py`

  Expected: exit 0.

- [ ] **Step 10b: Run lint**

  Run: `uv run ruff check src/adlife/adapters/cognition/city_cache_v2.py src/adlife/adapters/cognition/city_service_v2.py tests/unit/cognition/test_city_cache_v2.py tests/unit/cognition/test_city_service_v2.py`

  Expected: exit 0.

- [ ] **Step 10c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 11: Inspect the unstaged change**

  Run: `git diff -- src/adlife/adapters/cognition/city_cache_v2.py src/adlife/adapters/cognition/city_service_v2.py tests/unit/cognition/test_city_cache_v2.py tests/unit/cognition/test_city_service_v2.py`

- [ ] **Step 12: Stage only Task 4 files**

  Run: `git add -- src/adlife/adapters/cognition/city_cache_v2.py src/adlife/adapters/cognition/city_service_v2.py tests/unit/cognition/test_city_cache_v2.py tests/unit/cognition/test_city_service_v2.py`

- [ ] **Step 13: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the four Task 4 paths listed in Step 12.

- [ ] **Step 14: Commit the V2 cache/service**

  Run: `git commit -m "feat(cognition): resolve and cache bounded signals"`

- [ ] **Step 15: Push the checked commit**

  Run: `git push origin main`

### Task 5: Admit strict schema-4 documents while keeping execution disabled

**Files:**
- Modify: `src/adlife/city/workbench_input.py`
- Modify: `src/adlife/city/workbench_validation.py`
- Modify: `src/adlife/city/workbench_transport.py`
- Modify: `src/adlife/city/workbench_binding.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `tests/unit/city/test_workbench_input.py`
- Create: `tests/unit/city/test_workbench_transport_v4.py`
- Modify: `tests/unit/city/test_workbench_validation.py`
- Modify: `tests/unit/city/test_workbench_binding.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/property/test_city_workbench_properties.py`
- Modify: `tests/contract/test_schema_versions.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**
- Consumes from the core plan: V2 prompt/result/selection/fallback/mock/influence policy documents and hashes, `CityCognitionRunContextV2`, and response-model V2 identity.
- Produces:
  - `class WorkbenchCognitionDraftV4` with exactly `effect_scope`, `mode`, `provider_profile_id`, `provider_revision_sha256`, and `funded_response_limit`.
  - `class NormalizedWorkbenchCognitionV4` freezing all V2 contract/policy versions, documents, hashes, hard limits, `aggregate_output_token_ceiling`, response model, and rule-state noninterference statement.
  - `WorkbenchRunDraftV4`, `NormalizedWorkbenchRunDraftV4`, `WorkbenchRunInputV4`, and `WorkbenchRunInputViewV4`.
  - `build_city_cognition_run_context_v2(workbench_input: WorkbenchRunInputV4, *, pack: CityPackV2, spatial_scenario: SpatialCampaignScenario, response_input: SpatialResponseInput, baseline_evaluation: SpatialResponseEvaluation) -> CityCognitionRunContextV2`.
  - updated exact unions `WorkbenchRunDraftDocument`, `WorkbenchRunInputDocument`, and `WorkbenchRunInputViewDocument`.
- Temporary execution contract: `CityJobManager.submit(validated)` raises the existing value-free `WorkbenchJobSubmissionError` for schema 4 before operation admission, destination inspection, job allocation, or worker scheduling. Task 9 removes only this temporary refusal after compatibility and storage are ready.

- [ ] **Step 1: Write exact schema-4 draft tests**

  Assert `effect_scope="bounded-signals-v1"`; rules/mock require no profile and limit 60; local/remote require exact profile/revision and limit 10/20/40/60; provider default materialization is 20; `aggregate_output_token_ceiling=limit*512` is derived server-side; browser-supplied policy hashes, ceilings, secrets, unknown fields, booleans, floats, and numeric strings are refused.

- [ ] **Step 2: Write normalized input/transport/view tests**

  Assert the only new pair is workbench 4 to run 10, canonical bytes stay below 131,072, all V2 policy documents/hashes and limits are frozen, and V1-V3 dispatch/bytes remain unchanged. Reject unknown 5/11, `True`, `4.0`, duplicate JSON keys, non-finite constants, malformed UTF-8, and every unsupported pair.

- [ ] **Step 3: Write V2 context-binding tests**

  Assert the builder cross-binds workbench/city/scenario/response/baseline hashes and includes only causal traits/interests/creative/rule factors in its causal projections. Changing only display name, age, occupation, background, or MBTI must preserve those causal projections, the causal-contract hash, request ID, and deterministic mock result, while changing the full `run_input_sha256`, complete context/cognition-input fingerprints, and request-record hash. Changing a causal trait must also change the causal projection and request ID. This test must never require the full provenance-bearing context to remain equal after a descriptive input change.

- [ ] **Step 4: Write the disabled-submission test**

  Pass a fully valid schema-4 `ValidatedWorkbenchRun` to `CityJobManager.submit`, monkeypatch operation admission, destination inspection, credential resolution, and executor submission to fail if called, and assert one constant `WorkbenchJobSubmissionError` with no ghost job or leaked input.

- [ ] **Step 5: Run the focused red nodes**

  Run: `uv run pytest tests/unit/city/test_workbench_transport_v4.py::test_schema4_normalizes_exact_bounded_cognition_contract tests/unit/city/test_workbench_jobs.py::test_schema4_submission_is_disabled_before_any_side_effect -q`

  Expected: FAIL because schema version 4 is rejected/unknown and `WorkbenchRunInputV4` is absent.

- [ ] **Step 6a: Implement schema-4 draft, normalized, and input models**

  Add the V4 model family without widening V3 fields or validators. Derive all frozen policy fields and token ceilings in Python after validating the five-field browser draft.

- [ ] **Step 6b: Extend exact parser and view dispatch for schema 4**

  Add only exact workbench-input schema `4` and `WorkbenchRunInputViewV4` branches. Keep schema 1-3 serialized bytes and unknown-version refusals unchanged.

- [ ] **Step 7a: Implement schema-4 construction and execution-binding validation**

  Extend `construct_workbench_run` and `validate_workbench_execution_binding` by exact type/version dispatch while preserving every schema 1-3 branch.

- [ ] **Step 7b: Implement V2 causal context binding**

  Add `build_city_cognition_run_context_v2` with the exact signature above and without descriptive persona fields or adapter dependencies.

- [ ] **Step 8: Add the temporary side-effect-free schema-4 job refusal**

  Perform the type check before `WorkbenchOperationState.begin_job`, `preflight_cognition`, or `ensure_available`; keep V1-V3 submission unchanged.

- [ ] **Step 9: Run schema-4 model/transport/binding/property/security tests**

  Run: `uv run pytest tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_transport_v4.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_binding.py tests/unit/city/test_workbench_jobs.py tests/property/test_city_workbench_properties.py tests/contract/test_schema_versions.py tests/security/test_workbench_browser_security.py -q`

  Expected: PASS.

- [ ] **Step 10: Run schema 1-3 transport regressions**

  Run: `uv run pytest tests/unit/city/test_workbench_transport.py tests/unit/city/test_workbench_transport_v3.py tests/integration/test_city_workbench_artifacts.py -q`

  Expected: PASS.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/workbench_input.py src/adlife/city/workbench_validation.py src/adlife/city/workbench_transport.py src/adlife/city/workbench_binding.py src/adlife/city/workbench_jobs.py tests/unit/city/test_workbench_transport_v4.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/city/workbench_input.py src/adlife/city/workbench_validation.py src/adlife/city/workbench_transport.py src/adlife/city/workbench_binding.py src/adlife/city/workbench_jobs.py tests/unit/city/test_workbench_transport_v4.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/workbench_input.py src/adlife/city/workbench_validation.py src/adlife/city/workbench_transport.py src/adlife/city/workbench_binding.py src/adlife/city/workbench_jobs.py tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_transport_v4.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_binding.py tests/unit/city/test_workbench_jobs.py tests/property/test_city_workbench_properties.py tests/contract/test_schema_versions.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 13: Stage only Task 5 files**

  Run: `git add -- src/adlife/city/workbench_input.py src/adlife/city/workbench_validation.py src/adlife/city/workbench_transport.py src/adlife/city/workbench_binding.py src/adlife/city/workbench_jobs.py tests/unit/city/test_workbench_input.py tests/unit/city/test_workbench_transport_v4.py tests/unit/city/test_workbench_validation.py tests/unit/city/test_workbench_binding.py tests/unit/city/test_workbench_jobs.py tests/property/test_city_workbench_properties.py tests/contract/test_schema_versions.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the thirteen Task 5 paths listed in Step 13.

- [ ] **Step 15: Commit disabled schema-4 admission**

  Run: `git commit -m "feat(workbench): validate bounded cognition drafts"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

### Task 6: Add effect-scope-specific provider compatibility probes and registry

**Files:**
- Create: `src/adlife/city/provider_compatibility.py`
- Modify: `src/adlife/city/workbench_providers.py`
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/city/workbench_web.py`
- Create: `tests/unit/city/test_provider_compatibility.py`
- Modify: `tests/unit/city/test_workbench_providers.py`
- Modify: `tests/unit/city/test_workbench_runs.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/unit/city/test_provider_profiles.py`
- Modify: `tests/unit/city/test_provider_runtime.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**
- Consumes: V1/V2 provider ports/adapters, `ProviderProfileRevisionV1`, frozen effective settings, and the complete V2 causal-contract/prompt/result/selection/fallback/mock/influence hashes.
- Produces:
  - `ProviderEffectScope = Literal["explanation-only", "bounded-signals-v1"]`
  - `class WorkbenchProviderCompatibilityKey(DomainModel)` binding effect scope, profile ID/revision/snapshot hash, effective-settings hash, and scope-specific contract hashes. For V2 these are `causal_contract_sha256`, prompt-contract hash, result-schema hash, selection-policy hash, fallback-policy hash, mock-policy hash, and influence-policy hash; for V1 only the existing exact explanation contract fields are populated.
  - `class WorkbenchProviderCompatibilityRecord(DomainModel)` binding a key, status, bounded reason, and no credential data.
  - `class WorkbenchProviderCompatibilityRequired(RuntimeError)` with one constant, value-free public message.
  - `build_workbench_provider_compatibility_key(profile: ProviderProfileRevisionV1, *, effect_scope: ProviderEffectScope) -> WorkbenchProviderCompatibilityKey`; this is the only key-construction function used by checks and run preflight, derives V1 or V2 effective settings by exact scope, and never reads a credential.
  - `class WorkbenchCheckProviderFactoryV2(Protocol)` with `__call__(*, endpoint_policy: CityEndpointPolicy, endpoint: BoundProviderEndpoint, cognition_input: CityCognitionInputV2, credential: ResolvedCredential) -> CityCognitionProviderV2`.
  - `class WorkbenchProviderCompatibilityRegistry`
  - `WorkbenchProviderCompatibilityRegistry.record(record: WorkbenchProviderCompatibilityRecord) -> None`
  - `WorkbenchProviderCompatibilityRegistry.lookup(key: WorkbenchProviderCompatibilityKey) -> WorkbenchProviderCompatibilityRecord | None`
  - `WorkbenchProviderCompatibilityRegistry.require_success(key: WorkbenchProviderCompatibilityKey) -> None`, raising `WorkbenchProviderCompatibilityRequired` on absence/non-success.
  - `WorkbenchProviderCompatibilityRegistry.invalidate(profile_id: str, revision_sha256: str) -> None`
  - `class WorkbenchProviderCheckResultV2(DomainModel)` with exact effect scope, status, bounded reason, compatibility-key hash, profile/revision, and contract hashes.
  - `WorkbenchProviderController.__init__(registry: ProviderProfileRegistry, runtime_policy: ProviderRuntimePolicy, *, credentials: CityCredentialStore | None = None, operations: WorkbenchOperationState | None = None, provider_factory: WorkbenchCheckProviderFactory | None = None, provider_factory_v2: WorkbenchCheckProviderFactoryV2 | None = None, compatibility_registry: WorkbenchProviderCompatibilityRegistry | None = None) -> None`.
  - `WorkbenchProviderController.check(profile_id: str, revision_id: str, *, effect_scope: ProviderEffectScope = "explanation-only") -> Awaitable[WorkbenchProviderCheckResult | WorkbenchProviderCheckResultV2]`.
- Lifetime: one registry instance is created by `create_city_workbench_app` and injected into both `WorkbenchProviderController` and `WorkbenchRunRepository`; restart intentionally clears it.

- [ ] **Step 1: Write registry-key and lifecycle tests**

  Assert V1 and V2 keys differ for the same profile; every profile/model/effective-settings/causal-contract/prompt/result/selection/fallback/mock/influence change changes the V2 key; each redundant V2 member agrees with the frozen causal contract; no key contains a credential/hash of a credential; only exact success authorizes; restart has no record; and credential replacement invalidates both scopes for that revision.

- [ ] **Step 2: Write exact compatibility-request API tests**

  Assert `{}` invokes only the V1 narrative probe and `{"effect_scope":"bounded-signals-v1"}` invokes only V2. Reject extra/browser-supplied hash/settings/schema fields, wrong values, arrays, booleans, and query parameters without contacting a provider.

- [ ] **Step 3: Write V2 fixed-probe tests**

  Assert the probe identifier is exactly `city-cognition-compatibility-probe-v2`; the sole interest is `compatibility-probe`; all six causal traits are `0.5`; initial sentiment/recall are `0`; the one mobile-feed opportunity is at day/minute/millisecond `0`; relative price is `1`; prior notice is absent; cap is `4`; and campaign text is exactly `Return the required JSON object for this synthetic compatibility probe.` Opaque hashes are SHA-256 of UTF-8 `"city-cognition-compatibility-probe-v2\0" + field_name`; agent ID is `person-000`; and the run, campaign, and placement bindings are `compatibility-probe`. Assert the input is fictional and V2-only, uses the exact profile revision/effective settings/contracts/policies, caps output at 128, returns compatible only for a valid exact-ID `CityCognitionResultV2`, discards the probe result instead of caching/applying/persisting/exposing it, and stores only a bounded sanitized compatibility record. V1 success must not satisfy V2 lookup.

- [ ] **Step 4: Write shared-registry and credential-invalidation tests**

  Construct the app, prove controller and repository reference the same registry, store a replacement credential, and assert the prior compatibility record disappears before any new check. No secret/path/raw body may appear in list/check DTOs or errors.

- [ ] **Step 5: Run the focused red node**

  Run: `uv run pytest tests/unit/city/test_provider_compatibility.py::test_v1_success_cannot_authorize_bounded_signal_scope -q`

  Expected: the named node collects and FAILS inside the test because `adlife.city.provider_compatibility` is not implemented; collection itself must succeed.

- [ ] **Step 6: Implement compatibility key/record/registry models**

  Keep the registry process-local and give it its own private `RLock`; each lookup/record/invalidate operation is atomic. The controller's existing operation lease remains the outer serialization boundary, so code never acquires the operation-state lock while holding the registry lock. Implement the single key builder above so check and submission cannot drift. Do not serialize the registry into workspace files, run inputs, browser storage, or reports.

- [ ] **Step 7: Add the V2 compatibility probe and exact request-body dispatch**

  Preserve the current V1 `{}` behavior. Build the V2 fixed request/input through core contracts, use `CityOpenAICompatibleProviderV2`, and record the exact scoped result after provider close/credential cleanup.

- [ ] **Step 8a: Inject one process-local registry**

  Extend constructors with an optional `compatibility_registry: WorkbenchProviderCompatibilityRegistry`; `create_city_workbench_app` creates one default and passes the same instance to both owners. Preserve the legacy `_last_checks`/`WorkbenchProviderView.last_check` DTO behavior for explanation-only callers while mirroring its sanitized outcome into the scoped registry.

- [ ] **Step 8b: Invalidate compatibility after credential replacement**

  After a successful credential replacement, clear the legacy last-check entry and invalidate both compatibility scopes for the exact profile revision before returning a new view. Do not invalidate a different profile/revision or retain a credential-derived key.

- [ ] **Step 9: Run compatibility/controller/web/security tests**

  Run: `uv run pytest tests/unit/city/test_provider_compatibility.py tests/unit/city/test_workbench_providers.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_web.py tests/unit/city/test_provider_profiles.py tests/unit/city/test_provider_runtime.py tests/security/test_workbench_browser_security.py -q`

  Expected: PASS while schema-4 job submission remains disabled.

- [ ] **Step 10: Run V1 provider regression tests**

  Run: `uv run pytest tests/unit/cognition/test_city_openai_compatible.py tests/unit/cognition/test_city_service.py -q`

  Expected: PASS.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/provider_compatibility.py src/adlife/city/workbench_providers.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_web.py tests/unit/city/test_provider_compatibility.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/city/provider_compatibility.py src/adlife/city/workbench_providers.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_web.py tests/unit/city/test_provider_compatibility.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/provider_compatibility.py src/adlife/city/workbench_providers.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_web.py tests/unit/city/test_provider_compatibility.py tests/unit/city/test_workbench_providers.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_web.py tests/unit/city/test_provider_profiles.py tests/unit/city/test_provider_runtime.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 13: Stage only Task 6 files**

  Run: `git add -- src/adlife/city/provider_compatibility.py src/adlife/city/workbench_providers.py src/adlife/city/workbench_runs.py src/adlife/city/workbench_web.py tests/unit/city/test_provider_compatibility.py tests/unit/city/test_workbench_providers.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_web.py tests/unit/city/test_provider_profiles.py tests/unit/city/test_provider_runtime.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the eleven Task 6 paths listed in Step 13.

- [ ] **Step 15: Commit scoped compatibility**

  Run: `git commit -m "feat(workbench): verify bounded provider compatibility"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

### Task 7: Prepare and finalize V10 evidence entirely in memory

**Files:**
- Modify: `src/adlife/city/runs.py`
- Modify: `tests/unit/city/test_city_run_preparation.py`
- Create: `tests/integration/test_city_cognition_v2_pipeline.py`
- Test: `tests/integration/test_city_response_run.py`
- Test: `tests/property/test_spatial_response_v2_invariants.py`

**Interfaces:**
- Consumes from the core plan: `CityRunManifestV10`, `CityCognitionManifestReceiptV2`, `build_city_cognition_manifest_receipt_v2`, `CityCognitionInputV2`, `CityCognitionEvaluationV2`, `SpatialResponseEvaluationV2`, `build_city_cognition_requests_v2`, `build_city_cognition_input_v2`, `validate_city_cognition_evaluation_v2`, `apply_spatial_cognition_v2`, and V2 response serializers/summaries.
- Consumes from Task 5: `WorkbenchRunInputV4` and `build_city_cognition_run_context_v2`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class PreparedCityScientificRunV2`
  - `prepare_city_scientific_run_v2(pack: CityPackDocument, *, run_id: str, seed: int, agent_count: int, days: int, spatial_scenario: SpatialCampaignScenario, spatial_response: SpatialResponseInput, workbench_input: WorkbenchRunInputV4, places: CityPlaceSet | None = None, provider_profile: ProviderProfileRevisionV1 | None = None) -> PreparedCityScientificRunV2`
  - `finalize_city_run_v2(prepared: PreparedCityScientificRunV2, *, cognition_input: CityCognitionInputV2, cognition_evaluation: CityCognitionEvaluationV2) -> PreparedCityRun`.
- `PreparedCityScientificRunV2` fields are exactly `pack: CityPackV2`, `mobility: CityMobility`, `spatial_scenario: SpatialCampaignScenario`, `opportunity_evaluation: SpatialOpportunityEvaluation`, `attention_evaluation: SpatialAttentionEvaluation`, `response_input: SpatialResponseInput`, `baseline_evaluation: SpatialResponseEvaluation`, `workbench_input: WorkbenchRunInputV4`, `cognition_input: CityCognitionInputV2`, and `cognition_requests: tuple[CityCognitionRequestV2, ...]`.
- Extends `PreparedCityRun` fields to exact V1/V2 unions without changing construction for V1-V9.

- [ ] **Step 1: Write preparation noninterference tests**

  Assert preparation validates and freezes schema 4, computes mobility/opportunity/attention plus an unpublished V1 baseline evaluation, constructs every V2 request/reservation before provider work, does no network/cache/credential/storage call, and produces the same baseline bytes/rule states/purchase intention as `evaluate_spatial_responses`.

- [ ] **Step 2: Write mode/profile/effective-settings tests**

  Assert rules/mock accept no profile, limit 60, and one canonical `funded=True,reserved_output_tokens=0` reservation per selected request; local/remote require the exact selected immutable profile and derive effective output `min(profile.max_output_tokens, 128)` with funded limit 10/20/40/60 and 512-token funded reservations.

- [ ] **Step 3: Write finalization and corruption tests**

  Assert finalization revalidates exact input/evaluation/request bindings, calls `apply_spatial_cognition_v2`, builds all V10 response/cognition hashes/counts/receipts, and returns no path. Duplicate/orphan/reordered/cross-run/cross-agent/cross-campaign/cross-minute/cross-policy records must raise before a `PreparedCityRun` exists.

- [ ] **Step 4: Write rules/mock/provider pipeline tests**

  Resolve rules/mock through `CityCognitionServiceV2`, provider through a typed fake, then assert exact cardinality equations, rule/purchase byte identity, bounded cognition changes, and identical output under reverse async completion and input permutations.

- [ ] **Step 5: Run the focused red node**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_pipeline.py::test_v10_provider_pipeline_changes_only_cognition_state -q`

  Expected: FAIL because `prepare_city_scientific_run_v2` is not defined.

- [ ] **Step 6a: Implement `PreparedCityScientificRunV2`**

  Add the exact frozen staging fields above and keep the deterministic baseline evaluation internal until finalization.

- [ ] **Step 6b: Implement V2 profile and input freezing**

  Reuse the immutable V1 provider-profile snapshot only. Call the core `build_city_cognition_input_v2` instead of duplicating its reservation/settings/policy logic; freeze V2 documents/hashes and the initial cognition-state document.

- [ ] **Step 7: Implement `prepare_city_scientific_run_v2`**

  Follow the existing validated V9 preparation sequence, then call `build_city_cognition_run_context_v2`, `build_city_cognition_requests_v2(context, mobility, baseline_evaluation, causal_contract=...)`, and `build_city_cognition_input_v2(...)` with the frozen mode, funded limit, profile snapshot, and causal contract. The function is pure with respect to provider/cache/filesystem state.

- [ ] **Step 8: Implement `finalize_city_run_v2`**

  Revalidate terminal evidence, derive `SpatialResponseEvaluationV2`, summaries/state documents/receipts, and construct `CityRunManifestV10`. Do not publish or inspect a destination.

- [ ] **Step 9: Run V2 preparation/pipeline tests**

  Run: `uv run pytest tests/unit/city/test_city_run_preparation.py tests/integration/test_city_cognition_v2_pipeline.py tests/property/test_spatial_response_v2_invariants.py -q`

  Expected: PASS.

- [ ] **Step 10: Run V1 response/V9 cognition regressions**

  Run: `uv run pytest tests/integration/test_city_response_run.py tests/integration/test_city_cognition_artifacts.py tests/unit/city/test_city_cognition.py -q`

  Expected: PASS.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/runs.py tests/unit/city/test_city_run_preparation.py tests/integration/test_city_cognition_v2_pipeline.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/city/runs.py tests/unit/city/test_city_run_preparation.py tests/integration/test_city_cognition_v2_pipeline.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/runs.py tests/unit/city/test_city_run_preparation.py tests/integration/test_city_cognition_v2_pipeline.py`

- [ ] **Step 13: Stage only Task 7 files**

  Run: `git add -- src/adlife/city/runs.py tests/unit/city/test_city_run_preparation.py tests/integration/test_city_cognition_v2_pipeline.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the three Task 7 paths listed in Step 13.

- [ ] **Step 15: Commit pure V10 preparation**

  Run: `git commit -m "feat(city): prepare bounded cognition evidence"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

### Task 8: Persist and load V10 artifacts atomically with manifest-selected parsers

**Files:**
- Modify: `src/adlife/city/run_store.py`
- Create: `tests/integration/test_city_cognition_v2_artifacts.py`
- Modify: `tests/integration/test_city_run_store.py`
- Create: `tests/security/test_city_artifact_security.py`
- Modify: `tests/integration/test_city_workbench_artifacts.py`
- Modify: `tests/integration/test_city_workbench_registry.py`

**Interfaces:**
- Consumes: `PreparedCityRun` V2 unions, `CityRunManifestV10`, workbench V4, V2 cognition/response types, and the exact V2 serializers/limits from the core plan.
- Produces:
  - `StoredCityRun.cognition_input: CityCognitionInputV1 | CityCognitionInputV2 | None`
  - `StoredCityRun.cognition_evaluation: CityCognitionEvaluationV1 | CityCognitionEvaluationV2 | None`
  - `StoredCityRun.response_evaluation: SpatialResponseEvaluation | SpatialResponseEvaluationV2 | None`
  - V10 support in `CityRunStore.save(...) -> Path` and `CityRunStore.load(run_id: str) -> StoredCityRun` selected only after strict manifest parsing.
- V10 artifact paths are exactly the reused versioned paths in the spec; V10 requires all cognition and response files even when their streams are empty.

- [ ] **Step 1: Write exact V10 round-trip and version-dispatch tests**

  Save/load rules, mock, provider, cache, fallback, budget-skipped, and zero-selected V10 fixtures. Assert canonical bytes, every required path, exact manifest receipt, and that V9 rejects V2 contents while V10 rejects V1 contents, missing sidecars, extra sidecars, and unknown V11.

- [ ] **Step 2: Write stream/document corruption tests**

  Modify, truncate, append, reorder, duplicate, cross-bind, or replace each cognition/response input, terminal line, applied line, state, summary, and manifest field. Assert `CorruptRunArtifact` or `UnsafeRunLocation`, never fallback/repair/trial parsing.

- [ ] **Step 3: Write path and filesystem attack tests**

  Cover traversal, absolute escape, symlink/reparse, hard-link anomalies where applicable, reserved names, malformed UTF-8, duplicate JSON keys, non-finite constants, oversized documents/streams, and missing/renamed files under both input/output directories.

- [ ] **Step 4: Write publication fault-injection tests**

  Fault each input write, output write, stream write, fsync/verification, manifest stage, and final rename. Assert `run.json` is never visible before all verified bytes, no prior destination is overwritten or removed, no failed run is loadable, the exact run ID stays reserved by any incomplete directory, and `.run.json.tmp` is cleaned.

- [ ] **Step 5: Write maximum-size and exact-boundary storage tests**

  Consume the core plan's certified upper-bound counts and maximum canonical record sizes; assert request/input/terminal/cognition-stream/summary/response-stream/dual-state/response-summary bounds remain within 8,192/131,072/8,192/1,048,576/16,384/2,147,483,648/8,388,608/131,072 respectively. Stream a representative 30-agent/7-day rules V10 run through the real serializer/store without retaining the response stream in memory. For each loader/writer, use bounded generated readers to prove exactly-at-limit acceptance and limit-plus-one refusal without creating a 2 GiB fixture on disk. The runtime test must fail if its enforcement constant differs from the core-certified bound or frozen specification.

- [ ] **Step 6: Run the focused red nodes**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_artifacts.py::test_v10_round_trip_uses_only_v2_manifest_selected_parsers tests/security/test_city_artifact_security.py::test_v10_modified_terminal_record_is_corruption_not_fallback -q`

  Expected: FAIL because `CityRunStore.save` refuses `CityRunManifestV10`/V2 evidence.

- [ ] **Step 7: Add exact V10 save verification and serialization**

  Branch after strict manifest type selection. Recompute the deterministic baseline and V2 requests, validate frozen terminals, apply frozen influence, compare complete V2 evaluations/receipts, stage exact canonical documents/streams, and preserve the existing manifest-last/no-clobber contract.

- [ ] **Step 8: Add exact V10 load verification**

  Read each file with its V2 limit, parse only its manifest-selected type, rebuild all science/evidence from frozen inputs, stream-compare large JSONL output, and reject any missing/extra/wrong-version/cross-bound content.

- [ ] **Step 9: Run V10 artifact/store/security tests**

  Run: `uv run pytest tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/security/test_city_artifact_security.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py -q`

  Expected: PASS.

- [ ] **Step 10: Run V1-V9 artifact regressions**

  Run: `uv run pytest tests/integration/test_artifact_layout.py tests/integration/test_city_cognition_artifacts.py tests/integration/test_city_response_run.py -q`

  Expected: PASS with historical bytes and refusal semantics unchanged.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/run_store.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/security/test_city_artifact_security.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/city/run_store.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/security/test_city_artifact_security.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/run_store.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/security/test_city_artifact_security.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py`

- [ ] **Step 13: Stage only Task 8 files**

  Run: `git add -- src/adlife/city/run_store.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/security/test_city_artifact_security.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the six Task 8 paths listed in Step 13.

- [ ] **Step 15: Commit V10 storage**

  Run: `git commit -m "feat(storage): persist verified v10 evidence"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

### Task 9: Activate schema-4 repository and job execution only after compatibility and storage

**Files:**
- Modify: `src/adlife/city/workbench_runs.py`
- Modify: `src/adlife/city/workbench_jobs.py`
- Modify: `src/adlife/city/workbench_web.py`
- Modify: `tests/unit/city/test_workbench_runs.py`
- Modify: `tests/unit/city/test_workbench_jobs.py`
- Modify: `tests/unit/city/test_workbench_web.py`
- Modify: `tests/integration/test_city_workbench_artifacts.py`
- Modify: `tests/integration/test_city_workbench_registry.py`
- Modify: `tests/property/test_city_workbench_properties.py`
- Modify: `tests/security/test_workbench_browser_security.py`

**Interfaces:**
- Consumes: Tasks 4, 6, 7, and 8.
- Produces:
  - `@dataclass(slots=True) class WorkbenchCognitionOperationV2` containing only operation-scoped profile, endpoint, and resolved credential plus `clear() -> None`.
  - `WorkbenchRunRepository.__init__(workspace: WorkbenchWorkspace, *, provider_runtime: ProviderRuntimePolicy | None = None, provider_registry: ProviderProfileRegistry | None = None, credential_store: CityCredentialStore | None = None, endpoint_policy: CityEndpointPolicy | None = None, cognition_service: CityCognitionService | None = None, cognition_service_v2: CityCognitionServiceV2 | None = None, compatibility_registry: WorkbenchProviderCompatibilityRegistry | None = None, provider_factory_v2: WorkbenchCheckProviderFactoryV2 | None = None) -> None`.
  - `WorkbenchRunRepository.preflight_cognition_v2(validated: ValidatedWorkbenchRun) -> WorkbenchCognitionOperationV2`.
  - `WorkbenchRunRepository.prepare_scientific_v2(validated: ValidatedWorkbenchRun, operation: WorkbenchCognitionOperationV2) -> PreparedCityScientificRunV2`.
  - `WorkbenchRunRepository.resolve_cognition_v2(prepared: PreparedCityScientificRunV2, operation: WorkbenchCognitionOperationV2, cancellation: CityCognitionCancellation) -> CityCognitionEvaluationV2`.
  - `WorkbenchRunRepository.finalize_cognition_v2(prepared: PreparedCityScientificRunV2, evaluation: CityCognitionEvaluationV2) -> PreparedCityRun`.
  - V10 additions to `WorkbenchAcceptedSettings`, `WorkbenchRunSummary`, `_RunOperations`, and `WorkbenchJobPhase`.
- V10 phases are exactly `planning-cognition`, `requesting-cognition`, and `applying-cognition`; V9 retains `selecting-explanations` and `requesting-explanations`.

- [ ] **Step 1: Write exact preflight-order tests**

  For provider V4, assert `require_success` is called before destination inspection, credential resolution, job ID creation, or executor submission. Missing/stale/V1-only/incompatible checks yield the constant public refusal and leave no active lease/job/secret. Rules/mock bypass compatibility and credentials.

- [ ] **Step 2: Write repository V2 lifecycle tests**

  Assert preparation is pure, service resolution uses only V2 provider/service/cache, finalization is a separate pure phase, operation credentials clear on success/fallback/cancel/defect, and publication loads back exact V10 evidence.

- [ ] **Step 3: Write V10 accepted-settings/summary tests**

  Assert exact pair `(4,10)`, effect scope, cognition mode, provider binding, funded response limit, aggregate ceiling, source/fallback/applied counts, and inspector URL. Reject bool/float/string schema/count values and incomplete/misplaced provider fields; keep V1-V9 summaries unchanged.

- [ ] **Step 4: Write job phase/cancellation/fault tests**

  Observe canonical V10 phase order through completion. Cancel before planning, after planning, during provider wait, and after resolution/before persistence; assert no completed artifact. Fault provider, cache, finalization, save, and verification; assert screened failure, released operation lease/credential, and no half-published completed run.

- [ ] **Step 5: Write end-to-end API job tests**

  Submit rules, mock, valid provider, cache hit, fallback, and budget-skipped schema-4 jobs through `/api/jobs`; poll to completion and freshly load V10. Assert machine JSON is value-free, double submit is refused, stale compatibility cannot authorize, and V1-V3 job/API behavior is unchanged.

- [ ] **Step 6: Run the focused red node**

  Run: `uv run pytest tests/unit/city/test_workbench_jobs.py::test_schema4_provider_check_precedes_destination_and_job_reservation -q`

  Expected: FAIL because Task 5's explicit schema-4 submission refusal fires before V2 preflight/phase execution.

- [ ] **Step 7: Add V2 operation/repository methods and injected service/provider factories**

  Extend `WorkbenchRunRepository.__init__` with optional `cognition_service_v2: CityCognitionServiceV2` and a typed V2 provider factory. Require the exact compatibility key before endpoint/credential resolution, and keep operation material private and clearable.

- [ ] **Step 8a: Extend accepted settings and summaries**

  Add only `(4,10)` and V10 effect/funding/evidence fields through strict version dispatch. Keep V1-V9 DTO fields and serialized values exact.

- [ ] **Step 8b: Extend the internal run protocol and job phases**

  Add only the three V10 phases and typed V2 repository methods to `_RunOperations`. Keep V9 phase names and control flow exact.

- [ ] **Step 9: Replace the temporary Task 5 refusal with exact V10 job dispatch**

  Branch by `WorkbenchRunInputV4`: preflight, ensure destination, prepare, request, apply, persist, verify. Pass `prepared.baseline_evaluation` to `CityCognitionServiceV2.resolve_many`. Check cancellation between every side-effect boundary; no provider-derived data becomes visible before pure finalization completes.

- [ ] **Step 10: Run V10 repository/job/API/security tests**

  Run: `uv run pytest tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_web.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py tests/property/test_city_workbench_properties.py tests/security/test_workbench_browser_security.py -q`

  Expected: PASS.

- [ ] **Step 11: Run V9 provider/job regressions**

  Run: `uv run pytest tests/unit/city/test_workbench_providers.py tests/integration/test_city_cognition_artifacts.py tests/unit/cognition/test_city_service.py -q`

  Expected: PASS.

- [ ] **Step 12a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py src/adlife/city/workbench_web.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_web.py`

  Expected: exit 0.

- [ ] **Step 12b: Run lint**

  Run: `uv run ruff check src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py src/adlife/city/workbench_web.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_web.py`

  Expected: exit 0.

- [ ] **Step 12c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 13: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py src/adlife/city/workbench_web.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_web.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py tests/property/test_city_workbench_properties.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 14: Stage only Task 9 files**

  Run: `git add -- src/adlife/city/workbench_runs.py src/adlife/city/workbench_jobs.py src/adlife/city/workbench_web.py tests/unit/city/test_workbench_runs.py tests/unit/city/test_workbench_jobs.py tests/unit/city/test_workbench_web.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py tests/property/test_city_workbench_properties.py tests/security/test_workbench_browser_security.py`

- [ ] **Step 15: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the ten Task 9 paths listed in Step 14.

- [ ] **Step 16: Commit V10 job activation**

  Run: `git commit -m "feat(workbench): execute bounded cognition runs"`

- [ ] **Step 17: Push the checked commit**

  Run: `git push origin main`

### Task 10: Replay V10 exclusively from frozen evidence and expose truthful CLI output

**Files:**
- Modify: `src/adlife/city/runs.py`
- Modify: `src/adlife/cli/commands/city_replay.py`
- Modify: `tests/integration/test_city_run_replay.py`
- Modify: `tests/cli/test_city_replay.py`
- Modify: `tests/security/test_city_artifact_security.py`

**Interfaces:**
- Consumes: verified `StoredCityRun` V10 from Task 8 and pure V2 core/runtime builders from Tasks 4 and 7.
- Produces: a V10 branch in `replay_city_run(stored: StoredCityRun) -> CityReplayResult` and additive V2 fields on `CityReplayResult` for response model/scope, baseline/applied/dual-state counts, cognition effect scope/mode, terminal/source/fallback/applied counts, and receipt hashes.
- CLI contract: `adlife city-replay ROOT RUN_ID` remains read-only; JSON contains only verified bounded fields, and human output distinguishes rule-owned purchase/baseline evidence from provider-dependent cognition.

- [ ] **Step 1: Write the no-external-dependency replay test**

  Load a V10 provider run, hash every source file, then replace provider constructors, network resolution, both cache versions, keyring, environment lookup, profile registry, current prompt constants, and current policy constants with raising sentinels. Assert replay is identical and source hashes/paths are unchanged.

- [ ] **Step 2: Write exact evidence-regeneration tests**

  Assert replay regenerates mobility, opportunities, attention, V1 baseline, V2 requests/reservations, validated stored terminals, applied responses, dual states, summaries, and manifest receipt exactly from frozen documents. Reverse stored line order, remove one selected record, or alter a policy/binding and assert corruption, never neutral repair.

- [ ] **Step 3: Write rules/mock/provider/fallback replay tests**

  Assert rules/mock need no provider state, identical recorded provider cognition gives byte-identical downstream V10 evidence, and fresh provider nondeterminism is irrelevant because replay never calls it.

- [ ] **Step 4: Write CLI JSON/human/error tests**

  Assert V10 JSON is one parseable object with model/effect scope, baseline/applied/state/count/hash fields; human text says bounded synthetic cognition and rule-owned purchase intention; neither mode exposes raw result prose/body/prompt, credentials, paths beyond the already public root contract, or tracebacks. Corrupt V10 exits through the artifact-error class.

- [ ] **Step 5: Run the focused red nodes**

  Run: `uv run pytest tests/integration/test_city_run_replay.py::test_v10_replay_is_offline_and_source_artifacts_are_immutable tests/cli/test_city_replay.py::test_v10_replay_json_separates_rule_and_cognition_evidence -q`

  Expected: FAIL because replay/CLI do not dispatch `CityRunManifestV10` or expose V2 evidence.

- [ ] **Step 6: Implement the V10 replay branch**

  Dispatch only after exact manifest type selection. Rebuild requests from frozen workbench/science, validate stored terminals against the frozen input policies, call `apply_spatial_cognition_v2`, and compare every evaluation/summary/receipt/manifest field. Do not import provider, cache, credential, registry, or network modules into replay code.

- [ ] **Step 7a: Extend `CityReplayResult` by exact response version**

  Preserve every V1-V9 field and add only bounded V10 typed projections for response/cognition identities, counts, and hashes.

- [ ] **Step 7b: Extend `city_replay` output by exact response version**

  Preserve V1-V9 wording. For V10, add explicit authority/claim-scope text; keep JSON stdout clean and diagnostics on stderr through the existing command boundary.

- [ ] **Step 8: Run replay/CLI/artifact-security tests**

  Run: `uv run pytest tests/integration/test_city_run_replay.py tests/cli/test_city_replay.py tests/security/test_city_artifact_security.py -q`

  Expected: PASS.

- [ ] **Step 9: Run V1-V9 replay regressions**

  Run: `uv run pytest tests/integration/test_artifact_layout.py tests/cli/test_replay_safety.py tests/integration/test_city_cognition_artifacts.py -q`

  Expected: PASS.

- [ ] **Step 10a: Run the replay suite with hash seed 0**

  Run:

  ```powershell
  $env:PYTHONHASHSEED = '0'
  try {
      uv run pytest tests/integration/test_city_run_replay.py -q
      if ($LASTEXITCODE -ne 0) {
          throw "hash-seed replay suite failed"
      }
  }
  finally {
      Remove-Item Env:PYTHONHASHSEED -ErrorAction SilentlyContinue
  }
  ```

  Expected: PASS.

- [ ] **Step 10b: Run the replay suite with hash seed 12345**

  Run:

  ```powershell
  $env:PYTHONHASHSEED = '12345'
  try {
      uv run pytest tests/integration/test_city_run_replay.py -q
      if ($LASTEXITCODE -ne 0) {
          throw "hash-seed replay suite failed"
      }
  }
  finally {
      Remove-Item Env:PYTHONHASHSEED -ErrorAction SilentlyContinue
  }
  ```

  Expected: PASS.

- [ ] **Step 11a: Run formatting**

  Run: `uv run ruff format --check src/adlife/city/runs.py src/adlife/cli/commands/city_replay.py tests/integration/test_city_run_replay.py tests/cli/test_city_replay.py tests/security/test_city_artifact_security.py`

  Expected: exit 0.

- [ ] **Step 11b: Run lint**

  Run: `uv run ruff check src/adlife/city/runs.py src/adlife/cli/commands/city_replay.py tests/integration/test_city_run_replay.py tests/cli/test_city_replay.py tests/security/test_city_artifact_security.py`

  Expected: exit 0.

- [ ] **Step 11c: Run strict typing**

  Run: `uv run mypy src`

  Expected: exit 0.

- [ ] **Step 12: Inspect the unstaged change**

  Run: `git diff -- src/adlife/city/runs.py src/adlife/cli/commands/city_replay.py tests/integration/test_city_run_replay.py tests/cli/test_city_replay.py tests/security/test_city_artifact_security.py`

- [ ] **Step 13: Stage only Task 10 files**

  Run: `git add -- src/adlife/city/runs.py src/adlife/cli/commands/city_replay.py tests/integration/test_city_run_replay.py tests/cli/test_city_replay.py tests/security/test_city_artifact_security.py`

- [ ] **Step 14: Verify the staged path set**

  Run: `git diff --cached --name-only`

  Expected: only the five Task 10 paths listed in Step 13.

- [ ] **Step 15: Commit offline V10 replay**

  Run: `git commit -m "feat(replay): reproduce frozen v10 cognition"`

- [ ] **Step 16: Push the checked commit**

  Run: `git push origin main`

---

## Runtime Completion Gate

Run this gate only after Tasks 1-10 are individually green and pushed. Execute each command separately and stop on the first nonzero exit; do not place them in one semicolon chain.

- [ ] Run: `uv run ruff format --check src/adlife/adapters/cognition src/adlife/city src/adlife/cli/commands/city_replay.py tests/unit/cognition tests/unit/city tests/integration/test_city_cognition_v2_pipeline.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_replay.py tests/security/test_city_provider_security.py tests/security/test_city_artifact_security.py tests/security/test_redaction_corpus.py`
- [ ] Run: `uv run ruff check src/adlife/adapters/cognition src/adlife/city src/adlife/cli/commands/city_replay.py tests/unit/cognition tests/unit/city tests/integration/test_city_cognition_v2_pipeline.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_replay.py tests/security/test_city_provider_security.py tests/security/test_city_artifact_security.py tests/security/test_redaction_corpus.py`
- [ ] Run: `uv run mypy src`
- [ ] Run: `uv run pytest tests/unit/cognition tests/unit/city tests/architecture/test_core_import_boundary.py tests/golden/test_city_cognition_v2_science.py tests/integration/test_artifact_layout.py tests/integration/test_city_response_run.py tests/integration/test_city_cognition_artifacts.py tests/integration/test_city_cognition_v2_pipeline.py tests/integration/test_city_cognition_v2_artifacts.py tests/integration/test_city_run_store.py tests/integration/test_city_run_replay.py tests/integration/test_city_workbench_artifacts.py tests/integration/test_city_workbench_registry.py tests/property/test_city_workbench_properties.py tests/property/test_spatial_response_v2_invariants.py tests/contract/test_run_store.py tests/contract/test_schema_versions.py tests/security/test_city_provider_security.py tests/security/test_city_artifact_security.py tests/security/test_redaction_corpus.py tests/security/test_workbench_browser_security.py tests/cli/test_city_replay.py tests/cli/test_replay_safety.py -q`
- [ ] Run: `git diff --check`
- [ ] Run: `git status --short`

Expected final runtime state: schema 4 can be constructed, compatibility-checked, executed, atomically stored as V10, freshly verified, and replayed offline; schema 1-3/V1-V9 behavior remains green; no presentation/report/metrics claim is added by this plan. The separate experience/release plan owns V10 metrics, studies, reports, browser controls/inspector, documentation, packaging, full-suite certification, and preview replacement.
