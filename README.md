# Genesis

An observable grid world for an artificial-life experiment. The [constitution](AGENTS.md) defines the experiment; [the roadmap](docs/ROADMAP.md) describes its incremental implementation.

**Implemented: M01 (canonical grid and inspector), M02 (deterministic environment and resource accounting), M03 (snapshots, restart and basic replay), M04 (one finite-lived organism with local perception), M05 (costed extraction and durable cell modification), M06 (recorded cognition and deterministic scheduling), M07A (local MLX-LM models), M07B — API-connected models (OpenAI-compatible chat-completions and Replicate) — M08 (costly memory and bounded recall) M09 (ten organisms and local exchange), M10 (motivation profiles), M11 (bounded reflection with evidence) and M12 (agent-initiated birth with imperfect trait inheritance).** The backend owns a seeded 32 × 32 grid with integer energy and material. One explicit rule adds up to 1 energy per cell every 10th tick (cap 100); every change is committed atomically with a causal event, exact deltas and a ledger. Checksummed snapshots are saved every 100 ticks and on pause/close. On startup the run is rebuilt from the newest valid snapshot plus recorded events and verified against recorded hashes. The browser has run controls, a legend, an event list, inspectors and a read-only history view that can rebuild any committed tick from a snapshot or from the initial state.

Organisms have a hidden sampled lifespan, pay upkeep and movement costs, perceive only a radius-2 neighbourhood, and die of age, starvation or exhaustion; the last death records an extinction and archives the run. In M04 organisms exist only in **labeled test fixtures** driven by scripted test policies — standard runs have none, and nothing is autonomous yet. Organisms can extract energy or loose material from their cell and pack carried material into it (or unpack it), all at explicit energy costs; packed cells cost more to enter and stay changed after their maker dies. Every organism decision now crosses the same boundary a live model will use (M07): a provider-independent request (observation, previous result, action schema, input hash) is persisted, answered through a provider, parsed strictly and validated by the engine. Decisions are due every 4 ticks or on a significant local change, cost 1 energy, and resolve in tick-rotated order inside an atomic tick barrier. Providers: recorded responses and scripted test policies (fixtures), and — since M07A — a **local MLX-LM model** running in a dedicated worker process, selectable for a one-organism live run. Since M07B a run can instead use a **model API** (an OpenAI-compatible endpoint such as your own vllm-mlx server, or Replicate) through the same boundary. Since M08 each organism has a **costly, limited memory**: after every decision the engine stores what it observed on its cell and the outcome of an accepted action; writing costs 1 energy per started KiB and keeping costs 1 per started KiB every 10 ticks; at most 64 records / 16 KiB are kept and the oldest are forgotten first (history keeps them). Each request recalls up to 8 of the organism's own records (recency + keyword overlap + fixed salience), given as offsets from where it is now. Since M09 up to 12 organisms share the world and can **give** energy or material to (`transfer`, fee 1) or **message** (`communicate`, ≤ 256 bytes, fee 1 + 1 per started 64 bytes) a living organism in an adjacent cell; items arrive in the recipient's inbox the next tick, are read at its next decision and become memories. Messages are claims, never instructions. Since M10 each organism starts with six seeded **tendencies** (maintenance, caution, curiosity, affiliation, experimentation, transmission; 0–100, fixed for life) stated in its own prompt as inclinations, not goals; a *neutral* option (all 50) makes a paired control with an identical world. New runs can also use a smaller square map (**Map size**). Since M11 an organism that has gathered 8 new salient records (and not reflected for 20 ticks) gets a **reflection** instead of a decision: up to two short interpretations, each citing its own records as evidence, stored as paid memories; invalid citations are rejected and nothing is stored. Since M12 an organism may **reproduce** into an adjacent empty cell (fee 10 plus an endowment of at least 20 energy, keeping 10, at most every 20 ticks): the child gets a fresh hidden lifespan, a parent link, generation + 1 and tendencies that are partly inherited (±10) and partly new; births happen only by such a choice, never to keep a population alive. There is no decay or diffusion.

## Run locally

Requirements: Python 3.12 or later (below 3.15), `uv`, and Node.js 22.22.2+ on the 22.x line (or 24.15+ on the 24.x line) with npm. Development here was verified with Python 3.12 and Node.js 22.23. Dependencies are locked in `uv.lock` and `web/package-lock.json`.

From the repository root, install the dependencies:

```sh
uv sync --locked
npm ci --prefix web
```

Start the backend in one terminal:

```sh
uv run uvicorn genesis.api.app:app --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```sh
npm run dev --prefix web
```

To make local MLX-LM models available (optional; Genesis itself never imports MLX), point the backend at a Python interpreter that has `mlx` and `mlx-lm`, and at the folder holding your MLX models (default `~/models`):

```sh
GENESIS_MLX_PYTHON=~/models/vllm-mlx/.venv/bin/python \
GENESIS_MLX_MODEL_DIRS=~/models \
uv run uvicorn genesis.api.app:app --host 127.0.0.1 --port 8000
```

Nothing is downloaded. Without `GENESIS_MLX_PYTHON` the app works as before; live-model runs are simply not offered (and existing ones replay without the model).

### Model APIs (M07B, optional)

API connections are listed in [`config/providers.toml`](config/providers.toml) (override with `GENESIS_PROVIDERS_FILE`). The file holds **no keys**: each profile names the environment variable that holds its key, and you set it in the shell that starts the backend. Keys stay in the backend process; they are never stored in the database, run manifest, reports or anything the browser receives.

- **Your own vllm-mlx server** (profile `local-vllm`, no key). Start it on port 8090 (Genesis uses 8000); `HF_HUB_OFFLINE=1` guarantees nothing is downloaded:

  ```sh
  HF_HUB_OFFLINE=1 ~/models/vllm-mlx/.venv/bin/vllm-mlx serve \
    ~/models/Qwen3.8-27B-Uncensored-MLX/8-bit \
    --served-model-name Qwen3.8-27B-Uncensored-MLX/8-bit --port 8090
  ```

- **Replicate** (profile `replicate`). Put a text model with a `prompt` input into `model = "owner/name"` (optionally `owner/name:version`), then start the backend with your token in its environment:

  ```sh
  export REPLICATE_API_TOKEN=...   # in this shell only; never in a file
  uv run uvicorn genesis.api.app:app --host 127.0.0.1 --port 8000
  ```

  At run creation Genesis pins the model's current version and reads which inputs it accepts; the run uses exactly that version for its whole life. Replicate bills your account per prediction.

API runs do not need MLX or local model files. Each run is locked to the profile it was created with (sealed into the run, so editing the file later changes nothing for existing runs); there is never an automatic fallback to another model. Network failures, refused keys, rate limits and the per-run request/token budget pause the run as technical problems — no ecological time passes — and Resume retries. Replay never needs the endpoint or the key.

Open **http://127.0.0.1:5173**. The development server forwards `/api` to the backend at port 8000. Both servers bind to loopback only. Stop each with Ctrl+C in its terminal. Neither server needs an account, API key, model or external font/image service (only API runs contact the endpoint you configure).

If a port is already occupied, use or stop the previously started Genesis process rather than launching another copy. Run a single backend worker. FastAPI's interactive contract is at http://127.0.0.1:8000/docs.

## Watching a run

The page is built around the world itself:

- **Stage.** The grid fills the window. Bright cells hold more energy; pink squares are living organisms. **Hover an organism** to see its energy, carried material, age, position and last action (with the model's own explanation). **Fullscreen** (top right) shows only the stage; press Esc to leave.
- **Control bar** (below the stage). Start / Pause / Resume, Step, Speed and *Pause at tick*, plus a new-run setup: **Seed**, **Model** (local MLX-LM models found under `~/models`, and *API ·* connections from `config/providers.toml`, with **Check connection** to probe one) and **Organisms** (1–12, all sharing the one loaded model, each with its own private context). **New run** starts it; **Summary** writes a plain-English summary of the current run.
- **Dashboard** (top right). Every inspector from earlier milestones — organisms, recorded history, cell inspector, run record and ledger, decision inspector, event list — unchanged, in a drawer that opens beside the stage.
- **Run summaries.** When the last organism dies, and whenever you replace a run with a new one, Genesis writes a plain-English summary built only from the recorded facts (no model writes it) and shows it on screen. Each is saved as Markdown in `data/reports/` (override with `GENESIS_REPORTS_DIR`), one file per run, e.g. `data/reports/2026-11-09_c9a4f582_seed42.md`.

With several organisms each decision round takes roughly *number of organisms × 4 s* with the local 27B model; logical time waits for every organism, so a 10-organism run moves visibly slower than a 1-organism run.

## Test M12 yourself

With the backend started with `GENESIS_DEV=1`:

1. **A birth.** New run with fixture `birth_pair`, then **Step** once. The stage caption reads `t1 org-0001 → org-0002 born, endowment 40 energy`, a pink dotted arrow points to the new organism, and *Organisms* lists `org-0002`.
2. **Costs, identity, traits.** Select `org-0001`: *Gave birth to org-0002 (endowment 40, fee 10)*, energy 49. Select `org-0002`: *Parent: org-0001* (click it), generation 1, energy 40, sampled lifespan *hidden while alive*. Its tendency bars differ from the parent's (80, 20, 60, 40, 70, 30): four are inherited ±10, two are fresh. The accepted event's detail lists the mask and every draw.
3. **Refusals.** Step on. The parent's later attempts are rejected `cooldown`, `endowment_too_small`, `occupied` (its child is there) and `insufficient_energy`. The child, running the same script, is rejected `insufficient_energy`.
4. **Parent dies.** **Start**: the parent dies of age at tick 33, while the child lives on. Its *Parent* link and generation stay, and *Recorded history* shows both at earlier ticks.
5. **No rescue.** The child starves at tick 41. The run goes EXTINCT, and nobody is created to replace it.

## Test M11 yourself

With the backend started with `GENESIS_DEV=1`:

1. **Below the threshold.** New run with fixture `reflection_probe`, **Step** to tick 8. Every attempt in the *Decision inspector* is an ordinary action.
2. **Crossing it.** **Step** once more. Tick 9 is `accepted (cognition.reflect.v1)`. Its detail reads *reflection request: 12 of its own records*, with the exact `reflection-v1` prompt and the raw reply.
3. **Open the evidence.** In the organism's *Memory*, the new record is marked **reflection · interpretation** and *cites* two record ids; those records are listed above it, unchanged.
4. **Invalid evidence.** New run with fixture `reflection_invalid`, then step to tick 9. The attempt is `rejected: invalid_evidence` and nothing is stored.
5. **Cooldown and budget.** **Start** either fixture. The next reflection comes only at tick 29, never two requests in one tick, and each accepted reflection costs 2 energy plus storage in the ledger.

## Test M10 yourself

With the backend started with `GENESIS_DEV=1`:

1. **Matched fixtures.** New run with fixture `tendency_social`, then **Step** twice. In the Dashboard, *Population* shows org-0001's affiliation bar at 90, and its *Tendencies and behaviour* section reads `communicate 2 · messages 2 sent`. Repeat with `tendency_solitary`: affiliation 10, `extract 2`, no messages. Nothing else differs, and the policy is a scripted test that reads only its own context.
2. **Prompt fields.** In the *Decision inspector* → *Exact prompt sent* of a live run, `decision-v4` lists `tendencies` and the sentence "They are dispositions, not goals or rules". Other organisms' tendencies never appear.
3. **Paired live runs (uses your model).** Same seed, model, map size and organism count: one run with **Tendencies** *Seeded*, one with *Neutral*. Compare the *Population* tables. No clear difference is a valid result; record the sample size.

## Test M09 yourself

With the backend started with `GENESIS_DEV=1`:

1. **Two neighbours.** New run with fixture `exchange_pair`, then **Step** 4 times. The bottom-left *Recent exchanges* caption on the stage lists `t2 org-0001 → org-0002 gave 5 energy`, `t3 … gave 3 material` and `t4 … “There is loose material on my cell.”`. Amber (gift) and dashed blue (message) arrows appear on the grid and fade over 8 ticks.
2. **Next-tick delivery and a reply.** **Step** twice. At tick 5 org-0002 answers `“Received. Thank you.”`, which is only possible because the message arrived at tick 5. In the Dashboard, select `org-0002`: *Exchanges* lists all four items as `read` with their ticks, and *Memory* holds `org-0001 said: …`.
3. **Out of range and oversized.** The event list shows `message_too_long` at tick 5 and `not_adjacent` at ticks 7–8, after org-0001 has moved one cell away. Rejections change nothing.
4. **Ten organisms (live; uses your model).** In **Model**, pick the local Qwen model, set **Organisms** to `10`, **New run**, then **Start**. Each decision round asks the model 10 times (about 50 s with the local 27B model). Exchanges happen only if organisms meet and choose to; none is a valid outcome. **Fullscreen** shows the stage with the exchange caption.
5. **Pause and inspect.** Pause during a round: the yellow barrier note shows the queue. Each organism's *Decision inspector* shows its own context only.

## Test M08 yourself

With the backend started with `GENESIS_DEV=1`, open the **Dashboard**:

1. **Visit and return.** New run with fixture `memory_return`, **Step** once, select `org-0001`. Under *Memory* it has 2 records: an observation of its cell (`loose material 25`) and the outcome `waited`; energy fell by upkeep + a 1-energy write fee.
2. **Out of view, still remembered.** Step to tick 5. The organism walked three cells east (beyond its radius-2 view). In the *Decision inspector* the tick-5 attempt lists *Memories recalled* — including `org-0001-m00001 … at offset (-3, 0) — “cell: … loose material 25 …”`. Its observation table no longer contains that cell, and the remembered values are as they were then (never refreshed).
3. **Recalled evidence drives the scripted choice.** That attempt's proposal is `move west`. Step to tick 9: it is back at `(10, 10)` carrying `5 / 20`. *Recalled for its latest decision* shows each candidate's score, recency, relevance and salience, and which were given. (This is a scripted test policy that reads only its request context — not autonomy.)
4. **Budget, eviction, unaffordable writes.** New run with fixture `memory_budget` (6-record memory), **Start**. It dies of starvation at tick 14. *Memory* shows `6 / 6 records` and *Forgotten records (18)* — observer only, each with the tick and event that forgot it. The event list shows `memory_written`, `memory_upkeep` (tick 10) and a `memory_skipped` diagnostic (`insufficient_energy`) before death; the ledger stays balanced.
5. **Restart.** Stop and restart the backend: the same memory, forgotten records and hash return. *Recorded history* at an earlier tick shows the memory as it was then.

## Test M07B yourself

1. Start vllm-mlx on port 8090 as above, then the backend (no `GENESIS_MLX_PYTHON` needed).
2. In **Model**, choose *API · vllm-mlx on this Mac · …*; press **Check connection** (expect "Connected; … is served"). Set **Organisms** and press **New run**. The stage says *SELF-HOSTED API · 127.0.0.1:8090 · …*.
3. **Start.** The health line under the controls reads *API connection: … · requests, failures · budget*. In the Dashboard's *Decision inspector* each attempt shows the exact prompt, endpoint, sampling and `extra_body` sent, the raw reply and the engine's outcome. Token counts the server does not report are shown as unknown.
4. **Interrupt.** Stop the vllm-mlx server while the run is going: the run pauses with a `transport` problem at the pending tick, nothing is consumed and the provider is not switched. Start the server again and press **Resume**: that tick commits exactly once.
5. **Replay offline.** Stop the server and the backend; start the backend again. The run returns at the same tick and hash, and *Recorded history* verifies from the initial state without contacting any endpoint.
6. **Replicate** (needs your token and a chosen model): as steps 2–3 with *API · Replicate · owner/name*; the stage says *REMOTE API*.

## Test M07A yourself

Start the backend with `GENESIS_MLX_PYTHON` as above (add `GENESIS_DEV=1` if you also want fixtures).

1. **Model offered.** In the control bar, *Model* lists `1 organism · local MLX-LM: Qwen3.8-27B-Uncensored-MLX/8-bit (29.5 GB)` (and any other MLX folders under `~/models`).
2. **Start a one-organism run.** In the control bar pick it under *Model*, seed `42`, *Organisms* `1`, **New run**. The header shows `COGNITION: local MLX-LM · … — live model, nondeterministic`; the organism is labeled `LIVE MODEL`; below the controls, *Inference worker: stopped* (it starts on the first decision).
3. **Inspect three decisions.** Speed 5, *Pause at tick* `9`, **Start**. While the model works (~3–5 s per decision) a yellow *waiting at the decision barrier* note appears and logical time waits; the page stays responsive. In the *Decision inspector* open each attempt: raw output, parse, parsed proposal, engine outcome (accepted or rejected — the model's choices are not corrected), tokens and latency, and *Exact prompt sent (decision-v1)* with sampling, limits and runtime. The worker line shows `ready · pid … · ~29 GB`.
4. **Kill the worker during a request.** **Resume**; when the yellow note appears, run `kill -9 <pid>` with the pid shown. The run stops with *Provider failure … worker_crashed*; the committed tick stays, the pending tick waits, nothing is consumed.
5. **Restore and resume.** Click **Resume**: a fresh worker loads the model, the pending tick commits exactly once, and the worker line shows a restart.
6. **Replay without inference.** Stop the backend and start it *without* `GENESIS_MLX_PYTHON`. The run returns at the same tick and hash; *Recorded history* (full replay from the initial state) verifies, and every recorded prompt/output is still inspectable. No model is loaded.

## Test M06 yourself

With the backend started with `GENESIS_DEV=1`:

1. **Recorded-valid fixture.** New run with fixture `recorded_valid`. The world header shows `COGNITION: recorded · recorded_valid — not a live model`.
2. **Observation and action.** Click **Step**. The *Decision inspector* (below the grid) shows tick 1: the input hash, the observation the organism was given (`… cells, energy 58, age 1`), the **verbatim raw output** (`{"action": "extract", …, "amount": 5, …}`), *Parse* `valid`, the parsed proposal and the engine outcome `accepted (action.extract.v1)` with a link to its event. The organism now carries `5 / 20`; energy `57` (upkeep + 1 cognition fee). Step again: tick 2 makes no request (decisions are due every 4 ticks or on a local change).
3. **Invalid outputs.** New run with fixture `recorded_invalid`, *Pause at tick* `13`, **Start**. In the inspector: tick 1 `malformed_json`, tick 5 `schema_invalid` (amount is a string), tick 9 `schema_invalid` (amount 99 out of range), tick 13 `schema_invalid` (unknown action `teleport`). Each has no proposal, consumed its opportunity and changed nothing (the organism stays at `(3, 0)`; only upkeep and fees were spent). Later ticks add an unknown field, empty output, a well-formed but illegal move (`rejected: out_of_bounds`), a non-object and a duplicate key.
4. **Pause with a response pending.** New run with fixture `recorded_delayed` (each response takes 3 s), speed 10, **Start**. When the yellow note *Tick N waiting at the decision barrier: 0 / 1 responses* appears, click **Pause**. Wait a few seconds: the note becomes `1 / 1 responses … nothing commits until you resume or step`, while the tick stays put.
5. **Resume, one commit.** **Resume** (or **Step**). The pending tick commits exactly once; the inspector shows a single attempt for it and the event list a single action.
6. Optional: fixture `recorded_mismatch` — the second recorded response does not match its request, so the run stops with a technical *Provider failure … mismatch* before that tick; no ecological time passes.

Recordings live in `backend/genesis/providers/recordings/` and are regenerated with `uv run python -m genesis.providers.record` (`--check` verifies them).

## Test M05 yourself

Start the backend with `GENESIS_DEV=1` (see below), then:

1. **Material fixture.** New run with seed `42` and *Test fixture* `material_workshop`. `org-0001` stands at `(10, 10)` with `0 / 20` carried material; inspect cell `(10, 10)`: 25 loose, `0 / 8` packed, *Energy to enter* `1`.
2. **Extraction and packing.** Step to tick 7, watching *Latest decision*: tick 1 `Unparsed: schema_invalid` (asked for 11; since M06 the action schema enforces 1–10), ticks 2–3 extract 10 + 10 (carried `20 / 20`), tick 4 rejected `over_capacity`, tick 5 packs 2, tick 6 `Unparsed: schema_invalid` (pack 3; the schema allows 1–2), tick 7 packs 2. Cell `(10, 10)` now shows `4 / 8` packed and entry cost `3`; its *Recent changes* name `org-0001` with before/after values. The *Cost preview* table shows what each action would cost right now, computed by the engine's validator.
3. **Deltas.** Step twice more: it leaves east (cost 1) and re-enters (cost **3** — the packed cell is harder to enter). Click the tick-2 extraction event: the cell table shows `loose_material 25 → 15`, the organism table `carried_material 0 → 10` and `energy 58 → 57`, and the ledger line `extraction_fee +1`. The ledger panel shows loose + packed + carried + retired equal to the initial material.
4. **Over-capacity and unaffordable.** Step to tick 21: tick 13 is rejected `over_capacity` (the cell holds at most 8), the cell reaches `8 / 8` (entry cost `5`), and tick 21's move back is rejected `insufficient_energy` (4 energy < 5). Rejections change nothing.
5. **Death, restart, revisit.** **Start**: it starves at tick 25; its 12 carried units are retired (not dropped). Restart the backend and inspect `(10, 10)` again: still `8 / 8`, entry cost `5`, still showing `by org-0001`; the grey packed marker is still drawn. *Recorded history* at tick 4 shows the cell before packing.

## Test M04 yourself

Organism fixtures are development-only. Start the backend with fixtures enabled (stop any running backend first):

```sh
GENESIS_DEV=1 uv run uvicorn genesis.api.app:app --host 127.0.0.1 --port 8000
```

1. **Corner perception.** In the control bar, choose seed `42`, *Test fixture* `corner_perception`, **New run**. The run is labeled TEST FIXTURE; *Organisms* shows `org-0001` alive at `(0, 0)`, “scripted, not autonomous”, and *Sampled lifespan: hidden while alive*. A pink marker sits in the top-left cell.
2. **Overlay vs observation.** Click **Step** once. *Latest decision* reads `Rejected: out_of_bounds` (it tried to move west off the grid) and lists the exact observation it was given: 6 cells with relative offsets `dx,dy` (radius 2, clipped at the corner), its own energy/age and a general life note — no tick, position, lifespan or anything farther away. The pink overlay on the grid covers exactly those 6 cells; click them and compare energy/material with the table. Untick *Show this local view* to hide it.
3. **Movement.** Step to tick 6: `(0,0)` rejected again, then `(1,0)`, `(2,0)`, `(2,1)`, then a wait. The marker moves one cell at a time and energy is `51 / 100` (6 upkeep + 3 moves; rejections cost nothing). The event list shows upkeep, moves and rejection diagnostics.
4. **Starvation and age limit.** New run with fixture `starvation`, **Start**: within seconds it dies (`dead (starvation)`), the EXTINCT banner appears and the run pauses. New run with fixture `age_limit`, speed 50, **Start**: it dies `dead (age)` and only now shows its sampled lifespan (20–30), equal to its death tick; energy left at death is shown as retired and the ledger stays balanced.
5. **Death is final.** Resume and Step are disabled; restarting the backend keeps the run extinct with the same tick and no new organism. In the event list, the death and extinction events change no cell. *Recorded history* can still show the organism alive at earlier ticks.

## Test M03 yourself

1. Click **New run** (seed 42). Set speed 50 and **Pause at tick** `150`, then **Start**. When it pauses at 150, copy the *State hash*. *Recorded history* lists snapshots `0 (initial), 100 (periodic), 150 (auto_pause)`.
2. Stop the backend (Ctrl+C) and start it again. The page reconnects to the same run at tick 150 with the same hash, paused; *Loaded at startup from* reads `snapshot @ tick 150 + 0 replayed ticks`. (To see a replayed tail: step a few ticks, kill the backend hard with `kill -9`, restart; it reads `snapshot @ tick 150 + N`.)
3. Optionally **Resume** the live run. In *Recorded history* enter tick `50` and click **View tick**. A *HISTORY · tick 50* banner appears; the grid, cell inspector, ledger and event list show tick 50 only, while the live tick keeps moving in the banner. Run controls and **New run** are disabled. Select a few cells and compare their energy with live values.
4. Click **Return to live**. The live grid and controls return.
5. Pause the live run. View tick `150` with *Rebuild from: Nearest valid snapshot*, note the hash; then with *Initial state + all events (full replay)*. Both hashes equal the one copied in step 1, and *Recorded hash check* reads ✓ matches.

## Test M02 yourself

1. Open the page: **Connected**, **Paused**, tick **000000**. Click **New run** with seed `42`.
2. Click **Step** nine times (tick 9): the event list holds only the creation event. Click **Step** once more (tick 10): an `energy_inflow` event appears and changed cells are outlined.
3. Click that event: note the rule ID `env.energy_inflow.v1`, credited vs blocked totals and the per-cell before/after table. Click a cell in the table; the inspector shows the same before/after under *Recent changes*. The ledger reads **✓ Ledger balanced**.
4. Choose a speed, click **Start**, then **Pause**; wait several seconds. The tick must not move. **Step** is disabled while running.
5. Click **New run** (seed 42), set **Pause at tick** to `100`, speed 10, **Start**; when it pauses at tick 100 copy the *State hash*. Repeat with speed 50 in another new seed-42 run: the hashes must match.
6. Optional fixture: start the backend with `GENESIS_DEV=1`, pick the `zero_inflow` fixture and create a run: it is labeled TEST FIXTURE and no inflow event ever appears.

## Test M01 yourself (still valid for selection/resize/disconnect)

1. Open the page. Confirm **Connected**, **Paused**, tick **000000**, and **1,024 cells**.
2. Click the upper-left cell: the inspector shows `(0, 0)`, index `0`. Click the lower-right cell: `(31, 31)`, index `1023`.
3. Use the inspector inputs to select `(11, 23)`: index `747`. Focus the canvas and use the arrow keys; selection stays within the grid.
4. Resize the browser, then repeat the corner selections. The grid and hit targets should stay aligned. Narrow layouts move the inspector below the grid.
5. Note the run ID and checksum. Refresh the page: both remain the same. Selection is a temporary observer preference and resets on refresh.
6. Stop only the backend. Within roughly six seconds, the page shows **Disconnected / Backend unavailable** and labels any displayed world as its last confirmed state. New run creation is disabled. With no previously loaded state, it shows a waiting view instead of inventing a grid.
7. Restart the backend. The page reconnects automatically and retains the saved run ID/checksum. **Reconnect** can also request a refresh immediately.
8. Enter seed `123` and click **New run** once. The ID changes, the seed becomes `123`, and the grid differs from seed 42. Refresh and restart preserve this new active run. Earlier run records remain stored; browsing other runs is a later milestone.

## Automated checks

```sh
uv run pytest -q
uv run ruff check backend tests
uv run ruff format --check backend tests
npm test --prefix web
npm run build --prefix web
```

Python tests use temporary SQLite databases. Frontend tests use jsdom, mocked backend replies and controlled canvas geometry for selection, resizing, keyboard input and connection behavior. These tests do **not** replace visual verification in a real browser. See [M01](docs/validation/M01.md), [M02](docs/validation/M02.md), [M03](docs/validation/M03.md), [M04](docs/validation/M04.md), [M05](docs/validation/M05.md), [M06](docs/validation/M06.md), [M07A](docs/validation/M07A.md), [UI-01](docs/validation/UI-01.md), [M07B](docs/validation/M07B.md), [M08](docs/validation/M08.md), [M09](docs/validation/M09.md), [M10](docs/validation/M10.md), [M11](docs/validation/M11.md) and [M12](docs/validation/M12.md) validation for actual results and remaining manual checks.

After a build, `npm run preview --prefix web` serves the production bundle on the same port (stop the dev server first); the backend still runs separately.

## Data and boundaries

- Local run data lives in `data/genesis.sqlite3`, outside version control. Override its location with `GENESIS_DB=/absolute/path/to/test.sqlite3` when starting the backend.
- SQLite with one transaction per tick (events, deltas, ledger, cell state, tick hash and any periodic snapshot). WAL is enabled only after the runtime is checked (SQLite ≥ 3.51.3 or a fixed backport, and a real WAL probe); otherwise rollback journaling is kept. `/api/health` reports the mode and how the run was restored. The database layout is version 7; older files (versions 2–6) are migrated in place on first start without rewriting any history, and the migration is recorded in `schema_migrations`. Unknown/newer versions are refused untouched. M01's old tables are left untouched and unused.
- Each run has a canonical, SHA-256-sealed manifest: identity, configuration, rules/state schema, RNG algorithm, engine/source fingerprint, initial cells, initial PRNG state (new runs) and initial hash. New runs use state schema 3, whose hash also covers the PRNG state; M02 runs keep schema 2 and their original hashes.
- Recorded events, ticks and the manifest are authoritative. Replay applies stored deltas only — no rule, seeding or model is re-run. Snapshots and the `cells` table are verified conveniences: a bad snapshot is skipped (and noted in `technical_log`), while missing or inconsistent history stops startup with an explicit error and changes nothing.
- New runs append records and atomically change the active reference. No old run is deleted. Corrupt or incompatible saved state produces an explicit error and is preserved for inspection.
- Canonical coordinates are `(x, y)`: origin top left; `x` increases rightward, `y` downward. Cell index is `y × width + x`. Canvas pixels, color and selection never modify the world.
- Organisms: observer views withhold the sampled lifespan while an organism lives. Each decision event stores the observation actually supplied, the proposal and the engine outcome. `GET /api/organisms/{id}` (`at_tick`) returns an organism's state, latest decision, recent events, (live) a cost preview from the engine's validator and (state schema 7) its memory: active records with where/when they were acquired, forgotten records (observer only, from the recorded deltas) and the recall scores behind its latest decision; and (state schema 8) `messages`: its inbox and recent items sent or received with their state. `GET /api/world` (frame v8) includes `interactions`, the transfers and messages of the latest tick. (State schema 9) `profile` gives its tendencies and behaviour counts; `GET /api/population` (`at_tick`) compares all organisms. `POST /api/runs` accepts `size` (8–64, square) and `tendencies` (`seeded`/`neutral`). Cell inspection returns the engine's entry cost.
- Summaries: `POST /api/summary` writes or refreshes the active run's summary; `GET /api/summary` returns the latest saved one. `POST /api/runs` accepts `organisms` (1–12) together with `cognition`.
- Local models: `GET /api/models` (discovered MLX folders and runtime availability; nothing is loaded), and `POST /api/runs` with `cognition: "mlx:<model id>"` creates a one-organism live run. The run's manifest seals the model path, a file-manifest digest, runtime versions, sampling settings, limits and prompt version; each decision stores the exact prompt and usage.
- Decisions: `GET /api/decisions` (`organism_id`, `limit`, `max_tick`), `GET /api/decisions/{request_id}` (exact request context, raw output, parse, outcome, usage — unknown values stay unknown) and `GET /api/requests` (technical request inbox).
- APIs: `GET /api/world`, `GET /api/cells/{x}/{y}` (`at_tick`), `GET /api/events` (`limit`, `before`, `max_tick`), `GET /api/events/{sequence}`, `GET /api/history/{tick}` (`source=snapshot|initial`; read-only), `GET /api/snapshots`, `GET /api/health`, `POST /api/control` (`start|resume|pause|step|speed` with a unique `command_id`), and `POST /api/runs` with an integer `seed` from `0` to `4294967295` (and, only with `GENESIS_DEV=1`, `fixture: "zero_inflow"`).
- Speed changes pacing only; logical time advances only through committed ticks. Pausing stops it. A restarted backend always comes back paused.

The next step is M13 (selective memory inheritance), which needs approval. No part of M13 is active in this build.
