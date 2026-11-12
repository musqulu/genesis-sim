"""World construction and deterministic tick advancement.

No dependency on rendering, HTTP, persistence or wall-clock time. Cognition is injected
as a `decide(policy, observation) -> Proposal` callable that only ever sees observations.
"""

import json
import random
from collections.abc import Callable
from dataclasses import dataclass, replace

from genesis.domain.actions import Proposal, ReflectionProposal, Unparsed
from genesis.domain.config import FIXTURES, TENDENCIES, TENDENCY_MAX, LifeConfig, WorldConfig
from genesis.domain.events import (
    INIT_RULE,
    ORGANISM_BORN,
    RUN_CREATED,
    SEED_BIRTH_RULE,
    WorldEvent,
)
from genesis.domain.state import Cell, Ledger, MemoryRecord, Message, Organism, World
from genesis.engine import actions, cadence, inheritance, lifecycle, memory, messages, reflection
from genesis.engine.ledger import apply_entries, require_balanced
from genesis.engine.perception import Observation, observe
from genesis.engine.rules import RULES

Decide = Callable[[str, Observation], Proposal]


class ExtinctRun(RuntimeError):
    """The population is extinct; the run is archived and never advances again."""


def create_world(config: WorldConfig, life: LifeConfig | None = None) -> World:
    """Seeded initial state.

    Draw order is fixed: per cell energy then material; then one lifespan per seed
    organism, in seed order; then (schema 9, unless tendencies are off) six tendencies per
    seed organism, in seed order. Neutral mode and fixture-fixed values still draw, so the
    world and its random stream are identical across paired conditions. Everything drawn is
    stored, so replay never re-draws.
    """
    life = life or LifeConfig()
    rng = random.Random(config.seed)
    cells = []
    for y in range(config.height):
        for x in range(config.width):
            energy = rng.randrange(config.energy_cap + 1)
            has_material = rng.randrange(100) < config.material_density_percent
            material = rng.randrange(1, config.material_max + 1) if has_material else 0
            cells.append(Cell(x, y, energy=energy, loose_material=material))
    if config.fixture is not None:  # labeled test stockpiles replace the seeded amount
        for x, y, amount in FIXTURES[config.fixture].stockpiles:
            index = y * config.width + x
            cells[index] = replace(cells[index], loose_material=amount)
    organisms = tuple(
        Organism(
            id=f"org-{n:04d}",
            label="LIVE MODEL" if seed.policy == "live" else f"TEST POLICY: {seed.policy}",
            policy=seed.policy,
            birth_tick=0,
            generation=0,
            x=seed.x,
            y=seed.y,
            energy=life.start_energy,
            lifespan=rng.randint(life.lifespan_min, life.lifespan_max),
        )
        for n, seed in enumerate(life.seeds, start=1)
    )
    if life.tendency_mode != "off":
        with_tendencies = []
        for organism, seed in zip(organisms, life.seeds, strict=True):
            drawn = tuple(rng.randint(0, TENDENCY_MAX) for _ in TENDENCIES)
            if seed.tendencies is not None:  # a labeled fixture fixed them
                drawn = seed.tendencies
            elif life.tendency_mode == "neutral":
                drawn = (TENDENCY_MAX // 2,) * len(TENDENCIES)
            with_tendencies.append(replace(organism, tendencies=drawn))
        organisms = tuple(with_tendencies)
    world = World(
        config=config,
        cells=tuple(cells),
        ledger=Ledger(
            initial_energy=sum(c.energy for c in cells) + sum(o.energy for o in organisms),
            initial_material=sum(c.loose_material for c in cells),
        ),
        rng_state=rng_state_json(rng),
        schema=12,
        life=life,
        organisms=organisms,
    )
    require_balanced(world)
    return world


def rng_state_json(rng: random.Random) -> str:
    """Canonical, persistable PRNG state, so replay never depends on re-seeding."""
    version, internal, gauss = rng.getstate()
    return json.dumps([version, list(internal), gauss], separators=(",", ":"))


def creation_event(world: World) -> WorldEvent:
    return WorldEvent(
        tick=0,
        phase="initialization",
        type=RUN_CREATED,
        rule_id=INIT_RULE,
        cause="operator",
        summary={
            "seed": world.config.seed,
            "cells_total": len(world.cells),
            "initial_energy": world.ledger.initial_energy,
            "initial_material": world.ledger.initial_material,
            "seed_organisms": len(world.organisms),
        },
    )


def birth_events(world: World) -> list[WorldEvent]:
    """Seed births are part of the recorded initial state; these events document them."""
    return [
        WorldEvent(
            tick=0,
            phase="initialization",
            type=ORGANISM_BORN,
            rule_id=SEED_BIRTH_RULE,
            cause="operator",
            actor_id=o.id,
            summary={
                "x": o.x,
                "y": o.y,
                "energy": o.energy,
                "generation": o.generation,
                "policy": o.policy,
            },
        )
        for o in world.organisms
    ]


@dataclass(frozen=True, slots=True)
class TickResult:
    world: World
    events: tuple[WorldEvent, ...]


def _apply(world: World, event: WorldEvent) -> World:
    cells = list(world.cells)
    width = world.config.width
    for change in event.changes:
        index = change.y * width + change.x
        cell = cells[index]
        if getattr(cell, change.field) != change.before:
            raise ValueError("Event delta does not match the state it was computed from")
        cells[index] = replace(cell, **{change.field: change.after})
    # An event's organism deltas are applied to working records and validated once per
    # organism, so multi-field transitions (e.g. death) never pass through invalid states.
    records = {o.id: o.to_dict() for o in world.organisms}
    touched = set()
    for change in event.organism_changes:
        if change.field == "record":
            if change.organism_id in records or change.before is not None:
                raise ValueError("Organism creation does not match the recorded state")
            records[change.organism_id] = dict(change.after)
        else:
            current = records.get(change.organism_id)
            if (
                current is None
                or change.field not in current
                or current[change.field] != (change.before)
            ):
                raise ValueError("Organism delta does not match the state it was computed from")
            current[change.field] = change.after
        touched.add(change.organism_id)
    organisms = {o.id: o for o in world.organisms}
    for organism_id in touched:
        organisms[organism_id] = Organism.from_dict(records[organism_id])
    memories = world.memories
    if event.memory_changes:
        active = {m.id: m for m in memories}
        for change in event.memory_changes:
            record = MemoryRecord.from_dict(change.record)
            if change.op == "add" and record.id not in active:
                active[record.id] = record
            elif change.op == "forget" and active.get(record.id) == record:
                del active[record.id]
            else:
                raise ValueError("Memory delta does not match the state it was computed from")
        memories = tuple(active[k] for k in sorted(active))
    inbox = world.messages
    if event.message_changes:
        items = {m.id: m for m in inbox}
        for change in event.message_changes:
            message = Message.from_dict(change.record)
            if change.op == "add" and message.id not in items:
                items[message.id] = message
            elif change.op == "remove" and items.get(message.id) == message:
                del items[message.id]
            else:
                raise ValueError("Message delta does not match the state it was computed from")
        inbox = tuple(items[k] for k in sorted(items))
    return replace(
        world,
        cells=tuple(cells),
        organisms=tuple(organisms[k] for k in sorted(organisms)),
        ledger=apply_entries(world.ledger, event.ledger),
        memories=memories,
        messages=inbox,
    )


def apply_event(world: World, event: WorldEvent) -> World:
    """Apply a recorded event's exact deltas; used by the engine and by replay."""
    return _apply(world, event)


@dataclass(frozen=True, slots=True)
class DecisionSlot:
    """One decision opportunity: everything cognition may see, plus observer context."""

    organism_id: str
    policy: str
    due_reason: str
    observation: Observation
    origin: tuple[int, int]  # observer-only; never part of the observation
    # Schema 7: the organism's own active memory as offsets from here (None: no memory).
    memories: tuple[memory.MemoryView, ...] | None = None
    # Schema 8: delivered, unread inbox items (None: no exchange in this run).
    inbox: tuple[messages.InboxView, ...] | None = None
    # Schema 9: its own starting tendencies by name (None: no tendencies in this run).
    tendencies: dict[str, int] | None = None
    # Schema 10: "decide" (one action) or "reflect" (interpret its own records instead).
    # For a reflection, `memories` holds exactly the records shown and `inbox` is None.
    purpose: str = "decide"


@dataclass(frozen=True, slots=True)
class PreparedTick:
    """The tick up to the decision barrier. Nothing here is committed or published."""

    world: World  # candidate state after environment, lifecycle and cognition fees
    events: tuple[WorldEvent, ...]
    slots: tuple[DecisionSlot, ...]  # in resolution order


Outcome = Proposal | ReflectionProposal | Unparsed


def prepare_tick(world: World) -> PreparedTick:
    """Schema 6+, phase 1: environment -> age deaths -> upkeep -> memory upkeep (schema 7,
    on its interval) -> starvation -> due check -> cognition fee -> deaths from the fee ->
    one observation (and own memory view) per decision-maker, all taken from the same state
    before any action is applied."""
    if world.extinct:
        raise ExtinctRun("The population is extinct; this archived run cannot advance")
    if world.schema < 6:
        raise ValueError("prepare_tick requires state schema 6")
    tick = world.tick + 1
    candidate = replace(world, tick=tick)
    events: list[WorldEvent] = []

    def record(event: WorldEvent | None) -> None:
        nonlocal candidate
        if event is not None:
            candidate = _apply(candidate, event)
            events.append(event)

    for rule in RULES:
        record(rule(candidate, tick))
    slots: list[DecisionSlot] = []
    if candidate.living:
        for event in lifecycle.age_deaths(candidate):
            record(event)
        record(lifecycle.upkeep_event(candidate))
        if memory.enabled(candidate):
            record(memory.upkeep_event(candidate))
        for event in lifecycle.starvation_deaths(candidate):
            record(event)
        if messages.enabled(candidate):  # delivered before anyone observes
            record(messages.delivery_event(candidate))
        due = []
        for organism in candidate.living:
            reason = cadence.due_reason(candidate, organism, observe(candidate, organism))
            if reason is not None:
                due.append((organism, reason, observe(candidate, organism)))
        reflecting = frozenset(o.id for o, _, _ in due if reflection.eligible(candidate, o))
        if due:
            record(cadence.decision_round_event(candidate, due, reflecting))
            for event in lifecycle.starvation_deaths(candidate):  # the fee can be fatal
                record(event)
        reasons = {o.id: r for o, r, _ in due}
        deciding = [i for i in reasons if candidate.organism(i).alive]
        for organism_id in cadence.resolution_order(deciding, tick):
            organism = candidate.organism(organism_id)
            reflecting_now = organism_id in reflecting
            slots.append(
                DecisionSlot(
                    organism_id,
                    organism.policy,
                    reasons[organism_id],
                    observe(candidate, organism),
                    (organism.x, organism.y),
                    reflection.shown(candidate, organism)
                    if reflecting_now
                    else memory.views(candidate, organism)
                    if memory.enabled(candidate)
                    else None,
                    None
                    if reflecting_now or not messages.enabled(candidate)
                    else messages.views(candidate, organism_id),
                    organism.tendency_map(),
                    "reflect" if reflecting_now else "decide",
                )
            )
        if candidate.extinct:
            record(lifecycle.extinction_event(candidate))
    return PreparedTick(candidate, tuple(events), tuple(slots))


def finish_tick(prepared: PreparedTick, outcomes: dict[str, tuple[Outcome, dict]]) -> TickResult:
    """Schema 6+, phase 2: resolve one outcome per slot in the prepared (rotated) order.

    Each outcome is a parsed Proposal or an Unparsed record; both consume the opportunity.
    Unparsed and illegal proposals produce diagnostics with no state effect. Under schema 7
    a surviving organism's memory then records the decision's experience (paid, or skipped
    with a diagnostic when unaffordable).
    """
    if set(outcomes) != {s.organism_id for s in prepared.slots}:
        raise ValueError("Exactly one outcome per decision slot is required")
    candidate = prepared.world
    events = list(prepared.events)

    def record(event: WorldEvent | None) -> None:
        nonlocal candidate
        if event is not None:
            candidate = _apply(candidate, event)
            events.append(event)

    for slot in prepared.slots:
        outcome, payload = outcomes[slot.organism_id]
        organism = candidate.organism(slot.organism_id)
        if isinstance(outcome, Unparsed):
            resolved = actions.unparsed_event(candidate, organism, outcome, payload)
        elif slot.purpose == "reflect":
            if not isinstance(outcome, ReflectionProposal):
                raise ValueError("A reflection opportunity needs a reflection reply")
            shown_ids = frozenset(v.id for v in slot.memories or ())
            resolved = reflection.resolve(candidate, organism, outcome, shown_ids, payload)
        else:
            resolved = actions.resolve(candidate, organism, outcome, payload)
        record(resolved)
        if resolved.rule_id == "action.reproduce.v1":  # an observer record of the new life
            record(inheritance.birth_event(candidate, resolved))
        after = candidate.organism(slot.organism_id)
        if after.alive and after.energy == 0:
            record(lifecycle.death_event(candidate, after, "exhaustion"))
        elif after.alive:
            if slot.inbox:
                record(messages.read_event(candidate, slot.organism_id, slot.inbox))
            if memory.enabled(candidate):
                accepted_reflections = (
                    tuple(
                        (r["text"], tuple(r["evidence"]))
                        for r in resolved.summary.get("reflections", ())
                    )
                    if resolved.rule_id == "cognition.reflect.v1"
                    else ()
                )
                record(
                    memory.write_event(
                        candidate,
                        after,
                        slot.observation,
                        slot.origin,
                        resolved,
                        slot.inbox or (),
                        accepted_reflections,
                    )
                )
    if candidate.extinct and not any(e.type == "extinction" for e in events):
        record(lifecycle.extinction_event(candidate))
    require_balanced(candidate)
    return TickResult(world=candidate, events=tuple(events))


def advance(world: World, decide: Decide | None = None) -> TickResult:
    """Compute the next tick as a candidate; nothing is committed here.

    Schema <= 5 runs keep their milestone's every-tick rules. Schema 6 runs go through the
    decision barrier; `decide(policy, observation)` stands in for parsed cognition output.
    """
    if world.extinct:
        raise ExtinctRun("The population is extinct; this archived run cannot advance")
    if world.schema < 6:
        return _advance_legacy(world, decide)
    prepared = prepare_tick(world)
    if prepared.slots and decide is None:
        raise RuntimeError("Living organisms need a decision source")
    outcomes = {
        slot.organism_id: (decide(slot.policy, slot.observation), {}) for slot in prepared.slots
    }
    return finish_tick(prepared, outcomes)


def _advance_legacy(world: World, decide: Decide | None = None) -> TickResult:
    """State schema <= 5 (M04/M05 rules): every living organism decides every tick, each
    observing the state at its own turn. Kept unchanged so earlier runs keep their rules.

    Order: environment -> age deaths -> upkeep -> starvation deaths -> each living
    organism (stable id order) observes the same post-upkeep state as at its turn,
    proposes, and the engine validates/applies -> exhaustion death -> extinction check.
    """
    tick = world.tick + 1
    candidate = replace(world, tick=tick)
    events: list[WorldEvent] = []

    def record(event: WorldEvent | None) -> None:
        nonlocal candidate
        if event is not None:
            candidate = _apply(candidate, event)
            events.append(event)

    for rule in RULES:
        record(rule(candidate, tick))
    had_life = bool(candidate.living)
    if had_life:
        for event in lifecycle.age_deaths(candidate):
            record(event)
        record(lifecycle.upkeep_event(candidate))
        for event in lifecycle.starvation_deaths(candidate):
            record(event)
        for organism_id in [o.id for o in candidate.living]:
            organism = candidate.organism(organism_id)
            if not organism.alive:
                continue
            if decide is None:
                raise RuntimeError("Living organisms need a decision source")
            observation = observe(candidate, organism)
            proposal = decide(organism.policy, observation)
            payload = {
                "observation": observation.to_dict(),
                "observation_sha256": observation.digest(),
                # Observer-only context: where the observation was taken. Never sent to
                # the organism (its observation uses relative offsets only).
                "origin": {"x": organism.x, "y": organism.y},
                "proposal": proposal.to_dict(),
                "policy": organism.policy,
            }
            record(actions.resolve(candidate, organism, proposal, payload))
            after = candidate.organism(organism_id)
            if after.alive and after.energy == 0:
                record(lifecycle.death_event(candidate, after, "exhaustion"))
        if candidate.extinct:
            record(lifecycle.extinction_event(candidate))
    require_balanced(candidate)
    return TickResult(world=candidate, events=tuple(events))
