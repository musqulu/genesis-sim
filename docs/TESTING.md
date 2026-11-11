# Testing and experimental evidence

Status: updated 2026-11-10. Automated tests exist and have been run through M10; see each milestone's validation note (latest: [M10](validation/M10.md)) for evidence and outstanding checks. The broader strategy below remains a plan for later milestones, including local MLX-LM and API-connected models. Milestone-specific tests and manual procedures are in [ROADMAP.md](ROADMAP.md); rules under test are in [ARCHITECTURE.md](ARCHITECTURE.md).

## What counts as failure

| Observation | Classification | Response |
|---|---|---|
| Crash, lost committed events, invalid transition, duplicated birth, resource discrepancy, leaked private state | Technical failure | Preserve diagnostics, pause if integrity is uncertain, repair code with explicit review and versioned tests. |
| Model proposes malformed/illegal action; engine rejects it without action effects | Contained decision failure | Record raw output/reason and opportunity cost categories. It may indicate integration/prompt problems; never silently replace it with a successful action. |
| Engine accepts an illegal action or rejects one contrary to published rules | Technical failure | Reproduce from fixture; correct validator/reducer. |
| MLX worker failure, missing local model, API outage, authentication failure or rate limit | Technical failure | Bounded retry for transient failures; configuration/authentication errors pause for correction. No committed ecological time advances while waiting, and no automatic provider fallback occurs. |
| Starvation, extinction, nonreproduction, hoarding, repetitive movement, conflict, useless structures | Simulation outcome | Preserve and describe it. No replenishment, resurrection, hidden parameter change or success-targeted fallback. |
| Reflection makes an unsupported factual claim but cites accessible evidence correctly | Organism-level interpretation error | Mark as interpretation; preserve evidence. Do not rewrite the world to match language. |
| A “memory” cites another organism's private record, or changes historical facts | Technical boundary failure | Reject, test isolation and provenance. |

These categories prevent technical reliability from becoming ecological intervention. Health dashboards show integrity/provider/storage failures separately from population and behavioral measures.

## Three different reproducibility claims

1. **Recorded replay:** exact reconstruction of committed state at every event/tick from immutable effects. No model or RNG resampling. This is a hard requirement.
2. **Deterministic engine rerun:** same initial state, versioned rules, seeded random algorithm, ordered requests and recorded responses give the same canonical hashes. This is a hard requirement within the declared engine/schema version.
3. **Fresh live-model trial:** same seed and prompt settings may yield different outputs across devices, model runtimes or invocations. Hosted aliases may also change without exposing a weight digest. This is an experiment, not a bitwise determinism promise. Temperature zero is not evidence of universal determinism.

Every failure artifact includes run/config/engine revision, event schema, seed and RNG state, request IDs/hashes, prompts and raw outputs, provider kind, sanitized endpoint identity, model identifier/available revision or digest/settings and the shortest useful event prefix. Mark unavailable provider metadata as unknown. Credentials and authorization headers must never enter artifacts. Wall-clock timing and UI pacing are excluded from ecological state hashes but retained as operational evidence. Record random draw outcomes for lifespans and inheritance so replay does not depend on a changed PRNG implementation.

## Test layers and execution policy

| Layer | Suggested tooling | Purpose / when |
|---|---|---|
| Domain unit tests | pytest; standard fixtures | Costs, bounds, perception, lifecycle and exact expected deltas; every relevant change. |
| Stateful/property tests | [Hypothesis state machines](https://hypothesis.readthedocs.io/en/latest/stateful.html) | Generate sequences of valid/invalid actions and shrink invariant failures; physics/ledger changes. |
| Persistence integration | Temporary real SQLite DB, recorded provider, subprocess fault injection | Commit/restart/replay/backup correctness; storage and scheduler changes. |
| Provider contract | Mock MLX worker, local fake HTTP endpoint and recorded byte-for-byte outputs | Schema, cancellation, retry, credentials, model metadata, prompt isolation; no model download or paid API call in CI. |
| Browser acceptance | [Playwright](https://playwright.dev/docs/intro), real local API with deterministic fixtures | Selection, control semantics, disconnection, inspector and historical truth. |
| Live-model experiment | Explicit opt-in, selected MLX-LM or API model/config | Semantic usefulness, schema adherence, token/time costs, behavior; separate from deterministic merge checks. Verify actual endpoint compatibility before claiming it. |
| Soak/fault campaign | Offline fixture policies, then opt-in live trial | Bounded RAM/queues, complete history and recovery across generations. |

Only install these tools after the relevant implementation approval. Keep commands simple and documented in M01; expected organization is `tests/unit`, `properties`, `integration`, `fixtures`, `e2e`, `soak`. Do not test UI styling with broad brittle snapshots; test meaningful selection and state. Do not assert generated prose verbatim. Lock supported dependency/runtime versions when building, including the SQLite runtime actually used by Python.

## Local and API provider acceptance

Run the same normalized-response fixtures through both adapters and require equal engine outcomes/hashes for equal requests and supplied responses. This checks transport independence; it does not require two different live models to choose the same action.

| Boundary | Required evidence |
|---|---|
| MLX-LM integration (M07A) | One resident model in a dedicated worker; bounded context/output; missing model and worker crashes detected; observer remains responsive; timed-out replies cannot commit later. |
| API connection (M07B) | Configurable URL/model/credential reference; selected endpoint accepts the request dialect; optional model-list support; API-only run starts without MLX imports or local weights. |
| Output capabilities | Test native-schema support and unsupported-schema cases independently; schema prompting is not treated as guaranteed constrained decoding. Malformed/illegal outputs have no action effects. |
| Provider errors | Fixtures for 401/403, 429, 5xx, connection loss and timeout; bounded backoff for transient errors, no endless auth retry, one world commit per request despite duplicated responses. |
| Secret handling | Sentinel API key never appears in browser payloads, prompts, history, exception logs or exports; backend alone resolves the credential reference. |
| Usage and limits | Missing usage is unknown, not zero; request/token ceilings are enforced before dispatch where possible; budget exhaustion pauses technical work without changing organism resources. Cost estimates are labeled and separately sourced. |
| Selection and isolation | New-run selector shows local versus remote provider/model; credentials/private contexts never cross connections; unavailable model never silently switches provider. |
| Replay | Recorded local and API runs reconstruct with MLX disabled, network blocked and no API key present. |

If API credentials are unavailable during implementation, offline adapter tests can pass but the selected endpoint's live smoke check stays explicitly unverified. A successful test of one compatible endpoint does not establish support for every vendor API. Cancellation can prevent world effects but may not prevent remote billing; record ambiguous attempts without resubmitting indefinitely.

## Core invariants and edge cases

| Area | Required assertions | Important boundaries |
|---|---|---|
| Grid | Dimensions/coordinates stable; one living occupant/cell; positions inside bounds | Corners, one-cell fixtures, last row/column, negative and huge coordinates. |
| Action validation | Invalid action leaves action-owned domain state unchanged; no partial transfer/modification | Negative/zero/boolean/fractional amounts; unknown fields; oversized strings; stale request; dead actor. |
| Energy | Sum of current stores matches initial + actual source − named sinks | Capped inflow, fee exactly affordable, insufficient pre-extraction fee, zero post-action energy. |
| Material | Loose + packed + living carried + retired equals initial total | Full cell/inventory, unpack overflow, parent/child endowment, death with material. |
| Knowledge | Cognition only receives visible current data plus its accessible memory/messages | Hidden-field sentinel values outside radius; neighbor private energy/deadline; observer API not available to model. |
| Scheduling | Same state for all pre-action observations; logged rotated resolution; no completion-order advantage | Two actors target same cell, competing extraction/recipient capacity, message delivered next tick. |
| Lifecycle | Every organism has a bounded private lifespan; each death happens once; dead instance never acts | Death at upkeep, exact deadline, death after valid action, pending stale proposal, last death/extinction. |
| Cognition | One consumed response/request; no request on routine nondue tick; queue bounded | Delayed/duplicate/truncated output; context overflow; trigger storms; provider timeout. |
| Memory | Active limits/costs enforced; historical contents preserved; no cross-agent aliasing | Empty/equal retrieval scores, Unicode byte limits, forgotten evidence, unaffordable write, eviction ties. |
| Reflection | Source IDs accessible; triggers/cooldowns/costs bounded; facts remain immutable | Missing source, ancestor-only private source, repeated reflections, too many output records. |
| Reproduction | Parent funds child; birth fully atomic; unique identity/ancestry; offspring first acts next tick | Occupied target, cooldown boundary, exact fee/endowment/retained energy, duplicated request, parent death. |
| Inheritance | Partial traits/information; seeded variation reproducible; independent child records | Zero retained memories, all candidate drops, clipping, invalid ownership, parent mutation after birth. |
| History | State has complete causal events; events ordered/immutable; snapshots complete | Unknown schema, missing event, checksum mismatch, pagination gaps, memory at earlier tick. |
| Operations | Technical recovery does not alter rules/population/resources | Restart extinct run, disk-full, corrupt data, missing model, second runner attempting ownership. |

For invalid-action tests, compare the candidate state immediately before and after validation; also compare whole ticks against an otherwise identical rejected/waiting fixture to account for separately scheduled upkeep/cognition. Diagnostic records may change. Do not accidentally exempt unauthorized memory or metadata mutations from the domain-state comparison.

Property generators must respect published rules when generating a “valid” case. Use a small independent accounting oracle for energy/material rather than repeating the production transition implementation. Assert conservation after every event group, not just at the final tick. Hypothesis shrinking should preserve a reproducible failing sequence.

## Replay, transactions and crash matrix

For every state-bearing event type, build a fixture with known before/after values. Reconstruct all fixture ticks from genesis, then from each eligible snapshot plus tail. Compare canonical hashes including private deadlines, memory active sets, inboxes, scheduler/RNG state and ancestral data. Historical UI queries must match those reconstructed values, never current-state fields.

| Inject failure at | Expected durable result |
|---|---|
| Before request input is persisted | Last committed tick; prepare a new request with logged recovery context. |
| After input persisted, before provider responds | Same pending request/input; bounded retry when resumed; no ecological effects yet. |
| After response persisted, before tick transaction | Reuse recorded response; no duplicate inference needed for that request. |
| During action/event/state transaction | Full rollback or full commit; never a partial birth, charge or modification. |
| After commit, before UI notification | Restart/reconnect sees the committed sequence exactly once. |
| While writing snapshot | Previous valid snapshot plus complete event tail reconstructs state. |
| During backup | Incomplete backup marked invalid; original run preserved; verified backup has an explicit boundary. |

Test forced process termination, provider interruption, database busy/full errors and corrupted snapshot bytes. If an external reply existed but was not persisted before a crash, it is not reconstructable; record a retry instead of pretending identical recovery. Corrupted or missing canonical events stop recovery; a new model response cannot repair history. Test restoration into a separate directory and confirm its final checksum. SQLite's backup mechanism must be used consistently with its journaling mode.

## Browser-visible acceptance

At each milestone, the user follows the roadmap's procedure in the same application. Verify:

1. Coordinate selection after zoom/resize matches backend coordinates and inspector values.
2. Pause freezes committed ticks; step advances exactly once; resume keeps run identity; speed changes pacing only.
3. Pending inference and disconnected API are visible states, not fabricated motion.
4. Event entries show rule/actor, affected locations, costs and request links as applicable.
5. An organism overlay matches its actual supplied observation; the observer's global view is never reused as a prompt.
6. Historical mode uses the selected tick for grid, organism, memory and ancestry, with mutation controls disabled.
7. Browser close/reopen preserves the run; a deliberately extinct run is still extinct.
8. Generated messages are displayed as text; a message containing markup cannot execute scripts.

Use browser tests for these functional risks, plus manual inspection of legends, focus and readability. An attractive animation is not acceptance evidence of engine correctness.

## Testing behavior without prescribing it

Use two sets of evidence. **Mechanical fixtures** deliberately supply actions to cover reproduction, message exchange, packing and inheritance; their role is to prove permission and accounting. **Autonomous trials** let the model choose among available actions, with no outcome correction. Keep those datasets and labels separate.

For motivation and memory experiments, compare matched seeds/configs where only the tested input differs. Demonstrate the information path deterministically with a simple fixture policy, then report live action frequencies, invalid-action rates and variation across trials. A model ignoring a tendency may motivate a prompt experiment, but cannot justify an engine rule that forces a role. Test memory influence through retrieved IDs and changed inputs; do not require an organism to tell a convincing story about remembering.

Measure births per living-organism tick, generation depth, lifespan distribution, active-memory use, inherited-record retention, visited cells, modification count/duration, transfers, communication and action diversity. Report denominators, observation horizon, seeds, model/settings and failed/extinct runs. These are descriptive measures, not automatic proof of novelty, adaptation or improved fitness.

Reproduction acceptance has three levels: atomic mechanics pass; a recorded fixture demonstrates partial inheritance and persistent modification across generations; a live trial may demonstrate an autonomous second-generation birth with those properties. Only the third supports the stronger live proof-of-concept claim. A no-birth live trial remains a legitimate recorded result.

## Performance and long-running operation

No throughput or memory benchmarks were run for this plan. In M07A/M07B/M09 measure wall seconds/request, available prompt/output tokens, local model load time, queue length, due decisions/tick and UI response time. For API runs, also record network/rate-limit waits and separately sourced cost estimates; unavailable fields remain unknown. Keep ecological charges in separate units; GPU seconds and API charges are not billed as organism energy. Establish performance targets from the measured machine/endpoint and selected model, then record them.

Before MVP acceptance, propose these bounded campaigns:

- **Offline engine soak:** at least 100,000 ticks across fixed seeds with deterministic fixtures, including a reproducing lineage fixture and separate extinction fixtures. Fixture labels prevent scripted survival being mistaken for ecological evidence.
- **Recovery soak:** inject at least one failure at every crash-matrix boundary, including during birth/memory transmission. Compare committed histories and hashes.
- **Live observation:** run to extinction or a predeclared horizon, initially up to one hour of wall time. Repeat with additional seeds only under a declared experiment budget. An early extinction is recorded, not replaced with a longer-surviving run.

After warmup, pending inference must stay within configured concurrency, active memory/inboxes within caps, and in-memory event caches within explicit size limits. Track resident process memory separately from the model runtime and growing database. Verify old history is paginated from disk rather than retained in RAM. Disk growth is expected; report bytes per tick/decision and estimated archive capacity, then pause before storage exhaustion. Optimization must not erase meaningful history.

## Completion evidence

Each milestone's validation note records what was built, exact commands/versions, fixture IDs, automated results, manual observations and known limits. A failure should become a small reproducible fixture. Run relevant tests after changes; broaden testing when domain boundaries, persistence or recovery are affected. Do not rerun expensive live trials for a presentation-only edit.

MVP technical completion requires the invariant suite, exact replay, partial inheritance fixtures, resource balances, browser acceptance and crash/soak gates. The final experiment report separately states which autonomous outcomes actually occurred. Never mark extinction as a failed test merely because it is visually disappointing.
