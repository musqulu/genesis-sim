# Experiments log

Live runs that are not tied to a single milestone's validation. Descriptive only: one run is an observation, not evidence of an effect.

## 2026-11-12 — mixed models, 15 organisms (`6b564fd4…`)

**Setup** (requested by the user):

- 20 × 20 map, seed 42, energy inflow 3 per cell every 10 ticks (3× the default), seeded tendencies, M13 rules.
- A mixed population: org-0001 to org-0008 on `Qwen3.8-27B-Uncensored-MLX/8-bit`; org-0009 to org-0015 on `Qwen3.5-4B-MLX-4bit`. Both have the prefix cache on; children keep their parent's model.
- Started to tick 300. Extinct at **tick 176** after 38 min.

| | 27B organisms (8) | 4B organisms (7) |
|---|---|---|
| Decisions | 418 | 83 |
| Accepted / rejected / unparsed | 72% / 114 / 1 | 79% / 14 / 3 |
| Accepted actions | extract 318, move 45, reflect 31, wait 18, communicate 3, reproduce 1, transfer 1 (rejected) | **wait 57**, extract 16, move 4, reflect 3 |
| Mean latency | 5.15 s | 0.75 s |
| Deaths | starvation at ticks 70–176 (last: org-0004 at 176) | **all starved by tick 41** (ticks 30–41) |

**Observations.**

- **The 4B organisms starved quickly, mostly by waiting.** Their explanations show a misunderstanding of the rules, e.g. *"Maintaining energy while assessing surroundings"*, as if waiting cost nothing (upkeep is 1 per tick). This matches the replay benchmark (51% `wait`).
- **One birth:** org-0004 (27B) → org-0016 at tick 155, passing no memories. The parent then messaged its child (*"I carry 20 material, low energy (26)…"*, tick 158), and at tick 174 tried to give energy but was rejected (`insufficient_energy`). The child starved at tick 175, and the parent at 176.
- **A two-way exchange between 27B organisms** (ticks 61–62):
  - org-0005: *"I keep finding you at (0,1). Are you resting there…?"*
  - org-0001: *"I extract at (0,0). I'll move to (-1,0) next tick…"*
- **Even with 3× inflow, every 27B organism starved before its lifespan ended.** They are sedentary: 45 moves in 418 decisions. Most rejections (92 of 114) were `insufficient_source`, i.e. extracting from depleted cells. Local depletion, not total scarcity, appears to limit them.
- No packing (grid modification) by any organism.
