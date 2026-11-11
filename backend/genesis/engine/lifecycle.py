"""Aging, upkeep, death and extinction. Death never changes any cell."""

from genesis.domain.events import (
    DEATH_RULES,
    EXTINCTION,
    EXTINCTION_RULE,
    ORGANISM_DIED,
    UPKEEP,
    UPKEEP_RULE,
    LedgerEntry,
    OrganismChange,
    WorldEvent,
)
from genesis.domain.state import Organism, World


def death_event(world: World, organism: Organism, cause: str) -> WorldEvent:
    """Terminal: remaining carried energy is retired, not dropped into the cell."""
    tick = world.tick
    retired = organism.energy
    changes = [
        OrganismChange(organism.id, "alive", True, False),
        OrganismChange(organism.id, "death_tick", None, tick),
        OrganismChange(organism.id, "death_cause", None, cause),
    ]
    if retired:
        changes += [
            OrganismChange(organism.id, "energy", retired, 0),
            OrganismChange(organism.id, "retired_energy", organism.retired_energy, retired),
        ]
    material = organism.carried_material
    if material:  # carried material is retired too; death never drops it into the cell
        changes += [
            OrganismChange(organism.id, "carried_material", material, 0),
            OrganismChange(organism.id, "retired_material", organism.retired_material, material),
        ]
    return WorldEvent(
        tick=tick,
        phase="lifecycle",
        type=ORGANISM_DIED,
        rule_id=DEATH_RULES[cause],
        cause="lifecycle",
        actor_id=organism.id,
        summary={
            "cause": cause,
            "age": tick - organism.birth_tick,
            "retired_energy": retired,
            "retired_material": material,
            "x": organism.x,
            "y": organism.y,
        },
        organism_changes=tuple(changes),
        ledger=tuple(
            [LedgerEntry("energy", "death_retirement", retired)] * bool(retired)
            + [LedgerEntry("material", "retired", material)] * bool(material)
        ),
    )


def age_deaths(world: World) -> list[WorldEvent]:
    return [death_event(world, o, "age") for o in world.living if world.tick >= o.deadline]


def upkeep_event(world: World) -> WorldEvent | None:
    """Basal upkeep for every living organism; only the actual amount is dissipated."""
    upkeep = world.life.upkeep if world.life else 0
    changes, total = [], 0
    for organism in world.living:
        charged = min(upkeep, organism.energy)
        if charged:
            changes.append(
                OrganismChange(organism.id, "energy", organism.energy, organism.energy - charged)
            )
            total += charged
    if not world.living:
        return None
    return WorldEvent(
        tick=world.tick,
        phase="lifecycle",
        type=UPKEEP,
        rule_id=UPKEEP_RULE,
        cause="lifecycle",
        summary={
            "per_organism": upkeep,
            "organisms": len(world.living),
            "charged_total": total,
        },
        organism_changes=tuple(changes),
        ledger=(LedgerEntry("energy", "upkeep", total),) if total else (),
    )


def starvation_deaths(world: World) -> list[WorldEvent]:
    return [death_event(world, o, "starvation") for o in world.living if o.energy == 0]


def extinction_event(world: World) -> WorldEvent:
    return WorldEvent(
        tick=world.tick,
        phase="lifecycle",
        type=EXTINCTION,
        rule_id=EXTINCTION_RULE,
        cause="lifecycle",
        summary={"organisms_ever": len(world.organisms), "last_tick": world.tick},
    )
