"""Costly active memory (state schema 7): world rules for writing, eviction and upkeep.

After each decision opportunity the engine writes, deterministically, what the organism
experienced: an `observation` of the cell it stood on, and an `outcome` record for an
accepted action (rejections stay diagnostics and never enter memory). Writing costs energy
per started KiB; keeping active text costs energy per started KiB on every upkeep tick.
When a write would exceed the record/byte limits, the oldest acquisitions are forgotten
first (stable id tie-break). Forgotten records leave active state only: the full record
stays in history (the `forget` delta carries it).

Records keep where they were acquired; cognition only ever receives a `MemoryView`, an
offset from the owner's current position. The organism moves only by its own accepted
moves, so that offset is what path integration would give it; no absolute coordinate,
tick or world size is revealed.
"""

from dataclasses import dataclass

from genesis.domain.events import (
    ACTION_ACCEPTED,
    MEMORY_SKIPPED,
    MEMORY_UPKEEP,
    MEMORY_UPKEEP_RULE,
    MEMORY_WRITE_RULE,
    MEMORY_WRITTEN,
    LedgerEntry,
    MemoryChange,
    OrganismChange,
    WorldEvent,
)
from genesis.domain.state import MemoryRecord, Organism, World
from genesis.engine.perception import Observation

# Fixed category salience (recall uses it as one score component).
SALIENCE = {"observation": 1, "outcome": 2, "message": 2}
MAX_SALIENCE = max(SALIENCE.values())


def enabled(world: World) -> bool:
    return world.schema >= 7 and world.life is not None and world.life.memory_enabled


def started_kib(size: int) -> int:
    return -(-size // 1024)


def truncate(text: str, limit: int) -> str:
    """Cut to at most `limit` UTF-8 bytes without splitting a character."""
    data = text.encode()
    if len(data) <= limit:
        return text
    return data[:limit].decode(errors="ignore")


@dataclass(frozen=True, slots=True)
class MemoryView:
    """What cognition may see of one of its own records."""

    id: str
    kind: str
    ticks_ago: int
    age: int  # owner's age when it was recorded
    dx: int  # from the owner's current position to where it was recorded
    dy: int
    text: str
    keywords: tuple[str, ...]
    salience: int
    size: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "ticks_ago": self.ticks_ago,
            "age_then": self.age,
            "dx": self.dx,
            "dy": self.dy,
            "text": self.text,
        }


def views(world: World, organism: Organism) -> tuple[MemoryView, ...]:
    """The organism's own active records, as offsets from where it is now."""
    return tuple(
        MemoryView(
            m.id,
            m.kind,
            world.tick - m.tick,
            m.age,
            m.x - organism.x,
            m.y - organism.y,
            m.text,
            m.keywords,
            m.salience,
            m.size,
        )
        for m in world.memories_of(organism.id)
    )


def status(world: World, organism_id: str) -> dict:
    active = world.memories_of(organism_id)
    life = world.life
    return {
        "active_records": len(active),
        "active_bytes": sum(m.size for m in active),
        "max_records": life.memory_max_records,
        "max_bytes": life.memory_max_bytes,
    }


def rules_text(world: World) -> str:
    life = world.life
    return (
        "After each decision your memory automatically stores what you observed on your "
        "cell and, if your action was accepted, its outcome. Writing costs "
        f"{life.memory_write_fee_per_kib} energy per started KiB; keeping memories costs "
        f"{life.memory_upkeep_per_kib} energy per started KiB every "
        f"{life.memory_upkeep_interval} ticks. At most {life.memory_max_records} records "
        f"({life.memory_max_bytes} bytes) are kept; the oldest are forgotten first. "
        "Recalled records give offsets (dx, dy) from where you are now; their values are "
        "as they were then and may have changed."
    )


# -- record content (fixed formats, no world state beyond what was observed) ---------------


def _observation_draft(observation: Observation) -> tuple[str, tuple[str, ...]]:
    here = next(c for c in observation.cells if (c.dx, c.dy) == (0, 0))
    text = (
        f"cell: energy {here.energy}, loose material {here.loose_material}, "
        f"packed material {here.packed_material}"
    )
    others = len(observation.organisms)
    if others:
        text += f"; {others} other organism(s) within view"
    keywords = ["observation"]
    keywords += ["energy"] * bool(here.energy)
    keywords += ["material"] * bool(here.loose_material)
    keywords += ["packed"] * bool(here.packed_material)
    keywords += ["organism"] * bool(others)
    return text, tuple(keywords)


def _outcome_draft(summary: dict) -> tuple[str, tuple[str, ...]]:
    action = summary.get("action")
    cost = summary.get("energy_cost", 0)
    if action == "move":
        direction = summary["direction"]
        text = f"moved {direction}; entry cost {cost} energy"
        return text, ("outcome", "move", direction)
    if action == "extract":
        resource = summary["resource"]
        text = f"extracted {summary['amount']} {resource} from this cell; fee {cost} energy"
        return text, ("outcome", "extract", resource)
    if action == "modify":
        mode = summary["mode"]
        text = (
            f"{mode}ed {summary['amount']} material on this cell; packed now "
            f"{summary['packed_after']}; cost {cost} energy"
        )
        return text, ("outcome", "modify", mode, "packed")
    if action == "transfer":
        gift = f"{summary['amount']} {summary['resource']}"
        text = f"gave {gift} to {summary['target']}; fee {cost} energy"
        return text, ("outcome", "transfer", summary["resource"], "organism")
    if action == "communicate":
        text = f'told {summary["target"]}: "{summary["text"]}"'
        return text, ("outcome", "communicate", "message", "organism")
    return "waited", ("outcome", "wait")


def _message_draft(item) -> tuple[str, tuple[str, ...]]:
    """A read inbox item. Words are the sender's claim, kept verbatim (and truncated)."""
    if item.kind == "transfer":
        resource = item.text.split()[-1]
        return f"{item.sender_id} gave me {item.text}", ("message", "organism", resource)
    return f'{item.sender_id} said: "{item.text}"', ("message", "organism")


def write_event(
    world: World,
    organism: Organism,
    observation: Observation,
    origin: tuple[int, int],
    outcome: WorldEvent,
    read: tuple = (),
) -> WorldEvent | None:
    """Record one decision's experience for a living organism, or a logged skip.

    An observation identical to the owner's latest active observation of the same cell is
    not written again. A write is affordable only if the organism keeps at least one
    energy after the fee: memory never kills by itself; an unaffordable write is skipped
    with a diagnostic and changes nothing.
    """
    life = world.life
    owned = world.memories_of(organism.id)
    drafts: list[tuple[str, str, tuple[str, ...]]] = []
    text, keywords = _observation_draft(observation)
    latest = next(
        (m for m in reversed(owned) if m.kind == "observation" and (m.x, m.y) == origin), None
    )
    if latest is None or latest.text != text:
        drafts.append(("observation", text, keywords))
    if outcome.type == ACTION_ACCEPTED:
        drafts.append(("outcome", *_outcome_draft(outcome.summary)))
    for item in read:  # inbox items shown at this decision (M09)
        drafts.append(("message", *_message_draft(item)))
    if not drafts:
        return None
    records = []
    for n, (kind, text, keywords) in enumerate(drafts, start=organism.memories_written + 1):
        records.append(
            MemoryRecord(
                id=f"{organism.id}-m{n:05d}",
                owner_id=organism.id,
                kind=kind,
                tick=world.tick,
                age=world.tick - organism.birth_tick,
                x=origin[0],
                y=origin[1],
                text=truncate(text, life.memory_record_max_bytes),
                keywords=keywords,
                salience=SALIENCE[kind],
            )
        )
    # One decision never stores more than memory can hold: later drafts (messages read)
    # beyond the limits are not stored; numbering stays contiguous.
    fitted, used = [], 0
    for r in records:
        if len(fitted) < life.memory_max_records and used + r.size <= life.memory_max_bytes:
            fitted.append(r)
            used += r.size
        else:
            break
    not_stored = len(records) - len(fitted)
    records = fitted
    written = sum(r.size for r in records)
    fee = started_kib(written) * life.memory_write_fee_per_kib
    summary = {
        **({"not_stored": not_stored} if not_stored else {}),
        "records": len(records),
        "bytes": written,
        "fee": fee,
        "kinds": [r.kind for r in records],
    }
    if fee and organism.energy <= fee:
        return WorldEvent(
            tick=world.tick,
            phase="diagnostic",
            type=MEMORY_SKIPPED,
            rule_id=MEMORY_WRITE_RULE,
            cause="lifecycle",
            actor_id=organism.id,
            summary={**summary, "reason": "insufficient_energy", "energy": organism.energy},
        )
    # Oldest acquisitions are forgotten first until the new records fit.
    kept = list(owned)
    evicted = []
    while kept and (
        len(kept) + len(records) > life.memory_max_records
        or sum(m.size for m in kept) + written > life.memory_max_bytes
    ):
        evicted.append(kept.pop(0))
    changes = []
    if fee:
        changes.append(
            OrganismChange(organism.id, "energy", organism.energy, organism.energy - fee)
        )
    changes.append(
        OrganismChange(
            organism.id,
            "memories_written",
            organism.memories_written,
            organism.memories_written + len(records),
        )
    )
    return WorldEvent(
        tick=world.tick,
        phase="memory",
        type=MEMORY_WRITTEN,
        rule_id=MEMORY_WRITE_RULE,
        cause="lifecycle",
        actor_id=organism.id,
        summary={
            **summary,
            "added": [r.id for r in records],
            "forgotten": [m.id for m in evicted],
            "active_records": len(kept) + len(records),
            "active_bytes": sum(m.size for m in kept) + written,
        },
        organism_changes=tuple(changes),
        memory_changes=tuple(
            [MemoryChange("forget", m.to_dict()) for m in evicted]
            + [MemoryChange("add", r.to_dict()) for r in records]
        ),
        ledger=(LedgerEntry("energy", "memory_write_fee", fee),) if fee else (),
    )


def upkeep_event(world: World) -> WorldEvent | None:
    """Every `memory_upkeep_interval` ticks each living organism pays for its active text,
    like basal upkeep: at most what it holds (a charge that empties it starves it)."""
    life = world.life
    if world.tick % life.memory_upkeep_interval:
        return None
    changes, charges, total = [], {}, 0
    for organism in world.living:
        size = sum(m.size for m in world.memories_of(organism.id))
        due = started_kib(size) * life.memory_upkeep_per_kib
        charged = min(due, organism.energy)
        if not size:
            continue
        charges[organism.id] = {"bytes": size, "due": due, "charged": charged}
        if charged:
            changes.append(
                OrganismChange(organism.id, "energy", organism.energy, organism.energy - charged)
            )
            total += charged
    if not charges:
        return None
    return WorldEvent(
        tick=world.tick,
        phase="lifecycle",
        type=MEMORY_UPKEEP,
        rule_id=MEMORY_UPKEEP_RULE,
        cause="lifecycle",
        summary={
            "per_kib": life.memory_upkeep_per_kib,
            "organisms": len(charges),
            "charged_total": total,
        },
        organism_changes=tuple(changes),
        ledger=(LedgerEntry("energy", "memory_upkeep", total),) if total else (),
        payload={"charges": charges},
    )
