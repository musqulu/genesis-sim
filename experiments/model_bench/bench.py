"""Replay benchmark: how would another model answer the exact requests organisms received?

A labelled offline experiment, not a run. It samples consumed decision (and reflection)
requests from recorded runs, sends each unchanged request to a candidate MLX model, parses
the reply with the run's own rules and lets the engine judge the proposal against the world
reconstructed (from recorded history) at that tick. Nothing is committed anywhere; the
original organisms' histories are untouched.

    GENESIS_MLX_MODEL_DIRS=~/models GENESIS_MLX_PYTHON=~/models/vllm-mlx/.venv/bin/python \\
      uv run python experiments/model_bench/bench.py --model "Qwen3.5-4B-MLX-4bit" --n 120

Reported per model: parse-valid rate, engine-accepted rate, rejection reasons, action mix,
agreement with the original model's action type, reflection validity, latency and tokens.
These measure interface competence on recorded situations; they say nothing about which
model produces "better" organisms.
"""

import argparse
import json
import random
import sqlite3
import statistics
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from genesis.cognition.context import DecisionRequest
from genesis.cognition.session import parse_for
from genesis.domain.actions import ReflectionProposal, Unparsed
from genesis.engine import actions as engine_actions
from genesis.engine import reflection as engine_reflection
from genesis.engine.world import prepare_tick
from genesis.persistence.sqlite import SqliteStore
from genesis.providers import mlx
from genesis.providers.base import ProviderError

HERE = Path(__file__).parent
DB = Path(__file__).resolve().parents[2] / "data" / "genesis.sqlite3"


def sample_requests(db: sqlite3.Connection, n: int, seed: int, min_schema: int) -> list[dict]:
    """Consumed requests from live MLX runs with state schema >= min_schema, stratified so
    reflections are represented (about 1 in 6)."""
    rows = db.execute(
        "SELECT r.run_id, r.request_id, r.tick, r.request_json, d.raw_output, d.proposal_json, "
        "d.outcome FROM request_records r JOIN decision_attempts d "
        "ON d.run_id = r.run_id AND d.request_id = r.request_id "
        "JOIN run_manifest m ON m.run_id = r.run_id "
        "WHERE r.status = 'consumed' AND m.schema_version >= ? "
        "AND json_extract(d.provider_json, '$.kind') = 'mlx'",
        (min_schema,),
    ).fetchall()
    rng = random.Random(seed)
    decide = [r for r in rows if json.loads(r[3])["purpose"] == "decide"]
    reflect = [r for r in rows if json.loads(r[3])["purpose"] == "reflect"]
    n_reflect = min(len(reflect), n // 6)
    chosen = rng.sample(decide, min(len(decide), n - n_reflect)) + rng.sample(reflect, n_reflect)
    return [
        {
            "run_id": run,
            "request_id": rid,
            "tick": tick,
            "request": json.loads(req),
            "original_raw": raw,
            "original_proposal": None if prop is None else json.loads(prop),
            "original_outcome": outcome,
        }
        for run, rid, tick, req, raw, prop, outcome in sorted(chosen, key=lambda r: (r[0], r[2]))
    ]


def as_request(data: dict) -> DecisionRequest:
    return DecisionRequest(
        request_id=data["request_id"],
        run_id=data["run_id"],
        actor_id=data["actor_id"],
        target_tick=data["target_tick"],
        purpose=data["purpose"],
        schema_version=data["schema_version"],
        context=data["context"],
        input_hash=data["input_hash"],
        recall=data.get("recall"),
    )


class Judge:
    """The engine's verdict on a proposal against the recorded world at that tick."""

    def __init__(self, store: SqliteStore):
        self.store = store
        self.cache: dict[tuple[str, int], object] = {}

    def prepared(self, run_id: str, tick: int):
        key = (run_id, tick)
        if key not in self.cache:
            world = self.store.history(run_id, tick - 1).world
            self.cache[key] = prepare_tick(world)
        return self.cache[key]

    def verdict(self, item: dict, outcome) -> str:
        if isinstance(outcome, Unparsed):
            return f"unparsed:{outcome.reason}"
        prepared = self.prepared(item["run_id"], item["tick"])
        world = prepared.world
        actor = item["request"]["actor_id"]
        organism = world.organism(actor)
        if isinstance(outcome, ReflectionProposal):
            slot = next((s for s in prepared.slots if s.organism_id == actor), None)
            shown = frozenset(v.id for v in (slot.memories or ())) if slot else frozenset()
            event = engine_reflection.resolve(world, organism, outcome, shown, {})
        else:
            event = engine_actions.resolve(world, organism, outcome, {})
        return (
            "accepted" if event.type == "action_accepted" else f"rejected:{event.summary['reason']}"
        )


def action_of(outcome) -> str:
    if isinstance(outcome, Unparsed):
        return "unparsed"
    if isinstance(outcome, ReflectionProposal):
        return "reflect"
    return str(outcome.action)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="MLX model id under GENESIS_MLX_MODEL_DIRS")
    parser.add_argument("--n", type=int, default=120)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--min-schema", type=int, default=10)
    parser.add_argument("--no-prefix-cache", action="store_true")
    parser.add_argument("--tag", default="", help="suffix for the results file name")
    args = parser.parse_args()
    db = sqlite3.connect(DB)
    store = SqliteStore(DB)  # read-only use: history reconstruction only
    items = sample_requests(db, args.n, args.seed, args.min_schema)
    print(f"{len(items)} recorded requests from {len({i['run_id'] for i in items})} runs")
    settings = mlx.MLXSettings(prefix_cache=not args.no_prefix_cache)
    provider = mlx.MLXProvider(args.model, mlx.profile_for(args.model, settings))
    judge = Judge(store)
    rows = []
    try:
        for k, item in enumerate(items, 1):
            request = as_request(item["request"])
            run_life = store.history(item["run_id"], 0).world.life
            available = tuple(
                v["properties"]["action"]["const"]
                for v in request.context.get("action_schema", {}).get("oneOf", [])
            )
            started = time.monotonic()
            try:
                response = provider.decide(request, threading.Event())
                raw, usage = response.raw_text, response.usage
            except ProviderError as exc:
                raw, usage = "", None
                print(f"  provider error: {exc}")
            parsed = parse_for(request, raw, run_life, available)
            verdict = judge.verdict(item, parsed.outcome)
            original = item["original_proposal"] or {}
            original_action = (
                "reflect" if "reflections" in original else original.get("action", "unparsed")
            )
            rows.append(
                {
                    "run_id": item["run_id"],
                    "request_id": item["request_id"],
                    "purpose": request.purpose,
                    "raw": raw,
                    "action": action_of(parsed.outcome),
                    "verdict": verdict,
                    "original_action": original_action,
                    "original_outcome": item["original_outcome"],
                    "latency_s": round(time.monotonic() - started, 2),
                    "input_tokens": None if usage is None else usage.input_tokens,
                    "output_tokens": None if usage is None else usage.output_tokens,
                }
            )
            if k % 10 == 0:
                print(f"  {k}/{len(items)}")
    finally:
        provider.close()
    decide = [r for r in rows if r["purpose"] == "decide"]
    reflect = [r for r in rows if r["purpose"] == "reflect"]

    def rate(xs, pred):
        return round(100 * sum(map(pred, xs)) / len(xs), 1) if xs else None

    summary = {
        "model": args.model,
        "provider": provider.identity.to_dict(),
        "requests": len(rows),
        "decisions": {
            "n": len(decide),
            "parse_valid_pct": rate(decide, lambda r: not r["verdict"].startswith("unparsed")),
            "engine_accepted_pct": rate(decide, lambda r: r["verdict"] == "accepted"),
            "same_action_as_original_pct": rate(
                decide, lambda r: r["action"] == r["original_action"]
            ),
            "actions": dict(Counter(r["action"] for r in decide).most_common()),
            "verdicts": dict(Counter(r["verdict"] for r in decide).most_common()),
        },
        "reflections": {
            "n": len(reflect),
            "accepted_pct": rate(reflect, lambda r: r["verdict"] == "accepted"),
            "verdicts": dict(Counter(r["verdict"] for r in reflect).most_common()),
        },
        "latency_s_median": statistics.median(r["latency_s"] for r in rows) if rows else None,
        "prefix_cache": not args.no_prefix_cache,
        "output_tokens_median": statistics.median(
            [r["output_tokens"] for r in rows if r["output_tokens"]] or [0]
        ),
    }
    out = (
        HERE
        / "results"
        / f"{datetime.now(UTC):%Y-%m-%d}_{args.model.replace('/', '_')}{args.tag}.json"
    )
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "settings": vars(args), "rows": rows}, indent=1) + "\n"
    )
    print(json.dumps(summary, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
