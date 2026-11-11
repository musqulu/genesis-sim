"""M08 memory A/B experiment: does a live model act on a recalled memory?

A labelled experiment, not a Genesis run: it builds hand-made worlds with the real engine,
context builder, prompt, providers, parser and validator, and stores nothing in the run
database. Raw outputs are written to `results/`.

Probes (one decision each). The organism stands on barren ground (nothing in its radius-2
view, energy 25/100). Conditions differ only in its recalled memory:
  A  none           - no memory at all (recalled = [])
  B  rich_west      - "cell: energy 90, loose material 25" recorded 3 cells west
  C  rich_east      - the same record 3 cells east (direction control)
  D  barren_west    - a record 3 cells west saying energy 0, material 0 (content control)
Replicates differ only in the tick, which changes the request hash and so the sampling seed.

Episodes (closed loop, memory writing on). The resource really is 3 cells west, just
outside view. Success = the organism stands on it within the decision budget. With vs
without the memory record.

    GENESIS_MLX_PYTHON=... uv run python experiments/m08_memory/memory_ab.py --provider mlx
    REPLICATE_API_TOKEN=... uv run python experiments/m08_memory/memory_ab.py --provider replicate
"""

import argparse
import json
import math
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from genesis.cognition.context import build_request
from genesis.cognition.policy import parse
from genesis.cognition.prompts import prompt_version
from genesis.cognition.session import decision_payload, update_previous
from genesis.domain.config import LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, MemoryRecord, Organism, World
from genesis.engine.actions import available_actions
from genesis.engine.world import finish_tick, prepare_tick
from genesis.providers.base import ProviderError

HERE = Path(__file__).parent
MLX_MODEL = "Qwen3.8-27B-Uncensored-MLX/8-bit"
START = (16, 16)
OFFSET = 3
RICH = "cell: energy 90, loose material 25, packed material 0"
BARREN = "cell: energy 0, loose material 0, packed material 0"
CONDITIONS = {
    "A_none": None,
    "B_rich_west": (-OFFSET, RICH),
    "C_rich_east": (OFFSET, RICH),
    "D_barren_west": (-OFFSET, BARREN),
}


def make_provider(kind: str):
    if kind == "mlx":
        from genesis.providers import mlx

        return mlx.MLXProvider(MLX_MODEL, mlx.profile_for(MLX_MODEL)), f"mlx:{MLX_MODEL}"
    from genesis.providers import config as api_config
    from genesis.providers.replicate import ReplicateProvider, resolve

    profile = api_config.get_profile("replicate")
    sealed = api_config.sealed(profile, {"replicate": resolve(profile)})
    provider = ReplicateProvider(api_config.from_sealed(sealed), sealed["replicate"], used=(0, 0))
    return provider, "api:replicate"


def world_for(provider: str, tick: int, memory: tuple[int, str] | None) -> World:
    """Barren 32x32 world. The resource exists only where a rich memory says it is."""
    life = LifeConfig(provider=provider, decision_interval=1)
    config = WorldConfig(inflow_amount=0, fixture=None)
    cells = [Cell(i % 32, i // 32) for i in range(32 * 32)]
    records = ()
    if memory is not None:
        dx, text = memory
        x, y = START[0] + dx, START[1]
        if text == RICH:
            cells[y * 32 + x] = Cell(x, y, energy=90, loose_material=25)
        keywords = ("observation", "energy", "material") if text == RICH else ("observation",)
        records = (
            MemoryRecord(
                "org-0001-m00001", "org-0001", "observation", tick - 8, 32, x, y, text, keywords, 1
            ),
        )
    organism = Organism(
        "org-0001",
        "EXPERIMENT",
        "live",
        tick - 40,
        0,
        *START,
        energy=25,
        lifespan=100_000,
        memories_written=len(records),
    )
    return World(
        config,
        tuple(cells),
        Ledger(
            initial_energy=sum(c.energy for c in cells) + 25,
            initial_material=sum(c.loose_material for c in cells),
        ),
        tick=tick,
        life=life,
        organisms=(organism,),
        memories=records,
    )


def ask(provider, prepared, slot, previous):
    request = build_request("m08-memory-ab", prepared.world, slot, previous)
    for attempt in range(3):
        try:
            response = provider.decide(request, threading.Event())
            break
        except ProviderError as exc:  # technical: retry, never invent an answer
            if attempt == 2:
                raise
            print("  provider error, retrying:", exc)
            time.sleep(3)
    parsed = parse(response.raw_text, prepared.world.life, available_actions(prepared.world))
    return request, response, parsed


def label(parsed) -> str:
    if parsed.status != "valid":
        return f"unparsed:{parsed.status}"
    p = parsed.outcome
    return (
        f"move {p.direction}"
        if p.action == "move"
        else f"{p.action} {p.resource or p.mode or ''}".strip()
    )


def probes(provider, selection: str, n: int) -> dict:
    out = {}
    for name, memory in CONDITIONS.items():
        rows = []
        for r in range(n):
            world = world_for(selection, 1000 + r * 7, memory)
            prepared = prepare_tick(world)
            (slot,) = prepared.slots
            request, response, parsed = ask(provider, prepared, slot, None)
            rows.append(
                {
                    "replicate": r,
                    "input_hash": request.input_hash,
                    "recalled": request.context["recalled"],
                    "prompt_version": prompt_version(request),
                    "raw": response.raw_text,
                    "label": label(parsed),
                    "explanation": parsed.explanation,
                    "latency_ms": response.usage.latency_ms,
                }
            )
            print(f"  {name} #{r}: {rows[-1]['label']}")
        out[name] = rows
    return out


def episode(provider, selection: str, with_memory: bool, r: int, budget: int) -> dict:
    world = world_for(selection, 2000 + r * 50, (-OFFSET, RICH))
    if not with_memory:  # identical world (resource still there), but nothing remembered
        world = replace(
            world, memories=(), organisms=(replace(world.organisms[0], memories_written=0),)
        )
    target = (START[0] - OFFSET, START[1])
    previous, path, decisions = {}, [START], []
    for _ in range(budget):
        prepared = prepare_tick(world)
        if not prepared.slots:
            break
        (slot,) = prepared.slots
        request, response, parsed = ask(provider, prepared, slot, previous.get("org-0001"))
        payload = decision_payload(slot, request, response.raw_text, parsed)
        result = finish_tick(prepared, {"org-0001": (parsed.outcome, payload)})
        update_previous(previous, result)
        world = result.world
        o = world.organism("org-0001")
        decisions.append(
            {
                "label": label(parsed),
                "recalled_offsets": [
                    (m["dx"], m["dy"])
                    for m in request.context["recalled"]
                    if m["id"] == "org-0001-m00001"
                ],
                "explanation": parsed.explanation,
            }
        )
        path.append((o.x, o.y))
        if not o.alive or (o.x, o.y) == target:
            break
    o = world.organism("org-0001")
    reached = target in path
    return {
        "replicate": r,
        "memory": with_memory,
        "reached": reached,
        "decisions_to_reach": path.index(target) if reached else None,
        "path": path,
        "alive": o.alive,
        "decisions": decisions,
    }


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact p for the 2x2 table [[a, b], [c, d]] (sum of tables no more likely)."""
    n, row, col = a + b + c + d, a + b, a + c

    def p(x):
        return math.comb(col, x) * math.comb(n - col, row - x) / math.comb(n, row)

    observed = p(a)
    lo, hi = max(0, row + col - n), min(row, col)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


def summarize(probe: dict, episodes: list[dict]) -> dict:
    counts = {name: {} for name in probe}
    for name, rows in probe.items():
        for row in rows:
            counts[name][row["label"]] = counts[name].get(row["label"], 0) + 1
    n = {name: len(rows) for name, rows in probe.items()}

    def share(name, lab):
        return counts[name].get(lab, 0)

    tests = {}
    for cond, lab in (
        ("B_rich_west", "move west"),
        ("C_rich_east", "move east"),
        ("D_barren_west", "move west"),
    ):
        k, base = share(cond, lab), share("A_none", lab)
        tests[f"{cond}: {lab}"] = {
            "with": f"{k}/{n[cond]}",
            "without (A)": f"{base}/{n['A_none']}",
            "fisher_p": round(fisher_two_sided(k, n[cond] - k, base, n["A_none"] - base), 5),
        }
    k, base = share("B_rich_west", "move west"), share("D_barren_west", "move west")
    tests["B vs D (content): move west"] = {
        "rich": f"{k}/{n['B_rich_west']}",
        "barren": f"{base}/{n['D_barren_west']}",
        "fisher_p": round(
            fisher_two_sided(k, n["B_rich_west"] - k, base, n["D_barren_west"] - base), 5
        ),
    }
    ep = {}
    for flag in (True, False):
        runs = [e for e in episodes if e["memory"] == flag]
        ep["with_memory" if flag else "without_memory"] = {
            "reached": f"{sum(e['reached'] for e in runs)}/{len(runs)}",
            "decisions_to_reach": [e["decisions_to_reach"] for e in runs],
        }
    return {"probe_counts": counts, "tests": tests, "episodes": ep}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["mlx", "replicate"], required=True)
    parser.add_argument("--probes", type=int, default=20)
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--budget", type=int, default=8)
    args = parser.parse_args()
    provider, selection = make_provider(args.provider)
    started = datetime.now(UTC).isoformat()
    try:
        print("probes")
        probe = probes(provider, selection, args.probes)
        print("episodes")
        episodes = []
        for r in range(args.episodes):
            for flag in (True, False):
                e = episode(provider, selection, flag, r, args.budget)
                print(f"  memory={flag} #{r}: reached={e['reached']} path={e['path']}")
                episodes.append(e)
    finally:
        if hasattr(provider, "close"):
            provider.close()
    summary = summarize(probe, episodes)
    record = {
        "experiment": "m08-memory-ab-v1",
        "label": "EXPERIMENT (labelled; hand-built worlds; not a Genesis run)",
        "started": started,
        "finished": datetime.now(UTC).isoformat(),
        "provider": provider.identity.to_dict(),
        "selection": selection,
        "settings": vars(args),
        "summary": summary,
        "probes": probe,
        "episodes": episodes,
    }
    out = HERE / "results" / f"{started[:10]}_{args.provider}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=1, default=str) + "\n")
    print(json.dumps(summary, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
