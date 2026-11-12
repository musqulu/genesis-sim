"""Validation and resolution of proposed actions.

All checks happen before any mutation. A rejected proposal yields a diagnostic event with
no state changes; independent upkeep is charged elsewhere and is never a rejection penalty.
Check order: arguments -> per-action limit -> target -> fee affordability -> source ->
capacity. Requests are never silently clamped. Material moves between a cell and an
organism, or (schema 8 `transfer`) between two adjacent organisms; fees are dissipated
energy. A `communicate` proposal queues a message that is delivered on the next tick.
"""

from dataclasses import replace

from genesis.domain import actions as A
from genesis.domain.config import DIRECTIONS
from genesis.domain.events import (
    ACTION_ACCEPTED,
    ACTION_REJECTED,
    COMMUNICATE_RULE,
    EXTRACT_RULE,
    MODIFY_RULE,
    MOVE_RULE,
    PARSE_RULE,
    REPRODUCE_RULE,
    TRANSFER_RULE,
    VALIDATION_RULE,
    WAIT_RULE,
    CellChange,
    LedgerEntry,
    MessageChange,
    OrganismChange,
    WorldEvent,
)
from genesis.domain.state import Message, Organism, World
from genesis.engine import inheritance, memory


def move_cost(world: World, x: int, y: int) -> int:
    """Entry cost derives from the destination's packed material (effective geography)."""
    return world.life.move_base_cost + world.cells[y * world.config.width + x].packed_material // 2


def available_actions(world: World) -> tuple[str, ...]:
    if world.schema < 8:
        return A.ACTIONS_BY_SCHEMA.get(world.schema, ())
    life = world.life
    actions = list(A.ACTIONS_BY_SCHEMA[7])
    if life.exchange_enabled:
        actions += ["transfer", "communicate"]
    if world.schema >= 11 and life.reproduction_enabled:
        actions.append("reproduce")
    return tuple(actions)


def message_fee(world: World, size: int) -> int:
    life = world.life
    return life.message_fee_base + life.message_fee_per_64_bytes * -(-size // 64)


def neighbour(world: World, organism: Organism, target: object) -> Organism | None:
    """A living organism in a cell sharing an edge with the actor's, or None."""
    if not isinstance(target, str) or target == organism.id:
        return None
    other = next((o for o in world.organisms if o.id == target), None)
    if other is None or not other.alive:
        return None
    return other if abs(other.x - organism.x) + abs(other.y - organism.y) == 1 else None


def _reject(
    world: World, organism: Organism, proposal: A.Proposal, reason: str, payload
) -> WorldEvent:
    return WorldEvent(
        tick=world.tick,
        phase="diagnostic",
        type=ACTION_REJECTED,
        rule_id=VALIDATION_RULE,
        cause="organism",
        actor_id=organism.id,
        summary={"reason": reason, "action": str(proposal.action)[:32]},
        payload=payload,
    )


def unparsed_event(
    world: World, organism: Organism, outcome: A.Unparsed, payload: dict
) -> WorldEvent:
    """Output that never became a proposal. Diagnostic only; it consumes the opportunity."""
    return WorldEvent(
        tick=world.tick,
        phase="diagnostic",
        type=ACTION_REJECTED,
        rule_id=PARSE_RULE,
        cause="organism",
        actor_id=organism.id,
        summary={"reason": outcome.reason, "action": "unparsed"},
        payload=payload,
    )


def _accept(world, organism, rule, summary, changes, cell_changes, ledger, payload) -> WorldEvent:
    return WorldEvent(
        tick=world.tick,
        phase="action",
        type=ACTION_ACCEPTED,
        rule_id=rule,
        cause="organism",
        actor_id=organism.id,
        summary=summary,
        organism_changes=tuple(changes),
        changes=tuple(cell_changes),
        ledger=tuple(ledger),
        payload=payload,
    )


def _amount(proposal: A.Proposal) -> int | None:
    value = proposal.amount
    return value if type(value) is int and value > 0 else None


def _only(proposal: A.Proposal, *allowed: str) -> bool:
    """True if no argument outside `allowed` was supplied (unknown args are invalid)."""
    names = ("direction", "resource", "mode", "amount", "target", "text")
    return all(getattr(proposal, n) is None for n in names if n not in allowed)


def _energy_change(organism: Organism, after: int) -> list[OrganismChange]:
    return (
        []
        if after == organism.energy
        else [OrganismChange(organism.id, "energy", organism.energy, after)]
    )


def resolve(world: World, organism: Organism, proposal: A.Proposal, payload: dict) -> WorldEvent:
    life = world.life
    if not organism.alive:
        return _reject(world, organism, proposal, A.ACTOR_NOT_ALIVE, payload)
    if not isinstance(proposal.action, str) or proposal.action not in available_actions(world):
        return _reject(world, organism, proposal, A.UNKNOWN_ACTION, payload)
    action = proposal.action

    if action == "wait":
        if not _only(proposal):
            return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
        return _accept(
            world, organism, WAIT_RULE, {"action": "wait", "energy_cost": 0}, [], [], [], payload
        )

    if action == "move":
        if (
            not _only(proposal, "direction")
            or not isinstance(proposal.direction, str)
            or proposal.direction not in DIRECTIONS
        ):
            return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
        dx, dy = DIRECTIONS[proposal.direction]
        x, y = organism.x + dx, organism.y + dy
        if not (0 <= x < world.config.width and 0 <= y < world.config.height):
            return _reject(world, organism, proposal, A.OUT_OF_BOUNDS, payload)
        if world.occupant(x, y) is not None:
            return _reject(world, organism, proposal, A.OCCUPIED, payload)
        cost = move_cost(world, x, y)
        if organism.energy < cost:
            return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
        changes = [
            OrganismChange(organism.id, "x", organism.x, x),
            OrganismChange(organism.id, "y", organism.y, y),
            *_energy_change(organism, organism.energy - cost),
        ]
        summary = {
            "action": "move",
            "direction": proposal.direction,
            "from_x": organism.x,
            "from_y": organism.y,
            "to_x": x,
            "to_y": y,
            "entered_packed_material": world.cell_at(x, y).packed_material,
            "energy_cost": cost,
        }
        ledger = [LedgerEntry("energy", "movement", cost)] if cost else []
        return _accept(world, organism, MOVE_RULE, summary, changes, [], ledger, payload)

    if action in ("transfer", "communicate"):
        return _exchange(world, organism, proposal, payload)
    if action == "reproduce":
        return _reproduce(world, organism, proposal, payload)

    index = organism.y * world.config.width + organism.x
    cell = world.cells[index]
    amount = _amount(proposal)

    if action == "extract":
        if (
            not _only(proposal, "resource", "amount")
            or proposal.resource not in A.RESOURCES
            or amount is None
        ):
            return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
        if amount > life.extract_max:
            return _reject(world, organism, proposal, A.OVER_LIMIT, payload)
        fee = life.extract_fee
        if organism.energy < fee:  # paid from energy held before extracting
            return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
        if proposal.resource == "energy":
            if cell.energy < amount:
                return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
            if organism.energy - fee + amount > life.energy_capacity:
                return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
            cell_changes = [CellChange(cell.x, cell.y, "energy", cell.energy, cell.energy - amount)]
            changes = _energy_change(organism, organism.energy - fee + amount)
        else:
            if cell.loose_material < amount:
                return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
            if organism.carried_material + amount > life.material_capacity:
                return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
            cell_changes = [
                CellChange(
                    cell.x,
                    cell.y,
                    "loose_material",
                    cell.loose_material,
                    cell.loose_material - amount,
                )
            ]
            changes = [
                OrganismChange(
                    organism.id,
                    "carried_material",
                    organism.carried_material,
                    organism.carried_material + amount,
                ),
                *_energy_change(organism, organism.energy - fee),
            ]
        summary = {
            "action": "extract",
            "resource": proposal.resource,
            "amount": amount,
            "x": cell.x,
            "y": cell.y,
            "energy_cost": fee,
        }
        ledger = [LedgerEntry("energy", "extraction_fee", fee)] if fee else []
        return _accept(
            world, organism, EXTRACT_RULE, summary, changes, cell_changes, ledger, payload
        )

    # action == "modify"
    if (
        not _only(proposal, "mode", "amount")
        or proposal.mode not in A.MODIFY_MODES
        or amount is None
    ):
        return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
    if amount > life.modify_max:
        return _reject(world, organism, proposal, A.OVER_LIMIT, payload)
    fee = life.modify_fee_per_unit * amount
    if organism.energy < fee:
        return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
    if proposal.mode == "pack":
        if organism.carried_material < amount:
            return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
        if cell.packed_material + amount > world.config.packed_cap:
            return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
        carried_after = organism.carried_material - amount
        packed_after = cell.packed_material + amount
    else:
        if cell.packed_material < amount:
            return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
        if organism.carried_material + amount > life.material_capacity:
            return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
        carried_after = organism.carried_material + amount
        packed_after = cell.packed_material - amount
    cell_changes = [
        CellChange(cell.x, cell.y, "packed_material", cell.packed_material, packed_after)
    ]
    changes = [
        OrganismChange(organism.id, "carried_material", organism.carried_material, carried_after),
        *_energy_change(organism, organism.energy - fee),
    ]
    summary = {
        "action": "modify",
        "mode": proposal.mode,
        "amount": amount,
        "x": cell.x,
        "y": cell.y,
        "packed_before": cell.packed_material,
        "packed_after": packed_after,
        "entry_cost_after": life.move_base_cost + packed_after // 2,
        "energy_cost": fee,
    }
    ledger = [LedgerEntry("energy", "modification_fee", fee)] if fee else []
    return _accept(world, organism, MODIFY_RULE, summary, changes, cell_changes, ledger, payload)


def _receipt_or_message(world, organism, recipient, kind, text) -> tuple[list, MessageChange]:
    """A pending inbox item from `organism`, delivered on the next tick."""
    n = organism.messages_sent + 1
    message = Message(
        id=f"{organism.id}-s{n:05d}",
        kind=kind,
        sender_id=organism.id,
        recipient_id=recipient.id,
        text=text,
        sent_tick=world.tick,
        status="pending",
    )
    counter = OrganismChange(organism.id, "messages_sent", organism.messages_sent, n)
    return [counter], MessageChange("add", message.to_dict())


def _exchange(world: World, organism: Organism, proposal: A.Proposal, payload) -> WorldEvent:
    life = world.life
    if proposal.action == "transfer":
        amount = _amount(proposal)
        if (
            not _only(proposal, "target", "resource", "amount")
            or proposal.resource not in A.RESOURCES
            or amount is None
        ):
            return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
        if amount > life.transfer_max:
            return _reject(world, organism, proposal, A.OVER_LIMIT, payload)
        other = neighbour(world, organism, proposal.target)
        if other is None:
            return _reject(world, organism, proposal, A.NOT_ADJACENT, payload)
        fee = life.transfer_fee
        if proposal.resource == "energy":
            if organism.energy < amount + fee:
                return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
            if other.energy + amount > life.energy_capacity:
                return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
            changes = [
                *_energy_change(organism, organism.energy - amount - fee),
                OrganismChange(other.id, "energy", other.energy, other.energy + amount),
            ]
        else:
            if organism.energy < fee:
                return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
            if organism.carried_material < amount:
                return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
            if other.carried_material + amount > life.material_capacity:
                return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
            changes = [
                OrganismChange(
                    organism.id,
                    "carried_material",
                    organism.carried_material,
                    organism.carried_material - amount,
                ),
                OrganismChange(
                    other.id,
                    "carried_material",
                    other.carried_material,
                    other.carried_material + amount,
                ),
                *_energy_change(organism, organism.energy - fee),
            ]
        counter, receipt = _receipt_or_message(
            world, organism, other, "transfer", f"{amount} {proposal.resource}"
        )
        event = _accept(
            world,
            organism,
            TRANSFER_RULE,
            {
                "action": "transfer",
                "target": other.id,
                "resource": proposal.resource,
                "amount": amount,
                "energy_cost": fee,
                "from_x": organism.x,
                "from_y": organism.y,
                "to_x": other.x,
                "to_y": other.y,
                "receipt_id": receipt.record["id"],
            },
            changes + counter,
            [],
            [LedgerEntry("energy", "transfer_fee", fee)] if fee else [],
            payload,
        )
        return replace(event, message_changes=(receipt,))
    # communicate
    text = proposal.text
    if not _only(proposal, "target", "text") or not isinstance(text, str) or not text.strip():
        return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
    size = len(text.encode())
    if size > life.message_max_bytes:
        return _reject(world, organism, proposal, A.MESSAGE_TOO_LONG, payload)
    other = neighbour(world, organism, proposal.target)
    if other is None:
        return _reject(world, organism, proposal, A.NOT_ADJACENT, payload)
    fee = message_fee(world, size)
    if organism.energy < fee:
        return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
    queued = sum(1 for m in world.inbox_of(other.id) if m.kind == "message")
    if queued >= life.inbox_capacity:
        return _reject(world, organism, proposal, A.INBOX_FULL, payload)
    counter, message = _receipt_or_message(world, organism, other, "message", text)
    event = _accept(
        world,
        organism,
        COMMUNICATE_RULE,
        {
            "action": "communicate",
            "target": other.id,
            "bytes": size,
            "text": text,
            "energy_cost": fee,
            "from_x": organism.x,
            "from_y": organism.y,
            "to_x": other.x,
            "to_y": other.y,
            "message_id": message.record["id"],
        },
        _energy_change(organism, organism.energy - fee) + counter,
        [],
        [LedgerEntry("energy", "message_fee", fee)] if fee else [],
        payload,
    )
    return replace(event, message_changes=(message,))


def _reproduce(world: World, organism: Organism, proposal: A.Proposal, payload) -> WorldEvent:
    """Validate a birth (schema 11). Check order: arguments -> endowment bounds -> cooldown
    -> placement -> energy -> material. The accepted event creates the child atomically:
    parent debits, child record, fee dissipated, endowment conserved."""
    life = world.life
    endowment, material = proposal.endowment, proposal.material
    if material is None:
        material = 0
    selected = proposal.memory_ids
    if selected is None:
        selected = []
    if (
        not _only(proposal, "direction", "endowment", "material", "memory_ids")
        or not isinstance(selected, list | tuple)
        or not all(isinstance(m, str) for m in selected)
        or len(set(selected)) != len(selected)
        or (selected and world.schema < 12)
        or not isinstance(proposal.direction, str)
        or proposal.direction not in DIRECTIONS
        or type(endowment) is not int
        or type(material) is not int
        or material < 0
    ):
        return _reject(world, organism, proposal, A.INVALID_ARGUMENTS, payload)
    if endowment < life.reproduction_min_endowment:
        return _reject(world, organism, proposal, A.ENDOWMENT_TOO_SMALL, payload)
    if endowment > life.energy_capacity or material > life.material_capacity:
        return _reject(world, organism, proposal, A.OVER_CAPACITY, payload)
    owned = {m.id: m for m in world.memories_of(organism.id)}
    if len(selected) > life.inherit_memory_max:
        return _reject(world, organism, proposal, A.OVER_LIMIT, payload)
    if any(m not in owned for m in selected):  # unknown, another's, or already forgotten
        return _reject(world, organism, proposal, A.INVALID_MEMORY, payload)
    transmission = (
        memory.started_kib(sum(owned[m].size for m in selected)) * life.memory_write_fee_per_kib
        if selected
        else 0
    )
    last = organism.last_reproduced_tick
    if last is not None and world.tick - last < life.reproduction_cooldown:
        return _reject(world, organism, proposal, A.COOLDOWN, payload)
    dx, dy = DIRECTIONS[proposal.direction]
    x, y = organism.x + dx, organism.y + dy
    if not (0 <= x < world.config.width and 0 <= y < world.config.height):
        return _reject(world, organism, proposal, A.OUT_OF_BOUNDS, payload)
    if world.occupant(x, y) is not None:
        return _reject(world, organism, proposal, A.OCCUPIED, payload)
    fee = life.reproduction_fee
    if organism.energy < fee + transmission + endowment + life.reproduction_retain:
        return _reject(world, organism, proposal, A.INSUFFICIENT_ENERGY, payload)
    if organism.carried_material < material:
        return _reject(world, organism, proposal, A.INSUFFICIENT_SOURCE, payload)
    child, log, copies = inheritance.make_child(
        world, organism, x, y, endowment, material, tuple(selected)
    )
    changes = [
        OrganismChange(
            organism.id,
            "energy",
            organism.energy,
            organism.energy - fee - transmission - endowment,
        ),
        OrganismChange(
            organism.id, "last_reproduced_tick", organism.last_reproduced_tick, world.tick
        ),
    ]
    if material:
        changes.append(
            OrganismChange(
                organism.id,
                "carried_material",
                organism.carried_material,
                organism.carried_material - material,
            )
        )
    changes.append(OrganismChange(child.id, "record", None, child.to_dict(world.schema)))
    summary = {
        "action": "reproduce",
        "direction": proposal.direction,
        "child_id": child.id,
        "target": child.id,
        "endowment": endowment,
        "material": material,
        "energy_cost": fee + transmission,
        "transmission_fee": transmission,
        "from_x": organism.x,
        "from_y": organism.y,
        "to_x": x,
        "to_y": y,
        "generation": child.generation,
        "inheritance": log,
    }
    ledger = [LedgerEntry("energy", "reproduction_fee", fee)] if fee else []
    if transmission:
        ledger.append(LedgerEntry("energy", "inheritance_fee", transmission))
    event = _accept(world, organism, REPRODUCE_RULE, summary, changes, [], ledger, payload)
    return replace(event, memory_changes=tuple(copies))


PREVIEW_PROPOSALS = (
    A.Proposal("wait"),
    *(A.Proposal("move", direction=d) for d in DIRECTIONS),
    A.Proposal("extract", resource="energy", amount=1),
    A.Proposal("extract", resource="material", amount=1),
    A.Proposal("modify", mode="pack", amount=1),
    A.Proposal("modify", mode="unpack", amount=1),
)


def preview(world: World, organism: Organism) -> list[dict]:
    """Outcome of representative proposals against this exact state, using the same
    validator (no commit). The next tick applies inflow and upkeep before any action."""
    results = []
    for proposal in PREVIEW_PROPOSALS:
        if proposal.action not in available_actions(world):
            continue
        event = resolve(world, organism, proposal, {})
        results.append(
            {
                "proposal": proposal.to_dict(),
                "accepted": event.type == ACTION_ACCEPTED,
                "reason": event.summary.get("reason"),
                "energy_cost": sum(e.amount for e in event.ledger if e.resource == "energy"),
            }
        )
    return results
