"""Birth and imperfect inheritance (state schema 11).

A birth is the engine's response to a parent's validated `reproduce` proposal; population
size never causes one. The child is a new, independent organism: fresh id, next generation,
parent link, its own hidden lifespan, no memories (memory inheritance is M13), no inbox,
and the parent's cognition policy. It first acts on the next tick.

Tendencies are inherited imperfectly. For each tendency, in TENDENCIES order, a mask draw
decides whether it is inherited (`inherit_probability_percent`); an inherited value moves by
a uniform -n..+n (`inherit_perturbation`) and is clipped to 0-100, otherwise a fresh value
0-100 is drawn. All draws come from a PRNG seeded by the world seed and the child id, so a
birth is reproducible without touching the world's random stream, and every draw is logged.
"""

import random

from genesis.domain.config import TENDENCIES, TENDENCY_MAX
from genesis.domain.events import BIRTH_RULE, ORGANISM_BORN, WorldEvent
from genesis.domain.state import Organism, World


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


def make_child(
    world: World, parent: Organism, x: int, y: int, endowment: int, material: int
) -> tuple[Organism, dict]:
    """The new organism record plus the observer log of its inheritance draws. Draw order:
    lifespan, then the tendencies (mask and delta or fresh value, per tendency)."""
    life = world.life
    new_id = child_id(world)
    rng = birth_rng(world, new_id)
    lifespan = rng.randint(life.lifespan_min, life.lifespan_max)
    tendencies, log = inherit(world, parent, rng)
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
    return child, log


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
