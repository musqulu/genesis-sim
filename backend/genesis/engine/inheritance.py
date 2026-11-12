"""Birth and imperfect inheritance (state schema 11).

A birth is the engine's response to a parent's validated `reproduce` proposal; population
size never causes one. The child is a new, independent organism: fresh id, next generation,
parent link, its own hidden lifespan, no inbox, and the parent's cognition policy. It first
acts on the next tick.

Memory (schema 12): the parent may select up to `inherit_memory_max` of its own active
records. Each selected record survives independently (`inherit_memory_retention_percent`);
a survivor becomes the child's own `inherited` record: the text, prefixed "[from <parent>]"
and truncated to the record limit, at the original place, with its origin (parent, source
record, kind and tick, first ancestor). It is a claim about the parent's past, never
refreshed; the parent's records are not changed. The child may receive none.

Tendencies are inherited imperfectly. For each tendency, in TENDENCIES order, a mask draw
decides whether it is inherited (`inherit_probability_percent`); an inherited value moves by
a uniform -n..+n (`inherit_perturbation`) and is clipped to 0-100, otherwise a fresh value
0-100 is drawn. All draws come from a PRNG seeded by the world seed and the child id, so a
birth is reproducible without touching the world's random stream, and every draw is logged.
"""

import random
from dataclasses import replace

from genesis.domain.config import TENDENCIES, TENDENCY_MAX
from genesis.domain.events import BIRTH_RULE, ORGANISM_BORN, MemoryChange, WorldEvent
from genesis.domain.state import MemoryRecord, Organism, World
from genesis.engine.memory import truncate


def child_id(world: World) -> str:
    numbers = [int(o.id.rsplit("-", 1)[1]) for o in world.organisms]
    return f"org-{max(numbers, default=0) + 1:04d}"


def birth_rng(world: World, new_id: str) -> random.Random:
    return random.Random(f"birth:{world.config.seed}:{new_id}")


def inherit(world: World, parent: Organism, rng: random.Random) -> tuple[tuple | None, dict]:
    """Draw the child's tendencies from the parent's; returns (values, log)."""
    life = world.life
    if parent.tendencies is None:
        return None, {"tendencies": None}
    mask, draws, values = [], [], []
    for name, inherited_from in zip(TENDENCIES, parent.tendencies, strict=True):
        roll = rng.randrange(100)
        keep = roll < life.inherit_probability_percent
        if keep:
            delta = rng.randint(-life.inherit_perturbation, life.inherit_perturbation)
            value = max(0, min(TENDENCY_MAX, inherited_from + delta))
            draws.append({"tendency": name, "mask_roll": roll, "delta": delta})
        else:
            value = rng.randint(0, TENDENCY_MAX)
            draws.append({"tendency": name, "mask_roll": roll, "fresh": value})
        mask.append(keep)
        values.append(value)
    log = {
        "mask": mask,
        "parent": list(parent.tendencies),
        "child": values,
        "draws": draws,
    }
    return tuple(values), log


def inherit_memories(
    world: World, parent: Organism, new_id: str, selected: tuple[str, ...], rng: random.Random
) -> tuple[list[MemoryRecord], dict]:
    """Copies of the selected parent records that survive their retention roll."""
    life = world.life
    owned = {m.id: m for m in world.memories_of(parent.id)}
    kept, rolls = [], []
    for memory_id in selected:
        roll = rng.randrange(100)
        survives = roll < life.inherit_memory_retention_percent
        rolls.append({"id": memory_id, "roll": roll, "retained": survives})
        if not survives:
            continue
        source = owned[memory_id]
        ancestor = source.origin["ancestor_id"] if source.kind == "inherited" else parent.id
        kept.append(
            MemoryRecord(
                id=f"{new_id}-m{len(kept) + 1:05d}",
                owner_id=new_id,
                kind="inherited",
                tick=world.tick,
                age=0,
                x=source.x,
                y=source.y,
                text=truncate(f"[from {parent.id}] {source.text}", life.memory_record_max_bytes),
                keywords=tuple(sorted({*source.keywords, "inherited"} - {source.kind})),
                salience=source.salience,
                origin={
                    "from": parent.id,
                    "source_id": source.id,
                    "source_kind": source.kind,
                    "source_tick": source.tick,
                    "ancestor_id": ancestor,
                },
            )
        )
    log = {
        "selected": list(selected),
        "retained": [r["id"] for r in rolls if r["retained"]],
        "dropped": [r["id"] for r in rolls if not r["retained"]],
        "rolls": rolls,
        "copies": [m.id for m in kept],
    }
    return kept, log


def make_child(
    world: World,
    parent: Organism,
    x: int,
    y: int,
    endowment: int,
    material: int,
    memory_ids: tuple[str, ...] = (),
) -> tuple[Organism, dict, list[MemoryChange]]:
    """The new organism record, the observer log of its inheritance draws and the memory
    deltas for its inherited records. Draw order: lifespan, the tendencies (mask and delta
    or fresh value, per tendency), then one retention roll per selected record."""
    life = world.life
    new_id = child_id(world)
    rng = birth_rng(world, new_id)
    lifespan = rng.randint(life.lifespan_min, life.lifespan_max)
    tendencies, log = inherit(world, parent, rng)
    records: list[MemoryRecord] = []
    if world.schema >= 12:
        records, log["memories"] = inherit_memories(world, parent, new_id, memory_ids, rng)
    child = Organism(
        id=new_id,
        label=parent.label,
        policy=parent.policy,
        birth_tick=world.tick,
        generation=parent.generation + 1,
        x=x,
        y=y,
        energy=endowment,
        lifespan=lifespan,
        parent_ids=(parent.id,),
        carried_material=material,
        tendencies=tendencies,
    )
    child = replace(child, memories_written=len(records))
    return child, log, [MemoryChange("add", r.to_dict()) for r in records]


def birth_event(world: World, accepted: WorldEvent) -> WorldEvent:
    """Observer record of a birth just committed by a parent's accepted proposal. It holds
    no deltas (the parent's event created the child); the lifespan is not included."""
    s = accepted.summary
    child = world.organism(s["child_id"])
    return WorldEvent(
        tick=world.tick,
        phase="lifecycle",
        type=ORGANISM_BORN,
        rule_id=BIRTH_RULE,
        cause="organism",
        actor_id=child.id,
        summary={
            "x": child.x,
            "y": child.y,
            "energy": child.energy,
            "carried_material": child.carried_material,
            "generation": child.generation,
            "parent_id": accepted.actor_id,
            "policy": child.policy,
            "inheritance": s.get("inheritance"),
        },
    )
