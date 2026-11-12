# Model replay benchmark (`bench.py`)

A labelled offline experiment, not a run.

**Method.**

- It samples consumed requests from recorded live MLX runs (state schema ≥ 10, about 1 in 6 a reflection).
- Each request is sent **unchanged** to a candidate model.
- The reply is parsed with the run's own rules, and the engine judges it against the world reconstructed at that tick from recorded history.
- Nothing is committed; recorded runs are untouched.

**Scope.** It measures interface competence on situations organisms actually faced. It does not measure which model makes "better" organisms.

## Results, 2026-11-12

Machine: the user's Apple Silicon Mac, mlx 0.32.0 / mlx-lm 0.31.3, temperature 0.7, top-p 0.95, top-k 20, thinking off. Same sample seed (1).

| Model | n | Parse-valid | Engine-accepted | Same action as original | Reflections accepted | Median s/request |
|---|---|---|---|---|---|---|
| Qwen3.8-27B-Uncensored 8-bit, no prefix cache | 30 | 96% | 76% | 100% | 5/5 | 5.03 |
| Qwen3.8-27B-Uncensored 8-bit, **prefix cache** | 30 | 100% | 80% | 96% | 5/5 | **4.10** |
| Qwen3.5-4B MLX 4-bit, no prefix cache | 120 | 87% | 69% | 30% | 35% | 0.76 |
| Qwen3.5-4B MLX 4-bit, **prefix cache** | 120 | 88% | 70% | 31% | 35% | **0.65** |

"Same action as original" compares with the 27B's recorded choice; the 27B's own score reflects seeds derived from the request hash.

**Qwen3.5-4B** (`mlx-community/Qwen3.5-4B-MLX-4bit`, 2.9 GB) is about **6–7× faster** than the 27B. Its weaknesses:

- **Verbosity.**
  - 9 of its 13 unparsed decisions had an explanation over the 280-character limit.
  - All 8 rejected reflections had text over 256 bytes (up to 428).
- **Passivity.** It answered `wait` in 51% of decisions, including 42 situations where the 27B extracted energy.
- **Reflection validity:** 35%, against 100% for the 27B.

**Prefix cache** (`MLXSettings.prefix_cache`, default on):

- It reuses the model state of the shared prompt beginning: ~660 of ~1,700 tokens for decisions.
- Speed: −14% latency on the 4B (output generation dominates there), −18% on the 27B (prompt reading dominates).
- Outputs are not byte-identical to uncached inference. 27B: 18/30 raw replies identical, 29/30 the same action, with the differences in wording.
- Each decision records `prefix_cache.cached_tokens` in its detail. Turn it off for strict replay comparisons.

Rerun:

```sh
GENESIS_MLX_MODEL_DIRS=~/models GENESIS_MLX_PYTHON=~/models/vllm-mlx/.venv/bin/python \
  uv run python experiments/model_bench/bench.py --model "Qwen3.5-4B-MLX-4bit" --n 120
```
