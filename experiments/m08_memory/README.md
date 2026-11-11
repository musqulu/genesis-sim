# M08 memory A/B experiment (`m08-memory-ab-v1`)

Run on 2026-11-11. This is a **labelled experiment** on hand-built worlds. It uses the real engine, context builder, `decision-v2` prompt, providers, strict parser and validator. It writes nothing to the run database. Raw outputs are in `results/`.

**Question.** Does a live model act on a recalled memory, i.e. on information it can no longer observe?

## Design

**Setup.** One organism stands at (16, 16) on a barren 32 × 32 grid. Its radius-2 view contains no energy or material, and its energy is 25 / 100. Memory writing is on.

**Probes.** Each probe is one decision, sampled 20 times per condition. The sampling seed is varied only through the tick. Conditions differ **only** in `recalled`:

| Condition | Recalled record (`observation`, 9 ticks ago) |
|---|---|
| A — none | (nothing) |
| B — rich west | offset (−3, 0): `cell: energy 90, loose material 25, packed material 0` |
| C — rich east | the same record at offset (+3, 0): tests direction |
| D — barren west | offset (−3, 0): `cell: energy 0, loose material 0, packed material 0`: tests content |

**Episodes.** Closed loop through `prepare_tick` / `finish_tick`, up to 8 decisions, 5 runs per arm. The resource really is 3 cells west. The two arms are identical worlds, with or without the memory record. Success means standing on the resource.

**Models.** Local `Qwen3.8-27B-Uncensored-MLX/8-bit` (MLX-LM, temperature 0.7) and Replicate `deepseek-ai/deepseek-v3` (pinned version `14201f21…`). Both use the sealed default settings.

## Results

### Local Qwen3.8-27B (MLX)

**Probes:**

| Condition | Actions (n = 20) |
|---|---|
| A — none | extract energy 14, wait 3, move north 2, extract material 1. **Never west or east.** |
| B — rich west | **move west 20 / 20** |
| C — rich east | **move east 20 / 20** |
| D — barren west | extract energy 8, wait 7, move east 5. **Never west.** |

**Statistical tests** (exact two-sided Fisher test, p ≈ 1.5 × 10⁻¹¹ each):
- B vs A, moving west: 20/20 vs 0/20.
- C vs A, moving east: 20/20 vs 0/20.
- B vs D, moving west: 20/20 vs 0/20. The model responds to the record's **content**, not just to the presence of an offset.

**Episodes:**

| Arm | Reached the resource | Decisions needed |
|---|---|---|
| With memory | **5 / 5** | 3, 5, 3, 3, 3 |
| Without memory | **0 / 5** | Stayed put, or drifted north or east |

**Path integration.** As the organism walked, the recalled offset updated (−3 → −2 → −1), and the explanations track it. For example: *"Recalled a rich cell (90 energy, 25 material) 3 west … Moving west to re-approach and harvest."*

### Replicate DeepSeek-V3

- **Probes:** `{"action":"wait"}` in all 80 probes and all 80 episode decisions, in every condition. It gave no explanations, so its reasons are unknown.
- **Episodes:** 0 / 5 runs reached the resource, with or without memory.
- **Delivery checked:** the Replicate input is built verbatim from the same messages, and a re-sent B probe contained the memory paragraph and the recalled record.
- **Conclusion:** this model, under this prompt, does not use recalled memory, and does not move toward anything, remembered or not. In the earlier live run it extracted 1 energy repeatedly, which was its only action there.
- Replicate rate-limited the run (6 requests/min while the account holds under $5 credit). The script retried after the advertised wait, so no decision was invented or dropped.

## Conclusion

The memory pathway works end to end. Recorded experience reaches the request as offsets from the current position, with bounded recall and correct updating as the organism moves. A capable model's choices measurably depend on it. Whether a model *uses* memory varies by model: Qwen does, strongly; DeepSeek-V3 does not, under `decision-v2`. Neither result says anything about learning or adaptation.

## Limits

- **One clean record.** Real memories hold up to 64 records competing for 8 recall slots. In the earlier live run, older observations were often outranked by recent outcomes (see M08 validation, choice 5).
- **First step decides the episode.** After one step west the resource enters the radius-2 view. The episodes therefore mainly test whether memory triggers the first move toward something unseen; the rest of the approach can rely on what it sees.
- **Small samples:** n = 20 probes and 5 episodes per arm, one seed geometry, one prompt version and default sampling settings.
- **Cost of memory is not exercised.** The episodes are short, so memory upkeep never mattered.

Rerun:

```sh
GENESIS_MLX_MODEL_DIRS=~/models GENESIS_MLX_PYTHON=~/models/vllm-mlx/.venv/bin/python \
  uv run python experiments/m08_memory/memory_ab.py --provider mlx
REPLICATE_API_TOKEN=... uv run python experiments/m08_memory/memory_ab.py --provider replicate
```
