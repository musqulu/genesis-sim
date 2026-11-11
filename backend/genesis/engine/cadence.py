"""Decision cadence (state schema 6): when an organism gets a decision opportunity.

These are world rules, not cognition: they use only the organism's own local view, are
deterministic, and their per-organism state is part of the state hash.
"""

from genesis.domain.events import (
    DECISION_ROUND,
    SCHEDULE_RULE,
    LedgerEntry,
    OrganismChange,
    WorldEvent,
)
from genesis.domain.state import Organism, World
from genesis.engine.perception import Observation


def view_total(observation: Observation) -> int:
    """What the organism can sense of local resources: energy + loose material in view."""
    return sum(c.energy + c.loose_material for c in observation.cells)


def due_reason(world: World, organism: Organism, observation: Observation) -> str | None:
    """Periodic every `decision_interval` ticks, or early on a significant local change.

    At most one decision per tick (the minimum gap is one tick). Returns None if not due.
    """
    life = world.life
    if organism.last_decision_tick is None:
        return "first"
    gap = world.tick - organism.last_decision_tick
    if gap >= life.decision_interval:
        return "periodic"
    if (
        world.schema >= 8
        and gap >= 1
        and any(
            m.status == "delivered" and m.delivered_tick == world.tick
            for m in world.inbox_of(organism.id)
        )
    ):
        return "message"  # something arrived for it this tick
    if (
        life.trigger_threshold
        and gap >= 1
        and organism.view_total_at_decision is not None
        and abs(view_total(observation) - organism.view_total_at_decision) >= life.trigger_threshold
    ):
        return "local_change"
    return None


def resolution_order(organism_ids: list[str], tick: int) -> list[str]:
    """Stable id order rotated by tick, so no organism always resolves first.

    Never depends on response arrival or completion speed.
    """
    ordered = sorted(organism_ids)
    if not ordered:
        return []
    k = tick % len(ordered)
    return ordered[k:] + ordered[:k]


def decision_round_event(world: World, due: list[tuple[Organism, str, Observation]]) -> WorldEvent:
    """Charge the cognition fee and record the schedule for every due organism."""
    fee = world.life.cognition_fee
    changes, total = [], 0
    for organism, _, observation in due:
        charged = min(fee, organism.energy)
        if charged:
            changes.append(
                OrganismChange(organism.id, "energy", organism.energy, organism.energy - charged)
            )
            total += charged
        changes.append(
            OrganismChange(
                organism.id, "last_decision_tick", organism.last_decision_tick, world.tick
            )
        )
        changes.append(
            OrganismChange(
                organism.id,
                "view_total_at_decision",
                organism.view_total_at_decision,
                view_total(observation),
            )
        )
    return WorldEvent(
        tick=world.tick,
        phase="cognition",
        type=DECISION_ROUND,
        rule_id=SCHEDULE_RULE,
        cause="lifecycle",
        summary={"due": len(due), "fee_per_decision": fee, "charged_total": total},
        organism_changes=tuple(changes),
        ledger=(LedgerEntry("energy", "cognition_fee", total),) if total else (),
        payload={"due": [{"organism_id": o.id, "reason": r} for o, r, _ in due]},
    )
