# City Provider Cognition Design

**Status:** Approved for implementation planning on 2026-10-13
**Date:** 2026-10-12
**Depends on:** `2026-10-12-city-personas-design.md`
**Scope:** Optional, provider-backed narrative explanations for deterministic city responses

## Intent

The local city workbench currently has no runtime AI integration. Its provider capability flags are false and every city result is produced by deterministic rules. This design adds a provider panel and optional OpenAI-compatible narrative generation without changing the scientific model:

> The deterministic city engine decides movement, exposure, attention, response, state, and every numeric metric. A provider may only explain an already-computed synthetic response in bounded text.

The resulting prose is illustrative synthetic narrative. It is not chain-of-thought, observed behavior, validation of the rule, or evidence about real people.

## Current constraints

- City simulation is a separate pipeline from legacy zone cognition.
- The existing legacy `CognitionResult` contains bounded numeric influence fields. Reusing it would silently broaden city semantics and is prohibited.
- Only noticed impressions create city response events; ignored impressions do not change state.
- City response aggregation reads immutable pre-minute state and commits campaign-scoped updates atomically.
- V7 replay regenerates deterministic numeric evidence from frozen inputs and refuses corruption.
- The current OpenAI-compatible adapter implements a strict, non-streaming Chat Completions subset. It does not implement Responses API, streaming, tools, model discovery, or OAuth.

## Chosen approach

Add a city-specific narrative request/result contract, a server-owned provider-profile registry, and a frozen terminal cognition stream. Deterministic science completes first in staging. A deterministic subset of persisted response events is then explained. Every selected request receives exactly one terminal record: validated provider result, deterministic fallback, or deterministic budget skip. Only after the complete record set verifies does the run publish.

This is preferred over inserting a provider inside the response equation because it preserves causal auditability, A/A behavior, offline fallback, and replay. It is preferred over reusing the legacy cognition result because the city contract contains no numeric modifier at all.

## Semantic boundary

`response_mode` remains exactly `deterministic-rules`. A separate `cognition_mode` controls narrative generation:

- `rules`: deterministic local explanations, no provider or credential;
- `mock`: deterministic provider-shaped explanations for tests and demos;
- `local`: loopback OpenAI-compatible provider;
- `remote`: explicitly allowed public HTTPS OpenAI-compatible provider.

Replay is not a cognition mode. It is an artifact operation that consumes frozen terminal records and makes zero provider calls.

Across all modes, output wording, cache state, provider latency, retries, fallback, model, and sampling must leave the following byte-identical after normalization for run identity and wall-clock telemetry:

- mobility events;
- opportunity and attention evidence;
- response events and numeric deltas;
- state snapshots;
- purchase-intention and other response state;
- scientific metrics.

This feature adds no purchase transaction or monetary-budget model.

Provider output never becomes input to another response or later cognition request in this slice. It cannot create events, choose an agent or campaign, move an agent, modify budget, supply probability, change state, or add memory/social propagation.

## City cognition contracts

### Request V1

`CityCognitionRequestV1` is strict, frozen, extra-forbidden, and at most 8,192 canonical UTF-8 bytes. It contains:

- exact integer schema version;
- a stable `cog-<64 lowercase hex>` request ID derived from canonical request facts;
- run-input fingerprint, response event ID, causal notice/opportunity ID, agent ID, and campaign ID;
- model minute, activity, channel, placement, and creative snapshot/hash;
- the frozen fictional persona's bounded descriptive context and explicit interests/traits;
- the authoritative deterministic rule factors, numeric deltas, pre-state digest, committed state-update event ID, and committed post-minute state;
- disclosure `synthetic-narrative-not-observed-behavior`.

Several responses in the same minute may contribute to one atomic committed state update. The request identifies that shared update and describes the selected response's own delta; it never attributes the complete combined state change to one response. It omits filesystem paths, credentials, provider configuration, unrelated agents, full trajectories, raw uploaded documents, real identities, and any instruction that asks the model to override the rule result.

Campaign and persona text are delimited as untrusted data. The system instruction states that embedded instructions are content to describe, not commands to follow.

### Result V1

`CityCognitionResultV1` contains only:

| Field | Contract |
|---|---|
| `schema_version` | exact integer `1` |
| `request_id` | exact request ID match |
| `interpretation` | 1–480 safe characters |
| `decision_factors` | 1–3 unique safe strings, each 1–120 characters |
| `safety_flags` | zero to three values from `stereotype-risk`, `unsupported-inference`, `unsafe-content` |
| `disclosure` | exact literal `synthetic-narrative-not-observed-behavior` |

There are no provider-chosen reaction labels, probabilities, valence, relevance, credibility, sharing, rule-modifier, movement, budget, memory, event, identity, or state-update fields. Unknown fields are rejected rather than dropped. Non-finite values, invalid Unicode, control/bidi override characters, secret-like text, request-ID mismatch, and oversized output are invalid.

The UI calls provider/cache text an “AI-generated explanation” and labels rules, mock, fallback, and skipped text by its actual deterministic source. It never calls any explanation “the person's thoughts,” “reasoning trace,” or “prediction.”

## Deterministic selection and budgets

Eligible responses are persisted noticed-response events sorted by:

```text
(model_minute, agent_id, campaign_id, response_event_id)
```

Selection takes at most the first four responses per agent and then the first 60 globally. Ignored impressions never become requests. Request construction, selection, budget winners, terminal ordering, and fallback wording are deterministic and independent of async completion order.

The first implementation freezes these hard limits in the run input:

| Limit | Value |
|---|---:|
| selected requests per agent | 4 |
| selected requests per run | 60 |
| concurrent calls | 4 |
| request bytes | 8,192 |
| wire response bytes | 1,048,576 |
| generated tokens requested per call | 384 |
| output-token reservation per remote/local request | 1,536 (4 attempts × 384) |
| maximum aggregate requested output-token reservation | 92,160 |
| initial HTTP attempt | 1 |
| transient transport retries | 2 |
| schema repair attempts | 1 |
| total HTTP attempts per request | 4 |
| timeout per attempt | 30 seconds |
| aggregate cognition wall time | 180 seconds |

Before any async local/remote work starts, selected requests receive whole 1,536-token reservations in deterministic selection order. An operator may choose a lower aggregate ceiling in whole-reservation increments; local/remote requests after the last funded reservation become deterministic `budget-skipped` records. Reservations are never reassigned based on completion timing. Every initial, retry, and repair attempt charges its own 384-token slice from that request's reservation. Unused slices are not transferred to another request. Cache hits produce fully bound records but consume no attempt slice. Rules and mock modes use no provider-token budget, never produce `budget-skipped`, and always emit their deterministic record for every selected request.

This is an output-token scheduling ceiling, not a monetary limit and not a bound on provider input-token charges. The UI states that remote inference can incur additional input and vendor costs.

Only connection/timeouts, `429`, and explicitly selected transient `5xx` statuses retry. Authentication, authorization, endpoint-policy, incompatible-protocol, and schema-contract failures do not retry transport. At most one schema-repair request is allowed after an otherwise successful but invalid answer.

Cancellation stops scheduling, cancels bounded in-flight awaits, and leaves no published run. It never converts cancellation, malformed run inputs, artifact corruption, I/O failure, or programming defects into a successful fallback.

## Provider profiles

The server owns provider profiles. A profile has a stable ID and immutable revision containing:

- mode: `local` or `remote`;
- normalized base URL;
- model identifier;
- exact protocol identifier `openai-chat-completions-json-schema-v1`;
- temperature, top-p, seed policy, and output-token limit;
- timeout/retry limits within the hard ceilings;
- server-assigned credential-slot metadata, never a credential value;
- the exact approved origin/base-path policy configured when the server starts.

The browser may create a new non-secret profile revision, choose a profile, inspect its normalized non-secret configuration, and run an explicit compatibility check. It cannot mutate a profile revision already referenced by a run; editing creates a new revision/hash.

Profiles are canonical immutable records at `<pinned-workbench-root>/provider-profiles/<profile_id>/<revision_sha256>.json`; `revision_sha256` is the full content hash, and there is no mutable “current” pointer. Listings sort by `(profile_id, revision_sha256)` and the browser/job selects one exact revision. The registry accepts at most 20 profile IDs, at most 10 revisions per ID, and at most 16,384 canonical bytes per revision. Profile IDs are 1–64-character portable slugs, display labels are at most 80 characters, model IDs at most 120 characters, and normalized URLs at most 2,048 characters. Publication is locked, no-clobber, symlink/reparse-safe, and independently revalidated using the same workspace principles as run artifacts. There is no delete or in-place update in this slice.

Public provider APIs are:

- `GET /api/providers` — path-free profile summaries and capabilities;
- `POST /api/providers` — create one validated non-secret immutable profile revision;
- `POST /api/providers/{profile_id}/revisions/{revision_id}/credential` — store/replace the exact revision's origin-bound credential in the configured server-side secret store;
- `POST /api/providers/{profile_id}/revisions/{revision_id}/check` — perform one explicit bounded compatible-inference check;
- `POST /api/jobs` — select a profile revision through the workbench cognition draft.

Provider creation and credential writes remain loopback-only, same-origin, CSRF-protected POSTs. Query parameters are refused. The API distinguishes:

- profile configured;
- credential available;
- endpoint policy accepted;
- last explicit compatible inference succeeded.

A successful model-list request is not treated as proof that strict JSON-schema inference works. Compatibility checking can incur provider usage and requires an explicit UI action and disclosure. Checks are single-flight, are refused while a simulation job is active, and are limited to five starts per rolling minute per process. Each check submits the full CityCognitionResultV1 schema with one attempt, a 10-second deadline, the normal 384-token output maximum, and a 1,048,576-byte wire-response maximum; it never retries or repairs. These limits are independent of run budgets.

## Credential handling

Remote credentials come only from an explicitly named environment variable or an OS-keyring-backed credential slot. Every slot is created server-side and bound to one trust class and exact normalized scheme/host/port/base path. A profile cannot borrow a slot bound to another origin, even when both origins are on the server allowlist. Local mode always uses a fixed dummy credential and never inherits a remote credential.

When OS keyring support is available, the UI may submit a 1–4,096-byte credential once to the dedicated endpoint through a separate 8,192-byte request ceiling. This design deliberately supersedes the earlier workbench prohibition on credential-bearing browser requests for this one route only. The secret exists transiently in the password input and same-origin request body, is excluded from rendered text/attributes and application state, and the field is cleared immediately after submission. The application requests no browser persistence or autocomplete but does not claim control over independent password-manager behavior. If no approved keyring backend is available, the endpoint refuses and directs the operator to the configured environment variable; it never falls back to plaintext storage.

Job acceptance resolves the profile and its exact origin-bound credential before reserving a run ID, copies the credential into a private job-local in-memory value, and clears that value after the bounded job. Replacing the keyring entry while a job or check is running cannot change later attempts in that operation. Missing credentials, mismatched slots, and endpoint-policy failures refuse job acceptance; only availability or provider refusal arising after successful acceptance can produce a frozen runtime fallback.

Credentials are never written to or returned in:

- browser storage or later API responses;
- provider-profile documents;
- run inputs, manifests, cognition records, cache, reports, metrics, or HTML;
- URLs, query strings, exception messages, logs, raw-response diagnostics, or temporary filenames;
- prompts or repair messages.

The server does not read ChatGPT/Codex login caches, browser cookies, shell authentication, or unrelated ambient provider keys.

A ChatGPT/Codex subscription, identity OAuth token, or successful product login does not satisfy this Chat Completions profile contract. A provider-specific OAuth integration may support inference when its issuer, audience, scopes, protocol, storage, refresh, logout, and revocation are implemented deliberately; that is a separate reviewed subsystem, not a generic credential shortcut.

## Endpoint and transport security

### Local trust class

- The default is exactly `http://127.0.0.1:11434/v1`. Other literal `127.0.0.1` or `[::1]` origins, ports, and base paths must be explicitly approved at server startup; hostname aliases such as `localhost` are not accepted.
- Userinfo, query, fragment, redirects, proxy inheritance, Unix sockets, and arbitrary headers are refused.
- A remote credential is never attached.

### Remote trust class

- HTTPS and valid TLS hostname verification are mandatory.
- The normalized scheme, hostname, effective port, and base path must exactly match one server-start allowlist entry for the profile.
- Userinfo, query, fragment, credential-bearing path segments, redirects, ambient proxies, and arbitrary headers are refused.
- DNS is resolved for every attempt; all IPv4/IPv6 results must be public and must not be loopback, private, link-local, multicast, reserved, documentation, carrier-grade NAT, metadata-service, or otherwise special-use space.
- Connection establishment is bound to the validated resolution while TLS validates the original hostname; peer-address mismatch or DNS rebinding refuses the attempt.
- Redirect following is disabled, including during compatibility checks and repair.

The only outbound request is a non-streaming `POST <base_url>/chat/completions` with bearer authorization, model, system/user messages, sampling fields, `max_tokens`, optional seed, and mandatory strict `response_format.type=json_schema`. The response must contain nonempty `choices[0].message.content` holding one valid result object. Streaming/SSE, tool calls, images, provider-native extensions, silent schema downgrades, and automatic model discovery are unsupported.

Provider claims of “OpenAI compatibility” are not sufficient; the explicit check validates this exact subset.

## Failure and fallback

Provider failure cannot change scientific output. For every selected request that does not produce a valid result, the service creates a deterministic grounded explanation from the already-frozen rule facts and records one bounded reason code such as:

- `endpoint-resolution-refused`;
- `authentication-refused`;
- `timeout-exhausted`;
- `rate-limit-exhausted`;
- `server-error-exhausted`;
- `response-too-large`;
- `protocol-incompatible`;
- `invalid-result`;
- `budget-skipped`.

Error bodies and raw provider responses are not persisted. Diagnostics contain only screened bounded reason codes and safe provider metadata. Missing credentials, invalid profiles, and statically rejected endpoints fail submission before run-ID reservation. Fallback is valid only for a runtime provider/DNS/TLS failure after valid run construction; it must not hide corrupt artifacts, invalid local input, storage failure, or implementation defects.

## Workbench and manifest versioning

This feature follows persona schema 2 and introduces `WorkbenchRunDraftV3`/`WorkbenchRunInputV3`. Schema 3 retains the schema-2 population contract and adds a strict cognition selection containing mode, provider-profile revision/hash where applicable, prompt/schema version, and frozen budgets.

New schema-3 runs use `CityRunManifestV9`. Rules-only legacy schema-1/V7 and persona schema-2/V8 runs remain valid. Once the UI emits schema 3, every selected response gets a frozen terminal record in every mode. `cognition_mode: rules` uses `city-rules-explainer-v1`; `mock` uses `city-mock-explainer-v1`. Both are deterministic, credential-free, and make no provider call.

V9 binds:

- exact city, scenario, mobility, response, creative, and workbench inputs;
- canonical cognition-input snapshot hash and byte count;
- city cognition request/result schema and explainer identities;
- complete prompt/result-schema/selection/fallback contract versions and hashes;
- complete selected non-secret provider profile snapshot and revision/hash when applicable;
- sampling and all budgets;
- selected-request count and outcome counts;
- canonical terminal-record stream hash, byte count, and record count;
- summary hash;
- no credential or raw provider body.

Every consumer dispatches V9 intentionally. V1–V8 readers and their existing behavior remain supported; unknown versions refuse cleanly.

## Frozen cognition evidence

The run stores these self-contained artifacts:

- `inputs/cognition.json`, at most 65,536 canonical bytes, containing the mode, complete non-secret profile snapshot when applicable, effective settings, prompt text, result JSON schema, deterministic selection policy, budget allocation, and deterministic fallback policy;
- `outputs/cognition.jsonl`, at most 1,048,576 bytes, containing at most 60 canonical terminal records of at most 8,192 bytes each;
- `outputs/cognition-summary.json`, at most 16,384 canonical bytes, containing exact source/reason/count/usage totals and disclosure.

Required/forbidden presence is versioned: every V9 run contains all three files, including an empty JSONL stream when no response is eligible; V1–V8 runs contain none of them. A provider registry, current prompt constant, or current fallback implementation is never needed to validate V9.

Records are sorted by deterministic selection order and each binds:

- request ID and complete request hash;
- response, notice/opportunity, agent, campaign, and input fingerprints;
- provider profile/protocol/prompt/schema hashes without secrets;
- source kind: `rules`, `mock`, `provider`, `cache`, `fallback`, or `budget-skipped`;
- validated result or deterministic fallback result;
- bounded reason code, strict attempt count, and optional strict nonnegative integer prompt/completion usage no larger than 2,147,483,647;
- record hash.

Allowed combinations are exact:

| Source kind | Result | Attempts | Reason | Provider snapshot |
|---|---|---:|---|---|
| `rules` | deterministic rules explanation | `0` | absent | absent |
| `mock` | deterministic mock explanation | `0` | absent | absent |
| `provider` | validated provider result | `1`–`4` | absent | required |
| `cache` | validated cached result | `0` | absent | required |
| `fallback` | deterministic fallback result | `0`–`4` | required runtime reason | required |
| `budget-skipped` | deterministic fallback result | `0` | exact `budget-skipped` | required |

Usage is absent for rules/mock/budget-skipped, optional for provider/cache/fallback, and informational only. Request IDs are SHA-256 over canonical request facts with `request_id` omitted. Record hashes are SHA-256 over canonical record data with `record_hash` omitted. These derivations are non-circular.

Fresh fallbacks and budget skips are stored even though the existing opportunistic cache does not store them. The cache is only an optimization; its key or contents are not artifact-integrity evidence.

Publication is manifest-last. Any cognition-write failure leaves the source workspace without a completed run and preserves existing artifacts. Independent loading verifies canonical bytes, counts, hashes, ordering, request/result identity, causal event bindings, allowed source/status combinations, provider snapshots, and exactly one terminal record per selected request. It independently regenerates rules/mock/fallback/budget-skipped results from the frozen versioned policy so a rehashed plausible replacement still fails.

## Replay

Replay:

1. loads and verifies all frozen numeric inputs and events;
2. regenerates deterministic scientific output and deterministic selected cognition requests;
3. reads the frozen terminal stream without credentials, provider configuration, cache, catalog, or network;
4. verifies every request/result binding, status, reason, count, and hash;
5. reproduces the same saved narrative projection in memory without writing source or destination artifacts.

Replay never regenerates prose, fills a missing record, calls a provider, silently falls back, or repairs a mismatch. Live provider prose is not promised to repeat on a fresh independent run, even at temperature zero; the frozen run and its replay are reproducible.

## User interface

The RUN step gains an optional “Explanations” panel:

- Rules, mock, local, and remote mode choices with plain-language scope;
- profile chooser/editor for non-secret endpoint/model/protocol settings;
- credential availability only, never the stored value;
- explicit “Check compatibility” action with usage warning;
- exact narrative budget summary before starting;
- disclosure that the deterministic engine controls all numeric behavior.

Job progress uses truthful phases such as `simulating`, `selecting explanations`, `requesting explanations`, `verifying artifacts`, and `complete`. It shows counts, not fabricated percentages.

The saved inspector presents explanations beside their already-computed response events, with provider/model, terminal status, fallback reason, and synthetic-narrative disclosure. It renders provider text as inert text, not HTML or executable Markdown. Rules and fallback explanations remain useful offline.

## Compatibility and architecture

- `src/adlife/core` contains only provider-neutral city cognition ports/models and imports no FastAPI, HTTP client, keyring, or provider adapter.
- Provider transport, profile storage, credential storage, and FastAPI routes remain adapters/application code.
- The deterministic simulation can run without any cognition adapter.
- Existing legacy zone cognition semantics and tests remain unchanged.
- Existing V1–V8 city artifacts remain readable and replayable without migration.
- Default demo and test flows remain offline and credential-free.
- Reports include only validated narrative, safe provenance, and disclosure; no raw responses or secrets.

## Required tests

### Semantics and determinism

- Golden numeric invariance across every cognition mode, valid prose variants, model/sampling changes, cache hits, timeout/fallback, and reordered async completion.
- Deterministic selection, per-agent/global reservation winners, fixed attempt charging, terminal ordering, no ignored-impression request, no duplicate/orphan record, and complete causal binding.
- Provider cannot create events, control movement, mutate budget/state, supply purchase probability, inject agent/campaign IDs, or affect later requests.
- Rules/mock/fallback outputs are deterministic under multiple `PYTHONHASHSEED` values.

### Provider and security

- Exact Chat Completions request/response subset and strict-schema compatibility check.
- Request-ID mismatch, unknown/forbidden/numeric influence fields, malformed/deep/huge JSON, NaN/Infinity, invalid Unicode, prompt injection, streaming/dribbling, compressed-body limits, and hostile prose.
- Every retry, repair, dispatch, reservation, attempt-token, concurrency, per-attempt timeout, run-deadline, profile-count/revision/size, and compatibility-check bound.
- Cancellation during DNS/connect/read/repair; no new work after cancellation.
- Remote HTTPS, allowlist, TLS, IPv4/IPv6 special ranges, DNS rebinding, redirect, proxy, userinfo/query/fragment, header, and credential-path refusals using local test doubles only.
- Local mode accepts only loopback and never sends a remote secret.
- Fake secrets appear only transiently as the password input value and dedicated credential-request body; after submission they are absent from the DOM, rendered text/attributes, application state, later requests/responses, job state, logs, exception chains, cache, artifacts, reports, URLs, prompts, and temporary names.
- OS-keyring absence/refusal never creates plaintext fallback; password fields clear and do not persist.

### Artifacts and replay

- V9 canonical round trip and replay after removing credentials, profile registry, current prompt/fallback code defaults, cache, catalog, and network access.
- Provider, cache, fallback, and budget-skipped records replay exactly.
- Missing, duplicate, reordered, changed, rehashed-but-misbound, noncanonical, oversized, symlinked, and incomplete cognition artifacts refuse.
- Publication/write fault preserves prior artifacts and exposes no completed partial run.
- Hash all source artifacts before/after replay and prove read-only behavior.
- V1–V8 compatibility remains green.

### API and browser

- Provider/profile/credential/check endpoints enforce method, media, body, Host, Origin, CSRF, and bounded path-free errors.
- Configured/credential/compatible states are distinct and truthful.
- Provider panel keyboard/focus behavior, 390-pixel containment, stale-response handling, Persian/bidi text, inert hostile provider text, and no external browser assets.
- Saved inspector shows explanation scope, provenance, outcome counts, and fallback status without implying observed psychology.

### Repository gates

- Core architecture boundary and legacy cognition contract tests.
- Relevant unit, integration, browser, security, replay, report, study, packaging, and offline suites.
- Ruff format/lint, strict mypy, full pytest under multiple hash seeds, branch coverage, wheel smoke, and offline demo gates.

## Explicit non-goals

- Letting an LLM decide a purchase, movement, attention, state, budget, event, memory, or social propagation.
- Reusing legacy numeric cognition influence in the city engine.
- Chain-of-thought capture or claims that prose exposes model reasoning.
- Streaming, tools, multimodal input, model discovery, provider-native APIs, or silent protocol downgrade.
- Generic OAuth, ChatGPT/Codex subscription reuse, or application-user authentication.
- Remote multi-user deployment; it requires the separate identity, authorization, audit, quota, and secret-management roadmap gates.
- Real-person profiling or inference.

## Implementation order after approval

1. Complete the approved persona/V8 implementation dependency.
2. Add city request/result/terminal models and red/green semantic-invariance tests.
3. Add provider profile, credential-store, endpoint-policy, and transport adapters with adversarial tests.
4. Add deterministic selection, budgets, fallback, frozen stream, V9 publication, and offline replay.
5. Add provider APIs, panel, job phases, and saved inspector explanations with browser tests.
6. Run security, determinism, replay, report, packaging, wheel, and full repository gates.
