# Decision register

Version: 0.5, 2026-11-11. This file records requirements and proposals. **The user approved implementation and testing of M01–M10, one milestone at a time (see the planning record).** Local MLX-LM and API-support choices remain user-directed; later milestones await approval. The [constitution](../AGENTS.md) remains unchanged. Detailed mechanisms are in [ARCHITECTURE.md](ARCHITECTURE.md), with delivery gates in [ROADMAP.md](ROADMAP.md).

## Status definitions

- **Confirmed requirement:** explicitly established by the constitution or this planning request.
- **Proposed — awaiting approval:** recommendation to review when approving the relevant milestone. It is not a permanent project rule.
- **Assumption:** provisional input used to make the plan concrete; verify before relying on it.
- **Open:** needs evidence or a deliberate design choice; a reversible default is offered where useful.
- **Deferred:** deliberately excluded from MVP, not implicitly authorized future work.

Once an implementation choice is accepted, append its approval date, rationale and evidence here. Supersede earlier entries rather than silently rewriting their status. Changes to foundational principles require an explicitly approved, versioned constitutional revision; ordinary experimental parameter changes use a new rules/config version and run ID.

## Confirmed requirements

| ID | Requirement | Source / implication |
|---|---|---|
| C01 | Canonical mutable 2D cell grid; rendering independent | Constitution §2, §9. No renderer-owned movement or physics. |
| C02 | Finite, partly unknowable lifespan; terminal death | §1, §4. No dead instance resumes; model cannot see exact sampled death time. |
| C03 | Finite resources, costed actions/communication/memory/modification | §1, §5–6. No resource creation by language. |
| C04 | Partial observations and independent organism identity/memory | §1, §3, §6. Shared model weights do not imply shared personal context. |
| C05 | Engine validates all effects; invalid actions do not mutate state | §5. A valid JSON response is only a proposal. |
| C06 | Voluntary reproduction, partial imperfect inheritance, no target population | §1, §4. Extinction archived; a new seed starts a new run. |
| C07 | Stable foundation, no arbitrary host execution or automatic code deployment | §1, §5, §8. Local conventions cannot override invariants. |
| C08 | Durable causal events, configuration, identities, snapshots and replay | §7, §10. Recorded decisions/effects, not model regeneration. |
| C09 | Technical recovery separate from ecological outcomes | §8. No silent rescues, material cleanup or success-oriented intervention. |
| C10 | Browser grid/inspector early, roughly ten organisms, shared local model with adapter | §6, §9–10 and planning request. Exact sizes/technology remain provisional. |
| C11 | Research actual Generative Agents code and prefer practical reuse | Planning request. Selective adaptation recommended in RESEARCH.md. |
| C12 | Original research stage was documentation only; M01 implementation was subsequently authorized | Initial planning restriction was superseded for M01 by explicit user approval on 2026-11-08. No later milestones, model downloads or AGENTS.md edits are authorized. |
| C13 | Use an Apple Silicon-oriented local runtime; exclude Ollama | User correction, 2026-11-08. Interpreting “molx” as MLX; plan uses MLX-LM for local LLM inference. Exact model and transport details remain proposals. |
| C14 | Support models connected through an API, not only locally loaded models | User correction, 2026-11-08. API connectivity is part of MVP, via the same cognition/action boundary; first reference experiment remains local as required by §6. |

## Technical choices — M01 accepted; later scope proposed

M01 approval accepts P01/P02 for this milestone: Python/FastAPI and React/TypeScript with Canvas 2D. Its minimal provenance store uses SQLite rollback journaling; broader P03 event/history work remains for M02/M03. Planned future paths and parameters are not implied approvals.

| ID / review gate | Proposal and rationale | Alternative / reversal cost |
|---|---|---|
| P01 / M01 | Python domain + FastAPI API; typed data and small modules | TypeScript could unify languages, but Python suits model tooling. Medium migration cost; keep domain/API contracts narrow. |
| P02 / M01 | React/TypeScript with Canvas 2D for 1,024 cells | PixiJS is reasonable but unnecessary initially. Low replacement cost with `GridFrame`; do not bind physics to either. |
| P03 / M02–03 | Local SQLite, one writer, append-only events and rebuildable projections | PostgreSQL only if actual multi-user/writer scale needs it. Event schema is more costly to change than DB transport. Check fixed SQLite runtime before WAL. |
| P04 / M02–06 | Integer logical ticks, atomic tick commits, one simulation owner, barrier during inference | Asynchronous ecological time would make model latency affect survival. Revisit only as an explicit experiment; medium/high behavioral impact. |
| P05 / M04–05 | Integer energy plus material; packing affects movement cost | Rich chemistry/structures deferred. Low configuration cost, medium schema cost. Avoid introducing an “information substance” before needed. |
| P06 / M06 | Strict schema + semantic validation; no action substitution on invalid output | Corrective retries change opportunity and cost semantics. Transport retry only, bounded and logged. |
| P07 / superseded 2026-11-08 | Original recommendation: Ollama first, MLX later | Superseded by C13/C14 and P15/P16 below. Retained as decision history only; Ollama is not a planned dependency. |
| P08 / M08 | Bounded keyword/recency/salience retrieval before embeddings | Semantic embeddings only after an observed retrieval limitation. Low extension cost; embeddings would add model/cache/version obligations. **Accepted 2026-11-10 for M08** as `recall-v1` (unweighted recency + Jaccard keyword overlap + fixed salience, top 8 within 2,048 bytes). |
| P09 / M09 | Same observation boundary per tick, rotated deterministic action resolution | Simultaneous auctions/reservations are more complex. Ordering is an ecological assumption and must be logged. |
| P10 / M11 | Threshold/cooldown reflection with evidence; one bounded request | No reflection is a useful later comparison; routine reflection every tick rejected on cost/interpretability grounds. |
| P11 / M12–13 | Asexual adjacent birth, parent-funded endowment, partial trait/memory inheritance | Sexual reproduction and spontaneous births deferred. Fresh identity and imperfect inheritance remain mandatory. |
| P12 / M14–15 | Full state reconstruction, technical fault pauses, verified backups | No “replay” using newly sampled decisions. Corrupt history stops recovery instead of being invented. |
| P13 / all | Use maintained infrastructure libraries directly; selectively adapt cognition ideas | Forking Generative Agents is rejected as a recommendation, not forbidden forever. Reconsider only with evidence that extraction is cheaper and constitution-compatible. |
| P14 / M16 | Separate mechanical MVP completion from stronger live proof-of-concept evidence | A fixture proves possibility; it cannot prove autonomous success in a live trial. No “evolution achieved” milestone label. |
| P15 / M07A | MLX-LM Python API in a dedicated local inference worker; one resident model | Runtime family is user-directed; subprocess integration is proposed. Do not assume native JSON-schema decoding; validate every output. |
| P16 / M07B | Configurable backend HTTP adapter for compatible chat-completion APIs; endpoint, model ID and credential reference | API support is user-directed; initial wire protocol is proposed. Add dedicated adapters for other protocols when needed. Model/provider choice is explicit per run, with no automatic local/remote fallback. **2026-11-09:** a dedicated Replicate (predictions API) adapter was added in M07B at the user's request; profiles live in `config/providers.toml` without secrets. **2026-11-10 (user decision):** the strict parser accepts a reply that is *exactly one* ```json (or bare ```) code fence around the JSON, because DeepSeek-V3 on Replicate fences every reply despite the prompt; nothing else is repaired, raw output stays verbatim, and such decisions carry the note "accepted after removing one ```json code fence". Earlier recorded verdicts are unchanged. |

## Proposed experiment defaults — not constitutional amendments

These values make implementation testable. Review at the relevant milestone, then freeze them in a ruleset for each run. Do not tune an ongoing run to preserve its organisms.

| Parameter | Initial recommendation | First review / reason |
|---|---|---|
| Grid / occupancy | 32 × 32, bounded, cardinal moves, one living organism/cell | M01/M04. Simple conflicts and limits; toroidal geometry is open. |
| Seed population | 10; smaller explicitly labeled fixtures | M09. No target population maintenance. |
| Energy | Cell cap 100; organism cap 100, initial 60; +1/cell each tenth tick up to cap | M02/M04. Explicit environmental source; no population feedback. |
| Material | Seeded finite loose material, no renewal; organism capacity 20; packed cell cap 8 | M05. Exact initial distribution to choose with fixture feasibility evidence. |
| Resistance | Move entry fee `1 + floor(packed_material / 2)` | M05. Minimal meaningful change to geography, without prescribed structure names. |
| Lifespan | Hidden engine sample 160–240 ticks; organism sees age but not deadline | M04. Deliberately short laboratory lives; may need longer observation windows later. |
| Upkeep / cognition | 1 energy/tick; 1 per due cognition opportunity; routine sensing included | M04/M06. Separate logged charges, never invalid-action penalties. |
| Sensing | Manhattan radius 2; ≤8 local event summaries; no occlusion | M04. Geometric edge tests; stale memories remain stale. |
| Decision cadence | Every four ticks or coalesced local trigger, minimum gap one tick | M06. Physical steps need no inference; no unbounded event cascades. |
| Memory | ≤64 records, ≤16 KiB text, ≤256 bytes per record; top eight retrieved | M08. Costs and deterministic active-set eviction; permanent history preserved. |
| Memory charges | 1 energy/started KiB written; 1/started KiB active text each tenth tick | M08. Cheap to explain; viability and dominant effects remain open. |
| Messaging | Adjacent unicast; ≤256 bytes; inbox ≤8 messages; oldest messages expire after 20 ticks | M09. No forced recipient response; inbox expiration logged and historical copies retained. |
| Tendencies | Six bounded weights, varied at seeding; stable within a life initially | M10. Competing dispositions, no human roles. |
| Reflection | ≥8 new salient memories, ≥20-tick cooldown; ≤2 outputs; fee 2 plus storage | M11. Uses a decision opportunity to bound model calls. |
| Reproduction | Fee 10; ≥20 energy endowment; retain ≥10; cooldown 20; adjacent vacant target | M12. Demonstrate affordability mathematically and by fixture, not by automatic subsidy. |
| Trait inheritance | Seeded partial mask; small clipped perturbation; fresh values for remainder | M12. Exact mask distribution and perturbation magnitude remain open. |
| Memory inheritance | Parent chooses ≤2 owned active IDs; independent 50% retention; truncation/costs | M13. May transmit zero. Exact per-copy transmission cost to approve with M13. |
| Snapshot cadence | Every 100 ticks and graceful pause/close | M03. Adjust by measured replay/storage cost, not biological outcome. |
| Inference | Local MLX-LM by default; API profile selectable; one concurrent request, roughly 4K context/256 output tokens | M07A/M07B. One resident model for local runs only. Verify capabilities/template; no hardware or cross-provider output guarantee. |
| API profile | Backend-only base URL, model ID, optional API-key environment-variable reference, timeouts and request/token limits | M07B. Remote HTTPS, loopback HTTP for local development; credentials never stored in run history. Exact provider and budgets remain open. |

The action-specific fees and limits are collected in [ARCHITECTURE.md](ARCHITECTURE.md). If a proposed value is revised before implementation, update both documents and record the rationale. A run manifest is the authority for an executed experiment's values.

## Assumptions to verify

| ID | Assumption | Verification |
|---|---|---|
| A01 | Target is an Apple Silicon MacBook Pro with 128 GB unified memory | Supplied by user. Exact chip, free memory, thermals and installed tooling not inspected. Measure only when implementation is approved. |
| A02 | Engine, UI and history run locally for one user; inference can be local or API-connected | A hosted inference connection does not imply public hosting of Genesis. Confirm deployment intent before exposing the observer/API. |
| A03 | Ten bounded sequential requests are acceptable even if a round takes seconds or longer | Validate measured latency and user experience in M07A/M07B/M09; visual frame rate is independent. |
| A04 | Small pure-Python grid calculations are inexpensive compared with inference | Reasonable for 1,024 cells, unbenchmarked. Profile before NumPy/GPU/parallel engine work. |
| A05 | Active organism state can stay bounded while archives grow on disk | Verify M15 soak, pagination, event sizes and retention budgets. Never confuse bounded RAM with deletion of history. |

## Open questions and decision triggers

| ID | Question | Reversible default / evidence needed |
|---|---|---|
| O01 | Which energy inflow and seed distributions permit meaningful choices? | Published simple source and finite material. Test viability range, depletion and extinction without optimizing for a particular population history. |
| O02 | Is packed material sufficient as a persistent environmental primitive? | One movement-resistance effect first. Add storage/signal properties only when a concrete experiment needs them. |
| O03 | Are time, memory and action costs too entangled? | Keep distinct ledger categories and matched experimental configs. Do not infer “better cognition” from survival alone. |
| O04 | Should cognition latency ever count as ecological time? | No for MVP. A later latency-sensitive run must be labeled and compared fairly across providers. |
| O05 | Should corpses return resources? | Retire carried resources without changing cells. Recycling would alter ecology and needs an explicit design decision consistent with §1.4. |
| O06 | How should tendencies change through experience? | Stable weights plus evolving memories first; later bounded updates require evidence, provenance and comparison with fixed weights. |
| O07 | What is inherited as a strategy? | Plain bounded memory/reflection text initially; no executable programs. Evaluate retention/use without claiming weight evolution. |
| O08 | What constitutes useful novelty or adaptation? | Report action diversity, spatial coverage and persistence descriptively. Adaptation needs repeated controlled comparisons and a declared operational measure; language fluency is insufficient. |
| O09 | Can communication create local protocols? | Observe repeated messages/actions first. No engine-recognized laws, offices or privileges. Any enforceable local-rule primitive needs a separate proposal and invariant boundary. |
| O10 | Which exact local model/quantization/license and which API provider/model? | Select during M07A/M07B for action validity, latency, available capabilities and permitted use. Record hosted aliases/revisions honestly when weight digests are unavailable. No model chosen solely because 128 GB can hold it. |
| O11 | What long-run archive budget is acceptable? | Measure bytes/tick and bytes/request. Prefer lossless archival; any lossy retention policy needs explicit approval and must preserve meaningful causality. |
| O12 | What empirical evidence is enough for the first artistic proof? | M16 separates mechanism feasibility from an actual autonomous second-generation birth/inheritance/modification record. Archive unsuccessful trials as well. |
| O13 | Does the chosen MLX-LM/model combination need constrained decoding? | Begin with schema prompting and strict rejection; measure schema adherence in M07A. Add a compatible decoding mechanism only if evidence justifies it; no unverified `response_format` promise. |
| O14 | Which API budgets and endpoint protocols are needed first? | M07B starts with one tested compatible chat endpoint and configurable request/token limits. Cost estimates need a recorded pricing source; unknown usage is not zero. Additional native protocols remain adapter extensions. |

## Deferred capabilities

Comparative experiment tooling (M17); mixed-model populations and mid-run provider changes; semantic embeddings; changing tendency weights; public participation; physical LED transport/mapping implementation; 3D; complex agent-authored local rules; unsandboxed self-modification; automatic evolutionary improvement. MLX-LM local inference and API-connected models are part of the initial plan, not deferred capabilities. Future extension contracts do not authorize building deferred features now.

## Planning record

2026-11-07: constitution read; Generative Agents source reviewed at a pinned revision; official stack documentation consulted; five planning documents created. No constitutional changes or application changes proposed as already accepted. Next actionable decision is approval of **M01**, including P01/P02 and minimal run provenance.

2026-11-08: user requested API-connected models and a macOS/Apple Silicon runtime instead of Ollama. Interpreted the recalled name “molx” as MLX and selected MLX-LM as the local LLM layer. Superseded P07, added C13/C14 and proposed P15/P16, split inference delivery into M07A (local) and M07B (API), and updated all five documents. The local-first reference experiment remains constitution-compatible. No AGENTS.md changes, application implementation, model downloads or API inference calls are authorized by this documentation update.

2026-11-08: user explicitly approved starting implementation with step one and then testing. Implemented M01 using Python 3.12, FastAPI, React/TypeScript, Vite and Canvas 2D, plus a minimal SQLite run store. Initialized local Git without creating a remote. Dependency installation and M01 tests are part of this approval; model installation and later milestones are not. Added exact commands and manual acceptance steps in README.md. See [M01 validation](validation/M01.md) for results and outstanding real-browser checks.

2026-11-08: user approved and requested M02 only. Implemented deterministic seeded environment, `env.energy_inflow.v1`, integer ledger, atomic per-tick SQLite commits (WAL enabled after runtime probe: SQLite 3.53.1), run controls and event UI. Accepted P03/P04 for M02 scope; initial energy/material distributions remain provisional (O01). Added a "pause at tick" control and a dev-only `zero_inflow` fixture. M01's legacy tables are preserved unused. See [M02 validation](validation/M02.md). Later milestones, models and AGENTS.md are untouched.

2026-11-08: user approved M03 ("step 3"). Implemented sealed versioned manifests, checksummed snapshots (every 100 ticks, initial, pause/auto-pause/close), startup restore from latest valid snapshot plus recorded tail, replay from stored deltas only, read-only history view and an in-place v2→v3 database migration that rewrites no history. New runs use state schema 3 (PRNG state recorded and hashed); M02 runs remain schema 2 with unchanged hashes, so the seed-42 tick-100 hash for new runs differs from M02's documented value. Snapshot cadence remains a technical parameter (not physics). See [M03 validation](validation/M03.md).

2026-11-08: user approved M04 ("next step"). Implemented organism state (state schema 4), hidden lifespan sampled from the seeded PRNG (160–240; fixtures may narrow it), upkeep 1/tick, move cost `1 + floor(packed/2)`, Manhattan-radius-2 relative-offset perception, wait/move validation with diagnostic rejections, death by age/starvation/exhaustion with energy retirement (no cell change), single extinction record that archives the run, and DB layout 4 (additive 3→4 migration). Choices made within the proposals: organisms decide every tick in M04 (the 4-tick cadence belongs to M06 scheduling); seed endowment counts as initial energy; observations use relative offsets without absolute position or tick; extinction stops all further ticks including environmental ones. Organisms appear only in labeled dev fixtures driven by scripted policies; standard runs have none until cognition (M06/M07). See [M04 validation](validation/M04.md).

2026-11-08: user approved M05 ("next step"). Implemented `extract(energy|material, 1–10)` with a 1-energy fee paid from energy held before extracting, `modify(pack|unpack, 1–2)` at 2 energy per unit, organism material capacity 20, cell packed cap 8, entry cost `1 + floor(packed/2)`, retirement of carried material at death, an engine-validator cost preview and actor-attributed cell history (state schema 5, rules m05-rules-1). Choices within the proposals: validation order arguments → per-action limit → fee → source → capacity, never clamping; extracted energy may not exceed capacity after the fee; fixtures may define initial stockpiles that are recorded in the initial state. Schema-4 runs keep M04 rules (no extract/modify) and byte-identical hashes. Frame contract v5. See [M05 validation](validation/M05.md).

2026-11-08: user approved M06 ("next one"). Implemented the provider contract (request/identity/capabilities/usage/typed errors), recorded and scripted providers, strict Pydantic parsing with schema bounds from the run's rules, decision cadence (every 4 ticks or a local view change ≥ 10 energy+material, at most one decision per tick), a 1-energy cognition fee, same-state observations, tick-rotated resolution, a persisted request inbox and append-only decision attempts with a unique consumption key, a staged tick barrier with pending-pause semantics, one transport retry and technical pauses (state schema 6, rules m06-rules-1, DB layout 5). Choices within the proposals: the previous decision result given to an organism is derived from recorded events (so a rejection has no state effect) and is bound by the input hash; the input hash excludes the run id so recordings match fresh runs of a fixture; a fatal cognition fee is recorded as starvation; the schema enforces per-action amount limits, so such requests are now `schema_invalid` at the parse boundary (the engine's `over_action_limit` remains as defense in depth); M04/M05 fixtures keep every-tick, fee-free decisions via labeled fixture parameters. Frame contract v6. See [M06 validation](validation/M06.md).

2026-11-09: user selected the local model `~/models/Qwen3.8-27B-Uncensored-MLX/8-bit` (an 8-bit MLX quantization of a `qwen3_5` architecture, ~29.5 GB) and asked to start it, which approves M07A (resolves O10 for the local path; license/provenance of this variant is unrecorded in its files and remains unknown). Implemented a dedicated MLX-LM worker subprocess run by a configured interpreter (`GENESIS_MLX_PYTHON`, here the existing `~/models/vllm-mlx` venv with mlx 0.32.0 / mlx-lm 0.31.3; nothing installed or downloaded), one resident model, one active request, 4096-token context / 256-token output limits, sampling temperature 0.7 / top-p 0.95 / top-k 20 with a per-request seed from the input hash, thinking disabled, and the versioned prompt `decision-v1` (rules and situation only, no goals or roles). Failure policy: timeout kills the worker and is retried once; a worker crash is not auto-retried and pauses the run until resume; config/context errors are not retried; truncated or non-JSON output is recorded as unparsed. The manifest seals a file-manifest digest (content of small files, size/mtime of weights), not a weight content hash. Live runs place one organism with a seeded placement. Output schema adherence is prompted and validated, not constrained (O13 open). See [M07A validation](validation/M07A.md).

2026-11-10: user asked to build the step after M07B ("lets build next step what is after 7b"), which approves M08 only. Implemented costly memory and bounded recall: state schema 7 (rules `m08-rules-1`), DB layout 6 (additive `event_memory_changes`), snapshot format 3, prompt `decision-v2`. The proposed defaults were adopted unchanged: 64 records / 16 KiB / 256 bytes per record, 1 energy per started KiB written, and 1 energy per started KiB kept every 10 ticks.

Choices made within the proposals, all reversible:

- Memory writes are automatic and deterministic: an observation of the organism's own cell, plus an outcome for each accepted action. Identical observations of the same cell are deduplicated. Organisms cannot yet author or delete records.
- A write is skipped (with a diagnostic) unless at least 1 energy would remain, so memory never kills by itself. Upkeep can starve, like basal upkeep.
- Recalled records reach cognition only as offsets from the current position. Acquisition coordinates stay engine and observer data.
- Salience is observation 1, outcome 2.
- Dead organisms' records stay frozen in state.
- Memory rules live in `engine/memory.py` because they mutate state; recall lives in `cognition/retrieval.py`.
- Earlier fixtures set `memory_enabled=false`, so their behaviour and recordings are unchanged. The new dev fixtures are `memory_return` (labelled `recall_max` 16) and `memory_budget`.

The live-model A/B observation required by the M08 acceptance criteria has not been run. See [M08 validation](validation/M08.md). AGENTS.md is untouched; later milestones still need approval.

2026-11-11: user asked for an experiment to check that memory works before M09. Ran `m08-memory-ab-v1` (`experiments/m08_memory/`): labelled hand-built worlds, nothing written to the run database. Local Qwen3.8-27B acted on recalled memory (direction- and content-specific, 20/20 vs 0/20; episodes 5/5 vs 0/5). DeepSeek-V3 on Replicate waited in every decision and ignored memory. The M08 criterion G observation is recorded in the [M08 validation](validation/M08.md); no rules or defaults were changed.

2026-11-11: user approved M09 ("lets implement m09 and test it with 10 organisms, ask me before testing"). Implemented local exchange: state schema 8 (rules `m09-rules-1`), DB layout 7 (additive `event_message_changes`), snapshot format 4, frame contract v8 (`interactions`), and prompt `decision-v3`.

The messaging defaults were adopted unchanged: adjacent unicast, at most 256 bytes, inbox of 8, expiry after 20 ticks, and fee `1 + ceil(bytes/64)`. Transfer: fee 1, at most 10 units.

Choices made within the proposals, all reversible:

- Adjacent means sharing an edge (Manhattan distance 1).
- A transfer also queues a free engine receipt to the recipient. Receipts sit outside inbox capacity.
- Inbox items are read when shown at a decision, then stored as paid `message` memories.
- A delivered message makes the recipient's decision due (reason `message`).
- One decision's memory write is capped at the memory limits; extra read items are not stored.
- Earlier fixtures set `exchange_enabled=false`.

The user's backend was restarted on the new code because their Vite page had reloaded to frame v8; the database was backed up first. The live 10-organism run waits for the user's go-ahead. See [M09 validation](validation/M09.md).

2026-11-11: two live M09 runs were made with 10 local Qwen organisms, seed 42, 40 ticks each:

- **32 × 32:** almost no encounters.
- **16 × 16** (user-requested smaller map; added the `size` option, 8–64, for new runs): neighbours were adjacent in 40 of 202 decisions.

Neither run produced a transfer or message. Recorded as a finding; the prompt was not changed to encourage exchange.

The user then approved continuing the 16 × 16 run and building M10 ("yes resume"). Implemented starting tendencies: state schema 9 (rules `m10-rules-1`), prompt `decision-v4`, tendency modes `seeded`/`neutral`/`off`, a population comparison and per-organism behaviour counts from events, and matched fixtures `tendency_social`/`tendency_solitary`. Choices:

- Six integers 0–100, uniform and independent.
- Neutral means all 50, drawn from the same random stream, so paired worlds are identical.
- Neighbours' tendencies stay private.
- No engine rule reads tendencies.

Fixed an M09 display bug: exchange outcomes were shown as "Waited". The paired live runs wait for the user. See [M10 validation](validation/M10.md).

2026-11-11: user approved the paired M10 live test ("yes lets go"). Backend restarted on M10 (database backed up). Seed 42, 16 × 16, 10 local Qwen organisms, 40 ticks per arm:

- **Seeded:** org-0010 (affiliation 88) sent 10 messages to an adjacent neighbour, which read them and declined. org-0010 starved at tick 33.
- **Neutral:** 4 communication attempts, all `not_adjacent`.

These are the first exchanges or attempts in any run; there were none without tendencies in the prompt. Recorded as n = 1 and descriptive; it possibly reflects the prompt mentioning affiliation at all. No defaults changed. See [M10 validation](validation/M10.md).

