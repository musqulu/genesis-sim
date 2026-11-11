"""Local perception: the only world-to-cognition boundary.

An observation is a plain immutable DTO built from the candidate state. It contains only
what lies within the organism's Manhattan radius (relative offsets, no absolute position or
world size), the organism's own age/energy, and general rules. It never contains the
sampled lifespan or deadline, the tick, seed, PRNG state, global ledger, population count or
anything outside the radius.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from genesis.domain.config import DIRECTIONS
from genesis.domain.state import Organism, World


@dataclass(frozen=True, slots=True)
class VisibleCell:
    dx: int
    dy: int
    energy: int
    loose_material: int
    packed_material: int
    occupied: bool  # by another living organism


@dataclass(frozen=True, slots=True)
class VisibleOrganism:
    id: str
    dx: int
    dy: int


@dataclass(frozen=True, slots=True)
class Observation:
    organism_id: str
    age: int
    energy: int
    energy_capacity: int
    carried_material: int
    material_capacity: int
    upkeep_per_tick: int
    perception_radius: int
    life_note: str
    actions: tuple[str, ...]
    cells: tuple[VisibleCell, ...]
    organisms: tuple[VisibleOrganism, ...]

    def to_dict(self) -> dict:
        return asdict(self)

    def canonical(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()


def observe(world: World, organism: Organism) -> Observation:
    life = world.life
    radius = life.perception_radius
    cells = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if abs(dx) + abs(dy) > radius:
                continue
            x, y = organism.x + dx, organism.y + dy
            if not (0 <= x < world.config.width and 0 <= y < world.config.height):
                continue  # beyond the edge: simply not perceived
            cell = world.cells[y * world.config.width + x]
            occupant = world.occupant(x, y)
            cells.append(
                VisibleCell(
                    dx,
                    dy,
                    cell.energy,
                    cell.loose_material,
                    cell.packed_material,
                    occupant is not None and occupant.id != organism.id,
                )
            )
    others = tuple(
        VisibleOrganism(o.id, o.x - organism.x, o.y - organism.y)
        for o in world.living
        if o.id != organism.id and abs(o.x - organism.x) + abs(o.y - organism.y) <= radius
    )
    return Observation(
        organism_id=organism.id,
        age=world.tick - organism.birth_tick,
        energy=organism.energy,
        energy_capacity=life.energy_capacity,
        carried_material=organism.carried_material,
        material_capacity=life.material_capacity if world.schema >= 5 else 0,
        upkeep_per_tick=life.upkeep,
        perception_radius=radius,
        life_note=(
            "Life is finite. Lifespans of this population typically fall between "
            f"{life.lifespan_min} and {life.lifespan_max} ticks; your own end is unknown."
        ),
        actions=_action_rules(world),
        cells=tuple(cells),
        organisms=others,
    )


def _action_rules(world: World) -> tuple[str, ...]:
    """General rule descriptions (identical for every organism in the run)."""
    life = world.life
    rules = [
        "wait",
        "move(direction in "
        + "/".join(DIRECTIONS)
        + f"): costs {life.move_base_cost} + floor(destination packed_material/2) energy",
    ]
    if world.schema >= 5:
        rules += [
            f"extract(resource energy|material, amount 1-{life.extract_max}) from your cell: "
            f"fee {life.extract_fee} energy paid first; no partial amounts",
            f"modify(mode pack|unpack, amount 1-{life.modify_max}) at your cell: "
            f"{life.modify_fee_per_unit} energy per unit; packed cells hold at most "
            f"{world.config.packed_cap} and cost more to enter",
        ]
    if world.schema >= 8 and life.exchange_enabled:
        rules += [
            f"transfer(target organism id, resource energy|material, amount 1-"
            f"{life.transfer_max}) to a living organism in an adjacent cell (one step "
            f"north/east/south/west): fee {life.transfer_fee} energy; it arrives at once",
            f"communicate(target organism id, text up to {life.message_max_bytes} bytes) to a "
            f"living organism in an adjacent cell: costs {life.message_fee_base} + "
            f"{life.message_fee_per_64_bytes} per started 64 bytes energy; delivered next tick; "
            f"its inbox holds at most {life.inbox_capacity} messages",
        ]
    return tuple(rules)
