"""Bounded reflection (state schema 10): world rules for when an organism may reflect and
how a reflection response is validated. Storage goes through the memory write rules.

A due decision opportunity becomes a reflection opportunity (never both in one tick) when:
memory and reflection are enabled; at least `reflection_cooldown` ticks have passed since
the last reflection opportunity; the organism holds at least `reflection_threshold` active
salient records (salience >= 2, reflections excluded) acquired since then; and it can pay
the cognition fee and the reflection fee while keeping one energy. The opportunity is
recorded when scheduled, so a rejected or empty reply still starts the cooldown.

A reply may contain zero to `reflection_max` interpretations; each must cite only ids of
records shown in that request (its own, non-reflection records). Anything else rejects the
whole reply with no state effect. Reflections never cite reflections (no recursive chains)
and never change or replace the records they cite.
"""

from genesis.domain import actions as A
from genesis.domain.events import (
    ACTION_ACCEPTED,
    ACTION_REJECTED,
    REFLECT_RULE,
    VALIDATION_RULE,
    LedgerEntry,
    OrganismChange,
    WorldEvent,
)
from genesis.domain.state import Organism, World
from genesis.engine import memory
from genesis.engine.memory import MemoryView


def enabled(world: World) -> bool:
    return world.schema >= 10 and world.life.reflection_enabled and memory.enabled(world)


def new_salient(world: World, organism: Organism) -> int:
    since = -1 if organism.reflected_at_tick is None else organism.reflected_at_tick
    return sum(
        1
        for m in world.memories_of(organism.id)
        if m.salience >= 2 and m.kind not in ("reflection", "inherited") and m.tick > since
    )


def eligible(world: World, organism: Organism) -> bool:
    if not enabled(world):
        return False
    life = world.life
    last = organism.reflected_at_tick
    if last is not None and world.tick - last < life.reflection_cooldown:
        return False
    if organism.energy <= life.cognition_fee + life.reflection_fee:
        return False
    return new_salient(world, organism) >= life.reflection_threshold


def shown(world: World, organism: Organism) -> tuple[MemoryView, ...]:
    """The records a reflection request shows: its newest non-reflection records."""
    views = [v for v in memory.views(world, organism) if v.kind != "reflection"]
    return tuple(reversed(views))[: world.life.reflection_context_max]


def _reject(world, organism, reason, payload) -> WorldEvent:
    return WorldEvent(
        tick=world.tick,
        phase="diagnostic",
        type=ACTION_REJECTED,
        rule_id=VALIDATION_RULE,
        cause="organism",
        actor_id=organism.id,
        summary={"reason": reason, "action": "reflect"},
        payload=payload,
    )


def resolve(
    world: World,
    organism: Organism,
    proposal: A.ReflectionProposal,
    shown_ids: frozenset[str],
    payload: dict,
) -> WorldEvent:
    """Validate a parsed reflection reply. Accepted: the fee only (records are stored by the
    memory write that follows); rejected: a diagnostic with no state change."""
    life = world.life
    if not organism.alive:
        return _reject(world, organism, A.ACTOR_NOT_ALIVE, payload)
    if len(proposal.reflections) > life.reflection_max:
        return _reject(world, organism, A.OVER_LIMIT, payload)
    for text, evidence in proposal.reflections:
        if not evidence or not set(evidence) <= shown_ids:
            return _reject(world, organism, A.INVALID_EVIDENCE, payload)
        if len(text.encode()) > life.memory_record_max_bytes:
            return _reject(world, organism, A.INVALID_ARGUMENTS, payload)
    fee = life.reflection_fee if proposal.reflections else 0
    if fee and organism.energy <= fee:
        return _reject(world, organism, A.INSUFFICIENT_ENERGY, payload)
    changes = []
    if fee:
        changes.append(
            OrganismChange(organism.id, "energy", organism.energy, organism.energy - fee)
        )
    return WorldEvent(
        tick=world.tick,
        phase="action",
        type=ACTION_ACCEPTED,
        rule_id=REFLECT_RULE,
        cause="organism",
        actor_id=organism.id,
        summary={
            "action": "reflect",
            "count": len(proposal.reflections),
            "energy_cost": fee,
            "reflections": [{"text": t, "evidence": list(e)} for t, e in proposal.reflections],
        },
        organism_changes=tuple(changes),
        ledger=(LedgerEntry("energy", "reflection_fee", fee),) if fee else (),
        payload=payload,
    )
