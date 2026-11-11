# Incremental implementation roadmap

Status: updated 2026-11-10. **M01–M12 implementation approved; later milestones remain proposed.** M01–M12 code is present (see [M02](validation/M02.md)–[M12](validation/M12.md) validation). M09's live runs found no exchange at 32 × 32 or 16 × 16; M10's paired live runs await the user's go-ahead. M07B added, at the user's request, a dedicated Replicate adapter alongside the chat-completions adapter (Replicate's predictions API is not chat-completions compatible); see [validation](validation/M01.md) for automated results and manual acceptance status. Local inference will use MLX-LM; API-connected models are also required. M07 is split into M07A/M07B so both adapters have independent acceptance gates. Follow the [constitution](../AGENTS.md); parameter choices remain proposals in [DECISIONS.md](DECISIONS.md).

## How to use this plan

Every milestone leaves a runnable browser application. Complete its acceptance demonstration and save evidence before advancing. Each builds on completed prerequisites, but has a self-contained fixture and test procedure; it must not depend on favorable behavior from a later feature or live model. The first organisms already have finite lives and costs. Event history begins with the first mutable world, rather than being retrofitted after generations have passed.

File names below describe each milestone's intended scope; the M01 subset is now implemented. `backend/genesis/` is abbreviated `g/`, `web/src/` as `w/`. M01 documents exact startup/test commands in the project README; subsequent milestones keep those commands working. Fixture selection creates a **new, clearly labeled test run**, never edits an ongoing scientific run. Deterministic fixture policies and recorded model responses are diagnostics, not evidence of autonomous emergence. Add fixture controls only to development/test mode. M01 approval does not authorize implementing later milestones.

For each milestone, save run ID/config, screenshots or observer steps, test results and known limits in `docs/validation/Mxx.md`. Do not commit model weights or large run databases. An acceptance failure means fix the mechanism or explicitly revise the proposal, not tune organisms to produce a desired story.

| Milestone | Observable version | Boundary |
|---|---|---|
| 01 | Inspect a canonical grid in the browser | First implementation approval |
| 02 | Watch deterministic resource changes and their causes | Engine foundation |
| 03 | Restart and replay the changing world | Engine foundation |
| 04 | Inspect a finite-lived organism's local view and movement | Engine foundation |
| 05 | Extract resources and persistently reshape cells | Engine foundation |
| 06 | Inspect recorded proposals, rejection and request scheduling | Cognition contract |
| 07A | Observe one organism using local MLX-LM | Apple Silicon inference |
| 07B | Select and use an API-connected model | API inference |
| 08 | Inspect costly memories and recalled evidence | Cognition |
| 09 | Watch ten isolated organisms exchange resources/messages | Shared world |
| 10 | Compare motivation profiles without assigned roles | Experiment observability |
| 11 | Inspect evidence-linked, bounded reflections | Cognition |
| 12 | Trace a chosen birth and imperfect trait transmission | Lifecycle |
| 13 | Inspect selective inherited memories | Inheritance |
| 14 | Scrub full history and inspect ancestry | Observer experience |
| 15 | Recover from technical failures with history intact | Reliability |
| 16 | Run and archive a complete generational experiment | MVP 0.1 gate |
| 17 | Compare configurations and providers using existing adapters | Post-MVP, separate approval |

## M01 — Canonical grid and browser inspector

**A. Objective.** Establish the smallest visible slice: the backend owns coordinates and the browser displays them. Serves grid authority and display independence (§2, §9).

**B. Implementation scope.** Create `g/domain/state.py`, `g/domain/config.py`, `g/engine/world.py`, `g/api/app.py`, `g/api/routes.py`; `w/App.tsx`, `w/api.ts`, `w/renderers/CanvasGrid.ts`, `w/components/CellInspector.tsx`; minimal Python/frontend manifests and README startup instructions. Return a 32 × 32 immutable initial grid and run identity. Define coordinate conventions and the `GridFrame` contract. A small manifest/initial-state persistence record establishes run provenance. Controls show paused status; only cell selection and new static run work at this stage.

**C. Existing code reuse.** Use React, FastAPI and Pydantic directly. Adapt `Maze.access_tile`'s coordinate concept; no upstream source or frontend assets required.

**D. Visible outcome.** Open localhost, see 1,024 cells and a coordinate inspector. Refresh keeps the same run/grid. An unavailable backend displays a disconnected state rather than fictional data.

**E. Manual test.** 1. Launch documented commands. 2. Select `(0,0)`, `(31,31)` and an interior cell. 3. Resize the window and select again. 4. Refresh and compare run ID. 5. Stop backend and verify disconnection.

**F. Automated tests.** Coordinate/bounds tests; initial-state serialization round trip; API dimensions/ID contract; browser selection and resize hit-testing smoke test.

**G. Acceptance criteria.** Exactly 1,024 unique coordinates; inspector values match API; selection/resize changes no world state; no frontend simulation rules; fresh setup instructions work.

**H. Dependencies and risks.** No prior milestone. Confirm stack and coordinate defaults when approving M01. Current directory lacks Git metadata; establish version control as an explicit development setup task before recording engine revisions. Avoid rendering-framework scope growth.

**I. Out of scope.** Organisms, time progression, LLMs, resource rules, artwork, PixiJS, LED mapping and full history UI.

## M02 — Deterministic environment and resource accounting

**A. Objective.** Make the grid change under explicit, observable environmental rules (§1.2, §1.8).

**B. Implementation scope.** Add `g/engine/rules.py`, `g/engine/ledger.py`, `g/domain/events.py`, `g/runtime/runner.py`, `g/runtime/commands.py`, `g/persistence/sqlite.py` and initial schema. Add integer energy/material and packed-material fields (packing stays inactive); seeded initialization and proposed energy inflow. Atomically persist each tick's environmental deltas, ledger and committed state. Add run controls, color legend and event list in `w/components/`.

**C. Existing code reuse.** Standard seeded RNG and SQLite transactions; explicit step concept from `ReverieServer`, independently implemented without browser handshakes.

**D. Visible outcome.** Cells brighten on configured inflow ticks; clicking an event identifies changed cells, actual energy added and rule ID. Start/pause/resume/step/speed work.

**E. Manual test.** 1. Start seed 42. 2. Step through ticks 9–10. 3. Compare a cell and its event. 4. Pause and wait. 5. Run a second seed-42 world to tick 100 at another speed and compare hashes.

**F. Automated tests.** Seed repeatability; boundary and cap accounting; zero-inflow fixture; ledger balance; pause/step idempotency; atomic state/event commit. Test full energy cells receive zero credited inflow.

**G. Acceptance criteria.** Same seed/rules/tick yields equal state; paused logical time stays fixed; every changed value has a causal event; totals match ledger; speed changes only pacing.

**H. Dependencies and risks.** M01. Validate SQLite runtime before enabling WAL. Event schema must carry exact deltas, not just descriptive text. No biological viability assumption is needed yet.

**I. Out of scope.** Organisms, decay, diffusion, weather, complex cell schemas and resource balancing for population survival.

## M03 — Snapshots, restart and basic replay

**A. Objective.** Prove changing world history is reconstructable before adding cognition (§7, §8).

**B. Implementation scope.** Add `g/persistence/events.py`, `snapshots.py`, `replay.py`, versioned manifest and canonical hashing. Save periodic snapshots and restore on startup. Add a basic tick picker/read-only replay view, clearly separate from live mode. Persist RNG and scheduler information even while simple. Implement transaction uniqueness and replay without environmental recomputation.

**C. Existing code reuse.** SQLite backup/transactions and JSON serialization; adapt delta storage concepts from `compress`, not its movement-only format.

**D. Visible outcome.** Stop/restart the server and see the same run/tick. Inspect an earlier tick without changing live state.

**E. Manual test.** 1. Run to tick 150 and record hash. 2. Restart. 3. Seek tick 50 and inspect selected cells. 4. Return live. 5. Reconstruct tick 150 from initial state and from a snapshot; compare hashes.

**F. Automated tests.** Full replay versus snapshot-plus-tail; duplicate sequence refusal; crash before/after tick commit; unsupported schema rejection; corrupted snapshot detected with fallback to earlier valid snapshot.

**G. Acceptance criteria.** Exact state equality at requested ticks; restart adds no duplicate environmental effects; replay performs zero inference; history mode is read-only; unavailable historical data produces an explicit error.

**H. Dependencies and risks.** M02. Future state fields must enter snapshots/reducers and hashes. Do not confuse a screenshot or animated replay with authoritative reconstruction.

**I. Out of scope.** Advanced timelines, organism history, branching experiments, compression policy and complete disaster-recovery tooling.

## M04 — One finite-lived organism with local perception

**A. Objective.** Introduce embodiment, partial knowledge and mortality together (§1.1–3, §3–4).

**B. Implementation scope.** Add organism state, `g/engine/perception.py`, `g/engine/lifecycle.py`, basic `wait`/`move` validation and a deterministic test policy. Sample hidden lifespan; charge basal upkeep and movement. Add organism selection, local-view overlay, energy/age and death inspection. Record initial seed birth, actions, retirement of carried resources and extinction. Archive extinction; preserve grid. Test policy only receives observation DTOs.

**C. Existing code reuse.** Adapt attention/locality from `perceive`; use domain validation and existing event reducer. Do not import `Scratch` or grant maze access.

**D. Visible outcome.** A labeled test organism moves one cell at a time, shows a bounded local view, loses energy and eventually dies. Its history remains selectable.

**E. Manual test.** 1. Open the corner-perception fixture. 2. Compare overlay and supplied observation. 3. Step movement. 4. Run a starvation case and an age-limit case. 5. Confirm the dead organism never returns and the cell does not change on death.

**F. Automated tests.** Perception boundaries and hidden-field sentinel leakage; invalid move leaves action state unchanged; lifecycle thresholds; no actions after death; death retirement balances; extinction exactly once.

**G. Acceptance criteria.** No exact death deadline in observation; all living organisms bounded in lifespan; movement/upkeep accounted; dead IDs terminal; no implicit reseeding.

**H. Dependencies and risks.** M03. Tiny fixtures can predefine resource/lifespan parameters but must be labeled as tests. Distinguish independent upkeep from invalid-action effects.

**I. Out of scope.** LLMs, reproduction, social interaction, immortality toggles for scientific runs and movement paths using global knowledge.

## M05 — Costed extraction and durable cell modification

**A. Objective.** Prove organisms can change the environment inherited by later lives (§1.2, §1.4, §5).

**B. Implementation scope.** Extend `g/domain/actions.py`, validation, ledger and reducers with `extract`, `modify(pack/unpack)`. Add carried material/capacity and derived movement resistance. Development fixture policy sequences extraction and packing. Add cell change history, cost preview from actual action rules and before/after inspector fields. Baseline local observation remains part of upkeep; no redundant paid sensing action is needed.

**C. Existing code reuse.** Existing engine pipeline and Pydantic; adapt symbolic action/outcome separation from Generative Agents, independently implement physical effects.

**D. Visible outcome.** A cell accumulates packed material, changes appearance and costs more to enter. It remains changed after its maker dies and after restart.

**E. Manual test.** 1. Open material fixture. 2. Step extraction and packing. 3. Inspect material/energy deltas. 4. Attempt over-capacity and unaffordable actions. 5. Let organism die, restart and revisit cell.

**F. Automated tests.** Source/destination conservation; amounts/bounds/capacity; no partial mutation on failure; packing/unpacking round trip with energy dissipation; persistent modification across death/replay; movement-cost dependency.

**G. Acceptance criteria.** Every material unit remains accounted; invalid proposals produce diagnostics only; resource ledger closes; persistent modification links to a specific living actor's action.

**H. Dependencies and risks.** M04. Modification must alter effective geography, not merely a color. Default resistance/fees may be unhelpful behaviorally; that is not a technical defect.

**I. Out of scope.** Buildings, roads, ownership, damage/combat, decay, additional material types and any `build_city` action.

## M06 — Recorded cognition and deterministic scheduling

**A. Objective.** Establish the complete decision boundary before introducing model variability (§5–6, §8).

**B. Implementation scope.** Create `g/providers/base.py`, `recorded.py`, `g/cognition/policy.py`, `context.py`, action JSON schema and `g/runtime/scheduler.py`. Define provider-independent context, capability, response/usage and typed-error contracts for both local and HTTP-backed models. Persist request inputs/responses and consumption keys. Implement staged tick barrier, periodic/event triggers, cognition fees, bounded queue, conflict/rejection outcomes and pending-tick pause semantics. Add DecisionInspector with input, raw output, parsed proposal and outcome.

**C. Existing code reuse.** Pydantic strict validation; adapt `safe_generate_response` validation concept, with typed failures and no invented fallback action.

**D. Visible outcome.** Recorded responses act through exactly the future live-model contract. Malformed JSON and illegal proposals are visible; only valid actions mutate state.

**E. Manual test.** 1. Load recorded-valid fixture. 2. Inspect observation and resulting action. 3. Run invalid-type, out-of-range and unknown-action fixtures. 4. Pause with a delayed response pending. 5. Resume and verify one commit.

**F. Automated tests.** Schema fuzzing; request/hash mismatch; stale response; duplicate response; queue bounds; delay/pacing invariance; pending pause; no retries for semantic rejection; zero calls for nondue ticks.

**G. Acceptance criteria.** One consumed proposal per request; deterministic hashes for recorded runs; rejected proposal has no own state effects; no world/database object passed to cognition; no duplicate cost on replay/recovery.

**H. Dependencies and risks.** M05. Request durability and tick transactions must agree. Record technical versus ecological failures distinctly.

**I. Out of scope.** Real inference, autonomous multi-step plans, embeddings, model agents with tools and retry loops intended to force valid behavior.

## M07A — One organism using local MLX-LM

**A. Objective.** Replace recorded decisions with MLX-LM proposals on Apple Silicon macOS while preserving the same authority boundary (§5–6).

**B. Implementation scope.** Add `g/providers/mlx.py`, `mlx_worker.py`, versioned decision prompt, capability/configuration checks and inference health metrics. Use MLX-LM's Python API in a dedicated worker, one resident model, explicit context/output limits, one active request and typed timeout/crash handling. Supply the action schema in the prompt and validate returned JSON; native constrained decoding is optional only if verified. Record model revision/digest, runtime/template/settings and available usage. Add local model selection at new-run setup. Select/install a model only in this separately approved implementation stage; none is selected or installed by this plan.

**C. Existing code reuse.** Official MLX-LM loading/generation API and M06 contracts. Adapt prompt assembly patterns, not human-town prompts or legacy hosted-model calls.

**D. Visible outcome.** The inspector names the MLX-LM model/runtime and shows its bounded observation, raw response and actual engine outcome. UI stays responsive while logical time waits.

**E. Manual test.** 1. Configure a locally available approved MLX-compatible model. 2. Start one-organism run. 3. Inspect three decision attempts. 4. Terminate the inference worker during a request. 5. Observe technical pause, restore the worker and resume. 6. Replay committed history with inference disabled.

**F. Automated tests.** Mock worker fixtures for valid/invalid JSON, unavailable model, timeout/crash/truncation; bounded queue and cancellation; raw-output persistence; no double commit after retry; replay requires no model. Optional repeated live smoke tests are labeled nondeterministic.

**G. Acceptance criteria.** At least one live valid primitive is committed with a traceable request; every invalid output is recorded; unavailable provider changes no committed ecological time; no hidden fallback policy. Failure to produce valid actions blocks integration quality review, not a population rescue.

**H. Dependencies and risks.** M06; Apple Silicon/macOS compatibility, model availability/license and structured-output reliability. MLX-LM generation is not assumed to enforce the schema; persistent malformed output blocks acceptance for that model/adapter combination. No latency claim until measured.

**I. Out of scope.** API transport (M07B), ten-agent scaling, broad benchmarking, Ollama, reflection and guaranteed reproduction.

## M07B — Configurable API-connected models

**A. Objective.** Allow hosted or self-hosted model APIs through the same cognition boundary, so Genesis is not limited to locally loaded models. Serves the user's 2026-11-08 requirement and the constitution's provider separation (§6).

**B. Implementation scope.** Add `g/providers/http_chat.py`, `config.py`, backend connection-profile handling and a provider selector in new-run setup. Start with a compatible chat-completions endpoint: configurable base URL, model ID, optional API-key environment-variable reference, capabilities, context/output caps, timeout and request/token budget. Normalize responses/usage and typed errors, apply bounded transient retries, and keep secrets server-side. Record provider identity without credentials. API-only runs must not require installed MLX or local model weights. Freeze provider selection per run; no automatic fallback.

**C. Existing code reuse.** Maintained asynchronous HTTP client, M06 provider contract and the same strict schema/engine validator used by M07A. Use native schema constraints only when the selected endpoint supports them; otherwise validate prompted JSON. Dedicated adapters can later support noncompatible protocols.

**D. Visible outcome.** Choose Local MLX-LM or API connection for a new run, identify the model/endpoint, inspect connection status and see accepted/rejected decisions with available token/time usage. Remote operation is clearly labeled.

**E. Manual test.** 1. Configure an approved API endpoint/model and backend credential reference. 2. Select it for a new one-organism run. 3. Inspect three attempts and confirm no local model was loaded. 4. Test invalid credentials, rate limiting or interruption with a controlled endpoint. 5. Confirm technical pause without provider switching. 6. Replay with the network disconnected and credential unavailable.

**F. Automated tests.** Local HTTP fixtures for compatible requests, missing model-list support, optional native schema capability, malformed output, 401/403/429/5xx, timeout and unknown usage; credential redaction; request/token limits; no duplicate effects after ambiguous retry; no cross-context leakage; API-only startup without MLX; provider-independent recorded replay. Browser test covers provider selection and its displayed identity.

**G. Acceptance criteria.** A configured compatible API produces at least one traceable valid action through the unchanged validator. The adapter passes local HTTP contract fixtures; a live smoke test verifies the specific selected endpoint before claiming support for it. Missing credentials leave that live check explicitly unverified. No secret appears in browser data/history, no silent local/remote fallback occurs, and replay requires no endpoint. This gate delivers API connectivity before MVP; M17 is not a prerequisite.

**H. Dependencies and risks.** M06 contract; delivered after M07A for the local-first reference path, but independently testable without MLX. A chosen endpoint/credentials are needed for its live smoke check during implementation. API dialects, rate limits, costs and changing hosted aliases differ; do not promise universal compatibility. Technical budgets pause runs rather than altering ecology.

**I. Out of scope.** Supporting every vendor protocol immediately, mixed-model populations, mid-run provider swapping, public hosting of Genesis, comparative analytics and automatic paid fallback.

## M08 — Costly memory and bounded recall

**A. Objective.** Give individual experience a persistent, inspectable path into future decisions (§1.2–3, §6).

**B. Implementation scope.** Add `g/cognition/memory.py`, `retrieval.py`; active memory membership/content events, storage/upkeep costs, deterministic retention/eviction, sparse remembered coordinates and bounded prompt assembly. Add MemoryInspector with provenance, age, active/forgotten status and retrieval scores. Historical archive remains complete.

**C. Existing code reuse.** Adapt `ConceptNode`, keyword indexes and decomposed retrieval scoring. Independently handle empty/equal scores, elapsed time and ties; no vector database.

**D. Visible outcome.** An organism remembers a past cell/outcome, and the next prompt visibly includes selected evidence even after it moves away. Forgotten records remain available to the observer only.

**E. Manual test.** 1. Run visit-and-return fixture. 2. Inspect observation and remembered tick. 3. Move outside visibility and verify no remote refresh. 4. Inspect recalled IDs in next input. 5. Fill the memory budget, observe costs/eviction, then restart.

**F. Automated tests.** Agent isolation; recall ordering and empty case; no stale-memory refresh; capacity/UTF-8 limits; affordability; eviction reconstruction; restart equality; a recorded memory-sensitive policy chooses differently with/without a specific recalled record.

**G. Acceptance criteria.** Storage and maintenance charged exactly; active memory within limits; deterministic recall reproducible; private data never leaks. A live A/B observation documents influence or lack of influence without requiring the model to “learn correctly.”

**H. Dependencies and risks.** M07A/M07B (offline acceptance still uses M06). Cost/retention defaults can dominate behavior and must remain explicit.

**I. Out of scope.** Reflection, semantic embeddings, global shared memory, weight training and claims of adaptation.

## M09 — Ten organisms and local exchange

**A. Objective.** Establish isolated cognition in a shared finite world, including actual communication and resource exchange (§3, §5–6).

**B. Implementation scope.** Extend scheduler to configurable 8–12 seed organisms, rotated resolution order, local inbox delivery and bounded population-dependent queue. Add `transfer` and `communicate`, capacity/range checks, message costs and next-tick visibility. Add population list, selected-organism overlay and per-actor event filters. Keep a single resident model.

**C. Existing code reuse.** Existing provider/engine; adapt conversation cooldown and memory provenance ideas. Do not copy `agent_chat_v2` or joint participant mutation.

**D. Visible outcome.** Ten organisms occupy and alter one grid. Resource transfer has matching debits/credits. Messages appear only for intended local recipients and may or may not receive replies.

**E. Manual test.** 1. Run two-neighbor transfer/message fixture. 2. Inspect both ledgers and delayed inbox delivery. 3. Repeat outside range. 4. Run ten local-model organisms. 5. Pause and inspect independent request contexts and queue order.

**F. Automated tests.** Two actors contest one cell/resource; full recipient/inbox; receiver death; adjacent-only delivery; private-memory sentinel isolation; ordering independent of response arrival; fairness over repeated rotations; bounded queue.

**G. Acceptance criteria.** No overlapping living positions; no duplicated resources/messages; one model provider serves isolated contexts; each due survivor gets an opportunity; actual exchanges demonstrated by fixtures, with live behavior reported separately.

**H. Dependencies and risks.** M08. Model latency scales with due requests; no real-time guarantee. Population capacity follows available cells, not a target maintained by the engine.

**I. Out of scope.** Global chat, enforced cooperation, relationships as mandatory stats, synchronous conversation scenes and distributed inference.

## M10 — Motivation profiles and behavioral observation

**A. Objective.** Make competing starting tendencies visible and test their effect without imposing roles (§3, §11.4–6).

**B. Implementation scope.** Add typed tendency configuration, seeded varied starting vectors and prompt integration if not already present. Add profile inspector and simple per-organism summaries: actions attempted/accepted, visited cells, resources spent, messages and modifications. Add paired test-run configuration differing only in tendencies.

**C. Existing code reuse.** Adapt inspectable scratch identity/context assembly, replacing biography with bounded weights. Reuse event-derived statistics; no external analytics framework.

**D. Visible outcome.** Select organisms to compare weights, supplied prompts and observed behavior. The UI can show different action distributions when they occur, or honestly show no clear effect.

**E. Manual test.** 1. Open two matched fixtures with different tendency vectors. 2. Compare prompt fields. 3. Confirm a recorded tendency-sensitive policy changes its proposal. 4. Run several paired live seeds with fixed model/settings. 5. Inspect counts and document inconclusive cases.

**F. Automated tests.** Seeded profile diversity; range validation; only intended prompt fields differ; weights do not bypass engine constraints; statistics match known event fixture totals.

**G. Acceptance criteria.** All initial organisms have inspectable varied tendencies; deterministic fixture proves the information path; live results and sample sizes are recorded. Statistically distinct behavior is a research question, not a required outcome or a justification for scripting roles.

**H. Dependencies and risks.** M09. Prompt wording can overwhelm numeric tendencies. Review evidence before adding complexity or changing defaults.

**I. Out of scope.** Fixed professions, mandatory objectives, automatic personality optimization, reinforcement learning and within-life tendency drift.

## M11 — Bounded reflection with evidence

**A. Objective.** Permit interpretations of experience while preserving underlying observations (§6–7).

**B. Implementation scope.** Add `g/cognition/reflection.py`, a separate prompt/schema and trigger/cooldown/cost handling. At most one request per reflection opportunity; at most two stored reflections. Validate evidence IDs against accessible memories. Add evidence links and a visual distinction between observation, claim and reflection.

**C. Existing code reuse.** Adapt `reflection_trigger` and `generate_insights_and_evidence`; retain provenance, remove multi-stage inference cascade.

**D. Visible outcome.** After the configured threshold, a reflection appears with cost, source records and request metadata. It can later be recalled as an interpretation.

**E. Manual test.** 1. Run salient-event fixture below threshold. 2. Cross threshold and inspect request. 3. Open cited evidence. 4. Try an output citing nonexistent/private memory. 5. Continue ticks to verify cooldown and budget.

**F. Automated tests.** Threshold/cooldown boundaries; no empty-evidence trigger; invalid references; cost/capacity failure; no inference on every physical tick; raw facts remain unchanged; restart preserves trigger state.

**G. Acceptance criteria.** Reflections are bounded and traceable; invalid references have no memory effect; no source history overwritten; call budget measurable and enforced.

**H. Dependencies and risks.** M10. Reflection can hallucinate or repeat; those contents are not automatically engine defects. Invalid provenance is a technical boundary violation.

**I. Out of scope.** Weight updates, mandatory insight generation, recursive reflection chains and numerical tendency changes.

## M12 — Agent-initiated birth and imperfect traits

**A. Objective.** Make second-generation birth possible through a validated organism choice (§4–5).

**B. Implementation scope.** Add reproduction proposal and `g/engine/inheritance.py`; parent energy/material endowment, fee, retained-energy threshold, adjacent placement, cooldown, unique child identity, fresh hidden lifespan and partial varied traits. Log all draws and before/after values. Add birth event and parent/child links. Child first acts next tick. Minimal imperfect trait inheritance ships now; richer memory transmission follows.

**C. Existing code reuse.** Existing validator/ledger/seeded RNG; no Generative Agents lifecycle component fits this requirement.

**D. Visible outcome.** An accepted reproduction proposal produces a distinct child; parent loses exact costs. Rejection explains insufficient energy, occupancy or cooldown. No births follow from low population alone.

**E. Manual test.** 1. Run recorded birth fixture and inspect submitted proposal. 2. Inspect parent debit, child ID, generation and trait variation. 3. Test occupied/poor-parent fixtures. 4. Let parent die. 5. Inspect surviving child and unchanged ancestry.

**F. Automated tests.** Atomic birth and capacity checks; concurrent placement conflict; duplicate request prevention; next-tick eligibility; parent-death boundary; independent child state; reproducible mutation masks/draws; no auto-reseed on extinction.

**G. Acceptance criteria.** Fixture demonstrates valid and invalid birth paths with exact ledger balance; lineage and traits replay identically. Live model trials expose reproduction but need not produce a birth for this mechanics gate.

**H. Dependencies and risks.** M11. Costs may make births rare or impossible in some conditions. Review mathematical feasibility using a fixture, without rescuing failed live populations.

**I. Out of scope.** Sexual reproduction, spontaneous births, full parent-context cloning, population targets and claims that inherited traits improve fitness.

## M13 — Selective memory inheritance

**A. Objective.** Allow partial transmission of experience without continuing a parent instance (§1.5, §4, §6).

**B. Implementation scope.** Extend reproduction with at most two selected active memory IDs, explicit transmission/storage fees, seeded retention/drop and truncation. Store new child records with source/ancestor metadata; omit stale live references and plans. Add an inheritance comparison showing selected, retained, omitted and altered data. Preserve private data boundaries.

**C. Existing code reuse.** Memory provenance patterns and existing ledger/reproduction pipeline; no need for a new lineage framework.

**D. Visible outcome.** A child's inspector distinguishes inherited claims from its own observations. Some fixtures transmit none; others transmit a bounded subset. Parent memories remain unchanged.

**E. Manual test.** 1. Run fixture with more parent records than allowed. 2. Select allowed IDs in the recorded proposal. 3. Inspect retained/dropped outcomes for known seeds. 4. Change child memory through experience. 5. Confirm parent history and unrelated organisms remain unaffected.

**F. Automated tests.** Unknown/nonowned/forgotten IDs; caps and byte limits; seeded dropout; fee/capacity atomicity; no aliasing; zero-inheritance case; dead ancestor records accessible only through permitted copied content; exact replay.

**G. Acceptance criteria.** Inheritance is partial and imperfect in controlled cases; every child record identifies origin; copied text fits costs/limits; no lifespan/PRNG/private sibling data leaks.

**H. Dependencies and risks.** M12 and M08. Inherited misinformation is allowed; treating it as verified present-world truth is not.

**I. Out of scope.** Full autobiography transfer, reincarnation identity semantics, learned-weight inheritance and automatic “best memory” selection by omniscient engine.

## M14 — Historical inspection and genealogy

**A. Objective.** Make causal archaeology usable across lives (§7, §9).

**B. Implementation scope.** Extend replay/read projections for all organism, memory, birth and death fields. Add `w/components/HistoryPanel.tsx`, `GenealogyPanel.tsx`, event-to-decision navigation, cell diff view and live/history mode separation. Paginate history and load snapshots on demand. Show parent/child lists or a small tree; no elaborate graph system required.

**C. Existing code reuse.** Adapt persona inspection/demo interactions from Generative Agents; reuse M03 reducers. Do not reuse final-memory-as-historical-memory behavior.

**D. Visible outcome.** Scrub before/after a modification, death or birth. Inspect precisely the memories/tendencies available at that tick, and follow the causal decision and ancestry.

**E. Manual test.** 1. Open a fixture spanning two generations. 2. Seek before birth: child absent. 3. Seek after birth: child present. 4. Compare modified cell before/after maker's death. 5. Navigate inherited memory to its source. 6. Return live without changing it.

**F. Automated tests.** Historical membership boundaries; full versus snapshot replay at each fixture tick; no future data in historical view; event pagination ordering; browser mode controls and cross-links; missing/corrupt snapshot handling.

**G. Acceptance criteria.** All displayed historical data comes from the selected sequence; zero inference on replay; causal navigation works for every fixture change; live state hash unchanged by inspection.

**H. Dependencies and risks.** M13. Schema evolution and incomplete historical cognition records are primary risks, addressed by earlier event-first milestones.

**I. Out of scope.** Editing the past, alternate-history generation, visual art direction and cross-run statistical conclusions.

## M15 — Technical health and safe recovery

**A. Objective.** Recover technical operation while leaving ecological outcomes intact (§8).

**B. Implementation scope.** Add `g/ops/health.py`, `metrics.py`, `recovery.py`, backup/restore verification, disk-space guard, bounded caches and failure-injection harness. Extend minimal M03 recovery to pending model work, memory and births. Show provider status, last durable tick, queue length, invariant failures and recovery events. Technical alerts never modify ecological rules.

**C. Existing code reuse.** SQLite integrity/backup mechanisms, standard logging and existing request/event contracts. No self-repair code generator or infrastructure platform.

**D. Visible outcome.** Kill/restart at a pending birth or inference and recover a coherent run with no duplicated child/debit. Low storage or corrupted history produces a clear technical pause.

**E. Manual test.** 1. Start recovery fixture. 2. Kill process before and after a commit boundary. 3. Restart and compare hash/event counts. 4. Stop provider and restore it. 5. Restore a verified backup to a separate path. 6. Open an extinct run and verify recovery does not reseed it.

**F. Automated tests.** Crash matrix; duplicate consumption; disk-full/locked DB; corrupt snapshot/event; backup plus tail integrity; queue/cache bounds; simulated long-run plateau of resident state; no ecological rescue under any fault handler.

**G. Acceptance criteria.** Zero loss/duplication of committed history in injected crashes; ambiguous/corrupt history fails closed; verified backup reconstructs its recorded boundary; 100,000-tick offline soak passes invariants across documented fixtures.

**H. Dependencies and risks.** M14. Long-lived test populations need explicit reproduction fixtures; production extinction cannot be bypassed to keep the test running. Archive disk grows intentionally; resident memory should not grow with all history.

**I. Out of scope.** Autonomous code deployment, distributed failover, ecological healing and deleting history to meet memory targets.

## M16 — Complete generational experiment and MVP gate

**A. Objective.** Demonstrate the full experiment is possible and auditable, without requiring progress in every run (§10–11).

**B. Implementation scope.** Integrate a frozen MVP ruleset/config, run manifests and a small experiment report/export in `g/ops/` and the web archive list. Make only fixes justified by failed acceptance evidence. Run an independent live local MLX-LM trial from roughly ten organisms, without human action injection. Keep M07B API connection/contract acceptance in the MVP checklist; an API trial can be a separately labeled run. Archive completion/extinction and document resource, inference and lineage measures.

**C. Existing code reuse.** Entire completed core; event-derived summaries and existing export/backup facilities. No new simulation framework.

**D. Visible outcome.** A complete inspectable run can include multiple generations and lasting cell changes, or stagnation/extinction. The archive distinguishes live trials from mechanical test fixtures.

**E. Manual test.** 1. Choose a frozen seed/config/model and start a live run. 2. Observe without steering organisms. 3. Follow any modification and reproduction attempts to their inputs/outcomes. 4. At extinction or declared observation horizon, archive and replay. 5. Inspect controlled proof fixtures separately.

**F. Automated tests.** Full contract/invariant suite; multi-generation fixture with partial inheritance and persistent modification; extinction fixture; restart/replay checks; report counts equal source events; bounded inference/memory metrics.

**G. Acceptance criteria.** All M01–M15 mechanisms pass. A controlled fixture proves second-generation birth/inheritance/modification is feasible. A live trial is archived with complete causal data and an honest outcome. For the stronger artistic proof-of-concept claim, obtain a recorded live, organism-initiated second-generation birth with partial inheritance and lasting modification; if absent, explicitly mark that empirical proof unobserved, not “achieved” through the fixture. No requirement that every trial succeeds.

**H. Dependencies and risks.** M15. No emergent outcome is guaranteed. Further trials or changed conditions require new versioned runs, never concealed rescue or selective reporting.

**I. Out of scope.** Claims of consciousness, measured weight evolution, forced civilization, public input, LED installation and mandatory indefinite survival.

## M17 — Controlled comparisons using existing model adapters (post-MVP)

**A. Objective.** Compare explicit hypotheses after the core is trustworthy (§6, §11–12).

**B. Implementation scope.** Add a small immutable experiment matrix, configuration diff and side-by-side reports using the existing MLX-LM and API adapters from M07A/M07B. Compare one variable at a time with multiple seeds. Keep model identity, prompt, available revision/quantization, context and sampling visible, explicitly marking unknown hosted-model details. Saved run archives remain independent. New native API protocols require their own adapter contract tests if an experiment needs them.

**C. Existing code reuse.** Existing local/API providers, common contract and event summaries. No experiment orchestration service required; basic model API connectivity is already an MVP capability.

**D. Visible outcome.** Compare survival curves, accepted/invalid actions, offspring counts, inherited-memory retention, visited cells, material changes, inference tokens/time and technical failures between labeled runs.

**E. Manual test.** 1. Duplicate a config into a new experiment. 2. Change one parameter/provider. 3. Review diff before starting. 4. Run matched seeds and open reports. 5. Reopen source events behind a surprising metric.

**F. Automated tests.** Manifest immutability, config diff accuracy, provider contract parity, no cross-run contamination, exact metric fixture values and explicit inclusion of extinct/failed runs.

**G. Acceptance criteria.** Results can be reproduced from archived decisions; comparisons disclose confounders and sample size; no claimed adaptation from a single attractive run. Provider speed/accuracy conclusions require measured evidence.

**H. Dependencies and risks.** M16 plus separate approval. Model formats/templates can confound comparisons. Parameter sweeps can consume substantial time and storage; predeclare their bounds.

**I. Out of scope.** Automatic optimization toward survival, mixed-model ecosystems by default, public interventions, LED hardware and new local-law mechanics. Each needs its own future hypothesis and milestone proposal.

## Approval and completion boundary

M01's concrete scope is approved and implemented: canonical grid, backend API, browser cell inspector and minimal run provenance. Complete its manual acceptance using the README and validation note before requesting the next milestone. This approval does not cover all eighteen milestone gates (including M07A/M07B). Stop at each requested milestone boundary and report the working result, evidence and unresolved choices.
